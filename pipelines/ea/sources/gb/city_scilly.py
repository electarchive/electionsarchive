import difflib
import html
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import LiteralString
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import foreign_preferred
from ea.store import DATA, Connection, connect

SOURCE = "gb.city_scilly"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
VOTES, BALLOTS, INVALID, VOTERS = 1, 3, 5, 14
SHARE_ONLY = 4
CITY_DAY = date(2022, 3, 24)
CITY_NOTICES = {
    date(2009, 3, 16): (
        "http://www.cityoflondon.gov.uk/about-the-city/how-we-work/elections-and-wards/Documents/ALL.pdf",
        "https://web.archive.org/web/20120830201319if_/",
        "Election of Common Councilmen: notices of persons elected, 16 March 2009",
    ),
    date(2013, 3, 21): (
        "http://www.cityoflondon.gov.uk/about-the-city/how-we-work/elections-and-wards/Documents/Notice-of-Results-City-Vote-2013.pdf",
        "https://web.archive.org/web/20130720042049if_/",
        "Election of Common Councilmen: notices of persons elected, 21 March 2013",
    ),
    date(2017, 3, 23): (
        "https://www.cityoflondon.gov.uk/about-the-city/voting-elections/Documents/notice-of-persons-elected-2017.pdf",
        "https://web.archive.org/web/20171109022948if_/",
        "Election of Common Councilmen: notices of persons elected, 23 March 2017",
    ),
}
CITY_SURNAMES = {
    "Robert James (commonly known as Jamie) Ingham Clark": ("Ingham Clark", "Robert James (commonly known as Jamie)"),
    "Havilland James De Sausmarez": ("De Sausmarez", "Havilland James"),
    "Xuelin Li Lady Bates": ("Bates", "Xuelin Li"),
}
NOTICE_VOTES = re.compile(r"\d+|\(E\)|Uncontested")
CITY_PAGE = "https://www.cityoflondon.gov.uk/about-us/voting-elections/{page}-2022-elections-results"
WAYBACK = "https://web.archive.org/web/2022if_/"
CITY_PAGE_2025 = "https://www.cityoflondon.gov.uk/about-us/voting-elections/elections-results/2025-city-elections-results/ward-of-{page}"
CITY_RESULTS = "https://www.cityoflondon.gov.uk/about-us/voting-elections/{path}"
CITY_RESULTS_LIVE = CITY_RESULTS.format(path="elections-results")
ALDERMEN_PDF = "https://www.cityoflondon.gov.uk/assets/About-us/all-wards-aldermanic-results.pdf"
ALDERMEN_ARCHIVE = "https://web.archive.org/web/20210625044537if_/"
RESULTS_CAPTURES = (
    ("20210507003827", "elections/election-results"),
    ("20211020024105", "elections-results"),
    ("20220714182649", "elections-results"),
    ("20221104165456", "elections-results"),
    ("20230207220646", "elections-results"),
    ("20231002121322", "elections-results"),
    ("20240911073304", "elections-results"),
    ("20250104124228", "elections-results"),
    ("20250416013538", "elections-results"),
    ("20250928061907", "elections-results"),
    ("20260814070845", "elections-results"),
)
HISTORY_PAGES = (
    ("20161011142051", "http://www.cityoflondon.gov.uk/about-the-city/elections-and-wards/Pages/election.aspx"),
    ("20171223181623", "https://www.cityoflondon.gov.uk/about-the-city/voting-elections/Pages/results.aspx"),
    ("20191016091131", "https://www.cityoflondon.gov.uk/about-the-city/voting-elections/Pages/results.aspx"),
)
WARDS_FROM = date(2013, 3, 8)
DAY_FIXES = {
    ("E05009307", date(2017, 8, 7)): (
        date(2019, 8, 6),
        (
            "The aldermanic results PDF dates this election 7 August 2017; the results page captured in 2019 dates it "
            "Tuesday 6 August 2019, which fits the six-year term from Charles Bowman's election on 7 May 2013. The "
            "page's date is used."
        ),
    ),
    ("E05009305", date(2017, 2, 9)): (
        date(2017, 2, 8),
        (
            "The results page captured in 2017 dates this election Wednesday 9 February 2017, but 9 February 2017 was "
            "a Thursday; the aldermanic results PDF gives 8 February 2017, which is used."
        ),
    ),
}
CITY_CODE = "E09000001"
CITY_WARDS = (
    ("E05009288", "Aldersgate", "aldersgate"),
    ("E05009289", "Aldgate", "aldgate"),
    ("E05009290", "Bassishaw", "bassishaw"),
    ("E05009291", "Billingsgate", "billingsgate"),
    ("E05009292", "Bishopsgate", "bishopsgate"),
    ("E05009293", "Bread Street", "bread-street"),
    ("E05009294", "Bridge and Bridge Without", "bridge-and-bridge-without"),
    ("E05009295", "Broad Street", "broad-street"),
    ("E05009296", "Candlewick", "candlewick"),
    ("E05009297", "Castle Baynard", "castle-baynard"),
    ("E05009298", "Cheap", "cheap"),
    ("E05009299", "Coleman Street", "coleman-street"),
    ("E05009300", "Cordwainer", "cordwainer"),
    ("E05009301", "Cornhill", "cornhill"),
    ("E05009302", "Cripplegate", "cripplegate"),
    ("E05009303", "Dowgate", "dowgate"),
    ("E05009304", "Farringdon Within", "farringdon-within"),
    ("E05009305", "Farringdon Without", "farringdon-without"),
    ("E05009306", "Langbourn", "langbourn"),
    ("E05009307", "Lime Street", "lime-street"),
    ("E05009308", "Portsoken", "portsoken"),
    ("E05009309", "Queenhithe", "queenhithe"),
    ("E05009310", "Tower", "tower"),
    ("E05009311", "Vintry", "vintry"),
    ("E05009312", "Walbrook", "walbrook"),
)
OLD_WARDS = tuple((f"E05{number:06d}", label, page) for number, (_, label, page) in enumerate(CITY_WARDS, start=1))
OLD_CODES = {new: old for (new, *_), (old, *_) in zip(CITY_WARDS, OLD_WARDS, strict=True)}
NEW_CODES = {old: new for new, old in OLD_CODES.items()}
CITY_SEATS = 100
GENERAL_DAYS = (date(2009, 3, 16), date(2013, 3, 21), date(2017, 3, 23), CITY_DAY, date(2025, 3, 20))
CITY_NOTES = {
    date(2009, 3, 16): (
        "Voters in each ward had as many votes as the ward has seats. The corporation's notices of persons elected "
        "print each candidate's votes and mark the winners; they print neither the electorate nor the turnout. In the "
        "wards where no more candidates stood than seats, the candidates were declared elected without a poll on 13 "
        "March. The wards are those drawn in 2003, which were replaced on 8 March 2013. The aldermen are elected "
        "separately, at ward meetings, and are not included."
    ),
    date(2013, 3, 21): (
        "Voters in each ward had as many votes as the ward has seats. The corporation's notices of persons elected "
        "print each candidate's votes and mark the winners; they print neither the electorate nor the turnout. In the "
        "four wards where no more candidates stood than seats, the candidates were declared elected without a poll "
        "on 20 March. The aldermen are elected separately, at ward meetings, and are not included."
    ),
    date(2017, 3, 23): (
        "Voters in each ward had as many votes as the ward has seats. The corporation's notices of persons elected "
        "print each candidate's votes, mark the winners, and give the turnout of each polled ward as a percentage; "
        "they print no electorate, so no ballot count is derived. In the six wards where no more candidates stood "
        "than seats, the candidates were declared elected without a poll on 22 March. The aldermen are elected "
        "separately, at ward meetings, and are not included."
    ),
    CITY_DAY: (
        "Voters in each ward had as many votes as the ward has seats. The corporation's results pages print each "
        "candidate's votes, mark the winners, and give the turnout as a percentage; they print no electorate, so no "
        "ballot count is derived. Wards where no more candidates stood than seats are declared elected without a poll. "
        "The aldermen are elected separately, at ward meetings, and are not included."
    ),
    date(2025, 3, 20): (
        "Voters in each ward had as many votes as the ward has seats. The corporation's results pages print each "
        "candidate's votes, mark the winners, and give the turnout as a whole percent; they print no electorate, so "
        "no ballot count is derived. Wards where no more candidates stood than seats are declared elected without a "
        "poll and print no turnout. The aldermen are elected separately, at ward meetings, and are not included."
    ),
}
CITY_PARTIES = {
    "INDEPENDENT": "independent",
    "Independent": "independent",
    "": None,
    "Labour Party": ("wikidata", "Q9630"),
    "The Labour Party Candidate": ("wikidata", "Q9630"),
    "Labour": ("wikidata", "Q9630"),
    "n/a": None,
    "Social Democratic Party": None,
    "Castle Baynard Independents": "Castle Baynard Independents Party",
    "Women's Equality Party": ("wikidata", "Q20648714"),
    "Womens Equality Party": ("wikidata", "Q20648714"),
    "Castle Baynard Independents Party": "Castle Baynard Independents Party",
    "Temple and Farringdon Together": "Temple and Farringdon Together",
}
CITY_VACANCIES = {
    (date(2009, 3, 16), "E05000005"): (
        1,
        (
            "Seven candidates were nominated for the ward's eight seats, so all seven were declared elected without "
            "a poll and one seat was left vacant. The corporation's results page records the by-election of 20 and 21 "
            "May 2009 that filled it."
        ),
    ),
}
CITY_DISAGREEMENTS = {
    (CITY_DAY, "E05009297"): (
        "The corporation's results page gives Michael Hudson 177 votes, as does Andrew Teale's Local Elections Archive "
        "Project. Democracy Club and Wikipedia give 117. The figure shown is the corporation's; Hudson was not "
        "elected under either."
    ),
}
PARTICLES = {"DE", "VAN", "VON", "DU", "DA", "DI", "LA", "LE", "DEL"}
SURNAME_WORD = re.compile(r"(?:Mc)?[A-Z'’\-]{2,}|[A-Z]")

