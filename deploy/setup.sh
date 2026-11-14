#!/usr/bin/env bash
# One-time VPS setup (Ubuntu 24.04 / Debian 12+). Run as root:
#   sudo bash deploy/setup.sh fires.example.com
# Safe to re-run: it updates code, packages and config.
set -euo pipefail
DOMAIN="${1:?usage: setup.sh <domain>}"
APP=/opt/nasa-hotspot-harmonizer
REPO=https://github.com/sonnetechgh-lang/nasa-hotspot-harmonizer.git

apt-get update -q
apt-get install -y -q git nginx python3 python3-venv curl

id hotspots >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin hotspots

if [ ! -d "$APP/.git" ]; then
  git clone -q "$REPO" "$APP"
fi
mkdir -p "$APP/data"
chown -R hotspots:hotspots "$APP"
git config --system --add safe.directory "$APP" 2>/dev/null || true
sudo -u hotspots git -C "$APP" pull -q --ff-only

sudo -u hotspots python3 -m venv "$APP/.venv"
sudo -u hotspots "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u hotspots "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chmod +x "$APP/deploy/refresh.sh"

if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  echo ">> Edit $APP/.env and add FIRMS_MAP_KEY"
fi
chown hotspots:hotspots "$APP/.env"
chmod 600 "$APP/.env"

# Cloudflare IP ranges, so nginx logs show real visitor IPs
{
  for ip in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do
    echo "set_real_ip_from $ip;"
  done
  echo "real_ip_header CF-Connecting-IP;"
} > /etc/nginx/snippets/cloudflare-realip.conf

mkdir -p /var/cache/nginx/hotspots /etc/ssl/cloudflare
sed "s/DOMAIN/$DOMAIN/g" "$APP/deploy/nginx-hotspots.conf" > /etc/nginx/sites-available/hotspots
ln -sf /etc/nginx/sites-available/hotspots /etc/nginx/sites-enabled/hotspots
rm -f /etc/nginx/sites-enabled/default

cp "$APP/deploy/hotspots.service" "$APP/deploy/hotspots-refresh.service" \
   "$APP/deploy/hotspots-refresh.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q hotspots.service hotspots-refresh.timer

if [ -f "$APP/data/processed/grid_daily.parquet" ]; then
  systemctl restart hotspots.service
  systemctl start hotspots-refresh.timer
else
  echo ">> No data yet: upload data/ (see deploy/DEPLOY.md), then run setup.sh again"
fi

if [ -f /etc/ssl/cloudflare/origin.pem ]; then
  nginx -t && systemctl reload nginx
else
  echo ">> Put the Cloudflare Origin Certificate in /etc/ssl/cloudflare/origin.pem and origin.key, then run setup.sh again"
fi
echo "Setup done for $DOMAIN"
