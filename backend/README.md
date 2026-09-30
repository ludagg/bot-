# SIH — backend

Spec : `../docs/SPEC.md`. Choix d'implémentation : `../docs/DECISIONS.md`.

## Démarrage

```bash
cp ../.env.example ../.env          # puis ajuster
docker compose up -d postgres redis
cd backend && pip install -e ".[dev]"
python -m sih.cli migrate
python -m sih.cli hn-backfill --days 30
python -m sih.cli hn-collect        # passage incrémental (toutes les 10 min via compose)
```

## Tests

Nécessitent un PostgreSQL 16 (`TEST_DATABASE_URL`, défaut `postgresql+psycopg://postgres@localhost:5433/sih_test`) ; la base est réinitialisée à chaque session.

```bash
pytest
```
