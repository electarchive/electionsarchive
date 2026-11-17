"""Cyprus: the Ministry of Interior's published results of national elections, by ballot box.

The Central Elections Service publishes results at results.elections.moi.gov.cy: for each election, the island, each
district, each community or municipality, each polling station, and each ballot box. `crawl.py` walks that tree. This
module builds one package per election day from the crawl, plus the places and the registry they depend on.
"""

import functools
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.cy import crawl

SOURCE = crawl.SOURCE
CYPRUS = str(uuid5(package.NAMESPACE, "polity\x1fCY"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fCYP"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD_DISTRICT = 7
SEATS = 56
# Seats of each constituency, by the Ministry's district id: the First Schedule of Law 124/1985 for the elections
# from 2001 to 2021, and the First Schedule as replaced by Law 226(I)/2025 for the 2026 election.
DISTRICTS = {1: "Nicosia", 2: "Kyrenia", 3: "Famagusta", 4: "Larnaca", 5: "Limassol", 6: "Paphos"}
SEATS_1985 = {1: 21, 2: 3, 3: 11, 4: 5, 5: 12, 6: 4}
SEATS_2025 = {1: 19, 2: 3, 3: 11, 4: 6, 5: 12, 6: 5}
SEAT_LAW_2025 = date(2026, 1, 1)


@dataclass(frozen=True)
class Election:
    site: int
    family: str
    day: date
    round: int = 1


ELECTIONS = [
    Election(22, "parliament", date(2001, 5, 27)),
    Election(34, "president", date(2003, 2, 16)),
    Election(41, "euro", date(2004, 6, 13)),
    Election(43, "parliament", date(2006, 5, 21)),
    Election(56, "president", date(2008, 2, 17)),
    Election(57, "president", date(2008, 2, 24), 2),
    Election(58, "euro", date(2009, 6, 7)),
    Election(59, "parliament", date(2011, 5, 22)),
    Election(81, "president", date(2013, 2, 17)),
    Election(83, "president", date(2013, 2, 24), 2),
    Election(85, "euro", date(2014, 5, 25)),
    Election(89, "parliament", date(2016, 5, 22)),
    Election(101, "president", date(2018, 1, 28)),
    Election(111, "president", date(2018, 2, 4), 2),
    Election(112, "euro", date(2019, 5, 26)),
    Election(127, "parliament", date(2021, 5, 30)),
    Election(154, "president", date(2023, 2, 5)),
    Election(157, "president", date(2023, 2, 12), 2),
    Election(163, "euro", date(2024, 6, 9)),
    Election(190, "parliament", date(2026, 5, 24)),
]
FAMILY = {
    "parliament": ("parliamentary", "House of Representatives election", "Εκλογή Βουλής των Αντιπροσώπων"),
    "president": ("presidential", "presidential election", "Προεδρική εκλογή"),
    "euro": ("european-parliament", "European Parliament election", "Ευρωεκλογές"),
}
METHOD = (
    "Counted by the Central Elections Service of the Ministry of Interior per ballot box and published on its "
    "results site. A polling station holds one or more ballot boxes; each box is stored as its own reporting unit, "
    "named by its polling station and box. Registered voters are the electoral roll of the box, ballots cast are "
    "the voters who voted, and the valid, invalid, and blank ballots add up to the ballots cast. The site types "
    "every ballot line of a list election as a party, including lines of independent candidates. The site prints no "
    "seat counts, so no seats are stored. A House election is split into the six constituencies, each with the "
    "ballot boxes of its district; the constituency's seats are those of the First Schedule to the law on the "
    "election of members of the House of Representatives (Law 124/1985, replaced by Law 226(I)/2025 from 2026)."
)
RUNOFF = {
    date(2008, 2, 24): date(2008, 2, 17),
    date(2013, 2, 24): date(2013, 2, 17),
    date(2018, 2, 4): date(2018, 1, 28),
    date(2023, 2, 12): date(2023, 2, 5),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MOI_SOURCE = key("source", SOURCE)
HOUSE = key("body", "house")
PRESIDENCY = key("office", "president")
PRESIDENT_SYSTEM = key("electoral_system", "president", 2003)


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


def package_key(e: Election) -> str:
    return f"{SOURCE}/{e.day}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(e) for e in ELECTIONS)]


def fetch() -> None:
    for e in ELECTIONS:
        crawl.fetch_election(e.site)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


@functools.cache
def load(site: int) -> dict[str, Any]:
    document = json.loads(raw.cached(SOURCE, crawl.crawl_url(site)).path.read_text())
    assert isinstance(document, dict)
    return document


def artifact(site: int) -> raw.Artifact:
    return raw.cached(SOURCE, crawl.crawl_url(site))


def _boxes(
    site: int,
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Each ballot box with its district, community, polling station, and own result."""
    out = []
    for district in load(site)["districts"]:
        for place in district["areas"]:
            for station in place["stations"]:
                for box, result in station["boxes"]:
                    out.append(
                        (
                            district["district"],
                            place["area"],
                            station["station"],
                            {"id": box[0], "name": box[1]},
                            result,
                        )
                    )
    return out


@functools.cache
def places() -> tuple[dict[int, tuple[str, str | None]], dict[int, tuple[str, str | None, int]]]:
    """Districts and communities with their Greek and English names, from the newest election that lists each."""
    districts: dict[int, tuple[str, str | None]] = {}
    communities: dict[int, tuple[str, str | None, int]] = {}
    for e in sorted(ELECTIONS, key=lambda e: e.day):
        document = load(e.site)
        english = document["english"]["districts"]
        for district in document["districts"]:
            node = district["district"]["node"]
            district_id = node[0]
            en = english[str(district_id)]
            districts[district_id] = (node[1], en["node"][1])
            names = {row[0]: row[1] for row in en.get("areas") or []}
            for row in district["district"].get("areas") or []:
                communities[row[0]] = (row[1], names.get(row[0]), district_id)
    return districts, communities


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Cyprus's districts and the communities and municipalities named in the Ministry of Interior's results",
    )
    p.external |= {CYPRUS, COUNTRY}
    add_source(p)
    for e in ELECTIONS:
        p.artifact(artifact(e.site), MOI_SOURCE)
    for scheme, rank, kind, noun in (
        ("cy.district", 1, "administrative", "district"),
        ("cy.community", 2, "administrative", "community or municipality"),
        ("cy.ballot_box", 3, "precinct", "ballot box"),
    ):
        p.add("scheme", code=scheme, polity_id=CYPRUS, kind=kind, rank=rank, noun=noun, code_system=None)
    districts, communities = places()
    for code, (greek, english) in sorted(districts.items()):
        id = area("cy.district", code)
        p.add("area", id=id, scheme_code="cy.district")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, greek, "el", "official")
        if english:
            p.name(id, english, "en", "english", False)
    for code, (greek, english, district) in sorted(communities.items()):
        id = area("cy.community", code)
        p.add("area", id=id, scheme_code="cy.community")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=area("cy.district", district))
        p.name(id, greek, "el", "official")
        if english:
            p.name(id, english, "en", "english", False)
    return p


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=MOI_SOURCE,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών, Κεντρική Υπηρεσία Εκλογών",
        title="Official election results",
        url=crawl.BASE,
        license="Official results published by the Ministry of Interior for public use; no license is stated on the site",
        license_url=crawl.BASE,
        attribution="Republic of Cyprus, Ministry of Interior, Central Elections Service",
    )


def party_name(name: str) -> str:
    return " ".join(name.split())


@functools.cache
def lines() -> dict[str, dict[str, str | None]]:
    """Every ballot line of a list election, by Greek name, with an English name when the site gives one."""
    out: dict[str, dict[str, str | None]] = {}
    for e in ELECTIONS:
        if e.family == "president":
            continue
        document = load(e.site)
        english = document["english"]["island"]["who"]
        for cid, (name, _, _) in document["island"]["who"].items():
            row = out.setdefault(party_name(name), {"en": None})
            if row["en"] is None and cid in english:
                row["en"] = party_name(english[cid][0])
    return out


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Cyprus's House of Representatives, presidency, and party ballot lines",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CYPRUS, COUNTRY}
    for e in ELECTIONS:
        p.uses(artifact(e.site))
    p.add("body", id=HOUSE, polity_id=CYPRUS, kind="unicameral")
    p.name(HOUSE, "House of Representatives", "en", "official")
    p.name(HOUSE, "Βουλή των Αντιπροσώπων", "el", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=CYPRUS, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Cyprus", "en", "official")
    p.name(PRESIDENCY, "Πρόεδρος της Κυπριακής Δημοκρατίας", "el", "native", False)
    p.add(
        "electoral_system",
        id=PRESIDENT_SYSTEM,
        polity_id=CYPRUS,
        valid_from=date(1960, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", PRESIDENT_SYSTEM),
        entity_id=PRESIDENT_SYSTEM,
        kind="methodology",
        body_md=(
            "The President is elected by direct vote for five years. A candidate who wins more than half of the valid "
            "votes in the first round is elected; otherwise the two leading candidates go to a second round a week later."
        ),
        lang="en",
    )
    for name, row in sorted(lines().items()):
        id = key("party", name)
        p.add("party", id=id, kind="party", polity_id=CYPRUS)
        p.name(id, name, "el", "official")
        if row["en"] and row["en"] != name:
            p.name(id, row["en"], "en", "english", False)
    return p


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


ACCOUNTING = (
    (REGISTERED, "reg"),
    (CAST, "voted"),
    (VALID, "valid"),
    (INVALID, "invalid"),
    (BLANK, "blank"),
)


def election(e: Election) -> Package:
    slug, english_kind, _ = FAMILY[e.family]
    document = load(e.site)
    island = document["island"]
    title = island["election"][1]
    year = e.day.year
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    first = next((o for o in ELECTIONS if o.day == RUNOFF.get(e.day)), None)
    if first is not None:
        depends.append(package_key(first))
    p = Package(
        package_key(e),
        SOURCE,
        f"The {e.day} {FAMILY[e.family][1]} in Cyprus by ballot box from the Ministry of Interior",
        depends=depends,
    )
    p.external |= {CYPRUS, COUNTRY}
    p.uses(artifact(e.site))
    day = e.day
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=CYPRUS,
        kind="runoff" if e.round == 2 else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    round_text = ", second round" if e.round == 2 else ""
    english = f"{year} Cypriot {english_kind}{round_text}"
    p.name(election_id, english, "en", "official")
    p.name(election_id, title, "el", "native", False)
    path = f"cy/{day}-{slug}" + ("-runoff" if e.round == 2 else "")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Facts()
    seen: set[object] = set()
    boxes = _boxes(e.site)
    if e.family == "parliament":
        seats = SEATS_2025 if e.day >= SEAT_LAW_2025 else SEATS_1985
        if sum(seats.values()) != SEATS:
            raise ValueError("constituency seats do not add up to the House")
        known = {d["district"]["node"][0] for d in document["districts"]}
        stray = {box[0]["node"][0] for box in boxes} - set(seats)
        if stray or not set(seats) <= known:
            raise ValueError(f"{package_key(e)}: unexpected districts {sorted(stray)}")
        scopes: list[tuple[int | None, str]] = [(d, d_name) for d, d_name in DISTRICTS.items()]
    else:
        scopes = [(None, "")]
    sets: list[str] = []
    for district, district_name in scopes:
        if district is None:
            scope_boxes, total, contest_name = boxes, island, english
            suffix: tuple[object, ...] = ()
            area_id, magnitude = COUNTRY, {"president": 1, "euro": 6}[e.family]
            slug_path = f"{path}/{ {'president': 'president', 'euro': 'cyprus-seats'}[e.family] }"
            greek = f"{title}"
        else:
            scope_boxes = [box for box in boxes if box[0]["node"][0] == district]
            total = next(d["district"] for d in document["districts"] if d["district"]["node"][0] == district)
            contest_name = f"{english}, {district_name}"
            suffix = (district,)
            area_id, magnitude = area("cy.district", district), seats[district]
            slug_path = f"{path}/{district_name.lower()}"
            greek = f"{title} - {total['node'][1]}"
        contest = key("contest", day, *suffix)
        previous = key("contest", first.day) if first is not None else None
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind={"president": "office", "parliament": "list_allocation", "euro": "list_allocation"}[e.family],
            office_id=PRESIDENCY if e.family == "president" else None,
            body_id=HOUSE if e.family == "parliament" else None,
            district_area_id=area_id,
            magnitude=magnitude,
            round_number=e.round,
            previous_round_id=previous,
            electoral_system_id=PRESIDENT_SYSTEM if e.family == "president" else None,
            default_result_kind="counted",
        )
        p.name(contest, contest_name, "en", "official")
        p.name(contest, greek, "el", "native", False)
        p.add("slug", path=slug_path, entity_id=contest, is_current=True)
        result_set = key("result_set", day, *suffix)
        sets.append(result_set)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=MOI_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        who: dict[str, list[str]] = dict(total["who"] if district is not None else island["who"])
        en_who = document["english"]["island"]["who"]
        order = [str(c[0]) for c in (total["c"] if district is not None else island["c"])]
        for *_, result in scope_boxes:
            for cid, name in result["who"].items():
                who.setdefault(cid, name)
                if cid not in order:
                    order.append(cid)
        options: dict[str, str] = {}
        for position, cid in enumerate(order, 1):
            name = party_name(who[cid][0])
            option = options[cid] = key("option", day, *suffix, cid)
            if e.family == "president":
                person = key("person", day, cid)
                p.add("person", id=person)
                p.name(person, name, "el", "official")
                if cid in en_who:
                    p.name(person, party_name(en_who[cid][0]), "en", "english", False)
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
                    id=key("candidacy", day, cid),
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
        for unit_district, place, station, box, result in scope_boxes:
            if box["id"] in seen:
                raise ValueError(f"{package_key(e)}: ballot box {box['id']} appears twice")
            seen.add(box["id"])
            unit = key("unit", day, box["id"])
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CYPRUS,
                scheme_code="cy.ballot_box",
                code=f"{day}:{box['id']}",
                area_relation="within",
                area_id=area("cy.community", place["node"][0]),
                kind="abroad_mission" if unit_district["node"][0] == ABROAD_DISTRICT else "standard",
            )
            label = station["node"][1] if station["node"][1] == box["name"] else f"{station['node'][1]} - {box['name']}"
            p.name(unit, label, "el", "official")
            for measure, field in ACCOUNTING:
                values.add(result_set, unit, None, measure, result[field])
            for cid, count in result["c"]:
                values.add(result_set, unit, options[str(cid)], VOTES, count)
        for measure, field in ACCOUNTING:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total[field])
        for cid, count in total["c"]:
            p.add("declared", result_set_id=result_set, option_id=options[str(cid)], measure=VOTES, value=count)
        _compare(f"{package_key(e)} {district_name}".strip(), scope_boxes, total)
        if e.family == "president":
            _outcomes(p, e, result_set, options, island)
    p.frame("result_value", values.frame())
    if e.family == "parliament":
        _compare(f"{package_key(e)} island", boxes, island)
    _overcount(p, sets, boxes)
    return p


def _compare(label: str, boxes: Sequence[tuple[dict[str, Any], ...]], island: dict[str, Any]) -> None:
    sums = {field: 0 for _, field in ACCOUNTING}
    votes: dict[int, int] = {}
    for *_, result in boxes:
        for field in sums:
            sums[field] += result[field] or 0
        for cid, count in result["c"]:
            votes[cid] = votes.get(cid, 0) + count
    printed = {cid: count for cid, count in island["c"]}
    off = {field: (sums[field], island[field]) for field in sums if sums[field] != island[field]}
    if off or votes != printed:
        raise ValueError(f"{label}: ballot boxes disagree with the island totals: {off}")
    print(
        f"{label}: {len(boxes)} boxes, totals match "
        f"(registered {sums['reg']}, voted {sums['voted']}, valid {sums['valid']})",
        flush=True,
    )


def _outcomes(p: Package, e: Election, result_set: str, options: dict[str, str], island: dict[str, Any]) -> None:
    ranked = sorted(island["c"], key=lambda row: -row[1])
    valid = island["valid"]
    top = ranked[0][1]
    elected = e.round == 2 or top * 2 > valid
    for index, (cid, _) in enumerate(ranked):
        option = options[str(cid)]
        if elected:
            kind = "elected" if index == 0 else "lost"
        else:
            kind = "advanced" if index < 2 else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)


def _overcount(p: Package, result_sets: Sequence[str], boxes: Sequence[tuple[dict[str, Any], ...]]) -> None:
    over = [result for *_, result in boxes if (result["voted"] or 0) > (result["reg"] or 0)]
    if not over:
        return
    extra = sum(result["voted"] - result["reg"] for result in over)
    text = (
        f"The Ministry's own ballot box figures show more voters who voted than registered voters in {len(over)} "
        f"ballot box{'es' if len(over) > 1 else ''} ({extra} more in all). They are stored as printed."
    )
    p.accept("accounting.turnout", text)
    for result_set in result_sets:
        p.add(
            "note",
            id=key("note", result_set, "overcount"),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
