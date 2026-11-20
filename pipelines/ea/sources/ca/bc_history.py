import re
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
import pymupdf
import xlrd

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.ca import bc_volumes
from ea.sources.licenses import PUBLIC_RECORD
from ea.store import DATA, connect

SOURCE = "ca.bc_history"
KEY = f"{SOURCE}/all"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CATALOGUE = "https://catalogue.data.gov.bc.ca/dataset"
VOTES = f"{CATALOGUE}/44914a35-de9a-4830-ac48-870001ef8935"
REGISTERED = f"{CATALOGUE}/60bf8c02-1436-49fc-b350-f985618d6881"
BY_AREA = f"{VOTES}/resource/fb40239e-b718-4a79-b18f-7a62139d9792/download/provincial_voting_results_by_va.csv"
BY_PLACE = (
    f"{VOTES}/resource/4e7be8e3-0805-4f39-87f3-47c4c29e262d/download/provincial_voting_results_by_voting_place.csv"
)
COUNTS = f"{REGISTERED}/resource/c332d1dc-67aa-4a6a-883f-18a50751844d/download/provincial_registered_voter_counts.csv"
RPT = "https://elections.bc.ca/docs/rpt"
# The statements of votes before the open data begins: the 1996 and 2001 general elections, whose voting-area tables
# Elections BC also publishes as workbooks, and the four by-elections between them, read from the text layer.
STATEMENTS = {
    "1996": f"{RPT}/1996-SOVGeneralElection.pdf",
    "2001": f"{RPT}/2001GEResults/2001-SOVGeneralElection.pdf",
    "1997": f"{RPT}/swr-sov97.pdf",
    "1998": f"{RPT}/par-sov98.pdf",
    "1999": f"{RPT}/dls_sov99.pdf",
    "2004": f"{RPT}/SOV_By-election_2004_SRP.pdf",
}
TITLES = {
    "1996": "Statement of Votes, 36th Provincial General Election, May 28, 1996",
    "2001": "Statement of Votes, 37th Provincial General Election, May 16, 2001",
    "1997": "Statement of Votes, Surrey-White Rock By-Election, September 15, 1997",
    "1998": "Statement of Votes, Parksville-Qualicum By-Election, December 14, 1998",
    "1999": "Statement of Votes, Delta South By-Election, December 7, 1999",
    "2004": "Report of the Chief Electoral Officer on the Surrey-Panorama Ridge By-election, October 28, 2004",
}
SHEETS = {
    "1996": f"{RPT}/1996GEResults/1996-Voting-Results-by-Voting-Area.xls",
    "2001": f"{RPT}/2001GEResults/2001ResultsxED.xls",
}
GENERALS = {"1996": date(1996, 5, 28), "2001": date(2001, 5, 16)}
BY_ELECTIONS = ("1997", "1998", "1999", "2004")
# The statements' party abbreviations, as the open data's affiliation labels where the open data carries the same
# party from 2005 (the Progressive Conservative Party of 1996 is the Conservative Party, Reform BC is BC Reform), and
# as the statement's own party name otherwise. Independents keep the open data's label; candidates with no
# affiliation have none.
ABBREVIATIONS: dict[str, str | None] = {
    "LIB": "BC Liberal Party",
    "NDP": "BC NDP",
    "GP": "BC Green Party",
    "COMM": "Communist Party of BC",
    "LBN": "Libertarian",
    "CP": "Conservative",
    "SC": "BC Social Credit Party",
    "BCM": "BC Marijuana Party",
    "PF": "People's Front",
    "WCC": "Western Canada Concept",
    "RP": "BC Reform",
    "UPBC": "BC Unity Party",
    "BCPA": "B.C. Patriot Party",
    "BCP": "British Columbia Party",
    "PDA": "Progressive Democratic Alliance",
    "FCP": "The Family Coalition Party of British Columbia",
    "NLP": "Natural Law Party",
    "CCF": "Common Sense, Community, Family Party",
    "ANP": "All Nations Party of British Columbia",
    "BCAP": "B.C. Action Party",
    "BCCA": "British Columbia Citizens Alliance Now",
    "CENT": "The Central Party",
    "CFED": "Citizens Commonwealth Federation",
    "COBC": "Council of British Columbians",
    "FREE": "The Freedom Party of British Columbia",
    "POC": "Party of Citizens Who Have Decided To Think For Themselves And Be Their Own Politicians",
    "WR": "Western Reform",
    "IND": "Independent",
    "NA": None,
}
# The 1996 workbook's district summary spells a few names unlike the statement's table of contents.
NAMES_1996 = {
    "Bulkey Valley-Stikine": "Bulkley Valley-Stikine",
    "Okanagan-East": "Okanagan East",
    "Okanagan-West": "Okanagan West",
    "Richmond Steveston": "Richmond-Steveston",
}
# Party labels the 1996 workbook gets wrong, as the statement prints them in its Summary of Results by Candidate and
# its district tables: Laery Braaten's Saanich South column says CCP, a party the statement does not list, and Bill
# Tomlinson's North Vancouver-Seymour column says NLP, while the workbook's own district summary counts his 54 votes
# as LBN.
PARTY_FIXES = {("1996", "SAS", "Braaten"): "CCF", ("1996", "NVS", "Tomlinson"): "LBN"}
NOTES = {
    (GENERALS["1996"], "SAS"): (
        "Elections BC's workbook of the 1996 results labels Laery Braaten's party CCP; the Statement of Votes prints "
        "CCF, the Common Sense, Community, Family Party, which is the label used here."
    ),
    (GENERALS["1996"], "NVS"): (
        "Elections BC's workbook of the 1996 results labels Bill Tomlinson's party NLP; the Statement of Votes and "
        "the workbook's own district summary give him to the Libertarian Party (LBN), which is the label used here."
    ),
}
# The 2001 workbook names Penticton-Okanagan Valley PKV; the open data gives the same district PEN from 2005.
CODES_2001 = {"PKV": "PEN"}
PUBLISHER = "Elections BC"
LICENSE = "Elections BC Open Data Licence"
LICENSE_URL = "https://www.elections.bc.ca/docs/EBC-Open-Data-Licence.pdf"
ATTRIBUTION = "Contains information licensed under the Elections BC Open Data Licence."
# Each map from the first general election held on it. The 75 districts of 1996 were first contested in 1991, and the
# 79 districts of 2005 in 2001. RESOLVED dates each map's areas as they were first keyed, so their ids stay put when
# a map's start moves back.
MAPS = {"1991": date(1991, 10, 17), "2005": date(2001, 5, 16), "2009": date(2009, 5, 12), "2017": date(2017, 5, 9)}
RESOLVED = {"1991": date(1996, 5, 28), "2005": date(2005, 5, 17), "2009": date(2009, 5, 12), "2017": date(2017, 5, 9)}
CURRENT = date(2024, 10, 19)
SEATS = {"1991": 75, "2005": 79, "2009": 85, "2017": 87}
SHARED = {
    "BC Green Party": "BC Green Party",
    "BC NDP": "BC NDP",
    "Communist Party of BC": "Communist Party of BC",
    "Libertarian": "Libertarian",
    "Freedom Party of BC": "Freedom Party of BC",
    "Conservative": "Conservative Party",
    "Conservative Party": "Conservative Party",
    "Christian Heritage Party of B.C.": "Christian Heritage Party of BC",
}
RENAMED = {"BC Liberal Party": "BC United"}
# The renamed party's name as the statements of 1996 to 2004 print it, before the open data's label takes over.
STATEMENT_NAMES = {"BC Liberal Party": "British Columbia Liberal Party"}
UNITED = date(2023, 4, 12)
COLORS = {
    "BC NDP": "#f58220",
    "BC United": "#d71920",
    "BC Green Party": "#3d9b35",
    "Conservative Party": "#1a4782",
}


