"""Czechoslovak National Assembly elections of 1920, 1925, 1929, and 1935 from the Czech Statistical Office.

The Czech Statistical Office (ČSÚ) publication "Volby do Národního shromáždění 1920 až 1935" republishes the State
Statistical Office's returns for the Chamber of Deputies and the Senate of the First Republic as one Excel workbook per
table. For each election and chamber it prints three tables:

- the valid votes and seats of each party nationally (Tab. 1.6, 1.9, 1.12, 1.15 for the Chamber; 2.6, 2.9, 2.12,
  2.15 for the Senate);
- the registered voters (Chamber only), valid votes, and seats of each electoral region (volební kraj), with the
  totals of the four lands and the whole state (Tab. 1.7, 1.10, 1.13, 1.16; 2.7, 2.10, 2.13, 2.16);
- the valid votes of each party in each electoral region, with the same land and state totals (Tab. 1.8, 1.11,
  1.14, 1.17; 2.8, 2.11, 2.14, 2.17).

The publication groups the parties into eleven named groups comparable with the parties of 1935, plus "other
parties", so a group can stand for several lists in an earlier year. The groups are stored as printed, and the
methodology note on each result set says how the publication formed them.

Seats were allocated in the electoral regions and then nationally in a second and third scrutiny, so the seats are
stored only for the state as a whole, as the publication prints them by party. Parsing checks every region's party
votes against its valid votes, every region against its land and the state totals, and the state totals against the
national tables and the cross-year summaries (Tab. 1.1, 1.2, 2.1, 2.2). A mismatch raises.

The elections are stored under Czechia, the census tier that holds the Czechoslovak National Assembly, and cover the
whole state, including Slovakia and Subcarpathian Ruthenia.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import xlrd

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "cz.csu"
CZECHIA = str(uuid5(package.NAMESPACE, "polity\x1fCZ"))
PRODUCT = "https://csu.gov.cz/produkty/volby-do-narodniho-shromazdeni-1920-az-1935-n-2hwwxuf57q"
OVERVIEW = "https://csu.gov.cz/docs/107508/ddabdcc0-6ee4-e3ca-f24b-857177e4db70/4219rrc_2prehl.pdf?version=1.0"
FILES = {
    "4219rr_1": "7dccbe50-3346-b2b1-adff-f1014541578d",
    "4219rr_2": "64e467db-344c-2ca7-a52a-ca260220cd3c",
    "4219rr_6": "e281b8fa-774a-2cb1-6494-5d59872415de",
    "4219rr_7": "5118f661-ffe0-720a-6e2d-09536c0fc6fc",
    "4219rr_8": "e4aaa644-2fcb-053b-6954-8a93eafaae44",
    "4219rr_9": "1d2aee9b-564f-0527-32c0-73b20ae651d8",
    "4219rr10": "289ec578-ae48-e922-b3dd-394fb8682cba",
    "4219rr11": "a620c830-2dbc-3b47-dea5-09d28a4de878",
    "4219rr12": "45de0f51-8db7-8ac1-1cc9-c809ec5c1b37",
    "4219rr13": "31604e92-2379-2b0d-c042-a7c60f3d2aa2",
    "4219rr14": "d3d2160d-d319-c8f3-2ee5-f1b78e1ef5e8",
    "4219rr15": "5b5ab8e1-fee0-9cb5-41e8-eb6eae367608",
    "4219rr16": "a4c86bbf-5740-04bf-676f-602ad8c6a9e9",
    "4219rr17": "01623fb5-0e14-6bfd-4e64-9d4f80e55fa8",
    "4219rr2_1": "65f387f8-8ce1-9679-4676-b57734001501",
    "4219rr2_2": "ac7aeaca-467d-ccfb-0886-f79291bf272e",
    "4219rr2_6": "bc2a2044-7f6a-e0aa-2d36-189696825e98",
    "4219rr2_7": "2a63672b-791d-b558-50ee-bd1519f81af4",
    "4219rr2_8": "48403a05-bf3d-a83e-02e3-ced99e2694d7",
    "4219rr2_9": "f52edcdf-bb7b-d9e4-b819-694c8e48dc44",
    "4219rr210": "dd1483e3-545a-9cd5-adc0-0bae02be00aa",
    "4219rr211": "fee59cfb-11d8-f344-cd47-7748545936b9",
    "4219rr212": "738ee5e2-655f-e5b0-6d60-f346f8d684f2",
    "4219rr213": "138d82e2-e05b-3a03-edaf-8d8b3d7fa2f3",
    "4219rr214": "1264da91-3df5-b32e-f554-7a497a29b97c",
    "4219rr215": "3e3ff68a-2ced-6014-220a-a5904561ed09",
    "4219rr216": "6e0fe97c-6745-7121-ad64-5068eae6370b",
    "4219rr217": "151c783c-3491-bf88-324f-696959f07c98",
}
VOTES, REGISTERED, VALID = 1, 2, 4
MISLABELED = {"4219rr2_8"}
"""Tables whose continuation page repeats the first page's party headings; the totals prove the usual order."""


