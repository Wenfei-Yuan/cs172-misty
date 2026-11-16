// study_controls.js — Start Reading / End Reading bar for the study reading pages.
//
// The ARIA reader overlay covers the whole page, which hides the reading page's own
// buttons. This script, injected after the overlay, adds the same bar on top of it.
// It only runs on the study reading pages served by browser_bridge.py (/read/<X>),
// which carry a #study-reading-page marker, and calls the same /api/reading/* endpoints.
//
// The CSS/HTML/JS below is copied from webcam/reading_flow.py (BAR_CSS, BAR_HTML,
// BAR_JS) so both bars look and behave the same. If you change one, regenerate this
// file (see webcam/reading_flow.py) or change both.

(function () {
  if (!document.getElementById("study-reading-page")) return;
  const old = document.getElementById("study-controls-host");
  if (old) { if (old._scTimer) clearInterval(old._scTimer); old.remove(); }

  const host = document.createElement("div");
  host.id = "study-controls-host";
  // Appended after the reader overlay with the same max z-index, so it sits on top.
  host.style.cssText = "position:fixed;left:0;bottom:0;width:0;height:0;z-index:2147483647;";
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>
  #study-controls {
    position: fixed; left: 24px; bottom: 24px; z-index: 2147483647;
    width: 230px; background: #ffffff; border: 1px solid #d9d4ca; border-radius: 12px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.15); padding: 14px 16px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #222;
  }
  #study-controls .sc-msg { font-size: 13px; line-height: 1.45; margin-bottom: 10px; min-height: 18px; }
  #study-controls .sc-ok   { color: #1e7b3a; }
  #study-controls .sc-err  { color: #b3261e; }
  #study-controls .sc-info { color: #555; }
  #study-controls button {
    width: 100%; padding: 10px; border: none; border-radius: 8px; font-size: 15px;
    font-weight: 600; cursor: pointer; color: #fff;
  }
  #study-controls button:disabled { opacity: 0.5; cursor: not-allowed; }
  #study-controls .sc-start { background: #2e7d32; }
  #study-controls .sc-end   { background: #b3261e; }
  #sc-modal-back {
    position: fixed; inset: 0; z-index: 2147483647; background: rgba(0,0,0,0.35);
    display: none; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  }
  #sc-modal { background: #fff; border-radius: 12px; padding: 24px; width: 360px; text-align: center; }
  #sc-modal p { font-size: 15px; color: #222; margin: 0 0 18px; line-height: 1.5; }
  #sc-modal button { padding: 9px 18px; border-radius: 8px; font-size: 14px; cursor: pointer; margin: 0 6px; }
  #sc-modal .sc-cancel  { border: 1px solid #ccc; background: #fff; color: #222; }
  #sc-modal .sc-confirm { border: none; background: #b3261e; color: #fff; font-weight: 600; }
</style>
<div id="study-controls">
  <div class="sc-msg sc-info" id="sc-msg">Loading…</div>
  <button class="sc-start" id="sc-start" style="display:none">Start Reading</button>
  <button class="sc-end" id="sc-end" style="display:none">End Reading</button>
</div>
<div id="sc-modal-back">
  <div id="sc-modal">
    <p>Are you sure you have finished reading?<br>You cannot go back to the passage after this.</p>
    <button class="sc-cancel" id="sc-cancel">Keep reading</button>
    <button class="sc-confirm" id="sc-confirm">Yes, end reading</button>
  </div>
</div>
`;


  function studyControls(root, api, source) {
    var $ = function (id) { return root.getElementById(id); };
    var msg = $('sc-msg'), btnStart = $('sc-start'), btnEnd = $('sc-end');
    var back = $('sc-modal-back'), bar = $('study-controls');
    var busy = false, redirected = false;
    var REASONS = {
      webcam_not_connected: 'the camera program is not connected',
      camera_open_failed: 'the camera could not be opened',
      calibration_failed: 'calibration failed (please face the screen)',
      timeout: 'no response from the camera',
      camera_start_failed: 'the camera did not start'
    };
    function show(text, kind) { bar.style.display = ''; msg.style.display = ''; msg.textContent = text; msg.className = 'sc-msg sc-' + kind; }
    function hideBar() { bar.style.display = 'none'; }
    function go(url) { if (!redirected) { redirected = true; window.location.href = url; } }
    async function call(method, path, body) {
      var res = await fetch(api + path, {
        method: method, headers: {'Content-Type': 'application/json'},
        body: body ? JSON.stringify(body) : undefined
      });
      return await res.json();
    }
    function render(d) {
      if (!d || !d.ok) return;
      if (d.state === 'ended') { show('Reading finished. Opening the questions…', 'info'); go(d.redirect_url); return; }
      if (d.state === 'idle') {
        btnStart.style.display = 'none'; btnEnd.style.display = 'none';
        show('No active session. Please go back to the start page or tell the researcher.', 'err');
        return;
      }
      if (d.camera_result === 'pending') {
        btnStart.style.display = 'block'; btnStart.disabled = true; btnEnd.style.display = 'none';
        hideBar();  // say nothing while the camera starts / calibrates
      } else if (d.camera_result === 'success') {
        btnStart.style.display = 'none'; btnEnd.style.display = 'block'; btnEnd.disabled = busy;
        bar.style.display = ''; msg.textContent = ''; msg.style.display = 'none';  // only the End Reading button
      } else if (d.camera_result === 'failed') {
        btnStart.style.display = 'block'; btnStart.disabled = busy; btnEnd.style.display = 'none';
        show('✗ Camera failed to start: ' + (REASONS[d.camera_detail] || d.camera_detail) +
             '. Try again, or ring the bell for the researcher.', 'err');
      } else {
        btnStart.style.display = 'block'; btnStart.disabled = busy; btnEnd.style.display = 'none';
        show('When you are ready, click Start Reading.', 'info');
      }
    }
    async function poll() {
      try { render(await call('GET', '/api/reading/status')); }
      catch (e) { show('Cannot reach the study server. Please ring the bell for the researcher.', 'err'); }
    }
    btnStart.addEventListener('click', async function () {
      busy = true; btnStart.disabled = true;
      hideBar();  // say nothing while the camera starts / calibrates
      try { render(await call('POST', '/api/reading/start', {source: source})); }
      catch (e) { show('✗ Could not reach the study server. Please ring the bell for the researcher.', 'err'); btnStart.disabled = false; }
      busy = false;
    });
    btnEnd.addEventListener('click', function () { back.style.display = 'flex'; });
    $('sc-cancel').addEventListener('click', function () { back.style.display = 'none'; });
    $('sc-confirm').addEventListener('click', async function () {
      back.style.display = 'none'; busy = true; btnEnd.disabled = true;
      show('Ending the session…', 'info');
      for (var i = 0; i < 3; i++) {
        try {
          var d = await call('POST', '/api/reading/end', {source: source, client_ts: new Date().toISOString()});
          if (d && d.ok) { render(d); return; }
        } catch (e) {}
        await new Promise(function (r) { setTimeout(r, 1500); });
      }
      busy = false; btnEnd.disabled = false;
      show('✗ Could not end the session. Please ring the bell for the researcher.', 'err');
    });
    poll();
    return setInterval(poll, 1500);
  }
  
  host._scTimer = studyControls(shadow, window.location.origin, "extension_overlay");
})();
