"""Jamaican local government elections: the council divisions, the Portmore mayor, and the division by-elections.

The Electoral Commission of Jamaica publishes a polling-station report for each election, with the candidates of each
electoral division and the totals it prints for the division, and a summary of the division totals. A general
election of the parish councils and municipal corporations holds one contest for every electoral division, and the
municipality of Portmore also elects a mayor by the votes of all its divisions. A by-election fills one division.
The reports from before 1998 are scans without a text layer.
"""

import functools
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date

from ea import changeset, raw
from ea.package import Package
from ea.sources.jm import ecj, pdf

SOURCE = ecj.SOURCE
BASE = ecj.BASE
JAMAICA = ecj.JAMAICA
VOTES, REGISTERED, CAST, VALID, INVALID = ecj.VOTES, ecj.REGISTERED, ecj.CAST, ecj.VALID, ecj.INVALID
KEY = "jm.ecj/local-registry"
COUNCILS = ecj.key("body", "local-councils")
MAYOR = ecj.key("office", "portmore-mayor")
SYSTEM = ecj.SYSTEM
METHOD = (
    "Counted per polling station and published by the Electoral Commission of Jamaica in its box-by-box report. "
    "Registered voters are the electors on the official voters list of the station. Ballots cast are the total votes "
    "including rejected ballots, rejected ballots are those declared invalid, and valid votes are the total less the "
    "rejected ballots. Stations designated for election-day workers, police, and the military are marked in the "
    "report, and their turnout can exceed their list. A blank cell for a candidate is not stored, and a blank cell "
    "for rejected ballots at a station that prints its total is stored as zero. The report "
    "prints the totals of each electoral division; they are stored as declared. The winner of a division is the "
    "candidate with the most votes."
)


@dataclass(frozen=True)
class Report:
    """The polling-station report of an election and the authority's summary of it, when the summary has a text layer."""

    box: str
    summary: str | None = None


def independent(tag: str) -> bool:
    """Whether a printed tag is an independent candidate: the reports also label them INA, INB, and so on."""
    return ecj.independent(tag) or re.fullmatch(r"IN[A-Z]", tag) is not None


def link(path: str) -> str:
    return f"{BASE}/{path}"


