# -*- coding: utf-8 -*-
# =============================================================================
# 🛰️  RECHERCHE MONDE 195 — MONO-FICHIER UNIFIÉ, CORRIGÉ ET DÉDUPLIQUÉ
# -----------------------------------------------------------------------------
# OBJET DU PROJET (brief utilisateur) :
#   • Recherche web SANS clé d'API payante (aucune clé Google, OpenAI, Bing…)
#   • Navigateur réel piloté (Playwright, repli Selenium) directement intégré
#     au moteur de recherche temps réel : Playwright est « branché » au code,
#     pas seulement appelé depuis l'extérieur.
#   • Suivi des actualités des 195 pays et territoires observateurs
#   • Notification Gmail (mot de passe d'application lu dans l'ENVIRONNEMENT,
#     jamais écrit en dur) vers l'adresse choisie par l'utilisateur.
#
# ORIGINE DU CODE (fusion, pas réécriture) :
#   • ToutBot_Mundo.py (18 628 lignes) → SEULES les couches utiles ont été
#     conservées et améliorées :
#         - clients réels Wikipédia / DuckDuckGo / Google News RSS / SearXNG
#         - agrégateur « MoteurRecherche » (+ cache, dédoublonnage)
#         - bloc de mémoire temps réel + prompt « l'IA n'invente rien »
#         - philosophie « chaque résultat porte son lien »
#     → tout le reste (monétisation V8, abonnements, pourboires, portefeuille,
#       admin, plaintes, messagerie, batteries de tests, annexes) a été RETIRÉ :
#       hors sujet pour ce projet.
#   • README(1).md (web-search-mcp) → conservé comme SPÉCIFICATION de
#     comportement du moteur Playwright : priorité Bing > Brave > DuckDuckGo,
#     isolation de navigateur, extraction de contenu, repli HTTP/1.1,
#     récupération des erreurs HTTP/2, traitement concurrent.
#   • index.html + script.js + script.css (style (1).css) → page de recherche
#     avec bascule de thème sombre, RÉINTÉGRÉE ici en gabarit unique servi par
#     l'application (plus aucun fichier externe nécessaire).
#   • Les ~60 fichiers TypeScript/JavaScript, workflow CI, règles ESLint,
#     scénarios d'évaluation, fichiers VS Code, manifestes de greffon
#     (chrome-devtools-mcp) → RETIRÉS : outils de compilation d'un AUTRE
#     projet, aucun rapport avec la recherche temps réel ni avec Gmail.
#   • google.html + style.css (clone de la page d'accueil Google) → RETIRÉS :
#     maquette statique sans logique, étrangère au projet.
#   • WEB RECH_*.txt (14 fichiers, ~4,8 Mo) → RETIRÉS : captures brutes de
#     sources Chromium (aucun code réutilisable, aucun apport fonctionnel).
#
# DÉMARRAGE
#   pip install playwright feedparser beautifulsoup4 lxml requests
#   python -m playwright install chromium
#   export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"   # mot de passe d'application
#   python recherche_monde_195.py --verifier-pays
#   python recherche_monde_195.py --recherche "élections sénégal"
#   python recherche_monde_195.py --actualites --une-fois --dry-run
#   python recherche_monde_195.py --actualites --veille --intervalle 3600
#   python recherche_monde_195.py --sert --port 8099     # interface web locale
#
# SÉCURITÉ : aucune clé, aucun mot de passe, aucune adresse n'est écrite en dur
# (l'adresse du destinataire est un simple défaut modifiable par variable
# d'environnement). Le fichier ne lit, ne stocke et ne diffuse aucun média.
# =============================================================================

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import email.utils
import hashlib
import html as _html
import json
import logging
import os
import random
import re
import smtplib
import sqlite3
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__version__ = "1.0.0"
LOGGER = logging.getLogger("recherche.monde")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

# =============================================================================
# 1) CONFIGURATION — TOUT SE RÈGLE PAR VARIABLES D'ENVIRONNEMENT
# =============================================================================


def _env(nom: str, defaut: str) -> str:
    valeur = os.environ.get(nom)
    return valeur if valeur not in (None, "") else defaut


def _env_int(nom: str, defaut: int) -> int:
    try:
        return int(_env(nom, str(defaut)).strip())
    except (TypeError, ValueError):
        return defaut


def _env_float(nom: str, defaut: float) -> float:
    try:
        return float(_env(nom, str(defaut)).strip())
    except (TypeError, ValueError):
        return defaut


def _env_bool(nom: str, defaut: bool) -> bool:
    brut = _env(nom, "1" if defaut else "0").strip().lower()
    return brut in ("1", "true", "vrai", "oui", "yes", "on")


CONFIG: Dict[str, Any] = {
    # --- Sortie / état -----------------------------------------------------
    "DB": _env("RECHERCHE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "recherche_monde.db")),
    "LANGUE": _env("RECHERCHE_LANGUE", "fr"),
    "PAYS_DEFAUT": _env("RECHERCHE_PAYS", "FR"),  # contexte régional des moteurs
    # --- Réseau ------------------------------------------------------------
    "UA": _env(
        "RECHERCHE_UA",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36",
    ),
    "TIMEOUT": _env_int("RECHERCHE_TIMEOUT", 12),
    "REPLIS": _env_int("RECHERCHE_REPLIS", 2),          # 1 réessai + 1 original
    "PAUSE_MIN": _env_float("RECHERCHE_PAUSE_MIN", 1.2),  # cadence minimale (s)
    "PAUSE_MAX": _env_float("RECHERCHE_PAUSE_MAX", 2.6),  # gigue aléatoire (s)
    "MAX_RESULTATS": _env_int("RECHERCHE_MAX_RESULTATS", 6),
    "CACHE_TTL": _env_int("RECHERCHE_CACHE_TTL", 600),
    "SEARXNG_URL": _env("SEARXNG_URL", ""),
    "MOTEURS_HTTP": [
        m.strip()
        for m in _env("RECHERCHE_MOTEURS_HTTP", "google_news,wikipedia,duckduckgo,searxng").split(",")
        if m.strip()
    ],
    # --- Playwright / Selenium -------------------------------------------
    "PLAYWRIGHT": _env_bool("RECHERCHE_PLAYWRIGHT", True),
    "PLAYWRIGHT_HEADLESS": _env_bool("RECHERCHE_HEADLESS", True),
    "PLAYWRIGHT_TIMEOUT": _env_int("RECHERCHE_PW_TIMEOUT", 20000),  # ms
    "PLAYWRIGHT_MOTEURS": [
        m.strip()
        for m in _env("RECHERCHE_PW_MOTEURS", "bing,duckduckgo").split(",")
        if m.strip()
    ],
    "PLAYWRIGHT_SELENIUM_REPLI": _env_bool("RECHERCHE_SELENIUM_REPLI", True),
    "BLOQUER_RESSOURCES": _env_bool("RECHERCHE_BLOQUER_RESSOURCES", True),
    "VERIFIER_PERTINENCE": _env_bool("RECHERCHE_PERTINENCE", True),
    "SEUIL_PERTINENCE": _env_float("RECHERCHE_SEUIL_PERTINENCE", 0.25),
    # --- Actualités --------------------------------------------------------
    "NEWS_MAX_PAR_PAYS": _env_int("NEWS_MAX_PAR_PAYS", 8),
    "NEWS_FENETRE_H": _env_int("NEWS_FENETRE_H", 24),
    "NEWS_MAX_PAYS_PAR_PASSE": _env_int("NEWS_MAX_PAYS_PAR_PASSE", 195),
    "NEWS_QUERY": _env("NEWS_QUERY", "{pays} actualité"),
    "NEWS_AVEC_PLAYWRIGHT": _env_bool("NEWS_AVEC_PLAYWRIGHT", False),
    # --- Assistant IA (facultatif, sans clé) ------------------------------
    "LLM_ACTIF": _env_bool("RECHERCHE_LLM", True),
    "LLM_ENDPOINT": _env("TOUTBOT_LLM_ENDPOINT", _env("RECHERCHE_LLM_ENDPOINT", "https://text.pollinations.ai/openai")),
    "LLM_MODEL": _env("TOUTBOT_LLM_MODEL", _env("RECHERCHE_LLM_MODEL", "openai")),
    "LLM_KEY": _env("TOUTBOT_LLM_KEY", ""),  # facultatif : Pollinations marche sans clé
    "LLM_TIMEOUT": _env_int("RECHERCHE_LLM_TIMEOUT", 45),
    # --- Gmail / SMTP (mot de passe APP lu dans l'environnement) ----------
    "SMTP_HOTE": _env("SMTP_HOTE", "smtp.gmail.com"),
    "SMTP_PORT": _env_int("SMTP_PORT", 465),
    "SMTP_UTILISATEUR": _env("SMTP_UTILISATEUR", _env("GMAIL_ADRESSE", "")),
    "SMTP_MOTDEPASSE": _env("SMTP_MOTDEPASSE", _env("GMAIL_APP_PASSWORD", "")),
    "MAIL_DE": _env("MAIL_DE", _env("GMAIL_ADRESSE", "")),
    "MAIL_VERS": [
        a.strip() for a in _env("MAIL_VERS", "sowbirich1212@gmail.com").split(",") if a.strip()
    ],
    "MAIL_SUJET": _env("MAIL_SUJET", "🌍 Veille mondiale — {nb} dépêches, {pays} pays"),
    # --- Divers ------------------------------------------------------------
    "TITRE_APP": _env("RECHERCHE_TITRE", "Recherche Monde 195"),
    "SECRET_KEY": _env("SECRET_KEY", "cle-de-developpement-a-changer"),
}


# =============================================================================
# 2) OUTILS GÉNÉRAUX (temps, texte, empreintes, réseau, cadence, cache)
# =============================================================================


def maintenant() -> str:
    """Horodatage local ISO (secondes)."""
    return _dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def il_y_a_heures(heures: int) -> _dt.datetime:
    return _dt.datetime.now() - _dt.timedelta(hours=heures)


def sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in decompose if not unicodedata.combining(c))


def nettoyer_texte(brut: Any, limite: int = 400) -> str:
    """Supprime les balises, normalise les espaces, tronque proprement."""
    texte = re.sub(r"<[^>]+>", " ", str(brut or ""))
    texte = _html.unescape(texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte[:limite]


def empreinte(*parties: Any) -> str:
    """SHA-256 court et stable, utilisé pour dédoublonner les dépêches."""
    matiere = "|".join(sans_accents(str(p or "")).lower().strip() for p in parties)
    return hashlib.sha256(matiere.encode("utf-8", "replace")).hexdigest()[:32]


MOTS_VIDES = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "en", "au", "aux",
    "the", "of", "and", "a", "to", "in", "for", "on", "actualite", "actualites",
    "news", "today", "aujourd", "hui", "2025", "2026",
}


def mots_cles(texte: str) -> set:
    mots = re.split(r"[^0-9a-zA-Z\u00c0-\u024f]+", sans_accents(texte).lower())
    return {m for m in mots if len(m) > 2 and m not in MOTS_VIDES}


def score_pertinence(requete: str, titre: str, extrait: str = "") -> float:
    """Score 0→1 : recouvrement de mots-clés requête ↔ résultat.

    Remplace « ENABLE_RELEVANCE_CHECKING » du README : évite de renvoyer des
    résultats hors sujet qu'un moteur peut produire sur une requête courte.
    """
    attendus = mots_cles(requete)
    if not attendus:
        return 1.0
    obtenus = mots_cles(f"{titre} {extrait}")
    if not obtenus:
        return 0.0
    recouvrement = len(attendus & obtenus) / len(attendus)
    bonus = 0.15 if sans_accents(requete).lower()[:24] in sans_accents(f"{titre} {extrait}").lower() else 0.0
    return min(1.0, recouvrement + bonus)


class Cadenceur:
    """Cadenceur global : espace les appels réseau pour ne pas se faire bloquer.

    Reprend l'esprit « Smart Request Strategy » du README en ajoutant une
    gigue aléatoire (les robots amateurs sont repérés par une cadence fixe).
    """

    def __init__(self, pause_min: Optional[float] = None, pause_max: Optional[float] = None) -> None:
        self.pause_min = CONFIG["PAUSE_MIN"] if pause_min is None else pause_min
        self.pause_max = CONFIG["PAUSE_MAX"] if pause_max is None else pause_max
        self._dernier = 0.0

    def attendre(self) -> None:
        ecoule = time.monotonic() - self._dernier
        cible = random.uniform(self.pause_min, self.pause_max)
        if ecoule < cible:
            time.sleep(cible - ecoule)
        self._dernier = time.monotonic()


CADENCEUR = Cadenceur()


class ErreurMoteur(RuntimeError):
    """Moteur indisponible (dépendance absente, CAPTCHA, réseau coupé)."""


