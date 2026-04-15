/*  background.js — Webcam Monitor service worker
 *
 *  Manages the offscreen document lifecycle and relays status
 *  from the offscreen camera capture to the extension badge.
 */

const OFFSCREEN_PATH = "offscreen.html";
let creating = null;
let isRunning = false;

// ── Offscreen document helpers ───────────────────────────────

async function hasOffscreen() {
  if (chrome.runtime.getContexts) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
      documentUrls: [chrome.runtime.getURL(OFFSCREEN_PATH)],
    });
    return contexts.length > 0;
  }
  return false;
}

async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  if (creating) {
    await creating;
    return;
  }
  creating = chrome.offscreen
    .createDocument({
      url: OFFSCREEN_PATH,
      reasons: ["USER_MEDIA"],
      justification: "Webcam capture for posture monitoring",
    })
    .finally(() => {
      creating = null;
    });
  await creating;
}

async function closeOffscreen() {
  if (await hasOffscreen()) {
    await chrome.offscreen.closeDocument();
  }
}

// ── Start / Stop ─────────────────────────────────────────────

async function startMonitor() {
  await ensureOffscreen();
  await chrome.runtime.sendMessage({
    target: "offscreen-webcam",
    type: "START",
  });
  isRunning = true;
  updateBadge("ON", "#1f8f5f");
}

async function stopMonitor() {
  if (await hasOffscreen()) {
    try {
      await chrome.runtime.sendMessage({
        target: "offscreen-webcam",
        type: "STOP",
      });
    } catch {
      /* offscreen may already be gone */
    }
  }
  await closeOffscreen();
  isRunning = false;
  updateBadge("", "#1f8f5f");
}

// ── Badge ────────────────────────────────────────────────────

function updateBadge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
}

// ── Message router ───────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "TOGGLE") {
    (isRunning ? stopMonitor() : startMonitor())
      .then(() => sendResponse({ ok: true, running: isRunning }))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true; // async
  }

  if (msg.type === "GET_STATUS") {
    sendResponse({ running: isRunning });
    return;
  }

  // Status updates from offscreen.js
  if (msg.type === "WEBCAM_STATUS") {
    const s = (msg.status || "").toUpperCase();
    if (s === "CALIBRATING") updateBadge("CAL", "#b58105");
    else if (s === "CALIBRATED" || s === "ACTIVE") updateBadge("ON", "#1f8f5f");
    else if (s === "ARMING") updateBadge("ARM", "#b58105");
    else if (s === "CALIBRATION_RETRY") updateBadge("CAL", "#b58105");
    else if (s.startsWith("ERROR") || s === "ERR") {
      updateBadge("ERR", "#b42318");
      isRunning = false;
    } else if (s === "STOPPED") {
      updateBadge("", "#1f8f5f");
      isRunning = false;
    } else if (s === "STARTED" || s === "CONNECTED") {
      updateBadge("CAM", "#1f8f5f");
    } else if (s === "RECONNECTING") {
      updateBadge("REC", "#b58105");
    }
  }
});

// Keep service worker alive while monitoring
chrome.alarms.create("keepAlive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {});
