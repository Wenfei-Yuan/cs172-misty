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
  // Wait for offscreen.js to signal it's loaded
  await waitForOffscreenReady();
  const resp = await chrome.runtime.sendMessage({
    target: "offscreen-webcam",
    type: "START",
  });
  if (resp && !resp.ok) {
    throw new Error(resp.error || "offscreen start failed");
  }
  isRunning = true;
  updateBadge("ON", "#1f8f5f");
}

function waitForOffscreenReady() {
  return new Promise((resolve) => {
    // Check if already ready via a ping
    chrome.runtime.sendMessage({ target: "offscreen-webcam", type: "PING" }, (resp) => {
      if (resp && resp.ok) { resolve(); return; }
      // Not ready yet, listen for READY signal
      const listener = (msg) => {
        if (msg.type === "OFFSCREEN_READY") {
          chrome.runtime.onMessage.removeListener(listener);
          resolve();
        }
      };
      chrome.runtime.onMessage.addListener(listener);
      // Safety timeout — resolve after 3s no matter what
      setTimeout(() => {
        chrome.runtime.onMessage.removeListener(listener);
        resolve();
      }, 3000);
    });
  });
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

// ── Grant flow: open a real tab to get camera permission ─────

let pendingGrantResolve = null;
let pendingGrantReject = null;

async function requestCameraViaTab() {
  return new Promise((resolve, reject) => {
    pendingGrantResolve = resolve;
    pendingGrantReject = reject;
    chrome.tabs.create({ url: chrome.runtime.getURL("grant.html"), active: true }, (tab) => {
      // Safety timeout — if grant page doesn't respond in 30s, reject
      setTimeout(() => {
        if (pendingGrantResolve) {
          pendingGrantResolve = null;
          pendingGrantReject = null;
          reject(new Error("Camera permission timeout"));
        }
      }, 30000);
    });
  });
}

async function startWithGrant() {
  // Open grant.html tab to get camera permission first
  await requestCameraViaTab();
  // Permission granted, now start offscreen monitor
  await startMonitor();
}

// ── Message router ───────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "TOGGLE") {
    (isRunning ? stopMonitor() : startWithGrant())
      .then(() => sendResponse({ ok: true, running: isRunning }))
      .catch((e) => sendResponse({ ok: false, error: e.message, running: false }));
    return true; // async
  }

  if (msg.type === "GET_STATUS") {
    sendResponse({ running: isRunning });
    return;
  }

  // Camera grant page signals
  if (msg.type === "CAMERA_GRANTED") {
    if (pendingGrantResolve) {
      const res = pendingGrantResolve;
      pendingGrantResolve = null;
      pendingGrantReject = null;
      res();
    }
    return;
  }
  if (msg.type === "CAMERA_DENIED") {
    if (pendingGrantReject) {
      const rej = pendingGrantReject;
      pendingGrantResolve = null;
      pendingGrantReject = null;
      rej(new Error("Camera permission denied"));
    }
    updateBadge("ERR", "#b42318");
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
