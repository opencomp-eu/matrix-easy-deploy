import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


class WhatsAppSetupNonInteractiveTests(unittest.TestCase):
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
                            "admin_username": "admin",
                        },
                        "modules": {
                            "whatsapp_bridge": {
                                "enabled": True,
                                "admin_username": "yamladmin",
                                "db_name": "yaml_whatsapp",
                            }
                        },
                    },
                    sort_keys=False,
                )
            )

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/whatsapp-bridge/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1 MED_NON_INTERACTIVE=1; "
                f"source '{script}'; "
                f"DEPLOY_YAML='{deploy_yaml}'; "
                "ADMIN_USERNAME='envadmin'; "
                "WA_ADMIN_USERNAME='from_env'; "
                "WA_DB_NAME='from_env_db'; "
                "load_module_defaults; "
                "gather_config >/dev/null; "
                "printf '%s\\n%s\\n' \"$WA_ADMIN_USERNAME\" \"$WA_DB_NAME\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 2)
            self.assertEqual(lines[-2], "yamladmin")
            self.assertEqual(lines[-1], "yaml_whatsapp")


if __name__ == "__main__":
    unittest.main()
