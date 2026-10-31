import os
import re
import time
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_sos_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{snapshot}id_/https://www.sos.nh.gov/sites/g/files/ehbemt561/files/inline-documents/sonh/"
DAYS = {
    2016: date(2016, 11, 8),
    2018: date(2018, 11, 6),
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2024: date(2024, 11, 5),
}
PAGES = {
    2016: "https://www.sos.nh.gov/elections/2016-election-results/2016-general-election-results",
    2018: "https://www.sos.nh.gov/elections/2018-election-results/2018-general-election-results",
    2020: "https://www.sos.nh.gov/elections/2020-election-results/2020-general-election-results",
    2022: "https://www.sos.nh.gov/elections/elections/election-results/2022-election-results-0/2022-general-election-results",
    2024: "https://www.sos.nh.gov/2024-general-election-results",
}
FILES = {
    2016: {
        "2016-ge-state-senate-district-1.xls": None,
        "2016-ge-state-senate-district-2.xls": None,
        "2016-ge-state-senate-districts-3-4.xls": None,
        "2016-ge-state-senate-districts-5-6.xls": None,
        "2016-ge-state-senate-districts-7-8.xls": None,
        "2016-ge-state-senate-districts-9-11.xls": None,
        "2016-ge-state-senate-districts-12-15.xls": None,
        "2016-ge-state-senate-districts-16-18.xls": None,
        "2016-ge-state-senate-districts-19-21.xls": None,
        "2016-ge-state-senate-districts-22-24.xls": None,
        "2016-ge-house-belknap.xls": "belknap",
        "2016-ge-house-carroll.xls": "carroll",
        "2016-ge-house-cheshire.xls": "cheshire",
        "2016-ge-house-coos.xlsx": "coos",
        "2016-ge-house-grafton.xls": "grafton",
        "2016-ge-house-hillsborough.xls": "hillsborough",
        "2016-ge-house-merrimack.xls": "merrimack",
        "2016-ge-house-rockingham.xls": "rockingham",
        "2016-ge-house-strafford.xls": "strafford",
        "2016-ge-house-sullivan.xls": "sullivan",
    },
    2018: {
        "2018-ge-state-senate-district-1-8.xls": None,
        "2018-ge-state-senate-district-9-24.xls": None,
        "2018-ge-house-belknap.xls": "belknap",
        "2018-ge-house-carroll.xls": "carroll",
        "2018-ge-house-cheshire_0.xls": "cheshire",
        "2018-ge-house-coos.xls": "coos",
        "2018-ge-house-grafton.xls": "grafton",
        "2018-ge-house-hillsborough_0.xls": "hillsborough",
        "2018-ge-house-merrimack.xls": "merrimack",
        "2018-ge-house-rockingham.xls": "rockingham",
        "2018-ge-house-strafford.xls": "strafford",
        "2018-ge-house-sullivan.xls": "sullivan",
    },
    2020: {
        "2020-state-senate-district-1-11.xls": None,
        "2020-state-senate-district-12-24.xls": None,
        "2020-ge-house-belknap.xls": "belknap",
        "2020-ge-house-carroll.xls": "carroll",
        "2020-ge-house-cheshire.xls": "cheshire",
        "2020-ge-house-coos.xls": "coos",
        "2020-ge-house-grafton.xls": "grafton",
        "2020-ge-house-hillsborough.xls": "hillsborough",
        "2020-ge-house-merrimack.xls": "merrimack",
        "2020-ge-house-rockingham.xls": "rockingham",
        "2020-ge-house-strafford.xls": "strafford",
        "2020-ge-house-sullivan.xls": "sullivan",
    },
    2022: {
        "2022-ge-state-senate-district-1-24_1.xls": None,
        "2022-ge-house-belknap_1.xls": "belknap",
        "2022-ge-house-carroll.xls": "carroll",
        "2022-ge-house-cheshire_0.xls": "cheshire",
        "2022-ge-house-coos_1.xls": "coos",
        "2022-ge-house-grafton_1.xls": "grafton",
        "2022-ge-house-hillsborough_12.xls": "hillsborough",
        "2022-ge-house-merrimack_5.xls": "merrimack",
        "2022-ge-house-rockingham_7.xls": "rockingham",
        "2022-ge-house-strafford_0.xls": "strafford",
        "2022-ge-house-sullivan_1.xls": "sullivan",
    },
    2024: {
        "2024-ge-state-senate-district-1-24_4.xls": None,
        "2024-ge-house-belknap.xls": "belknap",
        "2024-ge-house-carroll_0.xlsx": "carroll",
        "2024-ge-house-cheshire_0.xls": "cheshire",
        "2024-ge-house-coos_0.xls": "coos",
        "2024-ge-house-grafton_2.xlsx": "grafton",
        "2024-ge-house-hillsborough_7.xls": "hillsborough",
        "2024-ge-house-merrimack_2.xlsx": "merrimack",
        "2024-ge-house-rockingham_3.xlsx": "rockingham",
        "2024-ge-house-strafford_3.xls": "strafford",
        "2024-ge-house-sullivan_0.xlsx": "sullivan",
    },
}
PUBLISHER = "New Hampshire Secretary of State"
WINNERS = (
    "Winners are those declared in the earlier default result set for this race, from {publisher}. The Secretary of "
    "State's workbook gives the votes, not who won the seat."
)
RECOUNTED = "Votes are the Secretary of State's recount totals for every candidate the state recounted in this race."
DOUBLED = (
    "The earlier default result set for this race, from {publisher}, gives every candidate exactly twice the votes "
    "the Secretary of State certified, so this race's totals come from the Secretary of State instead."
)
UNNAMED = {("2022-ge-house-rockingham_7.xls", 1): ["Northwood", "Nottingham", "Totals"]}
NUMERIC = re.compile(r"-?\d+|[-\u2013\u2212]+")
DASH = re.compile(r"[-\u2013\u2212]+")
MISSPELLED = {"Brasill": "Brassill", "Sorenson": "Sorensen"}
ALIASES = {"atkinsonandgilmantonacgt": "atkinsonandgilmantonacademygrant"}


