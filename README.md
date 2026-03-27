# Hermes Agent Home Assistant Add-on

> The self-improving AI agent built by Nous Research. Home Assistant add-on by Wolfram Ravenwolf.

![Hermes Agent running in Home Assistant](hermes-ha-addon.png)

[Hermes Agent](https://hermes-agent.nousresearch.com/) packaged as a [Home Assistant](https://home-assistant.io/) add-on/app. Persistent AI agent with memory, self-improving skills, multi-platform messaging, and a plugin architecture for custom tools.

https://github.com/WolframRavenwolf/hermes-ha-addon/releases/download/v1.0.0/hermes-ha-addon.mp4

## Features

- **Persistent memory** -- SQLite FTS5 long-term memory that survives restarts
- **Self-improving skills** -- agent learns and creates new capabilities over time
- **Multi-platform messaging** -- Telegram, Discord, WhatsApp, and more via the gateway
- **OpenAI-compatible API** -- connect any chat frontend ([Open WebUI](https://github.com/open-webui/open-webui), [SillyTavern](https://github.com/SillyTavern/SillyTavern), etc.) via `/v1/`
- **Plugin architecture** -- custom tools, commands, and hooks without forking
- **Self-modifiable source** -- editable install lets the agent read and modify its own code
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
| `enable_terminal`     | `false`                                            | Enable web terminal on direct HTTP/HTTPS ports                                  |
| `enable_api`          | `false`                                            | Enable the OpenAI-compatible API server on direct HTTP/HTTPS ports              |
| `access_password`     |                                                    | Password for HTTP/HTTPS access (web terminal). Also used as the server API key  |
| `env_vars`            | `OPENROUTER_API_KEY` (example)                     | Hermes .env variables — written to `~/.hermes/.env` on each start               |
| `hermes_home`         | `.hermes`                                          | Agent profile directory (relative to ~). Change to switch profiles (e.g. "amy") |

API keys can be configured in two places: `env_vars` above (convenient, via Home Assistant UI) or `~/.hermes/.env` directly (full list, via terminal or `hermes setup`). Non-empty `env_vars` are written to `.env` on each start, overriding existing entries.

**Note:** Values added via `env_vars` are not removed or reset from `.env` when cleared or removed in the Home Assistant UI -- edit `~/.hermes/.env` directly to remove them.

Hermes-internal configuration (model, platforms, memory, tools) is managed via the terminal:

```bash
hermes setup          # Interactive first-time setup
hermes config edit    # Edit config directly
hermes doctor         # Diagnostics and dependency check
hermes gateway setup  # Configure messaging platforms
```

## Access

The add-on is accessible via the **Home Assistant Sidebar** (landing page with embedded terminal, mode switching, and status display) and, optionally, via direct URLs. Replace `homeassistant.local` with your Home Assistant hostname or IP.

Direct HTTP/HTTPS access requires `enable_terminal` (**Enable Web Terminal**) and/or `enable_api` (**Enable API Server**) in the add-on configuration. Set an **Access Password** to secure these ports (username: `hermes`).

### Web Terminal

| URL                                            | Description                                                              |
| ---------------------------------------------- | ------------------------------------------------------------------------ |
| `https://homeassistant.local:8443/hermes/`     | Hermes Agent (starts hermes, crash drops to shell)                       |
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
ssh -p 22222 -t root@homeassistant.local "docker exec -it \$(docker ps -qf name=hermes_agent) tmux -u new -A -s hermes /usr/local/bin/start-hermes"

# Terminal (shared tmux session — same as Home Assistant sidebar "Terminal" tab)
ssh -p 22222 -t root@homeassistant.local "docker exec -it \$(docker ps -qf name=hermes_agent) tmux -u new -A -s terminal bash"

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

## Architecture

Three services in a Debian Bookworm container:

1. **Hermes Gateway** (`hermes gateway run`) -- persistent AI agent daemon with OpenAI-compatible API server and messaging platform connectors. Logs visible in the Home Assistant add-on log and in `~/.hermes/logs/gateway.log`.
2. **ttyd** (x2) -- web terminals backed by persistent tmux sessions (`hermes` + `terminal`)
3. **nginx** -- HTTP, HTTPS, and Home Assistant ingress proxy routing to terminal + API

### Shell Environment

The Hermes tab uses a dedicated `start-hermes` wrapper (sources .bashrc, starts hermes, fallback shell on error). The Terminal tab provides a plain shell with all paths configured.

| File                | Persistent? | Purpose                                         |
| ------------------- | ----------- | ----------------------------------------------- |
| `~/.bashrc`         | Yes         | Sources .hermes_profile + .env, prompt, aliases |
| `~/.hermes_profile` | Regenerated | Env vars, PATH, tokens (from add-on config)     |
| `~/.profile`        | Yes         | Sources .bashrc (login shell init)              |
| `~/.tmux.conf`      | Yes         | Terminal config (mouse scroll, history)         |

### Persistent Storage

`~` is `/config/` (add-on-isolated via `addon_config`). Everything survives add-on updates and is included in Home Assistant backups:

```
~ (/config/)
├── .certs/                # TLS certificates (auto-generated or custom)
├── .go/                   # Go workspace
├── .hermes/               # HERMES_HOME (matches official installer layout)
│   ├── hermes-agent/      # Git clone (source code, agent-modifiable)
│   │   └── venv/          # Python venv (editable install)
│   ├── logs/              # Gateway logs
│   ├── memories/          # Long-term memory (MEMORY.md, USER.md)
│   ├── sessions/          # Conversation state
│   ├── skills/            # Auto-created + installed skills
│   ├── .env               # API keys (chmod 600)
│   ├── SOUL.md            # Agent personality
│   ├── config.yaml        # Hermes config (model, platforms, tools)
│   └── state.db           # SQLite FTS5 state
├── .linuxbrew/            # Homebrew
├── .npm-global/           # npm global packages
├── .bash_aliases          # Custom aliases and functions (optional, user-created)
├── .bashrc                # Shell config
├── .hermes_install        # Install marker
├── .hermes_profile        # Env vars + PATH (regenerated)
├── .profile               # Sources .bashrc (login shell init)
└── .tmux.conf             # tmux config

/media/                    # Home Assistant media directory (shared, visible in Home Assistant media browser)
/share/                    # Home Assistant shared directory (shared between all add-ons)
```

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

## License

This Home Assistant add-on/app is [MIT licensed](LICENSE). Hermes Agent itself is also [MIT licensed](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE).

---

Copyright (c) 2026 Wolfram Ravenwolf
