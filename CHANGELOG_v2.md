# 📋 JOURNAL v2 — CE QUI A CHANGÉ DEPUIS LA v1

## v1 (package initial)
Extraction brute du code IA de ToutBot_Mundo.py : 6 dossiers, 14 fichiers Python
(2 632 lignes), code source repris tel quel avec numéros de lignes d'origine.

## v2 (cette version) — 5 ajouts + 3 correctifs

| Élément | Dossier | Description |
|---|---|---|
| **Recherche sans clé API** | `MOTEUR_SANS_CLE_API/` | Nouveau module : DuckDuckGo HTML et Bing pilotés par **Playwright**, secours **Selenium**, filet **Google News RSS** (sans navigateur). Limitation de cadence aléatoire 8-15 s, rotation de 5 User-Agents, avertissement explicite sur les CGU des moteurs et les CAPTCHA. Chaîne de repli automatique moteur par moteur. + `test_recherche.py` (test réel). |
| **Surveillance 195 pays** | `SUIVI_ACTUALITES_195_PAYS/` | `pays_agences_rss.py` : mapping pays → édition nationale Google News RSS (format `hl`/`gl`/`ceid` vérifié). `surveillant_actualites.py` : collecte programmable (cron), sorties JSON + digeste Markdown + historique SQLite `surveillance.db`, repli automatique `hl=en` si un flux national est vide. |
| **Workflow e-mail quotidien** | (automatisation Genspark) | Digeste mondial envoyé chaque jour à 05h30 UTC à sowbirich1212@gmail.com — créé via l'outil d'automatisation, à activer depuis la carte. |
| **Prompt admin enrichi v2.0** | `IA3_ia_personnelle_admin/prompt_admin.py` | Remplace/complète `PROMPT_ASSISTANCE` (lignes 4345-4356) : persona « Mundo », 7 règles absolues (zéro invention, confidentialité, aucune promesse financière), 3 variantes branchables sur `interroger_llm()` : personnelle, analyse tableau JSON, rapport sécurité. |
| **Correctif SEO** | `STOCKAGE_TEMPS_REEL_EN_LIGNE/03_sitemap_robots.py` | Ce qui manquait dans l'app : `/sitemap.xml` + `/robots.txt` + en-tête `X-Robots-Tag: noindex` sur tous les espaces privés (messages, chat IA, portefeuille, admin…). |
| **Guide de mise en ligne** | `GUIDE_MISE_EN_LIGNE.md` | Pas à pas : base PostgreSQL en ligne (Neon/Supabase), hébergement Render ou VPS, variables d'environnement, HTTPS, WebSocket derrière nginx, Search Console/Bing, SMTP Gmail, crons, checklist sécurité. |

## Correctifs internes appliqués à la v2 avant livraison
- Import `typing` manquant dans `pays_agences_rss.py` (aurait provoqué un `NameError`).
- URL d'agence de presse non vérifiée retirée du mapping (seule l'URL RSS d'ONU News, vérifiable, est conservée).
- Ligne de code morte supprimée dans `surveillant_actualites.py`.
- Compilation (`py_compile`) et tests réels exécutés — résultats dans le LISEZMOI et le message de livraison.
