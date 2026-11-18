"""Montenegro: direct mayoral elections under the 2003 law, from the municipal commissions' reports in the gazette.

The Law on the Election of the Mayor (Zakon o izboru predsjednika opštine, Sl. list RCG 42/2003) had citizens elect
each municipality's mayor directly, by a majority of the valid votes with a second round of the top two 14 days later;
the amendments of December 2009 gave the choice back to the municipal assembly. Municipal election commissions
published their reports on the results in the official gazette. The gazette's register (sluzbenilist.me/propisi)
serves each act as an HTML page with the printed page embedded as an image. Searching every issue of the Republic
series (2003-2007), the municipal series (2007-2010), and the national series (2006-2010) finds three such reports:
Tivat (29 March 2009), Žabljak (16 August 2009), and Kotor (8 November 2009). Each figure was read from the page
image, read again by OCR, and checked against the report's own sums; each keeps its page and box.
"""

import base64
import io
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from PIL import Image

from ea import package, raw
from ea.package import Package

SOURCE = "me.sluzbenilist"
MONTENEGRO = str(uuid5(package.NAMESPACE, "polity\x1fME"))
SCHEME = "me.opstina"  # held by me.dik/registry
REGISTER = "https://www.sluzbenilist.me/propisi"
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
ALL, ELECTION_DAY, POSTAL = 0, 1, 3
DIGITIZED = 8
LAW = f"{REGISTER}/307015"

Box = tuple[float, float, float, float]  # pixels of the 720 x 931 page image, from the top left


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


@dataclass(frozen=True)
class Party:
    tag: str
    name: str
    kind: str


PARTIES = {
    p.tag: p
    for p in (
        Party("dps", "Demokratska partija socijalista Crne Gore", "party"),
        Party("lp", "Liberalna partija Crne Gore", "party"),
        Party("snp", "Socijalistička narodna partija Crne Gore", "party"),
        Party("nsd", "Nova srpska demokratija", "party"),
        Party("evropski-tivat", "Koalicija „Evropski Tivat - Dragan Kankaraš“", "bloc"),
        Party("snp-tivat", "SNP - Đoko Vukmirović - Za Tivat jednakih šansi", "bloc"),
    )
}


@dataclass(frozen=True)
class Candidate:
    name: str  # given name first
    label: str  # the ballot line as the report prints it
    party: str | None  # the proposer's tag; None for a group of citizens
    votes: int
    box: Box


@dataclass(frozen=True)
class Report:
    place: str
    slug: str
    day: date
    item: int  # the act's number in the gazette register
    gazette: str
    title: str
    candidates: tuple[Candidate, ...]
    # (measure, channel, value, box) for each accounting figure the report prints
    figures: tuple[tuple[int, int, int, Box], ...]
    elected: int | None  # the index of the candidate the commission declares elected
    method: str
    lists: int | None = None  # the register item of the commission's list of candidates, when it names the proposers


