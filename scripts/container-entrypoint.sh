#!/usr/bin/env sh
set -eu

SRC_DIR="/usr/local/share/matrix-easy-deploy"
WORK_DIR="${MEDKIT_WORKDIR:-/workspace}"

mkdir -p "$WORK_DIR"

if [ ! -f "$WORK_DIR/matrix-wizard.sh" ]; then
  cp -a "$SRC_DIR"/. "$WORK_DIR"/
fi

cd "$WORK_DIR"

if [ "${1:-}" = "" ]; then
  set -- bash matrix-wizard.sh
fi

exec "$@"
