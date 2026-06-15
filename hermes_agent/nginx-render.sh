#!/bin/bash
# shellcheck shell=bash
# ─────────────────────────────────────────────────────────────────────
# Nginx config rendering library (sourced by run.sh and by tests).
#
# Consumes these arrays + scalars from the caller:
#   PROFILE_DIRS[]          PROFILE_NAMES[]          PROFILE_PATH_PREFIX[]
#   TTYD_HERMES_PORTS[]     TTYD_TERMINAL_PORTS[]    API_PORTS[]
#   DASHBOARD_PORTS[]       DASHBOARD_TOKENS[]
#   ENABLE_TERMINAL         ENABLE_API               ENABLE_DASHBOARD
#   DASHBOARD_AVAILABLE
#
# Nothing here mutates global state. Safe to source repeatedly.
# ─────────────────────────────────────────────────────────────────────

emit_upstreams() {
    local i
    for i in "${!PROFILE_DIRS[@]}"; do
        printf '    upstream ttyd_hermes_%d      { server 127.0.0.1:%d; }\n' "$i" "${TTYD_HERMES_PORTS[$i]}"
        printf '    upstream ttyd_terminal_%d    { server 127.0.0.1:%d; }\n' "$i" "${TTYD_TERMINAL_PORTS[$i]}"
        printf '    upstream hermes_api_%d       { server 127.0.0.1:%d; }\n' "$i" "${API_PORTS[$i]}"
        printf '    upstream hermes_dashboard_%d { server 127.0.0.1:%d; }\n' "$i" "${DASHBOARD_PORTS[$i]}"
    done
}

emit_dashboard_maps() {
    local i prefix
    for i in "${!PROFILE_DIRS[@]}"; do
        prefix="${PROFILE_PATH_PREFIX[$i]}"
        cat <<MAP
    map \$http_x_forwarded_prefix \$dashboard_proxy_prefix_${i} {
        default "\$http_x_forwarded_prefix${prefix}/dashboard";
        "" "${prefix}/dashboard";
    }
    map \$http_x_ingress_path \$dashboard_forwarded_prefix_${i} {
        default "\$http_x_ingress_path${prefix}/dashboard";
        "" \$dashboard_proxy_prefix_${i};
    }
MAP
    done
}

emit_token_maps() {
    local i token
    for i in "${!PROFILE_DIRS[@]}"; do
        token="${DASHBOARD_TOKENS[$i]}"
        cat <<TOK
    map "\$http_x_hermes_session_token|\$http_authorization" \$dashboard_token_ok_${i} {
        default 0;
        ~^${token}\| 1;
        ~^\|Bearer\ ${token}\$ 1;
    }
TOK
    done
}

# server_kind: ingress | http | https
emit_profile_locations() {
    local server_kind="$1"
    local proto_header=""
    local auth_off=""
    case "$server_kind" in
        https) proto_header='proxy_set_header X-Forwarded-Proto https;' ;;
    esac
    case "$server_kind" in
        http|https) auth_off='%%AUTH_BASIC_OFF%%' ;;
    esac

    local show_terminal="false"
    if [ "$server_kind" = "ingress" ] || [ "$ENABLE_TERMINAL" = "true" ]; then
        show_terminal="true"
    fi
    local show_api="false"
    if [ "$server_kind" = "ingress" ] || [ "$ENABLE_API" = "true" ]; then
        show_api="true"
    fi
    local show_dashboard="false"
    if [ "$DASHBOARD_AVAILABLE" = "true" ]; then
        if [ "$server_kind" = "ingress" ] || [ "$ENABLE_DASHBOARD" = "true" ]; then
            show_dashboard="true"
        fi
    fi

    local i prefix token token_guard
    for i in "${!PROFILE_DIRS[@]}"; do
        prefix="${PROFILE_PATH_PREFIX[$i]}"
        token="${DASHBOARD_TOKENS[$i]}"

        if [ "$show_terminal" = "true" ]; then
            cat <<HLOC

        # ── Profile ${i} (${PROFILE_NAMES[$i]}): hermes + terminal ──
        location = ${prefix}/hermes { return 302 ${prefix}/hermes/; }
        location ${prefix}/hermes/ {
            proxy_pass http://ttyd_hermes_${i};
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            ${proto_header}
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        location = ${prefix}/terminal { return 302 ${prefix}/terminal/; }
        location ${prefix}/terminal/ {
            proxy_pass http://ttyd_terminal_${i};
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            ${proto_header}
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
HLOC
        fi

        if [ "$show_api" = "true" ]; then
            cat <<ALOC

        # ── Profile ${i} (${PROFILE_NAMES[$i]}): API ──
        location ${prefix}/v1/ {
            ${auth_off}
            proxy_pass http://hermes_api_${i}/v1/;
            proxy_http_version 1.1;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            ${proto_header}
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
ALOC
        fi

        if [ "$show_dashboard" = "true" ]; then
            cat <<DHEAD

        # ── Profile ${i} (${PROFILE_NAMES[$i]}): dashboard ──
        location = ${prefix}/dashboard { return 302 ${prefix}/dashboard/; }
DHEAD
            if [ "$server_kind" != "ingress" ]; then
                cat <<DSTATUS
        # Public health endpoint — landing-page status indicator calls this
        # unauthenticated. Mirrors Hermes' own _PUBLIC_API_PATHS whitelist.
        location = ${prefix}/dashboard/api/status {
            ${auth_off}
            proxy_pass http://hermes_dashboard_${i}/api/status;
            proxy_http_version 1.1;
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host \$host;
            proxy_set_header X-Forwarded-Prefix \$dashboard_forwarded_prefix_${i};
            proxy_set_header X-Real-IP \$remote_addr;
            ${proto_header}
            proxy_set_header Authorization "Bearer ${token}";
            proxy_buffering off;
        }
DSTATUS
            fi
            token_guard=""
            if [ "$server_kind" != "ingress" ]; then
                token_guard="if (\$dashboard_token_ok_${i} = 0) { return 401; }"
            fi
            cat <<DAPI
        location ${prefix}/dashboard/api/ {
            ${auth_off}
            ${token_guard}
            proxy_pass http://hermes_dashboard_${i}/api/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection \$connection_upgrade;
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host \$host;
            proxy_set_header X-Forwarded-Prefix \$dashboard_forwarded_prefix_${i};
            proxy_set_header X-Real-IP \$remote_addr;
            ${proto_header}
            proxy_set_header Authorization "Bearer ${token}";
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }

        location ${prefix}/dashboard/ {
            proxy_pass http://hermes_dashboard_${i}/;
            proxy_http_version 1.1;
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host \$host;
            proxy_set_header X-Forwarded-Prefix \$dashboard_forwarded_prefix_${i};
            proxy_set_header X-Real-IP \$remote_addr;
            ${proto_header}
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
DAPI
        fi
    done
}

substitute_marker() {
    local file="$1" marker="$2"
    local insert_file tmp
    insert_file="$(mktemp)"
    cat > "$insert_file"
    tmp="$(mktemp)"
    awk -v marker="$marker" -v inserts="$insert_file" '
        index($0, marker) {
            while ((getline line < inserts) > 0) print line
            close(inserts)
            next
        }
        { print }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
    rm -f "$insert_file"
}
