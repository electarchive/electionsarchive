"""Victorian Legislative Assembly by-elections and supplementary elections from the Victorian Electoral Commission (VEC).

The commission lists every by-election since 2007 on one timeline page. Each election has a district page, with the
candidates' first preference votes and the count after the distribution of preferences, and a page of first
preference votes by voting centre. The 2007 to 2017 pages are the same HTML pages the commission uses for its general
elections of those years, read by the general election parser in `vec`. The 2023 to 2026 pages use the current site's
layout and are read here. The Narracan District supplementary election's voting centre page prints no postal or
other declaration votes, so its figures come from the results spreadsheet the page links, which does. One package
holds each date's by-elections, and the registry and geo packages of `au.vec` carry the districts, parties, and files
they add.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from ea import changeset, raw
from ea.package import Package

from . import vec
from .vec import (
    ASSEMBLY,
    ASSEMBLY_METHOD,
    ASSEMBLY_SYSTEM,
    AUSTRALIA,
    DECLARATION,
    NO_PARTY,
    ROOT,
    SOURCE,
    VEC,
    Page,
    Row,
    Values,
    area,
    key,
    match,
    party,
    person_name,
    tokens,
)

TIMELINE = f"{ROOT}/results/state-election-results/state-by-elections-timeline"
OLD = "https://itsitecoreblobvecprd01.blob.core.windows.net/public-files/historical-results/others/stateby"
NARRACAN_SHEET = (
    "https://itsitecoreblobvecprd01.blob.core.windows.net/public-files/State/Reports/"
    "Narracan%20District-Results%20by%20Voting%20Centre.xls"
)


@dataclass(frozen=True)
class Bye:
    day: date
    district: str
    vintage: str  # the boundary plan the district belongs to
    page: str  # the district page
    sheet: str  # the page of first preferences by voting centre
    supplementary: bool = False
    spreadsheet: str | None = None  # a results spreadsheet, where the voting centre page is incomplete


def current(
    day: date,
    district: str,
    folder: str,
    page: str,
    sheet: str,
    supplementary: bool = False,
    spreadsheet: str | None = None,
) -> Bye:
    base = f"{TIMELINE}/{folder}"
    return Bye(day, district, "2021", f"{base}/{page}".rstrip("/"), f"{base}/{sheet}", supplementary, spreadsheet)


def _results(day: date, district: str, vintage: str, slug: str) -> Bye:
    """A 2007 to 2013 page named `stateby<year>result<district>district`."""
    squash = slug.replace("-", "")
    return Bye(
        day,
        district,
        vintage,
        f"{OLD}/{day.year}/stateby{day.year}result{squash}district.html",
        f"{OLD}/{day.year}/stateby{day.year}fpvbyvotingcentre{squash}district.html",
    )


def _bare(day: date, district: str, vintage: str, slug: str) -> Bye:
    """A 2015 or 2017 page named `<district>district`."""
    return Bye(
        day,
        district,
        vintage,
        f"{OLD}/{day.year}/{slug}district.html",
        f"{OLD}/{day.year}/fpvbyvotingcentre{slug}district.html",
    )


BYES = [
    _results(date(2007, 9, 15), "Albert Park", "2005", "albertpark"),
    _results(date(2007, 9, 15), "Williamstown", "2005", "williamstown"),
    _results(date(2008, 6, 28), "Kororoit", "2005", "kororoit"),
    _results(date(2010, 2, 13), "Altona", "2005", "altona"),
    _results(date(2011, 2, 19), "Broadmeadows", "2005", "broadmeadows"),
    _results(date(2012, 3, 24), "Niddrie", "2005", "niddrie"),
    _results(date(2012, 7, 21), "Melbourne", "2005", "melbourne"),
    _results(date(2013, 4, 27), "Lyndhurst", "2005", "lyndhurst"),
    _bare(date(2015, 3, 14), "Gippsland South", "2013", "gippslandsouth"),
    _bare(date(2015, 10, 31), "Polwarth", "2013", "polwarth"),
    _bare(date(2015, 10, 31), "South-West Coast", "2013", "south-westcoast"),
    _bare(date(2017, 11, 18), "Northcote", "2013", "northcote"),
    current(
        date(2023, 1, 28),
        "Narracan",
        "narracan-district-supplementary-election-results",
        "",
        "district-results-by-voting-centre",
        supplementary=True,
        spreadsheet=NARRACAN_SHEET,
    ),
    current(
        date(2023, 8, 26),
        "Warrandyte",
        "warrandyte-district-by-election-results/results-by-district",
        "warrandyte-district-results",
        "warrandyte-district-results/Warrandyte-District-results-by-voting-centre",
    ),
    current(
        date(2023, 11, 18),
        "Mulgrave",
        "mulgrave-district-by-election-results/results-by-district",
        "mulgrave-district-by-election-results",
        "mulgrave-district-by-election-results/Mulgrave-District-results-by-voting-centre",
    ),
    current(
        date(2025, 2, 8),
        "Prahran",
        "prahran-district-results/results-by-district",
        "prahran-district-results",
        "prahran-district-results/Prahran-District-results-by-voting-centre",
    ),
    current(
        date(2025, 2, 8),
        "Werribee",
        "werribee-district-results/results-by-district",
        "werribee-district-results",
        "werribee-district-results/Werribee-District-results-by-voting-centre",
    ),
    current(
        date(2026, 5, 2),
        "Nepean",
        "nepean-by-election-results/results-by-district",
        "nepean-district-results",
        "nepean-district-results/Nepean-District-results-by-voting-centre",
    ),
]


def days() -> list[date]:
    return sorted({b.day for b in BYES})


def on(day: date) -> list[Bye]:
    return [b for b in BYES if b.day == day]


def slug_of(b: Bye) -> str:
    return changeset.slug(b.district)


def urls(b: Bye) -> list[str]:
    return [b.page, b.sheet, *([b.spreadsheet] if b.spreadsheet else [])]


def files() -> list[str]:
    return [TIMELINE, *(u for b in BYES for u in urls(b))]


def fetch() -> None:
    raw.fetch(SOURCE, TIMELINE, suffix=".html")
    raw.fetch_many(SOURCE, [u for b in BYES for u in urls(b) if not u.endswith(".xls")], suffix=".html")
    raw.fetch_many(SOURCE, [b.spreadsheet for b in BYES if b.spreadsheet], suffix=".xls")


# Parsing ----------------------------------------------------------------------------------------------------------


def _read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8")


def _final(page: Page) -> Page:
    """The distribution table puts the party before the candidate on some pages; keep the candidate's name."""
    fixed = [(a if ", " in a else b, "", v) for a, b, v in page.final]
    return page._replace(final=fixed)


