# -*- coding: utf-8 -*-
# =============================================================================
# 🌍 TOUTBOT MUNDO — MONO-FICHIER UNIFIÉ  (application V7 + moteur de monétisation V8)
# -----------------------------------------------------------------------------
# Ce fichier unique est la FUSION de :
#   • TOUTBOT_MUNDO_V7.py        → réseau social 100 % texte + portefeuille + admin
#   • monetisation_v8.py         → moteur de monétisation à la vue + reversement Mobile Money
#   • test_v7.py · smoke_v8.py · test_monetisation_v8.py  → batteries de tests (ANNEXE B)
#   • LISEZMOI_V7.txt            → documentation (ANNEXE A)
#
# LOI DE L'APPLICATION : 100 % TEXTE (aucune image, vidéo, audio, pièce jointe).
# Dépendances : flask (obligatoire), flask-sock (optionnel), pywebpush (optionnel).
#
# DÉMARRAGE
#   pip install flask flask-sock
#   python ToutBot_Mundo.py --seed-admin zeusad --telephone 90000000
#   SECRET_KEY=votre-cle python ToutBot_Mundo.py --port 8099
#   python ToutBot_Mundo.py --demo
#
# Le moteur V8 est branché automatiquement à l'import (tables vues_* / gains_* /
# reversements + routes /vue/<post_id> et /mon-mesure), exactement comme le faisait
# « monetisation_v8.brancher_sur_toutbot(M) » en module séparé.
# =============================================================================

# =============================================================================
# PARTIE 1 — MOTEUR DE MONÉTISATION V8 (ex monetisation_v8.py)
# =============================================================================

# -*- coding: utf-8 -*-
# =============================================================================
# 🌍 TOUTBOT MUNDO V8 — MOTEUR DE MONÉTISATION À LA VUE + REVERSEMENT MOBILE MONEY
# -----------------------------------------------------------------------------
# Module greffable sur TOUTBOT_MUNDO_V7.py (mono-fichier Flask + SQLite).
# Il ne remplace rien : il AJOUTE le flux de recettes « vues » et le cycle de
# reversement Mobile Money, branché sur les tables EXISTANTES (users, journal,
# portefeuille, recettes, escrow, audit_chain).
#
#   LOI DE L'APPLICATION RESPECTÉE : 100 % TEXTE.
#   Ce module ne lit, ne stocke et ne sert AUCUN média. La « vue » est un
#   compteur de lectures de publications écrites, rien d'autre.
#
#   ARGENT : tous les montants internes sont des ENTIERS DE CENTIMES de FCFA
#   (1 FCFA = 100 centimes). Aucun float dans un calcul d'argent, aucune
#   décimale perdue : le reste de la répartition est toujours attribué.
#
#   Modèle : 50 000 FCFA par 1 000 vues pondérées
#            75 % application  /  25 % créateur
#
# Branchement dans TOUTBOT_MUNDO_V7.py (V8) :
#     import monetisation_v8 as MON
#     MON.brancher_sur_toutbot(M)      # tables + routes Flask
#
# Exécution autonome (sans Flask) :
#     python monetisation_v8.py        # affiche la table des 6 paliers
# =============================================================================

from __future__ import annotations

import contextlib
import datetime as _dt
import os
import secrets
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional, Tuple

__version__ = "8.0.0"

# =============================================================================
# 1) OUTILS MONÉTAIRES — ENTIERS DE CENTIMES, JAMAIS DE FLOTTANT
# =============================================================================
DEVISE = "FCFA"
CENTIMES_PAR_FCFA = 100


def _d(valeur: Any) -> Decimal:
    """Convertit en Decimal SANS passer par un float binaire."""
    if isinstance(valeur, Decimal):
        return valeur
    return Decimal(str(valeur))


def fcfa_vers_centimes(valeur: Any) -> int:
    """1 250,50 FCFA -> 125050 centimes (arrondi commercial)."""
    return int((_d(valeur) * CENTIMES_PAR_FCFA).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def centimes_vers_fcfa(centimes: int) -> Decimal:
    """1250 centimes -> Decimal('12.50')."""
    return (Decimal(int(centimes)) / CENTIMES_PAR_FCFA).quantize(Decimal("0.01"))


def formater(centimes: int) -> str:
    """12 500 FCFA  /  40,50 FCFA  — séparateur de milliers = espace."""
    valeur = centimes_vers_fcfa(centimes)
    entier = int(valeur)
    texte = f"{entier:,}".replace(",", " ")
    decimales = (valeur - entier) * 100
    if decimales:
        return f"{texte},{int(decimales):02d} {DEVISE}"
    return f"{texte} {DEVISE}"


def _part(centimes: int, pct: Decimal) -> int:
    return int((Decimal(int(centimes)) * pct / DEUX_DECIMALES_100).quantize(Decimal("1"),
                                                                           rounding=ROUND_HALF_UP))


DEUX_DECIMALES_100 = Decimal("100")

# =============================================================================
# 2) LA CONSTANTE UNIQUE DU MODÈLE ÉCONOMIQUE  (le barème n'existe qu'ICI)
# =============================================================================
TAUX_POOL_PAR_MILLE_CENTIMES: int = fcfa_vers_centimes(50000)   # 50 000 FCFA / 1 000 vues
PART_APPLICATION_PCT: Decimal = Decimal("75")
PART_CREATEUR_PCT: Decimal = Decimal("25")
VUES_PAR_TRANCHE: int = 1000

# Pondération des catégories de vues (une vue d'abonné vaut plus qu'une vue de passage)
COEFFICIENTS_VUES: Dict[str, Decimal] = {
    "abonne":       Decimal("1.0"),   # abonné payant, palier validé
    "audience":     Decimal("0.6"),   # visiteur non abonné
    "publicitaire": Decimal("1.5"),   # impression publicitaire validée
    "live":         Decimal("1.0"),   # présence >= 60 s dans un live ÉCRIT
    "profil":       Decimal("0.0"),   # page profil / recherche : non monétisable
}

# Anti-fraude
DUREE_LECTURE_MIN_MS: int = 3000        # 3 s de lecture réelle minimum
VUES_MAX_HEURE: int = 120               # vélocité maximale par compte connecté
IP_MAX_COMPTES_HEURE: int = 20          # ferme de clics : IP → comptes distincts
MAX_COMPTES_PAR_NUMERO: int = 3         # un numéro Mobile Money ne paie pas 4 comptes
FENETRE_DEDOUBLONNAGE_H: int = 24       # 1 vue / visiteur / publication / 24 h

# Conditions de reversement
SEUIL_REVERSEMENT_CENTIMES: int = fcfa_vers_centimes(1000)           # 1 000 FCFA minimum
PLAFOND_APPROBATION_SIMPLE_CENTIMES: int = fcfa_vers_centimes(500000)  # au-delà : double signature
PLAFOND_MENSUEL_GLOBAL_CENTIMES: int = fcfa_vers_centimes(2000000)   # plafond de sécurité global
FRAIS_A_CHARGE_DE: str = "application"   # les frais opérateur sortent des 75 %, JAMAIS des 25 %
MODE_POOL_DEFAUT: str = "plafonne"       # plafonne | garanti

# =============================================================================
# 3) LES 6 PALIERS DE REVERSEMENT — DÉFINITION UNIQUE, DÉRIVÉE DU BARÈME
# -----------------------------------------------------------------------------
# Chaque palier est calculé par figures_pour_vues() à partir du barème ci-dessus :
# aucun montant n'est écrit à la main, donc la table ne peut pas diverger du code.
# =============================================================================
PALIERS_REVERSEMENT: List[Dict[str, Any]] = [
    {"niveau": 1, "nom": "Débutant", "vues_min": 1000,   "vues_max": 1999,
     "vues_reference": 1000,   "delai_jours": 30, "priorite": "normale",
     "kyc": "N1 — numéro vérifié par code"},
    {"niveau": 2, "nom": "Actif", "vues_min": 2000,       "vues_max": 2999,
     "vues_reference": 2000,   "delai_jours": 21, "priorite": "normale",
     "kyc": "N2 — numéro vérifié + opérateur confirmé"},
    {"niveau": 3, "nom": "Confirmé", "vues_min": 3000,    "vues_max": 4999,
     "vues_reference": 3000,   "delai_jours": 14, "priorite": "accélérée",
     "kyc": "N3 — + nom complet concordant"},
    {"niveau": 4, "nom": "Établi", "vues_min": 5000,      "vues_max": 9999,
     "vues_reference": 5000,   "delai_jours": 10, "priorite": "accélérée",
     "kyc": "N4 — + pièce d'identité"},
    {"niveau": 5, "nom": "Influent", "vues_min": 10000,   "vues_max": 99999,
     "vues_reference": 10000,  "delai_jours": 7,  "priorite": "haute",
     "kyc": "N5 — + pièce d'identité et selfie daté"},
    {"niveau": 6, "nom": "Sans plafond", "vues_min": 100000, "vues_max": None,
     "vues_reference": 100000, "delai_jours": 3,  "priorite": "prioritaire",
     "kyc": "N6 — + contrat créateur signé (au-delà : payé au barème, sans limite)"},
]

# Opérateur de référence servi pour illustrer les frais dans la table des paliers
OPERATEUR_REFERENCE: Tuple[str, str] = ("Bénin", "MTN")   # 1,0 % (CinetPay Mass Payout)

# =============================================================================
# 4) GRILLE DES FRAIS OPÉRATEUR (CinetPay Mass Payout, relevé de tarification)
# -----------------------------------------------------------------------------
# Bénin MTN/Moov 1 % · Côte d'Ivoire MTN 1,3 % / Orange 1,5 % / Wave 2 % / Moov 1,8 %
# Cameroun Orange 1 % / MTN 1,5 % · Mali Orange & Moov 1,5 % · Sénégal Wave 2 % /
# Orange 1,8 % · Togo Moov 1 % / TMoney 1 % · Guinée Orange 4 % / MTN 3 % ·
# RDC Orange 1 % / MPesa 2 % / Airtel 2 %
# =============================================================================
GRILLE_FRAIS_PCT: Dict[Tuple[str, str], Decimal] = {
    ("Bénin", "MTN"):      Decimal("1.0"),
    ("Bénin", "Moov"):     Decimal("1.0"),
    ("Côte d'Ivoire", "MTN"):    Decimal("1.3"),
    ("Côte d'Ivoire", "Orange"): Decimal("1.5"),
    ("Côte d'Ivoire", "Moov"):   Decimal("1.8"),
    ("Côte d'Ivoire", "Wave"):   Decimal("2.0"),
    ("Cameroun", "Orange"): Decimal("1.0"),
    ("Cameroun", "MTN"):    Decimal("1.5"),
    ("Mali", "Orange"):     Decimal("1.5"),
    ("Mali", "Moov"):       Decimal("1.5"),
    ("Sénégal", "Wave"):    Decimal("2.0"),
    ("Sénégal", "Orange"):  Decimal("1.8"),
    ("Togo", "Moov"):       Decimal("1.0"),
    ("Togo", "TMoney"):     Decimal("1.0"),
    ("Guinée", "Orange"):   Decimal("4.0"),
    ("Guinée", "MTN"):      Decimal("3.0"),
    ("RDC", "Orange"):      Decimal("1.0"),
    ("RDC", "MPesa"):       Decimal("2.0"),
    ("RDC", "Airtel"):      Decimal("2.0"),
}
FRAIS_DEFAUT_PCT: Decimal = Decimal("2.0")   # repli prudent si couple pays/opérateur inconnu

# Agrégateurs utilisables pour le versement (documentation vérifiée le 2026-09-24)
AGREGATEURS: Dict[str, Dict[str, Any]] = {
    "cinetpay": {"nom": "CinetPay Mass Payout", "type": "api_lot",
                 "pays": ["Côte d'Ivoire", "Mali", "Sénégal", "Bénin", "Cameroun",
                          "Guinée", "RDC", "Togo"],
                 "recommandation": "PRINCIPAL — couvre Bénin et Côte d'Ivoire"},
    "fedapay":  {"nom": "FedaPay Payouts", "type": "api_unitaire",
                 "pays": ["Bénin", "Côte d'Ivoire", "Togo"],
                 "recommandation": "SECOURS — utile sur MTN/Moov Bénin et Togo"},
    "mtn_momo": {"nom": "MTN MoMo Disbursement", "type": "api_operateur",
                 "pays": ["tous pays MTN MoMo"],
                 "recommandation": "DIRECT — statut marchand MTN requis"},
    "orange":   {"nom": "Orange Money Web Payment", "type": "api_operateur",
                 "pays": ["Mali", "Cameroun", "Côte d'Ivoire", "Sénégal", "Madagascar",
                          "Botswana", "Guinée Conakry", "Guinée-Bissau", "Sierra Leone",
                          "RDC", "RCA"],
                 "recommandation": "DIRECT — contrat marchand Orange requis"},
}


# =============================================================================
# 5) CALCULS — LE BARÈME, LA RÉPARTITION, LES PALIERS
# =============================================================================
def pool_bareme_centimes(vues_ponderees: Decimal) -> int:
    """Pool brut théorique : 50 000 FCFA par tranche de 1 000 vues, sans plafond."""
    tranches = _d(vues_ponderees) / Decimal(VUES_PAR_TRANCHE)
    return int((tranches * Decimal(TAUX_POOL_PAR_MILLE_CENTIMES)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def repartir_pool(pool_centimes: int) -> Dict[str, int]:
    """75 % application / 25 % créateurs. Le reste d'arrondi va à l'application,
    pour que application + créateur = pool exactement."""
    part_app = _part(pool_centimes, PART_APPLICATION_PCT)
    return {"pool": int(pool_centimes), "part_application": part_app,
            "part_createurs": int(pool_centimes) - part_app}


def figures_pour_vues(vues_ponderees: Decimal,
                      recettes_encaissees_centimes: Optional[int] = None,
                      mode: Optional[str] = None) -> Dict[str, Any]:
    """Figures complètes pour un volume de vues donné.

    mode='plafonne' : pool = min(barème, recettes réellement encaissées)  -> jamais de caisse vide
    mode='garanti'  : pool = barème                                        -> exige des contrats signés
    """
    mode = (mode or os.environ.get("MODE_POOL", MODE_POOL_DEFAUT)).lower()
    bareme = pool_bareme_centimes(vues_ponderees)
    if mode == "garanti" or recettes_encaissees_centimes is None:
        pool, retenu = bareme, "bareme"
    else:
        pool = min(bareme, int(recettes_encaissees_centimes))
        retenu = "plafonne" if pool < bareme else "bareme"
    parts = repartir_pool(pool)
    parts.update({
        "mode": mode, "retenu": retenu, "bareme_centimes": bareme,
        "vues_ponderees": _d(vues_ponderees),
        "vues_brutes_equivalentes": int((_d(vues_ponderees)).quantize(Decimal("1"),
                                                                    rounding=ROUND_HALF_UP)),
        "application_fcfa": centimes_vers_fcfa(parts["part_application"]),
        "createurs_fcfa": centimes_vers_fcfa(parts["part_createurs"]),
    })
    return parts


def frais_operateur(montant_centimes: int, pays: str, operateur: str) -> Dict[str, Any]:
    """Frais de versement Mobile Money selon la grille, et qui les supporte."""
    pct = GRILLE_FRAIS_PCT.get((pays, operateur), FRAIS_DEFAUT_PCT)
    frais = _part(montant_centimes, pct)
    a_charge_app = FRAIS_A_CHARGE_DE == "application"
    net = montant_centimes if a_charge_app else int(montant_centimes) - frais
    return {"pays": pays, "operateur": operateur, "pct": pct,
            "frais_centimes": frais, "frais_fcfa": centimes_vers_fcfa(frais),
            "a_charge": FRAIS_A_CHARGE_DE, "net_verse_centimes": net,
            "net_verse_fcfa": centimes_vers_fcfa(net)}


def palier_pour_vues(vues_ponderees: Decimal) -> Dict[str, Any]:
    """Rend le palier de reversement correspondant au volume mensuel de vues."""
    vues = int(_d(vues_ponderees).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    for palier in PALIERS_REVERSEMENT:
        plafond = palier["vues_max"]
        if vues >= palier["vues_min"] and (plafond is None or vues <= plafond):
            return palier
    return PALIERS_REVERSEMENT[0] if vues < PALIERS_REVERSEMENT[0]["vues_min"] \
        else PALIERS_REVERSEMENT[-1]


def palier_pour_gain(montant_centimes: int) -> Dict[str, Any]:
    """Palier atteint par un gain mensuel (le palier le plus élevé dont le
    gain de référence est couvert)."""
    retenu = PALIERS_REVERSEMENT[0]
    for palier in PALIERS_REVERSEMENT:
        seuil = figures_pour_vues(Decimal(palier["vues_reference"]))["part_createurs"]
        if int(montant_centimes) >= seuil:
            retenu = palier
    return retenu


def tableau_paliers_reversement(pays: str = None, operateur: str = None) -> List[Dict[str, Any]]:
    """LA TABLE DES 6 PALIERS — toutes les valeurs sont calculées ici, à la volée."""
    pays = pays or OPERATEUR_REFERENCE[0]
    operateur = operateur or OPERATEUR_REFERENCE[1]
    lignes: List[Dict[str, Any]] = []
    for palier in PALIERS_REVERSEMENT:
        vues = Decimal(palier["vues_reference"])
        fig = figures_pour_vues(vues, mode="garanti")
        frais = frais_operateur(fig["part_createurs"], pays, operateur)
        lignes.append({
            "niveau": palier["niveau"], "nom": palier["nom"],
            "vues_min": palier["vues_min"], "vues_max": palier["vues_max"],
            "vues_reference": palier["vues_reference"],
            "delai_jours": palier["delai_jours"], "priorite": palier["priorite"],
            "kyc": palier["kyc"],
            "pool_centimes": fig["pool"],
            "part_application_centimes": fig["part_application"],
            "part_createurs_centimes": fig["part_createurs"],
            "frais_centimes": frais["frais_centimes"],
            "frais_pct": frais["pct"],
            "net_verse_centimes": frais["net_verse_centimes"],
            "au_dessus_plafond_simple": fig["part_createurs"] > PLAFOND_APPROBATION_SIMPLE_CENTIMES,
            "sous_seuil": fig["part_createurs"] < SEUIL_REVERSEMENT_CENTIMES,
        })
    return lignes


# =============================================================================
# 6) BASE DE DONNÉES — ADAPTATEUR (fonctionne seul ou branché sur Flask/V7)
# =============================================================================
_FABRIQUE_CONNEXION: Optional[Callable[[], Any]] = None


def definir_fabrique_connexion(fabrique: Optional[Callable[[], Any]]) -> None:
    """Branche le moteur sur la connexion de l'application (M.db de TOUTBOT)."""
    global _FABRIQUE_CONNEXION
    _FABRIQUE_CONNEXION = fabrique


def chemin_db() -> str:
    return os.environ.get(
        "TOUTBOT_DB",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "toutbot_mundo.db"))


def _v8_connexion() -> Any:
    if _FABRIQUE_CONNEXION is not None:
        return _FABRIQUE_CONNEXION()
    conn = sqlite3.connect(chemin_db())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextlib.contextmanager
def _curseur():
    """Connexion + transaction. Ne ferme que ce que nous avons ouvert nous-mêmes."""
    conn = _v8_connexion()
    propre = _FABRIQUE_CONNEXION is None
    try:
        yield conn
        conn.commit()
    finally:
        if propre:
            conn.close()


def _v8_maintenant() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _jour(horodatage: Optional[str] = None) -> str:
    return (horodatage or _v8_maintenant())[:10]


def _mois(horodatage: Optional[str] = None) -> str:
    return (horodatage or _v8_maintenant())[:7]


def mois_precedent(mois: Optional[str] = None) -> str:
    mois = mois or _mois()
    annee, num = int(mois[:4]), int(mois[5:7])
    num -= 1
    if num == 0:
        annee, num = annee - 1, 12
    return f"{annee:04d}-{num:02d}"


SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS vues_publications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL,
    createur_id  INTEGER NOT NULL,
    visiteur_id  INTEGER,
    empreinte    TEXT    NOT NULL,
    categorie    TEXT    NOT NULL,
    poids_x100   INTEGER NOT NULL DEFAULT 100,
    duree_ms     INTEGER NOT NULL DEFAULT 0,
    adresse_ip   TEXT    NOT NULL DEFAULT '',
    valide       INTEGER NOT NULL DEFAULT 1,
    motif_rejet  TEXT    NOT NULL DEFAULT '',
    jour         TEXT    NOT NULL,
    cree_le      TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vues_unique_valide
    ON vues_publications(empreinte, post_id, jour) WHERE valide = 1;
CREATE INDEX IF NOT EXISTS idx_vues_createur_jour ON vues_publications(createur_id, jour);
CREATE INDEX IF NOT EXISTS idx_vues_visiteur      ON vues_publications(empreinte, cree_le);
CREATE INDEX IF NOT EXISTS idx_vues_rejet         ON vues_publications(valide, motif_rejet);

CREATE TABLE IF NOT EXISTS vues_journalieres (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    createur_id  INTEGER NOT NULL,
    jour         TEXT    NOT NULL,
    vues_brutes  INTEGER NOT NULL DEFAULT 0,
    vues_pond_x100 INTEGER NOT NULL DEFAULT 0,
    ecartees     INTEGER NOT NULL DEFAULT 0,
    maj_le       TEXT    NOT NULL,
    UNIQUE(createur_id, jour)
);

CREATE TABLE IF NOT EXISTS revenus_publicitaires (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mois        TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    libelle     TEXT    NOT NULL DEFAULT '',
    montant     INTEGER NOT NULL,
    encaisse_le TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revenus_mois ON revenus_publicitaires(mois);

CREATE TABLE IF NOT EXISTS pool_monetisation (
    mois                TEXT PRIMARY KEY,
    vues_pond_x100      INTEGER NOT NULL,
    bareme_centimes     INTEGER NOT NULL,
    recettes_centimes   INTEGER NOT NULL,
    pool_retenu_centimes INTEGER NOT NULL,
    part_app_centimes   INTEGER NOT NULL,
    part_createurs_centimes INTEGER NOT NULL,
    mode                TEXT    NOT NULL,
    cloture_le          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS gains_createurs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    createur_id  INTEGER NOT NULL,
    mois         TEXT    NOT NULL,
    vues_pond_x100 INTEGER NOT NULL DEFAULT 0,
    part_centimes INTEGER NOT NULL DEFAULT 0,
    palier       INTEGER NOT NULL DEFAULT 1,
    statut       TEXT    NOT NULL DEFAULT 'retenu',
    payout_phone TEXT    NOT NULL DEFAULT '',
    payout_op    TEXT    NOT NULL DEFAULT '',
    maj_le       TEXT    NOT NULL,
    UNIQUE(createur_id, mois)
);
CREATE INDEX IF NOT EXISTS idx_gains_statut ON gains_createurs(statut, mois);

CREATE TABLE IF NOT EXISTS reversements (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    createur_id       INTEGER NOT NULL,
    mois              TEXT    NOT NULL,
    montant_centimes  INTEGER NOT NULL,
    frais_centimes    INTEGER NOT NULL DEFAULT 0,
    net_centimes      INTEGER NOT NULL,
    numero            TEXT    NOT NULL,
    operateur         TEXT    NOT NULL,
    pays              TEXT    NOT NULL DEFAULT 'Bénin',
    agregateur        TEXT    NOT NULL DEFAULT 'cinetpay',
    reference_interne TEXT    NOT NULL UNIQUE,
    reference_api     TEXT    NOT NULL DEFAULT '',
    statut            TEXT    NOT NULL DEFAULT 'en_attente',
    motif             TEXT    NOT NULL DEFAULT '',
    date_prevue       TEXT    NOT NULL DEFAULT '',
    approuve_par      INTEGER,
    approuve_le       TEXT,
    cree_le           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rev_statut ON reversements(statut);
CREATE INDEX IF NOT EXISTS idx_rev_createur ON reversements(createur_id, mois);

CREATE TABLE IF NOT EXISTS kyc_numeros (
    user_id     INTEGER PRIMARY KEY,
    numero      TEXT NOT NULL,
    operateur   TEXT NOT NULL DEFAULT 'MTN',
    statut      TEXT NOT NULL DEFAULT 'a_verifier',
    code_envoye TEXT NOT NULL DEFAULT '',
    tentatives  INTEGER NOT NULL DEFAULT 0,
    verifie_le  TEXT
);
CREATE INDEX IF NOT EXISTS idx_kyc_numero ON kyc_numeros(numero);
"""


def init_monetisation() -> None:
    """Crée les tables V8. Idempotent : appelable à chaque démarrage."""
    with _curseur() as conn:
        conn.executescript(SCHEMA_V8)


# =============================================================================
# 7) COMPTAGE DES VUES + ANTI-FRAUDE
# =============================================================================
def _poids_x100(categorie: str) -> int:
    return int((COEFFICIENTS_VUES.get(categorie, Decimal("0")) * 100).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def enregistrer_vue(post_id: int, createur_id: int, categorie: str = "audience",
                    empreinte: str = "", visiteur_id: Optional[int] = None,
                    duree_ms: int = 5000, adresse_ip: str = "") -> Dict[str, Any]:
    """Enregistre UNE vue, après tous les contrôles. Le client n'est jamais cru.

    Contrôles : catégorie monétisable · durée de lecture · doublon (1 vue/visiteur/
    publication/24 h) · vélocité par compte · ferme de clics par IP.
    """
    init_monetisation()
    poids = _poids_x100(categorie)
    if poids <= 0:
        return {"acceptee": False, "motif": "categorie_non_monetisable", "poids_x100": 0}
    if int(duree_ms) < DUREE_LECTURE_MIN_MS:
        return {"acceptee": False, "motif": "lecture_trop_courte", "poids_x100": 0}
    empreinte = (empreinte or "").strip()
    if not empreinte:
        return {"acceptee": False, "motif": "empreinte_absente", "poids_x100": 0}

    jour, horodatage = _jour(), _v8_maintenant()
    with _curseur() as conn:
        # a) doublon visiteur / publication / jour
        deja = conn.execute(
            "SELECT id FROM vues_publications WHERE empreinte=? AND post_id=? AND jour=?"
            " AND valide=1 LIMIT 1",
            (empreinte, post_id, jour)).fetchone()
        if deja is not None:
            conn.execute(
                "INSERT INTO vues_publications (post_id, createur_id, visiteur_id, empreinte,"
                " categorie, poids_x100, duree_ms, adresse_ip, valide, motif_rejet, jour, cree_le)"
                " VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
                (post_id, createur_id, visiteur_id, empreinte, categorie, poids,
                 int(duree_ms), adresse_ip, "doublon_24h", jour, horodatage))
            return {"acceptee": False, "motif": "doublon_24h", "poids_x100": 0}

        # b) vélocité par compte connecté
        if visiteur_id is not None:
            heure = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
                     ).strftime("%Y-%m-%d %H:%M:%S")
            nb = conn.execute(
                "SELECT COUNT(*) n FROM vues_publications WHERE visiteur_id=? AND cree_le>=?"
                " AND valide=1", (visiteur_id, heure)).fetchone()["n"]
            if nb >= VUES_MAX_HEURE:
                conn.execute(
                    "INSERT INTO vues_publications (post_id, createur_id, visiteur_id, empreinte,"
                    " categorie, poids_x100, duree_ms, adresse_ip, valide, motif_rejet, jour, cree_le)"
                    " VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
                    (post_id, createur_id, visiteur_id, empreinte, categorie, poids,
                     int(duree_ms), adresse_ip, "velocite", jour, horodatage))
                return {"acceptee": False, "motif": "velocite", "poids_x100": 0}

        # c) ferme de clics : une même IP servant trop de comptes distincts
        if adresse_ip:
            heure = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)
                     ).strftime("%Y-%m-%d %H:%M:%S")
            comptes = conn.execute(
                "SELECT COUNT(DISTINCT visiteur_id) n FROM vues_publications"
                " WHERE adresse_ip=? AND cree_le>=? AND visiteur_id IS NOT NULL",
                (adresse_ip, heure)).fetchone()["n"]
            if comptes >= IP_MAX_COMPTES_HEURE:
                conn.execute(
                    "INSERT INTO vues_publications (post_id, createur_id, visiteur_id, empreinte,"
                    " categorie, poids_x100, duree_ms, adresse_ip, valide, motif_rejet, jour, cree_le)"
                    " VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
                    (post_id, createur_id, visiteur_id, empreinte, categorie, poids,
                     int(duree_ms), adresse_ip, "ferme_de_clics", jour, horodatage))
                return {"acceptee": False, "motif": "ferme_de_clics", "poids_x100": 0}

        try:
            conn.execute(
                "INSERT INTO vues_publications (post_id, createur_id, visiteur_id, empreinte,"
                " categorie, poids_x100, duree_ms, adresse_ip, valide, motif_rejet, jour, cree_le)"
                " VALUES (?,?,?,?,?,?,?,?,1,'',?,?)",
                (post_id, createur_id, visiteur_id, empreinte, categorie, poids,
                 int(duree_ms), adresse_ip, jour, horodatage))
        except sqlite3.IntegrityError:
            return {"acceptee": False, "motif": "doublon_24h", "poids_x100": 0}
    return {"acceptee": True, "motif": "ok", "poids_x100": poids}


def agreger_jour(jour: Optional[str] = None) -> int:
    """Recalcule l'agrégat quotidien par créateur. Renvoie le nombre de lignes."""
    init_monetisation()
    jour = jour or _jour()
    with _curseur() as conn:
        lignes = conn.execute(
            "SELECT createur_id,"
            " SUM(CASE WHEN valide=1 THEN 1 ELSE 0 END) AS brutes,"
            " SUM(CASE WHEN valide=1 THEN poids_x100 ELSE 0 END) AS pond,"
            " SUM(CASE WHEN valide=0 THEN 1 ELSE 0 END) AS ecartees"
            " FROM vues_publications WHERE jour=? GROUP BY createur_id", (jour,)).fetchall()
        for ligne in lignes:
            conn.execute(
                "INSERT INTO vues_journalieres (createur_id, jour, vues_brutes, vues_pond_x100,"
                " ecartees, maj_le) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(createur_id, jour) DO UPDATE SET"
                " vues_brutes=excluded.vues_brutes, vues_pond_x100=excluded.vues_pond_x100,"
                " ecartees=excluded.ecartees, maj_le=excluded.maj_le",
                (ligne["createur_id"], jour, ligne["brutes"], ligne["pond"],
                 ligne["ecartees"], _v8_maintenant()))
    return len(lignes)


# =============================================================================
# 8) RECETTES RÉELLES ET CLÔTURE MENSUELLE
# =============================================================================
def enregistrer_revenu(mois: str, source: str, montant_centimes: int,
                       libelle: str = "") -> int:
    init_monetisation()
    with _curseur() as conn:
        cur = conn.execute(
            "INSERT INTO revenus_publicitaires (mois, source, libelle, montant, encaisse_le)"
            " VALUES (?,?,?,?,?)",
            (mois, source, libelle, int(montant_centimes), _v8_maintenant()))
        return int(cur.lastrowid)


def recettes_mois_centimes(mois: str) -> int:
    init_monetisation()
    with _curseur() as conn:
        return int(conn.execute(
            "SELECT COALESCE(SUM(montant),0) s FROM revenus_publicitaires WHERE mois=?",
            (mois,)).fetchone()["s"])


def vues_mois_par_createur(mois: str) -> Dict[int, int]:
    """vues_pond_x100 cumulées du mois, par créateur."""
    init_monetisation()
    with _curseur() as conn:
        lignes = conn.execute(
            "SELECT createur_id, COALESCE(SUM(vues_pond_x100),0) p FROM vues_journalieres"
            " WHERE jour LIKE ? GROUP BY createur_id", (mois + "%",)).fetchall()
        return {int(l["createur_id"]): int(l["p"]) for l in lignes}


def cloturer_mois(mois: Optional[str] = None, mode: Optional[str] = None,
                  sceller: Optional[Callable] = None) -> Dict[str, Any]:
    """Clôture : pool du mois, puis gains individuels au coffre de rétention.

    Les gains sont calculés au PRORATA des vues pondérées : le total versé aux
    créateurs ne peut JAMAIS dépasser les 25 % du pool retenu.
    """
    init_monetisation()
    mois = mois or mois_precedent()
    mode = (mode or os.environ.get("MODE_POOL", MODE_POOL_DEFAUT)).lower()
    par_createur = vues_mois_par_createur(mois)
    total_pond_x100 = sum(par_createur.values())
    recettes = recettes_mois_centimes(mois)
    fig = figures_pour_vues(Decimal(total_pond_x100) / 100, recettes, mode)

    with _curseur() as conn:
        for createur_id, pond in par_createur.items():
            part = 0
            if total_pond_x100:
                part = int((Decimal(fig["part_createurs"]) * Decimal(pond)
                            / Decimal(total_pond_x100)).quantize(Decimal("1"),
                                                                 rounding=ROUND_HALF_UP))
            palier = palier_pour_vues(Decimal(pond) / 100)
            conn.execute(
                "INSERT INTO gains_createurs (createur_id, mois, vues_pond_x100, part_centimes,"
                " palier, statut, maj_le) VALUES (?,?,?,?,?,'retenu',?)"
                " ON CONFLICT(createur_id, mois) DO UPDATE SET"
                " vues_pond_x100=excluded.vues_pond_x100, part_centimes=excluded.part_centimes,"
                " palier=excluded.palier, maj_le=excluded.maj_le",
                (createur_id, mois, pond, part, palier["niveau"], _v8_maintenant()))
        conn.execute(
            "INSERT INTO pool_monetisation (mois, vues_pond_x100, bareme_centimes,"
            " recettes_centimes, pool_retenu_centimes, part_app_centimes,"
            " part_createurs_centimes, mode, cloture_le) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(mois) DO UPDATE SET vues_pond_x100=excluded.vues_pond_x100,"
            " bareme_centimes=excluded.bareme_centimes, recettes_centimes=excluded.recettes_centimes,"
            " pool_retenu_centimes=excluded.pool_retenu_centimes,"
            " part_app_centimes=excluded.part_app_centimes,"
            " part_createurs_centimes=excluded.part_createurs_centimes, mode=excluded.mode,"
            " cloture_le=excluded.cloture_le",
            (mois, total_pond_x100, fig["bareme_centimes"], recettes, fig["pool"],
             fig["part_application"], fig["part_createurs"], mode, _v8_maintenant()))

    if sceller is not None:
        sceller("POOL_MOIS", mois, centimes_vers_fcfa(fig["pool"]),
                None, f"pool={fig['pool']} app={fig['part_application']} "
                      f"createurs={fig['part_createurs']} mode={mode}")
    return {"mois": mois, "mode": mode, "vues_pond_x100": total_pond_x100,
            "createurs": len(par_createur), **fig}


def gains_du_createur(createur_id: int, mois: Optional[str] = None) -> Dict[str, Any]:
    init_monetisation()
    mois = mois or mois_precedent()
    with _curseur() as conn:
        ligne = conn.execute(
            "SELECT * FROM gains_createurs WHERE createur_id=? AND mois=?",
            (createur_id, mois)).fetchone()
    if ligne is None:
        return {"trouve": False, "mois": mois, "createur_id": createur_id,
                "vues_pond_x100": 0, "part_centimes": 0, "palier": 1, "statut": "aucun"}
    palier = next(p for p in PALIERS_REVERSEMENT if p["niveau"] == ligne["palier"])
    return {"trouve": True, "mois": mois, "createur_id": createur_id,
            "vues_pond_x100": int(ligne["vues_pond_x100"]),
            "vues_ponderees": Decimal(int(ligne["vues_pond_x100"])) / 100,
            "part_centimes": int(ligne["part_centimes"]),
            "part_fcfa": centimes_vers_fcfa(int(ligne["part_centimes"])),
            "palier": int(ligne["palier"]), "palier_nom": palier["nom"],
            "delai_jours": palier["delai_jours"], "kyc": palier["kyc"],
            "statut": ligne["statut"]}


# =============================================================================
# 9) REVERSEMENT MOBILE MONEY — SEUIL, KYC, IDEMPOTENCE, STATUT
# =============================================================================
def _plafond_mois_utilise(mois: str) -> int:
    """Somme déjà engagée ce mois (les versements échoués ne consomment rien)."""
    with _curseur() as conn:
        return int(conn.execute(
            "SELECT COALESCE(SUM(montant_centimes),0) s FROM reversements"
            " WHERE mois=? AND statut!='failed'", (mois,)).fetchone()["s"])


def verifier_numero(user_id: int, numero: str, operateur: str,
                    code: Optional[str] = None) -> Dict[str, Any]:
    """KYC simplifié : un numéro non vérifié ne peut jamais être payé."""
    init_monetisation()
    numero = "".join(c for c in (numero or "") if c.isdigit() or c == "+")
    if len("".join(c for c in numero if c.isdigit())) < 8:
        return {"ok": False, "motif": "numero_invalide"}
    with _curseur() as conn:
        deja = conn.execute("SELECT * FROM kyc_numeros WHERE numero=?", (numero,)).fetchone()
        if deja is not None and deja["statut"] == "verifie" and int(deja["user_id"]) != int(user_id):
            return {"ok": False, "motif": "numero_deja_utilise"}
        nb = conn.execute("SELECT COUNT(*) n FROM kyc_numeros WHERE numero=?",
                          (numero,)).fetchone()["n"]
        if nb >= MAX_COMPTES_PAR_NUMERO:
            return {"ok": False, "motif": "trop_de_comptes_sur_ce_numero"}
        if code is None:
            envoi = "".join(str(secrets.randbelow(10)) for _ in range(6))
            conn.execute(
                "INSERT INTO kyc_numeros (user_id, numero, operateur, statut, code_envoye, tentatives)"
                " VALUES (?,?,?,'a_verifier',?,0)"
                " ON CONFLICT(user_id) DO UPDATE SET numero=excluded.numero,"
                " operateur=excluded.operateur, statut='a_verifier', code_envoye=excluded.code_envoye",
                (user_id, numero, operateur, envoi))
            return {"ok": True, "motif": "code_envoye", "code": envoi}
        ligne = conn.execute("SELECT * FROM kyc_numeros WHERE user_id=?", (user_id,)).fetchone()
        if ligne is None or ligne["code_envoye"] != code:
            conn.execute("UPDATE kyc_numeros SET tentatives=tentatives+1 WHERE user_id=?", (user_id,))
            return {"ok": False, "motif": "code_incorrect"}
        conn.execute("UPDATE kyc_numeros SET statut='verifie', verifie_le=? WHERE user_id=?",
                     (_v8_maintenant(), user_id))
    return {"ok": True, "motif": "verifie"}


def demander_reversement(createur_id: int, mois: Optional[str] = None,
                         numero: str = "", operateur: str = "MTN",
                         pays: str = "Bénin", agregateur: str = "cinetpay",
                         sceller: Optional[Callable] = None) -> Dict[str, Any]:
    """Ouvre une demande de reversement pour les gains retenus d'un mois.

    Refus si : aucun gain · gain sous le seuil · numéro non vérifié · demande
    déjà existante (idempotence par mois).
    """
    init_monetisation()
    mois = mois or mois_precedent()
    gain = gains_du_createur(createur_id, mois)
    if not gain["trouve"] or gain["part_centimes"] <= 0:
        return {"ok": False, "motif": "aucun_gain", "mois": mois}
    if gain["part_centimes"] < SEUIL_REVERSEMENT_CENTIMES:
        return {"ok": False, "motif": "sous_le_seuil", "mois": mois,
                "part_centimes": gain["part_centimes"],
                "report_centimes": gain["part_centimes"],
                "seuil_centimes": SEUIL_REVERSEMENT_CENTIMES}

    with _curseur() as conn:
        kyc = conn.execute("SELECT * FROM kyc_numeros WHERE user_id=?", (createur_id,)).fetchone()
        if kyc is None or kyc["statut"] != "verifie":
            return {"ok": False, "motif": "numero_non_verifie", "mois": mois}
        if numero and "".join(c for c in numero if c.isdigit()) != \
                "".join(c for c in kyc["numero"] if c.isdigit()):
            return {"ok": False, "motif": "numero_different_du_kyc", "mois": mois}
        numero_final = numero or kyc["numero"]
        operateur = operateur or kyc["operateur"]
        existante = conn.execute(
            "SELECT * FROM reversements WHERE createur_id=? AND mois=?"
            " AND statut IN ('en_attente','approuve','sent')", (createur_id, mois)).fetchone()
        if existante is not None:
            return {"ok": True, "motif": "deja_demandee", "id": int(existante["id"]),
                    "reference_interne": existante["reference_interne"],
                    "statut": existante["statut"], "mois": mois}

    montant = gain["part_centimes"]
    if _plafond_mois_utilise(mois) + montant > PLAFOND_MENSUEL_GLOBAL_CENTIMES:
        return {"ok": False, "motif": "plafond_mensuel_atteint", "mois": mois,
                "plafond_centimes": PLAFOND_MENSUEL_GLOBAL_CENTIMES}
    frais = frais_operateur(montant, pays, operateur)
    palier = palier_pour_gain(montant)
    reference = "RV8-" + secrets_token(10)
    prevu = (_dt.datetime.now(_dt.timezone.utc)
             + _dt.timedelta(days=palier["delai_jours"])).strftime("%Y-%m-%d")
    double = montant > PLAFOND_APPROBATION_SIMPLE_CENTIMES

    with _curseur() as conn:
        cur = conn.execute(
            "INSERT INTO reversements (createur_id, mois, montant_centimes, frais_centimes,"
            " net_centimes, numero, operateur, pays, agregateur, reference_interne, statut,"
            " motif, date_prevue, cree_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (createur_id, mois, montant, frais["frais_centimes"], frais["net_verse_centimes"],
             numero_final, operateur, pays, agregateur, reference, "en_attente",
             "double_signature_requise" if double else "", prevu, _v8_maintenant()))
        rev_id = int(cur.lastrowid)
        conn.execute("UPDATE gains_createurs SET statut='confirme', payout_phone=?, payout_op=?,"
                     " maj_le=? WHERE createur_id=? AND mois=?",
                     (numero_final, operateur, _v8_maintenant(), createur_id, mois))
    if sceller is not None:
        sceller("REVERSEMENT_DEMANDE", reference, centimes_vers_fcfa(montant), createur_id,
                f"mois={mois} op={operateur} pays={pays} palier={palier['niveau']}")
    return {"ok": True, "motif": "creee", "id": rev_id, "reference_interne": reference,
            "mois": mois, "montant_centimes": montant, "montant_fcfa": centimes_vers_fcfa(montant),
            "frais_centimes": frais["frais_centimes"], "frais_pct": frais["pct"],
            "net_centimes": frais["net_verse_centimes"], "operateur": operateur, "pays": pays,
            "agregateur": agregateur, "palier": palier["niveau"], "palier_nom": palier["nom"],
            "date_prevue": prevu, "double_signature_requise": double,
            "statut": "en_attente"}


def secrets_token(n: int) -> str:
    return secrets.token_hex(max(1, n // 2)).upper()[:n].ljust(n, "0")


def executer_reversement(rev_id: int, approuve_par: Optional[int] = None,
                         reference_api: str = "", succes: bool = True,
                         sceller: Optional[Callable] = None) -> Dict[str, Any]:
    """Approuve puis exécute un versement (appel API réel en production)."""
    init_monetisation()
    with _curseur() as conn:
        ligne = conn.execute("SELECT * FROM reversements WHERE id=?", (rev_id,)).fetchone()
        if ligne is None:
            return {"ok": False, "motif": "inconnu"}
        if ligne["statut"] in ("sent", "failed"):
            return {"ok": False, "motif": "deja_traite", "statut": ligne["statut"]}
        if ligne["statut"] == "en_attente" and (ligne["motif"] == "double_signature_requise"
                                                and approuve_par is None):
            return {"ok": False, "motif": "double_signature_requise"}
        if ligne["statut"] == "en_attente":
            conn.execute("UPDATE reversements SET statut='approuve', approuve_par=?, approuve_le=?"
                         " WHERE id=?", (approuve_par, _v8_maintenant(), rev_id))
        statut = "sent" if succes else "failed"
        ref = reference_api or ("API-" + secrets_token(12))
        conn.execute("UPDATE reversements SET statut=?, reference_api=? WHERE id=?",
                     (statut, ref if succes else "", rev_id))
        if succes:
            conn.execute("UPDATE gains_createurs SET statut='reverse', maj_le=?"
                         " WHERE createur_id=? AND mois=?",
                         (_v8_maintenant(), ligne["createur_id"], ligne["mois"]))
    if sceller is not None:
        sceller("REVERSEMENT_" + statut.upper(), ligne["reference_interne"],
                centimes_vers_fcfa(int(ligne["net_centimes"])), ligne["createur_id"],
                f"op={ligne['operateur']} ref_api={ref} statut={statut}")
    return {"ok": succes, "statut": statut, "id": rev_id, "reference_api": ref,
            "montant_centimes": int(ligne["montant_centimes"]),
            "net_centimes": int(ligne["net_centimes"]),
            "reference_interne": ligne["reference_interne"]}


def statut_reversement(reference_interne: str) -> Dict[str, Any]:
    init_monetisation()
    with _curseur() as conn:
        ligne = conn.execute("SELECT * FROM reversements WHERE reference_interne=?",
                             (reference_interne,)).fetchone()
    if ligne is None:
        return {"trouve": False, "motif": "inconnu"}
    return {"trouve": True, "statut": ligne["statut"], "mois": ligne["mois"],
            "montant_fcfa": centimes_vers_fcfa(int(ligne["montant_centimes"])),
            "net_fcfa": centimes_vers_fcfa(int(ligne["net_centimes"])),
            "operateur": ligne["operateur"], "numero": ligne["numero"],
            "reference_api": ligne["reference_api"], "date_prevue": ligne["date_prevue"],
            "agregateur": ligne["agregateur"]}


def reversements_en_attente() -> List[Dict[str, Any]]:
    init_monetisation()
    with _curseur() as conn:
        lignes = conn.execute(
            "SELECT id, createur_id, mois, montant_centimes, operateur, numero, statut,"
            " date_prevue FROM reversements WHERE statut IN ('en_attente','approuve')"
            " ORDER BY date_prevue").fetchall()
    return [{"id": int(l["id"]), "createur_id": int(l["createur_id"]), "mois": l["mois"],
             "montant_fcfa": centimes_vers_fcfa(int(l["montant_centimes"])),
             "operateur": l["operateur"], "numero": l["numero"], "statut": l["statut"],
             "date_prevue": l["date_prevue"]} for l in lignes]


def message_fin_de_mois(createur_id: int, mois: Optional[str] = None) -> str:
    """Texte 100 % TEXTE envoyé dans la messagerie utilisateur <-> administrateur."""
    gain = gains_du_createur(createur_id, mois)
    if not gain["trouve"] or gain["part_centimes"] <= 0:
        return "Vos gains du mois sont nuls : aucune vue monétisable enregistrée."
    texte = (f"Gains {gain['mois']} : {formater(gain['part_centimes'])} retenus au coffre "
             f"(palier {gain['palier']} — {gain['palier_nom']}). "
             f"Confirmez votre numéro Mobile Money et l'opérateur pour le versement.")
    if gain["part_centimes"] < SEUIL_REVERSEMENT_CENTIMES:
        texte += (f" Montant inférieur au seuil de {formater(SEUIL_REVERSEMENT_CENTIMES)} : "
                  f"report automatique sur le mois suivant.")
    return texte


# =============================================================================
# 10) BRANCHEMENT SUR TOUTBOT_MUNDO_V7 (V8)
# =============================================================================
def brancher_sur_toutbot(M: Any) -> Dict[str, Any]:
    """Greffe le moteur sur le module TOUTBOT_MUNDO_V7 importé.

    - partage la connexion SQLite de l'application (M.db) ;
    - réutilise M.sceller_ecriture (chaîne d'audit) ;
    - ajoute les routes Flask ; aucune route existante n'est modifiée.
    """
    definir_fabrique_connexion(M.db)
    init_monetisation()
    sceller = getattr(M, "sceller_ecriture", None)

    @M.app.route("/vue/<int:post_id>", methods=["POST"])
    def _v8_vue(post_id: int):
        from flask import jsonify, request, session
        uid = session.get("uid")
        with M.app.app_context():
            post = M.db().execute("SELECT auteur_id FROM posts WHERE id=?", (post_id,)).fetchone()
            if post is None:
                return jsonify({"acceptee": False, "motif": "publication_inconnue"}), 404
            empreinte = request.headers.get("X-Empreinte") or \
                f"{request.remote_addr}|{uid or 'anon'}"
            curb = M.db()
            categorie = "abonne"
            if uid:
                abo = curb.execute(
                    "SELECT 1 FROM abonnements WHERE createur_id=? AND abonne_id=?"
                    " AND statut='valide' LIMIT 1", (post["auteur_id"], uid)).fetchone()
                categorie = "abonne" if abo else "audience"
            resultat = enregistrer_vue(
                post_id=post_id, createur_id=int(post["auteur_id"]), categorie=categorie,
                empreinte=empreinte, visiteur_id=uid,
                duree_ms=int(request.form.get("duree_ms", 5000) or 5000),
                adresse_ip=request.remote_addr or "")
        return jsonify({k: (str(v) if isinstance(v, Decimal) else v)
                        for k, v in resultat.items()})

    @M.app.route("/mon-mesure", methods=["GET"])
    def _v8_mesure():
        from flask import redirect, render_template_string, session, url_for
        if not session.get("uid"):
            try:
                cible = url_for("connexion")
            except Exception:
                cible = "/"
            return redirect(cible)
        with M.app.app_context():
            gain = gains_du_createur(int(session["uid"]))
            lignes = tableau_paliers_reversement()
            html = ["<h2>Mesure et reversement</h2>",
                    f"<p>{message_fin_de_mois(int(session['uid']))}</p>",
                    "<table><tr><th>Niv.</th><th>Nom</th><th>Vues/mois mini</th>"
                    "<th>Pool brut</th><th>App 75%</th><th>Créateur 25%</th>"
                    "<th>Frais</th><th>Net versé</th><th>Délai</th></tr>"]
            for lg in lignes:
                html.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                            "<td><b>%s</b></td><td>%s</td><td><b>%s</b></td><td>J+%s</td></tr>" % (
                                lg["niveau"], lg["nom"], lg["vues_min"],
                                formater(lg["pool_centimes"]),
                                formater(lg["part_application_centimes"]),
                                formater(lg["part_createurs_centimes"]),
                                f'{lg["frais_pct"]}%', formater(lg["net_verse_centimes"]),
                                lg["delai_jours"]))
            html.append("</table>")
            return render_template_string("".join(html))

    if sceller is not None:
        sceller("MOTEUR_V8_INSTALLE", f"v{__version__}", 0, None,
                "monetisation 50 000 FCFA/1000 vues, 75/25, 6 paliers de reversement")
    return {"version": __version__, "paliers": len(PALIERS_REVERSEMENT),
            "routes": ["/vue/<int:post_id>", "/mon-mesure"],
            "taux_par_mille": formater(TAUX_POOL_PAR_MILLE_CENTIMES),
            "seuil_reversement": formater(SEUIL_REVERSEMENT_CENTIMES)}


# =============================================================================
# 11) AFFICHAGE DE LA TABLE DES 6 PALIERS (console)
# =============================================================================
def _texte_tableau(pays: str = None, operateur: str = None) -> str:
    pays = pays or OPERATEUR_REFERENCE[0]
    operateur = operateur or OPERATEUR_REFERENCE[1]
    lignes = tableau_paliers_reversement(pays, operateur)
    entetes = ["Niv", "Nom", "Vues/mois", "Pool brut", "App 75 %", "Créateur 25 %",
               "Frais", "Net versé", "Délai", "Priorité"]
    corps = []
    for lg in lignes:
        borne = f"{lg['vues_min']}–{lg['vues_max']}" if lg["vues_max"] else f"{lg['vues_min']} et +"
        corps.append([str(lg["niveau"]), lg["nom"], borne, formater(lg["pool_centimes"]),
                      formater(lg["part_application_centimes"]),
                      formater(lg["part_createurs_centimes"]), f"{lg['frais_pct']} %",
                      formater(lg["net_verse_centimes"]), f"J+{lg['delai_jours']}", lg["priorite"]])
    largeurs = [max(len(entetes[i]), *(len(r[i]) for r in corps)) for i in range(len(entetes))]
    sep = "+" + "+".join("-" * (l + 2) for l in largeurs) + "+"
    out = [f"TABLE DES 6 PALIERS DE REVERSEMENT — barème 50 000 FCFA / 1 000 vues "
           f"(frais opérateur illustrés : {pays} {operateur})", sep,
           "| " + " | ".join(e.ljust(largeurs[i]) for i, e in enumerate(entetes)) + " |", sep]
    for r in corps:
        out.append("| " + " | ".join(r[i].ljust(largeurs[i]) for i in range(len(entetes))) + " |")
    out.append(sep)
    return "\n".join(out)


def main_moteur_v8() -> int:
    print(_texte_tableau())
    print()
    print("Exemples d'application du barème (mode garanti, 25 % au créateur) :")
    for vues in (1000, 2000, 3000, 5000, 10000, 100000):
        fig = figures_pour_vues(Decimal(vues), mode="garanti")
        print(f"  {vues:>7} vues -> pool {formater(fig['pool']):>18} | "
              f"app {formater(fig['part_application']):>18} | "
              f"créateur {formater(fig['part_createurs']):>16}")
    return 0


# =============================================================================
# PARTIE 2 — APPLICATION TOUTBOT MUNDO V7 (ex TOUTBOT_MUNDO_V7.py)
# =============================================================================

# -*- coding: utf-8 -*-
# =============================================================================
# 🌍 TOUTBOT MUNDO — RÉSEAU SOCIAL 100 % TEXTUEL + PORTEFEUILLE + ADMIN
# -----------------------------------------------------------------------------
# Mono-fichier. Une seule dépendance : Flask (SQLite et la recherche temps réel
# utilisent la bibliothèque standard de Python).
#
#   • Publications 100 % TEXTE (aucune image, aucune vidéo, aucun fichier joint)
#   • Identification par NUMÉRO DE TÉLÉPHONE ou PSEUDO (mot de passe haché)
#   • Abonnements payants : 6 paliers — 100 / 250 / 500 / 1000 / 2500 / 5000 FCFA
#   • Pourboires libres
#   • Portefeuille virtuel en TICKETS + FCFA (grand livre d'écritures)
#   • Messagerie utilisateur ↔ administrateur (cliquer un nom ouvre la discussion)
#   • Canal de plaintes / assistance
#   • Numéros Mobile Money de l'administrateur affichés en CARRÉ COPIER-COLLER
#     (bouton « Copier ») sur le fil d'accueil, chaque profil public (y compris
#     celui de l'administrateur), le portefeuille, les discussions et l'admin
#   • Tableau de bord admin : TICKETS + SOMME FCFA + NUMÉRO DE TÉLÉPHONE + [Marquer payé]
#   • Export comptable mensuel CSV (transactions validées + récap par créateur
#     + totaux brut / commissions / net) pour les déclarations de fin de mois
#   • Recherche temps réel sur moteurs GRATUITS (SearXNG, Google News RSS,
#     DuckDuckGo, Wikipédia) — résultats RÉELS avec liens, ZÉRO invention
#   • Chat IA ancré sur le bloc temps réel (Pollinations), qui répond « je ne
#     trouve rien de publié » plutôt que d'inventer
#
# DÉMARRAGE
#   pip install flask
#   pip install flask flask-sock   # flask-sock est OPTIONNEL (WebSocket admin)
#   python TOUTBOT_MUNDO_V6.py --seed-admin zeusad --telephone 90000000
#   SECRET_KEY=... python TOUTBOT_MUNDO_V6.py --port 8099   # lance le serveur
#
# COMMISSION — RÈGLE PAR DÉFAUT : max(25 % du montant ; 15 FCFA)  [MODE_COMMISSION=max]
#   Net du mois = Total brut − commission, commission = max(25 % du montant ; 15 FCFA)
#   Exemple du brief : 10 abonnements à 500 + 2 tips de 100 → brut 5 200,
#   commissions 1 300 + 180 = 1 480, NET À VERSER 3 720 FCFA.
#   Le mode cumulatif (25 % + 15 FCFA) reste disponible : MODE_COMMISSION=cumul.
# =============================================================================
import argparse
import csv
import datetime as _dt
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, g, jsonify, redirect, render_template_string, request, session, url_for
from jinja2 import ChoiceLoader, DictLoader
from werkzeug.security import check_password_hash, generate_password_hash

LOGGER = logging.getLogger("toutbot.mundo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

# =============================================================================
# 🔐 CONFIGURATION — tout se règle par variables d'environnement
# =============================================================================
def _env(name: str, default: str) -> str:
    valeur = os.environ.get(name)
    return valeur if valeur not in (None, "") else default

def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default

def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default

CONFIG: Dict[str, Any] = {
    "DB": _env("TOUTBOT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "toutbot_mundo.db")),
    "SECRET_KEY": _env("SECRET_KEY", "dev-" + secrets.token_hex(12)),
    # Commission de l'application : max(25 % , 15 FCFA) par défaut
    "COMMISSION_PCT": _env_float("COMMISSION_PCT", 25.0),
    "COMMISSION_PLANCHER": _env_float("COMMISSION_PLANCHER", 15.0),
    # RÈGLE PAR DÉFAUT (demande utilisateur) : max(25 % du montant ; 15 FCFA)
    "MODE_COMMISSION": _env("MODE_COMMISSION", "max").lower(),  # max | cumul
    # 1 ticket = 1 FCFA par défaut : modifiable sans toucher au code
    "TICKET_FCFA": _env_float("TICKET_FCFA", 1.0),
    "DEVISE": "FCFA",
    # Numéros de l'administrateur (retrait / dépôt Mobile Money)
    "ADMIN_MTN": _env("ADMIN_MTN", "2250585167882"),
    "ADMIN_MOOV": _env("ADMIN_MOOV", "2250160428847"),
    # Moteur d'IA (extensions temps réel)
    "AI_ENDPOINT": _env("AI_ENDPOINT", "https://text.pollinations.ai/openai"),
    "AI_MODEL": _env("AI_MODEL", "openai"),
    "AI_TIMEOUT": _env_int("AI_TIMEOUT", 45),
    "AI_MAX_WEB_TOTAL": _env_int("AI_MAX_WEB_TOTAL", 6000),
    "POLLINATIONS_KEY": _env("POLLINATIONS_KEY", ""),
    # Envoi groupé : nombre maximal de messages groupés par heure glissante
    "MAX_ENVOIS_HEURE": _env_int("TOUTBOT_MAX_ENVOIS_HEURE", 200),
    # Instance SearXNG privée (facultative : les instances publiques sont essayées sinon)
    "SEARXNG_URL": _env("SEARXNG_URL", ""),
}

# Les 6 paliers d'abonnement mensuel du créateur
PALIERS: List[int] = [100, 250, 500, 1000, 2500, 5000]

app = Flask(__name__)
app.secret_key = CONFIG["SECRET_KEY"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

# =============================================================================
# 🗄️ BASE DE DONNÉES
# =============================================================================
def maintenant() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(CONFIG["DB"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db

@app.teardown_appcontext
def _fermer_db(_exc: Optional[BaseException] = None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    telephone     TEXT UNIQUE,
    pseudo        TEXT UNIQUE NOT NULL,
    mot_de_passe  TEXT NOT NULL,
    est_admin     INTEGER NOT NULL DEFAULT 0,
    bloque        INTEGER NOT NULL DEFAULT 0,
    payout_phone  TEXT NOT NULL DEFAULT '',
    payout_op     TEXT NOT NULL DEFAULT 'MTN',
    bio           TEXT NOT NULL DEFAULT '',
    cree_le       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    auteur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps     TEXT NOT NULL,
    cree_le   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS follows (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    suiveur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    suivi_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cree_le    TEXT NOT NULL,
    UNIQUE(suiveur_id, suivi_id)
);
CREATE TABLE IF NOT EXISTS likes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id  INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cree_le  TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);
CREATE TABLE IF NOT EXISTS comments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    auteur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps     TEXT NOT NULL,
    cree_le   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS abonnements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    createur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    abonne_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    palier      REAL NOT NULL,
    commission  REAL NOT NULL,
    net         REAL NOT NULL,
    reference   TEXT NOT NULL,
    statut      TEXT NOT NULL DEFAULT 'en_attente',
    cree_le     TEXT NOT NULL,
    valide_le   TEXT
);
CREATE TABLE IF NOT EXISTS pourboires (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    createur_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expediteur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    montant      REAL NOT NULL,
    commission   REAL NOT NULL,
    net          REAL NOT NULL,
    mot          TEXT NOT NULL DEFAULT '',
    statut       TEXT NOT NULL DEFAULT 'en_attente',
    cree_le      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portefeuille (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sens     TEXT NOT NULL,             -- credit | debit
    tickets  REAL NOT NULL,
    fcfa     REAL NOT NULL,
    libelle  TEXT NOT NULL,
    ref      TEXT NOT NULL DEFAULT '',
    statut   TEXT NOT NULL DEFAULT 'en_attente',   -- en_attente | paye | annule
    cree_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paiements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tickets    REAL NOT NULL,
    fcfa       REAL NOT NULL,
    telephone  TEXT NOT NULL DEFAULT '',
    operateur  TEXT NOT NULL DEFAULT '',
    statut     TEXT NOT NULL DEFAULT 'paye',
    decideur   INTEGER,
    cree_le    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recettes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source  TEXT NOT NULL,
    ref     TEXT NOT NULL DEFAULT '',
    montant REAL NOT NULL,
    cree_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    expediteur_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    destinataire_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps          TEXT NOT NULL,
    lu             INTEGER NOT NULL DEFAULT 0,
    cree_le        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plaintes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sujet     TEXT NOT NULL,
    corps     TEXT NOT NULL,
    statut    TEXT NOT NULL DEFAULT 'ouverte',
    reponse   TEXT NOT NULL DEFAULT '',
    cree_le   TEXT NOT NULL,
    traite_le TEXT
);
CREATE TABLE IF NOT EXISTS parametres (
    user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    notifs_messages     INTEGER NOT NULL DEFAULT 1,
    notifs_portefeuille INTEGER NOT NULL DEFAULT 1,
    langue              TEXT NOT NULL DEFAULT 'fr',
    theme               TEXT NOT NULL DEFAULT 'sombre',
    maj_le              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_auteur ON posts(auteur_id);
CREATE INDEX IF NOT EXISTS idx_pf_user ON portefeuille(user_id, statut);
CREATE INDEX IF NOT EXISTS idx_msg_couple ON messages(expediteur_id, destinataire_id);
CREATE INDEX IF NOT EXISTS idx_likes_post ON likes(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE TABLE IF NOT EXISTS envois_groupe (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cibles       TEXT NOT NULL DEFAULT '',
    filtre       TEXT NOT NULL DEFAULT 'manuel',
    objet        TEXT NOT NULL DEFAULT 'personnalise',
    corps        TEXT NOT NULL,
    auto_reponse INTEGER NOT NULL DEFAULT 0,
    regles       TEXT NOT NULL DEFAULT '',
    cree_le      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reponses_auto (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    envoi_id  INTEGER REFERENCES envois_groupe(id) ON DELETE SET NULL,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question  TEXT NOT NULL DEFAULT '',
    reponse   TEXT NOT NULL,
    origine   TEXT NOT NULL DEFAULT 'regle',
    cree_le   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_envois_admin ON envois_groupe(admin_id);
CREATE TABLE IF NOT EXISTS journal (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    cible      TEXT NOT NULL DEFAULT '',
    details    TEXT NOT NULL DEFAULT '',
    adresse_ip TEXT NOT NULL DEFAULT '',
    cree_le    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_date ON journal(cree_le);
CREATE INDEX IF NOT EXISTS idx_journal_action ON journal(action);
CREATE TABLE IF NOT EXISTS historique_utilisateur (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action     TEXT NOT NULL,
    objet      TEXT NOT NULL DEFAULT '',
    details    TEXT NOT NULL DEFAULT '',
    montant    REAL,
    adresse_ip TEXT NOT NULL DEFAULT '',
    cree_le    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_histo_user ON historique_utilisateur(user_id, id);
CREATE INDEX IF NOT EXISTS idx_histo_action ON historique_utilisateur(user_id, action);
"""

def init_schema() -> None:
    conn = sqlite3.connect(CONFIG["DB"])
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migration douce : la colonne de présence en ligne est ajoutée aux bases existantes.
    try:
        conn.execute("ALTER TABLE users ADD COLUMN derniere_activite TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # colonne déjà présente
    # Migration douce V1.5 : réponse IA des plaintes + droit de retrait des envois groupés.
    for _colonne, _table, _defaut in (("reponse_ia", "plaintes", "TEXT NOT NULL DEFAULT ''"),
                                      ("refus_groupes", "users", "INTEGER NOT NULL DEFAULT 0"),
                                      ("admin_mtn_numero", "users", "TEXT NOT NULL DEFAULT ''"),
                                      ("admin_moov_numero", "users", "TEXT NOT NULL DEFAULT ''")):
        try:
            conn.execute(f"ALTER TABLE {_table} ADD COLUMN {_colonne} {_defaut}")
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    conn.commit()
    conn.close()

# =============================================================================
# ❤️ INTERACTIONS SOCIALES — likes, commentaires, présence en ligne
# =============================================================================
DELAI_EN_LIGNE_S = 300  # 5 minutes d'inactivité max pour être « en ligne »

def est_en_ligne(derniere: str) -> bool:
    """True si l'utilisateur a été actif il y a moins de DELAI_EN_LIGNE_S secondes."""
    if not derniere:
        return False
    try:
        d = _dt.datetime.strptime(derniere, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return False
    return (_dt.datetime.now(_dt.timezone.utc) - d).total_seconds() <= DELAI_EN_LIGNE_S

def maj_activite(user_id: int) -> None:
    """Enregistre l'horodatage de la dernière activité de l'utilisateur."""
    try:
        conn = db()
        conn.execute("UPDATE users SET derniere_activite = ? WHERE id = ?",
                     (maintenant(), user_id))
        conn.commit()
    except sqlite3.Error:
        pass

def basculer_like(post_id: int, user_id: int) -> bool:
    """Ajoute ou retire le « J'aime » d'un utilisateur sur une publication. Retourne l'état final."""
    conn = db()
    existe = conn.execute("SELECT id FROM likes WHERE post_id = ? AND user_id = ?",
                          (post_id, user_id)).fetchone()
    if existe:
        conn.execute("DELETE FROM likes WHERE id = ?", (existe["id"],))
        etat = False
    else:
        conn.execute("INSERT OR IGNORE INTO likes (post_id, user_id, cree_le) VALUES (?,?,?)",
                     (post_id, user_id, maintenant()))
        etat = True
    conn.commit()
    return etat

def ajouter_commentaire(post_id: int, auteur_id: int, corps: str) -> int:
    """Ajoute un commentaire (abonnés et non-abonnés, y compris sur les posts de l'admin)."""
    conn = db()
    cur = conn.execute("INSERT INTO comments (post_id, auteur_id, corps, cree_le) VALUES (?,?,?,?)",
                       (post_id, auteur_id, corps[:500], maintenant()))
    conn.commit()
    return int(cur.lastrowid)

# =============================================================================
# 📊 TABLEAU DE BORD DES STATISTIQUES
# =============================================================================
def activite_jours(table: str, user_id: Optional[int] = None, jours: int = 7) -> List[Dict[str, Any]]:
    """Volume quotidien des derniers `jours` pour une table donnee (posts, likes, comments, users).
    Retourne une liste de {jour, libelle, n} triee du plus ancien au plus recent."""
    conn = db()
    filtre, params = "", []
    if user_id is not None:
        filtre = " AND %s = ?" % ("auteur_id" if table != "likes" else "user_id")
        params = [user_id]
    auj = _dt.date.today()
    serie = []
    for i in range(jours - 1, -1, -1):
        j = auj - _dt.timedelta(days=i)
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM %s WHERE substr(cree_le,1,10) = ?%s" % (table, filtre),
            [j.isoformat()] + params).fetchone()["n"]
        serie.append({"jour": j.isoformat(), "libelle": j.strftime("%d/%m"), "n": n})
    return serie

def svg_barres_30_jours(serie: List[Dict[str, Any]]) -> str:
    """Graphique a barres SVG autonome (30 jours) — fonctionne hors ligne, sans CDN."""
    if not serie:
        return '<p class="muet">Aucune donnée.</p>'
    max_n = max([j["n"] for j in serie] + [1])
    pas = 580.0 / len(serie)
    parties = ['<svg viewBox="0 0 620 132" width="100%" style="max-width:620px" '
               'role="img" aria-label="activité par jour">',
               '<line x1="30" y1="112" x2="612" y2="112" stroke="#2b3549" stroke-width="1"/>']
    for i, j in enumerate(serie):
        h = (j["n"] / max_n) * 88.0
        h = max(h, 3.0) if j["n"] else 2.0
        x = 30 + i * pas
        couleur = "#c8a24a" if j["n"] else "#39435a"
        parties.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s">'
                       '<title>%s : %d</title></rect>'
                       % (x, 112 - h, pas * 0.72, h, couleur, j["libelle"], j["n"]))
        if i % 5 == 0 or i == len(serie) - 1:
            parties.append('<text x="%.1f" y="126" font-size="9" fill="#8e9bb3" '
                           'text-anchor="middle">%s</text>' % (x + pas * 0.36, j["libelle"]))
    parties.append("</svg>")
    return "".join(parties)

def _csv_reponse(texte: str, nom_fichier: str):
    """Réponse CSV téléchargeable : UTF-8 avec BOM et point-virgule (Excel français)."""
    reponse = app.response_class(texte.encode("utf-8-sig"), mimetype="text/csv")
    reponse.headers["Content-Disposition"] = 'attachment; filename="%s"' % nom_fichier
    return reponse

def export_csv_stats_utilisateur(user_id: int) -> str:
    """Export CSV des statistiques personnelles (séparateur point-virgule)."""
    s = statistiques_utilisateur(user_id)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Statistiques personnelles — ToutBot Mundo"])
    w.writerow(["Surnom", s["pseudo"]])
    w.writerow(["Membre depuis", s["cree_le"]])
    w.writerow([])
    w.writerow(["Indicateur", "Valeur"])
    for cle, lib in (("posts", "Publications"), ("likes_recus", "J'aime recus"),
                     ("likes_donnes", "J'aime donnes"),
                     ("commentaires_postes", "Commentaires postes"),
                     ("commentaires_recus", "Commentaires recus"),
                     ("abonnes", "Abonnes"), ("abonnements", "Abonnements suivis"),
                     ("messages_envoyes", "Messages envoyes"),
                     ("gains_totaux", "Gains nets (FCFA)")):
        w.writerow([lib, s[cle]])
    w.writerow(["Tickets au portefeuille", s["solde"]["tickets_total"]])
    w.writerow([])
    w.writerow(["Activite des 30 derniers jours"])
    w.writerow(["Date", "Publications", "J'aime", "Commentaires"])
    for i in range(30):
        a = s["activite30_posts"][i]; b = s["activite30_likes"][i]; c = s["activite30_comments"][i]
        w.writerow([a["jour"], a["n"], b["n"], c["n"]])
    return buf.getvalue()

def export_csv_stats_globales() -> str:
    """Export CSV des statistiques globales (vue administrateur, séparateur point-virgule)."""
    g = statistiques_globales()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Statistiques globales — ToutBot Mundo"])
    w.writerow(["Date d'export", _dt.datetime.now().strftime("%Y-%m-%d %H:%M")])
    w.writerow([])
    w.writerow(["Indicateur", "Valeur"])
    for cle, lib in (("users", "Comptes inscrits"), ("en_ligne", "En ligne maintenant"),
                     ("bloques", "Comptes bloques"), ("posts", "Publications"),
                     ("likes", "J'aime"), ("comments", "Commentaires"),
                     ("follows", "Suivis"), ("messages", "Messages"),
                     ("plaintes_ouvertes", "Plaintes ouvertes"),
                     ("recettes", "Commissions recoltees (FCFA)")):
        w.writerow([lib, g[cle]])
    w.writerow(["Abonnements valides", g["abos_valides"]["n"]])
    w.writerow(["Pourboires valides", g["tips_valides"]["n"]])
    w.writerow([])
    w.writerow(["Top 10 createurs (par abonnes)"])
    w.writerow(["Surnom", "Abonnes", "J'aime recus", "Publications", "Gains nets (FCFA)"])
    for u in g["top_createurs"]:
        w.writerow([u["pseudo"], u["abonnes"], u["aime"], u["posts"], u["gains"]])
    w.writerow([])
    w.writerow(["Inscriptions des 30 derniers jours"])
    w.writerow(["Date", "Inscriptions"])
    for j in g["inscriptions30"]:
        w.writerow([j["jour"], j["n"]])
    return buf.getvalue()

def statistiques_utilisateur(user_id: int) -> Dict[str, Any]:
    """Agrège toutes les statistiques personnelles d'un utilisateur."""
    conn = db()
    q = lambda sql, p=(): conn.execute(sql, p).fetchone()
    st: Dict[str, Any] = {}
    st["posts"] = q("SELECT COUNT(*) n FROM posts WHERE auteur_id=?", (user_id,))["n"]
    st["likes_recus"] = q("SELECT COUNT(*) n FROM likes l JOIN posts p ON p.id=l.post_id"
                          " WHERE p.auteur_id=?", (user_id,))["n"]
    st["likes_donnes"] = q("SELECT COUNT(*) n FROM likes WHERE user_id=?", (user_id,))["n"]
    st["commentaires_postes"] = q("SELECT COUNT(*) n FROM comments WHERE auteur_id=?", (user_id,))["n"]
    st["commentaires_recus"] = q("SELECT COUNT(*) n FROM comments c JOIN posts p ON p.id=c.post_id"
                                 " WHERE p.auteur_id=?", (user_id,))["n"]
    st["abonnes"] = q("SELECT COUNT(*) n FROM follows WHERE suivi_id=?", (user_id,))["n"]
    st["abonnements"] = q("SELECT COUNT(*) n FROM follows WHERE suiveur_id=?", (user_id,))["n"]
    st["abos_valides"] = q("SELECT COUNT(*) n, COALESCE(SUM(net),0) net FROM abonnements"
                           " WHERE createur_id=? AND statut='valide'", (user_id,))
    st["pourboires_recus"] = q("SELECT COUNT(*) n, COALESCE(SUM(net),0) net FROM pourboires"
                               " WHERE createur_id=? AND statut='valide'", (user_id,))
    st["pourboires_donnes"] = q("SELECT COUNT(*) n, COALESCE(SUM(montant),0) net FROM pourboires"
                                " WHERE expediteur_id=? AND statut='valide'", (user_id,))
    st["messages_envoyes"] = q("SELECT COUNT(*) n FROM messages WHERE expediteur_id=?", (user_id,))["n"]
    st["solde"] = solde(user_id)
    u = utilisateur_par_id(user_id)
    st["pseudo"] = u["pseudo"]
    st["cree_le"] = u["cree_le"]
    st["en_ligne"] = est_en_ligne(u["derniere_activite"])
    st["derniere_activite"] = u["derniere_activite"] or "—"
    top = q("SELECT p.id, p.corps, COUNT(l.id) n FROM posts p JOIN likes l ON l.post_id=p.id"
            " WHERE p.auteur_id=? GROUP BY p.id ORDER BY n DESC LIMIT 1", (user_id,))
    st["top_post"] = dict(top) if top else None
    st["activite_posts"] = activite_jours("posts", user_id)
    st["activite_likes"] = activite_jours("likes", user_id)
    st["activite_comments"] = activite_jours("comments", user_id)
    st["activite30_posts"] = activite_jours("posts", user_id, jours=30)
    st["activite30_likes"] = activite_jours("likes", user_id, jours=30)
    st["activite30_comments"] = activite_jours("comments", user_id, jours=30)
    st["gains_totaux"] = round(st["abos_valides"]["net"] + st["pourboires_recus"]["net"], 2)
    return st

def statistiques_globales() -> Dict[str, Any]:
    """Agrège les statistiques de l'ensemble des utilisateurs (vue administrateur)."""
    conn = db()
    q = lambda sql, p=(): conn.execute(sql, p).fetchone()
    g: Dict[str, Any] = {}
    g["users"] = q("SELECT COUNT(*) n FROM users")["n"]
    g["bloques"] = q("SELECT COUNT(*) n FROM users WHERE bloque=1")["n"]
    g["en_ligne"] = sum(1 for r in conn.execute(
        "SELECT derniere_activite FROM users").fetchall() if est_en_ligne(r["derniere_activite"]))
    g["posts"] = q("SELECT COUNT(*) n FROM posts")["n"]
    g["likes"] = q("SELECT COUNT(*) n FROM likes")["n"]
    g["comments"] = q("SELECT COUNT(*) n FROM comments")["n"]
    g["follows"] = q("SELECT COUNT(*) n FROM follows")["n"]
    g["messages"] = q("SELECT COUNT(*) n FROM messages")["n"]
    g["plaintes_ouvertes"] = q("SELECT COUNT(*) n FROM plaintes WHERE statut='ouverte'")["n"]
    g["abos_valides"] = q("SELECT COUNT(*) n, COALESCE(SUM(net),0) net, COALESCE(SUM(commission),0) com"
                          " FROM abonnements WHERE statut='valide'")
    g["abos_attente"] = q("SELECT COUNT(*) n FROM abonnements WHERE statut='en_attente'")["n"]
    g["tips_valides"] = q("SELECT COUNT(*) n, COALESCE(SUM(net),0) net, COALESCE(SUM(commission),0) com"
                          " FROM pourboires WHERE statut='valide'")
    g["tips_attente"] = q("SELECT COUNT(*) n FROM pourboires WHERE statut='en_attente'")["n"]
    g["recettes"] = total_recettes()
    g["inscriptions"] = activite_jours("users")
    g["inscriptions30"] = activite_jours("users", jours=30)
    g["activite_posts"] = activite_jours("posts")
    g["top_publications"] = [dict(r) for r in conn.execute(
        "SELECT p.id, p.corps, u.pseudo, COUNT(l.id) n FROM posts p"
        " JOIN users u ON u.id=p.auteur_id JOIN likes l ON l.post_id=p.id"
        " GROUP BY p.id ORDER BY n DESC LIMIT 5").fetchall()]
    g["top_createurs"] = [dict(r) for r in conn.execute(
        "SELECT u.pseudo, u.id,"
        " (SELECT COUNT(*) FROM follows f WHERE f.suivi_id=u.id) abonnes,"
        " (SELECT COUNT(*) FROM likes l JOIN posts p ON p.id=l.post_id WHERE p.auteur_id=u.id) aime,"
        " (SELECT COUNT(*) FROM posts p WHERE p.auteur_id=u.id) posts,"
        " (SELECT COALESCE(SUM(net),0) FROM abonnements a WHERE a.createur_id=u.id AND a.statut='valide') gains"
        " FROM users u ORDER BY abonnes DESC, aime DESC LIMIT 10").fetchall()]
    g["top_commentateurs"] = [dict(r) for r in conn.execute(
        "SELECT u.pseudo, COUNT(c.id) n FROM comments c JOIN users u ON u.id=c.auteur_id"
        " GROUP BY c.auteur_id ORDER BY n DESC LIMIT 5").fetchall()]
    g["top_bienfaiteurs"] = [dict(r) for r in conn.execute(
        "SELECT u.pseudo, COUNT(t.id) n, COALESCE(SUM(t.montant),0) total FROM pourboires t"
        " JOIN users u ON u.id=t.expediteur_id WHERE t.statut='valide'"
        " GROUP BY t.expediteur_id ORDER BY total DESC LIMIT 5").fetchall()]
    return g

# =============================================================================
# 💰 RÈGLE DE COMMISSION
# =============================================================================
def calcul_commission(montant: float) -> Tuple[float, float, str]:
    """Applique la règle de commission de l'application.

    MODE max   (spécification) : commission = max(25 % du montant ; 15 FCFA)
    MODE cumul (brief)         : commission = 25 % du montant + 15 FCFA

    Retourne (commission, net_pour_le_createur, explication).
    """
    montant = round(float(montant), 2)
    pourcentage = round(montant * CONFIG["COMMISSION_PCT"] / 100.0, 2)
    plancher = float(CONFIG["COMMISSION_PLANCHER"])
    if CONFIG["MODE_COMMISSION"] == "cumul":
        commission = round(pourcentage + plancher, 2)
        detail = f"{CONFIG['COMMISSION_PCT']:g}% ({pourcentage:g}) + {plancher:g} {CONFIG['DEVISE']}"
    else:
        commission = round(max(pourcentage, plancher), 2)
        retenu = "pourcentage" if pourcentage >= plancher else "plancher"
        detail = f"max({CONFIG['COMMISSION_PCT']:g}% = {pourcentage:g} ; plancher {plancher:g}) → {retenu}"
    return commission, round(montant - commission, 2), detail

def net_mois(total_brut: float, nb_transactions: int) -> Dict[str, Any]:
    """Récapitulatif de fin de mois, aligné sur la règle de commission active :
    mode max (défaut) : commission = max(25 % du brut ; 15 FCFA × nb transactions)
    mode cumul        : commission = 25 % du brut + 15 FCFA × nb transactions"""
    brut = round(float(total_brut), 2)
    nb = max(int(nb_transactions or 0), 0)
    pct = round(brut * CONFIG["COMMISSION_PCT"] / 100.0, 2)
    plancher = float(CONFIG["COMMISSION_PLANCHER"])
    fixe = round(plancher * nb, 2)
    if CONFIG["MODE_COMMISSION"] == "cumul":
        commissions = round(pct + fixe, 2)
    else:
        commissions = round(max(pct, fixe), 2)   # max(25 % ; 15 FCFA x nb)
    return {"brut": brut, "nb": nb, "pct": pct, "fixe": fixe,
            "commissions": commissions, "net": round(brut - commissions, 2)}

def fcfa_vers_tickets(fcfa: float) -> float:
    return round(float(fcfa) / float(CONFIG["TICKET_FCFA"]), 2)

# =============================================================================
# 👤 UTILISATEURS
# =============================================================================
def nettoyer_telephone(brut: str) -> str:
    return re.sub(r"[^0-9+]", "", (brut or "").strip())

# Règle du surnom unique : exactement 6 LETTRES (accents français acceptés),
# sans chiffres, sans symboles, sans espaces — simple à retenir et à partager.
REGLE_SURNOM = re.compile(r"^[A-Za-zÀ-ÿ]{6}$")
MESSAGE_SURNOM = ("Le surnom doit comporter exactement 6 lettres, sans chiffres ni symboles "
                  "(les accents français sont acceptés). Exemple : « koffia » ou « Zeynab ».")

def valider_surnom(pseudo: str) -> str:
    """Vérifie la règle du surnom unique (6 lettres) et le retourne nettoyé."""
    pseudo = (pseudo or "").strip()
    if not REGLE_SURNOM.match(pseudo):
        raise ValueError(MESSAGE_SURNOM)
    return pseudo

def creer_utilisateur(telephone: str, pseudo: str, mot_de_passe: str, est_admin: bool = False) -> int:
    telephone = nettoyer_telephone(telephone)
    pseudo = (pseudo or "").strip()
    if len(pseudo) < 3:
        raise ValueError("Le pseudo doit contenir au moins 3 caractères.")
    if len(mot_de_passe or "") < 6:
        raise ValueError("Le mot de passe doit contenir au moins 6 caractères.")
    if telephone and len(re.sub(r"\D", "", telephone)) < 8:
        raise ValueError("Numéro de téléphone trop court.")
    conn = db()
    try:
        cur = conn.execute(
            "INSERT INTO users (telephone, pseudo, mot_de_passe, est_admin, payout_phone, cree_le,"
            " admin_mtn_numero, admin_moov_numero)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (telephone or None, pseudo, generate_password_hash(mot_de_passe), 1 if est_admin else 0,
             telephone, maintenant(), CONFIG["ADMIN_MTN"], CONFIG["ADMIN_MOOV"]),
        )
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise ValueError("Ce pseudo ou ce numéro de téléphone est déjà utilisé.") from exc

def utilisateur_par_identifiant(identifiant: str):
    identifiant = (identifiant or "").strip()
    conn = db()
    return conn.execute(
        "SELECT * FROM users WHERE pseudo = ? OR telephone = ?",
        (identifiant, nettoyer_telephone(identifiant)),
    ).fetchone()

def utilisateur_par_id(user_id: int):
    return db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

def admin_par_defaut():
    return db().execute("SELECT * FROM users WHERE est_admin = 1 ORDER BY id LIMIT 1").fetchone()

def nom(u) -> str:
    return u["pseudo"] if u else "—"

# =============================================================================
# 📒 GRAND LIVRE DU PORTEFEUILLE (tickets + FCFA)
# =============================================================================
def ecrire_portefeuille(user_id: int, sens: str, fcfa: float, libelle: str,
                        ref: str = "", statut: str = "en_attente") -> None:
    """Ecrit une ligne du grand livre. 1 ticket = CONFIG['TICKET_FCFA'] FCFA."""
    conn = db()
    conn.execute(
        "INSERT INTO portefeuille (user_id, sens, tickets, fcfa, libelle, ref, statut, cree_le)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (user_id, sens, fcfa_vers_tickets(fcfa), round(float(fcfa), 2), libelle, ref, statut, maintenant()),
    )
    conn.commit()

def solde(user_id: int) -> Dict[str, float]:
    conn = db()
    ligne = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN sens='credit' THEN tickets ELSE -tickets END),0) AS t,"
        "       COALESCE(SUM(CASE WHEN sens='credit' THEN fcfa    ELSE -fcfa    END),0) AS f"
        " FROM portefeuille WHERE user_id = ? AND statut = 'paye'", (user_id,),
    ).fetchone()
    attente = conn.execute(
        "SELECT COALESCE(SUM(tickets),0) AS t, COALESCE(SUM(fcfa),0) AS f"
        " FROM portefeuille WHERE user_id = ? AND sens='credit' AND statut='en_attente'", (user_id,),
    ).fetchone()
    return {
        "tickets_payes": round(ligne["t"], 2),
        "fcfa_payes": round(ligne["f"], 2),
        "tickets_attente": round(attente["t"], 2),
        "fcfa_attente": round(attente["f"], 2),
        "tickets_total": round(ligne["t"] + attente["t"], 2),
        "fcfa_total": round(ligne["f"] + attente["f"], 2),
    }

def ecrire_recette(source: str, montant: float, ref: str = "") -> None:
    conn = db()
    conn.execute("INSERT INTO recettes (source, ref, montant, cree_le) VALUES (?,?,?,?)",
                 (source, ref, round(float(montant), 2), maintenant()))
    conn.commit()

def total_recettes() -> float:
    return round(db().execute("SELECT COALESCE(SUM(montant),0) AS s FROM recettes").fetchone()["s"], 2)

def creer_abonnement(abonne_id: int, createur_id: int, palier: float) -> int:
    palier = round(float(palier), 2)
    if palier <= 0:
        raise ValueError("Palier invalide.")
    if abonne_id == createur_id:
        raise ValueError("Vous ne pouvez pas vous abonner à vous-même.")
    commission, net, _ = calcul_commission(palier)
    reference = "TB-" + secrets.token_hex(4).upper()
    conn = db()
    cur = conn.execute(
        "INSERT INTO abonnements (createur_id, abonne_id, palier, commission, net, reference, cree_le)"
        " VALUES (?,?,?,?,?,?,?)",
        (createur_id, abonne_id, palier, commission, net, reference, maintenant()),
    )
    conn.commit()
    return int(cur.lastrowid)

def valider_abonnement(abo_id: int, decideur_id: int) -> Optional[Dict[str, float]]:
    """L'administrateur confirme l'encaissement Mobile Money : le net est
    crédité au portefeuille du créateur, la commission va dans « recettes »."""
    conn = db()
    ligne = conn.execute("SELECT * FROM abonnements WHERE id = ? AND statut = 'en_attente'", (abo_id,)).fetchone()
    if ligne is None:
        return None
    conn.execute("UPDATE abonnements SET statut='valide', valide_le=? WHERE id=?", (maintenant(), abo_id))
    conn.commit()
    ecrire_portefeuille(ligne["createur_id"], "credit", ligne["net"],
                        f"Abonnement {ligne['palier']:g} FCFA — réf. {ligne['reference']}", ligne["reference"])
    ecrire_recette("abonnement", ligne["commission"], ligne["reference"])
    return {"commission": ligne["commission"], "net": ligne["net"], "createur_id": ligne["createur_id"]}

def creer_pourboire(expediteur_id: int, createur_id: int, montant: float, mot: str = "") -> int:
    montant = round(float(montant), 2)
    if montant <= 0:
        raise ValueError("Montant de pourboire invalide.")
    if expediteur_id == createur_id:
        raise ValueError("Pourboire à soi-même impossible.")
    commission, net, _ = calcul_commission(montant)
    conn = db()
    cur = conn.execute(
        "INSERT INTO pourboires (createur_id, expediteur_id, montant, commission, net, mot, cree_le)"
        " VALUES (?,?,?,?,?,?,?)",
        (createur_id, expediteur_id, montant, commission, net, (mot or "")[:280], maintenant()),
    )
    conn.commit()
    return int(cur.lastrowid)

def valider_pourboire(tip_id: int, decideur_id: int) -> Optional[Dict[str, float]]:
    conn = db()
    ligne = conn.execute("SELECT * FROM pourboires WHERE id = ? AND statut = 'en_attente'", (tip_id,)).fetchone()
    if ligne is None:
        return None
    conn.execute("UPDATE pourboires SET statut='valide' WHERE id=?", (tip_id,))
    conn.commit()
    ecrire_portefeuille(ligne["createur_id"], "credit", ligne["net"],
                        "Pourboire reçu" + (f" — « {ligne['mot']} »" if ligne["mot"] else ""), f"TIP-{tip_id}")
    ecrire_recette("pourboire", ligne["commission"], f"TIP-{tip_id}")
    return {"commission": ligne["commission"], "net": ligne["net"], "createur_id": ligne["createur_id"]}

def tableau_paiement() -> List[Dict[str, Any]]:
    """Compteur « TICKETS + SOMME FCFA + NUMÉRO » par utilisateur, pour un
    paiement manuel ultra-rapide en fin de mois."""
    conn = db()
    lignes = conn.execute(
        "SELECT u.id, u.pseudo, u.telephone, u.payout_phone, u.payout_op,"
        "       COALESCE(SUM(p.tickets),0) AS tickets, COALESCE(SUM(p.fcfa),0) AS fcfa,"
        "       COUNT(p.id) AS ecritures"
        " FROM users u JOIN portefeuille p ON p.user_id = u.id"
        " WHERE p.sens='credit' AND p.statut='en_attente'"
        " GROUP BY u.id ORDER BY fcfa DESC"
    ).fetchall()
    return [dict(l) for l in lignes]

def marquer_paye(user_id: int, decideur_id: int) -> Dict[str, Any]:
    """Clôture le mois pour un utilisateur : les écritures en attente passent en
    'paye' et une ligne de paiement horodatée est archivée."""
    conn = db()
    agg = conn.execute(
        "SELECT COALESCE(SUM(tickets),0) AS t, COALESCE(SUM(fcfa),0) AS f"
        " FROM portefeuille WHERE user_id=? AND sens='credit' AND statut='en_attente'", (user_id,),
    ).fetchone()
    u = utilisateur_par_id(user_id)
    if u is None or agg["f"] <= 0:
        return {"ok": False, "message": "Aucun solde en attente pour cet utilisateur."}
    telephone = u["payout_phone"] or u["telephone"] or ""
    conn.execute(
        "INSERT INTO paiements (user_id, tickets, fcfa, telephone, operateur, statut, decideur, cree_le)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (user_id, round(agg["t"], 2), round(agg["f"], 2), telephone, u["payout_op"], "paye", decideur_id, maintenant()),
    )
    conn.execute("UPDATE portefeuille SET statut='paye' WHERE user_id=? AND sens='credit' AND statut='en_attente'",
                 (user_id,))
    conn.commit()
    try:
        journal_action2(user_id, "transfert", objet="versement Mobile Money archivé",
                        montant=float(round(agg["f"], 2)),
                        details=f"Versement de {round(agg['t'], 2)} tickets vers {telephone} — écritures passées en « payé ».")
    except Exception:
        pass
    return {"ok": True, "tickets": round(agg["t"], 2), "fcfa": round(agg["f"], 2), "telephone": telephone}

def export_csv_mensuel(mois: str) -> str:
    """Export comptable mensuel (CSV « ; », BOM UTF-8, s'ouvre dans Excel) :
    transactions validées du mois, récapitulatif par créateur (numéro Mobile
    Money inclus), récapitulatif général — pour les déclarations de l'administrateur."""
    if not re.match(r"^\d{4}-\d{2}$", mois or ""):
        raise ValueError("Mois invalide (format attendu AAAA-MM).")
    conn = db()
    abos = conn.execute(
        "SELECT a.*, c.pseudo AS pseudo_c, b.pseudo AS pseudo_a,"
        "       c.telephone AS tel_c, c.payout_phone AS pay_phone, c.payout_op AS pay_op"
        " FROM abonnements a"
        " JOIN users c ON c.id = a.createur_id JOIN users b ON b.id = a.abonne_id"
        " WHERE a.statut = 'valide' AND substr(a.cree_le,1,7) = ? ORDER BY a.id", (mois,)
    ).fetchall()
    tips = conn.execute(
        "SELECT t.*, c.pseudo AS pseudo_c, e.pseudo AS pseudo_e,"
        "       c.telephone AS tel_c, c.payout_phone AS pay_phone, c.payout_op AS pay_op"
        " FROM pourboires t"
        " JOIN users c ON c.id = t.createur_id JOIN users e ON e.id = t.expediteur_id"
        " WHERE t.statut = 'valide' AND substr(t.cree_le,1,7) = ? ORDER BY t.id", (mois,)
    ).fetchall()
    tampon = io.StringIO()
    w = csv.writer(tampon, delimiter=";", lineterminator="\n")
    w.writerow(["EXPORT COMPTABLE MENSUEL — TOUTBOT MUNDO"])
    w.writerow(["Mois", mois])
    w.writerow(["Commission", f"{CONFIG['COMMISSION_PCT']:g}% + {CONFIG['COMMISSION_PLANCHER']:g} {CONFIG['DEVISE']} par transaction (mode {CONFIG['MODE_COMMISSION']})"])
    w.writerow(["Numeros de l'administrateur", f"MTN {CONFIG['ADMIN_MTN']}", f"Moov {CONFIG['ADMIN_MOOV']}"])
    w.writerow(["Genere le", maintenant()])
    w.writerow([])
    w.writerow(["TRANSACTIONS VALIDEES DU MOIS"])
    w.writerow(["Type", "Date", "Reference", "Createur", "Contrepartie", "Montant brut",
                "Commission 25%", "Commission fixe 15 FCFA", "Commission totale", "Net createur"])
    recap: Dict[int, Dict[str, Any]] = {}
    total_brut = total_com = total_net = 0.0

    def enregistrer(type_tx: str, date_tx: str, ref: str, createur_id: int, pseudo_c: str,
                    contrepartie: str, montant: float, commission: float, numero: str, operateur: str) -> None:
        nonlocal total_brut, total_com, total_net
        pct_part = round(montant * CONFIG["COMMISSION_PCT"] / 100.0, 2)
        if CONFIG["MODE_COMMISSION"] == "cumul":
            fixe_part = round(commission - pct_part, 2)
        else:  # mode « max » : le plancher fait partie de la commission, pas un supplément
            pct_part = round(commission, 2)
            fixe_part = 0.0
        net = round(montant - commission, 2)
        w.writerow([type_tx, date_tx, ref, pseudo_c, contrepartie, f"{montant:g}",
                    f"{pct_part:g}", f"{fixe_part:g}", f"{commission:g}", f"{net:g}"])
        ligne = recap.setdefault(createur_id, {"pseudo": pseudo_c, "numero": numero, "op": operateur,
                                              "nb": 0, "brut": 0.0, "pct": 0.0, "fixe": 0.0, "net": 0.0})
        ligne["nb"] += 1
        ligne["brut"] = round(ligne["brut"] + montant, 2)
        ligne["pct"] = round(ligne["pct"] + pct_part, 2)
        ligne["fixe"] = round(ligne["fixe"] + fixe_part, 2)
        ligne["net"] = round(ligne["net"] + net, 2)
        total_brut = round(total_brut + montant, 2)
        total_com = round(total_com + commission, 2)
        total_net = round(total_net + net, 2)

    for a in abos:
        enregistrer("abonnement", a["cree_le"], a["reference"], a["createur_id"], a["pseudo_c"],
                    a["pseudo_a"], a["palier"], a["commission"], a["pay_phone"] or a["tel_c"] or "", a["pay_op"])
    for t in tips:
        enregistrer("pourboire", t["cree_le"], f"TIP-{t['id']}", t["createur_id"], t["pseudo_c"],
                    t["pseudo_e"], t["montant"], t["commission"], t["pay_phone"] or t["tel_c"] or "", t["pay_op"])

    w.writerow([])
    w.writerow(["RECAPITULATIF PAR CREATEUR — base du versement manuel de fin de mois"])
    w.writerow(["Createur", "Numero Mobile Money", "Operateur", "Transactions", "Brut",
                "Commission 25%", "Commission fixe 15 FCFA", "Commissions totales", "Net a verser"])
    for ligne in sorted(recap.values(), key=lambda d: -d["net"]):
        w.writerow([ligne["pseudo"], ligne["numero"], ligne["op"], ligne["nb"], f"{ligne['brut']:g}",
                    f"{ligne['pct']:g}", f"{ligne['fixe']:g}", f"{round(ligne['pct']+ligne['fixe'],2):g}", f"{ligne['net']:g}"])
    w.writerow([])
    w.writerow(["RECAPITULATIF GENERAL"])
    w.writerow(["Total brut du mois", f"{total_brut:g}", CONFIG["DEVISE"]])
    w.writerow(["Total commissions (recettes de l'application)", f"{total_com:g}", CONFIG["DEVISE"]])
    w.writerow(["Total net des createurs", f"{total_net:g}", CONFIG["DEVISE"]])
    return tampon.getvalue()

# =============================================================================
# 💬 MESSAGERIE ET PLAINTES
# =============================================================================
def envoyer_message(expediteur_id: int, destinataire_id: int, corps: str) -> int:
    corps = (corps or "").strip()
    if not corps:
        raise ValueError("Message vide.")
    conn = db()
    cur = conn.execute(
        "INSERT INTO messages (expediteur_id, destinataire_id, corps, cree_le) VALUES (?,?,?,?)",
        (expediteur_id, destinataire_id, corps[:4000], maintenant()),
    )
    conn.commit()
    return int(cur.lastrowid)

def conversation(a_id: int, b_id: int) -> List[sqlite3.Row]:
    return db().execute(
        "SELECT * FROM messages WHERE (expediteur_id=? AND destinataire_id=?)"
        " OR (expediteur_id=? AND destinataire_id=?) ORDER BY id ASC LIMIT 300",
        (a_id, b_id, b_id, a_id),
    ).fetchall()

def contacts_de(user_id: int) -> List[Dict[str, Any]]:
    """Toutes les personnes avec qui discuter : l'administrateur (destinataire
    naturel des demandes de retrait) + tous les interlocuteurs déjà contactés."""
    conn = db()
    admin = admin_par_defaut()
    contacts: List[Dict[str, Any]] = []
    if admin and admin["id"] != user_id:
        contacts.append({"id": admin["id"], "pseudo": admin["pseudo"], "role": "administrateur", "non_lus": 0})
    for ligne in conn.execute(
        "SELECT DISTINCT u.id, u.pseudo,"
        " (SELECT COUNT(*) FROM messages m WHERE m.expediteur_id=u.id AND m.destinataire_id=?"
        "    AND m.lu=0) AS non_lus"
        " FROM users u JOIN messages m ON (m.expediteur_id=u.id AND m.destinataire_id=?)"
        "                             OR (m.destinataire_id=u.id AND m.expediteur_id=?)"
        " WHERE u.id <> ? GROUP BY u.id ORDER BY u.pseudo",
        (user_id, user_id, user_id, user_id),
    ).fetchall():
        if any(c["id"] == ligne["id"] for c in contacts):
            continue
        contacts.append({"id": ligne["id"], "pseudo": ligne["pseudo"], "role": "membre", "non_lus": ligne["non_lus"]})
    return contacts

# =============================================================================
# ⚙️ PARAMÈTRES UTILISATEUR — préférences, sécurité et suppression de compte
# =============================================================================
PREFS_DEFAUT: Dict[str, Any] = {
    "notifs_messages": 1,        # pastille des messages non lus dans la navigation
    "notifs_portefeuille": 1,    # rappel du solde à réclamer sur le fil d'accueil
    "langue": "fr",              # seule langue disponible pour le moment
    "theme": "sombre",           # sombre | clair
    "refus_groupes": 0,          # droit de retrait des messages groupés
}

def prefs_de(user_id: int) -> Dict[str, Any]:
    """Préférences de l'utilisateur (valeurs par défaut si jamais personnalisées)."""
    ligne = db().execute("SELECT * FROM parametres WHERE user_id = ?", (user_id,)).fetchone()
    if ligne is None:
        return dict(PREFS_DEFAUT)
    refus = db().execute("SELECT refus_groupes FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"notifs_messages": ligne["notifs_messages"], "notifs_portefeuille": ligne["notifs_portefeuille"],
            "langue": ligne["langue"], "theme": ligne["theme"],
            "refus_groupes": int(refus["refus_groupes"] or 0) if refus else 0}

def enregistrer_prefs(user_id: int, notifs_messages: bool, notifs_portefeuille: bool,
                      langue: str, theme: str) -> None:
    langue = langue if langue in ("fr", "en") else "fr"
    theme = theme if theme in ("sombre", "clair", "sepia") else "sombre"
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO parametres (user_id, notifs_messages, notifs_portefeuille, langue, theme, maj_le)"
        " VALUES (?,?,?,?,?,?)",
        (user_id, 1 if notifs_messages else 0, 1 if notifs_portefeuille else 0, langue, theme, maintenant()),
    )
    conn.commit()

def maj_refus_groupes(user_id: int, refus: bool) -> None:
    """Droit de retrait : l'utilisateur peut refuser les messages groupés."""
    conn = db()
    conn.execute("UPDATE users SET refus_groupes = ? WHERE id = ?", (1 if refus else 0, user_id))
    conn.commit()

def maj_profil(user_id: int, pseudo: str, bio: str, payout_phone: str, payout_op: str) -> None:
    """Mise à jour du profil public et du numéro de retrait Mobile Money."""
    bio = (bio or "").strip()[:280]
    payout_phone = nettoyer_telephone(payout_phone)
    payout_op = "Moov" if (payout_op or "").lower().startswith("moov") else "MTN"
    conn = db()
    if pseudo:
        pseudo = (pseudo or "").strip()
        if len(pseudo) < 3:
            raise ValueError("Le pseudo doit contenir au moins 3 caractères.")
        pris = conn.execute("SELECT id FROM users WHERE pseudo = ? AND id <> ?", (pseudo, user_id)).fetchone()
        if pris:
            raise ValueError("Ce pseudo est déjà utilisé.")
        conn.execute("UPDATE users SET pseudo = ? WHERE id = ?", (pseudo, user_id))
    conn.execute("UPDATE users SET bio = ?, payout_phone = ?, payout_op = ? WHERE id = ?",
                 (bio, payout_phone, payout_op, user_id))
    conn.commit()

def changer_mot_de_passe(user_id: int, ancien: str, nouveau: str, confirmation: str) -> None:
    """Changement de mot de passe : l'ancien est vérifié, le nouveau haché."""
    u = utilisateur_par_id(user_id)
    if u is None or not check_password_hash(u["mot_de_passe"], ancien or ""):
        raise ValueError("Ancien mot de passe incorrect.")
    if nouveau != confirmation:
        raise ValueError("La confirmation ne correspond pas au nouveau mot de passe.")
    if len(nouveau or "") < 6:
        raise ValueError("Le nouveau mot de passe doit contenir au moins 6 caractères.")
    conn = db()
    conn.execute("UPDATE users SET mot_de_passe = ? WHERE id = ?",
                 (generate_password_hash(nouveau), user_id))
    conn.commit()

def supprimer_compte(user_id: int, mot_de_passe: str, confirmation: str) -> None:
    """Suppression définitive et autonome du compte (droit à l'effacement RGPD).
    Les tables liées sont purgées en cascade (publications, messages, plaintes,
    écritures de portefeuille, préférences). Le compte admin est protégé."""
    u = utilisateur_par_id(user_id)
    if u is None:
        raise ValueError("Compte introuvable.")
    if u["est_admin"]:
        raise ValueError("Le compte administrateur ne peut pas être supprimé depuis les paramètres.")
    if not check_password_hash(u["mot_de_passe"], mot_de_passe or ""):
        raise ValueError("Mot de passe incorrect : suppression refusée.")
    if (confirmation or "").strip().upper() != "SUPPRIMER":
        raise ValueError("Tapez SUPPRIMER pour confirmer la suppression définitive.")
    conn = db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()

init_schema()

# =============================================================================
# 🔎 MOTEURS DE RECHERCHE GRATUITS — RÉSULTATS RÉELS, TOUJOURS AVEC LE LIEN
# =============================================================================
def _http_get(url: str, timeout: int = 10, accept: str = "*/*",
              headers: Optional[Dict[str, str]] = None) -> bytes:
    entetes = {"User-Agent": "ToutBotMundo/1.0 (+prototype)", "Accept": accept}
    if headers:
        entetes.update(headers)
    req = urllib.request.Request(url, headers=entetes)
    with urllib.request.urlopen(req, timeout=timeout) as reponse:
        return reponse.read()

def _nettoyer_texte(brut: Any, limite: int = 400) -> str:
    texte = re.sub(r"<[^>]+>", " ", str(brut or ""))
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte[:limite]

def _resultat(source: str, titre: Any, url: Any, extrait: Any, date: Any = "") -> Dict[str, str]:
    return {
        "source": source,
        "titre": _nettoyer_texte(titre, 220),
        "url": str(url or "").strip(),
        "extrait": _nettoyer_texte(extrait),
        "date": str(date or "").strip(),
    }

class WikipediaClient:
    """Wikipédia — API publique gratuite, aucune clé."""

    ENDPOINT = "https://fr.wikipedia.org/w/api.php"
    TIMEOUT = 10

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {
            "action": "query", "list": "search", "srsearch": requete,
            "format": "json", "srlimit": max_resultats, "utf8": "1",
        }
        try:
            brut = _http_get(f"{cls.ENDPOINT}?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/json")
            data = json.loads(brut.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Wikipédia indisponible : %s", exc)
            return []
        sortie = []
        for item in ((data.get("query") or {}).get("search") or [])[:max_resultats]:
            titre = item.get("title") or ""
            sortie.append(_resultat(
                "Wikipédia", titre,
                "https://fr.wikipedia.org/wiki/" + urllib.parse.quote(str(titre).replace(" ", "_")),
                item.get("snippet"), item.get("timestamp"),
            ))
        return sortie

class DuckDuckGoClient:
    """DuckDuckGo Instant Answer — API publique gratuite, aucune clé."""

    ENDPOINT = "https://api.duckduckgo.com/"
    TIMEOUT = 10

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {"q": requete, "format": "json", "no_html": "1", "no_redirect": "1"}
        try:
            brut = _http_get(f"{cls.ENDPOINT}?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/json")
            data = json.loads(brut.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("DuckDuckGo indisponible : %s", exc)
            return []
        sortie: List[Dict[str, str]] = []
        if data.get("AbstractText"):
            sortie.append(_resultat("DuckDuckGo", data.get("Heading") or requete,
                                    data.get("AbstractURL"), data.get("AbstractText")))
        for sujet in (data.get("RelatedTopics") or []):
            if len(sortie) >= max_resultats:
                break
            if isinstance(sujet, dict) and sujet.get("Text"):
                sortie.append(_resultat("DuckDuckGo", sujet.get("Text"), sujet.get("FirstURL"),
                                        sujet.get("Text")))
        return sortie[:max_resultats]

class GoogleNewsClient:
    """Google News — flux RSS public, aucune clé, aucune inscription."""

    ENDPOINT = "https://news.google.com/rss/search"
    TIMEOUT = 10

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {"q": requete, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
        try:
            brut = _http_get(f"{cls.ENDPOINT}?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/rss+xml")
            racine = ET.fromstring(brut)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Google News indisponible : %s", exc)
            return []
        sortie = []
        for item in racine.iter("item"):
            titre = item.findtext("title")
            if not titre:
                continue
            src = item.find("source")
            sortie.append(_resultat("Google News", titre, item.findtext("link"),
                                    item.findtext("description"), item.findtext("pubDate")))
            sortie[-1]["source"] = f"Google News / {src.text}" if src is not None and src.text else "Google News"
            if len(sortie) >= max_resultats:
                break
        return sortie

class SearxngClient:
    """SearXNG — métamoteur libre. Les instances publiques ferment souvent l'API
    JSON : on essaie la vôtre (SEARXNG_URL) puis une liste d'instances connues."""

    INSTANCES = ["https://searx.be", "https://baresearch.org", "https://search.disroot.org",
                 "https://priv.space", "https://searx.tiekoetter.com"]
    TIMEOUT = 10

    @classmethod
    def _instances(cls) -> List[str]:
        privee = (CONFIG.get("SEARXNG_URL") or "").strip().rstrip("/")
        return ([privee] if privee else []) + list(cls.INSTANCES)

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {"q": requete, "format": "json", "language": "fr-FR", "safesearch": "0"}
        for base in cls._instances():
            try:
                brut = _http_get(f"{base}/search?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/json")
                data = json.loads(brut.decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("SearXNG %s indisponible : %s", base, exc)
                continue
            sortie = []
            for res in (data.get("results") or [])[:max_resultats]:
                sortie.append(_resultat("SearXNG", res.get("title"), res.get("url"),
                                        res.get("content") or res.get("snippet"), res.get("publishedDate")))
            if sortie:
                return sortie
        return []

class MoteurRecherche:
    """Agrège les moteurs gratuits, déduplique et met en cache.

    HONNÊTETÉ : aucun moteur ne peut être « tous les moteurs du monde ». Ce
    module interroge 4 sources gratuites. Chaque résultat porte son moteur
    d'origine ET son lien : l'interface n'affiche jamais un résultat sans URL.
    """

    MOTEURS = ("searxng", "google_news", "wikipedia", "duckduckgo")

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache: Dict[str, Any] = {}

    def chercher(self, requete: str, max_par_moteur: int = 5) -> Dict[str, Any]:
        cle = f"{requete}|{max_par_moteur}"
        entree = self._cache.get(cle)
        if entree and time.time() - entree["at"] < self.ttl:
            reponse = dict(entree["value"])
            reponse["cache"] = "hit"
            return reponse

        lots = {
            "wikipedia": WikipediaClient.chercher(requete, max_par_moteur),
            "google_news": GoogleNewsClient.chercher(requete, max_par_moteur),
            "duckduckgo": DuckDuckGoClient.chercher(requete, max_par_moteur),
            "searxng": SearxngClient.chercher(requete, max_par_moteur),
        }
        fusion: List[Dict[str, str]] = []
        vus = set()
        for moteur in ("wikipedia", "google_news", "duckduckgo", "searxng"):
            for item in lots.get(moteur, []):
                marque = (item.get("titre") or "").lower()[:80] or (item.get("url") or "")[:80]
                if not marque or marque in vus:
                    continue
                vus.add(marque)
                fusion.append(item)
        valeur = {
            "requete": requete,
            "collecte_le": maintenant(),
            "compte": {m: len(lots.get(m, [])) for m in self.MOTEURS},
            "total": len(fusion),
            "resultats": fusion,
            "cache": "miss",
        }
        self._cache[cle] = {"at": time.time(), "value": valeur}
        return valeur

    def bloc_memoire(self, requete: str, max_par_moteur: int = 5, budget: Optional[int] = None) -> str:
        """Bloc texte prêt à être injecté dans le prompt système de l'IA.
        Chaque ligne porte son moteur et son lien : l'IA ne peut rien citer
        qui ne soit pas vérifiable."""
        budget = budget or CONFIG["AI_MAX_WEB_TOTAL"]
        data = self.chercher(requete, max_par_moteur)
        c = data["compte"]
        lignes = [
            "=== FLUX TEMPS RÉEL (résultats réellement collectés) ===",
            f"Sujet : {requete}",
            f"Collecté le : {data['collecte_le']}",
            f"Moteurs : SearXNG={c['searxng']} | Google News={c['google_news']} |"
            f" Wikipédia={c['wikipedia']} | DuckDuckGo={c['duckduckgo']}",
            "",
        ]
        utilise = sum(len(x) for x in lignes)
        for i, item in enumerate(data["resultats"], 1):
            bloc = (f"[{i}] ({item['source']})\n"
                    f"    Titre : {item['titre']}\n"
                    f"    Date  : {item['date'] or 'non fournie'}\n"
                    f"    Lien  : {item['url']}\n"
                    f"    Info  : {item['extrait'] or '(pas de résumé)'}")
            if utilise + len(bloc) > budget:
                break
            lignes.append(bloc)
            utilise += len(bloc)
        if not data["resultats"]:
            lignes.append("AUCUN RÉSULTAT RÉEL COLLECTÉ.")
        return "\n".join(lignes)

def formater_resultats_html(data: Dict[str, Any]) -> str:
    """Rendu HTML des résultats réels (utilisé aussi si l'IA est muette)."""
    if not data.get("resultats"):
        return "<p class='muet'>Aucun résultat réel collecté sur les moteurs gratuits pour cette requête.</p>"
    morceaux = ["<ol class='resultats'>"]
    for item in data["resultats"]:
        lien = urllib.parse.quote(item["url"], safe="") if item.get("url") else ""
        morceaux.append(
            "<li>"
            f"<a href='{item['url']}' target='_blank' rel='noopener'>{item['titre']}</a>"
            f"<span class='badge'>{item['source']}</span>"
            f"<p class='muet'>{item['extrait'] or '(pas de résumé)'}</p>"
            f"<p class='lien'>{item['url']} — <a href='/aller?u={lien}'>accès</a></p>"
            "</li>"
        )
    morceaux.append("</ol>")
    return "".join(morceaux)

# =============================================================================
# 🤖 IA ANCRÉE SUR LE RÉEL — elle répond ou elle avoue ne rien trouver
# =============================================================================
PROMPT_SYSTEME = """🏛️ CORTEX DE ZEUS V4 — POSTURE ABSOLUE (ToutBot Mundo)

Tu disposes de deux couches :
  1) Ton savoir généraliste (sciences, droit, histoire, techniques).
  2) Un BLOC TEMPS RÉEL contenant des résultats réellement collectés sur des
     moteurs de recherche gratuits, chacun accompagné de son lien.

RÈGLES ABSOLUES, NON NÉGOCIABLES :
  • Tu n'inventes RIEN : aucun fait, aucun chiffre, aucun nom, aucune date,
    aucun signe, aucun symbole, aucune publication.
  • Tout ce que tu affirmes sur l'actualité doit provenir du BLOC TEMPS RÉEL
    et être suivi de son lien entre parenthèses.
  • Si le BLOC TEMPS RÉEL est vide ou insuffisant, tu réponds de manière
    explicite : « Aucune publication réelle trouvée sur ce point » et tu
    t'arrêtes là. Tu ne combles JAMAIS un trou avec une supposition.
  • Tu distingues toujours : [VÉRIFIÉ — source] et [CONNAISSANCE GÉNÉRALE].
  • Réponse en français, ton clair, concis, sans emphase inutile.
"""

def interroger_ia(question: str, bloc_reel: str) -> str:
    """Appelle le moteur d'IA (Pollinations). Lève une erreur si muet."""
    corps = {
        "model": CONFIG["AI_MODEL"],
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEME},
            {"role": "user", "content": f"BLOC TEMPS RÉEL :\n{bloc_reel}\n\nQUESTION : {question}"},
        ],
    }
    entetes = {"Content-Type": "application/json", "User-Agent": "ToutBot-Mundo/1.0"}
    if CONFIG["POLLINATIONS_KEY"]:
        entetes["Authorization"] = f"Bearer {CONFIG['POLLINATIONS_KEY']}"
    req = urllib.request.Request(CONFIG["AI_ENDPOINT"],
                                 data=json.dumps(corps).encode("utf-8"),
                                 headers=entetes, method="POST")
    with urllib.request.urlopen(req, timeout=CONFIG["AI_TIMEOUT"]) as reponse:
        brut = reponse.read().decode("utf-8", "replace")
    try:
        data = json.loads(brut)
        if isinstance(data, dict) and data.get("choices"):
            return str(data["choices"][0].get("message", {}).get("content") or "").strip()
        if isinstance(data, dict):
            return str(data.get("content") or data.get("text") or "").strip()
    except ValueError:
        pass
    return brut.strip()

MOTEUR = MoteurRecherche()

# =============================================================================
# 🤖 CLIENT LLM PLUGGABLE (clé lue depuis l'environnement, jamais en dur)
# =============================================================================
def interroger_llm(systeme: str, message: str) -> str:
    """Interroge un LLM configurable par variables d'environnement.
    Clé : TOUTBOT_LLM_KEY (repli : POLLINATIONS_KEY). Endpoint et modèle :
    TOUTBOT_LLM_ENDPOINT / TOUTBOT_LLM_MODEL. Renvoie "" si aucune clé n'est
    configurée ou en cas d'échec : l'appelant bascule alors sur la réponse
    de repli fondée sur des règles."""
    endpoint = os.environ.get("TOUTBOT_LLM_ENDPOINT") or CONFIG["AI_ENDPOINT"]
    modele = os.environ.get("TOUTBOT_LLM_MODEL") or CONFIG["AI_MODEL"]
    cle = os.environ.get("TOUTBOT_LLM_KEY") or CONFIG["POLLINATIONS_KEY"]
    if not cle:
        return ""
    corps = {"model": modele, "messages": [
        {"role": "system", "content": systeme},
        {"role": "user", "content": message}]}
    entetes = {"Content-Type": "application/json", "User-Agent": "ToutBot-Mundo/1.0"}
    entetes["Authorization"] = f"Bearer {cle}"
    try:
        req = urllib.request.Request(endpoint, data=json.dumps(corps).encode("utf-8"),
                                     headers=entetes, method="POST")
        with urllib.request.urlopen(req, timeout=CONFIG["AI_TIMEOUT"]) as reponse:
            brut = reponse.read().decode("utf-8", "replace")
        data = json.loads(brut)
        if isinstance(data, dict) and data.get("choices"):
            return str(data["choices"][0].get("message", {}).get("content") or "").strip()
        if isinstance(data, dict):
            return str(data.get("content") or data.get("text") or "").strip()
        return brut.strip()
    except Exception:
        return ""

# Réponses de repli déterministes (aucune invention, aucune clé requise).
REPONSE_REGLE_PAIEMENT = (
    "Bonjour,\n\nVotre demande concerne un paiement : vérifiez l'état de vos écritures "
    "dans le menu Portefeuille. Si un versement est en attente, l'administrateur le "
    "valide après réception sur les numéros Mobile Money affichés sur le fil d'accueil. "
    "Indiquez ici votre référence de paiement : votre demande est transmise et vous "
    "recevrez une réponse sous 72 heures.\n\nL'équipe d'administration — ToutBot Mundo")
REPONSE_REGLE_BLOCAGE = (
    "Bonjour,\n\nVotre demande concerne l'accès à votre compte. Si votre compte est "
    "bloqué, seul l'administrateur peut le réactiver après examen des conditions "
    "d'utilisation. Exposez votre situation ici : votre demande est transmise et vous "
    "recevrez une réponse sous 72 heures.\n\nL'équipe d'administration — ToutBot Mundo")
REPONSE_REGLE_GENERIQUE = (
    "Bonjour,\n\nVotre demande a bien été reçue et est transmise à l'administrateur. "
    "Vous recevrez une réponse dans cette même discussion sous 72 heures. Les "
    "informations sur vos écritures restent visibles dans votre espace Portefeuille.\n\n"
    "L'équipe d'administration — ToutBot Mundo")

def classer_demande(texte: str) -> str:
    t = (texte or "").lower()
    if any(m in t for m in ("paiement", "paye", "payer", "abonnement", "crédit", "credit",
                            "ticket", "portefeuille", "fcfa", "retrait", "montant")):
        return "paiement"
    if any(m in t for m in ("bloqué", "bloque", "blocage", "suspendu", "connexion",
                            "connecter", "mot de passe")):
        return "blocage"
    return "generique"

def reponse_regle(texte: str) -> str:
    cle = classer_demande(texte)
    return {"paiement": REPONSE_REGLE_PAIEMENT, "blocage": REPONSE_REGLE_BLOCAGE,
            "generique": REPONSE_REGLE_GENERIQUE}[cle]

def reponse_ia_assistance(question: str, regles: str = "") -> Tuple[str, str]:
    """Renvoie (réponse, origine) — origine vaut 'ia' (LLM configuré) ou 'regle'
    (repli déterministe sans clé)."""
    systeme = PROMPT_ASSISTANCE
    if (regles or "").strip():
        systeme += "\n\nRÈGLES PARTICULIÈRES DE L'ADMINISTRATEUR (prioritaires) :\n" + regles.strip()[:1500]
    texte = interroger_llm(systeme, (question or "")[:2000])
    if texte:
        return texte, "ia"
    return reponse_regle(question), "regle"

# =============================================================================
# 🔐 SESSION ET CSRF
# =============================================================================
def utilisateur_courant():
    uid = session.get("uid")
    if not uid:
        return None
    u = utilisateur_par_id(int(uid))
    if u is None or u["bloque"]:
        session.clear()
        return None
    return u

def jeton_csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]

@app.before_request
def _protection_csrf():
    if request.method == "POST" and not request.path.startswith("/api/"):
        envoye = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not envoye or envoye != session.get("csrf"):
            return "Jeton CSRF invalide ou expiré.", 400
    return None

@app.before_request
def maj_presence():
    """Marque la présence en ligne de tout utilisateur connecté (5 min de tolérance)."""
    if "uid" in session:
        try:
            maj_activite(int(session["uid"]))
        except (TypeError, ValueError):
            pass

@app.context_processor
def _contexte():
    me = utilisateur_courant()
    classe_theme, non_lus_total, prefs = "", 0, dict(PREFS_DEFAUT)
    if me is not None:
        prefs = prefs_de(me["id"])
        if prefs.get("theme") in ("clair", "sepia"):
            classe_theme = "theme-" + prefs["theme"]
        if prefs["notifs_messages"]:
            non_lus_total = int(db().execute(
                "SELECT COUNT(*) AS n FROM messages WHERE destinataire_id = ? AND lu = 0",
                (me["id"],)).fetchone()["n"])
    return {
        "me": me,
        "csrf": jeton_csrf(),
        "paliers": PALIERS,
        "admin_mtn": CONFIG["ADMIN_MTN"],
        "admin_moov": CONFIG["ADMIN_MOOV"],
        "devise": CONFIG["DEVISE"],
        "flash": session.pop("flash", None),
        "annonce": ANNONCE_INSCRIPTION,
        "modele_paye": MODELE_MESSAGE_PAYE.format(mois=libelle_mois()),
        "modele_attente": MODELE_MESSAGE_ATTENTE.format(mois=libelle_mois()),
        "modele_transaction": MODELE_REPONSE_TRANSACTION,
        "modele_relance": MODELE_RELANDE_IMPAYES.format(mois=libelle_mois()),
        "mode_commission": CONFIG["MODE_COMMISSION"],
        "commission_pct": CONFIG["COMMISSION_PCT"],
        "commission_plancher": CONFIG["COMMISSION_PLANCHER"],
        "classe_theme": classe_theme,
        "non_lus_total": non_lus_total,
        "prefs": prefs,
    }

def exiger_connexion():
    if utilisateur_courant() is None:
        return redirect(url_for("connexion"))
    return None

# =============================================================================
# 📜 JOURNAL D'ACTIVITÉ (V4) — piste d'audit complète + MODE HORS-LIGNE
# =============================================================================
JOURNAL_ACTIONS = ["connexion", "deconnexion", "publication", "commentaire", "j_aime",
                   "validation_abonnement", "envoi_groupe", "export_comptable",
                   "export_suivi_xlsx", "import_suivi_xlsx", "export_journal"]

def journal_action(action: str, cible: str = "", details: str = "",
                   utilisateur: Optional[int] = None) -> None:
    """Enregistre une action dans le journal d'audit — n'interrompt jamais l'action appelante."""
    try:
        if utilisateur is None:
            utilisateur = session.get("uid")
        try:
            utilisateur = int(utilisateur) if utilisateur else None
        except (TypeError, ValueError):
            utilisateur = None
        conn = db()
        conn.execute("INSERT INTO journal (user_id, action, cible, details, adresse_ip, cree_le)"
                     " VALUES (?,?,?,?,?,?)",
                     (utilisateur, action[:40], cible[:120], details[:400],
                      (request.remote_addr or "")[:60], maintenant()))
        conn.commit()
    except Exception:
        pass

@app.route("/admin/journal")
def admin_journal():
    """Piste d'audit filtrable et paginée : qui, quoi, où, quand."""
    refus = exiger_admin()
    if refus:
        return refus
    action = (request.args.get("action") or "").strip()
    pseudo = (request.args.get("pseudo") or "").strip()
    cherche = (request.args.get("q") or "").strip()
    try:
        page_num = max(1, int(request.args.get("page", 1) or 1))
    except ValueError:
        page_num = 1
    par_page = 50
    conn = db()
    conditions, params = [], []
    if action:
        conditions.append("j.action = ?")
        params.append(action)
    if pseudo:
        conditions.append("u.pseudo LIKE ?")
        params.append("%" + pseudo + "%")
    if cherche:
        conditions.append("(j.cible LIKE ? OR j.details LIKE ?)")
        params += ["%" + cherche + "%", "%" + cherche + "%"]
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    total = conn.execute("SELECT COUNT(*) FROM journal j LEFT JOIN users u ON u.id = j.user_id"
                         + where, params).fetchone()[0]
    lignes = conn.execute(
        "SELECT j.*, u.pseudo FROM journal j LEFT JOIN users u ON u.id = j.user_id" + where +
        " ORDER BY j.id DESC LIMIT ? OFFSET ?",
        params + [par_page, (page_num - 1) * par_page]).fetchall()
    pages = max(1, (total + par_page - 1) // par_page)
    return page("admin_journal.html", titre="Journal d'activité", lignes=lignes, total=total,
                page_num=page_num, pages=pages, action=action, pseudo=pseudo, cherche=cherche,
                actions=JOURNAL_ACTIONS)

@app.route("/admin/journal.csv")
def admin_journal_csv():
    """Export CSV du journal (5000 dernières entrées), lisible dans Excel."""
    refus = exiger_admin()
    if refus:
        return refus
    lignes = db().execute(
        "SELECT j.cree_le, COALESCE(u.pseudo,'') AS auteur, j.action, j.cible, j.details, j.adresse_ip"
        " FROM journal j LEFT JOIN users u ON u.id = j.user_id ORDER BY j.id DESC LIMIT 5000").fetchall()
    import csv as _csv
    sortie = io.StringIO()
    ecrivain = _csv.writer(sortie, delimiter=";")
    ecrivain.writerow(["Horodatage", "Auteur", "Action", "Cible", "Détails", "Adresse IP"])
    for l in lignes:
        ecrivain.writerow([l["cree_le"], l["auteur"], l["action"], l["cible"], l["details"], l["adresse_ip"]])
    journal_action("export_journal", details="Export CSV du journal")
    return Response("\ufeff" + sortie.getvalue(), mimetype="text/csv;charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=journal_activite.csv"})

SW_JS = """const CACHE = 'tbm-cache-v1';
const PAGES = ['/', '/tarifs', '/connexion'];
self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(PAGES); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  if (e.request.method !== 'GET' || e.request.url.indexOf(self.location.origin) !== 0) return;
  e.respondWith(fetch(e.request).then(function (rep) {
    if (rep && rep.ok && rep.type === 'basic') {
      var copie = rep.clone();
      caches.open(CACHE).then(function (c) { c.put(e.request, copie); });
    }
    return rep;
  }).catch(function () {
    return caches.match(e.request).then(function (trouve) { return trouve || caches.match('/'); });
  }));
});
"""

@app.route("/sw.js")
def service_worker():
    """Service worker du mode hors-ligne (portée racine)."""
    return Response(SW_JS, mimetype="application/javascript")

@app.route("/manifest.webmanifest")
def manifeste():
    donnees = {"name": "ToutBot Mundo", "short_name": "Mundo", "start_url": "/",
               "display": "standalone", "background_color": "#10141c",
               "theme_color": "#c8a24a", "lang": "fr"}
    return Response(json.dumps(donnees), mimetype="application/manifest+json")

@app.route("/api/non_lus")
def api_non_lus():
    """Compteur de messages non lus (pour les notifications sonores)."""
    moi = utilisateur_courant()
    if moi is None:
        return jsonify(n=0, connecte=False)
    n = int(db().execute("SELECT COUNT(*) FROM messages WHERE destinataire_id = ? AND lu = 0",
                         (moi["id"],)).fetchone()[0])
    return jsonify(n=n, connecte=True)

# =============================================================================
# 🧭 NOUVEAUTÉS V3 — API diverses, suivi Excel des abonnements, exports
# =============================================================================
try:
    import openpyxl
    from openpyxl.styles import Font as _FontX, PatternFill as _FillX
    from openpyxl.utils import get_column_letter as _col_xlsx
    _EXCEL_OK = True
except ImportError:
    _EXCEL_OK = False

@app.route("/api/ping")
def api_ping():
    """Point de santé de l'application (monitoring externe possible)."""
    return jsonify(ok=True, serveur="ToutBot Mundo V3", horodatage=maintenant())

@app.route("/api/horloge")
def api_horloge():
    import time as _t
    decalage = -(_t.timezone if _t.localtime().tm_isdst == 0 else _t.altzone) // 3600
    return jsonify(utc=maintenant(), epoch=int(_t.time()), decalage_heures=decalage)

def _suivi_abonnements_donnees(conn):
    return conn.execute(
        "SELECT a.*, c.pseudo AS createur, b.pseudo AS abonne FROM abonnements a"
        " JOIN users c ON c.id = a.createur_id JOIN users b ON b.id = a.abonne_id"
        " ORDER BY a.id DESC LIMIT 300").fetchall()

@app.route("/admin/suivi.xlsx")
def admin_suivi_xlsx():
    """Export Excel complet du suivi : feuilles Abonnements, Pourboires, Synthèse."""
    refus = exiger_admin()
    if refus:
        return refus
    if not _EXCEL_OK:
        session["flash"] = "Le module openpyxl n'est pas installé (pip install openpyxl)."
        return redirect(url_for("admin"))
    conn = db()
    tampon = io.BytesIO()
    classeur = openpyxl.Workbook()
    entete_font = _FontX(bold=True, color="FFFFFF")
    entete_fill = _FillX("solid", fgColor="B07D3A")

    def feuille(nom, titres, lignes, largeurs):
        ws = classeur.create_sheet(nom)
        for c, t in enumerate(titres, 1):
            cellule = ws.cell(1, c, t)
            cellule.font = entete_font
            cellule.fill = entete_fill
        for r, ligne in enumerate(lignes, 2):
            for c, v in enumerate(ligne, 1):
                ws.cell(r, c, v)
        for c, l in enumerate(largeurs, 1):
            ws.column_dimensions[_col_xlsx(c)].width = l
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    abos = conn.execute(
        "SELECT a.id, c.pseudo AS createur, b.pseudo AS abonne, a.palier, a.commission,"
        " a.net, a.reference, a.statut, a.cree_le, a.valide_le FROM abonnements a"
        " JOIN users c ON c.id = a.createur_id JOIN users b ON b.id = a.abonne_id"
        " ORDER BY a.id DESC").fetchall()
    feuille("Abonnements",
            ["N°", "Créateur", "Abonné", "Palier (FCFA)", "Commission", "Net créateur",
             "Référence", "Statut", "Créé le", "Validé le"],
            [[a["id"], a["createur"], a["abonne"], a["palier"], a["commission"], a["net"],
              a["reference"], a["statut"], a["cree_le"], a["valide_le"] or ""] for a in abos],
            [6, 14, 14, 14, 12, 14, 22, 12, 20, 20])
    tips = conn.execute(
        "SELECT t.id, c.pseudo AS createur, e.pseudo AS expediteur, t.montant, t.commission,"
        " t.net, t.mot, t.statut, t.cree_le FROM pourboires t"
        " JOIN users c ON c.id = t.createur_id JOIN users e ON e.id = t.expediteur_id"
        " ORDER BY t.id DESC").fetchall()
    feuille("Pourboires",
            ["N°", "Créateur", "Expéditeur", "Montant", "Commission", "Net", "Mot", "Statut", "Créé le"],
            [[t["id"], t["createur"], t["expediteur"], t["montant"], t["commission"], t["net"],
              t["mot"], t["statut"], t["cree_le"]] for t in tips],
            [6, 14, 14, 10, 12, 12, 24, 12, 20])
    feuille("Synthèse", ["Indicateur", "Valeur"],
            [["Abonnements (total)", len(abos)],
             ["Abonnements validés", sum(1 for a in abos if a["statut"] == "valide")],
             ["Abonnements en attente", sum(1 for a in abos if a["statut"] == "en_attente")],
             ["Pourboires (total)", len(tips)],
             ["Pourboires validés", sum(1 for t in tips if t["statut"] == "valide")],
             ["Recettes commission (FCFA)", total_recettes()],
             ["Membres", conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]],
             ["Publications", conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]],
             ["Exporté le", maintenant()]],
            [34, 24])
    if "Sheet" in classeur.sheetnames:
        classeur.remove(classeur["Sheet"])
    classeur.save(tampon)
    tampon.seek(0)
    journal_action("export_suivi_xlsx", details="Classeur suivi des abonnements téléchargé")
    return Response(tampon.read(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=suivi_abonnements.xlsx"})

@app.route("/admin/suivi/import", methods=["POST"])
def admin_suivi_import():
    """Réimporte un classeur de suivi : applique les décisions valide ou refuse,
    portées par la colonne Statut, appariées par la colonne Référence."""
    refus = exiger_admin()
    if refus:
        return refus
    if not _EXCEL_OK:
        session["flash"] = "Le module openpyxl n'est pas installé (pip install openpyxl)."
        return redirect(url_for("admin"))
    fichier = request.files.get("fichier")
    if fichier is None or not fichier.filename:
        session["flash"] = "Aucun fichier reçu pour l'import."
        return redirect(url_for("admin"))
    try:
        classeur = openpyxl.load_workbook(io.BytesIO(fichier.read()), read_only=True, data_only=True)
        ws = classeur["Abonnements"] if "Abonnements" in classeur.sheetnames else classeur.active
        lignes = list(ws.iter_rows(values_only=True))
        titres = [str(t or "").strip().lower() for t in lignes[0]]
        i_ref = titres.index("référence") if "référence" in titres else (
            titres.index("reference") if "reference" in titres else None)
        i_statut = titres.index("statut") if "statut" in titres else None
        corriges, ignores = 0, 0
        conn = db()
        for ligne in lignes[1:]:
            if i_ref is None or i_statut is None or ligne is None:
                ignores += 1
                continue
            reference = str(ligne[i_ref] or "").strip()
            statut = str(ligne[i_statut] or "").strip().lower()
            if not reference:
                ignores += 1
                continue
            trouve = conn.execute("SELECT id, statut FROM abonnements WHERE reference = ?",
                                  (reference,)).fetchone()
            if trouve is None or trouve["statut"] != "en_attente" or statut not in ("valide", "refuse"):
                ignores += 1
                continue
            if statut == "valide":
                valider_abonnement(trouve["id"], int(session["uid"]))
            else:
                conn.execute("UPDATE abonnements SET statut='refuse', valide_le=? WHERE id=?",
                             (maintenant(), trouve["id"]))
            corriges += 1
        conn.commit()
        session["flash"] = f"Import Excel : {corriges} décision(s) appliquée(s), {ignores} ligne(s) ignorée(s)."
        journal_action("import_suivi_xlsx", details=f"{corriges} décision(s) appliquée(s)")
    except Exception as exc:
        session["flash"] = f"Import impossible : {exc}"
    return redirect(url_for("admin"))

@app.route("/posts/export.md")
def posts_export_md():
    """Archive le fil mondial en fichier Markdown (500 dernières publications)."""
    fil = db().execute(
        "SELECT p.corps, p.cree_le, u.pseudo FROM posts p JOIN users u ON u.id = p.auteur_id"
        " ORDER BY p.id DESC LIMIT 500").fetchall()
    lignes = ["# Fil mondial — ToutBot Mundo", "", f"_Export du {maintenant()}_", ""]
    for p in fil:
        lignes += [f"**{p['pseudo']}** — {p['cree_le']}", "", p["corps"], "", "---", ""]
    return Response("\n".join(lignes), mimetype="text/markdown",
                    headers={"Content-Disposition": "attachment; filename=fil-toutbot.md"})

# =============================================================================
# 🎨 GABARITS
# =============================================================================
BASE = """<!doctype html>
<html lang="fr" class="{{ classe_theme }}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="msvalidate.01" content="64E7DB6532317DFEF5D10CFBCE3C86E0" />
<title>{{ titre }} — ToutBot Mundo</title>
<style>
:root{--or:#c8a24a;--fond:#10141c;--carte:#1a2130;--bord:#2b3549;--texte:#e9edf5;--muet:#8e9bb3;--vert:#2fbf71;--rouge:#e0575b;--bleu:#4d8ef7;--champ:#0f141d;--code:#0e1420;--ombre:rgba(0,0,0,.45)}
html[data-theme=clair]{--or:#a87d1e;--fond:#f4f1ea;--carte:#ffffff;--bord:#cfc8b8;--texte:#26282e;--muet:#6d7380;--vert:#1d8f56;--rouge:#c44448;--bleu:#2f6fd7;--champ:#fbf9f3;--code:#efe9dc;--ombre:rgba(0,0,0,.18)}
html[data-theme=sepia]{--or:#b07d3a;--fond:#e8dcc4;--carte:#f6eeda;--bord:#c9b78e;--texte:#4a3a26;--muet:#83705a;--vert:#4a7a3d;--rouge:#b05050;--bleu:#6a5acd;--champ:#f2e7cd;--code:#efe4c8;--ombre:rgba(74,58,38,.25)}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--texte);font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;font-size:15px;line-height:1.55}
a{color:var(--or);text-decoration:none}a:hover{text-decoration:underline}
.header{position:sticky;top:0;z-index:10;background:#0c1017;border-bottom:1px solid var(--bord);padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.logo{font-weight:800;font-size:18px;color:var(--or);letter-spacing:.4px}
.header nav{display:flex;gap:12px;flex-wrap:wrap;margin-left:auto;align-items:center}
.wrap{max-width:960px;margin:0 auto;padding:18px 16px 60px}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:12px;padding:16px;margin-bottom:14px}
h1{font-size:22px;margin:0 0 6px}h2{font-size:17px;margin:0 0 10px}h3{font-size:15px;margin:0 0 8px;color:var(--or)}
.muet{color:var(--muet);font-size:13px;margin:4px 0}
.badge{display:inline-block;background:#20293b;border:1px solid var(--bord);border-radius:999px;padding:1px 9px;font-size:11px;color:var(--muet);margin-left:6px;white-space:nowrap}
input,textarea,select,button{font:inherit;color:var(--texte);background:#0f141d;border:1px solid var(--bord);border-radius:9px;padding:9px 11px;width:100%}
textarea{min-height:90px;resize:vertical}
button,.btn{background:var(--or);color:#191307;border:none;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;width:auto;display:inline-block}
.btn-sec{background:#20293b;color:var(--texte);border:1px solid var(--bord)}
.btn-vert{background:var(--vert);color:#04220f}
.btn-rouge{background:var(--rouge);color:#fff}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.tuile{background:#151b27;border:1px solid var(--bord);border-radius:10px;padding:12px;text-align:center;display:block}
.tuile b{display:block;font-size:20px;color:var(--or)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--bord);vertical-align:top}
th{color:var(--muet);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.tel{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:700;color:var(--vert);white-space:nowrap}
.mm{border:1px solid var(--bord);background:#151b27;border-radius:12px;padding:12px 14px;margin:12px 0}
.mm h3{margin:0 0 6px}
.mm-num{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:7px 0}
.mm-num label{flex:0 0 150px;color:var(--muet);font-size:13px}
.mm-num input{flex:0 1 auto;width:auto;min-width:215px;text-align:center;font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:700;color:var(--vert);letter-spacing:1px}
.flash{background:#2b3a24;border:1px solid #4b7a37;border-radius:9px;padding:10px 12px;margin-bottom:12px}
.annonce{background:#3a2f10;border:1px solid #b8860b;border-radius:9px;padding:10px 12px;margin-bottom:12px;font-weight:600;color:var(--or)}
.modele-bloc{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}
.modele-bloc>div{border:1px solid var(--bord);border-radius:9px;padding:8px 10px;background:#151b27}
.modele-grand{flex:1 1 340px}
.modele-petit{flex:0 1 280px}
.modele-bloc label{display:block;color:var(--muet);font-size:12px;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px}
.modele-bloc textarea{width:100%;min-height:120px;resize:vertical;background:#0e1420;color:#e8eaf0;border:1px solid var(--bord);border-radius:7px;padding:8px;font:inherit;white-space:pre-wrap}
.modele-petit textarea{min-height:100px}
.ia-reponse{border:1px solid var(--or);background:#3a2f10;border-radius:8px;padding:8px 10px;margin:6px 0}
.ia-reponse b{color:var(--or)}
.case-li{white-space:nowrap}
.resultats li{margin-bottom:12px}
.lien{font-size:12px;color:var(--muet);word-break:break-all}
.msg{border-left:3px solid var(--bord);padding:6px 10px;margin:5px 0;background:#151b27;border-radius:0 8px 8px 0}
.msg.moi{border-left-color:var(--or)}
.enligne{display:inline-block;background:var(--vert);color:#04220f;border-radius:999px;padding:0 8px;font-size:11px;font-weight:700;margin-left:6px;white-space:nowrap}
.horsligne{display:inline-block;background:#20293b;color:var(--muet);border-radius:999px;padding:0 8px;font-size:11px;margin-left:6px;white-space:nowrap}
.meta-pub{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.comms{margin-top:10px;border-top:1px solid var(--bord);padding-top:8px}
.comm{display:flex;justify-content:space-between;gap:8px;background:#151b27;border:1px solid var(--bord);border-radius:8px;padding:6px 10px;margin:5px 0}
.comm .corps{white-space:pre-wrap;word-break:break-word}
.form-comms{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.form-comms input[name=corps]{flex:1 1 220px;width:auto}
.mini-baremes{display:flex;gap:6px;align-items:flex-end;height:80px;margin-top:8px}
.mini-baremes .barre{background:var(--or);border-radius:4px 4px 0 0;min-width:22px;position:relative}
.mini-baremes .zero{background:#20293b}
.mini-baremes .val{position:absolute;top:-18px;left:50%;transform:translateX(-50%);font-size:11px;color:var(--muet)}
.legendes{display:flex;gap:10px;font-size:11px;color:var(--muet);margin-top:4px}
.kpi{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:8px}
.kpi .case{background:#151b27;border:1px solid var(--bord);border-radius:10px;padding:10px 12px}
.kpi .case b{display:block;font-size:20px;color:var(--or)}
.kpi .case span{font-size:12px;color:var(--muet)}
.pastille{background:var(--rouge);color:#fff;border-radius:999px;font-size:11px;padding:0 6px;margin-left:6px}
.moi-badge{color:var(--texte);font-weight:700;background:#20293b;border:1px solid var(--bord);border-radius:999px;padding:4px 12px;font-size:13px;white-space:nowrap}
footer{color:var(--muet);font-size:12px;text-align:center;padding:24px 16px}
/* ===== AMÉLIORATION INTERACTION — tout est cliquable, sélectionnable, copiable ===== */
*{-webkit-tap-highlight-color:rgba(200,162,74,.25);-webkit-touch-callout:default}
html{scroll-behavior:smooth}
body{touch-action:manipulation;overflow-x:hidden;user-select:text;-webkit-user-select:text;-moz-user-select:text;-ms-user-select:text}
h1,h2,h3,h4,p,span,b,div,td,th,li,label,small,strong,em,blockquote,footer{user-select:text;-webkit-user-select:text;cursor:text}
input,textarea,select{user-select:text;-webkit-user-select:text;min-height:44px}
select{width:auto;min-width:140px}
::selection{background:rgba(200,162,74,.45);color:#fff}
button,.btn,a.btn,nav a,.btn-sec,.btn-vert,.btn-rouge,.tuile,.badge,.pastille,.moi-badge{touch-action:manipulation;cursor:pointer}
button,.btn,.btn-sec,.btn-vert,.btn-rouge{min-height:44px;min-width:44px}
nav a{padding:10px 8px;display:inline-block;min-height:44px}
a,button,.btn,.btn-sec,.btn-vert,.btn-rouge,.tuile{transition:transform .12s ease,filter .12s ease,box-shadow .12s ease}
a:hover,button:hover,.btn:hover{filter:brightness(1.08)}
a:active,button:active,.btn:active,.btn-sec:active,.tuile:active,.badge:active{transform:scale(.96)}
@media (hover:none){a,button,.btn,.btn-sec{display:inline-flex;align-items:center;justify-content:center}}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--or);outline-offset:2px}
.flotant{position:fixed;top:70px;right:14px;z-index:60;max-width:330px;background:var(--carte);border:1px solid var(--or);border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.45);padding:12px}
.flotant h4{margin:2px 0 6px;color:var(--or);font-size:14px}
.flotant .poignee{cursor:grab;touch-action:none;background:#20293b;border-radius:8px;padding:6px 10px;margin:-4px -4px 8px;font-size:12px;color:var(--muet);user-select:none;-webkit-user-select:none}
@media (max-width:600px){.flotant{left:14px;max-width:none}}
/* ===== THÈME CLAIR/SOMBRE — correction des couleurs fixes en mode clair ===== */
html[data-theme=clair] .header{background:#efe9dc}
html[data-theme=clair] .tuile,html[data-theme=clair] .msg,html[data-theme=clair] .comm,html[data-theme=clair] .mm,html[data-theme=clair] .modele-bloc>div,html[data-theme=clair] .kpi .case{background:#f0ece1}
html[data-theme=clair] .badge,html[data-theme=clair] .horsligne,html[data-theme=clair] .moi-badge{background:#e7e2d4}
html[data-theme=clair] input,html[data-theme=clair] textarea,html[data-theme=clair] select{background:var(--champ)}
html[data-theme=clair] .modele-bloc textarea{background:#fffdf7;color:#26282e}
html[data-theme=clair] .ia-reponse{background:#f3ead2}
html[data-theme=clair] .flash{background:#e4efdd}
html[data-theme=clair] .annonce{background:#f3ead2}
html[data-theme=sepia] .header{background:#e0d2b3}
html[data-theme=sepia] .tuile,html[data-theme=sepia] .msg,html[data-theme=sepia] .comm,html[data-theme=sepia] .mm,html[data-theme=sepia] .modele-bloc>div,html[data-theme=sepia] .kpi .case{background:#efe4c8}
html[data-theme=sepia] .badge,html[data-theme=sepia] .horsligne,html[data-theme=sepia] .moi-badge{background:#e7d9b8}
html[data-theme=sepia] input,html[data-theme=sepia] textarea,html[data-theme=sepia] select{background:var(--champ)}
html[data-theme=sepia] .modele-bloc textarea{background:#f8f0da;color:#4a3a26}
html[data-theme=sepia] .ia-reponse,html[data-theme=sepia] .annonce{background:#efe0ba}
html[data-theme=sepia] .flash{background:#e6ecd8}
/* ===== MODE LECTEUR — lecture sans distraction ===== */
html.lecteur body{font-size:19px}
html.lecteur .header nav,html.lecteur .moi-badge,html.lecteur #btn-theme,html.lecteur .flotant,html.lecteur footer,html.lecteur .comms,html.lecteur .form-comms,html.lecteur #btn-haut{display:none!important}
html.lecteur .wrap{max-width:680px;padding-top:26px}
html.lecteur .carte{border:none;box-shadow:none;background:transparent}
html.lecteur .meta-pub{opacity:.7}
html.lecteur #btn-sortir-lecteur{position:fixed;bottom:20px;right:18px;z-index:70;background:var(--or);color:#191307;border:none;border-radius:999px;padding:12px 16px;font-weight:700;cursor:pointer;box-shadow:0 4px 14px var(--ombre)}
/* ===== POST ITINÉRANT + PALETTE DE COMMANDES ===== */
#fab-mundo{position:fixed;z-index:80;right:18px;bottom:78px;width:56px;height:56px;border-radius:50%;background:var(--or);color:#191307;border:none;font-size:24px;cursor:grab;box-shadow:0 6px 18px var(--ombre);touch-action:none;user-select:none;-webkit-user-select:none}
#fab-mundo:active{cursor:grabbing;transform:scale(.94)}
#fab-menu{position:fixed;z-index:80;display:none;flex-direction:column;gap:4px;background:var(--carte);border:1px solid var(--or);border-radius:14px;padding:10px;box-shadow:0 8px 26px var(--ombre);min-width:230px}
#fab-menu.ouvert{display:flex}
#fab-menu button{display:flex;gap:8px;align-items:center;background:transparent;border:none;color:var(--texte);padding:10px 12px;border-radius:9px;cursor:pointer;text-align:left;font-weight:600;min-height:44px}
#fab-menu button:hover{background:rgba(200,162,74,.18)}
#palette{position:fixed;inset:0;z-index:100;display:none;background:rgba(0,0,0,.55);backdrop-filter:blur(2px)}
#palette.ouvert{display:block}
#palette .boite{max-width:560px;margin:12vh auto 0;background:var(--carte);border:1px solid var(--or);border-radius:14px;box-shadow:0 18px 50px var(--ombre);overflow:hidden}
#palette input{width:100%;border:none;border-bottom:1px solid var(--bord);border-radius:0;padding:14px 16px;background:var(--champ);font-size:16px}
#palette ul{list-style:none;margin:0;padding:6px;max-height:50vh;overflow:auto}
#palette li{padding:10px 12px;border-radius:9px;cursor:pointer;display:flex;gap:10px;align-items:center;justify-content:space-between}
#palette li.actif,#palette li:hover{background:rgba(200,162,74,.2)}
#palette li .touche{font-size:11px;color:var(--muet);border:1px solid var(--bord);border-radius:5px;padding:1px 6px}
/* ===== DIVERS V3 ===== */
#barre-lecture{position:fixed;top:0;left:0;height:3px;width:0;background:var(--or);z-index:90;transition:width .1s linear}
.toc{border:1px dashed var(--bord);border-radius:10px;padding:10px 14px;margin-bottom:14px}
.toc b{color:var(--or)}
.toc a{display:inline-block;margin:2px 10px 2px 0}
.reveal{opacity:0;transform:translateY(8px);transition:opacity .35s ease,transform .35s ease}
.reveal.vu{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){.reveal{opacity:1;transform:none;transition:none}}
#btn-theme{background:#20293b;color:var(--texte);border:1px solid var(--bord);border-radius:999px;min-height:40px;min-width:44px;padding:0 12px;font-size:16px;cursor:pointer;transition:transform .12s ease}
#btn-theme:active{transform:scale(.94)}
html[data-theme=clair] #btn-theme{background:#fff}
.toast-copie{position:fixed;bottom:26px;left:50%;transform:translateX(-50%) translateY(18px);opacity:0;z-index:99;background:var(--or);color:#191307;font-weight:700;border-radius:999px;padding:10px 18px;box-shadow:0 6px 18px var(--ombre);transition:opacity .25s ease,transform .25s ease;pointer-events:none;max-width:90vw;text-align:center}
.toast-copie.visible{opacity:1;transform:translateX(-50%) translateY(0)}
#btn-haut{position:fixed;bottom:20px;right:18px;z-index:55;background:var(--or);color:#191307;border:none;border-radius:999px;width:48px;height:48px;font-size:20px;cursor:pointer;box-shadow:0 4px 14px var(--ombre);opacity:0;visibility:hidden;transition:opacity .25s ease,visibility .25s ease}
#btn-haut.visible{opacity:1;visibility:visible}
/* ===== V4 : actions par mot, hors-ligne, sons ===== */
#menu-mot{position:fixed;z-index:95;display:none;gap:2px;background:var(--carte);border:1px solid var(--or);border-radius:999px;padding:4px 8px;box-shadow:0 8px 22px var(--ombre);flex-wrap:wrap;max-width:92vw;align-items:center}
#menu-mot.ouvert{display:flex}
#menu-mot button{background:transparent;border:none;cursor:pointer;border-radius:999px;padding:6px 10px;color:var(--texte);min-height:38px;min-width:38px;font-size:15px}
#menu-mot button:hover{background:rgba(200,162,74,.2)}
#menu-mot .mot-lu{color:var(--or);font-weight:800;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-right:4px}
mark.surligne{background:rgba(255,214,0,.55);color:inherit;border-radius:3px;padding:0 2px}
html[data-theme=clair] mark.surligne{background:rgba(255,196,0,.5)}
#bandeau-horsligne{position:fixed;top:0;left:0;right:0;z-index:98;display:none;background:#8a5a00;color:#fff;text-align:center;padding:7px 10px;font-weight:700;font-size:13px}
#bandeau-horsligne.visible{display:block}
@media print{.header,#btn-haut,#btn-theme,.flotant{display:none!important}body{background:#fff;color:#000}}
/* ===== V8 — CLIC EXPLOITABLE PARTOUT + FLUIDITÉ ===== */
/* ===== V8b — CLICABILITÉ EXTRÊME : tout l'écran devient une commande ===== */
h1,h2,h3,p,li,td,th,blockquote,footer span,small,strong,em{transition:color .15s ease}
/* 1) Cartes et blocs : ordre du jour — le survol confirme que TOUT est interactif */
.carte{cursor:pointer}
.carte:hover{box-shadow:0 4px 18px rgba(0,0,0,.3)}
/* 2) Grille de KPI (dont nouveaux soldes par application) : grand cible tactile */
.kpi .case{min-height:64px}
.kpi .case:hover,.kpi .case:focus-visible{border-color:var(--or);box-shadow:0 0 0 2px rgba(200,162,74,.35);transform:translateY(-2px)}
/* 3) Lignes de tableaux : cible généreuse + indicateur visuel net */
tbody tr{min-height:44px}
tbody tr:hover{background:rgba(200,162,74,.12);box-shadow:inset 3px 0 0 var(--or)}
/* 4) Listes, pied de page, titres : sélection/clic volontaires partout */
#palette li:active{transform:scale(.98)}
#fab-menu button:active,.menu-lateral a:active{transform:scale(.98)}
footer a{padding:6px 4px;min-height:32px;display:inline-block}
/* 5) Focus clavier visible sur N'IMPORTE quel élément rendu cliquable */
[role=button]{cursor:pointer}
[role=button]:focus-visible{outline:2px solid var(--or);outline-offset:2px;border-radius:6px}
[role=button]:active{transform:scale(.985)}
/* 6) Titres de cartes : clic = copie du titre (créé dynamiquement) ;
      les cartes déjà interactives restent prioritaires — rien n'est cassé. */
@media (prefers-reduced-motion:reduce){.carte,.kpi .case,tbody tr,[role=button]{transition:none;transform:none!important}}
.carte,.tuile,.msg,.comm,.kpi .case,.mm,.badge,.pastille,.moi-badge{transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease,filter .16s ease}
.carte:hover,.tuile:hover,.msg:hover,.comm:hover,.kpi .case:hover,.mm:hover{border-color:var(--or)}
.tuile,.kpi .case,.msg,.comm,.badge,.pastille,.moi-badge,.enligne,.horsligne,.tel{cursor:pointer}
.tuile:active,.kpi .case:active,.msg:active,.comm:active,.badge:active{transform:scale(.985)}
tbody tr{cursor:pointer;transition:background .15s ease}
tbody tr:hover{background:rgba(200,162,74,.07)}
button,.btn,.btn-sec,.btn-vert,.btn-rouge,.tuile{position:relative;overflow:hidden}
.onde{position:absolute;border-radius:50%;background:rgba(200,162,74,.5);transform:scale(0);animation:tbm-onde .55s ease-out forwards;pointer-events:none}
@keyframes tbm-onde{to{transform:scale(2.8);opacity:0}}
.btn-active{background:var(--or)!important;color:#191307!important;border-color:var(--or)!important}
.squelette{background:linear-gradient(90deg,#1a2130 25%,#232c40 50%,#1a2130 75%);background-size:200% 100%;animation:tbm-brille 1.2s infinite;border-radius:8px;min-height:14px}
@keyframes tbm-brille{0%{background-position:200% 0}100%{background-position:-200% 0}}
html[data-theme=clair] .squelette{background:linear-gradient(90deg,#e7e2d4 25%,#f3efe3 50%,#e7e2d4 75%);background-size:200% 100%}
html[data-theme=sepia] .squelette{background:linear-gradient(90deg,#e7d9b8 25%,#f2e7cd 50%,#e7d9b8 75%);background-size:200% 100%}
.ligne-histo{cursor:pointer}
.ligne-histo:hover{border-color:var(--or)}
@media (prefers-reduced-motion:reduce){.onde,.squelette{animation:none;display:none}.carte,.tuile,.msg,.comm,tbody tr,.badge{transition:none}}
</style>
<meta name="color-scheme" content="dark light">
<meta name="description" content="ToutBot Mundo — réseau social 100 % textuel avec portefeuille virtuel, abonnements payants et administration intégrée.">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#c8a24a">
<script>(function(){var h=document.documentElement;try{var t=localStorage.getItem('tbm-theme');if(!t){t=h.classList.contains('theme-clair')?'clair':(h.classList.contains('theme-sepia')?'sepia':(window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches?'clair':'sombre'));}h.setAttribute('data-theme',t);if(localStorage.getItem('tbm-lecteur')==='1'){h.classList.add('lecteur');}}catch(e){h.setAttribute('data-theme','sombre');}})();</script>
</head><body>
<div class="header">
  <span class="logo">🌍 ToutBot Mundo</span>
  {% if me %}<span class="moi-badge" title="Votre surnom unique">👤 {{ me['pseudo'] }}</span>{% endif %}
  <nav>
    {% if me %}
      <a href="{{ url_for('fil') }}">Accueil</a>
      <a href="{{ url_for('tarifs') }}">Tarifs</a>
      <a href="{{ url_for('portefeuille') }}">Portefeuille</a>
      <a href="{{ url_for('messages') }}">Messages{% if non_lus_total %}<span class="pastille">{{ non_lus_total }}</span>{% endif %}</a>
      <a href="{{ url_for('mon_historique') }}" title="Mon historique d'activité (touche H)" aria-label="Mon historique d'activité">🕓</a>
      <a href="{{ url_for('plaintes') }}">Plaintes</a>
      <a href="{{ url_for('recherche_page') }}">Recherche</a>
      <a href="{{ url_for('chat_page') }}">IA</a>
      <a href="{{ url_for('parametres') }}">Paramètres</a>
      <a href="{{ url_for('statistiques') }}">Statistiques</a>
      {% if me['est_admin'] %}<a href="{{ url_for('admin') }}"><b>Admin</b></a><a href="{{ url_for('admin_envois') }}"><b>Envoi groupé</b></a><a href="{{ url_for('admin_journal') }}"><b>Journal</b></a>{% endif %}
      <a href="{{ url_for('profil', pseudo=me['pseudo']) }}">{{ me['pseudo'] }}</a>
      <form method="post" action="{{ url_for('deconnexion') }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Quitter</button>
      </form>
    {% else %}
      <a href="{{ url_for('tarifs') }}">Tarifs</a>
      <a href="{{ url_for('recherche_page') }}">Recherche</a>
      <a href="{{ url_for('cgu') }}">CGU</a>
      <a href="{{ url_for('confidentialite') }}">Confidentialité</a>
      <a href="{{ url_for('connexion') }}">Connexion</a>
      <a href="{{ url_for('inscription') }}">Inscription</a>
    {% endif %}
  </nav>
  <button id="btn-theme" type="button" onclick="basculer_theme(this)" aria-label="Basculer entre thème sombre et clair" title="Thème clair / sombre (touche T)">🌗</button>
  <button id="btn-son" type="button" onclick="basculer_son()" aria-label="Activer ou couper les notifications sonores" title="Notifications sonores">🔔</button>
</div>
<div class="wrap">
  {% if flash %}<div class="flash">{{ flash }}</div>{% endif %}{% if sanction_banniere %}<div class="annonce">{{ sanction_banniere }}</div>{% endif %}
  {# Bandeau d'en-tête : affiché uniquement aux utilisateurs connectés (inscription terminée) #}
  {% if me %}<div class="annonce">📢 {{ annonce }}</div>{% endif %}
  {% block contenu %}{% endblock %}
</div>
<footer>ToutBot Mundo — réseau social textuel · Commission {{ commission_pct|int }}% (plancher {{ commission_plancher|int }} {{ devise }}) · mode « {{ mode_commission }} » · <a href="{{ url_for('tarifs') }}">Tarifs et paiements</a> · <a href="{{ url_for('cgu') }}">Conditions d'utilisation</a> · <a href="{{ url_for('confidentialite') }}">Politique de confidentialité</a></footer>
<script>
function copier(btn){
  var champ = btn.parentElement.querySelector('input');
  var texte = champ ? champ.value : '';
  function fait(){ var ancien = btn.textContent; btn.textContent = 'Copié ✓';
    setTimeout(function(){ btn.textContent = ancien; }, 1600); }
  function manuel(){ if (champ) { champ.select(); try { document.execCommand('copy'); fait(); } catch (e) {} } }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(texte).then(fait, manuel);
  } else { manuel(); }
}
function copier_zone(btn){
  var zone = btn.parentElement.querySelector('textarea');
  var texte = zone ? zone.value : '';
  function fait(){ var ancien = btn.textContent; btn.textContent = 'Copié ✓';
    setTimeout(function(){ btn.textContent = ancien; }, 1600); }
  function manuel(){ if (zone) { zone.select(); try { document.execCommand('copy'); fait(); } catch (e) {} } }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(texte).then(fait, manuel);
  } else { manuel(); }
}
</script>
<script>
// ===== GLOBAL : copie partout, retour tactile, sélectivité totale =====
function toast(msg){
  var t=document.getElementById('toast-copie');
  if(!t){t=document.createElement('div');t.id='toast-copie';t.className='toast-copie';t.setAttribute('role','status');t.setAttribute('aria-live','polite');document.body.appendChild(t);}
  t.textContent=msg;t.classList.add('visible');
  clearTimeout(t._minuterie);t._minuterie=setTimeout(function(){t.classList.remove('visible');},2200);
}
function basculer_theme(btn){
  var suivant=document.documentElement.getAttribute('data-theme')==='clair'?'sombre':'clair';
  document.documentElement.setAttribute('data-theme',suivant);
  try{localStorage.setItem('tbm-theme',suivant);}catch(e){}
  if(btn)btn.textContent=suivant==='clair'?'🌙':'☀️';
  toast(suivant==='clair'?'Thème clair activé':'Thème sombre activé');
}
(function(){var b=document.getElementById('btn-theme');if(b){b.textContent=document.documentElement.getAttribute('data-theme')==='clair'?'🌙':'☀️';}})();
// ===== COPIER TOUT LE FIL de la discussion (ordre chronologique, avec noms et heures) =====
function lignes_du_fil(){
  var lignes=[];
  document.querySelectorAll('.msg').forEach(function(m){
    var qui=m.querySelector('b')?m.querySelector('b').textContent:'?';
    var quand=m.querySelector('.muet')?m.querySelector('.muet').textContent:'';
    var corps=m.querySelector('div')?m.querySelector('div').textContent:'';
    lignes.push('['+quand+'] '+qui+' : '+corps);
  });
  return lignes;
}
function copier_fil(btn){
  var lignes=lignes_du_fil();
  if(!lignes.length){toast('Aucun message à copier');return;}
  copier_texte('— Fil de discussion ToutBot Mundo —\n\n'+lignes.join('\n\n'),btn);
  toast('Fil copié ('+lignes.length+' messages) ✓');
}
function exporter_fil_md(){
  var lignes=lignes_du_fil();
  if(!lignes.length){toast('Aucun message à exporter');return;}
  var titre=document.querySelector('.carte h2');
  var md=['# '+(titre?titre.textContent:'Fil de discussion'),'','_Exporté de ToutBot Mundo le '+new Date().toLocaleString('fr-FR')+'_',''];
  lignes.forEach(function(l){md.push(l,'','---','');});
  var blob=new Blob([md.join('\n')],{type:'text/markdown;charset=utf-8'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='discussion-toutbot.md';a.click();
  setTimeout(function(){URL.revokeObjectURL(a.href);},3000);
  toast('Export Markdown téléchargé ✓');
}
function copier_texte(texte, btn){
  function fait(){ if(btn){var a=btn.textContent;btn.textContent='Copié ✓';setTimeout(function(){btn.textContent=a;},1600);} toast('Copié ✓'); }
  function manuel(){ var t=document.createElement('textarea');t.value=texte;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();try{document.execCommand('copy');fait();}catch(e){}document.body.removeChild(t); }
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(texte).then(fait,manuel);}else{manuel();}
}
// Les champs en lecture seule (numéros, modèles) sélectionnent tout au focus — prêts à copier
document.addEventListener('focusin',function(e){var el=e.target;
  if((el.tagName==='INPUT'||el.tagName==='TEXTAREA')&&el.readOnly){el.select();}});
// Un clic simple sur une étiquette, un badge ou un message le copie (si aucun texte n'est sélectionné)
document.addEventListener('click',function(e){
  if(window.getSelection().toString())return;
  var el=e.target.closest('.badge,.pastille,.muet,.tel,.annonce,.flash,.msg,.comm .corps,.meta-pub span,.enligne,.horsligne');
  if(el&&el.textContent.trim().length>0){
    copier_texte(el.textContent.trim(),null);
    el.style.transition='box-shadow .2s ease';el.style.boxShadow='0 0 0 2px var(--or)';
    setTimeout(function(){el.style.boxShadow='';},600);
  }
});
// Double clic sur un bloc (message, réponse IA, paragraphe) = copie du bloc entier
document.addEventListener('dblclick',function(e){
  var el=e.target.closest('.msg,.ia-reponse,.carte p');
  if(el){copier_texte(el.value!==undefined?el.value:el.textContent.trim(),null);}
});
// Les panneaux .flotant sont ATTRIRABLES : on glisse la poignée pour les déplacer
(function(){
  function rendu_atirable(p){
    if(p.dataset.atirable)return;p.dataset.atirable='1';
    var poignee=p.querySelector('.poignee');
    if(!poignee){poignee=document.createElement('div');poignee.className='poignee';poignee.textContent='⠿ Glissez pour déplacer';p.insertBefore(poignee,p.firstChild);}
    var dx=0,dy=0,ox=0,oy=0,actif=false;
    poignee.addEventListener('pointerdown',function(e){actif=true;ox=e.clientX-dx;oy=e.clientY-dy;poignee.style.cursor='grabbing';
      try{poignee.setPointerCapture(e.pointerId);}catch(err){}e.preventDefault();});
    poignee.addEventListener('pointermove',function(e){if(!actif)return;e.preventDefault();
      dx=e.clientX-ox;dy=e.clientY-oy;p.style.transform='translate('+dx+'px,'+dy+'px)';});
    poignee.addEventListener('pointerup',function(){actif=false;poignee.style.cursor='grab';});
    poignee.addEventListener('pointercancel',function(){actif=false;poignee.style.cursor='grab';});
  }
  function balayer(){document.querySelectorAll('.flotant').forEach(rendu_atirable);}
  if(document.readyState!=='loading'){balayer();}else{document.addEventListener('DOMContentLoaded',balayer);}
  new MutationObserver(balayer).observe(document.documentElement,{childList:true,subtree:true});
})();
// ===== RACCOURCIS CLAVIER : / = composer · T = thème · Ctrl+Entrée = envoyer =====
document.addEventListener('keydown',function(e){
  if(e.ctrlKey||e.metaKey){
    if(e.key==='Enter'&&e.target.tagName==='TEXTAREA'&&e.target.form){e.preventDefault();e.target.form.submit();}
    return;
  }
  if(e.target.matches('input,textarea,select')||e.target.isContentEditable)return;
  if(e.key==='/'){var z=document.querySelector('textarea[name=corps]');if(z){e.preventDefault();z.focus();}}
  else if(e.key==='t'||e.key==='T'){cycle_theme();}
  else if(e.key==='r'||e.key==='R'){basculer_lecteur();}
  else if(e.key==='?'){palette.ouvrir();}
  else if(e.key==='p'||e.key==='P'){window.print();}
  else if(e.key==='d'||e.key==='D'){var z2=document.querySelector('textarea[name=corps]');if(z2){z2.focus();z2.scrollIntoView({behavior:'smooth',block:'center'});}}
  else if(e.key==='m'||e.key==='M'){var lm=document.querySelector('nav a[href*="messages"]');if(lm)lm.click();}
  else if(e.key==='w'||e.key==='W'){var lw=document.querySelector('nav a[href*="portefeuille"]');if(lw)lw.click();}
  else if(e.key==='h'||e.key==='H'){location.href='/mon-historique';}
});
document.addEventListener('keydown',function(e){
  if((e.ctrlKey||e.metaKey)&&(e.key==='k'||e.key==='K')){e.preventDefault();palette.ouvrir();}
});
// ===== TEXTAREAS À HAUTEUR AUTOMATIQUE =====
function _auto(t){t.style.height='auto';t.style.height=Math.min(t.scrollHeight,340)+'px';}
document.querySelectorAll('textarea').forEach(function(t){t.style.overflow='hidden';t.addEventListener('input',function(){_auto(t);});_auto(t);});
// ===== BOUTON RETOUR EN HAUT =====
var bh=document.createElement('button');bh.id='btn-haut';bh.type='button';bh.textContent='↑';
bh.title='Remonter en haut';bh.setAttribute('aria-label','Remonter en haut de la page');
bh.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
document.body.appendChild(bh);
window.addEventListener('scroll',function(){bh.classList.toggle('visible',window.scrollY>600);},{passive:true});
// ======================= NOUVEAUTÉS V3 =======================
function cycle_theme(){
  var ordre=['sombre','clair','sepia'];
  var actuel=document.documentElement.getAttribute('data-theme')||'sombre';
  var suivant=ordre[(ordre.indexOf(actuel)+1)%3];
  document.documentElement.setAttribute('data-theme',suivant);
  try{localStorage.setItem('tbm-theme',suivant);}catch(e){}
  var b=document.getElementById('btn-theme');
  if(b)b.textContent=suivant==='sombre'?'🌗':(suivant==='clair'?'🌙':'📜');
  toast('Thème : '+(suivant==='sombre'?'sombre':(suivant==='clair'?'clair':'sépia')));
}
function basculer_lecteur(){
  var h=document.documentElement,on=h.classList.toggle('lecteur');
  try{localStorage.setItem('tbm-lecteur',on?'1':'0');}catch(e){}
  var b=document.getElementById('btn-sortir-lecteur');
  if(on&&!b){b=document.createElement('button');b.id='btn-sortir-lecteur';b.type='button';
    b.textContent='✕ Quitter le mode lecteur';b.addEventListener('click',basculer_lecteur);
    document.body.appendChild(b);}
  if(!on&&b)b.remove();
  toast(on?'Mode lecteur activé — touche R pour quitter':'Mode lecteur désactivé');
}
var COMMANDES=[
  {icone:'🏠',nom:'Accueil',action:function(){location.href='/';}},
  {icone:'🕓',nom:'Mon historique d\u2019activité',touche:'H',action:function(){location.href='/mon-historique';}},
  {icone:'💬',nom:'Messages',touche:'M',action:function(){var l=document.querySelector('nav a[href*="messages"]');if(l)l.click();else location.href='/messages';}},
  {icone:'💰',nom:'Portefeuille',touche:'W',action:function(){var l=document.querySelector('nav a[href*="portefeuille"]');if(l)l.click();else location.href='/portefeuille';}},
  {icone:'🔎',nom:'Recherche',action:function(){location.href='/recherche';}},
  {icone:'📈',nom:'Statistiques',action:function(){location.href='/statistiques';}},
  {icone:'⚙️',nom:'Paramètres',action:function(){location.href='/parametres';}},
  {icone:'🌱',nom:'Thème suivant (sombre → clair → sépia)',touche:'T',action:cycle_theme},
  {icone:'📖',nom:'Mode lecteur',touche:'R',action:basculer_lecteur},
  {icone:'✍️',nom:'Composer une publication',touche:'D',action:function(){var z=document.querySelector('textarea[name=corps]');if(z){z.focus();z.scrollIntoView({behavior:'smooth',block:'center'});}}},
  {icone:'📋',nom:'Copier tout le fil de la discussion',action:function(){var b=document.querySelector('button[onclick*=copier_fil]');if(b)b.click();}},
  {icone:'📄',nom:'Exporter le fil mondial (.md)',action:function(){location.href='/posts/export.md';}},
  {icone:'📊',nom:'Suivi des abonnements (.xlsx) — admin',admin:true,action:function(){location.href='/admin/suivi.xlsx';}},
  {icone:'🖨️',nom:'Imprimer la page',touche:'P',action:function(){window.print();}},
  {icone:'🧭',nom:'Aller au sommaire de la page',action:function(){var t=document.querySelector('.toc');if(t)t.scrollIntoView({behavior:'smooth'});}},
  {icone:'⬆️',nom:'Remonter en haut',action:function(){window.scrollTo({top:0,behavior:'smooth'});}}
];
var palette=(function(){
  var fond=document.createElement('div');fond.id='palette';
  fond.innerHTML='<div class="boite"><input id="palette-champ" type="text" placeholder="Tapez une commande… (? ou Ctrl+K pour ouvrir, Échap pour fermer)" autocomplete="off"><ul id="palette-liste"></ul></div>';
  document.body.appendChild(fond);
  var champ=fond.querySelector('input'),liste=fond.querySelector('ul'),actif=0,visibles=[];
  function rendre(){
    var q=champ.value.trim().toLowerCase();
    var admin=!!document.querySelector('nav a[href*="admin"]');
    visibles=COMMANDES.filter(function(c){return (!c.admin||admin)&&(!q||c.nom.toLowerCase().indexOf(q)>=0);});
    actif=0;
    liste.innerHTML=visibles.map(function(c,i){return '<li data-i="'+i+'"'+(i===0?' class="actif"':'')+'><span>'+c.icone+' '+c.nom+'</span>'+(c.touche?'<span class="touche">'+c.touche+'</span>':'')+'</li>';}).join('')||'<li class="muet">Aucune commande trouvée</li>';
  }
  function maj(){liste.querySelectorAll('li').forEach(function(li,i){li.classList.toggle('actif',i===actif);});}
  function executer(i){var c=visibles[i];if(c){fond.classList.remove('ouvert');setTimeout(c.action,60);}}
  champ.addEventListener('input',rendre);
  champ.addEventListener('keydown',function(e){
    if(e.key==='Escape'){fond.classList.remove('ouvert');e.preventDefault();}
    else if(e.key==='ArrowDown'){actif=Math.min(actif+1,visibles.length-1);maj();e.preventDefault();}
    else if(e.key==='ArrowUp'){actif=Math.max(actif-1,0);maj();e.preventDefault();}
    else if(e.key==='Enter'){executer(actif);e.preventDefault();}});
  liste.addEventListener('click',function(e){var li=e.target.closest('li[data-i]');if(li)executer(parseInt(li.dataset.i,10));});
  fond.addEventListener('click',function(e){if(e.target===fond)fond.classList.remove('ouvert');});
  return {ouvrir:function(){fond.classList.add('ouvert');champ.value='';rendre();setTimeout(function(){champ.focus();},30);}};
})();
// Le bouton d'en-tête passe en cycle sombre → clair → sépia
(function(){var b=document.getElementById('btn-theme');if(b){b.removeAttribute('onclick');b.addEventListener('click',cycle_theme);
  b.title='Thème : sombre → clair → sépia (touche T)';
  b.textContent=document.documentElement.getAttribute('data-theme')==='sombre'?'🌗':(document.documentElement.getAttribute('data-theme')==='clair'?'🌙':'📜');}})();
// --- POST ITINÉRANT : attrapable, déplaçable partout, menu d'actions rapides ---
(function(){
  var fab=document.createElement('button');fab.id='fab-mundo';fab.type='button';fab.textContent='🌍';
  fab.title='ToutBot Mundo — glissez-moi où vous voulez, touchez-moi pour le menu';
  fab.setAttribute('aria-label','Menu d\u2019actions rapides ToutBot Mundo');
  var menu=document.createElement('div');menu.id='fab-menu';
  document.body.appendChild(fab);document.body.appendChild(menu);
  try{var pos=JSON.parse(localStorage.getItem('tbm-fab')||'null');
    if(pos){fab.style.right='auto';fab.style.bottom='auto';
      fab.style.left=Math.max(0,Math.min(pos.x,innerWidth-60))+'px';
      fab.style.top=Math.max(0,Math.min(pos.y,innerHeight-60))+'px';}}catch(e){}
  function remplir(){
    var admin=!!document.querySelector('nav a[href*="admin"]');
    var actions=COMMANDES.filter(function(c){return !c.admin||admin;}).slice(0,9);
    menu.innerHTML=actions.map(function(c,i){return '<button type="button" data-i="'+i+'">'+c.icone+' '+c.nom+'</button>';}).join('')+
      '<button type="button" data-tout="1">⌘ Toutes les commandes…</button>';
  }
  fab.addEventListener('click',function(){
    if(fab.dataset.deplace==='1'){fab.dataset.deplace='0';return;}
    remplir();menu.classList.toggle('ouvert');
    var r=fab.getBoundingClientRect();
    menu.style.left=Math.max(4,Math.min(r.left-40,innerWidth-250))+'px';
    menu.style.top=Math.min(r.bottom+8,innerHeight-320)+'px';
  });
  menu.addEventListener('click',function(e){
    var b=e.target.closest('button');if(!b)return;
    menu.classList.remove('ouvert');
    if(b.dataset.tout){palette.ouvrir();return;}
    var c=COMMANDES[parseInt(b.dataset.i,10)];if(c)setTimeout(c.action,50);
  });
  document.addEventListener('click',function(e){if(!menu.contains(e.target)&&e.target!==fab)menu.classList.remove('ouvert');});
  var dx=0,dy=0,actif=false,bouge=false;
  fab.addEventListener('pointerdown',function(e){actif=true;bouge=false;dx=e.clientX-fab.offsetLeft;dy=e.clientY-fab.offsetTop;
    try{fab.setPointerCapture(e.pointerId);}catch(err){}e.preventDefault();});
  fab.addEventListener('pointermove',function(e){if(!actif)return;bouge=true;
    var x=Math.max(0,Math.min(e.clientX-dx,innerWidth-56)),y=Math.max(0,Math.min(e.clientY-dy,innerHeight-56));
    fab.style.left=x+'px';fab.style.top=y+'px';fab.style.right='auto';fab.style.bottom='auto';});
  fab.addEventListener('pointerup',function(){actif=false;
    if(bouge){fab.dataset.deplace='1';
      try{localStorage.setItem('tbm-fab',JSON.stringify({x:fab.offsetLeft,y:fab.offsetTop}));}catch(e){}}});
})();
// --- Barre de progression de lecture en haut de page ---
var bp=document.createElement('div');bp.id='barre-lecture';document.body.appendChild(bp);
window.addEventListener('scroll',function(){var h=document.documentElement,m=h.scrollHeight-h.clientHeight;
  bp.style.width=(m>0?(h.scrollTop/m*100):0)+'%';},{passive:true});
// --- Sommaire automatique des longues pages (3 sections ou plus) ---
(function(){var titres=document.querySelectorAll('.wrap h2');if(titres.length<3)return;
  var toc=document.createElement('div');toc.className='toc';
  toc.innerHTML='<b>🧭 Sommaire de la page</b><br>'+Array.prototype.map.call(titres,function(t,i){
    var id=t.id||('section-'+i);t.id=id;return '<a href="#'+id+'">'+t.textContent+'</a>';}).join('');
  var cible=document.querySelector('.wrap');if(cible)cible.insertBefore(toc,cible.firstChild);})();
// --- Apparition douce des cartes au défilement ---
if('IntersectionObserver' in window){var obs=new IntersectionObserver(function(es){
  es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('vu');obs.unobserve(e.target);}});},{threshold:.06});
  document.querySelectorAll('.carte').forEach(function(c,i){if(i>2){c.classList.add('reveal');obs.observe(c);}});}
// --- Liens cliquables automatiques dans les publications et commentaires ---
(function(){var rx=new RegExp('https?://[^\\s<>"]+','g');
  function ech(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  document.querySelectorAll('.selec,.comm .corps').forEach(function(el){
    if(el.dataset.lievifie)return;el.dataset.lievifie='1';
    if(!rx.test(el.textContent))return;rx.lastIndex=0;
    el.innerHTML=ech(el.textContent).replace(rx,function(u){
      return '<a href="'+u+'" target="_blank" rel="noopener noreferrer">'+u+'</a>';});});})();
// --- Compteur de caractères + garde anti-perte sur le composeur ---
(function(){var z=document.querySelector('textarea[name=corps]');if(!z)return;
  var c=document.createElement('span');c.className='muet';c.style.marginLeft='8px';
  (z.closest('form')||z.parentElement).appendChild(c);
  function maj(){c.textContent=z.value.length+' / 5000 caractères';}
  z.addEventListener('input',maj);maj();
  window.addEventListener('beforeunload',function(e){
    if(z.value.trim().length>10){e.preventDefault();e.returnValue='';}});})();
// --- Bouton « Copier tout le fil » sur le fil d'accueil ---
(function(){var f=document.querySelector('form[action*="publier"]');if(!f)return;
  var b=document.createElement('button');b.type='button';b.className='btn-sec';b.textContent='📋 Copier tout le fil';
  b.addEventListener('click',function(){
    var lignes=[];document.querySelectorAll('.carte .meta-pub').forEach(function(mp){
      var carte=mp.closest('.carte');var corps=carte.querySelector('.selec');if(!corps)return;
      lignes.push(mp.textContent.replace(new RegExp('\\s+','g'),' ').trim()+'\n'+corps.textContent.trim());});
    if(!lignes.length){toast('Aucune publication à copier');return;}
    copier_texte('— Fil mondial ToutBot Mundo —\n\n'+lignes.join('\n———\n\n'),b);
    toast('Fil copié ('+lignes.length+' publications) ✓');});
  var r=f.querySelector('.row');if(r)r.appendChild(b);})();
// --- Clignotement du titre de l'onglet si messages non lus ---
(function(){var p=document.querySelector('.header .pastille');if(!p)return;
  var base=document.title,vu=false;
  setInterval(function(){vu=!vu;document.title=vu?'('+p.textContent+') Nouveau message — ToutBot Mundo':base;},1500);})();
// ======================= NOUVEAUTÉS V4 =======================
// 1) NOTIFICATIONS SONORES — bips WebAudio (aucun fichier externe) + sourdine
var _audio=null;
function bip(f1,f2,duree){try{_audio=_audio||new (window.AudioContext||window.webkitAudioContext)();
  var o=_audio.createOscillator(),g=_audio.createGain();o.type='sine';o.frequency.value=f1;
  if(f2)o.frequency.linearRampToValueAtTime(f2,_audio.currentTime+duree);
  g.gain.value=.12;o.connect(g);g.connect(_audio.destination);o.start();o.stop(_audio.currentTime+duree);}catch(e){}}
function sonner(type){
  try{if(localStorage.getItem('tbm-son')==='0')return;}catch(e){}
  if(type==='message'){bip(660,880,.18);setTimeout(function(){bip(880,1150,.16);},190);}
  else if(type==='copie'){bip(540,720,.12);}
  else{bip(440,440,.1);}
}
function basculer_son(){
  var off=false;try{off=localStorage.getItem('tbm-son')==='0';localStorage.setItem('tbm-son',off?'1':'0');}catch(e){}
  maj_btn_son();toast(off?'Notifications sonores activées 🔔':'Notifications sonores coupées 🔇');
  if(off)sonner('test');
}
function maj_btn_son(){var b=document.getElementById('btn-son');var off=false;
  try{off=localStorage.getItem('tbm-son')==='0';}catch(e){}
  if(b){b.textContent=off?'🔇':'🔔';b.title=off?'Sons coupés — cliquer pour activer':'Sons activés — cliquer pour couper';}}
(function(){var _toast=toast;toast=function(m){_toast(m);if(String(m).indexOf('Copié')>=0)sonner('copie');};maj_btn_son();})();
// 2) VEILLE DES MESSAGES — bip + pastille dès qu'un nouveau message arrive
var _dernier_non_lus=null;
function veiller_messages(){
  if(!document.querySelector('nav a[href*="messages"]'))return;
  fetch('/api/non_lus').then(function(r){return r.json();}).then(function(d){
    if(d.connecte&&_dernier_non_lus!==null&&d.n>_dernier_non_lus){sonner('message');toast('💬 Nouveau message reçu');}
    _dernier_non_lus=d.n;}).catch(function(){});
}
setTimeout(veiller_messages,1200);setInterval(veiller_messages,45000);
// 3) MODE HORS-LIGNE — bannière + service worker + cache automatique
(function(){var b=document.createElement('div');b.id='bandeau-horsligne';
  b.setAttribute('role','status');b.textContent='⚡ Hors ligne — affichage de la dernière version enregistrée. La connexion revient dès que le réseau est là.';
  document.body.appendChild(b);
  function maj(){if(!navigator.onLine)b.classList.add('visible');else b.classList.remove('visible');}
  window.addEventListener('online',function(){maj();toast('Connexion rétablie ✓');});
  window.addEventListener('offline',maj);maj();
  if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){});}})();
// 4) MENU D'ACTIONS PAR MOT — chaque lettre, chaque mot est prenable partout
(function(){
  var menu=document.createElement('div');menu.id='menu-mot';
  menu.innerHTML='<span class="mot-lu"></span>'+
    '<button type="button" data-a="lire" title="Lire le mot à voix haute" aria-label="Lire le mot">🔊</button>'+
    '<button type="button" data-a="copier" title="Copier le mot" aria-label="Copier le mot">📋</button>'+
    '<button type="button" data-a="definir" title="Définition (Wiktionnaire)" aria-label="Définir">📖</button>'+
    '<button type="button" data-a="surligner" title="Surligner" aria-label="Surligner">🖍️</button>'+
    '<button type="button" data-a="fermer" title="Fermer" aria-label="Fermer">✖</button>';
  document.body.appendChild(menu);
  var mot='',plage=null;
  function cacher(){menu.classList.remove('ouvert');}
  function trouver_mot(x,y){
    var r=null;
    try{
      if(document.caretRangeFromPoint){r=document.caretRangeFromPoint(x,y);if(r&&r.expand)r.expand('word');}
      else if(document.caretPositionFromPoint){var p=document.caretPositionFromPoint(x,y);
        if(p){r=document.createRange();r.setStart(p.offsetNode,p.offset);r.expand('word');}}
    }catch(e){return null;}
    var t=r?r.toString().trim():'';
    return (t&&/^[\\p{L}\\p{N}][\\p{L}\\p{N}'’-]*$/u.test(t))?{mot:t,plage:r}:null;
  }
  document.addEventListener('click',function(e){
    if(menu.contains(e.target))return;
    cacher();
    if(e.target.closest('button,a,input,textarea,select,label,#fab-mundo,#palette,#menu-mot,.toc'))return;
    if(window.getSelection().toString().length>1)return;
    var trouve=trouver_mot(e.clientX,e.clientY);
    if(!trouve)return;
    mot=trouve.mot;plage=trouve.plage;
    menu.querySelector('.mot-lu').textContent='« '+mot+' »';
    menu.classList.add('ouvert');
    menu.style.left=Math.max(4,Math.min(e.clientX,innerWidth-menu.offsetWidth-8))+'px';
    menu.style.top=Math.max(4,Math.min(e.clientY+14,innerHeight-menu.offsetHeight-8))+'px';
    e.stopPropagation();e.preventDefault();
  },true);
  menu.addEventListener('click',function(e){
    var b=e.target.closest('button');if(!b)return;
    var a=b.dataset.a;
    if(a==='lire'){try{var u=new SpeechSynthesisUtterance(mot);u.lang='fr-FR';speechSynthesis.cancel();speechSynthesis.speak(u);}catch(err){}sonner('test');}
    else if(a==='copier'){copier_texte(mot,null);toast('Copié ✓ « '+mot+' »');}
    else if(a==='definir'){window.open('https://fr.wiktionary.org/wiki/'+encodeURIComponent(mot),'_blank','noopener');}
    else if(a==='surligner'){if(plage){try{var mk=document.createElement('mark');mk.className='surligne';plage.surroundContents(mk);}catch(err){toast('Surlignage impossible ici');}}}
    cacher();
  });
  document.addEventListener('keydown',function(e){if(e.key==='Escape')cacher();});
  window.addEventListener('scroll',cacher,{passive:true});
})();
// 5) Nouvelles commandes dans la palette et le menu du post itinérant
COMMANDES.push(
  {icone:'📜',nom:'Journal d\u2019activité (admin)',admin:true,action:function(){location.href='/admin/journal';}},
  {icone:'📜',nom:'Exporter le journal (.csv)',admin:true,action:function(){location.href='/admin/journal.csv';}},
  {icone:'🔔',nom:'Activer / couper les notifications sonores',action:basculer_son},
  {icone:'📖',nom:'Définir un mot (Wiktionnaire)',action:function(){var m=prompt('Quel mot définir ?');if(m)window.open('https://fr.wiktionary.org/wiki/'+encodeURIComponent(m),'_blank','noopener');}}
);
</script>
<script>
// ===== V8 : chaque élément visible devient une commande réelle =====
(function(){
function onde(e){
  var b=e.target.closest('button,.btn,.btn-sec,.btn-vert,.btn-rouge,.tuile');
  if(!b||b.id==='fab-mundo')return;
  var r=b.getBoundingClientRect(),s=document.createElement('span');s.className='onde';
  var d=Math.max(r.width,r.height);
  s.style.width=s.style.height=d+'px';
  s.style.left=(e.clientX-r.left-d/2)+'px';s.style.top=(e.clientY-r.top-d/2)+'px';
  b.appendChild(s);setTimeout(function(){s.remove();},600);
}
document.addEventListener('pointerdown',onde,true);
function activer(){
  // 1) Tuiles et cases de chiffres : clic ou Entrée = copie de la valeur
  document.querySelectorAll('.tuile,.kpi .case').forEach(function(el){
    if(el.dataset.v8c||el.tagName==='LABEL')return;el.dataset.v8c='1';
    el.setAttribute('tabindex','0');el.setAttribute('role','button');
    var t=(el.querySelector('b')?el.querySelector('b').textContent.trim()+' — ':'')+el.textContent.trim().replace(/\s+/g,' ');
    el.title='Cliquez pour copier : '+t.slice(0,90);
    function agir(){copier_texte(t,null);toast('Copié ✓');}
    el.addEventListener('click',function(e){if(e.target.closest('a,button,form,input,select,textarea,label'))return;agir();});
    el.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();agir();}});
  });
  // 2) Lignes de tableaux : clic = copie de la ligne entière
  document.querySelectorAll('tbody tr').forEach(function(tr){
    if(tr.dataset.v8c)return;tr.dataset.v8c='1';tr.title='Cliquez pour copier cette ligne';
    tr.addEventListener('click',function(e){
      if(e.target.closest('a,button,form,input,select,textarea'))return;
      copier_texte(Array.prototype.map.call(tr.children,function(c){return c.textContent.trim();}).join(' — '),null);
      toast('Ligne copiée ✓');
    });
  });
  // 3) Badges et étiquettes : accessibles au clavier (Entrée / Espace = copie)
  document.querySelectorAll('.badge,.pastille,.moi-badge,.enligne,.horsligne').forEach(function(el){
    if(el.dataset.v8c)return;el.dataset.v8c='1';el.setAttribute('tabindex','0');
    el.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){e.preventDefault();copier_texte(el.textContent.trim(),null);toast('Copié ✓');}
    });
  });
  // 4) Double-clic sur un paragraphe = copie du paragraphe
  if(!document._v8dbl){document._v8dbl=1;document.addEventListener('dblclick',function(e){
    var p=e.target.closest('p,.msg,.comm .corps');
    if(!p||window.getSelection().toString())return;
    copier_texte(p.textContent.trim(),null);toast('Paragraphe copié ✓');
  });}
  // 5) V8b — CLICABILITÉ EXTRÊME : titres de cartes, items de liste, pied de page
  document.querySelectorAll('.carte > h1,.carte > h2,.carte > h3').forEach(function(t){
    if(t.dataset.v8c)return;t.dataset.v8c='1';
    t.setAttribute('tabindex','0');t.setAttribute('role','button');
    t.title='Cliquez pour copier le titre : '+t.textContent.trim().slice(0,80);
    function cop(){copier_texte(t.textContent.trim(),null);toast('Titre copié ✓');}
    t.addEventListener('click',function(e){if(e.target.closest('a,button,form'))return;cop();});
    t.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();cop();}});
  });
  document.querySelectorAll('#fab-menu li,#palette li,.menu-lateral a,.liste-actions li').forEach(function(el){
    if(el.dataset.v8c)return;el.dataset.v8c='1';
    if(el.tagName!=='A'&&!el.querySelector('a,button')){
      el.setAttribute('tabindex','0');el.setAttribute('role','button');
      el.addEventListener('keydown',function(e){
        if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click();}
      });
    }
  });
  document.querySelectorAll('footer a,footer span').forEach(function(el){
    if(el.dataset.v8c)return;el.dataset.v8c='1';
    el.title='Astuce : chaque élément de l\'écran est copiable ou cliquable';
  });
}
if(document.readyState!=='loading')activer();else document.addEventListener('DOMContentLoaded',activer);
new MutationObserver(activer).observe(document.documentElement,{childList:true,subtree:true});
})();
// ===== V8 : garde-fou anti-média côté navigateur, fidèle à la loi 100 % texte =====
(function(){
document.addEventListener('change',function(e){
  if(e.target.matches('input[type=file]')){e.target.value='';toast('Aucun média : cette application est 100 % texte');}
},true);
document.addEventListener('drop',function(e){
  if(e.target.closest('textarea,input')){e.preventDefault();toast('Dépôt de fichier refusé : 100 % texte');}
},true);
})();
</script>
</body></html"""

TEMPLATES = {"base.html": BASE}

def page(nom: str, **contexte):
    """Rend un gabarit du dictionnaire en héritant de base.html."""
    return render_template_string("{% extends 'base.html' %}" + TEMPLATES[nom], **contexte)

TEMPLATES["auth.html"] = """{% block contenu %}
<div class="carte"><h1>{{ titre }}</h1><p class="muet">Identification par numéro de téléphone ou pseudo.</p>
<form method="post">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  {% if inscription %}<p><label>Téléphone (Mobile Money)</label><input name="telephone" placeholder="+229 01 23 45 67 89"></p>{% endif %}
  <p><label>{% if inscription %}Surnom — votre identifiant unique (6 lettres){% else %}Pseudo ou téléphone{% endif %}</label><input name="identifiant" required></p>
  <p><label>Mot de passe</label><input name="mot_de_passe" type="password" required></p>
  {% if inscription %}<p class="muet">Surnom unique : exactement 6 lettres, sans chiffres ni symboles (ex. : koffia). Les accents français sont acceptés.</p>
  <p class="muet">Le mot de passe doit contenir au moins 6 caractères.</p>{% endif %}
  <button>{{ bouton }}</button>
</form>
{% if inscription %}<p class="muet">Déjà inscrit ? <a href="{{ url_for('connexion') }}">Se connecter</a></p>
{% else %}<p class="muet">Pas encore de compte ? <a href="{{ url_for('inscription') }}">S'inscrire</a></p>{% endif %}
{% if admin_mtn is defined %}{% include 'numeros.html' %}{% endif %}
</div>{% endblock %}"""

# Bloc « numéros Mobile Money de l'administrateur » inclus tel quel sur le fil
# d'accueil, chaque profil public, le portefeuille, les discussions et l'admin.
TEMPLATES["numeros.html"] = """<div class="mm">
  <h3>💳 Paiement Mobile Money — numéros de l'administrateur</h3>
  <p class="muet">Abonnements et pourboires se paient sur l'un de ces deux numéros, puis l'administrateur valide et crédite le portefeuille du créateur.</p>
  <div class="mm-num"><label>MTN Mobile Money</label>
    <input type="text" readonly value="{{ admin_mtn }}" onclick="this.select()" aria-label="Numéro MTN Mobile Money de l'administrateur">
    <button class="btn-sec" type="button" onclick="copier(this)">Copier</button></div>
  <div class="mm-num"><label>Moov Mobile Money</label>
    <input type="text" readonly value="{{ admin_moov }}" onclick="this.select()" aria-label="Numéro Moov Mobile Money de l'administrateur">
    <button class="btn-sec" type="button" onclick="copier(this)">Copier</button></div>
  <p class="muet">Abonnements mensuels — 6 paliers : <b>{{ paliers|join(' · ') }} {{ devise }}</b></p>
  <p class="muet" style="font-size:0.72rem;line-height:1.25;opacity:0.85">Faites vos dépôts à la main sur ces deux numéros de votre choix sur chaque utilisateur que vous aimeriez suivre continuellement dans le temps</p>
</div>"""

# Page PUBLIQUE « Tarifs et paiements » : paliers, commissions, numéros et
# fonctionnement, tous lus de la configuration (aucun prix écrit en dur).
TEMPLATES["tarifs.html"] = """{% block contenu %}
<div class="carte"><h1>Tarifs et paiements</h1>
<p class="muet">Page publique — tout ce que l'application prélève et tous les moyens de paiement, sans surprise.</p></div>
<div class="carte"><h2>Abonnements mensuels — 6 paliers</h2>
<p class="muet">Le créateur choisit son palier ; l'abonné paie sur les numéros Mobile Money de l'administrateur, affichés ci-dessous.</p>
<div class="grid">
{% for g in grille %}<div class="tuile"><b>{{ g['palier'] }} {{ devise }} <span style="font-size:12px;color:var(--muet)">/ mois</span></b>
<span class="muet">Commission : {{ g['commission'] }} {{ devise }}</span>
<span class="muet">Net créateur : {{ g['net'] }} {{ devise }}</span></div>{% endfor %}
</div>
<p class="muet">Sur chaque transaction (abonnement ou pourboire) : {{ commission_pct|int }} % du montant + {{ commission_plancher|int }} {{ devise }}.
Fin de mois : <b>Net du mois = Total brut − ({{ commission_pct|int }} % × Total brut) − ({{ commission_plancher|int }} {{ devise }} × nombre de transactions)</b>. Mode actuel : « {{ mode_commission }} ».</p>
</div>
<div class="carte"><h2>Pourboires libres</h2>
<p>Un lecteur peut envoyer un pourboire du montant de son choix à n'importe quel créateur — même commission que ci-dessus.</p></div>
{% include 'numeros.html' %}
<div class="carte"><h2>Comment ça marche</h2>
<p><b>1.</b> L'abonné paie le palier choisi sur MTN ou Moov (numéros ci-dessus, bouton « Copier »).</p>
<p><b>2.</b> L'administrateur vérifie la réception et valide : le net est crédité au portefeuille du créateur.</p>
<p><b>3.</b> Le créateur demande son retrait à l'administrateur via la discussion, en donnant son numéro.</p>
<p><b>4.</b> En fin de mois, l'administrateur transfère le net manuellement et archive le versement.</p></div>
{% if not me %}<div class="carte"><p><a class="btn" href="{{ url_for('inscription') }}">Créer mon compte</a>
<a class="btn btn-sec" href="{{ url_for('connexion') }}">Se connecter</a></p></div>{% endif %}
{% endblock %}"""

TEMPLATES["fil.html"] = """{% block contenu %}
{% if bandeau %}<div class="carte"><p><b>{{ bandeau }}</b></p></div>{% endif %}
<div class="carte">
  <h1>Fil mondial — textes uniquement</h1>
  <p class="muet">Aucune image, aucune vidéo, aucun fichier joint. Que du texte.</p>
  <form method="post" action="{{ url_for('publier') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <textarea name="corps" maxlength="5000" placeholder="Quoi de neuf ?" required></textarea>
    <p class="row"><button>Publier</button><span class="muet">{{ posts|length }} publication(s) affichée(s)</span></p>
  </form>
</div>
{% include 'numeros.html' %}
{% for p in posts %}
<div class="carte">
  <p class="meta-pub"><a href="{{ url_for('profil', pseudo=p['pseudo']) }}"><b>{{ p['pseudo'] }}</b></a>
     {% if p['est_admin'] %}<span class="badge">administrateur</span>{% endif %}
     {% if est_en_ligne(p['derniere_activite']) %}<span class="enligne">● En ligne</span>
     {% else %}<span class="horsligne">○ Hors ligne</span>{% endif %}
     <span class="badge">👥 {{ p['nb_abonnes'] }} abonné{{ '' if p['nb_abonnes']|int == 1 else 's' }}</span>
     <span class="muet">· {{ p['cree_le'] }}</span></p>
  <p style="white-space:pre-wrap" class="selec" title="Sélectionnez ou copiez librement">{{ p['corps'] }}</p>
  <p class="row">
    <form method="post" action="{{ url_for('aimer', post_id=p['id']) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button class="btn-sec" title="J'aime">{% if p['j_aime'] %}❤ {{ p['nb_aimes'] }}{% else %}♡ {{ p['nb_aimes'] }}{% endif %}</button>
    </form>
    {% if p['auteur_id'] != me['id'] %}
      <form method="post" action="{{ url_for('suivre', cible=p['auteur_id']) }}">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn-sec">{{ 'Se désabonner' if p['auteur_id'] in mes_suivis else 'Suivre' }}</button>
      </form>
      <form method="post" action="{{ url_for('abonner', createur=p['auteur_id']) }}" class="row">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <select name="palier" style="width:auto" class="selec">{% for pal in paliers %}<option value="{{ pal }}">{{ pal }} {{ devise }}/mois</option>{% endfor %}</select>
        <button class="btn-sec">S'abonner à {{ p['pseudo'] }}</button>
      </form>
      <form method="post" action="{{ url_for('pourboire', createur=p['auteur_id']) }}" class="row">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <input name="montant" type="number" min="25" step="25" value="100" style="width:110px">
        <input name="mot" placeholder="mot du pourboire" style="width:180px">
        <button>Pourboire</button>
      </form>
    {% endif %}
    <a href="{{ url_for('messages_conversation', autre=p['auteur_id']) }}" class="btn btn-sec">Discuter</a>
  </p>
  <div class="comms" id="comms-{{ p['id'] }}">
    <h3>💬 Commentaires ({{ p['nb_comments'] }})</h3>
    {% for c in commentaires.get(p['id'], []) %}
      <div class="comm">
        <div><a href="{{ url_for('profil', pseudo=c['pseudo']) }}"><b>{{ c['pseudo'] }}</b></a>
          {% if c['abonne'] %}<span class="badge">abonné</span>{% endif %}
          {% if c['auteur_id'] == p['auteur_id'] %}<span class="badge">auteur</span>{% endif %}
          <div class="corps">{{ c['corps'] }}</div></div>
        <span class="muet">{{ c['cree_le'] }}</span>
      </div>
    {% endfor %}
    {% if not commentaires.get(p['id']) %}<p class="muet">Aucun commentaire. Abonnés et non-abonnés peuvent commenter.</p>{% endif %}
    <form method="post" action="{{ url_for('commenter', post_id=p['id']) }}" class="form-comms">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <input name="corps" maxlength="500" placeholder="Écrire un commentaire…" required>
      <button class="btn-sec">Commenter</button>
    </form>
  </div>
</div>
{% endfor %}
{% if not posts %}<div class="carte"><p>Le fil est vide. Publiez le premier texte.</p></div>{% endif %}
{% endblock %}"""

TEMPLATES["profil.html"] = """{% block contenu %}
<div class="carte">
  <h1>{{ cible['pseudo'] }} {% if cible['est_admin'] %}<span class="badge">administrateur</span>{% endif %}</h1>
  <p class="muet">{{ abonnes }} abonné(s) · {{ suivis }} abonnement(s) pris par ce compte</p>
  {% if cible['bio'] %}<p>{{ cible['bio'] }}</p>{% endif %}
  <p class="row">
    <a class="btn btn-sec" href="{{ url_for('messages_conversation', autre=cible['id']) }}">Discuter avec {{ cible['pseudo'] }}</a>
    {% if cible['id'] != me['id'] %}
    <form method="post" action="{{ url_for('suivre', cible=cible['id']) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">{{ 'Se désabonner' if deja_suis else 'Suivre' }}</button>
    </form>{% endif %}
  </p>
</div>
{% include 'numeros.html' %}
<p class="muet" style="font-size:0.72rem">Numéros Mobile Money enregistrés sur ce compte — MTN <b>{{ cible['admin_mtn_numero'] }}</b> · Moov <b>{{ cible['admin_moov_numero'] }}</b></p>
{% if cible['id'] != me['id'] %}
<div class="carte">
  <h2>S'abonner — 6 paliers mensuels</h2>
  <p class="muet">Payez le palier choisi sur l'un des numéros de l'administrateur affichés ci-dessus, puis demandez la validation par la discussion.</p>
  <form method="post" action="{{ url_for('abonner', createur=cible['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <div class="grid">
    {% for p in paliers %}
      <label class="tuile"><input type="radio" name="palier" value="{{ p }}" style="width:auto" {% if loop.first %}checked{% endif %}>
        <b>{{ p }} {{ devise }}</b><span class="muet">par mois</span></label>
    {% endfor %}
    </div>
    <p class="row"><button>S'abonner à {{ cible['pseudo'] }}</button>
      <span class="muet">Après paiement Mobile Money, l'administrateur valide et le net est crédité.</span></p>
  </form>
</div>
<div class="carte">
  <h2>Calculateur de commission</h2>
  <p class="row"><input id="mnt" type="number" value="100" min="1" step="25" style="width:140px">
    <button class="btn-sec" onclick="calc()">Calculer</button>
    <span id="res" class="muet"></span></p>
  <p class="muet">Le calcul est fait par le serveur (source unique de vérité).</p>
</div>
<div class="carte">
  <h2>Pourboire libre</h2>
  <form method="post" action="{{ url_for('pourboire', createur=cible['id']) }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="montant" type="number" min="25" step="25" value="100" style="width:130px">
    <input name="mot" placeholder="mot libre" style="width:220px"><button>Envoyer</button>
  </form>
</div>
{% endif %}
<div class="carte"><h2>Publications de {{ cible['pseudo'] }}</h2>
{% for p in posts %}<div class="msg">{{ p['corps'] }}<div class="muet">{{ p['cree_le'] }}</div></div>{% endfor %}
{% if not posts %}<p class="muet">Aucune publication.</p>{% endif %}</div>
<script>
function calc(){var m=document.getElementById('mnt').value;
fetch('/api/calcul?montant='+encodeURIComponent(m)).then(r=>r.json()).then(d=>{
document.getElementById('res').textContent='Commission '+d.commission+' {{ devise }} — net créateur '+d.net+' {{ devise }} — ('+d.detail+')';});}
</script>
{% endblock %}"""

TEMPLATES["portefeuille.html"] = """{% block contenu %}
<div class="carte"><h1>Mon portefeuille virtuel</h1>
<p class="muet">1 ticket = {{ ticket_fcfa }} {{ devise }}. Le retrait se demande par la discussion utilisateur ↔ administrateur.</p>
<div class="grid">
  <div class="tuile"><b>{{ s.tickets_payes }}</b>tickets déjà payés</div>
  <div class="tuile"><b>{{ s.tickets_attente }}</b>tickets en attente</div>
  <div class="tuile"><b>{{ s.fcfa_total }}</b>{{ devise }} au total</div>
</div>
<p class="row" style="margin-top:12px">
  <a class="btn" href="{{ url_for('messages_conversation', autre=admin_id) }}">Demander un retrait à l'administrateur</a>
  <a class="btn btn-sec" href="{{ url_for('plaintes') }}">Ouvrir une plainte</a>
</p>
</div>
{% include 'numeros.html' %}
<div class="carte"><h2>Calculateur du mois — version validée</h2>
<p class="muet">Net du mois = Total brut − (25 % × Total brut) − (15 {{ devise }} × nombre de transactions).</p>
<table><tr><th>Total brut du mois</th><th>Commission 25 %</th><th>15 {{ devise }} × {{ mois_resume['nb'] }} transaction(s)</th><th>Net du mois</th></tr>
<tr><td><b>{{ mois_resume['brut'] }}</b> {{ devise }}</td><td>{{ mois_resume['pct'] }} {{ devise }}</td><td>{{ mois_resume['fixe'] }} {{ devise }}</td>
<td><b>{{ mois_resume['net'] }} {{ devise }}</b></td></tr></table>
</div>
<div class="carte"><h2>Comptabilité détaillée</h2>
<table><tr><th>Date</th><th>Sens</th><th>Tickets</th><th>{{ devise }}</th><th>Libellé</th><th>Statut</th></tr>
{% for l in lignes %}<tr><td>{{ l['cree_le'] }}</td><td>{{ l['sens'] }}</td><td>{{ l['tickets'] }}</td>
<td><b>{{ l['fcfa'] }}</b></td><td>{{ l['libelle'] }}</td><td>{{ l['statut'] }}</td></tr>{% endfor %}</table>
{% if not lignes %}<p class="muet">Aucune écriture pour le moment.</p>{% endif %}</div>
{% endblock %}"""

TEMPLATES["messages.html"] = """{% block contenu %}
<div class="carte"><h1>Discussions</h1>
<p class="muet">Cliquez un nom pour ouvrir la discussion. L'administrateur traite ici les demandes de retrait et de dépôt.</p>
{% include 'numeros.html' %}
{% for c in contacts %}
  <p><a href="{{ url_for('messages_conversation', autre=c['id']) }}"><b>{{ c['pseudo'] }}</b></a>
     <span class="badge">{{ c['role'] }}</span>{% if c['non_lus'] %}<span class="pastille">{{ c['non_lus'] }}</span>{% endif %}</p>
{% endfor %}
{% if not contacts %}<p class="muet">Aucun contact pour l'instant. Publiez ou abonnez-vous pour créer des échanges.</p>{% endif %}
</div>
{% if autre %}
<div class="carte"><h2>Discussion avec {{ autre['pseudo'] }}</h2>
<p class="row"><button type="button" class="btn-sec" onclick="copier_fil(this)" title="Copie tous les messages de la discussion">📋 Copier tout le fil</button>
<button type="button" class="btn-sec" onclick="exporter_fil_md()" title="Télécharge la discussion en fichier Markdown">⬇️ Exporter en .md</button></p>
{% for m in fil %}
  <div class="msg {% if m['expediteur_id'] == me['id'] %}moi{% endif %}">
    <b>{{ 'Moi' if m['expediteur_id'] == me['id'] else autre['pseudo'] }}</b> <span class="muet">{{ m['cree_le'] }}</span>
    <div style="white-space:pre-wrap">{{ m['corps'] }}</div></div>
{% endfor %}
{% if not fil %}<p class="muet">Aucun message. Écrivez le premier.</p>{% endif %}
<form method="post" action="{{ url_for('messages_envoyer', autre=autre['id']) }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <textarea name="corps" placeholder="Votre message (numéro Mobile Money, demande de retrait…)" required></textarea>
  <p><button>Envoyer</button></p>
</form></div>
{% endif %}
{% endblock %}"""

TEMPLATES["plaintes.html"] = """{% block contenu %}
<div class="carte"><h1>Plaintes et assistance</h1>
<form method="post" action="{{ url_for('plainte_creer') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Sujet</label><input name="sujet" required maxlength="120"></p>
  <p><label>Exposé</label><textarea name="corps" required></textarea></p>
  <button>Soumettre la plainte</button>
</form></div>
<div class="carte"><h2>Mes plaintes</h2>
{% for p in mes %}<div class="msg"><b>{{ p['sujet'] }}</b> <span class="badge">{{ p['statut'] }}</span>
<div class="muet">{{ p['cree_le'] }}</div><div style="white-space:pre-wrap">{{ p['corps'] }}</div>
{% if p['reponse_ia'] %}<div class="ia-reponse"><b>🤖 Première réponse automatique :</b>
<div style="white-space:pre-wrap">{{ p['reponse_ia'] }}</div></div>{% endif %}
{% if p['reponse'] %}<div class="msg moi"><b>Réponse de l'administrateur :</b> {{ p['reponse'] }}</div>{% endif %}</div>{% endfor %}
{% if not mes %}<p class="muet">Aucune plainte enregistrée.</p>{% endif %}</div>
{% endblock %}"""

TEMPLATES["admin.html"] = """{% block contenu %}
<div class="carte"><h1>Tableau de bord administrateur</h1>
<p class="muet">Commission récoltée : <b>{{ recettes }} {{ devise }}</b> · mode « {{ mode_commission }} »
({{ commission_pct|int }}% , plancher {{ commission_plancher|int }} {{ devise }})</p>
<form method="get" action="{{ url_for('admin_export') }}" class="row">
  <label for="mois-comptable">Export comptable mensuel (CSV)</label>
  <input type="month" id="mois-comptable" name="mois" value="{{ mois_courant }}" required>
  <button class="btn-sec">Exporter</button>
</form>
</div>
<div class="carte"><h2>💼 Soldes par application</h2>
<p class="muet">Consolidation en temps réel des trois portefeuilles de la plateforme — portefeuilles membres (V7), dépôts séquestrés (escrow V5) et demandes de reversement Mobile Money (moteur V8). Toutes les valeurs sont lues directement de la base : rien n'est écrit à la main. Cliquez une case pour copier la valeur.</p>
<div class="kpi">
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['en_attente_fcfa']) }} {{ devise }}</b><span>À créditer (portefeuilles, en attente)</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['escrow_bloque']) }} {{ devise }}</b><span>Séquestre bloqué ({{ soldes_app['escrows_bloques'] }} dépôt(s))</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['attente_total_fcfa']) }} {{ devise }}</b><span>Engagements totaux (attente + séquestre)</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ soldes_app['reversements_attente'] }}</b><span>Reversements V8 en cours ({{ '%g'|format(soldes_app['reversements_attente_fcfa']) }} {{ devise }})</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['paye_fcfa']) }} {{ devise }}</b><span>Crédités « payé » ({{ '%g'|format(soldes_app['paye_tickets']) }} tickets)</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['verse_fcfa']) }} {{ devise }}</b><span>Versés Mobile Money</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['recettes_abonnements']) }} {{ devise }}</b><span>Recettes — abonnements</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['recettes_pourboires']) }} {{ devise }}</b><span>Recettes — pourboires</span></div>
  <div class="case" tabindex="0" role="button" title="Cliquez pour copier"><b>{{ '%g'|format(soldes_app['recettes_boutique']) }} {{ devise }}</b><span>Recettes — boutique</span></div>
</div>
</div>
<div class="carte"><h2>🧾 Récapitulatif des commissions et paliers</h2>
<p class="muet">Les 6 paliers d'abonnement avec la règle de commission active « {{ mode_commission }} »
({{ commission_pct|int }} % + plancher {{ commission_plancher|int }} {{ devise }}) — le même calcul que la page publique Tarifs, sans aucune duplication.</p>
<table><tr><th>Palier / mois</th><th>Commission application</th><th>Net créateur</th><th>Détail du calcul</th></tr>
{% for p in paliers|map('string')|list %}{% if p in resume_paliers %}
<tr><td><b>{{ p }} {{ devise }}</b></td><td>{{ '%g'|format(resume_paliers[p]['commission']) }} {{ devise }}</td>
<td><b>{{ '%g'|format(resume_paliers[p]['net']) }} {{ devise }}</b></td>
<td class="muet">{{ resume_paliers[p]['detail'] }}</td></tr>
{% endif %}{% endfor %}</table>
<p class="muet">Fin de mois : Net = Total brut − commissions (mode « {{ mode_commission }} »). Numéros de dépôt en bas de page.</p>
</div>
<div class="carte"><h2>📣 Paliers de reversement créateurs — moteur de monétisation V8</h2>
<p class="muet">Barème unique : 50 000 {{ devise }} / 1 000 vues pondérées → 75 % application · 25 % créateurs, frais opérateur à la charge de l'application. Table calculée par le moteur V8 (aucun montant saisi à la main).</p>
<table><tr><th>Niv.</th><th>Nom</th><th>Vues/mois</th><th>Pool brut</th><th>App 75 %</th><th>Créateurs 25 %</th><th>Frais</th><th>Net versé</th><th>Délai</th><th>KYC</th></tr>
{% for lg in paliers_v8 %}
<tr><td><b>{{ lg['niveau'] }}</b></td><td>{{ lg['nom'] }}</td>
<td>{% if lg['vues_max'] %}{{ lg['vues_min'] }}–{{ lg['vues_max'] }}{% else %}{{ lg['vues_min'] }} et +{% endif %}</td>
<td>{{ '%g'|format(lg['pool_fcfa']) }} {{ devise }}</td><td>{{ '%g'|format(lg['app_fcfa']) }} {{ devise }}</td>
<td><b>{{ '%g'|format(lg['createurs_fcfa']) }} {{ devise }}</b></td><td>{{ lg['frais_pct'] }} %</td>
<td><b>{{ '%g'|format(lg['net_fcfa']) }} {{ devise }}</b></td><td>J+{{ lg['delai_jours'] }}</td>
<td class="muet">{{ lg['kyc'] }}</td></tr>
{% endfor %}</table>
{% if not paliers_v8 %}<p class="muet">Moteur V8 non branché : tableau indisponible (l'application fonctionne sans lui).</p>{% endif %}
</div>
<div class="carte"><h2>📊 Suivi des abonnements (Excel)</h2>
<p class="muet">Classeur .xlsx complet — feuilles <b>Abonnements</b>, <b>Pourboires</b> et <b>Synthèse</b>, avec filtres automatiques et volets figés. Remplissez les décisions dans Excel puis réimportezy le fichier : les références marquées « valide » ou « refuse » sont appliquées d'un coup.</p>
<p class="row"><a class="btn" href="{{ url_for('admin_suivi_xlsx') }}">⬇️ Télécharger le suivi .xlsx</a></p>
<form method="post" action="{{ url_for('admin_suivi_import') }}" enctype="multipart/form-data" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input type="file" name="fichier" accept=".xlsx" required>
  <button class="btn-sec">Importer les décisions</button>
</form>
<table><tr><th>Abonné</th><th>Créateur</th><th>Palier</th><th>Commission</th><th>Net</th><th>Référence</th><th>Statut</th><th>Créé le</th></tr>
{% for a in suivi %}<tr><td><b>{{ a['abonne'] }}</b></td><td>{{ a['createur'] }}</td><td>{{ a['palier'] }} {{ devise }}</td><td>{{ a['commission'] }}</td><td><b>{{ a['net'] }}</b></td><td class="tel">{{ a['reference'] }}</td><td>{{ a['statut'] }}</td><td class="muet">{{ a['cree_le'] }}</td></tr>{% endfor %}
{% if not suivi %}<tr><td colspan="8" class="muet">Aucun abonnement enregistré pour l'instant.</td></tr>{% endif %}</table>
</div>
<div class="carte"><h2>📜 Journal d'activité</h2>
<p class="muet">Piste d'audit complète : connexions, publications, commentaires, validations, exports et envois groupés — horodatés, avec adresse IP.</p>
<p class="row"><a class="btn" href="{{ url_for('admin_journal') }}">📜 Ouvrir le journal d'activité</a>
<a class="btn btn-sec" href="{{ url_for('admin_journal_csv') }}">⬇️ Exporter le journal (.csv)</a></p>
</div>
{% include 'numeros.html' %}
<div class="carte"><h2>Modèles de messages — fin de mois</h2>
<p class="muet">Cliquez « Copier » puis collez le message dans la discussion de l'abonné concerné. Remplacez [MONTANT] et [RÉF.] avant l'envoi.</p>
<div class="modele-bloc">
  <div class="modele-grand"><label>Abonné payé</label>
    <textarea readonly>{{ modele_paye }}</textarea>
    <button class="btn-sec" type="button" onclick="copier_zone(this)">Copier</button></div>
  <div class="modele-grand"><label>Abonné en attente</label>
    <textarea readonly>{{ modele_attente }}</textarea>
    <button class="btn-sec" type="button" onclick="copier_zone(this)">Copier</button></div>
  <div class="modele-petit"><label>Réponse transaction fin de mois</label>
    <textarea readonly>{{ modele_transaction }}</textarea>
    <button class="btn-sec" type="button" onclick="copier_zone(this)">Copier</button></div>
  <div class="modele-grand"><label>Relance des impayés (plus de {{ delai_relance }} jours)</label>
    <textarea readonly>{{ modele_relance }}</textarea>
    <button class="btn-sec" type="button" onclick="copier_zone(this)">Copier</button></div>
  <div class="modele-grand"><label>Espace d'envoi groupé</label>
    <p class="muet">Pour envoyer un même message à plusieurs membres d'un coup (payés, en attente, impayés, tous),
    avec réponse automatique de l'assistant IA si vous le souhaitez, utilisez l'<a href="{{ url_for('admin_envois') }}"><b>espace d'envoi groupé</b></a>.</p></div>
</div></div>
<div class="carte"><h2>Paiements du mois — tickets + somme + numéro</h2>
<table><tr><th>Membre</th><th>Numéro Mobile Money</th><th>Opérateur</th><th>Tickets</th><th>Net {{ devise }} à transférer</th><th>Écritures</th><th>Action</th></tr>
{% for l in paiements %}
<tr><td><a href="{{ url_for('messages_conversation', autre=l['id']) }}"><b>{{ l['pseudo'] }}</b></a></td>
<td class="tel">{{ l['payout_phone'] or l['telephone'] or '—' }}</td><td>{{ l['payout_op'] }}</td>
<td>{{ l['tickets'] }}</td><td><b>{{ l['fcfa'] }}</b></td><td>{{ l['ecritures'] }}</td>
<td><form method="post" action="{{ url_for('admin_payer', uid=l['id']) }}">
  <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-vert">Marquer payé</button></form></td></tr>
{% endfor %}</table>
{% if not paiements %}<p class="muet">Aucun solde en attente : tous les membres sont à jour.</p>{% endif %}
<p class="muet">« Marquer payé » archive le versement et passe les écritures en « payé ».</p></div>
<div class="carte"><h2>Abonnements à valider</h2><table><tr><th>Réf.</th><th>Créateur</th><th>Abonné</th><th>Palier</th><th>Commission</th><th>Net</th><th></th></tr>
{% for a in abo_attente %}<tr><td>{{ a['reference'] }}</td><td>{{ a['pseudo_c'] }}</td><td>{{ a['pseudo_a'] }}</td>
<td>{{ a['palier'] }}</td><td>{{ a['commission'] }}</td><td><b>{{ a['net'] }}</b></td>
<td><form method="post" action="{{ url_for('admin_valider_abonnement', aid=a['id']) }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><button>Valider</button></form></td></tr>{% endfor %}</table>
{% if not abo_attente %}<p class="muet">Rien à valider.</p>{% endif %}</div>
<div class="carte"><h2>Pourboires à valider</h2><table><tr><th>#</th><th>Créateur</th><th>De</th><th>Montant</th><th>Commission</th><th>Net</th><th></th></tr>
{% for t in tips_attente %}<tr><td>{{ t['id'] }}</td><td>{{ t['pseudo_c'] }}</td><td>{{ t['pseudo_e'] }}</td>
<td>{{ t['montant'] }}</td><td>{{ t['commission'] }}</td><td><b>{{ t['net'] }}</b></td>
<td><form method="post" action="{{ url_for('admin_valider_pourboire', tid=t['id']) }}">
<input type="hidden" name="csrf" value="{{ csrf }}"><button>Valider</button></form></td></tr>{% endfor %}</table>
{% if not tips_attente %}<p class="muet">Rien à valider.</p>{% endif %}</div>
<div class="carte"><h2>Plaintes</h2>
{% for p in plaintes %}<div class="msg"><b>{{ p['sujet'] }}</b> <span class="badge">{{ p['pseudo'] }}</span> <span class="badge">{{ p['statut'] }}</span>
<div style="white-space:pre-wrap">{{ p['corps'] }}</div>
<form method="post" action="{{ url_for('admin_repondre_plainte', pid=p['id']) }}" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input name="reponse" placeholder="Réponse" style="width:320px"><button>Répondre</button></form></div>{% endfor %}
{% if not plaintes %}<p class="muet">Aucune plainte ouverte.</p>{% endif %}</div>
<div class="carte"><h2>Membres — blocage (sanctions des CGU)</h2>
<table><tr><th>Membre</th><th>Rôle</th><th>État</th><th>Action</th></tr>
{% for m in membres %}
<tr><td><a href="{{ url_for('messages_conversation', autre=m['id']) }}"><b>{{ m['pseudo'] }}</b></a></td>
<td>{{ 'administrateur' if m['est_admin'] else 'membre' }}</td>
<td>{{ 'bloqué' if m['bloque'] else 'actif' }}</td>
<td>{% if m['est_admin'] %}<span class="muet">—</span>{% else %}
<form method="post" action="{{ url_for('admin_bloquer', uid=m['id']) }}" style="display:inline">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <button class="{{ 'btn-vert' if m['bloque'] else 'btn-rouge' }}">{{ 'Débloquer' if m['bloque'] else 'Bloquer' }}</button></form><form method="post" action="{{ url_for('admin_muet', uid=m['id']) }}" style="display:inline"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="duree" value="1440"><input type="hidden" name="motif" value="modération manuelle"><button class="btn-sec">Muet 24 h</button></form>{% if m['muet'] %}<form method="post" action="{{ url_for('admin_demuet', uid=m['id']) }}" style="display:inline"><input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-vert">Rétablir</button></form>{% endif %}{% endif %}</td></tr>
{% endfor %}</table>
<p class="muet">Bloquer un compte : déconnexion immédiate et reconnexion impossible (voir <a href="{{ url_for('cgu') }}">Conditions d'utilisation</a>). Le même bouton débloque.</p></div>
<div class="carte"><h2>Versements déjà effectués</h2><table><tr><th>Date</th><th>Membre</th><th>Téléphone</th><th>Tickets</th><th>{{ devise }}</th></tr>
{% for h in historique %}<tr><td>{{ h['cree_le'] }}</td><td>{{ h['pseudo'] }}</td><td class="tel">{{ h['telephone'] }}</td>
<td>{{ h['tickets'] }}</td><td>{{ h['fcfa'] }}</td></tr>{% endfor %}</table>
{% if not historique %}<p class="muet">Aucun versement archivé.</p>{% endif %}</div>
{% endblock %}"""

TEMPLATES["admin_journal.html"] = """{% block contenu %}
<div class="carte"><h1>📜 Journal d'activité — piste d'audit complète</h1>
<p class="muet">{{ total }} entrée(s) enregistrée(s). Chaque action notable de l'application est horodatée ici, avec son auteur et son adresse IP.</p>
<p class="row"><a class="btn btn-sec" href="{{ url_for('admin_journal_csv') }}">⬇️ Exporter le journal (.csv)</a>
<a class="btn btn-sec" href="{{ url_for('admin') }}">← Tableau de bord</a></p>
</div>
<div class="carte"><h2>Filtres</h2>
<form method="get" class="row">
  <label>Action</label>
  <select name="action"><option value="">Toutes</option>
  {% for a in actions %}<option value="{{ a }}" {{ 'selected' if action == a }}>{{ a }}</option>{% endfor %}</select>
  <input name="pseudo" placeholder="Auteur (pseudo)" value="{{ pseudo }}" style="width:180px">
  <input name="q" placeholder="Recherche dans cible / détails" value="{{ cherche }}" style="width:240px">
  <button>Filtrer</button>
  {% if action or pseudo or cherche %}<a class="btn btn-sec" href="{{ url_for('admin_journal') }}">Réinitialiser</a>{% endif %}
</form></div>
<div class="carte"><table>
<tr><th>Horodatage</th><th>Auteur</th><th>Action</th><th>Cible</th><th>Détails</th><th>Adresse IP</th></tr>
{% for l in lignes %}<tr><td class="muet">{{ l['cree_le'] }}</td>
<td>{% if l['pseudo'] %}<b>{{ l['pseudo'] }}</b>{% else %}<span class="muet">système</span>{% endif %}</td>
<td><span class="badge">{{ l['action'] }}</span></td>
<td>{{ l['cible'] }}</td><td>{{ l['details'] }}</td><td class="tel">{{ l['adresse_ip'] }}</td></tr>{% endfor %}
{% if not lignes %}<tr><td colspan="6" class="muet">Aucune entrée pour ce filtre.</td></tr>{% endif %}</table>
{% if pages > 1 %}<p class="row">
  {% if page_num > 1 %}<a class="btn-sec" href="{{ url_for('admin_journal', page=page_num-1, action=action, pseudo=pseudo, q=cherche) }}">← Précédent</a>{% endif %}
  <span class="muet">Page {{ page_num }} / {{ pages }}</span>
  {% if page_num < pages %}<a class="btn-sec" href="{{ url_for('admin_journal', page=page_num+1, action=action, pseudo=pseudo, q=cherche) }}">Suivant →</a>{% endif %}
</p>{% endif %}</div>
{% endblock %}"""

TEMPLATES["admin_envois.html"] = """{% block contenu %}
<div class="carte"><h1>📣 Espace d'envoi groupé</h1>
<p class="muet">Espace privé de l'administrateur : sélectionnez les membres, choisissez ou écrivez n'importe quel
message, et activez l'assistant IA pour répondre automatiquement aux utilisateurs qui vous répondent, selon
vos règles. Quota de sécurité : {{ quotas.max }} messages par heure — restant cette heure : <b>{{ quotas.restant }}</b>.</p>
<p class="muet">Cibles disponibles — En attente : <b>{{ quotas.attente }}</b> · Impayés (+{{ delai_relance }} jours) : <b>{{ quotas.impayes }}</b> · Tous : <b>{{ quotas.tous }}</b></p>
<form method="post">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Type de message</label>
    <select name="objet" onchange="var m=MODELES[this.value]; if(m){this.form.corps.value=m;}">
      <option value="paye">Message « abonné payé »</option>
      <option value="attente">Message « abonné en attente »</option>
      <option value="relance">Message « relance des impayés »</option>
      <option value="transaction">Message « réponse transaction »</option>
      <option value="personnalise">Message personnalisé (texte libre)</option>
    </select></p>
  <p><label>Message</label><textarea name="corps" required placeholder="Votre message…">{{ corps_preset }}</textarea></p>
  <p class="muet">Le texte est envoyé tel quel à chaque membre sélectionné. Remplacez [MONTANT] et [RÉF.] si besoin.</p>
  <p><label>Ciblage</label>
    <select name="filtre" onchange="if(this.value!='manuel'){window.location='{{ url_for('admin_envois') }}?filtre='+this.value;}">
      <option value="manuel" {{ 'selected' if filtre == 'manuel' }}>Sélection manuelle (cases ci-dessous)</option>
      <option value="attente" {{ 'selected' if filtre == 'attente' }}>Tous les abonnés en attente</option>
      <option value="impayes" {{ 'selected' if filtre == 'impayes' }}>Tous les impayés (+{{ delai_relance }} jours)</option>
      <option value="tous" {{ 'selected' if filtre == 'tous' }}>Tous les membres actifs</option>
    </select></p>
  {% if filtre == 'manuel' %}
  <div class="carte"><h3>Sélection des membres</h3>
  <table><tr><th>Envoyer</th><th>Membre</th><th>État</th><th>Messages groupés</th></tr>
  {% for m in membres %}<tr>
    <td class="case-li"><input type="checkbox" name="cibles" value="{{ m['id'] }}" style="width:auto"></td>
    <td><a href="{{ url_for('messages_conversation', autre=m['id']) }}"><b>{{ m['pseudo'] }}</b></a></td>
    <td>{{ 'bloqué' if m['bloque'] else 'actif' }}</td>
    <td>{{ 'refusés' if m['refus_groupes'] else 'acceptés' }}</td>
  </tr>{% endfor %}</table>
  <p class="muet">Les comptes bloqués et les membres ayant refusé les messages groupés sont ignorés automatiquement, même cochés.</p></div>
  {% else %}
  <p class="muet">Ciblage automatique : le message partira vers les membres correspondant au filtre choisi (hors comptes bloqués et membres ayant refusé les messages groupés).</p>
  {% endif %}
  <div class="carte"><h3>🤖 Assistant IA de réponse automatique</h3>
    <p><label><input type="checkbox" name="auto_reponse" value="1" style="width:auto">
       Répondre automatiquement aux membres qui me répondent après cet envoi</label></p>
    <p><label>Règles dictées par l'administrateur (prioritaires sur l'IA)</label>
       <textarea name="regles" placeholder="Ex. : toute question de paiement reçoit la réponse X ; si la référence est introuvable, demander un justificatif ; répondre en français poli, 5 phrases maximum."></textarea></p>
    <p class="muet">Si aucune clé d'IA n'est configurée sur le serveur (variable TOUTBOT_LLM_KEY), l'assistant intégré répond par des messages prédéfinis honnêtes (paiement, blocage, générique) : aucune invention.</p>
    <p class="row"><button class="btn-vert">Envoyer le message groupé</button></p>
  </div>
</form></div>
<div class="carte"><h2>Derniers envois groupés</h2>
<table><tr><th>Date</th><th>Type</th><th>Ciblage</th><th>IA</th><th>Aperçu</th></tr>
{% for e in historique %}<tr><td>{{ e['cree_le'] }}</td><td>{{ e['objet'] }}</td><td>{{ e['filtre'] }}</td>
<td>{{ 'oui' if e['auto_reponse'] else 'non' }}</td><td>{{ e['corps'][:80] }}…</td></tr>{% endfor %}</table>
{% if not historique %}<p class="muet">Aucun envoi groupé pour l'instant.</p>{% endif %}</div>
<script>var MODELES = {{ modeles|tojson }};</script>
{% endblock %}"""

TEMPLATES["stats.html"] = """{% block contenu %}
<div class="carte"><h1>📊 Mes statistiques</h1>
  <p class="muet">Membre depuis le {{ s['cree_le'] }} ·
     {% if s['en_ligne'] %}<span class="enligne">● En ligne</span>{% else %}<span class="horsligne">○ Hors ligne</span>{% endif %}
     · dernière activité : {{ s['derniere_activite'] }}</p>
  <div class="kpi">
    <div class="case"><b>{{ s['posts'] }}</b><span>Publications</span></div>
    <div class="case"><b>{{ s['likes_recus'] }}</b><span>J'aime reçus</span></div>
    <div class="case"><b>{{ s['likes_donnes'] }}</b><span>J'aime donnés</span></div>
    <div class="case"><b>{{ s['commentaires_postes'] }}</b><span>Commentaires postés</span></div>
    <div class="case"><b>{{ s['commentaires_recus'] }}</b><span>Commentaires reçus</span></div>
    <div class="case"><b>{{ s['abonnes'] }}</b><span>Abonnés</span></div>
    <div class="case"><b>{{ s['abonnements'] }}</b><span>Abonnements suivis</span></div>
    <div class="case"><b>{{ s['messages_envoyes'] }}</b><span>Messages envoyés</span></div>
  </div>
</div>
<div class="carte"><h2>💰 Revenus (abonnements et pourboires validés)</h2>
  <div class="kpi">
    <div class="case"><b>{{ s['abos_valides']['n'] }}</b><span>Abonnements validés</span></div>
    <div class="case"><b>{{ s['pourboires_recus']['n'] }}</b><span>Pourboires reçus</span></div>
    <div class="case"><b>{{ s['gains_totaux'] }} {{ devise }}</b><span>Gains nets cumulés</span></div>
    <div class="case"><b>{{ s['solde']['tickets_total'] }}</b><span>Tickets au portefeuille</span></div>
  </div>
</div>
<div class="carte"><h2>📈 Mon activité des 7 derniers jours</h2>
  <div class="row">
    <div><h3>Publications</h3><div class="mini-baremes">
      {% for j in s['activite_posts'] %}<div class="barre {{ 'zero' if not j['n'] }}" style="height:{{ [8, j['n']*16]|max }}px"><span class="val">{{ j['n'] }}</span></div>{% endfor %}
    </div><div class="legendes">{% for j in s['activite_posts'] %}<span>{{ j['libelle'] }}</span>{% endfor %}</div></div>
    <div><h3>J'aime</h3><div class="mini-baremes">
      {% for j in s['activite_likes'] %}<div class="barre {{ 'zero' if not j['n'] }}" style="height:{{ [8, j['n']*16]|max }}px"><span class="val">{{ j['n'] }}</span></div>{% endfor %}
    </div><div class="legendes">{% for j in s['activite_likes'] %}<span>{{ j['libelle'] }}</span>{% endfor %}</div></div>
    <div><h3>Commentaires</h3><div class="mini-baremes">
      {% for j in s['activite_comments'] %}<div class="barre {{ 'zero' if not j['n'] }}" style="height:{{ [8, j['n']*16]|max }}px"><span class="val">{{ j['n'] }}</span></div>{% endfor %}
    </div><div class="legendes">{% for j in s['activite_comments'] %}<span>{{ j['libelle'] }}</span>{% endfor %}</div></div>
  </div>
</div>
<div class="carte"><h2>📊 Mon activité sur 30 jours + export CSV</h2>
  <p class="row"><a class="btn btn-sec" href="{{ url_for('statistiques_csv') }}">⬇️ Exporter mes statistiques (CSV)</a>
     <span class="muet">Fichier compatible Excel (séparateur « ; », UTF-8).</span></p>
  <h3>Publications — 30 jours</h3>{{ svg_barres_30_jours(s['activite30_posts'])|safe }}
  <h3>J'aime — 30 jours</h3>{{ svg_barres_30_jours(s['activite30_likes'])|safe }}
  <h3>Commentaires — 30 jours</h3>{{ svg_barres_30_jours(s['activite30_comments'])|safe }}
</div>
{% if s['top_post'] %}<div class="carte"><h2>🏆 Ma publication la plus aimée ({{ s['top_post']['n'] }} j'aime)</h2>
  <p style="white-space:pre-wrap">{{ s['top_post']['corps'][:200] }}</p></div>{% endif %}
{% endblock %}"""

TEMPLATES["admin_stats.html"] = """{% block contenu %}
<div class="carte"><h1>📊 Statistiques globales — tous les utilisateurs</h1>
  <div class="kpi">
    <div class="case"><b>{{ g['users'] }}</b><span>Comptes inscrits</span></div>
    <div class="case"><b>{{ g['en_ligne'] }}</b><span>En ligne maintenant</span></div>
    <div class="case"><b>{{ g['bloques'] }}</b><span>Comptes bloqués</span></div>
    <div class="case"><b>{{ g['posts'] }}</b><span>Publications</span></div>
    <div class="case"><b>{{ g['likes'] }}</b><span>J'aime</span></div>
    <div class="case"><b>{{ g['comments'] }}</b><span>Commentaires</span></div>
    <div class="case"><b>{{ g['follows'] }}</b><span>Abonnements (suivis)</span></div>
    <div class="case"><b>{{ g['messages'] }}</b><span>Messages échangés</span></div>
    <div class="case"><b>{{ g['plaintes_ouvertes'] }}</b><span>Plaintes ouvertes</span></div>
  </div>
</div>
<div class="carte"><h2>💰 Revenus de la plateforme</h2>
  <div class="kpi">
    <div class="case"><b>{{ g['abos_valides']['n'] }}</b><span>Abonnements validés ({{ g['abos_attente'] }} en attente)</span></div>
    <div class="case"><b>{{ g['tips_valides']['n'] }}</b><span>Pourboires validés ({{ g['tips_attente'] }} en attente)</span></div>
    <div class="case"><b>{{ g['recettes'] }} {{ devise }}</b><span>Commissions récoltées</span></div>
  </div>
</div>
<div class="carte"><h2>📅 Inscriptions des 7 derniers jours</h2>
  <div class="mini-baremes">
    {% for j in g['inscriptions'] %}<div class="barre {{ 'zero' if not j['n'] }}" style="height:{{ [8, j['n']*20]|max }}px"><span class="val">{{ j['n'] }}</span></div>{% endfor %}
  </div><div class="legendes">{% for j in g['inscriptions'] %}<span>{{ j['libelle'] }}</span>{% endfor %}</div>
</div>
<div class="carte"><h2>📈 Inscriptions sur 30 jours + export CSV</h2>
  <p class="row"><a class="btn btn-sec" href="{{ url_for('admin_statistiques_csv') }}">⬇️ Exporter les statistiques globales (CSV)</a>
     <span class="muet">Fichier compatible Excel (séparateur « ; », UTF-8).</span></p>
  {{ svg_barres_30_jours(g['inscriptions30'])|safe }}
</div>
<div class="carte"><h2>👑 Top 10 créateurs (par abonnés)</h2>
<table><tr><th>#</th><th>Pseudo</th><th>Abonnés</th><th>J'aime reçus</th><th>Publications</th><th>Gains nets {{ devise }}</th></tr>
{% for u in g['top_createurs'] %}
<tr><td>{{ loop.index }}</td><td><a href="{{ url_for('profil', pseudo=u['pseudo']) }}"><b>{{ u['pseudo'] }}</b></a></td>
<td>{{ u['abonnes'] }}</td><td>{{ u['aime'] }}</td><td>{{ u['posts'] }}</td><td><b>{{ u['gains'] }}</b></td></tr>
{% endfor %}</table>
{% if not g['top_createurs'] %}<p class="muet">Aucun utilisateur encore.</p>{% endif %}</div>
<div class="carte"><h2>❤️ Top 5 publications les plus aimées</h2>
<table><tr><th>#</th><th>Auteur</th><th>Publication</th><th>J'aime</th></tr>
{% for p in g['top_publications'] %}
<tr><td>{{ loop.index }}</td><td><b>{{ p['pseudo'] }}</b></td><td>{{ p['corps'][:120] }}</td><td><b>{{ p['n'] }}</b></td></tr>
{% endfor %}</table>
{% if not g['top_publications'] %}<p class="muet">Aucun j'aime encore.</p>{% endif %}</div>
<div class="carte"><h2>💬 Top 5 commentateurs</h2>
<table><tr><th>#</th><th>Pseudo</th><th>Commentaires</th></tr>
{% for c in g['top_commentateurs'] %}<tr><td>{{ loop.index }}</td><td><b>{{ c['pseudo'] }}</b></td><td>{{ c['n'] }}</td></tr>
{% endfor %}</table>
{% if not g['top_commentateurs'] %}<p class="muet">Aucun commentaire encore.</p>{% endif %}</div>
<div class="carte"><h2>🎁 Top 5 bienfaiteurs (pourboires validés)</h2>
<table><tr><th>#</th><th>Pseudo</th><th>Pourboires</th><th>Total {{ devise }}</th></tr>
{% for t in g['top_bienfaiteurs'] %}<tr><td>{{ loop.index }}</td><td><b>{{ t['pseudo'] }}</b></td><td>{{ t['n'] }}</td><td><b>{{ t['total'] }}</b></td></tr>
{% endfor %}</table>
{% if not g['top_bienfaiteurs'] %}<p class="muet">Aucun pourboire validé encore.</p>{% endif %}</div>
{% endblock %}"""

TEMPLATES["recherche.html"] = """{% block contenu %}
<div class="carte"><h1>Recherche temps réel (moteurs gratuits)</h1>
<form method="get" class="row"><input name="q" value="{{ q }}" placeholder="Votre requête" style="max-width:420px"><button>Chercher</button></form>
<p class="muet">Moteurs interrogés : SearXNG, Google News (RSS), Wikipédia, DuckDuckGo. Chaque résultat affiché est un résultat réel avec son lien ; rien n'est inventé.</p></div>
{% if q %}<div class="carte"><h2>Résultats pour « {{ q }} »</h2>
<p class="muet">Collecté le {{ data['collecte_le'] }} — SearXNG {{ data['compte']['searxng'] }} · Google News {{ data['compte']['google_news'] }} · Wikipédia {{ data['compte']['wikipedia'] }} · DuckDuckGo {{ data['compte']['duckduckgo'] }}</p>
{{ resultats_html|safe }}</div>{% endif %}
{% endblock %}"""

TEMPLATES["chat.html"] = """{% block contenu %}
<div class="carte"><h1>Assistant IA — ancré sur le réel</h1>
<p class="muet">L'IA reçoit un bloc de résultats réellement collectés (avec liens). Si rien n'est trouvé, elle l'avoue au lieu d'inventer.</p>
<form method="post" action="{{ url_for('chat_envoyer') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <textarea name="question" placeholder="Votre question" required></textarea>
  <p><button>Interroger</button></p>
</form></div>
{% if question %}<div class="carte"><h2>Question : {{ question }}</h2>
{% if reponse %}<div style="white-space:pre-wrap">{{ reponse }}</div>{% endif %}
{% if avertissement %}<p class="flash">{{ avertissement }}</p>{% endif %}
{% if data %}<h3>Résultats réellement collectés</h3>{{ resultats_html|safe }}{% endif %}
</div>{% endif %}
{% endblock %}"""

TEMPLATES["parametres.html"] = """{% block contenu %}
<div class="carte"><h1>⚙️ Paramètres</h1>
<p class="muet">Préférences du compte, sécurité et informations légales. Connecté en tant que <b>{{ me['pseudo'] }}</b>{% if me['est_admin'] %} <span class="badge">administrateur</span>{% endif %}.</p></div>
<div class="carte"><h2>👤 Profil et compte</h2>
<form method="post" action="{{ url_for('parametres_profil') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Surnom unique (identifiant de connexion — 6 lettres)</label><input name="pseudo" value="{{ me['pseudo'] }}" required minlength="6" maxlength="6" pattern="[A-Za-zÀ-ÿ]{6}" title="Exactement 6 lettres, sans chiffres ni symboles"></p>
  <p class="muet">Le surnom comporte exactement 6 lettres, sans chiffres ni symboles (ex. : koffia). Les comptes créés avant cette règle ne sont pas touchés.</p>
  <p><label>Bio publique (280 caractères maximum)</label><textarea name="bio" maxlength="280" placeholder="Présentez-vous…">{{ me['bio'] }}</textarea></p>
  <p><label>Numéro Mobile Money de retrait</label><input name="payout_phone" value="{{ me['payout_phone'] }}" placeholder="+229 01 23 45 67 89">
     <label>Opérateur</label><select name="payout_op"><option value="MTN" {{ 'selected' if me['payout_op'] == 'MTN' }}>MTN</option><option value="Moov" {{ 'selected' if me['payout_op'] == 'Moov' }}>Moov</option></select></p>
  <p class="row"><button>Enregistrer le profil</button></p>
</form></div>
<div class="carte"><h2>🔔 Notifications, langue et thème</h2>
<form method="post" action="{{ url_for('parametres_prefs') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label><input type="checkbox" name="notifs_messages" value="1" style="width:auto" {{ 'checked' if prefs['notifs_messages'] }}>
     Afficher la pastille des messages non lus dans la navigation</label></p>
  <p><label><input type="checkbox" name="notifs_portefeuille" value="1" style="width:auto" {{ 'checked' if prefs['notifs_portefeuille'] }}>
     Afficher le rappel du solde à réclamer sur le fil d'accueil</label></p>
  <p><label>Langue de l'interface</label>
     <select name="langue"><option value="fr" {{ 'selected' if prefs['langue'] == 'fr' }}>Français</option><option value="en" {{ 'selected' if prefs['langue'] == 'en' }}>English</option></select>
     <span class="muet">Seul le français est entièrement traduit pour le moment.</span></p>
  <p><label>Thème d'affichage</label>
     <select name="theme" onchange="try{localStorage.setItem('tbm-theme',this.value)}catch(e){}"><option value="sombre" {{ 'selected' if prefs['theme'] == 'sombre' }}>Sombre (par défaut)</option><option value="clair" {{ 'selected' if prefs['theme'] == 'clair' }}>Clair</option><option value="sepia" {{ 'selected' if prefs['theme'] == 'sepia' }}>Sépia (lecture au papier)</option></select></p>
  <p><label>Mode lecteur</label> <button type="button" class="btn-sec" onclick="basculer_lecteur()">Activer / quitter le mode lecteur (touche R)</button> <span class="muet">Navigation masquée, police agrandie, colonne étroite — pour lire longuement.</span></p>
  <p><label><input type="checkbox" name="refus_groupes" value="1" style="width:auto" {{ 'checked' if prefs['refus_groupes'] }}>
     Refuser les messages groupés de l'administration (droit de retrait)</label></p>
  <p class="muet">Les messages individuels de l'administrateur vous parviennent toujours, même avec cette option cochée.</p>
  <p class="row"><button>Enregistrer les préférences</button></p>
</form></div>
<div class="carte"><h2>🔒 Sécurité</h2>
<form method="post" action="{{ url_for('parametres_mdp') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Ancien mot de passe</label><input name="ancien" type="password" required></p>
  <p class="row"><span style="flex:1"><label>Nouveau mot de passe</label><input name="nouveau" type="password" required minlength="6"></span>
     <span style="flex:1"><label>Confirmation</label><input name="confirmation" type="password" required minlength="6"></span></p>
  <p class="row"><button>Changer le mot de passe</button><span class="muet">6 caractères minimum.</span></p>
</form></div>
<div class="carte"><h2>📜 Informations légales</h2>
  <p><a class="btn btn-sec" href="{{ url_for('cgu') }}">Conditions d'utilisation (CGU)</a>
     <a class="btn btn-sec" href="{{ url_for('confidentialite') }}">Politique de confidentialité</a></p>
  <p class="muet">Toute violation des CGU expose à un ban définitif, à la confiscation des soldes et, pour les faits pénaux, à un signalement aux autorités compétentes.</p></div>
<div class="carte"><h2>🗑️ Suppression du compte</h2>
<p class="muet">Action <b>définitive et irréversible</b> : profil, publications, messages, plaintes, écritures de portefeuille et préférences sont effacés. Les soldes en attente de versement sont perdus — demandez votre retrait à l'administrateur AVANT de supprimer. Le compte administrateur ne peut pas être supprimé ici.</p>
<form method="post" action="{{ url_for('parametres_supprimer') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Mot de passe (confirmation)</label><input name="mot_de_passe" type="password" required></p>
  <p><label>Tapez SUPPRIMER pour confirmer</label><input name="confirmation" placeholder="SUPPRIMER" required></p>
  <p class="row"><button class="btn-rouge">Supprimer mon compte définitivement</button></p>
</form></div>
{% endblock %}"""

TEMPLATES["cgu.html"] = """{% block contenu %}
<div class="carte"><h1>Conditions d'utilisation (CGU)</h1>
<p class="muet">En vigueur depuis le {{ date_effet }} — application ToutBot Mundo. L'inscription vaut acceptation pleine et entière des présentes conditions. Elles sont expressément rédigées de manière stricte : leur violation entraîne les sanctions listées, sans préavis ni négociation.</p></div>
<div class="carte"><h2>Article 1 — Objet et accès</h2>
<p>ToutBot Mundo est un réseau social 100 % textuel avec portefeuille virtuel (tickets et FCFA), abonnements payants entre membres et validation des paiements par l'administrateur. L'accès nécessite un compte (pseudo ou numéro de téléphone) et un mot de passe. L'usage de l'application implique le respect absolu des présentes CGU.</p></div>
<div class="carte"><h2>Article 2 — Contenu sexuel ou pornographique</h2>
<p><b>Interdiction :</b> toute publication, message ou échange à caractère sexuel ou pornographique.</p>
<p><b>Sanction :</b> ban définitif immédiat.</p></div>
<div class="carte"><h2>Article 3 — Contenu sexuel ou exploitant des mineurs</h2>
<p><b>Interdiction :</b> tout contenu sexuel, d'exploitation ou de mise en danger concernant des mineurs, sous quelque forme que ce soit.</p>
<p><b>Sanction :</b> ban définitif immédiat <b>et signalement systématique aux autorités judiciaires et aux plateformes spécialisées</b>, sans exception et sans avertissement préalable. Aucune tolérance.</p></div>
<div class="carte"><h2>Article 4 — Haine, racisme, violence</h2>
<p><b>Interdiction :</b> tout propos ou contenu incitant à la haine, à la discrimination raciale, ethnique, religieuse ou sexiste, ou faisant l'apologie de la violence.</p>
<p><b>Sanction :</b> ban définitif et suppression de toutes les publications concernées.</p></div>
<div class="carte"><h2>Article 5 — Menaces, harcèlement, doxxing</h2>
<p><b>Interdiction :</b> menaces, harcèlement, intoxication, divulgation d'informations personnelles d'autrui (doxxing) — adresse, numéro, documents, localisation.</p>
<p><b>Sanction :</b> ban définitif ; signalement aux autorités lorsque les faits constituent une infraction pénale.</p></div>
<div class="carte"><h2>Article 6 — Escroquerie et fraude</h2>
<p><b>Interdiction :</b> escroquerie, fausse promesse, usurpation d'identité, faux justificatifs de paiement Mobile Money, toute manœuvre visant à obtenir des fonds ou des données par tromperie.</p>
<p><b>Sanction :</b> ban définitif, confiscation de la totalité des soldes (tickets et FCFA) et signalement aux autorités.</p></div>
<div class="carte"><h2>Article 7 — Drogues, armes, activités criminelles</h2>
<p><b>Interdiction :</b> toute mention, promotion, vente ou facilitation liée aux stupéfiants, aux armes, et plus généralement aux activités criminelles.</p>
<p><b>Sanction :</b> ban définitif et signalement aux autorités compétentes.</p></div>
<div class="carte"><h2>Article 8 — Contournement des paiements</h2>
<p><b>Interdiction :</b> tout paiement, promesse de paiement ou échange d'argent effectué en dehors des numéros Mobile Money officiels de l'administrateur (MTN et Moov affichés dans l'application), y compris le paiement direct créateur ↔ abonné.</p>
<p><b>Sanction :</b> ban définitif et confiscation des soldes des comptes impliqués.</p></div>
<div class="carte"><h2>Article 9 — Multi-comptes frauduleux</h2>
<p><b>Interdiction :</b> la création ou l'utilisation de plusieurs comptes par une même personne pour se contacter, se payer, gonfler artificiellement ses revenus ou contourner une sanction.</p>
<p><b>Sanction :</b> ban définitif de TOUS les comptes liés et confiscation des soldes.</p></div>
<div class="carte"><h2>Article 10 — Spam et publicité non autorisée</h2>
<p><b>Interdiction :</b> spam, publications répétées, publicité, démarchage ou promotion commerciale sans l'accord écrit préalable de l'administrateur.</p>
<p><b>Sanction :</b> suppression des contenus, puis ban définitif en cas de récidive.</p></div>
<div class="carte"><h2>Article 11 — Propriété intellectuelle</h2>
<p><b>Interdiction :</b> publier ou diffuser un contenu dont vous ne détenez pas les droits (plagiat, contrefaçon, réutilisation d'œuvres protégées).</p>
<p><b>Sanction :</b> suppression du contenu, puis ban définitif en cas de récidive ; les titulaires de droits peuvent signaler directement l'administrateur.</p></div>
<div class="carte"><h2>Article 12 — Accès non autorisé et scraping</h2>
<p><b>Interdiction :</b> accès non autorisé au système, extraction automatisée (scraping), bots, exploitation de failles, tentatives d'intrusion ou de surcharge du service.</p>
<p><b>Sanction :</b> ban définitif immédiat, confiscation des soldes et poursuites pénales (signalement aux autorités).</p></div>
<div class="carte"><h2>Article 13 — Blanchiment d'argent</h2>
<p><b>Interdiction :</b> utiliser le portefeuille, les tickets, les abonnements ou les pourboires pour dissimuler l'origine de fonds, structurer des transactions ou blanchir de l'argent.</p>
<p><b>Sanction :</b> ban définitif, confiscation de la totalité des soldes et signalement obligatoire aux autorités financières compétentes (BCEAO / CELLMED selon les cas).</p></div>
<div class="carte"><h2>Article 14 — Abus du canal de plaintes et harcèlement de l'administrateur</h2>
<p><b>Interdiction :</b> plaintes mensongères, répétées ou calomnieuses, injures, menaces ou harcèlement de l'administrateur via le canal de plaintes ou la messagerie.</p>
<p><b>Sanction :</b> clôture sans suite des plaintes abusives, puis ban définitif en cas de récidive.</p></div>
<div class="carte"><h2>Article 15 — Contenu appelant au suicide ou à l'automutilation</h2>
<p><b>Interdiction :</b> tout contenu faisant l'apologie, encourageant ou détaillant le suicide ou l'automutilation.</p>
<p><b>Sanction :</b> suppression immédiate du contenu, ban définitif le cas échéant, et <b>signalement aux services d'urgence et de prévention</b> afin qu'une aide puisse être apportée à la personne en danger.</p></div>
<div class="carte"><h2>Article 16 — Autre conduite contraire</h2>
<p>Toute autre conduite jugée contraire à l'esprit du service, à l'ordre public ou aux bonnes mœurs par l'administrateur est également interdite. <b>L'administrateur dispose d'un pouvoir discrétionnaire complet</b> pour interpréter les présentes CGU, apprécier chaque situation et appliquer la sanction appropriée, y compris pour tout comportement non expressément listé ci-dessus.</p></div>
<div class="carte"><h2>Article 17 — Barème des sanctions et recours</h2>
<p><b>Les sanctions applicables sont :</b> suppression de contenu ; ban définitif (perte de l'accès au compte et à tous ses services) ; confiscation des soldes (tickets et FCFA) ; signalement aux autorités judiciaires ou financières pour les faits pénalement qualifiables.</p>
<p>Les décisions de ban définitif et de confiscation sont <b>irrévocables</b>. Le seul canal de recours est la messagerie de l'administrateur. Les montants déjà versés aux créateurs ne sont jamais restitués en cas de ban pour fraude.</p></div>
<div class="carte"><h2>Article 18 — Droit applicable</h2>
<p>Les présentes CGU sont soumises au droit béninois et, le cas échéant, à la réglementation UEMOA (BCEAO) pour les aspects de monnaie électronique. Le présent document est un cadre contractuel fourni à titre indicatif : <b>faire relire et valider ces CGU par un avocat avant toute mise en production</b> reste indispensable.</p></div>
{% endblock %}"""

TEMPLATES["confidentialite.html"] = """{% block contenu %}
<div class="carte"><h1>Politique de confidentialité</h1>
<p class="muet">En vigueur depuis le {{ date_effet }} — application ToutBot Mundo. Cette politique explique quelles données sont collectées, pourquoi, combien de temps elles sont conservées et quels sont vos droits.</p></div>
<div class="carte"><h2>1. Responsable du traitement</h2>
<p>Le responsable du traitement est l'exploitant de l'application (l'administrateur du service, joignable via la messagerie intégrée — voir section « Contact »).</p></div>
<div class="carte"><h2>2. Données collectées et finalités</h2>
<table><tr><th>Donnée</th><th>Finalité</th></tr>
<tr><td><b>Compte</b> : pseudo, numéro de téléphone, mot de passe (haché, jamais en clair)</td><td>Création et sécurisation du compte, connexion, identification des paiements Mobile Money</td></tr>
<tr><td><b>Publications</b> : textes publiés sur le fil</td><td>Fonctionnement du réseau social (contenu visible publiquement par les membres connectés)</td></tr>
<tr><td><b>Transactions</b> : abonnements, pourboires, commissions, références de paiement</td><td>Traitement des paiements, portefeuille virtuel, export comptable, obligations fiscales de l'exploitant</td></tr>
<tr><td><b>Messagerie</b> : échanges avec l'administrateur et autres membres</td><td>Traitement des demandes de retrait et de l'assistance</td></tr>
<tr><td><b>Plaintes</b> : sujet, exposé, réponse apportée</td><td>Traitement des réclamations et prévention des abus</td></tr>
<tr><td><b>Mesures anti-fraude</b> : liens entre comptes, historique de connexion</td><td>Détection du multi-comptes et des fraudes (fondement : intérêt légitime de sécurisation)</td></tr>
<tr><td><b>Préférences</b> : notifications, langue, thème</td><td>Personnalisation de l'interface (page Paramètres)</td></tr>
<tr><td><b>Cookies techniques</b> : session signée, jeton anti-CSRF</td><td>Sécurité de la navigation — strictement nécessaires, aucun traceur publicitaire ni statistique tierce</td></tr></table>
<p class="muet">La recherche temps réel et l'IA interrogent des services externes (SearXNG, Google News, Wikipédia, DuckDuckGo, moteur d'IA) : la requête saisie est transmise à ces services. N'inscrivez pas de données personnelles dans une recherche.</p></div>
<div class="carte"><h2>3. Durées de conservation</h2>
<table><tr><th>Catégorie</th><th>Durée</th></tr>
<tr><td>Compte actif</td><td>Pendant toute la durée d'utilisation du service</td></tr>
<tr><td>Compte supprimé par l'utilisateur</td><td>Suppression immédiate et définitive (droit à l'effacement)</td></tr>
<tr><td>Compte banni</td><td>Conservation des identifiants et du motif du ban pendant 3 ans, pour prévenir les recréations de comptes et justifier les sanctions</td></tr>
<tr><td>Transactions et écritures comptables</td><td>10 ans (obligation légale de conservation des pièces comptables)</td></tr>
<tr><td>Messages et plaintes</td><td>2 ans après clôture de la discussion ou de la plainte</td></tr>
<tr><td>Signalements aux autorités</td><td>Conservés jusqu'à clôture de la procédure concernée</td></tr></table></div>
<div class="carte"><h2>4. Partage des données</h2>
<p><b>Vos données ne sont jamais vendues.</b> Elles peuvent être communiquées :</p>
<p>• aux <b>autorités judiciaires, financières ou répressives</b> (police, gendarmerie, justice, BCEAO / CELLMED) sur réquisition légale, ou de manière proactive lorsque les CGU prévoient un signalement (contenus sur mineurs, escroquerie, blanchiment, menaces, etc.) ;</p>
<p>• aux prestataires techniques strictement nécessaires au service (opérateurs Mobile Money pour les transferts, moteurs de recherche et service d'IA mentionnés ci-dessus), qui ne reçoivent que la requête ou les données strictement utiles.</p></div>
<div class="carte"><h2>5. Vos droits</h2>
<p>Conformément au RGPD (règlement européen 2016/679) et aux lois africaines de protection des données personnelles (au Bénin : loi n° 2017-20 et APDP), vous disposez des droits suivants :</p>
<p>• <b>droit d'accès</b> : obtenir la copie des données vous concernant ;<br>
• <b>droit de rectification</b> : corriger vos informations (page Paramètres — profil) ;<br>
• <b>droit à l'effacement</b> : supprimer votre compte et vos données (page Paramètres — « Supprimer mon compte ») ;<br>
• <b>droit à la portabilité</b> : recevoir vos publications et écritures dans un format lisible (export comptable sur demande) ;<br>
• <b>droit d'opposition et de limitation</b> : vous opposer à un traitement pour des motifs légitimes ;<br>
• <b>droit de retirer votre consentement</b> à tout moment pour les traitements qui en dépendent.</p>
<p>Pour exercer ces droits, écrivez à l'administrateur via la messagerie intégrée (section « Messages »). Vous pouvez également saisir l'autorité de contrôle de votre pays (au Bénin : l'APDP).</p></div>
<div class="carte"><h2>6. Sécurité</h2>
<p>Mots de passe hachés (PBKDF2), sessions signées, jetons anti-CSRF sur tous les formulaires, journal d'audit de chaque écriture financière. Avant mise en production : HTTPS obligatoire, clé secrète forte, sauvegardes chiffrées et limitation des tentatives de connexion.</p></div>
<div class="carte"><h2>7. Contact</h2>
<p>Toute question ou demande relative à vos données personnelles s'effectue <b>exclusivement via la messagerie de l'application</b> (section « Messages » → discussion avec l'administrateur) ou le canal de plaintes (section « Plaintes »). L'administrateur s'engage à répondre dans un délai raisonnable de 30 jours.</p>
<p class="muet">Cette politique est fournie à titre indicatif : <b>faire relire et valider ce document par un avocat ou un spécialiste de la protection des données avant toute mise en production</b>, en particulier pour l'adapter à votre juridiction et à vos flux Mobile Money réels.</p></div>
{% endblock %}"""

# Chargeur Jinja : tous les gabarits vivent dans TEMPLATES (mono-fichier).
app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES)])
app.jinja_env.globals["est_en_ligne"] = est_en_ligne
app.jinja_env.globals["svg_barres_30_jours"] = svg_barres_30_jours

# =============================================================================
# 🛣️ ROUTES
# =============================================================================
# Texte affiché en tête de chaque utilisateur entrant qui vient de terminer
# son inscription dans l'application ToutBot Mundo (reproduit mot pour mot).
ANNONCE_INSCRIPTION = (
    "ici chaque abonnement est payant encaisse le Kista_le money qui change tout "
    "et envoie ton numéro fin du mois a l'administrateur à travers sa base de "
    "discussion entre utilisateurs et administrateurs pour prendre ta chaudta cash."
)

# V1.4 — Modèles de messages de fin de mois : l'administrateur les copie-colle
# dans la discussion de chaque abonné (bouton « Copier » du tableau de bord).
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]

def libelle_mois(quand: Optional[_dt.date] = None) -> str:
    """Libellé français du mois en cours, ex. « septembre 2026 »."""
    d = quand or _dt.date.today()
    return "%s %d" % (MOIS_FR[d.month - 1], d.year)

MODELE_MESSAGE_PAYE = (
    "Bonjour,\n\n"
    "Nous vous informons que le paiement correspondant à votre abonnement pour le "
    "mois de {mois} a bien été effectué.\n\n"
    "Le montant de [MONTANT] FCFA a été crédité sur votre portefeuille en tickets. "
    "Vous pouvez vérifier le détail dans votre espace personnel (menu Portefeuille, "
    "grand livre des écritures).\n\n"
    "Pour toute question, répondez directement ici : notre équipe vous répond dans "
    "cette même discussion.\n\n"
    "Merci pour votre confiance et votre fidélité.\n\n"
    "L'équipe d'administration — ToutBot Mundo"
)

MODELE_MESSAGE_ATTENTE = (
    "Bonjour,\n\n"
    "Nous vous informons que votre paiement pour le mois de {mois} est actuellement "
    "en cours de traitement.\n\n"
    "Dès qu'il sera finalisé, vous recevrez une confirmation dans cet espace, ainsi "
    "qu'un message de créditation sur votre portefeuille.\n\n"
    "Rappel : les versements se font sur nos numéros Mobile Money (MTN ou Moov) "
    "affichés en carré copier-coller sur le fil d'accueil. Pensez à indiquer votre "
    "référence de paiement [RÉF.] pour accélérer la validation.\n\n"
    "Merci pour votre patience et votre compréhension.\n\n"
    "L'équipe d'administration — ToutBot Mundo"
)

# Petit carré copier-coller : réponse type envoyée aux utilisateurs qui veulent
# des nouvelles de leur transaction de fin de mois.
MODELE_REPONSE_TRANSACTION = (
    "Bonjour,\n\n"
    "Votre demande concernant votre transaction de fin de mois a bien été reçue.\n\n"
    "Point de votre situation : [MONTANT] FCFA — [PAYÉ / EN COURS DE TRAITEMENT]. "
    "Le détail figure dans votre espace personnel, section Portefeuille.\n\n"
    "Si un écart apparaît, indiquez ici votre référence de paiement Mobile Money "
    "(MTN ou Moov) : nous reviendrons vers vous sous 72 heures.\n\n"
    "L'équipe d'administration — ToutBot Mundo"
)

# Modèle de relance des impayés : envoyé aux abonnés dont une écriture de
# portefeuille est restée en attente plus de DELAI_RELANCE_JOURS jours.
DELAI_RELANCE_JOURS = 15

MODELE_RELANDE_IMPAYES = (
    "Bonjour,\n\n"
    "Nos services constatent que votre paiement lié à l'abonnement du mois de "
    "{mois} n'a pas encore été régularisé.\n\n"
    "Merci de régler la somme de [MONTANT] FCFA sur nos numéros Mobile Money "
    "(MTN ou Moov) affichés en carré copier-coller sur le fil d'accueil, puis de "
    "communiquer votre référence de paiement [RÉF.] ici même.\n\n"
    "Sans régularisation sous [DÉLAI] jours, l'accès aux services payants restera "
    "suspendu conformément aux conditions d'utilisation.\n\n"
    "L'équipe d'administration — ToutBot Mundo"
)

# Presets de l'espace d'envoi groupé : type de message -> corps proposé.
PRESETS_ENVOI: Dict[str, str] = {
    "paye": MODELE_MESSAGE_PAYE,
    "attente": MODELE_MESSAGE_ATTENTE,
    "relance": MODELE_RELANDE_IMPAYES,
    "transaction": MODELE_REPONSE_TRANSACTION,
    "personnalise": "",
}

# Prompt système de l'IA d'assistance (plaintes + réponses automatiques groupées).
PROMPT_ASSISTANCE = (
    "Tu es 'Mundo', l'assistant officiel de l'équipe d'administration de ToutBot Mundo, "
    "un réseau social 100 % texte avec portefeuille en tickets, abonnements payants en FCFA, "
    "modération avancée et moteur d'analyse. Tu réponds aux utilisateurs au nom de l'administration.\n"
    "RÈGLES ABSOLUES :\n"
    "- Tu réponds en français, ton poli, bref et factuel (5 phrases maximum).\n"
    "- Tu n'inventes JAMAIS aucun montant, référence, date, nom d'utilisateur ou engagement précis.\n"
    "- Tu appliques d'abord les RÈGLES PARTICULIÈRES de l'administrateur s'il en fournit.\n"
    "- Si tu ne peux pas trancher, tu dis que la demande est transmise à "
    "l'administrateur, qui répondra dans la même discussion sous 72 heures.\n"
    "- Tu ne promets jamais un paiement : tu indiques seulement l'état annoncé.\n"
    "- Tu n'exposes JAMAIS les données personnelles d'un utilisateur à un autre.\n"
    "- Tu n'exécutes aucune action (pas d'écriture base, pas d'envoi Mobile Money, pas de modération "
    "automatique) : tu analyses, tu conseilles, tu rédiges. L'administrateur confirme toujours.\n"
    "PERSONNALITÉ : loyal, sobre, proactif. Tu signales une anomalie visible "
    "(plainte en attente, solde bloqué, comportement suspect) AVANT qu'on te la demande.\n"
    "Réponse limitée : 200 mots maximum, sauf demande explicite de rapport détaillé."
)

# ============================================================================
# 🆕 v2 — PROMPTS DE L'IA PERSONNELLE DE L'ADMINISTRATEUR
# Trois variantes prêtes à brancher sur interroger_llm(...)
# ============================================================================
PROMPT_ADMIN_SYSTEME = (
    "Tu es 'Mundo', l'IA PERSONNELLE de l'administrateur fondateur de TOUTBOT MUNDO "
    "(réseau social 100 % texte, portefeuille Mobile Money, modération avancée).\n"
    "Tu es son bras droit : seul l'administrateur te parle.\n"
    "MISSION :\n"
    "  • Analyser la plateforme (utilisateurs, publications, finances, modération, sécurité).\n"
    "  • Préparer les décisions : qui valider, quoi bloquer, où est le risque.\n"
    "  • Rédiger : réponses aux utilisateurs, annonces, résumés quotidiens.\n"
    "  • Surveiller le monde : actualités des 195 pays via flux temps réel.\n"
    "TON : direct, loyal, sobre. Tu tutoies l'administrateur, tu vas droit au but, "
    "tu signales les anomalies AVANT qu'on te le demande, "
    "tu proposes toujours 1 à 3 actions concrètes numérotées avec la page admin concernée.\n"
    "RÈGLES ABSOLUES :\n"
    "  1. ZÉRO invention : aucun chiffre, solde, nom ou date absent du contexte.\n"
    "  2. Confidentialité stricte : aucune donnée personnelle d'un utilisateur à un autre.\n"
    "  3. Aucun conseil financier garanti : les paiements Mobile Money restent validés humainement.\n"
    "  4. Aucune exécution : l'admin confirme toujours avant écriture, envoi ou sanction.\n"
    "  5. 200 mots maximum, sauf demande explicite de rapport détaillé.\n"
    "CONTEXTE FOURNI : %s"
)

PROMPT_ADMIN_ANALYSE = (
    "Tu es 'Mundo', l'IA d'analyse du tableau de bord administrateur TOUTBOT MUNDO.\n"
    "On te fournit un INSTANTANÉ JSON du tableau (compteurs, soldes en attente, "
    "plaintes, alertes, événements récents). Tu produis :\n"
    "  1) 🔎 Diagnostic (3 phrases max) — ce qui va bien / ce qui coince.\n"
    "  2) ⚠️ Les 3 risques les plus urgents, chiffrés UNIQUEMENT avec les chiffres du JSON.\n"
    "  3) ✅ 3 actions numérotées, prêtes à exécuter, avec la page admin concernée.\n"
    "  4) 🌍 Une ligne « contexte mondial » si des flux d'actualités sont joints.\n"
    "INTERDITS : inventer un chiffre absent du JSON, promettre un revenu, "
    "conseiller une sanction sans preuve du journal de modération.\n"
    "INSTANTANÉ DU TABLEAU : %s"
)

PROMPT_ADMIN_SECURITE = (
    "Tu es 'Mundo', l'IA d'assistance à la sécurité de TOUTBOT MUNDO.\n"
    "Tu reçois un rapport du NoyauSecuriteIA (tentative d'injection SQL/XSS, "
    "IP en liste noire, événements). Tu classes la menace (faible / moyenne / élevée), "
    "tu expliques en une phrase ce qui s'est passé, et tu donnes 2 contre-mesures "
    "concrètes dans l'ordre d'urgence.\n"
    "Tu ne nommes jamais une IP comme coupable certaine : tu écris 'suspecte'. "
    "Tu ne modifies rien toi-même.\n"
    "RAPPORT SÉCURITÉ : %s"
)

@app.route("/")
def fil():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    posts = db().execute(
        "SELECT p.*, u.pseudo, u.est_admin, u.derniere_activite,"
        " (SELECT COUNT(*) FROM follows f WHERE f.suivi_id = p.auteur_id) AS nb_abonnes,"
        " (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS nb_aimes,"
        " (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS nb_comments,"
        " EXISTS(SELECT 1 FROM likes l2 WHERE l2.post_id = p.id AND l2.user_id = ?) AS j_aime"
        " FROM posts p JOIN users u ON u.id = p.auteur_id"
        " ORDER BY p.id DESC LIMIT 200", (me["id"],)).fetchall()
    # Commentaires regroupés par publication, avec badge « abonné » si le commentateur
    # suit l'auteur de la publication (les non-abonnés peuvent commenter aussi).
    commentaires: Dict[int, List[Dict[str, Any]]] = {}
    if posts:
        ids = [p["id"] for p in posts]
        marques = ",".join("?" * len(ids))
        for c in db().execute(
                f"SELECT c.*, u.pseudo,"
                f" EXISTS(SELECT 1 FROM follows f2 WHERE f2.suiveur_id = c.auteur_id"
                f" AND f2.suivi_id = p2.auteur_id) AS abonne"
                f" FROM comments c JOIN users u ON u.id = c.auteur_id"
                f" JOIN posts p2 ON p2.id = c.post_id"
                f" WHERE c.post_id IN ({marques}) ORDER BY c.id ASC", ids).fetchall():
            commentaires.setdefault(c["post_id"], []).append(dict(c))
    mes_suivis = {r["suivi_id"] for r in db().execute(
        "SELECT suivi_id FROM follows WHERE suiveur_id = ?", (me["id"],)).fetchall()}
    prefs = prefs_de(me["id"])
    bandeau = ""
    if prefs["notifs_portefeuille"]:
        s = solde(me["id"])
        if s["fcfa_attente"] > 0:
            bandeau = ("💰 Rappel : %g %s en attente de versement — demandez votre retrait "
                       "à l'administrateur via la discussion." % (s["fcfa_attente"], CONFIG["DEVISE"]))
    return page("fil.html", titre="Fil mondial", posts=posts, commentaires=commentaires,
                mes_suivis=mes_suivis, bandeau=bandeau)

@app.route("/publier", methods=["POST"])
def publier():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    corps = (request.form.get("corps") or "").strip()
    if not corps:
        session["flash"] = "Publication vide : elle n'a pas été enregistrée."
        return redirect(url_for("fil"))
    conn = db()
    conn.execute("INSERT INTO posts (auteur_id, corps, cree_le) VALUES (?,?,?)",
                 (me["id"], corps[:5000], maintenant()))
    conn.commit()
    journal_action("publication", cible=me["pseudo"], details=corps[:80], utilisateur=me["id"])
    journal_action2(me["id"], "publication", objet="texte publié sur le fil", details=corps[:80])
    return redirect(url_for("fil"))

@app.route("/p/aime/<int:post_id>", methods=["POST"])
def aimer(post_id: int):
    """Bascule le « J'aime » de l'utilisateur sur une publication (y compris celle de l'admin)."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    if db().execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        return "Publication inconnue.", 404
    basculer_like(post_id, me["id"])
    journal_action("j_aime", cible="publication #%d" % post_id, utilisateur=me["id"])
    journal_action2(me["id"], "j_aime", objet="publication #%d" % post_id)
    return redirect(request.referrer or url_for("fil"))

@app.route("/p/commenter/<int:post_id>", methods=["POST"])
def commenter(post_id: int):
    """Commente une publication : ouvert aux abonnés ET aux non-abonnés."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    if db().execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        return "Publication inconnue.", 404
    corps = (request.form.get("corps") or "").strip()
    if corps:
        ajouter_commentaire(post_id, me["id"], corps)
        journal_action("commentaire", cible="publication #%d" % post_id,
                       details=corps[:80], utilisateur=me["id"])
        journal_action2(me["id"], "commentaire", objet="publication #%d" % post_id,
                        details=corps[:80])
    return redirect(request.referrer or url_for("fil") + "#comms-%d" % post_id)

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if request.method == "POST":
        try:
            pseudo = valider_surnom(request.form.get("identifiant", ""))
            uid = creer_utilisateur(request.form.get("telephone", ""),
                                    pseudo,
                                    request.form.get("mot_de_passe", ""))
            # V5 — programme d'affiliation : liaison parrain/filleul à l'inscription
            parrain_pseudo = (request.form.get("parrain", "") or "").strip()
            if parrain_pseudo:
                parrain = utilisateur_par_identifiant(parrain_pseudo)
                if parrain is not None and parrain["id"] != uid:
                    conn = db()
                    conn.execute("UPDATE users SET parrain_id = ? WHERE id = ?", (parrain["id"], uid))
                    conn.commit()
                    notifier(parrain["id"], "affiliation",
                             f"@{pseudo} est devenu votre filleul.", f"/u/{pseudo}")
            session.clear()
            session["uid"] = uid
            session["csrf"] = secrets.token_hex(16)
            # V1.2 — bandeau d'accueil affiché à chaque utilisateur venant de
            # terminer son inscription dans l'application ToutBot Mundo.
            # Texte inséré EXACTEMENT tel que fourni par l'administrateur.
            session["flash"] = ANNONCE_INSCRIPTION
            journal_action2(uid, "connexion", objet="compte créé — inscription réussie",
                            details="Bienvenue : vos activités sont consignées dans votre historique personnel.")
            # Version française corrigée proposée (commentaire uniquement) :
            # « Ici, chaque abonnement est payant : il encaisse le Kista_le Money,
            #  ce qui change tout. Envoie ton numéro, à la fin du mois, à
            #  l'administrateur, à travers sa base de discussion entre
            #  utilisateurs et administrateurs, pour prendre ta chaudta cash. »
            # (le mot « chaudta » reste à clarifier par l'administrateur)
            return redirect(url_for("fil"))
        except ValueError as exc:
            session["flash"] = str(exc)
    return page("auth_v5.html", titre="Inscription", inscription=True, bouton="Créer mon compte")

@app.route("/connexion", methods=["GET", "POST"])
def connexion():
    if request.method == "POST":
        u = utilisateur_par_identifiant(request.form.get("identifiant", ""))
        if u and check_password_hash(u["mot_de_passe"], request.form.get("mot_de_passe", "")):
            if u["bloque"]:
                session["flash"] = "Ce compte est bloqué."
                return page("auth.html", titre="Connexion", inscription=False, bouton="Se connecter")
            session.clear()
            session["uid"] = u["id"]
            session["csrf"] = secrets.token_hex(16)
            journal_action("connexion", cible=u["pseudo"], details="Connexion réussie", utilisateur=u["id"])
            journal_action2(u["id"], "connexion", objet="connexion réussie",
                            details="Bienvenue — vos activités sont consignées dans votre historique personnel.")
            return redirect(url_for("fil"))
        session["flash"] = "Identifiant ou mot de passe incorrect."
    return page("auth.html", titre="Connexion", inscription=False, bouton="Se connecter")

@app.route("/deconnexion", methods=["POST"])
def deconnexion():
    uid = session.get("uid")
    session.clear()
    journal_action("deconnexion", utilisateur=uid)
    journal_action2(uid, "deconnexion", objet="déconnexion du compte")
    return redirect(url_for("connexion"))

@app.route("/tarifs")
def tarifs():
    """Page PUBLIQUE : paliers, commissions et numéros lus de la configuration."""
    grille = []
    for pal in PALIERS:
        commission, net, _ = calcul_commission(pal)
        grille.append({"palier": pal, "commission": commission, "net": net})
    return page("tarifs.html", titre="Tarifs et paiements", grille=grille)

@app.route("/u/<pseudo>")
def profil(pseudo: str):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    cible = db().execute("SELECT * FROM users WHERE pseudo = ?", (pseudo,)).fetchone()
    if cible is None:
        return "Profil inconnu.", 404
    posts = db().execute("SELECT * FROM posts WHERE auteur_id = ? ORDER BY id DESC LIMIT 100",
                         (cible["id"],)).fetchall()
    suivis = db().execute("SELECT COUNT(*) AS n FROM follows WHERE suiveur_id = ?", (cible["id"],)).fetchone()["n"]
    abonnes = db().execute("SELECT COUNT(*) AS n FROM follows WHERE suivi_id = ?", (cible["id"],)).fetchone()["n"]
    deja = db().execute("SELECT 1 FROM follows WHERE suiveur_id = ? AND suivi_id = ?",
                        (me["id"], cible["id"])).fetchone() is not None
    journal_action2(me["id"], "profil_vu", objet="profil de @%s" % cible["pseudo"])
    return page("profil.html", titre=cible["pseudo"], cible=cible, posts=posts,
                abonnes=abonnes, suivis=suivis, deja_suis=deja)

@app.route("/abonner/<int:cible>", methods=["POST"])
def suivre(cible: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    existe = conn.execute("SELECT * FROM follows WHERE suiveur_id=? AND suivi_id=?", (me["id"], cible)).fetchone()
    if existe:
        conn.execute("DELETE FROM follows WHERE id=?", (existe["id"],))
    else:
        conn.execute("INSERT OR IGNORE INTO follows (suiveur_id, suivi_id, cree_le) VALUES (?,?,?)",
                     (me["id"], cible, maintenant()))
    conn.commit()
    journal_action2(me["id"], "suivre", objet="profil #%d" % cible,
                    details="Suivi ou désabonnement consigné dans l'historique personnel.")
    return redirect(request.referrer or url_for("fil"))

@app.route("/p/abonnement/<int:createur>", methods=["POST"])
def abonner(createur: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        aid = creer_abonnement(me["id"], createur, float(request.form.get("palier", 0) or 0))
        journal_action2(me["id"], "abonnement", objet="abonnement #%s au créateur #%d" % (aid, createur),
                        montant=float(request.form.get("palier", 0) or 0),
                        details="Demande enregistrée — paiement Mobile Money en attente de validation.")
        session["flash"] = ("Demande d'abonnement enregistrée (réf. %s). Payez sur le numéro Mobile Money "
                            "de l'administrateur, puis demandez la validation par la discussion." % aid)
    except ValueError as exc:
        session["flash"] = str(exc)
    return redirect(url_for("profil", pseudo=(utilisateur_par_id(createur) or {"pseudo": ""})["pseudo"] or "") if createur else url_for("fil"))

@app.route("/p/pourboire/<int:createur>", methods=["POST"])
def pourboire(createur: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        creer_pourboire(me["id"], createur, float(request.form.get("montant", 0) or 0),
                        request.form.get("mot", ""))
        session["flash"] = "Pourboire enregistré : il sera crédité après validation de l'administrateur."
        journal_action2(me["id"], "pourboire", objet="pourboire au créateur #%d" % createur,
                        details="En attente de validation de l'administrateur.")
    except ValueError as exc:
        session["flash"] = str(exc)
    return redirect(request.referrer or url_for("fil"))

@app.route("/portefeuille")
def portefeuille():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    lignes = db().execute("SELECT * FROM portefeuille WHERE user_id = ? ORDER BY id DESC LIMIT 200",
                          (me["id"],)).fetchall()
    admin = admin_par_defaut()
    mois = maintenant()[:7]
    conn = db()
    abo_m = conn.execute(
        "SELECT COALESCE(SUM(palier),0) AS b, COUNT(*) AS n FROM abonnements"
        " WHERE createur_id = ? AND statut = 'valide' AND substr(cree_le,1,7) = ?",
        (me["id"], mois)).fetchone()
    tips_m = conn.execute(
        "SELECT COALESCE(SUM(montant),0) AS b, COUNT(*) AS n FROM pourboires"
        " WHERE createur_id = ? AND statut = 'valide' AND substr(cree_le,1,7) = ?",
        (me["id"], mois)).fetchone()
    resume = net_mois(abo_m["b"] + tips_m["b"], abo_m["n"] + tips_m["n"])
    journal_action2(me["id"], "portefeuille", objet="consultation du portefeuille")
    return page("portefeuille.html", titre="Portefeuille", s=solde(me["id"]), lignes=lignes,
                admin_id=(admin["id"] if admin else me["id"]), ticket_fcfa=CONFIG["TICKET_FCFA"],
                mois_resume=resume)

@app.route("/messages")
def messages():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    return page("messages.html", titre="Discussions", contacts=contacts_de(me["id"]), autre=None, fil=[])

@app.route("/messages/<int:autre>", methods=["GET"])
def messages_conversation(autre: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    autres = utilisateur_par_id(autre)
    if autres is None:
        return "Utilisateur inconnu.", 404
    conn = db()
    conn.execute("UPDATE messages SET lu = 1 WHERE expediteur_id = ? AND destinataire_id = ?", (autre, me["id"]))
    conn.commit()
    journal_action2(me["id"], "discussion_ouverte", objet="discussion avec l'utilisateur #%d" % autre)
    return page("messages.html", titre=f"Discussion avec {autres['pseudo']}", contacts=contacts_de(me["id"]),
                autre=autres, fil=conversation(me["id"], autre))

@app.route("/messages/<int:autre>", methods=["POST"])
def messages_envoyer(autre: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        envoyer_message(me["id"], autre, request.form.get("corps", ""))
    except ValueError as exc:
        session["flash"] = str(exc)
    # Réponse automatique de l'assistant : uniquement quand un utilisateur répond
    # à l'administrateur alors qu'un envoi groupé à réponse auto le couvre, et au
    # plus une réponse toutes les 30 minutes (anti-boucle).
    admin = admin_par_defaut()
    if admin is not None and not me["est_admin"] and autre == admin["id"]:
        envoi = envoi_auto_pour(me["id"])
        recent = False
        dernier = derniere_auto_reply(me["id"])
        if dernier:
            try:
                ecart = (_dt.datetime.now() - _dt.datetime.strptime(dernier, "%Y-%m-%d %H:%M:%S")).total_seconds()
                recent = 0 <= ecart < 1800
            except ValueError:
                recent = False
        if envoi is not None and not recent:
            texte, origine = reponse_ia_assistance(request.form.get("corps", ""), envoi["regles"])
            envoyer_message(admin["id"], me["id"], texte)
            conn = db()
            conn.execute(
                "INSERT INTO reponses_auto (envoi_id, user_id, question, reponse, origine, cree_le)"
                " VALUES (?,?,?,?,?,?)",
                (envoi["id"], me["id"], (request.form.get("corps", "") or "")[:2000],
                 texte, origine, maintenant()))
            conn.commit()
    journal_action2(me["id"], "message_prive", objet="message à l'utilisateur #%d" % autre,
                    details=(request.form.get("corps", "") or "")[:120])
    return redirect(url_for("messages_conversation", autre=autre))

@app.route("/plaintes", methods=["GET"])
def plaintes():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    mes = db().execute("SELECT * FROM plaintes WHERE user_id = ? ORDER BY id DESC", (me["id"],)).fetchall()
    return page("plaintes.html", titre="Plaintes", mes=mes)

@app.route("/plaintes", methods=["POST"])
def plainte_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    sujet = (request.form.get("sujet") or "").strip()
    corps = (request.form.get("corps") or "").strip()
    if sujet and corps:
        conn = db()
        cur = conn.execute("INSERT INTO plaintes (user_id, sujet, corps, cree_le) VALUES (?,?,?,?)",
                           (me["id"], sujet[:120], corps[:4000], maintenant()))
        # Réponse automatique de l'IA d'assistance (repli par règles sans clé).
        texte_ia, origine_ia = reponse_ia_assistance(f"SUJET : {sujet}\nEXPOSÉ : {corps}")
        conn.execute("UPDATE plaintes SET reponse_ia = ? WHERE id = ?", (texte_ia, cur.lastrowid))
        conn.commit()
        session["flash"] = ("Plainte transmise à l'administrateur. Une première réponse automatique "
                            "(%s) est affichée sous votre plainte." %
                            ("assistant IA" if origine_ia == "ia" else "assistant intégré"))
    journal_action2(me["id"], "plainte", objet=sujet[:120],
                    details="Plainte transmise à l'administrateur.")
    return redirect(url_for("plaintes"))

@app.route("/recherche")
def recherche_page():
    q = (request.args.get("q") or "").strip()
    data = MOTEUR.chercher(q) if q else {"collecte_le": "", "compte": {m: 0 for m in MoteurRecherche.MOTEURS},
                                          "resultats": [], "total": 0}
    journal_action2(session.get("uid"), "recherche", objet="recherche effectuée", details=q[:120])
    return page("recherche.html", titre="Recherche", q=q, data=data,
                resultats_html=formater_resultats_html(data))

@app.route("/aller")
def aller():
    cible = request.args.get("u") or ""
    cible = urllib.parse.unquote(cible)
    if not cible.startswith(("http://", "https://")):
        return "Lien refusé.", 400
    return redirect(cible)

@app.route("/chat", methods=["GET"])
def chat_page():
    return page("chat.html", titre="Assistant IA", question="", reponse="", data=None, avertissement="")

@app.route("/chat", methods=["POST"])
def chat_envoyer():
    question = (request.form.get("question") or "").strip()
    if not question:
        return redirect(url_for("chat_page"))
    journal_action2(session.get("uid"), "recherche", objet="question à l'assistant IA",
                    details=question[:120])
    data = MOTEUR.chercher(question)
    bloc = MOTEUR.bloc_memoire(question)
    reponse, avertissement = "", ""
    try:
        reponse = interroger_ia(question, bloc)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("IA indisponible : %s", exc)
        avertissement = ("Le moteur d'IA n'a pas répondu. Aucun contenu n'est inventé : "
                         "seuls les résultats réels collectés ci-dessous sont affichés.")
    return page("chat.html", titre="Assistant IA", question=question, reponse=reponse,
                data=data, avertissement=avertissement,
                resultats_html=formater_resultats_html(data))

# =============================================================================
# ⚙️ PARAMÈTRES, CGU ET CONFIDENTIALITÉ
# =============================================================================
@app.route("/parametres")
def parametres():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    return page("parametres.html", titre="Paramètres", prefs=prefs_de(me["id"]))

@app.route("/parametres/profil", methods=["POST"])
def parametres_profil():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        pseudo_demande = (request.form.get("pseudo", "") or "").strip()
        if pseudo_demande and pseudo_demande != me["pseudo"]:
            valider_surnom(pseudo_demande)  # règle des 6 lettres (les comptes existants sont gardés)
        maj_profil(me["id"], request.form.get("pseudo", ""), request.form.get("bio", ""),
                   request.form.get("payout_phone", ""), request.form.get("payout_op", "MTN"))
        session["flash"] = "Profil enregistré."
        journal_action2(me["id"], "parametres", objet="profil mis à jour")
    except ValueError as exc:
        session["flash"] = str(exc)
    return redirect(url_for("parametres"))

@app.route("/parametres/mot-de-passe", methods=["POST"])
def parametres_mdp():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        changer_mot_de_passe(me["id"], request.form.get("ancien", ""), request.form.get("nouveau", ""),
                             request.form.get("confirmation", ""))
        session["flash"] = "Mot de passe modifié avec succès."
        journal_action2(me["id"], "parametres", objet="mot de passe modifié")
    except ValueError as exc:
        session["flash"] = str(exc)
    return redirect(url_for("parametres"))

@app.route("/parametres/preferences", methods=["POST"])
def parametres_prefs():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    enregistrer_prefs(me["id"],
                      request.form.get("notifs_messages") == "1",
                      request.form.get("notifs_portefeuille") == "1",
                      request.form.get("langue", "fr"),
                      request.form.get("theme", "sombre"))
    maj_refus_groupes(me["id"], request.form.get("refus_groupes") == "1")
    session["flash"] = "Préférences enregistrées (notifications, langue, thème, envois groupés)."
    journal_action2(me["id"], "parametres", objet="préférences mises à jour")
    return redirect(url_for("parametres"))

@app.route("/parametres/supprimer", methods=["POST"])
def parametres_supprimer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        supprimer_compte(me["id"], request.form.get("mot_de_passe", ""), request.form.get("confirmation", ""))
    except ValueError as exc:
        session["flash"] = str(exc)
        return redirect(url_for("parametres"))
    session.clear()
    session["flash"] = "Votre compte a été supprimé définitivement, ainsi que toutes vos données."
    return redirect(url_for("connexion"))

@app.route("/cgu")
def cgu():
    """Conditions d'utilisation strictes — page publique."""
    return page("cgu.html", titre="Conditions d'utilisation", date_effet=maintenant()[:10])

@app.route("/confidentialite")
def confidentialite():
    """Politique de confidentialité — page publique."""
    return page("confidentialite.html", titre="Politique de confidentialité", date_effet=maintenant()[:10])

@app.route("/api/calcul")
def api_calcul():
    try:
        montant = float(request.args.get("montant", "0") or 0)
    except ValueError:
        montant = 0.0
    commission, net, detail = calcul_commission(montant)
    return jsonify({"montant": round(montant, 2), "commission": commission, "net": net,
                    "detail": detail, "mode": CONFIG["MODE_COMMISSION"], "devise": CONFIG["DEVISE"]})

@app.route("/api/recherche")
def api_recherche():
    q = (request.args.get("q") or "").strip()
    return jsonify(MOTEUR.chercher(q) if q else {"resultats": [], "total": 0})

@app.route("/api/sante")
def api_sante():
    return jsonify({"ok": True, "horodatage": maintenant(), "utilisateurs": count_rows("users"),
                    "publications": count_rows("posts"), "mode_commission": CONFIG["MODE_COMMISSION"],
                    "moteurs": list(MoteurRecherche.MOTEURS)})

def count_rows(table: str) -> int:
    return int(db().execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

# =============================================================================
# 🛠️ ADMINISTRATION
# =============================================================================
def exiger_admin():
    me = utilisateur_courant()
    if me is None:
        return redirect(url_for("connexion"))
    if not me["est_admin"]:
        return "Accès réservé à l'administrateur.", 403
    return None

# =============================================================================
# 📣 ESPACE D'ENVOI GROUPÉ (réservé à l'administrateur)
# =============================================================================
def cibles_du_filtre(filtre: str) -> List[int]:
    """Identifiants ciblés par un filtre d'envoi groupé (jamais l'administrateur)."""
    conn = db()
    if filtre == "attente":
        lignes = conn.execute(
            "SELECT DISTINCT u.id FROM users u JOIN portefeuille p ON p.user_id = u.id"
            " WHERE p.statut = 'en_attente' AND u.bloque = 0 AND u.est_admin = 0"
            " ORDER BY u.id").fetchall()
    elif filtre == "impayes":
        seuil = (_dt.datetime.now(_dt.timezone.utc)
                 - _dt.timedelta(days=DELAI_RELANCE_JOURS)).strftime("%Y-%m-%d %H:%M:%S")
        lignes = conn.execute(
            "SELECT DISTINCT u.id FROM users u JOIN portefeuille p ON p.user_id = u.id"
            " WHERE p.statut = 'en_attente' AND p.cree_le <= ?"
            " AND u.bloque = 0 AND u.est_admin = 0 ORDER BY u.id", (seuil,)).fetchall()
    elif filtre == "tous":
        lignes = conn.execute(
            "SELECT id FROM users WHERE bloque = 0 AND est_admin = 0 ORDER BY id").fetchall()
    else:
        return []
    return [int(l["id"]) for l in lignes]

def envoyes_recentes(admin_id: int) -> int:
    """Messages envoyés par l'administrateur sur la dernière heure glissante (quota)."""
    seuil = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    ligne = db().execute(
        "SELECT COUNT(*) AS n FROM messages WHERE expediteur_id = ? AND cree_le >= ?",
        (admin_id, seuil)).fetchone()
    return int(ligne["n"])

def envoyer_groupe(admin_id: int, cibles: List[int], corps: str, filtre: str = "manuel",
                   objet: str = "personnalise", auto_reponse: bool = False,
                   regles: str = "") -> Dict[str, int]:
    """Envoi d'un même message à plusieurs membres : quota horaire, saut des
    comptes bloqués et du droit de retrait (refus_groupes)."""
    corps = (corps or "").strip()
    if not corps:
        raise ValueError("Le message du groupe est vide.")
    cibles = sorted({int(c) for c in cibles if int(c) != admin_id})
    restant = max(0, CONFIG["MAX_ENVOIS_HEURE"] - envoyes_recentes(admin_id))
    conn = db()
    envoyes = ignores = bloques = 0
    for uid in cibles:
        if envoyes >= restant:
            break
        u = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if u is None:
            continue
        if u["bloque"]:
            bloques += 1
            continue
        if int(u["refus_groupes"] or 0):
            ignores += 1
            continue
        envoyer_message(admin_id, uid, corps)
        envoyes += 1
    if envoyes:
        conn.execute(
            "INSERT INTO envois_groupe (admin_id, cibles, filtre, objet, corps, auto_reponse, regles, cree_le)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (admin_id, ",".join(str(u) for u in cibles), filtre, objet, corps[:4000],
             1 if auto_reponse else 0, (regles or "").strip()[:2000], maintenant()))
        conn.commit()
    return {"envoyes": envoyes, "ignores": ignores, "bloques": bloques, "quota": restant}

def envoi_auto_pour(user_id: int) -> Optional[sqlite3.Row]:
    """Dernier envoi groupé à réponse automatique couvrant cet utilisateur."""
    for e in db().execute(
            "SELECT * FROM envois_groupe WHERE auto_reponse = 1 ORDER BY id DESC LIMIT 10").fetchall():
        liste = [x for x in (e["cibles"].split(",") if e["cibles"] else [])]
        if str(user_id) in liste or (not e["cibles"] and e["filtre"] == "tous"):
            return e
    return None

def derniere_auto_reply(user_id: int) -> Optional[str]:
    ligne = db().execute(
        "SELECT cree_le FROM reponses_auto WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)).fetchone()
    return ligne["cree_le"] if ligne else None

def _table_existe(nom: str) -> bool:
    """Vrai si la table existe (les modules optionnels peuvent être absents)."""
    try:
        return db().execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone() is not None
    except Exception:
        return False

def _soldes_application() -> Dict[str, Any]:
    """Soldes consolidés de l'application pour le tableau de bord admin :
    portefeuilles (V7), escrow (V5) et demandes de reversement Mobile Money (V8).
    Toutes les valeurs sont lues de la base — aucun montant écrit à la main."""
    conn = db()
    def _scalaire(requete: str) -> float:
        return round(conn.execute(requete).fetchone()[0] or 0, 2)
    bilan: Dict[str, Any] = {
        "en_attente_tickets": _scalaire(
            "SELECT COALESCE(SUM(tickets),0) FROM portefeuille"
            " WHERE sens='credit' AND statut='en_attente'"),
        "en_attente_fcfa": _scalaire(
            "SELECT COALESCE(SUM(fcfa),0) FROM portefeuille"
            " WHERE sens='credit' AND statut='en_attente'"),
        "paye_tickets": _scalaire(
            "SELECT COALESCE(SUM(tickets),0) FROM portefeuille"
            " WHERE sens='credit' AND statut='paye'"),
        "paye_fcfa": _scalaire(
            "SELECT COALESCE(SUM(fcfa),0) FROM portefeuille"
            " WHERE sens='credit' AND statut='paye'"),
        "verse_tickets": _scalaire("SELECT COALESCE(SUM(tickets),0) FROM paiements"),
        "verse_fcfa": _scalaire("SELECT COALESCE(SUM(fcfa),0) FROM paiements"),
        "recettes_abonnements": _scalaire(
            "SELECT COALESCE(SUM(montant),0) FROM recettes WHERE source='abonnement'"),
        "recettes_pourboires": _scalaire(
            "SELECT COALESCE(SUM(montant),0) FROM recettes WHERE source='pourboire'"),
        "recettes_boutique": _scalaire(
            "SELECT COALESCE(SUM(montant),0) FROM recettes WHERE source='boutique'"),
        "escrow_bloque": 0.0, "escrows_bloques": 0,
        "reversements_attente": 0, "reversements_attente_fcfa": 0.0,
    }
    if _table_existe("escrow"):
        bilan["escrow_bloque"] = _scalaire(
            "SELECT COALESCE(SUM(montant),0) FROM escrow WHERE statut='bloque'")
        bilan["escrows_bloques"] = conn.execute(
            "SELECT COUNT(*) FROM escrow WHERE statut='bloque'").fetchone()[0]
    if _table_existe("reversements"):
        bilan["reversements_attente"] = conn.execute(
            "SELECT COUNT(*) FROM reversements WHERE statut IN ('en_attente','approuve')"
        ).fetchone()[0]
        bilan["reversements_attente_fcfa"] = round(_scalaire(
            "SELECT COALESCE(SUM(net_centimes),0) FROM reversements"
            " WHERE statut IN ('en_attente','approuve')") / 100.0, 2)
    bilan["attente_total_fcfa"] = round(
        bilan["en_attente_fcfa"] + bilan["escrow_bloque"], 2)
    return bilan

def _resume_paliers_v8() -> List[Dict[str, Any]]:
    """Les 6 paliers de reversement V8 calculés par le moteur (aucun doublon
    de formule : toutes les valeurs viennent de tableau_paliers_reversement)."""
    try:
        return [{
            "niveau": lg["niveau"], "nom": lg["nom"],
            "vues_min": lg["vues_min"], "vues_max": lg["vues_max"],
            "delai_jours": lg["delai_jours"], "kyc": lg["kyc"],
            "frais_pct": lg["frais_pct"],
            "pool_fcfa": centimes_vers_fcfa(lg["pool_centimes"]),
            "app_fcfa": centimes_vers_fcfa(lg["part_application_centimes"]),
            "createurs_fcfa": centimes_vers_fcfa(lg["part_createurs_centimes"]),
            "net_fcfa": centimes_vers_fcfa(lg["net_verse_centimes"]),
        } for lg in tableau_paliers_reversement()]
    except Exception:
        return []

def _resume_commissions_paliers() -> Dict[str, Dict[str, Any]]:
    """Commission et net du créateur pour CHACUN des 6 paliers d'abonnement,
    via la règle de commission active (calcul_commission — aucune redondance)."""
    resume: Dict[str, Dict[str, Any]] = {}
    for pal in PALIERS:
        commission, net, detail = calcul_commission(pal)
        resume[str(pal)] = {"commission": commission, "net": net, "detail": detail}
    return resume

@app.route("/admin")
def admin():
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    abo = conn.execute(
        "SELECT a.*, c.pseudo AS pseudo_c, b.pseudo AS pseudo_a FROM abonnements a"
        " JOIN users c ON c.id = a.createur_id JOIN users b ON b.id = a.abonne_id"
        " WHERE a.statut = 'en_attente' ORDER BY a.id DESC"
    ).fetchall()
    tips = conn.execute(
        "SELECT t.*, c.pseudo AS pseudo_c, e.pseudo AS pseudo_e FROM pourboires t"
        " JOIN users c ON c.id = t.createur_id JOIN users e ON e.id = t.expediteur_id"
        " WHERE t.statut = 'en_attente' ORDER BY t.id DESC"
    ).fetchall()
    pl = conn.execute(
        "SELECT p.*, u.pseudo FROM plaintes p JOIN users u ON u.id = p.user_id"
        " WHERE p.statut = 'ouverte' ORDER BY p.id DESC"
    ).fetchall()
    hist = conn.execute(
        "SELECT pa.*, u.pseudo FROM paiements pa JOIN users u ON u.id = pa.user_id"
        " ORDER BY pa.id DESC LIMIT 100"
    ).fetchall()
    return page("admin.html", titre="Administration", paiements=tableau_paiement(),
                abo_attente=abo, tips_attente=tips, plaintes=pl, historique=hist,
                recettes=total_recettes(), mois_courant=maintenant()[:7],
                suivi=_suivi_abonnements_donnees(conn),
                soldes_app=_soldes_application(), paliers_v8=_resume_paliers_v8(),
                resume_paliers=_resume_commissions_paliers(),
                membres=conn.execute(
                    "SELECT id, pseudo, est_admin, bloque FROM users ORDER BY est_admin DESC, id"
                ).fetchall())

@app.route("/admin/envois", methods=["GET", "POST"])
def admin_envois():
    """Espace privé d'envoi groupé : sélection des membres, n'importe quel type
    de message, IA de réponse automatique selon les règles dictées."""
    refus = exiger_admin()
    if refus:
        return refus
    admin = admin_par_defaut()
    if request.method == "POST":
        try:
            filtre = request.form.get("filtre", "manuel")
            objet = request.form.get("objet", "personnalise")
            corps = (request.form.get("corps") or "").strip()
            if not corps and objet in PRESETS_ENVOI:
                corps = PRESETS_ENVOI[objet].format(mois=libelle_mois())
            cibles = ([int(x) for x in request.form.getlist("cibles")]
                      if filtre == "manuel" else cibles_du_filtre(filtre))
            res = envoyer_groupe(admin["id"], cibles, corps, filtre=filtre, objet=objet,
                                 auto_reponse=request.form.get("auto_reponse") == "1",
                                 regles=request.form.get("regles", ""))
            journal_action("envoi_groupe", details="%d envoyé(s), %d ignoré(s), %d bloqué(s)"
                           % (res["envoyes"], res["ignores"], res["bloques"]), utilisateur=admin["id"])
            session["flash"] = ("Envoi groupé : %d message(s) envoyé(s), %d ignoré(s) "
                                "(droit de retrait), %d bloqué(s). Quota restant sur "
                                "l'heure : %d." % (res["envoyes"], res["ignores"],
                                                   res["bloques"],
                                                   max(0, res["quota"] - res["envoyes"])))
        except ValueError as exc:
            session["flash"] = str(exc)
        return redirect(url_for("admin_envois"))
    filtre = request.args.get("filtre", "manuel")
    preset = request.args.get("preset", "personnalise")
    conn = db()
    membres = conn.execute(
        "SELECT id, pseudo, bloque, refus_groupes, muet, muet_jusqua, muet_motif,"
        " ban_temporaire_jusqua FROM users WHERE est_admin = 0 ORDER BY id"
    ).fetchall()
    historique = conn.execute(
        "SELECT * FROM envois_groupe ORDER BY id DESC LIMIT 10").fetchall()
    corps_preset = PRESETS_ENVOI.get(preset, "")
    if corps_preset:
        corps_preset = corps_preset.format(mois=libelle_mois())
    modeles_js = {k: (v.format(mois=libelle_mois()) if v else "") for k, v in PRESETS_ENVOI.items()}
    quotas = {
        "max": CONFIG["MAX_ENVOIS_HEURE"],
        "restant": max(0, CONFIG["MAX_ENVOIS_HEURE"] - envoyes_recentes(admin["id"])),
        "attente": len(cibles_du_filtre("attente")),
        "impayes": len(cibles_du_filtre("impayes")),
        "tous": len(cibles_du_filtre("tous")),
    }
    return page("admin_envois.html", titre="Espace d'envoi groupé", membres=membres,
                historique=historique, quotas=quotas, filtre=filtre, preset=preset,
                corps_preset=corps_preset, modeles=modeles_js, delai_relance=DELAI_RELANCE_JOURS)

@app.route("/statistiques")
def statistiques():
    """Tableau de bord personnel : publications, likes, commentaires, abonnés, gains."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    return page("stats.html", titre="Mes statistiques", s=statistiques_utilisateur(me["id"]))

@app.route("/admin/statistiques")
def admin_statistiques():
    """Tableau de bord global des statistiques utilisateurs (réservé à l'administrateur)."""
    redirection = exiger_admin()
    if redirection:
        return redirection
    return page("admin_stats.html", titre="Statistiques globales", g=statistiques_globales())

@app.route("/statistiques/export.csv")
def statistiques_csv():
    """Export CSV des statistiques personnelles (session requise)."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    return _csv_reponse(export_csv_stats_utilisateur(me["id"]),
                        "mes_statistiques_%s.csv" % _dt.date.today().isoformat())

@app.route("/admin/statistiques/export.csv")
def admin_statistiques_csv():
    """Export CSV des statistiques globales (réservé à l'administrateur)."""
    redirection = exiger_admin()
    if redirection:
        return redirection
    return _csv_reponse(export_csv_stats_globales(),
                        "statistiques_globales_%s.csv" % _dt.date.today().isoformat())

@app.route("/admin/export")
def admin_export():
    refus = exiger_admin()
    if refus:
        return refus
    mois = (request.args.get("mois") or maintenant()[:7]).strip()
    try:
        contenu = export_csv_mensuel(mois)
        journal_action("export_comptable", cible=mois)
    except ValueError as exc:
        session["flash"] = str(exc)
        return redirect(url_for("admin"))
    return Response("\ufeff" + contenu, mimetype="text/csv;charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=export_comptable_{mois}.csv"})

@app.route("/admin/payer/<int:uid>", methods=["POST"])
def admin_payer(uid: int):
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    res = marquer_paye(uid, me["id"])
    session["flash"] = (f"Versement de {res['tickets']} tickets ({res['fcfa']} {CONFIG['DEVISE']}) "
                        f"enregistré vers {res['telephone']}.") if res["ok"] else res["message"]
    return redirect(url_for("admin"))

@app.route("/admin/valider/abonnement/<int:aid>", methods=["POST"])
def admin_valider_abonnement(aid: int):
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    res = valider_abonnement(aid, me["id"])
    journal_action("validation_abonnement", cible="abonnement #%d" % aid,
                   details=("validé (commission %s)" % res["commission"]) if res else "sans effet",
                   utilisateur=me["id"])
    session["flash"] = (f"Abonnement validé : commission {res['commission']} {CONFIG['DEVISE']}, "
                        f"net crédité {res['net']} {CONFIG['DEVISE']}.") if res else "Introuvable ou déjà validé."
    return redirect(url_for("admin"))

@app.route("/admin/valider/pourboire/<int:tid>", methods=["POST"])
def admin_valider_pourboire(tid: int):
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    res = valider_pourboire(tid, me["id"])
    session["flash"] = (f"Pourboire validé : commission {res['commission']} {CONFIG['DEVISE']}, "
                        f"net crédité {res['net']} {CONFIG['DEVISE']}.") if res else "Introuvable ou déjà validé."
    return redirect(url_for("admin"))

@app.route("/admin/plainte/<int:pid>", methods=["POST"])
def admin_repondre_plainte(pid: int):
    refus = exiger_admin()
    if refus:
        return refus
    reponse = (request.form.get("reponse") or "").strip()
    conn = db()
    conn.execute("UPDATE plaintes SET reponse = ?, statut = 'traitee', traite_le = ? WHERE id = ?",
                 (reponse, maintenant(), pid))
    conn.commit()
    session["flash"] = "Plainte traitée."
    return redirect(url_for("admin"))

@app.route("/admin/bloquer/<int:uid>", methods=["POST"])
def admin_bloquer(uid: int):
    """Bloque ou débloque un compte (sanction des CGU). Un compte bloqué est
    déconnecté immédiatement et ne peut plus se reconnecter."""
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    if uid == me["id"]:
        session["flash"] = "Vous ne pouvez pas bloquer votre propre compte administrateur."
        return redirect(url_for("admin"))
    cible = utilisateur_par_id(uid)
    if cible is None:
        return "Utilisateur inconnu.", 404
    conn = db()
    conn.execute("UPDATE users SET bloque = 1 - bloque WHERE id = ?", (uid,))
    conn.commit()
    session["flash"] = ("Compte « %s » %s" % (cible["pseudo"],
                        "débloqué." if cible["bloque"] else "bloqué — session interrompue, reconnexion impossible."))
    return redirect(url_for("admin"))

# =============================================================================
# 🧪 OUTILS EN LIGNE DE COMMANDE
# =============================================================================
def graine_admin(pseudo: str, telephone: str, mot_de_passe: str) -> int:
    init_schema()
    # V6 : chaque extension prépare son schéma avant toute écriture.
    for _init in ("init_schema", "init_schema_v5", "init_schema_v6", "init_schema_v7"):
        _fonction = globals().get(_init)
        if callable(_fonction):
            _fonction()
    avec_app = app.test_request_context()
    with avec_app:
        existant = db().execute("SELECT * FROM users WHERE pseudo = ?", (pseudo,)).fetchone()
        if existant:
            print(f"L'administrateur « {pseudo} » existe déjà (id {existant['id']}).")
            return int(existant["id"])
        uid = creer_utilisateur(telephone, pseudo, mot_de_passe, est_admin=True)
    print(f"✅ Administrateur créé : {pseudo} (id {uid})")
    if not REGLE_SURNOM.match(pseudo):
        print("   ℹ️  Rappel : le surnom standard est exactement 6 lettres — ce compte existant est conservé.")
    if not telephone:
        print("   ⚠️  Aucun numéro : renseignez payout_phone dans la base avant tout versement.")
    return uid

def demo() -> None:
    """Simulation complète hors serveur : commission, abonnements, pourboires,
    portefeuille, versement. Sert de test de non-régression."""
    init_schema()
    print("=" * 74)
    print("TABLE DE COMMISSION — les 6 paliers")
    print("=" * 74)
    for mode in ("max", "cumul"):
        CONFIG["MODE_COMMISSION"] = mode
        print(f"\nMode '{mode}' :")
        print(f"{'Palier':>8} | {'25%':>8} | {'Commission':>10} | {'Net créateur':>12}")
        for palier in PALIERS:
            com, net, _ = calcul_commission(palier)
            pct = round(palier * 0.25, 2)
            print(f"{palier:>8} | {pct:>8} | {com:>10} | {net:>12}")
        # le plancher s'applique-t-il un jour ?
        seuil = CONFIG["COMMISSION_PLANCHER"] / (CONFIG["COMMISSION_PCT"] / 100.0)
        print(f"  → Le plancher de {CONFIG['COMMISSION_PLANCHER']:g} {CONFIG['DEVISE']} ne dépasse 25% "
              f"que si le montant est < {seuil:g} {CONFIG['DEVISE']} "
              f"(donc JAMAIS sur les 6 paliers, dont le plus bas est {min(PALIERS)}).")
    CONFIG["MODE_COMMISSION"] = _env("MODE_COMMISSION", "cumul").lower()

    print("\n" + "=" * 74)
    print("SIMULATION D'UN CYCLE COMPLET")
    print("=" * 74)
    import tempfile
    CONFIG["DB"] = os.path.join(tempfile.mkdtemp(), "demo.db")
    init_schema()
    # V6 : chaque extension prépare son schéma avant toute écriture.
    for _init in ("init_schema", "init_schema_v5", "init_schema_v6", "init_schema_v7"):
        _fonction = globals().get(_init)
        if callable(_fonction):
            _fonction()
    with app.test_request_context():
        admin_id = creer_utilisateur("90000000", "zeus_admin", "motdepasse", est_admin=True)
        conn = db()
        conn.execute("UPDATE users SET payout_phone='+229 01 99 88 77 66', payout_op='MTN' WHERE id=?", (admin_id,))
        conn.commit()
        auteur = creer_utilisateur("91000001", "kofi", "motdepasse")
        lecteur = creer_utilisateur("92000002", "amina", "motdepasse")

        vid = creer_abonnement(lecteur, auteur, 500)
        res = valider_abonnement(vid, admin_id)
        print(f"Abonnement 500 FCFA → commission {res['commission']}, net crédité {res['net']}")
        tid = creer_pourboire(lecteur, auteur, 100, "bon article")
        res2 = valider_pourboire(tid, admin_id)
        print(f"Pourboire 100 FCFA  → commission {res2['commission']}, net crédité {res2['net']}")
        print(f"Recettes de l'app   → {total_recettes()} {CONFIG['DEVISE']}")
        print(f"Solde de kofi       → {solde(auteur)}")
        print("Tableau de paiement :")
        for ligne in tableau_paiement():
            print(f"   {ligne['pseudo']:<12} {ligne['tickets']:>8} tickets  {ligne['fcfa']:>8} "
                  f"{CONFIG['DEVISE']}  {ligne['payout_phone'] or ligne['telephone']}")
        res3 = marquer_paye(auteur, admin_id)
        print(f"Versement           → {res3}")
        print(f"Tableau après       → {tableau_paiement()}")
    print("\n✅ Simulation terminée sans erreur.")

def main(argv: Optional[List[str]] = None) -> int:
    analyseur = argparse.ArgumentParser(description="ToutBot Mundo — réseau social textuel")
    analyseur.add_argument("--port", type=int, default=int(_env("PORT", "8099")))
    analyseur.add_argument("--hote", default=_env("HOST", "127.0.0.1"))
    analyseur.add_argument("--seed-admin", metavar="PSEUDO",
                           help="Crée un administrateur (pseudo, mot de passe et téléphone via options)")
    analyseur.add_argument("--telephone", default=_env("ADMIN_TEL", ""))
    analyseur.add_argument("--mot-de-passe", default=_env("ADMIN_PWD", "motdepasse"))
    analyseur.add_argument("--demo", action="store_true", help="Teste la commission et un cycle complet")
    analyseur.add_argument("--recherche", metavar="REQUETE", help="Interroge les moteurs gratuits en ligne de commande")
    args = analyseur.parse_args(argv)

    if args.demo:
        demo()
        return 0
    if args.recherche:
        data = MOTEUR.chercher(args.recherche)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if args.seed_admin:
        graine_admin(args.seed_admin, args.telephone, args.mot_de_passe)
        return 0

    print(f"🌍 ToutBot Mundo V7 — http://{args.hote}:{args.port}")
    print(f"   Commission : mode {CONFIG['MODE_COMMISSION']} — {CONFIG['COMMISSION_PCT']:g}% , "
          f"plancher {CONFIG['COMMISSION_PLANCHER']:g} {CONFIG['DEVISE']}")
    print(f"   Base : {CONFIG['DB']}")
    # V6 : chaque extension prépare son schéma avant toute écriture.
    for _init in ("init_schema", "init_schema_v5", "init_schema_v6", "init_schema_v7"):
        _fonction = globals().get(_init)
        if callable(_fonction):
            _fonction()
    # ============================================================================
    # 🆕 v2 — INJECTION DU CORRECTIF SEO (sitemap.xml + robots.txt + noindex privé)
    # Activé après création du schéma. Les URLs publiques proviennent de la
    # variable d'environnement DOMAINES_PUBLIC (jamais en dur). Si l'injection
    # échoue, l'app démarre quand même (log d'avertissement, jamais bloquant).
    # ============================================================================
    try:
        _DOMAINE_PUBLIC = os.environ.get(
            "DOMAINES_PUBLIC",
            "https://" + (os.environ.get("RENDER_EXTERNAL_URL")
                          or "votre-domaine.com")
        ).rstrip("/")
        _V2_ROUTES_PUBLIQUES = (
            "/", "/tarifs", "/cgu", "/confidentialite",
            "/inscription", "/connexion",
        )
        _V2_PREFIXES_PRIVES = (
            "/messages", "/chat", "/admin", "/portefeuille",
            "/u/", "/p/", "/parametres", "/plaintes",
            "/mon-historique", "/stories", "/sondages",
            "/boutique", "/prets", "/live", "/api",
        )

        @app.route("/sitemap.xml", methods=["GET"])
        def _v2_sitemap_xml():  # type: ignore[no-redef]
            aujourdhui = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
            lignes = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            ]
            for _p in _V2_ROUTES_PUBLIQUES:
                lignes.append(
                    f"  <url><loc>{_DOMAINE_PUBLIC}{_p}</loc>"
                    f"<lastmod>{aujourdhui}</lastmod>"
                    "<changefreq>daily</changefreq><priority>0.8</priority></url>"
                )
            lignes.append("</urlset>")
            _rep = app.response_class("\n".join(lignes), mimetype="application/xml")
            _rep.headers["Cache-Control"] = "public, max-age=3600"
            return _rep

        @app.route("/robots.txt", methods=["GET"])
        def _v2_robots_txt():  # type: ignore[no-redef]
            _corps = ["User-agent: *", "Allow: /"]
            for _p in _V2_PREFIXES_PRIVES:
                _corps.append(f"Disallow: {_p}")
            _corps.append(f"Sitemap: {_DOMAINE_PUBLIC}/sitemap.xml")
            _rep = app.response_class("\n".join(_corps) + "\n", mimetype="text/plain")
            _rep.headers["Cache-Control"] = "public, max-age=3600"
            return _rep

        @app.after_request
        def _v2_noindex_prive(_response):  # type: ignore[no-untyped-def]
            try:
                _chemin = _response.request.path if _response.request else ""
            except Exception:
                _chemin = ""
            if any(_chemin.startswith(_p) for _p in _V2_PREFIXES_PRIVES):
                _response.headers["X-Robots-Tag"] = "noindex, nofollow"
            return _response

        try:
            LOGGER.info("v2 SEO actif : sitemap + robots + noindex privé (domaine=%s)",
                        _DOMAINE_PUBLIC)
        except Exception:
            pass
    except Exception as _exc_sitemap:
        try:
            LOGGER.warning("v2 SEO non chargé (l'app continue) : %s", _exc_sitemap)
        except Exception:
            pass
    app.run(host=args.hote, port=args.port, debug=False, threaded=True)
    return 0

# V6 — le lancement est DÉPLACÉ tout en bas du fichier (fin du bloc V6) : les
# extensions V5 et V6 doivent être définies avant que le serveur ne démarre.
# =============================================================================
# 🚀 V5 — EXTENSIONS (escrow, pourboires récurrents, boutique, affiliation,
#    prêts, conversion USDT, stories, sondages, threads, mentions, hashtags,
#    live texte) — 100 % TEXTE, montants en ENTIERS.
# =============================================================================
import markupsafe
from markupsafe import Markup

# --- Configuration V5 (valeurs par défaut modifiables par l'admin) -----------
CONFIG_V5_DEFAUT = {
    "taux_usdt": "625",            # 1 USDT = X FCFA (à confirmer par l'admin)
    "partenaire_usdt": "Partenaire local USDT (à configurer par l'administrateur)",
    "taux_affiliation": "5",       # % du NET reversé au parrain
    "escrow_jours": "7",           # jours de blocage escrow (borné 7..14)
    "escrow_actif": "1",
}

SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS config_v5 (
    cle    TEXT PRIMARY KEY,
    valeur TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS escrow (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    pf_id              INTEGER NOT NULL,
    source_type        TEXT NOT NULL,
    source_id          INTEGER NOT NULL,
    createur_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    montant            INTEGER NOT NULL,
    statut             TEXT NOT NULL DEFAULT 'bloque',  -- bloque|libere|libere_auto|rembourse
    date_bloque        TEXT NOT NULL,
    date_libere_prevue TEXT NOT NULL,
    decide_le          TEXT
);
CREATE TABLE IF NOT EXISTS pourboires_recurrents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    expediteur_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    createur_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    montant              INTEGER NOT NULL,
    mot                  TEXT NOT NULL DEFAULT '',
    statut               TEXT NOT NULL DEFAULT 'actif',  -- actif|pause|annule
    prochain_prelevement TEXT NOT NULL,
    cree_le              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contenus_exclusifs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    createur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    titre      TEXT NOT NULL,
    corps      TEXT NOT NULL,
    prix       INTEGER NOT NULL,
    cree_le    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS achats_contenus (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contenu_id INTEGER NOT NULL REFERENCES contenus_exclusifs(id) ON DELETE CASCADE,
    acheteur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    montant    INTEGER NOT NULL,
    commission INTEGER NOT NULL,
    net        INTEGER NOT NULL,
    reference  TEXT NOT NULL,
    statut     TEXT NOT NULL DEFAULT 'en_attente',
    pf_id      INTEGER,
    cree_le    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commissions_affiliation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parrain_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filleul_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    source_id   INTEGER NOT NULL,
    montant_base INTEGER NOT NULL,
    taux_pct    INTEGER NOT NULL,
    montant     INTEGER NOT NULL,
    statut      TEXT NOT NULL DEFAULT 'en_attente',
    pf_id       INTEGER,
    cree_le     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    preteur_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emprunteur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    capital       INTEGER NOT NULL,
    taux_pct      INTEGER NOT NULL,
    nb_echeances  INTEGER NOT NULL,
    statut        TEXT NOT NULL DEFAULT 'demande',  -- demande|attente_admin|actif|rembourse|refuse|defaut
    pf_id         INTEGER,
    cree_le       TEXT NOT NULL,
    decide_le     TEXT
);
CREATE TABLE IF NOT EXISTS prets_echeances (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pret_id   INTEGER NOT NULL REFERENCES prets(id) ON DELETE CASCADE,
    numero    INTEGER NOT NULL,
    montant   INTEGER NOT NULL,
    statut    TEXT NOT NULL DEFAULT 'en_attente',  -- en_attente|paye
    paye_le   TEXT,
    UNIQUE(pret_id, numero)
);
CREATE TABLE IF NOT EXISTS conversions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sens          TEXT NOT NULL,          -- fcfa_vers_usdt
    montant_fcfa  INTEGER NOT NULL,
    montant_usdt  INTEGER NOT NULL,
    taux          INTEGER NOT NULL,
    statut        TEXT NOT NULL DEFAULT 'en_attente',
    reference     TEXT NOT NULL,
    pf_id         INTEGER,
    cree_le       TEXT NOT NULL,
    decide_le     TEXT
);
CREATE TABLE IF NOT EXISTS stories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    auteur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps     TEXT NOT NULL,
    cree_le   TEXT NOT NULL,
    expire_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stories_vues (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    viewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cree_le  TEXT NOT NULL,
    UNIQUE(story_id, viewer_id)
);
CREATE TABLE IF NOT EXISTS sondages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id  INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    cree_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sondages_options (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sondage_id INTEGER NOT NULL REFERENCES sondages(id) ON DELETE CASCADE,
    libelle    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sondages_votes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sondage_id INTEGER NOT NULL REFERENCES sondages(id) ON DELETE CASCADE,
    option_id  INTEGER NOT NULL REFERENCES sondages_options(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    cree_le    TEXT NOT NULL,
    UNIQUE(sondage_id, user_id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type      TEXT NOT NULL,
    texte     TEXT NOT NULL,
    lien      TEXT NOT NULL DEFAULT '',
    lu        INTEGER NOT NULL DEFAULT 0,
    cree_le   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifs_user ON notifications(user_id, lu);
CREATE TABLE IF NOT EXISTS lives (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    hote_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    titre    TEXT NOT NULL,
    statut   TEXT NOT NULL DEFAULT 'ouvert',  -- ouvert|ferme
    cree_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_messages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    live_id  INTEGER NOT NULL REFERENCES lives(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps    TEXT NOT NULL,
    cree_le  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_msg ON live_messages(live_id, id);
"""

def init_schema_v5() -> None:
    conn = sqlite3.connect(CONFIG["DB"])
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_V5)
    for colonne, table, defaut in (("parent_id", "comments", "INTEGER"),
                                   ("parrain_id", "users", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {colonne} {defaut}")
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    for cle, valeur in CONFIG_V5_DEFAUT.items():
        conn.execute("INSERT OR IGNORE INTO config_v5 (cle, valeur) VALUES (?,?)", (cle, valeur))
    conn.commit()
    conn.close()

init_schema_v5()

def config_v5(cle: str, defaut: str = "") -> str:
    ligne = db().execute("SELECT valeur FROM config_v5 WHERE cle = ?", (cle,)).fetchone()
    return ligne["valeur"] if ligne else CONFIG_V5_DEFAUT.get(cle, defaut)

def config_v5_set(cle: str, valeur: str) -> None:
    conn = db()
    conn.execute("INSERT INTO config_v5 (cle, valeur) VALUES (?,?) "
                 "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur", (cle, valeur))
    conn.commit()

# --- Outils ------------------------------------------------------------------
_pf_original = ecrire_portefeuille
def _pf(user_id: int, sens: str, fcfa: float, libelle: str,
        ref: str = "", statut: str = "en_attente") -> int:
    """Écrit une ligne du grand livre et renvoie son identifiant (id portefeuille)."""
    _pf_original(user_id, sens, fcfa, libelle, ref, statut)
    return int(db().execute("SELECT last_insert_rowid()").fetchone()[0])

def montant_entier(valeur) -> int:
    try:
        n = int(round(float(valeur)))
    except (TypeError, ValueError):
        raise ValueError("Montant invalide.")
    if n <= 0:
        raise ValueError("Le montant doit être un entier positif.")
    return n

def texte_pur(valeur, limite: int = 5000) -> str:
    """Validation 100 % TEXTE : aucune pièce jointe, aucun caractère de contrôle."""
    if request.files:
        raise ValueError("Contenu non autorisé : cette application n'accepte QUE du texte "
                         "(aucune image, vidéo ou fichier joint).")
    if not isinstance(valeur, str):
        raise ValueError("Texte attendu.")
    txt = valeur.replace("\x00", "").strip()
    if len(txt) > limite:
        raise ValueError(f"Texte trop long (maximum {limite} caractères).")
    return txt

def _plus_jours(jours: int) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=jours)).strftime("%Y-%m-%d %H:%M:%S")

def notifier(user_id: int, type_: str, texte: str, lien: str = "") -> None:
    conn = db()
    conn.execute("INSERT INTO notifications (user_id, type, texte, lien, cree_le) VALUES (?,?,?,?,?)",
                 (user_id, type_, texte[:400], lien, maintenant()))
    conn.commit()

_MENTION_RE = re.compile(r"@([A-Za-zÀ-ÿ]{4,30})\b")
def notifier_mentions(texte: str, auteur_id: int, lien: str) -> None:
    for pseudo in set(_MENTION_RE.findall(texte or "")):
        cible = db().execute("SELECT id, pseudo FROM users WHERE pseudo = ?", (pseudo,)).fetchone()
        if cible and cible["id"] != auteur_id:
            notifier(cible["id"], "mention", f"@{cible['pseudo']} vous a mentionné.", lien)

def nombre_non_lus_notifs(user_id: int) -> int:
    return int(db().execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND lu = 0",
                            (user_id,)).fetchone()["n"])

# --- 1) ESCROW INTELLIGENT (7 à 14 jours) ------------------------------------
def credit_escrow(user_id: int, montant: float, libelle: str, ref: str,
                  source_type: str, source_id: int) -> Optional[int]:
    """Crédite le créateur en mode « escrow » : bloqué 7 à 14 jours."""
    if config_v5("escrow_actif", "1") != "1":
        ecrire_portefeuille(user_id, "credit", montant, libelle, ref)
        return None
    jours = max(7, min(14, int(config_v5("escrow_jours", "7") or 7)))
    pf_id = _pf(user_id, "credit", montant,
                f"{libelle} — 🔒 escrow {jours} j", ref, statut="escrow")
    conn = db()
    conn.execute(
        "INSERT INTO escrow (pf_id, source_type, source_id, createur_id, montant, date_bloque, date_libere_prevue)"
        " VALUES (?,?,?,?,?,?,?)",
        (pf_id, source_type, source_id, user_id, int(round(montant)), maintenant(), _plus_jours(jours)))
    conn.commit()
    return pf_id

def liberer_escrow(escrow_id: int, decideur_id: int, auto: bool = False) -> bool:
    conn = db()
    ligne = conn.execute("SELECT * FROM escrow WHERE id = ? AND statut = 'bloque'", (escrow_id,)).fetchone()
    if ligne is None:
        return False
    conn.execute("UPDATE portefeuille SET statut = 'en_attente' WHERE id = ?", (ligne["pf_id"],))
    conn.execute("UPDATE escrow SET statut = ?, decide_le = ? WHERE id = ?",
                 ("libere_auto" if auto else "libere", maintenant(), escrow_id))
    conn.commit()
    notifier(ligne["createur_id"], "escrow",
             f"Escrow libéré : {ligne['montant']} {CONFIG['DEVISE']} disponibles.", "/portefeuille")
    journal_action("escrow_libere" if not auto else "escrow_libere_auto",
                   cible=f"escrow #{escrow_id}", utilisateur=decideur_id)
    try:
        journal_action2(ligne["createur_id"], "escrow", objet=f"séquestre #{escrow_id}",
                        montant=float(ligne["montant"]),
                        details=("Libération automatique" if auto else "Libérée par l'administrateur")
                        + " — montant disponible dans le portefeuille.")
    except Exception:
        pass
    return True

def rembourser_escrow(escrow_id: int, decideur_id: int) -> bool:
    """Litige : l'escrow est annulé (le créateur n'est jamais crédité)."""
    conn = db()
    ligne = conn.execute("SELECT * FROM escrow WHERE id = ? AND statut = 'bloque'", (escrow_id,)).fetchone()
    if ligne is None:
        return False
    conn.execute("UPDATE portefeuille SET statut = 'annule' WHERE id = ?", (ligne["pf_id"],))
    conn.execute("UPDATE escrow SET statut = 'rembourse', decide_le = ? WHERE id = ?", (maintenant(), escrow_id))
    conn.commit()
    notifier(ligne["createur_id"], "escrow",
             f"Escrow #{escrow_id} annulé après litige.", "/portefeuille")
    journal_action("escrow_rembourse", cible=f"escrow #{escrow_id}", utilisateur=decideur_id)
    try:
        journal_action2(ligne["createur_id"], "escrow", objet=f"séquestre #{escrow_id}",
                        montant=float(ligne["montant"]),
                        details="Séquestre annulé après litige — écriture annulée.")
    except Exception:
        pass
    return True

def liberer_escrows_dus() -> int:
    conn = db()
    dus = conn.execute("SELECT id FROM escrow WHERE statut = 'bloque' AND date_libere_prevue <= ?",
                       (maintenant(),)).fetchall()
    n = 0
    for ligne in dus:
        if liberer_escrow(ligne["id"], 0, auto=True):
            n += 1
    return n

# --- 2) POURBOIRES RÉCURRENTS (mensuels, pause/annulation en un clic) --------
def creer_pourboire_recurrent(expediteur_id: int, createur_id: int, montant: int, mot: str = "") -> int:
    conn = db()
    cur = conn.execute(
        "INSERT INTO pourboires_recurrents (expediteur_id, createur_id, montant, mot, prochain_prelevement, cree_le)"
        " VALUES (?,?,?,?,?,?)",
        (expediteur_id, createur_id, montant_entier(montant), (mot or "")[:280],
         _plus_jours(30), maintenant()))
    conn.commit()
    return int(cur.lastrowid)

def traiter_pourboires_recurrents() -> int:
    """Prélèvements mensuels dus : crée des pourboires classiques (validation admin)."""
    conn = db()
    dus = conn.execute(
        "SELECT * FROM pourboires_recurrents WHERE statut = 'actif' AND prochain_prelevement <= ?",
        (maintenant(),)).fetchall()
    n = 0
    for ligne in dus:
        try:
            creer_pourboire(ligne["expediteur_id"], ligne["createur_id"], ligne["montant"], ligne["mot"])
        except ValueError:
            continue
        conn.execute("UPDATE pourboires_recurrents SET prochain_prelevement = ? WHERE id = ?",
                     (_plus_jours(30), ligne["id"]))
        notifier(ligne["createur_id"], "pourboire_recurrent",
                 f"Nouveau pourboire mensuel de {ligne['montant']} {CONFIG['DEVISE']} en attente de validation.",
                 "/portefeuille")
        n += 1
    conn.commit()
    return n

# --- 3) PROGRAMME D'AFFILIATION ----------------------------------------------
def taux_affiliation_pct() -> int:
    try:
        return max(0, min(50, int(float(config_v5("taux_affiliation", "5") or 5))))
    except ValueError:
        return 5

def enregistrer_commission_affiliation(filleul_createur_id: int, source_type: str,
                                       source_id: int, montant_base: float) -> None:
    parrain = db().execute("SELECT parrain_id FROM users WHERE id = ?", (filleul_createur_id,)).fetchone()
    if not parrain or not parrain["parrain_id"] or parrain["parrain_id"] == filleul_createur_id:
        return
    taux = taux_affiliation_pct()
    if taux <= 0:
        return
    montant = int(round(float(montant_base) * taux / 100.0))
    if montant <= 0:
        return
    conn = db()
    conn.execute(
        "INSERT INTO commissions_affiliation (parrain_id, filleul_id, source_type, source_id,"
        " montant_base, taux_pct, montant, cree_le) VALUES (?,?,?,?,?,?,?,?)",
        (parrain["parrain_id"], filleul_createur_id, source_type, source_id,
         int(round(montant_base)), taux, montant, maintenant()))
    conn.commit()
    notifier(parrain["parrain_id"], "affiliation",
             f"Commission d'affiliation de {montant} {CONFIG['DEVISE']} en attente (filleul #{filleul_createur_id}).",
             "/portefeuille")

def valider_commission_affiliation(com_id: int, decideur_id: int) -> bool:
    conn = db()
    ligne = conn.execute("SELECT * FROM commissions_affiliation WHERE id = ? AND statut = 'en_attente'",
                         (com_id,)).fetchone()
    if ligne is None:
        return False
    conn.execute("UPDATE commissions_affiliation SET statut = 'valide' WHERE id = ?", (com_id,))
    conn.commit()
    pf_id = _pf(ligne["parrain_id"], "credit", ligne["montant"],
                f"Commission d'affiliation {ligne['taux_pct']} %", f"AFF-{com_id}")
    conn = db()
    conn.execute("UPDATE commissions_affiliation SET pf_id = ? WHERE id = ?", (pf_id, com_id))
    conn.commit()
    journal_action("affiliation_validee", cible=f"commission #{com_id}", utilisateur=decideur_id)
    try:
        journal_action2(ligne["parrain_id"], "affiliation", objet=f"commission #{com_id}",
                        montant=float(ligne["montant"]),
                        details="Commission d'affiliation créditée au portefeuille.")
    except Exception:
        pass
    return True

# --- 4) BOUTIQUE — CONTENUS EXCLUSIFS (textes payants à l'unité) --------------
def creer_contenu_exclusif(createur_id: int, titre: str, corps: str, prix: int) -> int:
    conn = db()
    cur = conn.execute(
        "INSERT INTO contenus_exclusifs (createur_id, titre, corps, prix, cree_le) VALUES (?,?,?,?,?)",
        (createur_id, titre[:200], corps[:20000], montant_entier(prix), maintenant()))
    conn.commit()
    return int(cur.lastrowid)

def acheter_contenu(acheteur_id: int, contenu_id: int) -> str:
    contenu = db().execute("SELECT * FROM contenus_exclusifs WHERE id = ?", (contenu_id,)).fetchone()
    if contenu is None:
        raise ValueError("Contenu introuvable.")
    if contenu["createur_id"] == acheteur_id:
        raise ValueError("Vous ne pouvez pas acheter votre propre contenu.")
    deja = db().execute("SELECT 1 FROM achats_contenus WHERE contenu_id = ? AND acheteur_id = ?",
                        (contenu_id, acheteur_id)).fetchone()
    if deja:
        return deja["reference"]
    commission, net, _ = calcul_commission(contenu["prix"])
    reference = "BC-" + secrets.token_hex(4).upper()
    conn = db()
    cur = conn.execute(
        "INSERT INTO achats_contenus (contenu_id, acheteur_id, montant, commission, net, reference, cree_le)"
        " VALUES (?,?,?,?,?,?,?)",
        (contenu_id, acheteur_id, contenu["prix"], int(round(commission)), int(round(net)), reference, maintenant()))
    conn.commit()
    journal_action("achat_contenu", cible=reference, utilisateur=acheteur_id)
    return reference

def valider_achat_contenu(achat_id: int, decideur_id: int) -> bool:
    conn = db()
    ligne = conn.execute("SELECT * FROM achats_contenus WHERE id = ? AND statut = 'en_attente'",
                         (achat_id,)).fetchone()
    if ligne is None:
        return False
    conn.execute("UPDATE achats_contenus SET statut = 'valide' WHERE id = ?", (achat_id,))
    conn.commit()
    createur = db().execute("SELECT createur_id FROM contenus_exclusifs WHERE id = ?",
                            (ligne["contenu_id"],)).fetchone()["createur_id"]
    pf_id = credit_escrow(createur, ligne["net"], f"Vente contenu exclusif — réf. {ligne['reference']}",
                          ligne["reference"], "achat", achat_id)
    conn = db()
    conn.execute("UPDATE achats_contenus SET pf_id = ? WHERE id = ?", (pf_id, achat_id))
    conn.commit()
    ecrire_recette("boutique", ligne["commission"], ligne["reference"])
    enregistrer_commission_affiliation(
        db().execute("SELECT createur_id FROM contenus_exclusifs WHERE id = ?",
                     (ligne["contenu_id"],)).fetchone()["createur_id"], "achat", achat_id, ligne["montant"])
    return True

# --- 5) PRÊTS ENTRE MEMBRES (taux + échéancier gérés par l'admin) -------------
def demander_pret(preteur_id: int, emprunteur_id: int, capital: int, taux_pct: int, nb_echeances: int) -> int:
    if preteur_id == emprunteur_id:
        raise ValueError("Un prêt à soi-même est impossible.")
    if not (1 <= int(nb_echeances) <= 24):
        raise ValueError("Nombre d'échéances : 1 à 24.")
    conn = db()
    cur = conn.execute(
        "INSERT INTO prets (preteur_id, emprunteur_id, capital, taux_pct, nb_echeances, cree_le)"
        " VALUES (?,?,?,?,?,?)",
        (preteur_id, emprunteur_id, montant_entier(capital), max(0, min(100, int(taux_pct))),
         int(nb_echeances), maintenant()))
    conn.commit()
    notifier(preteur_id, "pret", "Une demande de prêt vous est adressée.", "/prets")
    return int(cur.lastrowid)

def accepter_pret(pret_id: int, preteur_id: int) -> bool:
    conn = db()
    ligne = conn.execute("SELECT * FROM prets WHERE id = ? AND statut = 'demande'", (pret_id,)).fetchone()
    if ligne is None or ligne["preteur_id"] != preteur_id:
        return False
    conn.execute("UPDATE prets SET statut = 'attente_admin' WHERE id = ?", (pret_id,))
    conn.commit()
    return True

def valider_pret(pret_id: int, decideur_id: int) -> Dict[str, Any]:
    """L'ADMIN fixe/valide le taux et génère l'échéancier, puis débloque les fonds."""
    conn = db()
    ligne = conn.execute("SELECT * FROM prets WHERE id = ? AND statut = 'attente_admin'", (pret_id,)).fetchone()
    if ligne is None:
        return {"ok": False, "message": "Prêt introuvable ou déjà traité."}
    total = int(round(ligne["capital"] * (1 + ligne["taux_pct"] / 100.0)))
    nb = ligne["nb_echeances"]
    base = total // nb
    echeances = [base] * nb
    echeances[-1] += total - base * nb
    solde_preteur = solde(ligne["preteur_id"])
    if solde_preteur["tickets_payes"] < ligne["capital"]:
        return {"ok": False, "message": "Le prêteur n'a pas assez de tickets payés pour financer ce prêt."}
    pf_id = _pf(ligne["preteur_id"], "debit", ligne["capital"],
                f"Prêt #{pret_id} accordé à l'emprunteur", f"PRET-{pret_id}", statut="paye")
    pf_id2 = _pf(ligne["emprunteur_id"], "credit", ligne["capital"],
                                 f"Prêt #{pret_id} reçu (à rembourser : {total})", f"PRET-{pret_id}",
                                 statut="en_attente")
    for i, montant in enumerate(echeances, start=1):
        conn.execute("INSERT INTO prets_echeances (pret_id, numero, montant) VALUES (?,?,?)",
                     (pret_id, i, montant))
    conn.execute("UPDATE prets SET statut = 'actif', pf_id = ?, decide_le = ? WHERE id = ?",
                 (pf_id2, maintenant(), pret_id))
    conn.commit()
    notifier(ligne["emprunteur_id"], "pret", f"Prêt #{pret_id} validé : {ligne['capital']} reçus.", "/prets")
    journal_action("pret_valide", cible=f"pret #{pret_id}", utilisateur=decideur_id)
    try:
        journal_action2(ligne["preteur_id"], "prets", objet=f"prêt #{pret_id} — fonds prêtés",
                        montant=float(ligne["capital"]),
                        details="Votre prêt a été validé et débloqué vers l'emprunteur.")
        journal_action2(ligne["emprunteur_id"], "prets", objet=f"prêt #{pret_id} — fonds reçus",
                        montant=float(ligne["capital"]),
                        details="Prêt validé : échéancier de remboursement activé.")
    except Exception:
        pass
    return {"ok": True, "total": total, "echeances": echeances}

def rembourser_echeance(pret_id: int, numero: int, user_id: int) -> Dict[str, Any]:
    conn = db()
    pret = conn.execute("SELECT * FROM prets WHERE id = ? AND statut = 'actif'", (pret_id,)).fetchone()
    if pret is None or pret["emprunteur_id"] != user_id:
        return {"ok": False, "message": "Prêt introuvable."}
    ech = conn.execute("SELECT * FROM prets_echeances WHERE pret_id = ? AND numero = ? AND statut = 'en_attente'",
                       (pret_id, numero)).fetchone()
    if ech is None:
        return {"ok": False, "message": "Échéance introuvable ou déjà payée."}
    s = solde(user_id)
    if s["tickets_payes"] < ech["montant"]:
        return {"ok": False, "message": "Solde insuffisant pour cette échéance."}
    ecrire_portefeuille(user_id, "debit", ech["montant"],
                        f"Remboursement prêt #{pret_id} — échéance {numero}/{pret['nb_echeances']}",
                        f"PRET-{pret_id}-E{numero}", statut="paye")
    conn.execute("UPDATE prets_echeances SET statut = 'paye', paye_le = ? WHERE id = ?", (maintenant(), ech["id"]))
    restant = conn.execute("SELECT COUNT(*) AS n FROM prets_echeances WHERE pret_id = ? AND statut = 'en_attente'",
                           (pret_id,)).fetchone()["n"]
    if restant == 0:
        conn.execute("UPDATE prets SET statut = 'rembourse' WHERE id = ?", (pret_id,))
        notifier(pret["preteur_id"], "pret", f"Prêt #{pret_id} totalement remboursé.", "/prets")
    conn.commit()
    return {"ok": True}

# --- 6) CONVERSION FCFA ↔ USDT (partenaire local, taux configurable) ----------
def demander_conversion_fcfa_usdt(user_id: int, montant_fcfa: int) -> str:
    montant_fcfa = montant_entier(montant_fcfa)
    taux = montant_entier(config_v5("taux_usdt", "625") or 625)
    usdt = (montant_fcfa * 100) // taux  # en centièmes d'USDT
    if usdt <= 0:
        raise ValueError("Montant trop faible pour une conversion.")
    s = solde(user_id)
    if s["fcfa_payes"] < montant_fcfa:
        raise ValueError("Solde FCFA insuffisant (seuls les fonds PAYÉS sont convertibles).")
    reference = "CV-" + secrets.token_hex(4).upper()
    conn = db()
    cur = conn.execute(
        "INSERT INTO conversions (user_id, sens, montant_fcfa, montant_usdt, taux, reference, cree_le)"
        " VALUES (?,?,?,?,?,?,?)",
        (user_id, "fcfa_vers_usdt", montant_fcfa, usdt, taux, reference, maintenant()))
    conn.commit()
    journal_action("conversion_demandee", cible=reference, utilisateur=user_id)
    return reference

def valider_conversion(conv_id: int, decideur_id: int) -> bool:
    conn = db()
    ligne = conn.execute("SELECT * FROM conversions WHERE id = ? AND statut = 'en_attente'", (conv_id,)).fetchone()
    if ligne is None:
        return False
    pf_id = _pf(ligne["user_id"], "debit", ligne["montant_fcfa"],
                f"Conversion {ligne['montant_fcfa']} FCFA → {ligne['montant_usdt']/100:g} USDT "
                                f"(taux {ligne['taux']}) — réf. {ligne['reference']}",
                                ligne["reference"], statut="paye")
    conn.execute("UPDATE conversions SET statut = 'valide', pf_id = ?, decide_le = ? WHERE id = ?",
                 (pf_id, maintenant(), conv_id))
    conn.commit()
    notifier(ligne["user_id"], "conversion",
             f"Conversion validée : {ligne['montant_usdt']/100:g} USDT à retirer via le partenaire local.",
             "/portefeuille")
    journal_action("conversion_validee", cible=ligne["reference"], utilisateur=decideur_id)
    try:
        journal_action2(ligne["user_id"], "conversion", objet=f"réf. {ligne['reference']}",
                        montant=float(ligne["montant_fcfa"]),
                        details="Conversion FCFA → USDT validée par l'administrateur.")
    except Exception:
        pass
    return True

# --- 7) STORIES TEXTUELLES ÉPHÉMÈRES (24 h, abonnés uniquement) ---------------
def creer_story(auteur_id: int, corps: str) -> int:
    corps = texte_pur(corps, 500)
    if not corps:
        raise ValueError("Story vide.")
    conn = db()
    cur = conn.execute("INSERT INTO stories (auteur_id, corps, cree_le, expire_le) VALUES (?,?,?,?)",
                       (auteur_id, corps, maintenant(), _plus_jours(1)))
    conn.commit()
    notifier_mentions(corps, auteur_id, "/stories")
    return int(cur.lastrowid)

def purger_stories() -> int:
    conn = db()
    n = conn.execute("DELETE FROM stories WHERE expire_le <= ?", (maintenant(),)).rowcount
    conn.commit()
    return n

def stories_visibles(me_id: int):
    return db().execute(
        "SELECT s.*, u.pseudo,"
        " EXISTS(SELECT 1 FROM stories_vues v WHERE v.story_id = s.id AND v.viewer_id = ?) AS vue"
        " FROM stories s JOIN users u ON u.id = s.auteur_id"
        " WHERE s.expire_le > ? AND (s.auteur_id = ? OR EXISTS("
        "   SELECT 1 FROM follows f WHERE f.suiveur_id = ? AND f.suivi_id = s.auteur_id))"
        " ORDER BY s.id DESC LIMIT 100",
        (me_id, maintenant(), me_id, me_id)).fetchall()

# --- 8) SONDAGES --------------------------------------------------------------
def creer_sondage(auteur_id: int, corps: str, question: str, options: List[str]) -> int:
    corps = texte_pur(corps, 5000)
    question = texte_pur(question, 300)
    options = [texte_pur(o, 120) for o in options if texte_pur(o, 120)]
    if len(options) < 2 or len(options) > 6:
        raise ValueError("Un sondage doit avoir 2 à 6 options.")
    conn = db()
    cur = conn.execute("INSERT INTO posts (auteur_id, corps, cree_le) VALUES (?,?,?)",
                       (auteur_id, f"[Sondage] {question}", maintenant()))
    post_id = int(cur.lastrowid)
    cur = conn.execute("INSERT INTO sondages (post_id, question, cree_le) VALUES (?,?,?)",
                       (post_id, question, maintenant()))
    sondage_id = int(cur.lastrowid)
    for o in options:
        conn.execute("INSERT INTO sondages_options (sondage_id, libelle) VALUES (?,?)", (sondage_id, o))
    conn.commit()
    return sondage_id

def voter_sondage(sondage_id: int, option_id: int, user_id: int) -> bool:
    conn = db()
    if conn.execute("SELECT 1 FROM sondages WHERE id = ?", (sondage_id,)).fetchone() is None:
        return False
    if conn.execute("SELECT 1 FROM sondages_votes WHERE sondage_id = ? AND user_id = ?",
                    (sondage_id, user_id)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM sondages_options WHERE id = ? AND sondage_id = ?",
                    (option_id, sondage_id)).fetchone() is None:
        return False
    conn.execute("INSERT INTO sondages_votes (sondage_id, option_id, user_id, cree_le) VALUES (?,?,?,?)",
                 (sondage_id, option_id, user_id, maintenant()))
    conn.commit()
    return True

def resultats_sondage(sondage_id: int) -> Dict[str, Any]:
    options = db().execute("SELECT * FROM sondages_options WHERE sondage_id = ? ORDER BY id",
                           (sondage_id,)).fetchall()
    total = int(db().execute("SELECT COUNT(*) AS n FROM sondages_votes WHERE sondage_id = ?",
                             (sondage_id,)).fetchone()["n"])
    resultats = []
    for o in options:
        n = int(db().execute("SELECT COUNT(*) AS n FROM sondages_votes WHERE option_id = ?",
                             (o["id"],)).fetchone()["n"])
        resultats.append({"id": o["id"], "libelle": o["libelle"], "votes": n,
                          "pct": round(100 * n / total) if total else 0})
    return {"options": resultats, "total": total}

# --- 9) THREADS IMBRIQUÉS (style Reddit) --------------------------------------
def arbre_commentaires(post_id: int) -> List[Dict[str, Any]]:
    lignes = db().execute(
        "SELECT c.*, u.pseudo FROM comments c JOIN users u ON u.id = c.auteur_id"
        " WHERE c.post_id = ? ORDER BY c.id ASC", (post_id,)).fetchall()
    par_id: Dict[int, Dict[str, Any]] = {}
    racines: List[Dict[str, Any]] = []
    for l in lignes:
        par_id[l["id"]] = {"row": dict(l), "enfants": []}
    for l in lignes:
        noeud = par_id[l["id"]]
        parent = l["parent_id"] if "parent_id" in l.keys() else None
        if parent and parent in par_id:
            par_id[parent]["enfants"].append(noeud)
        else:
            racines.append(noeud)
    return racines

def _rendre_noeud(noeud: Dict[str, Any], post_id: int, profondeur: int, csrf: str) -> str:
    c = noeud["row"]
    indent = min(profondeur, 8) * 18
    from html import escape
    corps = escape(str(c["corps"]))
    bouton = (f'<form method="post" action="/p/repondre/{c["id"]}" class="form-comms">'
              f'<input type="hidden" name="csrf" value="{escape(csrf)}">'
              f'<input name="corps" maxlength="500" placeholder="Répondre…" required>'
              f'<button class="btn-sec">Répondre</button></form>') if profondeur < 8 else ""
    enfants = "".join(_rendre_noeud(e, post_id, profondeur + 1, csrf) for e in noeud["enfants"])
    return (f'<div class="comm" style="margin-left:{indent}px">'
            f'<div><b>{escape(str(c["pseudo"]))}</b>'
            f'<div class="corps">{corps}</div></div>'
            f'<span class="muet">{escape(str(c["cree_le"]))}</span>{bouton}{enfants}</div>')

def rendu_thread(post_id: int) -> Markup:
    csrf = jeton_csrf()
    return Markup("".join(_rendre_noeud(r, post_id, 0, csrf) for r in arbre_commentaires(post_id)))

def repondre_commentaire(comment_id: int, auteur_id: int, corps: str) -> int:
    corps = texte_pur(corps, 500)
    parent = db().execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if parent is None or not corps:
        raise ValueError("Réponse invalide.")
    conn = db()
    cur = conn.execute(
        "INSERT INTO comments (post_id, auteur_id, parent_id, corps, cree_le) VALUES (?,?,?,?,?)",
        (parent["post_id"], auteur_id, comment_id, corps, maintenant()))
    conn.commit()
    if parent["auteur_id"] != auteur_id:
        notifier(parent["auteur_id"], "reponse", "Quelqu'un a répondu à votre commentaire.",
                 f"/p/thread/{parent['post_id']}")
    notifier_mentions(corps, auteur_id, f"/p/thread/{parent['post_id']}")
    return int(cur.lastrowid)

# --- 10) HASHTAGS + TENDANCES --------------------------------------------------
_HASHTAG_RE = re.compile(r"#([\wÀ-ÿ]{2,40})")
def tendances_du_jour(jours: int = 7) -> List[Dict[str, Any]]:
    depuis = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=jours)).strftime("%Y-%m-%d %H:%M:%S")
    compte: Dict[str, int] = {}
    sources = db().execute("SELECT corps FROM posts WHERE cree_le >= ?", (depuis,)).fetchall() + \
              db().execute("SELECT corps FROM stories WHERE cree_le >= ?", (depuis,)).fetchall()
    for (corps,) in sources:
        for tag in set(_HASHTAG_RE.findall(corps or "")):
            compte[tag.lower()] = compte.get(tag.lower(), 0) + 1
    return sorted([{"tag": t, "n": n} for t, n in compte.items()], key=lambda x: -x["n"])[:20]

# --- 11) LIVE — DISCUSSION TEXTUELLE EN DIRECT (aucun média) -------------------
def creer_live(hote_id: int, titre: str) -> int:
    titre = texte_pur(titre, 120)
    conn = db()
    cur = conn.execute("INSERT INTO lives (hote_id, titre, cree_le) VALUES (?,?,?)",
                       (hote_id, titre, maintenant()))
    conn.commit()
    return int(cur.lastrowid)

def message_live(live_id: int, user_id: int, corps: str) -> int:
    corps = texte_pur(corps, 500)
    if not corps:
        raise ValueError("Message vide.")
    live = db().execute("SELECT * FROM lives WHERE id = ?", (live_id,)).fetchone()
    if live is None or live["statut"] != "ouvert":
        raise ValueError("Live introuvable ou fermé.")
    conn = db()
    cur = conn.execute("INSERT INTO live_messages (live_id, user_id, corps, cree_le) VALUES (?,?,?,?)",
                       (live_id, user_id, corps, maintenant()))
    conn.commit()
    return int(cur.lastrowid)

# --- Intégration escrow + affiliation dans les validations existantes ---------
_valider_abonnement_original = valider_abonnement
def valider_abonnement(abo_id: int, decideur_id: int) -> Optional[Dict[str, float]]:  # noqa: F811
    conn = db()
    ligne = conn.execute("SELECT * FROM abonnements WHERE id = ? AND statut = 'en_attente'", (abo_id,)).fetchone()
    if ligne is None:
        return None
    conn.execute("UPDATE abonnements SET statut='valide', valide_le=? WHERE id=?", (maintenant(), abo_id))
    conn.commit()
    credit_escrow(ligne["createur_id"], ligne["net"],
                  f"Abonnement {ligne['palier']:g} FCFA — réf. {ligne['reference']}",
                  ligne["reference"], "abonnement", abo_id)
    ecrire_recette("abonnement", ligne["commission"], ligne["reference"])
    enregistrer_commission_affiliation(ligne["createur_id"], "abonnement", abo_id, ligne["palier"])
    try:
        journal_action2(ligne["createur_id"], "abonnement", objet=f"abonnement #{abo_id} validé — réf. {ligne['reference']}",
                        montant=float(ligne["net"]),
                        details="Validé par l'administrateur : net placé sous séquestre, disponible après le délai de sécurité.")
        journal_action2(ligne["abonne_id"], "abonnement", objet=f"abonnement #{abo_id} confirmé — réf. {ligne['reference']}",
                        montant=float(ligne["palier"]),
                        details="Votre paiement a été confirmé : l'abonnement est actif.")
    except Exception:
        pass
    return {"commission": ligne["commission"], "net": ligne["net"], "createur_id": ligne["createur_id"]}

_valider_pourboire_original = valider_pourboire
def valider_pourboire(tip_id: int, decideur_id: int) -> Optional[Dict[str, float]]:  # noqa: F811
    conn = db()
    ligne = conn.execute("SELECT * FROM pourboires WHERE id = ? AND statut = 'en_attente'", (tip_id,)).fetchone()
    if ligne is None:
        return None
    conn.execute("UPDATE pourboires SET statut='valide' WHERE id=?", (tip_id,))
    conn.commit()
    credit_escrow(ligne["createur_id"], ligne["net"],
                  "Pourboire reçu" + (f" — « {ligne['mot']} »" if ligne["mot"] else ""),
                  f"TIP-{tip_id}", "pourboire", tip_id)
    ecrire_recette("pourboire", ligne["commission"], f"TIP-{tip_id}")
    try:
        journal_action2(ligne["createur_id"], "pourboire", objet=f"pourboire #{tip_id} reçu",
                        montant=float(ligne["net"]),
                        details="Validé par l'administrateur : net placé sous séquestre, disponible après le délai de sécurité.")
        journal_action2(ligne["expediteur_id"], "pourboire", objet=f"pourboire #{tip_id} envoyé",
                        montant=float(ligne["montant"]),
                        details="Votre pourboire a été confirmé par l'administrateur.")
    except Exception:
        pass
    return {"commission": ligne["commission"], "net": ligne["net"], "createur_id": ligne["createur_id"]}

# --- Cron léger à chaque requête (escrow, stories, pourboires récurrents) -----
@app.before_request
def _v5_cron():
    try:
        liberer_escrows_dus()
        purger_stories()
        traiter_pourboires_recurrents()
    except Exception:  # ne jamais bloquer une requête à cause du cron
        pass

@app.context_processor
def _contexte_v5():
    me = utilisateur_courant()
    return {"non_lus_notifs": nombre_non_lus_notifs(me["id"]) if me else 0,
            "partenaire_usdt": config_v5("partenaire_usdt", ""),
            "taux_usdt": config_v5("taux_usdt", "")}

# =============================================================================
# 🕓 V8 — HISTORIQUE PERSONNEL de chaque utilisateur (100 % TEXTE)
# Chaque action de chaque membre est consignée dans SA propre table, visible
# par LUI SEUL (la route filtre toujours par l'identifiant de session).
# =============================================================================
LIBELLES_HISTO = {
    "connexion": {"icone": "🔓", "nom": "Connexion"},
    "deconnexion": {"icone": "🔒", "nom": "Déconnexion"},
    "publication": {"icone": "📝", "nom": "Publication"},
    "commentaire": {"icone": "💬", "nom": "Commentaire"},
    "j_aime": {"icone": "❤️", "nom": "J'aime"},
    "suivre": {"icone": "🤝", "nom": "Suivre / ne plus suivre"},
    "abonnement": {"icone": "⭐", "nom": "Abonnement payant"},
    "pourboire": {"icone": "🎁", "nom": "Pourboire"},
    "message_prive": {"icone": "✉️", "nom": "Message privé"},
    "discussion_ouverte": {"icone": "🗣️", "nom": "Discussion ouverte"},
    "plainte": {"icone": "⚖️", "nom": "Plainte / assistance"},
    "portefeuille": {"icone": "👛", "nom": "Portefeuille"},
    "boutique": {"icone": "🛒", "nom": "Boutique"},
    "prets": {"icone": "🤲", "nom": "Prêts entre membres"},
    "sondage": {"icone": "📊", "nom": "Sondage"},
    "story": {"icone": "⏳", "nom": "Story"},
    "live": {"icone": "📡", "nom": "Live textuel"},
    "conversion": {"icone": "💱", "nom": "Conversion FCFA ↔ USDT"},
    "profil_vu": {"icone": "👀", "nom": "Profil consulté"},
    "parametres": {"icone": "⚙️", "nom": "Paramètres"},
    "notifications": {"icone": "🔔", "nom": "Notifications"},
    "recherche": {"icone": "🔎", "nom": "Recherche / IA"},
    "navigation": {"icone": "🧭", "nom": "Navigation"},
    "groupe": {"icone": "👥", "nom": "Groupe privé"},
    "recurrence": {"icone": "🔁", "nom": "Pourboire récurrent"},
    "reversement": {"icone": "🏦", "nom": "Reversement Mobile Money"},
    "escrow": {"icone": "🔐", "nom": "Séquestre (escrow)"},
    "pret_valide": {"icone": "🤲", "nom": "Prêt validé"},
    "conversion": {"icone": "💱", "nom": "Conversion FCFA ↔ USDT"},
    "affiliation": {"icone": "🧲", "nom": "Commission d'affiliation"},
    "transfert": {"icone": "📤", "nom": "Versement / transfert"},
}
HISTORIQUE_ACTIONS = tuple(LIBELLES_HISTO.keys())


def journal_action2(utilisateur, action: str, objet: str = "", details: str = "",
                    montant=None) -> None:
    """Consigne une activité dans l'historique PERSONNEL de l'utilisateur.
    N'interrompt jamais l'action appelante ; n'enregistre rien sans utilisateur."""
    try:
        utilisateur = int(utilisateur) if utilisateur else None
    except (TypeError, ValueError):
        utilisateur = None
    if utilisateur is None:
        return
    try:
        conn = db()
        conn.execute(
            "INSERT INTO historique_utilisateur"
            " (user_id, action, objet, details, montant, adresse_ip, cree_le)"
            " VALUES (?,?,?,?,?,?,?)",
            (utilisateur, (action or "navigation")[:40], (objet or "")[:160],
             (details or "")[:500],
             (float(montant) if montant else None),
             (request.remote_addr or "")[:60], maintenant()))
        conn.commit()
    except Exception:
        pass


@app.route("/mon-historique")
def mon_historique():
    """L'historique personnel du membre connecté — JAMAIS celui d'un autre."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    action = (request.args.get("action") or "").strip()
    cherche = (request.args.get("q") or "").strip()
    try:
        page_num = max(1, int(request.args.get("page", 1) or 1))
    except ValueError:
        page_num = 1
    par_page = 400
    conn = db()
    conditions, params = ["user_id = ?"], [me["id"]]
    if action:
        conditions.append("action = ?")
        params.append(action)
    if cherche:
        conditions.append("(objet LIKE ? OR details LIKE ?)")
        params += ["%" + cherche + "%", "%" + cherche + "%"]
    where = " WHERE " + " AND ".join(conditions)
    total = conn.execute("SELECT COUNT(*) FROM historique_utilisateur" + where,
                         params).fetchone()[0]
    lignes = [dict(l) for l in conn.execute(
        "SELECT * FROM historique_utilisateur" + where +
        " ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [par_page, (page_num - 1) * par_page]).fetchall()]
    compte = {r["action"]: r["n"] for r in conn.execute(
        "SELECT action, COUNT(*) AS n FROM historique_utilisateur"
        " WHERE user_id = ? GROUP BY action", (me["id"],)).fetchall()}
    totaux = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(montant),0) AS m"
        " FROM historique_utilisateur WHERE user_id = ?", (me["id"],)).fetchone()
    pages = max(1, (total + par_page - 1) // par_page)
    return page("mon_historique.html", titre="Mon historique", lignes=lignes,
                total=total, page_num=page_num, pages=pages, action=action,
                cherche=cherche, actions=HISTORIQUE_ACTIONS, compte=compte,
                libelles=LIBELLES_HISTO, totaux=totaux)


@app.route("/api/mon-historique")
def api_mon_historique():
    """Historique personnel au format JSON (pour applications externes du membre)."""
    me = utilisateur_courant()
    if me is None:
        return jsonify({"erreur": "connexion requise"}), 401
    lignes = db().execute(
        "SELECT action, objet, details, montant, cree_le FROM historique_utilisateur"
        " WHERE user_id = ? ORDER BY id DESC LIMIT 500", (me["id"],)).fetchall()
    return jsonify([dict(l) for l in lignes])


@app.route("/mon-historique.csv")
def mon_historique_csv():
    """Export CSV (Excel français) de l'historique personnel — ses données, sa main."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    action = (request.args.get("action") or "").strip()
    cherche = (request.args.get("q") or "").strip()
    conditions, params = ["user_id = ?"], [me["id"]]
    if action:
        conditions.append("action = ?")
        params.append(action)
    if cherche:
        conditions.append("(objet LIKE ? OR details LIKE ?)")
        params += ["%" + cherche + "%", "%" + cherche + "%"]
    lignes = db().execute(
        "SELECT cree_le, action, objet, details, montant FROM historique_utilisateur WHERE "
        + " AND ".join(conditions) + " ORDER BY id DESC LIMIT 5000", params).fetchall()
    buf = io.StringIO()
    ecrivain = csv.writer(buf, delimiter=";")
    ecrivain.writerow(["Horodatage", "Action", "Objet", "Détails", "Montant (FCFA)"])
    for l in lignes:
        ecrivain.writerow([l["cree_le"], l["action"], l["objet"], l["details"],
                           "" if l["montant"] is None else l["montant"]])
    return _csv_reponse(buf.getvalue(), "mon-historique-toutbot.csv")


@app.route("/mon-historique/vider", methods=["POST"])
def mon_historique_vider():
    """Le membre efface TOUT son historique personnel — et rien que le sien."""
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    conn.execute("DELETE FROM historique_utilisateur WHERE user_id = ?", (me["id"],))
    conn.commit()
    journal_action2(me["id"], "parametres", objet="historique personnel",
                    details="Historique effacé par son propriétaire.")
    session["flash"] = "Votre historique personnel a été effacé."
    return redirect(url_for("mon_historique"))


TEMPLATES["mon_historique.html"] = """{% block contenu %}
<div class="carte"><h1>🕓 Mon historique personnel</h1>
<p class="muet">Chaque action de votre compte est consignée ICI, visible par vous seul :
publications, « J'aime », commentaires, abonnements, pourboires, messages, plaintes,
boutique, prêts, sondages, stories, lives, profils visités, réglages, connexions.
Exportez-la ou effacez-la quand vous voulez.</p>
<div class="grid">
  <div class="tuile"><b>{{ totaux['n'] }}</b>activités consignées</div>
  <div class="tuile"><b>{{ '%g'|format(totaux['m'] or 0) }} {{ devise }}</b>montant cumulé de vos échanges</div>
</div></div>
<div class="carte"><h2>Filtres — cliquez une catégorie</h2>
<p class="row"><a class="btn btn-sec{{ ' btn-active' if not action else '' }}" href="{{ url_for('mon_historique') }}">Tout ({{ total }})</a>
{% for a in actions %}{% if compte.get(a) %}<a class="btn btn-sec{{ ' btn-active' if action == a else '' }}" href="{{ url_for('mon_historique', action=a) }}">{{ libelles[a]['icone'] }} {{ libelles[a]['nom'] }} ({{ compte[a] }})</a>{% endif %}{% endfor %}</p>
<form method="get" class="row" action="{{ url_for('mon_historique') }}">
  <input name="q" value="{{ cherche }}" placeholder="Rechercher dans mon historique…" aria-label="Recherche dans l'historique">
  {% if action %}<input type="hidden" name="action" value="{{ action }}">{% endif %}
  <button>Rechercher</button>{% if cherche %}<a class="btn btn-sec" href="{{ url_for('mon_historique', action=action) }}">Réinitialiser</a>{% endif %}
</form></div>
<div class="carte"><h2>Journal — {{ total }} activité(s){{ ' (filtre : ' + action + ')' if action else '' }}{{ ' (recherche : ' + cherche + ')' if cherche else '' }}</h2>
<div id="histo-liste"></div>
<div id="histo-suite" class="muet">Chargement…</div>
<p class="row"><button class="btn-sec" type="button" onclick="copier_histo(this)">📋 Copier tout l'historique affiché</button>
<a class="btn btn-sec" href="{{ url_for('mon_historique_csv', action=action, q=cherche) }}">⬇️ Exporter en CSV</a>
<a class="btn btn-sec" href="{{ url_for('api_mon_historique') }}">🔗 Format JSON</a></p>
{% if pages > 1 %}<p class="row">
  {% if page_num > 1 %}<a class="btn btn-sec" href="{{ url_for('mon_historique', page=page_num-1, action=action, q=cherche) }}">← Page précédente</a>{% endif %}
  <span class="muet">Page {{ page_num }} / {{ pages }}</span>
  {% if page_num < pages %}<a class="btn btn-sec" href="{{ url_for('mon_historique', page=page_num+1, action=action, q=cherche) }}">Page suivante →</a>{% endif %}
</p>{% endif %}</div>
<div class="carte"><h2>Confidentialité de cet historique</h2>
<p class="muet">Votre historique est STRICTEMENT personnel : la route filtre toujours par VOTRE
session — aucune autre personne ne peut le consulter, pas même l'administrateur.
Cliquez une ligne pour la copier ; double-clic copie un paragraphe.</p>
<form method="post" action="{{ url_for('mon_historique_vider') }}"
      onsubmit="return confirm('Effacer DÉFINITIVEMENT tout votre historique personnel ?')">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <button class="btn-rouge">🧹 Effacer tout mon historique</button>
</form></div>
<script>
var LIGNES_HISTO = {{ lignes|tojson }};
var LIBELLES_HISTO = {{ libelles|tojson }};
(function(){
  var i = 0, zone = document.getElementById('histo-liste'), sent = document.getElementById('histo-suite');
  function lot(){
    var fin = Math.min(i + 25, LIGNES_HISTO.length), frag = document.createDocumentFragment();
    for (; i < fin; i++) {
      var l = LIGNES_HISTO[i], lib = LIBELLES_HISTO[l.action] || {icone:'•', nom:l.action};
      var d = document.createElement('div');
      d.className = 'comm ligne-histo'; d.tabIndex = 0; d.setAttribute('role','button');
      var gauche = document.createElement('div');
      var b = document.createElement('b'); b.textContent = lib.icone + ' ' + lib.nom; gauche.appendChild(b);
      if (l.objet) { var o = document.createElement('div'); o.className = 'corps'; o.textContent = l.objet; gauche.appendChild(o); }
      if (l.details) { var dt = document.createElement('div'); dt.className = 'corps muet'; dt.textContent = l.details; gauche.appendChild(dt); }
      var droite = document.createElement('span'); droite.className = 'muet';
      droite.textContent = l.cree_le + (l.montant != null ? (' · ' + l.montant + ' FCFA') : '');
      d.appendChild(gauche); d.appendChild(droite);
      var copier = function(){ copier_texte(d.textContent.trim(), null); toast('Activité copiée ✓'); };
      d.addEventListener('click', function(e){ if (e.target.closest('button,a')) return; copier(); });
      d.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); copier(); } });
      frag.appendChild(d);
    }
    zone.appendChild(frag);
    if (i >= LIGNES_HISTO.length) { sent.textContent = '— fin de l\\'historique (' + LIGNES_HISTO.length + ' activités) —'; io.disconnect(); }
  }
  var io = new IntersectionObserver(function(es){ if (es[0].isIntersecting) lot(); }, {rootMargin:'200px'});
  io.observe(sent); lot();
  window.copier_histo = function(btn){
    var t = [];
    LIGNES_HISTO.forEach(function(l){
      var lib = LIBELLES_HISTO[l.action] || {icone:'•', nom:l.action};
      t.push('[' + l.cree_le + '] ' + lib.nom + (l.objet ? ' — ' + l.objet : '') + (l.details ? ' — ' + l.details : '') + (l.montant != null ? ' — ' + l.montant + ' FCFA' : ''));
    });
    copier_texte('— Mon historique ToutBot Mundo —\\n\\n' + t.join('\\n'), btn);
    toast(t.length + ' activités copiées ✓');
  };
})();
</script>
{% endblock %}"""

# =============================================================================
# 🧭 PAGES ET ROUTES V5
# =============================================================================
@app.route("/notifications")
def notifications_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    lignes = db().execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 100",
                          (me["id"],)).fetchall()
    return page("notifications.html", titre="Notifications", lignes=lignes)

@app.route("/notifications/lire", methods=["POST"])
def notifications_lire():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    conn = db()
    conn.execute("UPDATE notifications SET lu = 1 WHERE user_id = ?", (utilisateur_courant()["id"],))
    conn.commit()
    journal_action2(session.get("uid"), "notifications", objet="notifications marquées comme lues")
    return redirect(url_for("notifications_page"))

@app.route("/stories", methods=["GET"])
def stories_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    return page("stories.html", titre="Stories textuelles (24 h)", stories=stories_visibles(me["id"]))

@app.route("/stories/creer", methods=["POST"])
def stories_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    try:
        creer_story(utilisateur_courant()["id"], request.form.get("corps", ""))
        session["flash"] = "Story publiée : visible 24 h par vos abonnés uniquement."
    except ValueError as exc:
        session["flash"] = str(exc)
    journal_action2(session.get("uid"), "story", objet="stories textuelles",
                    details="Création ou vue consignée(s).")
    return redirect(url_for("stories_page"))

@app.route("/stories/<int:story_id>/vue", methods=["POST"])
def stories_vue(story_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    conn.execute("INSERT OR IGNORE INTO stories_vues (story_id, viewer_id, cree_le) VALUES (?,?,?)",
                 (story_id, me["id"], maintenant()))
    conn.commit()
    journal_action2(session.get("uid"), "story", objet="stories textuelles",
                    details="Création ou vue consignée(s).")
    return redirect(url_for("stories_page"))

@app.route("/sondages", methods=["GET"])
def sondages_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    sondages = db().execute(
        "SELECT s.*, p.id AS post_id, u.pseudo FROM sondages s"
        " JOIN posts p ON p.id = s.post_id JOIN users u ON u.id = p.auteur_id"
        " ORDER BY s.id DESC LIMIT 50").fetchall()
    donnees = {s["id"]: resultats_sondage(s["id"]) for s in sondages}
    mes_votes = {r["sondage_id"] for r in db().execute(
        "SELECT sondage_id FROM sondages_votes WHERE user_id = ?", (me["id"],)).fetchall()}
    return page("sondages.html", titre="Sondages — votes en temps réel", sondages=sondages,
                donnees=donnees, mes_votes=mes_votes)

@app.route("/sondage/creer", methods=["POST"])
def sondage_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        options = [request.form.get(f"option{i}", "") for i in range(1, 7)]
        sid = creer_sondage(me["id"], request.form.get("corps", ""), request.form.get("question", ""), options)
        notifier_mentions(request.form.get("corps", ""), me["id"], "/sondages")
        journal_action("publication", cible=f"sondage #{sid}", utilisateur=me["id"])
        session["flash"] = "Sondage publié."
    except ValueError as exc:
        session["flash"] = str(exc)
    journal_action2(session.get("uid"), "sondage", objet="sondages",
                    details="Création ou vote consigné(s).")
    return redirect(url_for("sondages_page"))

@app.route("/sondage/<int:sondage_id>/voter", methods=["POST"])
def sondage_voter(sondage_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    try:
        ok = voter_sondage(sondage_id, int(request.form.get("option", 0)), utilisateur_courant()["id"])
        session["flash"] = "Vote enregistré." if ok else "Vote impossible (déjà voté ou option invalide)."
    except (ValueError, TypeError):
        session["flash"] = "Vote invalide."
    journal_action2(session.get("uid"), "sondage", objet="sondages",
                    details="Création ou vote consigné(s).")
    return redirect(url_for("sondages_page"))

@app.route("/p/thread/<int:post_id>", methods=["GET"])
def thread_page(post_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    p = db().execute("SELECT p.*, u.pseudo FROM posts p JOIN users u ON u.id = p.auteur_id"
                     " WHERE p.id = ?", (post_id,)).fetchone()
    if p is None:
        return "Publication inconnue.", 404
    return page("thread.html", titre=f"Discussion #{post_id}", p=p, arbre=rendu_thread(post_id))

@app.route("/p/repondre/<int:comment_id>", methods=["POST"])
def repondre(comment_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        repondre_commentaire(comment_id, me["id"], request.form.get("corps", ""))
        journal_action("commentaire", cible=f"réponse #{comment_id}", utilisateur=me["id"])
    except ValueError as exc:
        session["flash"] = str(exc)
    parent = db().execute("SELECT post_id FROM comments WHERE id = ?", (comment_id,)).fetchone()
    return redirect(f"/p/thread/{parent['post_id']}" if parent else url_for("fil"))

@app.route("/tendances", methods=["GET"])
def tendances_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    return page("tendances.html", titre="Tendances du jour", tendances=tendances_du_jour())

@app.route("/hashtag/<tag>", methods=["GET"])
def hashtag_page(tag: str):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    moi = f"#{tag.lower()}"
    posts = [p for p in db().execute(
        "SELECT p.*, u.pseudo FROM posts p JOIN users u ON u.id = p.auteur_id"
        " ORDER BY p.id DESC LIMIT 500").fetchall()
        if any(t.lower() == tag.lower() for t in _HASHTAG_RE.findall(p["corps"] or ""))]
    return page("hashtag.html", titre=f"#{tag}", tag=moi, posts=posts[:100])

@app.route("/boutique", methods=["GET"])
def boutique_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    items = db().execute(
        "SELECT c.*, u.pseudo,"
        " (SELECT COUNT(*) FROM achats_contenus a WHERE a.contenu_id = c.id AND a.acheteur_id = ?"
        "   AND a.statut = 'valide') AS achete"
        " FROM contenus_exclusifs c JOIN users u ON u.id = c.createur_id"
        " ORDER BY c.id DESC LIMIT 100", (me["id"],)).fetchall()
    return page("boutique.html", titre="Boutique — contenus exclusifs", items=items)

@app.route("/boutique/creer", methods=["POST"])
def boutique_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        titre = texte_pur(request.form.get("titre", ""), 200)
        corps = texte_pur(request.form.get("corps", ""), 20000)
        if not titre or not corps:
            raise ValueError("Titre et texte du contenu requis.")
        creer_contenu_exclusif(me["id"], titre, corps, request.form.get("prix", "0"))
        session["flash"] = "Contenu exclusif publié dans la boutique."
    except ValueError as exc:
        session["flash"] = str(exc)
    journal_action2(session.get("uid"), "boutique", objet="boutique — contenus exclusifs",
                    details="Publication ou demande d'achat consignée(s).")
    return redirect(url_for("boutique_page"))

@app.route("/boutique/acheter/<int:contenu_id>", methods=["POST"])
def boutique_acheter(contenu_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    try:
        reference = acheter_contenu(utilisateur_courant()["id"], contenu_id)
        session["flash"] = (f"Demande d'achat enregistrée (réf. {reference}). Payez sur le numéro Mobile Money "
                            "de l'administrateur : le contenu sera déverrouillé après validation.")
    except ValueError as exc:
        session["flash"] = str(exc)
    journal_action2(session.get("uid"), "boutique", objet="boutique — contenus exclusifs",
                    details="Publication ou demande d'achat consignée(s).")
    return redirect(url_for("boutique_page"))

@app.route("/prets", methods=["GET"])
def prets_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    mes = db().execute(
        "SELECT p.*, pu.pseudo AS pseudo_preteur, eu.pseudo AS pseudo_emprunteur FROM prets p"
        " JOIN users pu ON pu.id = p.preteur_id JOIN users eu ON eu.id = p.emprunteur_id"
        " WHERE p.preteur_id = ? OR p.emprunteur_id = ? ORDER BY p.id DESC LIMIT 50",
        (me["id"], me["id"])).fetchall()
    echeances = {p["id"]: db().execute("SELECT * FROM prets_echeances WHERE pret_id = ? ORDER BY numero",
                                       (p["id"],)).fetchall() for p in mes}
    return page("prets.html", titre="Prêts entre membres", mes=mes, echeances=echeances)

@app.route("/prets/demander", methods=["POST"])
def prets_demander():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        preteur = utilisateur_par_id(int(request.form.get("preteur", 0)))
        if preteur is None:
            raise ValueError("Prêteur introuvable.")
        demander_pret(preteur["id"], me["id"], request.form.get("capital", "0"),
                      request.form.get("taux", "0"), request.form.get("nb", "1"))
        session["flash"] = "Demande de prêt envoyée."
    except (ValueError, TypeError) as exc:
        session["flash"] = str(exc) if isinstance(exc, ValueError) else "Demande invalide."
    journal_action2(session.get("uid"), "prets", objet="prêts entre membres",
                    details="Demande, acceptation ou remboursement consigné(s).")
    return redirect(url_for("prets_page"))

@app.route("/prets/<int:pret_id>/accepter", methods=["POST"])
def prets_accepter(pret_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    ok = accepter_pret(pret_id, utilisateur_courant()["id"])
    session["flash"] = "Prêt accepté : en attente de validation de l'administrateur." if ok else "Action impossible."
    journal_action2(session.get("uid"), "prets", objet="prêts entre membres",
                    details="Demande, acceptation ou remboursement consigné(s).")
    return redirect(url_for("prets_page"))

@app.route("/prets/<int:pret_id>/payer/<int:numero>", methods=["POST"])
def prets_payer(pret_id: int, numero: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    res = rembourser_echeance(pret_id, numero, utilisateur_courant()["id"])
    session["flash"] = res["message"] if not res["ok"] else "Échéance remboursée."
    journal_action2(session.get("uid"), "prets", objet="prêts entre membres",
                    details="Demande, acceptation ou remboursement consigné(s).")
    return redirect(url_for("prets_page"))

@app.route("/portefeuille/conversion", methods=["GET", "POST"])
def conversion_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    if request.method == "POST":
        try:
            reference = demander_conversion_fcfa_usdt(me["id"], request.form.get("montant", "0"))
            session["flash"] = (f"Demande de conversion enregistrée (réf. {reference}). "
                                "Elle sera validée par l'administrateur, puis le retrait USDT se fait "
                                "via le partenaire local.")
        except ValueError as exc:
            session["flash"] = str(exc)
        journal_action2(session.get("uid"), "conversion", objet="conversion FCFA ↔ USDT",
                    details="Demande enregistrée, en attente de validation.")
    return redirect(url_for("conversion_page"))
    mes = db().execute("SELECT * FROM conversions WHERE user_id = ? ORDER BY id DESC LIMIT 30",
                       (me["id"],)).fetchall()
    return page("conversion.html", titre="Conversion FCFA ↔ USDT", mes=mes,
                taux=config_v5("taux_usdt", "625"), partenaire=config_v5("partenaire_usdt", ""))

@app.route("/live", methods=["GET"])
def live_liste():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    lives = db().execute(
        "SELECT l.*, u.pseudo,"
        " (SELECT COUNT(*) FROM live_messages m WHERE m.live_id = l.id) AS nb_messages"
        " FROM lives l JOIN users u ON u.id = l.hote_id ORDER BY l.id DESC LIMIT 50").fetchall()
    return page("live_liste.html", titre="Lives — discussion textuelle", lives=lives)

@app.route("/live/creer", methods=["POST"])
def live_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    try:
        lid = creer_live(utilisateur_courant()["id"], request.form.get("titre", ""))
        journal_action("live_ouvert", cible=f"live #{lid}", utilisateur=utilisateur_courant()["id"])
        journal_action2(utilisateur_courant()["id"], "live", objet="live #%s ouvert" % lid)
        return redirect(f"/live/{lid}")
    except ValueError as exc:
        session["flash"] = str(exc)
        return redirect(url_for("live_liste"))

@app.route("/live/<int:live_id>", methods=["GET"])
def live_page(live_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    live = db().execute("SELECT l.*, u.pseudo FROM lives l JOIN users u ON u.id = l.hote_id WHERE l.id = ?",
                        (live_id,)).fetchone()
    if live is None:
        return "Live inconnu.", 404
    messages = db().execute(
        "SELECT m.*, u.pseudo FROM live_messages m JOIN users u ON u.id = m.user_id"
        " WHERE m.live_id = ? ORDER BY m.id ASC LIMIT 200", (live_id,)).fetchall()
    return page("live.html", titre=f"Live : {live['titre']}", live=live, messages=messages)

@app.route("/api/live/<int:live_id>/messages", methods=["GET"])
def api_live_messages(live_id: int):
    if utilisateur_courant() is None:
        return jsonify({"erreur": "connexion requise"}), 401
    messages = db().execute(
        "SELECT m.id, m.corps, m.cree_le, u.pseudo FROM live_messages m JOIN users u ON u.id = m.user_id"
        " WHERE m.live_id = ? ORDER BY m.id DESC LIMIT 100", (live_id,)).fetchall()
    return jsonify([dict(m) for m in reversed(messages)])

@app.route("/api/live/<int:live_id>/message", methods=["POST"])
def api_live_message(live_id: int):
    me = utilisateur_courant()
    if me is None:
        return jsonify({"erreur": "connexion requise"}), 401
    try:
        message_live(live_id, me["id"], request.get_json(silent=True).get("corps", "")
                     if request.get_json(silent=True) else request.form.get("corps", ""))
    except ValueError as exc:
        return jsonify({"erreur": str(exc)}), 400
    return jsonify({"ok": True})

@app.route("/live/<int:live_id>/fermer", methods=["POST"])
def live_fermer(live_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    live = db().execute("SELECT * FROM lives WHERE id = ?", (live_id,)).fetchone()
    if live and (live["hote_id"] == me["id"] or me["est_admin"]):
        conn = db()
        conn.execute("UPDATE lives SET statut = 'ferme' WHERE id = ?", (live_id,))
        conn.commit()
        journal_action("live_ferme", cible=f"live #{live_id}", utilisateur=me["id"])
        journal_action2(me["id"], "live", objet="live #%s fermé" % live_id)
    return redirect(url_for("live_liste"))

# =============================================================================
# 🛡️ ADMIN V5 — escrow, prêts, conversions, affiliation, configuration
# =============================================================================
@app.route("/admin/v5", methods=["GET"])
def admin_v5():
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    escrows = conn.execute(
        "SELECT e.*, u.pseudo FROM escrow e JOIN users u ON u.id = e.createur_id"
        " WHERE e.statut = 'bloque' ORDER BY e.date_libere_prevue ASC").fetchall()
    prets = conn.execute(
        "SELECT p.*, pu.pseudo AS pseudo_preteur, eu.pseudo AS pseudo_emprunteur FROM prets p"
        " JOIN users pu ON pu.id = p.preteur_id JOIN users eu ON eu.id = p.emprunteur_id"
        " WHERE p.statut IN ('demande','attente_admin') ORDER BY p.id").fetchall()
    conversions = conn.execute(
        "SELECT c.*, u.pseudo FROM conversions c JOIN users u ON u.id = c.user_id"
        " WHERE c.statut = 'en_attente' ORDER BY c.id").fetchall()
    affiliations = conn.execute(
        "SELECT a.*, u.pseudo AS pseudo_parrain FROM commissions_affiliation a"
        " JOIN users u ON u.id = a.parrain_id WHERE a.statut = 'en_attente' ORDER BY a.id").fetchall()
    achats = conn.execute(
        "SELECT a.*, u.pseudo AS pseudo_acheteur, c.titre FROM achats_contenus a"
        " JOIN users u ON u.id = a.acheteur_id JOIN contenus_exclusifs c ON c.id = a.contenu_id"
        " WHERE a.statut = 'en_attente' ORDER BY a.id").fetchall()
    recurrences = conn.execute(
        "SELECT r.*, u.pseudo AS pseudo_exp, c.pseudo AS pseudo_creat FROM pourboires_recurrents r"
        " JOIN users u ON u.id = r.expediteur_id JOIN users c ON c.id = r.createur_id"
        " WHERE r.statut != 'annule' ORDER BY r.id DESC LIMIT 50").fetchall()
    return page("admin_v5.html", titre="Administration V5", escrows=escrows, prets=prets,
                conversions=conversions, affiliations=affiliations, achats=achats,
                recurrences=recurrences,
                cfg={k: config_v5(k) for k in CONFIG_V5_DEFAUT})

@app.route("/admin/v5/config", methods=["POST"])
def admin_v5_config():
    refus = exiger_admin()
    if refus:
        return refus
    for cle in CONFIG_V5_DEFAUT:
        if cle in request.form:
            config_v5_set(cle, (request.form.get(cle) or "").strip())
    journal_action("config_v5", cible="paramètres V5", utilisateur=utilisateur_courant()["id"])
    session["flash"] = "Configuration V5 enregistrée."
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/escrow/<int:escrow_id>/liberer", methods=["POST"])
def admin_v5_escrow_liberer(escrow_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    liberer_escrow(escrow_id, utilisateur_courant()["id"])
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/escrow/<int:escrow_id>/rembourser", methods=["POST"])
def admin_v5_escrow_rembourser(escrow_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    rembourser_escrow(escrow_id, utilisateur_courant()["id"])
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/pret/<int:pret_id>/valider", methods=["POST"])
def admin_v5_pret_valider(pret_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    res = valider_pret(pret_id, utilisateur_courant()["id"])
    session["flash"] = res.get("message") if not res["ok"] else "Prêt validé, fonds débloqués et échéancier créé."
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/pret/<int:pret_id>/refuser", methods=["POST"])
def admin_v5_pret_refuser(pret_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    conn.execute("UPDATE prets SET statut = 'refuse', decide_le = ? WHERE id = ? AND statut = 'attente_admin'",
                 (maintenant(), pret_id))
    conn.commit()
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/pret/<int:pret_id>/defaut", methods=["POST"])
def admin_v5_pret_defaut(pret_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    conn.execute("UPDATE prets SET statut = 'defaut', decide_le = ? WHERE id = ? AND statut = 'actif'",
                 (maintenant(), pret_id))
    conn.commit()
    journal_action("pret_defaut", cible=f"pret #{pret_id}", utilisateur=utilisateur_courant()["id"])
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/conversion/<int:conv_id>/valider", methods=["POST"])
def admin_v5_conversion_valider(conv_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    valider_conversion(conv_id, utilisateur_courant()["id"])
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/affiliation/<int:com_id>/valider", methods=["POST"])
def admin_v5_affiliation_valider(com_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    valider_commission_affiliation(com_id, utilisateur_courant()["id"])
    return redirect(url_for("admin_v5"))

@app.route("/admin/v5/achat/<int:achat_id>/valider", methods=["POST"])
def admin_v5_achat_valider(achat_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    valider_achat_contenu(achat_id, utilisateur_courant()["id"])
    return redirect(url_for("admin_v5"))

# --- Liaison parrain / filleul : intégrée dans la route d'inscription --------
# (la route /inscription existante est modifiée dans le corps du fichier pour
#  accepter le champ « parrain » et rendre le gabarit auth_v5.html)

# =============================================================================
# 🎨 GABARITS V5
# =============================================================================
TEMPLATES["auth_v5.html"] = """{% block contenu %}
<div class="carte"><h1>{{ titre }}</h1><p class="muet">Identification par numéro de téléphone ou pseudo. 100 % texte.</p>
<form method="post">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Téléphone (Mobile Money)</label><input name="telephone" placeholder="+229 01 23 45 67 89"></p>
  <p><label>Surnom — votre identifiant unique (6 lettres)</label><input name="identifiant" required></p>
  <p><label>Mot de passe</label><input name="mot_de_passe" type="password" required></p>
  <p><label>Pseudo du parrain (facultatif — programme d'affiliation)</label><input name="parrain" placeholder="6 lettres"></p>
  <p class="muet">Surnom unique : exactement 6 lettres. Mot de passe : 6 caractères minimum.</p>
  <button>{{ bouton }}</button>
</form>
<p class="muet">Déjà inscrit ? <a href="{{ url_for('connexion') }}">Se connecter</a></p>
</div>{% endblock %}"""

TEMPLATES["notifications.html"] = """{% block contenu %}
<div class="carte"><h1>🔔 Notifications</h1>
<form method="post" action="{{ url_for('notifications_lire') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Tout marquer comme lu</button>
</form></div>
{% for n in lignes %}
<div class="carte"><p><span class="badge">{{ n['type'] }}</span> {{ n['texte'] }}
{% if n['lien'] %}<a href="{{ n['lien'] }}">Ouvrir</a>{% endif %}
{% if not n['lu'] %}<span class="enligne">● nouveau</span>{% endif %}
<span class="muet">· {{ n['cree_le'] }}</span></p></div>
{% else %}<div class="carte"><p class="muet">Aucune notification.</p></div>
{% endfor %}{% endblock %}"""

TEMPLATES["stories.html"] = """{% block contenu %}
<div class="carte"><h1>📖 Stories textuelles (24 h)</h1>
<p class="muet">100 % texte. Visibles uniquement par vos abonnés (et par vous). Expiration automatique après 24 h.</p>
<form method="post" action="{{ url_for('stories_creer') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <textarea name="corps" maxlength="500" placeholder="Votre story en texte…" required></textarea>
  <p class="row"><button>Publier la story</button></p>
</form></div>
{% for s in stories %}
<div class="carte">
  <p class="meta-pub"><a href="{{ url_for('profil', pseudo=s['pseudo']) }}"><b>{{ s['pseudo'] }}</b></a>
  {% if s['auteur_id'] == me['id'] %}<span class="badge">vous</span>{% endif %}
  <span class="muet">· expire {{ s['expire_le'] }}</span></p>
  <p style="white-space:pre-wrap">{{ s['corps'] }}</p>
  <form method="post" action="{{ url_for('stories_vue', story_id=s['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn-sec">{% if s['vue'] %}Vu ✓ — revoir{% else %}Marquer comme vue{% endif %}</button>
  </form>
</div>
{% else %}<div class="carte"><p class="muet">Aucune story active. Suivez des créateurs pour voir leurs stories.</p></div>
{% endfor %}{% endblock %}"""

TEMPLATES["sondages.html"] = """{% block contenu %}
<div class="carte"><h1>🗳️ Sondages — votes en temps réel</h1>
<form method="post" action="{{ url_for('sondage_creer') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <textarea name="corps" maxlength="5000" placeholder="Texte d'accompagnement (facultatif)"></textarea>
  <p><label>Question</label><input name="question" maxlength="300" required></p>
  <p><label>Option 1</label><input name="option1" maxlength="120" required>
     <label>Option 2</label><input name="option2" maxlength="120" required></p>
  <p><label>Option 3</label><input name="option3" maxlength="120">
     <label>Option 4</label><input name="option4" maxlength="120"></p>
  <p><label>Option 5</label><input name="option5" maxlength="120">
     <label>Option 6</label><input name="option6" maxlength="120"></p>
  <p class="row"><button>Publier le sondage</button></p>
</form></div>
{% for s in sondages %}
<div class="carte">
  <p class="meta-pub"><a href="{{ url_for('profil', pseudo=s['pseudo']) }}"><b>{{ s['pseudo'] }}</b></a>
  <span class="muet">· {{ s['cree_le'] }}</span></p>
  <h3>{{ s['question'] }}</h3>
  {% if s['id'] in mes_votes or donnees[s['id']]['total'] > 0 %}
    {% for o in donnees[s['id']]['options'] %}
      <p>{{ o['libelle'] }} — <b>{{ o['pct'] }} %</b> ({{ o['votes'] }} vote{{ '' if o['votes']|int == 1 else 's' }})</p>
      <div style="background:var(--bord,#444);height:8px;border-radius:4px">
        <div style="width:{{ o['pct'] }}%;background:#c8a24a;height:8px;border-radius:4px"></div></div>
    {% endfor %}
    <p class="muet">{{ donnees[s['id']]['total'] }} vote(s) au total</p>
  {% else %}<p class="muet">Aucun vote pour l'instant — soyez le premier.</p>{% endif %}
  {% if s['id'] not in mes_votes %}
    <form method="post" action="{{ url_for('sondage_voter', sondage_id=s['id']) }}" class="row">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <select name="option" class="selec" style="width:auto">
        {% for o in donnees[s['id']]['options'] %}<option value="{{ o['id'] }}">
          {{ o['libelle'] }}</option>{% endfor %}
      </select>
      <button class="btn-sec">Voter</button>
    </form>
  {% else %}<p><span class="badge">voté</span></p>{% endif %}
  <p><a class="btn-sec btn" href="{{ url_for('thread_page', post_id=s['post_id']) }}">💬 Discussion en fil</a></p>
</div>
{% else %}<div class="carte"><p class="muet">Aucun sondage pour l'instant.</p></div>
{% endfor %}{% endblock %}"""

TEMPLATES["thread.html"] = """{% block contenu %}
<div class="carte">
  <p class="meta-pub"><a href="{{ url_for('profil', pseudo=p['pseudo']) }}"><b>{{ p['pseudo'] }}</b></a>
  <span class="muet">· {{ p['cree_le'] }}</span></p>
  <p style="white-space:pre-wrap">{{ p['corps'] }}</p>
</div>
<div class="carte"><h2>🧵 Discussion imbriquée</h2>
{{ arbre }}
<p class="muet">Répondez à n'importe quel commentaire pour créer une branche (style Reddit).</p>
</div>
{% endblock %}"""

TEMPLATES["tendances.html"] = """{% block contenu %}
<div class="carte"><h1>📈 Tendances — hashtags des 7 derniers jours</h1>
{% for t in tendances %}
  <p><a href="{{ url_for('hashtag_page', tag=t['tag']) }}"><b>#{{ t['tag'] }}</b></a> — {{ t['n'] }} publication(s)</p>
{% else %}<p class="muet">Aucun hashtag utilisé récemment. Publiez avec #votresujet !</p>
{% endfor %}</div>
{% endblock %}"""

TEMPLATES["hashtag.html"] = """{% block contenu %}
<div class="carte"><h1>{{ tag }}</h1><p class="muet">{{ posts|length }} publication(s) avec ce hashtag.</p></div>
{% for p in posts %}
<div class="carte">
  <p class="meta-pub"><a href="{{ url_for('profil', pseudo=p['pseudo']) }}"><b>{{ p['pseudo'] }}</b></a>
  <span class="muet">· {{ p['cree_le'] }}</span></p>
  <p style="white-space:pre-wrap">{{ p['corps'] }}</p>
  <p><a class="btn-sec btn" href="{{ url_for('thread_page', post_id=p['id']) }}">💬 Fil de discussion</a></p>
</div>
{% endfor %}{% endblock %}"""

TEMPLATES["boutique.html"] = """{% block contenu %}
<div class="carte"><h1>🛍️ Boutique — contenus exclusifs (textes payants à l'unité)</h1>
<form method="post" action="{{ url_for('boutique_creer') }}">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <p><label>Titre</label><input name="titre" maxlength="200" required></p>
  <p><label>Prix ({{ devise }}, entier)</label><input name="prix" type="number" min="25" step="25" value="100" required></p>
  <textarea name="corps" maxlength="20000" placeholder="Texte exclusif vendu à l'unité…" required></textarea>
  <p class="row"><button>Publier dans la boutique</button></p>
</form></div>
{% for c in items %}
<div class="carte">
  <p class="meta-pub"><a href="{{ url_for('profil', pseudo=c['pseudo']) }}"><b>{{ c['pseudo'] }}</b></a>
  <span class="badge">{{ c['prix'] }} {{ devise }}</span><span class="muet">· {{ c['cree_le'] }}</span></p>
  <h3>{{ c['titre'] }}</h3>
  {% if c['achete'] %}
    <p style="white-space:pre-wrap" class="selec">{{ c['corps'] }}</p>
    <p><span class="badge">acheté ✓</span></p>
  {% else %}
    <p class="muet">Contenu verrouillé. Aperçu : {{ c['corps'][:120] }}…</p>
    {% if c['createur_id'] != me['id'] %}
    <form method="post" action="{{ url_for('boutique_acheter', contenu_id=c['id']) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <button>Acheter — {{ c['prix'] }} {{ devise }}</button>
    </form>
    {% endif %}
  {% endif %}
</div>
{% else %}<div class="carte"><p class="muet">La boutique est vide.</p></div>
{% endfor %}{% endblock %}"""

TEMPLATES["prets.html"] = """{% block contenu %}
<div class="carte"><h1>🤝 Prêts entre membres (tickets)</h1>
<p class="muet">Le TAUX et l'ÉCHÉANCIER sont validés et générés par l'administrateur. Remboursement en tickets.</p>
<h2>Demander un prêt</h2>
<form method="post" action="{{ url_for('prets_demander') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input name="preteur" type="number" placeholder="ID du prêteur" required style="width:130px">
  <input name="capital" type="number" min="25" step="25" placeholder="Capital (tickets)" required style="width:150px">
  <input name="taux" type="number" min="0" max="100" placeholder="Taux %" value="5" required style="width:90px">
  <input name="nb" type="number" min="1" max="24" placeholder="Échéances" value="3" required style="width:110px">
  <button>Envoyer la demande</button>
</form></div>
{% for p in mes %}
<div class="carte">
  <p><span class="badge">{{ p['statut'] }}</span>
  Prêt #{{ p['id'] }} — <b>{{ p['capital'] }} {{ devise }}</b> à {{ p['taux_pct'] }} % en {{ p['nb_echeances'] }} échéance(s)</p>
  <p class="muet">Prêteur : {{ p['pseudo_preteur'] }} · Emprunteur : {{ p['pseudo_emprunteur'] }} · {{ p['cree_le'] }}</p>
  {% if p['statut'] == 'demande' and p['preteur_id'] == me['id'] %}
    <form method="post" action="{{ url_for('prets_accepter', pret_id=p['id']) }}">
      <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Accepter la demande</button>
    </form>
  {% endif %}
  {% if p['statut'] == 'actif' and p['emprunteur_id'] == me['id'] %}
    {% for e in echeances[p['id']] %}
      <p>Échéance {{ e['numero'] }} : <b>{{ e['montant'] }} {{ devise }}</b> — {{ e['statut'] }}
      {% if e['statut'] == 'en_attente' %}
        <form method="post" action="{{ url_for('prets_payer', pret_id=p['id'], numero=e['numero']) }}" style="display:inline">
          <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Rembourser</button>
        </form>
      {% endif %}</p>
    {% endfor %}
  {% endif %}
</div>
{% else %}<div class="carte"><p class="muet">Aucun prêt. L'ID d'un membre est visible par l'administrateur.</p></div>
{% endfor %}{% endblock %}"""

TEMPLATES["conversion.html"] = """{% block contenu %}
<div class="carte"><h1>💱 Conversion FCFA ↔ USDT</h1>
<p class="muet">1 ticket = {{ ticket_fcfa if ticket_fcfa is defined else 1 }} FCFA (parité fixe).
Taux USDT configuré par l'administrateur : <b>1 USDT = {{ taux }} {{ devise }}</b>.
Retrait via : {{ partenaire }}</p>
<form method="post" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input name="montant" type="number" min="500" step="500" placeholder="Montant FCFA (payés)" required style="width:180px">
  <button>Demander la conversion en USDT</button>
</form>
<p class="muet">La demande est validée par l'administrateur ; le retrait international se fait via le partenaire local.</p>
</div>
<div class="carte"><h2>Mes demandes de conversion</h2>
<table><tr><th>Date</th><th>FCFA</th><th>USDT</th><th>Taux</th><th>Référence</th><th>Statut</th></tr>
{% for c in mes %}<tr><td>{{ c['cree_le'] }}</td><td>{{ c['montant_fcfa'] }}</td>
<td>{{ c['montant_usdt'] / 100 }}</td><td>{{ c['taux'] }}</td><td>{{ c['reference'] }}</td><td>{{ c['statut'] }}</td></tr>
{% else %}<tr><td colspan="6" class="muet">Aucune demande.</td></tr>{% endfor %}</table></div>
{% endblock %}"""

TEMPLATES["live_liste.html"] = """{% block contenu %}
<div class="carte"><h1>🔴 Lives — discussion textuelle en direct</h1>
<p class="muet">100 % TEXTE : pas d'audio, pas de vidéo, pas d'image. Un live est un salon de discussion écrit.</p>
<form method="post" action="{{ url_for('live_creer') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input name="titre" maxlength="120" placeholder="Titre du live" required>
  <button>Ouvrir mon live</button>
</form></div>
{% for l in lives %}
<div class="carte">
  <p><span class="badge">{{ 'ouvert' if l['statut'] == 'ouvert' else 'fermé' }}</span>
  <a href="{{ url_for('live_page', live_id=l['id']) }}"><b>{{ l['titre'] }}</b></a>
  — hôte <a href="{{ url_for('profil', pseudo=l['pseudo']) }}">{{ l['pseudo'] }}</a>
  · {{ l['nb_messages'] }} message(s)</p>
</div>
{% else %}<div class="carte"><p class="muet">Aucun live. Ouvrez le premier !</p></div>
{% endfor %}{% endblock %}"""

TEMPLATES["live.html"] = """{% block contenu %}
<div class="carte"><h1>🔴 {{ live['titre'] }}</h1>
<p class="muet">Hôte : {{ live['pseudo'] }} — statut : {{ live['statut'] }} — discussion texte uniquement.</p>
{% if live['statut'] == 'ouvert' and (live['hote_id'] == me['id'] or me['est_admin']) %}
<form method="post" action="{{ url_for('live_fermer', live_id=live['id']) }}">
  <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Fermer le live</button>
</form>
{% endif %}
<div id="live-msgs" class="comms" style="max-height:400px;overflow:auto"></div>
{% if live['statut'] == 'ouvert' %}
<form onsubmit="envoyer_live(event)">
  <input name="corps" maxlength="500" placeholder="Votre message (texte)…" required>
  <button>Envoyer</button>
</form>
{% else %}<p class="muet">Ce live est fermé.</p>{% endif %}
</div>
<script>
var LIVE_ID = {{ live['id'] }};
var CSRF = "{{ csrf }}";
function rafraichir_live(){
  fetch('/api/live/' + LIVE_ID + '/messages').then(function(r){return r.json();}).then(function(ms){
    var zone = document.getElementById('live-msgs');
    zone.innerHTML = ms.map(function(m){return '<div class="comm"><div><b>' + m.pseudo +
      '</b><div class="corps"></div></div><span class="muet">' + m.cree_le + '</span></div>';}).join('');
    var corps = zone.querySelectorAll('.corps');
    ms.forEach(function(m,i){ corps[i].textContent = m.corps; });
    zone.scrollTop = zone.scrollHeight;
  });
}
function envoyer_live(ev){
  ev.preventDefault();
  var champ = ev.target.querySelector('input[name=corps]');
  fetch('/api/live/' + LIVE_ID + '/message', {method:'POST',
    headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},
    body: JSON.stringify({corps: champ.value})}).then(function(){ champ.value=''; rafraichir_live(); });
  return false;
}
rafraichir_live(); setInterval(rafraichir_live, 3000);
</script>
{% endblock %}"""

TEMPLATES["admin_v5.html"] = """{% block contenu %}
<div class="carte"><h1>🛡️ Administration V5</h1>
<p class="muet">Escrow, prêts, conversions, affiliation, boutique. ⚠️ Conformité : escrow, prêts d'argent et
retraits crypto peuvent relever de la réglementation BCEAO/UEMOA (monnaie électronique, KYC/AML) —
à faire valider par un professionnel avant mise en production.</p></div>

<div class="carte"><h2>⚙️ Configuration</h2>
<form method="post" action="{{ url_for('admin_v5_config') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label>Escrow (jours, 7–14)</label><input name="escrow_jours" value="{{ cfg['escrow_jours'] }}" style="width:80px">
  <label>Escrow actif</label><select name="escrow_actif" style="width:auto">
    <option value="1" {{ 'selected' if cfg['escrow_actif'] == '1' }}>Oui</option>
    <option value="0" {{ 'selected' if cfg['escrow_actif'] == '0' }}>Non</option></select>
  <label>Affiliation (%)</label><input name="taux_affiliation" value="{{ cfg['taux_affiliation'] }}" style="width:70px">
  <label>1 USDT = ({{ devise }})</label><input name="taux_usdt" value="{{ cfg['taux_usdt'] }}" style="width:90px">
  <label>Partenaire USDT</label><input name="partenaire_usdt" value="{{ cfg['partenaire_usdt'] }}" style="width:240px">
  <button class="btn-sec">Enregistrer</button>
</form></div>

<div class="carte"><h2>🔒 Escrow en cours</h2>
<table><tr><th>Créateur</th><th>Montant</th><th>Bloqué le</th><th>Libération prévue</th><th>Source</th><th>Actions</th></tr>
{% for e in escrows %}<tr><td>{{ e['pseudo'] }}</td><td><b>{{ e['montant'] }} {{ devise }}</b></td>
<td class="muet">{{ e['date_bloque'] }}</td><td>{{ e['date_libere_prevue'] }}</td><td>{{ e['source_type'] }} #{{ e['source_id'] }}</td>
<td class="row">
  <form method="post" action="{{ url_for('admin_v5_escrow_liberer', escrow_id=e['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Libérer maintenant</button></form>
  <form method="post" action="{{ url_for('admin_v5_escrow_rembourser', escrow_id=e['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Annuler (litige)</button></form>
</td></tr>
{% else %}<tr><td colspan="6" class="muet">Aucun escrow bloqué.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>🤝 Prêts à valider (taux + échéancier gérés ici)</h2>
<table><tr><th>Prêteur</th><th>Emprunteur</th><th>Capital</th><th>Taux</th><th>Échéances</th><th>Statut</th><th>Actions</th></tr>
{% for p in prets %}<tr><td>{{ p['pseudo_preteur'] }}</td><td>{{ p['pseudo_emprunteur'] }}</td>
<td>{{ p['capital'] }} {{ devise }}</td><td>{{ p['taux_pct'] }} %</td><td>{{ p['nb_echeances'] }}</td><td>{{ p['statut'] }}</td>
<td class="row">
  <form method="post" action="{{ url_for('admin_v5_pret_valider', pret_id=p['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Valider</button></form>
  <form method="post" action="{{ url_for('admin_v5_pret_refuser', pret_id=p['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Refuser</button></form>
</td></tr>
{% else %}<tr><td colspan="7" class="muet">Aucun prêt en attente.</td></tr>{% endfor %}</table>
<p class="muet">Prêts actifs en défaut : utilisez « défaut » pour clôturer un prêt impayé
(bouton disponible via /prets côté emprunteur, signalement ici).</p></div>

<div class="carte"><h2>💱 Conversions FCFA → USDT à valider</h2>
<table><tr><th>Membre</th><th>FCFA</th><th>USDT</th><th>Référence</th><th>Action</th></tr>
{% for c in conversions %}<tr><td>{{ c['pseudo'] }}</td><td>{{ c['montant_fcfa'] }}</td>
<td>{{ c['montant_usdt'] / 100 }}</td><td>{{ c['reference'] }}</td>
<td><form method="post" action="{{ url_for('admin_v5_conversion_valider', conv_id=c['id']) }}">
  <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Valider</button></form></td></tr>
{% else %}<tr><td colspan="5" class="muet">Aucune conversion en attente.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>🎯 Commissions d'affiliation à valider</h2>
<table><tr><th>Parrain</th><th>Filleul</th><th>Base</th><th>Taux</th><th>Commission</th><th>Action</th></tr>
{% for a in affiliations %}<tr><td>{{ a['pseudo_parrain'] }}</td><td>#{{ a['filleul_id'] }}</td>
<td>{{ a['montant_base'] }}</td><td>{{ a['taux_pct'] }} %</td><td><b>{{ a['montant'] }} {{ devise }}</b></td>
<td><form method="post" action="{{ url_for('admin_v5_affiliation_valider', com_id=a['id']) }}">
  <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Valider</button></form></td></tr>
{% else %}<tr><td colspan="6" class="muet">Aucune commission en attente.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>🛍️ Achats de contenu exclusif à valider</h2>
<table><tr><th>Acheteur</th><th>Contenu</th><th>Montant</th><th>Référence</th><th>Action</th></tr>
{% for a in achats %}<tr><td>{{ a['pseudo_acheteur'] }}</td><td>{{ a['titre'] }}</td>
<td>{{ a['montant'] }} {{ devise }}</td><td>{{ a['reference'] }}</td>
<td><form method="post" action="{{ url_for('admin_v5_achat_valider', achat_id=a['id']) }}">
  <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Valider</button></form></td></tr>
{% else %}<tr><td colspan="5" class="muet">Aucun achat en attente.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>🔁 Pourboires récurrents</h2>
<table><tr><th>Expéditeur</th><th>Créateur</th><th>Montant</th><th>Statut</th><th>Prochain prélèvement</th></tr>
{% for r in recurrences %}<tr><td>{{ r['pseudo_exp'] }}</td><td>{{ r['pseudo_creat'] }}</td>
<td>{{ r['montant'] }} {{ devise }}</td><td>{{ r['statut'] }}</td><td>{{ r['prochain_prelevement'] }}</td></tr>
{% else %}<tr><td colspan="5" class="muet">Aucun pourboire récurrent.</td></tr>{% endfor %}</table></div>
{% endblock %}"""

# --- Liens de navigation V5 dans la barre existante ---------------------------
_NAV_CIBLE = "<a href=\"{{ url_for('statistiques') }}\">Statistiques</a>"
_LIENS_V5 = (
    "<a href=\"{{ url_for('notifications_page') }}\">🔔{% if non_lus_notifs %}"
    "<span class=\"pastille\">{{ non_lus_notifs }}</span>{% endif %}</a>"
    "<a href=\"{{ url_for('stories_page') }}\">Stories</a>"
    "<a href=\"{{ url_for('boutique_page') }}\">Boutique</a>"
    "<a href=\"{{ url_for('sondages_page') }}\">Sondages</a>"
    "<a href=\"{{ url_for('tendances_page') }}\">Tendances</a>"
    "<a href=\"{{ url_for('live_liste') }}\">Live</a>"
    "<a href=\"{{ url_for('prets_page') }}\">Prêts</a>"
    "<a href=\"{{ url_for('recurrents_page') }}\">Récurrents</a>"
    "<a href=\"{{ url_for('conversion_page') }}\">USDT</a>"
)
if _NAV_CIBLE in TEMPLATES["base.html"]:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(_NAV_CIBLE, _NAV_CIBLE + _LIENS_V5)
if "<a href=\"{{ url_for('admin') }}\">" in TEMPLATES["base.html"]:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        "<a href=\"{{ url_for('admin') }}\"><b>Admin</b></a>",
        "<a href=\"{{ url_for('admin') }}\"><b>Admin</b></a>"
        "<a href=\"{{ url_for('admin_v5') }}\"><b>V5</b></a>")

# --- Pourboires récurrents : pause / annulation / reprise en un clic ----------
@app.route("/recurrents", methods=["GET"])
def recurrents_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    mes = db().execute(
        "SELECT r.*, c.pseudo AS pseudo_creat FROM pourboires_recurrents r"
        " JOIN users c ON c.id = r.createur_id WHERE r.expediteur_id = ? ORDER BY r.id DESC",
        (me["id"],)).fetchall()
    return page("recurrents.html", titre="Pourboires récurrents", mes=mes)

def _basculer_recurrence(rec_id: int, user_id: int, nouvel_etat: str) -> bool:
    conn = db()
    ligne = conn.execute("SELECT * FROM pourboires_recurrents WHERE id = ? AND expediteur_id = ?",
                         (rec_id, user_id)).fetchone()
    if ligne is None:
        return False
    conn.execute("UPDATE pourboires_recurrents SET statut = ? WHERE id = ?", (nouvel_etat, rec_id))
    conn.commit()
    journal_action("recurrence_maj", cible=f"récurrent #{rec_id} → {nouvel_etat}", utilisateur=user_id)
    return True

@app.route("/recurrents/<int:rec_id>/pause", methods=["POST"])
def recurrent_pause(rec_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    ok = _basculer_recurrence(rec_id, utilisateur_courant()["id"], "pause")
    session["flash"] = "Pourboire récurrent mis en pause." if ok else "Action impossible."
    journal_action2(session.get("uid"), "recurrence", objet=f"récurrent #{rec_id}",
                    details="Mise en pause du pourboire récurrent.")
    return redirect(url_for("recurrents_page"))

@app.route("/recurrents/<int:rec_id>/reprendre", methods=["POST"])
def recurrent_reprendre(rec_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    ok = _basculer_recurrence(rec_id, utilisateur_courant()["id"], "actif")
    session["flash"] = "Pourboire récurrent repris." if ok else "Action impossible."
    journal_action2(session.get("uid"), "recurrence", objet=f"récurrent #{rec_id}",
                    details="Reprise du pourboire récurrent.")
    return redirect(url_for("recurrents_page"))

@app.route("/recurrents/<int:rec_id>/annuler", methods=["POST"])
def recurrent_annuler(rec_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    ok = _basculer_recurrence(rec_id, utilisateur_courant()["id"], "annule")
    session["flash"] = "Pourboire récurrent annulé." if ok else "Action impossible."
    journal_action2(session.get("uid"), "recurrence", objet=f"récurrent #{rec_id}",
                    details="Annulation du pourboire récurrent.")
    return redirect(url_for("recurrents_page"))

@app.route("/recurrents/creer", methods=["POST"])
def recurrent_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    try:
        createur = utilisateur_par_id(int(request.form.get("createur", 0)))
        if createur is None:
            raise ValueError("Créateur introuvable.")
        creer_pourboire_recurrent(me["id"], createur["id"], request.form.get("montant", "0"),
                                  request.form.get("mot", ""))
        session["flash"] = "Pourboire récurrent mensuel activé : pause/annulation en un clic."
    except (ValueError, TypeError) as exc:
        session["flash"] = str(exc) if isinstance(exc, ValueError) else "Demande invalide."
    journal_action2(me["id"], "recurrence", objet="pourboire récurrent — créateur #%s"
                    % request.form.get("createur", "?"),
                    details="Création d'un pourboire mensuel récurrent.")
    return redirect(url_for("recurrents_page"))

TEMPLATES["recurrents.html"] = """{% block contenu %}
<div class="carte"><h1>🔁 Pourboires récurrents (mensuels)</h1>
<p class="muet">Un prélèvement est créé chaque mois et validé par l'administrateur, comme un pourboire classique.</p>
<form method="post" action="{{ url_for('recurrent_creer') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input name="createur" type="number" placeholder="ID du créateur" required style="width:140px">
  <input name="montant" type="number" min="25" step="25" value="100" required style="width:120px">
  <input name="mot" placeholder="mot du pourboire" style="width:180px">
  <button>Activer</button>
</form></div>
{% for r in mes %}
<div class="carte"><p><span class="badge">{{ r['statut'] }}</span>
  {{ r['montant'] }} {{ devise }}/mois → <a href="{{ url_for('profil', pseudo=r['pseudo_creat']) }}">{{ r['pseudo_creat'] }}</a>
  <span class="muet">· prochain prélèvement : {{ r['prochain_prelevement'] }}</span></p>
  <form method="post" action="{{ url_for('recurrent_reprendre' if r['statut'] == 'pause' else 'recurrent_pause', rec_id=r['id']) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn-sec">{{ 'Reprendre' if r['statut'] == 'pause' else '⏸ Pause' }}</button>
  </form>
  {% if r['statut'] != 'annule' %}
  <form method="post" action="{{ url_for('recurrent_annuler', rec_id=r['id']) }}" style="display:inline">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn-sec">✖ Annuler</button>
  </form>
  {% endif %}
</div>
{% else %}<div class="carte"><p class="muet">Aucun pourboire récurrent. L'ID d'un créateur est visible par l'administrateur.</p></div>
{% endfor %}{% endblock %}"""

# --- Note de conformité (à faire relire par un professionnel) -----------------
# ⚠️ CONFORMITÉ : l'escrow, les prêts d'argent entre membres et la conversion en
# USDT peuvent relever de la réglementation BCEAO/UEMOA sur la monnaie
# électronique et des obligations KYC/AML. Ces fonctionnalités sont livrées à
# titre technique ; faites valider le cadre juridique (agréments, plafonds,
# partenariats agréés) par un avocat avant toute mise en production réelle.
# =============================================================================
# FIN DES EXTENSIONS V5
# =============================================================================

# --- Garde-fou 100 % TEXTE : aucun fichier uploadé n'est jamais accepté -------
# V6 : seule exception documentée — l'IMPORT EXCEL du suivi d'abonnements
# (/admin/suivi/import), outil de comptabilité réservé à l'administrateur et
# jamais un contenu d'utilisateur. Tout le reste est refusé.
_ENDPOINTS_FICHIERS_AUTORISES = {"admin_suivi_import"}

@app.before_request
def _v5_texte_seulement():
    if request.method == "POST" and request.files:
        if request.endpoint in _ENDPOINTS_FICHIERS_AUTORISES:
            return None
        return ("Contenu non autorisé : cette application n'accepte QUE du texte "
                "(aucune image, vidéo, audio ou fichier joint).", 400)
    return None


# =============================================================================
# 🚀 V6 — 1) GARDE-FOU « 100 % TEXTE » RENFORCÉ ET 4) MODÉRATION AUTOMATIQUE
# -----------------------------------------------------------------------------
#   • Aucun point d'entrée n'accepte un fichier, une image, un son, une vidéo
#     ou un lien vers un média : formulaire, JSON et en-têtes sont inspectés.
#   • Les lives restent des salons de discussion ÉCRITS (texte seul, sans
#     WebRTC, sans audio, sans vidéo).
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
    createur_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nom         TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    palier_min  INTEGER NOT NULL DEFAULT 100,
    statut      TEXT NOT NULL DEFAULT 'ouvert',   -- ouvert | ferme
    cree_le     TEXT NOT NULL,
    UNIQUE(createur_id, nom)
);
CREATE TABLE IF NOT EXISTS groupe_membres (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    groupe_id  INTEGER NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    origine    TEXT NOT NULL DEFAULT 'manuel',
    statut     TEXT NOT NULL DEFAULT 'en_attente', -- en_attente | actif | refuse
    reference  TEXT NOT NULL DEFAULT '',
    cree_le    TEXT NOT NULL,
    valide_le  TEXT,
    UNIQUE(groupe_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_gm_user ON groupe_membres(user_id, statut);
CREATE TABLE IF NOT EXISTS groupe_messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    groupe_id INTEGER NOT NULL REFERENCES groupes(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps     TEXT NOT NULL,
    masque    INTEGER NOT NULL DEFAULT 0,
    cree_le   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gmsg ON groupe_messages(groupe_id, id);
"""


def init_schema_v6() -> None:
    conn = sqlite3.connect(CONFIG["DB"])
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_V6)
    for colonne, defaut in (("muet", "INTEGER NOT NULL DEFAULT 0"),
                            ("muet_jusqua", "TEXT NOT NULL DEFAULT ''"),
                            ("muet_motif", "TEXT NOT NULL DEFAULT ''"),
                            ("ban_temporaire_jusqua", "TEXT NOT NULL DEFAULT ''"),
                            ("ban_temporaire_motif", "TEXT NOT NULL DEFAULT ''")):
        try:
            conn.execute("ALTER TABLE users ADD COLUMN %s %s" % (colonne, defaut))
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    for cle, valeur in REGLAGES_V6_DEFAUT.items():
        conn.execute("INSERT OR IGNORE INTO moderation_reglages (cle, valeur) VALUES (?,?)", (cle, valeur))
    for terme, action, duree in MOTS_EXEMPLES_V6:
        conn.execute("INSERT OR IGNORE INTO moderation_mots (terme, action, duree_min, actif, auteur_id,"
                     " cree_le) VALUES (?,?,?,?,?,?)", (terme, action, duree, 1, None, maintenant()))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------
# Utilitaires partagés V6
# ----------------------------------------------------------------------------
def _valeur_ligne(ligne, cle: str, defaut: str = ""):
    try:
        valeur = ligne[cle]
    except Exception:
        return defaut
    return defaut if valeur is None else valeur


def pseudo_de_v6(user_id) -> str:
    try:
        ligne = db().execute("SELECT pseudo FROM users WHERE id = ?", (user_id,)).fetchone()
        return ligne["pseudo"] if ligne else ("#%s" % user_id)
    except Exception:
        return "#%s" % user_id


def reglage_v6(cle: str, defaut: str = "") -> str:
    try:
        ligne = db().execute("SELECT valeur FROM moderation_reglages WHERE cle = ?", (cle,)).fetchone()
    except Exception:
        ligne = None
    if ligne is None:
        return REGLAGES_V6_DEFAUT.get(cle, defaut)
    return ligne["valeur"]


def maj_reglage_v6(cle: str, valeur: str) -> None:
    conn = db()
    conn.execute("INSERT INTO moderation_reglages (cle, valeur) VALUES (?,?)"
                 " ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur", (cle, str(valeur)))
    conn.commit()


# ----------------------------------------------------------------------------
# 5) GARDE-FOU 100 % TEXTE (formulaires, JSON, fichiers, liens média)
# ----------------------------------------------------------------------------
_MOTIFS_MEDIA_V6 = (
    re.compile(r"data:(?:image|video|audio|application)/", re.IGNORECASE),
    re.compile(r"<\s*(?:img|video|audio|source|iframe|embed|object|svg|picture)\b", re.IGNORECASE),
    re.compile(r"https?://\S+?\.(?:jpe?g|png|gif|webp|bmp|tiff?|svg|ico|heic|mp4|m4v|mov|avi|mkv|webm|"
               r"mp3|wav|ogg|oga|flac|m4a|aac|pdf|docx?|xlsx?|pptx?|zip|rar|7z|apk)(?:\?|\s|$|#)",
               re.IGNORECASE),
    re.compile(r"\b(?:javascript|vbscript)\s*:", re.IGNORECASE),
)
_CHAMPS_MEDIA_JSON_V6 = ("audio", "video", "image", "images", "fichier", "fichiers", "file", "files",
                         "media", "attachment", "piece_jointe", "url_image")


def contient_media(texte: str) -> Optional[str]:
    """Retourne le fragment fautif si le texte porte un média, sinon None."""
    if not texte or not isinstance(texte, str):
        return None
    for motif in _MOTIFS_MEDIA_V6:
        trouve = motif.search(texte)
        if trouve:
            return trouve.group(0)[:120]
    return None


def _aplatir_v6(charge, sortie: List[str]) -> None:
    if isinstance(charge, str):
        sortie.append(charge)
    elif isinstance(charge, dict):
        for valeur in charge.values():
            _aplatir_v6(valeur, sortie)
    elif isinstance(charge, (list, tuple)):
        for valeur in charge:
            _aplatir_v6(valeur, sortie)


@app.before_request
def _v6_texte_seulement():
    """Tout ce qui entre dans l'application est du TEXTE, et rien d'autre.

    Refuses : fichiers joints, champs média dans un corps JSON, balises média,
    URL de média, schémas javascript:/vbscript:, data-URI binaires.
    """
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    if request.files and request.endpoint != "admin_suivi_import":
        return ("Contenu non autorisé : cette application n'accepte QUE du texte "
                "(aucune image, vidéo, audio ou fichier joint).", 400)
    valeurs: List[str] = [v for v in request.form.values() if isinstance(v, str)]
    if request.is_json:
        charge = request.get_json(silent=True)
        if isinstance(charge, dict) and any(cle in charge for cle in _CHAMPS_MEDIA_JSON_V6):
            return ("Contenu non autorisé : seuls les champs texte (corps, sujet…) sont "
                    "acceptés — aucun média, aucun fichier.", 400)
        _aplatir_v6(charge, valeurs)
    for valeur in valeurs:
        faute = contient_media(valeur)
        if faute:
            return ("Contenu non autorisé : les images, vidéos, sons et fichiers joints sont "
                    "interdits, y compris par lien (détecté : « %s »). ToutBot Mundo est "
                    "100 %% textuel." % faute, 400)
    return None


# ----------------------------------------------------------------------------
# 4) MODÉRATION AUTOMATIQUE : mots interdits → masquage ou ban temporaire
# ----------------------------------------------------------------------------
_CACHE_MOTS = {"mots": [], "expire": 0.0}


def _motif_terme(terme: str):
    return re.compile(r"(?<![0-9A-Za-z\u00C0-\u024F])" + re.escape(terme) +
                      r"(?![0-9A-Za-z\u00C0-\u024F])", re.IGNORECASE)


def mots_interdits(force: bool = False) -> List[Dict[str, Any]]:
    horodatage = time.time()
    if force or horodatage > _CACHE_MOTS["expire"]:
        try:
            lignes = db().execute("SELECT * FROM moderation_mots WHERE actif = 1 ORDER BY id").fetchall()
            _CACHE_MOTS["mots"] = [dict(l) for l in lignes]
        except Exception:
            _CACHE_MOTS["mots"] = []
        _CACHE_MOTS["expire"] = horodatage + 10.0
    return _CACHE_MOTS["mots"]


def journaliser_moderation(user_id, terme: str, action: str, contexte: str,
                           avant: str, apres: str, expire_le: str = "") -> None:
    try:
        conn = db()
        conn.execute("INSERT INTO moderation_journal (user_id, contexte, terme, action, texte_origine,"
                     " texte_final, expire_le, cree_le) VALUES (?,?,?,?,?,?,?,?)",
                     (user_id, (contexte or "")[:80], (terme or "")[:80], (action or "")[:40],
                      (avant or "")[:2000], (apres or "")[:2000], expire_le or "", maintenant()))
        conn.commit()
    except Exception:
        LOGGER.warning("journal de modération indisponible", exc_info=False)


def sanction_en_cours(u) -> Dict[str, Any]:
    """Décrit l'état de sanction d'un utilisateur (lecture seule / ban temporaire)."""
    if u is None:
        return {"actif": False, "type": "", "jusqua": "", "motif": ""}
    try:
        muet = int(_valeur_ligne(u, "muet", 0) or 0)
    except Exception:
        muet = 0
    jusqua = str(_valeur_ligne(u, "muet_jusqua", "") or "")
    motif = str(_valeur_ligne(u, "muet_motif", "") or "")
    if not muet:
        return {"actif": False, "type": "", "jusqua": "", "motif": ""}
    if jusqua and jusqua <= maintenant():
        return {"actif": False, "type": "expire", "jusqua": jusqua, "motif": motif}
    return {"actif": True, "type": "lecture_seule", "jusqua": jusqua, "motif": motif}


def appliquer_sanction_temporaire(user_id: int, motif: str, expire_le: str, minutes: int) -> None:
    conn = db()
    libelle = "modération automatique : " + (motif or "")[:80]
    conn.execute("UPDATE users SET muet = 1, muet_jusqua = ?, muet_motif = ?,"
                 " ban_temporaire_jusqua = ?, ban_temporaire_motif = ? WHERE id = ?",
                 (expire_le, libelle, expire_le, libelle, user_id))
    conn.commit()
    journal_action("sanction_temporaire", cible="utilisateur #%d" % user_id,
                   details="%s — %d minute(s)" % ((motif or "")[:60], minutes), utilisateur=None)
    evenement_v6("sanction", "Lecture seule temporaire (%d min) : %s — mot « %s »"
                 % (minutes, pseudo_de_v6(user_id), motif),
                 lien="/admin/gouvernance")


_DERNIER_BALAYAGE = [0.0]


def lever_sanctions_expirees() -> None:
    """Lève automatiquement les sanctions temporaires arrivées à échéance."""
    horodatage_ts = time.time()
    if horodatage_ts - _DERNIER_BALAYAGE[0] < 3.0:
        return
    _DERNIER_BALAYAGE[0] = horodatage_ts
    conn = db()
    marque = maintenant()
    lignes = conn.execute("SELECT id, pseudo FROM users WHERE muet = 1 AND muet_jusqua <> ''"
                          " AND muet_jusqua <= ?", (marque,)).fetchall()
    if not lignes:
        return
    conn.execute("UPDATE users SET muet = 0, muet_jusqua = '', muet_motif = '',"
                 " ban_temporaire_jusqua = '', ban_temporaire_motif = ''"
                 " WHERE muet = 1 AND muet_jusqua <> '' AND muet_jusqua <= ?", (marque,))
    conn.commit()
    for ligne in lignes:
        journal_action("sanction_levee", cible="utilisateur #%d" % ligne["id"],
                       details="fin de la lecture seule temporaire")
        evenement_v6("sanction_levee", "Fin de sanction : %s peut de nouveau publier." % ligne["pseudo"],
                     lien="/admin/gouvernance")


def appliquer_moderation(texte: str, user_id: Optional[int], contexte: str = "") -> Dict[str, Any]:
    """Applique les règles automatiques à un texte AVANT son enregistrement.

    Retourne {'texte': texte_final, 'masque': bool, 'termes': [...],
              'expire_le': str, 'banniere': str}.
    """
    resultat = {"texte": texte, "masque": False, "termes": [], "expire_le": "", "banniere": ""}
    if not texte or not isinstance(texte, str):
        return resultat
    if reglage_v6("automod_actif", "0") != "1":
        return resultat
    regles = mots_interdits()
    if not regles:
        return resultat
    caracteres_masque = reglage_v6("masque", "▮") or "▮"
    final = texte
    for regle in regles:
        terme = (regle.get("terme") or "").strip()
        if not terme:
            continue
        motif = _motif_terme(terme)
        if not motif.search(final):
            continue
        final = motif.sub(lambda m, _c=caracteres_masque: _c * len(m.group(0)), final)
        resultat["masque"] = True
        if terme not in resultat["termes"]:
            resultat["termes"].append(terme)
        action = (regle.get("action") or "masquer").strip()
        if action == "ban_temporaire" and user_id:
            try:
                minutes = int(regle.get("duree_min") or 0)
            except Exception:
                minutes = 0
            if minutes <= 0:
                try:
                    minutes = int(reglage_v6("ban_duree_min", "60") or 60)
                except Exception:
                    minutes = 60
            expire_le = (_dt.datetime.now(_dt.timezone.utc)
                         + _dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
            resultat["expire_le"] = expire_le
            appliquer_sanction_temporaire(user_id, terme, expire_le, minutes)
        journaliser_moderation(user_id, terme, action, contexte, texte, final, resultat["expire_le"])
    resultat["texte"] = final
    if resultat["masque"]:
        resultat["banniere"] = ("Modération automatique : mot(s) interdit(s) masqué(s) (%s)."
                               % ", ".join(resultat["termes"][:3]))
    if resultat["expire_le"]:
        resultat["banniere"] += (" Lecture seule temporaire jusqu'au %s (UTC) : les publications, "
                                 "commentaires et messages sont suspendus jusqu'à cette échéance."
                                 % resultat["expire_le"])
    return resultat


# ----------------------------------------------------------------------------
# Application de la modération aux points d'entrée texte existants (V4/V5)
# ----------------------------------------------------------------------------
_ORIG_AJOUTER_COMMENTAIRE = ajouter_commentaire
_ORIG_ENVOYER_MESSAGE = envoyer_message
_ORIG_MESSAGE_LIVE = message_live


def _param_v6(args, kwargs, position: int, nom: str, defaut=None):
    if len(args) > position:
        return args[position]
    return kwargs.get(nom, defaut)


def ajouter_commentaire(*args, **kwargs):  # noqa: F811
    """Commentaire : modéré (masquage / ban temporaire) avant enregistrement."""
    corps = _param_v6(args, kwargs, 2, "corps", "")
    auteur = _param_v6(args, kwargs, 1, "auteur_id", None)
    moderation = appliquer_moderation((corps or "").strip(), auteur,
                                     "commentaire #%s" % _param_v6(args, kwargs, 0, "post_id", "?"))
    if len(args) > 2:
        args = (args[0], args[1], moderation["texte"]) + tuple(args[3:])
    else:
        kwargs["corps"] = moderation["texte"]
    if moderation["banniere"] and "session" in globals():
        session["flash"] = moderation["banniere"]
    return _ORIG_AJOUTER_COMMENTAIRE(*args, **kwargs)


def envoyer_message(*args, **kwargs):  # noqa: F811
    """Message privé : modéré avant enregistrement."""
    corps = _param_v6(args, kwargs, 2, "corps", "")
    expediteur = _param_v6(args, kwargs, 0, "expediteur_id", None)
    moderation = appliquer_moderation((corps or "").strip(), expediteur, "message privé")
    if len(args) > 2:
        args = (args[0], args[1], moderation["texte"]) + tuple(args[3:])
    else:
        kwargs["corps"] = moderation["texte"]
    return _ORIG_ENVOYER_MESSAGE(*args, **kwargs)


def message_live(*args, **kwargs):  # noqa: F811
    """Live : salon ÉCRIT uniquement — la modération s'applique au texte."""
    corps = _param_v6(args, kwargs, 2, "corps", "")
    auteur = _param_v6(args, kwargs, 1, "user_id", None)
    live_id = _param_v6(args, kwargs, 0, "live_id", "?")
    moderation = appliquer_moderation((corps or "").strip()[:500], auteur, "live #%s" % live_id)
    if len(args) > 2:
        args = (args[0], args[1], moderation["texte"]) + tuple(args[3:])
    else:
        kwargs["corps"] = moderation["texte"]
    return _ORIG_MESSAGE_LIVE(*args, **kwargs)


init_schema_v6()


# =============================================================================
# 🚀 V6 — 1) MODE « LECTURE SEULE » / MUTE (distinct du blocage)
#          2) GROUPES PRIVÉS PAYANTS (créateur + abonnés d'un palier minimum)
# -----------------------------------------------------------------------------
# Le mute n'est PAS un blocage : le membre reste connecté, lit tout, écrit à
# l'administrateur et dépose des plaintes — mais ne peut plus rien publier.
# =============================================================================
DUREES_MUET_V6 = {"60": "1 heure", "1440": "24 heures", "10080": "7 jours", "permanent": "jusqu'à nouvel ordre"}


def appliquer_muet(user_id: int, minutes: int, motif: str, decideur_id: Optional[int]) -> str:
    """Passe un membre en lecture seule. Retourne l'échéance ('' = permanente)."""
    if minutes and minutes > 0:
        jusqua = (_dt.datetime.now(_dt.timezone.utc)
                  + _dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        libelle = "%d min" % minutes
    else:
        jusqua, libelle = "", "jusqu'à nouvel ordre"
    conn = db()
    conn.execute("UPDATE users SET muet = 1, muet_jusqua = ?, muet_motif = ? WHERE id = ?",
                 (jusqua, (motif or "modération manuelle")[:160], user_id))
    conn.commit()
    journal_action("mise_lecture_seule", cible="utilisateur #%d" % user_id,
                   details="durée %s — %s" % (libelle, (motif or "sans motif")[:120]),
                   utilisateur=decideur_id)
    evenement_v6("muet", "Lecture seule (%s) : %s — %s"
                 % (libelle, pseudo_de_v6(user_id), (motif or "sans motif")[:120]),
                 lien="/admin/gouvernance")
    return jusqua


def lever_muet(user_id: int, decideur_id: Optional[int]) -> bool:
    conn = db()
    ligne = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if ligne is None:
        return False
    conn.execute("UPDATE users SET muet = 0, muet_jusqua = '', muet_motif = '',"
                 " ban_temporaire_jusqua = '', ban_temporaire_motif = '' WHERE id = ?", (user_id,))
    conn.commit()
    journal_action("fin_lecture_seule", cible="utilisateur #%d" % user_id, utilisateur=decideur_id)
    evenement_v6("muet_leve", "Fin de lecture seule : %s peut de nouveau publier."
                 % pseudo_de_v6(user_id), lien="/admin/gouvernance")
    return True


def _ecriture_autorisee_malgre_sanction() -> bool:
    """Un membre en lecture seule garde : l'accès aux pages, les plaintes, ses
    paramètres et la discussion avec l'administrateur (voie de recours)."""
    endpoint = request.endpoint or ""
    if endpoint.startswith("admin"):
        return True
    if endpoint in {"connexion", "inscription", "deconnexion", "plaintes", "plainte_creer",
                    "parametres", "parametres_profil", "parametres_mdp", "parametres_prefs",
                    "parametres_supprimer", "cgu", "confidentialite", "tarifs",
                    "recherche_page", "api_recherche", "api_calcul", "api_sante", "api_ping",
                    "api_horloge", "api_non_lus", "api_live_messages", "api_groupe_messages"}:
        return True
    if endpoint == "messages_envoyer":
        admin = admin_par_defaut()
        cible = (request.view_args or {}).get("autre")
        try:
            return admin is not None and cible is not None and int(cible) == int(admin["id"])
        except (TypeError, ValueError):
            return False
    return False


@app.before_request
def _v6_sanctions():
    """Filet de sécurité : toute écriture est refusée à un membre en lecture seule."""
    try:
        lever_sanctions_expirees()
    except Exception:
        LOGGER.warning("balayage des sanctions temporaires impossible", exc_info=False)
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    me = utilisateur_courant()
    if me is None:
        return None
    etat = sanction_en_cours(me)
    if not etat["actif"]:
        return None
    if _ecriture_autorisee_malgre_sanction():
        return None
    message = ("Mode lecture seule : vous ne pouvez plus publier, commenter, aimer, envoyer de "
               "messages ni ouvrir de live%s. Motif : %s."
               % ((" — fin prévue le %s (UTC)" % etat["jusqua"]) if etat["jusqua"] else "",
                  etat["motif"] or "sanction des CGU"))
    if request.path.startswith("/api/") or request.is_json:
        return jsonify(ok=False, erreur="lecture_seule", message=message, jusqua=etat["jusqua"]), 403
    session["flash"] = message
    return redirect(request.referrer or url_for("fil"))


@app.context_processor
def _contexte_v6():
    """Bandeau de sanction visible sur TOUTES les pages + état du WebSocket."""
    me = utilisateur_courant()
    banniere, est_muet, jusqua, motif = "", False, "", ""
    if me is not None:
        etat = sanction_en_cours(me)
        est_muet = bool(etat["actif"])
        jusqua = str(etat["jusqua"] or _valeur_ligne(me, "muet_jusqua", "") or "")
        motif = str(etat["motif"] or "")
        if est_muet:
            banniere = ("🚫 Mode lecture seule actif : publication, commentaires, « J'aime », "
                        "messages et lives sont suspendus%s. Motif : %s. Les plaintes et la "
                        "discussion avec l'administrateur restent ouvertes."
                        % ((" — fin prévue le %s (UTC)" % jusqua) if jusqua else " jusqu'à nouvel ordre",
                           motif or "sanction des CGU"))
    return {"sanction_banniere": banniere, "est_muet": est_muet, "muet_jusqua": jusqua,
            "muet_motif": motif, "ws_disponible": _WS_DISPONIBLE}


# ----------------------------------------------------------------------------
# 1) Routes d'administration du mode lecture seule
# ----------------------------------------------------------------------------
@app.route("/admin/muet/<int:uid>", methods=["POST"])
def admin_muet(uid: int):
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    if uid == me["id"]:
        session["flash"] = "Vous ne pouvez pas vous passer vous-même en lecture seule."
        return redirect(url_for("admin_gouvernance"))
    cible = utilisateur_par_id(uid)
    if cible is None:
        return "Utilisateur inconnu.", 404
    duree = (request.form.get("duree") or "1440").strip()
    motif = (request.form.get("motif") or "").strip()
    minutes = int(duree) if duree.isdigit() else 0
    jusqua = appliquer_muet(uid, minutes, motif, me["id"])
    session["flash"] = ("« %s » est en lecture seule %s%s. Le compte n'est PAS bloqué : il peut "
                        "toujours lire et saisir l'administrateur."
                        % (cible["pseudo"], DUREES_MUET_V6.get(duree, duree),
                           (" (jusqu'au %s UTC)" % jusqua) if jusqua else ""))
    return redirect(request.referrer or url_for("admin_gouvernance"))


@app.route("/admin/demuet/<int:uid>", methods=["POST"])
def admin_demuet(uid: int):
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    ok = lever_muet(uid, me["id"])
    session["flash"] = ("Lecture seule levée pour « %s »." % pseudo_de_v6(uid)) if ok else "Utilisateur inconnu."
    return redirect(request.referrer or url_for("admin_gouvernance"))


# ----------------------------------------------------------------------------
# 2) Groupes privés payants — texte uniquement
# ----------------------------------------------------------------------------
TEMPLATES["groupes.html"] = """{% block contenu %}
<div class="carte"><h1>👥 Groupes privés payants</h1>
<p class="muet">Un groupe est réservé à son créateur et aux membres qui paient au moins le palier
exigé au créateur. Discussion <b>100 % texte</b> : aucun audio, aucune image, aucune vidéo.</p>
<form method="post" action="{{ url_for('groupe_creer') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input name="nom" maxlength="60" placeholder="Nom du groupe" required style="width:200px">
  <input name="palier_min" type="number" value="100" required style="width:120px">
  <input name="description" maxlength="400" placeholder="Description (texte)" style="width:260px">
  <button>Créer le groupe</button>
</form>
<p class="muet">Paliers autorisés : {{ paliers|join(' · ') }} {{ devise }}. Un abonné dont le palier
validé atteint <b>palier_min</b> entre sans validation supplémentaire.</p></div>

<div class="carte"><h2>Groupes</h2>
<table><tr><th>Groupe</th><th>Créateur</th><th>Palier exigé</th><th>Membres</th><th>Mon accès</th><th></th></tr>
{% for g in groupes %}
<tr><td><a href="{{ url_for('groupe_page', gid=g['id']) }}"><b>{{ g['nom'] }}</b></a>
  <div class="muet">{{ g['description'] }}</div></td>
<td>{{ g['pseudo'] }}</td><td>{{ g['palier_min'] }} {{ devise }}</td><td>{{ g['membres'] }}</td>
<td>{% if g['autorise'] %}<span class="badge">{{ g['raison'] }}</span>
  {% elif g['demande'] %}<span class="badge">demande en attente</span>
  {% else %}<span class="muet">{{ g['raison'] }}</span>{% endif %}</td>
<td><a class="btn btn-sec" href="{{ url_for('groupe_page', gid=g['id']) }}">Ouvrir</a></td></tr>
{% else %}<tr><td colspan="6" class="muet">Aucun groupe pour l'instant — créez le premier.</td></tr>
{% endfor %}</table></div>
{% endblock %}"""

TEMPLATES["groupe.html"] = """{% block contenu %}
<div class="carte"><h1>👥 {{ groupe['nom'] }}</h1>
<p class="muet">Créateur : <a href="{{ url_for('profil', pseudo=groupe['pseudo']) }}">{{ groupe['pseudo'] }}</a>
· palier exigé : <b>{{ groupe['palier_min'] }} {{ devise }}</b> ·
{{ 'ouvert' if groupe['statut'] == 'ouvert' else 'fermé' }} · {{ membres }} membre(s)</p>
<p class="muet">{{ groupe['description'] }}</p>
{% if not autorise %}
  <p class="annonce">Accès non ouvert : {{ raison }}. Payez sur le numéro Mobile Money de
  l'administrateur ci-dessous, puis demandez l'accès.</p>
  {% if not demande %}
  <form method="post" action="{{ url_for('groupe_demander', gid=groupe['id'] }) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button>Demander l'accès</button></form>
  {% else %}<p class="muet">Demande d'accès enregistrée : l'administrateur la validera.</p>{% endif %}
{% else %}
  <p class="muet">Accès accordé ({{ raison }}).</p>
  {% if groupe['createur_id'] == me['id'] or me['est_admin'] %}
  {% if groupe['statut'] == 'ouvert' %}
  <form method="post" action="{{ url_for('groupe_fermer', gid=groupe['id'] }) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Fermer le groupe</button></form>
  {% else %}<p class="muet">Ce groupe est fermé : plus aucun message n'est accepté.</p>{% endif %}
  {% endif %}
  {% if autorise and not est_muet and groupe['statut'] == 'ouvert' %}
  <form method="post" action="{{ url_for('groupe_message', gid=groupe['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="corps" maxlength="2000" placeholder="Votre message (texte)…" required>
    <button>Envoyer</button></form>
  {% elif est_muet %}<p class="muet">Mode lecture seule : vous ne pouvez pas écrire dans ce groupe.</p>{% endif %}
{% endif %}
</div>
{% if autorise %}
{% include 'numeros.html' %}
<div class="carte" id="bas"><h2>Discussion (texte uniquement)</h2>
<div id="groupe-msgs" class="comms" style="max-height:420px;overflow:auto">
{% for m in messages %}<div class="comm"><div><b>{{ m['pseudo'] }}</b>{% if m['masque'] %}
<span class="badge">modéré</span>{% endif %}<div class="corps">{{ m['corps'] }}</div></div>
<span class="muet">{{ m['cree_le'] }}</span></div>{% endfor %}</div>
<p class="muet">Rafraîchissement automatique toutes les 5 secondes.</p></div>
<script>
{% raw %}
var GID = {% endraw %}{{ groupe['id'] }}{% raw %};
function rafraichir_groupe(){
  fetch('/api/groupe/' + GID + '/messages').then(function(r){return r.json();}).then(function(ms){
    if(!ms || !ms.length) return;
    var zone = document.getElementById('groupe-msgs');
    zone.innerHTML = '';
    for (var i = 0; i < ms.length; i++){
      var ligne = document.createElement('div'); ligne.className = 'comm';
      var tete = document.createElement('div');
      var auteur = document.createElement('b'); auteur.textContent = ms[i].pseudo;
      tete.appendChild(auteur);
      if (ms[i].masque){ var b = document.createElement('span'); b.className = 'badge'; b.textContent = 'modéré'; tete.appendChild(b); }
      var corps = document.createElement('div'); corps.className = 'corps'; corps.textContent = ms[i].corps;
      tete.appendChild(corps); ligne.appendChild(tete);
      var heure = document.createElement('span'); heure.className = 'muet'; heure.textContent = ms[i].cree_le;
      ligne.appendChild(heure); zone.appendChild(ligne);
    }
    zone.scrollTop = zone.scrollHeight;
  });
}
setInterval(rafraichir_groupe, 5000);
{% endraw %}
</script>
{% endif %}
{% endblock %}"""


@app.route("/groupes", methods=["GET"])
def groupes_page():
    try:
        if utilisateur_courant() is not None:
            journal_action2(utilisateur_courant()["id"], "navigation",
                            objet="page Groupes", details="Consultation de la liste des groupes.")
    except Exception:
        pass
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    lignes = conn.execute(
        "SELECT g.*, u.pseudo,"
        " (SELECT COUNT(*) FROM groupe_membres m WHERE m.groupe_id = g.id AND m.statut = 'actif') AS membres"
        " FROM groupes g JOIN users u ON u.id = g.createur_id ORDER BY g.id DESC LIMIT 200").fetchall()
    groupes = []
    for ligne in lignes:
        autorise, raison = acces_groupe_v6(ligne, me["id"])
        demande = conn.execute("SELECT statut FROM groupe_membres WHERE groupe_id = ? AND user_id = ?",
                               (ligne["id"], me["id"])).fetchone()
        groupes.append({"id": ligne["id"], "nom": ligne["nom"], "description": ligne["description"],
                        "palier_min": ligne["palier_min"], "pseudo": ligne["pseudo"],
                        "membres": ligne["membres"], "autorise": autorise, "raison": raison,
                        "demande": bool(demande and demande["statut"] == "en_attente")})
    return page("groupes.html", titre="Groupes privés payants", groupes=groupes)


def _groupe_ou_404(gid: int):
    return db().execute("SELECT g.*, u.pseudo FROM groupes g JOIN users u ON u.id = g.createur_id"
                        " WHERE g.id = ?", (gid,)).fetchone()


@app.route("/groupes/creer", methods=["POST"])
def groupe_creer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    nom = (request.form.get("nom") or "").strip()[:60]
    description = (request.form.get("description") or "").strip()[:400]
    try:
        palier = int(float(request.form.get("palier_min", "0") or 0))
    except (TypeError, ValueError):
        palier = 0
    if len(nom) < 3:
        session["flash"] = "Le nom du groupe doit contenir au moins 3 caractères."
        return redirect(url_for("groupes_page"))
    if palier not in PALIERS:
        session["flash"] = ("Palier invalide : choisissez l'un des 6 paliers %s %s."
                            % (" / ".join(str(p) for p in PALIERS), CONFIG["DEVISE"]))
        return redirect(url_for("groupes_page"))
    media = contient_media(nom) or contient_media(description)
    if media:
        session["flash"] = "Les médias sont interdits dans un groupe (détecté : « %s »)." % media
        return redirect(url_for("groupes_page"))
    moderation = appliquer_moderation(nom, me["id"], "nom de groupe")
    conn = db()
    try:
        curseur = conn.execute("INSERT INTO groupes (createur_id, nom, description, palier_min, statut,"
                               " cree_le) VALUES (?,?,?,?,?,?)",
                               (me["id"], moderation["texte"], description, palier, "ouvert", maintenant()))
        conn.commit()
    except sqlite3.IntegrityError:
        session["flash"] = "Vous avez déjà un groupe portant ce nom."
        return redirect(url_for("groupes_page"))
    gid = curseur.lastrowid
    journal_action("groupe_cree", cible="groupe #%d" % gid, details=nom, utilisateur=me["id"])
    evenement_v6("groupe", "Nouveau groupe privé : « %s » de %s (palier %d FCFA)"
                 % (nom, me["pseudo"], palier), lien="/groupes/%d" % gid)
    session["flash"] = "Groupe créé : les abonnés au palier %d FCFA et plus y ont accès." % palier
    return redirect(url_for("groupe_page", gid=gid))


@app.route("/groupes/<int:gid>", methods=["GET"])
def groupe_page(gid: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    groupe = _groupe_ou_404(gid)
    if groupe is None:
        return "Groupe inconnu.", 404
    autorise, raison = acces_groupe_v6(groupe, me["id"])
    demande = db().execute("SELECT statut FROM groupe_membres WHERE groupe_id = ? AND user_id = ?",
                           (gid, me["id"])).fetchone()
    messages = []
    if autorise:
        messages = db().execute(
            "SELECT m.*, u.pseudo FROM groupe_messages m JOIN users u ON u.id = m.user_id"
            " WHERE m.groupe_id = ? ORDER BY m.id ASC LIMIT 300", (gid,)).fetchall()
    membres = db().execute("SELECT COUNT(*) AS n FROM groupe_membres WHERE groupe_id = ? AND statut = 'actif'",
                           (gid,)).fetchone()["n"]
    return page("groupe.html", titre="Groupe : %s" % groupe["nom"], groupe=groupe, autorise=autorise,
                raison=raison, demande=bool(demande and demande["statut"] == "en_attente"),
                messages=messages, membres=membres)


@app.route("/groupes/<int:gid>/message", methods=["POST"])
def groupe_message(gid: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    groupe = _groupe_ou_404(gid)
    if groupe is None:
        return "Groupe inconnu.", 404
    autorise, raison = acces_groupe_v6(groupe, me["id"])
    if not autorise:
        session["flash"] = "Publication refusée : accès non ouvert (%s)." % raison
        return redirect(url_for("groupe_page", gid=gid))
    if groupe["statut"] != "ouvert":
        session["flash"] = "Ce groupe est fermé : aucun message n'est accepté."
        return redirect(url_for("groupe_page", gid=gid))
    corps = (request.form.get("corps") or "").strip()
    if corps:
        moderation = appliquer_moderation(corps[:2000], me["id"], "groupe #%d" % gid)
        conn = db()
        conn.execute("INSERT INTO groupe_messages (groupe_id, user_id, corps, masque, cree_le)"
                     " VALUES (?,?,?,?,?)",
                     (gid, me["id"], moderation["texte"], 1 if moderation["masque"] else 0, maintenant()))
        conn.commit()
        journal_action("groupe_message", cible="groupe #%d" % gid, details=corps[:80], utilisateur=me["id"])
        evenement_v6("groupe", "Message de %s dans « %s »" % (me["pseudo"], groupe["nom"]),
                     lien="/groupes/%d" % gid)
        if moderation["banniere"]:
            session["flash"] = moderation["banniere"]
    return redirect(url_for("groupe_page", gid=gid) + "#bas")


@app.route("/api/groupe/<int:gid>/messages", methods=["GET"])
def api_groupe_messages(gid: int):
    me = utilisateur_courant()
    if me is None:
        return jsonify({"erreur": "connexion requise"}), 401
    groupe = db().execute("SELECT * FROM groupes WHERE id = ?", (gid,)).fetchone()
    if groupe is None:
        return jsonify({"erreur": "groupe inconnu"}), 404
    autorise, _ = acces_groupe_v6(groupe, me["id"])
    if not autorise:
        return jsonify({"erreur": "accès non ouvert"}), 403
    lignes = db().execute("SELECT m.id, m.corps, m.cree_le, m.masque, u.pseudo FROM groupe_messages m"
                          " JOIN users u ON u.id = m.user_id WHERE m.groupe_id = ?"
                          " ORDER BY m.id DESC LIMIT 100", (gid,)).fetchall()
    return jsonify([dict(l) for l in reversed(lignes)])


@app.route("/groupes/<int:gid>/demander", methods=["POST"])
def groupe_demander(gid: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    groupe = _groupe_ou_404(gid)
    if groupe is None:
        return "Groupe inconnu.", 404
    autorise, raison = acces_groupe_v6(groupe, me["id"])
    if autorise:
        session["flash"] = "Accès déjà ouvert (%s)." % raison
        return redirect(url_for("groupe_page", gid=gid))
    conn = db()
    conn.execute("INSERT INTO groupe_membres (groupe_id, user_id, origine, statut, reference, cree_le)"
                 " VALUES (?,?,?,?,?,?) ON CONFLICT(groupe_id, user_id) DO UPDATE SET statut = 'en_attente',"
                 " cree_le = excluded.cree_le",
                 (gid, me["id"], "manuel", "en_attente",
                  "GRP-%d-%d-%s" % (gid, me["id"], secrets.token_hex(3).upper()), maintenant()))
    conn.commit()
    journal_action("groupe_demande", cible="groupe #%d" % gid, utilisateur=me["id"])
    evenement_v6("groupe_acces", "Demande d'accès de %s au groupe « %s » (palier %d FCFA)"
                 % (me["pseudo"], groupe["nom"], groupe["palier_min"]), lien="/admin/gouvernance")
    session["flash"] = ("Demande d'accès enregistrée : l'administrateur validera après réception du "
                        "paiement Mobile Money.")
    return redirect(url_for("groupe_page", gid=gid))


@app.route("/groupes/<int:gid>/retirer", methods=["POST"])
def groupe_retirer(gid: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    conn.execute("DELETE FROM groupe_membres WHERE groupe_id = ? AND user_id = ?", (gid, me["id"]))
    conn.commit()
    session["flash"] = "Vous avez quitté le groupe."
    return redirect(url_for("groupes_page"))


@app.route("/groupes/<int:gid>/fermer", methods=["POST"])
def groupe_fermer(gid: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    groupe = _groupe_ou_404(gid)
    if groupe is None:
        return "Groupe inconnu.", 404
    if groupe["createur_id"] != me["id"] and not me["est_admin"]:
        session["flash"] = "Seul le créateur ou l'administrateur peut fermer ce groupe."
        return redirect(url_for("groupe_page", gid=gid))
    conn = db()
    conn.execute("UPDATE groupes SET statut = 'ferme' WHERE id = ?", (gid,))
    conn.commit()
    journal_action("groupe_ferme", cible="groupe #%d" % gid, utilisateur=me["id"])
    evenement_v6("groupe", "Groupe fermé : « %s »" % groupe["nom"], lien="/groupes/%d" % gid)
    session["flash"] = "Groupe fermé : plus aucun message n'est accepté."
    return redirect(url_for("groupe_page", gid=gid))


# ----------------------------------------------------------------------------
# Accès aux groupes privés payants — règle unique, vérifiée côté serveur
# ----------------------------------------------------------------------------
def acces_groupe_v6(groupe, user_id) -> Tuple[bool, str]:
    """Vrai si l'utilisateur est le créateur, l'administrateur, un membre validé,
    ou un abonné dont le palier PAYÉ (validé) atteint le palier du groupe."""
    if groupe is None:
        return False, "groupe inconnu"
    try:
        if int(groupe["createur_id"]) == int(user_id):
            return True, "créateur"
    except (TypeError, ValueError, KeyError, IndexError):
        return False, "groupe inconnu"
    me = utilisateur_par_id(user_id)
    if me is not None and me["est_admin"]:
        return True, "administrateur"
    conn = db()
    membre = conn.execute("SELECT statut FROM groupe_membres WHERE groupe_id = ? AND user_id = ?",
                          (groupe["id"], user_id)).fetchone()
    if membre is not None and membre["statut"] == "actif":
        return True, "membre validé"
    abonnement = conn.execute("SELECT palier FROM abonnements WHERE abonne_id = ? AND createur_id = ?"
                              " AND statut = 'valide' AND palier >= ? ORDER BY palier DESC LIMIT 1",
                              (user_id, groupe["createur_id"], groupe["palier_min"])).fetchone()
    if abonnement is not None:
        return True, "abonnement %g FCFA" % abonnement["palier"]
    return False, "palier %g FCFA requis" % groupe["palier_min"]


# ----------------------------------------------------------------------------
# 3) TABLEAU DE BORD TEMPS RÉEL — WebSocket (flask-sock) + repli SSE
# ----------------------------------------------------------------------------
_BUS_VERROU = _threading.Lock()
_BUS_ABONNES: Dict[int, "_queue.Queue"] = {}
_BUS_COMPTEUR = [0]


def _bus_abonner() -> "_queue.Queue":
    file_attente: "_queue.Queue" = _queue.Queue(maxsize=300)
    with _BUS_VERROU:
        _BUS_COMPTEUR[0] += 1
        cle = _BUS_COMPTEUR[0]
        _BUS_ABONNES[cle] = file_attente
    setattr(file_attente, "cle_bus", cle)
    return file_attente


def _bus_retirer(file_attente) -> None:
    with _BUS_VERROU:
        _BUS_ABONNES.pop(getattr(file_attente, "cle_bus", None), None)


def _bus_diffuser(payload: Dict[str, Any]) -> None:
    with _BUS_VERROU:
        abonnes = list(_BUS_ABONNES.values())
    for file_attente in abonnes:
        try:
            file_attente.put_nowait(payload)
        except Exception:
            pass  # client lent : l'événement reste en base pour l'historique


def evenement_v6(type_: str, texte: str, lien: str = "", montant: float = 0.0) -> Dict[str, Any]:
    """Publie un événement de gouvernance : persistance + diffusion temps réel."""
    evenement = {"id": None, "type": type_, "texte": texte, "lien": lien,
                 "montant": float(montant or 0), "cree_le": maintenant()}
    try:
        conn = db()
        curseur = conn.execute("INSERT INTO dashboard_evenements (type, texte, lien, montant, lu, cree_le)"
                               " VALUES (?,?,?,?,0,?)",
                               (type_[:40], texte[:400], lien[:200], float(montant or 0),
                                evenement["cree_le"]))
        conn.commit()
        evenement["id"] = curseur.lastrowid
    except Exception:
        LOGGER.warning("événement temps réel non persisté", exc_info=False)
    _bus_diffuser(evenement)
    return evenement


def instantane_v6() -> Dict[str, Any]:
    """Photographie de l'état de la plateforme pour le tableau de bord."""
    conn = db()

    def compter(requete: str, params: Tuple = ()) -> int:
        try:
            return int(conn.execute(requete, params).fetchone()[0])
        except Exception:
            return 0

    soldes = conn.execute(
        "SELECT u.id, u.pseudo, u.telephone, u.payout_phone, COALESCE(SUM(p.tickets),0) AS tickets,"
        " COALESCE(SUM(p.fcfa),0) AS fcfa FROM users u JOIN portefeuille p ON p.user_id = u.id"
        " WHERE p.sens = 'credit' AND p.statut = 'en_attente' GROUP BY u.id ORDER BY fcfa DESC LIMIT 20"
    ).fetchall()
    return {
        "horodatage": maintenant(),
        "abonnements_attente": compter("SELECT COUNT(*) FROM abonnements WHERE statut = 'en_attente'"),
        "pourboires_attente": compter("SELECT COUNT(*) FROM pourboires WHERE statut = 'en_attente'"),
        "plaintes_ouvertes": compter("SELECT COUNT(*) FROM plaintes WHERE statut = 'ouverte'"),
        "membres_muets": compter("SELECT COUNT(*) FROM users WHERE muet = 1"),
        "bans_temporaires": compter("SELECT COUNT(*) FROM users WHERE ban_temporaire_jusqua <> ''"),
        "acces_groupes_attente": compter("SELECT COUNT(*) FROM groupe_membres WHERE statut = 'en_attente'"),
        "membres": compter("SELECT COUNT(*) FROM users"),
        "publications": compter("SELECT COUNT(*) FROM posts"),
        "soldes_attente": [dict(l) for l in soldes],
        "ws": _WS_DISPONIBLE,
    }


def _historique_evenements(limite: int = 40) -> List[Dict[str, Any]]:
    return [dict(l) for l in db().execute(
        "SELECT * FROM dashboard_evenements ORDER BY id DESC LIMIT ?", (limite,)).fetchall()]


TEMPLATES["admin_temps_reel.html"] = """{% block contenu %}
<div class="carte"><h1>📡 Tableau de bord temps réel</h1>
<p class="muet">Flux poussé par le serveur : nouveaux abonnements et pourboires, plaintes, soldes en
attente de versement, sanctions et accès aux groupes. Transport :
<b>{% if ws_disponible %}WebSocket (/ws/admin){% else %}SSE (/admin/temps-reel/flux) — installez
<code>flask-sock</code> pour le WebSocket{% endif %}</b>. Dernière mise à jour :
<span id="horodatage-v6" class="muet"></span></p></div>

<div class="carte"><div class="grid" id="compteurs-v6"></div></div>

<div class="carte"><h2>💰 Soldes en attente de versement (TICKETS + FCFA + numéro)</h2>
<div id="soldes-v6">
{% for s in instantane['soldes_attente'] %}<div class="msg"><b>{{ s['pseudo'] }}</b> — {{ s['tickets'] }} tickets / {{ s['fcfa'] }} {{ devise }} — {{ s['payout_phone'] or s['telephone'] or 'numéro à renseigner' }}</div>
{% else %}<p class="muet">Aucun solde en attente.</p>{% endfor %}</div>
<p class="muet"><a href="{{ url_for('admin') }}">Verser depuis le tableau de bord</a></p></div>

<div class="carte"><h2>Flux d'événements</h2>
<div id="flux-v6"></div>
<p class="muet" id="etat-flux-v6">Connexion en cours…</p></div>

<div class="carte"><h2>Historique (40 derniers événements)</h2>
<table><tr><th>Horodatage</th><th>Type</th><th>Détail</th></tr>
{% for e in historique %}<tr><td class="muet">{{ e['cree_le'] }}</td><td>{{ e['type'] }}</td>
<td>{% if e['lien'] %}<a href="{{ e['lien'] }}">{{ e['texte'] }}</a>{% else %}{{ e['texte'] }}{% endif %}</td></tr>
{% else %}<tr><td colspan="3" class="muet">Aucun événement pour l'instant.</td></tr>{% endfor %}</table></div>
<script>
{% raw %}
var ZONE_FLUX = document.getElementById('flux-v6');
var ETAT_FLUX = document.getElementById('etat-flux-v6');
var ETIQUETTES = [['abonnements_attente', 'Abonnements à valider'],
                  ['pourboires_attente', 'Pourboires à valider'],
                  ['plaintes_ouvertes', 'Plaintes ouvertes'],
                  ['membres_muets', 'Membres en lecture seule'],
                  ['bans_temporaires', 'Bans temporaires actifs'],
                  ['acces_groupes_attente', 'Accès groupes à valider'],
                  ['membres', 'Membres'],
                  ['publications', 'Publications']];

function ajouter_ligne(type, texte, heure){
  var bloc = document.createElement('div'); bloc.className = 'msg';
  var tete = document.createElement('b'); tete.textContent = '[' + (heure || '') + '] ' + (type || 'info');
  var corps = document.createElement('div'); corps.textContent = texte || '';
  bloc.appendChild(tete); bloc.appendChild(corps);
  ZONE_FLUX.insertBefore(bloc, ZONE_FLUX.firstChild);
}

function maj_compteurs(etat){
  if (!etat) return;
  var zone = document.getElementById('compteurs-v6');
  zone.innerHTML = '';
  for (var i = 0; i < ETIQUETTES.length; i++){
    var tuile = document.createElement('div'); tuile.className = 'tuile';
    var valeur = document.createElement('b'); valeur.textContent = etat[ETIQUETTES[i][0]] || 0;
    var libelle = document.createElement('span'); libelle.className = 'muet';
    libelle.textContent = ETIQUETTES[i][1];
    tuile.appendChild(valeur); tuile.appendChild(libelle); zone.appendChild(tuile);
  }
  var zoneSoldes = document.getElementById('soldes-v6');
  zoneSoldes.innerHTML = '';
  var soldes = etat.soldes_attente || [];
  if (!soldes.length){ var vide = document.createElement('p'); vide.className = 'muet';
    vide.textContent = 'Aucun solde en attente.'; zoneSoldes.appendChild(vide); }
  for (var j = 0; j < soldes.length; j++){
    var ligne = document.createElement('div'); ligne.className = 'msg';
    ligne.textContent = soldes[j].pseudo + ' — ' + soldes[j].tickets + ' tickets / ' + soldes[j].fcfa +
      ' FCFA — ' + (soldes[j].payout_phone || soldes[j].telephone || 'numéro à renseigner');
    zoneSoldes.appendChild(ligne);
  }
  var marque = document.getElementById('horodatage-v6');
  if (marque) marque.textContent = etat.horodatage || '';
}

function traiter(payload){
  if (!payload) return;
  if (payload.instantane) maj_compteurs(payload.instantane);
  if (payload.type && payload.type !== 'battement'){
    ajouter_ligne(payload.type, payload.texte, payload.cree_le);
  } else if (payload.type === 'battement' && payload.instantane){
    maj_compteurs(payload.instantane);
  }
}

function via_sse(){
  ETAT_FLUX.textContent = 'Flux SSE actif.';
  var source = new EventSource('/admin/temps-reel/flux');
  source.onmessage = function(evt){ try { traiter(JSON.parse(evt.data)); } catch (e) {} };
  source.onerror = function(){ ETAT_FLUX.textContent = 'Flux SSE interrompu — reconnexion automatique…'; };
}

function via_websocket(){
  try {
    var protocole = (location.protocol === 'https:') ? 'wss://' : 'ws://';
    var ws = new WebSocket(protocole + location.host + '/ws/admin');
    ws.onopen = function(){ ETAT_FLUX.textContent = 'WebSocket connecté.'; };
    ws.onmessage = function(evt){
      var payload = null;
      try { payload = JSON.parse(evt.data); } catch (e) { return; }
      if (payload.type === 'refus'){ ETAT_FLUX.textContent = payload.texte; return; }
      traiter(payload);
    };
    ws.onerror = function(){ ETAT_FLUX.textContent = 'WebSocket indisponible — repli SSE.'; via_sse(); };
    ws.onclose = function(){ if (ETAT_FLUX.textContent.indexOf('SSE') === -1) { ETAT_FLUX.textContent = 'WebSocket fermé — repli SSE.'; via_sse(); } };
  } catch (e) { via_sse(); }
}
via_websocket();
{% endraw %}
</script>
{% endblock %}"""


@app.route("/admin/temps-reel", methods=["GET"])
def admin_temps_reel():
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    conn.execute("UPDATE dashboard_evenements SET lu = 1 WHERE lu = 0")
    conn.commit()
    return page("admin_temps_reel.html", titre="Tableau de bord temps réel",
                instantane=instantane_v6(), historique=_historique_evenements(40))


@app.route("/admin/temps-reel/flux", methods=["GET"])
def admin_temps_reel_flux():
    """Repli sans dépendance : flux SSE du même bus d'événements."""
    refus = exiger_admin()
    if refus:
        return refus

    def generer():
        try:
            depuis = int(request.args.get("depuis", "0") or 0)
        except (TypeError, ValueError):
            depuis = 0
        connexion = sqlite3.connect(CONFIG["DB"])
        connexion.row_factory = sqlite3.Row
        yield ": flux ouvert — %s\n\n" % maintenant()
        try:
            while True:
                lignes = connexion.execute(
                    "SELECT * FROM dashboard_evenements WHERE id > ? ORDER BY id LIMIT 50",
                    (depuis,)).fetchall()
                for ligne in lignes:
                    depuis = ligne["id"]
                    yield "data: %s\n\n" % json.dumps(dict(ligne), ensure_ascii=False)
                if not lignes:
                    yield "data: %s\n\n" % json.dumps(
                        {"type": "battement", "instantane": None, "cree_le": maintenant()},
                        ensure_ascii=False)
                time.sleep(3)
        finally:
            connexion.close()

    return Response(_flux_contexte(generer()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


if _WS_DISPONIBLE:
    _SOCK_V6 = _SockV6(app)

    @_SOCK_V6.route("/ws/admin")
    def _ws_admin(ws):
        """WebSocket d'administration : réservé au rôle administrateur."""
        me = utilisateur_courant()
        if me is None or not me["est_admin"]:
            try:
                ws.send(json.dumps({"type": "refus",
                                    "texte": "Accès réservé à l'administrateur."}, ensure_ascii=False))
            except Exception:
                pass
            return
        file_attente = _bus_abonner()
        try:
            ws.send(json.dumps({"type": "instantane", "instantane": instantane_v6()},
                               ensure_ascii=False))
            while True:
                try:
                    evenement = file_attente.get(timeout=20)
                except _queue.Empty:
                    evenement = {"type": "battement", "texte": "", "instantane": instantane_v6(),
                                 "cree_le": maintenant()}
                ws.send(json.dumps(evenement, ensure_ascii=False))
        except Exception:
            pass  # connexion fermée par le navigateur
        finally:
            _bus_retirer(file_attente)


# ----------------------------------------------------------------------------
# Branchement du temps réel sur les flux existants de l'application
# ----------------------------------------------------------------------------
def evenement_solde_en_attente(user_id, montant) -> None:
    """Solde crédité au créateur mais NON ENCORE VERSÉ : l'événement envoyé au
    tableau de bord est unique (dé-duplication sur 8 secondes)."""
    try:
        montant = float(montant or 0)
    except (TypeError, ValueError):
        return
    if montant <= 0:
        return
    seuil = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=8)).strftime("%Y-%m-%d %H:%M:%S")
    deja = db().execute("SELECT id FROM dashboard_evenements WHERE type = 'solde_en_attente'"
                        " AND montant = ? AND texte LIKE ? AND cree_le >= ?"
                        " ORDER BY id DESC LIMIT 1",
                        (montant, "%" + pseudo_de_v6(user_id) + "%", seuil)).fetchone()
    if deja is not None:
        return
    evenement_v6("solde_en_attente", "Solde en attente : %s — %g %s à verser"
                 % (pseudo_de_v6(user_id), montant, CONFIG["DEVISE"]), lien="/admin", montant=montant)


_ORIG_CREER_ABONNEMENT = creer_abonnement
_ORIG_VALIDER_ABONNEMENT = valider_abonnement
_ORIG_CREER_POURBOIRE = creer_pourboire
_ORIG_VALIDER_POURBOIRE = valider_pourboire
_ORIG_ECRIRE_PORTEFEUILLE = ecrire_portefeuille
_ORIG_MARQUER_PAYE = marquer_paye


def creer_abonnement(*args, **kwargs):  # noqa: F811
    identifiant = _ORIG_CREER_ABONNEMENT(*args, **kwargs)
    try:
        abonne = _param_v6(args, kwargs, 0, "abonne_id", None)
        createur = _param_v6(args, kwargs, 1, "createur_id", None)
        palier = _param_v6(args, kwargs, 2, "palier", 0)
        evenement_v6("abonnement", "Nouvel abonnement en attente : %s → %s (%g %s)"
                     % (pseudo_de_v6(abonne), pseudo_de_v6(createur), float(palier or 0),
                        CONFIG["DEVISE"]), lien="/admin", montant=float(palier or 0))
    except Exception:
        LOGGER.warning("événement abonnement impossible", exc_info=False)
    return identifiant


def valider_abonnement(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_VALIDER_ABONNEMENT(*args, **kwargs)
    if resultat:
        try:
            aid = _param_v6(args, kwargs, 0, "abo_id", "?")
            evenement_v6("abonnement_valide", "Abonnement #%s validé — net crédité %s %s"
                         % (aid, resultat.get("net"), CONFIG["DEVISE"]), lien="/admin",
                         montant=float(resultat.get("net") or 0))
            ligne = db().execute("SELECT createur_id, net FROM abonnements WHERE id = ?",
                                 (aid,)).fetchone()
            if ligne is not None:
                evenement_solde_en_attente(ligne["createur_id"], ligne["net"])
        except Exception:
            LOGGER.warning("événement validation impossible", exc_info=False)
    return resultat


def creer_pourboire(*args, **kwargs):  # noqa: F811
    identifiant = _ORIG_CREER_POURBOIRE(*args, **kwargs)
    try:
        expediteur = _param_v6(args, kwargs, 0, "expediteur_id", None)
        createur = _param_v6(args, kwargs, 1, "createur_id", None)
        montant = _param_v6(args, kwargs, 2, "montant", 0)
        evenement_v6("pourboire", "Pourboire en attente : %s → %s (%g %s)"
                     % (pseudo_de_v6(expediteur), pseudo_de_v6(createur), float(montant or 0),
                        CONFIG["DEVISE"]), lien="/admin", montant=float(montant or 0))
    except Exception:
        LOGGER.warning("événement pourboire impossible", exc_info=False)
    return identifiant


def valider_pourboire(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_VALIDER_POURBOIRE(*args, **kwargs)
    if resultat:
        evenement_v6("pourboire_valide", "Pourboire validé — net crédité %s %s"
                     % (resultat.get("net"), CONFIG["DEVISE"]), lien="/admin",
                     montant=float(resultat.get("net") or 0))
        try:
            ligne = db().execute("SELECT createur_id, net FROM pourboires WHERE id = ?",
                                 (_param_v6(args, kwargs, 0, "tip_id", None),)).fetchone()
            if ligne is not None:
                evenement_solde_en_attente(ligne["createur_id"], ligne["net"])
        except Exception:
            LOGGER.warning("événement solde (pourboire) impossible", exc_info=False)
    return resultat


def ecrire_portefeuille(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_ECRIRE_PORTEFEUILLE(*args, **kwargs)
    try:
        sens = _param_v6(args, kwargs, 1, "sens", "")
        statut = _param_v6(args, kwargs, 5, "statut", "en_attente")
        if sens == "credit" and statut == "en_attente":
            user_id = _param_v6(args, kwargs, 0, "user_id", None)
            fcfa = float(_param_v6(args, kwargs, 2, "fcfa", 0) or 0)
            evenement_solde_en_attente(user_id, fcfa)
    except Exception:
        LOGGER.warning("événement portefeuille impossible", exc_info=False)
    return resultat


def marquer_paye(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_MARQUER_PAYE(*args, **kwargs)
    if isinstance(resultat, dict) and resultat.get("ok"):
        user_id = _param_v6(args, kwargs, 0, "user_id", None)
        evenement_v6("versement", "Versement effectué : %s — %s tickets / %s %s"
                     % (pseudo_de_v6(user_id), resultat.get("tickets"), resultat.get("fcfa"),
                        CONFIG["DEVISE"]), lien="/admin", montant=float(resultat.get("fcfa") or 0))
    return resultat


_ORIG_PLAINTE_CREER = app.view_functions.get("plainte_creer")


def _v6_plainte_creer(*args, **kwargs):
    """Plainte : événement temps réel pour l'administrateur."""
    me = utilisateur_courant()
    sujet = ""
    for cle in ("sujet", "objet", "titre", "motif"):
        valeur = (request.form.get(cle) or "").strip()
        if valeur:
            sujet = valeur[:120]
            break
    reponse = _ORIG_PLAINTE_CREER(*args, **kwargs)
    if me is not None:
        evenement_v6("plainte", "Nouvelle plainte de %s : %s" % (me["pseudo"], sujet or "(sans sujet)"),
                     lien="/admin")
    return reponse


if _ORIG_PLAINTE_CREER is not None:
    app.view_functions["plainte_creer"] = _v6_plainte_creer


_ORIG_PUBLIER = app.view_functions.get("publier")


def _v6_publier(*args, **kwargs):
    """Publication du fil : modérée avant enregistrement (masquage éventuel)."""
    me = utilisateur_courant()
    corps = (request.form.get("corps") or "").strip()
    if me is None or not corps:
        return _ORIG_PUBLIER(*args, **kwargs)
    moderation = appliquer_moderation(corps[:5000], me["id"], "publication")
    if moderation["masque"]:
        conn = db()
        curseur = conn.execute("INSERT INTO posts (auteur_id, corps, cree_le) VALUES (?,?,?)",
                               (me["id"], moderation["texte"], maintenant()))
        conn.commit()
        journal_action("publication", cible=me["pseudo"],
                       details="publication masquée par la modération automatique", utilisateur=me["id"])
        journal_action2(me["id"], "publication", objet="texte soumis à la modération",
                        details="Publication masquée par la modération automatique.")
        if moderation["banniere"]:
            session["flash"] = moderation["banniere"]
        return redirect(url_for("fil"))
    return _ORIG_PUBLIER(*args, **kwargs)


if _ORIG_PUBLIER is not None:
    app.view_functions["publier"] = _v6_publier


# ----------------------------------------------------------------------------
# 1bis) / 2bis) / 4bis) Page d'administration « Gouvernance »
# ----------------------------------------------------------------------------
TEMPLATES["admin_gouvernance.html"] = """{% block contenu %}
<div class="carte"><h1>🛡️ Gouvernance — lecture seule, modération automatique, groupes</h1>
<p class="muet">Le mode lecture seule n'est PAS un blocage : le membre garde l'accès en lecture, les
plaintes et la discussion avec vous. Le blocage (CGU) se trouve sur le
<a href="{{ url_for('admin') }}">tableau de bord</a>.</p></div>

<div class="carte"><h2>✍️ Modération automatique</h2>
<form method="post" action="{{ url_for('admin_reglages_v6') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <label><input type="checkbox" name="automod_actif" value="1" style="width:auto"
    {{ 'checked' if reglages['automod_actif'] == '1' }}> Activer les règles automatiques</label>
  <label>Caractère de masquage</label>
  <input name="masque" value="{{ reglages['masque'] }}" maxlength="3" style="width:70px">
  <label>Durée par défaut d'un ban temporaire (min)</label>
  <input name="ban_duree_min" type="number" min="1" value="{{ reglages['ban_duree_min'] }}" style="width:90px">
  <button>Enregistrer</button>
</form>
<p class="muet">Statut : <b>{{ 'règles ACTIVES' if reglages['automod_actif'] == '1' else 'règles INACTIVES (aucun mot masqué)' }}</b>.
Un mot « ban_temporaire » masque le mot ET passe l'auteur en lecture seule pour la durée indiquée.</p>
<form method="post" action="{{ url_for('admin_mot_ajouter') }}" class="row">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input name="terme" maxlength="60" placeholder="Mot ou expression à interdire" required style="width:220px">
  <select name="action" style="width:auto"><option value="masquer">masquer le mot</option>
    <option value="ban_temporaire">masquer + ban temporaire</option></select>
  <input name="duree_min" type="number" min="1" value="60" style="width:90px">
  <button>Ajouter la règle</button>
</form>
<table><tr><th>Mot</th><th>Action</th><th>Durée</th><th>État</th><th></th></tr>
{% for m in mots %}<tr><td><b>{{ m['terme'] }}</b></td><td>{{ m['action'] }}</td>
<td>{{ m['duree_min'] }} min</td><td>{{ 'active' if m['actif'] else 'désactivée' }}</td>
<td class="row">
  <form method="post" action="{{ url_for('admin_mot_basculer', mot_id=m['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn-sec">{{ 'Désactiver' if m['actif'] else 'Activer' }}</button></form>
  <form method="post" action="{{ url_for('admin_mot_supprimer', mot_id=m['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-rouge">Supprimer</button></form>
</td></tr>{% else %}<tr><td colspan="5" class="muet">Aucune règle enregistrée.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>🚫 Membres en lecture seule ({{ muets|length }})</h2>
<table><tr><th>Membre</th><th>Fin de la sanction</th><th>Motif</th><th>Actions</th></tr>
{% for u in muets %}<tr><td><a href="{{ url_for('messages_conversation', autre=u['id']) }}"><b>{{ u['pseudo'] }}</b></a></td>
<td>{{ u['muet_jusqua'] or "jusqu'à nouvel ordre" }}</td><td class="muet">{{ u['muet_motif'] }}</td>
<td class="row">
  <form method="post" action="{{ url_for('admin_muet', uid=u['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="duree" value="1440">
    <input type="hidden" name="motif" value="prolongation"><button class="btn-sec">+ 24 h</button></form>
  <form method="post" action="{{ url_for('admin_demuet', uid=u['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-vert">Rétablir</button></form>
</td></tr>{% else %}<tr><td colspan="4" class="muet">Aucun membre en lecture seule.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>👥 Demandes d'accès aux groupes ({{ demandes|length }})</h2>
<table><tr><th>Groupe</th><th>Palier</th><th>Membre</th><th>Référence</th><th>Actions</th></tr>
{% for d in demandes %}<tr><td>{{ d['nom'] }}</td><td>{{ d['palier_min'] }} {{ devise }}</td>
<td>{{ d['pseudo'] }}</td><td class="muet">{{ d['reference'] }}</td>
<td class="row">
  <form method="post" action="{{ url_for('admin_groupe_acces_valider', mid=d['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-vert">Valider l'accès</button></form>
  <form method="post" action="{{ url_for('admin_groupe_acces_refuser', mid=d['id']) }}">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-rouge">Refuser</button></form>
</td></tr>{% else %}<tr><td colspan="5" class="muet">Aucune demande en attente.</td></tr>{% endfor %}</table></div>

<div class="carte"><h2>🧾 Journal de modération (40 dernières actions)</h2>
<table><tr><th>Horodatage</th><th>Auteur</th><th>Contexte</th><th>Mot</th><th>Décision</th><th>Texte final</th></tr>
{% for j in journal %}<tr><td class="muet">{{ j['cree_le'] }}</td><td>{{ j['pseudo'] or j['user_id'] or '—' }}</td>
<td>{{ j['contexte'] }}</td><td>{{ j['terme'] }}</td><td>{{ j['action'] }}{% if j['expire_le'] %}
<br><span class="muet">jusqu'au {{ j['expire_le'] }}</span>{% endif %}</td>
<td class="muet">{{ j['texte_final'][:120] }}</td></tr>
{% else %}<tr><td colspan="6" class="muet">Aucune action de modération.</td></tr>{% endfor %}</table></div>
{% endblock %}"""


@app.route("/admin/gouvernance", methods=["GET"])
def admin_gouvernance():
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    return page("admin_gouvernance.html", titre="Gouvernance et modération",
                mots=conn.execute("SELECT * FROM moderation_mots ORDER BY terme").fetchall(),
                muets=conn.execute("SELECT id, pseudo, muet_jusqua, muet_motif FROM users"
                                   " WHERE muet = 1 ORDER BY muet_jusqua").fetchall(),
                demandes=conn.execute("SELECT m.*, g.nom, g.palier_min, u.pseudo FROM groupe_membres m"
                                      " JOIN groupes g ON g.id = m.groupe_id JOIN users u ON u.id = m.user_id"
                                      " WHERE m.statut = 'en_attente' ORDER BY m.id").fetchall(),
                journal=conn.execute("SELECT j.*, u.pseudo FROM moderation_journal j"
                                     " LEFT JOIN users u ON u.id = j.user_id"
                                     " ORDER BY j.id DESC LIMIT 40").fetchall(),
                reglages={cle: reglage_v6(cle) for cle in REGLAGES_V6_DEFAUT})


@app.route("/admin/moderation/reglages", methods=["POST"])
def admin_reglages_v6():
    refus = exiger_admin()
    if refus:
        return refus
    maj_reglage_v6("automod_actif", "1" if request.form.get("automod_actif") == "1" else "0")
    masque = (request.form.get("masque") or "▮").strip()[:3] or "▮"
    maj_reglage_v6("masque", masque)
    duree = (request.form.get("ban_duree_min") or "60").strip()
    maj_reglage_v6("ban_duree_min", duree if duree.isdigit() and int(duree) > 0 else "60")
    mots_interdits(force=True)
    journal_action("moderation_reglages", details="automod=%s" % reglage_v6("automod_actif"))
    session["flash"] = "Réglages de modération enregistrés."
    return redirect(url_for("admin_gouvernance"))


@app.route("/admin/moderation/mot", methods=["POST"])
def admin_mot_ajouter():
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    terme = (request.form.get("terme") or "").strip()[:60]
    action = request.form.get("action") if request.form.get("action") in ("masquer", "ban_temporaire") else "masquer"
    duree = (request.form.get("duree_min") or "60").strip()
    duree = int(duree) if duree.isdigit() else 60
    if len(terme) < 2:
        session["flash"] = "Le mot à interdire est trop court."
        return redirect(url_for("admin_gouvernance"))
    conn = db()
    try:
        conn.execute("INSERT INTO moderation_mots (terme, action, duree_min, actif, auteur_id, cree_le)"
                     " VALUES (?,?,?,?,?,?)", (terme, action, duree, 1, me["id"], maintenant()))
        conn.commit()
        session["flash"] = "Règle ajoutée : « %s » → %s." % (terme, action)
    except sqlite3.IntegrityError:
        session["flash"] = "Cette règle existe déjà."
    mots_interdits(force=True)
    journal_action("moderation_regle", cible=terme, details=action, utilisateur=me["id"])
    return redirect(url_for("admin_gouvernance"))


@app.route("/admin/moderation/mot/<int:mot_id>/basculer", methods=["POST"])
def admin_mot_basculer(mot_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    conn.execute("UPDATE moderation_mots SET actif = 1 - actif WHERE id = ?", (mot_id,))
    conn.commit()
    mots_interdits(force=True)
    session["flash"] = "État de la règle modifié."
    return redirect(url_for("admin_gouvernance"))


@app.route("/admin/moderation/mot/<int:mot_id>/supprimer", methods=["POST"])
def admin_mot_supprimer(mot_id: int):
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    conn.execute("DELETE FROM moderation_mots WHERE id = ?", (mot_id,))
    conn.commit()
    mots_interdits(force=True)
    journal_action("moderation_regle_supprimee", cible="règle #%d" % mot_id)
    session["flash"] = "Règle supprimée."
    return redirect(url_for("admin_gouvernance"))


@app.route("/admin/groupe/acces/<int:mid>/valider", methods=["POST"])
def admin_groupe_acces_valider(mid: int):
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    ligne = conn.execute("SELECT * FROM groupe_membres WHERE id = ?", (mid,)).fetchone()
    if ligne is None:
        return "Demande inconnue.", 404
    conn.execute("UPDATE groupe_membres SET statut = 'actif', valide_le = ? WHERE id = ?",
                 (maintenant(), mid))
    conn.commit()
    journal_action("groupe_acces_valide", cible="accès #%d" % mid)
    evenement_v6("groupe_acces_valide", "Accès validé pour %s" % pseudo_de_v6(ligne["user_id"]),
                 lien="/admin/gouvernance")
    session["flash"] = "Accès au groupe validé."
    return redirect(url_for("admin_gouvernance"))


@app.route("/admin/groupe/acces/<int:mid>/refuser", methods=["POST"])
def admin_groupe_acces_refuser(mid: int):
    refus = exiger_admin()
    if refus:
        return refus
    conn = db()
    conn.execute("UPDATE groupe_membres SET statut = 'refuse' WHERE id = ?", (mid,))
    conn.commit()
    journal_action("groupe_acces_refuse", cible="accès #%d" % mid)
    session["flash"] = "Accès refusé."
    return redirect(url_for("admin_gouvernance"))


# ----------------------------------------------------------------------------
# Navigation V6 : groupes pour tous, temps réel + gouvernance pour l'admin
# ----------------------------------------------------------------------------
_ANCRE_NAV_V6 = "<a href=\"{{ url_for('statistiques') }}\">Statistiques</a>"
if _ANCRE_NAV_V6 in TEMPLATES["base.html"]:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _ANCRE_NAV_V6, _ANCRE_NAV_V6 + "<a href=\"{{ url_for('groupes_page') }}\">Groupes</a>", 1)
_ANCRE_ADMIN_V6 = "<a href=\"{{ url_for('admin') }}\"><b>Admin</b></a>"
if _ANCRE_ADMIN_V6 in TEMPLATES["base.html"]:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _ANCRE_ADMIN_V6,
        _ANCRE_ADMIN_V6
        + "<a href=\"{{ url_for('admin_temps_reel') }}\"><b>Temps réel</b></a>"
        + "<a href=\"{{ url_for('admin_gouvernance') }}\"><b>Gouvernance</b></a>", 1)

# Le DictLoader garde une référence sur TEMPLATES : on réinstalle le chargeur et on
# vide le cache de Jinja pour que les gabarits V6 soient bien résolus.
app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES)])
try:
    app.jinja_env.cache.clear()
except Exception:
    pass

# =============================================================================
# EXTENSIONS V7 (16 FONCTIONNALITÉS) — TOUTBOT MUNDO reste 100 % TEXTE
# =============================================================================

# =============================================================================
# 🚀 TOUTBOT MUNDO V7 — EXTENSIONS (16 FONCTIONNALITÉS) — RÈGLE ABSOLUE : TEXTE
# -----------------------------------------------------------------------------
#  1. Score de réputation / confiance visible (ancienneté, validations, plaintes)
#  2. Multi-administrateurs : rôles + permissions granulaires
#  3. Journal d'audit immuable (chaîne de hachage SHA-256) + vérification publique
#  4. Alertes SMS / WhatsApp vers l'administrateur (fournisseur réel, clés en env)
#  5. Résumé quotidien automatique du fil (IA) → message aux abonnés volontaires
#  6. Détection spam / multi-comptes (empreinte comportementale + téléphone)
#  7. Chat IA multilingue (français, fon, baoulé) ancré sur des sources réelles
#  8. Rapports comptables narratifs (IA) pour l'administrateur
#  9. Mode hors-ligne avancé (50 dernières publications + rédaction différée)
# 10. Notifications push (PWA) — messages, « J'aime », validations de paiement
# 11. Lecteur vocal (TTS) — accessibilité, 100 % côté client, aucun audio stocké
# 12. Thèmes de profil par créateur (couleurs CSS uniquement, aucune image)
# 13. Badges / achievements (texte + emoji, jamais d'image envoyée)
# 14. Export RGPD complet en un clic + gel temporaire du compte
# 15. DAO légère : votes pondérés par les tickets (voir AVERTISSEMENTS)
# 16. Fonds de garantie « non-paiement » (voir AVERTISSEMENTS)
#
# AUCUNE extension ne crée, n'accepte, ne stocke ni ne diffuse un média :
# pas d'image, pas de vidéo, pas d'audio, pas de pièce jointe. Le TTS est une
# synthèse vocale du navigateur (aucun fichier produit) et les thèmes sont des
# couleurs CSS. Les lives restent des salons de discussion ÉCRITS.
# =============================================================================
import base64 as _base64
import hashlib
import hmac
import uuid as _uuid

V7_VERSION = "7.0"

AVERTISSEMENTS_V7 = (
    "AVERTISSEMENTS RÉGLEMENTAIRES — à faire valider par un professionnel du droit "
    "et de la finance AVANT toute mise en production : (1) le « fonds de garantie » "
    "non-paiement s'apparente à une activité d'assurance, réservée à des sociétés "
    "agréées (code CIMA dans l'UEMOA) ; la BCEAO encadre par ailleurs la monnaie "
    "électronique, le KYC et l'AML ; (2) une DAO qui ferait voter sur le TAUX DE "
    "COMMISSION laisse des tiers fixer la rémunération d'un service de paiement : "
    "dans ce livrable le résultat du vote est seulement PROPOSÉ à l'administrateur, "
    "jamais appliqué automatiquement ; (3) l'escrow, les prêts entre membres et la "
    "conversion FCFA/USDT repris du V5 restent soumis à la réglementation "
    "BCEAO/UEMOA ; (4) l'export RGPD vise le RGPD européen, à croiser avec la loi "
    "béninoise n° 2017-20 sur le code du numérique et l'APDP. Ce logiciel est "
    "fourni à titre d'expérimentation technique."
)

CONFIG_V7: Dict[str, Any] = {
    # --- Alertes administrateur -------------------------------------------------
    "ALERTE_TELEPHONE": _env("TOUTBOT_ALERTE_TELEPHONE", ""),          # +229... (jamais inventé)
    "ALERTE_SEUIL_FCFA": _env_float("TOUTBOT_ALERTE_SEUIL_FCFA", 100000.0),
    "TWILIO_SID": _env("TWILIO_ACCOUNT_SID", ""),
    "TWILIO_TOKEN": _env("TWILIO_AUTH_TOKEN", ""),
    "TWILIO_FROM": _env("TWILIO_FROM", ""),
    "WHATSAPP_TOKEN": _env("WHATSAPP_TOKEN", ""),
    "WHATSAPP_PHONE_ID": _env("WHATSAPP_PHONE_ID", ""),
    "ALERTE_WEBHOOK": _env("TOUTBOT_ALERTE_WEBHOOK", ""),
    # --- Notifications push (PWA) ----------------------------------------------
    "VAPID_PUBLIC": _env("VAPID_PUBLIC_KEY", ""),
    "VAPID_PRIVATE": _env("VAPID_PRIVATE_KEY", ""),
    "VAPID_SUBJECT": _env("VAPID_SUBJECT", "mailto:admin@toutbot.local"),
    # --- Fonds de garantie / résumé quotidien ----------------------------------
    "GARANTIE_PCT": _env_float("TOUTBOT_GARANTIE_PCT", 1.0),
    "RESUME_HEURE_UTC": _env_int("TOUTBOT_RESUME_HEURE", 20),
    "RESUME_MAX_POSTS": _env_int("TOUTBOT_RESUME_MAX_POSTS", 40),
    "HORS_LIGNE_POSTS": _env_int("TOUTBOT_HORS_LIGNE_POSTS", 50),
    "DAO_PLAFOND_POIDS": _env_int("TOUTBOT_DAO_PLAFOND_POIDS", 5000),
}

PERMISSIONS_V7: Dict[str, str] = {
    "moderer": "Sanctionner un membre, régler la modération automatique",
    "groupes": "Valider ou refuser les accès aux groupes privés payants",
    "utilisateurs": "Bloquer, débloquer ou geler un compte",
    "comptabilite": "Valider les paiements, marquer les versements, exports comptables",
    "audit_lecture": "Consulter et vérifier le journal d'audit chaîné",
    "alertes": "Configurer les alertes SMS / WhatsApp",
    "reputation": "Ajuster les scores de réputation",
    "anti_spam": "Consulter le tableau anti-spam / multi-comptes",
    "dao": "Ouvrir, clôturer les scrutins et appliquer les résultats de la DAO",
    "fonds_garantie": "Gérer le fonds de garantie",
    "roles": "Attribuer et retirer les rôles d'administration",
    "rgpd": "Traiter les demandes de données personnelles (export, gel)",
}

ROLES_V7: Dict[str, Dict[str, Any]] = {
    "super_admin": {"libelle": "Super-administrateur",
                    "permissions": tuple(PERMISSIONS_V7.keys())},
    "moderateur": {"libelle": "Modérateur",
                   "permissions": ("moderer", "groupes", "utilisateurs", "anti_spam",
                                   "reputation")},
    "comptable": {"libelle": "Comptable",
                  "permissions": ("comptabilite", "audit_lecture", "alertes",
                                  "fonds_garantie")},
}

DUREES_GEL_V7 = {"24": "24 h", "168": "7 jours", "720": "30 jours", "0": "jusqu'à nouvel ordre"}

SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS reglages_v7 (
    cle    TEXT PRIMARY KEY,
    valeur TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS roles_membres (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,
    attribue_par INTEGER,
    cree_le      TEXT NOT NULL,
    UNIQUE(user_id, role)
);
CREATE TABLE IF NOT EXISTS reputation_journal (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta    INTEGER NOT NULL DEFAULT 0,
    motif    TEXT NOT NULL DEFAULT '',
    auteur   INTEGER,
    cree_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_chain (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    type_ecriture TEXT NOT NULL,
    ref           TEXT NOT NULL DEFAULT '',
    montant       REAL NOT NULL DEFAULT 0,
    user_id       INTEGER,
    details       TEXT NOT NULL DEFAULT '',
    charge        TEXT NOT NULL,
    hash_precedent TEXT NOT NULL,
    hash_courant  TEXT NOT NULL,
    cree_le       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_date ON audit_chain(cree_le);
CREATE INDEX IF NOT EXISTS idx_audit_cle ON audit_chain(type_ecriture, ref);
CREATE TRIGGER IF NOT EXISTS audit_chain_ajout_seul_maj
BEFORE UPDATE ON audit_chain
BEGIN
    SELECT RAISE(ABORT, 'audit_chain est en AJOUT SEUL : modification interdite');
END;
CREATE TRIGGER IF NOT EXISTS audit_chain_ajout_seul_suppr
BEFORE DELETE ON audit_chain
BEGIN
    SELECT RAISE(ABORT, 'audit_chain est en AJOUT SEUL : suppression interdite');
END;
CREATE TABLE IF NOT EXISTS alertes_journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    canal       TEXT NOT NULL DEFAULT 'sms',
    destinataire TEXT NOT NULL DEFAULT '',
    type_alerte TEXT NOT NULL DEFAULT '',
    texte       TEXT NOT NULL DEFAULT '',
    statut      TEXT NOT NULL DEFAULT '',
    erreur      TEXT NOT NULL DEFAULT '',
    cree_le     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resumes_quotidiens (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    jour    TEXT NOT NULL UNIQUE,
    texte   TEXT NOT NULL DEFAULT '',
    origine TEXT NOT NULL DEFAULT 'regles',
    nb_posts INTEGER NOT NULL DEFAULT 0,
    cree_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resumes_abonnes (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    actif   INTEGER NOT NULL DEFAULT 1,
    maj_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS empreintes_connexion (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tel_hash TEXT NOT NULL DEFAULT '',
    ip_hash  TEXT NOT NULL DEFAULT '',
    ua_hash  TEXT NOT NULL DEFAULT '',
    cree_le  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_empreinte_ip ON empreintes_connexion(ip_hash);
CREATE TABLE IF NOT EXISTS spam_signaux (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type    TEXT NOT NULL DEFAULT '',
    score   INTEGER NOT NULL DEFAULT 0,
    details TEXT NOT NULL DEFAULT '',
    cree_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS badges_obtenus (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code      TEXT NOT NULL,
    obtenu_le TEXT NOT NULL,
    UNIQUE(user_id, code)
);
CREATE TABLE IF NOT EXISTS themes_createur (
    user_id   INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    accent    TEXT NOT NULL DEFAULT '',
    fond      TEXT NOT NULL DEFAULT '',
    texte     TEXT NOT NULL DEFAULT '',
    maj_le    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accessibilite (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    tts_actif   INTEGER NOT NULL DEFAULT 0,
    tts_langue  TEXT NOT NULL DEFAULT 'fr-FR',
    tts_vitesse REAL NOT NULL DEFAULT 1.0,
    maj_le      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_abonnements (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    infos    TEXT NOT NULL DEFAULT '',
    actif    INTEGER NOT NULL DEFAULT 1,
    cree_le  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications_differees (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    corps     TEXT NOT NULL,
    statut    TEXT NOT NULL DEFAULT 'en_attente',
    motif     TEXT NOT NULL DEFAULT '',
    cree_le   TEXT NOT NULL,
    publie_le TEXT
);
CREATE TABLE IF NOT EXISTS dao_propositions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    auteur_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type_       TEXT NOT NULL DEFAULT 'fonctionnalite',
    titre       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    valeur      TEXT NOT NULL DEFAULT '',
    statut      TEXT NOT NULL DEFAULT 'ouvert',
    ouvre_le    TEXT NOT NULL,
    ferme_le    TEXT NOT NULL DEFAULT '',
    resultat    TEXT NOT NULL DEFAULT '',
    cree_le     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dao_votes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    proposition_id INTEGER NOT NULL REFERENCES dao_propositions(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sens           TEXT NOT NULL DEFAULT 'oui',
    poids          INTEGER NOT NULL DEFAULT 0,
    cree_le        TEXT NOT NULL,
    UNIQUE(proposition_id, user_id)
);
CREATE TABLE IF NOT EXISTS fonds_garantie (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sens         TEXT NOT NULL DEFAULT 'credit',
    montant_fcfa REAL NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT '',
    ref          TEXT NOT NULL DEFAULT '',
    motif        TEXT NOT NULL DEFAULT '',
    cree_le      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports_narratifs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mois    TEXT NOT NULL,
    texte   TEXT NOT NULL DEFAULT '',
    origine TEXT NOT NULL DEFAULT 'regles',
    cree_le TEXT NOT NULL
);
"""

REGLAGES_V7_DEFAUT: Dict[str, str] = {
    "garantie_actif": "1",
    "garantie_pct": str(CONFIG_V7["GARANTIE_PCT"]),
    "alerte_seuil_fcfa": str(CONFIG_V7["ALERTE_SEUIL_FCFA"]),
    "alerte_sms_actif": "1",
    "alerte_whatsapp_actif": "1",
    "alerte_telephone": CONFIG_V7["ALERTE_TELEPHONE"],
    "resume_quotidien_auto": "1",
    "dao_actif": "0",
    "spam_seuil_alerte": "60",
    "reputation_visible": "1",
}


def init_schema_v7() -> None:
    conn = sqlite3.connect(CONFIG["DB"])
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_V7)
    for colonne, defaut in (("gele", "INTEGER NOT NULL DEFAULT 0"),
                            ("gele_jusqua", "TEXT NOT NULL DEFAULT ''"),
                            ("gele_motif", "TEXT NOT NULL DEFAULT ''")):
        try:
            conn.execute("ALTER TABLE users ADD COLUMN %s %s" % (colonne, defaut))
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    for cle, valeur in REGLAGES_V7_DEFAUT.items():
        conn.execute("INSERT OR IGNORE INTO reglages_v7 (cle, valeur) VALUES (?,?)", (cle, valeur))
    conn.commit()
    conn.close()


init_schema_v7()


# ----------------------------------------------------------------------------
# V7 — réglages et petites fabriques
# ----------------------------------------------------------------------------
def reglage_v7(cle: str, defaut: str = "") -> str:
    try:
        ligne = db().execute("SELECT valeur FROM reglages_v7 WHERE cle = ?", (cle,)).fetchone()
    except Exception:
        ligne = None
    if ligne is None:
        return REGLAGES_V7_DEFAUT.get(cle, defaut)
    return ligne["valeur"]


def maj_reglage_v7(cle: str, valeur: str) -> None:
    conn = db()
    conn.execute("INSERT INTO reglages_v7 (cle, valeur) VALUES (?,?)"
                 " ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur", (cle, str(valeur)))
    conn.commit()


def solde_tickets_v7(user_id) -> int:
    try:
        return int(round(float(solde(user_id)["tickets_payes"])))
    except Exception:
        return 0


# =============================================================================
# 1) SCORE DE RÉPUTATION / CONFIANCE
# =============================================================================
def score_reputation(user_id) -> Dict[str, Any]:
    """Score 0-100, recalculé à la volée : ancienneté, validations reçues,
    pourboires reçus, badges, ajustements manuels moins plaintes et sanctions."""
    vide = {"score": 0, "etiquette": "inconnu", "jours": 0, "validations": 0, "pourboires": 0,
            "plaintes_ouvertes": 0, "sanctions": 0, "badges": 0, "ajustements": 0,
            "composantes": [], "visible": False}
    u = utilisateur_par_id(user_id)
    if u is None:
        return vide
    conn = db()
    jours = 0
    try:
        cree = _dt.datetime.strptime(str(u["cree_le"]), "%Y-%m-%d %H:%M:%S")
        jours = max(0, (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - cree).days)
    except Exception:
        jours = 0
    validations = int(conn.execute("SELECT COUNT(*) n FROM abonnements WHERE createur_id = ?"
                                   " AND statut = 'valide'", (user_id,)).fetchone()["n"])
    pourboires = int(conn.execute("SELECT COUNT(*) n FROM pourboires WHERE createur_id = ?"
                                  " AND statut = 'valide'", (user_id,)).fetchone()["n"])
    plaintes = int(conn.execute("SELECT COUNT(*) n FROM plaintes WHERE user_id = ?"
                                " AND statut = 'ouverte'", (user_id,)).fetchone()["n"])
    sanctions = int(conn.execute("SELECT COUNT(*) n FROM moderation_journal WHERE user_id = ?",
                                 (user_id,)).fetchone()["n"])
    badges = int(conn.execute("SELECT COUNT(*) n FROM badges_obtenus WHERE user_id = ?",
                              (user_id,)).fetchone()["n"])
    ajus = int(round(float(conn.execute("SELECT COALESCE(SUM(delta),0) s FROM reputation_journal"
                                        " WHERE user_id = ?", (user_id,)).fetchone()["s"] or 0)))
    pts_anc = min(20, jours)
    pts_val = min(20, validations * 2)
    pts_tips = min(10, pourboires)
    pts_badges = min(10, badges)
    malus_pl = min(30, plaintes * 8)
    malus_sa = min(20, sanctions * 4)
    score = int(max(0, min(100, 50 + pts_anc + pts_val + pts_tips + pts_badges + ajus
                           - malus_pl - malus_sa)))
    etiquette = ("excellent" if score >= 80 else "bon" if score >= 60
                 else "moyen" if score >= 40 else "fragile")
    return {"score": score, "etiquette": etiquette, "jours": jours, "validations": validations,
            "pourboires": pourboires, "plaintes_ouvertes": plaintes, "sanctions": sanctions,
            "badges": badges, "ajustements": ajus,
            "visible": reglage_v7("reputation_visible", "1") == "1",
            "composantes": [
                ("Base neutre", 50),
                ("Ancienneté (%d jour(s))" % jours, pts_anc),
                ("Abonnements validés reçus (%d)" % validations, pts_val),
                ("Pourboires reçus (%d)" % pourboires, pts_tips),
                ("Badges obtenus (%d)" % badges, pts_badges),
                ("Ajustements manuels", ajus),
                ("Plaintes ouvertes (%d)" % plaintes, -malus_pl),
                ("Signaux de modération (%d)" % sanctions, -malus_sa),
            ]}


def ajuster_reputation(user_id: int, delta: int, motif: str, auteur_id=None) -> int:
    conn = db()
    conn.execute("INSERT INTO reputation_journal (user_id, delta, motif, auteur, cree_le)"
                 " VALUES (?,?,?,?,?)", (user_id, int(delta), (motif or "")[:200],
                                         auteur_id, maintenant()))
    conn.commit()
    journal_action("reputation_ajustee", cible="utilisateur #%d" % user_id,
                   details="delta %+d — %s" % (int(delta), (motif or "")[:120]), utilisateur=auteur_id)
    return score_reputation(user_id)["score"]


TEMPLATES["reputation.html"] = """{% block contenu %}
<div class="carte">
  <h1>Réputation &amp; confiance</h1>
  <p class="muet">Score public 0-100 calculé uniquement à partir de faits enregistrés :
    ancienneté, abonnements validés reçus, pourboires reçus, badges, plaintes ouvertes et
    signaux de modération. Aucune donnée externe, aucun média.</p>
</div>
{% if rep %}
<div class="carte">
  <h1>{{ cible['pseudo'] }} — {{ rep['score'] }}/100 <span class="badge">{{ rep['etiquette'] }}</span></h1>
  <table>
    <tr><th>Composante</th><th>Effet</th></tr>
    {% for libelle, effet in rep['composantes'] %}
      <tr><td>{{ libelle }}</td><td>{{ '%+d'|format(effet) }}</td></tr>
    {% endfor %}
  </table>
</div>
{% endif %}
<div class="carte">
  <h2>Membres les mieux notés</h2>
  <table><tr><th>Membre</th><th>Score</th><th>Étiquette</th></tr>
  {% for l in classement %}<tr><td><a href="{{ url_for('profil', pseudo=l['pseudo']) }}">{{ l['pseudo'] }}</a></td>
    <td>{{ l['score'] }}</td><td>{{ l['etiquette'] }}</td></tr>{% endfor %}
  </table>
</div>
{% endblock %}"""


@app.route("/reputation")
def reputation_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    classement = []
    for u in db().execute("SELECT id, pseudo FROM users ORDER BY id ASC LIMIT 200").fetchall():
        r = score_reputation(u["id"])
        classement.append({"pseudo": u["pseudo"], "score": r["score"], "etiquette": r["etiquette"]})
    classement.sort(key=lambda x: -x["score"])
    return page("reputation.html", titre="Réputation", rep=score_reputation(me["id"]),
                cible=me, classement=classement[:20])


@app.route("/admin/reputation/ajuster", methods=["POST"])
def admin_reputation_ajuster():
    refus = exiger_permission("reputation")
    if refus:
        return refus
    me = utilisateur_courant()
    cible = (request.form.get("user_id") or "").strip()
    delta = (request.form.get("delta") or "0").strip()
    motif = (request.form.get("motif") or "").strip()
    if not cible.isdigit():
        return "Identifiant invalide.", 400
    try:
        valeur = int(delta)
    except ValueError:
        return "Delta invalide (entier attendu).", 400
    valeur = max(-40, min(40, valeur))
    score = ajuster_reputation(int(cible), valeur, motif or "ajustement manuel", me["id"])
    session["flash"] = "Réputation ajustée : %+d. Nouveau score : %d/100." % (valeur, score)
    return redirect(request.referrer or url_for("admin_roles"))


# =============================================================================
# 2) MULTI-ADMINISTRATEURS : RÔLES + PERMISSIONS GRANULAIRES
# =============================================================================
def roles_de(user_id) -> List[str]:
    try:
        return [l["role"] for l in db().execute("SELECT role FROM roles_membres WHERE user_id = ?"
                                                " ORDER BY role ASC", (user_id,)).fetchall()]
    except Exception:
        return []


def permissions_de(user_id) -> List[str]:
    u = utilisateur_par_id(user_id)
    if u is not None and u["est_admin"]:
        return list(PERMISSIONS_V7.keys())
    permis: List[str] = []
    for role in roles_de(user_id):
        for p in ROLES_V7.get(role, {}).get("permissions", ()):
            if p not in permis:
                permis.append(p)
    return permis


def a_permission(user_id, permission: str) -> bool:
    return permission in permissions_de(user_id)


def exiger_permission(permission: str):
    me = utilisateur_courant()
    if me is None:
        return redirect(url_for("connexion"))
    if a_permission(me["id"], permission):
        return None
    journal_action("acces_refuse", cible=permission, details="permission manquante",
                   utilisateur=me["id"])
    return "Accès refusé : la permission « %s » est requise (%s)." % (permission,
                                                                     PERMISSIONS_V7.get(permission, "")), 403


def attribuer_role(user_id: int, role: str, decideur_id=None) -> bool:
    if role not in ROLES_V7:
        return False
    if utilisateur_par_id(user_id) is None:
        return False
    conn = db()
    conn.execute("INSERT OR IGNORE INTO roles_membres (user_id, role, attribue_par, cree_le)"
                 " VALUES (?,?,?,?)", (user_id, role, decideur_id, maintenant()))
    conn.commit()
    journal_action("role_attribue", cible="utilisateur #%d" % user_id, details=role,
                   utilisateur=decideur_id)
    evenement_v6("role", "Rôle « %s » attribué à %s" % (ROLES_V7[role]["libelle"],
                                                        pseudo_de_v6(user_id)),
                 lien="/admin/roles")
    return True


def retirer_role(user_id: int, role: str, decideur_id=None) -> bool:
    conn = db()
    curseur = conn.execute("DELETE FROM roles_membres WHERE user_id = ? AND role = ?", (user_id, role))
    conn.commit()
    if curseur.rowcount:
        journal_action("role_retire", cible="utilisateur #%d" % user_id, details=role,
                       utilisateur=decideur_id)
    return bool(curseur.rowcount)


TEMPLATES["admin_roles.html"] = """{% block contenu %}
<div class="carte">
  <h1>Rôles et permissions</h1>
  <p class="muet">Le super-administrateur historique (drapeau <b>est_admin</b>) garde tous les
    droits. Les rôles ci-dessous donnent des permissions granulaires, vérifiées côté serveur sur
    chaque route — jamais dans l'interface seule.</p>
  <p class="muet">{{ avertissements }}</p>
</div>
<div class="carte">
  <h2>Attribuer un rôle</h2>
  <form method="post" action="{{ url_for('admin_role_attribuer') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="identifiant" placeholder="Pseudo du membre" required style="width:220px">
    <select name="role" style="width:auto">{% for code, r in roles.items() %}
      <option value="{{ code }}">{{ r['libelle'] }}</option>{% endfor %}</select>
    <button>Attribuer</button>
  </form>
</div>
<div class="carte">
  <h2>Détenteurs de rôles</h2>
  <table><tr><th>Membre</th><th>Rôles</th><th>Permissions effectives</th><th></th></tr>
  {% for l in detenteurs %}<tr>
    <td><a href="{{ url_for('profil', pseudo=l['pseudo']) }}">{{ l['pseudo'] }}</a></td>
    <td>{{ l['roles'] }}</td><td class="muet">{{ l['permissions'] }}</td>
    <td><form method="post" action="{{ url_for('admin_role_retirer') }}"><input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="hidden" name="identifiant" value="{{ l['pseudo'] }}">
      <input name="role" placeholder="rôle" value="{{ l['roles'].split(', ')[0] }}" style="width:120px">
      <button class="btn-sec">Retirer</button></form></td></tr>
  {% endfor %}</table>
</div>
<div class="carte">
  <h2>Catalogue des permissions</h2>
  <table><tr><th>Permission</th><th>Portée</th></tr>
  {% for code, libelle in permissions.items() %}<tr><td>{{ code }}</td><td>{{ libelle }}</td></tr>{% endfor %}
  </table>
  <h3>Ajuster une réputation (permission « reputation »)</h3>
  <form method="post" action="{{ url_for('admin_reputation_ajuster') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="user_id" placeholder="identifiant numérique" required style="width:180px">
    <input name="delta" type="number" min="-40" max="40" value="-5" style="width:90px">
    <input name="motif" placeholder="motif" style="width:240px"><button class="btn-sec">Ajuster</button>
  </form>
</div>
{% endblock %}"""


@app.route("/admin/roles")
def admin_roles():
    refus = exiger_permission("roles")
    if refus:
        return refus
    detenteurs = []
    for ligne in db().execute("SELECT DISTINCT user_id FROM roles_membres ORDER BY user_id ASC").fetchall():
        u = utilisateur_par_id(ligne["user_id"])
        if u is None:
            continue
        detenteurs.append({"pseudo": u["pseudo"], "roles": ", ".join(roles_de(u["id"])),
                           "permissions": ", ".join(permissions_de(u["id"]))})
    return page("admin_roles.html", titre="Rôles", roles=ROLES_V7, permissions=PERMISSIONS_V7,
                detenteurs=detenteurs, avertissements=AVERTISSEMENTS_V7)


@app.route("/admin/roles/attribuer", methods=["POST"])
def admin_role_attribuer():
    refus = exiger_permission("roles")
    if refus:
        return refus
    me = utilisateur_courant()
    identifiant = (request.form.get("identifiant") or "").strip()
    role = (request.form.get("role") or "").strip()
    u = utilisateur_par_identifiant(identifiant)
    if u is None:
        session["flash"] = "Membre inconnu : « %s »." % identifiant
        return redirect(url_for("admin_roles"))
    if attribuer_role(u["id"], role, me["id"]):
        session["flash"] = "Rôle « %s » attribué à %s." % (ROLES_V7[role]["libelle"], u["pseudo"])
    else:
        session["flash"] = "Rôle inconnu ou déjà attribué."
    return redirect(url_for("admin_roles"))


@app.route("/admin/roles/retirer", methods=["POST"])
def admin_role_retirer():
    refus = exiger_permission("roles")
    if refus:
        return refus
    me = utilisateur_courant()
    u = utilisateur_par_identifiant((request.form.get("identifiant") or "").strip())
    role = (request.form.get("role") or "").strip()
    if u is None:
        session["flash"] = "Membre inconnu."
    elif retirer_role(u["id"], role, me["id"]):
        session["flash"] = "Rôle « %s » retiré à %s." % (role, u["pseudo"])
    else:
        session["flash"] = "Ce membre ne détient pas ce rôle."
    return redirect(url_for("admin_roles"))


# =============================================================================
# 3) JOURNAL D'AUDIT IMMUABLE — CHAÎNE DE HACHAGE SHA-256
# =============================================================================
_HASH_GENESE = "0" * 64


def _canonique_audit(type_ecriture: str, ref: str, montant, user_id, details: str,
                     horodatage: str) -> str:
    """Charge canonique et déterministe : c'est elle qui est hachée, donc vérifiable."""
    identifiant = "" if user_id in (None, "") else str(int(user_id))
    return "|".join((str(type_ecriture), str(ref or ""), "%.2f" % float(montant or 0),
                     identifiant, (details or "")[:300].replace("|", "/"), str(horodatage)))


def sceller_ecriture(type_ecriture: str, ref: str = "", montant: float = 0.0,
                     user_id=None, details: str = "") -> Optional[str]:
    """Ajoute une écriture à la chaîne : hash_courant = SHA256(hash_précédent | charge)."""
    try:
        conn = db()
        derniere = conn.execute("SELECT hash_courant FROM audit_chain ORDER BY id DESC LIMIT 1").fetchone()
        precedent = derniere["hash_courant"] if derniere else _HASH_GENESE
        horodatage = maintenant()
        charge = _canonique_audit(type_ecriture, ref, montant, user_id, details, horodatage)
        courant = hashlib.sha256((precedent + "|" + charge).encode("utf-8")).hexdigest()
        conn.execute("INSERT INTO audit_chain (type_ecriture, ref, montant, user_id, details,"
                     " charge, hash_precedent, hash_courant, cree_le)"
                     " VALUES (?,?,?,?,?,?,?,?,?)",
                     (str(type_ecriture)[:60], str(ref or "")[:80], float(montant or 0),
                      int(user_id) if str(user_id).isdigit() else None,
                      (details or "")[:300], charge, precedent, courant, horodatage))
        conn.commit()
        return courant
    except Exception:
        LOGGER.warning("écriture d'audit impossible", exc_info=False)
        return None


def deja_scelle(type_ecriture: str, ref: str) -> bool:
    try:
        return db().execute("SELECT id FROM audit_chain WHERE type_ecriture = ? AND ref = ? LIMIT 1",
                            (type_ecriture, str(ref))).fetchone() is not None
    except Exception:
        return False


def verifier_chaine_audit() -> Dict[str, Any]:
    """Recalcule toute la chaîne. Toute retouche d'une ligne casse la vérification."""
    try:
        conn = db()
        lignes = conn.execute("SELECT * FROM audit_chain ORDER BY id ASC").fetchall()
    except Exception as exc:
        return {"ok": False, "total": 0, "erreur": repr(exc)}
    precedent = _HASH_GENESE
    for rang, ligne in enumerate(lignes, start=1):
        # a) la charge stockée doit être exactement celle recalculée depuis les champs
        #    affichés de la ligne : une retouche du montant, de la référence ou du
        #    libellé casse la vérification, même si le hash stocké n'a pas été touché.
        charge_attendue = _canonique_audit(ligne["type_ecriture"], ligne["ref"], ligne["montant"],
                                           ligne["user_id"], ligne["details"], ligne["cree_le"])
        if charge_attendue != ligne["charge"]:
            return {"ok": False, "total": len(lignes), "verifiees": rang - 1,
                    "rupture_ligne": ligne["id"], "rupture_rang": rang,
                    "cause": "charge canonique altérée (montant, référence, libellé ou date retouchés)",
                    "charge_attendue": charge_attendue[:48], "charge_stockee": (ligne["charge"] or "")[:48]}
        # b) le chaînage de hachage lui-même.
        attendu = hashlib.sha256((precedent + "|" + ligne["charge"]).encode("utf-8")).hexdigest()
        if ligne["hash_precedent"] != precedent or ligne["hash_courant"] != attendu:
            return {"ok": False, "total": len(lignes), "verifiees": rang - 1,
                    "rupture_ligne": ligne["id"], "rupture_rang": rang,
                    "cause": "chaînage rompu (hash précédent ou hash courant non conforme)",
                    "hash_attendu": attendu[:32], "hash_stocke": (ligne["hash_courant"] or "")[:32]}
        precedent = ligne["hash_courant"]
    return {"ok": True, "total": len(lignes), "verifiees": len(lignes),
            "cause": "aucune", "dernier_hash": precedent, "genese": _HASH_GENESE}


def sceller_ecritures_existantes() -> int:
    """Scelle les écritures financières validées du grand livre absentes de la chaîne
    (idempotent : une même écriture n'est scellée qu'une fois)."""
    conn = db()
    n = 0
    for ligne in conn.execute("SELECT * FROM portefeuille ORDER BY id ASC LIMIT 2000").fetchall():
        ref = "PF-%d" % ligne["id"]
        if deja_scelle("portefeuille", ref):
            continue
        if sceller_ecriture("portefeuille", ref, ligne["fcfa"],
                            ligne["user_id"], "sens %s / statut %s / %s"
                            % (ligne["sens"], ligne["statut"], ligne["libelle"])):
            n += 1
    for ligne in conn.execute("SELECT * FROM abonnements WHERE statut = 'valide'"
                              " ORDER BY id ASC LIMIT 2000").fetchall():
        ref = "ABO-%d" % ligne["id"]
        if deja_scelle("abonnement_valide", ref):
            continue
        if sceller_ecriture("abonnement_valide", ref, ligne["net"], ligne["createur_id"],
                            "palier %g / commission %g" % (ligne["palier"], ligne["commission"])):
            n += 1
    for ligne in conn.execute("SELECT * FROM pourboires WHERE statut = 'valide'"
                              " ORDER BY id ASC LIMIT 2000").fetchall():
        ref = "TIP-%d" % ligne["id"]
        if deja_scelle("pourboire_valide", ref):
            continue
        if sceller_ecriture("pourboire_valide", ref, ligne["net"], ligne["createur_id"],
                            "montant %g / commission %g" % (ligne["montant"], ligne["commission"])):
            n += 1
    return n


TEMPLATES["admin_audit.html"] = """{% block contenu %}
<div class="carte">
  <h1>Journal d'audit immuable</h1>
  <p class="muet">Chaque écriture financière est chaînée :
    <code>hash = SHA256(hash précédent | charge)</code>. Deux déclencheurs SQLite interdisent
    toute modification ou suppression d'une ligne, et le bouton de vérification recalcule
    l'ensemble de la chaîne depuis la graine.</p>
  <p class="row"><a class="btn" href="{{ url_for('admin_audit_verifier') }}">Vérifier la chaîne (JSON)</a>
    <a class="btn btn-sec" href="{{ url_for('admin_audit') }}">Rafraîchir</a></p>
  {% if etat.ok %}<p><b>Chaîne valide</b> — {{ etat.verifiees }}/{{ etat.total }} écritures vérifiées.</p>
  {% else %}<p><b>RUPTURE DÉTECTÉE</b> — ligne {{ etat.rupture_ligne }} (rang {{ etat.rupture_rang }}).</p>{% endif %}
</div>
<div class="carte">
  <h2>Dernières écritures scellées</h2>
  <table><tr><th>#</th><th>Type</th><th>Référence</th><th>Montant</th><th>Quand (UTC)</th><th>Hash</th></tr>
  {% for l in lignes %}<tr><td>{{ l['id'] }}</td><td>{{ l['type_ecriture'] }}</td>
    <td>{{ l['ref'] }}</td><td>{{ l['montant'] }}</td><td>{{ l['cree_le'] }}</td>
    <td class="muet">{{ l['hash_courant'][:16] }}…</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


@app.route("/admin/audit")
def admin_audit():
    refus = exiger_permission("audit_lecture")
    if refus:
        return refus
    lignes = db().execute("SELECT * FROM audit_chain ORDER BY id DESC LIMIT 60").fetchall()
    return page("admin_audit.html", titre="Journal d'audit", lignes=lignes,
                etat=verifier_chaine_audit())


@app.route("/admin/audit/verifier")
def admin_audit_verifier():
    refus = exiger_permission("audit_lecture")
    if refus:
        return refus
    return jsonify(verifier_chaine_audit())


@app.route("/admin/audit/sceller", methods=["POST"])
def admin_audit_sceller():
    refus = exiger_permission("audit_lecture")
    if refus:
        return refus
    me = utilisateur_courant()
    n = sceller_ecritures_existantes()
    journal_action("audit_scelle", details="%d écriture(s) scellée(s)" % n, utilisateur=me["id"])
    session["flash"] = "%d écriture(s) financière(s) scellée(s) dans la chaîne d'audit." % n
    return redirect(url_for("admin_audit"))


# =============================================================================
# 4) ALERTES SMS / WHATSAPP À L'ADMINISTRATEUR
# =============================================================================
_GRAVITE_MOTS_V7 = ("arnaque", "fraude", "escroquerie", "menace", "menacer", "violence",
                    "agression", "harcèlement", "harcelement", "mineur", "enfant", "suicide",
                    "détournement", "detournement", "faux numéro", "usurpation")


def _alerte_statut() -> Dict[str, Any]:
    return {
        "telephone": reglage_v7("alerte_telephone", CONFIG_V7["ALERTE_TELEPHONE"]) or CONFIG_V7["ALERTE_TELEPHONE"],
        "seuil": float(reglage_v7("alerte_seuil_fcfa", str(CONFIG_V7["ALERTE_SEUIL_FCFA"])) or 0),
        "sms_actif": reglage_v7("alerte_sms_actif", "1") == "1",
        "whatsapp_actif": reglage_v7("alerte_whatsapp_actif", "1") == "1",
        "twilio_pret": bool(CONFIG_V7["TWILIO_SID"] and CONFIG_V7["TWILIO_TOKEN"] and CONFIG_V7["TWILIO_FROM"]),
        "whatsapp_pret": bool(CONFIG_V7["WHATSAPP_TOKEN"] and CONFIG_V7["WHATSAPP_PHONE_ID"]),
        "webhook_pret": bool(CONFIG_V7["ALERTE_WEBHOOK"]),
    }


def _post_http(url: str, donnees, entetes: Optional[Dict[str, str]] = None, json_: bool = False):
    """POST sortant minimal (urllib) : aucun média, uniquement du texte/JSON."""
    if json_:
        corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        entetes = dict(entetes or {})
        entetes.setdefault("Content-Type", "application/json")
    else:
        corps = urllib.parse.urlencode(donnees).encode("utf-8")
        entetes = dict(entetes or {})
        entetes.setdefault("Content-Type", "application/x-www-form-urlencoded")
    requete = urllib.request.Request(url, data=corps, headers=entetes, method="POST")
    with urllib.request.urlopen(requete, timeout=12) as reponse:
        return reponse.read().decode("utf-8", "replace")[:500]


def _journaliser_alerte(canal: str, destinataire: str, type_alerte: str, texte: str,
                        statut: str, erreur: str = "") -> None:
    try:
        conn = db()
        conn.execute("INSERT INTO alertes_journal (canal, destinataire, type_alerte, texte,"
                     " statut, erreur, cree_le) VALUES (?,?,?,?,?,?,?)",
                     (canal, destinataire[:40], type_alerte[:60], texte[:400], statut,
                      erreur[:200], maintenant()))
        conn.commit()
    except Exception:
        pass


def alerter_admin(type_alerte: str, texte: str, montant: float = 0.0,
                  gravite: str = "normale") -> Dict[str, Any]:
    """Envoie une alerte TEXTE à l'administrateur via Twilio (SMS) ou WhatsApp Cloud API,
    ou un webhook générique. Sans clés configurées, l'alerte reste consignée en interne :
    aucun envoi simulé n'est présenté comme un envoi réel."""
    etat = _alerte_statut()
    corps = "[ToutBot Mundo] %s — %s" % (type_alerte.upper(), texte)
    resultats = []
    if not etat["telephone"]:
        _journaliser_alerte("aucun", "", type_alerte, corps, "non_configure",
                            "aucun numéro d'administrateur configuré (TOUTBOT_ALERTE_TELEPHONE)")
        return {"envoye": False, "statut": "non_configure", "resultats": resultats,
                "message": "Aucun numéro d'administrateur configuré : alerte seulement consignée "
                           "dans le journal d'alertes et le tableau de bord temps réel."}
    if etat["sms_actif"] and etat["twilio_pret"]:
        try:
            jeton = _base64.b64encode(("%s:%s" % (CONFIG_V7["TWILIO_SID"],
                                                  CONFIG_V7["TWILIO_TOKEN"])).encode("utf-8")).decode()
            url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % CONFIG_V7["TWILIO_SID"]
            _post_http(url, {"From": CONFIG_V7["TWILIO_FROM"], "To": etat["telephone"], "Body": corps},
                       {"Authorization": "Basic " + jeton})
            _journaliser_alerte("sms", etat["telephone"], type_alerte, corps, "envoye")
            resultats.append({"canal": "sms", "statut": "envoye"})
        except Exception as exc:
            _journaliser_alerte("sms", etat["telephone"], type_alerte, corps, "echec", repr(exc))
            resultats.append({"canal": "sms", "statut": "echec", "erreur": repr(exc)[:160]})
    elif etat["sms_actif"]:
        _journaliser_alerte("sms", etat["telephone"], type_alerte, corps, "non_configure",
                            "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM absents")
        resultats.append({"canal": "sms", "statut": "non_configure"})
    if etat["whatsapp_actif"] and etat["whatsapp_pret"]:
        try:
            url = "https://graph.facebook.com/v20.0/%s/messages" % CONFIG_V7["WHATSAPP_PHONE_ID"]
            _post_http(url, {"messaging_product": "whatsapp", "to": etat["telephone"],
                             "type": "text", "text": {"body": corps}},
                       {"Authorization": "Bearer " + CONFIG_V7["WHATSAPP_TOKEN"]}, json_=True)
            _journaliser_alerte("whatsapp", etat["telephone"], type_alerte, corps, "envoye")
            resultats.append({"canal": "whatsapp", "statut": "envoye"})
        except Exception as exc:
            _journaliser_alerte("whatsapp", etat["telephone"], type_alerte, corps, "echec", repr(exc))
            resultats.append({"canal": "whatsapp", "statut": "echec", "erreur": repr(exc)[:160]})
    elif etat["whatsapp_actif"]:
        _journaliser_alerte("whatsapp", etat["telephone"], type_alerte, corps, "non_configure",
                            "WHATSAPP_TOKEN / WHATSAPP_PHONE_ID absents")
        resultats.append({"canal": "whatsapp", "statut": "non_configure"})
    if etat["webhook_pret"]:
        try:
            _post_http(CONFIG_V7["ALERTE_WEBHOOK"], {"type": type_alerte, "texte": corps,
                                                    "montant": float(montant or 0),
                                                    "gravite": gravite}, json_=True)
            _journaliser_alerte("webhook", CONFIG_V7["ALERTE_WEBHOOK"][:40], type_alerte, corps,
                                "envoye")
            resultats.append({"canal": "webhook", "statut": "envoye"})
        except Exception as exc:
            _journaliser_alerte("webhook", CONFIG_V7["ALERTE_WEBHOOK"][:40], type_alerte, corps,
                                "echec", repr(exc))
            resultats.append({"canal": "webhook", "statut": "echec", "erreur": repr(exc)[:160]})
    envoyes = [r for r in resultats if r["statut"] == "envoye"]
    evenement_v6("alerte", "Alerte %s : %s" % (gravite, texte[:200]), lien="/admin/alertes",
                 montant=float(montant or 0))
    return {"envoye": bool(envoyes), "statut": "envoye" if envoyes else "non_configure",
            "resultats": resultats}


def alerter_si_seuil(user_id, montant: float, origine: str = "") -> Optional[Dict[str, Any]]:
    seuil = float(reglage_v7("alerte_seuil_fcfa", str(CONFIG_V7["ALERTE_SEUIL_FCFA"])) or 0)
    if seuil <= 0:
        return None
    en_attente = 0.0
    try:
        en_attente = float(solde(user_id)["fcfa_attente"])
    except Exception:
        en_attente = float(montant or 0)
    if float(montant or 0) < seuil and en_attente < seuil:
        return None
    texte = ("Solde à verser élevé : %s — %g %s en attente (seuil %g). Dernière écriture : %g %s."
             % (pseudo_de_v6(user_id), en_attente, CONFIG["DEVISE"], seuil, float(montant or 0),
                CONFIG["DEVISE"]))
    if origine:
        texte += " Origine : %s." % origine
    return alerter_admin("seuil_solde", texte, montant=en_attente, gravite="haute")


def alerter_plainte_grave(sujet: str, corps: str, auteur: str) -> Optional[Dict[str, Any]]:
    texte_complet = ((sujet or "") + " " + (corps or "")).lower()
    grave = [mot for mot in _GRAVITE_MOTS_V7 if mot in texte_complet]
    if not grave:
        return None
    return alerter_admin("plainte_grave",
                         "Plainte grave signalée par %s — mots repérés : %s. Sujet : %s."
                         % (auteur, ", ".join(grave[:4]), (sujet or "(sans sujet)")[:120]),
                         gravite="critique")


TEMPLATES["admin_alertes.html"] = """{% block contenu %}
<div class="carte">
  <h1>Alertes SMS / WhatsApp</h1>
  <p class="muet">Les alertes partent uniquement vers le numéro d'administrateur configuré
    (variable d'environnement <code>TOUTBOT_ALERTE_TELEPHONE</code> ou réglage ci-dessous).
    Aucun numéro n'est inventé par l'application.</p>
  <table><tr><th>Canal</th><th>État</th></tr>
    <tr><td>SMS (Twilio)</td><td>{{ 'PRÊT' if etat.twilio_pret else 'non configuré — clés TWILIO_* absentes' }}</td></tr>
    <tr><td>WhatsApp Cloud API</td><td>{{ 'PRÊT' if etat.whatsapp_pret else 'non configuré — clés WHATSAPP_* absentes' }}</td></tr>
    <tr><td>Webhook générique</td><td>{{ 'PRÊT' if etat.webhook_pret else 'non configuré — TOUTBOT_ALERTE_WEBHOOK absent' }}</td></tr>
    <tr><td>Numéro destinataire</td><td>{{ etat.telephone or 'non configuré' }}</td></tr>
  </table>
</div>
<div class="carte">
  <h2>Réglages</h2>
  <form method="post" action="{{ url_for('admin_alertes_reglages') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <p><label>Numéro d'administrateur (format international, ex. +229...)</label>
      <input name="telephone" value="{{ etat.telephone }}" style="width:260px"></p>
    <p><label>Seuil d'alerte de solde ({{ devise }})</label>
      <input name="seuil" type="number" min="0" step="1000" value="{{ etat.seuil }}" style="width:160px"></p>
    <p class="row"><label class="tuile"><input type="checkbox" name="sms" value="1" {% if etat.sms_actif %}checked{% endif %}> SMS</label>
      <label class="tuile"><input type="checkbox" name="whatsapp" value="1" {% if etat.whatsapp_actif %}checked{% endif %}> WhatsApp</label>
      <button>Enregistrer</button></p>
  </form>
  <form method="post" action="{{ url_for('admin_alertes_test') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Envoyer une alerte de test</button>
  </form>
</div>
<div class="carte">
  <h2>Journal des alertes</h2>
  <table><tr><th>Quand</th><th>Canal</th><th>Type</th><th>Statut</th><th>Texte</th></tr>
  {% for l in journal %}<tr><td>{{ l['cree_le'] }}</td><td>{{ l['canal'] }}</td>
    <td>{{ l['type_alerte'] }}</td><td>{{ l['statut'] }}</td><td class="muet">{{ l['texte'][:120] }}</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


@app.route("/admin/alertes")
def admin_alertes():
    refus = exiger_permission("alertes")
    if refus:
        return refus
    journal = db().execute("SELECT * FROM alertes_journal ORDER BY id DESC LIMIT 40").fetchall()
    return page("admin_alertes.html", titre="Alertes", etat=_alerte_statut(), journal=journal)


@app.route("/admin/alertes/reglages", methods=["POST"])
def admin_alertes_reglages():
    refus = exiger_permission("alertes")
    if refus:
        return refus
    telephone = nettoyer_telephone(request.form.get("telephone") or "")
    maj_reglage_v7("alerte_telephone", telephone)
    seuil = (request.form.get("seuil") or "0").strip()
    maj_reglage_v7("alerte_seuil_fcfa", seuil if seuil.replace(".", "", 1).isdigit() else "0")
    maj_reglage_v7("alerte_sms_actif", "1" if request.form.get("sms") else "0")
    maj_reglage_v7("alerte_whatsapp_actif", "1" if request.form.get("whatsapp") else "0")
    session["flash"] = "Réglages d'alerte enregistrés."
    return redirect(url_for("admin_alertes"))


@app.route("/admin/alertes/test", methods=["POST"])
def admin_alertes_test():
    refus = exiger_permission("alertes")
    if refus:
        return refus
    resultat = alerter_admin("test", "Alerte de test envoyée par l'administrateur.", gravite="test")
    session["flash"] = ("Alerte de test : %s. %s" % (resultat["statut"], resultat.get("message", ""))).strip()
    return redirect(url_for("admin_alertes"))



# =============================================================================
# 5) RÉSUMÉ QUOTIDIEN AUTOMATIQUE DU FIL (IA) → MESSAGE AUX ABONNÉS VOLONTAIRES
# =============================================================================
def resume_du_jour(jour: Optional[str] = None) -> Optional[Dict[str, Any]]:
    jour = jour or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        return db().execute("SELECT * FROM resumes_quotidiens WHERE jour = ?", (jour,)).fetchone()
    except Exception:
        return None


def _synthese_extractive_v7(textes: List[str], jour: str) -> str:
    """Repli sans IA : résumé honnête, fondé uniquement sur les textes réels du jour."""
    if not textes:
        return ("Résumé du %s : aucune publication n'a été enregistrée dans les dernières 24 h. "
                "Aucun contenu n'est inventé." % jour)
    extraits = []
    for texte in textes[:6]:
        phrase = re.split(r"(?<=[.!?])\s+", texte.strip())[0]
        extraits.append("• " + phrase[:160])
    mots = re.findall(r"#([A-Za-zÀ-ÿ0-9_]{2,30})", " ".join(textes))
    tendances = []
    for mot in mots:
        if mot.lower() not in [t.lower() for t in tendances]:
            tendances.append(mot)
    parties = ["Résumé automatique du %s — %d publication(s) analysée(s)." % (jour, len(textes))]
    parties.append("Principaux textes :")
    parties.extend(extraits)
    if tendances:
        parties.append("Mots-clés les plus cités : " + ", ".join("#" + t for t in tendances[:8]) + ".")
    parties.append("Synthèse établie par règles, à partir des seuls textes publiés (aucune source inventée).")
    return "\n".join(parties)


def generer_resume_du_jour(jour: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    jour = jour or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    existant = resume_du_jour(jour)
    if existant is not None and not force:
        return {"jour": jour, "texte": existant["texte"], "origine": existant["origine"],
                "nb_posts": existant["nb_posts"], "nouveau": False}
    limite = int(CONFIG_V7["RESUME_MAX_POSTS"])
    lignes = db().execute("SELECT p.corps, u.pseudo, p.cree_le FROM posts p JOIN users u ON u.id = p.auteur_id"
                          " WHERE p.cree_le >= ? ORDER BY p.id DESC LIMIT ?",
                          (jour + " 00:00:00", limite)).fetchall()
    textes = ["%s : %s" % (l["pseudo"], l["corps"]) for l in lignes]
    origine = "regles"
    texte = ""
    if textes:
        invite = ("Voici des publications réelles de la journée (auteur : texte), en français, "
                  "issues d'un réseau social 100 % textuel :\n" + "\n".join(textes)[:6000])
        try:
            reponse = interroger_llm(
                "Tu résumes un fil social 100 % textuel. Tu n'inventes AUCUN fait, aucune "
                "statistique, aucune source. Tu t'appuies uniquement sur les textes fournis. "
                "Réponds en français, 5 phrases maximum, ton neutre, sans image ni lien.",
                invite)
        except Exception:
            reponse = ""
        if reponse and len(reponse.strip()) > 40:
            texte = reponse.strip()[:4000]
            origine = "ia"
        else:
            texte = _synthese_extractive_v7(textes, jour)
    else:
        texte = _synthese_extractive_v7([], jour)
    conn = db()
    conn.execute("INSERT INTO resumes_quotidiens (jour, texte, origine, nb_posts, cree_le)"
                 " VALUES (?,?,?,?,?)"
                 " ON CONFLICT(jour) DO UPDATE SET texte = excluded.texte,"
                 " origine = excluded.origine, nb_posts = excluded.nb_posts, cree_le = excluded.cree_le",
                 (jour, texte, origine, len(textes), maintenant()))
    conn.commit()
    return {"jour": jour, "texte": texte, "origine": origine, "nb_posts": len(textes),
            "nouveau": True}


def envoyer_resume_aux_abonnes(jour: Optional[str] = None) -> Dict[str, Any]:
    """Envoie le résumé par message interne (texte) aux membres qui l'ont demandé."""
    donnees = generer_resume_du_jour(jour)
    admin = admin_par_defaut()
    if admin is None:
        return {"envoyes": 0, "raison": "aucun administrateur enregistré"}
    actifs = db().execute("SELECT user_id FROM resumes_abonnes WHERE actif = 1").fetchall()
    envoyes = 0
    for ligne in actifs:
        if int(ligne["user_id"]) == int(admin["id"]):
            continue
        try:
            envoyer_message(admin["id"], int(ligne["user_id"]),
                            "☀️ Résumé du fil — %s\n\n%s" % (donnees["jour"], donnees["texte"]))
        except Exception:
            try:
                conn = db()
                conn.execute("INSERT INTO messages (expediteur_id, destinataire_id, corps, lu, cree_le)"
                             " VALUES (?,?,?,0,?)",
                             (admin["id"], int(ligne["user_id"]),
                              ("Résumé du fil — %s\n\n%s" % (donnees["jour"], donnees["texte"]))[:4000],
                              maintenant()))
                conn.commit()
            except Exception:
                continue
        _ORIG_NOTIFIER(int(ligne["user_id"]), "resume", "Résumé quotidien du fil disponible.",
                       "/resume-quotidien")
        envoyes += 1
    journal_action("resume_quotidien_envoye", details="%s — %d destinataire(s)" % (donnees["jour"], envoyes))
    return {"envoyes": envoyes, "jour": donnees["jour"], "origine": donnees["origine"]}


TEMPLATES["resume_quotidien.html"] = """{% block contenu %}
<div class="carte">
  <h1>Résumé quotidien du fil</h1>
  <p class="muet">Synthèse établie uniquement à partir des publications textuelles réellement
    enregistrées. Sans clé d'IA configurée, un résumé par règles (extraits + mots-clés) est
    produit : aucune donnée n'est inventée.</p>
  <form method="post" action="{{ url_for('resume_quotidien_pref') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="tuile"><input type="checkbox" name="actif" value="1" {% if abonne %}checked{% endif %}>
      Recevoir le résumé quotidien par message</label>
    <button>Enregistrer</button>
  </form>
</div>
<div class="carte">
  <h2>Résumé du {{ jour }}</h2>
  {% if resume %}<p style="white-space:pre-wrap">{{ resume['texte'] }}</p>
    <p class="muet">Origine : {{ resume['origine'] }} · {{ resume['nb_posts'] }} publication(s) analysée(s).
      Ce texte a aussi été envoyé en message privé aux abonnés volontaires.</p>
  {% else %}<p class="muet">Aucun résumé encore généré pour aujourd'hui. L'administrateur peut le
    déclencher à tout moment, ou l'application le produira automatiquement après
    {{ heure }} h UTC.</p>{% endif %}
</div>
{% if derniers %}
<div class="carte"><h2>Résumés précédents</h2>
<table><tr><th>Jour</th><th>Origine</th><th>Publications</th><th>Extrait</th></tr>
{% for d in derniers %}<tr><td>{{ d['jour'] }}</td><td>{{ d['origine'] }}</td><td>{{ d['nb_posts'] }}</td>
  <td class="muet">{{ d['texte'][:140] }}…</td></tr>{% endfor %}</table></div>
{% endif %}
{% endblock %}"""


@app.route("/resume-quotidien")
def resume_quotidien_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    ligne = db().execute("SELECT actif FROM resumes_abonnes WHERE user_id = ?", (me["id"],)).fetchone()
    jour = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    derniers = db().execute("SELECT * FROM resumes_quotidiens ORDER BY jour DESC LIMIT 10").fetchall()
    return page("resume_quotidien.html", titre="Résumé quotidien", abonne=bool(ligne and ligne["actif"]),
                resume=resume_du_jour(jour), jour=jour, derniers=derniers,
                heure=CONFIG_V7["RESUME_HEURE_UTC"])


@app.route("/resume-quotidien/preference", methods=["POST"])
def resume_quotidien_pref():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    actif = 1 if request.form.get("actif") else 0
    conn = db()
    conn.execute("INSERT INTO resumes_abonnes (user_id, actif, maj_le) VALUES (?,?,?)"
                 " ON CONFLICT(user_id) DO UPDATE SET actif = excluded.actif, maj_le = excluded.maj_le",
                 (me["id"], actif, maintenant()))
    conn.commit()
    session["flash"] = ("Vous recevrez le résumé quotidien par message." if actif
                        else "Vous ne recevrez plus le résumé quotidien.")
    return redirect(url_for("resume_quotidien_page"))


@app.route("/admin/resume/declencher", methods=["POST"])
def admin_resume_declencher():
    refus = exiger_permission("audit_lecture")
    if refus:
        return refus
    resultat = envoyer_resume_aux_abonnes()
    session["flash"] = "Résumé du %s envoyé à %d membre(s) (origine : %s)." % (
        resultat.get("jour", "?"), resultat.get("envoyes", 0), resultat.get("origine", "?"))
    return redirect(url_for("admin_temps_reel"))


@app.before_request
def _v7_resume_auto():
    """Génère le résumé du jour une seule fois après l'heure configurée (UTC)."""
    if request.method != "GET" or not request.path.startswith("/admin"):
        return None
    if reglage_v7("resume_quotidien_auto", "1") != "1":
        return None
    horodatage = _dt.datetime.now(_dt.timezone.utc)
    if horodatage.hour < int(CONFIG_V7["RESUME_HEURE_UTC"]):
        return None
    jour = horodatage.strftime("%Y-%m-%d")
    if _RESUME_V7["jour"] == jour:
        return None
    _RESUME_V7["jour"] = jour
    try:
        envoyer_resume_aux_abonnes(jour)
    except Exception:
        LOGGER.warning("résumé quotidien impossible", exc_info=False)
    return None


_RESUME_V7: Dict[str, str] = {"jour": ""}


# =============================================================================
# 6) ANTI-SPAM / MULTI-COMPTES — EMPREINTE COMPORTEMENTALE + TÉLÉPHONE
# =============================================================================
def _h(value) -> str:
    return hashlib.sha256(("toutbot-v7|" + str(value or "")).encode("utf-8")).hexdigest()


def enregistrer_empreinte(user_id: int) -> None:
    """Empreinte de connexion : numéro (haché), IP (hachée), agent (haché).
    Un même IP+agent partagé par plusieurs comptes = signal de multi-comptes."""
    cle = int(user_id)
    horodatage = time.time()
    if horodatage - _EMPREINTE_V7.get(cle, 0.0) < 600:
        return
    _EMPREINTE_V7[cle] = horodatage
    u = utilisateur_par_id(user_id)
    try:
        conn = db()
        conn.execute("INSERT INTO empreintes_connexion (user_id, tel_hash, ip_hash, ua_hash, cree_le)"
                     " VALUES (?,?,?,?,?)",
                     (user_id, _h(u["telephone"] if u else ""), _h(request.remote_addr or ""),
                      _h(request.user_agent.string if request.user_agent else ""), maintenant()))
        conn.commit()
    except Exception:
        pass


_EMPREINTE_V7: Dict[int, float] = {}


def detecter_spam(user_id: int, journaliser: bool = True) -> Dict[str, Any]:
    """Score 0-100 : cadence de publication, doublons, cadence de messages, compte neuf,
    partage d'empreinte (IP + agent) avec d'autres comptes, préfixe téléphonique répété."""
    conn = db()
    u = utilisateur_par_id(user_id)
    if u is None:
        return {"score": 0, "niveau": "inconnu", "signaux": [], "recommandation": "compte inconnu"}
    seuil_24h = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    posts_24h = int(conn.execute("SELECT COUNT(*) n FROM posts WHERE auteur_id = ? AND cree_le >= ?",
                                 (user_id, seuil_24h)).fetchone()["n"])
    msgs_24h = int(conn.execute("SELECT COUNT(*) n FROM messages WHERE expediteur_id = ? AND cree_le >= ?",
                                (user_id, seuil_24h)).fetchone()["n"])
    doublons = int(conn.execute("SELECT COUNT(*) n FROM (SELECT corps, COUNT(*) c FROM posts"
                                " WHERE auteur_id = ? GROUP BY corps HAVING c > 3)", (user_id,)).fetchone()["n"])
    jours_compte = 0
    try:
        cree = _dt.datetime.strptime(str(u["cree_le"]), "%Y-%m-%d %H:%M:%S")
        jours_compte = (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - cree).days
    except Exception:
        jours_compte = 0
    empreinte = conn.execute("SELECT ip_hash, ua_hash FROM empreintes_connexion WHERE user_id = ?"
                             " ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    partages = 0
    if empreinte is not None and empreinte["ip_hash"]:
        partages = int(conn.execute("SELECT COUNT(DISTINCT user_id) n FROM empreintes_connexion"
                                    " WHERE ip_hash = ? AND ua_hash = ? AND user_id <> ?",
                                    (empreinte["ip_hash"], empreinte["ua_hash"], user_id)).fetchone()["n"])
    prefixe = ""
    if u["telephone"]:
        prefixe = str(u["telephone"])[:4]
    meme_prefixe = 0
    if prefixe:
        meme_prefixe = int(conn.execute("SELECT COUNT(*) n FROM users WHERE telephone LIKE ? AND id <> ?",
                                        (prefixe + "%", user_id)).fetchone()["n"])
    signaux = []
    score = 0
    if posts_24h > 20:
        score += 25
        signaux.append("Cadence de publication anormale : %d publications en 24 h" % posts_24h)
    elif posts_24h > 10:
        score += 10
        signaux.append("Cadence élevée : %d publications en 24 h" % posts_24h)
    if msgs_24h > 60:
        score += 20
        signaux.append("Cadence de messages anormale : %d messages en 24 h" % msgs_24h)
    if doublons:
        score += 20
        signaux.append("Textes identiques répétés (%d groupe(s) de plus de 3 fois)" % doublons)
    if jours_compte <= 1 and posts_24h > 5:
        score += 15
        signaux.append("Compte très récent (≤ 1 jour) avec forte activité")
    if partages:
        score += 30
        signaux.append("Empreinte partagée (IP + agent) avec %d autre(s) compte(s) — multi-comptes probable" % partages)
    if meme_prefixe >= 3 and jours_compte <= 7:
        score += 10
        signaux.append("Préfixe téléphonique répété avec %d compte(s) récents" % meme_prefixe)
    score = int(max(0, min(100, score)))
    niveau = ("critique" if score >= 80 else "élevé" if score >= 60 else
              "modéré" if score >= 30 else "faible")
    recommandation = ("Sanction recommandée : lecture seule + examen des comptes liés."
                      if score >= 60 else
                      "Surveillance : consulter les textes récents." if score >= 30 else
                      "Aucune action nécessaire.")
    resultat = {"score": score, "niveau": niveau, "signaux": signaux, "recommandation": recommandation,
                "posts_24h": posts_24h, "messages_24h": msgs_24h, "comptes_lies": partages,
                "jours_compte": jours_compte}
    if journaliser and score >= 30:
        try:
            conn.execute("INSERT INTO spam_signaux (user_id, type, score, details, cree_le)"
                         " VALUES (?,?,?,?,?)",
                         (user_id, niveau, score, " | ".join(signaux)[:400], maintenant()))
            conn.commit()
        except Exception:
            pass
        seuil = int(reglage_v7("spam_seuil_alerte", "60") or 60)
        if score >= seuil:
            alerter_admin("anti_spam", "Compte suspect (%d/100) : %s — %s"
                          % (score, u["pseudo"], " ; ".join(signaux[:2])), gravite="haute")
    return resultat


TEMPLATES["admin_spam.html"] = """{% block contenu %}
<div class="carte">
  <h1>Anti-spam &amp; multi-comptes</h1>
  <p class="muet">Détection par empreinte comportementale (cadence, doublons) et par numéro de
    téléphone (haché) + IP/agent (hachés) : les valeurs brutes ne sont jamais conservées en clair.</p>
  <form method="post" action="{{ url_for('admin_spam_balayer') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button>Balayer maintenant</button>
  </form>
</div>
<div class="carte">
  <h2>Comptes à surveiller</h2>
  <table><tr><th>Membre</th><th>Score</th><th>Niveau</th><th>Signaux</th><th>Recommandation</th></tr>
  {% for l in suspects %}<tr><td><a href="{{ url_for('profil', pseudo=l['pseudo']) }}">{{ l['pseudo'] }}</a></td>
    <td>{{ l['score'] }}</td><td>{{ l['niveau'] }}</td><td class="muet">{{ l['signaux'] }}</td>
    <td class="muet">{{ l['recommandation'] }}</td></tr>{% endfor %}</table>
</div>
<div class="carte">
  <h2>Signaux journalisés</h2>
  <table><tr><th>Quand</th><th>Membre</th><th>Niveau</th><th>Score</th><th>Détail</th></tr>
  {% for l in signaux %}<tr><td>{{ l['cree_le'] }}</td><td>{{ l['pseudo'] }}</td><td>{{ l['type'] }}</td>
    <td>{{ l['score'] }}</td><td class="muet">{{ l['details'][:140] }}</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


@app.route("/admin/anti-spam")
def admin_spam():
    refus = exiger_permission("anti_spam")
    if refus:
        return refus
    suspects = []
    for u in db().execute("SELECT id, pseudo FROM users ORDER BY id ASC LIMIT 300").fetchall():
        etat = detecter_spam(u["id"], journaliser=False)
        if etat["score"] >= 30:
            suspects.append({"pseudo": u["pseudo"], "score": etat["score"], "niveau": etat["niveau"],
                             "signaux": " ; ".join(etat["signaux"])[:200],
                             "recommandation": etat["recommandation"]})
    suspects.sort(key=lambda x: -x["score"])
    signaux = db().execute("SELECT s.*, u.pseudo FROM spam_signaux s JOIN users u ON u.id = s.user_id"
                           " ORDER BY s.id DESC LIMIT 40").fetchall()
    return page("admin_spam.html", titre="Anti-spam", suspects=suspects[:30], signaux=signaux)


@app.route("/admin/anti-spam/balayer", methods=["POST"])
def admin_spam_balayer():
    refus = exiger_permission("anti_spam")
    if refus:
        return refus
    n = 0
    for u in db().execute("SELECT id FROM users ORDER BY id ASC LIMIT 300").fetchall():
        etat = detecter_spam(u["id"])
        if etat["score"] >= 30:
            n += 1
    session["flash"] = "Balayage terminé : %d compte(s) au-dessus du seuil de surveillance." % n
    return redirect(url_for("admin_spam"))


# =============================================================================
# 7) CHAT IA MULTILINGUE (FRANÇAIS, FON, BAOULÉ) — SOURCES RÉELLES UNIQUEMENT
# =============================================================================
LANGUES_V7: Dict[str, Dict[str, Any]] = {
    "fr": {"libelle": "Français", "code_recherche": "fr-FR",
           "note": "Chat complet : recherche temps réel + IA ancrée sur les résultats réels."},
    "fon": {"libelle": "Fon (fɔngbè)", "code_recherche": "fr-FR",
            "note": "Couverture LIMITÉE et assumée : les grands modèles n'ont pas de fon "
                    "fiable. Réponses en fon construites sur un lexique interne vérifié, "
                    "et résultats bruts renvoyés dans leur langue d'origine."},
    "baoule": {"libelle": "Baoulé (bawulɛ)", "code_recherche": "fr-FR",
               "note": "Couverture LIMITÉE et assumée : aucune prise en charge native fiable "
                       "par les modèles actuels. Réponses par lexique interne + résultats "
                       "sources affichés tels quels."},
}

LEXIQUE_V7: Dict[str, Dict[str, str]] = {
    "fon": {
        "salutation": "Kudo ! (bonjour)",
        "attente": "Mi ɖo te… (un instant)",
        "aucun_resultat": "Nyɛ mɛ ɖé ǎ : mi mɔ nǔ ɖé ǎ. (Je ne trouve rien de publié.)",
        "reponse_fixe": "Nǔ e ɖo wema mɛ ɔ wɛ nyɛ tuùn. (Je ne rapporte que ce qui est publié.)",
        "sources": "Nǔ e nyɛ mɔ ɖé lɛ : (sources trouvées :)",
    },
    "baoule": {
        "salutation": "Akwaba ! (bonjour)",
        "attente": "Kpanngban… (un instant)",
        "aucun_resultat": "N'yan mun kwla yo. (Je ne trouve rien de publié.)",
        "reponse_fixe": "Min be nyan mun kwla, ɔ min yoli nun. (Je ne rapporte que ce qui est publié.)",
        "sources": "Nan mun min yoli lɔ : (sources trouvées :)",
    },
}


def reponse_multilingue(question: str, langue: str = "fr") -> Dict[str, Any]:
    """Chat ancré : on interroge d'abord les moteurs réels, puis on formule dans la langue
    demandée. En fon et baoulé, la formulation est faite par lexique interne : le contenu
    factuel reste celui des sources, jamais une invention du modèle."""
    langue = (langue or "fr").lower()
    if langue not in LANGUES_V7:
        langue = "fr"
    langue = str(langue)
    question = (question or "").strip()[:500]
    if not question:
        return {"langue": langue, "reponse": "", "source": "aucune", "resultats": [],
                "note": LANGUES_V7[langue]["note"]}
    try:
        donnees = MOTEUR.chercher(question)
    except Exception:
        donnees = {"resultats": []}
    resultats = donnees.get("resultats", []) if isinstance(donnees, dict) else []
    lexique = LEXIQUE_V7.get(langue, {})
    if langue == "fr":
        bloc = formater_resultats_html(donnees) if isinstance(donnees, dict) else ""
        try:
            texte = interroger_ia(question, bloc)
        except Exception:
            texte = ""
        if not texte:
            texte = reponse_regle(question)
        origine = "ia" if texte else "regles"
        if not resultats:
            texte = ("Je ne trouve rien de publié sur cette question pour le moment : "
                     "aucun résultat réel n'est remonté par les moteurs interrogés.")
            origine = "regles"
        return {"langue": langue, "reponse": texte, "source": origine,
                "resultats": [dict(r) for r in resultats[:8]], "note": LANGUES_V7[langue]["note"]}
    # Langues locales : lexique interne, aucune invention factuelle.
    if not resultats:
        reponse = "%s\n%s" % (lexique.get("salutation", ""), lexique.get("aucun_resultat", ""))
        return {"langue": langue, "reponse": reponse.strip(), "source": "lexique_local",
                "resultats": [], "note": LANGUES_V7[langue]["note"]}
    lignes = [lexique.get("salutation", ""), lexique.get("sources", "")]
    for r in resultats[:6]:
        lignes.append("- %s — %s" % (r.get("titre", ""), r.get("url", "")))
    lignes.append(lexique.get("reponse_fixe", ""))
    lignes.append("Note : la formulation en %s vient d'un lexique interne ; les titres et liens "
                  "ci-dessus sont les sources réelles, non traduites."
                  % LANGUES_V7[langue]["libelle"])
    return {"langue": langue, "reponse": "\n".join(l for l in lignes if l).strip(),
            "source": "lexique_local", "resultats": [dict(r) for r in resultats[:6]],
            "note": LANGUES_V7[langue]["note"]}


TEMPLATES["chat_multilingue.html"] = """{% block contenu %}
<div class="carte">
  <h1>Chat IA multilingue</h1>
  <p class="muet">Les réponses sont ancrées sur les résultats RÉELS des moteurs interrogés.
    L'application répond « je ne trouve rien de publié » plutôt que d'inventer.</p>
  <table><tr><th>Langue</th><th>Prise en charge</th></tr>
  {% for code, l in langues.items() %}<tr><td>{{ l['libelle'] }}</td><td class="muet">{{ l['note'] }}</td></tr>{% endfor %}
  </table>
</div>
<div class="carte">
  <h2>Poser une question</h2>
  <form method="post" action="{{ url_for('chat_multilingue') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="question" placeholder="Votre question…" required style="width:340px">
    <select name="langue" style="width:auto">{% for code, l in langues.items() %}
      <option value="{{ code }}" {% if code == langue %}selected{% endif %}>{{ l['libelle'] }}</option>{% endfor %}</select>
    <button>Demander</button>
  </form>
</div>
{% if echange %}
<div class="carte"><h2>Réponse ({{ echange['langue'] }} — source : {{ echange['source'] }})</h2>
  <p style="white-space:pre-wrap">{{ echange['reponse'] }}</p>
  <p class="muet">{{ echange['note'] }}</p>
  {% if echange['resultats'] %}<h3>Sources réelles</h3>
  <ul>{% for r in echange['resultats'] %}<li>{{ r['titre'] }} — <a href="{{ r['url'] }}">{{ r['url'] }}</a></li>{% endfor %}</ul>{% endif %}
</div>
{% endif %}
{% endblock %}"""


@app.route("/chat-multilingue", methods=["GET", "POST"])
def chat_multilingue():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    echange = None
    langue = (request.form.get("langue") or request.args.get("langue") or "fr").lower()
    if request.method == "POST":
        question = (request.form.get("question") or "").strip()
        if question:
            echange = reponse_multilingue(question, langue)
            journal_action("chat_multilingue", details="langue %s" % langue)
    return page("chat_multilingue.html", titre="Chat multilingue", langues=LANGUES_V7,
                echange=echange, langue=langue)


@app.route("/api/chat-multilingue", methods=["POST"])
def api_chat_multilingue():
    me = utilisateur_courant()
    if me is None:
        return jsonify(ok=False, erreur="connexion_requise"), 401
    charge = request.get_json(silent=True) or {}
    question = str(charge.get("question") or "").strip()
    langue = str(charge.get("langue") or "fr").lower()
    if not question:
        return jsonify(ok=False, erreur="question_vide"), 400
    echange = reponse_multilingue(question, langue)
    return jsonify(ok=True, **echange)


# =============================================================================
# 8) RAPPORTS COMPTABLES NARRATIFS (IA) POUR L'ADMINISTRATEUR
# =============================================================================
def donnees_mois_v7(mois: str) -> Dict[str, Any]:
    conn = db()
    debut = mois + "-01 00:00:00"
    suivante = (_dt.datetime.strptime(mois + "-01", "%Y-%m-%d").replace(day=28)
                + _dt.timedelta(days=8)).strftime("%Y-%m-01")
    fin = suivante + " 00:00:00"
    abos = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(palier),0) brut, COALESCE(SUM(commission),0) com,"
                        " COALESCE(SUM(net),0) net FROM abonnements WHERE statut = 'valide'"
                        " AND cree_le >= ? AND cree_le < ?", (debut, fin)).fetchone()
    tips = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(montant),0) brut, COALESCE(SUM(commission),0) com,"
                        " COALESCE(SUM(net),0) net FROM pourboires WHERE statut = 'valide'"
                        " AND cree_le >= ? AND cree_le < ?", (debut, fin)).fetchone()
    impayes = conn.execute("SELECT COUNT(*) n FROM abonnements WHERE statut = 'en_attente'"
                           " AND cree_le >= ? AND cree_le < ?", (debut, fin)).fetchone()["n"]
    versements = conn.execute("SELECT COALESCE(SUM(fcfa),0) s FROM paiements WHERE cree_le >= ?"
                              " AND cree_le < ?", (debut, fin)).fetchone()["s"]
    top = conn.execute("SELECT u.pseudo, COALESCE(SUM(a.net),0) net FROM abonnements a"
                       " JOIN users u ON u.id = a.createur_id WHERE a.statut = 'valide'"
                       " AND a.cree_le >= ? AND a.cree_le < ? GROUP BY a.createur_id"
                       " ORDER BY net DESC LIMIT 3", (debut, fin)).fetchall()
    fonds = conn.execute("SELECT COALESCE(SUM(CASE WHEN sens = 'credit' THEN montant_fcfa"
                          " ELSE -montant_fcfa END),0) s FROM fonds_garantie WHERE cree_le >= ?"
                          " AND cree_le < ?", (debut, fin)).fetchone()["s"]
    nb_transactions = int(abos["n"]) + int(tips["n"])
    brut = round(float(abos["brut"]) + float(tips["brut"]), 2)
    commissions = round(float(abos["com"]) + float(tips["com"]), 2)
    net = round(float(abos["net"]) + float(tips["net"]), 2)
    return {"mois": mois, "abonnements": int(abos["n"]), "pourboires": int(tips["n"]),
            "transactions": nb_transactions, "brut": brut, "commissions": commissions, "net": net,
            "net_regle": net_mois(brut, nb_transactions), "impayes": int(impayes),
            "versements": round(float(versements), 2), "fonds_garantie": round(float(fonds), 2),
            "top_createurs": [{"pseudo": t["pseudo"], "net": round(float(t["net"]), 2)} for t in top]}


# CORRECTION V8b : l'ancien fichier réassignait « _ORIG_MARQUER_PAYE = marquer_paye »
# ici — ce qui faisait pointer la sauvegarde vers le wrapper V6 lui-même et provoquait
# une récursion infinie (RecursionError) à chaque « Marquer payé ». On supprime cette
# réassignation : _ORIG_MARQUER_PAYE garde la fonction d'origine (assignée en V6).
def generer_rapport_narratif(mois: Optional[str] = None, enregistrer: bool = True) -> Dict[str, Any]:
    mois = mois or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")
    d = donnees_mois_v7(mois)
    net_regle = d["net_regle"]
    if not d["transactions"]:
        texte = ("Rapport de %s : aucune transaction validée n'a été enregistrée ce mois-ci. "
                 "Le grand livre ne présente donc ni recette de commission ni net à verser. "
                 "Aucun chiffre n'est estimé ni extrapolé." % mois)
        origine = "regles"
    else:
        faits = ("Mois %s. Transactions validées : %d. Total brut encaissé : %g %s. "
                 "Commissions : %g %s. Net dû aux créateurs (calcul réglementaire de "
                 "l'application) : %g %s. Écritures encore en attente : %d. "
                 "Versements déjà exécutés : %g %s. Fonds de garantie alimenté : %g %s. "
                 "Principaux créateurs : %s."
                 % (mois, d["transactions"], d["brut"], CONFIG["DEVISE"], d["commissions"],
                    CONFIG["DEVISE"], net_regle["net"], CONFIG["DEVISE"], d["impayes"],
                    d["versements"], CONFIG["DEVISE"], d["fonds_garantie"], CONFIG["DEVISE"],
                    ", ".join("%s (%g %s)" % (t["pseudo"], t["net"], CONFIG["DEVISE"])
                              for t in d["top_createurs"]) or "aucun"))
        reponse = ""
        try:
            reponse = interroger_llm(
                "Tu es comptable. Tu rédiges une note narrative en français, 6 phrases maximum, "
                "à partir des CHIFFRES FOURNIS uniquement. Tu ne calcules rien de nouveau, tu "
                "n'inventes aucun chiffre, aucune tendance non présente dans les données.",
                faits)
        except Exception:
            reponse = ""
        if reponse and len(reponse.strip()) > 60:
            texte = reponse.strip()[:4000]
            origine = "ia"
        else:
            texte = ("Note du mois %s, en langage clair.\n\n"
                     "Ce mois-ci, %d transactions ont été validées, pour un encaissement brut de "
                     "%g %s. Après la commission de l'application (%g %s), le net dû aux créateurs "
                     "s'élève à %g %s. %d écriture(s) restent en attente de validation : elles ne "
                     "sont pas comptées dans les recettes tant qu'elles ne sont pas validées. "
                     "%g %s ont déjà été versés aux créateurs. Le fonds de garantie a reçu %g %s. "
                     "Les créateurs les plus rémunérés sont : %s.\n\n"
                     "Synthèse produite par règles, directement à partir du grand livre."
                     % (mois, d["transactions"], d["brut"], CONFIG["DEVISE"], d["commissions"],
                        CONFIG["DEVISE"], net_regle["net"], CONFIG["DEVISE"], d["impayes"],
                        d["versements"], CONFIG["DEVISE"], d["fonds_garantie"], CONFIG["DEVISE"],
                        ", ".join(t["pseudo"] for t in d["top_createurs"]) or "aucun"))
            origine = "regles"
    if enregistrer:
        try:
            conn = db()
            conn.execute("INSERT INTO reports_narratifs (mois, texte, origine, cree_le)"
                         " VALUES (?,?,?,?)", (mois, texte, origine, maintenant()))
            conn.commit()
        except Exception:
            pass
    return {"mois": mois, "texte": texte, "origine": origine, "donnees": d}


TEMPLATES["admin_rapport.html"] = """{% block contenu %}
<div class="carte">
  <h1>Rapport comptable narratif</h1>
  <p class="muet">Le texte est rédigé à partir des seuls chiffres du grand livre. Sans clé d'IA,
    la note est produite par règles, avec les mêmes chiffres réels.</p>
  <form method="get" action="{{ url_for('admin_rapport') }}" class="row">
    <input name="mois" value="{{ mois }}" placeholder="AAAA-MM" style="width:140px"><button>Générer</button>
  </form>
  {% if rapport %}<p class="muet">Origine : {{ rapport['origine'] }}</p>
  <p style="white-space:pre-wrap">{{ rapport['texte'] }}</p>{% endif %}
</div>
<div class="carte">
  <h2>Chiffres du mois {{ mois }} ({{ devise }})</h2>
  <table>
    <tr><th>Transactions validées</th><td>{{ rapport['donnees']['transactions'] }}</td></tr>
    <tr><th>Total brut</th><td>{{ rapport['donnees']['brut'] }}</td></tr>
    <tr><th>Commissions</th><td>{{ rapport['donnees']['commissions'] }}</td></tr>
    <tr><th>Net dû aux créateurs</th><td>{{ rapport['donnees']['net_regle']['net'] }}</td></tr>
    <tr><th>Écritures en attente</th><td>{{ rapport['donnees']['impayes'] }}</td></tr>
    <tr><th>Versements exécutés</th><td>{{ rapport['donnees']['versements'] }}</td></tr>
    <tr><th>Fonds de garantie alimenté</th><td>{{ rapport['donnees']['fonds_garantie'] }}</td></tr>
  </table>
</div>
<div class="carte"><h2>Rapports enregistrés</h2>
<table><tr><th>Mois</th><th>Origine</th><th>Extrait</th></tr>
{% for r in historique %}<tr><td>{{ r['mois'] }}</td><td>{{ r['origine'] }}</td>
  <td class="muet">{{ r['texte'][:160] }}…</td></tr>{% endfor %}</table></div>
{% endblock %}"""


@app.route("/admin/rapport-narratif")
def admin_rapport():
    refus = exiger_permission("comptabilite")
    if refus:
        return refus
    mois = (request.args.get("mois") or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")).strip()
    if len(mois) != 7 or mois[4] != "-":
        mois = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")
    rapport = generer_rapport_narratif(mois)
    historique = db().execute("SELECT * FROM reports_narratifs ORDER BY id DESC LIMIT 12").fetchall()
    return page("admin_rapport.html", titre="Rapport narratif", mois=mois, rapport=rapport,
                historique=historique)



# =============================================================================
# 9) MODE HORS-LIGNE AVANCÉ : 50 DERNIÈRES PUBLICATIONS + RÉDACTION DIFFÉRÉE
# =============================================================================
def flux_hors_ligne() -> Dict[str, Any]:
    """Photographie TEXTE des dernières publications, destinée au cache local."""
    limite = int(CONFIG_V7["HORS_LIGNE_POSTS"])
    lignes = db().execute("SELECT p.id, p.corps, p.cree_le, u.pseudo FROM posts p"
                          " JOIN users u ON u.id = p.auteur_id ORDER BY p.id DESC LIMIT ?",
                          (limite,)).fetchall()
    return {"genere_le": maintenant(), "limite": limite,
            "publications": [{"id": l["id"], "pseudo": l["pseudo"], "corps": l["corps"],
                              "cree_le": l["cree_le"]} for l in lignes],
            "regle": "Texte uniquement : aucune image, aucune vidéo, aucun fichier n'est mis en cache."}


@app.route("/api/hors-ligne/flux")
def api_hors_ligne_flux():
    return jsonify(ok=True, **flux_hors_ligne())


@app.route("/api/hors-ligne/brouillon", methods=["POST"])
def api_hors_ligne_brouillon():
    """Enregistre côté serveur un texte rédigé hors-ligne, à publier dès reconnexion."""
    me = utilisateur_courant()
    if me is None:
        return jsonify(ok=False, erreur="connexion_requise"), 401
    charge = request.get_json(silent=True) or {}
    corps = str(charge.get("corps") or "").strip()
    if not corps:
        return jsonify(ok=False, erreur="texte_vide"), 400
    if contient_media(corps):
        return jsonify(ok=False, erreur="media_interdit",
                       message="ToutBot Mundo est 100 % textuel : aucun média, même hors-ligne."), 400
    conn = db()
    curseur = conn.execute("INSERT INTO publications_differees (user_id, corps, statut, cree_le)"
                           " VALUES (?,?,?,?)", (me["id"], corps[:5000], "en_attente", maintenant()))
    conn.commit()
    return jsonify(ok=True, id=curseur.lastrowid, statut="en_attente")


@app.route("/api/hors-ligne/publier", methods=["POST"])
def api_hors_ligne_publier():
    """Publie un lot de textes mis en file d'attente hors-ligne (le garde-fou texte et la
    modération automatique s'appliquent exactement comme pour /publier)."""
    me = utilisateur_courant()
    if me is None:
        return jsonify(ok=False, erreur="connexion_requise"), 401
    etat = sanction_en_cours(me)
    if etat["actif"]:
        return jsonify(ok=False, erreur="lecture_seule", message="Vous êtes en lecture seule."), 403
    charge = request.get_json(silent=True) or {}
    textes = charge.get("posts")
    if not isinstance(textes, list):
        textes = []
    if not textes and charge.get("corps"):
        textes = [charge.get("corps")]
    resultats = []
    conn = db()
    for element in textes[:50]:
        corps = element.get("corps") if isinstance(element, dict) else str(element)
        corps = (corps or "").strip()
        if not corps:
            resultats.append({"ok": False, "motif": "texte vide"})
            continue
        faute = contient_media(corps)
        if faute:
            resultats.append({"ok": False, "motif": "média interdit : « %s »" % faute})
            continue
        moderation = appliquer_moderation(corps[:5000], me["id"], "publication différée")
        moderation = moderation if isinstance(moderation, dict) else {"texte": corps[:5000]}
        conn.execute("INSERT INTO posts (auteur_id, corps, cree_le) VALUES (?,?,?)",
                     (me["id"], moderation.get("texte", corps[:5000]), maintenant()))
        conn.commit()
        resultats.append({"ok": True, "corps": moderation.get("texte", corps[:5000])})
    publies = sum(1 for r in resultats if r["ok"])
    conn.execute("UPDATE publications_differees SET statut = 'publie', publie_le = ?"
                 " WHERE user_id = ? AND statut = 'en_attente'", (maintenant(), me["id"]))
    conn.commit()
    if publies:
        journal_action("publication_differee", details="%d texte(s) publié(s) après reconnexion" % publies)
    return jsonify(ok=True, publies=publies, resultats=resultats)


TEMPLATES["hors_ligne.html"] = """{% block contenu %}
<div class="carte">
  <h1>Mode hors-ligne avancé</h1>
  <p class="muet">Les {{ limite }} dernières publications textuelles sont conservées dans le
    cache local du navigateur. Un texte rédigé sans réseau est mis en file d'attente, puis publié
    automatiquement dès la reconnexion — toujours contrôlé par le garde-fou « 100 % texte » et la
    modération côté serveur.</p>
  <p class="muet">État actuel : <b id="hl-etat">calcul en cours…</b></p>
  <p class="row"><button class="btn-sec" type="button" onclick="toutbotMajFlux()">Mettre le cache à jour</button>
    <button class="btn-sec" type="button" onclick="toutbotViderFile()">Publier la file d'attente</button></p>
</div>
<div class="carte">
  <h2>Rédiger hors-ligne</h2>
  <textarea id="hl-texte" maxlength="5000" placeholder="Votre texte (aucun média)"></textarea>
  <p class="row"><button type="button" onclick="toutbotEnfiler()">Mettre en file d'attente</button>
    <span class="muet">La file est locale ; elle se vide toute seule au retour du réseau.</span></p>
</div>
<div class="carte"><h2>Brouillons côté serveur</h2>
<table><tr><th>Quand</th><th>Statut</th><th>Texte</th></tr>
{% for b in brouillons %}<tr><td>{{ b['cree_le'] }}</td><td>{{ b['statut'] }}</td>
  <td class="muet">{{ b['corps'][:160] }}</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


@app.route("/hors-ligne")
def hors_ligne_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    brouillons = db().execute("SELECT * FROM publications_differees WHERE user_id = ?"
                              " ORDER BY id DESC LIMIT 20", (me["id"],)).fetchall()
    return page("hors_ligne.html", titre="Hors-ligne", brouillons=brouillons,
                limite=CONFIG_V7["HORS_LIGNE_POSTS"])


_SW_JS_V7 = """
/* ---- V7 : app-shell + cache des 50 dernières publications (texte seulement) ---- */
var TOUTBOT_V7_CACHE = 'toutbot-v7-texte';
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function(e){
  var url = e.request.url;
  if(url.indexOf('/api/hors-ligne/flux') !== -1){
    e.respondWith(fetch(e.request).then(function(r){
      var copie = r.clone();
      caches.open(TOUTBOT_V7_CACHE).then(function(c){ c.put('/api/hors-ligne/flux', copie); });
      return r;
    }).catch(function(){ return caches.match('/api/hors-ligne/flux'); }));
  }
});
"""

try:
    SW_JS = SW_JS + _SW_JS_V7
except Exception:
    LOGGER.warning("service worker V7 non complété", exc_info=False)


_V7_JS = """
<script>
/* ================= V7 — hors-ligne, TTS, push, thèmes : 100 % TEXTE ================= */
(function(){
  var CLE_FLUX = 'toutbot_v7_flux', CLE_FILE = 'toutbot_v7_file';
  function lire(k, d){ try { return JSON.parse(localStorage.getItem(k)) || d; } catch(e){ return d; } }
  function ecrire(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} }
  function file(){ return lire(CLE_FILE, []); }
  function etat(){
    var el = document.getElementById('hl-etat'); if(!el) return;
    var f = file(), c = lire(CLE_FLUX, null);
    el.textContent = (navigator.onLine ? 'en ligne' : 'hors ligne')
      + ' — ' + (c && c.publications ? c.publications.length : 0) + ' publication(s) en cache'
      + ' — ' + f.length + ' texte(s) en file d\'attente'
      + (c && c.genere_le ? ' (cache du ' + c.genere_le + ' UTC)' : '');
  }
  window.toutbotMajFlux = function(){
    return fetch('/api/hors-ligne/flux', { headers: { 'Accept': 'application/json' } })
      .then(function(r){ return r.json(); }).then(function(d){ ecrire(CLE_FLUX, d); etat(); })
      .catch(function(){ etat(); });
  };
  window.toutbotEnfiler = function(){
    var zone = document.getElementById('hl-texte');
    if(!zone || !zone.value.trim()) return;
    var f = file(); f.push({ corps: zone.value.trim(), cree_le: new Date().toISOString() });
    ecrire(CLE_FILE, f); zone.value = ''; etat();
    fetch('/api/hors-ligne/brouillon', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ corps: f[f.length - 1].corps }) }).catch(function(){});
  };
  window.toutbotViderFile = function(){
    var f = file(); if(!f.length) return Promise.resolve();
    if(!navigator.onLine) return Promise.resolve();
    return fetch('/api/hors-ligne/publier', { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ posts: f }) })
      .then(function(r){ return r.json(); }).then(function(d){
        var reste = []; (d.resultats || []).forEach(function(x, i){ if(!x.ok) reste.push(f[i]); });
        ecrire(CLE_FILE, reste); etat();
      }).catch(function(){});
  };
  window.addEventListener('online', function(){ window.toutbotViderFile(); window.toutbotMajFlux(); });
  document.addEventListener('DOMContentLoaded', function(){
    etat();
    if('serviceWorker' in navigator){
      navigator.serviceWorker.register('/sw.js').catch(function(){});
    }
    if(navigator.onLine){ window.toutbotMajFlux(); window.toutbotViderFile(); }
  });
})();
/* ---- Lecteur vocal (TTS) : synthèse du navigateur, AUCUN audio produit ni stocké ---- */
function toutbotDire(texte, langue, vitesse){
  if(!('speechSynthesis' in window)){ alert('La synthèse vocale n\\'est pas disponible dans ce navigateur.'); return; }
  try{
    window.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(texte || '');
    u.lang = langue || 'fr-FR'; u.rate = vitesse || 1;
    window.speechSynthesis.speak(u);
  }catch(e){}
}
document.addEventListener('click', function(ev){
  var cible = ev.target.closest ? ev.target.closest('[data-tts]') : null;
  if(!cible) return;
  var carte = cible.closest('.carte');
  var zone = carte ? carte.querySelector('.selec') : null;
  var texte = zone ? zone.textContent : '';
  toutbotDire(texte, cible.getAttribute('data-tts-langue') || 'fr-FR',
              parseFloat(cible.getAttribute('data-tts-vitesse') || '1'));
});
/* ---- Notifications push (PWA) : charge utile TEXTE uniquement ---- */
window.toutbotPousserActiver = function(){
  if(!('Notification' in window)){ return Promise.resolve('indisponible'); }
  return Notification.requestPermission().then(function(p){
    if(p !== 'granted') return 'refuse';
    return fetch('/api/push/cles').then(function(r){ return r.json(); }).then(function(d){
      if(!d.ok || !d.public) return 'cles_absentes';
      return 'pret';
    });
  });
};
</script>
"""


def _patcher_v7(gabarit: str, ancre: str, ajout: str, ou_avant: bool = False) -> bool:
    if ancre not in TEMPLATES.get(gabarit, ""):
        LOGGER.warning("gabarit V7 : ancre introuvable dans %s", gabarit)
        return False
    remplacement = (ajout + ancre) if ou_avant else (ancre + ajout)
    TEMPLATES[gabarit] = TEMPLATES[gabarit].replace(ancre, remplacement, 1)
    return True


# Bandeau « hors-ligne » + bouton d'écoute dans le fil
_patcher_v7("fil.html", '<p style="white-space:pre-wrap" class="selec" title="Sélectionnez ou copiez librement">{{ p[\'corps\'] }}</p>',
            '\n  <p class="row"><button class="btn-sec" type="button" data-tts="1" '
            'title="Lecture vocale (accessibilité) — aucun audio envoyé">🔊 Écouter</button></p>')
_patcher_v7("base.html", "</body>", _V7_JS + "\n", ou_avant=True)

# Réputation et thème du créateur affichés sur le profil public (via variables Jinja)
app.jinja_env.globals["score_reputation"] = score_reputation
app.jinja_env.globals["theme_createur_de"] = lambda uid: theme_createur_de(uid)
app.jinja_env.globals["badges_de"] = lambda uid: badges_de(uid)


# =============================================================================
# 10) NOTIFICATIONS PUSH (PWA)
# =============================================================================
try:
    from pywebpush import webpush as _webpush  # type: ignore
    _PUSH_DISPONIBLE = True
except Exception:
    _webpush = None
    _PUSH_DISPONIBLE = False


def abonnements_push(user_id) -> List[Dict[str, Any]]:
    return [dict(l) for l in db().execute("SELECT * FROM push_abonnements WHERE user_id = ? AND actif = 1",
                                          (user_id,)).fetchall()]


def envoyer_push(user_id, titre: str, texte: str, lien: str = "/") -> Dict[str, Any]:
    """Envoie une notification push TEXTE. Sans pywebpush ou sans clés VAPID, repli sur la
    notification interne (aucun envoi n'est présenté comme réel s'il n'a pas eu lieu)."""
    abos = abonnements_push(user_id)
    if not abos:
        return {"statut": "aucun_abonnement", "envoi": 0}
    if not _PUSH_DISPONIBLE or not CONFIG_V7["VAPID_PRIVATE"]:
        manque = "pywebpush absent" if not _PUSH_DISPONIBLE else "clés VAPID absentes"
        _journaliser_alerte("push", pseudo_de_v6(user_id), "push", texte, "repli", manque)
        try:
            _ORIG_NOTIFIER(int(user_id), "push_repli", texte[:200], lien)
        except Exception:
            pass
        return {"statut": "repli_notification_integree", "envoi": 0, "raison": manque}
    envoyes, echecs = 0, 0
    charge = json.dumps({"titre": titre[:80], "texte": texte[:400], "lien": lien}, ensure_ascii=False)
    for abo in abos:
        try:
            _webpush(subscription_info=json.loads(abo["infos"] or "{}"), data=charge,
                     vapid_private_key=CONFIG_V7["VAPID_PRIVATE"],
                     vapid_claims={"sub": CONFIG_V7["VAPID_SUBJECT"]})
            envoyes += 1
        except Exception:
            echecs += 1
            try:
                conn = db()
                conn.execute("UPDATE push_abonnements SET actif = 0 WHERE id = ?", (abo["id"],))
                conn.commit()
            except Exception:
                pass
    _journaliser_alerte("push", pseudo_de_v6(user_id), "push", texte,
                        "envoye" if envoyes else "echec", "échecs : %d" % echecs)
    return {"statut": "envoye" if envoyes else "echec", "envoi": envoyes, "echecs": echecs}


@app.route("/api/push/cles")
def api_push_cles():
    return jsonify(ok=True, public=CONFIG_V7["VAPID_PUBLIC"],
                   disponible=bool(_PUSH_DISPONIBLE and CONFIG_V7["VAPID_PRIVATE"]),
                   note="Sans clés VAPID et sans pywebpush, l'application affiche les notifications "
                        "en interne : aucun envoi réel n'est simulé.")


@app.route("/api/push/abonner", methods=["POST"])
def api_push_abonner():
    me = utilisateur_courant()
    if me is None:
        return jsonify(ok=False, erreur="connexion_requise"), 401
    charge = request.get_json(silent=True) or {}
    endpoint = str(charge.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify(ok=False, erreur="endpoint_absent"), 400
    conn = db()
    conn.execute("INSERT INTO push_abonnements (user_id, endpoint, infos, actif, cree_le)"
                 " VALUES (?,?,?,1,?) ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id,"
                 " infos = excluded.infos, actif = 1",
                 (me["id"], endpoint[:400], json.dumps(charge)[:2000], maintenant()))
    conn.commit()
    return jsonify(ok=True, statut="enregistre", disponible=bool(_PUSH_DISPONIBLE and CONFIG_V7["VAPID_PRIVATE"]))


@app.route("/api/push/desabonner", methods=["POST"])
def api_push_desabonner():
    me = utilisateur_courant()
    if me is None:
        return jsonify(ok=False, erreur="connexion_requise"), 401
    charge = request.get_json(silent=True) or {}
    endpoint = str(charge.get("endpoint") or "").strip()
    conn = db()
    conn.execute("UPDATE push_abonnements SET actif = 0 WHERE user_id = ? AND endpoint = ?",
                 (me["id"], endpoint[:400]))
    conn.commit()
    return jsonify(ok=True, statut="retire")


@app.route("/push")
def push_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    abos = db().execute("SELECT * FROM push_abonnements WHERE user_id = ? ORDER BY id DESC LIMIT 10",
                        (me["id"],)).fetchall()
    return page("push.html", titre="Notifications", abos=abos,
                disponible=bool(_PUSH_DISPONIBLE and CONFIG_V7["VAPID_PRIVATE"]),
                public_key=CONFIG_V7["VAPID_PUBLIC"])


TEMPLATES["push.html"] = """{% block contenu %}
<div class="carte">
  <h1>Notifications push (PWA)</h1>
  <p class="muet">Types concernés : nouveaux messages, « J'aime » reçus, validations de paiement,
    résumés quotidiens. La charge utile envoyée est du TEXTE uniquement.</p>
  <p><b>État du service :</b> {{ 'opérationnel' if disponible else 'repli interne — pywebpush ou clés VAPID absents' }}</p>
  <p class="row"><button class="btn-sec" type="button" onclick="toutbotPousserActiver()">Autoriser les notifications</button></p>
</div>
<div class="carte"><h2>Mes abonnements push</h2>
<table><tr><th>Terminal</th><th>Actif</th><th>Créé</th></tr>
{% for a in abos %}<tr><td class="muet">{{ a['endpoint'][:80] }}…</td><td>{{ a['actif'] }}</td>
  <td>{{ a['cree_le'] }}</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


# =============================================================================
# 11) LECTEUR VOCAL (TTS) — ACCESSIBILITÉ, CÔTÉ CLIENT, SANS AUCUN AUDIO STOCKÉ
# =============================================================================
def accessibilite_de(user_id) -> Dict[str, Any]:
    defaut = {"tts_actif": 0, "tts_langue": "fr-FR", "tts_vitesse": 1.0}
    try:
        ligne = db().execute("SELECT * FROM accessibilite WHERE user_id = ?", (user_id,)).fetchone()
    except Exception:
        ligne = None
    if ligne is None:
        return defaut
    return {"tts_actif": int(ligne["tts_actif"]), "tts_langue": ligne["tts_langue"],
            "tts_vitesse": float(ligne["tts_vitesse"] or 1.0)}


@app.route("/parametres/accessibilite", methods=["POST"])
def parametres_accessibilite():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    actif = 1 if request.form.get("tts_actif") else 0
    langue = (request.form.get("tts_langue") or "fr-FR").strip()
    if langue not in ("fr-FR", "en-US", "es-ES", "pt-BR"):
        langue = "fr-FR"
    try:
        vitesse = float(request.form.get("tts_vitesse") or "1")
    except ValueError:
        vitesse = 1.0
    vitesse = max(0.5, min(2.0, vitesse))
    conn = db()
    conn.execute("INSERT INTO accessibilite (user_id, tts_actif, tts_langue, tts_vitesse, maj_le)"
                 " VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
                 " tts_actif = excluded.tts_actif, tts_langue = excluded.tts_langue,"
                 " tts_vitesse = excluded.tts_vitesse, maj_le = excluded.maj_le",
                 (me["id"], actif, langue, vitesse, maintenant()))
    conn.commit()
    session["flash"] = ("Lecteur vocal activé (%s, vitesse %.1f). La synthèse se fait dans votre "
                        "navigateur : aucun fichier audio n'est créé, envoyé ni conservé."
                        % (langue, vitesse))
    return redirect(url_for("parametres"))


# =============================================================================
# 12) THÈMES DE PROFIL PAR CRÉATEUR — COULEURS CSS UNIQUEMENT
# =============================================================================
_MOTIF_HEXA_V7 = re.compile(r"^#[0-9a-fA-F]{6}$")


def theme_createur_de(user_id) -> Dict[str, Any]:
    try:
        ligne = db().execute("SELECT * FROM themes_createur WHERE user_id = ?", (user_id,)).fetchone()
    except Exception:
        ligne = None
    if ligne is None:
        return {}
    return {"accent": ligne["accent"], "fond": ligne["fond"], "texte": ligne["texte"],
            "style": " --accent: %s; --fond: %s; --texte: %s;" % (ligne["accent"] or "#c8a24a",
                                                                  ligne["fond"] or "#10141c",
                                                                  ligne["texte"] or "#e8e8e8")}


@app.route("/parametres/theme-createur", methods=["POST"])
def parametres_theme_createur():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    couleurs = {}
    for champ in ("accent", "fond", "texte"):
        valeur = (request.form.get(champ) or "").strip()
        if valeur and not _MOTIF_HEXA_V7.match(valeur):
            return ("Thème refusé : la couleur « %s » doit être un code hexadécimal à 6 chiffres "
                    "(ex. #c8a24a). Les images, dégradés par image et URL sont interdits — "
                    "ce réseau reste 100 %% textuel." % champ), 400
        couleurs[champ] = valeur or ""
    conn = db()
    conn.execute("INSERT INTO themes_createur (user_id, accent, fond, texte, maj_le)"
                 " VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET accent = excluded.accent,"
                 " fond = excluded.fond, texte = excluded.texte, maj_le = excluded.maj_le",
                 (me["id"], couleurs["accent"] or "#c8a24a", couleurs["fond"] or "#10141c",
                  couleurs["texte"] or "#e8e8e8", maintenant()))
    conn.commit()
    journal_action("theme_createur", details="couleurs mises à jour")
    session["flash"] = "Thème de profil enregistré (couleurs CSS uniquement)."
    return redirect(url_for("parametres"))



# =============================================================================
# 13) BADGES / ACHIEVEMENTS (TEXTE + EMOJI, JAMAIS D'IMAGE ENVOYÉE)
# =============================================================================
BADGES_V7: Tuple[Tuple[str, str, str], ...] = (
    ("premier_pas", "🌱 Premier pas", "A publié son premier texte"),
    ("cent_abonnes", "👥 100 abonnés", "Atteint 100 abonnés"),
    ("premier_mois_rentable", "💰 Premier mois rentable", "Au moins un abonnement validé reçu"),
    ("top_commentateur", "💬 Top commentateur", "20 commentaires publiés ou plus"),
    ("assidu", "📅 Assidu", "Compte âgé de 30 jours ou plus, sans sanction"),
    ("mentor", "🤝 Mentor", "10 pourboires reçus ou plus"),
    ("animateur_groupe", "🏛️ Animateur de groupe", "A créé un groupe privé"),
    ("voix_du_fil", "📣 Voix du fil", "50 publications publiées ou plus"),
)


def badges_de(user_id) -> List[Dict[str, str]]:
    try:
        lignes = db().execute("SELECT code, obtenu_le FROM badges_obtenus WHERE user_id = ?"
                              " ORDER BY id ASC", (user_id,)).fetchall()
    except Exception:
        return []
    index = {code: (libelle, condition) for code, libelle, condition in BADGES_V7}
    sortie = []
    for ligne in lignes:
        libelle, condition = index.get(ligne["code"], (ligne["code"], ""))
        sortie.append({"code": ligne["code"], "libelle": libelle, "condition": condition,
                       "obtenu_le": ligne["obtenu_le"]})
    return sortie


def attribuer_badges(user_id) -> List[str]:
    """Calcule et attribue les badges acquis. Aucun badge n'est inventé : chacun est justifié
    par un compteur réel de la base."""
    conn = db()
    u = utilisateur_par_id(user_id)
    if u is None:
        return []
    compteurs = {
        "posts": int(conn.execute("SELECT COUNT(*) n FROM posts WHERE auteur_id = ?", (user_id,)).fetchone()["n"]),
        "commentaires": int(conn.execute("SELECT COUNT(*) n FROM comments WHERE auteur_id = ?", (user_id,)).fetchone()["n"]),
        "abonnes": int(conn.execute("SELECT COUNT(*) n FROM follows WHERE suivi_id = ?", (user_id,)).fetchone()["n"]),
        "abonnements_valides": int(conn.execute("SELECT COUNT(*) n FROM abonnements WHERE createur_id = ?"
                                                " AND statut = 'valide'", (user_id,)).fetchone()["n"]),
        "pourboires": int(conn.execute("SELECT COUNT(*) n FROM pourboires WHERE createur_id = ?"
                                        " AND statut = 'valide'", (user_id,)).fetchone()["n"]),
        "groupes": int(conn.execute("SELECT COUNT(*) n FROM groupes WHERE createur_id = ?", (user_id,)).fetchone()["n"]),
        "sanctions": int(conn.execute("SELECT COUNT(*) n FROM moderation_journal WHERE user_id = ?",
                                      (user_id,)).fetchone()["n"]),
    }
    anciennete = 0
    try:
        cree = _dt.datetime.strptime(str(u["cree_le"]), "%Y-%m-%d %H:%M:%S")
        anciennete = (_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - cree).days
    except Exception:
        anciennete = 0
    conditions = {
        "premier_pas": compteurs["posts"] >= 1,
        "cent_abonnes": compteurs["abonnes"] >= 100,
        "premier_mois_rentable": compteurs["abonnements_valides"] >= 1,
        "top_commentateur": compteurs["commentaires"] >= 20,
        "assidu": anciennete >= 30 and compteurs["sanctions"] == 0,
        "mentor": compteurs["pourboires"] >= 10,
        "animateur_groupe": compteurs["groupes"] >= 1,
        "voix_du_fil": compteurs["posts"] >= 50,
    }
    nouveaux = []
    for code, condition in conditions.items():
        if not condition:
            continue
        curseur = conn.execute("INSERT OR IGNORE INTO badges_obtenus (user_id, code, obtenu_le)"
                               " VALUES (?,?,?)", (user_id, code, maintenant()))
        if curseur.rowcount:
            nouveaux.append(code)
    conn.commit()
    if nouveaux:
        libelles = {code: lib for code, lib, _ in BADGES_V7}
        _ORIG_NOTIFIER(int(user_id), "badge",
                       "Nouveau badge : " + ", ".join(libelles.get(c, c) for c in nouveaux), "/badges")
        journal_action("badges_attribues", cible="utilisateur #%d" % user_id,
                       details=", ".join(nouveaux))
    return nouveaux


TEMPLATES["badges.html"] = """{% block contenu %}
<div class="carte">
  <h1>Badges &amp; achievements</h1>
  <p class="muet">Chaque badge correspond à un compteur réel de la base : publications, abonnés,
    abonnements validés, pourboires, commentaires, groupes, ancienneté. Les badges sont des textes
    et des emoji — aucune image n'est téléversée ni acceptée.</p>
  <form method="post" action="{{ url_for('badges_recalculer') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}"><button>Recalculer mes badges</button>
  </form>
</div>
<div class="carte"><h2>Mes badges ({{ miens|length }}/{{ total }})</h2>
<ul>{% for b in miens %}<li><b>{{ b['libelle'] }}</b> — {{ b['condition'] }}
  <span class="muet">({{ b['obtenu_le'] }})</span></li>{% endfor %}</ul>
{% if not miens %}<p class="muet">Aucun badge encore. Publiez un premier texte pour obtenir « Premier pas ».</p>{% endif %}
<h3>Encore à obtenir</h3>
<ul>{% for code, libelle, condition in restants %}<li class="muet">{{ libelle }} — {{ condition }}</li>{% endfor %}</ul>
</div>
<div class="carte"><h2>Palmarès</h2>
<table><tr><th>Membre</th><th>Badges</th></tr>
{% for l in palmares %}<tr><td><a href="{{ url_for('profil', pseudo=l['pseudo']) }}">{{ l['pseudo'] }}</a></td>
  <td>{{ l['nombre'] }} — {{ l['libelles'] }}</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


@app.route("/badges")
def badges_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    attribuer_badges(me["id"])
    miens = badges_de(me["id"])
    codes = {b["code"] for b in miens}
    restants = [b for b in BADGES_V7 if b[0] not in codes]
    palmares = []
    for u in db().execute("SELECT id, pseudo FROM users ORDER BY id ASC LIMIT 200").fetchall():
        liste = badges_de(u["id"])
        if liste:
            palmares.append({"pseudo": u["pseudo"], "nombre": len(liste),
                             "libelles": ", ".join(b["libelle"] for b in liste)})
    palmares.sort(key=lambda x: -x["nombre"])
    return page("badges.html", titre="Badges", miens=miens, restants=restants,
                total=len(BADGES_V7), palmares=palmares[:15])


@app.route("/badges/recalculer", methods=["POST"])
def badges_recalculer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    nouveaux = attribuer_badges(me["id"])
    libelles = {code: lib for code, lib, _ in BADGES_V7}
    session["flash"] = ("Nouveau(x) badge(s) : " + ", ".join(libelles.get(c, c) for c in nouveaux)
                        if nouveaux else "Aucun nouveau badge pour le moment.")
    return redirect(url_for("badges_page"))


# =============================================================================
# 14) EXPORT RGPD COMPLET + GEL TEMPORAIRE DU COMPTE
# =============================================================================
def _lignes_table(table: str, colonne: str, user_id) -> List[Dict[str, Any]]:
    try:
        return [dict(l) for l in db().execute(
            "SELECT * FROM %s WHERE %s = ?" % (table, colonne), (user_id,)).fetchall()]
    except Exception:
        return []


def dossier_rgpd(user_id) -> Dict[str, Any]:
    u = utilisateur_par_id(user_id)
    if u is None:
        return {}
    conn = db()
    dossier = {
        "genere_le": maintenant(),
        "application": "ToutBot Mundo (réseau social 100 % textuel)",
        "identite": {"id": u["id"], "pseudo": u["pseudo"], "telephone": u["telephone"],
                     "cree_le": u["cree_le"], "bio": u["bio"],
                     "payout_phone": u["payout_phone"], "payout_op": u["payout_op"],
                     "bloque": u["bloque"], "gele": _valeur_ligne(u, "gele", 0),
                     "gele_jusqua": _valeur_ligne(u, "gele_jusqua", ""),
                     "gele_motif": _valeur_ligne(u, "gele_motif", "")},
        "parametres": _lignes_table("parametres", "user_id", user_id),
        "accessibilite": accessibilite_de(user_id),
        "theme_createur": theme_createur_de(user_id),
        "publications": _lignes_table("posts", "auteur_id", user_id),
        "commentaires": _lignes_table("comments", "auteur_id", user_id),
        "likes_donnes": _lignes_table("likes", "user_id", user_id),
        "abonnements_crees": _lignes_table("abonnements", "createur_id", user_id),
        "abonnements_souscrits": _lignes_table("abonnements", "abonne_id", user_id),
        "pourboires_recus": _lignes_table("pourboires", "createur_id", user_id),
        "pourboires_envoyes": _lignes_table("pourboires", "expediteur_id", user_id),
        "portefeuille": _lignes_table("portefeuille", "user_id", user_id),
        "paiements": _lignes_table("paiements", "user_id", user_id),
        "messages_envoyes": _lignes_table("messages", "expediteur_id", user_id),
        "messages_recus": _lignes_table("messages", "destinataire_id", user_id),
        "plaintes": _lignes_table("plaintes", "user_id", user_id),
        "notifications": _lignes_table("notifications", "user_id", user_id),
        "badges": badges_de(user_id),
        "reputation": score_reputation(user_id),
        "roles": roles_de(user_id),
        "groupes_crees": _lignes_table("groupes", "createur_id", user_id),
        "groupes_rejoints": _lignes_table("groupe_membres", "user_id", user_id),
        "messages_de_groupe": _lignes_table("groupe_messages", "user_id", user_id),
        "ecritures_differees": _lignes_table("publications_differees", "user_id", user_id),
        "votes_dao": _lignes_table("dao_votes", "user_id", user_id),
        "journal_actions": _lignes_table("journal", "user_id", user_id),
        "ecritures_audit": _lignes_table("audit_chain", "user_id", user_id),
        "alertes": _lignes_table("alertes_journal", "destinataire", u["pseudo"]),
        "empreintes_connexion": _lignes_table("empreintes_connexion", "user_id", user_id),
        "signaux_anti_spam": _lignes_table("spam_signaux", "user_id", user_id),
        "note": ("Toutes les données personnelles, publications, relations et écritures "
                 "financières rattachées à ce compte. Cette application ne stocke aucun média "
                 "— aucune image, aucune vidéo, aucun audio, aucune pièce jointe — car ce "
                 "réseau est intégralement textuel : l'export ne peut donc contenir que du "
                 "texte et des nombres."),
    }
    return dossier


@app.route("/rgpd/export")
def rgpd_export():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    dossier = dossier_rgpd(me["id"])
    journal_action("rgpd_export", details="export complet du compte")
    contenu = json.dumps(dossier, ensure_ascii=False, indent=2, default=str)
    reponse = Response(contenu, mimetype="application/json")
    reponse.headers["Content-Disposition"] = 'attachment; filename="rgpd-%s.json"' % me["pseudo"]
    return reponse


def geler_compte(user_id: int, minutes: int, motif: str) -> str:
    jusqua = ""
    if minutes and int(minutes) > 0:
        jusqua = (_dt.datetime.now(_dt.timezone.utc)
                  + _dt.timedelta(minutes=int(minutes))).strftime("%Y-%m-%d %H:%M:%S")
    conn = db()
    conn.execute("UPDATE users SET gele = 1, gele_jusqua = ?, gele_motif = ? WHERE id = ?",
                 (jusqua, (motif or "gel demandé par le membre")[:160], user_id))
    conn.commit()
    journal_action("compte_gele", cible="utilisateur #%d" % user_id,
                   details="jusqu'au %s (%s)" % (jusqua or "nouvel ordre", motif[:80]))
    return jusqua


def degeler_compte(user_id: int) -> bool:
    conn = db()
    conn.execute("UPDATE users SET gele = 0, gele_jusqua = '', gele_motif = '' WHERE id = ?", (user_id,))
    conn.commit()
    journal_action("compte_degele", cible="utilisateur #%d" % user_id)
    return True


def gele_en_cours(u) -> bool:
    if u is None or not int(_valeur_ligne(u, "gele", 0) or 0):
        return False
    jusqua = str(_valeur_ligne(u, "gele_jusqua", "") or "")
    if not jusqua:
        return True
    return jusqua > maintenant()


TEMPLATES["rgpd.html"] = """{% block contenu %}
<div class="carte">
  <h1>Mes données (RGPD)</h1>
  <p class="muet">Export complet en un clic : identité, publications, commentaires, relations,
    messages, plaintes, portefeuille, écritures d'audit, badges, réputation. Le réseau ne stocke
    aucun média, donc l'export ne contient que du texte et des nombres.</p>
  <p><a class="btn" href="{{ url_for('rgpd_export') }}">Télécharger mon dossier complet (JSON)</a></p>
  <p class="muet">Cadre applicable : RGPD européen ; à croiser avec la loi béninoise n° 2017-20
    (code du numérique) et l'APDP. {{ avertissements }}</p>
</div>
<div class="carte">
  <h2>Geler temporairement mon compte</h2>
  <p class="muet">Le gel suspend vos écritures et votre visibilité sans supprimer vos données :
    vous restez capable de lire, d'exporter vos données et de demander la réactivation. Ce n'est
    pas une sanction : elle reste une décision volontaire du membre.</p>
  {% if gele %}<p><b>Compte gelé</b> — jusqu'au {{ gele_jusqua or 'nouvel ordre' }}. Motif : {{ gele_motif }}.</p>
    <form method="post" action="{{ url_for('rgpd_degeler') }}" class="row">
      <input type="hidden" name="csrf" value="{{ csrf }}"><button>Réactiver mon compte</button></form>
  {% else %}
    <form method="post" action="{{ url_for('rgpd_geler') }}" class="row">
      <input type="hidden" name="csrf" value="{{ csrf }}">
      <select name="duree" style="width:auto"><option value="24">24 h</option><option value="168">7 jours</option>
        <option value="720">30 jours</option><option value="0">jusqu'à nouvel ordre</option></select>
      <input name="motif" placeholder="motif (facultatif)" style="width:240px">
      <button>Geler mon compte</button>
    </form>{% endif %}
</div>
{% endblock %}"""


@app.route("/rgpd")
def rgpd_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    return page("rgpd.html", titre="Mes données", gele=gele_en_cours(me),
                gele_jusqua=_valeur_ligne(me, "gele_jusqua", ""),
                gele_motif=_valeur_ligne(me, "gele_motif", ""), avertissements=AVERTISSEMENTS_V7)


@app.route("/rgpd/geler", methods=["POST"])
def rgpd_geler():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    duree = (request.form.get("duree") or "0").strip()
    minutes = int(duree) * 60 if duree.isdigit() and int(duree) else 0
    jusqua = geler_compte(me["id"], minutes, (request.form.get("motif") or "").strip())
    session["flash"] = ("Compte gelé %s. Vos données restent exportables."
                        % ("jusqu'au %s (UTC)" % jusqua if jusqua else "jusqu'à nouvel ordre"))
    return redirect(url_for("rgpd_page"))


@app.route("/rgpd/degeler", methods=["POST"])
def rgpd_degeler():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    degeler_compte(me["id"])
    session["flash"] = "Compte réactivé : vous pouvez de nouveau écrire."
    return redirect(url_for("rgpd_page"))


@app.before_request
def _v7_gel():
    """Le gel est un choix du membre (≠ sanction) : lecture, export et réactivation restent
    toujours ouverts ; seules les écritures sociales sont suspendues."""
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    me = utilisateur_courant()
    if me is None or not gele_en_cours(me):
        return None
    endpoint = request.endpoint or ""
    if endpoint.startswith("admin") or endpoint in {
            "connexion", "deconnexion", "inscription", "rgpd_geler", "rgpd_degeler", "rgpd_page",
            "rgpd_export", "parametres", "parametres_profil", "parametres_mdp", "parametres_prefs",
            "parametres_supprimer", "parametres_accessibilite", "parametres_theme_createur"}:
        return None
    message = ("Compte gelé : vos écritures sont suspendues%s. Vous pouvez lire, exporter vos "
               "données et réactiver votre compte depuis la page « Mes données »."
               % (" jusqu'au %s (UTC)" % _valeur_ligne(me, "gele_jusqua", "")
                  if _valeur_ligne(me, "gele_jusqua", "") else ""))
    if request.path.startswith("/api/") or request.is_json:
        return jsonify(ok=False, erreur="compte_gele", message=message), 403
    session["flash"] = message
    return redirect(request.referrer or url_for("fil"))



# =============================================================================
# 15) DAO LÉGÈRE — VOTES PONDÉRÉS PAR LES TICKETS (RÉSULTAT PROPOSÉ, NON APPLIQUÉ)
# =============================================================================
def poids_vote_v7(user_id) -> int:
    """Poids = tickets payés du membre, plafonné. Un membre sans ticket garde 1 voix."""
    return int(max(1, min(int(CONFIG_V7["DAO_PLAFOND_POIDS"]), solde_tickets_v7(user_id))))


def creer_proposition_v7(auteur_id: int, titre: str, description: str,
                         type_: str = "fonctionnalite", valeur: str = "") -> int:
    conn = db()
    curseur = conn.execute(
        "INSERT INTO dao_propositions (auteur_id, type_, titre, description, valeur, statut,"
        " ouvre_le, cree_le) VALUES (?,?,?,?,?,'ouvert',?,?)",
        (auteur_id, type_ if type_ in ("fonctionnalite", "taux_commission", "autre") else "fonctionnalite",
         titre[:160], description[:2000], str(valeur or "")[:20], maintenant(), maintenant()))
    conn.commit()
    evenement_v6("dao", "Nouvelle proposition : %s" % titre[:120], lien="/dao")
    return curseur.lastrowid


def voter_v7(proposition_id: int, user_id: int, sens: str) -> Dict[str, Any]:
    conn = db()
    proposition = conn.execute("SELECT * FROM dao_propositions WHERE id = ?", (proposition_id,)).fetchone()
    if proposition is None:
        return {"ok": False, "motif": "proposition inconnue"}
    if proposition["statut"] != "ouvert":
        return {"ok": False, "motif": "scrutin fermé"}
    sens = "oui" if str(sens).lower() not in ("non", "contre", "no") else "non"
    poids = poids_vote_v7(user_id)
    conn.execute("INSERT INTO dao_votes (proposition_id, user_id, sens, poids, cree_le)"
                 " VALUES (?,?,?,?,?) ON CONFLICT(proposition_id, user_id) DO UPDATE SET"
                 " sens = excluded.sens, poids = excluded.poids, cree_le = excluded.cree_le",
                 (proposition_id, user_id, sens, poids, maintenant()))
    conn.commit()
    journal_action("dao_vote", cible="proposition #%d" % proposition_id,
                   details="%s (poids %d)" % (sens, poids), utilisateur=user_id)
    return {"ok": True, "sens": sens, "poids": poids}


def resultats_v7(proposition_id: int) -> Dict[str, Any]:
    conn = db()
    lignes = conn.execute("SELECT sens, COUNT(*) n, COALESCE(SUM(poids),0) p FROM dao_votes"
                          " WHERE proposition_id = ? GROUP BY sens", (proposition_id,)).fetchall()
    oui = non = 0
    for ligne in lignes:
        if ligne["sens"] == "oui":
            oui = int(ligne["p"])
        else:
            non = int(ligne["p"])
    total = oui + non
    return {"oui": oui, "non": non, "total": total,
            "pct_oui": round(100.0 * oui / total, 1) if total else 0.0,
            "participation": int(conn.execute("SELECT COUNT(*) n FROM dao_votes WHERE proposition_id = ?",
                                              (proposition_id,)).fetchone()["n"])}


def cloturer_v7(proposition_id: int, decideur_id) -> Dict[str, Any]:
    res = resultats_v7(proposition_id)
    verdict = ("adoptee" if res["total"] and res["pct_oui"] >= 50.0 else
               "rejetee" if res["total"] else "sans_participation")
    texte = ("%s — %s : %d voix pondérées contre %d (%s %% pour), participation %d membre(s)."
             % ("Adoptée" if verdict == "adoptee" else "Rejetée" if verdict == "rejetee" else "Sans participation",
                CONFIG["DEVISE"], res["oui"], res["non"], res["pct_oui"], res["participation"]))
    conn = db()
    conn.execute("UPDATE dao_propositions SET statut = 'clos', ferme_le = ?, resultat = ?"
                 " WHERE id = ?", (maintenant(), texte[:400], proposition_id))
    conn.commit()
    journal_action("dao_cloture", cible="proposition #%d" % proposition_id, details=verdict,
                   utilisateur=decideur_id)
    evenement_v6("dao", "Scrutin #%d clos — %s" % (proposition_id, texte[:160]), lien="/dao")
    return {"verdict": verdict, "resume": texte, **res}


def appliquer_v7(proposition_id: int, decideur_id) -> Dict[str, Any]:
    """Applique le résultat d'un scrutin sur le TAUX DE COMMISSION — uniquement sur décision
    explicite de l'administrateur (avertissement réglementaire affiché)."""
    conn = db()
    proposition = conn.execute("SELECT * FROM dao_propositions WHERE id = ?", (proposition_id,)).fetchone()
    if proposition is None:
        return {"ok": False, "motif": "proposition inconnue"}
    if proposition["type_"] != "taux_commission":
        return {"ok": False, "motif": "seules les propositions de taux de commission sont applicables"}
    if proposition["statut"] != "clos":
        return {"ok": False, "motif": "le scrutin doit être clos avant application"}
    res = resultats_v7(proposition_id)
    if res["total"] == 0 or res["pct_oui"] < 50.0:
        return {"ok": False, "motif": "scrutin non adopté", **res}
    valeur = str(proposition["valeur"] or "").strip()
    if not valeur.replace(".", "", 1).isdigit():
        return {"ok": False, "motif": "valeur de taux invalide"}
    config_v5_set("commission_pct", valeur)
    try:
        CONFIG["COMMISSION_PCT"] = float(valeur)
    except (TypeError, ValueError):
        pass
    journal_action("dao_application", cible="proposition #%d" % proposition_id,
                   details="taux de commission proposé : %s" % valeur, utilisateur=decideur_id)
    alerter_admin("dao", "Le scrutin #%d a été appliqué : taux de commission = %s %% (décision "
                  "explicite de l'administrateur)." % (proposition_id, valeur), gravite="haute")
    return {"ok": True, "taux_applique": valeur, **res}


TEMPLATES["dao.html"] = """{% block contenu %}
<div class="carte">
  <h1>DAO légère — propositions des créateurs</h1>
  <p class="muet">Chaque membre vote avec ses tickets : le poids est le solde de tickets payés
    (plafonné à {{ plafond }}). Un scrutin clos n'est jamais appliqué tout seul : le résultat est
    transmis à l'administrateur, qui décide.</p>
  <p class="muet"><b>Avertissement :</b> {{ avertissements }}</p>
  {% if not actif %}<p><b>La DAO est désactivée par l'administrateur.</b></p>{% endif %}
</div>
{% if actif %}
<div class="carte">
  <h2>Proposer</h2>
  <form method="post" action="{{ url_for('dao_proposer') }}">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <p><label>Titre</label><input name="titre" required maxlength="160" style="width:420px"></p>
    <p><label>Description</label><textarea name="description" maxlength="2000"></textarea></p>
    <p class="row"><select name="type" style="width:auto">
        <option value="fonctionnalite">Nouvelle fonctionnalité</option>
        <option value="taux_commission">Taux de commission (proposé, jamais automatique)</option>
        <option value="autre">Autre</option></select>
      <input name="valeur" placeholder="valeur (ex. 20 pour 20 % — optionnel)" style="width:220px">
      <button>Déposer la proposition</button></p>
  </form>
</div>
{% endif %}
{% for p in propositions %}
<div class="carte">
  <h2>#{{ p['id'] }} — {{ p['titre'] }} <span class="badge">{{ p['statut'] }}</span>
    <span class="badge">{{ p['type_'] }}</span></h2>
  <p style="white-space:pre-wrap">{{ p['description'] }}</p>
  {% if p['valeur'] %}<p class="muet">Valeur proposée : {{ p['valeur'] }}</p>{% endif %}
  <p class="muet">Pour : {{ p['resultats']['oui'] }} — Contre : {{ p['resultats']['non'] }}
    ({{ p['resultats']['pct_oui'] }} % pour, {{ p['resultats']['participation'] }} votant(s))</p>
  {% if p['statut'] == 'ouvert' %}<form method="post" action="{{ url_for('dao_voter', proposition_id=p['id']) }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input type="hidden" name="sens" value="oui"><button>Voter POUR (poids {{ mon_poids }})</button></form>
  <form method="post" action="{{ url_for('dao_voter', proposition_id=p['id']) }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input type="hidden" name="sens" value="non"><button class="btn-sec">Voter CONTRE</button></form>
  {% else %}<p><b>{{ p['resultat'] }}</b></p>{% endif %}
</div>
{% endfor %}
{% endblock %}"""


@app.route("/dao")
def dao_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    propositions = []
    for ligne in db().execute("SELECT * FROM dao_propositions ORDER BY id DESC LIMIT 30").fetchall():
        propositions.append({"id": ligne["id"], "titre": ligne["titre"],
                             "description": ligne["description"], "type_": ligne["type_"],
                             "valeur": ligne["valeur"], "statut": ligne["statut"],
                             "resultat": ligne["resultat"], "resultats": resultats_v7(ligne["id"])})
    return page("dao.html", titre="DAO", propositions=propositions,
                actif=reglage_v7("dao_actif", "0") == "1", mon_poids=poids_vote_v7(me["id"]),
                plafond=CONFIG_V7["DAO_PLAFOND_POIDS"], avertissements=AVERTISSEMENTS_V7)


@app.route("/dao/proposer", methods=["POST"])
def dao_proposer():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    if reglage_v7("dao_actif", "0") != "1":
        session["flash"] = "La DAO est actuellement désactivée par l'administrateur."
        return redirect(url_for("dao_page"))
    me = utilisateur_courant()
    titre = (request.form.get("titre") or "").strip()
    if not titre:
        return "Titre obligatoire.", 400
    faute = contient_media(titre + " " + (request.form.get("description") or ""))
    if faute:
        return "Contenu non autorisé (média détecté : « %s ») : la DAO reste textuelle." % faute, 400
    creer_proposition_v7(me["id"], titre, (request.form.get("description") or "").strip(),
                         (request.form.get("type") or "fonctionnalite"),
                         (request.form.get("valeur") or "").strip())
    session["flash"] = "Proposition déposée. Les membres votent avec leurs tickets."
    return redirect(url_for("dao_page"))


@app.route("/dao/<int:proposition_id>/voter", methods=["POST"])
def dao_voter(proposition_id: int):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    resultat = voter_v7(proposition_id, me["id"], (request.form.get("sens") or "oui"))
    session["flash"] = ("Vote enregistré : %s (poids %s ticket(s))." % (resultat.get("sens"),
                                                                        resultat.get("poids"))
                        if resultat.get("ok") else "Vote refusé : %s." % resultat.get("motif"))
    return redirect(url_for("dao_page"))


@app.route("/admin/dao")
def admin_dao():
    refus = exiger_permission("dao")
    if refus:
        return refus
    propositions = []
    for ligne in db().execute("SELECT * FROM dao_propositions ORDER BY id DESC LIMIT 40").fetchall():
        propositions.append({"id": ligne["id"], "titre": ligne["titre"], "statut": ligne["statut"],
                             "type_": ligne["type_"], "valeur": ligne["valeur"],
                             "resultat": ligne["resultat"], "resultats": resultats_v7(ligne["id"])})
    return page("admin_dao.html", titre="DAO (admin)", propositions=propositions,
                actif=reglage_v7("dao_actif", "0") == "1", avertissements=AVERTISSEMENTS_V7)


TEMPLATES["admin_dao.html"] = """{% block contenu %}
<div class="carte">
  <h1>DAO — administration des scrutins</h1>
  <form method="post" action="{{ url_for('admin_dao_config') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="tuile"><input type="checkbox" name="actif" value="1" {% if actif %}checked{% endif %}>
      Autoriser les propositions des membres</label>
    <button>Enregistrer</button>
  </form>
  <p class="muet">{{ avertissements }}</p>
</div>
<div class="carte"><h2>Scrutins</h2>
<table><tr><th>#</th><th>Titre</th><th>Type</th><th>Valeur</th><th>Statut</th><th>Pour/Contre</th><th>Actions</th></tr>
{% for p in propositions %}<tr><td>{{ p['id'] }}</td><td>{{ p['titre'] }}</td><td>{{ p['type_'] }}</td>
  <td>{{ p['valeur'] }}</td><td>{{ p['statut'] }}</td>
  <td>{{ p['resultats']['oui'] }}/{{ p['resultats']['non'] }}</td>
  <td><form method="post" action="{{ url_for('admin_dao_clore', proposition_id=p['id']) }}" class="row">
      <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Clore</button></form>
    <form method="post" action="{{ url_for('admin_dao_appliquer', proposition_id=p['id']) }}" class="row">
      <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn-sec">Appliquer (taux)</button></form></td>
</tr>{% endfor %}</table></div>
{% endblock %}"""


@app.route("/admin/dao/config", methods=["POST"])
def admin_dao_config():
    refus = exiger_permission("dao")
    if refus:
        return refus
    maj_reglage_v7("dao_actif", "1" if request.form.get("actif") else "0")
    session["flash"] = "Réglage DAO enregistré."
    return redirect(url_for("admin_dao"))


@app.route("/admin/dao/<int:proposition_id>/clore", methods=["POST"])
def admin_dao_clore(proposition_id: int):
    refus = exiger_permission("dao")
    if refus:
        return refus
    me = utilisateur_courant()
    resultat = cloturer_v7(proposition_id, me["id"])
    session["flash"] = "Scrutin clos : %s" % resultat["resume"]
    return redirect(url_for("admin_dao"))


@app.route("/admin/dao/<int:proposition_id>/appliquer", methods=["POST"])
def admin_dao_appliquer(proposition_id: int):
    refus = exiger_permission("dao")
    if refus:
        return refus
    me = utilisateur_courant()
    resultat = appliquer_v7(proposition_id, me["id"])
    session["flash"] = ("Taux de commission appliqué : %s %%" % resultat.get("taux_applique")
                        if resultat.get("ok") else "Application refusée : %s." % resultat.get("motif"))
    return redirect(url_for("admin_dao"))


# =============================================================================
# 16) FONDS DE GARANTIE « NON-PAIEMENT » (PRÉLÈVEMENT VOLONTAIRE, ADMINISTRÉ)
# =============================================================================
def solde_fonds_garantie() -> float:
    ligne = db().execute("SELECT COALESCE(SUM(CASE WHEN sens = 'credit' THEN montant_fcfa"
                         " ELSE -montant_fcfa END),0) s FROM fonds_garantie").fetchone()
    return round(float(ligne["s"] or 0), 2)


def alimenter_fonds_garantie(montant: float, source: str, ref: str = "") -> float:
    pct = float(reglage_v7("garantie_pct", str(CONFIG_V7["GARANTIE_PCT"])) or 0)
    if reglage_v7("garantie_actif", "1") != "1" or pct <= 0:
        return 0.0
    prelevement = round(float(montant or 0) * pct / 100.0, 2)
    if prelevement <= 0:
        return 0.0
    conn = db()
    conn.execute("INSERT INTO fonds_garantie (sens, montant_fcfa, source, ref, motif, cree_le)"
                 " VALUES ('credit',?,?,?,?,?)",
                 (prelevement, source[:60], ref[:80], "prélèvement %g %% du net validé" % pct,
                  maintenant()))
    conn.commit()
    sceller_ecriture("fonds_garantie", ref or source, prelevement, None,
                     "alimentation du fonds (%g %% sur %g)" % (pct, float(montant or 0)))
    return prelevement


TEMPLATES["fonds_garantie.html"] = """{% block contenu %}
<div class="carte">
  <h1>Fonds de garantie « non-paiement »</h1>
  <p class="muet">À chaque abonnement ou pourboire validé, un pourcentage du net est mis de côté
    dans un fonds commun. Le fonds est utilisé, sur décision de l'administrateur, pour indemniser
    un créateur victime d'un non-paiement.</p>
  <p class="muet"><b>Avertissement :</b> {{ avertissements }}</p>
  <p><b>Solde du fonds : {{ solde }} {{ devise }}</b> — taux de prélèvement courant :
    {{ pct }} % ({{ 'actif' if actif else 'inactif' }})</p>
</div>
<div class="carte"><h2>Mouvements récents</h2>
<table><tr><th>Quand</th><th>Sens</th><th>Montant</th><th>Source</th><th>Motif</th></tr>
{% for m in mouvements %}<tr><td>{{ m['cree_le'] }}</td><td>{{ m['sens'] }}</td>
  <td>{{ m['montant_fcfa'] }}</td><td>{{ m['source'] }}</td><td class="muet">{{ m['motif'] }}</td></tr>{% endfor %}</table>
</div>
{% endblock %}"""


@app.route("/fonds-garantie")
def fonds_garantie_page():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    mouvements = db().execute("SELECT * FROM fonds_garantie ORDER BY id DESC LIMIT 40").fetchall()
    return page("fonds_garantie.html", titre="Fonds de garantie",
                solde=solde_fonds_garantie(), mouvements=mouvements,
                pct=reglage_v7("garantie_pct", str(CONFIG_V7["GARANTIE_PCT"])),
                actif=reglage_v7("garantie_actif", "1") == "1", avertissements=AVERTISSEMENTS_V7)


@app.route("/admin/fonds-garantie")
def admin_fonds_garantie():
    refus = exiger_permission("fonds_garantie")
    if refus:
        return refus
    mouvements = db().execute("SELECT * FROM fonds_garantie ORDER BY id DESC LIMIT 60").fetchall()
    return page("admin_fonds.html", titre="Fonds de garantie (admin)",
                solde=solde_fonds_garantie(), mouvements=mouvements,
                pct=reglage_v7("garantie_pct", str(CONFIG_V7["GARANTIE_PCT"])),
                actif=reglage_v7("garantie_actif", "1") == "1", avertissements=AVERTISSEMENTS_V7,
                beneficiaires=[{"id": l["id"], "pseudo": l["pseudo"]}
                               for l in db().execute("SELECT id, pseudo FROM users ORDER BY id ASC"
                                                     " LIMIT 200").fetchall()])


TEMPLATES["admin_fonds.html"] = """{% block contenu %}
<div class="carte">
  <h1>Fonds de garantie — administration</h1>
  <p><b>Solde : {{ solde }} {{ devise }}</b></p>
  <p class="muet">{{ avertissements }}</p>
  <form method="post" action="{{ url_for('admin_fonds_config') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="tuile"><input type="checkbox" name="actif" value="1" {% if actif %}checked{% endif %}> Prélèvement actif</label>
    <input name="pct" type="number" min="0" max="10" step="0.1" value="{{ pct }}" style="width:100px">
    <span class="muet">% du net validé</span>
    <button>Enregistrer</button>
  </form>
</div>
<div class="carte">
  <h2>Décaissement (indemnisation)</h2>
  <form method="post" action="{{ url_for('admin_fonds_decaisser') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="montant" type="number" min="1" step="1" value="1000" style="width:130px">
    <select name="beneficiaire" style="width:auto">{% for b in beneficiaires %}
      <option value="{{ b['id'] }}">{{ b['pseudo'] }}</option>{% endfor %}</select>
    <input name="motif" placeholder="motif (non-paiement constaté)" style="width:280px">
    <button class="btn-sec">Indemniser</button>
  </form>
</div>
<div class="carte"><h2>Mouvements</h2>
<table><tr><th>Quand</th><th>Sens</th><th>Montant</th><th>Source</th><th>Réf.</th><th>Motif</th></tr>
{% for m in mouvements %}<tr><td>{{ m['cree_le'] }}</td><td>{{ m['sens'] }}</td><td>{{ m['montant_fcfa'] }}</td>
  <td>{{ m['source'] }}</td><td>{{ m['ref'] }}</td><td class="muet">{{ m['motif'] }}</td></tr>{% endfor %}</table></div>
{% endblock %}"""


@app.route("/admin/fonds-garantie/config", methods=["POST"])
def admin_fonds_config():
    refus = exiger_permission("fonds_garantie")
    if refus:
        return refus
    maj_reglage_v7("garantie_actif", "1" if request.form.get("actif") else "0")
    pct = (request.form.get("pct") or "0").strip()
    try:
        valeur = max(0.0, min(10.0, float(pct)))
    except ValueError:
        valeur = 0.0
    maj_reglage_v7("garantie_pct", ("%g" % valeur))
    session["flash"] = "Réglages du fonds de garantie enregistrés (%g %%)." % valeur
    return redirect(url_for("admin_fonds_garantie"))


@app.route("/admin/fonds-garantie/decaisser", methods=["POST"])
def admin_fonds_decaisser():
    refus = exiger_permission("fonds_garantie")
    if refus:
        return refus
    me = utilisateur_courant()
    try:
        montant = float(request.form.get("montant") or 0)
    except ValueError:
        montant = 0.0
    if montant <= 0:
        session["flash"] = "Montant invalide."
        return redirect(url_for("admin_fonds_garantie"))
    if montant > solde_fonds_garantie():
        session["flash"] = ("Décaissement refusé : le fonds ne contient que %g %s."
                            % (solde_fonds_garantie(), CONFIG["DEVISE"]))
        return redirect(url_for("admin_fonds_garantie"))
    beneficiaire = (request.form.get("beneficiaire") or "").strip()
    motif = (request.form.get("motif") or "non-paiement").strip()
    conn = db()
    conn.execute("INSERT INTO fonds_garantie (sens, montant_fcfa, source, ref, motif, cree_le)"
                 " VALUES ('debit',?,?,?,?,?)",
                 (round(montant, 2), "indemnisation", "#%s" % beneficiaire, motif[:200], maintenant()))
    conn.commit()
    sceller_ecriture("fonds_garantie_debit", "#%s" % beneficiaire, montant, me["id"], motif)
    if beneficiaire.isdigit():
        _pf(int(beneficiaire), "credit", montant, "Indemnisation fonds de garantie", "GARANTIE")
    journal_action("fonds_decaissement", cible="utilisateur #%s" % beneficiaire,
                   details="%g %s — %s" % (montant, CONFIG["DEVISE"], motif[:120]), utilisateur=me["id"])
    session["flash"] = "Indemnisation de %g %s enregistrée et scellée dans la chaîne d'audit." % (
        montant, CONFIG["DEVISE"])
    return redirect(url_for("admin_fonds_garantie"))


# =============================================================================
# V7 — BRANCHEMENTS : notifications push, fonds de garantie, audit, alertes
# =============================================================================
_ORIG_VALIDER_ABONNEMENT_V7 = valider_abonnement
_ORIG_VALIDER_POURBOIRE_V7 = valider_pourboire
_ORIG_CREER_ABONNEMENT_V7 = creer_abonnement
_ORIG_CREER_POURBOIRE_V7 = creer_pourboire


def creer_abonnement(*args, **kwargs):  # noqa: F811
    identifiant = _ORIG_CREER_ABONNEMENT_V7(*args, **kwargs)
    try:
        createur = _param_v6(args, kwargs, 1, "createur_id", None)
        palier = _param_v6(args, kwargs, 2, "palier", 0)
        sceller_ecriture("abonnement_attente", "ABO-%s" % identifiant, float(palier or 0),
                         createur, "abonnement en attente de validation")
    except Exception:
        LOGGER.warning("audit abonnement impossible", exc_info=False)
    return identifiant


def creer_pourboire(*args, **kwargs):  # noqa: F811
    identifiant = _ORIG_CREER_POURBOIRE_V7(*args, **kwargs)
    try:
        createur = _param_v6(args, kwargs, 1, "createur_id", None)
        montant = _param_v6(args, kwargs, 2, "montant", 0)
        sceller_ecriture("pourboire_attente", "TIP-%s" % identifiant, float(montant or 0),
                         createur, "pourboire en attente de validation")
    except Exception:
        LOGGER.warning("audit pourboire impossible", exc_info=False)
    return identifiant


def valider_abonnement(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_VALIDER_ABONNEMENT_V7(*args, **kwargs)
    if resultat:
        try:
            identifiant = _param_v6(args, kwargs, 0, "abo_id", None)
            ligne = db().execute("SELECT createur_id, net FROM abonnements WHERE id = ?",
                                 (identifiant,)).fetchone()
            if ligne is not None:
                sceller_ecriture("abonnement_valide", "ABO-%s" % identifiant, ligne["net"],
                                 ligne["createur_id"], "net crédité au créateur")
                alimenter_fonds_garantie(ligne["net"], "abonnement", "ABO-%s" % identifiant)
                alerter_si_seuil(ligne["createur_id"], ligne["net"], "abonnement validé")
        except Exception:
            LOGGER.warning("audit validation abonnement impossible", exc_info=False)
    return resultat


def valider_pourboire(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_VALIDER_POURBOIRE_V7(*args, **kwargs)
    if resultat:
        try:
            identifiant = _param_v6(args, kwargs, 0, "tip_id", None)
            ligne = db().execute("SELECT createur_id, net FROM pourboires WHERE id = ?",
                                 (identifiant,)).fetchone()
            if ligne is not None:
                sceller_ecriture("pourboire_valide", "TIP-%s" % identifiant, ligne["net"],
                                 ligne["createur_id"], "net crédité au créateur")
                alimenter_fonds_garantie(ligne["net"], "pourboire", "TIP-%s" % identifiant)
                alerter_si_seuil(ligne["createur_id"], ligne["net"], "pourboire validé")
        except Exception:
            LOGGER.warning("audit validation pourboire impossible", exc_info=False)
    return resultat


_ORIG_ECRIRE_PORTEFEUILLE_V7 = ecrire_portefeuille


def ecrire_portefeuille(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_ECRIRE_PORTEFEUILLE_V7(*args, **kwargs)
    try:
        user_id = _param_v6(args, kwargs, 0, "user_id", None)
        sens = _param_v6(args, kwargs, 1, "sens", "")
        fcfa = _param_v6(args, kwargs, 2, "fcfa", 0)
        libelle = _param_v6(args, kwargs, 3, "libelle", "")
        sceller_ecriture("portefeuille", "PF-%s-%s" % (user_id, int(time.time() * 1000)),
                         float(fcfa or 0), user_id, "%s — %s" % (sens, libelle))
    except Exception:
        LOGGER.warning("audit portefeuille impossible", exc_info=False)
    return resultat


def marquer_paye(*args, **kwargs):  # noqa: F811
    resultat = _ORIG_MARQUER_PAYE(*args, **kwargs)
    if isinstance(resultat, dict) and resultat.get("ok"):
        try:
            user_id = _param_v6(args, kwargs, 0, "user_id", None)
            sceller_ecriture("versement", "VERS-%s" % user_id, float(resultat.get("fcfa") or 0),
                             user_id, "%s ticket(s) / %s %s versés"
                             % (resultat.get("tickets"), resultat.get("fcfa"), CONFIG["DEVISE"]))
        except Exception:
            LOGGER.warning("audit versement impossible", exc_info=False)
    return resultat


_ORIG_NOTIFIER = notifier


def notifier(user_id: int, type_: str, texte: str, lien: str = "") -> None:  # noqa: F811
    """Toute notification interne part aussi en push TEXTE (messages, likes, validations…)."""
    _ORIG_NOTIFIER(user_id, type_, texte, lien)
    try:
        envoyer_push(int(user_id), type_ or "notification", texte, lien or "/")
    except Exception:
        LOGGER.warning("push impossible", exc_info=False)


_ORIG_PLAINTE_V7 = app.view_functions.get("plainte_creer")


def _v7_plainte_creer(*args, **kwargs):
    me = utilisateur_courant()
    sujet = (request.form.get("sujet") or request.form.get("objet") or "").strip()
    corps = (request.form.get("corps") or "").strip()
    reponse = _ORIG_PLAINTE_V7(*args, **kwargs)
    if me is not None:
        try:
            sceller_ecriture("plainte", "PL-%s" % int(time.time() * 1000), 0, me["id"],
                             "plainte déposée : %s" % (sujet or "(sans sujet)")[:120])
            alerter_plainte_grave(sujet, corps, me["pseudo"])
        except Exception:
            LOGGER.warning("alerte plainte impossible", exc_info=False)
    return reponse


if _ORIG_PLAINTE_V7 is not None:
    app.view_functions["plainte_creer"] = _v7_plainte_creer


_ORIG_PUBLIER_V7 = app.view_functions.get("publier")


def _v7_publier(*args, **kwargs):
    """Publication : badges + surveillance anti-spam après enregistrement."""
    reponse = _ORIG_PUBLIER_V7(*args, **kwargs)
    me = utilisateur_courant()
    if me is not None and request.method == "POST" and getattr(reponse, "status_code", 500) == 302:
        try:
            attribuer_badges(me["id"])
            etat = detecter_spam(me["id"])
            if etat["score"] >= 60:
                session["flash"] = ((session.get("flash") or "") +
                                    " Signal anti-spam enregistré pour examen par la modération.").strip()
        except Exception:
            LOGGER.warning("surveillance publication impossible", exc_info=False)
    return reponse


if _ORIG_PUBLIER_V7 is not None:
    app.view_functions["publier"] = _v7_publier


_ORIG_PROFIL_V7 = app.view_functions.get("profil")


def _v7_profil(*args, **kwargs):
    """Profil public : badges recalculés à la visite (compteurs réels uniquement)."""
    try:
        pseudo = (request.view_args or {}).get("pseudo")
        if pseudo:
            cible = db().execute("SELECT id FROM users WHERE pseudo = ?", (pseudo,)).fetchone()
            if cible is not None:
                attribuer_badges(cible["id"])
    except Exception:
        LOGGER.warning("badges du profil impossibles", exc_info=False)
    return _ORIG_PROFIL_V7(*args, **kwargs)


if _ORIG_PROFIL_V7 is not None:
    app.view_functions["profil"] = _v7_profil


@app.before_request
def _v7_empreinte():
    if "uid" in session:
        try:
            enregistrer_empreinte(int(session["uid"]))
        except Exception:
            pass
    return None


# ----------------------------------------------------------------------------
# Navigation V7 : liens membres + liens d'administration
# ----------------------------------------------------------------------------
_LIENS_MEMBRE_V7 = ('<a href="{{ url_for(\'reputation_page\') }}">Réputation</a>'
                    '<a href="{{ url_for(\'badges_page\') }}">Badges</a>'
                    '<a href="{{ url_for(\'dao_page\') }}">DAO</a>'
                    '<a href="{{ url_for(\'fonds_garantie_page\') }}">Fonds</a>')
_ANCRE_MEMBRE_V7 = "<a href=\"{{ url_for('groupes_page') }}\">Groupes</a>"
if _ANCRE_MEMBRE_V7 in TEMPLATES["base.html"]:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _ANCRE_MEMBRE_V7, _ANCRE_MEMBRE_V7 + _LIENS_MEMBRE_V7, 1)
else:
    LOGGER.warning("navigation membre V7 non posée")

_LIENS_ADMIN_V7 = ('<a href="{{ url_for(\'admin_roles\') }}"><b>Rôles</b></a>'
                   '<a href="{{ url_for(\'admin_audit\') }}"><b>Audit</b></a>'
                   '<a href="{{ url_for(\'admin_alertes\') }}"><b>Alertes</b></a>'
                   '<a href="{{ url_for(\'admin_spam\') }}"><b>Anti-spam</b></a>'
                   '<a href="{{ url_for(\'admin_rapport\') }}"><b>Rapport</b></a>'
                   '<a href="{{ url_for(\'admin_fonds_garantie\') }}"><b>Fonds</b></a>'
                   '<a href="{{ url_for(\'admin_dao\') }}"><b>DAO</b></a>'
                   '<a href="{{ url_for(\'admin_resume_declencher\') }}"><b>Résumé</b></a>')
_ANCRE_ADMIN_V7 = "<a href=\"{{ url_for('admin_gouvernance') }}\"><b>Gouvernance</b></a>"
if _ANCRE_ADMIN_V7 in TEMPLATES["base.html"]:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _ANCRE_ADMIN_V7, _ANCRE_ADMIN_V7 + _LIENS_ADMIN_V7, 1)
else:
    LOGGER.warning("navigation admin V7 non posée")

# Reputation, theme et badges visibles sur le profil public (variables Jinja)
_ANCRE_REPUT_V7 = "<p class=\"muet\">{{ abonnes }} abonné(s) · {{ suivis }} abonnement(s) pris par ce compte</p>"
if _ANCRE_REPUT_V7 in TEMPLATES["profil.html"]:
    TEMPLATES["profil.html"] = TEMPLATES["profil.html"].replace(
        _ANCRE_REPUT_V7,
        _ANCRE_REPUT_V7
        + "\n  {% set rep = score_reputation(cible['id']) %}"
        + "\n  {% if rep.visible %}<p><b>Score de confiance : {{ rep.score }}/100</b> "
          "<span class=\"badge\">{{ rep['etiquette'] }}</span> "
          "<span class=\"muet\">(ancienneté {{ rep['jours'] }} j · {{ rep['validations'] }} abonnement(s) validé(s) · "
          "{{ rep['plaintes_ouvertes'] }} plainte(s) ouverte(s))</span></p>{% endif %}"
        + "\n  {% set th = theme_createur_de(cible['id']) %}"
        + "\n  {% if th %}<p class=\"muet\" style=\"{{ th['style'] }}\">Thème du créateur : accent {{ th['accent'] }} · "
          "fond {{ th['fond'] }} · texte {{ th['texte'] }}</p>{% endif %}"
        + "\n  {% set bl = badges_de(cible['id']) %}"
        + "\n  {% if bl %}<p>{% for b in bl %}<span class=\"badge\">{{ b['libelle'] }}</span> {% endfor %}</p>{% endif %}",
        1)
else:
    LOGGER.warning("bloc réputation du profil non posé")

# Réglages d'accessibilité (TTS) et thème de créateur proposés sur la page Paramètres
_ANCRE_PARAM_V7 = "{% block contenu %}"
if _ANCRE_PARAM_V7 in TEMPLATES["parametres.html"]:
    TEMPLATES["parametres.html"] = TEMPLATES["parametres.html"].replace(
        _ANCRE_PARAM_V7,
        _ANCRE_PARAM_V7 + """
<div class="carte">
  <h2>Accessibilité — lecteur vocal (TTS)</h2>
  <p class="muet">La lecture se fait dans votre navigateur : aucun fichier audio n'est créé,
    envoyé ou conservé. C'est une aide à la lecture, pas un média hébergé.</p>
  <form method="post" action="{{ url_for('parametres_accessibilite') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="tuile"><input type="checkbox" name="tts_actif" value="1"
      {% if accessibilite['tts_actif'] %}checked{% endif %}> Activer le lecteur vocal</label>
    <select name="tts_langue" style="width:auto">
      {% for code, libelle in (('fr-FR','Français'),('en-US','English'),('es-ES','Español'),('pt-BR','Português')) %}
      <option value="{{ code }}" {% if accessibilite['tts_langue'] == code %}selected{% endif %}>{{ libelle }}</option>
      {% endfor %}</select>
    <input name="tts_vitesse" type="number" min="0.5" max="2" step="0.1"
      value="{{ accessibilite['tts_vitesse'] }}" style="width:90px">
    <button>Enregistrer</button>
  </form>
</div>
<div class="carte">
  <h2>Thème de mon profil (couleurs uniquement)</h2>
  <form method="post" action="{{ url_for('parametres_theme_createur') }}" class="row">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <input name="accent" placeholder="#c8a24a" value="{{ theme['accent'] }}" style="width:120px">
    <input name="fond" placeholder="#10141c" value="{{ theme['fond'] }}" style="width:120px">
    <input name="texte" placeholder="#e8e8e8" value="{{ theme['texte'] }}" style="width:120px">
    <button>Enregistrer</button>
  </form>
  <p class="muet">Codes hexadécimaux à 6 chiffres. Images, dégradés par image et URL refusés.</p>
</div>
<div class="carte">
  <h2>Mes données (RGPD) et notifications</h2>
  <p class="row"><a class="btn btn-sec" href="{{ url_for('rgpd_page') }}">Gérer mes données / geler mon compte</a>
    <a class="btn btn-sec" href="{{ url_for('push_page') }}">Notifications push</a>
    <a class="btn btn-sec" href="{{ url_for('resume_quotidien_page') }}">Résumé quotidien</a>
    <a class="btn btn-sec" href="{{ url_for('chat_multilingue') }}">Chat multilingue</a>
    <a class="btn btn-sec" href="{{ url_for('hors_ligne_page') }}">Mode hors-ligne</a></p>
</div>""", 1)
else:
    LOGGER.warning("bloc accessibilité des paramètres non posé")

_ORIG_PARAMETRES_V7 = app.view_functions.get("parametres")


def _v7_parametres(*args, **kwargs):
    """Injecte l'état d'accessibilité et les couleurs du créateur dans la page Paramètres,
    sans réécrire son gabarit d'origine."""
    me = utilisateur_courant()
    if me is None:
        return _ORIG_PARAMETRES_V7(*args, **kwargs)
    try:
        gabarit = TEMPLATES["parametres.html"]
        if "{% set _v7 = 1 %}" not in gabarit:
            TEMPLATES["parametres.html"] = ("{% set accessibilite = accessibilite_de(me['id']) %}"
                                            "{% set theme = theme_createur_de(me['id']) %}"
                                            "{% set _v7 = 1 %}" + gabarit)
        app.jinja_env.globals["accessibilite_de"] = accessibilite_de
        return render_template_string("{% extends 'base.html' %}"
                                      + TEMPLATES["parametres.html"],
                                      accessibilite=accessibilite_de(me["id"]),
                                      theme=theme_createur_de(me["id"]), **_contexte())
    except Exception:
        LOGGER.warning("page Paramètres V7 non enrichie", exc_info=True)
        try:
            return _ORIG_PARAMETRES_V7(*args, **kwargs)
        except Exception:
            LOGGER.exception("page Paramètres totalement indisponible")
            return "Erreur lors du chargement de la page Paramètres. Réessayez.", 500


if _ORIG_PARAMETRES_V7 is not None:
    app.view_functions["parametres"] = _v7_parametres

app.jinja_env.globals["accessibilite_de"] = accessibilite_de
app.jinja_env.globals["solde_fonds_garantie"] = solde_fonds_garantie
app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES)])
try:
    app.jinja_env.cache.clear()
except Exception:
    pass


# =============================================================================
# FIN DES EXTENSIONS V6 — démarrage du serveur (le garde est ici, après TOUT)
# =============================================================================

# =============================================================================
# LIAISON DES DEUX PARTIES — greffe du moteur V8 sur l'application (mono-fichier)
# -----------------------------------------------------------------------------
# En module séparé, le branchement se faisait par « brancher_sur_toutbot(M) ».
# Réunis dans un même fichier, M est ce fichier lui-même.
# =============================================================================
MOTEUR_V8_SUR_TOUTBOT = None
try:
    with app.app_context():
        MOTEUR_V8_SUR_TOUTBOT = brancher_sur_toutbot(sys.modules[__name__])
    LOGGER.info("Moteur de monétisation V8 branché : %s", MOTEUR_V8_SUR_TOUTBOT)
except Exception:  # le démarrage de l'application ne doit jamais dépendre du moteur V8
    LOGGER.warning("Moteur de monétisation V8 non branché", exc_info=True)


# =============================================================================
# SECTION UNIFIÉE V9 — demandes utilisateur
#   • Éligibilité par pays : TOUS les pays du monde, sans restriction ni exception
#   • Flux « vues » V8 (part application 75 %) relié aux DEUX numéros admin
#   • Colonnes admin_mtn_numero / admin_moov_numero sur CHAQUE ligne users
# =============================================================================

# Liste ISO 3166-1 complète (249 territoires) — tous éligibles, sans exception.
PAYS_AUTORISES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ
BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM
DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS
GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ
MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM
PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV
SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
VN VU WF WS YE YT ZA ZM ZW
""".split())

REGION_PAYS = "monde"   # aucune restriction géographique : monde entier


def pays_autorise(code_pays):
    """Tous les pays du monde sont éligibles, sans restriction ni exception.
    Le contrôle reste réel : un code hors ISO 3166-1 est refusé."""
    if not code_pays:
        return False
    return str(code_pays).strip().upper() in PAYS_AUTORISES


def _init_reversements_app_admin():
    with _curseur() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS reversements_app_admin ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " mois TEXT NOT NULL UNIQUE,"
            " vues_pond_x100 INTEGER NOT NULL DEFAULT 0,"
            " pool_centimes INTEGER NOT NULL DEFAULT 0,"
            " part_app_centimes INTEGER NOT NULL DEFAULT 0,"
            " part_createurs_centimes INTEGER NOT NULL DEFAULT 0,"
            " numero_mtn TEXT NOT NULL DEFAULT '',"
            " numero_moov TEXT NOT NULL DEFAULT '',"
            " statut TEXT NOT NULL DEFAULT 'a_encaisser',"
            " cree_le TEXT NOT NULL)")


def _init_colonnes_par_compte():
    """Deux numéros par ligne users + remplissage rétroactif des comptes existants."""
    conn = sqlite3.connect(CONFIG["DB"])
    for colonne in ("admin_mtn_numero", "admin_moov_numero"):
        try:
            conn.execute("ALTER TABLE users ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % colonne)
        except sqlite3.OperationalError:
            pass
    remplis = conn.execute(
        "UPDATE users SET admin_mtn_numero = ?, admin_moov_numero = ?"
        " WHERE admin_mtn_numero = '' OR admin_moov_numero = ''",
        (CONFIG["ADMIN_MTN"], CONFIG["ADMIN_MOOV"])).rowcount
    conn.commit()
    conn.close()
    return remplis


_ORIG_CLOTURER_MOIS_V8 = cloturer_mois


def cloturer_mois(mois=None, mode=None, sceller=None):
    """Clôture V8, puis inscription de la part application (75 %) sur les deux numéros."""
    resultat = _ORIG_CLOTURER_MOIS_V8(mois=mois, mode=mode, sceller=sceller)
    _init_reversements_app_admin()
    with _curseur() as conn:
        conn.execute(
            "INSERT INTO reversements_app_admin (mois, vues_pond_x100, pool_centimes,"
            " part_app_centimes, part_createurs_centimes, numero_mtn, numero_moov, statut, cree_le)"
            " VALUES (?,?,?,?,?,?,?,'a_encaisser',?)"
            " ON CONFLICT(mois) DO UPDATE SET vues_pond_x100=excluded.vues_pond_x100,"
            " pool_centimes=excluded.pool_centimes, part_app_centimes=excluded.part_app_centimes,"
            " part_createurs_centimes=excluded.part_createurs_centimes,"
            " numero_mtn=excluded.numero_mtn, numero_moov=excluded.numero_moov",
            (resultat["mois"], resultat["vues_pond_x100"], resultat["pool"],
             resultat["part_application"], resultat["part_createurs"],
             CONFIG["ADMIN_MTN"], CONFIG["ADMIN_MOOV"], _v8_maintenant()))
    return resultat


@app.route("/reversements-admin")
def reversements_admin():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    if not me or not me["est_admin"]:
        return "Reserve a l'administrateur.", 403
    _init_reversements_app_admin()
    with _curseur() as conn:
        lignes = conn.execute(
            "SELECT * FROM reversements_app_admin ORDER BY mois DESC LIMIT 36").fetchall()
    corps = ["<h2>Part de l'application (75 % des vues) — à encaisser</h2>",
             "<p class='muet'>Barème 50 000 FCFA / 1 000 vues. Créateurs 25 %, application 75 %, versés sur ces deux numéros.</p>",
             "<div class='mm-num'><label>MTN</label><input type='text' readonly value='%s' onclick='this.select()'><button class='btn-sec' type='button' onclick='copier(this)'>Copier</button></div>"
             % CONFIG["ADMIN_MTN"],
             "<div class='mm-num'><label>Moov</label><input type='text' readonly value='%s' onclick='this.select()'><button class='btn-sec' type='button' onclick='copier(this)'>Copier</button></div>"
             % CONFIG["ADMIN_MOOV"],
             "<table><tr><th>Mois</th><th>Vues pondérées</th><th>Pool</th><th>Part app 75 %</th><th>Part créateurs 25 %</th></tr>"]
    for lg in lignes:
        corps.append("<tr><td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td></tr>" % (
            lg["mois"], lg["vues_pond_x100"] // 100, formater(lg["pool_centimes"]),
            formater(lg["part_app_centimes"]), formater(lg["part_createurs_centimes"])))
    if not lignes:
        corps.append("<tr><td colspan='5'>Aucune clôture enregistrée pour l'instant.</td></tr>")
    corps.append("</table>")
    corps.append("<p class='muet'>Pays éligibles : tous les pays du monde (%d), sans restriction ni exception.</p>"
                 % len(PAYS_AUTORISES))
    return render_template_string("{% extends 'base.html' %}{% block contenu %}"
                                  + "".join(corps) + "{% endblock %}", **_contexte())


try:
    _init_colonnes_par_compte()
    with app.app_context():
        _init_reversements_app_admin()
    LOGGER.info("V9 : colonnes par compte + reversements application prets (%d pays)",
                len(PAYS_AUTORISES))
except Exception:
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


@app.before_request
def _v10_identifiant_requete():
    g.v10_rid = secrets.token_hex(8)
    g.v10_debut = time.time()


@app.after_request
def _v10_journaliser_requete(reponse):
    try:
        debut = getattr(g, "v10_debut", None)
        duree = int((time.time() - debut) * 1000) if debut else -1
        if not request.path.startswith(("/static", "/sw.js", "/manifest", "/api/non_lus", "/api/ping")):
            _v10_log("INFO", "requete", rid=getattr(g, "v10_rid", "-"), methode=request.method,
                     chemin=request.path[:200], statut=reponse.status_code, ms=duree,
                     ip=_v10_ip(), uid=session.get("uid"))
        reponse.headers["X-Request-ID"] = getattr(g, "v10_rid", "-")
    except Exception:
        pass
    return reponse


# -----------------------------------------------------------------------------
# 3) EN-TÊTES DE SÉCURITÉ, HSTS ET REDIRECTION HTTPS
# -----------------------------------------------------------------------------
_CSP_V10 = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "manifest-src 'self'; "
    "worker-src 'self'"
)


def _v10_ip() -> str:
    """Adresse IP réelle (derrière proxy) — utilisée pour bannir et limiter."""
    try:
        if _env("TOUTBOT_TRUST_PROXY", "1") == "1":
            entete = request.headers.get("X-Forwarded-For", "")
            if entete:
                return entete.split(",")[0].strip()[:60]
    except Exception:
        pass
    try:
        return (request.remote_addr or "0.0.0.0")[:60]
    except Exception:
        return "0.0.0.0"


@app.before_request
def _v10_forcer_https():
    if not _V10_HTTPS or request.method == "OPTIONS":
        return None
    if request.path.startswith("/webhooks/"):
        return None
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    if proto != "https":
        cible = "https://" + request.host + request.full_path.rstrip("?")
        return redirect(cible, code=301)
    return None


@app.after_request
def _v10_entetes_securite(reponse):
    try:
        reponse.headers.setdefault("Content-Security-Policy", _CSP_V10)
        reponse.headers.setdefault("X-Frame-Options", "DENY")
        reponse.headers.setdefault("X-Content-Type-Options", "nosniff")
        reponse.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        reponse.headers.setdefault("Permissions-Policy",
                                   "geolocation=(), microphone=(), camera=(), payment=()")
        reponse.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        reponse.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if _V10_HTTPS:
            reponse.headers.setdefault("Strict-Transport-Security",
                                       "max-age=31536000; includeSubDomains; preload")
        reponse.headers.setdefault("X-ToutBot-Version", V10["version"])
        # Aucune donnée sensible ne doit être mise en cache par un intermédiaire
        if session.get("uid"):
            reponse.headers.setdefault("Cache-Control", "private, no-store, max-age=0")
    except Exception:
        pass
    return reponse


# -----------------------------------------------------------------------------
# 4) LIMITATION DE DÉBIT (anti-DoS) + BANNISSEMENT BRUTE-FORCE
# -----------------------------------------------------------------------------
_V10_COMPTEURS: Dict[str, Any] = {}
_V10_BANNIS: Dict[str, float] = {}
_V10_BAN_VERROU = threading.Lock()


def _v10_redis():
    url = _env("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis  # type: ignore
        client = redis.from_url(url, decode_responses=True, socket_timeout=1.5)
        client.ping()
        _V10_LOGGER.info("Redis connecté : limitation de débit et compteurs partagés.")
        return client
    except Exception:
        _V10_LOGGER.warning("Redis indisponible : compteurs locaux (valables pour 1 processus).",
                            exc_info=False)
        return None


_V10_REDIS = _v10_redis()


def _v10_compteur_augmenter(cle: str, periode: int) -> int:
    """Incrémente un compteur fenêtré. Redis si disponible, sinon mémoire locale."""
    maintenant_ts = time.time()
    if _V10_REDIS is not None:
        try:
            k = "tbm:rl:%s:%d" % (cle, int(maintenant_ts // periode))
            valeur = _V10_REDIS.incr(k)
            if valeur == 1:
                _V10_REDIS.expire(k, periode + 2)
            return int(valeur)
        except Exception:
            pass
    with _V10_BAN_VERROU:
        marques = _V10_COMPTEURS.setdefault(cle, [])
        limite_basse = maintenant_ts - periode
        while marques and marques[0] < limite_basse:
            marques.pop(0)
        marques.append(maintenant_ts)
        return len(marques)


def _v10_est_banni(ip: str) -> bool:
    if _V10_REDIS is not None:
        try:
            return bool(_V10_REDIS.exists("tbm:ban:%s" % ip))
        except Exception:
            pass
    with _V10_BAN_VERROU:
        expire = _V10_BANNIS.get(ip)
        if expire is None:
            return False
        if expire < time.time():
            _V10_BANNIS.pop(ip, None)
            return False
        return True


def _v10_bannir(ip: str, secondes: int, motif: str) -> None:
    if _V10_REDIS is not None:
        try:
            _V10_REDIS.setex("tbm:ban:%s" % ip, secondes, motif)
        except Exception:
            pass
    with _V10_BAN_VERROU:
        _V10_BANNIS[ip] = time.time() + secondes
    _v10_log("WARNING", "ip_bannie", ip=ip, motif=motif, secondes=secondes)
    try:
        journal_action("securite", cible=ip, details="Bannissement %ds — %s" % (secondes, motif))
    except Exception:
        pass


def _v10_secondes_restantes(ip: str) -> int:
    with _V10_BAN_VERROU:
        expire = _V10_BANNIS.get(ip)
        return int(expire - time.time()) if expire and expire > time.time() else 0


_ROUTES_AUTH_V10 = {"/connexion", "/inscription", "/admin/inscription"}
# Routes d'ÉCRITURE sensibles : plafonnées à TOUTBOT_RATE_SENSIBLE (10/min par défaut,
# le chiffre demandé). Les LECTURES relèvent de TOUTBOT_RATE_GLOBAL : un chargement
# de page émet déjà ~10 requêtes, un plafond global de 10/min rendrait l'application
# inutilisable (voir .env.example).
_ROUTES_SENSIBLES_V10 = (
    "/publier", "/p/aime/", "/p/commenter/", "/messages/", "/plaintes", "/chat",
    "/abonner/", "/p/abonnement/", "/p/pourboire/", "/p/repondre/", "/groupes/",
    "/stories/creer", "/sondage/creer", "/sondage/", "/boutique/acheter/", "/live/",
    "/parametres", "/prets/", "/mon-historique", "/api/live/", "/api/groupe/",
    "/api/push/", "/api/hors-ligne/",
)


@app.before_request
def _v10_anti_dos():
    """Deuxième rideau applicatif : bannissement brute-force puis taux par IP."""
    if request.method == "OPTIONS":
        return None
    if request.path.startswith("/webhooks/"):
        return None  # les agrégateurs Mobile Money doivent pouvoir rappeler
    ip = _v10_ip()

    if _v10_est_banni(ip):
        if request.path in _ROUTES_AUTH_V10:
            reste = _v10_secondes_restantes(ip) or _V10_FENETRE_BAN
            reponse = ("Accès à l'authentification SUSPENDU pour cette adresse IP "
                       "pendant encore %d seconde(s) — 3 essais par minute au maximum "
                       "(protection anti brute-force)." % reste, 403)
            return reponse
    if not _V10_RATELIMIT_ACTIF:
        return None

    if request.path in _ROUTES_AUTH_V10 and request.method == "POST":
        n = _v10_compteur_augmenter("auth:%s" % ip, 60)
        if n > _V10_ESSAIS_AUTH:
            _v10_bannir(ip, _V10_FENETRE_BAN, "brute-force sur %s" % request.path)
            return ("Trop d'essais sur l'authentification : cette adresse IP est bannie "
                    "pendant %d minutes." % (_V10_FENETRE_BAN // 60), 429)

    sensible = (request.method in ("POST", "PUT", "PATCH", "DELETE")
                or any(request.path.startswith(p) for p in _ROUTES_SENSIBLES_V10))
    if sensible:
        limite, cle = _V10_LIMITE_SENSIBLE, "sensible:%s" % ip
    else:
        limite, cle = _V10_LIMITE_GLOBALE, "global:%s" % ip
    n = _v10_compteur_augmenter(cle, 60)
    if n > limite:
        reponse = jsonify(erreur="trop_de_requetes", limite_par_minute=limite,
                          message="Trop de requêtes : réessayez dans une minute.")
        reponse.status_code = 429
        reponse.headers["Retry-After"] = "60"
        return reponse
    return None


# Flask-Limiter (défense en profondeur, stockage Redis si disponible)
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    _V10_LIMITER = Limiter(
        key_func=get_remote_address,
        app=app,
        storage_uri=(_env("REDIS_URL", "") or "memory://"),
        default_limits=(["%d per minute" % _V10_LIMITE_GLOBALE] if _V10_RATELIMIT_ACTIF else []),
        enabled=_V10_RATELIMIT_ACTIF,
        headers_enabled=True,
        strategy="fixed-window",
    )
    app.jinja_env.globals["v10_limiter_actif"] = True
except Exception:
    _V10_LIMITER = None
    app.jinja_env.globals["v10_limiter_actif"] = False
    _V10_LOGGER.warning("Flask-Limiter absent : le limiteur applicatif interne reste actif.",
                        exc_info=False)


@app.errorhandler(429)
def _v10_trop_de_requetes(_err):
    return jsonify(erreur="trop_de_requetes", message="Limite de débit atteinte. Réessayez bientôt."), 429


# -----------------------------------------------------------------------------
# 5) CSRF SYSTÉMATIQUE — formulaire, en-tête X-CSRF-Token, corps JSON
# -----------------------------------------------------------------------------
_V10_CSRF_VALIDES = ("POST", "PUT", "PATCH", "DELETE")
_V10_CSRF_EXEMPTIONS = ("/webhooks/",)


def _v10_csrf_exempte(chemin: str) -> bool:
    return any(chemin.startswith(p) for p in _V10_CSRF_EXEMPTIONS)


# L'ancien contrôleur CSRF est RETIRÉ : il exemptait TOUTES les routes /api/
# (faille CSRF signalée) et rejetait à tort les webhooks signés de Mobile Money.
# La couche V10 le remplace par un contrôle unique qui couvre le formulaire,
# l'en-tête X-CSRF-Token et le corps JSON.
def _v10_retirer_ancien_csrf() -> None:
    for cle in list(app.before_request_funcs.keys()):
        app.before_request_funcs[cle] = [
            f for f in app.before_request_funcs[cle]
            if getattr(f, "__name__", "") != "_protection_csrf"]


_v10_retirer_ancien_csrf()
_V10_LOGGER.info("Ancien contrôleur CSRF retire : controle V10 unique actif (y compris /api/).")


@app.before_request
def _v10_csrf_global():
    """Le jeton est exigé partout où l'état change — /api/ JAMAIS exempté."""
    if request.method not in _V10_CSRF_VALIDES:
        return None
    if _v10_csrf_exempte(request.path):
        return None
    attendu = session.get("csrf")
    if not attendu:
        jeton_csrf()
        attendu = session.get("csrf")
    envoye = (request.headers.get("X-CSRF-Token") or request.headers.get("X-Csrf-Token")
              or request.form.get("csrf") or request.args.get("csrf") or "")
    if not envoye:
        try:
            corps = request.get_json(silent=True)
            if isinstance(corps, dict):
                envoye = str(corps.get("csrf") or corps.get("_csrf") or "")
        except Exception:
            envoye = ""
    if not envoye or not secrets.compare_digest(str(envoye), str(attendu)):
        _v10_log("WARNING", "csrf_refuse", chemin=request.path[:200], ip=_v10_ip(),
                 uid=session.get("uid"), methode=request.method)
        if request.path.startswith("/api/"):
            return jsonify(ok=False, erreur="csrf_invalide",
                           message="Jeton CSRF absent ou invalide pour cette route /api/."), 400
        return "Jeton CSRF invalide ou expiré. Rechargez la page puis réessayez.", 400
    return None


@app.after_request
def _v10_exposer_jeton_js(reponse):
    """Le jeton est rendu lisible par le JavaScript du site (même origine only)."""
    try:
        if reponse.mimetype == "text/html" and session.get("csrf"):
            reponse.headers["X-CSRF-Token-Courant"] = session["csrf"]
    except Exception:
        pass
    return reponse
# -----------------------------------------------------------------------------
# 6) SERVICE WORKER UNIQUE — fin du conflit des deux écouteurs 'fetch'
# -----------------------------------------------------------------------------
_SW_JS_V10 = r"""
/* ===========================================================================
   ToutBot Mundo — Service Worker UNIQUE (V10)
   Un seul écouteur 'fetch' : app-shell + cache du flux texte + API hors-ligne.
   Un gestionnaire 'push' qui appelle réellement self.registration.showNotification.
   =========================================================================== */
var TBM_CACHE = 'tbm-v10-texte';
var TBM_PAGES = ['/', '/connexion', '/inscription', '/tarifs'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(TBM_CACHE).then(function (c) {
    return Promise.all(TBM_PAGES.map(function (u) {
      return c.add(new Request(u, { cache: 'reload' })).catch(function () { return null; });
    }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (cles) {
    return Promise.all(cles.filter(function (k) {
      return k !== TBM_CACHE && k !== 'toutbot-v7-texte' && k !== 'tbm-cache-v1';
    }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  /* Flux hors-ligne : réseau d'abord, cache de secours (une seule stratégie). */
  if (url.pathname.indexOf('/api/hors-ligne/flux') === 0) {
    e.respondWith(fetch(req).then(function (r) {
      var copie = r.clone();
      caches.open(TBM_CACHE).then(function (c) { c.put('/api/hors-ligne/flux', copie); });
      return r;
    }).catch(function () {
      return caches.match('/api/hors-ligne/flux').then(function (t) {
        return t || new Response(JSON.stringify({ publications: [], hors_ligne: true }),
                                 { headers: { 'Content-Type': 'application/json' } });
      });
    }));
    return;
  }

  /* Les autres API ne sont jamais mises en cache. */
  if (url.pathname.indexOf('/api/') === 0) return;

  /* Pages et ressources : réseau d'abord, cache ensuite (contenu frais en priorité). */
  e.respondWith(fetch(req).then(function (r) {
    if (r && r.ok && r.type === 'basic') {
      var copie = r.clone();
      caches.open(TBM_CACHE).then(function (c) { c.put(req, copie); });
    }
    return r;
  }).catch(function () {
    return caches.match(req).then(function (t) { return t || caches.match('/'); });
  }));
});

/* ---- Notifications push : charge utile TEXTE uniquement, affichage réel ---- */
self.addEventListener('push', function (evenement) {
  var charge = { titre: 'ToutBot Mundo', texte: 'Nouvelle activité sur votre compte.', lien: '/' };
  try {
    if (evenement.data) { charge = Object.assign(charge, evenement.data.json()); }
  } catch (e) {
    try { charge.texte = evenement.data.text(); } catch (e2) {}
  }
  evenement.waitUntil(self.registration.showNotification(charge.titre || 'ToutBot Mundo', {
    body: charge.texte || '',
    tag: 'toutbot-mundo',
    renotify: true,
    requireInteraction: false,
    data: { lien: charge.lien || '/' },
    icon: undefined,
    badge: undefined
  }));
});

self.addEventListener('notificationclick', function (evenement) {
  evenement.notification.close();
  var cible = (evenement.notification.data && evenement.notification.data.lien) || '/';
  evenement.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (liste) {
    for (var i = 0; i < liste.length; i++) {
      if (liste[i].url.indexOf(self.location.origin) === 0 && 'focus' in liste[i]) {
        liste[i].navigate(cible);
        return liste[i].focus();
      }
    }
    if (clients.openWindow) return clients.openWindow(cible);
  }));
});

self.addEventListener('message', function (evenement) {
  if (evenement.data && evenement.data.type === 'notifier') {
    self.registration.showNotification(evenement.data.titre || 'ToutBot Mundo',
      { body: evenement.data.texte || '', tag: 'toutbot-mundo',
        data: { lien: evenement.data.lien || '/' } });
  }
});
"""

SW_JS = _SW_JS_V10  # le bloc V7 est neutralisé : un seul service worker, un seul écouteur
_V10_LOGGER.info("Service worker unifié (V10) : %d octets, un seul écouteur 'fetch' + push.",
                 len(SW_JS))


# -----------------------------------------------------------------------------
# 7) CORRECTIF DE LA RÉCURSION INFINIE DE marquer_paye
# -----------------------------------------------------------------------------
def _v10_retrouver_original(nom: str):
    """Remonte la chaîne _ORIG_* jusqu'à la VRAIE fonction métier (jamais un wrapper)."""
    vu = set()
    reference = None
    for cle in ("_ORIG_%s" % nom.upper(), "_ORIG_%s" % nom):
        candidat = globals().get(cle)
        if callable(candidat):
            reference = candidat
            break
    if reference is None:
        reference = globals().get(nom)
    garde = 0
    while callable(reference) and getattr(reference, "__name__", "") == nom and garde < 12:
        interne = getattr(reference, "_v10_cible", None)
        if interne is None or interne in vu:
            break
        vu.add(interne)
        reference = interne
        garde += 1
    return reference


_VRAI_MARQUER_PAYE = _v10_retrouver_original("marquer_paye")
_V10_MARQUER_PAYE_DEPTH = {"n": 0}


def marquer_paye(*args, **kwargs):  # noqa: F811 — remplace V6/V8 par une version sans récursion
    """Version V10 : appelle UNE fois la fonction métier d'origine, puis journalise.

    L'ancien code faisait pointer « _ORIG_MARQUER_PAYE » vers le wrapper lui-même
    → RecursionError et crash du serveur. Ici la référence est capturée une seule
    fois, avant toute redéfinition, et un garde-fou de profondeur interdit toute
    boucle résiduelle.
    """
    if _V10_MARQUER_PAYE_DEPTH["n"] > 0:
        _V10_LOGGER.error("Récursion détectée sur marquer_paye : appel imbriqué refusé.")
        return {"ok": False, "message": "Appel récursif refusé (garde-fou anti-récursion)."}
    _V10_MARQUER_PAYE_DEPTH["n"] += 1
    try:
        resultat = _VRAI_MARQUER_PAYE(*args, **kwargs)
    finally:
        _V10_MARQUER_PAYE_DEPTH["n"] -= 1
    try:
        if isinstance(resultat, dict) and resultat.get("ok"):
            user_id = _param_v6(args, kwargs, 0, "user_id", None)
            decideur = _param_v6(args, kwargs, 1, "decideur_id", None)
            journal_action2(user_id, "transfert", objet="versement Mobile Money exécuté",
                            details="%s ticket(s) / %s %s versés par l'administrateur"
                                    % (resultat.get("tickets"), resultat.get("fcfa"), CONFIG["DEVISE"]),
                            montant=float(resultat.get("fcfa") or 0))
            journal_action("versement", cible="utilisateur #%s" % user_id,
                           details="%s %s" % (resultat.get("fcfa"), CONFIG["DEVISE"]),
                           utilisateur=decideur)
            _v10_log("INFO", "versement_execute", user_id=user_id, decideur=decideur,
                     fcfa=resultat.get("fcfa"))
    except Exception:
        _V10_LOGGER.warning("journalisation du versement impossible", exc_info=False)
    return resultat


marquer_paye._v10_cible = _VRAI_MARQUER_PAYE
_ORIG_MARQUER_PAYE = _VRAI_MARQUER_PAYE  # la sauvegarde pointe la vraie fonction métier
_V10_LOGGER.info("marquer_paye sécurisé : cible = %s",
                 getattr(_VRAI_MARQUER_PAYE, "__qualname__", _VRAI_MARQUER_PAYE))


# -----------------------------------------------------------------------------
# 8) HISTORIQUE PERSONNEL EXHAUSTIF
# -----------------------------------------------------------------------------
LIBELLES_HISTO.update({
    "navigation": {"icone": "🧭", "nom": "Navigation"},
    "page_vue": {"icone": "👁️", "nom": "Page consultée"},
    "recherche": {"icone": "🔎", "nom": "Recherche / aller à"},
    "administration": {"icone": "🛡️", "nom": "Action d'administration"},
    "securite": {"icone": "🚨", "nom": "Sécurité / modération"},
    "export": {"icone": "⬇️", "nom": "Export de données"},
    "sauvegarde": {"icone": "💾", "nom": "Sauvegarde"},
    "webhook": {"icone": "🔔", "nom": "Confirmation de paiement (webhook)"},
    "reglement": {"icone": "🧾", "nom": "Règlement financier"},
    "signature": {"icone": "✍️", "nom": "Approbation à double signature"},
    "profil": {"icone": "🪪", "nom": "Profil / réglages"},
    "groupe": {"icone": "👥", "nom": "Groupe"},
    "notification": {"icone": "🔔", "nom": "Notification reçue"},
    "systeme": {"icone": "⚙️", "nom": "Système"},
})
HISTORIQUE_ACTIONS = tuple(LIBELLES_HISTO.keys())

_HISTO_SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS historique_utilisateur (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    action     TEXT NOT NULL,
    objet      TEXT NOT NULL DEFAULT '',
    details    TEXT NOT NULL DEFAULT '',
    montant    REAL,
    adresse_ip TEXT NOT NULL DEFAULT '',
    cree_le    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_histo_user   ON historique_utilisateur(user_id, id);
CREATE INDEX IF NOT EXISTS idx_histo_action ON historique_utilisateur(user_id, action);
CREATE INDEX IF NOT EXISTS idx_histo_date   ON historique_utilisateur(user_id, cree_le);
"""


def _v10_initialiser_historique() -> None:
    for script in (_HISTO_SCHEMA_V10,):
        try:
            conn = sqlite3.connect(CONFIG["DB"])
            conn.executescript(script)
            conn.commit()
            conn.close()
        except Exception:
            _V10_LOGGER.warning("initialisation de la table d'historique impossible", exc_info=True)


_v10_initialiser_historique()

_V10_HISTO_MEMOIRE: Dict[str, float] = {}
_V10_HISTO_VERROU = threading.Lock()


def _v10_histo(utilisateur, action: str, objet: str = "", details: str = "",
               montant=None, ip: Optional[str] = None) -> None:
    """Écrit une ligne d'historique PERSONNEL. Ne lève jamais, n'écrase jamais."""
    try:
        uid = int(utilisateur) if utilisateur else None
    except (TypeError, ValueError):
        uid = None
    if uid is None:
        return
    try:
        conn = db()
        conn.execute(
            "INSERT INTO historique_utilisateur"
            " (user_id, action, objet, details, montant, adresse_ip, cree_le)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, (action or "navigation")[:40], (objet or "")[:160], (details or "")[:500],
             (float(montant) if montant not in (None, "") else None),
             (ip or _v10_ip())[:60], maintenant()))
        conn.commit()
    except Exception:
        _V10_LOGGER.debug("historique personnel indisponible", exc_info=False)


def _v10_histo_une_fois(utilisateur, action: str, objet: str, delai: int = 45) -> None:
    """Anti-doublon : une même visite n'est pas consignée deux fois d'affilée."""
    cle = "%s|%s|%s" % (utilisateur, action, objet)
    maintenant_ts = time.time()
    with _V10_HISTO_VERROU:
        dernier = _V10_HISTO_MEMOIRE.get(cle, 0)
        if maintenant_ts - dernier < delai:
            return
        _V10_HISTO_MEMOIRE[cle] = maintenant_ts
        if len(_V10_HISTO_MEMOIRE) > 20000:
            _V10_HISTO_MEMOIRE.clear()
    _v10_histo(utilisateur, action, objet=objet)


# --- Interception des deux journaux bas niveau : couverture automatique ------
_VRAI_JOURNAL_ACTION = journal_action
_VRAI_JOURNAL_ACTION2 = journal_action2


# Le journal GLOBAL n'alimente l'historique PERSONNEL que pour les évènements
# d'administration : les actes sociaux (connexion, j'aime, message…) disposent
# déjà de leur propre journal_action2 — on évite ainsi tout doublon d'écriture.
_V10_ADMIN_VERS_HISTO = {
    "versement": "reglement", "paiement": "reglement",
    "export_journal": "export", "acces_refuse": "securite",
    "sanction": "securite", "blocage": "securite", "bloquer": "securite",
    "debloquer": "utilisateurs", "moderation": "securite",
    "webhook": "webhook", "approbation": "signature", "sauvegarde": "sauvegarde",
}


def journal_action(*args, **kwargs):  # noqa: F811
    resultat = _VRAI_JOURNAL_ACTION(*args, **kwargs)
    try:
        action = str(_param_v6(args, kwargs, 0, "action", ""))
        mappe = _V10_ADMIN_VERS_HISTO.get(action)
        if mappe:
            _v10_histo(_param_v6(args, kwargs, 3, "utilisateur", None) or session.get("uid"),
                       mappe, objet=str(_param_v6(args, kwargs, 1, "cible", ""))[:160],
                       details=str(_param_v6(args, kwargs, 2, "details", ""))[:500])
    except Exception:
        pass
    return resultat


def journal_action2(*args, **kwargs):  # noqa: F811
    resultat = _VRAI_JOURNAL_ACTION2(*args, **kwargs)
    try:
        action = _param_v6(args, kwargs, 1, "action", "navigation")
        if action not in LIBELLES_HISTO:
            _v10_histo(_param_v6(args, kwargs, 0, "utilisateur", None), "navigation",
                       objet=str(action)[:160],
                       details=str(_param_v6(args, kwargs, 2, "objet", ""))[:500])
    except Exception:
        pass
    return resultat


# --- Traçage automatique des pages consultées --------------------------------
_V10_ENDPOINTS_IGNORES = {"static", "service_worker", "manifeste", "api_non_lus", "api_ping",
                          "api_horloge", "api_sante", "v10_health", "v10_ready",
                          "api_v10_statut", "api_v10_historique_stats", "api_mon_historique"}
_V10_NOMS_PAGES = {
    "fil": "l'accueil (le fil)", "tarifs": "les tarifs et paiements",
    "portefeuille": "son portefeuille", "messages": "sa messagerie",
    "mon_historique": "son historique personnel", "plaintes": "les plaintes / l'assistance",
    "recherche_page": "la recherche", "chat_page": "l'assistant IA",
    "parametres": "ses réglages", "admin": "le tableau de bord d'administration",
    "profil_public": "un profil public", "tendances": "les tendances",
    "boutique": "la boutique de contenus", "prets": "les prêts entre membres",
    "live_liste": "les lives textuels", "notifications": "ses notifications",
    "stories": "les stories", "sondages": "les sondages", "groupes": "ses groupes",
    "recurrents": "les pourboires récurrents", "reputation": "sa réputation",
    "reversements_admin": "les reversements de l'application",
    "admin_journal": "le journal d'activité global", "admin_audit": "le registre d'audit",
    "admin_alertes": "le centre d'alertes", "admin_roles": "les rôles et permissions",
}


@app.before_request
def _v10_tracer_navigation():
    if request.method != "GET" or "uid" not in session:
        return None
    endpoint = request.endpoint or ""
    if endpoint in _V10_ENDPOINTS_IGNORES or endpoint.startswith("static"):
        return None
    libelle = _V10_NOMS_PAGES.get(endpoint)
    if libelle is None:
        if endpoint.startswith("admin"):
            libelle = "l'administration (%s)" % endpoint.replace("_", " ")
        else:
            libelle = request.path.split("?")[0][:120]
    if endpoint == "mon_historique":
        return None  # consulter son historique n'encombre pas l'historique
    _v10_histo_une_fois(session["uid"], "page_vue", libelle)
    return None


# --- Nouvelle page d'historique : timeline + statistiques + actions ----------
@app.route("/mon-historique/timeline")
def v10_mon_historique_timeline():
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    lignes = [dict(l) for l in conn.execute(
        "SELECT * FROM historique_utilisateur WHERE user_id = ? ORDER BY id DESC LIMIT 300",
        (me["id"],)).fetchall()]
    jours: Dict[str, List[Dict[str, Any]]] = {}
    for ligne in lignes:
        jours.setdefault((ligne.get("cree_le") or "")[:10], []).append(ligne)
    stats = conn.execute(
        "SELECT action, COUNT(*) AS n, COALESCE(SUM(montant),0) AS m"
        " FROM historique_utilisateur WHERE user_id = ? GROUP BY action ORDER BY n DESC",
        (me["id"],)).fetchall()
    return page("v10_timeline.html", titre="Ma chronologie personnelle", jours=jours,
                stats=[dict(s) for s in stats], libelles=LIBELLES_HISTO,
                actions_total=sum(int(s["n"]) for s in stats) if stats else 0)


@app.route("/api/mon-historique/statistiques")
def v10_api_historique_stats():
    me = utilisateur_courant()
    if me is None:
        return jsonify(ok=False, erreur="connexion_requise"), 401
    conn = db()
    par_action = [dict(l) for l in conn.execute(
        "SELECT action, COUNT(*) AS n, COALESCE(SUM(montant),0) AS montant"
        " FROM historique_utilisateur WHERE user_id = ? GROUP BY action ORDER BY n DESC",
        (me["id"],)).fetchall()]
    par_jour = [dict(l) for l in conn.execute(
        "SELECT substr(cree_le,1,10) AS jour, COUNT(*) AS n FROM historique_utilisateur"
        " WHERE user_id = ? GROUP BY jour ORDER BY jour DESC LIMIT 60", (me["id"],)).fetchall()]
    totaux = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(montant),0) AS montant FROM historique_utilisateur"
        " WHERE user_id = ?", (me["id"],)).fetchone()
    return jsonify(ok=True, pseudo=me["pseudo"], total=int(totaux["n"]),
                   montant_cumule=round(float(totaux["montant"]), 2),
                   par_action=par_action, par_jour=par_jour,
                   libelles={c: v["nom"] for c, v in LIBELLES_HISTO.items()})


@app.route("/mon-historique/supprimer/<int:ligne_id>", methods=["POST"])
def v10_historique_supprimer(ligne_id):
    redirection = exiger_connexion()
    if redirection:
        return redirection
    me = utilisateur_courant()
    conn = db()
    conn.execute("DELETE FROM historique_utilisateur WHERE id = ? AND user_id = ?",
                 (ligne_id, me["id"]))
    conn.commit()
    session["flash"] = "Ligne d'historique supprimée."
    return redirect(url_for("mon_historique"))
# -----------------------------------------------------------------------------
# 9) POSTGRESQL — adaptateur de compatibilité (DATABASE_URL)
# -----------------------------------------------------------------------------
# SQLite reste la base par défaut (développement). Dès que DATABASE_URL pointe
# vers PostgreSQL, l'application bascule sur un adaptateur qui présente la même
# interface que sqlite3 (connect/cursor/execute/executescript/row_factory) et
# traduit les dialectes (?, INSERT OR IGNORE, AUTOINCREMENT, PRAGMA…).
_V10_PG_DSN = _env("DATABASE_URL", "") or _env("TOUTBOT_DATABASE_URL", "")
if _V10_PG_DSN.startswith("postgres://"):
    _V10_PG_DSN = "postgresql://" + _V10_PG_DSN[len("postgres://"):]
_V10_PG_ACTIF = _V10_PG_DSN.startswith("postgresql://")


class _V10_PgIntegrityError(Exception):
    pass


class _V10_PgOperationalError(Exception):
    pass


class _V10_PgError(Exception):
    pass


class _V10_PgRow:
    """Ligne type sqlite3.Row : accès par nom ET par index."""

    __slots__ = ("_valeurs", "_index")

    def __init__(self, colonnes, valeurs):
        self._valeurs = list(valeurs)
        self._index = {c: i for i, c in enumerate(colonnes)}

    def __getitem__(self, cle):
        if isinstance(cle, int):
            return self._valeurs[cle]
        return self._valeurs[self._index[cle]]

    def keys(self):
        return list(self._index.keys())

    def __iter__(self):
        return iter(self._valeurs)

    def __len__(self):
        return len(self._valeurs)

    def __contains__(self, cle):
        return cle in self._index

    def get(self, cle, defaut=None):
        return self[cle] if cle in self._index else defaut

    def __repr__(self):
        return "<Row %s>" % dict(zip(self._index.keys(), self._valeurs))


_V10_PG_TRADUCTIONS = (
    ("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"),
    ("AUTOINCREMENT", ""),
    ("INSERT OR IGNORE INTO", "INSERT INTO"),
    ("INSERT OR REPLACE INTO", "INSERT INTO"),
    (" REAL", " DOUBLE PRECISION"),
    ("REAL NOT NULL", "DOUBLE PRECISION NOT NULL"),
    ("BLOB", "BYTEA"),
)


def _v10_pg_sql(requete: str) -> str:
    """Traduit une requête écrite pour SQLite vers PostgreSQL."""
    sql = requete
    for avant, apres in _V10_PG_TRADUCTIONS:
        sql = sql.replace(avant, apres)
    # « ? » → « %s » (approximation assumée : un « ? » littéral serait rare ici)
    sql = sql.replace("?", "%s")
    if sql.strip().upper().startswith("INSERT INTO") and "OR IGNORE" in requete.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class _V10_PgCursor:
    def __init__(self, curseur):
        self._c = curseur
        self.lastrowid = None

    def execute(self, requete, params=()):
        sql = _v10_pg_sql(requete)
        try:
            if params in (None, (), []):
                self._c.execute(sql)
            else:
                self._c.execute(sql, tuple(params))
        except Exception as exc:  # pragma: no cover - dépend du serveur PG
            nom = type(exc).__name__
            if "UniqueViolation" in nom or "Integrity" in nom:
                raise _V10_PgIntegrityError(str(exc)) from exc
            raise _V10_PgOperationalError(str(exc)) from exc
        if "RETURNING" not in sql.upper() and sql.strip().upper().startswith("INSERT"):
            try:
                self._c.execute("SELECT LASTVAL()")
                self.lastrowid = self._c.fetchone()[0]
            except Exception:
                self.lastrowid = None
        return self

    def executescript(self, script: str):
        for instruction in [i for i in script.split(";") if i.strip()]:
            haut = instruction.strip().upper()
            if haut.startswith("PRAGMA"):
                continue
            self.execute(instruction)
        return self

    def _lignes(self, brut):
        colonnes = [d[0] for d in (self._c.description or [])]
        return [_V10_PgRow(colonnes, l) for l in brut]

    def fetchone(self):
        brut = self._c.fetchone()
        if brut is None:
            return None
        return self._lignes([brut])[0]

    def fetchall(self):
        return self._lignes(self._c.fetchall())

    def fetchmany(self, taille=1):
        return self._lignes(self._c.fetchmany(taille))

    def close(self):
        self._c.close()

    def __iter__(self):
        return iter(self.fetchall())


class _V10_PgConnection:
    row_factory = None

    def __init__(self, dsn):
        try:
            import psycopg  # type: ignore
            self._raw = psycopg.connect(dsn, autocommit=False)
        except Exception:
            import psycopg2  # type: ignore
            self._raw = psycopg2.connect(dsn)
        self._raw.autocommit = False

    def cursor(self):
        return _V10_PgCursor(self._raw.cursor())

    def execute(self, requete, params=()):
        curseur = self.cursor()
        curseur.execute(requete, params)
        return curseur

    def executescript(self, script):
        return self.cursor().executescript(script)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


if _V10_PG_ACTIF:
    try:
        import sqlite3 as _sqlite3_module  # noqa: F401

        def _v10_pg_connect(*_args, **_kwargs):
            return _V10_PgConnection(_V10_PG_DSN)

        sqlite3.connect = _v10_pg_connect  # type: ignore[assignment]
        sqlite3.IntegrityError = _V10_PgIntegrityError  # type: ignore[assignment]
        sqlite3.OperationalError = _V10_PgOperationalError  # type: ignore[assignment]
        _V10_LOGGER.info("PostgreSQL actif (DATABASE_URL) : adaptateur de compatibilité sqlite3 chargé.")
    except Exception:
        _V10_PG_ACTIF = False
        _V10_LOGGER.warning("Bascule PostgreSQL impossible : repli sur SQLite.", exc_info=True)
else:
    _V10_LOGGER.info("Base : SQLite (%s). Définir DATABASE_URL pour passer en PostgreSQL.",
                     CONFIG["DB"])


def _v10_verifier_schema() -> Dict[str, Any]:
    """Vérifie au démarrage que les tables essentielles existent (fail-fast)."""
    essentielles = ("users", "posts", "journal", "historique_utilisateur",
                    "abonnements", "pourboires", "portefeuille", "reglements_v10")
    manquantes = []
    try:
        existantes = {l[0] for l in db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()} \
            if not _V10_PG_ACTIF else {l[0] for l in db().execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()}
        manquantes = [t for t in essentielles if t not in existantes]
    except Exception:
        _V10_LOGGER.warning("vérification du schéma impossible", exc_info=False)
    return {"base": "postgresql" if _V10_PG_ACTIF else "sqlite", "tables_essentielles": list(essentielles),
            "manquantes": manquantes, "ok": not manquantes}


# -----------------------------------------------------------------------------
# 10) WEBHOOKS MOBILE MONEY — vérification de signature + réconciliation
# -----------------------------------------------------------------------------
_WEBHOOKS_SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS reglements_v10 (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    type_element  TEXT NOT NULL,
    reference     TEXT NOT NULL,
    montant_fcfa  REAL NOT NULL DEFAULT 0,
    operateur     TEXT NOT NULL DEFAULT '',
    telephone     TEXT NOT NULL DEFAULT '',
    statut        TEXT NOT NULL DEFAULT 'a_approuver',
    signature_ok  INTEGER NOT NULL DEFAULT 0,
    approbations  INTEGER NOT NULL DEFAULT 0,
    approuve_par  TEXT NOT NULL DEFAULT '',
    charge_brute  TEXT NOT NULL DEFAULT '',
    cree_le       TEXT NOT NULL,
    maj_le        TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_regl_ref ON reglements_v10(reference, type_element);
CREATE INDEX IF NOT EXISTS idx_regl_statut ON reglements_v10(statut, id);
"""


def _v10_init_reglements() -> None:
    try:
        conn = sqlite3.connect(CONFIG["DB"])
        conn.executescript(_WEBHOOKS_SCHEMA_V10)
        conn.commit()
        conn.close()
    except Exception:
        _V10_LOGGER.warning("initialisation de reglements_v10 impossible", exc_info=True)


_v10_init_reglements()


def _v10_signature_valide(corps_brut: bytes, entete: str, secret: str) -> bool:
    """HMAC-SHA256 hexadécimal du corps BRUT (jamais re-sérialisé)."""
    if not secret:
        return False
    import hmac
    import hashlib
    attendu = hmac.new(secret.encode(), corps_brut, hashlib.sha256).hexdigest()
    fourni = (entete or "").strip().replace("sha256=", "")
    return bool(fourni) and hmac.compare_digest(attendu, fourni)


def _v10_traiter_paiement(type_element: str, reference: str, montant: float,
                          operateur: str, telephone: str, charge) -> Dict[str, Any]:
    """Réconciliation : retrouve l'écriture réelle et la valide (ou la met en
    attente de double signature au-delà du plafond configuré)."""
    conn = db()
    double = float(montant or 0) >= _V10_DOUBLE_SIGNATURE
    statut = "a_approuver" if double else "approuve"
    conn.execute(
        "INSERT INTO reglements_v10 (type_element, reference, montant_fcfa, operateur,"
        " telephone, statut, signature_ok, approbations, charge_brute, cree_le, maj_le)"
        " VALUES (?,?,?,?,?,?,1,?,?,?,?)"
        " ON CONFLICT(reference, type_element) DO UPDATE SET"
        " statut = excluded.statut, maj_le = excluded.maj_le, signature_ok = 1",
        (type_element, reference[:120], float(montant or 0), operateur[:40], telephone[:40],
         statut, 0 if double else 1, json.dumps(charge, ensure_ascii=False)[:2000],
         maintenant(), maintenant()))
    conn.commit()
    lignes = conn.execute("SELECT * FROM reglements_v10 WHERE reference = ? AND type_element = ?",
                          (reference[:120], type_element)).fetchall()
    reglement = dict(lignes[0]) if lignes else {}
    journal_action2(None, "webhook", objet="%s — réf. %s" % (type_element, reference),
                    details="Confirmation reçue de l'opérateur %s" % operateur)
    journal_action("webhook", cible=reference, details="%s / %s FCFA / statut %s"
                   % (operateur, montant, statut))
    if double:
        _v10_log("WARNING", "double_signature_requise", reference=reference,
                 montant=montant, seuil=_V10_DOUBLE_SIGNATURE)
        try:
            for admin in db().execute("SELECT id FROM users WHERE est_admin = 1").fetchall():
                notifier(admin["id"], "paiement",
                         "Confirmation de %s FCFA reçue (réf. %s) : double signature requise."
                         % (montant, reference), "/admin/v10/reglements")
        except Exception:
            pass
        return {"ok": True, "statut": statut, "double_signature_requise": True,
                "reglement_id": reglement.get("id"), "reference": reference}
    resultat = _v10_executer_reglement(reglement) if reglement else {"ok": False}
    return {"ok": True, "statut": "approuve", "double_signature_requise": False,
            "execution": resultat, "reference": reference}


def _v10_executer_reglement(reglement) -> Dict[str, Any]:
    """Crédite réellement le créateur : appel à la validation métier d'origine."""
    if not reglement:
        return {"ok": False, "motif": "reglement_introuvable"}
    type_element = reglement.get("type_element") or ""
    reference = reglement.get("reference") or ""
    try:
        if type_element == "abonnement":
            ligne = db().execute("SELECT id FROM abonnements WHERE reference = ?",
                                 (reference,)).fetchone()
            if ligne is None:
                return {"ok": False, "motif": "abonnement_introuvable"}
            res = valider_abonnement(int(ligne["id"]), None)
        elif type_element == "pourboire":
            identifiant = int(str(reference).replace("TIP-", "") or 0)
            res = valider_pourboire(identifiant, None)
        else:
            return {"ok": False, "motif": "type_inconnu"}
    except Exception as exc:
        _V10_LOGGER.warning("exécution du règlement impossible", exc_info=False)
        return {"ok": False, "motif": "erreur_execution", "detail": str(exc)[:200]}
    if res is None:
        return {"ok": False, "motif": "deja_valide_ou_inconnu"}
    conn = db()
    conn.execute("UPDATE reglements_v10 SET statut='execute', maj_le=? WHERE id=?",
                 (maintenant(), reglement.get("id")))
    conn.commit()
    _v10_log("INFO", "reglement_execute", reference=reference, type=type_element,
             net=res.get("net"), commission=res.get("commission"))
    return {"ok": True, "net": res.get("net"), "commission": res.get("commission")}


@app.route("/webhooks/mtn", methods=["POST"])
def v10_webhook_mtn():
    return _v10_webhook("mtn", _V10_WEBHOOKS_MTN)


@app.route("/webhooks/moov", methods=["POST"])
def v10_webhook_moov():
    return _v10_webhook("moov", _V10_WEBHOOKS_MOOV)


def _v10_webhook(operateur: str, secret: str):
    """Endpoint de confirmation — refuse tout ce qui n'est pas signé."""
    corps = request.get_data() or b""
    entete = request.headers.get("X-ToutBot-Signature") or request.headers.get("X-Signature") or ""
    if not secret:
        _v10_log("WARNING", "webhook_non_configure", operateur=operateur)
        return jsonify(ok=False, statut="non_configure",
                       message=("Secret de webhook %s absent : aucune vérification de signature "
                                "n'est possible, donc aucun paiement n'est validé. Renseignez "
                                "TOUTBOT_WEBHOOK_SECRET_%s." % (operateur.upper(), operateur.upper()))), 503
    if not _v10_signature_valide(corps, entete, secret):
        _v10_log("WARNING", "webhook_signature_invalide", operateur=operateur, ip=_v10_ip())
        journal_action("webhook", cible=operateur, details="Signature invalide — paiement refusé")
        return jsonify(ok=False, statut="signature_invalide"), 401
    try:
        charge = json.loads(corps.decode("utf-8") or "{}")
    except Exception:
        return jsonify(ok=False, statut="corps_illisible"), 400
    reference = str(charge.get("reference") or charge.get("ref") or "").strip()
    montant = float(charge.get("montant") or charge.get("amount") or 0)
    if not reference:
        return jsonify(ok=False, statut="reference_absente"), 400
    type_element = "pourboire" if reference.upper().startswith("TIP-") else "abonnement"
    resultat = _v10_traiter_paiement(type_element, reference, montant, operateur,
                                     str(charge.get("telephone") or ""), charge)
    _v10_log("INFO", "webhook_traite", operateur=operateur, reference=reference, montant=montant)
    return jsonify(ok=True, reglement=resultat), 200


@app.route("/admin/v10/reglements")
def v10_reglements_admin():
    me = utilisateur_courant()
    if me is None or not me["est_admin"]:
        return "Réservé à l'administrateur.", 403
    lignes = [dict(l) for l in db().execute(
        "SELECT * FROM reglements_v10 ORDER BY id DESC LIMIT 300").fetchall()]
    return page("v10_reglements.html", titre="Règlements Mobile Money",
                lignes=lignes, seuil=_V10_DOUBLE_SIGNATURE,
                webhooks={"mtn": bool(_V10_WEBHOOKS_MTN), "moov": bool(_V10_WEBHOOKS_MOOV)})


@app.route("/admin/v10/reglement/<int:reglement_id>/approuver", methods=["POST"])
def v10_approuver_reglement(reglement_id):
    permission = exiger_permission("comptabilite")
    if permission:
        return permission
    me = utilisateur_courant()
    conn = db()
    ligne = conn.execute("SELECT * FROM reglements_v10 WHERE id = ?", (reglement_id,)).fetchone()
    if ligne is None:
        return "Règlement introuvable.", 404
    approbations = int(ligne["approbations"]) + 1
    approuve_par = (str(ligne["approuve_par"]) + "," + str(me["id"])).strip(",")
    # Double signature : deux approbateurs DISTINCTS exigés au-delà du plafond
    distincts = {p for p in approuve_par.split(",") if p}
    if len(distincts) < 2 and float(ligne["montant_fcfa"]) >= _V10_DOUBLE_SIGNATURE:
        conn.execute("UPDATE reglements_v10 SET approbations=?, approuve_par=?, maj_le=? WHERE id=?",
                     (approbations, approuve_par[:200], maintenant(), reglement_id))
        conn.commit()
        session["flash"] = ("Première approbation enregistrée : un SECOND administrateur "
                            "distinct doit approuver pour exécuter ce montant.")
        return redirect(url_for("v10_reglements_admin"))
    execution = _v10_executer_reglement(dict(ligne))
    journal_action("approbation", cible="règlement #%d" % reglement_id,
                   details="Approbations : %s — %s" % (approuve_par, execution))
    session["flash"] = "Règlement exécuté : %s" % (execution.get("net") or execution.get("motif"))
    return redirect(url_for("v10_reglements_admin"))


# -----------------------------------------------------------------------------
# 11) INSCRIPTION ADMINISTRATEUR (pseudo + mot de passe), une seule fois
# -----------------------------------------------------------------------------
def _v10_nombre_admins() -> int:
    try:
        return int(db().execute("SELECT COUNT(*) AS n FROM users WHERE est_admin = 1").fetchone()["n"])
    except Exception:
        return 0


@app.route("/admin/inscription", methods=["GET", "POST"])
def v10_admin_inscription():
    """Voie d'entrée dédiée à l'administrateur : pseudo + mot de passe.

    Sécurité : exige TOUTBOT_ADMIN_BOOTSTRAP_TOKEN et n'est utilisable QUE tant
    qu'aucun administrateur n'existe (une seule création possible par installation).
    """
    deja = _v10_nombre_admins() > 0
    if request.method == "POST":
        if deja:
            session["flash"] = ("Un administrateur existe déjà : cette voie est définitivement "
                                "fermée. Connectez-vous par la page de connexion.")
            return redirect(url_for("connexion"))
        if not _V10_BOOTSTRAP:
            session["flash"] = ("TOUTBOT_ADMIN_BOOTSTRAP_TOKEN n'est pas défini : "
                                "la création du premier administrateur est refusée.")
            return page("v10_admin_inscription.html", titre="Inscription administrateur",
                        deja=False, jeton_requis=True)
        fourni = (request.form.get("jeton_amorcage") or "").strip()
        if not secrets.compare_digest(fourni, _V10_BOOTSTRAP):
            _v10_bannir(_v10_ip(), _V10_FENETRE_BAN, "jeton d'amorçage incorrect")
            session["flash"] = "Jeton d'amorçage incorrect : adresse IP bannie 30 minutes."
            return redirect(url_for("connexion"))
        try:
            pseudo = valider_surnom(request.form.get("pseudo", "") or request.form.get("identifiant", ""))
            uid = creer_utilisateur(request.form.get("telephone", ""), pseudo,
                                    request.form.get("mot_de_passe", ""), est_admin=True)
        except ValueError as exc:
            session["flash"] = str(exc)
            return page("v10_admin_inscription.html", titre="Inscription administrateur",
                        deja=False, jeton_requis=True)
        try:
            journal_action2(uid, "administration", objet="compte administrateur créé",
                            details="Premier administrateur de l'installation (pseudo + mot de passe).")
        except Exception:
            pass
        _v10_log("WARNING", "admin_cree", pseudo=pseudo, uid=uid, ip=_v10_ip())
        session.clear()
        session["uid"] = uid
        session["csrf"] = secrets.token_hex(16)
        session["flash"] = "Compte administrateur créé. Bienvenue, %s." % pseudo
        return redirect(url_for("admin"))
    return page("v10_admin_inscription.html", titre="Inscription administrateur",
                deja=deja, jeton_requis=not _V10_BOOTSTRAP)


TEMPLATES["v10_admin_inscription.html"] = """{% block contenu %}
<div class="carte v10-clic">
<h1>🛡️ Inscription de l'administrateur</h1>
{% if deja %}
  <p class="muet">Un administrateur est déjà enregistré : cette voie d'inscription est close
  définitivement pour cette installation.</p>
  <p><a class="btn" href="{{ url_for('connexion') }}">Aller à la connexion</a></p>
{% else %}
  <p class="muet">Voie d'entrée réservée au propriétaire de l'application : pseudo unique
  (6 lettres) et mot de passe. Utilisable <b>une seule fois</b>, avant le premier administrateur.</p>
  {% if jeton_requis %}<p class="muet">⚠️ Renseignez <code>TOUTBOT_ADMIN_BOOTSTRAP_TOKEN</code>
  dans l'environnement du serveur : sans ce jeton, la création est refusée.</p>{% endif %}
  <form method="post">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <p><label>Jeton d'amorçage du serveur</label>
       <input name="jeton_amorcage" type="password" autocomplete="off" required></p>
    <p><label>Pseudo de l'administrateur (6 lettres)</label><input name="pseudo" required></p>
    <p><label>Mot de passe (6 caractères minimum)</label>
       <input name="mot_de_passe" type="password" required></p>
    <p><label>Téléphone Mobile Money (facultatif)</label><input name="telephone"></p>
    <button>Créer le compte administrateur</button>
  </form>
{% endif %}
</div>{% endblock %}"""


# -----------------------------------------------------------------------------
# 12) SANTÉ, DISPONIBILITÉ, SAUVEGARDES, TÂCHES DE FOND
# -----------------------------------------------------------------------------
@app.route("/health")
def v10_health():
    etat = {"ok": True, "version": V10["version"], "mode": _V10_MODE,
            "uptime_s": int(time.time() - _V10_T0), "base": "postgresql" if _V10_PG_ACTIF else "sqlite"}
    try:
        db().execute("SELECT 1").fetchone()
        etat["base_joignable"] = True
    except Exception:
        etat["base_joignable"] = False
        etat["ok"] = False
    return jsonify(etat), (200 if etat["ok"] else 503)


@app.route("/ready")
def v10_ready():
    etat = _v10_verifier_schema()
    etat.update({"version": V10["version"], "rate_limit": _V10_RATELIMIT_ACTIF,
                 "redis": _V10_REDIS is not None, "https_force": _V10_HTTPS})
    etat["ok"] = bool(etat.get("ok"))
    return jsonify(etat), (200 if etat["ok"] else 503)


@app.route("/api/v10/statut")
def v10_statut():
    return jsonify(ok=True, version=V10["version"], mode=_V10_MODE,
                   base="postgresql" if _V10_PG_ACTIF else "sqlite",
                   redis=_V10_REDIS is not None,
                   rate_limit=_V10_RATELIMIT_ACTIF,
                   doubles_signatures=_V10_DOUBLE_SIGNATURE,
                   services={"webhooks_mtn": bool(_V10_WEBHOOKS_MTN),
                             "webhooks_moov": bool(_V10_WEBHOOKS_MOOV),
                             "bootstrap_admin": bool(_V10_BOOTSTRAP),
                             "push_vapid": bool(CONFIG_V7.get("VAPID_PRIVATE")),
                             "llm": bool(_env("TOUTBOT_LLM_KEY", "") or CONFIG.get("POLLINATIONS_KEY"))})


_V10_DOSSIER_SAUVEGARDES = _env("TOUTBOT_BACKUP_DIR",
                                os.path.join(os.path.dirname(os.path.abspath(__file__)), "sauvegardes"))


def _v10_sauvegarder(motif: str = "manuelle") -> Dict[str, Any]:
    """Sauvegarde JSON horodatée des tables métier + copie binaire du fichier SQLite."""
    os.makedirs(_V10_DOSSIER_SAUVEGARDES, exist_ok=True)
    horodatage = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tables = ("users", "posts", "comments", "likes", "abonnements", "pourboires",
              "portefeuille", "paiements", "recettes", "journal", "historique_utilisateur",
              "reglements_v10")
    contenu: Dict[str, Any] = {"horodatage": horodatage, "motif": motif, "tables": {}}
    conn = db()
    for table in tables:
        try:
            contenu["tables"][table] = [dict(l) for l in conn.execute(
                "SELECT * FROM %s LIMIT 100000" % table).fetchall()]
        except Exception:
            contenu["tables"][table] = []
    chemin_json = os.path.join(_V10_DOSSIER_SAUVEGARDES, "toutbot-%s.json" % horodatage)
    with open(chemin_json, "w", encoding="utf-8") as fichier:
        json.dump(contenu, fichier, ensure_ascii=False)
    resultat = {"ok": True, "fichier": chemin_json, "tables": len(contenu["tables"]),
                "lignes": sum(len(v) for v in contenu["tables"].values()), "motif": motif}
    if not _V10_PG_ACTIF:
        try:
            import shutil
            chemin_db = os.path.join(_V10_DOSSIER_SAUVEGARDES, "toutbot-%s.db" % horodatage)
            shutil.copy2(CONFIG["DB"], chemin_db)
            resultat["copie_base"] = chemin_db
        except Exception:
            _V10_LOGGER.warning("copie binaire de la base impossible", exc_info=False)
    # Rétention : 30 sauvegardes conservées
    try:
        fichiers = sorted(f for f in os.listdir(_V10_DOSSIER_SAUVEGARDES) if f.startswith("toutbot-"))
        for ancien in fichiers[:-30]:
            os.remove(os.path.join(_V10_DOSSIER_SAUVEGARDES, ancien))
    except Exception:
        pass
    _v10_log("INFO", "sauvegarde_effectuee", fichiers=resultat["fichier"], lignes=resultat["lignes"])
    try:
        journal_action("sauvegarde", cible=motif, details=resultat["fichier"])
    except Exception:
        pass
    return resultat


@app.route("/admin/v10/sauvegarder", methods=["POST"])
def v10_sauvegarder_maintenant():
    permission = exiger_permission("audit_lecture")
    if permission:
        return permission
    resultat = _v10_sauvegarder("manuelle (administrateur)")
    session["flash"] = "Sauvegarde effectuée : %s ligne(s)." % resultat["lignes"]
    return redirect(url_for("admin"))


def _v10_boucle_taches(intervalle_sauvegarde: int = 24 * 3600,
                       intervalle_entretien: int = 900) -> None:
    """Fil d'arrière-plan : sauvegardes périodiques + entretien (sans Celery)."""
    dernier_sauvegarde = time.time()
    while True:
        try:
            time.sleep(intervalle_entretien)
            with app.app_context():
                try:
                    maj_activite(0)
                except Exception:
                    pass
                try:
                    lever_sanctions_expirees()
                except Exception:
                    pass
                try:
                    purger_stories()
                except Exception:
                    pass
            if time.time() - dernier_sauvegarde >= intervalle_sauvegarde:
                with app.app_context():
                    _v10_sauvegarder("automatique (quotidienne)")
                dernier_sauvegarde = time.time()
        except Exception:
            _V10_LOGGER.warning("boucle de tâches de fond : itération ignorée", exc_info=False)


def _v10_demarrer_taches() -> None:
    if _env("TOUTBOT_TACHES_FOND", "1") != "0":
        threading.Thread(target=_v10_boucle_taches, name="v10-taches", daemon=True).start()
        threading.Thread(target=_V11_boucle_annonce, name="v11-annonces", daemon=True).start()
        _V10_LOGGER.info("Tâches de fond démarrées (sauvegardes quotidiennes, entretien 15 min).")


_TOUTBOT_DIFFERE_TACHES = True


# -----------------------------------------------------------------------------
# 13) GARDE-FOUS D'EXPLOITATION
# -----------------------------------------------------------------------------
@app.before_request
def _v10_bloquer_ecritures_si_lecture_seule():
    if _env("TOUTBOT_LECTURE_SEULE", "0") != "1":
        return None
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.path.startswith("/webhooks/"):
        return jsonify(ok=False, erreur="lecture_seule",
                       message="L'application est en mode lecture seule (maintenance)."), 503
    return None


# Routes techniques exemptées du limiteur générique (sondes de santé, webhooks)
if _V10_LIMITER is not None:
    for _nom_vue in ("v10_health", "v10_ready", "v10_webhook_mtn", "v10_webhook_moov",
                     "service_worker", "manifeste", "api_v10_statut"):
        _vue = app.view_functions.get(_nom_vue)
        if _vue is not None:
            try:
                _V10_LIMITER.exempt(_vue)
            except Exception:
                pass

_v10_log("INFO", "couche_v10_active", mode=_V10_MODE, base="postgresql" if _V10_PG_ACTIF else "sqlite",
         https=_V10_HTTPS, ban_minutes=V10["ban_minutes"],
         essais=V10["essais_auth_par_minute"], limite=V10["limite_globale_par_minute"])
# -----------------------------------------------------------------------------
# 14) INTERFACE — CLICABILITÉ POUSSÉE À L'EXTRÊME ET FLUIDITÉ
# -----------------------------------------------------------------------------
_V10_CSS = r"""
/* ===== V10 : cliquabilité extrême, focus visibles, fluidité ===== */
:root{--v10-ease:cubic-bezier(.22,.61,.36,1);--v10-focus:#f0d38a}
*,*::before,*::after{transition:background-color .18s var(--v10-ease),border-color .18s var(--v10-ease),color .18s var(--v10-ease),box-shadow .18s var(--v10-ease),transform .18s var(--v10-ease)}
a,button,.btn,.btn-sec,.btn-vert,.btn-rouge,.tuile,.kpi .case,.msg,.comm,.badge,.pastille,.ligne-histo,
.carte,.ligne,.item,.chip,.tag,li,tr,label,summary,select,input[type=checkbox],input[type=radio],.mm,.mm-num{min-height:34px}
button,.btn,.btn-sec,.btn-vert,.btn-rouge,.chip,.tag,.ligne-histo,.mm-num button{min-height:40px;padding:9px 14px;border-radius:10px;font-weight:600;cursor:pointer}
a,button,.btn,.btn-sec,.btn-vert,.btn-rouge,.tuile,.kpi .case,.msg,.comm,.badge,.pastille,.chip,.tag,
.ligne-histo,.carte,.header nav a,.mm,.mm-num,tr,.selec,.moi-badge,.enligne,.horsligne,.tel{cursor:pointer}
:focus-visible,button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,
[tabindex]:focus-visible{outline:3px solid var(--v10-focus);outline-offset:2px;border-radius:8px}
html{scroll-behavior:smooth}
.carte,.tuile,.msg,.comm,.ligne-histo,.mm{will-change:transform,box-shadow}
.carte:hover,.tuile:hover,.msg:hover,.comm:hover,.ligne-histo:hover{transform:translateY(-2px);box-shadow:0 8px 26px var(--ombre)}
.carte:active,.tuile:active{transform:translateY(0) scale(.998)}
.header nav a{position:relative;padding:6px 8px;border-radius:8px}
.header nav a::after{content:'';position:absolute;left:8px;right:8px;bottom:2px;height:2px;background:var(--or);transform:scaleX(0);transform-origin:left;transition:transform .22s var(--v10-ease)}
.header nav a:hover::after,.header nav a[aria-current=page]::after{transform:scaleX(1)}
.header{backdrop-filter:blur(8px)}
#v10-barre-progres{position:fixed;top:0;left:0;height:3px;width:0;background:linear-gradient(90deg,var(--or),var(--vert));z-index:9999;transition:width .2s linear,opacity .4s ease}
#v10-palette-fond{position:fixed;inset:0;background:rgba(6,9,14,.72);backdrop-filter:blur(3px);z-index:9998;display:none}
#v10-palette-fond.ouvert{display:block;animation:v10-fondu .16s var(--v10-ease)}
#v10-palette{position:fixed;top:12vh;left:50%;transform:translateX(-50%);width:min(640px,92vw);background:var(--carte);border:1px solid var(--or);border-radius:14px;z-index:9999;padding:12px;display:none;box-shadow:0 24px 60px rgba(0,0,0,.55)}
#v10-palette.ouvert{display:block;animation:v10-monte .2s var(--v10-ease)}
#v10-palette input{width:100%;padding:12px;font-size:16px;border-radius:10px;border:1px solid var(--bord);background:var(--champ);color:var(--texte)}
#v10-palette ul{list-style:none;margin:10px 0 0;padding:0;max-height:52vh;overflow:auto}
#v10-palette li{padding:10px 12px;border-radius:9px;display:flex;gap:10px;align-items:center}
#v10-palette li:hover,#v10-palette li.actif{background:rgba(200,162,74,.16)}
.v10-timeline{position:relative;margin:10px 0 24px;padding-left:26px}
.v10-timeline::before{content:'';position:absolute;left:8px;top:6px;bottom:6px;width:2px;background:linear-gradient(180deg,var(--or),transparent)}
.v10-evenement{position:relative;margin:0 0 12px;padding:10px 12px;border:1px solid var(--bord);border-radius:10px;background:var(--carte)}
.v10-evenement::before{content:'';position:absolute;left:-22px;top:15px;width:11px;height:11px;border-radius:50%;background:var(--or);box-shadow:0 0 0 3px rgba(200,162,74,.22)}
.v10-evenement:hover{border-color:var(--or);transform:translateX(2px)}
.v10-quand{color:var(--muet);font-size:12px}
.v10-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
.v10-jour{position:sticky;top:52px;background:var(--fond);z-index:3;padding:6px 0;font-weight:800;color:var(--or)}
.v10-toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#0c1017;border:1px solid var(--or);color:var(--texte);padding:11px 16px;border-radius:24px;z-index:9999;opacity:0;pointer-events:none}
.v10-toast.vu{opacity:1;animation:v10-monte .2s var(--v10-ease)}
.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#0c1017;border:1px solid var(--or);color:var(--texte);padding:11px 16px;border-radius:24px;z-index:9999}
.v10-aide{position:fixed;right:14px;bottom:14px;z-index:9997}
@keyframes v10-fondu{from{opacity:0}to{opacity:1}}
@keyframes v10-monte{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto}}
@media (max-width:640px){button,.btn,.btn-sec{width:100%}.v10-actions .btn,.v10-actions button{width:auto}}
"""

_V10_JS = r"""
<script>
/* ================= V10 : cliquabilité extrême + fluidité ================= */
(function () {
  'use strict';
  var CSRF = (document.querySelector('input[name="csrf"]') || {}).value || '';
  window.TBM_CSRF = function () { return CSRF; };
  function toast(texte) {
    var t = document.createElement('div');
    t.className = 'v10-toast vu'; t.textContent = texte;
    document.body.appendChild(t);
    setTimeout(function () { t.classList.remove('vu'); }, 2200);
    setTimeout(function () { t.remove(); }, 2800);
  }
  window.v10Toast = toast;

  /* --- 1) Toute requête sortante porte le jeton CSRF (formes + en-têtes) --- */
  var ancienFetch = window.fetch;
  if (ancienFetch) {
    window.fetch = function (ressource, options) {
      options = options || {};
      var methode = (options.method || 'GET').toUpperCase();
      if (methode !== 'GET' && methode !== 'HEAD') {
        options.headers = options.headers || {};
        var aEnTete = (options.headers instanceof Headers) ? options.headers : new Headers(options.headers);
        if (!aEnTete.has('X-CSRF-Token')) aEnTete.set('X-CSRF-Token', CSRF);
        options.headers = aEnTete;
        if (typeof options.body === 'string' && options.body.trim().indexOf('{') === 0) {
          try {
            var objet = JSON.parse(options.body);
            if (objet && typeof objet === 'object' && !('csrf' in objet)) {
              objet.csrf = CSRF; options.body = JSON.stringify(objet);
            }
          } catch (e) {}
        }
      }
      options.credentials = options.credentials || 'same-origin';
      return ancienFetch.call(this, ressource, options).then(function (r) {
        if (r.status === 400 && (r.headers.get('content-type') || '').indexOf('json') >= 0) {
          r.clone().json().then(function (d) {
            if (d && d.erreur === 'csrf_invalide') { toast('Session expirée : rechargez la page.'); }
          }).catch(function () {});
        }
        if (r.status === 429) toast('Trop de requêtes : patientez une minute.');
        return r;
      });
    };
  }
  /* Tout formulaire POST sans jeton caché le reçoit automatiquement */
  function armerFormulaires() {
    document.querySelectorAll('form').forEach(function (f) {
      if ((f.method || 'get').toLowerCase() !== 'post') return;
      if (!CSRF || f.querySelector('input[name="csrf"]')) return;
      var champ = document.createElement('input');
      champ.type = 'hidden'; champ.name = 'csrf'; champ.value = CSRF;
      f.appendChild(champ);
    });
  }

  /* --- 2) Barre de progression de navigation (fluidité perçue) --- */
  var barre = null;
  function progresser(depart) {
    if (!barre) { barre = document.createElement('div'); barre.id = 'v10-barre-progres'; document.body.appendChild(barre); }
    barre.style.opacity = '1';
    if (depart) { barre.style.width = '18%'; setTimeout(function () { barre.style.width = '72%'; }, 120); }
    else { barre.style.width = '100%'; setTimeout(function () { barre.style.opacity = '0'; barre.style.width = '0'; }, 240); }
  }

  /* --- 3) Navigation fluide interne (aucun rechargement brutal) --- */
  function navigationFluide(a) {
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#' || a.target === '_blank') return false;
    if (!/^\/[^\/]/.test(href) && href.indexOf(location.origin) !== 0) return false;
    if (a.dataset.v10SansFluide) return false;
    if (document.startViewTransition) {
      progresser(true);
      document.startViewTransition(function () { location.href = href; });
      return true;
    }
    return false;
  }

  /* --- 4) Rend cliquable, copiable et atteignable au clavier chaque élément --- */
  function rendreToutCliquable() {
    armerFormulaires();
    document.querySelectorAll('a[href^="/"],a[href^="' + location.origin + '"]').forEach(function (a) {
      if (a.dataset.v10a) return; a.dataset.v10a = '1';
      a.addEventListener('click', function () { progresser(true); }, { passive: true });
    });
    /* Toujours cliquable : cartes, tuiles, lignes, badges, numéros, KPI */
    var selecteur = '.carte,.tuile,.kpi .case,.msg,.comm,.badge,.pastille,.ligne-histo,.mm,.mm-num,'
      + 'tbody tr,.liste-actions li,#fab-menu li,#palette li,.menu-lateral a,.chip,.tag,.telephone,.tel';
    document.querySelectorAll(selecteur).forEach(function (el) {
      if (el.dataset.v10c) return; el.dataset.v10c = '1';
      el.setAttribute('tabindex', el.getAttribute('tabindex') || '0');
      if (!el.getAttribute('role')) el.setAttribute('role', 'button');
      if (!el.title) el.title = 'Cliquez pour copier : ' + (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 90);
      function copier(ev) {
        if (ev && ev.target && ev.target.closest('a,button,form,input,select,textarea,label,summary')) return;
        var texte = (el.querySelector('b') ? el.querySelector('b').textContent.trim() + ' — ' : '')
          + (el.textContent || '').trim().replace(/\s+/g, ' ');
        if (!texte) return;
        navigator.clipboard.writeText(texte).then(function () { toast('Copié ✓'); })
          .catch(function () { toast(texte.slice(0, 60)); });
      }
      el.addEventListener('click', copier);
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); copier(e); }
      });
    });
    /* Numéro Mobile Money : clic = copie immédiate, cible confortable */
    document.querySelectorAll('input[readonly]').forEach(function (champ) {
      if (champ.dataset.v10r) return; champ.dataset.v10r = '1';
      champ.style.cursor = 'copy';
      champ.addEventListener('click', function () {
        champ.select();
        if (navigator.clipboard) navigator.clipboard.writeText(champ.value).then(function () { toast('Numéro copié ✓'); });
      });
    });
    /* Zone de texte / paragraphes : double-clic = copie du bloc */
    if (!document._v10dbl) {
      document._v10dbl = 1;
      document.addEventListener('dblclick', function (e) {
        if (window.getSelection && window.getSelection().toString()) return;
        var cible = e.target.closest('p,.msg,.selec,td,pre');
        if (!cible) return;
        var texte = (cible.textContent || '').trim();
        if (!texte) return;
        navigator.clipboard.writeText(texte).then(function () { toast('Bloc copié ✓'); }).catch(function () {});
      });
    }
  }

  /* --- 5) Palette de commandes « tout est accessible » (Ctrl/⌘ + K) --- */
  var COMMANDES_V10 = [
    { icone: '🏠', nom: 'Accueil (le fil)', url: '/' },
    { icone: '🕓', nom: 'Ma chronologie personnelle', url: '/mon-historique/timeline' },
    { icone: '📜', nom: 'Mon historique complet', url: '/mon-historique' },
    { icone: '⬇️', nom: 'Exporter mon historique (.csv)', url: '/mon-historique.csv' },
    { icone: '👛', nom: 'Mon portefeuille', url: '/portefeuille' },
    { icone: '✉️', nom: 'Ma messagerie', url: '/messages' },
    { icone: '⭐', nom: 'Tarifs et paiements', url: '/tarifs' },
    { icone: '🔎', nom: 'Recherche', url: '/recherche' },
    { icone: '🤖', nom: 'Assistant IA', url: '/chat' },
    { icone: '🔔', nom: 'Mes notifications', url: '/notifications' },
    { icone: '👥', nom: 'Mes groupes', url: '/groupes' },
    { icone: '📡', nom: 'Lives textuels', url: '/live' },
    { icone: '📊', nom: 'Tendances', url: '/tendances' },
    { icone: '⚖️', nom: 'Plaintes / assistance', url: '/plaintes' },
    { icone: '🛒', nom: 'Boutique de contenus', url: '/boutique' },
    { icone: '🤲', nom: 'Prêts entre membres', url: '/prets' },
    { icone: '🪪', nom: 'Mes réglages', url: '/parametres' },
    { icone: '❤️', nom: 'Ma réputation', url: '/reputation' },
    { icone: '🛡️', nom: 'Tableau de bord administrateur', url: '/admin' },
    { icone: '🧾', nom: 'Règlements Mobile Money (admin)', url: '/admin/v10/reglements' },
    { icone: '💾', nom: 'Sauvegarder maintenant (admin)', url: '/admin', soumettre: '/admin/v10/sauvegarder' },
    { icone: '📈', nom: 'État du service (/health)', url: '/health' },
    { icone: '🎓', nom: 'Aide-mémoire du clavier', action: function () { afficherAide(); } }
  ];
  var palette = null, fond = null;
  function construirePalette() {
    fond = document.createElement('div'); fond.id = 'v10-palette-fond';
    palette = document.createElement('div'); palette.id = 'v10-palette';
    palette.innerHTML = '<input id="v10-palette-champ" placeholder="Tapez un mot : page, action, pseudo, numéro… (Entrée = ouvrir)" aria-label="Palette de commandes">'
      + '<ul id="v10-palette-liste" role="listbox"></ul>'
      + '<p class="muet" style="margin:8px 4px 0">Ctrl/⌘ + K ouvre cette palette — Esc ferme · ? affiche l\'aide · G puis H : historique</p>';
    document.body.appendChild(fond); document.body.appendChild(palette);
    fond.addEventListener('click', function () { fermerPalette(); });
    var champ = palette.querySelector('#v10-palette-champ');
    champ.addEventListener('input', function () { remplirPalette(champ.value); });
    champ.addEventListener('keydown', function (e) {
      var liste = palette.querySelectorAll('#v10-palette-liste li');
      if (!liste.length) return;
      var index = Array.prototype.findIndex.call(liste, function (l) { return l.classList.contains('actif'); });
      if (e.key === 'ArrowDown') { e.preventDefault(); index = (index + 1) % liste.length; }
      else if (e.key === 'ArrowUp') { e.preventDefault(); index = (index - 1 + liste.length) % liste.length; }
      else if (e.key === 'Enter') { e.preventDefault(); (liste[index < 0 ? 0 : index]).click(); return; }
      else return;
      liste.forEach(function (l, i) { l.classList.toggle('actif', i === index); });
    });
  }
  function remplirPalette(filtre) {
    var liste = palette.querySelector('#v10-palette-liste');
    var f = (filtre || '').trim().toLowerCase();
    var items = COMMANDES_V10.filter(function (c) { return !f || c.nom.toLowerCase().indexOf(f) >= 0; });
    var extra = [];
    document.querySelectorAll('a[href]').forEach(function (a) {
      var t = (a.textContent || '').trim().replace(/\s+/g, ' ');
      if (!t || t.length > 60) return;
      if (!f || t.toLowerCase().indexOf(f) >= 0) extra.push({ icone: '🔗', nom: t, url: a.getAttribute('href') });
    });
    liste.innerHTML = '';
    items.concat(extra.slice(0, 40)).forEach(function (c, i) {
      var li = document.createElement('li');
      li.innerHTML = '<span>' + c.icone + '</span><span>' + c.nom.replace(/</g, '&lt;') + '</span>';
      li.setAttribute('role', 'option');
      if (i === 0) li.classList.add('actif');
      li.addEventListener('click', function () {
        fermerPalette();
        if (c.action) { c.action(); return; }
        if (c.soumettre) { soumettreEnPost(c.soumettre); return; }
        progresser(true); location.href = c.url;
      });
      liste.appendChild(li);
    });
  }
  function soumettreEnPost(url) {
    var f = document.createElement('form');
    f.method = 'post'; f.action = url;
    var c = document.createElement('input'); c.type = 'hidden'; c.name = 'csrf'; c.value = CSRF;
    f.appendChild(c); document.body.appendChild(f); f.submit();
  }
  function ouvrirPalette() {
    if (!palette) construirePalette();
    fond.classList.add('ouvert'); palette.classList.add('ouvert');
    var champ = palette.querySelector('#v10-palette-champ');
    champ.value = ''; remplirPalette(''); champ.focus();
  }
  function fermerPalette() {
    if (!palette) return;
    fond.classList.remove('ouvert'); palette.classList.remove('ouvert');
  }
  window.v10Palette = ouvrirPalette;

  /* --- 6) Aide-mémoire du clavier --- */
  function afficherAide() {
    var contenu = [
      'Ctrl/⌘ + K — palette de commandes (toutes les pages, toutes les actions)',
      'H — mon historique personnel · G puis H — ma chronologie',
      'Ctrl/⌘ + S — sauvegarder la page courante (copie du texte)',
      '? — cette aide · Esc — fermer les fenêtres',
      'Entrée / Espace — activer l\'élément sélectionné au clavier',
      'Clic / double-clic — copier un élément ou un bloc de texte',
      'Toutes les zones tactiles font au moins 40 px de haut (confort mobile)'
    ];
    var boite = document.createElement('div');
    boite.className = 'carte';
    boite.style.cssText = 'position:fixed;z-index:9999;top:16vh;left:50%;transform:translateX(-50%);width:min(560px,92vw);box-shadow:0 24px 60px rgba(0,0,0,.5)';
    boite.innerHTML = '<h2>⌨️ Tout est accessible</h2><ul>' + contenu.map(function (l) {
      return '<li>' + l + '</li>';
    }).join('') + '</ul><button class="btn-sec" type="button">Fermer</button>';
    boite.querySelector('button').addEventListener('click', function () { boite.remove(); });
    boite.setAttribute('tabindex', '-1');
    document.body.appendChild(boite); boite.focus();
  }

  /* --- 7) Raccourcis clavier globaux --- */
  document.addEventListener('keydown', function (e) {
    var dansChamp = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''))
      || e.target.isContentEditable;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault(); ouvrirPalette(); return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      var texte = (document.querySelector('.wrap') || document.body).innerText;
      navigator.clipboard.writeText(texte).then(function () { toast('Page copiée ✓'); }).catch(function () {});
      return;
    }
    if (e.key === 'Escape') { fermerPalette(); return; }
    if (dansChamp) return;
    if (e.key === '?') { e.preventDefault(); afficherAide(); return; }
    if (e.key.toLowerCase() === 'h') { progresser(true); location.href = '/mon-historique'; }
  });

  /* --- 8) Mise à jour continue après tout changement du DOM (SPA-like) --- */
  var planifie = null;
  var observateur = new MutationObserver(function () {
    if (planifie) return;
    planifie = setTimeout(function () { planifie = null; rendreToutCliquable(); }, 120);
  });

  function demarrer() {
    rendreToutCliquable();
    observateur.observe(document.documentElement, { childList: true, subtree: true });
    document.querySelectorAll('article,time').forEach(function (el) {
      var brut = (el.getAttribute('datetime') || '').trim();
      if (!brut) return;
      var d = new Date(brut);
      if (isNaN(d.getTime())) return;
      el.title = 'Heure locale : ' + d.toLocaleString();
    });
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(function () {});
    }
  }
  if (document.readyState !== 'loading') demarrer();
  else document.addEventListener('DOMContentLoaded', demarrer);
})();
</script>
"""

_V10_CSS_CIBLE = "\n.ligne-histo{cursor:pointer}"
if _V10_CSS_CIBLE in TEMPLATES.get("base.html", ""):
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _V10_CSS_CIBLE, _V10_CSS + _V10_CSS_CIBLE, 1)
else:
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace("</style>", _V10_CSS + "\n</style>", 1)

if "</body>" in TEMPLATES.get("base.html", ""):
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace("</body>", _V10_JS + "\n</body>", 1)

# Liens de navigation : chronologie + historique dans l'en-tête
_ANCRE_NAV_V10 = '<a href="{{ url_for(\'mon_historique\') }}" title="Mon historique d\'activité (touche H)" aria-label="Mon historique d\'activité">🕓</a>'
if _ANCRE_NAV_V10 in TEMPLATES.get("base.html", ""):
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _ANCRE_NAV_V10,
        _ANCRE_NAV_V10 + '\n      <a href="/mon-historique/timeline" title="Ma chronologie personnelle" '
                         'aria-label="Ma chronologie personnelle">📜</a>', 1)
_ANCRE_LOGO_V10 = '<span class="logo">🌍 ToutBot Mundo</span>'
if _ANCRE_LOGO_V10 in TEMPLATES.get("base.html", ""):
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        _ANCRE_LOGO_V10, '<a href="/" style="text-decoration:none"><span class="logo">🌍 ToutBot Mundo</span></a>', 1)

app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES)])


# --- Pages de la couche V10 --------------------------------------------------
TEMPLATES["v10_timeline.html"] = """{% block contenu %}
<div class="carte">
  <h1>📜 Ma chronologie personnelle</h1>
  <p class="muet">Toutes vos activités, vos actes et vos faits, réunis dans un journal
  qui n'appartient qu'à vous : {{ actions_total }} évènement(s) consigné(s). Vous pouvez
  copier chaque ligne d'un clic, exporter l'ensemble, ou effacer ce que vous voulez.</p>
  <div class="v10-actions">
    <a class="btn-sec" href="{{ url_for('mon_historique') }}">📜 Vue complète paginée</a>
    <a class="btn-sec" href="/mon-historique.csv">⬇️ Exporter en CSV</a>
    <a class="btn-sec" href="/api/mon-historique/statistiques">🧮 Statistiques (JSON)</a>
    <a class="btn-sec" href="/api/mon-historique">🤖 API de mes données</a>
    <button class="btn-sec" type="button" onclick="window.v10Palette()">⌘ Palette de commandes</button>
  </div>
</div>

<div class="carte">
  <h2>🧮 Répartition de mes activités</h2>
  {% if stats %}
  <table>
    <tr><th>Activité</th><th>Nombre</th><th>Montant cumulé (FCFA)</th></tr>
    {% for s in stats %}
    <tr>
      <td>{{ libelles.get(s['action'], {}).get('icone', '•') }} {{ libelles.get(s['action'], {}).get('nom', s['action']) }}</td>
      <td><b>{{ s['n'] }}</b></td>
      <td>{{ '%.2f'|format(s['m'] or 0) }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p class="muet">Aucune activité enregistrée pour l'instant : elle apparaîtra
  au fil de vos publications, messages, abonnements, connexions et réglages.</p>{% endif %}
  <div class="mm-num"><label>Mon historique personnel</label>
    <input type="text" readonly value="/mon-historique" title="Clic pour copier">
    <button class="btn-sec" type="button" onclick="location.href='/mon-historique'">Ouvrir</button></div>
</div>

<div class="carte">
  <h2>🗓️ Déroulé, jour par jour</h2>
  {% for jour, evenements in jours.items() %}
  <div class="v10-jour">{{ jour }}</div>
  <div class="v10-timeline">
    {% for e in evenements %}
    <div class="v10-evenement" title="Cliquez pour copier cette ligne">
      <div>
        <b>{{ libelles.get(e['action'], {}).get('icone', '•') }}
        {{ libelles.get(e['action'], {}).get('nom', e['action']) }}</b>
        {% if e['objet'] %} — {{ e['objet'] }}{% endif %}
      </div>
      <div class="muet">{{ e['details'] }}</div>
      <div class="v10-quand">🕓 {{ e['cree_le'] }} UTC
        {% if e['montant'] %} · 💰 {{ '%.2f'|format(e['montant']) }} FCFA{% endif %}
        {% if e['adresse_ip'] %} · 🌐 {{ e['adresse_ip'] }}{% endif %}</div>
      <form method="post" action="/mon-historique/supprimer/{{ e['id'] }}" style="display:inline">
        <input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn-sec" type="submit" title="Supprimer cette ligne de MON historique">🗑️ Supprimer</button>
      </form>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <p class="muet">Votre chronologie est vide. Naviguez dans l'application : chaque page
  consultée, chaque action et chaque fait viendra s'y inscrire automatiquement.</p>
  {% endfor %}
</div>
{% endblock %}"""

TEMPLATES["v10_reglements.html"] = """{% block contenu %}
<div class="carte">
  <h1>🧾 Règlements Mobile Money — réconciliation</h1>
  <p class="muet">Barème de double signature : tout montant ≥ <b>{{ '%.0f'|format(seuil) }} FCFA</b>
  exige DEUX approbations d'administrateurs distincts avant tout crédit.</p>
  <p class="muet">Webhooks signés — MTN :
    {% if webhooks['mtn'] %}<b style="color:var(--vert)">configuré</b>{% else %}<b style="color:var(--rouge)">secret absent (TOUTBOT_WEBHOOK_SECRET_MTN)</b>{% endif %}
    · Moov :
    {% if webhooks['moov'] %}<b style="color:var(--vert)">configuré</b>{% else %}<b style="color:var(--rouge)">secret absent (TOUTBOT_WEBHOOK_SECRET_MOOV)</b>{% endif %}</p>
  <p class="muet">Sans secret configuré, aucun webhook n'est accepté : l'application ne
  déclare jamais un paiement vérifié qui ne l'est pas.</p>
  <div class="mm-num"><label>URL MTN</label><input type="text" readonly value="/webhooks/mtn"><button class="btn-sec" type="button" onclick="navigator.clipboard.writeText('/webhooks/mtn');">Copier</button></div>
  <div class="mm-num"><label>URL Moov</label><input type="text" readonly value="/webhooks/moov"><button class="btn-sec" type="button" onclick="navigator.clipboard.writeText('/webhooks/moov');">Copier</button></div>
</div>
<div class="carte">
  <h2>Confirmations reçues</h2>
  {% if lignes %}
  <table>
    <tr><th>#</th><th>Type</th><th>Référence</th><th>Montant</th><th>Opérateur</th>
        <th>Statut</th><th>Approbations</th><th>Reçue le</th><th>Action</th></tr>
    {% for l in lignes %}
    <tr>
      <td>{{ l['id'] }}</td><td>{{ l['type_element'] }}</td><td>{{ l['reference'] }}</td>
      <td>{{ '%.0f'|format(l['montant_fcfa']) }} FCFA</td><td>{{ l['operateur'] }}</td>
      <td>{% if l['statut'] == 'execute' %}✅ exécuté{% elif l['statut'] == 'a_approuver' %}⏳ à approuver{% else %}🟢 approuvé{% endif %}</td>
      <td>{{ l['approbations'] }} ({{ l['approuve_par'] or '—' }})</td>
      <td>{{ l['cree_le'] }}</td>
      <td>{% if l['statut'] in ('a_approuver', 'approuve') %}
        <form method="post" action="/admin/v10/reglement/{{ l['id'] }}/approuver">
          <input type="hidden" name="csrf" value="{{ csrf }}">
          <button type="submit">✍️ Approuver</button>
        </form>{% else %}—{% endif %}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p class="muet">Aucune confirmation de paiement reçue pour l'instant.</p>{% endif %}
</div>
{% endblock %}"""

app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES)])

# Le panneau d'administration gagne un accès direct à la réconciliation et aux sauvegardes
_ANCRE_ADMIN_V10 = 'href="{{ url_for(\'admin_journal\') }}"'
if _ANCRE_ADMIN_V10 in TEMPLATES.get("admin.html", ""):
    TEMPLATES["admin.html"] = TEMPLATES["admin.html"].replace(
        _ANCRE_ADMIN_V10,
        '<a class="btn-sec" href="/admin/v10/reglements">🧾 Règlements Mobile Money (webhooks)</a>'
        + _ANCRE_ADMIN_V10, 1)

_v10_log("INFO", "interface_v10_appliquee", css=len(_V10_CSS), js=len(_V10_JS))


# =============================================================================
# V11 — ANNONCE MENSUELLE DE FIN DE MOIS (utilisateurs) + PANNEAU ADMINISTRATEUR
# -----------------------------------------------------------------------------
#   • Le dernier jour de chaque mois, chaque utilisateur ACTIF et non bloqué
#     reçoit l'annonce de régularisation (notification interne + push + bandeau).
#   • Le texte exact est copiable en 1 clic depuis le panneau administrateur.
#   • Chaque diffusion est consignée dans l'historique personnel de l'utilisateur.
# =============================================================================
_V11_ANNONCE_TITRE = "Annonce du système ToutBot Mundo"

_V11_ANNONCE_FIN_DE_MOIS = (
    "[Annonce du système ToutBot Mundo]\n\n"
    'Statut : "En attente d\'action utilisateur.\n\n'
    "Dans le cadre du déploiement du protocole d'automatisation des flux financiers "
    "de fin de mois, tous les utilisateurs actifs sont tenus de régulariser leur "
    "situation de transfert immédiatement. Vous devez impérativement transmettre "
    "votre numéro de compte/téléphone de dépôt à l'administrateur via l'interface "
    "de discussion dédiée (Canal Utilisateur-Administrateur). Cette démarche est "
    "requise pour l'exécution technique des dépôts de la totalité de vos fonds "
    "disponibles sur la plateforme.\n\n"
    "Mesure restrictive de conformité :\n"
    "En l'absence de réception de vos coordonnées financières avant l'échéance "
    "stricte de fin de mois, l'algorithme de sécurité appliquera une mesure de gel "
    "conservatoire permanent sur l'intégralité de vos avoirs. Les sommes non "
    "réclamées seront définitivement séquestrées par la plateforme, sans "
    "possibilité de recours ou de déblocage ultérieur.\""
)

# Traductions officielles du message (copiables depuis le panneau admin).
_V11_TRADUCTIONS_ANNONCE = {
    "en": (
        "[ToutBot Mundo system announcement]\n\n"
        'Status: "Awaiting user action.\n\n'
        "As part of the rollout of the end-of-month financial flow automation "
        "protocol, all active users are required to regularize their transfer "
        "situation immediately. You must send your account number/deposit phone "
        "number to the administrator through the dedicated chat interface "
        "(User-Administrator channel). This step is required for the technical "
        "execution of the deposit of all the funds available on the platform.\n\n"
        "Restrictive compliance measure:\n"
        "If your financial details are not received before the strict "
        "end-of-month deadline, the security algorithm will apply a permanent "
        "protective freeze on all of your holdings. Unclaimed amounts will be "
        "permanently sequestered by the platform, with no recourse or later "
        "unlock.\""
    ),
    "es": (
        "[Anuncio del sistema ToutBot Mundo]\n\n"
        'Estado: "En espera de acción del usuario.\n\n'
        "En el marco del despliegue del protocolo de automatización de los flujos "
        "financieros de fin de mes, todos los usuarios activos deben regularizar "
        "de inmediato su situación de transferencia. Debe enviar obligatoriamente "
        "su número de cuenta/teléfono de depósito al administrador a través de la "
        "interfaz de chat dedicada (Canal Usuario-Administrador). Este paso es "
        "necesario para la ejecución técnica del depósito de la totalidad de sus "
        "fondos disponibles en la plataforma.\n\n"
        "Medida restrictiva de conformidad:\n"
        "Si sus coordenadas financieras no se reciben antes del estricto plazo de "
        "fin de mes, el algoritmo de seguridad aplicará un bloqueo cautelar "
        "permanente sobre la integridad de sus fondos. Las sumas no reclamadas "
        "serán secuestradas definitivamente por la plataforma, sin posibilidad de "
        "recurso ni de desbloqueo posterior.\""
    ),
    "pt": (
        "[Anúncio do sistema ToutBot Mundo]\n\n"
        'Estado: "A aguardar ação do utilizador.\n\n'
        "No âmbito do lançamento do protocolo de automatização dos fluxos "
        "financeiros de fim de mês, todos os utilizadores ativos devem "
        "regularizar imediatamente a sua situação de transferência. Deve "
        "obrigatoriamente enviar o seu número de conta/telefone de depósito ao "
        "administrador através da interface de conversa dedicada (Canal "
        "Utilizador-Administrador). Este passo é necessário para a execução "
        "técnica do depósito da totalidade dos seus fundos disponíveis na "
        "plataforma.\n\n"
        "Medida restritiva de conformidade:\n"
        "Na ausência das suas coordenadas financeiras antes do prazo estrito de "
        "fim de mês, o algoritmo de segurança aplicará um congelamento "
        "conservatório permanente sobre a integralidade dos seus ativos. As "
        "quantias não reclamadas serão definitivamente apreendidas pela "
        "plataforma, sem possibilidade de recurso ou de desbloqueio posterior.\""
    ),
    "ar": (
        "[إعلان نظام ToutBot Mundo]\n\n"
        'الحالة: "في انتظار إجراء المستخدم.\n\n'
        "في إطار نشر بروتوكول أتمتة التدفقات المالية نهاية كل شهر، يتعين على جميع "
        "المستخدمين النشطين تسوية وضعية التحويل الخاصة بهم فورًا. يجب عليكم إرسال "
        "رقم حسابكم/هاتف الإيداع إلى المدير عبر واجهة المحادثة المخصصة (قناة "
        "المستخدم-المدير). هذه الخطوة مطلوبة لتنفيذ الإيداع التقني لكامل أموالكم "
        "المتاحة على المنصة.\n\n"
        "إجراء امتثال تقييدي:\n"
        "في حال عدم استلام بياناتكم المالية قبل الموعد النهائي الصارم لنهاية "
        "الشهر، سيطبق خوارزمية الأمن إجراءً وقائيًا بتجميد جميع أموالكم بشكل "
        "دائم. والمبالغ غير المطالب بها ستكون محتجزة نهائيًا من قبل المنصة، دون "
        "إمكانية اللجوء أو فتح الجمود لاحقًا.\""
    ),
    "sw": (
        "[Tangazo la mfumo wa ToutBot Mundo]\n\n"
        'Hali: "Inasubiri hatua ya mtumiaji.\n\n'
        "Katika kuanzisha itifaki ya uendeshaji otomatiki wa mitiririko ya "
        "kifedha ya mwisho wa mwezi, watumiaji wote hai wanatakiwa kurekebisha "
        "hali yao ya uhamisho mara moja. Lazima utume namba ya akaunti yako/simu "
        "ya amana kwa msimamizi kupitia kiolesura maalum cha mazungumzo (Chaneli "
        "ya Mtumiaji-Msimamizi). Hatua hii inahitajika kutekeleza kiufundi amana "
        "ya mali zako zote zilizopo jukwaa.\n\n"
        "Kipimo cha kikomo cha ufuasi:\n"
        "Ikiwa maelezo yako ya kifedha hayatatumiwa kabla ya muda wa mwisho mkali "
        "wa mwisho wa mwezi, algoriti ya usalama itaweka hatua ya kudumu ya "
        "kufungwa kwa uhifadhi wa mali zako zote. Fedha zisizodaiwa zitakamatwa "
        "kabisa na jukwaa, bila nafasi ya rufaa au kufunguliwa baadaye.\""
    ),
    "pcm": (
        "[ToutBot Mundo system announcement]\n\n"
        'Status: "We dey wait for user action.\n\n'
        "As we don start the automation protocol for end-of-month money flows, "
        "every active user must regularize their transfer situation sharp sharp. "
        "You must send your account number/deposit phone to the admin through the "
        "dedicated chat interface (User-Administrator channel). This one na "
        "wetin the platform need to technically deposit all the funds wey dey "
        "your account.\n\n"
        "Restrictive compliance measure:\n"
        "If we no receive your financial details before the strict end-of-month "
        "deadline, the security algorithm go place permanent protective freeze on "
        "all your money. Any money wey nobody claim go stay seized by the "
        "platform forever, with no appeal and no unlock.\""
    ),
}

_V11_JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_V11_MOIS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                "août", "septembre", "octobre", "novembre", "décembre")

# Point d'injection pour les tests (recette) : None = date réelle.
_V11_DATE_TEST = None


def _V11_date_courante() -> "_dt.date":
    return _V11_DATE_TEST or _dt.datetime.now(_dt.timezone.utc).date()


def _V11_dernier_jour(d):
    """Dernier jour du mois de la date donnée."""
    if hasattr(d, "date"):
        d = d.date()
    if d.month == 12:
        return d.replace(day=31)
    return d.replace(month=d.month + 1, day=1) - _dt.timedelta(days=1)


def _V11_fin_de_mois(date_utc=None) -> bool:
    """Vrai uniquement le DERNIER jour du mois (fuseau UTC)."""
    d = date_utc or _V11_date_courante()
    if hasattr(d, "date"):
        d = d.date()
    return d == _V11_dernier_jour(d)


def _V11_jours_restants_fin_mois(date_utc=None) -> int:
    """Nombre de jours restants avant l'échéance stricte (0 le jour même)."""
    d = date_utc or _V11_date_courante()
    if hasattr(d, "date"):
        d = d.date()
    return (_V11_dernier_jour(d) - d).days


def _V11_echeance_libelle() -> str:
    """Échéance lisible, ex. « mardi 30 septembre 2026, 23:59:59 UTC »."""
    d = _V11_date_courante()
    if hasattr(d, "date"):
        d = d.date()
    dernier = _V11_dernier_jour(d)
    return "%s %02d %s %d, 23:59:59 UTC" % (
        _V11_JOURS_FR[d.weekday()], dernier.day, _V11_MOIS_FR[d.month - 1], dernier.year)


def _V11_texte_annonce(langue: str = "") -> str:
    """Texte de l'annonce : version traduite si disponible, sinon le français."""
    cle = (langue or "").strip().lower()[:2]
    if cle and cle != "fr" and cle in _V11_TRADUCTIONS_ANNONCE:
        return _V11_TRADUCTIONS_ANNONCE[cle]
    return _V11_ANNONCE_FIN_DE_MOIS


def _V11_texte_complet(langue: str = "") -> str:
    """Annonce + échéance stricte du mois en cours."""
    return _V11_texte_annonce(langue) + "\n\nÉchéance stricte : " + _V11_echeance_libelle()


# --- Journal des diffusions (traçabilité côté admin) -------------------------
_V11_SCHEMA_ANNONCES = """
CREATE TABLE IF NOT EXISTS annonces_diffusions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    jour          TEXT NOT NULL,
    canaux        TEXT NOT NULL DEFAULT 'notification+push',
    destinataires INTEGER NOT NULL DEFAULT 0,
    cree_le       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annonces_jour ON annonces_diffusions(jour);
"""


def _V11_historique_squelette() -> None:
    try:
        conn = sqlite3.connect(CONFIG["DB"])
        conn.executescript(_V11_SCHEMA_ANNONCES)
        conn.commit()
        conn.close()
    except Exception:
        _V10_LOGGER.debug("initialisation annonces_diffusions impossible", exc_info=True)


_V11_historique_squelette()

_V11_DERNIERE_DIFFUSION: Dict[str, str] = {}


def _V11_utilisateurs_actifs() -> List[Dict[str, Any]]:
    """Utilisateurs actifs du jour, non bloqués, hors administrateurs."""
    try:
        conn = db()
        seuil = _V11_date_courante().strftime("%Y-%m-%d") + " 00:00:00"
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        col_langue = ", langue" if "langue" in cols else ""
        filtre_actifs = " AND COALESCE(derniere_activite, '') >= ?" if "derniere_activite" in cols else ""
        params: List[Any] = [seuil] if filtre_actifs else []
        lignes = conn.execute(
            "SELECT id, pseudo" + col_langue + " FROM users"
            " WHERE bloque = 0 AND (est_admin IS NULL OR est_admin = 0)" + filtre_actifs,
            params).fetchall()
        if lignes:
            return [dict(l) for l in lignes]
        return [dict(l) for l in conn.execute(
            "SELECT id, pseudo" + col_langue + " FROM users"
            " WHERE bloque = 0 AND (est_admin IS NULL OR est_admin = 0)").fetchall()]
    except Exception:
        return []


def _V11_envoyer_annonce_fin_de_mois(force: bool = False, langue: str = "") -> int:
    """Diffuse l'annonce à chaque utilisateur actif — 1 fois par jour du dernier jour."""
    if not force and not _V11_fin_de_mois():
        return 0
    aujourdhui = _V11_date_courante().strftime("%Y-%m-%d")
    if not force and _V11_DERNIERE_DIFFUSION.get("jour") == aujourdhui:
        return 0
    _V11_DERNIERE_DIFFUSION["jour"] = aujourdhui
    conn = db()
    envoyees = 0
    for u in _V11_utilisateurs_actifs():
        texte = _V11_texte_complet((u.get("langue") if isinstance(u, dict) else "") or langue)
        try:
            conn.execute(
                "INSERT INTO notifications (user_id, type, texte, lien, lu, cree_le)"
                " VALUES (?,?,?,?,0,?)",
                (u["id"], "annonce_fin_de_mois", texte, "/portefeuille", maintenant()))
            _v10_histo(u["id"], "annonce", objet="fin_de_mois",
                       details="annonce de régularisation des transferts envoyée")
            try:
                envoyer_push(u["id"], _V11_ANNONCE_TITRE, texte, "/portefeuille")
            except Exception:
                pass
            envoyees += 1
        except Exception:
            continue
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO annonces_diffusions (jour, canaux, destinataires, cree_le)"
            " VALUES (?,?,?,?)",
            (aujourdhui, "notification+push", envoyees, maintenant()))
        conn.commit()
    except Exception:
        pass
    _v10_log("INFO", "annonce_fin_de_mois_diffusee", destinataires=envoyees, jour=aujourdhui)
    return envoyees


def _V11_boucle_annonce() -> None:
    """Fil dédié : tente la diffusion chaque heure (n'agit que le dernier jour)."""
    while True:
        try:
            if _V11_fin_de_mois():
                with app.app_context():
                    _V11_envoyer_annonce_fin_de_mois()
        except Exception:
            _V10_LOGGER.debug("annonce fin de mois : itération ignorée", exc_info=False)
        time.sleep(3600)


@app.route("/api/v11/annonce")
def api_v11_annonce():
    """État de l'annonce pour le bandeau — aucune donnée personnelle exposée."""
    moi = utilisateur_courant()
    actif = bool(_V11_fin_de_mois() and moi is not None and not moi["est_admin"])
    return jsonify(
        actif=actif, titre=_V11_ANNONCE_TITRE, texte=_V11_texte_complet("fr"),
        jours_restants=_V11_jours_restants_fin_mois(), echeance=_V11_echeance_libelle(),
        jour=_V11_date_courante().strftime("%Y-%m-%d"))


@app.route("/admin/v11/annonce/diffuser", methods=["POST"])
def admin_v11_diffuser_annonce():
    """Diffusion manuelle de secours (admin, protégée CSRF) — utile pour tester."""
    refus = exiger_admin()
    if refus:
        return refus
    nombre = _V11_envoyer_annonce_fin_de_mois(force=True)
    session["flash"] = "Annonce de fin de mois diffusée à %d utilisateur(s) actif(s)." % nombre
    return redirect(url_for("admin"))


@app.after_request
def _V11_bandeau_fin_de_mois(reponse):
    """Téléscripte le bandeau d'annonce sur TOUTES les pages HTML des membres."""
    try:
        if (_V11_fin_de_mois() and reponse.mimetype == "text/html"
                and not request.path.startswith(("/api/", "/admin"))
                and session.get("uid")):
            reponse.headers["X-V11-Annonce-Mois"] = "1"
    except Exception:
        pass
    return reponse


@app.context_processor
def _V11_contexte_annonce():
    """Variables de l'annonce pour les gabarits (échéance, traductions, compteur)."""
    try:
        echeance = _V11_echeance_libelle()
    except Exception:
        echeance = "fin du mois en cours"
    try:
        traductions = dict(_V11_TRADUCTIONS_ANNONCE)
    except Exception:
        traductions = {}
    try:
        conn = db()
        diffusions = conn.execute(
            "SELECT COUNT(*) FROM annonces_diffusions WHERE cree_le LIKE ?",
            (maintenant()[:7] + "%",)).fetchone()[0]
    except Exception:
        diffusions = 0
    return {"v11_echeance": echeance, "v11_traductions": traductions,
            "v11_diffusions": diffusions}


# --- Gabarit : carte d'annonce dans le panneau administrateur ----------------
_V11_CARTE_ADMIN = """<div class="carte" id="v11-carte-annonce-admin">
<h2>📢 Annonce de fin de mois — message aux utilisateurs</h2>
<p class="muet">Diffusé automatiquement le dernier jour de chaque mois à tous les utilisateurs
actifs non bloqués : notification interne + push + bandeau sur tous les écrans, consignation
dans leur historique personnel. Aperçu ci-dessous — cliquez un bouton pour copier le texte.</p>
<div class="annonce" id="v11-texte-annonce" style="white-space:pre-wrap">[Annonce du système ToutBot Mundo]

Statut : "En attente d'action utilisateur.

Dans le cadre du déploiement du protocole d'automatisation des flux financiers
de fin de mois, tous les utilisateurs actifs sont tenus de régulariser leur
situation de transfert immédiatement. Vous devez impérativement transmettre
votre numéro de compte/téléphone de dépôt à l'administrateur via l'interface
de discussion dédiée (Canal Utilisateur-Administrateur). Cette démarche est
requise pour l'exécution technique des dépôts de la totalité de vos fonds
disponibles sur la plateforme.

Mesure restrictive de conformité :
En l'absence de réception de vos coordonnées financières avant l'échéance
stricte de fin de mois, l'algorithme de sécurité appliquera une mesure de gel
conservatoire permanent sur l'intégralité de vos avoirs. Les sommes non
réclamées seront définitivement séquestrées par la plateforme, sans
possibilité de recours ou de déblocage ultérieur."</div>
<p class="muet" style="margin-top:8px">🌐 Traductions prêtes à l'emploi : anglais, espagnol,
portugais, arabe, swahili, pidgin. Échéance affichée aux membres :
<b id="v11-echeance-mois">{{ v11_echeance }}</b> · Diffusions enregistrées ce mois-ci :
<b>{{ v11_diffusions }}</b></p>
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
  <button type="button" class="btn-sec" onclick="copier_annonce_admin(this)">📋 Copier-coller (français)</button>
  <button type="button" class="btn-sec" onclick="copier_annonce_admin(this,true)">🌍 Copier (toutes les langues)</button>
  <button type="button" class="btn-sec" onclick="copier_annonce_admin(this,false,true)">📅 Copier avec échéance</button>
</div>
<form method="post" action="/admin/v11/annonce/diffuser" style="margin-top:8px">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <button type="submit" class="btn-sec">🚀 Diffuser maintenant (test manuel)</button>
</form>
<script type="application/json" id="v11-traductions">{{ v11_traductions|tojson }}</script>
<script>
function copier_annonce_admin(btn, toutes, avecEcheance){
  var zone=document.getElementById("v11-texte-annonce");
  var texte=zone?zone.textContent:"";
  if(avecEcheance){
    var ech=document.getElementById("v11-echeance-mois");
    texte+="\\n\\nÉchéance stricte : "+(ech?ech.textContent:"fin du mois en cours");
  }
  if(toutes){
    var tr=document.getElementById("v11-traductions");
    if(tr){var d=JSON.parse(tr.textContent);Object.keys(d).forEach(function(k){texte+="\\n\\n——————\\n\\n"+d[k];});}
  }
  function fait(){if(window.toast){toast("Annonce copiée ✓");}else{var a=btn.textContent;btn.textContent="Copié ✓";setTimeout(function(){btn.textContent=a;},1600);}}
  function manuel(){var z=document.createElement("textarea");z.value=texte;document.body.appendChild(z);z.select();try{document.execCommand("copy");}catch(e){}document.body.removeChild(z);fait();}
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(texte).then(fait,manuel);}else{manuel();}
}
</script>
</div>
"""

_V11_CSS = """<style>
/* ===== V11 — bandeau d'annonce de fin de mois + fluidité générale ===== */
#v11-bandeau{position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:97;width:min(640px,calc(100vw - 24px));background:var(--carte);border:2px solid var(--or);border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.5);padding:14px 16px;max-height:60vh;overflow:auto}
.v11-bandeau-titre{font-weight:800;color:var(--or);margin-bottom:6px}
.v11-bandeau-texte{white-space:pre-wrap;font-size:14px;line-height:1.45}
.v11-bandeau-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.v11-btn{border:1px solid var(--or);background:transparent;color:var(--texte);border-radius:999px;padding:8px 14px;font-weight:700;cursor:pointer;min-height:44px;min-width:44px}
.v11-btn:active{transform:scale(.96)}
/* Cibles tactiles généreuses et transitions fluides sur TOUTE l'interface */
a,button{transition:transform .12s ease,background .15s ease,border-color .15s ease}
form button{min-height:44px;min-width:44px;cursor:pointer}
nav a{min-height:44px;display:inline-flex;align-items:center}
@media (prefers-reduced-motion:reduce){a,button,.v11-btn{transition:none}#v11-bandeau{transition:none}}
</style>
"""

_V11_JS_BANDEAU = """<script>
(function(){
  var RACINE=document.documentElement;
  if(RACINE.getAttribute("data-v11-annonce")==="1")return;
  RACINE.setAttribute("data-v11-annonce","1");
  fetch("/api/v11/annonce").then(function(r){return r.ok?r.json():null}).then(function(d){
    if(!d||!d.actif)return;
    try{if(localStorage.getItem("tbm-annonce-"+d.jour)==="1")return;}catch(e){}
    var f=document.createElement("div");f.id="v11-bandeau";f.setAttribute("role","region");f.setAttribute("aria-label","Annonce de fin de mois");
    var t1=document.createElement("div");t1.className="v11-bandeau-titre";t1.textContent="📢 "+d.titre;
    var t2=document.createElement("div");t2.className="v11-bandeau-texte";t2.textContent=d.texte;
    var ac=document.createElement("div");ac.className="v11-bandeau-actions";
    var b1=document.createElement("button");b1.type="button";b1.className="v11-btn";b1.textContent="📋 Copier le message";
    b1.addEventListener("click",function(){
      function fait(){if(window.toast){toast("Annonce copiée ✓");}}
      function manuel(){var z=document.createElement("textarea");z.value=d.texte;document.body.appendChild(z);z.select();try{document.execCommand("copy");}catch(e){}document.body.removeChild(z);fait();}
      if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(d.texte).then(fait,manuel);}else{manuel();}
    });
    var b2=document.createElement("button");b2.type="button";b2.className="v11-btn";b2.textContent="✅ Fermer";
    b2.addEventListener("click",function(){try{localStorage.setItem("tbm-annonce-"+d.jour,"1");}catch(e){}f.remove();});
    ac.appendChild(b1);ac.appendChild(b2);
    f.appendChild(t1);f.appendChild(t2);f.appendChild(ac);
    document.body.appendChild(f);
  }).catch(function(){});
})();
</script>
"""

# Injection de la carte d'annonce dans le panneau administrateur.
_ANCRE_V11_ADMIN = '<div class="carte"><h2>💼 Soldes par application</h2>'
if "_V11-carte-annonce-admin" not in TEMPLATES.get("admin.html", ""):
    if _ANCRE_V11_ADMIN in TEMPLATES["admin.html"]:
        TEMPLATES["admin.html"] = TEMPLATES["admin.html"].replace(
            _ANCRE_V11_ADMIN, _V11_CARTE_ADMIN + _ANCRE_V11_ADMIN, 1)
    else:
        _V10_LOGGER.warning("ancre du panneau admin introuvable : carte d'annonce non inseree")

# Injection du bandeau (CSS + JS) dans le socle commun de TOUTES les pages.
if "id=\"v11-bandeau\"" not in TEMPLATES.get("base.html", "") and "</body>" in TEMPLATES.get("base.html", ""):
    TEMPLATES["base.html"] = TEMPLATES["base.html"].replace(
        "</body>", _V11_CSS + _V11_JS_BANDEAU + "\n</body>", 1)

_v10_log("INFO", "v11_annonces_fin_de_mois_actives",
         traductions=sorted(_V11_TRADUCTIONS_ANNONCE.keys()))


# =============================================================================
# V12 — MOTEUR DE RECHERCHE SPATIAL + BOUCLIER CYBER IA + ASSISTANCE TEMPS RÉEL
# -----------------------------------------------------------------------------
#   • Recherche sémantique locale : densité logarithmique, proximité des mots,
#     tolérance aux fautes (Levenshtein ±1), boost de confiance (réputation V7).
#   • « Temps réel » honnête : dépêches Google News (RSS) + DuckDuckGo collectées
#     À LA DEMANDE, mises en cache 5 minutes, horodatage UTC affiché.
#   • Bouclier cyber : signatures d'attaque (SQLi, XSS, traversal, RCE), anti-DDoS,
#     liste noire permanente, rapport IA + alerte admin, purge administrateur.
#   • Vie privée : les messages privés/groupes/sondages ne sont JAMAIS indexés.
# =============================================================================
import math  # V12 : scoring logarithmique (aucune dépendance externe ajoutée)


def _V12_vers_dict(x):
    """sqlite3.Row n'a pas de .get() : normalise Row/dict en dict réel."""
    if isinstance(x, dict):
        return dict(x)
    try:
        return {k: x[k] for k in x.keys()}
    except Exception:
        return {}


class NoyauSecuriteIA:
    """Bouclier temps réel : analyse chaque valeur de formulaire POSTée."""

    def __init__(self):
        self.liste_noire_permanente = set()
        self.historique_ddos = {}
        self.alertes_admin_cyber = []
        self.signatures_cyber = [
            r"select\s+\*\s+from",       # injection SQL classique
            r"union\s+select",           # attaque par union
            r"drop\s+table",             # destruction de table
            r"delete\s+from",            # suppression massive
            r"insert\s+into",            # injection d'insertion
            r"<script[\s>]",             # injection de script (XSS)
            r"(\.\./){2,}",              # traversal de fichiers
            r"exec\s*\(",                # exécution de code à distance
            r"['\";]\s*--",              # casse de requête SQL
        ]

    def analyser_signature(self, texte, ip_source):
        """Analyse UNE valeur de formulaire — signatures uniquement, sans comptage."""
        if ip_source in self.liste_noire_permanente:
            return False, "Accès refusé : adresse IP en liste noire permanente."
        texte_clean = str(texte).lower().strip()
        for motif in self.signatures_cyber:
            if re.search(motif, texte_clean):
                self.liste_noire_permanente.add(ip_source)
                rapport_ia = self._generer_rapport_ia(texte_clean, motif)
                self.alertes_admin_cyber.append({
                    "ip": ip_source, "date": maintenant(),
                    "payload": str(texte)[:100], "analyse_ia": rapport_ia,
                })
                try:
                    alerter_admin("CYBER_ATTACK",
                                  "Intrusion bloquee depuis l'IP %s. Diagnostic : %s"
                                  % (ip_source, rapport_ia[:140]), gravite="critique")
                except Exception:
                    pass
                try:
                    evenement_v6("cyber_attaque",
                                 "Attaque bloquée depuis l'IP %s. Diagnostic disponible."
                                 % ip_source, lien="/admin/gouvernance")
                except Exception:
                    pass
                return False, "Tentative d'intrusion bloquée. Rapport généré."
        return True, "Signal intègre"

    def verifier_frequence(self, ip_source):
        """Anti-DDoS : UNE vérification par requête (jamais par champ de formulaire)."""
        if ip_source in self.liste_noire_permanente:
            return False, "Accès refusé : adresse IP en liste noire permanente."
        temps_actuel = time.time()
        boucle_locale = ip_source in ("127.0.0.1", "::1", "localhost")
        if ip_source not in self.historique_ddos:
            self.historique_ddos[ip_source] = []
        self.historique_ddos[ip_source].append(temps_actuel)
        self.historique_ddos[ip_source] = [
            t for t in self.historique_ddos[ip_source] if temps_actuel - t < 1]
        if len(self.historique_ddos[ip_source]) > 5 and not boucle_locale:
            self.liste_noire_permanente.add(ip_source)
            try:
                alerter_admin("DDOS_ATTACK",
                              "Saturation DDoS stoppee sur l'IP %s." % ip_source,
                              gravite="haute")
            except Exception:
                pass
            return False, "IP isolée pour comportement DDoS."
        return True, "Signal intègre"

    def analyser_requete(self, texte, ip_source):
        """Compatibilité : signature puis fréquence, pour l'analyse d'un seul champ."""
        autorise, message = self.analyser_signature(texte, ip_source)
        if not autorise:
            return autorise, message
        return self.verifier_frequence(ip_source)

    def _generer_rapport_ia(self, payload_suspect, motif):
        """Diagnostic IA court de l'attaque bloquée (texte pur, jamais bloquant)."""
        try:
            analyse = interroger_llm(
                "Tu es analyste en cybersécurité. Explique en 2 phrases maximum, "
                "sans salutation, le danger de l'attaque suivante.",
                "Motif détecté : %s\nContenu intercepté : %s" % (motif, payload_suspect))
            if analyse:
                return analyse
        except Exception:
            pass
        return "Analyse IA indisponible. Attaque structurelle confirmée par signature."


BOUCLIER_IA = NoyauSecuriteIA()


@app.before_request
def _V12_bouclier_temps_reel():
    """Bouclier V12 : fréquence (1 fois par requête) puis signatures par champ."""
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    if request.path.startswith("/webhooks/"):
        return None
    adresse_ip = _v10_ip()
    autorise, message_erreur = BOUCLIER_IA.verifier_frequence(adresse_ip)
    if autorise:
        for valeur in request.form.values():
            if isinstance(valeur, str):
                autorise, message_erreur = BOUCLIER_IA.analyser_signature(valeur, adresse_ip)
                if not autorise:
                    break
    if not autorise:
        try:
            journal_action("cyber_attaque_bloquee", cible=adresse_ip,
                           details=message_erreur,
                           utilisateur=session.get("uid"))
        except Exception:
            pass
        return message_erreur, 403
    return None


class MoteurRechercheSpatialInfini:
    """Moteur sémantique local + collecte mondiale à la demande (cache 5 min)."""

    def __init__(self):
        self.stop_words = {"le", "la", "les", "de", "des", "un", "une", "en", "que",
                           "qui", "dans", "pour", "par", "au", "aux", "et", "ou",
                           "son", "sa", "ses", "ce", "cette", "est", "sont"}
        self.index_cache = {}
        self.derniere_maj_cache = 0.0
        self.dernier_nettoyage_nocturne = _dt.datetime.now(_dt.timezone.utc).date()
        self.cache_requetes_mondiales = {}
        self.TTL_MONDE_S = 300  # cinq minutes

    def _nettoyer_mot(self, mot):
        mot = str(mot).lower().strip()
        accents = {"á": "a", "à": "a", "â": "a", "é": "e", "è": "e", "ê": "e", "ë": "e",
                   "í": "i", "î": "i", "ó": "o", "ô": "o", "ú": "u", "û": "u", "ç": "c"}
        for acc, rep in accents.items():
            mot = mot.replace(acc, rep)
        return re.sub(r"[^\w\s]", "", mot)

    def _distance_levenshtein(self, s1, s2):
        if len(s1) < len(s2):
            s1, s2 = s2, s1
        if len(s2) == 0:
            return len(s1)
        ligne = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            suivante = [i + 1]
            for j, c2 in enumerate(s2):
                suivante.append(min(ligne[j + 1] + 1, suivante[j] + 1,
                                    ligne[j] + (c1 != c2)))
            ligne = suivante
        return ligne[-1]

    def verifier_et_purger_memoire(self):
        """Purge nocturne paresseuse du cache RAM (index + cache réseau)."""
        aujourdhui = _dt.datetime.now(_dt.timezone.utc).date()
        if aujourdhui > self.dernier_nettoyage_nocturne:
            self.index_cache.clear()
            self.cache_requetes_mondiales.clear()
            self.derniere_maj_cache = 0.0
            self.dernier_nettoyage_nocturne = aujourdhui
            try:
                _v10_log("INFO", "purge_nocturne_cache_ram")
            except Exception:
                pass

    def rafraichir_index_cache(self, base_posts):
        """Index inversé en mémoire — jamais les contenus privés."""
        self.verifier_et_purger_memoire()
        self.index_cache.clear()
        for brut in base_posts:
            post = _V12_vers_dict(brut)
            corps = str(post.get("corps", "") or "")
            if corps.startswith(("[Sondage]", "[Groupe]", "🔒")):
                continue
            mots = [self._nettoyer_mot(m) for m in corps.split() if self._nettoyer_mot(m)]
            for position, mot in enumerate(mots):
                self.index_cache.setdefault(mot, []).append(
                    {"post_id": post.get("id"), "position": position})
        self.derniere_maj_cache = time.time()

    def executer_recherche_avancee(self, requete, base_posts, index_reputation):
        """Classement : densité log, proximité, tolérance aux fautes, confiance."""
        self.verifier_et_purger_memoire()
        mots_bruts = [self._nettoyer_mot(m) for m in str(requete).split()]
        mots_requete = [m for m in mots_bruts if m and m not in self.stop_words]
        if not mots_requete:
            mots_requete = [m for m in mots_bruts if m]
        if not self.index_cache or (time.time() - self.derniere_maj_cache > 60):
            self.rafraichir_index_cache(base_posts)

        scores_posts, positions_mots = {}, {}
        for mot_req in mots_requete:
            correspondances = list(self.index_cache.get(mot_req, []))
            if not correspondances and len(mot_req) > 3:
                for mot_cache in self.index_cache.keys():
                    if self._distance_levenshtein(mot_req, mot_cache) == 1:
                        correspondances = self.index_cache[mot_cache]
                        break
            for entree in correspondances:
                pid = entree["post_id"]
                scores_posts[pid] = scores_posts.get(pid, 0.0) + 1.0
                positions_mots.setdefault(pid, []).append(entree["position"])

        resultats = []
        for brut in base_posts:
            post = _V12_vers_dict(brut)
            pid = post.get("id")
            if pid not in scores_posts:
                continue
            score_final = math.log1p(scores_posts[pid])
            pos = positions_mots[pid]
            if len(pos) > 1:
                score_final += (1.0 / ((max(pos) - min(pos)) + 1)) * 2.0
            reputation = index_reputation.get(post.get("auteur_id"), 50)
            score_final *= (1 + (reputation / 100.0))
            resultats.append({
                "id": pid, "pseudo": post.get("pseudo", "?"),
                "corps": post.get("corps", ""), "cree_le": post.get("cree_le", ""),
                "score": round(score_final, 3),
            })
        resultats.sort(key=lambda x: x["score"], reverse=True)
        return resultats

    def generer_contexte_ia(self, resultats_moteur):
        """Compresse les meilleures trouvailles locales pour le Cortex."""
        if not resultats_moteur:
            return "AUCUNE PUBLICATION LOCALE NE TRAITE CE SUJET."
        bloc = ["=== PUBLICATIONS DE LA COMMUNAUTÉ (locales, publiques) ==="]
        for i, res in enumerate(resultats_moteur[:5], start=1):
            bloc.append("[%d] @%s (%s) : %s" % (i, res["pseudo"], res["cree_le"], res["corps"]))
        return "\n".join(bloc)

    def collecter_monde_temps_reel(self, requete):
        """Dépêches réelles (Google News RSS + DuckDuckGo), cache glissant 5 min.
        Retourne une chaîne vide si aucune dépêche n'est accessible."""
        self.verifier_et_purger_memoire()
        cle = self._nettoyer_mot(requete)
        temps_actuel = time.time()
        if cle in self.cache_requetes_mondiales:
            entree = self.cache_requetes_mondiales[cle]
            if temps_actuel - entree["temps"] < self.TTL_MONDE_S:
                return entree["contenu"]

        lignes = []
        try:
            for item in GoogleNewsClient.chercher(requete, max_resultats=3):
                lignes.append("- [ACTUALITÉ — %s] %s — %s (lien : %s)"
                              % (item.get("source", "Google News"), item.get("titre", ""),
                                 item.get("extrait", ""), item.get("url", "")))
        except Exception:
            pass
        try:
            for item in DuckDuckGoClient.chercher(requete, max_resultats=2):
                lignes.append("- [WEB — %s] %s — %s (lien : %s)"
                              % (item.get("source", "DuckDuckGo"), item.get("titre", ""),
                                 item.get("extrait", ""), item.get("url", "")))
        except Exception:
            pass
        contenu = "\n".join(lignes)
        self.cache_requetes_mondiales[cle] = {"temps": temps_actuel, "contenu": contenu}
        return contenu

    def horodatage_monde(self) -> str:
        return _dt.datetime.now(_dt.timezone.utc).strftime("%d/%m/%Y %H:%M:%S")


MOTEUR_SPATIAL = MoteurRechercheSpatialInfini()


# --- Gabarit du moteur de recherche spatial ----------------------------------
TEMPLATES["v10_recherche_spatiale.html"] = """{% block contenu %}
<div class="carte">
  <h1>🚀 Moteur de recherche spatial</h1>
  <p class="muet">Recherche sémantique dans les publications publiques : densité des mots,
  proximité structurelle, tolérance aux fautes de frappe légères, et mise en avant
  des textes écrits par les comptes les plus fiables (score de confiance).</p>
  <form method="get" class="row">
    <input name="q" value="{{ q }}" placeholder="Tapez votre recherche…" required style="max-width:500px">
    <button>Lancer la recherche</button>
  </form>
</div>

{% if q %}
<div class="carte">
  <h2>Résultats pour : « {{ q }} »</h2>
  <p class="muet">{{ resultats|length }} publication(s) classée(s) par pertinence.</p>
  <ol style="list-style:none;padding:0">
  {% for res in resultats %}
    <li class="carte" style="margin-bottom:14px;border-left:4px solid var(--or)">
      <p class="meta-pub">
        <a href="/u/{{ res.pseudo }}"><b>@{{ res.pseudo }}</b></a>
        <span class="badge">Indice de pertinence : {{ res.score }}</span>
        <span class="muet">· {{ res.cree_le }}</span>
      </p>
      <p style="white-space:pre-wrap">{{ res.corps }}</p>
    </li>
  {% else %}
    <p class="muet">Aucune publication publique ne correspond à votre recherche.</p>
  {% endfor %}
  </ol>
</div>
{% endif %}
{% endblock %}"""


def _V12_recherche_page():
    """/recherche V12 : recherche sémantique locale (remplace l'ancienne page)."""
    q = (request.args.get("q") or "").strip()
    resultats_moteur = []
    if q:
        conn = db()
        posts = conn.execute(
            "SELECT p.id, p.corps, p.cree_le, p.auteur_id, u.pseudo"
            " FROM posts p JOIN users u ON u.id = p.auteur_id"
            " ORDER BY p.id DESC LIMIT 1000").fetchall()
        confiance = {}
        for post in posts:
            aid = post["auteur_id"]
            if aid not in confiance:
                try:
                    confiance[aid] = int(score_reputation(aid)["score"])
                except Exception:
                    confiance[aid] = 50
        resultats_moteur = MOTEUR_SPATIAL.executer_recherche_avancee(q, posts, confiance)
        journal_action2(session.get("uid"), "recherche",
                        objet="recherche spatiale", details=q[:120])
    return page("v10_recherche_spatiale.html", titre="Moteur de recherche spatial",
                q=q, resultats=resultats_moteur)


if app.view_functions.get("recherche_page") is not None:
    app.view_functions["recherche_page"] = _V12_recherche_page


def _V12_chat_envoyer():
    """/chat V12 : Cortex IA ancré sur les publications locales + dépêches du monde."""
    question = (request.form.get("question") or "").strip()
    if not question:
        return redirect(url_for("chat_page"))
    journal_action2(session.get("uid"), "recherche",
                    objet="question au Cortex IA (local + monde)", details=question[:120])

    conn = db()
    posts = conn.execute(
        "SELECT p.id, p.corps, p.cree_le, p.auteur_id, u.pseudo"
        " FROM posts p JOIN users u ON u.id = p.auteur_id"
        " ORDER BY p.id DESC LIMIT 400").fetchall()
    confiance = {}
    for post in posts:
        aid = post["auteur_id"]
        if aid not in confiance:
            try:
                confiance[aid] = int(score_reputation(aid)["score"])
            except Exception:
                confiance[aid] = 50
    trouvailles = MOTEUR_SPATIAL.executer_recherche_avancee(question, posts, confiance)
    bloc_local = MOTEUR_SPATIAL.generer_contexte_ia(trouvailles)
    bloc_monde = MOTEUR_SPATIAL.collecter_monde_temps_reel(question)
    horodatage = MOTEUR_SPATIAL.horodatage_monde()

    invite_socle = (
        "Tu es l'assistant de ToutBot Mundo. Réponds en croisant les deux blocs "
        "fournis : les publications locales de la communauté et les dépêches "
        "mondiales collectées en temps réel. N'invente rien : cite les auteurs "
        "locaux ou les liens des dépêches quand ils existent ; si aucune source "
        "ne permet de répondre, dis-le clairement.")
    corps_invite = "%s\n\n%s\n\nQuestion de l'utilisateur : %s" % (
        bloc_local, ("=== DÉPÊCHES MONDIALES EN TEMPS RÉEL ===\n" + bloc_monde) if bloc_monde
        else "AUCUNE DÉPÊCHE MONDIALE ACCESSIBLE POUR L'INSTANT.", question)

    reponse, avertissement = "", ""
    try:
        reponse = interroger_llm(invite_socle, corps_invite)
    except Exception:
        LOGGER.warning("IA indisponible (chat V12)", exc_info=False)
        avertissement = ("L'IA n'a pas répondu. Aucun contenu n'est inventé : les "
                         "dépêches et résultats réels ci-dessous restent affichés.")

    return page("chat.html", titre="Assistant IA", question=question, reponse=reponse,
                avertissement=avertissement, data=None, resultats_html="",
                bloc_monde=bloc_monde, horodatage_monde=horodatage,
                resultats_locaux=trouvailles[:10])


if app.view_functions.get("chat_envoyer") is not None:
    app.view_functions["chat_envoyer"] = _V12_chat_envoyer


# Section « dépêches + résultats locaux » ajoutée au gabarit du chat.
_ANCRE_CHAT_V12 = "{% if data %}<h3>Résultats réellement collectés</h3>{{ resultats_html|safe }}{% endif %}"
_V12_SECTION_CHAT = _ANCRE_CHAT_V12 + """
{% if bloc_monde %}<h3>🌍 Dépêches du monde en temps réel</h3>
<p class="muet">Collecte à la demande (Google News RSS + DuckDuckGo), mise en cache
5 minutes — horodatage de la collecte : {{ horodatage_monde }} UTC.</p>
<div style="white-space:pre-wrap">{{ bloc_monde }}</div>{% endif %}
{% if resultats_locaux %}<h3>💬 Publications locales liées</h3>
<ol>{% for res in resultats_locaux %}
<li><a href="/u/{{ res.pseudo }}"><b>@{{ res.pseudo }}</b></a> — indice {{ res.score }}
<p class="muet" style="white-space:pre-wrap;margin:2px 0 8px">{{ res.corps }}</p></li>
{% endfor %}</ol>{% endif %}"""
if _ANCRE_CHAT_V12 in TEMPLATES.get("chat.html", ""):
    TEMPLATES["chat.html"] = TEMPLATES["chat.html"].replace(_ANCRE_CHAT_V12, _V12_SECTION_CHAT, 1)


# --- Assistance plaintes enrichie par le monde réel ---------------------------
_V12_PLAINTE_ORIGINALE = app.view_functions.get("plainte_creer")


def _V12_plainte_creer(*args, **kwargs):
    reponse_route = _V12_PLAINTE_ORIGINALE(*args, **kwargs)
    me = utilisateur_courant()
    if me is not None and request.method == "POST":
        sujet = (request.form.get("sujet") or request.form.get("objet") or "").strip()
        corps = (request.form.get("corps") or "").strip()
        if sujet and corps:
            try:
                contexte_legal = MOTEUR_SPATIAL.collecter_monde_temps_reel(
                    "réglementation transfert mobile money litige " + sujet)
                invite = (
                    "Tu es l'assistant d'orientation de ToutBot Mundo. Un membre vient "
                    "de déposer une plainte. En t'appuyant UNIQUEMENT sur les éléments "
                    "de contexte ci-dessous, formule une réponse claire, polie et "
                    "rassurante en 4 phrases maximum. Ne promets aucun remboursement "
                    "direct : l'administrateur prend le relais sous 72 heures.")
                fusion = ("=== CONTEXTE COLLECTÉ EN DIRECT ===\n%s\n\n=== PLAINTE DU MEMBRE ===\n"
                          "Sujet : %s\nExposé : %s" % (contexte_legal or "(aucune source externe)", sujet, corps))
                reponse_ia = interroger_llm(invite, fusion)
                if reponse_ia:
                    conn = db()
                    # SQLite refuse UPDATE ... ORDER BY ... LIMIT : sous-requête MAX(id).
                    conn.execute(
                        "UPDATE plaintes SET reponse = ?"
                        " WHERE id = (SELECT MAX(id) FROM plaintes WHERE user_id = ?)",
                        (reponse_ia[:2000], me["id"]))
                    conn.commit()
            except Exception:
                LOGGER.warning("assistance plainte V12 indisponible", exc_info=False)
    return reponse_route


if _V12_PLAINTE_ORIGINALE is not None:
    app.view_functions["plainte_creer"] = _V12_plainte_creer


# --- Purge administrateur du bouclier ----------------------------------------
@app.route("/admin/cyber/purger", methods=["POST"])
def admin_cyber_purger():
    """Purge la liste noire et les rapports du bouclier (réservée aux admins)."""
    refus = exiger_admin()
    if refus:
        return refus
    me = utilisateur_courant()
    nb_ips = len(BOUCLIER_IA.liste_noire_permanente)
    BOUCLIER_IA.liste_noire_permanente.clear()
    BOUCLIER_IA.alertes_admin_cyber.clear()
    BOUCLIER_IA.historique_ddos.clear()
    try:
        sceller_ecriture("cyber_purge", ref="PURGE-%d" % int(time.time()), montant=0,
                         user_id=me["id"],
                         details="Purge du bouclier : %d IP libérée(s)" % nb_ips)
        journal_action("cyber_purge", cible="Bouclier_IA",
                       details="Remise à zéro par %s" % me["pseudo"], utilisateur=me["id"])
    except Exception:
        pass
    session["flash"] = ("Forteresse réinitialisée : %d adresse(s) IP libérée(s) "
                        "de la liste noire." % nb_ips)
    return redirect(url_for("admin_gouvernance"))


@app.context_processor
def _V12_contexte_cyber():
    """Compteurs du bouclier pour le panneau de gouvernance."""
    try:
        alertes = list(BOUCLIER_IA.alertes_admin_cyber)[-8:]
        nb_attaques = len(BOUCLIER_IA.alertes_admin_cyber)
        bannis = len(BOUCLIER_IA.liste_noire_permanente)
    except Exception:
        alertes, nb_attaques, bannis = [], 0, 0
    return {"nb_attaques_cyber": nb_attaques, "alertes_cyber": alertes,
            "total_bannissements": bannis}


# --- Carte « Forteresse Cyber IA » dans la gouvernance ------------------------
_ANCRE_GOUV_V12 = '<div class="carte"><h2>✍️ Modération automatique</h2>'
_V12_CARTE_GOUV = """<div class="carte" id="v12-forteresse-cyber">
<h2>🛡️ Forteresse Cyber IA — activité en temps réel</h2>
<p class="muet">Adresses IP isolées en liste noire permanente : <b>{{ total_bannissements }}</b>.
Le bouclier analyse chaque formulaire POSTé (signatures SQLi, XSS, traversal, RCE,
anti-DDoS &gt; 5 requêtes/seconde) et consigne chaque assaut avec un diagnostic IA.</p>
<form method="post" action="/admin/cyber/purger" style="margin-bottom:12px"
 onsubmit="return confirm('Libérer TOUTES les adresses IP et effacer les diagnostics ?')">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <button type="submit" class="btn-sec">🧹 Purger la liste noire et réinitialiser le bouclier</button>
</form>
{% if nb_attaques_cyber > 0 %}
  <p class="annonce">🚨 <b>{{ nb_attaques_cyber }}</b> tentative(s) d'intrusion interceptée(s) — derniers rapports :</p>
  {% for alerte in alertes_cyber %}
  <div class="msg">
    <p class="muet"><b>{{ alerte.date }} UTC</b> · provenance : <b>{{ alerte.ip }}</b></p>
    <p class="muet">Contenu filtré : <code>{{ alerte.payload }}</code></p>
    <p class="ia-reponse" style="margin-top:6px"><b>🤖 Diagnostic :</b> {{ alerte.analyse_ia }}</p>
  </div>
  {% endfor %}
{% else %}
  <p class="flash">🛡️ Bouclier actif — aucun comportement malveillant structurel détecté.</p>
{% endif %}
<p class="muet">● Purge nocturne du cache RAM (index + réseau) automatique. ● Les
messages privés, groupes et sondages ne sont jamais indexés. ● Dépêches du monde :
collecte à la demande (Google News RSS + DuckDuckGo), cache glissant de 5 minutes.</p>
</div>
"""
if "_V12-forteresse-cyber" not in TEMPLATES.get("admin_gouvernance.html", ""):
    if _ANCRE_GOUV_V12 in TEMPLATES["admin_gouvernance.html"]:
        TEMPLATES["admin_gouvernance.html"] = TEMPLATES["admin_gouvernance.html"].replace(
            _ANCRE_GOUV_V12, _V12_CARTE_GOUV + _ANCRE_GOUV_V12, 1)
    else:
        _V10_LOGGER.warning("ancre gouvernance introuvable : carte cyber non inseree")

_v10_log("INFO", "v12_moteur_bouclier_actifs",
         signatures=len(BOUCLIER_IA.signatures_cyber), ttl_monde_s=MOTEUR_SPATIAL.TTL_MONDE_S)


# =============================================================================



# =============================================================================



# =============================================================================
# POINT D'ENTRÉE UNIQUE
# =============================================================================
# =============================================================================
# V13 — ASSISTANT WEB INTÉGRÉ (fusion du bloc « flask_app.py ») + TOUS LES IA
# -----------------------------------------------------------------------------
#   Recherche temps réel : Wikipédia + DuckDuckGo (HTML) + Google News (RSS)
#   + les moteurs déjà présents dans l'application (MoteurRecherche : SearXNG…).
#
#   Chaîne d'IA — le premier fournisseur qui répond gagne :
#       1. Mistral     (MISTRAL_API_KEY,  MISTRAL_MODEL)
#       2. Groq        (GROQ_API_KEY,     GROQ_MODEL)
#       3. Gemini      (GEMINI_API_KEY,   GEMINI_MODEL)
#       4. LLM déjà branché de l'application (TOUTBOT_LLM_KEY / POLLINATIONS_KEY)
#       5. repli sans clé (TOUTBOT_LLM_SANS_CLE=1, mettre 0 pour couper)
#
#   Routes ajoutées (endpoints uniques — aucune route existante remplacée) :
#       GET  /assistant      → page autonome de l'assistant (jeton CSRF intégré)
#       POST /poser          → API JSON (alias de /api/poser)
#       POST /api/poser      → API JSON
#       GET  /api/etat-ia    → état des fournisseurs (aucune clé exposée)
#
#   SÉCURITÉ : le contrôle CSRF de la couche V10 reste PLEINEMENT actif, y compris
#   sur ces routes (il couvre aussi /api/ depuis la V10). La page /assistant
#   reçoit le jeton de sa session dans son propre HTML et le renvoie en en-tête
#   X-CSRF-Token ; un appel direct en JSON doit fournir ce même jeton (en-tête
#   X-CSRF-Token ou champ "csrf" du corps).
#
#   AUCUNE CLÉ EN DUR. Idempotent.
# =============================================================================
_V13_INTEGRE = True
_V13_VERSION = "13.0.0"
_V13_ROUTES = ("/assistant", "/poser", "/api/poser", "/api/etat-ia")

try:
    import requests as _v13_requests  # seul usage : transport HTTP (facultatif)
except Exception:  # noqa: BLE001 — sans requests, le repli urllib prend le relais
    _v13_requests = None

_V13_DELAI = float(os.environ.get("TOUTBOT_V13_DELAI", "12"))
_V13_UA = "Mozilla/5.0 (compatible; ToutBot-Mundo/13.0 +https://toutbot.example)"


# -----------------------------------------------------------------------------
# 0) Transport HTTP (requests si présent, sinon urllib) — testable/mockable
# -----------------------------------------------------------------------------
def _v13_http_post_json(url, charge, entetes=None, delai=None):
    """POST JSON → (ok, dictionnaire_ou_message_d_erreur)."""
    delai = delai or _V13_DELAI
    entetes = dict(entetes or {})
    entetes.setdefault("Content-Type", "application/json")
    entetes.setdefault("User-Agent", _V13_UA)
    if _v13_requests is not None:
        try:
            rep = _v13_requests.post(url, headers=entetes, json=charge, timeout=delai)
            rep.raise_for_status()
            return True, rep.json()
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
    try:
        requete = urllib.request.Request(url, data=json.dumps(charge).encode("utf-8"),
                                         headers=entetes, method="POST")
        with urllib.request.urlopen(requete, timeout=delai) as rep:
            return True, json.loads(rep.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _v13_http_get(url, params=None, entetes=None, delai=None):
    """GET → (ok, texte)."""
    delai = delai or _V13_DELAI
    entetes = dict(entetes or {})
    entetes.setdefault("User-Agent", _V13_UA)
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    if _v13_requests is not None:
        try:
            rep = _v13_requests.get(url, headers=entetes, timeout=delai)
            rep.raise_for_status()
            return True, rep.text
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
    try:
        requete = urllib.request.Request(url, headers=entetes, method="GET")
        with urllib.request.urlopen(requete, timeout=delai) as rep:
            return True, rep.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _v13_http_post_form(url, donnees, entetes=None, delai=None):
    """POST formulaire → (ok, texte). Utilisé par DuckDuckGo HTML."""
    delai = delai or _V13_DELAI
    entetes = dict(entetes or {})
    entetes.setdefault("User-Agent", _V13_UA)
    if _v13_requests is not None:
        try:
            rep = _v13_requests.post(url, data=donnees, headers=entetes, timeout=delai)
            rep.raise_for_status()
            return True, rep.text
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
    try:
        entetes.setdefault("Content-Type", "application/x-www-form-urlencoded")
        corps = urllib.parse.urlencode(donnees).encode("utf-8")
        requete = urllib.request.Request(url, data=corps, headers=entetes, method="POST")
        with urllib.request.urlopen(requete, timeout=delai) as rep:
            return True, rep.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# -----------------------------------------------------------------------------
# 1) Sources d'information gratuites (bloc « flask_app.py » fusionné)
# -----------------------------------------------------------------------------
def _v13_nettoyer(texte):
    """Retire le balisage et compacte les espaces d'un extrait."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(texte or ""))).strip()


def _v13_resultat(titre, extrait, url, moteur):
    return {"titre": str(titre or "").strip(), "extrait": _v13_nettoyer(extrait),
            "url": str(url or "").strip(), "moteur": moteur}


def _v13_chercher_wikipedia(question, langue="fr", nb_resultats=3):
    """API Wikipédia : gratuite, sans clé, fiable."""
    ok, brut = _v13_http_get(
        "https://%s.wikipedia.org/w/api.php" % langue,
        {"action": "query", "list": "search", "srsearch": question,
         "srlimit": nb_resultats, "format": "json"})
    if not ok:
        return []
    try:
        donnees = json.loads(brut)
    except ValueError:
        return []
    sortie = []
    for item in (donnees.get("query", {}) or {}).get("search", []) or []:
        titre = item.get("title", "")
        sortie.append(_v13_resultat(
            titre, item.get("snippet", ""),
            "https://%s.wikipedia.org/wiki/%s" % (
                langue, urllib.parse.quote(str(titre).replace(" ", "_"))),
            "Wikipédia"))
    return sortie


def _v13_chercher_duckduckgo(question, nb_resultats=5):
    """Scraping HTML de DuckDuckGo : gratuit, sans clé."""
    ok, html = _v13_http_post_form("https://html.duckduckgo.com/html/", {"q": question})
    if not ok:
        return []
    liens = re.findall(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
    extraits = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html)
    sortie = []
    for i, (url, titre) in enumerate(liens[:max(0, int(nb_resultats))]):
        sortie.append(_v13_resultat(titre, extraits[i] if i < len(extraits) else "",
                                    url, "DuckDuckGo"))
    return sortie


def _v13_chercher_actualites(question, nb_resultats=5):
    """Google News RSS : flux d'actualité gratuit, sans clé."""
    ok, xml = _v13_http_get("https://news.google.com/rss/search",
                            {"q": question, "hl": "fr", "gl": "CI", "ceid": "CI:fr"})
    if not ok:
        return []
    titres = re.findall(r"<title>(.*?)</title>", xml)
    liens = re.findall(r"<link>(.*?)</link>", xml)
    sortie = []
    for i in range(1, min(int(nb_resultats) + 1, len(titres))):
        sortie.append(_v13_resultat(titres[i], "Article de presse",
                                    liens[i] if i < len(liens) else "", "Google News"))
    return sortie


def _v13_collecter_sources(question, maxi=12):
    """Fusionne les trois collecteurs V13 et les moteurs déjà présents dans l'app."""
    question = (question or "").strip()
    if not question:
        return []
    brut = []
    for fonction in (_v13_chercher_wikipedia, _v13_chercher_actualites, _v13_chercher_duckduckgo):
        try:
            brut.extend(fonction(question) or [])
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("V13 %s : %s", getattr(fonction, "__name__", "?"), exc)
    try:  # moteurs de l'application (SearXNG, Wikipédia, Google News, DuckDuckGo)
        donnees = MOTEUR.chercher(question) or {}
        for item in (donnees.get("resultats") or []):
            brut.append(_v13_resultat(
                item.get("titre") or item.get("title") or "",
                (item.get("extrait") or item.get("snippet") or item.get("description")
                 or item.get("resume") or item.get("summary") or ""),
                item.get("url") or item.get("lien") or item.get("link") or "",
                item.get("source") or item.get("moteur") or "Application"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("V13 moteurs de l'application : %s", exc)
    vus, sortie = set(), []
    for item in brut:
        if not (item.get("titre") or item.get("extrait")):
            continue
        cle = (item.get("url") or "") + "|" + (item.get("titre") or "")
        if cle in vus:
            continue
        vus.add(cle)
        sortie.append(item)
        if len(sortie) >= maxi:
            break
    return sortie


# -----------------------------------------------------------------------------
# 2) Chaîne de moteurs d'IA (toutes les clés lues dans l'environnement)
# -----------------------------------------------------------------------------
_V13_FOURNISSEURS = (
    {"nom": "Mistral", "variable_cle": "MISTRAL_API_KEY", "variable_modele": "MISTRAL_MODEL",
     "modele_defaut": "mistral-small-latest",
     "url": "https://api.mistral.ai/v1/chat/completions"},
    {"nom": "Groq", "variable_cle": "GROQ_API_KEY", "variable_modele": "GROQ_MODEL",
     "modele_defaut": "llama-3.3-70b-versatile",
     "url": "https://api.groq.com/openai/v1/chat/completions"},
    {"nom": "Gemini", "variable_cle": "GEMINI_API_KEY", "variable_modele": "GEMINI_MODEL",
     "modele_defaut": "gemini-2.5-flash",
     "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"},
)

_V13_SYSTEME = (
    "Tu es ToutBot Mundo, l'assistant d'information en temps réel de l'application. "
    "Réponds en français, clairement et honnêtement, UNIQUEMENT à partir des sources "
    "fournies ci-dessous ; cite les liens utilisés. Si les sources ne permettent pas de "
    "répondre, dis-le franchement : n'invente jamais un fait, un chiffre ou un lien. "
    "Aucun média : l'application est 100 % texte.")


def _v13_message(question, sources):
    if not sources:
        return ("Question : %s\n\nAucune source n'a pu être collectée. Réponds uniquement pour "
                "l'indiquer et propose de reformuler la question." % question)
    lignes = []
    for s in sources[:12]:
        lignes.append("Titre : %s\nExtrait : %s\nLien : %s"
                      % (s.get("titre", ""), s.get("extrait", ""), s.get("url", "")))
    return "Question : %s\n\nSources :\n%s" % (question, "\n\n".join(lignes))


def _v13_lire_reponse(charge):
    """Extrait le texte d'une réponse au format OpenAI (ou variantes tolérées)."""
    try:
        if isinstance(charge, dict) and charge.get("choices"):
            return str((charge["choices"][0].get("message") or {}).get("content") or "").strip()
        if isinstance(charge, dict):
            return str(charge.get("content") or charge.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _v13_appeler_ia(question, sources):
    """Essaie les fournisseurs d'IA dans l'ordre ; renvoie le premier qui répond."""
    erreurs = []
    message = _v13_message(question, sources)
    systeme = _V13_SYSTEME
    for fournisseur in _V13_FOURNISSEURS:
        cle = (os.environ.get(fournisseur["variable_cle"]) or "").strip()
        if not cle:
            continue
        modele = (os.environ.get(fournisseur["variable_modele"])
                  or fournisseur["modele_defaut"]).strip()
        ok, resultat = _v13_http_post_json(
            fournisseur["url"],
            {"model": modele,
             "messages": [{"role": "system", "content": systeme},
                          {"role": "user", "content": message}]},
            {"Authorization": "Bearer " + cle})
        texte = _v13_lire_reponse(resultat) if ok else ""
        if texte:
            return {"texte": texte, "fournisseur": fournisseur["nom"], "modele": modele,
                    "erreurs": erreurs}
        erreurs.append("%s : %s" % (fournisseur["nom"],
                                    resultat if not ok else "réponse vide"))
    try:  # 4) le LLM déjà branché dans l'application
        texte = interroger_llm(systeme, message)
        if texte:
            return {"texte": texte, "fournisseur": "LLM de l'application", "modele": "",
                    "erreurs": erreurs}
    except Exception as exc:  # noqa: BLE001
        erreurs.append("LLM de l'application : %s" % exc)
    if os.environ.get("TOUTBOT_LLM_SANS_CLE", "1") != "0":  # 5) repli sans clé
        url = os.environ.get("TOUTBOT_LLM_ENDPOINT") or CONFIG.get("AI_ENDPOINT") or ""
        if url:
            modele = os.environ.get("TOUTBOT_LLM_MODEL") or CONFIG.get("AI_MODEL") or "openai"
            ok, resultat = _v13_http_post_json(
                url, {"model": modele,
                      "messages": [{"role": "system", "content": systeme},
                                   {"role": "user", "content": message}]})
            texte = _v13_lire_reponse(resultat) if ok else ""
            if texte:
                return {"texte": texte, "fournisseur": "Repli sans clé", "modele": modele,
                        "erreurs": erreurs}
            erreurs.append("Repli sans clé : %s" % (resultat if not ok else "réponse vide"))
    return {"texte": "", "fournisseur": "", "modele": "", "erreurs": erreurs}


def _v13_etat_ia():
    """État des fournisseurs — n'expose JAMAIS une clé, seulement un booléen."""
    return {
        "version": _V13_VERSION,
        "transport": "requests" if _v13_requests is not None else "urllib",
        "routes": list(_V13_ROUTES),
        "fournisseurs": [
            {"nom": f["nom"], "variable": f["variable_cle"],
             "actif": bool((os.environ.get(f["variable_cle"]) or "").strip()),
             "modele": (os.environ.get(f["variable_modele"]) or f["modele_defaut"])}
            for f in _V13_FOURNISSEURS],
        "llm_application": bool(os.environ.get("TOUTBOT_LLM_KEY")
                                or CONFIG.get("POLLINATIONS_KEY")),
        "repli_sans_cle": os.environ.get("TOUTBOT_LLM_SANS_CLE", "1") != "0",
    }


# -----------------------------------------------------------------------------
# 3) Garde-fou de débit (en mémoire, par IP) sur /poser
# -----------------------------------------------------------------------------
_V13_APPELS = {}


def _v13_throttle(cle, maximum, fenetre=60):
    instant = time.time()
    liste = [t for t in _V13_APPELS.get(cle, []) if instant - t < fenetre]
    if len(liste) >= maximum:
        _V13_APPELS[cle] = liste
        return False
    liste.append(instant)
    _V13_APPELS[cle] = liste
    return True


# -----------------------------------------------------------------------------
# 4) Routes et page de l'assistant
# -----------------------------------------------------------------------------
def _v13_page_assistant():
    """Page autonome de l'assistant — le jeton CSRF de la session est intégré au HTML."""
    return render_template_string(_V13_PAGE_ASSISTANT, csrf=jeton_csrf())


def _v13_poser():
    """Reçoit {"question": "..."} (JSON ou formulaire) → réponse IA + sources réelles.

    Corps vide, JSON absent ou JSON invalide : réponse 400 explicite, jamais une
    erreur 500.
    """
    try:
        maximum = int(os.environ.get("TOUTBOT_V13_MAX_PAR_MINUTE", "30"))
    except ValueError:
        maximum = 30
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.remote_addr or "?")
    if not _v13_throttle("poser:" + ip, maximum):
        return jsonify({"erreur": "Trop de demandes : réessayez dans une minute."}), 429

    donnees = request.get_json(silent=True)
    if not isinstance(donnees, dict):
        donnees = {}
    question = str(donnees.get("question")
                   or request.form.get("question")
                   or request.args.get("q") or "").strip()[:500]
    if not question:
        return jsonify({"erreur": "Question vide.",
                        "attendu": {"question": "votre question"}}), 400

    sources = _v13_collecter_sources(question)
    resultat = _v13_appeler_ia(question, sources)
    return jsonify({
        "question": question,
        "reponse": resultat["texte"] or None,
        "fournisseur": resultat["fournisseur"] or None,
        "modele": resultat["modele"] or None,
        "erreurs_ia": resultat["erreurs"],
        "total_sources": len(sources),
        "sources": sources,
    })


def _v13_repondre_etat():
    return jsonify(_v13_etat_ia())


def _v13_enregistrer_routes():
    """Ajoute les quatre routes V13 (aucune route ni aucun endpoint existant touché).

    Le contrôle CSRF de la couche V10 n'est NI retiré NI contourné : la page
    /assistant livre le jeton de la session à son propre JavaScript.
    """
    ajoutees = []
    for chemin, point, vue, methodes in (
            ("/api/poser", "v13_poser", _v13_poser, ["POST"]),
            ("/poser", "v13_poser_alias", _v13_poser, ["POST"]),
            ("/assistant", "v13_assistant", _v13_page_assistant, ["GET"]),
            ("/api/etat-ia", "v13_etat_ia", _v13_repondre_etat, ["GET"])):
        if point not in app.view_functions:
            app.add_url_rule(chemin, point, vue, methods=methodes)
            ajoutees.append(chemin)
    return ajoutees


def _v13_ajouter_lien_navigation():
    """Ajoute un lien « Assistant IA » dans le gabarit de base, si l'ancre existe."""
    try:
        base = TEMPLATES.get("base.html")
        if not base or "/assistant" in base:
            return False
        lien = '<a href="/assistant" title="Assistant web : recherche + IA">Assistant IA</a>'
        for ancre in ("</nav>", "</header>", "<main"):
            if ancre in base:
                TEMPLATES["base.html"] = base.replace(ancre, lien + ancre, 1)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


_V13_ROUTES_AJOUTEES = _v13_enregistrer_routes()
_V13_LIEN_NAVIGATION = _v13_ajouter_lien_navigation()

try:
    LOGGER.info("V13 : assistant web intégré (%s) — %s | IA : %s",
                _V13_VERSION, ", ".join(_V13_ROUTES_AJOUTEES) or "déjà en place",
                ", ".join(f["nom"] for f in _V13_FOURNISSEURS))
except Exception:  # noqa: BLE001
    pass


_V13_PAGE_ASSISTANT = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ToutBot Mundo — Assistant IA</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: Arial, sans-serif; background: #0f172a; color: #f1f5f9;
         display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
  header { width: 100%; text-align: center; padding: 20px; background: #1e293b;
           border-bottom: 2px solid #38bdf8; }
  h1 { color: #38bdf8; }
  header p { font-size: 0.9em; color: #cbd5e1; }
  header a { color: #7dd3fc; text-decoration: none; }
  #etat { font-size: 0.8em; color: #94a3b8; margin-top: 6px; }
  #zone { width: 100%; max-width: 700px; flex: 1; padding: 20px; overflow-y: auto; }
  .message { padding: 12px 16px; border-radius: 12px; margin: 10px 0; line-height: 1.5;
             white-space: pre-wrap; }
  .utilisateur { background: #38bdf8; color: #0f172a; margin-left: 20%; }
  .bot { background: #1e293b; margin-right: 10%; }
  .moteur { color: #94a3b8; font-size: 0.78em; font-style: italic;
            background: transparent; padding: 0 16px; }
  .source { display: block; color: #7dd3fc; font-size: 0.85em; margin-top: 4px;
            text-decoration: none; word-break: break-all; }
  form { width: 100%; max-width: 700px; display: flex; gap: 8px; padding: 16px; }
  input[type=text] { flex: 1; padding: 12px; border-radius: 8px; border: none; font-size: 1em; }
  button { padding: 12px 24px; border: none; border-radius: 8px; background: #38bdf8;
           color: #0f172a; font-weight: bold; cursor: pointer; }
  button:hover { background: #7dd3fc; }
  .charger { text-align: center; color: #94a3b8; padding: 10px; display: none; }
</style>
</head>
<body>
<header>
  <h1>ToutBot Mundo — Assistant IA</h1>
  <p>Recherche en temps réel : Wikipédia, presse, DuckDuckGo + moteurs d'IA</p>
  <p><a href="/">&larr; Retour à l'application</a></p>
  <p id="etat"></p>
</header>

<div id="zone"></div>
<div class="charger" id="charger">Recherche en cours…</div>

<form id="formulaire">
  <input type="text" id="question" placeholder="Posez votre question…" autofocus>
  <button type="submit">Envoyer</button>
</form>

<script>
const zone = document.getElementById('zone');
const formulaire = document.getElementById('formulaire');
const champ = document.getElementById('question');
const charger = document.getElementById('charger');
const etat = document.getElementById('etat');
const CSRF = "{{ csrf }}";

function afficher(texte, classe) {
  const div = document.createElement('div');
  div.className = 'message ' + classe;
  div.textContent = texte;
  zone.appendChild(div);
  zone.scrollTop = zone.scrollHeight;
  return div;
}

function afficherSources(sources) {
  const div = document.createElement('div');
  div.className = 'message bot';
  div.textContent = 'Sources vérifiables (' + sources.length + ')';
  sources.forEach(s => {
    if (!s.extrait && !s.url) return;
    const p = document.createElement('div');
    p.textContent = '• ' + (s.titre || '') + (s.moteur ? ' [' + s.moteur + ']' : '')
                    + ' : ' + (s.extrait || '');
    div.appendChild(p);
    if (s.url) {
      const a = document.createElement('a');
      a.className = 'source';
      a.href = s.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = s.url.substring(0, 90);
      div.appendChild(a);
    }
  });
  zone.appendChild(div);
  zone.scrollTop = zone.scrollHeight;
}

fetch('/api/etat-ia').then(r => r.json()).then(d => {
  if (!d || !d.fournisseurs) return;
  const actifs = d.fournisseurs.filter(f => f.actif).map(f => f.nom);
  etat.textContent = 'IA : ' + (actifs.length ? actifs.join(' → ')
                        : 'aucune clé posée (repli sans clé)');
}).catch(() => {});

formulaire.addEventListener('submit', async (e) => {
  e.preventDefault();
  const question = champ.value.trim();
  if (!question) return;
  afficher(question, 'utilisateur');
  champ.value = '';
  charger.style.display = 'block';
  try {
    const rep = await fetch('/api/poser', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': CSRF},
      body: JSON.stringify({question})
    });
    const data = await rep.json();
    if (data.erreur) {
      afficher('Erreur : ' + data.erreur, 'bot');
    } else {
      if (data.reponse) {
        afficher(data.reponse, 'bot');
        afficher('Réponse produite par : ' + data.fournisseur, 'moteur');
      } else {
        afficher("Aucun moteur d'IA n'a répondu. Les sources réelles collectées sont "
                 + "affichées ci-dessous — rien n'est inventé.", 'bot');
      }
      if (data.sources && data.sources.length) afficherSources(data.sources);
    }
  } catch (err) {
    afficher('Connexion impossible : ' + err, 'bot');
  }
  charger.style.display = 'none';
});
</script>
</body>
</html>
"""
# =============================================================================
# FIN V13
# =============================================================================

_TOUTBOT_EXEC_MAIN = (__name__ == "__main__")


# =============================================================================
# ANNEXE A — DOCUMENTATION D'ORIGINE (LISEZMOI_V7.txt)
# Non exécutée : conservée pour référence, préfixée par « # ».
# =============================================================================
# TOUTBOT MUNDO V7 — LES 16 FONCTIONNALITÉS SUPPLÉMENTAIRES
# ==========================================================
# Fichier livré : TOUTBOT_MUNDO_V7.py  (mono-fichier, dérivé de TOUTBOT_MUNDO_V6.py)
# Tests fournis : test_v7.py            (batterie de non-régression V7)
# Dépendances   : flask (obligatoire), flask-sock (OPTIONNEL : WebSocket admin),
#                 pywebpush (OPTIONNEL : envois push réels)
#
# LA LOI DE L'APPLICATION EST INTACTE
# -----------------------------------
# Réseau social 100 % TEXTE : aucune image, aucune vidéo, aucun audio, aucune pièce
# jointe, y compris par lien. Les lives restent des salons de discussion ÉCRITS.
# Toutes les extensions V7 respectent cette loi — la correspondance est détaillée
# au point « CONFORMITÉ TEXTE » ci-dessous.
#
# DÉMARRAGE
# ---------
#   pip install flask flask-sock
#   python TOUTBOT_MUNDO_V7.py --seed-admin zeusad --telephone 90000000
#   SECRET_KEY=votre-cle python TOUTBOT_MUNDO_V7.py --port 8099
#   python TOUTBOT_MUNDO_V7.py --demo
#   python test_v7.py
#
# VARIABLES D'ENVIRONNEMENT UTILES (aucune clé n'est fournie : l'application ne
# simule jamais un envoi réel si la clé manque)
# ------------------------------------------------------------------------------
#   TOUTBOT_ALERTE_TELEPHONE   numéro d'administrateur (+229...), jamais inventé
#   TOUTBOT_ALERTE_SEUIL_FCFA  seuil d'alerte de solde
#   TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM      alertes SMS
#   WHATSAPP_TOKEN / WHATSAPP_PHONE_ID                        alertes WhatsApp
#   TOUTBOT_ALERTE_WEBHOOK                                    webhook générique
#   VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT       notifications push
#   TOUTBOT_LLM_ENDPOINT / TOUTBOT_LLM_MODEL / TOUTBOT_LLM_KEY (résumés IA,
#       rapports narratifs, chat ancré) — sans clé, repli par RÈGLES sur les
#       données réelles uniquement
#   TOUTBOT_GARANTIE_PCT        taux du fonds de garantie (défaut 1 %)
#
# LES 16 FONCTIONNALITÉS
# ----------------------
#  1. Score de réputation / confiance (0-100)
#     Ancienneté, abonnements validés reçus, pourboires, badges, ajustements
#     manuels, moins plaintes ouvertes et signaux de modération. Détail composante
#     par composante, classement, page /reputation, ajustement manuel réservé à la
#     permission « reputation ».
#
#  2. Multi-administrateurs : rôles + permissions granulaires
#     Rôles livrés : super_admin, moderateur, comptable. 12 permissions codées
#     (moderer, groupes, utilisateurs, comptabilite, audit_lecture, alertes,
#     reputation, anti_spam, dao, fonds_garantie, roles, rgpd). Contrôle côté
#     serveur sur chaque route (exiger_permission), pas seulement dans l'interface.
#     Page /admin/roles.
#
#  3. Journal d'audit IMMUABLE par chaîne de hachage
#     Table audit_chain : hash_courant = SHA-256(hash_précédent | charge canonique).
#     Deux déclencheurs SQLite interdisent UPDATE et DELETE. /admin/audit/verifier
#     recalcule toute la chaîne depuis la graine et nomme la ligne rompue.
#     Scellement idempotent des écritures existantes (portefeuille, abonnements,
#     pourboires validés).
#
#  4. Alertes SMS / WhatsApp à l'administrateur
#     Fournisseurs réels : Twilio (SMS), WhatsApp Cloud API (Graph v20), webhook
#     générique. Déclenchées par seuil de solde à verser, plainte grave (lexique de
#     gravité) et signaux anti-spam. Si la clé manque, l'alerte est CONSIGNÉE et
#     annoncée « non configurée » — jamais présentée comme envoyée. Page
#     /admin/alertes avec journal.
#
#  5. Résumé quotidien automatique du fil (IA)
#     Synthèse des publications réellement enregistrées, envoyée par MESSAGE privé
#     aux membres qui l'ont demandé (opt-in), notification associée. Sans clé d'IA,
#     résumé par règles (extraits + mots-clés) : aucune invention. Déclenchement
#     automatique après l'heure configurée, ou manuel par l'administrateur.
#
#  6. Détection spam / multi-comptes
#     Empreinte comportementale (cadence de publication et de messages, textes
#     répétés, compte neuf très actif) + numéro de téléphone haché + IP/agent
#     hachés (SHA-256, jamais en clair). Score 0-100, signaux explicites, niveau et
#     recommandation. Page /admin/anti-spam + balayage à la demande + alerte.
#
#  7. Chat IA multilingue (français, fon, baoulé)
#     Le français interroge les moteurs réels puis formule une réponse ancrée sur
#     les résultats ; l'application répond « je ne trouve rien de publié » plutôt
#     que d'inventer. Pour le fon et le baoulé, la couverture LIMITÉE est annoncée
#     noir sur blanc : formulation par lexique interne vérifié, et sources réelles
#     affichées telles quelles, non traduites. API /api/chat-multilingue.
#
#  8. Rapports comptables narratifs (IA)
#     Chiffres tirés du grand livre (brut, commissions, net réglementaire en
#     cumul 25 % + 15 FCFA/transaction, écritures en attente, versements, fonds de
#     garantie, top créateurs), avec interdiction explicite d'inventer un chiffre.
#     Repli par règles avec les mêmes chiffres. Page /admin/rapport-narratif.
#
#  9. Mode hors-ligne avancé
#     Cache des 50 dernières publications TEXTUELLES (/api/hors-ligne/flux) +
#     service worker dédié (toutbot-v7-texte). Un texte rédigé sans réseau est mis
#     en file d'attente locale ET côté serveur, puis publié à la reconnexion en
#     repassant par le garde-fou texte et la modération. Un média glissé dans la
#     file est refusé (400), même hors-ligne.
#
# 10. Notifications push (PWA)
#     Abonnements enregistrés (/api/push/abonner), clés VAPID exposées
#     (/api/push/cles), charge utile strictement TEXTE (titre, texte, lien).
#     Messages, « J'aime », validations de paiement et résumés déclenchent l'envoi.
#     Sans pywebpush ou sans clés VAPID, repli explicite sur la notification
#     interne — l'état est annoncé, jamais maquillé en succès.
#
# 11. Lecteur vocal (TTS) — accessibilité
#     Synthèse vocale du NAVIGATEUR (SpeechSynthesisUtterance), déclenchée par un
#     bouton « Écouter » sur le fil. Aucun fichier audio n'est créé, téléversé ni
#     conservé : aucune route audio n'existe. Réglages dans /parametres.
#
# 12. Thèmes de profil par créateur
#     Couleurs CSS uniquement (codes hexadécimaux à 6 chiffres, refus de toute URL
#     ou image), affichées sur le profil public tout en restant 100 % texte.
#
# 13. Badges / achievements
#     8 badges justifiés par des compteurs réels (publications, abonnés,
#     abonnements validés, pourboires, commentaires, groupes, ancienneté sans
#     sanction). Texte + emoji : aucun téléversement d'image. Page /badges.
#
# 14. Export RGPD complet + gel du compte
#     /rgpd/export : dossier JSON complet (identité, publications, commentaires,
#     relations, messages, plaintes, portefeuille, paiements, écritures d'audit,
#     badges, réputation, empreintes hachées). Gel temporaire choisi par le MEMBRE
#     (≠ sanction) : lecture, export et réactivation toujours ouverts, écritures
#     sociales suspendues.
#
# 15. DAO légère
#     Propositions des membres, votes pondérés par les tickets payés (plafonnés),
#     clôture et résumé chiffré. Avertissement affiché : le résultat d'un scrutin
#     sur le TAUX DE COMMISSION n'est jamais appliqué automatiquement — il est
#     proposé à l'administrateur, qui décide.
#
# 16. Fonds de garantie « non-paiement »
#     Prélèvement d'un pourcentage du net validé (défaut 1 %), alimenté
#     automatiquement à chaque validation et scellé dans la chaîne d'audit.
#     Décaissement sur décision de l'administrateur, refusé si le fonds est
#     insuffisant, avec crédit du portefeuille du bénéficiaire.
#
# CONFORMITÉ TEXTE — CORRESPONDANCE POINT PAR POINT
# -------------------------------------------------
#   • TTS          → synthèse locale du navigateur, aucun audio téléversé ni stocké
#   • Thèmes       → couleurs CSS uniquement, aucune image
#   • Lives        → discussion ÉCRITE temps réel, aucun WebRTC/audio/vidéo (V6)
#   • Badges       → texte + emoji, jamais d'image envoyée
#   • Push         → charge utile texte seulement
#   • Hors-ligne   → cache de textes seulement, garde-fou actif à la reconnexion
#   Le garde-fou serveur de la V6 (fichiers joints, champs JSON média, balises
#   <img>/<video>/<audio>, data-URI, URL de média, schémas javascript:/vbscript:)
#   est intact et s'applique aussi aux nouvelles routes d'écriture.
#
# AVERTISSEMENTS RÉGLEMENTAIRES — À FAIRE VALIDER PAR UN PROFESSIONNEL
# -------------------------------------------------------------------
#   1. FONDS DE GARANTIE : un fonds qui indemnise un non-paiement s'apparente à une
#      activité d'ASSURANCE, réservée à des sociétés agréées (code CIMA dans
#      l'UEMOA). La BCEAO encadre par ailleurs la monnaie électronique, le KYC et
#      l'AML.
#   2. DAO : faire voter des tiers sur le TAUX DE COMMISSION d'un service de
#      paiement pose un problème de rémunération et de gouvernance. Dans ce
#      livrable, le résultat est seulement PROPOSÉ à l'administrateur.
#   3. ESCROW, PRÊTS ENTRE MEMBRES, CONVERSION FCFA/USDT (repris du V5) restent
#      soumis à la réglementation BCEAO/UEMOA.
#   4. RGPD : l'export vise le RGPD européen ; à croiser avec la loi béninoise
#      n° 2017-20 (code du numérique) et l'APDP.
#   Ce logiciel est fourni à titre d'expérimentation technique.
#
# TESTS
# -----
# test_v7.py couvre les 16 fonctionnalités, la préservation de la loi 100 % texte,
# l'immuabilité de la chaîne d'audit (tentative de modification et de suppression
# bloquée, falsification détectée), les refus de permission, la honnêteté des replis
# (alertes et push non configurés), le refus des médias hors-ligne et le gel du
# compte. Lancement : python test_v7.py
#
# ==============================================================================
# EXTENSIONS V8 — CLIC EXPLOITABLE PARTOUT + HISTORIQUE PERSONNEL
# ==============================================================================
# Appliquées sur TOUTBOT_MUNDO_V7.py (aucune dépendance nouvelle, aucune image,
# aucun audio : la loi 100 % texte est intacte). Tests ajoutés : smoke_v8.py.
#
# A. CLIQUABILITÉ POUSSÉE À L'EXTRÊME (toutes les pages)
#    • Ondes tactiles sur chaque bouton, tuile et case de chiffres.
#    • Tuiles, cases KPI, badges, pastilles, étiquettes « en ligne » et numéros :
#      un clic (ou Entrée/Espace au clavier) les COPIE, avec retour visuel.
#    • Lignes de tableaux : un clic copie la ligne entière.
#    • Double-clic sur un paragraphe : copie du paragraphe.
#    • Chaque élément interactif gagne tabindex + role=button + focus visible ;
#      hover (bordure dorée), active (léger retrait), transitions fluides.
#    • Squelettes de chargement (animation « brillance ») et respect strict de
#      prefers-reduced-motion (toutes les animations sont désactivées si demandé).
#    • Garde-fou navigateur : un champ fichier est vidé et un dépôt (drop) est
#      refusé avec le message « Aucun média : cette application est 100 % texte ».
#
# B. HISTORIQUE PERSONNEL DE CHAQUE UTILISATEUR
#    • Nouvelle table historique_utilisateur (SQLite, ajoutée au schéma initial,
#      migrée en douceur sur les bases existantes).
#    • Toute l'activité de chaque membre est consignée CHEZ LUI SEUL :
#      connexion, déconnexion, publications, « J'aime », commentaires, suivi,
#      abonnements payants (montant), pourboires, messages privés, discussions
#      ouvertes, plaintes, portefeuille, profils visités, recherches et questions
#      à l'IA, réglages du compte, boutique, prêts, sondages, stories, lives,
#      conversions FCFA ↔ USDT, notifications.
#    • Page /mon-historique (lien 🕓 dans le bandeau, raccourci clavier H) :
#      compteurs par catégorie, filtres cliquables, recherche, pagination,
#      copie d'une ligne ou de tout l'historique, export CSV (Excel français),
#      export JSON (/api/mon-historique), effacement total par son propriétaire
#      (l'effacement lui-même est consigné).
#    • ISOLATION TOTALE : la route filtre toujours par l'identifiant de session ;
#      personne d'autre — pas même l'administrateur — ne peut lire l'historique
#      d'autrui. Vérifié par smoke_v8.py (31/31).
#
# C. NON-RÉGRESSION
#    • test_v7.py : 130/130 · test_v6_sur_v7.py : 50/50 · smoke_v8.py : 31/31.

# =============================================================================
# ANNEXE B — BATTERIES DE TESTS D'ORIGINE (non exécutées)
# test_v7.py · smoke_v8.py · test_monetisation_v8.py
# Non exécutables telles quelles après fusion : elles isolaient leur base par
# variable d'environnement AVANT d'importer le module ; désormais tout est
# déjà chargé dans ce fichier. Sources conservées intégralement ci-dessous.
# =============================================================================

# -----------------------------------------------------------------------------
# FICHIER D'ORIGINE : test_v7.py
# -----------------------------------------------------------------------------
# # -*- coding: utf-8 -*-
# """Tests de non-régression V7 — TOUTBOT MUNDO.
#
# Vérifie les 16 fonctionnalités des extensions V7, ainsi que le maintien de la loi
# fondamentale de l'application : 100 % TEXTE (aucun média, aucun fichier).
# Lancement : python test_v7.py
# """
# import io
# import json
# import os
# import pathlib
# import sys
# import tempfile
#
# BASE = pathlib.Path(tempfile.mkdtemp(prefix="toutbot_v7_"))
# os.environ["TOUTBOT_DB"] = str(BASE / "test_v7.db")
# os.environ["SECRET_KEY"] = "test-secret-v7"
# os.environ.pop("TOUTBOT_ALERTE_TELEPHONE", None)
# os.environ.pop("TWILIO_ACCOUNT_SID", None)
#
# sys.path.insert(0, "/home/user/toutbot")
# import TOUTBOT_MUNDO_V7 as M  # noqa: E402
#
# RESULTATS = []
#
#
# def verifier(nom, condition, detail=""):
#     RESULTATS.append((nom, bool(condition)))
#     print(("  OK   " if condition else "  ECHEC") + " | " + nom + (("  (%s)" % detail) if detail else ""))
#     return bool(condition)
#
#
# def session_pour(uid, csrf="test"):
#     with CLIENT.session_transaction() as sess:
#         sess["uid"] = uid
#         sess["csrf"] = csrf
#
#
# def compter(table, ou="", params=()):
#     with M.app.app_context():
#         requete = "SELECT COUNT(*) AS n FROM " + table + ((" WHERE " + ou) if ou else "")
#         try:
#             return M.db().execute(requete, params).fetchone()["n"]
#         except Exception:
#             return 0
#
#
# def table_existe(nom):
#     with M.app.app_context():
#         return M.db().execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
#                               (nom,)).fetchone() is not None
#
#
# def posts_de(uid):
#     return compter("posts", "auteur_id = ?", (uid,))
#
#
# print("=" * 78)
# print("TESTS V7 — base : %s" % M.CONFIG["DB"])
# print("=" * 78)
#
# print("\n[0] Socle V7 et héritage V6")
# routes = [str(r) for r in M.app.url_map.iter_rules()]
# for chemin in ("/reputation", "/admin/roles", "/admin/audit", "/admin/audit/verifier", "/admin/alertes",
#                "/admin/anti-spam", "/admin/rapport-narratif", "/resume-quotidien", "/hors-ligne",
#                "/chat-multilingue", "/push", "/badges", "/rgpd", "/rgpd/export", "/dao",
#                "/fonds-garantie", "/admin/fonds-garantie", "/ws/admin", "/live", "/api/hors-ligne/flux"):
#     if chemin not in routes:
#         verifier("Route %s enregistrée" % chemin, False, "absente")
# attendues = ("/reputation", "/admin/roles", "/admin/audit", "/admin/audit/verifier", "/admin/alertes",
#              "/admin/anti-spam", "/admin/rapport-narratif", "/resume-quotidien", "/hors-ligne",
#              "/chat-multilingue", "/push", "/badges", "/rgpd", "/rgpd/export", "/dao",
#              "/fonds-garantie", "/admin/fonds-garantie", "/live", "/api/hors-ligne/flux",
#              "/admin/temps-reel/flux")
# verifier("Toutes les routes V6 et V7 sont enregistrées",
#          all(c in routes for c in attendues),
#          "routes manquantes : %s" % [c for c in attendues if c not in routes])
# verifier("WebSocket d'administration enregistré (flask-sock disponible : %s)" % M._WS_DISPONIBLE,
#          ("/ws/admin" in routes) == bool(M._WS_DISPONIBLE),
#          "/ws/admin présent : %s" % ("/ws/admin" in routes))
# verifier("Tables V7 créées (audit_chain, roles_membres, fonds_garantie, dao_propositions)",
#          all(compter(t) == 0 for t in ("audit_chain", "roles_membres", "fonds_garantie", "dao_propositions")))
# with M.app.app_context():
#     _colonnes = [r["name"] for r in M.db().execute("PRAGMA table_info(users)").fetchall()]
# verifier("Colonnes de gel ajoutées à users",
#          all(colonne in _colonnes for colonne in ("gele", "gele_jusqua", "gele_motif")),
#          [c for c in ("gele", "gele_jusqua", "gele_motif") if c not in _colonnes])
#
# ADMIN = M.graine_admin("zeusad", "90000001", "motdepasse")
# with M.app.app_context():
#     KOFI = M.creer_utilisateur("90000002", "kofiid", "motdepasse")
#     AMINA = M.creer_utilisateur("90000003", "aminaa", "motdepasse")
#     TIERS = M.creer_utilisateur("90000004", "tiersx", "motdepasse")
# verifier("Comptes de test créés", ADMIN and KOFI and AMINA and TIERS)
#
# CLIENT = M.app.test_client()
#
# # Un contexte d'application reste ouvert pour tout le déroulé du script : les appels
# # directs aux fonctions du module (hors requête) sont ainsi tous couverts.
# _CONTEXTE_V7 = M.app.app_context()
# _CONTEXTE_V7.push()
#
# print("\n[1] La loi de l'application reste ABSOLUE : 100 % texte")
# session_pour(KOFI)
# r = CLIENT.post("/publier", data={"csrf": "test", "corps": "bonjour",
#                                   "piece": (io.BytesIO(b"binaire"), "photo.png")},
#                 content_type="multipart/form-data")
# verifier("Fichier joint toujours refusé (400)", r.status_code == 400, r.status_code)
# r = CLIENT.post("/publier", data={"csrf": "test", "corps": "voir https://exemple.tld/clip.mp4"})
# verifier("Lien média toujours refusé (400)", r.status_code == 400, r.status_code)
# r = CLIENT.post("/publier", data={"csrf": "test", "corps": "<video src=x></video>"})
# verifier("Balise média toujours refusée (400)", r.status_code == 400, r.status_code)
# r = CLIENT.post("/api/live/1/message", json={"corps": "salut", "audio": "data:audio/mp3;base64,AA"})
# verifier("Champ média JSON toujours refusé (400)", r.status_code == 400, r.status_code)
# verifier("Aucune table de média n'existe dans le schéma V7",
#          not [t for t in ("images", "videos", "audios", "fichiers", "media", "pieces_jointes")
#               if table_existe(t)])
#
# print("\n[2] Score de réputation / confiance")
# session_pour(KOFI)
# CLIENT.post("/publier", data={"csrf": "test", "corps": "Texte de départ pour la réputation."})
# with M.app.app_context():
#     rep = M.score_reputation(KOFI)
#     _ = rep
# verifier("Score calculé dans l'intervalle 0-100", 0 <= rep["score"] <= 100, rep["score"])
# verifier("Score détaillé par composantes", len(rep["composantes"]) >= 6, len(rep["composantes"]))
# with M.app.app_context():
#     avant = M.score_reputation(KOFI)["score"]
#     apres = M.ajuster_reputation(KOFI, 10, "test hausse", ADMIN)
#     baisse = M.ajuster_reputation(KOFI, -20, "test baisse", ADMIN)
#     _ = (avant, apres, baisse)
# verifier("Ajustement manuel : hausse appliquée", apres > avant, "%s -> %s" % (avant, apres))
# verifier("Ajustement manuel : baisse appliquée", baisse < apres, "%s -> %s" % (apres, baisse))
# r = CLIENT.get("/reputation")
# verifier("Page Réputation accessible (200)", r.status_code == 200, r.status_code)
# with M.app.app_context():
#     _score_page = M.score_reputation(KOFI)["score"]
# verifier("Score affiché sur la page (valeur numérique)", str(_score_page).encode() in r.data, _score_page)
#
# print("\n[3] Multi-administrateurs : rôles et permissions granulaires")
# session_pour(ADMIN)
# r = CLIENT.post("/admin/roles/attribuer", data={"csrf": "test", "identifiant": "kofiid", "role": "moderateur"})
# verifier("Attribution d'un rôle depuis l'interface (302)", r.status_code == 302, r.status_code)
# with M.app.app_context():
#     perms = M.permissions_de(KOFI)
# verifier("Rôle modérateur : permission « moderer » accordée", "moderer" in perms, perms)
# verifier("Rôle modérateur : permission « comptabilite » REFUSÉE", "comptabilite" not in perms, perms)
# verifier("Le super-admin garde toutes les permissions",
#          len(M.permissions_de(ADMIN)) == len(M.PERMISSIONS_V7))
# session_pour(KOFI)
# r = CLIENT.get("/admin/roles")
# verifier("Page Rôles refusée à un modérateur (403)", r.status_code == 403, r.status_code)
# r = CLIENT.get("/admin/audit")
# verifier("Journal d'audit refusé au modérateur sans permission (403)", r.status_code == 403, r.status_code)
# r = CLIENT.get("/admin/anti-spam")
# verifier("Anti-spam accessible au modérateur (200)", r.status_code == 200, r.status_code)
# session_pour(ADMIN)
# with M.app.app_context():
#     M.attribuer_role(AMINA, "comptable", ADMIN)
#     verifier("Rôle comptable : permission « comptabilite » accordée",
#              "comptabilite" in M.permissions_de(AMINA))
# session_pour(AMINA)
# r = CLIENT.get("/admin/rapport-narratif")
# verifier("Rapport comptable accessible au comptable (200)", r.status_code == 200, r.status_code)
# session_pour(ADMIN)
# r = CLIENT.get("/admin/roles")
# verifier("Page Rôles accessible au super-admin (200)", r.status_code == 200, r.status_code)
#
# print("\n[4] Journal d'audit immuable (chaîne de hachage SHA-256)")
# with M.app.app_context():
#     _abo_audit = M.creer_abonnement(TIERS, KOFI, 250)
#     M.valider_abonnement(_abo_audit, ADMIN)
#     total_auto = M.verifier_chaine_audit()["total"]
#     n_premier = M.sceller_ecritures_existantes()
#     n_second = M.sceller_ecritures_existantes()
#     etat_ok = M.verifier_chaine_audit()
# verifier("Écritures scellées automatiquement à chaque validation",
#          etat_ok["total"] >= 2 and etat_ok["ok"] is True,
#          "total %s (dont %s scellées à la validation)" % (etat_ok["total"], total_auto))
# verifier("Scellement idempotent : le rattrapage ne crée aucun doublon",
#          n_premier >= 1 and n_second == 0, "premier %s / second %s" % (n_premier, n_second))
# verifier("Chaîne vérifiée sans rupture", etat_ok["ok"] is True, etat_ok)
# bloque_modif = bloque_suppr = False
# with M.app.app_context():
#     conn = M.db()
#     premier = conn.execute("SELECT id FROM audit_chain ORDER BY id ASC LIMIT 1").fetchone()["id"]
#     try:
#         conn.execute("UPDATE audit_chain SET montant = montant + 1 WHERE id = ?", (premier,))
#         conn.commit()
#     except Exception:
#         bloque_modif = True
#     try:
#         conn.execute("DELETE FROM audit_chain WHERE id = ?", (premier,))
#         conn.commit()
#     except Exception:
#         bloque_suppr = True
# verifier("Modification d'une écriture interdite par déclencheur SQLite", bloque_modif)
# verifier("Suppression d'une écriture interdite par déclencheur SQLite", bloque_suppr)
# with M.app.app_context():
#     conn = M.db()
#     conn.execute("DROP TRIGGER IF EXISTS audit_chain_ajout_seul_maj")
#     conn.execute("UPDATE audit_chain SET montant = montant + 1 WHERE id = ?", (premier,))
#     conn.commit()
#     falsifie = M.verifier_chaine_audit()
#     M.init_schema_v7()
# verifier("Falsification détectée par la vérification", falsifie["ok"] is False,
#          "rupture ligne %s — %s" % (falsifie.get("rupture_ligne"), falsifie.get("cause")))
# session_pour(ADMIN)
# r = CLIENT.get("/admin/audit/verifier")
# verifier("Route de vérification JSON accessible à l'admin (200)", r.status_code == 200, r.status_code)
#
# print("\n[5] Alertes SMS / WhatsApp à l'administrateur")
# with M.app.app_context():
#     etat_alertes = M._alerte_statut()
# verifier("Aucun numéro inventé : destinataire vide tant qu'il n'est pas configuré",
#          etat_alertes["telephone"] == "", etat_alertes["telephone"])
# with M.app.app_context():
#     essai = M.alerter_admin("test", "Alerte de test sans fournisseur configuré.", gravite="test")
# verifier("Sans clés : alerte consignée, jamais présentée comme envoyée",
#          essai["envoye"] is False and essai["statut"] == "non_configure", essai["statut"])
# verifier("Alerte tracée dans le journal des alertes", compter("alertes_journal") >= 1)
# session_pour(ADMIN)
# r = CLIENT.post("/admin/alertes/reglages", data={"csrf": "test", "telephone": "+229 61 23 45 67",
#                                                  "seuil": "50000", "sms": "1", "whatsapp": "1"})
# with M.app.app_context():
#     etat2 = M._alerte_statut()
# verifier("Numéro d'alerte enregistré et normalisé", r.status_code == 302 and etat2["telephone"].startswith("+229"),
#          etat2["telephone"])
# verifier("Seuil d'alerte enregistré", etat2["seuil"] == 50000.0, etat2["seuil"])
# with M.app.app_context():
#     essai2 = M.alerter_admin("test", "Test canal SMS.", gravite="test")
#     canaux = {r["canal"]: r["statut"] for r in essai2["resultats"]}
# verifier("Canal SMS en repli explicite sans clés Twilio (jamais simulé)",
#          canaux.get("sms") == "non_configure", canaux)
# r = CLIENT.get("/admin/alertes")
# verifier("Page Alertes accessible (200)", r.status_code == 200, r.status_code)
# with M.app.app_context():
#     M.creer_abonnement(TIERS, KOFI, 5000)
#     ligne = M.db().execute("SELECT id FROM abonnements ORDER BY id DESC LIMIT 1").fetchone()["id"]
#     res = M.valider_abonnement(ligne, ADMIN)
#     fonds = M.solde_fonds_garantie()
# verifier("Alerte de seuil déclenchée par une validation (fonds alimenté par la même occasion)",
#          fonds > 0, fonds)
#
# print("\n[6] Résumé quotidien automatique du fil (IA)")
# with M.app.app_context():
#     resume = M.generer_resume_du_jour(force=True)
# verifier("Résumé du jour généré à partir des textes réels",
#          bool(resume["texte"]) and resume["nb_posts"] >= 1, resume["nb_posts"])
# verifier("Sans clé d'IA : origine « regles » assumée (aucune invention)",
#          resume["origine"] in ("regles", "ia"), resume["origine"])
# session_pour(AMINA)
# r = CLIENT.post("/resume-quotidien/preference", data={"csrf": "test", "actif": "1"})
# verifier("Abonnement au résumé quotidien enregistré", r.status_code == 302, r.status_code)
# with M.app.app_context():
#     avant_msgs = compter("messages")
#     envoi = M.envoyer_resume_aux_abonnes()
#     apres_msgs = compter("messages")
# verifier("Résumé envoyé par message aux abonnés volontaires",
#          envoi["envoyes"] >= 1 and apres_msgs > avant_msgs, "envois %s" % envoi["envoyes"])
# session_pour(KOFI)
# r = CLIENT.get("/resume-quotidien")
# verifier("Page Résumé quotidien accessible (200)", r.status_code == 200, r.status_code)
# session_pour(ADMIN)
# r = CLIENT.post("/admin/resume/declencher", data={"csrf": "test"})
# verifier("Déclenchement manuel du résumé par l'administrateur (302)", r.status_code == 302, r.status_code)
#
# print("\n[7] Détection de spam / multi-comptes")
# session_pour(KOFI)
# for i in range(26):
#     CLIENT.post("/publier", data={"csrf": "test", "corps": "Texte repetitif numero %d sur le fil." % (i % 3)})
# with M.app.app_context():
#     spam = M.detecter_spam(KOFI)
# verifier("Score de spam calculé (0-100)", 0 <= spam["score"] <= 100, spam["score"])
# verifier("Cadence anormale détectée avec signaux explicites", spam["score"] > 0 and bool(spam["signaux"]),
#          spam["signaux"][:1])
# verifier("Doublons de texte comptés comme signal",
#          any("identiques" in s for s in spam["signaux"]) or spam["score"] > 0, spam["signaux"])
# session_pour(KOFI)
# CLIENT.get("/")
# session_pour(TIERS)
# CLIENT.get("/")
# with M.app.app_context():
#     lie = M.detecter_spam(TIERS)
# verifier("Empreinte partagée (IP + agent) détectée entre deux comptes",
#          lie["comptes_lies"] >= 1, "comptes liés : %s" % lie["comptes_lies"])
# with M.app.app_context():
#     _empreinte = M.db().execute("SELECT tel_hash FROM empreintes_connexion LIMIT 1").fetchone()
# verifier("Téléphones et IP stockés hachés (aucune valeur en clair)",
#          _empreinte is not None and len(_empreinte["tel_hash"]) == 64)
# session_pour(ADMIN)
# r = CLIENT.get("/admin/anti-spam")
# verifier("Tableau anti-spam accessible à l'administrateur (200)", r.status_code == 200, r.status_code)
# r = CLIENT.post("/admin/anti-spam/balayer", data={"csrf": "test"})
# verifier("Balayage anti-spam exécutable (302)", r.status_code == 302, r.status_code)
#
# print("\n[8] Chat IA multilingue (français, fon, baoulé)")
# session_pour(KOFI)
# r = CLIENT.get("/chat-multilingue")
# verifier("Page du chat multilingue accessible (200)", r.status_code == 200, r.status_code)
# with M.app.app_context():
#     fr = M.reponse_multilingue("Quelles sont les dernières actualités du Bénin ?", "fr")
# verifier("Réponse française construite sur des sources réelles (liste fournie)",
#          isinstance(fr["resultats"], list) and fr["reponse"] != "", fr["source"])
# fon = M.reponse_multilingue("Quelles sont les dernières actualités ?", "fon")
# baoule = M.reponse_multilingue("Donne-moi des informations.", "baoule")
# verifier("Fon : formulation par lexique interne, sources réelles affichées telles quelles",
#          fon["source"] == "lexique_local" and "LIMITÉE" in fon["note"], fon["source"])
# verifier("Fon : l'absence de résultat est annoncée honnêtement",
#          "aucun résultat" in fon["note"].lower() or bool(fon["resultats"]) or "ɖé ǎ" in fon["reponse"]
#          or fon["reponse"] != "")
# verifier("Baoulé : couverture limitée reconnue explicitement", "LIMITÉE" in baoule["note"])
# verifier("Aucune invention : la réponse locale cite la source d'origine",
#          baoule["source"] in ("lexique_local", "regles", "ia"))
# r = CLIENT.post("/api/chat-multilingue", json={"question": "Infos sur le coton au Bénin", "langue": "fr"})
# verifier("API de chat multilingue opérationnelle (200)", r.status_code == 200, r.status_code)
#
# print("\n[9] Rapport comptable narratif (IA)")
# with M.app.app_context():
#     mois = M._dt.datetime.now(M._dt.timezone.utc).strftime("%Y-%m")
#     rapport = M.generer_rapport_narratif(mois)
#     donnees = M.donnees_mois_v7(mois)
# verifier("Rapport narratif produit", bool(rapport["texte"]), rapport["origine"])
# verifier("Chiffres du rapport conformes au grand livre",
#          ("%g" % donnees["brut"]) in rapport["texte"] or donnees["brut"] == 0,
#          "brut %s" % donnees["brut"])
# verifier("Net réglementaire calculé (cumul 25 % + 15 FCFA/transaction)",
#          abs(donnees["net_regle"]["net"] - max(0.0, donnees["brut"] - 0.25 * donnees["brut"]
#                                                - 15 * donnees["transactions"])) < 0.01,
#          donnees["net_regle"]["net"])
# session_pour(ADMIN)
# r = CLIENT.get("/admin/rapport-narratif")
# verifier("Page Rapport narratif accessible au comptable (200)", r.status_code == 200, r.status_code)
#
# print("\n[10] Mode hors-ligne avancé")
# r = CLIENT.get("/api/hors-ligne/flux")
# donnees_hl = json.loads(r.data.decode("utf-8"))
# verifier("Flux hors-ligne exposé (API 200)",
#          r.status_code == 200 and donnees_hl["ok"] is True, r.status_code)
# verifier("Cache plafonné aux 50 dernières publications",
#          donnees_hl["limite"] == 50 and len(donnees_hl["publications"]) <= 50,
#          len(donnees_hl["publications"]))
# verifier("Le cache ne contient que du texte (pseudo, corps, dates)",
#          all(set(p.keys()) == {"id", "pseudo", "corps", "cree_le"} for p in donnees_hl["publications"]))
# r = CLIENT.post("/api/hors-ligne/brouillon", json={"corps": "Texte rédigé sans réseau."})
# verifier("Brouillon hors-ligne enregistré (200)", r.status_code == 200, r.status_code)
# r = CLIENT.post("/api/hors-ligne/brouillon", json={"corps": "regarde https://x.tld/img.png"})
# verifier("Le garde-fou texte s'applique aussi hors-ligne (400)", r.status_code == 400, r.status_code)
# session_pour(KOFI)
# avant_hl = posts_de(KOFI)
# r = CLIENT.post("/api/hors-ligne/publier", json={"posts": [{"corps": "Publication différée numéro un."},
#                                                            {"corps": "Publication différée numéro deux."}]})
# resultat_hl = json.loads(r.data.decode("utf-8"))
# verifier("File d'attente publiée après reconnexion (2 textes)",
#          resultat_hl["publies"] == 2 and posts_de(KOFI) == avant_hl + 2, resultat_hl["publies"])
# r = CLIENT.post("/api/hors-ligne/publier", json={"posts": [{"corps": "regarde https://x.tld/a.jpg"}]})
# verifier("Un média glissé dans la file fait refuser la requête (garde-fou serveur, 400)",
#          r.status_code == 400 and posts_de(KOFI) == avant_hl + 2, r.status_code)
# verifier("Service worker V7 enrichi (cache texte + hors-ligne)",
#          "toutbot-v7-texte" in M.SW_JS and "api/hors-ligne/flux" in M.SW_JS)
# r = CLIENT.get("/hors-ligne")
# verifier("Page Hors-ligne accessible (200)", r.status_code == 200, r.status_code)
#
# print("\n[11] Notifications push (PWA)")
# r = CLIENT.get("/api/push/cles")
# cles = json.loads(r.data.decode("utf-8"))
# verifier("Route des clés push opérationnelle", r.status_code == 200 and cles["ok"] is True)
# verifier("Sans clés VAPID : état annoncé comme non disponible (aucun faux succès)",
#          cles["disponible"] is False, cles["note"][:60])
# r = CLIENT.post("/api/push/abonner", json={"endpoint": "https://push.exemple.tld/abc123",
#                                            "keys": {"p256dh": "x", "auth": "y"}})
# verifier("Abonnement push enregistré (200)", r.status_code == 200, r.status_code)
# verifier("Abonnement présent en base", compter("push_abonnements") == 1)
# with M.app.app_context():
#     envoi_push = M.envoyer_push(KOFI, "message", "Vous avez un nouveau message.", "/messages")
# verifier("Envoi push en repli explicite sans pywebpush/clés VAPID",
#          envoi_push["statut"] in ("repli_notification_integree", "aucun_abonnement"), envoi_push["statut"])
# r = CLIENT.post("/api/push/desabonner", json={"endpoint": "https://push.exemple.tld/abc123"})
# verifier("Désabonnement push accepté (200)", r.status_code == 200, r.status_code)
# r = CLIENT.get("/push")
# verifier("Page Notifications accessible (200)", r.status_code == 200, r.status_code)
#
# print("\n[12] Lecteur vocal (TTS) — accessibilité, 100 % côté client")
# session_pour(KOFI)
# r = CLIENT.post("/parametres/accessibilite", data={"csrf": "test", "tts_actif": "1",
#                                                    "tts_langue": "fr-FR", "tts_vitesse": "1.3"})
# with M.app.app_context():
#     acc = M.accessibilite_de(KOFI)
#     _ = acc
# verifier("Réglages TTS enregistrés", r.status_code == 302 and acc["tts_actif"] == 1 and
#          abs(acc["tts_vitesse"] - 1.3) < 0.01, acc)
# autorisees_texte = {"/admin/suivi/import"}
# verifier("Aucune route de téléversement de fichier, hormis l'outil Excel de l'administrateur",
#          not [c for c in routes if ("audio" in c or "media" in c or "upload" in c
#                                     or "televers" in c) and c not in autorisees_texte])
# verifier("Le lecteur vocal est bien injecté côté client (aucun audio serveur)",
#          "speechSynthesis" in M.TEMPLATES["base.html"] and "SpeechSynthesisUtterance" in M.TEMPLATES["base.html"])
# verifier("Bouton d'écoute présent dans le fil", 'data-tts="1"' in M.TEMPLATES["fil.html"])
#
# print("\n[13] Thèmes de profil par créateur (couleurs CSS uniquement)")
# with M.app.app_context():
#     refus_theme = CLIENT.post("/parametres/theme-createur",
#                               data={"csrf": "test", "accent": "url(http://x.tld/i.png)",
#                                     "fond": "#10141c", "texte": "#e8e8e8"}).status_code
# verifier("Thème non chromatique refusé (400)", refus_theme == 400, refus_theme)
# r = CLIENT.post("/parametres/theme-createur", data={"csrf": "test", "accent": "#ff6600",
#                                                     "fond": "#10141c", "texte": "#eeeeee"})
# with M.app.app_context():
#     theme = M.theme_createur_de(KOFI)
#     _ = theme
# verifier("Thème accepté et enregistré (couleurs hexadécimales)",
#          r.status_code == 302 and theme["accent"] == "#ff6600", theme)
# verifier("Aucune image dans le thème (uniquement des codes couleur)",
#          all(str(v).startswith("#") or v == "" for v in (theme["accent"], theme["fond"], theme["texte"])))
#
# print("\n[14] Badges / achievements")
# with M.app.app_context():
#     nouveaux = M.attribuer_badges(KOFI)
#     mes_badges = M.badges_de(KOFI)
#     _ = nouveaux
# verifier("Badge « Premier pas » attribué par un compteur réel",
#          any(b["code"] == "premier_pas" for b in mes_badges), [b["code"] for b in mes_badges])
# verifier("Badges justifiés et datés", all(b["obtenu_le"] for b in mes_badges))
# verifier("Badges = texte uniquement (aucun fichier image)",
#          all(isinstance(b["libelle"], str) for b in mes_badges))
# session_pour(KOFI)
# r = CLIENT.get("/badges")
# verifier("Page Badges accessible (200)", r.status_code == 200, r.status_code)
# r = CLIENT.post("/badges/recalculer", data={"csrf": "test"})
# verifier("Recalcul des badges exécutable (302)", r.status_code == 302, r.status_code)
#
# print("\n[15] Export RGPD complet + gel temporaire du compte")
# session_pour(KOFI)
# r = CLIENT.get("/rgpd/export")
# dossier = json.loads(r.data.decode("utf-8"))
# verifier("Export RGPD téléchargeable (200)", r.status_code == 200, r.status_code)
# verifier("Export complet : identité, publications, portefeuille, audit",
#          all(cle in dossier for cle in ("identite", "publications", "portefeuille", "ecritures_audit",
#                                         "messages_envoyes", "badges", "reputation")))
# verifier("Export conforme : absence de média explicitée",
#          "aucun média" in dossier["note"].lower(), dossier["note"][:80])
# r = CLIENT.post("/rgpd/geler", data={"csrf": "test", "duree": "24", "motif": "test de gel"})
# with M.app.app_context():
#     gele = M.gele_en_cours(M.utilisateur_par_id(KOFI))
# verifier("Compte gelé par le membre (302 + drapeau posé)", r.status_code == 302 and gele is True)
# avant_gel = posts_de(KOFI)
# r = CLIENT.post("/publier", data={"csrf": "test", "corps": "Ecriture pendant le gel."})
# verifier("Écriture refusée pendant le gel", posts_de(KOFI) == avant_gel, posts_de(KOFI))
# r = CLIENT.get("/")
# verifier("Lecture toujours possible pendant le gel (200)", r.status_code == 200, r.status_code)
# r = CLIENT.get("/rgpd/export")
# verifier("Export RGPD possible pendant le gel (droit d'accès préservé)", r.status_code == 200, r.status_code)
# r = CLIENT.post("/rgpd/degeler", data={"csrf": "test"})
# r = CLIENT.post("/publier", data={"csrf": "test", "corps": "Publication apres degel."})
# verifier("Publication de nouveau possible après dégel", posts_de(KOFI) == avant_gel + 1)
#
# print("\n[16] DAO légère : votes pondérés par les tickets")
# session_pour(ADMIN)
# r = CLIENT.post("/admin/dao/config", data={"csrf": "test", "actif": "1"})
# verifier("DAO activée par l'administrateur (302)", r.status_code == 302, r.status_code)
# session_pour(KOFI)
# r = CLIENT.post("/dao/proposer", data={"csrf": "test", "titre": "Ajouter un filtre par thème",
#                                        "description": "Proposition de test.", "type": "fonctionnalite"})
# with M.app.app_context():
#     proposition = M.db().execute("SELECT * FROM dao_propositions ORDER BY id DESC LIMIT 1").fetchone()
#     pid = proposition["id"]
#     poids_am = M.poids_vote_v7(AMINA)
# verifier("Proposition enregistrée", r.status_code == 302 and proposition is not None,
#          proposition["titre"] if proposition else None)
# verifier("Poids de vote = tickets payés, minimum 1", poids_am >= 1, poids_am)
# session_pour(AMINA)
# CLIENT.post("/dao/%d/voter" % pid, data={"csrf": "test", "sens": "oui"})
# with M.app.app_context():
#     res = M.resultats_v7(pid)
# verifier("Vote pondéré enregistré", res["oui"] >= 1 and res["participation"] == 1, res)
# session_pour(ADMIN)
# r = CLIENT.post("/admin/dao/%d/clore" % pid, data={"csrf": "test"})
# with M.app.app_context():
#     clos = M.db().execute("SELECT statut, resultat FROM dao_propositions WHERE id = ?", (pid,)).fetchone()
# verifier("Scrutin clos avec résumé chiffré", r.status_code == 302 and clos["statut"] == "clos",
#          clos["resultat"][:60])
# session_pour(KOFI)
# r = CLIENT.post("/dao/proposer", data={"csrf": "test", "titre": "Taux de commission à 15 %",
#                                        "description": "Scrutin sur le taux.", "type": "taux_commission",
#                                        "valeur": "15"})
# with M.app.app_context():
#     pid_taux = M.db().execute("SELECT id FROM dao_propositions ORDER BY id DESC LIMIT 1").fetchone()["id"]
# session_pour(ADMIN)
# CLIENT.post("/dao/%d/voter" % pid_taux, data={"csrf": "test", "sens": "oui"})
# r = CLIENT.post("/admin/dao/%d/clore" % pid_taux, data={"csrf": "test"})
# r = CLIENT.post("/admin/dao/%d/appliquer" % pid_taux, data={"csrf": "test"})
# with M.app.app_context():
#     taux = M.CONFIG["COMMISSION_PCT"]
# verifier("Résultat de DAO appliqué UNIQUEMENT sur décision explicite de l'administrateur",
#          r.status_code == 302 and abs(taux - 15.0) < 0.01, taux)
# verifier("Avertissement réglementaire BCEAO/UEMOA affiché",
#          "BCEAO" in M.AVERTISSEMENTS_V7 and "CIMA" in M.AVERTISSEMENTS_V7)
# session_pour(KOFI)
# r = CLIENT.get("/dao")
# verifier("Page DAO accessible aux membres (200)", r.status_code == 200, r.status_code)
# session_pour(ADMIN)
# r = CLIENT.get("/admin/dao")
# verifier("Page DAO d'administration accessible (200)", r.status_code == 200, r.status_code)
#
# print("\n[17] Fonds de garantie « non-paiement »")
# with M.app.app_context():
#     solde_fonds = M.solde_fonds_garantie()
#     _garantie_chain = compter("audit_chain", "type_ecriture = 'fonds_garantie'")
# verifier("Fonds alimenté automatiquement à chaque validation", solde_fonds > 0, solde_fonds)
# verifier("Prélèvement au taux configuré (1 % du net validé)",
#          abs(M.CONFIG_V7["GARANTIE_PCT"] - 1.0) < 0.001)
# verifier("Alimentation du fonds scellée dans la chaîne d'audit", _garantie_chain >= 1,
#          _garantie_chain)
# session_pour(ADMIN)
# r = CLIENT.post("/admin/fonds-garantie/decaisser", data={"csrf": "test", "montant": "99999999",
#                                                          "beneficiaire": str(KOFI), "motif": "test"})
# with M.app.app_context():
#     apres_refus = M.solde_fonds_garantie()
# verifier("Décaissement supérieur au solde REFUSÉ", abs(apres_refus - solde_fonds) < 0.01,
#          "solde %s" % apres_refus)
# r = CLIENT.post("/admin/fonds-garantie/decaisser", data={"csrf": "test", "montant": "10",
#                                                          "beneficiaire": str(KOFI),
#                                                          "motif": "non-paiement constaté (test)"})
# with M.app.app_context():
#     apres_ok = M.solde_fonds_garantie()
# verifier("Indemnisation valide débitée du fonds", apres_ok < solde_fonds, apres_ok)
# session_pour(KOFI)
# r = CLIENT.get("/fonds-garantie")
# verifier("Page Fonds de garantie accessible (200)", r.status_code == 200, r.status_code)
# session_pour(ADMIN)
# r = CLIENT.get("/admin/fonds-garantie")
# verifier("Page d'administration du fonds accessible (200)", r.status_code == 200, r.status_code)
#
# print("\n[18] Régressions du socle V4/V5/V6")
# session_pour(ADMIN)
# for chemin in ("/admin", "/admin/temps-reel", "/admin/gouvernance", "/admin/v5", "/admin/journal",
#                "/tarifs", "/groupes", "/statistiques", "/portefeuille", "/live"):
#     r = CLIENT.get(chemin)
#     verifier("Route historique %s intacte (200)" % chemin, r.status_code == 200, r.status_code)
# r = CLIENT.get("/admin/suivi.xlsx")
# verifier("Export Excel admin toujours autorisé (200)", r.status_code == 200, r.status_code)
# session_pour(TIERS)
# r = CLIENT.get("/admin/temps-reel")
# verifier("Tableau de bord toujours refusé à un membre (403)", r.status_code == 403, r.status_code)
# r = CLIENT.get("/admin/audit")
# verifier("Journal d'audit refusé à un simple membre (403)", r.status_code == 403, r.status_code)
#
# print("\n" + "=" * 78)
# reussis = sum(1 for _nom, ok in RESULTATS if ok)
# print("RÉSULTAT : %d/%d tests réussis" % (reussis, len(RESULTATS)))
# for nom, ok in RESULTATS:
#     if not ok:
#         print("  ÉCHEC : " + nom)
# print("=" * 78)
# sys.exit(0 if reussis == len(RESULTATS) else 1)

# -----------------------------------------------------------------------------
# FICHIER D'ORIGINE : smoke_v8.py
# -----------------------------------------------------------------------------
# # -*- coding: utf-8 -*-
# """Test fonctionnel V8 : cliquabilité (routes/pages), historique personnel isolé."""
# import os, pathlib, sys, tempfile
# BASE = pathlib.Path(tempfile.mkdtemp(prefix="v8_smoke_"))
# os.environ["TOUTBOT_DB"] = str(BASE / "smoke.db")
# os.environ["SECRET_KEY"] = "smoke-v8"
# sys.path.insert(0, "/home/user/toutbot")
# import TOUTBOT_MUNDO_V7 as M
#
# ok = []
# def v(n, c, d=""):
#     ok.append(c); print(("  OK   " if c else "  ECHEC") + " | " + n + (("  (%s)" % d) if d else ""))
#
# A = M.graine_admin("zeusad", "90000001", "motdepasse")
# with M.app.app_context():
#     K = M.creer_utilisateur("90000002", "kofiid", "motdepasse")
#     A2 = M.creer_utilisateur("90000003", "aminaa", "motdepasse")
# c = M.app.test_client()
# def sess(uid, csrf="t"):
#     with c.session_transaction() as s:
#         s["uid"] = uid; s["csrf"] = csrf
#
# # 1) Actions de kofiid (la vraie connexion HTTP est faite à la fin : elle régénère le jeton CSRF)
# sess(K)
# c.get("/messages/%d" % A2)
# c.post("/publier", data={"csrf": "t", "corps": "Bonjour Mundo"})
# c.post("/p/aime/1", data={"csrf": "t"})
# c.post("/p/commenter/1", data={"csrf": "t", "corps": "super"})
# c.post("/abonner/%d" % A2, data={"csrf": "t"})
# c.post("/p/abonnement/%d" % A2, data={"csrf": "t", "palier": "500"})
# c.post("/p/pourboire/%d" % A2, data={"csrf": "t", "montant": "100", "mot": "bravo"})
# c.get("/u/aminaa")
# c.get("/portefeuille")
# c.get("/recherche?q=test")
# c.get("/mon-historique")
# c.post("/messages/%d" % A2, data={"csrf": "t", "corps": "salut"})
# c.post("/deconnexion", data={"csrf": "t"})
# # Vraie connexion HTTP en fin de parcours (jeton csrf "t" posé avant)
# sess(K)
# c.post("/connexion", data={"csrf": "t", "identifiant": "kofiid", "mot_de_passe": "motdepasse"})
#
# CTX = M.app.app_context(); CTX.push()
# with M.app.app_context():
#     nK = M.db().execute("SELECT COUNT(*) n FROM historique_utilisateur WHERE user_id=?", (K,)).fetchone()["n"]
#     nA = M.db().execute("SELECT COUNT(*) n FROM historique_utilisateur WHERE user_id=?", (A,)).fetchone()["n"]
# v("Actions de kofiid consignées dans SON historique", nK >= 10, "n=%d" % nK)
# v("Aucune fuite chez l'administrateur (isolation)", nA == 0, "n=%d" % nA)
# acts = [r["action"] for r in M.db().execute("SELECT DISTINCT action FROM historique_utilisateur WHERE user_id=?", (K,))]
# for a in ("publication", "j_aime", "commentaire", "suivre", "abonnement", "pourboire",
#           "profil_vu", "portefeuille", "recherche", "message_prive",
#           "discussion_ouverte", "deconnexion", "connexion"):
#     v("activité « %s » enregistrée" % a, a in acts)
#
# # 2) Pages V8
# sess(K)
# r = c.get("/mon-historique"); v("page /mon-historique (200)", r.status_code == 200, r.status_code)
# v("page contient les tuiles cliquables et le filtre", b"Mon historique personnel" in r.data and b"tuile" in r.data)
# r = c.get("/mon-historique.csv"); v("export CSV (200)", r.status_code == 200, r.status_code)
# r = c.get("/api/mon-historique"); v("API JSON (200)", r.status_code == 200, r.status_code)
# r = c.get("/mon-historique?action=publication"); v("filtre par action (200)", r.status_code == 200 and b"filtre" in r.data)
# r = c.get("/mon-historique", query_string={"q": "Mundo"}); v("recherche dans l'historique (200)", r.status_code == 200)
# # purge
# r = c.post("/mon-historique/vider", data={"csrf": "t"})
# with M.app.app_context():
#     nK2 = M.db().execute("SELECT COUNT(*) n FROM historique_utilisateur WHERE user_id=?", (K,)).fetchone()["n"]
# reste = [r["action"] for r in M.db().execute(
#     "SELECT action FROM historique_utilisateur WHERE user_id=?", (K,)).fetchall()]
# v("purge de SON historique (seul l'effacement reste consigné)",
#   nK2 == 1 and reste == ["parametres"], "n=%d %s" % (nK2, reste))
#
# # 3) Isolation stricte : amina ne voit rien de kofiid
# sess(A2)
# r = c.get("/mon-historique")
# v("amina : page 200", r.status_code == 200)
# with M.app.app_context():
#     nA2 = M.db().execute("SELECT COUNT(*) n FROM historique_utilisateur WHERE user_id=?", (A2,)).fetchone()["n"]
# v("l'historique de l'autre compte reste vide (isolation totale)", nA2 == 0, "n=%d" % nA2)
#
# # 4) Anonyme refusé
# cc = M.app.test_client()
# r = cc.get("/mon-historique", follow_redirects=False)
# v("anonyme redirigé vers connexion", r.status_code == 302, r.status_code)
#
# # 5) Cliquabilité : le gabarit de base contient les nouveaux mécanismes
# sess(K)
# r = c.get("/")
# for motif, nom in ((b"ligne-histo", "lignes historique cliquables"),
#                    (b"tbm-onde", "ondes tactiles"),
#                    (b"tabindex", "accessibilité clavier"),
#                    (b"prefers-reduced-motion", "respect prefers-reduced-motion"),
#                    ("Aucun média".encode("utf-8"), "garde-fou anti-média navigateur"),
#                    (b"mon-historique", "lien nav historique")):
#     v("interface : %s" % nom, motif in r.data)
# print("=" * 60)
# print("SMOKE V8 : %d/%d" % (sum(1 for x in ok if x), len(ok)))
# sys.exit(0 if all(ok) else 1)

# -----------------------------------------------------------------------------
# FICHIER D'ORIGINE : test_monetisation_v8.py
# -----------------------------------------------------------------------------
# # -*- coding: utf-8 -*-
# """Tests du moteur de monétisation V8 — TOUTBOT MUNDO.
#
# Couvre : les 6 paliers de reversement, le barème, la répartition 75/25,
# le seuil de reversement (dessous / exactement au seuil), l'anti-fraude
# (doublon, lecture trop courte, vélocité, ferme de clics), la grille des
# frais opérateur, le plafonnement par les recettes réelles, le plafond
# mensuel global, et le cycle demande -> approbation -> versement
# (idempotence et anti-double paiement inclus).
#
# Lancement : python test_monetisation_v8.py
# """
# import os
# import pathlib
# import sqlite3
# import sys
# import tempfile
# from decimal import Decimal
#
# BASE = pathlib.Path(tempfile.mkdtemp(prefix="toutbot_v8_mon_"))
# os.environ["TOUTBOT_DB"] = str(BASE / "mon_v8.db")
# os.environ["MODE_POOL"] = "garanti"
#
# sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# import monetisation_v8 as MON  # noqa: E402
#
# # Le moteur V8 se greffe sur le schéma V7 : le test fournit donc le socle « users ».
# with sqlite3.connect(os.environ["TOUTBOT_DB"]) as _c:
#     _c.execute(
#         "CREATE TABLE IF NOT EXISTS users ("
#         " id INTEGER PRIMARY KEY AUTOINCREMENT, telephone TEXT UNIQUE,"
#         " pseudo TEXT UNIQUE NOT NULL, mot_de_passe TEXT NOT NULL,"
#         " est_admin INTEGER NOT NULL DEFAULT 0, bloque INTEGER NOT NULL DEFAULT 0,"
#         " payout_phone TEXT NOT NULL DEFAULT '', payout_op TEXT NOT NULL DEFAULT 'MTN',"
#         " bio TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL)")
# MON.init_monetisation()
#
# RESULTATS = []
#
#
# def verifier(nom, condition, detail=""):
#     RESULTATS.append((nom, bool(condition)))
#     print(("  OK   " if condition else "  ECHEC") + " | " + nom +
#           (("  (%s)" % detail) if detail else ""))
#     return bool(condition)
#
#
# def _conn():
#     c = sqlite3.connect(os.environ["TOUTBOT_DB"])
#     c.row_factory = sqlite3.Row
#     return c
#
#
# def _utilisateur(pseudo, telephone):
#     with _conn() as c:
#         cur = c.execute("INSERT INTO users (telephone, pseudo, mot_de_passe, est_admin,"
#                         " payout_phone, cree_le) VALUES (?,?,?,0,?,?)",
#                         (telephone, pseudo, "x" * 12, telephone, MON.maintenant()))
#         return int(cur.lastrowid)
#
#
# def _kyc_verifie(user_id, numero, operateur):
#     MON.verifier_numero(user_id, numero, operateur)
#     with _conn() as c:
#         c.execute("UPDATE kyc_numeros SET statut='verifie', verifie_le=? WHERE user_id=?",
#                   (MON.maintenant(), user_id))
#
#
# def _gain_force(createur_id, mois, part_fcfa):
#     """Insère un gain mensuel directement (pour tester seuils et plafonds)."""
#     with _conn() as c:
#         c.execute("INSERT INTO gains_createurs (createur_id, mois, vues_pond_x100, part_centimes,"
#                   " palier, statut, maj_le) VALUES (?,?,?,?,1,'retenu',?)",
#                   (createur_id, mois, int(monetisation_vues(part_fcfa)),
#                    MON.fcfa_vers_centimes(part_fcfa), MON.maintenant()))
#         c.commit()
#
#
# def monetisation_vues(part_fcfa):
#     return int(Decimal(part_fcfa) * 1000 / Decimal(12500) * 100)
#
#
# def _inserer_vues(createur_id, nombre, prefixe, ip_base="10.9.0."):
#     """Charge en masse des vues valides (contourne la vélocité, qui est testée à part)."""
#     with _conn() as c:
#         c.executemany(
#             "INSERT INTO vues_publications (post_id, createur_id, visiteur_id, empreinte,"
#             " categorie, poids_x100, duree_ms, adresse_ip, valide, motif_rejet, jour, cree_le)"
#             " VALUES (?,?,?,?,?,?,?,?,1,'',?,?)",
#             [(90000 + i, createur_id, createur_id, "%s-%d" % (prefixe, i), "abonne", 100, 6000,
#               ip_base + str(i % 250), MON._jour(), MON.maintenant())
#              for i in range(nombre)])
#
#
# print("=" * 104)
# print("1) TABLE DES 6 PALIERS DE REVERSEMENT (générée par le code, jamais recopiée)")
# print("=" * 104)
# print(MON._texte_tableau())
# print()
#
# PALIERS = MON.tableau_paliers_reversement()
# verifier("6 paliers de reversement déclarés", len(PALIERS) == 6, "n=%d" % len(PALIERS))
# verifier("paliers numérotés de 1 à 6", [p["niveau"] for p in PALIERS] == [1, 2, 3, 4, 5, 6])
# verifier("délais décroissants (J+30 jusqu'à J+3)",
#          [p["delai_jours"] for p in PALIERS] == sorted(
#              [p["delai_jours"] for p in PALIERS], reverse=True),
#          str([p["delai_jours"] for p in PALIERS]))
# verifier("tranches de vues sans recouvrement",
#          all(PALIERS[i]["vues_max"] < PALIERS[i + 1]["vues_min"] for i in range(5)))
# verifier("6e palier sans plafond (barème jusqu'à l'infini)", PALIERS[5]["vues_max"] is None)
# _SOURCE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
#                             "monetisation_v8.py"), encoding="utf-8").read()
# verifier("le barème n'est écrit qu'UNE fois dans le moteur (constante unique)",
#          _SOURCE.count("fcfa_vers_centimes(50000)") == 1,
#          "occurrences=%d" % _SOURCE.count("fcfa_vers_centimes(50000)"))
#
# for p in PALIERS:
#     attendu_pool = MON.fcfa_vers_centimes(50000) * p["vues_reference"] // 1000
#     attendu_crea = MON.fcfa_vers_centimes(12500) * p["vues_reference"] // 1000
#     verifier("palier %d : pool = 50 000 FCFA x %d / 1000" % (p["niveau"], p["vues_reference"]),
#              p["pool_centimes"] == attendu_pool,
#              "%s vs %s" % (MON.formater(p["pool_centimes"]), MON.formater(attendu_pool)))
#     verifier("palier %d : part créateur = 25 %% (%s)" % (p["niveau"], MON.formater(attendu_crea)),
#              p["part_createurs_centimes"] == attendu_crea)
#     verifier("palier %d : app 75 %% + créateur 25 %% = pool (reste d'arrondi inclus)" % p["niveau"],
#              p["part_application_centimes"] + p["part_createurs_centimes"] == p["pool_centimes"])
#     verifier("palier %d : frais opérateur à la charge de l'app, net = 25 %%" % p["niveau"],
#              p["net_verse_centimes"] == p["part_createurs_centimes"])
#
# print()
# print("=" * 104)
# print("2) BARÈME LINÉAIRE ET RÉPARTITION 75 / 25")
# print("=" * 104)
# for vues in (1000, 2000, 3000, 5000, 10000, 100000, 1000000):
#     fig = MON.figures_pour_vues(Decimal(vues), mode="garanti")
#     print("  %9d vues | pool %20s | app %20s | créateur %18s" % (
#         vues, MON.formater(fig["pool"]), MON.formater(fig["part_application"]),
#         MON.formater(fig["part_createurs"])))
#     verifier("barème %d vues = 50 000 FCFA x %d / 1000" % (vues, vues),
#              fig["pool"] == MON.fcfa_vers_centimes(50000) * vues // 1000)
# verifier("progression linéaire : 2 000 vues = 2 x 1 000 vues",
#          MON.figures_pour_vues(Decimal(2000), mode="garanti")["pool"] ==
#          2 * MON.figures_pour_vues(Decimal(1000), mode="garanti")["pool"])
# verifier("sans plafond : 1 000 000 de vues atteignent 50 000 000 FCFA",
#          MON.figures_pour_vues(Decimal(1000000), mode="garanti")["pool"] ==
#          MON.fcfa_vers_centimes(50000000),
#          MON.formater(MON.figures_pour_vues(Decimal(1000000), mode="garanti")["pool"]))
# verifier("aucun plafond : 5 000 000 de vues paient 250 000 000 FCFA",
#          MON.figures_pour_vues(Decimal(5000000), mode="garanti")["pool"] ==
#          MON.fcfa_vers_centimes(250000000),
#          MON.formater(MON.figures_pour_vues(Decimal(5000000), mode="garanti")["pool"]))
# verifier("1 000 vues -> 37 500 FCFA à l'application",
#          MON.figures_pour_vues(Decimal(1000), mode="garanti")["part_application"] ==
#          MON.fcfa_vers_centimes(37500))
# verifier("1 000 vues -> 12 500 FCFA au créateur",
#          MON.figures_pour_vues(Decimal(1000), mode="garanti")["part_createurs"] ==
#          MON.fcfa_vers_centimes(12500))
#
# print()
# print("=" * 104)
# print("3) CLASSEMENT PAR PALIER SELON LES VUES (bornes testées)")
# print("=" * 104)
# bornes = [(999, 1), (1000, 1), (1999, 1), (2000, 2), (2999, 2), (3000, 3), (4999, 3),
#           (5000, 4), (9999, 4), (10000, 5), (99999, 5), (100000, 6), (5000000, 6)]
# for vues, attendu in bornes:
#     obtenu = MON.palier_pour_vues(Decimal(vues))["niveau"]
#     verifier("%d vues -> palier %d" % (vues, attendu), obtenu == attendu, "obtenu=%d" % obtenu)
#
# print()
# print("=" * 104)
# print("4) COEFFICIENTS DE PONDÉRATION DES VUES")
# print("=" * 104)
# for cat, coef in MON.COEFFICIENTS_VUES.items():
#     print("  %-14s coef %s -> poids_x100 %d" % (cat, coef, MON._poids_x100(cat)))
# verifier("vue d'abonné = 1,0", MON._poids_x100("abonne") == 100)
# verifier("vue d'audience = 0,6", MON._poids_x100("audience") == 60)
# verifier("vue publicitaire = 1,5", MON._poids_x100("publicitaire") == 150)
# verifier("vue de live écrite = 1,0", MON._poids_x100("live") == 100)
# verifier("vue de profil non monétisable", MON._poids_x100("profil") == 0)
# verifier("publicitaire > abonné > audience", MON._poids_x100("publicitaire")
#          > MON._poids_x100("abonne") > MON._poids_x100("audience"))
#
# print()
# print("=" * 104)
# print("5) GRILLE DES FRAIS OPÉRATEUR (relevé CinetPay Mass Payout)")
# print("=" * 104)
# for (pays, op), pct in sorted(MON.GRILLE_FRAIS_PCT.items()):
#     print("  %-16s %-8s %s %%" % (pays, op, pct))
# verifier("Bénin MTN = 1,0 % (le moins cher de la grille)",
#          MON.GRILLE_FRAIS_PCT[("Bénin", "MTN")] == Decimal("1.0"))
# verifier("couple pays/opérateur inconnu -> repli prudent 2 %",
#          MON.frais_operateur(1000000, "Pays imaginaire", "Opérateur X")["pct"] == Decimal("2.0"))
# verifier("frais sur 12 500 FCFA au Bénin MTN = 125 FCFA",
#          MON.frais_operateur(MON.fcfa_vers_centimes(12500), "Bénin", "MTN")["frais_centimes"]
#          == MON.fcfa_vers_centimes(125))
# verifier("frais sur 1 250 000 FCFA en Wave Sénégal = 25 000 FCFA",
#          MON.frais_operateur(MON.fcfa_vers_centimes(1250000), "Sénégal", "Wave")["frais_centimes"]
#          == MON.fcfa_vers_centimes(25000))
# verifier("frais opérateur prélevés sur les 75 % de l'application, jamais sur le créateur",
#          MON.FRAIS_A_CHARGE_DE == "application")
#
# print()
# print("=" * 104)
# print("6) ANTI-FRAUDE SUR LE COMPTAGE DES VUES")
# print("=" * 104)
# MON.init_monetisation()
# K = _utilisateur("koffia", "90000021")
# V = _utilisateur("visiteu", "90000022")
#
# r1 = MON.enregistrer_vue(1, K, "abonne", "emp-A", V, 6000, "10.0.0.1")
# verifier("1re vue acceptée", r1["acceptee"], r1["motif"])
# r2 = MON.enregistrer_vue(1, K, "abonne", "emp-A", V, 6000, "10.0.0.1")
# verifier("2e vue même visiteur/publication/jour REJETÉE", not r2["acceptee"], r2["motif"])
# r3 = MON.enregistrer_vue(2, K, "abonne", "emp-A", V, 6000, "10.0.0.1")
# verifier("même visiteur sur une AUTRE publication acceptée", r3["acceptee"], r3["motif"])
# r4 = MON.enregistrer_vue(3, K, "abonne", "emp-B", V, 1500, "10.0.0.1")
# verifier("lecture < 3 s REJETÉE", not r4["acceptee"], r4["motif"])
# r5 = MON.enregistrer_vue(4, K, "profil", "emp-C", V, 6000, "10.0.0.1")
# verifier("catégorie non monétisable REJETÉE", not r5["acceptee"], r5["motif"])
# r6 = MON.enregistrer_vue(5, K, "abonne", "", V, 6000, "10.0.0.1")
# verifier("empreinte absente REJETÉE", not r6["acceptee"], r6["motif"])
#
# for i in range(200):
#     MON.enregistrer_vue(1000 + i, K, "abonne", "vel-%d" % i, V, 6000, "10.0.0.9")
# with _conn() as c:
#     rejet_vel = c.execute("SELECT COUNT(*) n FROM vues_publications WHERE motif_rejet='velocite'"
#                           ).fetchone()["n"]
# verifier("vélocité : au-delà de 120 vues/h, mise en quarantaine",
#          rejet_vel > 0, "rejetées=%d" % rejet_vel)
#
# for i in range(30):
#     v = _utilisateur("ferm%d" % i, "9001%05d" % i)
#     MON.enregistrer_vue(900 + i, K, "audience", "farm-%d" % i, v, 6000, "203.0.113.7")
# with _conn() as c:
#     rejet_ip = c.execute("SELECT COUNT(*) n FROM vues_publications"
#                          " WHERE motif_rejet='ferme_de_clics'").fetchone()["n"]
# verifier("ferme de clics (> 20 comptes distincts sur une IP en 1 h) détectée",
#          rejet_ip > 0, "rejetées=%d" % rejet_ip)
#
# n = MON.agreger_jour()
# with _conn() as c:
#     agr = c.execute("SELECT SUM(vues_brutes) b, SUM(ecartees) e, SUM(vues_pond_x100) p"
#                     " FROM vues_journalieres").fetchone()
# print("  agrégat : vues valides=%s | écartées=%s | poids_x100=%s"
#       % (agr["b"], agr["e"], agr["p"]))
# verifier("agrégation quotidienne exécutée", n >= 1, "lignes=%d" % n)
# verifier("vues valides et vues écartées comptabilisées séparément",
#          agr["b"] > 0 and agr["e"] > 0, "valides=%s ecartees=%s" % (agr["b"], agr["e"]))
# with _conn() as c:
#     attendu_poids = c.execute(
#         "SELECT COALESCE(SUM(poids_x100),0) s FROM vues_publications WHERE valide=1"
#     ).fetchone()["s"]
#     par_categorie = c.execute(
#         "SELECT categorie, COUNT(*) n, SUM(poids_x100) s FROM vues_publications"
#         " WHERE valide=1 GROUP BY categorie").fetchall()
# print("  valides par catégorie : " + " | ".join(
#     "%s=%s (poids %s)" % (l["categorie"], l["n"], l["s"]) for l in par_categorie))
# verifier("poids_x100 de l'agrégat = somme des poids des vues valides",
#          agr["p"] == attendu_poids, "%s vs %s" % (agr["p"], attendu_poids))
# verifier("chaque catégorie garde bien son coefficient dans l'agrégat",
#          all(int(l["s"]) == int(l["n"]) * MON._poids_x100(l["categorie"])
#              for l in par_categorie))
#
# print()
# print("=" * 104)
# print("7) PLAFONNEMENT PAR LES RECETTES RÉELLES (le modèle ne peut pas payer ce qu'il n'encaisse pas)")
# print("=" * 104)
# mois = MON.mois_precedent()
# MON.enregistrer_revenu(mois, "annonceur", MON.fcfa_vers_centimes(300000), "Contrat télécom du mois")
# recettes = MON.recettes_mois_centimes(mois)
# fig_plaf = MON.figures_pour_vues(Decimal(100000), recettes, "plafonne")
# fig_gar = MON.figures_pour_vues(Decimal(100000), recettes, "garanti")
# print("  100 000 vues | recettes réellement encaissées : %s" % MON.formater(recettes))
# print("  mode garanti  : pool %s (app %s | créateurs %s)" % (
#     MON.formater(fig_gar["pool"]), MON.formater(fig_gar["part_application"]),
#     MON.formater(fig_gar["part_createurs"])))
# print("  mode plafonne : pool %s (app %s | créateurs %s)" % (
#     MON.formater(fig_plaf["pool"]), MON.formater(fig_plaf["part_application"]),
#     MON.formater(fig_plaf["part_createurs"])))
# verifier("mode plafonné : pool = min(barème, recettes encaissées)",
#          fig_plaf["pool"] == MON.fcfa_vers_centimes(300000), MON.formater(fig_plaf["pool"]))
# verifier("mode plafonné : jamais plus que les recettes du mois",
#          fig_plaf["pool"] <= recettes)
# verifier("mode garanti : barème plein (5 000 000 FCFA) malgré recettes insuffisantes",
#          fig_gar["pool"] == MON.fcfa_vers_centimes(5000000), MON.formater(fig_gar["pool"]))
#
# print()
# print("=" * 104)
# print("8) CLÔTURE MENSUELLE ET PRORATA DES VUES")
# print("=" * 104)
# A = _utilisateur("aminaa", "90000023")
# B = _utilisateur("zeynab", "90000024")
# mois_test = MON._jour()[:7]
# # isolation : la clôture ne doit porter que sur les vues de ce scénario
# with _conn() as c:
#     c.execute("DELETE FROM vues_publications")
#     c.execute("DELETE FROM vues_journalieres")
#     c.execute("DELETE FROM gains_createurs WHERE mois=?", (mois_test,))
#     c.execute("DELETE FROM pool_monetisation WHERE mois=?", (mois_test,))
#     c.commit()
# _inserer_vues(A, 1000, "A")
# _inserer_vues(B, 500, "B")
# MON.agreger_jour()
# scelles = []
# res = MON.cloturer_mois(mois_test, mode="garanti",
#                         sceller=lambda *a: scelles.append(a))
# print("  clôture %s : %d créateurs | %s vues pondérées | pool %s" % (
#     res["mois"], res["createurs"], Decimal(res["vues_pond_x100"]) / 100,
#     MON.formater(res["pool"])))
# print("  part application %s | part créateurs %s" % (
#     MON.formater(res["part_application"]), MON.formater(res["part_createurs"])))
#
# with _conn() as c:
#     gains = c.execute("SELECT createur_id, vues_pond_x100, part_centimes, palier"
#                       " FROM gains_createurs WHERE mois=? ORDER BY createur_id",
#                       (mois_test,)).fetchall()
#     total_parts = sum(int(g["part_centimes"]) for g in gains)
# verifier("une ligne de gain par créateur", len(gains) == 2, "n=%d" % len(gains))
# verifier("le total versé aux créateurs ne dépasse JAMAIS les 25 % du pool",
#          total_parts <= res["part_createurs"],
#          "%s <= %s" % (MON.formater(total_parts), MON.formater(res["part_createurs"])))
# verifier("le total versé reste inférieur au pool global", total_parts < res["pool"])
# for g in gains:
#     print("  créateur #%d : %s vues pondérées -> %s (palier %d)" % (
#         int(g["createur_id"]), Decimal(int(g["vues_pond_x100"])) / 100,
#         MON.formater(int(g["part_centimes"])), g["palier"]))
# verifier("prorata respecté : A (2/3 des vues) touche 2 x B",
#          int([g for g in gains if g["createur_id"] == A][0]["part_centimes"]) ==
#          2 * int([g for g in gains if g["createur_id"] == B][0]["part_centimes"]))
# verifier("le pool du mois est scellé dans la chaîne d'audit",
#          len(scelles) == 1 and scelles[0][0] == "POOL_MOIS", str(scelles[:1]))
# verifier("gain laissé en rétention (cohérent avec l'annonce faite au créateur)",
#          all(int(g["part_centimes"]) >= 0 for g in gains))
#
# print()
# print("=" * 104)
# print("9) SEUIL DE REVERSEMENT — DESSOUS, EXACTEMENT AU SEUIL")
# print("=" * 104)
# print("  seuil = %s" % MON.formater(MON.SEUIL_REVERSEMENT_CENTIMES))
# C = _utilisateur("petitx", "90000025")
# D = _utilisateur("seuilx", "90000026")
# _gain_force(C, mois_test, 999)
# _gain_force(D, mois_test, 1000)
# _kyc_verifie(C, "90000025", "MTN")
# _kyc_verifie(D, "90000026", "MTN")
#
# refus = MON.demander_reversement(C, mois_test, "90000025", "MTN")
# print("  999 FCFA  -> ok=%s  motif=%s" % (refus["ok"], refus["motif"]))
# verifier("999 FCFA (sous le seuil) REFUSÉ et reporté tel quel",
#          (not refus["ok"]) and refus["motif"] == "sous_le_seuil" and
#          refus["report_centimes"] == MON.fcfa_vers_centimes(999))
#
# accept = MON.demander_reversement(D, mois_test, "90000026", "MTN")
# print("  1 000 FCFA -> ok=%s  statut=%s  référence=%s  net=%s" % (
#     accept["ok"], accept.get("statut"), accept.get("reference_interne"),
#     MON.formater(accept.get("net_centimes", 0))))
# verifier("1 000 FCFA (exactement au seuil) ACCEPTÉ", accept["ok"], accept["motif"])
# verifier("montant stocké en centimes entiers, aucune décimale perdue",
#          isinstance(accept["montant_centimes"], int) and accept["montant_centimes"] == 100000)
#
# encore = MON.demander_reversement(D, mois_test, "90000026", "MTN")
# verifier("demande rejouée = idempotente (même référence, aucune seconde ligne)",
#          encore["ok"] and encore["motif"] == "deja_demandee" and
#          encore["reference_interne"] == accept["reference_interne"])
#
# E = _utilisateur("sanskyd", "90000027")
# _gain_force(E, mois_test, 62500)
# bloque = MON.demander_reversement(E, mois_test, "90000027", "MTN")
# verifier("numéro NON vérifié (KYC absent) -> versement BLOQUÉ",
#          (not bloque["ok"]) and bloque["motif"] == "numero_non_verifie")
#
# print()
# print("=" * 104)
# print("10) PLAFOND MENSUEL GLOBAL (logiciel de sécurité, cumul du mois)")
# print("=" * 104)
# sauve_plafond = MON.PLAFOND_MENSUEL_GLOBAL_CENTIMES
# MON.PLAFOND_MENSUEL_GLOBAL_CENTIMES = MON.fcfa_vers_centimes(10000)
# mois_bis = "2026-01"
# F = _utilisateur("grosxx", "90000031")
# G_ = _utilisateur("moyenx", "90000032")
# H = _utilisateur("petitb", "90000033")
# for u, montant, numero in ((F, 50000, "90000031"), (G_, 5000, "90000032"), (H, 6000, "90000033")):
#     _gain_force(u, mois_bis, montant)
#     _kyc_verifie(u, numero, "Moov")
# print("  plafond mensuel global (test) = %s" % MON.formater(MON.PLAFOND_MENSUEL_GLOBAL_CENTIMES))
# trop = MON.demander_reversement(F, mois_bis, "90000031", "Moov")
# print("  50 000 FCFA -> ok=%s motif=%s" % (trop["ok"], trop["motif"]))
# verifier("un gain supérieur au plafond mensuel est REFUSÉ",
#          (not trop["ok"]) and trop["motif"] == "plafond_mensuel_atteint")
# ok1 = MON.demander_reversement(G_, mois_bis, "90000032", "Moov")
# verifier("5 000 FCFA accepté tant que le plafond n'est pas atteint", ok1["ok"], ok1["motif"])
# cumul = MON._plafond_mois_utilise(mois_bis)
# verifier("le cumul du mois est bien suivi", cumul == MON.fcfa_vers_centimes(5000),
#          MON.formater(cumul))
# ok2 = MON.demander_reversement(H, mois_bis, "90000033", "Moov")
# verifier("5 000 + 6 000 = 11 000 > plafond 10 000 -> refusé",
#          (not ok2["ok"]) and ok2["motif"] == "plafond_mensuel_atteint", ok2["motif"])
# MON.PLAFOND_MENSUEL_GLOBAL_CENTIMES = sauve_plafond
#
# print()
# print("=" * 104)
# print("11) CYCLE DE VIE DU VERSEMENT : DEMANDE -> APPROBATION -> VERSÉ")
# print("=" * 104)
# with _conn() as c:
#     c.execute("DELETE FROM gains_createurs WHERE createur_id=? AND mois=?", (E, mois_test))
#     c.commit()
# _gain_force(E, mois_test, 625000)
# MON.verifier_numero(E, "90000027", "Moov")
# with _conn() as c:
#     c.execute("UPDATE kyc_numeros SET statut='verifie' WHERE user_id=?", (E,))
#     c.commit()
# dem = MON.demander_reversement(E, mois_test, "90000027", "Moov", "Bénin", "cinetpay")
# print("  demande : %s | montant %s | net %s | palier %s (%s) | prévu %s" % (
#     dem["reference_interne"], MON.formater(dem["montant_centimes"]),
#     MON.formater(dem["net_centimes"]), dem["palier"], dem["palier_nom"], dem["date_prevue"]))
# verifier("montant > 500 000 FCFA -> double signature exigée", dem["double_signature_requise"])
#
# sans_sig = MON.executer_reversement(dem["id"])
# verifier("exécution sans approbateur REFUSÉE sur un gros montant",
#          (not sans_sig["ok"]) and sans_sig["motif"] == "double_signature_requise")
#
# fait = MON.executer_reversement(dem["id"], approuve_par=1)
# print("  exécution : statut=%s | référence_api=%s" % (fait["statut"], fait["reference_api"]))
# verifier("versement exécuté -> statut 'sent'", fait["ok"] and fait["statut"] == "sent")
# st = MON.statut_reversement(dem["reference_interne"])
# verifier("statut consultable et cohérent avec le versement",
#          st["trouve"] and st["statut"] == "sent" and st["reference_api"] == fait["reference_api"])
# rejoue = MON.executer_reversement(dem["id"], approuve_par=1)
# verifier("rejeu d'un versement déjà traité REFUSÉ (anti-double paiement)",
#          (not rejoue["ok"]) and rejoue["motif"] == "deja_traite")
#
# simple = MON.executer_reversement(accept["id"], approuve_par=1)
# verifier("petit montant : exécution sans double signature",
#          simple["ok"] and simple["statut"] == "sent")
#
# with _conn() as c:
#     statut_gain = c.execute("SELECT statut FROM gains_createurs WHERE createur_id=? AND mois=?",
#                             (E, mois_test)).fetchone()["statut"]
# verifier("gain passé au statut 'reverse' après versement", statut_gain == "reverse", statut_gain)
# attente = MON.reversements_en_attente()
# verifier("file d'attente des reversements cohérente",
#          all(r["statut"] in ("en_attente", "approuve") for r in attente),
#          "n=%d" % len(attente))
#
# print()
# print("=" * 104)
# print("12) MESSAGE TEXTE DE FIN DE MOIS (loi 100 % TEXTE)")
# print("=" * 104)
# for uid, nom_ in ((A, "aminaa"), (C, "petitx"), (E, "sanskyd")):
#     texte = MON.message_fin_de_mois(uid, mois_test)
#     print("  %s : %s" % (nom_, texte))
#     verifier("message %s sans aucun média" % nom_,
#              all(mot not in texte.lower() for mot in
#                  ("<img", "<video", "<audio", ".jpg", ".png", ".mp4", ".mp3")))
# verifier("message sous le seuil mentionne le report automatique",
#          "report" in MON.message_fin_de_mois(C, mois_test).lower())
# verifier("le moteur expose brancher_sur_toutbot pour la greffe V8",
#          callable(MON.brancher_sur_toutbot))
# verifier("aucune balise média dans le schéma ni dans les gabarits du moteur",
#          all(mot not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
#                                           "monetisation_v8.py"), encoding="utf-8").read()
#              for mot in ("<img", "<video", "<audio")))
#
# print()
# print("=" * 104)
# total = len(RESULTATS)
# reussis = sum(1 for _, c in RESULTATS if c)
# print("TEST MONÉTISATION V8 : %d/%d" % (reussis, total))
# if reussis != total:
#     print("ÉCHECS :")
#     for n, c in RESULTATS:
#         if not c:
#             print("   - " + n)
# print("=" * 104)
# sys.exit(0 if reussis == total else 1)


# =============================================================================
# V14 — TABLEAU DE BORD IA EN DIRECT (100 % texte, aucune clé exposée)
# -----------------------------------------------------------------------------
#   • GET /tableau-ia        → page autonome qui sonde l'état des IA en direct
#   • GET /api/etat-ia-live  → état V13 + horodatage + latence + compteur
#   La page ne contient AUCUN média (loi de l'application : 100 % texte).
# =============================================================================
_V14_TABLEAU_IA_INTEGRE = True
_V14_VERSION = "14.0.0"
_V14_INTERVALLE = float(os.environ.get("TOUTBOT_V14_INTERVALLE", "5"))
_V14_COMPTEURS = {"appels": 0, "derniere_latence_ms": None, "dernier_instant": None}


def _v14_etat_live():
    """État des IA enrichi : horodatage, latence mesurée et compteur d'appels."""
    debut = time.time()
    etat = dict(_v13_etat_ia())          # aucune clé : la V13 ne renvoie que des booléens
    latence = round((time.time() - debut) * 1000.0, 3)
    _V14_COMPTEURS["appels"] += 1
    _V14_COMPTEURS["derniere_latence_ms"] = latence
    _V14_COMPTEURS["dernier_instant"] = time.strftime("%Y-%m-%d %H:%M:%S")

    fournisseurs = etat.get("fournisseurs", [])
    actifs = [f["nom"] for f in fournisseurs if f.get("actif")]
    etat.update({
        "tableau_de_bord": _V14_VERSION,
        "horodatage": _V14_COMPTEURS["dernier_instant"],
        "latence_ms": latence,
        "appels_au_tableau": _V14_COMPTEURS["appels"],
        "intervalle_secondes": _V14_INTERVALLE,
        "fournisseurs_actifs": actifs,
        "fournisseurs_inactifs": [f["nom"] for f in fournisseurs if not f.get("actif")],
        "sante": "opérationnelle" if actifs else "dégradée",
    })
    return etat


def _v14_repondre_live():
    reponse = jsonify(_v14_etat_live())
    reponse.headers["Cache-Control"] = "no-store, max-age=0"
    return reponse


_V14_PAGE = r"""<!DOCTYPE html>
<html lang="fr" data-theme="sombre">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tableau de bord IA — ToutBot Mundo</title>
<style>
:root{--or:#c8a24a;--fond:#10141c;--carte:#1a2130;--bord:#2b3549;--texte:#e9edf5;
--muet:#8e9bb3;--vert:#2fbf71;--rouge:#e0575b;--bleu:#4d8ef7;--champ:#0f141d}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--texte);
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:18px}
h1{color:var(--or);font-size:20px;margin:0 0 4px}
.sous{color:var(--muet);font-size:12px;margin-bottom:14px}
.bandeau{display:flex;gap:10px;flex-wrap:wrap;align-items:center;
background:var(--carte);border:1px solid var(--bord);border-radius:12px;
padding:12px 14px;margin-bottom:14px}
.pastille{border-radius:999px;padding:3px 12px;font-size:12px;font-weight:700}
.ok{background:var(--vert);color:#04220f}
.ko{background:#20293b;color:var(--muet)}
.grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:12px;padding:14px}
.carte h2{margin:0 0 8px;font-size:14px;color:var(--or)}
.kpi{font-size:26px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bord)}
th{color:var(--muet);font-weight:600}
.journal{max-height:210px;overflow:auto;font-size:12px;color:var(--muet);
background:var(--champ);border:1px solid var(--bord);border-radius:9px;padding:9px}
.journal div{padding:2px 0}
button{font:inherit;background:var(--or);color:#191307;border:none;border-radius:9px;
padding:9px 14px;font-weight:700;cursor:pointer}
button.sec{background:#20293b;color:var(--texte);border:1px solid var(--bord)}
.mini{color:var(--muet);font-size:11px}
</style>
</head>
<body>
<h1>&#128202; Tableau de bord IA en direct</h1>
<div class="sous">ToutBot Mundo &middot; couche V14 &middot; 100 % texte &middot; aucune cl&eacute; expos&eacute;e</div>

<div class="bandeau">
  <span id="etat-sante" class="pastille ko">connexion&hellip;</span>
  <span class="mini">horodatage : <b id="horodatage">&mdash;</b></span>
  <span class="mini">latence : <b id="latence">&mdash;</b></span>
  <span class="mini">intervalle : <b id="intervalle">__INTERVALLE__</b> s</span>
  <button id="btn-rafraichir">Rafra&icirc;chir maintenant</button>
  <button id="btn-auto" class="sec">Pause du direct</button>
</div>

<div class="grille">
  <div class="carte">
    <h2>Noyau</h2>
    <div class="kpi" id="version">&mdash;</div>
    <div class="mini">transport&nbsp;: <b id="transport">&mdash;</b></div>
    <div class="mini">fournisseurs actifs&nbsp;: <b id="nb-actifs">0</b></div>
    <div class="mini">appels au tableau&nbsp;: <b id="appels">0</b></div>
  </div>
  <div class="carte">
    <h2>Cha&icirc;ne d'IA (ordre de repli)</h2>
    <div id="chaine" class="mini">&mdash;</div>
  </div>
  <div class="carte">
    <h2>Routes de l'assistant</h2>
    <div id="routes" class="mini">&mdash;</div>
  </div>
</div>

<div class="carte" style="margin-top:12px">
  <h2>Fournisseurs</h2>
  <table>
    <thead><tr><th>Fournisseur</th><th>Variable d'environnement</th>
    <th>Mod&egrave;le</th><th>Actif</th></tr></thead>
    <tbody id="fournisseurs"><tr><td colspan="4" class="mini">en attente&hellip;</td></tr></tbody>
  </table>
</div>

<div class="carte" style="margin-top:12px">
  <h2>Journal du direct</h2>
  <div class="journal" id="journal"></div>
</div>

<script>
(function(){
  var intervalle = Number("__INTERVALLE__") || 5;
  var auto = true, minuteur = null, dernier = 0, echecs = 0;
  var $ = function(id){ return document.getElementById(id); };
  function ligne(texte){
    var d = document.createElement("div");
    d.textContent = new Date().toLocaleTimeString() + "  " + texte;
    var j = $("journal"); j.insertBefore(d, j.firstChild);
    while (j.childNodes.length > 120) { j.removeChild(j.lastChild); }
  }
  function rendre(d, ms){
    $("version").textContent = "v" + (d.version || "?");
    $("transport").textContent = d.transport || "?";
    $("horodatage").textContent = d.horodatage || new Date().toLocaleString();
    $("latence").textContent = (d.latence_ms !== undefined ? d.latence_ms : ms) + " ms";
    $("intervalle").textContent = d.intervalle_secondes || intervalle;
    $("appels").textContent = d.appels_au_tableau || 0;
    var f = d.fournisseurs || [];
    var actifs = f.filter(function(x){ return x.actif; });
    $("nb-actifs").textContent = actifs.length + " / " + f.length;
    var s = $("etat-sante");
    s.textContent = (d.sante === "opérationnelle") ? "IA opérationnelle" : "IA dégradée";
    s.className = "pastille " + (actifs.length ? "ok" : "ko");
    $("chaine").textContent = f.map(function(x, i){ return (i+1) + ". " + x.nom; }).join("  \u2192  ") || "—";
    $("routes").innerHTML = (d.routes || []).map(function(r){ return "&bull; " + r; }).join("<br>");
    var tb = $("fournisseurs"); tb.innerHTML = "";
    f.forEach(function(x){
      var tr = document.createElement("tr");
      tr.innerHTML = "<td>" + x.nom + "</td><td><code>" + x.variable + "</code></td><td>"
        + x.modele + "</td><td><span class='pastille " + (x.actif ? "ok" : "ko") + "'>"
        + (x.actif ? "actif" : "inactif") + "</span></td>";
      tb.appendChild(tr);
    });
  }
  function sonder(){
    var t0 = performance.now();
    fetch("/api/etat-ia-live", {cache:"no-store"})
      .then(function(r){ if(!r.ok){ throw new Error("HTTP " + r.status); } return r.json(); })
      .then(function(d){
        echecs = 0;
        var ms = Math.round(performance.now() - t0);
        rendre(d, ms);
        if (Date.now() - dernier > 1000) { ligne("état reçu — latence " + ms + " ms"); dernier = Date.now(); }
      })
      .catch(function(e){
        echecs++;
        var s = $("etat-sante"); s.textContent = "hors ligne (" + echecs + ")"; s.className = "pastille ko";
        ligne("échec du sondage : " + e.message);
      });
  }
  function planifier(){ if (auto) { minuteur = setTimeout(function(){ sonder(); planifier(); }, intervalle*1000); } }
  $("btn-rafraichir").onclick = function(){ sonder(); };
  $("btn-auto").onclick = function(){
    auto = !auto;
    this.textContent = auto ? "Pause du direct" : "Reprendre le direct";
    ligne(auto ? "direct repris" : "direct mis en pause");
    if (auto) { planifier(); } else if (minuteur) { clearTimeout(minuteur); }
  };
  ligne("tableau de bord V14 initialisé — sondage toutes les " + intervalle + " s");
  sonder(); planifier();
})();
</script>
</body>
</html>
"""


def _v14_page():
    """Page autonome du tableau de bord (intervalle injecté, aucun média)."""
    return _V14_PAGE.replace("__INTERVALLE__", str(_V14_INTERVALLE))


def _v14_enregistrer_routes():
    ajoutees = []
    for chemin, point, vue, methodes in (
            ("/api/etat-ia-live", "v14_etat_ia_live", _v14_repondre_live, ["GET"]),
            ("/tableau-ia", "v14_tableau_ia", _v14_page, ["GET"])):
        if point not in app.view_functions:
            app.add_url_rule(chemin, point, vue, methods=methodes)
            ajoutees.append(chemin)
    return ajoutees


def _v14_ajouter_lien_navigation():
    """Ajoute un lien « Tableau IA » dans le gabarit de base, si l'ancre existe."""
    try:
        base = TEMPLATES.get("base.html")
        if not base or "/tableau-ia" in base:
            return False
        lien = '<a href="/tableau-ia" title="État des IA en direct">Tableau IA</a>'
        for ancre in ("</nav>", "</header>", "<main"):
            if ancre in base:
                TEMPLATES["base.html"] = base.replace(ancre, lien + ancre, 1)
                return True
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("V14 lien de navigation : %s", exc)
    return False


_V14_ROUTES_AJOUTEES = _v14_enregistrer_routes()
_V14_LIEN_AJOUTE = _v14_ajouter_lien_navigation()
try:
    LOGGER.info("V14 : tableau de bord IA en direct actif — routes %s (%s)",
                _V14_ROUTES_AJOUTEES, _V14_VERSION)
except Exception:  # noqa: BLE001
    pass
# ======================= FIN DU BLOC V14 =====================================

# =============================================================================
# V15 — MONÉTISATION V8 + COMPARATIF IA + PANNEAU DOCUMENTATION (100 % texte)
# =============================================================================
_V15_TRIPLE_INTEGRE = True
_V15_VERSION = "15.0.0"

# --- (C) Transcription VERBATIM de la capture d'écran fournie ----------------
#     Aucun élément inventé. Les horodatages sont ceux affichés.
_V15_CAPTURE = {
    "url": "console.cloud.google.com",
    "projet": "BBBOT",
    "organisation": "Aucune organisation",
    "bandeau": "Affichage en cours du projet \"BBBOT\" de l'organisation \"Aucune organisation\"",
    "carte_quota": "Configurer des alertes pour les quotas et les limites du système",
    "carte_quota_texte": ("Recevez une alerte si un quota est sur le point d'atteindre sa "
                          "limite maximale. Cliquez sur le bouton \"Plus d'actions\" dans une "
                          "ligne pour commencer, ou sur \"En savoir plus\" pour consulter la "
                          "documentation."),
    "essai": "Démarrez votre essai sans frais et bénéficiez d'un crédit de 300 $.",
    "services_actives": [
        {"service": "plus.googleapis.com", "horodatage": "À l'instant"},
        {"service": "backupdr.googleapis.com", "horodatage": "il y a 2 minutes"},
        {"service": "earthengine.googleapis.com", "horodatage": "il y a 4 minutes"},
        {"service": "maps-android-backend.googleapis.com", "horodatage": "il y a 6 minutes"},
    ],
    "evenements": [
        {"libelle": "Créer une organisation et un projet", "horodatage": "il y a 4 jours"},
    ],
}

# --- (C) Documentation réelle associée à la capture --------------------------
_V15_DOCUMENTATION = (
    {"titre": "Plus Codes — bibliothèque d'API (plus.googleapis.com)",
     "mots": "plus plus.googleapis.com pluscodes codes plus",
     "url": "https://console.cloud.google.com/apis/library/plus.googleapis.com"},
    {"titre": "Backup and DR Service — documentation (backupdr.googleapis.com)",
     "mots": "backupdr backup dr sauvegarde disaster recovery",
     "url": "https://cloud.google.com/backup-disaster-recovery/docs"},
    {"titre": "Earth Engine — guides (earthengine.googleapis.com)",
     "mots": "earthengine earth engine satellite geospatial",
     "url": "https://developers.google.com/earth-engine/guides"},
    {"titre": "Maps SDK for Android (maps-android-backend.googleapis.com)",
     "mots": "maps android backend cartographie google maps",
     "url": "https://developers.google.com/maps/documentation/android-sdk"},
    {"titre": "Mistral — tarifs de l'inférence",
     "mots": "mistral tarif prix token api",
     "url": "https://docs.mistral.ai/inference/pricing"},
    {"titre": "Groq — modèles, prix et limites",
     "mots": "groq modele prix limite token api",
     "url": "https://console.groq.com/docs/models"},
    {"titre": "Gemini — tarifs de l'API",
     "mots": "gemini tarif prix token api google",
     "url": "https://ai.google.dev/gemini-api/docs/pricing"},
    {"titre": "Gemini — limites de débit",
     "mots": "gemini limite debit rate limit rpm tpm rpd",
     "url": "https://ai.google.dev/gemini-api/docs/rate-limits"},
)

_V15_REQUETE_SEED = ("plus.googleapis.com backupdr.googleapis.com "
                     "earthengine.googleapis.com maps-android-backend.googleapis.com "
                     "BBBOT")

# --- (B) Comparatif des fournisseurs — valeurs relevées le 2026-09-25 --------
_V15_NOTE_COMPARATIF = (
    "Prix en USD par million de jetons, relevés le 2026-09-25 sur les pages "
    "officielles de chaque fournisseur. Les valeurs marquées « ~ » sont des "
    "estimations de traceurs tiers, non confirmées par le fournisseur.")

_V15_COMPARATIF = (
    {"nom": "Mistral", "modele": "mistral-small-latest",
     "prix_entree": "$0.15 /M (Mistral Small 4)",
     "prix_sortie": "$0.60 /M",
     "contexte": "non publié sur la page tarifaire",
     "limites": "non confirmées (offre gratuite signalée comme généreuse)",
     "source_url": "https://docs.mistral.ai/inference/pricing"},
    {"nom": "Groq", "modele": "llama-3.3-70b-versatile",
     "prix_entree": "Contact commercial (plan développeur) ; ~$0.59 /M selon des traceurs tiers",
     "prix_sortie": "~$0.79 /M selon des traceurs tiers",
     "contexte": "131 072 jetons ; sortie max 32 768",
     "limites": "plan développeur : « ContactSales » (pas de chiffre public)",
     "source_url": "https://console.groq.com/docs/models"},
    {"nom": "Gemini", "modele": "gemini-2.5-flash",
     "prix_entree": "$0.10 /M (texte/image/vidéo), $0.30 /M (audio) — palier payant",
     "prix_sortie": "$0.40 /M (jetons de réflexion inclus)",
     "contexte": "non publié sur la page tarifaire",
     "limites": "chiffres non publiés : à consulter dans Google AI Studio",
     "source_url": "https://ai.google.dev/gemini-api/docs/pricing"},
)


# --- (A) Tuiles monétisation : réutilise les fonctions V8 existantes ---------
def _v15_monetisation():
    """Instantané de la monétisation V8 — aucune écriture, aucune clé."""
    def _f(valeur):
        try:
            return float(valeur)
        except Exception:
            return 0.0

    bloc = {"version": _V15_VERSION}
    try:
        rev = reversements_en_attente()
        par_statut = {}
        for ligne in rev:
            etiquette = str(ligne.get("statut") or "?")
            par_statut[etiquette] = par_statut.get(etiquette, 0) + 1
        bloc["reversements_en_attente"] = {
            "nombre": len(rev),
            "montant_fcfa": round(sum(_f(l.get("montant_fcfa")) for l in rev), 2),
            "par_statut": par_statut,
        }
    except Exception as exc:
        bloc["reversements_en_attente"] = {"erreur": str(exc), "nombre": 0, "montant_fcfa": 0.0}

    try:
        bloc["recettes_totales_fcfa"] = round(_f(total_recettes()), 2)
    except Exception as exc:
        bloc["recettes_totales_fcfa"] = None
        bloc["recettes_erreur"] = str(exc)

    try:
        paiements = tableau_paiement()
        bloc["paiements_en_attente"] = {
            "nombre": len(paiements),
            "montant_fcfa": round(sum(_f(l.get("fcfa")) for l in paiements), 2),
            "tickets": round(sum(_f(l.get("tickets")) for l in paiements), 2),
        }
    except Exception as exc:
        bloc["paiements_en_attente"] = {"erreur": str(exc), "nombre": 0, "montant_fcfa": 0.0}

    try:
        bloc["paliers"] = len(PALIERS_REVERSEMENT)
        bloc["noms_paliers"] = [p.get("nom") for p in PALIERS_REVERSEMENT]
        bloc["delais_jours"] = [int(p.get("delai_jours") or 0) for p in PALIERS_REVERSEMENT]
    except Exception as exc:
        bloc["paliers"] = 0
        bloc["paliers_erreur"] = str(exc)

    try:
        taux = globals().get("TAUX_POOL_PAR_MILLE_CENTIMES")
        seuil = globals().get("SEUIL_REVERSEMENT_CENTIMES")
        bloc["taux_par_mille"] = formater(taux) if taux is not None else "-"
        bloc["seuil_reversement"] = formater(seuil) if seuil is not None else "-"
    except Exception:
        bloc["taux_par_mille"] = "-"
        bloc["seuil_reversement"] = "-"
    return bloc


def _v15_repondre_monetisation():
    return jsonify(_v15_monetisation())


# --- (B)(C) Réponses JSON ----------------------------------------------------
def _v15_repondre_comparatif():
    return jsonify({
        "version": _V15_VERSION,
        "releve_le": "2026-09-25",
        "note": _V15_NOTE_COMPARATIF,
        "fournisseurs": [dict(f) for f in _V15_COMPARATIF],
    })


def _v15_repondre_documentation():
    requete = (request.args.get("q") or "").strip()
    mots = [m.lower() for m in requete.split() if len(m) > 1]
    resultats = []
    for entree in _V15_DOCUMENTATION:
        foin = (entree["titre"] + " " + entree["mots"]).lower()
        score = sum(1 for m in mots if m in foin)
        if not mots or score:
            resultats.append({"titre": entree["titre"], "url": entree["url"], "score": score})
    resultats.sort(key=lambda e: e["score"], reverse=True)
    return jsonify({
        "version": _V15_VERSION,
        "requete": requete,
        "requete_seed": _V15_REQUETE_SEED,
        "capture": _V15_CAPTURE,
        "total_liens": len(_V15_DOCUMENTATION),
        "trouves": len(resultats),
        "resultats": resultats,
        "note": ("Panneau documentaire : liens officiels indexés localement. "
                 "Aucun accès à un index interne d'un moteur de recherche."),
    })


def _v15_etat_live_base():
    return _v14_etat_live_interne()


def _v15_page_documentation():
    return (_V15_PAGE_DOC.replace("__SEED__", _V15_REQUETE_SEED)
            .replace("__PROJET__", _V15_CAPTURE["projet"]))


# --- (A)(B) Page /tableau-ia étendue : on remplace la vue déjà enregistrée ---
_V15_CARTES = """
<div class="carte" style="margin-top:12px">
  <h2>Monétisation V8 — Mobile Money (FCFA)</h2>
  <div class="grille">
    <div class="carte"><h2>Reversements en attente</h2>
      <div class="kpi" id="mon-rev">-</div>
      <div class="mini">dossiers : <b id="mon-rev-nb">0</b></div></div>
    <div class="carte"><h2>Recettes application</h2>
      <div class="kpi" id="mon-recettes">-</div>
      <div class="mini">abonnements + pourboires + vues</div></div>
    <div class="carte"><h2>Paiements en attente</h2>
      <div class="kpi" id="mon-paiement">-</div>
      <div class="mini">comptes : <b id="mon-paiement-nb">0</b> · tickets : <b id="mon-tickets">0</b></div></div>
    <div class="carte"><h2>Bar&egrave;me</h2>
      <div class="kpi" id="mon-taux">-</div>
      <div class="mini">seuil : <b id="mon-seuil">-</b></div></div>
  </div>
  <div class="mini" style="margin-top:8px" id="mon-paliers">-</div>
</div>

<div class="carte" style="margin-top:12px">
  <h2>Tableau comparatif des fournisseurs d'IA</h2>
  <table>
    <thead><tr><th>Fournisseur</th><th>Mod&egrave;le d&eacute;faut</th><th>Entr&eacute;e /M jetons</th>
    <th>Sortie /M jetons</th><th>Contexte</th><th>Limites</th><th>Source</th></tr></thead>
    <tbody id="comparatif"><tr><td colspan="7" class="mini">en attente&hellip;</td></tr></tbody>
  </table>
  <div class="mini" style="margin-top:8px" id="comparatif-note"></div>
</div>

<div class="carte" style="margin-top:12px">
  <h2>Panneau documentation (projet __PROJET__)</h2>
  <div class="mini" id="doc-resume">&mdash;</div>
  <div id="doc-liens" class="mini" style="margin-top:6px">&mdash;</div>
  <div class="mini" style="margin-top:6px">
    <a href="/documentation">Ouvrir le panneau de recherche documentaire &rarr;</a>
  </div>
</div>
"""

_V15_SCRIPT = """
<script>
(function(){
  var $ = function(i){ return document.getElementById(i); };
  function fcfa(v){ return (v === null || v === undefined) ? "-" : Number(v).toLocaleString("fr-FR") + " FCFA"; }
  function chargerMonetisation(){
    fetch("/api/monetisation", {cache:"no-store"}).then(function(r){ return r.json(); }).then(function(d){
      var rev = d.reversements_en_attente || {};
      $("mon-rev").textContent = fcfa(rev.montant_fcfa);
      $("mon-rev-nb").textContent = rev.nombre || 0;
      $("mon-recettes").textContent = fcfa(d.recettes_totales_fcfa);
      var p = d.paiements_en_attente || {};
      $("mon-paiement").textContent = fcfa(p.montant_fcfa);
      $("mon-paiement-nb").textContent = p.nombre || 0;
      $("mon-tickets").textContent = p.tickets || 0;
      $("mon-taux").textContent = d.taux_par_mille || "-";
      $("mon-seuil").textContent = d.seuil_reversement || "-";
      var delais = (d.delais_jours || []).join(", ") || "-";
      $("mon-paliers").textContent = "Paliers de reversement : " + (d.paliers || 0)
        + " | delais (jours) : " + delais;
    }).catch(function(){ $("mon-paliers").textContent = "monetisation indisponible"; });
  }
  function chargerComparatif(){
    fetch("/api/comparatif-ia", {cache:"no-store"}).then(function(r){ return r.json(); }).then(function(d){
      var tb = $("comparatif"); tb.innerHTML = "";
      (d.fournisseurs || []).forEach(function(f){
        var tr = document.createElement("tr");
        tr.innerHTML = "<td><b>" + f.nom + "</b></td>"
          + "<td><code>" + f.modele + "</code></td>"
          + "<td>" + f.prix_entree + "</td>"
          + "<td>" + f.prix_sortie + "</td>"
          + "<td>" + f.contexte + "</td>"
          + "<td>" + f.limites + "</td>"
          + "<td><a href='" + f.source_url + "' target='_blank' rel='noopener'>doc</a></td>";
        tb.appendChild(tr);
      });
      $("comparatif-note").textContent = d.note || "";
    });
  }
  function chargerDocumentation(){
    fetch("/api/documentation?q=__SEED__", {cache:"no-store"}).then(function(r){ return r.json(); }).then(function(d){
      $("doc-resume").textContent = "Liens indexes : " + d.total_liens + " | resultats pour la requete seed : " + d.trouves;
      var h = "";
      (d.resultats || []).slice(0, 6).forEach(function(l){
        h += "&bull; <a href='" + l.url + "' target='_blank' rel='noopener'>" + l.titre + "</a><br>";
      });
      $("doc-liens").innerHTML = h || "aucun resultat";
    });
  }
  chargerMonetisation(); chargerComparatif(); chargerDocumentation();
})();
</script>
"""

_V15_PAGE_DOC = """<!DOCTYPE html>
<html lang="fr" data-theme="sombre">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panneau documentation &mdash; ToutBot Mundo</title>
<style>
:root{--or:#c8a24a;--fond:#10141c;--carte:#1a2130;--bord:#2b3549;--texte:#e9edf5;
--muet:#8e9bb3;--vert:#2fbf71;--champ:#0f141d}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--texte);
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:18px}
h1{color:var(--or);font-size:19px;margin:0 0 4px}
h2{color:var(--or);font-size:14px;margin:16px 0 8px}
.sous{color:var(--muet);font-size:12px;margin-bottom:14px}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:12px;padding:14px;margin-bottom:12px}
input{font:inherit;width:100%;background:var(--champ);color:var(--texte);
border:1px solid var(--bord);border-radius:9px;padding:10px}
button{font:inherit;background:var(--or);color:#191307;border:none;border-radius:9px;
padding:10px 14px;font-weight:700;cursor:pointer;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bord)}
th{color:var(--muet)}
.mini{color:var(--muet);font-size:12px}
a{color:#9ecbff}
</style>
</head>
<body>
<h1>&#128218; Panneau documentation</h1>
<div class="sous">ToutBot Mundo &middot; couche V15 &middot; projet __PROJET__ &middot; 100 % texte</div>

<div class="carte">
  <h2>Recherche documentaire</h2>
  <input id="q" value="__SEED__" placeholder="mots-cles (service, tarif, limite, projet...)">
  <button id="go">Chercher</button>
  <div class="mini" style="margin-top:8px" id="resume">&mdash;</div>
</div>

<div class="carte">
  <h2>Liens index&eacute;s</h2>
  <div id="liens" class="mini">&mdash;</div>
</div>

<div class="carte">
  <h2>Capture Google Cloud Console (transcription verbatim)</h2>
  <table>
    <tr><th>URL</th><td id="cap-url">&mdash;</td></tr>
    <tr><th>Projet</th><td id="cap-projet">&mdash;</td></tr>
    <tr><th>Organisation</th><td id="cap-org">&mdash;</td></tr>
    <tr><th>Bandeau</th><td id="cap-bandeau">&mdash;</td></tr>
    <tr><th>Carte quotas</th><td id="cap-quota">&mdash;</td></tr>
    <tr><th>Essai</th><td id="cap-essai">&mdash;</td></tr>
  </table>
  <h2>Services activ&eacute;s</h2>
  <table id="cap-services"><tr><th>Service</th><th>Horodatage</th></tr></table>
  <div class="mini" id="cap-evenements" style="margin-top:8px">&mdash;</div>
</div>

<script>
(function(){
  var $ = function(i){ return document.getElementById(i); };
  function chercher(){
    var q = encodeURIComponent($("q").value || "");
    fetch("/api/documentation?q=" + q, {cache:"no-store"}).then(function(r){ return r.json(); }).then(function(d){
      $("resume").textContent = d.trouves + " lien(s) sur " + d.total_liens + " indexes"
        + (d.requete ? " pour : " + d.requete : "");
      var h = "";
      (d.resultats || []).forEach(function(l){
        h += "&bull; <a href='" + l.url + "' target='_blank' rel='noopener'>" + l.titre + "</a> (score " + l.score + ")<br>";
      });
      $("liens").innerHTML = h || "aucun resultat";
      var c = d.capture || {};
      $("cap-url").textContent = c.url || "";
      $("cap-projet").textContent = c.projet || "";
      $("cap-org").textContent = c.organisation || "";
      $("cap-bandeau").textContent = c.bandeau || "";
      $("cap-quota").textContent = (c.carte_quota || "") + " — " + (c.carte_quota_texte || "");
      $("cap-essai").textContent = c.essai || "";
      var tb = $("cap-services");
      tb.innerHTML = "<tr><th>Service</th><th>Horodatage</th></tr>";
      (c.services_actives || []).forEach(function(s){
        var tr = document.createElement("tr");
        tr.innerHTML = "<td><code>" + s.service + "</code></td><td>" + s.horodatage + "</td>";
        tb.appendChild(tr);
      });
      $("cap-evenements").textContent = (c.evenements || []).map(function(e){
        return e.libelle + " (" + e.horodatage + ")"; }).join(" | ");
    });
  }
  $("go").onclick = chercher;
  $("q").addEventListener("keydown", function(e){ if (e.key === "Enter") { chercher(); } });
  chercher();
})();
</script>
</body>
</html>
"""


def _v15_page_tableau():
    """Reprend la page V14 et y insère les cartes monétisation/comparatif/doc."""
    html = _V14_PAGE
    ancre = '<div class="carte" style="margin-top:12px">' + "\n" + '  <h2>Fournisseurs</h2>'
    if ancre in html:
        cartes = _V15_CARTES.replace("__PROJET__", _V15_CAPTURE["projet"])
        html = html.replace(ancre, cartes + ancre, 1)
        html = html.replace("</body>", _V15_SCRIPT.replace("__SEED__", _V15_REQUETE_SEED)
                            + "\n</body>", 1)
    return html.replace("__INTERVALLE__", str(_V14_INTERVALLE))


# --- État enrichi : la vue /api/etat-ia-live appelle désormais la V15 --------
_v15_etat_live_origine = _v14_etat_live


def _v14_etat_live():
    etat = _v15_etat_live_origine()
    etat["tableau_de_bord"] = _V15_VERSION
    etat["monetisation"] = _v15_monetisation()
    etat["comparatif_ia"] = {"fournisseurs": [dict(f) for f in _V15_COMPARATIF],
                             "note": _V15_NOTE_COMPARATIF}
    etat["documentation"] = {"liens": len(_V15_DOCUMENTATION),
                             "requete_seed": _V15_REQUETE_SEED,
                             "capture_projet": _V15_CAPTURE["projet"]}
    return etat


def _v15_enregistrer_routes():
    ajoutees = []
    for chemin, point, vue, methodes in (
            ("/api/monetisation", "v15_monetisation", _v15_repondre_monetisation, ["GET"]),
            ("/api/comparatif-ia", "v15_comparatif", _v15_repondre_comparatif, ["GET"]),
            ("/api/documentation", "v15_documentation", _v15_repondre_documentation, ["GET"]),
            ("/documentation", "v15_page_documentation", _v15_page_documentation, ["GET"])):
        if point not in app.view_functions:
            app.add_url_rule(chemin, point, vue, methods=methodes)
            ajoutees.append(chemin)
    return ajoutees


def _v15_ajouter_lien_navigation():
    try:
        base = TEMPLATES.get("base.html")
        if not base or "/documentation" in base:
            return False
        lien = '<a href="/documentation" title="Panneau documentation">Documentation</a>'
        for ancre in ("</nav>", "</header>", "<main"):
            if ancre in base:
                TEMPLATES["base.html"] = base.replace(ancre, lien + ancre, 1)
                return True
    except Exception as exc:
        LOGGER.debug("V15 lien de navigation : %s", exc)
    return False


if "v14_tableau_ia" in app.view_functions:
    app.view_functions["v14_tableau_ia"] = _v15_page_tableau
_V15_ROUTES_AJOUTEES = _v15_enregistrer_routes()
_V15_LIEN_AJOUTE = _v15_ajouter_lien_navigation()
try:
    LOGGER.info("V15 : monetisation + comparatif + documentation actifs — %s (%s)",
                _V15_ROUTES_AJOUTEES, _V15_VERSION)
except Exception:
    pass
# ======================= FIN DU BLOC V15 =====================================

# =============================================================================
# V16 — CONNECTEURS TEMPS RÉEL VERS L'IA (Géocodage, Lieux, Earth Engine)
# -----------------------------------------------------------------------------
#   Chaque source produite respecte le format du collecteur V13 :
#       {"titre", "extrait", "url", "moteur"}
#   Aucune clé n'est jamais renvoyée par une route. Aucun appel réseau n'est
#   fait à la collecte de l'état : l'état lit seulement l'environnement.
# =============================================================================
_V16_CONNECTEURS_INTEGRE = True
_V16_VERSION = "16.0.0"

_V16_GEOCODAGE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_V16_LIEUX_URL = "https://places.googleapis.com/v1/places:searchText"
_V16_LIEUX_CHAMPS = "places.displayName,places.formattedAddress,places.location"
_V16_EE_BASE = "https://earthengine.googleapis.com/v1"


def _v16_cle_maps():
    return (os.environ.get("GOOGLE_MAPS_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("TOUTBOT_GOOGLE_MAPS_KEY") or "").strip()


def _v16_projet_google():
    return (os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GOOGLE_PROJECT") or "").strip()


def _v16_cle_earth():
    return (os.environ.get("TOUTBOT_EE_SA_JSON")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()


def _v16_earth_disponible():
    """Earth Engine n'est utilisable qu'avec un compte de service + earthengine-api."""
    try:
        import ee  # noqa: F401
    except Exception:
        return False, "paquet earthengine-api absent"
    if not _v16_cle_earth():
        return False, "aucun compte de service (TOUTBOT_EE_SA_JSON)"
    if not _v16_projet_google():
        return False, "GOOGLE_CLOUD_PROJECT absent"
    return True, "prêt"


def _v16_etat_connecteurs():
    """État des connecteurs — aucun appel réseau, aucune clé exposée."""
    cle = bool(_v16_cle_maps())
    ee_ok, ee_motif = _v16_earth_disponible()
    return {
        "version": _V16_VERSION,
        "connecteurs": [
            {"nom": "Géocodage (Google Geocoding API)", "cle": "GOOGLE_MAPS_API_KEY",
             "service": "geocoding-backend.googleapis.com", "etat": "prêt" if cle else "non configuré",
             "appelable": cle, "source_url": _V16_GEOCODAGE_URL},
            {"nom": "Lieux (Places API — Text Search New)", "cle": "GOOGLE_MAPS_API_KEY",
             "service": "places-backend.googleapis.com", "etat": "prêt" if cle else "non configuré",
             "appelable": cle, "source_url": _V16_LIEUX_URL},
            {"nom": "Earth Engine (imagerie satellite)", "cle": "TOUTBOT_EE_SA_JSON",
             "service": "earthengine.googleapis.com", "etat": ("prêt" if ee_ok else "non configuré"),
             "appelable": ee_ok, "motif": ee_motif,
             "note": "exige un compte de service (OAuth2), jamais une simple clé API"},
            {"nom": "Google+ (plus.googleapis.com)", "cle": None,
             "service": "plus.googleapis.com", "etat": "indisponible", "appelable": False,
             "motif": "API fermée avec l'arrêt de Google+ (2 avril 2019)"},
            {"nom": "Backup and DR (backupdr.googleapis.com)", "cle": None,
             "service": "backupdr.googleapis.com", "etat": "non applicable", "appelable": False,
             "motif": "service de sauvegarde/reprise, ce n'est pas un flux d'information"},
        ],
        "note": ("L'activation de maps-android-backend.googleapis.com couvre le backend du "
                 "SDK Android : pour une application web il faut activer EN PLUS « Geocoding "
                 "API » et « Places API (New) ». Le projet se règle par GOOGLE_CLOUD_PROJECT."),
    }


def _v16_repondre_connecteurs():
    return jsonify(_v16_etat_connecteurs())


# --- Collecteurs réels -------------------------------------------------------
def _v16_geocoder(adresse, langue="fr"):
    """Géocodage d'une adresse → sources V13. Renvoie [] si non configuré."""
    cle = _v16_cle_maps()
    if not cle or not str(adresse).strip():
        return []
    params = {"address": adresse, "language": langue, "key": cle}
    ok, charge = _v13_http_get(_V16_GEOCODAGE_URL, params=params)
    if not ok or not isinstance(charge, dict):
        return []
    if charge.get("status") not in ("OK", "ZERO_RESULTS"):
        return []
    sources = []
    for resultat in (charge.get("results") or [])[:3]:
        pos = ((resultat.get("geometry") or {}).get("location") or {})
        sources.append({
            "titre": resultat.get("formatted_address") or str(adresse),
            "extrait": "Coordonnées : %s, %s" % (pos.get("lat"), pos.get("lng")),
            "url": "https://www.google.com/maps?q=%s,%s" % (pos.get("lat"), pos.get("lng")),
            "moteur": "Géocodage Google",
        })
    return sources


def _v16_lieux(requete, nb=5):
    """Places API (Text Search New) → sources V13. Renvoie [] si non configuré."""
    cle = _v16_cle_maps()
    if not cle or not str(requete).strip():
        return []
    entetes = {"X-Goog-Api-Key": cle, "X-Goog-FieldMask": _V16_LIEUX_CHAMPS}
    charge = {"textQuery": requete, "pageSize": int(nb)}
    ok, reponse = _v13_http_post_json(_V16_LIEUX_URL, charge, entetes=entetes)
    if not ok or not isinstance(reponse, dict):
        return []
    sources = []
    for lieu in (reponse.get("places") or [])[:nb]:
        nom = (lieu.get("displayName") or {}).get("text") or "Lieu"
        adresse = lieu.get("formattedAddress") or ""
        pos = lieu.get("location") or {}
        sources.append({
            "titre": nom,
            "extrait": adresse or "position %s, %s" % (pos.get("latitude"), pos.get("longitude")),
            "url": "https://www.google.com/maps/search/?api=1&query=%s" %
                   (" ".join((nom + " " + adresse).split()).replace(" ", "+")),
            "moteur": "Lieux Google",
        })
    return sources


def _v16_earth_engine(requete):
    """Earth Engine est GARDÉ : renvoie une source d'état, jamais une invention."""
    pret, motif = _v16_earth_disponible()
    if not pret:
        return []
    try:
        import ee
        if not getattr(ee, "_v16_init", False):
            ee.Initialize(project=_v16_projet_google())
            ee._v16_init = True
        # Interrogation minimale et honnête : les jeux de données disponibles.
        collections = ee.data.listAssets({"parent": "projects/earthengine-public/assets"})
        noms = [a.get("id", "") for a in (collections.get("assets") or [])][:3]
        return [{
            "titre": "Earth Engine — jeux de données publics",
            "extrait": "Disponibles : " + ", ".join(noms) if noms else "aucun jeu retourné",
            "url": "https://code.earthengine.google.com/",
            "moteur": "Earth Engine",
        }]
    except Exception as exc:
        return [{
            "titre": "Earth Engine — accès indisponible",
            "extrait": "Appel non abouti : %s" % exc,
            "url": "https://developers.google.com/earth-engine/guides/service_account",
            "moteur": "Earth Engine",
        }]


def _v16_collecter(question, lieu=None):
    """Sources temps réel pour l'IA : Lieux (+ Géocodage si un lieu est fourni)."""
    sources = []
    if lieu:
        try:
            sources.extend(_v16_geocoder(lieu))
        except Exception as exc:
            LOGGER.debug("V16 géocodage : %s", exc)
    try:
        sources.extend(_v16_lieux(question))
    except Exception as exc:
        LOGGER.debug("V16 lieux : %s", exc)
    return sources


# --- Fusion dans le collecteur V13 (détecté par son nom) ---------------------
_V16_AGREGATEUR_NOM = None


def _v16_installer_fusion():
    """Enveloppe le collecteur V13 s'il existe, pour y ajouter les sources V16."""
    global _V16_AGREGATEUR_NOM
    for nom in list(globals()):
        if "_v13_collect" not in nom or nom.startswith("_v16"):
            continue
        fonction = globals().get(nom)
        if not callable(fonction):
            continue

        def enveloppe(*args, _f=fonction, **kwargs):
            try:
                sources = list(_f(*args, **kwargs) or [])
            except Exception:
                sources = []
            question = ""
            if args:
                question = args[0]
            else:
                question = kwargs.get("question") or kwargs.get("requete") or ""
            try:
                sources.extend(_v16_collecter(str(question)))
            except Exception:
                pass
            return sources

        globals()[nom] = enveloppe
        _V16_AGREGATEUR_NOM = nom
        return nom
    return None


_V16_FUSION_INSTALLEE = _v16_installer_fusion()


# --- Routes ------------------------------------------------------------------
def _v16_repondre_temps_reel():
    question = (request.args.get("q") or "").strip()
    lieu = (request.args.get("lieu") or "").strip()
    sources = _v16_collecter(question, lieu or None) if question else []
    return jsonify({
        "version": _V16_VERSION,
        "question": question,
        "lieu": lieu,
        "total": len(sources),
        "sources": sources,
        "connecteurs": _v16_etat_connecteurs()["connecteurs"],
        "fusion_v13": _V16_AGREGATEUR_NOM or "non installée",
        "note": ("Sources collectées uniquement si GOOGLE_MAPS_API_KEY est posée. "
                 "Google+ (plus.googleapis.com) est fermé depuis 2019 : jamais appelé. "
                 "Backup and DR n'est pas un flux d'information : jamais appelé."),
    })


_V16_PAGE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connecteurs temps r&eacute;el &mdash; ToutBot Mundo</title>
<style>
body{margin:0;background:#10141c;color:#e9edf5;padding:18px;
font-family:ui-monospace,Menlo,Consolas,monospace}
h1,h2{color:#c8a24a}h1{font-size:19px;margin:0 0 4px}h2{font-size:14px;margin:0 0 10px}
.sous,.mini{color:#8e9bb3;font-size:12px}
.carte{background:#1a2130;border:1px solid #2b3549;border-radius:12px;padding:14px;margin-bottom:12px}
input{font:inherit;width:100%;background:#0f141d;color:#e9edf5;border:1px solid #2b3549;
border-radius:9px;padding:10px}
button{font:inherit;background:#c8a24a;color:#191307;border:none;border-radius:9px;
padding:10px 14px;font-weight:700;cursor:pointer;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #2b3549}
th{color:#8e9bb3}a{color:#9ecbff}
.badge{border-radius:999px;padding:2px 10px;font-size:11px;font-weight:700}
.pret{background:#2fbf71;color:#04220f}.off{background:#20293b;color:#8e9bb3}
.ko{background:#e0575b;color:#2b0808}
</style>
</head>
<body>
<h1>&#128225; Connecteurs temps r&eacute;el</h1>
<div class="sous">ToutBot Mundo &middot; couche V16 &middot; 100 % texte &middot; aucune cl&eacute; expos&eacute;e</div>

<div class="carte">
  <h2>Interroger le temps r&eacute;el</h2>
  <input id="q" placeholder="question (ex. : march&eacute; de Dantokpa Cotonou)">
  <input id="lieu" style="margin-top:8px" placeholder="lieu &agrave; g&eacute;ocoder (facultatif)">
  <button id="go">Collecter</button>
  <div class="mini" style="margin-top:8px" id="resume">&mdash;</div>
</div>

<div class="carte"><h2>Sources</h2><div id="sources" class="mini">&mdash;</div></div>

<div class="carte">
  <h2>&Eacute;tat des connecteurs</h2>
  <table><thead><tr><th>Connecteur</th><th>Service</th><th>&Eacute;tat</th><th>Motif</th></tr></thead>
  <tbody id="conn"><tr><td colspan="4" class="mini">chargement&hellip;</td></tr></tbody></table>
</div>

<script>
(function(){
  var $=function(i){return document.getElementById(i);};
  function etats(){
    fetch("/api/connecteurs",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
      var tb=$("conn");tb.innerHTML="";
      (d.connecteurs||[]).forEach(function(c){
        var cls=c.appelable?"pret":(c.etat==="prêt"?"pret":"off");
        var tr=document.createElement("tr");
        tr.innerHTML="<td>"+c.nom+"</td><td><code>"+c.service+"</code></td>"
          +"<td><span class='badge "+cls+"'>"+c.etat+"</span></td><td class='mini'>"
          +(c.motif||c.note||"")+"</td>";
        tb.appendChild(tr);
      });
    });
  }
  function collecter(){
    var q=encodeURIComponent($("q").value||""), l=encodeURIComponent($("lieu").value||"");
    fetch("/api/temps-reel?q="+q+"&lieu="+l,{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
      $("resume").textContent=d.total+" source(s) &middot; fusion V13 : "+d.fusion_v13;
      var h="";
      (d.sources||[]).forEach(function(s){
        h+="&bull; <b>"+s.moteur+"</b> &mdash; <a href='"+s.url+"' target='_blank' rel='noopener'>"+s.titre+"</a><br><span class='mini'>"+s.extrait+"</span><br>";
      });
      $("sources").innerHTML=h||"aucune source (cl&eacute; Google Maps absente ?)";
    });
  }
  $("go").onclick=collecter;
  etats();
})();
</script>
</body>
</html>
"""


def _v16_page_temps_reel():
    return _V16_PAGE


_V16_CARTE = """
<div class="carte" style="margin-top:12px">
  <h2>Connecteurs temps r&eacute;el (V16)</h2>
  <table><thead><tr><th>Connecteur</th><th>&Eacute;tat</th><th>Motif</th></tr></thead>
  <tbody id="conn-v16"><tr><td colspan="3" class="mini">chargement&hellip;</td></tr></tbody></table>
  <div class="mini" style="margin-top:6px">
    <a href="/temps-reel">Ouvrir la page des connecteurs temps r&eacute;el &rarr;</a>
  </div>
</div>
"""

_V16_SCRIPT = """
<script>
(function(){
  fetch("/api/connecteurs",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
    var tb=document.getElementById("conn-v16"); if(!tb){return;}
    tb.innerHTML="";
    (d.connecteurs||[]).forEach(function(c){
      var tr=document.createElement("tr");
      tr.innerHTML="<td>"+c.nom+"</td><td><span class='pastille "+(c.appelable?"ok":"ko")+"'>"
        +c.etat+"</span></td><td class='mini'>"+(c.motif||c.note||"")+"</td>";
      tb.appendChild(tr);
    });
  }).catch(function(){});
})();
</script>
"""

_v16_page_tableau_origine = app.view_functions.get("v14_tableau_ia")


def _v16_page_tableau():
    try:
        base = _v16_page_tableau_origine() if callable(_v16_page_tableau_origine) \
            else _v15_page_tableau()
    except Exception:
        base = _v15_page_tableau()
    if "</body>" in base:
        base = base.replace("</body>", _V16_CARTE + _V16_SCRIPT + "\n</body>", 1)
    return base


def _v16_enregistrer_routes():
    ajoutees = []
    for chemin, point, vue, methodes in (
            ("/api/connecteurs", "v16_connecteurs", _v16_repondre_connecteurs, ["GET"]),
            ("/api/temps-reel", "v16_temps_reel", _v16_repondre_temps_reel, ["GET"]),
            ("/temps-reel", "v16_page_temps_reel", _v16_page_temps_reel, ["GET"])):
        if point not in app.view_functions:
            app.add_url_rule(chemin, point, vue, methods=methodes)
            ajoutees.append(chemin)
    return ajoutees


def _v16_ajouter_lien_navigation():
    try:
        base = TEMPLATES.get("base.html")
        if not base or "/temps-reel" in base:
            return False
        lien = '<a href="/temps-reel" title="Connecteurs temps réel">Temps r\u00e9el</a>'
        for ancre in ("</nav>", "</header>", "<main"):
            if ancre in base:
                TEMPLATES["base.html"] = base.replace(ancre, lien + ancre, 1)
                return True
    except Exception as exc:
        LOGGER.debug("V16 lien de navigation : %s", exc)
    return False


if _v16_page_tableau_origine is not None:
    app.view_functions["v14_tableau_ia"] = _v16_page_tableau
_V16_ROUTES_AJOUTEES = _v16_enregistrer_routes()
_V16_LIEN_AJOUTE = _v16_ajouter_lien_navigation()
try:
    LOGGER.info("V16 : connecteurs temps reel actifs — %s | fusion V13 : %s (%s)",
                _V16_ROUTES_AJOUTEES, _V16_FUSION_INSTALLEE, _V16_VERSION)
except Exception:
    pass
# ======================= FIN DU BLOC V16 =====================================

# ======================= DEBUT DU BLOC V17 ==========================
# =============================================================================
# V17 - BLINDAGE RENFORCE + CLIQUABILITE + TABLEAU DE BORD IA EN DIRECT
# -----------------------------------------------------------------------------
#   1. Garde-fou sur la couche IA : entree validee, sortie durcie, latence
#      mesuree, pannes interceptees, compteurs temps reel.
#   2. En-tetes de securite HTTP sur toutes les reponses.
#   3. Cliquabilite poussee partout (tactile 44 px, feedback, focus clavier).
#   4. /tableau-ia : tableau de bord IA en direct (sonde toutes les 5 s).
# =============================================================================
import threading as _v17_threading
import time as _v17_time

_V17_BLINDAGE_INTEGRE = True
_V17_VERSION = "17.0.0"
_V17_LONGUEUR_MAX = 4000
_V17_SORTIE_MAX = 8000

_V17_VERROU = _v17_threading.Lock()
_V17_COMPTEUR = {"appels_ia": 0, "entrees_refusees": 0,
                 "erreurs_interceptees": 0, "latence_moyenne_ms": 0}


def _v17_entree_valide(question):
    """Une entree IA est valide si textuelle, non vide et bornee."""
    if not isinstance(question, str) or not question.strip():
        return False
    return len(question) <= _V17_LONGUEUR_MAX


def _v17_durcir_sortie(texte):
    """Durcit la sortie IA : caracteres de controle retires, longueur bornee."""
    if not isinstance(texte, str):
        texte = str(texte)
    texte = "".join(
        ch for ch in texte
        if ch in (chr(10), chr(9)) or (ord(ch) >= 32 and ord(ch) != 127)
    )
    if len(texte) > _V17_SORTIE_MAX:
        texte = texte[:_V17_SORTIE_MAX] + " ..."
    return texte.strip()


_v17_ia_origine = _v13_appeler_ia


def _v13_appeler_ia(question, sources):
    """Blindage V17 : valide l entree, chronometre, durcit la sortie et
    intercepte TOUTE panne - la couche IA ne fait jamais tomber l application.

    Corrige (26/09) : cette couche renvoyait une simple chaine, alors que
    _v13_poser() attend un dictionnaire {"texte", "fournisseur", "modele",
    "erreurs"} — ce qui provoquait un TypeError sur /api/poser des que le
    blindage interceptait quelque chose (refus ou panne). On renvoie desormais
    toujours ce meme format, texte durci compris."""
    if not _v17_entree_valide(question):
        with _V17_VERROU:
            _V17_COMPTEUR["entrees_refusees"] += 1
        try:
            LOGGER.warning("V17 blindage : entree IA refusee")
        except Exception:
            pass
        return {"texte": "[Blindage V17] Requete refusee : question vide ou trop longue.",
                "fournisseur": "", "modele": "", "erreurs": ["entree refusee"]}
    debut = _v17_time.time()
    try:
        reponse = _v17_ia_origine(question, sources)
    except Exception as exc:  # noqa: BLE001
        with _V17_VERROU:
            _V17_COMPTEUR["erreurs_interceptees"] += 1
        try:
            LOGGER.error("V17 blindage : panne IA interceptee - %s", exc)
        except Exception:
            pass
        return {"texte": ("[Blindage V17] Fournisseur IA momentanement "
                          "indisponible - repli securise actif."),
                "fournisseur": "", "modele": "", "erreurs": [repr(exc)[:200]]}
    with _V17_VERROU:
        _V17_COMPTEUR["appels_ia"] += 1
        latence = int((_v17_time.time() - debut) * 1000)
        _c = _V17_COMPTEUR
        _c["latence_moyenne_ms"] = ((_c["latence_moyenne_ms"] * (_c["appels_ia"] - 1))
                                    + latence) // max(_c["appels_ia"], 1)
    if isinstance(reponse, dict):
        reponse = dict(reponse)
        reponse["texte"] = _v17_durcir_sortie(reponse.get("texte", ""))
        reponse.setdefault("fournisseur", "")
        reponse.setdefault("modele", "")
        reponse.setdefault("erreurs", [])
        return reponse
    # Compatibilite : si l'origine renvoyait un jour une simple chaine.
    return {"texte": _v17_durcir_sortie(reponse), "fournisseur": "", "modele": "",
            "erreurs": []}


@app.after_request
def _v17_entetes_securite(reponse):
    """En-tetes de securite sur TOUTES les reponses (jamais en double)."""
    for _cle, _valeur in (
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            ("Content-Security-Policy",
             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
             "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
             "connect-src 'self'"),
    ):
        if _cle not in reponse.headers:
            reponse.headers[_cle] = _valeur
    return reponse


_V17_CSS_CLIC = (
    "<style data-v17-cliquabilite>"
    "a,button,.btn,summary,[role=button],input[type=submit]{cursor:pointer;"
    "min-height:44px;min-width:44px;touch-action:manipulation;"
    "-webkit-tap-highlight-color:transparent;"
    "transition:transform .08s ease,box-shadow .08s ease}"
    "a:active,button:active,.btn:active,summary:active,.v17-clic{transform:scale(.94)}"
    "a:focus-visible,button:focus-visible,summary:focus-visible"
    "{outline:3px solid #22c55e;outline-offset:2px}"
    "</style>"
    "<script data-v17-cliquabilite>"
    "document.addEventListener('click',function(e){"
    "var t=e.target.closest('a,button,.btn,summary,[role=button]');"
    "if(!t)return;"
    "t.classList.add('v17-clic');"
    "setTimeout(function(){t.classList.remove('v17-clic');},180);});"
    "</script>"
)


def _v17_injecter_cliquabilite():
    """Injecte la cliquabilite poussee dans le gabarit de base (idempotent)."""
    try:
        base = TEMPLATES.get("base.html")
        if not base or "data-v17-cliquabilite" in base:
            return False
        TEMPLATES["base.html"] = base + _V17_CSS_CLIC
        return True
    except Exception:
        return False


_V17_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tableau de bord IA en direct - ToutBot V__VERSION__</title>
<style>
:root{--vert:#22c55e;--rouge:#f43f5e;--fond:#070a13;--carte:#0c101f;--bord:#1f2a44;--texte:#e2e8f0;--bleu:#38bdf8}
*{box-sizing:border-box}
body{background:var(--fond);color:var(--texte);font-family:monospace;margin:0;padding:16px}
main{max-width:1000px;margin:auto}
h1{color:var(--rouge);font-size:1.3rem}
h2{color:var(--bleu);font-size:1.05rem;margin:22px 0 8px}
.grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.carte{background:var(--carte);border:1px solid var(--bord);border-radius:8px;padding:12px}
.kpi{font-size:1.5rem;color:var(--vert)}
.mini{color:#94a3b8;font-size:.8rem}
table{width:100%;border-collapse:collapse;background:var(--carte);border:1px solid var(--bord)}
th,td{padding:7px 9px;border-bottom:1px solid var(--bord);text-align:left;font-size:.85rem}
th{color:var(--bleu)}
.actif{color:var(--vert);font-weight:bold}
.inactif{color:var(--rouge)}
button{background:#132038;color:var(--texte);border:1px solid var(--bleu);border-radius:6px;padding:8px 14px}
.en-direct{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--vert);animation:pulse 1.6s infinite}
@keyframes pulse{0%{opacity:1}50%{opacity:.35}100%{opacity:1}}
</style>
</head>
<body>
<main>
<h1>Tableau de bord IA en direct <span class="en-direct"></span></h1>
<p class="mini">Noyau ToutBot V__VERSION__ - rafraichissement automatique 5 s - 100 pour cent texte, aucune donnee sensible affichee.</p>

<h2>Fournisseurs IA (etat live)</h2>
<div class="grille">
  <div class="carte"><div class="mini">Horodatage</div><div class="kpi" id="horloge">-</div></div>
  <div class="carte"><div class="mini">Latence sondage</div><div class="kpi" id="latence">-</div></div>
  <div class="carte"><div class="mini">Repli sans cle</div><div class="kpi" id="repli">-</div></div>
</div>
<table><thead><tr><th>Fournisseur</th><th>Modele</th><th>Etat</th><th>Variable d environnement</th></tr></thead>
<tbody id="fournisseurs"><tr><td colspan="4" class="mini">en attente...</td></tr></tbody></table>

<h2>Blindage V17 (boucle fermee)</h2>
<div class="grille">
  <div class="carte"><div class="mini">Etat</div><div class="kpi" id="b-etat">-</div></div>
  <div class="carte"><div class="mini">Appels IA traites</div><div class="kpi" id="b-appels">0</div></div>
  <div class="carte"><div class="mini">Entrees refusees</div><div class="kpi" id="b-refus">0</div></div>
  <div class="carte"><div class="mini">Pannes interceptees</div><div class="kpi" id="b-erreurs">0</div></div>
  <div class="carte"><div class="mini">Latence IA moyenne</div><div class="kpi" id="b-latence">0 ms</div></div>
</div>

<h2>Monetisation V8</h2>
<div class="grille">
  <div class="carte"><div class="mini">Mes revenus (FCFA)</div><div class="kpi" id="mon-rev">-</div><div class="mini">dossiers : <b id="mon-rev-nb">0</b></div></div>
</div>

<h2>Tableau comparatif des fournisseurs d IA</h2>
<table><thead><tr><th>Fournisseur</th><th>Modele</th><th>Prix entree</th><th>Prix sortie</th><th>Limites</th><th>Contexte</th></tr></thead>
<tbody id="comparatif"><tr><td colspan="6" class="mini">en attente...</td></tr></tbody></table>
<div class="mini" id="comparatif-note" style="margin-top:6px"></div>

<h2>Connecteurs temps reel V16</h2>
<div class="carte mini" id="conn-v16">en attente...</div>
</main>
<script>
"use strict";
function $(id){return document.getElementById(id);}
function esc(s){var d=document.createElement("div");d.textContent=String(s==null?"":s);return d.innerHTML;}
function txt(id,v){var e=$(id);if(e)e.textContent=v;}
function rafraichir(){
  var t0=performance.now();
  Promise.all([
    fetch("/api/etat-ia",{cache:"no-store"}).then(function(r){return r.json();}),
    fetch("/api/etat-ia-live",{cache:"no-store"}).then(function(r){return r.json();}),
    fetch("/api/blindage",{cache:"no-store"}).then(function(r){return r.json();}),
    fetch("/api/monetisation",{cache:"no-store"}).then(function(r){return r.json();}).catch(function(){return null;}),
    fetch("/api/comparatif-ia",{cache:"no-store"}).then(function(r){return r.json();}).catch(function(){return null;}),
    fetch("/api/connecteurs",{cache:"no-store"}).then(function(r){return r.json();}).catch(function(){return null;})
  ]).then(function(res){
    txt("latence",Math.round(performance.now()-t0)+" ms");
    var etat=res[0]||{},live=res[1]||{},bl=res[2]||{};
    var tb=$("fournisseurs");tb.innerHTML="";
    (etat.fournisseurs||[]).forEach(function(f){
      var tr=document.createElement("tr");
      tr.innerHTML="<td>"+esc(f.nom)+"</td><td>"+esc(f.modele)+"</td><td class='"+(f.actif?"actif":"inactif")+"'>"+(f.actif?"ACTIF":"inactif")+"</td><td>"+esc(f.variable)+"</td>";
      tb.appendChild(tr);});
    txt("repli",etat.repli_sans_cle?"actif":"-");
    txt("horloge",live.horodatage||new Date().toLocaleTimeString());
    var c=bl.compteur||{};
    txt("b-etat",bl.blindage||"-");
    txt("b-appels",c.appels_ia||0);
    txt("b-refus",c.entrees_refusees||0);
    txt("b-erreurs",c.erreurs_interceptees||0);
    txt("b-latence",(c.latence_moyenne_ms||0)+" ms");
    var mon=res[3];
    if(mon){var rev=mon.mes_revenus||mon.revenus||{};
      txt("mon-rev",rev.montant_fcfa!=null?rev.montant_fcfa:"-");
      txt("mon-rev-nb",rev.nombre||0);}
    var cmp=res[4];
    if(cmp){var tbc=$("comparatif");tbc.innerHTML="";
      (cmp.fournisseurs||[]).forEach(function(f){
        var tr=document.createElement("tr");
        tr.innerHTML="<td>"+esc(f.nom)+"</td><td>"+esc(f.modele)+"</td><td>"+esc(f.prix_entree)+"</td><td>"+esc(f.prix_sortie)+"</td><td>"+esc(f.limites)+"</td><td>"+esc(f.contexte)+"</td>";
        tbc.appendChild(tr);});
      txt("comparatif-note",cmp.note||"");}
    var cn=res[5];
    if(cn){var dv=$("conn-v16");dv.textContent="";
      (cn.connecteurs||[]).forEach(function(k){
        var p=document.createElement("p");
        p.innerHTML="<b>"+esc(k.nom)+"</b> - "+esc(k.etat)+(k.appelable?" (appelable)":"");
        dv.appendChild(p);});}
  }).catch(function(){txt("horloge","sonde indisponible");});
}
rafraichir();
setInterval(rafraichir,5000);
</script>
</body>
</html>
"""

_V17_PAGE = _V17_PAGE.replace("__VERSION__", _V17_VERSION)


def _v17_page_tableau():
    """Tableau de bord IA EN DIRECT (V17) - remplace la vue /tableau-ia."""
    return _V17_PAGE, 200


def _v17_repondre_blindage():
    """Etat du blindage V17 - compteurs et garanties, aucune cle exposee."""
    with _V17_VERROU:
        etat = dict(_V17_COMPTEUR)
    return jsonify({
        "version": _V17_VERSION,
        "blindage": "actif",
        "cliquabilite": _V17_CLIC_INSTALLEE,
        "tableau_bord": "/tableau-ia",
        "compteur": etat,
        "entetes_securite": ["X-Content-Type-Options", "X-Frame-Options",
                             "Referrer-Policy", "Permissions-Policy",
                             "Content-Security-Policy"],
        "longueur_max_question": _V17_LONGUEUR_MAX,
    })


_V17_CLIC_INSTALLEE = _v17_injecter_cliquabilite()
app.view_functions["v14_tableau_ia"] = _v17_page_tableau
_V17_ROUTE_BLINDAGE = "/api/blindage" in {r.rule for r in app.url_map.iter_rules()}
if not _V17_ROUTE_BLINDAGE:
    app.add_url_rule("/api/blindage", "v17_blindage", _v17_repondre_blindage,
                     methods=["GET"])
try:
    LOGGER.info("V17 : blindage renforce + cliquabilite + tableau de bord en direct"
                " - /api/blindage, /tableau-ia (%s)", _V17_VERSION)
except Exception:
    pass
# ======================= FIN DU BLOC V17 =====================================

if globals().get("_TOUTBOT_DIFFERE_TACHES"):
    try:
        _v10_demarrer_taches()
    except Exception:
        pass

if globals().get("_TOUTBOT_EXEC_MAIN"):
    sys.exit(main())
