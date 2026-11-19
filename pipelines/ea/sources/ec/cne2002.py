"""Ecuador's elections of 2002, 2004, and 2006, from the National Electoral Council's 2002-2007 electoral atlas.

The atlas prints the presidential first rounds and runoffs and the Andean Parliament contests nationally, and the
provincial councillor and prefect contests by province. The tables are read from its text layer by ``atlas2002``,
and each table is checked against the totals the atlas prints under it before it is stored.

Guayas and Pichincha were larger before November 2007, when Santa Elena and Santo Domingo de los Tsáchilas were made
provinces of their own, so their results here belong to the provinces as they stood, each outlined as the union of
today's provinces it then held.
"""

import json
from collections import Counter
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ec import atlas2002, cne, cne2017

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fECU"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = cne.VOTES, cne.REGISTERED, cne.CAST, cne.VALID, cne.INVALID, cne.BLANK
key = cne.key
area = cne.area
CNE_SOURCE = cne.CNE_SOURCE
ANDEAN = cne2017.ANDEAN
PROVINCIAL_COUNCIL = key("body", "provincial-council")
SYSTEM_DHONDT = key("electoral_system", "lists", "dhondt")
SHAPE_SOURCE = cne2017.SHAPE_SOURCE
ADM1 = cne2017.GBOPEN + "ECU/ADM1/geoBoundaries-ECU-ADM1.geojson"

DAYS = {
    "2002-10-20": date(2002, 10, 20),
    "2002-11-24": date(2002, 11, 24),
    "2004-10-17": date(2004, 10, 17),
    "2006-10-15": date(2006, 10, 15),
    "2006-11-26": date(2006, 11, 26),
}
# the atlas's province names and the Council's province codes
CODES = {
    "Azuay": "1",
    "Bolívar": "2",
    "Cañar": "3",
    "Carchi": "4",
    "Cotopaxi": "5",
    "Chimborazo": "6",
    "El Oro": "7",
    "Esmeraldas": "8",
    "Guayas": "9",
    "Imbabura": "10",
    "Loja": "11",
    "Los Ríos": "12",
    "Manabí": "13",
    "Morona Santiago": "14",
    "Napo": "15",
    "Pastaza": "16",
    "Pichincha": "17",
    "Tungurahua": "18",
    "Zamora Chinchipe": "19",
    "Galápagos": "20",
    "Sucumbíos": "21",
    "Orellana": "22",
}
# the provinces as they stood before November 2007: code, and the outlines of today's provinces each then held
FORMER = {
    "9": ("9-1998", ("Guayas", "Santa Elena")),
    "17": ("17-1998", ("Pichincha", "Santo Domingo de los Tsáchilas")),
}
# the atlas page of each table
PRESIDENT = {2002: 32, 2006: 46}
ANDEAN_PAGE = {2002: 63, 2006: 65}
COUNCILLORS = {2002: range(97, 108), 2004: range(108, 119), 2006: range(119, 130)}
PREFECTS = range(85, 96)


