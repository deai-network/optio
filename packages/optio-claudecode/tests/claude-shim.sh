#!/bin/bash
# Substitutes the real claude binary during tests. Forwards all args to
# fake_claude.py from this directory.
exec python3 "$(dirname "$0")/fake_claude.py" "$@"
