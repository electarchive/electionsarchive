import argparse
import importlib
from pathlib import Path

from ea import cache, coverage, fixtures, raw, search, tiles
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
    commands.add_parser(
        "coverage", help="measure a polity's coverage against its v1 goal and write .agents/spec/{polity}-coverage.md"
    ).add_argument("polity", choices=("us",))
    caching = commands.add_parser("raw", help="check and maintain the raw cache")
    caching.add_argument(
        "action",
        choices=("verify", "license", "pin", "retire", "push", "pull"),
        help="verify files against the index, stamp licenses from the database, pin Wikipedia fetches to revisions, "
        "move unindexed files to data/retired, or sync the cache with the EA_REMOTE store",
    )
    caching.add_argument("source", nargs="?", help="limit verify, pin, push, or pull to one source, such as us.medsl")
    caching.add_argument("--quick", action="store_true", help="verify sizes only, without hashing")
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
        case "coverage":
            print(getattr(coverage, args.polity)())
        case "raw" if args.action in ("push", "pull"):
            print(getattr(raw, args.action)(args.source))
        case "raw":
            cache.main(args.action, args.source, quick=args.quick)
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
