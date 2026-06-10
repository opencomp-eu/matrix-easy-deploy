#!/usr/bin/env python3
"""Sync bridge appservice registration files into Tuwunel's appservice_dir."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def sync_appservice_registration(
    appservice_dir: Path,
    registration_src: Path,
    registration_filename: str,
) -> bool:
    appservice_dir.mkdir(parents=True, exist_ok=True)
    dest = appservice_dir / registration_filename

    if not registration_src.exists():
        if dest.exists():
            dest.unlink()
            return True
        return False

    needs_copy = not dest.exists() or registration_src.read_bytes() != dest.read_bytes()
    if needs_copy:
        shutil.copy2(registration_src, dest)
    return needs_copy


def remove_appservice_registration(appservice_dir: Path, registration_filename: str) -> bool:
    dest = appservice_dir / registration_filename
    if dest.exists():
        dest.unlink()
        return True
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Tuwunel appservice registration files")
    parser.add_argument("--appservice-dir", required=True)
    parser.add_argument("--registration-src", required=True)
    parser.add_argument("--registration-filename", required=True)
    parser.add_argument("--remove", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    appservice_dir = Path(args.appservice_dir)
    registration_src = Path(args.registration_src)

    if args.remove:
        changed = remove_appservice_registration(appservice_dir, args.registration_filename)
        if changed:
            print(f"Removed {args.registration_filename} from appservice_dir.")
        return 0

    changed = sync_appservice_registration(
        appservice_dir,
        registration_src,
        args.registration_filename,
    )
    if changed:
        print(f"Synced {args.registration_filename} into appservice_dir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
