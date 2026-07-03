#!/bin/bash
# Substitutes the real kimi binary during tests. Forwards all args to
# fake_kimi.py from this script's real location (the symlink may point to
# this script from a tmpdir, so we resolve $0 to its target).
#
# Unlike grok (which needed a separate ttyd shim), kimi serves its OWN web
# SPA — so this single shim both serves a stub page (`kimi server run` /
# `kimi web`) and speaks the optio.log protocol. There is no ttyd/tmux shim.
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec python3 "$SCRIPT_DIR/fake_kimi.py" "$@"
