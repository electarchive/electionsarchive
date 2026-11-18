"""The Finnish elections of 2003 to 2009, built from the results service's HTML pages (see `pages`).

These elections come before the service's CSV files, and its pages carry the results by municipality, not by voting
district. A municipality is the finest unit the pages publish, so each election's reporting units are its municipalities.
"""

import functools
import itertools
from collections import defaultdict
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.fi import pages, vaalit
from ea.sources.fi.pages import Candidate, Election, Group, Page, Result, Row, Turnout, Unit

NEW_PARTIES = (
    "Liberaalit",
    "Yhteisvastuu puolue",
    'Kirjava "Puolue" - Elonkehän Puolesta - KIPU',
    "Eläkeläiset Kansan Asialla",
    "Muutosvoimat Suomi",
    "Suomi Nousee - Kansa Yhdistyy",
    "Suomen Kansan Sinivalkoiset",
    "Suomi - Isänmaa",
    "Itsenäisyyspuolue Vapaan Suomen Liitto",
    "Vaihtoehtoväki",
    "Suomen Isänmaallinen kansanliike",
)
ALIASES = {
    "Ruotsalainen kansanpuolue": "5",
    "Rauhan ja Sosialismin puolesta - Kommunistinen Työväenpuolue": "14",
}
VOTES_MEASURE, PREFERENCE = 1, 7
REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5
REPEAT_TITLES = {
    "K2008U": (
        "2009 Finnish municipal elections, repeat elections in Karkkila, Kauniainen, and Vihti",
        "Uusittavat kunnallisvaalit 2009 Karkkilassa, Kauniaisissa ja Vihdissä",
        "Omval 2009 i Karis, Grankulla och Vichtis",
    ),
    "K2009LOV": (
        "2009 Finnish municipal election in New Loviisa",
        "Uuden Loviisan kunnallisvaalit 2009",
        "Kommunalval i Nya Lovisa 2009",
    ),
}
METHOD = (
    "Published in the Ministry of Justice's election information and results service as one page for each "
    "municipality, constituency, and the whole country. The service prints votes for each list and, for the "
    "elections that have candidate pages, for each candidate; it prints no counts for voting districts. Votes and "
    "seats are those of the checked count (tarkastuslaskenta). Registered voters and voters are those of the "
    "service's turnout page, which leaves out Finnish citizens living abroad. The service also splits the votes "
    "into advance votes and election-day votes, which the archive does not store; the totals are stored."
)
LIST_METHOD = (
    " Each list holds its votes, and each candidate holds the votes for the candidate as preference votes. A "
    "candidate is placed on the list whose votes equal the votes of the candidates it is placed on, since the "
    "pages name a candidate's party only by an abbreviation, and the European Parliament pages name it not at all."
)
# The parliamentary elections whose voting districts `fi.statfin_aanestysalue` adds: its result sets supersede these
SUPERSEDED = {date(2003, 3, 16), date(2007, 3, 18)}
SUPERSEDED_NOTE = (
    " Statistics Finland's tables give the same votes by voting district, and the result set built from them "
    "supersedes this one as the contest's default; this set remains the one with each candidate's votes."
)
NO_WINNER = "The service marks no candidate as elected or advancing on these pages, so no outcome is stored."
RESULT_ONLY = (
    "The service marks no individual candidate as elected on these pages, so only each list's seats are stored."
)


# ---------------------------------------------------------------------------------------------------------------------
# Places


@functools.cache
def places() -> dict[str, dict[str, tuple[str, str]]]:
    """Municipalities, councils, and the region of Kainuu that the pages name, as {code: (Finnish name, Swedish name)}."""
    found: dict[str, dict[str, tuple[str, str]]] = {"fi.kunta": {}, "council": {}, "fi.maakunta": {}}
    for page in pages.PAGES:
        election = pages.load(page.folder)
        for group in election.groups:
            for unit in group.units:
                found["fi.kunta"][unit.code] = (unit.name, "")
                if page.kind == "K":
                    found["council"][unit.code] = (unit.name, "")
    found["fi.maakunta"][pages.KAINUU[0]] = (pages.KAINUU[1], "Kajanaland")
    return found


