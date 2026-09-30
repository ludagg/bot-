"""Briques communes des collecteurs : backoff, stockage idempotent, état de source."""
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import Connection, text

log = structlog.get_logger()

RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RawEvent:
    external_id: str
    published_at: datetime
    title: str | None = None
    url: str | None = None
    domain: str | None = None
    body: str | None = None
    metrics: dict[str, Any] | None = None


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    retries: int = 5,
    base_delay: float = 1.0,
) -> Any:
    """GET JSON avec backoff exponentiel sur 429/5xx et erreurs réseau."""
    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
                reset = int(resp.headers.get("x-ratelimit-reset", "0"))
                wait = max(1.0, min(reset - time.time(), 90.0))
                if attempt < retries:
                    log.warning("rate_limited", url=url, wait=wait)
                    await asyncio.sleep(wait)
                    continue
            if resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()
                return resp.json()
            reason = f"http {resp.status_code}"
        except httpx.TransportError as exc:
            reason = type(exc).__name__
        if attempt == retries:
            raise RuntimeError(f"échec après {retries + 1} tentatives : {url} ({reason})")
        delay = base_delay * 2**attempt
        log.warning("retry", url=url, reason=reason, attempt=attempt + 1, delay=delay)
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


# Idempotent : (source_id, external_id) déjà connu → seules les métriques sont rafraîchies.
# La ligne existante est retrouvée sans dépendre de published_at (clé de partition).
UPSERT_SQL = text(
    """
    WITH existing AS (
      UPDATE raw_events SET metrics = :metrics
      WHERE source_id = :source_id AND external_id = :external_id
      RETURNING 1
    )
    INSERT INTO raw_events (source_id, external_id, title, url, domain, body, metrics, published_at)
    SELECT :source_id, :external_id, :title, :url, :domain, :body, :metrics, :published_at
    WHERE NOT EXISTS (SELECT 1 FROM existing)
    RETURNING id
    """
)


def store_events(conn: Connection, source_id: int, events: list[RawEvent]) -> int:
    """Insère les événements nouveaux ; renvoie le nombre de lignes créées."""
    import json

    created = 0
    for ev in events:
        row = conn.execute(
            UPSERT_SQL,
            {
                "source_id": source_id,
                "external_id": ev.external_id,
                "title": ev.title,
                "url": ev.url,
                "domain": ev.domain,
                "body": ev.body,
                "metrics": json.dumps(ev.metrics) if ev.metrics is not None else None,
                "published_at": ev.published_at,
            },
        ).first()
        created += row is not None
    return created


def mark_success(
    conn: Connection,
    source_id: int,
    cursor: dict | None = None,
    interval_minutes: int | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    import json

    conn.execute(
        text(
            """
            UPDATE sources SET last_success_at = now(), error_count = 0,
              interval_minutes = COALESCE(:interval, interval_minutes),
              etag = COALESCE(:etag, etag), last_modified = COALESCE(:lm, last_modified),
              next_fetch_at = now() + make_interval(mins => COALESCE(:interval, interval_minutes)),
              cursor = COALESCE(CAST(:cursor AS jsonb), cursor)
            WHERE id = :id
            """
        ),
        {
            "id": source_id,
            "cursor": json.dumps(cursor) if cursor else None,
            "interval": interval_minutes,
            "etag": etag,
            "lm": last_modified,
        },
    )


MAX_ERRORS = 10


def mark_failure(conn: Connection, source_id: int) -> None:
    """Backoff sur next_fetch_at ; désactivation après MAX_ERRORS échecs consécutifs."""
    conn.execute(
        text(
            """
            UPDATE sources SET error_count = error_count + 1,
              active = (error_count + 1) < :max_errors,
              next_fetch_at = now() + make_interval(mins => LEAST(interval_minutes * power(2, error_count + 1), 1440)::int)
            WHERE id = :id
            """
        ),
        {"id": source_id, "max_errors": MAX_ERRORS},
    )
