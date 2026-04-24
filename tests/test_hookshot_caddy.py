import tempfile
import unittest
from pathlib import Path

from scripts import hookshot_caddy


class HookshotCaddyTests(unittest.TestCase):
    def test_upsert_adds_managed_block_when_missing(self):
        original = "matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n"
        updated = hookshot_caddy.upsert_hookshot_block(original, "hookshot.example.com")

        self.assertIn(hookshot_caddy.BEGIN_MARKER, updated)
        self.assertIn("hookshot.example.com {", updated)
        self.assertIn("reverse_proxy matrix-hookshot:9000", updated)
        self.assertEqual(updated.count(hookshot_caddy.BEGIN_MARKER), 1)

    def test_upsert_replaces_existing_managed_block(self):
        original = (
            "matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n\n"
            f"{hookshot_caddy.BEGIN_MARKER}\n"
            "old.example.com {\n    reverse_proxy matrix-hookshot:9000\n}\n"
            f"{hookshot_caddy.END_MARKER}\n"
        )

        updated = hookshot_caddy.upsert_hookshot_block(original, "hookshot.example.com")
        self.assertNotIn("old.example.com {", updated)
        self.assertIn("hookshot.example.com {", updated)
        self.assertEqual(updated.count(hookshot_caddy.BEGIN_MARKER), 1)
        self.assertEqual(updated.count(hookshot_caddy.END_MARKER), 1)

    def test_upsert_removes_legacy_hookshot_block_for_same_domain(self):
        original = (
            "hookshot.example.com {\n"
            "    reverse_proxy matrix-hookshot:9000\n"
            "}\n"
            "\n"
            "matrix.example.com {\n"
            "    reverse_proxy matrix_synapse:8008\n"
            "}\n"
        )

        updated = hookshot_caddy.upsert_hookshot_block(original, "hookshot.example.com")
        self.assertEqual(updated.count("hookshot.example.com {"), 1)
        self.assertIn(hookshot_caddy.BEGIN_MARKER, updated)

    def test_main_writes_updated_caddyfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            caddyfile = Path(tmp) / "Caddyfile"
            caddyfile.write_text("matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n")

            rc = hookshot_caddy.main([
                "--caddyfile",
                str(caddyfile),
                "--domain",
                "hookshot.example.com",
            ])

            self.assertEqual(rc, 0)
            content = caddyfile.read_text()
            self.assertIn("hookshot.example.com {", content)
            self.assertEqual(content.count(hookshot_caddy.BEGIN_MARKER), 1)


if __name__ == "__main__":
    unittest.main()