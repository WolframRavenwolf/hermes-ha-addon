#!/bin/bash
# shellcheck shell=bash
# Optional Hermes Desktop remote-backend lifecycle helpers.

DESKTOP_BACKEND_PORT="${DESKTOP_BACKEND_PORT:-9119}"
DESKTOP_BACKEND_LAUNCHER="${DESKTOP_BACKEND_LAUNCHER:-/usr/local/bin/hermes-desktop-backend}"
DESKTOP_BACKEND_STOP_TIMEOUT="${DESKTOP_BACKEND_STOP_TIMEOUT:-10}"
DESKTOP_BACKEND_PID="${DESKTOP_BACKEND_PID:-}"


desktop_backend_validate_options() {
    if [ "${ENABLE_DESKTOP_BACKEND:-false}" != "true" ]; then
        return 0
    fi
    local password="${ACCESS_PASSWORD:-}"
    if [ -z "$password" ] || [ -z "${password//[[:space:]]/}" ]; then
        echo "[desktop-backend] FATAL: enable_desktop_backend requires access_password" >&2
        return 1
    fi
}


desktop_backend_validate_runtime() {
    if [ "${ENABLE_DESKTOP_BACKEND:-false}" != "true" ]; then
        return 0
    fi
    if [ ! -x "${VENV_DIR:-}/bin/python" ]; then
        echo "[desktop-backend] FATAL: Hermes venv Python is unavailable" >&2
        return 1
    fi
    if [ ! -x "${VENV_DIR:-}/bin/hermes" ]; then
        echo "[desktop-backend] FATAL: Hermes CLI is unavailable" >&2
        return 1
    fi
    if [ ! -f "$DESKTOP_BACKEND_LAUNCHER" ]; then
        echo "[desktop-backend] FATAL: launcher not found: $DESKTOP_BACKEND_LAUNCHER" >&2
        return 1
    fi
    local serve_help
    if ! serve_help=$("$VENV_DIR/bin/hermes" serve --help 2>&1); then
        echo "[desktop-backend] FATAL: installed Hermes does not support 'hermes serve'" >&2
        return 1
    fi
    case "$serve_help" in
        *--skip-build*) ;;
        *)
            echo "[desktop-backend] FATAL: installed Hermes 'serve' does not support required --skip-build" >&2
            return 1
            ;;
    esac
}


desktop_backend_start() {
    if [ "${ENABLE_DESKTOP_BACKEND:-false}" != "true" ]; then
        DESKTOP_BACKEND_PID=""
        return 0
    fi

    echo "[desktop-backend] WARNING: enabled on container port ${DESKTOP_BACKEND_PORT}; expose only on a trusted LAN/VPN/Tailscale path."
    echo "[desktop-backend] WARNING: authenticated users receive powerful, full agent control and accept that risk."
    echo "[desktop-backend] Starting official Hermes Desktop backend..."
    (
        cd "$PRIMARY_HOME"
        export HERMES_HOME="$PRIMARY_HOME"
        export PATH="$VENV_DIR/bin:$BASE_PATH"
        exec "$VENV_DIR/bin/python" "$DESKTOP_BACKEND_LAUNCHER" \
            --host 0.0.0.0 \
            --port "$DESKTOP_BACKEND_PORT" \
            --skip-build <<<"$ACCESS_PASSWORD"
    ) &
    DESKTOP_BACKEND_PID=$!
    echo "[desktop-backend] PID: $DESKTOP_BACKEND_PID"
}


desktop_backend_stop() {
    local pid="${DESKTOP_BACKEND_PID:-}"
    if [ -z "$pid" ]; then
        return 0
    fi
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
        local waited=0
        while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt "$DESKTOP_BACKEND_STOP_TIMEOUT" ]; do
            sleep 1
            waited=$((waited + 1))
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "[desktop-backend] Process did not stop gracefully; force killing..."
            kill -KILL "$pid" 2>/dev/null || true
        fi
        wait "$pid" 2>/dev/null || true
    fi
    DESKTOP_BACKEND_PID=""
    echo "[desktop-backend] Stopped"
}


desktop_backend_supervise() {
    if [ "${ENABLE_DESKTOP_BACKEND:-false}" != "true" ]; then
        return 0
    fi
    local pid="${DESKTOP_BACKEND_PID:-}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi

    local exit_code=0
    if [ -n "$pid" ]; then
        set +e
        wait "$pid" 2>/dev/null
        exit_code=$?
        set -e
    fi
    echo "[desktop-backend] Exited (code: $exit_code); restarting..."
    desktop_backend_start
}
