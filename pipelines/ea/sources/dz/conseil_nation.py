"""Algeria: the renewals of the elected two thirds of the Council of the Nation, from the proclamations in the Journal
officiel.

Two seats per wilaya are filled by an electoral college of the wilaya's communal and wilaya assembly members, and half
of the elected seats are renewed every three years. After each poll the Constitutional Council (the Constitutional
Court since 2022) proclaims the result in the Journal officiel of the Secrétariat général du Gouvernement: for each
wilaya the electors registered, voting and abstaining, the valid and null ballots, the member elected, and the votes
that member obtained. The proclamations print no other candidate's votes, so those are not stored."""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.dz import tables
from ea.sources.dz.tables import Member, Row

SOURCE = "dz.conseil_nation"
JO = "https://www.joradp.dz/FTP/jo-francais"
DIGITIZED = Path(__file__).with_name("digitized.json")
ALGERIA = str(uuid5(package.NAMESPACE, "polity\x1fDZ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fDZA"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5


def issue(year: int, number: int) -> str:
    return f"{JO}/{year}/F{year}{number:03d}.pdf"


@dataclass(frozen=True)
class Rerun:
    """A poll held again in wilayas whose result was annulled, proclaimed by an amending proclamation."""

    day: date
    file: str
    rows: tuple[Row, ...]
    reason: str
    expected: dict[str, int] | None = None


@dataclass(frozen=True)
class Election:
    day: date
    file: str
    proclamation: str
    nulls_first: bool = False
    new_seats: int = 1
    names: dict[tuple[int, int], str] = field(default_factory=dict)
    rerun: Rerun | None = None
    left: str = ""
    annulled: tuple[int, ...] = ()
    digitized: str = ""
    seats: int = 1


ELECTIONS = (
    Election(
        date(1997, 12, 25),
        issue(1997, 86),
        "Proclamation n° 02-97/P.CC of 27 December 1997 (Journal officiel n° 86 of 28 December 1997, pages 12 to 15)",
        digitized="1997",
        seats=2,
    ),
    Election(
        date(2000, 12, 30),
        issue(2001, 4),
        "Proclamation n° 01-P-CC-01 of 1 January 2001 (Journal officiel n° 4 of 14 January 2001, pages 5 and 6)",
        digitized="2000",
    ),
    Election(
        date(2003, 12, 30),
        issue(2004, 5),
        "Proclamation n° 01/P-CC/04 of 4 January 2004 (Journal officiel n° 5 of 18 January 2004)",
        nulls_first=True,
        left="Béjaïa and Tizi Ouzou are absent from the proclamation, which counts 46 wilayas, and the vote in El "
        "Bayadh was annulled by the Constitutional Council; the Journal officiel of 2004 prints no later poll "
        "for them.",
    ),
    Election(
        date(2006, 12, 28),
        issue(2007, 4),
        "Proclamation n° 01/P.CC/07 of 1 January 2007 (Journal officiel n° 4 of 14 January 2007)",
        nulls_first=True,
        rerun=Rerun(
            date(2007, 1, 8),
            issue(2007, 10),
            (
                Row(17, 391, 367, 24, 346, 21, members=[Member("Belabbès Belabbes", 211)]),
                Row(25, 192, 181, 11, 172, 9, members=[Member("Bounah Kamel", 104)]),
            ),
            "The Constitutional Council annulled the results of 28 December 2006 in the wilayas of Djelfa and "
            "Constantine (decisions 01/D.CC/07 and 02/D.CC/07 of 1 January 2007).",
        ),
    ),
    Election(
        date(2009, 12, 29),
        issue(2010, 6),
        "Proclamation n° 02/P.CC/09 of 31 December 2009 (Journal officiel n° 6 of 24 January 2010)",
        nulls_first=True,
        rerun=Rerun(
            date(2010, 1, 13),
            issue(2010, 6),
            (Row(30, 240, 238, 2, 233, 5, members=[Member("Gobbi Adem", 107)]),),
            "The Constitutional Council annulled the result of 29 December 2009 in the wilaya of Ouargla (decision "
            "01/D.CC/10 of 4 January 2010).",
        ),
        annulled=(30,),
    ),
    Election(
        date(2012, 12, 29),
        issue(2013, 2),
        "Proclamation n° 01/P.CC/13 of 1 January 2013 (Journal officiel n° 2 of 13 January 2013)",
    ),
    Election(
        date(2015, 12, 29),
        issue(2016, 2),
        "Proclamation n° 01/P.CC/16 of 1 January 2016 (Journal officiel n° 2 of 13 January 2016)",
    ),
    Election(
        date(2018, 12, 29),
        issue(2019, 5),
        "Proclamation n° 01/P.CC/19 of 4 January 2019 (Journal officiel n° 5 of 23 January 2019)",
        names={(43, 0): "BENCHAOUI Abdelouakil"},
        rerun=Rerun(
            date(2019, 1, 10),
            issue(2019, 9),
            (Row(13, 856, 846, 10, 763, 83, members=[Member("BEKHECHI Mohammed", 518)]),),
            "The Constitutional Council annulled the result of 29 December 2018 in the wilaya of Tlemcen "
            "(proclamation 01/P.CC/19).",
            {"registered": 26874, "voting": 26338, "abstaining": 536, "valid": 23748},
        ),
    ),
    Election(
        date(2022, 2, 5),
        issue(2022, 12),
        "Proclamation n° 01/PCC/22 of 10 February 2022 (Journal officiel n° 12 of 28 February 2022)",
        new_seats=2,
    ),
    Election(
        date(2025, 3, 9),
        issue(2025, 19),
        "Proclamation n° 01/P.C.C/25 of 13 March 2025 (Journal officiel n° 19 of 19 March 2025)",
    ),
)
FILES = sorted({e.file for e in ELECTIONS} | {e.rerun.file for e in ELECTIONS if e.rerun})


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


JORADP = key("source", "joradp")
BODY = key("body", "council_of_the_nation")
SYSTEM = key("electoral_system", "college_plurality")
BLOCK = key("electoral_system", "college_block")
TOTALS: dict[str, dict[str, object]] = {}
LEFT, RIGHT = 0.03, 0.97
ACCEPTED = (
    "The proclamation prints only the votes of the member elected in each wilaya, never the other candidates' "
    "votes, so each unit's stored candidate votes fall short of its valid ballots."
)


def wilaya(code: int) -> str:
    return key("area", "dz.wilaya", f"{code:02d}")


def packages() -> list[str]:
    days = [e.day for e in ELECTIONS] + [e.rerun.day for e in ELECTIONS if e.rerun]
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in sorted(days))]


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def path(url: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, url).storage_key


