"""Greek elections of 1926 to 1951 from the General Statistical Service's printed election volumes.

The Hellenic Statistical Authority (ELSTAT) keeps the volumes in its digital library as scans without a text layer,
one PDF per volume (`GRESYE_02_2101_000NN`). Each volume opens with general tables that give, for every electoral
constituency, the registered electors, the voters, the invalid ballots, the valid votes of each party, and the seats
each party won, with subtotals for each court of appeal and a total for the state. Later pages break the votes down by
municipality and community.

The general tables were read from 300 dpi renders of the pages and transcribed into `elstat_tables/{name}.txt`, one
file per election. Each transcribed row keeps the band of the page it was read from (`@y0:y1`), and each table keeps
the edges of its columns, so every stored number points at its page and box. Parsing checks every row against the
totals the volume prints: party votes against the valid votes, party seats against the seats, the constituencies
against each printed subtotal and the state total, and voters less invalid ballots against the valid votes. A
mismatch raises, so a misread number never reaches a package.
"""

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "gr.elstat"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
SCHEME = "gr.electoral_district"
TABLES = Path(__file__).with_name("elstat_tables")
LIBRARY = "http://dlib.statistics.gr/portal/page/portal/ESYE/categoryyears?p_cat=14308576&p_topic=14308576"

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
MEASURES = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "invalid": INVALID, "blank": BLANK}


def volume_url(number: int) -> str:
    return f"http://dlib.statistics.gr/Book/GRESYE_02_2101_{number:05d}.pdf"


VOLUMES = range(1, 13)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "elstat")
BODIES = {
    "parliament": (key("body", "vouli"), "lower_house", "Hellenic Parliament", "Βουλή των Ελλήνων"),
    "assembly": (
        key("body", "ethnosyneleusi"),
        "constituent_assembly",
        "Fifth National Assembly",
        "Ε΄ Εθνική Συνέλευση",
    ),
    "senate": (key("body", "gerousia"), "upper_house", "Senate of Greece", "Γερουσία"),
}
KINDS = {
    "parliament": ("parliamentary", "parliamentary election", "Βουλευτικές εκλογές"),
    "assembly": ("national-assembly", "National Assembly election", "Εκλογές Εθνοσυνελεύσεως"),
    "senate": ("senate", "Senate election", "Εκλογές Γερουσίας"),
    "referendum": ("referendum", "referendum", "Δημοψήφισμα"),
}
METHOD = (
    "Transcribed from the general tables of the General Statistical Service's printed volume for this election "
    "(page {pages} of the scan in ELSTAT's digital library). The volume gives, for each electoral constituency, the "
    "registered electors, the electors who voted, the invalid ballots, the valid votes of each party, list, or "
    "independent candidate, and the seats each won. Every number was checked against the totals the volume prints: "
    "the party votes against the valid votes, the seats against the constituency's seats, the constituencies against "
    "the subtotal of each court of appeal and the total for the state, and the voters less the invalid ballots "
    "against the valid votes. The volume's later tables by municipality and community are not yet transcribed, so "
    "the constituency is the finest unit stored."
)


def fold(text: str) -> str:
    """Greek text without accents or case, for matching names across volumes."""
    stripped = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.replace("ς", "σ")).strip().casefold()


@dataclass
class Party:
    key: str
    kind: str
    greek: str
    french: str


@dataclass
class Row:
    code: str
    band: tuple[float, float] | None
    values: list[int | None]
    misprinted: set[str] = field(default_factory=set)
    """Columns of a printed subtotal that a misprint note above it owns."""


@dataclass
class Block:
    name: str
    volume: int
    page: int
    columns: list[str]
    edges: list[float]
    rows: list[Row] = field(default_factory=list)
    derived: set[str] = field(default_factory=set)
    """Columns the transcription computed rather than read; they are checked but not stored."""

    def span(self, j: int) -> tuple[float, float]:
        """Column j's left and right edges: from n + 1 shared edges, or from a pair per column."""
        if len(self.edges) == 2 * len(self.columns):
            return self.edges[2 * j], self.edges[2 * j + 1]
        return self.edges[j], self.edges[j + 1]


