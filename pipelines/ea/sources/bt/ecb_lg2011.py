"""Bhutan: the Election Commission of Bhutan's results for the 2011 Local Government elections.

The first Local Government elections filled, in each of the 205 gewogs, a Gup and a Mangmi (voted by the whole gewog)
and a Tshogpa for each chiwog; they also filled a Thromde Thuemi for each yenlag thromde and the Tshogpa of the Phuentsholing
and Thimphu thromdes. The Commission publishes a contested and an uncontested PDF for each dzongkhag, plus a file of
Thromde Thuemi and a file of Thromde Tshogpa results. A candidate without an opponent stands on a yes or no vote."""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.bt import ecb, lg2011
from ea.sources.bt.lg2011 import Record
from ea.sources.bt.tables import DZONGKHAGS, dzongkhag_index

SOURCE = "bt.ecb_lg2011"
BASE = "https://ecb.bt"
PAGE = f"{BASE}/local-government-elections-2011-results/"
# The page links the Samdrup Jongkhar file twice and leaves the Bumthang contested file out of its table; the file sits
# beside the others under an unnumbered name.
EXTRA = [f"{BASE}/wp-content/uploads/2026/04/Elected-and-Non-Elected-Candidates-Results.pdf"]
BHUTAN = ecb.BHUTAN
VOTES = 1
DAY = date(2011, 1, 1)
END = date(2011, 12, 31)
THROMDE_TSHOGPA = "ThromdeTshogpa.pdf"
CLASS_A = {"Phuentsholing": "Chhukha", "Thimphu": "Thimphu"}
POSTS = {"Gup": "Gup", "Mangmi": "Mangmi"}
METHOD = (
    "Counted by the Election Commission of Bhutan and printed as a table of votes for each candidate. Votes cast on "
    "the electronic voting machines at polling stations are stored on the election-day unit, and postal ballots on the "
    "postal unit. The table gives no counts of registered voters or of invalid or blank ballots, so none are stored."
)
YESNO = (
    " A candidate with no opponent stands on a yes or no vote: the candidate's option holds the yes votes, a No "
    "option holds the no votes, and the Commission marks the candidate elected or not elected."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "ecb")
SYSTEM = key("electoral_system", "fptp")
ELECTION = key("election", "2011")
OFFICES = {"Gup": key("office", "gup"), "Mangmi": key("office", "mangmi")}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/2011"]


def urls() -> list[str]:
    """Every file link on the results page, in page order without repeats, plus the file the page leaves out."""
    page = raw.cached(SOURCE, PAGE)
    html = (raw.DATA / page.storage_key).read_text(errors="replace")
    found = re.findall(r'href="\.\./(wp-content/uploads/[^"]*\.pdf)"', html)
    return [*dict.fromkeys(f"{BASE}/{link}" for link in found), *EXTRA]


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch_many(SOURCE, urls(), suffix=".pdf", workers=3, attempts=6)


def read() -> list[Record]:
    out: list[Record] = []
    for url in urls():
        path = raw.DATA / raw.cached(SOURCE, url).storage_key
        name = url.rsplit("/", 1)[-1]
        uncontested = "Un-Contested" in name or name == THROMDE_TSHOGPA
        out += lg2011.records(path, None, uncontested=uncontested)
    return out


@dataclass(frozen=True)
class Place:
    """A gewog or thromde with its dzongkhag's index."""

    dzongkhag: int
    name: str


def dz_of(record: Record) -> int:
    return dzongkhag_index(CLASS_A.get(record.dzongkhag, record.dzongkhag))


def place_of(record: Record) -> Place:
    if record.post == "Thromde Tshogpa":
        return Place(dz_of(record), f"{record.dzongkhag} Thromde")
    return Place(dz_of(record), record.gewog)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Geography:
    """The gewogs, chiwogs, thromdes, and thromde constituencies the files name, each with a code."""

    gewogs: dict[Place, str]
    chiwogs: dict[tuple[Place, str], str]
    thromdes: dict[Place, str]
    constituencies: dict[tuple[Place, str], str]


