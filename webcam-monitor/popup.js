const btn = document.getElementById("toggle-btn");
const statusEl = document.getElementById("status");
let running = false;

function updateUI() {
  btn.textContent = running ? "Stop" : "Start";
  btn.classList.toggle("active", running);
  statusEl.textContent = running ? "Monitoring…" : "Idle";
}

btn.addEventListener("click", async () => {
  btn.disabled = true;
  try {
    const resp = await chrome.runtime.sendMessage({ type: "TOGGLE" });
    if (resp) running = resp.running;
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
  }
  btn.disabled = false;
  updateUI();
});

// Fetch initial state
chrome.runtime.sendMessage({ type: "GET_STATUS" }, (resp) => {
  if (resp) {
    running = resp.running;
    updateUI();
  }
});