@dataclass
class Election:
    name: str
    family: str
    day: date
    parties: dict[str, Party]
    districts: dict[str, tuple[str, str]]
    blocks: list[Block]
    notes: list[str]
    tiers: dict[str, tuple[str, str, str]]
    """Units that allocate seats above the constituency, such as a court of appeal group's second distribution."""

    @property
    def volume(self) -> int:
        return self.blocks[0].volume


NAMES = ("1926", "1928", "1929s", "1932s", "1932", "1935", "1936", "1946", "1946r", "1950", "1951")


def names() -> list[str]:
    return [name for name in NAMES if (TABLES / f"{name}.txt").exists()]


@cache
def read(name: str) -> Election:
    family, day = "", date.min
    parties: dict[str, Party] = {}
    districts: dict[str, tuple[str, str]] = {}
    blocks: list[Block] = []
    notes: list[str] = []
    tiers: dict[str, tuple[str, str, str]] = {}
    pending: set[str] = set()
    for number, line in enumerate((TABLES / f"{name}.txt").read_text().splitlines(), 1):
        if line.startswith("# misprint:"):
            notes.append(line.removeprefix("# misprint:").strip())
            match = MISPRINT.match(notes[-1])
            if match is not None and match.group(2) == "=":
                pending.add(match.group(3) or "")
        text = line.split("#")[0].strip()
        if not text:
            continue
        head, _, rest = text.partition(" ")
        if head == "election":
            family, when = rest.split()
            day = date.fromisoformat(when)
        elif head == "party":
            spec, greek, french = (part.strip() for part in rest.split("|"))
            code, kind = spec.split()
            parties[code] = Party(code, kind, greek, french)
        elif head == "district":
            code, greek, french = (part.strip() for part in rest.split("|"))
            districts[code] = (greek, french)
        elif head == "tier":
            code, greek, french, english = (part.strip() for part in rest.split("|"))
            tiers[code] = (greek, french, english)
        elif head == "derived":
            blocks[-1].derived |= set(rest.split())
        elif head == "table":
            table, *options = rest.split()
            opts = dict(option.split("=") for option in options)
            blocks.append(Block(table, int(opts["volume"]), int(opts["page"]), [], []))
        elif head == "columns":
            blocks[-1].columns = rest.split()
        elif head == "x":
            blocks[-1].edges = [float(value) for value in rest.split()]
        elif head in ("y", "anchor"):
            continue
        else:
            fields = text.split()
            band = None
            if len(fields) > 1 and fields[1].startswith("@"):
                y0, y1 = fields.pop(1)[1:].split(":")
                band = (float(y0), float(y1))
            values = [None if value == "-" else int(value) for value in fields[1:]]
            if len(values) != len(blocks[-1].columns):
                raise ValueError(f"{name}.txt:{number}: {len(values)} values for {len(blocks[-1].columns)} columns")
            blocks[-1].rows.append(Row(fields[0], band, values, pending if fields[0] == "=" else set()))
            pending = set()
    election = Election(name, family, day, parties, districts, blocks, notes, tiers)
    _verify(election)
    return election


TABLE_NAMES = ("turnout", "votes", "seats")
MISPRINT = re.compile(r"^(\w+) ([A-Z]?\d+|[A-Z]|=)(?: \([^)]*\))?(?: (\w+))?:")


def misprints(e: Election) -> set[tuple[str, str, str | None]]:
    """The cells each `# misprint:` comment owns up to, as (table, row, column); a column of None means the row."""
    found: set[tuple[str, str, str | None]] = set()
    for note in e.notes:
        match = MISPRINT.match(note)
        if match is None:
            raise ValueError(f"{e.name}: a misprint note must start with '<table> <row> [<column>]:': {note}")
        found.add((match.group(1), match.group(2), match.group(3)))
    return found


