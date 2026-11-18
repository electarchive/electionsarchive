"""Tanzania's National Assembly elections of 2015 and 2020, by constituency, from the Government Gazette.

The Union presidential elections printed in the same Gazettes are read in `president.py`.

The National Electoral Commission publishes the result of each general election in the Government Gazette. Notice
No. 1546 (Gazette of 28 October 2016) prints the 2015 result and General Notice No. 30B (special Gazette of 5 January
2021) the 2020 result. Each lists, for every constituency (jimbo la uchaguzi), the candidates with their party and
votes, then the candidate the Commission declares elected. The 2020 notice leaves out the constituencies whose member
was elected without opposition and lists those members in a table of their own.

The notices print no registered voters, ballots cast, or valid votes, and no national totals. Only the candidates'
votes and the declared winner are stored.
"""

import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.tz import text

SOURCE = "tz.inec"
TANZANIA = str(uuid5(package.NAMESPACE, "polity\x1fTZ"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fTZA"))
UPLOADS = "https://www.inec.go.tz/uploads/documents"
VOTES = 1


@dataclass(frozen=True)
class Election:
    year: int
    day: date
    gazette: str
    notices: str  # the notices of the presidential and National Assembly results
    published: str
    url: str
    first: int  # first and last PDF page that hold the constituencies
    last: int


ELECTIONS = {
    2015: Election(
        2015,
        date(2015, 10, 25),
        "Notice No. 1546",
        "Notices No. 1545 and 1546",
        "28 October 2016",
        f"{UPLOADS}/sw-1713443566-1513775610-21-MATOKEO%20YA%20UCHAGUZI%20MKUU%202015.pdf",
        22,
        58,
    ),
    2020: Election(
        2020,
        date(2020, 10, 28),
        "General Notice No. 30B",
        "General Notices No. 30A and 30B",
        "5 January 2021",
        f"{UPLOADS}/sw-1713180982-1624892417-matokeo%20final%205%20january%202021%20special%20gazzete.pdf",
        2,
        22,
    ),
}

# The members the 2020 notice lists as elected without opposition: name, region, constituency, as printed.
UNOPPOSED = (
    ("JOB YUSTINO NDUGAI", "DODOMA", "KONGWA"),
    ("CHUMI COSATO DAVID", "IRINGA", "MAFINGA MJINI"),
    ("ISACK ALOYCE KAMWELWE", "KATAVI", "KATAVI"),
    ("NAPE MOSES NNAUYE", "LINDI", "MTAMA"),
    ("MAJALIWA KASSIM MAJALIWA", "LINDI", "RUANGWA"),
    ("GEKUL PAULINE PHILIPO", "MANYARA", "BABATI MJINI"),
    ("SILLO DANIEL BARAN", "MANYARA", "BABATI VIJIJINI"),
    ("SAGINI JUMANNE ABDALLAH", "MARA", "BUTIAMA"),
    ("SHABIBY AHMED MABKHUT", "MOROGORO", "GAIRO"),
    ("KABUDI PALAMAGAMBA JOHN", "MOROGORO", "KILOSA"),
    ("KUNAMBI EMMANUEL GODWIN", "MOROGORO", "MLIMBA"),
    ("KALOGERIES INNOCENT EDWARD", "MOROGORO", "MOROGORO KUSINI"),
    ("TALETALE HAMISI SHABANI", "MOROGORO", "MOROGORO KUSINI"),
    ("ZEELAND JONAS VAN", "MOROGORO", "MVOMERO"),
    ("MNYETI ALEXANDER PASTORY", "MWANZA", "MISUNGWI"),
    ("KAMONGA JOSEPH ZACHARIUS", "NJOMBE", "LUDEWA"),
    ("SWALLE EDWIN ENOSY", "NJOMBE", "LUPEMBE"),
    ("KIKWETE RIDHIWANI JAKAYA", "PWANI", "CHALINZE"),
    ("JOSEPHAT SINKAMBA KANDEGE", "RUKWA", "KALAMBO"),
    ("JOSEPH KIZITO MHAGAMA", "RUVUMA", "MADABA"),
    ("IDDI KASSIM IDDI", "SHINYANGA", "MSALALA"),
    ("KWANDIKWA ELIAS JOHN", "SHINYANGA", "USHETU"),
    ("ENG. MSONGWE GODFREY KASEKENYA", "SONGWE", "ILEJE"),
    ("MULUGO PHILIPO AUGUSTINO", "SONGWE", "SONGWE"),
    ("REHEMA JUMA MIGILLA", "TABORA", "ULYANKULU"),
    ("DKT. KIGWANGALA HAMISI ANDREA", "TABORA", "NZEGA VIJIJINI"),
    ("JANUARY YUSUF MAKAMBA", "TANGA", "BUMBULI"),
    ("JUMA HAMIDU AWESO", "TANGA", "PANGANI"),
)
# The notice prints Morogoro Kusini for two of the members; the 2015 notice names the second constituency
# Morogoro Kusini Mashariki, and so does every other list of the constituencies.
UNOPPOSED_FIXES = {("TALETALE HAMISI SHABANI", "MOROGORO KUSINI"): "MOROGORO KUSINI MASHARIKI"}
# Constituencies the 2015 notice lists under Mbeya and the 2020 notice under Songwe.
MOVED = {("MBEYA", "SONGWE")}
# The 2015 notice declares a candidate who did not lead the count.
DECLARED_AGAINST_COUNT = {("MBEYA", "MBEYA VIJIJINI")}
# The 2015 notice lists a second candidate of one party, and prints one figure with a space inside it.
CAVEATS = {
    (2015, "KAVUU"): (
        "The notice prints two candidates of CHADEMA in this constituency, MANGWESHI Laurent Senga and KISESA "
        "Stanslaus Michael. Both are stored with the party as printed."
    ),
    (2015, "LIWALE"): "The notice prints the votes of MITAMBO Faith Mohamed as 1 8,327. It is stored as 18,327.",
    (2015, "NANYAMBA"): (
        "The notice prints one candidate, with 42,782 votes, and says that the candidate was elected without "
        "opposition (amepita bila kupingwa). The votes are stored as printed."
    ),
    (2015, "MBEYA VIJIJINI"): (
        "The notice declares ZELLA Adam Abraham of CHADEMA elected, although it prints more votes for ORAN Manase "
        "Njeza of CCM (56,307 against 52,298). The declared winner is stored as elected."
    ),
}

METHOD = (
    "Votes for each candidate in the constituency, as the National Electoral Commission published them in the "
    "Government Gazette ({gazette} of {published}). The notice prints no registered voters, ballots cast, or valid "
    "votes for the constituency. The candidate the notice declares elected is stored as elected."
)
UNOPPOSED_METHOD = (
    "The special Government Gazette of 5 January 2021 (General Notice No. 30B) lists this member in a table of "
    "members elected without opposition and prints no votes. The member is stored as elected, with no counted votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCES = {2015: key("source", "gazette-2015"), 2020: key("source", "gazette-2020")}
ASSEMBLY = key("body", "national-assembly")
SYSTEM = key("electoral_system", "constituency-plurality")


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/assembly-{year}" for year in ELECTIONS),
        *(f"{SOURCE}/president-{year}" for year in ELECTIONS),
    ]


def fetch() -> None:
    for election in ELECTIONS.values():
        raw.fetch(SOURCE, election.url, suffix=".pdf")


def parse() -> None:
    from ea.sources.tz import president

    geo().write()
    registry().write()
    for year in ELECTIONS:
        build(year).write()
        president.build(year).write()


def emit() -> None:
    package.run("load", packages())


def pretty(shout: str) -> str:
    """A place's name from the capitals the notices print: DAR ES SALAAM as Dar es Salaam, NYANG'HWALE as Nyang'hwale."""
    words = re.sub(r"[’‘]", "'", shout).lower().split()
    return " ".join(
        w if w == "es" else re.sub(r"(^|[-'])([a-z])", lambda m: m.group(1) + m.group(2).upper(), w, count=1)
        for w in words
    )


KEEP = ("AbdulAziz",)


def tidy(name: str) -> str:
    """A candidate's name as printed, with the spaces the text layer drops put back between run-together words."""
    name = re.sub(r"\s+", " ", name).strip()
    parts = re.split(f"({'|'.join(KEEP)})", name)
    return "".join(part if part in KEEP else _split(part) for part in parts)


def _split(name: str) -> str:
    name = re.sub(r"([A-Z]{2,})([A-Z][a-z])", r"\1 \2", name)
    return re.sub(
        r"([a-z])([A-Z][a-z])",
        lambda m: m.group(0) if m.group(1) + m.group(2)[0] in ("cC", "cD") else f"{m.group(1)} {m.group(2)}",
        name,
    )


@dataclass
class Candidate:
    name: str
    party: str
    votes: int | None
    elected: bool = False


@dataclass
class Seat:
    region: str  # the region the notice lists the constituency under
    name: str
    candidates: list[Candidate]
    area_region: str  # the region the constituency's area is keyed by
    unopposed: bool = False
    caveat: str = ""


@cache
def lines(year: int) -> list[str]:
    election = ELECTIONS[year]
    path = str(raw.cached(SOURCE, election.url).path)
    if year == 2015:
        return text.pdftext(path, election.first, election.last).split("\n")
    out: list[str] = []
    for page in range(election.first, election.last + 1):
        for x in (0, 306):
            out += text.pdftext(path, page, page, x, 306).split("\n")
    return out


def unopposed_words() -> set[str]:
    """The words of the table of members elected without opposition, which wraps names across lines."""
    path = str(raw.cached(SOURCE, ELECTIONS[2020].url).path)
    page = text.pdftext(path, 22, 22)
    return set(re.findall(r"[A-Z][A-Z.]*", page[page.index("WABUNGE WALIOCHAGULIWA BILA KUPINGWA") :]))


def winner_of(contest: text.Contest) -> int:
    """The candidate the notice declares elected: the one of the declared party, or the best match by name."""
    named = [i for i, c in enumerate(contest.candidates) if c.party == contest.winner_party]

    def words(name: str) -> set[str]:
        return {t for t in re.split(r"[\s.]+", tidy(name).casefold()) if len(t) > 2}

    if not named:
        raise ValueError(f"{contest.name}: {contest.winner!r} of {contest.winner_party} is not a candidate")
    best = max(named, key=lambda i: len(words(contest.winner) & words(contest.candidates[i].name)))
    if not words(contest.winner) & words(contest.candidates[best].name):
        raise ValueError(f"{contest.name}: {contest.winner!r} does not match {contest.candidates[best].name!r}")
    return best


@cache
def seats(year: int) -> tuple[Seat, ...]:
    found = text.parse(lines(year))
    prove(year, found)
    out: list[Seat] = []
    later = {c.name: c.region for c in text.parse(lines(2020))} if year == 2015 else {}
    for contest in found:
        region = contest.region
        keyed = later.get(contest.name, region)
        if keyed != region and (region, keyed) not in MOVED:
            raise ValueError(f"{contest.name}: listed under {region} in 2015 and {keyed} in 2020")
        candidates = [Candidate(tidy(c.name), c.party, c.votes) for c in contest.candidates]
        candidates[winner_of(contest)].elected = True
        out.append(Seat(region, contest.name, candidates, keyed, caveat=CAVEATS.get((year, contest.name), "")))
    if year == 2020:
        page = unopposed_words()
        for name, region, jimbo in UNOPPOSED:
            if not {*name.split(), *region.split(), *jimbo.split()} <= page:
                raise ValueError(f"the notice does not list {name} for {jimbo}")
            jimbo = UNOPPOSED_FIXES.get((name, jimbo), jimbo)
            out.append(Seat(region, jimbo, [Candidate(name, "CCM", None, True)], region, unopposed=True))
    if len(out) != 264 or len({(s.area_region, s.name) for s in out}) != 264:
        raise ValueError(f"{year}: {len(out)} constituencies")
    return tuple(out)


def prove(year: int, found: list[text.Contest]) -> None:
    """Check the parse against the lines: nothing left over, and every figure a line prints is a candidate's votes."""
    stray = [line for c in found for line in c.lines]
    if stray:
        raise ValueError(f"{year}: lines that fit nothing: {stray[:3]}")
    printed: list[int] = []
    inside = False
    for raw_line in lines(year):
        line = raw_line.strip()
        if text.STOP.match(line):
            break
        inside = inside or bool(text.region_header(line))
        if not inside or text.PAGE.search(line) or text.HEADER.match(line) or text.WINNER.match(line):
            continue
        hit = text.LEADER.match(line) or text.VOTES_ONLY.match(line) or text.GLUED.match(line)
        if hit and (hit.re is not text.GLUED or text.split_party(hit.group("label"))):
            printed.append(int(re.sub(r"[, ]", "", hit.group("votes"))))
    parsed = [c.votes for contest in found for c in contest.candidates if c.votes is not None]
    if sorted(printed) != sorted(parsed):
        raise ValueError(f"{year}: {len(printed)} figures printed, {len(parsed)} parsed")


def region_area(region: str) -> str:
    return key("area", "tz.region", changeset.slug(region))


def seat_area(region: str, name: str) -> str:
    return key("area", "tz.constituency", changeset.slug(region), changeset.slug(name))


def party_id(party: str) -> str:
    return key("party", changeset.slug(party))


def geo() -> Package:
    from ea.sources.tz import president

    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Tanzania's regions and the National Assembly constituencies of 2015 and 2020, keyed by region and name",
    )
    p.external |= {TANZANIA, TERRITORY}
    president.geo(p)
    for code, rank, kind, noun in (
        ("tz.region", 1, "administrative", "region"),
        ("tz.constituency", 2, "electoral_district", "constituency"),
    ):
        p.add("scheme", code=code, polity_id=TANZANIA, kind=kind, rank=rank, noun=noun)
    regions = sorted({s.area_region for year in ELECTIONS for s in seats(year)})
    for region in regions:
        id = region_area(region)
        p.add("area", id=id, scheme_code="tz.region")
        p.add("area_code", area_id=id, code=changeset.slug(region))
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, pretty(region), "sw", "official")
    placed: set[str] = set()
    for year in ELECTIONS:
        for s in seats(year):
            id = seat_area(s.area_region, s.name)
            if id in placed:
                continue
            placed.add(id)
            p.add("area", id=id, scheme_code="tz.constituency")
            p.add("area_code", area_id=id, code=f"{changeset.slug(s.area_region)}:{changeset.slug(s.name)}")
            p.add("area_parent", area_id=id, parent_id=region_area(s.area_region))
            p.name(id, pretty(s.name), "sw", "official")
    return p


