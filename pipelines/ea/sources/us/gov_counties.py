import csv
import difflib
import io
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import fl_sos, medsl, oe_statewide, openelections
from ea.sources.us.me_federal import COUNTIES as MAINE
from ea.store import DATA, connect

SOURCE = "us.gov_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OPENELECTIONS = {("AZ", 2014), ("MT", 2008), ("OR", 2010)}
FLORIDA = range(1982, 2011, 4)
ME_2006 = "https://web.archive.org/web/20081127115711id_/http://www.maine.gov/sos/cec/elec/2006g/Gov.txt"
ME_2014 = (
    "https://web.archive.org/web/2016id_/https://www.maine.gov/sos/sites/maine.gov.sos/files/content/assets/"
    "governorgen.xlsx"
)
RI_2006 = (
    "https://elections.ri.gov/sites/g/files/xkgbur756/files/publications/Election_Publications/Countbooks/"
    "RI_Election_Countbook_2006.pdf"
)
NM = {
    date(2006, 11, 7): ("https://nm.elstats2.civera.com/eng/files/serve/454", "GOVERNOR", "governor"),
    date(2012, 11, 6): ("https://nm.elstats2.civera.com/eng/files/serve/492", "UNITED STATES SENATOR", "senate"),
}
NM_COUNTIES = [
    "35001",
    "35003",
    "35005",
    "35006",
    "35007",
    "35009",
    "35011",
    "35013",
    "35015",
    "35017",
    "35019",
    "35021",
    "35023",
    "35025",
    "35027",
    "35028",
    "35029",
    "35031",
    "35033",
    "35035",
    "35037",
    "35039",
    "35041",
    "35043",
    "35045",
    "35047",
    "35049",
    "35051",
    "35053",
    "35055",
    "35057",
    "35059",
    "35061",
]
RI_COUNTIES = {"BRISTOL": "44001", "KENT": "44003", "NEWPORT": "44005", "PROVIDENCE": "44007", "WASHINGTON": "44009"}
AL_2014 = "https://www.sos.alabama.gov/sites/default/files/voter-pdfs/2014/2014GeneralResults-WithoutWriteIn.pdf"
SC_2010 = "https://sc.elstats.civera.com/api/download_contest/10366_table.csv?split_party=true"
IL_2006 = (
    "https://elections.il.gov/NewDocDisplay.aspx?khDtbt6dhc8zLboSZnz8zqVh5SQVox7uAOAe2nieWDAlNyd4%2btjArHsz9xVXIJ4p6"
    "Y57u3FvWw0%2bgnNWdT3uKyV44EJPSNKJqSNPoTMrt%2fmA2Vga9kq1ZXrHCzqRzWcetZpt%2bDb3HXofIhXnPPi3csOqBry45uvj3IL41jGJRx"
    "QhuRCmj50ecZcCmYz6Q9t2"
)
MN_2006 = (
    "https://web.archive.org/web/2015id_/https://electionresults.sos.mn.gov/Results/MediaFile_Archive/Index"
    "?erselectionId=55&mediafileid=100"
)
AR_2006 = "https://www.sos.arkansas.gov/uploads/elections/Voices2006-WEB_sm.pdf"
OFFICIAL = {
    "AL": (
        "Alabama Secretary of State",
        "2014 General Election results, statewide offices by county",
        "https://www.sos.alabama.gov/alabama-votes/voter/election-data",
        [AL_2014],
    ),
    "SC": (
        "South Carolina Election Commission",
        "Election history",
        "https://electionhistory.scvotes.gov/contest/10366",
        [SC_2010],
    ),
    "IL": (
        "Illinois State Board of Elections",
        "Official Vote Cast at the General Election, November 7, 2006",
        "https://www.elections.il.gov/ElectionOperations/DownloadVoteTotals.aspx",
        [IL_2006],
    ),
    "MN": (
        "Minnesota Secretary of State",
        "Election results media files",
        "https://electionresults.sos.mn.gov",
        [MN_2006],
    ),
    "AR": (
        "Arkansas Secretary of State",
        "Voices: the 2006 elections",
        "https://www.sos.arkansas.gov/elections/research/election-results",
        [AR_2006],
    ),
    "ME": (
        "Maine Secretary of State",
        "Election results: general election tabulations by municipality",
        "https://www.maine.gov/sos/cec/elec/results/index.html",
        [ME_2006, ME_2014],
    ),
    "RI": (
        "Rhode Island Board of Elections",
        "2006 General Election Count Book",
        "https://elections.ri.gov/publications/election-publications",
        [RI_2006],
    ),
    "NM": (
        "New Mexico Secretary of State",
        "Election statistics archive",
        "https://electionstats.sos.nm.gov",
        [url for url, _, _ in NM.values()],
    ),
}
COLUMNS = [
    "day",
    "chamber",
    "code",
    "candidate",
    "votes",
    "year",
    "district",
    "special",
    "state",
    "writein",
    "measure",
    "kind",
    "source",
]