def province_area(code: str) -> str:
    former = FORMER.get(code)
    return area("ec.provincia", former[0]) if former else area("ec.provincia", code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo-2002", f"{SOURCE}/registry-2002", *(f"{SOURCE}/{day}" for day in DAYS)]


def fetch() -> None:
    raw.fetch(SOURCE, atlas2002.ATLAS, suffix=".pdf")
    raw.fetch(SOURCE, ADM1, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-2002",
        SOURCE,
        "Guayas and Pichincha as they stood before Santa Elena and Santo Domingo de los Tsáchilas became provinces "
        "in November 2007, with their outlines",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.update([ECUADOR, "ec.provincia"])
    p.add(
        "source",
        id=SHAPE_SOURCE,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen outlines of Ecuador's provinces and cantons and of the countries Ecuadorians vote in",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab); Ecuador's outlines are from the National Institute of "
        "Statistics and Censuses (INEC)",
    )
    shapes = cne.artifact(ADM1)
    p.artifact(shapes, SHAPE_SOURCE)
    p.artifact(cne.artifact(atlas2002.ATLAS), CNE_SOURCE)
    outlines = {
        book_norm(feature["properties"]["shapeName"]): shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    for code, (former, parts) in sorted(FORMER.items()):
        id = area("ec.provincia", former)
        name = next(n for n, c in CODES.items() if c == code)
        p.add("area", id=id, scheme_code="ec.provincia")
        p.add("area_code", area_id=id, code=former)
        p.name(id, name, "es", "official")
        p.name(id, f"{name} (before November 2007)", "en", "official", False)
        shape = shapely.union_all([outlines[book_norm(part)] for part in parts]).simplify(0.001)
        cne2017._geometry(p, id, shape)
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="methodology",
            body_md=(
                f"{name} as it stood from 1998 until November 2007, when {parts[1]} became a province of its own. "
                f"Its outline is the union of today's {parts[0]} and {parts[1]} in the geoBoundaries outlines, which "
                "follow later boundaries in a few places."
            ),
            lang="en",
        )
    return p


def book_norm(text: str) -> str:
    return atlas2002.book2017.norm(text)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry-2002",
        SOURCE,
        "Ecuador's provincial councils, its members of the Andean Parliament, and the D'Hondt list system that filled "
        "their seats from 2002 to 2006",
    )
    p.external.add(ECUADOR)
    p.add("body", id=PROVINCIAL_COUNCIL, polity_id=ECUADOR, kind="council")
    p.name(PROVINCIAL_COUNCIL, "Provincial councils of Ecuador", "en", "official")
    p.name(PROVINCIAL_COUNCIL, "Consejos provinciales del Ecuador", "es", "native", False)
    p.add("body", id=ANDEAN, polity_id=ECUADOR, kind="unicameral")
    p.name(ANDEAN, "Ecuador's members of the Andean Parliament", "en", "official")
    p.name(ANDEAN, "Parlamentarios andinos del Ecuador", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM_DHONDT,
        polity_id=ECUADOR,
        rules={"formula": "dhondt", "lists": {"order": "open", "vacancy": "substitute"}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_DHONDT),
        entity_id=SYSTEM_DHONDT,
        kind="methodology",
        body_md=(
            "Under the Elections Law of 2000, voters marked candidates within or across lists, and seats went to lists "
            "by the D'Hondt method of continuous divisors. Each list's votes are the sum of its candidates' votes, as "
            "the Council recounted them in its 2002-2007 atlas."
        ),
        lang="en",
    )
    return p


METHOD = (
    "Counted by the Supreme Electoral Tribunal and published by the National Electoral Council in its 2002-2007 "
    "electoral atlas, which prints the presidential and Andean Parliament results for the whole country and the "
    "provincial results for each province. Registered voters are the electors on the roll and ballots cast are the "
    "voters who took part, as the atlas's yearly summary gives them. Null and blank ballots are counted separately. A "
    "voter in a list contest marks several candidates, so no valid-ballot figure is stored for those contests; each "
    "list's votes are the sum of its candidates' votes, recounted by the Council to compare with later elections."
)


class Builder:
    def __init__(self, p: Package, day: str, base: str) -> None:
        self.p = p
        self.day = day
        self.base = base
        self.election = key("election", day)
        self.units: dict[str, str] = {}

    def national(self) -> str:
        if "EC" not in self.units:
            unit = self.units["EC"] = key("unit", self.day, "national", "EC")
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=ECUADOR,
                scheme_code="world.country",
                code=f"{self.day}:national:EC",
                area_relation="equals",
                area_id=COUNTRY,
                kind="standard",
            )
            self.p.name(unit, "Ecuador", "es", "official")
        return self.units["EC"]

    def province(self, name: str) -> str:
        code = CODES[name]
        if code not in self.units:
            unit = self.units[code] = key("unit", self.day, "province", code)
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=ECUADOR,
                scheme_code="ec.provincia",
                code=f"{self.day}:province:{code}",
                area_relation="equals",
                area_id=province_area(code),
                kind="standard",
            )
            self.p.name(unit, name, "es", "official")
        return self.units[code]

    def contest(self, token: str, english: str, spanish: str, tail: str, kind: str, **fields: object) -> str:
        contest = key("contest", self.day, token)
        self.p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            round_number=fields.pop("round_number", 1),
            default_result_kind="counted",
            **fields,
        )
        self.p.name(contest, english, "en", "official")
        self.p.name(contest, spanish, "es", "native", False)
        self.p.add("slug", path=f"{self.base}/{tail}", entity_id=contest, is_current=True)
        return contest

    def result_set(self, contest: str, token: str) -> str:
        return self.counted(contest, token, 1.0, METHOD)

    def counted(self, contest: str, token: str, completeness: float, method: str) -> str:
        result_set = key("result_set", self.day, token)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        self.p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=method,
            lang="en",
        )
        return result_set


def _declare(p: Package, result_set: str, options: list[str], table: atlas2002.Table | atlas2002.National) -> None:
    for option, row in zip(options, table.rows, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=row.votes)
    p.add("declared", result_set_id=result_set, option_id=None, measure=BLANK, value=table.blank)
    p.add("declared", result_set_id=result_set, option_id=None, measure=INVALID, value=table.null)


