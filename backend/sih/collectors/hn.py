"""Collecteur Hacker News via l'API Algolia (gratuite).

Le curseur est le dernier created_at_i lu ; chaque passage relit une heure de
chevauchement pour rafraîchir points et commentaires (upsert idempotent).
"""
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import Engine, text

from sih.collectors.base import RawEvent, get_json, mark_failure, mark_success, store_events

log = structlog.get_logger()

HN_URL = "https://hn.algolia.com/api/v1/search_by_date"
HN_COMMUNITY = "hackernews"
PAGE_SIZE = 1000  # maximum Algolia ; au-delà de 1000 résultats par requête, on scinde la fenêtre
OVERLAP = timedelta(hours=1)
WINDOW = timedelta(hours=6)


def parse_hit(hit: dict) -> RawEvent | None:
    if not hit.get("objectID") or hit.get("created_at_i") is None:
        return None
    url = hit.get("url")
    return RawEvent(
        external_id=str(hit["objectID"]),
        published_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC),
        title=hit.get("title"),
        url=url,
        domain=(urlparse(url).hostname or None) if url else None,
        body=hit.get("story_text"),
        metrics={
            "points": hit.get("points") or 0,
            "num_comments": hit.get("num_comments") or 0,
            "author": hit.get("author"),
        },
    )


async def fetch_window(
    client: httpx.AsyncClient, start: datetime, end: datetime
) -> list[RawEvent]:
    """Toutes les stories de [start, end). Scinde la fenêtre si Algolia plafonne à 1000."""
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())}",
        "hitsPerPage": PAGE_SIZE,
    }
    first = await get_json(client, HN_URL, {**params, "page": 0})
    if first["nbHits"] > PAGE_SIZE and (end - start) > timedelta(minutes=1):
        mid = start + (end - start) / 2
        return await fetch_window(client, start, mid) + await fetch_window(client, mid, end)
    events = [e for h in first["hits"] if (e := parse_hit(h))]
    return events


async def collect(
    engine: Engine,
    source_id: int,
    since: datetime,
    until: datetime | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Collecte [since, until) par fenêtres de 6 h. Renvoie le nombre d'événements créés."""
    until = until or datetime.now(UTC)
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30)
    created = 0
    try:
        cursor = since
        while cursor < until:
            end = min(cursor + WINDOW, until)
            events = await fetch_window(client, cursor, end)
            with engine.begin() as conn:
                created += store_events(conn, source_id, events)
                mark_success(conn, source_id, {"last_created_at": end.isoformat()})
            log.info("hn.window", start=cursor.isoformat(), end=end.isoformat(), fetched=len(events))
            cursor = end
    except Exception:
        log.exception("hn.failed", source_id=source_id)
        with engine.begin() as conn:
            mark_failure(conn, source_id)
        raise
    finally:
        if own_client:
            await client.aclose()
    return created


def ensure_source(engine: Engine) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                """
                INSERT INTO sources (type, url, community, interval_minutes)
                VALUES ('hn', :url, :community, 10)
                ON CONFLICT (url) DO UPDATE SET url = EXCLUDED.url
                RETURNING id
                """
            ),
            {"url": HN_URL, "community": HN_COMMUNITY},
        ).scalar_one()


async def backfill(engine: Engine, days: int = 30, **kw) -> int:
    source_id = ensure_source(engine)
    return await collect(engine, source_id, datetime.now(UTC) - timedelta(days=days), **kw)


async def collect_latest(engine: Engine, **kw) -> int:
    """Passage périodique (toutes les 10 min) : repart du curseur moins le chevauchement."""
    source_id = ensure_source(engine)
    with engine.connect() as conn:
        cur = conn.execute(text("SELECT cursor FROM sources WHERE id = :i"), {"i": source_id}).scalar_one()
    since = (
        datetime.fromisoformat(cur["last_created_at"]) - OVERLAP
        if cur
        else datetime.now(UTC) - timedelta(days=1)
    )
    return await collect(engine, source_id, since, **kw)
