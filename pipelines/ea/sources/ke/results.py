"""The packages for Kenya's 2017 elections: the registry of bodies, offices, systems, and parties, the general election
of 8 August 2017 with its six ballots, and the fresh presidential election of 26 October 2017."""

import functools
import subprocess
from collections import Counter, defaultdict
from datetime import date
from typing import cast

import pymupdf

from ea import changeset, raw
from ea.package import Package
from ea.sources.ke import iebc, report2017
from ea.sources.ke.iebc import CONSTITUENCY, COUNTY, KENYA, SOURCE, TERRITORY, WARD, area, key, titled
from ea.sources.ke.report2017 import Entry

REPORT = "https://www.iebc.or.ke/uploads/resources/siEABKREDq.pdf"
FRESH = "https://www.iebc.or.ke/uploads/resources/A1wUl9EnMM.pdf"
REPORT_SOURCE = key("source", "report-2017")
FRESH_SOURCE = key("source", "fresh-2017")
GENERAL = date(2017, 8, 8)
RERUN = date(2017, 10, 26)
NATIONAL, SENATE = key("body", "national-assembly"), key("body", "senate")
PRESIDENCY = key("office", "president")
FPTP, PRESIDENTIAL = key("electoral_system", "plurality"), key("electoral_system", "president")
SINCE = date(2010, 8, 27)
VOTES, REGISTERED, VALID, REJECTED = 1, 2, 4, 5
DIASPORA = "diaspora"
ABROAD = "abroad"

METHOD = (
    "Counted by the Independent Electoral and Boundaries Commission and printed in its Data Report of 2017 Elections. "
    "The report prints each candidate's votes in a county, constituency, or county assembly ward, and prints no "
    "registered voters, rejected ballots, or winners for these ballots, so the candidate with the most votes is "
    "stored as elected. The report prints a total of valid votes for most counties' governor, senator, and woman "
    "representative contests; those are stored as declared. A dash in the votes column is read as no votes. Lines "
    "whose votes the report's text layer does not carry are stored without a value."
)
PRESIDENT_METHOD = (
    "Counted by the Independent Electoral and Boundaries Commission and printed per county in the report's national "
    "tallying table, with a line for votes cast outside Kenya. Registered voters, each candidate's votes, valid votes, "
    'and rejected ballots are as printed, except that the table prints the word "no" for one figure, the votes for '
    "Shakhalaga Khwa Jirongo in Uasin Gishu, which is stored without a value. The candidate with the most votes is "
    "stored as elected. The Supreme Court "
    "annulled this result on 1 September 2017 and ordered the fresh election of 26 October 2017."
)
FRESH_METHOD = (
    "Counted by the Independent Electoral and Boundaries Commission and printed per county in its summary of the fresh "
    "presidential election, and again in its Data Report of 2017 Elections. Registered voters, each candidate's "
    "votes, valid votes, and rejected ballots are as printed. Raila Odinga withdrew from the election but stayed on the "
    "ballot, and his votes are stored as printed. Siaya and Homa Bay counties held no vote on the day, and the "
    "tables print only their registered voters; in Kisumu and Migori counties few voted. The commission "
    "published no result for the constituencies or below."
)


@functools.cache
def document() -> pymupdf.Document:
    return pymupdf.open(raw.cached(SOURCE, REPORT).path)


@functools.cache
def tables() -> dict[str, list[report2017.Row]]:
    return {
        ballot: report2017.rows(document(), ballot) for ballot in ("assembly", "woman", "senate", "governor", "ward")
    }


@functools.cache
def parties() -> dict[str, tuple[str, str]]:
    return report2017.parties(tables())


@functools.cache
def ballots() -> dict[str, list[Entry]]:
    wards = iebc.roll()
    counties = {w.county: w.county_name for w in wards}
    constituencies = {w.constituency: (w.county, w.constituency_name) for w in wards}
    places = {w.code: (w.constituency, w.name) for w in wards}
    return {
        ballot: report2017.entries(ballot, rows, counties, constituencies, places, parties())
        for ballot, rows in tables().items()
    }


