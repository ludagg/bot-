"""Collecteur GitHub : nouveaux dépôts (Search API), avec étoiles, forks et topics.

Chaque passage relit les dépôts créés depuis 2 jours, triés par étoiles : l'upsert
rafraîchit les métriques, ce qui donne la dynamique des étoiles.
"""
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy import Engine, text

from sih.collectors.base import (
    RawEvent,
    get_json,
    mark_failure,
    mark_success,
    store_events,
)

log = structlog.get_logger()

GH_URL = "https://api.github.com/search/repositories"
GH_COMMUNITY = "github"
PAGES = 3
LOOKBACK = timedelta(days=2)


def parse_repo(item: dict) -> RawEvent | None:
    if not item.get("id") or not item.get("full_name") or not item.get("created_at"):
        return None
    topics = item.get("topics") or []
    desc = item.get("description") or ""
    return RawEvent(
        external_id=str(item["id"]),
        published_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
        title=item["full_name"],
        url=item.get("html_url"),
        domain="github.com",
        body=(desc + " " + " ".join(topics)).strip() or None,
        metrics={
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "language": item.get("language"),
            "topics": topics,
        },
    )


async def collect(engine: Engine, source: dict, client: httpx.AsyncClient, token: str = "") -> int:
    sid = source["id"]
    since = (datetime.now(UTC) - LOOKBACK).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SIH-bot/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        events: list[RawEvent] = []
        for page in range(1, PAGES + 1):
            data = await get_json(
                client,
                GH_URL,
                {"q": f"created:>={since} stars:>=1", "sort": "stars", "order": "desc", "per_page": 100, "page": page},
                headers=headers,
            )
            items = data.get("items", [])
            events += [e for i in items if (e := parse_repo(i))]
            if len(items) < 100:
                break
        with engine.begin() as conn:
            created = store_events(conn, sid, events)
            mark_success(conn, sid)
        return created
    except Exception:
        log.warning("github.failed", source_id=sid, exc_info=True)
        with engine.begin() as conn:
            mark_failure(conn, sid)
        return 0


def ensure_source(engine: Engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """INSERT INTO sources (type, url, community, interval_minutes)
                   VALUES ('github', :u, :c, 15)
                   ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url RETURNING id"""
            ),
            {"u": GH_URL, "c": GH_COMMUNITY},
        ).scalar_one()
