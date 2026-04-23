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

HTTP_STATUS=$(curl -fsSL -o /dev/null -w "%{http_code}" \
    -X POST "${BASE_URL}/_synapse/admin/v1/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"nonce\":    \"${NONCE}\",
        \"username\": \"${USERNAME}\",
        \"password\": \"${PASSWORD}\",
        \"admin\":    true,
        \"mac\":      \"${MAC}\"
    }")

if [[ "$HTTP_STATUS" == "200" ]] || [[ "$HTTP_STATUS" == "201" ]]; then
    success "Admin user '@${USERNAME}:${BASE_URL#*://}' created successfully."
else
    # Check if user already exists
    if [[ "$HTTP_STATUS" == "400" ]]; then
        warn "User '${USERNAME}' may already exist (HTTP 400). Skipping."
    else
        die "Failed to create admin user (HTTP ${HTTP_STATUS})."
    fi
fi
