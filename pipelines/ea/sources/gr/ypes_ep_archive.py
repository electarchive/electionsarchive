"""Greece's European Parliament elections of 1981 to 1994, national results, from the Ministry of Interior's scans.

The Ministry's file archive (`webapps.ypes.gr/elections_data/`) holds one scanned results book for each European
Parliament election before 1999. Each book opens with the parties, a general table of each electoral district's
registered voters, ballots, and votes for each party (two facing pages), the seat allocation of the Supreme Electoral
Committee, and the elected members, and then prints the votes of every municipality and commune.

This module builds the national result of each book held in the raw cache: the registered voters, ballots cast, valid,
invalid, and blank ballots, each party's votes, and the seats each party won. The scans have no text layer. Every
figure was keyed from the page and checked against the Tesseract reading of the same page, whose word boxes give each
value's locator, and the parties' votes add up to the printed valid total. The district and commune tables are not
built here.

- **1984** (17 June): the general table's national row (page 16) gives the ballots and the votes of the five largest
  parties, and the first seat allocation (page 18) gives every party's votes. The allocation prints 2,477,245 votes for
  PASOK, a misprint: the general table prints 2,477,445, which the allocation's own remainder (243,726 after nine full
  quotas of 248,191) and the valid total both confirm, so the general table's figure is stored.

The 1981, 1989, and 1994 books are not yet in the raw cache. The 1999 and 2004 national results are built by
`ypes_results`, and later ones by `ypes_national` and `ypes_ep`.

Each election is a package, `gr.ypes_ep_archive/ep-<year>`, built on `gr.ypes_ep_archive/geo` (the scheme of the national unit) and
`gr.ypes_ep_archive/registry` (the source, the scans, and the parties).
"""

from dataclasses import dataclass
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import pymupdf

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "gr.ypes_ep_archive"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
GEO = f"{SOURCE}/geo"
SCHEME = "gr.ep_archive_country"
REGISTRY = f"{SOURCE}/registry"
ARCHIVE = "https://webapps.ypes.gr/elections_data/"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DIGITIZED = 8
DPI = 300  # the resolution the boxes below were read at


@dataclass(frozen=True)
class Cell:
    value: int
    page: int
    box: tuple[int, int, int, int]  # left, top, width, height in pixels at DPI


@dataclass(frozen=True)
class Party:
    number: int  # the party's number on the Ministry's list
    name: str
    short: str | None
    votes: Cell
    seats: int


@dataclass(frozen=True)
class Election:
    key: str
    day: date
    url: str
    seats: int
    registered: Cell
    cast: Cell
    valid: Cell
    invalid: Cell
    blank: Cell
    parties: tuple[Party, ...]
    note: str

    @property
    def package(self) -> str:
        return f"{SOURCE}/{self.key}"

    @property
    def path(self) -> str:
        return f"gr/{self.day.isoformat()}-european-parliament"


def folder(day: date) -> str:
    stamp = f"{day.year}_{day.month:02d}_{day.day:02d}"
    compact = f"{day.day:02d}{day.month:02d}{day.year}"
    return (
        f"{ARCHIVE}ΕΚΛΟΓΕΣ ΓΙΑ ΤΟ ΕΥΡΩΠΑΙΚΟ ΚΟΙΝΟΒΟΥΛΙΟ/ΑΠΟΤΕΛΕΣΜΑΤΑ ΕΥΡΩΚΛΟΓΩΝ/"
        f"ΑΠΟΤΕΛΕΣΜΑΤΑ_ΕΥΡΩΒΟΥΛΕΥΤΙΚΕΣ_{stamp}/PDF/ΑΠΟΤΕΛΕΣΜΑΤΑ_ΕΥΡΩΒΟΥΛΕΥΤΙΚΕΣ_{compact}.pdf"
    )


