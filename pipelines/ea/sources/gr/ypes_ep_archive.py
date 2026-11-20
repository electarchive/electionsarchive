"""Greece's European Parliament elections of 1981 to 1994, national results, from the Ministry of Interior's scans.

The Ministry's file archive (`webapps.ypes.gr/elections_data/`) holds one scanned results book for each European
Parliament election before 1999. Each book opens with the parties, a general table of each electoral district's
registered voters, ballots, and votes for each party (two facing pages), the seat allocation of the Supreme Electoral
Committee, and the elected members, and then prints the votes of every municipality and commune.

This module builds the national result of each book held in the raw cache: the registered voters, ballots cast, valid,
invalid, and blank ballots, each party's votes, and the seats each party won. The scans have no text layer. Every
figure was keyed from the page and checked against the Tesseract reading of the same page, whose word boxes give each
value's locator, and the parties' votes add up to the printed valid total. The commune tables are not built here.

- **1984** (17 June): the general table's national row (page 16) gives the ballots and the votes of the five largest
  parties, and the first seat allocation (page 18) gives every party's votes. The allocation prints 2,477,245 votes for
  PASOK, a misprint: the general table prints 2,477,445, which the allocation's own remainder (243,726 after nine full
  quotas of 248,191) and the valid total both confirm, so the general table's figure is stored.

- **1984 by electoral district**: the same pages (16 and 17, one table over two facing pages) print the polling
  stations, registered voters, ballots, and each of the sixteen parties' votes for the 56 electoral districts (the
  prefectures, with Athens and Piraeus each split in two) and for the votes cast abroad. They are keyed from the page
  like the national row and read back against the Tesseract reading of both pages; each district's parties add up to
  its valid ballots, and the districts add up to the printed state and general totals, with the two exceptions
  noted on the result set. The districts are the constituencies of the Ministry's later results, so they attach to
  the areas `gr.ypes_national` already holds.

- **1984 by municipality, eparchy, and commune**: pages 25 to 54 print, for each electoral district, the valid ballots
  and every party's votes for each municipality (Δήμος), for the communes of each eparchy taken together (Κοινότητες
  Επαρχίας), for the few communes of Athens' second district printed one by one, and for the parishes (Ενορίες) of
  Athens, Piraeus, and Thessaloniki, with totals for each eparchy, each of those three municipalities, and each
  district. The pages were read twice: by OCR of every row (Tesseract, with the printed percentages and sums used to
  pick between readings) and by a multimodal model transcribing the vote columns from page crops, and the two
  were reconciled cell by cell against the printed percentages. The result is `ypes_ep_archive_tables/ep-1984-municipalities.json`.
  Every leaf row's parties are checked against the printed eparchy, municipality, and district totals; the
  differences that remain are the book's own and are listed in `KNOWN_GROUP_DIFFERENCES`. A note on page 24 says
  these tables were recomputed by machine after the court decisions, so their sums can differ slightly from the
  general table on pages 16 and 17. The municipalities, eparchies, and communes become areas of their own (schemes
  `gr.ep_archive_municipality`, `gr.ep_archive_eparchy`, and `gr.ep_archive_community`) under the electoral district
  areas of `gr.ypes_national`; the book prints no boundaries, so they have no shapes yet. The polling-station tables
  that follow (pages 57 to 370) are not built yet.

The 1981, 1989, and 1994 books are not yet in the raw cache. The 1999 and 2004 national results are built by
`ypes_results`, and later ones by `ypes_national` and `ypes_ep`.

Each election is a package, `gr.ypes_ep_archive/ep-<year>`, built on `gr.ypes_ep_archive/geo` (the scheme of the national unit) and
`gr.ypes_ep_archive/registry` (the source, the scans, and the parties).
"""

