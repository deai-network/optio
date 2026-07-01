#!/bin/bash
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec python3 "$SCRIPT_DIR/fake_codex.py" "$@"