def _verify(e: Election) -> None:
    """Raise unless every row agrees with the totals the volume prints, or a misprint note owns the difference."""
    owned = misprints(e)

    def excused(table: str, codes: list[str], column: str | None) -> bool:
        return any(
            t == table and c in codes and (column is None or col is None or col == column) for t, c, col in owned
        )

    def kind(code: str) -> str:
        return "tier" if code in e.tiers else "place" if territorial(code) else "body"

    problems: list[str] = []
    earlier: dict[str, list[tuple[Block, Row]]] = {}
    for block in e.blocks:
        if len(block.edges) not in (len(block.columns) + 1, 2 * len(block.columns)):
            problems.append(f"{block.name} p{block.page}: {len(block.edges)} edges for {len(block.columns)} columns")
        group: list[Row] = []
        everything: list[Row] = []
        subtotals: list[Row] = []
        for row in block.rows:
            if row.code in ("=", "=="):
                scope = group if row.code == "=" else everything
                codes = [r.code for r in scope]
                for j, column in enumerate(block.columns):
                    summed = sum(r.values[j] or 0 for r in scope)
                    if row.code == "==":
                        # a table split over several pages prints its total once, after its last part
                        found = [
                            r.values[b.columns.index(column)] or 0
                            for b, r in [*earlier.get(block.name, []), *((block, r) for r in everything)]
                            if column in b.columns and kind(r.code) in {kind(x.code) for x in everything}
                        ]
                        summed = sum(found)
                        codes = [r.code for _, r in earlier.get(block.name, [])] + codes
                    if (
                        summed != (row.values[j] or 0)
                        and not excused(block.name, codes, column)
                        and column not in row.misprinted
                    ):
                        problems.append(f"{block.name} p{block.page} {row.code} {column}: {summed} <> {row.values[j]}")
                    if row.code == "==" and subtotals and block.name not in earlier:
                        summed = sum(r.values[j] or 0 for r in subtotals)
                        if summed != (row.values[j] or 0) and not any(column in r.misprinted for r in subtotals):
                            problems.append(f"{block.name} p{block.page} == {column}: subtotals {summed}")
                if row.code == "=":
                    subtotals.append(row)
                    group = []
                continue
            if row.code in e.tiers and block.name != "seats":
                problems.append(f"{block.name} p{block.page}: tier row {row.code} outside a seats table")
            if row.code not in e.districts and row.code not in e.tiers:
                problems.append(f"{block.name} p{block.page}: row {row.code} names no district")
            if row.band is None:
                problems.append(f"{block.name} p{block.page}: row {row.code} has no band")
            group.append(row)
            everything.append(row)
        earlier.setdefault(block.name, []).extend((block, r) for r in everything)
    for code, figures in district_values(e).items():
        votes = [v for column, v in figures.items() if column.startswith("votes:")]
        if votes and "valid" in figures and sum(votes) != figures["valid"] and not excused("votes", [code], None):
            problems.append(f"{code}: party votes {sum(votes)} <> valid {figures['valid']}")
        seats = [v for column, v in figures.items() if column.startswith("seats:")]
        if seats and "seats" in figures and sum(seats) != figures["seats"] and not excused("seats", [code], None):
            problems.append(f"{code}: party seats {sum(seats)} <> seats {figures.get('seats')}")
        if {"cast", "invalid", "valid"} <= set(figures):
            balance = figures["cast"] - figures["invalid"] - figures.get("blank", 0) - figures["valid"]
            if balance and not any(c == code and col in ("valid", "cast", "invalid") for _, c, col in owned):
                problems.append(f"{code}: cast less invalid differs from valid by {balance}")
    for column in {c for b in e.blocks for c in b.columns if c not in MEASURES and c != "seats"}:
        if column not in e.parties:
            problems.append(f"column {column} names no party")
    if problems:
        raise ValueError(f"{e.name}: " + "; ".join(problems[:20]))


def district_values(e: Election) -> dict[str, dict[str, int]]:
    """Each constituency's figures by column, party columns prefixed with their table's name."""
    out: dict[str, dict[str, int]] = {}
    for block in e.blocks:
        for row in block.rows:
            if row.code in ("=", "=="):
                continue
            for column, value in zip(block.columns, row.values, strict=True):
                if value is None:
                    continue
                name = column if column in MEASURES or column == "seats" else f"{block.name}:{column}"
                out.setdefault(row.code, {})[name] = value
    return out


def national(e: Election) -> dict[str, int]:
    """The state totals the volume prints, by column."""
    out: dict[str, int] = {}
    for block in e.blocks:
        for row in block.rows:
            if row.code != "==":
                continue
            for column, value in zip(block.columns, row.values, strict=True):
                if value is not None and column not in block.derived:
                    name = column if column in MEASURES or column == "seats" else f"{block.name}:{column}"
                    out[name] = value
    return out