import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "gr.ypes_ep_archive"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
GEO = f"{SOURCE}/geo"
SCHEME = "gr.ep_archive_country"
DISTRICT_SCHEME = "gr.ep_archive_district"
EPARCHY_SCHEME = "gr.ep_archive_eparchy"
MUNICIPALITY_SCHEME = "gr.ep_archive_municipality"
COMMUNITY_SCHEME = "gr.ep_archive_community"
PARISH_SCHEME = "gr.ep_archive_parish"
TABLES = Path(__file__).with_name("ypes_ep_archive_tables")
NATIONAL = "gr.ypes_national"
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


def district_area(code: int) -> str:
    """The id `gr.ypes_national` gave the electoral district with this code."""
    return str(uuid5(package.NAMESPACE, "\x1f".join([NATIONAL, "area", "gr.electoral_district", str(code)])))


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
    render_pages()
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
    p = Package(
        GEO,
        SOURCE,
        "The schemes of the national unit and the electoral districts that Greece's scanned European Parliament results use",
    )
    p.external.add(GREECE)
    p.add("scheme", code=SCHEME, polity_id=GREECE, kind="administrative", rank=0, noun="country")
    p.add(
        "scheme",
        code=DISTRICT_SCHEME,
        polity_id=GREECE,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
    )
    p.add("scheme", code=EPARCHY_SCHEME, polity_id=GREECE, kind="administrative", rank=2, noun="eparchy")
    p.add("scheme", code=MUNICIPALITY_SCHEME, polity_id=GREECE, kind="administrative", rank=3, noun="municipality")
    p.add("scheme", code=COMMUNITY_SCHEME, polity_id=GREECE, kind="administrative", rank=3, noun="community")
    p.add("scheme", code=PARISH_SCHEME, polity_id=GREECE, kind="precinct", rank=4, noun="parish")
    for place in places():
        p.external.add(district_area(place.code))
        p.add("area", id=place.id, scheme_code=place.scheme)
        p.add("area_parent", area_id=place.id, parent_id=place.parent)
        p.name(place.id, place.name, "el", "official")
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


DISTRICT_NOTE = (
    "The general table (pages 16 and 17) prints each electoral district's ballots and votes. The first 56 lines are the "
    "prefectures, with Athens and Piraeus each split in two; a line for the votes cast abroad follows the printed "
    "state total. The book prints each district's registered voters, including those who voted abroad, and a "
    "footnote says the 58,722 abroad voters on the abroad line are already counted in their home districts, so the "
    "abroad line carries no registered voters here. Where a cell is left empty on the page, no value is stored. "
    "Kilkis's ΕΔΗΚ line (93) makes that district's party votes exceed its valid ballots (74,378) by one, and the "
    "districts' ΕΔΗΚ votes add up to 16,580 where the book prints 16,579 for the state; both are the book's own figures."
)
PARTY_COLUMNS = 16
KNOWN_ROW_EXCESS = {37: 1}  # district line -> its party votes minus its valid ballots, as the book prints them
KNOWN_COLUMN_EXCESS = {5: 1}  # party number -> the districts' votes minus the state line, as the book prints them


def table(e: Election) -> list[dict[str, object]]:
    return json.loads((TABLES / f"{e.key}.json").read_text())


def cell(raw_cell: object) -> Cell | None:
    if raw_cell is None:
        return None
    assert isinstance(raw_cell, list)
    value, page, left, top, width, height = (int(part) for part in raw_cell)
    return Cell(value, page, (left, top, width, height))


