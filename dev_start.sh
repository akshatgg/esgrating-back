#!/usr/bin/env bash
# dev_start.sh — start the API on :8000 in the background
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p .dev
if ! nc -z 127.0.0.1 27017 2>/dev/null; then
  echo "MongoDB not reachable on 27017 — starting via brew services"
  brew services start mongodb-community >/dev/null || true
  for _ in $(seq 1 20); do nc -z 127.0.0.1 27017 && break; sleep 1; done
fi
[ -f .env ] || { echo "Missing .env — copy .env.example and fill it"; exit 1; }
if [ -f .dev/api.pid ] && kill -0 "$(cat .dev/api.pid)" 2>/dev/null; then
  echo "API already running (pid $(cat .dev/api.pid))"; exit 0
fi
uv sync --quiet
nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload > .dev/api.log 2>&1 &
echo $! > .dev/api.pid
for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:8000/api/health >/dev/null && { echo "API ready → http://localhost:8000/api/health  (log: .dev/api.log)"; exit 0; }
  sleep 0.5
done
echo "API did not become healthy — see .dev/api.log"; exit 1
