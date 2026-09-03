#!/usr/bin/env bash
# jobwatch daily runner. Designed to be safe under cron: it cd's to its own
# directory, prefers the project venv, and never leaves a half-run behind.
#
#   ./run.sh            print the digest
#   ./run.sh --email    print and email it
set -euo pipefail

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"          # Linux / macOS venv
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"  # Windows venv under Git Bash
else
    PYTHON="$(command -v python3 || command -v python)"
fi

# Load .env if present so cron (which starts with almost no environment) still
# has the SMTP settings. Exported here rather than read by Python so a manual
# `source .env` behaves the same way.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

exec "$PYTHON" jobwatch.py run "$@"
