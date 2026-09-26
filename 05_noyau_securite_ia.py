# =============================================================================
# NoyauSecuriteIA : noyau de securite dedie aux IA de l application
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 14775 a 14910
# Code extrait tel quel (aucune modification).
# =============================================================================

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