def map_of(day: date) -> str | None:
    if day >= CURRENT:
        return None
    return max(vintage for vintage, start in MAPS.items() if start <= day)


def fetch() -> None:
    bc_volumes.fetch()
    for url in (BY_AREA, BY_PLACE, COUNTS):
        raw.fetch(SOURCE, url, suffix=".csv")
    for url in STATEMENTS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in SHEETS.values():
        raw.fetch(SOURCE, url, suffix=".xls")


def read(url: str, encoding: str) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, url).path, encoding=encoding, infer_schema_length=0)
    return frame.rename({column: column.removeprefix("\xef\xbb\xbf").lstrip("\ufeff") for column in frame.columns})


def number(value: object) -> int:
    """A count as a workbook cell (a float) or a statement line ("1,174", or "-" and "—" for none) gives it."""
    if isinstance(value, float):
        if value != int(value):
            raise ValueError(f"{value} is not a count")
        return int(value)
    text = str(value).strip().replace(",", "")
    return 0 if text in ("-", "—", "") else int(text)


def is_number(text: str) -> bool:
    return bool(re.fullmatch(r"[\d,]+|-|—", text.strip()))


def text_lines(path: Path) -> list[list[str]]:
    """Each page's lines, rebuilt from the character boxes. The later statements set words with gaps at their
    hyphenation points, which a plain text dump reads as spaces ("Abra ham"), so a space goes only where the gap
    between two characters is wider than 15% of the font size."""
    pages: list[list[str]] = []
    for page in pymupdf.open(path):
        lines: list[str] = []
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                text, last = "", None
                for span in line["spans"]:
                    for char in span["chars"]:
                        if char["c"].isspace():
                            continue
                        if last is not None and char["bbox"][0] - last > 0.15 * span["size"]:
                            text += " "
                        text += char["c"]
                        last = char["bbox"][2]
                if text:
                    lines.append(text)
        pages.append(lines)
    return pages


