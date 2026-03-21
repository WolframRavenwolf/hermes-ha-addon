# Hermes Agent Home Assistant Add-on

[NousResearch Hermes Agent](https://github.com/NousResearch/hermes-agent) packaged as a Home Assistant add-on. Persistent AI agent with memory, self-improving skills, multi-platform messaging, and a plugin architecture for custom tools.

## Features

- **Persistent memory** -- SQLite FTS5 long-term memory that survives restarts
- **Self-improving skills** -- agent learns and creates new capabilities over time
- **Multi-platform messaging** -- Telegram, Discord, WhatsApp, and more via the gateway
- **OpenAI-compatible API** -- connect any chat frontend (Open WebUI, SillyTavern, etc.) via port 8642
- **Plugin architecture** -- custom tools, commands, and hooks without forking
- **Self-modifiable source** -- editable install lets the agent read and modify its own code
- **Persistent web terminal** -- full CLI access via tmux-backed ttyd through the HA sidebar
- **Full persistence** -- source code, venv, Homebrew, npm, Go, and all agent data survive addon updates

## Installation

1. Add this repository to Home Assistant: **Settings > Add-ons > Add-on Store > ⋮ > Repositories**
2. Paste the repository URL and click **Add**
3. Find **Hermes Agent** in the store and click **Install**
4. Start the addon and open **Hermes Agent** from the sidebar
5. The setup wizard runs automatically -- configure your model and API keys

## Configuration

Addon-level options are configured in the HA UI (Settings > Add-ons > Hermes Agent > Configuration):

| Option | Default | Description |
|--------|---------|-------------|
| `git_url` | NousResearch repo | Git repository URL |
| `git_ref` | `main` | Branch, tag, or commit |
| `git_token` | | Token for private repos |
| `auto_update` | `false` | Pull latest on restart |
| `auto_setup` | `true` | Run setup wizard on first terminal login if not configured |
| `timezone` | `Europe/Berlin` | Container timezone |
| `force_ipv4_dns` | `true` | Prefer IPv4 DNS resolution |
| `homeassistant_token` | | Long-lived access token for HA API integration |
| `hass_url` | `http://homeassistant.local:8123` | Home Assistant URL |
| `env_vars` | `[]` | Extra environment variables (API keys, etc.) |

Hermes-internal configuration (model, platforms, memory, tools) is managed via the terminal:

```bash
hermes setup          # Interactive first-time setup
hermes config edit    # Edit config directly
hermes doctor         # Diagnostics and dependency check
hermes gateway setup  # Configure messaging platforms
```

## Architecture

Three services in a Debian Bookworm container:

1. **Hermes Gateway** (`hermes gateway`) -- persistent AI agent daemon with OpenAI-compatible API and messaging platform connectors
2. **ttyd** -- web terminal on the HA ingress port, backed by a persistent tmux session
3. **nginx** -- internal reverse proxy for future API exposure (port 8099)

The HA sidebar opens directly into the terminal (ttyd on the ingress port with `ingress_stream: true`). The tmux session `hermes` persists across reconnects -- closing and reopening the sidebar returns to the same session.

### Persistent Storage

Everything under `/config/` survives addon updates and is included in HA backups:

```
/config/
├── hermes-agent/          # Git clone (agent-modifiable source code)
├── .hermes/               # HERMES_HOME
│   ├── config.yaml        # Hermes config (model, platforms, tools)
│   ├── .env               # API keys (chmod 600)
│   ├── SOUL.md            # Agent personality
│   ├── .tmux.conf         # Terminal config (mouse scroll, history)
│   ├── profile.sh         # Custom shell profile (optional)
│   ├── memories/          # Long-term memory (MEMORY.md, USER.md)
│   ├── skills/            # Auto-created + installed skills
│   ├── plugins/           # Custom tools and hooks
│   ├── workspace/         # Persistent docs, data, code
│   ├── sessions/          # Conversation state
│   ├── state.db           # SQLite FTS5 state
│   └── venv/              # Python venv (editable install -> source)
├── .linuxbrew/            # Homebrew (persistent)
├── .node_global/          # npm global packages (persistent)
└── .go/                   # Go workspace (persistent)
```

### Container Toolchain

Pre-installed at build time:

- **Languages**: Python 3.11+ (uv), Node.js 22, Go 1.22
- **Browser**: Chromium + agent-browser (headless automation)
- **Media**: ffmpeg (TTS audio conversion)
- **Dev tools**: git, gh (GitHub CLI), ripgrep, fd-find, bat, jq, tree, vim, nano
- **Networking**: curl, wget, openssh-client, dnsutils, netcat
- **System**: tmux, nginx, sqlite3, rsync, zip/unzip, procps
- **Package managers**: Homebrew (Linuxbrew), npm, uv, go install

## SSH Access

Connect to the same tmux session via SSH (bypassing the HA UI):

```bash
ssh -tp <port> root@<ha-host> "docker exec -it addon_<slug>_hermes_agent tmux -u new -A -s hermes /usr/bin/bash -l"
```

## Supported Architectures

- `amd64`
- `aarch64`

## License

This addon packaging is provided as-is. Hermes Agent itself is [MIT licensed](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE).
