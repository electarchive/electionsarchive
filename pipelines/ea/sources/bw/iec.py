import functools
import re
import subprocess
from datetime import date
from typing import NamedTuple
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.bw import iec1999, text
from ea.sources.bw.text import Unit

SOURCE = "bw.iec"
BOTSWANA = str(uuid5(package.NAMESPACE, "polity\x1fBW"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBWA"))
HOME = "https://www.iec.gov.bw/"
BASE = "https://www.iec.gov.bw/images"


# Each general election report prints every polling result in its appendices: the constituency results for the
# National Assembly, and the polling district results for the local councils.
class General(NamedTuple):
    day: date
    url: str
    marks: tuple[str, str, str, str]
    parties: dict[str, int]
    seats: dict[str, int]


class Report(NamedTuple):
    url: str
    title: str


GENERAL = {
    2009: General(
        date(2009, 10, 16),
        f"{BASE}/2009_report.pdf",
        ("APPENDIX A", "APPENDIX B", "APPENDIX E", "Appendix F"),
        {
            "BAM": 12387,
            "BCP": 104302,
            "BDP": 290099,
            "BNF": 119509,
            "BPP": 7554,
            "BTTO": 40,
            "IND": 10464,
            "MELS": 292,
        },
        {"BAM": 1, "BCP": 4, "BDP": 45, "BNF": 6, "IND": 1},
    ),
    2014: General(
        date(2014, 10, 24),
        f"{BASE}/documents/2014%20General%20Elections%20Report.pdf",
        ("APPENDIX A", "APPENDIX B", "APPENDIX E", "Appendix F"),
        {"BCP": 140998, "BDP": 320647, "IND": 21484, "UDC": 207113},
        {"BCP": 3, "BDP": 37, "UDC": 17},
    ),
}
# The 2019 report prints only the National Assembly results by constituency in its first annexure; the councils'
# ward results from its second annexure are built by bw.iec_council, on the same polling day.
ASSEMBLY_2019 = General(
    date(2019, 10, 23),
    f"{BASE}/Botswana_2019_General_Elections_REPORT(1).pdf",
    ("Annexure 1", "Annexure 2", "", ""),
    {},
    # The report's text: 38 seats for the BDP, 15 for the UDC, 3 for the BPF, and 1 for the AP, of the 57.
    {"BDP": 38, "UDC": 15, "BPF": 3, "AP": 1},
)
COUNCIL_ELECTION_2019 = str(uuid5(package.NAMESPACE, "bw.iec_council\x1felection\x1f2019-10-23"))
COUNCIL_PACKAGE_2019 = "bw.iec_council/2019-10-23"
# Each by-election report prints its results, one block per polling district.
BY = {
    date(2021, 12, 18): Report(f"{BASE}/11_by_election__REPORT.pdf", "2021 local government by-elections"),
    date(2022, 2, 5): Report(f"{BASE}/Ledumadumane.pdf", "2022 Ledumadumane East by-election"),
    date(2022, 4, 9): Report(f"{BASE}/Bophirima_REPORT_2022.pdf", "2022 Bophirima by-election"),
    date(2022, 7, 30): Report(f"{BASE}/Moselewapula_REPORT.pdf", "2022 Moselewapula by-election"),
}
BY_COUNCILS = {"bophirima": "GABORONE CITY COUNCIL", "moselewapula": "FRANCISTOWN CITY COUNCIL"}

# The 2009 appendix names only its first council, so the others follow from the printed unit numbers, which run
# through each council's seats. They match the seats each council contested in the report's summary of the elections.
COUNCILS_2009 = (
    (1, 46, "NORTH WEST DISTRICT COUNCIL"),
    (47, 65, "NORTH EAST DISTRICT COUNCIL"),
    (66, 84, "FRANCISTOWN CITY COUNCIL"),
    (85, 138, "CENTRAL DISTRICT COUNCIL"),
    (139, 152, "SELIBE PHIKWE TOWN COUNCIL"),
    (153, 238, "CENTRAL DISTRICT COUNCIL"),
    (239, 261, "KGATLENG DISTRICT COUNCIL"),
    (262, 291, "GABORONE CITY COUNCIL"),
    (292, 311, "SOUTH EAST DISTRICT COUNCIL"),
    (312, 377, "KWENENG DISTRICT COUNCIL"),
    (378, 389, "LOBATSE TOWN COUNCIL"),
    (390, 441, "SOUTHERN DISTRICT COUNCIL"),
    (442, 448, "JWANENG TOWN COUNCIL"),
    (449, 470, "KGALAGADI DISTRICT COUNCIL"),
    (471, 490, "GHANZI DISTRICT COUNCIL"),
)
COUNCIL_SEATS_2009 = {
    "NORTH WEST DISTRICT COUNCIL": 46,
    "NORTH EAST DISTRICT COUNCIL": 19,
    "FRANCISTOWN CITY COUNCIL": 19,
    "CENTRAL DISTRICT COUNCIL": 140,
    "SELIBE PHIKWE TOWN COUNCIL": 14,
    "KGATLENG DISTRICT COUNCIL": 23,
    "GABORONE CITY COUNCIL": 30,
    "SOUTH EAST DISTRICT COUNCIL": 20,
    "KWENENG DISTRICT COUNCIL": 66,
    "LOBATSE TOWN COUNCIL": 12,
    "SOUTHERN DISTRICT COUNCIL": 52,
    "JWANENG TOWN COUNCIL": 7,
    "KGALAGADI DISTRICT COUNCIL": 22,
    "GHANZI DISTRICT COUNCIL": 20,
}
PARTIES = {
    "BAM": "Botswana Alliance Movement",
    "BCP": "Botswana Congress Party",
    "BDP": "Botswana Democratic Party",
    "BLP": "Botswana Labour Party",
    "AP": "Alliance for Progressives",
    "BMD": "Botswana Movement for Democracy",
    "BNF": "Botswana National Front",
    "RAP": "Real Alternative Party",
    "SDP": "Social Democratic Party",
    "BPF": "Botswana Patriotic Front",
    "BPU": "Botswana Progressive Union",
    "BPP": "Botswana Peoples Party",
    "BTTO": "Botswana Tlhoko Tiro Organisation",
    "MELS": "MELS Movement of Botswana",
    "UDC": "Umbrella for Democratic Change",
}
COALITIONS = {"UDC"}
NAMES = {"bam": "BAM"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


IEC_SOURCE = key("source", SOURCE)
ASSEMBLY = key("body", "national_assembly")
SYSTEM_FPTP = key("electoral_system", "fptp")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def party(code: str) -> str:
    return key("party", code)


def council_body(council: str) -> str:
    return key("body", "council", changeset.slug(council))


def _artifact(url: str) -> raw.Artifact:
    """The cached report, found under its own address or under the archived copy of it."""
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        for found in sorted(raw.keys(SOURCE)):
            if found.endswith((url, url.replace("%20", " "))):
                return raw.cached(SOURCE, found)
        raise


def fetch() -> None:
    for spec in [*GENERAL.values(), ASSEMBLY_2019, *BY.values()]:
        raw.fetch(SOURCE, spec.url, suffix=".pdf")
    raw.fetch(SOURCE, iec1999.URL, suffix=".pdf")


def pages(url: str, name: str) -> list[str]:
    """The report's text with its layout kept, one string per line."""
    cache = raw.parsed(SOURCE) / f"{name}.txt"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        path = raw.DATA / _artifact(url).storage_key
        subprocess.run(["pdftotext", "-layout", str(path), str(cache)], check=True, capture_output=True)
    return cache.read_text().split("\n")


def lines(year: int) -> list[str]:
    return pages(GENERAL[year].url, str(year))


ASSEMBLY_HEADER = re.compile(r"^\s*(\d{1,2})\s+(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+(\d+)%\s*$")
# A page break once leaves a stray letter after a constituency's code, so the name and figures print on the next line.
ASSEMBLY_STRAY = re.compile(r"^\s*(\d{1,2})\s+[A-Z]\s*$")
ASSEMBLY_NAMED = re.compile(r"^\s+(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+)\s+(\d+)%\s*$")
ASSEMBLY_CANDIDATE = re.compile(r"^\s+(\S.*?)\s{2,}(\S.*?)\s{2,}([MF])\s+([A-Z]{2,5})\s+(\d+)\s*$")


CORRECTIONS_2019 = {"35": 52}
NOTES_2019: dict[str, str] = {}


class Constituency(NamedTuple):
    unit: Unit
    margin: int
    turnout: int


@functools.cache
def assembly_2019() -> list[Constituency]:
    """The 2019 constituencies, with candidates, from the report's first annexure."""
    rows = pages(ASSEMBLY_2019.url, "2019")
    start = next(i for i, row in enumerate(rows) if row.strip() == "Annexure 1" and "Parliamentary" in rows[i + 1])
    end = next(i for i in range(start + 1, len(rows)) if rows[i].strip() == "Annexure 2")
    found: list[Constituency] = []
    stray = ""
    for row in rows[start:end]:
        match = ASSEMBLY_HEADER.match(row)
        if match:
            code, name, *figures = match.groups()
        else:
            match = ASSEMBLY_STRAY.match(row)
            if match:
                stray = match[1]
                continue
            match = ASSEMBLY_NAMED.match(row) if stray else None
            if not match:
                candidate = ASSEMBLY_CANDIDATE.match(row)
                if candidate and found:
                    first, last, _, code, votes = candidate.groups()
                    found[-1].unit.candidates.append((f"{first.strip()} {last.strip()}".title(), code, int(votes)))
                continue
            code, (name, *figures) = stray, match.groups()
            stray = ""
        registered, valid, rejected, cast, margin, turnout = (int(n) for n in figures)
        unit = Unit(code, name.strip(), "", registered, rejected, cast, margin)
        unit.valid = valid
        found.append(Constituency(unit, margin, turnout))
    if [int(c.unit.code) for c in found] != list(range(1, 58)):
        raise ValueError(f"the 2019 annexure does not print constituencies 1 to 57 in order: {len(found)}")
    for each in found:
        unit = each.unit
        ranked = sorted((v or 0 for _, _, v in unit.candidates), reverse=True)
        if unit.code in CORRECTIONS_2019:
            note = (
                f"The annexure prints {unit.rejected:,} rejected votes, which cannot be right beside its {unit.valid:,} "
                f"valid and {unit.cast:,} total votes, so it is stored as {CORRECTIONS_2019[unit.code]:,}, the figure "
                "that makes the three add up and that brings the constituencies' rejected votes to the 6,185 the "
                "report's rejected ballots trend gives for 2019."
            )
            NOTES_2019[unit.code] = note
            unit.rejected = CORRECTIONS_2019[unit.code]
        if sum(ranked) != unit.valid or unit.valid + unit.rejected != unit.cast:
            raise ValueError(f"2019 {unit.name}: the printed totals do not add up")
        if ranked[0] - ranked[1] != each.margin:
            raise ValueError(f"2019 {unit.name}: the printed margin is not the leaders' difference")
    return found


@functools.cache
def by(day: date) -> list[Unit]:
    """The by-election's polling districts, with candidates, from the first results table of its report."""
    found: dict[str, Unit] = {}
    current: Unit | None = None
    for line in pages(BY[day].url, f"by-{day}"):
        match = text.BY_HEADER.match(line)
        if match:
            code = match[1]
            if code in found:
                current = None
                continue
            current = found[code] = Unit(
                code, match[2].strip(), "", text.number(match[3]), text.number(match[5]), text.number(match[6]), None
            )
            current.valid = text.number(match[4])
            continue
        match = text.BY_CANDIDATE.match(line)
        if match and current is not None:
            current.candidates.append(
                (f"{match[1].strip()} {match[2].strip()}".title(), match[4], text.number(match[5]))
            )
    known = {key: name for key, name in districts_by_council().items()}
    for unit in found.values():
        slug = changeset.slug(unit.name)
        unit.council = BY_COUNCILS.get(slug) or known[slug]
        _settle(unit)
        if not unit.candidates:
            raise ValueError(f"{day} {unit.name} has no candidates")
    return list(found.values())


@functools.cache
def districts_by_council() -> dict[str, str]:
    """A polling district's council, for the districts of the general elections."""
    found: dict[str, str] = {}
    for year in GENERAL:
        for unit in books(year)[1]:
            found[changeset.slug(unit.name)] = unit.council
    return found


@functools.cache
def books(year: int) -> tuple[list[Unit], list[Unit]]:
    """The year's constituency results for the National Assembly and polling district results for the councils."""
    rows = lines(year)
    marks = GENERAL[year].marks
    found: list[int] = []
    for mark in marks:
        found.append(
            next(
                i
                for i in range(found[-1] + 1 if found else 0, len(rows))
                if rows[i].strip().lower().startswith(mark.lower())
            )
        )
    parliament = text.units(rows, found[0], found[1])
    local = text.units(rows, found[2], found[3])
    if year == 2009:
        for unit in local:
            unit.council = next(name for low, high, name in COUNCILS_2009 if low <= int(unit.code) <= high)
        seats: dict[str, int] = {}
        for unit in local:
            seats[unit.council] = seats.get(unit.council, 0) + 1
        if seats != COUNCIL_SEATS_2009:
            raise ValueError(f"2009 council seats do not match the report: {seats}")
    for unit in [*parliament, *local]:
        _settle(unit)
    return parliament, local


def _settle(unit: Unit) -> None:
    """Take the valid total from the candidates when the printed total line is garbled, and check the unit adds up."""
    votes = sum(v or 0 for _, _, v in unit.candidates)
    if unit.cast == -1:
        unit.valid = None
        return
    if unit.valid != votes:
        unit.valid = votes
    if votes + unit.rejected != unit.cast:
        raise ValueError(f"{unit.line} {unit.name}: {unit.valid} valid and {unit.rejected} rejected is not {unit.cast}")


def packages() -> list[str]:
    days = sorted([iec1999.DAY, *(GENERAL[y].day for y in GENERAL), ASSEMBLY_2019.day, *BY])
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days)]


def parse() -> None:
    geo().write()
    registry().write()
    iec1999.images()
    iec1999.general().write()
    for year in GENERAL:
        general(year).write()
    assembly(2019).write()
    for day in BY:
        byelection(day).write()


def emit() -> None:
    package.run("load", packages())


def _source(p: Package) -> None:
    p.add(
        "source",
        id=IEC_SOURCE,
        kind="official_body",
        publisher="Independent Electoral Commission of Botswana",
        title="General election reports of the Independent Electoral Commission",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent Electoral Commission of Botswana",
    )


def councils() -> list[str]:
    found: set[str] = set(iec1999.councils())
    for year in GENERAL:
        found |= {u.council for u in books(year)[1]}
    return sorted(found)


def constituencies() -> dict[str, str]:
    found: dict[str, str] = {}
    for year in GENERAL:
        for unit in books(year)[0]:
            found[changeset.slug(unit.name)] = unit.name.title()
    for each in assembly_2019():
        found.setdefault(changeset.slug(each.unit.name), each.unit.name.title())
    for code, name in iec1999.constituencies().items():
        found.setdefault(code, name)
    return dict(sorted(found.items()))


def districts() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for year in GENERAL:
        for unit in books(year)[1]:
            found[(changeset.slug(unit.council), changeset.slug(unit.name))] = unit.name.title()
    for day in BY:
        for unit in by(day):
            found.setdefault((changeset.slug(unit.council), changeset.slug(unit.name)), unit.name.title())
    for key_, name in iec1999.districts().items():
        found.setdefault(key_, name)
    return dict(sorted(found.items()))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Botswana's local councils, National Assembly constituencies, and council polling districts as the "
        "Independent Electoral Commission's general election reports list them",
    )
    p.external |= {BOTSWANA, COUNTRY}
    _source(p)
    for spec in [*GENERAL.values(), ASSEMBLY_2019, *BY.values()]:
        p.artifact(_artifact(spec.url), IEC_SOURCE)
    p.artifact(_artifact(iec1999.URL), IEC_SOURCE)
    for code, rank, kind, noun in (
        ("bw.council", 1, "administrative", "local council"),
        ("bw.constituency", 1, "electoral_district", "constituency"),
        ("bw.polling_district", 2, "electoral_district", "polling district"),
    ):
        p.add("scheme", code=code, polity_id=BOTSWANA, kind=kind, rank=rank, noun=noun, code_system=None)
    for name in councils():
        id = area("bw.council", changeset.slug(name))
        p.add("area", id=id, scheme_code="bw.council")
        p.add("area_code", area_id=id, code=changeset.slug(name))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name.title(), "en", "official")
    for code, name in constituencies().items():
        id = area("bw.constituency", code)
        p.add("area", id=id, scheme_code="bw.constituency")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
    for (council, code), name in districts().items():
        id = area("bw.polling_district", f"{council}:{code}")
        p.add("area", id=id, scheme_code="bw.polling_district")
        p.add("area_code", area_id=id, code=f"{council}:{code}")
        p.add("area_parent", area_id=id, parent_id=area("bw.council", council))
        p.name(id, name, "en", "official")
    return p