def registry() -> Package:
    from ea.sources.tz import president

    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Tanzania's National Assembly and presidency, their electoral systems, and the parties named in the National "
        "Electoral Commission's notices of 2015 and 2020",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(TANZANIA)
    for year, election in ELECTIONS.items():
        p.add(
            "source",
            id=SOURCES[year],
            kind="official_body",
            publisher="Tume ya Taifa ya Uchaguzi (National Electoral Commission of Tanzania)",
            title=f"Government Gazette of the United Republic of Tanzania: {election.notices} of {election.published}, "
            f"results of the {year} general election for President and the National Assembly",
            url=election.url,
            license="Official publication; reuse terms are not stated",
            attribution="National Electoral Commission of Tanzania, Government Gazette of the United Republic of Tanzania",
        )
        p.artifact(raw.cached(SOURCE, election.url), SOURCES[year])
    president.registry(p)
    p.add("body", id=ASSEMBLY, polity_id=TANZANIA, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly of Tanzania", "en", "official")
    p.name(ASSEMBLY, "Bunge la Jamhuri ya Muungano wa Tanzania", "sw", "official", False)
    p.add("electoral_system", id=SYSTEM, polity_id=TANZANIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "One member for each constituency, elected by the most votes. The National Assembly also holds members "
            "for special seats, which the notices list by party and which this system does not cover."
        ),
        lang="en",
    )
    labels = sorted({c.party for year in ELECTIONS for s in seats(year) for c in s.candidates} | president.parties())
    for label in labels:
        id = party_id(label)
        p.add("party", id=id, kind="party", polity_id=TANZANIA)
        p.name(id, label, "en", "abbreviation")
        p.add("external_id", entity_id=id, scheme="tz.inec.party", value=changeset.slug(label))
    return p