@dataclass(frozen=True)
class Chamber:
    key: str
    body: str
    title: str
    native: str
    seats: int
    scheme: str
    noun: str
    regions: tuple[str, ...]
    lands: dict[str, tuple[str, ...]]
    tables: dict[int, tuple[str, str, str]]
    summary: tuple[str, str]


LANDS = ("Čechy", "Morava a Slezsko", "Slovensko", "Podkarpatská Rus")
LAND_CODES = {
    "Čechy": "cechy",
    "Morava a Slezsko": "morava-slezsko",
    "Slovensko": "slovensko",
    "Podkarpatská Rus": "pr",
}
CHAMBER = Chamber(
    key="ps",
    body="chamber",
    title="Chamber of Deputies",
    native="Poslanecká sněmovna",
    seats=300,
    scheme="cz.ns1920_ps",
    noun="Chamber of Deputies electoral region",
    regions=(
        "Praha",
        "Pardubice",
        "Hradec Králové",
        "Mladá Boleslav",
        "Česká Lípa",
        "Louny",
        "Karlovy Vary",
        "Plzeň",
        "České Budějovice",
        "Jihlava",
        "Brno",
        "Olomouc",
        "Uherské Hradiště",
        "Moravská Ostrava",
        "Trnava",
        "Nové Zámky",
        "Turčianský Sv.Martin",
        "Bánská Bystrica",
        "Liptovský Sv.Mikuláš",
        "Košice",
        "Prešov",
        "Užhorod",
        "Těšín",
    ),
    lands={
        "Čechy": (
            "Praha",
            "Pardubice",
            "Hradec Králové",
            "Mladá Boleslav",
            "Česká Lípa",
            "Louny",
            "Karlovy Vary",
            "Plzeň",
            "České Budějovice",
        ),
        "Morava a Slezsko": ("Jihlava", "Brno", "Olomouc", "Uherské Hradiště", "Moravská Ostrava", "Těšín"),
        "Slovensko": (
            "Trnava",
            "Nové Zámky",
            "Turčianský Sv.Martin",
            "Bánská Bystrica",
            "Liptovský Sv.Mikuláš",
            "Košice",
            "Prešov",
        ),
        "Podkarpatská Rus": ("Užhorod",),
    },
    tables={
        1920: ("4219rr_6", "4219rr_7", "4219rr_8"),
        1925: ("4219rr_9", "4219rr10", "4219rr11"),
        1929: ("4219rr12", "4219rr13", "4219rr14"),
        1935: ("4219rr15", "4219rr16", "4219rr17"),
    },
    summary=("4219rr_1", "4219rr_2"),
)
SENATE = Chamber(
    key="senat",
    body="senate",
    title="Senate",
    native="Senát",
    seats=150,
    scheme="cz.ns1920_senat",
    noun="Senate electoral region",
    regions=(
        "Praha",
        "Hradec Králové",
        "Mladá Boleslav",
        "Louny",
        "Plzeň",
        "Brno",
        "Moravská Ostrava",
        "Turčianský Sv.Martin",
        "Liptovský Sv.Mikuláš",
        "Prešov",
        "Nové Zámky",
        "Užhorod",
        "Těšín",
    ),
    lands={
        "Čechy": ("Praha", "Hradec Králové", "Mladá Boleslav", "Louny", "Plzeň"),
        "Morava a Slezsko": ("Brno", "Moravská Ostrava", "Těšín"),
        "Slovensko": ("Turčianský Sv.Martin", "Liptovský Sv.Mikuláš", "Prešov", "Nové Zámky"),
        "Podkarpatská Rus": ("Užhorod",),
    },
    tables={
        1920: ("4219rr2_6", "4219rr2_7", "4219rr2_8"),
        1925: ("4219rr2_9", "4219rr210", "4219rr211"),
        1929: ("4219rr212", "4219rr213", "4219rr214"),
        1935: ("4219rr215", "4219rr216", "4219rr217"),
    },
    summary=("4219rr2_1", "4219rr2_2"),
)
CHAMBERS = (CHAMBER, SENATE)
YEARS = (1920, 1925, 1929, 1935)
DAYS: dict[int, dict[str, date]] = {
    1920: {"ps": date(1920, 4, 18), "senat": date(1920, 4, 25)},
    1925: {"ps": date(1925, 11, 15), "senat": date(1925, 11, 15)},
    1929: {"ps": date(1929, 10, 27), "senat": date(1929, 10, 27)},
    1935: {"ps": date(1935, 5, 19), "senat": date(1935, 5, 19)},
}
OSTRAVA_MERGE = date(1925, 1, 1)
"""The Těšín region voted for the first time in 1925, as part of the Moravská Ostrava region."""
PLAN_FROM, PLAN_TO = date(1920, 2, 29), date(1939, 3, 15)


