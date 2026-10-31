import csv
import re
import time
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.nh_sos_primaries import ALIASES, PUBLISHER, WAYBACK, key, sheets
from ea.store import DATA, connect

SOURCE = "us.nh_sos_pres_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = Path(__file__).with_name("nh_sos_pres_primaries_files.tsv")
DAYS = {2016: "2016-02-09", 2020: "2020-02-11", 2024: "2024-01-23"}
PAGES = {
    2016: "https://www.sos.nh.gov/elections/2016-election-results/2016-presidential-primary-election-results",
    2020: "https://www.sos.nh.gov/elections/2020-election-results/2020-presidential-primary-election-results",
    2024: "https://www.sos.nh.gov/2024-presidential-primary-election-results",
}
NUMBER = re.compile(r"\d[\d,]*(?:\.0+)?")
SCATTER = re.compile(r"scatter", re.IGNORECASE)
ALIASES.update(
    {
        "atandgilacgrant": "atkinsonandgilmantonacademygrant",
        "secondcollgrant": "secondcollegegrant",
        "fitzilliam": "fitzwilliam",
    }
)
POOLED = (
    "Named write-ins with no option on file for this contest are counted with its scattered write-ins, as printed "
    "in the Secretary of State's write-in workbooks."
)


def files() -> list[dict[str, str]]:
    with FILES.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fetch() -> None:
    for row in files():
        url = WAYBACK + row["file"]
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=Path(row["file"]).suffix)
                break
            except httpx.HTTPError, OSError:
                time.sleep(5 * (attempt + 1))
        else:
            print(f"not fetched: {row['file']}")
        time.sleep(1.2)


def count(cell: str) -> int | None:
    return int(float(cell.replace(",", ""))) if NUMBER.fullmatch(cell) else None


def label_of(cell: str) -> str:
    return re.sub(r"\s*\*+$", "", cell).strip()


def read(path: Path, name: str, down: bool) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    sheet = next(iter(sheets(path).values()))
    cells: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    if down:
        labels = {column: label_of(text) for column, text in enumerate(sheet[1]) if column and text}
        for row in sheet[2:]:
            head = label_of(row[0])
            if not head or head.startswith("*"):
                continue
            if head.upper().startswith("TOTAL"):
                for column, label in labels.items():
                    printed = count(row[column]) if column < len(row) else None
                    if printed is not None and sum(cells.get(label, {}).values()) != printed:
                        print(f"{name}: {label} towns sum to {sum(cells.get(label, {}).values())}, printed {printed}")
                        cells.pop(label, None)
                        if printed:
                            totals[label] = printed
                break
            for column, label in labels.items():
                if column < len(row) and (votes := count(row[column])) is not None:
                    cells.setdefault(label, {})[head] = votes
        return cells, totals
    printed: dict[str, list[int]] = {}
    columns: dict[int, str] = {}
    total_column = None
    for index, row in enumerate(sheet):
        head = row[0]
        if head.upper().endswith("COUNTY"):
            above = sheet[index - 1] if index and not sheet[index - 1][0] else []
            columns = {}
            total_column = None
            for column in range(1, len(row)):
                text = label_of((above[column] if column < len(above) else "") or row[column])
                if not text or re.fullmatch(r"Page \d+ of \d+", text):
                    continue
                if text.upper().startswith("TOTAL"):
                    total_column = column
                    break
                columns[column] = text
            continue
        if not columns or not head or head.startswith("*"):
            continue
        total = count(row[total_column]) if total_column is not None and total_column < len(row) else None
        if total is not None:
            printed.setdefault(head, []).append(total)
        for column, town in columns.items():
            if column < len(row) and (votes := count(row[column])) is not None:
                cells.setdefault(head, {})[town] = votes
    for head, found in printed.items():
        if head not in cells:
            if found[-1]:
                totals[head] = found[-1]
            continue
        counted = sum(cells[head].values())
        if counted not in (sum(found), found[-1]):
            print(f"{name}: {head} towns sum to {counted}, printed {found}")
    return cells, totals


