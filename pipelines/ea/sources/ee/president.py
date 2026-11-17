"""Estonian presidential elections of 1992 to 2026, every ballot, from the State Electoral Office's archive pages.

The Riigikogu elects the President by repeated ballots, and when it fails, an electoral body of the Riigikogu and
municipal delegates votes. In 1992 the first round was a popular vote, counted by county and city. The page for each
election prints the accounting of every ballot (members on the roll, ballots issued, participants, invalid and unmarked
ballots) and each candidate's votes. Those are the finest figures the office publishes.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.ee import referendums, valimised

SOURCE = "ee.president"
ESTONIA = str(uuid5(package.NAMESPACE, "polity\x1fEE"))
PAGE = "https://www.valimised.ee/et/valimiste-arhiiv/vabariigi-presidendi-valimised/vabariigi-presidendi-valimine-{}-aastal"
YEARS = (1992, 1996, 2001, 2006, 2011, 2016, 2021, 2026)
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK, VOTERS = 1, 2, 3, 4, 5, 6, 14
DERIVED = 1
MONTHS = {
    "jaanuaril": 1,
    "veebruaril": 2,
    "märtsil": 3,
    "aprillil": 4,
    "mail": 5,
    "juunil": 6,
    "juulil": 7,
    "augustil": 8,
    "septembril": 9,
    "oktoobril": 10,
    "novembril": 11,
    "detsembril": 12,
}
STAGES = {"riigikogu": "Riigikogu", "college": "electoral body", "popular": "popular vote"}
# The same person under the names the pages print.
ALIASES = {"Lennart-Georg Meri": "Lennart Meri"}
LICENSE = (
    "Published by the State Electoral Office on valimised.ee; no license is stated for these pages, reuse with the "
    "source cited"
)
METHOD = (
    "Counted and published by the National Electoral Committee (Vabariigi Valimiskomisjon) and shown on the State "
    "Electoral Office's archive page for the election. Each ballot is its own contest. The page prints, for every "
    "ballot, the members on the roll, the members who received a ballot, the members who took part, the invalid "
    "ballots, the unmarked ballots (a member's vote against every candidate) and each candidate's votes. Valid votes "
    "are the votes for candidates: participants less invalid and unmarked ballots."
)
POPULAR = (
    "Under the 1992 President of the Republic Election Act the first round was a general election. The county and city "
    "rows are what the page prints for each county, each of the cities Kohtla-Järve, Narva, Pärnu, Sillamäe, Tallinn "
    "and Tartu, and for the polling stations abroad. No candidate received more than half of the votes of the voters "
    "who took part, so the Riigikogu chose between the two candidates with the most votes."
)
SYSTEMS = {
    "popular_1992": (
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "In 1992 the first round was a general election, and a candidate with more than half of the votes of the "
            "voters who took part was elected. Failing that, the Riigikogu chose between the two candidates with the most "
            "votes. The archive records the declared outcome and does not recompute it."
        ),
    ),
    "riigikogu": (
        {"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 2 / 3}]},
        (
            "The Riigikogu votes by secret ballot, and a candidate who receives the votes of two-thirds of its members is "
            "elected. The archive records the declared outcome and does not recompute it."
        ),
    ),
    "college": (
        {"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
        (
            "When the Riigikogu does not elect a President, the electoral body of the Riigikogu and the delegates of the "
            "municipal councils votes, and a candidate with the votes of more than half of the members who take part is "
            "elected. The archive records the declared outcome and does not recompute it."
        ),
    ),
    "riigikogu_final": (
        {"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
        (
            "A Riigikogu vote between the leading candidates after a popular round (1992) or after the electoral body "
            "failed to elect (2016) is decided by a majority. The archive records the declared outcome and does not "
            "recompute it."
        ),
    ),
}


@dataclass
class Ballot:
    stage: str
    stage_no: int
    day: date
    registered: int
    issued: int | None
    cast: int
    invalid: int
    blank: int
    votes: dict[str, int]
    valid_stated: int | None = None

    @property
    def valid(self) -> int:
        return self.cast - self.invalid - self.blank


@dataclass
class Place:
    label: str
    registered: int
    cast: int
    invalid: int
    valid: int
    votes: dict[str, int]


@dataclass
class Election:
    year: int
    ballots: list[Ballot] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)
    total: Place | None = None
    born: dict[str, date] = field(default_factory=dict)
    last: str = ""


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [
        f"{referendums.SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in elections()),
    ]


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, PAGE.format(year), suffix=".html")
    for file in (referendums.GB_COUNTIES, referendums.GB_CITIES):
        raw.fetch(referendums.SOURCE, file, suffix=".geojson")


def parse() -> None:
    referendums.geo().write()
    registry().write()
    for day in elections():
        election(day).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- reading


def clean(text: str) -> str:
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def number(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def tokens(year: int) -> list[tuple[bool, list[str]]]:
    """The article's lines as (is table row, cells or the one text)."""
    page = raw.cached(SOURCE, PAGE.format(year)).path.read_text()
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.DOTALL)
    page = re.sub(r"<(td|th)[^>]*>", " | ", page)
    page = re.sub(r"<(br|/p|/tr|/h\d|/li|/div)[^>]*>", "\n", page)
    page = clean_lines(re.sub(r"<[^>]+>", "", page))
    start = page.rfind(f"Vabariigi Presidendi valimine {year}. aastal")
    end = page.find("Telefon", start)
    if start < 0 or end < 0:
        raise LookupError(f"{year}: the page lacks its article")
    out: list[tuple[bool, list[str]]] = []
    for line in page[start:end].split("\n"):
        if line.strip().startswith("|"):
            cells = [clean(cell) for cell in line.split("|")]
            if cells := [cell for cell in cells if cell]:
                if out and out[-1][0]:
                    out[-1][1].extend(cells)
                else:
                    out.append((True, cells))
        elif clean(line):
            out.append((False, [clean(line)]))
    return out