def build(year: int) -> Package:
    election = ELECTIONS[year]
    p = Package(
        f"{SOURCE}/assembly-{year}",
        SOURCE,
        f"The {year} National Assembly election of Tanzania, by constituency, from the Government Gazette",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {TANZANIA}
    p.uses(raw.cached(SOURCE, election.url))
    id = key("election", election.day)
    p.add(
        "election",
        id=id,
        polity_id=TANZANIA,
        kind="general",
        date_from=election.day,
        date_to=election.day,
        status="held",
    )
    p.add("slug", path=f"tz/{election.day}", entity_id=id, is_current=True)
    p.name(id, f"{year} Tanzanian general election", "en", "official")
    for s in seats(year):
        _contest(p, year, id, s)
    for s in seats(year):
        if year == 2015 and (s.region, s.name) in DECLARED_AGAINST_COUNT:
            p.accept(
                "outcomes.winner",
                "The 2015 notice declares ZELLA Adam Abraham of CHADEMA elected in Mbeya Vijijini, while it prints more "
                "votes for ORAN Manase Njeza of CCM (56,307 against 52,298). The declared winner is stored as printed.",
            )
    return p


def _contest(p: Package, year: int, election: str, s: Seat) -> None:
    day = ELECTIONS[year].day
    tag = f"{day}:{changeset.slug(s.area_region)}:{changeset.slug(s.name)}"
    area = seat_area(s.area_region, s.name)
    shown = pretty(s.name)
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=TANZANIA,
        scheme_code="tz.constituency",
        code=f"{day}:{changeset.slug(s.area_region)}:{changeset.slug(s.name)}",
        area_relation="equals",
        area_id=area,
        kind="standard",
    )
    p.name(unit, shown, "sw", "official")
    p.add("reporting_unit_area", unit_id=unit, area_id=area, method="official_list", approximate=False, partial=False)
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=ASSEMBLY,
        district_area_id=area,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="tacit" if s.unopposed else "counted",
    )
    p.name(contest, f"{year} National Assembly election, {shown} constituency", "en", "official")
    p.add("slug", path=f"tz/{day}/assembly/{changeset.slug(s.name)}", entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if s.unopposed else "counted",
        source_id=SOURCES[year],
        status="active",
        is_default=True,
        completeness=0.0 if s.unopposed else 1.0,
    )
    e = ELECTIONS[year]
    method = UNOPPOSED_METHOD if s.unopposed else METHOD.format(gazette=e.gazette, published=e.published)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    caveats = [s.caveat] if s.caveat else []
    if s.region != s.area_region:
        caveats.append(
            f"The {year} notice lists this constituency under {pretty(s.region)} region. The 2020 notice lists it "
            f"under {pretty(s.area_region)} region, which keys its area."
        )
    if s.unopposed and s.name == "MOROGORO KUSINI MASHARIKI":
        caveats.append(
            "The notice prints Morogoro Kusini for this member and for the member of Morogoro Kusini. The 2015 notice "
            "names the second constituency Morogoro Kusini Mashariki."
        )
    for index, caveat in enumerate(caveats):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", body_md=caveat, lang="en")
    for position, c in enumerate(s.candidates, 1):
        person = key("person", tag, position)
        option = key("option", tag, position)
        party = party_id(c.party)
        p.add("person", id=person)
        p.name(person, c.name, "sw", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party,
            is_independent=False,
        )
        if c.votes is not None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=c.votes,
            )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if c.elected else "lost",
            seats=1 if c.elected else None,
        )
