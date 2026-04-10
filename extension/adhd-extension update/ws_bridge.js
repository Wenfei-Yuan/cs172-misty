// ws_bridge.js — Stage 6: WebSocket bridge to Misty II robot
// Runs in the background service worker alongside background.js.
//
// ── SETUP ─────────────────────────────────────────────────────────────────
// Before the study, fill in the two placeholders below:
//
//   MISTY_WS_URL  — Misty's WebSocket address on your local network
//                   Format: "ws://192.168.x.x:PORT"
//                   Find Misty's IP in the Misty app under "Settings"
//                   Default Misty REST/WS port is 80, so try "ws://192.168.x.x:80"
//
//   REDIRECTION_EVENT — The exact event name Misty sends when it wants to
//                       redirect the participant's attention back to reading.
//                       Check your Misty skill code for what it broadcasts.
//                       Common pattern: "HazardNotification" or a custom
//                       skill event like "AttentionRedirect"
// ──────────────────────────────────────────────────────────────────────────

const MISTY_WS_URL       = "ws://10.0.0.162:8765";  // ← Wenfei's bridge server — update port if not 8765
const REDIRECTION_EVENT  = "AttentionRedirect";        // ← FILL IN event name from Misty skill
const RESUMPTION_EVENT   = "AttentionResumed";         // ← FILL IN resumption confirm event name
const DISENGAGEMENT_SIGNAL = "covert_disengagement=true";
const REENGAGEMENT_SIGNAL  = "covert_disengagement=false";

// How often to send reading state to Misty (ms)
const SEND_INTERVAL_MS = 2000;
const RECONNECT_DELAY_MS = 5000;
const STATE_STALE_AFTER_MS = 6000;

// Pause duration threshold before sending disengagement signal (ms)
// e.g. 5000 = send after 5 seconds of no activity
const DISENGAGE_THRESHOLD_MS = 5000;

// ── State ──────────────────────────────────────────────────────────────────
let ws            = null;
let sendInterval  = null;
let reconnectTimer = null;
let isConnected         = false;
let lastReadingState    = {};
let lastReadingStateAt  = 0;
let disengage_sent      = false;  // tracks whether we've already sent the signal for this pause
let reconnectAttempts   = 0;
let bridgeTabId         = null;

function clearReadingState() {
  lastReadingState = {};
  lastReadingStateAt = 0;
  disengage_sent = false;
}

function truncateForLog(value, maxLength = 500) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "";
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

function logBridgeTraffic(direction, label, payload, extra = {}) {
  const raw = typeof payload === "string" ? payload : JSON.stringify(payload);
  logToBackground(`ws_${direction}`, {
    label,
    raw: truncateForLog(raw),
    ...extra,
  });
}

function wsStateLabel(socket = ws) {
  if (!socket) return "NO_SOCKET";

  switch (socket.readyState) {
    case WebSocket.CONNECTING:
      return "CONNECTING";
    case WebSocket.OPEN:
      return "OPEN";
    case WebSocket.CLOSING:
      return "CLOSING";
    case WebSocket.CLOSED:
      return "CLOSED";
    default:
      return `UNKNOWN(${socket.readyState})`;
  }
}

function previewMessage(data) {
  if (typeof data === "string") return data;
  if (data instanceof ArrayBuffer) return `[ArrayBuffer ${data.byteLength} bytes]`;
  if (typeof Blob !== "undefined" && data instanceof Blob) return `[Blob ${data.size} bytes]`;

  try {
    return JSON.stringify(data);
  } catch {
    return String(data);
  }
}

async function normalizeIncomingData(data) {
  if (typeof data === "string") return data;
  if (typeof Blob !== "undefined" && data instanceof Blob) return await data.text();
  if (data instanceof ArrayBuffer) return new TextDecoder().decode(data);
  return String(data);
}

function tryParseJson(raw) {
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.log("[WS Bridge] Incoming payload is not JSON.", { raw, error: error.message });
    return null;
  }
}

