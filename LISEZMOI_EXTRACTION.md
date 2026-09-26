# 📦 EXTRACTION — CODE IA / AGENTS POUR TOUTBOT MUNDO

Source principale : **ToutBot_Mundo.py** (mono-fichier fusion V7 + V8, ~18 625 lignes, 937 Ko).
Code extrait **tel quel**, aucune modification. Les numéros de lignes indiqués renvoient à ce fichier.
Deux exemplaires du fichier avaient été envoyés : ils ne diffèrent que par la gestion d'erreur de la
page Paramètres (16 lignes de diff). Tout le reste est identique.

---

## 🎯 CE QUE VOULIEZ OBTEINIR ↔ CE QUE LE CODE FAIT DÉJÀ

| Votre demande | Où c'est dans votre code | État |
|---|---|---|
| IA dans les **bulles de discussion** (utilisateurs ↔ admin) | `interroger_llm` + réponse automatique dans `messages_envoyer` | ✅ Déjà codé (lignes 2342-2370, 4640-4685) |
| IA **assistance + analyse du tableau de bord** | `PROMPT_ASSISTANCE`, réponses auto plaintes, `generer_resume_du_jour`, WebSocket `/ws/admin` + SSE | ✅ Déjà codé (lignes 4345-4356, 8305-8610, 9966-10160) |
| **IA personnelle de l'administrateur** | Page `/chat` (Assistant IA) + `MoteurRechercheSpatialInfini` (V12/V15) | ✅ Déjà codé (lignes 4726-4753, 14899-15660) |
| Recherche **temps réel** sans clé API | Wikipedia, DuckDuckGo, Google News, SearXNG (`MoteurRecherche`) | ✅ Déjà codé (lignes 2057-2341) |
| **Stockage en temps réel en ligne** | SQLite local + adaptateur **PostgreSQL** (`DATABASE_URL`) + WebSocket/SSE | ✅ Mécanisme codé ; il reste à HÉBERGER le serveur + une base en ligne (voir ci-dessous) |
| Indexation dans **chaque moteur de recherche** | Rien dans le code (aucun sitemap/robots) | ⚠️ Voir la mise au point honnête ci-dessous |

---

## 🗂️ ARBORESCENCE DU PACKAGE

### IA1_bulles_discussion — IA qui répond dans les bulles (utilisateurs et admin)
| Fichier | Rôle | Lignes source |
|---|---|---|
| `01_moteur_llm_branche.py` | Client LLM branchable : `interroger_llm()` — clé lue dans l'environnement (`TOUTBOT_LLM_KEY`, repli `POLLINATIONS_KEY`), endpoint compatible OpenAI configurable (`TOUTBOT_LLM_ENDPOINT`, défaut `https://text.pollinations.ai/openai`), modèle configurable (`TOUTBOT_LLM_MODEL`). Repli honnête par règles (aucune invention). | 2342-2420 |
| `02_couverture_envoi_auto.py` | `envoi_auto_pour()` + `derniere_auto_reply()` : couvre les envois groupés à réponse auto, anti-boucle 30 minutes. | 4930-4990 |
| `03_reponse_auto_messages_et_plaintes.py` | La réponse automatique de l'IA **dans les bulles de discussion** : quand un utilisateur écrit à l'administrateur, l'IA répond dans la même conversation ; + `PROMPT_ASSISTANCE` (prompt système de l'IA). | 4638-4685, 4345-4356 |
| `04_ia_assistance_plaintes.py` | Réponse IA automatique enregistrée sur chaque plainte (`reponse_ia`). | 4686-4712 |
| `05_page_chat_ia.py` | Routes `/chat` : page « Assistant IA » + `/chat` POST (question → réponse). | 4725-4753 |