# The general elections, by election day: the councils of every division, and the mayor of Portmore where one was elected.
# The 1998 summary and the summary of the 2003 mayoral election are scans, or another election's summary.
GENERAL: dict[str, tuple[Report, Report | None]] = {
    "1998-09-10": (Report(link("2017/12/1998KSACParishCouncilElectionBoxbyBox.pdf")), None),
    "2003-06-19": (
        Report(
            link("2017/12/2003ParishCouncilElectionBoxbyBox.pdf"), link("2017/12/2003ParishCouncilElectionSummary.pdf")
        ),
        Report(link("2017/12/2003MayoralElection-MunicipalityofPortmoreBoxbyBox.pdf")),
    ),
    "2007-12-05": (
        Report(
            link("2017/12/2007ParishCouncilElectionBoxbyBox.pdf"), link("2017/12/2007ParishCouncilElectionSummary.pdf")
        ),
        Report(link("2017/12/2007MayoralElection-MunicipalityofPortmoreBoxbyBox.pdf")),
    ),
    "2012-03-26": (
        Report(link("2017/12/2012ParishCouncilBoxByBox.pdf"), link("2017/12/2012ParishCouncilSummary.pdf")),
        Report(
            link("2017/12/2012MayoralElection-MunicipalityofPortmoreBoxByBox.pdf"),
            link("2017/12/2012MayoralElection-MunicipalityofPortmoreSummary.pdf"),
        ),
    ),
    "2016-11-28": (
        Report(
            link("2017/12/2016LocalGovernmentElectionBoxbyBox.pdf"),
            link("2017/12/2016LocalGovernmentElectionSummary.pdf"),
        ),
        Report(
            link("2017/12/2016MayoralElection-MunicipalityofPortmore-BoxbyBox.pdf"),
            link("2017/12/2016MayoralElection-MunicipalityofPortmore-Summary.pdf"),
        ),
    ),
    "2024-02-26": (
        Report(
            link("2024/05/2024LocalGovernmentDetailResults.pdf"), link("2024/05/2024LocalGovernmentSummaryResults.pdf")
        ),
        Report(
            link("2024/03/PortmoreCityMunicipalityElection2024-BoxByBoxResult.pdf"),
            link("2024/03/PortmoreCityMunicipalityElection2024-Summary.pdf"),
        ),
    ),
}
# The by-elections of one or more divisions, by election day.
BY: dict[str, Report] = {
    "2004-11-25": Report(link("2017/12/2004By-ElectionParishCouncilMountIndustryBoxbyBox.pdf")),
    "2005-11-24": Report(
        link("2017/12/2005By-ElectionParishCouncilAlexandriaBoxbyBox.pdf"),
        link("2017/12/2005By-ElectionParishCouncilAlexandriaSummary.pdf"),
    ),
    "2009-07-14": Report(
        link("2017/12/2009By-ElectionParishCouncilCornwallMountainBoxbyBox.pdf"),
        link("2017/12/2009By-ElectionParishCouncilCornwallMountainSummary.pdf"),
    ),
    "2011-04-04": Report(
        link("2017/12/2011By-ElectionParishCouncilClarendonNorthWesternBoxbyBox.pdf"),
        link("2017/12/2011By-ElectionParishCouncilClarendonNorthWesternSummary.pdf"),
    ),
    "2013-08-08": Report(
        link("2017/12/2013By-ElectionParishCouncilCassiaParkDivisionBoxbyBox.pdf"),
        link("2017/12/2013By-ElectionParishCouncilCassiaParkDivisionSummary.pdf"),
    ),
    "2017-03-27": Report(
        link("2017/12/2017By-ElectionParishCouncilGreaterPortmoreNorthBoxbyBox.pdf"),
        link("2017/12/2017By-ElectionParishCouncilGreaterPortmoreNorthSummary.pdf"),
    ),
    "2018-03-05": Report(link("2018/04/Norman-Gardens-By-Election-2018-Box-By-Box-Results.pdf")),
    "2018-04-27": Report(link("2018/05/ParishCouncilBy-Election2018BoxbyBox.pdf")),
    "2024-11-22": Report(link("2024/11/November2024ByElectionBoxByBoxResultParishCouncil.pdf")),
    "2025-09-03": Report(
        link("2025/09/2025-Local-Goverment-By-Election-Box-By-Box-Results.pdf"),
        link("2025/09/2025-Local-Goverment-By-Election-Summary-Results.pdf"),
    ),
}
DAYS = sorted({*GENERAL, *BY})


def packages() -> list[str]:
    return [KEY, *(f"jm.ecj/local-{day}" for day in DAYS)]


def reports(day: str) -> list[Report]:
    if day in BY:
        return [BY[day]]
    council, mayor = GENERAL[day]
    return [council, *([mayor] if mayor else [])]


def fetch() -> None:
    for day in DAYS:
        for report in reports(day):
            for url in (report.box, report.summary):
                if url:
                    raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


@functools.cache
def document(url: str, by: str) -> pdf.Document:
    return pdf.read(raw.cached(SOURCE, url).path, by, "Portmore")


def councils(day: str) -> pdf.Document:
    doc = document((BY[day] if day in BY else GENERAL[day][0]).box, "division")
    if doc.day != date.fromisoformat(day):
        raise ValueError(f"{day}: report says {doc.day}")
    return doc


def mayoral(day: str) -> pdf.Document | None:
    mayor = None if day in BY else GENERAL[day][1]
    if mayor is None:
        return None
    doc = document(mayor.box, "municipality")
    if doc.day != date.fromisoformat(day):
        raise ValueError(f"{day}: mayoral report says {doc.day}")
    return doc


def documents() -> list[pdf.Document]:
    out = []
    for day in DAYS:
        out.append(councils(day))
        if (mayor := mayoral(day)) is not None:
            out.append(mayor)
    return out


