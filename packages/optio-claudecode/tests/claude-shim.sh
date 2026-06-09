#!/bin/bash
# Substitutes the real claude binary during tests. Forwards all args to
# fake_claude.py from this script's real location (the symlink may
# point to this script from a tmpdir, so we resolve $0 to its target).
# Argv passes through unmodified: when it contains --input-format,
# fake_claude.py switches to its bidirectional stream-json (NDJSON)
# mode instead of the FAKE_CLAUDE_SCENARIO script mode.
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec python3 "$SCRIPT_DIR/fake_claude.py" "$@"
