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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ensure Synapse appservice registration path")
    parser.add_argument("--homeserver-yaml", required=True)
    parser.add_argument("--registration-path", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    homeserver_yaml = Path(args.homeserver_yaml)

    if not homeserver_yaml.exists():
        raise FileNotFoundError(f"homeserver.yaml not found: {homeserver_yaml}")

    changed = ensure_appservice_registration(homeserver_yaml, args.registration_path)
    if changed:
        print(f"Added {args.registration_path} to app_service_config_files.")
    else:
        print(f"Registration already present: {args.registration_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
