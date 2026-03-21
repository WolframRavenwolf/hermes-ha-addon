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

    log_format minimal '$remote_addr - $request_uri $status';
    access_log /dev/stdout minimal;

    server {
        listen %%NGINX_PORT%% default_server;
        server_name _;

        # Health check
        location = /health {
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }

        # Placeholder for future API proxy (e.g. Hermes Gateway API on port 8642)
        # location /v1/ {
        #     proxy_pass http://127.0.0.1:8642;
        #     proxy_http_version 1.1;
        #     proxy_set_header Host $host;
        #     proxy_set_header X-Real-IP $remote_addr;
        # }
    }
}