SCILLY_CODE = "E06000053"
SCILLY_HOLDER = "Council of the Isles of Scilly"
SCILLY_PAGES = {
    "results21": (
        "https://scilly.gov.uk/node/4241",
        ".html",
        "Council election results - 6 May 2021",
    ),
    "bryher21": (
        "https://scilly.gov.uk/node/4267",
        ".html",
        "Declaration of result for Bryher election",
    ),
    "results25": (
        "https://www.scilly.gov.uk/sites/default/files/Local%20-%20May%202025.pdf",
        ".pdf",
        "Local government election results, 1 May 2025",
    ),
    "martins25": (
        "https://scilly.gov.uk/node/5624",
        ".html",
        "Result of St Martin’s election - 1 May 2025",
    ),
}
SCILLY_NOTE = "The council's results give each candidate's name and votes but no party, so none is recorded."
SCILLY_BRYHER = (
    "Nobody was nominated for Bryher on 6 May 2021, so the Returning Officer ordered a further election, to be "
    "held on 24 June if contested. Andrew Frazer was the only person nominated and was declared elected, without a "
    "poll, on 28 May."
)
SCILLY = (
    (
        date(2021, 5, 6),
        "E05011091",
        1,
        "results21",
        None,
        (("Legg", "Harry", None),),
        None,
    ),
    (
        date(2021, 5, 6),
        "E05011092",
        1,
        "results21",
        None,
        (("Tobin-Dougan", "Toby", None),),
        None,
    ),
    (
        date(2021, 5, 6),
        "E05011093",
        12,
        "results21",
        None,
        (
            ("Bedford", "Anita Heather", 470),
            ("Dean", "Timothy Patrick", 491),
            ("Francis", "Robert Dennis", 492),
            ("Grottick", "Frances Marion", 458),
            ("Guy", "Andrew Stephen", 371),
            ("Jones", "Timothy David", 272),
            ("Marcus", "Daniel Michael", 406),
            ("Mumford", "Avril Washington", 518),
            ("Nelhams", "Michael Anthony", 367),
            ("Rodger", "Alexander Euan", 419),
            ("Sims", "Stephen Mark", 419),
            ("Watt", "Stephen John", 378),
            ("Williams", "Joel Gordon", 490),
        ),
        None,
    ),
    (
        date(2021, 5, 6),
        "E05011094",
        1,
        "results21",
        None,
        (("Dorrien-Smith", "Robert", None),),
        None,
    ),
    (
        date(2021, 6, 24),
        "E05011090",
        1,
        "bryher21",
        SCILLY_BRYHER,
        (("Frazer", "Andrew Crispin", None),),
        None,
    ),
    (
        date(2025, 5, 1),
        "E05011090",
        1,
        "results25",
        None,
        (("Sandford", "Alex", None),),
        None,
    ),
    (
        date(2025, 5, 1),
        "E05011091",
        1,
        "results25",
        None,
        (("Legg", "Harry Francis", None),),
        None,
    ),
    (
        date(2025, 5, 1),
        "E05011092",
        1,
        "martins25",
        None,
        (("Tobin-Dougan", "Toby", 20), ("White", "Geoff", 66)),
        0.725,
    ),
    (
        date(2025, 5, 1),
        "E05011093",
        12,
        "results25",
        None,
        (
            ("Blackwell", "Lynn", None),
            ("Dean", "Tim", None),
            ("Francis", "Robert", None),
            ("Grottick", "Fran", None),
            ("Guy", "Andy", None),
            ("Marcus", "Daniel Michael", None),
            ("Mumford", "Avril Washington", None),
            ("Nixon", "Stuart Anthony", None),
            ("Peacock", "John", None),
            ("Rodger", "Euan", None),
            ("Sims", "Steve", None),
            ("Williams", "Joel", None),
        ),
        None,
    ),
    (
        date(2025, 5, 1),
        "E05011094",
        1,
        "results25",
        None,
        (("Dorrien-Smith", "Robert Arthur", None),),
        None,
    ),
)


def results_capture(stamp: str, path: str) -> str:
    return f"https://web.archive.org/web/{stamp}if_/{CITY_RESULTS.format(path=path)}"


def fetch() -> None:
    for *_, page in CITY_WARDS:
        url = WAYBACK + CITY_PAGE.format(page=page)
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=".html")
                break
            except httpx.TransportError:
                if attempt == 5:
                    raise
                time.sleep(30 * (attempt + 1))
        time.sleep(1.2)
    for url, archive, _ in CITY_NOTICES.values():
        raw.fetch(SOURCE, archive + url, suffix=".pdf")
    for *_, page in CITY_WARDS:
        raw.fetch(SOURCE, CITY_PAGE_2025.format(page=page), suffix=".html")
    raw.fetch(SOURCE, CITY_RESULTS_LIVE, suffix=".html")
    raw.fetch(SOURCE, ALDERMEN_ARCHIVE + ALDERMEN_PDF, suffix=".pdf")
    for stamp, path in RESULTS_CAPTURES:
        raw.fetch(SOURCE, results_capture(stamp, path), suffix=".html")
        time.sleep(1.2)
    for stamp, url in HISTORY_PAGES:
        raw.fetch(SOURCE, f"https://web.archive.org/web/{stamp}if_/{url}", suffix=".html")
        time.sleep(1.2)
    for url, suffix, _ in SCILLY_PAGES.values():
        raw.fetch(SOURCE, url, suffix=suffix)


def proper(text: str) -> str:
    words = []
    for word in text.split():
        if word in PARTICLES:
            words.append(word.lower())
            continue
        prefix = "Mc" if word.startswith("Mc") or (word.startswith("MC") and len(word) > 3 and word.isupper()) else ""
        rest = word[len(prefix) :]
        words.append(
            prefix + "-".join("'".join(part.capitalize() for part in piece.split("'")) for piece in rest.split("-"))
        )
    return " ".join(words)


def split_name(printed: str) -> tuple[str, str]:
    tokens = printed.split()
    count = 0
    while count < len(tokens) - 1 and SURNAME_WORD.fullmatch(tokens[count]):
        count += 1
    if count == 0:
        raise ValueError(f"no surname in {printed!r}")
    return proper(" ".join(tokens[:count])), " ".join(tokens[count:])


def cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
        for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
    ]


def notice_lines(path: Path) -> list[list[tuple[float, list[tuple[float, float, str]]]]]:
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pages = []
    for page in re.findall(r"<page.*?</page>", out, flags=re.DOTALL):
        lines: list[tuple[float, list[tuple[float, float, str]]]] = []
        found = re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)"[^>]*>(.*?)</word>', page)
        for x0, y, x1, text in sorted(found, key=lambda word: (float(word[1]), float(word[0]))):
            word = (float(x0), float(x1), html.unescape(text).replace("’", "'"))
            if lines and abs(lines[-1][0] - float(y)) < 3:
                lines[-1][1].append(word)
            else:
                lines.append((float(y), [word]))
        pages.append([(y, sorted(words)) for y, words in lines])
    return pages


