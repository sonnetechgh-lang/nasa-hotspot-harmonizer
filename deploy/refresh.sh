#!/usr/bin/env bash
# Daily data refresh, run inside the container by hotspots-refresh.service:
#   docker exec hotspots_app /app/deploy/refresh.sh
# Writes new files next to the old ones and swaps them in; the app picks them up on restart.
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>data/.refresh.lock
flock -n 9 || { echo "refresh already running"; exit 0; }

PY="${PY:-python}"
echo "== download $(date -u +%FT%TZ)"
$PY -m pipeline.download
echo "== normalize"
$PY -m pipeline.normalize
echo "== grid"
$PY -m pipeline.grid
echo "== done $(date -u +%FT%TZ)"
