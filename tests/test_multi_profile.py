"""Multi-profile tests: resolution (A), env merge (B), rendered nginx config (D)."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE_INIT_LIB = ROOT / "hermes_agent" / "profile-init.sh"
NGINX_RENDER_LIB = ROOT / "hermes_agent" / "nginx-render.sh"
NGINX_TPL = ROOT / "hermes_agent" / "nginx.conf.tpl"
NGINX_PORTS_TPL = ROOT / "hermes_agent" / "nginx-ports.conf.tpl"

# On macOS, /bin/bash is GPLv2-era 3.2 — the version Home Assistant developers
# also hit locally. Force tests through it so bash-4-only features (mapfile,
# associative arrays, etc.) trip the suite immediately instead of in the field.
# Linux containers ship bash 4+ and resolve `bash` from PATH normally.
BASH = "/bin/bash" if sys.platform == "darwin" and os.path.exists("/bin/bash") else "bash"


# ── A. resolve_profiles ──────────────────────────────────────────────

def _run_resolve(options_json, *, legacy_hermes_home="", home_base=None, profiles_base=None, existing_dirs=None):
    """Run resolve_profiles, return either {'rows': [...]} or {'error': '...'}.

    profiles_base: None → inherit profile-init default (`.hermes/profiles`).
                   string → exported as PROFILES_BASE (empty string disables prefix).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if home_base is None:
            home_base = tmp_path / "home"
            home_base.mkdir()
        for existing in existing_dirs or []:
            (home_base / existing).mkdir(parents=True, exist_ok=True)
        options_path = tmp_path / "options.json"
        options_path.write_text(json.dumps(options_json))
        export_profiles_base = (
            f"export PROFILES_BASE={shlex.quote(profiles_base)}"
            if profiles_base is not None
            else ""
        )
        script = textwrap.dedent(f"""
            set -euo pipefail
            export HOME={shlex.quote(str(home_base))}
            export OPTIONS_FILE={shlex.quote(str(options_path))}
            export HERMES_HOME_DIR={shlex.quote(legacy_hermes_home)}
            {export_profiles_base}
            source {shlex.quote(str(PROFILE_INIT_LIB))}
            resolve_profiles
            for i in "${{!PROFILE_DIRS[@]}}"; do
                printf 'idx=%d|dir=%s|name=%s|home=%s|prefix=%s|marker=%s|api=%d|th=%d|tt=%d|dash=%d\\n' \\
                    "$i" "${{PROFILE_DIRS[$i]}}" "${{PROFILE_NAMES[$i]}}" \\
                    "${{PROFILE_HOMES[$i]}}" \\
                    "${{PROFILE_PATH_PREFIX[$i]}}" "${{PROFILE_MARKER[$i]}}" \\
                    "${{API_PORTS[$i]}}" "${{TTYD_HERMES_PORTS[$i]}}" \\
                    "${{TTYD_TERMINAL_PORTS[$i]}}" "${{DASHBOARD_PORTS[$i]}}"
            done
        """)
        result = subprocess.run(
            [BASH, "-c", script], text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip(), "returncode": result.returncode}
        rows = []
        for line in result.stdout.strip().splitlines():
            entry = {}
            for kv in line.split("|"):
                k, _, v = kv.partition("=")
                entry[k] = v
            rows.append(entry)
        return {"rows": rows, "home": str(home_base)}


