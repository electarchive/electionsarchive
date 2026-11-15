import csv
import io
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import house_books
from ea.store import DATA, connect

SOURCE = "us.nh_pres_manual"
RAW = "us.nh_manual"
VERSION = "1"
PUBLISHER = "New Hampshire Secretary of State"
LICENSE = "Public domain"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
DPI = 300
COUNTIES = (
    "Belknap", "Carroll", "Cheshire", "Coos", "Grafton", "Hillsborough", "Merrimack", "Rockingham", "Strafford",
    "Sullivan",
)  # fmt: skip
ITEMS = {year: (f"manualforgeneral{(year - 1886) // 2}newh", day) for year, day in {
    1952: date(1952, 3, 11),
    1956: date(1956, 3, 13),
    1960: date(1960, 3, 8),
    1964: date(1964, 3, 10),
    1968: date(1968, 3, 12),
    1972: date(1972, 3, 7),
    1976: date(1976, 2, 24),
    1980: date(1980, 2, 26),
    1984: date(1984, 2, 28),
    1988: date(1988, 2, 16),
}.items()}  # fmt: skip
TABLES: dict[int, list[tuple[str, int, int, str]]] = {
    1952: [
        ("R", 333, 1, "Eisenhower, Schneider, Stassen, Taft, MacArthur, Scattering"),
        ("D", 452, 0, "Kefauver, Truman, Farley, MacArthur|R, Stevenson, Scattering"),
    ],
    1956: [
        ("R", 353, 1, "Eisenhower, Scattering"),
        ("D", 441, 0, "Kefauver, Stevenson, Scattering"),
    ],
    1960: [
        ("R", 314, 1, "Bridges, Fisher, Kennedy|D, Lodge, Nixon, Rockefeller, Scattering"),
        ("D", 414, 0, "Fisher, Kennedy, Nixon|R, Stevenson, Symington, Scattering"),
    ],
    1964: [
        ("R", 312, 1, "Goldwater, LePage, Rockefeller, Smith, Stassen, Lodge, Nixon, Scranton, Scattering"),
        (
            "D",
            460,
            0,
            "Johnson, Kennedy, Lodge|R, Nixon|R, Goldwater|R, Stevenson, Humphreys, Rockefeller|R, Zuber, Scattering",
        ),
    ],
    1968: [
        ("R", 350, 1, "Coy, DuMont, Evans, Hoover, Nixon, Romney, Stassen, Stone, Watumull"),
        ("R", 351, 0, "Rockefeller, McCarthy|D, Fisher|D, Johnson|D, Reagan, Scattering"),
        ("D", 475, 0, "Crommelin, Gordon, Lee, McCarthy, Johnson, Nixon|R"),
        ("D", 475, 1, "Fisher, Kennedy, Rockefeller|R, Wallace|AI, Scattering"),
    ],
    1972: [
        ("R", 336, 0, "Ashbrook, McCloskey, Nixon, Paulsen, Kennedy|D, Mills|D"),
        ("R", 337, 0, "Wallace|D, Coll|D, Hartke|D, McGovern|D, Muskie|D, Yorty|D, Scattering"),
        ("D", 360, 0, "Coll, Hartke, McGovern, Muskie, Yorty, Ashbrook|R, McCloskey|R"),
        ("D", 361, 0, "Nixon|R, Paulsen|R, Kennedy, Mills, Wallace, Humphrey, Jackson, Scattering"),
    ],
    1976: [
        ("R", 334, 1, "Ford, Reagan, Bayh|D, Carter|D, Harris|D, Udall|D"),
        ("D", 335, 0, "Arnold, Bayh, Blessitt, Bona, Carter, Clegg"),
        ("D", 335, 1, "Harris, Kelleher, Loewenberg, McCormack, Sanford, Schechter"),
        ("D", 336, 0, "Shriver, Udall, Humphrey, Jackson, Ford|R, Reagan|R, Wallace"),
    ],
    1980: [
        (
            "R",
            52,
            0,
            "Anderson, Baker, Bush, Connally, Crane, Dole, Reagan, Brown|D, Carter|D, Kay|D, Kennedy|D, LaRouche|D",
        ),
        (
            "D",
            52,
            1,
            "Brown, Carter, Kay, Kennedy, LaRouche, Anderson|R, Baker|R, Bush|R, Connally|R, Crane|R, Dole|R, Reagan|R",
        ),
    ],
    1984: [
        ("D", 74, 0, "Askew, Bagley, Beckman, Buchanan, Caplette, Clendenan"),
        ("D", 74, 1, "Cranston, Glenn, Hart, Hollings, Jackson, Kay"),
        ("D", 75, 0, "King, Kirk, Koczak, Kreml, McGovern, Mondale"),
        ("D", 75, 1, "O'Donnell, Rudnicki, Sagan, Willis, Reagan|R"),
        ("R", 98, 0, "Arnold, Fernandez, Kelley, Reagan, Stassen"),
        ("R", 98, 1, "Askew|D, Beckman|D, Buchanan|D, Cranston|D, Glenn|D"),
        ("R", 99, 0, "Hart|D, Hollings|D, Jackson|D, Kay|D, McGovern|D"),
        ("R", 99, 1, "Mondale|D, Sagan|D, Willis|D"),
    ],
    1988: [
        ("R", 144, 0, "Bush, Conley, Dole, Drucker, duPont, Haig, Horrigan, Kemp, Levinson"),
        ("R", 144, 1, "Rachner, Robertson, Stassen, Babbitt|D, Dukakis|D, Duke|D, DuPont|D, Gephardt|D"),
        ("R", 144, 2, "Gore|D, Hart|D, Jackson|D, King|D, LaRouche|D, Marra|D, Roy|D, Simon|D"),
        ("D", 163, 0, "Babbitt, Dennerll, DiDonato, Dukakis, Duke, DuPont, Gephardt, Gore"),
        ("D", 163, 1, "Hart, Jackson, King, Kirk, Koczak, LaRouche, Lock, Marra"),
        ("D", 163, 2, "Martin-Trigona, O'Donnell, Roy, Sagan, Simon, Thomas, Thorpe, Van Petten"),
        ("D", 164, 0, "Zucker, Bush|R, Dole|R, duPont|R, Haig|R, Horrigan|R, Kemp|R, Robertson|R, Stassen|R"),
    ],
}


