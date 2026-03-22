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

GIT_URL=$(opt git_url)
GIT_REF=$(opt git_ref)
GIT_TOKEN=$(opt git_token)
AUTO_UPDATE=$(opt_bool auto_update)
HASS_URL=$(opt hass_url)
HASS_TOKEN=$(opt homeassistant_token)
HERMES_HOME_DIR=$(opt hermes_home)
PREFER_IPV4=$(opt_bool prefer_ipv4_dns)

# ── Section 2: System setup ─────────────────────────────────────────
# Timezone: sync /etc/localtime + /etc/timezone from HA's TZ env var
if [ -n "$TZ" ] && [[ "$TZ" != *..* ]] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
    echo "[run] Timezone: $TZ"
fi

# IPv4 DNS priority
if [ "$PREFER_IPV4" = "true" ]; then
    if ! grep -q "precedence ::ffff:0:0/96  100" /etc/gai.conf 2>/dev/null; then
        echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf
    fi
    echo "[run] IPv4 DNS priority: enabled"
fi

# Core paths
export HOME="/config"
export HERMES_HOME="$HOME/${HERMES_HOME_DIR:-.hermes}"
echo "[run] HERMES_HOME: $HERMES_HOME"

# ── Section 3: Persistent storage setup ──────────────────────────────
SRC_DIR="$HOME/hermes-agent"
VENV_DIR="$HOME/.venv"
WORKSPACE_DIR="$HERMES_HOME/workspace"
BREW_DIR="$HOME/.linuxbrew"
NODE_DIR="$HOME/.npm-global"
GO_DIR="$HOME/.go"
CERTS_DIR="$HOME/certs"
INGRESS_PORT=49169
TTYD_HERMES_PORT=49269
TTYD_TERMINAL_PORT=49369
HTTP_PORT=8080
HTTPS_PORT=8443

# Create persistent directories
for d in "$HERMES_HOME" \
         "$HERMES_HOME/cron" \
         "$HERMES_HOME/logs" \
         "$HERMES_HOME/memories" \
         "$HERMES_HOME/plugins" \
         "$HERMES_HOME/sessions" \
         "$HERMES_HOME/skills" \
         "$WORKSPACE_DIR" \
         "$NODE_DIR/lib" \
         "$GO_DIR/bin" \
         "$CERTS_DIR"; do
    mkdir -p "$d"
done

# Go
export GOPATH="$GO_DIR"
export GOBIN="$GO_DIR/bin"
export PATH="$GOBIN:$PATH"

# Node global
export NPM_CONFIG_PREFIX="$NODE_DIR"
export PATH="$NODE_DIR/bin:$PATH"

# Homebrew: sync from image on first boot, then persistent
BREW_IMAGE="/home/linuxbrew/.linuxbrew"
if [ -d "$BREW_IMAGE" ] && [ ! -d "$BREW_DIR/bin" ]; then
    echo "[run] First boot: syncing Homebrew to persistent storage..."
    rsync -a "$BREW_IMAGE/" "$BREW_DIR/"
    echo "[run] Homebrew synced"
fi
if [ -d "$BREW_DIR/bin" ]; then
    export HOMEBREW_PREFIX="$BREW_DIR"
    export HOMEBREW_CELLAR="$BREW_DIR/Cellar"
    export HOMEBREW_REPOSITORY="$BREW_DIR/Homebrew"
    export PATH="$BREW_DIR/sbin:$BREW_DIR/bin:$PATH"
fi

# ── Section 4: Shell environment ─────────────────────────────────────
# ~/.hermes_profile: env vars + PATH (regenerated every start, dynamic from config)
cat > /config/.hermes_profile << ENVSH
export HERMES_HOME="$HERMES_HOME"
export GOPATH="$GO_DIR"
export GOBIN="$GO_DIR/bin"
export NPM_CONFIG_PREFIX="$NODE_DIR"
export PATH="$VENV_DIR/bin:$BREW_DIR/sbin:$BREW_DIR/bin:$GO_DIR/bin:/usr/local/go/bin:$NODE_DIR/bin:\$PATH"
$([ -n "$HASS_TOKEN" ] && echo "export HASS_TOKEN=\"$HASS_TOKEN\"")
$([ -n "$HASS_URL" ] && echo "export HASS_URL=\"$HASS_URL\"")
$([ -n "$GIT_TOKEN" ] && echo "export GITHUB_TOKEN=\"$GIT_TOKEN\"")
export API_SERVER_ENABLED=true
export API_SERVER_PORT=8642
export API_SERVER_HOST=127.0.0.1
ENVSH