ALIASES = {
    "πρωην δημοσ πειραιωσ μετα των επαρχιων αιγινησ, τροιζηνιασ και κυθηρων, του δημου σπετσων και τησ κοινοτητοσ υδρασ": (
        "πρωην δημοσ πειραιωσ μετα των επαρχιων αιγινησ, τροιζηνιασ και κυθηρων και των δημων σπετσων και υδρασ"
    ),
    "προνομιουχοσ εκλογικη περιφερεια υδρασ": "προνομιουχοσ περιφερεια υδρασ",
    "προνομιουχοσ εκλογικη περιφερεια σπετσων": "προνομιουχοσ περιφερεια σπετσων",
    "προνομιουχοσ εκλογικη περιφερεια ψαρων": "προνομιουχοσ περιφερεια ψαρων",
    # the same constituency spelled differently from one volume to the next
    "τεωσ δημοσ αθηναιων": "πρωην δημοσ αθηναιων",
    "νομοσ τρικκαλων": "νομοσ τρικαλων",
    "νομοσ ρεθυμνου": "νομοσ ρεθυμνησ",
    "νομοσ καβαλλασ": "νομοσ καβαλασ",
    "εκλογικοσ συλλογοσ ισραηλιτων του δημου θεσσαλονικησ": "εκλογικοσ συλλογοσ ισραηλιτων θεσσαλονικησ",
    "πρωην δημοσ πειραιωσ μετα των επαρχιων αιγινησ, τροιζηνιασ, κυθηρων, του δημου σπετσων και τησ κοινοτητοσ υδρασ": (
        "πρωην δημοσ πειραιωσ μετα των επαρχιων αιγινησ, τροιζηνιασ και κυθηρων και των δημων σπετσων και υδρασ"
    ),
}
FRENCH = (
    (re.compile(r"^Provinces? (?:de la |de |d'|des )(.+)$"), "{} Province"),
    (re.compile(r"^Tribunal de 1ère instance (?:de la |de |d'|des )(.+)$"), "{} court of first instance district"),
    (re.compile(r"^Départements? (?:de la |de |d'|des )(.+)$"), "{} Prefecture"),
)


def territorial(code: str) -> bool:
    """Constituencies with a place; the Senate's seats for professional bodies and for the houses have none."""
    return code.isdigit()


def english_name(greek: str, french: str) -> str:
    """The constituency's English name: curated where the volumes' wording needs it, else from its French name."""
    found = ENGLISH.get(area_key(greek))
    if found is not None:
        return found
    for pattern, form in FRENCH:
        match = pattern.match(french)
        if match is not None:
            return form.format(match.group(1))
    raise ValueError(f"no English name for constituency {greek} ({french})")


def area_key(greek: str) -> str:
    folded = fold(greek)
    return ALIASES.get(folded, folded)


def area(greek: str) -> str:
    return key("area", area_key(greek))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in names())]


def fetch() -> None:
    for number in VOLUMES:
        raw.fetch(SOURCE, volume_url(number), suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for name in names():
        election(read(name)).write()


def emit() -> None:
    package.run("load", packages())


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Ελληνική Στατιστική Αρχή (ΕΛΣΤΑΤ)",
        title="Στατιστική των εκλογών, 1926–1951 (ΕΛΣΤΑΤ, Ψηφιακή Βιβλιοθήκη)",
        url=LIBRARY,
        license="Public sector information of the Hellenic Statistical Authority; reuse with the source acknowledged",
        license_url="https://www.statistics.gr/en/legal-notice",
        attribution="Hellenic Statistical Authority (ELSTAT), digital library",
    )