def used_parties() -> list[str]:
    found: set[str] = set()
    for year in GENERAL:
        for units in books(year):
            for unit in units:
                found |= {code for _, code, _ in unit.candidates if code != "IND"}
    for each in assembly_2019():
        found |= {code for _, code, _ in each.unit.candidates if code != "IND"}
    for day in BY:
        for unit in by(day):
            found |= {code for _, code, _ in unit.candidates if code != "IND"}
    return sorted(found | iec1999.parties())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Botswana's parties, National Assembly, local councils, and electoral system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BOTSWANA}
    p.add("body", id=ASSEMBLY, polity_id=BOTSWANA, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    for name in councils():
        id = council_body(name)
        p.add("body", id=id, polity_id=BOTSWANA, kind="council")
        p.name(id, name.title(), "en", "official")
    p.add(
        "electoral_system",
        id=SYSTEM_FPTP,
        polity_id=BOTSWANA,
        valid_from=date(1965, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_FPTP),
        entity_id=SYSTEM_FPTP,
        kind="methodology",
        body_md="One member for each constituency or council ward, elected by the most votes.",
        lang="en",
    )
    for code in used_parties():
        id = party(code)
        p.add("party", id=id, kind="bloc" if code in COALITIONS else "party", polity_id=BOTSWANA)
        p.add("external_id", entity_id=id, scheme="bw.iec.party", value=code)
        p.name(id, PARTIES.get(code, code), "en", "official")
        if code in PARTIES:
            p.name(id, code, "en", "abbreviation", False)
    return p


METHOD = (
    "Counted by the Independent Electoral Commission for each {unit}, as the appendix to its general election report "
    "prints them. Registered voters are the roll, rejected votes are the ballots set aside, and ballots cast are the "
    "valid and rejected ballots together. The valid votes are the candidates' votes added up. The report declares "
    "winners through each unit's winning margin and its tally of seats by party, so the leading candidate is stored "
    "as elected; a unit whose leading candidates tied has no winner stored."
)


def _contest(
    p: Package,
    election: str,
    tag: str,
    unit: Unit,
    *,
    path: str,
    name: str,
    district: str,
    body: str,
    unit_id: str,
    extra: str = "",
) -> None:
    walkover = unit.cast == -1
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM_FPTP,
        default_result_kind="tacit" if walkover else "counted",
    )
    p.name(contest, name, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if walkover else "counted",
        source_id=IEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD.format(unit="constituency" if "parliament" in tag else "polling district")
    if extra:
        method += f" {extra}"
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    ranked = sorted((v or 0 for _, _, v in unit.candidates), reverse=True)
    for index, (candidate, code, votes) in enumerate(unit.candidates, start=1):
        option = key("option", tag, index)
        person = key("person", tag, index)
        solo = code == "IND"
        p.add("person", id=person)
        p.name(person, candidate, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if solo else party(code),
            ballot_position=index,
            ballot_label=candidate,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, index),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate,
            member_of_party_id=None if solo else party(code),
            is_independent=solo,
        )
        if walkover or (votes == ranked[0] and (len(ranked) < 2 or ranked[0] > ranked[1])):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        if votes is not None:
            _value(p, result_set, unit_id, option, 1, votes)
    for measure, value in (
        (2, unit.registered),
        (4, unit.valid),
        (5, None if walkover else unit.rejected),
        (3, None if walkover else unit.cast),
    ):
        if value is not None:
            _value(p, result_set, unit_id, None, measure, value)


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


