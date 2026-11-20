"""The European Parliament election in Romania of 25 November 2007, the country's first, from the Central Electoral
Bureau's national tally.

The Bureau's 2007 site is gone. Its national tally (form T1 PE with the statistics sheet L3 PE, signed on 27
November 2007) survives as a scanned four-page PDF: page 1 and 2 give the voters on the lists, the voters who took
part, the valid and null votes, and the valid votes of each of the 14 lists and candidates, ordered by votes; page 3
lists the polling stations counted in each county; page 4 repeats the totals with the voters by kind of list. No
county results or seat allocation survive from the Bureau, so this is the national count only. Every number keeps its
page and bounding box.
"""

import subprocess
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.ro import ep
from ea.sources.ro.aep import ALL, CAST, INVALID, REGISTERED, VALID, VOTES, canon
from ea.store import DATA

SOURCE = "ro.ep2007"
DAY = date(2007, 11, 25)
ROMANIA = ep.ROMANIA
ORIGINAL = "http://www.bec2007pe.ro/documente/REZULT_27_11_2007_ORA13.pdf"
URL = f"https://web.archive.org/web/2007id_/{ORIGINAL}"
REGISTRY = f"{SOURCE}/registry"
KEY = f"{SOURCE}/{DAY}"
SEATS = 35
DIGITIZED = 8
DPI = 150
PAGES = DATA / "pages" / SOURCE
# The member-state unit and area another pipeline holds for Romania's European Parliament results.
MEMBER_STATE_UNIT = "591d8a43-46dd-5029-b1c7-40987f8d1aca"

# Each number the tally prints, as read from the scan: page, value, and box (x0, y0, x1, y1 as fractions of the page).
TOTALS = {
    REGISTERED: (1, 18224597, (0.6531, 0.2573, 0.7184, 0.2667)),
    CAST: (1, 5370171, (0.6603, 0.2844, 0.7168, 0.2939)),
    VALID: (1, 5122226, (0.6603, 0.3139, 0.7184, 0.3233)),
    INVALID: (1, 246555, (0.6687, 0.3393, 0.7184, 0.3485)),
}
# The lists and candidates in the order the tally prints them (by valid votes): label, abbreviation, independent?,
# page, votes, and box.
LINES = [
    ("PARTIDUL DEMOCRAT", "PD", False, 1, 1476105, (0.5843, 0.5380, 0.6420, 0.5472)),
    ("PARTIDUL SOCIAL DEMOCRAT", "PSD", False, 1, 1184018, (0.5839, 0.5649, 0.6420, 0.5740)),
    ("PARTIDUL NAȚIONAL LIBERAL", "PNL", False, 1, 688859, (0.5915, 0.5918, 0.6420, 0.6009)),
    ("PARTIDUL LIBERAL DEMOCRAT", "PLD", False, 1, 398901, (0.5915, 0.6184, 0.6396, 0.6275)),
    ("UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA", "UDMR", False, 1, 282929, (0.5915, 0.6452, 0.6416, 0.6544)),
    ("PARTIDUL NOUA GENERAȚIE CREȘTIN DEMOCRAT", "PNG-CD", False, 1, 248863, (0.5915, 0.6827, 0.6412, 0.6918)),
    ("PARTIDUL ROMÂNIA MARE", "PRM", False, 1, 212596, (0.5911, 0.7198, 0.6408, 0.7290)),
    ("TÖKÉS LADISLAU", None, True, 1, 176533, (0.5915, 0.7470, 0.6408, 0.7561)),
    ("PARTIDUL CONSERVATOR", "PC", False, 1, 150385, (0.5911, 0.7739, 0.6408, 0.7830)),
    ("PARTIDUL INIȚIATIVA NAȚIONALĂ", "PIN", False, 1, 124829, (0.5911, 0.8007, 0.6408, 0.8099)),
    (
        "PARTIDUL NAȚIONAL ȚĂRĂNESC CREȘTIN DEMOCRAT",
        "PNȚCD",
        False,
        1,
        71001,
        (0.5982, 0.8276, 0.6388, 0.8368),
    ),
    ("PARTIDA ROMILOR PRO-EUROPA", None, False, 2, 58903, (0.5991, 0.3531, 0.6411, 0.3622)),
    ("PARTIDUL ALIANȚA SOCIALISTĂ", "PAS", False, 2, 28484, (0.5995, 0.3799, 0.6411, 0.3891)),
    ("PARTIDUL VERDE", None, False, 2, 19820, (0.5995, 0.4065, 0.6407, 0.4157)),
]
# Parties the 2009 and 2014 European Parliament pipeline already holds under the same name.
SHARED = {
    canon(label)
    for label in (
        "PARTIDUL NAȚIONAL LIBERAL",
        "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA",
        "PARTIDUL ROMÂNIA MARE",
        "PARTIDUL NAȚIONAL ȚĂRĂNESC CREȘTIN DEMOCRAT",
        "PARTIDUL VERDE",
    )
}
METHOD = (
    "Read from the scanned national tally of the Central Electoral Bureau (form T1 PE and statistics sheet L3 PE, "
    "27 November 2007), with every polling station counted but one that was dissolved. Registered voters are the "
    "voters on the permanent and special lists; ballots cast are the voters who took part, including those on the "
    "supplementary lists; valid votes and invalid ballots are the valid and null votes. The tally prints 1,390 more "
    "voters who took part than valid and null votes together; the figures are stored as printed. The lists' votes "
    "add up to the printed valid votes. The Bureau's county results and its allocation of the 35 seats do not "
    "survive, and the tally prints lists in order of votes, not ballot order."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "bec")


