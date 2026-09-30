"""Import de sources dans le registre (OPML ou CSV `url,community`)."""
import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy import Engine, text

INSERT = text(
    """INSERT INTO sources (type, url, community, interval_minutes)
       VALUES ('rss', :url, :community, 60) ON CONFLICT (url) DO NOTHING"""
)


def read_opml(path: Path, community: str) -> list[tuple[str, str]]:
    """Les catégories OPML (outline sans xmlUrl) servent de communauté si présentes."""
    rows: list[tuple[str, str]] = []

    def walk(node: ET.Element, cat: str) -> None:
        for o in node.findall("outline"):
            url = o.get("xmlUrl")
            if url:
                rows.append((url.strip(), cat))
            else:
                walk(o, (o.get("title") or o.get("text") or cat).strip().lower().replace(" ", "-"))

    walk(ET.parse(path).getroot().find("body"), community)
    return rows


def read_csv(path: Path) -> list[tuple[str, str]]:
    with path.open(newline="") as f:
        return [(r[0].strip(), r[1].strip()) for r in csv.reader(f) if r and not r[0].startswith("#")]


def import_sources(engine: Engine, path: Path, community: str = "general") -> int:
    rows = read_opml(path, community) if path.suffix in (".opml", ".xml") else read_csv(path)
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM sources")).scalar_one()
        conn.execute(INSERT, [{"url": u, "community": c} for u, c in rows])
        after = conn.execute(text("SELECT count(*) FROM sources")).scalar_one()
    return after - before
