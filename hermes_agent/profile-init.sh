#!/bin/bash
# shellcheck shell=bash
# shellcheck disable=SC2034  # arrays populated here are consumed by run.sh + nginx-render.sh
# ─────────────────────────────────────────────────────────────────────
# Profile initialisation library (sourced by run.sh and by tests).
#
# Reads from caller env:
#   OPTIONS_FILE          path to HA add-on options.json
#   HERMES_HOME_DIR       legacy single-profile fallback (string, may be empty)
#   PROFILES_BASE         default-base for bare profile names (default ".hermes/profiles")
#   HOME                  user home (per-profile homes are $HOME/<effective_dir>)
#
# Populates arrays in the caller's scope:
#   PROFILE_DIRS[]        raw "home" string per profile
#   PROFILE_NAMES[]       sanitized identifier per profile (alnum + _)
#   PROFILE_HOMES[]       $HOME/<dir>
#   PROFILE_PATH_PREFIX[] "" for primary, "/profile/<name>" otherwise
#   PROFILE_MARKER[]      install-marker file per profile
#   API_PORTS[]           per-profile gateway API server port
#   TTYD_HERMES_PORTS[]   per-profile ttyd hermes port
#   TTYD_TERMINAL_PORTS[] per-profile ttyd terminal port
#   DASHBOARD_PORTS[]     per-profile hermes dashboard port
# ─────────────────────────────────────────────────────────────────────

# Env vars the add-on owns; users cannot override these via env_vars / profile env_vars.
RESERVED_VARS="HERMES_HOME|HASS_TOKEN|HASS_URL|GITHUB_TOKEN|API_SERVER_PORT|API_SERVER_HOST"

API_BASE_PORT=8642
TTYD_HERMES_BASE_PORT=49269
TTYD_TERMINAL_BASE_PORT=49369
DASHBOARD_BASE_PORT=49469

# Sanitize a raw directory name into a safe identifier (nginx upstream, tmux session, URL segment).
# Strips leading dot, collapses non-alnum to `_`, drops trailing `_`. Empty result is an error.
sanitize_profile_name() {
  local raw="$1"
  local base="${raw##*/}" # basename
  base="${base#.}"        # strip leading dot
  local name
  name="$(printf '%s' "$base" | tr -cs '[:alnum:]_' '_')"
  name="${name%_}"
  printf '%s' "$name"
}

# Resolve a `profiles[]` entry into its on-disk relative directory.
# Bare names (no leading `.`) are placed under PROFILES_BASE so users can write
# `finance-ana` instead of `.hermes/profiles/finance-ana`. Entries starting
# with `.` are kept as-is so the legacy `.hermes` path keeps working.
_resolve_profile_dir() {
  local raw="$1" base="$2"
  if [ -z "$base" ]; then
    printf '%s' "$raw"
    return
  fi
  case "$raw" in
    .*) printf '%s' "$raw" ;;
    *) printf '%s/%s' "$base" "$raw" ;;
  esac
}

# Read the profiles list (or legacy hermes_home) and populate all PROFILE_* + port arrays.
resolve_profiles() {
  PROFILE_DIRS=()
  local _line
  local profiles_from_list=true
  while IFS= read -r _line; do
    [ -n "$_line" ] && PROFILE_DIRS+=("$_line")
  done < <(jq -r '.profiles[]? // empty' "$OPTIONS_FILE")
  if [ "${#PROFILE_DIRS[@]}" -eq 0 ]; then
    PROFILE_DIRS=("${HERMES_HOME_DIR:-.hermes}")
    profiles_from_list=false
  fi

  PROFILE_NAMES=()
  PROFILE_HOMES=()
  PROFILE_PATH_PREFIX=()
  PROFILE_MARKER=()

  local i j dir effective_dir name
  local base="${PROFILES_BASE-.hermes/profiles}"
  for i in "${!PROFILE_DIRS[@]}"; do
    dir="${PROFILE_DIRS[$i]}"
    if [ "$profiles_from_list" = "true" ]; then
      effective_dir="$(_resolve_profile_dir "$dir" "$base")"
      case "$dir" in
        .*) ;;
        *)
          if [ -n "$base" ] && [ -d "$HOME/$dir" ] && [ ! -e "$HOME/$effective_dir" ]; then
            echo "[profile-init] WARNING: using existing legacy profile directory '$dir' instead of '$effective_dir'; migrate the profile data or set profiles_base to empty to keep flat paths intentionally" >&2
            effective_dir="$dir"
          fi
          ;;
      esac
    else
      effective_dir="$dir"
    fi
    name="$(sanitize_profile_name "$dir")"
    if [ -z "$name" ]; then
      echo "[profile-init] FATAL: profile dir '$dir' yields empty name after sanitization" >&2
      return 1
    fi
    for j in "${!PROFILE_NAMES[@]}"; do
      if [ "${PROFILE_NAMES[$j]}" = "$name" ]; then
        echo "[profile-init] FATAL: profile name collision ('$name' from '$dir' and '${PROFILE_DIRS[$j]}'); rename one" >&2
        return 1
      fi
    done
    PROFILE_NAMES[i]="$name"
    PROFILE_HOMES[i]="$HOME/$effective_dir"
    if [ "$i" -eq 0 ]; then
      PROFILE_PATH_PREFIX[i]=""
    else
      PROFILE_PATH_PREFIX[i]="/profile/$name"
    fi
    PROFILE_MARKER[i]="$HOME/.hermes_install_${name}"
  done

  API_PORTS=()
  TTYD_HERMES_PORTS=()
  TTYD_TERMINAL_PORTS=()
  DASHBOARD_PORTS=()
  for i in "${!PROFILE_DIRS[@]}"; do
    API_PORTS[i]=$((API_BASE_PORT + i))
    TTYD_HERMES_PORTS[i]=$((TTYD_HERMES_BASE_PORT + i))
    TTYD_TERMINAL_PORTS[i]=$((TTYD_TERMINAL_BASE_PORT + i))
    DASHBOARD_PORTS[i]=$((DASHBOARD_BASE_PORT + i))
  done
}

