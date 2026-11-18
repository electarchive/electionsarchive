"""Iceland's municipal elections of 16 May 2026 from the National Electoral Commission's election reports, and the
candidate lists of the 2024 Althing election from the commission's notice of the lists."""

import importlib
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "is.landskjorstjorn"
PAGE = "https://island.is/s/landskjorstjorn/sveitarstjornarkosningar-fra-2022"
ICELAND = str(uuid5(package.NAMESPACE, "polity\x1fIS"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fISL"))
DAY = date(2026, 5, 16)
EKEY = "local-2026-05-16"
SCHEME = "is-lk.sveitarfelag"
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ISL/ADM2"
GB_URL = f"{GEOBOUNDARIES}/geoBoundaries-ISL-ADM2.geojson"
SHAPES_FROM = date(2020, 1, 1)
LISTS_PAGE = "https://island.is/s/landskjorstjorn/frett/frambodslistar"
LISTS_2024 = (
    "https://assets.ctfassets.net/8k0h54kbe6bj/1gtENqhJYNkfpbwfGjCMnv/7678c549ba1093e4d051a53abd20b71e/"
    "Augl_sing_frambo_slista_til_Al_ingis_2024.pdf"
)
LETTER = "A-ZÞÆÖÐÁÉÍÓÚÝ"
NUMBER = r"\d{1,3}(?:,\d{3})*"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def number(text: str) -> int:
    return int(text.replace(",", ""))


# ---------------------------------------------------------------- fetch


def pdf_urls() -> list[str]:
    html = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf8")
    found = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if found is None:
        raise LookupError("the page of municipal election reports holds no page data")
    text = json.dumps(json.loads(found.group(1)), ensure_ascii=False)
    return sorted(set(re.findall(r'https://(?:assets|downloads)\.ctfassets\.net[^"\\ ]+\.pdf', text)))


def fetch() -> None:
    raw.fetch(SOURCE, LISTS_PAGE, suffix=".html")
    raw.fetch(SOURCE, LISTS_2024, suffix=".pdf")
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SOURCE, GB_URL, suffix=".geojson")
    raw.fetch_many(SOURCE, pdf_urls(), suffix=".pdf", workers=8)


# ---------------------------------------------------------------- reports


@dataclass
class Candidate:
    seat: int
    name: str
    address: str | None
    strikes: int
    votes: Decimal
    rank: int


@dataclass
class Entry:
    letter: str
    name: str
    votes: int
    seats: int
    rank_number: int
    modified: int | None = None
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Member:
    name: str
    address: str | None
    role: str
    place: int
    votes: int


@dataclass
class Report:
    url: str
    name: str
    unbound: bool
    registered: int
    cast: int
    valid: int
    void_blank: int
    invalid: int
    blank: int
    entries: list[Entry] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    total_votes: int | None = None
    total_seats: int | None = None


def text_of(url: str) -> str:
    done = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    )
    return done.stdout


def figure(text: str, label: str) -> int:
    found = re.search(rf"^\s*{label}\s+({NUMBER})\b", text, re.MULTILINE)
    if found is None:
        raise LookupError(f"no '{label}' line")
    return number(found.group(1))


ENTRY = re.compile(
    rf"^\s*([{LETTER}]{{1,2}})\s+[–-]\s+listi\s+(.+?)\s{{2,}}({NUMBER})\s+([\d.]+)%\s+(\d+)\s+(\d+)\s*$", re.MULTILINE
)
TOTAL = re.compile(rf"^\s*Samtals\s+({NUMBER})\s+([\d.]+)%\s+(\d+)\s*$", re.MULTILINE)
BLOCK = re.compile(rf"^\s{{10,}}([{LETTER}]{{1,2}})\s{{2,}}(\S.*?)\s*$")
RANKING = re.compile(rf"Röðunartala listans.*?:\s+(\d+)\s+lista:\s+({NUMBER})\s+seðla:\s+({NUMBER})\s+listans:")
ROW = re.compile(r"^\s*(\d+)\.\s{2,}(.*?)\s*$")
TAIL = re.compile(rf"(?:^|\s)({NUMBER})\s+([\d.]+)%\s+({NUMBER}\.\d\d)\s+(\d+)\.\s*$")
MEMBER = re.compile(rf"^(.+?)\s{{2,}}(Aðalmaður|(\d+)\. varamaður)\s+({NUMBER})\b")


