import csv
import io
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.store import DATA

ROOT = DATA / "inbox" / "manual"
ILLINOIS = "https://www.elections.il.gov/ElectionOperations/ElectionVoteTotals.aspx"
RETRIEVED = datetime(2026, 10, 21, tzinfo=UTC)
PARTY_LETTERS = {"D": "Democratic", "R": "Republican", "G": "Green", "L": "Libertarian", "LIB": "Libertarian"}
PARTY_WORDS = {
    "democratic": "Democratic",
    "democrat": "Democratic",
    "dem": "Democratic",
    "republican": "Republican",
    "rep": "Republican",
    "libertarian": "Libertarian",
    "lib": "Libertarian",
    "green": "Green",
}
PENNSYLVANIA_NAMES = {
    date(2001, 5, 15): {"Republican": "Bill Shuster", "Democratic": "Scott Conklin", "Green": "Alanna Hartzok"},
    date(2010, 5, 18): {"Republican": "Tim Burns", "Democratic": "Mark Critz", "Libertarian": "Demo Agoris"},
}
PENNSYLVANIA_NOTE = (
    "The state's export names no candidates, only parties. The names come from the English Wikipedia's article on "
    "the election and were matched to the export by party. The 2001 party totals equal the article's; the 2010 "
    "totals are the certified ones and differ slightly from the article's."
)
SITES = {
    "OH": ("Ohio Secretary of State", "Special election official canvasses", "https://data.ohiosos.gov/portal/past-election-results"),
    "GA": ("Georgia Secretary of State", "Special election results", "https://results.sos.ga.gov/"),
    "UT": ("Utah Lieutenant Governor's Office", "Special election canvasses and results", "https://electionresults.utah.gov/"),
    "IL": ("Illinois State Board of Elections", "Election vote totals", ILLINOIS),
    "MT": ("Montana Secretary of State", "2017 special election results", "https://electionresults.mt.gov/ResultsList.aspx?eid=15"),
    "WI": ("Wisconsin Elections Commission", "Election results archive", "https://elections.wi.gov/elections/election-results/results-all"),
    "PA": ("Pennsylvania Department of State", "Election returns, special elections", "https://www.electionreturns.pa.gov/"),
}
LOADED = (
    ("MF-015", "MT", 0, date(2017, 5, 25), "Statewide Results.xlsx"),
    ("MF-017", "WI", 7, date(2020, 5, 12), "Cong 7 Special-County by County Results.xlsx"),
    ("MF-001", "OH", 8, date(2016, 6, 7), "2016-06-07-oh08-special-general/results_county.xlsx"),
    ("MF-001", "OH", 12, date(2018, 8, 7), "2018-08-07-oh12-special-general/resultsbycounty.xlsx"),
    (
        "MF-001",
        "OH",
        0,
        date(2021, 11, 2),
        "2021-11-02-oh11-oh15-special-general/2021-11-2_11and15congressionalresultsbycounty.xlsx",
    ),
    ("MF-001", "OH", 0, date(2021, 8, 3), "2021-08-03-oh11-oh15-special-primary/2021-08-03_countydem.xlsx"),
    ("MF-001", "OH", 0, date(2021, 8, 3), "2021-08-03-oh11-oh15-special-primary/2021-08-03_countyrep.xlsx"),
    ("MF-001", "OH", 6, date(2024, 6, 11), "2024-06-11-oh06-special-general/results-by-county---summary.xlsx"),
    ("MF-005", "GA", 6, date(2017, 4, 18), "2017-04-18-ga06-special"),
    ("MF-005", "GA", 6, date(2017, 6, 20), "2017-06-20-ga06-special-runoff"),
    ("MF-005", "GA", 5, date(2020, 12, 1), "2020-12-01-ga05-special-runoff"),
    ("MF-005", "GA", 14, date(2026, 3, 10), "2026-03-10-ga14-special"),
    ("MF-005", "GA", 14, date(2026, 4, 7), "2026-04-07-ga14-special-runoff"),
    ("MF-005", "GA", 13, date(2026, 7, 28), "2026-07-28-ga13-special"),
    ("MF-005", "GA", 13, date(2026, 8, 25), "2026-08-25-ga13-special-runoff"),
    ("MF-005", "GA", 10, date(2007, 6, 19), "2007-06-19-ga10-special"),
    ("MF-005", "GA", 10, date(2007, 7, 17), "2007-07-17-ga10-special-runoff"),
    ("MF-005", "GA", 9, date(2010, 5, 11), "2010-05-11-ga09-special"),
    ("MF-005", "GA", 9, date(2010, 6, 8), "2010-06-08-ga09-special-runoff"),
    ("MF-006", "UT", 3, date(2017, 11, 7), "2017-11-07-ut03-special-general"),
    ("MF-006", "UT", 2, date(2023, 11, 21), "2023-11-21-ut02-special-general"),
    ("MF-010", "PA", 9, date(2001, 5, 15), "2001-05-15-pa09-special"),
    ("MF-010", "PA", 12, date(2010, 5, 18), "2010-05-18-pa12-special"),
    ("MF-010", "PA", 18, date(2018, 3, 13), "2018-03-13-pa18-special"),
    ("MF-010", "PA", 12, date(2019, 5, 21), "2019-05-21-pa12-special"),
)
GEORGIA_FIRST_ROUNDS = {date(2007, 6, 19), date(2010, 5, 11), date(2017, 4, 18), date(2026, 3, 10), date(2026, 7, 28)}
NOTE = "A person saved this file from the state's results site in a web browser on 2026-10-21; the site blocks scripts."


