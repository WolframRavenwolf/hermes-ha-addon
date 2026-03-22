#!/command/with-contenv bash
# ─────────────────────────────────────────────────────────────────────
# Hermes Agent HA Add-on Entrypoint
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Section 1: Read options ──────────────────────────────────────────
OPTIONS_FILE="/data/options.json"
if [ ! -f "$OPTIONS_FILE" ]; then
    echo "[run] FATAL: $OPTIONS_FILE not found"
    exit 1
fi

opt() { jq -r ".${1} // empty" "$OPTIONS_FILE"; }
opt_bool() { jq -r ".${1} // false" "$OPTIONS_FILE"; }

HERMES_HOME_DIR=$(opt hermes_home)
GIT_URL=$(opt git_url)
GIT_REF=$(opt git_ref)
GIT_TOKEN=$(opt git_token)
AUTO_UPDATE=$(opt_bool auto_update)
TIMEZONE=$(opt timezone)
FORCE_IPV4=$(opt_bool force_ipv4_dns)
HASS_TOKEN=$(opt homeassistant_token)
HASS_URL=$(opt hass_url)

# ── Section 2: System setup ─────────────────────────────────────────
# Timezone (reject path traversal)
if [ -n "$TIMEZONE" ] && [[ "$TIMEZONE" != *..* ]] && [ -f "/usr/share/zoneinfo/$TIMEZONE" ]; then
    ln -snf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
    echo "$TIMEZONE" > /etc/timezone
    echo "[run] Timezone: $TIMEZONE"
fi

# IPv4 DNS priority
if [ "$FORCE_IPV4" = "true" ]; then
    if ! grep -q "precedence ::ffff:0:0/96  100" /etc/gai.conf 2>/dev/null; then
        echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf
    fi
    echo "[run] IPv4 DNS priority: enabled"
fi

# Core paths
export HERMES_HOME="/config/${HERMES_HOME_DIR:-.hermes}"
export HOME="/root"
echo "[run] HERMES_HOME: $HERMES_HOME"

# ── Section 3: Persistent storage setup ──────────────────────────────
SRC_DIR="/config/hermes-agent"
VENV_DIR="$HERMES_HOME/venv"
WORKSPACE_DIR="$HERMES_HOME/workspace"
BREW_PERSIST="/config/.linuxbrew"
NODE_GLOBAL="/config/.node_global"
GO_DIR="/config/.go"
CERTS_DIR="/config/certs"
TTYD_TERMINAL_PORT=7681
TTYD_HERMES_PORT=7682
INGRESS_PORT=48099
HTTP_PORT=8080
HTTPS_PORT=8443

# Create persistent directories
for d in "$HERMES_HOME" "$HERMES_HOME/memories" "$HERMES_HOME/skills" \
         "$HERMES_HOME/plugins" "$HERMES_HOME/sessions" "$HERMES_HOME/cron" \
         "$HERMES_HOME/logs" "$WORKSPACE_DIR" \
         "$NODE_GLOBAL/lib" "$GO_DIR/bin" "$CERTS_DIR"; do
    mkdir -p "$d"
done

# Symlink ~/.hermes -> HERMES_HOME
ln -snf "$HERMES_HOME" "$HOME/.hermes"

# Go
export GOPATH="$GO_DIR"
export GOBIN="$GO_DIR/bin"
export PATH="$GOBIN:$PATH"

# Node global
export NPM_CONFIG_PREFIX="$NODE_GLOBAL"
export PATH="$NODE_GLOBAL/bin:$PATH"

# Homebrew: sync from image on first boot, then persistent
BREW_IMAGE="${HOMEBREW_IMAGE_PREFIX:-/home/linuxbrew/.linuxbrew}"
if [ -d "$BREW_IMAGE" ] && [ ! -d "$BREW_PERSIST/bin" ]; then
    echo "[run] First boot: syncing Homebrew to persistent storage..."
    rsync -a "$BREW_IMAGE/" "$BREW_PERSIST/"
    echo "[run] Homebrew synced"
