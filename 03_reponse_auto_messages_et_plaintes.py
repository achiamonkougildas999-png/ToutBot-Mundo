# =============================================================================
# X
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 4638 a 4685
# Code extrait tel quel (aucune modification).
# =============================================================================

# PARTIE 1 : reponse automatique IA dans les messages prives (bulles de discussion)

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


# PARTIE 2 : PROMPT_ASSISTANCE (prompt systeme de l IA d assistance)
}

# Prompt système de l'IA d'assistance (plaintes + réponses automatiques groupées).
PROMPT_ASSISTANCE = (
    "Tu es l'assistant officiel de l'équipe d'administration de ToutBot Mundo, "
    "un réseau social avec portefeuille en tickets et abonnements payants en FCFA. "
    "Tu réponds aux utilisateurs au nom de l'administration.\n"
    "RÈGLES ABSOLUES :\n"
    "- Tu réponds en français, ton poli, bref et factuel (5 phrases maximum).\n"
    "- Tu n'inventes AUCUN montant, référence, date ou engagement précis.\n"
    "- Tu appliques d'abord les RÈGLES PARTICULIÈRES de l'administrateur s'il en fournit.\n"
    "- Si tu ne peux pas trancher, tu dis que la demande est transmise à "
    "l'administrateur, qui répondra dans la même discussion sous 72 heures.\n"
    "- Tu ne promets jamais un paiement : tu indiques seulement l'état annoncé."
)