def geography(records: list[Record]) -> Geography:
    gewogs: dict[int, set[str]] = defaultdict(set)
    chiwogs: dict[tuple[int, str], set[str]] = defaultdict(set)
    thromdes: dict[int, set[str]] = defaultdict(set)
    constituencies: dict[tuple[int, str], set[str]] = defaultdict(set)
    for r in records:
        place = place_of(r)
        if r.post in {"Gup", "Mangmi", "Tshogpa"}:
            gewogs[place.dzongkhag].add(place.name)
            chiwogs[place.dzongkhag, place.name].add(clean(r.chiwog))
        else:
            thromdes[place.dzongkhag].add(place.name)
            if r.post == "Thromde Tshogpa":
                constituencies[place.dzongkhag, place.name].add(clean(r.chiwog))

    def order(names: set[str]) -> list[str]:
        return sorted(names, key=lambda name: (name.casefold(), name))

    out = Geography({}, {}, {}, {})
    for dz, names in gewogs.items():
        for number, name in enumerate(order(names), 1):
            code = f"{dz + 1:02d}.{number:02d}"
            out.gewogs[Place(dz, name)] = code
            for sub, chiwog in enumerate(order(chiwogs[dz, name]), 1):
                out.chiwogs[Place(dz, name), chiwog] = f"{code}.{sub:02d}"
    for dz, names in thromdes.items():
        for number, name in enumerate(order(names), 1):
            code = f"{dz + 1:02d}.t{number}"
            out.thromdes[Place(dz, name)] = code
            for sub, chiwog in enumerate(order(constituencies.get((dz, name), set())), 1):
                out.constituencies[Place(dz, name), chiwog] = f"{code}.{sub}"
    return out


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def dzongkhag_area(index: int) -> str:
    return ecb.dzongkhag_area(index)


def parse() -> None:
    records = read()
    geo(records).write()
    registry(records).write()
    build(records).write()


def emit() -> None:
    package.run("load", packages())


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Election Commission of Bhutan",
        title="Local Government Elections 2011: results by dzongkhag, contested and uncontested",
        url=PAGE,
        license="No license stated; published by the Election Commission of Bhutan",
        attribution="Election Commission of Bhutan",
    )


def geo(records: list[Record]) -> Package:
    g = geography(records)
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bhutan's gewogs, chiwogs, thromdes, and thromde constituencies as the Election Commission of Bhutan names "
        "them in its 2011 Local Government election results",
        depends=["bt.ecb/geo"],
    )
    p.external |= {BHUTAN, *(dzongkhag_area(i) for i in range(len(DZONGKHAGS)))}
    add_source(p)
    for code, rank, kind, noun in (
        ("bt.gewog", 3, "administrative", "gewog"),
        ("bt.thromde", 3, "administrative", "thromde"),
        ("bt.chiwog", 4, "administrative", "chiwog"),
        ("bt.thromde_constituency", 4, "electoral_district", "constituency"),
    ):
        p.add("scheme", code=code, polity_id=BHUTAN, kind=kind, rank=rank, noun=noun, code_system=None)

    def add(scheme: str, code: str, name: str, parent: str) -> str:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, name, "en", "official")
        return id

    for place, code in g.gewogs.items():
        add("bt.gewog", code, place.name, dzongkhag_area(place.dzongkhag))
    for (place, name), code in g.chiwogs.items():
        add("bt.chiwog", code, name, area("bt.gewog", g.gewogs[place]))
    for place, code in g.thromdes.items():
        add("bt.thromde", code, place.name, dzongkhag_area(place.dzongkhag))
    for (place, name), code in g.constituencies.items():
        add("bt.thromde_constituency", code, name, area("bt.thromde", g.thromdes[place]))
    return p


def registry(records: list[Record]) -> Package:
    g = geography(records)
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Gup and Mangmi offices and the Gewog Tshogde, Thromde Tshogde, and Dzongkhag Tshogdu bodies of the 2011 "
        "Local Government elections, with the Election Commission's result files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BHUTAN}
    for url in [PAGE, *urls()]:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for post, id in OFFICES.items():
        p.add("office", id=id, polity_id=BHUTAN, kind="other")
        p.name(id, post, "en", "official")
    for place in g.gewogs:
        id = gewog_body(place)
        p.add("body", id=id, polity_id=BHUTAN, kind="council")
        p.name(id, f"{place.name} Gewog Tshogde", "en", "official")
    for place in g.thromdes:
        if place.name.removesuffix(" Thromde") in CLASS_A:
            id = thromde_body(place)
            p.add("body", id=id, polity_id=BHUTAN, kind="council")
            p.name(id, f"{place.name} Tshogde", "en", "official")
    for dz in sorted({place.dzongkhag for place in g.thromdes if place.name.removesuffix(" Thromde") not in CLASS_A}):
        id = dzongkhag_body(dz)
        p.add("body", id=id, polity_id=BHUTAN, kind="council")
        p.name(id, f"{DZONGKHAGS[dz]} Dzongkhag Tshogdu", "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=BHUTAN,
        valid_from=DAY,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "One member for each gewog (Gup and Mangmi), chiwog (Tshogpa), thromde constituency, or yenlag thromde "
            "(Thromde Thuemi), elected by the most votes. A candidate with no opponent stands on a yes or no vote."
        ),
        lang="en",
    )
    return p


