#!/bin/bash
# Test substitute for the real claustrum Landlock sandbox CLI (Stage 9).
#
# Real claustrum applies a Landlock ruleset from its grant flags then execs
# the wrapped command; KERNEL enforcement is covered by the env-gated
# test_sandbox_enforce.py. This shim ACCEPTS and otherwise IGNORES the sandbox
# request — it enforces nothing — so default-on fs_isolation runs end-to-end
# through the fast fake suite (real tmux -> bash -> this shim -> codex-shim ->
# fake_codex, or directly claustrum -> fake_codex app-server in conversation
# mode).
#
# Args layout (from session._build_claustrum_wrap):
#   claustrum --best-effort --abi-min 1 <grant flags...> -- <cmd> <args...>
# It skips everything up to the `--` separator and execs the wrapped command
# UNCONFINED.
#
# When FAKE_CLAUSTRUM_RECORD names a path, the full argv is appended there
# (outside the workdir, which is wiped on teardown) so a wiring test can assert
# the grants + separator + the codex argv reached the sandbox layer.

if [ "$1" = "--version" ]; then
    echo "claustrum 0.0.0-test-shim"
    exit 0
fi

if [ -n "$FAKE_CLAUSTRUM_RECORD" ]; then
    echo "$*" >> "$FAKE_CLAUSTRUM_RECORD"
fi

while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    shift
done
if [ "$1" = "--" ]; then
    shift
fi
exec "$@"