def general(year: int) -> Package:
    spec = GENERAL[year]
    day = spec.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} general election of Botswana: the National Assembly by constituency and the local "
        "councils by polling district",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BOTSWANA, COUNTRY}
    p.uses(_artifact(spec.url))
    parliament, local = books(year)
    _declared(year, parliament)
    election = key("election", day)
    path = f"bw/{day}"
    p.add("election", id=election, polity_id=BOTSWANA, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Botswana general election", "en", "official")
    for unit in parliament:
        code = changeset.slug(unit.name)
        unit_id = key("unit", f"{day}:constituency:{code}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=BOTSWANA,
            scheme_code="bw.constituency",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=area("bw.constituency", code),
            kind="standard",
        )
        _contest(
            p,
            election,
            f"{day}:parliament:{code}",
            unit,
            path=f"{path}/parliament/{code}",
            name=f"{year} National Assembly election, {unit.name.title()}",
            district=area("bw.constituency", code),
            body=ASSEMBLY,
            unit_id=unit_id,
        )
    for unit in local:
        council = changeset.slug(unit.council)
        code = f"{council}:{changeset.slug(unit.name)}"
        unit_id = key("unit", f"{day}:polling_district:{unit.code}:{code}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=BOTSWANA,
            scheme_code="bw.polling_district",
            code=f"{day}:{unit.code}:{code}",
            area_relation="equals",
            area_id=area("bw.polling_district", code),
            kind="standard",
        )
        _contest(
            p,
            election,
            f"{day}:local:{unit.code}:{code}",
            unit,
            path=f"{path}/local/{unit.code}-{changeset.slug(unit.name)}",
            name=f"{year} local council election, {unit.name.title()} ({unit.council.title()})",
            district=area("bw.polling_district", code),
            body=council_body(unit.council),
            unit_id=unit_id,
        )
    return p


def assembly(year: int) -> Package:
    """The 2019 National Assembly election by constituency, on the polling day of the councils' ward elections."""
    day = ASSEMBLY_2019.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} general election of Botswana: the National Assembly by constituency",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", COUNCIL_PACKAGE_2019],
    )
    p.external |= {BOTSWANA, COUNTRY}
    p.uses(_artifact(ASSEMBLY_2019.url))
    constituencies_ = assembly_2019()
    _declared_2019([each.unit for each in constituencies_])
    election = key("election", day)
    path = f"bw/{day}"
    # The councils' ward elections were held the same day, and their own package holds that election.
    p.add(
        "election",
        id=election,
        polity_id=BOTSWANA,
        parent_id=COUNCIL_ELECTION_2019,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Botswana general election", "en", "official")
    p.add(
        "note",
        id=key("note", election),
        entity_id=election,
        kind="methodology",
        body_md="The National Assembly election by constituency. The local council ward elections of the same polling "
        "day are a separate election, linked as its parent.",
        lang="en",
    )
    for each in constituencies_:
        unit = each.unit
        code = changeset.slug(unit.name)
        unit_id = key("unit", f"{day}:constituency:{code}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=BOTSWANA,
            scheme_code="bw.constituency",
            code=f"{day}:{code}",
            area_relation="equals",
            area_id=area("bw.constituency", code),
            kind="standard",
        )
        _contest(
            p,
            election,
            f"{day}:parliament:{code}",
            unit,
            path=f"{path}/parliament/{code}",
            name=f"{year} National Assembly election, {unit.name.title()}",
            district=area("bw.constituency", code),
            body=ASSEMBLY,
            unit_id=unit_id,
            extra=NOTES_2019.get(unit.code, ""),
        )
    return p


def _declared_2019(parliament: list[Unit]) -> None:
    """Check the constituencies against the report's text on the seats won and its national voter figures."""
    seats: dict[str, int] = {}
    for unit in parliament:
        leader = max(unit.candidates, key=lambda candidate: candidate[2] or 0)
        seats[leader[1]] = seats.get(leader[1], 0) + 1
    # Annexure 5 prints 925,478 registered voters, and annexure 6 prints 778,343 voted and 6,185 rejected ballots.
    totals = (
        sum(unit.registered for unit in parliament),
        sum(unit.cast for unit in parliament),
        sum(unit.rejected for unit in parliament),
    )
    if seats != ASSEMBLY_2019.seats or totals != (925478, 778343, 6185):
        raise ValueError(f"2019 constituencies do not match the report's seats and national totals: {seats} {totals}")


def byelection(day: date) -> Package:
    spec = BY[day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} local government by-elections of Botswana, by polling district",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BOTSWANA, COUNTRY}
    p.uses(_artifact(spec.url))
    election = key("election", day)
    path = f"bw/{day}"
    p.add("election", id=election, polity_id=BOTSWANA, kind="by_election", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, spec.title, "en", "official")
    for unit in by(day):
        council = changeset.slug(unit.council)
        code = f"{council}:{changeset.slug(unit.name)}"
        unit_id = key("unit", f"{day}:polling_district:{unit.code}:{code}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=BOTSWANA,
            scheme_code="bw.polling_district",
            code=f"{day}:{unit.code}:{code}",
            area_relation="equals",
            area_id=area("bw.polling_district", code),
            kind="standard",
        )
        _contest(
            p,
            election,
            f"{day}:local:{unit.code}:{code}",
            unit,
            path=f"{path}/local/{unit.code}-{changeset.slug(unit.name)}",
            name=f"{day.year} local council by-election, {unit.name.title()} ({unit.council.title()})",
            district=area("bw.polling_district", code),
            body=council_body(unit.council),
            unit_id=unit_id,
        )
    return p


def _declared(year: int, parliament: list[Unit]) -> None:
    """Check the constituencies against the report's own national tables of party votes and seats won."""
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for unit in parliament:
        leader = max(unit.candidates, key=lambda candidate: candidate[2] or 0)
        seats[leader[1]] = seats.get(leader[1], 0) + 1
        for _, code, count in unit.candidates:
            votes[code] = votes.get(code, 0) + (count or 0)
    if votes != GENERAL[year].parties or seats != GENERAL[year].seats:
        raise ValueError(f"{year} constituencies do not match the report's party votes and seats: {votes} {seats}")
