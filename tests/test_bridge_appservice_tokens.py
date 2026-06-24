import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import bridge_appservice_tokens


class BridgeAppserviceTokenTests(unittest.TestCase):
    def test_verify_fails_for_placeholder_tokens(self):
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

            errors = bridge_appservice_tokens.verify_tokens(config_path, reg_path)
            self.assertTrue(errors)

    def test_verify_fails_when_tokens_mismatch(self):
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

            errors = bridge_appservice_tokens.verify_tokens(config_path, reg_path)
            self.assertIn("as_token values do not match", "\n".join(errors))

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

            self.assertEqual(
                bridge_appservice_tokens.verify_tokens(config_path, reg_path),
                [],
            )


if __name__ == "__main__":
    unittest.main()