def _main_rows(label: str, page: Page, html: str) -> list[Row]:
    """First preferences by voting centre from a page in the layout the commission has used since 2022."""
    body = html[html.index("<main") :]
    count = len(page.first)
    heads: list[str] = []
    section = ""
    out: list[Row] = []
    for tr in re.findall(r"<tr[^>]*>.*?</tr>", body, flags=re.DOTALL):
        cells = [vec._text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.DOTALL)]
        if not heads:
            names = [c for c in cells if ", " in c]
            if len(names) == count:
                heads = names
            continue
        text, values = cells[0], cells[1:]
        if len(cells) != count + 3 or not text:
            continue
        if not any(values):
            section = text.lower()
            continue
        if text.startswith(("Total first preference", "Percentage")) or text in {
            "Ordinary votes total",
            "Declaration votes total",
        }:
            continue
        place = text
        state = "ordinary"
        if section == "early votes":
            if text != "Early votes total":
                raise ValueError(f"{label}: unexpected early votes row {text!r}")
            place, state = "Early Vote", "declaration"
        elif section == "declaration votes":
            place, state = DECLARATION.get(text.title(), text.title()), "declaration"
            if place not in vec.AGGREGATE and place != "Postal Vote":
                raise ValueError(f"{label}: unexpected declaration row {text!r}")
        elif section != "ordinary votes":
            raise ValueError(f"{label}: unexpected section {section!r}")
        numbers = [vec._number(v) for v in values]
        for order, ((who, party_name, _), votes) in enumerate(zip(page.first, numbers, strict=False), 1):
            out.append(Row(page.district, place, state, who, order, party_name, votes))
        out.append(Row(page.district, place, state, "Informal Votes", count + 1, "", numbers[-2]))
        out.append(Row(page.district, place, state, "Total Votes", count + 2, "", numbers[-1]))
    if not all(tokens(n) <= tokens(h) for (n, _, _), h in zip(page.first, heads, strict=True)):
        raise ValueError(f"{label}: the candidates on the voting centre page differ from the district page")
    if not out:
        raise ValueError(f"{label}: no voting centre rows")
    return out