def notices(day: date, path: Path) -> list[dict[str, object]]:
    codes: dict[str, tuple[str, str]] = {label.upper(): (code, label) for code, label, _ in CITY_WARDS}
    wards: list[dict] = []
    for lines in notice_lines(path):
        texts = [" ".join(word[2] for word in words) for _, words in lines]
        start = 0
        heading = next((m.group(1) for text in texts if (m := re.fullmatch(r"([A-Z ]+) WARD", text))), None)
        if heading is not None:
            top = next(i for i, text in enumerate(texts) if text.startswith("Candidate"))
            stated = next(text for text in texts if text.startswith("DATE OF ELECTION"))
            wards.append(
                {
                    "code": codes[heading][0],
                    "label": codes[heading][1],
                    "column": next(word[0] for word in lines[top][1] if word[2] == "Description"),
                    "uncontested": "UNCONTESTED" in stated,
                    "turnout": None,
                    "candidates": [],
                }
            )
            start = top + 1
        ward = wards[-1]
        for text in texts:
            if m := re.fullmatch(r"Turnout:\s*([\d.]+)%", text):
                ward["turnout"] = float(m.group(1))
        stop = next(
            (i for i, text in enumerate(texts) if re.search(r"denotes elected|^Dated:|^Turnout", text)), len(lines)
        )
        body = []
        for y, words in lines[start:stop]:
            side = "name"
            for x0, _, text in words:
                if NOTICE_VOTES.fullmatch(text) and x0 > ward["column"] + 90:
                    body.append((y, "votes", text))
                    continue
                if x0 > ward["column"] - 45:
                    side = "description"
                body.append((y, side, text))
        anchors = sorted({y for y, side, _ in body if side == "votes"})
        if not anchors:
            heights = sorted({y for y, _, _ in body})
            anchors = [y for i, y in enumerate(heights) if i == 0 or y - heights[i - 1] > 20]
            groups = {a: [b for b in body if max(c for c in anchors if c <= b[0] + 0.1) == a] for a in anchors}
        else:
            groups = {a: [b for b in body if min(anchors, key=lambda c: abs(c - b[0])) == a] for a in anchors}
        for anchor in anchors:
            names: list[list[str]] = []
            last_y = None
            for y, side, text in groups[anchor]:
                if side != "name":
                    continue
                if last_y is not None and abs(y - last_y) < 3:
                    names[-1].append(text)
                else:
                    names.append([text])
                last_y = y
            ward["candidates"].append(
                (
                    [" ".join(line) for line in names],
                    " ".join(text for _, side, text in groups[anchor] if side == "description"),
                    " ".join(text for _, side, text in groups[anchor] if side == "votes"),
                )
            )
    rows: list[dict[str, object]] = []
    url, archive, _ = CITY_NOTICES[day]
    for ward in wards:
        for number, (names, party, votes) in enumerate(ward["candidates"], start=1):
            if day.year == 2013:
                elected = names[0].endswith(" *")
                surname, given = names[0].removesuffix(" *"), " ".join(names[1:])
                printed = f"{given} {surname}"
                uncontested = ward["uncontested"]
                if uncontested != (votes == ""):
                    raise ValueError(f"{day} {ward['label']} {printed}: {votes!r}")
            else:
                printed = " ".join(names)
                surname, given = CITY_SURNAMES.get(printed) or (printed.split()[-1], " ".join(printed.split()[:-1]))
                elected = votes.endswith("(E)")
                uncontested = votes == "Uncontested (E)"
            if uncontested and not elected or not given or "*" in printed:
                raise ValueError(f"{day} {ward['label']}: {names} {votes!r}")
            rows.append(
                {
                    "day": day,
                    "ward_code": ward["code"],
                    "ward": ward["label"],
                    "number": number,
                    "printed": printed,
                    "surname": surname,
                    "given": given,
                    "party": party,
                    "votes": None if uncontested else int(votes.removesuffix(" (E)")),
                    "elected": elected,
                    "turnout": ward["turnout"],
                    "uncontested": uncontested,
                    "page": archive + url,
                }
            )
    return rows


def layout_notices(day: date, path: Path) -> list[dict[str, object]]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    url, archive, _ = CITY_NOTICES[day]
    wards: list[dict] = []
    reading = False
    for line in text.replace("’", "'").splitlines():
        if m := re.fullmatch(r"\s*([A-Z ]+?) WARD\s*", line):
            code, label = WARD_KEYS[m.group(1)]
            wards.append({"code": OLD_CODES[code], "label": label, "uncontested": None, "candidates": []})
        elif m := re.fullmatch(r"\s*Date of Election( \(Uncontested\))?: (.+?)\s*", line):
            stated = datetime.strptime(m.group(2), "%d %B %Y").date()
            wards[-1]["uncontested"] = m.group(1) is not None
            if stated != (date(2009, 3, 13) if m.group(1) else day):
                raise ValueError(f"{wards[-1]['label']}: dated {stated}")
        elif line.strip().startswith("CANDIDATE"):
            reading = True
        elif re.match(r"\s*(\*denotes|Dated:|Printed and Published)", line):
            reading = False
        elif reading and line.strip():
            parts = re.split(r"\s{2,}", line.strip())
            candidates = wards[-1]["candidates"]
            if re.match(r"[A-Z][A-Z' \-]*[A-Z],", line):
                votes = parts.pop() if len(parts) > 1 and parts[-1].isdigit() else None
                candidates.append([parts[0], " ".join(parts[1:]), votes])
            elif len(parts) == 1 and candidates:
                candidates[-1][0] += " " + parts[0]
            else:
                raise ValueError(f"{wards[-1]['label']}: line {line!r}")
    rows: list[dict[str, object]] = []
    for ward in wards:
        for number, (printed, party, votes) in enumerate(ward["candidates"], start=1):
            uncontested = ward["uncontested"]
            elected = uncontested or printed.endswith("*")
            surname, _, given = printed.removesuffix("*").partition(", ")
            if uncontested != (votes is None) or not given or "*" in given:
                raise ValueError(f"{day} {ward['label']}: {printed!r} {votes!r}")
            rows.append(
                {
                    "day": day,
                    "ward_code": ward["code"],
                    "ward": ward["label"],
                    "number": number,
                    "printed": printed.removesuffix("*"),
                    "surname": proper(surname),
                    "given": given.replace(",", ""),
                    "party": party,
                    "votes": None if votes is None else int(votes),
                    "elected": elected,
                    "turnout": None,
                    "uncontested": uncontested,
                    "page": archive + url,
                }
            )
    return rows


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE = re.compile(rf"(\d{{1,2}} (?:{MONTHS}) \d{{4}})")
WARD_KEYS: dict[str, tuple[str, str]] = {label.upper(): (code, label) for code, label, _ in CITY_WARDS}
WARD_KEYS["BRIDGE & BRIDGE WITHOUT"] = WARD_KEYS["BRIDGE AND BRIDGE WITHOUT"]
WARD_KEYS["CORDWINER"] = WARD_KEYS["CORDWAINER"]
WARD_KEYS["ALDERGATE"] = WARD_KEYS["ALDERSGATE"]
WARD_KEYS["BRIDGE"] = WARD_KEYS["BRIDGE AND BRIDGE WITHOUT"]
SPAN = re.compile(rf"(\d{{1,2}})(?: ({MONTHS}))? ?/ ?(\d{{1,2}} (?:{MONTHS}) \d{{4}})")
SECTION = re.compile(r"(?i)^(Aldermanic elections|Common Council(?:lor|man)? (?:By-)?elections?)\b")
SECTION_END = re.compile(
    r"^(Lord Mayor|Sheriffs|UK Parliamentary|GLA|London Region|Mayor of London|Referendum|Business Improvement|"
    r"Download results|Common Council City-wide|Election of Verderers|2017 elections|Forthcoming|Related links)"
)
UNLABELED = {date(2023, 3, 23): "a second by-election table with no ward named"}
UNLABELED_NOTE = (
    "The corporation's results page prints {what} for this day, so that contest is not loaded: the ward would "
    "have to be inferred."
)
BY_NOTE = (
    "A Common Council by-election fills one vacancy in a ward. The corporation's results page prints each "
    "candidate's votes, marks the winner, and gives the turnout where the poll was held."
)
ALDERMEN_NOTE = (
    "Each ward elects one alderman, who must be re-elected at least every six years, and the Court of Aldermen "
    "is made up of these 25. The poll is held among the ward's electors. A ward with one candidate is declared "
    "elected without a poll."
)
ALDERMEN_TITLE = "Aldermanic results for all wards (PDF)"
MULTI_NOTE = (
    "This by-election filled {seats} vacancies at once, and each voter had {seats} votes, so the candidates' votes "
    "add up to more than the ballot papers counted."
)
SDP_NOTE = (
    "The notice prints the Social Democratic Party as this candidate's description. It is not linked to a party "
    "record because the notice does not say which party of that name is meant."
)
PDF_VOTES = re.compile(r"(Uncontested \(E\)|\d+(?: \(E\))?)")


