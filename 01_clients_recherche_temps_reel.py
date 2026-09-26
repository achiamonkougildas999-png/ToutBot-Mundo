# =============================================================================
# MOTEURS DE RECHERCHE TEMPS REEL : WikipediaClient, DuckDuckGoClient, GoogleNewsClient, SearxngClient, MoteurRecherche (multi-source), interroger_ia (synthese extractive ancree sur le reel)
# Source : ToutBot_Mundo.py (mono-fichier fusion V7+V8) — lignes 2057 a 2341
# Code extrait tel quel (aucune modification).
# =============================================================================

def _http_get(url: str, timeout: int = 10, accept: str = "*/*",
              headers: Optional[Dict[str, str]] = None) -> bytes:
    entetes = {"User-Agent": "ToutBotMundo/1.0 (+prototype)", "Accept": accept}
    if headers:
        entetes.update(headers)
    req = urllib.request.Request(url, headers=entetes)
    with urllib.request.urlopen(req, timeout=timeout) as reponse:
        return reponse.read()

def _nettoyer_texte(brut: Any, limite: int = 400) -> str:
    texte = re.sub(r"<[^>]+>", " ", str(brut or ""))
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte[:limite]

def _resultat(source: str, titre: Any, url: Any, extrait: Any, date: Any = "") -> Dict[str, str]:
    return {
        "source": source,
        "titre": _nettoyer_texte(titre, 220),
        "url": str(url or "").strip(),
        "extrait": _nettoyer_texte(extrait),
        "date": str(date or "").strip(),
    }

