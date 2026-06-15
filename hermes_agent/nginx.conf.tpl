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

    # Dashboard tokens can exceed nginx's default 64-byte map bucket. This must
    # be set before any map block is parsed.
    map_hash_bucket_size 128;

    # Dashboard /api/ carries both normal HTTP requests and WebSocket upgrades
    # (/api/pty, /api/ws, /api/events). Preserve Upgrade semantics without
    # forcing Connection: upgrade onto ordinary REST calls.
    map $http_upgrade $connection_upgrade {
        default upgrade;
        '' close;
    }

%%UPSTREAMS%%
%%DASHBOARD_MAPS%%

    # ── Ingress (HA sidebar — landing page) ──────────────────────────
    server {
        listen %%INGRESS_PORT%%;
        server_name _;

        location = / {
            root /var/www;
            try_files /landing.html =404;
            add_header Cache-Control "no-cache";
        }

%%INGRESS_PROFILE_LOCATIONS%%

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
