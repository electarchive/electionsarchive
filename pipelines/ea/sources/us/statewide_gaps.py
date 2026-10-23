import difflib
import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections, sc_house_reports
from ea.store import DATA, connect

SOURCE = "us.statewide_gaps"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
KY_URL = "https://elect.ky.gov/SiteCollectionDocuments/Election%20Results/2010-2019/2015/2015%20General%20Election%20Results.pdf"
WY_URL = "https://sos.wyo.gov/Elections/Docs/2006/06Results/06General/SW_Candidates_Summary.pdf"
SD_URL = (
    "https://sdsos.gov/elections-voting/assets/Archive/2012%20Assets/2012generalelectionstatewidecandidatesbycounty.pdf"
)
KS_URLS = {
    "Treasurer": "https://sos.ks.gov/elections/22elec/2022-General-Election-State-Treasurer-Results-By-Precinct.xlsx",
    "Commissioner of Insurance": (
        "https://sos.ks.gov/elections/22elec/2022-General-Election-Commissioner-of-Insurance-Results-By-Precinct.xlsx"
    ),
}
KS_COUNTIES = {
    "Johnson": "https://www.jocoelection.org/sites/default/files/2022-11/GN2022%20Official%20Final%20Results.pdf",
    "Sedgwick": "https://www.sedgwickcounty.org/media/62931/after-canvass-official-no-wi.pdf",
    "Shawnee": "https://cdn.snco.gov/elections/document/2022G_Final_Official_Results.pdf",
    "Wyandotte": "https://wycovoteskck.gov/s/GN2022_Official_Final.pdf",
}
KS_TOTALS = "https://sos.ks.gov/elections/22elec/2022-General-Official-Vote-Totals.pdf"
SC_URL = sc_house_reports.CLARITY[date(2010, 11, 2)]
AL_URL = "https://www.sos.alabama.gov/sites/default/files/voter-pdfs/2014/2014GeneralResults-WithoutWriteIn.pdf"
CITATIONS = {
    "AL": (
        "Alabama Secretary of State",
        "Statewide Offices - General Election Results, November 4, 2014, certified by the State Canvassing Board",
        AL_URL,
    ),
    "KY": (
        "Kentucky Secretary of State",
        "Official 2015 General Election Results",
        "https://elect.ky.gov/results/2010-2019/Pages/2015primaryandgeneralelectionresults.aspx",
    ),
    "WY": (
        "Wyoming Secretary of State",
        "Statewide Candidates Official Summary, Wyoming General Election, November 7, 2006",
        WY_URL,
    ),
    "SD": (
        "South Dakota Secretary of State",
        "2012 General Election official results, statewide candidates by county",
        SD_URL,
    ),
    "KS": (
        "Kansas Secretary of State",
        "2022 General Election results by precinct, with Johnson, Sedgwick, Shawnee, and Wyandotte counties' results",
        "https://sos.ks.gov/elections/election-results.html",
    ),
    "SC": (
        sc_house_reports.PUBLISHER,
        sc_house_reports.CLARITY_TITLE.format(year=2010),
        "https://www.enr-scvotes.org/SC/19077/40477/en/summary.html",
    ),
}
KY_DAY = date(2015, 11, 3)
KY_OFFICES = {
    "Secretary of State": (
        "Secretary of State",
        [("Alison Lundergan Grimes", "DEMOCRATIC"), ("Stephen L. Knipper", "REPUBLICAN")],
    ),
    "Attorney General": (
        "Attorney General",
        [("Andy Beshear", "DEMOCRATIC"), ("Whitney H. Westerfield", "REPUBLICAN")],
    ),
    "Auditor of Public Accounts": ("Auditor", [("Mike Harmon", "REPUBLICAN"), ("Adam H. Edelen", "DEMOCRATIC")]),
    "State Treasurer": ("Treasurer", [("Allison Ball", "REPUBLICAN"), ("Rick Nelson", "DEMOCRATIC")]),
    "Commissioner of Agriculture": (
        "Commissioner of Agriculture",
        [("Ryan F. Quarles", "REPUBLICAN"), ("Jean-Marie Lawson Spann", "DEMOCRATIC")],
    ),
}
WY_DAY = date(2006, 11, 7)
WY_OFFICES = {
    "Dennis Brossman": "Secretary of State",
    "Max Maxfield": "Secretary of State",
    "Bill Eikenberry": "Auditor",
    "Rita Meyer": "Auditor",
    "Ron Redo": "Treasurer",
    "Joe Meyer": "Treasurer",
    "Michelle L. Hoffman": "Superintendent of Public Instruction",
    "Jim McBride": "Superintendent of Public Instruction",
}
LETTERS = {"D": "DEMOCRATIC", "R": "REPUBLICAN", "L": "LIBERTARIAN"}
SD_DAY = date(2012, 11, 6)
SD_PARTIES = {
    "Kristie Fiegen": "REPUBLICAN",
    "Matt McGovern": "DEMOCRATIC",
    "Russell Clarke": "LIBERTARIAN",
    "Nick Nemec": "DEMOCRATIC",
    "Chris Nelson": "REPUBLICAN",
}
SD_SPECIAL = "Kristie Fiegen"
KS_DAY = date(2022, 11, 8)
SC_DAY = date(2010, 11, 2)
SC_OFFICES = {
    "Lieutenant Governor": "Lieutenant Governor",
    "Secretary of State": "Secretary of State",
    "State Treasurer": "Treasurer",
    "Attorney General": "Attorney General",
    "Comptroller General": "Comptroller General",
    "State Superintendent of Education": "Superintendent of Education",
    "Commissioner of Agriculture": "Commissioner of Agriculture",
}
SC_PARTIES = {
    "REP": "REPUBLICAN",
    "DEM": "DEMOCRATIC",
    "GRN": "GREEN",
    "LIB": "LIBERTARIAN",
    "CON": "CONSTITUTION",
    "UNC": "UNITED CITIZENS",
    "WFM": "WORKING FAMILIES",
    "IND": "INDEPENDENCE",
    "PET": "INDEPENDENT",
}
AL_DAY = date(2014, 11, 4)
AL_GROUPS: dict[tuple[int, int], list[tuple[str, str | None, str, str | None] | None]] = {
    (2, 3): [
        None,
        None,
        None,
        ("Lieutenant Governor", None, "James C. Fields Jr.", "DEMOCRATIC"),
        ("Lieutenant Governor", None, "Kay Ivey", "REPUBLICAN"),
        ("Lieutenant Governor", None, "Write-In", None),
        None,
        None,
        ("Attorney General", None, "Joe Hubbard", "DEMOCRATIC"),
        ("Attorney General", None, "Luther Strange", "REPUBLICAN"),
        ("Attorney General", None, "Write-In", None),
    ],
    (6, 7): [
        None,
        None,
        ("Secretary of State", None, "Lula Albert-Kaigler", "DEMOCRATIC"),
        ("Secretary of State", None, "John Merrill", "REPUBLICAN"),
        ("Secretary of State", None, "Write-In", None),
        None,
        None,
    ],
    (8, 9): [
        ("Auditor", None, "Miranda Karrine Joseph", "DEMOCRATIC"),
        ("Auditor", None, "Jim Zeigler", "REPUBLICAN"),
        ("Auditor", None, "Write-In", None),
        ("Commissioner of Agriculture and Industries", None, 'Doug "New Blue" Smith', "DEMOCRATIC"),
        ("Commissioner of Agriculture and Industries", None, "John McMillan", "REPUBLICAN"),
        ("Commissioner of Agriculture and Industries", None, "Write-In", None),
    ],
    (10, 11): [
        ("Public Service Commissioner", "1", "Jeremy H. Oden", "REPUBLICAN"),
        ("Public Service Commissioner", "1", "Write-In", None),
        ("Public Service Commissioner", "2", 'Chris "Chip" Beeker Jr.', "REPUBLICAN"),
        ("Public Service Commissioner", "2", "Write-In", None),
    ],
}
COLUMNS = ["year", "day", "state", "title", "special", "county", "candidate", "party", "writein", "votes"]
METHOD = (
    "County totals are the state's official county returns as published by its election office: the canvass tables "
    "for Alabama, Kentucky, Wyoming, and South Dakota, the sums of the Secretary of State's precinct returns for Kansas, with Johnson, Sedgwick, Shawnee, and Wyandotte counties, which those files omit, from the counties' own official results and write-ins left out, and the county detail of "
    "South Carolina's official results. The winner shown is the candidate with the most votes in those totals."
)