def gewog_body(place: Place) -> str:
    return key("body", "gewog", place.dzongkhag, place.name)


def thromde_body(place: Place) -> str:
    return key("body", "thromde", place.dzongkhag, place.name)


def dzongkhag_body(dz: int) -> str:
    return key("body", "dzongkhag", dz)


@dataclass
class Contest:
    place: Place
    post: str
    chiwog: str
    rows: list[Record]

    @property
    def uncontested(self) -> bool:
        return any(r.uncontested for r in self.rows)


def contests(records: list[Record]) -> list[Contest]:
    found: dict[tuple[int, str, str, str], Contest] = {}
    for r in records:
        place = place_of(r)
        chiwog = clean(r.chiwog) if r.post in {"Tshogpa", "Thromde Tshogpa"} else ""
        found.setdefault((place.dzongkhag, place.name, r.post, chiwog), Contest(place, r.post, chiwog, [])).rows.append(
            r
        )
    for c in found.values():
        if len({r.uncontested for r in c.rows}) > 1:
            # The contested file also lists a candidate who stood unopposed, with the yes votes and no remark that the
            # uncontested file repeats as a yes or no vote; the yes or no vote is the contest.
            yes = [r for r in c.rows if r.uncontested]
            c.rows = [
                r
                for r in c.rows
                if r.uncontested
                or not any((r.name, r.evm, r.postal, r.total) == (y.name, y.evm, y.postal, y.total) for y in yes)
            ]
        if len({r.uncontested for r in c.rows}) > 1 or (c.uncontested and len(c.rows) != 1):
            raise ValueError(f"{c.place} {c.post} {c.chiwog}: contested and uncontested rows mix")
    return sorted(
        found.values(), key=lambda c: (c.place.dzongkhag, c.place.name.casefold(), c.post, c.chiwog.casefold())
    )


def value(p: Package, result_set: str, unit: str, option: str, number: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=VOTES,
        count_round=0,
        flags=0,
        value=number,
    )


def build(records: list[Record]) -> Package:
    g = geography(records)
    p = Package(
        f"{SOURCE}/2011",
        SOURCE,
        "The 2011 Local Government elections by gewog, chiwog, and thromde from the Election Commission of Bhutan",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BHUTAN}
    path_ = "bt/2011-lg"
    p.add(
        "election",
        id=ELECTION,
        polity_id=BHUTAN,
        kind="local",
        date_from=DAY,
        date_to=END,
        date_precision="year",
        status="held",
    )
    p.name(ELECTION, "2011 Local Government elections", "en", "official")
    p.add("slug", path=path_, entity_id=ELECTION, is_current=True)
    children: dict[int, str] = {}
    for dz in sorted({place.dzongkhag for place in g.gewogs} | {place.dzongkhag for place in g.thromdes}):
        id = children[dz] = key("election", "2011", dz)
        p.add(
            "election",
            id=id,
            polity_id=BHUTAN,
            parent_id=ELECTION,
            kind="local",
            date_from=DAY,
            date_to=END,
            date_precision="year",
            status="held",
        )
        p.name(id, f"2011 Local Government elections: {DZONGKHAGS[dz]} dzongkhag", "en", "official")
        p.add("slug", path=f"{path_}/{dz + 1:02d}", entity_id=id, is_current=True)
    units: dict[str, tuple[str, str]] = {}
    for c in contests(records):
        add_contest(p, g, c, children[c.place.dzongkhag], path_, units)
    return p


