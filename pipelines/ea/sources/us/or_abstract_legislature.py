import re
import subprocess
import tempfile
from datetime import UTC, date, datetime
from itertools import takewhile
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.or_abstract_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://records.sos.state.or.us/ORSOSWebDrawer/Record/{}/File/document"
ABSTRACTS = {
    2000: (6920724, date(2000, 11, 7), "us.or_abstract_2000"),
    2002: (8411079, date(2002, 11, 5), SOURCE),
    2004: (6873503, date(2004, 11, 2), SOURCE),
    2006: (6873552, date(2006, 11, 7), SOURCE),
    2008: (6873598, date(2008, 11, 4), SOURCE),
    2010: (6873642, date(2010, 11, 2), SOURCE),
    2012: (6873690, date(2012, 11, 6), SOURCE),
    2014: (6873735, date(2014, 11, 4), SOURCE),
    2020: (13735450, date(2020, 11, 3), SOURCE),
}
PUBLISHER = "Oregon Secretary of State"
TITLE = "General Election Abstract of Votes"
TITLE_LINE = re.compile(r"^\s*(STATE REPRESENTATIVE|STATE SENATOR),\s+(\d+)(?:ST|ND|RD|TH)\s+DISTRICT\s*$")
STOP = re.compile(r"^\s*(\*+\s*Elected|WI = Write|\w+ \d+, \d{4}, General Election|\d+\s*$)")
OFFICE_2014 = re.compile(r"^[A-Z][A-Za-z .&'-]{3,40}$")
DISTRICT_2014 = re.compile(r"^(\d+)(?:st|nd|rd|th) District\b")
PARTY_2000 = re.compile(r"\([A-Z]{1,5}\)$")
CELL = re.compile(r"\S+(?: \S+)*? \([A-Z]{1,5}\)(?=\s|$)|\S+(?: (?!\*)\S+)*")
NAME_2000 = re.compile(r"^\*?\s*([^,]+),\s*(.*?)\s*\(([A-Za-z]+)\)$")
NAME_2014 = NAME_2000
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z .'-]*?)\s+((?:\d[\d,]*\s+)*\d[\d,]*)\s*$")