# Where the report's columns touch, the name and the address run together in its text. Each is split by hand.
SPLITS = {
    "Guðmundur Sveinn Bæringsson Sunnubraut 2": ("Guðmundur Sveinn Bæringsson", "Sunnubraut 2"),
    "Sigurður Bjarni Gilbertsson Sunnubraut 6": ("Sigurður Bjarni Gilbertsson", "Sunnubraut 6"),
    "Guðmundur Sigurbjörn Óskarsson Hríshóll II": ("Guðmundur Sigurbjörn Óskarsson", "Hríshóll II"),
    "Brynjar Darri Sigurðsson Kerúlf Vallholt": ("Brynjar Darri Sigurðsson Kerúlf", "Vallholt"),
    "Hrafnhildur Helga GuðmundsdóttirHólshús": ("Hrafnhildur Helga Guðmundsdóttir", "Hólshús"),
    "Vilborg Anna Strange GarðarsdóttirKjarrmóar 23": ("Vilborg Anna Strange Garðarsdóttir", "Kjarrmóar 23"),
    "Ragnheiður Dröfn Benidiktsdóttir Fagurhóll 2": ("Ragnheiður Dröfn Benidiktsdóttir", "Fagurhóll 2"),
    "Bjarni Halldór Heiðuson Janusson Tjarnabraut 2": ("Bjarni Halldór Heiðuson Janusson", "Tjarnabraut 2"),
    "Svanfríður Guðrún Bergvinsdóttir Skólavegur 9": ("Svanfríður Guðrún Bergvinsdóttir", "Skólavegur 9"),
}


def split_person(body: str) -> tuple[str, str | None]:
    parts = re.split(r"\s{2,}", body.strip())
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return SPLITS.get(clean(parts[0]), (parts[0], None))


def parse_text(url: str, text: str) -> Report | None:
    lines = [line for line in text.splitlines() if line.strip()]
    if not any("16. maí 2026" in line for line in lines[:3]):
        return None
    name = clean(lines[2])
    unbound = any("Óbundnar kosningar" in line for line in lines[3:5])
    report = Report(
        url=url,
        name=name,
        unbound=unbound,
        registered=figure(text, "Fjöldi kjósenda á kjörskrá"),
        cast=figure(text, "Greidd atkvæði alls"),
        valid=figure(text, "Gildir atkvæðaseðlar"),
        void_blank=figure(text, "Auðir seðlar og ógildir"),
        invalid=figure(text, "Þar af ógildir seðlar"),
        blank=figure(text, "Þar af auðir seðlar"),
    )
    if unbound:
        for line in lines:
            found = MEMBER.match(line)
            if found is None:
                continue
            who, address = split_person(found.group(1))
            place = int(found.group(3) or 0)
            report.members.append(
                Member(who, address, "member" if place == 0 else "deputy", place, number(found.group(4)))
            )
        return report
    for found in ENTRY.finditer(text):
        report.entries.append(
            Entry(
                found.group(1), clean(found.group(2)), number(found.group(3)), int(found.group(5)), int(found.group(6))
            )
        )
    total = TOTAL.search(text)
    if total is not None:
        report.total_votes, report.total_seats = number(total.group(1)), int(total.group(3))
    by_letter = {entry.letter: entry for entry in report.entries}
    current: Entry | None = None
    pending: tuple[int, str] | None = None
    for line in lines:
        block = BLOCK.match(line)
        if block is not None and block.group(1) in by_letter and not ROW.match(line):
            current = by_letter[block.group(1)]
            continue
        if current is None:
            continue
        ranking = RANKING.search(line)
        if ranking is not None:
            current.modified = int(ranking.group(3).replace(",", ""))
            continue
        row = ROW.match(line)
        if row is None:
            # A long name wraps onto its own line, and the address and figures follow on the next.
            tail = TAIL.search(line)
            if pending is None or tail is None:
                continue
            seat, who = pending
            pending = None
            body = re.sub(rf"(?:\s{{2,}}{NUMBER})+\s*$", "", line[: tail.start()])
            current.candidates.append(
                Candidate(
                    seat,
                    who,
                    clean(body) or None,
                    number(tail.group(1)),
                    Decimal(tail.group(3).replace(",", "")),
                    int(tail.group(4)),
                )
            )
            continue
        tail = TAIL.search(row.group(2))
        if tail is None:
            pending = (int(row.group(1)), clean(row.group(2)))
            continue
        pending = None
        body = row.group(2)[: tail.start()]
        # The vote columns sit between the address and the strikes; cut them off the end of the body.
        body = re.sub(rf"(?:\s{{2,}}{NUMBER})+\s*$", "", body)
        who, address = split_person(body)
        current.candidates.append(
            Candidate(
                int(row.group(1)),
                who,
                address,
                number(tail.group(1)),
                Decimal(tail.group(3).replace(",", "")),
                int(tail.group(4)),
            )
        )
    return report


