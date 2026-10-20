import re
import struct
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.ca_sov_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
SITE = "https://elections.cdn.sos.ca.gov/sov/"
FILE = "2000-primary/sov-complete.pdf"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, March 7, 2000 Primary Election"
DAY = "2000-03-07"
PARTIES = {
    "DEM": "democratic",
    "REP": "republican",
    "LIB": "libertarian",
    "NL": "natural-law",
    "REF": "reform",
    "GRN": "green",
    "AI": "american-independent",
}
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*|\d+")
DISTRICT = re.compile(r"(\d+)(?:st|nd|rd|th) Congressional District")
Box = tuple[int, int, int, int]
Row = tuple[str, list[tuple[int, Box]], int]


def fetch() -> None:
    raw.fetch(SOURCE, SITE + FILE, suffix=".pdf")


def lines(pdf: Path, number: int) -> list[list[tuple[str, Box]]]:
    words = sorted(scan.layer(pdf, number), key=lambda word: (word[1][1] + word[1][3]) / 2)
    found: list[list[tuple[str, Box]]] = []
    last = None
    for word in words:
        middle = (word[1][1] + word[1][3]) / 2
        if last is None or middle - last > 10:
            found.append([])
        found[-1].append(word)
        last = middle
    return [sorted(line, key=lambda word: word[1][0]) for line in found]


def split(line: list[tuple[str, Box]]) -> tuple[str, list[tuple[int, Box]]]:
    first = next((index for index, (text, _) in enumerate(line) if NUMBER.fullmatch(text)), len(line))
    return (
        " ".join(text for text, _ in line[:first]),
        [(int(text.replace(",", "")), box) for text, box in line[first:] if NUMBER.fullmatch(text)],
    )