def president(b: Builder, year: int, runoff: bool) -> None:
    p = b.p
    table = atlas2002.president(PRESIDENT[year])[1 if runoff else 0]
    first = next(day for day in DAYS if day.startswith(str(year)))
    summary = atlas2002.summary(year)
    contest = b.contest(
        "president",
        f"{year} Ecuadorian presidential election, {'second' if runoff else 'first'} round",
        f"Elecciones presidenciales del Ecuador {year}, {'segunda' if runoff else 'primera'} vuelta",
        "president",
        "office",
        office_id=cne.PRESIDENCY,
        magnitude=1,
        round_number=2 if runoff else 1,
        previous_round_id=key("contest", first, "president") if runoff else None,
        electoral_system_id=cne.SYSTEM_PRESIDENT,
    )
    result_set = b.result_set(contest, "president")
    unit = b.national()
    options: list[str] = []
    for position, row in enumerate(table.rows, 1):
        option = key("option", b.day, "president", row.number)
        options.append(option)
        name = cne.title(row.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="ticket",
            ballot_position=position,
            ballot_label=f"{name} ({row.sigla}, list {row.number})",
        )
        person = key("person", f"president-{year}", row.name)
        if not runoff:
            p.add("person", id=person)
            p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", b.day, "president", row.number),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        cne._put(p, result_set, unit, option, VOTES, row.votes)
    if sum(row.votes for row in table.rows) != table.valid:
        raise ValueError(f"{year} president: votes do not add up to the printed valid total")
    cast = summary["cast2" if runoff else "cast"]
    cne._put(p, result_set, unit, None, VALID, table.valid)
    cne._put(p, result_set, unit, None, BLANK, table.blank)
    cne._put(p, result_set, unit, None, INVALID, table.null)
    cne._put(p, result_set, unit, None, CAST, cast)
    cne._put(p, result_set, unit, None, REGISTERED, summary["registered"])
    order = sorted(range(len(options)), key=lambda i: -table.rows[i].votes)
    for i, option in enumerate(options):
        if runoff:
            kind = "elected" if i == order[0] else "lost"
        else:
            kind = "advanced" if i in order[:2] else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    _declare(p, result_set, options, table)
    p.add("declared", result_set_id=result_set, option_id=None, measure=CAST, value=cast)
    p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=summary["registered"])
    gap = cast - table.valid - table.blank - table.null
    if gap:
        p.accept(
            "accounting.ballots_sum",
            f"the atlas's own national figures disagree: its summary counts {cast:,} voters who took part, and its "
            f"results table's valid, blank, and null votes add up to {cast - gap:,}; stored as published",
        )


def lists(
    b: Builder,
    table: atlas2002.Table | atlas2002.National,
    token: str,
    english: str,
    spanish: str,
    tail: str,
    body: str,
    unit: str,
    district: str | None,
    registered: int | None,
) -> None:
    """One list-allocation contest: its lists, the seats each won, and its votes."""
    p = b.p
    seats = sum(row.seats or 0 for row in table.rows)
    if seats != table.seats or sum(row.votes for row in table.rows) != table.valid:
        raise ValueError(f"{token}: lists do not add up to the printed totals")
    contest = b.contest(
        token,
        english,
        spanish,
        tail,
        "list_allocation",
        body_id=body,
        magnitude=seats,
        electoral_system_id=SYSTEM_DHONDT,
        district_area_id=district,
    )
    result_set = b.result_set(contest, token)
    options: list[str] = []
    numbers = Counter(row.number for row in table.rows)
    for position, row in enumerate(table.rows, 1):
        # a list number can appear twice in one province when two local movements drew the same number
        tag = row.number if numbers[row.number] == 1 else f"{row.number}/{row.sigla}"
        option = key("option", b.day, token, tag)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=f"{row.sigla} (list {row.number})",
        )
        cne._put(p, result_set, unit, option, VOTES, row.votes)
        if row.seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
    cne._put(p, result_set, unit, None, BLANK, table.blank)
    cne._put(p, result_set, unit, None, INVALID, table.null)
    if registered is not None:
        cne._put(p, result_set, unit, None, REGISTERED, registered)
    if isinstance(table, atlas2002.National):
        by_number = {row.number: option for row, option in zip(table.rows, options, strict=True)}
        for number, name, sex in table.elected:
            person_name = cne.title(name)
            person = key("person", b.day, token, name)
            p.add("person", id=person, gender={"HOMBRES": "male", "MUJERES": "female"}[sex])
            p.name(person, person_name, "es", "official")
            candidacy = key("candidacy", b.day, token, name)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=by_number[number],
                role="list_member",
                ballot_name=person_name,
            )
            p.add(
                "outcome", result_set_id=result_set, option_id=by_number[number], candidacy_id=candidacy, kind="elected"
            )
    _declare(p, result_set, options, table)


