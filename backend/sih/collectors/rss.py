"""Collecteur RSS/Atom générique : requêtes conditionnelles (ETag / If-Modified-Since),
intervalle adaptatif selon la fréquence de publication."""
import asyncio
import hashlib
import re
from calendar import timegm
from datetime import UTC, datetime
from urllib.parse import urlparse

import feedparser
import httpx
import structlog
from sqlalchemy import Engine

from sih.collectors.base import RawEvent, mark_failure, mark_success, store_events

log = structlog.get_logger()

TAG_RE = re.compile(r"<[^>]+>")
MIN_INTERVAL, MAX_INTERVAL = 15, 1440
UA = "SIH-bot/0.1 (+https://github.com/ludagg/bot-)"


def adapt_interval(current: int, new_items: int) -> int:
    """Flux actif → relevé plus souvent ; flux muet → relevé moins souvent."""
    if new_items > 0:
        return max(MIN_INTERVAL, current // 2)
    return min(MAX_INTERVAL, int(current * 1.5))


def _ts(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(timegm(t), tz=UTC)
    return None


def parse_feed(content: bytes, now: datetime | None = None) -> list[RawEvent]:
    now = now or datetime.now(UTC)
    events = []
    for e in feedparser.parse(content).entries:
        link = e.get("link")
        title = (e.get("title") or "").strip() or None
        ext = e.get("id") or link or (hashlib.sha1((title or "").encode()).hexdigest() if title else None)
        if not ext:
            continue
        # Date absente : `now` (la première valeur vue est conservée, cf. store_events).
        published = min(_ts(e) or now, now)
        summary = TAG_RE.sub(" ", e.get("summary") or "").strip()[:500] or None
        events.append(
            RawEvent(
                external_id=str(ext)[:500],
                published_at=published,
                title=title,
                url=link,
                domain=(urlparse(link).hostname or None) if link else None,
                body=summary,
            )
        )
    return events


async def collect(engine: Engine, source: dict, client: httpx.AsyncClient) -> int:
    sid = source["id"]
    headers = {"User-Agent": UA}
    if source.get("etag"):
        headers["If-None-Match"] = source["etag"]
    if source.get("last_modified"):
        headers["If-Modified-Since"] = source["last_modified"]
    try:
        resp = await client.get(source["url"], headers=headers, follow_redirects=True)
        if resp.status_code == 304:
            with engine.begin() as conn:
                mark_success(conn, sid, interval_minutes=adapt_interval(source["interval_minutes"], 0))
            return 0
        resp.raise_for_status()
        events = await asyncio.to_thread(parse_feed, resp.content)
        with engine.begin() as conn:
            created = store_events(conn, sid, events)
            mark_success(
                conn,
                sid,
                interval_minutes=adapt_interval(source["interval_minutes"], created),
                etag=resp.headers.get("etag"),
                last_modified=resp.headers.get("last-modified"),
            )
        return created
    except Exception:
        log.warning("rss.failed", source_id=sid, url=source["url"], exc_info=True)
        with engine.begin() as conn:
            mark_failure(conn, sid)
        return 0
