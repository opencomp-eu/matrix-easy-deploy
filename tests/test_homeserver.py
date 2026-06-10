import unittest

from scripts import homeserver


class HomeserverTests(unittest.TestCase):
    def test_default_implementation(self):
        self.assertEqual(homeserver.get_implementation({}), "synapse")
        self.assertEqual(
            homeserver.get_implementation({"matrix": {"domain": "matrix.example.com"}}),
            "synapse",
        )

    def test_tuwunel_implementation(self):
        config = {
            "matrix": {
                "domain": "matrix.example.com",
                "server_implementation": "tuwunel",
            }
        }
        spec = homeserver.get_spec(config)
        self.assertEqual(spec.implementation, "tuwunel")
        self.assertEqual(spec.container_name, "matrix_tuwunel")

    def test_invalid_implementation_raises(self):
        with self.assertRaises(ValueError):
            homeserver.normalize_implementation("dendrite")

    def test_caddy_admin_block_only_for_synapse(self):
        synapse = homeserver.SPECS["synapse"]
        tuwunel = homeserver.SPECS["tuwunel"]
        self.assertIn("/_synapse/", homeserver.caddy_synapse_admin_block(synapse))
        self.assertEqual(homeserver.caddy_synapse_admin_block(tuwunel), "")


if __name__ == "__main__":
    unittest.main()
