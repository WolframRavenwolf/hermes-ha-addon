"""Focused regressions for dashboard toolchain and token readiness (#61/#62)."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
from typing import Optional
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "hermes_agent" / "run.sh"
NGINX_RENDER = ROOT / "hermes_agent" / "nginx-render.sh"


def shell_function(name: str) -> str:
    source = RUN_SH.read_text()
    marker = f"{name}() {{\n"
    start = source.find(marker)
    if start < 0:
        return ""
    end = source.find("\n}\n", start)
    if end < 0:
        raise AssertionError(f"unterminated production function: {name}")
    return source[start:end + 3]


def executable(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o755)
    return path


class DashboardBuildRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ha-dashboard-build-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.venv_bin = self.root / "venv" / "bin"
        self.venv_bin.mkdir(parents=True)
        self.node_log = self.root / "node.log"
        executable(self.bin / "npm", """#!/bin/sh
printf 'npm %s\\n' "$*" >> "$NODE_LOG"
exit 0
""")

    def run_build(self, *, resolver: str, node: Path, native_builder: bool) -> subprocess.CompletedProcess:
        (self.source / "web").mkdir(parents=True, exist_ok=True)
        (self.source / "web/package.json").write_text("{}\n")
        if native_builder:
            (self.source / "scripts/build").mkdir(parents=True)
            (self.source / "scripts/build/web.mjs").write_text("// test fixture\n")
        executable(self.venv_bin / "python", resolver)
        script = "\n".join((
            "set -uo pipefail",
            f"SRC_DIR={self.source}",
            f"VENV_DIR={self.venv_bin.parent}",
            shell_function("resolve_dashboard_build_path"),
            shell_function("build_dashboard_frontend"),
            "set +e",
            "build_dashboard_frontend",
            "status=$?",
            "printf 'BUILD_STATUS=%s\\n' \"$status\"",
            "exit \"$status\"",
        ))
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "NODE_LOG": str(self.node_log),
            "NODE_RESULT": str(node),
            "BUILD_STATUS_LOG": str(self.root / "build-status.log"),
            "TMPDIR": str(self.root),
            "HOME": str(self.root),
        })
        return subprocess.run(
            ["/bin/bash", "-c", script], cwd=self.root, env=env,
            text=True, capture_output=True, timeout=20,
        )

    def test_managed_node_resolver_runs_the_native_upstream_builder(self):
        managed_bin = self.root / "managed-bin"
        managed_bin.mkdir()
        npm_bin = self.root / "managed-npm-bin"
        npm_bin.mkdir()
        managed_node = executable(managed_bin / "node", """#!/bin/sh
printf '%s\\n' "$*" >> "$NODE_LOG"
[ "$1" = "scripts/build/web.mjs" ] || exit 98
printf 'native build complete\\n'
""")
        executable(managed_bin / "npm", "#!/bin/sh\nexit 97\n")
        executable(npm_bin / "npm", """#!/bin/sh
printf 'npm %s via %s\\n' "$*" "$(command -v node)" >> "$NODE_LOG"
exit 0
""")
        executable(self.bin / "node", "#!/bin/sh\nprintf 'wrong PATH node used\\n' >&2\nexit 99\n")
        resolver = """#!/bin/sh
case "$*" in
  *'ensure("npm")'*) printf '%s:%s:%s\\n' "${NPM_BIN:-$(dirname "$NODE_RESULT")}" "$(dirname "$NODE_RESULT")" "$PATH" ;;
  *) exit 1 ;;
esac
"""

        with mock.patch.dict(os.environ, {"NPM_BIN": str(npm_bin)}):
            result = self.run_build(resolver=resolver, node=managed_node, native_builder=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.node_log.read_text().splitlines(),
            [f"npm install --silent via {managed_node}", "scripts/build/web.mjs"],
        )
        self.assertIn("native build complete", result.stdout)

    def test_older_checkout_uses_path_node_and_existing_vite_build(self):
        node = executable(self.bin / "node", "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$NODE_LOG\"\nexit 0\n")
        executable(self.bin / "npm", "#!/bin/sh\nprintf 'npm %s\\n' \"$*\" >> \"$NODE_LOG\"\nexit 0\n")
        executable(self.bin / "npx", "#!/bin/sh\nprintf 'npx %s\\n' \"$*\" >> \"$NODE_LOG\"\nexit 0\n")
        resolver = "#!/bin/sh\nexit 0\n"

        result = self.run_build(resolver=resolver, node=node, native_builder=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.node_log.read_text().splitlines()
        self.assertEqual(log[0], "npm install --silent")
        self.assertEqual(log[1], "npx vite build --outDir ../hermes_cli/web_dist --emptyOutDir")

    def test_build_failure_returns_failure_and_keeps_bounded_diagnostics(self):
        failed_bin = self.root / "failed-bin"
        failed_bin.mkdir()
        failed_node = executable(failed_bin / "node", """#!/bin/sh
