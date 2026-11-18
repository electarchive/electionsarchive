"""Palestine's local elections of 25 April 2026 from the Central Elections Commission (elections.ps).

The Commission publishes the results as PDF tables, one council to a page or more, and prints each council's
candidates (and lists) with their votes. Municipal councils (90 polled, 42 acclaimed) elect their members from open
lists by the Sainte-Laguë method; village councils (93 polled, 155 acclaimed) elect individuals by plurality. Where
a council had no competition the Commission lists the winners declared by acclamation (a tacit result set). The
tables have a text layer whose Arabic is read by glyph (see `arabic_pdf`); every vote keeps its page and box.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

import pymupdf
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ps import cec_local_tables as tables
from ea.sources.ps.cec_local_tables import Acclaimed, Box, Cell, Council

SOURCE = "ps.cec_local"
SITE = "https://www.elections.ps"
DAY = date(2026, 4, 25)
PALESTINE = str(uuid5(package.NAMESPACE, "polity\x1fPS"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fPSE"))
VOTES, REGISTERED, CAST, PREFERENCE = 1, 2, 3, 7
GEOBOUNDARIES = "ps.geoboundaries"
GEO_URL = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/PSE/ADM2/"
    "geoBoundaries-PSE-ADM2_simplified.geojson"
)
FILES = {
    "page": f"{SITE}/tabid/1310/language/ar-PS/Default.aspx",
    "votes": f"{SITE}/Portals/0/Elections_2026_Full_7_5_20266.pdf",
    "lists": f"{SITE}/Portals/0/RES_ELECTED_LISTS_2026_7_5_2026.pdf",
    "villages": f"{SITE}/Portals/0/RES_ELECTED_WINNERS_NOMINAL_2026_7_5_2026.pdf",
    "acclaimed-lists": f"{SITE}/Portals/0/RES_REC_LISTS_2026_1.pdf",
    "acclaimed-villages": f"{SITE}/Portals/0/RES_REC_NOMINAL_2026.pdf",
    "turnout": f"{SITE}/Portals/0/turn%20out%20final.pdf",
    "system": f"{SITE}/tabid/669/language/ar-PS/Default.aspx",
}
# The Commission's electoral districts are the governorates; each is placed on geoBoundaries' governorate shape.
DISTRICTS = {
    "القدس": ("Jerusalem", "PS-JEM"),
    "جنين": ("Jenin", "PS_JEN"),
    "طولكرم": ("Tulkarm", "PS-TKM"),
    "طوباس": ("Tubas", "PS-TBS"),
    "نابلس": ("Nablus", "PS-NBS"),
    "قلقيلية": ("Qalqilya", "PS-QQA"),
    "سلفيت": ("Salfit", "PS-SLT"),
    "رام الله والبيرة": ("Ramallah and al-Bireh", "PS-RBH*"),
    "أريحا": ("Jericho", "PS-JRH"),
    "بيت لحم": ("Bethlehem", "PS-BTH"),
    "الخليل": ("Hebron", "PS-HBN"),
    "دير البلح": ("Deir al-Balah", "PS-DEB"),
}
TYPES = {"مستقلة": "independent", "حركة فتح": "fatah", "هيئة حزبية": "party_body"}
# The font prints a final alef with a tanween (ـاً) as one glyph, which reads as an alef with a hamza or a madda.
SPELLINGS = {"أولأ": "أولاً", "أولآ": "أولاً", "اولأ": "اولاً", "معأ": "معاً"}
LIST_METHOD = (
    "Counted by the Central Elections Commission per local authority. Each voter chose one list and up to five "
    "candidates on it (an open list): a list's votes are the ballots for it, and each candidate's preference votes "
    "are the votes the candidate received. The Commission's tables print the candidates with their votes, each "
    "list's votes and seats, and the authority's registered voters and ballots cast; they print no valid, invalid, "
    "or blank totals, so none is stored. Seats are stored as declared. Within a list the seats go to the candidates "
    "with the most votes, with the representation of women and the Muslim-Christian quota taken into account, so "
    "the Commission marks each candidate as a winner or a loser."
)
VILLAGE_METHOD = (
    "Counted by the Central Elections Commission per village council. Each voter chose up to five candidates, so the "
    "candidates' votes add up to more than the ballots cast. The Commission marks each candidate as a winner or a "
    "loser; the seats go to the candidates with the most votes, with the representation of women and the "
    "Muslim-Christian quota taken into account, so a winner can have fewer votes than a loser. A council's "
    "registered voters and ballots cast are the figures printed at the head of its table; the tables print no "
    "valid, invalid, or blank totals, so none is stored."
)
TACIT_METHOD = (
    "No vote was held for this council: the Commission declared the candidates who nominated for its seats elected "
    "by acclamation (tazkiya). Its table lists the winners and the authority's seats and registered voters; it "
    "counts no ballots, so the set holds no votes. A council may have fewer winners than seats when too few "
    "candidates came forward."
)
SYSTEM_NOTE = (
    "Decree-Law no. 23 of 2025 (19 November 2025) on the election of local councils set two systems. Municipal "
    "councils are elected by proportional representation from open lists: a list has as many candidates as the "
    "council has seats, the voter chooses one list and up to five candidates on it, seats go to the lists with at "
    "least 5 percent of the valid votes (the threshold) by the Sainte-Laguë method, and a list's seats go to its "
    "candidates by their votes, with the representation of women and the Muslim-Christian quota taken into "
    "account. The declared seats of all 90 polled municipal councils are reproduced by Sainte-Laguë among the "
    "lists with at least 5 percent of the lists' votes. Seats are stored as declared."
)
BLOCK_NOTE = (
    "Decree-Law no. 23 of 2025 (19 November 2025) on the election of local councils sets plurality voting for village "
    "councils: candidates stand individually, the voter chooses up to five candidates, and seats go to the "
    "candidates with the most votes, with the representation of women and the Muslim-Christian quota taken into "
    "account."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
GB_SOURCE = key("source", "geoboundaries")
FATAH = key("party", "fatah")
SYSTEM_LIST = key("electoral_system", "list")
SYSTEM_BLOCK = key("electoral_system", "block")


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".aspx" if url.endswith(".aspx") else ".pdf")
    raw.fetch(GEOBOUNDARIES, GEO_URL, suffix=".geojson")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


# --- the Commission's tables ----------------------------------------------------------------------------------------


def norm(text: str) -> str:
    return re.sub(r"[\s()]", "", text)


def fix(text: str) -> str:
    return " ".join(SPELLINGS.get(word, word) for word in text.split())


def lkey(text: str) -> str:
    """A list name as its tables print it, whose spacing and dashes differ from table to table."""
    return re.sub(r"[\s\-/]+", "", fix(text))


def path(name: str) -> Path:
    return raw.cached(SOURCE, FILES[name]).path


@dataclass(frozen=True)
class Authority:
    """One local authority's council and how it was elected."""

    district: str
    name: str
    seats: int
    registered: int
    municipal: bool
    polled: bool
    council: Council | None  # the candidates and votes of a polled council (and its header boxes)
    lists: Council | None  # a polled municipal council's lists and winners
    acclaimed: Acclaimed | None

    @property
    def ident(self) -> str:
        return f"{self.district}:{norm(self.name)}"


