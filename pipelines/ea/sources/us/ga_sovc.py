import html
import re
import subprocess
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ga_sovc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {"2002_1105": date(2002, 11, 5), "2004_1102": date(2004, 11, 2), "2006_1107": date(2006, 11, 7)}
HOSTS = ("sos.state.ga.us", "www.sos.state.ga.us", "sos.georgia.gov", "sos.ga.gov", "www.sos.ga.gov")
CDX = (
    "https://web.archive.org/cdx/search/cdx?url={host}/acrobat/Elections/precincts/*"
    "&fl=timestamp,original,statuscode,mimetype&limit=100000"
)
WAYBACK = "https://web.archive.org/web/{stamp}id_/{url}"
NUMBER = re.compile(r"\d[\d,]*")
SHARE = re.compile(r"\d+\.\d+%|-")
LABELS = {"Times", "Total", "Reg.", "Cards", "Number"}
STATUS = {"Official", "Unofficial", "Results", "and", "Complete"}
OFFICES = {
    r"(US|U\.S\.) PRESIDENT|PRESIDENT( OF THE UNITED STATES)?": "president",
    r"(US|U\.S\.|UNITED STATES) SENATE|US SENATOR": "senate",
    r"GOVERNOR": "governor",
    r"LT\.? ?GOVERNOR|LIEUTENANT GOVERNOR|LT\.? GOV": "lieutenant-governor",
    r"SECRETARY OF STATE|SEC(\.|RETARY)? OF STATE": "secretary-of-state",
    r"ATTORNEY GENERAL|ATTY\.? GENERAL": "attorney-general",
    r"(COMM?(ISSIONER|\.)? OF )?AGRICULTURE( COMM?(ISSIONER|\.)?)?|AG(RICULTURE)? COMMISSIONER": "commissioner-of-agriculture",
    r"(COMM?(ISSIONER|\.)? OF )?INSURANCE( COMM?(ISSIONER|\.)?)?": "commissioner-of-insurance",
    r"(COMM?(ISSIONER|\.)? OF )?LABOR( COMM?(ISSIONER|\.)?)?": "commissioner-of-labor",
    r"(STATE )?SCHOOL SUPERINTENDENT|STATE SCHOOL SUPT\.?|STATE SUPERINTENDENT( OF SCHOOLS)?": "school-superintendent",
}
FEDERAL = r"^(US|U\.S\.|UNITED STATES) (REP|HOUSE|CONGRESS)|^CONGRESS(IONAL|MAN)?\b|^US REPRESENTATIVE"
LEGISLATIVE = r"^(STATE |ST\. )?(REP|REPRESENTATIVE|HOUSE|SENATE|SENATOR|SEN)(\b.*\d|\d)"


def snapshots() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for host in HOSTS:
        for line in raw.cached(SOURCE, CDX.format(host=host)).path.read_text().splitlines():
            parts = line.split()
            matched = len(parts) == 4 and re.search(
                r"/precincts/(\d{4}_\d{4})/([A-Za-z0-9]+(_[A-Za-z_]+)?)\.pdf$", parts[1]
            )
            if matched and matched[1] in DAYS and parts[2] == "200" and parts[3] == "application/pdf":
                found[(matched[1], matched[2].lower())].add((parts[0], parts[1]))
    return {
        key: [WAYBACK.format(stamp=stamp, url=url) for stamp, url in sorted(pairs, reverse=True)]
        for key, pairs in sorted(found.items())
    }


def complete(path: Path) -> bool:
    pages = subprocess.run(["pdfinfo", str(path)], check=False, capture_output=True, text=True).stdout
    first = subprocess.run(
        ["pdftotext", "-l", "1", "-layout", str(path), "-"], check=False, capture_output=True, text=True
    ).stdout
    count = re.search(r"Pages:\s+(\d+)", pages)
    stated = re.search(r"Page:\s*1 of (\d+)", first)
    return count is not None and stated is not None and count[1] == stated[1]


def fetch() -> None:
    for host in HOSTS:
        raw.fetch(SOURCE, CDX.format(host=host), suffix=".txt")
    for urls in snapshots().values():
        for url in urls:
            try:
                artifact = raw.cached(SOURCE, url)
            except LookupError:
                artifact = raw.fetch(SOURCE, url, suffix=".pdf")
                time.sleep(12)
            if complete(artifact.path):
                break