class WikipediaClient:
    """Wikipédia — API publique gratuite, aucune clé."""

    ENDPOINT = "https://fr.wikipedia.org/w/api.php"
    TIMEOUT = 10

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {
            "action": "query", "list": "search", "srsearch": requete,
            "format": "json", "srlimit": max_resultats, "utf8": "1",
        }
        try:
            brut = _http_get(f"{cls.ENDPOINT}?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/json")
            data = json.loads(brut.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Wikipédia indisponible : %s", exc)
            return []
        sortie = []
        for item in ((data.get("query") or {}).get("search") or [])[:max_resultats]:
            titre = item.get("title") or ""
            sortie.append(_resultat(
                "Wikipédia", titre,
                "https://fr.wikipedia.org/wiki/" + urllib.parse.quote(str(titre).replace(" ", "_")),
                item.get("snippet"), item.get("timestamp"),
            ))
        return sortie

class DuckDuckGoClient:
    """DuckDuckGo Instant Answer — API publique gratuite, aucune clé."""

    ENDPOINT = "https://api.duckduckgo.com/"
    TIMEOUT = 10

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {"q": requete, "format": "json", "no_html": "1", "no_redirect": "1"}
        try:
            brut = _http_get(f"{cls.ENDPOINT}?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/json")
            data = json.loads(brut.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("DuckDuckGo indisponible : %s", exc)
            return []
        sortie: List[Dict[str, str]] = []
        if data.get("AbstractText"):
            sortie.append(_resultat("DuckDuckGo", data.get("Heading") or requete,
                                    data.get("AbstractURL"), data.get("AbstractText")))
        for sujet in (data.get("RelatedTopics") or []):
            if len(sortie) >= max_resultats:
                break
            if isinstance(sujet, dict) and sujet.get("Text"):
                sortie.append(_resultat("DuckDuckGo", sujet.get("Text"), sujet.get("FirstURL"),
                                        sujet.get("Text")))
        return sortie[:max_resultats]

class GoogleNewsClient:
    """Google News — flux RSS public, aucune clé, aucune inscription."""

    ENDPOINT = "https://news.google.com/rss/search"
    TIMEOUT = 10

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {"q": requete, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
        try:
            brut = _http_get(f"{cls.ENDPOINT}?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/rss+xml")
            racine = ET.fromstring(brut)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Google News indisponible : %s", exc)
            return []
        sortie = []
        for item in racine.iter("item"):
            titre = item.findtext("title")
            if not titre:
                continue
            src = item.find("source")
            sortie.append(_resultat("Google News", titre, item.findtext("link"),
                                    item.findtext("description"), item.findtext("pubDate")))
            sortie[-1]["source"] = f"Google News / {src.text}" if src is not None and src.text else "Google News"
            if len(sortie) >= max_resultats:
                break
        return sortie

class SearxngClient:
    """SearXNG — métamoteur libre. Les instances publiques ferment souvent l'API
    JSON : on essaie la vôtre (SEARXNG_URL) puis une liste d'instances connues."""

    INSTANCES = ["https://searx.be", "https://baresearch.org", "https://search.disroot.org",
                 "https://priv.space", "https://searx.tiekoetter.com"]
    TIMEOUT = 10

    @classmethod
    def _instances(cls) -> List[str]:
        privee = (CONFIG.get("SEARXNG_URL") or "").strip().rstrip("/")
        return ([privee] if privee else []) + list(cls.INSTANCES)

    @classmethod
    def chercher(cls, requete: str, max_resultats: int = 5) -> List[Dict[str, str]]:
        params = {"q": requete, "format": "json", "language": "fr-FR", "safesearch": "0"}
        for base in cls._instances():
            try:
                brut = _http_get(f"{base}/search?{urllib.parse.urlencode(params)}", cls.TIMEOUT, "application/json")
                data = json.loads(brut.decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                LOGGER.info("SearXNG %s indisponible : %s", base, exc)
                continue
            sortie = []
            for res in (data.get("results") or [])[:max_resultats]:
                sortie.append(_resultat("SearXNG", res.get("title"), res.get("url"),
                                        res.get("content") or res.get("snippet"), res.get("publishedDate")))
            if sortie:
                return sortie
        return []

class MoteurRecherche:
    """Agrège les moteurs gratuits, déduplique et met en cache.

    HONNÊTETÉ : aucun moteur ne peut être « tous les moteurs du monde ». Ce
    module interroge 4 sources gratuites. Chaque résultat porte son moteur
    d'origine ET son lien : l'interface n'affiche jamais un résultat sans URL.
    """

    MOTEURS = ("searxng", "google_news", "wikipedia", "duckduckgo")

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._cache: Dict[str, Any] = {}

    def chercher(self, requete: str, max_par_moteur: int = 5) -> Dict[str, Any]:
        cle = f"{requete}|{max_par_moteur}"
        entree = self._cache.get(cle)
        if entree and time.time() - entree["at"] < self.ttl:
            reponse = dict(entree["value"])
            reponse["cache"] = "hit"
            return reponse

        lots = {
            "wikipedia": WikipediaClient.chercher(requete, max_par_moteur),
            "google_news": GoogleNewsClient.chercher(requete, max_par_moteur),
            "duckduckgo": DuckDuckGoClient.chercher(requete, max_par_moteur),
            "searxng": SearxngClient.chercher(requete, max_par_moteur),
        }
        fusion: List[Dict[str, str]] = []
        vus = set()
        for moteur in ("wikipedia", "google_news", "duckduckgo", "searxng"):
            for item in lots.get(moteur, []):
                marque = (item.get("titre") or "").lower()[:80] or (item.get("url") or "")[:80]
                if not marque or marque in vus:
                    continue
                vus.add(marque)
                fusion.append(item)
        valeur = {
            "requete": requete,
            "collecte_le": maintenant(),
            "compte": {m: len(lots.get(m, [])) for m in self.MOTEURS},
            "total": len(fusion),
            "resultats": fusion,
            "cache": "miss",
        }
        self._cache[cle] = {"at": time.time(), "value": valeur}
        return valeur

    def bloc_memoire(self, requete: str, max_par_moteur: int = 5, budget: Optional[int] = None) -> str:
        """Bloc texte prêt à être injecté dans le prompt système de l'IA.
        Chaque ligne porte son moteur et son lien : l'IA ne peut rien citer
        qui ne soit pas vérifiable."""
        budget = budget or CONFIG["AI_MAX_WEB_TOTAL"]
        data = self.chercher(requete, max_par_moteur)
        c = data["compte"]
        lignes = [
            "=== FLUX TEMPS RÉEL (résultats réellement collectés) ===",
            f"Sujet : {requete}",
            f"Collecté le : {data['collecte_le']}",
            f"Moteurs : SearXNG={c['searxng']} | Google News={c['google_news']} |"
            f" Wikipédia={c['wikipedia']} | DuckDuckGo={c['duckduckgo']}",
            "",
        ]
        utilise = sum(len(x) for x in lignes)
        for i, item in enumerate(data["resultats"], 1):
            bloc = (f"[{i}] ({item['source']})\n"
                    f"    Titre : {item['titre']}\n"
                    f"    Date  : {item['date'] or 'non fournie'}\n"
                    f"    Lien  : {item['url']}\n"
                    f"    Info  : {item['extrait'] or '(pas de résumé)'}")
            if utilise + len(bloc) > budget:
                break
            lignes.append(bloc)
            utilise += len(bloc)
        if not data["resultats"]:
            lignes.append("AUCUN RÉSULTAT RÉEL COLLECTÉ.")
        return "\n".join(lignes)

def formater_resultats_html(data: Dict[str, Any]) -> str:
    """Rendu HTML des résultats réels (utilisé aussi si l'IA est muette)."""
    if not data.get("resultats"):
        return "<p class='muet'>Aucun résultat réel collecté sur les moteurs gratuits pour cette requête.</p>"
    morceaux = ["<ol class='resultats'>"]
    for item in data["resultats"]:
        lien = urllib.parse.quote(item["url"], safe="") if item.get("url") else ""
        morceaux.append(
            "<li>"
            f"<a href='{item['url']}' target='_blank' rel='noopener'>{item['titre']}</a>"
            f"<span class='badge'>{item['source']}</span>"
            f"<p class='muet'>{item['extrait'] or '(pas de résumé)'}</p>"
            f"<p class='lien'>{item['url']} — <a href='/aller?u={lien}'>accès</a></p>"
            "</li>"
        )
    morceaux.append("</ol>")
    return "".join(morceaux)

# =============================================================================
# 🤖 IA ANCRÉE SUR LE RÉEL — elle répond ou elle avoue ne rien trouver
# =============================================================================
PROMPT_SYSTEME = """🏛️ CORTEX DE ZEUS V4 — POSTURE ABSOLUE (ToutBot Mundo)

Tu disposes de deux couches :
  1) Ton savoir généraliste (sciences, droit, histoire, techniques).
  2) Un BLOC TEMPS RÉEL contenant des résultats réellement collectés sur des
     moteurs de recherche gratuits, chacun accompagné de son lien.

RÈGLES ABSOLUES, NON NÉGOCIABLES :
  • Tu n'inventes RIEN : aucun fait, aucun chiffre, aucun nom, aucune date,
    aucun signe, aucun symbole, aucune publication.
  • Tout ce que tu affirmes sur l'actualité doit provenir du BLOC TEMPS RÉEL
    et être suivi de son lien entre parenthèses.
  • Si le BLOC TEMPS RÉEL est vide ou insuffisant, tu réponds de manière
    explicite : « Aucune publication réelle trouvée sur ce point » et tu
    t'arrêtes là. Tu ne combles JAMAIS un trou avec une supposition.
  • Tu distingues toujours : [VÉRIFIÉ — source] et [CONNAISSANCE GÉNÉRALE].
  • Réponse en français, ton clair, concis, sans emphase inutile.
"""

def interroger_ia(question: str, bloc_reel: str) -> str:
    """Appelle le moteur d'IA (Pollinations). Lève une erreur si muet."""
    corps = {
        "model": CONFIG["AI_MODEL"],
        "messages": [
            {"role": "system", "content": PROMPT_SYSTEME},
            {"role": "user", "content": f"BLOC TEMPS RÉEL :\n{bloc_reel}\n\nQUESTION : {question}"},
        ],
    }
    entetes = {"Content-Type": "application/json", "User-Agent": "ToutBot-Mundo/1.0"}
    if CONFIG["POLLINATIONS_KEY"]:
        entetes["Authorization"] = f"Bearer {CONFIG['POLLINATIONS_KEY']}"
    req = urllib.request.Request(CONFIG["AI_ENDPOINT"],
                                 data=json.dumps(corps).encode("utf-8"),
                                 headers=entetes, method="POST")
    with urllib.request.urlopen(req, timeout=CONFIG["AI_TIMEOUT"]) as reponse:
        brut = reponse.read().decode("utf-8", "replace")
    try:
        data = json.loads(brut)
        if isinstance(data, dict) and data.get("choices"):
            return str(data["choices"][0].get("message", {}).get("content") or "").strip()
        if isinstance(data, dict):
            return str(data.get("content") or data.get("text") or "").strip()
    except ValueError:
        pass
    return brut.strip()

MOTEUR = MoteurRecherche()

# =============================================================================