class ProfileResolutionTests(unittest.TestCase):
    def test_empty_profiles_falls_back_to_legacy_hermes_home(self):
        res = _run_resolve({}, legacy_hermes_home=".hermes")
        rows = res["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dir"], ".hermes")
        self.assertEqual(rows[0]["name"], "hermes")
        self.assertEqual(rows[0]["prefix"], "")
        self.assertEqual(rows[0]["api"], "8642")

    def test_empty_options_defaults_to_dot_hermes(self):
        res = _run_resolve({}, legacy_hermes_home="")
        rows = res["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dir"], ".hermes")
        self.assertEqual(rows[0]["name"], "hermes")

    def test_three_profiles_primary_keeps_empty_prefix(self):
        res = _run_resolve(
            {"profiles": [".hermes", "amy", "bob"]}
        )
        rows = res["rows"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "hermes")
        self.assertEqual(rows[0]["prefix"], "")
        self.assertEqual(rows[1]["name"], "amy")
        self.assertEqual(rows[1]["prefix"], "/profile/amy")
        self.assertEqual(rows[2]["name"], "bob")
        self.assertEqual(rows[2]["prefix"], "/profile/bob")

    def test_port_allocation_increments_per_profile(self):
        res = _run_resolve(
            {"profiles": ["a", "b", "c"]}
        )
        rows = res["rows"]
        self.assertEqual([r["api"] for r in rows], ["8642", "8643", "8644"])
        self.assertEqual([r["th"] for r in rows], ["49269", "49270", "49271"])
        self.assertEqual([r["tt"] for r in rows], ["49369", "49370", "49371"])
        self.assertEqual([r["dash"] for r in rows], ["49469", "49470", "49471"])

    def test_name_collision_fails_with_clear_error(self):
        # ".hermes" sanitizes to "hermes"; collides with "hermes" entry.
        res = _run_resolve(
            {"profiles": [".hermes", "hermes"]}
        )
        self.assertIn("error", res)
        self.assertIn("collision", res["error"])
        self.assertIn("hermes", res["error"])

    def test_sanitization_collapses_special_chars(self):
        res = _run_resolve(
            {"profiles": ["primary", "my-profile.v2"]}
        )
        rows = res["rows"]
        self.assertEqual(rows[1]["name"], "my_profile_v2")
        self.assertEqual(rows[1]["prefix"], "/profile/my_profile_v2")

    def test_bare_name_resolves_under_default_profiles_base(self):
        """Bare names without `/` or leading `.` go under PROFILES_BASE (default `.hermes/profiles`)."""
        res = _run_resolve({"profiles": ["finance-ana"]})
        home = res["home"]
        row = res["rows"][0]
        self.assertEqual(row["dir"], "finance-ana")
        self.assertEqual(row["name"], "finance_ana")
        self.assertEqual(row["home"], f"{home}/.hermes/profiles/finance-ana")

    def test_dotted_entry_preserved_as_is(self):
        """Entries starting with `.` are not re-prefixed (`.hermes` must stay flat)."""
        res = _run_resolve({"profiles": [".hermes", ".hermes/profiles/manual"]})
        home = res["home"]
        rows = res["rows"]
        self.assertEqual(rows[0]["home"], f"{home}/.hermes")
        self.assertEqual(rows[1]["home"], f"{home}/.hermes/profiles/manual")

    def test_entry_with_slash_is_also_prefixed(self):
        """Non-dotted entries always go under profiles_base, even if they contain `/`."""
        res = _run_resolve({"profiles": ["primary", "team/finance"]})
        home = res["home"]
        rows = res["rows"]
        self.assertEqual(rows[0]["home"], f"{home}/.hermes/profiles/primary")
        self.assertEqual(rows[1]["home"], f"{home}/.hermes/profiles/team/finance")

    def test_profiles_base_empty_disables_prefix(self):
        """Empty PROFILES_BASE preserves the pre-feature layout (`/config/<name>`)."""
        res = _run_resolve({"profiles": ["amy"]}, profiles_base="")
        home = res["home"]
        self.assertEqual(res["rows"][0]["home"], f"{home}/amy")

    def test_existing_flat_profile_dir_is_preserved_on_upgrade(self):
        """Existing pre-profiles_base profile dirs must not silently move."""
        res = _run_resolve({"profiles": ["amy"]}, existing_dirs=["amy"])
        home = res["home"]
        self.assertEqual(res["rows"][0]["home"], f"{home}/amy")

    def test_profiles_base_custom_value(self):
        res = _run_resolve(
            {"profiles": ["alpha", "beta"]},
            profiles_base="agents",
        )
        home = res["home"]
        rows = res["rows"]
        self.assertEqual(rows[0]["home"], f"{home}/agents/alpha")
        self.assertEqual(rows[1]["home"], f"{home}/agents/beta")

    def test_legacy_hermes_home_ignores_profiles_base(self):
        """Single-profile fallback must keep its pre-feature layout."""
        res = _run_resolve({}, legacy_hermes_home="amy", profiles_base=".hermes/profiles")
        home = res["home"]
        self.assertEqual(res["rows"][0]["home"], f"{home}/amy")

    def test_marker_and_home_paths_are_per_profile(self):
        res = _run_resolve(
            {"profiles": [".hermes", "amy"]}
        )
        home = res["home"]
        rows = res["rows"]
        self.assertEqual(rows[0]["home"], f"{home}/.hermes")
        # `amy` is bare → goes under default profiles_base.
        self.assertEqual(rows[1]["home"], f"{home}/.hermes/profiles/amy")
        self.assertEqual(rows[0]["marker"], f"{home}/.hermes_install_hermes")
        self.assertEqual(rows[1]["marker"], f"{home}/.hermes_install_amy")


