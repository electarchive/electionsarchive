"""Ecuador's National Assembly elections of 2009 and 2013, from the National Electoral Council's 2009-2014 atlas.

The atlas is a book of page images with no text layer. For each election it prints the national contest's lists, and
for each province and each overseas circumscription a table of the lists that ran there: the list number, the
organization, its votes, its share of the valid votes, and the seats it won, closing on the total of valid votes and
seats. Every table was read by eye from the page, and each reading in ``atlas2009_assembly.json`` adds up to the
total the table prints, gives every list the share the table prints to one decimal, and gives the table's seats;
those checks run again at every build. ``atlas2009.list_boxes`` finds each votes cell on the page, so every figure
cites the cell it was read from.

The atlas prints no candidates, no blank or null ballots, and no figures by canton, so each contest holds the lists'
votes for its whole circumscription and the seats each list won. In 2013 Guayas, Manabí, and Pichincha elected their
members in several districts each, and the atlas prints only the province's sum of its districts, so those three
contests are left out of 2013.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.ec import atlas2009, cne, cne2014

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fECU"))
VOTES = cne.VOTES
DIGITIZED = cne2014.DIGITIZED
key = cne.key
area = cne.area
ATLAS_SOURCE = cne2014.ATLAS_SOURCE
ASSEMBLY = key("body", "national-assembly")
DAYS = {2009: "2009-04-26", 2013: "2013-02-17"}
SYSTEM_WEBSTER = key("electoral_system", "assembly-2009", "webster")
SYSTEM_DHONDT = key("electoral_system", "assembly-2013", "dhondt")
TABLES = Path(__file__).with_name("atlas2009_assembly.json")
REGISTRY = f"{SOURCE}/registry-2009"

PROVINCES = cne.PROVINCES
OVERSEAS = {
    "26": ("Europa, Asia y Oceanía", "Europe, Asia, and Oceania"),
    "27": ("Estados Unidos y Canadá", "the United States and Canada"),
    "28": ("América Latina, el Caribe y África", "Latin America, the Caribbean, and Africa"),
}
# provinces whose 2013 members were elected in several districts; the atlas prints only the province's sum
DISTRICTED = {(2013, "9"), (2013, "13"), (2013, "17")}
# the 2009 Loja table credits MPD (6.8%) with 2 seats and MPAIS (40.6%) with none, which no allocation of its
# figures gives; its seat column is left unread
MISPRINTED = {(2009, "11")}

METHOD = (
    "Counted by the National Electoral Council and published in its 2009-2014 electoral atlas, which prints each "
    "list's votes, its share of the valid votes, and the seats it won, for the whole circumscription. Each list's votes "
    "are the sum of its candidates' votes. The atlas prints no candidates, no figures by canton, and no blank or null "
    "ballots, so none is stored. The atlas's pages are images, so every figure was read by eye from the page, and each "
    "table's figures add up to the total it prints and give the shares it prints."
)
MISPRINT = (
    "The atlas's seat column for this contest credits MPD (list 15), with 6.8% of the valid votes, with 2 seats, and "
    "MPAIS (list 35), with 40.6%, with none. No allocation of the printed votes gives that, so the column appears to "
    "be misprinted, and no seats are stored for this contest's lists; its 4 seats are the contest's magnitude."
)


class Row(NamedTuple):
    number: str
    sigla: str
    votes: int
    share: str
    seats: int


class Table(NamedTuple):
    page: int
    year: int
    circumscription: str
    seats: int
    total: int
    rows: list[Row]
    boxes: list[tuple[float, float, float, float]]


def _int(text: str) -> int:
    return int(text.replace(".", ""))


def tables() -> list[Table]:
    """The atlas's list tables, each checked against the totals, shares, and seats it prints."""
    entries: list[dict[str, object]] = json.loads(TABLES.read_text(encoding="utf-8"))
    by_page: dict[int, int] = {}
    found: list[Table] = []
    for entry in entries:
        page = int(str(entry["page"]))
        lists = entry["lists"]
        if not isinstance(lists, list):
            raise TypeError(f"page {page}: lists is not a list")
        rows = [Row(str(n), str(s), _int(str(v)), str(sh), int(str(st))) for n, s, v, sh, st in lists]
        table = Table(
            page,
            int(str(entry["year"])),
            str(entry["circumscription"]),
            int(str(entry["seats"])),
            _int(str(entry["total"])),
            rows,
            [],
        )
        where = f"page {page}, {table.year} {table.circumscription}"
        if sum(r.votes for r in rows) != table.total:
            raise ValueError(f"{where}: the lists' votes do not add up to the printed total")
        if sum(r.seats for r in rows) != table.seats:
            raise ValueError(f"{where}: the lists' seats do not add up to the printed seats")
        for r in rows:
            share = (Decimal(r.votes) * 100 / Decimal(table.total)).quantize(Decimal("0.1"), ROUND_HALF_UP)
            if str(share).replace(".", ",") != r.share:
                raise ValueError(f"{where}: list {r.number} has {share}% of the votes, and the atlas prints {r.share}%")
        if len({r.number for r in rows}) != len(rows):
            raise ValueError(f"{where}: a list number appears twice")
        index = by_page.get(page, 0)
        by_page[page] = index + 1
        boxes = atlas2009.list_boxes(page)[index]
        if len(boxes) != len(rows):
            raise ValueError(f"{where}: {len(boxes)} votes cells on the page for {len(rows)} lists")
        found.append(table._replace(boxes=boxes))
    return found


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{day}-assembly" for day in DAYS.values())]