@functools.cache
def parties() -> dict[str, str]:
    """The party tags of every report that no parliamentary report prints, with their first appearance."""
    known = ecj.registered_parties()
    found: dict[str, str] = {}
    for doc in documents():
        for c in doc.constituencies:
            for tag, _ in c.candidates:
                if tag and not independent(tag) and tag not in known:
                    found.setdefault(tag, str(doc.day))
    return found


def registry() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The councils and the mayor of Portmore that Jamaica's local government elections fill, and the parties "
        "they add, from the Electoral Commission of Jamaica's polling-station reports",
        depends=["jm.ecj/registry"],
    )
    p.external.add(JAMAICA)
    p.add("body", id=COUNCILS, polity_id=JAMAICA, kind="council")
    p.name(COUNCILS, "Parish councils and municipal corporations of Jamaica", "en", "official")
    p.add("office", id=MAYOR, polity_id=JAMAICA, kind="mayor")
    p.name(MAYOR, "Mayor of Portmore", "en", "official")
    for tag in sorted(parties()):
        id = ecj.party_id(tag)
        p.add("party", id=id, kind="party", polity_id=JAMAICA)
        p.name(id, tag, "en", "abbreviation")
    return p


def division_number(station: pdf.Station) -> int:
    found = re.search(r"\((\d+)\)\s*$", station.division)
    if found is None:
        raise ValueError(f"no division number in {station.division!r}")
    return int(found.group(1))


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    ecj._put(p, result_set, unit, option, measure, value)


def _station(p: Package, result_set: str, options: list[str], unit: str, station: pdf.Station) -> None:
    _put(p, result_set, unit, None, REGISTERED, station.electors)
    if station.total is not None:
        _put(p, result_set, unit, None, CAST, station.total)
    if station.total is not None:
        rejected = station.rejected or 0
        _put(p, result_set, unit, None, INVALID, rejected)
        _put(p, result_set, unit, None, VALID, station.total - rejected)
    for option, value in zip(options, station.votes, strict=False):
        _put(p, result_set, unit, option, VOTES, value)