@cache
def authorities() -> list[Authority]:
    votes = {(c.district, norm(c.authority)): c for c in tables.councils(path("votes"), "votes")}
    lists = {(c.district, norm(c.authority)): c for c in tables.councils(path("lists"), "lists")}
    villages = {(c.district, norm(c.authority)): c for c in tables.councils(path("villages"), "villages")}
    if set(votes) != set(lists):
        raise ValueError(f"the candidate and list tables name different councils: {set(votes) ^ set(lists)}")
    out: list[Authority] = []
    for k, council in sorted(votes.items()):
        listed = lists[k]
        if (council.seats, council.registered.text, council.cast.text) != (
            listed.seats,
            listed.registered.text,
            listed.cast.text,
        ):
            raise ValueError(f"{k}: the candidate and list tables disagree on seats or electors")
        out.append(
            Authority(
                council.district,
                council.authority,
                council.seats,
                int(council.registered.text),
                True,
                True,
                council,
                listed,
                None,
            )
        )
    for k, council in sorted(villages.items()):
        out.append(
            Authority(
                council.district,
                council.authority,
                council.seats,
                int(council.registered.text),
                False,
                True,
                council,
                None,
                None,
            )
        )
    for municipal, name in ((True, "acclaimed-lists"), (False, "acclaimed-villages")):
        for entry in tables.acclaimed(path(name), "lists" if municipal else "villages"):
            out.append(
                Authority(
                    entry.district, entry.authority, entry.seats, entry.registered, municipal, False, None, None, entry
                )
            )
    idents = [a.ident for a in out]
    if len(set(idents)) != len(idents):
        raise ValueError(f"two authorities share a name: {[i for i, n in Counter(idents).items() if n > 1]}")
    unknown = {a.district for a in out} - set(DISTRICTS)
    if unknown:
        raise ValueError(f"districts without a governorate: {unknown}")
    return out