def fetch() -> None:
    raw.fetch(SOURCE, atlas2009.ATLAS, suffix=".pdf")


def parse() -> None:
    found = tables()
    registry().write()
    for year in DAYS:
        build(year, [t for t in found if t.year == year]).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The National Electoral Council's 2009-2014 electoral atlas, and the list systems that filled the National "
        "Assembly's seats in 2009 and 2013",
    )
    p.external.add(ECUADOR)
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
    p.artifact(cne.artifact(atlas2009.ATLAS), ATLAS_SOURCE)
    for system, formula, text in (
        (
            SYSTEM_WEBSTER,
            "sainte_lague",
            (
                "Under the Code of Democracy of 2009, voters marked candidates within or across lists, each list's "
                "votes are the sum of its candidates' votes, and seats went to lists by the Webster method, which "
                "divides list totals by the odd numbers. Within a list the most-voted candidates won the seats. This "
                "system filled every Assembly seat in 2009 and the national seats in 2013."
            ),
        ),
        (
            SYSTEM_DHONDT,
            "dhondt",
            (
                "After the reform of 2012, the provincial and overseas seats went to lists by the D'Hondt method, "
                "which divides list totals by the whole numbers. Voters marked candidates within or across lists, "
                "each list's votes are the sum of its candidates' votes, and within a list the most-voted candidates "
                "won the seats."
            ),
        ),
    ):
        p.add(
            "electoral_system",
            id=system,
            polity_id=ECUADOR,
            rules={"formula": formula, "lists": {"order": "open", "vacancy": "substitute"}},
            rules_version=1,
        )
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=text, lang="en")
    return p


def build(year: int, found: list[Table]) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}-assembly",
        SOURCE,
        f"Ecuador's {year} National Assembly election, by circumscription, from the National Electoral Council's "
        "2009-2014 electoral atlas",
        depends=[REGISTRY],
    )
    election = key("election", day)
    national_unit = key("unit", day, "national", "EC")
    # the country, the election, its national unit, the Assembly, and the provinces are production's rows
    p.external.update([ECUADOR, COUNTRY, election, national_unit, ASSEMBLY, "ec.provincia", "world.country"])
    atlas = cne.artifact(atlas2009.ATLAS)
    p.uses(atlas)
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{ATLAS_SOURCE}\x1f{atlas.storage_key}"))
    base = f"ec/{day}-general"
    found = [t for t in found if (year, t.circumscription) not in DISTRICTED]
    pages: dict[int, str] = {}
    for table in found:
        if table.page in pages:
            continue
        width, height = atlas2009.page_image(table.page)
        pages[table.page] = key("page", atlas.sha256, table.page)
        p.add(
            "source_page",
            id=pages[table.page],
            artifact_id=artifact_id,
            page_number=table.page,
            image_key=atlas2009.image_key(table.page),
            width=width,
            height=height,
        )
    for table in found:
        circ = table.circumscription
        if circ == "national":
            token = "national"
            english = f"{year} Ecuadorian National Assembly election, national seats"
            spanish = f"Asambleístas nacionales del Ecuador {year}"
            tail = "national-assembly"
            unit = national_unit
            district = COUNTRY
            system = SYSTEM_WEBSTER
        else:
            district = area("ec.provincia", circ)
            p.external.add(district)
            system = SYSTEM_WEBSTER if year == 2009 else SYSTEM_DHONDT
            unit = key("unit", day, "province", circ)
            if circ in OVERSEAS:
                native, where = OVERSEAS[circ]
                token = f"overseas/{circ}"
                english = f"{year} Ecuadorian National Assembly election, overseas seats for {where}"
                spanish = f"Asambleístas del exterior de {native} {year}"
                tail = f"overseas/{cne.slug(native)}"
            else:
                native = PROVINCES[circ]
                token = f"province/{circ}"
                english = f"{year} Ecuadorian National Assembly election, {native} provincial seats"
                spanish = f"Asambleístas provinciales de {native} {year}"
                tail = f"provincial/{cne.slug(native)}"
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ECUADOR,
                scheme_code="ec.provincia",
                code=f"{day}:province:{circ}",
                area_relation="equals",
                area_id=district,
                kind="standard",
            )
            p.name(unit, native, "es", "official")
        contest = key("contest", day, "assembly", token)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            round_number=1,
            default_result_kind="counted",
            body_id=ASSEMBLY,
            magnitude=table.seats,
            electoral_system_id=system,
            district_area_id=district,
        )
        p.name(contest, english, "en", "official")
        p.name(contest, spanish, "es", "native", False)
        p.add("slug", path=f"{base}/{tail}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, "assembly", token)
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
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        misprinted = (year, circ) in MISPRINTED
        if misprinted:
            p.add(
                "note",
                id=key("note", result_set, "seats"),
                entity_id=result_set,
                kind="caveat",
                body_md=MISPRINT,
                lang="en",
            )
        for position, (row, box) in enumerate(zip(table.rows, table.boxes, strict=True), 1):
            option = key("option", day, "assembly", token, row.number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                ballot_position=position,
                ballot_label=f"{row.sigla} (list {row.number})",
            )
            cell = {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": VOTES,
                "count_round": 0,
            }
            p.add("result_value", **cell, flags=DIGITIZED, value=row.votes)
            x0, y0, x1, y1 = box
            p.add(
                "value_locator",
                **cell,
                page_id=pages[table.page],
                x0=round(x0, 4),
                y0=round(y0, 4),
                x1=round(x1, 4),
                y1=round(y1, 4),
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=row.votes)
            if row.seats and not misprinted:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
    return p
