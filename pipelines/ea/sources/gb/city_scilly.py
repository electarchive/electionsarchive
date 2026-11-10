import html
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import LiteralString
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, Connection, connect

SOURCE = "gb.city_scilly"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
VOTES, VOTERS = 1, 14
SHARE_ONLY = 4
CITY_DAY = date(2022, 3, 24)
CITY_NOTICES = {
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
CITY_SEATS = 100
CITY_NOTES = {
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
}
CITY_PARTIES = {
    "INDEPENDENT": "independent",
    "Independent": "independent",
    "": None,
    "Labour Party": ("wikidata", "Q9630"),
    "The Labour Party Candidate": ("wikidata", "Q9630"),
    "Women's Equality Party": ("wikidata", "Q20648714"),
    "Womens Equality Party": ("wikidata", "Q20648714"),
    "Castle Baynard Independents Party": "Castle Baynard Independents Party",
    "Temple and Farringdon Together": "Temple and Farringdon Together",
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
    for url, suffix, _ in SCILLY_PAGES.values():
        raw.fetch(SOURCE, url, suffix=suffix)


def proper(text: str) -> str:
    words = []
    for word in text.split():
        if word in PARTICLES:
            words.append(word.lower())
            continue
        prefix = "Mc" if word.startswith("Mc") else ""
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


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day, (url, archive, _) in CITY_NOTICES.items():
        rows += notices(day, raw.cached(SOURCE, archive + url).path)
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
    frame = pl.DataFrame(rows, infer_schema_length=None)
    seats = frame.group_by("day").agg(pl.col("ward_code").n_unique().alias("wards"), pl.col("elected").sum())
    if seats.height != 3 or (seats["wards"] != 25).any() or (seats["elected"] != CITY_SEATS).any():
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
            summary="City of London Corporation's 2013, 2017, and 2022 Common Council ward elections and the Council of the Isles of Scilly's 2021 and 2025 elections, from the councils' own results",
            polity="GB",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
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

        def add_person(key: str, given: str, surname: str) -> UUID:
            person = cs.key("gb.city_scilly.person", key)
            cs.add("person", id=person)
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
            for code, label, page in CITY_WARDS:
                lines = held.filter(pl.col("ward_code") == code).sort("number")
                seats = int(lines["elected"].sum())
                tacit = bool(lines["uncontested"][0])
                if day in CITY_NOTICES:
                    url, archive, title = CITY_NOTICES[day]
                    source = source_of(url, "City of London Corporation", title, archive + url)
                else:
                    url = CITY_PAGE.format(page=page)
                    title = f"{label}: 2022 elections results"
                    source = source_of(url, "City of London Corporation", title, lines["page"][0])
                area = cs.area("gb.ward", code)
                unit = cs.unit("gb.ward", code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="gb.ward")
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=city_area, valid_from=None, valid_to=None)
                    name(area, label)
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
                    )
                    name(unit, label)
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
        return cs.write()
