<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Agent</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px;background:#0b0f14;color:#e6edf3}
  .card{max-width:1100px;margin:0 auto;background:#111827;border:1px solid #1f2937;border-radius:12px;padding:16px}
  .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
  .btn{background:#2563eb;color:white;border:0;border-radius:10px;padding:10px 14px;cursor:pointer;text-decoration:none;display:inline-block;font-size:14px}
  .btn.secondary{background:#334155}
  .btn.green{background:#059669}
  .btn:hover{filter:brightness(1.15)}
  .status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:12px 0}
  .status-item{padding:10px 14px;border-radius:10px;background:#0d1117;border:1px solid #1f2937;font-size:14px;display:flex;align-items:center;gap:8px}
  .term{margin-top:14px;height:60vh;min-height:360px;border:1px solid #1f2937;border-radius:10px;overflow:hidden}
  iframe{width:100%;height:100%;border:0;background:black}
  .muted{color:#9ca3af;font-size:13px;margin-top:8px}
</style>
</head>
<body>
<div class="card">
  <h2 style="margin:0 0 4px">Hermes Agent</h2>
  <p style="color:#9ca3af;font-size:14px;margin:0 0 12px">%%HERMES_VERSION%%</p>

  <div class="status-grid">
    <div class="status-item" id="statusGateway">
      <span>&#x23F3;</span><span>Gateway: checking&hellip;</span>
    </div>
    <div class="status-item" id="statusSecure">
      <span>&#x1F512;</span><span>Secure context: checking&hellip;</span>
    </div>
  </div>

  <div class="row">
    <a class="btn" href="./hermes/" target="_self">Open Hermes</a>
    <a class="btn secondary" href="./terminal/" target="_self">Open Terminal</a>
    <a class="btn green" href="./cert/ca.crt">Download CA Certificate</a>
  </div>

  <div class="term">
    <iframe src="./hermes/" title="Hermes Agent"></iframe>
  </div>

  <p class="muted">
    Terminal is backed by tmux &mdash; sessions persist across reconnects.
    API endpoint: <code>/v1/chat/completions</code>
  </p>
</div>

<script>
(function() {
  var s = document.getElementById('statusSecure');
  if (window.isSecureContext) {
    s.innerHTML = '<span>&#x2705;</span><span>Secure context: <b>yes</b></span>';
  } else {
    s.innerHTML = '<span>&#x26A0;&#xFE0F;</span><span>Secure context: <b>no</b></span>';
  }
  var g = document.getElementById('statusGateway');
  fetch('./v1/models', {cache:'no-store'}).then(function(r) {
    g.innerHTML = r.ok
      ? '<span>&#x2705;</span><span>Gateway API: <b>running</b></span>'
      : '<span>&#x26A0;&#xFE0F;</span><span>Gateway API: <b>error ' + r.status + '</b></span>';
  }).catch(function() {
    g.innerHTML = '<span>&#x26A0;&#xFE0F;</span><span>Gateway API: <b>not running</b></span>';
  });
})();
</script>
</body>
</html>