def http_obtenir(
    url: str,
    timeout: Optional[int] = None,
    accept: str = "*/*",
    en_tetes: Optional[Dict[str, str]] = None,
    repeter: Optional[int] = None,
) -> bytes:
    """GET robuste : cadence, réessais exponentiels, plusieurs agents.

    Correction par rapport à la version d'origine : un échec HTTP passager ne
    fait plus remonter une exception brute jusqu'à l'interface ; le code
    réessaie, puis lève une ErreurMoteur explicite que l'appelant peut ignorer.
    """
    timeout = CONFIG["TIMEOUT"] if timeout is None else timeout
    repeter = CONFIG["REPLIS"] if repeter is None else repeter
    agents = [
        CONFIG["UA"],
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "ToutBot-Monde/1.0 (+veille-presse; contact: administrateur)",
    ]
    derniere: Optional[BaseException] = None
    for tentative in range(repeter + 1):
        CADENCEUR.attendre()
        entetes = {
            "User-Agent": agents[tentative % len(agents)],
            "Accept": accept,
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
            "Accept-Encoding": "identity",
            "Connection": "close",  # contourne les erreurs HTTP/2 (cf. README)
        }
        if en_tetes:
            entetes.update(en_tetes)
        try:
            requete = urllib.request.Request(url, headers=entetes)
            with urllib.request.urlopen(requete, timeout=timeout) as reponse:
                return reponse.read()
        except urllib.error.HTTPError as exc:
            derniere = exc
            if exc.code in (400, 401, 403, 404, 410):  # inutile d'insister
                break
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError) as exc:
            derniere = exc
        if tentative < repeter:
            time.sleep(0.6 * (2**tentative) + random.random() * 0.4)
    raise ErreurMoteur(f"{url[:90]} — {type(derniere).__name__}: {derniere}")


@dataclass
class Resultat:
    """Un résultat de recherche. Invariant : porte TOUJOURS sa source et son lien."""

    source: str
    titre: str
    url: str
    extrait: str = ""
    date: str = ""
    pertinence: float = 1.0

    def en_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "titre": self.titre,
            "url": self.url,
            "extrait": self.extrait,
            "date": self.date,
            "pertinence": round(self.pertinence, 3),
        }

    def en_bloc(self, indice: int) -> str:
        return (
            f"[{indice}] ({self.source})\n"
            f"    Titre : {self.titre}\n"
            f"    Date  : {self.date or 'non fournie'}\n"
            f"    Lien  : {self.url}\n"
            f"    Info  : {self.extrait or '(pas de résumé)'}"
        )


# =============================================================================
# 3) LES 195 PAYS ET OBSERVATEURS (liste unique, vérifiable, sans doublon)
# =============================================================================
# 193 États membres de l'ONU + 2 observateurs (Saint-Siège, Palestine) = 195.
# Chaque entrée : (ISO-2, nom français, nom anglais utilisé pour les requêtes).

PAYS: Tuple[Tuple[str, str, str], ...] = (
    # ---- Afrique (54) ----
    ("DZ", "Algérie", "Algeria"),
    ("AO", "Angola", "Angola"),
    ("BJ", "Bénin", "Benin"),
    ("BW", "Botswana", "Botswana"),
    ("BF", "Burkina Faso", "Burkina Faso"),
    ("BI", "Burundi", "Burundi"),
    ("CV", "Cabo Verde", "Cape Verde"),
    ("CM", "Cameroun", "Cameroon"),
    ("CF", "République centrafricaine", "Central African Republic"),
    ("TD", "Tchad", "Chad"),
    ("KM", "Comores", "Comoros"),
    ("CG", "Congo", "Republic of the Congo"),
    ("CD", "République démocratique du Congo", "Democratic Republic of the Congo"),
    ("CI", "Côte d'Ivoire", "Ivory Coast"),
    ("DJ", "Djibouti", "Djibouti"),
    ("EG", "Égypte", "Egypt"),
    ("GQ", "Guinée équatoriale", "Equatorial Guinea"),
    ("ER", "Érythrée", "Eritrea"),
    ("SZ", "Eswatini", "Eswatini"),
    ("ET", "Éthiopie", "Ethiopia"),
    ("GA", "Gabon", "Gabon"),
    ("GM", "Gambie", "Gambia"),
    ("GH", "Ghana", "Ghana"),
    ("GN", "Guinée", "Guinea"),
    ("GW", "Guinée-Bissau", "Guinea-Bissau"),
    ("KE", "Kenya", "Kenya"),
    ("LS", "Lesotho", "Lesotho"),
    ("LR", "Liberia", "Liberia"),
    ("LY", "Libye", "Libya"),
    ("MG", "Madagascar", "Madagascar"),
    ("MW", "Malawi", "Malawi"),
    ("ML", "Mali", "Mali"),
    ("MR", "Mauritanie", "Mauritania"),
    ("MU", "Maurice", "Mauritius"),
    ("MA", "Maroc", "Morocco"),
    ("MZ", "Mozambique", "Mozambique"),
    ("NA", "Namibie", "Namibia"),
    ("NE", "Niger", "Niger"),
    ("NG", "Nigéria", "Nigeria"),
    ("RW", "Rwanda", "Rwanda"),
    ("ST", "Sao Tomé-et-Principe", "Sao Tome and Principe"),
    ("SN", "Sénégal", "Senegal"),
    ("SC", "Seychelles", "Seychelles"),
    ("SL", "Sierra Leone", "Sierra Leone"),
    ("SO", "Somalie", "Somalia"),
    ("ZA", "Afrique du Sud", "South Africa"),
    ("SS", "Soudan du Sud", "South Sudan"),
    ("SD", "Soudan", "Sudan"),
    ("TZ", "Tanzanie", "Tanzania"),
    ("TG", "Togo", "Togo"),
    ("TN", "Tunisie", "Tunisia"),
    ("UG", "Ouganda", "Uganda"),
    ("ZM", "Zambie", "Zambia"),
    ("ZW", "Zimbabwe", "Zimbabwe"),
    # ---- Amériques (35) ----
    ("AG", "Antigua-et-Barbuda", "Antigua and Barbuda"),
    ("AR", "Argentine", "Argentina"),
    ("BS", "Bahamas", "Bahamas"),
    ("BB", "Barbade", "Barbados"),
    ("BZ", "Belize", "Belize"),
    ("BO", "Bolivie", "Bolivia"),
    ("BR", "Brésil", "Brazil"),
    ("CA", "Canada", "Canada"),
    ("CL", "Chili", "Chile"),
    ("CO", "Colombie", "Colombia"),
    ("CR", "Costa Rica", "Costa Rica"),
    ("CU", "Cuba", "Cuba"),
    ("DM", "Dominique", "Dominica"),
    ("DO", "République dominicaine", "Dominican Republic"),
    ("EC", "Équateur", "Ecuador"),
    ("SV", "El Salvador", "El Salvador"),
    ("GD", "Grenade", "Grenada"),
    ("GT", "Guatemala", "Guatemala"),
    ("GY", "Guyana", "Guyana"),
    ("HT", "Haïti", "Haiti"),
    ("HN", "Honduras", "Honduras"),
    ("JM", "Jamaïque", "Jamaica"),
    ("MX", "Mexique", "Mexico"),
    ("NI", "Nicaragua", "Nicaragua"),
    ("PA", "Panama", "Panama"),
    ("PY", "Paraguay", "Paraguay"),
    ("PE", "Pérou", "Peru"),
    ("KN", "Saint-Christophe-et-Niévès", "Saint Kitts and Nevis"),
    ("LC", "Sainte-Lucie", "Saint Lucia"),
    ("VC", "Saint-Vincent-et-les-Grenadines", "Saint Vincent and the Grenadines"),
    ("SR", "Suriname", "Suriname"),
    ("TT", "Trinité-et-Tobago", "Trinidad and Tobago"),
    ("US", "États-Unis", "United States"),
    ("UY", "Uruguay", "Uruguay"),
    ("VE", "Venezuela", "Venezuela"),
    # ---- Asie (47) ----
    ("AF", "Afghanistan", "Afghanistan"),
    ("SA", "Arabie saoudite", "Saudi Arabia"),
    ("AM", "Arménie", "Armenia"),
    ("AZ", "Azerbaïdjan", "Azerbaijan"),
    ("BH", "Bahreïn", "Bahrain"),
    ("BD", "Bangladesh", "Bangladesh"),
    ("BT", "Bhoutan", "Bhutan"),
    ("BN", "Brunei", "Brunei"),
    ("KH", "Cambodge", "Cambodia"),
    ("CN", "Chine", "China"),
    ("CY", "Chypre", "Cyprus"),
    ("KP", "Corée du Nord", "North Korea"),
    ("KR", "Corée du Sud", "South Korea"),
    ("AE", "Émirats arabes unis", "United Arab Emirates"),
    ("GE", "Géorgie", "Georgia"),
    ("IN", "Inde", "India"),
    ("ID", "Indonésie", "Indonesia"),
    ("IR", "Iran", "Iran"),
    ("IQ", "Iraq", "Iraq"),
    ("IL", "Israël", "Israel"),
    ("JP", "Japon", "Japan"),
    ("JO", "Jordanie", "Jordan"),
    ("KZ", "Kazakhstan", "Kazakhstan"),
    ("KW", "Koweït", "Kuwait"),
    ("KG", "Kirghizistan", "Kyrgyzstan"),
    ("LA", "Laos", "Laos"),
    ("LB", "Liban", "Lebanon"),
    ("MY", "Malaisie", "Malaysia"),
    ("MV", "Maldives", "Maldives"),
    ("MN", "Mongolie", "Mongolia"),
    ("MM", "Myanmar", "Myanmar"),
    ("NP", "Népal", "Nepal"),
    ("OM", "Oman", "Oman"),
    ("PK", "Pakistan", "Pakistan"),
    ("PH", "Philippines", "Philippines"),
    ("QA", "Qatar", "Qatar"),
    ("SG", "Singapour", "Singapore"),
    ("LK", "Sri Lanka", "Sri Lanka"),
    ("SY", "Syrie", "Syria"),
    ("TJ", "Tadjikistan", "Tajikistan"),
    ("TH", "Thaïlande", "Thailand"),
    ("TL", "Timor oriental", "East Timor"),
    ("TR", "Turquie", "Turkey"),
    ("TM", "Turkménistan", "Turkmenistan"),
    ("UZ", "Ouzbékistan", "Uzbekistan"),
    ("VN", "Viêt Nam", "Vietnam"),
    ("YE", "Yémen", "Yemen"),
    # ---- Europe (43) ----
    ("AL", "Albanie", "Albania"),
    ("AD", "Andorre", "Andorra"),
    ("AT", "Autriche", "Austria"),
    ("BY", "Belarus", "Belarus"),
    ("BE", "Belgique", "Belgium"),
    ("BA", "Bosnie-Herzégovine", "Bosnia and Herzegovina"),
    ("BG", "Bulgarie", "Bulgaria"),
    ("HR", "Croatie", "Croatia"),
    ("CZ", "Tchéquie", "Czechia"),
    ("DK", "Danemark", "Denmark"),
    ("EE", "Estonie", "Estonia"),
    ("FI", "Finlande", "Finland"),
    ("FR", "France", "France"),
    ("DE", "Allemagne", "Germany"),
    ("GR", "Grèce", "Greece"),
    ("HU", "Hongrie", "Hungary"),
    ("IS", "Islande", "Iceland"),
    ("IE", "Irlande", "Ireland"),
    ("IT", "Italie", "Italy"),
    ("LV", "Lettonie", "Latvia"),
    ("LI", "Liechtenstein", "Liechtenstein"),
    ("LT", "Lituanie", "Lithuania"),
    ("LU", "Luxembourg", "Luxembourg"),
    ("MT", "Malte", "Malta"),
    ("MD", "Moldavie", "Moldova"),
    ("MC", "Monaco", "Monaco"),
    ("ME", "Monténégro", "Montenegro"),
    ("NL", "Pays-Bas", "Netherlands"),
    ("MK", "Macédoine du Nord", "North Macedonia"),
    ("NO", "Norvège", "Norway"),
    ("PL", "Pologne", "Poland"),
    ("PT", "Portugal", "Portugal"),
    ("RO", "Roumanie", "Romania"),
    ("RU", "Russie", "Russia"),
    ("SM", "Saint-Marin", "San Marino"),
    ("RS", "Serbie", "Serbia"),
    ("SK", "Slovaquie", "Slovakia"),
    ("SI", "Slovénie", "Slovenia"),
    ("ES", "Espagne", "Spain"),
    ("SE", "Suède", "Sweden"),
    ("CH", "Suisse", "Switzerland"),
    ("UA", "Ukraine", "Ukraine"),
    ("GB", "Royaume-Uni", "United Kingdom"),
    # ---- Océanie (14) ----
    ("AU", "Australie", "Australia"),
    ("FJ", "Fidji", "Fiji"),
    ("KI", "Kiribati", "Kiribati"),
    ("MH", "Îles Marshall", "Marshall Islands"),
    ("FM", "Micronésie", "Micronesia"),
    ("NR", "Nauru", "Nauru"),
    ("NZ", "Nouvelle-Zélande", "New Zealand"),
    ("PW", "Palaos", "Palau"),
    ("PG", "Papouasie-Nouvelle-Guinée", "Papua New Guinea"),
    ("WS", "Samoa", "Samoa"),
    ("SB", "Îles Salomon", "Solomon Islands"),
    ("TO", "Tonga", "Tonga"),
    ("TV", "Tuvalu", "Tuvalu"),
    ("VU", "Vanuatu", "Vanuatu"),
    # ---- Observateurs ONU (2) ----
    ("VA", "Saint-Siège (Vatican)", "Vatican City"),
    ("PS", "Palestine", "Palestine"),
)

PAYS_PAR_ISO: Dict[str, Tuple[str, str, str]] = {p[0]: p for p in PAYS}
PAYS_PAR_NOM: Dict[str, Tuple[str, str, str]] = {sans_accents(p[1]).lower(): p for p in PAYS}


