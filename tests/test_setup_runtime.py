import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


class SetupRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.runtime_sh = self.repo_root / "scripts/setup/runtime.sh"

    def _run_script(self, script: str, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_setup_admin_uses_admin_password_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            capture_file = tmp_path / "captured-password.txt"

            scripts_dir = tmp_path / "scripts"
            scripts_dir.mkdir(parents=True)
            create_account = scripts_dir / "create-account.sh"
            create_account.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    --password)\n"
                "      printf '%s' \"$2\" > \"$CAPTURE_FILE\"\n"
                "      exit 0\n"
                "      ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "exit 1\n"
            )
            create_account.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

                wait_for_mas_http() { return 0; }
                source "$RUNTIME_SH"

                info() { :; }
                warn() { :; }
                success() { :; }

                ask_secret() {
                    echo "ask_secret should not be called when ADMIN_PASSWORD is set" >&2
                    return 99
                }

                docker() {
                    if [[ "$1" == "inspect" ]]; then
                        echo "healthy"
                        return 0
                    fi
                    return 0
                }

                CYAN=""
                RESET=""
                SCRIPT_DIR="$TMP_DIR"
                MATRIX_DOMAIN="matrix.example.com"
                REGISTRATION_SHARED_SECRET="reg-secret"
                ADMIN_USERNAME="admin"
                SERVER_NAME="example.com"

                setup_admin
                """
            )

            env = os.environ.copy()
            env.update(
                {
                    "RUNTIME_SH": str(self.runtime_sh),
                    "TMP_DIR": str(tmp_path),
                    "CAPTURE_FILE": str(capture_file),
                    "ADMIN_PASSWORD": "env-super-secret-pass",
                }
            )

            result = self._run_script(script, env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(capture_file.read_text(), "env-super-secret-pass")

    def test_setup_admin_prompts_for_password_when_env_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            capture_file = tmp_path / "captured-password.txt"
            ask_count_file = tmp_path / "ask-count.txt"

            scripts_dir = tmp_path / "scripts"
            scripts_dir.mkdir(parents=True)
            create_account = scripts_dir / "create-account.sh"
            create_account.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  case \"$1\" in\n"
                "    --password)\n"
                "      printf '%s' \"$2\" > \"$CAPTURE_FILE\"\n"
                "      exit 0\n"
                "      ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "exit 1\n"
            )
            create_account.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

                wait_for_mas_http() { return 0; }
                source "$RUNTIME_SH"

                info() { :; }
                warn() { :; }
                success() { :; }

                ASK_COUNT=0
                ask_secret() {
                    local _var="$1"
                    ASK_COUNT=$((ASK_COUNT + 1))
                    if [[ "$ASK_COUNT" -eq 1 ]]; then
                        printf -v "$_var" '%s' "short"
                    else
                        printf -v "$_var" '%s' "long-enough-password"
                    fi
                }

                docker() {
                    if [[ "$1" == "inspect" ]]; then
                        echo "healthy"
                        return 0
                    fi
                    return 0
                }

                CYAN=""
                RESET=""
                SCRIPT_DIR="$TMP_DIR"
                MATRIX_DOMAIN="matrix.example.com"
                REGISTRATION_SHARED_SECRET="reg-secret"
                ADMIN_USERNAME="admin"
                SERVER_NAME="example.com"

                setup_admin
                printf '%s' "$ASK_COUNT" > "$ASK_COUNT_FILE"
                """
            )

            env = os.environ.copy()
            env.update(
                {
                    "RUNTIME_SH": str(self.runtime_sh),
                    "TMP_DIR": str(tmp_path),
                    "CAPTURE_FILE": str(capture_file),
                    "ASK_COUNT_FILE": str(ask_count_file),
                }
            )
            env.pop("ADMIN_PASSWORD", None)

            result = self._run_script(script, env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(capture_file.read_text(), "long-enough-password")
            self.assertEqual(ask_count_file.read_text(), "3")

    def test_start_services_stops_existing_stack_before_resetting_core_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            events_file = tmp_path / "events.log"

            (tmp_path / "caddy").mkdir(parents=True)
            (tmp_path / "modules/core").mkdir(parents=True)
            (tmp_path / "modules/calls").mkdir(parents=True)

            stop_script = tmp_path / "stop.sh"
            stop_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho stop-script >> \"$EVENTS_FILE\"\n")
            stop_script.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

                wait_for_mas_http() { return 0; }
                source "$RUNTIME_SH"

                info() { :; }
                warn() { :; }
                success() { :; }

                record_compose() {
                    printf 'compose:%s:%s\n' "$PWD" "$*" >> "$EVENTS_FILE"
                }

                docker() {
                    if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
                        return 0
                    fi
                    if [[ "$1" == "volume" && "$2" == "rm" ]]; then
                        printf 'docker-rm:%s\n' "$3" >> "$EVENTS_FILE"
                        return 0
                    fi
                    return 0
                }

                SCRIPT_DIR="$TMP_DIR"
                EVENTS_FILE="$EVENTS_FILE"
                DOCKER_COMPOSE=(record_compose)
                INSTALL_ELEMENT="true"
                POSTGRES_PASSWORD="secret"
                HOMESERVER_COMPOSE_PROFILE="synapse"

                build_core_compose_stop_profiles() {
                    CORE_COMPOSE_PROFILES=(--profile synapse --profile tuwunel --profile element)
                }
                build_core_compose_start_profiles() {
                    CORE_COMPOSE_PROFILES=(--profile synapse --profile element)
                }

                start_services
                """
            )

            env = os.environ.copy()
            env.update(
                {
                    "RUNTIME_SH": str(self.runtime_sh),
                    "TMP_DIR": str(tmp_path),
                    "EVENTS_FILE": str(events_file),
                }
            )

            result = self._run_script(script, env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            events = events_file.read_text().splitlines()
            self.assertIn("stop-script", events)
            self.assertIn("docker-rm:core_postgres_data", events)
            self.assertLess(events.index("stop-script"), events.index("docker-rm:core_postgres_data"))

    def test_start_services_skips_stop_when_core_volume_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            events_file = tmp_path / "events.log"

            (tmp_path / "caddy").mkdir(parents=True)
            (tmp_path / "modules/core").mkdir(parents=True)
            (tmp_path / "modules/calls").mkdir(parents=True)

            stop_script = tmp_path / "stop.sh"
            stop_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho stop-script >> \"$EVENTS_FILE\"\n")
            stop_script.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

                wait_for_mas_http() { return 0; }
                source "$RUNTIME_SH"

                info() { :; }
                warn() { :; }
                success() { :; }

                record_compose() {
                    printf 'compose:%s:%s\n' "$PWD" "$*" >> "$EVENTS_FILE"
                }

                docker() {
                    if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
                        return 1
                    fi
                    if [[ "$1" == "volume" && "$2" == "rm" ]]; then
                        printf 'docker-rm:%s\n' "$3" >> "$EVENTS_FILE"
                        return 0
                    fi
                    return 0
                }

                SCRIPT_DIR="$TMP_DIR"
                EVENTS_FILE="$EVENTS_FILE"
                DOCKER_COMPOSE=(record_compose)
                INSTALL_ELEMENT="false"
                POSTGRES_PASSWORD="secret"
                HOMESERVER_COMPOSE_PROFILE="synapse"

                build_core_compose_stop_profiles() {
                    CORE_COMPOSE_PROFILES=(--profile synapse --profile tuwunel --profile element)
                }
                build_core_compose_start_profiles() {
                    CORE_COMPOSE_PROFILES=(--profile synapse)
                }

                start_services
                """
            )

            env = os.environ.copy()
            env.update(
                {
                    "RUNTIME_SH": str(self.runtime_sh),
                    "TMP_DIR": str(tmp_path),
                    "EVENTS_FILE": str(events_file),
                }
            )

            result = self._run_script(script, env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            events = events_file.read_text().splitlines() if events_file.exists() else []
            self.assertNotIn("stop-script", events)
            self.assertFalse(any(line.startswith("docker-rm:") for line in events))

    def test_start_services_starts_mas_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            events_file = tmp_path / "events.log"

            (tmp_path / "caddy").mkdir(parents=True)
            (tmp_path / "modules/core").mkdir(parents=True)
            (tmp_path / "modules/calls").mkdir(parents=True)
            (tmp_path / "modules/mas").mkdir(parents=True)
            (tmp_path / "modules/mas/config.yaml").write_text("http:\n  public_base: https://matrix.example.com/auth/\n")
            (tmp_path / "modules/mas/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
            (tmp_path / "modules/mas/setup.sh").chmod(0o755)
            (tmp_path / ".env").write_text(
                "\n".join(
                    [
                        "MATRIX_DOMAIN=matrix.example.com",
                        "SERVER_NAME=matrix.example.com",
                        "MAS_ENABLED=true",
                        "MAS_DB_PASSWORD=secret",
                        "POSTGRES_PASSWORD=secret",
                    ]
                )
                + "\n"
            )

            script = textwrap.dedent(
                """
                set -euo pipefail

                wait_for_mas_http() { return 0; }
                source "$RUNTIME_SH"

                info() { :; }
                warn() { :; }
                success() { :; }

                record_compose() {
                    printf 'compose:%s:%s\n' "$PWD" "$*" >> "$EVENTS_FILE"
                }

                docker() {
                    if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
                        return 1
                    fi
                    if [[ "$1" == "ps" ]]; then
                        echo "matrix_postgres"
                        return 0
                    fi
                    if [[ "$1" == "exec" ]]; then
                        return 0
                    fi
                    return 0
                }

                SCRIPT_DIR="$TMP_DIR"
                EVENTS_FILE="$EVENTS_FILE"
                DOCKER_COMPOSE=(record_compose)
                INSTALL_ELEMENT="false"
                POSTGRES_PASSWORD="secret"
                HOMESERVER_COMPOSE_PROFILE="synapse"
                MAS_ENABLED="true"

                build_core_compose_stop_profiles() {
                    CORE_COMPOSE_PROFILES=(--profile synapse --profile tuwunel --profile element)
                }
                build_core_compose_start_profiles() {
                    CORE_COMPOSE_PROFILES=(--profile synapse)
                }

                start_services
                """
            )

            env = os.environ.copy()
            env.update(
                {
                    "RUNTIME_SH": str(self.runtime_sh),
                    "TMP_DIR": str(tmp_path),
                    "EVENTS_FILE": str(events_file),
                }
            )

            result = self._run_script(script, env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            events = events_file.read_text().splitlines()
            self.assertTrue(any("modules/mas" in line for line in events))

    def test_setup_admin_omits_shared_secret_when_mas_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            capture_file = tmp_path / "captured-args.txt"

            scripts_dir = tmp_path / "scripts"
            scripts_dir.mkdir(parents=True)
            create_account = scripts_dir / "create-account.sh"
            create_account.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s\\n' \"$*\" > \"$CAPTURE_FILE\"\n"
                "exit 0\n"
            )
            create_account.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

                wait_for_mas_http() { return 0; }
                source "$RUNTIME_SH"

                info() { :; }
                warn() { :; }
                success() { :; }

                ask_secret() {
                    printf -v "$1" '%s' "long-enough-password"
                }

                docker() {
                    if [[ "$1" == "inspect" ]]; then
                        echo "healthy"
                        return 0
                    fi
                    if [[ "$1" == "exec" ]]; then
                        return 0
                    fi
                    return 0
                }

                CYAN=""
                RESET=""
                SCRIPT_DIR="$TMP_DIR"
                MATRIX_DOMAIN="matrix.example.com"
                REGISTRATION_SHARED_SECRET="reg-secret"
                ADMIN_USERNAME="admin"
                SERVER_NAME="example.com"
                MAS_ENABLED="true"
                ADMIN_PASSWORD="long-enough-password"

                setup_admin
                """
            )

            env = os.environ.copy()
            env.update(
                {
                    "RUNTIME_SH": str(self.runtime_sh),
                    "TMP_DIR": str(tmp_path),
                    "CAPTURE_FILE": str(capture_file),
                }
            )

            result = self._run_script(script, env)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            args = capture_file.read_text()
            self.assertNotIn("--shared-secret", args)


if __name__ == "__main__":
    unittest.main()