def clean_lines(text: str) -> str:
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", html.unescape(line)) for line in text.split("\n"))


def day_of(line: str, year: int) -> date | None:
    found = re.search(r"(\d{1,2})\.? (" + "|".join(MONTHS) + r"),? ?(\d{4})?", line)
    if found is None:
        return None
    return date(int(found[3] or year), MONTHS[found[2]], int(found[1]))


def person(name: str) -> str:
    return ALIASES.get(name, name)


def read(year: int) -> Election:
    out = Election(year)
    stage, stage_no, day = "riigikogu", 0, None
    label = ""
    counts: dict[str, int] = {}
    patterns = {
        "registered": r"Valijate nimekirja kanti (\d+) Riigikogu liiget",
        "issued": r"Valimissedeli sai (\d+) Riigikogu liiget",
        "cast": r"Hääletamisest võttis osa (\d+) Riigikogu liiget",
        "invalid": r"Kehtetuks tunnistati (\d+) valimissedelit",
    }
    for is_row, cells in tokens(year):
        text = cells[0]
        if not is_row:
            label = text
            heading = re.match(r"^(?:I{1,2} )?[Vv]alimine ([Rr]iigikogus|[Vv]alimiskogus)", text)
            if heading:
                stage, stage_no = ("riigikogu" if heading[1].lower() == "riigikogus" else "college"), stage_no + 1
            elif re.match(r"^(Esimene|Teine|Kolmas) hääletusvoor", text) or "valimiskogus toimus" in text:
                day = day_of(text, year) or day
            elif text.startswith("Hääletamistulemused Vabariigi Presidendi valimisel Riigikogus"):
                day = day_of(text, year)
            for name, pattern in patterns.items():
                if found := re.search(pattern, text):
                    counts[name] = int(found[1])
            if re.search(r"valit(uks|i )|osutus", text) and not text.startswith("Kuna"):
                out.last = text
            if (born := re.search(r"^(.+?) on sündinud (\d{1,2})\. (\w+) (\d{4})", text)) and born[3] in MONTHS:
                out.born[born[1]] = date(int(born[4]), MONTHS[born[3]], int(born[2]))
        elif text == "Nimekirjadesse kantud valijaid":
            place = read_place(label, cells)
            if label.startswith("Eesti Vabariigi Presidendi"):
                out.total = place
            else:
                out.places.append(place)
        elif re.match(r"^\d\. ", text):
            if day is None:
                raise LookupError(f"{year}: a ballot has no date")
            out.ballots.append(read_ballot(cells, stage, stage_no, day))
        elif cells[:2] == ["Kandidaat", "Hääli"] and year == 1992:
            if day is None or set(counts) != set(patterns):
                raise LookupError("1992: the Riigikogu ballot lacks its date or counts")
            votes = {person(n): number(v) for n, v in zip(cells[2::2], cells[3::2], strict=True)}
            out.ballots.append(
                Ballot(
                    "riigikogu",
                    1,
                    day,
                    counts["registered"],
                    counts["issued"],
                    counts["cast"],
                    counts["invalid"],
                    0,
                    votes,
                )
            )
    return out


