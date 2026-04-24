import tempfile
import unittest
from pathlib import Path

from scripts import bridge_config_patch


class BridgeConfigPatchTests(unittest.TestCase):
    def test_patch_updates_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "homeserver:\n"
                "  domain: \"old.example.com\"\n"
                "  address: \"http://old:8008\"\n"
                "appservice:\n"
                "  address: \"http://old:29318\"\n"
                "  hostname: \"127.0.0.1\"\n"
                "database:\n"
                "  type: \"sqlite\"\n"
                "  uri: \"sqlite:///tmp.db\"\n"
                "permissions:\n"
                "  \"old.example.com\": admin\n"
            )

            bridge_config_patch.patch_bridge_config(
                config_path=config_path,
                server_name="example.com",
                hs_address="http://matrix_synapse:8008",
                as_address="http://mautrix:29318",
                db_type="postgres",
                db_uri="postgres://user:pass@matrix_postgres/db",
                admin_user="admin",
            )

            content = config_path.read_text()
            self.assertIn('domain: "example.com"', content)
            self.assertIn('address: "http://matrix_synapse:8008"', content)
            self.assertIn('address: "http://mautrix:29318"', content)
            self.assertIn('hostname: "0.0.0.0"', content)
            self.assertIn('type: "postgres"', content)
            self.assertIn('uri: "postgres://user:pass@matrix_postgres/db"', content)
            self.assertIn('"@admin:example.com": admin', content)

    def test_patch_injects_permissions_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text(
                "bridge:\n"
                "  relay: false\n"
                "homeserver:\n"
                "  domain: \"old\"\n"
            )

            bridge_config_patch.patch_bridge_config(
                config_path=config_path,
                server_name="example.com",
                hs_address="http://matrix_synapse:8008",
                as_address="http://mautrix:29318",
                db_type="postgres",
                db_uri="postgres://u:p@db/db",
                admin_user="operator",
            )

            content = config_path.read_text()
            self.assertIn("permissions:", content)
            self.assertIn('"example.com": user', content)
            self.assertIn('"@operator:example.com": admin', content)


if __name__ == "__main__":
    unittest.main()