@cache
def turnout() -> tuple[list[tables.Turnout], tables.Total]:
    return tables.turnout(path("turnout"))


# --- packages -------------------------------------------------------------------------------------------------------


def governorate(district: str) -> str:
    return key("area", "ps.governorate", DISTRICTS[district][1].replace("_", "-").rstrip("*"))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Palestine's governorates from geoBoundaries, the electoral districts of the 2026 local elections, and the "
        "scheme of local authorities",
    )
    p.external |= {PALESTINE, COUNTRY}
    artifact = raw.cached(GEOBOUNDARIES, GEO_URL)
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (Open Data Watch)",
        title="geoBoundaries gbOpen State of Palestine ADM2 (governorates), 2017",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), Open Data Watch",
    )
    p.artifact(artifact, GB_SOURCE)
    p.add(
        "scheme",
        code="ps.governorate",
        polity_id=PALESTINE,
        kind="administrative",
        rank=1,
        noun="governorate",
        code_system="iso_3166_2",
    )
    p.add(
        "scheme",
        code="ps.local_authority",
        polity_id=PALESTINE,
        kind="administrative",
        rank=2,
        noun="local authority",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", "ps.governorate", "2017"),
        scheme_code="ps.governorate",
        vintage="2017",
        as_of=artifact.retrieved_at.date(),
    )
    features = {str(f["properties"]["shapeISO"]): f for f in json.loads(artifact.path.read_text("utf-8"))["features"]}
    arabic = {iso: district for district, (_, iso) in DISTRICTS.items()}
    for iso, feature in sorted(features.items()):
        code = iso.replace("_", "-").rstrip("*")
        id = key("area", "ps.governorate", code)
        p.add("area", id=id, scheme_code="ps.governorate")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, str(feature["properties"]["shapeName"]), "en", "english", True)
        if iso in arabic:
            p.name(id, arabic[iso], "ar", "official", False)
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        shape = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3])
        shape = shapely.set_srid(shape, 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GB_SOURCE,
        )
    return p


def body(authority: Authority) -> str:
    return key("body", authority.ident)


def council_label(authority: Authority) -> str:
    return "مجلس بلدي" if authority.municipal else "مجلس قروي"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Palestine's local councils (municipal and village), their electoral systems, and the Fatah movement as the "
        "party the 2026 local lists name",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {PALESTINE, COUNTRY}
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title="Results of the local elections of 25 April 2026 published by the Central Elections Commission",
        url=FILES["page"],
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    for id, rules, note in (
        (
            SYSTEM_LIST,
            {"formula": "sainte_lague", "threshold": {"percent": 5, "level": "list", "scope": "contest"}},
            SYSTEM_NOTE,
        ),
        (SYSTEM_BLOCK, {"formula": "block"}, BLOCK_NOTE),
    ):
        p.add(
            "electoral_system", id=id, polity_id=PALESTINE, valid_from=date(2025, 11, 19), rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    p.add("party", id=FATAH, kind="party", polity_id=PALESTINE)
    p.name(FATAH, "حركة فتح", "ar", "official")
    p.name(FATAH, "Fatah", "en", "english", False)
    p.add("external_id", entity_id=FATAH, scheme="ps.cec_local.party", value="fatah")
    for authority in authorities():
        id = body(authority)
        p.add("body", id=id, polity_id=PALESTINE, kind="council")
        p.name(id, f"{council_label(authority)} {authority.name}", "ar", "official")
    return p


def _put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, cell: Cell | int, pages: dict[int, str]
) -> None:
    value = tables.number(cell.text) if isinstance(cell, Cell) else cell
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
    if isinstance(cell, Cell):
        box: Box = cell.box
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=pages[box.page],
            source_id=CEC_SOURCE,
            x0=box.x0,
            y0=box.y0,
            x1=box.x1,
            y1=box.y1,
        )


