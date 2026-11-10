FROM python:3.12-slim

ENV PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py catalog_client.py db.py loans.py migrate.py ./
COPY migrations/ migrations/

RUN useradd --system --no-create-home appuser
USER appuser

EXPOSE 8000

# Migrations run once before the server starts; at most one replica runs, so they never run concurrently.
CMD ["sh", "-c", "python migrate.py && exec gunicorn --workers 1 --threads 4 --timeout 120 --no-control-socket --bind 0.0.0.0:${PORT} app:app"]