for n in $(seq 1 35); do printf 'build diagnostic %s\\n' "$n"; done
exit 17
""")
        resolver = """#!/bin/sh
case "$*" in
  *'ensure("npm")'*) printf '%s:%s:%s\\n' "${NPM_BIN:-$(dirname "$NODE_RESULT")}" "$(dirname "$NODE_RESULT")" "$PATH" ;;
  *) exit 1 ;;
esac
"""
        executable(self.bin / "node", "#!/bin/sh\nexit 98\n")

        result = self.run_build(resolver=resolver, node=failed_node, native_builder=True)

        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertIn("build diagnostic 35", result.stderr)
        self.assertNotIn("build diagnostic 1\n", result.stderr)
        self.assertLessEqual(len(result.stderr.splitlines()), 24)


class DashboardTokenReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ha-dashboard-token-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.count = self.root / "curl-count"
        self.curl_args = self.root / "curl-args"

    def run_token_fetch(self, success_at: Optional[int]) -> subprocess.CompletedProcess:
        executable(self.bin / "curl", textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import os
            from pathlib import Path
            count_file = Path(os.environ["CURL_COUNT"])
            count = int(count_file.read_text()) + 1 if count_file.exists() else 1
            count_file.write_text(str(count))
            with open(os.environ["CURL_ARGS"], "a", encoding="utf-8") as stream:
                stream.write(" ".join(__import__("sys").argv[1:]) + "\\n")
            if {success_at!r} is not None and count >= {success_at!r}:
                print('<script>window.__HERMES_SESSION_TOKEN__="dashboard-secret-token"</script>')
            """))
        executable(self.bin / "grep", textwrap.dedent("""\
            #!/usr/bin/env python3
            import re
            import sys
            text = sys.stdin.read()
            match = re.search(r'__HERMES_SESSION_TOKEN__="([^"]+)', text)
            if match:
                print(match.group(1))
                raise SystemExit(0)
            raise SystemExit(1)
            """))
        executable(self.bin / "sleep", "#!/bin/sh\nexit 0\n")
        function = shell_function("inject_dashboard_token_for_profile")
        script = "\n".join((
            "set -uo pipefail",
            'DASHBOARD_AVAILABLE="true"',
            'PROFILE_NAMES=("primary")',
            'DASHBOARD_PORTS=(49469)',
            "DASHBOARD_TOKENS=()",
            function,
            "set +e",
            "inject_dashboard_token_for_profile 0",
            "status=$?",
            "printf 'FETCH_STATUS=%s\\n' \"$status\"",
            'printf "FETCH_TOKEN=%s\\n" "${DASHBOARD_TOKENS[0]-}"',
            "exit 0",
        ))
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "CURL_COUNT": str(self.count),
            "CURL_ARGS": str(self.curl_args),
            "TMPDIR": str(self.root),
            "HOME": str(self.root),
        })
        return subprocess.run(
            ["/bin/bash", "-c", script], cwd=self.root, env=env,
            text=True, capture_output=True, timeout=20,
        )

    def test_token_ready_after_the_old_15_attempt_window(self):
        result = self.run_token_fetch(success_at=16)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FETCH_STATUS=0", result.stdout)
        self.assertIn("FETCH_TOKEN=dashboard-secret-token", result.stdout)
        self.assertNotIn("UNAVAILABLE", result.stdout)
        self.assertEqual(self.count.read_text(), "16")

    def test_permanent_token_failure_is_reported_without_a_placeholder(self):
        result = self.run_token_fetch(success_at=None)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FETCH_STATUS=1", result.stdout)
        self.assertIn("FETCH_TOKEN=", result.stdout)
        self.assertNotIn("UNAVAILABLE", result.stdout)
        self.assertNotIn("token obtained", result.stdout.lower())
        self.assertGreater(int(self.count.read_text()), 15)

    def test_successful_token_fetch_has_curl_connection_and_total_timeouts(self):
        result = self.run_token_fetch(success_at=1)

        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.curl_args.read_text()
        self.assertIn("--connect-timeout", args)
        self.assertIn("--max-time", args)
        self.assertIn("dashboard-secret-token", result.stdout)

    def test_unavailable_dashboard_does_not_emit_direct_token_maps(self):
        script = "\n".join((
            "set -euo pipefail",
            f"source {NGINX_RENDER}",
            'DASHBOARD_AVAILABLE="false"',
            'PROFILE_DIRS=(".hermes")',
            'DASHBOARD_TOKENS=("PENDING_TOKEN_0")',
            "emit_token_maps",
        ))
        result = subprocess.run(
            ["/bin/bash", "-c", script], cwd=self.root,
            text=True, capture_output=True, timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
