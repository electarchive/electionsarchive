import re
import zipfile
from datetime import date
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.legislature_size"
VERSION = "1"
KLARNER = "https://dataverse.harvard.edu/api/access/datafile/2432030"
NCSL = "https://www.ncsl.org/resources/details/number-of-legislators-and-length-of-terms-in-years"
STATES = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}
LOWER = {
    "CA": "assembly",
    "NV": "assembly",
    "NY": "assembly",
    "WI": "assembly",
    "NJ": "general-assembly",
    "MD": "house-of-delegates",
    "VA": "house-of-delegates",
    "WV": "house-of-delegates",
}
SLIPS = {("CA", "house", 1947): 80, ("CA", "house", 1948): 80, ("MT", "senate", 1979): 50, ("MT", "senate", 1980): 50}
LATER = {("NY", "senate"): (2012, 63), ("WY", "house"): (2022, 62), ("WY", "senate"): (2022, 31)}


def fetch() -> None:
    raw.fetch(SOURCE, KLARNER, suffix=".xlsx")
    raw.fetch(SOURCE, NCSL, suffix=".html")


def emit() -> Path:
    klarner = raw.cached(SOURCE, KLARNER)
    ncsl = raw.cached(SOURCE, NCSL)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    book = zipfile.ZipFile(klarner.path)
    shared = [
        "".join(t.text or "" for t in si.iter(f"{ns}t"))
        for si in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).findall(f"{ns}si")
    ]
    table: list[dict[int, str]] = []
    for row in ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml")).iter(f"{ns}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{ns}c"):
            value = cell.find(f"{ns}v")
            if value is None or value.text is None:
                continue
            index = 0
            for letter in cell.get("r", "").rstrip("0123456789"):
                index = index * 26 + ord(letter) - 64
            cells[index - 1] = shared[int(value.text)] if cell.get("t") == "s" else value.text
        table.append(cells)
    header = {name: index for index, name in table[0].items()}
    sessions: dict[tuple[str, str], dict[int, tuple[int, int]]] = {}
    for cells in table[1:]:
        usps = STATES[cells[header["state"]]]
        for column, chamber in (
            ("sen_tot_in_sess", "legislature" if usps == "NE" else "senate"),
            ("hs_tot_in_sess", LOWER.get(usps, "house")),
        ):
            if header[column] not in cells:
                continue
            year = int(cells[header["year"]])
            if usps in ("AK", "HI") and year < 1959:
                continue
            seats = SLIPS.get(
                (usps, "house" if column == "hs_tot_in_sess" else "senate", year), int(cells[header[column]])
            )
            sessions.setdefault((usps, chamber), {})[year] = (int(cells[header["election_year"]]), seats)
    text = re.sub(
        r"\s+",
        " ",
        unescape(
            re.sub(
                r"<[^>]+>",
                " ",
                re.sub(r"<(script|style).*?</\1>", "", ncsl.path.read_text(errors="ignore"), flags=re.DOTALL),
            )
        ),
    )
    current: dict[tuple[str, str], int] = {}
    for name, usps in STATES.items():
        found = re.search(rf"\b{name} (\d+) \d (\d+|NA) (?:\d|NA) ", text)
        if found is None:
            raise ValueError(f"{name} is missing from the NCSL table")
        current[(usps, "legislature" if usps == "NE" else "senate")] = int(found.group(1))
        if usps != "NE":
            current[(usps, LOWER.get(usps, "house"))] = int(found.group(2))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Seats in each state legislative chamber from 1936 to the present, from Klarner's State Partisan Balance Data (1937–2011 sessions) and NCSL's current table",
            polity="US",
        )
        partisan = cs.source("Carl Klarner", "State Partisan Balance Data, 1937 - 2011")
        cs.add(
            "source",
            id=partisan,
            kind="academic",
            publisher="Carl Klarner",
            title="State Partisan Balance Data, 1937 - 2011",
            url="https://doi.org/10.7910/DVN/LZHMG3",
            license="CC0 1.0",
        )
        cs.artifact(klarner, partisan)
        conference = cs.source(
            "National Conference of State Legislatures", "Number of Legislators and Length of Terms in Years"
        )
        cs.add(
            "source",
            id=conference,
            kind="official_body",
            publisher="National Conference of State Legislatures",
            title="Number of Legislators and Length of Terms in Years",
            url=NCSL,
        )
        cs.artifact(ncsl, conference)
        for (usps, chamber), years in sorted(sessions.items()):
            body = cs.key("us.body", f"{usps}:{chamber}")
            if body in cs.minted:
                raise ValueError(f"{usps}:{chamber} must already exist")
            elected = dict(
                conn.execute(
                    """select extract(year from e.date_from)::int, min(e.date_from)
                       from contest c join election e on e.id = c.election_id
                       where c.body_id = %s and e.kind = 'general' group by 1""",
                    (body,),
                ).fetchall()
            )
            runs: list[tuple[int, int]] = []
            for year in sorted(years):
                election, seats = years[year]
                if not runs or runs[-1][1] != seats:
                    runs.append((election, seats))
            if (usps, chamber) in LATER:
                runs.append(LATER[(usps, chamber)])
            if runs[-1][1] != current[(usps, chamber)]:
                raise ValueError(f"{usps}:{chamber} ends at {runs[-1][1]} seats, NCSL has {current[(usps, chamber)]}")
            starts = [(elected[year], "day") if year in elected else (date(year, 1, 1), "year") for year, _ in runs]
            for index, (_, seats) in enumerate(runs):
                end = starts[index + 1] if index + 1 < len(runs) else (None, "day")
                cs.add(
                    "body_size",
                    body_id=body,
                    seats=seats,
                    valid_from=starts[index][0],
                    valid_from_precision=starts[index][1],
                    valid_to=end[0],
                    valid_to_precision=end[1],
                )
        return cs.write()
