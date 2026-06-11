// background.js — Stage 5 + 6: logging, script injection, WebSocket bridge
// ws_bridge.js is imported via importScripts below.

try { importScripts('ws_bridge.js'); } catch(e) { console.warn('[ADHD Reader] ws_bridge.js not loaded:', e.message); }

// ── Keep-alive via alarms (MV3 service workers sleep after ~30s) ──────────
chrome.alarms.create("keepAlive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {}); // just wakes the worker

// ── Session log stored in chrome.storage.session ──────────────────────────
// (persists while browser is open, cleared on restart — perfect for study sessions)
function getLog() {
  return new Promise(resolve => {
    chrome.storage.session.get("sessionLog", r => resolve(r.sessionLog || null));
  });
}
function setLog(log) {
  return chrome.storage.session.set({ sessionLog: log });
}

function newLog(tabId, title, url) {
  return {
    sessionId:   `session_${Date.now()}`,
    tabId,
    title,
    url,
    startTime:   Date.now(),
    endTime:     null,
    events:      [],   // all timestamped events
    // summary fields updated on export
    totalDuration:    null,
    modeChanges:      [],
    redirections:     [],
    pauseCount:       0,
    totalPauseMs:     0,
    selfCheckAnswers: [],
  };
}

function logEvent(type, data = {}) {
  getLog().then(log => {
    if (!log) return;
    log.events.push({ type, ts: Date.now(), ...data });
    // Update summary fields inline for easy export
    if (type === "mode_change")       log.modeChanges.push({ ts: Date.now(), mode: data.mode });
    if (type === "redirection")       log.redirections.push({ ts: Date.now() });
    if (type === "pause_start")       log.pauseCount++;
    if (type === "pause_end")         log.totalPauseMs += (data.durationMs || 0);
    if (type === "nav_advance")        log.navAdvances = (log.navAdvances || []).concat({ ts: Date.now(), dwellMs: data.dwellMs, paragraphIndex: data.paragraphIndex, sentenceIndex: data.sentenceIndex });
    if (type === "selfcheck_answer") {
      // data.answers is an array of { question, thumb, notes, wroteNote }
      const entries = Array.isArray(data.answers) ? data.answers : [];
      entries.forEach(r => log.selfCheckAnswers.push({
        ts: Date.now(), question: r.question, thumb: r.thumb,
        notes: r.notes, wroteNote: r.wroteNote
      }));
    }
    setLog(log);
  });
}

// ── Toolbar button click: inject scripts ──────────────────────────────────
chrome.action.onClicked.addListener(async (tab) => {
  const log = newLog(tab.id, tab.title, tab.url);
  await setLog(log);
  logEvent("session_start", { title: tab.title, url: tab.url });

  // Connect WebSocket bridge to Misty when session starts
  if (typeof connectBridge === "function") connectBridge();

  chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["Readability.js"] })
  .then(() => chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content_script.js"] }))
  .then(results => {
    // content_script.js returns true on success, undefined/false if it bailed early
    // (unsupported page, parse failure, etc.) — don't inject UI scripts in that case
    const succeeded = results?.[0]?.result === true;
    if (!succeeded) {
      console.log("[ADHD Reader] content_script did not succeed — skipping UI injection.");
      return;
    }
    return chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["reader.js"] })
      .then(() => chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["modes.js"] }))
      .then(() => chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["phases.js"] }))
      .then(() => chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["logger.js"] }));
  })
  .catch(err => console.error("[ADHD Reader] Injection failed:", err));
});

// ── Message handler: receives events from content scripts ─────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  if (msg.type === "LOG_EVENT") {
    logEvent(msg.event, msg.data || {});
    sendResponse({ ok: true });
  }

  if (msg.type === "EXPORT_LOG") {
    getLog().then(log => {
      if (!log) { sendResponse({ error: "No log found" }); return; }
      log.endTime      = Date.now();
      log.totalDuration = log.endTime - log.startTime;
      setLog(log);
      sendResponse({ log });
    });
    return true; // async response
  }

  if (msg.type === "PING") {
    sendResponse({ ok: true });
  }

  // Reading state from content scripts → forward to Misty
  if (msg.type === "READING_STATE") {
    if (typeof updateReadingState === "function") updateReadingState(msg.data || {});
    sendResponse({ ok: true });
  }

  // Bridge status check (for debugging)
  if (msg.type === "BRIDGE_STATUS") {
    const status = typeof getBridgeStatus === "function" ? getBridgeStatus() : { isConnected: false };
    sendResponse(status);
  }
});
