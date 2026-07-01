#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "ttyd 1.0.0-test-shim"
    exit 0
fi

FAKE_PORT=${FAKE_TTYD_PORT:-31415}
echo "Listening on http://127.0.0.1:${FAKE_PORT}/"

while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    shift
done
if [ "$1" = "--" ]; then
    shift
fi
exec "$@"