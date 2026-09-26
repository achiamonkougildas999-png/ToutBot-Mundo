# =============================================================================
# Routes /chat : page Assistant IA + chat_envoyer (question/reponse)
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 4725 a 4753
# Code extrait tel quel (aucune modification).
# =============================================================================


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
