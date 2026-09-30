"""Planificateur : lit sources.next_fetch_at, réserve les sources dues (bail en base,
sans transaction longue), les collecte avec limite de débit par domaine."""
import asyncio
from collections import defaultdict
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import Engine, text

from sih.collectors import github, hn, rss
from sih.config import get_settings

log = structlog.get_logger()

LEASE_MINUTES = 5
GLOBAL_CONCURRENCY = 50
DOMAIN_CONCURRENCY = 2
DOMAIN_DELAY = 0.5

CLAIM_SQL = text(
    """
    UPDATE sources SET next_fetch_at = now() + make_interval(mins => :lease)
    WHERE id IN (
      SELECT id FROM sources WHERE active AND next_fetch_at <= now()
      ORDER BY next_fetch_at LIMIT :limit FOR UPDATE SKIP LOCKED
    )
    RETURNING id, type, url, community, interval_minutes, etag, last_modified, cursor
    """
)


def claim_due(engine: Engine, limit: int = 500) -> list[dict]:
    with engine.begin() as conn:
        return [dict(r._mapping) for r in conn.execute(CLAIM_SQL, {"lease": LEASE_MINUTES, "limit": limit})]


async def run_due(engine: Engine, limit: int = 500, client: httpx.AsyncClient | None = None) -> dict[str, int]:
    """Collecte toutes les sources dues. Un collecteur en échec ne bloque jamais les autres."""
    sources = claim_due(engine, limit)
    if not sources:
        return {}
    token = get_settings().github_token
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=GLOBAL_CONCURRENCY))
    gate = asyncio.Semaphore(GLOBAL_CONCURRENCY)
    domain_gates: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(DOMAIN_CONCURRENCY))

    async def one(src: dict) -> tuple[str, int]:
        domain = urlparse(src["url"]).hostname or ""
        async with gate, domain_gates[domain]:
            try:
                if src["type"] == "hn":
                    n = await hn.collect_source(engine, src, client)
                elif src["type"] == "github":
                    n = await github.collect(engine, src, client, token)
                elif src["type"] == "rss":
                    n = await rss.collect(engine, src, client)
                else:
                    log.warning("scheduler.unknown_type", type=src["type"])
                    n = 0
            except Exception:
                log.exception("scheduler.source_crashed", source_id=src["id"])
                n = 0
            await asyncio.sleep(DOMAIN_DELAY)
            return src["type"], n

    try:
        results = await asyncio.gather(*(one(s) for s in sources))
    finally:
        if own:
            await client.aclose()
    out: dict[str, int] = defaultdict(int)
    for t, n in results:
        out[t] += n
    log.info("scheduler.cycle", sources=len(sources), created=dict(out))
    return dict(out)