fi
if [ -d "$BREW_PERSIST/bin" ]; then
    export HOMEBREW_PREFIX="$BREW_PERSIST"
    export HOMEBREW_CELLAR="$BREW_PERSIST/Cellar"
    export HOMEBREW_REPOSITORY="$BREW_PERSIST/Homebrew"
    export PATH="$BREW_PERSIST/bin:$BREW_PERSIST/sbin:$PATH"
fi

# ── Section 4: Shell environment ─────────────────────────────────────
# .bashrc: paths + variables for ALL shells (login and non-login)
cat > /root/.bashrc << BASHRC
export HERMES_HOME="$HERMES_HOME"
export GOPATH="$GO_DIR"
export GOBIN="$GO_DIR/bin"
export NPM_CONFIG_PREFIX="$NODE_GLOBAL"
export PATH="$VENV_DIR/bin:$GO_DIR/bin:/usr/local/go/bin:$NODE_GLOBAL/bin:$BREW_PERSIST/bin:$BREW_PERSIST/sbin:\$PATH"
cd "$HERMES_HOME"
# Source persistent user profile if it exists (agent/user customizations)
[ -f "$HERMES_HOME/profile.sh" ] && . "$HERMES_HOME/profile.sh"
BASHRC

# profile.d: hermes autostart for LOGIN shells only
cat > /etc/profile.d/hermes.sh << 'PROFILE'
# Source .bashrc for paths (login shells don't source it by default in some setups)
[ -f /root/.bashrc ] && . /root/.bashrc
# Start hermes — clean exit ends session, crash drops to shell
hermes
_exit=$?
if [ $_exit -eq 0 ]; then
    exit 0
fi
echo ""
echo "Hermes exited with code $_exit. Shell is available for debugging."
echo "Run 'hermes' to restart, or 'exit' to close this session."
PROFILE

# ── Section 5: Hermes installation ───────────────────────────────────
MARKER_FILE="$HERMES_HOME/.install_marker"

compute_marker() {
    echo "git|${GIT_URL}|${GIT_REF}|$(cd "$SRC_DIR" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo none)"
}

install_needed() {
    local current
    current=$(compute_marker)
    if [ ! -f "$MARKER_FILE" ]; then return 0; fi
    if [ "$(cat "$MARKER_FILE")" != "$current" ]; then return 0; fi
    if [ ! -f "$VENV_DIR/bin/activate" ]; then return 0; fi
    if [ ! -f "$VENV_DIR/bin/hermes" ]; then return 0; fi
    return 1
}

activate_venv() {
    if [ ! -f "$VENV_DIR/bin/activate" ]; then
        echo "[run] Creating venv..."
        uv venv "$VENV_DIR" --python 3.11
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
}

# Clone if missing
if [ ! -d "$SRC_DIR/.git" ]; then
    echo "[run] Cloning Hermes Agent..."
    CLONE_URL="$GIT_URL"
    if [ -n "$GIT_TOKEN" ]; then
        CLONE_URL=$(echo "$GIT_URL" | sed "s|https://|https://${GIT_TOKEN}@|")
    fi
    CLONE_ARGS=()
    if [ -n "$GIT_REF" ]; then
        CLONE_ARGS+=(--branch "$GIT_REF")
    fi
    git clone "${CLONE_ARGS[@]}" "$CLONE_URL" "$SRC_DIR"
    cd "$SRC_DIR"
    git submodule update --init --recursive 2>/dev/null || true
    echo "[run] Clone complete: $(git log --oneline -1)"
fi

# Auto-update
if [ "$AUTO_UPDATE" = "true" ] && [ -d "$SRC_DIR/.git" ]; then
    echo "[run] Pulling latest changes..."
    cd "$SRC_DIR"
    git pull --ff-only 2>/dev/null || echo "[run] Warning: git pull failed (may have local changes)"
    git submodule update --init --recursive 2>/dev/null || true