// ── Tcpdump Natural-Language Parser ───────────────────────────────────────
// Parses a single tcpdump line (IPv4 TCP) into a human-readable sentence.
//
// Example input:
//   "17:54:02.368528 IP 10.5.15.192.60050 > 10.5.15.160.8765: Flags [.], ack 1818, win 1024, options [nop,nop,TS val 1422699297 ecr 2092639974], length 0"
//
// Example output:
//   "At 17:54:02.368528, a TCP ACK packet was sent from 10.5.15.192:60050 to
//    10.5.15.160:8765 (bridge server). No data payload (0 bytes). Acknowledges
//    byte 1818. Receive window: 1024 bytes. TCP timestamps: val=1422699297,
//    ecr=2092639974."
//
// Returns null if the line does not match the expected tcpdump format.
function parseTcpdumpLine(line) {
  if (!line || typeof line !== "string") return null;

  // ── 1. Timestamp ──────────────────────────────────────────────────────
  const tsMatch = line.match(/^(\d{2}:\d{2}:\d{2}\.\d+)/);
  if (!tsMatch) return null;
  const timestamp = tsMatch[1];

  // ── 2. Source / Destination  (last dot-segment = port) ───────────────
  const addrMatch = line.match(
    /IP\s+([\d.]+)\.(\d+)\s+>\s+([\d.]+)\.(\d+):/
  );
  if (!addrMatch) return null;
  const [, srcIp, srcPort, dstIp, dstPort] = addrMatch;

  // ── 3. Flags ──────────────────────────────────────────────────────────
  const flagsMatch = line.match(/Flags\s+\[([^\]]*)\]/);
  const rawFlags   = flagsMatch ? flagsMatch[1] : "";

  const FLAG_NAMES = {
    S: "SYN",
    F: "FIN",
    R: "RST",
    P: "PSH",
    U: "URG",
    E: "ECE",
    C: "CWR",
    ".": "ACK",
  };
  const flagLabels = rawFlags
    .split("")
    .map(f => FLAG_NAMES[f] || f)
    .filter(Boolean);
  const flagDesc = flagLabels.length
    ? flagLabels.join("+")
    : "unknown-flags";

  // Friendly one-word description of common flag combos
  const COMBO_LABELS = {
    "ACK":         "pure acknowledgment",
    "SYN":         "connection request",
    "SYN+ACK":     "connection accepted",
    "FIN+ACK":     "graceful close",
    "RST":         "connection reset",
    "RST+ACK":     "connection reset (acknowledged)",
    "PSH+ACK":     "data push",
    "PSH":         "data push (no ACK)",
  };
  const comboLabel = COMBO_LABELS[flagDesc] || `TCP ${flagDesc}`;

  // ── 4. Sequence / ACK numbers ─────────────────────────────────────────
  const seqMatch = line.match(/\bseq\s+(\d+)(?::(\d+))?/);
  const ackMatch = line.match(/\back\s+(\d+)/);
  const seqNum   = seqMatch ? seqMatch[1] : null;
  const seqEnd   = seqMatch ? seqMatch[2] : null;
  const ackNum   = ackMatch ? ackMatch[1] : null;

  // ── 5. Window size ────────────────────────────────────────────────────
  const winMatch = line.match(/\bwin\s+(\d+)/);
  const winSize  = winMatch ? winMatch[1] : null;

  // ── 6. TCP options ────────────────────────────────────────────────────
  const tsOptMatch = line.match(/\bTS\s+val\s+(\d+)\s+ecr\s+(\d+)/i);
  const tsVal      = tsOptMatch ? tsOptMatch[1] : null;
  const tsEcr      = tsOptMatch ? tsOptMatch[2] : null;

  const mssMatch = line.match(/\bmss\s+(\d+)/i);
  const mssVal   = mssMatch ? mssMatch[1] : null;

  // ── 7. Payload length ─────────────────────────────────────────────────
  const lenMatch = line.match(/\blength\s+(\d+)/);
  const length   = lenMatch ? parseInt(lenMatch[1], 10) : null;

  // ── 8. Annotate known addresses ───────────────────────────────────────
  function annotateAddr(ip, port) {
    const addr = `${ip}:${port}`;
    if (ip === "10.5.15.160" && port === "8765") return `${addr} (bridge server)`;
    if (ip === "10.5.15.192")                    return `${addr} (extension host)`;
    return addr;
  }
  const srcLabel = annotateAddr(srcIp, srcPort);
  const dstLabel = annotateAddr(dstIp, dstPort);

  // ── 9. Compose sentence ───────────────────────────────────────────────
  const parts = [
    `At ${timestamp}, a TCP ${comboLabel} packet was sent from ${srcLabel} to ${dstLabel}.`,
  ];

  if (seqNum !== null) {
    parts.push(seqEnd
      ? `Carries bytes ${seqNum}–${seqEnd} (${Number(seqEnd) - Number(seqNum)} bytes of data).`
      : `Sequence number: ${seqNum}.`);
  }

  if (length !== null) {
    parts.push(length === 0
      ? "No data payload (0 bytes)."
      : `Payload size: ${length} byte${length !== 1 ? "s" : ""}.`);
  }

  if (ackNum !== null) {
    parts.push(`Acknowledges byte ${ackNum} from the remote side.`);
  }

  if (winSize !== null) {
    parts.push(`Receive window: ${winSize} bytes.`);
  }

  if (tsVal !== null) {
    parts.push(`TCP timestamps: val=${tsVal}, ecr=${tsEcr}.`);
  }

  if (mssVal !== null) {
    parts.push(`Maximum segment size (MSS): ${mssVal} bytes.`);
  }

  return parts.join(" ");
}