def url(year: int) -> str:
    item = ITEMS[year][0]
    return f"https://archive.org/download/{item}/{item}_bw.pdf"


def fetch() -> None:
    for year in ITEMS:
        try:
            raw.cached(RAW, url(year))
        except LookupError:
            raw.fetch(RAW, url(year), suffix=".pdf")


def image_of(year: int, page: int) -> Path:
    book = raw.cached(RAW, url(year))
    image = PAGES / f"{book.sha256}-{page}-{DPI}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        house_books._render(book, page, DPI, image, False)
    return image


def words_of(year: int, page: int) -> tuple[list[tuple[str, int, int, int, int]], int, int]:
    image = image_of(year, page)
    cache = image.with_suffix(".tsv")
    if not cache.exists():
        done = subprocess.run(
            ["tesseract", str(image), "stdout", "--psm", "6", "tsv"], capture_output=True, check=True, text=True
        )
        cache.write_text(done.stdout)
    found = []
    for row in csv.DictReader(io.StringIO(cache.read_text()), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row["text"] or "").strip()
        if text and row["level"] == "5":
            left, top = int(row["left"]), int(row["top"])
            found.append((text, left, top, left + int(row["width"]), top + int(row["height"])))
    with Image.open(image) as opened:
        width, height = opened.size
    return found, width, height


