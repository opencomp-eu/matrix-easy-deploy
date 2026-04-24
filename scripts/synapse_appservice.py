#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


def ensure_appservice_registration(homeserver_yaml: Path, registration_path: str) -> bool:
    content = homeserver_yaml.read_text()

    if registration_path in content:
        return False

    if "app_service_config_files" in content:
        content = re.sub(
            r"(app_service_config_files:(?:\s*\n\s+-[^\n]*)*)",
            lambda m: m.group(0) + f"\n  - {registration_path}",
            content,
            count=1,
        )
    else:
        content += (
            "\n# Application services (bridges)\n"
            "app_service_config_files:\n"
            f"  - {registration_path}\n"
        )

    homeserver_yaml.write_text(content)
    return True


def remove_appservice_registration(homeserver_yaml: Path, registration_path: str) -> bool:
    content = homeserver_yaml.read_text()
    lines = content.splitlines()

    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*app_service_config_files\s*:\s*$", line):
            header = line
            i += 1
            kept_items: list[str] = []
            removed_in_block = False

            while i < len(lines) and re.match(r"^\s*-\s+", lines[i]):
                item_line = lines[i]
                item_value = re.sub(r"^\s*-\s+", "", item_line).strip()
                if item_value == registration_path:
                    removed_in_block = True
                    changed = True
                else:
                    kept_items.append(item_line)
                i += 1

            if kept_items:
                out.append(header)
                out.extend(kept_items)
            elif not removed_in_block:
                out.append(header)

            continue

        out.append(line)
        i += 1

    if not changed:
        return False

    new_content = "\n".join(out)
    if content.endswith("\n"):
        new_content += "\n"
    homeserver_yaml.write_text(new_content)
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure Synapse appservice registration path")
    parser.add_argument("--homeserver-yaml", required=True)
    parser.add_argument("--registration-path", required=True)
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove registration path from app_service_config_files instead of adding it",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    homeserver_yaml = Path(args.homeserver_yaml)

    if not homeserver_yaml.exists():
        raise FileNotFoundError(f"homeserver.yaml not found: {homeserver_yaml}")

    if args.remove:
        changed = remove_appservice_registration(homeserver_yaml, args.registration_path)
        if changed:
            print(f"Removed {args.registration_path} from app_service_config_files.")
        else:
            print(f"Registration not present: {args.registration_path}")
    else:
        changed = ensure_appservice_registration(homeserver_yaml, args.registration_path)
        if changed:
            print(f"Added {args.registration_path} to app_service_config_files.")
        else:
            print(f"Registration already present: {args.registration_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
