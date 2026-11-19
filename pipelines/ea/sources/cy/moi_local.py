"""Cyprus: the Ministry of Interior's published results of local elections, by ballot box.

The Central Elections Service publishes local results on the same site as national ones (see `moi.py`): mayors,
municipal councils, deputy mayors, community leaders, community councils, school boards, and the presidents of the
district self-government organisations. Each election id lists every municipality or community that voted, with its
polling stations and ballot boxes. `crawl.py` walks that tree. This module builds one package per election day: each
election is one election row, and each municipality, community, or district is one contest, stored by ballot box.
"""

import functools
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.cy import crawl
from ea.sources.cy import moi as national

PIPELINE = "cy.moi_local"
RAW = crawl.SOURCE
CYPRUS = national.CYPRUS
COUNTRY = national.COUNTRY
VOTES = national.VOTES
ACCOUNTING = national.ACCOUNTING
ABROAD_DISTRICT = national.ABROAD_DISTRICT


@dataclass(frozen=True)
class Election:
    site: int
    kind: str
    day: date
    by_election: bool = False
    place: str | None = None


# The days follow the Ministry's own election titles and the Republic's election notices.
ELECTIONS = [
    Election(71, "mayor", date(2011, 12, 18)),
    Election(74, "council", date(2011, 12, 18)),
    Election(75, "leader", date(2011, 12, 18)),
    Election(72, "community_council", date(2011, 12, 18)),
    Election(73, "school", date(2011, 12, 18)),
    Election(96, "mayor", date(2016, 5, 22)),
    Election(97, "council", date(2016, 5, 22)),
    Election(100, "leader", date(2016, 5, 22)),
    Election(98, "community_council", date(2016, 5, 22)),
    Election(99, "school", date(2016, 5, 22)),
    Election(129, "mayor", date(2019, 8, 25), True, "Famagusta"),
    Election(133, "mayor", date(2020, 1, 12), True, "Ayia Napa"),
    Election(139, "mayor", date(2020, 8, 2), True, "Aglantzia"),
    Election(159, "mayor", date(2024, 6, 9)),
    Election(164, "council", date(2024, 6, 9)),
    Election(165, "deputy", date(2024, 6, 9)),
    Election(173, "leader", date(2024, 6, 9)),
    Election(161, "community_council", date(2024, 6, 9)),
    Election(172, "school", date(2024, 6, 9)),
    Election(174, "district", date(2024, 6, 9)),
    Election(197, "deputy", date(2026, 6, 28), True, "Aglantzia"),
]


# The municipal council elections of the three municipal election days. They are their own package set, loaded
# earlier; the set built here holds the other elections of those days and borrows that set's schemes, areas, and
# party lines.
COUNCIL_SITES = {74, 97, 164}
COUNCIL_PREFIX = f"{PIPELINE}/councils"
# School boards, deputy mayors, and the presidents of the district self-government organisations.
OFFICE_SITES = {73, 99, 172, 165, 197, 174}


# The package set being built: "offices" (the default) or "councils".
MODE = ["offices"]


def prefix() -> str:
    return COUNCIL_PREFIX if MODE[0] == "councils" else f"{PIPELINE}/offices"


def active() -> list[Election]:
    return [e for e in ELECTIONS if e.site in (COUNCIL_SITES if MODE[0] == "councils" else OFFICE_SITES)]


def councils() -> list[Election]:
    return [e for e in ELECTIONS if e.site in COUNCIL_SITES]


@dataclass(frozen=True)
class Kind:
    slug: str
    english: str  # "{year} Cypriot {english}", or "{year} {place} {by_english}" for a by-election
    by_english: str
    contest: str  # contest kind
    scope: str  # "area" or "district"
    seats: str  # "single" or "list"
    registry: str  # key of the body or office kind
    greek: str


