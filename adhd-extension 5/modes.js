// modes.js — Stage 3
// Paragraph-by-paragraph and sentence-by-sentence reading modes.

(function () {

  function splitSentences(text) {
    const protected_ = text.replace(/\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|vs|etc|approx|Fig|St|Dept|Vol|No)\./g, "$1PROTECTEDDOT");
    const raw = protected_.split(/(?<=[.!?])\s+(?=[A-Z"'])/);
    return raw.map(s => s.replace(/PROTECTEDDOT/g, ".").trim()).filter(s => s.length > 0);
  }

  let shadow, paragraphs, contentEl, contentBlocks;
  let paraIndex = 0;
  let sentParagraphIndex = 0;
  let sentSentenceIndex = 0;
  let sentences = [];
  let currentMode = "full";
  let paraImages = [];

  function initModes(_shadow, _paragraphs, _contentEl, _contentBlocks) {
    shadow = _shadow; paragraphs = _paragraphs; contentEl = _contentEl;
    contentBlocks = _contentBlocks || [];
    paraImages = computeParaImages(contentBlocks, paragraphs.length);
  }

  function computeParaImages(blocks, paraCount) {
    const result = Array.from({ length: paraCount }, () => []);
    let idx = -1; const pre = [];
    blocks.forEach(b => {
      if (b.type === "text") { idx++; if (idx === 0) result[0].unshift(...pre); }
      else if (b.type === "image") { if (idx < 0) pre.push(b); else if (idx < paraCount) result[idx].push(b); }
    });
    return result;
  }

  function setMode(mode) {
    const parasContainer = shadow.getElementById("paragraphs-container");

    if (currentMode === "full" && mode !== "full") {
      const visIdx = getFullModeVisibleParaIndex();
      if (visIdx !== null) { paraIndex = visIdx; sentParagraphIndex = visIdx; sentSentenceIndex = 0; }
    }

    const prevMode = currentMode;
    currentMode = mode;
    removeModeUI();

    if (mode === "full") {
      parasContainer.style.display = "";
      Array.from(parasContainer.children).forEach(p => { p.style.opacity = "1"; p.style.display = ""; });
      const targetIdx = prevMode === "sentence" ? sentParagraphIndex : paraIndex;
      setTimeout(() => {
        const paras = shadow.querySelectorAll(".article-paragraph");
        const target = paras[targetIdx];
        if (target) target.scrollIntoView({ behavior: "instant", block: "start" });
      }, 16);
    } else if (mode === "para") {
      parasContainer.style.display = "none";
      renderParaMode();
    } else if (mode === "sentence") {
      parasContainer.style.display = "none";
      renderSentenceMode();
    }
  }

  function getFullModeVisibleParaIndex() {
    const paras = shadow.querySelectorAll(".article-paragraph");
    if (!paras.length) return null;
    const targetY = window.innerHeight * 0.4;
    let bestIdx = 0, bestDist = Infinity;
    for (let i = 0; i < paras.length; i++) {
      const rect = paras[i].getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
      const dist = Math.abs(rect.top - targetY);
      if (dist < bestDist) { bestDist = dist; bestIdx = i; }
    }
    return bestIdx;
  }

  function removeModeUI() {
    const existing = shadow.getElementById("mode-ui");
    if (existing) existing.remove();
  }

  function renderParaMode() {
    const total = paragraphs.length;
    if (paraIndex >= total) paraIndex = total - 1;
    if (paraIndex < 0) paraIndex = 0;
    removeModeUI();

    const progress = Math.round((paraIndex / total) * 100);
    const ui = document.createElement("div");
    ui.id = "mode-ui";
    ui.style.cssText = "padding: 0 32px;";

    const imgs = paraImages[paraIndex] || [];
    const imgHtml = imgs.map(img =>
      `<figure style="margin:16px 0;text-align:center;">
        <img src="${escAttr(img.src)}" alt="${escAttr(img.alt)}" loading="eager"
          style="max-width:100%;height:auto;border-radius:6px;display:block;margin:0 auto;"
          onerror="this.parentElement.style.display='none'">
        ${img.caption || img.alt ? `<figcaption style="font-size:0.8em;color:#888;margin-top:0.5em;font-style:italic;line-height:1.4;">${escHtml(img.caption || img.alt)}</figcaption>` : ''}
      </figure>`).join('');

    ui.innerHTML = `
      <div style="margin-bottom:16px;">
        <div style="font-size:12px;color:#999;margin-bottom:6px;letter-spacing:0.04em;">Paragraph ${paraIndex + 1} of ${total}</div>
        <div style="height:3px;background:#e8e4dc;border-radius:2px;overflow:hidden;">
          <div style="height:100%;width:${progress}%;background:#c4a882;border-radius:2px;transition:width 0.2s;"></div>
        </div>
      </div>
      <div id="mode-text" style="margin-bottom:${imgs.length ? '16px' : '32px'};">${escHtml(paragraphs[paraIndex])}</div>
      ${imgHtml}
      <div style="display:flex;gap:10px;align-items:center;margin-top:${imgs.length ? '24px' : '0'};">
        <button id="mode-prev" class="nav-btn" ${paraIndex === 0 ? "disabled" : ""}>← Back</button>
        <button id="mode-next" class="nav-btn primary" ${paraIndex >= total - 1 ? "disabled" : ""}>Next →</button>
        <span id="mode-done" style="display:${paraIndex >= total - 1 ? 'inline' : 'none'};color:#888;font-size:13px;font-style:italic;">End of article</span>
      </div>`;

    injectNavStyles();
    contentEl.appendChild(ui);

    shadow.getElementById("mode-prev").addEventListener("click", () => { if (paraIndex > 0) { paraIndex--; renderParaMode(); } });
    shadow.getElementById("mode-next").addEventListener("click", () => { if (paraIndex < total - 1) { paraIndex++; renderParaMode(); } });

    if (window.__readerState) { window.__readerState.__currentParaIndex = paraIndex; window.__readerState.__currentSentenceIndex = null; }
    const overlay = shadow.getElementById("reader-overlay");
    if (overlay) overlay.scrollTop = 0;
  }

  function renderSentenceMode() {
    sentences = splitSentences(paragraphs[sentParagraphIndex] || "");
    if (sentSentenceIndex >= sentences.length) sentSentenceIndex = 0;

    const totalParas = paragraphs.length, totalSents = sentences.length;
    const progress = Math.round(((sentParagraphIndex * 100) / totalParas) + ((sentSentenceIndex / totalSents) * (100 / totalParas)));

    const ui = document.createElement("div");
    ui.id = "mode-ui";
    ui.style.cssText = "padding: 0 32px;";
    ui.innerHTML = `
      <div style="margin-bottom:16px;">
        <div style="font-size:12px;color:#999;margin-bottom:6px;letter-spacing:0.04em;">Para ${sentParagraphIndex + 1}/${totalParas} · Sentence ${sentSentenceIndex + 1}/${totalSents}</div>
        <div style="height:3px;background:#e8e4dc;border-radius:2px;overflow:hidden;">
          <div style="height:100%;width:${progress}%;background:#c4a882;border-radius:2px;transition:width 0.2s;"></div>
        </div>
      </div>
      <div id="mode-text" style="margin-bottom:32px;transition:opacity 0.15s;">${escHtml(sentences[sentSentenceIndex] || "")}</div>
      ${(paraImages[sentParagraphIndex] || []).map(img =>
        `<figure style="margin:0 0 20px;text-align:center;">
          <img src="${escAttr(img.src)}" alt="${escAttr(img.alt)}" loading="eager"
            style="max-width:100%;height:auto;border-radius:6px;display:block;margin:0 auto;opacity:0.85;"
            onerror="this.parentElement.style.display='none'">
          ${img.caption || img.alt ? `<figcaption style="font-size:0.78em;color:#aaa;margin-top:0.4em;font-style:italic;">${escHtml(img.caption || img.alt)}</figcaption>` : ''}
        </figure>`).join('')}
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <button id="mode-prev" class="nav-btn" ${sentParagraphIndex === 0 && sentSentenceIndex === 0 ? "disabled" : ""}>← Back</button>
        <button id="mode-next" class="nav-btn primary" ${sentParagraphIndex === totalParas - 1 && sentSentenceIndex === totalSents - 1 ? "disabled" : ""}>Next →</button>
        <span id="mode-done" style="display:none;color:#888;font-size:13px;font-style:italic;">End of article</span>
      </div>`;

    injectNavStyles();
    contentEl.appendChild(ui);

    if (window.__readerState) { window.__readerState.__currentParaIndex = sentParagraphIndex; window.__readerState.__currentSentenceIndex = sentSentenceIndex; }

    shadow.getElementById("mode-next").addEventListener("click", () => {
      if (sentSentenceIndex < sentences.length - 1) { sentSentenceIndex++; }
      else if (sentParagraphIndex < totalParas - 1) { sentParagraphIndex++; sentSentenceIndex = 0; }
      else { shadow.getElementById("mode-next").style.display = "none"; shadow.getElementById("mode-done").style.display = "inline"; return; }
      removeModeUI(); renderSentenceMode();
    });
    shadow.getElementById("mode-prev").addEventListener("click", () => {
      if (sentSentenceIndex > 0) { sentSentenceIndex--; }
      else if (sentParagraphIndex > 0) { sentParagraphIndex--; sentSentenceIndex = splitSentences(paragraphs[sentParagraphIndex]).length - 1; }
      removeModeUI(); renderSentenceMode();
    });

    const overlay = shadow.getElementById("reader-overlay");
    if (overlay) overlay.scrollTop = 0;
  }

  let navStylesInjected = false;
  function injectNavStyles() {
    if (navStylesInjected) return;
    navStylesInjected = true;
    const s = document.createElement("style");
    s.textContent = `
      .nav-btn { padding:8px 20px;font-size:14px;border:1px solid #d0ccc4;border-radius:6px;background:white;cursor:pointer;color:#444;font-family:inherit; }
      .nav-btn:hover:not(:disabled) { background:#f0ede8; }
      .nav-btn:disabled { opacity:0.35;cursor:default; }
      .nav-btn.primary { background:#1a1a1a;color:white;border-color:#1a1a1a; }
      .nav-btn.primary:hover:not(:disabled) { background:#333; }
    `;
    shadow.appendChild(s);
  }

  function escHtml(str)  { return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
  function escAttr(str)  { return (str || "").replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

  const state = window.__readerState;
  if (state) initModes(state.shadow, state.paragraphs, state.content, state.contentBlocks);
  else console.error("[ADHD Reader] modes.js: no __readerState found.");

  window.__readerModes = { initModes, setMode };
  console.log("[ADHD Reader] modes.js loaded.");

})();