STORED: list[tuple[str, str]] = []
ILLINOIS_CSV = "https://www.elections.il.gov/Downloads/ElectionOperations/ElectionResults/ByOffice/49/49-420-18TH%20CONGRESS-2015SE.csv"
ILLINOIS_PAGES = {
    24: (date(2008, 3, 8), 14),
    27: (date(2009, 4, 7), 5),
    40: (date(2013, 4, 9), 2),
    49: (date(2015, 9, 10), 18),
}
ILLINOIS_PRIMARIES = {
    23: (date(2008, 2, 5), 14),
    26: (date(2009, 3, 3), 5),
    38: (date(2013, 2, 26), 2),
    48: (date(2015, 7, 7), 18),
}
ILLINOIS_PRIMARY_CSV = "https://www.elections.il.gov/Downloads/ElectionOperations/ElectionResults/ByOffice/48/48-420-18TH%20CONGRESS-2015SP.csv"


def party(text: str | None) -> str | None:
    if text is None:
        return None
    return PARTY_WORDS.get(text.strip().lower()) or PARTY_LETTERS.get(text.strip().upper())


def person(text: str) -> str:
    tokens = []
    for token in re.sub(r"\s+", " ", text).strip().split(" "):
        if token.isupper() and len(token) > 1 and not token.startswith('"') and token not in ("II", "III", "IV"):
            token = token.title()
        token = re.sub(r"^(Mc|La|Le)([A-Z]{2,})$", lambda hit: hit[1] + hit[2].title(), token)
        tokens.append(re.sub(r"\bMc([a-z])", lambda hit: "Mc" + hit[1].upper(), token))
    return " ".join(tokens)


def sheet(path: Path, name: str) -> list[tuple[str | None, ...]]:
    return fastexcel.read_excel(path).load_sheet_by_name(name, header_row=None, dtypes="string").to_polars().rows()


def stored(mf: str, relative: str, state: str) -> Path:
    path = ROOT / mf / relative
    url = f"{SITES[state][2]}#{mf}/{relative}"
    STORED.append((state, url))
    try:
        raw.cached("us.elstats_specials", url)
    except LookupError:
        raw.store(
            "us.elstats_specials",
            url,
            path.read_bytes(),
            suffix=path.suffix.lower(),
            retrieved_at=RETRIEVED,
            media_type="application/octet-stream",
        )
    return path


