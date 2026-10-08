from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx
from sqlalchemy import text

from sih import scheduler
from sih.collectors import github, rss
from sih.sources import import_sources

FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Big &lt;b&gt;news&lt;/b&gt;</title><link>https://blog.example.com/a</link><guid>g1</guid>
<pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Hello&lt;/p&gt;</description></item>
<item><title>No date</title><link>https://blog.example.com/b</link></item></channel></rss>"""


def src(engine, type_, url, community="ai", interval=60):
    with engine.begin() as conn:
        sid = conn.execute(text("INSERT INTO sources (type,url,community,interval_minutes) VALUES (:t,:u,:c,:i) RETURNING id"),
                           {"t": type_, "u": url, "c": community, "i": interval}).scalar_one()
        return dict(conn.execute(text("SELECT id,type,url,community,interval_minutes,etag,last_modified,cursor FROM sources WHERE id=:i"), {"i": sid}).one()._mapping)


def test_parse_feed_and_adaptive_interval():
    ev = rss.parse_feed(FEED, datetime(2026, 9, 30, tzinfo=UTC))
    assert [e.external_id for e in ev][0] == "g1"
    assert ev[0].domain == "blog.example.com" and "<" not in ev[0].body
    assert ev[1].published_at == datetime(2026, 9, 30, tzinfo=UTC)  # date absente → now
    assert rss.adapt_interval(60, 3) == 30 and rss.adapt_interval(20, 1) == 15
    assert rss.adapt_interval(1000, 0) == 1440


@respx.mock
async def test_rss_conditional_requests(clean):
    s = src(clean, "rss", "https://blog.example.com/feed")
    respx.get(s["url"]).mock(return_value=httpx.Response(200, content=FEED, headers={"etag": '"v1"'}))
    async with httpx.AsyncClient() as c:
        assert await rss.collect(clean, s, c) == 2
        s["etag"] = '"v1"'
        respx.get(s["url"]).mock(return_value=httpx.Response(304))
        assert await rss.collect(clean, s, c) == 0
        assert respx.calls.last.request.headers["if-none-match"] == '"v1"'
    with clean.connect() as conn:
        etag, err = conn.execute(text("SELECT etag, error_count FROM sources WHERE id=:i"), {"i": s["id"]}).one()
    assert etag == '"v1"' and err == 0


@respx.mock
async def test_rss_failure_is_isolated(clean):
    s = src(clean, "rss", "https://down.example.com/feed")
    respx.get(s["url"]).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as c:
        assert await rss.collect(clean, s, c) == 0
    with clean.connect() as conn:
        assert conn.execute(text("SELECT error_count FROM sources WHERE id=:i"), {"i": s["id"]}).scalar_one() == 1


@respx.mock
async def test_github_collects_repos_with_metrics(clean):
    s = src(clean, "github", github.GH_URL, "github", 15)
    item = {"id": 7, "full_name": "Foo/Bar", "created_at": "2026-09-30T01:00:00Z", "html_url": "https://github.com/Foo/Bar",
            "description": "An agent framework", "topics": ["llm"], "stargazers_count": 120, "forks_count": 4, "language": "Python"}
    respx.get(github.GH_URL).mock(return_value=httpx.Response(200, json={"items": [item]}))
    async with httpx.AsyncClient() as c:
        assert await github.collect(clean, s, c, "tok") == 1
    assert respx.calls.last.request.headers["authorization"] == "Bearer tok"
    with clean.connect() as conn:
        r = conn.execute(text("SELECT title, (metrics->>'stars')::int stars FROM raw_events")).one()
    assert (r.title, r.stars) == ("Foo/Bar", 120)


@respx.mock
async def test_github_rate_limit_waits_then_retries(clean, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("sih.collectors.base.asyncio.sleep", fake_sleep)
    s = src(clean, "github", github.GH_URL, "github", 15)
    respx.get(github.GH_URL).mock(side_effect=[
        httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"}),
        httpx.Response(200, json={"items": []})])
    async with httpx.AsyncClient() as c:
        await github.collect(clean, s, c)
    assert slept and slept[0] >= 1


@respx.mock
async def test_scheduler_claims_due_sources_once_and_dispatches(clean, monkeypatch):
    monkeypatch.setattr("sih.scheduler.DOMAIN_DELAY", 0)
    a = src(clean, "rss", "https://a.example.com/feed")
    with clean.begin() as conn:  # une source non due
        conn.execute(text("INSERT INTO sources (type,url,community,next_fetch_at) VALUES ('rss','https://later.example.com',"
                          "'ai', now() + interval '1 day')"))
    respx.get(a["url"]).mock(return_value=httpx.Response(200, content=FEED))
    out = await scheduler.run_due(clean)
    assert out == {"rss": 2}
    assert scheduler.claim_due(clean) == []  # bail posé puis prochaine échéance repoussée


def test_import_csv_and_opml(clean, tmp_path):
    csv_ = tmp_path / "f.csv"
    csv_.write_text("# c\nhttps://a.example/feed,ai\nhttps://b.example/feed,security\n")
    assert import_sources(clean, csv_) == 2
    assert import_sources(clean, csv_) == 0  # idempotent
    opml = tmp_path / "f.opml"
    opml.write_text('<opml><body><outline title="Web Dev"><outline xmlUrl="https://c.example/feed"/></outline></body></opml>')
    assert import_sources(clean, opml) == 1
    with clean.connect() as conn:
        assert conn.execute(text("SELECT community FROM sources WHERE url='https://c.example/feed'")).scalar_one() == "web-dev"
    seed = Path(__file__).parent.parent / "seeds" / "feeds.csv"
    assert import_sources(clean, seed) > 30