def artifact_id(number: int) -> str:
    stored = raw.cached(SOURCE, volume_url(number))
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{SOURCE_ID}\x1f{stored.storage_key}"))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The electoral constituencies of Greek elections from 1926 to 1951, as ELSTAT's election volumes name them",
    )
    p.external |= {GREECE, COUNTRY, SCHEME}
    add_source(p)
    seen: dict[str, tuple[str, str, date]] = {}
    for name in names():
        e = read(name)
        for code, (greek, french) in e.districts.items():
            if not territorial(code):
                continue
            seen.setdefault(area_key(greek), (greek, french, e.day))
            if e.day > seen[area_key(greek)][2]:
                seen[area_key(greek)] = (greek, french, e.day)
    codes: set[str] = set()
    for folded, (greek, french, _) in sorted(seen.items()):
        id = area(greek)
        p.add("area", id=id, scheme_code=SCHEME)
        code = f"elstat:{changeset.slug(english_name(greek, french))}"
        if code in codes:
            raise ValueError(f"two constituencies share the area code {code}")
        codes.add(code)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, greek, "el", "official")
        p.name(id, french, "fr", "official", False)
        p.name(id, english_name(greek, french), "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Greek parliaments, the 1927-1935 Senate, and the parties of ELSTAT's 1926-1951 election volumes",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(GREECE)
    for number in VOLUMES:
        try:
            p.artifact(raw.cached(SOURCE, volume_url(number)), SOURCE_ID)
        except LookupError:
            continue
    families = {read(name).family for name in names()}
    for family, (id, kind, english, greek) in BODIES.items():
        if family not in families:
            continue
        p.add("body", id=id, polity_id=GREECE, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, greek, "el", "native", False)
    parties: dict[str, list[tuple[date, Party]]] = {}
    for name in names():
        e = read(name)
        if e.family == "referendum":
            continue
        for party in e.parties.values():
            if party.kind == "list":
                parties.setdefault(fold(party.greek), []).append((e.day, party))
    for folded, uses in sorted(parties.items()):
        id = key("party", folded)
        p.add("party", id=id, kind="party", polity_id=GREECE)
        latest = max(uses, key=lambda use: use[0])[1]
        p.name(id, latest.greek, "el", "official")
        p.name(id, latest.french, "fr", "official", False)
    return p


def _pages(stored: raw.Artifact) -> dict[int, tuple[int, int]]:
    sizes: dict[int, tuple[int, int]] = {}
    out = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "9999", str(stored.path)], capture_output=True, text=True, check=True
    ).stdout
    for found in re.finditer(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", out):
        sizes[int(found.group(1))] = (round(float(found.group(2))), round(float(found.group(3))))
    return sizes


def election(e: Election) -> Package:
    slug, english_kind, greek_kind = KINDS[e.family]
    p = Package(
        f"{SOURCE}/{e.name}",
        SOURCE,
        f"The {e.day.year} Greek {english_kind} by electoral constituency from ELSTAT's printed volume",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GREECE, COUNTRY, SCHEME}
    stored = raw.cached(SOURCE, volume_url(e.volume))
    p.uses(stored)
    election_id = key("election", e.name)
    referendum = e.family == "referendum"
    p.add(
        "election",
        id=election_id,
        polity_id=GREECE,
        kind="referendum" if referendum else "general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    english = f"{e.day.year} Greek {english_kind}"
    p.name(election_id, english, "en", "official")
    p.name(election_id, f"{greek_kind} {e.day.year}", "el", "native", False)
    path = f"gr/{e.day}-{slug}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    sizes = _pages(stored)
    pages = sorted({block.page for block in e.blocks})
    page_ids = {page: key("page", e.volume, page) for page in pages}
    for page in pages:
        width, height = sizes[page]
        p.add(
            "source_page",
            id=page_ids[page],
            artifact_id=artifact_id(e.volume),
            page_number=page,
            image_key=f"pages/{SOURCE}/{stored.sha256}/{page}.png",
            width=width,
            height=height,
        )
    method = METHOD.format(pages=", ".join(str(page) for page in pages))
    figures = district_values(e)
    order = sorted(e.parties, key=lambda party: -national(e).get(f"votes:{party}", 0))
    position = {party: index for index, party in enumerate(order, 1)}
    units = {code: key("unit", e.name, code) for code in e.districts}
    for code, (greek, french) in e.districts.items():
        p.add(
            "reporting_unit",
            id=units[code],
            polity_id=GREECE,
            scheme_code=SCHEME,
            code=f"{e.day}:{code}",
            area_relation="equals" if territorial(code) else "unlocated",
            area_id=area(greek) if territorial(code) else None,
            kind="standard",
        )
        p.name(units[code], greek, "el", "official")
    # contest and result set of each row: one per constituency, or one for the state in a referendum
    sets: dict[str, str] = {}
    options: dict[tuple[str, str], str] = {}
    if referendum:
        contest = key("contest", e.name)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            district_area_id=COUNTRY,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, QUESTIONS[e.name][0], "en", "official")
        p.name(contest, QUESTIONS[e.name][1], "el", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(QUESTIONS[e.name][2])}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="plebiscite")
        result_set = _result_set(p, e, election_id, contest, "state", method)
        for party in order:
            option = options[("state", party)] = key("option", e.name, "state", party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position[party],
                ballot_label=e.parties[party].greek,
            )
            p.name(option, e.parties[party].greek, "el", "official")
            p.name(option, e.parties[party].french, "fr", "official", False)
        for code in e.districts:
            sets[code] = result_set
        totals = national(e)
        for column, value in sorted(totals.items()):
            if column in MEASURES:
                p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[column], value=value)
            elif column.startswith("votes:"):
                party = column.removeprefix("votes:")
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[("state", party)],
                    measure=VOTES,
                    value=value,
                )
    else:
        body = BODIES[e.family][0]
        for code, (greek, french) in e.districts.items():
            contest = key("contest", e.name, code)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="list_allocation",
                body_id=body,
                district_area_id=area(greek) if territorial(code) else None,
                magnitude=figures.get(code, {}).get("seats"),
                round_number=1,
                default_result_kind="counted",
            )
            title = english_name(greek, french)
            p.name(contest, title, "en", "official")
            p.name(contest, greek, "el", "native", False)
            p.add(
                "slug",
                path=f"{path}/{changeset.slug(title)}",
                entity_id=contest,
                is_current=True,
            )
            sets[code] = _result_set(p, e, election_id, contest, code, method)
            running = [
                party
                for party in order
                if f"votes:{party}" in figures.get(code, {}) or f"seats:{party}" in figures.get(code, {})
            ]
            for party in running:
                spec = e.parties[party]
                option = options[(code, party)] = key("option", e.name, code, party)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list" if spec.kind == "list" else "independents",
                    party_id=key("party", fold(spec.greek)) if spec.kind == "list" else None,
                    ballot_position=position[party],
                    ballot_label=spec.greek,
                )
                won = figures[code].get(f"seats:{party}")
                if won:
                    p.add("outcome", result_set_id=sets[code], option_id=option, kind="won", seats=won)
    if e.tiers:
        _tiers(p, e, election_id, path, method, figures, position)
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []
    for block in e.blocks:
        if block.name == "seats":
            continue
        for row in block.rows:
            if row.code in ("=", "==") or row.code in e.tiers or row.band is None:
                continue
            for j, (column, value) in enumerate(zip(block.columns, row.values, strict=True)):
                if value is None or column in block.derived:
                    continue
                if column in MEASURES:
                    option, measure = None, MEASURES[column]
                else:
                    option, measure = options[(("state" if referendum else row.code), column)], VOTES
                fact = {
                    "result_set_id": sets[row.code],
                    "unit_id": units[row.code],
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                }
                values.append({**fact, "flags": 0, "value": value})
                locators.append(
                    {
                        **fact,
                        "page_id": page_ids[block.page],
                        "source_id": None,
                        "x0": block.span(j)[0],
                        "y0": row.band[0],
                        "x1": block.span(j)[1],
                        "y1": row.band[1],
                    }
                )
    if e.notes:
        printed = "; ".join(note for note in e.notes if not note.startswith(tuple(f"{t} = " for t in TABLE_NAMES)))
        owned = misprints(e)
        cells = {(table, row, column) for table, row, column in owned if row != "="}
        codes = {"accounting.votes_valid"} if any(table == "votes" for table, _, _ in cells) else set()
        if any(column in ("valid", "cast", "invalid") for _, _, column in cells):
            codes.add("accounting.ballots_sum")
        for code in sorted(codes):
            p.accept(code, f"The volume's own figures disagree, kept as printed and noted on the result set: {printed}")
    for row in values:
        p.add("result_value", **row)
    for row in locators:
        p.add("value_locator", **row)
    return p


