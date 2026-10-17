import csv
import io
import re
import struct
import subprocess
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import fastexcel
import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.elector_districts"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
CERTIFICATE = "https://www.archives.gov/files/electoral-college/{year}/{prefix}ascertainment-{state}.pdf"
YEARS = {2012: "2012-", 2016: "", 2020: "", 2024: ""}
STATES = {"ME": "maine", "NE": "nebraska"}
STATE_CODES = {"ME": "23", "NE": "31"}
LLDC = "https://lldc.mainelegislature.org/Open/Elections/Returns/{year}/{day}/elec_{day}{file}"
TOWNS = {
    1988: ("19881108", "i.pdf", ["h.pdf"]),
    1992: ("19921103", "d.xlsx", []),
    1996: ("19961105", "f.xlsx", ["l.xlsx"]),
    2000: ("20001107", "b.xlsx", ["m.xlsx"]),
    2004: ("20041102", "m.xlsx", ["w.xlsx"]),
    2008: ("20081104", "p.xlsx", ["z.xlsx", "ab.xlsx"]),
}
TABULATIONS = {
    1972: (
        "https://digitalmaine.com/tabulations_for_elections_1970s/29/",
        "https://web.archive.org/web/2024id_/https://digitalmaine.com/cgi/viewcontent.cgi?article=1028&context=tabulations_for_elections_1970s",
        ["McGovern", "Nixon"],
    ),
    1976: (
        "https://digitalmaine.com/tabulations_for_elections_1970s/62/",
        "https://web.archive.org/web/2024id_/https://digitalmaine.com/cgi/viewcontent.cgi?article=1061&context=tabulations_for_elections_1970s",
        ["Bubar", "Carter", "Ford", "McCarthy"],
    ),
    1980: (
        "https://digitalmaine.com/tabulations_for_elections_1980s/10/",
        "https://web.archive.org/web/2024id_/https://digitalmaine.com/cgi/viewcontent.cgi?article=1009&context=tabulations_for_elections_1980s",
        ["Anderson", "Carter", "Clark", "Commoner", "Hall", "Reagan", "Others"],
    ),
    1984: (
        "https://digitalmaine.com/tabulations_for_elections_1980s/41/",
        "https://web.archive.org/web/2024id_/https://digitalmaine.com/cgi/viewcontent.cgi?article=1040&context=tabulations_for_elections_1980s",
        ["Hall", "Mondale", "Reagan", "Serrette"],
    ),
}
COUNTIES = {
    "Androscoggin": (19509, 19406),
    "Aroostook": (11474, 19051),
    "Cumberland": (33326, 51268),
    "Franklin": (2988, 5958),
    "Hancock": (4191, 11889),
    "Kennebec": (16379, 24617),
    "Knox": (3601, 8478),
    "Lincoln": (2903, 7580),
    "Oxford": (6661, 12114),
    "Penobscot": (18552, 30186),
    "Piscataquis": (2518, 4617),
    "Sagadahoc": (3414, 6463),
    "Somerset": (5921, 10079),
    "Waldo": (2941, 6480),
    "Washington": (3742, 7820),
    "York": (22464, 30452),
}
FIRST = {"Cumberland", "Kennebec", "Knox", "Lincoln", "Sagadahoc", "Waldo", "York"}
TOWN_NAMES = {"Isleboro": "Islesboro"}
CANVASS = {
    2000: "https://sos.nebraska.gov/sites/default/files/doc/elections/2000/2000%20General.pdf",
    2008: "https://sos.nebraska.gov/sites/default/files/doc/elections/2008/2008%20General%20Canvass%20Book.pdf",
}
IMAGES = {
    2004: [
        "https://web.archive.org/web/2006id_/http://www.archives.gov/federal-register/electoral-college/2004_certificates/images/ascertainment_nebraska_01.jpg",
        "https://web.archive.org/web/2006id_/http://www.archives.gov/federal-register/electoral-college/2004_certificates/images/ascertainment_nebraska_02.jpg",
    ]
}
TRANSCRIBED = {
    "Bush": (512814, 169888, 153041, 189885),
    "Kerry": (254328, 96314, 97858, 60156),
    "Badnarik": (2041, 656, 813, 572),
    "Peroutka": (1314, 405, 305, 604),
    "Cobb": (978, 453, 261, 264),
    "Calero": (82, 30, 23, 29),
    "Nader": (5698, 2025, 1731, 1942),
}
PAIRS = (
    [(year, state) for year in YEARS for state in STATES]
    + [(year, "ME") for year in (*TABULATIONS, *TOWNS)]
    + [(year, "NE") for year in (*CANVASS, *IMAGES)]
)
DAYS = {
    1972: "1972-11-07",
    1976: "1976-11-02",
    1980: "1980-11-04",
    1984: "1984-11-06",
    1988: "1988-11-08",
    1992: "1992-11-03",
    1996: "1996-11-05",
    2000: "2000-11-07",
    2004: "2004-11-02",
    2008: "2008-11-04",
    2012: "2012-11-06",
    2016: "2016-11-08",
    2020: "2020-11-03",
    2024: "2024-11-05",
}
METHOD = {
    "certificate": (
        "Votes by congressional district for each ticket's district elector, from the state's certificate of "
        "ascertainment as the National Archives publishes it. The certificate lists only the tickets on the ballot, "
        "so write-ins, blanks, and scattering are not here; completeness is the tickets' share of the statewide vote."
    ),
    "towns": (
        "Votes by congressional district, summed from the Secretary of State's official vote by town, ward, and "
        "precinct as the Maine State Law and Legislative Reference Library publishes it. Each town is assigned to its "
        "district by the same year's returns for Representative to Congress; the districts sum to the statewide "
        "official vote for every candidate."
    ),
    "tabulation": (
        "Votes by congressional district for each ticket, from the total lines of the Secretary of State's official "
        "tabulation by district, county, and town, a scan with OCR text that the Maine State Library keeps on "
        "DigitalMaine. The district tables list the tickets, and in 1980 also the other write-ins; in other years "
        "write-ins are not here, and completeness is the tickets' share of the statewide vote."
    ),
    "counties": (
        "Votes for each ticket by county, from the Secretary of State's official tabulation, a scan that the Maine "
        "State Library keeps on DigitalMaine; each figure keeps its place on the page. The county figures were "
        "transcribed from the scan and add up to its printed state totals. Every county lay wholly in one district "
        "from 1972 through 1980, so a district's vote is the sum of its counties. The tabulation lists only the two "
        "major tickets, so write-ins are not here; completeness is the tickets' share of the statewide vote."
    ),
    "canvass": (
        "Votes by congressional district for each ticket, from the Secretary of State's official canvass book, "
        "which reports the presidential vote by district. The district tables leave out write-ins, so completeness is "
        "the tickets' share of the statewide vote."
    ),
}
MAINE = (
    "Maine counted {uocava} votes from uniformed-service and overseas voters only toward its at-large electors, not "
    "toward either district, so the two districts sum to slightly less than the statewide vote."
)
POOLED = (
    "The district tables name {names}, whom the statewide results on file count under Others, so their district votes "
    "are counted under Others here too."
)
WALDO = (
    "The 1988 vote book is a scan read by optical character recognition, and each figure keeps its place on the "
    "page. Votes are by county from the book's state summary, which sums to its state totals, except in Waldo County, "
    "which was split between the districts: its votes are by town from the book's Waldo table, which sums to the "
    "county's row."
)

