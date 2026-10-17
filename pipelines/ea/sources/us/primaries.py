import html
import io
import itertools
import json
import re
import struct
import subprocess
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID
from xml.etree import ElementTree

import fastexcel
import polars as pl

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.sources.us import elstats, ncsbe, openelections, wa_local
from ea.store import DATA, connect

SOURCE = "us.primaries"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
CALIFORNIA = "https://elections.cdn.sos.ca.gov/sov/2024-primary/sov/79-us-rep-congress.xlsx"
NORTH_CAROLINA = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/2024_03_05/results_pct_20240305.zip"
MINNESOTA = "https://electionresultsfiles.sos.mn.gov/20240813/{}.txt"
MARYLAND = "https://elections.maryland.gov/elections/archive/2024/election_data/PP24_{:02d}Precincts{}.csv"
WASHINGTON = "https://results.vote.wa.gov/results/{0}/export/{0}_allcounties.csv"
INDIANA = "https://enr.indianavoters.in.gov/archive/2024Primary/data/OffCatC_{}_B.json"
IN_CATEGORIES = (1012, 1006, 1005, 1009)
ALABAMA = (
    "https://www.sos.alabama.gov/sites/default/files/election-data/2024-04/2024%20Primary%20Precinct%20Results.ZIP",
    "https://www.sos.alabama.gov/sites/default/files/election-data/2024-07/2024%20Primary%20Runoff%20Precinct%20Results.zip",
)
MASSACHUSETTS = [
    f"{elstats.MASSACHUSETTS}search/year_from:2024/year_to:2024/office_id:{office}{stage}"
    for office, stage in (("1", ""), ("6", ""), ("5", "/stage:Democratic"), ("5", "/stage:Republican"))
]
IOWA = "https://electionresults.iowa.gov/IA/121508/344769/reports/detailxml.zip"
KANSAS = "https://sos.ks.gov/elections/24elec/2024-Primary-Election-United-States-House-of-Representatives-Precinct%20Results.xlsx"
KS_PRESIDENT = "https://sos.ks.gov/elections/24elec/2024-Presidential-Preference-Primary-Official-Vote-Totals{}.pdf"
TED = "https://data.capitol.texas.gov/api/3/action/package_show?id=2024_{}"
TEXAS = ("democratic_primary", "republican_primary", "democratic_runoff", "republican_runoff")
OHIO = (
    "https://web.archive.org/web/20240420171448id_/https://www.ohiosos.gov/globalassets/elections/2024/pri/official/precinct-level-official-results-2024-primary---republican.xlsx",
    "https://web.archive.org/web/20240420171451id_/https://www.ohiosos.gov/globalassets/elections/2024/pri/official/precinct-level-official-results-primary-2024---democratic.xlsx",
)
FLORIDA = "https://flelectionfiles.floridados.gov/enightfilespublic/{}_ElecResultsFL.txt"
OREGON = "https://records.sos.state.or.us/ORSOSCMSearch/Search/DocumentStream.ashx?uri=13735456"
MISSOURI = "https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/ActualResults-August62024.pdf"
MAINE = "https://lldc.mainelegislature.org/Open/Elections/Returns/2024/{}.xlsx"
NEW_JERSEY = "https://www.nj.gov/state/elections/assets/pdf/election-results/2024/2024-official-primary-results-{}.pdf"
NEBRASKA = "https://sos.nebraska.gov/sites/default/files/doc/elections/2024/2024%20Primary%20Canvass%20Book.pdf"
NJ_FILES = {"president": "President", "us-senate": "U.S. Senate", "us-house": "U.S. House"}
ORDINALS = (
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
)
ME_FILES = {
    "20240305/elec_20240305a": ("President", None, "Democratic"),
    "20240305/elec_20240305b": ("President", None, "Republican"),
    "20240611/elec_20240611a": ("U.S. Senate", None, "Democratic"),
    "20240611/elec_20240611c": ("U.S. Senate", None, "Republican"),
    "20240611/elec_20240611e": ("U.S. House", "1", "Democratic"),
    "20240611/elec_20240611i": ("U.S. House", "1", "Republican"),
    "20240611/elec_20240611k": ("U.S. House", "2", "Democratic"),
    "20240611/elec_20240611m": ("U.S. House", "2", "Republican"),
}
OPENELECTIONS = {
    "AZ": "bf14331521f62f74cb17848ab11ca3dfd27f4f82",
    "CT": "f948d0a1ce36871de4a1d696612ab2a268fd0758",
    "GA": "36b7453793848c02d8f41601d9304a66ad828dd0",
    "KY": "025799258505414d62830ea75fd34b58cb2ee59b",
    "MI": "2103114f669c2bb25f19792bf5ca290300381396",
    "MS": "84d07603a7eacefa07cb58e90fcb12ce76fcdcce",
    "ND": "c25145fbab08f849e6eeec319f516d7916533ea1",
    "PA": "e79aeb525b4bb4e834c21bab0699fdbf9b6b2e9a",
    "RI": "977565f9d7cf7a32d3a2a454282b5fd291cef291",
    "SC": "5e05b67e15f3614a20d31144df83248235cba6f2",
    "SD": "9e01b9aea628e9ae17ada6656b649daf319fd212",
    "TN": "4cb8cc5d0bb74b45dc05ad03c80c41b246301c1c",
    "UT": "a023ab4d87c4a93f0913f505c6fcbad1a585db57",
    "WV": "49d32c279d0e02d740c8af4fcb5b5eee380fbed4",
    "WY": "d4d430672e8823beeb7dbc9274362992739155c3",
}
PARTIAL = ("AZ", "PA")
ELECTIONSTATS = ("CO", "CT", "ID", "NH", "NM", "NY", "VA", "VT")
TYPOS = {
    "MO": {"kansascity": "2938000"},
    "MS": {"jeffdavis": "28065"},
    "TN": {"williamsonx": "47187"},
    "VT": {"essexjunction": "50007"},
}
DAYS = {("RI", date(2024, 8, 13)): date(2024, 9, 10)}
CHECKED = {
    "2024-05-14:MD:president:republican",
    "2024-05-14:MD:house:3:democratic",
    "2024-05-07:IN:governor:republican",
    "2024-03-05:AL:house:3:republican",
    "2024-03-05:AL:house:4:republican",
    "2024-06-04:NJ:president:republican",
}
PARTIES = {
    "d": "democratic",
    "dem": "democratic",
    "democrat": "democratic",
    "democratic": "democratic",
    "democratic party": "democratic",
    "r": "republican",
    "rep": "republican",
    "republican": "republican",
    "republican party": "republican",
    "lib": "libertarian",
    "lbn": "libertarian",
    "libertarian": "libertarian",
    "dfl": "minnesota-democratic-farmer-labor",
    "grn": "green",
    "green": "green",
    "legal marijuana now": "legal-marijuana-now",
}
SKIPPED = r"(?i)^(registered voters|ballots cast|total ballots|times (blank|over|under) voted|total ?votes|under ?votes?|over ?votes?|no candidate qualified|blank|void)\b"
COLUMNS = ("county", "precinct", "office", "district", "party", "candidate", "votes")


def search(state: str) -> str:
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {
            "search": json.dumps(
                {
                    "global": {"years": {"from": 2024, "to": 2024}},
                    "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
                    "contests": {
                        "candidates": [],
                        "divisions": [],
                        "offices": [
                            {"free": office}
                            for office in (
                                "U.S. House",
                                "U.S. Senate",
                                "United States",
                                "Congress",
                                "President",
                                "Governor",
                            )
                        ],
                    },
                    "specialElectionsOnly": False,
                    "voterStats": False,
                    "stages": [],
                },
                separators=(",", ":"),
            )
        }
    )


def picks(fips: dict[str, dict[str, str]]) -> list[tuple[str, str, str]]:
    chosen: list[tuple[str, str, str]] = []
    for state, commit in OPENELECTIONS.items():
        tree = json.loads(
            raw.cached(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)).path.read_text()
        )
        found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for entry in tree["tree"]:
            name = entry["path"].rsplit("/", 1)[-1]
            if not (
                entry["type"] == "blob"
                and entry["path"].startswith("2024/")
                and re.fullmatch(rf"2024\d{{4}}__{state.lower()}__[\w-]*primary[\w-]*\.csv", name)
            ):
                continue
            middle = re.search(r"primary__([a-z_]+)__precinct\.csv$", name)
            grain = (
                "county"
                if re.search(r"__(county|county-level|town)\.csv$", name)
                else "part"
                if "/counties/" in entry["path"] or (middle and re.sub(r"[^a-z]", "", middle[1]) in fips[state])
                else "precinct"
            )
            found[name[:8]][grain].append(entry["path"])
        for _, files in sorted(found.items()):
            precincts = files["precinct"] or (
                files["part"] if len(files["part"]) == len(fips[state]) or state in PARTIAL else []
            )
            chosen.extend((state, "county", path) for path in files["county"])
            chosen.extend((state, "precinct", path) for path in precincts)
    return chosen


