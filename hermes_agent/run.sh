#!/command/with-contenv bash
# shellcheck shell=bash
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
# shellcheck disable=SC2034  # consumed by resolve_profiles in profile-init.sh
HERMES_HOME_DIR=$(opt hermes_home)
ENABLE_DASHBOARD=$(opt_bool enable_dashboard)
ENABLE_TERMINAL=$(opt_bool enable_terminal)
ENABLE_API=$(opt_bool enable_api)
ACCESS_PASSWORD=$(opt access_password)

# ── Section 2: System setup ─────────────────────────────────────────
# Timezone: sync /etc/localtime + /etc/timezone from HA's TZ env var
if [ -n "$TZ" ] && [[ "$TZ" != *..* ]] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
    echo "[run] Timezone: $TZ"
fi

# IPv4 DNS priority (always enabled — no practical IPv6-only home networks)
if grep -q "^precedence ::ffff:0:0/96  100" /etc/gai.conf 2>/dev/null; then
    : # already active
elif grep -q "^#[[:space:]]*precedence ::ffff:0:0/96  100" /etc/gai.conf 2>/dev/null; then
    sed -i 's/^#[[:space:]]*\(precedence ::ffff:0:0\/96  100\)/\1/' /etc/gai.conf
else
    echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf
fi

# HA's s6 supervises this wrapper; keep upstream Hermes in foreground mode.
export HERMES_GATEWAY_NO_SUPERVISE=1

# ── Section 3: Profile resolution ────────────────────────────────────
# Source the profile-init library. Resolves the `profiles` list (or legacy
# `hermes_home`) into PROFILE_* arrays and per-profile port arrays.
PROFILE_INIT_LIB=""
for _candidate in \
    "/usr/local/lib/hermes-profile-init.sh" \
    "$(dirname "${BASH_SOURCE[0]}")/profile-init.sh"; do
    if [ -f "$_candidate" ]; then
        PROFILE_INIT_LIB="$_candidate"
        break
    fi
done
if [ -z "$PROFILE_INIT_LIB" ]; then
    echo "[run] FATAL: profile-init.sh not found"
    exit 1
fi
# shellcheck source=profile-init.sh
source "$PROFILE_INIT_LIB"

resolve_profiles || exit 1

PRIMARY_HOME="${PROFILE_HOMES[0]}"
PRIMARY_VENV_DIR="${PROFILE_VENV_DIRS[0]}"
export HERMES_HOME="$PRIMARY_HOME"

echo "[run] Profiles (${#PROFILE_DIRS[@]}):"
for i in "${!PROFILE_DIRS[@]}"; do
    prefix_label="${PROFILE_PATH_PREFIX[$i]:-/}"
    echo "[run]   [$i] ${PROFILE_NAMES[$i]} → ${PROFILE_HOMES[$i]} (route: $prefix_label)"
done

# ── Section 3b: System paths ─────────────────────────────────────────
BREW_DIR="$HOME/.linuxbrew"
NODE_DIR="$HOME/.npm-global"
GO_DIR="$HOME/.go"
CERTS_DIR="$HOME/.certs"
INGRESS_PORT=49169
HTTP_PORT=8080
HTTPS_PORT=8443

# Start nginx early with loading page (replaced with full config after setup)
cat > /etc/nginx/nginx.conf << LOADCONF
worker_processes 1;
pid /var/run/nginx.pid;
error_log stderr warn;
events { worker_connections 64; }
http {
    server {
        listen ${INGRESS_PORT};
        location / { root /var/www; try_files /loading.html =404; }
        location = /health { return 200 "OK\n"; add_header Content-Type text/plain; }
    }
}
LOADCONF
nginx
echo "[run] Loading page active (ingress: $INGRESS_PORT)"

# Create persistent directories (only system infra — Hermes creates its own)
mkdir -p "$NODE_DIR/lib" "$GO_DIR/bin" "$CERTS_DIR"
for i in "${!PROFILE_DIRS[@]}"; do
    mkdir -p "${PROFILE_HOMES[$i]}"
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

# Snapshot PATH before adding any per-profile venv — used when building per-shell PATH.
BASE_PATH="$PATH"

