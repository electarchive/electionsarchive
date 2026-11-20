"""Myanmar's general elections of 2010 and 2015 by constituency, from the Union Election Commission's results tables.

The commission published each legislature's results as a PDF table: one block per constituency with its voters,
ballots, rejected ballots, and valid votes, then one line per candidate with the party and the ordinary, advance, and
total votes. The tables use the Win Innwa legacy fonts, so `winfont.decode` turns each name into Unicode. A
constituency is stored only when its candidates' votes add up to the valid votes the table prints for it.
"""

import functools
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.mm import parties as party_table
from ea.sources.mm import pdftable
from ea.sources.mm.uec import MYANMAR
from ea.sources.mm.uec import PARTIES as PARTIES_2017
from ea.sources.mm.winfont import decode

SOURCE = "mm.uec"
WAYBACK = "https://web.archive.org/web"
RAW = {
    (2015, "pyithu"): (
        f"{WAYBACK}/20161129060128id_/http://uecmyanmar.org/images/stories/pdf/2015Result/2015%20Pyi%20Thu.pdf",
        8,
    ),
    (2015, "state"): ("browser://20261120-025050-abd44e/2015 S  D.pdf", 8),
    (2010, "pyithu"): (
        f"{WAYBACK}/20161201210513id_/http://uecmyanmar.org/images/stories/pdf/result/Pyithu2010.pdf",
        9,
    ),
}
DAYS = {2010: "2010-11-07", 2015: "2015-11-08"}
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
COUNT_SCHEME = "mm.general_count"
EXISTING_PARTIES = {code for code, _ in PARTIES_2017.values()}
STATES = {
    "ကချင်": ("kachin", "Kachin State"),
    "ကယား": ("kayah", "Kayah State"),
    "ကရင်": ("kayin", "Kayin State"),
    "ချင်း": ("chin", "Chin State"),
    "စစ်ကိုင်း": ("sagaing", "Sagaing Region"),
    "တနင်္သာရီ": ("tanintharyi", "Tanintharyi Region"),
    "ပဲခူး": ("bago", "Bago Region"),
    "မကွေး": ("magway", "Magway Region"),
    "မန္တလေး": ("mandalay", "Mandalay Region"),
    "မွန်": ("mon", "Mon State"),
    "ရခိုင်": ("rakhine", "Rakhine State"),
    "ရန်ကုန်": ("yangon", "Yangon Region"),
    "ရှမ်း": ("shan", "Shan State"),
    "ဧရာဝတီ": ("ayeyarwady", "Ayeyarwady Region"),
}
CHAMBERS = {
    "pyithu": ("lower_house", "Pyithu Hluttaw"),
}
METHOD = (
    "Published by the Union Election Commission as a table of results by constituency. Each candidate's votes are "
    "the total of the ordinary ballots and the advance ballots. Rejected ballots are the cancelled and the invalid "
    "ballots together, and valid votes are the votes for the candidates. The declared winner is the candidate with "
    "the most votes, as the table lists no winner."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UEC = key("source", "general-results")
SYSTEM = key("electoral_system", "general-fptp")


@dataclass
class Candidate:
    name: str | None
    party: str
    ordinary: int
    advance: int
    total: int


@dataclass
class Seat:
    code: str
    name: str
    state: str
    voters: int | None
    cast: int
    invalid: int
    valid: int
    candidates: list[Candidate]


def state_of(legacy: str) -> tuple[str, str]:
    text = decode(legacy).replace(" ", "")
    for prefix, found in STATES.items():
        if text.startswith(prefix):
            return found
    raise ValueError(f"unknown state {text!r}")


def party_code(text: str) -> tuple[str, str | None, str] | None:
    """The code, English name, and Burmese name of a party, None for an independent, and a ValueError for noise."""
    burmese = decode(text)
    found = party_table.norm(burmese)
    if found == party_table.INDEPENDENT:
        return None
    if found in party_table.PARTIES:
        return party_table.PARTIES[found]
    prefix = [k for k in party_table.PARTIES if len(found) >= 14 and k.startswith(found)]
    if len(prefix) == 1:
        return party_table.PARTIES[prefix[0]]
    if len(found) >= 14 and "\ufffd" not in burmese and re.search(r"ပါတီ|အဖွဲ့ချုပ်|အဖွဲ့$", burmese):
        return "x" + hashlib.sha1(found.encode()).hexdigest()[:8], None, burmese
    raise ValueError(f"unknown party {burmese!r}")


@functools.cache
def read(year: int, chamber: str) -> tuple[list[Seat], list[str]]:
    """The constituencies whose candidates' votes add up to the printed valid votes, and the ones left out."""
    url, header = RAW[(year, chamber)]
    found, _ = pdftable.seats(pdftable.text(raw.cached(SOURCE, url).path), header)
    seats: list[Seat] = []
    left: list[str] = []
    count: Counter[str] = Counter()
    for s in found:
        if not s.rows or len(s.totals) < 9 or not s.state:
            continue
        state, _ = state_of(s.state)
        count[state] += 1
        name = decode(s.name)
        numbers = [int(x) for x in s.totals]
        voters = numbers[0] if year == 2015 else None
        invalid, valid = numbers[-4], numbers[-1]
        cast = next((numbers[i] for i in (-8, -7) if numbers[i] == valid + invalid), -1)
        if cast < 0:
            left.append(f"{year} {chamber} {name}: the printed ballots, rejected ballots, and valid votes disagree")
            continue
        rows = [Candidate(None, r.party, r.ordinary, r.advance, r.total) for r in s.rows]
        if sum(r.total for r in s.rows) != valid or any(r.ordinary + r.advance != r.total for r in s.rows):
            left.append(f"{year} {chamber} {name}: the candidates' votes do not add up to the printed valid votes")
            continue
        try:
            for r, c in zip(s.rows, rows, strict=True):
                party_code(r.party)
                c.name = decode(r.name)
                if "�" in c.name:
                    c.name = None
        except ValueError as e:
            left.append(f"{year} {chamber} {name}: {e}")
            continue
        seats.append(Seat(f"{chamber}-{state}-{count[state]}", name, state, voters, cast, invalid, valid, rows))
    return seats, left


# Packages --------------------------------------------------------------------------------------------------------


def fetch() -> None:
    """Fetch the tables that a plain request reaches. A table kept under a `browser://` key was saved by hand from
    the commission's archived site and is added with `raw.store`."""
    for url, _ in RAW.values():
        if url.startswith("https://"):
            raw.fetch(SOURCE, url, suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/general-registry", f"{SOURCE}/geo", *(f"{SOURCE}/{DAYS[y]}" for y in sorted(DAYS))]


def datasets() -> dict[tuple[int, str], list[Seat]]:
    return {k: read(*k)[0] for k in RAW}


def left_out() -> list[str]:
    return [line for k in RAW for line in read(*k)[1]]


def body_key(state: str) -> str:
    return key("body", "state", state)


def party_key(code: str) -> str:
    return key("party", code)


def area_key(year: int, seat: Seat) -> str:
    return key("area", year, seat.code)


def scheme(year: int) -> str:
    return f"mm.constituency_{year}"


def cached(year: int, chamber: str) -> raw.Artifact:
    return raw.cached(SOURCE, RAW[(year, chamber)][0])


def registry() -> Package:
    p = Package(
        f"{SOURCE}/general-registry",
        SOURCE,
        "The Union Election Commission as a source, and the parties and state and region legislatures of Myanmar's "
        "2010 and 2015 general elections",
    )
    p.external.add(MYANMAR)
    p.add(
        "source",
        id=UEC,
        kind="official_body",
        publisher="Union Election Commission of Myanmar",
        title="Myanmar general elections of 2010 and 2015: results by constituency",
        url="http://uecmyanmar.org/",
        license="Official publication; reuse terms are not stated",
        attribution="Union Election Commission of Myanmar",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=MYANMAR, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each constituency elects one member, and the candidate with the most votes is elected.",
        lang="en",
    )
    data = datasets()
    parties: dict[str, tuple[str | None, str]] = {}
    states: set[str] = set()
    for (_, chamber), seats in data.items():
        for seat in seats:
            if chamber == "state":
                states.add(seat.state)
            for c in seat.candidates:
                if found := party_code(c.party):
                    parties.setdefault(found[0], (found[1], found[2]))
    for state in sorted(states):
        english = next(e for _, (s, e) in STATES.items() if s == state)
        id = body_key(state)
        if state in {"kayah", "shan"}:
            p.external.add(id)
            continue
        p.add("body", id=id, polity_id=MYANMAR, kind="unicameral")
        p.name(id, f"{english} Hluttaw", "en", "official")
    for code, (english, burmese) in sorted(parties.items()):
        if code in EXISTING_PARTIES:
            p.external.add(party_key(code))
            continue
        id = party_key(code)
        p.add("party", id=id, kind="party", polity_id=MYANMAR)
        p.name(id, burmese, "my", "official")
        if english:
            p.name(id, english, "en", "official")
    for key_ in RAW:
        p.artifact(cached(*key_), UEC)
    return p


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The constituencies of Myanmar's 2010 and 2015 general elections, as the Union Election Commission lists them",
        depends=[f"{SOURCE}/general-registry"],
    )
    p.external.add(MYANMAR)
    for year in sorted(DAYS):
        p.add("scheme", code=scheme(year), polity_id=MYANMAR, kind="electoral_district", rank=1, noun="constituency")
    p.add("scheme", code=COUNT_SCHEME, polity_id=MYANMAR, kind="synthetic", rank=2, noun="constituency count")
    for (year, _), seats in datasets().items():
        for seat in seats:
            id = area_key(year, seat)
            p.add("area", id=id, scheme_code=scheme(year))
            p.add("area_code", area_id=id, code=seat.code)
            p.name(id, seat.name, "my", "official")
    return p


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Myanmar's general election of {day} by constituency for the Pyithu Hluttaw and the state and region "
        "Hluttaws, with every candidate's votes, from the Union Election Commission",
        depends=[f"{SOURCE}/general-registry", f"{SOURCE}/geo"],
    )
    p.external.update({MYANMAR, key("body", "pyithu")})
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=MYANMAR,
        kind="general",
        date_from=date.fromisoformat(day),
        date_to=date.fromisoformat(day),
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} Myanmar general election", "en", "official")
    p.add("slug", path=f"mm/{day}-general-election", entity_id=election_id, is_current=True)
    for (y, chamber), seats in datasets().items():
        if y != year:
            continue
        p.uses(cached(y, chamber))
        for seat in seats:
            body = key("body", "pyithu") if chamber == "pyithu" else body_key(seat.state)
            if chamber == "state" and seat.state in {"kayah", "shan"}:
                p.external.add(body)
            code = seat.code
            contest = key("contest", day, code)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="body_seats",
                body_id=body,
                district_area_id=area_key(year, seat),
                magnitude=1,
                round_number=1,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            p.name(contest, seat.name, "my", "official")
            p.add("slug", path=f"mm/{day}-general-election/{code}", entity_id=contest, is_current=True)
            rs = key("result_set", day, code)
            p.add(
                "result_set",
                id=rs,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=UEC,
                status="active",
                is_default=True,
            )
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
            unit = key("unit", day, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MYANMAR,
                scheme_code=COUNT_SCHEME,
                code=f"{day}:{code}",
                area_relation="equals",
                area_id=area_key(year, seat),
                kind="standard",
            )
            p.name(unit, seat.name, "my", "official")
            for measure, value in (
                (REGISTERED, seat.voters),
                (BALLOTS, seat.cast),
                (VALID, seat.valid),
                (INVALID, seat.invalid),
            ):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=rs,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            top = max(c.total for c in seat.candidates)
            for position, c in enumerate(seat.candidates):
                found = party_code(c.party)
                option = key("option", day, code, position)
                person = key("person", day, code, position)
                party = party_key(found[0]) if found else None
                if found and found[0] in EXISTING_PARTIES:
                    p.external.add(party_key(found[0]))
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=c.name,
                )
                p.add("person", id=person)
                if c.name:
                    p.name(person, c.name, "my", "official")
                candidacy = key("candidacy", day, code, position)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=c.name,
                    member_of_party_id=party,
                    is_independent=found is None,
                )
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=c.total,
                )
                won = c.total == top
                p.add(
                    "outcome",
                    result_set_id=rs,
                    option_id=option,
                    kind="elected" if won else "lost",
                    seats=1 if won else None,
                )
    return p


def parse() -> None:
    registry().write()
    geo().write()
    for year in sorted(DAYS):
        election(year).write()
