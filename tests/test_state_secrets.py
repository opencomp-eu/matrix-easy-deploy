import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import state_secrets


class StateSecretsTests(unittest.TestCase):
    def test_get_returns_error_when_key_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "secrets.yaml"
            rc = state_secrets.main([
                "--secrets-file",
                str(secrets_file),
                "--get",
                "WA_DB_PASSWORD",
            ])
            self.assertEqual(rc, 1)

    def test_set_then_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_file = Path(tmp) / "secrets.yaml"

            rc_set = state_secrets.main([
                "--secrets-file",
                str(secrets_file),
                "--set",
                "WA_DB_PASSWORD=secret123",
            ])
            self.assertEqual(rc_set, 0)

            data = yaml.safe_load(secrets_file.read_text())
            self.assertEqual(data["WA_DB_PASSWORD"], "secret123")

            rc_get = state_secrets.main([
                "--secrets-file",
                str(secrets_file),
                "--get",
                "WA_DB_PASSWORD",
            ])
            self.assertEqual(rc_get, 0)


if __name__ == "__main__":
    unittest.main()