@functools.cache
def places_names() -> dict[str, list[str]]:
    """Every name that the pages give each municipality number, newest last."""
    names: dict[str, list[str]] = defaultdict(list)
    for page in pages.PAGES:
        for group in pages.load(page.folder).groups:
            for unit in group.units:
                if unit.name not in names[unit.code]:
                    names[unit.code].append(unit.name)
    return dict(names)


def cited() -> list[raw.Artifact]:
    out: dict[str, raw.Artifact] = {}
    for page in pages.PAGES:
        for artifact in pages.load(page.folder).artifacts:
            out[artifact.storage_key] = artifact
    for table in pages.STATFIN_TABLES:
        artifact = raw.cached(pages.SOURCE, pages.statfin_url(table))
        out[artifact.storage_key] = artifact
    return list(out.values())


# ---------------------------------------------------------------------------------------------------------------------
# Elections


def printed_name(name: str) -> str:
    """A candidate's name as the pages print it (`Surname, Given names`), in the order a person says it."""
    last, sep, first = name.partition(", ")
    return f"{first} {last}" if sep else name


def contest_key(page: Page, code: str) -> str:
    return code if page.kind in ("E", "K") else ("18" if page.kind == "M" else "0")


def election_key(page: Page) -> str:
    return vaalit.key("election", page.day, page.kind)


def contest_id(page: Page, ck: str) -> str:
    return vaalit.key("contest", page.day, page.kind, ck)


def election(day: date) -> Package:
    """One package for the elections held on a day."""
    held = [page for page in pages.PAGES if page.day == day]
    depends = [f"{vaalit.SOURCE}/geo", f"{vaalit.SOURCE}/registry"]
    for page in held:
        if page.round > 1:
            first = next(p for p in pages.PAGES if p.kind == page.kind and p.round == 1)
            depends.append(f"{vaalit.SOURCE}/{first.day}")
        if page.folder == "K2008U":
            depends.append(f"{vaalit.SOURCE}/{next(p for p in pages.PAGES if p.folder == 'K2008').day}")
    p = Package(
        f"{vaalit.SOURCE}/{day}",
        vaalit.SOURCE,
        "The "
        + " and the ".join(vaalit.TITLES[page.kind][0] for page in held)
        + f" of {day} by municipality from the Ministry of Justice's results service",
        depends=depends,
    )
    p.external.add(vaalit.FINLAND)
    units: dict[str, str] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    off = 0
    for page in held:
        data = pages.load(page.folder)
        for artifact in data.artifacts:
            p.uses(artifact)
        off += Build(p, data, units, values).run()
    p.frame("result_value", frame(values))
    if off:
        p.accept(
            "accounting.ballots_sum",
            f"the results service prints valid and invalid ballots that differ from the voters in {off} municipalities; "
            "stored as printed",
        )
    return p


