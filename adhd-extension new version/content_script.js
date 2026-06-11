// content_script.js — Stage 2
// Extracts article with Readability, mounts shadow DOM, then loads reader.js.

(function () {

  // ── Guard: remove any existing overlay and start fresh ─────────────────
  // (handles re-clicks and stale overlays from previous sessions)
  const existing = document.getElementById("adhd-reader-root");
  if (existing) existing.remove();

  // ── Unsupported page types ───────────────────────────────────────────────
  const url = window.location.href;
  const unsupported = [
    { match: /docs\.google\.com/,   name: "Google Docs" },
    { match: /sheets\.google\.com/, name: "Google Sheets" },
    { match: /slides\.google\.com/, name: "Google Slides" },
    { match: /figma\.com/,          name: "Figma" },
  ];
  const blocked = unsupported.find(u => u.match.test(url));
  if (blocked) {
    showUnsupportedMessage(
      `${blocked.name} isn't supported`,
      `${blocked.name} renders content on a canvas rather than as readable HTML, so text extraction doesn't work here.`,
      "Try opening the document as a web page, or copy-paste the text into a plain webpage."
    );
    return;
  }

  // ── Extract article ─────────────────────────────────────────────────────
  // First attempt: standard Readability parse
  let article = null;
  try {
    const documentClone = document.cloneNode(true);
    article = new Readability(documentClone).parse();
  } catch(e) {
    console.warn("[ADHD Reader] Readability threw:", e);
  }

  // If Readability failed or returned very little text, try a fallback:
  // find the element with the most text on the page and use that.
  const MIN_CHARS = 300;
  if (!article || (article.textContent || "").trim().length < MIN_CHARS) {
    console.warn("[ADHD Reader] Readability result too short, trying fallback extraction.");
    article = fallbackExtract() || article;
  }

  if (!article || (article.textContent || "").trim().length < MIN_CHARS) {
    console.warn("[ADHD Reader] Could not extract enough content.");
    showUnsupportedMessage(
      "Couldn't find article content",
      "This page doesn't appear to contain a readable article.",
      "Try a news article, Wikipedia page, or academic reading."
    );
    return;
  }

  // ── Fallback extractor ─────────────────────────────────────────────────
  // Finds the DOM element with the most text content and uses that.
  // Works well on pages with non-standard article structure.
  function fallbackExtract() {
    const candidates = Array.from(
      document.querySelectorAll("article, main, [role='main'], .content, .article, .post, .entry, #content, #main")
    );

    // Also consider large divs — find the one with the most text
    document.querySelectorAll("div, section").forEach(el => {
      const text = el.innerText || "";
      if (text.length > 500) candidates.push(el);
    });

    if (candidates.length === 0) return null;

    // Pick the element with the most text
    const best = candidates.reduce((a, b) =>
      (a.innerText || "").length > (b.innerText || "").length ? a : b
    );

    const text = best.innerText || best.textContent || "";
    if (text.trim().length < MIN_CHARS) return null;

    return {
      title:       document.title || "",
      byline:      null,
      content:     best.innerHTML,
      textContent: text,
      length:      text.length,
      excerpt:     text.slice(0, 200),
    };
  }

  // ── Helper: friendly unsupported message ─────────────────────────────────
  function showUnsupportedMessage(title, reason, suggestion) {
    const host = document.createElement("div");
    host.id = "adhd-reader-root";
    host.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:auto;";
    document.body.appendChild(host);
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        #msg {
          position: absolute; inset: 0;
          background: #fffef9;
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          font-family: Georgia, serif; color: #1a1a1a;
          text-align: center; padding: 40px;
        }
        h2 { font-size: 1.3rem; margin-bottom: 0.6em; }
        p  { font-size: 1rem; color: #666; margin-bottom: 0.5em; max-width: 420px; }
        .tip { font-size: 0.85rem; color: #999; margin-top: 0.5em; font-style: italic; max-width: 420px; }
        button { margin-top: 1.5em; padding: 7px 20px; border: 1px solid #ccc;
                 border-radius: 6px; background: white; cursor: pointer; font-size: 0.9rem; }
        button:hover { background: #f0ede8; }
      </style>
      <div id="msg">
        <h2>${title}</h2>
        <p>${reason}</p>
        <p class="tip">${suggestion}</p>
        <button id="close">Close</button>
      </div>
    `;
    shadow.getElementById("close").addEventListener("click", () => host.remove());
  }

  // ── Try to find hero image from the original page ─────────────────────
  // The featured/hero image is usually outside the article body,
  // so Readability misses it. We grab it from the original DOM and prepend it.
  function findHeroImage() {
    // 1. og:image meta tag (most reliable)
    const og = document.querySelector('meta[property="og:image"]');
    if (og?.content) return og.content;

    // 2. Common hero image selectors
    const heroSelectors = [
      ".hero img", ".hero-image img", ".featured-image img",
      ".post-thumbnail img", ".article-hero img", ".article-image img",
      ".article__image img", ".entry-image img", ".story-image img",
      ".header-image img", "[class*='hero'] img", "[class*='featured'] img",
      "[class*='banner'] img",
    ];
    for (const sel of heroSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        const src = el.src || el.getAttribute("data-src") || el.getAttribute("data-lazy-src") || el.getAttribute("data-original");
        if (src && !src.startsWith("data:")) return new URL(src, document.baseURI).href;
      }
    }

    // 3. First large image above the article content
    const imgs = Array.from(document.querySelectorAll("img"));
    for (const img of imgs) {
      const src = img.src || img.getAttribute("data-src") || "";
      if (!src || src.startsWith("data:")) continue;
      if ((img.naturalWidth || img.width) < 200) continue; // skip small icons
      return new URL(src, document.baseURI).href;
    }
    return null;
  }

  const heroSrc = findHeroImage();
  if (heroSrc && article.content) {
    // Robust dedup: compare by URL pathname to handle CDN variants, query strings, etc.
    let heroPathname = "";
    try { heroPathname = new URL(heroSrc).pathname; } catch { heroPathname = heroSrc; }

    const tempParser = document.createElement("div");
    tempParser.innerHTML = article.content;
    const existingImgs = Array.from(tempParser.querySelectorAll("img"));
    const alreadyPresent = existingImgs.some(img => {
      const src = img.getAttribute("src") || img.getAttribute("data-src") ||
                  img.getAttribute("data-lazy-src") || img.getAttribute("data-original") || "";
      if (!src) return false;
      try {
        const p = new URL(src, document.baseURI).pathname;
        return p === heroPathname;
      } catch { return src === heroSrc; }
    });

    if (!alreadyPresent) {
      const heroHtml = `<figure><img src="${heroSrc}" alt="Article hero image"/></figure>`;
      article.content = heroHtml + article.content;
    }
  }

  if (article.content && articleFigureCount(article.content) < 2) {
    article.content = insertArticleFigures(article.content, document.body.innerText || article.textContent || "");
  }
  if (article.content && !articleHasTable(article.content)) {
    article.content = insertArticleTables(article.content, document.body.innerText || article.textContent || "");
  }

  function articleFigureCount(html) {
    const temp = document.createElement("div");
    temp.innerHTML = html || "";
    return Array.from(getExistingImageKeys(temp)).filter(key => key.startsWith("figure-")).length;
  }

  function collectArticleFigures(existingHtml = "") {
    const figureUrls = new Map();
    const existing = document.createElement("div");
    existing.innerHTML = existingHtml || "";
    const existingKeys = getExistingImageKeys(existing);
    const imageUrlPattern = /\.(avif|gif|jpe?g|png|svg|webp)(\?|#|$)/i;
    const likelyArticleImagePattern = /(\/files\/Articles\/|\/xml-images\/|\/image_m\/|\/article-image\/)/i;

    function addUrl(rawUrl, labelSource = "", sourceEl = null) {
      if (!rawUrl || rawUrl.startsWith("data:")) return;
      let url;
      try { url = new URL(rawUrl, document.baseURI); } catch { return; }
      if (!imageUrlPattern.test(url.pathname) && !likelyArticleImagePattern.test(url.href)) return;

      const figureNo = inferFigureNumber(url.href);
      if (!figureNo) return;
      const key = `figure-${figureNo}`;
      if (existingKeys.has(key)) return;
      if (figureUrls.has(key)) return;
      const inferred = `Figure ${figureNo}`;
      figureUrls.set(key, { src: url.href, label: inferred, caption: findFigureCaption(sourceEl, figureNo) });
    }

    document.querySelectorAll("a[href]").forEach(a => {
      const text = (a.textContent || "").trim().replace(/\s+/g, " ");
      addUrl(a.getAttribute("href"), text, a);
    });

    document.querySelectorAll("img, source").forEach(el => {
      ["currentSrc", "src", "data-src", "data-lazy-src", "data-original", "srcset", "data-srcset"].forEach(attr => {
        const value = attr === "currentSrc" ? el.currentSrc : el.getAttribute(attr);
        if (!value) return;
        value.split(",").forEach(part => addUrl(part.trim().split(/\s+/)[0], el.getAttribute("alt") || "", el));
      });
    });

    const figures = Array.from(figureUrls.values())
      .filter(item => item.src !== heroSrc)
      .sort((a, b) => figureSortKey(a.src) - figureSortKey(b.src));

    return figures;
  }

  function insertArticleFigures(html, articleText = "") {
    const figures = collectArticleFigures(html);
    if (figures.length === 0) return html;

    const temp = document.createElement("div");
    temp.innerHTML = html;
    const unplaced = [];
    const textCaptions = extractFigureCaptionsFromText(articleText);
    const textAnchors = extractLabelAnchorsFromText(articleText, "FIGURE");

    figures.forEach(item => {
      const figureNo = inferFigureNumber(item.src);
      item.caption = textCaptions.get(figureNo) || getExtractedFigureCaption(temp, figureNo) || item.caption;
      const figHtml = figureHtml(item);
      if (!figureNo || !insertAfterFigureMarker(temp, figureNo, figHtml, textAnchors.get(figureNo))) {
        unplaced.push(item);
      }
    });

    if (unplaced.length > 0) {
      const section = document.createElement("section");
      section.setAttribute("data-reader-figures", "true");
      section.innerHTML = `<h2>Figures</h2>${unplaced.map(figureHtml).join("")}`;
      temp.appendChild(section);
    }

    return temp.innerHTML;
  }

  function insertAfterFigureMarker(root, figureNo, figHtml, anchorText = "") {
    const target = findExactLabelTarget(root, "FIGURE", figureNo)
      || findReferenceTarget(root, "Figure", figureNo)
      || findTextAnchorTarget(root, anchorText);
    if (!target) return false;
    insertHtmlAfter(target, figHtml);
    return true;
  }

  function findExactLabelTarget(root, label, number) {
    return Array.from(root.querySelectorAll("p, h1, h2, h3, h4, h5, h6, div"))
      .find(el => {
        const text = (el.textContent || "").trim();
        if (/view in article/i.test(text)) return false;
        const mentions = text.match(new RegExp(`\\b${label}\\s+\\d+\\b`, "gi")) || [];
        return mentions.length === 1 && new RegExp(`^\\s*${label}\\s+${number}\\b`, "i").test(text);
      });
  }

  function findReferenceTarget(root, label, number) {
    return Array.from(root.querySelectorAll("p, li, div"))
      .find(el => {
        const text = (el.textContent || "").trim().replace(/\s+/g, " ");
        if (text.length < 35 || text.length > 1200 || /view in article/i.test(text)) return false;
        if (el.tagName.toLowerCase() === "div" && el.querySelector("p, li, div")) return false;
        if (new RegExp(`^\\s*${label}\\s*${number}\\b`, "i").test(text)) return false;
        return referencePattern(label, number).test(text);
      });
  }

  function referencePattern(label, number) {
    const noun = /^fig/i.test(label) ? "fig(?:ure)?s?\\.?" : "tables?\\.?";
    return new RegExp(`\\b${noun}\\s*${number}[A-Za-z]?\\b`, "i");
  }

  function findTextAnchorTarget(root, anchorText) {
    const anchor = normalizeAnchorText(anchorText).split(" ").slice(-18).join(" ");
    if (anchor.length < 45) return null;
    return Array.from(root.querySelectorAll("p, li, div"))
      .find(el => {
        if (el.tagName.toLowerCase() === "div" && el.querySelector("p, li, div")) return false;
        const text = normalizeAnchorText(el.textContent || "");
        return text.includes(anchor);
      }) || null;
  }

  function extractLabelAnchorsFromText(text, label) {
    const anchors = new Map();
    const articleStart = (text || "").search(/\bAbstract\b/i);
    const articleText = articleStart >= 0 ? text.slice(articleStart) : (text || "");
    const lines = articleText.split(/\n+/).map(line => line.trim()).filter(Boolean);
    const marker = new RegExp(`^${label}\\s+(\\d+)$`, "i");

    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(marker);
      if (!match) continue;
      const number = parseInt(match[1], 10);
      for (let j = i - 1; j >= 0; j--) {
        const line = lines[j];
        if (/^(FIGURE|TABLE)\s+\d+$/i.test(line)) break;
        if (/^View in article$/i.test(line) || /^Image:?/i.test(line)) continue;
        if (line.length >= 45 && !isBadFigureCaption(line)) {
          anchors.set(number, line);
          break;
        }
      }
    }
    return anchors;
  }

  function normalizeAnchorText(text) {
    return (text || "")
      .toLowerCase()
      .replace(/[–—−]/g, "-")
      .replace(/[^a-z0-9]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function insertHtmlAfter(target, html) {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    target.insertAdjacentElement("afterend", wrapper.firstElementChild);
  }

  function figureHtml(item) {
    const caption = item.caption ? `${item.label}: ${item.caption}` : item.label;
    return `<figure data-reader-figure-fallback="true"><img src="${escAttr(item.src)}" alt="${escAttr(item.label)}"><figcaption>${escHtml(caption)}</figcaption></figure>`;
  }

  function getExtractedFigureCaption(root, figureNo) {
    if (!figureNo) return "";
    const marker = Array.from(root.querySelectorAll("p, h1, h2, h3, h4, h5, h6, div"))
      .find(el => new RegExp(`^\\s*FIGURE\\s+${figureNo}\\s*$`, "i").test((el.textContent || "").trim()));
    if (!marker) return "";

    let next = marker.nextElementSibling;
    while (next) {
      const text = (next.textContent || "").trim().replace(/\s+/g, " ");
      if (!text) { next = next.nextElementSibling; continue; }
      if (/^(FIGURE|TABLE)\s+\d+/i.test(text)) return "";
      if (isBadFigureCaption(text)) return "";
      return cleanFigureCaption(text, figureNo);
    }
    return "";
  }

  function extractFigureCaptionsFromText(text) {
    const captions = new Map();
    const articleStart = (text || "").search(/\bAbstract\b/i);
    const articleText = articleStart >= 0 ? text.slice(articleStart) : (text || "");
    const lines = articleText.split(/\n+/).map(line => line.trim()).filter(Boolean);

    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(/^FIGURE\s+(\d+)$/i);
      if (!match) continue;
      const figureNo = parseInt(match[1], 10);
      let caption = "";

      for (let j = i + 1; j < lines.length; j++) {
        const line = lines[j];
        if (/^(FIGURE|TABLE)\s+\d+$/i.test(line)) break;
        if (/^View in article$/i.test(line) || /^Image:?/i.test(line)) continue;
        if (isBadFigureCaption(line)) continue;
        caption = line;
        break;
      }

      const cleaned = cleanFigureCaption(caption, figureNo);
      if (cleaned) captions.set(figureNo, cleaned);
    }

    return captions;
  }

  function findFigureCaption(sourceEl, figureNo) {
    if (!sourceEl) return "";

    let scope = sourceEl;
    for (let i = 0; i < 6 && scope; i++, scope = scope.parentElement) {
      const text = (scope.textContent || "").trim().replace(/\s+/g, " ");
      if (text.length > 40 && /figure\s+\d+/i.test(text) && !isBadFigureCaption(text)) {
        const cleaned = cleanFigureCaption(text, figureNo);
        if (cleaned) return cleaned;
      }
      const captionEl = scope.querySelector?.("figcaption, [class*='caption' i], [class*='legend' i], [class*='description' i]");
      if (captionEl) {
        const raw = captionEl.textContent.trim().replace(/\s+/g, " ");
        if (isBadFigureCaption(raw)) return "";
        const cleaned = cleanFigureCaption(raw, figureNo);
        if (cleaned) return cleaned;
      }
    }

    if (!figureNo) return "";
    const marker = Array.from(document.querySelectorAll("p, div, section, figure"))
      .find(el => new RegExp(`\\bFIGURE\\s+${figureNo}\\b`, "i").test((el.textContent || "").trim()));
    if (!marker) return "";
    const text = (marker.textContent || "").trim().replace(/\s+/g, " ");
    if (isBadFigureCaption(text)) return "";
    return cleanFigureCaption(text, figureNo);
  }

  function isBadFigureCaption(text) {
    const compact = (text || "").replace(/\s+/g, " ").trim();
    if (!compact) return true;
    if (/view in article/i.test(compact)) return true;
    const figureMentions = compact.match(/\bFIGURE\s+\d+\b/gi) || [];
    if (figureMentions.length > 1) return true;
    if (/\bTABLE\s+\d+\b/i.test(compact) && /\bFIGURE\s+\d+\b/i.test(compact)) return true;
    return false;
  }

  function cleanFigureCaption(text, figureNo) {
    let cleaned = text || "";
    if (figureNo) cleaned = cleaned.replace(new RegExp(`^\\s*FIGURE\\s+${figureNo}\\s*`, "i"), "");
    cleaned = cleaned.replace(/^Image:?\s*/i, "").replace(/^View in article\s*/i, "").trim();
    if (!cleaned || /^figure\s+\d+$/i.test(cleaned)) return "";
    return cleaned.length > 900 ? `${cleaned.slice(0, 900).trim()}...` : cleaned;
  }

  function getExistingImageKeys(root) {
    const keys = new Set();
    root.querySelectorAll("img").forEach(img => {
      const raw = img.getAttribute("src") || img.getAttribute("data-src") || img.getAttribute("data-lazy-src") || "";
      if (!raw) return;
      let url;
      try { url = new URL(raw, document.baseURI); } catch { return; }
      if (heroSrc && url.href === heroSrc) return;
      const figureNo = inferFigureNumber(url.href);
      keys.add(figureNo ? `figure-${figureNo}` : url.pathname.replace(/[-_](large|small|thumb|thumbnail|preview)(?=\.)/i, ""));
    });
    return keys;
  }

  function inferFigureLabel(src) {
    const n = inferFigureNumber(src);
    return n ? `Figure ${n}` : "";
  }

  function inferFigureNumber(src) {
    const match = src.match(/g(\d{3,})/i);
    return match ? parseInt(match[1], 10) : null;
  }

  function figureSortKey(src) {
    return inferFigureNumber(src) || Number.MAX_SAFE_INTEGER;
  }

  function articleHasTable(html) {
    const temp = document.createElement("div");
    temp.innerHTML = html || "";
    return temp.querySelector("table") !== null;
  }

  function collectArticleTables() {
    const domTables = Array.from(document.querySelectorAll("table")).map((table, index) => {
      const text = table.textContent.trim().replace(/\s+/g, " ");
      if (text.length < 40) return null;
      const clone = table.cloneNode(true);
      clone.querySelectorAll("script, style, noscript, button, svg").forEach(el => el.remove());
      return {
        number: index + 1,
        caption: table.querySelector("caption")?.textContent.trim().replace(/\s+/g, " ") || `Table ${index + 1}`,
        html: clone.outerHTML,
      };
    }).filter(Boolean);
    return domTables.length > 0 ? domTables : collectArticleTablesFromText(document.body.innerText || "");
  }

  function collectArticleTablesFromText(text) {
    const articleStart = (text || "").search(/\bAbstract\b/i);
    const articleText = articleStart >= 0 ? text.slice(articleStart) : (text || "");
    const lines = articleText.split(/\n+/).map(line => line.trim()).filter(Boolean);
    const tables = [];

    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(/^TABLE\s+(\d+)$/i);
      if (!match) continue;
      const number = parseInt(match[1], 10);
      const collected = [];

      for (let j = i + 1; j < lines.length; j++) {
        const line = lines[j];
        if (/^(FIGURE|TABLE)\s+\d+$/i.test(line)) break;
        if (/^View in article$/i.test(line)) continue;
        if (line.length < 2) continue;
        if (findTableCaptionIndex(collected) >= 0 && isLikelySectionHeading(line)) break;
        collected.push(line);
        if (collected.length >= 32) break;
      }

      const captionIndex = findTableCaptionIndex(collected);
      const caption = captionIndex >= 0 ? cleanTableCaption(collected[captionIndex], number) : `Table ${number}`;
      const rows = captionIndex >= 0 ? collected.slice(0, captionIndex) : collected;
      const bodyRows = rows.length
        ? rows.map(row => `<tr><td>${escHtml(row)}</td></tr>`).join("")
        : `<tr><td>Open the source article to inspect this table.</td></tr>`;
      tables.push({
        number,
        caption,
        html: `<table><tbody>${bodyRows}</tbody></table>`,
      });
    }

    return tables;
  }

  function findTableCaptionIndex(lines) {
    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i];
      if (line.length <= 180 && /\.$/.test(line) && !/\b(e\.g\.|i\.e\.)/i.test(line)) return i;
    }
    return -1;
  }

  function isLikelySectionHeading(line) {
    const cleaned = (line || "").trim();
    if (cleaned.length < 4 || cleaned.length > 90) return false;
    if (/[.;:!?]$/.test(cleaned)) return false;
    return /^(abstract|introduction|discussion|conclusions?|references|the sense of agency|human[-–]computer|our approach|body augmentation|action augmentation|outcome augmentation|author contributions|funding|conflict of interest|publisher)/i.test(cleaned);
  }

  function cleanTableCaption(text, tableNo) {
    let cleaned = (text || "").replace(/^View in article\s*/i, "").trim();
    if (tableNo) cleaned = cleaned.replace(new RegExp(`^\\s*TABLE\\s+${tableNo}\\s*`, "i"), "").trim();
    return cleaned;
  }

  function insertArticleTables(html, articleText = "") {
    const tables = collectArticleTables();
    if (tables.length === 0) return html;

    const temp = document.createElement("div");
    temp.innerHTML = html;
    const unplaced = [];
    const textAnchors = extractLabelAnchorsFromText(articleText, "TABLE");

    tables.forEach(table => {
      const tableHtml = `<figure data-reader-table-fallback="true"><figcaption>${escHtml(table.caption)}</figcaption>${table.html}</figure>`;
      if (!insertAfterTableMarker(temp, table.number, tableHtml, textAnchors.get(table.number))) unplaced.push(tableHtml);
    });

    if (unplaced.length > 0) {
      const section = document.createElement("section");
      section.setAttribute("data-reader-tables", "true");
      section.innerHTML = `<h2>Tables</h2>${unplaced.join("")}`;
      temp.appendChild(section);
    }

    return temp.innerHTML;
  }

  function insertAfterTableMarker(root, tableNo, tableHtml, anchorText = "") {
    const target = findExactLabelTarget(root, "TABLE", tableNo)
      || findReferenceTarget(root, "Table", tableNo)
      || findTextAnchorTarget(root, anchorText);
    if (!target) return false;
    insertHtmlAfter(target, tableHtml);
    return true;
  }

  function escHtml(str) {
    return (str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function escAttr(str) {
    return escHtml(str).replace(/"/g, "&quot;");
  }

  console.group("[ADHD Reader] Extraction result");
  console.log("Title:   ", article.title);
  console.log("Byline:  ", article.byline);
  console.log("Length:  ", article.length, "chars");
  console.log("Excerpt: ", article.excerpt);
  console.log("Preview: ", (article.textContent || "").slice(0, 300) + "...");
  console.log("Page body total chars:", document.body.innerText.length);
  console.groupEnd();

  // ── Mount shadow DOM overlay ────────────────────────────────────────────
  const host = document.createElement("div");
  host.id = "adhd-reader-root";
  host.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:auto;";
  document.body.appendChild(host);
  host.attachShadow({ mode: "open" });

  // ── Attach article data for reader.js ──────────────────────────────────
  host.__articleData = {
    title:       article.title,
    byline:      article.byline,
    content:     article.content,
    textContent: article.textContent,
    length:      article.length,
    excerpt:     article.excerpt,
  };

  console.log("[ADHD Reader] content_script done — host ready for reader.js");
  return true; // signal success to background.js

})();
