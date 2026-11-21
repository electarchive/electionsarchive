"""Greece's parliamentary election of 5 November 1989 by electoral constituency, from the Ministry of Interior's scan.

The Ministry's file archive (`webapps.ypes.gr/elections_data/`) holds a scanned results book for each parliamentary
election of 1989 onward and for earlier ones. The first volume of the book for 5 November 1989 opens with the
parties and their ballot numbers (pages 13 to 17), a general table of the votes of each party in each of the 56
electoral constituencies (pages 20 and 21, one table over two facing pages), the number of seats each party won in
each constituency and nationally (page 22), and the elected members, and then prints the votes of every
municipality, commune, and parish.

This module builds the constituency result of the book: the registered voters, ballots cast, valid ballots, invalid
and blank ballots, and each party's votes in each of the 56 constituencies and in the whole state, and the seats each
party won, in each constituency and from the 12 state seats. The pages have no text layer. Every figure was read from
the page image by Tesseract, cell by cell, and then settled against the book's own sums: every constituency's parties
add up to its valid ballots, every party's constituencies add up to the printed state total, and the party seats
add up to each constituency's seats and to the printed national seats. A cell the reading and these sums did not
settle was read again by eye from the page. Each value's locator is its cell on the page.

The tables of municipalities, communes, and parishes (pages 53 to 119) are not built here: they print the
constituencies' figures as recomputed by machine from the polling-station returns, so they differ from the court-decided
general table, and their reading is not yet reconciled.

The election is the package `gr.ypes_parl_archive/pl-1989-11`, built on `gr.ypes_parl_archive/registry`. The constituencies are the areas `gr.ypes_national` holds.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid5

import polars as pl
import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "gr.ypes_parl_archive"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
NATIONAL = "gr.ypes_national"
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
KEY = "pl-1989-11"
DAY = date(1989, 11, 5)
SCHEME = "gr.parl_archive_district"
NATIONAL_SCHEME = "gr.electoral_district"
TABLES = Path(__file__).with_name("ypes_parl_archive_tables")
ARCHIVE = "https://webapps.ypes.gr/elections_data/"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
DPI = 300
STATE_SEATS = 12
BOOK = (
    "ΕΘΝΙΚΕΣ ΕΚΛΟΓΕΣ/ΑΠΟΤΕΛΕΣΜΑΤΑ ΕΘΝΙΚΩΝ (ΒΟΥΛΕΥΤΙΚΩΝ) ΕΚΛΟΓΩΝ/"
    "ΑΠΟΤΕΛΕΣΜΑΤΑ_ΒΟΥΛΕΥΤΙΚΕΣ_1989_11_05_1ΤΟΜ_ΣΥΓΚΕΝΤΡΩΤΙΚΑ/PDF/"
    "ΑΠΟΤΕΛΕΣΜΑΤΑ_ΒΟΥΛΕΥΤΙΚΕΣ_05111989_1ΤΟΜ_ΣΥΓΚΕΝΤΡΩΤΙΚΑ.pdf"
)
URL = quote(ARCHIVE + BOOK, safe=":/()")
PAGES = (20, 21, 22)

# the ballot number, name, and abbreviation of each party, from pages 13 to 17 and the headings of pages 20 and 21
PARTIES: list[tuple[int, str, str | None]] = [
    (1, "Νέα Δημοκρατία", "Ν.Δ."),
    (2, "Πανελλήνιο Σοσιαλιστικό Κίνημα", "ΠΑ.ΣΟ.Κ."),
    (3, "Συνασπισμός της Αριστεράς και της Προόδου", "ΣΥΝΑΣΠΙΣΜΟΣ"),
    (4, "Αριστερή Πρωτοβουλία Κουμμουνιστών-Ριζοσπαστών Οικολόγων", "ΑΡΙΣΤ. ΠΡΩΤΟΒ."),
    (5, "Κόμμα Φιλελευθέρων", "Κ.Φ."),
    (6, "Οικολόγοι Εναλλακτικοί - Ομοσπονδία Οικολογικών και Εναλλακτικών Οργανώσεων", "ΟΙΚΟΛΟΓΟΙ ΕΝΑΛΛ."),
    (7, "Κολλάτος - Οικολογικό Ελληνικό Κίνημα - Ένωση Οικολόγων", "ΚΟΜΜΑΤΟΣ ΟΙΚ.ΕΛΛ.ΚΙΝ."),
    (8, "Κομμουνιστικό Κόμμα Ελλάδας (Μαρξιστικό-Λενινιστικό)", "Κ.Κ.Ε. (μ-λ)"),
    (9, "Αγωνιστικό Σοσιαλιστικό Κόμμα Ελλάδας", "Α.Σ.Κ.Ε."),
    (10, "Αυτοδύναμη Κίνηση Εργατικής Πολιτικής", "Α.Κ.Ε.Π."),
    (11, "Μαρξιστικό - Λενινιστικό Κομμουνιστικό", "Μ.Λ.(Κ.Κ.Ε.)"),
    (12, "Ολυμπισμός", "ΟΛΥΜΠΙΣΜΟΣ"),
    (13, "Οργάνωση για την ανασυγκρότηση του ΚΚΕ", "Ο.Α.Κ.Κ.Ε."),
    (14, "Οικολόγοι Ελλάδας", "ΟΙΚΟΛ.ΕΛΛ."),
    (15, "Εργατικό Αντιιμπεριαλιστικό Μέτωπο", "Ε.Α.Μ."),
    (16, "Λευκό", "ΛΕΥΚΟ"),
    (17, "Εργατικό Επαναστατικό Κόμμα (Τροτσκιστές)", "Ε.Ε.Κ."),
    (18, "Αυτοσεβασμός", None),
    (19, "Ελληνικό Ευρωπαϊκό Κόμμα", None),
    (20, "Εθνικό Μέτωπο - Των Εθνικιστών Αγωνιστών", None),
    (21, "Ανεξάρτητος Σοσιαλδημοκρατική Αναγέννησις", "Α.Σ.Α."),
    (22, "Κόμμα Ανθρωπιά και Ειρήνη", None),
    (23, "Οι Πράσινοι - Οικολογικό Κόμμα Ελλάδας - Πανελλήνια Πράσινη Εναλλακτική Κίνηση", None),
    (24, "Ανεξάρτητο Κίνημα Δημοκρατικής Αναγέννησης", "Α.ΚΙ.Δ.Α."),
    (25, "Ελληνοευρωπαϊκό Κόμμα 1984", None),
    (26, "Κόμμα Ελληνισμού", None),
    (27, "Ελληνικό Σοσιαλδημοκρατικό Επαναστατικό Απελευθερωτικό Μέτωπο ο Σπάρτακος", "ΕΛΔΗΚ"),
    (28, "Κίνηση μη Προνομιούχων", None),
    (29, "Επαναστατική - Λαϊκή - Εκτελεστική - Επιτροπή - Μέριμνα", "Ε.Λ.Ε.Ε.Μ."),
    (30, "Ολυμπιακή Δημοκρατία", "Ο.ΔΗ."),
    (31, "Περιφερειακή Αστική Ανάπτυξη", "Π.Α.Α."),
    (32, "Πεπρωμένο", None),
    (33, "Εμπιστοσύνη", None),
]
INDEPENDENTS = "Ανεξάρτητοι μεμονωμένοι"
# the party seats the book prints (page 22) for each constituency, as {ballot number: (first allocation, second)},
# with 0 for the independents' combinations; the constituency's code is its line on the page
SEAT_ROWS = [  # for each constituency, in order of code: first and second allocation of ND, PASOK, SYN, Oikologoi, independents
    (3, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 1, 0),
    (1, 1, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (6, 0, 5, 1, 1, 0, 0, 0, 0, 0),
    (3, 1, 3, 0, 0, 0, 0, 0, 0, 0),
    (4, 0, 3, 0, 0, 1, 0, 0, 0, 0),
    (2, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 1, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (3, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (3, 0, 2, 0, 0, 1, 0, 0, 0, 0),
    (2, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (4, 0, 3, 0, 1, 0, 0, 0, 0, 0),
    (3, 0, 2, 1, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 1, 0, 0, 0, 0),
    (2, 1, 2, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 1, 0, 0, 0, 0, 0, 0),
    (0, 0, 0, 0, 0, 0, 0, 0, 1, 0),
    (1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (0, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (4, 0, 3, 1, 0, 0, 0, 0, 0, 0),
    (4, 0, 4, 0, 0, 1, 0, 0, 0, 0),
    (3, 0, 3, 0, 0, 0, 0, 0, 0, 0),
    (3, 0, 2, 0, 0, 1, 0, 0, 0, 0),
    (1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (3, 0, 3, 0, 0, 0, 0, 0, 0, 0),
    (10, 0, 7, 0, 3, 0, 0, 1, 0, 0),
    (13, 0, 12, 1, 5, 1, 0, 0, 0, 0),
    (4, 0, 3, 0, 1, 0, 0, 0, 0, 0),
    (3, 0, 4, 0, 1, 0, 0, 0, 0, 0),
    (3, 1, 3, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (4, 1, 2, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 2, 0, 1, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 2, 0, 0, 0, 0, 0, 0, 0),
    (1, 1, 2, 0, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
    (2, 0, 4, 1, 0, 0, 0, 0, 0, 0),
    (1, 0, 1, 0, 0, 0, 0, 0, 0, 0),
]
SEATS: dict[int, dict[int, tuple[int, int]]] = {
    code: {
        number: (row[2 * n], row[2 * n + 1]) for n, number in enumerate((1, 2, 3, 6, 34)) if row[2 * n] + row[2 * n + 1]
    }
    for code, row in enumerate(SEAT_ROWS, 1)
}
STATE: dict[int, int] = {1: 6, 2: 5, 3: 1}
METHOD = (
    "The Ministry of Interior's results book for the election of 5 November 1989 prints, for each electoral "
    "constituency, the registered voters, the ballots cast, the invalid and blank ballots together, the valid "
    "ballots, and the votes of each party and of the independent candidates standing alone. These are the figures "
    "after the decisions of the courts, which the book gives on its general table. Each figure was read from the "
    "scanned page by OCR and settled against the book's own sums, and its cell on the page is its locator."
)
INVALID_NOTE = (
    "The book prints the invalid and blank ballots as one figure, and it is stored as the invalid ballots; the "
    "blank ballots are not stored separately. Ballots cast are the valid ballots plus this figure."
)
SEAT_METHOD = (
    "Seats are those the book's table of seat numbers (page 22) prints for each constituency: the seats a party won "
    "in the constituency itself and those it won there in the second distribution among the constituencies. "
    "Of the 300 seats, 288 are won in the 56 constituencies and 12 are state seats distributed on the national "
    "vote; the state seats are held in their own contest."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def district_area(code: int) -> str:
    """The id `gr.ypes_national` gave the electoral constituency with this code."""
    return str(uuid5(package.NAMESPACE, "\x1f".join([NATIONAL, "area", NATIONAL_SCHEME, str(code)])))


PARLIAMENT = str(uuid5(package.NAMESPACE, f"{NATIONAL}\x1fbody\x1fparliament"))
YPES = key("source")
PACKAGE = f"{SOURCE}/{KEY}"
PATH = f"gr/{DAY.isoformat()}-parliamentary"


def packages() -> list[str]:
    return [GEO, REGISTRY, PACKAGE]


def mirror() -> str:
    """The cache key of the book: the Ministry's address, or the snapshot of it that was fetched."""
    for k in raw.keys(SOURCE):
        if k == URL or k.endswith("/" + URL):
            return k
    return URL


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def artifact_id(artifact: raw.Artifact) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{YPES}\x1f{artifact.storage_key}"))


