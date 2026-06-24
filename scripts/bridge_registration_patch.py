#!/usr/bin/env python3

import argparse
import secrets
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


def patch_registration(config_path: Path, registration_path: Path) -> bool:
    config = load_yaml(config_path)
    registration = load_yaml(registration_path)
    changed = False

    homeserver = config.get("homeserver", {}) if isinstance(config.get("homeserver", {}), dict) else {}
    appservice = config.get("appservice", {}) if isinstance(config.get("appservice", {}), dict) else {}
    encryption = config.get("encryption", {}) if isinstance(config.get("encryption", {}), dict) else {}

    as_address = str(appservice.get("address", "") or "").strip()
    ephemeral_events = bool(appservice.get("ephemeral_events", False))
    encryption_enabled = bool(encryption.get("allow", False))
    bot_username = str((appservice.get("bot", {}) or {}).get("username", "whatsappbot") or "whatsappbot")

    if as_address and registration.get("url") != as_address:
        registration["url"] = as_address
        changed = True

    if ephemeral_events:
        if not registration.get("receive_ephemeral"):
            registration["receive_ephemeral"] = True
            changed = True
        if registration.pop("de.sorunome.msc2409.push_ephemeral", None) is not None:
            changed = True
        if registration.pop("push_ephemeral", None) is not None:
            changed = True

    if ephemeral_events or encryption_enabled:
        if not registration.get("org.matrix.msc3202"):
            registration["org.matrix.msc3202"] = True
            changed = True

    sender_localpart = str(registration.get("sender_localpart", "") or "")
    if not sender_localpart or sender_localpart == bot_username:
        registration["sender_localpart"] = secrets.token_hex(8)
        changed = True

    if changed:
        write_yaml(registration_path, registration)
    return changed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch mautrix bridge registration.yaml for Synapse compatibility")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--registration-path", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    changed = patch_registration(Path(args.config_path), Path(args.registration_path))
    if changed:
        print("registration.yaml patched for Synapse compatibility.")
    else:
        print("registration.yaml already compatible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