def reporting_units(
    p: Package, units: dict[str, tuple[str, str]], scheme: str, code: str, area_id: str, label: str
) -> tuple[str, str]:
    """The machine and postal units of an area, made once for all the area's contests."""
    if code in units:
        return units[code]
    made = []
    for suffix, kind, text in (
        ("evm", "standard", "voting machines"),
        ("postal", "postal_district", "postal ballots"),
    ):
        id = key("unit", scheme, code, suffix)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=BHUTAN,
            scheme_code=scheme,
            code=f"2011:{code}:{suffix}",
            area_relation="within",
            area_id=area_id,
            kind=kind,
        )
        p.name(id, f"{label}, {text}", "en", "official")
        made.append(id)
    units[code] = (made[0], made[1])
    return units[code]


def add_contest(
    p: Package, g: Geography, c: Contest, election: str, path_: str, units: dict[str, tuple[str, str]]
) -> None:
    dz = c.place.dzongkhag
    if c.post in {"Gup", "Mangmi"}:
        code = g.gewogs[c.place]
        scheme, office, body = "bt.gewog", OFFICES[c.post], None
        label = f"{c.place.name} gewog"
        name = f"{c.place.name} {c.post}"
        slug = f"{code.split('.')[1]}-{c.post.lower()}"
        area_code = code
    elif c.post == "Tshogpa":
        code = g.chiwogs[c.place, c.chiwog]
        scheme, office, body = "bt.chiwog", None, gewog_body(c.place)
        label = f"{c.chiwog} chiwog, {c.place.name} gewog"
        name = f"{c.place.name} Gewog Tshogde: {c.chiwog} chiwog"
        slug = ".".join(code.split(".")[1:])
        area_code = code
    elif c.post == "Thromde Thuemi":
        code = g.thromdes[c.place]
        scheme, office, body = "bt.thromde", None, dzongkhag_body(dz)
        label = c.place.name
        name = f"{c.place.name} Thuemi to the {DZONGKHAGS[dz]} Dzongkhag Tshogdu"
        slug = f"{code.split('.')[1]}-thuemi"
        area_code = code
    else:
        code = g.constituencies[c.place, c.chiwog]
        scheme, office, body = "bt.thromde_constituency", None, thromde_body(c.place)
        label = f"{c.chiwog} constituency, {c.place.name}"
        name = f"{c.place.name} Tshogde: {c.chiwog} constituency"
        slug = code.split(".", 1)[1]
        area_code = code
    area_id = area(scheme, area_code)
    evm, postal = reporting_units(p, units, scheme, area_code, area_id, label)
    tag = f"{scheme}:{area_code}:{c.post}"
    cid = key("contest", tag)
    p.add(
        "contest",
        id=cid,
        election_id=election,
        kind="office" if office else "body_seats",
        office_id=office,
        body_id=body,
        district_area_id=area_id,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(cid, name, "en", "official")
    p.add("slug", path=f"{path_}/{dz + 1:02d}/{slug}", entity_id=cid, is_current=True)
    rs = key("result_set", tag)
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    note = METHOD + (YESNO if c.uncontested else "")
    odd = [r for r in c.rows if r.evm + r.postal != r.total]
    if odd:
        note += (
            " The printed total of "
            + ", ".join(f"{clean(r.name)} ({r.total:,})" for r in odd)
            + " does not equal the machine and postal counts printed beside it; both counts are stored as printed."
        )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
    for position, r in enumerate(c.rows, 1):
        person = key("person", tag, position)
        option = key("option", tag, position)
        candidate = clean(r.name)
        p.add("person", id=person)
        p.name(person, candidate, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="candidate",
            ballot_position=position,
            ballot_label=candidate,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate,
        )
        outcome = "elected" if r.elected else "tied" if r.tied else "lost"
        p.add("outcome", result_set_id=rs, option_id=option, kind=outcome)
        value(p, rs, evm, option, r.evm)
        value(p, rs, postal, option, r.postal)
        if r.uncontested and r.no is not None:
            no = key("option", tag, "no")
            p.add(
                "contest_option",
                id=no,
                contest_id=cid,
                kind="none_of_the_above",
                ballot_position=len(c.rows) + 1,
                ballot_label="No",
            )
            value(p, rs, evm, no, r.no[0])
            value(p, rs, postal, no, r.no[1])


def summary(records: list[Record]) -> dict[str, object]:
    """What the files add up to, for the run's report."""
    return {
        "candidates": len(records),
        "by_post": {post: sum(r.post == post for r in records) for post in sorted({r.post for r in records})},
        "printed_total_mismatches": sum(r.evm + r.postal != r.total for r in records),
        "votes": sum(r.total for r in records),
    }
