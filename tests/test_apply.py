import json
import os
import re
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch, Mock
from pathlib import Path

import yaml

from scripts import apply, mas_config
from tests.helpers.project_tree import (
    build_minimal_project,
    default_deploy_config,
    write_deploy_config,
)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_minimal_project(self.root, preset="full")
        self._prev_skip_bootstrap = os.environ.get("MED_SKIP_EXTERNAL_BOOTSTRAP")
        os.environ["MED_SKIP_EXTERNAL_BOOTSTRAP"] = "1"
        self._prev_mas_docker_generate = os.environ.get("MED_MAS_USE_DOCKER_GENERATE")
        os.environ["MED_MAS_USE_DOCKER_GENERATE"] = "0"

    def tearDown(self):
        if self._prev_mas_docker_generate is None:
            os.environ.pop("MED_MAS_USE_DOCKER_GENERATE", None)
        else:
            os.environ["MED_MAS_USE_DOCKER_GENERATE"] = self._prev_mas_docker_generate
        if self._prev_skip_bootstrap is None:
            os.environ.pop("MED_SKIP_EXTERNAL_BOOTSTRAP", None)
        else:
            os.environ["MED_SKIP_EXTERNAL_BOOTSTRAP"] = self._prev_skip_bootstrap
        self.tmp.cleanup()

    def sample_config(self):
        return default_deploy_config()

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

    def test_derive_values_caddy_matrix_hosts_space_after_comma(self):
        derived = apply.derive_values(self.sample_config(), server_ip="1.2.3.4")
        self.assertEqual(derived["CADDY_MATRIX_HOSTS"], "matrix.example.com, example.com")

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

        self.assertEqual(derived["MAS_ENABLED"], "true")
        self.assertEqual(derived["ENABLE_SSO"], "true")
        self.assertEqual(derived["OIDC_PROVIDER_COUNT"], "1")
        self.assertEqual(derived["OIDC_PROVIDER_NAMES"], "Google")
        self.assertEqual(derived["MAS_UPSTREAM_PROVIDER_COUNT"], "1")
        self.assertEqual(derived["MAS_UPSTREAM_PROVIDER_NAMES"], "Google")
        self.assertEqual(derived["LOCAL_LOGIN_ENABLED"], "false")
        self.assertIn("msc3861:", derived["SYNAPSE_MAS_EXPERIMENTAL_SECTION"])

    def test_derive_values_sso_only(self):
        cfg = self.sample_config()
        cfg["features"]["local_login_enabled"] = False
        cfg["features"]["sso"] = {
            "enabled": True,
            "providers": [
                {
                    "name": "Google",
                    "issuer": "https://accounts.google.com/",
                    "client_id": "a",
                    "client_secret": "b",
                }
            ],
        }

        derived = apply.derive_values(cfg, server_ip="1.2.3.4")

        self.assertEqual(derived["MAS_LOCAL_LOGIN_ENABLED"], "false")
        self.assertEqual(derived["LOCAL_LOGIN_ENABLED"], "false")

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

    def test_validate_config_rejects_invalid_local_login_enabled_type(self):
        cfg = self.sample_config()
        cfg["features"]["local_login_enabled"] = "no"
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_element_footer_links(self):
        cfg = self.sample_config()
        cfg["features"]["element"]["branding"] = {
            "auth_footer_links": [{"text": "FAQ"}],
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_element_labs_features(self):
        cfg = self.sample_config()
        cfg["features"]["element"]["labs"] = {
            "features": {"feature_video_rooms": "yes"},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_element_extra_config_shape(self):
        cfg = self.sample_config()
        cfg["features"]["element"]["extra_config"] = ["bad"]
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_sso_only_without_providers(self):
        cfg = self.sample_config()
        cfg["features"]["local_login_enabled"] = False
        cfg["features"]["sso"] = {"enabled": True, "providers": []}
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_sso_on_tuwunel(self):
        cfg = self.sample_config()
        cfg["matrix"]["server_implementation"] = "tuwunel"
        cfg["features"]["sso"] = {
            "enabled": True,
            "providers": [
                {
                    "name": "Google",
                    "issuer": "https://accounts.google.com/",
                    "client_id": "a",
                    "client_secret": "b",
                }
            ],
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_backup_path_when_relative(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "local", "path": "relative/path"},
            "retention": {"keep_daily": 7},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_backup_type_when_not_local(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "ssh", "path": "/var/backups/med-kit"},
            "retention": {"keep_daily": 7},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_negative_backup_retention(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": False,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "retention": {"keep_daily": -1},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_empty_backup_schedule_calendar_when_enabled(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "schedule": {"enabled": True, "calendar": ""},
            "retention": {"keep_daily": 7},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_backup_schedule_persistent_type(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": False,
            "schedule": {"enabled": False, "persistent": "yes"},
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_enabled_schedule_when_backup_disabled(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": False,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "schedule": {"enabled": True, "calendar": "daily", "persistent": True},
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_auto_join_rooms(self):
        cfg = self.sample_config()
        cfg["features"]["auto_join"] = {"rooms": "not-a-list"}
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_rejects_invalid_auto_join_room_object(self):
        cfg = self.sample_config()
        cfg["features"]["auto_join"] = {
            "rooms": [{"topic": "Missing alias"}],
        }
        with self.assertRaises(ValueError):
            apply.validate_config(cfg)

    def test_validate_config_accepts_mixed_auto_join_room_entries(self):
        cfg = self.sample_config()
        cfg["features"]["auto_join"] = {
            "rooms": [
                "#welcome:example.com",
                {
                    "alias": "announce",
                    "name": "Announcements",
                    "topic": "Server news",
                    "message": "Welcome!",
                    "handover": ["alice"],
                    "federated": False,
                },
            ],
        }
        apply.validate_config(cfg)

    def test_build_synapse_auto_join_section_normalizes_object_aliases(self):
        section = apply.build_synapse_auto_join_section(
            {
                "rooms": [
                    {"alias": "welcome", "name": "Welcome"},
                    "#announce:example.com",
                ],
            },
            server_name="example.com",
        )
        self.assertIn("'#welcome:example.com'", section)
        self.assertIn("'#announce:example.com'", section)
        self.assertIn("autocreate_auto_join_rooms: false", section)

    def test_build_tuwunel_auto_join_section_emits_alias_list(self):
        section = apply.build_tuwunel_auto_join_section(
            {"rooms": [{"alias": "welcome"}]},
            server_name="example.com",
        )
        self.assertIn("auto_join_rooms = [", section)
        self.assertIn("'#welcome:example.com'", section)

    def test_parse_auto_join_room_entry_returns_spec_fields(self):
        spec = apply.parse_auto_join_room_entry(
            {
                "alias": "welcome",
                "name": "Welcome",
                "topic": "Intro",
                "message": "Hello",
                "handover": ["alice"],
                "federated": True,
            },
            "example.com",
        )
        self.assertEqual(spec["alias"], "#welcome:example.com")
        self.assertEqual(spec["name"], "Welcome")
        self.assertEqual(spec["topic"], "Intro")
        self.assertEqual(spec["message"], "Hello")
        self.assertEqual(spec["handover"], ["alice"])
        self.assertTrue(spec["federated"])

    def test_build_synapse_auto_join_section_empty(self):
        self.assertEqual(apply.build_synapse_auto_join_section({}), "")
        self.assertEqual(apply.build_synapse_auto_join_section({"rooms": []}), "")

    def test_build_synapse_auto_join_section_with_rooms(self):
        section = apply.build_synapse_auto_join_section(
            {
                "rooms": ["#welcome:example.com", "#announce:example.com"],
                "synapse": {"rooms_for_guests": False},
            },
            server_name="example.com",
        )
        self.assertIn("auto_join_rooms:", section)
        self.assertIn("'#welcome:example.com'", section)
        self.assertIn("'#announce:example.com'", section)
        self.assertIn("autocreate_auto_join_rooms: false", section)
        self.assertIn("auto_join_rooms_for_guests: false", section)
        self.assertNotIn("autocreate_auto_join_rooms_federated", section)

    def test_derive_values_auto_join_sections(self):
        cfg = self.sample_config()
        cfg["features"]["auto_join"] = {
            "rooms": ["#welcome:example.com"],
        }
        derived = apply.derive_values(cfg, server_ip="1.2.3.4")
        self.assertIn("auto_join_rooms:", derived["SYNAPSE_AUTO_JOIN_SECTION"])
        self.assertIn("auto_join_rooms = [", derived["TUWUNEL_AUTO_JOIN_SECTION"])
        self.assertIn("autocreate_auto_join_rooms: false", derived["SYNAPSE_AUTO_JOIN_SECTION"])

    def test_apply_configuration_renders_auto_join(self):
        cfg = self.sample_config()
        cfg["features"]["auto_join"] = {
            "rooms": ["#welcome:example.com"],
        }
        self.write_config(cfg)
        template = (self.root / "modules/core/synapse/homeserver.yaml.template").read_text()
        (self.root / "modules/core/synapse/homeserver.yaml.template").write_text(
            template + "\n{{SYNAPSE_AUTO_JOIN_SECTION}}\n"
        )
        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        synapse = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("auto_join_rooms:", synapse)
        self.assertIn("'#welcome:example.com'", synapse)
        self.assertIn("autocreate_auto_join_rooms: false", synapse)
        self.assertNotIn("{{SYNAPSE_AUTO_JOIN_SECTION}}", synapse)

    def test_apply_configuration_renders_auto_join_for_tuwunel(self):
        cfg = self.sample_config()
        cfg["matrix"]["server_implementation"] = "tuwunel"
        cfg["features"]["auto_join"] = {
            "rooms": ["#welcome:example.com"],
        }
        self.write_config(cfg)
        tuw_template = (self.root / "modules/core/tuwunel/tuwunel.toml.template").read_text()
        (self.root / "modules/core/tuwunel/tuwunel.toml.template").write_text(
            tuw_template + "\n{{TUWUNEL_AUTO_JOIN_SECTION}}\n"
        )
        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        tuwunel = (self.root / "modules/core/tuwunel/tuwunel.toml").read_text()
        self.assertIn("'#welcome:example.com'", tuwunel)
        self.assertNotIn("{{TUWUNEL_AUTO_JOIN_SECTION}}", tuwunel)

    def test_reconcile_auto_join_rooms_invokes_med_admin(self):
        cfg = self.sample_config()
        cfg["features"]["auto_join"] = {"rooms": ["#welcome:example.com"]}
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)
        (self.root / "scripts").mkdir(exist_ok=True)
        med_admin = self.root / "scripts/med-admin.sh"
        med_admin.write_text("#!/usr/bin/env bash\nexit 0\n")
        med_admin.chmod(0o755)

        with (
            patch("scripts.apply.wait_for_homeserver") as mock_wait,
            patch("scripts.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()
            apply.reconcile_auto_join_rooms(ctx, cfg)

        mock_wait.assert_called_once_with(ctx, after_restart=False)

        cmd = mock_run.call_args.args[0]
        self.assertIn("setup-auto-join-rooms", cmd)
        self.assertIn("--yes", cmd)

    def test_wait_for_homeserver_returns_when_versions_responds(self):
        ctx = apply.ApplyContext(self.root)
        ctx.env_file.write_text("MATRIX_DOMAIN=matrix.example.com\n")
        with patch("scripts.apply.urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.status = 200
            apply.wait_for_homeserver(ctx, after_restart=False)
        mock_urlopen.assert_called_once()

    def test_write_env_file_preserves_med_admin_credentials(self):
        ctx = apply.ApplyContext(self.root)
        ctx.env_file.write_text(
            "MED_ADMIN_USERNAME=med-admin\n"
            "MED_ADMIN_PASSWORD=secret123456789\n"
            "OLD_KEY=keep-me\n"
        )
        apply.write_env_file(ctx, {"MATRIX_DOMAIN": "matrix.example.com", "SERVER_NAME": "example.com"})
        env_text = ctx.env_file.read_text()
        self.assertIn("MED_ADMIN_USERNAME=med-admin", env_text)
        self.assertIn("MED_ADMIN_PASSWORD=secret123456789", env_text)
        self.assertNotIn("OLD_KEY=keep-me", env_text)

    def test_write_env_file_excludes_mas_signing_keys_and_templates(self):
        ctx = apply.ApplyContext(self.root)
        apply.write_env_file(
            ctx,
            {
                "MATRIX_DOMAIN": "matrix.example.com",
                "MAS_SIGNING_KEYS": [
                    {
                        "kid": "rsa1",
                        "key": (
                            "-----BEGIN RSA PRIVATE KEY-----\n"
                            "MIIE\n"
                            "-----END RSA PRIVATE KEY-----"
                        ),
                    }
                ],
                "MAS_SIGNING_KEYS_YAML": "secrets:\n  keys:\n    - kid: rsa1\n",
                "SYNAPSE_MAS_EXPERIMENTAL_SECTION": "experimental:\n  mas:\n",
            },
        )
        env_text = ctx.env_file.read_text()
        self.assertIn("MATRIX_DOMAIN=matrix.example.com", env_text)
        self.assertNotIn("MAS_SIGNING_KEYS=", env_text)
        self.assertNotIn("MAS_SIGNING_KEYS_YAML=", env_text)
        self.assertNotIn("SYNAPSE_MAS_EXPERIMENTAL_SECTION=", env_text)
        self.assertNotIn("BEGIN", env_text)

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
        self.assertIn("LOCAL_LOGIN_ENABLED=true", env_text)

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertIn("matrix.example.com, example.com {", caddy)
        self.assertIn("handle_path /livekit/jwt*", caddy)
        self.assertIn("reverse_proxy matrix_lk_jwt_service:8080", caddy)
        self.assertIn("handle_path /livekit/sfu*", caddy)
        self.assertNotIn("{{", caddy)

        synapse = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("server_name: example.com", synapse)
        self.assertIn("enabled: true", synapse)
        self.assertIn("extra_well_known_client_content:", synapse)
        self.assertIn("org.matrix.msc4143.rtc_foci:", synapse)
        self.assertIn("matrix_rtc:", synapse)
        self.assertIn('livekit_service_url: "https://livekit.example.com/livekit/jwt"', synapse)
        self.assertNotIn("\nlivekit:\n", synapse)
        self.assertNotIn("{{", synapse)

        livekit = (self.root / "modules/calls/livekit/livekit.yaml").read_text()
        self.assertIn("room:\n  auto_create: false", livekit)

        modules_state = yaml.safe_load((self.root / ".matrix-easy-deploy/modules.yaml").read_text())
        self.assertIn("hookshot", modules_state)
        self.assertFalse(modules_state["hookshot"]["enabled"])

        element = json.loads((self.root / "modules/core/element/config.json").read_text())
        self.assertEqual(element["brand"], "Element")
        self.assertEqual(element["default_server_config"]["m.homeserver"]["base_url"], "https://matrix.example.com")
        self.assertEqual(element["room_directory"]["servers"], ["example.com"])
        self.assertEqual(element["integrations_ui_url"], "https://scalar.vector.im/")
        self.assertIn("SERVER_IMPLEMENTATION=synapse", env_text)
        self.assertIn("HOMESERVER_UPSTREAM=matrix_synapse:8008", env_text)

    def test_apply_configuration_renders_tuwunel(self):
        cfg = self.sample_config()
        cfg["matrix"]["server_implementation"] = "tuwunel"
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        env_text = (self.root / ".env").read_text()
        self.assertIn("SERVER_IMPLEMENTATION=tuwunel", env_text)
        self.assertIn("HOMESERVER_UPSTREAM=matrix_tuwunel:8008", env_text)

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertIn("reverse_proxy matrix_tuwunel:8008", caddy)
        self.assertNotIn("/_synapse/", caddy)

        tuwunel = (self.root / "modules/core/tuwunel/tuwunel.toml").read_text()
        self.assertIn('server_name = "example.com"', tuwunel)
        self.assertIn("allow_registration = true", tuwunel)
        self.assertNotIn("{{", tuwunel)

    def test_apply_configuration_renders_element_customizations(self):
        cfg = self.sample_config()
        cfg["features"]["element"].update(
            {
                "brand": "Acme Chat",
                "default_theme": "dark",
                "disable_custom_urls": True,
                "help_url": "https://docs.example.com/chat",
                "branding": {
                    "auth_header_logo_url": "https://assets.example.com/logo.svg",
                    "welcome_background_url": [
                        "https://assets.example.com/bg-1.jpg",
                        "https://assets.example.com/bg-2.jpg",
                    ],
                    "auth_footer_links": [
                        {"text": "FAQ", "url": "https://example.com/faq"},
                    ],
                },
                "embedded_pages": {
                    "home_url": "https://assets.example.com/home.html",
                    "welcome_url": "https://assets.example.com/welcome.html",
                    "login_for_welcome": True,
                },
                "integrations": {
                    "enabled": False,
                },
                "labs": {
                    "show_settings": True,
                    "features": {"feature_video_rooms": True},
                },
                "ui_features": {
                    "registration": False,
                    "password_reset": False,
                    "create_public_rooms": False,
                },
                "notice": {
                    "title": "Maintenance",
                    "description": "Platform will move next month.",
                    "show_once": True,
                },
                "terms_and_conditions": {
                    "links": [{"text": "Policy", "url": "https://example.com/policy"}],
                },
                "report_event": {
                    "admin_message_md": "Please review policy before reporting.",
                },
                "bug_report": {
                    "endpoint_url": "local",
                    "existing_issues_url": "https://example.com/issues",
                    "new_issue_url": "https://example.com/issues/new",
                    "sentry": {
                        "dsn": "https://sentry.example.com/1",
                        "environment": "prod",
                    },
                },
                "extra_config": {
                    "brand": "Acme Override",
                    "custom_translations_url": "https://assets.example.com/i18n.json",
                },
            }
        )
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        element = json.loads((self.root / "modules/core/element/config.json").read_text())
        self.assertEqual(element["brand"], "Acme Override")
        self.assertEqual(element["default_theme"], "dark")
        self.assertTrue(element["disable_custom_urls"])
        self.assertEqual(element["help_url"], "https://docs.example.com/chat")
        self.assertEqual(element["branding"]["auth_header_logo_url"], "https://assets.example.com/logo.svg")
        self.assertEqual(len(element["branding"]["welcome_background_url"]), 2)
        self.assertEqual(element["embedded_pages"]["home_url"], "https://assets.example.com/home.html")
        self.assertTrue(element["embedded_pages"]["login_for_welcome"])
        self.assertIsNone(element["integrations_ui_url"])
        self.assertIsNone(element["integrations_rest_url"])
        self.assertIsNone(element["integrations_widgets_urls"])
        self.assertTrue(element["show_labs_settings"])
        self.assertEqual(element["features"], {"feature_video_rooms": True})
        self.assertFalse(element["setting_defaults"]["UIFeature.registration"])
        self.assertFalse(element["setting_defaults"]["UIFeature.passwordReset"])
        self.assertFalse(element["setting_defaults"]["UIFeature.allowCreatingPublicRooms"])
        self.assertEqual(element["user_notice"]["title"], "Maintenance")
        self.assertEqual(element["terms_and_conditions_links"][0]["text"], "Policy")
        self.assertEqual(element["report_event"]["admin_message_md"], "Please review policy before reporting.")
        self.assertEqual(element["bug_report_endpoint_url"], "local")
        self.assertEqual(element["sentry"]["environment"], "prod")
        self.assertEqual(element["custom_translations_url"], "https://assets.example.com/i18n.json")

    def test_apply_configuration_renders_custom_integrations_override(self):
        cfg = self.sample_config()
        cfg["features"]["element"]["integrations"] = {
            "ui_url": "https://scalar.example.com/",
            "rest_url": "https://scalar.example.com/api",
            "widgets_urls": ["https://scalar.example.com/widgets"],
        }
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        element = json.loads((self.root / "modules/core/element/config.json").read_text())
        self.assertEqual(element["integrations_ui_url"], "https://scalar.example.com/")
        self.assertEqual(element["integrations_rest_url"], "https://scalar.example.com/api")
        self.assertEqual(element["integrations_widgets_urls"], ["https://scalar.example.com/widgets"])

    def test_apply_configuration_renders_mas_enabled_synapse(self):
        cfg = self.sample_config()
        cfg["features"]["local_login_enabled"] = False
        cfg["features"]["sso"] = {
            "enabled": True,
            "providers": [
                {
                    "name": "Google",
                    "issuer": "https://accounts.google.com/",
                    "client_id": "id",
                    "client_secret": "secret",
                }
            ],
        }
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        synapse = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("password_config:\n  enabled: false", synapse)
        self.assertIn("login_via_existing_session:\n  enabled: false", synapse)
        self.assertIn("oidc_providers: []", synapse)
        self.assertIn("msc3861:", synapse)
        self.assertIn("org.matrix.msc2965.authentication:", synapse)
        mas_cfg = (self.root / "modules/mas/config.yaml").read_text()
        self.assertIn("matrix.example.com/auth/", mas_cfg)
        self.assertIn(mas_config.MAS_DOCKER_ASSETS_PATH, mas_cfg)
        self.assertNotIn("/usr/local/share/assets/", mas_cfg)
        self.assertIn("upstream_oauth2:", mas_cfg)

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertIn("handle_path /auth/*", caddy)
        self.assertIn("handle /.well-known/openid-configuration", caddy)
        self.assertIn("reverse_proxy matrix_mas:8080", caddy)

    def test_reconcile_mas_bootstrap_defers_without_postgres(self):
        cfg = self.sample_config()
        cfg["features"]["sso"] = {
            "enabled": True,
            "providers": [
                {
                    "name": "Google",
                    "issuer": "https://accounts.google.com/",
                    "client_id": "id",
                    "client_secret": "secret",
                }
            ],
        }
        self.write_config(cfg)
        (self.root / "modules/mas/config.yaml").write_text("http:\n  public_base: https://matrix.example.com/auth/\n")
        ctx = apply.ApplyContext(self.root)
        env_vars = {"MAS_ENABLED": "true", "MAS_DB_PASSWORD": "secret"}

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="matrix_synapse\n",
                stderr="",
            )
            apply.reconcile_mas_bootstrap(ctx, cfg, env_vars)

        setup_calls = [
            call
            for call in mock_run.call_args_list
            if call.args and "setup.sh" in str(call.args[0])
        ]
        self.assertEqual(setup_calls, [])

    def test_apply_configuration_strips_disabled_livekit_caddy_block(self):
        cfg = self.sample_config()
        cfg["features"]["calls"] = {"enabled": False, "livekit_domain": "livekit.example.com"}
        self.write_config(cfg)
        (self.root / "caddy/Caddyfile.template").write_text(
            "matrix.example.com {\n"
            "    reverse_proxy matrix_synapse:8008\n"
            "}\n\n"
            "# LiveKit SFU\n"
            "{{LIVEKIT_DOMAIN}} {\n"
            "    reverse_proxy host.docker.internal:7880\n"
            "}\n"
        )
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertIn("matrix.example.com {", caddy)
        self.assertNotIn("host.docker.internal:7880", caddy)
        self.assertNotIn('"" {', caddy)
        self.assertNotIn("# LiveKit SFU", caddy)

    def test_merge_duplicate_caddy_site_blocks_ignores_comment_with_brace(self):
        original = (
            "# Comment {\n"
            "example.com {\n"
            "    reverse_proxy matrix_synapse:8008\n"
            "}\n"
        )
        merged = apply.merge_duplicate_caddy_site_blocks(original)
        self.assertIn("# Comment {", merged)
        self.assertEqual(merged.count("example.com {"), 1)
        self.assertIn("reverse_proxy matrix_synapse:8008", merged)

    def test_merge_duplicate_caddy_site_blocks_combines_identical_hosts(self):
        original = (
            "# Matrix\n"
            "example.com {\n"
            "    handle /_matrix/* {\n"
            "        reverse_proxy matrix_synapse:8008\n"
            "    }\n"
            "}\n\n"
            "# LiveKit\n"
            "example.com {\n"
            "    handle /livekit/jwt* {\n"
            "        reverse_proxy matrix_lk_jwt_service:8080\n"
            "    }\n"
            "}\n\n"
            "# Element\n"
            "example.com {\n"
            "    reverse_proxy matrix_element:80\n"
            "}\n"
        )
        merged = apply.merge_duplicate_caddy_site_blocks(original)
        self.assertEqual(merged.count("example.com {"), 1)
        self.assertIn("handle /_matrix/*", merged)
        self.assertIn("handle /livekit/jwt*", merged)
        self.assertIn("reverse_proxy matrix_element:80", merged)

    def test_merge_duplicate_caddy_site_blocks_leaves_distinct_hosts(self):
        original = (
            "matrix.example.com {\n"
            "    reverse_proxy matrix_synapse:8008\n"
            "}\n\n"
            "element.example.com {\n"
            "    reverse_proxy matrix_element:80\n"
            "}\n"
        )
        merged = apply.merge_duplicate_caddy_site_blocks(original)
        self.assertEqual(merged.count("matrix.example.com {"), 1)
        self.assertEqual(merged.count("element.example.com {"), 1)

    def test_apply_configuration_merges_unified_domain_caddy_blocks(self):
        repo_root = Path(__file__).resolve().parent.parent
        (self.root / "caddy/Caddyfile.template").write_text(
            (repo_root / "caddy/Caddyfile.template").read_text()
        )
        cfg = self.sample_config()
        unified = "example.com"
        cfg["matrix"]["domain"] = unified
        cfg["matrix"]["server_name"] = unified
        cfg["features"]["element"]["domain"] = unified
        cfg["features"]["calls"]["livekit_domain"] = unified
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")

        caddy = (self.root / "caddy/Caddyfile").read_text()
        self.assertEqual(len(re.findall(r"^example\.com \{", caddy, re.MULTILINE)), 1)
        self.assertIn("handle /_matrix/*", caddy)
        self.assertIn("handle_path /auth/*", caddy)
        self.assertIn("handle /livekit/jwt*", caddy)
        self.assertIn("handle {\n        reverse_proxy matrix_element:80", caddy)
        self.assertNotIn("handle /auth*", caddy)
        self.assertEqual(caddy.count("handle_path /auth/*"), 1)
        self.assertNotIn("{{", caddy)

    def test_apply_configuration_writes_bridge_env_values(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"] = {
            "enabled": True,
            "admin_username": "waadmin",
            "db_name": "wa_custom",
        }
        cfg["modules"]["slack_bridge"] = {
            "enabled": True,
            "admin_username": "sladmin",
            "db_name": "sl_custom",
        }
        self.write_config(cfg)
        (self.root / "modules/whatsapp-bridge/whatsapp/registration.yaml").write_text("wa-reg\n")
        (self.root / "modules/slack-bridge/slack/registration.yaml").write_text("sl-reg\n")
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        env_text = (self.root / ".env").read_text()
        self.assertIn("WA_ADMIN_USERNAME=waadmin", env_text)
        self.assertIn("WA_DB_NAME=wa_custom", env_text)
        self.assertIn("WA_DB_USER=mautrix_whatsapp", env_text)
        self.assertIn("WA_DB_URI=postgres://mautrix_whatsapp:", env_text)
        self.assertIn("SL_ADMIN_USERNAME=sladmin", env_text)
        self.assertIn("SL_DB_NAME=sl_custom", env_text)
        self.assertIn("SL_DB_USER=mautrix_slack", env_text)
        self.assertIn("SL_DB_URI=postgres://mautrix_slack:", env_text)

    def test_apply_reconciles_enabled_bridge_appservice_files_and_homeserver(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"]["enabled"] = True
        cfg["modules"]["slack_bridge"]["enabled"] = True
        self.write_config(cfg)

        (self.root / "modules/whatsapp-bridge/whatsapp/registration.yaml").write_text("wa-reg\n")
        (self.root / "modules/slack-bridge/slack/registration.yaml").write_text("sl-reg\n")

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        wa_dest = self.root / "modules/core/synapse_data/whatsapp-registration.yaml"
        sl_dest = self.root / "modules/core/synapse_data/slack-registration.yaml"
        self.assertTrue(wa_dest.exists())
        self.assertTrue(sl_dest.exists())
        self.assertEqual(wa_dest.read_text(), "wa-reg\n")
        self.assertEqual(sl_dest.read_text(), "sl-reg\n")

        homeserver = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("/data/whatsapp-registration.yaml", homeserver)
        self.assertIn("/data/slack-registration.yaml", homeserver)

    def test_apply_reconciles_disabled_bridge_appservice_cleanup(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"]["enabled"] = False
        cfg["modules"]["slack_bridge"]["enabled"] = False
        self.write_config(cfg)

        homeserver = self.root / "modules/core/synapse/homeserver.yaml"
        homeserver.parent.mkdir(parents=True, exist_ok=True)
        homeserver.write_text(
            "server_name: example.com\n"
            "app_service_config_files:\n"
            "  - /data/whatsapp-registration.yaml\n"
            "  - /data/slack-registration.yaml\n"
        )

        (self.root / "modules/core/synapse_data/whatsapp-registration.yaml").write_text("wa-reg\n")
        (self.root / "modules/core/synapse_data/slack-registration.yaml").write_text("sl-reg\n")

        ctx = apply.ApplyContext(self.root)
        apply.reconcile_bridge_appservices(ctx, cfg)

        self.assertFalse((self.root / "modules/core/synapse_data/whatsapp-registration.yaml").exists())
        self.assertFalse((self.root / "modules/core/synapse_data/slack-registration.yaml").exists())
        cleaned = homeserver.read_text()
        self.assertNotIn("/data/whatsapp-registration.yaml", cleaned)
        self.assertNotIn("/data/slack-registration.yaml", cleaned)

    def test_apply_reconciles_enabled_hookshot_appservice_and_caddy(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"] = {
            "enabled": True,
            "domain": "hookshot.example.com",
        }
        self.write_config(cfg)

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True, exist_ok=True)
        (self.root / "modules/hookshot/hookshot/registration.yml").write_text("hookshot-reg\n")

        caddy_path = self.root / "caddy/Caddyfile"
        caddy_path.write_text("matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n")

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        hookshot_dest = self.root / "modules/core/synapse_data/hookshot-registration.yml"
        self.assertTrue(hookshot_dest.exists())
        self.assertEqual(hookshot_dest.read_text(), "hookshot-reg\n")

        homeserver = (self.root / "modules/core/synapse/homeserver.yaml").read_text()
        self.assertIn("/data/hookshot-registration.yml", homeserver)

        caddy = caddy_path.read_text()
        self.assertIn("# BEGIN MED-HOOKSHOT BLOCK", caddy)
        self.assertIn("hookshot.example.com {", caddy)

    def test_apply_reconciles_disabled_hookshot_appservice_and_caddy_cleanup(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"] = {
            "enabled": False,
            "domain": "hookshot.example.com",
        }
        self.write_config(cfg)

        homeserver = self.root / "modules/core/synapse/homeserver.yaml"
        homeserver.parent.mkdir(parents=True, exist_ok=True)
        homeserver.write_text(
            "server_name: example.com\n"
            "app_service_config_files:\n"
            "  - /data/hookshot-registration.yml\n"
        )

        (self.root / "modules/core/synapse_data/hookshot-registration.yml").write_text("hookshot-reg\n")

        caddy_path = self.root / "caddy/Caddyfile"
        caddy_path.write_text(
            "matrix.example.com {\n    reverse_proxy matrix_synapse:8008\n}\n\n"
            "# BEGIN MED-HOOKSHOT BLOCK\n"
            "hookshot.example.com {\n    reverse_proxy matrix-hookshot:9000\n}\n"
            "# END MED-HOOKSHOT BLOCK\n"
        )

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        self.assertFalse((self.root / "modules/core/synapse_data/hookshot-registration.yml").exists())
        cleaned_hs = homeserver.read_text()
        self.assertNotIn("/data/hookshot-registration.yml", cleaned_hs)

        cleaned_caddy = caddy_path.read_text()
        self.assertNotIn("# BEGIN MED-HOOKSHOT BLOCK", cleaned_caddy)
        self.assertNotIn("hookshot.example.com {", cleaned_caddy)

    def test_apply_reconciles_disabled_hookshot_legacy_caddy_using_env_domain_fallback(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"] = {
            "enabled": False,
        }
        self.write_config(cfg)

        # Simulate prior deploy state where domain exists in generated .env.
        (self.root / ".env").write_text("HOOKSHOT_DOMAIN=hookshot.example.com\n")

        caddy_path = self.root / "caddy/Caddyfile"
        caddy_path.write_text(
            "hookshot.example.com {\n"
            "    reverse_proxy matrix-hookshot:9000\n"
            "}\n"
            "\n"
            "matrix.example.com {\n"
            "    reverse_proxy matrix_synapse:8008\n"
            "}\n"
        )

        ctx = apply.ApplyContext(self.root)
        apply.apply_configuration(ctx, server_ip="9.8.7.6", reconcile_modules=False)

        cleaned_caddy = caddy_path.read_text()
        self.assertNotIn("hookshot.example.com {", cleaned_caddy)
        self.assertIn("matrix.example.com", cleaned_caddy)

    def test_apply_reuses_existing_secrets(self):
        self.write_config(self.sample_config())
        ctx = apply.ApplyContext(self.root)

        apply.apply_configuration(ctx, server_ip="9.8.7.6")
        saved_1 = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())

        apply.apply_configuration(ctx, server_ip="9.8.7.6")
        saved_2 = yaml.safe_load((self.root / ".matrix-easy-deploy/secrets.yaml").read_text())

        self.assertEqual(saved_1, saved_2)

    def test_apply_configuration_reconciles_backup_schedule(self):
        cfg = self.sample_config()
        cfg["backup"] = {
            "enabled": True,
            "repository": {"type": "local", "path": "/var/backups/med-kit"},
            "schedule": {"enabled": True, "calendar": "daily", "persistent": True},
            "retention": {"keep_daily": 7},
        }
        self.write_config(cfg)
        ctx = apply.ApplyContext(self.root)

        with patch("scripts.apply.backup_schedule.reconcile", return_value="Automatic backup timer installed or updated.") as mock_reconcile:
            apply.apply_configuration(ctx, server_ip="9.8.7.6")

        mock_reconcile.assert_called_once_with(ctx.project_root, cfg)

    def test_run_runtime_reconcile_invokes_stop_then_start(self):
        ctx = apply.ApplyContext(self.root)
        with patch("scripts.apply.subprocess.run") as mock_run:
            apply.run_runtime_reconcile(ctx)

        self.assertEqual(mock_run.call_count, 2)
        first_args = mock_run.call_args_list[0].args[0]
        second_args = mock_run.call_args_list[1].args[0]
        self.assertTrue(str(first_args[1]).endswith("stop.sh"))
        self.assertTrue(str(second_args[1]).endswith("start.sh"))

    def test_parse_args_reconciles_runtime_by_default(self):
        args = apply.parse_args([])

        self.assertTrue(args.reconcile_runtime)

    def test_parse_args_supports_runtime_reconcile_opt_out(self):
        args = apply.parse_args(["--no-reconcile-runtime"])

        self.assertFalse(args.reconcile_runtime)

    def test_main_reconciles_runtime_by_default(self):
        self.write_config(self.sample_config())

        with patch("scripts.apply.apply_configuration") as mock_apply, patch(
            "scripts.apply.run_runtime_reconcile"
        ) as mock_reconcile, patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = apply.main(["--project-root", str(self.root), "--server-ip", "9.8.7.6"])

        self.assertEqual(exit_code, 0)
        mock_apply.assert_called_once()
        mock_reconcile.assert_called_once()
        self.assertIn("Runtime reconciled via stop/start.", stdout.getvalue())

    def test_main_skips_runtime_reconcile_with_opt_out(self):
        self.write_config(self.sample_config())

        with patch("scripts.apply.apply_configuration") as mock_apply, patch(
            "scripts.apply.run_runtime_reconcile"
        ) as mock_reconcile, patch("sys.stdout", new_callable=StringIO) as stdout:
            exit_code = apply.main(
                ["--project-root", str(self.root), "--server-ip", "9.8.7.6", "--no-reconcile-runtime"]
            )

        self.assertEqual(exit_code, 0)
        mock_apply.assert_called_once()
        mock_reconcile.assert_not_called()
        self.assertNotIn("Runtime reconciled via stop/start.", stdout.getvalue())

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

    def test_module_bootstrap_invoked_for_whatsapp_missing_registration(self):
        cfg = self.sample_config()
        cfg["modules"]["whatsapp_bridge"] = {
            "enabled": True,
            "admin_username": "waadmin",
            "db_name": "wa_custom",
        }

        (self.root / "modules/whatsapp-bridge/whatsapp/config.yaml").write_text("ok\n")

        ctx = apply.ApplyContext(self.root)

        def _mock_setup(*args, **kwargs):
            whatsapp_dir = self.root / "modules/whatsapp-bridge/whatsapp"
            whatsapp_dir.mkdir(parents=True, exist_ok=True)
            (whatsapp_dir / "registration.yaml").write_text("ok\n")

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.reconcile_module_bootstrap(ctx, cfg)

        self.assertEqual(mock_run.call_count, 1)
        call = mock_run.call_args
        cmd = call.args[0]
        env = call.kwargs["env"]
        self.assertTrue(str(cmd[1]).endswith("modules/whatsapp-bridge/setup.sh"))
        self.assertEqual(env.get("MED_NON_INTERACTIVE"), "1")
        self.assertEqual(env.get("MODULE_WA_ADMIN_USERNAME"), "waadmin")
        self.assertEqual(env.get("MODULE_WA_DB_NAME"), "wa_custom")

    def test_module_bootstrap_skips_when_required_files_exist(self):
        cfg = self.sample_config()
        cfg["modules"]["hookshot"]["enabled"] = True

        (self.root / "modules/hookshot/hookshot").mkdir(parents=True)
        (self.root / "modules/hookshot/hookshot/config.yml").write_text("ok\n")
        (self.root / "modules/hookshot/hookshot/registration.yml").write_text("ok\n")

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
