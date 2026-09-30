# SIH — backend

Spec : `../docs/SPEC.md`. Choix d'implémentation : `../docs/DECISIONS.md`. Front : `../web`.

## Architecture

`collectors/` (hn, github, rss) → `scheduler` (registre `sources`) → `entities` (extraction + compteurs
horaires) → `detect` (médiane/MAD, multi-communautés) → `analysis` (LLM, JSON validé) → `publish`
(journal immuable + file X) → `api` (FastAPI, lecture seule). `worker` orchestre le tout.

## Démarrage local

```bash
cp ../.env.example ../.env          # ajuster mots de passe / clés
docker compose -f ../docker-compose.yml up -d postgres redis
pip install -e ".[dev]"
python -m sih.cli migrate
python -m sih.cli import-sources seeds/feeds.csv     # ou un OPML : --community ai
python -m sih.cli hn-backfill --days 30              # historique (évite le démarrage à froid)
python -m sih.cli worker                              # collecte + analyse en continu
uvicorn sih.api:app --port 8000
```

En production : `docker compose up -d` (postgres, redis, migrate, worker, api, caddy), puis déployer `web/` sur Vercel
avec `API_URL` pointant vers l'API.

## Commandes utiles

| Commande | Rôle |
| --- | --- |
| `sih replay --days 14` | Rejoue le moteur sur l'historique (sans écrire) : sert à régler les seuils sur des événements connus |
| `sih analytics` | Un cycle agrégation → détection → analyse → publication |
| `sih x-list` / `sih x-approve 1 2` | Relecture humaine des messages X (mode `review`) |
| `sih health` | État des collecteurs, coût LLM du jour, détections par jour |
| `scripts/backup.sh` | `pg_dump` quotidien (cron) vers un stockage objet |

## Tests

Nécessitent PostgreSQL 16 (`TEST_DATABASE_URL`, défaut `postgresql+psycopg://postgres@localhost:5433/sih_test`) ; la base est réinitialisée à chaque session.

```bash
pytest
```

## Avant le lancement (à vérifier hors code)

Conditions et limites de l'API GitHub, d'Algolia HN, de chaque flux RSS ; règles et tarif de l'API X pour les publications automatisées ;
compléter la page `/legal` ; créer le compte X et renseigner les clés.
