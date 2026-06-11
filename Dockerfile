# Single image for every deployment role. The role is selected at runtime via
# APP_MODULES (public | admin | scheduler | any comma-separated combination),
# so the same build ships as one all-in-one server or as separate containers.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/app.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /healthz exists in every mode (web and scheduler-only alike).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

# --proxy-headers: trust X-Forwarded-For from the reverse proxy so rate
# limiting and logs see the real client IP. Safe because app ports are never
# published publicly (compose binds them to 127.0.0.1 / internal network).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
