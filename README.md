# Hermes Agent Home Assistant Add-on

> The self-improving AI agent built by Nous Research. Home Assistant add-on by Wolfram Ravenwolf.

[![Hermes Agent running in Home Assistant](hermes-ha-addon.png)](https://github.com/WolframRavenwolf/hermes-ha-addon/releases/download/v1.0.0/hermes-ha-addon.mp4)

[Hermes Agent](https://hermes-agent.nousresearch.com/) packaged as a [Home Assistant](https://home-assistant.io/) add-on/app. Persistent AI agent with memory, self-improving skills, multi-platform messaging, and a plugin architecture for custom tools.

## Features

- **Persistent memory** -- SQLite FTS5 long-term memory that survives restarts
- **Self-improving skills** -- agent learns and creates new capabilities over time
- **Multi-platform messaging** -- Telegram, Discord, WhatsApp, and more via the gateway
- **OpenAI-compatible API** -- connect any chat frontend ([Open WebUI](https://github.com/open-webui/open-webui), [SillyTavern](https://github.com/SillyTavern/SillyTavern), etc.) via `/v1/`
- **Plugin architecture** -- custom tools, commands, and hooks without forking
- **Self-modifiable source** -- editable install lets the agent read and modify its own code
- **Web dashboard** -- browser-based management UI for config, API keys, sessions, analytics, logs, cron, and skills
- **Persistent web terminal** -- full CLI access via tmux-backed ttyd through the Home Assistant sidebar
- **HTTP + HTTPS** -- direct LAN access with auto-generated TLS certificates
- **Full persistence** -- source code, venv, Homebrew, npm, Go, and all agent data survive add-on updates

## Installation

1. Add this repository to Home Assistant: **Settings > Apps > Install app > ⋮ > Repositories**
2. Paste the repository URL and click **Add**
3. Find **Hermes Agent** in the store and click **Install**
4. Start the add-on and open **Hermes Agent** from the sidebar
5. The setup wizard runs automatically -- configure your model and API keys

## Configuration

Add-on-level options are configured in the Home Assistant UI (Settings > Apps > Hermes Agent > Configuration):

| Option                | Default                                            | Description                                                                     |
| --------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------- |
| `git_url`             | `https://github.com/NousResearch/hermes-agent.git` | Git repository URL (clear to reset to default)                                  |
| `git_ref`             |                                                    | Branch, tag, or commit (empty = repo's default branch)                          |
| `git_token`           |                                                    | Token for private repos + exported as `GITHUB_TOKEN` for gh CLI                 |
| `auto_update`         | `false`                                            | Pull latest changes on restart (preserves local modifications)                  |
| `hass_url`            | `http://homeassistant.local:8123`                  | Home Assistant URL for API access                                               |
| `homeassistant_token` |                                                    | Long-lived access token for Home Assistant API integration                      |
| `enable_dashboard`    | `false`                                            | Enable web dashboard on direct HTTP/HTTPS ports                                 |
| `enable_terminal`     | `false`                                            | Enable web terminal on direct HTTP/HTTPS ports                                  |
| `enable_api`          | `false`                                            | Enable the OpenAI-compatible API server on direct HTTP/HTTPS ports              |
| `access_password`     |                                                    | Password for HTTP/HTTPS access (web terminal). Also used as the server API key  |
| `env_vars`            | `OPENROUTER_API_KEY` (example)                     | Hermes .env variables — written to each profile's `.env` on each start          |
| `hermes_home`         | `.hermes`                                          | Single-profile mode: agent profile directory (relative to ~). Ignored if `profiles` is non-empty |
| `profiles`            | `[]`                                               | Multi-profile mode: list of profile directories run concurrently. First entry is the primary |
| `profiles_base`       | `.hermes/profiles`                                 | Default parent dir for non-dotted profile names. Entries starting with `.` are taken as-is (legacy `.hermes` keeps working). Set to empty to disable the prefix |
| `profile_env_vars`    | `[]`                                               | Per-profile `.env` overrides: each entry is `{profile, name, value}` where `profile` matches a directory in `profiles` |

API keys can be configured in two places: `env_vars` above (convenient, via Home Assistant UI) or each profile's `.env` directly (full list, via terminal or `hermes setup`). Non-empty top-level `env_vars` are written to every profile's `.env` on each start, overriding existing entries. `profile_env_vars` entries layer on top of the top-level set for the profile whose directory matches `profile`.

### Running multiple profiles concurrently

Set `profiles` to run several Hermes instances in the same add-on. Non-dotted names are placed under `profiles_base` (default `.hermes/profiles`) to match upstream's [profile layout](https://hermes-agent.nousresearch.com/docs/user-guide/profiles); entries starting with `.` are taken as-is. Per-profile env overrides live in a flat `profile_env_vars` list (Home Assistant Supervisor only allows nested objects two levels deep):

```yaml
profiles:
  - .hermes              # primary, kept as-is (leading "."): /config/.hermes
  - amy                  # bare name, prefixed by profiles_base: /config/.hermes/profiles/amy
  - bob                  # same: /config/.hermes/profiles/bob
profile_env_vars:
  # `profile` matches the entry above exactly (use the string you put in `profiles`).
  - profile: amy
    name: OPENROUTER_API_KEY
    value: amy-only-key
  - profile: amy
    name: SOME_AMY_VAR
    value: amy-special
```

A single shared install at `~/.hermes/hermes-agent` (clone + venv) backs every profile — only the per-profile `.env`, `config.yaml`, `SOUL.md`, sessions, memories, and logs live under each profile's directory.

The first entry is the **primary** — it keeps the existing root URLs (`/hermes/`, `/dashboard/`, `/terminal/`, `/v1/`). Each additional profile is exposed under `/profile/<name>/...`. Per-profile ports allocate from a base + index (`8642`, `49269`, `49369`, `49469`).

**Upgrade note:** If you already used bare profile names with earlier multi-profile add-on versions, existing flat directories such as `/config/amy` are preserved automatically when the new `.hermes/profiles/amy` directory does not exist yet. To keep flat paths intentionally, set `profiles_base` to an empty string. To adopt the upstream-style layout, move the profile data to `/config/.hermes/profiles/<name>`.

**Note:** Values added via `env_vars` are not removed or reset from `.env` when cleared or removed in the Home Assistant UI -- edit each profile's `.env` directly to remove them.

Hermes-internal configuration (model, platforms, memory, tools) is managed via the terminal:

```bash
hermes setup          # Interactive first-time setup
hermes config edit    # Edit config directly
hermes doctor         # Diagnostics and dependency check
hermes gateway setup  # Configure messaging platforms
```

## Access

The add-on is accessible via the **Home Assistant Sidebar** (landing page with embedded terminal, mode switching, and status display) and, optionally, via direct URLs. Replace `homeassistant.local` with your Home Assistant hostname or IP.

Direct HTTP/HTTPS access requires `enable_dashboard` (**Enable Web Dashboard**), `enable_terminal` (**Enable Web Terminal**), and/or `enable_api` (**Enable API Server**) in the add-on configuration. Set an **Access Password** to secure these ports (username: `hermes`).

### Web Terminal & Dashboard

| URL                                            | Description                                                              |
| ---------------------------------------------- | ------------------------------------------------------------------------ |
| `https://homeassistant.local:8443/hermes/`     | Hermes Agent (starts hermes, crash drops to shell)                       |
| `https://homeassistant.local:8443/dashboard/`  | Web dashboard (config, API keys, sessions, analytics, logs)              |
| `https://homeassistant.local:8443/terminal/`   | Shell terminal (non-login shell -- plain shell, hermes not auto-started) |
| `https://homeassistant.local:8443/cert/ca.crt` | CA certificate download (for trusting self-signed HTTPS)                 |

### OpenAI-compatible API

Connect [Open WebUI](https://github.com/open-webui/open-webui), [SillyTavern](https://github.com/SillyTavern/SillyTavern), etc.

OpenAI-compatible API access requires `enable_api` (**Enable API Server**) in the add-on configuration. The **Access Password** doubles as the server API key.

| URL / Endpoint                                                | Method | Description                                       |
| ------------------------------------------------------------- | ------ | ------------------------------------------------- |
| `https://homeassistant.local:8443/v1/chat/completions`        | POST   | Chat Completions (stateless)                      |
| `https://homeassistant.local:8443/v1/responses`               | POST   | Responses API (stateful via previous_response_id) |
| `https://homeassistant.local:8443/v1/responses/{response_id}` | GET    | Retrieve a stored response                        |
| `https://homeassistant.local:8443/v1/responses/{response_id}` | DELETE | Delete a stored response                          |
| `https://homeassistant.local:8443/v1/models`                  | GET    | List available models                             |
| `https://homeassistant.local:8443/health`                     | GET    | Health check                                      |

### Ports

Both ports are configurable in the Home Assistant add-on network settings. Use the HTTPS port (8443) with an access password for secure access. The HTTP port (8080) is intended for TLS-terminating reverse proxies and disabled by default.

| Port     | Description                                          |
| -------- | ---------------------------------------------------- |
| **8080** | HTTP access (all URLs above, replace 8443 with 8080) |
| **8443** | HTTPS access (TLS with self-signed cert)             |

### SSH

Via Home Assistant host + docker exec, no SSH server in container required. Port 22222 is the default for the Advanced SSH & Web Terminal add-on (adjust if yours differs).

```bash
# Plain shell (new session, not shared with web terminal)
ssh -p 22222 -t root@homeassistant.local "docker exec -it \$(docker ps -qf name=hermes_agent) bash"

# Hermes (shared tmux session — same as Home Assistant sidebar "Hermes" tab)
# Replace <profile> with the sanitized profile name (e.g. "hermes" for the primary `.hermes`, "amy" for `amy`).
ssh -p 22222 -t root@homeassistant.local "docker exec -it \$(docker ps -qf name=hermes_agent) tmux -L hermes-<profile> -u new -A -s hermes-<profile> /usr/local/bin/start-hermes"

# Terminal (shared tmux session — same as Home Assistant sidebar "Terminal" tab)
ssh -p 22222 -t root@homeassistant.local "docker exec -it \$(docker ps -qf name=hermes_agent) tmux -L terminal-<profile> -u new -A -s terminal-<profile> bash"

# Copy files (e.g. upload a custom SOUL.md — works even when add-on is stopped)
scp -P 22222 SOUL.md "root@homeassistant.local:/mnt/data/supervisor/addon_configs/*hermes_agent/.hermes/"
```

### TLS Certificates

On first start, self-signed certificates are auto-generated in `~/.certs/`. To trust the HTTPS connection and avoid browser warnings, install the CA certificate on your devices:

1. Click **CA Cert** in the add-on titlebar (or download from `/cert/ca.crt`)
2. Install the certificate:
   - **Windows**: Double-click the .crt file → Install Certificate → Local Machine → Trusted Root Certification Authorities
   - **macOS**: Double-click → Keychain Access → set to "Always Trust"
   - **Android**: Settings → Security → Install certificate → CA certificate → select the file
   - **iOS**: Open the .crt file → Install Profile → Settings → General → About → Certificate Trust Settings → enable
   - **Linux**: Copy to `/usr/local/share/ca-certificates/` and run `sudo update-ca-certificates`

To use your own certificates instead of self-signed:

1. Stop the add-on
2. Replace `~/.certs/server.crt` and `~/.certs/server.key` with your own
3. Optionally replace `~/.certs/ca.crt` if you have a custom CA
4. Start the add-on

The add-on will use existing certificates and never overwrite them.

## Security Model

Authentication layers differ by access path:

- **Home Assistant Ingress** (sidebar): protected by Home Assistant's own session auth. All services — Hermes, Terminal, Dashboard — are reachable once you're logged in to HA.
- **Direct HTTP/HTTPS Ports** (8080/8443): two-layer auth protects the web UIs.
  1. **Basic Auth** (username `hermes`, password = `access_password`) gates the landing page, Terminal, and Dashboard HTML.
  2. **Session Token** (ephemeral, rotates on every add-on restart) gates dashboard API calls. The token is injected into the dashboard HTML on load — only clients who successfully loaded the page via Basic Auth ever see it. Requests to `/dashboard/api/*` without a matching Bearer token return 401. Only `/dashboard/api/status` is public (it mirrors Hermes' own whitelist and powers the landing page health indicator). If the dashboard process is restarted without restarting the add-on, the nginx-side token cache goes stale — restart the add-on to re-sync.
- **OpenAI-compatible API** (`/v1/*`): Bearer token authentication. The `access_password` doubles as the API key, passed as `Authorization: Bearer <password>`.

If you expose direct ports to the internet, place a network-perimeter gate (firewall, VPN, reverse proxy with stronger auth) in front — Basic Auth alone is not brute-force resistant.

## Architecture

Four services in a Debian Bookworm container:

1. **Hermes Gateway** (`hermes gateway run`) -- persistent AI agent daemon with OpenAI-compatible API server and messaging platform connectors. Logs visible in the Home Assistant add-on log and in `~/.hermes/logs/gateway.log`.
2. **Hermes Dashboard** (`hermes dashboard`) -- browser-based management UI (FastAPI + React) for config, API keys, sessions, analytics, logs, cron jobs, and skills.
3. **ttyd** (×2 per profile) -- web terminals backed by persistent tmux sessions (`hermes-<name>` + `terminal-<name>`)
4. **nginx** -- HTTP, HTTPS, and Home Assistant ingress proxy routing to dashboard + terminal + API. Multi-profile setups serve `/profile/<name>/...` for non-primary profiles.

### Shell Environment

The Hermes tab uses a dedicated `start-hermes` wrapper (sources .bashrc, starts hermes, fallback shell on error). The Terminal tab provides a plain shell with all paths configured.

| File                | Persistent? | Purpose                                         |
| ------------------- | ----------- | ----------------------------------------------- |
| `~/.bashrc`         | Yes         | Sources .hermes_profile + .env, prompt, aliases |
| `~/.hermes_profile` | Regenerated | Env vars, PATH, tokens (from add-on config)     |
| `~/.profile`        | Yes         | Sources .bashrc (login shell init)              |
| `~/.tmux.conf`      | Yes         | Terminal config (mouse scroll, history)         |

### Persistent Storage

Inside the add-on container, `~` is `/config`. That path is the add-on's private `addon_config` mount, not the normal Home Assistant Core `/config` folder. It survives add-on updates and is included in Home Assistant backups.

From the HAOS host or Samba, look for the `addon_configs` share/folder. The host-side path usually looks like this:

```text
/mnt/data/supervisor/addon_configs/<repo_or_slug>_hermes_agent/
```

For example, a locally installed/custom repository may appear as something like:

```text
/mnt/data/supervisor/addon_configs/a0b1c2d3_hermes_agent/
```

The exact prefix is installation-specific, but the important bit is: use `addon_configs`, not the regular Home Assistant Core config folder.

The default single-profile layout after a successful first start is:

```text
~ (/config inside the add-on, addon_configs/..._hermes_agent on the host)
├── .certs/                    # TLS certificates (auto-generated or custom)
├── .go/                       # Go workspace
├── .hermes/                   # Primary HERMES_HOME (official installer layout)
│   ├── hermes-agent/          # Git clone (source code, agent-modifiable)
│   │   └── venv/              # Python venv (editable install)
│   ├── logs/                  # Gateway logs
│   ├── memories/              # Long-term memory (MEMORY.md, USER.md)
│   ├── sessions/              # Conversation state
│   ├── skills/                # Auto-created + installed skills
│   ├── .env                   # API keys (chmod 600)
│   ├── SOUL.md                # Agent personality
│   ├── config.yaml            # Hermes config (model, platforms, tools)
│   └── state.db               # SQLite FTS5 state
├── .linuxbrew/                # Homebrew
├── .npm-global/               # npm global packages
├── .bash_aliases              # Custom aliases and functions (optional, user-created)
├── .bashrc                    # Shell config
├── .hermes_install            # Shared install marker for the Hermes clone + venv
├── .hermes_profile            # Env vars + PATH (regenerated)
├── .profile                   # Sources .bashrc (login shell init)
└── .tmux.conf                 # tmux config

/media/                        # Home Assistant media directory (shared, visible in Home Assistant media browser)
/share/                        # Home Assistant shared directory (shared between all add-ons)
```

Directories are created lazily during startup. If `/config/.hermes/hermes-agent` is missing inside the add-on terminal, the add-on likely has not completed the clone/install step yet; check the add-on log around the `[run] [hermes] Cloning Hermes Agent`, `Creating venv`, and `Installing Hermes` lines.

### Container Toolchain

Pre-installed at build time:

- **Languages**: Go 1.26, Node.js 22, Python 3.11
- **Browser**: Chromium, agent-browser
- **Dev tools**: bat, bc, fd-find, gh (GitHub CLI), git, htop, jq, moreutils, nano, ripgrep, tree, vim, yq
- **Graphics**: ghostscript, imagemagick
- **Media**: ffmpeg
- **Networking**: curl, dnsutils, netcat, openssh-client, ping, wget
- **Package managers**: go, Homebrew (Linuxbrew), npm, uv
- **System**: bash-completion, command-not-found, rsync, sqlite3, tmux, unzip/zip

### Supported Architectures

- `amd64`
- `aarch64`

## Changelog

Release notes for Home Assistant update screens live in [`hermes_agent/CHANGELOG.md`](hermes_agent/CHANGELOG.md). GitHub Releases carry the same user-facing release notes for tagged versions.

## License

This Home Assistant add-on/app is [MIT licensed](LICENSE). Hermes Agent itself is also [MIT licensed](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE).

---

Copyright (c) 2026 Wolfram Ravenwolf
