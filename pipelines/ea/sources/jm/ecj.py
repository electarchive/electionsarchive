import functools
import re
import subprocess
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.jm import pdf

SOURCE = "jm.ecj"
BASE = "https://www.ecj.com.jm/wp-content/uploads"
PAGE = "https://www.ecj.com.jm/elections/election-results/parliamentary-elections/"
JAMAICA = str(uuid5(package.NAMESPACE, "polity\x1fJM"))
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# Every election whose polling-station report carries a text layer, by its election day. Each lists the report of
# every polling station and the authority's own summary of the constituency totals.
FILES: dict[str, tuple[str, str | None]] = {
    "2001-03-08": (
        f"{BASE}/2017/12/2001By-ElectionStAnnNorthEasternBoxbyBox.pdf",
        f"{BASE}/2017/12/2001By-ElectionStAnnNorthEasternSummary.pdf",
    ),
    "2002-10-16": (f"{BASE}/2017/12/2002GeneralElectionBoxByBox.pdf", f"{BASE}/2017/12/2002GeneralElectionSummary.pdf"),
    "2007-09-03": (f"{BASE}/2017/12/2007GeneralElectionBoxbyBox.pdf", f"{BASE}/2017/12/2007GeneralElectionSummary.pdf"),
    "2005-04-13": (
        f"{BASE}/2017/12/2005By-ElectionKingstonWesternBoxbyBox.pdf",
        f"{BASE}/2017/12/2005By-ElectionKingstonWesternSummary.pdf",
    ),
    "2006-06-07": (
        f"{BASE}/2017/12/2006By-ElectionWestmorelandEasternBoxbyBox.pdf",
        f"{BASE}/2017/12/2006By-ElectionWestmorelandEasternSummary.pdf",
    ),
    "2009-06-16": (
        f"{BASE}/2017/12/2009By-ElectionStCatherineNorthEasternBoxbyBox.pdf",
        f"{BASE}/2017/12/2009By-ElectionStCatherineNorthEasternSummary.pdf",
    ),
    "2009-03-23": (
        f"{BASE}/2017/12/2009By-ElectionPortlandWesternBoxbyBox.pdf",
        f"{BASE}/2017/12/2009By-ElectionPortlandWesternSummary.pdf",
    ),
    "2009-09-04": (
        f"{BASE}/2017/12/2009By-ElectionClarendonNorthWesternBoxbyBox.pdf",
        f"{BASE}/2017/12/2009By-ElectionClarendonNorthWesternSummary.pdf",
    ),
    "2010-12-20": (
        f"{BASE}/2017/12/2010By-ElectionStAnnNorthEasternBoxbyBox.pdf",
        f"{BASE}/2017/12/2010By-ElectionStAnnNorthEasternSummary.pdf",
    ),
    "2011-04-04": (f"{BASE}/2017/12/2011By-ElectionStCatherineSouthWesternBoxbyBox.pdf", None),
    "2011-12-29": (f"{BASE}/2017/12/2011GeneralElectionBoxByBox.pdf", f"{BASE}/2017/12/2011GeneralElectionSummary.pdf"),
    "2016-02-25": (f"{BASE}/2017/12/2016GeneralElectionBoxbyBox.pdf", f"{BASE}/2017/12/2016GeneralElectionSummary.pdf"),
    "2017-10-30": (f"{BASE}/2017/12/2017By-ElectionsBoxbyBox.pdf", f"{BASE}/2017/12/2017By-ElectionSummary.pdf"),
    "2020-03-02": (
        f"{BASE}/2020/03/Clarendon-South-Eastern-By-Election-Results-Detail.pdf",
        f"{BASE}/2020/03/Clarendon-South-Eastern-By-Election-Results-Summary.pdf",
    ),
}
FILES = dict(sorted(FILES.items()))
PARTIES = {
    "JLP": "Jamaica Labour Party",
    "PNP": "People's National Party",
    "NDM": "National Democratic Movement",
    "MGPPP": "Marcus Garvey People's Political Party",
    "UPP": "United People's Party",
}
METHOD = (
    "Counted per polling station and published by the Electoral Commission of Jamaica in its box-by-box report. "
    "Registered voters are the electors on the official voters list of the station. Ballots cast are the total votes "
    "including rejected ballots, rejected ballots are those declared invalid, and valid votes are the total less the "
    "rejected ballots. Stations designated for election-day workers, police, and the military are marked in the "
    "report, and their turnout can exceed their list. The report prints the constituency totals; they are stored as "
    "declared. The winner of a constituency is the candidate with the most votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECJ_SOURCE = key("source", "ecj")
HOUSE = key("body", "house-of-representatives")
SYSTEM = key("electoral_system", "house", "fptp")


def packages() -> list[str]:
    return ["jm.ecj/registry", *(f"jm.ecj/{day}" for day in FILES)]


def fetch() -> None:
    for urls in FILES.values():
        for url in urls:
            if url:
                raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    for day in FILES:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def document(day: str) -> pdf.Document:
    return pdf.read(raw.cached(SOURCE, FILES[day][0]).path)


@functools.cache
def summary(day: str) -> dict[int, tuple[int, int, int]]:
    """The authority's own constituency lines in its summary: rejected ballots, total votes, and electors."""
    url = FILES[day][1]
    if url is None:
        return {}
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: dict[int, tuple[int, int, int]] = {}
    for line in text.splitlines():
        found = re.search(r"\((\d+)\)", line)
        if not found or "TOTALS" in line:
            continue
        cells = re.findall(r"[\d,]+(?:\.\d+)?%?", line[found.end() :])
        if len(cells) >= 4 and all(re.fullmatch(r"[\d,]+", cells[i]) for i in (-4, -3, -1)):
            rows[int(found.group(1))] = (
                int(cells[-4].replace(",", "")),
                int(cells[-3].replace(",", "")),
                int(cells[-1].replace(",", "")),
            )
    return rows


def independent(tag: str) -> bool:
    return tag in ("", "INA") or tag.startswith("IND")


def party_id(tag: str) -> str:
    return key("party", tag)


def registry() -> Package:
    p = Package(
        "jm.ecj/registry",
        SOURCE,
        "Jamaican parties, the House of Representatives, and its electoral system, from the Electoral Commission of "
        "Jamaica's polling-station reports",
    )
    p.external.add(JAMAICA)
    p.add(
        "source",
        id=ECJ_SOURCE,
        kind="official_body",
        publisher="Electoral Commission of Jamaica",
        title="Election results published by the Electoral Commission of Jamaica",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Commission of Jamaica",
    )
    for urls in FILES.values():
        for url in urls:
            if url:
                p.artifact(raw.cached(SOURCE, url), ECJ_SOURCE)
    p.add("scheme", code="jm.ps", polity_id=JAMAICA, kind="precinct", rank=1, noun="polling station")
    p.add("body", id=HOUSE, polity_id=JAMAICA, kind="lower_house")
    p.name(HOUSE, "House of Representatives of Jamaica", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=JAMAICA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each of the constituencies elects one member of the House of Representatives. The candidate with the "
        "most votes is elected.",
        lang="en",
    )
    tags = {
        party
        for day in FILES
        for constituency in document(day).constituencies
        for party, _ in constituency.candidates
        if not independent(party)
    }
    for tag in sorted(tags):
        id = party_id(tag)
        p.add("party", id=id, kind="party", polity_id=JAMAICA)
        if tag in PARTIES:
            p.name(id, PARTIES[tag], "en", "official")
            p.name(id, tag, "en", "abbreviation", False)
        else:
            p.name(id, tag, "en", "abbreviation")
    return p


def title(name: str) -> str:
    return name.title().replace("&", "and")


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
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


def build(day: str) -> Package:
    doc = document(day)
    first = date.fromisoformat(day)
    if doc.day != first:
        raise ValueError(f"{day}: report says {doc.day}")
    general = len(doc.constituencies) > 3
    p = Package(
        f"jm.ecj/{day}",
        SOURCE,
        f"The Jamaican {'general election' if general else 'by-election'} of {first} by polling station, from the "
        "Electoral Commission of Jamaica's reports",
        depends=["jm.ecj/registry"],
    )
    p.external.add(JAMAICA)
    for url in FILES[day]:
        if url:
            p.uses(raw.cached(SOURCE, url))
    election = key("election", day, "general")
    path = f"jm/{day}-general"
    if general:
        p.add("election", id=election, polity_id=JAMAICA, kind="general", date_from=first, date_to=first, status="held")
        p.add("slug", path=path, entity_id=election, is_current=True)
        p.name(election, f"{first.year} Jamaican general election", "en", "official")
    for constituency in doc.constituencies:
        name = title(constituency.name)
        if not general:
            election = key("election", day, constituency.number)
            path = f"jm/{day}-by-election-{changeset.slug(name)}"
            p.add(
                "election",
                id=election,
                polity_id=JAMAICA,
                kind="by_election",
                date_from=first,
                date_to=first,
                status="held",
            )
            p.add("slug", path=path, entity_id=election, is_current=True)
            p.name(election, f"{first.year} {name} by-election", "en", "official")
        contest = key("contest", day, constituency.number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=HOUSE,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{name}, House of Representatives", "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, constituency.number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ECJ_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options: list[str] = []
        for position, (party, label) in enumerate(constituency.candidates, 1):
            person = key("person", day, constituency.number, position)
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            option = key("option", day, constituency.number, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=key("candidacy", day, constituency.number, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=None if independent(party) else party_id(party),
                is_independent=independent(party),
            )
        for station in constituency.stations:
            unit = key("unit", day, constituency.number, station.pd, station.ps)
            code = f"{day}:{constituency.number}:{station.pd}:{station.ps}"
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=JAMAICA,
                scheme_code="jm.ps",
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            text = station.location or "Polling station"
            p.name(
                unit,
                f"{text} ({name}, division {station.division.split('(')[0].strip().title()}, "
                f"polling division {station.pd}, station {station.ps})",
                "en",
                "official",
            )
            _put(p, result_set, unit, None, REGISTERED, station.electors)
            if station.total is None or station.rejected is None:
                continue
            _put(p, result_set, unit, None, CAST, station.total)
            _put(p, result_set, unit, None, INVALID, station.rejected)
            _put(p, result_set, unit, None, VALID, station.total - station.rejected)
            for option, value in zip(options, station.votes, strict=True):
                _put(p, result_set, unit, option, VOTES, value)
        declared = constituency.declared
        if declared is None:
            if not constituency.divisions:
                raise ValueError(f"{day} {name}: no totals")
            # The report prints a total for every electoral division but none for the constituency.
            width = max(len(d.votes) for d in constituency.divisions)
            declared = pdf.Totals(
                sum(d.stations for d in constituency.divisions),
                [sum(d.votes[i] for d in constituency.divisions if i < len(d.votes)) for i in range(width)],
                sum(d.rejected for d in constituency.divisions),
                sum(d.total for d in constituency.divisions),
                sum(d.electors for d in constituency.divisions),
            )
            p.add(
                "note",
                id=key("note", result_set, "divisions"),
                entity_id=result_set,
                kind="caveat",
                body_md="The report prints no total for the constituency. The declared figures are the sum of the "
                "totals it prints for each electoral division.",
                lang="en",
            )
        count = len(options)
        if any(declared.votes[count:]):
            raise ValueError(f"{day} {name}: totals for unnamed candidates")
        for measure, value in (
            (REGISTERED, declared.electors),
            (CAST, declared.total),
            (INVALID, declared.rejected),
            (VALID, declared.total - declared.rejected),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for index, (option, value) in enumerate(zip(options, declared.votes[:count], strict=True)):
            if value == 0 and all(s.votes[index] is None for s in constituency.stations if s.votes):
                continue  # the report prints no cell for this candidate at any station, only a zero in the totals
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        top = max(declared.votes[:count])
        winners = [o for o, v in zip(options, declared.votes[:count], strict=True) if v == top]
        if len(winners) == 1:
            p.add("outcome", result_set_id=result_set, option_id=winners[0], kind="won")
    _caveats(p, doc)
    if general:
        _summary(p, day, doc)
    return p


def _summary(p: Package, day: str, doc: pdf.Document) -> None:
    """Notes the constituencies whose totals the authority's summary prints differently from its polling-station report."""
    rows = summary(day)
    for c in doc.constituencies:
        declared = c.declared
        if declared is None or c.number not in rows:
            continue
        if rows[c.number] != (declared.rejected, declared.total, declared.electors):
            rejected, total, electors = rows[c.number]
            result_set = key("result_set", day, c.number)
            p.add(
                "note",
                id=key("note", result_set, "summary"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"The authority's summary of the election prints {total:,} total votes including {rejected:,} "
                f"rejected ballots and {electors:,} electors for this constituency. Its polling-station report, "
                f"which these figures follow, prints {declared.total:,}, {declared.rejected:,}, and "
                f"{declared.electors:,}, and its stations add up to those.",
                lang="en",
            )


def _caveats(p: Package, doc: pdf.Document) -> None:
    over = sum(1 for c in doc.constituencies for s in c.stations if s.total is not None and s.total > s.electors)
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} polling stations print more ballots cast than electors on the official voters list, mostly "
            "stations designated for election-day workers, police, and the military; stored as printed",
        )
