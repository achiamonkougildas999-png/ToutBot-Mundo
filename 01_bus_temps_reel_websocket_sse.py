# =============================================================================
# BUS TEMPS REEL DU TABLEAU DE BORD : _bus_abonner/_bus_diffuser, evenement_v6, instantane_v6, /ws/admin (WebSocket flask-sock) + repli SSE /admin/temps-reel/flux
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 8305 a 8610
# Code extrait tel quel (aucune modification).
# =============================================================================



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
