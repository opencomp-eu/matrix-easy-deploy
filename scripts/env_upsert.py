#!/usr/bin/env python3

import argparse
from pathlib import Path


def parse_kv(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise ValueError(f"Invalid --set value (expected KEY=VALUE): {item}")
    key, value = item.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Invalid empty key in --set value: {item}")
    return key, value


def upsert_env_text(content: str, updates: dict[str, str]) -> str:
    lines = content.splitlines()
    out: list[str] = []
    seen = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue

        key, _ = line.split("=", 1)
        key = key.strip()
        if key in updates:
            if key not in seen:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
            # Drop duplicate definitions for updated keys.
            continue

        out.append(line)

    for key, value in updates.items():
        if key not in seen:
            if out and out[-1].strip() != "":
                out.append("")
            out.append(f"{key}={value}")

    return "\n".join(out) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upsert KEY=VALUE entries in a .env file")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--set", action="append", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_file = Path(args.env_file)

    updates: dict[str, str] = {}
    for item in args.set:
        key, value = parse_kv(item)
        updates[key] = value

    if env_file.exists():
        original = env_file.read_text()
    else:
        original = ""

    updated = upsert_env_text(original, updates)
    env_file.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