ELECTIONS = [
    Election(
        "ep-1984",
        date(1984, 6, 17),
        quote(folder(date(1984, 6, 17)), safe=":/"),
        24,
        registered=Cell(7_460_669, 16, (1544, 2658, 134, 27)),
        cast=Cell(6_012_203, 16, (1745, 2659, 128, 25)),
        valid=Cell(5_956_588, 16, (2352, 2659, 131, 26)),
        invalid=Cell(36_189, 16, (1914, 2659, 92, 25)),
        blank=Cell(19_426, 16, (2050, 2659, 82, 26)),
        parties=(
            Party(1, "ΠΑΝΕΛΛΗΝΙΟ ΣΟΣΙΑΛΙΣΤΙΚΟ ΚΙΝΗΜΑ", "ΠΑ.ΣΟ.Κ.", Cell(2_477_445, 16, (2551, 2659, 124, 26)), 10),
            Party(2, "ΝΕΑ ΔΗΜΟΚΡΑΤΙΑ", "Ν.Δ.", Cell(2_266_088, 16, (2851, 2659, 124, 26)), 9),
            Party(3, "ΚΟΜΜΟΥΝΙΣΤΙΚΟ ΚΟΜΜΑ ΕΛΛΑΔΑΣ", "Κ.Κ.Ε.", Cell(693_466, 16, (3176, 2659, 108, 26)), 3),
            Party(
                4,
                "ΚΟΜΜΟΥΝΙΣΤΙΚΟ ΚΟΜΜΑ ΕΛΛΑΔΑΣ ΕΣΩΤΕΡΙΚΟΥ",
                "Κ.Κ.Ε. Εσωτ.",
                Cell(203_671, 16, (3475, 2660, 102, 25)),
                1,
            ),
            Party(5, "ΕΝΩΣΗ ΔΗΜΟΚΡΑΤΙΚΟΥ ΚΕΝΤΡΟΥ", "Ε.ΔΗ.Κ.", Cell(16_865, 16, (3712, 2661, 89, 27)), 0),
            Party(6, "ΚΟΜΜΑ ΔΗΜΟΚΡΑΤΙΚΟΥ ΣΟΣΙΑΛΙΣΜΟΥ", "ΚΟ.ΔΗ.ΣΟ.", Cell(47_400, 18, (2269, 1270, 115, 28)), 0),
            Party(7, "ΧΡΙΣΤΙΑΝΙΚΗ ΔΗΜΟΚΡΑΤΙΑ", None, Cell(26_731, 18, (2270, 1310, 109, 30)), 0),
            Party(8, "ΚΟΜΜΑ ΤΩΝ ΠΡΟΟΔΕΥΤΙΚΩΝ", None, Cell(10_136, 18, (2273, 1352, 111, 29)), 0),
            Party(9, "ΕΘΝΙΚΗ ΠΟΛΙΤΙΚΗ ΕΝΩΣΙΣ", "Ε.Π.ΕΝ.", Cell(136_623, 18, (2252, 1393, 132, 29)), 1),
            Party(10, "ΕΝΙΑΙΑ ΣΟΣΙΑΛΙΣΤΙΚΗ ΠΑΡΑΤΑΞΗ ΕΛΛΑΔΑ", "Ε.Σ.Π.Ε.", Cell(8_321, 18, (2293, 1434, 87, 29)), 0),
            Party(11, "ΚΟΜΜΑ ΦΙΛΕΛΕΥΘΕΡΩΝ", None, Cell(20_910, 18, (2271, 1475, 115, 29)), 0),
            Party(12, "ΕΝΙΑΙΟ ΕΘΝΙΚΙΣΤΙΚΟ ΚΙΝΗΜΑ", "ΕΝ.Ε.Κ.", Cell(5_612, 18, (2294, 1517, 91, 28)), 0),
            Party(
                13,
                "ΕΠΑΝΑΣΤΑΤΙΚΟ ΚΟΜΜΟΥΝΙΣΤΙΚΟ ΚΙΝΗΜΑ ΕΛΛΑΔΑΣ",
                "Ε.Κ.Κ.Ε.",
                Cell(17_752, 18, (2274, 1558, 111, 28)),
                0,
            ),
            Party(
                14,
                "ΕΡΓΑΤΙΚΗ ΔΙΕΘΝΙΣΤΙΚΗ ΕΝΩΣΗ",
                "Ε.Δ.Ε. - ΤΡΟΤΣΚΙΣΤΕΣ",
                Cell(6_382, 18, (2293, 1599, 92, 29)),
                0,
            ),
            Party(
                15,
                "ΑΓΩΝΙΣΤΙΚΟ ΣΟΣΙΑΛΙΣΤΙΚΟ ΚΟΜΜΑ ΕΛΛΑΔΑΣ",
                "Α.Σ.Κ.Ε.",
                Cell(10_311, 18, (2275, 1640, 106, 29)),
                0,
            ),
            Party(16, "ΕΛΕΥΘΕΡΟΙ", None, Cell(8_875, 18, (2293, 1681, 93, 29)), 0),
        ),
        note=(
            "The Supreme Electoral Committee's first seat allocation (page 18 of the book) prints 2,477,245 votes for "
            "PASOK. The general table (page 16) prints 2,477,445, which the allocation's own remainder of 243,726 "
            "after nine quotas of 248,191 and the printed valid total of 5,956,588 both confirm, so that figure is "
            "stored. The registered voters include those who voted abroad on 16 June, whom the general table lists "
            "with their home districts."
        ),
    ),
]
BY_KEY = {e.key: e for e in ELECTIONS}

