"""Backup policy contracts for the Hermes Agent Home Assistant add-on."""

import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "hermes_agent"
CONFIG = ADDON / "config.yaml"
RUN_SCRIPT = ADDON / "run.sh"
CHANGELOG = ADDON / "CHANGELOG.md"
README = ROOT / "README.md"

BASH = "/bin/bash" if sys.platform == "darwin" and os.path.exists("/bin/bash") else "bash"

EXPECTED_BACKUP_EXCLUDE = [
    ".hermes/hermes-agent/venv",
    ".hermes/hermes-agent/node_modules",
    ".hermes/hermes-agent/web/node_modules",
    "lsp/node_modules",
    ".cache",
    ".npm",
]


def _manifest():
    return yaml.safe_load(CONFIG.read_text())


def _excluded_with_supervisor_pruning(full_path, patterns):
    """Model Supervisor's Path(full_path).match(pattern) directory pruning."""
    path = Path(full_path)
    for depth in range(1, len(path.parts) + 1):
        candidate = Path(*path.parts[:depth])
        if any(candidate.match(pattern) for pattern in patterns):
            return True
    return False


def _installation_functions():
    run = RUN_SCRIPT.read_text()
    start = run.index("compute_marker() {")
    end = run.index("\ninstall_hermes_core() {", start)
    return run[start:end]


class BackupManifestTests(unittest.TestCase):
    def test_manifest_has_exact_conservative_backup_exclude_allowlist(self):
        self.assertEqual(_manifest().get("backup_exclude"), EXPECTED_BACKUP_EXCLUDE)

    def test_manifest_preserves_hot_backup_mode(self):
        self.assertNotIn("backup", _manifest())

    def test_supervisor_matching_prunes_rebuildable_runtime_directories(self):
        patterns = _manifest().get("backup_exclude", ())
        excluded_paths = {
            "shared venv": "/config/.hermes/hermes-agent/venv",
            "shared venv descendant": "/config/.hermes/hermes-agent/venv/bin/python",
            "shared node modules": "/config/.hermes/hermes-agent/node_modules/agent-browser",
            "dashboard node modules": (
                "/config/.hermes/hermes-agent/web/node_modules/vite/bin/vite.js"
            ),
            "default profile LSP": "/config/.hermes/lsp/node_modules/typescript/lib/tsserver.js",
            "named profile LSP": (
                "/config/.hermes/profiles/amy/lsp/node_modules/typescript/lib/tsserver.js"
            ),
            "custom-base profile LSP": (
                "/config/custom/profiles/research/lsp/node_modules/typescript/lib/tsserver.js"
            ),
            "legacy-flat profile LSP": (
                "/config/amy/lsp/node_modules/typescript/lib/tsserver.js"
            ),
            "cache": "/config/.cache/uv/archive-v0/package",
            "npm cache": "/config/.npm/_cacache/content-v2/package",
        }
        for label, path in excluded_paths.items():
            with self.subTest(label=label, path=path):
                self.assertTrue(
                    _excluded_with_supervisor_pruning(path, patterns),
                    f"Supervisor would not prune {path}",
                )

    def test_supervisor_matching_keeps_durable_state_and_user_toolchains(self):
        patterns = _manifest().get("backup_exclude", ())
        retained_paths = {
            "source": "/config/.hermes/hermes-agent/hermes_cli/main.py",
            "source git": "/config/.hermes/hermes-agent/.git/objects/aa/object",
            "profile config": "/config/.hermes/config.yaml",
            "profile environment": "/config/.hermes/.env",
            "profile auth": "/config/.hermes/auth.json",
            "state database": "/config/.hermes/state.db",
            "sessions": "/config/.hermes/sessions/session.json",
            "memories": "/config/.hermes/memories/MEMORY.md",
            "skills": "/config/.hermes/skills/custom/SKILL.md",
            "plugins": "/config/.hermes/plugins/custom/plugin.py",
            "secrets": "/config/.hermes/secrets.yaml",
            "homebrew": "/config/.linuxbrew/Cellar/node/22/bin/node",
            "global npm": (
                "/config/.npm-global/lib/node_modules/typescript/lib/tsserver.js"
            ),
            "go": "/config/.go/pkg/mod/example.org/tool/source.go",
            "bun": "/config/.bun/install/cache/tool/package.json",
            "Camofox login state": (
                "/config/.camofox/profiles/user-hash/storage_state.json"
            ),
            "Chrome debug login state": (
                "/config/.hermes/chrome-debug/Default/Login Data"
            ),
            "certificates": "/config/.certs/server.key",
            "shell config": "/config/.bashrc",
            "similarly named cache": "/config/.cache-user/index",
            "npm configuration": "/config/.npmrc",
        }
        for label, path in retained_paths.items():
            with self.subTest(label=label, path=path):
                self.assertFalse(
                    _excluded_with_supervisor_pruning(path, patterns),
                    f"Supervisor would unexpectedly prune {path}",
                )