fi

# Editable install
activate_venv
if install_needed; then
    echo "[run] Installing Hermes (editable)..."
    cd "$SRC_DIR"
    uv pip install -e ".[all,dev]" 2>&1 | tail -5
    # mini-swe-agent submodule
    if [ -f "$SRC_DIR/mini-swe-agent/pyproject.toml" ]; then
        uv pip install -e "$SRC_DIR/mini-swe-agent" 2>&1 | tail -3
    fi
    compute_marker > "$MARKER_FILE"
    echo "[run] Install complete"
else
    echo "[run] Install up to date (marker match)"
fi

# Link image-installed npm packages into project node_modules (where Hermes expects them)
if [ ! -e "$SRC_DIR/node_modules/agent-browser" ]; then
    mkdir -p "$SRC_DIR/node_modules"
    ln -snf /usr/local/lib/node_modules/agent-browser "$SRC_DIR/node_modules/agent-browser"
    echo "[run] Linked agent-browser into project"
fi

# Verify
HERMES_VERSION=$(hermes --version 2>/dev/null | head -1 || echo "unknown")
export HERMES_VERSION
echo "[run] Hermes version: $HERMES_VERSION"

# ── Section 6: Initial config scaffolding ────────────────────────────
# Only create files if they don't exist -- NEVER overwrite user config
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    cat > "$HERMES_HOME/config.yaml" << 'YAML'
# Hermes Agent configuration
# Run `hermes setup` or `hermes config edit` to configure interactively.
#
# Minimal example:
# model:
#   provider: anthropic
#   model: claude-sonnet-4-20250514
#
# See: https://github.com/NousResearch/hermes-agent#configuration
YAML
    echo "[run] Created default config.yaml"
fi

if [ ! -f "$HERMES_HOME/.env" ]; then
    cat > "$HERMES_HOME/.env" << 'ENV'
# API keys for Hermes Agent
# Add your keys here or use `hermes setup` to configure.
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
# OPENROUTER_API_KEY=sk-or-...
ENV
    chmod 600 "$HERMES_HOME/.env"
    echo "[run] Created default .env (chmod 600)"
fi

if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
    cat > "$HERMES_HOME/SOUL.md" << 'SOUL'
# SOUL.md - Agent Personality

You are a helpful AI assistant running on Home Assistant via Hermes Agent.

Customize this file to define your agent's personality, instructions, and behavior.
SOUL
    echo "[run] Created default SOUL.md"
fi

if [ ! -f "$HERMES_HOME/memories/MEMORY.md" ]; then
    cat > "$HERMES_HOME/memories/MEMORY.md" << 'MEM'
# MEMORY.md - Agent Long-Term Memory

*This file is managed by the agent. It stores important information across sessions.*
MEM
    echo "[run] Created default MEMORY.md"
fi

if [ ! -f "$HERMES_HOME/memories/USER.md" ]; then
    cat > "$HERMES_HOME/memories/USER.md" << 'USR'
# USER.md - About the User

*Add information about yourself here so the agent can better assist you.*
USR
    echo "[run] Created default USER.md"
fi

# tmux config (persistent, user-editable)
if [ ! -f "$HERMES_HOME/.tmux.conf" ]; then
    cat > "$HERMES_HOME/.tmux.conf" << 'TMUX'
set -g mouse on
set -g history-limit 50000
set -g default-terminal "tmux-256color"
TMUX
    echo "[run] Created default .tmux.conf"
fi
ln -snf "$HERMES_HOME/.tmux.conf" /root/.tmux.conf

# ── Section 7: Environment variable passthrough ──────────────────────
# Only reserve vars controlled by dedicated config options (avoid conflicts)
RESERVED_VARS="HERMES_HOME|HASS_TOKEN|HASS_URL|TZ"

