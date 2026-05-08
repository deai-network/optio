#!/usr/bin/env bash
set -euo pipefail
git config core.hooksPath scripts/git-hooks
echo "Hooks installed via core.hooksPath=scripts/git-hooks"
echo "Pre-commit will run 'make codegen' drift check."