def rows() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for mf, state, district, day, relative in LOADED:
        path = ROOT / mf / relative
        files = [
            stored(mf, str(file.relative_to(ROOT / mf)), state)
            for file in (sorted(path.rglob("*")) if path.is_dir() else [path])
            if file.is_file()
        ]
        lines: list[tuple[int, str, str | None, str, int]] = []
        for file in files:
            if state == "OH":
                grid = sheet(file, "Master")
                banners = [
                    (index, int(re.search(r"District (\d+)", cell)[1]))
                    for index, cell in enumerate(grid[0])
                    if cell is not None and re.search(r"District (\d+)", cell)
                ]
                header = grid[1]
                for row in grid[4:] if grid[3][0] == "Percentage" else grid[2:]:
                    if row[0] in (None, "Total", "Percentage"):
                        continue
                    row_district = int(row[1]) if header[1] == "Congressional District" else banners[0][1]
                    for index, cell in enumerate(header):
                        hit = re.match(r"^(.*?)\s*\(([A-Z]+)\)\*?$", cell or "")
                        if hit is None:
                            continue
                        own = [value for start, value in banners if start <= index][-1]
                        if own != row_district:
                            continue
                        lines.append(
                            (
                                row_district,
                                row[0],
                                None if hit[2] == "WI" else PARTY_LETTERS.get(hit[2]),
                                "Write-ins" if hit[2] == "WI" else person(hit[1]),
                                int(float(row[index])),
                            )
                        )
            elif file.suffix == ".pdf":
                text = subprocess.run(["pdftotext", "-layout", str(file), "-"], check=True, capture_output=True, text=True).stdout
                if day == date(2007, 7, 17):
                    for line in text.splitlines():
                        hit = re.match(r"^\s*([A-Z][A-Z .'-]*?)\s+\d+\s+\d+\s+([\d,]+)\s+([\d,]+)", line)
                        if hit is not None:
                            for name, cell in (("Paul Broun", hit[2]), ("Jim Whitehead", hit[3])):
                                lines.append((district, hit[1].title(), "Republican", name, int(cell.replace(",", ""))))
                    if (sum(entry[4] for entry in lines if entry[3] == "Paul Broun"), sum(entry[4] for entry in lines if entry[3] == "Jim Whitehead")) != (23529, 23135):
                        raise ValueError("2007-07-17: the county rows do not sum to the printed totals")
                    continue
                inside = False
                for line in text.splitlines():
                    if re.search(r"U\. ?S\. Representative, District (\d+)", line):
                        inside = True
                        continue
                    if inside and re.match(r"^\s*State (Senator|Representative)", line):
                        break
                    hit = re.match(
                        r"^\s*(.+?)\s+(\((R|D|Lib|I|Ind)\)|Republican|Democratic|Libertarian|Independent)\s+([\d,]+)\s+[\d.]+%\s*$",
                        line,
                    )
                    if inside and hit is not None:
                        label = hit[2].strip("()")
                        lines.append((district, "", party(label), person(hit[1]), int(hit[4].replace(",", ""))))
            elif file.suffix == ".xlsx" and state == "MT":
                grid = sheet(file, fastexcel.read_excel(file).sheet_names[0])
                start = next(index for index, row in enumerate(grid) if row[0] and "UNITED STATES REPRESENTATIVE" in row[0])
                columns = [
                    (index, *cell.split("\n")) for index, cell in enumerate(grid[start]) if cell is not None and "\n" in cell
                ]
                sums = dict.fromkeys((entry[1] for entry in columns), 0)
                for row in grid[start + 1 :]:
                    if row[1] is None:
                        continue
                    if row[1] == "TOTALS":
                        if sums != {name: int(row[index]) for index, name, _ in columns}:
                            raise ValueError(f"{day}: the county rows do not sum to the printed totals")
                        break
                    for index, name, label in columns:
                        sums[name] += int(row[index])
                        lines.append((district, row[1], party(label), person(name), int(row[index])))
            elif file.suffix == ".xlsx" and state == "WI":
                grid = sheet(file, "County by County Report")
                start = next(index for index, row in enumerate(grid) if row[0] == "County")
                labels = [cell for cell in grid[start][2:] if cell is not None]
                names = [re.sub(r" \(Write-in\)$", "", cell) for cell in grid[start + 1] if cell is not None]
                sums = [0] * len(names)
                for row in grid[start + 2 :]:
                    if row[0] is None:
                        continue
                    cells = [cell for cell in row[1:] if cell is not None]
                    if row[0].startswith("Office Totals"):
                        if sums != [int(cell) for cell in cells[1:]]:
                            raise ValueError(f"{day}: the county rows do not sum to the printed totals")
                        break
                    if int(cells[0]) != sum(int(cell) for cell in cells[1:]):
                        raise ValueError(f"{day} {row[0]}: the candidates do not sum to the county total")
                    for index, cell in enumerate(cells[1:]):
                        sums[index] += int(cell)
                        scattering = names[index] == "SCATTERING"
                        lines.append(
                            (
                                district,
                                row[0].strip(),
                                party(labels[index]) if index < 2 else None,
                                "Write-ins" if scattering else person(names[index]),
                                int(cell),
                            )
                        )
            elif file.suffix == ".xlsx" and state == "UT" and day.year == 2017:
                grid = sheet(file, "Votes")
                header = grid[1]
                for row in grid[2:]:
                    if row[0] is None or row[0].strip().lower().startswith(("total", "votes", "percentage")):
                        continue
                    for index in range(1, len(header)):
                        hit = re.match(r"^(.*?)\s*\((.*)\)$", header[index])
                        write_in = hit[2] == "Write-in"
                        lines.append(
                            (
                                district,
                                row[0],
                                None if write_in else party(hit[2]),
                                "Write-ins" if write_in else person(hit[1]),
                                int(float(row[index])),
                            )
                        )
            elif file.suffix == ".xlsx":
                names = fastexcel.read_excel(file).sheet_names
                for row in sheet(file, "County Results" if "County Results" in names else "Precinct Results")[1:]:
                    cells = [cell for cell in row]
                    if cells[3] in ("Total Votes", "Ballots Cast") or cells[0] is None:
                        continue
                    if not re.search(r"(?i)^(u\.?s\.? )?(representative|house)", cells[1]):
                        continue
                    hit = re.match(r"^(.*?)(?:\s*(?:-|\()\s*(Rep|Dem|Lib|Ind|NP)\)?)?$", cells[3].strip(), re.I)
                    lines.append(
                        (district, cells[0].strip(), party(cells[5]) or party(hit[2]), person(hit[1]), int(cells[6]))
                    )
            elif file.suffix.upper() == ".CSV":
                for record in csv.DictReader(io.StringIO(file.read_text(encoding="utf-8-sig"))):
                    name = record["Candidate Name"] or PENNSYLVANIA_NAMES[day][record["Party Name"]]
                    lines.append(
                        (district, record["County Name"].title(), party(record["Party Name"]), person(name), int(record["Votes"].replace(",", "")))
                    )
        if not lines:
            raise ValueError(f"{day}: no lines read")
        totals: dict[tuple[int, str], int] = {}
        for line_district, _, _, name, count in lines:
            totals[(line_district, name)] = totals.get((line_district, name), 0) + count
        for line_district in {entry[0] for entry in lines}:
            ranked = sorted(
                ((name, count) for (current, name), count in totals.items() if current == line_district and name != "Write-ins"),
                key=lambda item: -item[1],
            )
            everyone = sum(count for (current, _), count in totals.items() if current == line_district)
            runoff_day = state == "GA" and day not in GEORGIA_FIRST_ROUNDS
            decided = runoff_day or state != "GA" or ranked[0][1] * 2 > everyone
            leaders = {name for name, _ in ranked[: 1 if decided else 2]}
            for _, division, party_name, name, count in (entry for entry in lines if entry[0] == line_district):
                found.append(
                    {
                        "state": state,
                        "election_date": str(day),
                        "district_name": str(line_district),
                        "candidate_name": name,
                        "candidate_party_name": party_name,
                        "division_name": division or f"cd:{line_district}",
                        "votes": str(count),
                        "outcome": ("Elected" if decided else "Runoff") if name in leaders else "Defeated",
                        "runoff": runoff_day,
                        "primary": "Democratic" if relative.endswith("countydem.xlsx") else "Republican" if relative.endswith("countyrep.xlsx") else None,
                        "manual": mf,
                    }
                )
    return found


