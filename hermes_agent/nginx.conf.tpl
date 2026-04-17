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

    upstream hermes_dashboard {
        server 127.0.0.1:%%DASHBOARD_PORT%%;
    }

    # ── Ingress (HA sidebar — landing page) ──────────────────────────
    server {
        listen %%INGRESS_PORT%%;
        server_name _;

        location = / {
            root /var/www;
            try_files /landing.html =404;
            add_header Cache-Control "no-cache";
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

        # DASHBOARD_START
        location = /dashboard { return 302 /dashboard/; }
        location /dashboard/ {
            proxy_pass http://hermes_dashboard/;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
        }
        # DASHBOARD_END

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

    %%INCLUDE_PORTS%%
}
