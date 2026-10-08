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

## Implémentation complète (hors découpage par jours)

9. **Redis non utilisé en v0.** Le planificateur pose un bail en base (`UPDATE … FOR UPDATE SKIP LOCKED` sur `sources.next_fetch_at`) et collecte en `asyncio` dans un seul processus, avec limite de débit par domaine. Redis reste dans le compose pour la montée en charge (workers multiples, verrous par domaine).
10. **GitHub : Search API uniquement.** Nouveaux dépôts des 2 derniers jours triés par étoiles (3 pages × 100), relus à chaque passage pour rafraîchir étoiles/forks. L'API Events n'est pas utilisée en v0.
11. **Fenêtre et score.** Fenêtre courante = 6 buckets horaires (dont l'heure en cours). Base = 14 jours de buckets horaires avant la fenêtre, absents = 0. `z = 0.6745·(moyenne horaire courante − médiane)/max(MAD, 0.25)` ; attendu = `max(6·médiane, 1)` ; une communauté « bouge » si z ≥ 4, ≥ 5 mentions et ≥ 3× l'attendu. `peak_ratio` = hausse relative (6.38 = +638 %, comme l'exemple de la spec).
12. **Confiance** = `0.2 × min(communautés, 4) + 0.2 × min(z, 20)/20`. 1 communauté = signal non stocké ; 2 = orange ; ≥ 3 = rouge.
13. **Déduplication.** `detections.last_active_at` (ajouté) : tant qu'une détection non clôturée a été active il y a moins de 48 h, l'entité y reste rattachée ; clôture après 48 h de calme. `first_detected_at` = heure de l'analyse (`as_of`), jamais modifié.
14. **Agrégation.** Additive par identifiant croissant (`kv_state.aggregate_last_id`), événements de moins de 30 s différés. Les événements de flux sans date prennent la date de première collecte.
15. **Cold start.** La publication (site et X) ne concerne que les détections postérieures à `PUBLISH_MIN_HISTORY_DAYS` (7) après le premier compteur horaire.
16. **X.** `X_MODE` = `off` (interrupteur), `review` (défaut : messages en file `x_outbox`, envoi par `sih x-approve <id>`), `auto`. Plafond 3 tweets/jour ; texte issu du gabarit fixe, entité réduite à `[a-z0-9 .+#/-]`. Le message UPDATE est mis en file quand l'explication est validée.
17. **LLM.** Appel HTTP direct à l'API Messages (modèle économique configurable, `LLM_MODEL`), un appel par détection, 3 tentatives maximum, plafond `LLM_DAILY_CAP`, coût cumulé dans `llm_usage`. Tarifs codés en dur, à confirmer.
18. **Purge.** Brut supprimé par `DELETE` après 31 jours, compteurs horaires après 60 jours (la base de référence en exige 14 à 30).
19. **Front.** Rendu serveur avec `revalidate = 60` + rafraîchissement client toutes les 60 s ; image Open Graph par détection (`next/og`). La page « Legal » contient un emplacement à compléter (éditeur, contact, hébergeur).

## Audit sur données réelles (collecte HN 30 j + 40 flux RSS)

20. **Bug corrigé : curseur d'agrégation.** Le curseur sautait les lignes trop récentes (identifiants attribués avant le commit) : sur la collecte réelle, 6 000 événements n'étaient jamais comptés. Désormais l'agrégation s'arrête à la première ligne de moins de 30 s, et ne dépasse jamais une ligne non traitée. Test de régression ajouté.
21. **Bug corrigé : vieux articles.** Les flux RSS renvoient parfois des articles de 2015. Ceux de plus de 31 jours sont stockés mais ne comptent plus comme activité.
22. **Trigger confirmé en conditions réelles.** Un TRUNCATE sur `publication_log` est refusé, comme prévu.
23. **Résultat sur données réelles (3 jours, 40 flux RSS et HN).** Zéro détection : aucun pic ne dépasse la base de référence avec les seuils de la spec. C'est le comportement attendu, mais ça ne valide rien : il faut plusieurs semaines de collecte multi-communautés avant de régler les seuils.
24. **Limite connue : mots génériques.** Des termes comme « work », « time », « life », « team » deviennent des entités. Ils ne déclenchent que s'ils montent nettement au-dessus de leur base, mais une liste de mots vides plus large (ou un filtre par fréquence) est à ajouter avant le lancement.
25. **Limite connue : GitHub injoignable depuis ce conteneur** (403 sur l'API Search sans jeton). Le collecteur n'a donc pas été testé en réel.
