#!/usr/bin/env python3
"""Tests for backup payload planning helpers."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.backup_payload import (
    resolve_backup_plan,
    resolve_database_dumps,
    resolve_persistent_paths,
    resolve_server_implementation,
    write_manifest,
    load_manifest,
    normalize_database_dumps,
)


class BackupPayloadTests(unittest.TestCase):
    def test_synapse_plan_includes_synapse_dump(self):
        deploy = {
            "matrix": {"server_implementation": "synapse"},
            "modules": {},
        }
        dumps = resolve_database_dumps(deploy)
        self.assertEqual(len(dumps), 1)
        self.assertEqual(dumps[0]["name"], "synapse")

    def test_tuwunel_plan_includes_tuwunel_data_path(self):
        deploy = {"matrix": {"server_implementation": "tuwunel"}}
        paths = resolve_persistent_paths(deploy)
        self.assertIn("modules/core/tuwunel_data", paths)
        self.assertEqual(resolve_database_dumps(deploy), [])

    def test_bridge_dumps_when_modules_enabled(self):
        deploy = {
            "matrix": {"server_implementation": "synapse"},
            "modules": {
                "whatsapp_bridge": {"enabled": True},
                "slack_bridge": {"enabled": True, "db_name": "custom_slack"},
            },
        }
        dumps = resolve_database_dumps(deploy)
        names = {item["name"] for item in dumps}
        self.assertEqual(names, {"synapse", "mautrix_whatsapp", "custom_slack"})

    def test_manifest_v2_and_restore_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy.yaml").write_text(
                "matrix:\n  server_implementation: synapse\nmodules:\n  whatsapp_bridge:\n    enabled: true\n"
            )
            (root / "VERSION").write_text("2.0.0\n")
            manifest_path = root / "manifest.json"
            write_manifest(
                manifest_path=manifest_path,
                project_root=root,
                repository_path="/var/backups/med-kit",
                encrypted=True,
            )
            manifest = load_manifest(manifest_path)
            self.assertEqual(manifest["format"], 2)
            self.assertTrue(manifest["encrypted"])
            dumps = normalize_database_dumps(manifest)
            self.assertTrue(any(item["name"] == "synapse" for item in dumps))
            self.assertTrue(any(item["db_user"] == "mautrix_whatsapp" for item in dumps if item["name"] == "mautrix_whatsapp"))

    def test_format1_manifest_fallback(self):
        legacy = {"format": 1, "database_dumps": ["database/synapse.dump"]}
        dumps = normalize_database_dumps(legacy)
        self.assertEqual(dumps[0]["path"], "database/synapse.dump")
        self.assertEqual(dumps[0]["db_user"], "synapse")

    def test_emit_plan_json_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deploy_yaml = root / "deploy.yaml"
            deploy_yaml.write_text("matrix:\n  server_implementation: synapse\n")
            plan = resolve_backup_plan(deploy_yaml)
            self.assertEqual(plan["server_implementation"], "synapse")
            self.assertIn("deploy.yaml", plan["persistent_paths"])


if __name__ == "__main__":
    unittest.main()
