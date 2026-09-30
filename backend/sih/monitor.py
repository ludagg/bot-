"""Monitoring minimal : sources muettes, coût LLM, détections par jour."""
import httpx
import structlog
from sqlalchemy import Engine, text

from sih.config import Settings

log = structlog.get_logger()
SILENT_HOURS = 2


def health(engine: Engine) -> dict:
    with engine.connect() as conn:
        sources = conn.execute(
            text("""SELECT type, count(*) FILTER (WHERE active) AS active, count(*) FILTER (WHERE NOT active) AS disabled,
                           max(last_success_at) AS last_success,
                           count(*) FILTER (WHERE active AND (last_success_at IS NULL
                                 OR last_success_at < now() - make_interval(hours => :h))) AS silent
                    FROM sources GROUP BY type ORDER BY type"""), {"h": SILENT_HOURS}).all()
        llm = conn.execute(
            text("""SELECT count(*), COALESCE(sum(cost_usd),0) FROM llm_usage
                    WHERE created_at >= date_trunc('day', now())""")).one()
        dets = conn.execute(
            text("""SELECT date_trunc('day', first_detected_at)::date AS day, count(*) FROM detections
                    WHERE first_detected_at > now() - interval '14 days' GROUP BY 1 ORDER BY 1 DESC""")).all()
    return {
        "sources": [{"type": s.type, "active": s.active, "disabled": s.disabled, "silent": s.silent,
                     "last_success": s.last_success.isoformat() if s.last_success else None} for s in sources],
        "llm_today": {"calls": llm[0], "cost_usd": float(llm[1])},
        "detections_per_day": [{"day": str(d.day), "count": d.count} for d in dets],
    }


def silent_sources(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            text("""SELECT id, type, url FROM sources
                    WHERE active AND last_success_at IS NOT NULL
                      AND last_success_at < now() - make_interval(hours => :h)
                      AND type IN ('hn', 'github')"""), {"h": SILENT_HOURS})]


def alert(settings: Settings, message: str) -> bool:
    """Alerte Telegram ; sans configuration, journalise seulement."""
    log.warning("alert", message=message)
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                   json={"chat_id": settings.telegram_chat_id, "text": message}, timeout=15)
    return r.is_success


def check_and_alert(engine: Engine, settings: Settings) -> int:
    """Alerte si un collecteur clé (HN, GitHub) est muet depuis plus de 2 h, ou si des sources sont désactivées."""
    bad = silent_sources(engine)
    for s in bad:
        alert(settings, f"SIH : source {s['type']} muette depuis plus de {SILENT_HOURS} h ({s['url']})")
    with engine.connect() as conn:
        disabled = conn.execute(text("SELECT count(*) FROM sources WHERE NOT active")).scalar_one()
    if disabled:
        alert(settings, f"SIH : {disabled} source(s) désactivée(s) après échecs répétés")
    return len(bad)
