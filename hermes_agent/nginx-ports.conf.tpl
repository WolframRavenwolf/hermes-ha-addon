    # ── HTTP (direct LAN access) ─────────────────────────────────────
    server {
        listen %%HTTP_PORT%%;
        server_name _;

        %%AUTH_BASIC_ON%%

        location = / {
            %%AUTH_BASIC_OFF%%
            root /var/www;
            try_files /landing.html =404;
            add_header Cache-Control "no-cache";
        }

        # TERMINAL_START
        location = /hermes { return 302 /hermes/; }
        location /hermes/ {
            proxy_pass http://ttyd_hermes;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        location = /terminal { return 302 /terminal/; }
        location /terminal/ {
            proxy_pass http://ttyd_terminal;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
        # TERMINAL_END

        # API_START
        location /v1/ {
            %%AUTH_BASIC_OFF%%
            proxy_pass http://hermes_api;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
        # API_END

        # DASHBOARD_START
        location = /dashboard { return 302 /dashboard/; }
        # Public health endpoint — landing-page status indicator calls this
        # unauthenticated. Mirrors Hermes' own _PUBLIC_API_PATHS whitelist.
        location = /dashboard/api/status {
            %%AUTH_BASIC_OFF%%
            proxy_pass http://hermes_dashboard/api/status;
            proxy_http_version 1.1;
            # Dashboard binds to 127.0.0.1 and validates Host for DNS-rebinding protection.
            # Preserve the external host separately, but send the upstream host it expects.
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header Authorization "Bearer %%DASHBOARD_TOKEN%%";
            proxy_buffering off;
        }
        # All other dashboard API calls: require client to echo the SPA's
        # injected Bearer token. Only users who loaded /dashboard/ (gated
        # by htpasswd) ever see the token, so this closes the drive-by API
        # access hole without adding a second auth layer in the browser.
        location /dashboard/api/ {
            %%AUTH_BASIC_OFF%%
            if ($http_authorization != "Bearer %%DASHBOARD_TOKEN%%") {
                return 401;
            }
            proxy_pass http://hermes_dashboard/api/;
            proxy_http_version 1.1;
            # Dashboard binds to 127.0.0.1 and validates Host for DNS-rebinding protection.
            # Preserve the external host separately, but send the upstream host it expects.
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
        location /dashboard/ {
            proxy_pass http://hermes_dashboard/;
            proxy_http_version 1.1;
            # Dashboard binds to 127.0.0.1 and validates Host for DNS-rebinding protection.
            # Preserve the external host separately, but send the upstream host it expects.
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
        # DASHBOARD_END

        location = /cert/ca.crt {
            %%AUTH_BASIC_OFF%%
            alias %%CERTS_DIR%%/ca.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="hermes-agent-ca.crt"';
        }

        location = /health {
            %%AUTH_BASIC_OFF%%
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }
    }

    # ── HTTPS (direct LAN access, TLS) ───────────────────────────────
    server {
        listen %%HTTPS_PORT%% ssl;
        server_name _;

        ssl_certificate %%CERTS_DIR%%/server.crt;
        ssl_certificate_key %%CERTS_DIR%%/server.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;

        %%AUTH_BASIC_ON%%

        location = / {
            %%AUTH_BASIC_OFF%%
            root /var/www;
            try_files /landing.html =404;
            add_header Cache-Control "no-cache";
        }

        # TERMINAL_START
        location = /hermes { return 302 /hermes/; }
        location /hermes/ {
            proxy_pass http://ttyd_hermes;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        location = /terminal { return 302 /terminal/; }
        location /terminal/ {
            proxy_pass http://ttyd_terminal;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
        # TERMINAL_END

        # API_START
        location /v1/ {
            %%AUTH_BASIC_OFF%%
            proxy_pass http://hermes_api;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }
        # API_END

        # DASHBOARD_START
        location = /dashboard { return 302 /dashboard/; }
        # Public health endpoint — landing-page status indicator calls this
        # unauthenticated. Mirrors Hermes' own _PUBLIC_API_PATHS whitelist.
        location = /dashboard/api/status {
            %%AUTH_BASIC_OFF%%
            proxy_pass http://hermes_dashboard/api/status;
            proxy_http_version 1.1;
            # Dashboard binds to 127.0.0.1 and validates Host for DNS-rebinding protection.
            # Preserve the external host separately, but send the upstream host it expects.
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Authorization "Bearer %%DASHBOARD_TOKEN%%";
            proxy_buffering off;
        }
        # All other dashboard API calls: require client to echo the SPA's
        # injected Bearer token. Only users who loaded /dashboard/ (gated
        # by htpasswd) ever see the token, so this closes the drive-by API
        # access hole without adding a second auth layer in the browser.
        location /dashboard/api/ {
            %%AUTH_BASIC_OFF%%
            if ($http_authorization != "Bearer %%DASHBOARD_TOKEN%%") {
                return 401;
            }
            proxy_pass http://hermes_dashboard/api/;
            proxy_http_version 1.1;
            # Dashboard binds to 127.0.0.1 and validates Host for DNS-rebinding protection.
            # Preserve the external host separately, but send the upstream host it expects.
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
        location /dashboard/ {
            proxy_pass http://hermes_dashboard/;
            proxy_http_version 1.1;
            # Dashboard binds to 127.0.0.1 and validates Host for DNS-rebinding protection.
            # Preserve the external host separately, but send the upstream host it expects.
            proxy_set_header Host 127.0.0.1;
            proxy_set_header X-Forwarded-Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
        # DASHBOARD_END

        location = /cert/ca.crt {
            %%AUTH_BASIC_OFF%%
            alias %%CERTS_DIR%%/ca.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="hermes-agent-ca.crt"';
        }

        location = /health {
            %%AUTH_BASIC_OFF%%
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }
    }
