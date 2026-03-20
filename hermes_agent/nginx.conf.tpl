worker_processes 1;
pid /var/run/nginx.pid;
error_log stderr warn;

events {
    worker_connections 128;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;

    # Conditional access log based on log level
    map $request_uri $loggable {
        default 1;
        ~^/health 0;
    }

    log_format minimal '$remote_addr - $request_uri $status';

    # %%NGINX_LOG_LEVEL%%: off / minimal / full
    # Resolved by run.sh: access_log directive inserted below
    %%ACCESS_LOG_DIRECTIVE%%

    upstream ttyd {
        server 127.0.0.1:%%TERMINAL_PORT%%;
    }

    server {
        listen %%INGRESS_PORT%% default_server;
        server_name _;

        # Landing page
        location = %%INGRESS_ENTRY%% {
            root /var/www;
            try_files /landing.html =404;
        }

        # Terminal proxy
        location %%INGRESS_ENTRY%%terminal/ {
            proxy_pass http://ttyd/;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        # Health check
        location = /health {
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }
    }
}
