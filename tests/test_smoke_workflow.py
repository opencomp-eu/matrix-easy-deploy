import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import apply


class SmokeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        (self.root / "caddy").mkdir(parents=True)
        (self.root / "modules/core/synapse").mkdir(parents=True)
        (self.root / "modules/core/element").mkdir(parents=True)
        (self.root / "modules/core/synapse_data").mkdir(parents=True)
        (self.root / "modules/calls/coturn").mkdir(parents=True)
        (self.root / "modules/calls/livekit").mkdir(parents=True)
        (self.root / "modules/hookshot").mkdir(parents=True)

        (self.root / "caddy/Caddyfile.template").write_text("{{MATRIX_DOMAIN}} {{CADDY_MATRIX_HOSTS}}\n")
        (self.root / "modules/core/synapse/homeserver.yaml.template").write_text(
            "server_name: {{SERVER_NAME}}\npublic_baseurl: https://{{MATRIX_DOMAIN}}\n"
        )
        (self.root / "modules/core/element/config.json.template").write_text('{"base_url":"https://{{MATRIX_DOMAIN}}"}\n')
        (self.root / "modules/calls/coturn/turnserver.conf.template").write_text("realm={{MATRIX_DOMAIN}}\n")
        (self.root / "modules/calls/livekit/livekit.yaml.template").write_text("keys:\n  {{LIVEKIT_KEY}}: {{LIVEKIT_SECRET}}\n")

        (self.root / "modules/hookshot/module.yaml").write_text(
            "name: hookshot\n"
            "config_key: hookshot\n"
            "generated_files:\n"
            "  - modules/hookshot/hookshot/config.yml\n"
            "  - modules/hookshot/hookshot/registration.yml\n"
            "runtime:\n"
            "  config_exists: modules/hookshot/hookshot/config.yml\n"
        )
        (self.root / "modules/hookshot/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_config(self, modules=None):
        modules_cfg = {
            "hookshot": {"enabled": False, "domain": "hookshot.example.com"},
            "whatsapp_bridge": {"enabled": False},
            "slack_bridge": {"enabled": False},
        }
        if modules:
            modules_cfg.update(modules)

        cfg = {
            "matrix": {
                "domain": "matrix.example.com",
                "server_name": "example.com",
                "admin_username": "admin",
            },
            "features": {
                "registration_enabled": False,
                "federation_enabled": True,
                "element": {"enabled": True, "domain": "element.example.com"},
                "calls": {"enabled": True, "livekit_domain": "livekit.example.com"},
                "sso": {"enabled": False, "providers": []},
            },
            "modules": modules_cfg,
        }
        (self.root / "deploy.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    def _env_map(self):
        return apply.load_env_map(self.root / ".env")

    def test_smoke_first_and_second_apply_idempotent(self):
        self._write_config()
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.9.9.9")
        first_env = self._env_map()
        first_secrets = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())
        first_element = json.loads((self.root / "modules/core/element/config.json").read_text())

        apply.apply_configuration(ctx, server_ip="9.9.9.9")
        second_env = self._env_map()
        second_secrets = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())
        second_element = json.loads((self.root / "modules/core/element/config.json").read_text())

        self.assertEqual(first_secrets, second_secrets)
        self.assertEqual(first_element, second_element)
        self.assertEqual(first_env.get("MATRIX_DOMAIN"), "matrix.example.com")
        self.assertEqual(first_env.get("SERVER_IP"), "9.9.9.9")
        self.assertEqual(second_env.get("MATRIX_DOMAIN"), "matrix.example.com")
        self.assertEqual(second_env.get("SERVER_IP"), "9.9.9.9")

    def test_smoke_runtime_reconcile_runs_stop_start(self):
        ctx = apply.ApplyContext(self.root)
        with patch("scripts.apply.subprocess.run") as mock_run:
            apply.run_runtime_reconcile(ctx)

        self.assertEqual(mock_run.call_count, 2)
        first = mock_run.call_args_list[0].args[0]
        second = mock_run.call_args_list[1].args[0]
        self.assertTrue(str(first[1]).endswith("stop.sh"))
        self.assertTrue(str(second[1]).endswith("start.sh"))

    def test_smoke_repeated_module_reapply(self):
        self._write_config(modules={"hookshot": {"enabled": True, "domain": "hookshot.example.com"}})
        ctx = apply.ApplyContext(self.root)

        def _mock_setup(*args, **kwargs):
            hookshot_dir = self.root / "modules/hookshot/hookshot"
            hookshot_dir.mkdir(parents=True, exist_ok=True)
            (hookshot_dir / "config.yml").write_text("ok\n")
            (hookshot_dir / "registration.yml").write_text("ok\n")

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.apply_configuration(ctx, server_ip="9.9.9.9")
            apply.apply_configuration(ctx, server_ip="9.9.9.9")

        self.assertEqual(mock_run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