@cache
def reports() -> tuple[Report, ...]:
    found = []
    for url in pdf_urls():
        report = parse_text(url, text_of(url))
        if report is not None:
            found.append(report)
    return tuple(sorted(found, key=lambda r: r.name))


# ---------------------------------------------------------------- geography

# The report prints this municipality's name in the genitive.
NAMES = {"Kaldrananeshrepps": "Kaldrananeshreppur"}
# The municipalities whose shape is not the one of the same name in the boundary file, with the shapes it is made
# of. The file is from before the mergers of 2022 and 2026, so a merged municipality is the union of its parts.
SHAPES = {
    "Akureyrarbær": ["Akureyrarkaupstaður"],
    "Sveitarfélagið Hornafjörður": ["Sveitarfélagið Hornafjörðu"],
    "Skagafjörður": ["Sveitarfélagið Skagafjörðu", "Akrahreppur"],
    "Sveitarfélagið Stykkishólmur": ["Stykkishólmsbær", "Helgafellssveit"],
    "Suðurnesjabær": ["Sveitarfélagið Garður", "Sandgerðisbær"],
    "Múlaþing": ["Borgarfjarðarhreppur", "Djúpavogshreppur", "Fljótsdalshérað", "Seyðisfjarðarkaupstaður"],
    "Sameinað sveitarfélag Borgarbyggðar og Skorradalshrepps": ["Borgarbyggð", "Skorradalshreppur"],
    "Húnabyggð": ["Blönduósbær", "Húnavatnshreppur"],
    "Langanesbyggð": ["Langanesbyggð", "Svalbarðshreppur"],
    "Þingeyjarsveit": ["Þingeyjarsveit", "Skútustaðahreppur"],
    "Fjarðabyggð": ["Fjarðabyggð", "Breiðdalshreppur"],
}


def municipality(report: Report) -> str:
    return NAMES.get(report.name, report.name)


def area(name: str) -> str:
    return key("area", SCHEME, name)


def outline(shape: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(shapely.simplify(shape, 0.001, preserve_topology=True))
    parts = [
        q for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6) for q in shapely.get_parts(part)
    ]
    return shapely.set_srid(shapely.MultiPolygon(parts), 4326)


def shapes() -> dict[str, shapely.Geometry]:
    """The shape of each municipality that held an election, by its name in the reports."""
    found = {
        f["properties"]["shapeName"]: shapely.make_valid(shapely.geometry.shape(f["geometry"]))
        for f in json.loads(raw.cached(SOURCE, GB_URL).path.read_bytes())["features"]
    }
    out: dict[str, shapely.Geometry] = {}
    for report in reports():
        name = municipality(report)
        parts = SHAPES.get(report.name, [name])
        missing = [part for part in parts if part not in found]
        if missing:
            raise LookupError(f"{name}: the boundary file has no {missing}")
        out[name] = outline(shapely.union_all([found[part] for part in parts]))
    return out


SHAPE_SOURCE = key("source", "geoboundaries-adm2")
STATE = key("source", SOURCE)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The municipalities of Iceland that held council elections on 16 May 2026, with boundaries from geoBoundaries",
    )
    p.external |= {ICELAND, TERRITORY}
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Iceland ADM2 (municipalities)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 4.0 International",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), CC BY 4.0",
        notes=(
            "The file's municipalities are those of before the mergers of 2022 and 2026. A merged municipality's shape "
            "is the union of the shapes of the municipalities it was made of, and every outline is simplified."
        ),
    )
    p.artifact(raw.cached(SOURCE, GB_URL), SHAPE_SOURCE)
    p.add("scheme", code=SCHEME, polity_id=ICELAND, kind="administrative", rank=2, noun="municipality")
    p.add("boundary_set", id=key("boundary_set", SCHEME, 2026), scheme_code=SCHEME, vintage="2026", as_of=DAY)
    for name, shape in sorted(shapes().items()):
        id = area(name)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, name, "is", "official")
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=SHAPES_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPE_SOURCE,
        )
    return p


