"""Exercise the Dockerfile's actual Go RUN step with local HTTP and real curl/tar."""

import hashlib
import io
import os
import socket
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "hermes_agent/Dockerfile"
OFFICIAL_SHA256 = {
    "amd64": "031f088e5d955bab8657ede27ad4e3bc5b7c1ba281f05f245bcc304f327c987a",
    "arm64": "a290581cfe4fe28ddd737dde3095f3dbeb7f2e4065cab4eae44dfc53b760c2f7",
}


def go_run():
    text = DOCKERFILE.read_text()
    section = text.split("# ── Go 1.26 ─", 1)[1].split("\nENV PATH=", 1)[0]
    return section.split("\nRUN ", 1)[1].strip().replace("\\\n", " ")


class GoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "local"
        self.dest.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.requests = 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(_self, format, *args):
                pass

            def do_GET(handler):
                self.requests += 1
                payload = self.archive
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(payload)))
                handler.end_headers()
                if self.reset_all or (self.reset_first and self.requests == 1):
                    handler.wfile.write(payload[:8])
                    handler.wfile.flush()
                    handler.connection.shutdown(socket.SHUT_RDWR)
                else:
                    handler.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.worker.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.reset_all = False
        self.reset_first = False
        content = b"validated Go archive\n"
        tar_bytes = io.BytesIO()
        with tarfile.open(fileobj=tar_bytes, mode="w:gz") as archive:
            member = tarfile.TarInfo("go/marker")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        self.archive = tar_bytes.getvalue()
        self.marker = self.dest / "go/marker"

    def run_step(self, arch="arm64", matching_checksum=True):
        (self.bin / "dpkg").write_text("#!/bin/sh\n[ \"$1\" = '--print-architecture' ] || exit 1\nprintf '%s\\n' '" + arch + "'\n")
        (self.bin / "dpkg").chmod(0o755)
        script = go_run().replace(
            "https://go.dev/dl/go1.26.1.linux-${ARCH}.tar.gz",
            f"http://127.0.0.1:{self.server.server_port}/go1.26.1.linux-${{ARCH}}.tar.gz",
        ).replace("tar -C /usr/local", f"tar -C {self.dest}")
        if arch in OFFICIAL_SHA256:
            checksum = hashlib.sha256(self.archive).hexdigest() if matching_checksum else "0" * 64
            script = script.replace(OFFICIAL_SHA256[arch], checksum)
        env = os.environ | {
            "HOME": str(self.root), "TMPDIR": str(self.root),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
        }
        return subprocess.run(["/bin/bash", "-o", "pipefail", "-c", script],
                              env=env, capture_output=True, text=True, timeout=25)

    def test_official_checksums_for_both_supported_architectures(self):
        text = go_run()
        for arch, digest in OFFICIAL_SHA256.items():
            with self.subTest(arch=arch):
                self.assertRegex(text, rf"\b{arch}\)\s*SHA256={digest}\b")

    def test_connection_reset_retries_then_extracts_validated_archive(self):
        self.reset_first = True
        result = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(self.requests, 2)
        self.assertEqual(self.marker.read_bytes(), b"validated Go archive\n")

    def test_exhausted_resets_never_extract(self):
        self.reset_all = True
        result = self.run_step()
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(self.requests, 2)
        self.assertLessEqual(self.requests, 4)
        self.assertFalse((self.dest / "go").exists())

    def test_checksum_mismatch_never_extracts(self):
        result = self.run_step(matching_checksum=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests, 1)
        self.assertFalse((self.dest / "go").exists())

    def test_amd64_valid_archive_extracts(self):
        result = self.run_step(arch="amd64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marker.read_bytes(), b"validated Go archive\n")

    def test_unsupported_architecture_exits_before_download(self):
        result = self.run_step(arch="i386")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests, 0)
        self.assertFalse((self.dest / "go").exists())


if __name__ == "__main__":
    unittest.main()
