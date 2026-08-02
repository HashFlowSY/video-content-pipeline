#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd -P)
EXPECTED_VENV="$PROJECT_ROOT/.venv"
EXPECTED_PYTHON="$EXPECTED_VENV/bin/python"

if [ "${VIRTUAL_ENV:-}" != "$EXPECTED_VENV" ]; then
    printf '%s\n' "VCP environment gate: activate $EXPECTED_VENV before starting Python." >&2
    exit 78
fi

if [ ! -x "$EXPECTED_PYTHON" ]; then
    printf '%s\n' "VCP environment gate: expected Python executable is missing: $EXPECTED_PYTHON" >&2
    exit 78
fi

if [ "$(command -v python || true)" != "$EXPECTED_PYTHON" ]; then
    printf '%s\n' "VCP environment gate: python does not resolve to $EXPECTED_PYTHON" >&2
    exit 78
fi
