# =============================================================================
# Analyse IA de la plateforme (second appel interroger_llm)
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 10490 a 10600
# Code extrait tel quel (aucune modification).
# =============================================================================

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