def party_id(label: str) -> str:
    return ep.party_id(label) if canon(label) in SHARED else key("party", canon(label))


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, KEY]


def parse() -> None:
    verify()
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


def verify() -> None:
    """Check the transcription against the totals the tally prints."""
    votes = sum(line[4] for line in LINES)
    if votes != TOTALS[VALID][1]:
        raise ValueError(f"the lists add up to {votes}, the tally prints {TOTALS[VALID][1]} valid votes")
    if TOTALS[CAST][1] - TOTALS[VALID][1] - TOTALS[INVALID][1] != 1390:
        raise ValueError("ballots cast, valid, and null votes differ from the transcription")
    if [line[4] for line in LINES] != sorted((line[4] for line in LINES), reverse=True):
        raise ValueError("the tally orders its lines by votes")


def artifact() -> raw.Artifact:
    return raw.cached(SOURCE, URL)


def images() -> dict[int, Path]:
    """Render each page of the tally once, at the resolution the site shows."""
    stored = artifact()
    PAGES.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(stored.path) as document:
        count = document.page_count
    found = {}
    for number in range(1, count + 1):
        image = PAGES / f"{stored.sha256}-{number}.png"
        if not image.exists():
            stem = image.with_suffix("")
            subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                + [str(stored.path), str(stem)],
                check=True,
            )
        found[number] = image
    return found


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The parties and the independent candidate in Romania's European Parliament election of 2007, from the "
        "Central Electoral Bureau's national tally",
    )
    p.external.add(ROMANIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Biroul Electoral Central",
        title="Situația voturilor valabil exprimate, Parlamentul European, 25 noiembrie 2007 (national tally)",
        url=ORIGINAL,
        license="No license stated: an official document of a state body, excluded from copyright by Romanian law",
        attribution="Biroul Electoral Central",
    )
    p.artifact(artifact(), SOURCE_ID)
    for label, abbreviation, independent, *_ in LINES:
        if independent or canon(label) in SHARED:
            continue
        identifier = party_id(label)
        p.add("party", id=identifier, kind="party", polity_id=ROMANIA)
        p.name(identifier, label, "ro", "official")
        if abbreviation:
            p.name(identifier, abbreviation, "ro", "abbreviation", False)
    return p


def build() -> Package:
    stored = artifact()
    p = Package(
        KEY,
        SOURCE,
        "Romania's European Parliament election of 25 November 2007, national totals from the Central Electoral "
        "Bureau's scanned tally",
        depends=[REGISTRY],
    )
    p.external.update({ROMANIA, MEMBER_STATE_UNIT})
    p.uses(stored)
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_ID}\x1f{stored.storage_key}"))
    pages: dict[int, str] = {}
    for number, image in images().items():
        pages[number] = key("page", stored.sha256, number)
        with pymupdf.open(image) as picture:
            width, height = int(picture[0].rect.width), int(picture[0].rect.height)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )
    for label in SHARED:
        p.external.add(ep.party_id(label))
    election = key("election", DAY)
    p.add("election", id=election, polity_id=ROMANIA, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=f"ro/{DAY}-european", entity_id=election, is_current=True)
    p.name(election, "2007 European Parliament election in Romania", "en", "official")
    p.name(election, "Alegeri pentru Parlamentul European din 25.11.2007", "ro", "native", False)
    contest = key("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        round_number=1,
        default_result_kind="counted",
        magnitude=SEATS,
    )
    p.name(contest, "2007 European Parliament election in Romania", "en", "official")
    p.name(contest, "Alegeri pentru Parlamentul European, România", "ro", "native", False)
    p.add("slug", path=f"ro/{DAY}-european/romania", entity_id=contest, is_current=True)
    result_set = key("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    rows: list[tuple[str | None, int, int, int, tuple[float, float, float, float]]] = [
        (None, measure, page, value, box) for measure, (page, value, box) in TOTALS.items()
    ]
    for label, _, independent, page, votes, box in LINES:
        option = key("option", DAY, canon(label))
        name = label.title() if independent else label
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if independent else "list",
            party_id=None if independent else party_id(label),
            ballot_label=name,
        )
        if independent:
            person = key("person", canon(label))
            p.add("person", id=person)
            p.name(person, name, "ro", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, canon(label)),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                is_independent=True,
            )
        rows.append((option, VOTES, page, votes, box))
    frame = pl.DataFrame(
        [
            (result_set, MEMBER_STATE_UNIT, option, measure, value, pages[page], *box)
            for option, measure, page, value, box in rows
        ],
        schema=["result_set_id", "unit_id", "option_id", "measure", "value", "page_id", "x0", "y0", "x1", "y1"],
        orient="row",
    ).with_columns(
        channel=pl.lit(ALL, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    )
    p.frame(
        "result_value",
        frame.select(
            "result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "value"
        ).with_columns(flags=pl.lit(DIGITIZED, pl.Int16)),
    )
    p.frame(
        "value_locator",
        frame.select(
            "result_set_id",
            "unit_id",
            "option_id",
            "channel",
            "measure",
            "count_round",
            "page_id",
            "x0",
            "y0",
            "x1",
            "y1",
        ).with_columns(source_id=pl.lit(None, pl.String)),
    )
    p.accept(
        "accounting.ballots_sum",
        "the Central Electoral Bureau's tally prints 5,370,171 voters who took part against 5,122,226 valid and "
        "246,555 null votes, 1,390 apart; stored as printed",
    )
    return p


def seed(path: Path, retrieved_at: datetime) -> None:
    """Store a copy of the tally fetched elsewhere under its archive address."""
    raw.store(SOURCE, URL, path.read_bytes(), suffix=".pdf", retrieved_at=retrieved_at, media_type="application/pdf")
