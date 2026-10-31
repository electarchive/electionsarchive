import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ca_house_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://elections.cdn.sos.ca.gov/sov/2000-general/us-rep.pdf"
LANDING = "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results/general-election-november-7-2000/statement-vote"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, November 7, 2000 General Election: Representatives in Congress"
DAY = date(2000, 11, 7)
TITLE_LINE = re.compile(r"(\d+)(?:st|nd|rd|th) Congressional District\s*$")
ROW = re.compile(r"^(\S.*?)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%%'"""
            ).fetchall()
        }
        held: dict[int, list[tuple[str, int]]] = {}
        for key, name, votes in conn.execute(
            """select x.value, coalesce(n.text, o.ballot_label), sum(v.value)::bigint
               from external_id x join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               join result_value v on v.result_set_k = rs.k and v.measure = 1
               join contest_option o on o.k = v.option_k
               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               left join name n on n.entity_id = cy.person_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value ~ '^2000-11-07:CA:house:\\d+$'
               group by 1, 2"""
        ).fetchall():
            held.setdefault(int(key.rsplit(":", 1)[1]), []).append((name, votes))
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, check=True, text=True
    ).stdout
    blocks: list[tuple[int, list[str]]] = []
    for line in text.splitlines():
        if found := TITLE_LINE.search(line):
            blocks.append((int(found[1]), []))
        elif blocks:
            blocks[-1][1].append(line)
    rows = []
    for district, lines in blocks:
        header: list[str] = []
        places: list[tuple[str, list[int]]] = []
        totals: list[tuple[int, int]] = []
        for line in lines:
            found = ROW.match(line)
            if found is None:
                if not places and line.strip():
                    header.append(line)
                continue
            if found[1].startswith("District Totals"):
                totals = [(m.end(), int(m[0].replace(",", ""))) for m in re.finditer(r"[\d,]+", line)]
                break
            places.append(
                (counties[re.sub(r"[^a-z]", "", found[1].lower())], [int(v.replace(",", "")) for v in found[2].split()])
            )
        ends = [end for end, _ in totals]
        labels: dict[int, list[str]] = {end: [] for end in ends}
        for line in header[:-1]:
            for word in re.finditer(r"\S+", line):
                if column := next((end for end in ends if word.end() <= end + 1), None):
                    labels[column].append(word[0].strip("*"))
        names = []
        for end, value in totals[:-1]:
            matches = [name for name, votes in held.get(district, []) if votes == value]
            names.append(matches[0] if len(matches) == 1 else " ".join(labels[end]))
        sums = [sum(cells[i] for _, cells in places) for i in range(len(ends))]
        if any(len(cells) != len(ends) for _, cells in places) or sums != [value for _, value in totals]:
            print(f"District {district}: counties sum to {sums}, printed {totals}, skipped")
            continue
        rows.extend((district, code, name, cells[i]) for code, cells in places for i, name in enumerate(names))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"district": pl.Int16, "code": pl.String, "candidate": pl.String, "votes": pl.Int64}, orient="row"
    ).with_columns(
        year=pl.lit(2000, pl.Int32),
        day=pl.lit(DAY),
        chamber=pl.lit("house"),
        special=pl.lit(False),
        state=pl.lit("CA"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.lit(False),
        kind=pl.lit("county"),
        source=pl.lit("CA"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 2000 U.S. House general by county (Secretary of State, Statement of Vote)",
            polity="US-CA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"CA": source}, strict=True)
        return cs.write()