def folded(text: str) -> str:
    return re.sub(r"[\s.]", "", text).casefold()


def affiliation(abbreviation: str) -> str | None:
    if abbreviation not in ABBREVIATIONS:
        raise ValueError(f"unknown party abbreviation {abbreviation!r}")
    return ABBREVIATIONS[abbreviation]


def tally(
    event: str, district: str, name: str, day: date, columns: list[tuple[str, str, str, int]], totals: list[int]
) -> tuple[dict[str, object], list[dict[str, object]], tuple[str, str]]:
    """One district's row and candidate rows from a statement's Grand Totals: the candidates (given name, family
    name, party abbreviation, votes), then total valid votes, rejected ballots, and registered voters. Checks that
    the candidates add up to the valid votes, and returns the district row, the candidate rows, and the leader."""
    valid, rejected, registered = totals
    if sum(votes for *_, votes in columns) != valid:
        raise ValueError(f"{event} {name}: the candidates do not add up to the {valid:,} valid votes")
    top = max(votes for *_, votes in columns)
    leaders = [(given, family) for given, family, _, votes in columns if votes == top]
    if len(leaders) != 1:
        raise ValueError(f"{event} {name}: no single leader")
    row: dict[str, object] = {
        "event": event,
        "district": district,
        "name": name,
        "rejected": rejected,
        "valid": valid,
        "registered": registered,
        "date": day,
        "general": "general election" in event,
    }
    candidates: list[dict[str, object]] = [
        {
            "event": event,
            "district": district,
            "candidate": f"{family}, {given}" if given else family,
            "votes": votes,
            "elected": votes == top,
            "affiliation": affiliation(party),
        }
        for given, family, party, votes in columns
    ]
    return row, candidates, leaders[0]


def initials(text: str) -> str:
    return "".join(word[0] for word in re.split(r"[\s.]+", text) if word).casefold()


def elected(declared: str, leader: tuple[str, str], where: str) -> None:
    """Checks that the candidate a statement declares elected, "Name (PARTY)", is the leader: the same family name
    and the same initials, as a header may shorten given names ("Jenny W. C." for "Jenny Wai Ching")."""
    name = re.sub(r"\s*\([A-Z]+\)$", "", declared.strip())
    given, family = leader
    if not folded(name).endswith(folded(family)) or initials(name) != initials(f"{given} {family}"):
        raise ValueError(f"{where}: {declared} is declared elected, but {given} {family} leads")