def town(text: str) -> str:
    text = text.lower().replace("&", "and").replace("'", "").replace("’", "")
    text = re.sub(r"\bpur\.?(?=\s|$)", "purchase", re.sub(r"\bgt\.?(?=\s|$)", "grant", text))
    text = re.sub(r"\bloc\.?(?=\s|$)", "location", text)
    text = re.sub(r"[^a-z0-9]", "", re.sub(r"\b(?:ward|wd)\.?\s*(\d+)", r"w\1", text))
    return ALIASES.get(text, text)


def link(year: int, name: str) -> str:
    return WAYBACK.format(snapshot="2024" if year == 2022 else "2026") + name


def years() -> list[int]:
    return [int(year) for year in os.environ.get("NH_YEARS", "2016,2018,2020,2024").split(",")]


def fetch() -> None:
    for year in years():
        for name in FILES[year]:
            url = link(year, name)
            try:
                raw.cached(SOURCE, url)
                continue
            except LookupError:
                pass
            for attempt in range(6):
                try:
                    raw.fetch(SOURCE, url, suffix=Path(name).suffix)
                    break
                except httpx.HTTPError, OSError:
                    time.sleep(5 * (attempt + 1))
            else:
                print(f"not fetched: {name}")
            time.sleep(1.2)


def parse() -> None:
    rows = []
    disagreeing: set[tuple[int, str | None, int]] = set()
    for year, name, county in ((y, n, c) for y in years() for n, c in FILES[y].items()):
        path = raw.cached(SOURCE, link(year, name)).path
        begin = len(rows)
        for sheet in pl.read_excel(
            path, sheet_id=0, has_header=False, infer_schema_length=0, raise_if_empty=False
        ).values():
            district = None
            labels: dict[int, str] = {}
            block: list[tuple[str | None, int, str, int, str, int]] = []
            carry = ""
            unnamed: list[str] = []
            for row in [*sheet.rows(), ("End",)]:
                cells = [" ".join(cell.split()) if cell else "" for cell in row]
                if (
                    len(cells) > 1
                    and re.match(r"State Senate Dist(?:rict|\.)?\s*\d+", cells[1])
                    and re.fullmatch(r"[\d\-: ]*", cells[0])
                ):
                    cells = [cells[1], *cells[2:]]
                head = cells[0]
                if not head and unnamed and any(re.fullmatch(r"\d+", cell) for cell in cells[1:]):
                    head = unnamed.pop(0)
                found = re.match(r"(?:State Senate Dist(?:rict|\.)?|District No\.?)\s*(\d+)|.*?(\d+) \(\d+\)", head)
                header = any(cell and not NUMERIC.fullmatch(cell) for cell in cells[1:])
                if found or header or head == "End" or head.upper().startswith("TOTAL"):
                    if head.upper().startswith("TOTAL"):
                        for index, cell in enumerate(cells):
                            if index in labels and cell and not DASH.fullmatch(cell):
                                counted = sum(line[5] for line in block if line[3] == index)
                                if counted != int(cell):
                                    message = f"{name} {district} {labels[index]}: {counted} against {cell}"
                                    if not labels[index].startswith(("Scatter", "BLC")):
                                        disagreeing.add((year, county, district))
                                    print(message)
                    rows.extend(block)
                    block = []
                if found:
                    district = int(found[1] or found[2])
                    unnamed = list(UNNAMED.get((name, district), []))
                    labels = {}
                if header:
                    labels = {}
                    carry = "" if found or re.fullmatch(r"[\d\-: ]*", head) else head
                    for index, cell in enumerate(cells[1:], 1):
                        if cell == "Recount":
                            labels[index] = labels.pop(index - 1) + " (recount)"
                        elif cell:
                            labels[index] = cell
                    continue
                head = head or carry
                if re.fullmatch(r"(?i)(?:court ordered )?recount", head) and labels:
                    towns = {line[4] for line in block}
                    if len(towns) != 1:
                        raise ValueError(f"{name} {district}: a recount row under {sorted(towns)}")
                    recount = {index: int(cells[index]) for index in labels if index < len(cells) and cells[index]}
                    block = [
                        (
                            *line[:2],
                            re.sub(r"(?: \(recount\))?$", " (recount)", line[2], count=1),
                            *line[3:5],
                            recount[line[3]],
                        )
                        if line[3] in recount
                        else line
                        for line in block
                    ]
                    continue
                if not head or not labels or district is None or head.upper().startswith("TOTAL"):
                    continue
                block.extend(
                    (county, district, labels[index], index, head, int(cells[index]))
                    for index in labels
                    if index < len(cells) and cells[index] and not DASH.fullmatch(cells[index])
                )
        rows[begin:] = [(year, *line) for line in rows[begin:]]
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "county": pl.String,
            "district": pl.Int32,
            "label": pl.String,
            "column": pl.Int32,
            "town": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    frame = frame.join(
        pl.DataFrame(
            list(disagreeing),
            schema={"year": pl.Int32, "county": pl.String, "district": pl.Int32},
            orient="row",
        ),
        on=["year", "county", "district"],
        how="anti",
        nulls_equal=True,
    )
    print("districts whose towns do not add to the printed totals:", sorted(disagreeing, key=str))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "county")
        .agg(pl.col("district").n_unique(), pl.col("votes").sum())
        .sort("year", "county")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's State Senate and State House general elections by town and ward, from the "
            "Secretary of State's official workbooks",
            polity="US-NH",
        )
        sources = {}
        units: dict[int, dict[str, str]] = {}
        for year in sorted(set(lines["year"])):
            title = f"{year} general election results, State Senate and State Representatives, by town and ward"
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
            for name in FILES[year]:
                cs.artifact(raw.cached(SOURCE, link(year, name)), source)
            units[year] = {}
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code like %s order by code",
                [f"{year}:NH:%%"],
            ).fetchall():
                parts = code.split(":", 4)
                if len(parts) == 5:
                    units[year].setdefault(town(parts[4] or parts[3]), code)
            missing = sorted(
                {
                    name
                    for name in lines.filter(pl.col("year") == year, pl.col("votes") > 0)["town"]
                    if town(name) not in units[year]
                }
            )
            if missing:
                raise ValueError(f"towns with no {year} unit: {missing}")
        caveats = {
            (str(entity), body)
            for entity, body in conn.execute("select entity_id, body_md from note where kind = 'caveat'").fetchall()
        }
        skipped: dict[str, list[str]] = {}
        values = []
        for (year, county, district), rows in lines.group_by("year", "county", "district", maintain_order=True):
            key = f"{DAYS[year].isoformat()}:NH:" + (
                f"house:{county}-{district}:g" if county else f"senate:{district}:g"
            )
            contest, election, default, first = conn.execute(
                """select c.id, c.election_id, rs.id, min(v.count_round)
                   from external_id x join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default and rs.status = 'active'
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s group by 1, 2, 3""",
                [key],
            ).fetchone() or (None, None, None, None)
            if contest is None:
                skipped.setdefault("no contest on file", []).append(key)
                continue
            options = conn.execute(
                """select o.id, o.ballot_label, o.kind,
                          coalesce((select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k
                                    where rs.id = %s and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [default, contest],
            ).fetchall()
            sums = dict(rows.group_by("label").agg(pl.col("votes").sum()).iter_rows())
            matched: dict[str, tuple[str, int]] = {}
            for label in sums:
                if label == "BLC":
                    continue
                name = re.sub(r"\s*\(recount\)$", "", label)
                name = re.sub(r"\s*\bw[-/]?in\.?$|,\s*[a-z]{1,3}$", "", name, flags=re.IGNORECASE)
                name = re.sub(r",?\s*\b(?:jr|sr|ii|iii|iv)\.?$", "", name, flags=re.IGNORECASE).strip()
                if re.fullmatch(r"scat\w*", name, flags=re.IGNORECASE):
                    hits = [o for o in options if o[2] == "write_in" and re.search(r"scatter", o[1], re.IGNORECASE)]
                else:
                    surname, _, given = name.partition(",") if county else (name.split()[-1], "", name.split()[0])
                    letters = re.sub(r"[^a-z]", "", MISSPELLED.get(surname, surname).lower())
                    given = re.sub(r"[^a-z]", "", given.lower())
                    words = {
                        o: re.sub(r"[^a-z ]", "", re.sub(r"\b(?:jr|sr|ii|iii|iv)\b\.?", "", o[1].lower())).split()
                        for o in options
                    }
                    hits = [
                        o for o, parts in words.items() if parts and parts[-1] == letters and parts[0].startswith(given)
                    ]
                    hits = hits or [
                        o
                        for o, parts in words.items()
                        if "".join(parts).endswith(letters) and parts[0].startswith(given)
                    ]
                if len(hits) != 1:
                    break
                matched[label] = (str(hits[0][0]), hits[0][3])
            else:
                if len({option for option, _ in matched.values()}) != len(matched):
                    skipped.setdefault("two columns match one candidate", []).append(key)
                    continue
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[year],
                    is_default=False,
                    completeness=1,
                )
                cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
                doubled = all(
                    votes == 2 * sums[label]
                    for label, (_, votes) in matched.items()
                    if sums[label] and not label.startswith("Scatter")
                )
                publisher = conn.execute(
                    "select s.publisher from result_set r join source s on s.id = r.source_id where r.id = %s",
                    [default],
                ).fetchone()[0]
                notes = [
                    *([("methodology", RECOUNTED)] if any(label.endswith("(recount)") for label in sums) else []),
                    *([("caveat", DOUBLED.format(publisher=publisher))] if doubled else []),
                ]
                declared = conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [default]
                ).fetchall()
                for option, kind, seats in declared:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
                if declared:
                    notes.append(("methodology", WINNERS.format(publisher=publisher)))
                for kind, body in notes:
                    if (str(result_set), body) not in caveats:
                        cs.add("note", entity_id=result_set, kind=kind, body_md=body)
                values.append(
                    rows.filter(pl.col("votes") > 0, pl.col("label").is_in(list(matched))).select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("town").map_elements(
                            lambda name: str(cs.unit("us.precinct", units[year][town(name)])), return_dtype=pl.String
                        ),
                        option_id=pl.col("label").replace_strict({label: pair[0] for label, pair in matched.items()}),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.lit(1, pl.Int16),
                        count_round=pl.lit(first, pl.Int16),
                        flags=pl.lit(0, pl.Int16),
                        value="votes",
                    )
                )
                continue
            skipped.setdefault("a candidate does not match", []).append(f"{key} {label}")
        for reason, keys in skipped.items():
            print(f"{len(keys)} contests skipped: {reason}", keys[:12])
        print(f"{len(values)} sets")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