# ---------------------------------------------------------------- registry

COUNCIL = key("body", "municipal-council")
SYSTEM = key("electoral_system", "local")
SYSTEM_NOTE = (
    "Each municipal council is elected by proportional representation from candidate lists, with seats allocated to "
    "the lists by the D'Hondt method. Each list's candidates are ranked by their vote numbers, which weight the "
    "votes of the ballots that put a candidate in each place and count the strikes and moves that voters made on the "
    "ballot, and the first as many candidates as the list has seats are elected as members of the council. In a "
    "municipality where no list stands, the voters write the names of residents who are eligible, and the "
    "residents named on the most ballots are elected as members and then as deputies."
)
SOURCE_NOTE = (
    "Counted by the municipal election committee and published by the National Electoral Commission in the "
    "municipality's election report. "
)


def fold(text: str) -> str:
    return clean(text).casefold()


def party(name: str) -> str:
    return key("party", fold(name))


def entries() -> dict[str, str]:
    """Every list that stood, by its folded name, with the name as the reports print it."""
    names: dict[str, str] = {}
    for report in reports():
        for entry in report.entries:
            names.setdefault(fold(entry.name), entry.name)
    return names


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The municipal councils of Iceland, the electoral system of their elections, and the candidate lists that "
        "stood on 16 May 2026, from the National Electoral Commission's reports",
    )
    p.external.add(ICELAND)
    p.add(
        "source",
        id=STATE,
        kind="official_body",
        publisher="Landskjörstjórn (National Electoral Commission of Iceland)",
        title="Kosningaskýrslur sveitarfélaga: sveitarstjórnarkosningar 16. maí 2026",
        url="https://island.is/s/landskjorstjorn/sveitarstjornarkosningar-fra-2022",
        license="Public reports of the National Electoral Commission",
        license_url="https://island.is/s/landskjorstjorn",
        attribution="Landskjörstjórn, island.is",
    )
    p.add("body", id=COUNCIL, polity_id=ICELAND, kind="council")
    p.name(COUNCIL, "Municipal councils", "en", "official")
    p.name(COUNCIL, "Sveitarstjórnir", "is", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ICELAND,
        valid_from=date(2022, 1, 1),
        rules={"formula": "dhondt"},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for name in sorted(entries().values()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=ICELAND)
        p.name(id, name, "is", "official")
    return p


# ---------------------------------------------------------------- election

PACKAGE = f"{SOURCE}/{EKEY}"
ELECTION = key("election", EKEY)
RANKING_NOTE = (
    "Each candidate's value is the vote number the report prints for the candidate (atkvæðatala frambjóðenda), "
    "which weights the ballots by the place the candidate held on them and takes off the votes lost by strikes and "
    "moves, so it can have a fraction. Candidates are ranked by it. The first as many candidates as the list has "
    "seats are elected, and the rest of the ranked candidates are deputies."
)
LIST_NOTE = (
    SOURCE_NOTE
    + "Voters on the electoral roll, votes cast, and blank and invalid ballots are the municipality's, and each "
    "list's votes are its valid votes. Seats are the members the list elected. The number of ballots that voters "
    "changed on the list is stored with the list. " + RANKING_NOTE
)
UNBOUND_NOTE = (
    SOURCE_NOTE
    + "No candidate list stood in this municipality, so the voters wrote in the names of residents. Voters on the "
    "electoral roll, votes cast, and blank and invalid ballots are the municipality's. The report names the "
    "residents elected as members and as deputies, with the votes it counted for each, and not the votes of anyone "
    "else."
)


def rows_of(report: Report) -> list[tuple[int, int]]:
    return [
        (2, report.registered),
        (3, report.cast),
        (4, report.valid),
        (5, report.invalid),
        (6, report.blank),
    ]


def election() -> Package:
    p = Package(
        PACKAGE,
        SOURCE,
        "The municipal council elections of 16 May 2026 by municipality, with each list's votes and seats and each "
        "candidate's ranking, from the National Electoral Commission's reports",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ICELAND, TERRITORY}
    p.artifact(raw.cached(SOURCE, PAGE), STATE)
    p.add(
        "election",
        id=ELECTION,
        polity_id=ICELAND,
        kind="local",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        original_text="16. maí 2026",
        status="held",
    )
    p.name(ELECTION, "May 2026 Icelandic local government elections", "en", "official")
    p.name(ELECTION, "Sveitarstjórnarkosningar maí 2026", "is", "native", False)
    p.add("slug", path="is/2026-05-16-local", entity_id=ELECTION, is_current=True)
    gaps: list[str] = []
    for report in reports():
        p.artifact(raw.cached(SOURCE, report.url), STATE)
        name = municipality(report)
        where = area(name)
        contest = key("contest", EKEY, name)
        seats = report.total_seats if not report.unbound else sum(m.place == 0 for m in report.members)
        p.add(
            "contest",
            id=contest,
            election_id=ELECTION,
            kind="body_seats" if report.unbound else "list_allocation",
            body_id=COUNCIL,
            district_area_id=where,
            magnitude=seats,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        title = f"2026 {name} municipal election"
        p.name(contest, title, "en", "official")
        p.name(contest, f"{name}: sveitarstjórnarkosningar 2026", "is", "native", False)
        p.add("slug", path=f"is/2026-05-16-local/{changeset.slug(title)}", entity_id=contest, is_current=True)
        result_set = key("result_set", EKEY, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=STATE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=UNBOUND_NOTE if report.unbound else LIST_NOTE,
            lang="en",
        )
        unit = key("unit", EKEY, name)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ICELAND,
            scheme_code=SCHEME,
            code=f"{EKEY}:{name}",
            area_relation="equals",
            area_id=where,
            kind="standard",
        )

        def value(option: str | None, measure: int, amount: int | Decimal, declared: bool = True) -> None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )
            if declared:
                p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=amount)

        for measure, amount in rows_of(report):
            value(None, measure, amount)
        if report.void_blank != report.invalid + report.blank:
            raise ValueError(f"{name}: invalid and blank ballots do not add up to the report's {report.void_blank}")
        if report.valid + report.void_blank != report.cast:
            gaps.append(name)
        if report.unbound:
            residents = key("option", EKEY, name, "residents")
            p.add(
                "contest_option",
                id=residents,
                contest_id=contest,
                kind="independents",
                ballot_position=1,
                ballot_label="Residents named on the ballots",
            )
            for order, member in enumerate(report.members, 1):
                ident = f"{name}:{member.role}:{member.place}:{member.name}"
                option = key("option", EKEY, ident)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=residents,
                    ballot_position=order,
                    ballot_label=member.name,
                )
                person = key("person", ident)
                p.add("person", id=person, birth_precision=None)
                p.name(person, member.name, "is", "official")
                candidacy = key("candidacy", ident)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member" if member.role == "member" else "substitute",
                    list_position=member.place or None,
                    ballot_name=member.name,
                    is_independent=True,
                )
                value(option, 10, member.votes, declared=False)
                if member.role == "member":
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=residents,
                        candidacy_id=candidacy,
                        kind="elected",
                    )
            continue
        listed = 0
        for position, entry in enumerate(report.entries, 1):
            option = key("option", EKEY, name, entry.letter)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(entry.name),
                ballot_position=position,
                ballot_label=f"{entry.letter} – {entry.name}",
            )
            value(option, 1, entry.votes)
            listed += entry.votes
            if entry.modified is not None:
                value(option, 23, entry.modified, declared=False)
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
            for candidate in entry.candidates:
                ident = f"{name}:{entry.letter}:{candidate.seat}"
                child = key("option", EKEY, ident)
                p.add(
                    "contest_option",
                    id=child,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=option,
                    party_id=party(entry.name),
                    ballot_position=candidate.seat,
                    ballot_label=candidate.name,
                )
                person = key("person", ident, candidate.name)
                p.add("person", id=person, birth_precision=None)
                p.name(person, candidate.name, "is", "official")
                candidacy = key("candidacy", ident)
                elected = candidate.rank <= entry.seats
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=child,
                    role="list_member" if elected else "substitute",
                    list_position=candidate.seat,
                    ballot_name=candidate.name,
                    member_of_party_id=party(entry.name),
                    is_independent=False,
                )
                value(child, 10, candidate.votes, declared=False)
                if elected:
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        candidacy_id=candidacy,
                        kind="elected",
                    )
        if listed != report.valid or listed != report.total_votes:
            raise ValueError(f"{name}: the lists add up to {listed}, and the report prints {report.valid} valid votes")
    if gaps:
        p.accept("accounting.ballots_sum", f"the ballots of {', '.join(gaps)} do not add up to the votes cast")
    return p


