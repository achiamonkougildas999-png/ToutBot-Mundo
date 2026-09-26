# =============================================================================
# WebSocket du tableau de bord (flask-sock optionnel) : handler /ws/admin
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 7395 a 7460
# Code extrait tel quel (aucune modification).
# =============================================================================

#   • Modération automatique configurable : mots interdits → masquage du mot
#     ou lecture seule temporaire (ban temporaire) à durée paramétrable.
# =============================================================================
import queue as _queue
import threading as _threading

try:  # WebSocket du tableau de bord : dépendance OPTIONNELLE
    from flask_sock import Sock as _SockV6
    _WS_DISPONIBLE = True
except Exception:  # ImportError, version incompatible…
    _SockV6 = None
    _WS_DISPONIBLE = False

from flask import stream_with_context as _flux_contexte

REGLAGES_V6_DEFAUT = {
    # La modération est LIVRÉE ÉTEINTE : rien n'est masqué tant que
    # l'administrateur ne l'active pas explicitement (aucune censure cachée).
    "automod_actif": "0",
    "masque": "▮",
    "ban_duree_min": "60",
}
# Mots d'EXEMPLE livrés pour montrer le mécanisme ; ils sont supprimables en un clic.
MOTS_EXEMPLES_V6 = (("exemple-spam", "masquer", 0),
                    ("exemple-arnaque", "masquer", 0),
                    ("exemple-insulte", "ban_temporaire", 60))

SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS moderation_mots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    terme     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    action    TEXT NOT NULL DEFAULT 'masquer',   -- masquer | ban_temporaire
    duree_min INTEGER NOT NULL DEFAULT 60,
    actif     INTEGER NOT NULL DEFAULT 1,
    auteur_id INTEGER,
    cree_le   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moderation_journal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    contexte      TEXT NOT NULL DEFAULT '',
    terme         TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL DEFAULT '',
    texte_origine TEXT NOT NULL DEFAULT '',
    texte_final   TEXT NOT NULL DEFAULT '',
    expire_le     TEXT NOT NULL DEFAULT '',
    cree_le       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_modj_date ON moderation_journal(cree_le);
CREATE TABLE IF NOT EXISTS moderation_reglages (
    cle    TEXT PRIMARY KEY,
    valeur TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dashboard_evenements (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    type    TEXT NOT NULL,
    texte   TEXT NOT NULL,
    lien    TEXT NOT NULL DEFAULT '',
    montant REAL NOT NULL DEFAULT 0,
    lu      INTEGER NOT NULL DEFAULT 0,
    cree_le TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evt_id ON dashboard_evenements(id);
CREATE INDEX IF NOT EXISTS idx_evt_lu ON dashboard_evenements(lu);
CREATE TABLE IF NOT EXISTS groupes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
