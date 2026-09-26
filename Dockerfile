# =============================================================================
# ToutBot Mundo — image de production (multi-étapes, non-root, Gunicorn)
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

WORKDIR /app

# Dépendances système minimales (build psycopg binaire non requis)
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Dépendances Python (déterministes, versions figées)
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Code applicatif
COPY ToutBot_Mundo.py gunicorn_conf.py ./

# Utilisateur non privilégié + volume de données
RUN useradd --create-home --uid 10001 toutbot \
 && mkdir -p /data && chown -R toutbot:toutbot /app /data
USER toutbot

ENV HOST=0.0.0.0 \
    PORT=8000 \
    TOUTBOT_MODE=production \
    TOUTBOT_DB=/data/toutbot_mundo.db

EXPOSE 8000

# Healthcheck intégré (route /health de l'application)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["gunicorn", "--config", "gunicorn_conf.py", "ToutBot_Mundo:app"]