ENV_COUNT=$(jq '.env_vars | length' "$OPTIONS_FILE" 2>/dev/null || echo 0)
if [ "$ENV_COUNT" -gt 0 ]; then
    for i in $(seq 0 $((ENV_COUNT - 1))); do
        VAR_NAME=$(jq -r ".env_vars[$i].name" "$OPTIONS_FILE")
        VAR_VALUE=$(jq -r ".env_vars[$i].value" "$OPTIONS_FILE")
        if echo "$VAR_NAME" | grep -qE "^($RESERVED_VARS)$"; then
            echo "[run] Warning: Skipping '$VAR_NAME' (use the dedicated config option instead)"
            continue
        fi
        export "$VAR_NAME"="$VAR_VALUE"
    done
    echo "[run] Exported $ENV_COUNT env var(s)"
fi

# HA integration: pass through if set
if [ -n "$HASS_TOKEN" ]; then
    export HASS_TOKEN
    echo "[run] HASS_TOKEN injected"
fi
if [ -n "$HASS_URL" ]; then
    export HASS_URL
    echo "[run] HASS_URL: $HASS_URL"
fi

# Enable OpenAI-compatible API server on the Gateway
export API_SERVER_ENABLED=true
export API_SERVER_PORT=8642
export API_SERVER_HOST=127.0.0.1