REPORTS = (
    Report(
        "Tivat",
        "tivat",
        date(2009, 3, 29),
        299791,
        "Službeni list Crne Gore - Opštinski propisi, br. 13/2009",
        "Izvještaj o konačnim rezultatima glasanja za izbor predsjednika Opštine Tivat",
        (
            Candidate(
                "Miodrag Kankaraš",
                "Miodrag Kankaraš - Kandidat Koalicije „Evropski Tivat - Dragan Kankaraš“",
                "evropski-tivat",
                4067,
                (548, 228, 582, 247),
            ),
            Candidate(
                "Đoko Vukmirović",
                "Đoko Vukmirović - Kandidat „SNP - Đoko Vukmirović - Za Tivat jednakih šansi“",
                "snp-tivat",
                849,
                (507, 251, 531, 268),
            ),
            Candidate(
                "Rato Brajković", "Rato Brajković - Kandidat Nove srpske demokratije", "nsd", 1209, (307, 282, 339, 300)
            ),
        ),
        (),
        None,
        "The report of the Tivat municipal election commission of 2 April 2009 gives only each candidate's votes, "
        "from the material of every polling station in the municipality. It prints no electorate, turnout, or invalid "
        "ballots, and it does not name the elected candidate; no outcome is stored. Miodrag Kankaraš's 4,067 votes "
        "are 66.4 percent of the 6,125 votes the three candidates received, more than the half that article 22 of the "
        "law requires. The mayoral vote was held on the day of the parliamentary election.",
    ),
    Report(
        "Žabljak",
        "zabljak",
        date(2009, 8, 16),
        299334,
        "Službeni list Crne Gore - Opštinski propisi, br. 28/2009",
        "Konačni rezultati izbora za predsjednika opštine Žabljak koji su održani 16. avgusta 2009. godine",
        (
            Candidate("Dobrica Šljivančanin", "DOBRICA ŠLJIVANČANIN", "snp", 1092, (325, 418, 361, 437)),
            Candidate("Isailo Šljivančanin", "ISAILO ŠLJIVANČANIN", "dps", 1431, (321, 428, 360, 446)),
            Candidate("Ratko Vuković", "RATKO VUKOVIĆ", None, 53, (344, 443, 360, 457)),
            Candidate("Danko Stijepović", "DANKO STIJEPOVIĆ", None, 103, (323, 452, 350, 467)),
        ),
        (
            (REGISTERED, ALL, 3367, (498, 259, 536, 277)),
            (VOTERS, ELECTION_DAY, 2486, (299, 322, 333, 340)),
            (VOTERS, POSTAL, 283, (413, 334, 436, 352)),
            (CAST, ALL, 2769, (167, 377, 201, 394)),
            (INVALID, ALL, 90, (163, 386, 182, 404)),
            (VALID, ALL, 2679, (163, 399, 201, 415)),
        ),
        1,
        "The final results the Žabljak municipal election commission set on 19 August 2009 from the polling boards' "
        "reports. Of 3,367 voters on the roll, 2,486 voted at polling stations and 283 outside them, by letter, which "
        "are stored as election-day and postal voters. Of the 3,367 ballots received, 598 went unused and 2,769 were "
        "used (stored as ballots cast); 90 were invalid and 2,679 valid. The commission declared Isailo Šljivančanin "
        "elected with more than half the valid votes. The proposers come from the commission's single list of "
        "candidates of 23 July 2009: Dobrica Šljivančanin by the Socialist People's Party's municipal board, Isailo "
        "Šljivančanin by the municipal board of the Democratic Party of Socialists, and the other two by groups of "
        "citizens.",
        299000,
    ),
    Report(
        "Kotor",
        "kotor",
        date(2009, 11, 8),
        304900,
        "Službeni list Crne Gore - Opštinski propisi, br. 36/2009",
        "Izvještaj o konačnim rezultatima izbora za predsjednika Opštine Kotor",
        (
            Candidate(
                "Marija Maja Ćatović",
                "Marija Maja Ćatović, kandidat Demokratske partije socijalista CG",
                "dps",
                5219,
                (535, 469, 569, 490),
            ),
            Candidate(
                "Andrija Pura Popović",
                "Andrija Pura Popović, kandidat Liberalne partije Crne Gore",
                "lp",
                1617,
                (505, 483, 536, 501),
            ),
        ),
        (
            (REGISTERED, ALL, 17897, (268, 335, 305, 352)),
            (VOTERS, ELECTION_DAY, 6928, (400, 345, 428, 362)),
            (VOTERS, POSTAL, 360, (433, 356, 457, 371)),
            (CAST, ALL, 7288, (168, 409, 205, 426)),
            (INVALID, ALL, 452, (176, 422, 203, 437)),
            (VALID, ALL, 6836, (173, 432, 206, 447)),
        ),
        0,
        "The final results the Kotor municipal election commission set on 11 November 2009, from all 36 polling "
        "stations and the polling station in the Podgorica prison and the Spuž remand prison. Of 17,897 voters on the "
        "roll, 6,928 voted at polling stations and 360 outside them, by letter, which are stored as election-day and "
        "postal voters. Of the 17,897 ballots received, 10,609 went unused and 7,288 were used (stored as ballots "
        "cast, matching the 7,288 control coupons); 452 were invalid and 6,836 valid. The commission declared Marija "
        "Maja Ćatović elected with more than half the valid votes.",
    ),
)