class ReconstructionContractTests(unittest.TestCase):
    def test_missing_excluded_shared_venv_requires_reinstallation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            source = home / ".hermes" / "hermes-agent"
            venv = source / "venv"
            marker = home / ".hermes_install"
            (source / ".git").mkdir(parents=True)
            (source / "mini-swe-agent").mkdir()
            (source / "mini-swe-agent" / "pyproject.toml").write_text(
                "[project]\nname = 'retained-source'\n"
            )
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "activate").write_text("")
            (venv / "bin" / "hermes").write_text("")

            script = textwrap.dedent(
                f"""
                set -euo pipefail
                GIT_URL=https://example.invalid/hermes-agent.git
                GIT_REF=retained-ref
                SRC_DIR={shlex.quote(str(source))}
                VENV_DIR={shlex.quote(str(venv))}
                MARKER_FILE={shlex.quote(str(marker))}

                {_installation_functions()}

                compute_marker() {{
                    printf 'retained-marker\\n'
                }}
                compute_marker > "$MARKER_FILE"
                if install_needed; then
                    printf 'unexpected-install-needed-before-removal\\n' >&2
                    exit 10
                fi
                printf 'UP_TO_DATE\\n'

                rm -rf "$VENV_DIR"
                if install_needed; then
                    printf 'INSTALL_NEEDED\\n'
                else
                    printf 'unexpected-up-to-date-after-removal\\n' >&2
                    exit 11
                fi
                """
            )
            result = subprocess.run(
                [BASH, "-c", script],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "UP_TO_DATE\nINSTALL_NEEDED\n")
            self.assertTrue(source.is_dir())
            self.assertTrue(marker.is_file())
            self.assertFalse(venv.exists())


class BackupDocumentationTests(unittest.TestCase):
    def test_readme_explains_backup_reconstruction_and_retention(self):
        readme = README.read_text()
        self.assertIn("### Home Assistant backups", readme)
        backup_section = readme.split("### Home Assistant backups", 1)[1]
        backup_section = backup_section.split("\n### ", 1)[0].lower()
        for phrase in (
            "regenerated",
            "slower",
            "network",
            "first lsp use",
            "hermes lsp install",
            "user-managed tools",
            "login state",
            "remain backed up",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, backup_section)
        self.assertNotIn(
            "excluded paths are regenerated during startup",
            backup_section,
        )

    def test_changelog_records_backup_reconstruction_and_retention(self):
        unreleased = CHANGELOG.read_text().split("## [Unreleased]", 1)[1]
        unreleased = unreleased.split("\n## [", 1)[0].lower()
        for phrase in (
            "regenerated",
            "slower",
            "network",
            "first lsp use",
            "user-managed tools",
            "login state",
            "remain backed up",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, unreleased)


if __name__ == "__main__":
    unittest.main()