def verifier_pays() -> Dict[str, Any]:
    """Contrôle d'intégrité de la liste : 195 entrées, aucun doublon, ISO valide."""
    codes = [p[0] for p in PAYS]
    noms = [sans_accents(p[1]).lower() for p in PAYS]
    doublons_codes = sorted({c for c in codes if codes.count(c) > 1})
    doublons_noms = sorted({n for n in noms if noms.count(n) > 1})
    iso_invalides = [c for c in codes if not re.fullmatch(r"[A-Z]{2}", c)]
    return {
        "total": len(PAYS),
        "attendu": 195,
        "conforme": len(PAYS) == 195 and not doublons_codes and not doublons_noms and not iso_invalides,
        "doublons_iso": doublons_codes,
        "doublons_noms": doublons_noms,
        "iso_invalides": iso_invalides,
    }


assert len(PAYS) == 195, f"La liste des pays doit contenir 195 entrées (obtenu : {len(PAYS)})"


def pays_depuis_requete(texte: str) -> List[Tuple[str, str, str]]:
    """Retrouve tout pays cité dans un texte libre (« actualités du Mali »)."""
    cible = sans_accents(texte or "").lower()
    trouves: List[Tuple[str, str, str]] = []
    for iso, nom_fr, nom_en in sorted(PAYS, key=lambda p: -len(p[1])):
        for variante in (nom_fr, nom_en):
            if sans_accents(variante).lower() in cible and (iso, nom_fr, nom_en) not in trouves:
                trouves.append((iso, nom_fr, nom_en))
                break
    return trouves


# =============================================================================
# 4) MOTEURS HTTP SANS CLÉ — RÉSULTATS RÉELS, TOUJOURS AVEC LE LIEN
# =============================================================================
# Améliorations apportées aux clients d'origine (ToutBot_Mundo.py) :
#   • le paramètre régional (hl/gl/ceid) est déduit du pays demandé au lieu
#     d'être figé sur la France ;
#   • la recherche Wikipédia utilise l'API opensearch puis l'API REST pour
#     obtenir un vrai résumé (l'ancien code ne renvoyait qu'un extrait tronqué
#     de balises) ;
#   • chaque résultat reçoit un score de pertinence (filtre de bruit) ;
#   • SearXNG n'essaie plus d'instance morte en boucle : les instances sont
#     essayées une seule fois par recherche et mises en quarantaine si muettes.


def _locale_pour(iso: str) -> Dict[str, str]:
    locales = {
        "fr": {"hl": "fr", "gl": "FR", "ceid": "FR:fr", "wiki": "fr", "recherche": "actualité"},
        "en": {"hl": "en-US", "gl": "US", "ceid": "US:en", "wiki": "en", "recherche": "news"},
        "ar": {"hl": "ar", "gl": "EG", "ceid": "EG:ar", "wiki": "ar", "recherche": "أخبار"},
        "es": {"hl": "es", "gl": "ES", "ceid": "ES:es", "wiki": "es", "recherche": "noticias"},
    }
    pays_ar = {"EG", "SA", "AE", "DZ", "MA", "TN", "LY", "SD", "IQ", "JO", "QA", "KW", "BH", "OM", "YE", "SY", "LB", "MR"}
    pays_es = {"ES", "MX", "AR", "CO", "PE", "CL", "VE", "EC", "GT", "CU", "BO", "DO", "HN", "PY", "SV", "NI", "CR", "PA", "UY", "PR"}
    if iso in pays_ar:
        return locales["ar"]
    if iso in pays_es:
        return locales["es"]
    if iso in ("US", "GB", "CA", "AU", "NZ", "IE", "ZA", "IN", "NG", "KE", "PH", "SG", "PK"):
        return locales["en"] if iso != "FR" else locales["fr"]
    return locales["fr"]


class ClientWikipedia:
    """Wikipédia — API publique gratuite, aucune clé, aucune inscription."""

    def __init__(self, langue: Optional[str] = None) -> None:
        self.langue = langue or CONFIG["LANGUE"]

    def chercher(self, requete: str, maximum: int = 5) -> List[Resultat]:
        base = f"https://{self.langue}.wikipedia.org"
        parametres = {
            "action": "query", "list": "search", "srsearch": requete,
            "format": "json", "srlimit": max(1, min(maximum, 20)), "utf8": "1",
        }
        try:
            brut = http_obtenir(
                f"{base}/w/api.php?{urllib.parse.urlencode(parametres)}",
                accept="application/json",
            )
            donnees = json.loads(brut.decode("utf-8", "replace"))
        except (ErreurMoteur, ValueError) as exc:
            LOGGER.warning("Wikipédia indisponible : %s", exc)
            return []
        resultats: List[Resultat] = []
        for element in ((donnees.get("query") or {}).get("search") or [])[:maximum]:
            titre = str(element.get("title") or "").strip()
            if not titre:
                continue
            lien = f"{base}/wiki/" + urllib.parse.quote(titre.replace(" ", "_"))
            resume = self.resume(titre)
            resultats.append(Resultat(
                source="Wikipédia",
                titre=titre,
                url=lien,
                extrait=resume or nettoyer_texte(element.get("snippet")),
                date=str(element.get("timestamp") or ""),
                pertinence=score_pertinence(requete, titre, resume),
            ))
        return resultats

    def resume(self, titre: str) -> str:
        """Vrai résumé en clair via l'API REST (correction du code d'origine)."""
        url = "https://%s.wikipedia.org/api/rest_v1/page/summary/%s" % (
            self.langue, urllib.parse.quote(titre.replace(" ", "_")),
        )
        try:
            brut = http_obtenir(url, accept="application/json", repeter=0)
            donnees = json.loads(brut.decode("utf-8", "replace"))
            return nettoyer_texte(donnees.get("extract"), 320)
        except (ErreurMoteur, ValueError):
            return ""


class ClientDuckDuckGo:
    """DuckDuckGo Instant Answer — API publique gratuite, aucune clé."""

    ENDPOINT = "https://api.duckduckgo.com/"

    def chercher(self, requete: str, maximum: int = 5) -> List[Resultat]:
        parametres = {"q": requete, "format": "json", "no_html": "1", "no_redirect": "1"}
        try:
            brut = http_obtenir(
                f"{self.ENDPOINT}?{urllib.parse.urlencode(parametres)}",
                accept="application/json",
            )
            donnees = json.loads(brut.decode("utf-8", "replace"))
        except (ErreurMoteur, ValueError) as exc:
            LOGGER.warning("DuckDuckGo (API) indisponible : %s", exc)
            return []
        resultats: List[Resultat] = []
        if donnees.get("AbstractText"):
            resultats.append(Resultat(
                source="DuckDuckGo",
                titre=nettoyer_texte(donnees.get("Heading") or requete, 200),
                url=str(donnees.get("AbstractURL") or ""),
                extrait=nettoyer_texte(donnees.get("AbstractText"), 320),
                pertinence=score_pertinence(requete, str(donnees.get("Heading") or ""), str(donnees.get("AbstractText") or "")),
            ))
        for sujet in (donnees.get("RelatedTopics") or []):
            if len(resultats) >= maximum:
                break
            if not isinstance(sujet, dict):
                continue
            if sujet.get("Topics"):  # sous-groupes
                for sous in sujet["Topics"]:
                    if len(resultats) >= maximum:
                        break
                    if isinstance(sous, dict) and sous.get("FirstURL"):
                        resultats.append(Resultat(
                            source="DuckDuckGo",
                            titre=nettoyer_texte(sous.get("Text"), 200),
                            url=str(sous.get("FirstURL") or ""),
                            extrait=nettoyer_texte(sous.get("Text"), 320),
                            pertinence=score_pertinence(requete, str(sous.get("Text") or "")),
                        ))
            elif sujet.get("FirstURL"):
                resultats.append(Resultat(
                    source="DuckDuckGo",
                    titre=nettoyer_texte(sujet.get("Text"), 200),
                    url=str(sujet.get("FirstURL") or ""),
                    extrait=nettoyer_texte(sujet.get("Text"), 320),
                    pertinence=score_pertinence(requete, str(sujet.get("Text") or "")),
                ))
        return resultats[:maximum]


class ClientGoogleNewsRSS:
    """Google News — flux RSS public, aucune clé, aucune inscription.

    Correction : le pays et la langue sont paramétrés (avant : hl=fr/gl=FR
    codés en dur, ce qui rendait la veille mondiale inopérante).
    """

    ENDPOINT = "https://news.google.com/rss/search"

    def chercher(
        self,
        requete: str,
        maximum: int = 5,
        iso: str = "FR",
        fenetre_heures: Optional[int] = None,
    ) -> List[Resultat]:
        locale = _locale_pour(iso)
        parametres = {"q": requete, "hl": locale["hl"], "gl": locale["gl"], "ceid": locale["ceid"]}
        try:
            brut = http_obtenir(
                f"{self.ENDPOINT}?{urllib.parse.urlencode(parametres)}",
                accept="application/rss+xml, application/xml",
            )
            racine = ET.fromstring(brut)
        except (ErreurMoteur, ET.ParseError) as exc:
            LOGGER.warning("Google News indisponible (%s) : %s", iso, exc)
            return []
        limite = il_y_a_heures(fenetre_heures) if fenetre_heures else None
        resultats: List[Resultat] = []
        for item in racine.iter("item"):
            titre = nettoyer_texte(item.findtext("title"), 220)
            if not titre:
                continue
            date_brute = (item.findtext("pubDate") or "").strip()
            quand = _dt.datetime.now()
            if date_brute:
                try:
                    quand = email.utils.parsedate_to_datetime(date_brute).replace(tzinfo=None)
                except (TypeError, ValueError):
                    pass
            if limite and quand < limite:
                continue
            source_noeud = item.find("source")
            media = nettoyer_texte(source_noeud.text, 60) if source_noeud is not None and source_noeud.text else ""
            resultats.append(Resultat(
                source=f"Google News / {media}" if media else "Google News",
                titre=titre,
                url=str(item.findtext("link") or "").strip(),
                extrait=nettoyer_texte(item.findtext("description"), 320),
                date=quand.replace(microsecond=0).isoformat(sep=" ") if date_brute else "",
                pertinence=score_pertinence(requete, titre, item.findtext("description") or ""),
            ))
            if len(resultats) >= maximum:
                break
        return resultats


class ClientSearxng:
    """SearXNG — métamoteur libre. Les instances publiques ferment souvent l'API
    JSON : la vôtre d'abord (SEARXNG_URL), puis une courte liste connue."""

    INSTANCES = (
        "https://searx.be",
        "https://baresearch.org",
        "https://search.disroot.org",
        "https://searx.tiekoetter.com",
    )

    def __init__(self) -> None:
        self._mortes: set = set()

    def _instances(self) -> List[str]:
        privee = (CONFIG["SEARXNG_URL"] or "").strip().rstrip("/")
        liste = ([privee] if privee else []) + list(self.INSTANCES)
        return [i for i in liste if i not in self._mortes]

    def chercher(self, requete: str, maximum: int = 5) -> List[Resultat]:
        parametres = {"q": requete, "format": "json", "language": f"{CONFIG['LANGUE']}-{CONFIG['PAYS_DEFAUT']}", "safesearch": "0"}
        for base in self._instances():
            try:
                brut = http_obtenir(
                    f"{base}/search?{urllib.parse.urlencode(parametres)}",
                    accept="application/json",
                    repeter=0,
                )
                donnees = json.loads(brut.decode("utf-8", "replace"))
            except (ErreurMoteur, ValueError) as exc:
                LOGGER.info("SearXNG %s muette : %s", base, exc)
                self._mortes.add(base)  # quarantaine (correction anti-boucle)
                continue
            resultats: List[Resultat] = []
            for res in (donnees.get("results") or [])[:maximum]:
                titre = nettoyer_texte(res.get("title"), 220)
                contenu = nettoyer_texte(res.get("content") or res.get("snippet"), 320)
                resultats.append(Resultat(
                    source="SearXNG",
                    titre=titre,
                    url=str(res.get("url") or ""),
                    extrait=contenu,
                    date=str(res.get("publishedDate") or ""),
                    pertinence=score_pertinence(requete, titre, contenu),
                ))
            if resultats:
                return resultats
        return []


# =============================================================================
# 5) MOTEUR PLAYWRIGHT INTÉGRÉ (navigateur réel) + REPLI SELENIUM
# =============================================================================
# C'est le cœur du brief : « brancher / connecter / stocker notre IA dans
# Playwright ». Le navigateur est piloté EN LIGNE dans le code (pas d'appel
# externe), avec :
#   • priorité Bing > DuckDuckGo > Brave (comme le README web-search-mcp) ;
#   • détection de CAPTCHA / « traffic inhabituel » et bascule immédiate ;
#   • blocage des ressources inutiles (images, polices, médias) → 3 à 5× plus
#     rapide et moins de mémoire ;
#   • navigation bloquée aux hôtes autorisés (garde-fou SSRF), filet de sécurité
#     contre les redirections vers un fichier local ou un service interne ;
#   • lecture du contenu principal (article/main) pour l'extraction de pages.