def read_summary(path: Path, name: str, down: bool) -> dict[str, dict[str, int]]:
    if down:
        return read(path, name, True)[0]
    sheet = next(iter(sheets(path).values()))
    cells: dict[str, dict[str, int]] = {}
    columns: dict[int, str] = {}
    for row in sheet:
        if row[0].upper().startswith("SUMMARY"):
            columns = {}
            for column in range(1, len(row)):
                if row[column].upper().startswith("TOTAL"):
                    break
                if row[column]:
                    columns[column] = label_of(row[column]).lower()
            continue
        if not columns or not row[0] or row[0].startswith("*") or row[0].upper().startswith("TOTAL"):
            continue
        for column, county in columns.items():
            if column < len(row) and (votes := count(row[column])) is not None:
                cells.setdefault(label_of(row[0]), {})[county] = votes
    return cells


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
        places = {
            (code[:5] if scheme == "us.town" else None, key(text)): (code, scheme.removeprefix("us."))
            for scheme, code, text in conn.execute(
                """select u.scheme_code, u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where (u.scheme_code = 'us.town' or u.scheme_code = 'us.place') and u.code like '33%%'"""
            ).fetchall()
        }
    rows = []
    unplaced: set[str] = set()
    summaries: dict[tuple[int, str], dict[str, dict[str, int]]] = {}
    for entry in files():
        year, party, county = int(entry["year"]), entry["party"], entry["county"]
        path = raw.cached(SOURCE, WAYBACK + entry["file"]).path
        if county == "summary":
            summaries[(year, party, entry["kind"])] = {
                label: {c.lower().removesuffix(" county"): v for c, v in counties.items()}
                for label, counties in read_summary(path, entry["file"], year == 2024).items()
            }
            continue
        cells, totals = read(path, entry["file"], year == 2024)
        if entry["kind"] == "main" and year == 2020:
            cells = {label: towns for label, towns in cells.items() if not SCATTER.search(label)}
            totals = {label: votes for label, votes in totals.items() if not SCATTER.search(label)}
        for label, votes in totals.items():
            rows.append((year, party, county, entry["kind"], codes[county], "county", label, votes))
        missing = set()
        for label, towns in cells.items():
            for town, votes in towns.items():
                name = re.sub(r"\bWare\b", "Ward", re.sub(r"\s*-\s*(?=Ward)", " ", town))
                place = places.get((codes[county], key(name))) or places.get((None, key(name)))
                if place is None:
                    missing.add(town)
                    continue
                rows.append((year, party, county, entry["kind"], *place, label, votes))
        if missing:
            print(f"{entry['file']}: no town for {sorted(missing)}")
            unplaced.update(missing)
    if unplaced:
        raise ValueError(f"no town for {sorted(unplaced)}")
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "party": pl.String,
            "county": pl.String,
            "file": pl.String,
            "code": pl.String,
            "kind": pl.String,
            "label": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    sums = {
        (y, p, f, c, label): votes
        for y, p, f, c, label, votes in frame.group_by("year", "party", "file", "county", "label")
        .agg(pl.col("votes").sum())
        .iter_rows()
    }
    replaced = []
    for (year, party, kind), cells in sorted(summaries.items()):
        for label, counties in cells.items():
            for county, votes in counties.items():
                found = sums.get((year, party, kind, county, label))
                if found is None and votes:
                    print(f"summary {year} {party} {kind} {county}: {label} {votes} has no town rows")
                elif found is not None and found != votes:
                    print(f"summary {year} {party} {kind} {county}: {label} towns {found}, summary {votes}")
                    if kind == "write":
                        replaced.append((year, party, kind, county, label, votes))
    for year, party, kind, county, label, votes in replaced:
        frame = frame.filter(
            ~(
                (pl.col("year") == year)
                & (pl.col("party") == party)
                & (pl.col("file") == kind)
                & (pl.col("county") == county)
                & (pl.col("label") == label)
            )
        )
        if votes:
            frame = pl.concat(
                [
                    frame,
                    pl.DataFrame(
                        [(year, party, county, kind, codes[county], "county", label, votes)],
                        schema=frame.schema,
                        orient="row",
                    ),
                ]
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "towns.parquet")


def surname(label: str) -> str:
    if "," in label:
        return re.sub(r"[^a-z]", "", label.split(",")[0].lower())
    words = person(re.sub(r"\s+[dr]$", "", label.strip(), flags=re.IGNORECASE))
    return words[-1] if words else ""


def given(label: str) -> str:
    parts = label.split(",")
    first = re.sub(r"[^a-z]", "", parts[1].lower()) if len(parts) > 1 else ""
    return first if len(first) > 2 and first not in ("jr", "sr") else ""


def person(ballot: str) -> list[str]:
    name = re.sub(r"\b(?:jr|sr|ii|iii|iv)\b\.?", "", ballot, flags=re.IGNORECASE)
    return re.sub(r"[^a-z ]", "", name.lower()).split()