def councillors(b: Builder, year: int) -> None:
    summary = atlas2002.summary(year)
    seen: set[str] = set()
    for page in COUNCILLORS[year]:
        for table in atlas2002.province_tables(page, False):
            if table.province in seen:
                raise ValueError(f"{year}: two councillor tables for {table.province}")
            seen.add(table.province)
            code = CODES[table.province]
            lists(
                b,
                table,
                f"councillors/{code}",
                f"{year} Ecuadorian provincial councillor election, {table.province}",
                f"Consejeros provinciales de {table.province} {year}",
                f"provincial-council/{cne.slug(table.province)}",
                PROVINCIAL_COUNCIL,
                b.province(table.province),
                province_area(code),
                summary[table.province],
            )
    if len(seen) != len(CODES):
        raise ValueError(f"{year}: councillor tables for {len(seen)} provinces")


def prefects(b: Builder) -> None:
    p = b.p
    summary = atlas2002.summary(2004)
    seen: set[str] = set()
    for page in PREFECTS:
        for table in atlas2002.province_tables(page, True):
            seen.add(table.province)
            code = CODES[table.province]
            if sum(row.votes for row in table.rows) != table.valid:
                raise ValueError(f"2004 prefect of {table.province}: votes do not add up to the printed total")
            contest = b.contest(
                f"prefect/{code}",
                f"2004 Ecuadorian prefect election, {table.province}",
                f"Prefecto provincial de {table.province} 2004",
                f"prefect/{cne.slug(table.province)}",
                "office",
                office_id=cne.PREFECTURE,
                magnitude=1,
                electoral_system_id=cne.SYSTEM_PLURALITY,
                district_area_id=province_area(code),
            )
            result_set = b.result_set(contest, f"prefect/{code}")
            unit = b.province(table.province)
            options: list[str] = []
            for position, row in enumerate(table.rows, 1):
                option = key("option", b.day, "prefect", code, row.number)
                options.append(option)
                name = cne.title(row.name)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=position,
                    ballot_label=f"{name} ({row.sigla}, list {row.number})",
                )
                person = key("person", b.day, "prefect", code, row.name)
                p.add("person", id=person)
                p.name(person, name, "es", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", b.day, "prefect", code, row.number),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                )
                cne._put(p, result_set, unit, option, VOTES, row.votes)
            cne._put(p, result_set, unit, None, VALID, table.valid)
            cne._put(p, result_set, unit, None, BLANK, table.blank)
            cne._put(p, result_set, unit, None, INVALID, table.null)
            cne._put(p, result_set, unit, None, REGISTERED, summary[table.province])
            top = max(range(len(options)), key=lambda i: table.rows[i].votes)
            for i, option in enumerate(options):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if i == top else "lost")
            _declare(p, result_set, options, table)
    if len(seen) != len(CODES):
        raise ValueError(f"2004: prefect tables for {len(seen)} provinces")


MAYOR_METHOD = (
    "Counted by the Supreme Electoral Tribunal and published by the National Electoral Council in its 2002-2007 "
    "electoral atlas, which prints for each canton only the elected mayor, the winner's votes, and the winner's share "
    "of the valid votes. The other candidates' votes and the canton's blank and null ballots are not published there, "
    "so none is stored; the set's completeness is the winner's share. Cantons are the areas of the later boundary "
    "files, which differ slightly from the 2004 limits where parishes have moved."
)
# the atlas's spellings that the Council's later canton names do not match, by their codes
CANTON_ALIAS = {"STA.ISABEL": "285", "OLMEDO/PUCA": "970", "JOYA DE LOS SAGCHAS": "725"}


