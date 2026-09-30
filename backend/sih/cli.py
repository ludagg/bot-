import argparse
import asyncio

from alembic import command
from alembic.config import Config

from sih.collectors import hn
from sih.config import get_settings
from sih.db import make_engine
from sih.logging import configure_logging


def main() -> None:
    p = argparse.ArgumentParser(prog="sih")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="applique les migrations Alembic")
    b = sub.add_parser("hn-backfill", help="rejoue l'historique Hacker News")
    b.add_argument("--days", type=int, default=30)
    sub.add_parser("hn-collect", help="passage incrémental Hacker News")
    args = p.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if args.cmd == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        return
    engine = make_engine()
    if args.cmd == "hn-backfill":
        asyncio.run(hn.backfill(engine, args.days))
    elif args.cmd == "hn-collect":
        asyncio.run(hn.collect_latest(engine))


if __name__ == "__main__":
    main()
