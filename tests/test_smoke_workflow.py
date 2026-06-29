import subprocess
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import apply
from tests.helpers.project_tree import build_minimal_project, write_deploy_config


class SmokeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_minimal_project(self.root, preset="core_only")
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

    def _write_config(self, modules=None):
        overrides: dict = {}
        if modules:
            overrides["modules"] = modules
        write_deploy_config(self.root, **overrides)

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

        import subprocess

        def _mock_setup(cmd, *args, **kwargs):
            cmd_str = " ".join(str(part) for part in cmd)
            if "modules/hookshot/setup.sh" not in cmd_str:
                return subprocess.run(cmd, *args, **kwargs)
            hookshot_dir = self.root / "modules/hookshot/hookshot"
            hookshot_dir.mkdir(parents=True, exist_ok=True)
            (hookshot_dir / "config.yml").write_text("ok\n")
            (hookshot_dir / "registration.yml").write_text("ok\n")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("scripts.apply.subprocess.run") as mock_run:
            mock_run.side_effect = _mock_setup
            apply.apply_configuration(ctx, server_ip="9.9.9.9")
            apply.apply_configuration(ctx, server_ip="9.9.9.9")

        hookshot_calls = [
            call
            for call in mock_run.call_args_list
            if "modules/hookshot/setup.sh" in " ".join(str(part) for part in call.args[0])
        ]
        self.assertEqual(len(hookshot_calls), 1)


if __name__ == "__main__":
    unittest.main()