# ---------------------------------------------------------------- Althing candidate lists

HAGSTOFA = "is.hagstofa"
ALTHING_EKEY = "general-2024-11-30"
ALTHING_PACKAGE = f"{SOURCE}/{ALTHING_EKEY}"
NOTICE = key("source", "althing-lists", ALTHING_EKEY)
# The constituencies as the notice heads them, by Statistics Iceland's constituency code.
HEADINGS = {
    "NORÐVESTURKJÖRDÆMI": "northwest-2003",
    "NORÐAUSTURKJÖRDÆMI": "northeast-2003",
    "SUÐURKJÖRDÆMI": "south-2003",
    "SUÐVESTURKJÖRDÆMI": "southwest-2003",
    "REYKJAVÍKURKJÖRDÆMI SUÐUR": "reykjavik-south-2003",
    "REYKJAVÍKURKJÖRDÆMI NORÐUR": "reykjavik-north-2003",
}
LISTS_NOTE = (
    "The candidates on each list, in the order the National Electoral Commission's notice of the lists in the "
    "running gives them (auglýsing frá landskjörstjórn um framboð við alþingiskosningar 30. nóvember 2024, issued "
    "on 6 November 2024 under article 47 of the Elections Act, no. 112/2021). Each list holds up to twice as many "
    "candidates as its constituency elects members. A candidate whose occupation the notice gives as member of the "
    "Althing is stored as an incumbent. The notice also prints each candidate's identity number, occupation, and "
    "address, which the archive does not store; the year of birth is read from the identity number."
)


