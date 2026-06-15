"""Parametrized edge-case tests for scripts/apply.py validation and derivation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from scripts import apply
from tests.helpers.project_tree import default_deploy_config


@pytest.fixture
def base_config() -> dict:
    return default_deploy_config()


@pytest.mark.parametrize(
    ("mutator", "expected_match"),
    [
        (lambda cfg: cfg.pop("matrix"), "Missing 'matrix' section"),
        (lambda cfg: cfg["matrix"].update({"domain": ""}), "matrix.domain"),
        (lambda cfg: cfg.update({"modules": []}), "modules must be an object"),
        (lambda cfg: cfg.update({"features": []}), "features must be an object"),
        (lambda cfg: cfg["features"].update({"registration_enabled": "yes"}), "features.registration_enabled"),
        (lambda cfg: cfg["modules"]["hookshot"].update({"enabled": "yes"}), "modules.hookshot.enabled"),
    ],
)
def test_validate_config_rejects_invalid_shapes(mutator, expected_match: str, base_config: dict) -> None:
    cfg = deepcopy(base_config)
    mutator(cfg)

    with pytest.raises(ValueError, match=expected_match):
        apply.validate_config(cfg)


@pytest.mark.parametrize(
    ("federation_enabled", "expected_whitelist", "expected_public_fed"),
    [
        (True, "~", "true"),
        (False, "[]", "false"),
    ],
)
def test_derive_values_federation_flags(
    federation_enabled: bool,
    expected_whitelist: str,
    expected_public_fed: str,
    base_config: dict,
) -> None:
    cfg = deepcopy(base_config)
    cfg["features"]["federation_enabled"] = federation_enabled

    derived = apply.derive_values(cfg, server_ip="10.0.0.1")

    assert derived["FEDERATION_WHITELIST"] == expected_whitelist
    assert derived["ALLOW_PUBLIC_ROOMS_FEDERATION"] == expected_public_fed
    assert derived["TUWUNEL_ALLOW_FEDERATION"] == expected_public_fed


@pytest.mark.parametrize(
    ("element_enabled", "element_domain", "expected_install", "expected_domain"),
    [
        (True, "chat.example.com", "true", "chat.example.com"),
        (False, "chat.example.com", "false", ""),
    ],
)
def test_derive_values_element_flags(
    element_enabled: bool,
    element_domain: str,
    expected_install: str,
    expected_domain: str,
    base_config: dict,
) -> None:
    cfg = deepcopy(base_config)
    cfg["features"]["element"] = {"enabled": element_enabled, "domain": element_domain}

    derived = apply.derive_values(cfg, server_ip="10.0.0.1")

    assert derived["INSTALL_ELEMENT"] == expected_install
    assert derived["ELEMENT_DOMAIN"] == expected_domain


def test_derive_values_uses_server_name_when_set(base_config: dict) -> None:
    cfg = deepcopy(base_config)
    cfg["matrix"]["server_name"] = "custom.example.com"

    derived = apply.derive_values(cfg, server_ip="10.0.0.1")

    assert derived["SERVER_NAME"] == "custom.example.com"
    assert derived["CADDY_MATRIX_HOSTS"] == "matrix.example.com, custom.example.com"
