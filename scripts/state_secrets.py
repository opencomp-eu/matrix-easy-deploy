#!/usr/bin/env python3

import argparse
from pathlib import Path

import yaml


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        yaml.safe_dump(data, handle, default_flow_style=False, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read/write persisted state secrets")
    parser.add_argument("--secrets-file", required=True)

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--get")
    action.add_argument("--set")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    secrets_file = Path(args.secrets_file)
    state = load_state(secrets_file)

    if args.get:
        value = state.get(args.get)
        if value is None:
            return 1
        print(str(value))
        return 0

    key_value = args.set
    if "=" not in key_value:
        raise ValueError("--set must be in KEY=VALUE format")

    key, value = key_value.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError("Secret key cannot be empty")

    state[key] = value
    save_state(secrets_file, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