def page_events(text: str) -> list[tuple[str, str | list[str]]]:
    out: list[tuple[str, str | list[str]]] = []
    row: list[str] | None = None
    cell: list[str] | None = None
    for m in re.finditer(r"<(/?)(\w+)[^>]*>|([^<]+)", text):
        if m.group(3) is not None:
            piece = html.unescape(m.group(3)).replace("\xa0", " ")
            if cell is not None:
                cell.append(piece)
            elif piece.strip():
                out.append(("text", " ".join(piece.split())))
            continue
        closing, tag = m.group(1), m.group(2).lower()
        if tag == "tr" and not closing:
            row = []
        elif tag in ("td", "th") and not closing:
            cell = []
        elif tag in ("td", "th") and closing and row is not None and cell is not None:
            row.append(" ".join("".join(cell).split()))
            cell = None
        elif tag == "tr" and closing and row is not None:
            out.append(("row", row))
            row = None
        elif tag == "table" and not closing:
            out.append(("table", ""))
    return out


def ward_of(text: str) -> tuple[str, str] | None:
    key = re.sub(r"^(?:Ward:?|:)\s*", "", text.strip()).strip(": ")
    return WARD_KEYS.get(re.sub(r"^Ward of ", "", key, flags=re.IGNORECASE).upper())


def given_tokens(given: str) -> tuple[list[str], set[str]]:
    nick = {m.lower() for m in re.findall(r"commonly known as (\w+)", given)}
    plain = re.sub(r"\(.*?\)|, commonly known as.*", "", given)
    plain = unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z]+", plain.lower()), nick


def same_given(a: str, b: str) -> int:
    (x, nx), (y, ny) = given_tokens(a), given_tokens(b)
    if not x or not y:
        return 0
    if x == y or x[: len(y)] == y or y[: len(x)] == x or sorted(x) == sorted(y):
        return 2
    if difflib.SequenceMatcher(None, " ".join(x), " ".join(y)).ratio() >= 0.93:
        return 2
    if len(x) == len(y) and x[1:] == y[1:] and {x[0], y[0]} == {"steve", "stephen"}:
        return 2
    for short, long, nick in ((x, y, ny), (y, x, nx)):
        if len(short) == 1:
            if short[0] in nick or (len(short[0]) >= 3 and long[0].startswith(short[0])):
                return 2
            if short[0] in long:
                return 1
    return 0


def same_councilman(stood: list[tuple[str, UUID, str, str, str]]) -> list[tuple[UUID, UUID]]:
    clusters: list[list[tuple[str, UUID, str, str, str]]] = []
    for row in sorted(stood, key=lambda row: (row[0], str(row[1]))):
        day, _, given, surname, ward = row
        norm = re.sub(r"[^a-z]", "", surname.lower())
        fits = [
            cluster
            for cluster in clusters
            if re.sub(r"[^a-z]", "", cluster[0][3].lower()) == norm
            and all(member[0] != day for member in cluster)
            and any(
                match == 2 or (match == 1 and member[4] == ward)
                for member in cluster
                if (match := same_given(member[2], given))
            )
        ]
        if len(fits) == 1:
            fits[0].append(row)
        elif not fits:
            clusters.append([row])
    return [(row[1], cluster[0][1]) for cluster in clusters for row in cluster[1:] if row[1] != cluster[0][1]]


@dataclass
class Block:
    section: str | None
    day: date | None
    ward: tuple[str, str] | None
    rows: list[list[str]] = field(default_factory=list)
    spoilt: int | None = None
    ballots: int | None = None
    turnout: float | None = None
    page: str = ""


def result_blocks(path: Path) -> list[Block]:
    text = path.read_text(errors="ignore")
    start, end = text.find('id="main"'), text.find("</main>")
    ev = page_events(text[start : end if end > 0 else None])
    out: list[Block] = []
    section: str | None = None
    day: date | None = None
    ward: tuple[str, str] | None = None
    cur: Block | None = None
    i = 0
    while i < len(ev):
        kind, val = ev[i]
        i += 1
        if kind == "table":
            cur = Block(section, day, ward)
            out.append(cur)
            ward = None
            continue
        if kind == "row":
            assert isinstance(val, list)
            if cur is not None and cur.spoilt is None:
                cur.rows.append(val)
            continue
        assert isinstance(val, str)
        if SECTION_END.match(val):
            section = day = ward = cur = None
            continue
        if SECTION.match(val):
            section = "alderman" if val.lower().startswith("alder") else "by"
            day = ward = cur = None
            continue
        if section is None:
            continue
        found = DATE.search(val)
        if found and len(val) < 90 and not val.startswith("Ward"):
            day = datetime.strptime(found.group(1), "%d %B %Y").date()
            ward = ward_of(val[: found.start()].replace("by-election", "").strip(" -"))
            cur = None
            continue
        if ward_of(val):
            ward = ward_of(val)
            cur = None
            continue
        if cur is None:
            continue
        nxt = ev[i][1] if i < len(ev) and ev[i][0] == "text" else ""
        assert isinstance(nxt, str)
        if val in ("Spoilt votes", "Spoil votes") and nxt.startswith(":"):
            cur.spoilt = int(nxt.lstrip(": "))
            i += 1
        elif val.startswith("Ballot papers") and nxt.startswith(":"):
            cur.ballots = int(nxt.lstrip(": ").split()[0].removesuffix("(*counted"))
            i += 1
        elif val == "Turnout" and nxt.startswith(":"):
            cur.turnout = float(nxt.lstrip(": ").rstrip("%"))
            i += 1
    return [b for b in out if b.section is not None]


def web_rows() -> list[dict[str, object]]:
    found: dict[tuple[str | None, date | None, str], Block] = {}
    captures = [(stamp, results_capture(stamp, path)) for stamp, path in RESULTS_CAPTURES]
    captures.append(("20261100000000", CITY_RESULTS_LIVE))
    for stamp, url in captures:
        for block in result_blocks(raw.cached(SOURCE, url).path):
            rows = block.rows
            if not rows or rows[0][0] != "Name":
                continue
            if block.ward is None:
                if block.day in UNLABELED:
                    continue
                raise ValueError(f"{url}: {block.day} table has no ward")
            key = (block.section, block.day, block.ward[0])
            block.page = url
            if key in found:
                if found[key].rows != rows:
                    raise ValueError(f"{key}: captures disagree")
                continue
            found[key] = block
    out: list[dict[str, object]] = []
    for (section, day, code), block in sorted(found.items(), key=lambda item: (item[0][1], item[0][2], item[0][0])):
        assert block.ward is not None
        lines = block.rows[1:]
        uncontested = all(votes == "Uncontested (E)" for *_, votes in lines)
        for number, (printed, party, votes) in enumerate(lines, start=1):
            m = PDF_VOTES.fullmatch(votes)
            if m is None:
                raise ValueError(f"{day} {code} {printed}: {votes!r}")
            surname, _, given = printed.partition(",")
            if not given.strip():
                raise ValueError(f"{day} {code}: no comma in {printed!r}")
            out.append(
                {
                    "kind": section,
                    "day": day,
                    "ward_code": code,
                    "ward": block.ward[1],
                    "number": number,
                    "printed": printed,
                    "surname": proper(surname.strip()) if surname.isupper() else surname.strip(),
                    "given": given.strip(),
                    "party": party.strip(),
                    "votes": None if votes == "Uncontested (E)" else int(votes.removesuffix(" (E)")),
                    "elected": votes.endswith("(E)"),
                    "uncontested": uncontested,
                    "turnout": block.turnout,
                    "ballots": block.ballots,
                    "spoilt": block.spoilt,
                    "page": block.page,
                }
            )
    return out


