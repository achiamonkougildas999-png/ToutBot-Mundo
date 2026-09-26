# =============================================================================
# COUCHE V10 : SECRET_KEY obligatoire en production, cookies durcis, logging JSON + request-id
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 12310 a 12420
# Code extrait tel quel (aucune modification).
# =============================================================================

    LOGGER.warning("V9 : initialisation partielle", exc_info=True)



import csv, hmac, hashlib, io, shutil, sqlite3, threading  # V10 : imports défensifs de la couche
# =============================================================================
# COUCHE V10 — DURCISSEMENT PRODUCTION, CLICABILITÉ TOTALE, HISTORIQUE PERSONNEL
# -----------------------------------------------------------------------------
# Cette couche est FUSIONNÉE dans le mono-fichier (elle s'insère juste avant le
# point d'entrée) et prend l'AUTORITÉ sur les blocs d'origine. Elle apporte :
#   1.  Correctif de la récursion infinie de marquer_paye (RecursionError)
#   2.  Service Worker unique (fin du conflit de deux écouteurs 'fetch') + push
#   3.  CSRF systématique (formulaire, X-CSRF-Token, corps JSON) sur TOUTES les
#       routes mutants, y compris /api/
#   4.  Anti brute-force : 3 essais/minute sur /connexion et /inscription puis
#       bannissement de l'IP pendant 900 s (30 min)
#   5.  Rate limiting anti-DoS (Flask-Limiter + Redis si présent, sinon mémoire)
#   6.  En-têtes de sécurité, HSTS, redirection HTTPS, cookies durcis,
#       SECRET_KEY obligatoire en production, ProxyFix, logging JSON + request-id
#   7.  Historique PERSONNEL exhaustif (page, timeline, API, export, statistiques)
#   8.  Cliquabilité extrême et fluidité de toute l'interface (CSS + JS)
#   9.  PostgreSQL via DATABASE_URL (adaptateur de compatibilité sqlite3 → PG)
#   10. Webhooks Mobile Money signés (HMAC) + réconciliation + double signature
#   11. Inscription administrateur par pseudo + mot de passe (jeton d'amorçage)
#   12. /health, /ready, sauvegardes, purges, garde-fous d'exploitation
# =============================================================================

_V10_LOGGER = logging.getLogger("toutbot.v10")
_V10_T0 = time.time()
_V10_MODE = _env("TOUTBOT_MODE", "dev").lower()
_V10_PROD = _V10_MODE in ("production", "prod", "live")
_V10_HTTPS = _env("TOUTBOT_HTTPS", "1" if _V10_PROD else "0") == "1"
_V10_FENETRE_BAN = _env_int("TOUTBOT_BAN_MINUTES", 30) * 60
_V10_ESSAIS_AUTH = _env_int("TOUTBOT_BRUTEFORCE_ESSAIS", 3)
_V10_LIMITE_SENSIBLE = _env_int("TOUTBOT_RATE_SENSIBLE", 10)
_V10_LIMITE_GLOBALE = _env_int("TOUTBOT_RATE_GLOBAL", 240)
_V10_RATELIMIT_ACTIF = _env("TOUTBOT_RATELIMIT", "1") != "0"
_V10_BOOTSTRAP = _env("TOUTBOT_ADMIN_BOOTSTRAP_TOKEN", "")
_V10_WEBHOOKS_MTN = _env("TOUTBOT_WEBHOOK_SECRET_MTN", "")
_V10_WEBHOOKS_MOOV = _env("TOUTBOT_WEBHOOK_SECRET_MOOV", "")
_V10_DOUBLE_SIGNATURE = _env_float("TOUTBOT_DOUBLE_SIGNATURE_FCFA", 500000.0)

V10 = {
    "version": "10.0",
    "mode": _V10_MODE,
    "https": _V10_HTTPS,
    "demarre_le": maintenant(),
    "rate_limit_actif": _V10_RATELIMIT_ACTIF,
    "limite_globale_par_minute": _V10_LIMITE_GLOBALE,
    "limite_sensible_par_minute": _V10_LIMITE_SENSIBLE,
    "essais_auth_par_minute": _V10_ESSAIS_AUTH,
    "ban_minutes": _V10_FENETRE_BAN // 60,
    "double_signature_fcfa": _V10_DOUBLE_SIGNATURE,
}

# -----------------------------------------------------------------------------
# 1) SECRET_KEY OBLIGATOIRE EN PRODUCTION + COOKIES DURCIS
# -----------------------------------------------------------------------------
if _V10_PROD and not os.environ.get("SECRET_KEY"):
    _V10_LOGGER.critical("SECRET_KEY absente : refus de démarrer en production.")
    raise SystemExit(
        "ARRÊT DE SÉCURITÉ — TOUTBOT_MODE=production exige la variable d'environnement "
        "SECRET_KEY (ex. : SECRET_KEY=$(python -c \"import secrets;print(secrets.token_hex(32))\")). "
        "Aucune valeur de secours n'est acceptée en production."
    )

app.secret_key = os.environ.get("SECRET_KEY") or CONFIG.get("SECRET_KEY") or app.secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(_V10_HTTPS),
    SESSION_COOKIE_NAME="tbm_session",
    PERMANENT_SESSION_LIFETIME=_dt.timedelta(hours=_env_int("TOUTBOT_SESSION_HEURES", 12)),
    MAX_CONTENT_LENGTH=_env_int("TOUTBOT_MAX_BODY", 2 * 1024 * 1024),
    JSON_SORT_KEYS=False,
)

if _env("TOUTBOT_TRUST_PROXY", "1") == "1":
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        _V10_LOGGER.info("ProxyFix actif : X-Forwarded-For / X-Forwarded-Proto pris en compte.")
    except Exception:
        _V10_LOGGER.warning("ProxyFix indisponible (werkzeug ancien ?)", exc_info=False)

# -----------------------------------------------------------------------------
# 2) LOGGING STRUCTURÉ (JSON) + request-id
# -----------------------------------------------------------------------------
_V10_LOG_JSON = _env("TOUTBOT_LOG_JSON", "1" if _V10_PROD else "0") == "1"


def _v10_log(niveau: str, message: str, **champs) -> None:
    """Journalisation structurée, sans jamais écrire un secret ni un jeton."""
    charge = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              "niveau": niveau, "message": message}
    for cle, valeur in champs.items():
        if valeur is None:
            continue
        texte = str(valeur)
        if any(mot in cle.lower() for mot in ("mot_de_passe", "password", "token", "secret", "csrf")):
            texte = "***masqué***"
        charge[cle] = texte[:400]
    try:
        if _V10_LOG_JSON:
            _V10_LOGGER.log(getattr(logging, niveau, logging.INFO), json.dumps(charge, ensure_ascii=False))
        else:
            _V10_LOGGER.log(getattr(logging, niveau, logging.INFO),
                            " ".join("%s=%s" % (k, v) for k, v in charge.items() if k != "ts"))
    except Exception:
        pass

