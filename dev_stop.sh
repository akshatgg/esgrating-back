#!/usr/bin/env bash
# dev_stop.sh — stop the API started by dev_start.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .dev/api.pid ]; then
  pid=$(cat .dev/api.pid)
  pkill -P "$pid" 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
  rm -f .dev/api.pid
  echo "API stopped"
else
  echo "API not running"
fi