def printed(path: str, first: int | None = None, last: int | None = None) -> str:
    command = ["pdftotext", "-layout"]
    if first is not None:
        command += ["-f", str(first), "-l", str(last)]
    return subprocess.run(
        [*command, str(raw.cached(SOURCE, path).path), "-"], check=True, capture_output=True, text=True
    ).stdout


@functools.cache
def president() -> tuple[list[report2017.County], report2017.County]:
    return report2017.counties(printed(REPORT, *report2017.PAGES["president"][:2]), "NATIONAL TOTAL")


@functools.cache
def fresh() -> tuple[list[report2017.County], report2017.County]:
    return report2017.counties(printed(FRESH), "NATIONAL TOTAL")


# The party or independent status each presidential candidate has in the report's tables of results (Tables 2 and 3).
PRESIDENTIAL_PARTIES = ("076", "055", "064", None, "027", None, None, "021")


def party(code: str) -> str:
    return key("party", code)


def person_name(surname: str, names: str) -> str:
    return titled(f"{surname} {names}".strip())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Kenya's National Assembly, Senate, county assemblies, presidency, governors, electoral systems, and the "
        "parties on the 2017 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {KENYA, TERRITORY}
    p.add(
        "source",
        id=REPORT_SOURCE,
        kind="official_body",
        publisher="Independent Electoral and Boundaries Commission",
        title="Data Report of 2017 Elections",
        url=REPORT,
    )
    p.add(
        "source",
        id=FRESH_SOURCE,
        kind="official_body",
        publisher="Independent Electoral and Boundaries Commission",
        title="Fresh Presidential Election Results, County Summary (26 October 2017)",
        url=FRESH,
    )
    p.artifact(raw.cached(SOURCE, REPORT), REPORT_SOURCE)
    p.artifact(raw.cached(SOURCE, FRESH), FRESH_SOURCE)
    p.add("body", id=NATIONAL, polity_id=KENYA, kind="lower_house")
    p.name(NATIONAL, "National Assembly", "en", "official")
    p.add("body", id=SENATE, polity_id=KENYA, kind="upper_house")
    p.name(SENATE, "Senate", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=KENYA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Kenya", "en", "official")
    for code, name in sorted({w.county: titled(w.county_name) for w in iebc.roll()}.items()):
        assembly = key("body", "county-assembly", code)
        p.add("body", id=assembly, polity_id=KENYA, kind="council")
        p.name(assembly, f"{name} County Assembly", "en", "official")
        governor = key("office", "governor", code)
        p.add("office", id=governor, polity_id=KENYA, kind="governor")
        p.name(governor, f"Governor of {name} County", "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=KENYA,
        valid_from=SINCE,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Members of the National Assembly for constituencies, county assemblies for wards, senators, "
        "governors, and county woman representatives are each elected by plurality: the candidate with the most votes "
        "wins.",
        lang="en",
    )
    p.add(
        "electoral_system",
        id=PRESIDENTIAL,
        polity_id=KENYA,
        valid_from=SINCE,
        valid_to=None,
        rules={
            "formula": "two_round",
            "majority": 0.5,
            "advance": 2,
            "spread": {"scheme": COUNTY, "share": 0.25, "units": {"share": 0.5, "strict": True}},
        },
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", PRESIDENTIAL),
        entity_id=PRESIDENTIAL,
        kind="methodology",
        body_md="The Constitution of 2010 elects the president if one candidate wins more than half of the valid votes "
        "and at least a quarter of the votes in more than half of the 47 counties. Otherwise the two leading "
        "candidates go to a second round within thirty days.",
        lang="en",
    )
    for code, (abbreviation, name) in sorted(parties().items()):
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=KENYA)
        p.add("external_id", entity_id=id, scheme="ke.iebc.party", value=code)
        p.name(id, titled(name), "en", "official")
        if abbreviation != name:
            p.name(id, abbreviation, "en", "abbreviation", False)
    return p


