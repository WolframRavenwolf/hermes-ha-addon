"""Launch an add-on dashboard in exactly the assigned HERMES_HOME."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def main() -> None:
    script = Path(__file__).absolute()
    installed = script.with_name("hermes-gateway-launcher.py")
    gateway_script = installed if installed.is_file() else script.with_name("gateway-launcher.py")
    spec = importlib.util.spec_from_file_location("hermes_addon_gateway_helpers", gateway_script)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load gateway launcher helpers from {gateway_script}")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)

    sys.argv[0] = str(script)
    if sys.argv[1:2] != ["dashboard"]:
        sys.argv.insert(1, "dashboard")
    if "--isolated" not in sys.argv[1:]:
        sys.argv.append("--isolated")
    helpers._activate_pm_dependencies()
    hermes_main = helpers._import_fixed_profile_main()
    hermes_main()


if __name__ == "__main__":
    main()
