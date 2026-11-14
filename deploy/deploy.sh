#!/usr/bin/env bash
# Deploy the latest main to the VPS. Run by GitHub Actions over SSH: the deploy key in
# /root/.ssh/authorized_keys is locked to this script (command="...",restrict), so it can do nothing else.
# Also fine to run by hand:  bash /opt/apps/hotspots/deploy/deploy.sh
set -euo pipefail
APP=/opt/apps/hotspots
IMAGE=hotspot-harmonizer
cd "$APP"

# Never rebuild in the middle of a data refresh: wait for the refresh lock (shared with the container)
exec 9>data/.refresh.lock
echo "== waiting for any running data refresh"
flock -w 900 9 || { echo "refresh still running after 15 min, giving up"; exit 1; }

echo "== update code"
before=$(git rev-parse HEAD)
git fetch -q origin main
git reset -q --hard origin/main      # .env and data/ are ignored, so they are kept
after=$(git rev-parse HEAD)
echo "   $before -> $after"
git log -1 --format='   %h %s'

# Re-install the refresh timer if it changed
if ! cmp -s deploy/hotspots-refresh.service /etc/systemd/system/hotspots-refresh.service \
   || ! cmp -s deploy/hotspots-refresh.timer /etc/systemd/system/hotspots-refresh.timer; then
  echo "== update refresh timer"
  cp deploy/hotspots-refresh.service deploy/hotspots-refresh.timer /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable -q --now hotspots-refresh.timer
fi

echo "== build"
docker image inspect "$IMAGE:latest" >/dev/null 2>&1 && docker tag "$IMAGE:latest" "$IMAGE:previous"
nice docker compose build -q

echo "== start"
docker compose up -d --no-build
flock -u 9   # the new container may refresh again

healthy() {
  for _ in $(seq 60); do
    s=$(docker inspect -f '{{.State.Health.Status}}' hotspots_app 2>/dev/null || echo missing)
    [ "$s" = healthy ] && return 0
    [ "$s" = unhealthy ] && return 1
    sleep 3
  done
  return 1
}

if healthy; then
  echo "== healthy: $(docker exec caddy wget -qO- http://hotspots_app:8000/api/health)"
  docker image prune -f >/dev/null
  echo "== deployed $after"
else
  echo "!! new container is not healthy, last log lines:"
  docker logs --tail 30 hotspots_app 2>&1 || true
  if docker image inspect "$IMAGE:previous" >/dev/null 2>&1; then
    echo "!! rolling back to the previous image"
    docker tag "$IMAGE:previous" "$IMAGE:latest"
    docker compose up -d --no-build --force-recreate
    healthy && echo "   rollback healthy" || echo "   rollback NOT healthy"
  fi
  exit 1
fi
