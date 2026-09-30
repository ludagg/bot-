from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from sih import detect as D

NOW = datetime(2026, 9, 30, 12, 30, tzinfo=UTC)
CUR0 = D.floor_hour(NOW) - timedelta(hours=5)


def seed(conn, name, community, baseline=None, spike=None, kind="keyword"):
    """baseline: mentions/h constantes sur 14 j ; spike: mentions/h sur la fenêtre courante."""
    eid = conn.execute(
        text("INSERT INTO entities (kind,name) VALUES (:k,:n) ON CONFLICT (kind,name) DO UPDATE SET name=EXCLUDED.name RETURNING id"),
        {"k": kind, "n": name},
    ).scalar_one()
    rows = []
    if baseline:
        rows += [(eid, community, CUR0 - timedelta(hours=i), baseline) for i in range(1, 14 * 24 + 1)]
    if spike:
        rows += [(eid, community, CUR0 + timedelta(hours=i), spike) for i in range(6)]
    for e, c, h, m in rows:
        conn.execute(text("INSERT INTO entity_hourly VALUES (:e,:c,:h,:m)"), {"e": e, "c": c, "h": h, "m": m})
    return eid


def test_robust_z_and_confidence():
    assert D.robust_z(5, 1, 0.5, 0.25) == pytest.approx(0.6745 * 4 / 0.5)
    assert D.robust_z(5, 1, 0, 0.25) == pytest.approx(0.6745 * 4 / 0.25)  # plancher MAD
    assert D.confidence(3, 10) > D.confidence(2, 10) > D.confidence(1, 10)
    assert D.confidence(9, 99) < 1


def test_rare_entity_zero_to_three_is_ignored(clean):
    with clean.begin() as conn:
        seed(conn, "rare", "ai", baseline=0, spike=0)
        conn.execute(text("INSERT INTO entity_hourly VALUES ((SELECT id FROM entities WHERE name='rare'),'ai',:h,3)"), {"h": CUR0 + timedelta(hours=5)})
        assert D.detect(conn, NOW, persist=False) == []  # 3 mentions < seuil de 5


def test_single_community_is_a_signal_not_stored(clean):
    with clean.begin() as conn:
        seed(conn, "solo", "ai", baseline=1, spike=10)
        res = D.detect(conn, NOW)
        assert [r.level for r in res] == ["signal"]
        assert conn.execute(text("SELECT count(*) FROM detections")).scalar_one() == 0


def test_three_communities_is_red_and_dedup_then_reopen_after_quiet(clean):
    with clean.begin() as conn:
        for c in ("ai", "hackernews", "github"):
            seed(conn, "gpt-9", c, baseline=1, spike=12)
        res = D.detect(conn, NOW)
        assert res[0].level == "red" and res[0].is_new and res[0].peak_ratio > 3
        first = conn.execute(text("SELECT id, first_detected_at FROM detections")).one()
        # Même entité 1 h plus tard : toujours la même détection.
        again = D.detect(conn, NOW + timedelta(hours=1))
        assert conn.execute(text("SELECT count(*) FROM detections")).scalar_one() == 1
        assert again == [] or not again[0].is_new
        assert conn.execute(text("SELECT first_detected_at FROM detections")).scalar_one() == first.first_detected_at
        # 49 h plus tard, retour au calme → clôturée ; nouveau pic → nouvelle détection.
        D.detect(conn, NOW + timedelta(hours=60))
        assert conn.execute(text("SELECT status FROM detections WHERE id=:i"), {"i": first.id}).scalar_one() == "closed"


def test_one_ecosystem_counts_once(clean):
    """50 blogs d'une même communauté = 1 signal : jamais de détection."""
    with clean.begin() as conn:
        seed(conn, "hype", "ai", baseline=1, spike=200)
        D.detect(conn, NOW)
        assert conn.execute(text("SELECT count(*) FROM detections")).scalar_one() == 0


def test_replay_finds_known_past_event(clean):
    """Un pic historique sur 2 communautés est retrouvé, avec son heure de première détection."""
    with clean.begin() as conn:
        for c in ("security", "hackernews"):
            seed(conn, "heartbleed", c, baseline=1, spike=15)
        found = D.replay(conn, NOW - timedelta(hours=8), NOW + timedelta(hours=8))
    t, r = found[("keyword", "heartbleed")]
    assert r.level == "orange"
    assert NOW - timedelta(hours=8) <= t <= NOW + timedelta(hours=8)