METHOD = (
    "The Ministry of Interior's results book for the election, a scan with no text layer, prints a general table of "
    "each electoral district's registered voters, ballots, and votes for each party, with the national totals, and "
    "the Supreme Electoral Committee's seat allocation. The values here are the national figures, keyed from the "
    "page and checked against an OCR reading of it; the parties' votes add up to the printed valid total, and the "
    "valid, invalid, and blank ballots add up to the ballots cast. Seats are those the Committee allocated: whole "
    "quotas of the valid votes divided by the seats, and the remaining seats by the largest remainders."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


YPES = key("source")


def packages() -> list[str]:
    return [GEO, REGISTRY, *(e.package for e in ELECTIONS if available(e))]


def available(election: Election) -> bool:
    try:
        raw.cached(SOURCE, mirror(election))
    except LookupError:
        return False
    return True


def mirror(election: Election) -> str:
    """The cache key of the book: the Ministry's address, or the snapshot of it that was fetched."""
    for k in raw.keys(SOURCE):
        if k == election.url or k.endswith("/" + election.url):
            return k
    return election.url


def fetch() -> None:
    for election in ELECTIONS:
        raw.fetch(SOURCE, election.url, suffix=".pdf")


def parse() -> None:
    for election in ELECTIONS:
        if available(election):
            verify(election)
    geo().write()
    registry().write()
    for election in ELECTIONS:
        if available(election):
            build(election).write()


def emit() -> None:
    package.run("load", packages())


def verify(e: Election) -> None:
    """Fail when the keyed figures disagree with the totals the book prints."""
    votes = sum(party.votes.value for party in e.parties)
    if votes != e.valid.value:
        raise ValueError(f"{e.key}: the parties add up to {votes:,}, the book prints {e.valid.value:,} valid")
    if e.valid.value + e.invalid.value + e.blank.value != e.cast.value:
        raise ValueError(f"{e.key}: valid, invalid, and blank ballots do not add up to the ballots cast")
    if sum(party.seats for party in e.parties) != e.seats:
        raise ValueError(f"{e.key}: the seats do not add up to {e.seats}")
    if e.cast.value > e.registered.value:
        raise ValueError(f"{e.key}: more ballots than registered voters")


def party(e: Election, number: int) -> str:
    return key("party", e.key, number)


def artifact_id(artifact: raw.Artifact) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{YPES}\x1f{artifact.storage_key}"))


def geo() -> Package:
    p = Package(GEO, SOURCE, "The scheme of the national unit that Greece's scanned European Parliament results use")
    p.external.add(GREECE)
    p.add("scheme", code=SCHEME, polity_id=GREECE, kind="administrative", rank=0, noun="country")
    return p


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Ministry of Interior's scanned results books of Greece's European Parliament elections, and their parties",
        depends=[GEO],
    )
    p.external.add(GREECE)
    p.add(
        "source",
        id=YPES,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών (Ministry of Interior)",
        title="Αρχείο εκλογικών αποτελεσμάτων (archive of election results)",
        url=ARCHIVE,
        license="Official publication; reuse terms are not stated",
        license_url=ARCHIVE,
        attribution="Υπουργείο Εσωτερικών",
    )
    for e in ELECTIONS:
        if not available(e):
            continue
        p.artifact(raw.cached(SOURCE, mirror(e)), YPES)
        for item in e.parties:
            id = party(e, item.number)
            p.add("party", id=id, kind="party", polity_id=GREECE)
            p.add("external_id", entity_id=id, scheme="gr.ypes_ep_archive.party", value=f"{e.day.year}:{item.number}")
            full = f"{item.name} ({item.short})" if item.short and item.short != item.name else item.name
            p.name(id, full, "el", "official")
            if item.short:
                p.name(id, item.short, "el", "abbreviation", False)
    return p