def choose(label: str, options: list[tuple[str, str]], candidates: set[str], held: set[str]) -> str | None:
    family = surname(label)
    hits = [o for o, ballot in options if (p := person(ballot)) and p[-1] == family]
    hits = hits or [o for o, ballot in options if (p := person(ballot)) and "".join(p).endswith(family)]
    if len(hits) > 1 and (first := given(label)):
        hits = [o for o in hits if person(dict(options)[o])[0][:3] == first[:3]]
    if len(hits) > 1:
        hits = [o for o in hits if o in candidates] or hits
    if len(hits) > 1:
        hits = [o for o in hits if o in held] or hits
    if len(hits) > 1 and len({tuple(person(dict(options)[o])[-1:]) for o in hits}) == 1 and not given(label):
        hits = sorted(hits)[:1]
    return hits[0] if len(hits) == 1 else None


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "towns.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2016, 2020, and 2024 presidential primaries by town, from the Secretary of State's "
            "workbooks, with city wards summed to their city",
            polity="US-NH",
        )
        sources = {}
        for year in sorted(set(frame["year"])):
            title = f"{year} presidential primary election results, by town"
            source = sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGES[year],
                license="Public record",
            )
        for entry in files():
            cs.artifact(raw.cached(SOURCE, WAYBACK + entry["file"]), sources[int(entry["year"])])
        places = {
            (scheme, code): cs.unit(f"us.{scheme}", code)
            for scheme, code in frame.select("kind", "code").unique().iter_rows()
        }
        values = []
        for (year, party), lines in frame.group_by("year", "party", maintain_order=True):
            contest_key = f"{DAYS[year]}:NH:president:{party}"
            contest, election = conn.execute(
                """select c.id, c.election_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [contest_key],
            ).fetchone()
            found = [
                (str(option), label, kind)
                for option, label, kind in conn.execute(
                    "select id, ballot_label, kind from contest_option where contest_id = %s and parent_option_id is null",
                    [contest],
                ).fetchall()
                if kind in ("candidate", "write_in") and not re.search(r"scatter|other", label, re.IGNORECASE)
            ]
            options = [(option, label) for option, label, _ in found]
            candidates = {option for option, _, kind in found if kind == "candidate"}
            held = {
                str(option)
                for (option,) in conn.execute(
                    """select distinct o.id from contest_option o
                       join result_value v on v.option_k = o.k
                       join result_set r on r.k = v.result_set_k
                       where o.contest_id = %s and r.contest_id = %s and r.is_default and r.status = 'active'""",
                    [contest, contest],
                ).fetchall()
            }
            pool = [
                str(option)
                for (option,) in conn.execute(
                    "select id from contest_option where contest_id = %s and kind = 'write_in' and ballot_label ~* 'scatter|other' order by ballot_label ~* 'scatter' desc, id limit 1",
                    [contest],
                ).fetchall()
            ]
            if len(pool) != 1:
                raise ValueError(f"{contest_key}: {len(pool)} scattered write-in options")
            mapping: dict[tuple[str, str], str] = {}
            pooled: dict[str, int] = {}
            for label, file in lines.select("label", "file").unique().iter_rows():
                if SCATTER.search(label):
                    mapping[(label, file)] = pool[0]
                    continue
                option = choose(label, options, candidates, held)
                if option is not None:
                    mapping[(label, file)] = option
                elif file == "write":
                    mapping[(label, file)] = pool[0]
                    pooled[label] = int(lines.filter(pl.col("label") == label, pl.col("file") == file)["votes"].sum())
                else:
                    raise ValueError(f"{contest_key}: {label} matches no option of {[o[1] for o in options]}")
            if pooled:
                print(contest_key, "pooled write-ins", pooled)
            prior = conn.execute(
                "select id from result_set where contest_id = %s and is_default and status = 'active'", [contest]
            ).fetchall()
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for (prior_id,) in prior:
                row = conn.execute(f"select {', '.join(columns)} from result_set where id = %s", [prior_id]).fetchone()
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
                supersedes_id=prior[0][0] if prior else None,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            if pooled:
                cs.add("note", entity_id=result_set, kind="methodology", body_md=POOLED)
            lookup = pl.DataFrame(
                [(label, file, option) for (label, file), option in mapping.items()],
                schema={"label": pl.String, "file": pl.String, "option": pl.String},
                orient="row",
            )
            values.append(
                lines.join(lookup, on=["label", "file"])
                .with_columns(
                    unit_id=pl.struct("kind", "code").map_elements(
                        lambda row: str(places[(row["kind"], row["code"])]), return_dtype=pl.String
                    )
                )
                .group_by("unit_id", "option")
                .agg(pl.col("votes").sum())
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id="unit_id",
                    option_id="option",
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
            print(contest_key, len(mapping), "columns", lines["votes"].sum(), "votes")
        cs.frame("result_value", pl.concat(values))
        return cs.write()
