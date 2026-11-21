"""Ecuador's prefects and mayors elected on 23 February 2014, from the National Electoral Council's 2009-2014 atlas.

The atlas is a book of page images with no text layer. For each province it prints a table of the mayors elected in
2014: the canton, the winning organization, the mayor, the winner's votes, and the winner's share of the valid votes.
``atlas2009`` reads those tables with Tesseract, and every reading was checked by eye against the printed page. The
atlas prints no other candidate's votes and no canton's blank or null ballots, so each canton's contest holds the
winner alone, as the 2004 contests from the 2002-2007 atlas do.

For each province that elects a prefect, the atlas prints a table of the lists that ran: the list numbers (several when
parties ran together), the organizations, each list's votes, its share of the valid votes, and the total of valid votes.
It prints no candidate names, no blank or null ballots, and no figures by canton, so each prefect contest holds the lists'
votes for the whole province, and the list with the most votes is the winner. Galápagos has no prefect.
"""

from datetime import date

from ea import package, raw
from ea.package import Package
from ea.sources.ec import atlas2009, cne

SOURCE = cne.SOURCE
DAY = "2014-02-23"
ECUADOR = cne.ECUADOR
VOTES = cne.VOTES
DIGITIZED = 8
key = cne.key
area = cne.area
CANTONS = 221
PREFECTURES = 23
ATLAS_SOURCE = key("source", "atlas-2009-2014")

METHOD = (
    "Counted by the National Electoral Council and published in its 2009-2014 electoral atlas, which prints for each "
    "canton only the elected mayor, the winning organization, the winner's votes, and the winner's share of the valid "
    "votes. The other candidates' votes and the canton's blank and null ballots are not published there, so none is "
    "stored; the set's completeness is the winner's share. The atlas's pages are images, so the figures were read "
    "with OCR and each was checked by eye against the page."
)


METHOD_PREFECT = (
    "Counted by the National Electoral Council and published in its 2009-2014 electoral atlas, which prints each list's "
    "votes and its share of the valid votes for the whole province, and the total of valid votes. The atlas prints no "
    "candidate names, no figures by canton, and no blank or null ballots, so none is stored. Where parties ran together, "
    "the list is the alliance and carries every list number. The atlas's pages are images, so every figure was read by "
    "eye from the page, and each table's figures add up to the total it prints and give the shares it prints."
)


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def fetch() -> None:
    raw.fetch(SOURCE, atlas2009.ATLAS, suffix=".pdf")


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def slug(place: str, province: str, shared: set[str]) -> str:
    return f"{cne.slug(place)}-{cne.slug(province) + '-' if place in shared else ''}mayor"


def build() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "Ecuador's prefects and mayors elected in the sectional elections of 2014, from the National Electoral Council's 2009-2014 "
        "electoral atlas",
    )
    # the country, the mayoralty, and the cantons are production's rows
    p.external.update([ECUADOR, "ec.canton", "ec.provincia", cne.MAYORALTY, cne.PREFECTURE])
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
    atlas = cne.artifact(atlas2009.ATLAS)
    artifact_id = p.artifact(atlas, ATLAS_SOURCE)
    election = key("election", DAY)
    base = f"ec/{DAY}-local"
    p.add(
        "election",
        id=election,
        polity_id=ECUADOR,
        kind="local",
        date_from=date(2014, 2, 23),
        date_to=date(2014, 2, 23),
        status="held",
    )
    p.add("slug", path=base, entity_id=election, is_current=True)
    p.name(election, "2014 Ecuadorian sectional elections", "en", "official")
    p.name(election, "Elecciones seccionales del Ecuador 2014", "es", "native", False)
    pages: dict[int, str] = {}
    prefects = atlas2009.prefects()
    for page in [*atlas2009.MAYOR_PAGES, *(t.page for t in prefects)]:
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
    mayors = atlas2009.mayors()
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
        p.name(contest, f"{place} mayor, 2014", "en", "official")
        p.name(contest, f"Alcalde de {place} 2014", "es", "native", False)
        p.add("slug", path=f"{base}/{slug(place, mayor.province, shared)}", entity_id=contest, is_current=True)
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
    for table in prefects:
        prefect(p, election, base, pages[table.page], table)
    if len(prefects) != PREFECTURES:
        raise ValueError(f"{DAY}: prefect tables for {len(prefects)} provinces")
    return p


def prefect(
    p: Package, election: str, base: str, page: str, table: atlas2009.Prefect, day: str = DAY, year: int = 2014
) -> None:
    """One province's prefect contest: each list's votes, the valid total, and the list with the most votes elected."""
    code = table.province
    province = cne.PROVINCES[code]
    token = f"prefect/{code}"
    district = area("ec.provincia", code)
    p.external.add(district)
    contest = key("contest", day, token)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        round_number=1,
        default_result_kind="counted",
        office_id=cne.PREFECTURE,
        magnitude=1,
        district_area_id=district,
    )
    p.name(contest, f"{province} prefect, {year}", "en", "official")
    p.name(contest, f"Prefecto de {province} {year}", "es", "native", False)
    p.add("slug", path=f"{base}/{cne.slug(province)}-prefect", entity_id=contest, is_current=True)
    result_set = key("result_set", day, token)
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
        completeness=1.0,
    )
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD_PREFECT, lang="en"
    )
    unit = key("unit", day, "province", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ECUADOR,
        scheme_code="ec.provincia",
        code=f"{day}:province:{code}",
        area_relation="equals",
        area_id=district,
        kind="standard",
    )
    p.name(unit, province, "es", "official")
    top = max(range(len(table.lists)), key=lambda i: table.lists[i][2])
    if sum(row[2] == table.lists[top][2] for row in table.lists) != 1:
        raise ValueError(f"{day}: prefect of province {code} is a tie")
    for position, (number, sigla, votes, _share, box) in enumerate(table.lists, 1):
        option = key("option", day, token, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=f"{sigla} (list {number})",
        )
        locate(p, result_set, unit, option, VOTES, votes, page, box)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if position == top + 1 else "lost")
    locate(p, result_set, unit, None, cne.VALID, table.total, page, table.total_box)
    p.add("declared", result_set_id=result_set, option_id=None, measure=cne.VALID, value=table.total)


def locate(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    value: int,
    page: str,
    box: tuple[float, float, float, float],
) -> None:
    """A digitized value and the cell of the page it was read from."""
    cell = {
        "result_set_id": result_set,
        "unit_id": unit,
        "option_id": option,
        "channel": 0,
        "measure": measure,
        "count_round": 0,
    }
    p.add("result_value", **cell, flags=DIGITIZED, value=value)
    x0, y0, x1, y1 = box
    p.add("value_locator", **cell, page_id=page, x0=round(x0, 4), y0=round(y0, 4), x1=round(x1, 4), y1=round(y1, 4))