def _check(label: str, page: Page, rows: list[Row]) -> None:
    """Each candidate's votes over the rows must equal the district page's first preferences."""
    by_name = {tokens(n): n for n, _, _ in page.first}
    sums: Counter[str] = Counter()
    for r in rows:
        if r.candidate not in ("Informal Votes", "Total Votes"):
            sums[match(r.candidate, by_name)] += r.votes
    declared = {n: v for n, _, v in page.first}
    if dict(sums) != declared:
        raise ValueError(f"{label}: the voting centre rows differ from the district page's first preferences")
    informal = sum(r.votes for r in rows if r.candidate == "Informal Votes")
    if informal != page.informal:
        raise ValueError(f"{label}: informal votes by voting centre differ from the district page")


def read(b: Bye) -> tuple[Page, list[Row]]:
    label = f"{b.day} {b.district}"
    if b.spreadsheet:
        _, enrolment, formal, informal, totals, rows = vec.parse_legacy_sheet(raw.cached(SOURCE, b.spreadsheet).path)
        page = vec.parse_main_page(label, b.district, _read(b.page))
        first = [(n, p, v) for n, p, v in totals if not n.startswith(("Informal", "Total"))]
        # The district page was last updated before the commission completed the count; the spreadsheet is the
        # complete count, so its figures replace the page's and the page gives the elected member.
        page = page._replace(enrolment=enrolment, formal=formal, informal=informal, first=first)
        page = _final(page)
        rows = [r._replace(district=b.district) for r in rows]
    elif "/stateby/" in b.page:
        page, rows = vec.parse_html_page(label, _read(b.page), _read(b.sheet))
        page = page._replace(district=b.district)
        rows = [r._replace(district=b.district) for r in rows]
    else:
        page = _final(vec.parse_main_page(label, b.district, _read(b.page)))
        rows = _main_rows(label, page, _read(b.sheet))
    _check(label, page, rows)
    if page.formal != sum(v for _, _, v in page.first):
        raise ValueError(f"{label}: first preferences do not add up to the formal votes")
    return page, rows


def parties() -> dict[str, str]:
    """The party names on the by-election candidate lists, by the registry's party codes."""
    names: dict[str, str] = {}
    for b in BYES:
        for r in read(b)[1]:
            if r.party not in NO_PARTY and r.candidate not in ("Total Votes", "Informal Votes"):
                code = vec.party_key(r.party)
                if code not in names or names[code].isupper():
                    names[code] = r.party
    return names


def districts() -> dict[tuple[str, str], str]:
    return {(b.vintage, b.district): b.district for b in BYES}


# Packages ---------------------------------------------------------------------------------------------------------

METHOD = (
    ASSEMBLY_METHOD.split(" The elected member")[0]
    + " The elected member and the candidates left after the distribution of preferences come from the district's "
    "results page; the later counts of the distribution are not stored here."
)
LATEST = (
    " The commission's district page for this election was last updated before the count was complete, and its voting "
    "centre page prints no postal or other declaration votes, so the votes here are the complete count in the "
    "results spreadsheet the page links, and the district page gives the elected member and the candidates left after "
    "the distribution of preferences. The page's totals (37,205 formal and 2,619 informal votes) differ from the "
    "spreadsheet's (37,503 formal and 2,323 informal votes)."
)


