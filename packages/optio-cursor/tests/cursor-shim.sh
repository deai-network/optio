#!/bin/bash
# Substitutes the real cursor-agent binary during tests. Forwards all args
# to fake_cursor.py from this script's real location (the symlink may point
# to this script from a tmpdir, so we resolve $0 to its target). Installed
# under the name `cursor-agent` by the shim_install_dir fixture.
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec python3 "$SCRIPT_DIR/fake_cursor.py" "$@"