@dataclass(frozen=True)
class Group:
    code: str
    name: str
    label: str
    kind: str


GROUPS = (
    Group("agrarnici", "Republikánská strana zemědělského a malorolnického lidu", "Republik.str.zeměď.", "party"),
    Group("cssd", "Československá sociálně-demokratická strana dělnická", "Čs.sociál.demokr.", "party"),
    Group("csns", "Československá strana národně-socialistická", "Čs.strana národně", "party"),
    Group("csl", "Československá strana lidová", "Československá", "party"),
    Group("zivnostnici", "Československá živnostensko-obchodnická strana", "Čs. živnostensko", "party"),
    Group("autonomisticky-blok", "Autonomistický blok", "Autonomistický", "bloc"),
    Group("dsap", "Německá sociálně-demokratická strana dělnická", "Německá sociálně", "party"),
    Group("ksc", "Komunistická strana Československa", "Komunistická strana", "party"),
    Group("sdp", "Sudetoněmecká strana", "Sudetoněmecká", "party"),
    Group("narodni-sjednoceni", "Národní sjednocení", "Národního", "bloc"),
    Group(
        "krajinska-madarska",
        "Krajinská křesťansko-sociální strana a maďarská národní strana",
        "Krajinské křesťan.",
        "bloc",
    ),
    Group("ostatni", "Ostatní strany", "Ostatní", "others"),
)

