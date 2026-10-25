import argparse
import atexit
import importlib
import resource
import subprocess
import sys
import time
from pathlib import Path

from ea import (
    cache,
    checks,
    coverage,
    coverage_countries,
    coverage_tables,
    fixtures,
    layout,
    portals,
    raw,
    scans,
    search,
    statewide,
    tiles,
)
from ea.apply import apply, publish, revert, rollup
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
    rolling = commands.add_parser("rollup", help="recompute the area rollups of result sets")
    rolling.add_argument("sets", nargs="*", type=int, help="result_set k values")
    rolling.add_argument("--affected", action="store_true", help="add every set with no rollup at its district")
    commands.add_parser("merged", help="list rows anywhere in the database that still reference a merged entity")
    commands.add_parser(
        "coverage",
        help="measure a country's coverage against its goals and write .agents/spec/us-coverage.md or "
        ".agents/spec/coverage/{cc}-coverage.md, or refresh the coverage tables the app's coverage pages read",
    ).add_argument("polity", choices=("us", "refresh", *coverage_countries.countries()))
    portal = commands.add_parser("portals", help="find county results portals and record them in us-portals.csv")
    portal.add_argument("action", choices=("census",))
    portal.add_argument("state", help="two-letter state code, such as TX")
    portal.add_argument(
        "--only",
        help="comma-separated platforms (clarity, enhanced_voting, hosted_static) to probe; skips the crawl and keeps "
        "the state's other registry rows",
    )
    scanning = commands.add_parser(
        "scans", help="batch vision pipeline for scanned county tables (manifests in pipelines/scans)"
    )
    scanning.add_argument(
        "action",
        choices=("prepare", "run", "status", "emit", "layout", "ledger", "reconcile", "statewide"),
        help="render crops and fill held totals, advance the Message Batches steps once, list manifests, "
        "or emit the tables that passed",
    )
    scanning.add_argument("name", nargs="?", help="manifest name, such as tribune-oh-1876")
    scanning.add_argument(
        "--mode",
        choices=("auto", "direct", "batch"),
        default="auto",
        help="run and layout: direct reads at full price in minutes (cancels a waiting batch), batch waits at half "
        "price, auto reads directly unless a first pass passes 200 requests",
    )
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
    atexit.register(
        lambda: (
            (DATA / "logs" / "ea-maxrss.tsv")
            .open("a")
            .write(
                f"{time.time():.0f}\t{args.command}\t{getattr(args, 'source', '') if args.command in ('parse', 'emit') else ''}\t"
                f"{max(resource.getrusage(who).ru_maxrss for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)) / 1048576:.3f}\n"
            )
        )
    )
    match args.command:
        case "apply":
            print(apply(args.changeset))
        case "portals":
            print(portals.census(args.state.upper(), args.only.split(",") if args.only else None))
        case "scans":
            print(
                scans.status()
                if args.action == "status"
                else scans.reconcile()
                if args.action == "ledger"
                else f"{scans.reconcile()}\n{scans.bill()}"
                if args.action == "reconcile"
                else layout.step(args.name, args.mode)
                if args.action == "layout"
                else statewide.step(args.name, args.mode)
                if args.action == "statewide"
                else statewide.emit(args.name)
                if args.action == "emit" and not (scans.MANIFESTS / f"{args.name}.json").exists()
                else scans.run(args.name, args.mode)
                if args.action == "run"
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
        case "coverage" if args.polity == "us":
            print(coverage.us())
        case "coverage":
            print(coverage_countries.report(args.polity))
        case "raw" if args.action in ("push", "pull"):
            print(getattr(raw, args.action)(args.source))
        case "raw":
            cache.main(args.action, args.source, quick=args.quick)
        case "merged":
            with connect() as conn:
                for relation, column, entity, into, merge, rows in checks.merged(conn):
                    print(f"{relation}.{column}: {rows} rows reference {entity}, merged into {into} by {merge}")
        case "rollup":
            with connect() as conn:
                rollup(conn, args.sets, args.affected)
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
