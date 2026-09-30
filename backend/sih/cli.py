import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from sih import detect, publish, worker
from sih.collectors import github, hn
from sih.config import get_settings
from sih.db import make_engine
from sih.logging import configure_logging
from sih.sources import import_sources


def main() -> None:
    p = argparse.ArgumentParser(prog="sih")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="applique les migrations Alembic")
    sub.add_parser("worker", help="boucle principale (collecte, détection, analyse, publication)")
    sub.add_parser("analytics", help="un cycle agrégation → détection → analyse → publication")
    b = sub.add_parser("hn-backfill", help="rejoue l'historique Hacker News")
    b.add_argument("--days", type=int, default=30)
    sub.add_parser("hn-collect", help="passage incrémental Hacker News")
    sub.add_parser("github-collect", help="passage GitHub (nouveaux dépôts)")
    i = sub.add_parser("import-sources", help="importe des flux RSS (OPML ou CSV url,community)")
    i.add_argument("file", type=Path)
    i.add_argument("--community", default="general", help="communauté par défaut pour un OPML")
    r = sub.add_parser("replay", help="rejoue l'historique du moteur (sans rien écrire)")
    r.add_argument("--days", type=int, default=14)
    r.add_argument("--step-hours", type=int, default=1)
    sub.add_parser("x-list", help="messages X en attente")
    a = sub.add_parser("x-approve", help="approuve et envoie des messages X (mode review)")
    a.add_argument("ids", type=int, nargs="+")
    sub.add_parser("health", help="état des collecteurs")
    args = p.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if args.cmd == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        return
    if args.cmd == "worker":
        asyncio.run(worker.main())
        return
    engine = make_engine()
    if args.cmd == "hn-backfill":
        asyncio.run(hn.backfill(engine, args.days))
    elif args.cmd == "hn-collect":
        asyncio.run(hn.collect_latest(engine))
    elif args.cmd == "github-collect":
        import httpx

        async def go():
            sid = github.ensure_source(engine)
            with engine.connect() as c:
                src = dict(c.execute(text("SELECT id, url, interval_minutes FROM sources WHERE id=:i"), {"i": sid}).one()._mapping)
            async with httpx.AsyncClient(timeout=30) as client:
                print(await github.collect(engine, src, client, settings.github_token))
        asyncio.run(go())
    elif args.cmd == "import-sources":
        print(f"{import_sources(engine, args.file, args.community)} sources ajoutées")
    elif args.cmd == "analytics":
        print(worker.analytics_cycle(engine, settings))
    elif args.cmd == "replay":
        end = datetime.now(UTC)
        with engine.connect() as conn:
            found = detect.replay(conn, end - timedelta(days=args.days), end, args.step_hours,
                                  detect.DetectParams(baseline_days=settings.baseline_days))
        for (kind, name), (t, res) in sorted(found.items(), key=lambda kv: kv[1][0]):
            print(f"{t:%Y-%m-%d %H:%M}  {res.level:6} conf={res.confidence}  {kind}:{name}  {res.communities}")
    elif args.cmd == "x-list":
        with engine.connect() as conn:
            for r in conn.execute(text("SELECT id, kind, status, text FROM x_outbox WHERE status='pending' ORDER BY id")):
                print(f"[{r.id}] {r.kind}: {r.text}")
    elif args.cmd == "x-approve":
        print(f"{publish.send_outbox(engine, settings, only_ids=args.ids)} envoyé(s)")
    elif args.cmd == "health":
        import json

        from sih import monitor
        print(json.dumps(monitor.health(engine), indent=2))


if __name__ == "__main__":
    main()