KINDS = {
    "mayor": Kind(
        "mayors", "municipal elections, mayors", "mayoral by-election", "office", "area", "single", "mayor", "Δήμαρχοι"
    ),
    "council": Kind(
        "municipal-councils",
        "municipal elections, municipal councils",
        "municipal council by-election",
        "list_allocation",
        "area",
        "list",
        "council",
        "Δημοτικά συμβούλια",
    ),
    "deputy": Kind(
        "deputy-mayors",
        "municipal elections, deputy mayors",
        "deputy mayor by-election",
        "office",
        "area",
        "single",
        "deputy",
        "Αντιδήμαρχοι",
    ),
    "leader": Kind(
        "community-leaders",
        "community leader elections",
        "community leader by-election",
        "office",
        "area",
        "single",
        "leader",
        "Κοινοτάρχες",
    ),
    "community_council": Kind(
        "community-councils",
        "community council elections",
        "community council by-election",
        "list_allocation",
        "area",
        "list",
        "community_council",
        "Κοινοτικά συμβούλια",
    ),
    "school": Kind(
        "school-boards",
        "school board elections",
        "school board by-election",
        "list_allocation",
        "area",
        "list",
        "school",
        "Σχολικές εφορείες",
    ),
    "district": Kind(
        "district-presidents",
        "District Self-Government Organisation president elections",
        "District Self-Government Organisation president by-election",
        "office",
        "district",
        "single",
        "district",
        "Πρόεδροι Επαρχιακών Οργανισμών Αυτοδιοίκησης",
    ),
}

METHOD = (
    "Counted by the Central Elections Service of the Ministry of Interior per ballot box and published on its results "
    "site. A polling station holds one or more ballot boxes; each box is stored as its own reporting unit, named by "
    "its polling station and box. Registered voters are the electoral roll of the box, ballots cast are the voters "
    "who voted, and the valid, invalid, and blank ballots add up to the ballots cast. The site prints no seat counts "
    "and no declared winners, so no seats are stored; the outcome of a single-winner contest is the candidate with "
    "the most votes."
)
LIST_METHOD = (
    "The site types every ballot line as a party, including local groups and lines of independent candidates. "
    "Votes are for lists; the site prints no preference votes."
)
CLUSTER_METHOD = (
    "A municipality with several municipal districts elects one deputy mayor in each. The site prints the "
    "candidates of all districts together for the municipality, so each district's contest is the set of candidates "
    "that share ballot boxes; the district itself is not named by the site."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([PIPELINE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "moi-local")


def election_package(day: date) -> str:
    return f"{prefix()}/{day}"


def days() -> list[date]:
    return sorted({e.day for e in active()})


def packages() -> list[str]:
    out = []
    for mode in ("councils", "offices"):
        MODE[0] = mode
        out += [f"{prefix()}/geo", f"{prefix()}/registry", *(election_package(day) for day in days())]
    MODE[0] = "offices"
    return out


def fetch() -> None:
    for e in ELECTIONS:
        if e.site in COUNCIL_SITES | OFFICE_SITES:
            crawl.fetch_election(e.site)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    for mode in ("councils", "offices"):
        MODE[0] = mode
        geo().write()
        registry().write()
        for day in days():
            build(day).write()
        for cache in (places, council_places, lines):
            cache.cache_clear()
    MODE[0] = "offices"


def artifact(site: int) -> raw.Artifact:
    return raw.cached(RAW, crawl.crawl_url(site))


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών, Κεντρική Υπηρεσία Εκλογών",
        title="Official results of the local elections",
        url=crawl.BASE,
        license="Official results published by the Ministry of Interior for public use; no license is stated on the site",
        license_url=crawl.BASE,
        attribution="Republic of Cyprus, Ministry of Interior, Central Elections Service",
    )


def area(code: object) -> str:
    return key("area", code)


def district_area(code: object) -> str:
    return key("district", code)


@functools.cache
def places() -> tuple[dict[int, tuple[str, str | None]], dict[int, tuple[str, str | None, int]]]:
    """Districts and the municipalities and communities with Greek and English names, from the newest election."""
    return _places(tuple(sorted({*active(), *councils()}, key=lambda e: e.site)))


@functools.cache
def council_places() -> tuple[dict[int, tuple[str, str | None]], dict[int, tuple[str, str | None, int]]]:
    """The districts and areas the council package set holds."""
    return _places(tuple(councils())) if MODE[0] == "offices" else ({}, {})


def _places(
    elections: tuple[Election, ...],
) -> tuple[dict[int, tuple[str, str | None]], dict[int, tuple[str, str | None, int]]]:
    districts: dict[int, tuple[str, str | None]] = {}
    areas: dict[int, tuple[str, str | None, int]] = {}
    for e in sorted(elections, key=lambda e: e.day):
        document = national.load(e.site)
        english = document["english"]["districts"]
        for district in document["districts"]:
            node = district["district"]["node"]
            en = english[str(node[0])]
            districts[node[0]] = (node[1], en["node"][1])
            names = {row[0]: row[1] for row in en.get("areas") or []}
            for row in district["district"].get("areas") or []:
                areas[row[0]] = (row[1], names.get(row[0]), node[0])
    return districts, areas


def label(code: int) -> tuple[str, str]:
    """An area's Greek name and an English name, falling back to the Greek."""
    greek, english, _ = places()[1][code]
    return greek, english or greek


def geo() -> Package:
    first = MODE[0] == "councils"
    p = Package(
        f"{prefix()}/geo",
        PIPELINE,
        "Cyprus's districts, municipalities, and communities that voted in the Ministry of Interior's local results"
        + ("" if first else ", beyond those in the council package set"),
        depends=[] if first else [f"{COUNCIL_PREFIX}/geo"],
    )
    p.external |= {CYPRUS, COUNTRY}
    if first:
        add_source(p)
        for scheme, rank, noun in (
            ("cy.local_district", 1, "district"),
            ("cy.local_area", 2, "municipality or community"),
        ):
            p.add(
                "scheme", code=scheme, polity_id=CYPRUS, kind="administrative", rank=rank, noun=noun, code_system=None
            )
    for e in active():
        p.artifact(artifact(e.site), SOURCE_ID)
    districts, areas = _places(tuple(active()))
    held_districts, held_areas = council_places()
    for code, (greek, english) in sorted(districts.items()):
        if code in held_districts:
            continue
        id = district_area(code)
        p.add("area", id=id, scheme_code="cy.local_district")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, greek, "el", "official")
        if english:
            p.name(id, english, "en", "english", False)
    for code, (greek, english, district) in sorted(areas.items()):
        if code in held_areas:
            continue
        id = area(code)
        p.add("area", id=id, scheme_code="cy.local_area")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=district_area(district))
        p.name(id, greek, "el", "official")
        if english:
            p.name(id, english, "en", "english", False)
        kind = polity_kind(code) if home(code) == code else None
        if kind is not None:
            place = polity(code)
            p.add("polity", id=place, kind=kind, iso_code=None)
            p.add("polity_parent", polity_id=place, parent_id=CYPRUS)
            p.add("polity_territory", polity_id=place, area_id=id)
            p.add("slug", path=polity_paths()[code], entity_id=place, is_current=True)
            p.name(place, greek, "el", "official")
            if english:
                p.name(place, english.title() if english.isupper() else english, "en", "english", False)
    return p