def parse() -> None:
    with connect() as conn:
        fips = {re.sub(r"[^a-z]", "", name): code for name, code in openelections.counties(conn)["GA"].items()}
    rows: list[tuple[date, str, str, str, str, int, str]] = []
    problems: list[str] = []
    for (folder, name), urls in snapshots().items():
        paths = []
        for url in urls:
            try:
                paths.append(raw.cached(SOURCE, url).path)
            except LookupError:
                continue
        if not paths:
            problems.append(f"{folder}/{name}: not fetched")
            continue
        path = next((path for path in paths if complete(path)), max(paths, key=lambda path: path.stat().st_size))
        if not complete(path):
            problems.append(f"{folder}/{name}: truncated, only races with a county total row are kept")
        out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True, text=True).stdout
        pending: dict[
            tuple[str, tuple[str, ...]], tuple[dict[str, list[tuple[list[int | None], int | None]]], str | None]
        ] = {}
        done: list[tuple[str, list[str], dict[str, list[tuple[list[int | None], int | None]]], list[int | None]]] = []
        stem = name.split("_")[0].removesuffix("county")
        county = sorted(fips.values())[int(stem) - 1] if stem.isdigit() else fips[stem]
        for page in re.findall(r"<page [^>]*>(.*?)</page>", out, re.DOTALL):
            lines: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
            for x0, y0, x1, text in re.findall(
                r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)<', page
            ):
                lines[round(float(y0))].append((float(x0), float(x1), html.unescape(text)))
            ys = sorted(lines)
            for y in ys:
                lines[y].sort()
            sovc = next((y for y in ys if lines[y][0][2] == "SOVC"), None) or next(
                (y for y in ys if {t for _, _, t in lines[y]} <= STATUS), None
            )
            if sovc is None:
                continue
            wide = next((y for y in ys if lines[y][0][2] == "Jurisdiction"), None) or next(
                (
                    y - 0.5
                    for y in ys
                    if y > sovc
                    and any(
                        NUMBER.fullmatch(lines[y][index][2]) and SHARE.fullmatch(lines[y][index + 1][2])
                        for index in range(len(lines[y]) - 1)
                    )
                ),
                None,
            )
            if wide is None:
                continue
            top = min((y for y in ys if sovc < y < wide and any(t in LABELS for _, _, t in lines[y])), default=None)
            body = [y for y in ys if y > wide]
            extension = top is None
            if top is None:
                heads = [y for y in ys if sovc < y < wide and not {w for _, _, w in lines[y]} <= STATUS]
                top = next(
                    (heads[index] for index in range(1, len(heads)) if heads[index] - heads[index - 1] > 12), None
                )
                if top is None:
                    problems.append(f"{folder}/{name}: unreadable page")
                    continue
                first = min(
                    (
                        lines[y][index][0]
                        for y in body
                        for index in range(len(lines[y]) - 1)
                        if NUMBER.fullmatch(lines[y][index][2]) and SHARE.fullmatch(lines[y][index + 1][2])
                    ),
                    default=None,
                )
                if first is None:
                    problems.append(f"{folder}/{name}: unreadable page")
                    continue
                start = max(x1 for y in body for x0, x1, _ in lines[y] if x1 < first - 1) + 1
            else:
                start = min(x0 for y in ys if top <= y < wide for x0, _, t in lines[y] if t in LABELS) - 4
            header = [(x0, t) for x0, _, t in lines[top]]
            kind = next((label for label in ("Times", "Reg.", "Total") if any(t == label for _, t in header)), None)
            starts = [0.0] if extension else [x0 for x0, t in header if t == kind]
            for number in range(1, len(starts)):
                index = next(index for index, (x0, _) in enumerate(header) if x0 == starts[number])
                while index > 0 and header[index - 1][1] in LABELS | {"Voters", "Counted", "Votes", "Cast"}:
                    index -= 1
                    if header[index][1] in LABELS:
                        starts[number] = header[index][0]
            edges: list[tuple[float, bool]] = []
            for x1, share in sorted(
                (x1, SHARE.fullmatch(t) is not None and t != "-")
                for y in body
                for x0, x1, t in lines[y]
                if x0 >= start and (NUMBER.fullmatch(t) or SHARE.fullmatch(t))
            ):
                if edges and x1 - edges[-1][0] < 3:
                    edges[-1] = (edges[-1][0], edges[-1][1] or share)
                else:
                    edges.append((x1, share))
            bounds: list[tuple[float, float, list[tuple[float, float]] | None]] = []
            for number, lo in enumerate(starts):
                hi = starts[number + 1] if number + 1 < len(starts) else 1e9
                last = max((x0 for x0, t in header if t in LABELS and lo <= x0 < hi), default=lo)
                group = [edge for edge in edges if lo < edge[0] < hi]
                plain = next((index for index, (x1, _) in enumerate(group) if x1 > last), None) if not extension else -1
                if plain is None:
                    continue
                rest = group[plain + 1 :]
                if len(rest) % 2 or any(rest[index][1] for index in range(0, len(rest), 2)):
                    bounds.append((lo if number else 0.0, hi if number + 1 < len(starts) else 1e9, None))
                    continue
                edge = group[plain][0] + 0.5 if plain >= 0 else start
                columns = []
                for index in range(0, len(rest), 2):
                    columns.append((edge, rest[index + 1][0] + 1))
                    edge = rest[index + 1][0] + 1
                bounds.append((lo if number else 0.0, hi if number + 1 < len(starts) else 1e9, columns))
            for number, (lo, hi, columns) in enumerate(bounds):
                right = hi
                title = " ".join(
                    t
                    for y in ys
                    if sovc < y < top and not {w for _, _, w in lines[y]} <= STATUS
                    for x0, _, t in lines[y]
                    if lo <= x0 < right
                )
                if columns is None:
                    if not title.startswith("TURN"):
                        problems.append(f"{folder}/{name} {title}: columns do not alternate")
                    continue
                names = tuple(
                    " ".join(
                        t
                        for y in ys
                        if top <= y < wide
                        for x0, _, t in lines[y]
                        if clo <= x0 < chi and t not in LABELS and t != "-"
                    )
                    for clo, chi in columns
                )
                values, section = pending.pop((title, names), ({}, None))
                total = None
                for y in body:
                    label = " ".join(t for x0, _, t in lines[y] if x0 < start)
                    if not any(x0 >= start and NUMBER.fullmatch(t) for x0, _, t in lines[y]):
                        if label and all(x0 < start for x0, _, _ in lines[y]):
                            section = label
                        continue
                    found = [
                        next(
                            (
                                int(t.replace(",", ""))
                                for x0, x1, t in lines[y]
                                if clo <= x0 and x1 <= chi and NUMBER.fullmatch(t)
                            ),
                            None,
                        )
                        for clo, chi in columns
                    ]
                    if all(value is None for value in found):
                        continue
                    stated = [
                        int(t.replace(",", ""))
                        for x0, x1, t in lines[y]
                        if x0 >= max(lo, start)
                        and x1 <= (columns[0][0] if columns else hi) + 0.5
                        and NUMBER.fullmatch(t)
                    ]
                    record = (found, stated[-2 if any(t == "Number" for _, t in header) else -1] if stated else None)
                    if label == "Total" and section in (None, "Total"):
                        total = record
                    elif section is None:
                        values.setdefault(label, []).append(record)
                    elif label == "Total":
                        values.setdefault(f"~{section.lower()}", []).append(record)
                if total is None:
                    pending[(title, names)] = (values, section)
                    continue
                if not extension:
                    done.append((title, list(names), values, list(total[0])))
                    continue
                base = next((race for race in reversed(done) if race[0] == title), None)
                if base is None:
                    problems.append(f"{folder}/{name} {title}: continued columns with no race before them")
                    continue
                for label in set(base[2]) | set(values):
                    old = base[2].get(label, [])
                    new = values.get(label, [])
                    base[2][label] = [
                        (
                            (old[index][0] if index < len(old) else [None] * len(base[1]))
                            + (new[index][0] if index < len(new) else [None] * len(names)),
                            old[index][1] if index < len(old) else None,
                        )
                        for index in range(max(len(old), len(new)))
                    ]
                base[1].extend(names)
                base[3].extend(total[0])
        problems.extend(f"{folder}/{name} {title}: no county total row" for title, _ in pending)
        for title, names, values, total in done:
            sums = [sum(r[0][i] or 0 for records in values.values() for r in records) for i in range(len(names))]
            if sums != [value or 0 for value in total]:
                problems.append(f"{folder}/{name} {title}: precincts {sums}, total {total}")
                continue
            off = [
                label
                for label, records in values.items()
                for found, stated in records
                if stated is not None and sum(value or 0 for value in found) != stated
            ]
            if off and not title.startswith("TURN"):
                problems.append(f"{folder}/{name} {title}: candidate votes differ from total votes in {off[:3]}")
                continue
            for label, records in values.items():
                for found, _ in records:
                    for candidate, votes in zip(names, found, strict=True):
                        rows.append(
                            (
                                DAYS[folder],
                                county,
                                title,
                                f"~{label.lower()}"
                                if re.match(r"(?i)((os|ts) )?(absentee|provis?onal)\b", label)
                                else label,
                                candidate,
                                votes or 0,
                                name,
                            )
                        )
    for problem in problems:
        print(problem)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "fips": pl.String,
            "title": pl.String,
            "precinct": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "file": pl.String,
        },
        orient="row",
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's November 2002, 2004, and 2006 general elections by precinct, from the Secretary of "
            "State's county statements of votes cast",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^200[246]-11-0\\d:GA'
                     and rs.changeset_id not in (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        source = cs.source("Georgia Secretary of State", "Statement of Votes Cast by county")
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Georgia Secretary of State",
                title="Statement of Votes Cast by county",
                url="https://sos.ga.gov/page/election-results-archive",
                license="Public record",
            )
        for urls in snapshots().values():
            for url in urls:
                try:
                    cs.artifact(raw.cached(SOURCE, url), source)
                except LookupError:
                    continue
        title = (
            pl.col("title")
            .str.to_uppercase()
            .str.replace(
                r"^((OFFICIAL|OFFICAL|AND|COMPLETE|FINAL|RESULTS|RESLULTS|CERTIFIED|ELECTION|STATEMENTS?|OF|VOTES|CAST|[A-Z])\s+)+",
                "",
            )
        )
        returns = (
            lines.with_columns(
                chamber=pl.coalesce(
                    *[
                        pl.when(title.str.contains(f"^({pattern})$")).then(pl.lit(office))
                        for pattern, office in OFFICES.items()
                    ],
                    pl.when(title.str.contains(FEDERAL)).then(pl.lit("house")),
                    pl.when(title.str.contains(LEGISLATIVE)).then(
                        pl.concat_str(
                            pl.when(title.str.contains(r"\bSEN")).then(pl.lit("senate")).otherwise(pl.lit("house")),
                            title.str.extract(r"(\d+)").cast(pl.Int16).cast(pl.String),
                            title.str.extract(r"POST\s*(\d+)"),
                            pl.lit("g"),
                            separator=":",
                            ignore_nulls=True,
                        )
                    ),
                ),
                district=pl.when(title.str.contains(FEDERAL)).then(title.str.extract(r"(\d+)").cast(pl.Int16)),
                writein=pl.col("candidate").str.contains(r"(?i)write-?in"),
                aggregate=pl.col("precinct").str.starts_with("~"),
                precinct=pl.col("precinct").str.strip_prefix("~"),
            )
            .filter(pl.col("chamber").is_not_null())
            .select(
                year=pl.col("day").dt.year(),
                chamber="chamber",
                day="day",
                special=pl.lit(False),
                state=pl.lit("GA"),
                district="district",
                code=pl.when("aggregate")
                .then(pl.concat_str(pl.lit("GA"), "fips", "precinct", pl.col("day").cast(pl.String), separator=":"))
                .otherwise(
                    pl.concat_str(
                        pl.col("day").dt.year().cast(pl.String),
                        pl.lit("GA"),
                        "fips",
                        pl.lit(""),
                        "precinct",
                        separator=":",
                    )
                ),
                fips="fips",
                candidate=pl.when("writein")
                .then(pl.lit("WRITE-INS"))
                .otherwise(
                    pl.col("candidate")
                    .str.replace(r"(\s*\(\w+\)\s*\w?)+$", "")
                    .str.replace(r"\s+[RDL]$", "")
                    .str.replace(r"^([A-Z. ]+?)\s*[-/]\s*[A-Z]+$", "$1")
                    .str.to_uppercase()
                ),
                measure=pl.lit(1, pl.Int16),
                writein="writein",
                votes="votes",
                kind=pl.when("aggregate").then(pl.lit("aggregate")).otherwise(pl.lit("precinct")),
                source=pl.lit("GA"),
            )
            .group_by(
                "year",
                "chamber",
                "day",
                "special",
                "state",
                "district",
                "code",
                "fips",
                "candidate",
                "measure",
                "kind",
                "source",
            )
            .agg(pl.col("writein").any(), pl.col("votes").sum())
        )
        keyed = returns.with_columns(
            key=pl.concat_str(
                pl.col("day").cast(pl.String),
                "state",
                "chamber",
                pl.col("district").cast(pl.String),
                separator=":",
                ignore_nulls=True,
            )
        )
        returns = keyed.filter(~pl.col("key").is_in(list(filled))).drop("key")
        medsl.attach(cs, conn, returns, {"GA": source}, grain="precinct")
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '13%%'"
            ).fetchall()
        }
        units = (
            returns.group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min(), pl.col("kind").first())
            .with_columns(
                id=pl.struct("code", "kind").map_elements(
                    lambda row: str(
                        cs.unit("us.precinct" if row["kind"] == "precinct" else "us.aggregate", row["code"])
                    ),
                    return_dtype=pl.String,
                )
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-GA"))),
                scheme_code=pl.when(pl.col("kind") == "precinct")
                .then(pl.lit("us.precinct"))
                .otherwise(pl.lit("us.aggregate")),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{units.height} new units")
        return cs.write()
