import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

from scripts import apply


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Minimal project structure expected by apply.py
        (self.root / "caddy").mkdir(parents=True)
        (self.root / "modules/core/synapse").mkdir(parents=True)
        (self.root / "modules/core/element").mkdir(parents=True)
        (self.root / "modules/calls/coturn").mkdir(parents=True)
        (self.root / "modules/calls/livekit").mkdir(parents=True)
        (self.root / "modules/hookshot").mkdir(parents=True)

        (self.root / "caddy/Caddyfile.template").write_text("{{MATRIX_DOMAIN}} {{CADDY_MATRIX_HOSTS}}")
        (self.root / "caddy/Caddyfile-no-element.template").write_text("no-element {{MATRIX_DOMAIN}}")
        (self.root / "modules/core/synapse/homeserver.yaml.template").write_text("server_name: {{SERVER_NAME}}\npublic_baseurl: https://{{MATRIX_DOMAIN}}")
        (self.root / "modules/core/element/config.json.template").write_text('{"base_url":"https://{{MATRIX_DOMAIN}}"}')
        (self.root / "modules/calls/coturn/turnserver.conf.template").write_text("realm={{MATRIX_DOMAIN}}")
        (self.root / "modules/calls/livekit/livekit.yaml.template").write_text("keys:\n  {{LIVEKIT_KEY}}: {{LIVEKIT_SECRET}}")
        (self.root / "modules/hookshot/module.yaml").write_text(
            "name: hookshot\n"
            "config_key: hookshot\n"
            "runtime:\n"
            "  config_exists: modules/hookshot/hookshot/config.yml\n"
        )
        (self.root / "modules/hookshot/setup.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def sample_config(self):
        return {
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
            "modules": {
                "hookshot": {"enabled": False, "domain": "hookshot.example.com"},
                "whatsapp_bridge": {"enabled": False},
                "slack_bridge": {"enabled": False},
            },
        }

    def write_config(self, cfg):
        (self.root / "deploy.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    def test_derive_values_federation_and_modules(self):
        cfg = self.sample_config()
        cfg["features"]["federation_enabled"] = False
        cfg["modules"]["hookshot"]["enabled"] = True

        derived = apply.derive_values(cfg, server_ip="1.2.3.4")

        self.assertEqual(derived["FEDERATION_WHITELIST"], "[]")
        self.assertEqual(derived["ALLOW_PUBLIC_ROOMS_FEDERATION"], "false")
        self.assertEqual(derived["SERVER_IP"], "1.2.3.4")
        self.assertEqual(derived["HOOKSHOT_ENABLED"], "true")
        self.assertEqual(derived["WHATSAPP_BRIDGE_ENABLED"], "false")

    def test_derive_values_with_oidc_providers(self):
        cfg = self.sample_config()
        cfg["features"]["sso"] = {
            "enabled": True,
            "providers": [
                {
                    "name": "Google",
                    "issuer": "https://accounts.google.com/",
                    "client_id": "id-1",
                    "client_secret": "secret-1",
                    "allow_registration": True,
                }
            ],
        }

        derived = apply.derive_values(cfg, server_ip="1.2.3.4")

        self.assertEqual(derived["ENABLE_SSO"], "true")
        self.assertEqual(derived["OIDC_PROVIDER_COUNT"], "1")
        self.assertEqual(derived["OIDC_PROVIDER_NAMES"], "Google")
        self.assertIn('"idp_name":"Google"', derived["OIDC_PROVIDERS_JSON"])

    def test_validate_config_rejects_invalid_modules_shape(self):
        cfg = self.sample_config()
        cfg["modules"] = []
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_enabled_type(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = "yes"
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_secrets_are_idempotent(self):
        ctx = apply.ApplyContext(self.root)
        first = apply.create_or_update_secrets(ctx, {})
        second = apply.create_or_update_secrets(ctx, first)

        for key in apply.DEFAULT_SECRET_KEYS + ["LIVEKIT_KEY"]:
            self.assertIn(key, second)
            self.assertEqual(first[key], second[key])

    def test_rotate_secrets_changes_core_values(self):
        ctx = apply.ApplyContext(self.root)
        first = apply.create_or_update_secrets(ctx, {})
        second = apply.create_or_update_secrets(ctx, first, rotate=True)

        changed = [k for k in apply.DEFAULT_SECRET_KEYS if first[k] != second[k]]
        self.assertTrue(changed)
        self.assertEqual(second["LIVEKIT_KEY"], "matrix")

    def test_apply_configuration_writes_env_and_templates(self):
        self.write_config(self.sample_config())
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        env_text = (self.root / ".env").read_text()
        self.assertIn("MATRIX_DOMAIN=matrix.example.com", env_text)
        self.assertIn("SERVER_IP=9.8.7.6", env_text)
        self.assertIn("HOOKSHOT_ENABLED=false", env_text)

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertIn("matrix.example.com", caddy)
        self.assertNotIn("{{", caddy)

        synapse = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("server_name: example.com", synapse)
        self.assertNotIn("{{", synapse)

        modules_state = yaml.safe_load((self.root / ".matrix-easy-deploy/modules.yaml").read_text())
        self.assertIn("hookshot", modules_state)
        self.assertFalse(modules_state["hookshot"]["enabled"])

    def test_apply_reuses_existing_secrets(self):
        self.write_config(self.sample_config())
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")
        saved_1 = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())

        apply.apply_configuration(ctx, server_ip="9.8.7.6")
        saved_2 = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())

        self.assertEqual(saved_1, saved_2)

    def test_run_runtime_reconcile_invokes_stop_then_start(self):
        ctx = apply.ApplyContext(self.root)
        with patch("scripts.apply.subprocess.run") as mock_run:
            apply.run_runtime_reconcile(ctx)

        self.assertEqual(mock_run.call_count, 2)
        first_args = mock_run.call_args_list[0].args[0]
        second_args = mock_run.call_args_list[1].args[0]
        self.assertTrue(str(first_args[1]).endswith("stop.sh"))
        self.assertTrue(str(second_args[1]).endswith("start.sh"))

    def test_module_bootstrap_invoked_when_enabled_and_missing_config(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True
        cfg["modules"]["hookshot"]["domain"] = "hookshot.example.com"

        ctx = apply.ApplyContext(self.root)
        def _mock_setup(*args, **kwargs):
            hookshot_dir = self.root / "modules/hookshot/hookshot"
            hookshot_dir.mkdir(parents=True, exist_ok=True)
            (hookshot_dir / "config.yml").write_text("ok\n")
            (hookshot_dir / "registration.yml").write_text("ok\n")

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 1)
        call = mock_run.call_args
        cmd = call.args[0]
        env = call.kwargs["env"]
        self.assertTrue(str(cmd[1]).endswith("modules/hookshot/setup.sh"))
        self.assertEqual(env.get("MED_NON_INTERACTIVE"), "1")
        self.assertEqual(env.get("MODULE_HOOKSHOT_DOMAIN"), "hookshot.example.com")

    def test_module_bootstrap_skips_when_config_exists(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True)
        (self.root / "modules/hookshot/hookshot/config.yml").write_text("ok\n")

        ctx = apply.ApplyContext(self.root)
        with patch("scripts.apply.subprocess.run") as mock_run:
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 0)

    def test_module_bootstrap_checks_generated_files_not_only_config_exists(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/module.yaml").write_text(
            "name: hookshot\n"
            "config_key: hookshot\n"
            "generated_files:\n"
            "  - modules/hookshot/hookshot/config.yml\n"
            "  - modules/hookshot/hookshot/registration.yml\n"
            "runtime:\n"
            "  config_exists: modules/hookshot/hookshot/config.yml\n"
        )

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True)
        (self.root / "modules/hookshot/hookshot/config.yml").write_text("ok\n")

        ctx = apply.ApplyContext(self.root)
        def _mock_setup(*args, **kwargs):
            hookshot_dir = self.root / "modules/hookshot/hookshot"
            hookshot_dir.mkdir(parents=True, exist_ok=True)
            (hookshot_dir / "registration.yml").write_text("ok\n")

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 1)

    def test_module_bootstrap_raises_when_setup_missing_and_files_missing(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/setup.sh").unlink()

        ctx = apply.ApplyContext(self.root)
        with self.assertRaises(RuntimeError):
            apply.reconcile_module_bootstrap(ctx, cfg)


if __name__ == "__main__":
    unittest.main()