class Builder:
    """Stages the units, contests, and values of one election's package."""

    def __init__(self, p: Package, day: date, source: str) -> None:
        self.p = p
        self.day = day
        self.source = source
        self.units: dict[tuple[str, str], str] = {}
        self.slugs: set[str] = set()
        self.path = f"ke/{day}-general" if day == GENERAL else f"ke/{day}-presidential"

    def unit(self, level: str, code: str) -> str:
        """The reporting unit for an area on this day, staged on first use."""
        known = self.units.get((level, code))
        if known is not None:
            return known
        id = key("unit", self.day, level, code)
        self.units[(level, code)] = id
        if level == DIASPORA:
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=KENYA,
                scheme_code=COUNTY,
                code=f"{self.day}:{DIASPORA}",
                area_relation="unlocated",
                area_id=None,
                kind="abroad_mission",
            )
            return id
        scheme, area_key = {COUNTY: (COUNTY, code), CONSTITUENCY: (CONSTITUENCY, code), WARD: (WARD, code)}[level]
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=KENYA,
            scheme_code=scheme,
            code=f"{self.day}:{code}",
            area_relation="equals",
            area_id=area(scheme, area_key),
            kind="standard",
        )
        return id

    def slug(self, election: str, *parts: str) -> str:
        path = f"{self.path}/{'-'.join(changeset.slug(part) for part in parts)}"
        if path in self.slugs:
            raise ValueError(f"the slug {path} repeats")
        self.slugs.add(path)
        return path

    def contest(
        self,
        election: str,
        tag: str,
        title: str,
        slug: str,
        district: str,
        kind: str = "body_seats",
        body: str | None = None,
        office: str | None = None,
        system: str = FPTP,
        method: str | None = None,
        status: str = "active",
    ) -> tuple[str, str]:
        p = self.p
        contest = key("contest", self.day, tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        result_set = key("result_set", self.day, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=self.source,
            status=status,
            is_default=True,
            completeness=1.0,
        )
        if method:
            p.add(
                "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
            )
        return contest, result_set

    def put(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add(
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

    def candidates(self, contest: str, result_set: str, unit: str, tag: str, entries: list[Entry]) -> None:
        """The contest's candidates, their votes in its one unit, and the outcome of a plurality count."""
        p = self.p
        options: list[tuple[str, Entry]] = []
        for position, e in enumerate(entries, 1):
            ticket = bool(e.mate_surname)
            option = key("option", self.day, tag, position)
            person = key("person", self.day, tag, position)
            label = person_name(e.surname, e.names)
            member = party(e.party) if e.party != "IND" else None
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="ticket" if ticket else "candidate",
                party_id=member,
                ballot_position=position,
                ballot_label=label + (f" and {person_name(e.mate_surname, e.mate_names)}" if ticket else ""),
            )
            p.add(
                "candidacy",
                id=key("candidacy", self.day, tag, position, "head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=member,
                is_independent=True if e.party == "IND" else None,
            )
            if ticket:
                mate = key("person", self.day, tag, position, "deputy")
                mate_label = person_name(e.mate_surname, e.mate_names)
                p.add("person", id=mate)
                p.name(mate, mate_label, "en", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", self.day, tag, position, "running_mate"),
                    person_id=mate,
                    option_id=option,
                    role="running_mate",
                    ballot_name=mate_label,
                    member_of_party_id=member,
                    is_independent=True if e.party == "IND" else None,
                )
            self.put(result_set, unit, option, VOTES, e.votes)
            options.append((option, e))
        self.outcomes(result_set, [(option, e.votes) for option, e in options])

    def outcomes(self, result_set: str, options: list[tuple[str, int | None]]) -> None:
        counted = [(option, votes) for option, votes in options if votes is not None]
        if len(counted) != len(options) or not counted:
            return
        top = max(votes for _, votes in counted if votes is not None)
        leaders = [option for option, votes in counted if votes == top]
        if len(leaders) != 1:
            return
        for option, _ in counted:
            if option == leaders[0]:
                self.p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            else:
                self.p.add("outcome", result_set_id=result_set, option_id=option, kind="lost", seats=0)


def group(entries: list[Entry]) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        out[e.area].append(e)
    return dict(sorted(out.items()))


def general() -> Package:
    p = Package(
        f"{SOURCE}/general-2017",
        SOURCE,
        "The 8 August 2017 general election: president by county, and governor, senator, woman representative, "
        "member of the National Assembly, and member of the county assembly by their areas",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {KENYA, TERRITORY}
    p.uses(raw.cached(SOURCE, REPORT))
    b = Builder(p, GENERAL, REPORT_SOURCE)
    election = key("election", GENERAL)
    p.add("election", id=election, polity_id=KENYA, kind="general", date_from=GENERAL, date_to=GENERAL, status="held")
    p.name(election, "2017 Kenyan general election", "en", "official")
    p.add("slug", path=b.path, entity_id=election, is_current=True)
    p.add("note", id=key("note", election), entity_id=election, kind="methodology", body_md=METHOD, lang="en")
    wards = iebc.roll()
    county_name = {w.county: titled(w.county_name) for w in wards}
    constituency_name = {w.constituency: titled(w.constituency_name) for w in wards}
    constituency_county = {w.constituency: w.county for w in wards}
    ward_info = {w.code: w for w in wards}
    data = ballots()
    # The president, by county, from the national tallying table.
    counties, national = president()
    contest, result_set = b.contest(
        election,
        "president",
        "2017 Kenyan presidential election",
        f"{b.path}/president",
        TERRITORY,
        kind="office",
        office=PRESIDENCY,
        system=PRESIDENTIAL,
        method=PRESIDENT_METHOD,
        status="annulled",
    )
    president_table(b, contest, result_set, counties, national, "president")
    # Governor, senator, and woman representative by county.
    for ballot, title, slug_name, kind, body in (
        ("governor", "Governor of {} County", "governor", "office", None),
        ("senate", "Senator for {} County", "senator", "body_seats", SENATE),
        ("woman", "Woman Representative for {} County", "woman-representative", "body_seats", NATIONAL),
    ):
        for county, entries in group(data[ballot]).items():
            tag = f"{ballot}:{county}"
            contest, result_set = b.contest(
                election,
                tag,
                title.format(county_name[county]),
                b.slug(election, slug_name, county_name[county]),
                area(COUNTY, county),
                kind=kind,
                office=key("office", "governor", county) if ballot == "governor" else None,
                body=body,
            )
            b.candidates(contest, result_set, b.unit(COUNTY, county), tag, entries)
            totals = {e.total for e in entries if e.total}
            if len(totals) == 1:
                b.put(result_set, b.unit(COUNTY, county), None, VALID, totals.pop())
    # Member of the National Assembly, by constituency.
    names = Counter(constituency_name.values())
    for code, entries in group(data["assembly"]).items():
        label = constituency_name[code] + (f" ({code})" if names[constituency_name[code]] > 1 else "")
        tag = f"assembly:{code}"
        contest, result_set = b.contest(
            election,
            tag,
            f"Member of the National Assembly for {label}",
            b.slug(election, "national-assembly", label),
            area(CONSTITUENCY, f"{constituency_county[code]}/{code}"),
            body=NATIONAL,
        )
        b.candidates(contest, result_set, b.unit(CONSTITUENCY, f"{constituency_county[code]}/{code}"), tag, entries)
    # Member of the county assembly, by ward.
    for code, entries in group(data["ward"]).items():
        w = ward_info[code]
        tag = f"ward:{code}"
        contest, result_set = b.contest(
            election,
            tag,
            f"Member of the {county_name[w.county]} County Assembly for {titled(w.name)} Ward",
            b.slug(election, "county-assembly", code, titled(w.name)),
            area(WARD, code),
            body=key("body", "county-assembly", w.county),
        )
        b.candidates(contest, result_set, b.unit(WARD, code), tag, entries)
    return p


def president_table(
    b: Builder,
    contest: str,
    result_set: str,
    counties: list[report2017.County],
    national: report2017.County,
    tag: str,
) -> None:
    """The presidential candidates and each county's figures, with the national line as declared."""
    p = b.p
    options: list[str] = []
    for position, (name, code) in enumerate(zip(report2017.CANDIDATES, PRESIDENTIAL_PARTIES, strict=True), 1):
        option = key("option", b.day, tag, position)
        person = key("person", b.day, "president", name)
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(code) if code else None,
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", b.day, tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=party(code) if code else None,
            is_independent=None if code else True,
        )
        options.append(option)
    names = {w.county: w.county_name for w in iebc.roll()}
    if len(counties) != len(names) + 1 or counties[-1].name != "DIASPORA":
        raise ValueError("the county lines are not the 47 counties and the diaspora")
    for county, (code, name) in zip(counties, [*sorted(names.items()), ("", "DIASPORA")], strict=True):
        if report2017.like(county.name, name) < 0.5:
            raise ValueError(f"the county line {county.name} is not {name}")
        unit = b.unit(DIASPORA, DIASPORA) if county.name == "DIASPORA" else b.unit(COUNTY, code)
        b.put(result_set, unit, None, REGISTERED, county.registered)
        if county.votes is None:
            continue
        for option, votes in zip(options, county.votes, strict=True):
            b.put(result_set, unit, option, VOTES, votes)
        b.put(result_set, unit, None, VALID, county.valid)
        b.put(result_set, unit, None, REJECTED, county.rejected)
    for measure, value in ((REGISTERED, national.registered), (VALID, national.valid), (REJECTED, national.rejected)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    assert national.votes is not None
    for option, votes in zip(options, national.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    accept(p, counties, national)
    totals = {option: sum(c.votes[i] or 0 for c in counties if c.votes) for i, option in enumerate(options)}
    b.outcomes(result_set, [(option, totals[option]) for option in options])


def accept(p: Package, counties: list[report2017.County], national: report2017.County) -> None:
    """Accept the findings that the report's own lines cause: a county whose valid votes are not its candidates' sum,
    and national figures that are not the sum of the county lines."""
    assert national.votes is not None
    wrong = [c for c in counties if c.votes is not None and sum(v or 0 for v in c.votes) != c.valid]
    if wrong:
        reasons = []
        for c in wrong:
            assert c.votes is not None
            missing = [report2017.CANDIDATES[i] for i, v in enumerate(c.votes) if v is None]
            reasons.append(
                f"the report prints {c.valid:,} valid votes for {titled(c.name)} where its candidates' votes add up to "
                f"{sum(v or 0 for v in c.votes):,}"
                + (f', and prints the word "no" in place of the votes for {", ".join(missing)}' if missing else "")
            )
        p.accept("accounting.votes_valid", "; ".join(reasons))
    held = [c for c in counties if c.votes is not None]
    pairs = [("registered voters", national.registered, sum(c.registered for c in counties))]
    pairs.append(("valid votes", cast(int, national.valid), sum(cast(int, c.valid) for c in held)))
    for index, name in enumerate(report2017.CANDIDATES):
        pairs.append(
            (f"{name}'s votes", cast(int, national.votes[index]), sum(c.votes[index] or 0 for c in held if c.votes))
        )
    differ = [f"{label} {printed:,} against {summed:,}" for label, printed, summed in pairs if printed != summed]
    if differ:
        p.accept(
            "accounting.declared",
            "The report's national line is not the sum of its county lines, which balance line by line except as the accepted valid-votes findings state: "
            + "; ".join(differ),
        )


def rerun() -> Package:
    p = Package(
        f"{SOURCE}/fresh-presidential-2017",
        SOURCE,
        "The 26 October 2017 fresh presidential election by county",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/general-2017"],
    )
    p.external |= {KENYA, TERRITORY}
    p.uses(raw.cached(SOURCE, FRESH))
    p.uses(raw.cached(SOURCE, REPORT))
    b = Builder(p, RERUN, FRESH_SOURCE)
    election = key("election", RERUN)
    p.add(
        "election",
        id=election,
        polity_id=KENYA,
        kind="general",
        date_from=RERUN,
        date_to=RERUN,
        status="held",
        rerun_of_id=key("election", GENERAL),
    )
    p.name(election, "2017 Kenyan fresh presidential election", "en", "official")
    p.add("slug", path=b.path, entity_id=election, is_current=True)
    counties, national = fresh()
    contest, result_set = b.contest(
        election,
        "president",
        "2017 Kenyan fresh presidential election",
        f"{b.path}/president",
        TERRITORY,
        kind="office",
        office=PRESIDENCY,
        system=PRESIDENTIAL,
        method=FRESH_METHOD,
    )
    president_table(b, contest, result_set, counties, national, "president")
    return p
