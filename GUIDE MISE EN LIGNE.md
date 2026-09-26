# 🚀 GUIDE DE MISE EN LIGNE PAS À PAS — TOUTBOT MUNDO
*(v2 — rédigé pour votre application mono-fichier Flask + SQLite/PostgreSQL + WebSocket)*

> Objectif : passer de « python ToutBot_Mundo.py en local » à une application
> **publique sur internet**, avec **stockage en ligne temps réel** et pages
> publiques **indexables** par les moteurs de recherche (sans jamais exposer
> les conversations).

---

## ÉTAPE 0 — Ce qu'il faut préparer (15 min)
- [ ] Le fichier `ToutBot_Mundo.py` (déjà prêt : PostgreSQL via `DATABASE_URL`, WebSocket/SSE, SECRET_KEY prod intégrés).
- [ ] Une carte bancaire ou un compte gratuit chez un hébergeur.
- [ ] Un e-mail pour les comptes : Gmail `sowbirich1212@gmail.com` convient.

## ÉTAPE 1 — Créer la base PostgreSQL EN LIGNE (le stockage temps réel)
1. Créez un compte gratuit sur **Neon** (neon.tech) ou **Supabase** (supabase.com).
2. Créez un projet → copiez la **chaîne de connexion** :
   `postgresql://utilisateur:motdepasse@hote/nombase?sslmode=require`
3. C'est cette valeur qui deviendra la variable `DATABASE_URL`.
   → Dès qu'elle est présente, votre app bascule automatiquement de SQLite
   vers PostgreSQL (adaptateur de compatibilité déjà dans le code, lignes 13158-13460). **Aucune migration manuelle** : les tables se créent au premier démarrage.

## ÉTAPE 2 — Héberger l'application (2 options)

### Option A — Render.com (le plus simple, gratuit puis ~7 $/mois)
1. Poussez `ToutBot_Mundo.py` dans un dépôt GitHub privé.
2. Render → **New Web Service** → connectez le dépôt.
3. Réglages :
   - Build : `pip install -r requirements.txt`
   - Start : `gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT ToutBot_Mundo:app`
   - `requirements.txt` = `flask` + `flask-sock` + `psycopg2-binary` + `gunicorn`
4. **Environment** (Render → Environment) :
   - `SECRET_KEY` = `python -c "import secrets;print(secrets.token_hex(32))"` (chez vous, une fois)
   - `DATABASE_URL` = la chaîne de l'étape 1
   - `TOUTBOT_LLM_KEY` = votre clé LLM (Pollinations ou autre, compatible OpenAI)
   - `TOUTBOT_LLM_ENDPOINT` / `TOUTBOT_LLM_MODEL` (facultatifs, défauts déjà bons)
5. Create → l'URL `https://votre-app.onrender.com` est en ligne.

### Option B — VPS (contrôle total, ~5 $/mois)
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv nginx
python3 -m venv ~/env && source ~/env/bin/activate
pip install flask flask-sock psycopg2-binary gunicorn
# si vous utilisez le module Playwright :  playwright install chromium
SECRET_KEY="votre-cle" DATABASE_URL="postgresql://..." \
  gunicorn -w 1 --threads 8 -b 127.0.0.1:8099 ToutBot_Mundo:app
```
Puis nginx en proxy inverse + `sudo certbot --nginx` pour le HTTPS (obligatoire : cookies durcis + WebSocket `wss://`).

## ÉTAPE 3 — Rendre l'app correcte en production
- [ ] `SECRET_KEY` défini → sinon l'app **refuse de démarrer** en prod (c'est voulu, couche V10).
- [ ] `--seed-admin zeusad --telephone 90000000` exécuté **une seule fois** pour créer votre compte.
- [ ] Testez `https://votre-domaine/api/sante` → doit répondre OK.

## ÉTAPE 4 — Le temps réel derrière le proxy
- Render : WebSocket fonctionne nativement.
- nginx : ajoutez dans le `location /` :
  `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`
- Le tableau de bord utilise `/ws/admin` (WebSocket) avec **repli automatique SSE** — rien à coder.

## ÉTAPE 5 — SEO : être présent dans les moteurs de recherche (honnête)
1. Ajoutez le correctif fourni : `STOCKAGE_TEMPS_REEL_EN_LIGNE/03_sitemap_robots.py`
   → expose `/sitemap.xml` + `/robots.txt`, protège les espaces privés (chat, portefeuille, admin) avec `noindex`.
2. **Google Search Console** (search.google.com/search-console) → ajoutez votre domaine → soumettez `https://votre-domaine/sitemap.xml`.
3. **Bing Webmaster Tools** (même démarche, import possible depuis Google).
4. ⚠️ Rappel : seules les pages publiques (accueil, tarifs, CGU) seront indexées — c'est la bonne pratique. Les bulles de discussion et vos IA restent **volontairement** inaccessibles aux moteurs.

## ÉTAPE 6 — E-mails (résumés, alertes 195 pays)
1. Activez la validation en 2 étapes sur le Gmail → créez un **mot de passe d'application**.
2. Variables d'environnement : `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`,
   `SMTP_USER=sowbirich1212@gmail.com`, `SMTP_PASS=<mot de passe d'application>`.
3. L'app disposera alors de l'envoi du résumé du jour (`envoyer_resume_aux_abonnes`, déjà codé).

## ÉTAPE 7 — Tâches programmées (cron)
```bash
crontab -e
# Résumé quotidien 06h00 UTC :
0 6 * * * cd /opt/toutbot && /home/user/env/bin/python -c "from ToutBot_Mundo import generer_resume_du_jour, envoyer_resume_aux_abonnes; envoyer_resume_aux_abonnes(generer_resume_du_jour(force=True)['jour'])"
# Surveillance monde 195 pays, 2 fois/jour :
0 5,17 * * * cd /chemin/extraction/SUIVI_ACTUALITES_195_PAYS && python surveillant_actualites.py
```
*(Un workflow Genspark a également été créé pour la veille e-mail — voir la carte d'activation.)*

## ÉTAPE 8 — Brancher les IA sur la recherche navigateur sans clé API
1. `pip install playwright && python -m playwright install chromium` sur le serveur.
2. Copiez `MOTEUR_SANS_CLE_API/recherche_sans_cle_api.py` à côté de l'app.
3. Dans le tableau admin, l'IA utilise `recherche_unifiee("...")` → DuckDuckGo → Bing → Google News RSS, avec limitation de cadence intégrée.
4. Alternative MCP (pour connecter d'autres agents) : référentiels vérifiés —
   `microsoft/playwright-mcp`, `ChromeDevTools/chrome-devtools-mcp`, `mrkrsl/web-search-mcp`, `searxng/searxng` (tous sur GitHub ; URLs dans le LISEZMOI).

## CHECKLIST SÉCURITÉ FINALE
- [ ] SECRET_KEY en variable d'environnement (jamais dans le code) ✔
- [ ] HTTPS actif (certbot ou Render natif) ✔
- [ ] Sauvegarde quotidienne : `pg_dump` vers un stockage séparé
- [ ] Espace privé en noindex (correctif étape 5) ✔
- [ ] Aucune clé API en dur (vérifié : scan complet du fichier, aucune clé exposée) ✔