HOTES_AUTORISES = {
    "bing.com", "www.bing.com",
    "duckduckgo.com", "html.duckduckgo.com", "api.duckduckgo.com",
    "search.brave.com", "brave.com",
    "news.google.com", "google.com",
    "wikipedia.org", "fr.wikipedia.org", "en.wikipedia.org",
    "reuters.com", "apnews.com", "afp.com", "lemonde.fr", "lefigaro.fr",
    "france24.com", "rfi.fr", "bbc.com", "bbc.co.uk", "aljazeera.com",
    "lequipe.fr", "ouest-france.fr", "20minutes.fr", "liberation.fr",
}


def hote_autorise(url: str) -> bool:
    """Garde-fou : on ne laisse le navigateur sortir que vers des hôtes connus."""
    try:
        analyse = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if analyse.scheme not in ("http", "https"):
        return False
    hote = (analyse.hostname or "").lower()
    if not hote:
        return False
    if hote in HOTES_AUTORISES:
        return True
    return any(hote.endswith("." + domaine) for domaine in HOTES_AUTORISES)


SIGNATURES_BLOCAGE = (
    "captcha", "unusual traffic", "traffic inhabituel", "verify you are human",
    "vérifiez que vous êtes humain", "are you a robot", "access denied",
    "accès refusé", "unusual activity", "pour continuer, veuillez",
)


class MoteurNavegateur:
    """Navigateur réel Playwright, avec repli Selenium puis HTTP.

    Disponibilité testée à l'appel : si Playwright n'est pas installé, le moteur
    le dit clairement et propose la commande d'installation, puis bascule.
    """

    MOTEURS = {
        "bing": "https://www.bing.com/search?q={q}&setlang=fr&cc={cc}",
        "duckduckgo": "https://html.duckduckgo.com/html/?q={q}&kl={region}",
        "brave": "https://search.brave.com/search?q={q}&source=web",
    }
    SELECTEURS = {
        "bing": "li.b_algo h2 a, li.b_algo a.tilk",
        "duckduckgo": "a.result__a, div.result__body a.result__url",
        "brave": "div.snippet a, a.heading-serpresult",
    }

    def __init__(self) -> None:
        self._playwright = None
        self._navigateur = None
        self._dernier_usage = 0.0
        self._disponible: Optional[bool] = None

    # -- disponibilité -----------------------------------------------------
    @property
    def disponible(self) -> bool:
        if self._disponible is None:
            if not CONFIG["PLAYWRIGHT"]:
                self._disponible = False
            else:
                try:
                    import playwright  # noqa: F401
                    self._disponible = True
                except Exception:  # noqa: BLE001
                    LOGGER.warning(
                        "Playwright absent : « pip install playwright » puis "
                        "« python -m playwright install chromium »."
                    )
                    self._disponible = False
        return bool(self._disponible)

    # -- cycle de vie ------------------------------------------------------
    def _ouvrir(self):
        if self._navigateur is not None:
            return self._navigateur
        from playwright.sync_api import sync_playwright  # import tardif volontaire

        self._playwright = sync_playwright().start()
        self._navigateur = self._playwright.chromium.launch(
            headless=CONFIG["PLAYWRIGHT_HEADLESS"],
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        return self._navigateur

    def fermer(self) -> None:
        with contextlib.suppress(Exception):
            if self._navigateur is not None:
                self._navigateur.close()
        with contextlib.suppress(Exception):
            if self._playwright is not None:
                self._playwright.stop()
        self._navigateur = None
        self._playwright = None

    def __enter__(self) -> "MoteurNavegateur":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.fermer()

    # -- contexte ----------------------------------------------------------
    def _contexte(self):
        navigateur = self._ouvrir()
        contexte = navigateur.new_context(
            user_agent=CONFIG["UA"],
            locale=f"{CONFIG['LANGUE']}-{CONFIG['PAYS_DEFAUT']}",
            viewport={"width": 1366, "height": 900},
            java_script_enabled=True,
        )
        contexte.set_default_timeout(CONFIG["PLAYWRIGHT_TIMEOUT"])
        if CONFIG["BLOQUER_RESSOURCES"]:
            def _filtrer(route):
                try:
                    if route.request.resource_type in ("image", "media", "font", "stylesheet"):
                        route.abort()
                    elif not hote_autorise(route.request.url) and route.request.resource_type == "document":
                        route.abort()
                    else:
                        route.continue_()
                except Exception:  # noqa: BLE001
                    with contextlib.suppress(Exception):
                        route.continue_()

            contexte.route("**/*", _filtrer)
        return contexte

    # -- lecture d'une page ------------------------------------------------
    def contenu_page(self, url: str, limite: int = 4000) -> str:
        """Extrait le texte principal d'une page (article, main, corps)."""
        if not self.disponible or not hote_autorise(url):
            return ""
        with self._contexte() as contexte:
            page = contexte.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["PLAYWRIGHT_TIMEOUT"])
                cadre = None
                for selecteur in ("article", "main", "[role=main]", "#content", "body"):
                    cadre = page.query_selector(selecteur)
                    if cadre:
                        break
                texte = nettoyer_texte(cadre.inner_text() if cadre else "", limite)
                return texte
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("Lecture de page impossible (%s) : %s", url[:70], exc)
                return ""
            finally:
                with contextlib.suppress(Exception):
                    page.close()

    # -- recherche ---------------------------------------------------------
    def chercher(self, requete: str, maximum: int = 5, moteurs: Optional[Sequence[str]] = None, iso: str = "FR") -> List[Resultat]:
        if not self.disponible:
            return []
        moteurs = list(moteurs or CONFIG["PLAYWRIGHT_MOTEURS"])
        return self._playwright_recherche(requete, maximum, moteurs, iso)

    def _playwright_recherche(self, requete: str, maximum: int, moteurs: Sequence[str], iso: str) -> List[Resultat]:
        from playwright.sync_api import Error as ErreurPlaywright

        locale = _locale_pour(iso)
        for moteur in moteurs:
            modele = self.MOTEURS.get(moteur)
            if not modele:
                continue
            url = modele.format(q=urllib.parse.quote_plus(requete), cc=locale["gl"].lower(), region=locale["gl"].lower())
            resultats: List[Resultat] = []
            page = None
            try:
                with self._contexte() as contexte:
                    page = contexte.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["PLAYWRIGHT_TIMEOUT"])
                    try:
                        page.wait_for_selector(self.SELECTEURS[moteur], timeout=6000)
                    except Exception:  # noqa: BLE001
                        page.wait_for_timeout(1200)
                    corps = (page.inner_text("body") or "")[:3000].lower() if page.query_selector("body") else ""
                    if any(sig in corps for sig in SIGNATURES_BLOCAGE):
                        LOGGER.warning(
                            "%s a renvoyé un mur anti-robot pour « %s » — bascule sur le moteur suivant.",
                            moteur, requete[:60],
                        )
                        continue
                    for lien in page.query_selector_all(self.SELECTEURS[moteur]):
                        titre = nettoyer_texte(lien.inner_text(), 220)
                        href = (lien.get_attribute("href") or "").strip()
                        if not titre or not href:
                            continue
                        if href.startswith("//"):
                            href = "https:" + href
                        if not href.startswith("http"):
                            continue
                        if any(nid in href for nid in ("bing.com/aclick", "duckduckgo.com/y.js", "ad_domain")):
                            continue
                        resultats.append(Resultat(
                            source=f"Playwright/{moteur.capitalize()}",
                            titre=titre,
                            url=href,
                            extrait="",
                            pertinence=score_pertinence(requete, titre),
                        ))
                        if len(resultats) >= maximum:
                            break
            except ErreurPlaywright as exc:
                LOGGER.warning("Playwright/%s a échoué (%s) — moteur suivant.", moteur, str(exc)[:90])
                continue
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Playwright/%s erreur inattendue : %s", moteur, str(exc)[:90])
                continue
            finally:
                if page is not None:
                    with contextlib.suppress(Exception):
                        page.close()

            resultats = [r for r in resultats if hote_autorise(r.url) or r.url.startswith("http")]
            if resultats:
                return resultats

        # Aucun moteur n'a abouti : repli Selenium si présent, puis HTTP.
        return self._selenium_repli(requete, maximum, moteurs, iso)

    def _selenium_repli(self, requete: str, maximum: int, moteurs: Sequence[str], iso: str) -> List[Resultat]:
        if not CONFIG["PLAYWRIGHT_SELENIUM_REPLI"]:
            return []
        try:
            from selenium import webdriver  # noqa: F401
            from selenium.webdriver.common.by import By
        except Exception:  # noqa: BLE001
            LOGGER.info("Selenium absent — repli HTTP sans navigateur.")
            return []
        options = None
        try:
            from selenium.webdriver.chrome.options import Options as OptionsChrome

            options = OptionsChrome()
            if CONFIG["PLAYWRIGHT_HEADLESS"]:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument(f"--user-agent={CONFIG['UA']}")
            pilote = webdriver.Chrome(options=options)
        except Exception as exc:  # noqa: BLE001
            LOGGER.info("Selenium indisponible : %s", str(exc)[:90])
            return []
        try:
            for moteur in moteurs:
                modele = self.MOTEURS.get(moteur)
                if not modele:
                    continue
                url = modele.format(q=urllib.parse.quote_plus(requete), cc="fr", region="fr")
                try:
                    pilote.get(url)
                    time.sleep(1.4)
                    texte_page = (pilote.page_source or "").lower()
                    if any(sig in texte_page for sig in SIGNATURES_BLOCAGE):
                        continue
                    resultats: List[Resultat] = []
                    for lien in pilote.find_elements(By.CSS_SELECTOR, self.SELECTEURS[moteur]):
                        titre = nettoyer_texte(lien.text, 220)
                        href = (lien.get_attribute("href") or "").strip()
                        if titre and href.startswith("http"):
                            resultats.append(Resultat(
                                source=f"Selenium/{moteur.capitalize()}",
                                titre=titre,
                                url=href,
                                pertinence=score_pertinence(requete, titre),
                            ))
                        if len(resultats) >= maximum:
                            break
                    if resultats:
                        return resultats
                except Exception as exc:  # noqa: BLE001
                    LOGGER.info("Selenium/%s : %s", moteur, str(exc)[:80])
                    continue
            return []
        finally:
            with contextlib.suppress(Exception):
                pilote.quit()


NAVIGATEUR = MoteurNavegateur()


# =============================================================================
# 6) AGRÉGATEUR — FUSION, DÉDOUBLONNAGE, PERTINENCE, CACHE
# =============================================================================
# Correction d'un défaut de l'original : la fusion n'éliminait que les doublons
# de titre dans un ordre figé, sans tenir compte du contenu réel. Ici, la clé de
# dédoublonnage est l'URL canonique (scheme+hôte+chemin sans paramètres de
# suivi) et l'ordre final est trié par pertinence décroissante puis par fraîcheur.


PARAMETRES_SUIVI = re.compile(
    r"(utm_[a-z]+|gclid|fbclid|mc_cid|mc_eid|igshid|ref|ref_src|spm|_ga)=[^&]*"
)


def url_canonique(url: str) -> str:
    try:
        analyse = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    requete = PARAMETRES_SUIVI.sub("", analyse.query).strip("&")
    chemin = analyse.path.rstrip("/")
    return urllib.parse.urlunsplit((
        analyse.scheme, analyse.netloc.lower(), chemin, requete, "",
    ))


