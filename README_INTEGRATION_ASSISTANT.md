# ToutBot Mundo — intégration V13 : assistant web dans l'application + tous les IA allumés

> Version applicative : **13.0** (V7 + V8 + V10 + V11 + V12 + **V13**)
> Fichier principal : `ToutBot_Mundo.py` · Application : Flask (mono-fichier) · 100 % texte

---

## 1. Ce qui a été fait

Le bloc autonome `flask_app.py` fourni (Wikipédia + DuckDuckGo + Google News +
synthèse LLM) a été **fusionné à l'intérieur de l'application existante** —
**une seule application Flask**, aucune seconde instance `app = Flask(...)`,
aucune route existante remplacée :

| Élément du bloc fourni | Devenir dans `ToutBot_Mundo.py` |
|---|---|
| `chercher_wikipedia()` | `_v13_chercher_wikipedia()` (préfixe V13, aucune collision) |
| `chercher_duckduckgo()` | `_v13_chercher_duckduckgo()` |
| `chercher_actualites()` | `_v13_chercher_actualites()` (Google News RSS) |
| `synthetiser_llm()` | `_v13_appeler_ia()` — devient une **chaîne** de fournisseurs |
| `@app.route("/")` (page de chat) | déplacé en `GET /assistant` (le fil social `/` est conservé) |
| `@app.route("/poser")` | `POST /api/poser` + alias `POST /poser` |
| `PAGE_HTML` | `_V13_PAGE_ASSISTANT` (page autonome, rendue par `render_template_string`) |

Les trois collecteurs V13 sont **fusionnés avec les moteurs déjà présents** dans
l'application (`MoteurRecherche` : SearXNG, Wikipédia, Google News, DuckDuckGo) :
`_v13_collecter_sources()` interroge les deux familles, déduplique par URL et
renvoie au plus 12 sources réelles, chacune avec son lien.

### Routes nouvelles

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/assistant` | Page autonome de l'assistant (saisie + sources cliquables) |
| `POST` | `/api/poser` | API JSON : `{"question": "…"}` → `{reponse, fournisseur, sources[]}` |
| `POST` | `/poser` | Alias de l'API JSON (le contrôle CSRF est levé **uniquement** pour ce chemin) |
| `GET` | `/api/etat-ia` | État des fournisseurs (booléens seulement, **jamais une clé**) |

Un lien « Assistant IA » est ajouté automatiquement à la navigation du gabarit
`base.html` lorsqu'une ancre compatible (`</nav>`, `</header>` ou `<main`) existe ;
sinon la page reste accessible par `/assistant`.

---

## 2. Tous les IA allumés — la chaîne de fournisseurs

`_v13_appeler_ia()` essaie les moteurs **dans l'ordre** et s'arrête au premier
qui répond. Une clé absente ou une panne fait passer au suivant ; les échecs sont
renvoyés dans `erreurs_ia`.

| Ordre | Fournisseur | Endpoint | Variable de clé | Modèle par défaut |
|---|---|---|---|---|
| 1 | **Mistral** | `api.mistral.ai/v1/chat/completions` | `MISTRAL_API_KEY` | `MISTRAL_MODEL` = `mistral-small-latest` |
| 2 | **Groq** | `api.groq.com/openai/v1/chat/completions` | `GROQ_API_KEY` | `GROQ_MODEL` = `llama-3.3-70b-versatile` |
| 3 | **Gemini** | `generativelanguage.googleapis.com/v1beta/openai/chat/completions` | `GEMINI_API_KEY` | `GEMINI_MODEL` = `gemini-2.5-flash` |
| 4 | LLM déjà branché | `TOUTBOT_LLM_ENDPOINT` | `TOUTBOT_LLM_KEY` (repli `POLLINATIONS_KEY`) | `TOUTBOT_LLM_MODEL` |
| 5 | Repli sans clé | `TOUTBOT_LLM_ENDPOINT` | *aucune* | `TOUTBOT_LLM_MODEL` |

**Clés gratuites** : Mistral → <https://console.mistral.ai> · Groq →
<https://console.groq.com/keys> · Gemini → <https://aistudio.google.com/apikey>.
Aucune clé n'est écrite en dur dans le code : chaque fournisseur est lu dans
l'environnement au moment de l'appel, donc **aucune modification de code n'est
nécessaire pour ajouter, changer ou retirer une clé** — il suffit de redémarrer
l'application.

> Si le modèle Groq `llama-3.3-70b-versatile` est retiré de votre compte, changez
> `GROQ_MODEL` (par exemple `openai/gpt-oss-120b`) : la liste à jour est sur
> <https://console.groq.com/docs/models>.

Pour **couper un fournisseur** : effacez sa variable de clé.
Pour **couper le repli sans clé** : `TOUTBOT_LLM_SANS_CLE=0`.
Pour **connaître l'état réel** après déploiement : `GET /api/etat-ia`.

---

## 3. Mise en service

### 3.1 PythonAnywhere (si vous gardez cette cible)

```bash
pip install --user -r requirements.txt
```

* **Web → WSGI file** : le pointeur ne change pas, l'assistant vit à l'intérieur
  de l'application existante :
  ```python
  import sys
  sys.path.insert(0, "/home/VOTRE_COMPTE/tb")
  from ToutBot_Mundo import app as application
  ```
* Les variables d'environnement (clés IA, `SECRET_KEY`, `TOUTBOT_MODE`…) se
  renseignent dans **Web → Environment variables** — un redémarrage de l'app est
  nécessaire après chaque ajout.
* **Limite du compte gratuit** : les requêtes sortantes passent par le proxy
  PythonAnywhere, qui n'autorise que les domaines en liste blanche. Wikipédia
  fonctionne ; **DuckDuckGo, Google News et les API d'IA peuvent être bloqués**
  (l'application reste utilisable : les collecteurs en panne sont simplement
  ignorés et listés dans `erreurs_ia`). Deux solutions :
  a) passer au plan *Hacker* (accès Internet illimité) ;
  b) héberger la même application sur **Render.com** (gratuit) — voir 3.3.
  Référence : <https://www.pythonanywhere.com/wiki/WhitelistProxy>

### 3.2 Docker / serveur (recommandé en production)

```bash
cp .env.example .env      # puis renseigner SECRET_KEY, DATABASE_URL, les clés IA
docker compose up -d --build
docker compose logs -f web
```

`docker-compose.yml` transmet déjà tout le fichier `.env` au conteneur `web`
(`env_file: [.env]`) : les clés IA sont donc actives dès le redémarrage, sans
toucher au `Dockerfile`, à Gunicorn ou à Nginx. Test rapide après démarrage :

```bash
curl -s https://VOTRE-DOMAINE/api/etat-ia | python -m json.tool
curl -s -X POST https://VOTRE-DOMAINE/api/poser \
     -H 'Content-Type: application/json' \
     -d '{"question":"actualité du jour en Côte d'"'"'Ivoire"}' | python -m json.tool