# ── Section 4: Shell environment ─────────────────────────────────────
# ~/.bashrc: persistent, create-if-missing (user-editable)
if [ ! -f /config/.bashrc ]; then
    cat > /config/.bashrc << 'BASHRC'
# Source Hermes API keys (.env first, then profile overrides)
[ -f "${HERMES_HOME:=$HOME/.hermes}/.env" ] && set -a && . "$HERMES_HOME/.env" && set +a
# Source Hermes environment (paths, variables, tokens — overrides .env)
[ -f ~/.hermes_profile ] && . ~/.hermes_profile

# If not running interactively, stop here
case $- in
    *i*) ;;
      *) return;;
esac

# Working directory
cd ~

# History
HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=1000000
HISTFILESIZE=1000000

# Shell options
shopt -s checkwinsize
shopt -s globstar

# lesspipe
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# Prompt
PS1='\[\033[01;34m\]\w\[\033[00m\]\$ '

# Colors
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias diff='diff --color=auto'
    alias egrep='egrep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias grep='grep --color=auto'
    alias ls='ls --color=auto'
fi

# ls aliases
alias l='ls -CF'
alias la='ls -A'
alias ll='ls -l'
alias lla='ls -Al'

# Alias definitions
[ -f ~/.bash_aliases ] && . ~/.bash_aliases

# Bash completion
if ! shopt -oq posix; then
    if [ -f /usr/share/bash-completion/bash_completion ]; then
        . /usr/share/bash-completion/bash_completion
    elif [ -f /etc/bash_completion ]; then
        . /etc/bash_completion
    fi
fi

# Command-not-found handler
if [ -x /usr/lib/command-not-found ]; then
    command_not_found_handle() { /usr/lib/command-not-found -- "$1"; return $?; }
fi
BASHRC
    echo "[run] Created default .bashrc"
fi

# ~/.profile: persistent, create-if-missing (user-editable)
# Hermes autostart is handled by /usr/local/bin/start-hermes (via ttyd),
# not .profile, to avoid recursion when Hermes spawns login subshells.
if [ ! -f /config/.profile ]; then
    cat > /config/.profile << 'PROFILE'
# Source .bashrc for paths and aliases
[ -f ~/.bashrc ] && . ~/.bashrc
PROFILE
    echo "[run] Created default .profile"
fi

# ── Section 5: Hermes installation (per profile) ─────────────────────
compute_marker() {
    local src_dir="$1"
    local ref="${GIT_REF:-$(cd "$src_dir" 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}"
    local hash
    hash="$(cd "$src_dir" 2>/dev/null && git rev-parse HEAD 2>/dev/null || echo none)"
    local subs
    subs="$(find "$src_dir" -mindepth 2 -maxdepth 2 -name pyproject.toml -print 2>/dev/null | while IFS= read -r pyproject; do basename "$(dirname "$pyproject")"; done | sort | paste -sd,)"
    echo "${GIT_URL}|${ref}|${hash}|${subs}"
}

install_needed() {
    local src_dir="$1" venv_dir="$2" marker_file="$3"
    local current
    current=$(compute_marker "$src_dir")
    if [ ! -f "$marker_file" ]; then return 0; fi
    if [ "$(cat "$marker_file")" != "$current" ]; then return 0; fi
    if [ ! -f "$venv_dir/bin/activate" ]; then return 0; fi
    if [ ! -f "$venv_dir/bin/hermes" ]; then return 0; fi
    return 1
}

