import argparse
import importlib
from pathlib import Path

from ea import fixtures, search, tiles
from ea.apply import apply, publish, revert
from ea.store import connect


def main() -> None:
    parser = argparse.ArgumentParser(prog="ea")
    commands = parser.add_subparsers(dest="command", required=True)
    for step in ("fetch", "parse", "emit"):
        commands.add_parser(step).add_argument("source", help="registry name, such as ca.elections")
    commands.add_parser("apply").add_argument("changeset", type=Path, help="changeset artifact directory")
    commands.add_parser("publish").add_argument("changeset_id", help="a changeset in pending_review")
    reverting = commands.add_parser(
        "revert", help="reject an unpublished changeset or undo a published one no later changeset builds on"
    )
    reverting.add_argument("changeset_id")
    reverting.add_argument("reason")
    commands.add_parser("fixtures", help="export the engine's golden-test fixtures from the database")
    commands.add_parser("tiles", help="build one PMTiles archive per boundary set").add_argument(
        "scheme", nargs="?", help="scheme_code prefix, such as tr or tr.il; omit to build every set"
    )
    commands.add_parser("search", help="rebuild the search index from names and slugs")
    args = parser.parse_args()
    match args.command:
        case "apply":
            print(apply(args.changeset))
        case "fixtures":
            print(fixtures.export())
        case "tiles":
            print(tiles.build(args.scheme))
        case "search":
            print(search.build())
        case "publish":
            with connect() as conn:
                publish(conn, args.changeset_id)
        case "revert":
            with connect() as conn:
                revert(conn, args.changeset_id, args.reason)
        case step:
            result = getattr(importlib.import_module(f"ea.sources.{args.source}"), step)()
            if result is not None:
                print(result)
