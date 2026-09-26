# ToutBot Mundo — Dossier de déploiement unifié (V10, production)

> Ce fichier rassemble en un seul document, pour référence et partage, tout ce
> qui était réparti dans `requirements.txt`, `Dockerfile`, `docker-compose.yml`,
> `gunicorn_conf.py`, `nginx.conf`, `RUNBOOK_DEPLOIEMENT.md`, `recette_v10.py`
> et `recette.log`.
>
> **Ces fichiers restent nécessaires séparément pour un déploiement réel** :
> Docker, Docker Compose, Nginx, Gunicorn et pip lisent chacun leur propre
> format (Dockerfile, YAML, conf Nginx, Python, texte) — on ne peut pas les
> exécuter fusionnés en un seul fichier. Ce document est la vue d'ensemble ;
> gardez les fichiers d'origine tels quels dans votre dépôt.

---

## 0. Vue d'ensemble de la pile

```
Internet ──▶ nginx (TLS, en-têtes sécu, anti-DoS) ──▶ web (Gunicorn + Flask)
                                                        │
                                                        ├──▶ db (PostgreSQL)
                                                        └──▶ cache (Redis)
```

Démarrage complet : `cp .env.example .env && docker compose up -d --build`.

---

## 1. Dépendances Python — `requirements.txt`

```text
# =============================================================================
# ToutBot Mundo — dépendances déterministes (déploiement production)
# Installation :  pip install -r requirements.txt
# =============================================================================

# --- Coeur application -------------------------------------------------------
Flask==3.0.3
Werkzeug==3.0.6
Jinja2==3.1.4
itsdangerous==2.2.0
click==8.1.7
blinker==1.8.2

# --- Serveur d'application (remplace app.run / serveur de développement) -----
gunicorn==23.0.0
waitress==3.0.2

# --- Limitation de débit / cache partagé ------------------------------------
Flask-Limiter==3.8.0
limits==3.13.0
redis==5.0.8

# --- Base de données de production (PostgreSQL) ------------------------------
psycopg[binary]==3.2.3

# --- Extensions optionnelles déclarées (repli codé si absentes) --------------
flask-sock==0.7.0
pywebpush==2.0.0
py-vapid==1.9.1
openpyxl==3.1.5

# --- Configuration / secrets -------------------------------------------------
python-dotenv==1.0.1

# --- Observabilité (optionnel) ----------------------------------------------
sentry-sdk[flask]==2.19.2
```

*(reçu deux fois à l'identique dans le dernier envoi — un seul exemplaire est repris ici.)*

---

## 2. Image de production — `Dockerfile`

```dockerfile
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
```

---

## 3. Orchestration complète — `docker-compose.yml`

```yaml
# =============================================================================
# ToutBot Mundo — pile de production complète
#   web (Gunicorn) + db (PostgreSQL) + cache (Redis) + nginx (TLS/HTTPS)
# Démarrage :  cp .env.example .env && docker compose up -d --build
# =============================================================================
version: "3.9"

services:
  web:
    build: .
    restart: unless-stopped
    env_file: [.env]
    environment:
      HOST: 0.0.0.0
      PORT: "8000"
      DATABASE_URL: postgresql://toutbot:${POSTGRES_PASSWORD:-toutbot}@db:5432/toutbot
      REDIS_URL: redis://cache:6379/0
      TOUTBOT_MODE: production
    depends_on:
      db:
        condition: service_healthy
      cache:
        condition: service_started
    volumes:
      - donnees_app:/data
    expose: ["8000"]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: toutbot
      POSTGRES_USER: toutbot
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-toutbot}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U toutbot -d toutbot"]
      interval: 10s
      timeout: 5s
      retries: 10

  cache:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    volumes:
      - redisdata:/data

  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    depends_on: [web]
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs:/etc/nginx/certs:ro
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1/health"]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  donnees_app:
  pgdata:
  redisdata:
```

---

## 4. Serveur d'application — `gunicorn_conf.py`

```python
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
```

---

## 5. Reverse proxy — `nginx.conf`

```nginx
# =============================================================================
# ToutBot Mundo — reverse proxy Nginx (TLS, HSTS, anti-DoS, proxy WebSocket)
# À placer dans /etc/nginx/conf.d/default.conf (ou le dossier des sites).
# =============================================================================

# Zones de limitation de débit (deuxième rideau devant celui de l'application)
limit_req_zone $binary_remote_addr zone=tbm_global:10m rate=240r/m;
limit_req_zone $binary_remote_addr zone=tbm_auth:10m   rate=10r/m;
limit_conn_zone $binary_remote_addr zone=tbm_conn:10m;

# Redirection HTTP -> HTTPS
server {
    listen 80;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    http2 on;
    server_name _;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;

    # En-têtes de sécurité (l'application les pose aussi : ceinture + bretelles)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;

    # Application 100 % TEXTUELLE : aucune pièce jointe attendue
    client_max_body_size 8m;
    client_body_timeout 15s;
    limit_conn tbm_conn 20;

    # Gunicorn derrière le proxy
    location / {
        limit_req zone=tbm_global burst=60 nodelay;
        proxy_pass         http://web:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_read_timeout 65s;
    }

    # Routes d'authentification : limitation serrée (brute-force)
    location ~ ^/(connexion|inscription|admin/inscription) {
        limit_req zone=tbm_auth burst=5 nodelay;
        proxy_pass         http://web:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    # Webhooks Mobile Money : jamais limités (les agrégateurs doivent rappeler)
    location /webhooks/ {
        proxy_pass         http://web:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location = /health { proxy_pass http://web:8000/health; access_log off; }
    location = /ready  { proxy_pass http://web:8000/ready;  access_log off; }
}
```

---

## 6. Runbook d'exploitation

# ToutBot Mundo — Runbook d'exploitation (production)

> Version applicative : **10.0** · Base : PostgreSQL (production) / SQLite (développement)
> Serveur d'application : **Gunicorn** (`gthread`) derrière **Nginx** (TLS)

---

## 1. Démarrage local (développement)

```bash
pip install -r requirements.txt
export TOUTBOT_MODE=dev
export TOUTBOT_ADMIN_BOOTSTRAP_TOKEN=$(python -c "import secrets;print(secrets.token_hex(24))")
python ToutBot_Mundo.py --port 8099
# → http://127.0.0.1:8099/admin/inscription   (création du premier administrateur)
```

En mode `dev`, si `SECRET_KEY` est absente, une clé est générée ; **aucun cookie
`Secure`** n'est posé (le HTTPS local n'est pas supposé actif).

