import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import bridge_registration_patch


class BridgeRegistrationPatchTests(unittest.TestCase):
    def test_adds_msc_flags_and_random_sender_localpart(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            reg_path = Path(tmp) / "registration.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "homeserver": {"domain": "example.com"},
                        "appservice": {
                            "address": "http://mautrix-whatsapp:29318",
                            "ephemeral_events": True,
                            "bot": {"username": "whatsappbot"},
                        },
                        "encryption": {"allow": True},
                    }
                )
            )
            reg_path.write_text(
                yaml.safe_dump(
                    {
                        "id": "whatsapp",
                        "url": "http://localhost:29318",
                        "sender_localpart": "whatsappbot",
                        "as_token": "as",
                        "hs_token": "hs",
                    }
                )
            )

            changed = bridge_registration_patch.patch_registration(config_path, reg_path)
            self.assertTrue(changed)

            registration = yaml.safe_load(reg_path.read_text())
            self.assertEqual(registration["url"], "http://mautrix-whatsapp:29318")
            self.assertTrue(registration["receive_ephemeral"])
            self.assertTrue(registration["org.matrix.msc3202"])
            self.assertTrue(registration["io.element.msc4190"])
            self.assertNotEqual(registration["sender_localpart"], "whatsappbot")


if __name__ == "__main__":
    unittest.main()
