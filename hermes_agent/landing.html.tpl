<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Agent</title>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;height:100%;overflow:hidden;background:#111111;color:#e6edf3;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  body{display:flex;flex-direction:column}
  .titlebar{display:flex;align-items:center;gap:8px;padding:4px 8px;background:#1c1c1c;border-bottom:1px solid #1f2937;min-height:32px;flex-shrink:0}
  .titlebar .version{color:#ffd700;font-size:12px;white-space:nowrap}
  .titlebar .buttons{display:flex;gap:6px;margin:0 auto}
  .titlebar .status{display:flex;gap:6px;font-size:11px;color:#9ca3af}
  .btn{background:#009ac7;color:white;border:0;border-radius:6px;padding:4px 10px;cursor:pointer;text-decoration:none;display:inline-block;font-size:12px}
  .btn.secondary{background:#334155}
  .btn.green{background:#0da035}
  .btn:hover{filter:brightness(1.15)}
  .btn.active{background:#f36d00}
  .term{flex:1;overflow:hidden;position:relative}
  .term iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:0;background:black}
  .term iframe.hidden{display:none}
  .term .no-services{display:none;justify-content:center;align-items:center;height:100%;color:#9ca3af;font-size:14px}
</style>
</head>
<body>

<div class="titlebar">
  <span class="version">%%HERMES_VERSION%%</span>
  <div class="buttons">
    <button class="btn active" id="btnHermes" onclick="setMode('hermes')">Hermes</button>
    <button class="btn secondary" id="btnDashboard" onclick="setMode('dashboard')" style="display:none">Dashboard</button>
    <button class="btn secondary" id="btnTerminal" onclick="setMode('terminal')">Terminal</button>
    <a class="btn green" href="./cert/ca.crt" download="hermes-agent-ca.crt">CA Cert</a>
    <a class="btn small" id="btnAppInfo" href="/config/app/%%ADDON_SLUG%%/info" target="_top" onclick="document.querySelectorAll('iframe').forEach(function(f){f.remove()})" style="display:none">App Info</a>
  </div>
  <div class="status">
    <span id="statusGateway">&#x23F3; Gateway</span>
    <span id="statusDashboard" style="display:none">&#x23F3; Dashboard</span>
    <span id="statusSecure">&#x1F512;</span>
  </div>
</div>

<div class="term">
  <iframe id="frameHermes" src="./hermes/" title="Hermes Agent"></iframe>
  <iframe id="frameDashboard" src="" title="Dashboard" class="hidden"></iframe>
  <iframe id="frameTerminal" src="./terminal/" title="Terminal" class="hidden"></iframe>
  <div id="noServices" class="no-services">These services are available via the Home Assistant sidebar.</div>
</div>

<script>
(function() {
  var frameHermes = document.getElementById('frameHermes');
  var frameDashboard = document.getElementById('frameDashboard');
  var frameTerminal = document.getElementById('frameTerminal');
  var btnHermes = document.getElementById('btnHermes');
  var btnDashboard = document.getElementById('btnDashboard');
  var btnTerminal = document.getElementById('btnTerminal');
  var current = 'hermes';
  var dashboardLoaded = false;

  var showDashboard = %%SHOW_DASHBOARD%%;
  if (showDashboard) {
    btnDashboard.style.display = '';
  }

  window.setMode = function(mode) {
    if (mode === current) return;
    current = mode;
    frameHermes.className = mode === 'hermes' ? '' : 'hidden';
    frameDashboard.className = mode === 'dashboard' ? '' : 'hidden';
    frameTerminal.className = mode === 'terminal' ? '' : 'hidden';
    btnHermes.className = mode === 'hermes' ? 'btn active' : 'btn secondary';
    btnDashboard.className = mode === 'dashboard' ? 'btn active' : 'btn secondary';
    btnTerminal.className = mode === 'terminal' ? 'btn active' : 'btn secondary';
    if (mode === 'dashboard' && !dashboardLoaded) {
      frameDashboard.src = './dashboard/';
      dashboardLoaded = true;
    }
  };

  // Detect context: iframe = HA ingress, top-level = direct port access
  try { var inIframe = window !== window.top; } catch(e) { var inIframe = true; }
  if (inIframe) {
    // Ingress: always show everything
    document.getElementById('btnAppInfo').style.display = '';
  } else {
    // Direct ports: respect config flags independently
    var showTerminal = %%SHOW_TERMINAL%%;
    var showDashboardPorts = %%SHOW_DASHBOARD_PORTS%%;
    if (!showTerminal) {
      btnHermes.style.display = 'none';
      btnTerminal.style.display = 'none';
      frameHermes.src = '';
      frameHermes.className = 'hidden';
      frameTerminal.src = '';
    }
    if (!showDashboardPorts) {
      btnDashboard.style.display = 'none';
    }
    if (!showTerminal && !showDashboardPorts) {
      document.getElementById('noServices').style.display = 'flex';
    }
  }

  var s = document.getElementById('statusSecure');
  s.textContent = window.isSecureContext ? '\u2705 Secure' : '\u26A0\uFE0F Not secure';

  var g = document.getElementById('statusGateway');
  fetch('./v1/models', {cache:'no-store'}).then(function(r) {
    g.textContent = r.ok ? '\u2705 Gateway' : '\uD83D\uDCA4 Gateway';
  }).catch(function() {
    g.textContent = '\uD83D\uDCA4 Gateway';
  });

  if (showDashboard) {
    var d = document.getElementById('statusDashboard');
    d.style.display = '';
    fetch('./dashboard/api/status', {cache:'no-store'}).then(function(r) {
      d.textContent = r.ok ? '\u2705 Dashboard' : '\uD83D\uDCA4 Dashboard';
    }).catch(function() {
      d.textContent = '\uD83D\uDCA4 Dashboard';
    });
  }
})();
</script>
</body>
</html>
