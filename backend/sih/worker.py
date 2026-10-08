"""Boucle principale : collecte → agrégation → détection → analyse → publication."""
import asyncio
from datetime import datetime

import structlog
from sqlalchemy import Engine, text

from sih import analysis, detect, entities, monitor, publish, scheduler
from sih.analysis import AnthropicLLM
from sih.config import Settings, get_settings
from sih.db import make_engine
from sih.logging import configure_logging

log = structlog.get_logger()
RAW_RETENTION_DAYS = 31
HOURLY_RETENTION_DAYS = 60


def analytics_cycle(engine: Engine, settings: Settings, llm=None, as_of: datetime | None = None) -> dict:
    with engine.begin() as conn:
        n_events = entities.aggregate(conn, as_of)
    with engine.begin() as conn:
        results = detect.detect(conn, as_of, detect.DetectParams(baseline_days=settings.baseline_days))
    if llm is None and settings.anthropic_api_key:
        llm = AnthropicLLM(settings)
    explained = analysis.analyze_pending(engine, llm, settings)
    published = publish.publish_pending(engine, settings)
    sent = publish.send_outbox(engine, settings) if settings.x_mode == "auto" else 0
    summary = {"events": n_events, "detections": sum(len(r.communities) >= 2 for r in results),
               "explained": explained, "published": published, "x_sent": sent}
    log.info("analytics.cycle", **summary)
    return summary


def maintenance(engine: Engine) -> None:
    """Partitions mensuelles, purge du brut à 30 jours, rétention des compteurs."""
    with engine.begin() as conn:
        for parent in ("raw_events", "entity_hourly"):
            conn.execute(text("SELECT ensure_month_partitions(:p, (now() - interval '1 month')::date, (now() + interval '3 months')::date)"),
                         {"p": parent})
        conn.execute(text("DELETE FROM raw_events WHERE published_at < now() - make_interval(days => :d)"), {"d": RAW_RETENTION_DAYS})
        conn.execute(text("DELETE FROM entity_hourly WHERE hour < now() - make_interval(days => :d)"), {"d": HOURLY_RETENTION_DAYS})


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = make_engine()
    from sih.collectors import github, hn
    hn.ensure_source(engine)
    github.ensure_source(engine)
    maintenance(engine)
    last_analytics = last_maint = last_alert = 0.0
    loop = asyncio.get_running_loop()
    while True:
        now = loop.time()
        try:
            await scheduler.run_due(engine)
            if now - last_analytics >= 300:  # toutes les 5 min
                await asyncio.to_thread(analytics_cycle, engine, settings)
                last_analytics = now
            if now - last_maint >= 6 * 3600:
                await asyncio.to_thread(maintenance, engine)
                last_maint = now
            if now - last_alert >= 1800:
                await asyncio.to_thread(monitor.check_and_alert, engine, settings)
                last_alert = now
        except Exception:
            log.exception("worker.cycle_failed")  # un cycle raté n'arrête jamais la boucle
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