install_profile() {
    local i="$1"
    local src_dir="${PROFILE_SRC_DIRS[$i]}"
    local venv_dir="${PROFILE_VENV_DIRS[$i]}"
    local marker_file="${PROFILE_MARKER[$i]}"
    local name="${PROFILE_NAMES[$i]}"

    # Clone if missing
    if [ ! -d "$src_dir/.git" ]; then
        echo "[run] [$name] Cloning Hermes Agent..."
        local clone_url="$GIT_URL"
        if [ -n "$GIT_TOKEN" ]; then
            clone_url=$(echo "$GIT_URL" | sed "s|https://|https://${GIT_TOKEN}@|")
        fi
        local clone_args=()
        if [ -n "$GIT_REF" ]; then
            clone_args+=(--branch "$GIT_REF")
        fi
        git clone "${clone_args[@]}" "$clone_url" "$src_dir"
        (cd "$src_dir" && git submodule update --init --recursive 2>/dev/null || true)
        echo "[run] [$name] Clone complete: $(cd "$src_dir" && git log --oneline -1)"
    fi

    # Auto-update (stash local changes, pull, restore)
    if [ "$AUTO_UPDATE" = "true" ] && [ -d "$src_dir/.git" ]; then
        echo "[run] [$name] Pulling latest changes..."
        (
            cd "$src_dir"
            git stash --quiet 2>/dev/null || true
            git pull --ff-only 2>/dev/null || echo "[run] [$name] Warning: git pull failed (branch may have diverged)"
            git stash pop --quiet 2>/dev/null || true
            git submodule update --init --recursive 2>/dev/null || true
        )
    fi

    # Editable install
    if [ ! -f "$venv_dir/bin/activate" ]; then
        echo "[run] [$name] Creating venv..."
        uv venv "$venv_dir" --python 3.11
    fi
    if install_needed "$src_dir" "$venv_dir" "$marker_file"; then
        echo "[run] [$name] Installing Hermes (editable)..."
        (
            cd "$src_dir"
            # shellcheck disable=SC1091
            source "$venv_dir/bin/activate"
            uv pip install -e ".[all,dev]" 2>&1 | tail -5
            if [ -f "$src_dir/mini-swe-agent/pyproject.toml" ]; then
                uv pip install -e "$src_dir/mini-swe-agent" 2>&1 | tail -3
            fi
            if [ -f "$src_dir/tinker-atropos/pyproject.toml" ]; then
                uv pip install -e "$src_dir/tinker-atropos" 2>&1 | tail -3
            fi
        )
        compute_marker "$src_dir" > "$marker_file"
        echo "[run] [$name] Install complete"
    else
        echo "[run] [$name] Install up to date (marker match)"
    fi

    # Link image-installed npm packages into project node_modules
    if [ ! -e "$src_dir/node_modules/agent-browser" ]; then
        mkdir -p "$src_dir/node_modules"
        ln -snf /usr/lib/node_modules/agent-browser "$src_dir/node_modules/agent-browser"
        (cd "$src_dir" && npm audit fix --silent 2>/dev/null || true)
        echo "[run] [$name] Linked agent-browser into project"
    fi

    # Build dashboard web frontend
    if [ -f "$src_dir/web/package.json" ]; then
        local rebuild="false"
        local status_file
        status_file="$(mktemp)"

        if ! /usr/local/bin/hermes-dashboard-patches "$src_dir" "$status_file"; then
            echo "[run] [$name] WARNING: dashboard compatibility patch failed - continuing startup"
        fi
        if [ -s "$status_file" ]; then
            rebuild="true"
        fi
        rm -f "$status_file"

        if grep -Eq '(src|href)="/assets/' "$src_dir/hermes_cli/web_dist/index.html" 2>/dev/null; then
            rebuild="true"
        fi

        if [ "$rebuild" = "true" ] || [ ! -d "$src_dir/hermes_cli/web_dist/assets" ]; then
            echo "[run] [$name] Building dashboard frontend..."
            if (cd "$src_dir/web" && npm install --silent 2>&1 | tail -3 && npx vite build --outDir ../hermes_cli/web_dist --emptyOutDir 2>&1 | tail -3); then
                echo "[run] [$name] Dashboard frontend built"
            else
                echo "[run] [$name] Warning: dashboard frontend build failed (dashboard will not be available)"
            fi
        fi
    fi
}

for i in "${!PROFILE_DIRS[@]}"; do
    install_profile "$i"
done

# Activate the primary profile's venv for any tooling (e.g. dashboard module probe).
# shellcheck disable=SC1091
source "$PRIMARY_VENV_DIR/bin/activate"

# Verify version (from primary)
HERMES_VERSION="$("$PRIMARY_VENV_DIR/bin/hermes" --version 2>/dev/null | head -1 || echo "unknown")"
export HERMES_VERSION
echo "[run] Hermes version: $HERMES_VERSION"

