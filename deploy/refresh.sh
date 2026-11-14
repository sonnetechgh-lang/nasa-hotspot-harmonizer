#!/usr/bin/env bash
# Daily refresh. Run by hotspots-refresh.service; safe to run by hand as the hotspots user:
#   sudo -u hotspots /opt/nasa-hotspot-harmonizer/deploy/refresh.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec 9>data/.refresh.lock
flock -n 9 || { echo "refresh already running"; exit 0; }

PY=.venv/bin/python
echo "== download $(date -u +%FT%TZ)"
$PY -m pipeline.download
echo "== normalize"
$PY -m pipeline.normalize
echo "== grid"
$PY -m pipeline.grid
echo "== done $(date -u +%FT%TZ)"
