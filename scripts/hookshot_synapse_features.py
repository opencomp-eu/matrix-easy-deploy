#!/usr/bin/env python3

import argparse
from pathlib import Path


REQUIRED_FLAGS = {
    "msc2409_to_device_messages_enabled": "true",
    "msc3202_device_masquerading": "true",
    "msc3202_transaction_extensions": "true",
}


def ensure_experimental_features_block(content: str) -> str:
    lines = content.splitlines()
    exp_idx = next((i for i, line in enumerate(lines) if line.strip() == "experimental_features:"), None)

    if exp_idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("experimental_features:")
        for key, value in REQUIRED_FLAGS.items():
            lines.append(f"  {key}: {value}")
        return "\n".join(lines) + "\n"

    j = exp_idx + 1
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            j += 1
            continue
        if not line.startswith("  "):
            break
        j += 1

    block_lines = lines[exp_idx + 1:j]
    existing: dict[str, int] = {}
    for idx, line in enumerate(block_lines):
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip()
        if key in REQUIRED_FLAGS:
            existing[key] = exp_idx + 1 + idx

    for key, value in REQUIRED_FLAGS.items():
        if key in existing:
            lines[existing[key]] = f"  {key}: {value}"
        else:
            lines.insert(j, f"  {key}: {value}")
            j += 1

    return "\n".join(lines) + "\n"


def reconcile_synapse_features(homeserver_yaml: Path) -> None:
    content = homeserver_yaml.read_text(encoding="utf-8")
    updated = ensure_experimental_features_block(content)
    homeserver_yaml.write_text(updated, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure Hookshot-required Synapse experimental features")
    parser.add_argument("--homeserver-yaml", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reconcile_synapse_features(Path(args.homeserver_yaml))
    print("Synapse experimental_features reconciled for Hookshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())