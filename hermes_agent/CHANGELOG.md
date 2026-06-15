# Changelog

All notable changes to the Hermes Agent Home Assistant add-on are documented here.

The format follows the spirit of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match the add-on `version` in `config.yaml`.

## [Unreleased]

## [1.1.2] - 2026-06-15

### Added

- Add this Home Assistant add-on changelog so users can see what changed before updating.

### Changed

- Clarify persistent storage documentation: container `/config` maps to the add-on's private `addon_config` storage, not the normal Home Assistant Core `/config` folder.
- Document where to find the same storage from HAOS/Samba via the `addon_configs` share.
- Correct the install-marker name shown in the storage tree for multi-profile aware versions.

### Fixed

- Fix the dashboard Chat tab behind Home Assistant Ingress by preserving WebSocket upgrades through the nginx `/dashboard/api/` proxy.
- Forward `Upgrade` and mapped `Connection` headers for dashboard API routes while keeping normal REST calls on the same path sane.
- Add regression coverage for primary and multi-profile dashboard API routes across Ingress, direct HTTP, and direct HTTPS render modes.

### Verified

- `git diff --check` - OK.
- `python3 -m pytest -q tests/test_dashboard_ingress_patches.py` - 18 passed.
- `python3 -m pytest -q` - 43 passed, 1 skipped.
- `bash -n hermes_agent/nginx-render.sh hermes_agent/run.sh` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py` - OK.
- Live Home Assistant local DEV add-on smoke - add-on started, `nginx -t` passed, dashboard returned HTTP 200 through Ingress, and `/dashboard/api/pty`, `/dashboard/api/ws`, and `/dashboard/api/events` returned `HTTP/1.1 101 Switching Protocols`.

## [1.1.1] - 2026-06-04

### Fixed

- Fix dashboard startup regression by executing the installed dashboard patch helper directly instead of relying on a non-guaranteed `python` alias.
- Use the helper's `#!/usr/bin/env python3` shebang, matching the Python executable installed in the add-on image.
- Add a regression assertion so startup does not reintroduce the bare `python` dependency.

### Verified

- `python3 -m unittest discover -s tests` - 43 tests OK, 1 skipped.
- `python3 -m py_compile hermes_agent/dashboard-patches.py tests/test_dashboard_ingress_patches.py tests/test_multi_profile.py` - OK.
- `bash -n hermes_agent/run.sh hermes_agent/profile-init.sh hermes_agent/nginx-render.sh` - OK.

## [1.1.0] - 2026-06-01

### Added

- Add multi-profile mode via `profiles`; the first profile keeps the existing root URLs and additional profiles are exposed under `/profile/<name>/...`.
- Add Home Assistant-compatible `profile_env_vars` for per-profile `.env` overrides.
- Render nginx routing per profile for dashboard, terminal, and API access.
- Add profile initialization support and per-profile tmux/session naming.
- Add regression tests for multi-profile behavior.

### Changed

- Keep Hermes Gateway in the foreground under Home Assistant s6 so Supervisor tracks the add-on lifecycle correctly.
- Update README and translations for multi-profile support.

### Notes

- Includes the v1.0.8 gateway foreground fix that was tagged but not published as a GitHub Release.
- Thanks to @imcvampire for the multi-profile work.

### Verified