def aldermen_rows() -> list[dict[str, object]]:
    path = raw.cached(SOURCE, ALDERMEN_ARCHIVE + ALDERMEN_PDF).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    out: list[dict[str, object]] = []
    for page in text.split("\f"):
        if not page.strip():
            continue
        lines = [line.rstrip() for line in page.splitlines()]
        heading = next(m.group(1) for line in lines if (m := re.fullmatch(r"\s*([A-Z ]+?) WARD\s*", line)))
        code, label = WARD_KEYS[heading]
        stated = next(m.group(1) for line in lines if (m := re.search(r"DATE OF ELECTION: (.+?)\]?\s*$", line)))
        day = datetime.strptime(stated.title(), "%d %B %Y").date()
        start = next(i for i, line in enumerate(lines) if "Elected" in line and "(E)" in line) + 1
        turnout = next(
            (float(m.group(1)) for line in lines if (m := re.fullmatch(r"\s*Turnout:\s*([\d.]+)%", line))), None
        )
        stop = next((i for i, line in enumerate(lines) if line.strip().startswith("Turnout:")), len(lines))
        groups: list[list[str]] = [[]]
        for line in lines[start:stop]:
            if line.strip():
                groups[-1].append(line)
            elif groups[-1]:
                groups.append([])
        entries = []
        for group in (g for g in groups if g):
            names: list[str] = []
            party = votes = None
            for line in group:
                m = re.fullmatch(r"\s*(.*?)\s{2,}(?:(\S.*?)\s{2,})?(Uncontested \(E\)|\d+(?: \(E\))?)\s*", line)
                if m:
                    names.append(m.group(1))
                    party, votes = m.group(2) or "", m.group(3)
                else:
                    names.append(line.strip())
            if votes is None:
                raise ValueError(f"{label} {day}: no votes in {group}")
            joined = ""
            for part in names:
                joined += part if joined.endswith("-") else (" " + part if joined else part)
            entries.append((joined.replace("’", "'"), party, votes))
        for number, (printed, party, votes) in enumerate(entries, start=1):
            tokens = printed.split()
            count = 0
            while count < len(tokens) - 1 and SURNAME_WORD.fullmatch(tokens[-1 - count]):
                count += 1
            if count == 0:
                raise ValueError(f"{label}: no surname in {printed!r}")
            uncontested = votes == "Uncontested (E)"
            out.append(
                {
                    "kind": "alderman",
                    "day": day,
                    "ward_code": code,
                    "ward": label,
                    "number": number,
                    "printed": printed,
                    "surname": proper(" ".join(tokens[-count:])),
                    "given": " ".join(tokens[:-count]),
                    "party": party,
                    "votes": None if uncontested else int(votes.removesuffix(" (E)")),
                    "elected": votes.endswith("(E)"),
                    "uncontested": uncontested,
                    "turnout": turnout,
                    "ballots": None,
                    "spoilt": None,
                    "page": ALDERMEN_ARCHIVE + ALDERMEN_PDF,
                }
            )
    return out


HISTORY_PARTIES = ("The Labour Party Candidate", "Labour Party", "Social Democratic Party", "Independent")
HISTORY_LINE = re.compile(r"(.+?)[\s\-–:]*(\d+)(?: votes?)?( \(E\))?(?:<|/li>)?")
HISTORY_END = re.compile(r"^(City-wide|Lord Mayor|Sheriffs|Page Content (?:[4-9]|1\d)$|On \w+day \d)")


def history_lines(path: Path) -> list[str]:
    text = path.read_text(errors="ignore")
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|</?(?:strong|b|em|i|span|font|u)\b[^>]*>", "", text)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text)).replace("\xa0", " ").replace("\u200b", "")
    out: list[str] = []
    for line in (" ".join(line.split()) for line in text.splitlines()):
        if not line:
            continue
        if out and re.fullmatch(r"[-–]?\s*[\d.]+\s*%?|%|votes?(?: \(E\))?", line):
            out[-1] = f"{out[-1]} {line}" if line != "%" else out[-1] + "%"
        else:
            out.append(line)
    return out


def given_first(head: str) -> bool:
    words = head.split()
    return len(words) > 1 and len(words[-1]) > 1 and words[-1].isupper() and not words[0].isupper()


def history_name(head: str) -> tuple[str, str, str]:
    head = head.strip(" -–:")
    party = ""
    if m := re.fullmatch(r"(.+?)\s*\((Independent)\)", head):
        head, party = m.groups()
    if given_first(head):
        words = head.split()
        count = 1
        while count < len(words) - 1 and words[-1 - count].isupper() and len(words[-1 - count]) > 1:
            count += 1
        return proper(" ".join(words[-count:])), " ".join(words[:-count]).rstrip(","), party
    if ":" in head:
        head, party = (part.strip(" -–") for part in head.split(":", 1))
    else:
        for known in HISTORY_PARTIES:
            if head.endswith(known) and ", " in head[: -len(known)]:
                head, party = head[: -len(known)].strip(" -–"), known
                break
    surname, sep, given = head.partition(", ")
    if not sep or not given or not surname[:1].isupper() or re.search(r"\d", head):
        raise ValueError(f"history name {head!r}")
    surname = re.sub(r"\s+-", "-", surname)
    given = re.sub(r"^(?:Lady|Sir|Dr) ", "", given)
    return proper(surname) if surname.isupper() else surname, given, party


def history_rows(stamp: str, url: str) -> list[dict[str, object]]:
    page = f"https://web.archive.org/web/{stamp}if_/{url}"
    lines = history_lines(raw.cached(SOURCE, page).path)
    blocks: list[dict[str, object]] = []
    section: str | None = None
    for line in lines:
        if line == "Aldermanic":
            section = "alderman"
        elif line.startswith("Common Councilman By-elections"):
            section = "by"
        elif HISTORY_END.match(line):
            section = None
        elif section is None:
            continue
        elif line.startswith("Ward of "):
            ward = ward_of(line.split(" - ")[0].removeprefix("Ward of "))
            if ward is None:
                raise ValueError(f"{page}: ward {line!r}")
            seats = 2 if line.endswith(" - Two Common Councilmen") else 1
            blocks.append({"kind": section, "ward": ward, "seats": seats, "lines": []})
        elif blocks and blocks[-1]["kind"] == section:
            block_lines = blocks[-1]["lines"]
            assert isinstance(block_lines, list)
            block_lines.append(line)
    out: list[dict[str, object]] = []
    for block in blocks:
        block_lines = block["lines"]
        ward = block["ward"]
        assert isinstance(block_lines, list) and isinstance(ward, tuple)
        found = next((m for line in block_lines if (m := DATE.search(line))), None)
        if found is None:
            raise ValueError(f"{page}: {ward[1]} has no date")
        if span := SPAN.search(found.string):
            day_to = datetime.strptime(span.group(3), "%d %B %Y").date()
            day = datetime.strptime(
                f"{span.group(1)} {span.group(2) or f'{day_to:%B}'} {day_to.year}", "%d %B %Y"
            ).date()
        else:
            day = day_to = datetime.strptime(found.group(1), "%d %B %Y").date()
        if day < WARDS_FROM:
            ward = (OLD_CODES[ward[0]], ward[1])
        seats = block["seats"]
        assert isinstance(seats, int)
        candidates: list[tuple[str, str, str, str, int | None, bool]] = []
        spoilt = ballots = cast = None
        turnout = None
        statement = ""
        uncontested = any("uncontested" in line.lower() for line in block_lines)
        after_colon = False
        for line in block_lines:
            if m := re.fullmatch(r"Total spoilt votes\s*[-–]?\s*(\d+)", line):
                spoilt = int(m.group(1))
            elif m := re.fullmatch(
                r"Number of ballot papers (?:verified and counted|counted and verified)\s*[-–]?\s*(\d+)", line
            ):
                ballots = int(m.group(1))
            elif m := re.fullmatch(r"Total votes cast\s*[-–]?\s*(\d+)", line):
                cast = int(m.group(1))
            elif m := re.fullmatch(r"Turnout\s*[-–]?\s*([\d.]+)\s*%?", line):
                turnout = float(m.group(1))
            elif re.match(
                r"(?:\([a-e]\) )?(?:Want of|Voting for|Writing|Being unmarked|Rejected in part|Number of)", line
            ):
                continue
            elif "elected" in line and not line.startswith(("A poll", "At a", "At Wardmote")):
                statement = line
                if uncontested and ", " in line.split(" was ")[0]:
                    surname, given, party = history_name(line.split(" was ")[0])
                    candidates.append((line.split(" was ")[0], surname, given, party, None, True))
            elif after_colon and uncontested and (", " in line or given_first(line)):
                surname, given, party = history_name(line)
                candidates.append((line, surname, given, party, None, True))
            elif (
                not uncontested
                and (m := HISTORY_LINE.fullmatch(line))
                and (", " in line or given_first(m.group(1).strip(" -–:")))
            ):
                surname, given, party = history_name(m.group(1))
                candidates.append((line, surname, given, party, int(m.group(2)), m.group(3) is not None))
            after_colon = "elected" in line and (line.endswith(":") or "following" in line)
        if not candidates:
            if uncontested:
                out.append({"kind": block["kind"], "day": day, "ward_code": ward[0], "ward": ward[1], "missing": True})
                continue
            raise ValueError(f"{page}: {ward[1]} {day} has no candidates")
        if not uncontested:
            ranked = sorted((c[4] or 0 for c in candidates), reverse=True)
            if not any(flag for *_, flag in candidates):
                candidates = [(*c[:5], (c[4] or 0) >= ranked[seats - 1]) for c in candidates]
            winners = [c for c in candidates if c[5]]
            if (
                len(winners) != seats
                or min(c[4] or 0 for c in winners) != ranked[seats - 1]
                or ranked[seats - 1] in ranked[seats:]
            ):
                raise ValueError(f"{page}: {ward[1]} {day} winner")
            if any(c[1].upper() not in statement.upper() for c in winners):
                raise ValueError(f"{page}: {ward[1]} {day} statement {statement!r}")
            total = sum(c[4] or 0 for c in candidates)
            if ballots is None and cast is not None and spoilt is not None and cast == total + spoilt:
                ballots = cast
        for number, (printed, surname, given, party, votes, elected) in enumerate(candidates, start=1):
            out.append(
                {
                    "kind": block["kind"],
                    "day": day,
                    "day_to": day_to,
                    "ward_code": ward[0],
                    "ward": ward[1],
                    "number": number,
                    "printed": printed,
                    "surname": surname,
                    "given": given,
                    "party": party,
                    "votes": votes,
                    "elected": elected,
                    "uncontested": uncontested,
                    "turnout": turnout,
                    "ballots": ballots,
                    "spoilt": spoilt,
                    "page": page,
                }
            )
    return out


