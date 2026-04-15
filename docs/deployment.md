# Deployment Guide

This guide covers deploying the backend to a cloud platform (Railway or Render) using Docker.

---

## Platform Recommendation

| Platform | Notes |
|---|---|
| **Railway** | Recommended. Native Docker support, managed Postgres and Redis add-ons, easy env var management |
| **Render** | Good alternative. Free tier available, Docker support, managed Postgres |
| **VPS (Ubuntu)** | Full control. Use Docker Compose with Nginx as a reverse proxy |

---

## Pre-Deployment Checklist

Before deploying to production, verify:

- [ ] `DJANGO_DEBUG=False`
- [ ] `DJANGO_SECRET_KEY` is a long, random, unique value
- [ ] `DJANGO_ALLOWED_HOSTS` includes your production domain
- [ ] `CORS_ALLOWED_ORIGINS` is set to exact Mini App and Admin Dashboard URLs
- [ ] `ENCRYPTION_KEY` is securely backed up — losing it means losing all encrypted NINs
- [ ] `PAYSTACK_SECRET_KEY` is a live key (`sk_live_...`), not test
- [ ] `FLUTTERWAVE_SECRET_HASH` is set
- [ ] Paystack webhook URL is registered: `https://api.spinrewards.com/api/v1/webhooks/paystack/`
- [ ] Flutterwave webhook URL is registered: `https://api.spinrewards.com/api/v1/webhooks/flutterwave/`
- [ ] Database is managed PostgreSQL (not the Docker container)
- [ ] Redis is managed Redis (not the Docker container)
- [ ] `DJANGO_SETTINGS_MODULE=config.settings.production`

---

## Railway Deployment

### Step 1 — Create a new project

1. Go to [railway.app](https://railway.app) and create a new project
2. Select "Deploy from GitHub repo"
3. Connect your backend repository

### Step 2 — Add services

From the Railway dashboard, add:
- **PostgreSQL** plugin → Railway auto-sets `DATABASE_URL`
- **Redis** plugin → Railway auto-sets `REDIS_URL`

### Step 3 — Set environment variables

In Railway → your service → Variables, add all production env vars from `.env.example`.

For `DB_HOST`, `DB_NAME`, etc., Railway provides these automatically when you attach the PostgreSQL plugin. You can reference them with `${{Postgres.PGHOST}}` etc., or use the `DATABASE_URL` connection string directly.

Update `config/settings/base.py` to support `DATABASE_URL` if using Railway's auto-variables:
```python
import dj_database_url
DATABASES = {'default': dj_database_url.config(default=config('DATABASE_URL', default=None)) or { ... }}
```

### Step 4 — Set start command

In Railway service settings → Start Command:
```
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

### Step 5 — Add Celery worker

Add a second Railway service from the same repo with start command:
```
celery -A config.celery worker --loglevel=warning --concurrency=2
```

Set `DJANGO_SETTINGS_MODULE=config.settings.production` on both services.

### Step 6 — Set custom domain

In Railway → Settings → Domains, add `api.spinrewards.com`. Then add a CNAME record in Cloudflare pointing to your Railway domain.

### Step 7 — Run migrations

Use Railway's one-time job feature or exec into the container:
```bash
railway run python manage.py migrate
```

---

## Render Deployment

Similar to Railway. Key differences:

- Render uses a `render.yaml` file for service definition
- Free tier PostgreSQL has a 90-day expiry — use paid tier for production
- Set `PYTHON_VERSION=3.11` in the Render environment

---

## VPS / Self-Hosted Deployment

### Requirements

- Ubuntu 22.04 LTS
- Docker and Docker Compose installed
- Nginx as reverse proxy
- Certbot for SSL

### Setup

```bash
# Clone repo
git clone <your-repo> /opt/spinrewards
cd /opt/spinrewards/backend

# Set env vars
cp .env.example .env
nano .env  # fill in production values

# Start with production compose
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Run migrations
docker-compose exec api python manage.py migrate

# Collect static
docker-compose exec api python manage.py collectstatic --noinput
```

### Nginx Configuration

```nginx
server {
    listen 443 ssl;
    server_name api.spinrewards.com;

    ssl_certificate /etc/letsencrypt/live/api.spinrewards.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.spinrewards.com/privkey.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Post-Deployment Verification

After deployment:

1. Hit the health check: `curl https://api.spinrewards.com/api/v1/auth/telegram/` → should return 405
2. Test Paystack webhook delivery from the Paystack dashboard
3. Test a full deposit flow with a small test amount
4. Check Celery worker is processing tasks (look for notification tasks in logs)
5. Verify admin panel: `https://admin.spinrewards.com` loads and can log in