def _pages(p: Package, name: str) -> dict[int, str]:
    artifact = raw.cached(SOURCE, FILES[name])
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CEC_SOURCE}\x1f{artifact.storage_key}"))
    pages: dict[int, str] = {}
    document = pymupdf.open(artifact.path)
    for number in range(1, document.page_count + 1):
        page = document[number - 1]
        pages[number] = key("page", name, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number}.png",
            width=round(page.rect.width),
            height=round(page.rect.height),
        )
    return pages


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The Palestinian local elections of 25 April 2026: the municipal and village councils, by local authority, "
        "from the Central Elections Commission's tables",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PALESTINE, COUNTRY}
    for url in FILES.values():
        p.uses(raw.cached(SOURCE, url))
    pages = {name: _pages(p, name) for name in ("votes", "lists", "villages")}
    day_id = key("election", DAY)
    p.add("election", id=day_id, polity_id=PALESTINE, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(day_id, "2026 Palestinian local elections", "en", "official")
    p.name(day_id, "الانتخابات المحلية 2026", "ar", "native", False)
    prefix = f"ps/{DAY}-local"
    p.add("slug", path=prefix, entity_id=day_id, is_current=True)
    everyone = authorities()
    ordinal: dict[tuple[str, bool], int] = defaultdict(int)
    differences, summary = _turnout_differences(everyone)
    p.add("note", id=key("note", day_id, "turnout"), entity_id=day_id, kind="caveat", body_md=summary, lang="en")
    counts = Counter()
    for authority in sorted(everyone, key=lambda a: (a.district, a.municipal is False, a.name)):
        ordinal[authority.district, authority.municipal] += 1
        slug = (
            f"{changeset.slug(DISTRICTS[authority.district][0])}-"
            f"{'municipal' if authority.municipal else 'village'}-{ordinal[authority.district, authority.municipal]:03d}"
        )
        contest, result_set = _contest(p, day_id, authority, f"{prefix}/{slug}")
        counts["contests"] += 1
        if authority.polled:
            unit = _unit(p, authority)
            if authority.municipal:
                assert authority.council is not None and authority.lists is not None
                counts["winners"] += _municipal(p, authority, contest, result_set, unit, pages["votes"], pages["lists"])
            else:
                assert authority.council is not None
                counts["winners"] += _village(p, authority, contest, result_set, unit, pages["villages"])
            if authority.ident in differences:
                p.add(
                    "note",
                    id=key("note", result_set, "turnout"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=differences[authority.ident],
                    lang="en",
                )
        else:
            counts["winners"] += _acclaimed(p, authority, contest, result_set)
    _verify(everyone, counts)
    short = [
        a for a in everyone if not a.polled and a.municipal and a.acclaimed and len(a.acclaimed.winners) != a.seats
    ]
    if short:
        p.accept(
            "outcomes.seats_magnitude",
            "The Commission declares "
            + ", ".join(
                f"{a.name} ({a.district}) {len(a.acclaimed.winners)} of {a.seats}" for a in short if a.acclaimed
            )
            + " seats won by the acclaimed list, fewer than the seats of the council, because too few candidates "
            "were nominated; the list's declared seats are the winners the Commission names and the council's size "
            "is as printed.",
        )
    return p


def _turnout_differences(everyone: list[Authority]) -> tuple[dict[str, str], str]:
    """Where the Commission's separate final turnout table differs from the ballots cast in a council's own table."""
    rows, total = turnout()
    by_registered: dict[int, list[tables.Turnout]] = defaultdict(list)
    for row in rows:
        by_registered[row.registered].append(row)
    taken: set[int] = set()
    notes: dict[str, str] = {}
    polled = [a for a in everyone if a.polled]
    cast = 0
    matched = 0
    for authority in polled:
        assert authority.council is not None
        own = int(authority.council.cast.text)
        cast += own
        options = [r for r in by_registered.get(authority.registered, []) if r.number not in taken]
        if not options:
            raise ValueError(f"{authority.ident}: no turnout row with {authority.registered} electors")
        row = min(options, key=lambda r: abs(r.cast - own))
        taken.add(row.number)
        matched += 1
        if row.cast != own:
            notes[authority.ident] = (
                f"The Commission's separate table of final turnout by local authority prints {row.cast:,} voters "
                f"for this authority; the table of its results prints {own:,} ballots cast. Both are stored as "
                "printed in their tables, and this set holds the results table's figure."
            )
    if len(taken) != len(rows) or matched != len(rows):
        raise ValueError("the turnout table and the results tables do not name the same authorities")
    if sum(a.registered for a in polled) != total.registered or sum(r.registered for r in rows) != total.registered:
        raise ValueError("registered voters differ from the turnout table's total")
    if sum(r.cast for r in rows) != total.cast:
        raise ValueError("the turnout table's rows do not add up to its printed total")
    summary = (
        f"The tables of results print {cast:,} ballots cast in the {len(polled)} authorities that voted, and "
        f"{sum(a.registered for a in polled):,} registered voters. The Commission's separate table of final turnout "
        f"prints {total.cast:,} voters and {total.registered:,} registered voters for the same authorities: the "
        f"registered voters agree, and the ballots cast differ by {cast - total.cast:+,} in total and in "
        f"{len(notes)} authorities. Each set holds the figures of its own results table."
    )
    print(summary, flush=True)
    return notes, summary


def _contest(p: Package, election_id: str, authority: Authority, slug: str) -> tuple[str, str]:
    contest = key("contest", authority.ident)
    result_set = key("result_set", authority.ident)
    polled = authority.polled
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation" if authority.municipal else "body_seats",
        body_id=body(authority),
        magnitude=authority.seats,
        round_number=1,
        electoral_system_id=SYSTEM_LIST if authority.municipal else SYSTEM_BLOCK,
        default_result_kind="counted" if polled else "tacit",
    )
    p.name(contest, f"{council_label(authority)} {authority.name}، {DAY.year}", "ar", "official")
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted" if polled else "tacit",
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0 if polled else None,
    )
    method = TACIT_METHOD if not polled else LIST_METHOD if authority.municipal else VILLAGE_METHOD
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return contest, result_set