def _tiers(
    p: Package,
    e: Election,
    election_id: str,
    path: str,
    method: str,
    figures: dict[str, dict[str, int]],
    position: dict[str, int],
) -> None:
    """Contests for seats allocated above the constituency; they hold seats only, since the volume prints no votes."""
    body = BODIES[e.family][0]
    for code, (greek, french, english) in e.tiers.items():
        won = {
            column.removeprefix("seats:"): seats
            for column, seats in figures.get(code, {}).items()
            if column.startswith("seats:") and seats
        }
        contest = key("contest", e.name, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body,
            district_area_id=COUNTRY if code == "G" else None,
            magnitude=sum(won.values()),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, english, "en", "official")
        p.name(contest, greek, "el", "native", False)
        p.name(contest, french, "fr", "official", False)
        p.add("slug", path=f"{path}/{changeset.slug(english)}", entity_id=contest, is_current=True)
        result_set = key("result_set", e.name, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=None,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=TIER, lang="en")
        for party, seats in sorted(won.items(), key=lambda item: position[item[0]]):
            spec = e.parties[party]
            option = key("option", e.name, code, party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list" if spec.kind == "list" else "independents",
                party_id=key("party", fold(spec.greek)) if spec.kind == "list" else None,
                ballot_position=position[party],
                ballot_label=spec.greek,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)


