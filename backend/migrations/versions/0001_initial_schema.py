"""Schéma initial SIH (spec section 3)

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = """
CREATE TABLE sources (
  id BIGSERIAL PRIMARY KEY,
  type TEXT NOT NULL,                       -- rss | hn | github | stream
  url TEXT NOT NULL UNIQUE,
  community TEXT NOT NULL,                  -- ai, security, web-dev...
  interval_minutes INT NOT NULL DEFAULT 60,
  next_fetch_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  etag TEXT,
  last_modified TEXT,
  cursor JSONB,                             -- état du collecteur (ex. dernier timestamp lu)
  last_success_at TIMESTAMPTZ,
  error_count INT NOT NULL DEFAULT 0,
  active BOOLEAN NOT NULL DEFAULT true
);

-- Partitionnée par mois sur published_at. Postgres impose que la clé de
-- partition figure dans toute contrainte unique : voir docs/DECISIONS.md.
CREATE TABLE raw_events (
  id BIGSERIAL,
  source_id BIGINT NOT NULL REFERENCES sources(id),
  external_id TEXT NOT NULL,
  title TEXT, url TEXT, domain TEXT, body TEXT,
  metrics JSONB,                            -- points, stars, forks...
  published_at TIMESTAMPTZ NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, published_at),
  UNIQUE (source_id, external_id, published_at)
) PARTITION BY RANGE (published_at);
CREATE INDEX raw_events_published_at_idx ON raw_events (published_at);

CREATE TABLE entities (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,                       -- keyword | repo | domain
  name TEXT NOT NULL,                       -- forme normalisée
  UNIQUE (kind, name)
);

CREATE TABLE entity_hourly (
  entity_id BIGINT NOT NULL REFERENCES entities(id),
  community TEXT NOT NULL,
  hour TIMESTAMPTZ NOT NULL,                -- tronqué à l'heure
  mentions INT NOT NULL,
  PRIMARY KEY (entity_id, community, hour)
) PARTITION BY RANGE (hour);
CREATE INDEX entity_hourly_hour_idx ON entity_hourly (hour);

CREATE TABLE detections (
  id BIGSERIAL PRIMARY KEY,
  entity_id BIGINT NOT NULL REFERENCES entities(id),
  first_detected_at TIMESTAMPTZ NOT NULL,   -- jamais modifié (trigger)
  confidence NUMERIC(4,3) NOT NULL,
  communities_moving TEXT[] NOT NULL,
  peak_ratio NUMERIC,
  status TEXT NOT NULL DEFAULT 'open',      -- open | explained | closed
  explanation JSONB,
  UNIQUE (entity_id, first_detected_at)
);

CREATE TABLE publication_log (
  id BIGSERIAL PRIMARY KEY,
  detection_id BIGINT NOT NULL REFERENCES detections(id),
  channel TEXT NOT NULL,                    -- site | x
  snapshot JSONB NOT NULL,
  content_hash TEXT NOT NULL,
  published_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Journal en ajout seul.
CREATE FUNCTION publication_log_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'publication_log est en ajout seul (% interdit)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER publication_log_no_update_delete
  BEFORE UPDATE OR DELETE ON publication_log
  FOR EACH ROW EXECUTE FUNCTION publication_log_append_only();
CREATE TRIGGER publication_log_no_truncate
  BEFORE TRUNCATE ON publication_log
  FOR EACH STATEMENT EXECUTE FUNCTION publication_log_append_only();

-- first_detected_at est immuable.
CREATE FUNCTION detections_first_detected_immutable() RETURNS trigger AS $$
BEGIN
  IF NEW.first_detected_at IS DISTINCT FROM OLD.first_detected_at THEN
    RAISE EXCEPTION 'detections.first_detected_at est immuable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER detections_first_detected_guard
  BEFORE UPDATE ON detections
  FOR EACH ROW EXECUTE FUNCTION detections_first_detected_immutable();

-- Création idempotente des partitions mensuelles [from_month, to_month].
CREATE FUNCTION ensure_month_partitions(parent TEXT, from_month DATE, to_month DATE)
RETURNS void AS $$
DECLARE
  m DATE := date_trunc('month', from_month)::date;
BEGIN
  WHILE m <= to_month LOOP
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
      parent || '_' || to_char(m, 'YYYY_MM'), parent, m, (m + interval '1 month')::date
    );
    m := (m + interval '1 month')::date;
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Partitions par défaut (filet de sécurité) + fenêtre initiale.
CREATE TABLE raw_events_default PARTITION OF raw_events DEFAULT;
CREATE TABLE entity_hourly_default PARTITION OF entity_hourly DEFAULT;
SELECT ensure_month_partitions('raw_events', (now() - interval '3 months')::date, (now() + interval '3 months')::date);
SELECT ensure_month_partitions('entity_hourly', (now() - interval '3 months')::date, (now() + interval '3 months')::date);
"""


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE publication_log, detections, entity_hourly, entities, raw_events, sources CASCADE;
        DROP FUNCTION publication_log_append_only(), detections_first_detected_immutable(),
                      ensure_month_partitions(TEXT, DATE, DATE);
        """
    )
