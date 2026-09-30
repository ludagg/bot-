"""Tables du pipeline : état, usage LLM, file de publication X

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE detections ADD COLUMN last_active_at TIMESTAMPTZ NOT NULL DEFAULT now();
        CREATE INDEX detections_active_idx ON detections (entity_id, last_active_at);

        CREATE TABLE kv_state (key TEXT PRIMARY KEY, value JSONB NOT NULL);

        CREATE TABLE llm_usage (
          id BIGSERIAL PRIMARY KEY,
          detection_id BIGINT NOT NULL REFERENCES detections(id),
          model TEXT NOT NULL,
          input_tokens INT NOT NULL DEFAULT 0,
          output_tokens INT NOT NULL DEFAULT 0,
          cost_usd NUMERIC(10,5) NOT NULL DEFAULT 0,
          ok BOOLEAN NOT NULL DEFAULT true,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE x_outbox (
          id BIGSERIAL PRIMARY KEY,
          detection_id BIGINT NOT NULL REFERENCES detections(id),
          kind TEXT NOT NULL,                      -- tweet | update
          text TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | rejected | failed
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          sent_at TIMESTAMPTZ,
          tweet_id TEXT,
          UNIQUE (detection_id, kind)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE x_outbox, llm_usage, kv_state; ALTER TABLE detections DROP COLUMN last_active_at;"
    )