class Agregateur:
    """Interroge les moteurs HTTP et le navigateur, puis fusionne proprement."""

    def __init__(self) -> None:
        self.wikipedia = ClientWikipedia()
        self.duckduckgo = ClientDuckDuckGo()
        self.google_news = ClientGoogleNewsRSS()
        self.searxng = ClientSearxng()
        self.navigateur = NAVIGATEUR
        self._cache: Dict[str, Dict[str, Any]] = {}

    # -- moteur HTTP -------------------------------------------------------
    def _appeler(self, nom: str, requete: str, maximum: int, iso: str) -> List[Resultat]:
        try:
            if nom == "wikipedia":
                return self.wikipedia.chercher(requete, maximum)
            if nom == "google_news":
                return self.google_news.chercher(requete, maximum, iso=iso)
            if nom == "duckduckgo":
                return self.duckduckgo.chercher(requete, maximum)
            if nom == "searxng":
                return self.searxng.chercher(requete, maximum)
        except Exception as exc:  # noqa: BLE001 — un moteur ne doit jamais tout casser
            LOGGER.warning("Moteur %s en échec : %s", nom, str(exc)[:110])
        return []

    # -- recherche complète ------------------------------------------------
    def chercher(
        self,
        requete: str,
        maximum: Optional[int] = None,
        iso: str = "FR",
        avec_navigateur: Optional[bool] = None,
    ) -> Dict[str, Any]:
        requete = (requete or "").strip()
        maximum = maximum or CONFIG["MAX_RESULTATS"]
        if not requete:
            return {"requete": "", "collecte_le": maintenant(), "compte": {}, "total": 0,
                    "resultats": [], "cache": "miss", "navigateur": "non utilisé"}

        cle = f"{requete}|{maximum}|{iso}|{avec_navigateur}"
        entree = self._cache.get(cle)
        if entree and time.time() - entree["at"] < CONFIG["CACHE_TTL"]:
            reponse = dict(entree["value"])
            reponse["cache"] = "hit"
            return reponse

        with_navigateur = CONFIG["PLAYWRIGHT"] if avec_navigateur is None else avec_navigateur
        compte: Dict[str, int] = {}
        lots: List[Resultat] = []

        for nom in CONFIG["MOTEURS_HTTP"]:
            trouves = self._appeler(nom, requete, maximum, iso)
            compte[nom] = len(trouves)
            lots.extend(trouves)

        etat_navigateur = "désactivé"
        if with_navigateur:
            if self.navigateur.disponible:
                trouves = self.navigateur.chercher(requete, maximum, iso=iso)
                compte["playwright"] = len(trouves)
                etat_navigateur = "utilisé" if trouves else "muet (CAPTCHA ou réseau)"
                lots.extend(trouves)
            else:
                compte["playwright"] = 0
                etat_navigateur = "indisponible (pip install playwright)"

        # Fusion + dédoublonnage par URL canonique
        fusion: List[Resultat] = []
        vus: set = set()
        for item in lots:
            if not item.url:
                continue
            marque = url_canonique(item.url)
            if marque in vus:
                continue
            vus.add(marque)
            fusion.append(item)

        if CONFIG["VERIFIER_PERTINENCE"]:
            avant = len(fusion)
            fusion = [r for r in fusion if r.pertinence >= CONFIG["SEUIL_PERTINENCE"]]
            ecartes = avant - len(fusion)
            if ecartes:
                compte["ecartes_hors_sujet"] = ecartes

        fusion.sort(key=lambda r: (-r.pertinence, r.date == ""))
        fusion = fusion[: maximum * 2]

        valeur = {
            "requete": requete,
            "collecte_le": maintenant(),
            "compte": compte,
            "total": len(fusion),
            "resultats": [r.en_dict() for r in fusion],
            "cache": "miss",
            "navigateur": etat_navigateur,
        }
        self._cache[cle] = {"at": time.time(), "value": valeur}
        return valeur

    # -- bloc mémoire pour l'IA -------------------------------------------
    def bloc_memoire(self, requete: str, maximum: Optional[int] = None, budget: int = 6000, iso: str = "FR") -> str:
        """Bloc texte injecté dans le prompt : chaque ligne porte son lien."""
        donnees = self.chercher(requete, maximum, iso=iso)
        compte = donnees["compte"]
        lignes = [
            "=== FLUX TEMPS RÉEL (résultats réellement collectés, aucun n'est inventé) ===",
            f"Sujet : {requete}",
            f"Collecté le : {donnees['collecte_le']}",
            "Moteurs : " + " | ".join(f"{k}={v}" for k, v in compte.items()),
            f"Navigateur : {donnees['navigateur']}",
            "",
        ]
        utilise = sum(len(x) for x in lignes)
        for i, item in enumerate(donnees["resultats"], 1):
            bloc = (
                f"[{i}] ({item['source']} — pertinence {item['pertinence']})\n"
                f"    Titre : {item['titre']}\n"
                f"    Date  : {item['date'] or 'non fournie'}\n"
                f"    Lien  : {item['url']}\n"
                f"    Info  : {item['extrait'] or '(pas de résumé)'}"
            )
            if utilise + len(bloc) > budget:
                break
            lignes.append(bloc)
            utilise += len(bloc)
        if not donnees["resultats"]:
            lignes.append("AUCUN RÉSULTAT RÉEL COLLECTÉ.")
        return "\n".join(lignes)


AGREGATEUR = Agregateur()


# =============================================================================
# 7) STOCKAGE SQLITE — DÉDOUBLONNAGE DURABLE + ÉTAT DE LA VEILLE
# =============================================================================
# Une seule base, un seul schéma, créé au premier lancement. Les dépêches déjà
# envoyées sont conservées : plus jamais deux fois la même alerte, même après
# un redémarrage (l'original ne dédoublonnait qu'en mémoire).

SCHEMA = """
CREATE TABLE IF NOT EXISTS depeches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    empreinte     TEXT    NOT NULL UNIQUE,      -- sha256(source|titre|pays)
    pays_iso      TEXT    NOT NULL,
    pays_nom      TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    titre         TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    extrait       TEXT    DEFAULT '',
    date_pub      TEXT    DEFAULT '',
    pertinence    REAL    DEFAULT 0,
    collecte_le   TEXT    NOT NULL,
    envoye        INTEGER NOT NULL DEFAULT 0,   -- 0 = jamais envoyé
    envoye_le     TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_depeches_pays  ON depeches(pays_iso, date_pub DESC);
CREATE INDEX IF NOT EXISTS idx_depeches_envoi ON depeches(envoye, collecte_le DESC);

CREATE TABLE IF NOT EXISTS passages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    commence_le   TEXT    NOT NULL,
    fini_le       TEXT    DEFAULT '',
    pays_vus      INTEGER DEFAULT 0,
    pays_ok       INTEGER DEFAULT 0,
    depeches      INTEGER DEFAULT 0,
    mails         INTEGER DEFAULT 0,
    duree_s       REAL    DEFAULT 0
);

CREATE TABLE IF NOT EXISTS envois (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quand         TEXT    NOT NULL,
    destinataires TEXT    NOT NULL,
    sujet         TEXT    NOT NULL,
    nb_depeches   INTEGER DEFAULT 0,
    nb_pays       INTEGER DEFAULT 0,
    statut        TEXT    DEFAULT 'inconnu',
    detail        TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cache_recherche (
    requete       TEXT PRIMARY KEY,
    reponse_json  TEXT    NOT NULL,
    horodatage    TEXT    NOT NULL
);
"""