LINES = Path(__file__).with_suffix(".tsv")
PARTIES = {"D": ("Democratic", "democratic-party"), "R": ("Republican", "republican-party")}
FIRST = {1952: date(1952, 3, 11), 1956: date(1956, 3, 13), 1960: date(1960, 3, 8), 1964: date(1964, 3, 10),
         1968: date(1968, 3, 12), 1972: date(1972, 3, 7), 1976: date(1976, 1, 19), 1980: date(1980, 1, 21),
         1984: date(1984, 2, 20), 1988: date(1988, 2, 8)}  # fmt: skip
LAST = {1952: date(1952, 6, 3), 1956: date(1956, 6, 5), 1960: date(1960, 6, 7), 1964: date(1964, 6, 2),
        1968: date(1968, 6, 18), 1972: date(1972, 6, 20), 1976: date(1976, 6, 8), 1980: date(1980, 6, 3),
        1984: date(1984, 6, 12), 1988: date(1988, 6, 14)}  # fmt: skip
NAMES = {
    "Eisenhower": "Dwight D. Eisenhower", "Schneider": "William R. Schneider", "Stassen": "Harold E. Stassen",
    "Taft": "Robert A. Taft", "MacArthur": "Douglas MacArthur", "Kefauver": "Estes Kefauver",
    "Truman": "Harry S. Truman", "Stevenson": "Adlai E. Stevenson", "Bridges": "Styles Bridges",
    "Fisher": "Paul C. Fisher", "Lodge": "Henry Cabot Lodge Jr.", "Nixon": "Richard M. Nixon",
    "Rockefeller": "Nelson A. Rockefeller", "Symington": "Stuart Symington", "Goldwater": "Barry M. Goldwater",
    "Smith": "Margaret Chase Smith", "Scranton": "William W. Scranton", "Johnson": "Lyndon B. Johnson",
    "Romney": "George W. Romney", "McCarthy": "Eugene J. McCarthy", "Reagan": "Ronald Reagan",
    "Wallace": "George C. Wallace", "Ashbrook": "John M. Ashbrook", "McCloskey": "Paul N. McCloskey Jr.",
    "Paulsen": "Pat Paulsen", "Mills": "Wilbur D. Mills", "Coll": "Edward T. Coll", "Hartke": "Vance Hartke",
    "McGovern": "George McGovern", "Muskie": "Edmund S. Muskie", "Yorty": "Samuel W. Yorty",
    "Humphrey": "Hubert H. Humphrey", "Jackson": "Henry M. Jackson", "Ford": "Gerald R. Ford",
    "Bayh": "Birch Bayh", "Carter": "Jimmy Carter", "Harris": "Fred R. Harris", "Udall": "Morris K. Udall",
    "Shriver": "Sargent Shriver", "Sanford": "Terry Sanford", "McCormack": "Ellen McCormack",
    "Anderson": "John B. Anderson", "Baker": "Howard H. Baker", "Bush": "George H. W. Bush",
    "Connally": "John B. Connally", "Crane": "Philip M. Crane", "Dole": "Robert Dole",
    "Brown": "Edmund G. Brown Jr.", "Kay": "Richard B. Kay", "LaRouche": "Lyndon H. LaRouche Jr.",
    "Kelley": "David M. Kelley", "Fernandez": "Ben Fernandez", "Hart": "Gary Hart", "Mondale": "Walter F. Mondale",
    "Glenn": "John Glenn", "Hollings": "Ernest F. Hollings", "Cranston": "Alan M. Cranston",
    "Askew": "Reubin O'Donovan Askew", "Beckman": "Martin J. Beckman", "Sagan": "Cyril E. Sagan",
    "Buchanan": "Walter R. Buchanan", "Willis": "Gerald Willis", "Koczak": "Stephen A. Koczak",
    "O'Donnell": "Edward T. O'Donnell Jr.", "King": "William King", "Kreml": "William P. Kreml",
    "Bagley": "Hugh G. Bagley", "Kirk": "Claude R. Kirk Jr.", "Rudnicki": "Chester M. Rudnicki",
    "Clendenan": "Roy J. Clendenan", "Caplette": "Raymond J. Caplette", "Conley": "Paul B. Conley",
    "Drucker": "Robert F. Drucker", "duPont": "Pete du Pont", "Haig": "Alexander M. Haig",
    "Horrigan": "William Horrigan Jr.", "Kemp": "Jack Kemp", "Levinson": "Michael S. Levinson",
    "Rachner": "Mary Jane Rachner", "Robertson": "Pat Robertson", "Babbitt": "Bruce Babbitt",
    "Dukakis": "Michael S. Dukakis", "Duke": "David E. Duke", "DuPont": "William J. DuPont",
    "Gephardt": "Dick Gephardt", "Gore": "Al Gore", "Marra": "William A. Marra", "Roy": "Conrad W. Roy",
    "Simon": "Paul Simon", "Dennerll": "Norbert G. Dennerll Jr.", "DiDonato": "Florenzo Di Donato",
    "Lock": "Stanley Lock", "Martin-Trigona": "Anthony R. Martin-Trigona", "Thomas": "Frank L. Thomas",
    "Thorpe": "Osie Thorpe", "Van Petten": "A. A. Van Petten", "Zucker": "Irwin Zucker",
}  # fmt: skip
TERMS = {
    ("Kennedy", 1960): "John F. Kennedy", ("Kennedy", 1964): "Robert F. Kennedy",
    ("Kennedy", 1968): "Robert F. Kennedy", ("Kennedy", 1972): "Edward M. Kennedy",
    ("Kennedy", 1980): "Edward M. Kennedy", ("Jackson", 1972): "Henry M. Jackson",
    ("Jackson", 1976): "Henry M. Jackson", ("Jackson", 1984): "Jesse L. Jackson", ("Jackson", 1988): "Jesse Jackson",
    ("Arnold", 1976): "Arnold", ("Arnold", 1984): "Gary Richard Arnold", ("Kirk", 1988): "Claude Kirk",
}  # fmt: skip