def fetch() -> None:
    for number, _, cache in ABSTRACTS.values():
        url = URL.format(number)
        if cache != SOURCE:
            continue
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "abstract.pdf"
            for _ in range(60):
                subprocess.run(
                    ["curl", "-sL", "-m", "120", "-C", "-", "-A", "Mozilla/5.0", "-o", str(target), url], check=False
                )
                if (
                    target.exists()
                    and subprocess.run(["pdfinfo", str(target)], capture_output=True, check=False).returncode == 0
                ):
                    raw.store(
                        SOURCE,
                        url,
                        target.read_bytes(),
                        suffix=".pdf",
                        retrieved_at=datetime.now(UTC),
                        media_type="application/pdf",
                    )
                    break
            else:
                raise RuntimeError(f"{url}: truncated every time")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '41%'"""
            ).fetchall()
        }
    rows: list[dict] = []
    for year, (number, day, cache) in ABSTRACTS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(cache, URL.format(number)).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        blocks: list[tuple[str, int, list[str]]] = []
        office = ""
        for line in text.splitlines():
            if year >= 2012:
                if line.startswith("Oregon Secretary of State"):
                    continue
                if OFFICE_2014.match(line.strip()):
                    office = {"State Senator": "senate", "State Representative": "house"}.get(line.strip(), "")
                    blocks.append(("", 0, []))
                elif office and (found := DISTRICT_2014.match(line.strip())):
                    blocks.append((office, int(found[1]), []))
                elif blocks and not STOP.match(line):
                    blocks[-1][2].append(line)
                elif blocks:
                    blocks.append(("", 0, []))
            elif found := TITLE_LINE.match(line):
                blocks.append(("house" if found[1].endswith("REPRESENTATIVE") else "senate", int(found[2]), []))
            elif blocks and not STOP.match(line):
                blocks[-1][2].append(line)
            elif blocks and STOP.match(line):
                blocks.append(("", 0, []))
        for chamber, district, lines in blocks:
            if not chamber:
                continue
            header = []
            body = []
            for line in lines:
                if not line.strip():
                    continue
                if not body and not re.match(r"^\s*County\b", line) and not ROW.match(line):
                    header.append(line)
                elif re.match(r"^\s*County\b", line):
                    first_line = line
                    body.append(None)
                else:
                    body.append(line)
            if not body or body[0] is not None:
                print(f"{year} {chamber} {district}: no County line, skipped")
                continue
            if year in (2000, 2012, 2014, 2020):
                tokens = [
                    [(found.start(), found.end(), found[0]) for found in CELL.finditer(line)]
                    for line in takewhile(lambda line: not ROW.match(line), lines)
                ]
                anchors = sorted(token for row in tokens for token in row if PARTY_2000.search(token[2]))
                if not anchors:
                    print(f"{year} {chamber} {district}: no party-tagged names, skipped")
                    continue
                cells: list[list[str]] = [[] for _ in anchors]
                for row in tokens:
                    for start, end, text in row:
                        if text not in ("County", "Misc.", "Misc"):
                            cells[
                                min(range(len(anchors)), key=lambda i: abs(anchors[i][0] + anchors[i][1] - start - end))
                            ].append(text)
                if year == 2000:
                    parsed = [NAME_2000.match(" ".join(cell)) for cell in cells]
                else:
                    parsed = [
                        NAME_2014.match(f"{' '.join(cell[:-1]).lstrip('*').strip()}, {cell[-1]}")
                        if len(cell) > 1
                        else None
                        for cell in cells
                    ]
                if not anchors or None in parsed:
                    print(f"{year} {chamber} {district}: header {cells} unreadable, skipped")
                    continue
                surnames = [found[1] for found in parsed if found]
                firsts = [f"{re.sub(r'\s*\(.*?\)', '', found[2])} ({found[3]})" for found in parsed if found]
            else:
                surnames = [
                    part.strip().lstrip("*").strip()
                    for part in re.split(r"\s{2,}", " ".join(header).strip())
                    if part.strip()
                ]
                surnames = [name for name in surnames if name != "Misc."]
                firsts = [
                    part.strip()
                    for part in re.split(r"\s{2,}", first_line.strip()[len("County") :].strip())
                    if part.strip()
                ]
                if len(surnames) != len(firsts):
                    print(f"{year} {chamber} {district}: {surnames} against {firsts}, skipped")
                    continue
            data: list[tuple[str, list[int]]] = []
            for line in body[1:]:
                matched = ROW.match(line) if line else None
                if matched is None:
                    continue
                data.append((matched[1].strip(), [int(n.replace(",", "")) for n in matched[2].split()]))
            width = len(surnames) + 1
            if any(len(values) != width for _, values in data):
                print(f"{year} {chamber} {district}: row widths {[len(v) for _, v in data]} against {width}, skipped")
                continue
            totals = [values for name, values in data if name.upper() == "TOTAL"]
            places = [(name, values) for name, values in data if name.upper() != "TOTAL"]
            sums = [sum(values[i] for _, values in places) for i in range(width)]
            if totals and totals[0] != sums:
                print(f"{year} {chamber} {district}: sums {sums} against total {totals[0]}, skipped")
                continue
            names = []
            for surname, first in zip(surnames, firsts, strict=True):
                party = re.search(r"\(([A-Za-z]+)\)\s*$", first)
                given = re.sub(r"\s*\([^)]*\)", "", first).strip()
                names.append((f"{given} {surname}".strip(), party[1] if party else ""))
            for county, values in places:
                code = counties.get(re.sub(r"[^a-z]", "", county.lower()))
                if code is None:
                    raise ValueError(f"{year}: no county {county}")
                for index, (candidate, party) in enumerate(names):
                    rows.append(
                        {
                            "year": year,
                            "chamber": f"{chamber}:{district}:g",
                            "day": day,
                            "code": code,
                            "candidate": candidate,
                            "writein": party == "WI",
                            "votes": values[index],
                        }
                    )
                rows.append(
                    {
                        "year": year,
                        "chamber": f"{chamber}:{district}:g",
                        "day": day,
                        "code": code,
                        "candidate": "WRITE-INS",
                        "writein": True,
                        "votes": values[-1],
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows)
        .with_columns(
            year=pl.col("year").cast(pl.Int32),
            special=pl.lit(False),
            state=pl.lit("OR"),
            district=pl.lit(None, pl.Int16),
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            source=pl.lit("OR"),
        )
        .group_by(
            "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "kind", "source"
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's 2000 through 2020 state House and Senate general elections by county from the abstracts of votes",
            polity="US-OR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.oregon.gov/elections/Pages/electionhistory.aspx",
            license="Public record",
        )
        for number, _, cache in ABSTRACTS.values():
            cs.artifact(raw.cached(cache, URL.format(number)), source)
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   where r.status = 'active' and x.value ~ '^20[012][0-9]-[0-9]{2}-[0-9]{2}:OR:(house|senate):'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))"""
            ).fetchall()
        }
        medsl.attach(
            cs,
            conn,
            frame.filter(
                ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(covered))
            ),
            {"OR": source},
            extend=True,
        )
        return cs.write()