### IA2_assistance_analyse_tableau — IA d'assistance et d'analyse du tableau de bord admin
| Fichier | Rôle | Lignes source |
|---|---|---|
| `01_bus_temps_reel_websocket_sse.py` | Le cœur temps réel du tableau de bord : bus d'événements (`_bus_abonner`, `_bus_diffuser`), `evenement_v6`, `instantane_v6`, route `/admin/temps-reel` et flux SSE `/admin/temps-reel/flux`, WebSocket `/ws/admin` réservé aux administrateurs. **C'est le mécanisme « temps réel » de tout le tableau.** | 8305-8610 |
| `02_websocket_admin_handler.py` | Handler WebSocket du tableau de bord (flask-sock, dépendance optionnelle, repli SSE). | 7395-7460 |
| `03_resume_analyse_du_jour.py` | Analyse/assistance : `resume_du_jour`, synthèse extractive ancrée sur les faits, `generer_resume_du_jour` (appel du LLM), `envoyer_resume_aux_abonnes`. | 9960-10160 |
| `04_analyse_ia_alertes.py` | Second usage d'`interroger_llm` : analyse narrative de la plateforme (rapports mensuels). | 10490-10600 |
| `05_noyau_securite_ia.py` | `NoyauSecuriteIA` : bouclier temps réel qui analyse chaque formulaire POSTé (injection SQL, XSS, traversal), liste noire IP, alertes admin cyber + `_generer_rapport_ia`. | 14775-14910 |

### IA3_ia_personnelle_admin — IA personnelle de l'administrateur
| Fichier | Rôle | Lignes source |
|---|---|---|
| `01_moteur_recherche_spatial_infini.py` | `MoteurRechercheSpatialInfini` : moteur **sémantique local** (Levenshtein, index cache, purge nocturne) + **collecte mondiale à la demande** (cache 5 min) — c'est le moteur personnel de l'admin (V12/V15), avec routes `_V12_chat_envoyer`, `_V12_recherche_page`, `_V12_plainte_creer` et interface d'analyse. | 14895-15660 |

### MOTEUR_RECHERCHE_TEMPS_REEL — recherche internet sans clé API (votre exigence « présentiel »)
| Fichier | Rôle | Lignes source |
|---|---|---|
| `01_clients_recherche_temps_reel.py` | `WikipediaClient`, `DuckDuckGoClient`, `GoogleNewsClient`, `SearxngClient`, `MoteurRecherche` (multi-source avec agrégation), `interroger_ia()` (synthèse extractive **ancrée sur le réel** — aucune invention). | 2057-2341 |

### STOCKAGE_TEMPS_REEL_EN_LIGNE — comment tout est stocké et synchronisé
| Fichier | Rôle | Lignes source |
|---|---|---|
| `01_couche_v10_securite.py` | Couche V10 : `SECRET_KEY` obligatoire en production, cookies durcis, journalisation JSON avec request-id. | 12310-12420 |
| `02_adaptateur_postgresql.py` | **Adaptateur de stockage en ligne** : dès que la variable `DATABASE_URL` pointe vers un PostgreSQL hébergé (Supabase, Neon, Railway…), toute l'application bascule de SQLite local vers la base en ligne — sans changer une ligne de code métier. | 13158-13460 |

### MCP_PLAYWRIGHT_SANS_CLE_API — recherche navigateur (Playwright) sans clé API
| Fichier | Rôle |
|---|---|
| `README_WebSearchMCP.md` | Serveur MCP open-source (MIT) de recherche web **sans aucune clé API** : Bing → Brave → DuckDuckGo via des navigateurs **Playwright**, extraction complète des pages, 3 outils (`full-web-search`, `get-web-search-summaries`, `get-single-web-page-content`). C'est exactement le mécanisme « brancher l'IA sur Playwright au lieu de payer une clé API » que vous décrivez. Compatible LM Studio / LibreChat ; modèles recommandés : Qwen3, Gemma 3. |
| `note_agent_195_pays.txt` | Votre note extraite du fichier WEB RECH_260926_025125.txt (recherche Playwright/Selenium sans clé API + suivi des actualités des 195 pays). |

---

## 🔌 COMMENT TOUT SE BRANCHE (vision d'ensemble)

```
 Utilisateur                       Administrateur
     │  bulles /chat                     │  tableau de bord + /chat personnel
     ▼                                   ▼
 messages_envoyer ──► reponse_ia_assistance ──► interroger_llm
 plainte_creer   ──►      │                     │  clé : TOUTBOT_LLM_KEY (env)
                          │                     ▼
                          │             endpoint OpenAI-compatible
                          │             (défaut : text.pollinations.ai)
                          ▼
              MoteurRecherche (Wikipedia/DuckDuckGo/GoogleNews/SearXNG)
              MoteurRechercheSpatialInfini (sémantique local + monde, cache 5 min)
                          │
                          ▼
        SQLite (dev) ──DATABASE_URL──► PostgreSQL en ligne (production)
                          │
                          ▼
        Bus temps réel : WebSocket /ws/admin  +  repli SSE /admin/temps-reel/flux
```

