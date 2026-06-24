#!/usr/bin/env python3

import argparse
import re
from pathlib import Path


def replace_field(text: str, key_path: str, new_value: str, quote: bool = True) -> str:
    parts = key_path.split(".")
    key = parts[-1]
    quoted_val = f'"{new_value}"' if quote else new_value
    key_pattern = rf"^(\s*{re.escape(key)}:)\s*.*$"

    if len(parts) == 1:
        result, _ = re.subn(key_pattern, rf"\1 {quoted_val}", text, count=1, flags=re.MULTILINE)
        return result

    section = parts[0]
    sec_match = re.search(rf"^( *){re.escape(section)}:\s*$", text, re.MULTILINE)
    if not sec_match:
        return text

    sec_indent = sec_match.group(1)
    sec_start = sec_match.end()

    body_end = len(text)
    for m in re.finditer(r"^(" + re.escape(sec_indent) + r"\S)", text[sec_start:], re.MULTILINE):
        body_end = sec_start + m.start()
        break

    section_body = text[sec_start:body_end]
    new_body, _ = re.subn(key_pattern, rf"\1 {quoted_val}", section_body, count=1, flags=re.MULTILINE)
    return text[:sec_start] + new_body + text[body_end:]


def patch_permissions(content: str, server_name: str, admin_user: str) -> str:
    perm_match = re.search(r"^( *)permissions:\s*\n((?:(?! *\S)|\1 [^\n]*\n)*)", content, re.MULTILINE)
    if perm_match:
        indent = perm_match.group(1)
        child_indent = indent + "    "
        new_block = (
            f"{indent}permissions:\n"
            f"{child_indent}\"{server_name}\": user\n"
            f"{child_indent}\"@{admin_user}:{server_name}\": admin\n"
        )
        return content[:perm_match.start()] + new_block + content[perm_match.end():]

    child_indent = "    "
    new_block = (
        f"  permissions:\n"
        f"{child_indent}\"{server_name}\": user\n"
        f"{child_indent}\"@{admin_user}:{server_name}\": admin\n"
    )
    bridge_match = re.search(r"^bridge:\s*$", content, re.MULTILINE)
    if bridge_match:
        insert_pos = content.index("\n", bridge_match.start()) + 1
        return content[:insert_pos] + new_block + content[insert_pos:]

    return content + f"\nbridge:\n{new_block}"


def patch_bridge_config(
    config_path: Path,
    server_name: str,
    hs_address: str,
    as_address: str,
    db_type: str,
    db_uri: str,
    admin_user: str,
    enable_e2ee: bool = False,
) -> None:
    content = config_path.read_text()

    content = replace_field(content, "homeserver.domain", server_name)
    content = replace_field(content, "homeserver.address", hs_address)

    content = replace_field(content, "appservice.address", as_address)
    content = replace_field(content, "appservice.hostname", "0.0.0.0")

    content = replace_field(content, "database.type", db_type)
    content = replace_field(content, "database.uri", db_uri)

    content = patch_permissions(content, server_name, admin_user)

    if enable_e2ee:
        # allow: bridge can participate in encrypted rooms.
        # default: false avoids auto-encrypting every portal; forcing encryption on all
        # bridged rooms makes Element warn that ghost senders use the bridge bot device.
        # self_sign: bridge cross-signs itself when encryption is used (recommended by mautrix).
        content = replace_field(content, "encryption.allow", "true", quote=False)
        content = replace_field(content, "encryption.default", "false", quote=False)
        content = replace_field(content, "encryption.self_sign", "true", quote=False)

    config_path.write_text(content)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch mautrix bridge config.yaml")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--hs-address", required=True)
    parser.add_argument("--as-address", required=True)
    parser.add_argument("--db-type", required=True)
    parser.add_argument("--db-uri", required=True)
    parser.add_argument("--admin-user", required=True)
    parser.add_argument(
        "--enable-e2ee",
        action="store_true",
        help="Enable end-to-bridge encryption support (encryption.allow, self_sign; default off)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    patch_bridge_config(
        config_path=Path(args.config_path),
        server_name=args.server_name,
        hs_address=args.hs_address,
        as_address=args.as_address,
        db_type=args.db_type,
        db_uri=args.db_uri,
        admin_user=args.admin_user,
        enable_e2ee=args.enable_e2ee,
    )
    print("config.yaml patched successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
