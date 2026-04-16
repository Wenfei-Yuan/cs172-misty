/* grant.js — Opens in a real tab, requests camera, then signals background */
const statusEl = document.getElementById("status");

(async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    });
    // Permission granted — stop stream immediately
    stream.getTracks().forEach(t => t.stop());
    statusEl.className = "ok";
    statusEl.textContent = "✅ Camera access granted! Starting monitor…";
    // Tell background.js permission is granted
    await chrome.runtime.sendMessage({ type: "CAMERA_GRANTED" });
    // Close this tab after a short delay
    setTimeout(() => window.close(), 800);
  } catch (e) {
    statusEl.className = "err";
    if (e.name === "NotAllowedError") {
      statusEl.textContent = "❌ Camera access denied. Please allow camera and try again.";
    } else if (e.name === "NotFoundError") {
      statusEl.textContent = "❌ No camera found on this device.";
    } else {
      statusEl.textContent = "❌ Error: " + e.message;
    }
    chrome.runtime.sendMessage({ type: "CAMERA_DENIED", error: e.message });
  }
})();