def build(e: Election) -> Package:
    verify(e)
    artifact = raw.cached(SOURCE, mirror(e))
    p = Package(
        e.package,
        SOURCE,
        f"The {e.day.year} European Parliament election in Greece, national result, from the Ministry of Interior's "
        "scanned results book",
        depends=[GEO, REGISTRY],
    )
    p.external |= {GREECE, TERRITORY}
    p.uses(artifact)
    pages: dict[int, str] = {}
    sizes: dict[int, tuple[float, float]] = {}
    used = {c.page for c in (e.registered, e.cast, e.valid, e.invalid, e.blank)} | {x.votes.page for x in e.parties}
    with pymupdf.open(artifact.path) as document:
        for number in sorted(used):
            rect = document[number - 1].rect
            sizes[number] = (rect.width * DPI / 72, rect.height * DPI / 72)
    for number in sorted(used):
        pages[number] = p.id("page", e.key, number)
        width, height = sizes[number]
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact_id(artifact),
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{number}.png",
            width=round(width / 3),
            height=round(height / 3),
        )

    election = key("election", e.key)
    p.add(
        "election",
        id=election,
        polity_id=GREECE,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{e.day.year} European Parliament election in Greece", "en", "official")
    p.name(election, f"Ευρωεκλογές {e.day.year}", "el", "native", False)
    p.add("slug", path=e.path, entity_id=election, is_current=True)

    title = "Greek seats in the European Parliament"
    contest = key("contest", e.key, "national")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=None,
        district_area_id=TERRITORY,
        magnitude=e.seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    p.name(contest, "Έδρες Ελλάδας στο Ευρωπαϊκό Κοινοβούλιο", "el", "native", False)
    p.add("slug", path=f"{e.path}/{changeset.slug(title)}", entity_id=contest, is_current=True)

    rs = key("result_set", e.key, "national")
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=YPES,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
    p.add("note", id=key("note", rs, "caveat"), entity_id=rs, kind="caveat", body_md=e.note, lang="en")

    unit = key("unit", e.key, "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=GREECE,
        scheme_code=SCHEME,
        code=f"{e.day}:national",
        area_relation="equals",
        area_id=TERRITORY,
        kind="standard",
    )

    rows: list[tuple[str | None, int, Cell]] = [
        (None, REGISTERED, e.registered),
        (None, CAST, e.cast),
        (None, VALID, e.valid),
        (None, INVALID, e.invalid),
        (None, BLANK, e.blank),
    ]
    for item in sorted(e.parties, key=lambda x: -x.votes.value):
        option = key("option", e.key, item.number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(e, item.number),
            ballot_position=item.number,
            ballot_label=f"{item.name} ({item.short})" if item.short else item.name,
        )
        rows.append((option, VOTES, item.votes))
        if item.seats:
            p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=item.seats)

    frame = pl.DataFrame(
        [
            (
                rs,
                unit,
                option,
                measure,
                cell.value,
                pages[cell.page],
                cell.box[0] / sizes[cell.page][0],
                cell.box[1] / sizes[cell.page][1],
                (cell.box[0] + cell.box[2]) / sizes[cell.page][0],
                (cell.box[1] + cell.box[3]) / sizes[cell.page][1],
            )
            for option, measure, cell in rows
        ],
        schema=["result_set_id", "unit_id", "option_id", "measure", "value", "page_id", "x0", "y0", "x1", "y1"],
        orient="row",
    ).with_columns(
        channel=pl.lit(0, pl.Int16),
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
    for option, measure, cell in rows:
        p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=cell.value)
    return p