def table() -> dict[str, Any]:
    return json.loads((TABLES / f"{KEY}.json").read_text())


def parse() -> None:
    verify(table())
    render_pages()
    geo().write()
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


def figure(value: object) -> int:
    assert isinstance(value, list)
    return int(value[0])


def verify(t: dict[str, Any]) -> None:
    """Fail when the keyed figures disagree with the book's own sums, in every constituency and in the state."""
    rows = t["rows"]
    assert isinstance(rows, list) and len(rows) == 56
    state = t["state"]
    assert isinstance(state, dict)
    column = [0] * (len(PARTIES) + 1)
    totals = {name: 0 for name in ("registered", "cast", "invalid", "valid", "seats")}
    for row in rows:
        votes = row["votes"]
        assert len(votes) == len(PARTIES) + 1
        added = sum(figure(v) for v in votes if v is not None)
        valid = figure(row["valid"])
        if added != valid:
            raise ValueError(f"constituency {row['code']}: the parties add up to {added:,}, the book prints {valid:,}")
        if valid + figure(row["invalid"]) != figure(row["cast"]):
            raise ValueError(f"constituency {row['code']}: valid and invalid ballots do not add up to the ballots cast")
        if figure(row["cast"]) > figure(row["registered"]):
            raise ValueError(f"constituency {row['code']}: more ballots than registered voters")
        for i, v in enumerate(votes):
            column[i] += 0 if v is None else figure(v)
        for name in totals:
            totals[name] += figure(row[name])
        seats = SEATS[row["code"]]
        if sum(a + b for a, b in seats.values()) != figure(row["seats"]):
            raise ValueError(f"constituency {row['code']}: the party seats do not add up to its seats")
    for name, added in totals.items():
        if added != figure(state[name]):
            raise ValueError(
                f"the constituencies' {name} add up to {added:,}, the state row prints {figure(state[name]):,}"
            )
    for i, v in enumerate(state["votes"]):
        printed = 0 if v is None else figure(v)
        if column[i] != printed:
            raise ValueError(
                f"party column {i + 1}: the constituencies add up to {column[i]:,}, the state row prints {printed:,}"
            )
    if sum(figure(v) for v in state["votes"] if v is not None) != figure(state["valid"]):
        raise ValueError("the state row's parties do not add up to its valid ballots")
    first = sum(a for s in SEATS.values() for a, _ in s.values())
    second = sum(b for s in SEATS.values() for _, b in s.values())
    if (first, second) != (t["first_seats"], t["second_seats"]):
        raise ValueError(
            f"the seats add up to {first} and {second}, the book prints {t['first_seats']} and {t['second_seats']}"
        )
    if sum(STATE.values()) != STATE_SEATS:
        raise ValueError("the state seats do not add up to 12")