def _unit(p: Package, authority: Authority) -> str:
    unit = key("unit", authority.ident)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PALESTINE,
        scheme_code="ps.local_authority",
        code=authority.ident,
        area_relation="within",
        area_id=governorate(authority.district),
        kind="standard",
    )
    p.name(unit, authority.name, "ar", "official")
    return unit


def _person(p: Package, tag: str, name: str) -> str:
    person = key("person", tag)
    p.add("person", id=person)
    p.name(person, name, "ar", "official")
    return person


def _municipal(
    p: Package,
    authority: Authority,
    contest: str,
    result_set: str,
    unit: str,
    vote_pages: dict[int, str],
    list_pages: dict[int, str],
) -> int:
    council, listed = authority.council, authority.lists
    assert council is not None and listed is not None
    _put(p, result_set, unit, None, REGISTERED, council.registered, vote_pages)
    _put(p, result_set, unit, None, CAST, council.cast, vote_pages)
    options: dict[str, str] = {}
    kinds: dict[str, str] = {}
    seats: dict[str, int] = {}
    for row in listed.lists:
        numbered = re.fullmatch(r"-?(\d+)\s*-?\s*(.+)", row["name"].text)
        if numbered is None:
            raise ValueError(f"{authority.ident}: a list without a number: {row['name'].text}")
        label = lkey(numbered.group(2).strip())
        number = numbered
        kind = TYPES[row["type"].text]
        option = options[label] = key("option", authority.ident, number.group(1))
        kinds[label] = kind
        seats[label] = tables.number(row["seats"].text)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=FATAH if kind == "fatah" else None,
            ballot_position=int(number.group(1)),
            ballot_label=fix(numbered.group(2).strip()),
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="won" if seats[label] else "lost",
            seats=seats[label],
        )
        _put(p, result_set, unit, option, VOTES, row["votes"], list_pages)
    winners = 0
    for index, row in enumerate(council.rows):
        label = lkey(row["list"].text)
        if label not in options:
            raise ValueError(f"{authority.ident}: candidate {row['name'].text} is on an unknown list {label!r}")
        tag = f"{authority.ident}:{index}"
        name = fix(row["name"].text)
        person = _person(p, tag, name)
        option = key("option", tag)
        parent = options[label]
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=parent,
            party_id=FATAH if kinds[label] == "fatah" else None,
            ballot_label=name,
        )
        candidacy = key("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=parent,
            role="list_member",
            ballot_name=name,
            member_of_party_id=FATAH if kinds[label] == "fatah" else None,
            is_independent=kinds[label] == "independent",
        )
        won = row["result"].text == "فائز"
        if not won and row["result"].text != "خاسر":
            raise ValueError(f"{authority.ident}: unknown result {row['result'].text!r}")
        winners += won
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=parent,
            candidacy_id=candidacy,
            kind="elected" if won else "lost",
        )
        _put(p, result_set, unit, option, PREFERENCE, row["votes"], vote_pages)
    for label, count in seats.items():
        got = sum(1 for row in council.rows if row["result"].text == "فائز" and lkey(row["list"].text) == label)
        if got != count:
            raise ValueError(f"{authority.ident}: list {label!r} has {got} winners for {count} seats")
    if winners != authority.seats:
        raise ValueError(f"{authority.ident}: {winners} winners for {authority.seats} seats")
    return winners


