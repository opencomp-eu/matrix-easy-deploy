#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

import yaml

PLACEHOLDER_TOKENS = {
    "This value is generated when generating the registration",
    "generate",
}


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def config_tokens(config_path: Path) -> tuple[str, str]:
    data = load_yaml(config_path)
    appservice = data.get("appservice", {})
    if not isinstance(appservice, dict):
        return "", ""
    as_token = str(appservice.get("as_token", "") or "").strip()
    hs_token = str(appservice.get("hs_token", "") or "").strip()
    return as_token, hs_token


def registration_tokens(registration_path: Path) -> tuple[str, str]:
    data = load_yaml(registration_path)
    as_token = str(data.get("as_token", "") or "").strip()
    hs_token = str(data.get("hs_token", "") or "").strip()
    return as_token, hs_token


def tokens_need_regeneration(config_path: Path, registration_path: Path) -> bool:
    cfg_as, cfg_hs = config_tokens(config_path)
    if not cfg_as or not cfg_hs:
        return True
    if cfg_as in PLACEHOLDER_TOKENS or cfg_hs in PLACEHOLDER_TOKENS:
        return True
    if not registration_path.exists():
        return True

    reg_as, reg_hs = registration_tokens(registration_path)
    if not reg_as or not reg_hs:
        return True
    return cfg_as != reg_as or cfg_hs != reg_hs


def synapse_registration_out_of_sync(
    config_path: Path,
    registration_src: Path,
    registration_dest: Path,
) -> bool:
    if not registration_dest.exists():
        return True
    if tokens_need_regeneration(config_path, registration_src):
        return True

    cfg_as, cfg_hs = config_tokens(config_path)
    dest_as, dest_hs = registration_tokens(registration_dest)
    return cfg_as != dest_as or cfg_hs != dest_hs


def homeserver_lists_registration(homeserver_config: Path, registration_container_path: str) -> bool:
    if not homeserver_config.exists():
        return False
    return registration_container_path in homeserver_config.read_text(encoding="utf-8")


def verify_tokens(config_path: Path, registration_path: Path) -> list[str]:
    errors: list[str] = []
    cfg_as, cfg_hs = config_tokens(config_path)

    if not cfg_as or cfg_as in PLACEHOLDER_TOKENS:
        errors.append("config.yaml appservice.as_token is missing or still a placeholder")
    if not cfg_hs or cfg_hs in PLACEHOLDER_TOKENS:
        errors.append("config.yaml appservice.hs_token is missing or still a placeholder")
    if not registration_path.exists():
        errors.append(f"{registration_path.name} does not exist")
        return errors

    reg_as, reg_hs = registration_tokens(registration_path)
    if not reg_as:
        errors.append(f"{registration_path.name} as_token is missing")
    if not reg_hs:
        errors.append(f"{registration_path.name} hs_token is missing")
    if cfg_as and reg_as and cfg_as != reg_as:
        errors.append("config.yaml and registration.yaml as_token values do not match")
    if cfg_hs and reg_hs and cfg_hs != reg_hs:
        errors.append("config.yaml and registration.yaml hs_token values do not match")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check mautrix bridge appservice token consistency")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--registration-path", required=True)
    parser.add_argument("--synapse-registration-path")
    parser.add_argument("--homeserver-yaml")
    parser.add_argument("--registration-container-path")
    parser.add_argument(
        "--needs-regeneration",
        action="store_true",
        help="Exit 0 when registration should be regenerated, 1 when tokens already match",
    )
    parser.add_argument(
        "--synapse-out-of-sync",
        action="store_true",
        help="Exit 0 when the Synapse registration copy is missing or token-mismatched",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Exit non-zero when config and registration tokens are invalid or mismatched",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config_path)
    registration_path = Path(args.registration_path)

    if args.needs_regeneration:
        return 0 if tokens_need_regeneration(config_path, registration_path) else 1

    if args.synapse_out_of_sync:
        if not args.synapse_registration_path:
            print("--synapse-registration-path is required with --synapse-out-of-sync", file=sys.stderr)
            return 2
        out_of_sync = synapse_registration_out_of_sync(
            config_path,
            registration_path,
            Path(args.synapse_registration_path),
        )
        if args.homeserver_yaml and args.registration_container_path:
            if not homeserver_lists_registration(
                Path(args.homeserver_yaml),
                args.registration_container_path,
            ):
                return 0
        return 0 if out_of_sync else 1

    if args.verify:
        errors = verify_tokens(config_path, registration_path)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("Bridge appservice tokens are consistent.")
        return 0

    raise SystemExit("Specify --needs-regeneration, --synapse-out-of-sync, or --verify")


if __name__ == "__main__":
    raise SystemExit(main())
