# -*- coding: utf-8 -*-
# =============================================================================
# 🛰️ SURVEILLANT D'ACTUALITÉS — 195 PAYS (RSS temps réel, sans clé API)
# -----------------------------------------------------------------------------
# NOUVEAU CODE v2 pour ToutBot Mundo. Tourne en cron / workflow :
#   python surveillant_actualites.py                    → tout le monde, 3 titres/pays
#   python surveillant_actualites.py --pays SN,FR,IN    → pays choisis
#   python surveillant_actualites.py --flash            → uniquement gros titres
#
# Sorties :
#   • digeste/ACTUALITES_YYYY-MM-JD.json  (données brutes horodatées)
#   • digeste/digeste_YYYY-MM-DD.md       (lisible, prêt à envoyer par e-mail)
#   • table SQLite surveillance.db (historique consultable depuis l'app)
#
# Cadence : 1 requête par pays avec délai aléatoire 2-4 s (Google News RSS
# tolère ce rythme ; ne PAS réduire les délais).
# =============================================================================
from __future__ import annotations
import argparse
import json
import os
import random
import sqlite3
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from pays_agences_rss import PAYS, flux_pays, total_pays

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digeste")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "surveillance.db")


def _get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ToutBotMundo-Surveil/2.0)",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as rep:
        return rep.read().decode("utf-8", "replace")


def titres_pays(code: str, limite: int = 3) -> list[dict]:
    """Titres de l'édition nationale ; bascule sur hl=en si flux vide."""
    nom, gl, hl = PAYS[code]
    url = flux_pays(code)
    try:
        items = _parser(url)
        if not items:  # couple gl:hl rarement vide → repli anglais
            url = (f"https://news.google.com/rss?hl=en&gl={gl}&ceid={gl}:en")
            items = _parser(url)
        return [{"pays": nom, "iso": code, "titre": t, "lien": l, "date": d}
                for t, l, d in items[:limite]]
    except Exception as exc:
        return [{"pays": nom, "iso": code, "titre": f"ERREUR: {type(exc).__name__}: {exc}",
                 "lien": "", "date": ""}]


def _parser(url: str) -> list[tuple[str, str, str]]:
    brut = _get(url)
    racine = ET.fromstring(brut)
    sortie = []
    for item in racine.iter("item"):
        sortie.append(((item.findtext("title") or "").strip(),
                       (item.findtext("link") or "").strip(),
                       (item.findtext("pubDate") or "").strip()))
    return sortie


def init_db() -> None:
    with sqlite3.connect(DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS titres (
            id INTEGER PRIMARY KEY AUTOINCREMENT, iso TEXT, pays TEXT,
            titre TEXT, lien TEXT, date_rss TEXT, collecte_le TEXT)""")


def enregistrer(lignes: list[dict]) -> None:
    maintenant = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(DB) as conn:
        conn.executemany(
            "INSERT INTO titres (iso, pays, titre, lien, date_rss, collecte_le) VALUES (?,?,?,?,?,?)",
            [(l["iso"], l["pays"], l["titre"], l["lien"], l["date"], maintenant) for l in lignes])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pays", default="", help="codes ISO séparés par virgules (défaut : tous)")
    ap.add_argument("--limite", type=int, default=3, help="titres par pays")
    ap.add_argument("--flash", action="store_true", help="1 seul titre par pays (flash)")
    args = ap.parse_args()
    os.makedirs(DOSSIER, exist_ok=True)
    init_db()

    codes = (args.pays.split(",") if args.pays else list(PAYS.keys()))
    codes = [c.strip().upper() for c in codes if c.strip().upper() in PAYS]
    limite = 1 if args.flash else args.limite
    print(f"Surveillance : {len(codes)} pays × {limite} titre(s) — démarrage {datetime.now(timezone.utc).isoformat()}")

    tout: list[dict] = []
    for i, code in enumerate(codes, 1):
        lignes = titres_pays(code, limite)
        tout.extend(lignes)
        enregistrer(lignes)
        if i % 25 == 0:
            print(f"  … {i}/{len(codes)} pays traités")
        time.sleep(random.uniform(2.0, 4.0))

    jour = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(os.path.join(DOSSIER, f"ACTUALITES_{jour}_{int(time.time())}.json"), "w", encoding="utf-8") as f:
        json.dump(tout, f, ensure_ascii=False, indent=1)

    md = [f"# 🌍 Digeste mondial — {jour}", f"",
          f"{len(tout)} titres collectés sur {len(codes)} pays (Google News RSS, sans clé API).", ""]
    for l in tout:
        md.append(f"- **{l['pays']}** — [{l['titre']}]({l['lien']})" if l["lien"] else f"- **{l['pays']}** — {l['titre']}")
    with open(os.path.join(DOSSIER, f"digeste_{jour}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"✔ Terminé : {len(tout)} titres → digeste_{jour}.md")


if __name__ == "__main__":
    main()