def verify_districts(e: Election, lines: list[dict[str, object]]) -> None:
    """Fail when the districts disagree with the book's own sums, other than where the book disagrees with itself."""
    if len(lines) != 59:
        raise ValueError(f"{e.key}: expected 56 districts, the state line, the abroad line, and the total")
    figures = ("registered", "cast", "valid", "invalid", "blank")

    def line(row: dict[str, object], name: str) -> int:
        raw_cell = row[name]
        assert isinstance(raw_cell, list)
        return int(raw_cell[0])

    def party_votes(row: dict[str, object]) -> list[int]:
        votes = row["votes"]
        assert isinstance(votes, list) and len(votes) == PARTY_COLUMNS
        return [0 if v is None else int(v[0]) for v in votes]

    for number, row in enumerate(lines[:58], 1):
        if line(row, "valid") + line(row, "invalid") + line(row, "blank") != line(row, "cast"):
            raise ValueError(f"{e.key}: line {number}: valid, invalid, and blank ballots do not add up to the cast")
        excess = sum(party_votes(row)) - line(row, "valid")
        if excess != KNOWN_ROW_EXCESS.get(number, 0):
            raise ValueError(f"{e.key}: line {number}: the parties' votes exceed the valid ballots by {excess}")
    state, abroad, total = lines[56], lines[57], lines[58]
    for name in figures:
        added = sum(line(row, name) for row in lines[:56])
        if added != line(state, name):
            raise ValueError(f"{e.key}: the districts' {name} add up to {added:,}, the state line prints it otherwise")
    for number in range(1, PARTY_COLUMNS + 1):
        added = sum(party_votes(row)[number - 1] for row in lines[:56])
        if added - party_votes(state)[number - 1] != KNOWN_COLUMN_EXCESS.get(number, 0):
            raise ValueError(f"{e.key}: the districts' votes for party {number} differ from the state line")
        if party_votes(state)[number - 1] + party_votes(abroad)[number - 1] != party_votes(total)[number - 1]:
            raise ValueError(f"{e.key}: party {number}: state plus abroad is not the general total")
    for name in ("cast", "valid", "invalid", "blank"):
        if line(state, name) + line(abroad, name) != line(total, name):
            raise ValueError(f"{e.key}: {name}: state plus abroad is not the general total")
    for name, expected in zip(figures, (e.registered, e.cast, e.valid, e.invalid, e.blank), strict=True):
        if line(total, name) != expected.value:
            raise ValueError(f"{e.key}: the table's {name} total differs from the national figure keyed on page 16")
    for item in e.parties:
        if party_votes(total)[item.number - 1] != item.votes.value:
            raise ValueError(f"{e.key}: party {item.number}: the table's general total differs from the national row")


def districts(
    p: Package,
    e: Election,
    election: str,
    contest: str,
    options: dict[int, str],
    pages: dict[int, str],
    sizes: dict[int, tuple[float, float]],
) -> list[tuple[str, str, str | None, int, Cell]]:
    """The district result set: one reporting unit for each electoral district and one for the votes abroad."""
    lines = table(e)
    verify_districts(e, lines)
    rs = key("result_set", e.key, "districts")
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=YPES,
        status="active",
        is_default=False,
        completeness=1.0,
    )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
    p.add("note", id=key("note", rs, "caveat"), entity_id=rs, kind="caveat", body_md=DISTRICT_NOTE, lang="en")
    p.accept(
        "accounting.votes_valid",
        "Kilkis's printed party votes add up to one more than its printed valid ballots (74,379 and 74,378); "
        "the book prints both.",
    )
    p.accept(
        "accounting.declared",
        "The districts' ΕΔΗΚ votes add up to 16,580 and the book's state line prints 16,579; the difference is "
        "Kilkis's one vote, which the book prints.",
    )
    rows: list[tuple[str, str, str | None, int, Cell]] = []
    for number, row in [*enumerate(lines[:56], 1), (57, lines[57])]:
        abroad = number == 57
        unit = key("unit", e.key, "district", number)
        if abroad:
            code = None
        else:
            code = row["code"]
            assert isinstance(code, int)
            p.external.add(district_area(code))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GREECE,
            scheme_code=DISTRICT_SCHEME,
            code=f"{e.day}:{number}",
            area_relation="unlocated" if abroad else "equals",
            area_id=None if code is None else district_area(code),
            kind="abroad_mission" if abroad else "standard",
        )
        name = row["name"]
        assert isinstance(name, str)
        p.name(unit, name, "el", "official")
        for field, measure in (
            ("registered", REGISTERED),
            ("cast", CAST),
            ("valid", VALID),
            ("invalid", INVALID),
            ("blank", BLANK),
        ):
            if abroad and field == "registered":
                continue
            found = cell(row[field])
            assert found is not None
            rows.append((rs, unit, None, measure, found))
        votes = row["votes"]
        assert isinstance(votes, list)
        for index, raw_cell in enumerate(votes, 1):
            found = cell(raw_cell)
            if found is not None:
                rows.append((rs, unit, options[index], VOTES, found))
    total = lines[58]
    for field, measure in (
        ("registered", REGISTERED),
        ("cast", CAST),
        ("valid", VALID),
        ("invalid", INVALID),
        ("blank", BLANK),
    ):
        found = cell(total[field])
        assert found is not None
        p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=found.value)
    votes = total["votes"]
    assert isinstance(votes, list)
    for index, raw_cell in enumerate(votes, 1):
        found = cell(raw_cell)
        assert found is not None
        p.add("declared", result_set_id=rs, option_id=options[index], measure=VOTES, value=found.value)
    return rows


