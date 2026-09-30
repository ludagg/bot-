# Décisions d'implémentation

Écarts et choix faits quand la spec (`docs/SPEC.md`) est ambiguë ou techniquement impossible telle quelle.

## J1

1. **Cohabitation avec le code existant.** Le dépôt contient déjà un bot WhatsApp (Node : `server.js`, `public/`). Il n'est pas touché ; SIH vit dans `backend/` (et `web/` à partir de J11), avec `docker-compose.yml` à la racine.
2. **Unicité sur table partitionnée.** PostgreSQL exige que la clé de partition figure dans toute contrainte unique. `raw_events` étant partitionnée par `published_at`, la clé est `(source_id, external_id, published_at)` et la PK `(id, published_at)`. L'idempotence réelle est garantie côté collecteur : l'insertion cherche d'abord `(source_id, external_id)` (upsert « update puis insert si absent »), ce qui reste correct même si `published_at` change. Pour les flux RSS sans date, on dérivera `published_at` de façon déterministe (à traiter en J3).
3. **Partitions.** Fonction SQL `ensure_month_partitions(parent, from, to)` ; la migration crée ±3 mois autour d'aujourd'hui plus une partition `DEFAULT` de sécurité. Une tâche planifiée appellera la fonction chaque mois (J14 / monitoring) et la purge du brut à 30 jours détachera les vieilles partitions.
4. **Communauté de Hacker News.** HN est une source unique ; on lui attribue sa propre communauté `hackernews`, considérée comme indépendante des blogs RSS.
5. **Curseur.** Colonne `sources.cursor JSONB` (absente de la spec) pour l'état du collecteur ; HN relit 1 h de chevauchement à chaque passage pour rafraîchir points/commentaires.
6. **Trigger supplémentaire.** En plus du journal en ajout seul, un trigger interdit toute modification de `detections.first_detected_at` (règle 6 du prompt).
7. **Backoff de source.** À chaque échec : `next_fetch_at` repoussé de `intervalle × 2^erreurs` (plafond 24 h) ; désactivation après 10 échecs consécutifs (l'alerte Telegram arrive avec le monitoring).
8. **Backfill HN.** Fenêtres de 6 h, scindées en deux si Algolia signale plus de 1000 résultats (limite dure de l'API).
