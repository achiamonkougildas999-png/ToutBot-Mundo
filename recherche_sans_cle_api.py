# -*- coding: utf-8 -*-
# =============================================================================
# 🔍 RECHERCHE WEB SANS CLÉ API — PLAYWRIGHT (principal) + SELENIUM (secours)
# -----------------------------------------------------------------------------
# NOUVEAU CODE v2 pour ToutBot Mundo — complète MoteurRecherche (lignes 2057-2341
# de ToutBot_Mundo.py) avec une vraie navigation navigateur, sans clé API, sans payer.
#
# ⚠️ AVERTISSEMENTS HONNÊTES :
#   1. Le scraping automatisé de DuckDuckGo/Bing peut contrevenir à leurs
#      conditions d'utilisation et déclencher des CAPTCHA. Ce module limite
#      sa cadence (délai aléatoire 8-15 s) et alterne les User-Agents pour
#      rester raisonnable, mais aucun contournement de CAPTCHA n'est fourni.
#   2. En cas d'échec d'un moteur, on passe au suivant (DuckDuckGo HTML →
#      Bing → Google News RSS). Le RSS Google News ne nécessite AUCUN
#      navigateur (simple URL vérifiée : hl / gl / ceid).
#
# Installation :
#   pip install playwright
#   python -m playwright install chromium
#   # Secours Selenium (optionnel) :  pip install selenium
#
# Utilisation :
#   python recherche_sans_cle_api.py "actualités intelligence artificielle"
# =============================================================================
from __future__ import annotations
import random
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

# --- Rotation d'identités navigateur (User-Agents réels, 2025-2026) ---------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]


def _ua() -> str:
    return random.choice(USER_AGENTS)


class LimiteurCadence:
    """Empêche de frapper les moteurs trop vite : délai aléatoire entre requêtes."""

    def __init__(self, mini: float = 8.0, maxi: float = 15.0) -> None:
        self.mini, self.maxi = mini, maxi
        self._dernier = 0.0

    def attendre(self) -> None:
        maintenant = time.time()
        pause = random.uniform(self.mini, self.maxi)
        ecoule = maintenant - self._dernier
        if ecoule < pause:
            time.sleep(pause - ecoule)
        self._dernier = time.time()


LIMITEUR = LimiteurCadence()


def _url(url: str, timeout: int = 15) -> str:
    """Téléchargement HTTP simple (pour le RSS, aucun navigateur requis)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": _ua(),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as rep:
        return rep.read().decode("utf-8", "replace")


# =============================================================================
# 1) DUCKDUCKGO HTML — via Playwright (aucune clé API, aucune JS lourde)
# =============================================================================
def duckduckgo_playwright(query: str, limite: int = 8) -> List[Dict[str, str]]:
    from playwright.sync_api import sync_playwright  # import paresseux

    resultats: List[Dict[str, str]] = []
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    LIMITEUR.attendre()
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = nav.new_context(user_agent=_ua(), locale="fr-FR")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            blocs = page.locator("div.result")
            n = min(blocs.count(), limite)
            for i in range(n):
                b = blocs.nth(i)
                try:
                    titre = b.locator("a.result__a").inner_text(timeout=2000).strip()
                    lien = b.locator("a.result__a").get_attribute("href", timeout=2000) or ""
                    try:
                        extrait = b.locator("a.result__snippet").inner_text(timeout=1500).strip()
                    except Exception:
                        extrait = ""
                    if lien.startswith("//duckduckgo.com/l/?uddg="):
                        lien = urllib.parse.unquote(lien.split("uddg=")[1].split("&")[0])
                    if titre and lien.startswith("http"):
                        resultats.append({"titre": titre, "url": lien, "extrait": extrait})
                except Exception:
                    continue
        finally:
            ctx.close()
            nav.close()
    return resultats


# =============================================================================
# 2) BING — via Playwright (deuxième chance)
# =============================================================================
def bing_playwright(query: str, limite: int = 8) -> List[Dict[str, str]]:
    from playwright.sync_api import sync_playwright

    resultats: List[Dict[str, str]] = []
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&setlang=fr"
    LIMITEUR.attendre()
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = nav.new_context(user_agent=_ua(), locale="fr-FR")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            blocs = page.locator("li.b_algo")
            n = min(blocs.count(), limite)
            for i in range(n):
                b = blocs.nth(i)
                try:
                    titre = b.locator("h2").inner_text(timeout=2000).strip()
                    lien = b.locator("h2 a").get_attribute("href", timeout=2000) or ""
                    try:
                        extrait = b.locator(".b_caption p").inner_text(timeout=1500).strip()
                    except Exception:
                        extrait = ""
                    if titre and lien.startswith("http"):
                        resultats.append({"titre": titre, "url": lien, "extrait": extrait})
                except Exception:
                    continue
        finally:
            ctx.close()
            nav.close()
    return resultats


# =============================================================================
# 3) SELENIUM — secours si Playwright est indisponible (même logique DDG)
# =============================================================================
def duckduckgo_selenium(query: str, limite: int = 8) -> List[Dict[str, str]]:
    """Nécessite : pip install selenium  (le pilote est téléchargé tout seul)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument(f"user-agent={_ua()}")
    nav = webdriver.Chrome(options=opts)
    resultats: List[Dict[str, str]] = []
    LIMITEUR.attendre()
    try:
        nav.get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
        WebDriverWait(nav, 20).until(
            lambda d: d.find_elements(By.CSS_SELECTOR, "div.result"))
        for b in nav.find_elements(By.CSS_SELECTOR, "div.result")[:limite]:
            try:
                a = b.find_element(By.CSS_SELECTOR, "a.result__a")
                titre = a.text.strip()
                lien = a.get_attribute("href") or ""
                if titre and lien.startswith("http"):
                    resultats.append({"titre": titre, "url": lien, "extrait": ""})
            except Exception:
                continue
    finally:
        nav.quit()
    return resultats


