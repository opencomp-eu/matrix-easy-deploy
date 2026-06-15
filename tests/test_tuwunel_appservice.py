"""Tests for scripts/tuwunel_appservice.py."""

from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

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

    def test_sync_removes_dest_when_source_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            dest = appservice_dir / "registration.yaml"
            appservice_dir.mkdir(parents=True)
            dest.write_text("stale\n")
            missing_src = root / "missing.yaml"

            changed = tuwunel_appservice.sync_appservice_registration(
                appservice_dir, missing_src, "registration.yaml"
            )

            self.assertTrue(changed)
            self.assertFalse(dest.exists())

    def test_sync_no_change_when_source_and_dest_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            missing_src = root / "missing.yaml"

            changed = tuwunel_appservice.sync_appservice_registration(
                appservice_dir, missing_src, "registration.yaml"
            )

            self.assertFalse(changed)
            self.assertFalse((appservice_dir / "registration.yaml").exists())

    def test_sync_updates_dest_when_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            src = root / "registration.yaml"
            dest = appservice_dir / "registration.yaml"
            appservice_dir.mkdir(parents=True)
            src.write_text("id: new\n")
            dest.write_text("id: old\n")

            changed = tuwunel_appservice.sync_appservice_registration(
                appservice_dir, src, "registration.yaml"
            )

            self.assertTrue(changed)
            self.assertEqual(dest.read_text(), "id: new\n")

    def test_remove_registration_no_change_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            appservice_dir.mkdir(parents=True)

            changed = tuwunel_appservice.remove_appservice_registration(
                appservice_dir, "registration.yaml"
            )

            self.assertFalse(changed)

    def test_main_sync_prints_when_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            src = root / "registration.yaml"
            src.write_text("id: test\n")

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                rc = tuwunel_appservice.main(
                    [
                        "--appservice-dir",
                        str(appservice_dir),
                        "--registration-src",
                        str(src),
                        "--registration-filename",
                        "registration.yaml",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertIn("Synced registration.yaml into appservice_dir.", stdout.getvalue())

    def test_main_sync_silent_when_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            src = root / "registration.yaml"
            src.write_text("id: test\n")
            appservice_dir.mkdir(parents=True)
            (appservice_dir / "registration.yaml").write_text("id: test\n")

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                rc = tuwunel_appservice.main(
                    [
                        "--appservice-dir",
                        str(appservice_dir),
                        "--registration-src",
                        str(src),
                        "--registration-filename",
                        "registration.yaml",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(stdout.getvalue(), "")

    def test_main_remove_prints_when_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            appservice_dir.mkdir(parents=True)
            (appservice_dir / "registration.yaml").write_text("id: test\n")

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                rc = tuwunel_appservice.main(
                    [
                        "--appservice-dir",
                        str(appservice_dir),
                        "--registration-src",
                        str(root / "unused.yaml"),
                        "--registration-filename",
                        "registration.yaml",
                        "--remove",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertIn("Removed registration.yaml from appservice_dir.", stdout.getvalue())

    def test_main_remove_silent_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appservice_dir = root / "appservices"
            appservice_dir.mkdir(parents=True)

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                rc = tuwunel_appservice.main(
                    [
                        "--appservice-dir",
                        str(appservice_dir),
                        "--registration-src",
                        str(root / "unused.yaml"),
                        "--registration-filename",
                        "registration.yaml",
                        "--remove",
                    ]
                )

            self.assertEqual(rc, 0)
            self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
