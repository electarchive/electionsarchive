"""Luxembourg's Chamber elections of 1919-1989 by constituency, from the Chamber's own record of verifying them.

After each election the Government sends the Chamber of Deputies the minutes of every constituency's central
bureau, and the Chamber's first sitting verifies its members' credentials. The compte rendu of that sitting, which
the Chamber's archive publishes as a PDF with a text layer, prints what the rapporteurs read out: for most
constituencies the ballots found, blank, invalid and valid, the lists' votes, the electoral quotient, and the deputies
elected on each list, sometimes with their votes. The figures were transcribed from the page images into
`chambre_1919_1989.json`; this module checks every constituency against its printed totals and quotient and locates
each value on the page through the PDF's words.

Until 1954 half the Chamber was renewed every three years, alternately the Sud and Est and the Centre and Nord, with
general elections after the dissolutions of 1919, 1925, and 1945. The constituencies, the Chamber, and the four
constituency areas are those `lu.elections` loads; the parties and people are this source's own.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.lu import elections
from ea.sources.lu.model import fold, slug

SOURCE = "lu.chd"
ARCHIVE = "https://wdocs-pub.chd.lu/docs/archive"
DATA = Path(__file__).with_name("chambre_1919_1989.json")
LUXEMBOURG = elections.LUXEMBOURG
COUNTRY = elections.COUNTRY
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK, CANDIDATE_VOTES = 1, 2, 3, 4, 5, 6, 10
STATS = {"registered": REGISTERED, "cast": CAST, "valid": VALID, "invalid": INVALID, "blank": BLANK}
DISTRICTS = ("Sud", "Est", "Centre", "Nord")

# The compte rendu of each verification sitting, by the archive's document path. Where a sitting runs over two
# documents, both are listed under the election's own key.
DOCS = {
    "1919": "2d/25/80092005",
    "1922": "2c/07/80096007",
    "1925a": "1c/0f/80099087",
    "1928a": "29/05/80104005",
    "1928b": "29/07/80104007",
    "1931": "17/3d/80107005",
    "1934a": "06/35/80110005",
    "1937": "35/2d/80113005",
    "1945": "05/3d/3125629",
    "1948": "33/26/3009766",
    "1951": "32/06/3009670",
    "1954": "18/26/3016230",
    "1959": "1d/0b/3139403",
    "1964": "0e/10/3089296",
    "1969": "30/12/3140626",
    "1974": "08/0b/3097099",
    "1979": "25/10/3144016",
    "1984": "1a/29/3081897",
    "1989": "3e/25/3035045",
}
TITLES = {
    "1919": "Vérification des pouvoirs, 4 novembre 1919",
    "1922": "Vérification des pouvoirs et assermentation des députés nouvellement élus, 4 juillet 1922",
    "1925a": "Vérification des pouvoirs des 1ère, 2ème et 4ème circonscriptions électorales, 26 mars 1925",
    "1928a": "Vérification des pouvoirs, 6 novembre 1928",
    "1928b": "Vérification des pouvoirs: continuation et fin de la discussion, 7 novembre 1928",
    "1931": "Vérification des pouvoirs, 10 novembre 1931",
    "1934a": "Vérification des pouvoirs (1ère et 2ème circonscriptions), 6 novembre 1934",
    "1937": "Vérification des pouvoirs des 3ème et 4ème circonscriptions, 9 novembre 1937",
    "1945": "Vérification des pouvoirs (1re, 2me, 3me et 4me circonscription), 16 novembre 1945",
    "1948": "Vérification des pouvoirs (1er et 2me circonscription), 20 juillet 1948",
    "1951": "Vérification des pouvoirs (IIème et IVème circonscription électorale), 10 juillet 1951",
    "1954": "Vérification des pouvoirs, 6 juillet 1954",
    "1959": "Vérification des pouvoirs, 5 mars 1959",
    "1964": "Vérification des pouvoirs, 21 juillet 1964",
    "1969": "Vérification des pouvoirs, 5 février 1969",
    "1974": "Vérification des pouvoirs, 27 juin 1974",
    "1979": "Validation des élections législatives et assermentation des élus, 6 juillet 1979",
    "1984": "Vérification des pouvoirs, 16 juillet 1984",
    "1989": "Vérification des pouvoirs, 18 juillet 1989",
}

METHOD = (
    "Transcribed from the compte rendu of the Chamber of Deputies' sitting that verified the election ({title}), "
    "which prints what the rapporteurs read from each constituency's central bureau minutes. The constituency is the "
    "finest unit the record gives. A voter has as many votes as the constituency has seats and may spread them "
    "across lists, so a list's votes are the votes cast for its candidates and the lists' votes add up to far more "
    "than the valid ballots. Only the figures the sitting prints are stored; where it names the deputies elected on "
    "each list they are stored as elected, with their votes where it gives them."
)
SYSTEM_NOTE = (
    "From 1919 the Chamber of Deputies is elected by list proportional representation with the Hagenbach-Bischoff "
    "method in four constituencies (Sud, Est, Centre, and Nord), each with one deputy per 5,500 inhabitants until "
    "1984 and a fixed number thereafter. Each voter has as many votes as the constituency has seats and may vote for "
    "a whole list or spread the votes across lists, giving a candidate at most two. Until 1954 half the Chamber was "
    "renewed every three years, alternately in the Sud and Est and in the Centre and Nord."
)

# Parties, by the labels the sittings print for their lists. Each is this source's own; a label printed in another
# language or abbreviated maps to the same party.
PARTIES: dict[str, tuple[str, str | None, str | None]] = {
    # key: (native name, abbreviation, English name)
    "droite": ("Parti de la droite", None, "Party of the Right"),
    "csv": ("Parti chrétien-social", "CSV", "Christian Social People's Party"),
    "lsap": ("Parti ouvrier socialiste luxembourgeois", "POSL", "Luxembourg Socialist Workers' Party"),
    "kpl": ("Parti communiste", "PC", "Communist Party of Luxembourg"),
    "dp": ("Parti démocratique", "PD", "Democratic Party"),
    "gd": ("Groupement démocratique", None, None),
    "psd": ("Parti social-démocrate", "PSD", "Social Democratic Party"),
    "lcr": ("Ligue communiste révolutionnaire", "LCR", "Revolutionary Communist League"),
    "pll": ("Parti libéral luxembourgeois", "LP", None),
    "mip": ("Mouvement indépendant populaire", None, None),
    "liberal-1937": ("Parti libéral", None, None),
    "liberal-1964": ("Parti libéral", None, None),
    "uni": ("Union nationale indépendante", None, None),
    "gauches": ("Liste des gauches", None, None),
    "gauche-independante": ("Gauche indépendante", None, None),
    "radical-socialiste": ("Parti radical-socialiste", None, None),
    "radical": ("Parti radical", None, None),
    "radical-liberal": ("Parti radical-libéral", None, None),
    "independants-1931": ("Groupe des indépendants", None, None),
    "independants-1934": ("Parti des indépendants", None, None),
    "progressiste-nord": ("Parti progressiste-démocrate du Nord", None, None),
    "agriculteurs": ("Parti des agriculteurs et des classes moyennes", None, None),
    "alw": ("Alternativ Lëscht Wiert Iech", None, None),
    "ef": ("EF", "EF", None),
    "gremling": ("Liste Jean Gremling", None, None),
    "club-independants": ("Club des Indépendants", None, None),
    "psi": ("PSI", "PSI", None),
    "gap": ("Gréng Alternativ Partei", "GAP", "Green Alternative Party"),
}
LABELS = {
    "droite": ("Droite", "Parti de la droite"),
    "csv": (
        "Parti chrétien-social",
        "Parti chrétien social",
        "CSV",
        "PCS",
        "Chrëschtlech-sozial Partei",
        "Chrëschtlech-sozial Vollekspartei",
    ),
    "lsap": (
        "Parti ouvrier",
        "Parti ouvrier socialiste",
        "Parti ouvrier socialiste luxembourgeois",
        "Parti ouvrier-socialiste luxembourgeois",
        "POSL",
        "Sozialisten",
        "Sozialistesch Arbechterpartei",
    ),
    "kpl": ("Parti communiste", "PC", "Kommunisten", "Kommunistesch Partei"),
    "dp": ("Parti démocratique", "PD", "Demokratesch Partei"),
    "gd": ("Groupement démocratique",),
    "psd": ("Parti social-démocrate", "PSD", "SDP", "Sozialdemokraten", "Sozial-demokratesch Partei"),
    "lcr": ("Ligue communiste révolutionnaire", "LCR"),
    "pll": ("Parti libéral luxembourgeois", "LP"),
    "mip": ("Mouvement indépendant populaire",),
    "uni": ("Union nationale indépendante",),
    "gauches": ("Liste des gauches",),
    "gauche-independante": ("Gauche indépendante",),
    "radical-socialiste": ("Parti radical-socialiste",),
    "radical": ("Parti radical",),
    "radical-liberal": ("Parti radical-libéral",),
    "independants-1931": ("Groupe des indépendants",),
    "independants-1934": ("Parti des indépendants",),
    "progressiste-nord": ("Parti progressiste-démocrate du Nord",),
    "agriculteurs": ("Parti des agriculteurs et des classes moyennes", "Parti des paysans et classes moyennes"),
    "alw": ("Alternativ Lëscht Wiert Iech",),
    "ef": ("EF",),
    "gremling": ("Liste Jean Gremling",),
    "club-independants": ("Club des Indépendants",),
    "psi": ("PSI",),
    "gap": ("GAP", "Gréng Alternativ Partei"),
}
BY_LABEL = {fold(label): party for party, labels in LABELS.items() for label in labels}


# Findings the printed figures cause, by election.
ACCEPTED = {
    date(1984, 6, 17): {
        "accounting.ballots_sum": "The Chamber's record prints the Sud's ballots as 77,732 found, 3,103 blank, 1,542 "
        "invalid (4,646 together) and 73,086 valid; blank, invalid, and valid add up to 77,731, and the figures are "
        "stored as printed.",
        "outcomes.list_members": "The Sud's communist list (list 1) won 2 seats; a blot on the scanned page hides the "
        "surname of its second deputy, so only René Urbany is stored as elected.",
    },
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "chamber-1919")


@dataclass
class Listing:
    number: int | None
    label: str | None
    votes: int | None
    seats: int | None
    elected: list[tuple[str, int | None]]

    @property
    def tag(self) -> str:
        return str(self.number) if self.number is not None else slug(self.label or "")

    @property
    def ballot_label(self) -> str:
        return self.label or f"Liste {self.number}"


@dataclass
class Race:
    district: str
    tag: str
    title: str | None
    doc: str
    stats: dict[str, int]
    votes: int | None
    quotient: int | None
    lists: list[Listing]

    @property
    def magnitude(self) -> int | None:
        if self.lists and all(entry.seats is not None for entry in self.lists):
            return sum(entry.seats or 0 for entry in self.lists)
        return None


@dataclass
class Election:
    day: date
    renewal: str
    doc: str
    notes: list[str]
    races: list[Race] = field(default_factory=list)

    @property
    def docs(self) -> list[str]:
        return sorted({self.doc, *(race.doc for race in self.races)})


def load() -> list[Election]:
    found = []
    for e in json.loads(DATA.read_text(encoding="utf-8")):
        races = [
            Race(
                district=r["district"],
                tag=r.get("tag", slug(r["district"])),
                title=r.get("title"),
                doc=r.get("doc", e["doc"]),
                stats={name: r[name] for name in STATS if name in r},
                votes=r.get("votes"),
                quotient=r.get("quotient"),
                lists=[
                    Listing(
                        number=entry.get("n"),
                        label=entry.get("label"),
                        votes=entry.get("votes"),
                        seats=entry.get("seats"),
                        elected=[(name, votes) for name, votes in entry["elected"]],
                    )
                    for entry in r["lists"]
                ],
            )
            for r in e["contests"]
        ]
        found.append(Election(date.fromisoformat(e["date"]), e["renewal"], e["doc"], e["notes"], races))
    return found


def url(doc: str) -> str:
    return f"{ARCHIVE}/{DOCS[doc]}_pdf"


def not_pdf(path: Path) -> None:
    if not path.read_bytes()[:5].startswith(b"%PDF"):
        raise ValueError(f"{path}: the archive did not answer with a PDF")


def fetch() -> None:
    for doc in DOCS:
        raw.fetch(SOURCE, url(doc), suffix=".pdf", check=not_pdf)


def party_of(label: str | None, day: date) -> str | None:
    if label is None:
        return None
    if fold(label) == fold("Parti libéral"):
        return "liberal-1937" if day.year < 1945 else "liberal-1964"
    found = BY_LABEL.get(fold(label))
    if found is None:
        raise ValueError(f"{day}: no party for the list label {label!r}")
    return found


def check(e: Election) -> list[str]:
    """Raise on any constituency whose printed figures disagree with each other; return the softer warnings."""
    warnings = []
    for race in e.races:
        where = f"{e.day} {race.district}"
        s = race.stats
        if {"blank", "invalid", "valid", "cast"} <= set(s) and s["blank"] + s["invalid"] + s["valid"] != s["cast"]:
            warnings.append(f"{where}: blank, invalid, and valid add up to {s['blank'] + s['invalid'] + s['valid']}")
        if {"invalid", "valid", "cast"} <= set(s) and "blank" not in s and s["invalid"] + s["valid"] != s["cast"]:
            warnings.append(f"{where}: invalid and valid do not add up to ballots cast")
        listed = [entry.votes for entry in race.lists]
        added = sum(v or 0 for v in listed)
        if race.votes is not None and listed and all(v is not None for v in listed) and added != race.votes:
            raise ValueError(f"{where}: lists add up to {sum(v or 0 for v in listed)}, not {race.votes}")
        for entry in race.lists:
            if entry.seats is not None and len(entry.elected) > entry.seats:
                raise ValueError(f"{where}: list {entry.tag} names more elected than its {entry.seats} seats")
        magnitude = race.magnitude
        if race.votes is not None and race.quotient is not None and magnitude:
            expected = race.votes // (magnitude + 1) + 1
            if abs(expected - race.quotient) > 1:
                if race.quotient * (magnitude + 1) > race.votes + magnitude + 1:
                    warnings.append(f"{where}: printed quotient {race.quotient}, the votes give {expected}")
                else:
                    raise ValueError(f"{where}: quotient {race.quotient} does not fit {magnitude} seats")
            elif expected != race.quotient:
                warnings.append(f"{where}: printed quotient {race.quotient}, the votes give {expected}")
        if magnitude and "valid" in s and race.votes is not None and race.votes > s["valid"] * magnitude:
            raise ValueError(f"{where}: {race.votes} votes exceed {magnitude} per valid ballot")
    return warnings


# Locating values on the page.


Box = tuple[float, float, float, float]


@dataclass
class Word:
    page: int
    text: str
    box: Box

    @property
    def digits(self) -> str:
        return re.sub(r"\D", "", self.text)


@dataclass
class Page:
    number: int
    width: float
    height: float
    words: list[Word]


def pages(doc: str) -> list[Page]:
    """Every page's words and their boxes in PDF points, from the PDF's text layer."""
    path = raw.cached(SOURCE, url(doc)).path
    html = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True, text=True).stdout
    found: list[Page] = []
    for number, (head, body) in enumerate(re.findall(r"<page ([^>]*)>(.*?)</page>", html, flags=re.DOTALL), start=1):
        size = re.search(r'width="([\d.]+)" height="([\d.]+)"', head)
        if size is None:
            raise ValueError(f"{doc}: page {number} has no size")
        width, height = float(size.group(1)), float(size.group(2))
        words = [
            Word(number, unescape(text), (float(x0), float(y0), float(x1), float(y1)))
            for x0, y0, x1, y1, text in re.findall(
                r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', body
            )
        ]
        found.append(Page(number, width, height, words))
    return found


class Locator:
    """Finds each printed number among a document's words, in reading order, each word used once."""

    def __init__(self, docs: list[str]) -> None:
        self.words: list[tuple[str, Word]] = [(doc, w) for doc in docs for page in pages(doc) for w in page.words]
        self.used: set[int] = set()

    def find(self, value: int) -> tuple[str, int, Box] | None:
        digits = str(value)
        if len(digits) < 4:
            return None
        for i in range(len(self.words)):
            if i in self.used:
                continue
            doc, first = self.words[i]
            if not first.digits or not digits.startswith(first.digits):
                continue
            joined, span = "", []
            for j in range(i, min(i + 3, len(self.words))):
                d, w = self.words[j]
                if d != doc or w.page != first.page or j in self.used or not re.fullmatch(r"[\d.,;:)(]*", w.text):
                    break
                joined += w.digits
                span.append(j)
                if joined == digits:
                    boxes = [self.words[k][1].box for k in span]
                    self.used |= set(span)
                    box = (
                        min(b[0] for b in boxes),
                        min(b[1] for b in boxes),
                        max(b[2] for b in boxes),
                        max(b[3] for b in boxes),
                    )
                    return doc, first.page, box
                if not digits.startswith(joined):
                    break
        return None


