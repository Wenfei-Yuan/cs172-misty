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

  // ── Thorough focus state ──────────────────────────────────────────────
  let thoroughFocusIndex     = 0;
  let thoroughScrollHandler  = null;
  let thoroughKeyHandler     = null;
  let thoroughScrollThrottle = null;

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

    /* Used by skim mode (spans inside paragraphs) */
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

    #notes-list { flex: 1; overflow-y: auto; padding: 8px 0; }
    .note-item { padding: 10px 16px; border-bottom: 1px solid #f0ede8; font-size: 13px; line-height: 1.5; }
    .note-item-label { font-size: 10px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: #aaa; margin-bottom: 3px; }
    .note-item-text { color: #333; }
    .note-item-para { font-size: 11px; color: #5a7a3a; margin-top: 3px; font-style: italic; }
    #notes-empty { padding: 24px 16px; font-size: 13px; color: #bbb; text-align: center; font-style: italic; }

    #notes-input-area { border-top: 1px solid #e8e4dc; flex-shrink: 0; }
    #notes-textarea { width: 100%; border: none; border-bottom: 1px solid #e8e4dc; padding: 10px 16px; font-size: 13px; font-family: Georgia, serif; line-height: 1.6; resize: none; background: #fffef9; color: #1a1a1a; outline: none; height: 80px; }
    #notes-panel-footer { padding: 8px 16px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    #notes-para-toggle { font-size: 11px; color: #888; background: none; border: none; cursor: pointer; padding: 0; font-family: inherit; text-decoration: underline; text-underline-offset: 2px; }
    #notes-para-toggle:hover { color: #444; }
    #notes-para-toggle.active { color: #5a7a3a; }
    .notes-btn { padding: 6px 14px; font-size: 12px; border-radius: 6px; cursor: pointer; font-family: inherit; border: 1px solid #d0ccc4; background: white; color: #555; }
    .notes-btn.primary { background: #2a2a2a; color: white; border-color: #2a2a2a; }
    .notes-btn:hover { opacity: 0.8; }

    .para-has-note { border-left: 3px solid #5a7a3a; padding-left: 8px; margin-left: -11px; background: rgba(90,122,58,0.08); border-radius: 0 4px 4px 0; }

    .inline-note-card {
      background: #f0f7f0;
      border-left: 3px solid #5a7a3a;
      border-radius: 0 8px 8px 0;
      padding: 8px 30px 8px 12px;
      margin: -6px 0 18px 0;
      font-size: 12.5px;
      color: #1e4d1e;
      line-height: 1.55;
      font-family: inherit;
      animation: noteSlideIn 0.22s ease;
      position: relative;
    }
    .inline-note-card .note-card-label {
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: #3d6b2a;
      margin-bottom: 3px;
    }
    .inline-note-card .note-card-text { color: #1e4d1e; }
    .inline-note-card .note-card-delete {
      position: absolute;
      top: 7px; right: 8px;
      background: none; border: none;
      font-size: 11px; color: #5a7a3a;
      cursor: pointer; padding: 1px 3px;
      border-radius: 3px; line-height: 1;
      opacity: 0.45; transition: opacity 0.15s, color 0.15s;
    }
    .inline-note-card .note-card-delete:hover { opacity: 1; color: #b91c1c; }
    @keyframes noteSlideIn {
      from { opacity: 0; transform: translateY(-4px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    #checkin-screen {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.35);
      overflow-y: auto;
      z-index: 90;
      align-items: center;
      justify-content: center;
      font-family: Georgia, serif;
      font-size: 16px;
      line-height: 1.5;
      padding: 24px;
    }
    #checkin-screen.visible { display: flex; }
    #checkin-card {
      background: #fffef9;
      border-radius: 12px;
      padding: 32px 36px;
      max-width: 480px;
      width: min(480px, 100%);
      max-height: calc(100vh - 48px);
      overflow-y: auto;
      box-shadow: 0 8px 40px rgba(0,0,0,0.18);
    }
    #checkin-screen h2 { font-size: 20px; line-height: 1.25; font-weight: 700; margin-bottom: 4px; color: #1a1a1a; }
    #checkin-screen .ci-subtitle { font-size: 14px; line-height: 1.4; color: #777; margin-bottom: 24px; }
    .ci-question label { display: block; font-size: 16px; line-height: 1.4; font-weight: 600; color: #222; margin-bottom: 12px; }
    .ci-question .ci-thumbs { display: flex; gap: 10px; margin-bottom: 14px; }
    .ci-thumb { flex: 1 1 0; min-width: 0; background: #f7f4ef; border: 1.5px solid #e0dbd3; border-radius: 8px; font-size: 15px; line-height: 1.2; padding: 10px 12px; cursor: pointer; transition: all 0.15s; color: #555; white-space: nowrap; }
    .ci-thumb:hover { background: #f0ede8; }
    .ci-thumb.selected { border-color: #5a7a3a; background: #eaf4e4; color: #2e5e2e; font-weight: 600; }
    .ci-question textarea { width: 100%; min-height: 96px; border: 1.5px solid #e0dbd3; border-radius: 8px; padding: 10px 14px; font-size: 15px; font-family: inherit; resize: vertical; background: #fafaf8; color: #1a1a1a; line-height: 1.5; display: block; }
    .ci-question textarea:focus { outline: none; border-color: #5a7a3a; background: white; }
    #checkin-actions { display: flex; gap: 10px; margin-top: 24px; justify-content: flex-end; flex-wrap: wrap; }
    .ci-btn { min-width: 84px; padding: 9px 18px; font-size: 14px; line-height: 1.2; border-radius: 8px; cursor: pointer; font-family: inherit; border: 1.5px solid #d0ccc4; background: white; color: #666; }
    .ci-btn.primary { background: #2a2a2a; color: white; border-color: #2a2a2a; }
    .ci-btn:hover { opacity: 0.85; }
    @media (max-width: 520px) {
      #checkin-screen { padding: 16px; align-items: flex-start; }
      #checkin-card { padding: 24px 20px; max-height: calc(100vh - 32px); }
      .ci-question .ci-thumbs { flex-direction: column; }
      .ci-thumb { width: 100%; }
      #checkin-actions { flex-direction: column-reverse; }
      .ci-btn { width: 100%; }
    }

    #note-toast {
      position: fixed;
      bottom: 80px;
      right: 28px;
      background: #2e5e2e;
      color: white;
      padding: 9px 18px;
      border-radius: 8px;
      font-size: 13px;
      font-family: inherit;
      box-shadow: 0 4px 16px rgba(0,0,0,0.18);
      z-index: 70;
      opacity: 0;
      transform: translateY(6px);
      transition: opacity 0.22s, transform 0.22s;
      pointer-events: none;
    }
    #note-toast.show { opacity: 1; transform: translateY(0); }
  `;
  shadow.appendChild(styleEl);

  // ── Phase pill ────────────────────────────────────────────────────────
  const pill = document.createElement("div");
  pill.id = "phase-pill";
  pill.innerHTML = `<span id="phase-pill-label">Skim</span><button id="phase-pill-btn">→ Thorough</button>`;

  const studyBtn = shadow.getElementById("mode-study");
  if (studyBtn) studyBtn.after(pill);
  else console.warn("[ADHD Reader] phases.js: #mode-study not found, pill not inserted.");

  const pillBtn = shadow.getElementById("phase-pill-btn");
  if (pillBtn) pillBtn.addEventListener("click", () => {
    if (currentPhase === "skim") enterThoroughPhase();
    else enterSkimPhase();
  });

  // ── Notes FAB + panel ─────────────────────────────────────────────────
  const notesFab = document.createElement("button");
  notesFab.id = "notes-fab";
  notesFab.textContent = "✎ Notes";
  shadow.getElementById("reader-overlay").appendChild(notesFab);

  const noteToast = document.createElement("div");
  noteToast.id = "note-toast";
  shadow.getElementById("reader-overlay").appendChild(noteToast);
  let toastTimer = null;
  function showToast(msg) {
    noteToast.textContent = msg;
    noteToast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => noteToast.classList.remove("show"), 2200);
  }

  const notesPanel = document.createElement("div");
  notesPanel.id = "notes-panel";
  notesPanel.innerHTML = `
    <div id="notes-panel-header"><span id="notes-panel-title">Notes</span><button id="notes-panel-close" title="Close">✕</button></div>
    <div id="notes-para-context"></div>
    <div id="notes-list"><div id="notes-empty">No notes yet.</div></div>
    <div id="notes-input-area">
      <textarea id="notes-textarea" placeholder="Add a note..."></textarea>
      <div id="notes-panel-footer">
        <button id="notes-para-toggle">+ link to paragraph</button>
        <div style="display:flex;gap:6px;">
          <button class="notes-btn" id="notes-download" title="Download all notes as text file">Save</button>
          <button class="notes-btn primary" id="notes-save">Add</button>
        </div>
      </div>
    </div>`;
  shadow.getElementById("reader-overlay").appendChild(notesPanel);

  const savedNotes = [];
  let linkedParaIndex = null, linkedParaSnippet = null;
  let notesRestored = false;
  const storageKey = "adhdReaderNotes::" + window.location.href;

  function persistNotes() {
    if (typeof chrome !== "undefined" && chrome.storage) {
      chrome.storage.local.set({ [storageKey]: savedNotes });
    }
  }

  function restoreNotes() {
    if (notesRestored || typeof chrome === "undefined" || !chrome.storage) return;
    notesRestored = true;
    chrome.storage.local.get(storageKey, r => {
      const stored = r[storageKey];
      if (!Array.isArray(stored) || stored.length === 0) return;
      stored.forEach(n => {
        if (!n || !n.ts || savedNotes.some(existing => existing.ts === n.ts)) return;
        savedNotes.push(n);
        insertInlineNote(n);
        if (hasLinkedPara(n)) {
          const paras = shadow.querySelectorAll(".article-paragraph");
          if (paras[n.paraIndex]) paras[n.paraIndex].classList.add("para-has-note");
        }
      });
      renderNotesList();
      updateFabCount();
      showToast(`Restored ${savedNotes.length} note${savedNotes.length > 1 ? "s" : ""}`);
    });
  }

  function hasLinkedPara(note) {
    return note.paraIndex !== null && note.paraIndex !== undefined;
  }

  function renderNotesList() {
    const list = shadow.getElementById("notes-list");
    if (savedNotes.length === 0) { list.innerHTML = '<div id="notes-empty">No notes yet.</div>'; return; }
    list.innerHTML = "";
    savedNotes.forEach(n => {
      const item = document.createElement("div"); item.className = "note-item";
      const lbl = document.createElement("div"); lbl.className = "note-item-label"; lbl.textContent = hasLinkedPara(n) ? `Para ${n.paraIndex + 1}` : "General";
      const txt = document.createElement("div"); txt.className = "note-item-text"; txt.textContent = n.text;
      item.appendChild(lbl); item.appendChild(txt);
      if (n.paraSnippet) { const p2 = document.createElement("div"); p2.className = "note-item-para"; p2.textContent = "\u201c" + n.paraSnippet + "\u201d"; item.appendChild(p2); }
      list.appendChild(item);
    });
  }

  function insertInlineNote(note) {
    if (note.paraIndex === null || note.paraIndex === undefined) return;
    const paras = shadow.querySelectorAll(".article-paragraph");
    const para = paras[note.paraIndex];
    if (!para || shadow.querySelector(`.inline-note-card[data-note-ts="${note.ts}"]`)) return;

    const card = document.createElement("div");
    card.className = "inline-note-card";
    card.dataset.noteTs = note.ts;

    const lbl = document.createElement("div");
    lbl.className = "note-card-label";
    lbl.textContent = "Your note";

    const txt = document.createElement("div");
    txt.className = "note-card-text";
    txt.textContent = note.text;

    const del = document.createElement("button");
    del.className = "note-card-delete";
    del.title = "Remove note";
    del.textContent = "x";
    del.addEventListener("click", () => {
      const idx = savedNotes.findIndex(n => n.ts === note.ts);
      if (idx !== -1) savedNotes.splice(idx, 1);
      renderNotesList();
      updateFabCount();
      card.remove();
      const stillHasNote = savedNotes.some(n => n.paraIndex === note.paraIndex);
      if (!stillHasNote) paras[note.paraIndex]?.classList.remove("para-has-note");
      persistNotes();
      showToast("Note removed");
    });

    card.appendChild(lbl);
    card.appendChild(txt);
    card.appendChild(del);

    let insertAfter = para;
    let next = para.nextElementSibling;
    while (next && next.classList.contains("inline-note-card")) {
      insertAfter = next;
      next = next.nextElementSibling;
    }
    insertAfter.insertAdjacentElement("afterend", card);
  }

  function updateFabCount() {
    const count = savedNotes.length;
    notesFab.textContent = count > 0 ? `✎ Notes (${count})` : "✎ Notes";
  }

  function getVisibleParaIndex() {
    const paras = shadow.querySelectorAll(".article-paragraph");
    let closest = null, closestDist = Infinity;
    paras.forEach((p, i) => { const dist = Math.abs(p.getBoundingClientRect().top - 120); if (dist < closestDist) { closestDist = dist; closest = i; } });
    return closest;
  }

  function updateParaContext() {
    const ctx = shadow.getElementById("notes-para-context"), toggle = shadow.getElementById("notes-para-toggle");
    if (linkedParaIndex !== null) {
      ctx.innerHTML = `<strong>Para ${linkedParaIndex + 1}:</strong> ${escHtml(linkedParaSnippet || "")}`;
      ctx.classList.add("visible"); toggle.textContent = "✕ unlink paragraph"; toggle.classList.add("active");
    } else { ctx.classList.remove("visible"); toggle.textContent = "+ link to paragraph"; toggle.classList.remove("active"); }
  }

  notesFab.addEventListener("click", () => {
    notesPanel.classList.add("visible"); notesFab.classList.remove("visible");
    const idx = getVisibleParaIndex();
    if (idx !== null) { const paras = shadow.querySelectorAll(".article-paragraph"); linkedParaIndex = idx; linkedParaSnippet = (paras[idx]?.textContent || "").slice(0, 60) + "..."; updateParaContext(); }
    shadow.getElementById("notes-textarea").focus();
  });
  shadow.getElementById("notes-panel-close").addEventListener("click", () => { notesPanel.classList.remove("visible"); notesFab.classList.add("visible"); });
  shadow.getElementById("notes-para-toggle").addEventListener("click", () => {
    if (linkedParaIndex !== null) { linkedParaIndex = null; linkedParaSnippet = null; }
    else { const idx = getVisibleParaIndex(); if (idx !== null) { const paras = shadow.querySelectorAll(".article-paragraph"); linkedParaIndex = idx; linkedParaSnippet = (paras[idx]?.textContent || "").slice(0, 60) + "..."; } }
    updateParaContext();
  });
  shadow.getElementById("notes-save").addEventListener("click", () => {
    const ta = shadow.getElementById("notes-textarea"), text = ta.value.trim();
    if (!text) return;
    const note = { text, paraIndex: linkedParaIndex, paraSnippet: linkedParaSnippet, ts: Date.now() };
    savedNotes.push(note);
    renderNotesList(); insertInlineNote(note); updateFabCount(); ta.value = "";
    if (linkedParaIndex !== null) { const paras = shadow.querySelectorAll(".article-paragraph"); if (paras[linkedParaIndex]) paras[linkedParaIndex].classList.add("para-has-note"); }
    const idx = getVisibleParaIndex();
    if (idx !== null) { const paras = shadow.querySelectorAll(".article-paragraph"); linkedParaIndex = idx; linkedParaSnippet = (paras[idx]?.textContent || "").slice(0, 60) + "..."; } else { linkedParaIndex = null; linkedParaSnippet = null; }
    updateParaContext();
    persistNotes();
    showToast("Note saved");
    if (typeof chrome !== "undefined" && chrome.runtime) chrome.runtime.sendMessage({ type: "LOG_EVENT", event: "reading_notes", data: { notes: savedNotes } }).catch(() => {});
  });

  shadow.getElementById("notes-download").addEventListener("click", () => downloadNotes());

  // ── Check-in screen ───────────────────────────────────────────────────
  const checkinScreen = document.createElement("div");
  checkinScreen.id = "checkin-screen";
  shadow.getElementById("reader-overlay").appendChild(checkinScreen);

  function showCheckinScreen(onDone) {
    checkinScreen.innerHTML = "";
    const card = document.createElement("div"); card.id = "checkin-card"; checkinScreen.appendChild(card);
    const h2 = document.createElement("h2"); h2.textContent = "Reading check-in"; card.appendChild(h2);
    const sub = document.createElement("p"); sub.className = "ci-subtitle"; sub.textContent = "A quick reflection before you go."; card.appendChild(sub);
    const block = document.createElement("div"); block.className = "ci-question";
    const lbl = document.createElement("label"); lbl.textContent = "How did the reading go overall?"; block.appendChild(lbl);
    let thumbValue = null;
    const thumbsRow = document.createElement("div"); thumbsRow.className = "ci-thumbs";
    ["👍 Clear", "👎 Unclear"].forEach(t => {
      const btn = document.createElement("button"); btn.className = "ci-thumb"; btn.textContent = t;
      btn.addEventListener("click", () => { thumbValue = t.startsWith("👍") ? "up" : "down"; thumbsRow.querySelectorAll(".ci-thumb").forEach(b => b.classList.remove("selected")); btn.classList.add("selected"); });
      thumbsRow.appendChild(btn);
    });
    block.appendChild(thumbsRow);
    const ta = document.createElement("textarea"); ta.placeholder = "Any thoughts or notes? (optional)"; block.appendChild(ta);
    card.appendChild(block);
    const actions = document.createElement("div"); actions.id = "checkin-actions";
    const skipBtn = document.createElement("button"); skipBtn.className = "ci-btn"; skipBtn.textContent = "Skip";
    skipBtn.addEventListener("click", () => { checkinScreen.classList.remove("visible"); onDone([]); });
    const doneBtn = document.createElement("button"); doneBtn.className = "ci-btn primary"; doneBtn.textContent = "Done";
    doneBtn.addEventListener("click", () => {
      const notes = ta.value.trim();
      const result = { question: "How did the reading go overall?", thumb: thumbValue, notes, wroteNote: notes.length > 0 };
      if (typeof chrome !== "undefined" && chrome.runtime) chrome.runtime.sendMessage({ type: "LOG_EVENT", event: "selfcheck_answer", data: { answers: [result] } }).catch(() => {});
      checkinScreen.classList.remove("visible"); onDone([result]);
    });
    actions.appendChild(skipBtn); actions.appendChild(doneBtn); card.appendChild(actions);
    checkinScreen.classList.add("visible");
  }

  // ── Public API ────────────────────────────────────────────────────────
  function activate(readingMode) {
    isActive = true; currentPhase = "skim";
    notesFab.classList.add("visible");
    setReadingMode(readingMode || "full");
    restoreNotes();
  }

  function setReadingMode(mode) {
    if (mode === "full") {
      pill.className = "phase-pill " + currentPhase + " visible";
      if (currentPhase === "skim") applySkimDimming();
      else applyThoroughFocus(thoroughFocusIndex);
    } else {
      pill.className = "phase-pill";
      removeSkimDimming();
      removeThoroughFocus();
    }
  }

  function deactivate(onDone) {
    removeThoroughFocus();
    notesFab.classList.remove("visible");
    notesPanel.classList.remove("visible");
    showCheckinScreen((results) => {
      isActive = false;
      pill.className = "phase-pill"; pill.classList.remove("visible");
      removeSkimDimming();
      if (onDone) onDone(results);
    });
  }

  // ── Skim phase ────────────────────────────────────────────────────────
  function enterSkimPhase() {
    currentPhase = "skim";
    removeThoroughFocus();
    if (window.__readerModes) window.__readerModes.setMode("full");
    shadow.getElementById("mode-full").classList.add("active");
    shadow.getElementById("mode-para").classList.remove("active");
    shadow.getElementById("mode-sentence").classList.remove("active");
    if (overlay) overlay.scrollTop = 0;
    pill.className = "phase-pill skim visible";
    shadow.getElementById("phase-pill-label").textContent = "Skim";
    shadow.getElementById("phase-pill-btn").textContent = "→ Thorough";
    applySkimDimming();
  }

  // ── Thorough phase ────────────────────────────────────────────────────
  // Full page stays visible. All paragraphs except the focused one are
  // greyed out — same visual treatment as skim mode but paragraph-granularity.
  // Navigate with ↑/↓ arrow keys or by scrolling.

  function enterThoroughPhase() {
    currentPhase = "thorough";
    removeSkimDimming();

    // Stay in Full mode — full page stays visible, no mode switch
    if (window.__readerModes) window.__readerModes.setMode("full");
    shadow.getElementById("mode-full").classList.add("active");
    shadow.getElementById("mode-para").classList.remove("active");
    shadow.getElementById("mode-sentence").classList.remove("active");

    pill.className = "phase-pill thorough visible";
    shadow.getElementById("phase-pill-label").textContent = "Thorough";
    shadow.getElementById("phase-pill-btn").textContent = "← Skim";

    // Start focus at whichever paragraph is currently in view
    thoroughFocusIndex = getViewportCenterParaIndex();
    applyThoroughFocus(thoroughFocusIndex);

    // Scroll: update focus as user scrolls
    thoroughScrollHandler = () => {
      if (thoroughScrollThrottle) return;
      thoroughScrollThrottle = setTimeout(() => {
        thoroughScrollThrottle = null;
        if (currentPhase !== "thorough") return;
        const idx = getViewportCenterParaIndex();
        if (idx !== thoroughFocusIndex) applyThoroughFocus(idx);
      }, 120);
    };
    overlay.addEventListener("scroll", thoroughScrollHandler, { passive: true });

    // Arrow keys: step one paragraph at a time
    thoroughKeyHandler = (e) => {
      if (currentPhase !== "thorough") return;
      if (isEditableTarget(e.target)) return;
      const paras = shadow.querySelectorAll(".article-paragraph");
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        const next = Math.min(thoroughFocusIndex + 1, paras.length - 1);
        applyThoroughFocus(next);
        paras[next].scrollIntoView({ behavior: "smooth", block: "center" });
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        const prev = Math.max(thoroughFocusIndex - 1, 0);
        applyThoroughFocus(prev);
        paras[prev].scrollIntoView({ behavior: "smooth", block: "center" });
      }
    };
    document.addEventListener("keydown", thoroughKeyHandler);
  }

  function isEditableTarget(target) {
    if (!target || target === document || target === window) return false;
    const tag = target.tagName?.toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
  }

  // Use inline styles (not CSS class) so we override the opacity:1 that
  // modes.js setMode("full") sets on every paragraph as an inline style.
  function applyThoroughFocus(idx) {
    const paras = shadow.querySelectorAll(".article-paragraph");
    paras.forEach((p, i) => {
      p.style.opacity    = i === idx ? "1" : "0.2";
      p.style.transition = "opacity 0.3s";
    });
    thoroughFocusIndex = idx;
    if (window.__readerState) {
      window.__readerState.__currentParaIndex     = idx;
      window.__readerState.__currentSentenceIndex = null;
    }
  }

  function removeThoroughFocus() {
    // Clear inline opacity so paragraphs return to natural appearance
    shadow.querySelectorAll(".article-paragraph").forEach(p => {
      p.style.opacity    = "";
      p.style.transition = "";
    });
    if (thoroughScrollHandler) { overlay.removeEventListener("scroll", thoroughScrollHandler); thoroughScrollHandler = null; }
    if (thoroughScrollThrottle) { clearTimeout(thoroughScrollThrottle); thoroughScrollThrottle = null; }
    if (thoroughKeyHandler) { document.removeEventListener("keydown", thoroughKeyHandler); thoroughKeyHandler = null; }
  }

  function getViewportCenterParaIndex() {
    const paras = shadow.querySelectorAll(".article-paragraph");
    const viewMid = window.innerHeight / 2;
    let bestIdx = 0, bestDist = Infinity;
    paras.forEach((p, i) => {
      const rect = p.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) return;
      const dist = Math.abs((rect.top + rect.bottom) / 2 - viewMid);
      if (dist < bestDist) { bestDist = dist; bestIdx = i; }
    });
    return bestIdx;
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
    paras.forEach((p, i) => { if (paragraphs[i] !== undefined) p.textContent = paragraphs[i]; });
  }

  function escHtml(str) {
    return (str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  function getSavedNotes() { return savedNotes; }

  function downloadNotes() {
    if (savedNotes.length === 0) { showToast("No notes to save yet"); return false; }
    let text = `ADHD READING SUPPORT - NOTES\n${"=".repeat(40)}\n\n`;
    savedNotes.forEach((n, i) => {
      text += `Note ${i + 1}${hasLinkedPara(n) ? ` (Para ${n.paraIndex + 1})` : ""}\n`;
      if (n.paraSnippet) text += `"${n.paraSnippet}"\n`;
      text += `${n.text}\n\n`;
    });
    const blob = new Blob([text], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url;
    a.download = `adhd_notes_${Date.now()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("Notes downloaded");
    return true;
  }

  window.__readerPhases = { activate, deactivate, setReadingMode, getSavedNotes, downloadNotes };
  console.log("[ADHD Reader] phases.js ready.");

})();
