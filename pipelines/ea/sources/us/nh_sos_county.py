import os
import re
import time
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_sos_county"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = Path(__file__).with_name("nh_sos_county_files.tsv")
WAYBACK = (
    "https://web.archive.org/web/2026id_/https://www.sos.nh.gov/sites/g/files/ehbemt561/files/inline-documents/sonh/"
)
PINNED = {"2022-ge-merrimack-county.xls": "20240114224201"}
PUBLISHER = "New Hampshire Secretary of State"
ALIASES = {
    "atkandgilmacgrant": "atkinsongilmantonacademygrant",
    "atkinsonandgilmantonacademygrant": "atkinsongilmantonacademygrant",
    "wentworthslocation": "wentworthlocation",
    "thompsonandmesspurchase": "thompsonandmeservespurchase",
}
OFFICES = (
    ("sheriff", "sheriff"),
    ("attorney", "district attorney"),
    ("countyattorney", "district attorney"),
    ("treasurer", "treasurer"),
    ("regofdeeds", "register of deeds"),
    ("registerofdeeds", "register of deeds"),
    ("regofprobate", "register of probate"),
    ("registerofprobate", "register of probate"),
)


def link(name: str) -> str:
    return WAYBACK.replace("/web/2026id_/", f"/web/{PINNED[name]}id_/") + name if name in PINNED else WAYBACK + name


def years() -> set[int]:
    return {int(year) for year in os.environ.get("NH_YEARS", "2016,2018,2020,2022,2024").split(",")}


def listing() -> list[dict[str, str]]:
    return [
        dict(zip(("day", "stage", "county", "file"), line.split("\t"), strict=True))
        for line in FILES.read_text().splitlines()[1:]
    ]


def town(text: str) -> str:
    text = re.sub(r",\s*NH$|\s+ward\s*\d+$|\s+\(.*\)$", "", text.strip().rstrip("*").strip(), flags=re.IGNORECASE)
    text = text.lower().replace("&", "and").replace("'", "").replace("’", "")
    text = re.sub(r"\bpur\.?(?=\s|$)", "purchase", re.sub(r"\bgt\.?(?=\s|$)", "grant", text))
    text = re.sub(r"\bloc\.?(?=\s|$)", "location", text)
    text = re.sub(r"[^a-z]", "", re.sub(r"\s+(township|town|city)$", "", text))
    return ALIASES.get(text, text)


def office(label: str, title: str) -> str | None:
    letters = re.sub(r"[^a-z0-9]", "", label.lower())
    district = re.fullmatch(r"dist(?:rict)?(?:no)?(\d)", letters)
    if district:
        return f"board of commissioners:{district[1]}"
    if letters.startswith("countycommissioner"):
        return "board of commissioners"
    for prefix, name in OFFICES:
        if letters.startswith(prefix):
            return name
    return None


def fetch() -> None:
    for entry in listing():
        if int(entry["day"][:4]) not in years():
            continue
        url = link(entry["file"])
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=Path(entry["file"]).suffix)
                break
            except httpx.HTTPError, OSError:
                time.sleep(5 * (attempt + 1))
        else:
            print(f"not fetched: {entry['file']}")
        time.sleep(1.2)


NUMBER = re.compile(r"\d[\d,]*(?:\.0+)?")
CANDIDATE = re.compile(
    r"scatter|no election|write-?ins?|w-in|(?:over|under)votes|(?:,\s*[a-z/&]{1,6}|\s+[rdli])\.?\*?$", re.IGNORECASE
)
WRITE_IN = re.compile(r"\bw-?in\b|write-?ins?", re.IGNORECASE)


