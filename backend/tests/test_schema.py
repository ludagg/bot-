import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def _detection(conn):
    ent = conn.execute(text("INSERT INTO entities (kind, name) VALUES ('keyword','x') RETURNING id")).scalar_one()
    return conn.execute(
        text(
            """INSERT INTO detections (entity_id, first_detected_at, confidence, communities_moving)
               VALUES (:e, now(), 0.9, '{a,b,c}') RETURNING id"""
        ),
        {"e": ent},
    ).scalar_one()


def test_publication_log_is_append_only(clean):
    with clean.begin() as conn:
        det = _detection(conn)
        conn.execute(
            text("INSERT INTO publication_log (detection_id, channel, snapshot, content_hash) VALUES (:d,'site','{}','h')"),
            {"d": det},
        )
    for stmt in ("UPDATE publication_log SET channel='x'", "DELETE FROM publication_log", "TRUNCATE publication_log"):
        with pytest.raises(DBAPIError, match="ajout seul"):
            with clean.begin() as conn:
                conn.execute(text(stmt))


def test_first_detected_at_is_immutable(clean):
    with clean.begin() as conn:
        det = _detection(conn)
    with pytest.raises(DBAPIError, match="immuable"):
        with clean.begin() as conn:
            conn.execute(text("UPDATE detections SET first_detected_at = now() - interval '1 day' WHERE id=:i"), {"i": det})
    with clean.begin() as conn:  # les autres colonnes restent modifiables
        conn.execute(text("UPDATE detections SET status='explained' WHERE id=:i"), {"i": det})


def test_partitions_route_by_month(clean):
    with clean.begin() as conn:
        sid = conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss','u','ai') RETURNING id")).scalar_one()
        conn.execute(
            text("INSERT INTO raw_events (source_id, external_id, published_at) VALUES (:s,'1', now())"), {"s": sid}
        )
        part = conn.execute(text("SELECT tableoid::regclass::text FROM raw_events")).scalar_one()
    assert part.startswith("raw_events_20")