**Variables d'environnement à définir pour activer les IA :**
- `TOUTBOT_LLM_KEY` (ou `POLLINATIONS_KEY`) — la clé du LLM (aucune clé en dur dans le code ✔)
- `TOUTBOT_LLM_ENDPOINT` — endpoint compatible OpenAI (défaut : `https://text.pollinations.ai/openai`)
- `TOUTBOT_LLM_MODEL` — modèle (défaut : `openai`)
- `DATABASE_URL` — pour le stockage en ligne PostgreSQL
- `SECRET_KEY` — obligatoire en production (refus de démarrer sinon)

---

## ⚠️ MISE AU POINT HONNÊTE SUR « STOCKÉ EN TEMPS RÉEL EN LIGNE SUR INTERNET ET DANS CHAQUE MOTEUR DE RECHERCHE »

1. **Stockage en ligne + temps réel : OUI, c'est faisable et le code est prêt.**
   Il suffit d'héberger l'application (Render, Railway, PythonAnywhere, VPS…) et de renseigner
   `DATABASE_URL` vers un PostgreSQL gratuit (Supabase, Neon…). Le temps réel est déjà assuré par
   le WebSocket `/ws/admin` et le flux SSE du tableau de bord. Les conversations restent alors
   disponibles en ligne, en continu, pour l'admin et les utilisateurs.

2. **« Indexé dans chaque moteur de recherche » : NON, aucun code ne peut le garantir.**
   Les moteurs (Google, Bing…) décident seuls de ce qu'ils indexent. On peut seulement les
   *aider* : déployer l'app sur un domaine public, ajouter un `sitemap.xml` + `robots.txt`,
   déclarer le site dans Google Search Console et Bing Webmaster Tools, et ne rendre public
   que ce qui doit l'être (pages d'accueil, tarifs, CGU…).

3. **Attention — ne mettez JAMAIS les bulles de discussion en index public.**
   Les conversations des utilisateurs et celles de l'administrateur sont des données **privées**
   (et souvent personnelles). Les exposer aux moteurs de recherche violerait la vie privée, vos
   propres CGU et des principes de protection des données. La bonne pratique : pages publiques
   indexables (accueil, tarifs,CGU), espace connecté en `noindex`. Le code actuel protège déjà
   ces espaces par authentification — conservez cela.

4. **Secrets :** le scan complet du fichier n'a trouvé **aucune clé ni mot de passe en dur** —
   la clé LLM est lue depuis l'environnement (`TOUTBOT_LLM_KEY`/`POLLINATIONS_KEY`, valeur par
   défaut vide) et le mot de passe admin est défini à l'installation via `--seed-admin`.
   Rien n'a donc eu besoin d'être masqué dans ces extraits.

---

## ▶️ POUR UTILISER CES EXTRAITS
Chaque fichier `.py` est du code Python valide extrait de votre application (avec son en-tête
indiquant la source et les lignes). Pour tout réunir, il suffit de réintégrer les extraits dans
le fichier d'origine — ou plus simplement : **ToutBot_Mundo.py contient déjà tout ce code**, il
n'a pas besoin d'être reconstruit ; ces dossiers servent de carte de lecture et de base pour
faire évoluer chaque IA séparément.

## 📜 LICENCES
- Code de ToutBot_Mundo.py : votre propriété (code applicatif personnel).
- `README_WebSearchMCP.md` : documentation du projet open-source
  [mrkrsl/web-search-mcp](https://github.com/mrkrsl/web-search-mcp) — licence MIT (utilisation,
  modification et redistribution libres, mention de la licence conservée).

---
🆕 **v2** : voir `CHANGELOG_v2.md` (recherche sans clé API Playwright/Selenium, surveillance 195 pays, guide de mise en ligne, prompt admin enrichi, correctif SEO).
