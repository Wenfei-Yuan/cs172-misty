// phases.js — Stage 4 (revised)
// Skim/thorough phases with end-of-session check-in screen.
// No inline interruptions — questions appear only when exiting Study mode.

(function () {

  const state = window.__readerState;
  if (!state) { console.error("[ADHD Reader] phases.js: no __readerState"); return; }

  const { shadow, paragraphs } = state;
  const overlay = shadow.getElementById("reader-overlay");

  let currentPhase = "skim";
  let isActive = false;

  // ── Styles ────────────────────────────────────────────────────────────
  const styleEl = document.createElement("style");
  styleEl.textContent = `
    #phase-pill {
      display: none;
      align-items: center;
      gap: 8px;
    }
    #phase-pill.visible { display: flex; }

    #phase-pill-label {
      font-size: 13px;
      font-weight: 600;
      padding: 5px 12px;
      border-radius: 20px;
      white-space: nowrap;
      letter-spacing: 0.02em;
    }
    #phase-pill.skim     #phase-pill-label { background: #fdf0c4; color: #7a6030; }
    #phase-pill.thorough #phase-pill-label { background: #d4ecc4; color: #2e5e2e; }

    #phase-pill-btn {
      background: none;
      border: 1px solid #d0ccc4;
      border-radius: 20px;
      font-size: 13px;
      cursor: pointer;
      color: #888;
      font-family: inherit;
      white-space: nowrap;
      padding: 4px 10px;
      transition: all 0.15s;
    }
    #phase-pill-btn:hover { color: #333; border-color: #aaa; background: #f5f2ed; }

    .para-dim { opacity: 0.2; transition: opacity 0.3s; }
    .para-dim:hover { opacity: 0.65; }

    /* Floating notes button */
    #notes-fab {
      display: none;
      position: fixed;
      bottom: 28px;
      right: 28px;
      z-index: 60;
      background: #2a2a2a;
      color: white;
      border: none;
      border-radius: 24px;
      padding: 10px 18px;
      font-size: 13px;
      font-family: inherit;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,0.18);
      transition: background 0.15s, transform 0.15s;
    }
    #notes-fab.visible { display: block; }
    #notes-fab:hover { background: #444; transform: translateY(-1px); }

    /* Notes side panel */
    #notes-panel {
      display: none;
      position: fixed;
      top: 0;
      right: 0;
      bottom: 0;
      width: 300px;
      background: #fffef9;
      border-left: 1px solid #e8e4dc;
      z-index: 55;
      flex-direction: column;
      box-shadow: -4px 0 20px rgba(0,0,0,0.10);
    }
    #notes-panel.visible { display: flex; }
    #notes-panel-header {
      padding: 12px 16px;
      border-bottom: 1px solid #e8e4dc;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      font-weight: 600;
      color: #333;
      flex-shrink: 0;
    }
    #notes-panel-title { flex: 1; }
    #notes-panel-close {
      background: none;
      border: none;
      cursor: pointer;
      font-size: 16px;
      color: #aaa;
      padding: 2px 6px;
      border-radius: 4px;
    }
    #notes-panel-close:hover { background: #fee2e2; color: #b91c1c; }

    /* Paragraph context strip */
    #notes-para-context {
      display: none;
      padding: 8px 16px;
      font-size: 11px;
      color: #888;
      background: #f7f4ef;
      border-bottom: 1px solid #e8e4dc;
      font-style: italic;
      line-height: 1.4;
      flex-shrink: 0;
    }
    #notes-para-context.visible { display: block; }
    #notes-para-context strong { color: #555; font-style: normal; }

    /* Saved notes list */
    #notes-list {
      flex: 1;
      overflow-y: auto;
      padding: 8px 0;
    }
    .note-item {
      padding: 10px 16px;
      border-bottom: 1px solid #f0ede8;
      font-size: 13px;
      line-height: 1.5;
    }
    .note-item-label {
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: #aaa;
      margin-bottom: 3px;
    }
    .note-item-text { color: #333; }
    .note-item-para {
      font-size: 11px;
      color: #5a7a3a;
      margin-top: 3px;
      font-style: italic;
    }
    #notes-empty {
      padding: 24px 16px;
      font-size: 13px;
      color: #bbb;
      text-align: center;
      font-style: italic;
    }

    /* Input area */
    #notes-input-area {
      border-top: 1px solid #e8e4dc;
      flex-shrink: 0;
    }
    #notes-textarea {
      width: 100%;
      border: none;
      border-bottom: 1px solid #e8e4dc;
      padding: 10px 16px;
      font-size: 13px;
      font-family: Georgia, serif;
      line-height: 1.6;
      resize: none;
      background: #fffef9;
      color: #1a1a1a;
      outline: none;
      height: 80px;
    }
    #notes-panel-footer {
      padding: 8px 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }
    #notes-para-toggle {
      font-size: 11px;
      color: #888;
      background: none;
      border: none;
      cursor: pointer;
      padding: 0;
      font-family: inherit;
      text-decoration: underline;
      text-underline-offset: 2px;
    }
    #notes-para-toggle:hover { color: #444; }
    #notes-para-toggle.active { color: #5a7a3a; }
    .notes-btn {
      padding: 6px 14px;
      font-size: 12px;
      border-radius: 6px;
      cursor: pointer;
      font-family: inherit;
      border: 1px solid #d0ccc4;
      background: white;
      color: #555;
    }
    .notes-btn.primary { background: #2a2a2a; color: white; border-color: #2a2a2a; }
    .notes-btn:hover { opacity: 0.8; }

    /* Paragraph note indicator */
    .para-has-note {
      border-left: 3px solid #5a7a3a;
      padding-left: 8px;
      margin-left: -11px;
    }

    /* End-of-session check-in screen */
    #checkin-screen {
      display: none;
      position: absolute;
      inset: 0;
      background: rgba(0,0,0,0.35);
      overflow-y: auto;
      z-index: 50;
      align-items: center;
      justify-content: center;
      font-family: Georgia, serif;
    }
    #checkin-screen.visible { display: flex; }

    #checkin-card {
      background: #fffef9;
      border-radius: 16px;
      padding: 40px 44px;
      max-width: 480px;
      width: 90%;
      box-shadow: 0 8px 40px rgba(0,0,0,0.18);
    }
    #checkin-screen h2 {
      font-size: 1.25em;
      font-weight: 700;
      margin-bottom: 0.25em;
      color: #1a1a1a;
    }
    #checkin-screen .ci-subtitle {
      font-size: 0.85em;
      color: #999;
      margin-bottom: 1.8em;
    }
    .ci-question label {
      display: block;
      font-size: 1em;
      font-weight: 600;
      color: #222;
      margin-bottom: 12px;
    }
    .ci-question .ci-thumbs {
      display: flex;
      gap: 10px;
      margin-bottom: 14px;
    }
    .ci-thumb {
      flex: 1;
      background: #f7f4ef;
      border: 1.5px solid #e0dbd3;
      border-radius: 10px;
      font-size: 15px;
      padding: 10px 0;
      cursor: pointer;
      transition: all 0.15s;
      color: #555;
    }
    .ci-thumb:hover { background: #f0ede8; }
    .ci-thumb.selected { border-color: #5a7a3a; background: #eaf4e4; color: #2e5e2e; font-weight: 600; }
    .ci-question textarea {
      width: 100%;
      min-height: 90px;
      border: 1.5px solid #e0dbd3;
      border-radius: 10px;
      padding: 10px 14px;
      font-size: 0.95em;
      font-family: inherit;
      resize: vertical;
      background: #fafaf8;
      color: #1a1a1a;
      line-height: 1.6;
    }
    .ci-question textarea:focus { outline: none; border-color: #5a7a3a; background: white; }
    #checkin-actions {
      display: flex;
      gap: 10px;
      margin-top: 24px;
      justify-content: flex-end;
    }
    .ci-btn {
      padding: 9px 22px;
      font-size: 14px;
      border-radius: 8px;
      cursor: pointer;
      font-family: inherit;
      border: 1.5px solid #d0ccc4;
      background: white;
      color: #666;
    }
    .ci-btn.primary {
      background: #2a2a2a;
      color: white;
      border-color: #2a2a2a;
    }
    .ci-btn:hover { opacity: 0.85; }
  `;
  shadow.appendChild(styleEl);

  // ── Phase pill (tiny status in toolbar) ───────────────────────────────
  const pill = document.createElement("div");
  pill.id = "phase-pill";
  pill.innerHTML = `
    <span id="phase-pill-label">Skim</span>
    <button id="phase-pill-btn">→ Thorough</button>
  `;

  // Insert pill right after the Study button in the toolbar
  // Guard: if Study button doesn't exist, skip pill insertion gracefully
  const studyBtn = shadow.getElementById("mode-study");
  if (studyBtn) {
    studyBtn.after(pill);
  } else {
    console.warn("[ADHD Reader] phases.js: #mode-study not found, pill not inserted.");
  }

  const pillBtn = shadow.getElementById("phase-pill-btn");
  if (pillBtn) pillBtn.addEventListener("click", () => {
    if (currentPhase === "skim") enterThoroughPhase();
    else enterSkimPhase();
  });

  // ── Floating notes button + panel ───────────────────────────────────
  const notesFab = document.createElement("button");
  notesFab.id = "notes-fab";
  notesFab.textContent = "✎ Notes";
  shadow.getElementById("reader-overlay").appendChild(notesFab);

  const notesPanel = document.createElement("div");
  notesPanel.id = "notes-panel";
  notesPanel.innerHTML = `
    <div id="notes-panel-header">
      <span id="notes-panel-title">Notes</span>
      <button id="notes-panel-close" title="Close">✕</button>
    </div>
    <div id="notes-para-context"></div>
    <div id="notes-list"><div id="notes-empty">No notes yet.</div></div>
    <div id="notes-input-area">
      <textarea id="notes-textarea" placeholder="Add a note..."></textarea>
      <div id="notes-panel-footer">
        <button id="notes-para-toggle">+ link to paragraph</button>
        <button class="notes-btn primary" id="notes-save">Add</button>
      </div>
    </div>
  `;
  shadow.getElementById("reader-overlay").appendChild(notesPanel);

  // Notes state
  const savedNotes = []; // { text, paraIndex, paraSnippet, ts }
  let linkedParaIndex = null;  // null = general note
  let linkedParaSnippet = null;

  function renderNotesList() {
    const list = shadow.getElementById("notes-list");
    if (savedNotes.length === 0) {
      list.innerHTML = '<div id="notes-empty">No notes yet.</div>';
      return;
    }
    list.innerHTML = "";
    savedNotes.forEach((n, i) => {
      const item = document.createElement("div");
      item.className = "note-item";
      const label = document.createElement("div");
      label.className = "note-item-label";
      label.textContent = n.paraIndex !== null ? `Para ${n.paraIndex + 1}` : "General";
      const text = document.createElement("div");
      text.className = "note-item-text";
      text.textContent = n.text;
      item.appendChild(label);
      item.appendChild(text);
      if (n.paraSnippet) {
        const para = document.createElement("div");
        para.className = "note-item-para";
        para.textContent = "“" + n.paraSnippet + "”";
        item.appendChild(para);
      }
      list.appendChild(item);
    });
  }

  function getVisibleParaIndex() {
    const paras = shadow.querySelectorAll(".article-paragraph");
    let closest = null, closestDist = Infinity;
    paras.forEach((p, i) => {
      const rect = p.getBoundingClientRect();
      const dist = Math.abs(rect.top - 120);
      if (dist < closestDist) { closestDist = dist; closest = i; }
    });
    return closest;
  }

  function updateParaContext() {
    const ctx = shadow.getElementById("notes-para-context");
    const toggle = shadow.getElementById("notes-para-toggle");
    if (linkedParaIndex !== null) {
      ctx.innerHTML = `<strong>Para ${linkedParaIndex + 1}:</strong> ${escHtml(linkedParaSnippet || "")}`;
      ctx.classList.add("visible");
      toggle.textContent = "✕ unlink paragraph";
      toggle.classList.add("active");
    } else {
      ctx.classList.remove("visible");
      toggle.textContent = "+ link to paragraph";
      toggle.classList.remove("active");
    }
  }

  notesFab.addEventListener("click", () => {
    notesPanel.classList.add("visible");
    notesFab.classList.remove("visible");
    // Auto-link to currently visible paragraph
    const idx = getVisibleParaIndex();
    if (idx !== null) {
      const paras = shadow.querySelectorAll(".article-paragraph");
      linkedParaIndex = idx;
      linkedParaSnippet = (paras[idx]?.textContent || "").slice(0, 60) + "...";
      updateParaContext();
    }
    shadow.getElementById("notes-textarea").focus();
  });

  shadow.getElementById("notes-panel-close").addEventListener("click", () => {
    notesPanel.classList.remove("visible");
    notesFab.classList.add("visible");
  });

  shadow.getElementById("notes-para-toggle").addEventListener("click", () => {
    if (linkedParaIndex !== null) {
      linkedParaIndex = null;
      linkedParaSnippet = null;
    } else {
      const idx = getVisibleParaIndex();
      if (idx !== null) {
        const paras = shadow.querySelectorAll(".article-paragraph");
        linkedParaIndex = idx;
        linkedParaSnippet = (paras[idx]?.textContent || "").slice(0, 60) + "...";
      }
    }
    updateParaContext();
  });

  shadow.getElementById("notes-save").addEventListener("click", () => {
    const ta = shadow.getElementById("notes-textarea");
    const text = ta.value.trim();
    if (!text) return;
    const note = { text, paraIndex: linkedParaIndex, paraSnippet: linkedParaSnippet, ts: Date.now() };
    savedNotes.push(note);
    renderNotesList();
    ta.value = "";
    // Mark paragraph with green bar
    if (linkedParaIndex !== null) {
      const paras = shadow.querySelectorAll(".article-paragraph");
      if (paras[linkedParaIndex]) paras[linkedParaIndex].classList.add("para-has-note");
    }
    // Reset link to current para
    const idx = getVisibleParaIndex();
    if (idx !== null) {
      const paras = shadow.querySelectorAll(".article-paragraph");
      linkedParaIndex = idx;
      linkedParaSnippet = (paras[idx]?.textContent || "").slice(0, 60) + "...";
    } else {
      linkedParaIndex = null;
      linkedParaSnippet = null;
    }
    updateParaContext();
    // Log to session
    if (typeof chrome !== "undefined" && chrome.runtime) {
      chrome.runtime.sendMessage({ type: "LOG_EVENT", event: "reading_notes", data: { notes: savedNotes } }).catch(() => {});
    }
  });

  // ── End-of-session check-in screen ───────────────────────────────────
  const checkinScreen = document.createElement("div");
  checkinScreen.id = "checkin-screen";
  shadow.getElementById("reader-overlay").appendChild(checkinScreen);

  function showCheckinScreen(onDone) {
    checkinScreen.innerHTML = "";

    const card = document.createElement("div");
    card.id = "checkin-card";
    checkinScreen.appendChild(card);

    const h2 = document.createElement("h2");
    h2.textContent = "Reading check-in";
    card.appendChild(h2);

    const sub = document.createElement("p");
    sub.className = "ci-subtitle";
    sub.textContent = "A quick reflection before you go.";
    card.appendChild(sub);

    // Single card
    const block = document.createElement("div");
    block.className = "ci-question";

    const label = document.createElement("label");
    label.textContent = "How did the reading go overall?";
    block.appendChild(label);

    // Thumbs
    let thumbValue = null;
    const thumbsRow = document.createElement("div");
    thumbsRow.className = "ci-thumbs";
    ["👍 Clear", "👎 Unclear"].forEach(t => {
      const btn = document.createElement("button");
      btn.className = "ci-thumb";
      btn.textContent = t;
      btn.addEventListener("click", () => {
        thumbValue = t.startsWith("👍") ? "up" : "down";
        thumbsRow.querySelectorAll(".ci-thumb").forEach(b => b.classList.remove("selected"));
        btn.classList.add("selected");
      });
      thumbsRow.appendChild(btn);
    });
    block.appendChild(thumbsRow);

    const ta = document.createElement("textarea");
    ta.placeholder = "Any thoughts or notes? (optional)";
    block.appendChild(ta);
    card.appendChild(block);

    const actions = document.createElement("div");
    actions.id = "checkin-actions";

    const skipBtn = document.createElement("button");
    skipBtn.className = "ci-btn";
    skipBtn.textContent = "Skip";
    skipBtn.addEventListener("click", () => {
      checkinScreen.classList.remove("visible");
      onDone([]);
    });

    const doneBtn = document.createElement("button");
    doneBtn.className = "ci-btn primary";
    doneBtn.textContent = "Done";
    doneBtn.addEventListener("click", () => {
      const notes = ta.value.trim();
      const result = { question: "How did the reading go overall?", thumb: thumbValue, notes, wroteNote: notes.length > 0 };
      if (typeof chrome !== "undefined" && chrome.runtime) {
        chrome.runtime.sendMessage({ type: "LOG_EVENT", event: "selfcheck_answer", data: { answers: [result] } })
          .catch(() => {});
      }
      checkinScreen.classList.remove("visible");
      onDone([result]);
    });

    actions.appendChild(skipBtn);
    actions.appendChild(doneBtn);
    card.appendChild(actions);
    checkinScreen.classList.add("visible");
  }

  // ── Public API ────────────────────────────────────────────────────────
  function activate(readingMode) {
    isActive = true;
    currentPhase = "skim";
    notesFab.classList.add("visible");
    setReadingMode(readingMode || "full");
  }

  // Show/hide skim pill depending on which reading mode is active
  function setReadingMode(mode) {
    if (mode === "full") {
      // Full mode: show skim pill and apply dimming
      pill.className = "phase-pill " + currentPhase + " visible";
      if (currentPhase === "skim") applySkimDimming();
    } else {
      // Para or Sent: hide skim pill, remove dimming (they already see one chunk)
      pill.className = "phase-pill"; // hidden
      removeSkimDimming();
    }
  }

  function deactivate(onDone) {
    notesFab.classList.remove("visible");
    notesPanel.classList.remove("visible");
    showCheckinScreen((results) => {
      isActive = false;
      pill.className = "phase-pill";
      pill.classList.remove("visible");
      removeSkimDimming();
      if (onDone) onDone(results);
    });
  }

  // ── Skim phase ────────────────────────────────────────────────────────
  function enterSkimPhase() {
    currentPhase = "skim";
    pill.className = "phase-pill skim visible";
    shadow.getElementById("phase-pill-label").textContent = "Skim";
    shadow.getElementById("phase-pill-btn").textContent = "→ Thorough";
    applySkimDimming();
  }

  function enterThoroughPhase() {
    currentPhase = "thorough";
    pill.className = "phase-pill thorough visible";
    shadow.getElementById("phase-pill-label").textContent = "Thorough";
    shadow.getElementById("phase-pill-btn").textContent = "← Skim";
    removeSkimDimming();
    if (overlay) overlay.scrollTop = 0;
  }

  // ── Skim dimming ──────────────────────────────────────────────────────
  function applySkimDimming() {
    const paras = shadow.querySelectorAll(".article-paragraph");
    paras.forEach(p => {
      const text = p.textContent.trim();
      const firstSentEnd = text.search(/(?<=[.!?])\s+[A-Z"']/);
      if (firstSentEnd === -1) { p.innerHTML = escHtml(text); return; }
      const first = text.slice(0, firstSentEnd + 1);
      const rest  = text.slice(firstSentEnd + 1);
      p.innerHTML = escHtml(first) + `<span class="para-dim"> ${escHtml(rest)}</span>`;
    });
  }

  function removeSkimDimming() {
    const paras = shadow.querySelectorAll(".article-paragraph");
    paras.forEach((p, i) => {
      if (paragraphs[i] !== undefined) p.textContent = paragraphs[i];
    });
  }

  function escHtml(str) {
    return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  window.__readerPhases = { activate, deactivate, setReadingMode };
  console.log("[ADHD Reader] phases.js ready.");

})();
