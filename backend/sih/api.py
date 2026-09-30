"""API publique en lecture seule (spec section 6)."""
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine, text

from sih import monitor
from sih.config import get_settings
from sih.db import make_engine

app = FastAPI(title="SIH API", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_methods=["GET"])

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    _engine = _engine or make_engine()
    return _engine


DETECTION_SQL = """
SELECT d.id, e.kind, e.name, d.first_detected_at, d.last_active_at, d.confidence, d.communities_moving,
       d.peak_ratio, d.status, d.explanation,
       (SELECT p.content_hash FROM publication_log p WHERE p.detection_id = d.id AND p.channel = 'site'
        ORDER BY p.id LIMIT 1) AS content_hash
FROM detections d JOIN entities e ON e.id = d.entity_id
WHERE cardinality(d.communities_moving) >= 2
  AND EXISTS (SELECT 1 FROM publication_log p WHERE p.detection_id = d.id AND p.channel = 'site')
"""


def _card(r) -> dict:
    n = len(r.communities_moving)
    now = datetime.now(UTC)
    return {
        "id": r.id, "entity": r.name, "kind": r.kind,
        "level": "red" if n >= 3 else "orange",
        "change_pct": round(float(r.peak_ratio or 0) * 100),
        "communities": list(r.communities_moving),
        "confidence": float(r.confidence),
        "status": r.status,
        "first_detected_at": r.first_detected_at.isoformat(),
        "age_seconds": max(0, int((now - r.first_detected_at).total_seconds())),
        "headline": (r.explanation or {}).get("headline"),
        "content_hash": r.content_hash,
    }


@app.get("/api/detections")
def list_detections(status: str = Query("open", pattern="^(open|closed|all)$"), limit: int = Query(20, ge=1, le=100),
                    engine: Engine = Depends(get_engine)):
    """`open` = détections actives (ouvertes ou expliquées), triées par confiance puis récence."""
    cond = {"open": "AND d.status <> 'closed'", "closed": "AND d.status = 'closed'", "all": ""}[status]
    with engine.connect() as conn:
        rows = conn.execute(text(DETECTION_SQL + cond + " ORDER BY d.confidence DESC, d.first_detected_at DESC LIMIT :n"),
                            {"n": limit}).all()
    return [_card(r) for r in rows]


@app.get("/api/detections/{detection_id}")
def get_detection(detection_id: int, engine: Engine = Depends(get_engine)):
    with engine.connect() as conn:
        r = conn.execute(text(DETECTION_SQL + " AND d.id = :i"), {"i": detection_id}).first()
        if not r:
            raise HTTPException(404, "detection not found")
        log_rows = conn.execute(
            text("SELECT channel, snapshot, content_hash, published_at FROM publication_log WHERE detection_id=:i ORDER BY id"),
            {"i": detection_id}).all()
    return {**_card(r), "explanation": r.explanation,
            "log": [{"channel": l.channel, "snapshot": l.snapshot, "content_hash": l.content_hash,
                     "published_at": l.published_at.isoformat()} for l in log_rows]}


@app.get("/api/detections/{detection_id}/series")
def get_series(detection_id: int, hours: int = Query(72, ge=6, le=336), engine: Engine = Depends(get_engine)):
    with engine.connect() as conn:
        det = conn.execute(text("SELECT entity_id, first_detected_at FROM detections WHERE id=:i"), {"i": detection_id}).first()
        if not det:
            raise HTTPException(404, "detection not found")
        end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=hours - 1)
        rows = conn.execute(
            text("SELECT community, hour, mentions FROM entity_hourly WHERE entity_id=:e AND hour >= :s AND hour <= :en"),
            {"e": det.entity_id, "s": start, "en": end}).all()
    grid = [start + timedelta(hours=i) for i in range(hours)]
    by: dict[str, dict] = {}
    for r in rows:
        by.setdefault(r.community, {})[r.hour] = r.mentions
    return {"hours": [h.isoformat() for h in grid], "first_detected_at": det.first_detected_at.isoformat(),
            "series": {c: [v.get(h, 0) for h in grid] for c, v in sorted(by.items())}}


@app.get("/api/log")
def public_log(limit: int = Query(100, ge=1, le=500), engine: Engine = Depends(get_engine)):
    """Journal immuable, du plus récent au plus ancien."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""SELECT p.id, p.detection_id, p.snapshot, p.content_hash, p.published_at
                    FROM publication_log p WHERE p.channel='site' ORDER BY p.id DESC LIMIT :n"""), {"n": limit}).all()
    return [{"id": r.id, "detection_id": r.detection_id, "snapshot": r.snapshot,
             "content_hash": r.content_hash, "published_at": r.published_at.isoformat()} for r in rows]


@app.get("/api/health")
def api_health(engine: Engine = Depends(get_engine)):
    return monitor.health(engine)
