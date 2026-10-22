import argparse
import importlib
import subprocess
import sys
from pathlib import Path

from ea import cache, checks, coverage, coverage_tables, fixtures, layout, portals, raw, scans, search, tiles
from ea.apply import apply, publish, revert
from ea.store import DATA, connect


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
    commands.add_parser("parties", help="rebuild the precomputed party pages the app serves")
    commands.add_parser("merged", help="list rows anywhere in the database that still reference a merged entity")
    commands.add_parser(
        "coverage",
        help="measure a polity's coverage against its v1 goal and write .agents/spec/{polity}-coverage.md, "
        "or refresh the coverage tables the app's coverage page reads",
    ).add_argument("polity", choices=("us", "refresh"))
    portal = commands.add_parser("portals", help="find county results portals and record them in us-portals.csv")
    portal.add_argument("action", choices=("census",))
    portal.add_argument("state", help="two-letter state code, such as TX")
    scanning = commands.add_parser(
        "scans", help="batch vision pipeline for scanned county tables (manifests in pipelines/scans)"
    )
    scanning.add_argument(
        "action",
        choices=("prepare", "run", "status", "emit", "layout"),
        help="render crops and fill held totals, advance the Message Batches steps once, list manifests, "
        "or emit the tables that passed",
    )
    scanning.add_argument("name", nargs="?", help="manifest name, such as tribune-oh-1876")
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
        case "portals":
            print(portals.census(args.state.upper()))
        case "scans":
            print(
                scans.status()
                if args.action == "status"
                else layout.step(args.name)
                if args.action == "layout"
                else getattr(scans, args.action)(args.name)
            )
        case "fixtures":
            print(fixtures.export())
        case "tiles":
            print(tiles.build(args.scheme))
        case "search":
            print(search.build())
        case "parties":
            subprocess.run(
                ["node", "scripts/party-pages.js"], cwd=Path(__file__).resolve().parents[2] / "apps" / "web", check=True
            )
        case "coverage" if args.polity == "refresh":
            print(coverage_tables.refresh())
        case "coverage":
            print(getattr(coverage, args.polity)())
        case "raw" if args.action in ("push", "pull"):
            print(getattr(raw, args.action)(args.source))
        case "raw":
            cache.main(args.action, args.source, quick=args.quick)
        case "merged":
            with connect() as conn:
                for relation, column, entity, into, merge, rows in checks.merged(conn):
                    print(f"{relation}.{column}: {rows} rows reference {entity}, merged into {into} by {merge}")
        case "publish":
            with connect() as conn:
                publish(conn, args.changeset_id)
        case "revert":
            with connect() as conn:
                revert(conn, args.changeset_id, args.reason)
        case step:
            module = importlib.import_module(f"ea.sources.{args.source}")
            for loaded in [loaded for name, loaded in sys.modules.items() if name.startswith("ea.sources.")]:
                if isinstance(source := getattr(loaded, "SOURCE", None), str):
                    raw.parsed(source)
            result = getattr(module, step)()
            if result is not None:
                print(result)
            if raw.HOST and (DATA / "parsed" / args.source).is_dir():
                try:
                    raw.push(args.source)
                except subprocess.SubprocessError:
                    print(f"{args.source} is not on {raw.HOST} yet; run `ea raw push {args.source}`", file=sys.stderr)
