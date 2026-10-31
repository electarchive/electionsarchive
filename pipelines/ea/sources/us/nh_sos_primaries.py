import re
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_sos_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = (
    "https://web.archive.org/web/2026id_/https://www.sos.nh.gov/sites/g/files/ehbemt561/files/inline-documents/sonh/"
)
PUBLISHER = "New Hampshire Secretary of State"
PAGES = {
    2016: "https://www.sos.nh.gov/elections/2016-election-results/2016-state-primary-election-results",
    2018: "https://www.sos.nh.gov/elections/2018-election-results/2018-primary-election-results",
    2020: "https://www.sos.nh.gov/elections/2020-election-results/2020-state-primary-election-results",
    2022: "https://www.sos.nh.gov/elections/2022-election-results/2022-state-primary-election-results",
    2024: "https://www.sos.nh.gov/2024-state-primary-election-results",
}
DAYS = {
    2016: date(2016, 9, 13),
    2018: date(2018, 9, 11),
    2020: date(2020, 9, 8),
    2022: date(2022, 9, 13),
    2024: date(2024, 9, 10),
}
MISDATED = {2022: date(2022, 9, 11)}
SYSTEMIC = ("k", "changeset_id", "recorded_from")
GOVERNOR = {
    ("governor", 2016, "democratic"): "2016-governor-democratic.xls",
    ("governor", 2016, "republican"): "2016-sp-governor-republican.xls",
    ("governor", 2018, "democratic"): "2018-sp-governor-democratic_0.xls",
    ("governor", 2018, "republican"): "2018-sp-governor-republican_0.xls",
    ("governor", 2018, "libertarian"): "2018-sp-governor-libertarian_0.xls",
    ("governor", 2020, "democratic"): "2020-sp-governor-democratic.xls",
    ("governor", 2020, "republican"): "2020-sp-governor-republican_0.xls",
    ("governor", 2022, "democratic"): "2022-sp-governor-democratic_3.xls",
    ("governor", 2022, "republican"): "2022-sp-governor-republican_1.xls",
    ("governor", 2024, "democratic"): "2024-sp-governor-democratic_4.xlsx",
    ("governor", 2024, "republican"): "2024-sp-governor-republican_2.xlsx",
}
FILES = GOVERNOR
ALIASES = {
    "atandgilacademygrant": "atkinsonandgilmantonacademygrant",
    "atkandgilmacgrant": "atkinsonandgilmantonacademygrant",
    "atkinsonandgilmantonacademygrant": "atkinsonandgilmantonacademygrant",
    "thompsonandmesspurchase": "thompsonandmeservespurchase",
    "wentworthslocation": "wentworthlocation",
}
POOLED = re.compile(r"scatter|write-?ins?|w-in", re.IGNORECASE)
IGNORED = re.compile(r"over\s*votes?|under\s*votes?|blank", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d,]*(?:\.0+)?|[-\u2212\u2013]")


def key(text: str) -> str:
    text = re.sub(r",\s*NH$|\s+Ward\s*\d+$", "", text.strip().rstrip("*").strip(), flags=re.IGNORECASE)
    text = text.lower().replace("&", "and").replace("'", "").replace("’", "")
    text = re.sub(r"\bmes\.?(?=\s)", "meserves", text)
    text = re.sub(r"\bpur\.?(?=\s|$)", "purchase", re.sub(r"\bgt\.?(?=\s|$)", "grant", text))
    text = re.sub(r"\bloc\.?(?=\s|$)", "location", text)
    text = re.sub(r"[^a-z]", "", re.sub(r"\s+(township|town|city)$", "", text))
    return ALIASES.get(text, text)


def fetch() -> None:
    for name in dict.fromkeys(FILES.values()):
        url = WAYBACK + name
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


def sheets(path: Path) -> dict[str, list[list[str]]]:
    return {
        name: [[" ".join(cell.split()) if cell else "" for cell in row] for row in sheet.rows()]
        for name, sheet in pl.read_excel(
            path, sheet_id=0, has_header=False, infer_schema_length=0, raise_if_empty=False
        ).items()
    }


def count(cell: str) -> int:
    return 0 if cell in "-\u2212\u2013" else int(float(cell.replace(",", "")))


