#!/usr/bin/env bash
# Start the API server (FastAPI) on all interfaces, port 7000, so LAN devices can call it directly.
# The API trusts the X-Student-ID header: anyone who can reach it can act as any student. Test environments only.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON=python3
if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; fi

echo
echo "  API docs on this machine: http://127.0.0.1:7000/docs"
ip -4 -o addr show scope global 2>/dev/null | while read -r _ ifname _ addr _; do
  if [ "$ifname" != lo ]; then echo "  From LAN devices:         http://${addr%/*}:7000/docs   ($ifname)"; fi
done || true
echo "  Uvicorn prints http://0.0.0.0:7000 below - browsers cannot open that address, use the ones above."
echo
exec "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 7000 "$@"