WAYBACK = (
    "The National Archives' 2004 certificates survive only as page images on its former website, as the Internet "
    "Archive's Wayback Machine captured them in 2006. The images are too coarse to read by machine, so the figures "
    "were transcribed from them, each keeping its place on the page; every ticket's districts sum to its statewide "
    "figure, which matches the state's official vote."
)
SCANNED = (
    "The 2000 canvass book is a scan read by optical character recognition, and each figure keeps its place on the "
    "page. Each district table's total line was read three times and the readings agree, and the three districts sum "
    "to the statewide official vote for every ticket."
)


def documents(year: int, state: str) -> list[str]:
    if year in YEARS:
        return [CERTIFICATE.format(year=year, prefix=YEARS[year], state=STATES[state])]
    if year in IMAGES:
        return IMAGES[year]
    if state == "NE":
        return [CANVASS[year]]
    if year in TABULATIONS:
        return [TABULATIONS[year][1]]
    day, president, house = TOWNS[year]
    return [LLDC.format(year=year, day=day, file=name) for name in (president, *house)]


def fetch() -> None:
    for year, state in PAIRS:
        for address in documents(year, state):
            raw.fetch(SOURCE, address, suffix=Path(address).suffix)


def text(address: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, address).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def runs(pdf: Path, limit: int) -> list[tuple[int, str, scan.Box]]:
    found: list[tuple[int, str, scan.Box]] = []
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    for page in range(1, min(limit, int(info.split("Pages:")[1].split()[0])) + 1):
        joining = False
        for word, box in scan.layer(pdf, page):
            if re.fullmatch(r"[\d,.]*\d[\d,.]*", word) is None:
                joining = False
                continue
            if joining:
                _, digits, last = found[-1]
                if 0 <= box[0] - last[2] < 45 and min(box[3], last[3]) > max(box[1], last[1]):
                    found[-1] = (
                        page,
                        digits + re.sub(r"\D", "", word),
                        (last[0], min(last[1], box[1]), box[2], max(last[3], box[3])),
                    )
                    continue
            found.append((page, re.sub(r"\D", "", word), box))
            joining = True
    return found


def locate(found: list[tuple[int, str, scan.Box]], values: list[int]) -> list[tuple[int, scan.Box]]:
    located = []
    cursor = 0
    for value in values:
        after = [index for index in range(cursor, len(found)) if found[index][1] == str(value)]
        anywhere = [index for index in range(len(found)) if found[index][1] == str(value)]
        if not after and len(anywhere) != 1:
            raise ValueError(f"{value} is not in the word layer")
        index = after[0] if after else anywhere[0]
        located.append((found[index][0], found[index][2]))
        cursor = max(cursor, index + 1)
    return located


def place(county: str, town: str, ward: str | None) -> tuple[str, str, str]:
    return (
        county.strip().upper(),
        re.sub(r"\s+", " ", town.strip().upper()),
        re.sub(r"[^0-9WP]", "", (ward or "").upper()),
    )