def fetch() -> None:
    raw.fetch(SOURCE, AL_URL, suffix=".pdf")
    raw.fetch(SOURCE, KY_URL, suffix=".pdf")
    raw.fetch(SOURCE, WY_URL, suffix=".pdf")
    raw.fetch(SOURCE, SD_URL, suffix=".pdf")
    for url in KS_URLS.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for url in KS_COUNTIES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, KS_TOTALS, suffix=".pdf")


def text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, check=True, text=True
    ).stdout


def number(cell: str) -> int:
    return int(cell.replace(",", ""))


def kentucky(fips: dict[str, str]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    office = None
    lines = text(KY_URL).splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "For the office of":
            heading = next(line.strip() for line in lines[index + 1 :] if line.strip())
            office = KY_OFFICES.get(heading)
            printed: list[int] = []
            continue
        if office is None:
            continue
        title, candidates = office
        if match := re.fullmatch(r"\s+Total Votes\s+([\d,]+)\s+([\d,]+)\s*", line):
            printed = [number(match[1]), number(match[2])]
            for position, (candidate, _) in enumerate(candidates):
                summed = sum(row[-1] for row in rows if row[3] == title and row[6] == candidate)
                if summed != printed[position]:
                    raise ValueError(f"KY {title} {candidate}: counties sum to {summed}, printed {printed[position]}")
            office = None
            continue
        if re.fullmatch(r"\s+[A-Z][A-Z' .-]+\s+[A-Z][A-Z' .-]+\s*", line):
            surnames = line.split()
            for candidate, _ in candidates:
                if candidate.split()[-1].upper() not in surnames:
                    raise ValueError(f"KY {title}: header {line.strip()!r} lacks {candidate}")
            continue
        if match := re.fullmatch(r"\s+([A-Z][A-Za-z .]+?)\s{2,}([\d,]+)\s+([\d,]+)\s*", line):
            county = fips[re.sub(r"[^a-z]", "", match[1].lower())]
            for (candidate, party), votes in zip(candidates, match.groups()[1:], strict=True):
                rows.append((KY_DAY.year, KY_DAY, "KY", title, False, county, candidate, party, False, number(votes)))
    for title, _ in KY_OFFICES.values():
        if len({row[5] for row in rows if row[3] == title}) != 120:
            raise ValueError(f"KY {title}: not every county")
    return rows


def wyoming(fips: dict[str, str]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for page in text(WY_URL).split("\f"):
        candidates: list[tuple[str, str]] = []
        for line in page.splitlines():
            if " - " in line and not candidates:
                candidates = [(name, LETTERS[letter]) for name, letter in re.findall(r"(\S.*?) - ([DRL])\b", line)]
                continue
            match = re.fullmatch(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?)((?:\s+\d+)+)\s*", line)
            if not candidates or not match:
                continue
            for (candidate, party), votes in zip(candidates, match[2].split(), strict=True):
                if candidate not in WY_OFFICES:
                    continue
                if match[1] == "Total":
                    summed = sum(row[-1] for row in rows if row[6] == candidate)
                    if summed != int(votes):
                        raise ValueError(f"WY {candidate}: counties sum to {summed}, printed {votes}")
                    continue
                county = fips[re.sub(r"[^a-z]", "", match[1].lower())]
                rows.append(
                    (
                        WY_DAY.year,
                        WY_DAY,
                        "WY",
                        WY_OFFICES[candidate],
                        False,
                        county,
                        candidate,
                        party,
                        False,
                        int(votes),
                    )
                )
    if {row[6] for row in rows} != set(WY_OFFICES) or len(rows) != 23 * len(WY_OFFICES):
        raise ValueError(f"WY: {len(rows)} rows for {len({row[6] for row in rows})} candidates")
    return rows


def south_dakota(fips: dict[str, str]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for section in text(SD_URL).split("\f"):
        if not section.lstrip().startswith("Public Utilities Commissioner"):
            continue
        lines = section.splitlines()
        header = next(line for line in lines if line.startswith("County"))
        candidates = [name for name in re.split(r"\s{2,}", header.strip())[1:-1]]
        special = SD_SPECIAL in candidates
        for line in lines:
            cells = re.split(r"\s{2,}", line.strip())
            if len(cells) != len(candidates) + 2 or not cells[1].isdigit():
                continue
            counts = [int(cell) for cell in cells[1:]]
            if sum(counts[:-1]) != counts[-1]:
                raise ValueError(f"SD {cells[0]}: candidates sum to {sum(counts[:-1])}, printed {counts[-1]}")
            if cells[0].lower() in ("total", "totals"):
                for candidate, votes in zip(candidates, counts, strict=False):
                    summed = sum(row[-1] for row in rows if row[6] == candidate)
                    if summed != votes:
                        raise ValueError(f"SD {candidate}: counties sum to {summed}, printed {votes}")
                continue
            county = fips[re.sub(r"[^a-z]", "", cells[0].lower())]
            for candidate, votes in zip(candidates, counts, strict=False):
                rows.append(
                    (
                        SD_DAY.year,
                        SD_DAY,
                        "SD",
                        "Public Utilities Commissioner",
                        special,
                        county,
                        candidate,
                        SD_PARTIES[candidate],
                        False,
                        votes,
                    )
                )
    if len(rows) != 66 * 5:
        raise ValueError(f"SD: {len(rows)} rows")
    return rows


def kansas(fips: dict[str, str]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    totals = re.sub(r"\s+", " ", text(KS_TOTALS))
    for title, url in KS_URLS.items():
        sheet = pl.read_excel(raw.cached(SOURCE, url).path, engine="calamine", infer_schema_length=0)
        sums = (
            sheet.with_columns(pl.col("Votes").cast(pl.Int64))
            .group_by("County", "Candidate", "Party")
            .agg(pl.col("Votes").sum())
        )
        names = {
            str(candidate).split(",")[0]: (candidate, party)
            for candidate, party in sums.select("Candidate", "Party").unique().iter_rows()
        }
        extra = []
        for county, county_url in KS_COUNTIES.items():
            lines = text(county_url).splitlines()
            start = next(
                index
                for index, line in enumerate(lines)
                if re.fullmatch(rf"\s*(For )?{'State Treasurer' if title == 'Treasurer' else title}\s*", line)
            )
            found = 0
            for line in lines[start + 1 :]:
                if re.match(r"\s*(Total Votes Cast|Write-In Totals)", line):
                    break
                if match := re.fullmatch(r"\s*(?:(?:DEM|REP|LIB) )?(\S.*?)\s{2,}([\d,]+)(?:\s+[\d.]+%)?\s*", line):
                    candidate, party = names[match[1].split()[-1]]
                    extra.append((county, candidate, party, number(match[2])))
                    found += 1
            if found != len(names):
                raise ValueError(f"KS {title} {county}: {found} candidates")
        sums = pl.concat([sums, pl.DataFrame(extra, schema=sums.schema, orient="row")])
        for county, candidate, party, votes in sums.iter_rows():
            name = re.sub(r"^([^,]+), (.+)$", r"\2 \1", candidate)
            rows.append(
                (
                    KS_DAY.year,
                    KS_DAY,
                    "KS",
                    title,
                    False,
                    fips[re.sub(r"[^a-z]", "", county.lower())],
                    name,
                    party.upper(),
                    False,
                    votes,
                )
            )
        for (candidate,), frame in sums.group_by("Candidate"):
            total = int(frame["Votes"].sum())
            surname = str(candidate).split(",")[0]
            if not re.search(rf"{re.escape(surname)}\b.{{0,40}}?\b{total:,}\b", totals, re.IGNORECASE):
                raise ValueError(f"KS {title} {candidate}: precincts sum to {total:,}, not in the official totals")
        if len({row[5] for row in rows if row[3] == title}) != 105:
            raise ValueError(f"KS {title}: not every county")
    return rows


def south_carolina(fips: dict[str, str]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    with zipfile.ZipFile(raw.cached(sc_house_reports.SOURCE, SC_URL).path) as archive:
        root = ElementTree.fromstring(archive.read(archive.namelist()[0]))
    for contest in root.iter("Contest"):
        title = SC_OFFICES.get(contest.get("text", ""))
        if title is None:
            continue
        for choice in contest.iter("Choice"):
            candidate = choice.get("text", "")
            writein = candidate.lower().startswith("write-in")
            party = None if writein else SC_PARTIES[choice.get("party", "")]
            counties: dict[str, int] = {}
            for kind in choice.iter("VoteType"):
                for county in kind.iter("County"):
                    code = fips[re.sub(r"[^a-z]", "", county.get("name", "").lower())]
                    counties[code] = counties.get(code, 0) + int(county.get("votes", "0"))
            if sum(counties.values()) != int(choice.get("totalVotes", "0")) or len(counties) != 46:
                raise ValueError(f"SC {title} {candidate}: {len(counties)} counties sum to {sum(counties.values())}")
            rows.extend(
                (SC_DAY.year, SC_DAY, "SC", title, False, code, candidate, party, writein, votes)
                for code, votes in counties.items()
            )
    if len({row[3] for row in rows}) != len(SC_OFFICES):
        raise ValueError("SC: missing offices")
    return rows


def alabama(fips: dict[str, str]) -> list[tuple[object, ...]]:
    pdf = str(raw.cached(SOURCE, AL_URL).path)
    names = {re.sub(r"[^a-z]", "", name): code for name, code in fips.items()}
    rows: list[tuple[object, ...]] = []
    for pages, columns in AL_GROUPS.items():
        table: dict[str, list[int]] = {}
        printed: list[int] = []
        for page in pages:
            box = subprocess.run(
                ["pdftotext", "-bbox", "-f", str(page), "-l", str(page), pdf, "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
            words = [
                (float(left), float(top), float(right), float(bottom), word)
                for left, top, right, bottom, word in re.findall(
                    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', box
                )
            ]
            labels = []
            for word in words:
                if word[0] < 110 and word[1] > 100 and re.fullmatch(r"[A-Za-z.]+", word[4]):
                    plain = re.sub(r"[^a-z]", "", word[4].lower())
                    hit = difflib.get_close_matches(plain, names, n=1, cutoff=0.7)
                    if plain == "total" or hit:
                        labels.append(("total" if plain == "total" else names[hit[0]], (word[1] + word[3]) / 2))
            cells = sorted(
                (
                    word
                    for word in words
                    if word[0] > 110 and re.fullmatch(r"[-\d,.OolI]+", word[4]) and re.search(r"\d", word[4])
                ),
                key=lambda word: (round((word[1] + word[3]) / 2), word[0]),
            )
            merged: list[tuple[float, float, float, float, str]] = []
            for word in cells:
                last = merged[-1] if merged else None
                if (
                    last
                    and last[4].endswith(",")
                    and abs((last[1] + last[3]) / 2 - (word[1] + word[3]) / 2) < 4
                    and word[0] - last[2] < 12
                ):
                    merged[-1] = (last[0], last[1], word[2], word[3], last[4] + word[4])
                else:
                    merged.append(word)
            bands: list[list[float]] = []
            for right in sorted(word[2] for word in merged):
                if bands and right - bands[-1][-1] < 18:
                    bands[-1].append(right)
                else:
                    bands.append([right])
            centers = [sum(band) / len(band) for band in bands if len(band) >= 5]
            if len(centers) != len(columns):
                raise ValueError(f"AL page {page}: {len(centers)} columns for {len(columns)}")
            for label, middle in labels:
                values: list[int | None] = [None] * len(centers)
                for word in merged:
                    if abs((word[1] + word[3]) / 2 - middle) < 5:
                        column = min(range(len(centers)), key=lambda index: abs(centers[index] - word[2]))
                        digits = re.sub(r"\D", "", word[4].translate(str.maketrans("OolI", "0011")))
                        values[column] = int(f"{values[column] or ''}{digits}")
                if label == "total":
                    printed = [value or 0 for value in values]
                else:
                    table[label] = [value or 0 for value in values]
        if len(table) != 67:
            raise ValueError(f"AL pages {pages}: {len(table)} counties")
        for index, column in enumerate(columns):
            summed = sum(values[index] for values in table.values())
            if summed != printed[index]:
                raise ValueError(f"AL pages {pages} column {index}: counties sum to {summed}, printed {printed[index]}")
            if column is None:
                continue
            title, post, candidate, party = column
            rows.extend(
                (AL_DAY.year, AL_DAY, "AL", title, False, county, candidate, party, party is None, values[index], post)
                for county, values in table.items()
            )
    return rows


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    rows = (
        kentucky(fips["KY"])
        + wyoming(fips["WY"])
        + south_dakota(fips["SD"])
        + kansas(fips["KS"])
        + south_carolina(fips["SC"])
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    returns = (
        pl.concat(
            [
                pl.DataFrame(rows, schema=COLUMNS, orient="row"),
                pl.DataFrame(alabama(fips["AL"]), schema=[*COLUMNS, "post"], orient="row", infer_schema_length=None),
            ],
            how="diagonal_relaxed",
        )
        .with_columns(
            pl.col("year").cast(pl.Int32),
            pl.col("votes").cast(pl.Int64),
            candidate=pl.col("candidate").str.strip_chars().str.replace_all(r"\s+", " ").str.to_uppercase(),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code=pl.lit("county"),
            measure=pl.lit(1, pl.Int16),
        )
        .group_by(
            *contest,
            "magnitude",
            "kind",
            "code",
            "county",
            "candidate",
            "party",
            "writein",
            "measure",
            maintain_order=True,
        )
        .agg(pl.col("votes").sum())
    )
    counts = {state: len(set(codes.values())) for state, codes in fips.items()}
    races = returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.col("state").first().replace_strict(counts),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(PARSED / "returns.parquet")
    races.write_parquet(PARSED / "races.parquet")
    print(races.select(*contest[:4], "special", "counties", pl.col("present").list.len()))
    print(
        returns.group_by("state", "title", "special", "candidate", "party")
        .agg(pl.col("votes").sum())
        .sort("state", "title", "votes")
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        sources = {}
        for state, (publisher, title, url) in CITATIONS.items():
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
        cs.artifact(raw.cached(SOURCE, AL_URL), sources["AL"])
        cs.artifact(raw.cached(SOURCE, KY_URL), sources["KY"])
        cs.artifact(raw.cached(SOURCE, WY_URL), sources["WY"])
        cs.artifact(raw.cached(SOURCE, SD_URL), sources["SD"])
        for url in [*KS_URLS.values(), *KS_COUNTIES.values(), KS_TOTALS]:
            cs.artifact(raw.cached(SOURCE, url), sources["KS"])
        cs.artifact(raw.cached(sc_house_reports.SOURCE, SC_URL), sources["SC"])
        return sources

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Alabama's 2014 lieutenant governor, attorney general, secretary of state, auditor, agriculture "
        "commissioner, and both public service commission places, Kentucky's 2015 secretary of state, attorney general, auditor, treasurer, and agriculture "
        "commissioner, Wyoming's 2006 secretary of state, auditor, treasurer, and superintendent, South Dakota's two "
        "2012 public utilities commissioner races, Kansas's 2022 treasurer and insurance commissioner, and South "
        "Carolina's 2010 down-ballot statewide offices by county from each state's official returns",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
        declared={
            "2015-11-03:KY:attorney-general": (
                "Andy Beshear",
                "Andy Beshear led by 2,201 votes in the State Board of Elections' certified results and was elected.",
            )
        },
    )
