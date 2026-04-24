#!/usr/bin/env bash
# scripts/create-admin.sh
# Creates the initial admin user using Synapse's shared-secret registration.
# Usage: ./create-admin.sh <base_url> <shared_secret> <username> <password>
#
# Requires: curl, python3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/lib.sh"

BASE_URL="${1:?BASE_URL required}"
SHARED_SECRET="${2:?SHARED_SECRET required}"
USERNAME="${3:?USERNAME required}"
PASSWORD="${4:?PASSWORD required}"

# ---------------------------------------------------------------------------
# Step 1: fetch a nonce from Synapse
# ---------------------------------------------------------------------------
info "Fetching registration nonce from Synapse…"

NONCE_RESPONSE=$(curl -fsSL "${BASE_URL}/_synapse/admin/v1/register")
NONCE=$(echo "$NONCE_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['nonce'])")

if [[ -z "$NONCE" ]]; then
    die "Could not retrieve nonce from Synapse. Is the server running and reachable?"
fi

# ---------------------------------------------------------------------------
# Step 2: compute HMAC-SHA1 MAC
# Format: nonce + NUL + username + NUL + password + NUL + "admin"
# ---------------------------------------------------------------------------
info "Computing registration MAC…"

MAC=$(python3 - <<PYEOF
import hmac, hashlib, sys

nonce    = "${NONCE}"
username = "${USERNAME}"
password = "${PASSWORD}"
secret   = "${SHARED_SECRET}"

mac = hmac.new(
    secret.encode("utf-8"),
    b"\x00".join([
        nonce.encode("utf-8"),
        username.encode("utf-8"),
        password.encode("utf-8"),
        b"admin",
    ]),
    hashlib.sha1,
).hexdigest()

print(mac)
PYEOF
)

# ---------------------------------------------------------------------------
# Step 3: register the admin user
# ---------------------------------------------------------------------------
info "Registering admin user '${USERNAME}'…"

JSON_PAYLOAD=$(python3 - <<PYEOF
import json

payload = {
    "nonce": "${NONCE}",
    "username": "${USERNAME}",
    "password": "${PASSWORD}",
    "admin": True,
    "mac": "${MAC}",
}

print(json.dumps(payload))
PYEOF
)

RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

HTTP_STATUS=$(curl -sS -o "$RESPONSE_FILE" -w "%{http_code}" \
    -X POST "${BASE_URL}/_synapse/admin/v1/register" \
    -H "Content-Type: application/json" \
    --data-binary @- <<< "${JSON_PAYLOAD}")

unset JSON_PAYLOAD

if [[ "$HTTP_STATUS" == "200" ]] || [[ "$HTTP_STATUS" == "201" ]]; then
    success "Admin user '@${USERNAME}:${BASE_URL#*://}' created successfully."
else
    RESPONSE_BODY="$(cat "$RESPONSE_FILE")"

    ERR_INFO=$(python3 - <<'PYEOF' "$RESPONSE_BODY"
import json
import sys

raw = sys.argv[1]
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}

errcode = data.get("errcode", "") if isinstance(data, dict) else ""
error = data.get("error", "") if isinstance(data, dict) else ""
print(f"{errcode}\t{error}")
PYEOF
)

    ERR_CODE="${ERR_INFO%%$'\t'*}"
    ERR_MSG="${ERR_INFO#*$'\t'}"

    if [[ "$HTTP_STATUS" == "400" && "$ERR_CODE" == "M_USER_IN_USE" ]]; then
        warn "User '${USERNAME}' already exists. Skipping."
    else
        if [[ -n "$ERR_CODE" || -n "$ERR_MSG" ]]; then
            die "Failed to create admin user (HTTP ${HTTP_STATUS}, ${ERR_CODE}: ${ERR_MSG})."
        fi
        die "Failed to create admin user (HTTP ${HTTP_STATUS}). Response: ${RESPONSE_BODY}"
    fi
fi