def parse() -> None:
    rows = []
    cells = []
    for year, state in PAIRS:
        addresses = documents(year, state)
        uocava = short = 0
        start = len(rows)
        if year in YEARS and state == "NE":
            spans = runs(raw.cached(SOURCE, addresses[0]).path, 3)
            for block in re.split(r"\n(?=\S[^\n]*,\s*Pre[^\n]*/)", text(addresses[0]))[1:]:
                numbers = [
                    int(re.sub(r"\D", "", found)) for found in re.findall(r"Vote\w*\s+Rec[^:\n]*:\s*(\d[\d ,]*)", block)
                ][:4]
                head = re.search(r"\s{2,}(.*?),\s*Pre", block.split("\n", 1)[0])
                if len(numbers) != 4 or numbers[0] != sum(numbers[1:]) or head is None:
                    raise ValueError(f"{year} {state}: unreadable ticket {block[:80]!r} {numbers}")
                rows.append((year, state, head.group(1), *numbers))
        elif year in YEARS:
            certificate = text(addresses[0])
            found = re.search(r"There were ([\d,]+) votes", certificate)
            uocava = int(found.group(1).replace(",", "")) if found else 0
            spans = runs(raw.cached(SOURCE, addresses[0]).path, 3)
            for block in re.split(r"(?=The [A-Z ]+(?:PARTY|ALL)\s+Electors)", certificate)[1:]:
                numbers = [
                    int(re.sub(r"\D", "", found.group(1)))
                    for line in block.split("\n")
                    if re.search(r"[a-z]", line)
                    and not re.search(r"District|Large|Congress|votes|There were|President", line, re.IGNORECASE)
                    and (found := re.search(r"\s(\d[\d ,]*\d|\d)\s*$", line))
                ][:3]
                head = re.search(r"candidacy\s*of\s*(.*?)\s*(?:/or|for)\s+President", block, re.DOTALL)
                if len(numbers) != 3 or head is None or not 0 <= numbers[2] - numbers[0] - numbers[1] <= uocava:
                    raise ValueError(f"{year} {state}: unreadable ticket {block[:80]!r} {numbers}")
                rows.append((year, state, re.sub(r"\s+", " ", head.group(1)), numbers[2], *numbers[:2], None))
                short += numbers[2] - numbers[0] - numbers[1]
            if short > uocava:
                raise ValueError(f"{year} {state}: the districts fall short by more than the {uocava} overseas votes")
        if year in YEARS:
            tickets = rows[start:]
            located = iter(
                locate(
                    spans,
                    [
                        value
                        for _, _, _, statewide, *districts in tickets
                        for value in ([statewide, *districts] if state == "NE" else [*districts[:2], statewide])
                        if value is not None
                    ],
                )
            )
            for _, _, candidate, _, *districts in tickets:
                spots = [next(located) for _ in range(4 if state == "NE" else 3)]
                for number, (page, box) in enumerate(spots[1:] if state == "NE" else spots[:2], start=1):
                    cells.append(
                        (
                            year,
                            state,
                            candidate,
                            number,
                            "us.cd",
                            STATE_CODES[state] + f"{number:02d}",
                            districts[number - 1],
                            page,
                            *box,
                        )
                    )
        elif year in TABULATIONS:
            names = TABULATIONS[year][2]
            document = text(addresses[0])
            if year == 1972:
                districts = [
                    [
                        sum(votes[index] for county, votes in COUNTIES.items() if (county in FIRST) is first)
                        for index in (0, 1)
                    ]
                    for first in (True, False)
                ]
            elif year == 1976:
                halves = [
                    [int(number.replace(",", "")) for number in found.groups()]
                    for found in re.finditer(
                        r"^\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$", document[: document.index("November 2, 1976")], re.MULTILINE
                    )
                ]
                halves = halves[-6:]
                districts = [halves[2] + halves[3], halves[4] + halves[5]] if len(halves) == 6 else []
                if not districts or [one + two for one, two in zip(*districts, strict=True)] != halves[0] + halves[1]:
                    raise ValueError(f"{year} {state}: the district tables do not sum to the state table {halves}")
            else:
                districts = [
                    [int(number.replace(",", "")) for number in found.group(1).split()][: len(names)]
                    for found in re.finditer(r"^\s*TOTA\S* +(\d[\d, ]*\d)", document, re.MULTILINE)
                ][:2]
            if len(districts) != 2 or any(len(district) != len(names) for district in districts):
                raise ValueError(f"{year} {state}: unreadable district totals {districts}")
            for name, one, two in zip(names, *districts, strict=True):
                rows.append((year, state, name, one + two, one, two, None))
            pdf = raw.cached(SOURCE, addresses[0]).path
            if year == 1972:
                words = scan.layer(pdf, 1)
                clean = [box for word, box in words if re.fullmatch(r"\d{1,3}(?:,\d{3})+", word)]
                left = sorted(box[2] for box in clean if box[0] + box[2] < 4400)
                right = sorted(box[2] for box in clean if box[0] + box[2] >= 4400)
                edges = [left[len(left) // 2], right[len(right) // 2]]
                for county, votes in COUNTIES.items():
                    (anchor,) = [
                        box for word, box in words if word == (county[:6] if county == "Penobscot" else county)
                    ]
                    for name, value, edge in zip(names, votes, edges, strict=True):
                        cells.append(
                            (
                                year,
                                state,
                                name,
                                1 if county in FIRST else 2,
                                "us.county",
                                county,
                                value,
                                1,
                                edge - 270,
                                anchor[1] - 5,
                                edge + 25,
                                anchor[3] + 20,
                            )
                        )
            else:
                located = iter(locate(runs(pdf, 5), [value for district in districts for value in district]))
                for number, district in enumerate(districts, start=1):
                    for name, value in zip(names, district, strict=True):
                        page, box = next(located)
                        cells.append((year, state, name, number, "us.cd", f"23{number:02d}", value, page, *box))
        elif year in IMAGES:
            readings = []
            for number, address in enumerate(addresses, start=1):
                image = Image.open(raw.cached(SOURCE, address).path).convert("L")
                buffer = io.BytesIO()
                image.resize((image.width * 4, image.height * 4), Image.Resampling.LANCZOS).save(buffer, "PNG")
                lines: dict[tuple[str, str, str], list[tuple[str, scan.Box]]] = {}
                for word in csv.DictReader(
                    io.StringIO(
                        subprocess.run(
                            ["tesseract", "stdin", "stdout", "--psm", "6", "tsv"],
                            input=buffer.getvalue(),
                            capture_output=True,
                            check=True,
                        ).stdout.decode()
                    ),
                    delimiter="\t",
                    quoting=csv.QUOTE_NONE,
                ):
                    if word["level"] == "5" and word["text"].strip():
                        lines.setdefault((word["block_num"], word["par_num"], word["line_num"]), []).append(
                            (
                                word["text"],
                                (
                                    int(word["left"]) // 4,
                                    int(word["top"]) // 4,
                                    (int(word["left"]) + int(word["width"])) // 4,
                                    (int(word["top"]) + int(word["height"])) // 4,
                                ),
                            )
                        )
                for words in lines.values():
                    marks = [index for index, (word, _) in enumerate(words) if word.startswith(("Rec", "Ree"))]
                    tail = [
                        box
                        for word, box in (words[marks[0] + 1 :] if marks else [])
                        if re.fullmatch(r"\$?[\d,.]*\d[\d,.]*", word.translate(scan.CONFUSABLE))
                    ]
                    if tail:
                        readings.append(
                            (
                                number,
                                (
                                    min(box[0] for box in tail),
                                    min(box[1] for box in tail),
                                    max(box[2] for box in tail),
                                    max(box[3] for box in tail),
                                ),
                            )
                        )
            if len(readings) != 4 * len(TRANSCRIBED):
                raise ValueError(f"{year} {state}: {len(readings)} vote lines, not {4 * len(TRANSCRIBED)}")
            spots = iter(readings)
            for name, (statewide, *districts) in TRANSCRIBED.items():
                if statewide != sum(districts):
                    raise ValueError(f"{year} {state}: {name}'s districts do not sum to {statewide}")
                rows.append((year, state, name, statewide, *districts))
                next(spots)
                for district, value in enumerate(districts, start=1):
                    page, box = next(spots)
                    cells.append((year, state, name, district, "us.cd", f"31{district:02d}", value, page, *box))
        elif state == "NE" and year == 2000:
            pdf = raw.cached(SOURCE, addresses[0]).path
            names = ["Bush", "Gore", "Browne", "Hagelin", "Nader", "Buchanan", "Phillips"]
            districts = []
            for number in (9, 10):
                with tempfile.TemporaryDirectory() as folder:
                    page = scan.rendered(pdf, number, Path(folder))
                    image = Image.open(page).convert("L")
                    for lines, columns in scan.tables(page):
                        left, top = columns[0] + 4, lines[0] + 4
                        crop = image.crop((left, top, image.width - 60, lines[-1] - 2))
                        readings = []
                        for psm, scale in (("6", 1), ("6", 2), ("4", 1)):
                            buffer = io.BytesIO()
                            crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS).save(
                                buffer, "PNG"
                            )
                            words = [
                                (
                                    word["text"].strip(),
                                    (
                                        left + int(word["left"]) // scale,
                                        top + int(word["top"]) // scale,
                                        left + (int(word["left"]) + int(word["width"])) // scale,
                                        top + (int(word["top"]) + int(word["height"])) // scale,
                                    ),
                                )
                                for word in csv.DictReader(
                                    io.StringIO(
                                        subprocess.run(
                                            ["tesseract", "stdin", "stdout", "--psm", psm, "tsv"],
                                            input=buffer.getvalue(),
                                            capture_output=True,
                                            check=True,
                                        ).stdout.decode()
                                    ),
                                    delimiter="\t",
                                    quoting=csv.QUOTE_NONE,
                                )
                                if word["level"] == "5" and word["text"].strip()
                            ]
                            band = [
                                (word, box)
                                for word, box in sorted(words, key=lambda word: word[1][0])
                                if lines[2] - 3 <= (box[1] + box[3]) / 2 <= lines[3] + 3
                            ]
                            figures: list[tuple[str, scan.Box]] = []
                            for word, box in band[1:]:
                                digits = re.sub(r"\D", "", word.translate(scan.CONFUSABLE))
                                if figures and box[0] - figures[-1][1][2] < 45:
                                    last = figures[-1][1]
                                    figures[-1] = (
                                        figures[-1][0] + digits,
                                        (last[0], min(last[1], box[1]), box[2], max(last[3], box[3])),
                                    )
                                elif digits:
                                    figures.append((digits, box))
                            if band and band[0][0].lower() == "total" and len(figures) == len(names):
                                readings.append(figures)
                        tally = Counter(tuple(digits for digits, _ in figures) for figures in readings).most_common(1)
                        if not tally or tally[0][1] < 2:
                            raise ValueError(f"{year} {state}: page {number}'s district total is unreadable {readings}")
                        agreed = next(
                            figures for figures in readings if tuple(digits for digits, _ in figures) == tally[0][0]
                        )
                        districts.append((number, [int(digits) for digits, _ in agreed], [box for _, box in agreed]))
            if len(districts) != 3:
                raise ValueError(f"{year} {state}: {len(districts)} district tables, not 3")
            for index, name in enumerate(names):
                values = [counts[index] for _, counts, _ in districts]
                rows.append((year, state, name, sum(values), *values))
                for district, (number, counts, boxes) in enumerate(districts, start=1):
                    cells.append(
                        (
                            year,
                            state,
                            name,
                            district,
                            "us.cd",
                            f"31{district:02d}",
                            counts[index],
                            number,
                            *boxes[index],
                        )
                    )
        elif state == "NE":
            book = text(addresses[0])
            heading = re.search(r"^For President of the United States\s*$", book, re.MULTILINE)
            total = re.search(r"^TOTAL +([\d ]+)$", book[heading.end() :] if heading else "", re.MULTILINE)
            if total is None:
                raise ValueError(f"{year} {state}: no statewide table")
            statewide = [int(number) for number in total.group(1).split()]
            districts = []
            for block in re.split(
                r"Congressional District (?:One|Two|Three)\n",
                book[book.index("For President of the United States by Congressional District") :],
            )[1:4]:
                found = re.search(r"^TOTAL +([\d ]+)$", block, re.MULTILINE)
                names = block.split("\n")[1].split()
                if found is None or len(names) != len(found.group(1).split()):
                    raise ValueError(f"{year} {state}: unreadable district table {block[:80]!r}")
                districts.append(dict(zip(names, map(int, found.group(1).split()), strict=True)))
            for number, name in enumerate(districts[0]):
                values = [district[name] for district in districts]
                if statewide[number] != sum(values):
                    raise ValueError(
                        f"{year} {state}: {name}'s districts sum to {sum(values)}, not {statewide[number]}"
                    )
                rows.append((year, state, name, statewide[number], *values))
        elif year == 1988:
            pdf, house = raw.cached(SOURCE, addresses[0]).path, text(addresses[1])
            names = ["Bush", "Dukakis", "Fulani", "Paul", "Others"]
            tables = {}
            for number, marker, reach in ((4, "TOTALS", 45), (20, "TOTALS:", 22)):
                words = scan.layer(pdf, number)
                (anchor,) = [box for word, box in words if word == marker]
                line = sorted(
                    (box, word) for word, box in words if re.search(r"\d", word) and abs(box[1] - anchor[1]) < 30
                )
                edges = [box[2] for box, _ in line]
                labels = (
                    {county: county for county in COUNTIES}
                    if number == 4
                    else {
                        TOWN_NAMES.get(word, word) + (" Springs" if word == "Stockton" else ""): word
                        for word, box in words
                        if re.fullmatch(r"[A-Z][a-z]+", word) and box[0] < 200
                    }
                )
                table = {}
                for key, label in labels.items():
                    (name,) = [box for word, box in words if word == label]
                    middle = (name[1] + name[3]) / 2
                    columns: list[list[tuple[scan.Box, str]]] = [[] for _ in edges]
                    for word, box in words:
                        nearest = min(range(len(edges)), key=lambda index: abs(edges[index] - box[2]))
                        if (
                            re.search(r"\d", word)
                            and box[0] > name[2]
                            and abs(edges[nearest] - box[2]) <= 60
                            and abs((box[1] + box[3]) / 2 - middle) <= reach
                        ):
                            columns[nearest].append((box, word))
                    table[key] = [
                        (
                            int(re.sub(r"\D", "", "".join(word for _, word in sorted(hits))) or 0),
                            (
                                min(box[0] for box, _ in hits),
                                min(box[1] for box, _ in hits),
                                max(box[2] for box, _ in hits),
                                max(box[3] for box, _ in hits),
                            )
                            if hits
                            else (edge - 180, name[1], edge + 20, name[3]),
                        )
                        for edge, hits in zip(edges, columns, strict=True)
                    ]
                tables[number] = ([int(word.replace(",", "")) for _, word in line], table)
            (totals, summary), (waldo, towns) = tables[4], tables[20]
            counties = {county: [value for value, _ in row] for county, row in summary.items()}
            if len(totals) != 5 or [sum(column) for column in zip(*counties.values(), strict=True)] != totals:
                raise ValueError(f"{year} {state}: the county summary does not sum to the state totals")
            first = house[house.index("DISTRICT 1 SUMMARY") : house.index("DISTRICT 2 SUMMARY")]
            second = house[house.index("DISTRICT 2 SUMMARY") :]
            ones = set(re.findall(r"^\s*([A-Z][a-z]+)\s+\d", first[: first.index("STATE")], re.MULTILINE))
            twos = set(re.findall(r"^\s*([A-Z][a-z]+)\s+\d", second[: second.index("STATE")], re.MULTILINE))
            if ones | twos != set(counties) or ones & twos != {"Waldo"}:
                raise ValueError(f"{year} {state}: the districts do not partition the counties apart from Waldo")
            section = house[house.index("COUNTY: WALDO") :]
            section = section[: section.index("TOTALS:")]
            west = {
                TOWN_NAMES.get(found.group(1), found.group(1))
                for found in re.finditer(r"^([A-Z][A-Za-z.' ]*?)\s{2,}\d", section, re.MULTILINE)
            }
            if (
                len(towns) != 26
                or not west <= set(towns)
                or [sum(value for value, _ in column) for column in zip(*towns.values(), strict=True)]
                != counties["Waldo"]
                or waldo != counties["Waldo"]
            ):
                raise ValueError(f"{year} {state}: Waldo's towns do not sum to its county total")
            for number, name in enumerate(names):
                one = sum(counties[county][number] for county in ones - {"Waldo"}) + sum(
                    towns[town][number][0] for town in west
                )
                two = sum(counties[county][number] for county in twos - {"Waldo"}) + sum(
                    row[number][0] for town, row in towns.items() if town not in west
                )
                if one + two != totals[number]:
                    raise ValueError(f"{year} {state}: {name}'s districts do not sum to the state total")
                rows.append((year, state, name, totals[number], one, two, None))
                for county, row in summary.items():
                    if county != "Waldo":
                        cells.append(
                            (
                                year,
                                state,
                                name,
                                1 if county in ones else 2,
                                "us.county",
                                county,
                                row[number][0],
                                4,
                                *row[number][1],
                            )
                        )
                for town, row in towns.items():
                    cells.append(
                        (
                            year,
                            state,
                            name,
                            1 if town in west else 2,
                            "us.town",
                            town,
                            row[number][0],
                            20,
                            *row[number][1],
                        )
                    )
        else:
            sheets = [
                fastexcel.read_excel(raw.cached(SOURCE, address).path)
                .load_sheet(0, header_row=None, dtypes="string")
                .to_polars()
                .rows()
                for address in addresses
            ]
            district = {}
            for sheet in sheets[1:]:
                for row in sheet:
                    if row[0] == "CG":
                        district[place(row[2], row[3], row[4])] = int(float(row[1]))
                    elif year == 2000 and row[0] and row[0].strip().isdigit() and row[4]:
                        district[place(row[1], row[2], row[3])] = int(float(row[4]))
            totals: Counter[tuple[int, str]] = Counter()
            for row in sheets[0]:
                if year == 1992 and row[0] and row[0].strip().isdigit():
                    unit = int(float(row[3]))
                    pairs = [*zip(row[11:17], row[4:10], strict=True), ("Others", row[10])]
                elif year == 2000 and row[0] and row[0].strip().isdigit():
                    unit = district[place(row[1], row[2], row[3])]
                    pairs = [*((row[index + 1], row[index]) for index in range(4, 16, 2)), ("Others", row[16])]
                elif year in (1996, 2004) and row[0] and len(row[0].strip()) == 3 and row[3] is not None:
                    unit = district[place(row[0], row[1], row[2])]
                    pairs = [(row[index], row[index + 1]) for index in range(3, len(row) - 1, 2) if row[index]]
                elif year == 2008 and row[0] == "PR":
                    unit = district[place(row[1], row[2], row[3])]
                    pairs = [*((row[index], row[index + 2]) for index in range(4, 16, 3)), ("Others", row[17])]
                else:
                    continue
                for name, votes in pairs:
                    if name is not None and votes is not None:
                        surname = name.split(",")[0].strip().title()
                        totals[(unit, "Others" if surname in ("Other", "Others") else surname)] += int(float(votes))
            for name in dict.fromkeys(name for _, name in totals):
                rows.append(
                    (
                        year,
                        state,
                        name,
                        totals[(1, name)] + totals[(2, name)],
                        totals[(1, name)],
                        totals[(2, name)],
                        None,
                    )
                )
        rows.append((year, state, "", uocava, None, None, None))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int16,
            "state": pl.String,
            "candidate": pl.String,
            "statewide": pl.Int64,
            "d1": pl.Int64,
            "d2": pl.Int64,
            "d3": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "certificates.parquet")
    pl.DataFrame(
        cells,
        schema={
            "year": pl.Int16,
            "state": pl.String,
            "candidate": pl.String,
            "district": pl.Int8,
            "scheme": pl.String,
            "unit": pl.String,
            "value": pl.Int64,
            "page": pl.Int32,
            "x0": pl.Int32,
            "y0": pl.Int32,
            "x1": pl.Int32,
            "y1": pl.Int32,
        },
        orient="row",
    ).write_parquet(PARSED / "cells.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "certificates.parquet")
    cells = pl.read_parquet(PARSED / "cells.parquet")
    with connect() as conn:
        places = {
            (scheme, text.removesuffix(", ME").removesuffix(" County")): code
            for scheme, code, text in conn.execute(
                """select u.scheme_code, u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.is_preferred
                   where (u.scheme_code = 'us.county' and u.code like '23%')
                      or (u.scheme_code = 'us.town' and u.code like '23027%')"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's presidential votes by congressional district 1972–2024 and Nebraska's 2000–2024, from the states' certificates of ascertainment, Maine's official vote by town, and Nebraska's canvass book",
            polity="US",
        )
        for (year, state), lines in frame.group_by("year", "state", maintain_order=True):
            day = DAYS[int(year)]
            if cs.key(f"{SOURCE}.result_set", f"{day}:{state}-01:president") not in cs.minted:
                continue
            uocava = int(lines.filter(pl.col("candidate") == "")["statewide"][0])
            lines = lines.filter(pl.col("candidate") != "")
            kind = (
                "certificate"
                if year in YEARS or year in IMAGES
                else "counties"
                if year == 1972
                else "tabulation"
                if year in TABULATIONS
                else "towns"
                if state == "ME"
                else "canvass"
            )
            addresses = documents(int(year), str(state))
            publisher, title, license = {
                "certificate": (
                    "National Archives and Records Administration",
                    f"{year} certificate of ascertainment, {'Maine' if state == 'ME' else 'Nebraska'}",
                    "Public domain",
                ),
                "towns": (
                    "Maine Department of the Secretary of State",
                    f"{year} general election, official vote for President by town",
                    "Public record",
                ),
                "tabulation": (
                    "Maine Department of the Secretary of State",
                    f"{year} general election, official tabulation for President",
                    "Public domain",
                ),
                "counties": (
                    "Maine Department of the Secretary of State",
                    f"{year} general election, official tabulation for President",
                    "Public domain",
                ),
                "canvass": ("Nebraska Secretary of State", f"{year} general canvass book", "Public record"),
            }[kind]
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=TABULATIONS[int(year)][0] if int(year) in TABULATIONS else addresses[0],
                license=license,
            )
            artifacts = [cs.artifact(raw.cached(SOURCE, address), source) for address in addresses]
            located = cells.filter((pl.col("year") == year) & (pl.col("state") == state))
            pages = {}
            for number in located["page"].unique().sort():
                scanned = int(year) in IMAGES
                pdf = raw.cached(SOURCE, addresses[number - 1 if scanned else 0])
                image = PAGES / f"{pdf.sha256}-{1 if scanned else number}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    if scanned:
                        Image.open(pdf.path).save(image)
                    else:
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                            + [str(pdf.path), str(image.with_suffix(""))],
                            check=True,
                        )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                page = cs.page(artifacts[number - 1 if scanned else 0], 1 if scanned else number)
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifacts[number - 1 if scanned else 0],
                    page_number=1 if scanned else number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                pages[number] = (page, width * (1 if scanned else 3), height * (1 if scanned else 3))
            options = conn.execute(
                """select o.id, o.kind, o.party_id, sum(v.value)::bigint,
                          (select array_agg(array[cy.role::text, cy.person_id::text, n.text] order by cy.role)
                           from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                           where cy.option_id = o.id), o.ballot_label
                   from external_id x join contest_option o on o.contest_id = x.entity_id and o.kind in ('ticket', 'write_in', 'others')
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s
                   group by o.id, o.kind, o.party_id, o.ballot_label""",
                [f"{day}:{state}:president"],
            ).fetchall()
            total = conn.execute(
                """select sum(v.value)::bigint from external_id x
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1 and v.option_k <> 0
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{day}:{state}:president"],
            ).fetchone()
            if total is None or total[0] is None:
                raise ValueError(f"{day}:{state}:president has no default votes")
            tickets = []
            pooled: list[tuple[str, int, list[int]]] = []
            members: dict[int, list[str]] = {}
            for candidate, statewide, *districts in lines.select(
                "candidate", "statewide", "d1", "d2", "d3"
            ).iter_rows():
                values = [value for value in districts if value is not None]
                surname = slug(candidate).split("-")[-1]
                found = [row for row in options if row[3] == statewide and row[4]] or [
                    row
                    for row in options
                    if any(role == "head" and slug(text).split("-")[-1] == surname for role, _, text in row[4] or [])
                ]
                scattered = [row for row in options if row[1] == "write_in" and not row[4] and row[3] == statewide]
                if not found and len(scattered) == 1:
                    members[len(tickets)] = [candidate]
                    tickets.append(("write_in", None, [], values, scattered[0][5]))
                    continue
                if not found and any(row[1] == "others" for row in options):
                    pooled.append((candidate, statewide, values))
                    continue
                if not found and candidate == "Others":
                    if statewide != sum(row[3] for row in options if row[1] == "write_in"):
                        raise ValueError(f"{day}:{state}: others ({statewide}) are not the write-ins on file")
                    members[len(tickets)] = [candidate]
                    tickets.append(("others", None, [], values, "Write-ins"))
                    continue
                if len(found) != 1:
                    raise ValueError(f"{day}:{state}: {candidate} ({statewide}) matches {len(found)} tickets")
                option, option_kind, party, held, candidacies, _ = found[0]
                if abs(held - statewide) > max(5, held * 0.001):
                    raise ValueError(f"{day}:{state}: {candidate} {statewide} against {held} on file")
                members[len(tickets)] = [candidate]
                tickets.append((option_kind, party, candidacies, values, None))
            if pooled:
                residual = next(row[3] for row in options if row[1] == "others")
                if sum(statewide for _, statewide, _ in pooled) > residual:
                    raise ValueError(f"{day}:{state}: {pooled} exceed the {residual} others on file")
                members[len(tickets)] = [candidate for candidate, *_ in pooled]
                tickets.append(
                    (
                        "others",
                        None,
                        [],
                        [sum(column) for column in zip(*(values for *_, values in pooled), strict=True)],
                        "Others",
                    )
                )
            counted = int(lines["statewide"].sum())
            for number in range(1, 3 if state == "ME" else 4):
                district_key = f"{day}:{state}-{number:02d}:president"
                contest, election, area, unit, existing = conn.execute(
                    """select c.id, c.election_id, c.district_area_id, u.id, rs.id
                       from external_id x join contest c on c.id = x.entity_id
                       join reporting_unit u on u.area_id = c.district_area_id and u.scheme_code = 'us.cd'
                       join result_set rs on rs.contest_id = c.id and rs.is_default
                       where x.scheme = 'us.contest' and x.value = %s""",
                    [district_key],
                ).fetchone() or (None, None, None, None, None)
                if contest is None:
                    raise ValueError(f"{district_key} is not held")
                declared = conn.execute(
                    """select cy.person_id from outcome o join candidacy cy on cy.option_id = o.option_id
                       and cy.role = 'head' where o.result_set_id = %s and o.kind = 'won'""",
                    [existing],
                ).fetchone()
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                              supersedes_id from result_set where id = %s""",
                    [existing],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
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
                                ),
                                row,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", district_key)
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
                    completeness=round(counted / total[0], 4),
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD[kind])
                if uocava:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=MAINE.format(uocava=f"{uocava:,}"))
                named = [candidate for candidate, *_ in pooled if candidate != "Others"]
                if named:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=POOLED.format(names=" and ".join(named)),
                    )
                if year == 1988 and state == "ME":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=WALDO)
                if year == 2000 and state == "NE":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=SCANNED)
                if year in IMAGES:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=WAYBACK)
                ranked = []
                joined: set[tuple[str, str]] = set()
                for index, (option_kind, party, candidacies, districts, label) in enumerate(tickets):
                    head = next((person for role, person, _ in candidacies if role == "head"), None)
                    option_key = f"{district_key}:{head or 'others'}"
                    option = cs.key("us.option", option_key)
                    if option in cs.minted:
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind=option_kind,
                            party_id=party,
                            ballot_label=label,
                        )
                        for role, person, text in candidacies:
                            cs.add(
                                "candidacy",
                                id=cs.key("us.candidacy", f"{option_key}:{role}"),
                                person_id=person,
                                option_id=option,
                                role=role,
                                ballot_name=text,
                                is_independent=False,
                            )
                    spots = located.filter(
                        pl.col("candidate").is_in(members[index]) & (pl.col("district") == number)
                    ).group_by("scheme", "unit", maintain_order=True)
                    placed = 0
                    for (scheme, name), group in spots:
                        place = unit if scheme == "us.cd" else cs.unit(str(scheme), places[(str(scheme), str(name))])
                        if scheme != "us.cd" and (str(scheme), str(name)) not in joined:
                            joined.add((str(scheme), str(name)))
                            cs.add(
                                "reporting_unit_area",
                                unit_id=place,
                                area_id=area,
                                valid_from=date.fromisoformat(day),
                                valid_to=date.fromisoformat(day) + timedelta(days=1),
                            )
                        if group["page"].n_unique() != 1:
                            raise ValueError(f"{district_key}: {name} spans pages")
                        page, width, height = pages[int(group["page"][0])]
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=place,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            value=int(group["value"].sum()),
                            flags=8 | (1 if group.height > 1 else 0),
                        )
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=place,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            page_id=page,
                            source_id=None,
                            x0=min(group["x0"].to_list()) / width,
                            y0=min(group["y0"].to_list()) / height,
                            x1=max(group["x1"].to_list()) / width,
                            y1=max(group["y1"].to_list()) / height,
                        )
                        placed += int(group["value"].sum())
                    if located.height and placed != districts[number - 1]:
                        raise ValueError(f"{district_key}: the cells sum to {placed}, not {districts[number - 1]}")
                    if not located.height:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            value=districts[number - 1],
                            flags=0,
                        )
                    ranked.append((districts[number - 1], head, option))
                ranked.sort(key=lambda item: item[0], reverse=True)
                if declared is None or str(declared[0]) != ranked[0][1] or ranked[0][0] == ranked[1][0]:
                    raise ValueError(f"{district_key}: the certificate's leader is not the declared winner")
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][2], kind="won", seats=1)
        return cs.write()