SYSTEM_NOTE = (
    "The mayor is elected directly by the municipality's voters for five years. A candidate with more than half the "
    "valid votes is elected; otherwise the two leading candidates meet in a second round 14 days later (Zakon o izboru "
    "predsjednika opštine, Sl. list RCG 42/2003, arts. 22-23)."
)


def url(item: int) -> str:
    return f"{REGISTER}/{item}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{r.day}" for r in REPORTS)]


def fetch() -> None:
    for item in sorted({LAW, *(url(r.item) for r in REPORTS), *(url(r.lists) for r in REPORTS if r.lists)}):
        raw.fetch(SOURCE, item, suffix=".html")


def page(item: int) -> tuple[raw.Artifact, int, int]:
    """The cached act and the size of the first page image embedded in it."""
    stored = raw.cached(SOURCE, url(item))
    found = re.search(r'src="data:image/jpeg;base64,([^"]+)"', stored.path.read_text())
    if found is None:
        raise ValueError(f"{url(item)} embeds no page image")
    width, height = Image.open(io.BytesIO(base64.b64decode(found[1]))).size
    return stored, width, height


def verify(r: Report) -> None:
    """Hold the transcription to the report's own sums, so a misread figure cannot pass."""
    got = {(m, c): v for m, c, v, _ in r.figures}
    total = sum(c.votes for c in r.candidates)
    if got:
        if got[VALID, ALL] != total:
            raise ValueError(f"{r.place}: the candidates have {total} votes, the report prints {got[VALID, ALL]} valid")
        if got[VALID, ALL] + got[INVALID, ALL] != got[CAST, ALL]:
            raise ValueError(f"{r.place}: valid and invalid ballots do not add up to the ballots used")
        if got[VOTERS, ELECTION_DAY] + got[VOTERS, POSTAL] != got[CAST, ALL]:
            raise ValueError(f"{r.place}: the voters at and outside the polling stations do not add up")
    if r.elected is not None:
        winner = r.candidates[r.elected].votes
        if 2 * winner <= total or winner != max(c.votes for c in r.candidates):
            raise ValueError(f"{r.place}: the declared winner does not have a majority")


def parse() -> None:
    registry().write()
    for r in REPORTS:
        build(r).write()


def emit() -> None:
    package.run("load", packages())


GAZETTE = key("source", "gazette")
SYSTEM = key("electoral_system", "two-round")


def office(r: Report) -> str:
    return key("office", r.slug)