# The 2024 school board elections list their areas under codes of 10000 plus the code of the municipality whose school
# board it is, such as 14010 for Aradippou's 4010. Those areas are school board districts, not places of their own.
SCHOOL_BOARD_OFFSET = 10000


def home(code: int) -> int:
    """The municipality or community whose polity holds the body or office of an area."""
    return code - SCHOOL_BOARD_OFFSET if code >= SCHOOL_BOARD_OFFSET else code


def polity(code: int) -> str:
    """The polity of a municipality or community that has a council or an office of its own."""
    return key("polity", home(code))


@functools.cache
def _holders() -> tuple[dict[int, str], dict[int, tuple[str, str | None, int]]]:
    """Every place with a body or office of its own across both package sets, with its polity kind and names.

    A place that held a municipal council election on any of the municipal election days is a municipality; any other
    place with a school board or deputy mayor is a community, which the archive stores as a submunicipal polity.
    """
    elections = tuple(e for e in ELECTIONS if e.site in COUNCIL_SITES | OFFICE_SITES)
    municipalities = {
        place["node"][0] for e in elections if e.site in COUNCIL_SITES for _, place, *_ in national._boxes(e.site)
    }
    holders = {
        home(place["node"][0])
        for e in elections
        if KINDS[e.kind].scope == "area"
        for _, place, *_ in national._boxes(e.site)
    }
    kinds = {code: "municipality" if code in municipalities else "submunicipal" for code in sorted(holders)}
    names = _places(elections)[1]
    if missing := sorted(code for code in kinds if code not in names):
        raise ValueError(f"no area for the school board districts of {missing}")
    return kinds, names


def polity_kind(code: int) -> str | None:
    return _holders()[0].get(code)


@functools.cache
def polity_paths() -> dict[int, str]:
    """A slug for each polity: the place's English name, with its district's name where two places share one."""
    kinds, names = _holders()
    districts = _places(tuple(e for e in ELECTIONS if e.site in COUNCIL_SITES | OFFICE_SITES))[0]
    base = {code: slug(names[code][1] or names[code][0]) for code in kinds}
    count: dict[str, int] = defaultdict(int)
    for text in base.values():
        count[text] += 1
    out: dict[int, str] = {}
    for code, text in base.items():
        if count[text] > 1:
            district = districts[names[code][2]]
            text = f"{text}-{slug(district[1] or district[0])}"
        out[code] = f"cy/{text}"
    if len(set(out.values())) != len(out):
        raise ValueError("two Cypriot polities share a slug")
    return out