MUNICIPALITIES = TABLES / "ep-1984-municipalities.json"
LEAVES = ("municipality", "communes", "community", "parish")
# (district, printed total) -> the leaf rows' sums minus the printed total, as the book prints them: index 0 is the
# valid ballots and 1 to 16 the parties. Each is the book's own figure, checked against the page.
KNOWN_GROUP_DIFFERENCES: dict[tuple[int, str], dict[int, int]] = {
    (1, "Σύνολο Εκλ. Περιφερείας Α΄ Αθηνών"): {
        0: -2566, 1: -158, 2: -142, 3: -52, 4: -26, 5: -1, 6: -10, 7: -3, 8: -2, 9: -2, 11: -2, 13: -2, 15: -1, 16: -1
    },
    (2, "Σύνολο Εκλ. Περιφερείας Β΄ Αθηνών"): {9: -6, 11: 6},
    (4, "Σύνολο Εκλ. Περιφερείας Β΄ Πειραιώς"): {
        0: -3559, 1: -1348, 2: -1657, 3: -229, 4: -113, 5: -13, 6: -22, 7: -16, 8: -6, 9: -114, 10: -1, 11: -13,
        12: -7, 13: -1, 14: -7, 15: -4, 16: -8,
    },
    (21, "Σύνολο Επαρχίας Άρτης"): {8: -3},
    (21, "Σύνολο Εκλ. Περιφερείας Άρτης"): {8: 3},
    (34, "Σύνολο Δήμου Θεσσαλονίκης"): {
        0: -490, 1: -179, 2: -186, 3: -66, 4: -32, 6: -7, 7: -3, 8: -1, 9: -11, 10: -1, 11: -1, 12: -2, 13: -1
    },
    (53, "Σύνολο Επαρχίας Τεμένους"): {3: 5, 10: -5},
    (53, "Σύνολο Εκλ. Περιφερείας Ηρακλείου"): {1: 3, 3: -3, 10: -3},
}  # fmt: skip


@dataclass(frozen=True)
class Row:
    district: int  # the district's line on the general table, 1 to 56
    code: int  # the district's code in gr.ypes_national
    name: str
    kind: str  # municipality, communes, community, parish, eparchy_total, municipality_total, or district
    valid: Cell
    votes: tuple[Cell, ...]
    eparchy: str | None = None  # the eparchy's name, for rows inside an eparchy total
    municipality: str | None = None  # the municipality's name, for parishes inside a municipality total

    @property
    def figures(self) -> list[int]:
        return [self.valid.value, *(c.value for c in self.votes)]


@dataclass(frozen=True)
class Place:
    id: str
    scheme: str
    key: str
    name: str
    parent: str
    code: int


def eparchy_name(label: str) -> str:
    for prefix in ("Σύνολο Επαρχίας ", "Κοινότητες Επαρχίας "):
        if label.startswith(prefix):
            return label.removeprefix(prefix)
    raise ValueError(f"not an eparchy row: {label}")


