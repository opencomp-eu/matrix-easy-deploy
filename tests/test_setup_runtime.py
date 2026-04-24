import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


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
            create_admin = scripts_dir / "create-admin.sh"
            create_admin.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s' \"$4\" > \"$CAPTURE_FILE\"\n"
            )
            create_admin.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

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
            create_admin = scripts_dir / "create-admin.sh"
            create_admin.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s' \"$4\" > \"$CAPTURE_FILE\"\n"
            )
            create_admin.chmod(0o755)

            script = textwrap.dedent(
                """
                set -euo pipefail

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


if __name__ == "__main__":
    unittest.main()