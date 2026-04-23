import tempfile
import unittest
from pathlib import Path

from scripts import env_upsert


class EnvUpsertTests(unittest.TestCase):
    def test_upsert_updates_existing_and_appends_new(self):
        original = (
            "# Header\n"
            "A=1\n"
            "B=2\n"
        )
        updated = env_upsert.upsert_env_text(original, {"B": "20", "C": "30"})

        self.assertIn("A=1\n", updated)
        self.assertIn("B=20\n", updated)
        self.assertIn("C=30\n", updated)
        self.assertNotIn("B=2\n", updated)

    def test_upsert_deduplicates_updated_keys(self):
        original = (
            "A=1\n"
            "A=2\n"
            "B=3\n"
        )
        updated = env_upsert.upsert_env_text(original, {"A": "9"})
        self.assertEqual(updated.count("A="), 1)
        self.assertIn("A=9\n", updated)

    def test_main_writes_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("X=1\n")

            rc = env_upsert.main([
                "--env-file",
                str(env_file),
                "--set",
                "X=2",
                "--set",
                "Y=3",
            ])

            self.assertEqual(rc, 0)
            content = env_file.read_text()
            self.assertIn("X=2\n", content)
            self.assertIn("Y=3\n", content)


if __name__ == "__main__":
    unittest.main()
