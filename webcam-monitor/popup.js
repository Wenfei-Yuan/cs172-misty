const btn = document.getElementById("toggle-btn");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
let running = false;

function log(msg) {
  const ts = new Date().toLocaleTimeString();
  logEl.textContent += `[${ts}] ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function updateUI(detail) {
  btn.textContent = running ? "Stop" : "Start";
  btn.classList.toggle("active", running);
  statusEl.textContent = detail || (running ? "Monitoring…" : "Idle");
}

btn.addEventListener("click", async () => {
  btn.disabled = true;
  log(running ? "Stopping…" : "Starting… (a new tab will open for camera permission)");
  statusEl.textContent = running ? "Stopping…" : "Starting…";
  try {
    const resp = await chrome.runtime.sendMessage({ type: "TOGGLE" });
    log("Response: " + JSON.stringify(resp));
    if (resp) {
      running = resp.running;
      if (!resp.ok) {
        log("ERROR: " + (resp.error || "unknown"));
        updateUI("Error: " + (resp.error || "unknown"));
      } else {
        updateUI();
      }
    }
  } catch (e) {
    log("CATCH: " + e.message);
    updateUI("Error: " + e.message);
  }
  btn.disabled = false;
});

// Listen for live status updates from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "WEBCAM_STATUS") {
    const s = (msg.status || "").toUpperCase();
    const detail = msg.detail || "";
    log("STATUS: " + s + (detail ? " | " + detail : ""));
    if (s.startsWith("ERROR") || s === "ERR") {
      running = false;
      updateUI("Error: " + detail);
    } else if (s === "STARTED" || s === "CONNECTED") {
      updateUI("Camera on, connecting…");
    } else if (s === "CALIBRATING" || s === "CAL") {
      updateUI("Calibrating (look at screen)…");
    } else if (s === "ACTIVE" || s === "CALIBRATED") {
      updateUI("Active — monitoring");
    } else if (s === "RECONNECTING") {
      updateUI("Reconnecting to bridge…");
    } else if (s === "STOPPED") {
      running = false;
      updateUI();
    }
  }
});

// Fetch initial state
chrome.runtime.sendMessage({ type: "GET_STATUS" }, (resp) => {
  if (resp) {
    running = resp.running;
    log("Initial state: running=" + running);
    updateUI();
  }
});
log("Popup loaded");
