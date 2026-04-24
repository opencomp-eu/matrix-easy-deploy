#!/usr/bin/env python3

import argparse
import shlex
from pathlib import Path

import yaml


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    return _as_dict(data)


def resolve_runtime_state(project_root: Path) -> dict[str, str]:
    deploy = load_yaml(project_root / "deploy.yaml")
    modules_state = load_yaml(project_root / ".matrix-easy-deploy" / "modules.yaml")

    features = _as_dict(deploy.get("features"))
    element = _as_dict(features.get("element"))
    modules_cfg = _as_dict(deploy.get("modules"))

    def module_enabled(config_key: str) -> bool:
        cfg = _as_dict(modules_cfg.get(config_key))
        if "enabled" in cfg:
            return _as_bool(cfg.get("enabled"), False)

        state_cfg = _as_dict(modules_state.get(config_key))
        if "enabled" in state_cfg:
            return _as_bool(state_cfg.get("enabled"), False)

        return False

    install_element = True
    if "enabled" in element:
        install_element = _as_bool(element.get("enabled"), True)

    return {
        "INSTALL_ELEMENT": "true" if install_element else "false",
        "HOOKSHOT_ENABLED": "true" if module_enabled("hookshot") else "false",
        "WHATSAPP_BRIDGE_ENABLED": "true" if module_enabled("whatsapp_bridge") else "false",
        "SLACK_BRIDGE_ENABLED": "true" if module_enabled("slack_bridge") else "false",
    }


def emit_shell(state: dict[str, str]) -> str:
    lines = []
    for key in sorted(state.keys()):
        lines.append(f"{key}={shlex.quote(state[key])}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve runtime enablement flags from deploy state")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--emit-shell", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parent.parent
    state = resolve_runtime_state(project_root)

    if args.emit_shell:
        print(emit_shell(state))
    else:
        for key in sorted(state.keys()):
            print(f"{key}={state[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())