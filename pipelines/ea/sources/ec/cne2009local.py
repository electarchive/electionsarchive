"""Ecuador's mayors elected on 26 April 2009, from the National Electoral Council's 2009-2014 electoral atlas.

The atlas is a book of page images with no text layer. For each province it prints a table of the mayors elected in
2009, on the page before the province's 2014 table: the canton, the winning organization, the mayor, the winner's
votes, and the winner's share of the valid votes. ``atlas2009`` finds each ruled table and its votes cells, and every
cell was read by eye from the page. The atlas prints no other candidate's votes and no canton's blank or null
ballots, so each canton's contest holds the winner alone, as the 2014 contests do. The atlas also prints, for each
province, a table of every list's votes for prefect, with the valid total; those contests are built as the 2014 ones
are. The contests belong to the 2009 general election the archive already holds.
"""

from ea import package, raw
from ea.package import Package
from ea.sources.ec import atlas2009, cne, cne2014

SOURCE = cne.SOURCE
DAY = "2009-04-26"
ECUADOR = cne.ECUADOR
VOTES = cne.VOTES
DIGITIZED = cne2014.DIGITIZED
ATLAS_SOURCE = cne2014.ATLAS_SOURCE
CANTONS = cne2014.CANTONS
key = cne.key
area = cne.area

METHOD = (
    "Counted by the National Electoral Council and published in its 2009-2014 electoral atlas, which prints for each "
    "canton only the elected mayor, the winning organization, the winner's votes, and the winner's share of the valid "
    "votes. The other candidates' votes and the canton's blank and null ballots are not published there, so none is "
    "stored; the set's completeness is the winner's share. The atlas's pages are images, so each figure was read by "
    "eye from its page."
)


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}-mayors"]


def fetch() -> None:
    raw.fetch(SOURCE, atlas2009.ATLAS, suffix=".pdf")


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def build() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}-mayors",
        SOURCE,
        "Ecuador's mayors and prefects elected in the sectional elections of 2009, from the National Electoral Council's 2009-2014 "
        "electoral atlas",
    )
    election = key("election", DAY)
    # the country, the 2009 general election, the mayoralty, and the cantons are production's rows
    p.external.update([ECUADOR, election, "ec.canton", cne.MAYORALTY, cne.PREFECTURE])
    atlas = cne.artifact(atlas2009.ATLAS)
    p.add(
        "source",
        id=ATLAS_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral del Ecuador",
        title="Atlas Electoral del Ecuador 2009-2014",
        url=atlas2009.ATLAS,
        license="Official publication; reuse terms are not stated",
        attribution="Consejo Nacional Electoral del Ecuador",
    )
    artifact_id = p.artifact(atlas, ATLAS_SOURCE)
    base = f"ec/{DAY}-general"
    pages: dict[int, str] = {}
    prefects = atlas2009.prefects(2009)
    for page in [*atlas2009.MAYOR_PAGES_2009, *(t.page for t in prefects)]:
        width, height = atlas2009.page_image(page)
        pages[page] = key("page", atlas.sha256, page)
        p.add(
            "source_page",
            id=pages[page],
            artifact_id=artifact_id,
            page_number=page,
            image_key=atlas2009.image_key(page),
            width=width,
            height=height,
        )
    mayors = atlas2009.mayors2009()
    names: dict[str, int] = {}
    for mayor in mayors:
        names[mayor.canton] = names.get(mayor.canton, 0) + 1
    shared = {name for name, n in names.items() if n > 1}
    seen: set[str] = set()
    for mayor in mayors:
        if mayor.code in seen:
            raise ValueError(f"{DAY}: two mayors for canton {mayor.code}")
        seen.add(mayor.code)
        p.external.add(area("ec.canton", mayor.code))
        token = f"mayor/{mayor.code}"
        place = mayor.canton
        contest = key("contest", DAY, token)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            round_number=1,
            default_result_kind="counted",
            office_id=cne.MAYORALTY,
            magnitude=1,
            district_area_id=area("ec.canton", mayor.code),
        )
        p.name(contest, f"{place} mayor, 2009", "en", "official")
        p.name(contest, f"Alcalde de {place} 2009", "es", "native", False)
        slug = cne2014.slug(place, mayor.province, shared)
        p.add("slug", path=f"{base}/{slug}", entity_id=contest, is_current=True)
        share = float(mayor.share.removesuffix("%").replace(",", "."))
        result_set = key("result_set", DAY, token)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ATLAS_SOURCE,
            status="active",
            is_default=True,
            completeness=round(share / 100, 3),
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        unit = key("unit", DAY, "canton", mayor.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ECUADOR,
            scheme_code="ec.canton",
            code=f"{DAY}:canton:{mayor.code}",
            area_relation="equals",
            area_id=area("ec.canton", mayor.code),
            kind="standard",
        )
        p.name(unit, place, "es", "official")
        option = key("option", DAY, token, mayor.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=1,
            ballot_label=f"{mayor.name} ({mayor.sigla})",
        )
        person = key("person", DAY, token, mayor.name)
        p.add("person", id=person)
        p.name(person, mayor.name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", DAY, token, mayor.name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=mayor.name,
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=DIGITIZED,
            value=mayor.votes,
        )
        x0, y0, x1, y1 = mayor.box
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            page_id=pages[mayor.page],
            x0=round(x0, 4),
            y0=round(y0, 4),
            x1=round(x1, 4),
            y1=round(y1, 4),
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=mayor.votes)
    if len(seen) != CANTONS:
        raise ValueError(f"{DAY}: mayors of {len(seen)} cantons")
    if len(prefects) != cne2014.PREFECTURES:
        raise ValueError(f"{DAY}: prefect tables for {len(prefects)} provinces")
    for table in prefects:
        cne2014.prefect(p, election, base, pages[table.page], table, DAY, 2009)
    return p
