import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from sih import analysis, api, publish, worker
from sih.config import Settings

NOW = datetime.now(UTC)


def settings(**kw):
    return Settings(publish_min_history_days=0, llm_daily_cap=5, x_mode="auto", site_base_url="https://sih.test", **kw)


def make_detection(engine, name="gpt-9", communities=("ai", "github", "hackernews"), first=None, ratio=6.38):
    first = first or NOW - timedelta(hours=1)
    with engine.begin() as conn:
        eid = conn.execute(text("INSERT INTO entities (kind,name) VALUES ('keyword',:n) RETURNING id"), {"n": name}).scalar_one()
        conn.execute(text("INSERT INTO entity_hourly VALUES (:e,'ai',:h,9)"), {"e": eid, "h": first.replace(minute=0, second=0, microsecond=0)})
        sid = conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('hn',:u,'hackernews') RETURNING id"), {"u": f"u-{name}"}).scalar_one()
        conn.execute(text("INSERT INTO raw_events (source_id, external_id, title, url, metrics, published_at) VALUES "
                          "(:s,'1',:t,'https://real.example/a','{\"points\": 50}', :p)"),
                     {"s": sid, "t": f"{name} released", "p": first})
        return conn.execute(
            text("""INSERT INTO detections (entity_id, first_detected_at, confidence, communities_moving, peak_ratio)
                    VALUES (:e,:f,0.8,:c,:r) RETURNING id"""), {"e": eid, "f": first, "c": list(communities), "r": ratio}).scalar_one()


class FakeLLM:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def complete(self, system, user):
        self.calls += 1
        self.user = user
        return json.dumps(self.payload), 100, 50


GOOD = {"headline": "Activité autour de gpt-9 : +638 % en 6 h", "summary": "s", "confidence_note": "bot ?",
        "hypotheses": ["une sortie de modèle"],
        "timeline": [{"time": "2026-09-30T10:00:00Z", "event": "post HN", "source_url": "https://real.example/a"},
                     {"time": "2026-09-30T10:05:00Z", "event": "inventé", "source_url": "https://evil.example/x"}]}


def test_llm_output_validation_drops_uncited_timeline_points():
    exp = analysis.validate("```json\n" + json.dumps(GOOD) + "\n```", {"https://real.example/a"})
    assert [p.source_url for p in exp.timeline] == ["https://real.example/a"]
    assert exp.hypotheses == ["Hypothèse : une sortie de modèle"]
    with pytest.raises(Exception):
        analysis.validate("pas du json", set())


def test_analyze_pending_stores_explanation_and_respects_cap(clean):
    d1 = make_detection(clean, "one")
    llm = FakeLLM(GOOD)
    assert analysis.analyze_pending(clean, llm, settings()) == 1
    with clean.connect() as conn:
        status, exp = conn.execute(text("SELECT status, explanation FROM detections WHERE id=:i"), {"i": d1}).one()
        cost = conn.execute(text("SELECT input_tokens, cost_usd FROM llm_usage")).one()
    assert status == "explained" and len(exp["timeline"]) == 1 and cost[0] == 100
    # Contenu collecté transmis comme donnée JSON, jamais mêlé aux consignes.
    assert json.loads(llm.user)["events"][0]["url"] == "https://real.example/a"
    assert analysis.analyze_pending(clean, llm, settings()) == 0  # déjà expliquée → un seul appel par détection
    assert llm.calls == 1
    make_detection(clean, "two")
    assert analysis.analyze_pending(clean, FakeLLM(GOOD), Settings(llm_daily_cap=1)) == 0  # plafond atteint


def test_invalid_llm_output_leaves_detection_open_and_retries_are_bounded(clean):
    d = make_detection(clean, "bad")

    class Bad(FakeLLM):
        def complete(self, s, u):
            return "n'importe quoi", 1, 1

    for _ in range(5):
        analysis.analyze_pending(clean, Bad({}), settings())
    with clean.connect() as conn:
        assert conn.execute(text("SELECT status FROM detections WHERE id=:i"), {"i": d}).scalar_one() == "open"
        assert conn.execute(text("SELECT count(*) FROM llm_usage")).scalar_one() == analysis.MAX_ATTEMPTS


def test_tweet_template_is_fixed_and_sanitised(clean):
    d = make_detection(clean, "@everyone http://x.co")
    with clean.connect() as conn:
        row = conn.execute(text("SELECT d.id, e.name, d.first_detected_at, d.peak_ratio FROM detections d JOIN entities e ON e.id=d.entity_id WHERE d.id=:i"), {"i": d}).one()
    t = publish.tweet_text(row, settings())
    assert "@" not in t and "://x" not in t
    assert t.startswith("SOMETHING IS HAPPENING — Activity around ") and "increased 638% in 6h" in t
    assert t.endswith(f"https://sih.test/d/{d}")


class FakeX:
    def __init__(self):
        self.sent = []

    def post(self, t):
        self.sent.append(t)
        return str(len(self.sent))