METHOD = (
    "From the Czech Statistical Office's publication Volby do Národního shromáždění 1920 až 1935 (tables {tables}), "
    "which republishes the returns of the State Statistical Office. The publication groups the parties into eleven "
    "groups comparable with the parties that ran in 1935, plus other parties: Národní sjednocení stands for the "
    "Czechoslovak National Democratic Party and the parties that merged into it in 1935; the Autonomist Bloc for "
    "Hlinka's Slovak People's Party and its partners; the Provincial Christian Social Party and Hungarian National "
    "Party for the Hungarian and German parties of Slovakia that merged into them; and in 1920 the Czechoslovak "
    "National Socialist Party includes the Socialist Party of the Czechoslovak People. Other parties holds every "
    "other list. Valid votes are stored for each electoral region (volební kraj){registered}. Seats were allocated "
    "in the electoral regions and then nationally in further scrutinies, so they are stored for the state as a "
    "whole, as the publication prints them."
)
CAVEATS: dict[int, list[str]] = {
    1920: [
        (
            "No election was held in 1920 in Subcarpathian Ruthenia, which first voted in 1924, and the publication's "
            "totals leave out the Těšín region. The publication prints the seats of both regions within the state total, "
            "and the parties' seats sum to it."
        ),
        (
            "In Bohemia, Moravia, and Silesia, the votes of the Socialist Party of the Working People (Modráček's party) "
            "are taken out of the Czechoslovak National Socialist Party and counted with the other parties."
        ),
    ],
    1925: [
        (
            "The Hungarian national parties, which ran with the German parties of Slovakia and the German Agrarians in "
            "1925, are counted with the other parties, not with the Provincial Christian Social Party."
        ),
        (
            "In the Moravská Ostrava region, the votes of the Polish minority party (Związek Ludowo-Robotniczy), first "
            "counted with the Autonomist Bloc, are counted with the other parties."
        ),
    ],
}
GERMANS = (
    "Until 1929 the German parties that later joined the Sudeten German Party, such as the German Agrarians, German "
    "Christian Socials, and German Nationals, are counted with the other parties."
)
DATES_1920 = "The Chamber of Deputies voted on 18 April 1920 and the Senate on 25 April 1920."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def url(name: str) -> str:
    return f"https://csu.gov.cz/docs/107508/{FILES[name]}/{name}.xls?version=1.0"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/ns-{year}" for year in YEARS)]


def fetch() -> None:
    raw.fetch(SOURCE, PRODUCT, suffix=".html")
    raw.fetch(SOURCE, OVERVIEW, suffix=".pdf")
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=".xls")


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# Reading the workbooks


def _sheet(name: str) -> xlrd.sheet.Sheet:
    book = xlrd.open_workbook(str(raw.cached(SOURCE, url(name)).path))
    return book.sheet_by_name("A") if "A" in book.sheet_names() else book.sheet_by_index(0)


def _number(cell: object) -> int | None:
    if isinstance(cell, float):
        if cell != int(cell):
            raise ValueError(f"not a whole number: {cell}")
        return int(cell)
    text = str(cell).replace("\xa0", "").replace(" ", "").strip()
    if text in ("", "-"):
        return None
    return int(text)


def _label(cell: object) -> str:
    text = " ".join(str(cell).split())
    for prefix in ("v tom:", "v tom :"):
        text = text.removeprefix(prefix).strip()
    return "Celkem" if text.replace(" ", "") == "Celkem" else text


def _rows(name: str) -> Iterator[tuple[str, list[object]]]:
    sheet = _sheet(name)
    for r in range(sheet.nrows):
        yield _label(sheet.cell_value(r, 0)), sheet.row_values(r)


def _groups(sheet_rows: list[tuple[str, list[object]]]) -> list[list[Group]]:
    """The party groups of each band of a region-by-party table, in column order, read from its header rows."""
    bands: list[list[Group]] = []
    for label, values in sheet_rows:
        if label or not any(str(v).strip() for v in values):
            continue
        heads = [" ".join(str(values[c]).split()) for c in range(1, len(values), 2)]
        found = [next((g for g in GROUPS if g.label == h), None) for h in heads]
        if all(found) and found:
            bands.append([g for g in found if g is not None])
    return bands


@dataclass
class Result:
    regions: dict[str, dict[str, int]]
    """Each region's valid votes by group code, plus "valid", "registered", and "seats"."""
    lands: dict[str, dict[str, int]]
    national: dict[str, int]
    seats: dict[str, int]


