#!/bin/sh
set -eu

# Generate pinned lock files from requirements.in files (pip lock).
# Usage:
#   ./scripts/lock.sh

python -m pip install --upgrade pip >/dev/null

# pip lock is experimental but ships with modern pip. It outputs a PEP-751 style
# TOML lock file, pinned to the current platform+python.
python -m pip lock -r requirements.in -o pylock.toml
python -m pip lock -r requirements-dev.in -o pylock.dev.toml

echo "Wrote pylock.toml and pylock.dev.toml"
