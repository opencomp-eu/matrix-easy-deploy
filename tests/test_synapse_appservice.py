import tempfile
import unittest
from pathlib import Path

from scripts import synapse_appservice


class SynapseAppserviceTests(unittest.TestCase):
    def test_adds_registration_when_list_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text("server_name: example.com\n")

            changed = synapse_appservice.ensure_appservice_registration(path, "/data/test.yml")
            content = path.read_text()

            self.assertTrue(changed)
            self.assertIn("app_service_config_files:", content)
            self.assertIn("  - /data/test.yml", content)

    def test_appends_registration_when_list_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text(
                "server_name: example.com\n"
                "app_service_config_files:\n"
                "  - /data/one.yml\n"
            )

            changed = synapse_appservice.ensure_appservice_registration(path, "/data/two.yml")
            content = path.read_text()

            self.assertTrue(changed)
            self.assertIn("  - /data/one.yml", content)
            self.assertIn("  - /data/two.yml", content)

    def test_no_change_when_registration_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text(
                "server_name: example.com\n"
                "app_service_config_files:\n"
                "  - /data/test.yml\n"
            )

            changed = synapse_appservice.ensure_appservice_registration(path, "/data/test.yml")
            self.assertFalse(changed)

    def test_remove_registration_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text(
                "server_name: example.com\n"
                "app_service_config_files:\n"
                "  - /data/one.yml\n"
                "  - /data/test.yml\n"
            )

            changed = synapse_appservice.remove_appservice_registration(path, "/data/test.yml")
            content = path.read_text()

            self.assertTrue(changed)
            self.assertIn("  - /data/one.yml", content)
            self.assertNotIn("  - /data/test.yml", content)

    def test_remove_registration_no_change_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text(
                "server_name: example.com\n"
                "app_service_config_files:\n"
                "  - /data/one.yml\n"
            )

            changed = synapse_appservice.remove_appservice_registration(path, "/data/test.yml")
            self.assertFalse(changed)

    def test_remove_registration_drops_header_when_last_item_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text(
                "server_name: example.com\n"
                "app_service_config_files:\n"
                "  - /data/test.yml\n"
            )

            changed = synapse_appservice.remove_appservice_registration(path, "/data/test.yml")
            content = path.read_text()

            self.assertTrue(changed)
            self.assertNotIn("app_service_config_files:", content)


if __name__ == "__main__":
    unittest.main()