def hagstofa_key(kind: str, *parts: str) -> str:
    """An id that Statistics Iceland's pipeline mints, built the same way it builds it."""
    return str(uuid5(package.NAMESPACE, "\x1f".join([HAGSTOFA, kind, *parts])))


def contest_of(code: str) -> str:
    return hagstofa_key("contest", ALTHING_EKEY, f"althing:{code}")


@dataclass
class Listed:
    constituency: str
    letter: str
    label: str
    position: int
    name: str
    occupation: str
    born: int


def althing_lists() -> list[Listed]:
    """Every candidate in the commission's notice of the 2024 Althing lists, constituency by constituency."""
    lines = [clean(line) for line in text_of(LISTS_2024).splitlines()]
    found: list[Listed] = []
    constituency = letter = label = ""
    pending: tuple[int, str] | None = None
    for number, line in enumerate(lines):
        if line in HEADINGS:
            constituency = HEADINGS[line]
            continue
        heading = re.match(rf"^([{LETTER}]) - listi (.+):$", line)
        if heading:
            letter, label = heading.group(1), heading.group(2)
            continue
        entry = re.match(rf"^(\d{{1,2}})\.\s+([{LETTER}].+),$", line)
        if entry and constituency and letter:
            pending = (int(entry.group(1)), entry.group(2))
            continue
        detail = re.match(r"^kt\. (\d{2})(\d{2})(\d{2})-\d{3}(\d),\s*([^,]+)", line)
        if detail:
            if pending is None:
                raise ValueError(f"line {number}: an identity number with no candidate before it")
            century = {"9": 1900, "0": 2000}[detail.group(4)]
            found.append(
                Listed(
                    constituency,
                    letter,
                    label,
                    pending[0],
                    pending[1],
                    detail.group(5),
                    century + int(detail.group(3)),
                )
            )
            pending = None
    if pending is not None:
        raise ValueError(f"{pending[1]} has no identity number")
    return found