def read_place(label: str, cells: list[str]) -> Place:
    votes: dict[str, int] = {}
    named = iter(cells)
    fields: dict[str, int] = {}
    for cell in named:
        if cell == "Nimekirjadesse kantud valijaid":
            fields["registered"] = number(next(named))
        elif cell == "Hääletamisest osavõtnud valijaid":
            fields["cast"] = number(next(named))
        elif cell == "Hääli kokku":
            fields["valid"] = number(next(named))
        elif cell == "Kehtetuid sedeleid":
            fields["invalid"] = number(next(named))
        elif cell == "Valimistest osavõtt protsentides":
            next(named)
        else:
            value = next(named)
            if value.endswith(" häält"):
                votes[person(cell)] = number(value)
                next(named)
    return Place(label, fields["registered"], fields["cast"], fields["invalid"], fields["valid"], votes)


def read_ballot(cells: list[str], stage: str, stage_no: int, day: date) -> Ballot:
    fields: dict[int, int] = {}
    at = 0
    while at < len(cells) and (found := re.match(r"^(\d)\. ", cells[at])):
        fields[int(found[1])] = number(cells[at + 1])
        at += 2
    if cells[at : at + 2] != ["Kandidaat", "Hääli"]:
        raise LookupError(f"{day}: a ballot table lacks its candidates")
    rest = cells[at + 2 :]
    votes = {person(name): number(value) for name, value in zip(rest[0::2], rest[1::2], strict=True)}
    return Ballot(stage, stage_no, day, fields[1], fields[2], fields[3], fields[4], fields[5], votes, fields.get(6))


_CACHE: dict[int, Election] = {}


def election_of(year: int) -> Election:
    if year not in _CACHE:
        _CACHE[year] = read(year)
    return _CACHE[year]


# ---------------------------------------------------------------- elections


def elections() -> dict[date, int]:
    """First ballot's date to the year, with 1992 split into its two elections."""
    out: dict[date, int] = {}
    for year in YEARS:
        if year == 1992:
            out[date(1992, 9, 20)] = year
            out[date(1992, 10, 5)] = year
        else:
            out[min(b.day for b in election_of(year).ballots)] = year
    return out


def candidates() -> list[str]:
    names: set[str] = set()
    for year in YEARS:
        found = election_of(year)
        for ballot in found.ballots:
            names.update(ballot.votes)
        if found.total is not None:
            names.update(found.total.votes)
    return sorted(names)