def frame(values: list[tuple[str, str, str | None, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        values,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int64,
            "value": pl.Int64,
        },
        orient="row",
    ).select(
        "result_set_id",
        "unit_id",
        "option_id",
        channel=pl.lit(0),
        measure=pl.col("measure"),
        count_round=pl.lit(0),
        flags=pl.lit(0),
        value=pl.col("value"),
    )


class Build:
    def __init__(
        self, p: Package, data: Election, units: dict[str, str], values: list[tuple[str, str, str | None, int, int]]
    ) -> None:
        self.p = p
        self.data = data
        self.page = data.page
        self.units = units
        self.values = values
        self.mismatched = 0

    # -- helpers ------------------------------------------------------------------------------------------------

    def unit(self, unit: Unit) -> str:
        day = self.page.day
        if unit.code not in self.units:
            id = self.units[unit.code] = vaalit.key("unit", day, unit.code)
            self.p.add(
                "reporting_unit",
                id=id,
                polity_id=vaalit.FINLAND,
                scheme_code="fi.kuntalaskenta",
                code=f"{day}:{unit.code}",
                area_relation="equals",
                area_id=vaalit.area("fi.kunta", unit.code),
                kind="standard",
            )
            self.p.name(id, unit.name, "fi", "official")
        return self.units[unit.code]

    def value(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        self.values.append((result_set, unit, option, measure, value))

    def declare(self, result_set: str, option: str | None, measure: int, value: int | None) -> None:
        if value is not None:
            self.p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)

    # -- the election ---------------------------------------------------------------------------------------------

    def run(self) -> int:
        page = self.page
        kind, year = page.kind, page.day.year
        runoff = page.round > 1
        election = election_key(page)
        english, finnish, swedish = REPEAT_TITLES.get(page.folder, vaalit.TITLES[kind])
        suffix = (", second round", ", 2. kierros", ", andra omgången") if runoff else ("", "", "")
        if page.folder in REPEAT_TITLES:
            names = (english, finnish, swedish)
        else:
            names = (f"{year} {english}{suffix[0]}", f"{finnish} {year}{suffix[1]}", f"{swedish} {year}{suffix[2]}")
        self.p.add(
            "election",
            id=election,
            polity_id=vaalit.FINLAND,
            kind="runoff" if runoff else ("local" if kind in ("K", "M") else "general"),
            date_from=page.day,
            date_to=page.day,
            status="held",
            rerun_of_id=vaalit.key("election", date(2008, 10, 26), "K") if page.folder == "K2008U" else None,
        )
        self.p.name(election, names[0], "en", "official")
        self.p.name(election, names[1], "fi", "native", False)
        self.p.name(election, names[2], "sv", "native", False)
        self.path = f"fi/{page.day}-{vaalit.SLUGS[kind]}" + ("-runoff" if runoff else "")
        self.p.add("slug", path=self.path, entity_id=election, is_current=True)
        if page.folder == "K2008":
            self.p.add(
                "note",
                id=vaalit.key("note", election, "annulled"),
                entity_id=election,
                kind="caveat",
                body_md=(
                    "The elections in Karkkila, Kauniainen, and Vihti were annulled and held again on 6 September "
                    "2009. The results service's pages of the 2008 elections hold the repeat results for those three "
                    "municipalities, so they are stored with the repeat elections and not here."
                ),
                lang="en",
            )
        self.used: set[str] = set()
        self.english = english
        if kind == "E":
            for group in self.data.groups:
                if group.result is not None:
                    self.contest(group.name, group.result, group.candidates, group.turnout, group.units, group)
        elif kind == "K":
            for group in self.data.groups:
                for unit in group.units:
                    self.contest(unit.name, unit.result, unit.candidates, unit.turnout, [unit], None, unit.code)
        elif kind == "M":
            group = self.data.groups[0]
            assert group.result is not None
            self.contest(group.name, group.result, None, group.turnout, group.units, group)
        else:
            assert self.data.national is not None
            members = [unit for group in self.data.groups for unit in group.units]
            self.contest(
                f"{year} {english}", self.data.national, self.data.candidates, self.data.turnout, members, None
            )
        return self.mismatched

    # -- one contest ---------------------------------------------------------------------------------------------

    def contest(
        self,
        name: str,
        result: Result,
        candidates: list[Candidate] | None,
        turnout: Turnout | None,
        members: list[Unit],
        group: Group | None,
        code: str | None = None,
    ) -> None:
        page, p = self.page, self.p
        kind, year = page.kind, page.day.year
        runoff = page.round > 1
        if kind == "E":
            ck = pages.CONSTITUENCIES[name]
        elif kind == "K":
            assert code is not None
            ck = code
        else:
            ck = contest_key(page, "")
        contest = contest_id(page, ck)
        president = kind == "P"
        seats = sum(row.seats or 0 for row in result.rows)
        district = {
            "E": vaalit.area("fi.vaalipiiri", ck),
            "K": vaalit.area("fi.kunta", ck),
            "M": vaalit.area("fi.maakunta", ck),
        }.get(kind, vaalit.COUNTRY)
        body = {
            "E": vaalit.PARLIAMENT,
            "K": vaalit.council(ck),
            "M": vaalit.council(ck, "regional"),
        }.get(kind)
        system = vaalit.key("electoral_system", "aland") if kind == "E" and ck == "AHV" else vaalit.system_of_kind(kind)
        p.add(
            "contest",
            id=contest,
            election_id=election_key(page),
            kind="office" if president else "list_allocation",
            office_id=vaalit.PRESIDENCY if president else None,
            body_id=body,
            district_area_id=district,
            magnitude=1 if president else seats,
            round_number=page.round,
            previous_round_id=contest_id(next(x for x in pages.PAGES if x.kind == "P" and x.round == 1), ck)
            if runoff
            else None,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        if president:
            title = f"{year} {self.english}, {'second' if runoff else 'first'} round"
        elif kind == "EP":
            title = f"{year} {self.english}"
        else:
            title = name
        p.name(contest, title, "en" if kind in ("EP", "P") else "fi", "official")
        slug = f"{self.path}/{'president' if president else 'finland' if kind == 'EP' else vaalit.slug_of(title)}"
        if slug in self.used:
            slug = f"{slug}-{ck}"
        self.used.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        result_set = vaalit.key("result_set", page.day, kind, ck)
        superseded = kind == "E" and page.day in SUPERSEDED
        p.add(
            "result_set",
            id=result_set,
            election_id=election_key(page),
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=vaalit.VOLBY,
            status="superseded" if superseded else "active",
            is_default=not superseded,
            completeness=1.0,
        )
        p.add(
            "note",
            id=vaalit.key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD
            + (LIST_METHOD if candidates and not president else "")
            + (" " + NO_WINNER if president else " " + RESULT_ONLY)
            + (SUPERSEDED_NOTE if superseded else ""),
            lang="en",
        )
        # The contest's own counts
        cast = result.cast if result.cast is not None else (turnout.cast if turnout else None)
        self.declare(result_set, None, REGISTERED, turnout.registered if turnout else None)
        self.declare(result_set, None, CAST, cast)
        self.declare(result_set, None, VALID, result.valid)
        self.declare(result_set, None, INVALID, result.invalid)
        # Options
        options = self.options(result, candidates, ck, result_set, president, kind)
        for row in result.rows:
            option = options.lists[row.name]
            self.declare(result_set, option, VOTES_MEASURE, row.votes)
            if row.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
        for cand in candidates or []:
            self.declare(result_set, options.candidates[cand.number], PREFERENCE, cand.votes)
        # The units' counts
        for member in members:
            unit = self.unit(member)
            t = member.turnout
            m = member.result
            if t:
                self.value(result_set, unit, None, REGISTERED, t.registered)
            unit_cast = m.cast if m.cast is not None else (t.cast if t else None)
            if unit_cast is not None:
                self.value(result_set, unit, None, CAST, unit_cast)
            if m.valid is not None:
                self.value(result_set, unit, None, VALID, m.valid)
            if m.invalid is not None:
                self.value(result_set, unit, None, INVALID, m.invalid)
            if (
                m.valid is not None
                and m.invalid is not None
                and unit_cast is not None
                and m.valid + m.invalid != unit_cast
            ):
                self.mismatched += 1
            for row in m.rows:
                self.value(result_set, unit, options.lists[row.name], VOTES_MEASURE, row.votes)
            if kind == "E" or kind == "EP" or kind == "K":
                for cand in member.candidates or []:
                    self.value(result_set, unit, options.candidates[cand.number], PREFERENCE, cand.votes)

    # -- options --------------------------------------------------------------------------------------------------

    def options(
        self,
        result: Result,
        candidates: list[Candidate] | None,
        ck: str,
        result_set: str,
        president: bool,
        kind: str,
    ) -> Options:
        page, p = self.page, self.p
        contest = contest_id(page, ck)
        out = Options({}, {})
        seen: dict[str, int] = defaultdict(int)
        if president:
            people = {row.name: i for i, row in enumerate(result.rows, start=2)}
            for row in result.rows:
                number = people[row.name]
                tag = (page.day, kind, ck, number)
                person = vaalit.key("person", *tag)
                p.add("person", id=person, birth_precision=None, gender=None)
                label = printed_name(row.name)
                p.name(person, label, "fi", "official")
                option = vaalit.key("option", *tag, "candidate")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=None,
                    party_id=None,
                    ballot_position=None,
                    ballot_label=label,
                )
                p.add(
                    "candidacy",
                    id=vaalit.key("candidacy", *tag),
                    person_id=person,
                    option_id=option,
                    role="head",
                    list_position=None,
                    ballot_name=label,
                    member_of_party_id=None,
                )
                out.lists[row.name] = option
            return out
        for row in result.rows:
            seen[row.name] += 1
            tag = vaalit.key("option", page.day, kind, ck, "list", row.name, seen[row.name])
            out.lists[row.name] = tag
            party = vaalit.list_party(row.name)
            p.add(
                "contest_option",
                id=tag,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=None,
                ballot_label=row.name,
            )
        if not candidates:
            return out
        owner = assign(candidates, result.rows)
        for cand in candidates:
            tag = (page.day, kind, ck, cand.number if kind != "EP" else cand.name)
            person = vaalit.key("person", *tag)
            label = printed_name(cand.name)
            p.add("person", id=person, birth_precision=None, gender=None)
            p.name(person, label, "fi", "official")
            listed = owner.get(cand.number)
            parent = out.lists[listed] if listed else None
            party = vaalit.list_party(listed) if listed else None
            option = out.candidates[cand.number] = vaalit.key("option", *tag, "candidate")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=parent,
                party_id=party,
                ballot_position=cand.number if cand.party else None,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=vaalit.key("candidacy", *tag),
                person_id=person,
                option_id=parent or option,
                role="list_member" if parent else "head",
                list_position=cand.number if cand.party and parent else None,
                ballot_name=label,
                member_of_party_id=party,
            )
        return out


