#!/bin/bash
# Substitutes the real claude binary during tests. Forwards all args to
# fake_claude.py from this script's real location (the symlink may
# point to this script from a tmpdir, so we resolve $0 to its target).
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec python3 "$SCRIPT_DIR/fake_claude.py" "$@"
