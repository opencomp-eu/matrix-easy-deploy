import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration


class HookshotSetupNonInteractiveTests(unittest.TestCase):
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
                            "hookshot": {
                                "enabled": True,
                                "domain": "hookshot.yaml.example.com",
                            }
                        },
                    },
                    sort_keys=False,
                )
            )

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/hookshot/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1 MED_NON_INTERACTIVE=1; "
                f"source '{script}'; "
                f"DEPLOY_YAML='{deploy_yaml}'; "
                "MATRIX_DOMAIN='matrix.example.com'; "
                "HOOKSHOT_DOMAIN='legacy-hookshot.example.com'; "
                "load_module_defaults; "
                "gather_config >/dev/null; "
                "printf '%s\n' \"$HOOKSHOT_DOMAIN\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 1)
            self.assertEqual(lines[-1], "hookshot.yaml.example.com")

    def test_gather_config_does_not_use_legacy_hookshot_env_fallback(self):
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
                            "hookshot": {
                                "enabled": True,
                            }
                        },
                    },
                    sort_keys=False,
                )
            )

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/hookshot/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1 MED_NON_INTERACTIVE=1; "
                f"source '{script}'; "
                f"DEPLOY_YAML='{deploy_yaml}'; "
                "MATRIX_DOMAIN='matrix.example.com'; "
                "HOOKSHOT_DOMAIN='legacy-hookshot.example.com'; "
                "load_module_defaults; "
                "gather_config >/dev/null; "
                "printf '%s\n' \"$HOOKSHOT_DOMAIN\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 1)
            self.assertEqual(lines[-1], "hookshot.example.com")

    def test_resolve_hookshot_tokens_reuses_persisted_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            secrets_file = tmp_path / "secrets.yaml"

            repo_root = Path(__file__).resolve().parent.parent
            script = repo_root / "modules/hookshot/setup.sh"
            cmd = (
                "set -euo pipefail; "
                "export MED_SOURCE_ONLY=1; "
                f"source '{script}'; "
                f"STATE_SECRETS='{secrets_file}'; "
                "resolve_hookshot_tokens; "
                "as1=\"$HOOKSHOT_AS_TOKEN\"; hs1=\"$HOOKSHOT_HS_TOKEN\"; "
                "unset HOOKSHOT_AS_TOKEN HOOKSHOT_HS_TOKEN; "
                "resolve_hookshot_tokens; "
                "as2=\"$HOOKSHOT_AS_TOKEN\"; hs2=\"$HOOKSHOT_HS_TOKEN\"; "
                "printf '%s\n%s\n%s\n%s\n' \"$as1\" \"$as2\" \"$hs1\" \"$hs2\""
            )

            result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.assertGreaterEqual(len(lines), 4)
            self.assertEqual(lines[-4], lines[-3])
            self.assertEqual(lines[-2], lines[-1])


if __name__ == "__main__":
    unittest.main()