def fetch() -> None:
    openelections.fetch()
    for _, _, _, urls in OFFICIAL.values():
        for url in urls:
            raw.fetch(
                SOURCE,
                url,
                suffix={SC_2010: ".csv", MN_2006: ".txt", ME_2006: ".txt", ME_2014: ".xlsx"}.get(url, ".pdf"),
            )


def parse() -> None:
    frame, _ = oe_statewide.load()
    opened = frame.filter(
        pl.col("office").str.contains(r"^(STATE )?GOVERNOR$"),
        pl.struct("state", "year").map_elements(
            lambda row: (row["state"], row["year"]) in OPENELECTIONS, return_dtype=pl.Boolean
        ),
    ).select(
        "day",
        "state",
        code="county",
        candidate=pl.when(
            pl.col("writein")
            | pl.col("candidate").is_in(["MISC."])
            | (pl.col("votes").sum().over("state", "candidate") < 2000)
        )
        .then(pl.lit("WRITE-INS"))
        .otherwise("candidate"),
        votes="votes",
        source=pl.concat_str(pl.lit("OE:"), "state"),
    )
    florida = (
        pl.read_parquet(fl_sos.PARSED / "lines.parquet")
        .filter(pl.col("race") == "GOV", pl.col("day").dt.year().is_in(list(FLORIDA)))
        .select(
            "day",
            state=pl.lit("FL"),
            code="code",
            candidate=pl.when(pl.col("party") == "WRI")
            .then(pl.lit("WRITE-INS"))
            .otherwise(pl.col("candidate").str.to_uppercase()),
            votes="votes",
            source=pl.lit("FL"),
        )
    )
    rows = [*opened.iter_rows(), *florida.iter_rows()]
    for row in list(csv.DictReader(io.StringIO(raw.cached(SOURCE, ME_2006).path.read_text("latin-1")))):
        for n in range(1, 6):
            if row[f"fullname{n}"]:
                rows.append(
                    (
                        date(2006, 11, 7),
                        "ME",
                        MAINE[row["county"]],
                        row[f"fullname{n}"].strip().upper(),
                        int(row[f"votes{n}"]),
                        "ME",
                    )
                )
    sheet = pl.read_excel(raw.cached(SOURCE, ME_2014).path, has_header=False)
    names = [str(cell).strip().upper() for cell in sheet.row(0)[1:5]]
    for row in sheet.iter_rows():
        if row[0] is not None and str(row[0]).endswith(" County Totals"):
            code = next(key for key in MAINE if str(row[0]).upper().startswith(key))
            for name, votes in zip(names, row[1:5], strict=True):
                rows.append(
                    (date(2014, 11, 4), "ME", MAINE[code], "WRITE-INS" if name == "OTHERS" else name, int(votes), "ME")
                )
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", "48", "-l", "48", raw.cached(SOURCE, RI_2006).path, "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    county = None
    for line in text.splitlines():
        if (found := re.fullmatch(r"([A-Z]+) COUNTY", line.strip())) is not None:
            county = RI_COUNTIES[found[1]]
        elif line.strip().startswith("County Total") and county is not None:
            carcieri, fogarty = (int(cell.replace(",", "")) for cell in line.split()[2:4])
            rows.append((date(2006, 11, 7), "RI", county, "DONALD L. CARCIERI", carcieri, "RI"))
            rows.append((date(2006, 11, 7), "RI", county, "CHARLES J. FOGARTY", fogarty, "RI"))
    for day, (url, office, chamber) in NM.items():
        text = subprocess.run(
            ["pdftotext", "-layout", "-f", "2", "-l", "3", raw.cached(SOURCE, url).path, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        inside = False
        for line in text.splitlines():
            if line.strip() and not line.strip()[0].isdigit() and not re.search(r"\d{2,}\s*$", line):
                if not line.strip().endswith("PARTY"):
                    inside = line.strip().startswith(office)
                continue
            found = re.fullmatch(r"\s*(.*?[A-Z.)])\s*((?:\d+\s*)+)", line)
            if inside and found is not None:
                numbers = [int(cell) for cell in found[2].split()]
                if len(numbers) != 34 or sum(numbers[:33]) != numbers[33]:
                    raise ValueError(f"{day} {found[1]}: {len(numbers)} columns that don't add up")
                name = re.sub(r"\s+and\s+.*$", "", found[1], flags=re.IGNORECASE).strip()
                for code, votes in zip(NM_COUNTIES, numbers[:33], strict=True):
                    rows.append((day, "NM", code, "WRITE-INS" if "Write-In" in name else name, votes, "NM"))
    with connect() as conn:
        named: dict[str, dict[str, str]] = {}
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and left(u.code, 2) in ('01', '05', '17', '45')"""
        ).fetchall():
            named.setdefault(code[:2], {})[re.sub(r"[^a-z]", "", text.lower().replace("saint", "st"))] = code

    def lines(url: str, first: int, last: int) -> list[str]:
        return subprocess.run(
            ["pdftotext", "-layout", "-f", str(first), "-l", str(last), raw.cached(SOURCE, url).path, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()

    def county(state: str, text: str) -> str | None:
        known = named[state]
        key = re.sub(r"[^a-z]", "", text.lower().replace("saint", "st"))
        close = difflib.get_close_matches(key, list(known), n=1, cutoff=0.75)
        return known[key] if key in known else known[close[0]] if close else None

    printed: list[int] = []
    pending = None
    for line in lines(AL_2014, 2, 3):
        line = re.sub(r"(\d), (\d{3})", r"\1,\2", line)
        numbers = [(found.end(), int(found[0].replace(",", ""))) for found in re.finditer(r"\d[\d,]*", line)]
        if line.startswith("Total"):
            printed = [votes for _, votes in numbers[:3]]
            continue
        named_row = re.match(r"\s{0,12}([A-Z][A-Za-z. ]+?)(?=\s{2,}|\s*$)", line)
        code = county("01", named_row[1]) if printed and named_row is not None else None
        if code is not None and not numbers:
            pending = code
            continue
        if code is None and pending is not None and len(numbers) >= 2 and line.lstrip()[:1].isdigit():
            code, pending = pending, None
        if code is None or len(numbers) < 2:
            continue
        writein = numbers[2][1] if len(numbers) > 2 and numbers[2][0] - numbers[1][0] <= 12 else 0
        for name, votes in zip(
            ["PARKER GRIFFITH", "ROBERT BENTLEY", "WRITE-INS"], [numbers[0][1], numbers[1][1], writein], strict=True
        ):
            rows.append((date(2014, 11, 4), "AL", code, name, votes, "AL"))
    summed = [
        sum(row[4] for row in rows if row[1] == "AL" and row[3] == name)
        for name in ["PARKER GRIFFITH", "ROBERT BENTLEY", "WRITE-INS"]
    ]
    if summed != printed:
        raise ValueError(f"Alabama's county rows add to {summed}, not the printed {printed}")
    table = list(csv.reader(io.StringIO(raw.cached(SOURCE, SC_2010).path.read_text())))
    labels = [
        "WRITE-INS" if name == "Write-In" else "BRUCE MORGAN" if party == "Green" else name.upper()
        for name, party in zip(table[0][2:7], table[1][2:7], strict=True)
    ]
    for row in table[2:]:
        if row[0] == "County":
            for name, votes in zip(labels, row[2:7], strict=True):
                rows.append((date(2010, 11, 2), "SC", county("45", row[1]), name, int(votes), "SC"))
    for line in lines(IL_2006, 5, 7):
        found = re.fullmatch(
            r"\s*([A-Z][A-Za-z. ]+?)\s+[\d,]+\((?:DEM|REP)\)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*",
            line,
        )
        if found is not None:
            code = county("17", found[1])
            for name, votes in zip(
                ["ROD R. BLAGOJEVICH", "JUDY BAAR TOPINKA", "RICH WHITNEY", "RANDY STUFFLEBEAM", "RANDY STUFFLEBEAM"],
                found.groups()[1:],
                strict=True,
            ):
                rows.append((date(2006, 11, 7), "IL", code, name, int(votes.replace(",", "")), "IL"))
    for row in csv.reader(io.StringIO(raw.cached(SOURCE, MN_2006).path.read_text("latin-1")), delimiter=";"):
        if len(row) > 13 and row[4].startswith("GOVERNOR"):
            name = re.sub(r"\s+AND\s+.*$", "", row[7]).strip()
            rows.append(
                (
                    date(2006, 11, 7),
                    "MN",
                    f"27{2 * int(row[1]) - 1:03}",
                    "WRITE-INS" if row[10] == "WI" else name,
                    int(row[13]),
                    "MN",
                )
            )
    names = ["ASA HUTCHINSON", "WRITE-INS", "ROD BRYAN", "MIKE BEEBE", "WRITE-INS", "JIM LENDALL"]
    for line in lines(AR_2006, 19, 20):
        found = re.fullmatch(r"\s*([A-Z][A-Za-z. ]+?)\s+((?:[\d,]+\s+){7})[\d.]+%\s*", line)
        if found is not None and found[1] != "Totals":
            code = county("05", found[1])
            for name, votes in zip(names, found[2].split()[:6], strict=True):
                rows.append((date(2006, 11, 7), "AR", code, name, int(votes.replace(",", "")), "AR"))
    official = pl.DataFrame(
        rows, schema=["day", "state", "code", "candidate", "votes", "source"], orient="row"
    ).with_columns(
        chamber=pl.when((pl.col("state") == "NM") & (pl.col("day").dt.year() == 2012))
        .then(pl.lit("senate"))
        .otherwise(pl.lit("governor"))
    )
    picked = (
        official.group_by("day", "chamber", "state", "code", "candidate", "source")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.lit(None, pl.Int16),
            special=pl.lit(False),
            writein=pl.col("candidate") == "WRITE-INS",
            measure=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
        )
        .select(COLUMNS)
    )
    with pl.Config(tbl_rows=80):
        print(
            picked.group_by("state", "day", "candidate")
            .agg(pl.col("votes").sum(), pl.col("code").n_unique())
            .sort("state", "votes", descending=[False, True])
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    picked.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    counties = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor generals by county, and New Mexico's 2012 U.S. Senate race, where the archive's only "
            "county breakdown came from ICPSR-derived returns: official canvasses for Alabama 2014, Arkansas 2006, "
            "Florida 1982–2010, Illinois 2006, Maine 2006 and 2014, Minnesota 2006, New Mexico 2006 and 2012, Rhode "
            "Island 2006, and South Carolina 2010, and OpenElections' transcriptions for Arizona 2014, Montana 2008, "
            "and Oregon 2010",
            polity="US",
        )
        sources = {"FL": cs.source(fl_sos.PUBLISHER, fl_sos.TITLE)}
        for state, (publisher, title, url, urls) in OFFICIAL.items():
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
            for link in urls:
                cs.artifact(raw.cached(SOURCE, link), sources[state])
        for day in fl_sos.DAYS:
            if day.year in FLORIDA:
                cs.artifact(raw.cached(fl_sos.SOURCE, fl_sos.EXTRACT.format(day=day)), sources["FL"])
        for state in sorted({state for state, _ in OPENELECTIONS}):
            title = f"{state} county-level results"
            sources[f"OE:{state}"] = cs.source("OpenElections", title)
            if sources[f"OE:{state}"] in cs.minted:
                cs.add(
                    "source",
                    id=sources[f"OE:{state}"],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
        for state, year, path in openelections.picks():
            if (state, year) in OPENELECTIONS:
                cs.artifact(
                    raw.cached(
                        openelections.SOURCE,
                        openelections.REPOSITORY.format(
                            state=state.lower(), commit=openelections.COMMITS[state], path=path
                        ),
                    ),
                    sources[f"OE:{state}"],
                )
        medsl.attach(
            cs,
            conn,
            counties.filter(pl.col("state") != "IL"),
            sources,
            strict=True,
            extend=True,
            partial="the county table leaves out ballots not counted in a county",
        )
        medsl.attach(
            cs,
            conn,
            counties.filter(pl.col("state") == "IL"),
            sources,
            extend=True,
            partial="the Board's county table leaves out the write-in candidates other than Randy Stufflebeam",
        )
        return cs.write()
