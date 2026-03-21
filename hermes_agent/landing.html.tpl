<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Agent</title>
<style>
  :root { --bg: #1a1b26; --fg: #c0caf5; --accent: #7aa2f7; --card: #24283b; --border: #414868; --green: #9ece6a; --red: #f7768e; --dim: #565f89; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--fg); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .container { max-width: 480px; width: 100%; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .subtitle { color: var(--dim); margin-bottom: 1.5rem; font-size: 0.9rem; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
  .card h2 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin-bottom: 0.75rem; }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 0.25rem 0; }
  .row .label { color: var(--dim); font-size: 0.9rem; }
  .row .value { font-size: 0.9rem; }
  .status-ok { color: var(--green); }
  .status-off { color: var(--red); }
  a.btn { display: block; text-align: center; background: var(--accent); color: var(--bg); text-decoration: none; padding: 0.75rem; border-radius: 6px; font-weight: 600; font-size: 0.95rem; margin-top: 1rem; transition: opacity 0.15s; }
  a.btn:hover { opacity: 0.85; }
  a.btn.disabled { background: var(--border); color: var(--dim); pointer-events: none; }
</style>
</head>
<body>
<div class="container">
  <h1>Hermes Agent</h1>
  <p class="subtitle">NousResearch Hermes Agent for Home Assistant</p>

  <div class="card">
    <h2>Status</h2>
    <div class="row"><span class="label">Version</span><span class="value">%%HERMES_VERSION%%</span></div>
    <div class="row"><span class="label">Gateway</span><span class="value status-ok">Running</span></div>
    <div class="row"><span class="label">Terminal</span><span class="value %%TERMINAL_STATUS_CLASS%%">%%TERMINAL_STATUS%%</span></div>
  </div>

  <div class="card">
    <h2>Quick Start</h2>
    <div class="row"><span class="label">1. Open terminal below</span></div>
    <div class="row"><span class="label">2. Run <code>hermes setup</code></span></div>
    <div class="row"><span class="label">3. Configure model &amp; platforms</span></div>
  </div>

  <a class="btn %%TERMINAL_BTN_CLASS%%" href="./terminal/">Open Terminal</a>
</div>
</body>
</html>