# ── Section 6: Initial config scaffolding (per profile) ──────────────
scaffold_profile_files() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local src_dir="${PROFILE_SRC_DIRS[$i]}"
    local name="${PROFILE_NAMES[$i]}"

    if [ ! -f "$home/.env" ] && [ -f "$src_dir/.env.example" ]; then
        cp -p "$src_dir/.env.example" "$home/.env"
        chmod 600 "$home/.env"
        echo "[run] [$name] Created .env from source example (chmod 600)"
    fi
    if [ ! -f "$home/config.yaml" ] && [ -f "$src_dir/cli-config.yaml.example" ]; then
        cp -p "$src_dir/cli-config.yaml.example" "$home/config.yaml"
        echo "[run] [$name] Created config.yaml from source example"
    fi
    if [ ! -f "$home/SOUL.md" ]; then
        cat > "$home/SOUL.md" << 'SOUL_EOF'
# Hermes Agent Persona

<!--
This file defines the agent's personality and tone.
The agent will embody whatever you write here.
Edit this to customize how Hermes communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->
SOUL_EOF
        echo "[run] [$name] Created SOUL.md template"
    fi
}

for i in "${!PROFILE_DIRS[@]}"; do
    scaffold_profile_files "$i"
done

# tmux config (persistent, user-editable, single-instance)
if [ ! -f /config/.tmux.conf ]; then
    cat > /config/.tmux.conf << 'TMUX'
set -g default-terminal "tmux-256color"
set -g history-limit 100000
set -g mouse on
TMUX
    echo "[run] Created default .tmux.conf"
fi

# ── Section 7: Environment variable passthrough ──────────────────────
# apply_env_vars_for_profile + upsert_env_var live in profile-init.sh
for i in "${!PROFILE_DIRS[@]}"; do
    apply_env_vars_for_profile "$i"
done

# HA integration: pass through if set (shared across profiles)
if [ -n "$HASS_TOKEN" ]; then
    export HASS_TOKEN
    echo "[run] HASS_TOKEN injected"
fi
if [ -n "$GIT_TOKEN" ]; then
    export GITHUB_TOKEN="$GIT_TOKEN"
    echo "[run] GITHUB_TOKEN injected"
fi
if [ -n "$HASS_URL" ]; then
    export HASS_URL
    echo "[run] HASS_URL: $HASS_URL"
fi

# nginx htpasswd (shared)
if [ -n "$ACCESS_PASSWORD" ]; then
    echo "hermes:$(openssl passwd -apr1 "$ACCESS_PASSWORD")" > /etc/nginx/.htpasswd
    echo "[run] Access password set (API key + nginx basic auth)"
else
    rm -f /etc/nginx/.htpasswd
fi

# ~/.hermes_profile: regenerated every start (shared, defaults to primary).
# HERMES_HOME is set only if unset, so ttyd subprocesses that pre-set it
# (per-profile sessions) keep their own profile.
cat > /config/.hermes_profile << ENVSH
: "\${HERMES_HOME:=$PRIMARY_HOME}"
export HERMES_HOME
export HERMES_GATEWAY_NO_SUPERVISE=1
export HERMES_VERSION="$HERMES_VERSION"
$([ -n "$GIT_TOKEN" ] && echo "export GITHUB_TOKEN=\"$GIT_TOKEN\"")
export GOBIN="$GO_DIR/bin"
export GOPATH="$GO_DIR"
$([ -n "$HASS_TOKEN" ] && echo "export HASS_TOKEN=\"$HASS_TOKEN\"")
$([ -n "$HASS_URL" ] && echo "export HASS_URL=\"$HASS_URL\"")
export HOMEBREW_CELLAR="$BREW_DIR/Cellar"
export HOMEBREW_PREFIX="$BREW_DIR"
export HOMEBREW_REPOSITORY="$BREW_DIR/Homebrew"
export NPM_CONFIG_PREFIX="$NODE_DIR"
export PATH="\${HERMES_HOME}/hermes-agent/venv/bin:$BREW_DIR/sbin:$BREW_DIR/bin:$GO_DIR/bin:/usr/local/go/bin:$NODE_DIR/bin:\$PATH"
ENVSH