```

### 3.3 Render.com (alternative gratuite, réseau sortant ouvert)

1. Dépôt Git avec `ToutBot_Mundo.py`, `requirements.txt`, `gunicorn_conf.py`.
2. **Start command** : `gunicorn --config gunicorn_conf.py ToutBot_Mundo:app`
3. **Environment** : ajouter les mêmes variables que `.env` (clés IA comprises).
4. Health check : `/api/sante`.

---

## 4. Application du patch sur votre propre copie

Le fichier `ToutBot_Mundo.py` livré est **déjà patché**. Pour rejouer
l'opération sur une autre copie :

```bash
python patch_v13_toutbot_web.py     # idempotent : relancer ne fait rien
```

Le script crée une sauvegarde `ToutBot_Mundo.py.avant_v13`, insère le bloc V13
juste avant `if __name__ == "__main__":` (donc actif en mode `gunicorn` **et** en
mode `python ToutBot_Mundo.py`) et n'écrit rien si le marqueur `_V13_INTEGRE` est
déjà présent.

## 5. Vérification (recette)

```bash
python recette_v13_assistant.py     # réseau entièrement simulé, aucun appel externe
```

La recette vérifie : présence du bloc et des 4 routes, absence de collision,
page `/assistant` (et son absence de média), `/api/etat-ia` sans fuite de clé,
l'ordre exact de la chaîne **Mistral → Groq → Gemini** (avec pannes simulées),
la robustesse (corps vide, JSON invalide → 400 et jamais 500), l'alias `/poser`
accessible malgré le CSRF, le garde-fou de débit (429), puis la non-régression
(`/`, `/api/sante`, CSRF ailleurs, blocs V11 toujours présents).

## 6. Sécurité

* Aucune clé dans le code, dans les gabarits ou dans les réponses HTTP ;
  `/api/etat-ia` n'expose qu'un booléen `actif`.
* Le contrôle CSRF de l'application reste actif partout : seule la route
  `POST /poser` (et `/api/*`, déjà exempté) en est dispensée, car elle est
  destinée à être appelée en JSON.
* `POST /poser` sans corps, JSON vide ou JSON invalide → **400** explicite.
* Garde-fou de débit en mémoire par IP (`TOUTBOT_V13_MAX_PAR_MINUTE`, 30/minute) ;
  le rideau Nginx (`limit_req`, 240 r/min) reste la première barrière.
* Honnêteté : sans source ni moteur d'IA disponible, la réponse est `null` et les
  sources réelles sont affichées — **rien n'est inventé**.