// Convenience: parse and log a raw tcpdump line to the console.
function logTcpdumpLine(line) {
  const nl = parseTcpdumpLine(line);
  if (nl) {
    console.log("[WS Bridge] Packet →", nl);
  } else {
    console.warn("[WS Bridge] Could not parse tcpdump line:", line);
  }
  return nl;
}

function sendWs(payload, label) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    console.warn(`[WS Bridge] Skip send (${label}) — socket is ${wsStateLabel()}.`);
    logBridgeTraffic("outbound_skipped", label, payload, { state: wsStateLabel() });
    return false;
  }

  const serialized = typeof payload === "string" ? payload : JSON.stringify(payload);
  console.log(`[WS Bridge] Sending ${label}:`, serialized);

  try {
    ws.send(serialized);
    logBridgeTraffic("outbound", label, payload, { state: wsStateLabel() });
    return true;
  } catch (error) {
    console.warn(`[WS Bridge] Send failed (${label}):`, error.message || error);
    logBridgeTraffic("outbound_error", label, payload, { error: error.message || String(error), state: wsStateLabel() });
    return false;
  }
}

// ── Connect ────────────────────────────────────────────────────────────────
function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;

  reconnectAttempts += 1;
  console.log(`[WS Bridge] Connecting (attempt ${reconnectAttempts}) to`, MISTY_WS_URL);
  logToBackground("ws_connect_attempt", { attempt: reconnectAttempts, url: MISTY_WS_URL });

  try {
    ws = new WebSocket(MISTY_WS_URL);
  } catch (e) {
    console.warn("[WS Bridge] Could not create WebSocket:", e.message);
    logToBackground("ws_connect_error", { stage: "constructor", error: e.message, url: MISTY_WS_URL });
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    reconnectAttempts = 0;
    console.log("[WS Bridge] Connected to Wenfei's bridge server at", MISTY_WS_URL, `(${wsStateLabel()})`);
    isConnected = true;
    logToBackground("ws_connected", { url: MISTY_WS_URL });

    // Register as "extension" client — server routes ROBOT_REDIRECT to this role
    sendWs({ client: "extension" }, "registration");

    // Also forward confirmation to content scripts so the UI can show it
    forwardToContentScripts({ type: "BRIDGE_CONNECTED" });

    console.log("[WS Bridge] Confirmation sent to server.");

    // Start sending reading state on an interval
    clearInterval(sendInterval);
    sendInterval = setInterval(sendReadingState, SEND_INTERVAL_MS);
  };

  ws.onmessage = async (event) => {
    console.log("[WS Bridge] Incoming frame preview:", previewMessage(event.data));

    const raw = await normalizeIncomingData(event.data);
    console.log("[WS Bridge] Message from server:", raw);
    logBridgeTraffic("inbound", "server message", raw);

    // Server sends ROBOT_REDIRECT as a plain string when distraction is detected
    if (raw === "ROBOT_REDIRECT") {
      console.log("[WS Bridge] Redirection signal received.");
      forwardToContentScripts({ type: "ROBOT_REDIRECT" });
      logToBackground("redirection", { source: "misty", ts: Date.now() });
      return;
    }

    if (raw === REDIRECTION_EVENT) {
      console.log("[WS Bridge] Named redirection event received.");
      forwardToContentScripts({ type: "ROBOT_REDIRECT" });
      logToBackground("redirection", { source: "misty", eventName: REDIRECTION_EVENT, ts: Date.now() });
      return;
    }

    // Server sends registration_ack as JSON — use it to confirm connection
    const msg = tryParseJson(raw);
    if (!msg) return;

    console.log("[WS Bridge] Parsed message object:", msg);

    if (msg?.type === "registered" && msg?.client === "extension") {
      console.log("[WS Bridge] Registered as extension client — ready.");
      forwardToContentScripts({ type: "BRIDGE_CONNECTED" });
      return;
    }

    // Handle any other named events
      const eventName = msg?.eventName || msg?.EventName || msg?.type || msg?.Type || "";
    if (eventName) {
      console.log("[WS Bridge] Event name resolved:", eventName);
    }

    if (eventName === REDIRECTION_EVENT) {
      console.log("[WS Bridge] JSON redirection event received.");
      forwardToContentScripts({ type: "ROBOT_REDIRECT" });
      logToBackground("redirection", { source: "misty", eventName, ts: Date.now() });
      return;
    }

    if (eventName === RESUMPTION_EVENT) {
      console.log("[WS Bridge] Resumption confirm received.");
      clearReadingState();
      forwardToContentScripts({ type: "ROBOT_RESUME" });
      logToBackground("robot_resume", { ts: Date.now() });
    }
  };

  ws.onerror = (err) => {
    console.warn("[WS Bridge] WebSocket error:", err.message || err, `(${wsStateLabel()})`);
    logToBackground("ws_error", { error: err?.message || String(err), state: wsStateLabel() });
  };

  ws.onclose = () => {
    console.log(`[WS Bridge] Connection closed. Will retry in ${RECONNECT_DELAY_MS / 1000}s.`);
    isConnected = false;
    clearInterval(sendInterval);
    logToBackground("ws_closed", { url: MISTY_WS_URL, nextRetryMs: RECONNECT_DELAY_MS });
    forwardToContentScripts({ type: "BRIDGE_DISCONNECTED" });
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  console.log(`[WS Bridge] Scheduling reconnect in ${RECONNECT_DELAY_MS} ms.`);
  logToBackground("ws_reconnect_scheduled", { delayMs: RECONNECT_DELAY_MS });
  reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
}

