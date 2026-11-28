#!/bin/bash
# shellcheck shell=bash
# Optional Hermes Webapp lifecycle helpers.
#
# The Webapp is the browser-hosted Desktop workspace. Its listener belongs to
# the gateway of the primary profile: it starts with the gateway and dies with
# it, so no extra process is supervised here. This library only owns the
# configuration seam, the bundle build and the warnings.

WEBAPP_PORT="${WEBAPP_PORT:-9120}"
WEBAPP_BUILD_LOG="${WEBAPP_BUILD_LOG:-}"
WEBAPP_BUILD_PID="${WEBAPP_BUILD_PID:-}"
WEBAPP_AUTH_STATE_HELPER="${WEBAPP_AUTH_STATE_HELPER:-/usr/local/bin/hermes-webapp-auth-state}"


webapp_validate_options() {
    if [ "${ENABLE_WEBAPP:-false}" != "true" ]; then
        return 0
    fi
    # A wildcard bind is behind the dashboard sign-in gate and fails closed.
    # Without a credential the listener would refuse to serve, so the option is
    # refused up front instead of shipping an unreachable port.
    local password="${ACCESS_PASSWORD:-}"
    if [ -z "$password" ] || [ -z "${password//[[:space:]]/}" ]; then
        echo "[webapp] FATAL: enable_webapp requires access_password" >&2
        return 1
    fi
}


# The bundled Basic-auth provider strips surrounding whitespace from a plaintext
# password before hashing it, while nginx hashes the dashboard password exactly
# as entered. Handing the provider a byte-exact scrypt hash of the configured
# password (the #17 Desktop-backend approach) keeps every access path on the
# same credential, padding included, and keeps the plaintext out of the
# gateway's environment entirely.
# Since Hermes v0.23.2 the session signing key is derived from the signing
# secret *and* the credential (the password hash), so a hash that is salted
# afresh on every start ends every session whenever the add-on restarts. The
# state file therefore keeps the verified hash together with the secret: both
# are reused while the configured password still verifies, and both are
# replaced when it changes. That also retires the unsalted SHA-256 fingerprint,
# which was a fast-to-guess digest of the password on disk.
webapp_auth_state() {
    local home="$1"
    # The helper lives next to this library in the image; the plugin package comes
    # from the checkout, so import from there instead of relying on the checkout
    # happening to be on sys.path.
    local root="${SRC_DIR:-$home}"
    if [ ! -d "$root/plugins/dashboard_auth" ]; then
        echo "[webapp] FATAL: no Hermes checkout with plugins/dashboard_auth at $root" >&2
        return 1
    fi
    (
        cd "$root" || exit 1
        export HERMES_HOME="$home"
        export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
        printf '%s' "${ACCESS_PASSWORD:-}" \
            | "${VENV_DIR:-}/bin/python" "$WEBAPP_AUTH_STATE_HELPER" "$home"
    )
}


webapp_validate_runtime() {
    if [ "${ENABLE_WEBAPP:-false}" != "true" ]; then
        return 0
    fi
    local hermes="${VENV_DIR:-}/bin/hermes"
    if [ ! -x "$hermes" ]; then
        echo "[webapp] FATAL: Hermes CLI is unavailable" >&2
        return 1
    fi
    local webapp_help
    if ! webapp_help=$("$hermes" webapp --help 2>&1); then
        echo "[webapp] FATAL: installed Hermes does not support 'hermes webapp'" >&2
        return 1
    fi
    case "$webapp_help" in
        *--build-only*) ;;
        *)
            echo "[webapp] FATAL: installed Hermes 'webapp' does not support required --build-only" >&2
            return 1
            ;;
    esac
}


# A plain CLI call whose HERMES_HOME sits at a machine root follows a sticky
# `hermes profile use` selection and would then write into that profile. Pinning
# `-p default` resolves to that very same root (profiles.profile_root_for_env_home),
# while a home below .../profiles is already unambiguous and must not be pinned.
webapp_profile_pin() {
    local home="$1"
    if [ "$(basename "$(dirname "$home")")" = "profiles" ]; then
        return 0
    fi
    printf '%s' "default"
}


# Run the Hermes CLI against exactly *home*, never a sticky interactive profile.
webapp_cli() {
    local home="$1"
    shift
    local pin
    pin=$(webapp_profile_pin "$home")
    if [ -n "$pin" ]; then
        HERMES_HOME="$home" "${VENV_DIR:-}/bin/hermes" -p "$pin" "$@"
    else
        HERMES_HOME="$home" "${VENV_DIR:-}/bin/hermes" "$@"
    fi
}