def blocks(path: Path, name: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for sheet_name, sheet in pl.read_excel(
        path, sheet_id=0, has_header=False, infer_schema_length=0, raise_if_empty=False
    ).items():
        grid = [[" ".join(cell.split()) if cell else "" for cell in row] for row in sheet.rows()]

        def texts(row: list[str]) -> dict[int, str]:
            return {col: cell for col, cell in enumerate(row) if col and cell and not NUMBER.fullmatch(cell)}

        for index, row in enumerate(grid):
            names = texts(row)
            if index == 0 or not any(CANDIDATE.search(cell) for cell in names.values()) or row[0]:
                continue
            labels = texts(grid[index - 1])
            if not labels or any(CANDIDATE.search(cell) for cell in labels.values()):
                continue
            above = texts(grid[index - 2]) if index > 1 and not grid[index - 2][0] else {}
            title = next(iter(above.values())) if len(above) == 1 else ""
            body: list[list[str]] = []
            for line in grid[index + 1 :]:
                if not line[0]:
                    break
                body.append(line)
                if line[0].upper().startswith("TOTAL"):
                    break
            total = body.pop() if body and body[-1][0].upper().startswith("TOTAL") else None
            for col, candidate in names.items():
                group = max((label for label in labels if label <= col), default=min(labels))
                if not any(col < len(line) and NUMBER.fullmatch(line[col]) for line in body):
                    continue
                found.append(
                    {
                        "file": name,
                        "sheet": sheet_name,
                        "title": title,
                        "heading": " ".join(" ".join(row) for row in grid[:3]).lower(),
                        "office": labels[group],
                        "candidate": candidate,
                        "column": col,
                        "rows": [
                            (line[0], int(float(line[col].replace(",", ""))))
                            for line in body
                            if col < len(line) and NUMBER.fullmatch(line[col])
                        ],
                        "total": int(float(total[col].replace(",", "")))
                        if total and col < len(total) and NUMBER.fullmatch(total[col])
                        else None,
                    }
                )
    return found


def parse() -> None:
    rows = []
    for entry in listing():
        if int(entry["day"][:4]) not in years():
            continue
        try:
            path = raw.cached(SOURCE, link(entry["file"])).path
            found = blocks(path, entry["file"])
        except Exception as error:
            print(f"{entry['file']}: {error!r}")
            continue
        stage = entry["stage"]
        bad = [
            b
            for b in found
            if stage != "general"
            and stage not in str(b["heading"])
            and any(p in str(b["heading"]) for p in ("democratic", "republican", "libertarian"))
        ]
        if bad:
            print(f"{entry['file']}: listed as {stage}, headed {bad[0]['heading'][:90]!r}; skipped")
            continue
        for block in found:
            mapped = office(str(block["office"]), str(block["title"]))
            if str(block["title"]).lower().startswith("county commissioner") and mapped is None:
                mapped = None
            for place, votes in block["rows"]:
                rows.append(
                    (
                        entry["day"],
                        entry["stage"],
                        entry["county"],
                        entry["file"],
                        str(block["title"]),
                        str(block["office"]),
                        mapped,
                        str(block["candidate"]),
                        int(block["column"]),
                        place,
                        votes,
                        block["total"],
                    )
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.String,
            "stage": pl.String,
            "county": pl.String,
            "file": pl.String,
            "title": pl.String,
            "label": pl.String,
            "office": pl.String,
            "candidate": pl.String,
            "column": pl.Int32,
            "town": pl.String,
            "votes": pl.Int64,
            "printed": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "stage").agg(pl.col("file").n_unique(), pl.len()).sort("day", "stage"))


COLUMNS = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "supersedes_id", "completeness")
SUFFIX = re.compile(r",?\s*\b(?:jr|sr|ii|iii|iv)\b\.?$", re.IGNORECASE)
CONTEST = re.compile(r"^(\d{4}) (\w+) County (.+?) election(?:, District (\d))?(?:, (\w+) primary)?$")


