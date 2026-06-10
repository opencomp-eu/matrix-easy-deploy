#!/usr/bin/env bash
# Shared helpers for Tuwunel admin commands.
# Account creation is handled via the Matrix registration API in create-account.sh.

set -euo pipefail

die() { echo "  [ERR] $*" >&2; exit 1; }
