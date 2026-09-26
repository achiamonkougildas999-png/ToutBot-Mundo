# =============================================================================
# envoi_auto_pour + derniere_auto_reply : couverture des envois groupes a reponse auto et anti-boucle 30 min
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 4930 a 4990
# Code extrait tel quel (aucune modification).
# =============================================================================

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
