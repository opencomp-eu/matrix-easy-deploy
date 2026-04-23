import tempfile
import unittest
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

        (self.root / "caddy/Caddyfile.template").write_text("{{MATRIX_DOMAIN}} {{CADDY_MATRIX_HOSTS}}")
        (self.root / "caddy/Caddyfile-no-element.template").write_text("no-element {{MATRIX_DOMAIN}}")
        (self.root / "modules/core/synapse/homeserver.yaml.template").write_text("server_name: {{SERVER_NAME}}\npublic_baseurl: https://{{MATRIX_DOMAIN}}")
        (self.root / "modules/core/element/config.json.template").write_text('{"base_url":"https://{{MATRIX_DOMAIN}}"}')
        (self.root / "modules/calls/coturn/turnserver.conf.template").write_text("realm={{MATRIX_DOMAIN}}")
        (self.root / "modules/calls/livekit/livekit.yaml.template").write_text("keys:\n  {{LIVEKIT_KEY}}: {{LIVEKIT_SECRET}}")

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

    def test_secrets_are_idempotent(self):
        ctx = apply.ApplyContext(self.root)
        first = apply.create_or_update_secrets(ctx, {})
        second = apply.create_or_update_secrets(ctx, first)

        for key in apply.DEFAULT_SECRET_KEYS + ["LIVEKIT_KEY"]:
            self.assertIn(key, second)
            self.assertEqual(first[key], second[key])

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


if __name__ == "__main__":
    unittest.main()
