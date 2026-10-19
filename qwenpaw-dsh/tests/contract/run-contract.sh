#!/bin/sh
# Convenience alias for `./pytest -m contract`.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$DIR/pytest" -m contract "$@"
