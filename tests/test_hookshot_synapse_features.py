import tempfile
import unittest
from pathlib import Path

from scripts import hookshot_synapse_features


class HookshotSynapseFeaturesTests(unittest.TestCase):
    def test_adds_block_when_missing(self):
        original = "server_name: example.com\n"
        updated = hookshot_synapse_features.ensure_experimental_features_block(original)

        self.assertIn("experimental_features:", updated)
        self.assertIn("  msc2409_to_device_messages_enabled: true", updated)
        self.assertIn("  msc3202_device_masquerading: true", updated)
        self.assertIn("  msc3202_transaction_extensions: true", updated)

    def test_updates_existing_flags_and_keeps_other_keys(self):
        original = (
            "experimental_features:\n"
            "  msc3202_device_masquerading: false\n"
            "  custom_key: keepme\n"
            "server_name: example.com\n"
        )
        updated = hookshot_synapse_features.ensure_experimental_features_block(original)

        self.assertIn("  msc3202_device_masquerading: true", updated)
        self.assertIn("  custom_key: keepme", updated)
        self.assertIn("  msc2409_to_device_messages_enabled: true", updated)

    def test_main_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "homeserver.yaml"
            path.write_text("server_name: example.com\n")

            rc = hookshot_synapse_features.main([
                "--homeserver-yaml",
                str(path),
            ])

            self.assertEqual(rc, 0)
            content = path.read_text()
            self.assertIn("experimental_features:", content)
            self.assertIn("msc2409_to_device_messages_enabled", content)


if __name__ == "__main__":
    unittest.main()