def render_pages() -> None:
    """Write the 100 dpi page images the figures cite, for upload beside the packages."""
    artifact = raw.cached(SOURCE, mirror())
    out = DATA / "pages" / SOURCE
    out.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(artifact.path) as document:
        for number in PAGES[:2]:
            target = out / f"{artifact.sha256}-{number}.png"
            if not target.exists():
                document[number - 1].get_pixmap(dpi=DPI // 3).save(target)


def geo() -> Package:
    p = Package(
        GEO, SOURCE, "The scheme of the electoral constituencies that Greece's scanned parliamentary results use"
    )
    p.external.add(GREECE)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=GREECE,
        kind="electoral_district",
        rank=1,
        noun="electoral constituency",
    )
    return p


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Ministry of Interior's scanned results book of Greece's parliamentary election of 5 November 1989, and its parties",
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
    p.artifact(raw.cached(SOURCE, mirror()), YPES)
    for number, name, short in PARTIES:
        id = party(number)
        p.add("party", id=id, kind="party", polity_id=GREECE)
        p.add("external_id", entity_id=id, scheme="gr.ypes_parl_archive.party", value=f"1989-11:{number}")
        full = f"{name} ({short})" if short else name
        p.name(id, full, "el", "official")
        if short:
            p.name(id, short, "el", "abbreviation", False)
    return p


