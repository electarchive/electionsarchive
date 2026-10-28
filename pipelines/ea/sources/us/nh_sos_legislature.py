import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nh_sos_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = (
    "https://web.archive.org/web/2024id_/https://www.sos.nh.gov/sites/g/files/ehbemt561/files/inline-documents/sonh/"
)
PAGE = (
    "https://www.sos.nh.gov/elections/elections/election-results/2022-election-results-0/2022-general-election-results"
)
DAY = date(2022, 11, 8)
FILES = {
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
}
PUBLISHER = "New Hampshire Secretary of State"
TITLE = "2022 general election results, State Senate and State Representatives, by town and ward"
WINNERS = (
    "Winners are those declared in Klarner's state legislative election returns for this race. The Secretary of "
    "State's workbook gives the votes, not who won the seat."
)
RECOUNTED = "Votes are the Secretary of State's recount totals for every candidate the state recounted in this race."
DOUBLED = (
    "Klarner's state legislative election returns give every candidate in this race exactly twice the votes the "
    "Secretary of State certified, so this race's totals come from the Secretary of State instead."
)
UNNAMED = {("2022-ge-house-rockingham_7.xls", 1): ["Northwood", "Nottingham", "Totals"]}
MISSPELLED = {"Brasill": "Brassill", "Sorenson": "Sorensen"}
ALIASES = {"atkinsonandgilmantonacgt": "atkinsonandgilmantonacademygrant"}


def town(text: str) -> str:
    text = text.lower().replace("&", "and").replace("'", "").replace("’", "")
    text = re.sub(r"\bpur\.?(?=\s|$)", "purchase", re.sub(r"\bgt\.?(?=\s|$)", "grant", text))
    text = re.sub(r"\bloc\.?(?=\s|$)", "location", text)
    text = re.sub(r"[^a-z0-9]", "", re.sub(r"\bward\s*(\d+)", r"w\1", text))
    return ALIASES.get(text, text)


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, WAYBACK + name, suffix=".xls")


def parse() -> None:
    rows = []
    for name, county in FILES.items():
        path = raw.cached(SOURCE, WAYBACK + name).path
        for sheet in pl.read_excel(path, sheet_id=0, has_header=False, infer_schema_length=0).values():
            district = None
            labels: dict[int, str] = {}
            block: list[tuple[str | None, int, str, int, str, int]] = []
            carry = ""
            unnamed: list[str] = []
            for row in [*sheet.rows(), ("End",)]:
                cells = [" ".join(cell.split()) if cell else "" for cell in row]
                head = cells[0]
                if not head and unnamed and any(re.fullmatch(r"\d+", cell) for cell in cells[1:]):
                    head = unnamed.pop(0)
                found = re.match(r"(?:State Senate District|District No\.?)\s*(\d+)|.*?(\d+) \(\d+\)", head)
                header = any(cell and not re.fullmatch(r"\d+", cell) for cell in cells[1:])
                if found or header or head == "End" or head.upper().startswith("TOTAL"):
                    if head.upper().startswith("TOTAL"):
                        for index, cell in enumerate(cells):
                            if index in labels and cell:
                                counted = sum(line[5] for line in block if line[3] == index)
                                if counted != int(cell):
                                    message = f"{name} {district} {labels[index]}: {counted} against {cell}"
                                    if not labels[index].startswith("Scatter"):
                                        raise ValueError(message)
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
                    if index < len(cells) and cells[index]
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "county": pl.String,
            "district": pl.Int32,
            "label": pl.String,
            "column": pl.Int32,
            "town": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("county").agg(pl.col("district").n_unique(), pl.col("votes").sum()).sort("county"))


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's 2022 State Senate and State House general elections by town and ward, from the "
            "Secretary of State's official workbooks",
            polity="US-NH",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, WAYBACK + name), source)
        units: dict[str, str] = {}
        for (code,) in conn.execute(
            "select code from reporting_unit where scheme_code = 'us.precinct' and code like '2022:NH:%%' order by code"
        ).fetchall():
            units.setdefault(town(code.split(":", 4)[4] or code.split(":", 4)[3]), code)
        missing = sorted({name for name in lines.filter(pl.col("votes") > 0)["town"] if town(name) not in units})
        if missing:
            raise ValueError(f"towns with no 2022 unit: {missing}")
        caveats = {
            (str(entity), body)
            for entity, body in conn.execute("select entity_id, body_md from note where kind = 'caveat'").fetchall()
        }
        skipped: dict[str, list[str]] = {}
        values = []
        for (county, district), rows in lines.group_by("county", "district", maintain_order=True):
            key = f"{DAY.isoformat()}:NH:" + (f"house:{county}-{district}:g" if county else f"senate:{district}:g")
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
                    source_id=source,
                    is_default=False,
                    completeness=1,
                )
                cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
                doubled = all(
                    votes == 2 * sums[label]
                    for label, (_, votes) in matched.items()
                    if sums[label] and not label.startswith("Scatter")
                )
                notes = [
                    *([("methodology", RECOUNTED)] if any(label.endswith("(recount)") for label in sums) else []),
                    *([("caveat", DOUBLED)] if doubled else []),
                ]
                declared = conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [default]
                ).fetchall()
                for option, kind, seats in declared:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
                if declared:
                    notes.append(("methodology", WINNERS))
                for kind, body in notes:
                    if (str(result_set), body) not in caveats:
                        cs.add("note", entity_id=result_set, kind=kind, body_md=body)
                values.append(
                    rows.filter(pl.col("votes") > 0, pl.col("label").is_in(list(matched))).select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("town").map_elements(
                            lambda name: str(cs.unit("us.precinct", units[town(name)])), return_dtype=pl.String
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
