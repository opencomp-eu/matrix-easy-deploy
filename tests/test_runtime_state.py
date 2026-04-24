import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import runtime_state


class RuntimeStateTests(unittest.TestCase):
    def test_uses_deploy_yaml_values_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "features": {"element": {"enabled": False}},
                        "modules": {
                            "hookshot": {"enabled": True},
                            "whatsapp_bridge": {"enabled": False},
                            "slack_bridge": {"enabled": True},
                        },
                    },
                    sort_keys=False,
                )
            )

            state = runtime_state.resolve_runtime_state(root)
            self.assertEqual(state["INSTALL_ELEMENT"], "false")
            self.assertEqual(state["HOOKSHOT_ENABLED"], "true")
            self.assertEqual(state["WHATSAPP_BRIDGE_ENABLED"], "false")
            self.assertEqual(state["SLACK_BRIDGE_ENABLED"], "true")

    def test_falls_back_to_modules_state_for_missing_module_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "features": {"element": {"enabled": True}},
                        "modules": {"hookshot": {"enabled": False}},
                    },
                    sort_keys=False,
                )
            )
            state_dir = root / ".matrix-easy-deploy"
            state_dir.mkdir(parents=True)
            (state_dir / "modules.yaml").write_text(
                yaml.safe_dump(
                    {
                        "whatsapp_bridge": {"enabled": True},
                        "slack_bridge": {"enabled": True},
                    },
                    sort_keys=True,
                )
            )

            state = runtime_state.resolve_runtime_state(root)
            self.assertEqual(state["HOOKSHOT_ENABLED"], "false")
            self.assertEqual(state["WHATSAPP_BRIDGE_ENABLED"], "true")
            self.assertEqual(state["SLACK_BRIDGE_ENABLED"], "true")

    def test_defaults_without_any_state_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = runtime_state.resolve_runtime_state(root)
            self.assertEqual(state["INSTALL_ELEMENT"], "true")
            self.assertEqual(state["HOOKSHOT_ENABLED"], "false")
            self.assertEqual(state["WHATSAPP_BRIDGE_ENABLED"], "false")
            self.assertEqual(state["SLACK_BRIDGE_ENABLED"], "false")


if __name__ == "__main__":
    unittest.main()