def _village(p: Package, authority: Authority, contest: str, result_set: str, unit: str, pages: dict[int, str]) -> int:
    council = authority.council
    assert council is not None
    _put(p, result_set, unit, None, REGISTERED, council.registered, pages)
    _put(p, result_set, unit, None, CAST, council.cast, pages)
    winners = 0
    for index, row in enumerate(council.rows, 1):
        tag = f"{authority.ident}:{index}"
        name = fix(row["name"].text)
        person = _person(p, tag, name)
        option = key("option", tag)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=name)
        candidacy = key("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            is_independent=False,
        )
        won = row["result"].text == "فائز"
        if not won and row["result"].text != "خاسر":
            raise ValueError(f"{authority.ident}: unknown result {row['result'].text!r}")
        winners += won
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if won else "lost",
        )
        _put(p, result_set, unit, option, VOTES, row["votes"], pages)
    if winners > authority.seats:
        raise ValueError(f"{authority.ident}: {winners} winners for {authority.seats} seats")
    return winners


def _acclaimed(p: Package, authority: Authority, contest: str, result_set: str) -> int:
    entry = authority.acclaimed
    assert entry is not None
    parent: str | None = None
    kind = "party_body"
    if authority.municipal:
        assert entry.list_name is not None and entry.list_type is not None
        kind = TYPES[entry.list_type]
        parent = key("option", authority.ident, "list")
        p.add(
            "contest_option",
            id=parent,
            contest_id=contest,
            kind="list",
            party_id=FATAH if kind == "fatah" else None,
            ballot_position=1,
            ballot_label=fix(entry.list_name),
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=parent,
            kind="won",
            seats=len(entry.winners),
        )
    for position, cell in entry.winners:
        tag = f"{authority.ident}:{position}"
        name = fix(cell.text)
        person = _person(p, tag, name)
        option = key("option", tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=parent,
            party_id=FATAH if kind == "fatah" else None,
            ballot_position=position,
            ballot_label=name,
        )
        candidacy = key("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=parent or option,
            role="list_member" if parent else "head",
            list_position=position if parent else None,
            ballot_name=name,
            member_of_party_id=FATAH if kind == "fatah" else None,
            is_independent=kind == "independent" or (parent is None),
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=parent or option,
            candidacy_id=candidacy if parent else None,
            kind="elected",
        )
    return len(entry.winners)


def _verify(everyone: list[Authority], counts: Counter[str]) -> None:
    """The Commission's results page counts 90 municipal and 93 village councils polled, 42 municipal and 155 village
    councils acclaimed, and 3,697 winners."""
    shape = Counter((a.municipal, a.polled) for a in everyone)
    expected = {(True, True): 90, (False, True): 93, (True, False): 42, (False, False): 155}
    if dict(shape) != expected or counts["winners"] != 3697:
        raise ValueError(f"the councils or winners differ from the Commission's page: {dict(shape)}, {counts}")