webapp_configure() {
    local home="$1"
    local hermes="${VENV_DIR:-}/bin/hermes"
    if [ "${ENABLE_WEBAPP:-false}" != "true" ] \
        && ! grep -qs '^[[:space:]]*webapp:' "$home/config.yaml" 2>/dev/null; then
        # Nothing to clear and nothing to write: skip the Hermes CLI start,
        # which costs seconds per profile on every boot of every user.
        return 0
    fi
    if [ ! -x "$hermes" ]; then
        echo "[webapp] FATAL: Hermes CLI is unavailable" >&2
        return 1
    fi

    if [ "${ENABLE_WEBAPP:-false}" != "true" ]; then
        # Only clear a block that is switched on; never create one on a
        # profile that never had the Webapp. `config get` exits non-zero for a
        # key without a default, which must not abort the entrypoint.
        local current=""
        current=$(webapp_cli "$home" config get webapp.enabled --raw 2>/dev/null) || current=""
        current=$(printf '%s' "$current" | tr -d '\r')
        case "$current" in
            true|True|TRUE)
                if webapp_cli "$home" config set --force webapp.enabled false >/dev/null; then
                    echo "[webapp] Disabled webapp.enabled in $home/config.yaml"
                else
                    echo "[webapp] WARNING: could not disable webapp.enabled in $home/config.yaml" >&2
                fi
                ;;
        esac
        return 0
    fi

    # host and port are read once, when the gateway starts; the add-on
    # therefore writes both before the first gateway and never restarts it.
    if ! webapp_cli "$home" config set --force webapp.enabled true >/dev/null \
        || ! webapp_cli "$home" config set --force webapp.host 0.0.0.0 >/dev/null \
        || ! webapp_cli "$home" config set --force webapp.port "$WEBAPP_PORT" >/dev/null; then
        echo "[webapp] FATAL: could not write the webapp settings to $home/config.yaml" >&2
        return 1
    fi
    echo "[webapp] Listening on 0.0.0.0:${WEBAPP_PORT} (config.yaml in $home)"
}


webapp_warn_if_basic_auth_disabled() {
    local home="$1"
    if [ "${ENABLE_WEBAPP:-false}" != "true" ]; then
        return 0
    fi
    # `plugins.disabled` has no default, so `config get` exits non-zero when it
    # was never written; under `set -e` that must not abort the entrypoint.
    local disabled=""
    disabled=$(webapp_cli "$home" config get plugins.disabled --json 2>/dev/null) || disabled=""
    disabled=$(printf '%s' "$disabled" | tr -d '\r')
    case "$disabled" in
        *'"basic"'*|*"'basic'"*)
            echo "[webapp] WARNING: the bundled 'basic' dashboard-auth plugin is disabled in $home/config.yaml." >&2
            echo "[webapp] WARNING: password sign-in cannot work while it stays disabled; run 'hermes plugins enable basic'." >&2
            ;;
    esac
}


# The bundle build is the only expensive step (~7 minutes and ~900 MB of build
# dependencies on a Raspberry Pi 4). The listener serves a bundle the moment it
# appears, so it runs in the background and never delays startup or needs a
# gateway restart. An image step pays it once per release; here it runs once per
# install and again only when a version bump invalidates the build receipt.
webapp_start_build() {
    local home="$1"
    if [ "${ENABLE_WEBAPP:-false}" != "true" ]; then
        return 0
    fi
    local log="${WEBAPP_BUILD_LOG:-$home/logs/webapp-build.log}"
    mkdir -p "$(dirname "$log")" 2>/dev/null || true
    echo "[webapp] Building or verifying the bundle in the background (log: $log)"
    local timeout_seconds="${WEBAPP_BUILD_TIMEOUT:-3600}"
    (
        cd "$home" || exit 1
        export HERMES_HOME="$home"
        export PATH="${VENV_DIR:-}/bin:$PATH"
        # The build runs npm/pnpm lifecycle scripts from a shared checkout; the
        # add-on's own credentials have no business in that environment.
        unset ACCESS_PASSWORD WEBAPP_PASSWORD_HASH WEBAPP_AUTH_SECRET HERMES_ADDON_API_KEY
        # The Supervisor tokens grant Home Assistant admin access; npm/pnpm
        # lifecycle scripts from a dependency tree we do not control have no
        # business with them. Keys from the profile's own .env are still inherited
        # by the build (the CLI loads that file itself) — see the README.
        # Guard the unthrottled source build: build_source_webapp() does not
        # apply the dashboard's build limits (no heap cap, no thread cap, no
        # build timeout). A cap the operator already set stays: appending a
        # second --max-old-space-size would override it.
        case "${NODE_OPTIONS:-}" in
            *--max-old-space-size*) ;;
            *) export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--max-old-space-size=2048" ;;
        esac
        export RAYON_NUM_THREADS="${RAYON_NUM_THREADS:-2}"
        # Low priority and a hard deadline: a stalled registry fetch must not
        # hold the build lock or starve the gateway forever.
        local pin
        pin=$(webapp_profile_pin "$home")
        local -a cmd=("${VENV_DIR:-}/bin/hermes")
        [ -n "$pin" ] && cmd+=(-p "$pin")
        cmd+=(webapp --build-only)
        status=0
        env -u SUPERVISOR_TOKEN -u HASSIO_TOKEN \
            nice -n 10 timeout -k 30 "$timeout_seconds" "${cmd[@]}" || status=$?
        if [ "$status" -ne 0 ]; then
            # Mirror `hermes update`: a failed or killed rebuild marks the
            # bundle stale, so the listener refuses a possibly half-written or
            # outdated UI instead of serving it until the next successful build.
            if ! "${VENV_DIR:-}/bin/python" -c \
                'from hermes_cli.config import get_project_root
from hermes_cli.webapp import mark_webapp_stale
mark_webapp_stale(get_project_root())'; then
                echo "[webapp] WARNING: could not mark the bundle stale; the listener may serve an unfinished build" >&2
            fi
        fi
        exit "$status"
    ) >>"$log" 2>&1 &
    WEBAPP_BUILD_PID=$!
    echo "[webapp] Build process: PID ${WEBAPP_BUILD_PID}"
    return 0
}