# ── Section 8: TLS certificates (shared) ─────────────────────────────
if [ ! -f "$CERTS_DIR/server.crt" ]; then
    echo "[run] Generating self-signed TLS certificates..."
    openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -keyout "$CERTS_DIR/ca.key" -out "$CERTS_DIR/ca.crt" \
        -days 3650 -subj "/CN=Hermes Agent CA" 2>/dev/null
    openssl req -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
        -keyout "$CERTS_DIR/server.key" -out /tmp/server.csr \
        -subj "/CN=hermes-agent" 2>/dev/null
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
DASHBOARD_AVAILABLE="false"
if "$PRIMARY_VENV_DIR/bin/python" -c "from hermes_cli.web_server import start_server" 2>/dev/null; then
    DASHBOARD_AVAILABLE="true"
fi

if [ -n "$ACCESS_PASSWORD" ]; then
    AUTH_BASIC_ON='auth_basic "Hermes Agent"; auth_basic_user_file /etc/nginx/.htpasswd;'
    AUTH_BASIC_OFF='auth_basic off;'
else
    AUTH_BASIC_ON='# no authentication'
    AUTH_BASIC_OFF=''
fi

# Per-profile dashboard tokens — populated after dashboards start.
# Use a placeholder until then so generated nginx config is still valid.
# (Consumed by emit_token_maps / emit_profile_locations in nginx-render.sh.)
# shellcheck disable=SC2034
DASHBOARD_TOKENS=()
for i in "${!PROFILE_DIRS[@]}"; do
    DASHBOARD_TOKENS[$i]="PENDING_TOKEN_$i"
done

# Nginx rendering helpers (sourced from a separate library for testability)
# Resolve adjacent file when run.sh is executed locally; container puts the lib in /usr/local/lib.
NGINX_RENDER_LIB=""
for _candidate in \
    "/usr/local/lib/hermes-nginx-render.sh" \
    "$(dirname "${BASH_SOURCE[0]}")/nginx-render.sh"; do
    if [ -f "$_candidate" ]; then
        NGINX_RENDER_LIB="$_candidate"
        break
    fi
done
if [ -z "$NGINX_RENDER_LIB" ]; then
    echo "[run] FATAL: nginx-render.sh not found"
    exit 1
fi
# shellcheck source=nginx-render.sh
source "$NGINX_RENDER_LIB"

render_nginx_config() {
    # Render ports config if any direct-port service enabled
    local include_ports
    if [ "$ENABLE_DASHBOARD" = "true" ] || [ "$ENABLE_TERMINAL" = "true" ] || [ "$ENABLE_API" = "true" ]; then
        cp /etc/nginx/nginx-ports.conf.tpl /etc/nginx/ports.conf
        emit_token_maps | substitute_marker /etc/nginx/ports.conf '%%DASHBOARD_TOKEN_MAPS%%'
        emit_profile_locations http | substitute_marker /etc/nginx/ports.conf '%%HTTP_PROFILE_LOCATIONS%%'
        emit_profile_locations https | substitute_marker /etc/nginx/ports.conf '%%HTTPS_PROFILE_LOCATIONS%%'
        sed -i \
            -e "s|%%HTTP_PORT%%|${HTTP_PORT}|g" \
            -e "s|%%HTTPS_PORT%%|${HTTPS_PORT}|g" \
            -e "s|%%CERTS_DIR%%|${CERTS_DIR}|g" \
            -e "s|%%AUTH_BASIC_ON%%|${AUTH_BASIC_ON}|g" \
            -e "s|%%AUTH_BASIC_OFF%%|${AUTH_BASIC_OFF}|g" \
            /etc/nginx/ports.conf
        include_ports="include /etc/nginx/ports.conf;"
        echo "[run] Direct ports: enabled (HTTP: $HTTP_PORT, HTTPS: $HTTPS_PORT)"
    else
        include_ports="# direct ports disabled"
        echo "[run] Direct ports: disabled (Ingress only)"
    fi

    cp /etc/nginx/nginx.conf.tpl /etc/nginx/nginx.conf
    emit_upstreams | substitute_marker /etc/nginx/nginx.conf '%%UPSTREAMS%%'
    emit_dashboard_maps | substitute_marker /etc/nginx/nginx.conf '%%DASHBOARD_MAPS%%'
    emit_profile_locations ingress | substitute_marker /etc/nginx/nginx.conf '%%INGRESS_PROFILE_LOCATIONS%%'
    sed -i \
        -e "s|%%INGRESS_PORT%%|${INGRESS_PORT}|g" \
        -e "s|%%CERTS_DIR%%|${CERTS_DIR}|g" \
        -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
        -e "s|%%INCLUDE_PORTS%%|${include_ports}|g" \
        /etc/nginx/nginx.conf
}