// ── Send reading state to Misty ────────────────────────────────────────────
// Extension → Robot:
//   scrollProgress  (0–100%)
//   readingSpeed    (estimated wpm, null if unknown)
//   pauseDuration   (ms since last scroll stall, 0 if active)
//   activeMode      ("full" | "para" | "sentence")
//   currentPhase    ("skim" | "thorough" | null if Study not active)
function sendReadingState() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  const stateAgeMs = lastReadingStateAt ? Date.now() - lastReadingStateAt : Infinity;
  if (stateAgeMs > STATE_STALE_AFTER_MS) {
    if (disengage_sent || Object.keys(lastReadingState).length > 0) {
      console.log("[WS Bridge] Skipping stale reading state.", { stateAgeMs });
      logToBackground("ws_state_stale", { stateAgeMs });
    }
    clearReadingState();
    return;
  }

  const state = lastReadingState;
  const pauseDuration = state.pauseDuration || 0;
  const disengaged    = state.covert_disengagement || false;
  const thresholdReached = disengaged && pauseDuration >= DISENGAGE_THRESHOLD_MS;

  // Send covert_disengagement=true once when pause exceeds threshold
  // Reset when user re-engages so it can fire again next pause
  if (thresholdReached && !disengage_sent) {
    disengage_sent = true;
    console.log("[WS Bridge] Sending disengagement signal to server.");
    sendWs(DISENGAGEMENT_SIGNAL, "disengagement signal");
  } else if (!disengaged && disengage_sent) {
    disengage_sent = false;  // reset so next pause can trigger again
    console.log("[WS Bridge] User re-engaged, resetting disengagement flag.");
    sendWs(REENGAGEMENT_SIGNAL, "re-engagement signal");
  }

  // Also send full reading state as context
  const payload = {
    client: "extension",
    type: "ReadingState",
    ...state,
    covert_disengagement: thresholdReached,
    ts: Date.now(),
  };
  sendWs(payload, "reading state");
}

// ── Forward signal to content scripts ─────────────────────────────────────
function forwardToContentScripts(msg) {
  if (Number.isInteger(bridgeTabId)) {
    chrome.tabs.sendMessage(bridgeTabId, msg).catch(() => {});
    return;
  }

  chrome.tabs.query({ active: true }, (tabs) => {
    tabs.forEach(tab => {
      chrome.tabs.sendMessage(tab.id, msg).catch(() => {});
    });
  });
}

// ── Log to background session log ─────────────────────────────────────────
function logToBackground(event, data) {
  // background.js handles LOG_EVENT messages
  // We call logEvent directly since we're in the same service worker scope
  if (typeof logEvent === "function") {
    logEvent(event, data);
  }
}

// ── Receive reading state updates from content scripts ────────────────────
// background.js routes READING_STATE messages here
function updateReadingState(state) {
  lastReadingState = { ...lastReadingState, ...state };
  lastReadingStateAt = Date.now();
}

// ── Public API (called from background.js) ────────────────────────────────
// Call connectBridge() when a study session starts (toolbar button clicked)
// Call disconnectBridge() when session ends
function connectBridge(tabId) {
  bridgeTabId = Number.isInteger(tabId) ? tabId : bridgeTabId;
  connect();
}

function disconnectBridge() {
  clearInterval(sendInterval);
  clearTimeout(reconnectTimer);
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  isConnected = false;
  reconnectAttempts = 0;
  bridgeTabId = null;
  clearReadingState();
  logToBackground("ws_disconnected", { url: MISTY_WS_URL });
  console.log("[WS Bridge] Disconnected.");
}

function getBridgeStatus() {
  return { isConnected, url: MISTY_WS_URL };
}
