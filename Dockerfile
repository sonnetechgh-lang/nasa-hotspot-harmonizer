FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY api ./api
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY web ./web
COPY deploy/refresh.sh ./deploy/refresh.sh

# data/ and .env are mounted at run time (see compose.yml)
RUN useradd --uid 1000 --create-home app && mkdir -p data && chown -R app:app /app && chmod +x deploy/refresh.sh
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# One worker: the app keeps its data and caches in memory. Only Caddy (same Docker network) reaches it.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
