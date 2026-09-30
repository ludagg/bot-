"""Extraction d'entités (mots-clés, dépôts, domaines), normalisation et comptage horaire."""
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

STOPWORDS = frozenset(
    """a about after all also an and any are as at be been but by can could did do does for from get got
    had has have how i if in into is it its just like make may more most new no not of on one only or our
    out over per re s so some than that the their them then there these they this to up us use used using
    via was we were what when where which who why will with you your
    show hn ask launch tell best top free now today first last
    le la les un une des du de et en est pour que qui dans sur par avec ce cette ces au aux ne pas plus
    """.split()
)
SHORT_OK = frozenset({"ai", "ml", "go", "js", "ts", "os", "c#", "vr", "ar"})
ALIASES = {"gpt4": "gpt-4"}
IGNORED_DOMAINS = frozenset({"github.com"})  # couvert par les entités « repo »
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+#-]*")
REPO_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
NON_REPO_OWNERS = frozenset({"orgs", "topics", "sponsors", "features", "about", "settings", "marketplace"})

Entity = tuple[str, str]  # (kind, name)


def _singular(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokens(text_: str) -> list[str | None]:
    """Jetons normalisés ; None marque un mot vide (coupe les n-grammes)."""
    out: list[str | None] = []
    for raw in TOKEN_RE.findall(text_.lower()):
        tok = raw.rstrip(".-")
        if not tok.endswith(("++", "#")):
            tok = tok.rstrip("+")
        if tok in STOPWORDS or tok.isdigit() or (len(tok) < 3 and tok not in SHORT_OK):
            out.append(None)
            continue
        out.append(_singular(tok))
    return out


PHRASES = [(re.compile(r"\blarge language models?\b"), "llm"), (re.compile(r"\bartificial intelligence\b"), "ai"),
           (re.compile(r"\bmachine learning\b"), "ml")]


def keywords(text_: str) -> set[str]:
    text_ = text_.lower()
    for pat, repl in PHRASES:
        text_ = pat.sub(repl, text_)
    toks = tokens(text_)
    found: set[str] = {t for t in toks if t}
    for a, b in zip(toks, toks[1:]):
        if a and b:
            found.add(f"{a} {b}")
    return {ALIASES.get(k, k) for k in found}


def normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    d = domain.lower().strip().removeprefix("www.")
    return d if d and d not in IGNORED_DOMAINS else None


def repos(*fields: str | None) -> set[str]:
    found = set()
    for f in fields:
        for owner, name in REPO_RE.findall(f or ""):
            name = name.removesuffix(".git").rstrip(".")
            if owner.lower() not in NON_REPO_OWNERS and name:
                found.add(f"{owner}/{name}".lower())
    return found


def extract(source_type: str, title: str | None, url: str | None, domain: str | None, body: str | None) -> set[Entity]:
    ents: set[Entity] = {("keyword", k) for k in keywords(f"{title or ''} {(body or '')[:300]}")}
    if source_type == "github" and title and "/" in title:
        ents.add(("repo", title.lower()))
    ents |= {("repo", r) for r in repos(url, title, body)}
    if (d := normalize_domain(domain)):
        ents.add(("domain", d))
    return ents


# --- Agrégation incrémentale -------------------------------------------------

WATERMARK_KEY = "aggregate_last_id"
BATCH = 5000


def _hour(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def aggregate(conn: Connection, now: datetime | None = None) -> int:
    """Ajoute les nouveaux événements bruts à entity_hourly (par communauté, par heure).

    Progresse par identifiant croissant ; les événements de moins de 30 s sont laissés au
    passage suivant (transactions de collecte en cours). Renvoie le nombre d'événements traités.
    """
    now = now or datetime.now(UTC)
    last_id = conn.execute(text("SELECT value FROM kv_state WHERE key = :k"), {"k": WATERMARK_KEY}).scalar()
    last_id = int(last_id) if last_id is not None else 0
    processed = 0
    while True:
        rows = conn.execute(
            text(
                """SELECT r.id, r.title, r.url, r.domain, r.body, r.published_at, r.collected_at,
                          s.community, s.type
                   FROM raw_events r JOIN sources s ON s.id = r.source_id
                   WHERE r.id > :last AND r.collected_at <= :cutoff ORDER BY r.id LIMIT :n"""
            ),
            {"last": last_id, "cutoff": now - timedelta(seconds=30), "n": BATCH},
        ).all()
        if not rows:
            break
        counts: Counter[tuple[str, str, str, datetime]] = Counter()
        for r in rows:
            hour = _hour(min(r.published_at, r.collected_at))  # dates futures ramenées à la collecte
            for kind, name in extract(r.type, r.title, r.url, r.domain, r.body):
                counts[(kind, name, r.community, hour)] += 1
        _flush(conn, counts)
        last_id = rows[-1].id
        processed += len(rows)
    conn.execute(
        text("INSERT INTO kv_state (key, value) VALUES (:k, to_jsonb(CAST(:v AS bigint))) "
             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
        {"k": WATERMARK_KEY, "v": last_id},
    )
    return processed


def _flush(conn: Connection, counts: Counter) -> None:
    if not counts:
        return
    keys = list({(k, n) for k, n, _, _ in counts})
    conn.execute(
        text("INSERT INTO entities (kind, name) SELECT * FROM unnest(CAST(:k AS text[]), CAST(:n AS text[])) "
             "ON CONFLICT DO NOTHING"),
        {"k": [k for k, _ in keys], "n": [n for _, n in keys]},
    )
    ids = {
        (r.kind, r.name): r.id
        for r in conn.execute(
            text("SELECT id, kind, name FROM entities WHERE (kind, name) IN "
                 "(SELECT * FROM unnest(CAST(:k AS text[]), CAST(:n AS text[])))"),
            {"k": [k for k, _ in keys], "n": [n for _, n in keys]},
        )
    }
    conn.execute(
        text("INSERT INTO entity_hourly (entity_id, community, hour, mentions) VALUES (:e, :c, :h, :m) "
             "ON CONFLICT (entity_id, community, hour) DO UPDATE "
             "SET mentions = entity_hourly.mentions + EXCLUDED.mentions"),
        [{"e": ids[(k, n)], "c": c, "h": h, "m": m} for (k, n, c, h), m in counts.items()],
    )