def municipal_rows() -> list[Row]:
    """The rows of pages 25 to 54, each leaf tied to the eparchy and municipality totals that print its sums."""
    raw_rows = json.loads(MUNICIPALITIES.read_text())
    rows: list[Row] = []
    for district in range(1, 57):
        mine = [r for r in raw_rows if r["district"] == district]
        built: list[Row] = []
        open_rows: list[int] = []
        for r in mine:
            valid = cell(r["valid"])
            assert valid is not None
            votes = tuple(c for c in (cell(v) for v in r["votes"]) if c is not None)
            assert len(votes) == PARTY_COLUMNS
            row = Row(district, int(r["code"]), str(r["name"]), str(r["kind"]), valid, votes)
            if row.kind in LEAVES:
                open_rows.append(len(built))
            elif row.kind == "municipality_total":
                take = len(open_rows)
                while take > 0 and built[open_rows[take - 1]].kind == "parish":
                    take -= 1
                name = "Δήμος " + row.name.removeprefix("Σύνολο Δήμου ")
                for i in open_rows[take:]:
                    built[i] = replace(built[i], municipality=name)
                del open_rows[take:]
                open_rows.append(len(built))
                row = replace(row, municipality=name)
            elif row.kind == "eparchy_total":
                # the shortest run of open rows above whose valid ballots add up to the total's
                want, acc, take = row.valid.value, 0, None
                for at in range(len(open_rows) - 1, -1, -1):
                    acc += built[open_rows[at]].valid.value
                    if acc == want:
                        take = at
                        break
                if take is None:
                    raise ValueError(f"{district}: no run of rows adds up to {row.name}")
                name = eparchy_name(row.name)
                for i in open_rows[take:]:
                    built[i] = replace(built[i], eparchy=name)
                    for j, other in enumerate(built):
                        if (
                            other.kind == "parish"
                            and other.municipality == built[i].municipality
                            and built[i].municipality
                        ):
                            built[j] = replace(other, eparchy=name)
                del open_rows[take:]
                row = replace(row, eparchy=name)
            elif row.kind == "district":
                open_rows.clear()
            else:
                raise ValueError(f"{district}: unknown row kind {row.kind}")
            built.append(row)
        rows += built
    return rows


def verify_municipal(rows: list[Row]) -> None:
    """Fail when a printed total disagrees with its rows other than where the book disagrees with itself."""
    for district in range(1, 57):
        mine = [r for r in rows if r.district == district]
        if sum(1 for r in mine if r.kind == "district") != 1:
            raise ValueError(f"{district}: expected one district total")
        for total in mine:
            if total.kind == "eparchy_total":
                members = [r for r in mine if r.eparchy == total.eparchy and r.kind in (*LEAVES, "municipality_total")]
                members = [r for r in members if not (r.kind == "parish" and r.municipality)]
            elif total.kind == "municipality_total":
                members = [r for r in mine if r.kind == "parish" and r.municipality == total.municipality]
            elif total.kind == "district":
                members = [
                    r
                    for r in mine
                    if (r.kind == "eparchy_total")
                    or (r.kind == "municipality_total" and r.eparchy is None)
                    or (r.kind in LEAVES and r.eparchy is None and r.municipality is None)
                ]
            else:
                continue
            added = [sum(column) for column in zip(*(m.figures for m in members), strict=True)]
            found = {i: a - b for i, (a, b) in enumerate(zip(added, total.figures, strict=True)) if a != b}
            if found != KNOWN_GROUP_DIFFERENCES.get((district, total.name), {}):
                raise ValueError(f"{district}: {total.name}: the rows differ from the printed total by {found}")