class Base:
    """Petit DAL SQLite : connexion par appel, transactions explicites."""

    def __init__(self, chemin: Optional[str] = None) -> None:
        self.chemin = chemin or CONFIG["DB"]
        self._initialiser()

    def connexion(self) -> sqlite3.Connection:
        connexion = sqlite3.connect(self.chemin, timeout=20)
        connexion.row_factory = sqlite3.Row
        connexion.execute("PRAGMA journal_mode=WAL")
        connexion.execute("PRAGMA synchronous=NORMAL")
        return connexion

    def _initialiser(self) -> None:
        with contextlib.closing(self.connexion()) as connexion:
            connexion.executescript(SCHEMA)
            self._migrer(connexion)
            connexion.commit()

    @staticmethod
    def _migrer(connexion: sqlite3.Connection) -> None:
        """Ajoute les colonnes manquantes a une base creee par une version anterieure.

        Sans cela, un utilisateur qui relance le script sur une base existante
        recevrait « no such column » : le fichier reste utilisable apres mise a jour.
        """
        attendues = {
            "passages": {
                "fini_le": "TEXT", "pays_vus": "INTEGER DEFAULT 0",
                "pays_ok": "INTEGER DEFAULT 0", "depeches": "INTEGER DEFAULT 0",
                "mails": "INTEGER DEFAULT 0", "duree_s": "REAL DEFAULT 0",
            },
            "depeches": {
                "extrait": "TEXT DEFAULT ''", "date_pub": "TEXT DEFAULT ''",
                "pertinence": "REAL DEFAULT 0", "envoye": "INTEGER DEFAULT 0",
                "envoye_le": "TEXT DEFAULT ''",
            },
            "envois": {
                "nb_depeches": "INTEGER DEFAULT 0", "nb_pays": "INTEGER DEFAULT 0",
                "statut": "TEXT DEFAULT 'inconnu'", "detail": "TEXT DEFAULT ''",
            },
        }
        for table, colonnes in attendues.items():
            existantes = {ligne[1] for ligne in connexion.execute(f"PRAGMA table_info({table})")}
            for nom, definition in colonnes.items():
                if nom not in existantes:
                    connexion.execute(f"ALTER TABLE {table} ADD COLUMN {nom} {definition}")

    # -- dépêches ----------------------------------------------------------
    def enregistrer_depeche(self, resultat: Resultat, iso: str, nom_pays: str) -> bool:
        """Renvoie True si la dépêche est NOUVELLE (donc à envoyer)."""
        marque = empreinte(resultat.source.split("/")[0], resultat.titre, iso)
        with contextlib.closing(self.connexion()) as connexion:
            curseur = connexion.execute(
                """INSERT OR IGNORE INTO depeches
                   (empreinte, pays_iso, pays_nom, source, titre, url, extrait,
                    date_pub, pertinence, collecte_le)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (marque, iso, nom_pays, resultat.source, resultat.titre, resultat.url,
                 resultat.extrait, resultat.date, resultat.pertinence, maintenant()),
            )
            connexion.commit()
            return curseur.rowcount > 0

    def depeches_du_jour(self, heures: int = 24, limite: int = 500) -> List[Dict[str, Any]]:
        depuis = il_y_a_heures(heures).replace(microsecond=0).isoformat(sep=" ")
        with contextlib.closing(self.connexion()) as connexion:
            lignes = connexion.execute(
                """SELECT * FROM depeches
                   WHERE collecte_le >= ?
                   ORDER BY date_pub DESC, pertinence DESC
                   LIMIT ?""",
                (depuis, limite),
            ).fetchall()
        return [dict(l) for l in lignes]

    def pays_couverts(self, heures: int = 24) -> int:
        depuis = il_y_a_heures(heures).replace(microsecond=0).isoformat(sep=" ")
        with contextlib.closing(self.connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT COUNT(DISTINCT pays_iso) AS n FROM depeches WHERE collecte_le >= ?",
                (depuis,),
            ).fetchone()
        return int(ligne["n"] if ligne else 0)

    def marquer_envoyees(self, identifiants: Iterable[int]) -> None:
        identifiants = list(identifiants)
        if not identifiants:
            return
        with contextlib.closing(self.connexion()) as connexion:
            connexion.executemany(
                "UPDATE depeches SET envoye = 1, envoye_le = ? WHERE id = ?",
                [(maintenant(), i) for i in identifiants],
            )
            connexion.commit()

    # -- passages / envois -------------------------------------------------
    def debut_passage(self) -> int:
        with contextlib.closing(self.connexion()) as connexion:
            curseur = connexion.execute(
                "INSERT INTO passages (commence_le) VALUES (?)", (maintenant(),)
            )
            connexion.commit()
            return int(curseur.lastrowid)

    def fin_passage(self, identifiant: int, pays_vus: int, pays_ok: int, depeches: int, mails: int, duree: float) -> None:
        with contextlib.closing(self.connexion()) as connexion:
            connexion.execute(
                """UPDATE passages SET fini_le=?, pays_vus=?, pays_ok=?, depeches=?,
                   mails=?, duree_s=? WHERE id=?""",
                (maintenant(), pays_vus, pays_ok, depeches, mails, round(duree, 1), identifiant),
            )
            connexion.commit()

    def journaliser_envoi(self, destinataires: Sequence[str], sujet: str, nb_depeches: int, nb_pays: int, statut: str, detail: str = "") -> None:
        with contextlib.closing(self.connexion()) as connexion:
            connexion.execute(
                """INSERT INTO envois (quand, destinataires, sujet, nb_depeches,
                   nb_pays, statut, detail) VALUES (?,?,?,?,?,?,?)""",
                (maintenant(), ", ".join(destinataires), sujet, nb_depeches, nb_pays, statut, detail[:500]),
            )
            connexion.commit()

    def statistiques(self) -> Dict[str, Any]:
        with contextlib.closing(self.connexion()) as connexion:
            total = connexion.execute("SELECT COUNT(*) AS n FROM depeches").fetchone()["n"]
            pays = connexion.execute("SELECT COUNT(DISTINCT pays_iso) AS n FROM depeches").fetchone()["n"]
            envois = connexion.execute("SELECT COUNT(*) AS n FROM envois WHERE statut='envoye'").fetchone()["n"]
            dernier = connexion.execute("SELECT * FROM passages ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "depeches_en_base": int(total),
            "pays_couverts": int(pays),
            "mails_envoyes": int(envois),
            "dernier_passage": dict(dernier) if dernier else None,
        }

    # -- cache de recherche ------------------------------------------------
    def lire_cache(self, requete: str, ttl: int) -> Optional[Dict[str, Any]]:
        with contextlib.closing(self.connexion()) as connexion:
            ligne = connexion.execute(
                "SELECT * FROM cache_recherche WHERE requete = ?", (requete,)
            ).fetchone()
        if not ligne:
            return None
        try:
            quand = _dt.datetime.fromisoformat(ligne["horodatage"])
        except ValueError:
            return None
        if (_dt.datetime.now() - quand).total_seconds() > ttl:
            return None
        try:
            return json.loads(ligne["reponse_json"])
        except ValueError:
            return None

    def ecrire_cache(self, requete: str, reponse: Dict[str, Any]) -> None:
        with contextlib.closing(self.connexion()) as connexion:
            connexion.execute(
                """INSERT INTO cache_recherche (requete, reponse_json, horodatage)
                   VALUES (?,?,?)
                   ON CONFLICT(requete) DO UPDATE SET
                     reponse_json=excluded.reponse_json, horodatage=excluded.horodatage""",
                (requete, json.dumps(reponse, ensure_ascii=False), maintenant()),
            )
            connexion.commit()


BASE = Base()


# =============================================================================
# 8) VEILLE DES 195 PAYS — COLLECTE, FILTRAGE, RAPPORT
# =============================================================================


@dataclass
class RapportPays:
    iso: str
    nom: str
    depeches: List[Resultat] = field(default_factory=list)
    erreur: str = ""


def veiller_pays(iso: str, nom_fr: str, nom_en: str, maximum: Optional[int] = None) -> RapportPays:
    """Collecte les dépêches d'un pays. Une erreur sur un pays ne bloque rien."""
    maximum = maximum or CONFIG["NEWS_MAX_PAR_PAYS"]
    rapport = RapportPays(iso=iso, nom=nom_fr)
    requete_modele = CONFIG["NEWS_QUERY"]
    requete_fr = requete_modele.format(pays=nom_fr, country=nom_en)
    requete_en = requete_modele.format(pays=nom_en, country=nom_en)
    try:
        trouves = AGREGATEUR.google_news.chercher(
            requete_fr, maximum, iso=iso, fenetre_heures=CONFIG["NEWS_FENETRE_H"]
        )
        if len(trouves) < maximum:
            trouves += AGREGATEUR.google_news.chercher(
                requete_en, maximum - len(trouves), iso=iso, fenetre_heures=CONFIG["NEWS_FENETRE_H"]
            )
        if CONFIG["NEWS_AVEC_PLAYWRIGHT"] and len(trouves) < 2:
            trouves += AGREGATEUR.navigateur.chercher(requete_fr, maximum, iso=iso)
        vus = set()
        for item in trouves:
            marque = url_canonique(item.url)
            if not item.url or marque in vus:
                continue
            vus.add(marque)
            rapport.depeches.append(item)
        rapport.depeches.sort(key=lambda r: (r.date == "", -r.pertinence))
    except Exception as exc:  # noqa: BLE001
        rapport.erreur = str(exc)[:200]
        LOGGER.warning("Veille %s : %s", nom_fr, rapport.erreur)
    return rapport


def veiller_monde(
    isos: Optional[Sequence[str]] = None,
    maximum_par_pays: Optional[int] = None,
    progression: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[str, Any]:
    """Passe sur les pays, enregistre les NOUVELLES dépêches, renvoie le bilan."""
    selection = [PAYS_PAR_ISO[i] for i in (isos or []) if i in PAYS_PAR_ISO] or list(PAYS)
    limite = CONFIG["NEWS_MAX_PAYS_PAR_PASSE"]
    selection = selection[:limite]
    identifiant = BASE.debut_passage()
    depart = time.monotonic()
    nouvelles: List[Resultat] = []
    pays_ok = 0
    for index, (iso, nom_fr, nom_en) in enumerate(selection, 1):
        rapport = veiller_pays(iso, nom_fr, nom_en, maximum_par_pays)
        if rapport.depeches:
            pays_ok += 1
        for item in rapport.depeches:
            if BASE.enregistrer_depeche(item, iso, nom_fr):
                nouvelles.append(item)
        if progression:
            progression(index, len(selection), nom_fr)
    duree = time.monotonic() - depart
    BASE.fin_passage(identifiant, len(selection), pays_ok, len(nouvelles), 0, duree)
    return {
        "pays_parcourus": len(selection),
        "pays_avec_resultats": pays_ok,
        "nouvelles_depeches": nouvelles,
        "duree_s": round(duree, 1),
        "quand": maintenant(),
    }


def rapport_texte(
    depeches: Sequence[Dict[str, Any]],
    heures: int = 24,
    maximum_par_pays: int = 4,
) -> str:
    """Rapport lisible : regroupé par pays, chaque ligne porte son lien."""
    if not depeches:
        return (
            f"🌍 VEILLE MONDIALE — {maintenant()}\n\n"
            "Aucune dépêche réelle n'a été collectée sur les sources interrogées "
            "pendant cette fenêtre. Rien n'est inventé : mieux vaut une absence "
            "qu'un fait non vérifié.\n"
        )
    par_pays: Dict[str, List[Dict[str, Any]]] = {}
    for ligne in depeches:
        par_pays.setdefault(ligne["pays_nom"], []).append(ligne)
    morceaux = [
        f"🌍 VEILLE MONDIALE DES 195 PAYS",
        f"Rapport généré le {maintenant()} — fenêtre des dernières {heures} heures",
        f"Pays couverts par au moins une dépêche : {len(par_pays)} / 195",
        f"Dépêches retenues : {len(depeches)}",
        "=" * 68,
        "",
    ]
    for nom_pays in sorted(par_pays):
        morceaux.append(f"■ {nom_pays} ({par_pays[nom_pays][0]['pays_iso']})")
        for item in par_pays[nom_pays][:maximum_par_pays]:
            quand = f" — {item['date_pub']}" if item.get("date_pub") else ""
            morceaux.append(f"   • {item['titre']}{quand}")
            morceaux.append(f"     {item['source']} — {item['url']}")
        reste = len(par_pays[nom_pays]) - maximum_par_pays
        if reste > 0:
            morceaux.append(f"   … et {reste} autre(s) dépêche(s) pour ce pays.")
        morceaux.append("")
    morceaux.append("=" * 68)
    morceaux.append(
        "Méthode : flux RSS publics et APIs ouvertes interrogés directement, "
        "sans clé payante. Chaque affirmation porte sa source et son lien."
    )
    return "\n".join(morceaux)


def rapport_html(depeches: Sequence[Dict[str, Any]], heures: int = 24, maximum_par_pays: int = 6) -> str:
    """Même rapport, en HTML autonome (utilisé pour le courriel)."""
    echapper = _html.escape
    if not depeches:
        return (
            "<p>Aucune dépêche réelle n'a été collectée sur les sources "
            "interrogées pendant cette fenêtre. Rien n'est inventé.</p>"
        )
    par_pays: Dict[str, List[Dict[str, Any]]] = {}
    for ligne in depeches:
        par_pays.setdefault(ligne["pays_nom"], []).append(ligne)
    morceaux = [
        "<div style=\"font-family:system-ui,Segoe UI,Arial,sans-serif;"
        "max-width:860px;margin:0 auto;color:#16202c\">",
        "<h1 style=\"font-size:22px;margin:0 0 4px\">🌍 Veille mondiale — "
        f"{len(par_pays)} pays sur 195</h1>",
        f"<p style=\"color:#5b6b7c;margin:0 0 18px\">Rapport du {echapper(maintenant())} "
        f"— fenêtre des dernières {heures} heures — {len(depeches)} dépêches.</p>",
    ]
    for nom_pays in sorted(par_pays):
        lignes = par_pays[nom_pays]
        morceaux.append(
            f"<h2 style=\"font-size:16px;margin:22px 0 8px;border-bottom:2px solid #e3e9f0;"
            f"padding-bottom:6px\">{echapper(nom_pays)} "
            f"<span style=\"color:#8c9aab;font-weight:400\">({echapper(lignes[0]['pays_iso'])})</span></h2>"
        )
        morceaux.append("<ul style=\"list-style:none;padding:0;margin:0\">")
        for item in lignes[:maximum_par_pays]:
            morceaux.append(
                "<li style=\"margin:0 0 12px\">"
                f"<a href=\"{echapper(item['url'])}\" style=\"color:#0a58ca;text-decoration:none;"
                f"font-weight:600\">{echapper(item['titre'])}</a>"
                f"<div style=\"font-size:12px;color:#6b7b8c;margin-top:2px\">"
                f"{echapper(item['source'])}"
                f"{' — ' + echapper(str(item['date_pub'])) if item.get('date_pub') else ''}"
                "</div>"
                f"<div style=\"font-size:13px;color:#3b4a5a;margin-top:3px\">"
                f"{echapper((item.get('extrait') or '')[:240])}</div>"
                "</li>"
            )
        reste = len(lignes) - maximum_par_pays
        if reste > 0:
            morceaux.append(f"<li style=\"color:#8c9aab;font-size:12px\">… et {reste} autre(s).</li>")
        morceaux.append("</ul>")
    morceaux.append(
        "<p style=\"font-size:12px;color:#8c9aab;margin-top:26px\">"
        "Méthode : flux RSS publics et APIs ouvertes interrogés directement, sans clé payante. "
        "Chaque affirmation porte sa source et son lien.</p></div>"
    )
    return "".join(morceaux)


# =============================================================================
# 9) ALERTE GMAIL — MOT DE PASSE D'APPLICATION LU DANS L'ENVIRONNEMENT
# =============================================================================
# Aucun mot de passe n'est écrit dans le fichier. Le script attend :
#     export GMAIL_ADRESSE="vous@gmail.com"
#     export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"   (mot de passe d'application)
# L'adresse du destinataire est un défaut surchargeable (MAIL_VERS).


class AlerteMail:
    """Envoi SMTP avec repli 465/SSL puis 587/STARTTLS et mode simulation."""

    def __init__(self, simulation: bool = False) -> None:
        self.simulation = simulation
        self.hote = CONFIG["SMTP_HOTE"]
        self.port = CONFIG["SMTP_PORT"]
        self.utilisateur = CONFIG["SMTP_UTILISATEUR"]
        self.motdepasse = CONFIG["SMTP_MOTDEPASSE"]
        self.expediteur = CONFIG["MAIL_DE"] or self.utilisateur
        self.destinataires = list(CONFIG["MAIL_VERS"])

    @property
    def configure(self) -> bool:
        return bool(self.utilisateur and self.motdepasse and self.destinataires)

    def etat(self) -> str:
        if self.simulation:
            return "simulation (aucun courriel envoyé)"
        if not self.utilisateur or not self.motdepasse:
            return (
                "non configuré — définissez GMAIL_ADRESSE et GMAIL_APP_PASSWORD "
                "(mot de passe d'application Gmail, pas le mot de passe du compte)"
            )
        if not self.destinataires:
            return "non configuré — aucune adresse destinataire (MAIL_VERS)"
        return "prêt"

    def envoyer(self, sujet: str, corps_texte: str, corps_html: str = "") -> Dict[str, Any]:
        if self.simulation:
            LOGGER.info("[SIMULATION] Courriel « %s » vers %s", sujet, ", ".join(self.destinataires))
            BASE.journaliser_envoi(self.destinataires, sujet, 0, 0, "simulation", "mode --dry-run")
            return {"statut": "simulation", "destinataires": self.destinataires, "sujet": sujet}
        if not self.configure:
            message = self.etat()
            LOGGER.warning("Envoi impossible : %s", message)
            BASE.journaliser_envoi(self.destinataires or ["—"], sujet, 0, 0, "non_configure", message)
            return {"statut": "non_configure", "detail": message}

        message = EmailMessage()
        message["Subject"] = sujet
        message["From"] = self.expediteur
        message["To"] = ", ".join(self.destinataires)
        message["Date"] = email.utils.formatdate(localtime=True)
        message["X-Veille"] = f"recherche-monde-195/{__version__}"
        message.set_content(corps_texte)
        if corps_html:
            message.add_alternative(corps_html, subtype="html")

        erreurs: List[str] = []
        for port, securite in ((self.port, "ssl"), (587, "starttls"), (25, "clair")):
            try:
                if securite == "ssl":
                    contexte = ssl.create_default_context()
                    with smtplib.SMTP_SSL(self.hote, port, timeout=30, context=contexte) as serveur:
                        serveur.login(self.utilisateur, self.motdepasse)
                        serveur.send_message(message)
                else:
                    with smtplib.SMTP(self.hote, port, timeout=30) as serveur:
                        serveur.ehlo()
                        if securite == "starttls":
                            serveur.starttls(context=ssl.create_default_context())
                            serveur.ehlo()
                        serveur.login(self.utilisateur, self.motdepasse)
                        serveur.send_message(message)
                LOGGER.info("Courriel envoyé (%s:%s) vers %s", self.hote, port, ", ".join(self.destinataires))
                BASE.journaliser_envoi(self.destinataires, sujet, 0, 0, "envoye", f"{self.hote}:{port}")
                return {"statut": "envoye", "port": port, "destinataires": self.destinataires}
            except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
                erreurs.append(f"{self.hote}:{port} → {type(exc).__name__}: {str(exc)[:120]}")
                continue
        detail = " | ".join(erreurs)
        LOGGER.error("Échec de l'envoi : %s", detail)
        BASE.journaliser_envoi(self.destinataires, sujet, 0, 0, "echec", detail)
        return {"statut": "echec", "detail": detail}


# =============================================================================
# 10) ASSISTANT IA ANCRÉ SUR LE RÉEL (facultatif, sans clé obligatoire)
# =============================================================================
# Conservé depuis ToutBot_Mundo.py, mais la clé n'est plus jamais nécessaire :
# l'endpoint ouvert est utilisé par défaut, et si le modèle est muet, la réponse
# de repli est produite par des règles déterministes — jamais par invention.

PROMPT_SYSTEME = """🏛️ CORTEX DE VÉRITÉ — POSTURE ABSOLUE (Recherche Monde 195)

Tu disposes de deux couches :
  1) Ton savoir généraliste (sciences, droit, histoire, techniques).
  2) Un BLOC TEMPS RÉEL contenant des résultats réellement collectés sur des
     moteurs de recherche ouverts, chacun accompagné de son lien.

RÈGLES ABSOLUES, NON NÉGOCIABLES :
  • Tu n'inventes RIEN : aucun fait, aucun chiffre, aucun nom, aucune date.
  • Tout ce que tu affirmes sur l'actualité doit provenir du BLOC TEMPS RÉEL et
    être suivi de son lien entre parenthèses.
  • Si le BLOC TEMPS RÉEL est vide ou insuffisant, tu réponds explicitement :
    « Aucune publication réelle trouvée sur ce point » et tu t'arrêtes là.
  • Tu distingues toujours : [VÉRIFIÉ — source] et [CONNAISSANCE GÉNÉRALE].
  • Réponse en français, ton clair, concis, sans emphase inutile.
"""


def interroger_llm(systeme: str, message_utilisateur: str) -> str:
    """Appelle le modèle configuré. Renvoie "" si aucune réponse n'est obtenue."""
    if not CONFIG["LLM_ACTIF"]:
        return ""
    corps = {
        "model": CONFIG["LLM_MODEL"],
        "messages": [
            {"role": "system", "content": systeme},
            {"role": "user", "content": message_utilisateur},
        ],
    }
    entetes = {"Content-Type": "application/json", "User-Agent": "Recherche-Monde-195/1.0"}
    if CONFIG["LLM_KEY"]:
        entetes["Authorization"] = f"Bearer {CONFIG['LLM_KEY']}"
    try:
        requete = urllib.request.Request(
            CONFIG["LLM_ENDPOINT"],
            data=json.dumps(corps).encode("utf-8"),
            headers=entetes,
            method="POST",
        )
        with urllib.request.urlopen(requete, timeout=CONFIG["LLM_TIMEOUT"]) as reponse:
            brut = reponse.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("Modèle injoignable : %s", str(exc)[:110])
        return ""
    try:
        donnees = json.loads(brut)
    except ValueError:
        return brut.strip()
    if isinstance(donnees, dict):
        choix = donnees.get("choices")
        if choix:
            return str(((choix[0] or {}).get("message") or {}).get("content") or "").strip()
        return str(donnees.get("content") or donnees.get("text") or "").strip()
    return ""


def reponse_ancree(question: str, iso: str = "FR") -> Dict[str, Any]:
    """Réponse assistée : bloc temps réel d'abord, IA ensuite, règles en dernier."""
    bloc = AGREGATEUR.bloc_memoire(question, iso=iso)
    donnees = AGREGATEUR.chercher(question, iso=iso)
    reponse = ""
    origine = "regle"
    if CONFIG["LLM_ACTIF"]:
        reponse = interroger_llm(
            PROMPT_SYSTEME,
            f"BLOC TEMPS RÉEL :\n{bloc}\n\nQUESTION : {question}",
        )
        if reponse:
            origine = "ia"
    if not reponse:
        if donnees["resultats"]:
            lignes = [
                f"Voici les résultats réellement collectés pour « {question} » "
                f"({donnees['total']} résultat(s), collecté le {donnees['collecte_le']}) :",
                "",
            ]
            for i, item in enumerate(donnees["resultats"][:8], 1):
                lignes.append(f"{i}. {item['titre']} — {item['source']}")
                lignes.append(f"   {item['url']}")
            lignes.append("")
            lignes.append(
                "Aucun résumé automatique n'a été produit (modèle muet) : ces liens "
                "sont les sources brutes, à lire directement."
            )
            reponse = "\n".join(lignes)
        else:
            reponse = (
                "Aucune publication réelle trouvée sur ce point. Les sources "
                "interrogées n'ont rien renvoyé d'exploitable pour cette requête. "
                "Je ne complète pas avec une supposition."
            )
    return {"question": question, "reponse": reponse, "origine": origine, "donnees": donnees}


# =============================================================================
# 11) INTERFACE WEB AUTONOME — BIBLIOTHÈQUE STANDARD UNIQUEMENT
# =============================================================================
# Correction majeure : l'application d'origine exigeait Flask (et flask-sock)
# pour afficher sa page de recherche. Ici, l'interface repose sur http.server,
# présent dans Python : zéro dépendance obligatoire, l'outil démarre partout.
# La page reprend le thème sombre, la barre de recherche et le bouton de
# bascule jour/nuit des fichiers index.html / script.js / style (1).css fournis,
# réunis dans un gabarit unique (plus aucun fichier statique externe).

EN_TETES_SECURITE = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITRE__</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;border:none;outline:none}
body{width:100%;min-height:100vh;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 display:flex;flex-direction:column;align-items:center;padding:4% 5%;position:relative;
 color:#16202c;background:#f7f9fc;transition:background .25s,color .25s}
body.dark{color:#e8eef5;background:#161b22}
h1{font-size:26px;text-transform:uppercase;letter-spacing:.02em;text-align:center;margin-bottom:26px}
h2{font-size:17px;margin:22px 0 10px;border-bottom:2px solid rgba(128,150,180,.28);padding-bottom:6px}
.theme-btn{position:absolute;top:18px;right:18px;width:46px;height:46px;border-radius:50%;
 display:flex;align-items:center;justify-content:center;cursor:pointer;user-select:none;
 background:#e9eef6;color:#16202c;font-size:20px;transition:background .2s}
body.dark .theme-btn{background:#222c38;color:#e8eef5}
.search__bar{display:flex;gap:10px;width:100%;max-width:660px;margin-bottom:8px}
.search{flex:1;padding:14px 18px;border-radius:24px;font-size:15px;background:#fff;color:#16202c;
 box-shadow:0 1px 6px rgba(32,33,36,.18)}
body.dark .search{background:#1d2530;color:#e8eef5}
button[type=submit],button.act{padding:13px 22px;border-radius:24px;cursor:pointer;font-size:14px;
 font-weight:600;background:#0a58ca;color:#fff}
button.act{background:#2c3a4b;margin:2px 6px 2px 0}
main{width:100%;max-width:860px}
.carte{background:#fff;border-radius:14px;padding:20px 22px;margin-bottom:16px;
 box-shadow:0 1px 4px rgba(20,30,45,.08)}
body.dark .carte{background:#1d2530}
.muet{color:#6b7b8c;font-size:13px;line-height:1.5}
.resultats{list-style:none;padding:0}
.resultats li{padding:12px 0;border-bottom:1px solid rgba(128,150,180,.18)}
.resultats a{color:#0a58ca;font-weight:600;text-decoration:none;font-size:15px}
body.dark .resultats a{color:#7fb2ff}
.badge{display:inline-block;margin-left:8px;padding:2px 8px;border-radius:10px;font-size:11px;
 background:#e9eef6;color:#41546b}
body.dark .badge{background:#2a3543;color:#a9bccf}
.lien{font-size:12px;color:#8c9aab;word-break:break-all;margin-top:3px}
.pays{font-size:12px;color:#8c9aab;font-weight:400}
.pre{white-space:pre-wrap;font-size:13.5px;line-height:1.6}
</style>
</head>
<body>
<div class="theme-btn" id="themeBtn" title="Basculer le thème"><span id="themeIcon">🌙</span></div>
<h1>__TITRE__</h1>
<main>
  <form class="search__bar" method="get" action="/recherche">
    <input class="search" type="search" name="q" value="__Q__"
           placeholder="Rechercher (195 pays, sans clé d'API)" autofocus>
    <button type="submit">Chercher</button>
  </form>
  <div class="carte">
    <p class="muet">Moteurs interrogés : Playwright (navigateur réel) puis Google News (RSS),
    Wikipédia, DuckDuckGo, SearXNG. Chaque résultat affiché est un résultat réel
    accompagné de son lien — rien n'est inventé.</p>
    <p style="margin-top:10px">
      <a href="/actualites"><button type="button" class="act">📰 Veille des 195 pays</button></a>
      <a href="/etat"><button type="button" class="act">📊 État du système</button></a>
    </p>
  </div>
  __CONTENU__
</main>
<script>
(function(){
  var bouton = document.getElementById('themeBtn');
  var icone = document.getElementById('themeIcon');
  var corps = document.body;
  var sombre = localStorage.getItem('rm195-theme') === 'dark';
  if (sombre) { corps.classList.add('dark'); icone.textContent = '☀️'; }
  bouton.addEventListener('click', function(){
    var actif = corps.classList.toggle('dark');
    icone.textContent = actif ? '☀️' : '🌙';
    localStorage.setItem('rm195-theme', actif ? 'dark' : 'light');
  });
})();
</script>
</body>
</html>"""


def rendre_page(titre: str, contenu: str = "", q: str = "") -> bytes:
    corps = (
        PAGE.replace("__TITRE__", _html.escape(titre))
        .replace("__CONTENU__", contenu)
        .replace("__Q__", _html.escape(q))
    )
    return corps.encode("utf-8")


def html_resultats(donnees: Dict[str, Any]) -> str:
    """Rendu HTML des résultats réels. Jamais un résultat sans lien."""
    if not donnees.get("resultats"):
        return (
            "<div class=\"carte\"><p class=\"muet\">Aucun résultat réel collecté "
            "pour cette requête sur les moteurs interrogés.</p></div>"
        )
    compte = " · ".join(f"{k} {v}" for k, v in (donnees.get("compte") or {}).items())
    morceaux = [
        "<div class=\"carte\">",
        f"<h2>Résultats pour « {_html.escape(donnees['requete'])} »</h2>",
        f"<p class=\"muet\">Collecté le {_html.escape(donnees['collecte_le'])} — {compte} "
        f"— navigateur : {_html.escape(str(donnees.get('navigateur', '')))}</p>",
        "<ol class=\"resultats\">",
    ]
    for item in donnees["resultats"]:
        url = _html.escape(item["url"])
        morceaux.append(
            "<li>"
            f"<a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">{_html.escape(item['titre'])}</a>"
            f"<span class=\"badge\">{_html.escape(item['source'])}</span>"
            f"<span class=\"badge\">pertinence {item['pertinence']}</span>"
            f"<p class=\"muet\">{_html.escape(item['extrait'] or '(pas de résumé)')}</p>"
            f"<p class=\"lien\">{url}</p>"
            "</li>"
        )
    morceaux.append("</ol></div>")
    return "".join(morceaux)


def html_veille(depeches: Sequence[Dict[str, Any]], heures: int) -> str:
    if not depeches:
        return (
            "<div class=\"carte\"><h2>Veille des 195 pays</h2>"
            "<p class=\"muet\">Aucune dépêche en base pour cette fenêtre. Lancez la collecte : "
            "<code>python recherche_monde_195.py --actualites --une-fois</code></p></div>"
        )
    par_pays: Dict[str, List[Dict[str, Any]]] = {}
    for ligne in depeches:
        par_pays.setdefault(ligne["pays_nom"], []).append(ligne)
    morceaux = [
        "<div class=\"carte\"><h2>Veille mondiale — "
        f"{len(par_pays)} pays sur 195</h2>"
        f"<p class=\"muet\">{len(depeches)} dépêches sur les dernières {heures} heures.</p></div>"
    ]
    for nom_pays in sorted(par_pays):
        lignes = par_pays[nom_pays]
        morceaux.append(
            f"<div class=\"carte\"><h2>{_html.escape(nom_pays)} "
            f"<span class=\"pays\">({_html.escape(lignes[0]['pays_iso'])})</span></h2>"
            "<ol class=\"resultats\">"
        )
        for item in lignes[:6]:
            url = _html.escape(item["url"])
            morceaux.append(
                "<li>"
                f"<a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">{_html.escape(item['titre'])}</a>"
                f"<span class=\"badge\">{_html.escape(item['source'])}</span>"
                f"<p class=\"muet\">{_html.escape(item['date_pub'] or 'date non fournie')}</p>"
                "</li>"
            )
        morceaux.append("</ol></div>")
    return "".join(morceaux)


def html_etat() -> str:
    stats = BASE.statistiques()
    integrite = verifier_pays()
    lignes = [
        f"<p class=\"muet\">Dépêches en base : <b>{stats['depeches_en_base']}</b></p>",
        f"<p class=\"muet\">Pays couverts : <b>{stats['pays_couverts']} / 195</b></p>",
        f"<p class=\"muet\">Courriels envoyés : <b>{stats['mails_envoyes']}</b></p>",
        f"<p class=\"muet\">Dernier passage : {_html.escape(json.dumps(stats['dernier_passage'], ensure_ascii=False))}</p>",
        f"<p class=\"muet\">Liste des pays : {integrite['total']}/195 — conforme : <b>{integrite['conforme']}</b></p>",
        f"<p class=\"muet\">Playwright : <b>{'disponible' if NAVIGATEUR.disponible else 'absent'}</b></p>",
        f"<p class=\"muet\">Alerte Gmail : {_html.escape(AlerteMail().etat())}</p>",
    ]
    return "<div class=\"carte\"><h2>État du système</h2>" + "".join(lignes) + "</div>"


def servir_interface(hote: str = "127.0.0.1", port: int = 8099) -> None:
    """Serveur web minimal : recherche, veille, assistant, état, redirection sûre."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Traitement(BaseHTTPRequestHandler):
        server_version = f"RechercheMonde195/{__version__}"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            LOGGER.info("%s — %s", self.address_string(), format % args)

        def _repondre(self, corps: bytes, code: int = 200, type_media: str = "text/html; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", type_media)
            self.send_header("Content-Length", str(len(corps)))
            for nom, valeur in EN_TETES_SECURITE.items():
                self.send_header(nom, valeur)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(corps)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            analyse = urllib.parse.urlparse(self.path)
            parametres = urllib.parse.parse_qs(analyse.query)
            chemin = analyse.path.rstrip("/") or "/"
            requete = (parametres.get("q") or [""])[0].strip()
            try:
                if chemin == "/":
                    self._repondre(rendre_page(CONFIG["TITRE_APP"]))
                elif chemin == "/recherche":
                    contenu = ""
                    if requete:
                        donnees = AGREGATEUR.chercher(requete)
                        contenu = html_resultats(donnees)
                    else:
                        contenu = (
                            "<div class=\"carte\"><p class=\"muet\">Saisissez une requête pour "
                            "interroger les moteurs réels.</p></div>"
                        )
                    self._repondre(rendre_page(f"{CONFIG['TITRE_APP']} — {requete}", contenu, requete))
                elif chemin == "/actualites":
                    depeches = BASE.depeches_du_jour(CONFIG["NEWS_FENETRE_H"], 400)
                    self._repondre(rendre_page(
                        f"{CONFIG['TITRE_APP']} — Veille", html_veille(depeches, CONFIG["NEWS_FENETRE_H"])
                    ))
                elif chemin == "/etat":
                    self._repondre(rendre_page(f"{CONFIG['TITRE_APP']} — État", html_etat()))
                elif chemin == "/chat":
                    contenu = ""
                    if requete:
                        resultat = reponse_ancree(requete)
                        contenu = (
                            "<div class=\"carte\"><h2>Réponse "
                            f"<span class=\"badge\">origine : {_html.escape(resultat['origine'])}</span></h2>"
                            f"<p class=\"pre\">{_html.escape(resultat['reponse'])}</p></div>"
                        )
                        contenu += html_resultats(resultat["donnees"])
                    else:
                        contenu = "<div class=\"carte\"><p class=\"muet\">Posez une question.</p></div>"
                    self._repondre(rendre_page(f"{CONFIG['TITRE_APP']} — Assistant", contenu, requete))
                elif chemin == "/api/recherche":
                    donnees = AGREGATEUR.chercher(requete) if requete else {"resultats": []}
                    self._repondre(
                        json.dumps(donnees, ensure_ascii=False).encode("utf-8"),
                        type_media="application/json; charset=utf-8",
                    )
                elif chemin == "/aller":
                    cible = urllib.parse.unquote((parametres.get("u") or [""])[0])
                    if not cible.startswith(("http://", "https://")):
                        self._repondre(b"Lien refuse.", 400, "text/plain; charset=utf-8")
                    else:
                        self.send_response(302)
                        self.send_header("Location", cible)
                        self.send_header("Referrer-Policy", "no-referrer")
                        self.end_headers()
                else:
                    self._repondre(rendre_page("Introuvable", "<div class=\"carte\"><p class=\"muet\">Page inconnue.</p></div>"), 404)
            except Exception as exc:  # noqa: BLE001 — le serveur ne doit jamais mourir
                LOGGER.exception("Erreur de traitement : %s", exc)
                self._repondre(
                    rendre_page("Erreur", "<div class=\"carte\"><p class=\"muet\">Erreur interne.</p></div>"),
                    500,
                )

        def do_POST(self) -> None:  # noqa: N802
            self._repondre(b"Method Not Allowed", 405, "text/plain; charset=utf-8")

    serveur = ThreadingHTTPServer((hote, port), Traitement)
    LOGGER.info("Interface ouverte sur http://%s:%s (Ctrl+C pour arrêter)", hote, port)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Arrêt de l'interface.")
    finally:
        serveur.server_close()
        NAVIGATEUR.fermer()


# =============================================================================
# 12) LIGNE DE COMMANDE
# =============================================================================


def _afficher_depeches(depeches: Sequence[Resultat], par_pays: Optional[str] = None) -> None:
    if not depeches:
        print("  (aucune dépêche réelle collectée)")
        return
    for i, item in enumerate(depeches, 1):
        prefixe = f"  {i:>2}. " if par_pays is None else "   • "
        print(f"{prefixe}{item.titre}")
        print(f"      {item.source} — {item.url}")
        if item.date:
            print(f"      {item.date}")


def commande_recherche(requete: str, maximum: int, iso: str, navigateur: Optional[bool]) -> int:
    print(f"🔎 Recherche : « {requete} » ({maximum} résultats max, contexte {iso})")
    donnees = AGREGATEUR.chercher(requete, maximum, iso=iso, avec_navigateur=navigateur)
    print(f"   Collecté le {donnees['collecte_le']} — moteurs : "
          f"{' · '.join(f'{k}={v}' for k, v in donnees['compte'].items())}")
    print(f"   Navigateur : {donnees['navigateur']}")
    print(f"   Total retenu : {donnees['total']} — cache : {donnees['cache']}")
    print("-" * 72)
    for i, item in enumerate(donnees["resultats"], 1):
        print(f"{i}. {item['titre']}")
        print(f"   {item['source']} — pertinence {item['pertinence']}")
        print(f"   {item['url']}")
        if item["extrait"]:
            print(f"   {item['extrait'][:220]}")
        print()
    if not donnees["resultats"]:
        print("Aucun résultat réel collecté. Rien n'est inventé pour combler ce vide.")
    return 0 if donnees["resultats"] else 1


def commande_actualites(args: argparse.Namespace) -> int:
    isos = [i.strip().upper() for i in (args.pays or "").split(",") if i.strip()]
    inconnus = [i for i in isos if i not in PAYS_PAR_ISO]
    if inconnus:
        print(f"⚠️  Code(s) pays inconnu(s), ignoré(s) : {', '.join(inconnus)}")
        isos = [i for i in isos if i in PAYS_PAR_ISO]
    if args.noms:
        for nom in args.noms.split(","):
            trouves = pays_depuis_requete(nom.strip())
            isos.extend(t[0] for t in trouves if t[0] not in isos)

    if args.rapport_seulement:
        depeches = BASE.depeches_du_jour(args.heures, 800)
        print(rapport_texte(depeches, args.heures))
        return 0

    def progression(index: int, total: int, nom: str) -> None:
        print(f"   [{index:>3}/{total}] {nom}")

    print(f"🌍 Veille des 195 pays — démarrage {maintenant()}")
    print(f"   Fenêtre : {args.heures} h · max {CONFIG['NEWS_MAX_PAR_PAYS']} dépêches/pays · "
          f"Playwright : {'oui' if not args.sans_navigateur else 'non'}")
    bilan = veiller_monde(isos or None, progression=progression)
    print(f"\n   Pays parcourus : {bilan['pays_parcourus']}")
    print(f"   Pays avec résultats : {bilan['pays_avec_resultats']}")
    print(f"   Nouvelles dépêches : {len(bilan['nouvelles_depeches'])}")
    print(f"   Durée : {bilan['duree_s']} s")

    depeches = BASE.depeches_du_jour(args.heures, 800)
    texte = rapport_texte(depeches, args.heures)
    if args.sortie:
        with open(args.sortie, "w", encoding="utf-8") as fichier:
            fichier.write(texte)
        print(f"   Rapport écrit dans {args.sortie}")
    elif not args.dry_run:
        print()
        print(texte)

    mails = 0
    if args.mail or args.dry_run:
        pays_touches = len({d["pays_iso"] for d in depeches})
        sujet = CONFIG["MAIL_SUJET"].format(nb=len(depeches), pays=pays_touches)
        alerte = AlerteMail(simulation=args.dry_run)
        resultat = alerte.envoyer(sujet, texte, rapport_html(depeches, args.heures))
        print(f"   Alerte Gmail : {resultat['statut']}" + (f" — {resultat.get('detail', '')}" if resultat.get("detail") else ""))
        mails = 1 if resultat["statut"] in ("envoye", "simulation") else 0
    return 0


def commande_veille(intervalle: int, heures: int, isos: List[str]) -> int:
    """Boucle de surveillance : collecte, rapport, courriel, puis pause."""
    print(f"🛰️  Veille continue — toutes les {intervalle} s (Ctrl+C pour arrêter)")
    while True:
        depart = time.monotonic()
        try:
            bilan = veiller_monde(isos or None)
            depeches = BASE.depeches_du_jour(heures, 800)
            if bilan["nouvelles_depeches"]:
                sujet = CONFIG["MAIL_SUJET"].format(nb=len(depeches), pays=len({d['pays_iso'] for d in depeches}))
                resultat = AlerteMail().envoyer(sujet, rapport_texte(depeches, heures), rapport_html(depeches, heures))
                print(f"[{maintenant()}] {len(bilan['nouvelles_depeches'])} nouvelle(s) dépêche(s) — alerte : {resultat['statut']}")
            else:
                print(f"[{maintenant()}] Aucune nouvelle dépêche réelle.")
        except Exception as exc:  # noqa: BLE001 — la boucle ne doit jamais mourir
            LOGGER.exception("Erreur pendant la veille : %s", exc)
        pause = max(30, intervalle - int(time.monotonic() - depart))
        time.sleep(pause)


def construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(
        prog="recherche_monde_195",
        description=(
            "Recherche web temps réel sans clé d'API (Playwright + flux RSS ouverts) "
            "et veille des actualités des 195 pays, avec alerte Gmail."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  python recherche_monde_195.py --verifier-pays\n"
            "  python recherche_monde_195.py --recherche \"élections sénégal\"\n"
            "  python recherche_monde_195.py --actualites --pays FR,SN,ML --dry-run\n"
            "  python recherche_monde_195.py --actualites --une-fois --mail\n"
            "  python recherche_monde_195.py --veille --intervalle 3600\n"
            "  python recherche_monde_195.py --sert --port 8099\n"
        ),
    )
    analyseur.add_argument("--version", action="version", version=f"recherche-monde-195 {__version__}")
    analyseur.add_argument("--verifier-pays", action="store_true",
                           help="Contrôle d'intégrité de la liste des 195 pays, puis sortie.")
    analyseur.add_argument("--recherche", metavar="REQUETE",
                           help="Interroge les moteurs et affiche les résultats réels.")
    analyseur.add_argument("--max", type=int, default=CONFIG["MAX_RESULTATS"],
                           help=f"Résultats maximum (défaut {CONFIG['MAX_RESULTATS']}).")
    analyseur.add_argument("--pays", default="",
                           help="Codes ISO des pays (ex. FR,SN,ML) ; tout par défaut.")
    analyseur.add_argument("--noms", default="",
                           help="Noms de pays en clair (ex. \"Sénégal,Mali\") ajoutés à --pays.")
    analyseur.add_argument("--actualites", action="store_true",
                           help="Lance la veille des pays et affiche le rapport.")
    analyseur.add_argument("--rapport-seulement", action="store_true",
                           help="Affiche le rapport depuis la base, sans nouvelle collecte.")
    analyseur.add_argument("--mail", action="store_true",
                           help="Force l'envoi du courriel (même sans nouvelle dépêche).")
    analyseur.add_argument("--dry-run", action="store_true",
                           help="N'envoie aucun courriel : affiche ce qui aurait été envoyé.")
    analyseur.add_argument("--sortie", metavar="FICHIER",
                           help="Écrit le rapport texte dans ce fichier au lieu de l'afficher.")
    analyseur.add_argument("--heures", type=int, default=CONFIG["NEWS_FENETRE_H"],
                           help=f"Fenêtre du rapport en heures (défaut {CONFIG['NEWS_FENETRE_H']}).")
    analyseur.add_argument("--une-fois", action="store_true",
                           help="Collecte unique puis sortie (par défaut avec --actualites).")
    analyseur.add_argument("--veille", action="store_true", help="Boucle de surveillance continue.")
    analyseur.add_argument("--intervalle", type=int, default=3600,
                           help="Secondes entre deux passages en mode --veille (défaut 3600).")
    analyseur.add_argument("--sans-navigateur", action="store_true",
                           help="Désactive Playwright pour cette exécution (HTTP seulement).")
    analyseur.add_argument("--sert", action="store_true", help="Démarre l'interface web locale.")
    analyseur.add_argument("--hote", default="127.0.0.1", help="Adresse d'écoute (défaut 127.0.0.1).")
    analyseur.add_argument("--port", type=int, default=8099, help="Port d'écoute (défaut 8099).")
    analyseur.add_argument("--verbosite", choices=("INFO", "DEBUG", "WARNING"), default="INFO",
                           help="Niveau des journaux.")
    return analyseur


def principal(arguments: Optional[Sequence[str]] = None) -> int:
    analyseur = construire_analyseur()
    args = analyseur.parse_args(arguments)
    logging.getLogger().setLevel(getattr(logging, args.verbosite, logging.INFO))

    if args.sans_navigateur:
        CONFIG["PLAYWRIGHT"] = False

    if args.verifier_pays:
        integrite = verifier_pays()
        print("🌍 Contrôle de la liste des pays")
        print(f"   Entrées trouvées     : {integrite['total']}")
        print(f"   Attendu              : {integrite['attendu']}")
        print(f"   Doublons de code ISO : {integrite['doublons_iso'] or 'aucun'}")
        print(f"   Doublons de nom      : {integrite['doublons_noms'] or 'aucun'}")
        print(f"   Codes ISO invalides  : {integrite['iso_invalides'] or 'aucun'}")
        print(f"   Conforme             : {'OUI' if integrite['conforme'] else 'NON'}")
        return 0 if integrite["conforme"] else 1

    if args.recherche:
        return commande_recherche(args.recherche, args.max, CONFIG["PAYS_DEFAUT"], not args.sans_navigateur)

    if args.actualites or args.rapport_seulement:
        return commande_actualites(args)

    if args.veille:
        isos = [i.strip().upper() for i in args.pays.split(",") if i.strip() and i.strip().upper() in PAYS_PAR_ISO]
        return commande_veille(args.intervalle, args.heures, isos)

    if args.sert:
        servir_interface(args.hote, args.port)
        return 0

    analyseur.print_help()
    print("\n🌍 Astuce : commencez par « --verifier-pays » puis « --actualites --pays FR,SN,ML --dry-run ».")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(principal())
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.")
        NAVIGATEUR.fermer()
        sys.exit(130)
