# Deploying to a VPS (behind Cloudflare)

```
visitor ──HTTPS──> Cloudflare (free proxy, CDN, DDoS) ──HTTPS (origin cert)──> nginx :443
                                                                                 │  caches GET /api/ for 6 h
                                                                                 ▼
                                                                     uvicorn 127.0.0.1:8000  (hotspots.service)
                                                                                 ▲
                                    hotspots-refresh.timer, daily 06:17 UTC ─────┘  download → normalize → grid → restart
```

Assumes Ubuntu 24.04 or Debian 12+ (Python ≥ 3.11), about 2 GB RAM and 2 GB free disk.
Replace `fires.example.com` with your domain and `user@vps` with your SSH login.

## 1. Cloudflare

1. Add the domain to Cloudflare (free plan) and point its nameservers there.
2. **DNS:** an `A` record for `fires` → the VPS IP, **Proxied** (orange cloud).
3. **SSL/TLS → Overview:** mode **Full (strict)**.
4. **SSL/TLS → Origin Server → Create certificate** (default: RSA, 15 years). Keep the page open:
   you paste the certificate and the private key in step 3.

## 2. Copy the data up (from your PC)

The data isn't in git (~440 MB). Copying it is much faster than downloading it all again from FIRMS.

```powershell
cd D:\Code\nasa-hotspot-harmonizer
tar -czf $env:TEMP\hotspots-data.tgz data
scp $env:TEMP\hotspots-data.tgz user@vps:/tmp/
```

## 3. On the VPS

```bash
# code + system packages + services (prints what's still missing)
sudo apt-get install -y git
sudo git clone https://github.com/sonnetechgh-lang/nasa-hotspot-harmonizer.git /opt/nasa-hotspot-harmonizer
sudo bash /opt/nasa-hotspot-harmonizer/deploy/setup.sh fires.example.com

# data
sudo tar -xzf /tmp/hotspots-data.tgz -C /opt/nasa-hotspot-harmonizer
sudo chown -R hotspots:hotspots /opt/nasa-hotspot-harmonizer/data
rm /tmp/hotspots-data.tgz

# FIRMS key (for the daily refresh)
sudo nano /opt/nasa-hotspot-harmonizer/.env          # set FIRMS_MAP_KEY=...

# Cloudflare origin certificate from step 1.4
sudo nano /etc/ssl/cloudflare/origin.pem             # paste the certificate
sudo nano /etc/ssl/cloudflare/origin.key             # paste the private key
sudo chmod 600 /etc/ssl/cloudflare/origin.key

# run setup again: now it starts the app, the timer and nginx
sudo bash /opt/nasa-hotspot-harmonizer/deploy/setup.sh fires.example.com
```

## 4. Check it

```bash
curl -s http://127.0.0.1:8000/api/health             # {"ok":true,"data_last_day":"..."}
curl -sI https://fires.example.com/api/meta | grep -i -e "^HTTP" -e x-cache   # 200, MISS then HIT
systemctl list-timers hotspots-refresh.timer         # next run
```

Then open https://fires.example.com in a browser.

## Day to day

| Task | Command |
|---|---|
| Deploy new code (after `git push`) | `sudo bash /opt/nasa-hotspot-harmonizer/deploy/update.sh` |
| Refresh data now | `sudo systemctl start hotspots-refresh.service` |
| Refresh log | `journalctl -u hotspots-refresh -n 50` |
| App log | `journalctl -u hotspots -f` |
| Restart the app | `sudo systemctl restart hotspots` |
| Recalibrate (only on purpose) | `sudo -u hotspots /opt/nasa-hotspot-harmonizer/.venv/bin/python -m pipeline.calibrate`, then refresh |

How the refresh works: if any step fails, the app keeps serving yesterday's data and nothing restarts.
Files are written to a temp name and swapped in, so the live app never reads a half-written file.
During the restart (a few seconds) nginx serves cached API answers.

## Optional: only let Cloudflare reach the origin

```bash
sudo ufw allow OpenSSH
for ip in $(curl -s https://www.cloudflare.com/ips-v4) $(curl -s https://www.cloudflare.com/ips-v6); do
  sudo ufw allow from $ip to any port 443 proto tcp
done
sudo ufw enable
```
