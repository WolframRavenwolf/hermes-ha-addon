"""Opt-in real nginx regression: HERMES_NGINX_TEST_IMAGE=nginx:stable-alpine."""

import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request

from test_multi_profile import _render_full_config


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get("HERMES_NGINX_TEST_IMAGE"), "opt-in Docker nginx test")
class DetailedHealthProxyRuntimeTests(unittest.TestCase):
    def test_auth_routing_and_legacy_liveness_on_all_listeners(self):
        image = os.environ["HERMES_NGINX_TEST_IMAGE"]
        requests = []
        servers = []
        threads = []
        container = None
        out = _render_full_config(
            [(".hermes", "hermes", ""), ("amy", "amy", "/profile/amy")],
            access_password="test-web-password",
        )
        try:
            for index in range(2):
                class Backend(http.server.BaseHTTPRequestHandler):
                    profile = index

                    def do_GET(self):
                        requests.append((self.profile, self.path, self.headers.get("Authorization")))
                        expected = f"Bearer test-api-key-{self.profile}"
                        code = 200 if self.headers.get("Authorization") == expected else 401
                        body = json.dumps({"status": "ok", "profile": self.profile}).encode()
                        self.send_response(code)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                    def log_message(self, *_args):
                        pass

                server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
                servers.append(server)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                threads.append(thread)

            ingress_port, http_port, https_port = (_free_port() for _ in range(3))
            certs = out / "certs"
            certs.mkdir()
            subprocess.run([
                "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
                "-keyout", str(certs / "server.key"), "-out", str(certs / "server.crt"),
                "-subj", "/CN=localhost",
            ], check=True, capture_output=True)
            for name in ("nginx.conf", "ports.conf"):
                path = out / name
                text = path.read_text().replace("/tmp/certs", str(certs))
                text = text.replace("listen 49169;", f"listen 127.0.0.1:{ingress_port};")
                text = text.replace("listen 8080;", f"listen 127.0.0.1:{http_port};")
                text = text.replace("listen 8443 ssl;", f"listen 127.0.0.1:{https_port} ssl;")
                text = text.replace("/etc/nginx/.htpasswd", str(out / "htpasswd"))
                for index, server in enumerate(servers):
                    text = text.replace(f"127.0.0.1:{8642 + index}", f"127.0.0.1:{server.server_port}")
                path.write_text(text)
            password = subprocess.run(
                ["openssl", "passwd", "-apr1", "test-web-password"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            (out / "htpasswd").write_text("hermes:" + password + "\n")
            common = ["docker", "run", "--rm", "--network", "host", "--mount",
                      f"type=bind,source={out},target={out},readonly", "--entrypoint", "nginx", image]
            syntax = subprocess.run(common + ["-t", "-c", str(out / "nginx.conf")],
                                    text=True, capture_output=True)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            container = subprocess.run(common[:2] + ["-d"] + common[2:] +
                                       ["-c", str(out / "nginx.conf"), "-g", "daemon off;"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            context = ssl._create_unverified_context()

            def get(base, path, authorization=None):
                headers = {"Authorization": authorization} if authorization else {}
                req = urllib.request.Request(base + path, headers=headers)
                try:
                    with urllib.request.urlopen(req, context=context, timeout=3) as response:
                        return response.status, response.read()
                except urllib.error.HTTPError as error:
                    with error:
                        return error.code, error.read()

            bases = (f"http://127.0.0.1:{ingress_port}", f"http://127.0.0.1:{http_port}",
                     f"https://127.0.0.1:{https_port}")
            for _ in range(50):
                try:
                    if get(bases[0], "/health")[0] == 200:
                        break
                except urllib.error.URLError:
                    time.sleep(0.05)
            else:
                self.fail("nginx failed to become ready")

            for base in bases:
                self.assertEqual(get(base, "/health"), (200, b"OK\n"))
                for index, prefix in enumerate(("", "/profile/amy")):
                    for authorization, expected in (
                        (None, 401), ("Bearer invalid", 401),
                        (f"Bearer test-api-key-{1-index}", 401),
                        (f"Bearer test-api-key-{index}", 200),
                    ):
                        with self.subTest(base=base, prefix=prefix, auth=authorization):
                            status, body = get(base, prefix + "/health/detailed", authorization)
                            self.assertEqual(status, expected)
                            self.assertEqual(json.loads(body)["profile"], index)
                            self.assertEqual(requests[-1], (index, "/health/detailed", authorization))
            for base in bases[1:]:
                self.assertEqual(get(base, "/hermes/")[0], 401)
        finally:
            if container:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=3)
            shutil.rmtree(out.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