def fetch() -> None:
    raw.fetch(SOURCE, CALIFORNIA, suffix=".xlsx")
    raw.fetch(SOURCE, NORTH_CAROLINA, suffix=".zip")
    for county in range(1, 25):
        for party in ("Democratic", "Republican"):
            raw.fetch(SOURCE, MARYLAND.format(county, party), suffix=".csv")
    for name in ("ushousepct", "ussenatepct", "PrctTbl"):
        raw.fetch(SOURCE, MINNESOTA.format(name), suffix=".txt")
    for day in ("20240312", "20240806"):
        raw.fetch(SOURCE, WASHINGTON.format(day), suffix=".csv")
    for category in IN_CATEGORIES:
        raw.fetch(SOURCE, INDIANA.format(category), suffix=".json")
    for url in ALABAMA:
        raw.fetch(SOURCE, url, suffix=".zip")
    for path in ME_FILES:
        raw.fetch(SOURCE, MAINE.format(path), suffix=".xlsx")
    raw.fetch(SOURCE, IOWA, suffix=".zip")
    for name in NJ_FILES:
        raw.fetch(SOURCE, NEW_JERSEY.format(name), suffix=".pdf")
    raw.fetch(SOURCE, NEBRASKA, suffix=".pdf")
    for url in OHIO:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for day in ("20240319", "20240820"):
        raw.fetch(SOURCE, FLORIDA.format(day), suffix=".txt")
    raw.fetch(SOURCE, OREGON, suffix=".pdf")
    raw.fetch(SOURCE, MISSOURI, suffix=".pdf")
    for package in TEXAS:
        for resource in json.loads(raw.fetch(SOURCE, TED.format(package), suffix=".json").path.read_text())["result"][
            "resources"
        ]:
            if resource["name"].startswith(("U.S.", "President")):
                raw.fetch(SOURCE, resource["url"], suffix=".csv")
    raw.fetch(SOURCE, KANSAS, suffix=".xlsx")
    for suffix in ("", "-by-County"):
        raw.fetch(SOURCE, KS_PRESIDENT.format(suffix), suffix=".pdf")
    for listing in MASSACHUSETTS:
        for election, *_ in elstats.MA_ROW.findall(raw.fetch(SOURCE, listing, suffix=".html").path.read_text()):
            raw.fetch(SOURCE, f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:1/", suffix=".csv")
    for state in ELECTIONSTATS:
        raw.fetch(SOURCE, search(state), suffix=".csv")
    for state, commit in OPENELECTIONS.items():
        raw.fetch(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit), suffix=".json")
    with connect() as conn:
        fips = openelections.counties(conn)
    for state, _, path in picks(fips):
        raw.fetch(
            SOURCE,
            openelections.REPOSITORY.format(state=state.lower(), commit=OPENELECTIONS[state], path=path),
            suffix=".csv",
        )


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
        towns = {
            (state, re.sub(r"[^a-z]", "", town.lower())): county
            for state, county, town in conn.execute(
                """select distinct split_part(code, ':', 2), split_part(code, ':', 3), split_part(code, ':', 4)
                   from reporting_unit where scheme_code = 'us.precinct' and code ~ '^2024:(CT|RI):[0-9]{5}:'"""
            ).fetchall()
        }
    rows: list[tuple[str, date, str, int | None, str | None, str, str, str | None, str, bool, int, str]] = []
    sheet = (
        fastexcel.read_excel(raw.cached(SOURCE, CALIFORNIA).path)
        .load_sheet(0, header_row=None, dtypes="string")
        .to_polars()
    )
    lines = list(sheet.iter_rows())
    district = 0
    names: list[tuple[str, str, bool]] = []
    counted: list[int] = []
    for index, (label, *cells) in enumerate(lines):
        if label is not None and (found := re.fullmatch(r"(\d+)\w\w Congressional District", label.strip())):
            district = int(found[1])
        elif label is None and any(cells) and index + 1 < len(lines) and lines[index + 1][0] is None:
            names = [
                (" ".join(name.split()), party.removesuffix(" (W/I)"), party.endswith("(W/I)"))
                for name, party in zip(cells, lines[index + 1][1:], strict=True)
                if name is not None and party is not None
            ]
            counted = [0] * len(names)
        elif label == "District Totals":
            printed = [int(cell) for cell in cells[: len(names)]]
            if printed != counted:
                raise ValueError(f"CA {district}: counties sum to {counted}, the sheet prints {printed}")
        elif label is not None and label.strip() != "Percent" and names:
            county = fips["CA"][re.sub(r"[^a-z]", "", label.lower())]
            for position, ((name, _, write_in), cell) in enumerate(zip(names, cells, strict=False)):
                counted[position] += int(cell or 0)
                rows.append(
                    (
                        "CA",
                        date(2024, 3, 5),
                        "house",
                        district,
                        None,
                        "county",
                        county,
                        None,
                        name,
                        write_in,
                        int(cell or 0),
                        CALIFORNIA,
                    )
                )
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in([*ELECTIONSTATS, "MA"]))
    places: dict[str, dict[str, str]] = defaultdict(dict)
    for state, geoid, name in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        places[state].setdefault(elstats.plain(name), geoid[:5])
        places[state].setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    for (state, name), code in elstats.FORMER.items():
        places[state].setdefault(name, code)
    texas = {
        resource["url"]: resource["name"]
        for package in TEXAS
        for resource in json.loads(raw.cached(SOURCE, TED.format(package)).path.read_text())["result"]["resources"]
        if resource["name"].startswith(("U.S.", "President"))
    }
    frames = [
        pl.DataFrame(
            rows,
            schema={
                "state": pl.String,
                "day": pl.Date,
                "office": pl.String,
                "district": pl.Int64,
                "party": pl.String,
                "grain": pl.String,
                "county": pl.String,
                "precinct": pl.String,
                "candidate": pl.String,
                "write_in": pl.Boolean,
                "votes": pl.Int64,
                "url": pl.String,
            },
            orient="row",
        )
    ]
    for state, grain, url in [
        ("NC", "precinct", NORTH_CAROLINA),
        ("MN", "precinct", MINNESOTA.format("ushousepct")),
        ("MN", "precinct", MINNESOTA.format("ussenatepct")),
        ("WA", "county", WASHINGTON.format("20240312")),
        ("WA", "county", WASHINGTON.format("20240806")),
        *(("IN", "county", INDIANA.format(category)) for category in IN_CATEGORIES),
        *(("AL", "precinct", url) for url in ALABAMA),
        *(("ME", "precinct", MAINE.format(path)) for path in ME_FILES),
        ("IA", "county", IOWA),
        *(("NJ", "county", NEW_JERSEY.format(name)) for name in NJ_FILES),
        ("NE", "county", NEBRASKA),
        *(("TX", "precinct", url) for url in texas),
        *(("OH", "precinct", url) for url in OHIO),
        *(("FL", "county", FLORIDA.format(day)) for day in ("20240319", "20240820")),
        ("OR", "county", OREGON),
        ("MO", "county", MISSOURI),
        ("KS", "precinct", KANSAS),
        ("KS", "county", KS_PRESIDENT.format("-by-County")),
        *(
            ("MA", "precinct", f"{elstats.MASSACHUSETTS}download/{election}/precincts_include:1/")
            for election in dict.fromkeys(
                election
                for listing in MASSACHUSETTS
                for election, _, _, _ in elstats.MA_ROW.findall(raw.cached(SOURCE, listing).path.read_text())
            )
        ),
        *(
            ("MD", "precinct", MARYLAND.format(county, party))
            for county in range(1, 25)
            for party in ("Democratic", "Republican")
        ),
        *(
            (state, grain, openelections.REPOSITORY.format(state=state.lower(), commit=OPENELECTIONS[state], path=path))
            for state, grain, path in picks(fips)
        ),
    ]:
        if state == "NC":
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                text = archive.read(archive.namelist()[0])
            contest = pl.col("Contest Name").str.strip_chars()
            frame = pl.read_csv(
                io.BytesIO(text), separator="\t", infer_schema=False, truncate_ragged_lines=True
            ).select(
                county="County",
                precinct="Precinct",
                office=contest.str.replace(r"^NC ", "").str.replace(r"\s*\(\w+\)$", ""),
                district=contest.str.extract(r"DISTRICT (\d+)"),
                party=contest.str.extract(r"\((\w+)\)$"),
                candidate="Choice",
                votes="Total Votes",
            )
        elif state == "WA":
            race = pl.col("Race").str.strip_chars()
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False).unique(
                    subset=["County", "Race", "Candidate"], keep="first", maintain_order=True
                )
            ).select(
                county="County",
                office=race.str.replace(r"^Washington State ", "").str.replace(r" - \w+ Party$", ""),
                district=race.str.extract(r"(?i)congressional district (\d+)"),
                party=race.str.extract(r" - (\w+) Party$"),
                candidate="Candidate",
                votes="Votes",
            )
        elif state == "AL":
            sheets: list[pl.DataFrame] = []
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                for name in archive.namelist():
                    sheet = fastexcel.read_excel(archive.read(name)).load_sheet(0, header_row=None).to_polars()
                    header = [str(cell).strip() for cell in sheet.row(0)]
                    columns = [
                        f"{cell} ({header[:index].count(cell) + 1})" if cell in header[:index] else cell
                        for index, cell in enumerate(header)
                    ]
                    sheets.append(
                        sheet.slice(1)
                        .rename(dict(zip(sheet.columns, columns, strict=True)))
                        .unpivot(columns[3:], index=columns[:3], variable_name="precinct", value_name="votes")
                        .select(
                            county=pl.lit(re.sub(r"^2024-Primary-(Runoff-)?|\.xls$", "", name)),
                            precinct="precinct",
                            office=pl.col("Contest Title").str.strip_chars(),
                            district=pl.col("Contest Title").str.strip_chars(),
                            party="Party",
                            candidate="Candidate",
                            votes="votes",
                        )
                    )
            frame = pl.concat(sheets)
        elif url == KS_PRESIDENT.format("-by-County"):
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells = []
            place = ""
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    if re.sub(r"[^a-z]", "", text.lower()) in fips["KS"]:
                        place = text
                    elif found := re.fullmatch(r"([DR])-(.+?)(?: ([\d,]+))? [\d.]+%", text):
                        box = (
                            words[-2][1]
                            if found[3]
                            else (words[0][1][0], words[0][1][1], words[-1][1][2], words[-1][1][3])
                        )
                        cells.append(
                            {
                                "county": place,
                                "office": "President",
                                "party": found[1],
                                "candidate": found[2],
                                "votes": found[3] or "0",
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                        )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
            summed = frame.group_by("party", "candidate").agg(
                pl.col("votes").str.replace_all(",", "").cast(pl.Int64).sum()
            )
            printed = {
                (party, name): int(votes.replace(",", ""))
                for party, name, votes in re.findall(
                    r"(?m)^\s*([DR])-(.+?)\s+([\d,]+)\s+[\d.]+%$",
                    subprocess.run(
                        ["pdftotext", "-layout", str(raw.cached(SOURCE, KS_PRESIDENT.format("")).path), "-"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout,
                )
            }
            if {(party, name): votes for party, name, votes in summed.iter_rows()} != printed:
                raise ValueError(f"KS president: the counties sum to {summed.rows()}, the state totals are {printed}")
        elif state == "KS":
            workbook = fastexcel.read_excel(raw.cached(SOURCE, url).path)
            sheets = [
                workbook.load_sheet(workbook.sheet_names[0], dtypes="string")
                .to_polars()
                .select(
                    county="County",
                    precinct="Precinct",
                    office="Race",
                    district="Race",
                    party="Party",
                    candidate=pl.col("Candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1"),
                    votes="Votes",
                )
            ]
            for name in workbook.sheet_names[1:]:
                table = workbook.load_sheet(name, header_row=None, dtypes="string").to_polars()
                columns = table.columns[2:]
                label = pl.col(table.columns[1])
                total = table.filter(label == "COUNTY TOTALS")
                table = (
                    table.slice(3)
                    .filter(label.is_not_null(), ~label.str.contains("TOTAL"))
                    .with_columns(pl.col(columns).str.replace(r"(?i)^redacted.*", "").replace("", None))
                )
                if table.select(pl.any_horizontal(pl.col(columns).is_null())).to_series().any():
                    table = pl.concat(
                        [
                            table,
                            pl.DataFrame(
                                {
                                    table.columns[1]: ["Redacted precincts (county total less the reported ones)"],
                                    **{
                                        column: [str(int(total[column][0]) - table[column].cast(pl.Int64).sum())]
                                        for column in columns
                                    },
                                }
                            ),
                        ],
                        how="diagonal",
                    )
                for column in columns:
                    if table[column].cast(pl.Int64).sum() != int(total[column][0]):
                        raise ValueError(f"Kansas' {name} precincts do not add up to the county total")
                heading = workbook.load_sheet(name, header_row=None, dtypes="string", n_rows=3).to_polars()
                sheets.append(
                    table.unpivot(columns, index=table.columns[1], variable_name="column", value_name="votes").select(
                        county=pl.lit(name.title()),
                        precinct=label,
                        office=pl.col("column").replace_strict({column: heading[column][0] for column in columns}),
                        district=pl.col("column").replace_strict({column: heading[column][0] for column in columns}),
                        party=pl.col("column").replace_strict({column: heading[column][1] for column in columns}),
                        candidate=pl.col("column").replace_strict({column: heading[column][2] for column in columns}),
                        votes="votes",
                    )
                )
            frame = pl.concat(sheets)
        elif state == "IA":
            with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            frame = pl.DataFrame(
                [
                    {
                        "county": place.get("name"),
                        "office": contest.get("text"),
                        "district": contest.get("text"),
                        "party": re.sub(r"^.* - (\w+)\.$", r"\1", str(contest.get("text"))),
                        "candidate": choice.get("text"),
                        "votes": place.get("votes"),
                    }
                    for contest in root.iter("Contest")
                    for choice in contest.iter("Choice")
                    for kind in choice.iter("VoteType")
                    for place in kind.iter("County")
                ],
                schema=dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
            )
        elif state == "NJ":
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells: list[dict[str, object]] = []
            seat: int | None = None
            candidate: str | None = None
            opened = party = ""
            naming = False
            tallied = 0
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines: list[list[tuple[str, scan.Box]]] = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][1], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][1] - box[1]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                heading = next(
                    (words for words in lines if {"Name", "Address", "Party", "Tally"} <= {word for word, _ in words}),
                    None,
                )
                if heading is None:
                    continue
                columns = {word: box[0] for word, box in heading}
                for words in lines[lines.index(heading) + 1 :]:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    named = next(
                        (
                            word
                            for word, box in words
                            if word in ("Democratic", "Republican") and abs(box[0] - columns["Party"]) < 40
                        ),
                        None,
                    )
                    first = re.sub(r"[^a-z]", "", " ".join(word for word, _ in words[:2]).lower())
                    place = (
                        fips["NJ"].get(first) or fips["NJ"].get(re.sub(r"[^a-z]", "", words[0][0].lower()))
                        if abs(words[0][1][0] - columns["County"]) < 40
                        else None
                    )
                    if found := re.match(r"(\w+) Congressional District:", text):
                        seat = ORDINALS.index(found[1]) + 1
                    elif named is not None:
                        heading_name = " ".join(word for word, box in words if box[2] < columns["Address"])
                        if candidate is not None and heading_name == opened:
                            continue
                        if candidate is not None:
                            raise ValueError(f"NJ {url}: {candidate} has no total")
                        candidate = opened = heading_name
                        party = named
                        naming = True
                        tallied = 0
                    elif candidate is not None and (found := re.fullmatch(r"Total ([\d,]+)", text)):
                        if int(found[1].replace(",", "")) != tallied:
                            raise ValueError(f"NJ {url}: {candidate}'s counties sum to {tallied}, not {found[1]}")
                        candidate = None
                    elif candidate is not None and place is not None and re.fullmatch(r"[\d,]+", words[-1][0]):
                        naming = False
                        tallied += int(words[-1][0].replace(",", ""))
                        cells.append(
                            {
                                "county": place,
                                "office": NJ_FILES[url.split("results-")[1].removesuffix(".pdf")],
                                "district": None if seat is None else str(seat),
                                "party": party,
                                "candidate": candidate,
                                "votes": words[-1][0],
                                "page": page,
                                "x0": words[-1][1][0],
                                "y0": words[-1][1][1],
                                "x1": words[-1][1][2],
                                "y1": words[-1][1][3],
                            }
                        )
                    elif candidate is not None and naming and words[0][1][2] < columns["Address"]:
                        candidate += " " + " ".join(word for word, box in words if box[2] < columns["Address"])
            if candidate is not None:
                raise ValueError(f"NJ {url}: {candidate} has no total")
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            ).with_columns(candidate=pl.col("candidate").str.replace_all(r"\(w\)|\*", "").str.strip_chars())
        elif state == "MO":
            pdf = raw.cached(SOURCE, url).path
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
            cells = []
            running: dict[tuple[str, str, str], int] = defaultdict(int)
            for page in range(1, int(info.split("Pages:")[1].split()[0]) + 1):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                start = next(index for index, words in enumerate(lines) if words[0][0] == "Primary") + 1
                header: list[list[tuple[str, scan.Box]]] = []
                edges: list[int] = []
                for words in lines[start:]:
                    if re.fullmatch(r"\d+", words[0][0]) or not re.fullmatch(r"\d+", words[-1][0]):
                        if edges:
                            raise ValueError(f"MO page {page}: {words[0][0]} interrupts a table")
                        header.append(words)
                        continue
                    if not edges:
                        margin = words[0][1][0]
                        edges = [box[2] for word, box in words if re.fullmatch(r"\d+", word)]
                        label: list[str] = []
                        banners: list[str | None] = [None] * len(edges)
                        heads: list[list[str]] = [[] for _ in edges]
                        for line in header:
                            chain = 0
                            if line[0][1][0] < margin + 120:
                                chain = 1
                                while chain < len(line) and line[chain][1][0] - line[chain - 1][1][2] < 60:
                                    chain += 1
                            label.extend(word for word, _ in line[:chain])
                            for word, box in line[chain:]:
                                column = next((i for i, edge in enumerate(edges) if box[2] <= edge + 30), None)
                                if column is None:
                                    raise ValueError(f"MO page {page}: {word} sits past the last column")
                                if word in ("Republican", "Democratic", "Libertarian", "Green", "Constitution"):
                                    banners[column] = word
                                else:
                                    heads[column].append(word)
                        title = " ".join(label)
                        numbered = re.search(r"District (\d+)", title)
                        kept = (
                            "U.S. Senate"
                            if title == "United States Senator"
                            else "Governor"
                            if title == "Governor"
                            else "U.S. House"
                            if title.startswith("U.S. Representative District")
                            else None
                        )
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"\d+", word)]
                    place = " ".join(word for word, _ in words if not re.fullmatch(r"\d+", word))
                    if len(numbers) != len(edges):
                        raise ValueError(f"MO page {page}: {place} has {len(numbers)} numbers for {len(edges)} columns")
                    for column, (word, box) in enumerate(numbers):
                        affiliation = banners[column]
                        if affiliation is None or kept is None:
                            continue
                        key = (title, affiliation, " ".join(heads[column]))
                        if place == "Total":
                            summed = running.pop(key, 0)
                            if summed != int(word):
                                raise ValueError(f"MO page {page}: {key} sums to {summed}, the total is {word}")
                            continue
                        running[key] += int(word)
                        cells.append(
                            {
                                "county": place,
                                "office": kept,
                                "district": numbered[1] if numbered else None,
                                "party": affiliation,
                                "candidate": key[2],
                                "votes": word,
                                "page": page,
                                "x0": box[0],
                                "y0": box[1],
                                "x1": box[2],
                                "y1": box[3],
                            }
                        )
                    if place == "Total":
                        header = []
                        edges = []
            if running:
                raise ValueError(f"MO: {sorted(running)} have no total row")
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "OR":
            pdf = raw.cached(SOURCE, url).path
            cells = []
            office_text = None
            seat = None
            party = ""
            stacked: list[list[tuple[str, scan.Box]]] = []
            labels = []
            sums: list[int] = []
            for page in range(1, 10):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    numbers = [(word, box) for word, box in words if re.fullmatch(r"\d{1,3}(,\d{3})*", word)]
                    place = " ".join(word for word, _ in words if not re.fullmatch(r"\d{1,3}(,\d{3})*", word))
                    if "Abstract of Votes" in text:
                        party, labels = "", []
                    elif text in ("US President", "US Representative"):
                        office_text, seat = ("President" if text == "US President" else "U.S. House"), None
                    elif found := re.fullmatch(r"(\d+)\w\w District", text):
                        seat = int(found[1])
                    elif text in ("Democrat", "Republican"):
                        party, stacked, labels = text, [], []
                    elif numbers and len(numbers) == len(words) - len(place.split()) and place:
                        if not labels:
                            edges = [box[2] for _, box in numbers]
                            columns: list[list[list[str]]] = [[] for _ in edges]
                            for header in stacked:
                                for column in columns:
                                    column.append([])
                                for word, box in header:
                                    if word != "County":
                                        columns[next(i for i, edge in enumerate(edges) if box[2] <= edge + 30)][
                                            -1
                                        ].append(word)
                            headers = [[" ".join(line) for line in column if line] for column in columns]
                            if not all(headers):
                                raise ValueError(f"OR page {page}: {place}'s columns have the headers {headers}")
                            labels = [
                                "Write-ins" if name == ["Misc."] else " ".join([*name[1:], name[0].lstrip("*")])
                                for name in headers
                            ]
                            sums = [0] * len(labels)
                        if len(numbers) != len(labels):
                            raise ValueError(f"OR page {page}: {place} has {len(numbers)} numbers for {labels}")
                        votes = [int(word.replace(",", "")) for word, _ in numbers]
                        if place == "Total":
                            if votes != sums:
                                raise ValueError(
                                    f"OR page {page}: the counties sum to {sums}, the abstract prints {votes}"
                                )
                            continue
                        if not any(votes):
                            continue
                        for position, (label, (word, box)) in enumerate(zip(labels, numbers, strict=True)):
                            sums[position] += votes[position]
                            cells.append(
                                {
                                    "county": place,
                                    "office": office_text,
                                    "district": None if seat is None else str(seat),
                                    "party": party,
                                    "candidate": label,
                                    "votes": word,
                                    "page": page,
                                    "x0": box[0],
                                    "y0": box[1],
                                    "x1": box[2],
                                    "y1": box[3],
                                }
                            )
                    elif party and not labels:
                        stacked.append(words)
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
        elif state == "FL":
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, separator="\t", infer_schema=False, quote_char=None)
                .filter(pl.col("RaceCode").is_in(["PRE", "USS", "USR"]))
                .select(
                    county="CountyName",
                    office="RaceName",
                    district="RaceName",
                    party="PartyName",
                    candidate=pl.concat_str(
                        "CanNameFirst", "CanNameMiddle", "CanNameLast", separator=" ", ignore_nulls=True
                    ),
                    votes="CanVotes",
                )
            )
        elif state == "OH":
            reader = fastexcel.read_excel(raw.cached(SOURCE, url).path)
            sheets = []
            for sheet_name in ("Presidential Delegates", "U.S. Congress"):
                table = reader.load_sheet(sheet_name, header_row=None, dtypes="string").to_polars()
                body = table.slice(4).filter(pl.col(table.columns[0]).is_not_null())
                heading = ""
                for index, (label, name, total) in enumerate(
                    zip(table.row(0), table.row(1), table.row(2), strict=True)
                ):
                    heading = (label or "").strip() or heading
                    if index < 8 or "(district)" in heading or "Unexpired" in heading:
                        continue
                    column = table.columns[index]
                    if body[column].cast(pl.Int64).sum() != int(total):
                        raise ValueError(
                            f"OH {heading} {name}: the precincts sum to {body[column].cast(pl.Int64).sum()}, not {total}"
                        )
                    sheets.append(
                        body.select(
                            county=table.columns[0],
                            precinct=table.columns[1],
                            office=pl.lit(heading),
                            district=pl.lit(heading),
                            party=pl.lit(re.sub(r"^.*\((\w+)\)$", r"\1", name)),
                            candidate=pl.lit(re.sub(r"\s*\(\w+\)$", "", name)),
                            votes=column,
                        )
                    )
            frame = pl.concat(sheets)
        elif state == "TX":
            frame = (
                pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False)
                .unpivot(index=["CNTYVTD", "VTDKEY"], variable_name="candidate", value_name="votes")
                .select(
                    county=pl.lit("48") + pl.col("CNTYVTD").str.slice(0, 3),
                    precinct=(
                        ((pl.col("CNTYVTD").str.slice(0, 3).cast(pl.Int64) + 1) // 2).cast(pl.String).str.zfill(3)
                        + pl.col("CNTYVTD").str.slice(3)
                    ).str.strip_chars_start("0"),
                    office=pl.lit(texas[url]),
                    district=pl.lit(texas[url]),
                    party=pl.col("candidate").str.extract(r"([DR])_24[PR]_"),
                    candidate=pl.col("candidate").str.replace(r"[DR]_24[PR]_.*$", ""),
                    votes="votes",
                )
            )
        elif state == "NE":
            pdf = raw.cached(SOURCE, url).path
            cells = []
            printed: dict[tuple[str | None, str | None, str, str], int] = {}
            office_text: str | None = None
            seat: int | None = None
            parties: list[tuple[int, str]] = []
            above: list[list[tuple[str, scan.Box]]] = []
            sections: list[tuple[int, int, str, list[str]]] = []
            for page in range(13, 24):
                lines = []
                for word, box in sorted(
                    ((html.unescape(word), box) for word, box in scan.layer(pdf, page)),
                    key=lambda item: (item[1][3], item[1][0]),
                ):
                    if lines and abs(lines[-1][0][1][3] - box[3]) < 12:
                        lines[-1].append((word, box))
                    else:
                        lines.append([(word, box)])
                for words in lines:
                    words.sort(key=lambda item: item[1][0])
                    text = " ".join(word for word, _ in words)
                    if text.startswith("Primary Election"):
                        break
                    heading = (
                        ("President", None)
                        if "President of the United States" in text
                        else ("U.S. Senate", None)
                        if "Six Year Term" in text
                        else (None, None)
                        if "Two Year Term" in text and "Senate" in text
                        else ("U.S. House", int(found[1]))
                        if (found := re.search(r"Congressional District (\d+)", text))
                        else False
                    )
                    if heading:
                        office_text, seat = heading
                    if found := re.findall(
                        r"(Republican|Democratic|Libertarian|Legal Marijuana NOW) Party Nomination", text
                    ):
                        parties = [
                            (box[0], party) for party in found for word, box in words if word == party.split()[0]
                        ]
                        above, sections = [], []
                        continue
                    if heading:
                        above, sections = [], []
                        continue
                    if words[0][0] == "County":
                        starts = [box[0] for word, box in words if word == "County"] + [10**6]
                        sections = []
                        for start, end in itertools.pairwise(starts):
                            party = min(parties, key=lambda item: abs(item[0] - start) if len(parties) > 1 else 0)[1]
                            sections.append((start, end, party, []))
                        above.append(words)
                        continue
                    if not sections:
                        above.append(words)
                        continue
                    for start, end, party, labels in sections:
                        part = [(word, box) for word, box in words if start - 20 <= box[0] < end - 20]
                        numbers = [(word, box) for word, box in part if re.fullmatch(r"[\d,]+", word)]
                        if not numbers:
                            continue
                        if not labels:
                            edges = [box[2] for _, box in numbers]
                            stacks: list[list[str]] = [[] for _ in edges]
                            for header in above:
                                for word, box in header:
                                    if word != "County" and start - 20 <= box[0] < end - 20:
                                        stacks[next(i for i, edge in enumerate(edges) if box[2] <= edge + 20)].append(
                                            word
                                        )
                            labels.extend(" ".join(column) for column in stacks)
                        place = re.sub(
                            r"\s*\(.*\)$", "", " ".join(word for word, _ in part if not re.fullmatch(r"[\d,]+", word))
                        )
                        if len(numbers) != len(labels):
                            raise ValueError(f"NE page {page}: {place} has {len(numbers)} numbers for {labels}")
                        for name, (word, box) in zip(labels, numbers, strict=True):
                            if office_text is None:
                                continue
                            if place == "Total":
                                printed[(office_text, None if seat is None else str(seat), party, name)] = int(
                                    word.replace(",", "")
                                )
                                continue
                            cells.append(
                                {
                                    "county": fips["NE"][re.sub(r"[^a-z]", "", place.lower())],
                                    "office": office_text,
                                    "district": None if seat is None else str(seat),
                                    "party": party,
                                    "candidate": name,
                                    "votes": word,
                                    "page": page,
                                    "x0": box[0],
                                    "y0": box[1],
                                    "x1": box[2],
                                    "y1": box[3],
                                }
                            )
            frame = pl.DataFrame(
                cells,
                schema={
                    **dict.fromkeys(("county", "office", "district", "party", "candidate", "votes"), pl.String),
                    **dict.fromkeys(("page", "x0", "y0", "x1", "y1"), pl.Int64),
                },
            )
            summed = frame.group_by("office", "district", "party", "candidate").agg(
                pl.col("votes").str.replace_all(",", "").cast(pl.Int64).sum()
            )
            if {(*key, votes) for *key, votes in summed.iter_rows()} != {
                (*key, votes) for key, votes in printed.items()
            }:
                raise ValueError(
                    f"NE: the counties sum to {summed.sort('office', 'district').rows()}, the book prints {printed}"
                )
        elif state == "ME":
            office_text, district, stage = ME_FILES[url.removeprefix(MAINE[:-7]).removesuffix(".xlsx")]
            table = (
                fastexcel.read_excel(raw.cached(SOURCE, url).path)
                .load_sheet(0, header_row=None, dtypes="string")
                .to_polars()
            )
            header = [str(cell or "").strip() for cell in table.row(0)]
            where = header.index("TOWN") if "TOWN" in header else header.index("MUNICIPALITY")
            region = header.index("CTY") if "CTY" in header else header.index("COUNTY")
            candidates = [
                column
                for index, (column, cell) in enumerate(zip(table.columns, header, strict=True))
                if index > where and cell not in ("BLANK", "TBC", "TOTAL BALLOTS CAST")
            ]
            town = pl.col(table.columns[where]).str.strip_chars()
            region_code = pl.col(table.columns[region]).str.strip_chars()
            grand = table.filter(town.str.contains("TOTAL") | region_code.str.contains("TOTAL")).tail(1)
            table = table.slice(1).filter(
                (region_code.str.contains(r"^[A-Z]{3}$") & ~town.str.contains("TOTAL")) | town.str.contains("UOCAVA")
            )
            for column in candidates:
                if table[column].cast(pl.Int64).sum() != int(grand[column][0]):
                    raise ValueError(f"Maine's towns do not add up to the state total in {url}")
            abbreviations = {name[:3].upper(): code for name, code in fips["ME"].items()}
            frame = table.unpivot(
                candidates,
                index=[table.columns[region], table.columns[where]],
                variable_name="column",
                value_name="votes",
            ).select(
                county=pl.when(region_code.str.contains(r"^[A-Z]{3}$"))
                .then(region_code.replace_strict(abbreviations, default=None))
                .otherwise(pl.lit("23")),
                precinct=town,
                office=pl.lit(office_text),
                district=pl.lit(district, pl.String),
                party=pl.lit(stage),
                candidate=pl.col("column")
                .replace_strict(
                    {
                        column: "Write-ins" if cell == "OTHERS" else cell
                        for column, cell in zip(table.columns, header, strict=True)
                    }
                )
                .str.replace(r"^([^,]+), ([^,]+), (.+)$", "$2 $1 $3")
                .str.replace(r"^([^,]+), ([^,]+)$", "$2 $1"),
                votes="votes",
            )
        elif state == "IN":
            data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig"))["Root"]
            lines = [
                {
                    "county": race["Jurisdiction"].get("FIPS", region.get("MAP_FIPS")),
                    "office": race["SubSortOrder"],
                    "district": race["SubSortOrder"],
                    "party": candidate["PARTY_ABBREV"],
                    "candidate": re.sub(r" \(W/I\)$", " (write-in)", candidate["NAME_ON_BALLOT"]),
                    "votes": str(candidate["TOTAL_VOTES"]),
                    "race": race["OFFICEID"],
                }
                for region in data["OfficeCategory"]["Regions"]["Region"]
                for race in (
                    region["Races"]["Race"] if isinstance(region["Races"]["Race"], list) else [region["Races"]["Race"]]
                )
                for candidate in (
                    race["Candidates"]["Candidate"]
                    if isinstance(race["Candidates"]["Candidate"], list)
                    else [race["Candidates"]["Candidate"]]
                )
            ]
            counted: dict[tuple[str, str], int] = defaultdict(int)
            for line in lines:
                counted[line["race"], line["candidate"]] += int(line["votes"])
            summary = data["StatewideSummary"]["Race"]
            if counted != {
                (race["OFFICEID"], re.sub(r" \(W/I\)$", " (write-in)", candidate["NAME_ON_BALLOT"])): int(
                    candidate["TOTAL"]
                )
                for race in (summary if isinstance(summary, list) else [summary])
                for candidate in (
                    race["Candidates"]["Candidate"]
                    if isinstance(race["Candidates"]["Candidate"], list)
                    else [race["Candidates"]["Candidate"]]
                )
            }:
                raise ValueError(f"Indiana's counties do not add up to the statewide totals in {url}")
            frame = pl.DataFrame(lines, schema=dict.fromkeys(lines[0], pl.String)).drop("race")
        elif state == "MA":
            listed: dict[str, tuple[str, str, str]] = {
                election: (office, district, stage)
                for listing in MASSACHUSETTS
                for election, _, office, district, stage in re.findall(
                    r'id="election-id-(\d+)".*?<td[^>]*>(\d{4})</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>',
                    raw.cached(SOURCE, listing).path.read_text(),
                    re.DOTALL,
                )
            }
            office_text, district, stage = listed[url.split("/")[-3]]
            if "Primary" not in stage:
                continue
            table = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, skip_rows_after_header=1)
            table = table.rename({table.columns[0]: "City/Town"})
            frame = (
                table.filter(pl.col("City/Town").is_not_null(), pl.col("City/Town") != "TOTALS")
                .unpivot(
                    [column for column in table.columns[3:] if column not in ("Blanks", "Total Votes Cast")],
                    index=["City/Town", "Ward", "Pct"],
                    variable_name="candidate",
                    value_name="votes",
                )
                .select(
                    county=pl.col("City/Town"),
                    precinct=pl.concat_str(
                        "City/Town",
                        pl.when(pl.col("Ward") != "-").then(pl.col("Ward")),
                        "Pct",
                        separator=" ",
                        ignore_nulls=True,
                    ),
                    office=pl.lit(office_text),
                    district=pl.lit(district),
                    party=pl.lit(stage.removesuffix(" Primary")),
                    candidate=pl.when(pl.col("candidate") == "All Others")
                    .then(pl.lit("Write-ins"))
                    .otherwise("candidate"),
                    votes=pl.col("votes").str.replace_all(",", ""),
                )
            )
        elif state == "MD":
            frame = pl.read_csv(
                io.BytesIO(re.sub(rb'" *\r?\n', b'"\n', raw.cached(SOURCE, url).path.read_bytes())),
                infer_schema=False,
            )
            frame = frame.filter(
                (pl.col("Office Name") != "President - Vice Pres")
                | (pl.col("Office District").fill_null("").str.strip_chars() == "")
            ).select(
                county="County Name",
                precinct="Election District - Precinct",
                office="Office Name",
                district="Office District",
                party="Party",
                candidate="Candidate Name",
                votes=pl.sum_horizontal(
                    pl.col(column).str.strip_chars().cast(pl.Int64)
                    for column in (
                        "Early Votes",
                        "Election Night Votes",
                        "Mail-In Ballot 1 Votes",
                        "Provisional Votes",
                        "Mail-In Ballot 2 Votes",
                    )
                ).cast(pl.String),
            )
        elif state == "MN":
            minnesota = {
                "column_1": "county",
                "column_2": "precinct",
                "column_4": "office",
                "column_5": "district",
                "column_7": "candidate",
                "column_10": "party",
                "column_13": "votes",
            }
            precincts = pl.read_csv(
                raw.cached(SOURCE, MINNESOTA.format("PrctTbl")).path,
                separator=";",
                has_header=False,
                infer_schema=False,
                encoding="utf8-lossy",
            ).select(county="column_0", precinct="column_1", name="column_2")
            frame = (
                pl.read_csv(
                    raw.cached(SOURCE, url).path,
                    separator=";",
                    has_header=False,
                    infer_schema=False,
                    encoding="utf8-lossy",
                    quote_char=None,
                )
                .rename(minnesota)
                .select(*minnesota.values())
                .join(precincts, on=["county", "precinct"], how="left", validate="m:1")
                .with_columns(
                    county=pl.lit("27") + (pl.col("county").cast(pl.Int64) * 2 - 1).cast(pl.String).str.zfill(3),
                    precinct=pl.coalesce("name", "precinct"),
                )
                .drop("name")
            )
        else:
            text = raw.cached(SOURCE, url).path.read_bytes()
            frame = pl.read_csv(
                io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
                infer_schema_length=0,
                truncate_ragged_lines=True,
            ).rename(str.lower)
        if "votes" not in frame.columns:
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in frame.columns
                    if column.endswith("_votes")
                ).cast(pl.String)
            )
        if state in ("MN", "IN", "ME", "NJ", "NE", "TX"):
            pass
        elif state == "MA":
            frame = frame.with_columns(
                county=pl.col("county")
                .str.replace(r"^N\. ", "North ")
                .str.replace(r"^S\. ", "South ")
                .str.replace(r"^E\. ", "East ")
                .str.replace(r"^W\. ", "West ")
                .str.replace(r" City$", "")
                .map_elements(elstats.plain, return_dtype=pl.String)
                .replace_strict(places["MA"], default=None)
            )
        elif "county" not in frame.columns or state in ("CT", "RI"):
            frame = frame.with_columns(
                county=pl.col("town" if "town" in frame.columns else "county")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .str.replace(r"limited$", "")
                .replace_strict(
                    {
                        **{town: county for (known, town), county in towns.items() if known == state},
                        **dict.fromkeys(("federalprecinct", "federalprecincts", "congressionaldistrict"), "44"),
                    },
                    default=None,
                )
            )
        else:
            frame = frame.with_columns(
                county=pl.col("county")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all(r"\b(county|parish)\b", "")
                .str.replace_all(r"[^a-z]", "")
                .replace_strict({**fips[state], **TYPOS.get(state, {})}, default=None)
            )
        office = pl.col("office").str.to_lowercase().str.strip_chars()
        day = (
            date(2024, 5, 14)
            if state in ("MD", "NE")
            else date(2024, 3, 19)
            if state == "OH"
            else date(2024, 5, 21)
            if state == "OR"
            else date(2024, 8, 6)
            if state == "MO"
            else (date(2024, 5, 28) if re.search(r"/50[89]/", url) else date(2024, 3, 5))
            if state == "TX"
            else (date(2024, 3, 19) if url.endswith("by-County.pdf") else date(2024, 8, 6))
            if state == "KS"
            else date(2024, 6, 4)
            if state in ("IA", "NJ")
            else date(2024, 5, 7)
            if state == "IN"
            else (date(2024, 4, 16) if "Runoff" in url else date(2024, 3, 5))
            if state == "AL"
            else (date(2024, 3, 5) if frame["office"][0] == "President" else date(2024, 9, 3))
            if state == "MA"
            else datetime.strptime(re.findall(r"2024\d{4}", url)[-1], "%Y%m%d").date()
        )
        frames.append(
            frame.with_columns(
                [pl.lit(None, pl.String).alias(column) for column in COLUMNS if column not in frame.columns]
            )
            .with_columns(
                kind=pl.when(
                    office.str.contains(r"^(united states |u\.?s\.? )?president|^presidential")
                    & ~office.str.contains("council|board|commission|delegate")
                )
                .then(pl.lit("president"))
                .when(office.str.contains(openelections.SENATE.pattern))
                .then(pl.lit("senate"))
                .when(
                    office.str.contains(openelections.HOUSE.pattern) & ~office.str.contains("measure|initiat|delegate")
                )
                .then(pl.lit("house"))
                .when(office.str.contains(r"^governor\b"))
                .then(pl.lit("governor"))
            )
            .filter(
                pl.col("kind").is_not_null(),
                ~pl.col("candidate").fill_null("").str.contains(SKIPPED),
                pl.col("candidate").fill_null("").str.strip_chars() != "",
            )
            .select(
                *(
                    pl.col(column).cast(pl.Int64)
                    for column in ("page", "x0", "y0", "x1", "y1")
                    if column in frame.columns
                ),
                state=pl.lit(state),
                day=pl.lit(DAYS.get((state, day), day)),
                office="kind",
                district=pl.when(pl.col("kind") == "house").then(
                    pl.lit(0, pl.Int64)
                    if state in ("AK", "DE", "ND", "SD", "VT", "WY")
                    else pl.col("district").fill_null("").str.extract(r"(\d+)").cast(pl.Int64).fill_null(0)
                ),
                party=pl.lit(None, pl.String)
                if state == "WA" and day.month == 8
                else pl.col("party")
                .str.to_lowercase()
                .str.strip_chars()
                .replace_strict(
                    {**PARTIES, "dem": "north-dakota-democratic-nonpartisan-league"} if state == "ND" else PARTIES,
                    default=None,
                ),
                grain=pl.lit(grain),
                county="county",
                precinct=pl.when(pl.lit(grain) == "precinct").then(
                    pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " ")
                ),
                candidate=pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " "),
                write_in=pl.col("candidate").str.contains(r"(?i)write[- ]?ins?\b"),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
                url=pl.lit(url),
            )
            .filter(pl.col("votes").is_not_null())
        )
    for state in ELECTIONSTATS:
        places[state].update(TYPOS.get(state, {}))
        url = search(state)
        frame = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False, truncate_ragged_lines=True)
        office = pl.col("office_name").str.to_lowercase().str.strip_chars()
        division = pl.col("division_name").str.replace(r"\s*\(.*\)$", "").str.strip_chars()
        frames.append(
            frame.filter(
                pl.col("election_type").str.contains("(?i)primary"),
                pl.col("division_type").is_in(["County", "Locality", "City/Town"]),
                ~pl.col("candidate_name").fill_null("").str.contains(SKIPPED),
            )
            .with_columns(
                kind=pl.when(
                    office.str.contains(r"^(u\.?s\.? )?president|^presidential") & ~office.str.contains("delegate")
                )
                .then(pl.lit("president"))
                .when(office.str.contains(openelections.SENATE.pattern))
                .then(pl.lit("senate"))
                .when(office.str.contains(openelections.HOUSE.pattern))
                .then(pl.lit("house"))
                .when(office.str.contains(r"^governor\b"))
                .then(pl.lit("governor"))
            )
            .filter(pl.col("kind").is_not_null())
            .select(
                state=pl.lit(state),
                day=pl.col("election_date").str.slice(0, 10).str.to_date(),
                office="kind",
                district=pl.when(pl.col("kind") == "house").then(
                    pl.col("district_name").str.extract(r"(\d+)").cast(pl.Int64).fill_null(0)
                ),
                party=pl.col("primary_party")
                .str.to_lowercase()
                .str.strip_chars()
                .replace_strict(PARTIES, default=None),
                grain=pl.lit("county"),
                county=pl.when(pl.col("division_type") == "City/Town")
                .then(
                    pl.coalesce(
                        division.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(
                            places[state], default=None
                        ),
                        division.str.to_lowercase()
                        .str.replace_all(elstats.SUFFIXES, "")
                        .map_elements(elstats.plain, return_dtype=pl.String)
                        .replace_strict(places[state], default=None),
                    )
                )
                .otherwise(
                    division.str.to_lowercase()
                    .str.replace_all(r"saint ", "st ")
                    .str.replace_all(r"\b(county|parish)\b", "")
                    .str.replace_all(r"[^a-z]", "")
                    .replace_strict({**fips[state], **TYPOS.get(state, {})}, default=None)
                ),
                precinct=pl.lit(None, pl.String),
                candidate=pl.col("candidate_name").str.strip_chars().str.replace_all(r"\s+", " "),
                write_in=pl.col("candidate_name").str.contains(r"(?i)write[- ]?ins?\b|scatter|^all other"),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                url=pl.lit(url),
            )
            .filter(pl.col("votes").is_not_null())
        )
    frame = pl.concat(frames, how="diagonal")
    print(frame.filter(pl.col("county").is_null()).group_by("state", "url").agg(pl.col("votes").sum()))
    frame = frame.filter(pl.col("county").is_not_null())
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    print(frame.group_by("state", "grain").agg(pl.col("day").n_unique(), pl.col("votes").sum()).sort("state", "grain"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "returns.parquet").with_columns(
        key=pl.concat_str(
            pl.col("day").cast(pl.String),
            "state",
            "office",
            pl.col("district").cast(pl.String),
            "party",
            separator=":",
            ignore_nulls=True,
        )
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official and OpenElections 2024 primary returns by county or precinct, taking the default from "
            "Wikipedia's election boxes",
            polity="US",
        )
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value = any(%s)",
                [frame["key"].unique().to_list()],
            ).fetchall()
        )
        done = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        }
        counties = {
            code: (unit, area, name)
            for code, unit, area, name in conn.execute(
                """select u.code, u.id, u.area_id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short' where u.scheme_code = 'us.county'
                   union all select code, id, area_id, 'statewide' from reporting_unit where scheme_code = 'us.state'
                   union all select code, id, area_id, 'Kansas City' from reporting_unit
                   where scheme_code = 'us.place' and code = '2938000'"""
            ).fetchall()
        }
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
        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        pages: dict[tuple[str, int], tuple[UUID, int, int]] = {}
        for state, used in frame.select("state", "url").unique(maintain_order=True).iter_rows():
            publisher, title, url, kind, license = (
                (
                    "California Secretary of State",
                    "Statement of Vote, March 5, 2024, Presidential Primary Election",
                    CALIFORNIA,
                    "official_body",
                    "Public record",
                )
                if state == "CA"
                else (
                    ncsbe.PUBLISHER,
                    ncsbe.TITLE,
                    "https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
                    "official_body",
                    "Public record",
                )
                if state == "NC"
                else (
                    "Minnesota Secretary of State",
                    "Election results media files",
                    "https://electionresults.sos.mn.gov",
                    "official_body",
                    "Public record",
                )
                if state == "MN"
                else (
                    "Maryland State Board of Elections",
                    "Election data",
                    "https://elections.maryland.gov/elections/archive/2024/election_data/index.html",
                    "official_body",
                    "Public record",
                )
                if state == "MD"
                else (
                    wa_local.PUBLISHER,
                    wa_local.TITLE,
                    "https://results.vote.wa.gov/results/",
                    "official_body",
                    "Public record",
                )
                if state == "WA"
                else (
                    "Indiana Election Division",
                    "2024 Primary Election results",
                    "https://enr.indianavoters.in.gov/archive/2024Primary/",
                    "official_body",
                    "Public record",
                )
                if state == "IN"
                else (
                    "Alabama Secretary of State",
                    "Election data downloads",
                    "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
                    "official_body",
                    "Public record",
                )
                if state == "AL"
                else (
                    "Maine Department of the Secretary of State",
                    "2024 primary election tabulations",
                    "https://lldc.mainelegislature.org/Open/Elections/Returns/2024/",
                    "official_body",
                    "Public record",
                )
                if state == "ME"
                else (
                    "Iowa Secretary of State",
                    "Election night results, 2024 Primary Election",
                    "https://electionresults.iowa.gov/IA/121508/",
                    "official_body",
                    "Public record",
                )
                if state == "IA"
                else (
                    "Kansas Secretary of State",
                    "Official vote totals by county, 2024 Presidential Preference Primary Election",
                    "https://sos.ks.gov/elections/election-results.html",
                    "official_body",
                    "Public record",
                )
                if used.endswith("by-County.pdf")
                else (
                    "Texas Legislative Council",
                    "Texas Election Data, 2024 primary and runoff elections by VTD",
                    "https://data.capitol.texas.gov/",
                    "official_body",
                    "Creative Commons Attribution",
                )
                if state == "TX"
                else (
                    "Ohio Secretary of State",
                    "March 19, 2024 Presidential Primary Election official canvass by precinct",
                    "https://www.ohiosos.gov/elections/election-results-and-data/2024-official-election-results/",
                    "official_body",
                    "Public record",
                )
                if state == "OH"
                else (
                    "Florida Division of Elections",
                    "Election results file, 2024 presidential preference primary and primary election",
                    "https://results.elections.myflorida.com/",
                    "official_body",
                    "Public record",
                )
                if state == "FL"
                else (
                    "Oregon Secretary of State",
                    "May 21, 2024, Primary Election Abstract of Votes",
                    "https://records.sos.state.or.us/ORSOSCMSearch/Search/SearchDetail.aspx?uri=13735456",
                    "official_body",
                    "Public record",
                )
                if state == "OR"
                else (
                    "Missouri Secretary of State",
                    "Official results, Primary Election - August 6, 2024",
                    "https://www.sos.mo.gov/elections/results",
                    "official_body",
                    "Public record",
                )
                if state == "MO"
                else (
                    "Kansas Secretary of State",
                    "Election results, 2024 primary election",
                    "https://sos.ks.gov/elections/election-results.html",
                    "official_body",
                    "Public record",
                )
                if state == "KS"
                else (
                    "New Jersey Division of Elections",
                    "2024 official primary election results",
                    "https://www.nj.gov/state/elections/election-information-2024.shtml",
                    "official_body",
                    "Public record",
                )
                if state == "NJ"
                else (
                    "Nebraska Secretary of State",
                    "Official report of the Board of State Canvassers, 2024 primary election",
                    NEBRASKA,
                    "official_body",
                    "Public record",
                )
                if state == "NE"
                else (
                    elstats.MA_PUBLISHER,
                    elstats.TITLE,
                    "https://electionstats.state.ma.us",
                    "official_body",
                    "Public record",
                )
                if state == "MA"
                else (
                    elstats.TENANTS[state][1],
                    elstats.TITLE,
                    elstats.TENANTS[state][2],
                    "official_body",
                    "Public record",
                )
                if "civera.com" in used
                else (
                    "OpenElections",
                    f"{state} 2024 primary results",
                    f"https://github.com/openelections/openelections-data-{state.lower()}",
                    "community",
                    "No license stated: facts published with attribution, raw files not redistributed",
                )
            )
            sources[used] = cs.source(publisher, title)
            if sources[used] in cs.minted and sources[used] not in list(sources.values())[:-1]:
                cs.add(
                    "source", id=sources[used], kind=kind, publisher=publisher, title=title, url=url, license=license
                )
            artifacts[used] = cs.artifact(raw.cached(SOURCE, used), sources[used])
        spans: dict[tuple[str, int], set[str]] = defaultdict(set)
        for value, county in conn.execute(
            """select distinct e.value, case u.scheme_code when 'us.county' then u.code
                      when 'us.county_part' then split_part(u.code, ':', 2) else split_part(u.code, ':', 3) end
               from external_id e join result_set rs on rs.contest_id = e.entity_id
               join result_value v on v.result_set_k = rs.k join reporting_unit u on u.k = v.unit_k
               where e.scheme = 'us.contest' and e.value ~ %s
               and u.scheme_code in ('us.county', 'us.county_part', 'us.precinct')""",
            [rf"^2024-11-05:({'|'.join(PARTIAL)}):house:[0-9]+$"],
        ).fetchall():
            spans[(value.split(":")[1], int(value.split(":")[3]))].add(county)
        values: list[pl.DataFrame] = []
        precincts: dict[str, tuple[str, date, str]] = {}
        parts: dict[str, tuple[str, str, date, str, UUID | None]] = {}
        ballots: dict[tuple[str, date, str, int | None], dict[str | None, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        for state, day, office, district, party, candidate in (
            frame.select("state", "day", "office", "district", "party", "candidate").unique().iter_rows()
        ):
            ballots[(state, day, office, district)][party].add(slug(candidate))
        for (key, state, day, office, district), rows in frame.group_by(
            "key", "state", "day", "office", "district", maintain_order=True
        ):
            contest = contests.get(key)
            if contest is None:
                if rows["votes"].sum() > 0:
                    print(f"{key}: no contest, {rows['votes'].sum()} votes skipped")
                continue
            if key in done:
                continue
            fine = rows.filter(pl.col("grain") == "precinct")
            coarse = rows.filter(pl.col("grain") == "county")
            if fine.height and coarse.height:
                sums = [
                    part.group_by("candidate").agg(pl.col("votes").sum()).sort("candidate") for part in (fine, coarse)
                ]
                if not sums[0].equals(sums[1]):
                    print(f"{key}: precinct and county files disagree, the county file used")
                    fine = fine.clear()
            lines = fine if fine.height else coarse
            if state in PARTIAL:
                prefix = lines["county"][0][:2]
                needed: set[str] = (
                    spans[(state, district)]
                    if office == "house"
                    else {str(code) for code in counties if len(code) == 5 and code.startswith(prefix)}
                )
                missing = needed - {str(code) for code in lines["county"]}
                if not needed or missing:
                    print(f"{key}: files for {sorted(missing)} missing, skipped")
                    continue
            election, district_area = conn.execute(
                "select election_id, district_area_id from contest where id = %s", [contest]
            ).fetchone() or (None, None)
            options = conn.execute(
                """select o.id, o.kind, o.ballot_label,
                          (select n.text from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                           where cy.option_id = o.id and cy.role = 'head' limit 1),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = any(%s) and v.option_k = o.k and v.measure = 1)
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [
                    [
                        k
                        for (k,) in conn.execute(
                            "select k from result_set where contest_id = %s and is_default", [contest]
                        ).fetchall()
                    ],
                    contest,
                ],
            ).fetchall()
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            picked: dict[str, UUID | str] = {}
            dropped: list[str] = []
            for candidate, write_in, votes in (
                lines.group_by("candidate", "write_in", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                words = [
                    word
                    for word in slug(re.sub(r"\(.*?\)|\".*?\"", "", candidate)).split("-")
                    if word not in {"jr", "sr", "ii", "iii", "iv"}
                ] or ["-"]
                same = [
                    option
                    for option, kind, label, person, _ in options
                    if kind != "write_in"
                    and any(words[-1] in slug(text).split("-") for text in (label, person) if text)
                ]
                for pattern, kind in (
                    (r"(?i)uncommitted|uninstructed|no preference|noncommitted", "uncommitted"),
                    (r"(?i)^none of", "none_of_the_above"),
                ):
                    if re.search(pattern, candidate):
                        same = [option for option, known, _, _, _ in options if known == kind]
                if len(same) > 1:
                    same = [
                        option
                        for option, _, label, person, _ in options
                        if option in same
                        and any(slug(text).split("-")[0] == words[0] for text in (label, person) if text)
                    ]
                if not same and votes:
                    same = [
                        option
                        for option, kind, _, _, total in options
                        if kind != "write_in" and total == votes and option not in picked.values()
                    ]
                if len(same) == 1:
                    picked[candidate] = same[0]
                elif write_in or any(
                    slug(candidate) in names
                    for party, names in ballots[(state, day, office, district)].items()
                    if party != rows["party"][0]
                ):
                    pooled = [option for option, kind, _, _, _ in options if kind == "write_in"]
                    picked[candidate] = pooled[0] if len(pooled) == 1 else f"{key}:write-in:{slug(candidate)}"
                elif not votes:
                    dropped.append(candidate)
                else:
                    break
            else:
                candidate = None
            if candidate is not None:
                print(f"{key}: {candidate} matches no single option, skipped")
                continue
            lines = lines.filter(~pl.col("candidate").is_in(dropped))
            totals = (
                lines.with_columns(option=pl.col("candidate").replace_strict({k: str(v) for k, v in picked.items()}))
                .group_by("option")
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
            )
            kept = {str(option) for option, kind, _ in outcomes if kind in ("won", "advanced", "elected")}
            leaders = set(totals["option"].head(len(kept)))
            if leaders != kept:
                print(f"{key}: the returns lead with {leaders}, the outcomes name {kept}, skipped")
                continue
            known = {str(option): total for option, _, _, _, total in options}
            drift = [
                (option, votes, known.get(option))
                for option, votes in totals.iter_rows()
                if known.get(option) is not None
                and abs(votes - (known[option] or 0)) > 10 + (known[option] or 0) // 100
            ]
            if drift and key not in CHECKED:
                print(f"{key}: drift from the earlier default {drift}, skipped")
                continue
            for candidate, option in list(picked.items()):
                if isinstance(option, str):
                    picked[candidate] = cs.key("us.option", option)
                    if picked[candidate] in cs.minted:
                        cs.add(
                            "contest_option",
                            id=picked[candidate],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label=candidate,
                        )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[lines["url"][0]],
                is_default=True,
            )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            members: dict[str, str] = {}
            if fine.height:
                for county, precinct in lines.select("county", "precinct").unique().iter_rows():
                    code = f"2024:{state}:{county}:{counties[county][2].upper()}:{precinct}"
                    members[f"{county}\x1f{precinct}"] = str(cs.unit("us.precinct", code, on=day))
                    precincts[members[f"{county}\x1f{precinct}"]] = (code, day, county)
            for county in lines["county"].unique().to_list():
                if fine.height:
                    continue
                if office != "house" or not district:
                    members[county] = str(counties[county][0])
                    continue
                part = f"{day.year}:{county}:{district}"
                members[county] = str(cs.unit("us.county_part", part, on=day))
                parts[members[county]] = (state, part, day, county, district_area)
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=(
                        pl.concat_str("county", "precinct", separator="\x1f") if fine.height else pl.col("county")
                    ).replace_strict(members),
                    option_id=pl.col("candidate").replace_strict({k: str(v) for k, v in picked.items()}),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum())
            )
            for url, number, county, candidate, x0, y0, x1, y1 in (
                lines.filter(pl.col("page").is_not_null())
                .select("url", "page", "county", "candidate", "x0", "y0", "x1", "y1")
                .iter_rows()
            ):
                if (url, number) not in pages:
                    pdf = raw.cached(SOURCE, url)
                    image = PAGES / f"{pdf.sha256}-{number}.png"
                    if not image.exists():
                        PAGES.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                            + [str(pdf.path), str(image.with_suffix(""))],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page = cs.page(artifacts[url], number)
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifacts[url],
                        page_number=number,
                        image_key=f"pages/{SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                    pages[(url, number)] = (page, width * 3, height * 3)
                page, width, height = pages[(url, number)]
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=UUID(members[county]),
                    option_id=UUID(str(picked[candidate])),
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=page,
                    source_id=None,
                    x0=x0 / width,
                    y0=y0 / height,
                    x1=x1 / width,
                    y1=y1 / height,
                )
        for unit, (code, day, county) in precincts.items():
            if UUID(unit) in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=UUID(unit),
                    polity_id=cs.polity(f"US-{code.split(':')[1]}"),
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=counties[county][0],
                    area_relation="within",
                    area_id=counties[county][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        for unit, (state, part, day, county, district_area) in parts.items():
            if UUID(unit) in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=UUID(unit),
                    polity_id=cs.polity(f"US-{state}"),
                    scheme_code="us.county_part",
                    code=part,
                    parent_id=counties[county][0],
                    area_relation="within",
                    area_id=counties[county][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                cs.add(
                    "reporting_unit_area",
                    unit_id=UUID(unit),
                    area_id=district_area,
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