def mayors(b: Builder) -> None:
    """One contest per canton, holding only the elected mayor, as far as the atlas prints."""
    p = b.p
    _, cantons, _ = cne.places((cne.LOCAL_2019,))
    index: dict[str, list[str]] = {}
    for code, (name, _province) in cantons.items():
        index.setdefault(book_norm(name), []).append(code)
    shared = {name for name, codes in index.items() if len(codes) > 1}
    seen: set[str] = set()
    for mayor in atlas2002.mayors():
        if mayor.canton in CANTON_ALIAS:
            code = CANTON_ALIAS[mayor.canton]
        else:
            found = index[book_norm(mayor.canton)]
            if len(found) > 1:
                found = [c for c in found if cantons[c][1] == CODES[mayor.province]]
            if len(found) != 1:
                raise ValueError(f"2004 mayor of {mayor.canton}, {mayor.province}: {found}")
            code = found[0]
        if code in seen:
            raise ValueError(f"2004: two mayors for canton {code}")
        seen.add(code)
        place = cne.title(cantons[code][0])
        token = f"mayor/{code}"
        contest = b.contest(
            token,
            f"{place} mayor, 2004",
            f"Alcalde de {place} 2004",
            f"{cne.slug(place)}-{cne.slug(mayor.province) + '-' if book_norm(place) in shared else ''}mayor",
            "office",
            office_id=cne.MAYORALTY,
            magnitude=1,
            electoral_system_id=cne.SYSTEM_PLURALITY,
            district_area_id=area("ec.canton", code),
        )
        share = float(mayor.share.removesuffix("%").replace(",", "."))
        result_set = b.counted(contest, token, round(share / 100, 3), MAYOR_METHOD)
        unit = key("unit", b.day, "canton", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ECUADOR,
            scheme_code="ec.canton",
            code=f"{b.day}:canton:{code}",
            area_relation="equals",
            area_id=area("ec.canton", code),
            kind="standard",
        )
        p.name(unit, place, "es", "official")
        name = cne.title(mayor.name)
        option = key("option", b.day, token, mayor.number, mayor.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=1,
            ballot_label=f"{name} ({mayor.sigla}, list {mayor.number})",
        )
        person = key("person", b.day, token, mayor.name)
        p.add("person", id=person)
        p.name(person, name, "es", "official")
        p.add(
            "candidacy",
            id=key("candidacy", b.day, token, mayor.number, mayor.name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        cne._put(p, result_set, unit, option, VOTES, mayor.votes)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=mayor.votes)
    if len(seen) != 219:
        raise ValueError(f"2004: mayors of {len(seen)} cantons")


def andean(b: Builder, year: int) -> None:
    table = atlas2002.andean(ANDEAN_PAGE[year])
    lists(
        b,
        table,
        "andean",
        f"{year} Ecuadorian election of members of the Andean Parliament",
        f"Parlamentarios andinos del Ecuador {year}",
        "andean-parliament",
        ANDEAN,
        b.national(),
        None,
        None,
    )


ELECTIONS = {
    "2002-10-20": ("general", "2002 Ecuadorian general election", "Elecciones generales del Ecuador 2002", "general"),
    "2002-11-24": (
        "runoff",
        "2002 Ecuadorian presidential election, second round",
        "Elecciones presidenciales del Ecuador 2002, segunda vuelta",
        "presidential-runoff",
    ),
    "2004-10-17": ("local", "2004 Ecuadorian sectional elections", "Elecciones seccionales del Ecuador 2004", "local"),
    "2006-10-15": ("general", "2006 Ecuadorian general election", "Elecciones generales del Ecuador 2006", "general"),
    "2006-11-26": (
        "runoff",
        "2006 Ecuadorian presidential election, second round",
        "Elecciones presidenciales del Ecuador 2006, segunda vuelta",
        "presidential-runoff",
    ),
}


def build(day: str) -> Package:
    kind, english, spanish, tail = ELECTIONS[day]
    year = DAYS[day].year
    first = next(d for d in DAYS if d.startswith(str(year)))
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Ecuador's {english[5:].replace('Ecuadorian ', '')} of {year}, from the National Electoral Council's "
        "2002-2007 electoral atlas",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/geo-2002",
            f"{SOURCE}/registry",
            f"{SOURCE}/registry-2002",
            *([f"{SOURCE}/{first}"] if kind == "runoff" else []),
        ],
    )
    p.external.update([ECUADOR, COUNTRY, "world.country", "ec.provincia", "ec.canton"])
    p.uses(cne.artifact(atlas2002.ATLAS))
    base = f"ec/{day}-{tail}"
    b = Builder(p, day, base)
    p.add(
        "election", id=b.election, polity_id=ECUADOR, kind=kind, date_from=DAYS[day], date_to=DAYS[day], status="held"
    )
    p.add("slug", path=base, entity_id=b.election, is_current=True)
    p.name(b.election, english, "en", "official")
    p.name(b.election, spanish, "es", "native", False)
    if kind == "runoff":
        president(b, year, True)
    elif kind == "general":
        president(b, year, False)
        andean(b, year)
        councillors(b, year)
    else:
        prefects(b)
        mayors(b)
        councillors(b, year)
    return p
