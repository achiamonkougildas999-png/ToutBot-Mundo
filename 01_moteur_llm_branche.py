# =============================================================================
# CLIENT LLM PLUGGABLE : interroger_llm (TOUTBOT_LLM_KEY / endpoint OpenAI-compatible) + reponses de repli regles + classer_demande + reponse_ia_assistance
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 2342 a 2420
# Code extrait tel quel (aucune modification).
# =============================================================================

# 🤖 CLIENT LLM PLUGGABLE (clé lue depuis l'environnement, jamais en dur)
# =============================================================================
def interroger_llm(systeme: str, message: str) -> str:
    """Interroge un LLM configurable par variables d'environnement.
    Clé : TOUTBOT_LLM_KEY (repli : POLLINATIONS_KEY). Endpoint et modèle :
    TOUTBOT_LLM_ENDPOINT / TOUTBOT_LLM_MODEL. Renvoie "" si aucune clé n'est
    configurée ou en cas d'échec : l'appelant bascule alors sur la réponse
    de repli fondée sur des règles."""
    endpoint = os.environ.get("TOUTBOT_LLM_ENDPOINT") or CONFIG["AI_ENDPOINT"]
    modele = os.environ.get("TOUTBOT_LLM_MODEL") or CONFIG["AI_MODEL"]
    cle = os.environ.get("TOUTBOT_LLM_KEY") or CONFIG["POLLINATIONS_KEY"]
    if not cle:
        return ""
    corps = {"model": modele, "messages": [
        {"role": "system", "content": systeme},
        {"role": "user", "content": message}]}
    entetes = {"Content-Type": "application/json", "User-Agent": "ToutBot-Mundo/1.0"}
    entetes["Authorization"] = f"Bearer {cle}"
    try:
        req = urllib.request.Request(endpoint, data=json.dumps(corps).encode("utf-8"),
                                     headers=entetes, method="POST")
        with urllib.request.urlopen(req, timeout=CONFIG["AI_TIMEOUT"]) as reponse:
            brut = reponse.read().decode("utf-8", "replace")
        data = json.loads(brut)
        if isinstance(data, dict) and data.get("choices"):
            return str(data["choices"][0].get("message", {}).get("content") or "").strip()
        if isinstance(data, dict):
            return str(data.get("content") or data.get("text") or "").strip()
        return brut.strip()
    except Exception:
        return ""

# Réponses de repli déterministes (aucune invention, aucune clé requise).
REPONSE_REGLE_PAIEMENT = (
    "Bonjour,\n\nVotre demande concerne un paiement : vérifiez l'état de vos écritures "
    "dans le menu Portefeuille. Si un versement est en attente, l'administrateur le "
    "valide après réception sur les numéros Mobile Money affichés sur le fil d'accueil. "
    "Indiquez ici votre référence de paiement : votre demande est transmise et vous "
    "recevrez une réponse sous 72 heures.\n\nL'équipe d'administration — ToutBot Mundo")
REPONSE_REGLE_BLOCAGE = (
    "Bonjour,\n\nVotre demande concerne l'accès à votre compte. Si votre compte est "
    "bloqué, seul l'administrateur peut le réactiver après examen des conditions "
    "d'utilisation. Exposez votre situation ici : votre demande est transmise et vous "
    "recevrez une réponse sous 72 heures.\n\nL'équipe d'administration — ToutBot Mundo")
REPONSE_REGLE_GENERIQUE = (
    "Bonjour,\n\nVotre demande a bien été reçue et est transmise à l'administrateur. "
    "Vous recevrez une réponse dans cette même discussion sous 72 heures. Les "
    "informations sur vos écritures restent visibles dans votre espace Portefeuille.\n\n"
    "L'équipe d'administration — ToutBot Mundo")

def classer_demande(texte: str) -> str:
    t = (texte or "").lower()
    if any(m in t for m in ("paiement", "paye", "payer", "abonnement", "crédit", "credit",
                            "ticket", "portefeuille", "fcfa", "retrait", "montant")):
        return "paiement"
    if any(m in t for m in ("bloqué", "bloque", "blocage", "suspendu", "connexion",
                            "connecter", "mot de passe")):
        return "blocage"
    return "generique"

def reponse_regle(texte: str) -> str:
    cle = classer_demande(texte)
    return {"paiement": REPONSE_REGLE_PAIEMENT, "blocage": REPONSE_REGLE_BLOCAGE,
            "generique": REPONSE_REGLE_GENERIQUE}[cle]

def reponse_ia_assistance(question: str, regles: str = "") -> Tuple[str, str]:
    """Renvoie (réponse, origine) — origine vaut 'ia' (LLM configuré) ou 'regle'
    (repli déterministe sans clé)."""
    systeme = PROMPT_ASSISTANCE
    if (regles or "").strip():
        systeme += "\n\nRÈGLES PARTICULIÈRES DE L'ADMINISTRATEUR (prioritaires) :\n" + regles.strip()[:1500]
    texte = interroger_llm(systeme, (question or "")[:2000])
    if texte:
        return texte, "ia"
    return reponse_regle(question), "regle"

# =============================================================================
# 🔐 SESSION ET CSRF
# =============================================================================
