// modes.js — Stage 3
// Paragraph-by-paragraph and sentence-by-sentence reading modes.
// Called by reader.js after the article is rendered.
//
// Exports two functions to window.__readerModes:
//   initModes(shadow, paragraphs, content) — sets up both modes
//   setMode(mode) — switches between "full", "para", "sentence"

(function () {

  // ── Sentence splitter ───────────────────────────────────────────────────
  // Splits a paragraph string into individual sentences.
  // Handles common abbreviations to avoid false splits.
  function splitSentences(text) {
    // Protect common abbreviations
    const protected_ = text
      .replace(/\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|vs|etc|approx|Fig|St|Dept|Vol|No)\./g, "$1PROTECTEDDOT");

    const raw = protected_.split(/(?<=[.!?])\s+(?=[A-Z"'])/);

    return raw
      .map(s => s.replace(/PROTECTEDDOT/g, ".").trim())
      .filter(s => s.length > 0);
  }

  // ── State ───────────────────────────────────────────────────────────────
  let shadow, paragraphs, contentEl;
  let paraIndex = 0;
  let sentParagraphIndex = 0;
  let sentSentenceIndex = 0;
  let sentences = [];  // sentences for current paragraph in sentence mode
  let currentMode = "full";

  // ── Init ────────────────────────────────────────────────────────────────
  function initModes(_shadow, _paragraphs, _contentEl) {
    shadow      = _shadow;
    paragraphs  = _paragraphs;
    contentEl   = _contentEl;
  }

  // ── Mode entry points ───────────────────────────────────────────────────
  function setMode(mode) {
    currentMode = mode;
    const parasContainer = shadow.getElementById("paragraphs-container");

    // Remove any mode UI from previous mode
    removeModeUI();

    if (mode === "full") {
      parasContainer.style.display = "";
      Array.from(parasContainer.children).forEach(p => {
        p.style.opacity = "1";
        p.style.display = "";
      });

    } else if (mode === "para") {
      parasContainer.style.display = "none";
      renderParaMode();

    } else if (mode === "sentence") {
      parasContainer.style.display = "none";
      renderSentenceMode();
    }
  }

  // ── Remove mode UI ───────────────────────────────────────────────────────
  function removeModeUI() {
    const existing = shadow.getElementById("mode-ui");
    if (existing) existing.remove();
  }

  // ── Paragraph mode ───────────────────────────────────────────────────────
  function renderParaMode() {
    const total = paragraphs.length;
    if (paraIndex >= total) paraIndex = total - 1;
    if (paraIndex < 0) paraIndex = 0;

    // Always remove and recreate — same pattern as sentence mode
    removeModeUI();

    const progress = Math.round((paraIndex / total) * 100);

    const ui = document.createElement("div");
    ui.id = "mode-ui";
    ui.style.cssText = "padding: 0 32px;";

    ui.innerHTML = `
      <div style="margin-bottom: 16px;">
        <div style="
          font-size: 12px;
          color: #999;
          margin-bottom: 6px;
          letter-spacing: 0.04em;
        ">Paragraph ${paraIndex + 1} of ${total}</div>
        <div style="
          height: 3px;
          background: #e8e4dc;
          border-radius: 2px;
          overflow: hidden;
        ">
          <div style="
            height: 100%;
            width: ${progress}%;
            background: #c4a882;
            border-radius: 2px;
            transition: width 0.2s;
          "></div>
        </div>
      </div>

      <div id="mode-text" style="margin-bottom: 32px;">
        ${escHtml(paragraphs[paraIndex])}
      </div>

      <div style="display:flex; gap:10px; align-items:center;">
        <button id="mode-prev" class="nav-btn" ${paraIndex === 0 ? "disabled" : ""}>← Back</button>
        <button id="mode-next" class="nav-btn primary" ${paraIndex >= total - 1 ? "disabled" : ""}>Next →</button>
        <span id="mode-done" style="display:${paraIndex >= total - 1 ? 'inline' : 'none'}; color:#888; font-size:13px; font-style:italic;">
          End of article
        </span>
      </div>
    `;

    injectNavStyles();
    contentEl.appendChild(ui);

    shadow.getElementById("mode-prev").addEventListener("click", () => {
      if (paraIndex > 0) { paraIndex--; renderParaMode(); }
    });
    shadow.getElementById("mode-next").addEventListener("click", () => {
      if (paraIndex < total - 1) { paraIndex++; renderParaMode(); }
    });

    // Expose position for logger.js
    if (window.__readerState) {
      window.__readerState.__currentParaIndex     = paraIndex;
      window.__readerState.__currentSentenceIndex = null;
    }

    const overlay = shadow.getElementById("reader-overlay");
    if (overlay) overlay.scrollTop = 0;
  }

  // ── Sentence mode ────────────────────────────────────────────────────────
  function renderSentenceMode() {
    // Rebuild sentences if we've changed paragraphs
    sentences = splitSentences(paragraphs[sentParagraphIndex] || "");
    if (sentSentenceIndex >= sentences.length) sentSentenceIndex = 0;

    const totalParas = paragraphs.length;
    const totalSents = sentences.length;
    const progress   = Math.round(
      ((sentParagraphIndex * 100) / totalParas) +
      ((sentSentenceIndex / totalSents) * (100 / totalParas))
    );

    const ui = document.createElement("div");
    ui.id = "mode-ui";
    ui.style.cssText = "padding: 0 32px;";

    ui.innerHTML = `
      <div style="margin-bottom: 16px;">
        <div style="
          font-size: 12px;
          color: #999;
          margin-bottom: 6px;
          letter-spacing: 0.04em;
        ">Para ${sentParagraphIndex + 1}/${totalParas} · Sentence ${sentSentenceIndex + 1}/${totalSents}</div>
        <div style="
          height: 3px;
          background: #e8e4dc;
          border-radius: 2px;
          overflow: hidden;
        ">
          <div style="
            height: 100%;
            width: ${progress}%;
            background: #c4a882;
            border-radius: 2px;
            transition: width 0.2s;
          "></div>
        </div>
      </div>

      <div id="mode-text" style="
        margin-bottom: 32px;
        transition: opacity 0.15s;
      ">${escHtml(sentences[sentSentenceIndex] || "")}</div>

      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <button id="mode-prev" class="nav-btn"
          ${sentParagraphIndex === 0 && sentSentenceIndex === 0 ? "disabled" : ""}>
          ← Back
        </button>
        <button id="mode-next" class="nav-btn primary"
          ${sentParagraphIndex === totalParas - 1 && sentSentenceIndex === totalSents - 1 ? "disabled" : ""}>
          Next →
        </button>
        <span id="mode-done" style="display:none; color:#888; font-size:13px; font-style:italic;">
          End of article
        </span>
      </div>
    `;

    injectNavStyles();
    contentEl.appendChild(ui);

    // Expose position for logger.js
    if (window.__readerState) {
      window.__readerState.__currentParaIndex     = sentParagraphIndex;
      window.__readerState.__currentSentenceIndex = sentSentenceIndex;
    }

    // Next: advance sentence, then paragraph
    shadow.getElementById("mode-next").addEventListener("click", () => {
      if (sentSentenceIndex < sentences.length - 1) {
        sentSentenceIndex++;
      } else if (sentParagraphIndex < totalParas - 1) {
        sentParagraphIndex++;
        sentSentenceIndex = 0;
      } else {
        shadow.getElementById("mode-next").style.display = "none";
        shadow.getElementById("mode-done").style.display = "inline";
        return;
      }
      removeModeUI();
      renderSentenceMode();
    });

    // Back: go to previous sentence, then previous paragraph's last sentence
    shadow.getElementById("mode-prev").addEventListener("click", () => {
      if (sentSentenceIndex > 0) {
        sentSentenceIndex--;
      } else if (sentParagraphIndex > 0) {
        sentParagraphIndex--;
        const prevSents = splitSentences(paragraphs[sentParagraphIndex]);
        sentSentenceIndex = prevSents.length - 1;
      }
      removeModeUI();
      renderSentenceMode();
    });

    const overlay = shadow.getElementById("reader-overlay");
    if (overlay) overlay.scrollTop = 0;
  }

  // ── Nav button styles (injected once) ───────────────────────────────────
  let navStylesInjected = false;
  function injectNavStyles() {
    if (navStylesInjected) return;
    navStylesInjected = true;
    const s = document.createElement("style");
    s.textContent = `
      .nav-btn {
        padding: 8px 20px;
        font-size: 14px;
        border: 1px solid #d0ccc4;
        border-radius: 6px;
        background: white;
        cursor: pointer;
        color: #444;
        font-family: inherit;
      }
      .nav-btn:hover:not(:disabled) { background: #f0ede8; }
      .nav-btn:disabled { opacity: 0.35; cursor: default; }
      .nav-btn.primary {
        background: #1a1a1a;
        color: white;
        border-color: #1a1a1a;
      }
      .nav-btn.primary:hover:not(:disabled) { background: #333; }
    `;
    shadow.appendChild(s);
  }

  // ── Helper ───────────────────────────────────────────────────────────────
  function escHtml(str) {
    return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  // ── Self-init from state set by reader.js ──────────────────────────────
  const state = window.__readerState;
  if (state) {
    initModes(state.shadow, state.paragraphs, state.content);
  } else {
    console.error("[ADHD Reader] modes.js: no __readerState found.");
  }

  // ── Expose to reader.js ──────────────────────────────────────────────────
  window.__readerModes = { initModes, setMode };

  console.log("[ADHD Reader] modes.js loaded.");

})();