# =============================================================================
# 4) GOOGLE NEWS RSS — 100 % fiable, sans navigateur, sans clé API
#    Format d'URL vérifié : hl (langue), gl (pays), ceid=GL:hl
# =============================================================================
def google_news_rss(query: Optional[str] = None, gl: str = "FR", hl: str = "fr",
                    limite: int = 8) -> List[Dict[str, str]]:
    if query:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
               + f"&hl={hl}&gl={gl}&ceid={gl}:{hl}")
    else:
        url = f"https://news.google.com/rss?hl={hl}&gl={gl}&ceid={gl}:{hl}"
    brut = _url(url)
    racine = ET.fromstring(brut)
    resultats: List[Dict[str, str]] = []
    for item in racine.iter("item"):
        titre = (item.findtext("title") or "").strip()
        lien = (item.findtext("link") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        if titre and lien:
            resultats.append({"titre": titre, "url": lien, "extrait": date})
        if len(resultats) >= limite:
            break
    return resultats


# =============================================================================
# 5) UNIFICATEUR — essaie tout, dans l'ordre, et raconte honnêtement
# =============================================================================
def recherche_unifiee(query: str, limite: int = 6,
                      verbose: bool = True) -> Dict[str, object]:
    essais = (
        ("DuckDuckGo/Playwright", lambda: duckduckgo_playwright(query, limite)),
        ("Bing/Playwright", lambda: bing_playwright(query, limite)),
        ("DuckDuckGo/Selenium", lambda: duckduckgo_selenium(query, limite)),
        ("Google News RSS", lambda: google_news_rss(query, limite=limite)),
    )
    erreurs: List[str] = []
    for nom, fn in essais:
        try:
            res = fn()
            if res:
                if verbose:
                    print(f"[OK] {nom} : {len(res)} résultats")
                return {"moteur": nom, "resultats": res, "erreurs": erreurs}
            erreurs.append(f"{nom} : 0 résultat")
        except Exception as exc:
            erreurs.append(f"{nom} : {type(exc).__name__}: {exc}")
            if verbose:
                print(f"[ÉCHEC] {nom} → {type(exc).__name__}: {exc}")
    return {"moteur": "aucun", "resultats": [], "erreurs": erreurs}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "actualités monde"
    sortie = recherche_unifiee(q)
    for r in sortie["resultats"]:  # type: ignore[attr-defined]
        print("—", r["titre"], "\n  ", r["url"])
