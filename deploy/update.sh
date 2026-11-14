#!/usr/bin/env bash
# Deploy the latest code from GitHub. Run as root:
#   sudo bash /opt/nasa-hotspot-harmonizer/deploy/update.sh
set -euo pipefail
APP=/opt/nasa-hotspot-harmonizer
sudo -u hotspots git -C "$APP" pull --ff-only
sudo -u hotspots "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chmod +x "$APP/deploy/refresh.sh"
cp "$APP/deploy/hotspots.service" "$APP/deploy/hotspots-refresh.service" \
   "$APP/deploy/hotspots-refresh.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl restart hotspots.service
rm -rf /var/cache/nginx/hotspots/*
sleep 5
curl -fsS http://127.0.0.1:8000/api/health && echo