def party(number: int) -> str:
    return key("party", KEY, number)


def build() -> Package:
    t = table()
    verify(t)
    artifact = raw.cached(SOURCE, mirror())
    p = Package(
        PACKAGE,
        SOURCE,
        "The 5 November 1989 parliamentary election in Greece by electoral constituency, from the Ministry of "
        "Interior's scanned results book",
        depends=[GEO, REGISTRY],
    )
    p.external |= {GREECE, TERRITORY, PARLIAMENT}
    p.uses(artifact)
    sizes: dict[int, tuple[float, float]] = {}
    pages: dict[int, str] = {}
    with pymupdf.open(artifact.path) as document:
        for number in PAGES[:2]:
            rect = document[number - 1].rect
            sizes[number] = (rect.width * DPI / 72, rect.height * DPI / 72)
    for number in PAGES[:2]:
        pages[number] = p.id("page", KEY, number)
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
    election = key("election", KEY)
    p.add(
        "election",
        id=election,
        polity_id=GREECE,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "November 1989 Greek parliamentary election", "en", "official")
    p.name(election, "Βουλευτικές εκλογές Νοεμβρίου 1989", "el", "native", False)
    p.add("slug", path=PATH, entity_id=election, is_current=True)

    order = sorted(
        range(len(PARTIES) + 1),
        key=lambda i: (-figure(t["state"]["votes"][i] or [0]), i),
    )
    position = {i: n for n, i in enumerate(order, 1)}
    rows: list[tuple[str, str, str | None, int, list[int]]] = []

    def contest(
        scope: object, title: str, greek: str, district: str, magnitude: int, method: str
    ) -> tuple[str, str, dict[int, str]]:
        c = key("contest", KEY, scope)
        p.add(
            "contest",
            id=c,
            election_id=election,
            kind="list_allocation",
            body_id=PARLIAMENT,
            district_area_id=district,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(c, title, "en", "official")
        p.name(c, greek, "el", "native", False)
        p.add("slug", path=f"{PATH}/{changeset.slug(title)}", entity_id=c, is_current=True)
        rs = key("result_set", KEY, scope)
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=YPES,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=method, lang="en")
        p.add("note", id=key("note", rs, "invalid"), entity_id=rs, kind="caveat", body_md=INVALID_NOTE, lang="en")
        options: dict[int, str] = {}
        for i in order:
            o = options[i] = key("option", KEY, scope, i + 1)
            independents = i == len(PARTIES)
            p.add(
                "contest_option",
                id=o,
                contest_id=c,
                kind="others" if independents else "list",
                party_id=None if independents else party(i + 1),
                ballot_position=position[i],
                ballot_label=INDEPENDENTS if independents else _label(i),
            )
        return c, rs, options

    def put(rs: str, unit: str, options: dict[int, str], row: dict[str, Any]) -> None:
        for name, measure in (("registered", REGISTERED), ("cast", CAST), ("valid", VALID), ("invalid", INVALID)):
            rows.append((rs, unit, None, measure, row[name]))
        for i, v in enumerate(row["votes"]):
            if v is not None:
                rows.append((rs, unit, options[i], VOTES, v))

    names = {r["code"]: r["name"] for r in t["rows"]}
    del names
    for row in t["rows"]:
        code = row["code"]
        p.external.add(district_area(code))
        _, rs, options = contest(
            code, row["english"], row["name"], district_area(code), figure(row["seats"]), METHOD + " " + SEAT_METHOD
        )
        unit = key("unit", KEY, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GREECE,
            scheme_code=SCHEME,
            code=f"{DAY}:{code}",
            area_relation="equals",
            area_id=district_area(code),
            kind="standard",
        )
        put(rs, unit, options, row)
        for number, (first, second) in sorted(SEATS[code].items()):
            if first + second:
                p.add("outcome", result_set_id=rs, option_id=options[number - 1], kind="won", seats=first + second)
    _, rs, options = contest(
        "state", "State seats", "Έδρες επικρατείας", TERRITORY, STATE_SEATS, METHOD + " " + SEAT_METHOD
    )
    unit = key("unit", KEY, "state")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=GREECE,
        scheme_code=SCHEME,
        code=f"{DAY}:state",
        area_relation="equals",
        area_id=TERRITORY,
        kind="aggregate",
    )
    state = t["state"]
    put(rs, unit, options, state)
    for number, seats in sorted(STATE.items()):
        p.add("outcome", result_set_id=rs, option_id=options[number - 1], kind="won", seats=seats)
    for name, measure in (("registered", REGISTERED), ("cast", CAST), ("valid", VALID), ("invalid", INVALID)):
        p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=figure(state[name]))
    for i, v in enumerate(state["votes"]):
        if v is not None:
            p.add("declared", result_set_id=rs, option_id=options[i], measure=VOTES, value=figure(v))

    frame = pl.DataFrame(
        [
            (
                rs,
                unit,
                option,
                measure,
                cell[0],
                pages[cell[1]],
                cell[2] / sizes[cell[1]][0],
                cell[3] / sizes[cell[1]][1],
                (cell[2] + cell[4]) / sizes[cell[1]][0],
                (cell[3] + cell[5]) / sizes[cell[1]][1],
            )
            for rs, unit, option, measure, cell in rows
        ],
        schema=["result_set_id", "unit_id", "option_id", "measure", "value", "page_id", "x0", "y0", "x1", "y1"],
        orient="row",
    ).with_columns(
        channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), measure=pl.col("measure").cast(pl.Int16)
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
    return p


def _label(i: int) -> str:
    _, name, short = PARTIES[i]
    return f"{name} ({short})" if short else name