def day_fix(row: dict[str, object]) -> tuple[date, str] | None:
    day = row["day"]
    return DAY_FIXES.get((str(row["ward_code"]), day)) if isinstance(day, date) else None


def extra_rows() -> list[dict[str, object]]:
    rows = aldermen_rows() + web_rows()
    for row in rows:
        fix = day_fix(row)
        if fix:
            row["day"], row["caveat"] = fix
    held: dict[tuple[object, object, object], list[object]] = {}
    for row in rows:
        held.setdefault((row["kind"], row["day"], row["ward_code"]), []).append(row["votes"])
    added: dict[tuple[object, object, object], list[dict[str, object]]] = {}
    for stamp, url in HISTORY_PAGES:
        found: dict[tuple[object, object, object], list[dict[str, object]]] = {}
        for row in history_rows(stamp, url):
            fix = day_fix(row)
            if fix:
                if row["day"] != row["day_to"]:
                    raise ValueError(f"{row['ward']} {row['day']}: a two-day poll to redate")
                row["day"] = row["day_to"] = fix[0]
            found.setdefault((row["kind"], row["day"], row["ward_code"]), []).append(row)
        for key, lines in found.items():
            if lines[0].get("missing"):
                if key not in held and key not in added:
                    raise ValueError(f"{key}: uncontested winner named without a surname, and not held elsewhere")
                continue
            votes = [line["votes"] for line in lines]
            known = held.get(key) or ([line["votes"] for line in added[key]] if key in added else None)
            if known is not None:
                if sorted(known, key=str) != sorted(votes, key=str):
                    raise ValueError(f"{key}: {known} against {votes} from {url}")
                continue
            added[key] = lines
    for lines in added.values():
        rows += lines
    for row in rows:
        row.setdefault("caveat", None)
        row.setdefault("day_to", row["day"])
    return rows


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day, (url, archive, _) in CITY_NOTICES.items():
        rows += (layout_notices if day < WARDS_FROM else notices)(day, raw.cached(SOURCE, archive + url).path)
    for code, label, page in CITY_WARDS:
        text = raw.cached(SOURCE, WAYBACK + CITY_PAGE.format(page=page)).path.read_text(errors="ignore")
        caption = re.search(r"<caption>(.*?)</caption>", text, flags=re.DOTALL)
        if caption is None or label not in html.unescape(caption.group(1)):
            raise ValueError(f"{label}: caption {caption and caption.group(1)!r}")
        uncontested = "(Uncontested)" in caption.group(1)
        turnout = re.search(r"Turnout:\s*([\d.]+)%", text)
        if (turnout is None) == (not uncontested):
            raise ValueError(f"{label}: turnout and uncontested disagree")
        table = text[text.index(caption.group(0)) : text.index("</table>", text.index(caption.group(0)))]
        body = [cells(row) for row in re.findall(r"<tr.*?</tr>", table, flags=re.DOTALL)]
        if body[0] != ["Candidate", "Description", "Votes"]:
            raise ValueError(f"{label}: header {body[0]}")
        for number, (printed, party, votes) in enumerate(body[1:], start=1):
            elected = uncontested or "(E)" in votes
            figure = None if uncontested else int(re.sub(r"\D", "", votes.replace("(E)", "")))
            if uncontested and votes != "Elected":
                raise ValueError(f"{label} {printed}: {votes!r}")
            surname, given = split_name(printed)
            rows.append(
                {
                    "day": CITY_DAY,
                    "ward_code": code,
                    "ward": label,
                    "number": number,
                    "printed": printed,
                    "surname": surname,
                    "given": given,
                    "party": party,
                    "votes": figure,
                    "elected": elected,
                    "turnout": None if turnout is None else float(turnout.group(1)),
                    "uncontested": uncontested,
                    "page": WAYBACK + CITY_PAGE.format(page=page),
                }
            )
    for code, label, page in CITY_WARDS:
        url = CITY_PAGE_2025.format(page=page)
        text = raw.cached(SOURCE, url).path.read_text(errors="ignore")
        caption = re.search(r"<caption>(.*?)</caption>", text, flags=re.DOTALL)
        if (
            caption is None
            or " ".join(html.unescape(re.sub(r"<[^>]+>", "", caption.group(1))).split()) != f"Ward of {label} results"
        ):
            raise ValueError(f"{label}: caption {caption and caption.group(1)!r}")
        table = text[text.index(caption.group(0)) : text.index("</table>", text.index(caption.group(0)))]
        body = [cells(row) for row in re.findall(r"<tr.*?</tr>", table, flags=re.DOTALL)]
        if body[0] != ["Candidate", "Description", "Votes"]:
            raise ValueError(f"{label}: header {body[0]}")
        turnout = re.search(r"<p><strong>Turnout (\d+)%</strong></p>", text)
        uncontested = all(votes == "Uncontested E" for *_, votes in body[1:])
        if uncontested == (turnout is not None) or not uncontested and any(v == "Uncontested E" for *_, v in body[1:]):
            raise ValueError(f"{label}: turnout and uncontested disagree")
        for number, (printed, party, votes) in enumerate(body[1:], start=1):
            surname, _, given = printed.partition(",")
            if not given.strip():
                raise ValueError(f"{label}: no comma in {printed!r}")
            m = re.fullmatch(r"(\d+)( E)?|Uncontested E", votes)
            if m is None:
                raise ValueError(f"{label} {printed}: {votes!r}")
            rows.append(
                {
                    "day": date(2025, 3, 20),
                    "ward_code": code,
                    "ward": label,
                    "number": number,
                    "printed": printed,
                    "surname": proper(surname.strip()),
                    "given": proper(given.strip()),
                    "party": party,
                    "votes": None if uncontested else int(m.group(1)),
                    "elected": uncontested or m.group(2) is not None,
                    "turnout": None if turnout is None else float(turnout.group(1)),
                    "uncontested": uncontested,
                    "page": url,
                }
            )
    frame = pl.DataFrame(rows, infer_schema_length=None)
    seats = frame.group_by("day").agg(pl.col("ward_code").n_unique().alias("wards"), pl.col("elected").sum())
    vacant = {day: sum(n for (on, _), (n, _) in CITY_VACANCIES.items() if on == day) for day in seats["day"]}
    seats = seats.with_columns(pl.col("elected") + pl.col("day").replace_strict(vacant, return_dtype=pl.UInt32))
    if seats.height != len(CITY_NOTICES) + 2 or (seats["wards"] != 25).any() or (seats["elected"] != CITY_SEATS).any():
        raise ValueError(f"wards and seats by day: {seats}")
    for ward in frame.group_by("day", "ward_code"):
        lines = ward[1]
        if lines["uncontested"][0] != lines["uncontested"].all() or lines["turnout"].is_null().any() != (
            lines["turnout"][0] is None
        ):
            raise ValueError(f"{ward[0]}: mixed rows")
        if not lines["uncontested"][0]:
            seats_won = int(lines["elected"].sum())
            ranked = lines["votes"].sort(descending=True)
            cut = ranked[seats_won - 1]
            if set(lines.filter(pl.col("elected"))["votes"]) != set(lines.filter(pl.col("votes") >= cut)["votes"]):
                raise ValueError(f"{ward[0]}: winners are not the top {seats_won}")
            if len(ranked) > seats_won and ranked[seats_won] == cut:
                raise ValueError(f"{ward[0]}: tie at the cut")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "city.parquet")
    extra = pl.DataFrame(extra_rows(), infer_schema_length=None)
    extra.write_parquet(PARSED / "extra.parquet")
    print(extra.group_by("kind", "day").agg(pl.col("ward").unique().sort(), pl.len()).sort("kind", "day"))
    print(frame.group_by("day", "uncontested").agg(pl.len(), pl.col("elected").sum()).sort("day", "uncontested"))


