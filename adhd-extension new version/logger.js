// logger.js — Stage 5 (cleaned up)
// Observes user behaviour and sends timestamped events to background.js.
//
// Events logged:
//   mode_change         — Full / Para / Sent button clicks
//   settings_change     — font size / line height / theme
//   pause_start         — scroll stall > 5s (includes scroll %, para index)
//   pause_end           — scroll resumes (includes duration ms)
//   selfcheck_answer    — check-in card submitted on Study exit
//   reading_notes       — notes saved from the notes panel
//   session_end         — overlay closed

(function () {

  function send(event, data = {}) {
    try {
      chrome.runtime.sendMessage({ type: "LOG_EVENT", event, data })
        .catch(() => {});
    } catch (_) {}
  }

  const host   = document.getElementById("adhd-reader-root");
  const shadow = host?.shadowRoot;
  if (!host || !shadow) {
    console.warn("[ADHD Reader] logger.js: no host found.");
    return;
  }
  window.__readerLogExported = false;

  // ── Mode change logging ──────────────────────────────────────────────
  ["mode-full", "mode-para", "mode-sentence"].forEach(id => {
    const btn = shadow.getElementById(id);
    if (!btn) return;
    btn.addEventListener("click", () => {
      send("mode_change", { mode: id.replace("mode-", "") });
    });
  });

  // ── Settings change logging ──────────────────────────────────────────
  const fontSlider = shadow.getElementById("font-size-slider");
  const lineSlider = shadow.getElementById("line-height-slider");
  if (fontSlider) fontSlider.addEventListener("change", () => {
    send("settings_change", { setting: "fontSize", value: fontSlider.value });
  });
  if (lineSlider) lineSlider.addEventListener("change", () => {
    send("settings_change", { setting: "lineHeight", value: lineSlider.value });
  });
  ["theme-warm","theme-white","theme-green","theme-dark"].forEach(id => {
    const btn = shadow.getElementById(id);
    if (btn) btn.addEventListener("click", () => {
      send("settings_change", { setting: "theme", value: id.replace("theme-", "") });
    });
  });

  // ── Scroll + pause + progress tracking ──────────────────────────────
  // Works across all three modes:
  //   Full mode:  scroll events drive pause detection
  //   Para/Sent:  dwell time between button clicks drives pause detection
  const overlay = shadow.getElementById("reader-overlay");
  let pauseTimer    = null;
  let pauseStart    = null;
  let isPaused      = false;
  let scrollSamples = [];
  let currentMode   = "full"; // tracked so we know which logic to apply
  let dwellStart    = null;   // when the user landed on current para/sentence
  let dwellPosition = null;   // { paraIndex, sentenceIndex } for para/sent modes
  let demoMode      = false;  // Demo mode: fire highlight locally without robot
  let sentenceHighlightTimer = null;

  const sentenceHighlightStyle = document.createElement("style");
  sentenceHighlightStyle.textContent = `
    .current-sentence-highlight {
      background: rgba(250, 204, 21, 0.32) !important;
      box-shadow: inset 0 -0.22em 0 rgba(250, 204, 21, 0.45), 0 0 0 2px rgba(202, 138, 4, 0.18) !important;
      border-radius: 4px !important;
      transition: background 160ms ease, box-shadow 160ms ease !important;
    }
    .reading-mode-offer-backdrop {
      position: fixed;
      inset: 0;
      z-index: 2147483646;
      background: rgba(26, 26, 26, 0.24);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .reading-mode-offer {
      width: min(420px, 100%);
      background: var(--reader-bg, #fffaf2);
      color: var(--reader-fg, #1f2933);
      border: 1px solid rgba(0, 0, 0, 0.12);
      border-radius: 8px;
      box-shadow: 0 18px 54px rgba(0, 0, 0, 0.22);
      padding: 18px;
      font-family: inherit;
    }
    .reading-mode-offer h2 {
      margin: 0 0 8px;
      font-size: 18px;
      line-height: 1.25;
      font-weight: 650;
      letter-spacing: 0;
    }
    .reading-mode-offer p {
      margin: 0 0 16px;
      font-size: 14px;
      line-height: 1.55;
      color: inherit;
      opacity: 0.78;
    }
    .reading-mode-offer-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .reading-mode-offer-actions button {
      min-height: 36px;
      padding: 8px 14px;
      border: 1px solid #d0ccc4;
      border-radius: 6px;
      background: white;
      color: #333;
      cursor: pointer;
      font: inherit;
      font-size: 14px;
    }
    .reading-mode-offer-actions button.primary {
      background: #1a1a1a;
      border-color: #1a1a1a;
      color: white;
    }
  `;
  shadow.appendChild(sentenceHighlightStyle);

  // Track mode changes so pause logic knows which mode we're in
  ["mode-full", "mode-para", "mode-sentence"].forEach(id => {
    const btn = shadow.getElementById(id);
    if (!btn) return;
    btn.addEventListener("click", () => {
      const newMode = id.replace("mode-", "");
      if (newMode !== currentMode) {
        cancelPause();
        currentMode = newMode;
        if (newMode !== "full") startDwellTracking();
      }
    });
  });

  function cancelPause() {
    clearTimeout(pauseTimer);
    if (isPaused) {
      send("pause_end", { durationMs: Date.now() - pauseStart, ...currentPosition() });
      isPaused = false;
      pauseStart = null;
    }
  }

  function currentPosition() {
    if (currentMode === "full") {
      return { scrollProgress: getScrollProgress(), paragraphIndex: getVisibleParagraphIndex() };
    }
    return { paragraphIndex: dwellPosition?.paraIndex ?? null, sentenceIndex: dwellPosition?.sentenceIndex ?? null };
  }

  function startDwellTracking(position) {
    dwellStart    = Date.now();
    dwellPosition = position || readCurrentNavPosition();
    clearTimeout(pauseTimer);
    pauseTimer = setTimeout(() => {
      isPaused   = true;
      pauseStart = Date.now();
      send("pause_start", currentPosition());
      if (demoMode) triggerRedirectHighlight();
    }, 5000);
  }

  function readCurrentNavPosition() {
    // Read current position from modes.js state exposed on __readerState
    const state = window.__readerState;
    return {
      paraIndex:     state?.__currentParaIndex     ?? null,
      sentenceIndex: state?.__currentSentenceIndex ?? null,
    };
  }

  // Attach dwell tracking to nav buttons — use MutationObserver since
  // mode-ui is rebuilt on every Next/Back click
  const navObserver = new MutationObserver(() => {
    const prevBtn = shadow.getElementById("mode-prev");
    const nextBtn = shadow.getElementById("mode-next");
    [prevBtn, nextBtn].forEach(btn => {
      if (!btn || btn.__loggerAttached) return;
      btn.__loggerAttached = true;
      btn.addEventListener("click", () => {
        // End any active pause, log dwell time, start fresh tracking
        const dwellMs = dwellStart ? Date.now() - dwellStart : null;
        if (isPaused) {
          send("pause_end", { durationMs: Date.now() - pauseStart, ...currentPosition() });
          isPaused = false; pauseStart = null;
        }
        if (dwellMs !== null) {
          send("nav_advance", { dwellMs, ...currentPosition() });
        }
        clearTimeout(pauseTimer);
        // Small delay to let modes.js update state before we read it
        setTimeout(() => startDwellTracking(), 50);
      });
    });
  });

  const contentEl = shadow.getElementById("reader-content");
  if (contentEl) navObserver.observe(contentEl, { childList: true, subtree: true });

  function getScrollProgress() {
    if (!overlay) return 0;
    const max = overlay.scrollHeight - overlay.clientHeight;
    if (max <= 0) return 100;
    return Math.round((overlay.scrollTop / max) * 100);
  }

  function getVisibleParagraphIndex() {
    if (currentMode !== "full") {
      return window.__readerState?.__currentParaIndex ?? 0;
    }
    const paras = shadow.querySelectorAll(".article-paragraph");
    if (!paras.length) return 0;
    // Find the paragraph whose center is closest to 40% viewport height
    // (approximate eye position when reading naturally)
    const targetY = window.innerHeight * 0.4;
    let bestIdx = 0, bestDist = Infinity;
    for (let i = 0; i < paras.length; i++) {
      const rect = paras[i].getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) continue; // off screen
      const centerY = (rect.top + rect.bottom) / 2;
      const dist = Math.abs(centerY - targetY);
      if (dist < bestDist) { bestDist = dist; bestIdx = i; }
    }
    return bestIdx;
  }

  function estimateWpm() {
    if (scrollSamples.length < 2) return null;
    const first = scrollSamples[0];
    const last  = scrollSamples[scrollSamples.length - 1];
    const elapsedMin = (last.ts - first.ts) / 60000;
    if (elapsedMin < 0.1) return null;
    const paras = shadow.querySelectorAll(".article-paragraph");
    let totalWords = 0;
    paras.forEach(p => { totalWords += p.textContent.trim().split(/\s+/).length; });
    const scrollFraction = Math.min(1, (last.scrollTop - first.scrollTop) /
      Math.max(1, overlay.scrollHeight - overlay.clientHeight));
    return Math.round((totalWords * scrollFraction) / elapsedMin);
  }

  function getCurrentPhase() {
    return window.__readerState?.__currentPhase || null;
  }

  function getCurrentReadingTarget() {
    const modeText = shadow.getElementById("mode-text");
    if (currentMode === "sentence" && modeText) {
      return { kind: "element", element: modeText, text: modeText.textContent.trim() };
    }
    if (currentMode === "para" && modeText) {
      const focusedSentence = modeText.querySelector(".para-focus-sentence");
      return {
        kind: "element",
        element: focusedSentence || modeText,
        text: (focusedSentence || modeText).textContent.trim(),
      };
    }

    const paras = shadow.querySelectorAll(".article-paragraph");
    const paraIndex = window.__readerState?.__currentParaIndex ?? getVisibleParagraphIndex();
    const paragraphEl = paras[paraIndex] || paras[getVisibleParagraphIndex()];

    if (getCurrentPhase() === "thorough") {
      return { kind: "element", element: paragraphEl, text: paragraphEl?.textContent?.trim() || "" };
    }

    return { kind: "element", element: paragraphEl, text: paragraphEl?.textContent?.trim() || "" };
  }

  if (overlay) {
    overlay.addEventListener("scroll", () => {
      if (currentMode !== "full") return; // only Full mode uses scroll
      const now = Date.now();
      scrollSamples.push({ ts: now, scrollTop: overlay.scrollTop });
      if (scrollSamples.length > 100) scrollSamples.shift();

      if (isPaused) {
        const durationMs = now - pauseStart;
        send("pause_end", {
          durationMs,
          scrollProgress: getScrollProgress(),
          paragraphIndex: getVisibleParagraphIndex(),
        });
        isPaused   = false;
        pauseStart = null;
      }
      clearTimeout(pauseTimer);
      pauseTimer = setTimeout(() => {
        isPaused   = true;
        pauseStart = Date.now();
        send("pause_start", {
          scrollProgress: getScrollProgress(),
          paragraphIndex: getVisibleParagraphIndex(),
        });
        if (demoMode) triggerRedirectHighlight();
      }, 5000);
    }, { passive: true });
  }

  // ── Send reading state to Misty every 2s ──────────────────────────
  // Includes current paragraph/sentence text so Misty can feed it to an LLM
  function getCurrentText() {
    return getCurrentReadingTarget().text || "";
  }

  let lastSentText = "";
  const readingStateInterval = setInterval(() => {
    const currentText = getCurrentText();
    const state = {
      scrollProgress: getScrollProgress(),
      paragraphIndex: getVisibleParagraphIndex(),
      activeMode:     currentMode,
      currentPhase:   getCurrentPhase(),
      pauseDuration:       isPaused ? Date.now() - pauseStart : 0,
      covert_disengagemnt: isPaused,
      currentText,
      textChanged:    currentText !== lastSentText,
    };
    lastSentText = currentText;
    try {
      chrome.runtime.sendMessage({ type: "READING_STATE", data: state }).catch(() => {});
    } catch (_) {}
  }, 2000);

  // ── Receive messages from background (robot signals + bridge status) ────────
  chrome.runtime.onMessage.addListener((msg) => {

    if (msg.type === "BRIDGE_CONNECTED") {
      const s = shadow.getElementById("robot-status");
      if (s) {
        s.textContent = "● Robot";
        s.title = "Robot connected";
        s.style.background = "#d4ecc4";
        s.style.color = "#2e5e2e";
        s.style.borderColor = "#b8d4a0";
      }
    }

    if (msg.type === "BRIDGE_DISCONNECTED") {
      const s = shadow.getElementById("robot-status");
      if (s) {
        s.textContent = "○ Robot";
        s.title = "Robot not connected";
        s.style.background = "#f0ede8";
        s.style.color = "#aaa";
        s.style.borderColor = "#ddd";
      }
    }

    if (msg.type === "ROBOT_REDIRECT") {
      triggerRedirectHighlight();
    }

    if (msg.type === "HIGHLIGHT_CURRENT_SENTENCE") {
      const durationMs = Number(msg.durationMs || 10000);
      triggerCurrentSentenceHighlight(Number.isFinite(durationMs) ? durationMs : 10000);
    }

    if (msg.type === "OFFER_READING_MODE") {
      showReadingModeOffer(msg.currentMode || currentMode, msg.recommendedMode || "para", msg.source || "stage4_fatigue_support");
    }
  });

  function normalizeMode(mode) {
    return ["full", "para", "sentence"].includes(mode) ? mode : "full";
  }

  function modeLabel(mode) {
    if (mode === "para") return "paragraph";
    if (mode === "sentence") return "sentence";
    return "full";
  }

  function showReadingModeOffer(rawCurrentMode, rawRecommendedMode, source) {
    const offerCurrentMode = normalizeMode(rawCurrentMode);
    if (offerCurrentMode === "sentence") {
      send("fatigue_support_offer_skipped", { currentMode: offerCurrentMode, source });
      return;
    }

    const existing = shadow.getElementById("reading-mode-offer-backdrop");
    if (existing) existing.remove();

    const options = offerCurrentMode === "para" ? ["sentence"] : ["para", "sentence"];
    const recommendedMode = options.includes(rawRecommendedMode) ? rawRecommendedMode : options[0];
    const stayLabel = offerCurrentMode === "para" ? "Stay paragraph" : "Stay full";

    const backdrop = document.createElement("div");
    backdrop.id = "reading-mode-offer-backdrop";
    backdrop.className = "reading-mode-offer-backdrop";
    backdrop.innerHTML = `
      <section class="reading-mode-offer" role="dialog" aria-modal="true" aria-labelledby="reading-mode-offer-title">
        <h2 id="reading-mode-offer-title">Adjust reading mode?</h2>
        <p>Choose a smaller view for this section, or keep reading in ${modeLabel(offerCurrentMode)} mode.</p>
        <div class="reading-mode-offer-actions">
          ${options.map(mode => `<button class="${mode === recommendedMode ? 'primary' : ''}" data-mode="${mode}">Switch to ${modeLabel(mode)}</button>`).join("")}
          <button data-mode="stay">${stayLabel}</button>
        </div>
      </section>
    `;
    shadow.appendChild(backdrop);

    send("fatigue_support_offer_shown", {
      currentMode: offerCurrentMode,
      recommendedMode,
      options,
      source,
    });

    backdrop.querySelectorAll("button[data-mode]").forEach(btn => {
      btn.addEventListener("click", () => {
        const selectedMode = btn.getAttribute("data-mode");
        backdrop.remove();
        if (selectedMode === "stay") {
          send("fatigue_support_declined", { currentMode: offerCurrentMode, source });
          return;
        }
        if (window.__readerSetMode) window.__readerSetMode(selectedMode);
        currentMode = selectedMode;
        cancelPause();
        if (selectedMode !== "full") startDwellTracking();
        send("fatigue_support_mode_selected", {
          previousMode: offerCurrentMode,
          selectedMode,
          source,
        });
        send("mode_change", { mode: selectedMode, source: "fatigue_support" });
      });
    });
  }

  // ── Highlight the currently displayed sentence/text for Stage 2 ───────────
  function triggerCurrentSentenceHighlight(durationMs = 10000) {
    const target = getCurrentReadingTarget();
    if (!target?.element) return;

    clearTimeout(sentenceHighlightTimer);
    clearCurrentSentenceHighlight();

    const highlightedEl = target.element;
    if (!highlightedEl) return;

    highlightedEl.classList.add("current-sentence-highlight");
    const rect = highlightedEl.getBoundingClientRect();
    const alreadyVisible = rect.top >= 48 && rect.bottom <= window.innerHeight - 48;
    if (!alreadyVisible) {
      highlightedEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    sentenceHighlightTimer = setTimeout(() => {
      clearCurrentSentenceHighlight();
      sentenceHighlightTimer = null;
    }, Math.max(1, durationMs));

    send("current_sentence_highlight", {
      durationMs,
      paragraphIndex: window.__readerState?.__currentParaIndex ?? getVisibleParagraphIndex(),
      sentenceIndex: window.__readerState?.__currentSentenceIndex ?? null,
      activeMode: currentMode,
      currentPhase: getCurrentPhase(),
      currentText: target.text,
    });
  }

  function clearCurrentSentenceHighlight() {
    shadow.querySelectorAll(".current-sentence-highlight").forEach(el => {
      el.classList.remove("current-sentence-highlight");
    });
  }

  // ── Log redirect event (no visual highlight) ────────────────────────────────
  function triggerRedirectHighlight() {
    const idx = getVisibleParagraphIndex();
    send("redirection", { paragraphIndex: idx, scrollProgress: getScrollProgress() });
  }

  // ── Check bridge status on load (connection may already be established)
  setTimeout(() => {
    try {
      chrome.runtime.sendMessage({ type: "BRIDGE_STATUS" }, (response) => {
        if (response?.isConnected) {
          const s = shadow.getElementById("robot-status");
          if (s) {
            s.textContent = "● Robot";
            s.title = "Robot connected";
            s.style.background = "#d4ecc4";
            s.style.color = "#2e5e2e";
            s.style.borderColor = "#b8d4a0";
          }
        }
      });
    } catch (_) {}
  }, 500);

  // ── Session end ──────────────────────────────────────────────────────
  let sessionEnded = false;
  function cleanupSessionEnd() {
    if (sessionEnded) return;
    sessionEnded = true;
    send("session_end");
    clearTimeout(pauseTimer);
    clearInterval(readingStateInterval);
  }

  const closeBtn = shadow.getElementById("reader-close");
  if (closeBtn) {
    closeBtn.addEventListener("click", cleanupSessionEnd);
  }
  window.addEventListener("ADHD_READER_CLOSING", cleanupSessionEnd);

  // ── Export buttons (appended to settings panel) ──────────────────────
  const settingsPanel = shadow.getElementById("settings-panel");
  if (settingsPanel) {
    const exportRow = document.createElement("div");
    exportRow.className = "setting-row";
    exportRow.style.cssText = "border-top:1px solid #e0dbd3; padding-top:10px; margin-top:4px;";
    exportRow.innerHTML = `
      <button id="export-log-btn" style="
        padding:5px 14px; font-size:13px; border:1px solid #d0ccc4;
        border-radius:6px; background:white; cursor:pointer; color:#444; font-family:inherit;
      ">⬇ Export session log</button>
      <button id="export-notes-btn" style="
        padding:5px 14px; font-size:13px; border:1px solid #b8d4a0;
        border-radius:6px; background:#f4faf0; cursor:pointer; color:#5a7a3a; font-family:inherit;
      ">⬇ Export my notes</button>
      <button id="demo-mode-btn" style="
        padding:5px 14px; font-size:13px; border:1px solid #c4b5fd;
        border-radius:6px; background:#f5f3ff; cursor:pointer; color:#7c3aed; font-family:inherit;
      ">○ Demo mode</button>
      <span id="export-status" style="font-size:12px; color:#888;"></span>
    `;
    settingsPanel.appendChild(exportRow);

    // ── Demo mode toggle ───────────────────────────────────────
    shadow.getElementById("demo-mode-btn").addEventListener("click", () => {
      const btn = shadow.getElementById("demo-mode-btn");
      demoMode = !demoMode;
      btn.textContent    = demoMode ? "● Demo mode" : "○ Demo mode";
      btn.style.background  = demoMode ? "#ede9fe" : "#f5f3ff";
      btn.style.borderColor = demoMode ? "#7c3aed" : "#c4b5fd";
      btn.style.fontWeight  = demoMode ? "600" : "";
    });

    // ── Session log export ─────────────────────────────────────────────
    window.__readerExportLog = function exportReaderLog(statusEl, onDone) {
      const status = statusEl || shadow.getElementById("export-status");
      if (status) status.textContent = "Exporting...";
      chrome.runtime.sendMessage({ type: "EXPORT_LOG" }, response => {
        if (!response?.log) {
          if (status) status.textContent = "No log found.";
          if (onDone) onDone(false);
          return;
        }
        const log = response.log;
        const durationSec = Math.round((log.totalDuration || 0) / 1000);
        const avgPauseMs  = log.pauseCount > 0 ? Math.round(log.totalPauseMs / log.pauseCount) : 0;
        const pauses      = log.events.filter(e => e.type === "pause_start");

        log.summary = {
          totalDurationMin:    parseFloat((durationSec / 60).toFixed(1)),
          totalDurationSec:    durationSec,
          estimatedWpm:        estimateWpm(),
          finalScrollProgress: getScrollProgress(),
          pauseCount:          log.pauseCount,
          avgPauseDurationSec: Math.round(avgPauseMs / 1000),
          totalPauseSec:       Math.round(log.totalPauseMs / 1000),
          pauseLocations:      pauses.map(e => ({
            atSecond:       Math.round((e.ts - log.startTime) / 1000),
            scrollProgress: e.scrollProgress ?? null,
            paragraphIndex: e.paragraphIndex ?? null,
          })),
          modeChanges:      log.modeChanges,
          phaseChanges:     log.events.filter(e => e.type === "phase_change"),
          selfCheckAnswers: log.selfCheckAnswers,
          redirectionCount: log.redirections.length,
        };

        downloadFile(
          `adhd_session_${log.sessionId}.json`,
          JSON.stringify(log, null, 2),
          "application/json"
        );
        window.__readerLogExported = true;
        if (status) {
          status.textContent = `Saved: adhd_session_${log.sessionId}.json`;
          setTimeout(() => { status.textContent = ""; }, 4000);
        }
        if (onDone) onDone(true);
      });
    };

    shadow.getElementById("export-log-btn").addEventListener("click", () => {
      window.__readerExportLog(shadow.getElementById("export-status"));
    });

    // ── Notes export ───────────────────────────────────────────────────
    shadow.getElementById("export-notes-btn").addEventListener("click", () => {
      const status = shadow.getElementById("export-status");
      const phaseNotes = window.__readerPhases?.getSavedNotes?.() || [];
      if (phaseNotes.length > 0) {
        const saved = window.__readerPhases?.downloadNotes?.();
        status.textContent = saved === false ? "No notes recorded yet." : `Saved ${phaseNotes.length} note${phaseNotes.length > 1 ? "s" : ""}.`;
        setTimeout(() => { status.textContent = ""; }, 4000);
        return;
      }

      chrome.runtime.sendMessage({ type: "EXPORT_LOG" }, response => {
        if (!response?.log) { status.textContent = "No log found."; return; }
        const log     = response.log;
        // selfCheckAnswers entries now have: { question, thumb, notes, wroteNote }
        const answers = log.selfCheckAnswers || [];

        if (answers.length === 0) {
          status.textContent = "No notes recorded yet.";
          setTimeout(() => { status.textContent = ""; }, 3000);
          return;
        }

        const date     = new Date(log.startTime).toLocaleString();
        const duration = log.totalDuration
          ? `${(log.totalDuration / 60000).toFixed(1)} min` : "unknown";

        let text  = `READING NOTES\n`;
        text     += `${"=".repeat(40)}\n`;
        text     += `Article: ${log.title || "Untitled"}\n`;
        text     += `Date:     ${date}\n`;
        text     += `Duration: ${duration}\n`;
        text     += `${"=".repeat(40)}\n\n`;

        answers.forEach((item, i) => {
          const thumb = item.thumb === "up"   ? "👍 Clear"
                      : item.thumb === "down" ? "👎 Unclear"
                      : "not rated";
          text += `Check-in ${i + 1}\n`;
          text += `Q: ${item.question || ""}\n`;
          text += `Clarity: ${thumb}\n`;
          text += `Notes: ${item.notes || "(none written)"}\n\n`;
        });

        const wrote     = answers.filter(a => a.wroteNote).length;
        const thumbUp   = answers.filter(a => a.thumb === "up").length;
        const thumbDown = answers.filter(a => a.thumb === "down").length;
        text += `${"=".repeat(40)}\n`;
        text += `Summary: ${answers.length} check-ins | ${wrote} with notes | ${thumbUp} clear, ${thumbDown} unclear\n`;

        downloadFile(`adhd_notes_${log.sessionId}.txt`, text, "text/plain");
        status.textContent = `Saved: adhd_notes_${log.sessionId}.txt`;
        setTimeout(() => { status.textContent = ""; }, 4000);
      });
    });
  }

  // ── Helper ───────────────────────────────────────────────────────────
  function downloadFile(filename, content, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  console.log("[ADHD Reader] logger.js ready.");

})();
