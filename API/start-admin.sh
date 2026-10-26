#!/usr/bin/env bash
# Start the database admin web UI (Flask) on 127.0.0.1:7001. It has no login, so it only listens on this machine.
# From another computer, open an SSH tunnel first: ssh -L 7001:127.0.0.1:7001 <user>@<server>
set -euo pipefail
cd "$(dirname "$0")"
PYTHON=python3
if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; fi
exec "$PYTHON" -m admin