def full_name(year: int, printed: str) -> str:
    return TERMS.get((printed, year), NAMES.get(printed, printed))


def label_of(text: str) -> str | None:
    letters = re.sub(r"[^a-z]", "", text.lower())
    if len(letters) < 3:
        return None
    for name, label in [*((county.lower(), county) for county in COUNTIES), ("totals", "TOTAL")]:
        if letters.startswith(name[:4]) or (len(letters) >= 4 and 0 < name.find(letters) <= 3):
            return label
    return None


def row_words(year: int, page: int, index: int) -> dict[str, list[tuple[str, int, int, int, int]]]:
    found, width, _ = words_of(year, page)
    lines: dict[int, list[tuple[str, int, int, int, int]]] = {}
    for word in sorted(found, key=lambda w: (w[2] + w[4]) / 2):
        middle = (word[2] + word[4]) / 2
        key = next((k for k in lines if abs(k - middle) < 14), None)
        lines.setdefault(int(middle) if key is None else key, []).append(word)
    blocks: list[dict[str, list[tuple[str, int, int, int, int]]]] = [{}]
    for key in sorted(lines):
        line = sorted(lines[key], key=lambda w: w[1])
        name = label_of(line[0][0]) if line[0][1] < width * 0.3 else None
        if name is None:
            continue
        blocks[-1][name] = [w for w in line[1:] if w[1] > width * 0.2]
        if name == "TOTAL":
            blocks.append({})
    blocks = [block for block in blocks if len(block) == len(COUNTIES) + 1]
    return blocks[index] if index < len(blocks) else {}


