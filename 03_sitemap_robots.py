# -*- coding: utf-8 -*-
# =============================================================================
# 🗺️ SITEMAP.XML + ROBOTS.TXT — CORRECTIF SEO (ce qui manquait dans l'app)
# -----------------------------------------------------------------------------
# CORRECTIF v2 : ToutBot_Mundo.py n'expose aucun sitemap/robots. Ce blueprint
# s'ajoute à l'application pour aider les moteurs à indexer UNIQUEMENT les
# pages publiques, et interdire l'indexation des bulles de discussion, du
# portefeuille, du tableau admin et des IA (données privées — à ne jamais exposer).
#
# Branchement : à la fin de ToutBot_Mundo.py, avant app.run :
#     from extraction.STOCKAGE_TEMPS_REEL_EN_LIGNE.sitemap_robots import enregistrer_sitemap
#     enregistrer_sitemap(app, domaine="https://votre-domaine.com")
# =============================================================================
from __future__ import annotations
import time


def enregistrer_sitemap(app, domaine: str) -> None:
    """Ajoute /sitemap.xml, /robots.txt et l'en-tête noindex sur les pages privées."""
    d = domaine.rstrip("/")

    # Pages PUBLIQUES autorisées à l'indexation (aucune page connectée, aucun chat)
    publiques = ["/", "/tarifs", "/cgu", "/confidentialite", "/inscription", "/connexion"]

    @app.route("/sitemap.xml", methods=["GET"])
    def _sitemap():  # type: ignore[no-redef]
        aujourdhui = time.strftime("%Y-%m-%d")
        lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
                  '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for p in publiques:
            lignes.append(f"  <url><loc>{d}{p}</loc><lastmod>{aujourdhui}</lastmod>"
                          "<changefreq>daily</changefreq><priority>0.8</priority></url>")
        lignes.append("</urlset>")
        reponse = app.response_class("\n".join(lignes), mimetype="application/xml")
        reponse.headers["Cache-Control"] = "public, max-age=3600"
        return reponse

    @app.route("/robots.txt", methods=["GET"])
    def _robots():  # type: ignore[no-redef]
        corps = "\n".join(
            ["User-agent: *",
             "Allow: /",
             # Espaces privés : JAMAIS indexés (conversations, argent, admin, IA)
             "Disallow: /messages", "Disallow: /chat", "Disallow: /portefeuille",
             "Disallow: /admin", "Disallow: /plaintes", "Disallow: /parametres",
             "Disallow: /mon-historique", "Disallow: /stories", "Disallow: /sondages",
             "Disallow: /boutique", "Disallow: /prets", "Disallow: /live",
             "Disallow: /u/", "Disallow: /p/",
             f"Sitemap: {d}/sitemap.xml"])
        reponse = app.response_class(corps + "\n", mimetype="text/plain")
        reponse.headers["Cache-Control"] = "public, max-age=3600"
        return reponse

    @app.after_request
    def _noindex_prive(reponse):  # type: ignore[no-untyped-def]
        chemin = reponse.request.path if reponse.request else ""
        prive = (chemin.startswith("/messages") or chemin.startswith("/chat")
                 or chemin.startswith("/admin") or chemin.startswith("/portefeuille")
                 or chemin.startswith("/u/") or chemin.startswith("/p/")
                 or chemin.startswith("/parametres") or chemin.startswith("/plaintes")
                 or chemin.startswith("/mon-historique") or chemin.startswith("/stories")
                 or chemin.startswith("/sondages") or chemin.startswith("/boutique")
                 or chemin.startswith("/prets") or chemin.startswith("/live"))
        if prive:
            reponse.headers["X-Robots-Tag"] = "noindex, nofollow"
        return reponse
