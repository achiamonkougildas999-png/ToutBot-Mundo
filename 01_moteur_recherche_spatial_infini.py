# =============================================================================
# MoteurRechercheSpatialInfini : moteur de recherche/indexation personnel de l admin (V15)
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 14895 a 15660
# Code extrait tel quel (aucune modification).
# =============================================================================

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