def places() -> list[Place]:
    """The eparchies, municipalities, and communes the municipal tables print, under the electoral districts."""
    if not MUNICIPALITIES.exists():
        return []
    out: dict[str, Place] = {}
    for r in municipal_rows():
        district = district_area(r.code)
        parent = district
        if r.eparchy:
            ep = key("area", EPARCHY_SCHEME, r.district, r.eparchy)
            out.setdefault(ep, Place(ep, EPARCHY_SCHEME, r.eparchy, f"Επαρχία {r.eparchy}", district, r.code))
            parent = ep
        if r.municipality:
            mu = key("area", MUNICIPALITY_SCHEME, r.district, r.municipality)
            out.setdefault(mu, Place(mu, MUNICIPALITY_SCHEME, r.municipality, r.municipality, parent, r.code))
        if r.kind == "municipality":
            mu = key("area", MUNICIPALITY_SCHEME, r.district, r.name)
            out.setdefault(mu, Place(mu, MUNICIPALITY_SCHEME, r.name, r.name, parent, r.code))
        elif r.kind == "community":
            co = key("area", COMMUNITY_SCHEME, r.district, r.name)
            out.setdefault(co, Place(co, COMMUNITY_SCHEME, r.name, r.name, parent, r.code))
    return list(out.values())


MUNICIPAL_METHOD = (
    "The Ministry of Interior's results book prints, after the general table, the valid ballots and every party's "
    "votes for each municipality, for the communes of each eparchy together, and for the parishes of Athens, Piraeus, "
    "and Thessaloniki (pages 25 to 54). The book is a scan without a text layer. Each row was read by OCR and, "
    "independently, transcribed by a multimodal model; the readings were reconciled cell by cell against the "
    "percentages the book prints beside each party's votes, and every row was checked against the eparchy, "
    "municipality, and district totals the book prints. The totals themselves are not stored as values; they are "
    "the sums of the rows."
)
MUNICIPAL_NOTE = (
    "A note on page 24 of the book says that these tables were produced by machine after the courts' decisions and "
    "that their sums were recomputed, so they can differ slightly from the general table on pages 16 and 17. They "
    "do: most districts' party totals differ from the general table by a few votes. The book also disagrees with "
    "itself in places, and the figures are stored as printed. About a quarter of the rows print valid ballots that "
    "differ from the sum of their parties' votes, usually by one to ten votes and in a few Athens parishes by "
    "several hundred, while every party's printed percentage agrees with its printed votes. Β΄ Πειραιώς prints a "
    "district total 3,559 valid ballots above the sum of its seven municipalities, Θεσσαλονίκη's municipal total is "
    "490 above the sum of its parishes, and Α΄ Αθηνών's parishes add up to 2,566 fewer valid ballots than its "
    "printed total; no row on the page carries the difference. The votes cast abroad are not in these tables. The "
    "communes of each eparchy are printed together, as one figure for the eparchy outside its municipalities, and "
    "are stored that way. One row, the communes of the eparchy of Σουφλί, is cut off on the scan and is the eparchy "
    "total less the municipality of Σουφλί."
)