def parse() -> None:
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()
        if election.rerun:
            rerun(election).write()
    print(json.dumps(TOTALS, indent=1))


def emit() -> None:
    package.run("load", packages())


def source(p: Package) -> None:
    p.add(
        "source",
        id=JORADP,
        kind="official_body",
        publisher="Secrétariat général du Gouvernement, Journal officiel de la République algérienne",
        title="Journal officiel de la République algérienne démocratique et populaire: proclamations of the "
        "Constitutional Council and the Constitutional Court",
        url="https://www.joradp.dz/",
        license="No license stated; published by the Secrétariat général du Gouvernement",
        attribution="Secrétariat général du Gouvernement, Journal officiel de la République algérienne",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Algeria's 58 wilayas, the districts of the Council of the Nation, as the Journal officiel numbers and names them",
    )
    p.external |= {ALGERIA, COUNTRY}
    source(p)
    p.add("scheme", code="dz.wilaya", polity_id=ALGERIA, kind="administrative", rank=1, noun="wilaya", code_system=None)
    for code, (french, arabic) in enumerate(zip(tables.WILAYAS, tables.ARABIC, strict=True), 1):
        id = wilaya(code)
        p.add("area", id=id, scheme_code="dz.wilaya")
        p.add("area_code", area_id=id, code=f"{code:02d}")
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, french, "fr", "official")
        p.name(id, arabic, "ar", "native", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Algeria's Council of the Nation and the electoral systems of its elected two thirds, with the proclamations "
        "that declare each renewal",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ALGERIA}
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), JORADP)
    p.add("body", id=BODY, polity_id=ALGERIA, kind="upper_house")
    p.name(BODY, "Council of the Nation", "en", "official")
    p.name(BODY, "Conseil de la Nation", "fr", "official", False)
    p.name(BODY, "مجلس الأمة", "ar", "native", False)
    for id, rules, text in (
        (
            SYSTEM,
            {"formula": "fptp"},
            (
                "One member for a wilaya whose seat is up for renewal. The members of the wilaya's communal and "
                "wilaya assemblies form the electoral college and each casts one vote; the candidate with the most "
                "votes in the single round is elected."
            ),
        ),
        (
            BLOCK,
            {"formula": "block"},
            (
                "Two members for a wilaya filling both its seats. The members of the wilaya's communal and wilaya "
                "assemblies form the electoral college, and the two candidates with the most votes in the single "
                "round are elected."
            ),
        ),
    ):
        p.add(
            "electoral_system", id=id, polity_id=ALGERIA, valid_from=None, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


METHOD = (
    "Counted per wilaya by the electoral college's polling committee and proclaimed by the Constitutional Council "
    "(the Constitutional Court from 2022). Registered voters are the members of the electoral college, ballots cast "
    "are those who voted, and valid and invalid are the valid and null ballots. The proclamation prints only the "
    "votes of the member or members elected, so the other candidates' votes are not stored."
)


@dataclass(frozen=True)
class Where:
    """The page and the vertical band of a digitized row, as fractions of the page."""

    page: str
    y0: float
    y1: float


def value(p: Package, rs: str, unit: str, option: str | None, measure: int, number: int, where: Where | None) -> None:
    p.add(
        "result_value",
        result_set_id=rs,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=number,
    )
    if where is not None:
        p.add(
            "value_locator",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=where.page,
            x0=LEFT,
            y0=where.y0,
            x1=RIGHT,
            y1=where.y1,
        )


def artifact_id(url: str) -> str:
    """The id `Package.artifact` gives the cached file, so a package can cite a file the registry holds."""
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{JORADP}\x1f{raw.cached(SOURCE, url).storage_key}"))


def digitized(election: Election) -> tuple[list[Row], dict[str, int], dict[int, tuple[int, float, float]]]:
    """The rows of a scanned proclamation, read from the page images and checked against the printed totals."""
    table = json.loads(DIGITIZED.read_text())[election.digitized]
    rows, where = [], {}
    for item in table["rows"]:
        members = [Member(name, votes) for name, votes in item["members"]]
        rows.append(
            Row(
                item["code"],
                item["registered"],
                item["voting"],
                item["abstaining"],
                item["valid"],
                item["null"],
                members=members,
            )
        )
        where[item["code"]] = (item["page"], item["y0"], item["y1"])
    return rows, table["printed"], where


def read(election: Election) -> tuple[list[Row], dict[str, int]]:
    if election.digitized:
        return digitized(election)[:2]
    rows, totals = tables.parse(path(election.file), nulls_first=election.nulls_first, new_seats=election.new_seats)
    for (code, index), name in election.names.items():
        row = next(r for r in rows if r.code == code)
        row.members[index].name = name
    return rows, totals


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def add_election(p: Package, day: date, name: str, rerun_of: str | None) -> tuple[str, str]:
    eid = key("election", day)
    p.add(
        "election",
        id=eid,
        polity_id=ALGERIA,
        kind="indirect",
        date_from=day,
        date_to=day,
        status="held",
        rerun_of_id=rerun_of,
    )
    p.name(eid, name, "en", "official")
    path_ = f"dz/{day}-cn"
    p.add("slug", path=path_, entity_id=eid, is_current=True)
    return eid, path_


def contest(
    p: Package, eid: str, path_: str, day: date, row: Row, *, annulled: bool, expected: int, where: Where | None = None
) -> None:
    code = row.code
    seats = len(row.members)
    if seats != expected:
        raise ValueError(f"{day} {tables.WILAYAS[code - 1]}: {seats} members, expected {expected}")
    tag = f"{day}:{code:02d}"
    cid = key("contest", tag)
    p.add(
        "contest",
        id=cid,
        election_id=eid,
        kind="body_seats",
        body_id=BODY,
        district_area_id=wilaya(code),
        magnitude=seats,
        round_number=1,
        electoral_system_id=SYSTEM if seats == 1 else BLOCK,
        default_result_kind="counted",
    )
    french = tables.WILAYAS[code - 1]
    p.name(cid, f"{french} wilaya", "en", "official")
    p.add("slug", path=f"{path_}/{code:02d}", entity_id=cid, is_current=True)
    rs = key("result_set", tag)
    p.add(
        "result_set",
        id=rs,
        election_id=eid,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=JORADP,
        status="annulled" if annulled else "active",
        is_default=not annulled,
        completeness=None,
    )
    note = METHOD + (
        " The Constitutional Council annulled this result and the poll was held again." if annulled else ""
    )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ALGERIA,
        scheme_code="dz.wilaya",
        code=tag,
        area_relation="equals",
        area_id=wilaya(code),
        kind="standard",
    )
    p.name(unit, french, "fr", "official")
    for measure, number in (
        (REGISTERED, row.registered),
        (BALLOTS, row.voting),
        (VALID, row.valid),
        (INVALID, row.null),
    ):
        if number is None:
            raise ValueError(f"{tag}: no figure for measure {measure}")
        value(p, rs, unit, None, measure, number, where)
    for position, member in enumerate(row.members, 1):
        name = clean(member.name)
        if not name:
            raise ValueError(f"{tag}: member {position} has no name")
        option = key("option", tag, position)
        person = key("person", tag, position)
        p.add("person", id=person)
        p.name(person, name, "fr", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=cid,
            kind="candidate",
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        value(p, rs, unit, option, VOTES, member.votes, where)
        p.add("outcome", result_set_id=rs, option_id=option, kind="elected", seats=1)


def verify(label: str, rows: list[Row], printed: dict[str, int]) -> None:
    """Compare the sums of the stored counts with the totals printed in the proclamation, and record the result."""
    live = [r for r in rows if not r.annulled]
    ours = {
        "registered": sum(r.registered or 0 for r in live),
        "voting": sum(r.voting or 0 for r in live),
        "abstaining": sum(r.abstaining or 0 for r in live),
        "valid": sum(r.valid or 0 for r in live),
        "null": sum(r.null or 0 for r in live),
    }
    for r in live:
        if (r.valid or 0) + (r.null or 0) != r.voting or (r.registered or 0) - (r.voting or 0) != r.abstaining:
            raise ValueError(f"{label}: wilaya {r.code} does not add up")
    compared = {}
    for name, given in printed.items():
        compared[name] = {"ours": ours[name], "printed": given}
        if ours[name] != given:
            raise ValueError(f"{label}: {name} is {ours[name]:,} but the proclamation prints {given:,}")
    TOTALS[label] = {"ours": ours, "compared": compared, "members": sum(len(r.members) for r in live)}


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day.year} renewal of the elected members of Algeria's Council of the Nation by wilaya, from the "
        "proclamation in the Journal officiel",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ALGERIA, COUNTRY}
    p.uses(raw.cached(SOURCE, election.file))
    if election.seats == 1:
        p.accept("accounting.votes_valid", ACCEPTED)
    rows, printed = read(election)
    verify(str(day), rows, printed)
    pages: dict[int, Where] = {}
    places: dict[int, Where] = {}
    if election.digitized:
        table = json.loads(DIGITIZED.read_text())[election.digitized]
        for number, size in table["pages"].items():
            id = key("page", day, number)
            pages[int(number)] = Where(id, 0, 0)
            p.add(
                "source_page",
                id=id,
                artifact_id=artifact_id(election.file),
                page_number=int(number),
                image_key=f"pages/{SOURCE}/{Path(election.file).stem}-{int(number):02d}.png",
                width=size["width"],
                height=size["height"],
            )
        places = {code: Where(pages[page].page, y0, y1) for code, (page, y0, y1) in digitized(election)[2].items()}
    eid, path_ = add_election(p, day, f"{day.year} Council of the Nation election", None)
    for row in rows:
        if row.annulled:
            continue
        seats = election.seats if election.seats > 1 else election.new_seats if row.code in tables.NEW else 1
        contest(
            p, eid, path_, day, row, annulled=row.code in election.annulled, expected=seats, where=places.get(row.code)
        )
    scan = (
        " The page is a scan: the figures were read from the page images, and each column of the wilaya rows adds up to "
        "the national total the proclamation prints."
        if election.digitized
        else ""
    )
    note = f"{election.proclamation}.{scan} {election.left}".strip()
    p.add("note", id=key("note", eid), entity_id=eid, kind="methodology", body_md=note, lang="en")
    return p


def rerun(election: Election) -> Package:
    again = election.rerun
    assert again is not None
    day = again.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The poll of {day:%-d %B %Y} that repeated an annulled vote for the Council of the Nation, from the "
        "proclamation in the Journal officiel",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{election.day}"],
    )
    p.external |= {ALGERIA, COUNTRY}
    p.uses(raw.cached(SOURCE, again.file))
    p.accept("accounting.votes_valid", ACCEPTED)
    rows = list(again.rows)
    verify(str(day), rows, {})
    if again.expected:
        # The amending proclamation prints new national totals: the original ones with the repeated wilayas added.
        kept = [r for r in read(election)[0] if r.code not in {x.code for x in rows} and not r.annulled]
        verify(f"{day} with the original poll", kept + rows, again.expected)
    eid, path_ = add_election(
        p, day, f"{day.year} Council of the Nation election, repeated poll", key("election", election.day)
    )
    for row in rows:
        contest(p, eid, path_, day, row, annulled=False, expected=1, where=None)
    p.add("note", id=key("note", eid), entity_id=eid, kind="methodology", body_md=again.reason, lang="en")
    return p
