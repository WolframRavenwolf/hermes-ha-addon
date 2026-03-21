<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Agent</title>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;height:100%;background:#0b0f14;color:#e6edf3;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
  body{display:flex;flex-direction:column;padding:8px;gap:8px}
  .header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;min-height:40px}
  .header h2{margin:0;font-size:16px;white-space:nowrap}
  .header .version{color:#9ca3af;font-size:13px}
  .status{display:flex;gap:8px;font-size:13px;align-items:center;margin-left:auto}
  .status span{padding:4px 10px;border-radius:8px;background:#0d1117;border:1px solid #1f2937}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .btn{background:#2563eb;color:white;border:0;border-radius:8px;padding:7px 12px;cursor:pointer;text-decoration:none;display:inline-block;font-size:13px}
  .btn.secondary{background:#334155}
  .btn.green{background:#059669}
  .btn.small{padding:5px 10px;font-size:12px}
  .btn:hover{filter:brightness(1.15)}
  .btn.active{outline:2px solid #60a5fa;outline-offset:1px}
  .term{flex:1;min-height:200px;border:1px solid #1f2937;border-radius:8px;overflow:hidden;position:relative}
  .term iframe{position:absolute;top:0;left:0;width:100%;height:100%;border:0;background:black}
  .term iframe.hidden{display:none}
  body.maximized .header,body.maximized .toolbar{display:none}
  body.maximized .term{border:0;border-radius:0}
  body.maximized{padding:0;gap:0}
</style>
</head>
<body>

<div class="header">
  <h2>Hermes Agent</h2>
  <span class="version">%%HERMES_VERSION%%</span>
  <div class="status">
    <span id="statusGateway">&#x23F3; Gateway: checking&hellip;</span>
    <span id="statusSecure">&#x1F512; checking&hellip;</span>
  </div>
</div>

<div class="toolbar">
  <button class="btn active" id="btnHermes" onclick="setMode('hermes')">Hermes</button>
  <button class="btn secondary" id="btnTerminal" onclick="setMode('terminal')">Terminal</button>
  <button class="btn small secondary" id="btnMax" onclick="toggleMax()">&#x26F6; Maximize</button>
  <a class="btn green small" href="./cert/ca.crt" download="hermes-agent-ca.crt">&#x1F512; CA Cert</a>
</div>

<div class="term" id="termContainer">
  <iframe id="frameHermes" src="./hermes/" title="Hermes Agent"></iframe>
  <iframe id="frameTerminal" src="./terminal/" title="Terminal" class="hidden"></iframe>
</div>

<script>
(function() {
  var frameHermes = document.getElementById('frameHermes');
  var frameTerminal = document.getElementById('frameTerminal');
  var btnHermes = document.getElementById('btnHermes');
  var btnTerminal = document.getElementById('btnTerminal');
  var current = 'hermes';

  window.setMode = function(mode) {
    if (mode === current) return;
    current = mode;
    frameHermes.className = mode === 'hermes' ? '' : 'hidden';
    frameTerminal.className = mode === 'terminal' ? '' : 'hidden';
    btnHermes.className = mode === 'hermes' ? 'btn active' : 'btn secondary';
    btnTerminal.className = mode === 'terminal' ? 'btn active' : 'btn secondary';
  };

  window.toggleMax = function() {
    document.body.classList.toggle('maximized');
  };

  // Escape key exits maximize
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && document.body.classList.contains('maximized')) {
      document.body.classList.remove('maximized');
    }
  });

  // Status checks
  var s = document.getElementById('statusSecure');
  s.innerHTML = window.isSecureContext
    ? '&#x2705; Secure'
    : '&#x26A0;&#xFE0F; Not secure';

  var g = document.getElementById('statusGateway');
  fetch('./v1/models', {cache:'no-store'}).then(function(r) {
    g.innerHTML = r.ok
      ? '&#x2705; Gateway API'
      : '&#x1F4A4; Gateway API (not enabled)';
  }).catch(function() {
    g.innerHTML = '&#x1F4A4; Gateway API (not running)';
  });
})();
</script>
</body>
</html>