## 2. Démarrage en production

```bash
cp .env.example .env       # puis renseigner SECRET_KEY, DATABASE_URL, REDIS_URL…
docker compose up -d --build
docker compose logs -f web
```

Sans `docker`, en direct :

```bash
export TOUTBOT_MODE=production
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
export DATABASE_URL=postgresql://toutbot:mdp@db:5432/toutbot
export REDIS_URL=redis://cache:6379/0
gunicorn --config gunicorn_conf.py ToutBot_Mundo:app
```

**Fail-fast** : avec `TOUTBOT_MODE=production` et sans `SECRET_KEY`, le processus
**refuse de démarrer** (aucune valeur de secours n'est tolérée).

## 3. Première entrée de l'administrateur

1. Renseigner `TOUTBOT_ADMIN_BOOTSTRAP_TOKEN` dans l'environnement.
2. Ouvrir `/admin/inscription` : pseudo unique (6 lettres) + mot de passe.
3. Cette voie se **ferme définitivement** dès qu'un administrateur existe.
   Toute tentative ultérieure est renvoyée vers `/connexion`.
4. Un jeton d'amorçage erroné entraîne le **bannissement 30 min** de l'IP appelante.

## 4. Sonde de santé / supervision

| Route | Rôle | Code |
|---|---|---|
| `/health` | vivacité + base joignable | 200 / 503 |
| `/ready` | schéma complet + Redis + HTTPS | 200 / 503 |
| `/api/v10/statut` | état public des services | 200 |

Brancher un vérificateur externe (UptimeRobot, Better Stack…) sur `/health`,
et l'orchestrateur (Docker/K8s) sur les deux routes.

## 5. Sauvegardes

* **Automatique** : un fil d'arrière-plan écrit une sauvegarde JSON toutes les
  24 h dans `TOUTBOT_BACKUP_DIR` (défaut `./sauvegardes`), avec rotation à 30 fichiers.
* **Manuelle** : bouton « Sauvegarder maintenant » (palette `Ctrl/⌘+K`) → `POST /admin/v10/sauvegarder`.
* **Restauration** : arrêter le service, restaurer la base, redémarrer. Toute
  restauration doit être consignée dans le journal d'audit.

## 6. Webhooks Mobile Money

| Fournisseur | URL à déclarer | En-tête attendu |
|---|---|---|
| MTN | `https://votre-domaine/webhooks/mtn` | `X-ToutBot-Signature: <hmac-sha256 hex du corps>` |
| Moov | `https://votre-domaine/webhooks/moov` | idem |

* Signature = `HMAC_SHA256(TOUTBOT_WEBHOOK_SECRET_*, corps_brut)`, hexadécimal.
* **Sans secret configuré, aucun webhook n'est accepté** (503 explicite) : l'application
  ne présente jamais un paiement comme vérifié s'il ne l'est pas.
* Référence reconnue : `TIP-<id>` → pourboire, sinon → abonnement.
* **Double signature** : tout montant ≥ `TOUTBOT_DOUBLE_SIGNATURE_FCFA`
  (500 000 par défaut) exige **deux approbations d'administrateurs distincts**
  sur `/admin/v10/reglements`.
* Les routes `/webhooks/` sont exemptées de CSRF et de limitation de débit
  (les agrégateurs doivent pouvoir rappeler), mais **jamais** de vérification de signature.

## 7. Incidents courants

| Symptôme | Cause probable | Action |
|---|---|---|
| Refus de démarrage | `SECRET_KEY` absente en production | générer et exporter la clé |
| `429` en rafale | limitation de débit (10/min routes sensibles, 240/min global) | vérifier le trafic, ajuster `TOUTBOT_RATE_*` |
| `403` sur `/connexion` | IP bannie (brute-force) | attendre la fin du ban ou vider `tbm:ban:*` dans Redis |
| `503` sur `/webhooks/*` | secret de webhook absent | renseigner `TOUTBOT_WEBHOOK_SECRET_MTN/MOOV` |
| `400 csrf_invalide` | session expirée ou page ancienne | recharger la page (le jeton est réinjecté) |
| `database is locked` (SQLite) | écritures concurrentes | migrer sur PostgreSQL (`DATABASE_URL`) |

## 8. Journalisation

* Format JSON par défaut en production (`TOUTBOT_LOG_JSON=1`), chaque ligne porte
  un `X-Request-ID` renvoyé au client : corréler avec les journaux Nginx.
* Les champs sensibles (mot de passe, jeton, secret, CSRF) sont **masqués** avant écriture.
* Rétention : à piloter au niveau de l'hôte (logrotate / collecteur).

## 9. Mise à jour sans coupure

```bash
git pull && docker compose build web
docker compose up -d --no-deps web     # recréation progressive du service web
curl -fsS https://votre-domaine/ready  # vérifier avant de purger Nginx
```

---

## 7. Recette V10 — état de la dernière exécution

`recette_v10.py` vérifie la couche V10 (CSRF, en-têtes de sécurité, service
worker, santé/disponibilité, webhooks signés, anti brute-force, historique
personnel…) contre une base SQLite temporaire, réseau non sollicité.

**`recette.log` montre que l'exécution s'est arrêtée avant la fin**, sur ce
traceback :

```text
✅ /api/v10/statut expose la version — 10.0
✅ webhook sans secret configuré → refus explicite (503) — 503
✅ webhook à signature invalide → 401 — 401
✅ webhook correctement signé accepté — {"ok":true,"reglement":{"double_signature_requise":false,"execution":{"motif":"deja_valide_ou_inconnu","ok":false},"ok":true,"reference":"TIP-999999","statut":"
Traceback (most recent call last):
  File "/home/user/tb/recette_v10.py", line 175, in <module>
    ligne_webhook = base("SELECT COUNT(*) AS n FROM reglements_v10")[0]["n"]
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: 'str' object is not callable
```

**Cause** (repérée en confrontant le script et le journal) : à la ligne 148 de
`recette_v10.py`, la variable `base` est réaffectée pour un autre usage :

```python
base = T.TEMPLATES["base.html"]          # écrase la fonction base() définie plus haut
...
ligne_webhook = base("SELECT COUNT(*) AS n FROM reglements_v10")[0]["n"]   # ligne 175 : base n'est plus la fonction SQL
```

`base` désigne d'abord une fonction d'accès SQLite (définie en tête de
fichier), puis est réutilisée comme nom de variable pour le gabarit HTML
`base.html` — la fonction est alors perdue, d'où `TypeError: 'str' object is
not callable` au premier appel SQL suivant. Les contrôles jusqu'à la ligne 174
inclusivement sont donc bien validés (tous en ✅ dans le journal) ; seule la
vérification finale du règlement webhook en base n'a pas pu s'exécuter.

**Correction minimale** : renommer l'une des deux variables, par exemple :

```python
gabarit_base = T.TEMPLATES["base.html"]
for marque in (...):
    verifier("interface : marqueur « %s »" % marque, marque in gabarit_base or marque in sw)
```

Dites-moi si vous voulez que je vous livre `recette_v10.py` corrigé et
relancé jusqu'au bout.

---

## 8. Aide-mémoire des commandes

```bash
# Développement local
pip install -r requirements.txt
python ToutBot_Mundo.py --port 8099

# Recette (réseau simulé)
python recette_v10.py

# Production
cp .env.example .env
docker compose up -d --build
docker compose logs -f web
curl -fsS https://votre-domaine/health
curl -fsS https://votre-domaine/ready

# Mise à jour sans coupure
git pull && docker compose build web
docker compose up -d --no-deps web
```