def parse() -> None:
    rows: list[dict[str, object]] = []
    with LINES.open() as handle:
        printed = [line.rstrip("\n").split("\t") for line in handle]
    for year, tables in TABLES.items():
        for party, page, index, header in tables:
            columns = [column.strip() for column in header.split(",")]
            lines = {row[4]: row[5].split() for row in printed if row[:4] == [str(year), party, str(page), str(index)]}
            if sorted(lines) != sorted([*COUNTIES, "TOTAL"]) or {len(v) for v in lines.values()} != {len(columns)}:
                raise ValueError(f"{year} {party} p{page} t{index}: transcription does not match the header")
            located = row_words(year, page, index)
            _, width, height = words_of(year, page)
            for county, values in lines.items():
                words = located.get(county, [])
                cursor = 0
                for column, value in zip(columns, values, strict=True):
                    if value == ".":
                        continue
                    votes = 0 if value == "-" else int(value)
                    box = None
                    for position in range(cursor, len(words)):
                        digits = re.sub(r"[^\d]", "", words[position][0])
                        if digits == str(votes) or (votes == 0 and re.fullmatch(r"[—–\-_~]+", words[position][0])):
                            _, x0, y0, x1, y1 = words[position]
                            box = [x0 / width, y0 / height, x1 / width, y1 / height]
                            cursor = position + 1
                            break
                    name, _, own = column.partition("|")
                    rows.append(
                        {
                            "year": year,
                            "party": party,
                            "page": page,
                            "column": name,
                            "own": own or party,
                            "county": county,
                            "votes": votes,
                            "box": box,
                        }
                    )
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    sums = frame.group_by("year", "party", "column", maintain_order=True).agg(
        counted=pl.col("votes").filter(pl.col("county") != "TOTAL").sum(),
        printed=pl.col("votes").filter(pl.col("county") == "TOTAL").sum(),
        located=pl.col("box").is_not_null().mean(),
    )
    print(sums.filter(pl.col("counted") != pl.col("printed")))
    print(f"{len(frame)} cells, {frame['box'].is_not_null().sum()} located")