# Source .env for the agent
if [ -f "$HERMES_HOME/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$HERMES_HOME/.env"
    set +a
fi

# ── Section 8: TLS certificates ──────────────────────────────────────
if [ ! -f "$CERTS_DIR/server.crt" ]; then
    echo "[run] Generating self-signed TLS certificates..."
    # CA
    openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -keyout "$CERTS_DIR/ca.key" -out "$CERTS_DIR/ca.crt" \
        -days 3650 -subj "/CN=Hermes Agent CA" 2>/dev/null
    # Server cert signed by CA
    openssl req -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -keyout "$CERTS_DIR/server.key" -out /tmp/server.csr \
        -subj "/CN=hermes-agent" 2>/dev/null
    # SAN: localhost + common LAN hostnames
    LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
    openssl x509 -req -in /tmp/server.csr \
        -CA "$CERTS_DIR/ca.crt" -CAkey "$CERTS_DIR/ca.key" \
        -CAcreateserial -out "$CERTS_DIR/server.crt" \
        -days 3650 -extfile <(printf "subjectAltName=DNS:hermes-agent,DNS:localhost,IP:127.0.0.1,IP:%s" "$LAN_IP") 2>/dev/null
    rm -f /tmp/server.csr "$CERTS_DIR/ca.srl"
    chmod 600 "$CERTS_DIR/server.key" "$CERTS_DIR/ca.key"
    echo "[run] TLS certificates generated (CA + server)"
    echo "[run] Install $CERTS_DIR/ca.crt on clients to avoid browser warnings"
else
    echo "[run] TLS certificates: using existing"
fi

# ── Section 9: Render nginx config ───────────────────────────────────
cp /etc/nginx/nginx.conf.tpl /etc/nginx/nginx.conf
sed -i \
    -e "s|%%INGRESS_PORT%%|${INGRESS_PORT}|g" \
    -e "s|%%HTTP_PORT%%|${HTTP_PORT}|g" \
    -e "s|%%HTTPS_PORT%%|${HTTPS_PORT}|g" \
    -e "s|%%TTYD_TERMINAL_PORT%%|${TTYD_TERMINAL_PORT}|g" \
    -e "s|%%TTYD_HERMES_PORT%%|${TTYD_HERMES_PORT}|g" \
    -e "s|%%CERTS_DIR%%|${CERTS_DIR}|g" \
    -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
    /etc/nginx/nginx.conf

# Render landing page
cp /var/www/landing.html.tpl /var/www/landing.html
sed -i \
    -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
    /var/www/landing.html

echo "[run] Nginx configured (ingress: $INGRESS_PORT, HTTP: $HTTP_PORT, HTTPS: $HTTPS_PORT)"

# ── Section 10: Start services ───────────────────────────────────────
GATEWAY_PID=""
TTYD_TERMINAL_PID=""
TTYD_HERMES_PID=""
NGINX_PID=""

start_gateway() {
    echo "[run] Starting Hermes gateway..."
    cd "$HERMES_HOME"
    hermes gateway run >> "$HERMES_HOME/logs/gateway.log" 2>&1 &
    GATEWAY_PID=$!
    echo "[run] Gateway started (PID: $GATEWAY_PID)"
}

start_ttyd() {
    echo "[run] Starting ttyd (terminal: ${TTYD_TERMINAL_PORT}, hermes: ${TTYD_HERMES_PORT})..."
    # Terminal: non-login shell (shell pur)
    ttyd \
        --port "${TTYD_TERMINAL_PORT}" \
        --interface 127.0.0.1 \
        --base-path /terminal/ \
        --writable \
        tmux -u new -A -s terminal /usr/bin/bash &
    TTYD_TERMINAL_PID=$!
    # Hermes: login shell (exec hermes via profile.d)
    ttyd \
        --port "${TTYD_HERMES_PORT}" \
        --interface 127.0.0.1 \
        --base-path /hermes/ \
        --writable \
        tmux -u new -A -s hermes /usr/bin/bash -l &
    TTYD_HERMES_PID=$!
    echo "[run] ttyd started (terminal PID: $TTYD_TERMINAL_PID, hermes PID: $TTYD_HERMES_PID)"
}

start_nginx() {
    echo "[run] Starting nginx..."
    nginx -g 'daemon off;' &
    NGINX_PID=$!
    echo "[run] nginx started (PID: $NGINX_PID)"
}

# Register signal handler BEFORE starting services
trap shutdown SIGTERM SIGINT

start_gateway
start_ttyd
start_nginx

echo "[run] All services started"
echo "─────────────────────────────────────────────"
echo " ${HERMES_VERSION}"
echo " Gateway PID: ${GATEWAY_PID}"
echo " Terminal:    http://localhost:${HTTP_PORT}/terminal/"
echo " API:         http://localhost:${HTTP_PORT}/v1/"
echo " HTTPS:       https://localhost:${HTTPS_PORT}/"
echo " HA Ingress:  sidebar (landing page)"
echo "─────────────────────────────────────────────"

# ── Section 11: Signal handling ──────────────────────────────────────
shutdown() {
    echo ""
    echo "[run] Shutting down..."
    # Reverse order: nginx -> ttyd -> gateway
    if [ -n "$NGINX_PID" ] && kill -0 "$NGINX_PID" 2>/dev/null; then
        kill "$NGINX_PID" 2>/dev/null
        echo "[run] nginx stopped"
    fi
    for pid in "$TTYD_TERMINAL_PID" "$TTYD_HERMES_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
    done
    echo "[run] ttyd stopped"
    if [ -n "$GATEWAY_PID" ] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
        kill -TERM "$GATEWAY_PID" 2>/dev/null
        local waited=0
        while kill -0 "$GATEWAY_PID" 2>/dev/null && [ $waited -lt 10 ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if kill -0 "$GATEWAY_PID" 2>/dev/null; then
            echo "[run] Gateway didn't stop gracefully, force killing..."
            kill -9 "$GATEWAY_PID" 2>/dev/null || true
        fi
        echo "[run] Gateway stopped"
    fi
    echo "[run] Shutdown complete"
    exit 0
}

# ── Section 12: Supervisor loop ──────────────────────────────────────
while true; do
    if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
        set +e; wait "$GATEWAY_PID" 2>/dev/null; EXIT_CODE=$?; set -e
        if [ $EXIT_CODE -eq 0 ]; then
            echo "[run] Gateway exited normally"
            break
        fi
        echo "[run] Gateway exited unexpectedly (code: $EXIT_CODE), restarting in 3s..."
        sleep 3
        start_gateway
    fi
    sleep 5
done

shutdown