def page_text(key: str) -> str:
    url, suffix, _ = SCILLY_PAGES[key]
    path = raw.cached(SOURCE, url).path
    if suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
    else:
        text = html.unescape(re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", path.read_text(errors="ignore")))
    return re.sub(r"\s+", " ", text.replace("’", "'")).casefold()


def check_scilly() -> None:
    texts = {key: page_text(key) for key in SCILLY_PAGES}
    for day, code, seats, key, _, candidates, turnout in SCILLY:
        text = texts[key]
        won = sorted((votes for *_, votes in candidates if votes is not None), reverse=True)[:seats]
        for surname, given, votes in candidates:
            forms = (f"{given} {surname}", f"{surname}, {given}", f"{given} {surname.replace('-', ' ')}")
            if votes is not None:
                if not any(re.search(re.escape(form.casefold()) + r"\W+" + str(votes) + r"\b", text) for form in forms):
                    raise ValueError(f"{day} {code} {surname}: {votes} is not printed")
            elif not any(form.casefold() in text for form in forms):
                raise ValueError(f"{day} {code} {surname}, {given} is not printed")
        if turnout is not None and f"{turnout * 100:.1f}%" not in text:
            raise ValueError(f"{day} {code}: turnout {turnout} is not printed")
        if len(candidates) > seats and sum((votes or 0) >= won[-1] for *_, votes in candidates) != seats:
            raise ValueError(f"{day} {code}: tie at the cut")
        if len(candidates) < seats or (len(candidates) == seats and any(v is not None for *_, v in candidates)):
            raise ValueError(f"{day} {code}: fewer candidates than seats")


def first(conn: Connection, query: LiteralString, entity: UUID) -> str:
    row = conn.execute(query, [entity]).fetchone()
    if row is None:
        raise ValueError(f"{entity}: no row")
    return row[0]


def emit() -> Path:
    check_scilly()
    city = pl.read_parquet(PARSED / "city.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="City of London Corporation's 2009, 2013, 2017, 2022, and 2025 Common Council ward elections, its aldermanic elections and Common Council by-elections from 2007, and the Council of the Isles of Scilly's 2021 and 2025 elections, from the councils' own results",
            polity="GB",
        )

        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            if kind == "official" and entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        fptp = cs.key("gb.electoral_system", "fptp")
        block = cs.key("gb.electoral_system", "block")
        if {fptp, block} & cs.minted:
            raise ValueError("publish gb.parliament first")

        parties: dict[str, UUID | None] = {"independent": None, "": None}

        def party_of(description: str) -> tuple[UUID | None, bool]:
            target = CITY_PARTIES[description]
            if target is None:
                return None, False
            if target == "independent":
                return None, True
            if description not in parties:
                if isinstance(target, tuple):
                    found = cs.key(*target)
                    if found in cs.minted:
                        raise ValueError(f"{description} resolves to no existing party")
                else:
                    found = cs.key("gb.party", slug(target))
                    if found in cs.minted:
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, target)
                parties[description] = found
            return parties[description], False

        named: set[UUID] = set()
        stood: list[tuple[str, UUID, str, str, str]] = []

        def add_person(key: str, given: str, surname: str, link: bool = True) -> UUID:
            person = cs.key("gb.city_scilly.person", key)
            cs.add("person", id=person)
            if link:
                stood.append((key[:10], person, given, surname, key.split(":")[2]))
            if person not in named:
                named.add(person)
                name(person, f"{given} {surname}")
            return person

        city_area = cs.area("gb.lad", CITY_CODE)
        if city_area in cs.minted:
            raise ValueError("publish gb.london first: the City of London area must exist")
        city_polity = cs.key("gb.polity", CITY_CODE)
        city_body = cs.key("gb.body", CITY_CODE)
        if city_polity in cs.minted:
            cs.add("polity", id=city_polity, kind="municipality", iso_code=None)
            cs.add("polity_parent", polity_id=city_polity, parent_id=cs.polity("GB-ENG"))
            cs.add("polity_territory", polity_id=city_polity, area_id=city_area)
            name(city_polity, "City of London Corporation")
            cs.add("slug", path="gb/city-of-london", entity_id=city_polity)
            cs.add("body", id=city_body, polity_id=city_polity, kind="council")
            name(city_body, "Court of Common Council")
        scheme_known = conn.execute("select 1 from scheme where code = 'gb.ward'").fetchone()
        if scheme_known is None:
            raise ValueError("publish gb.leh first: the ward scheme must exist")

        sources: dict[str, UUID] = {}

        def source_of(url: str, publisher: str, title: str, artifact_url: str | None = None) -> UUID:
            if url not in sources:
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(raw.cached(SOURCE, artifact_url or url), sources[url])
            return sources[url]

        wards_made: set[str] = set()
        old_codes = set(OLD_CODES.values())

        def ward_entities(code: str, label: str) -> tuple[UUID, UUID]:
            area = cs.area("gb.ward", code)
            unit = cs.unit("gb.ward", code)
            if code not in wards_made:
                wards_made.add(code)
                since, until = (None, WARDS_FROM) if code in old_codes else (WARDS_FROM, None)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="gb.ward")
                    name(area, label)
                cs.add("area_code", area_id=area, code=code, valid_from=since, valid_to=until)
                cs.add("area_parent", area_id=area, parent_id=city_area, valid_from=since, valid_to=until)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=city_polity,
                        scheme_code="gb.ward",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=None,
                        valid_to=until,
                    )
                    name(unit, label)
            return area, unit

        for (day,), held in city.group_by("day", maintain_order=True):
            city_election = cs.key("gb.election", f"{day}:local:{CITY_CODE}")
            cs.add(
                "election",
                id=city_election,
                polity_id=city_polity,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(city_election, f"{day.year} City of London Corporation election")
            cs.add("slug", path=f"gb/city-of-london/{day}-local", entity_id=city_election)
            cs.add("note", entity_id=city_election, kind="context", body_md=CITY_NOTES[day])
            for code, label, page in OLD_WARDS if day < WARDS_FROM else CITY_WARDS:
                lines = held.filter(pl.col("ward_code") == code).sort("number")
                vacancy = CITY_VACANCIES.get((day, code))
                seats = int(lines["elected"].sum()) + (vacancy[0] if vacancy else 0)
                tacit = bool(lines["uncontested"][0])
                if day in CITY_NOTICES:
                    url, archive, title = CITY_NOTICES[day]
                    source = source_of(url, "City of London Corporation", title, archive + url)
                elif day.year == 2025:
                    url = CITY_PAGE_2025.format(page=page)
                    source = source_of(
                        url, "City of London Corporation", f"Ward of {label}: 2025 City elections results"
                    )
                else:
                    url = CITY_PAGE.format(page=page)
                    title = f"{label}: 2022 elections results"
                    source = source_of(url, "City of London Corporation", title, lines["page"][0])
                area, unit = ward_entities(code, label)
                contest_key = f"{day}:local:{code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=city_election,
                    kind="body_seats",
                    body_id=city_body,
                    district_area_id=area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=block,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{label} ward, City of London Corporation, {day.year}")
                cs.add("slug", path=f"gb/city-of-london/{day}-local/{slug(label)}-{code.lower()}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source)
                result_set = cs.key("gb.city_scilly.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=city_election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if (day, code) in CITY_DISAGREEMENTS:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CITY_DISAGREEMENTS[day, code])
                if vacancy:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=vacancy[1])
                for line in lines.iter_rows(named=True):
                    surname, given = line["surname"], line["given"]
                    option_key = f"{contest_key}:{line['number']}"
                    person = add_person(option_key, given, surname)
                    party, independent = party_of(line["party"])
                    option = cs.key("gb.city_scilly.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=f"{given} {surname}",
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.city_scilly.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=f"{surname}, {given}",
                        member_of_party_id=party,
                        is_independent=independent,
                    )
                    if line["votes"] is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if lines["turnout"][0] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=VOTERS,
                        count_round=0,
                        flags=SHARE_ONLY,
                        value=round(lines["turnout"][0] / 100, 4),
                    )

        extra = pl.read_parquet(PARSED / "extra.parquet")
        offices: set[str] = set()
        staged_contests: set[str] = set()
        staged_elections: set[str] = set()
        for (kind, day), held in extra.group_by("kind", "day", maintain_order=True):
            day_to = held["day_to"].max()
            assert isinstance(day, date) and isinstance(day_to, date)
            aldermanic = kind == "alderman"
            election = cs.key("gb.election", f"{day}:{'aldermanic' if aldermanic else 'by'}:{CITY_CODE}")
            staged_elections.add(f"{day}:{'aldermanic' if aldermanic else 'by'}:{CITY_CODE}")
            when = f"{day.day} {day:%B} {day.year}"
            if day_to != day:
                when = f"{day.day} {f'{day:%B} ' if day.month != day_to.month else ''}to {day_to.day} {day_to:%B} {day.year}"
            cs.add(
                "election",
                id=election,
                polity_id=city_polity,
                kind="local" if aldermanic else "by_election",
                date_from=day,
                date_to=day_to,
                status="held",
            )
            name(
                election,
                f"City of London aldermanic election, {when}"
                if aldermanic
                else f"City of London Corporation by-election, {when}",
            )
            election_path = f"gb/city-of-london/{day}-{'aldermanic' if aldermanic else 'by-election'}"
            cs.add("slug", path=election_path, entity_id=election)
            cs.add("note", entity_id=election, kind="context", body_md=ALDERMEN_NOTE if aldermanic else BY_NOTE)
            if not aldermanic and day in UNLABELED:
                cs.add("note", entity_id=election, kind="caveat", body_md=UNLABELED_NOTE.format(what=UNLABELED[day]))
            for (code,), lines in held.group_by("ward_code", maintain_order=True):
                lines = lines.sort("number")
                label = lines["ward"][0]
                page = lines["page"][0]
                if page == ALDERMEN_ARCHIVE + ALDERMEN_PDF:
                    source = source_of(ALDERMEN_PDF, "City of London Corporation", ALDERMEN_TITLE, page)
                else:
                    stamp = re.search(r"/web/(\d{8})", page)
                    title = (
                        f"Election results page, captured {datetime.strptime(stamp.group(1), '%Y%m%d'):%-d %B %Y}"
                        if stamp
                        else "Election results page"
                    )
                    source = source_of(page, "City of London Corporation", title)
                area, unit = ward_entities(code, label)
                tacit = bool(lines["uncontested"][0])
                magnitude = int(lines["elected"].sum())
                contest_key = f"{day}:{'aldermanic' if aldermanic else 'by'}:{code}"
                staged_contests.add(contest_key)
                contest = cs.key("gb.contest", contest_key)
                fields: dict[str, object] = {}
                if aldermanic:
                    seat = NEW_CODES.get(code, code)
                    office = cs.key("gb.office", f"alderman:{seat}")
                    if seat not in offices:
                        offices.add(seat)
                        if office in cs.minted:
                            cs.add("office", id=office, polity_id=city_polity, body_id=None, kind="other")
                            name(office, f"Alderman of {label}")
                    fields = {"kind": "office", "office_id": office, "body_id": None}
                else:
                    fields = {"kind": "body_seats", "body_id": city_body, "term": "remainder"}
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=fptp if magnitude == 1 else block,
                    default_result_kind="tacit" if tacit else "counted",
                    **fields,
                )
                name(
                    contest,
                    f"Alderman of {label}, {when}"
                    if aldermanic
                    else f"{label} ward by-election, City of London Corporation, {when}",
                )
                cs.add("slug", path=f"{election_path}/{slug(label)}-{code.lower()}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source)
                result_set = cs.key("gb.city_scilly.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                votes_sum = int(lines["votes"].sum()) if not tacit else 0
                spoilt, ballots = lines["spoilt"][0], lines["ballots"][0]
                reconciled = ballots is not None and spoilt is not None and votes_sum + spoilt == ballots
                if magnitude > 1:
                    reconciled = ballots is not None and max(lines["votes"].to_list()) <= ballots <= votes_sum
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=MULTI_NOTE.format(seats=magnitude))
                if ballots is not None and not reconciled:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=(
                            f"The page prints {ballots:,} ballot papers counted and verified and "
                            f"{'no total of spoilt votes' if spoilt is None else f'{spoilt:,} spoilt votes'}. "
                            f"The candidates' votes total {votes_sum:,}, so the ballot-paper count is not stored."
                        ),
                    )
                if lines["caveat"][0] is not None:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=lines["caveat"][0])
                if "Social Democratic Party" in lines["party"].to_list():
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=SDP_NOTE)
                for line in lines.iter_rows(named=True):
                    surname, given = line["surname"], line["given"]
                    option_key = f"{contest_key}:{line['number']}"
                    person = add_person(option_key, given, surname)
                    party, independent = party_of(line["party"])
                    option = cs.key("gb.city_scilly.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=f"{given} {surname}",
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.city_scilly.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=f"{surname}, {given}",
                        member_of_party_id=party,
                        is_independent=independent,
                    )
                    if line["votes"] is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                for measure, value, flags in (
                    (VOTERS, None if lines["turnout"][0] is None else round(lines["turnout"][0] / 100, 4), SHARE_ONLY),
                    (INVALID, spoilt, 0),
                    (BALLOTS, ballots if reconciled else None, 0),
                ):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=flags,
                            value=value,
                        )

        stale_reason = (
            "The Corporation's own pages date this election differently from the earlier load, so the contest is "
            "now held under its corrected date."
        )
        ward_codes = [code for code, *_ in CITY_WARDS + OLD_WARDS]
        for scheme, value, entity in conn.execute(
            """select x.scheme, x.value, x.entity_id from external_id x join entity e on e.id = x.entity_id
               where e.retired_by is null and (
                 (x.scheme = 'gb.contest' and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:(aldermanic|by):' and split_part(x.value, ':', 3) = any(%s))
                 or (x.scheme = 'gb.election' and x.value ~ ':(aldermanic|by):' and split_part(x.value, ':', 3) = %s)
                 or x.scheme = 'gb.city_scilly.person')""",
            [ward_codes, CITY_CODE],
        ).fetchall():
            if (
                (scheme == "gb.contest" and value not in staged_contests)
                or (scheme == "gb.election" and value not in staged_elections)
                or (
                    scheme == "gb.city_scilly.person"
                    and re.match(r"\d{4}-\d\d-\d\d:(aldermanic|by):", value)
                    and value.rsplit(":", 1)[0] not in staged_contests
                )
            ):
                cs.add("entity_retirement", entity_id=entity, reason=stale_reason)

        scilly_polity = cs.key("gb.polity", SCILLY_CODE)
        scilly_body = cs.key("gb.body", SCILLY_CODE)
        if {scilly_polity, scilly_body} & cs.minted:
            raise ValueError("publish gb.leap first: the Isles of Scilly council must exist")
        path = first(conn, "select path from slug where entity_id = %s order by is_current desc limit 1", scilly_polity)
        scilly_name = "Isles of Scilly Council"
        elections: dict[date, UUID] = {}
        main: dict[int, UUID] = {}
        for day in sorted({row[0] for row in SCILLY}):
            election = cs.key("gb.election", f"{day}:local:{SCILLY_CODE}")
            owner = conn.execute(
                "select c.pipeline from election e join changeset c on c.id = e.changeset_id where e.id = %s",
                [election],
            ).fetchone()
            if owner is not None and owner[0] != SOURCE:
                raise ValueError(f"{day}: the Isles of Scilly election is already published by {owner[0]}")
            elections[day] = election
            parent = None
            if day.month == 6:
                parent = main[day.year]
                label = f"{day.year} {scilly_name} election, Bryher vacancy"
            else:
                main[day.year] = election
                parent = cs.key("gb.election", f"{day}:local:gb")
                if parent in cs.minted:
                    raise ValueError(f"publish gb.leh first: {day} national election")
                label = f"{day.year} {scilly_name} election"
            cs.add(
                "election",
                id=election,
                polity_id=scilly_polity,
                parent_id=parent,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, label)
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
        for day, code, seats, key, note, candidates, turnout in SCILLY:
            url, _, title = SCILLY_PAGES[key]
            source = source_of(url, SCILLY_HOLDER, title)
            area = cs.area("gb.ward", code)
            unit = cs.unit("gb.ward", code)
            if {area, unit} & cs.minted:
                raise ValueError(f"{code}: the Isles of Scilly ward must exist")
            ward_name = first(conn, "select text from name where entity_id = %s and is_preferred limit 1", area)
            tacit = all(votes is None for *_, votes in candidates)
            contest_key = f"{day}:local:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind="body_seats",
                body_id=scilly_body,
                district_area_id=area,
                magnitude=seats,
                round_number=1,
                electoral_system_id=fptp if seats == 1 else block,
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{ward_name} ward, {scilly_name}, {day.year}")
            cs.add("slug", path=f"{path}/{day}-local/{slug(ward_name)}-{code.lower()}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            result_set = cs.key("gb.city_scilly.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[day],
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=note or SCILLY_NOTE)
            won = sorted((votes for *_, votes in candidates if votes is not None), reverse=True)[:seats]
            for number, (surname, given, votes) in enumerate(candidates, start=1):
                option_key = f"{contest_key}:{number}"
                person = add_person(option_key, given, surname)
                option = cs.key("gb.city_scilly.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_label=f"{given} {surname}",
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.city_scilly.candidacy", option_key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=f"{surname}, {given}",
                    member_of_party_id=None,
                    is_independent=False,
                )
                if votes is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                if votes is None or votes >= won[-1]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if turnout is not None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=VOTERS,
                    count_round=0,
                    flags=SHARE_ONLY,
                    value=turnout,
                )
            if key == "martins25":
                other = source_of(SCILLY_PAGES["results25"][0], SCILLY_HOLDER, SCILLY_PAGES["results25"][2])
                cs.add("citation", entity_id=contest, source_id=other)
        for person, into in same_councilman(stood):
            cs.merge(person, into)
        return cs.write()