def tables(path: Path, name: str) -> dict[str, list[tuple[str, dict[str, int]]]]:
    counties: dict[str, list[tuple[str, dict[str, int]]]] = {}
    summary: dict[str, dict[str, int]] = {}
    for sheet in sheets(path).values():
        names: dict[int, str] = {}
        label = ""
        towns: list[tuple[str, dict[str, int]]] = []
        for row in sheet:
            head = row[0]
            if sum(bool(cell) and not NUMBER.fullmatch(cell) for cell in row[1:]) >= 2 and head:
                names = {
                    column: cell.rstrip("*").strip()
                    for column, cell in enumerate(row)
                    if column and cell and not NUMBER.fullmatch(cell)
                }
                label = head
                towns = []
                continue
            if not names or not head or head.startswith("*") or not any(row[1:]):
                continue
            cells = {
                names[column]: count(row[column])
                for column in names
                if column < len(row) and row[column] and NUMBER.fullmatch(row[column])
            }
            if not head.upper().startswith("TOTAL"):
                towns.append((head, cells))
                continue
            sums: dict[str, int] = {}
            for _, line in towns:
                for cell, votes in line.items():
                    sums[cell] = sums.get(cell, 0) + votes
            differing = {
                cell: (sums.get(cell, 0), votes) for cell, votes in cells.items() if sums.get(cell, 0) != votes
            }
            if differing:
                raise ValueError(f"{name} {label}: rows against printed totals {differing}")
            if label.lower().startswith("summary"):
                summary = {town.lower(): line for town, line in towns}
            else:
                counties[re.sub(r"\s+County$", "", label, flags=re.IGNORECASE).strip().lower()] = towns
            names = {}
    for county, towns in counties.items():
        for cell, votes in summary.get(county, {}).items():
            if sum(line.get(cell, 0) for _, line in towns) != votes:
                print(
                    f"{name} {county}: {cell} is {votes} on the summary sheet, {sum(line.get(cell, 0) for _, line in towns)} in the towns"
                )
    if set(summary) != set(counties):
        raise ValueError(f"{name}: summary counties {sorted(summary)} against {sorted(counties)}")
    return counties


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
    for (office, year, party), name in FILES.items():
        counties = tables(raw.cached(SOURCE, WAYBACK + name).path, name)
        if len(counties) != 10:
            raise ValueError(f"{name}: {len(counties)} counties")
        missing = set()
        for county, towns in counties.items():
            for town, cells in towns:
                place = places.get((codes[county], key(town))) or places.get((None, key(town)))
                if place is None:
                    missing.add((county, town))
                    continue
                for cell, votes in cells.items():
                    rows.append((office, year, party, county, *place, cell, votes))
        if missing:
            raise ValueError(f"{name}: no town for {sorted(missing)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "office": pl.String,
            "year": pl.Int32,
            "party": pl.String,
            "county": pl.String,
            "code": pl.String,
            "kind": pl.String,
            "label": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).group_by("office", "year", "party", "county", "code", "kind", "label").agg(pl.col("votes").sum()).write_parquet(
        PARSED / "towns.parquet"
    )


def person(label: str) -> list[str]:
    name = re.sub(r",\s*[a-z]{1,3}\.?\s*$", "", label.strip(), flags=re.IGNORECASE)
    name = re.sub(r"\b(?:jr|sr|ii|iii|iv)\b\.?", "", name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z ]", "", name.lower()).split()