render_nginx_config

# Render landing page
ADDON_SLUG=$(hostname | tr '-' '_')
SHOW_TERMINAL="false"
[ "$ENABLE_TERMINAL" = "true" ] && SHOW_TERMINAL="true"
SHOW_DASHBOARD="$DASHBOARD_AVAILABLE"
SHOW_DASHBOARD_PORTS="false"
if [ "$ENABLE_DASHBOARD" = "true" ] && [ "$DASHBOARD_AVAILABLE" = "true" ]; then
    SHOW_DASHBOARD_PORTS="true"
fi
SHOW_API="false"
[ "$ENABLE_API" = "true" ] && SHOW_API="true"

# Build profiles JSON for the landing page renderer
build_profiles_json() {
    local i pref
    printf '['
    for i in "${!PROFILE_DIRS[@]}"; do
        [ "$i" -gt 0 ] && printf ','
        pref="${PROFILE_PATH_PREFIX[$i]}"
        printf '{"name":%s,"prefix":%s,"primary":%s}' \
            "$(jq -Rn --arg n "${PROFILE_NAMES[$i]}" '$n')" \
            "$(jq -Rn --arg p "$pref" '$p')" \
            "$([ "$i" -eq 0 ] && echo true || echo false)"
    done
    printf ']'
}
PROFILES_JSON="$(build_profiles_json)"

cp /var/www/landing.html.tpl /var/www/landing.html
# PROFILES_JSON contains JSON; use a different delimiter so braces don't collide
PROFILES_JSON_ESC=$(printf '%s' "$PROFILES_JSON" | sed 's|[\\/&]|\\&|g')
sed -i \
    -e "s|%%HERMES_VERSION%%|${HERMES_VERSION}|g" \
    -e "s|%%ADDON_SLUG%%|${ADDON_SLUG}|g" \
    -e "s|%%SHOW_TERMINAL%%|${SHOW_TERMINAL}|g" \
    -e "s|%%SHOW_DASHBOARD%%|${SHOW_DASHBOARD}|g" \
    -e "s|%%SHOW_DASHBOARD_PORTS%%|${SHOW_DASHBOARD_PORTS}|g" \
    -e "s|%%SHOW_API%%|${SHOW_API}|g" \
    -e "s|%%PROFILES_JSON%%|${PROFILES_JSON_ESC}|g" \
    /var/www/landing.html

echo "[run] Nginx configured (ingress: $INGRESS_PORT, HTTP: $HTTP_PORT, HTTPS: $HTTPS_PORT)"

# ── Section 10: Start services (per profile) ─────────────────────────
GATEWAY_PIDS=()
TTYD_HERMES_PIDS=()
TTYD_TERMINAL_PIDS=()
DASHBOARD_PIDS=()

start_gateway_for_profile() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local venv="${PROFILE_VENV_DIRS[$i]}"
    local name="${PROFILE_NAMES[$i]}"
    local port="${API_PORTS[$i]}"

    echo "[run] [$name] Starting gateway (API port: $port)..."
    mkdir -p "$home/logs"
    (
        cd "$home"
        export HERMES_HOME="$home"
        export PATH="$venv/bin:$BASE_PATH"
        "$venv/bin/hermes" gateway run 2>&1 | tee -a "$home/logs/gateway.log"
    ) &
    local tee_pid=$!
    sleep 0.5
    # Find the matching gateway PID (cwd points to this profile's home)
    local pid=""
    for candidate in $(pgrep -f "hermes gateway run" 2>/dev/null | sort -n); do
        local cwd
        cwd=$(readlink "/proc/$candidate/cwd" 2>/dev/null || echo "")
        if [ "$cwd" = "$home" ]; then
            pid="$candidate"
            break
        fi
    done
    [ -z "$pid" ] && pid="$tee_pid"
    GATEWAY_PIDS[$i]="$pid"
    echo "[run] [$name] Gateway PID: $pid (tee: $tee_pid)"
}