def _declared(
    p: Package, result_set: str, options: list[str], c: pdf.Constituency, declared: pdf.Totals, label: str
) -> None:
    count = len(options)
    if any(declared.votes[count:]):
        raise ValueError(f"{label}: totals for unnamed candidates")
    for measure, value in (
        (REGISTERED, declared.electors),
        (CAST, declared.total),
        (INVALID, declared.rejected),
        (VALID, declared.total - declared.rejected),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for index, (option, value) in enumerate(zip(options, declared.votes[:count], strict=True)):
        if value == 0 and all(s.votes[index] is None for s in c.stations if s.votes):
            continue  # the report prints no cell for this candidate at any station, only a zero in the totals
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
    top = max(declared.votes[:count])
    winners = [o for o, v in zip(options, declared.votes[:count], strict=True) if v == top]
    if len(winners) == 1:
        p.add("outcome", result_set_id=result_set, option_id=winners[0], kind="won")


def _contest(
    p: Package,
    *,
    election: str,
    contest: str,
    day: str,
    path: str,
    label: str,
    c: pdf.Constituency,
    mayor: bool,
    declared: pdf.Totals,
    note: str | None,
    parent: str,
) -> None:
    fields: dict[str, object] = (
        {"kind": "office", "office_id": MAYOR} if mayor else {"kind": "body_seats", "body_id": COUNCILS}
    )
    p.add(
        "contest",
        id=contest,
        election_id=election,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
        **fields,
    )
    p.name(contest, label, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = ecj.key("local-result-set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ecj.ECJ_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=ecj.key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    if note:
        p.add(
            "note",
            id=ecj.key("note", result_set, "caveat"),
            entity_id=result_set,
            kind="caveat",
            body_md=note,
            lang="en",
        )
    options: list[str] = []
    for position, (tag, name) in enumerate(c.candidates, 1):
        person = ecj.key("local-person", contest, position)
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        option = ecj.key("local-option", contest, position)
        options.append(option)
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
            id=ecj.key("local-candidacy", contest, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=None if independent(tag) else ecj.party_id(tag),
            is_independent=independent(tag),
        )
    seen: Counter[tuple[int, str, str]] = Counter()
    for station in c.stations:
        place = (division_number(station), station.pd, station.ps)
        seen[place] += 1  # a report can print one polling division and station number twice
        again = f"#{seen[place]}" if seen[place] > 1 else ""
        unit = ecj.key("local-unit", day, contest, parent, *place, seen[place])
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=JAMAICA,
            scheme_code="jm.ps",
            code=f"local:{day}:{parent}:{place[0]}:{station.pd}:{station.ps}{again}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        text = station.location or "Polling station"
        division = station.division.split("(")[0].strip().title()
        p.name(unit, f"{text} ({division}, polling division {station.pd}, station {station.ps})", "en", "official")
        _station(p, result_set, options, unit, station)
    _declared(p, result_set, options, c, declared, label)


def _sums(c: pdf.Constituency, declared: pdf.Totals) -> tuple[str, int]:
    """How the stations' total votes differ from the declared total, and how many stations' votes miss their valid votes."""
    printed = [s for s in c.stations if s.total is not None]
    cast = sum(s.total or 0 for s in printed)
    rejected = sum(s.rejected or 0 for s in printed)
    off = ""
    if (cast, rejected) != (declared.total, declared.rejected):
        off = f"{cast:,} total votes and {rejected:,} rejected ballots at its stations against {declared.total:,} and {declared.rejected:,} declared"
    votes = sum(1 for s in printed if sum(v or 0 for v in s.votes) != (s.total or 0) - (s.rejected or 0))
    return off, votes


def _divisions_total(c: pdf.Constituency) -> pdf.Totals:
    width = max(len(d.votes) for d in c.divisions)
    return pdf.Totals(
        sum(d.stations for d in c.divisions),
        [sum(d.votes[i] for d in c.divisions if i < len(d.votes)) for i in range(width)],
        sum(d.rejected for d in c.divisions),
        sum(d.total for d in c.divisions),
        sum(d.electors for d in c.divisions),
    )


@dataclass(frozen=True)
class Line:
    """A line of the authority's summary: one electoral division."""

    name: str
    rejected: int
    total: int
    electors: int
    votes: dict[str, int] | None


@dataclass(frozen=True)
class Summary:
    tags: tuple[str, ...]
    lines: tuple[Line, ...]


@functools.cache
def summary(url: str) -> Summary:
    """The division lines of the authority's summary: rejected ballots, total votes, electors, and votes by column."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    tags: list[str] = []
    lines: list[Line] = []
    for line in text.splitlines():
        if re.search(r"\bJLP\b", line) and re.search(r"\bPNP\b", line) and re.search(r"\bTOTAL\b", line):
            tags = [t for t in re.findall(r"IND [A-Z]\b|[A-Z]{2,}", line) if t not in ("TOTAL", "VOTES")]
            continue
        found = re.match(r"^\s*\((\d+)\)\s+(.+?)\s{2,}(\d.*)$", line)
        if not found:
            continue
        cells = re.findall(r"[\d,]+(?:\.\d+)?", found.group(3))
        if len(cells) < 4 or "." in cells[-1] or "." not in cells[-2] or "." in cells[-3]:
            continue
        electors, total = (int(cells[i].replace(",", "")) for i in (-1, -3))
        rest = cells[:-3]
        if "." in rest[-1]:  # newer summaries print a share after the rejected ballots
            rest = rest[:-1]
        rejected = int(rest[-1].replace(",", ""))
        rest = rest[:-1]
        votes = None
        if len(rest) == 2 * len(tags):
            votes = dict(zip(tags, (int(rest[i].replace(",", "")) for i in range(0, len(rest), 2)), strict=True))
        lines.append(Line(found.group(2).strip().upper(), rejected, total, electors, votes))
    return Summary(tuple(tags), tuple(lines))


def _column(tag: str, tags: tuple[str, ...]) -> str | None:
    """The summary column that holds a candidate's votes, or None where the summary prints none for the candidate."""
    if tag in tags:
        return tag
    return "IND" if independent(tag) and "IND" in tags else None


def _differences(c: pdf.Constituency, d: pdf.Totals, found: Summary, line: Line) -> list[str]:
    """What a summary line prints that differs from the report's totals of the division."""
    out = []
    omitted = 0
    names = []
    if line.votes is not None:
        columns: dict[str, int] = {}
        for (tag, label), votes in zip(c.candidates, d.votes, strict=False):
            column = _column(tag, found.tags)
            if column is None:
                omitted += votes
                if votes:
                    names.append(f"{label.title()} ({votes:,})")
            else:
                columns[column] = columns.get(column, 0) + votes
        for column, votes in sorted(columns.items()):
            if line.votes.get(column, 0) != votes:
                out.append(f"{column} votes: {line.votes.get(column, 0):,} in the summary and {votes:,} in the report")
    if omitted:
        out.append(f"the summary has no column for the votes of {', '.join(names)}")
    for what, shown, printed in (
        ("rejected ballots", line.rejected, d.rejected),
        ("total votes including rejected ballots", line.total, d.total - omitted),
        ("electors", line.electors, d.electors),
    ):
        if shown != printed:
            out.append(f"{what}: {shown:,} in the summary and {printed:,} in the report")
    return out


def compare(label: str, doc: pdf.Document, url: str | None) -> tuple[dict[tuple[int, int], str], list[str]]:
    """The divisions whose summary line differs from the report, with a note for each, and the lines left unmatched."""
    if url is None:
        return {}, []
    found = summary(url)
    if not found.lines:
        return {}, [f"{label}: no division lines in the summary"]
    free = list(found.lines)
    notes: dict[tuple[int, int], str] = {}
    problems: list[str] = []
    for c in doc.constituencies:
        d = c.declared
        if d is None or c.blank:
            continue
        same = [x for x in free if x.name == c.name.upper()]
        line = min(same, key=lambda x: abs(x.electors - d.electors), default=None)
        if line is None:
            line = next(
                (x for x in free if (x.rejected, x.total, x.electors) == (d.rejected, d.total, d.electors)), None
            )
            if line is None:
                problems.append(f"{label}: no summary line for {c.name} {d}")
                continue
            note = f"The summary prints the name of this division as {line.name.title()}."
        else:
            note = ""
        free.remove(line)
        diffs = _differences(c, d, found, line)
        text = " ".join(filter(None, [note, "; ".join(diffs) + "." if diffs else ""]))
        if text:
            notes[(c.parent[0] if c.parent else 0, c.number)] = (
                "The authority's summary prints different figures for this division: " + text if diffs else text
            )
    problems += [f"{label}: the summary prints {x.name} that no report division matches" for x in free]
    return notes, problems


def build(day: str) -> Package:
    first = date.fromisoformat(day)
    by_election = day in BY
    p = Package(
        f"jm.ecj/local-{day}",
        SOURCE,
        f"The Jamaican local government {'by-election' if by_election else 'elections'} of {first} by polling "
        "station, from the Electoral Commission of Jamaica's reports",
        depends=["jm.ecj/registry", KEY],
    )
    p.external.add(JAMAICA)
    for report in reports(day):
        for url in (report.box, report.summary):
            if url:
                p.artifact(raw.cached(SOURCE, url), ecj.ECJ_SOURCE)
    known = ecj.registered_parties() | frozenset(parties())
    doc = councils(day)
    mayor = mayoral(day)
    for d in (doc, *([mayor] if mayor else [])):
        for c in d.constituencies:
            for tag, _ in c.candidates:
                if tag and not independent(tag) and tag not in known:
                    raise ValueError(f"{day}: party {tag} is not in a registry package")
    election = ecj.key("local-election", day)
    path = f"jm/{day}-local"
    if not by_election:
        p.add("election", id=election, polity_id=JAMAICA, kind="local", date_from=first, date_to=first, status="held")
        p.add("slug", path=path, entity_id=election, is_current=True)
        p.name(election, f"{first.year} Jamaican local government elections", "en", "official")
    names = Counter(c.name for c in doc.constituencies)
    council_report = BY[day] if by_election else GENERAL[day][0]
    notes, mismatches = compare(f"{day} councils", doc, council_report.summary)
    over = 0
    declared_off: list[str] = []
    votes_off: list[str] = []
    for c in doc.constituencies:
        if c.blank:
            continue
        if c.declared is None:
            raise ValueError(f"{day} {c.name}: no totals")
        parent = c.parent[0] if c.parent else 0
        division = ecj.title(c.name)
        constituency = ecj.title(c.parent[1]) if c.parent else ""
        slug = changeset.slug(division) + (f"-{changeset.slug(constituency)}" if names[c.name] > 1 else "")
        if by_election:
            election = ecj.key("local-election", day, parent, c.number)
            path = f"jm/{day}-local-by-election-{slug}"
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
            p.name(election, f"{first.year} {division} local government by-election", "en", "official")
            contest_path = f"{path}/{slug}"
        else:
            contest_path = f"{path}/{slug}"
        _contest(
            p,
            election=election,
            contest=ecj.key("local-contest", day, parent, c.number),
            day=day,
            path=contest_path,
            label=f"{division} division, {constituency}" if constituency else f"{division} division",
            c=c,
            mayor=False,
            declared=c.declared,
            note=notes.get((parent, c.number)),
            parent=str(parent),
        )
        over += sum(1 for s in c.stations if s.total is not None and s.total > s.electors)
        off, votes = _sums(c, c.declared)
        if off:
            declared_off.append(f"{division} ({c.number}) has {off}")
        if votes:
            votes_off.append(f"{division} ({c.number}, {votes} stations)")
    if mayor and mayor.constituencies:
        m = mayor.constituencies[0]
        declared = _divisions_total(m)
        note = (
            "The report prints a total for each electoral division of the municipality, and none for the "
            "municipality. The declared figures are the sum of the totals it prints for each division."
        )
        differs, problems = _compare_mayor(day, m, declared, GENERAL[day][1])
        mismatches += problems
        note += differs
        _contest(
            p,
            election=ecj.key("local-election", day),
            contest=ecj.key("local-contest", day, "mayor"),
            day=day,
            path=f"jm/{day}-local/mayor-of-portmore",
            label="Mayor of Portmore",
            c=m,
            mayor=True,
            declared=declared,
            note=note,
            parent="mayor",
        )
        over += sum(1 for s in m.stations if s.total is not None and s.total > s.electors)
        off, votes = _sums(m, declared)
        if off:
            declared_off.append(f"the mayor of Portmore has {off}")
        if votes:
            votes_off.append(f"the mayor of Portmore ({votes} stations)")
    if mismatches:
        raise ValueError("\n".join(mismatches))
    if declared_off:
        p.accept(
            "accounting.declared",
            f"{day}: {'; '.join(declared_off)}; the declared totals and the polling-station rows are stored as printed",
        )
    if votes_off:
        p.accept(
            "accounting.votes_valid",
            f"{day}: polling stations of {', '.join(votes_off)} print candidate votes that do not add up to their "
            "total votes less rejected ballots; stored as printed",
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} polling stations print more ballots cast than electors on the official voters list, mostly "
            "stations designated for election-day workers, police, and the military; stored as printed",
        )
    return p


def _compare_mayor(day: str, c: pdf.Constituency, declared: pdf.Totals, report: Report | None) -> tuple[str, list[str]]:
    """The station sums against the division totals of the mayoral report, and its division totals against the summary."""
    problems = []
    sums = [sum(s.votes[i] or 0 for s in c.stations if s.votes) for i in range(len(c.candidates))]
    if sums != declared.votes[: len(sums)] or declared.stations != len(c.stations):
        problems.append(f"{day} mayor: stations add up to {sums} and the divisions to {declared}")
    differs = ""
    if report is not None and report.summary:
        shown = Counter((x.rejected, x.total, x.electors) for x in summary(report.summary).lines)
        mine = Counter((d.rejected, d.total, d.electors) for d in c.divisions)
        if shown != mine:
            problems.append(f"{day} mayor: summary divisions {shown - mine} against report {mine - shown}")
    return differs, problems
