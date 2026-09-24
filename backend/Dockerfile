FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Python rather than curl: the slim image has no curl, and adding it for one
# probe would cost more than the probe is worth. /health answers without
# touching the database, so "unhealthy" means the process is wedged, not busy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# Port 8000 here, in docker-compose.yml and in .env's SERVER_PORT -- change
# all three together (see docs/DEVELOPMENT.md, Docker).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