- `python3 -m unittest discover -s tests -q` - 43 tests OK, 1 skipped.
- `git diff --check` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py tests/test_dashboard_ingress_patches.py tests/test_multi_profile.py` - OK.
- `bash -n hermes_agent/run.sh hermes_agent/nginx-render.sh hermes_agent/profile-init.sh` - OK.

## [1.0.8] - 2026-05-28

### Fixed

- Keep Hermes Gateway in the foreground under Home Assistant s6 supervision.
- Ensure Supervisor can correctly track the add-on lifecycle instead of losing sight of a backgrounded gateway process.
- Add a regression test for the foreground gateway behavior.

### Notes

- Tagged as v1.0.8 and later included in the v1.1.0 GitHub Release notes.

## [1.0.7] - 2026-05-17

### Fixed

- Fix dashboard asset loading behind Home Assistant Ingress for modern Hermes dashboard builds.
- Add an add-on-controlled `import.meta.url` fallback for dashboard base-path detection so API, plugin, router, and asset paths keep working when Home Assistant Ingress uses a long random prefix.
- Force Vite to emit relative dashboard asset URLs with `base: "./"` for modern and legacy dashboard sources.
- Rebuild stale dashboard bundles when `index.html` still contains absolute `/assets/...` references.
- Keep legacy dashboard compatibility patches and partial-patch repair behavior.
- Avoid polling `/v1/health` when the API server is disabled, preventing misleading nginx `connect() failed` log noise.

### Changed

- Update the landing page so `/v1/health` is shown as optional API server health, not Gateway health.

### Notes

- Thanks to @imcvampire for the logs and browser Network screenshot that exposed the remaining asset-path failure.

### Verified

- `python3 -m pytest -q` - 13 passed.
- `git diff --check` - OK.
- `python3 -m py_compile hermes_agent/dashboard-patches.py` - OK.
- `bash -n hermes_agent/run.sh` - OK.
- Independent code review passed with no blocking findings.

## [1.0.6] - 2026-05-16

### Fixed

- Fix Home Assistant add-on startup failure introduced in v1.0.5 when direct HTTP/HTTPS ports are enabled.
- Move `map_hash_bucket_size 128;` into the main nginx template before the first `map` block.
- Remove the late duplicate directive from the direct-ports include.
- Add a regression test that locks the directive ordering.

### Verified

- Verified on a real Home Assistant add-on installation with a fresh `/config` runtime.
- Upgrade/start path reached `nginx reloaded` and `All services started`.
- Ingress landing page, dashboard, dashboard deep links, dashboard assets, dashboard API, and `/v1/health` returned 200.

### Notes

- Thanks to @imcvampire for reporting the post-v1.0.5 dashboard startup problem.

## [1.0.5] - 2026-05-16

### Fixed

- Fix Home Assistant add-on startup failures caused by fragile dashboard source patching after upstream Hermes dashboard changes.
- Move dashboard source rewriting into a tested helper script instead of multi-expression `sed` calls.
- Skip source patches for modern Hermes dashboards that already support proxy prefixes via `X-Forwarded-Prefix`.
- Repair partially patched v1.0.4 dashboard sources from failed previous starts.
- Keep legacy fallback patches for older root-path-only Hermes dashboard sources.
- Forward the dashboard prefix from Home Assistant Ingress or custom reverse proxies to Hermes.
- Add regression tests for modern upstream, legacy fallback, and the `sed` delimiter failure class.

### Notes

- Thanks to @imcvampire for pinpointing the `sed` delimiter crash in PR #4.

## [1.0.4] - 2026-04-24

### Fixed

- Pass a loopback `Host` header to the upstream dashboard when proxying through nginx.
- Improve dashboard reverse-proxy compatibility after the v1.0.3 dashboard hardening work.

### Notes

- Tagged as v1.0.4 but not published as a separate GitHub Release.

## [1.0.3] - 2026-04-20

### Fixed

- Make the dashboard work fully behind Home Assistant Ingress, direct `/dashboard/` mounts, and custom reverse proxies.
- Replace the previous `BASE = "."` workaround with runtime base-path computation from `import.meta.url`.
- Patch `api.ts`, `usePlugins.ts`, and `vite.config.ts` idempotently in-container so API calls, plugin assets, router paths, and built HTML assets stay correctly prefixed.
- Fix the landing-page Gateway indicator by switching the probe to the public `/v1/health` endpoint instead of authenticated `/v1/models`.
- Protect direct-port `/dashboard/api/*` routes with the dashboard session token while keeping `/dashboard/api/status` public for health/status use.

### Added

- Add a README Security Model section covering Home Assistant Ingress, direct-port dashboard auth, and `/v1/*` API Bearer auth.

## [1.0.2] - 2026-04-20

### Fixed

- Restore dashboard compatibility with Hermes Agent v0.10.0 after upstream added a global auth middleware requiring an ephemeral Bearer token on `/api/` endpoints.
- Read the dashboard token from the HTML on startup and inject it server-side through nginx so Basic Auth and Home Assistant Ingress do not strip or consume it.

## [1.0.1] - 2026-04-20

### Added

- Add Web Dashboard support as a new tab alongside Hermes and Terminal through the Home Assistant sidebar.
- Add direct HTTP/HTTPS dashboard exposure through the `enable_dashboard` toggle.
- Expose dashboard pages for status, sessions, analytics, logs, cron, skills, config, and API keys.

### Fixed

- Fix Supervisor loop behavior by tracking the gateway PID correctly and always restarting the gateway, preventing container death on manual gateway restart.

## [1.0.0] - 2026-03-27

### Added

- Initial stable Home Assistant add-on packaging for Hermes Agent.
- Persistent AI agent with SQLite FTS5 long-term memory, self-improving skills, and multi-platform messaging.
- OpenAI-compatible API under `/v1/` for frontends such as Open WebUI and SillyTavern.
- Persistent web terminals backed by tmux through the Home Assistant sidebar.
- HTTP and HTTPS direct LAN access with auto-generated TLS certificates and optional Basic Auth.
- Full persistence for source code, venv, Homebrew, npm, Go, and agent data across add-on updates.
- Editable install so the agent can read and modify its own source code.
- Plugin architecture for custom tools, commands, and hooks.
- Container toolchain with Go 1.26, Node.js 22, Python 3.11, Chromium, GitHub CLI, Homebrew, uv, and common development tools.
- Support for `amd64` and `aarch64`.