def election(day: date) -> Package:
    byes = on(day)
    names = " and ".join(f"{b.district} District" for b in byes)
    p = Package(
        f"{SOURCE}/by-{day}",
        SOURCE,
        f"The {day.year} {names} {'supplementary election' if byes[0].supplementary else 'by-election'} "
        "for the Victorian Legislative Assembly, first preferences by voting centre, from the Victorian Electoral "
        "Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(AUSTRALIA)
    p.uses(raw.cached(SOURCE, TIMELINE))
    values = Values()
    known = {id for id in (party(n) for n in parties().values()) if vec._known(id)}
    for b in byes:
        page, rows = read(b)
        for url in urls(b):
            p.uses(raw.cached(SOURCE, url))
        slug = slug_of(b)
        noun = "supplementary election" if b.supplementary else "by-election"
        election_id = key("election", day, "by", slug)
        path = f"au/vic/{day}-by-election-{slug}"
        p.add(
            "election",
            id=election_id,
            polity_id=AUSTRALIA,
            kind="by_election",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.name(election_id, f"{day.year} {b.district} District {noun}", "en", "official")
        p.add("slug", path=path, entity_id=election_id, is_current=True)
        district = area("au-vic.district", f"{b.vintage}:{b.district}")
        contest = key("contest", day, "by", slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=ASSEMBLY_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, b.district, "en", "official")
        p.add("slug", path=f"{path}/assembly-{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + (LATEST if b.spreadsheet else ""),
            lang="en",
        )
        parties_of: dict[str, str] = {}
        order: dict[str, int] = {}
        for r in rows:
            if r.candidate not in ("Total Votes", "Informal Votes"):
                parties_of[r.candidate] = r.party
                order[r.candidate] = r.order
        by_tokens = {tokens(c): c for c in order}
        listed = {n: match(n, by_tokens) for n, _, _ in page.first}
        options: dict[str, str] = {}
        for candidate in sorted(order, key=lambda c: order[c]):
            id = options[candidate] = key("option", day, slug, candidate)
            named = parties_of[candidate]
            pid = party(named) if named not in NO_PARTY else None
            pid = pid if pid in known else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                party_id=pid,
                ballot_position=order[candidate],
                ballot_label=candidate,
            )
            person = key("person", day, slug, candidate)
            p.add("person", id=person)
            p.name(person, person_name({c: n for n, c in listed.items()}[candidate]), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, slug, candidate),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=candidate,
                member_of_party_id=pid,
                is_independent=named in NO_PARTY,
            )
        winners = [c for n, c in listed.items() if tokens(n) <= tokens(page.elected)]
        if len(winners) != 1:
            raise ValueError(f"{day} {b.district}: elected member {page.elected!r} matches {winners}")
        winner = winners[0]
        final = {match(n, by_tokens) for n, _, _ in page.final} or set(order)
        if winner not in final:
            raise ValueError(f"{day} {b.district}: elected member is not among the final candidates")
        if (
            max(page.first, key=lambda c: c[2])[0] != next(n for n, c in listed.items() if c == winner)
            and not page.final
        ):
            raise ValueError(f"{day} {b.district}: the elected member does not lead the first preferences")
        for candidate, id in options.items():
            kind = "elected" if candidate == winner else "lost" if candidate in final else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=id, kind=kind)
        for n, _, votes in page.first:
            p.add("declared", result_set_id=result_set, option_id=options[listed[n]], measure=1, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=page.formal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=page.informal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=3, value=page.formal + page.informal)
        units: dict[str, str] = {}

        def unit_of(place: str, b: Bye = b, slug: str = slug, district: str = district) -> str:
            if place not in units:
                id = units[place] = key("unit", day, "by", slug, place)
                kind = (
                    "postal_district"
                    if place == "Postal Vote"
                    else "aggregate"
                    if place in vec.AGGREGATE
                    else "standard"
                )
                p.add(
                    "reporting_unit",
                    id=id,
                    polity_id=AUSTRALIA,
                    scheme_code="au-vic.voting_centre",
                    code=f"{day}:by:{b.district}:{place}",
                    area_relation="within",
                    area_id=district,
                    kind=kind,
                )
                p.name(id, f"{b.district}: {place}" if kind != "standard" else place, "en", "official")
            return units[place]

        valid: Counter[str] = Counter()
        bad: dict[str, int] = {}
        total: dict[str, int] = {}
        for r in rows:
            unit = unit_of(r.place)
            if r.candidate == "Informal Votes":
                bad[unit] = r.votes
            elif r.candidate == "Total Votes":
                total[unit] = r.votes
            else:
                values.add(result_set, unit, options[r.candidate], 1, r.votes)
                valid[unit] += r.votes
        for unit, n in valid.items():
            values.add(result_set, unit, None, 4, n)
            values.add(result_set, unit, None, 5, bad[unit])
            values.add(result_set, unit, None, 3, total[unit])
        roll = key("unit", day, "by", slug, "roll")
        p.add(
            "reporting_unit",
            id=roll,
            polity_id=AUSTRALIA,
            scheme_code="au-vic.voting_centre",
            code=f"{day}:by:{b.district}:ROLL",
            area_relation="within",
            area_id=district,
            kind="aggregate",
        )
        p.name(roll, f"{b.district}: electoral roll", "en", "official")
        values.add(result_set, roll, None, 2, page.enrolment)
    p.frame("result_value", values.frame())
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/by-{day}" for day in days()]
