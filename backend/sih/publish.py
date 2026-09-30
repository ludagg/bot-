"""Journal public immuable et file de publication X (spec section 7)."""
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import Connection, Engine, text

from sih.config import Settings

log = structlog.get_logger()

TWEET_TEMPLATE = ("SOMETHING IS HAPPENING — Activity around {entity} increased {pct}% in {hours}h. "
                  "First detected: {when}. Details: {link}")
UPDATE_TEMPLATE = "UPDATE — Analysis available for {entity}. Details: {link}"
SAFE_ENTITY = re.compile(r"[^a-z0-9 .+#/-]")


def canonical_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def safe_entity(name: str) -> str:
    """Le texte X est un gabarit fixe : l'entité (donnée non fiable) est réduite à un jeu de caractères sûr."""
    return SAFE_ENTITY.sub("", name.lower())[:60].strip() or "unknown"


def tweet_text(det, settings: Settings, hours: int = 6) -> str:
    return TWEET_TEMPLATE.format(
        entity=safe_entity(det.name), pct=round(float(det.peak_ratio or 0) * 100), hours=hours,
        when=det.first_detected_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        link=f"{settings.site_base_url.rstrip('/')}/d/{det.id}",
    )


def _data_start(conn: Connection) -> datetime | None:
    return conn.execute(text("SELECT min(hour) FROM entity_hourly")).scalar()


def _insert_log(conn: Connection, detection_id: int, channel: str, snapshot: dict) -> None:
    conn.execute(
        text("INSERT INTO publication_log (detection_id, channel, snapshot, content_hash) VALUES (:d,:c,CAST(:s AS jsonb),:h)"),
        {"d": detection_id, "c": channel, "s": json.dumps(snapshot, sort_keys=True), "h": canonical_hash(snapshot)},
    )


def publish_pending(engine: Engine, settings: Settings) -> dict[str, int]:
    """Publie sur le site (journal) puis met en file les messages X. Ne modifie jamais l'existant."""
    out = {"site": 0, "update": 0, "x": 0}
    with engine.begin() as conn:
        start = _data_start(conn)
        if start is None:
            return out
        # Cold start : rien n'est publié avant N jours de données.
        eligible_from = start + timedelta(days=settings.publish_min_history_days)
        dets = conn.execute(
            text("""SELECT d.id, e.name, e.kind, d.first_detected_at, d.confidence, d.communities_moving,
                           d.peak_ratio, d.explanation,
                           EXISTS (SELECT 1 FROM publication_log p WHERE p.detection_id = d.id AND p.channel='site'
                                   AND p.snapshot->>'kind' = 'detection') AS published,
                           EXISTS (SELECT 1 FROM publication_log p WHERE p.detection_id = d.id AND p.channel='site'
                                   AND p.snapshot->>'kind' = 'update') AS updated
                    FROM detections d JOIN entities e ON e.id = d.entity_id
                    WHERE cardinality(d.communities_moving) >= 2 AND d.first_detected_at >= :from
                    ORDER BY d.first_detected_at"""),
            {"from": eligible_from},
        ).all()
        for d in dets:
            base = {"detection_id": d.id, "entity": {"kind": d.kind, "name": d.name},
                    "first_detected_at": d.first_detected_at.isoformat(),
                    "communities": list(d.communities_moving), "peak_ratio": float(d.peak_ratio or 0)}
            if not d.published:
                _insert_log(conn, d.id, "site", {**base, "kind": "detection", "confidence": float(d.confidence)})
                out["site"] += 1
            if d.explanation and not d.updated:
                _insert_log(conn, d.id, "site", {**base, "kind": "update", "headline": d.explanation.get("headline"),
                                                 "timeline_points": len(d.explanation.get("timeline", []))})
                out["update"] += 1
            out["x"] += _queue_x(conn, d, settings)
    return out


def _queue_x(conn: Connection, d, settings: Settings) -> int:
    if settings.x_mode == "off" or len(d.communities_moving) < 3:
        return 0
    queued = 0
    has_tweet = conn.execute(text("SELECT status FROM x_outbox WHERE detection_id=:i AND kind='tweet'"), {"i": d.id}).scalar()
    if has_tweet is None:
        today = conn.execute(
            text("SELECT count(*) FROM x_outbox WHERE kind='tweet' AND created_at >= date_trunc('day', now())")).scalar_one()
        if today < settings.x_daily_cap:
            conn.execute(text("INSERT INTO x_outbox (detection_id, kind, text) VALUES (:i,'tweet',:t)"),
                         {"i": d.id, "t": tweet_text(d, settings)})
            queued += 1
    elif has_tweet != "rejected" and d.explanation:
        r = conn.execute(
            text("INSERT INTO x_outbox (detection_id, kind, text) VALUES (:i,'update',:t) ON CONFLICT DO NOTHING"),
            {"i": d.id, "t": UPDATE_TEMPLATE.format(entity=safe_entity(d.name),
                                                   link=f"{settings.site_base_url.rstrip('/')}/d/{d.id}")})
        queued += r.rowcount
    return queued


class XClient:
    def __init__(self, s: Settings):
        import tweepy
        self.client = tweepy.Client(consumer_key=s.x_api_key, consumer_secret=s.x_api_secret,
                                    access_token=s.x_access_token, access_token_secret=s.x_access_secret)

    def post(self, text_: str) -> str:
        return str(self.client.create_tweet(text=text_).data["id"])


def send_outbox(engine: Engine, settings: Settings, client=None, only_ids: list[int] | None = None) -> int:
    """Envoie les messages en attente. Mode `auto` : tout ; mode `review` : seulement `only_ids` (approuvés)."""
    if settings.x_mode == "off":
        return 0
    if settings.x_mode == "review" and not only_ids:
        return 0
    with engine.connect() as conn:
        rows = conn.execute(
            text("""SELECT id, text FROM x_outbox WHERE status='pending'
                    AND (:all OR id = ANY(:ids)) ORDER BY id"""),
            {"all": settings.x_mode == "auto", "ids": only_ids or []},
        ).all()
    sent = 0
    for r in rows:
        client = client or XClient(settings)
        try:
            tweet_id, status = client.post(r.text), "sent"
        except Exception:
            log.exception("x.post_failed", outbox_id=r.id)
            tweet_id, status = None, "failed"
        with engine.begin() as conn:
            conn.execute(text("UPDATE x_outbox SET status=:s, tweet_id=:t, sent_at=now() WHERE id=:i"),
                         {"s": status, "t": tweet_id, "i": r.id})
        sent += status == "sent"
    return sent
