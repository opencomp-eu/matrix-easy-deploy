import tempfile
import unittest
from pathlib import Path

from scripts import tuwunel_appservice


class TuwunelAppserviceTests(unittest.TestCase):
    def test_sync_and_remove_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            src = root / "registration.yaml"
            src.write_text("id: test\n")

            changed = tuwunel_appservice.sync_appservice_registration(
                appservice_dir, src, "registration.yaml"
            )
            self.assertTrue(changed)
            self.assertTrue((appservice_dir / "registration.yaml").exists())

            changed_again = tuwunel_appservice.sync_appservice_registration(
                appservice_dir, src, "registration.yaml"
            )
            self.assertFalse(changed_again)

            removed = tuwunel_appservice.remove_appservice_registration(
                appservice_dir, "registration.yaml"
            )
            self.assertTrue(removed)
            self.assertFalse((appservice_dir / "registration.yaml").exists())


if __name__ == "__main__":
    unittest.main()
