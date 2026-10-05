"""Dashboard gateway controls must not launch a second service manager in the add-on."""

import importlib.util
from pathlib import Path
import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


LAUNCHER = Path(__file__).resolve().parents[1] / "hermes_agent" / "dashboard-launcher.py"


class DashboardGatewayLifecycleGuardTests(unittest.TestCase):
    def test_existing_security_checks_run_before_lifecycle_refusal(self) -> None:
        spec = importlib.util.spec_from_file_location("addon_dashboard_launcher", LAUNCHER)
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        app = FastAPI()
        dispatched = []

        @app.middleware("http")
        async def host_check(request, call_next):
            if request.headers.get("host") != "testserver":
                return JSONResponse(status_code=400, content={"detail": "Invalid Host"})
            return await call_next(request)

        @app.middleware("http")
        async def session_check(request, call_next):
            if request.headers.get("x-test-session") != "fixture-session":
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return await call_next(request)

        for verb in ("start", "stop", "restart"):
            async def native_action(verb=verb):
                dispatched.append(verb)
                return {"ok": True}
            app.add_api_route(f"/api/gateway/{verb}", native_action, methods=["POST"])

        launcher.install_gateway_lifecycle_guard(app)
        with TestClient(app) as client:
            for verb in ("start", "stop", "restart"):
                for suffix in ("", "?profile=coder"):
                    url = f"/api/gateway/{verb}{suffix}"
                    self.assertEqual(client.post(url).status_code, 401)
                    self.assertEqual(client.post(url, headers={
                        "host": "untrusted.example", "x-test-session": "fixture-session",
                    }).status_code, 400)
                    self.assertEqual(client.post(url, headers={
                        "x-test-session": "fixture-session",
                    }).status_code, 409)
            self.assertEqual(dispatched, [])

    def test_lifecycle_posts_refuse_before_upstream_actions_run(self) -> None:
        spec = importlib.util.spec_from_file_location("addon_dashboard_launcher", LAUNCHER)
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)

        app = FastAPI()
        dispatched = []

        for verb in ("start", "stop", "restart"):
            async def native_gateway_action(verb=verb):
                dispatched.append(verb)  # representative upstream CLI/s6 dispatch
                return {"ok": True}

            app.add_api_route(f"/api/gateway/{verb}", native_gateway_action, methods=["POST"])

        @app.get("/api/status")
        async def status():
            return {"gateway_running": True}

        @app.post("/api/config")
        async def save_config():
            return {"ok": True}

        launcher.install_gateway_lifecycle_guard(app)
        with TestClient(app) as client:
            for verb in ("start", "stop", "restart"):
                for suffix in ("", "?profile=coder"):
                    response = client.post(f"/api/gateway/{verb}{suffix}")
                    self.assertEqual(response.status_code, 409, (verb, response.text))
                    self.assertIn("Home Assistant", response.json()["detail"])
            self.assertEqual(dispatched, [])
            self.assertEqual(client.get("/api/status").json(), {"gateway_running": True})
            self.assertEqual(client.post("/api/config").json(), {"ok": True})
            self.assertEqual(client.get("/api/gateway/start").status_code, 405)
            self.assertEqual(dispatched, [])