def surname(text: str, party: bool = True) -> str:
    if party:
        text = re.sub(r"(?:,\s*[a-z/&]{1,6}|\s+[rdli])\.?$", "", text.strip(), flags=re.IGNORECASE)
    for _ in range(3):
        text = SUFFIX.sub("", text)
    return re.sub(r"[^a-z]", "", text.lower())


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet").filter(
        pl.col("office").is_not_null(), ~pl.col("candidate").str.contains(r"(?i)^(?:over|under)votes$")
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's county offices in the general and primary elections of "
            f"{', '.join(str(year) for year in sorted(years()))}, by town, from the Secretary of State's workbooks",
            polity="US-NH",
        )
        source = cs.source(PUBLISHER, "County office results by town, 2016-2024 general and state primary elections")
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="County office results by town, 2016-2024 general and state primary elections",
            url="https://www.sos.nh.gov/elections",
            license="Public record",
        )
        cell = ["day", "stage", "county", "office", "candidate", "town"]
        lines = lines.unique([*cell, "votes"], maintain_order=True)
        conflicts = lines.filter(pl.struct(cell).is_duplicated())
        if conflicts.height:
            print(f"{conflicts.height} cells differ between duplicate workbooks, dropped with their races")
            lines = lines.join(
                conflicts.select("day", "stage", "county", "office").unique(),
                on=["day", "stage", "county", "office"],
                how="anti",
            )
        for name in sorted(lines["file"].unique()):
            cs.artifact(raw.cached(SOURCE, link(name)), source)
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        }
        places = {
            (code[:5] if scheme == "us.town" else None, town(text)): (scheme, code)
            for scheme, code, text in conn.execute(
                """select u.scheme_code, u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where (u.scheme_code = 'us.town' or u.scheme_code = 'us.place') and u.code like '33%%'"""
            ).fetchall()
        }
        contests: dict[tuple[int, str, str, str | None, str | None], list[tuple[str, str, str]]] = {}
        for value, contest, text in conn.execute(
            """select x.value, x.entity_id, n.text from external_id x join name n on n.entity_id = x.entity_id
               where x.scheme = 'us.contest' and x.value like 'NH:local:%%' and n.text like '%% County %% election%%'"""
        ).fetchall():
            found = CONTEST.match(text)
            if found is None:
                continue
            year, county, name, district, party = found.groups()
            contests.setdefault(
                (int(year), county.lower(), name.lower(), district, party.lower() if party else None), []
            ).append((value, contest, text))
        keyed: dict[tuple[int, str, str, str | None, str | None], pl.DataFrame] = {}
        for (day, stage, county, mapped), rows in lines.group_by("day", "stage", "county", "office"):
            name, _, district = mapped.partition(":")
            keyed[(int(day[:4]), county, name, district or None, None if stage == "general" else stage)] = rows
        report: dict[str, list[str]] = {}
        values = []
        used: set[tuple[int, str, str, str | None, str | None]] = set()
        for key, rows in sorted(keyed.items(), key=lambda item: str(item[0])):
            year, county, name, district, party = key
            candidates = contests.get(key) or (
                contests.get((year, county, name, None, party)) if district is not None else None
            )
            tag = f"{year} {county} {name} {district or ''} {party or 'general'}".replace("  ", " ")
            if not candidates and district is None and name == "board of commissioners":
                spread = [
                    entry
                    for other, entries in contests.items()
                    if other[:3] == key[:3] and other[4] == party
                    for entry in entries
                ]
                candidates = spread if len(spread) == 1 else None
            if not candidates:
                report.setdefault("no contest", []).append(tag)
                continue
            if len(candidates) != 1:
                report.setdefault("two contests", []).append(f"{tag} {[c[0] for c in candidates]}")
                continue
            external, contest, _ = candidates[0]
            used.add(key)
            election, default, first = conn.execute(
                """select rs.election_id, rs.id, min(v.count_round)
                   from result_set rs join result_value v on v.result_set_k = rs.k
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active' group by 1, 2""",
                [contest],
            ).fetchone() or (None, None, None)
            if default is None:
                report.setdefault("no default set", []).append(f"{tag} {external}")
                continue
            options = conn.execute(
                """select o.id::text, o.ballot_label, o.kind,
                          coalesce((select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k
                                    where rs.id = %s and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [default, contest],
            ).fetchall()
            sums = rows.group_by("candidate").agg(pl.col("votes").sum(), pl.col("printed").first())
            if sums.filter(pl.col("printed").is_not_null() & (pl.col("votes") != pl.col("printed"))).height:
                report.setdefault("towns do not add to the printed total", []).append(f"{tag} {external}")
                continue
            matched: dict[str, str] = {}
            folded: dict[str, str] = {}
            failed = None
            others = [option[0] for option in options if option[2] == "others"]
            order = sorted(rows["candidate"].unique(maintain_order=True), key=lambda name: bool(WRITE_IN.search(name)))
            for candidate in order:
                if re.fullmatch(r"(?i)no election", candidate):
                    failed = "no election"
                    break
                if re.fullmatch(r"(?i)(?:scatter\w*|write-?ins?)", candidate):
                    hits = [option for option in options if option[2] == "others"]
                else:
                    letters = surname(WRITE_IN.sub("", candidate).strip(" ()*"))
                    hits = [
                        option
                        for option in options
                        if option[2] == "candidate"
                        and (surname(option[1], False).endswith(letters) or letters.endswith(surname(option[1], False)))
                    ]
                    if len(hits) > 1:
                        hits = [hit for hit in hits if hit[3]]
                        if len(hits) == 1:
                            report.setdefault("told apart by the compilation's votes", []).append(f"{tag} {candidate}")
                    if (
                        WRITE_IN.search(candidate)
                        and len(others) == 1
                        and (len(hits) != 1 or hits[0][0] in matched.values())
                    ):
                        folded[candidate] = others[0]
                        continue
                if len(hits) != 1:
                    failed = f"{candidate} matches {[hit[1] for hit in hits]}"
                    break
                matched[candidate] = hits[0][0]
            if failed is None:
                matched.update(folded)
            if failed is not None:
                report.setdefault("candidate mismatch", []).append(f"{tag} {external}: {failed}")
                continue
            named = [option for option in matched.values() if option not in others]
            if len(set(named)) != len(named):
                report.setdefault("two columns match one option", []).append(f"{tag} {external}")
                continue
            ours = {matched[c]: v for c, v in rows.group_by("candidate").agg(pl.col("votes").sum()).iter_rows()}
            theirs = {option[0]: option[3] for option in options if option[3]}
            if any(ours.get(option, 0) != theirs.get(option, 0) for option in {*ours, *theirs}):
                warded: dict[str, int] = {}
                for candidate, votes in (
                    rows.filter(pl.col("town").str.contains(r"(?i)\bward\s*\d"))
                    .group_by("candidate")
                    .agg(pl.col("votes").sum())
                    .iter_rows()
                ):
                    warded[matched[candidate]] = warded.get(matched[candidate], 0) + votes
                twice = all(
                    theirs.get(option, 0) == ours.get(option, 0) + warded.get(option, 0) for option in {*ours, *theirs}
                )
                gap = {
                    label: (theirs.get(oid, 0), ours.get(oid, 0))
                    for oid, label, _, _ in options
                    if theirs.get(oid, 0) != ours.get(oid, 0)
                }
                report.setdefault(
                    "differs: the compilation counts each city's wards twice" if twice else "differs: unexplained", []
                ).append(f"{tag} {external} {gap}")
            if not rows["votes"].sum():
                report.setdefault("no votes in the workbook", []).append(f"{tag} {external}")
                continue
            unit_codes: dict[str, tuple[str, str]] = {}
            for place in rows["town"].unique():
                hit = places.get((counties[county], town(place))) or places.get((None, town(place)))
                if hit is None:
                    raise ValueError(f"{county}: no unit for {place!r}")
                unit_codes[place] = hit
            result_set = cs.key(f"{SOURCE}.result_set", external)
            old = dict(
                zip(
                    COLUMNS,
                    conn.execute(f"select {', '.join(COLUMNS)} from result_set where id = %s", [default]).fetchone(),
                    strict=True,
                )
            )
            cs.add("result_set", **old, status="superseded", is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                supersedes_id=default,
                is_default=True,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            declared = conn.execute(
                "select option_id::text, kind, seats from outcome where result_set_id = %s", [default]
            ).fetchall()
            winners = {option for option, kind, _ in declared if kind in ("elected", "won")}
            ranked = sorted((votes, option) for option, votes in ours.items() if option not in others)[::-1]
            if (
                winners
                and len(winners) > len(ranked)
                or winners
                and {option for votes, option in ranked if votes >= ranked[len(winners) - 1][0]} != winners
            ):
                report.setdefault("declared winner is not the top vote-getter, outcomes left off", []).append(
                    f"{tag} {external}"
                )
                declared = []
            for option, kind, seats in declared:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            values.append(
                rows.filter(pl.col("votes") > 0)
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("town").map_elements(
                        lambda place, codes=unit_codes: str(cs.unit(*codes[place])), return_dtype=pl.String
                    ),
                    option_id=pl.col("candidate").replace_strict(matched),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    votes=pl.col("votes"),
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(value=pl.col("votes").sum())
            )
        for reason, items in report.items():
            print(f"{len(items)}: {reason}")
            for item in items[:40]:
                print("   ", item)
        unmatched = [
            key
            for key in contests
            if key[0] in years() and key not in keyed and key[3] is not None or key[0] in years() and key not in keyed
        ]
        print(f"{len(values)} sets; {len(unmatched)} contests on file with no workbook block")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
