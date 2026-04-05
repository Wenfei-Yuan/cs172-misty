// logger.js — Stage 5 (cleaned up)
// Observes user behaviour and sends timestamped events to background.js.
//
// Events logged:
//   mode_change         — Full / Para / Sent / Study button clicks
//   phase_change        — skim ↔ thorough transitions via pill button
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

  // ── Mode change logging ──────────────────────────────────────────────
  ["mode-full", "mode-para", "mode-sentence", "mode-study"].forEach(id => {
    const btn = shadow.getElementById(id);
    if (!btn) return;
    btn.addEventListener("click", () => {
      send("mode_change", { mode: id.replace("mode-", "") });
    });
  });

  // ── Phase change logging (skim pill button) ──────────────────────────
  // Uses MutationObserver so it picks up the pill even if phases.js
  // inserts it after logger.js runs.
  function attachPhasePillLogger() {
    const pillBtn = shadow.getElementById("phase-pill-btn");
    if (!pillBtn) return false;
    pillBtn.addEventListener("click", () => {
      setTimeout(() => {
        const label = shadow.getElementById("phase-pill-label")?.textContent;
        send("phase_change", { phase: label });
      }, 10);
    });
    return true;
  }

  if (!attachPhasePillLogger()) {
    // Pill not yet in DOM — wait for it
    const pillObserver = new MutationObserver(() => {
      if (attachPhasePillLogger()) pillObserver.disconnect();
    });
    pillObserver.observe(shadow, { childList: true, subtree: true });
  }

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
  let redirectHighlightTimer  = null;
  let redirectHighlightTarget = null;
  let redirectHighlightPrev   = "";
  let scrollSamples = [];
  let currentMode   = "full"; // tracked so we know which logic to apply
  let dwellStart    = null;   // when the user landed on current para/sentence
  let dwellPosition = null;   // { paraIndex, sentenceIndex } for para/sent modes

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
    const paras = shadow.querySelectorAll(".article-paragraph");
    for (let i = 0; i < paras.length; i++) {
      const rect = paras[i].getBoundingClientRect();
      if (rect.top >= 0 && rect.top < window.innerHeight * 0.6) return i;
    }
    return 0;
  }

  function getRedirectHighlightTarget() {
    if (currentMode === "para" || currentMode === "sentence") {
      return shadow.getElementById("mode-text");
    }

    const paras = shadow.querySelectorAll(".article-paragraph");
    const idx = getVisibleParagraphIndex();
    return paras[idx] || null;
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
      }, 5000);
    }, { passive: true });
  }

  // ── Send reading state to Misty every 2s ──────────────────────────
  // Includes current paragraph/sentence text so Misty can feed it to an LLM
  function getCurrentText() {
    if (currentMode === "para" || currentMode === "sentence") {
      // In para/sent mode, the visible text is in #mode-text
      return shadow.getElementById("mode-text")?.textContent?.trim() || "";
    }
    // In full mode, get the paragraph closest to the top of the view
    const paras = shadow.querySelectorAll(".article-paragraph");
    const idx   = getVisibleParagraphIndex();
    return paras[idx]?.textContent?.trim() || "";
  }

  let lastSentText = "";
  const readingStateInterval = setInterval(() => {
    const currentText = getCurrentText();
    const state = {
      scrollProgress: getScrollProgress(),
      paragraphIndex: getVisibleParagraphIndex(),
      activeMode:     currentMode,
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
      const idx    = getVisibleParagraphIndex();
      const target = getRedirectHighlightTarget();
      if (target) {
        const prev = target.getAttribute("style") || "";
          target.style.outline       = "3px solid #7c3aed";
        target.style.outlineOffset = "4px";
        target.style.transition    = "outline 0.2s";
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        redirectHighlightTarget = target;
        redirectHighlightPrev   = prev;
        clearTimeout(redirectHighlightTimer);
        redirectHighlightTimer = setTimeout(() => {
          target.setAttribute("style", prev);
          redirectHighlightTarget = null;
        }, 2000);
      }
      send("redirection", { paragraphIndex: idx, scrollProgress: getScrollProgress() });
    }

    if (msg.type === "ROBOT_RESUME") {
      if (redirectHighlightTarget) {
        clearTimeout(redirectHighlightTimer);
        redirectHighlightTarget.setAttribute("style", redirectHighlightPrev);
        redirectHighlightTarget = null;
      }
    }
  });

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
  const closeBtn = shadow.getElementById("reader-close");
  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      send("session_end");
      clearTimeout(pauseTimer);
      clearInterval(readingStateInterval);
    });
  }

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
      <span id="export-status" style="font-size:12px; color:#888;"></span>
    `;
    settingsPanel.appendChild(exportRow);

    // ── Session log export ─────────────────────────────────────────────
    shadow.getElementById("export-log-btn").addEventListener("click", () => {
      const status = shadow.getElementById("export-status");
      status.textContent = "Exporting...";
      chrome.runtime.sendMessage({ type: "EXPORT_LOG" }, response => {
        if (!response?.log) { status.textContent = "No log found."; return; }
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
        status.textContent = `Saved: adhd_session_${log.sessionId}.json`;
        setTimeout(() => { status.textContent = ""; }, 4000);
      });
    });

    // ── Notes export ───────────────────────────────────────────────────
    shadow.getElementById("export-notes-btn").addEventListener("click", () => {
      const status = shadow.getElementById("export-status");
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
