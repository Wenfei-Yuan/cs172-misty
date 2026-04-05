// reader.js — Stage 2 (fixed v2)
// Key fix: overlay uses position:absolute (not fixed) inside the host,
// which avoids Canvas's CSS transform breaking fixed positioning.

(function () {

  const host = document.getElementById("adhd-reader-root");
  if (!host) { console.error("[ADHD Reader] reader.js: host not found."); return; }
  const shadow = host.shadowRoot;
  if (!shadow) { console.error("[ADHD Reader] reader.js: no shadow root."); return; }
  const data = host.__articleData;
  if (!data) { console.error("[ADHD Reader] reader.js: no article data."); return; }

  console.log("[ADHD Reader] reader.js starting, title:", data.title);

  // ── Parse content: paragraphs + images, in document order ─────────────
  // Walk Readability's HTML and build an ordered array of content blocks:
  //   { type: "text", content: "paragraph string" }
  //   { type: "image", src, alt, caption }
  const tempDiv = document.createElement("div");
  tempDiv.innerHTML = data.content;

  const contentBlocks = [];
  function walk(node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = node.tagName.toLowerCase();

      if (tag === "img") {
        const src = node.src || node.getAttribute("src");
        if (src && !src.startsWith("data:")) {
          contentBlocks.push({ type: "image", src, alt: node.alt || "", caption: "" });
        }
        return;
      }

      if (tag === "figure") {
        const img     = node.querySelector("img");
        const caption = node.querySelector("figcaption");
        if (img) {
          const src = img.src || img.getAttribute("src");
          if (src && !src.startsWith("data:")) {
            contentBlocks.push({
              type: "image", src,
              alt:     img.alt || "",
              caption: caption ? caption.textContent.trim() : "",
            });
          }
        }
        return;
      }

      // Paragraph tags — main content unit
      if (tag === "p") {
        const text = node.textContent.trim();
        if (text.length > 10) contentBlocks.push({ type: "text", content: text });
        return;
      }

      // Headings — treat as short text blocks
      if (/^h[1-6]$/.test(tag)) {
        const text = node.textContent.trim();
        if (text.length > 2) contentBlocks.push({ type: "text", content: text });
        return;
      }

      // List items — each bullet becomes its own text block with a bullet prefix
      if (tag === "li") {
        const text = node.textContent.trim();
        if (text.length > 5) contentBlocks.push({ type: "text", content: "• " + text });
        return;
      }

      // Blockquotes and preformatted text
      if (tag === "blockquote" || tag === "pre") {
        const text = node.textContent.trim();
        if (text.length > 10) contentBlocks.push({ type: "text", content: text });
        return;
      }

      // Skip nav, header, footer, aside — these are page chrome not content
      if (["nav", "header", "footer", "aside", "script", "style", "noscript"].includes(tag)) {
        return;
      }

      // Recurse into everything else
      node.childNodes.forEach(walk);
    }
  }
  tempDiv.childNodes.forEach(walk);

  // Deduplicate: remove blocks whose text is fully contained in the previous block
  // (happens when a <p> wraps a <li> or vice versa)
  const deduped = [];
  contentBlocks.forEach(block => {
    if (block.type !== "text") { deduped.push(block); return; }
    const prev = deduped[deduped.length - 1];
    if (prev && prev.type === "text" && prev.content.includes(block.content)) return;
    if (prev && prev.type === "text" && block.content.includes(prev.content) && block.content.length > prev.content.length) {
      deduped[deduped.length - 1] = block; return;
    }
    deduped.push(block);
  });

  // Fallback: if we got nothing, split plain text by newlines
  if (deduped.filter(b => b.type === "text").length === 0) {
    data.textContent.split(/\n\s*\n/).map(t => t.trim()).filter(t => t.length > 10)
      .forEach(t => deduped.push({ type: "text", content: t }));
  }

  // Replace contentBlocks with deduped version
  contentBlocks.length = 0;
  deduped.forEach(b => contentBlocks.push(b));

  // Paragraphs array (for modes.js which only handles text)
  const paragraphs = contentBlocks.filter(b => b.type === "text").map(b => b.content);

  console.log(`[ADHD Reader] ${paragraphs.length} paragraphs, ${contentBlocks.filter(b=>b.type==="image").length} images`);

  // ── Styles ────────────────────────────────────────────────────────────
  // The HOST element is position:fixed covering the full viewport.
  // Everything inside the shadow root uses position:absolute so that
  // CSS transforms on Canvas ancestor elements don't break our layout.
  const styleEl = document.createElement("style");
  styleEl.textContent = `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :host {
      display: block;
      position: fixed !important;
      inset: 0 !important;
      z-index: 2147483647 !important;
    }

    #reader-overlay {
      position: absolute;
      inset: 0;
      background: var(--reader-bg, #fffef9);
      overflow-y: auto;
      font-family: Georgia, "Times New Roman", serif;
      font-size: var(--reader-font-size, 18px);
      line-height: var(--reader-line-height, 1.8);
      color: var(--reader-text, #1a1a1a);
    }

    #reader-header {
      position: sticky;
      top: 0;
      z-index: 10;
    }

    #reader-toolbar {
      background: var(--reader-toolbar-bg, #fffef9);
      border-bottom: 1px solid var(--reader-border, #e8e4dc);
      padding: 12px 16px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    #toolbar-title {
      flex: 1;
      font-size: 16px;
      font-weight: 500;
      color: #444;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      min-width: 0;
    }

    .toolbar-btn {
      background: white;
      border: 1px solid #d0ccc4;
      border-radius: 6px;
      padding: 5px 12px;
      font-size: 13px;
      cursor: pointer;
      color: #444;
    }
    .toolbar-btn:hover  { background: #f0ede8; }
    .toolbar-btn.active { background: #e8e0d4; border-color: #999; font-weight: 600; }

    .toolbar-divider {
      width: 1px;
      height: 20px;
      background: #d0ccc4;
      flex-shrink: 0;
      margin: 0 2px;
    }

    #mode-study { color: #5a7a3a; border-color: #b8d4a0; background: #f4faf0; }
    #mode-study:hover { background: #e8f3e0; }
    #mode-study.active { background: #d4ecc4; border-color: #5a7a3a; font-weight: 600; }
    #mode-study.active:hover { background: #fee2e2 !important; color: #b91c1c !important; border-color: #fca5a5 !important; }


    #settings-panel {
      display: none;
      background: #f7f4ef;
      border-bottom: 1px solid #e0dbd3;
      padding: 14px 20px;
      gap: 24px;
      align-items: center;
      flex-wrap: wrap;
    }
    #settings-panel.open { display: flex; }

    .setting-row {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 13px;
      color: #555;
    }
    .setting-row input[type=range] { width: 100px; cursor: pointer; }
    .setting-value { font-size: 12px; color: #888; min-width: 32px; }

    #reader-content {
      max-width: 680px;
      margin: 0 auto;
      padding: 48px 32px 120px;
    }

    #article-title {
      font-size: 1.6em;
      font-weight: 700;
      line-height: 1.3;
      margin-bottom: 0.4em;
      color: #111;
    }

    #article-byline {
      font-size: 0.82em;
      color: #888;
      margin-bottom: 2em;
      font-style: italic;
    }

    .article-paragraph { margin-bottom: 1.4em; }
    .article-paragraph.bullet { padding-left: 1.2em; text-indent: -1.2em; margin-bottom: 0.6em; }

    .article-image {
      margin: 1.6em 0;
      text-align: center;
    }
    .article-image img {
      max-width: 100%;
      height: auto;
      border-radius: 6px;
      display: block;
      margin: 0 auto;
    }
    .article-image figcaption {
      font-size: 0.78em;
      color: #888;
      margin-top: 0.5em;
      font-style: italic;
      line-height: 1.4;
    }

    /* reading guide removed */

    #reader-close {
      background: none;
      border: 1px solid var(--reader-border, #d0ccc4);
      border-radius: 6px;
      font-size: 15px;
      cursor: pointer;
      color: #888;
      padding: 4px 9px;
      line-height: 1;
      margin-left: 4px;
      flex-shrink: 0;
    }
    #reader-close:hover { background: #fee2e2; color: #b91c1c; border-color: #fca5a5; }

    .theme-swatch {
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border: 2px solid #ccc;
      cursor: pointer;
      flex-shrink: 0;
      transition: border-color 0.15s;
    }
    .theme-swatch:hover  { border-color: #888; }
    .theme-swatch.active { border-color: #333; border-width: 2.5px; }

    #settings-panel label { white-space: nowrap; }
  `;
  shadow.appendChild(styleEl);

  // ── Build DOM ─────────────────────────────────────────────────────────
  const overlay = document.createElement("div");
  overlay.id = "reader-overlay";

  // Sticky header wrapper — toolbar + settings panel stick together
  const header = document.createElement("div");
  header.id = "reader-header";
  overlay.appendChild(header);

  // Toolbar
  const toolbar = document.createElement("div");
  toolbar.id = "reader-toolbar";
  toolbar.innerHTML = `
    <span id="toolbar-title">${escHtml(data.title)}</span>
    <span id="robot-status" title="Robot not connected" style="
      font-size: 11px; padding: 3px 8px; border-radius: 20px;
      background: #f0ede8; color: #aaa; border: 1px solid #ddd;
      white-space: nowrap; flex-shrink: 0;
    ">&#9711; Robot</span>
    <button class="toolbar-btn active" id="mode-full">Full</button>
    <button class="toolbar-btn" id="mode-para">Para</button>
    <button class="toolbar-btn" id="mode-sentence">Sent</button>
    <span class="toolbar-divider"></span>
    <button class="toolbar-btn" id="mode-study">Study</button>
    <span class="toolbar-divider"></span>
    <button class="toolbar-btn" id="settings-toggle">⚙ Settings</button>
    <button id="reader-close" title="Close reader">✕</button>
  `;
  header.appendChild(toolbar);

  // Settings panel
  const settingsPanel = document.createElement("div");
  settingsPanel.id = "settings-panel";
  settingsPanel.innerHTML = `
    <div class="setting-row">
      <label>Font size</label>
      <input type="range" id="font-size-slider" min="14" max="28" step="1" value="18">
      <span class="setting-value" id="font-size-value">18px</span>
    </div>
    <div class="setting-row">
      <label>Line spacing</label>
      <input type="range" id="line-height-slider" min="1.4" max="2.4" step="0.1" value="1.8">
      <span class="setting-value" id="line-height-value">1.8</span>
    </div>
    <div class="setting-row">
      <button id="reset-defaults" style="
        padding: 4px 12px;
        font-size: 12px;
        border: 1px solid #d0ccc4;
        border-radius: 6px;
        background: white;
        cursor: pointer;
        color: #666;
        font-family: inherit;
      ">Reset defaults</button>
    </div>
    <div class="setting-row">
      <label>Theme</label>
      <button class="theme-swatch active" id="theme-warm"  title="Warm white"  style="background:#fffef9;"></button>
      <button class="theme-swatch"        id="theme-white" title="Pure white"  style="background:#ffffff;"></button>
      <button class="theme-swatch"        id="theme-green" title="Green tint"  style="background:#f0f7f0;"></button>
      <button class="theme-swatch"        id="theme-dark"  title="Dark mode"  style="background:#1a1a1a;"></button>
    </div>
  `;
  header.appendChild(settingsPanel);

  // Article content
  const content = document.createElement("div");
  content.id = "reader-content";

  const titleEl = document.createElement("h1");
  titleEl.id = "article-title";
  titleEl.textContent = data.title || "Untitled";
  content.appendChild(titleEl);

  if (data.byline) {
    const bylineEl = document.createElement("p");
    bylineEl.id = "article-byline";
    bylineEl.textContent = data.byline;
    content.appendChild(bylineEl);
  }

  const parasContainer = document.createElement("div");
  parasContainer.id = "paragraphs-container";
  let paraIndex = 0;
  contentBlocks.forEach(block => {
    if (block.type === "text") {
      const p = document.createElement("p");
      const isBullet = block.content.startsWith("• ");
      p.className = "article-paragraph" + (isBullet ? " bullet" : "");
      p.dataset.index = paraIndex++;
      p.textContent = block.content;
      parasContainer.appendChild(p);
    } else if (block.type === "image") {
      const fig = document.createElement("figure");
      fig.className = "article-image";
      const img = document.createElement("img");
      img.src = block.src;
      img.alt = block.alt;
      img.loading = "lazy";
      fig.appendChild(img);
      if (block.caption) {
        const cap = document.createElement("figcaption");
        cap.textContent = block.caption;
        fig.appendChild(cap);
      } else if (block.alt) {
        const cap = document.createElement("figcaption");
        cap.textContent = block.alt;
        fig.appendChild(cap);
      }
      parasContainer.appendChild(fig);
    }
  });
  content.appendChild(parasContainer);
  overlay.appendChild(content);

  shadow.appendChild(overlay);


  // Also force the host element's styles via JS in case page CSS overrides them
  host.style.cssText = `
    position: fixed !important;
    inset: 0 !important;
    z-index: 2147483647 !important;
    pointer-events: auto !important;
    transform: none !important;
  `;

  // ── Interactions ───────────────────────────────────────────────────────
  shadow.getElementById("reader-close").addEventListener("click", () => host.remove());

  const fontSlider  = shadow.getElementById("font-size-slider");
  const fontValue   = shadow.getElementById("font-size-value");
  const lineSlider  = shadow.getElementById("line-height-slider");
  const lineValue   = shadow.getElementById("line-height-value");
  const settingsBtn = shadow.getElementById("settings-toggle");

  chrome.storage.sync.get(["fontSize", "lineHeight", "theme"], (saved) => {
    const fs = saved.fontSize   || 18;
    const lh = saved.lineHeight || 1.8;
    applyFontSize(fs);
    applyLineHeight(lh);
    fontSlider.value = fs;
    lineSlider.value = lh;
    if (saved.theme) applyTheme(saved.theme);
  });

  function applyFontSize(val) {
    overlay.style.setProperty("--reader-font-size", val + "px");
    fontValue.textContent = val + "px";
  }
  function applyLineHeight(val) {
    overlay.style.setProperty("--reader-line-height", val);
    lineValue.textContent = parseFloat(val).toFixed(1);
  }

  fontSlider.addEventListener("input", () => {
    applyFontSize(fontSlider.value);
    chrome.storage.sync.set({ fontSize: Number(fontSlider.value) });
  });
  lineSlider.addEventListener("input", () => {
    applyLineHeight(lineSlider.value);
    chrome.storage.sync.set({ lineHeight: Number(lineSlider.value) });
  });
  settingsBtn.addEventListener("click", () => settingsPanel.classList.toggle("open"));

  // Reset defaults
  shadow.getElementById("reset-defaults").addEventListener("click", () => {
    const DEFAULT_FS = 18;
    const DEFAULT_LH = 1.8;
    applyFontSize(DEFAULT_FS);
    applyLineHeight(DEFAULT_LH);
    fontSlider.value = DEFAULT_FS;
    lineSlider.value = DEFAULT_LH;
    applyTheme("warm");
    chrome.storage.sync.set({ fontSize: DEFAULT_FS, lineHeight: DEFAULT_LH, theme: "warm" });
  });

  // ── Theme switcher ───────────────────────────────────────────────────
  const themes = {
    warm:  { bg: "#fffef9", text: "#1a1a1a", toolbar: "#fffef9",  border: "#e8e4dc" },
    white: { bg: "#ffffff", text: "#1a1a1a", toolbar: "#ffffff",  border: "#e8e4dc" },
    green: { bg: "#f0f7f0", text: "#1a2e1a", toolbar: "#e8f3e8",  border: "#c8dfc8" },
    dark:  { bg: "#1a1a1a", text: "#e8e6e0", toolbar: "#242424",  border: "#3a3a3a" },
  };

  function applyTheme(name) {
    const t = themes[name] || themes.warm;
    const dark = name === "dark";

    overlay.style.setProperty("--reader-bg",         t.bg);
    overlay.style.setProperty("--reader-text",       t.text);
    overlay.style.setProperty("--reader-toolbar-bg", t.toolbar);
    overlay.style.setProperty("--reader-border",     t.border);

    // Header + toolbar background (sticky wrapper needs it too)
    const tb = shadow.getElementById("reader-toolbar");
    tb.style.background   = t.toolbar;
    tb.style.borderColor  = t.border;
    const hd = shadow.getElementById("reader-header");
    if (hd) hd.style.background = t.toolbar;

    // Settings panel
    const sp = shadow.getElementById("settings-panel");
    sp.style.background = dark ? "#242424" : name === "green" ? "#e8f3e8" : "#f7f4ef";
    sp.style.color      = dark ? "#e8e6e0" : "#555";
    sp.style.borderColor = t.border;

    // Toolbar buttons
    const btnBg     = dark ? "#2e2e2e" : "white";
    const btnColor  = dark ? "#e8e6e0" : "#444";
    const btnBorder = dark ? "#4a4a4a" : "#d0ccc4";
    const btnActive = dark ? "#444"    : "#e8e0d4";
    shadow.querySelectorAll(".toolbar-btn").forEach(btn => {
      btn.style.background   = btn.classList.contains("active") ? btnActive : btnBg;
      btn.style.color        = btnColor;
      btn.style.borderColor  = btnBorder;
    });

    // Close button
    const cb = shadow.getElementById("reader-close");
    cb.style.color       = dark ? "#aaa" : "#888";
    cb.style.borderColor = btnBorder;

    // Article title + byline
    const titleEl = shadow.getElementById("article-title");
    if (titleEl) titleEl.style.color = dark ? "#f0eeea" : "#111";
    const bylineEl = shadow.getElementById("article-byline");
    if (bylineEl) bylineEl.style.color = dark ? "#888" : "#888";

    // Swatch active ring — invert for dark swatch so it's visible
    ["warm","white","green","dark"].forEach(n => {
      const sw = shadow.getElementById("theme-" + n);
      sw.classList.toggle("active", n === name);
      sw.style.borderColor = n === name ? (dark && n === "dark" ? "#aaa" : "#333") : "#ccc";
    });

    chrome.storage.sync.set({ theme: name });

    // Re-apply to mode UI buttons if they exist (nav buttons in para/sentence mode)
    shadow.querySelectorAll(".nav-btn").forEach(btn => {
      if (btn.classList.contains("primary")) {
        btn.style.background  = dark ? "#e8e6e0" : "#1a1a1a";
        btn.style.color       = dark ? "#1a1a1a" : "white";
        btn.style.borderColor = dark ? "#e8e6e0" : "#1a1a1a";
      } else {
        btn.style.background  = btnBg;
        btn.style.color       = btnColor;
        btn.style.borderColor = btnBorder;
      }
    });
  }

  ["warm","white","green","dark"].forEach(name => {
    shadow.getElementById("theme-" + name).addEventListener("click", () => applyTheme(name));
  });



  // Mode switcher — delegates to modes.js once it loads.
  let studyActive = false;

  function setMode(mode) {
    shadow.getElementById("mode-full").classList.toggle("active",     mode === "full");
    shadow.getElementById("mode-para").classList.toggle("active",     mode === "para");
    shadow.getElementById("mode-sentence").classList.toggle("active", mode === "sentence");
    // Study button keeps .active as long as studyActive is true
    if (!studyActive) {
      shadow.getElementById("mode-study").classList.toggle("active", mode === "study");
    }

    if (mode === "study") {
      if (studyActive) {
        deactivateStudy();
        return;
      }
      // Start Study in Full mode
      studyActive = true;
      shadow.getElementById("mode-full").classList.add("active");
      if (window.__readerModes) window.__readerModes.setMode("full");
      if (window.__readerPhases) window.__readerPhases.activate("full");
      shadow.getElementById("mode-study").textContent = "Exit Study";
    } else {
      // Switching reading mode while Study is active — just pass through
      if (window.__readerModes) window.__readerModes.setMode(mode);
      // Tell phases to show/hide skim pill based on mode
      if (studyActive && window.__readerPhases) {
        window.__readerPhases.setReadingMode(mode);
      }
    }
  }

  function deactivateStudy() {
    if (window.__readerPhases) {
      window.__readerPhases.deactivate(() => finishDeactivateStudy());
    } else {
      finishDeactivateStudy();
    }
  }

  function finishDeactivateStudy() {
    studyActive = false;
    const studyBtn = shadow.getElementById("mode-study");
    studyBtn.classList.remove("active");
    studyBtn.textContent = "Study";
    // Return to full mode
    shadow.getElementById("mode-full").classList.add("active");
    ["mode-para","mode-sentence"].forEach(id => {
      shadow.getElementById(id).classList.remove("active");
    });
    if (window.__readerModes) window.__readerModes.setMode("full");
  }

  // Expose state for modes.js and phases.js
  window.__readerState = { shadow, paragraphs, content };

  shadow.getElementById("mode-full").addEventListener("click",     () => setMode("full"));
  shadow.getElementById("mode-para").addEventListener("click",     () => setMode("para"));
  shadow.getElementById("mode-sentence").addEventListener("click", () => setMode("sentence"));
  shadow.getElementById("mode-study").addEventListener("click",    () => setMode("study"));
  setMode("full");

  function escHtml(str) {
    return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  console.log(`[ADHD Reader] Stage 3 ready — ${paragraphs.length} paragraphs.`);

})();
