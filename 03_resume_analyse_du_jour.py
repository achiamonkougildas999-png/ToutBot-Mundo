# =============================================================================
# ANALYSE/ASSISTANCE TABLEAU DE BORD : resume_du_jour, _synthese_extractive_v7, generer_resume_du_jour (appel LLM), envoyer_resume_aux_abonnes
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 9960 a 10160
# Code extrait tel quel (aucune modification).
# =============================================================================




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
