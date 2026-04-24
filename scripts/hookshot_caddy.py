#!/usr/bin/env python3

import argparse
from pathlib import Path


BEGIN_MARKER = "# BEGIN MED-HOOKSHOT BLOCK"
END_MARKER = "# END MED-HOOKSHOT BLOCK"


def build_block(domain: str) -> str:
    return (
        f"{BEGIN_MARKER}\n"
        "# Hookshot bridge - webhook ingress for GitHub, GitLab, generic hooks, etc.\n"
        f"{domain} {{\n"
        "    reverse_proxy matrix-hookshot:9000\n"
        "\n"
        "    header {\n"
        "        X-Content-Type-Options nosniff\n"
        "        X-Frame-Options SAMEORIGIN\n"
        "        Referrer-Policy strict-origin-when-cross-origin\n"
        "        -Server\n"
        "    }\n"
        "\n"
        "    log\n"
        "}\n"
        f"{END_MARKER}\n"
    )


def remove_legacy_hookshot_domain_blocks(content: str, domain: str) -> str:
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    i = 0

    domain_header = f"{domain} {{"
    while i < len(lines):
        line = lines[i]
        if line.strip() != domain_header:
            out.append(line)
            i += 1
            continue

        block: list[str] = [line]
        depth = line.count("{") - line.count("}")
        i += 1

        while i < len(lines) and depth > 0:
            block.append(lines[i])
            depth += lines[i].count("{") - lines[i].count("}")
            i += 1

        block_text = "".join(block)
        if "reverse_proxy matrix-hookshot:9000" in block_text:
            continue

        out.extend(block)

    return "".join(out)


def upsert_hookshot_block(content: str, domain: str) -> str:
    cleaned = remove_legacy_hookshot_domain_blocks(content, domain)
    block = build_block(domain)

    begin = cleaned.find(BEGIN_MARKER)
    end = cleaned.find(END_MARKER)
    if begin != -1 and end != -1 and end > begin:
        end_line = cleaned.find("\n", end)
        if end_line == -1:
            end_line = len(cleaned)
        else:
            end_line += 1
        return cleaned[:begin] + block + cleaned[end_line:]

    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    if cleaned and not cleaned.endswith("\n\n"):
        cleaned += "\n"

    return cleaned + block


def remove_hookshot_block(content: str, domain: str | None = None) -> str:
    updated = content

    begin = updated.find(BEGIN_MARKER)
    end = updated.find(END_MARKER)
    if begin != -1 and end != -1 and end > begin:
        end_line = updated.find("\n", end)
        if end_line == -1:
            end_line = len(updated)
        else:
            end_line += 1
        updated = updated[:begin] + updated[end_line:]

    # Also clean legacy unmanaged hookshot blocks when domain is known.
    if domain:
        updated = remove_legacy_hookshot_domain_blocks(updated, domain)

    # Keep file tidy after removals.
    while "\n\n\n" in updated:
        updated = updated.replace("\n\n\n", "\n\n")

    return updated


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upsert Hookshot block in Caddyfile")
    parser.add_argument("--caddyfile", required=True)
    parser.add_argument("--domain", required=False)
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove Hookshot block from Caddyfile instead of upserting it",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    caddyfile = Path(args.caddyfile)
    content = caddyfile.read_text() if caddyfile.exists() else ""
    if args.remove:
        updated = remove_hookshot_block(content, args.domain)
        print("Hookshot Caddy block removed.")
    else:
        if not args.domain:
            raise ValueError("--domain is required unless --remove is set")
        updated = upsert_hookshot_block(content, args.domain)
        print("Hookshot Caddy block reconciled.")
    caddyfile.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())