def holder(kind: str, code: int) -> str:
    """The polity that holds a body or office: its municipality or community, or the country for a district's."""
    return polity(code) if KINDS[kind].scope == "area" else CYPRUS


def entity(kind: str, code: int) -> str:
    """The body or office a kind of election fills in one place: an area, or a district for the district kind."""
    return key("body" if KINDS[kind].contest == "list_allocation" else "office", kind, code)


def entity_names(kind: str, code: int) -> tuple[str, str]:
    """English and Greek names of the body or office."""
    if kind == "district":
        greek, english = places()[0][code][0], places()[0][code][1] or places()[0][code][0]
    else:
        greek, english = label(code)
    english = english.title() if english.isupper() else english
    greek_title = greek
    match kind:
        case "mayor":
            return f"Mayor of {english}", f"Δήμαρχος {greek_title}"
        case "deputy":
            return f"Deputy mayor of {english}", f"Αντιδήμαρχος {greek_title}"
        case "council":
            return f"{english} Municipal Council", f"Δημοτικό Συμβούλιο {greek_title}"
        case "leader":
            return f"Community leader of {english}", f"Κοινοτάρχης {greek_title}"
        case "community_council":
            return f"{english} Community Council", f"Κοινοτικό Συμβούλιο {greek_title}"
        case "school":
            return f"{english} School Board", f"Σχολική Εφορεία {greek_title}"
        case _:
            return (
                f"President of the {english} District Self-Government Organisation",
                f"Πρόεδρος Επαρχιακού Οργανισμού Αυτοδιοίκησης {greek_title}",
            )


def party_name(name: str) -> str:
    return national.party_name(name)


@functools.cache
def lines(elections: tuple[Election, ...] = ()) -> dict[str, dict[str, str | None]]:
    """Every list ballot line, by Greek name, with an English name when the site gives one."""
    out: dict[str, dict[str, str | None]] = {}
    for e in elections or tuple(active()):
        if KINDS[e.kind].seats != "list":
            continue
        document = national.load(e.site)
        english = document["english"]["island"]["who"]
        for cid, (name, _, _) in document["island"]["who"].items():
            row = out.setdefault(party_name(name), {"en": None})
            if row["en"] is None and cid in english:
                row["en"] = party_name(english[cid][0])
    return out


def seen() -> dict[str, set[int]]:
    """For each kind of election, the codes of the places that held one."""
    out: dict[str, set[int]] = defaultdict(set)
    for e in active():
        for district, place, *_ in national._boxes(e.site):
            out[e.kind].add(district["node"][0] if KINDS[e.kind].scope == "district" else place["node"][0])
    return out


def registry() -> Package:
    p = Package(
        f"{prefix()}/registry",
        PIPELINE,
        "Cyprus's local councils, offices, and party ballot lines",
        depends=[f"{prefix()}/geo", *([] if MODE[0] == "councils" else [f"{COUNCIL_PREFIX}/geo"])],
    )
    p.external |= {CYPRUS, COUNTRY}
    for e in active():
        p.uses(artifact(e.site))
    for kind, codes in sorted(seen().items()):
        for code in sorted(codes):
            id = entity(kind, code)
            english, greek = entity_names(kind, code)
            if KINDS[kind].contest == "list_allocation":
                p.add("body", id=id, polity_id=holder(kind, code), kind="council")
            else:
                p.add(
                    "office",
                    id=id,
                    polity_id=holder(kind, code),
                    kind={"mayor": "mayor", "deputy": "deputy_mayor"}.get(kind, "other"),
                )
            p.name(id, english, "en", "official")
            p.name(id, greek, "el", "native", False)
    held = lines(tuple(councils())) if MODE[0] == "offices" else {}
    for name, row in sorted(lines().items()):
        if name in held:
            continue
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=CYPRUS)
        p.name(id, name, "el", "official")
        if row["en"] and row["en"] != name:
            p.name(id, row["en"], "en", "english", False)
    return p


Box = tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]