def choose(label: str, options: list[tuple[str, str]]) -> str | None:
    words = person(label)
    if not words:
        return None
    exact = [
        option
        for option, ballot in options
        if (parts := person(ballot)) and parts[-1] == words[-1] and parts[0][:3] == words[0][:3]
    ]
    if len(exact) == 1:
        return exact[0]
    scored = sorted(
        (
            (SequenceMatcher(None, " ".join(words), " ".join(person(ballot))).ratio(), option)
            for option, ballot in options
        ),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        return scored[0][1]
    return None


def move(conn, cs: Changeset, contest, election, contest_key: str, slug: str) -> None:
    cursor = conn.execute("select * from contest where id = %s", [contest])
    columns = [column.name for column in cursor.description]
    for row in cursor.fetchall():
        values = {c: v for c, v in zip(columns, row, strict=True) if c not in SYSTEMIC}
        cs.add("contest", **{**values, "election_id": election})
    cs.add("external_id", entity_id=contest, scheme="us.contest", value=contest_key)
    cs.add("slug", path=f"us/nh/{contest_key[:10]}-primary/{slug}", entity_id=contest)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "towns.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2016 to 2024 governor primaries by town, from the Secretary of State's workbooks, "
            "with city wards summed to their city",
            polity="US-NH",
        )
        sources = {}
        for year in sorted(set(frame["year"])):
            title = f"{year} state primary election results, governor, by town"
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
        for (office, year, party), name in FILES.items():
            cs.artifact(raw.cached(SOURCE, WAYBACK + name), sources[year])
        places = {
            (scheme, code): cs.unit(f"us.{scheme}", code)
            for scheme, code in frame.select("kind", "code").unique().iter_rows()
        }
        values = []
        for (office, year, party), lines in frame.group_by("office", "year", "party", maintain_order=True):
            contest_key = f"{DAYS[year]}:NH:{office}:{party}"
            legacy_key = f"{MISDATED[year]}:NH:{office}:{party}" if year in MISDATED else None
            found = None
            for key in (contest_key, legacy_key):
                found = (
                    key
                    and conn.execute(
                        """select c.id, c.election_id,
                              (select count(*) from result_set r join changeset w on w.id = r.changeset_id
                               where r.contest_id = c.id and r.status = 'active' and w.pipeline <> %s)
                       from external_id x join contest c on c.id = x.entity_id
                       where x.scheme = 'us.contest' and x.value = %s""",
                        [SOURCE, key],
                    ).fetchone()
                )
                if found:
                    break
            if found is None:
                raise ValueError(f"{contest_key}: no contest on file")
            contest, election, existing = found
            if existing:
                raise ValueError(f"{contest_key}: already has {existing} result sets")
            target = conn.execute(
                "select entity_id from slug where path = %s", [f"us/nh/{DAYS[year]}-primary"]
            ).fetchone()
            if target is not None and target[0] != election:
                election = target[0]
                move(conn, cs, contest, election, contest_key, f"{office}-{party}")
            options = [
                (str(option), label)
                for option, label, kind in conn.execute(
                    "select id, ballot_label, kind from contest_option where contest_id = %s and parent_option_id is null",
                    [contest],
                ).fetchall()
                if kind == "candidate"
            ]
            pool = [
                str(option)
                for (option,) in conn.execute(
                    "select id from contest_option where contest_id = %s and kind = 'write_in'", [contest]
                ).fetchall()
            ]
            if len(pool) != 1:
                raise ValueError(f"{contest_key}: {len(pool)} write-in options")
            mapping: dict[str, str] = {}
            for label in lines["label"].unique():
                if IGNORED.search(label):
                    continue
                if POOLED.search(label):
                    mapping[label] = pool[0]
                    continue
                option = choose(label, options)
                if option is None:
                    raise ValueError(f"{contest_key}: {label} matches no option of {[o[1] for o in options]}")
                mapping[label] = option
            if len({option for label, option in mapping.items() if option != pool[0]}) != len(
                [label for label, option in mapping.items() if option != pool[0]]
            ):
                raise ValueError(f"{contest_key}: two columns match one candidate")
            prior = conn.execute(
                "select id from result_set where contest_id = %s and source_id = %s", [contest, sources[year]]
            ).fetchone()
            result_set = prior[0] if prior else cs.key(f"{SOURCE}.result_set", contest_key)
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
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            values.append(
                lines.filter(pl.col("label").is_in(list(mapping)))
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.struct("kind", "code").map_elements(
                        lambda row: str(places[(row["kind"], row["code"])]), return_dtype=pl.String
                    ),
                    option_id=pl.col("label").replace_strict(mapping),
                    value="votes",
                )
                .group_by("result_set_id", "unit_id", "option_id")
                .agg(pl.col("value").sum())
                .with_columns(
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                )
            )
            print(contest_key, len(mapping), "columns", lines["votes"].sum(), "votes")
        cs.frame("result_value", pl.concat(values))
        return cs.write()
