#!/usr/bin/env bash
# scripts/sso.sh — SSO (OIDC) setup helpers for setup.sh
# Source this file from setup.sh; do not execute directly.

sanitize_idp_id() {
    local raw="$1"
    local sanitized
    sanitized="$(echo "$raw" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')"
    if [[ -z "$sanitized" ]]; then
        sanitized="oidc"
    fi
    echo "$sanitized"
}

build_oidc_provider_json() {
    OIDC_IDP_ID_CURRENT="$1" \
    OIDC_PROVIDER_NAME_CURRENT="$2" \
    OIDC_ISSUER_URL_CURRENT="$3" \
    OIDC_CLIENT_ID_CURRENT="$4" \
    OIDC_CLIENT_SECRET_CURRENT="$5" \
    OIDC_ENABLE_AUTO_REGISTRATION_CURRENT="$6" \
    OIDC_RESTRICT_CLAIM_CURRENT="$7" \
    OIDC_RESTRICT_VALUES_CURRENT="$8" \
        python3 - <<'PY'
import json
import os

provider = {
    "idp_id": os.environ["OIDC_IDP_ID_CURRENT"],
    "idp_name": os.environ["OIDC_PROVIDER_NAME_CURRENT"],
    "discover": True,
    "issuer": os.environ["OIDC_ISSUER_URL_CURRENT"],
    "client_id": os.environ["OIDC_CLIENT_ID_CURRENT"],
    "client_secret": os.environ["OIDC_CLIENT_SECRET_CURRENT"],
    "enable_registration": os.environ.get("OIDC_ENABLE_AUTO_REGISTRATION_CURRENT", "false") == "true",
    "scopes": ["openid", "profile", "email"],
    "user_mapping_provider": {
        "config": {
            "subject_claim": "sub"
        }
    }
}

restrict_claim = os.environ.get("OIDC_RESTRICT_CLAIM_CURRENT", "").strip()
restrict_values_raw = os.environ.get("OIDC_RESTRICT_VALUES_CURRENT", "")
if restrict_claim:
    allowed_values = [value.strip() for value in restrict_values_raw.split(",") if value.strip()]
    if len(allowed_values) == 1:
        provider["attribute_requirements"] = [{
            "attribute": restrict_claim,
            "value": allowed_values[0],
        }]
    elif len(allowed_values) > 1:
        provider["attribute_requirements"] = [{
            "attribute": restrict_claim,
            "one_of": allowed_values,
        }]

print(json.dumps(provider, separators=(",", ":")))
PY
}

gather_sso_config() {
    ask_yn ENABLE_SSO_INPUT \
        "Enable SSO login (OIDC/OAuth2, e.g. Google)?" \
        "n"

    if [[ "$ENABLE_SSO_INPUT" != "y" ]]; then
        ENABLE_SSO="false"
        OIDC_PROVIDERS_JSON="[]"
        OIDC_PROVIDER_COUNT="0"
        OIDC_PROVIDER_NAMES=""
        return 0
    fi

    ENABLE_SSO="true"
    OIDC_PROVIDERS_JSON="[]"
    OIDC_PROVIDER_COUNT=0
    OIDC_PROVIDER_NAMES=""
    local _used_idp_ids=" "

    while true; do
        OIDC_PROVIDER_COUNT=$((OIDC_PROVIDER_COUNT + 1))
        echo
        echo -e "  ${BOLD}SSO provider #${OIDC_PROVIDER_COUNT}${RESET}"

        local provider_name issuer_url client_id client_secret
        local auto_registration_input auto_registration
        local restrict_input restrict_claim restrict_values
        local idp_id_default idp_id_candidate idp_id_final

        ask provider_name "SSO provider display name" "Google"
        while [[ -z "$provider_name" ]]; do
            warn "Provider name is required."
            ask provider_name "SSO provider display name" "Google"
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

        idp_id_default="$(sanitize_idp_id "$provider_name")"
        idp_id_candidate="$idp_id_default"
        local suffix=2
        while [[ "$_used_idp_ids" == *" ${idp_id_candidate} "* ]]; do
            idp_id_candidate="${idp_id_default}_${suffix}"
            suffix=$((suffix + 1))
        done

        ask idp_id_final "Provider ID (unique; lowercase/underscore)" "$idp_id_candidate"
        idp_id_final="$(sanitize_idp_id "$idp_id_final")"
        while [[ -z "$idp_id_final" || "$_used_idp_ids" == *" ${idp_id_final} "* ]]; do
            if [[ -z "$idp_id_final" ]]; then
                warn "Provider ID cannot be empty."
            else
                warn "Provider ID '${idp_id_final}' is already in use. Choose another."
            fi
            ask idp_id_final "Provider ID (unique; lowercase/underscore)" "$idp_id_candidate"
            idp_id_final="$(sanitize_idp_id "$idp_id_final")"
        done
        _used_idp_ids+="${idp_id_final} "

        ask_yn auto_registration_input \
            "Allow NEW users to auto-register via this SSO provider?" \
            "y"
        if [[ "$auto_registration_input" == "y" ]]; then
            auto_registration="true"
        else
            auto_registration="false"
        fi

        ask_yn restrict_input \
            "Restrict this provider to specific OIDC claim values? (recommended)" \
            "n"
        if [[ "$restrict_input" == "y" ]]; then
            ask restrict_claim \
                "OIDC claim to match (e.g. hd, groups, email)" \
                "hd"
            while [[ -z "$restrict_claim" ]]; do
                warn "Claim name is required when claim restriction is enabled."
                ask restrict_claim \
                    "OIDC claim to match (e.g. hd, groups, email)" \
                    "hd"
            done

            ask restrict_values \
                "Allowed claim value(s), comma-separated" \
                ""
            while [[ -z "$restrict_values" ]]; do
                warn "Provide at least one allowed claim value."
                ask restrict_values \
                    "Allowed claim value(s), comma-separated" \
                    ""
            done
        else
            restrict_claim=""
            restrict_values=""
        fi

        local provider_json
        provider_json="$(build_oidc_provider_json \
            "$idp_id_final" \
            "$provider_name" \
            "$issuer_url" \
            "$client_id" \
            "$client_secret" \
            "$auto_registration" \
            "$restrict_claim" \
            "$restrict_values")"

        OIDC_PROVIDERS_JSON="$(
            OIDC_PROVIDERS_JSON="$OIDC_PROVIDERS_JSON" \
            OIDC_PROVIDER_JSON="$provider_json" \
            python3 - <<'PY'
import json
import os

providers = json.loads(os.environ.get("OIDC_PROVIDERS_JSON", "[]"))
provider = json.loads(os.environ["OIDC_PROVIDER_JSON"])
providers.append(provider)
print(json.dumps(providers, separators=(",", ":")))
PY
        )"

        if [[ -z "$OIDC_PROVIDER_NAMES" ]]; then
            OIDC_PROVIDER_NAMES="$provider_name"
        else
            OIDC_PROVIDER_NAMES="${OIDC_PROVIDER_NAMES}, ${provider_name}"
        fi

        ask_yn _add_another_provider "Add another SSO provider?" "n"
        if [[ "$_add_another_provider" != "y" ]]; then
            break
        fi
    done
}