def party(tag: str) -> str:
    return key("party", tag)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Montenegro's directly elected mayors of 2009, their two-round system, and the parties that proposed the "
        "candidates, from the municipal election commissions' acts in the official gazette",
    )
    p.external.add(MONTENEGRO)
    p.add(
        "source",
        id=GAZETTE,
        kind="official_body",
        publisher="JU Službeni list Crne Gore",
        title="Službeni list Crne Gore: register of published acts (propisi)",
        url=REGISTER,
        license="Official gazette; acts of state bodies are not protected by copyright (Zakon o autorskom i srodnim "
        "pravima, art. 9)",
        attribution="Službeni list Crne Gore",
    )
    p.artifact(raw.cached(SOURCE, LAW), GAZETTE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MONTENEGRO,
        valid_from=date(2003, 7, 16),
        valid_to=date(2010, 1, 8),
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for r in REPORTS:
        p.add("office", id=office(r), polity_id=MONTENEGRO, body_id=None, kind="mayor")
        p.name(office(r), f"Mayor of {r.place}", "en", "official")
        p.name(office(r), f"Predsjednik Opštine {r.place}", "sr", "native", False)
    for tag, info in PARTIES.items():
        p.add("party", id=party(tag), kind=info.kind, polity_id=MONTENEGRO)
        p.name(party(tag), info.name, "sr", "official")
    return p


def build(r: Report) -> Package:
    verify(r)
    p = Package(
        f"{SOURCE}/{r.day}",
        SOURCE,
        f"The direct election of the mayor of {r.place} on {r.day}, from the municipal election commission's report "
        "in the official gazette",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {MONTENEGRO, SCHEME}
    stored, width, height = page(r.item)
    artifact = p.artifact(stored, GAZETTE)
    if r.lists is not None:
        p.artifact(raw.cached(SOURCE, url(r.lists)), GAZETTE)
    sheet = key("page", r.item, 1)
    p.add(
        "source_page",
        id=sheet,
        artifact_id=artifact,
        page_number=1,
        image_key=f"pages/{SOURCE}/{stored.sha256}-1.jpg",
        width=width,
        height=height,
    )

    election = key("election", r.day, r.slug)
    base = f"me/{r.day}-mayor-{r.slug}"
    p.add(
        "election",
        id=election,
        polity_id=MONTENEGRO,
        kind="local",
        date_from=r.day,
        date_to=r.day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{r.day.year} {r.place} mayoral election", "en", "official")
    p.name(election, f"Izbori za predsjednika Opštine {r.place} {r.day.year}", "sr", "native", False)
    p.add("slug", path=base, entity_id=election, is_current=True)

    contest = key("contest", r.day, r.slug)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=office(r),
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"Mayor of {r.place}", "en", "official")
    p.name(contest, f"Predsjednik Opštine {r.place}", "sr", "native", False)
    p.add("slug", path=f"{base}/mayor", entity_id=contest, is_current=True)
    p.add(
        "citation",
        id=key("citation", contest),
        entity_id=contest,
        source_id=GAZETTE,
        locator={"url": url(r.item), "gazette": r.gazette, "title": r.title},
    )

    unit = key("unit", r.day, r.slug)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MONTENEGRO,
        scheme_code=SCHEME,
        code=f"{r.day}:mayor:{r.place}",
        area_relation="unlocated",
        area_id=None,
        kind="standard",
    )
    p.name(unit, r.place, "sr", "official")

    result_set = key("result_set", r.day, r.slug)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=GAZETTE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=r.method, lang="en")

    def put(option: str | None, measure: int, channel: int, value: int, box: Box) -> None:
        cell = {"result_set_id": result_set, "unit_id": unit, "option_id": option, "channel": channel}
        p.add("result_value", **cell, measure=measure, count_round=0, flags=DIGITIZED, value=value)
        x0, y0, x1, y1 = box
        p.add(
            "value_locator",
            **cell,
            measure=measure,
            count_round=0,
            page_id=sheet,
            source_id=GAZETTE,
            x0=float(x0),
            y0=float(y0),
            x1=float(x1),
            y1=float(y1),
        )

    for measure, channel, value, box in r.figures:
        put(None, measure, channel, value, box)
    for position, c in enumerate(r.candidates, 1):
        person = key("person", r.slug, c.name)
        p.add("person", id=person)
        p.name(person, c.name, "sr", "official")
        option = key("option", r.day, r.slug, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if c.party is None else party(c.party),
            ballot_position=position,
            ballot_label=c.label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", r.day, r.slug, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=None,
            is_independent=c.party is None,
        )
        put(option, VOTES, ALL, c.votes, c.box)
        if r.elected is not None:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if position - 1 == r.elected else "lost",
            )
    print(f"{p.key}: {len(r.candidates)} candidates", flush=True)
    return p