def read(chamber: Chamber, year: int) -> Result:
    parties, regions_table, votes_table = chamber.tables[year]
    regions: dict[str, dict[str, int]] = {}
    lands: dict[str, dict[str, int]] = {}
    national: dict[str, int] = {}

    def put(label: str, field: str, value: int | None) -> None:
        if value is None:
            return
        if label in chamber.regions:
            target = regions.setdefault(label, {})
        elif label in LANDS:
            target = lands.setdefault(label, {})
        elif label == "Celkem":
            target = national
        else:
            return
        if field in target and target[field] != value:
            raise ValueError(f"{chamber.key} {year}: {label} {field} printed as {target[field]} and {value}")
        target[field] = value

    known = {*chamber.regions, *LANDS, "Celkem"}
    for label, values in _rows(regions_table):
        if label not in known:
            continue
        if chamber is CHAMBER:
            put(label, "registered", _number(values[1]))
            put(label, "valid", _number(values[2]))
            put(label, "seats", _number(values[4]))
        else:
            put(label, "valid", _number(values[1]))
            put(label, "seats", _number(values[3]))
    rows = list(_rows(votes_table))
    printed = [[g.code for g in band] for band in _groups(rows)]
    bands = [list(GROUPS[:6]), list(GROUPS[6:])]
    expected = [[g.code for g in band] for band in bands]
    if printed != expected and not (votes_table in MISLABELED and printed == [expected[0], expected[0]]):
        raise ValueError(f"{votes_table}: unexpected party columns {printed}")
    band = -1
    for label, values in rows:
        if label.startswith("Tab."):
            band += 1
        if label not in known:
            continue
        for index, group in enumerate(bands[max(band, 0)]):
            put(label, group.code, _number(values[1 + 2 * index]))
    seats: dict[str, int] = {}
    for group, values in _by_group(parties):
        if (n := _number(values[1])) is not None:
            put("Celkem", group.code, n)
        if (n := _number(values[3])) is not None:
            seats[group.code] = n
    for label, values in _rows(parties):
        if label == "Celkem":
            put("Celkem", "valid", _number(values[1]))
            put("Celkem", "seats", _number(values[3]))
    result = Result(regions, lands, national, seats)
    _check(chamber, year, result)
    return result


def _by_group(name: str) -> list[tuple[Group, list[object]]]:
    """The party rows of a national table, which print the twelve groups in the same order in every year."""
    rows = []
    for label, values in _rows(name):
        if label == "Celkem":
            break
        if label.startswith(("Tab.", "Politick", "VOLBY", "NÁROD", "Volby")) or len(values) < 2:
            continue
        if isinstance(values[1], float) or str(values[1]).strip() == "-":
            rows.append((label, values))
    if len(rows) != len(GROUPS) or "Komunist" not in rows[7][0] or "Ostatní" not in rows[11][0]:
        raise ValueError(f"{name}: unexpected party rows {[label for label, _ in rows]}")
    return [(group, values) for group, (_, values) in zip(GROUPS, rows, strict=True)]


def _check(chamber: Chamber, year: int, result: Result) -> None:
    where = f"{chamber.key} {year}"
    codes = [g.code for g in GROUPS]
    for name, row in result.regions.items():
        if "valid" not in row:
            if any(code in row for code in codes):
                raise ValueError(f"{where}: {name} has party votes but no valid votes")
            continue
        summed = sum(row.get(code, 0) for code in codes)
        if summed != row["valid"]:
            raise ValueError(f"{where}: {name} parties sum to {summed}, valid votes {row['valid']}")
    for field in [*codes, "valid", "registered", "seats"]:
        total = sum(row.get(field, 0) for row in result.regions.values())
        if total != result.national.get(field, 0):
            raise ValueError(
                f"{where}: regions sum {field} to {total}, the state total is {result.national.get(field)}"
            )
        for land, members in chamber.lands.items():
            printed = result.lands.get(land, {}).get(field, 0)
            summed = sum(result.regions.get(member, {}).get(field, 0) for member in members)
            if field == "seats" and land not in result.lands:
                continue
            if printed != summed:
                raise ValueError(f"{where}: {land} {field} printed {printed}, regions sum to {summed}")
    if sum(result.seats.values()) != chamber.seats or result.national.get("seats") != chamber.seats:
        raise ValueError(f"{where}: seats sum to {sum(result.seats.values())}, not {chamber.seats}")
    votes_summary, seats_summary = chamber.summary
    column = 1 + 2 * YEARS.index(year)
    for name, field in ((votes_summary, None), (seats_summary, "seats")):
        for group, values in _by_group(name):
            value = _number(values[column])
            expected = result.seats.get(group.code) if field else result.national.get(group.code)
            if value != expected:
                raise ValueError(f"{where}: {name} prints {group.code} as {value}, the year's tables give {expected}")
        for label, values in _rows(name):
            if label == "Celkem" and _number(values[column]) != result.national.get(field or "valid"):
                raise ValueError(f"{where}: {name} prints the total as {values[column]}")


