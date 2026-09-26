# =============================================================================
# Gunicorn — configuration de production pour ToutBot Mundo
#   gunicorn --config gunicorn_conf.py ToutBot_Mundo:app
# SQLite ne supporte PAS plusieurs processus écrivains : avec SQLite on garde
# 1 worker + threads. En PostgreSQL (DATABASE_URL défini) on passe en workers
# multiples, activés par TOUTBOT_GUNICORN_WORKERS.
# =============================================================================
import multiprocessing
import os

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '8000')}"
_prod_pg = os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://"))
if _prod_pg:
    workers = int(os.environ.get("TOUTBOT_GUNICORN_WORKERS", 2 * multiprocessing.cpu_count() + 1))
    worker_class = "gthread"
    threads = int(os.environ.get("TOUTBOT_GUNICORN_THREADS", 8))
else:
    workers = 1
    worker_class = "gthread"
    threads = int(os.environ.get("TOUTBOT_GUNICORN_THREADS", 16))

timeout = int(os.environ.get("TOUTBOT_TIMEOUT", 60))
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("TOUTBOT_LOG_LEVEL", "info")
# Format d'accès inspiré JSON (request-id injecté par la couche V10)
access_log_format = ('{"ip":"%(h)s","request":"%(r)s","status":%(s)s,'
                     '"octets":%(b)s,"ms":%(L)s,"agent":"%(a)s","rid":"%(M)s"}')
forwarded_allow_ips = "*" if os.environ.get("TOUTBOT_TRUST_PROXY") == "1" else "127.0.0.1"
proxy_protocol = False
