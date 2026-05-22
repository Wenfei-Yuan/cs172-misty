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