# Portable in-place edit (works on GNU sed + BSD sed without temp-file leftovers).
_sed_inplace() {
  local file="$1"
  shift
  local tmp
  tmp="$(mktemp)"
  sed "$@" "$file" >"$tmp" && mv "$tmp" "$file"
}

# Update or append KEY=VALUE in a dotenv-style file.
# Values may contain sed-special characters (`&` `|` `\`); escape them before
# substituting on the replacement side. The append path writes the raw value.
upsert_env_var() {
  local env_file="$1" key="$2" value="$3"
  local escaped_value
  escaped_value="$(printf '%s' "$value" | sed 's/[\\&|]/\\&/g')"
  if grep -q "^${key}=" "$env_file"; then
    _sed_inplace "$env_file" "s|^${key}=.*|${key}=${escaped_value}|"
  else
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
  fi
}

# Apply the merged env_vars (top-level + per-profile overrides + add-on-owned vars)
# into a single profile's .env file.
#
# Reads from caller env:
#   PROFILE_HOMES[i] PROFILE_NAMES[i] API_PORTS[i]
#   ENABLE_API ACCESS_PASSWORD
apply_env_vars_for_profile() {
  local i="$1"
  local env_file="${PROFILE_HOMES[$i]}/.env"
  local name="${PROFILE_NAMES[$i]}"

  [ -f "$env_file" ] || return 0

  # Top-level env_vars (applied to every profile)
  local pairs
  pairs="$(jq -r '.env_vars[]? | "\(.name)=\(.value)"' "$OPTIONS_FILE" 2>/dev/null || true)"
  if [ -n "$pairs" ]; then
    while IFS= read -r line; do
      local k="${line%%=*}" v="${line#*=}"
      if echo "$k" | grep -qE "^($RESERVED_VARS)$"; then
        echo "[run] [$name] Warning: skipping top-level env '$k' (use dedicated option)"
        continue
      fi
      [ -n "$v" ] || continue
      upsert_env_var "$env_file" "$k" "$v"
    done <<<"$pairs"
  fi

  # Per-profile overrides (flat list; entries match on the raw `home` string).
  local home_id="${PROFILE_DIRS[$i]}"
  local overrides
  overrides="$(jq -r --arg id "$home_id" \
    '.profile_env_vars[]? | select(.profile == $id) | "\(.name)=\(.value)"' \
    "$OPTIONS_FILE" 2>/dev/null || true)"
  if [ -n "$overrides" ]; then
    while IFS= read -r line; do
      local k="${line%%=*}" v="${line#*=}"
      if echo "$k" | grep -qE "^($RESERVED_VARS)$"; then
        echo "[run] [$name] Warning: skipping per-profile env '$k' (use dedicated option)"
        continue
      fi
      [ -n "$v" ] || continue
      upsert_env_var "$env_file" "$k" "$v"
      echo "[run] [$name] .env override: $k"
    done <<<"$overrides"
  fi

  # Per-profile API server binding (always owned by the add-on)
  upsert_env_var "$env_file" "API_SERVER_HOST" "127.0.0.1"
  upsert_env_var "$env_file" "API_SERVER_PORT" "${API_PORTS[$i]}"

  # API server enabled/disabled (shared)
  upsert_env_var "$env_file" "API_SERVER_ENABLED" "$ENABLE_API"

  # Optional shared API key
  if [ -n "$ACCESS_PASSWORD" ]; then
    upsert_env_var "$env_file" "API_SERVER_KEY" "$ACCESS_PASSWORD"
  elif grep -q "^API_SERVER_KEY=" "$env_file"; then
    _sed_inplace "$env_file" "s|^API_SERVER_KEY=.*|API_SERVER_KEY=|"
  fi
}