class Options:
    def __init__(self, lists: dict[str, str], candidates: dict[int, str]) -> None:
        self.lists = lists
        self.candidates = candidates


# ---------------------------------------------------------------------------------------------------------------------
# Placing candidates on lists


@functools.cache
def abbreviations() -> dict[str, str]:
    """The party id of each abbreviation that a registered party carried from 2011."""
    return {
        text: vaalit.party(permanent) for permanent, names in vaalit.parties().items() for _, text in names["fi_short"]
    }


def assign(candidates: list[Candidate], lists: list[Row]) -> dict[int, str]:
    """Place each candidate on the list whose votes equal the votes of the candidates it is placed on.

    Candidates are grouped by their party abbreviation, or, where the page prints none, by runs in alphabetical order,
    since each list's candidates are printed together in alphabetical order. A group goes to the list with its total
    votes; groups left over go to a list whose votes they add up to. A candidate whose group no list matches is left
    without a list."""
    groups: list[list[Candidate]] = []
    if candidates and candidates[0].party:
        by: dict[str, list[Candidate]] = {}
        for cand in candidates:
            by.setdefault(cand.party, []).append(cand)
        groups = list(by.values())
    else:
        for cand in candidates:
            if groups and cand.name.casefold() >= groups[-1][-1].name.casefold():
                groups[-1].append(cand)
            else:
                groups.append([cand])
    owner: dict[int, str] = {}
    free = list(range(len(groups)))
    open_lists = [row for row in lists if row.votes > 0]
    total = [sum(c.votes for c in group) for group in groups]
    # A group whose abbreviation is a party's and whose votes equal that party's list votes belongs to the list, even
    # where another list has the same votes
    for i, group in enumerate(groups):
        party = abbreviations().get(group[0].party)
        same = [row for row in open_lists if row.votes == total[i] and party and vaalit.list_party(row.name) == party]
        if len(same) == 1:
            for cand in group:
                owner[cand.number] = same[0].name
            free.remove(i)
            open_lists.remove(same[0])
    # First a group on its own, then runs of groups that sit next to each other, then any few groups
    for size in (1, 2, 3, 4):
        for row in list(open_lists):
            matches = [
                combo
                for combo in itertools.combinations(free, size)
                if sum(total[i] for i in combo) == row.votes
                and (size == 1 or all(b - a == 1 or groups[0][0].party for a, b in itertools.pairwise(combo)))
            ]
            if len(matches) != 1:
                continue
            for i in matches[0]:
                for cand in groups[i]:
                    owner[cand.number] = row.name
            free = [i for i in free if i not in matches[0]]
            open_lists.remove(row)
    return owner