def fetch_illinois() -> None:
    client = httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=60)
    page = client.get(ILLINOIS).text
    hidden = {
        hit[0]: hit[1].replace("&amp;", "&")
        for hit in re.findall(r'name="(__[A-Z]+)" id="[^"]*" value="([^"]*)"', page)
    }
    for election in [*ILLINOIS_PAGES, *ILLINOIS_PRIMARIES]:
        url = f"{ILLINOIS}#election-{election}"
        try:
            raw.cached("us.elstats_specials", url)
        except LookupError:
            response = client.post(
                ILLINOIS,
                data={
                    **hidden,
                    "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlElections",
                    "ctl00$ContentPlaceHolder1$ddlElections": str(election),
                },
            )
            response.raise_for_status()
            raw.store(
                "us.elstats_specials",
                url,
                response.content,
                suffix=".html",
                retrieved_at=datetime.now(UTC),
                media_type="text/html",
            )
    raw.fetch("us.elstats_specials", ILLINOIS_CSV, suffix=".csv")
    raw.fetch("us.elstats_specials", ILLINOIS_PRIMARY_CSV, suffix=".csv")


def illinois_rows() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for election, (day, district) in ILLINOIS_PAGES.items():
        url = f"{ILLINOIS}#election-{election}"
        STORED.append(("IL", url))
        text = raw.cached("us.elstats_specials", url).path.read_text(errors="replace")
        start = text.index("CONGRESS")
        body = text[start : text.index("</table>", start)]
        candidates: list[tuple[str, str | None, int]] = []
        printed = 0
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]*>|&nbsp;", "", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 3:
                continue
            if cells[0] == "Vote Totals":
                printed = int(cells[2].replace(",", ""))
            else:
                candidates.append((cells[0], party(cells[1]), int(cells[2].replace(",", ""))))
        if sum(count for _, _, count in candidates) != printed:
            raise ValueError(f"{day}: the candidates do not sum to the printed total")
        names = {name: (person(name) if label is not None or count > 100 else "Write-ins", label) for name, label, count in candidates}
        winner = max(candidates, key=lambda entry: entry[2])[0]
        lines: list[tuple[str, str | None, str, int]] = []
        if election == 49:
            STORED.append(("IL", ILLINOIS_CSV))
            counts: dict[tuple[str, str], int] = {}
            for record in csv.DictReader(io.StringIO(raw.cached("us.elstats_specials", ILLINOIS_CSV).path.read_text(encoding="utf-8-sig"))):
                key = (record["CandidateName"], record["JurisName"].replace("CITY OF BLOOMINGTON", "McLEAN"))
                counts[key] = counts.get(key, 0) + int(record["VoteCount"])
            for (name, county), count in counts.items():
                if not name.lower().startswith("write-in"):
                    lines.append((person(name), names[name][1], county.title(), count))
        else:
            lines = [(names[name][0], names[name][1], f"cd:{district}", count) for name, _, count in candidates]
        for name, label, division, count in lines:
            found.append(
                {
                    "state": "IL",
                    "election_date": str(day),
                    "district_name": str(district),
                    "candidate_name": name,
                    "candidate_party_name": label,
                    "division_name": division,
                    "votes": str(count),
                    "outcome": "Elected" if name == person(winner) else "Defeated",
                    "runoff": False,
                    "manual": None,
                }
            )
    return found


