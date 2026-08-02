#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd -P)
"$SCRIPT_DIR/require-project-venv.sh"
exec "$VIRTUAL_ENV/bin/python" -m video_content_pipeline "$@"
