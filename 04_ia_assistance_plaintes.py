# =============================================================================
# plainte_creer : reponse IA automatique sur les plaintes (assistance)
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 4686 a 4712
# Code extrait tel quel (aucune modification).
# =============================================================================

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
