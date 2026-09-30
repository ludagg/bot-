from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import text

from sih.collectors import hn
from sih.collectors.base import get_json

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def hit(i: int, ts: datetime, points: int = 10):
    return {
        "objectID": str(i),
        "created_at_i": int(ts.timestamp()),
        "title": f"Story {i}",
        "url": f"https://example{i}.com/post",
        "points": points,
        "num_comments": 3,
        "author": "pg",
    }


def test_parse_hit_extracts_domain_and_metrics():
    ev = hn.parse_hit(hit(1, NOW))
    assert ev.external_id == "1"
    assert ev.domain == "example1.com"
    assert ev.metrics["points"] == 10
    assert ev.published_at == NOW


def test_parse_hit_without_url_and_invalid():
    h = hit(2, NOW)
    del h["url"]
    assert hn.parse_hit(h).domain is None
    assert hn.parse_hit({"objectID": "3"}) is None


@respx.mock
async def test_collect_is_idempotent_and_refreshes_metrics(clean):
    sid = hn.ensure_source(clean)
    route = respx.get(hn.HN_URL)
    route.mock(
        return_value=httpx.Response(200, json={"nbHits": 2, "hits": [hit(1, NOW - timedelta(hours=1)), hit(2, NOW - timedelta(hours=2))]})
    )
    since, until = NOW - timedelta(hours=6), NOW
    assert await hn.collect(clean, sid, since, until) == 2
    # Second passage : mêmes événements → rien de créé, pas de doublon.
    route.mock(
        return_value=httpx.Response(200, json={"nbHits": 2, "hits": [hit(1, NOW - timedelta(hours=1), points=99), hit(2, NOW - timedelta(hours=2))]})
    )
    assert await hn.collect(clean, sid, since, until) == 0
    with clean.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM raw_events")).scalar_one()
        pts = conn.execute(text("SELECT (metrics->>'points')::int FROM raw_events WHERE external_id='1'")).scalar_one()
        src = conn.execute(text("SELECT error_count, last_success_at IS NOT NULL, cursor FROM sources WHERE id=:i"), {"i": sid}).one()
    assert n == 2 and pts == 99
    assert src[0] == 0 and src[1] and src[2]["last_created_at"]


@respx.mock
async def test_window_is_split_when_algolia_caps_results():
    calls = []

    def handler(request: httpx.Request):
        f = request.url.params["numericFilters"]
        calls.append(f)
        big = len(calls) == 1  # la fenêtre entière déborde, les moitiés non
        return httpx.Response(200, json={"nbHits": 1500 if big else 1, "hits": [] if big else [hit(len(calls), NOW)]})

    respx.get(hn.HN_URL).mock(side_effect=handler)
    async with httpx.AsyncClient() as c:
        events = await hn.fetch_window(c, NOW - timedelta(hours=6), NOW)
    assert len(calls) == 3 and len(events) == 2


@respx.mock
async def test_failure_increments_error_count_and_backs_off(clean, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("sih.collectors.base.asyncio.sleep", no_sleep)
    sid = hn.ensure_source(clean)
    respx.get(hn.HN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError):
        await hn.collect(clean, sid, NOW - timedelta(hours=1), NOW)
    with clean.connect() as conn:
        ec, active, nxt = conn.execute(text("SELECT error_count, active, next_fetch_at > now() + interval '5 minutes' FROM sources WHERE id=:i"), {"i": sid}).one()
    assert ec == 1 and active and nxt


@respx.mock
async def test_get_json_retries_then_succeeds(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("sih.collectors.base.asyncio.sleep", no_sleep)
    respx.get("https://x.test/").mock(
        side_effect=[httpx.Response(429), httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    )
    async with httpx.AsyncClient() as c:
        assert await get_json(c, "https://x.test/") == {"ok": 1}


async def test_source_deactivated_after_repeated_failures(clean):
    sid = hn.ensure_source(clean)
    from sih.collectors.base import MAX_ERRORS, mark_failure

    for _ in range(MAX_ERRORS):
        with clean.begin() as conn:
            mark_failure(conn, sid)
    with clean.connect() as conn:
        assert conn.execute(text("SELECT active FROM sources WHERE id=:i"), {"i": sid}).scalar_one() is False
