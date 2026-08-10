#!/bin/bash
# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────
# API server option validation library (sourced by run.sh and tests).
#
# Reads and, when the API is enabled, normalizes these caller variables:
#   ENABLE_API       Home Assistant enable_api option
#   ACCESS_PASSWORD  shared direct-access password and API server key
# ─────────────────────────────────────────────────────────────────────

_api_server_credential_error() {
    printf '%s%s\n' \
        "[api-server] FATAL: enable_api=true requires access_password to be a " \
        "non-placeholder value of at least 16 safe ASCII characters" >&2
}

_api_server_line_break_error() {
    printf '%s%s\n' \
        "[api-server] FATAL: access_password must be a single-line value " \
        "without line breaks" >&2
}

api_server_read_json_string() {
    local options_file="$1" key="$2" default_value="${3-}"
    local output_name="${4-}"
    local marker="__HERMES_JSON_STRING_END_7B41D9F3__" result value
    if ! jq -e --arg key "$key" '
        if has($key) and .[$key] != null then
          ((.[$key] | tostring | index("\u0000")) == null)
        else
          true
        end' "$options_file" >/dev/null; then
        return 1
    fi
    result="$(jq -rj \
        --arg key "$key" \
        --arg default_value "$default_value" \
        --arg marker "$marker" \
        '((if has($key) then (.[$key] // $default_value) else $default_value end)
          | tostring) + $marker' \
        "$options_file")" || return 1
    case "$result" in
        *"$marker") value="${result%$marker}" ;;
        *) return 1 ;;
    esac
    if [ -n "$output_name" ]; then
        printf -v "$output_name" '%s' "$value"
    else
        printf '%s' "$value"
    fi
}

api_server_validate_env_records() {
    local options_file="$1"
    if HERMES_OPTIONS_FILE="$options_file" /usr/bin/python3 -B - <<'PY'
import json
import os
import re

name_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
with open(os.environ["HERMES_OPTIONS_FILE"], encoding="utf-8") as handle:
    options = json.load(handle)

configured_profiles = options.get("profiles", []) or []
if not isinstance(configured_profiles, list) or not all(
    isinstance(profile, str) for profile in configured_profiles
):
    raise ValueError("profiles")

for option_name in ("env_vars", "profile_env_vars"):
    records = options.get(option_name, [])
    if not isinstance(records, list):
        raise ValueError(option_name)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(option_name)
        name = record.get("name")
        value = record.get("value")
        if not isinstance(name, str) or name_pattern.fullmatch(name) is None:
            raise ValueError(option_name)
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError(option_name)
        if option_name == "profile_env_vars":
            profile = record.get("profile")
            if (
                not isinstance(profile, str)
                or "\n" in profile
                or "\r" in profile
                or profile not in configured_profiles
            ):
                raise ValueError(option_name)
PY
    then
        return 0
    fi
    printf '%s\n' \
        "[api-server] FATAL: environment variable names or values are invalid" >&2
    return 1
}

_api_server_normalize_credential() {
    HERMES_API_ACCESS_PASSWORD="${ACCESS_PASSWORD-}" /usr/bin/python3 -B - <<'PY'
import os
import sys

placeholders = {
    "*", "**", "***", "changeme", "your_api_key", "your_api_key_here",
    "your-api-key", "placeholder", "example", "dummy", "null", "none",
}
normalized = os.environ.get("HERMES_API_ACCESS_PASSWORD", "").strip()
safe_ascii = all(0x20 <= ord(character) <= 0x7E for character in normalized)
if (
    len(normalized) < 16
    or normalized.lower() in placeholders
    or not safe_ascii
    or "'" in normalized
    or "\\" in normalized
    or "${" in normalized
):
    raise SystemExit(1)
sys.stdout.write(normalized)
PY
}

api_server_validate_options() {
    case "${ACCESS_PASSWORD-}" in
        *$'\n'*|*$'\r'*)
            _api_server_line_break_error
            return 1
            ;;
    esac

    [ "${ENABLE_API:-false}" = "true" ] || return 0

    local normalized
    if ! normalized="$(_api_server_normalize_credential)"; then
        _api_server_credential_error
        return 1
    fi

    ACCESS_PASSWORD="$normalized"
    return 0
}
