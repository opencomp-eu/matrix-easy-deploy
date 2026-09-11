#!/usr/bin/env python3
"""Tests for backup payload planning helpers."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "easydeploy-lib" / "python"))
from backup_plan import load_manifest, load_plan, normalize_database_dumps, write_manifest  # noqa: E402
from scripts.backup_plan import (
    resolve_databases,
    resolve_plan,
    resolve_persistent_paths,
    resolve_server_implementation,
)


class BackupPayloadTests(unittest.TestCase):
    def test_synapse_plan_includes_synapse_dump(self):
        deploy = {
            "matrix": {"server_implementation": "synapse"},
            "modules": {},
        }
        dumps = resolve_databases(deploy)
        self.assertEqual(len(dumps), 1)
        self.assertEqual(dumps[0]["name"], "synapse")

    def test_tuwunel_plan_includes_tuwunel_data_path(self):
        deploy = {"matrix": {"server_implementation": "tuwunel"}}
        paths = resolve_persistent_paths(deploy)
        self.assertIn("modules/core/tuwunel_data", [item["path"] for item in paths])
        self.assertEqual(resolve_databases(deploy), [])

    def test_bridge_dumps_when_modules_enabled(self):
        deploy = {
            "matrix": {"server_implementation": "synapse"},
            "modules": {
                "whatsapp_bridge": {"enabled": True},
                "slack_bridge": {"enabled": True, "db_name": "custom_slack"},
            },
        }
        dumps = resolve_databases(deploy)
        names = {item["name"] for item in dumps}
        self.assertEqual(names, {"synapse", "mautrix_whatsapp", "custom_slack"})

    def test_manifest_v2_and_restore_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deploy.yaml").write_text(
                "matrix:\n  server_implementation: synapse\nmodules:\n  whatsapp_bridge:\n    enabled: true\n"
            )
            (root / "VERSION").write_text("2.0.0\n")
            (root / "scripts").mkdir()
            source_plan = Path(__file__).resolve().parents[1] / "scripts" / "backup_plan.py"
            (root / "scripts" / "backup_plan.py").write_text(source_plan.read_text())
            manifest_path = root / "manifest.json"
            plan = load_plan(root)
            write_manifest(
                manifest_path=manifest_path,
                project_root=root,
                plan=plan,
                repository_path="/var/backups/med-kit",
                encrypted=True,
            )
            manifest = load_manifest(manifest_path)
            self.assertEqual(manifest["format"], 3)
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
            plan = resolve_plan(root)
            self.assertEqual(resolve_server_implementation({"matrix": {"server_implementation": "synapse"}}), "synapse")
            self.assertIn("deploy.yaml", [item["path"] for item in plan["persistent_paths"]])


if __name__ == "__main__":
    unittest.main()
