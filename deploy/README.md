# Self-Hosted Deployment

This repo now supports the intended production shape:

- frontend on Vercel
- backend self-hosted on one VPS
- Caddy terminating TLS on `api.<your-domain>`
- persistent SQLite and branch YAML on the VPS filesystem

## Backend on Vercel

Do not deploy the current backend on Vercel. The backend starts long-lived background tasks and exposes WebSocket endpoints from the same process, so it should run on your VPS instead.

## Files

- `deploy/docker-compose.prod.yml`: production backend + Caddy
- `deploy/Caddyfile`: TLS reverse proxy for `api.<your-domain>`
- `deploy/.env.production.example`: production env template
- `deploy/scripts/backup-sqlite.sh`: nightly SQLite hot backup script
- `deploy/systemd/`: systemd service and timer for backups

## VPS Layout

Create these directories on the server:

```bash
sudo mkdir -p /srv/hypertrade/data/branches
sudo mkdir -p /srv/hypertrade/logs
sudo mkdir -p /srv/hypertrade/backups
sudo mkdir -p /srv/hypertrade/scripts
```

Clone the repo somewhere stable, for example `/srv/hypertrade/app`.

## Backend Deploy

1. Copy `deploy/.env.production.example` to `deploy/.env.production`.
2. Set `API_DOMAIN` to your backend domain, for example `api.example.com`.
3. Set `CORS_ALLOWED_ORIGINS` to your Vercel frontend domains.
4. Fill in any optional secrets you actually use: Telegram, Bybit, wallet, LLM provider.
5. Start the backend:

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

The backend persists data in:

- `/srv/hypertrade/data/data.db`
- `/srv/hypertrade/data/branches`
- `/srv/hypertrade/logs`

## Vercel Frontend

Set these Vercel environment variables:

```bash
NEXT_PUBLIC_API_URL=https://api.example.com
NEXT_PUBLIC_SIGNAL_WS_URL=wss://api.example.com/ws/signals
```

The current frontend already supports this split deployment via `frontend/lib/ws.ts`.

## Backups

Copy the backup script into place and make it executable:

```bash
sudo cp deploy/scripts/backup-sqlite.sh /srv/hypertrade/scripts/backup-sqlite.sh
sudo chmod +x /srv/hypertrade/scripts/backup-sqlite.sh
```

Install the timer:

```bash
sudo cp deploy/systemd/hypertrade-backup.service /etc/systemd/system/
sudo cp deploy/systemd/hypertrade-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hypertrade-backup.timer
```

Run one manual backup to verify:

```bash
sudo /srv/hypertrade/scripts/backup-sqlite.sh
```

Retention defaults:

- 7 daily backups
- 4 weekly backups

## Update Flow

Manual v1 deploy:

```bash
git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

This keeps the backend single-instance and single-worker, which is required while the signal runtime still starts inside FastAPI startup.