CAVEATS = {
    (1964, "R", "Scattering"): (483, 383),
    (1968, "D", "McCarthy"): (23269, 23263),
    (1968, "D", "Scattering"): (152, 154),
    (1976, "D", "Humphrey"): (4296, 4596),
    (1976, "D", "Wallace"): (1016, 1061),
    (1980, "D", "Brown"): (10686, 10706),
    (1988, "R", "Levinson"): (33, 43),
}


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's presidential primaries by county from the Manual for the General Court, "
            + ", ".join(str(year) for year in sorted(ITEMS)),
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        ((state_unit, state_area),) = conn.execute(
            """select u.id, u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-NH'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = dict(
            conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '33%%'"""
            ).fetchall()
        )
        PAGES.mkdir(parents=True, exist_ok=True)
        for year in sorted(ITEMS):
            item, day = ITEMS[year]
            lines = frame.filter(pl.col("year") == year)
            title = f"Manual for the General Court, {year + 1}"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=f"https://archive.org/details/{item}",
                license=LICENSE,
            )
            book = raw.cached(RAW, url(year))
            artifact = cs.artifact(book, source)
            pages: dict[int, UUID] = {}
            for (number,) in lines.select("page").unique().sort("page").iter_rows():
                image = PAGES / f"{book.sha256}-{number}.png"
                if not image.exists():
                    house_books._render(book, number, 100, image, False)
                with Image.open(image) as rendered:
                    width, height = rendered.size
                pages[number] = cs.page(artifact, number)
                cs.add(
                    "source_page",
                    id=pages[number],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            election = cs.key("us.election", f"{day}:NH:primary")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity("US-NH"),
                    kind="primary",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day:%B} {day.day}, {year} New Hampshire primary election")
                cs.add("slug", path=f"us/nh/{day}-primary", entity_id=election)
            for (owner,), block in lines.group_by("party", maintain_order=True):
                label, scope = PARTIES[str(owner)]
                word = label.lower()
                start = min(FIRST[year], day)
                umbrella = cs.key("us.election", f"{start}:{word}-presidential-primaries")
                national = cs.key("us.contest", f"{start}:president:{word}")
                if umbrella in cs.minted:
                    cs.add(
                        "election",
                        id=umbrella,
                        polity_id=cs.polity("US"),
                        kind="primary",
                        date_from=start,
                        date_to=LAST[year],
                        status="held",
                    )
                    name(umbrella, f"{year} {label} Party presidential primaries")
                    cs.add("slug", path=f"us/{start}-{word}-presidential-primaries", entity_id=umbrella)
                if national in cs.minted:
                    cs.add(
                        "contest",
                        id=national,
                        election_id=umbrella,
                        kind="delegate_allocation",
                        office_id=cs.key("us.office", "president"),
                        party_scope_id=cs.key("us.party", scope),
                        round_number=1,
                        default_result_kind="counted",
                    )
                    name(national, f"{year} {label} presidential nomination")
                    cs.add("slug", path=f"us/{start}-{word}-presidential-primaries/president", entity_id=national)
                contest_key = f"{day}:NH:president:{slug(label)}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="delegate_allocation",
                    office_id=cs.key("us.office", "president"),
                    district_area_id=state_area,
                    party_scope_id=cs.key("us.party", scope),
                    round_number=1,
                    allocation_parent_id=national,
                    default_result_kind="counted",
                )
                name(contest, f"{year} New Hampshire {label} presidential primary")
                cs.add("slug", path=f"us/nh/{day}-primary/president-{slug(label)}", entity_id=contest)
                sets = {
                    grain: cs.key(f"{SOURCE}.result_set", f"{contest_key}:{grain}") for grain in ("state", "county")
                }
                for grain, result_set in sets.items():
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=grain == "county",
                        completeness=1,
                    )
                for (when, party, column), (counted, printed) in CAVEATS.items():
                    if when == year and party == owner:
                        cs.add(
                            "note",
                            entity_id=sets["county"],
                            kind="caveat",
                            body_md=f"The Manual prints {column}'s statewide total as {printed:,}, while its county "
                            f"lines sum to {counted:,}; both are stored as printed.",
                        )
                totals: dict[str, int] = {}
                for (column, own), cells in block.group_by("column", "own", maintain_order=True):
                    printed_name = str(column)
                    kind = "write_in" if printed_name == "Scattering" else "candidate"
                    display = printed_name if kind == "write_in" else full_name(year, printed_name)
                    option_key = f"{contest_key}:{kind}:{slug(display)}"
                    option = cs.key("us.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind=kind,
                        party_id=cs.key("us.party", PARTIES[str(own)][1])
                        if own in PARTIES and kind == "candidate"
                        else None,
                        ballot_label=display,
                    )
                    if kind == "candidate":
                        person = cs.key(f"{SOURCE}.person", f"NH:{year}:{slug(display)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, display)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=display,
                            is_independent=False,
                        )
                    for county, votes, page, box in cells.select("county", "votes", "page", "box").iter_rows():
                        grain = "state" if county == "TOTAL" else "county"
                        unit = state_unit if county == "TOTAL" else counties[county]
                        if grain == "county":
                            totals[county] = totals.get(county, 0) + votes
                        fact = {
                            "result_set_id": sets[grain],
                            "unit_id": unit,
                            "option_id": option,
                            "channel": 0,
                            "measure": 1,
                            "count_round": 0,
                        }
                        cs.add("result_value", **fact, flags=house_books.DIGITIZED, value=votes)
                        if box is not None:
                            cs.add(
                                "value_locator",
                                **fact,
                                page_id=pages[page],
                                source_id=None,
                                x0=box[0],
                                y0=box[1],
                                x1=box[2],
                                y1=box[3],
                            )
                printed = int(block.filter(pl.col("county") == "TOTAL")["votes"].sum())
                for grain, unit, value in [
                    ("state", state_unit, printed),
                    *(("county", counties[county], votes) for county, votes in totals.items()),
                ]:
                    cs.add(
                        "result_value",
                        result_set_id=sets[grain],
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=4,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