def municipal(
    p: Package,
    e: Election,
    election: str,
    contest: str,
    options: dict[int, str],
) -> list[tuple[str, str, str | None, int, Cell]]:
    """The result set of municipalities, eparchies' communes, and parishes, with no values for the printed totals."""
    rows = municipal_rows()
    verify_municipal(rows)
    rs = key("result_set", e.key, "municipalities")
    state = table(e)[56]
    state_valid = cell(state["valid"])
    assert state_valid is not None
    leaves = [r for r in rows if r.kind in LEAVES]
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=YPES,
        status="active",
        is_default=False,
        completeness=round(min(1.0, sum(r.valid.value for r in leaves) / state_valid.value), 4),
    )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=MUNICIPAL_METHOD, lang="en")
    p.add("note", id=key("note", rs, "caveat"), entity_id=rs, kind="caveat", body_md=MUNICIPAL_NOTE, lang="en")
    for code, reason in (
        (
            "accounting.votes_valid",
            (
                "In the municipal tables the book prints valid ballots that differ from the sum of the parties' printed "
                "votes in about a quarter of the rows; each party's printed percentage agrees with its votes, so both are "
                "stored as printed."
            ),
        ),
        (
            "accounting.declared",
            (
                "The municipal set declares the general table's state line (page 16); the municipal tables were "
                "recomputed separately (the book's note on page 24) and leave out rows the district totals include, so "
                "their sums differ from it."
            ),
        ),
    ):
        earlier = p.accepted.get(code)
        p.accept(code, f"{earlier} {reason}" if earlier else reason)
    p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=state_valid.value)
    votes = state["votes"]
    assert isinstance(votes, list)
    for index, raw_cell in enumerate(votes, 1):
        found = cell(raw_cell)
        assert found is not None
        p.add("declared", result_set_id=rs, option_id=options[index], measure=VOTES, value=found.value)
    out: list[tuple[str, str, str | None, int, Cell]] = []
    for r in leaves:
        district = district_area(r.code)
        if r.kind == "municipality":
            scheme, area, relation = MUNICIPALITY_SCHEME, key("area", MUNICIPALITY_SCHEME, r.district, r.name), "equals"
        elif r.kind == "community":
            scheme, area, relation = COMMUNITY_SCHEME, key("area", COMMUNITY_SCHEME, r.district, r.name), "equals"
        elif r.kind == "communes":
            assert r.eparchy is not None
            scheme, area, relation = EPARCHY_SCHEME, key("area", EPARCHY_SCHEME, r.district, r.eparchy), "within"
        else:
            if r.municipality:
                area = key("area", MUNICIPALITY_SCHEME, r.district, r.municipality)
            else:
                area = district
            scheme, relation = PARISH_SCHEME, "within"
        unit = key("unit", e.key, "municipal", r.district, r.kind, r.name)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GREECE,
            scheme_code=scheme,
            code=f"{e.day}:{r.district}:{r.kind}:{r.name}",
            area_relation=relation,
            area_id=area,
            kind="standard",
        )
        p.name(unit, r.name, "el", "official")
        out.append((rs, unit, None, VALID, r.valid))
        for index, found in enumerate(r.votes, 1):
            out.append((rs, unit, options[index], VOTES, found))
    return out


def municipal_pages() -> set[int]:
    if not MUNICIPALITIES.exists():
        return set()
    return {c.page for r in municipal_rows() for c in (r.valid, *r.votes)}


def render_pages() -> None:
    """Write the 100 dpi page images the municipal and general tables cite, for upload beside the packages."""
    for e in ELECTIONS:
        if not available(e):
            continue
        artifact = raw.cached(SOURCE, mirror(e))
        out = DATA / "pages" / SOURCE
        out.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(artifact.path) as document:
            for number in sorted(municipal_pages() | {16, 17, 18}):
                target = out / f"{artifact.sha256}-{number}.png"
                if not target.exists():
                    document[number - 1].get_pixmap(dpi=DPI // 3).save(target)


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
    if (TABLES / f"{e.key}.json").exists():
        used |= {16, 17}
    if e.key == "ep-1984":
        used |= municipal_pages()
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

    rows: list[tuple[str, str, str | None, int, Cell]] = [
        (rs, unit, None, REGISTERED, e.registered),
        (rs, unit, None, CAST, e.cast),
        (rs, unit, None, VALID, e.valid),
        (rs, unit, None, INVALID, e.invalid),
        (rs, unit, None, BLANK, e.blank),
    ]
    options: dict[int, str] = {}
    for item in sorted(e.parties, key=lambda x: -x.votes.value):
        option = options[item.number] = key("option", e.key, item.number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(e, item.number),
            ballot_position=item.number,
            ballot_label=f"{item.name} ({item.short})" if item.short else item.name,
        )
        rows.append((rs, unit, option, VOTES, item.votes))
        if item.seats:
            p.add("outcome", result_set_id=rs, option_id=option, kind="won", seats=item.seats)

    national = list(rows)
    if (TABLES / f"{e.key}.json").exists():
        rows += districts(p, e, election, contest, options, pages, sizes)
    if e.key == "ep-1984" and MUNICIPALITIES.exists():
        p.external |= {district_area(r.code) for r in municipal_rows()}
        rows += municipal(p, e, election, contest, options)

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
            for rs, unit, option, measure, cell in rows
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
    for set_id, _, option, measure, cell in national:
        p.add("declared", result_set_id=set_id, option_id=option, measure=measure, value=cell.value)
    return p