TIER = (
    "Seats the volume's table of deputies elected assigns above the constituency: the seats of a later distribution "
    "of remainders, by court of appeal group or for the whole state. The volume prints no votes for these "
    "distributions, so the contest holds the seats each party won and no counts. The constituency contests hold only "
    "the seats of the first distribution."
)


def _result_set(p: Package, e: Election, election_id: str, contest: str, scope: str, method: str) -> str:
    result_set = key("result_set", e.name, scope)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    for index, text in enumerate(e.notes):
        match = MISPRINT.match(text)
        if scope != "state" and (match is None or match.group(2) != scope):
            continue
        p.add(
            "note",
            id=key("note", result_set, "misprint", index),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The printed volume is inconsistent here: {text}.",
            lang="en",
        )
    return result_set


QUESTIONS = {
    "1946r": (
        "Return of King George II",
        "Επάνοδος του Βασιλέως Γεωργίου Β΄",
        "return of the king",
    ),
}

ENGLISH: dict[str, str] = {
    "πρωην δημοσ αθηναιων": "Athens (former municipality)",
    "πρωην δημοσ πειραιωσ μετα των επαρχιων αιγινησ, τροιζηνιασ και κυθηρων και των δημων σπετσων και υδρασ": "Piraeus with Aegina, Troezenia, Kythira, Spetses, and Hydra",
    "υπολοιπον νομου αττικησ και βοιωτιασ": "Rest of Attica and Boeotia",
    "νομοσ φθιωτιδοσ και φωκιδοσ": "Phthiotis and Phocis",
    "νομοσ ευβοιασ": "Euboea",
    "νομοσ αργολιδοσ και κορινθιασ": "Argolis and Corinthia",
    "νομοσ αρκαδιασ": "Arcadia",
    "νομοσ λακωνιασ": "Laconia",
    "νομοσ μεσσηνιασ": "Messenia",
    "νομοσ αχαιασ": "Achaea",
    "νομοσ ηλειασ": "Elis",
    "νομοσ αιτωλιασ και ακαρνανιασ": "Aetolia-Acarnania",
    "νομοσ ζακυνθου": "Zakynthos",
    "νομοσ κεφαλληνιασ": "Cephalonia",
    "νομοσ κερκυρασ": "Corfu",
    "νομοσ ιωαννινων": "Ioannina",
    "νομοι πρεβεζησ και αρτησ": "Preveza and Arta",
    "νομοσ λαρισησ": "Larissa",
    "νομοσ τρικαλων": "Trikala",
    "νομοσ κοζανησ": "Kozani",
    "νομοσ θεσσαλονικησ": "Thessaloniki",
    "νομοσ ημαθιασ": "Imathia",
    "νομοσ πιεριασ": "Pieria",
    "νομοσ κιλκισ": "Kilkis",
    "νομοσ χαλκιδικησ": "Chalkidiki",
    "νομοσ πελλησ": "Pella",
    "νομοσ φλωρινησ": "Florina",
    "νομοσ σερρων": "Serres",
    "νομοσ κυκλαδων": "Cyclades",
    "νομοσ λεσβου": "Lesbos",
    "νομοσ σαμου": "Samos",
    "νομοσ χιου": "Chios",
    "γενικη διοικησισ δωδεκανησου": "Dodecanese",
    "νομοσ χανιων": "Chania",
    "νομοσ ηρακλειου": "Heraklion",
    "νομοσ λασηθιου": "Lasithi",
    "νομοσ ρεθυμνησ": "Rethymno",
    "νομοσ εβρου": "Evros",
    "νομοσ ροδοπησ": "Rhodope",
    "νομοσ δραμασ": "Drama",
    "νομοσ καβαλασ": "Kavala",
    "πρωην δημοσ πειραιωσ κλπ.": "Piraeus with neighboring provinces",
    "νομοσ αττικησ και βοιωτιασ": "Attica and Boeotia",
    "εκλογικοσ συλλογοσ ισραηλιτων θεσσαλονικησ": "Jewish electoral college of Thessaloniki",
    "εκλογικοσ συλλογοσ μουσουλμανων θρακησ": "Muslim electoral college of Thrace",
    "προνομιουχοσ περιφερεια υδρασ": "Hydra (privileged constituency)",
    "προνομιουχοσ περιφερεια σπετσων": "Spetses (privileged constituency)",
    "προνομιουχοσ περιφερεια ψαρων": "Psara (privileged constituency)",
    "πρωην δημοσ πειραιωσ": "Piraeus (former municipality)",
    "υπολοιπον επαρχιασ αττικησ": "Rest of Attica Province",
    "πρωτοδικειον πρεβεζησ μεθ' ολοκληρου τησ περιφερειασ πρωην υποδιοικησεωσ φιλιππιαδοσ": "Preveza court of first instance district with the former Philippiada subprefecture",
    "εμπορικα τμηματα των εμπορικων και βιομηχανικων επιμελητηριων": "Commercial sections of the chambers of commerce and industry",
    "βιομηχανικα τμηματα των εμπορικων και βιομηχανικων επιμελητηριων": "Industrial sections of the chambers of commerce and industry",
    "επαγγελματικα και βιοτεχνικα επιμελητηρια": "Chambers of trades and crafts",
    "ενωσισ ελληνων εφοπλιστων": "Union of Greek Shipowners",
    "γεωργικα επιμελητηρια": "Chambers of agriculture",
    "γενικη συνομοσπονδια των εργατων τησ ελλαδοσ": "General Confederation of Workers of Greece",
    "ακαδημια - πανεπιστημια": "Academy and universities",
    "πολυτεχνειον και τεχνικον επιμελητηριον": "Polytechnic and Technical Chamber",
    "εκλογη υπο τησ βουλησ και γερουσιασ": "Election by the Chamber and the Senate",
    "τεωσ δημοσ πειραιωσ μετα των επαρχιων αιγινησ, υδρασ, τροιζηνιασ και τησ νησου σπετσων": "Piraeus with Aegina, Hydra, Troezenia, and Spetses",
    "υπολοιπον νομου αττικησ και βοιωτιασ μετα την αφαιρεσιν των ανωτερω περιφερειων": "Rest of Attica and Boeotia (1926)",
    "νομοσ αργολιδοσ και κορινθιασ, πλην τησ επαρχιασ υδρασ και τησ νησου σπετσων": "Argolis and Corinthia without Hydra and Spetses",
    "νομοσ αχαιασ και ηλιδοσ": "Achaea and Elis",
    "νομοσ θεσσαλονικησ, πλην των περιφερειων των ειρηνοδικειων κατερινησ, βερροιασ και ναουσησ": "Thessaloniki without Katerini, Veria, and Naousa",
    "περιφερειαι των ειρηνοδικειων κατερινησ, βερροιασ και ναουσησ": "Katerini, Veria, and Naousa",
    "νομοσ αρτησ": "Arta",
    "νομοσ αττικησ": "Attica",
    "νομοσ βοιωτιασ": "Boeotia",
    "νομοσ ευρυτανιασ": "Evrytania",
    "νομοσ θεσπρωτιασ": "Thesprotia",
    "νομοσ καρδιτσησ": "Karditsa",
    "νομοσ καστοριασ": "Kastoria",
    "νομοσ λευκαδοσ": "Lefkada",
    "νομοσ μαγνησιασ": "Magnesia",
    "νομοσ ξανθησ": "Xanthi",
    "νομοσ πρεβεζησ": "Preveza",
    "νομοσ φθιωτιδοσ": "Phthiotis",
    "νομοσ φωκιδοσ": "Phocis",
}