# Packages


def area(chamber: Chamber, name: str, year: int | None = None) -> str:
    if name == "Moravská Ostrava":
        return key("area", chamber.scheme, name, "1920" if year == 1920 else "1925")
    return key("area", chamber.scheme, name)


def land(name: str) -> str:
    return key("area", "cz.ns1920_zeme", LAND_CODES[name])


STATE = key("area", "cz.ns1920_stat", "csr")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Czechoslovakia, its four lands, and the electoral regions of the Chamber of Deputies and the Senate of the "
        "First Republic, 1920 to 1935",
    )
    p.external.add(CZECHIA)
    p.add(
        "scheme",
        code="cz.ns1920_stat",
        polity_id=CZECHIA,
        kind="administrative",
        rank=0,
        noun="state",
        code_system=None,
    )
    p.add(
        "scheme",
        code="cz.ns1920_zeme",
        polity_id=CZECHIA,
        kind="administrative",
        rank=1,
        noun="land",
        code_system=None,
    )
    p.add("area", id=STATE, scheme_code="cz.ns1920_stat")
    p.add("area_code", area_id=STATE, code="csr", valid_from=date(1918, 10, 28), valid_to=PLAN_TO)
    p.name(STATE, "Československá republika", "cs", "official")
    p.name(STATE, "Czechoslovakia", "en", "official")
    for name in LANDS:
        id = land(name)
        p.add("area", id=id, scheme_code="cz.ns1920_zeme")
        p.add("area_code", area_id=id, code=LAND_CODES[name], valid_from=PLAN_FROM, valid_to=PLAN_TO)
        p.add("area_parent", area_id=id, parent_id=STATE, valid_from=PLAN_FROM, valid_to=PLAN_TO)
        p.name(id, name, "cs", "official")
    for chamber in CHAMBERS:
        p.add(
            "scheme",
            code=chamber.scheme,
            polity_id=CZECHIA,
            kind="electoral_district",
            rank=2,
            noun=chamber.noun,
            code_system=None,
        )
        parent = {member: name for name, members in chamber.lands.items() for member in members}
        for name in chamber.regions:
            spans: list[tuple[str, date, date, str]] = [(changeset.slug(name), PLAN_FROM, PLAN_TO, name)]
            if name == "Moravská Ostrava":
                spans = [
                    ("moravska-ostrava-1920", PLAN_FROM, OSTRAVA_MERGE, f"{name} (1920)"),
                    ("moravska-ostrava", OSTRAVA_MERGE, PLAN_TO, name),
                ]
            elif name == "Těšín":
                spans = [("tesin", PLAN_FROM, OSTRAVA_MERGE, name)]
            for code, start, end, text in spans:
                id = area(chamber, name, 1920 if start == PLAN_FROM and name == "Moravská Ostrava" else None)
                p.add("area", id=id, scheme_code=chamber.scheme)
                p.add("area_code", area_id=id, code=code, valid_from=start, valid_to=end)
                p.add("area_parent", area_id=id, parent_id=land(parent[name]), valid_from=start, valid_to=end)
                p.name(id, text, "cs", "official")
    return p


def source_id() -> str:
    return key("source", SOURCE)


def party(group: Group) -> str:
    return key("party", group.code)