def hagstofa_magnitudes() -> dict[str, int]:
    """The members each constituency elected in 2024, from Statistics Iceland's built package."""
    folder = package.folder(f"{HAGSTOFA}/{ALTHING_EKEY}") / "build"
    contests = pl.read_parquet(folder / "contest.parquet")
    return {
        code: int(row["magnitude"])
        for row in contests.iter_rows(named=True)
        for code in HEADINGS.values()
        if row["id"] == contest_of(code)
    }


def hagstofa_options() -> dict[tuple[str, str], str]:
    """Statistics Iceland's list option for each constituency and list letter, from its built 2024 package, whose
    labels end in the letter, as in 'Progressive Party (B)'."""
    folder = package.folder(f"{HAGSTOFA}/{ALTHING_EKEY}") / "build"
    contests = {contest_of(code): code for code in HEADINGS.values()}
    options: dict[tuple[str, str], str] = {}
    table = pl.read_parquet(folder / "contest_option.parquet")
    for row in table.iter_rows(named=True):
        code = contests.get(row["contest_id"])
        letter = re.search(r"\(([A-ZÞÆÖÐÁÉÍÓÚÝ])\)$", row["ballot_label"])
        if code is not None and row["kind"] == "list" and letter is not None:
            options[(code, letter.group(1))] = row["id"]
    return options


def althing() -> Package:
    p = Package(
        ALTHING_PACKAGE,
        SOURCE,
        "The candidates on every list in the 2024 Althing election, from the National Electoral Commission's notice "
        "of the lists",
        depends=[f"{HAGSTOFA}/geo", f"{HAGSTOFA}/registry", f"{HAGSTOFA}/{ALTHING_EKEY}"],
    )
    p.add(
        "source",
        id=NOTICE,
        kind="official_body",
        publisher="Landskjörstjórn (National Electoral Commission of Iceland)",
        title="Auglýsing frá landskjörstjórn um framboð við alþingiskosningar 30. nóvember 2024",
        url=LISTS_PAGE,
        license="Public notices of the National Electoral Commission",
        license_url="https://island.is/s/landskjorstjorn",
        attribution="Landskjörstjórn, island.is",
    )
    p.artifact(raw.cached(SOURCE, LISTS_PAGE), NOTICE)
    p.artifact(raw.cached(SOURCE, LISTS_2024), NOTICE)
    election = hagstofa_key("election", ALTHING_EKEY)
    note = key("note", "althing-lists", ALTHING_EKEY)
    p.add("note", id=note, entity_id=election, kind="methodology", body_md=LISTS_NOTE, lang="en")
    options = hagstofa_options()
    magnitudes = hagstofa_magnitudes()
    seen: dict[tuple[str, str], list[int]] = {}
    for c in althing_lists():
        option = options.get((c.constituency, c.letter))
        if option is None:
            raise LookupError(f"Statistics Iceland has no {c.letter} list in {c.constituency}")
        seen.setdefault((c.constituency, c.letter), []).append(c.position)
        tag = f"{c.constituency}:{c.letter}:{c.position}"
        person = key("person", ALTHING_EKEY, tag)
        p.add("person", id=person, birth_date=date(c.born, 1, 1), birth_precision="year")
        p.name(person, c.name, "is", "official")
        p.add(
            "candidacy",
            id=key("candidacy", ALTHING_EKEY, tag),
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=c.position,
            ballot_name=c.name,
            is_incumbent=c.occupation.startswith("alþingismaður"),
        )
    if len(seen) != len(options):
        raise ValueError(f"the notice holds {len(seen)} lists, and Statistics Iceland {len(options)}")
    for (constituency, letter), positions in seen.items():
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError(f"{constituency} {letter}: the places run {positions}")
        if len(positions) > 2 * magnitudes[constituency]:
            raise ValueError(f"{constituency} {letter}: {len(positions)} candidates")
    return p


# ---------------------------------------------------------------- run


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", PACKAGE, ALTHING_PACKAGE]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()
    hagstofa = importlib.import_module("ea.sources.is.hagstofa")
    for event in hagstofa.events():
        if event.ekey == ALTHING_EKEY:
            hagstofa.build(event).write()
    althing().write()


def emit() -> None:
    package.run("load", packages())