def test_publish_journal_x_cap_update_and_immutability(clean):
    for n in ("a1", "b2", "c3", "d4"):
        make_detection(clean, n)
    make_detection(clean, "orange1", communities=("ai", "github"))
    s = settings(x_daily_cap=3)
    out = publish.publish_pending(clean, s)
    assert out["site"] == 5 and out["x"] == 3  # orange non publié sur X ; plafond de 3/jour
    assert publish.publish_pending(clean, s) == {"site": 0, "update": 0, "x": 0}  # idempotent
    with clean.connect() as conn:
        snap, h = conn.execute(text("SELECT snapshot, content_hash FROM publication_log ORDER BY id LIMIT 1")).one()
    assert publish.canonical_hash(snap) == h
    x = FakeX()
    assert publish.send_outbox(clean, s, client=x) == 3 and len(x.sent) == 3
    # Explication terminée → ligne « update » (nouvelle ligne, jamais une modification) + message UPDATE.
    analysis.analyze_pending(clean, FakeLLM(GOOD), s)
    out = publish.publish_pending(clean, s)
    assert out["update"] >= 1 and out["x"] >= 1
    with pytest.raises(DBAPIError):
        with clean.begin() as conn:
            conn.execute(text("UPDATE publication_log SET content_hash='x'"))


def test_x_kill_switch_and_review_mode(clean):
    make_detection(clean, "e5")
    off = settings(x_daily_cap=3)
    off.x_mode = "off"
    assert publish.publish_pending(clean, off)["x"] == 0
    review = settings()
    review.x_mode = "review"
    assert publish.publish_pending(clean, review)["x"] == 1
    x = FakeX()
    assert publish.send_outbox(clean, review, client=x) == 0  # rien sans approbation humaine
    with clean.connect() as conn:
        oid = conn.execute(text("SELECT id FROM x_outbox")).scalar_one()
    assert publish.send_outbox(clean, review, client=x, only_ids=[oid]) == 1


def test_cold_start_blocks_publication(clean):
    make_detection(clean, "young")
    s = Settings(publish_min_history_days=7)
    assert publish.publish_pending(clean, s) == {"site": 0, "update": 0, "x": 0}


def test_api_endpoints(clean):
    did = make_detection(clean, "apitest")
    s = settings()
    analysis.analyze_pending(clean, FakeLLM(GOOD), s)
    publish.publish_pending(clean, s)
    api.app.dependency_overrides[api.get_engine] = lambda: clean
    c = TestClient(api.app)
    cards = c.get("/api/detections?status=open&limit=20").json()
    assert cards[0]["id"] == did and cards[0]["level"] == "red" and cards[0]["change_pct"] == 638
    assert cards[0]["age_seconds"] > 3000 and cards[0]["content_hash"]
    d = c.get(f"/api/detections/{did}").json()
    assert d["explanation"]["timeline"] and {l["snapshot"]["kind"] for l in d["log"]} == {"detection", "update"}
    ser = c.get(f"/api/detections/{did}/series").json()
    assert len(ser["hours"]) == 72 and sum(ser["series"]["ai"]) == 9
    assert c.get("/api/detections/9999").status_code == 404
    assert len(c.get("/api/log").json()) == 2
    h = c.get("/api/health").json()
    assert h["llm_today"]["calls"] == 1 and h["sources"]
    assert c.get("/api/detections?status=bogus").status_code == 422


def test_worker_cycle_end_to_end(clean):
    """Événements bruts → compteurs → détection → publication, sur 3 communautés indépendantes."""
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    with clean.begin() as conn:
        srcs = {c: conn.execute(text("INSERT INTO sources (type,url,community) VALUES ('rss',:u,:c) RETURNING id"),
                                {"u": f"https://{c}.x/feed", "c": c}).scalar_one() for c in ("ai", "security", "oss")}
        n = 0
        for c, sid in srcs.items():
            for i in range(14 * 24):  # base : 1 mention/h de « quantum »
                conn.execute(text("INSERT INTO raw_events (source_id, external_id, title, published_at, collected_at) VALUES (:s,:e,'quantum news',:p,:p)"),
                             {"s": sid, "e": f"b{n}", "p": now - timedelta(hours=7 + i)})
                n += 1
            for i in range(30):  # pic dans la fenêtre courante
                conn.execute(text("INSERT INTO raw_events (source_id, external_id, title, published_at, collected_at) VALUES (:s,:e,'quantum leap',:p,:p)"),
                             {"s": sid, "e": f"s{n}", "p": now - timedelta(hours=i % 5)})
                n += 1
    s = settings()
    out = worker.analytics_cycle(clean, s, as_of=now + timedelta(minutes=10))
    assert out["detections"] >= 1 and out["published"]["site"] >= 1
    with clean.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT e.name FROM detections d JOIN entities e ON e.id=d.entity_id"))}
    assert "quantum" in names
