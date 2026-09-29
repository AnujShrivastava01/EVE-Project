FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cached layer): install only what pyproject.toml declares.
COPY pyproject.toml ./
RUN pip install uv \
    && uv pip install --system -r pyproject.toml

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker/entrypoint.sh ./docker/entrypoint.sh

# Run as an unprivileged user.
RUN chmod +x docker/entrypoint.sh \
    && useradd --system --uid 1001 --no-create-home appuser \
    && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"

# --proxy-headers: trust X-Forwarded-For from the reverse proxy (needed for per-IP rate limits).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
