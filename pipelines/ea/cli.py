import argparse
import atexit
import importlib
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import UUID

from ea import (
    basemap,
    cache,
    checks,
    contrib,
    coverage,
    coverage_check,
    coverage_countries,
    coverage_tables,
    display,
    fixtures,
    layout,
    names,
    portals,
    prescreen,
    rasters,
    raw,
    scans,
    search,
    statewide,
    tiles,
    touched,
)
from ea.apply import apply, publish, revert, rollup
from ea.store import DATA, connect


def main() -> None:
    parser = argparse.ArgumentParser(prog="ea")
    commands = parser.add_subparsers(dest="command", required=True)
    for step in ("fetch", "parse", "emit"):
        commands.add_parser(step).add_argument("source", help="registry name, such as ca.elections")
    commands.add_parser("apply").add_argument("changeset", type=Path, help="changeset artifact directory")
    publishing = commands.add_parser("publish")
    publishing.add_argument("changeset_id", help="a changeset in pending_review")
    reverting = commands.add_parser(
        "revert", help="reject an unpublished changeset or undo a published one no later changeset builds on"
    )
    reverting.add_argument("changeset_id")
    reverting.add_argument("reason")
    for command in (publishing, reverting):
        command.add_argument("--reviewer", type=lambda value: str(UUID(value)), help="user id of the person deciding")
    commands.add_parser("fixtures", help="export the engine's golden-test fixtures from the database")
    commands.add_parser(
        "touched", help="backfill changeset_entity, the records each published changeset touched, from the core tables"
    ).add_argument("table", nargs="?", help="one core table, or entity for creations; all of them by default")
    contributing = commands.add_parser(
        "contrib",
        help="compile a contribution payload into a changeset folder, serve the proposal_job queue, "
        "or prescreen open proposals with a Haiku batch (set ANTHROPIC_API_KEY)",
    )
    contributing.add_argument("action", choices=("compile", "serve", "prescreen"))
    contributing.add_argument(
        "payload", nargs="?", type=Path, help="payload JSON, as contributions.md (Payload) describes"
    )
    contributing.add_argument("--author", type=UUID, help="user id of the human author or key owner")
    contributing.add_argument("--agent", action="store_true", help="the author is an agent acting for --author")
    commands.add_parser("coarse", help="refresh the simplified copies of heavy area geometries used for overlaps")
    commands.add_parser("tiles", help="build one PMTiles archive per boundary set").add_argument(
        "scheme",
        nargs="?",
        help="scheme_code prefix, such as tr or tr.il, or one set as scheme/vintage; omit to build every set",
    )
    commands.add_parser(
        "rasters", help="build one area-ID raster PMTiles archive per boundary set of 1,000 or more areas"
    ).add_argument(
        "scheme",
        nargs="?",
        help="scheme_code prefix, or one set as scheme/vintage, such as us.precinct/2024; omit to build every set",
    )
    commands.add_parser("basemap", help="build a polity's planar basemap from the latest Protomaps build").add_argument(
        "scheme", choices=sorted(basemap.COUNTRIES), help="polity scheme, such as us"
    )
    commands.add_parser("glyphs", help="build the basemap label glyphs the app serves")
    commands.add_parser(
        "hexes",
        help="build the hex tile maps the app serves",
    )
    commands.add_parser(
        "cartograms",
        help="build the US House, UK, French, and Turkish cartogram and tile layouts, then the other hex tile maps",
    )
    commands.add_parser("search", help="rebuild the search index from names and slugs")
    releasing = commands.add_parser(
        "release", help="write the bulk release bundles (Parquet, CSV, and GeoParquet) to data/releases/"
    )
    releasing.add_argument("--calver", help="release label, today's date by default")
    releasing.add_argument(
        "--record",
        action="store_true",
        help="instead of building, write a changeset that records the built release --calver in the release table",
    )
    commands.add_parser("bounties", help="sync the bounties from the known coverage gaps")
    commands.add_parser("parties", help="rebuild the precomputed party pages the app serves")
    commands.add_parser("mosaics", help="rebuild the precomputed national map mosaics the app serves")
    commands.add_parser("records", help="rebuild the precomputed election records of the largest polities")
    rolling = commands.add_parser("rollup", help="recompute the area rollups of result sets")
    rolling.add_argument("sets", nargs="*", type=int, help="result_set k values")
    rolling.add_argument("--affected", action="store_true", help="add every set with no rollup at its district")
    commands.add_parser("merged", help="list rows anywhere in the database that still reference a merged entity")
    commands.add_parser(
        "capitals",
        help="count preferred person names with an all-caps word of 3+ letters by pipeline, rows to data/logs/capitals.tsv",
    )
    commands.add_parser(
        "coverage",
        help="measure a country's coverage against its goals and write us-coverage.md or "
        "coverage/{cc}-coverage.md to the reports folder (EA_REPORTS), refresh the coverage tables the app's coverage pages read from the states and countries that changed "
        "(rebuild: all of them), "
        "check them against the cited counts, the pages, their twins, and REST, or write world-gaps.md there",
    ).add_argument("polity", choices=("us", "refresh", "rebuild", "check", "world", *coverage_countries.countries()))
    portal = commands.add_parser(
        "portals",
        help="find a state's county results portals and emit them with the rest of results_access as a us.results_access "
        "changeset (census), or emit results_access from a registry CSV (load)",
    )
    portal.add_argument("action", choices=("census", "load"))
    portal.add_argument("target", help="two-letter state code, such as TX, for census; the registry CSV path for load")
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
        choices=("prepare", "run", "status", "emit", "layout", "statewide"),
        help="render crops and fill held totals, advance the batch reads once, list manifests, "
        "or emit the tables that passed",
    )
    scanning.add_argument("name", nargs="?", help="manifest name, such as tribune-oh-1876")
    translating = commands.add_parser(
        "i18n",
        help="translate new and changed keys of apps/web/messages/en.json into the locales in "
        "project.inlang/settings.json (or the ones given) on Claude Code (Opus 5.5, medium): glossary, translation, "
        "review, and checks; status shows each run's requests done, percent, and time left; quote prints the estimated cost of a full run; redo retranslates every string into "
        "data/i18n/redo/messages without touching the shipped files, and adopt copies the staged strings that "
        "nobody edited since into apps/web/messages",
    )
    translating.add_argument("action", nargs="?", choices=("run", "status", "quote", "redo", "adopt"), default="run")
    translating.add_argument("locales", nargs="*")
    translating.add_argument("--once", action="store_true", help="advance one step and exit instead of waiting")
    batching = commands.add_parser(
        "batch", help="Message Batches jobs, accounts, and approvals (set ANTHROPIC_API_KEY)"
    )
    batching.add_argument(
        "action",
        choices=("status", "poll", "approve", "account", "cancel", "billed"),
        help="show caps, approvals, and open jobs; collect ended batches; approve dollars for a job prefix; set an "
        "account's remaining credit; cancel a job's open batches; or pull billed cost per account",
    )
    batching.add_argument("target", nargs="?", help="job or prefix (us.scans/al-osr-1970) or account name")
    batching.add_argument("usd", nargs="?", type=float, help="dollars for approve or account")
    living = commands.add_parser("live", help="poll official election night feeds into the live schema")
    living.add_argument(
        "action",
        choices=("run", "sync", "status", "check"),
        help="poll this poller's enabled feeds until stopped, load ea/live_feeds.toml into live.feed, list feeds, or "
        "check that each feed, state, and page will work on the night (exits nonzero on any red row)",
    )
    living.add_argument("--poller", default="fsn1", help="which feeds to poll, by their poller column")
    living.add_argument("--once", action="store_true", help="poll each due feed once and stop")
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
            print(
                portals.load(Path(args.target))
                if args.action == "load"
                else portals.census(args.target.upper(), args.only.split(",") if args.only else None)
            )
        case "scans":
            print(
                scans.status()
                if args.action == "status"
                else layout.step(args.name)
                if args.action == "layout"
                else statewide.step(args.name)
                if args.action == "statewide"
                else statewide.emit(args.name)
                if args.action == "emit" and not (scans.MANIFESTS / f"{args.name}.json").exists()
                else getattr(scans, args.action)(args.name)
            )
        case "live":
            from ea import live

            if args.action == "check":
                from ea import live_check

                raise SystemExit(live_check.run())
            if args.action == "run":
                live.run(args.poller, args.once)
            else:
                print(getattr(live, args.action)())
        case "i18n":
            from ea import i18n

            match args.action:
                case "quote":
                    print(i18n.quote(args.locales))
                case "adopt":
                    print(i18n.adopt(args.locales))
                case "status":
                    print("\n".join(i18n.batch.progress(i18n.SOURCE)) or "no translation runs yet")
                case _:
                    print(i18n.run(args.locales, wait=not args.once, redo=args.action == "redo"))
        case "batch":
            from ea import batch

            needs = {"approve": 2, "account": 2, "cancel": 1}.get(args.action, 0)
            if (needs >= 1 and args.target is None) or (needs == 2 and args.usd is None):
                parser.error(f"batch {args.action} needs {'a target and dollars' if needs == 2 else 'a job'}")
            print(
                batch.approve(args.target, args.usd)
                if args.action == "approve"
                else batch.account(args.target, args.usd)
                if args.action == "account"
                else batch.cancel(args.target)
                if args.action == "cancel"
                else getattr(batch, args.action)()
            )
        case "fixtures":
            print(fixtures.export())
        case "touched":
            with connect() as conn:
                touched.backfill(conn, args.table)
        case "contrib" if args.action == "serve":
            contrib.serve()
        case "contrib" if args.action == "prescreen":
            prescreen.run()
        case "contrib":
            if args.payload is None or args.author is None:
                parser.error("contrib compile needs a payload and --author")
            with connect() as conn:
                print(contrib.compile_file(conn, args.payload, args.author, "agent" if args.agent else "user"))
        case "coarse":
            with connect() as conn:
                print(display.coarsen(conn))
        case "tiles":
            print(tiles.build(args.scheme))
        case "rasters":
            print(rasters.build(args.scheme))
        case "basemap":
            print(basemap.build(args.scheme))
        case "glyphs":
            print(basemap.glyphs())
        case "hexes":
            from ea import hexes

            print(hexes.build_tiles())
        case "cartograms":
            from ea import cartogram, hexes

            print(cartogram.build())
            print(hexes.build_tiles())
        case "search":
            print(search.build())
        case "release":
            from ea import release

            print(release.record(args.calver) if args.record else release.build(args.calver))
        case "bounties":
            from ea import bounties

            print(bounties.sync())
        case "parties":
            subprocess.run(
                ["node", "scripts/party-pages.js"], cwd=Path(__file__).resolve().parents[2] / "apps" / "web", check=True
            )
        case "mosaics":
            subprocess.run(
                ["node", "scripts/mosaics.js"], cwd=Path(__file__).resolve().parents[2] / "apps" / "web", check=True
            )
        case "records":
            subprocess.run(
                ["node", "scripts/polity-records.js"],
                cwd=Path(__file__).resolve().parents[2] / "apps" / "web",
                check=True,
            )
        case "coverage" if args.polity in ("refresh", "rebuild"):
            print(coverage_tables.refresh(full=args.polity == "rebuild"))
        case "coverage" if args.polity == "check":
            print(coverage_check.check())
        case "coverage" if args.polity == "world":
            print(coverage_countries.world())
        case "coverage" if args.polity == "us":
            print(coverage_tables.refresh())
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
        case "capitals":
            with connect() as conn, (DATA / "logs" / "capitals.tsv").open("w", encoding="utf-8") as log:
                counts = Counter()
                for entity, text, lang, pipeline in conn.execute(
                    """select n.entity_id::text, n.text, n.lang, c.pipeline from name n
                       join entity e on e.id = n.entity_id and e.type = 'person'
                       join changeset c on c.id = n.changeset_id
                       where n.is_preferred and n.valid_from is null and n.valid_to is null
                         and n.text ~ '[[:upper:]]{3}'
                         and not exists (select from entity_merge m where m.entity_id = n.entity_id)"""
                ):
                    if any(
                        len(word) >= 3 and word.isupper() and word not in names.ACRONYMS | names.ROMAN
                        for word in names.RUN.findall(text)
                    ):
                        counts[pipeline] += 1
                        log.write(f"{pipeline}\t{entity}\t{lang}\t{text}\n")
                for pipeline, count in counts.most_common():
                    print(f"{pipeline}\t{count}")
                print(f"total\t{counts.total()}")
        case "rollup":
            with connect() as conn:
                rollup(conn, args.sets, args.affected)
        case "publish":
            with connect() as conn:
                publish(conn, args.changeset_id, args.reviewer)
        case "revert":
            with connect() as conn:
                revert(conn, args.changeset_id, args.reason, args.reviewer)
        case step:
            module = importlib.import_module(f"ea.sources.{args.source}")
            for loaded in [loaded for name, loaded in sys.modules.items() if name.startswith("ea.sources.")]:
                if isinstance(source := getattr(loaded, "SOURCE", None), str):
                    raw.parsed(source)
            if step == "parse":
                raw.atomic_parquet()
                for partial in (DATA / "parsed" / args.source).rglob(".*.parquet.*.tmp"):
                    try:
                        os.kill(int(partial.name.rsplit(".", 2)[1]), 0)
                    except ProcessLookupError:
                        partial.unlink(missing_ok=True)
                    except PermissionError:
                        pass
            result = getattr(module, step)()
            if result is not None:
                print(result)
            if raw.HOST and (DATA / "parsed" / args.source).is_dir():
                try:
                    raw.push(args.source)
                except subprocess.SubprocessError:
                    print(f"{args.source} is not on {raw.HOST} yet; run `ea raw push {args.source}`", file=sys.stderr)
