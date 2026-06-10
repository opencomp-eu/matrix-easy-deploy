#!/usr/bin/env python3
"""Homeserver implementation metadata for matrix-easy-deploy."""

from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_IMPLEMENTATIONS = frozenset({"synapse", "tuwunel"})
DEFAULT_IMPLEMENTATION = "synapse"


@dataclass(frozen=True)
class HomeserverSpec:
    implementation: str
    container_name: str
    internal_host: str
    internal_port: int
    compose_profile: str
    config_container_path: str
    rendered_config_rel: str
    rendered_config_template_rel: str
    data_dir_rel: str
    appservice_data_rel: str
    supports_synapse_admin_api: bool
    supports_shared_secret_registration: bool
    supports_synapse_appservice_yaml: bool
    supports_tuwunel_appservice_dir: bool


SPECS: dict[str, HomeserverSpec] = {
    "synapse": HomeserverSpec(
        implementation="synapse",
        container_name="matrix_synapse",
        internal_host="matrix_synapse",
        internal_port=8008,
        compose_profile="synapse",
        config_container_path="/data/homeserver.yaml",
        rendered_config_rel="modules/core/synapse/homeserver.yaml",
        rendered_config_template_rel="modules/core/synapse/homeserver.yaml.template",
        data_dir_rel="modules/core/synapse_data",
        appservice_data_rel="modules/core/synapse_data",
        supports_synapse_admin_api=True,
        supports_shared_secret_registration=True,
        supports_synapse_appservice_yaml=True,
        supports_tuwunel_appservice_dir=False,
    ),
    "tuwunel": HomeserverSpec(
        implementation="tuwunel",
        container_name="matrix_tuwunel",
        internal_host="matrix_tuwunel",
        internal_port=8008,
        compose_profile="tuwunel",
        config_container_path="/tuwunel.toml",
        rendered_config_rel="modules/core/tuwunel/tuwunel.toml",
        rendered_config_template_rel="modules/core/tuwunel/tuwunel.toml.template",
        data_dir_rel="modules/core/tuwunel_data",
        appservice_data_rel="modules/core/tuwunel_data/appservices",
        supports_synapse_admin_api=False,
        supports_shared_secret_registration=False,
        supports_synapse_appservice_yaml=False,
        supports_tuwunel_appservice_dir=True,
    ),
}


def normalize_implementation(value: str | None) -> str:
    if value is None or not str(value).strip():
        return DEFAULT_IMPLEMENTATION
    normalized = str(value).strip().lower()
    if normalized not in SUPPORTED_IMPLEMENTATIONS:
        supported = ", ".join(sorted(SUPPORTED_IMPLEMENTATIONS))
        raise ValueError(
            f"matrix.server_implementation must be one of: {supported} (got {value!r})"
        )
    return normalized


def get_implementation(config: dict) -> str:
    matrix = config.get("matrix", {}) if isinstance(config.get("matrix", {}), dict) else {}
    return normalize_implementation(matrix.get("server_implementation"))


def get_spec(config: dict) -> HomeserverSpec:
    return SPECS[get_implementation(config)]


def spec_env_overrides(spec: HomeserverSpec) -> dict[str, str]:
    internal_url = f"http://{spec.internal_host}:{spec.internal_port}"
    return {
        "SERVER_IMPLEMENTATION": spec.implementation,
        "HOMESERVER_CONTAINER": spec.container_name,
        "HOMESERVER_INTERNAL_HOST": spec.internal_host,
        "HOMESERVER_INTERNAL_PORT": str(spec.internal_port),
        "HOMESERVER_INTERNAL_URL": internal_url,
        "HOMESERVER_UPSTREAM": f"{spec.internal_host}:{spec.internal_port}",
        "HOMESERVER_COMPOSE_PROFILE": spec.compose_profile,
    }


def caddy_synapse_admin_block(spec: HomeserverSpec) -> str:
    if not spec.supports_synapse_admin_api:
        return ""
    return (
        "\n    # Synapse admin API\n"
        "    handle /_synapse/* {\n"
        f"        reverse_proxy {spec.internal_host}:{spec.internal_port}\n"
        "    }\n"
    )