def illinois_primary_rows() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for election, (day, district) in ILLINOIS_PRIMARIES.items():
        url = f"{ILLINOIS}#election-{election}"
        STORED.append(("IL", url))
        text = raw.cached("us.elstats_specials", url).path.read_text(errors="replace")
        start = text.index("CONGRESS")
        body = text[start : text.index("</table>", start)]
        candidates: list[tuple[str, str, int]] = []
        printed = 0
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]*>|&nbsp;", "", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 3:
                continue
            if cells[0] == "Vote Totals":
                printed = int(cells[2].replace(",", ""))
            else:
                candidates.append((cells[0], cells[1].title(), int(cells[2].replace(",", ""))))
        if sum(count for _, _, count in candidates) != printed:
            raise ValueError(f"{day}: the candidates do not sum to the printed total")
        lines: list[tuple[str, str, str, int]] = []
        if election == 48:
            STORED.append(("IL", ILLINOIS_PRIMARY_CSV))
            counts: dict[tuple[str, str, str], int] = {}
            for record in csv.DictReader(io.StringIO(raw.cached("us.elstats_specials", ILLINOIS_PRIMARY_CSV).path.read_text(encoding="utf-8-sig"))):
                if record["CandidateName"].lower().startswith("write-in"):
                    continue
                key = (
                    person(record["CandidateName"]),
                    record["PartyName"].title().replace("Democrat", "Democratic").replace("Democraticic", "Democratic"),
                    record["JurisName"].replace("CITY OF BLOOMINGTON", "McLEAN").title(),
                )
                counts[key] = counts.get(key, 0) + int(record["VoteCount"])
            lines = [(name, label, county, count) for (name, label, county), count in counts.items()]
            for name, label, count in [(person(n), l, c) for n, l, c in candidates if not n.lower().startswith("write-in")]:
                if any(line[0] == name for line in lines) and sum(line[3] for line in lines if line[0] == name) != count:
                    raise ValueError(f"{day}: {name} counties do not sum to {count}")
        else:
            lines = [(person(name), label, f"cd:{district}", count) for name, label, count in candidates]
        for name, label, division, count in lines:
            found.append(
                {
                    "state": "IL",
                    "election_date": str(day),
                    "district_name": str(district),
                    "candidate_name": name,
                    "candidate_party_name": label,
                    "division_name": division,
                    "votes": str(count),
                    "outcome": "Defeated",
                    "runoff": False,
                    "primary": label,
                }
            )
    return found