# Install the dedicated hermes startup wrapper (shared, sources .bashrc).
# Each ttyd subprocess sets HERMES_HOME via env before exec, so .bashrc sources
# the right .env for that session.
install_start_hermes_wrapper() {
    cat > /usr/local/bin/start-hermes << 'WRAPPER'
#!/bin/bash
source ~/.bashrc
hermes
ret=$?
if [ $ret -eq 0 ]; then exit 0; fi
echo ""
echo "Hermes exited with code $ret. Shell is available for debugging."
echo "Run 'hermes' to restart, or 'exit' to close."
exec bash
WRAPPER
    chmod +x /usr/local/bin/start-hermes
}

start_ttyd_for_profile() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local name="${PROFILE_NAMES[$i]}"
    local prefix="${PROFILE_PATH_PREFIX[$i]}"
    local hermes_port="${TTYD_HERMES_PORTS[$i]}"
    local term_port="${TTYD_TERMINAL_PORTS[$i]}"

    echo "[run] [$name] Starting ttyd (hermes: $hermes_port, terminal: $term_port)..."
    env HERMES_HOME="$home" \
        ttyd \
            --port "$hermes_port" \
            --interface 127.0.0.1 \
            --base-path "${prefix}/hermes/" \
            --writable -d 3 \
            tmux -u new -A -s "hermes-${name}" /usr/local/bin/start-hermes &
    TTYD_HERMES_PIDS[$i]=$!

    env HERMES_HOME="$home" \
        ttyd \
            --port "$term_port" \
            --interface 127.0.0.1 \
            --base-path "${prefix}/terminal/" \
            --writable -d 3 \
            tmux -u new -A -s "terminal-${name}" /usr/bin/bash &
    TTYD_TERMINAL_PIDS[$i]=$!
    echo "[run] [$name] ttyd PIDs: hermes=${TTYD_HERMES_PIDS[$i]} terminal=${TTYD_TERMINAL_PIDS[$i]}"
}

start_dashboard_for_profile() {
    local i="$1"
    local home="${PROFILE_HOMES[$i]}"
    local venv="${PROFILE_VENV_DIRS[$i]}"
    local name="${PROFILE_NAMES[$i]}"
    local port="${DASHBOARD_PORTS[$i]}"

    if [ "$DASHBOARD_AVAILABLE" != "true" ]; then
        echo "[run] [$name] Dashboard: not available (web_server module not found)"
        return
    fi
    echo "[run] [$name] Starting dashboard (port: $port)..."
    (
        cd "$home"
        export HERMES_HOME="$home"
        exec "$venv/bin/python" -c "from hermes_cli.web_server import start_server; start_server(host='127.0.0.1', port=${port}, open_browser=False)"
    ) &
    DASHBOARD_PIDS[$i]=$!
    echo "[run] [$name] Dashboard PID: ${DASHBOARD_PIDS[$i]}"
}

# Read the dashboard's ephemeral session token from a running dashboard.
# The dashboard generates a random token on each start and embeds it in index.html.
inject_dashboard_token_for_profile() {
    local i="$1"
    local name="${PROFILE_NAMES[$i]}"
    local port="${DASHBOARD_PORTS[$i]}"

    if [ "$DASHBOARD_AVAILABLE" != "true" ]; then
        return
    fi
    echo "[run] [$name] Waiting for dashboard token..."
    local token=""
    for _ in $(seq 1 15); do
        token=$(curl -s "http://127.0.0.1:${port}/" 2>/dev/null \
            | grep -oP '__HERMES_SESSION_TOKEN__="\K[^"]+' || true)
        if [ -n "$token" ]; then
            break
        fi
        sleep 2
    done
    if [ -z "$token" ]; then
        echo "[run] [$name] Warning: could not read dashboard token (dashboard API auth may not work)"
        token="UNAVAILABLE"
    fi
    # shellcheck disable=SC2034  # consumed by emit_token_maps / emit_profile_locations in nginx-render.sh
    DASHBOARD_TOKENS[$i]="$token"
    echo "[run] [$name] Dashboard token obtained (${#token} chars)"
}