def source_id(day: date) -> str:
    return key("source", day)


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The Estonian presidency, its electoral systems, and its candidates")
    p.external.add(ESTONIA)
    office = key("office", "president")
    p.add("office", id=office, polity_id=ESTONIA, body_id=None, kind="head_of_state")
    p.name(office, "Vabariigi President", "et", "official")
    p.name(office, "President of the Republic", "en", "official", False)
    body = key("body", "valimiskogu")
    p.add("body", id=body, polity_id=ESTONIA, kind="electoral_college")
    p.name(body, "Valimiskogu", "et", "official")
    p.name(body, "Electoral body of the President", "en", "official", False)
    p.add("scheme", code="ee.assembly", polity_id=ESTONIA, kind="electoral_district", rank=0, noun="assembly")
    p.add("scheme", code="ee.vp_unit", polity_id=ESTONIA, kind="precinct", rank=3, noun="reporting unit")
    for name, (rules, method) in SYSTEMS.items():
        id = key("electoral_system", name)
        p.add("electoral_system", id=id, polity_id=ESTONIA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for year in YEARS:
        p.uses(raw.cached(SOURCE, PAGE.format(year)))
    born = {name: day for year in YEARS for name, day in election_of(year).born.items()}
    for name in candidates():
        id = key("person", name)
        day = born.get(name)
        p.add("person", id=id, birth_date=day, birth_precision="day" if day else None)
        p.name(id, name, "et", "official")
        for alias, target in sorted(ALIASES.items()):
            if target == name:
                p.name(id, alias, "et", "official", False)
    return p


def stage_label(ballot: Ballot) -> str:
    if ballot.stage == "college":
        return "electoral body"
    return "Riigikogu"


def election(day: date) -> Package:
    year = elections()[day]
    found = election_of(year)
    popular = day == date(1992, 9, 20)
    ballots = [] if popular else [b for b in found.ballots if year != 1992 or b.day == day]
    first = day
    last = max(b.day for b in ballots) if ballots else day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Estonian presidential election"
        + (", popular first round, by county and city" if popular else ", every ballot"),
        depends=[
            f"{SOURCE}/registry",
            f"{valimised.SOURCE}/registry",
            *([f"{referendums.SOURCE}/geo"] if popular else []),
            *([f"{SOURCE}/{date(1992, 9, 20)}"] if day == date(1992, 10, 5) else []),
        ],
    )
    p.external.add(ESTONIA)
    source = source_id(day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Riigi Valimisteenistus",
        title=f"Vabariigi Presidendi valimine {year}. aastal",
        url=PAGE.format(year),
        license=LICENSE,
        attribution="Riigi Valimisteenistus, valimised.ee",
    )
    p.artifact(raw.cached(SOURCE, PAGE.format(year)), source)
    ident = key("election", day)
    kind = "general" if popular else "indirect"
    p.add("election", id=ident, polity_id=ESTONIA, kind=kind, date_from=first, date_to=last, status="held")
    suffix = {
        1992: {date(1992, 9, 20): ", popular first round", date(1992, 10, 5): ", Riigikogu round"},
    }.get(year, {}).get(day, "")
    et_suffix = {date(1992, 9, 20): ", otsevalimine", date(1992, 10, 5): ", valimine Riigikogus"}.get(day, "")
    p.name(ident, f"{year} Estonian presidential election{suffix}", "en", "official")
    p.name(ident, f"Vabariigi Presidendi valimine {year}{et_suffix}", "et", "native", False)
    path = f"ee/{day}-president"
    p.add("slug", path=path, entity_id=ident, is_current=True)
    p.add("note", id=key("note", ident), entity_id=ident, kind="methodology", body_md=METHOD, lang="en")
    office = key("office", "president")
    units: dict[str, str] = {}
    rows: list[tuple[str, str, str | None, int, int, int]] = []

    def unit(code: str, label: str, how: str, where: str | None, scheme: str, kind_: str) -> str:
        if code not in units:
            units[code] = key("unit", code)
            p.add(
                "reporting_unit",
                id=units[code],
                polity_id=ESTONIA,
                scheme_code=scheme,
                code=code,
                area_relation=how,
                area_id=where,
                kind=kind_,
            )
            p.name(units[code], label, "et", "official")
        return units[code]

    def result_set(contest: str, ident_: date, round_: int) -> str:
        rs = key("result_set", ident_, round_)
        p.add(
            "result_set",
            id=rs,
            election_id=ident,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        return rs

    if popular:
        contest = key("contest", day, 1)
        p.add(
            "contest",
            id=contest,
            election_id=ident,
            kind="office",
            office_id=office,
            magnitude=1,
            round_number=1,
            electoral_system_id=key("electoral_system", "popular_1992"),
            default_result_kind="counted",
        )
        p.name(contest, "1992 Estonian presidential election, popular first round", "en", "official")
        p.name(contest, "Vabariigi Presidendi otsevalimine 1992", "et", "native", False)
        p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
        p.add("note", id=key("note", contest), entity_id=contest, kind="methodology", body_md=POPULAR, lang="en")
        rs = result_set(contest, day, 1)
        if found.total is None:
            raise LookupError("1992: the page has no national total")
        options: dict[str, str] = {}
        for name in found.total.votes:
            options[name] = key("option", day, name)
            p.add("contest_option", id=options[name], contest_id=contest, kind="candidate", ballot_label=name)
            p.add(
                "candidacy",
                id=key("candidacy", day, name),
                person_id=key("person", name),
                option_id=options[name],
                role="head",
                ballot_name=name,
            )
        totals = [0, 0, 0, 0, 0]
        typo: dict[str, int] = {}
        sums = {name: 0 for name in options}
        for place in found.places:
            where, code, how, shown = place_of(place.label)
            u = unit(
                f"{day}:{code}", shown, how, where, "ee.vp_unit", "abroad_mission" if code == "abroad" else "standard"
            )
            votes = sum(place.votes.values())
            valid, flag = place.valid, 0
            if valid != votes:
                if place.cast != votes + place.invalid:
                    raise ValueError(f"1992 {place.label}: its rows do not add up")
                # The page's "Hääli kokku" disagrees with its own candidates and ballots; the candidates and the
                # participants less the invalid ballots agree, so the valid votes are derived from them.
                typo[place.label] = place.valid
                valid, flag = votes, DERIVED
            if place.cast != valid + place.invalid:
                raise ValueError(f"1992 {place.label}: its rows do not add up")
            for measure, value, flagged in (
                (REGISTERED, place.registered, 0),
                (CAST, place.cast, 0),
                (VALID, valid, flag),
                (INVALID, place.invalid, 0),
            ):
                rows.append((rs, u, None, measure, value, flagged))
            for name, value in place.votes.items():
                rows.append((rs, u, options[name], VOTES, value, 0))
                sums[name] += value
            for i, value in enumerate((place.registered, place.cast, valid, place.invalid)):
                totals[i] += value
        for label_, printed in typo.items():
            p.add(
                "note",
                id=key("note", contest, label_),
                entity_id=rs,
                kind="caveat",
                body_md=(
                    f"The page prints {printed} valid votes for {label_}, but its candidates' votes add up to "
                    f"{sum(found.places[[x.label for x in found.places].index(label_)].votes.values())}, which with "
                    "the invalid ballots makes the voters who took part. The archive stores the candidates' sum as the "
                    "county's valid votes and flags it as derived; the national total the page prints agrees with it."
                ),
                lang="en",
            )
        total = found.total
        stated = [total.registered, total.cast, total.valid, total.invalid]
        if totals[:4] != stated or sums != total.votes:
            raise ValueError(f"1992 units add up to {totals[:4]} {sums}, the page's total is {stated} {total.votes}")
        for measure, value in (
            (REGISTERED, total.registered),
            (CAST, total.cast),
            (VALID, total.valid),
            (INVALID, total.invalid),
        ):
            p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=value)
        for name, value in total.votes.items():
            p.add("declared", result_set_id=rs, option_id=options[name], measure=VOTES, value=value)
    else:
        previous: str | None = key("contest", date(1992, 9, 20), 1) if year == 1992 else None
        counts: dict[tuple[int, str], int] = {}
        for number_, ballot in enumerate(ballots, 1):
            stage_key = (ballot.stage_no, ballot.stage)
            counts[stage_key] = counts.get(stage_key, 0) + 1
            nth = counts[stage_key]
            final = ballot is ballots[-1]
            contest = key("contest", day, number_)
            college = ballot.stage == "college"
            if college:
                system, body, where = "college", key("body", "valimiskogu"), "valimiskogu"
            else:
                after = year == 1992 or (year == 2016 and ballot.stage_no == 3)
                system, where = "riigikogu_final" if after else "riigikogu", "riigikogu"
                body = valimised.key("body", valimised.CHAMBER_BODY)
            p.add(
                "contest",
                id=contest,
                election_id=ident,
                kind="office",
                office_id=office,
                body_id=body,
                magnitude=1,
                round_number=number_ + (1 if year == 1992 else 0),
                previous_round_id=previous,
                electoral_system_id=key("electoral_system", system),
                default_result_kind="counted",
            )
            label = f"{stage_label(ballot)}, ballot {nth}" if year != 1992 else "Riigikogu ballot"
            p.name(contest, f"{year} Estonian presidential election: {label}", "en", "official")
            slug = f"{'electoral-body' if college else 'riigikogu'}-{ballot.stage_no}-ballot-{nth}"
            p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
            p.add(
                "note",
                id=key("note", contest),
                entity_id=contest,
                kind="context",
                body_md=f"This ballot was held on {ballot.day.isoformat()}.",
                lang="en",
            )
            previous = contest
            rs = result_set(contest, day, number_)
            u = unit(
                f"{day}:{where}",
                "Valimiskogu" if college else "Riigikogu",
                "unlocated",
                None,
                "ee.assembly",
                "aggregate",
            )
            if (
                ballot.cast != ballot.valid + ballot.invalid + ballot.blank
                or sum(ballot.votes.values()) != ballot.valid
            ):
                raise ValueError(f"{day} ballot {number_}: its counts do not add up")
            if ballot.valid_stated is not None and ballot.valid_stated != ballot.valid:
                raise ValueError(f"{day} ballot {number_}: the page's votes for candidates differ")
            measures = [
                (REGISTERED, ballot.registered),
                (CAST, ballot.cast),
                (VALID, ballot.valid),
                (INVALID, ballot.invalid),
                (BLANK, ballot.blank),
            ]
            if ballot.issued is not None:
                measures.append((VOTERS, ballot.issued))
            for measure, value in measures:
                rows.append((rs, u, None, measure, value, 0))
                p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=value)
            leader = max(ballot.votes, key=lambda name: ballot.votes[name])
            for name, value in ballot.votes.items():
                option = key("option", day, number_, name)
                p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=name)
                p.add(
                    "candidacy",
                    id=key("candidacy", day, number_, name),
                    person_id=key("person", name),
                    option_id=option,
                    role="head",
                    ballot_name=name,
                )
                rows.append((rs, u, option, VOTES, value, 0))
                p.add("declared", result_set_id=rs, option_id=option, measure=VOTES, value=value)
                if final and name == leader:
                    surname = name.split()[-1]
                    if found.last and surname[:-1] not in found.last:
                        raise ValueError(f"{year}: the last ballot's leader is not the one the page names")
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
    p.frame(
        "result_value",
        pl.DataFrame(
            rows, schema=["result_set_id", "unit_id", "option_id", "measure", "value", "flags"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.col("flags").cast(pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p


def place_of(label: str) -> tuple[str | None, str, str, str]:
    """Area, unit code, relation and name of one row of the 1992 page."""
    if label.startswith("Valimistest osavõtt ja hääletamistulemused väljaspool"):
        return None, "abroad", "unlocated", "Väljaspool Eesti Vabariiki moodustatud jaoskonnad"
    city = {name: slug for slug, (name, _, _) in referendums.CITIES.items()}.get(label)
    if city is not None:
        return referendums.area("ee.linn", city), f"city:{city}", "equals", label
    county = referendums.county_of(label)
    if county is not None:
        return referendums.area("ee.maakond", county), f"county:{county}", "within", label
    raise LookupError(f"1992: unknown unit {label!r}")
