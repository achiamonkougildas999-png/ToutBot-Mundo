# =============================================================================
# Adaptateur PostgreSQL (DATABASE_URL) : stockage en ligne compatible sqlite3
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 13158 a 13460
# Code extrait tel quel (aucune modification).
# =============================================================================

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