def parse() -> None:
    pdf = raw.cached(SOURCE, SITE + FILE).path
    with connect() as conn:
        counties = {
            text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            )
        }
        options = conn.execute(
            """select x.value, o.id::text, o.ballot_label, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ %s group by 1, 2, 3""",
            [f"^{DAY}:CA:(house|senate)(:|$)"],
        ).fetchall()
    blocks: dict[tuple[int, tuple[str, ...]], dict[str, object]] = {}
    turnout: list[dict[str, object]] = []
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout)[1])  # type: ignore[index]
    section: str | None = None
    district = 0
    heads: tuple[str, ...] = ()
    for number in range(1, pages + 1):
        page = lines(pdf, number)
        text = " ".join(" ".join(word for word, _ in line) for line in page[:6])
        if "County-by-County" in text or "Summary of Votes" in text:
            section = None
            continue
        if "VOTER PARTICIPATION STATISTICS BY COUNTY" in text:
            section = "turnout"
        elif "Representatives in Congress" in text:
            section, district = "house", 0
        elif "United States Senator" in text and "County-by-County" not in text and "Summary" not in text:
            section, district = "senate", 0
        elif number > 150 and section in {"house", "senate", "turnout"}:
            if re.search(r"State Senators?|Member of the State Assembly|Proposition|Ballot Measures", text):
                section = None
        if section is None:
            continue
        for line in page:
            label, values = split(line)
            joined = " ".join(word for word, _ in line)
            found = DISTRICT.search(joined)
            if section == "house" and found:
                district = int(found[1])
                continue
            tokens = tuple(word for word, _ in line)
            if section != "turnout" and tokens and all(word in PARTIES or word in {"Others", "(W/I)"} for word in tokens):
                heads = tuple(word for word in tokens if word in PARTIES)
                continue
            if not values or not label or label.startswith("Percent"):
                continue
            if section == "turnout":
                if label in counties or label == "State Totals":
                    turnout.append({"county": label, "values": [value for value, _ in values], "boxes": [box for _, box in values], "page": number})
                continue
            if len(values) not in {len(heads), len(heads) + 1}:
                if len(values) > 1:
                    print(f"page {number} {label}: {len(values)} values for {len(heads)} candidates")
                continue
            key = (district, heads)
            block = blocks.setdefault(key, {"rows": [], "totals": None})
            if label in {"District Totals", "State Totals"}:
                block["totals"] = values[: len(heads)]
            elif label in counties:
                block["rows"].append((label, values[: len(heads)], number))  # type: ignore[union-attr]
            else:
                print(f"page {number}: unread row {label!r} {len(values)} values")
    rows: list[dict[str, object]] = []
    failed: set[str] = set()
    pool: dict[tuple[str, int], list[tuple[str, str, str, int]]] = {}
    for contest, option, label, votes in options:
        _, _, office, *rest = contest.split(":")
        pool.setdefault((office, int(rest[0]) if office == "house" else 0), []).append((contest, option, label, votes))
    for (office, seat), entries in sorted(pool.items()):
        columns = [
            (key, index)
            for key in blocks
            if (key[0] == seat if office == "house" else key[0] == 0)
            for index in range(len(key[1]))
        ]
        for contest, option, label, votes in entries:
            party = contest.split(":")[-1]
            hits = [
                (key, index)
                for key, index in columns
                if len(blocks[key]["totals"] or []) > index and blocks[key]["totals"][index][0] == votes  # type: ignore[index]
            ]
            if len(hits) > 1:
                hits = [hit for hit in hits if PARTIES[hit[0][1][hit[1]]] == party] or hits
            if len(hits) != 1 or votes == 0:
                failed.add(contest)
                print(f"{office} {seat}: {label} ({votes}) matches {len(hits)} columns")
                continue
            (key, index) = hits[0]
            cells = [(county, values[index], page) for county, values, page in blocks[key]["rows"]]  # type: ignore[union-attr]
            if sum(cell[1][0] for cell in cells) != votes:
                failed.add(contest)
                print(f"{office} {seat}: {label} sums to {sum(cell[1][0] for cell in cells)}, filed {votes}")
                continue
            for county, (value, box), page in cells:
                rows.append(
                    {"contest": contest, "option": option, "county": county, "votes": value, "page": page, "box": list(box)}
                )
    leftover = {contest for contest, *_ in options} - {str(row["contest"]) for row in rows}
    print(f"{len(leftover)} contests with no lines: {sorted(leftover)}")
    stale = failed - leftover
    keep = [row for row in rows if row["contest"] not in failed]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(keep).write_parquet(PARSED / "lines.parquet")
    print(f"{len({row['contest'] for row in keep})} contests, {len(keep)} lines, {len(stale)} stale")
    pl.DataFrame(
        [
            {"county": row["county"], "registered": row["values"][2], "ballots": row["values"][5], "page": row["page"], "box_registered": row["boxes"][2], "box_ballots": row["boxes"][5]}
            for row in turnout
            if len(row["values"]) >= 6  # type: ignore[arg-type]
        ]
    ).write_parquet(PARSED / "turnout.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    voters = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's U.S. House and Senate primaries by county on March 7, 2000, "
            "from the Secretary of State's Statement of Vote",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
            license="Public record",
        )
        pdf = raw.cached(SOURCE, SITE + FILE)
        artifact = cs.artifact(pdf, source)
        pages: dict[int, UUID] = {}
        for number in sorted({*frame["page"].to_list(), *voters["page"].to_list()}):
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(pdf.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            pages[number] = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        counties: dict[str, UUID] = {
            text: unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        }
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id where scheme = 'us.contest' and value ~ %s""",
                [f"^{DAY}:CA:(house|senate)(:|$)"],
            ).fetchall()
        )
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
        elections: set[UUID] = set()
        for (key,), lines in frame.group_by("contest", maintain_order=True):
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            contest = contests[key]
            ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            elections.add(election)
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            totals = {
                UUID(option): votes for option, votes in lines.group_by("option").agg(pl.col("votes").sum()).iter_rows()
            }
            for option, county, votes, number, box in lines.select("option", "county", "votes", "page", "box").iter_rows():
                fact = {
                    "result_set_id": result_set,
                    "unit_id": counties[county],
                    "option_id": UUID(option),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=votes)
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=pages[number],
                    source_id=None,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                )
            for option, kind, seats in outcomes:
                if option in totals:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for election in sorted(elections):
            result_set = cs.key(f"{SOURCE}.turnout", str(election))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election not in held,
                completeness=1,
            )
            for county, registered, ballots, number, box_registered, box_ballots in voters.filter(
                pl.col("county") != "State Totals"
            ).select("county", "registered", "ballots", "page", "box_registered", "box_ballots").iter_rows():
                for measure, value, box in ((2, registered, box_registered), (3, ballots, box_ballots)):
                    fact = {
                        "result_set_id": result_set,
                        "unit_id": counties[county],
                        "option_id": None,
                        "channel": 0,
                        "measure": measure,
                        "count_round": 0,
                    }
                    cs.add("result_value", **fact, flags=0, value=value)
                    cs.add(
                        "value_locator",
                        **fact,
                        page_id=pages[number],
                        source_id=None,
                        x0=box[0],
                        y0=box[1],
                        x1=box[2],
                        y1=box[3],
                    )
        return cs.write()
