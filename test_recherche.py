# -*- coding: utf-8 -*-
"""Test RÉEL du module recherche_sans_cle_api.py — sortie honnête, sans simulacre."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from recherche_sans_cle_api import google_news_rss, recherche_unifiee

print("=== TEST 1 : Google News RSS (sans navigateur) ===")
try:
    res = google_news_rss("intelligence artificielle", gl="SN", hl="fr", limite=3)
    for r in res:
        print(" •", r["titre"][:90], "→", r["url"][:70])
    print(f"[RSS] {len(res)} résultat(s)")
except Exception as e:
    print(f"[RSS ÉCHEC] {type(e).__name__}: {e}")

print()
print("=== TEST 2 : recherche_unifiee (Playwright → Bing → Selenium → RSS) ===")
sortie = recherche_unifiee("prix du mil au Sénégal", limite=3, verbose=True)
print("Moteur retenu :", sortie["moteur"])
for r in sortie["resultats"][:3]:  # type: ignore[attr-defined]
    print(" •", r["titre"][:90], "→", r["url"][:70])
if sortie["erreurs"]:
    print("Erreurs rencontrées (transparence) :")
    for e in sortie["erreurs"]:
        print("   -", e)
