# Getting Started — Spin Rewards Backend

This guide covers everything you need to get the backend running locally, whether you prefer Docker (recommended) or a manual Python setup.

---

## Prerequisites

| Tool | Minimum Version | Notes |
|---|---|---|
| Python | 3.11+ | Only needed for manual setup |
| Docker | 24+ | Recommended path |
| Docker Compose | 2.x | Included with Docker Desktop |
| PostgreSQL | 15+ | Only needed for manual setup |
| Redis | 7+ | Only needed for manual setup |
| Git | Any | |

---

## Option A — Docker (Recommended)

Docker handles PostgreSQL, Redis, the Django API server, and the Celery worker automatically. No local database or Redis install required.

### Step 1 — Clone and enter the project

```bash
git clone <your-repo-url>
cd backend
```

### Step 2 — Create your environment file

```bash
cp .env.example .env
```

Open `.env` and fill in the required values. See [Environment Variables](./environment-variables.md) for a full explanation of every variable.

At minimum, you must set:
- `DJANGO_SECRET_KEY` — any long random string for local dev
- `TELEGRAM_BOT_TOKEN` — from BotFather
- `ENCRYPTION_KEY` — generate with the command below
- `PAYSTACK_SECRET_KEY` — your Paystack test key

Generate an encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Step 3 — Start all services

```bash
docker-compose up --build
```

This starts:
- `db` — PostgreSQL on port 5432
- `redis` — Redis on port 6379
- `api` — Django dev server on port 8000 (auto-migrates on start)
- `worker` — Celery worker for async tasks

First boot takes ~60 seconds while dependencies install. Subsequent starts are fast.

### Step 4 — Verify it's running

```bash
curl http://localhost:8000/api/v1/auth/telegram/
# Should return 405 Method Not Allowed (GET on a POST endpoint) — server is up
```

### Step 5 — Create a superuser (optional, for Django admin)

```bash
docker-compose exec api python manage.py createsuperuser
```

Then visit `http://localhost:8000/django-admin/`

### Common Docker Commands

```bash
# Start in background
docker-compose up -d

# View logs
docker-compose logs -f api
docker-compose logs -f worker

# Stop all services
docker-compose down

# Stop and delete all data (wipe database)
docker-compose down -v

# Run a management command
docker-compose exec api python manage.py <command>

# Open a shell inside the API container
docker-compose exec api bash

# Run migrations manually
docker-compose exec api python manage.py migrate

# Create a new migration after model changes
docker-compose exec api python manage.py makemigrations
```

---

## Option B — Manual Setup (without Docker)

Use this if you prefer to manage PostgreSQL and Redis yourself.

### Step 1 — Clone and create virtual environment

```bash
git clone <your-repo-url>
cd backend
python -m venv venv
source venv/bin/activate      # macOS/Linux
# venv\Scripts\activate       # Windows
```

### Step 2 — Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3 — Set up PostgreSQL

Create a database and user:
```sql
CREATE DATABASE spinrewards;
CREATE USER spinrewards_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE spinrewards TO spinrewards_user;
```

### Step 4 — Set up Redis

Install and start Redis:
```bash
# macOS
brew install redis && brew services start redis

# Ubuntu/Debian
sudo apt install redis-server && sudo systemctl start redis
```

### Step 5 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your local database credentials and other values.

### Step 6 — Run migrations

```bash
python manage.py migrate
```

### Step 7 — Start the development server

```bash
python manage.py runserver
```

### Step 8 — Start Celery (in a separate terminal)

```bash
celery -A config.celery worker --loglevel=info
```

---

## Running Tests

```bash
# Docker
docker-compose exec api python manage.py test

# Manual
python manage.py test
```

---

## Next Steps

- [Environment Variables Reference](./environment-variables.md)
- [Architecture Overview](./architecture.md)
- [API Reference](./api-reference.md)
- [Deployment Guide](./deployment.md)