def body(chamber: Chamber) -> str:
    return key("body", chamber.body)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Chamber of Deputies and Senate of the Czechoslovak National Assembly, and the party groups of the Czech "
        "Statistical Office's 1920 to 1935 tables",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(CZECHIA)
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher="Český statistický úřad",
        title="Volby do Národního shromáždění 1920 až 1935",
        url=PRODUCT,
        license="ČSÚ terms of use: free reuse with the Czech Statistical Office acknowledged as the source",
        license_url="https://csu.gov.cz/podminky_pro_vyuzivani_a_dalsi_zverejnovani_statistickych_udaju_csu",
        attribution="Český statistický úřad",
    )
    p.artifact(raw.cached(SOURCE, PRODUCT), source_id())
    p.artifact(raw.cached(SOURCE, OVERVIEW), source_id())
    for name in FILES:
        p.artifact(raw.cached(SOURCE, url(name)), source_id())
    for chamber in CHAMBERS:
        id = body(chamber)
        p.add("body", id=id, polity_id=CZECHIA, kind="lower_house" if chamber is CHAMBER else "upper_house")
        p.name(id, f"{chamber.title} of the Czechoslovak National Assembly", "en", "official")
        p.name(id, f"{chamber.native} Národního shromáždění", "cs", "native", False)
    for group in GROUPS:
        if group.kind == "others":
            continue
        id = party(group)
        p.add("party", id=id, kind=group.kind, polity_id=CZECHIA)
        p.add("external_id", entity_id=id, scheme="cz.csu.ns1920_group", value=group.code)
        p.name(id, group.name, "cs", "official")
    return p


def election(year: int) -> Package:
    p = Package(
        f"{SOURCE}/ns-{year}",
        SOURCE,
        f"The {year} Czechoslovak National Assembly election, Chamber of Deputies and Senate, by electoral region from "
        "the Czech Statistical Office's 1920 to 1935 tables",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(CZECHIA)
    days = DAYS[year]
    first, last = min(days.values()), max(days.values())
    election_id = key("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=CZECHIA,
        kind="general",
        date_from=first,
        date_to=last,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} Czechoslovak parliamentary election", "en", "official")
    p.name(election_id, f"Volby do Národního shromáždění {year}", "cs", "native", False)
    path = f"cz/{first}-national-assembly"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    for chamber in CHAMBERS:
        tables = chamber.tables[year]
        for name in (*tables, *chamber.summary):
            p.uses(raw.cached(SOURCE, url(name)))
        result = read(chamber, year)
        contest = key("contest", year, chamber.key)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(chamber),
            district_area_id=STATE,
            magnitude=chamber.seats,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {chamber.title}", "en", "official")
        p.name(contest, f"{chamber.native} {year}", "cs", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(chamber.title)}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, chamber.key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        numbers = ", ".join(f"{'1' if chamber is CHAMBER else '2'}.{int(t[-2:].lstrip('_'))}" for t in tables)
        method = METHOD.format(
            tables=numbers,
            registered=", with its registered voters"
            if chamber is CHAMBER
            else "; the Senate tables print no electorate",
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        caveats = [*CAVEATS.get(year, []), *([GERMANS] if year < 1935 else [])]
        if year == 1920:
            caveats.append(DATES_1920)
        for index, text in enumerate(caveats):
            p.add(
                "note",
                id=key("note", result_set, index),
                entity_id=result_set,
                kind="caveat",
                body_md=text,
                lang="en",
            )
        options: dict[str, str] = {}
        running = [g for g in GROUPS if result.national.get(g.code) or result.seats.get(g.code)]
        for position, group in enumerate(running, start=1):
            option = options[group.code] = key("option", year, chamber.key, group.code)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if group.kind == "others" else "list",
                party_id=None if group.kind == "others" else party(group),
                ballot_position=position,
                ballot_label=group.name,
            )
            seats = result.seats.get(group.code, 0)
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if seats else "lost",
                seats=seats,
            )
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=VOTES,
                value=result.national[group.code],
            )
        p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=result.national["valid"])
        if "registered" in result.national:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=None,
                measure=REGISTERED,
                value=result.national["registered"],
            )
        for name in chamber.regions:
            row = result.regions.get(name, {})
            if "valid" not in row:
                continue
            day = days[chamber.key]
            unit = key("unit", year, chamber.key, name)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CZECHIA,
                scheme_code=chamber.scheme,
                code=f"{day}:{changeset.slug(name)}",
                area_relation="equals",
                area_id=area(chamber, name, year),
                kind="standard",
            )
            p.name(unit, name, "cs", "official")
            for field, measure in (("registered", REGISTERED), ("valid", VALID)):
                if field in row:
                    _value(p, result_set, unit, None, measure, row[field])
            for group in running:
                if group.code in row:
                    _value(p, result_set, unit, options[group.code], VOTES, row[group.code])
    return p


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
