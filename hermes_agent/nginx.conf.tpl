worker_processes 1;
pid /var/run/nginx.pid;
error_log stderr warn;

events {
    worker_connections 256;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_body_buffer_size 16m;
    client_max_body_size 0;

    log_format minimal '$remote_addr - $request_uri $status';
    access_log /dev/stdout minimal;

    upstream ttyd_terminal {
        server 127.0.0.1:%%TTYD_TERMINAL_PORT%%;
    }

    upstream ttyd_hermes {
        server 127.0.0.1:%%TTYD_HERMES_PORT%%;
    }

    upstream hermes_api {
        server 127.0.0.1:8642;
    }

    # ── Ingress (HA sidebar — landing page) ──────────────────────────
    server {
        listen %%INGRESS_PORT%%;
        server_name _;

        location = / {
            root /var/www;
            try_files /landing.html =404;
        }

        # Hermes Agent (login shell → exec hermes)
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

        # Terminal (non-login shell)
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

        # API
        location /v1/ {
            proxy_pass http://hermes_api;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        # CA certificate download
        location = /cert/ca.crt {
            alias %%CERTS_DIR%%/ca.crt;
            default_type application/x-x509-ca-cert;
            add_header Content-Disposition 'attachment; filename="hermes-agent-ca.crt"';
        }

        location = /health {
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }
    }

    # ── HTTP (direct LAN access) ─────────────────────────────────────
    server {
        listen %%HTTP_PORT%%;
        server_name _;

        %%AUTH_BASIC_ON%%

        location = / {
            root /var/www;
            try_files /landing.html =404;
        }

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

        location = /cert/ca.crt {
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
            root /var/www;
            try_files /landing.html =404;
        }

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

        location = /cert/ca.crt {
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
}
