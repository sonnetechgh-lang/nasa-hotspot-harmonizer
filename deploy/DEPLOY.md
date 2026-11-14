# Deploying to the VPS

The VPS already runs a shared **Caddy** container on ports 80/443 (network `proxy`, config in
`/opt/apps/proxy/Caddyfile`). It gets HTTPS certificates automatically. This app joins it as one more container:

```
visitor ──HTTPS──> caddy (Let's Encrypt) ──> hotspots_app:8000 (Docker network "proxy")
                                                    ▲  data/ and .env mounted from /opt/apps/hotspots
hotspots-refresh.timer (host, daily 06:17 UTC) ─────┘  docker exec refresh.sh → docker restart
```

## 1. DNS (Spaceship, 10xvibecode.org)

Add an `A` record `harmonizer` → `187.7.21.43` (TTL 60 s). Wait until
`nslookup harmonizer.10xvibecode.org` returns that IP before step 4, or Caddy can't get a certificate.

## 2. Code + data

```bash
# on the VPS
git clone https://github.com/sonnetechgh-lang/nasa-hotspot-harmonizer.git /opt/apps/hotspots
```

```powershell
# on the PC: data isn't in git (~440 MB)
cd D:\Code\nasa-hotspot-harmonizer
tar -czf $env:TEMP\hotspots-data.tgz data
scp $env:TEMP\hotspots-data.tgz root@187.7.21.43:/tmp/
```

```bash
# on the VPS
cd /opt/apps/hotspots
tar -xzf /tmp/hotspots-data.tgz && rm /tmp/hotspots-data.tgz
cp .env.example .env && nano .env      # FIRMS_MAP_KEY=...
chmod 600 .env
chown -R 1000:1000 data .env           # the container runs as uid 1000
```

## 3. Start the container

```bash
cd /opt/apps/hotspots
docker compose up -d --build
docker exec hotspots_app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read())"
```

## 4. Caddy

```bash
cp /opt/apps/proxy/Caddyfile /opt/apps/proxy/Caddyfile.bak-$(date +%F)
cat deploy/Caddyfile.snippet | sed '/^#/d' >> /opt/apps/proxy/Caddyfile
docker exec caddy caddy validate --config /etc/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
curl -s https://harmonizer.10xvibecode.org/api/health
```

## 5. Daily refresh

```bash
cp deploy/hotspots-refresh.service deploy/hotspots-refresh.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now hotspots-refresh.timer
systemctl list-timers hotspots-refresh.timer
```

## Day to day

| Task | Command (in `/opt/apps/hotspots`) |
|---|---|
| Deploy new code (after `git push`) | `git pull && docker compose up -d --build` |
| Refresh data now | `systemctl start hotspots-refresh.service` |
| Refresh log | `journalctl -u hotspots-refresh -n 50` |
| App log | `docker logs -f hotspots_app` |
| Recalibrate (only on purpose) | `docker exec hotspots_app python -m pipeline.calibrate`, then refresh |

If a refresh step fails, the app keeps serving yesterday's data and nothing restarts. New files are written to a
temp name and swapped in, so the live app never reads a half-written file. During the restart Caddy holds
requests (up to 30 s) instead of failing them.
