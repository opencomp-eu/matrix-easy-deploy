"""Parametrized validation tests for scripts/config_edit.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import config_edit


@pytest.mark.parametrize(
    "module_name",
    ["discord-bridge", "unknown", ""],
)
def test_update_module_config_rejects_unsupported_module(module_name: str) -> None:
    config = config_edit.load_or_init(Path("/nonexistent/deploy.yaml"))

    with pytest.raises(ValueError, match="Unsupported module name"):
        config_edit.update_module_config(config, module_name, enabled=True)


@pytest.mark.parametrize(
    "implementation",
    ["dendrite", "conduit", "invalid"],
)
def test_normalize_server_implementation_rejects_invalid(implementation: str) -> None:
    if implementation == "":
        assert config_edit.normalize_server_implementation(implementation) == "synapse"
        return

    with pytest.raises(ValueError, match="server_implementation must be one of"):
        config_edit.normalize_server_implementation(implementation)


@pytest.mark.parametrize("implementation", ["synapse", "tuwunel", "Tuwunel"])
def test_normalize_server_implementation_accepts_supported(implementation: str) -> None:
    assert config_edit.normalize_server_implementation(implementation) == implementation.lower()


def test_load_or_init_rejects_non_object_root(tmp_path: Path) -> None:
    deploy_yaml = tmp_path / "deploy.yaml"
    deploy_yaml.write_text(yaml.safe_dump(["not", "a", "mapping"], sort_keys=False))

    with pytest.raises(ValueError, match="deploy.yaml root must be an object"):
        config_edit.load_or_init(deploy_yaml)