def clusters(boxes: Sequence[Box]) -> list[tuple[set[int], list[Box]]]:
    """Candidates that share a ballot box are in one contest; the boxes of each such set make its units."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for *_, result in boxes:
        ids = [row[0] for row in result["c"]]
        for cid in ids:
            parent.setdefault(cid, cid)
        for cid in ids[1:]:
            parent[find(cid)] = find(ids[0])
    groups: dict[int, set[int]] = defaultdict(set)
    for cid in parent:
        groups[find(cid)].add(cid)
    out = []
    for members in sorted(groups.values(), key=min):
        # A box where nobody got a vote prints no candidates, so it joins the municipality's only contest.
        mine = [
            box
            for box in boxes
            if (box[4]["c"] and box[4]["c"][0][0] in members) or (not box[4]["c"] and len(groups) == 1)
        ]
        out.append((members, mine))
    return out


def english_title(e: Election) -> str:
    kind = KINDS[e.kind]
    if e.by_election:
        return f"{e.day.year} {e.place} {kind.by_english}"
    return f"{e.day.year} Cypriot {kind.english}"


def build(day: date) -> Package:
    mine = [e for e in active() if e.day == day]
    p = Package(
        election_package(day),
        PIPELINE,
        f"The {day} local elections in Cyprus by ballot box from the Ministry of Interior",
        depends=([f"{COUNCIL_PREFIX}/geo", f"{COUNCIL_PREFIX}/registry"] if MODE[0] == "offices" else [])
        + [f"{prefix()}/geo", f"{prefix()}/registry"],
    )
    p.external |= {CYPRUS, COUNTRY, "cy.ballot_box"}
    over = [0, 0]
    for e in mine:
        p.uses(artifact(e.site))
        contests(p, e, over)
    if over[0]:
        text = (
            f"The Ministry's own ballot box figures show more voters who voted than registered voters in {over[0]} "
            f"ballot box result{'s' if over[0] > 1 else ''} ({over[1]} more in all). They are stored as printed."
        )
        p.accept("accounting.turnout", text)
    return p


def contests(p: Package, e: Election, over: list[int]) -> None:
    kind = KINDS[e.kind]
    document = national.load(e.site)
    island = document["island"]
    title = island["election"][1]
    english = english_title(e)
    election_id = key("election", e.site)
    p.add(
        "election",
        id=election_id,
        polity_id=CYPRUS,
        kind="by_election" if e.by_election else "local",
        date_from=e.day,
        date_to=e.day,
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, title, "el", "native", False)
    path = f"cy/{e.day}-{kind.slug}" + ("-" + (e.place or "").lower().replace(" ", "-") if e.by_election else "")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    en_who = document["english"]["island"]["who"]
    boxes: list[Box] = national._boxes(e.site)
    names: dict[int, list[str]] = {int(cid): name for cid, name in island["who"].items()}
    for *_, result in boxes:
        for cid, name in result["who"].items():
            names.setdefault(int(cid), name)
    groups: dict[int, list[Box]] = defaultdict(list)
    for box in boxes:
        groups[box[0]["node"][0] if kind.scope == "district" else box[1]["node"][0]].append(box)
    unit_seen: set[object] = set()
    person_seen: set[str] = set()
    all_values = national.Facts()
    declared_areas: dict[int, dict[str, Any]] = {}
    for district in document["districts"]:
        if kind.scope == "district":
            declared_areas[district["district"]["node"][0]] = district["district"]
        else:
            for place in district["areas"]:
                declared_areas[place["area"]["node"][0]] = place["area"]
    totals = {field: 0 for _, field in ACCOUNTING}
    votes: dict[int, int] = {}
    for code, group in sorted(groups.items()):
        parts = clusters(group) if e.kind == "deputy" else [({row[0] for *_, r in group for row in r["c"]}, group)]
        for index, (members, part) in enumerate(parts, 1):
            suffix = f"-{index}" if len(parts) > 1 else ""
            contest = key("contest", e.site, code, index)
            holder = entity(e.kind, code)
            area_id = district_area(code) if kind.scope == "district" else area(code)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind=kind.contest,
                office_id=holder if kind.contest == "office" else None,
                body_id=holder if kind.contest == "list_allocation" else None,
                district_area_id=area_id,
                magnitude=1 if kind.contest == "office" else None,
                round_number=1,
                default_result_kind="counted",
            )
            en_name, el_name = entity_names(e.kind, code)
            suffix_text = f" ({index} of {len(parts)})" if len(parts) > 1 else ""
            p.name(contest, f"{english}: {en_name}{suffix_text}", "en", "official")
            p.name(contest, f"{title} - {el_name}{suffix_text}", "el", "native", False)
            p.add("slug", path=f"{path}/{code}{suffix}", entity_id=contest, is_current=True)
            result_set = key("result_set", e.site, code, index)
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
            note = (
                METHOD
                + (" " + LIST_METHOD if kind.seats == "list" else "")
                + (" " + CLUSTER_METHOD if len(parts) > 1 else "")
            )
            p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
            order: list[int] = []
            for *_, result in part:
                for cid, _count in result["c"]:
                    if cid not in order:
                        order.append(cid)
            options: dict[int, str] = {}
            for position, cid in enumerate(order, 1):
                name = party_name(names[cid][0])
                option = options[cid] = key("option", e.site, code, index, cid)
                if kind.seats == "single":
                    person = key("person", e.site, cid)
                    if person not in person_seen:
                        person_seen.add(person)
                        p.add("person", id=person)
                        p.name(person, name, "el", "official")
                        if str(cid) in en_who:
                            p.name(person, party_name(en_who[str(cid)][0]), "en", "english", False)
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        ballot_label=name,
                    )
                    p.add(
                        "candidacy",
                        id=key("candidacy", e.site, cid),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=name,
                    )
                else:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=key("party", name),
                        ballot_position=position,
                        ballot_label=name,
                    )
            sums = {field: 0 for _, field in ACCOUNTING}
            counts: dict[int, int] = {}
            for _district, place, station, box, result in part:
                if box["id"] in unit_seen:
                    raise ValueError(f"{e.site}: ballot box {box['id']} appears twice")
                unit_seen.add(box["id"])
                unit = key("unit", e.site, code, index, box["id"])
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=CYPRUS,
                    scheme_code="cy.ballot_box",
                    code=f"{e.day}:{e.site}:{box['id']}",
                    area_relation="within",
                    area_id=area(place["node"][0]),
                    kind="abroad_mission" if _district["node"][0] == ABROAD_DISTRICT else "standard",
                )
                text = (
                    station["node"][1] if station["node"][1] == box["name"] else f"{station['node'][1]} - {box['name']}"
                )
                p.name(unit, text, "el", "official")
                for measure, field in ACCOUNTING:
                    all_values.add(result_set, unit, None, measure, result[field])
                    sums[field] += result[field] or 0
                    totals[field] += result[field] or 0
                if (result["voted"] or 0) > (result["reg"] or 0):
                    over[0] += 1
                    over[1] += result["voted"] - result["reg"]
                for cid, count in result["c"]:
                    all_values.add(result_set, unit, options[cid], VOTES, count)
                    counts[cid] = counts.get(cid, 0) + count
                    votes[cid] = votes.get(cid, 0) + count
            printed = declared_areas[code]
            if len(parts) == 1:
                agree = all(sums[field] == printed[field] for _, field in ACCOUNTING) and counts == {
                    cid: count for cid, count in printed["c"]
                }
                if not agree:
                    print(f"{e.site}/{code}: boxes disagree with the printed place totals", flush=True)
                else:
                    for measure, field in ACCOUNTING:
                        p.add(
                            "declared", result_set_id=result_set, option_id=None, measure=measure, value=printed[field]
                        )
                    for cid, count in printed["c"]:
                        p.add("declared", result_set_id=result_set, option_id=options[cid], measure=VOTES, value=count)
            if kind.seats == "single":
                ranked = sorted(counts.items(), key=lambda row: -row[1])
                tied = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
                for rank, (cid, _count) in enumerate(ranked):
                    if tied and ranked[rank][1] == ranked[0][1]:
                        outcome = "tied"
                    else:
                        outcome = "elected" if rank == 0 else "lost"
                    p.add("outcome", result_set_id=result_set, option_id=options[cid], kind=outcome)
    p.frame("result_value", all_values.frame())
    island_totals = {field: island[field] for _, field in ACCOUNTING}
    printed_votes = {cid: count for cid, count in island["c"]}
    off = {field: (totals[field], island_totals[field]) for field in totals if totals[field] != island_totals[field]}
    state = (
        "match" if not off and votes == printed_votes else f"DIFFER {off} {len(set(votes) ^ set(printed_votes))} lines"
    )
    print(
        f"{e.site} {english}: {len(boxes)} boxes, {len(groups)} places, island totals {state} "
        f"(registered {totals['reg']}, voted {totals['voted']}, valid {totals['valid']})",
        flush=True,
    )