# Packages.


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in load())]


def parse() -> None:
    registry().write()
    for e in load():
        for warning in check(e):
            print(f"warning: {warning}", flush=True)
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Chambre des Députés du Grand-Duché de Luxembourg",
        title="Compte rendu des séances publiques: vérification des pouvoirs après les élections législatives, "
        "1919-1989",
        url="https://www.chd.lu/fr/searchactivities",
        license="Public parliamentary record of the Chamber of Deputies, published in its online archive; no licence "
        "stated",
        license_url="https://www.chd.lu/fr/notice-legale",
        attribution="Chambre des Députés du Grand-Duché de Luxembourg, archives",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Chamber of Deputies' archive as a source, the Chamber's electoral system from 1919, and the parties whose "
        "lists the verification sittings of 1919-1989 name",
    )
    p.external.add(LUXEMBOURG)
    _source(p)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LUXEMBOURG,
        valid_from=date(1919, 10, 26),
        rules={"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    used = {party_of(entry.label, e.day) for e in load() for race in e.races for entry in race.lists if entry.label}
    for party in sorted(p for p in used if p):
        native, abbreviation, english = PARTIES[party]
        id = key("party", party)
        p.add("party", id=id, kind="party", polity_id=LUXEMBOURG)
        p.name(id, native, "fr", "official")
        if abbreviation and abbreviation != native:
            p.name(id, abbreviation, "fr", "abbreviation", False)
        if english:
            p.name(id, english, "en", "english", False)
    return p


PARTIAL = {
    1922: "Centre, Nord",
    1928: "Sud, Est",
    1931: "Centre, Nord",
    1934: "Sud, Est",
    1937: "Centre, Nord",
    1948: "Sud, Est",
    1951: "Centre, Nord",
}


def election(e: Election) -> Package:
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"Luxembourg's Chamber election of {e.day} by constituency, from the Chamber's verification sitting",
        depends=[f"{SOURCE}/registry", f"{elections.SOURCE}/geo", f"{elections.SOURCE}/registry"],
    )
    p.external |= {LUXEMBOURG, COUNTRY, "lu.circonscription"}
    year = e.day.year
    locator = Locator(e.docs)
    artifacts: dict[str, str] = {}
    for doc in e.docs:
        artifact = raw.cached(SOURCE, url(doc))
        p.uses(artifact)
        artifacts[doc] = p.artifact(artifact, SOURCE_ID)
    page_sizes = {(doc, page.number): (page.width, page.height) for doc in e.docs for page in pages(doc)}
    page_ids: dict[tuple[str, int], str] = {}

    def page_id(doc: str, number: int) -> str:
        if (doc, number) not in page_ids:
            id = page_ids[(doc, number)] = key("page", doc, number)
            width, height = page_sizes[(doc, number)]
            p.add(
                "source_page",
                id=id,
                artifact_id=artifacts[doc],
                page_number=number,
                image_key=f"pages/{SOURCE}/{doc}/{number}.png",
                width=round(width),
                height=round(height),
            )
        return page_ids[(doc, number)]

    election_id = key("election", e.day)
    p.add(
        "election",
        id=election_id,
        polity_id=LUXEMBOURG,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    if e.renewal == "general":
        english = f"{year} Luxembourg legislative election"
        french = f"Élections législatives {year}"
    else:
        english = f"{year} Luxembourg legislative election (partial renewal: {PARTIAL[year]})"
        french = f"Élections législatives partielles {year}"
    p.name(election_id, english, "en", "official")
    p.name(election_id, french, "fr", "native", False)
    path = f"lu/{e.day}-legislative"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add(
        "citation",
        id=key("citation", election_id),
        entity_id=election_id,
        source_id=SOURCE_ID,
        locator=json.dumps({"documents": [url(doc) for doc in e.docs]}),
    )
    units: dict[str, str] = {}
    for race in e.races:
        district = elections.area("lu.circonscription", race.district)
        if race.district not in units:
            unit = units[race.district] = key("unit", e.day, race.district)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=LUXEMBOURG,
                scheme_code="lu.circonscription",
                code=f"{e.day}:{slug(race.district)}",
                area_relation="equals",
                area_id=district,
                kind="standard",
            )
            p.name(unit, race.district, "fr", "official")
        unit = units[race.district]
        contest = key("contest", e.day, race.tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=elections.CHAMBER,
            district_area_id=district,
            magnitude=race.magnitude,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        suffix = f" ({race.title})" if race.title else ""
        p.name(contest, f"{english}, {race.district} constituency{suffix}", "en", "official")
        p.add("slug", path=f"{path}/{race.tag}", entity_id=contest, is_current=True)
        result_set = key("result_set", e.day, race.tag)
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
            # A sitting that prints only the seats each list won gives no counted values at all.
            completeness=1.0 if race.stats or any(entry.votes is not None for entry in race.lists) else 0.0,
        )
        body = " ".join([METHOD.format(title=TITLES[race.doc]), *e.notes])
        if race.quotient is not None:
            body += f" The sitting gives the electoral quotient as {race.quotient:,}."
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=SOURCE_ID,
            locator=json.dumps({"url": url(race.doc), "title": TITLES[race.doc]}),
        )

        def value(option: str | None, measure: int, number: int) -> None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=number,
            )
            found = locator.find(number)
            if found is None:
                return
            doc, page, box = found
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page_id(doc, page),
                source_id=SOURCE_ID,
                x0=box[0],
                y0=box[1],
                x1=box[2],
                y1=box[3],
            )

        for name, measure in STATS.items():
            if name in race.stats:
                value(None, measure, race.stats[name])
        for entry in race.lists:
            option = key("option", e.day, race.tag, entry.tag)
            party = party_of(entry.label, e.day)
            party_id = key("party", party) if party else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id,
                ballot_position=entry.number,
                ballot_label=entry.ballot_label,
            )
            if entry.votes is not None:
                value(option, VOTES, entry.votes)
            if entry.seats is not None:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="won" if entry.seats else "lost",
                    seats=entry.seats,
                )
            for rank, (who, votes) in enumerate(entry.elected, start=1):
                person = key("person", e.day, race.tag, entry.tag, who)
                p.add("person", id=person)
                p.name(person, who, "fr", "official")
                candidate = key("option", e.day, race.tag, entry.tag, "candidate", who)
                p.add(
                    "contest_option",
                    id=candidate,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=option,
                    party_id=party_id,
                    ballot_position=rank,
                    ballot_label=who,
                )
                candidacy = key("candidacy", e.day, race.tag, entry.tag, who)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    ballot_name=who,
                    member_of_party_id=party_id,
                )
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
                if votes is not None:
                    value(candidate, CANDIDATE_VOTES, votes)
    if any("valid" in race.stats or any(entry.votes is not None for entry in race.lists) for race in e.races):
        p.accept(
            "accounting.votes_valid",
            "each voter has as many votes as the constituency has seats, so where the Chamber's record prints the "
            "lists' votes they add up to far more than the valid ballots, and where it prints valid ballots without "
            "the lists' votes there is nothing to add up; every figure is stored as printed",
        )
    for code, reason in ACCEPTED.get(e.day, {}).items():
        p.accept(code, reason)
    return p
