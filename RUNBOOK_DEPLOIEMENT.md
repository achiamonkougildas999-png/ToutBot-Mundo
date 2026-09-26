# ToutBot Mundo — Runbook d'exploitation (production)

> Version applicative : **10.0** · Base : PostgreSQL (production) / SQLite (développement)
> Serveur d'application : **Gunicorn** (`gthread`) derrière **Nginx** (TLS)

---

## 1. Démarrage local (développement)

```bash
pip install -r requirements.txt
export TOUTBOT_MODE=dev
export TOUTBOT_ADMIN_BOOTSTRAP_TOKEN=$(python -c "import secrets;print(secrets.token_hex(24))")
python ToutBot_Mundo.py --port 8099
# → http://127.0.0.1:8099/admin/inscription   (création du premier administrateur)
```

En mode `dev`, si `SECRET_KEY` est absente, une clé est générée ; **aucun cookie
`Secure`** n'est posé (le HTTPS local n'est pas supposé actif).

## 2. Démarrage en production

```bash
cp .env.example .env       # puis renseigner SECRET_KEY, DATABASE_URL, REDIS_URL…
docker compose up -d --build
docker compose logs -f web
```

Sans `docker`, en direct :

```bash
export TOUTBOT_MODE=production
export SECRET_KEY=$(python -c "import secrets;print(secrets.token_hex(32))")
export DATABASE_URL=postgresql://toutbot:mdp@db:5432/toutbot
export REDIS_URL=redis://cache:6379/0
gunicorn --config gunicorn_conf.py ToutBot_Mundo:app
```

**Fail-fast** : avec `TOUTBOT_MODE=production` et sans `SECRET_KEY`, le processus
**refuse de démarrer** (aucune valeur de secours n'est tolérée).

## 3. Première entrée de l'administrateur

1. Renseigner `TOUTBOT_ADMIN_BOOTSTRAP_TOKEN` dans l'environnement.
2. Ouvrir `/admin/inscription` : pseudo unique (6 lettres) + mot de passe.
3. Cette voie se **ferme définitivement** dès qu'un administrateur existe.
   Toute tentative ultérieure est renvoyée vers `/connexion`.
4. Un jeton d'amorçage erroné entraîne le **bannissement 30 min** de l'IP appelante.

## 4. Sonde de santé / supervision

| Route | Rôle | Code |
|---|---|---|
| `/health` | vivacité + base joignable | 200 / 503 |
| `/ready` | schéma complet + Redis + HTTPS | 200 / 503 |
| `/api/v10/statut` | état public des services | 200 |

Brancher un vérificateur externe (UptimeRobot, Better Stack…) sur `/health`,
et l'orchestrateur (Docker/K8s) sur les deux routes.

## 5. Sauvegardes

* **Automatique** : un fil d'arrière-plan écrit une sauvegarde JSON toutes les
  24 h dans `TOUTBOT_BACKUP_DIR` (défaut `./sauvegardes`), avec rotation à 30 fichiers.
* **Manuelle** : bouton « Sauvegarder maintenant » (palette `Ctrl/⌘+K`) → `POST /admin/v10/sauvegarder`.
* **Restauration** : arrêter le service, restaurer la base, redémarrer. Toute
  restauration doit être consignée dans le journal d'audit.

## 6. Webhooks Mobile Money

| Fournisseur | URL à déclarer | En-tête attendu |
|---|---|---|
| MTN | `https://votre-domaine/webhooks/mtn` | `X-ToutBot-Signature: <hmac-sha256 hex du corps>` |
| Moov | `https://votre-domaine/webhooks/moov` | idem |

* Signature = `HMAC_SHA256(TOUTBOT_WEBHOOK_SECRET_*, corps_brut)`, hexadécimal.
* **Sans secret configuré, aucun webhook n'est accepté** (503 explicite) : l'application
  ne présente jamais un paiement comme vérifié s'il ne l'est pas.
* Référence reconnue : `TIP-<id>` → pourboire, sinon → abonnement.
* **Double signature** : tout montant ≥ `TOUTBOT_DOUBLE_SIGNATURE_FCFA`
  (500 000 par défaut) exige **deux approbations d'administrateurs distincts**
  sur `/admin/v10/reglements`.
* Les routes `/webhooks/` sont exemptées de CSRF et de limitation de débit
  (les agrégateurs doivent pouvoir rappeler), mais **jamais** de vérification de signature.

## 7. Incidents courants

| Symptôme | Cause probable | Action |
|---|---|---|
| Refus de démarrage | `SECRET_KEY` absente en production | générer et exporter la clé |
| `429` en rafale | limitation de débit (10/min routes sensibles, 240/min global) | vérifier le trafic, ajuster `TOUTBOT_RATE_*` |
| `403` sur `/connexion` | IP bannie (brute-force) | attendre la fin du ban ou vider `tbm:ban:*` dans Redis |
| `503` sur `/webhooks/*` | secret de webhook absent | renseigner `TOUTBOT_WEBHOOK_SECRET_MTN/MOOV` |
| `400 csrf_invalide` | session expirée ou page ancienne | recharger la page (le jeton est réinjecté) |
| `database is locked` (SQLite) | écritures concurrentes | migrer sur PostgreSQL (`DATABASE_URL`) |

## 8. Journalisation

* Format JSON par défaut en production (`TOUTBOT_LOG_JSON=1`), chaque ligne porte
  un `X-Request-ID` renvoyé au client : corréler avec les journaux Nginx.
* Les champs sensibles (mot de passe, jeton, secret, CSRF) sont **masqués** avant écriture.
* Rétention : à piloter au niveau de l'hôte (logrotate / collecteur).

## 9. Mise à jour sans coupure

```bash
git pull && docker compose build web
docker compose up -d --no-deps web     # recréation progressive du service web
curl -fsS https://votre-domaine/ready  # vérifier avant de purger Nginx
```
