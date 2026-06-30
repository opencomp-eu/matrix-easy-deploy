import os
import unittest
from unittest.mock import patch

from scripts import mas_config


class MasConfigSigningKeyTests(unittest.TestCase):
    def test_normalize_pem_private_key_strips_ec_parameters(self):
        combined = "\n".join(
            [
                "-----BEGIN EC PARAMETERS-----",
                "BggqhkjOPQMBBw==",
                "-----END EC PARAMETERS-----",
                "-----BEGIN EC PRIVATE KEY-----",
                "MHcCAQEEICBFPwH0N1wGI83vE2z91UweR/0p8TyEXCkhhqn76CfXoAoGCCqGSM49",
                "-----END EC PRIVATE KEY-----",
            ]
        )
        normalized = mas_config._normalize_pem_private_key(combined)
        self.assertNotIn("EC PARAMETERS", normalized)
        self.assertIn("BEGIN EC PRIVATE KEY", normalized)

    def test_mas_signing_keys_usable_rejects_stub(self):
        stub = mas_config._generate_mas_signing_material_stub()["MAS_SIGNING_KEYS"]
        self.assertFalse(mas_config._mas_signing_keys_usable(stub))

    def test_mas_signing_keys_usable_accepts_openssl_rsa(self):
        material = mas_config._generate_mas_signing_material_openssl()
        self.assertTrue(mas_config._mas_signing_keys_usable(material["MAS_SIGNING_KEYS"]))
        key = material["MAS_SIGNING_KEYS"][0]["key"]
        self.assertIn("BEGIN PRIVATE KEY", key)
        self.assertNotIn("EC PARAMETERS", key)

    def test_build_mas_signing_keys_yaml_uses_literal_block(self):
        material = mas_config._generate_mas_signing_material_openssl()
        yaml_text = mas_config.build_mas_signing_keys_yaml_from_state(
            {
                "MAS_ENCRYPTION_SECRET": material["MAS_ENCRYPTION_SECRET"],
                "MAS_SIGNING_KEYS": material["MAS_SIGNING_KEYS"],
            }
        )
        self.assertIn("secrets:", yaml_text)
        self.assertIn("key: |", yaml_text)
        self.assertIn("BEGIN PRIVATE KEY", yaml_text)
        self.assertNotIn("EC PARAMETERS", yaml_text)

    def test_ensure_mas_secrets_regenerates_invalid_keys(self):
        invalid = mas_config._generate_mas_signing_material_stub()
        state = {
            "MAS_DB_PASSWORD": "db",
            "MAS_HOMESERVER_SECRET": "hs",
            "MAS_SYNAPSE_CLIENT_SECRET": "client",
            "MAS_ENCRYPTION_SECRET": invalid["MAS_ENCRYPTION_SECRET"],
            "MAS_SIGNING_KEYS": invalid["MAS_SIGNING_KEYS"],
        }
        with patch.dict(os.environ, {"MED_ALLOW_INSECURE_MAS_KEYS": "0"}, clear=False):
            updated = mas_config.ensure_mas_secrets(state, mas_enabled=True)
        self.assertTrue(mas_config._mas_signing_keys_usable(updated["MAS_SIGNING_KEYS"]))
        self.assertNotEqual(updated["MAS_SIGNING_KEYS"], invalid["MAS_SIGNING_KEYS"])


class MasConfigCaddyTests(unittest.TestCase):
    def test_caddy_mas_block_routes_oidc_discovery(self):
        block = mas_config.caddy_mas_block()
        self.assertIn("handle /.well-known/openid-configuration", block)
        self.assertIn("handle_path /auth/*", block)
        self.assertIn("reverse_proxy matrix_mas:8080", block)

    def test_build_caddy_element_routing_unified_host(self):
        routing = mas_config.build_caddy_element_routing(
            matrix_domain="example.com",
            server_name="example.com",
            element_enabled=True,
            element_domain="example.com",
        )
        self.assertIn("reverse_proxy matrix_element:80", routing["CADDY_ELEMENT_MATRIX_FALLBACK"])
        self.assertEqual(routing["CADDY_ELEMENT_SITE_BLOCK"], "")

    def test_build_caddy_element_routing_separate_host(self):
        routing = mas_config.build_caddy_element_routing(
            matrix_domain="matrix.example.com",
            server_name="example.com",
            element_enabled=True,
            element_domain="element.example.com",
        )
        self.assertEqual(routing["CADDY_ELEMENT_MATRIX_FALLBACK"], "")
        self.assertIn("element.example.com {", routing["CADDY_ELEMENT_SITE_BLOCK"])
        self.assertNotIn("matrix_mas", routing["CADDY_ELEMENT_SITE_BLOCK"])
