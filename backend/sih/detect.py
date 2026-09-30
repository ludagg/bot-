"""Moteur d'anomalies (spec section 4) : score robuste médiane/MAD par communauté,
seuils minimaux, confiance multi-communautés, déduplication 48 h."""
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

WINDOW_HOURS = 6  # fenêtre courante ; exclue de la base de référence


@dataclass(frozen=True)
class DetectParams:
    baseline_days: int = 14
    min_mentions: int = 5      # mentions minimales sur la fenêtre courante
    min_ratio: float = 3.0     # au moins 3× la valeur attendue
    z_threshold: float = 4.0
    mad_floor: float = 0.25    # plancher de la MAD (entités rares)
    quiet_hours: int = 48      # calme requis avant d'ouvrir une nouvelle détection


@dataclass
class Result:
    entity_id: int
    kind: str
    name: str
    communities: list[str]
    z_max: float
    peak_ratio: float          # hausse relative : 6.38 = +638 %
    confidence: float
    detection_id: int | None = None
    is_new: bool = False
    level: str = "signal"      # signal (1 communauté) | orange (2) | red (≥3)
    moving: dict = field(default_factory=dict)


def robust_z(x: float, median: float, mad: float, floor: float) -> float:
    return 0.6745 * (x - median) / max(mad, floor)


def confidence(n_communities: int, z_max: float) -> float:
    """0.2 par communauté indépendante (plafonné à 4) + jusqu'à 0.2 selon l'intensité du z."""
    return round(min(0.999, 0.2 * min(n_communities, 4) + 0.2 * min(max(z_max, 0), 20) / 20), 3)


def level_of(n: int) -> str:
    return "red" if n >= 3 else "orange" if n == 2 else "signal"


def floor_hour(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def score_community(series: dict[datetime, int], cur_start: datetime, params: DetectParams):
    """Renvoie (bouge?, z, ratio_hausse) pour une communauté. `series` : heure → mentions."""
    cur = [series.get(cur_start + timedelta(hours=i), 0) for i in range(WINDOW_HOURS)]
    total = sum(cur)
    base_hours = params.baseline_days * 24
    base = [series.get(cur_start - timedelta(hours=i), 0) for i in range(1, base_hours + 1)]
    med = statistics.median(base)
    mad = statistics.median(abs(v - med) for v in base)
    z = robust_z(total / WINDOW_HOURS, med, mad, params.mad_floor)
    expected = max(WINDOW_HOURS * med, 1.0)
    ratio = total / expected
    moving = total >= params.min_mentions and ratio >= params.min_ratio and z >= params.z_threshold
    return moving, z, ratio - 1


def detect(conn: Connection, as_of: datetime | None = None, params: DetectParams | None = None,
           persist: bool = True) -> list[Result]:
    params = params or DetectParams()
    as_of = as_of or datetime.now(UTC)
    last = floor_hour(as_of)
    cur_start = last - timedelta(hours=WINDOW_HOURS - 1)
    base_start = cur_start - timedelta(hours=params.baseline_days * 24)

    rows = conn.execute(
        text(
            """SELECT h.entity_id, e.kind, e.name, h.community, h.hour, h.mentions
               FROM entity_hourly h JOIN entities e ON e.id = h.entity_id
               WHERE h.hour >= :base_start AND h.hour <= :last AND h.entity_id IN (
                 SELECT entity_id FROM entity_hourly WHERE hour >= :cur_start AND hour <= :last
                 GROUP BY entity_id HAVING sum(mentions) >= :min)"""
        ),
        {"base_start": base_start, "last": last, "cur_start": cur_start, "min": params.min_mentions},
    ).all()

    data: dict[int, dict] = {}
    for r in rows:
        ent = data.setdefault(r.entity_id, {"kind": r.kind, "name": r.name, "series": defaultdict(dict)})
        ent["series"][r.community][r.hour] = r.mentions

    results: list[Result] = []
    for eid, ent in data.items():
        moving = {}
        for community, series in ent["series"].items():
            ok, z, inc = score_community(series, cur_start, params)
            if ok:
                moving[community] = (z, inc)
        if not moving:
            continue
        z_max = max(z for z, _ in moving.values())
        comms = sorted(moving)
        results.append(
            Result(eid, ent["kind"], ent["name"], comms, round(z_max, 2),
                   round(max(i for _, i in moving.values()), 3), confidence(len(comms), z_max),
                   level=level_of(len(comms)), moving=moving)
        )
    if persist:
        _persist(conn, results, as_of, params)
        _close_stale(conn, as_of, params)
    return sorted(results, key=lambda r: (-r.confidence, r.name))


def _persist(conn: Connection, results: list[Result], as_of: datetime, params: DetectParams) -> None:
    quiet = timedelta(hours=params.quiet_hours)
    for r in results:
        if len(r.communities) < 2:
            continue  # signal discret : jamais stocké ni publié
        active = conn.execute(
            text("""SELECT id, communities_moving FROM detections
                    WHERE entity_id = :e AND status <> 'closed' AND last_active_at > :since
                    ORDER BY first_detected_at DESC LIMIT 1 FOR UPDATE"""),
            {"e": r.entity_id, "since": as_of - quiet},
        ).first()
        if active:
            merged = sorted(set(active.communities_moving) | set(r.communities))
            conn.execute(
                text("""UPDATE detections SET last_active_at = :now, communities_moving = :c,
                          confidence = GREATEST(confidence, :conf), peak_ratio = GREATEST(peak_ratio, :pr)
                        WHERE id = :id"""),
                {"now": as_of, "c": merged, "conf": confidence(len(merged), r.z_max), "pr": r.peak_ratio,
                 "id": active.id},
            )
            r.detection_id = active.id
            r.communities = merged
            r.level = level_of(len(merged))
        else:
            r.detection_id = conn.execute(
                text("""INSERT INTO detections (entity_id, first_detected_at, confidence, communities_moving,
                                                peak_ratio, last_active_at)
                        VALUES (:e, :t, :conf, :c, :pr, :t)
                        ON CONFLICT (entity_id, first_detected_at) DO UPDATE SET last_active_at = EXCLUDED.last_active_at
                        RETURNING id"""),
                {"e": r.entity_id, "t": as_of, "conf": r.confidence, "c": r.communities, "pr": r.peak_ratio},
            ).scalar_one()
            r.is_new = True


def _close_stale(conn: Connection, as_of: datetime, params: DetectParams) -> None:
    conn.execute(
        text("UPDATE detections SET status = 'closed' WHERE status <> 'closed' AND last_active_at <= :t"),
        {"t": as_of - timedelta(hours=params.quiet_hours)},
    )


def replay(conn: Connection, start: datetime, end: datetime, step_hours: int = 1,
           params: DetectParams | None = None) -> dict[tuple[str, str], tuple[datetime, Result]]:
    """Rejoue l'historique sans rien écrire : première détection (≥ 2 communautés) par entité."""
    first: dict[tuple[str, str], tuple[datetime, Result]] = {}
    t = start
    while t <= end:
        for r in detect(conn, t, params, persist=False):
            if len(r.communities) >= 2:
                first.setdefault((r.kind, r.name), (t, r))
        t += timedelta(hours=step_hours)
    return first
