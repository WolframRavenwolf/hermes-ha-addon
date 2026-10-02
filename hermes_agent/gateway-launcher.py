"""Launch Hermes Gateway with add-on-owned API settings held authoritative."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
from types import CodeType
from typing import Any

_HANDOFF = {
    "HERMES_ADDON_API_HOST": "API_SERVER_HOST",
    "HERMES_ADDON_API_PORT": "API_SERVER_PORT",
    "HERMES_ADDON_API_ENABLED": "API_SERVER_ENABLED",
    "HERMES_ADDON_API_KEY": "API_SERVER_KEY",
    "HERMES_ADDON_PROFILE_HOME": "HERMES_HOME",
    "HERMES_ADDON_MULTIPLEX_PROFILES": "GATEWAY_MULTIPLEX_PROFILES",
    "HERMES_ADDON_GATEWAY_NO_SUPERVISE": "HERMES_GATEWAY_NO_SUPERVISE",
    "HERMES_ADDON_SUPERVISED_CHILD": "HERMES_S6_SUPERVISED_CHILD",
}
_EXTERNAL_SUPERVISOR_FLAG = "--external-supervisor"
_GATEWAY_PARSER_MODULE = "hermes_cli.subcommands.gateway"


def _code_contains_external_supervisor(code: CodeType) -> bool:
    """Detect the exact CLI feature in parser bytecode, including nested code."""
    return any(
        constant == _EXTERNAL_SUPERVISOR_FLAG
        or (
            isinstance(constant, CodeType)
            and _code_contains_external_supervisor(constant)
        )
        for constant in code.co_consts
    )


def _supports_external_supervisor(
    import_module: Any = importlib.import_module,
) -> bool:
    """Feature-detect the installed editable Hermes CLI without version guessing."""
    try:
        parser_module = import_module(_GATEWAY_PARSER_MODULE)
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if _GATEWAY_PARSER_MODULE == missing or _GATEWAY_PARSER_MODULE.startswith(
            f"{missing}."
        ):
            return False
        raise

    return any(
        isinstance(code, CodeType) and _code_contains_external_supervisor(code)
        for code in (
            getattr(value, "__code__", None)
            for value in vars(parser_module).values()
        )
    )


def _remove_unsupported_external_supervisor(
    import_module: Any = importlib.import_module,
) -> None:
    """Keep old pinned Hermes revisions startable while preserving modern handback."""
    if _EXTERNAL_SUPERVISOR_FLAG not in sys.argv:
        return
    if _supports_external_supervisor(import_module):
        return
    sys.argv[:] = [
        argument
        for argument in sys.argv
        if argument != _EXTERNAL_SUPERVISOR_FLAG
    ]


def _capture_protected_values() -> dict[str, str]:
    """Capture and remove the add-on-only process handoff variables."""
    missing = [source for source in _HANDOFF if source not in os.environ]
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(f"missing add-on API handoff variables: {names}")
    return {
        target: os.environ.pop(source)
        for source, target in _HANDOFF.items()
    }


def _guard_env_loader(env_loader: Any, protected: dict[str, str]) -> None:
    """Reload normal Hermes sources while keeping protected API values fixed."""
    project_env = Path(env_loader.__file__).resolve().parents[1] / ".env"
    original_load = env_loader.load_hermes_dotenv
    try:
        original_load(project_env=project_env)
    finally:
        os.environ.update(protected)

    def load_protected_env(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_load(*args, **kwargs)
        finally:
            os.environ.update(protected)

    env_loader.load_hermes_dotenv = load_protected_env


def _guard_gateway_config(gateway_config: Any, protected: dict[str, str]) -> None:
    """Enforce add-on API settings on the final GatewayConfig object."""
    original_load = gateway_config.load_gateway_config
    enabled = protected["API_SERVER_ENABLED"].lower() == "true"
    host = protected["API_SERVER_HOST"]
    port = int(protected["API_SERVER_PORT"])
    key = protected["API_SERVER_KEY"]

    def load_protected_gateway_config(*args: Any, **kwargs: Any) -> Any:
        config = original_load(*args, **kwargs)
        config.multiplex_profiles = False
        api_server = gateway_config.Platform.API_SERVER
        if not enabled:
            config.platforms.pop(api_server, None)
            return config

        platform = config.platforms.get(api_server)
        if platform is None:
            platform = gateway_config.PlatformConfig()
            config.platforms[api_server] = platform
        platform.enabled = True
        platform.extra = dict(platform.extra or {})
        platform.extra.update({"host": host, "port": port, "key": key})
        return config

    gateway_config.load_gateway_config = load_protected_gateway_config


_STICKY_PROFILE_FILENAME = "active_profile"


def _sticky_active_profile_paths() -> frozenset[Path]:
    """Every path a sticky ``active_profile`` file can be read from.

    Resolved before any masking, with the unpatched helpers, so the probe paths
    match the ones Hermes builds while importing ``hermes_cli.main``: the
    platform default home (``~/.hermes``) and the resolved default Hermes root
    — ``HERMES_HOME`` itself in this add-on's container layout.
    """
    roots = {Path.home() / ".hermes"}
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        roots.add(Path(env_home).expanduser())
    try:
        import hermes_constants  # type: ignore[import-not-found]

        root_helper = getattr(hermes_constants, "get_default_hermes_root", None)
        if callable(root_helper):
            roots.add(Path(root_helper()))
    except Exception:  # no hermes_constants yet: the literal roots still cover the probe
        pass
    return frozenset(root / _STICKY_PROFILE_FILENAME for root in roots)


def _import_fixed_profile_main(import_module: Any = importlib.import_module) -> Any:
    """Import Hermes main without following a sticky interactive profile.

    The add-on owns HERMES_HOME, so a sticky ``active_profile`` (written by an
    interactive ``hermes profile use`` in a terminal) must not re-home a
    supervised gateway slot. Mask exactly the ``active_profile`` existence
    probes for the duration of the import.

    Do NOT instead replace ``hermes_constants.get_default_hermes_root`` with a
    ``/dev/null`` sentinel: modules that bind the helper at import time
    (``pm.environments`` does) keep the replacement forever, so
    ``dependency_home_root()`` resolved to ``/dev/null/installs/...`` and the
    source-update dependency completion failed on every boot with ``cannot read
    dependency environment: /dev/null/installs/<key>/facts.json`` — leaving any
    platform SDK installed only in the committed dependency environment
    unusable.
    """
    sticky_paths = _sticky_active_profile_paths()
    original_exists = Path.exists
    original_is_file = Path.is_file

    def masked(original: Any, path: Any, *args: Any, **kwargs: Any) -> bool:
        try:
            if Path(str(path)) in sticky_paths:
                return False
        except (TypeError, ValueError):
            pass
        return original(path, *args, **kwargs)

    def exists_without_sticky_profile(path: Path, *args: Any, **kwargs: Any) -> bool:
        return masked(original_exists, path, *args, **kwargs)

    def is_file_without_sticky_profile(path: Path, *args: Any, **kwargs: Any) -> bool:
        return masked(original_is_file, path, *args, **kwargs)

    setattr(Path, "exists", exists_without_sticky_profile)
    setattr(Path, "is_file", is_file_without_sticky_profile)
    try:
        main_module = import_module("hermes_cli.main")
    finally:
        setattr(Path, "exists", original_exists)
        setattr(Path, "is_file", original_is_file)
    return main_module.main


def main() -> None:
    """Start the regular Hermes CLI after installing the API env guard."""
    protected = _capture_protected_values()

    from hermes_cli import env_loader  # type: ignore[import-not-found]

    _guard_env_loader(env_loader, protected)

    hermes_main = _import_fixed_profile_main()
    _remove_unsupported_external_supervisor()

    from gateway import config as gateway_config  # type: ignore[import-not-found]

    _guard_gateway_config(gateway_config, protected)

    hermes_main()


if __name__ == "__main__":
    main()
