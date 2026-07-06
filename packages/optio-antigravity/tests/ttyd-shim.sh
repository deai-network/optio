#!/bin/bash
# Substitutes the real ttyd binary during tests.
#
# Real ttyd binds a port and serves a WebSocket terminal; tests don't
# need any of that. This shim:
#   1. Supports `--version` so ensure_ttyd_installed detects it as a
#      working ttyd binary.
#   2. Prints a fake "Listening on http://127.0.0.1:<port>/" line on
#      stdout immediately, so the framework's port-discovery regex
#      finds a port and proceeds to register the iframe widget.
#   3. Skips ttyd's networking flags and exec's the inner command
#      after the `--` separator.
#
# Args layout (from build_ttyd_attach_argv):
#   ttyd -W -i <iface> -p <port> -T xterm-256color --
#        tmux -S <socket> attach -t <session>
# (agy now runs inside the detached tmux session; ttyd only attaches a
# viewer, so there is no -m 1 single-viewer cap any more.)

if [ "$1" = "--version" ]; then
    echo "ttyd 1.0.0-test-shim"
    exit 0
fi

# Pick a random "port" to advertise; this is never actually opened.
FAKE_PORT=${FAKE_TTYD_PORT:-31415}
echo "Listening on http://127.0.0.1:${FAKE_PORT}/"

while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    shift
done
if [ "$1" = "--" ]; then
    shift
fi
exec "$@"