# ~/.bashrc: persistent, create-if-missing (user-editable)
if [ ! -f /config/.bashrc ]; then
    cat > /config/.bashrc << 'BASHRC'
# Source Hermes environment (paths, variables, tokens)
[ -f ~/.hermes_profile ] && . ~/.hermes_profile
# Source Hermes API keys
[ -f "${HERMES_HOME:=$HOME/.hermes}/.env" ] && set -a && . "$HERMES_HOME/.env" && set +a
# Prompt
PS1='\[\033[01;34m\]\w\[\033[00m\]\$ '
# Working directory
cd "${HERMES_HOME:-$HOME}" 2>/dev/null || cd ~
# User customizations
[ -f ~/.bash_aliases ] && . ~/.bash_aliases
BASHRC
    echo "[run] Created default .bashrc"
fi

# ~/.profile: persistent, create-if-missing (user-editable)
if [ ! -f /config/.profile ]; then
    cat > /config/.profile << 'PROFILE'
# Source .bashrc for paths and aliases
[ -f ~/.bashrc ] && . ~/.bashrc
# Start Hermes Agent — clean exit ends session, crash drops to shell
hermes
_exit=$?
if [ $_exit -eq 0 ]; then
    exit 0
fi
echo ""
echo "Hermes exited with code $_exit. Shell is available for debugging."
echo "Run 'hermes' to restart, or 'exit' to close this session."
PROFILE
    echo "[run] Created default .profile"
fi

# ── Section 5: Hermes installation ───────────────────────────────────
MARKER_FILE="$HOME/.hermes_install"

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

# Auto-update (stash local changes, pull, restore)
if [ "$AUTO_UPDATE" = "true" ] && [ -d "$SRC_DIR/.git" ]; then
    echo "[run] Pulling latest changes..."
    cd "$SRC_DIR"
    git stash --quiet 2>/dev/null || true
    git pull --ff-only 2>/dev/null || echo "[run] Warning: git pull failed (branch may have diverged)"
    git stash pop --quiet 2>/dev/null || true
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
# Hermes creates its own defaults (config.yaml, SOUL.md, etc.) on first run.
# We only seed .env from the source example if missing.
if [ ! -f "$HERMES_HOME/.env" ] && [ -f "$SRC_DIR/.env.example" ]; then
    cp -p "$SRC_DIR/.env.example" "$HERMES_HOME/.env"
    chmod 600 "$HERMES_HOME/.env"
    echo "[run] Created .env from source example (chmod 600)"
fi

# tmux config (persistent, user-editable)
if [ ! -f /config/.tmux.conf ]; then
    cat > /config/.tmux.conf << 'TMUX'
set -g mouse on
set -g history-limit 50000
set -g default-terminal "tmux-256color"
TMUX
    echo "[run] Created default .tmux.conf"
fi

# ── Section 7: Environment variable passthrough ──────────────────────
# Only reserve vars controlled by dedicated config options (avoid conflicts)
RESERVED_VARS="HERMES_HOME|HASS_TOKEN|HASS_URL|GITHUB_TOKEN"

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
# Git token also serves as GITHUB_TOKEN (for gh CLI + Hermes skills)
if [ -n "$GIT_TOKEN" ]; then
    export GITHUB_TOKEN="$GIT_TOKEN"
    echo "[run] GITHUB_TOKEN injected"
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
ADDON_SLUG=$(hostname | tr '-' '_')
cp /var/www/landing.html.tpl /var/www/landing.html
sed -i \
    -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
    -e "s|%%ADDON_SLUG%%|${ADDON_SLUG}|g" \
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
