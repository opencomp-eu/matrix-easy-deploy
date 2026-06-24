import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import bridge_appservice_tokens


class BridgeAppserviceTokenTests(unittest.TestCase):
    def test_needs_regeneration_for_placeholder_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            reg_path = Path(tmp) / "registration.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "appservice": {
                            "as_token": "This value is generated when generating the registration",
                            "hs_token": "This value is generated when generating the registration",
                        }
                    }
                )
            )
            reg_path.write_text(
                yaml.safe_dump(
                    {
                        "as_token": "real-as-token",
                        "hs_token": "real-hs-token",
                    }
                )
            )

            self.assertTrue(
                bridge_appservice_tokens.tokens_need_regeneration(config_path, reg_path)
            )

    def test_needs_regeneration_when_tokens_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            reg_path = Path(tmp) / "registration.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "appservice": {
                            "as_token": "token-a",
                            "hs_token": "token-hs-a",
                        }
                    }
                )
            )
            reg_path.write_text(
                yaml.safe_dump(
                    {
                        "as_token": "token-b",
                        "hs_token": "token-hs-b",
                    }
                )
            )

            self.assertTrue(
                bridge_appservice_tokens.tokens_need_regeneration(config_path, reg_path)
            )

    def test_synapse_out_of_sync_when_homeserver_missing_registration_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            reg_path = Path(tmp) / "registration.yaml"
            synapse_reg = Path(tmp) / "synapse-registration.yaml"
            homeserver = Path(tmp) / "homeserver.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "appservice": {
                            "as_token": "shared-as",
                            "hs_token": "shared-hs",
                        }
                    }
                )
            )
            reg_path.write_text(
                yaml.safe_dump(
                    {
                        "as_token": "shared-as",
                        "hs_token": "shared-hs",
                    }
                )
            )
            synapse_reg.write_text(reg_path.read_text())
            homeserver.write_text("server_name: example.com\n")

            self.assertEqual(
                bridge_appservice_tokens.main(
                    [
                        "--config-path",
                        str(config_path),
                        "--registration-path",
                        str(reg_path),
                        "--synapse-registration-path",
                        str(synapse_reg),
                        "--homeserver-yaml",
                        str(homeserver),
                        "--registration-container-path",
                        "/data/whatsapp-registration.yaml",
                        "--synapse-out-of-sync",
                    ]
                ),
                0,
            )

    def test_verify_passes_when_tokens_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            reg_path = Path(tmp) / "registration.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "appservice": {
                            "as_token": "shared-as",
                            "hs_token": "shared-hs",
                        }
                    }
                )
            )
            reg_path.write_text(
                yaml.safe_dump(
                    {
                        "as_token": "shared-as",
                        "hs_token": "shared-hs",
                    }
                )
            )

            self.assertFalse(
                bridge_appservice_tokens.tokens_need_regeneration(config_path, reg_path)
            )
            self.assertEqual(
                bridge_appservice_tokens.verify_tokens(config_path, reg_path),
                [],
            )


if __name__ == "__main__":
    unittest.main()