reload_nginx() {
    echo "[run] Reloading nginx with full config..."
    nginx -s reload
    echo "[run] nginx reloaded"
}

# Register signal handler BEFORE starting services
trap shutdown SIGTERM SIGINT

install_start_hermes_wrapper

for i in "${!PROFILE_DIRS[@]}"; do
    start_gateway_for_profile "$i"
    start_ttyd_for_profile "$i"
    start_dashboard_for_profile "$i"
done

for i in "${!PROFILE_DIRS[@]}"; do
    inject_dashboard_token_for_profile "$i"
done

# Re-render nginx now that we have real dashboard tokens
render_nginx_config

reload_nginx

echo "[run] All services started"
BASE_URL="${HASS_URL:-http://localhost}"
BASE_SCHEME="${BASE_URL%%://*}"
BASE_HOST="${BASE_URL#*://}"
BASE_HOST="${BASE_HOST%%:*}"
BASE_HOST="${BASE_HOST%%/*}"
if [ "$BASE_SCHEME" = "https" ]; then
    BASE_URL="${BASE_SCHEME}://${BASE_HOST}:${HTTPS_PORT}"
else
    BASE_URL="${BASE_SCHEME}://${BASE_HOST}:${HTTP_PORT}"
fi
echo "─────────────────────────────────────────────"
echo " ${HERMES_VERSION}"
for i in "${!PROFILE_DIRS[@]}"; do
    prefix="${PROFILE_PATH_PREFIX[$i]}"
    label="${PROFILE_NAMES[$i]}"
    echo " Profile ${label} (PID ${GATEWAY_PIDS[$i]}):"
    echo "   Hermes:    ${BASE_URL}${prefix}/hermes/"
    [ "$DASHBOARD_AVAILABLE" = "true" ] && echo "   Dashboard: ${BASE_URL}${prefix}/dashboard/"
    echo "   Terminal:  ${BASE_URL}${prefix}/terminal/"
    echo "   API:       ${BASE_URL}${prefix}/v1/"
done
echo "─────────────────────────────────────────────"

# ── Section 11: Signal handling ──────────────────────────────────────
shutdown() {
    echo ""
    echo "[run] Shutting down..."
    nginx -s quit 2>/dev/null || true
    echo "[run] nginx stopped"
    for i in "${!PROFILE_DIRS[@]}"; do
        for pid in "${TTYD_TERMINAL_PIDS[$i]:-}" "${TTYD_HERMES_PIDS[$i]:-}" "${DASHBOARD_PIDS[$i]:-}"; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
            fi
        done
    done
    echo "[run] ttyd + dashboards stopped"
    for i in "${!PROFILE_DIRS[@]}"; do
        local pid="${GATEWAY_PIDS[$i]:-}"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
            local waited=0
            while kill -0 "$pid" 2>/dev/null && [ $waited -lt 10 ]; do
                sleep 1
                waited=$((waited + 1))
            done
            if kill -0 "$pid" 2>/dev/null; then
                echo "[run] [${PROFILE_NAMES[$i]}] Gateway didn't stop gracefully, force killing..."
                kill -9 "$pid" 2>/dev/null || true
            fi
            echo "[run] [${PROFILE_NAMES[$i]}] Gateway stopped"
        fi
    done
    echo "[run] Shutdown complete"
    exit 0
}

# ── Section 12: Supervisor loop ──────────────────────────────────────
while true; do
    for i in "${!PROFILE_DIRS[@]}"; do
        pid="${GATEWAY_PIDS[$i]:-}"
        if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
            set +e; wait "$pid" 2>/dev/null; EXIT_CODE=$?; set -e
            if [ "$EXIT_CODE" -eq 0 ]; then
                echo "[run] [${PROFILE_NAMES[$i]}] Gateway exited normally (code 0) — restarting in 3s..."
                echo "[run] (Use the shutdown handler to stop the container.)"
            else
                echo "[run] [${PROFILE_NAMES[$i]}] Gateway exited unexpectedly (code: $EXIT_CODE), restarting in 3s..."
            fi
            sleep 3
            start_gateway_for_profile "$i"
        fi
    done
    sleep 5
done

shutdown
