#!/usr/bin/env bash
# scripts/mas.sh — MAS upstream OAuth provider setup helpers for matrix-wizard.sh
# Source this file from setup scripts; do not execute directly.

generate_upstream_provider_id() {
    MAS_PROVIDER_NAME="$1" MAS_PROVIDER_ISSUER="$2" python3 - <<'PY'
import os
import sys

sys.path.insert(0, "scripts")
from mas_config import stable_provider_ulid

print(stable_provider_ulid(os.environ["MAS_PROVIDER_NAME"], os.environ["MAS_PROVIDER_ISSUER"]))
PY
}

gather_mas_upstream_providers() {
    ask_yn ENABLE_UPSTREAM_SSO_INPUT \
        "Enable upstream SSO login via MAS (OIDC/OAuth2, e.g. Google)?" \
        "n"

    if [[ "$ENABLE_UPSTREAM_SSO_INPUT" != "y" ]]; then
        MAS_UPSTREAM_PROVIDERS_YAML=""
        MAS_UPSTREAM_PROVIDER_COUNT="0"
        MAS_UPSTREAM_PROVIDER_NAMES=""
        return 0
    fi

    MAS_UPSTREAM_PROVIDERS_YAML=""
    MAS_UPSTREAM_PROVIDER_COUNT=0
    MAS_UPSTREAM_PROVIDER_NAMES=""

    while true; do
        MAS_UPSTREAM_PROVIDER_COUNT=$((MAS_UPSTREAM_PROVIDER_COUNT + 1))
        echo
        echo -e "  ${BOLD}MAS upstream provider #${MAS_UPSTREAM_PROVIDER_COUNT}${RESET}"

        local provider_name issuer_url client_id client_secret provider_id
        local auto_registration_input auto_registration

        ask provider_name "Provider display name" "Google"
        while [[ -z "$provider_name" ]]; do
            warn "Provider name is required."
            ask provider_name "Provider display name" "Google"
        done

        ask issuer_url "OIDC issuer URL" "https://accounts.google.com/"
        while [[ -z "$issuer_url" ]]; do
            warn "OIDC issuer URL is required."
            ask issuer_url "OIDC issuer URL" "https://accounts.google.com/"
        done

        ask client_id "OIDC client ID" ""
        while [[ -z "$client_id" ]]; do
            warn "OIDC client ID is required."
            ask client_id "OIDC client ID" ""
        done

        ask_secret client_secret "OIDC client secret"
        while [[ -z "$client_secret" ]]; do
            warn "OIDC client secret is required."
            ask_secret client_secret "OIDC client secret"
        done

        provider_id="$(generate_upstream_provider_id "$provider_name" "$issuer_url")"

        ask_yn auto_registration_input \
            "Allow NEW users to auto-register via this provider?" \
            "y"
        if [[ "$auto_registration_input" == "y" ]]; then
            auto_registration="true"
        else
            auto_registration="false"
        fi

        local provider_yaml
        provider_yaml="$(MAS_PROVIDER_ID="$provider_id" \
            MAS_PROVIDER_NAME="$provider_name" \
            MAS_PROVIDER_ISSUER="$issuer_url" \
            MAS_PROVIDER_CLIENT_ID="$client_id" \
            MAS_PROVIDER_CLIENT_SECRET="$client_secret" \
            MAS_PROVIDER_ALLOW_REGISTRATION="$auto_registration" \
            python3 - <<'PY'
import os
import yaml

entry = {
    "name": os.environ["MAS_PROVIDER_NAME"],
    "issuer": os.environ["MAS_PROVIDER_ISSUER"],
    "client_id": os.environ["MAS_PROVIDER_CLIENT_ID"],
    "client_secret": os.environ["MAS_PROVIDER_CLIENT_SECRET"],
    "allow_registration": os.environ.get("MAS_PROVIDER_ALLOW_REGISTRATION", "true") == "true",
    "id": os.environ["MAS_PROVIDER_ID"],
}
print(yaml.safe_dump(entry, sort_keys=False, default_flow_style=False).strip())
PY
)"

        if [[ -z "$MAS_UPSTREAM_PROVIDERS_YAML" ]]; then
            MAS_UPSTREAM_PROVIDERS_YAML="$provider_yaml"
        else
            MAS_UPSTREAM_PROVIDERS_YAML="${MAS_UPSTREAM_PROVIDERS_YAML}"$'\n'"$provider_yaml"
        fi

        if [[ -z "$MAS_UPSTREAM_PROVIDER_NAMES" ]]; then
            MAS_UPSTREAM_PROVIDER_NAMES="$provider_name"
        else
            MAS_UPSTREAM_PROVIDER_NAMES="${MAS_UPSTREAM_PROVIDER_NAMES}, ${provider_name}"
        fi

        ask_yn _add_another_provider "Add another upstream provider?" "n"
        if [[ "$_add_another_provider" != "y" ]]; then
            break
        fi
    done
}

gather_mas_config() {
    ask_yn ENABLE_MAS_INPUT \
        "Enable Matrix Authentication Service (MAS) for OAuth/QR login?" \
        "y"

    if [[ "$ENABLE_MAS_INPUT" != "y" ]]; then
        ENABLE_MAS="false"
        MAS_DOMAIN=""
        MAS_LOCAL_LOGIN_ENABLED="true"
        MAS_UPSTREAM_PROVIDERS_YAML=""
        MAS_UPSTREAM_PROVIDER_COUNT="0"
        MAS_UPSTREAM_PROVIDER_NAMES=""
        return 0
    fi

    ENABLE_MAS="true"
    local _suggested_auth_domain
    _suggested_auth_domain="auth.$(extract_base_domain "$MATRIX_DOMAIN")"
    ask MAS_DOMAIN "MAS domain  (e.g. auth.example.com)" "$_suggested_auth_domain"
    while [[ -z "$MAS_DOMAIN" ]]; do
        warn "MAS domain is required when MAS is enabled."
        ask MAS_DOMAIN "MAS domain" "$_suggested_auth_domain"
    done

    ask_yn MAS_LOCAL_LOGIN_INPUT \
        "Allow local password login via MAS?" \
        "y"
    if [[ "$MAS_LOCAL_LOGIN_INPUT" == "y" ]]; then
        MAS_LOCAL_LOGIN_ENABLED="true"
    else
        MAS_LOCAL_LOGIN_ENABLED="false"
    fi

    gather_mas_upstream_providers
}
