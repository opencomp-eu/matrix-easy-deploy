import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


class SlackSetupNonInteractiveTests(unittest.TestCase):
    def test_gather_config_prefers_deploy_yaml_module_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deploy_yaml = tmp_path / "deploy.yaml"
            deploy_yaml.write_text(
                yaml.safe_dump(
                    {
                        "matrix": {
                            "domain": "matrix.example.com",
                            "server_name": "example.com",
                        },
                        "modules": {
                            "slack_bridge": {
                                "enabled": True,
                                "admin_username": "yamladmin",
                                "db_name": "yaml_slack",
                            }
                        },
                    },
                    sort_keys=False,
                )
            )

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/slack-bridge/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1 MED_NON_INTERACTIVE=1; "
                f"source '{script}'; "
                f"DEPLOY_YAML='{deploy_yaml}'; "
                "SL_ADMIN_USERNAME='from_env'; "
                "SL_DB_NAME='from_env_db'; "
                "load_module_defaults; "
                "gather_config >/dev/null; "
                "printf '%s\\n%s\\n' \"$SL_ADMIN_USERNAME\" \"$SL_DB_NAME\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 2)
            self.assertEqual(lines[-2], "yamladmin")
            self.assertEqual(lines[-1], "yaml_slack")

    def test_gather_config_does_not_use_legacy_sl_env_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deploy_yaml = tmp_path / "deploy.yaml"
            deploy_yaml.write_text(
                yaml.safe_dump(
                    {
                        "matrix": {
                            "domain": "matrix.example.com",
                            "server_name": "example.com",
                        },
                        "modules": {
                            "slack_bridge": {
                                "enabled": True,
                            }
                        },
                    },
                    sort_keys=False,
                )
            )

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/slack-bridge/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1 MED_NON_INTERACTIVE=1; "
                f"source '{script}'; "
                f"DEPLOY_YAML='{deploy_yaml}'; "
                "SL_ADMIN_USERNAME='legacy_sl_admin'; "
                "SL_DB_NAME='legacy_sl_db'; "
                "load_module_defaults; "
                "gather_config >/dev/null; "
                "printf '%s\\n%s\\n' \"$SL_ADMIN_USERNAME\" \"$SL_DB_NAME\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 2)
            self.assertEqual(lines[-2], "admin")
            self.assertEqual(lines[-1], "mautrix_slack")

    def test_resolve_database_credentials_reuses_persisted_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            secrets_file = tmp_path / "secrets.yaml"

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/slack-bridge/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1; "
                f"source '{script}'; "
                f"STATE_SECRETS='{secrets_file}'; "
                "SL_DB_NAME='sl_test'; "
                "resolve_database_credentials; "
                "first=\"$SL_DB_PASSWORD\"; "
                "unset SL_DB_PASSWORD; "
                "resolve_database_credentials; "
                "second=\"$SL_DB_PASSWORD\"; "
                "printf '%s\\n%s\\n' \"$first\" \"$second\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 2)
            self.assertEqual(lines[-2], lines[-1])


if __name__ == "__main__":
    unittest.main()