# ── B. apply_env_vars_for_profile ────────────────────────────────────

def _run_env_merge(
    options_json,
    profile_index,
    *,
    enable_api="false",
    access_password="",
    initial_env=None,
):
    """Pre-create per-profile .env files, run apply_env_vars_for_profile, return resulting .env as dict."""
    initial_env = initial_env or {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home"
        home.mkdir()
        profiles = options_json.get("profiles", [])
        dirs = list(profiles) if profiles else [".hermes"]
        for idx, d in enumerate(dirs):
            (home / d).mkdir(parents=True, exist_ok=True)
            (home / d / ".env").write_text(initial_env.get(idx, ""))

        options_path = tmp_path / "options.json"
        options_path.write_text(json.dumps(options_json))

        script = textwrap.dedent(f"""
            set -euo pipefail
            export HOME={shlex.quote(str(home))}
            export OPTIONS_FILE={shlex.quote(str(options_path))}
            export HERMES_HOME_DIR=""
            export PROFILES_BASE=""
            export ENABLE_API={shlex.quote(enable_api)}
            export ACCESS_PASSWORD={shlex.quote(access_password)}
            source {shlex.quote(str(PROFILE_INIT_LIB))}
            resolve_profiles
            apply_env_vars_for_profile {profile_index}
            cat "${{PROFILE_HOMES[{profile_index}]}}/.env"
        """)
        result = subprocess.run(
            [BASH, "-c", script], text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise AssertionError(f"bash failed: {result.stderr}")
        env = {}
        for line in result.stdout.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v
        return env, result.stderr


class EnvMergeTests(unittest.TestCase):
    def test_top_level_env_applied_to_every_profile(self):
        options = {
            "profiles": [".hermes", "amy"],
            "env_vars": [{"name": "FOO", "value": "bar"}],
        }
        env0, _ = _run_env_merge(options, 0)
        env1, _ = _run_env_merge(options, 1)
        self.assertEqual(env0.get("FOO"), "bar")
        self.assertEqual(env1.get("FOO"), "bar")

    def test_per_profile_override_layers_over_top_level(self):
        options = {
            "profiles": [".hermes", "amy"],
            "env_vars": [{"name": "FOO", "value": "shared"}],
            "profile_env_vars": [
                {"profile": "amy", "name": "FOO", "value": "amy-special"},
            ],
        }
        env0, _ = _run_env_merge(options, 0)
        env1, _ = _run_env_merge(options, 1)
        self.assertEqual(env0.get("FOO"), "shared")
        self.assertEqual(env1.get("FOO"), "amy-special")

    def test_reserved_vars_rejected_at_top_level(self):
        options = {
            "profiles": [".hermes"],
            "env_vars": [
                {"name": "HERMES_HOME", "value": "/evil"},
                {"name": "API_SERVER_PORT", "value": "9999"},
                {"name": "FOO", "value": "ok"},
            ],
        }
        env, stderr = _run_env_merge(options, 0)
        # Reserved values must not be the user-supplied ones.
        self.assertNotEqual(env.get("HERMES_HOME"), "/evil")
        self.assertEqual(env.get("API_SERVER_PORT"), "8642")  # add-on owned
        self.assertEqual(env.get("FOO"), "ok")

    def test_reserved_vars_rejected_in_per_profile_override(self):
        options = {
            "profiles": [".hermes"],
            "profile_env_vars": [
                {"profile": ".hermes", "name": "HERMES_HOME", "value": "/evil"},
                {"profile": ".hermes", "name": "API_SERVER_HOST", "value": "0.0.0.0"},
            ],
        }
        env, _ = _run_env_merge(options, 0)
        self.assertNotEqual(env.get("HERMES_HOME"), "/evil")
        self.assertEqual(env.get("API_SERVER_HOST"), "127.0.0.1")

    def test_api_server_port_assigned_per_profile(self):
        options = {
            "profiles": ["p0", "p1", "p2"],
        }
        env0, _ = _run_env_merge(options, 0)
        env1, _ = _run_env_merge(options, 1)
        env2, _ = _run_env_merge(options, 2)
        self.assertEqual(env0.get("API_SERVER_PORT"), "8642")
        self.assertEqual(env1.get("API_SERVER_PORT"), "8643")
        self.assertEqual(env2.get("API_SERVER_PORT"), "8644")
        for env in (env0, env1, env2):
            self.assertEqual(env.get("API_SERVER_HOST"), "127.0.0.1")

    def test_api_server_enabled_reflects_flag(self):
        options = {"profiles": [".hermes"]}
        env_off, _ = _run_env_merge(options, 0, enable_api="false")
        env_on, _ = _run_env_merge(options, 0, enable_api="true")
        self.assertEqual(env_off.get("API_SERVER_ENABLED"), "false")
        self.assertEqual(env_on.get("API_SERVER_ENABLED"), "true")

    def test_api_server_key_set_with_password(self):
        options = {"profiles": [".hermes"]}
        env_with, _ = _run_env_merge(options, 0, access_password="secret123")
        self.assertEqual(env_with.get("API_SERVER_KEY"), "secret123")

    def test_env_value_with_sed_special_chars_survives_upsert(self):
        """Values containing &, |, and backslash must round-trip through upsert_env_var.

        Without escaping, `&` is expanded to the matched pattern (corrupting the
        line) and `|` aborts sed because it's the substitution delimiter.
        """
        # Both append (no existing key) and replace (key already in .env) paths
        # share the same escaping requirement.
        cases = [
            ("PIPE_KEY",   "value|with|pipes"),
            ("AMP_KEY",    "a&b&c"),
            ("BSLASH_KEY", r"raw\path\value"),
            ("MIXED",      r"weird & | \ stuff"),
        ]
        # Append path: fresh .env, value first written via `>> env_file`.
        for key, value in cases:
            options = {
                "profiles": [".hermes"],
                "env_vars": [{"name": key, "value": value}],
            }
            env, _ = _run_env_merge(options, 0)
            self.assertEqual(env.get(key), value, f"append: {key}")

        # Replace path: pre-seed the .env so upsert_env_var hits the sed branch.
        for key, value in cases:
            options = {
                "profiles": [".hermes"],
                "env_vars": [{"name": key, "value": value}],
            }
            env, _ = _run_env_merge(
                options,
                0,
                initial_env={0: f"{key}=placeholder\n"},
            )
            self.assertEqual(env.get(key), value, f"replace: {key}")

    def test_api_server_key_blanked_when_password_removed(self):
        options = {"profiles": [".hermes"]}
        # Simulate a previous run that wrote an API_SERVER_KEY value; now password is empty.
        env, _ = _run_env_merge(
            options,
            0,
            access_password="",
            initial_env={0: "API_SERVER_KEY=oldvalue\n"},
        )
        self.assertEqual(env.get("API_SERVER_KEY"), "")


# ── D. Rendered nginx config invariants ──────────────────────────────

def _render_full_config(
    profiles,
    *,
    enable_terminal="true",
    enable_api="true",
    enable_dashboard="true",
    dashboard_available="true",
    access_password="",
):
    """Render full nginx.conf + ports.conf into a tmp dir and return the dir Path.

    Caller is responsible for cleanup (use a `with` block on the return value)."""
    tmp = Path(tempfile.mkdtemp())
    # Stage templates where run.sh expects them.
    (tmp / "etc-nginx").mkdir()
    shutil.copy(NGINX_TPL, tmp / "etc-nginx" / "nginx.conf.tpl")
    shutil.copy(NGINX_PORTS_TPL, tmp / "etc-nginx" / "nginx-ports.conf.tpl")

    n = len(profiles)
    dirs = " ".join(shlex.quote(d) for d, _, _ in profiles)
    names = " ".join(shlex.quote(name) for _, name, _ in profiles)
    prefixes = " ".join(shlex.quote(prefix) for _, _, prefix in profiles)
    ports_api = " ".join(str(8642 + i) for i in range(n))
    ports_th = " ".join(str(49269 + i) for i in range(n))
    ports_tt = " ".join(str(49369 + i) for i in range(n))
    ports_dash = " ".join(str(49469 + i) for i in range(n))
    tokens = " ".join(f"TOK{i}" for i in range(n))

    auth_basic_on = (
        'auth_basic "Hermes Agent"; auth_basic_user_file /etc/nginx/.htpasswd;'
        if access_password
        else "# no authentication"
    )
    auth_basic_off = "auth_basic off;" if access_password else ""

    # Bash emits per-profile fragments via substitute_marker (which uses awk, portable).
    # Scalar `%%...%%` substitutions happen in Python afterwards, sidestepping BSD/GNU sed quirks.
    script = textwrap.dedent(f"""
        set -euo pipefail
        source {shlex.quote(str(NGINX_RENDER_LIB))}
        PROFILE_DIRS=({dirs})
        PROFILE_NAMES=({names})
        PROFILE_PATH_PREFIX=({prefixes})
        API_PORTS=({ports_api})
        TTYD_HERMES_PORTS=({ports_th})
        TTYD_TERMINAL_PORTS=({ports_tt})
        DASHBOARD_PORTS=({ports_dash})
        DASHBOARD_TOKENS=({tokens})
        DASHBOARD_AVAILABLE={shlex.quote(dashboard_available)}
        ENABLE_TERMINAL={shlex.quote(enable_terminal)}
        ENABLE_API={shlex.quote(enable_api)}
        ENABLE_DASHBOARD={shlex.quote(enable_dashboard)}

        ETC={shlex.quote(str(tmp / "etc-nginx"))}
        cp "$ETC/nginx-ports.conf.tpl" "$ETC/ports.conf"
        emit_token_maps | substitute_marker "$ETC/ports.conf" '%%DASHBOARD_TOKEN_MAPS%%'
        emit_profile_locations http | substitute_marker "$ETC/ports.conf" '%%HTTP_PROFILE_LOCATIONS%%'
        emit_profile_locations https | substitute_marker "$ETC/ports.conf" '%%HTTPS_PROFILE_LOCATIONS%%'

        cp "$ETC/nginx.conf.tpl" "$ETC/nginx.conf"
        emit_upstreams | substitute_marker "$ETC/nginx.conf" '%%UPSTREAMS%%'
        emit_dashboard_maps | substitute_marker "$ETC/nginx.conf" '%%DASHBOARD_MAPS%%'
        emit_profile_locations ingress | substitute_marker "$ETC/nginx.conf" '%%INGRESS_PROFILE_LOCATIONS%%'
    """)
    result = subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise AssertionError(f"render failed: {result.stderr}\n{result.stdout}")

    etc = tmp / "etc-nginx"
    scalar_subs = {
        "%%HTTP_PORT%%": "8080",
        "%%HTTPS_PORT%%": "8443",
        "%%INGRESS_PORT%%": "49169",
        "%%CERTS_DIR%%": "/tmp/certs",
        "%%HERMES_VERSION%%": "test",
        "%%AUTH_BASIC_ON%%": auth_basic_on,
        "%%AUTH_BASIC_OFF%%": auth_basic_off,
        "%%INCLUDE_PORTS%%": f"include {etc / 'ports.conf'};",
    }
    for fname in ("nginx.conf", "ports.conf"):
        p = etc / fname
        text = p.read_text()
        for marker, value in scalar_subs.items():
            text = text.replace(marker, value)
        p.write_text(text)

    # Remove stale template files so callers don't accidentally lint them.
    (etc / "nginx.conf.tpl").unlink()
    (etc / "nginx-ports.conf.tpl").unlink()
    return etc


class RenderedConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmpdirs = []

    def tearDown(self):
        for d in self._tmpdirs:
            shutil.rmtree(d.parent, ignore_errors=True)

    def _render(self, **kw):
        out = _render_full_config(**kw)
        self._tmpdirs.append(out)
        return out

    def _two_profiles(self):
        return [(".hermes", "hermes", ""), ("amy", "amy", "/profile/amy")]

    def test_no_unsubstituted_markers_in_rendered_output(self):
        out = self._render(profiles=self._two_profiles())
        for name in ("nginx.conf", "ports.conf"):
            text = (out / name).read_text()
            self.assertNotIn("%%", text, f"unsubstituted marker in {name}: {text}")

    def test_brace_balance_in_rendered_output(self):
        out = self._render(profiles=self._two_profiles())
        for name in ("nginx.conf", "ports.conf"):
            text = (out / name).read_text()
            self.assertEqual(
                text.count("{"),
                text.count("}"),
                f"unbalanced braces in {name}",
            )

    def test_server_block_count(self):
        out = self._render(profiles=self._two_profiles())
        nginx_conf = (out / "nginx.conf").read_text()
        ports_conf = (out / "ports.conf").read_text()
        # nginx.conf: 1 server (ingress)
        self.assertEqual(nginx_conf.count("\n    server {"), 1)
        # ports.conf: 2 servers (HTTP + HTTPS)
        self.assertEqual(ports_conf.count("\n    server {"), 2)

    def test_upstream_count_matches_profile_count(self):
        out = self._render(profiles=self._two_profiles())
        nginx_conf = (out / "nginx.conf").read_text()
        # 4 upstreams per profile × 2 profiles = 8
        self.assertEqual(nginx_conf.count("\n    upstream "), 8)

    def test_each_non_primary_has_its_own_forwarded_prefix_map(self):
        out = self._render(profiles=self._two_profiles())
        nginx_conf = (out / "nginx.conf").read_text()
        # Primary (i=0) and non-primary (i=1) both get their own map pair.
        self.assertIn("$dashboard_forwarded_prefix_0", nginx_conf)
        self.assertIn("$dashboard_forwarded_prefix_1", nginx_conf)

    def test_single_profile_emits_no_profile_prefix_locations(self):
        out = self._render(profiles=[(".hermes", "hermes", "")])
        for name in ("nginx.conf", "ports.conf"):
            text = (out / name).read_text()
            self.assertNotIn("/profile/", text, f"unexpected /profile/ in {name}")

    def test_disabled_features_strip_direct_port_locations(self):
        out = self._render(
            profiles=self._two_profiles(),
            enable_terminal="false",
            enable_api="false",
            enable_dashboard="false",
        )
        ports_conf = (out / "ports.conf").read_text()
        # Direct ports must not expose service routes when their flags are off.
        self.assertNotIn("location /hermes/", ports_conf)
        self.assertNotIn("location /v1/", ports_conf)
        self.assertNotIn("location /dashboard/", ports_conf)
        # Ingress always carries them, regardless of direct-port flags.
        nginx_conf = (out / "nginx.conf").read_text()
        self.assertIn("location /hermes/", nginx_conf)
        self.assertIn("location /v1/", nginx_conf)
        self.assertIn("location /dashboard/", nginx_conf)

    def test_dashboard_unavailable_strips_dashboard_everywhere(self):
        out = self._render(
            profiles=self._two_profiles(),
            dashboard_available="false",
        )
        for name in ("nginx.conf", "ports.conf"):
            text = (out / name).read_text()
            self.assertNotIn("location /dashboard/", text)
            self.assertNotIn("location /profile/amy/dashboard/", text)

    def test_token_guard_only_in_direct_ports(self):
        out = self._render(profiles=self._two_profiles())
        nginx_conf = (out / "nginx.conf").read_text()
        ports_conf = (out / "ports.conf").read_text()
        # Ingress trusts HA's auth; direct ports require the SPA token.
        self.assertNotIn("dashboard_token_ok_", nginx_conf)
        self.assertIn("if ($dashboard_token_ok_0 = 0)", ports_conf)
        self.assertIn("if ($dashboard_token_ok_1 = 0)", ports_conf)

    def test_nginx_minus_t_accepts_rendered_config(self):
        if shutil.which("nginx") is None:
            self.skipTest("nginx binary not in PATH")
        out = self._render(profiles=self._two_profiles())

        # Local nginx packages (brew, distro) keep mime.types at varying absolute
        # paths. The container's `/etc/nginx/mime.types` doesn't exist on the host.
        # Stub the include and rewrite paths that need write access (pid, errlog).
        conf = out / "nginx.conf"
        text = conf.read_text()
        text = text.replace(
            "include /etc/nginx/mime.types;",
            "types { text/html html; text/plain txt; }",
        )
        text = text.replace("pid /var/run/nginx.pid;", f"pid {out}/nginx.pid;")
        conf.write_text(text)

        # Cert paths nginx references must exist AND parse as real PEM (TLS server block).
        certs = out / "certs"
        certs.mkdir(exist_ok=True)
        if shutil.which("openssl") is None:
            self.skipTest("openssl not available to generate test certs")
        subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                "-days", "1",
                "-keyout", str(certs / "server.key"),
                "-out", str(certs / "server.crt"),
                "-subj", "/CN=test",
            ],
            check=True, capture_output=True,
        )
        (certs / "ca.crt").write_text((certs / "server.crt").read_text())
        text = conf.read_text().replace("/tmp/certs", str(certs))
        conf.write_text(text)
        ports = out / "ports.conf"
        ports.write_text(ports.read_text().replace("/tmp/certs", str(certs)))

        result = subprocess.run(
            ["nginx", "-t", "-c", str(conf), "-p", str(out)],
            text=True,
            capture_output=True,
            check=False,
        )
        combined = result.stdout + result.stderr
        if "permission denied" in combined.lower() or "operation not permitted" in combined.lower():
            self.skipTest(f"nginx -t lacks permissions: {combined}")
        self.assertEqual(
            result.returncode, 0,
            f"nginx -t rejected rendered config:\n{combined}"
        )


if __name__ == "__main__":
    unittest.main()
