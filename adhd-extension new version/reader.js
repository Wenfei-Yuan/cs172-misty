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
  const tempDiv = document.createElement("div");
  tempDiv.innerHTML = data.content;

  function resolveImageSrc(src) {
    if (!src || src.startsWith("data:")) return null;
    try { return new URL(src, document.baseURI).href; } catch { return null; }
  }

  function getBestSrc(imgEl) {
    const attrs = ["currentSrc","src","data-src","data-lazy-src","data-original","data-full-src","data-hi-res-src","data-image","data-url","data-srcset"];
    for (const attr of attrs) {
      const val = attr === "currentSrc" ? (imgEl.currentSrc || "") : (imgEl.getAttribute(attr) || "");
      if (!val || val.startsWith("data:")) continue;
      const candidate = pickBestSrcsetCandidate(val);
      const resolved = resolveImageSrc(candidate);
      if (resolved) return resolved;
    }
    const srcset = imgEl.getAttribute("srcset") || imgEl.srcset || "";
    if (srcset) {
      const resolved = resolveImageSrc(pickBestSrcsetCandidate(srcset));
      if (resolved) return resolved;
    }
    return null;
  }

  function pickBestSrcsetCandidate(value) {
    if (!value.includes(",")) return value.trim().split(" ")[0];
    const candidates = value.split(",").map(part => {
      const bits = part.trim().split(/\s+/);
      const score = bits[1] && /^\d+(\.\d+)?[wx]$/.test(bits[1]) ? parseFloat(bits[1]) : 0;
      return { url: bits[0], score };
    }).filter(c => c.url);
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.url || value.split(",")[0].trim().split(" ")[0];
  }

  function getLinkedImageSrc(el) {
    const link = el.querySelector("a[href]");
    if (!link) return null;
    return getImageHref(link);
  }

  function getImageHref(link) {
    const href = link.getAttribute("href") || "";
    if (!/\.(avif|gif|jpe?g|png|svg|webp)(\?|#|$)/i.test(href)) return null;
    return resolveImageSrc(href);
  }

  function inferFigureNumber(src) {
    const match = (src || "").match(/g(\d{3,})/i);
    return match ? parseInt(match[1], 10) : null;
  }

  function linkedFigureCaption(label, src) {
    const clean = (label || "").trim().replace(/\s+/g, " ");
    if (clean && !/^image:?$/i.test(clean)) return clean;
    const figureNo = inferFigureNumber(src);
    return figureNo ? `Figure ${figureNo}` : "";
  }

  function canonicalImageKey(src) {
    const figureNo = inferFigureNumber(src);
    if (figureNo) return `figure-${figureNo}`;
    try {
      const url = new URL(src);
      return decodeURIComponent(url.pathname)
        .replace(/\/(large|small|thumb|thumbnail|preview)\//ig, "/")
        .replace(/[-_](large|small|thumb|thumbnail|preview)(?=\.)/ig, "")
        .replace(/[-_]\d+x\d+(?=\.)/g, "");
    } catch {
      return src;
    }
  }

  function extractCaption(el) {
    const captionEl = el.querySelector("figcaption, caption, [class*='caption' i], [class*='legend' i], [class*='desc' i]");
    return captionEl ? captionEl.textContent.trim().replace(/\s+/g, " ") : "";
  }

  function extractTable(tableEl) {
    const rows = Array.from(tableEl.querySelectorAll("tr")).map(tr =>
      Array.from(tr.querySelectorAll("th,td")).map(cell => ({
        text: cell.textContent.trim().replace(/\s+/g, " "),
        header: cell.tagName.toLowerCase() === "th",
        colspan: Math.max(1, parseInt(cell.getAttribute("colspan") || "1", 10)),
      })).filter(cell => cell.text.length > 0)
    ).filter(row => row.length > 0);

    if (rows.length === 0) return null;
    return { type: "table", caption: extractCaption(tableEl), rows };
  }

  const contentBlocks = [];
  function walk(node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const tag = node.tagName.toLowerCase();
      if (tag === "table") {
        const table = extractTable(node);
        if (table) contentBlocks.push(table);
        return;
      }
      if (tag === "img") {
        const src = getBestSrc(node);
        if (src) contentBlocks.push({ type: "image", src, alt: node.alt || "", caption: "" });
        return;
      }
      if (tag === "a") {
        const src = getImageHref(node);
        if (src && inferFigureNumber(src)) {
          const label = node.textContent.trim().replace(/\s+/g, " ");
          const caption = linkedFigureCaption(label, src);
          contentBlocks.push({ type: "image", src, alt: caption || "Article figure", caption });
          return;
        }
      }
      if (tag === "figure") {
        const img = node.querySelector("img");
        const caption = node.querySelector("figcaption");
        const table = node.querySelector("table");
        if (table) {
          const tableBlock = extractTable(table);
          if (tableBlock) {
            tableBlock.caption = (caption ? caption.textContent.trim() : "") || tableBlock.caption;
            contentBlocks.push(tableBlock);
          }
          return;
        }
        const src = img ? getBestSrc(img) : getLinkedImageSrc(node);
        if (!img && src && !inferFigureNumber(src)) return;
        if (src) {
          contentBlocks.push({ type: "image", src, alt: img?.alt || "", caption: caption ? caption.textContent.trim() : extractCaption(node) });
        }
        return;
      }
      const classId = `${node.className || ""} ${node.id || ""}`;
      if (/(figure|fig|image|table)/i.test(classId)) {
        const table = node.querySelector("table");
        if (table) {
          const tableBlock = extractTable(table);
          if (tableBlock) {
            tableBlock.caption = tableBlock.caption || extractCaption(node);
            contentBlocks.push(tableBlock);
          }
          return;
        }
        const img = node.querySelector("img");
        const src = img ? getBestSrc(img) : getLinkedImageSrc(node);
        if (!img && src && !inferFigureNumber(src)) return;
        if (src) {
          contentBlocks.push({ type: "image", src, alt: img?.alt || "", caption: extractCaption(node) });
          return;
        }
      }
      if (tag === "p") { const text = node.textContent.trim(); if (text.length > 10) contentBlocks.push({ type: "text", content: text }); return; }
      if (/^h[1-6]$/.test(tag)) { const text = node.textContent.trim(); if (text.length > 2) contentBlocks.push({ type: "text", content: text }); return; }
      if (tag === "li") { const text = node.textContent.trim(); if (text.length > 5) contentBlocks.push({ type: "text", content: "• " + text }); return; }
      if (tag === "blockquote" || tag === "pre") { const text = node.textContent.trim(); if (text.length > 10) contentBlocks.push({ type: "text", content: text }); return; }
      if (["nav","header","footer","aside","script","style","noscript"].includes(tag)) return;
      node.childNodes.forEach(walk);
    }
  }
  tempDiv.childNodes.forEach(walk);

  const deduped = [];
  const seenImagePathnames = new Set();
  contentBlocks.forEach(block => {
    if (block.type === "image") {
      // Deduplicate images by pathname (handles CDN variants, different query strings, etc.)
      const key = canonicalImageKey(block.src);
      if (seenImagePathnames.has(key)) return; // skip duplicate
      seenImagePathnames.add(key);
      deduped.push(block);
      return;
    }
    if (block.type !== "text") { deduped.push(block); return; }
    const prev = deduped[deduped.length - 1];
    if (prev && prev.type === "text" && prev.content.includes(block.content)) return;
    if (prev && prev.type === "text" && block.content.includes(prev.content) && block.content.length > prev.content.length) { deduped[deduped.length - 1] = block; return; }
    deduped.push(block);
  });
  if (deduped.filter(b => b.type === "text").length === 0) {
    data.textContent.split(/\n\s*\n/).map(t => t.trim()).filter(t => t.length > 10).forEach(t => deduped.push({ type: "text", content: t }));
  }
  contentBlocks.length = 0;
  deduped.forEach(b => contentBlocks.push(b));

  const paragraphs = contentBlocks.filter(b => b.type === "text").map(b => b.content);
  console.log(`[ADHD Reader] ${paragraphs.length} paragraphs, ${contentBlocks.filter(b=>b.type==="image").length} images`);

  // ── Styles ────────────────────────────────────────────────────────────
  const styleEl = document.createElement("style");
  styleEl.textContent = `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :host { display: block; position: fixed !important; inset: 0 !important; z-index: 2147483647 !important; }
    #reader-overlay { position: absolute; inset: 0; background: var(--reader-bg, #fffef9); overflow-y: auto; font-family: Georgia, "Times New Roman", serif; font-size: var(--reader-font-size, 18px); line-height: var(--reader-line-height, 1.8); color: var(--reader-text, #1a1a1a); }
    #reader-header { position: sticky; top: 0; z-index: 10; }
    #reader-toolbar { background: var(--reader-toolbar-bg, #fffef9); border-bottom: 1px solid var(--reader-border, #e8e4dc); padding: 12px 16px; display: flex; align-items: center; gap: 8px; }
    #toolbar-title { flex: 1; font-size: 16px; font-weight: 500; color: #444; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; }
    .toolbar-btn { background: white; border: 1px solid #d0ccc4; border-radius: 6px; padding: 5px 12px; font-size: 13px; cursor: pointer; color: #444; }
    .toolbar-btn:hover  { background: #f0ede8; }
    .toolbar-btn.active { background: #e8e0d4; border-color: #999; font-weight: 600; }
    .toolbar-divider { width: 1px; height: 20px; background: #d0ccc4; flex-shrink: 0; margin: 0 2px; }
    #search-panel { display: none; background: #f7f4ef; border-bottom: 1px solid #e0dbd3; padding: 10px 16px; gap: 8px; align-items: center; }
    #search-panel.open { display: flex; }
    #reader-search-input { flex: 1; min-width: 120px; max-width: 360px; border: 1px solid #d0ccc4; border-radius: 6px; padding: 6px 10px; font-size: 13px; font-family: inherit; background: white; color: #222; outline: none; }
    #reader-search-input:focus { border-color: #7a6030; box-shadow: 0 0 0 2px rgba(196,168,130,0.25); }
    .search-btn { background: white; border: 1px solid #d0ccc4; border-radius: 6px; padding: 5px 10px; font-size: 13px; cursor: pointer; color: #444; font-family: inherit; }
    .search-btn:hover:not(:disabled) { background: #f0ede8; }
    .search-btn:disabled { opacity: 0.4; cursor: default; }
    #search-count { min-width: 64px; font-size: 12px; color: #777; text-align: center; white-space: nowrap; }
    .reader-search-mark { background: #f8e58c; color: inherit; border-radius: 2px; padding: 0 1px; }
    .reader-search-mark.current { background: #f59e0b; box-shadow: 0 0 0 2px rgba(245,158,11,0.25); }
    #settings-panel { display: none; background: #f7f4ef; border-bottom: 1px solid #e0dbd3; padding: 14px 20px; gap: 24px; align-items: center; flex-wrap: wrap; }
    #settings-panel.open { display: flex; }
    .setting-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #555; }
    .setting-row input[type=range] { width: 100px; cursor: pointer; }
    .setting-value { font-size: 12px; color: #888; min-width: 32px; }
    #reader-content { max-width: 680px; margin: 0 auto; padding: 48px 32px 120px; }
    #article-title { font-size: 1.6em; font-weight: 700; line-height: 1.3; margin-bottom: 0.4em; color: #111; }
    #article-byline { font-size: 0.82em; color: #888; margin-bottom: 2em; font-style: italic; }
    .article-paragraph { margin-bottom: 1.4em; }
    .article-paragraph.bullet { padding-left: 1.2em; text-indent: -1.2em; margin-bottom: 0.6em; }
    .article-image { margin: 1.6em 0; text-align: center; }
    .article-image img { max-width: 100%; height: auto; border-radius: 6px; display: block; margin: 0 auto; cursor: zoom-in; }
    .article-image figcaption { font-size: 0.78em; color: #888; margin-top: 0.5em; font-style: italic; line-height: 1.4; }
    .article-table { margin: 1.8em 0; overflow-x: auto; border: 1px solid var(--reader-border, #e8e4dc); border-radius: 6px; background: rgba(255,255,255,0.42); }
    .article-table table { width: 100%; min-width: 520px; border-collapse: collapse; font-size: 0.78em; line-height: 1.45; }
    .article-table caption { caption-side: top; text-align: left; padding: 10px 12px; color: #666; font-size: 0.9em; font-style: italic; border-bottom: 1px solid var(--reader-border, #e8e4dc); }
    .article-table th, .article-table td { border: 1px solid var(--reader-border, #e8e4dc); padding: 8px 10px; vertical-align: top; text-align: left; }
    .article-table th { background: rgba(90,122,58,0.10); font-weight: 700; color: inherit; }
    #reader-close { background: none; border: 1px solid var(--reader-border, #d0ccc4); border-radius: 6px; font-size: 15px; cursor: pointer; color: #888; padding: 4px 9px; line-height: 1; margin-left: 4px; flex-shrink: 0; }
    #reader-close:hover { background: #fee2e2; color: #b91c1c; border-color: #fca5a5; }
    .theme-swatch { width: 22px; height: 22px; border-radius: 50%; border: 2px solid #ccc; cursor: pointer; flex-shrink: 0; transition: border-color 0.15s; }
    .theme-swatch:hover  { border-color: #888; }
    .theme-swatch.active { border-color: #333; border-width: 2.5px; }
    #settings-panel label { white-space: nowrap; }
  `;
  shadow.appendChild(styleEl);

  // ── Build DOM ─────────────────────────────────────────────────────────
  const overlay = document.createElement("div");
  overlay.id = "reader-overlay";

  const header = document.createElement("div");
  header.id = "reader-header";
  overlay.appendChild(header);

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
    <button class="toolbar-btn" id="search-toggle" title="Find in reader">Find</button>
    <button class="toolbar-btn" id="settings-toggle">⚙ Settings</button>
    <button id="reader-close" title="Close reader">✕</button>
  `;
  header.appendChild(toolbar);

  const searchPanel = document.createElement("div");
  searchPanel.id = "search-panel";
  searchPanel.innerHTML = `
    <input id="reader-search-input" type="search" placeholder="Find in article" autocomplete="off" spellcheck="false">
    <button class="search-btn" id="search-prev" title="Previous match" disabled>Prev</button>
    <button class="search-btn" id="search-next" title="Next match" disabled>Next</button>
    <span id="search-count">0 / 0</span>
    <button class="search-btn" id="search-clear" title="Clear search">Clear</button>
    <button class="search-btn" id="search-close" title="Close find">Close</button>
  `;
  header.appendChild(searchPanel);

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
      <button id="reset-defaults" style="padding:4px 12px;font-size:12px;border:1px solid #d0ccc4;border-radius:6px;background:white;cursor:pointer;color:#666;font-family:inherit;">Reset defaults</button>
    </div>
    <div class="setting-row">
      <label>Theme</label>
      <button class="theme-swatch active" id="theme-warm"  title="Warm white" style="background:#fffef9;"></button>
      <button class="theme-swatch"        id="theme-white" title="Pure white" style="background:#ffffff;"></button>
      <button class="theme-swatch"        id="theme-green" title="Green tint" style="background:#f0f7f0;"></button>
      <button class="theme-swatch"        id="theme-dark"  title="Dark mode"  style="background:#1a1a1a;"></button>
    </div>
  `;
  header.appendChild(settingsPanel);

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
      img.src = block.src; img.alt = block.alt; img.loading = "eager";
      img.onerror = () => { fig.style.display = "none"; };
      img.addEventListener("click", () => window.open(block.src, "_blank", "noopener"));
      fig.appendChild(img);
      const capText = block.caption || block.alt;
      if (capText) { const cap = document.createElement("figcaption"); cap.textContent = capText; fig.appendChild(cap); }
      parasContainer.appendChild(fig);
    } else if (block.type === "table") {
      const tableWrap = document.createElement("figure");
      tableWrap.className = "article-table";
      const table = document.createElement("table");
      if (block.caption) {
        const caption = document.createElement("caption");
        caption.textContent = block.caption;
        table.appendChild(caption);
      }
      const tbody = document.createElement("tbody");
      block.rows.forEach(row => {
        const tr = document.createElement("tr");
        row.forEach(cell => {
          const cellEl = document.createElement(cell.header ? "th" : "td");
          if (cell.colspan > 1) cellEl.colSpan = cell.colspan;
          cellEl.textContent = cell.text;
          tr.appendChild(cellEl);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      tableWrap.appendChild(table);
      parasContainer.appendChild(tableWrap);
    }
  });
  content.appendChild(parasContainer);
  overlay.appendChild(content);
  shadow.appendChild(overlay);

  host.style.cssText = `position:fixed!important;inset:0!important;z-index:2147483647!important;pointer-events:auto!important;transform:none!important;`;

  // ── Interactions ───────────────────────────────────────────────────────
  shadow.getElementById("reader-close").addEventListener("click", (event) => {
    const notes = window.__readerPhases?.getSavedNotes?.() || [];
    const logUnsaved = window.__readerLogExported !== true;
    if (notes.length > 0 || logUnsaved) {
      event.stopImmediatePropagation();
      showQuitConfirm(notes.length, logUnsaved);
      return;
    }
    closeReader();
  });

  function closeReader() {
    document.removeEventListener("keydown", handleFindShortcut, true);
    window.dispatchEvent(new CustomEvent("ADHD_READER_CLOSING"));
    host.remove();
  }

  function showQuitConfirm(noteCount, logUnsaved) {
    const existing = shadow.getElementById("quit-confirm");
    if (existing) existing.remove();

    const title = logUnsaved ? "Session log not saved" : "Unsaved notes";
    const messages = [];
    if (logUnsaved) messages.push("You have not exported your session log yet.");
    if (noteCount > 0) messages.push(`You have ${noteCount} note${noteCount > 1 ? "s" : ""}.`);
    const noteSaveButton = noteCount > 0
      ? `<button id="qc-save" style="padding:8px 18px;font-size:13px;border:1px solid #5a7a3a;border-radius:8px;background:#5a7a3a;cursor:pointer;color:white;font-family:inherit;">Save notes &amp; close</button>`
      : "";
    const logSaveButton = logUnsaved
      ? `<button id="qc-save-log" style="padding:8px 18px;font-size:13px;border:1px solid #7a6030;border-radius:8px;background:#7a6030;cursor:pointer;color:white;font-family:inherit;">Save log</button>`
      : "";

    const modal = document.createElement("div");
    modal.id = "quit-confirm";
    modal.style.cssText = [
      "position:absolute", "inset:0", "background:rgba(0,0,0,0.35)",
      "display:flex", "align-items:center", "justify-content:center",
      "z-index:100", "font-family:Georgia,serif"
    ].join(";");
    modal.innerHTML = `
      <div style="background:#fffef9;border-radius:14px;padding:32px 36px;max-width:380px;width:90%;box-shadow:0 8px 40px rgba(0,0,0,0.18);text-align:center;">
        <div style="font-size:1.1em;font-weight:700;color:#1a1a1a;margin-bottom:0.5em;">${title}</div>
        <div style="font-size:0.88em;color:#666;line-height:1.7;margin-bottom:1.8em;">${messages.map(escHtml).join("<br>")}<br>Are you sure you want to quit?</div>
        <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
          <button id="qc-cancel" style="padding:8px 18px;font-size:13px;border:1px solid #d0ccc4;border-radius:8px;background:white;cursor:pointer;color:#555;font-family:inherit;">Cancel</button>
          <button id="qc-anyway" style="padding:8px 18px;font-size:13px;border:1px solid #d0ccc4;border-radius:8px;background:white;cursor:pointer;color:#999;font-family:inherit;">Quit anyway</button>
          ${logSaveButton}
          ${noteSaveButton}
        </div>
        <div id="qc-status" style="font-size:12px;color:#888;margin-top:12px;min-height:16px;"></div>
      </div>`;
    shadow.appendChild(modal);

    shadow.getElementById("qc-cancel").addEventListener("click", () => modal.remove());
    shadow.getElementById("qc-anyway").addEventListener("click", () => closeReader());
    shadow.getElementById("qc-save-log")?.addEventListener("click", () => {
      const status = shadow.getElementById("qc-status");
      if (typeof window.__readerExportLog !== "function") {
        if (status) status.textContent = "Log export is not ready yet.";
        return;
      }
      window.__readerExportLog(status, (ok) => {
        if (ok) {
          const btn = shadow.getElementById("qc-save-log");
          if (btn) {
            btn.textContent = "Log saved";
            btn.disabled = true;
            btn.style.opacity = "0.55";
            btn.style.cursor = "default";
          }
        }
      });
    });
    shadow.getElementById("qc-save")?.addEventListener("click", () => {
      const saved = window.__readerPhases?.downloadNotes?.();
      if (saved !== false) setTimeout(() => closeReader(), 400);
    });
  }

  const fontSlider  = shadow.getElementById("font-size-slider");
  const fontValue   = shadow.getElementById("font-size-value");
  const lineSlider  = shadow.getElementById("line-height-slider");
  const lineValue   = shadow.getElementById("line-height-value");
  const settingsBtn = shadow.getElementById("settings-toggle");
  const searchBtn   = shadow.getElementById("search-toggle");
  const searchInput = shadow.getElementById("reader-search-input");
  const searchPrev  = shadow.getElementById("search-prev");
  const searchNext  = shadow.getElementById("search-next");
  const searchClear = shadow.getElementById("search-clear");
  const searchClose = shadow.getElementById("search-close");
  const searchCount = shadow.getElementById("search-count");

  const searchState = { query: "", matches: [], activeIndex: -1 };

  searchBtn.addEventListener("click", () => {
    const open = !searchPanel.classList.contains("open");
    searchPanel.classList.toggle("open", open);
    searchBtn.classList.toggle("active", open);
    if (open) {
      settingsPanel.classList.remove("open");
      activateFullModeForSearch();
      setTimeout(() => searchInput.focus(), 0);
    } else {
      searchInput.value = "";
      clearSearch();
    }
  });

  searchInput.addEventListener("input", () => runSearch(searchInput.value));
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (event.shiftKey) goToSearchMatch(searchState.activeIndex - 1);
      else goToSearchMatch(searchState.activeIndex + 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeSearchPanel();
    }
  });
  searchPrev.addEventListener("click", () => goToSearchMatch(searchState.activeIndex - 1));
  searchNext.addEventListener("click", () => goToSearchMatch(searchState.activeIndex + 1));
  searchClear.addEventListener("click", () => {
    searchInput.value = "";
    clearSearch();
    searchInput.focus();
  });
  searchClose.addEventListener("click", () => closeSearchPanel());

  function closeSearchPanel() {
    searchPanel.classList.remove("open");
    searchBtn.classList.remove("active");
    searchInput.value = "";
    clearSearch();
  }

  function handleFindShortcut(event) {
    if (!host.isConnected) {
      document.removeEventListener("keydown", handleFindShortcut, true);
      return;
    }
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "f") return;
    if (document.getElementById("adhd-reader-root") !== host) return;
    event.preventDefault();
    settingsPanel.classList.remove("open");
    searchPanel.classList.add("open");
    searchBtn.classList.add("active");
    activateFullModeForSearch();
    setTimeout(() => {
      searchInput.focus();
      searchInput.select();
    }, 0);
  }
  document.addEventListener("keydown", handleFindShortcut, true);

  function activateFullModeForSearch() {
    if (shadow.getElementById("mode-full")?.classList.contains("active")) return;
    setMode("full");
  }

  function runSearch(rawQuery) {
    const query = rawQuery.trim();
    searchState.query = query;
    searchState.matches = [];
    searchState.activeIndex = -1;

    restoreParagraphSearchText();
    if (!query) {
      updateSearchControls();
      return;
    }

    activateFullModeForSearch();
    const lowered = query.toLowerCase();
    paragraphs.forEach((text, paragraphIndex) => {
      let from = 0;
      const source = text.toLowerCase();
      while (true) {
        const index = source.indexOf(lowered, from);
        if (index === -1) break;
        searchState.matches.push({ paragraphIndex, index });
        from = index + Math.max(1, lowered.length);
      }
    });

    if (searchState.matches.length > 0) searchState.activeIndex = 0;
    renderSearchHighlights();
    updateSearchControls();
    scrollToActiveSearchMatch();
  }

  function clearSearch() {
    searchState.query = "";
    searchState.matches = [];
    searchState.activeIndex = -1;
    restoreParagraphSearchText();
    updateSearchControls();
  }

  function restoreParagraphSearchText() {
    shadow.querySelectorAll(".article-paragraph").forEach(p => {
      const idx = Number(p.dataset.index);
      if (Number.isInteger(idx) && paragraphs[idx] !== undefined) p.textContent = paragraphs[idx];
    });
  }

  function renderSearchHighlights() {
    const query = searchState.query;
    if (!query) return;
    const paras = shadow.querySelectorAll(".article-paragraph");
    let globalMatchIndex = 0;

    paras.forEach(p => {
      const idx = Number(p.dataset.index);
      const text = Number.isInteger(idx) ? paragraphs[idx] : p.textContent;
      const paragraphMatches = searchState.matches.filter(m => m.paragraphIndex === idx);
      if (!paragraphMatches.length) {
        p.textContent = text;
        return;
      }

      let html = "";
      let cursor = 0;
      paragraphMatches.forEach(match => {
        const start = match.index;
        const end = start + query.length;
        html += escHtml(text.slice(cursor, start));
        const currentClass = globalMatchIndex === searchState.activeIndex ? " current" : "";
        html += `<mark class="reader-search-mark${currentClass}" data-search-index="${globalMatchIndex}">${escHtml(text.slice(start, end))}</mark>`;
        cursor = end;
        globalMatchIndex++;
      });
      html += escHtml(text.slice(cursor));
      p.innerHTML = html;
    });
  }

  function goToSearchMatch(nextIndex) {
    if (!searchState.matches.length) return;
    const total = searchState.matches.length;
    searchState.activeIndex = ((nextIndex % total) + total) % total;
    restoreParagraphSearchText();
    renderSearchHighlights();
    updateSearchControls();
    scrollToActiveSearchMatch();
  }

  function scrollToActiveSearchMatch() {
    if (searchState.activeIndex < 0) return;
    const mark = shadow.querySelector(`.reader-search-mark[data-search-index="${searchState.activeIndex}"]`);
    if (mark) mark.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function updateSearchControls() {
    const total = searchState.matches.length;
    searchCount.textContent = total ? `${searchState.activeIndex + 1} / ${total}` : "0 / 0";
    searchPrev.disabled = total === 0;
    searchNext.disabled = total === 0;
  }

  chrome.storage.sync.get(["fontSize", "lineHeight", "theme"], (saved) => {
    const fs = saved.fontSize   || 18;
    const lh = saved.lineHeight || 1.8;
    applyFontSize(fs); applyLineHeight(lh);
    fontSlider.value = fs; lineSlider.value = lh;
    if (saved.theme) applyTheme(saved.theme);
  });

  function applyFontSize(val)   { overlay.style.setProperty("--reader-font-size", val + "px"); fontValue.textContent = val + "px"; }
  function applyLineHeight(val) { overlay.style.setProperty("--reader-line-height", val); lineValue.textContent = parseFloat(val).toFixed(1); }

  fontSlider.addEventListener("input", () => { applyFontSize(fontSlider.value); chrome.storage.sync.set({ fontSize: Number(fontSlider.value) }); });
  lineSlider.addEventListener("input", () => { applyLineHeight(lineSlider.value); chrome.storage.sync.set({ lineHeight: Number(lineSlider.value) }); });
  settingsBtn.addEventListener("click", () => {
    const open = !settingsPanel.classList.contains("open");
    settingsPanel.classList.toggle("open", open);
    if (open) {
      searchPanel.classList.remove("open");
      searchBtn.classList.remove("active");
      searchInput.value = "";
      clearSearch();
    }
  });

  shadow.getElementById("reset-defaults").addEventListener("click", () => {
    applyFontSize(18); applyLineHeight(1.8);
    fontSlider.value = 18; lineSlider.value = 1.8;
    applyTheme("warm");
    chrome.storage.sync.set({ fontSize: 18, lineHeight: 1.8, theme: "warm" });
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
    const tb = shadow.getElementById("reader-toolbar");
    tb.style.background = t.toolbar; tb.style.borderColor = t.border;
    const hd = shadow.getElementById("reader-header");
    if (hd) hd.style.background = t.toolbar;
    const sp = shadow.getElementById("settings-panel");
    sp.style.background = dark ? "#242424" : name === "green" ? "#e8f3e8" : "#f7f4ef";
    sp.style.color = dark ? "#e8e6e0" : "#555";
    sp.style.borderColor = t.border;
    const search = shadow.getElementById("search-panel");
    if (search) {
      search.style.background = dark ? "#242424" : name === "green" ? "#e8f3e8" : "#f7f4ef";
      search.style.borderColor = t.border;
    }
    const btnBg = dark ? "#2e2e2e" : "white", btnColor = dark ? "#e8e6e0" : "#444",
          btnBorder = dark ? "#4a4a4a" : "#d0ccc4", btnActive = dark ? "#444" : "#e8e0d4";
    shadow.querySelectorAll(".toolbar-btn").forEach(btn => {
      btn.style.background  = btn.classList.contains("active") ? btnActive : btnBg;
      btn.style.color       = btnColor;
      btn.style.borderColor = btnBorder;
    });
    const cb = shadow.getElementById("reader-close");
    cb.style.color = dark ? "#aaa" : "#888"; cb.style.borderColor = btnBorder;
    shadow.querySelectorAll(".search-btn").forEach(btn => {
      btn.style.background = btnBg;
      btn.style.color = btnColor;
      btn.style.borderColor = btnBorder;
    });
    const searchInputEl = shadow.getElementById("reader-search-input");
    if (searchInputEl) {
      searchInputEl.style.background = dark ? "#2e2e2e" : "white";
      searchInputEl.style.color = dark ? "#e8e6e0" : "#222";
      searchInputEl.style.borderColor = btnBorder;
    }
    const searchCountEl = shadow.getElementById("search-count");
    if (searchCountEl) searchCountEl.style.color = dark ? "#aaa" : "#777";
    const titleElInner = shadow.getElementById("article-title");
    if (titleElInner) titleElInner.style.color = dark ? "#f0eeea" : "#111";
    const bylineElInner = shadow.getElementById("article-byline");
    if (bylineElInner) bylineElInner.style.color = dark ? "#888" : "#888";
    ["warm","white","green","dark"].forEach(n => {
      const sw = shadow.getElementById("theme-" + n);
      sw.classList.toggle("active", n === name);
      sw.style.borderColor = n === name ? (dark && n === "dark" ? "#aaa" : "#333") : "#ccc";
    });
    chrome.storage.sync.set({ theme: name });
    shadow.querySelectorAll(".nav-btn").forEach(btn => {
      if (btn.classList.contains("primary")) {
        btn.style.background = dark ? "#e8e6e0" : "#1a1a1a";
        btn.style.color = dark ? "#1a1a1a" : "white";
        btn.style.borderColor = dark ? "#e8e6e0" : "#1a1a1a";
      } else {
        btn.style.background = btnBg; btn.style.color = btnColor; btn.style.borderColor = btnBorder;
      }
    });
  }
  ["warm","white","green","dark"].forEach(name => {
    shadow.getElementById("theme-" + name).addEventListener("click", () => applyTheme(name));
  });

  // ── Mode switcher ─────────────────────────────────────────────────────
  let studyActive = true;

  function setMode(mode) {
    shadow.getElementById("mode-full").classList.toggle("active",     mode === "full");
    shadow.getElementById("mode-para").classList.toggle("active",     mode === "para");
    shadow.getElementById("mode-sentence").classList.toggle("active", mode === "sentence");
    if (mode === "study") {
      if (studyActive) { deactivateStudy(); return; }
      studyActive = true;
      shadow.getElementById("mode-full").classList.add("active");
      if (window.__readerModes) window.__readerModes.setMode("full");
      if (window.__readerPhases) window.__readerPhases.activate("full");
    } else {
      if (window.__readerModes) window.__readerModes.setMode(mode);
      if (studyActive && window.__readerPhases) window.__readerPhases.setReadingMode(mode);
    }
  }

  function deactivateStudy() {
    if (window.__readerPhases) window.__readerPhases.deactivate(() => finishDeactivateStudy());
    else finishDeactivateStudy();
  }

  function finishDeactivateStudy() {
    studyActive = false;
    shadow.getElementById("mode-full").classList.add("active");
    ["mode-para","mode-sentence"].forEach(id => shadow.getElementById(id).classList.remove("active"));
    if (window.__readerModes) window.__readerModes.setMode("full");
  }

  window.__readerState = { shadow, paragraphs, contentBlocks, content };
  window.__readerSetMode = setMode;

  shadow.getElementById("mode-full").addEventListener("click",     () => setMode("full"));
  shadow.getElementById("mode-para").addEventListener("click",     () => setMode("para"));
  shadow.getElementById("mode-sentence").addEventListener("click", () => setMode("sentence"));
  setMode("full");

  function escHtml(str) {
    return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  console.log(`[ADHD Reader] Stage 3 ready — ${paragraphs.length} paragraphs.`);

})();
