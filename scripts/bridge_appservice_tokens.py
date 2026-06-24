#!/usr/bin/env python3

import argparse
import subprocess
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


def config_domain(config_path: Path) -> str:
    data = load_yaml(config_path)
    homeserver = data.get("homeserver", {})
    if not isinstance(homeserver, dict):
        return ""
    return str(homeserver.get("domain", "") or "").strip()


def synapse_server_name(homeserver_yaml: Path) -> str:
    if not homeserver_yaml.exists():
        return ""
    for line in homeserver_yaml.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("server_name:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


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


def test_synapse_accepts_token(
    *,
    as_token: str,
    server_name: str,
    bot_username: str,
    synapse_container: str,
) -> tuple[bool, str]:
    bot_mxid = f"@{bot_username}:{server_name}"
    script = f"""
import sys
import urllib.error
import urllib.parse
import urllib.request

bot = {bot_mxid!r}
token = {as_token!r}
url = "http://localhost:8008/_matrix/client/versions?" + urllib.parse.urlencode({{"user_id": bot}})
req = urllib.request.Request(url, headers={{"Authorization": f"Bearer {{token}}"}})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(resp.status)
except urllib.error.HTTPError as exc:
    print(exc.code)
    sys.exit(0)
except Exception as exc:
    print(f"error: {{exc}}", file=sys.stderr)
    sys.exit(1)
"""
    result = subprocess.run(
        ["docker", "exec", synapse_container, "python3", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "docker exec failed").strip()
        return False, detail

    status = (result.stdout or "").strip()
    if status == "200":
        return True, "Synapse accepted the bridge as_token"
    return False, f"Synapse returned HTTP {status} for as_token (registration not loaded or token rejected)"


def verify_deployment(
    config_path: Path,
    registration_path: Path,
    synapse_registration_path: Path,
    homeserver_yaml: Path,
    registration_container_path: str,
    server_name: str,
    bot_username: str,
    synapse_container: str,
) -> list[str]:
    errors = verify_tokens(config_path, registration_path)

    config_sn = config_domain(config_path)
    if config_sn and server_name and config_sn != server_name:
        errors.append(
            f"config.yaml homeserver.domain ({config_sn}) does not match server_name ({server_name})"
        )

    homeserver_sn = synapse_server_name(homeserver_yaml)
    if homeserver_sn and server_name and homeserver_sn != server_name:
        errors.append(
            f"homeserver.yaml server_name ({homeserver_sn}) does not match expected server_name ({server_name})"
        )

    if not homeserver_lists_registration(homeserver_yaml, registration_container_path):
        errors.append(
            f"homeserver.yaml does not list {registration_container_path} in app_service_config_files"
        )

    if not synapse_registration_path.exists():
        errors.append(f"Synapse registration copy missing: {synapse_registration_path}")
    elif synapse_registration_path.read_bytes() != registration_path.read_bytes():
        errors.append("Synapse registration copy is out of sync with bridge registration.yaml")

    as_token, _ = config_tokens(config_path)
    if as_token and not errors:
        accepted, detail = test_synapse_accepts_token(
            as_token=as_token,
            server_name=server_name,
            bot_username=bot_username,
            synapse_container=synapse_container,
        )
        if not accepted:
            errors.append(detail)
            errors.append(
                "Check Synapse startup logs for appservice load errors: "
                f"docker logs {synapse_container} 2>&1 | grep -i appservice | tail -20"
            )

    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check mautrix bridge appservice token consistency")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--registration-path", required=True)
    parser.add_argument("--synapse-registration-path")
    parser.add_argument("--homeserver-yaml")
    parser.add_argument("--registration-container-path")
    parser.add_argument("--server-name")
    parser.add_argument("--bot-username", default="whatsappbot")
    parser.add_argument("--synapse-container", default="matrix_synapse")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Exit non-zero when config and registration tokens are invalid or mismatched",
    )
    parser.add_argument(
        "--verify-deployment",
        action="store_true",
        help="Verify on-disk wiring and that Synapse accepts the bridge as_token",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config_path)
    registration_path = Path(args.registration_path)

    if args.verify:
        errors = verify_tokens(config_path, registration_path)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("Bridge appservice tokens are consistent.")
        return 0

    if args.verify_deployment:
        if not args.synapse_registration_path or not args.homeserver_yaml or not args.registration_container_path:
            print(
                "--verify-deployment requires --synapse-registration-path, "
                "--homeserver-yaml, and --registration-container-path",
                file=sys.stderr,
            )
            return 2
        if not args.server_name:
            print("--verify-deployment requires --server-name", file=sys.stderr)
            return 2
        errors = verify_deployment(
            config_path=config_path,
            registration_path=registration_path,
            synapse_registration_path=Path(args.synapse_registration_path),
            homeserver_yaml=Path(args.homeserver_yaml),
            registration_container_path=args.registration_container_path,
            server_name=args.server_name,
            bot_username=args.bot_username,
            synapse_container=args.synapse_container,
        )
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print("WhatsApp bridge appservice deployment looks healthy.")
        return 0

    raise SystemExit("Specify --verify or --verify-deployment")


if __name__ == "__main__":
    raise SystemExit(main())