def sheets(year: str, names: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """A general election's districts and candidates from its workbook: one sheet per district with the
    candidates' names and parties in the header, then the voting areas, the Grand Totals row, and the candidate
    declared elected. 1996 puts each header in one cell with three lines; 2001 spreads it over three rows."""
    book = xlrd.open_workbook(raw.cached(SOURCE, SHEETS[year]).path)
    event, day = f"general election {year}", GENERALS[year]
    districts: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    for sheet in book.sheets():
        rows = [sheet.row_values(r) for r in range(sheet.nrows)]
        grand = [i for i, row in enumerate(rows) if any(str(c).strip() == "Grand Totals" for c in row)]
        if not grand:
            continue
        code = sheet.name.removesuffix("_2001")
        code = CODES_2001.get(code, code) if year == "2001" else code
        start = next(i for i, row in enumerate(rows) if str(row[0]).strip() == "Advance Voting")
        header = [row for row in rows[:start] if not any("Statement of Votes" in str(c) for c in row)]
        cells = ["\n".join(str(row[c]).strip() for row in header if str(row[c]).strip()) for c in range(sheet.ncols)]
        total = next(c for c, text in enumerate(cells) if text.startswith("Total"))
        if "Registered" not in cells[total + 2]:
            raise ValueError(f"{year} {code}: no registered voters column")
        # The Grand Totals row sits a column off the header in some sheets (2001 Delta North) and repeats a number in
        # its label column in others (2001 Prince George North), so its last numbers are read in order: one per
        # candidate, then valid votes, rejected ballots, and registered voters.
        values = [number(c) for c in rows[grand[0]] if isinstance(c, float)]
        heads = [[part.strip() for part in text.split("\n") if part.strip()] for text in cells[:total]]
        heads = [parts for parts in heads if parts]
        values = values[-(len(heads) + 3) :]
        if len(values) != len(heads) + 3:
            raise ValueError(f"{year} {code}: {len(heads)} candidates but {len(values)} grand totals")
        columns: list[tuple[str, str, str, int]] = []
        for parts, votes in zip(heads, values, strict=False):
            party = parts.pop() if parts[-1].isupper() and len(parts) > 1 else "NA"
            party = PARTY_FIXES.get((year, code, parts[-1]), party)
            columns.append((" ".join(parts[:-1]), parts[-1], party, votes))
        totals = values[-3:]
        if code not in names:
            raise ValueError(f"{year} {code}: no district name")
        row, found, leader = tally(event, code, names[code], day, columns, totals)
        row["source"] = year
        declared = next(
            next(str(c) for c in row_[j + 1 :] if str(c).strip())
            for row_ in rows
            for j, c in enumerate(row_)
            if str(c).strip().startswith("Candidate Elected")
        )
        elected(declared, leader, f"{year} {code}")
        districts.append(row)
        candidates.extend(found)
    if len(districts) != SEATS["1991" if year == "1996" else "2005"]:
        raise ValueError(f"{year}: {len(districts)} districts")
    return districts, candidates


def names_1996() -> dict[str, str]:
    """The 1996 district names, from the workbook's Summary of Results by Electoral District, each matched to its
    sheet by its valid votes, rejected ballots, and registered voters; also checks each district's votes by party
    against the sheet's candidates."""
    book = xlrd.open_workbook(raw.cached(SOURCE, SHEETS["1996"]).path)
    summary = book.sheet_by_name("edsov")
    head = summary.row_values(6)
    parties = {c: str(text).strip() for c, text in enumerate(head) if 3 <= c and str(text).strip() not in ("",)}
    parties = {c: p for c, p in parties.items() if c < head.index("Others") + 1}
    by_totals: dict[tuple[int, int, int], tuple[str, dict[str, int]]] = {}
    for r in range(8, summary.nrows):
        row = summary.row_values(r)
        if not str(row[1]).strip() or str(row[1]).strip() == "Totals":
            continue
        name = str(row[1]).strip()
        votes = {party: number(row[c]) for c, party in parties.items()}
        by_totals[(number(row[13]), number(row[14]), number(row[16]))] = (NAMES_1996.get(name, name), votes)
    names: dict[str, str] = {}
    for sheet in book.sheets():
        rows = [sheet.row_values(r) for r in range(sheet.nrows)]
        grand = next((row for row in rows if any(str(c).strip() == "Grand Totals" for c in row)), None)
        if grand is None:
            continue
        values = [number(c) for c in grand if isinstance(c, float)]
        key = (values[-3], values[-2], values[-1])
        if key not in by_totals:
            raise ValueError(f"1996 {sheet.name}: not in the district summary")
        names[sheet.name] = by_totals.pop(key)[0]
    if by_totals:
        raise ValueError(f"1996: districts in the summary without a sheet: {sorted(v[0] for v in by_totals.values())}")
    return names


def check_1996(candidates: pl.DataFrame, names: dict[str, str]) -> None:
    """Each district's votes by party in the workbook's district summary against the sum of its candidates."""
    book = xlrd.open_workbook(raw.cached(SOURCE, SHEETS["1996"]).path)
    summary = book.sheet_by_name("edsov")
    head = [str(text).strip() for text in summary.row_values(6)]
    codes = {name: code for code, name in names.items()}
    labels = {abbreviation: ABBREVIATIONS[abbreviation] for abbreviation in head[3 : head.index("Others")]}
    for r in range(8, summary.nrows):
        row = summary.row_values(r)
        name = NAMES_1996.get(str(row[1]).strip(), str(row[1]).strip())
        if name not in codes:
            continue
        mine = candidates.filter(pl.col("district") == codes[name])
        for abbreviation, label in labels.items():
            got = int(mine.filter(pl.col("affiliation") == label)["votes"].sum())
            if got != number(row[head.index(abbreviation)]):
                raise ValueError(
                    f"1996 {name}: {abbreviation} has {got:,} votes, the summary {row[head.index(abbreviation)]}"
                )
        others = int(
            mine.filter(~pl.col("affiliation").is_in(list(labels.values())) | pl.col("affiliation").is_null())[
                "votes"
            ].sum()
        )
        printed = [number(row[head.index(column)]) for column in [*labels, "Others"]]
        if others != printed[-1]:
            # A summary row whose own columns overshoot its valid votes is the summary's slip, not the sheet's.
            if sum(printed) == number(row[head.index("Votes")]):
                raise ValueError(f"1996 {name}: others have {others:,} votes, the summary {printed[-1]:,}")
            print(f"1996 {name}: the district summary's others ({printed[-1]:,}) do not add up to its valid votes")


def check_2001(districts: list[dict[str, object]], candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    """The 2001 workbook against the statement: every candidate's votes in the Summary of Results by Candidate,
    and each district's valid votes, rejected ballots, and registered voters in the Summary of Results by Electoral
    District. The workbook wraps some middle names into the family name row ("Nicholas Saint" over "Edmund
    Thorp"), so each candidate takes the family name the statement prints ("THORP, Nicholas Saint Edmund")."""
    pages = text_lines(raw.cached(SOURCE, STATEMENTS["2001"]).path)
    names = {str(row["name"]): str(row["district"]) for row in districts}
    printed: dict[tuple[str, int], list[tuple[str, str]]] = {}
    summary: dict[str, tuple[int, int, int]] = {}
    for lines in pages:
        if "Summary of Results by Candidate" in lines:
            district = None
            for i, line in enumerate(lines):
                if line in names:
                    district = names[line]
                    continue
                # A candidate is "FAMILY, Given", or one name alone (HELVIS), then the party, then the votes.
                found = re.fullmatch(r"\*?\s*([^,a-z]+)(?:, .+)?", line)
                rest = lines[i + 1 : i + 3]
                if district is None or found is None or len(rest) < 2 or is_number(line):
                    continue
                votes = rest[0] if is_number(rest[0]) else rest[1] if is_number(rest[1]) else None
                if votes is None or (not is_number(rest[0]) and not re.fullmatch(r"[A-Z]+", rest[0].strip())):
                    continue
                party = "NA" if is_number(rest[0]) else rest[0].strip()
                printed.setdefault((district, number(votes)), []).append((found.group(1).strip(), party))
        elif "Summary of Results by Electoral District" in lines:
            for i, line in enumerate(lines):
                if line in names:
                    cells = lines[i + 1 : i + 16]
                    summary[names[line]] = (number(cells[10]), number(cells[11]), number(cells[13]))
    fixed: list[dict[str, object]] = []
    for row in candidates:
        family, _, given = str(row["candidate"]).partition(", ")
        words = f"{given} {family}".split()
        matches = [
            ((", ".join([" ".join(words[i:]), " ".join(words[:i])]) if i else " ".join(words)), party)
            for printed_family, party in printed.pop((str(row["district"]), int(str(row["votes"]))), [])
            for i in range(len(words))
            if folded(" ".join(words[i:])) == folded(printed_family)
        ]
        if len(matches) != 1:
            raise ValueError(f"2001 {row['district']}: {given} {family} ({row['votes']:,}) is not in the statement")
        if affiliation(matches[0][1]) != row["affiliation"]:
            print(
                f"2001 {row['district']} {matches[0][0]}: {row['affiliation']} in the workbook, {matches[0][1]} printed"
            )
        fixed.append({**row, "candidate": matches[0][0], "affiliation": affiliation(matches[0][1])})
    if printed:
        raise ValueError(f"2001: candidates in the statement but not the workbook: {sorted(printed)[:5]}")
    for row in districts:
        if summary.get(str(row["district"])) != (row["valid"], row["rejected"], row["registered"]):
            raise ValueError(f"2001 {row['name']}: {summary.get(str(row['district']))} in the statement")
    return fixed


def by_election(key: str, names: dict[str, str]) -> tuple[dict[str, object], list[dict[str, object]]]:
    """A by-election from its statement's text: the date and district on the cover, the results header (given
    names, then family names, then party abbreviations, column by column), the Grand Totals, the candidate declared
    elected, and the list of candidates ("FAMILY, Given") that names each one again."""
    pages = text_lines(raw.cached(SOURCE, STATEMENTS[key]).path)
    cover = pages[0]
    day = next(
        datetime.strptime(line, "%B %d, %Y").date()
        for line in cover
        if re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", line)
    )
    name = next(
        re.sub(r"^on the ", "", line)[: -len(" By-election")] for line in cover if line.lower().endswith("by-election")
    )
    if name not in names:
        raise ValueError(f"{key}: {name} is not a district of the map in force")
    text = [line for page in pages for line in page]
    # The candidates in ballot order, which is the order of the results columns: "FAMILY, Given".
    listed = list(dict.fromkeys(line for line in text if re.fullmatch(r"[A-Z][A-Z'\- ]+, [^\d]+", line)))
    count = len(listed)
    lines = next(page for page in pages if "Grand Totals" in page)
    # The header's family names, which some statements set two to a line ("Brunie Hampson"), give each family name
    # its case and check the column order.
    start = next(i for i, line in enumerate(lines) if line.startswith("No. "))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("Valid"))
    heading, cursor = " ".join(lines[start + 1 : end]), 0
    votes_at = next(i for i in range(lines.index("Registered"), len(lines)) if lines[i] == "Votes")
    parties = lines[votes_at - count : votes_at]
    grand = lines.index("Grand Totals")
    values = [number(line) for line in lines[grand + 1 : grand + count + 4]]
    columns: list[tuple[str, str, str, int]] = []
    for line, party, votes in zip(listed, parties, values[:count], strict=True):
        family, _, given = line.partition(", ")
        at = heading.casefold().find(family.casefold(), cursor)
        if at < 0:
            raise ValueError(f"{key} {name}: {family} is not in the results header {heading!r} after {cursor}")
        cursor = at + len(family)
        columns.append((given.strip(), heading[at:cursor], party, votes))
    event = f"by-election {day}"
    row, candidates, leader = tally(event, names[name], name, day, columns, values[count:])
    row["source"] = key
    elected(text[text.index("Candidate Elected:") + 1], leader, f"{key} {name}")
    return row, candidates


def statements(open_names: dict[str, str]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The 1996 and 2001 general elections and the 1997 to 2004 by-elections, in the parsed tables' columns."""
    names = names_1996()
    districts, candidates = sheets("1996", names)
    check_1996(pl.DataFrame(candidates), names)
    later, found = sheets("2001", open_names)
    found = check_2001(later, found)
    districts += later
    candidates += found
    by_name = {
        "1991": {name: code for code, name in names.items()},
        "2005": {str(row["name"]): str(row["district"]) for row in later},
    }
    for key in BY_ELECTIONS:
        row, found = by_election(key, by_name["1991" if key < "2001" else "2005"])
        districts.append(row)
        candidates += found
    return (
        pl.DataFrame(districts).with_columns(pl.col("rejected", "valid", "registered").cast(pl.Int64)),
        pl.DataFrame(candidates).with_columns(pl.col("votes").cast(pl.Int64)),
    )


def parse() -> None:
    # The 1871 to 1990 elections, from the Electoral History volumes, are packages of their own that need no database.
    bc_volumes.parse()
    rows = pl.concat([read(BY_AREA, "windows-1252"), read(BY_PLACE, "utf8").drop("ID")], how="vertical").with_columns(
        event=pl.col("EVENT_NAME").str.to_lowercase().str.replace("provincial ", "", literal=True),
        votes=pl.col("VOTES_CONSIDERED").cast(pl.Int64),
        CANDIDATE=pl.col("CANDIDATE").str.strip_chars(),
        AFFILIATION=pl.col("AFFILIATION").str.strip_chars(),
    )
    counts = read(COUNTS, "utf8").with_columns(
        event=pl.col("EVENT_NAME").str.to_lowercase().str.replace("provincial ", "", literal=True),
        registered=pl.col("REGISTERED_VOTERS").cast(pl.Int64),
        date=pl.col("EVENT_DATE_TEXT").str.to_date("%m/%d/%Y"),
    )
    days = dict(counts.select("event", "date").unique().iter_rows())
    if missing := set(rows["event"]) - set(days):
        raise ValueError(f"no date for {sorted(missing)}")
    registered = counts.group_by("event", "ED_ABBREVIATION").agg(pl.col("registered").sum())
    if registered.join(rows.select("event", "ED_ABBREVIATION").unique(), on=["event", "ED_ABBREVIATION"]).height != (
        rows.select("event", "ED_ABBREVIATION").unique().height
    ):
        raise ValueError("a district has no registered voter count")
    districts = (
        rows.group_by("event", "ED_ABBREVIATION", "ED_NAME")
        .agg(
            rejected=pl.col("votes").filter(pl.col("VOTE_CATEGORY") == "Rejected").sum(),
            valid=pl.col("votes").filter(pl.col("VOTE_CATEGORY") == "Valid").sum(),
        )
        .join(registered, on=["event", "ED_ABBREVIATION"])
        .with_columns(
            date=pl.col("event").replace_strict(days, return_dtype=pl.Date),
            general=pl.col("event").str.contains("general election"),
        )
        .rename({"ED_ABBREVIATION": "district", "ED_NAME": "name"})
    )
    if districts.group_by("event", "district").len()["len"].max() != 1:
        raise ValueError("a district has two names in one event")
    candidates = (
        rows.filter(pl.col("VOTE_CATEGORY") == "Valid")
        .group_by("event", "ED_ABBREVIATION", "CANDIDATE", maintain_order=True)
        .agg(
            pl.col("AFFILIATION").unique().drop_nulls(),
            pl.col("votes").sum(),
            elected=pl.col("ELECTED").drop_nulls().unique(),
        )
        .rename({"ED_ABBREVIATION": "district", "CANDIDATE": "candidate"})
    )
    if (candidates["AFFILIATION"].list.len() > 1).any() or (candidates["elected"].list.len() != 1).any():
        raise ValueError("a candidate has two affiliations or no single elected flag")
    candidates = candidates.with_columns(
        affiliation=pl.col("AFFILIATION").list.first(), elected=pl.col("elected").list.first() == "Y"
    ).drop("AFFILIATION")
    districts = districts.with_columns(source=pl.lit("votes"))
    # 2001 is on the 2005 map, so its districts take the names the open data gives them in 2005.
    open_names = dict(districts.filter(pl.col("date") == RESOLVED["2005"]).select("district", "name").iter_rows())
    earlier, found = statements(open_names)
    districts = pl.concat([earlier.select(districts.columns), districts], how="vertical_relaxed")
    candidates = pl.concat([found.select(candidates.columns), candidates], how="vertical_relaxed")
    for (key, district), group in candidates.group_by("event", "district"):
        top = group.filter(pl.col("votes") == group["votes"].max())
        if group["elected"].sum() != 1 or top.height != 1 or not top["elected"][0]:
            raise ValueError(f"{key} {district}: the elected candidate is not the single leader")
    sums = candidates.group_by("event", "district").agg(pl.col("votes").sum())
    if not districts.join(sums, on=["event", "district"]).filter(pl.col("votes") != pl.col("valid")).is_empty():
        raise ValueError("candidate votes do not sum to the valid votes")
    PARSED.mkdir(parents=True, exist_ok=True)
    districts.write_parquet(PARSED / "districts.parquet")
    candidates.write_parquet(PARSED / "candidates.parquet")
    print(
        f"{districts['event'].n_unique()} events, {districts.height} contests, {candidates.height} candidates, "
        f"{candidates['affiliation'].n_unique()} affiliations"
    )
    print(build())


def listed(items: list[str]) -> str:
    return (
        items[0]
        if len(items) == 1
        else " and ".join(items)
        if len(items) == 2
        else f"{', '.join(items[:-1])}, and {items[-1]}"
    )


def person_name(text: str) -> str:
    family, _, given = text.partition(",")
    return f"{given.strip()} {family.strip()}".strip()


def emit() -> None:
    package.run("load", [*bc_volumes.packages(), KEY])


def build() -> Path:
    """Write the package ca.bc_history/all from the parsed tables. It reads the database, through Draft, so the
    rows it rebuilds keep the ids the changeset path gave them."""
    districts = pl.read_parquet(PARSED / "districts.parquet").sort("date", "district")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "British Columbia's general elections from 1996 to 2024 and its by-elections from 1997 to 2023, "
                "by electoral district, from Elections BC's statements of votes (1996 to 2004) and open data"
            ),
            polity="CA-BC",
        )

        def name(
            entity: UUID,
            text: str,
            kind: str = "official",
            preferred: bool | None = None,
            valid: tuple[date, date] | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang="en",
                kind=kind,
                is_preferred=(kind == "official") if preferred is None else preferred,
                **({"valid_from": valid[0], "valid_to": valid[1]} if valid else {}),
            )

        bc = cs.polity("CA-BC")
        province = cs.area("ca.province", "59")
        assembly = cs.key("ca.bc.body", "legislative_assembly")
        fptp = cs.key("ca.bc.electoral_system", "fptp")
        if {bc, province, assembly, fptp} & cs.minted:
            raise ValueError("run ca.elections and ca.bc first: British Columbia's assembly must exist")

        sources: dict[str, UUID] = {}
        for label, title, page, urls in (
            ("votes", "Provincial Voting Results (open data)", VOTES, (BY_AREA, BY_PLACE)),
            ("registered", "Provincial Registered Voter Counts (open data)", REGISTERED, (COUNTS,)),
        ):
            sources[label] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[label],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=page,
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
            )
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources[label])
        for label, title in TITLES.items():
            sources[label] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[label],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=STATEMENTS[label],
                license=PUBLIC_RECORD,
            )
            for url in (STATEMENTS[label], *([SHEETS[label]] if label in SHEETS else [])):
                cs.artifact(raw.cached(SOURCE, url), sources[label])

        vintages = sorted(MAPS)
        ends = dict(zip(vintages, [*[MAPS[v] for v in vintages[1:]], CURRENT], strict=True))
        for vintage in vintages:
            cs.add(
                "body_size", body_id=assembly, seats=SEATS[vintage], valid_from=MAPS[vintage], valid_to=ends[vintage]
            )
            cs.add(
                "boundary_set",
                scheme_code="ca.bc",
                vintage=vintage,
                valid_from=MAPS[vintage],
                valid_to=ends[vintage],
                as_of=MAPS[vintage],
            )
        areas: dict[str, UUID] = {}
        districts = districts.with_columns(
            area=pl.struct("date", "district").map_elements(
                lambda r: r["district"] if (v := map_of(r["date"])) is None else f"{v}:{r['district']}",
                return_dtype=pl.String,
            )
        )
        for code, labels, days in (
            districts.group_by("area", maintain_order=True).agg(pl.col("name"), pl.col("date")).iter_rows()
        ):
            vintage = map_of(days[0])
            if vintage is None:
                areas[code] = cs.area("ca.bc", code, CURRENT)
                if areas[code] in cs.minted:
                    raise ValueError(f"{code} is not on the 2023 map")
                continue
            if any(map_of(day) != vintage for day in days):
                raise ValueError(f"{code} spans two maps")
            area = areas[code] = cs.area("ca.bc", code, RESOLVED[vintage])
            cs.add("area", id=area, scheme_code="ca.bc")
            cs.add("area_code", area_id=area, code=code, valid_from=MAPS[vintage], valid_to=ends[vintage])
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, labels[-1])
            for label in dict.fromkeys(labels[:-1]):
                if label != labels[-1]:
                    name(area, label, "historical", False)

        party_ids: dict[str, UUID] = {}
        affiliations = (
            candidates.join(districts.select("event", "district", "date", "source"), on=["event", "district"])
            .filter(pl.col("affiliation").is_not_null() & (pl.col("affiliation") != "Independent"))
            .group_by("affiliation")
            .agg(
                first=pl.col("date").min(),
                last=pl.col("date").max(),
                opened=pl.col("date").filter(pl.col("source") == "votes").min(),
            )
            .sort("affiliation")
        )
        for affiliation, first, last, opened in affiliations.iter_rows():
            if affiliation in SHARED:
                key = SHARED[affiliation]
                party_ids[affiliation] = cs.key("ca.bc.party", key)
                if party_ids[affiliation] not in cs.minted:
                    continue
                cs.add("party", id=party_ids[affiliation], kind="party", polity_id=bc)
                name(party_ids[affiliation], key)
                continue
            key = RENAMED.get(affiliation, affiliation)
            party = party_ids[affiliation] = cs.key(f"{SOURCE}.party", key)
            if affiliation in RENAMED:
                name(party, affiliation, "historical", False, (opened, UNITED))
                if (earlier := STATEMENT_NAMES.get(affiliation)) is not None and first < opened:
                    name(party, earlier, "historical", False, (first, opened))
                continue
            cs.add("party", id=party, kind="party", polity_id=bc)
            name(party, affiliation)
        for key, color in COLORS.items():
            party = cs.key("ca.bc.party", key) if key in SHARED.values() else cs.key(f"{SOURCE}.party", key)
            cs.add("party_color", party_id=party, color=color)

        columns = {
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "channel": pl.Int16,
            "measure": pl.Int16,
            "count_round": pl.Int16,
            "flags": pl.Int16,
            "value": pl.Int64,
        }
        for (day, general), rows in districts.group_by("date", "general", maintain_order=True):
            kind = "general" if general else "by-election"
            labels = rows["name"].to_list()
            election = cs.key(f"{SOURCE}.election", day.isoformat())
            cs.add(
                "election",
                id=election,
                polity_id=bc,
                kind="general" if general else "by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            spoken = f"{day:%B} {day.day}, {day.year}"
            if general:
                name(election, f"{day.year} British Columbia general election")
            else:
                name(
                    election,
                    f"{listed(labels)} provincial by-election{'s' if len(labels) > 1 else ''}, {spoken}",
                )
            cs.add("slug", path=f"ca/bc/{day}-{kind}", entity_id=election)
            values: list[tuple[UUID, UUID, UUID | None, int, int]] = []
            for row in rows.iter_rows(named=True):
                district = row["district"]
                contest = cs.key(f"{SOURCE}.contest", f"{day}:{district}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=areas[row["area"]],
                    magnitude=1,
                    electoral_system_id=fptp,
                )
                name(contest, f"{day.year} {'election' if general else 'by-election'} in {row['name']}")
                cs.add("slug", path=f"ca/bc/{day}-{kind}/{slug(row['name'])}", entity_id=contest)
                unit = cs.unit("ca.bc", f"{day}:{district}", day)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=bc,
                    scheme_code="ca.bc",
                    code=f"{day}:{district}",
                    area_relation="equals",
                    area_id=areas[row["area"]],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{district}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=sources[row["source"]],
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                if (fix := NOTES.get((day, district))) is not None:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=fix)
                for measure, value in (
                    (2, row["registered"]),
                    (3, row["valid"] + row["rejected"]),
                    (4, row["valid"]),
                    (5, row["rejected"]),
                ):
                    values.append((result_set, unit, None, measure, value))
                for candidate in candidates.filter(
                    (pl.col("event") == row["event"]) & (pl.col("district") == district)
                ).iter_rows(named=True):
                    display = person_name(candidate["candidate"])
                    tag = f"{day}:{district}:{candidate['candidate']}"
                    person = cs.key(f"{SOURCE}.person", tag)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, display)
                    party = party_ids.get(candidate["affiliation"] or "")
                    option = cs.key(f"{SOURCE}.option", tag)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=display,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", tag),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=display,
                        member_of_party_id=party,
                        is_independent=party is None,
                    )
                    if candidate["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                    values.append((result_set, unit, option, 1, candidate["votes"]))
            cs.frame(
                "result_value",
                pl.DataFrame(
                    {
                        "result_set_id": [str(v[0]) for v in values],
                        "unit_id": [str(v[1]) for v in values],
                        "option_id": [None if v[2] is None else str(v[2]) for v in values],
                        "channel": [0] * len(values),
                        "measure": [v[3] for v in values],
                        "count_round": [0] * len(values),
                        "flags": [0] * len(values),
                        "value": [v[4] for v in values],
                    },
                    schema=columns,
                ),
            )
        return cs.write()
