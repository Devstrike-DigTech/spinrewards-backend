FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN mkdir -p /app/staticfiles && chmod 755 /app/staticfiles

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Collect static at build time — safe because SECRET_KEY is not needed
# with --settings=config.settings.production if you've configured it right.
# The `|| true` tolerates a missing SECRET_KEY during build.
RUN python manage.py collectstatic --noinput --settings=config.settings.production || true

# Ownership so non-root user can write to staticfiles if needed
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

# Shell form via sh -c so $PORT expands at runtime.
# Migrate runs on every startup — idempotent, safe.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120 --log-file -"]