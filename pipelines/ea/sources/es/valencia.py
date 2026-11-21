"""Corts Valencianes elections, 1983 to 2023, by province and municipality from the Archivo Histórico Electoral.

The Generalitat's Archivo Histórico Electoral (ARGOS) publishes, for every election since 1977, a detail page for each
province and each municipality: the electorate, voters, valid ballots, votes to candidacies, blank ballots, and the
votes of every list, with the seats (diputados) won by each list in the province pages. The Institut Cartogràfic
Valencià's open map layer of the autonomic elections (CC BY 4.0 per the Generalitat's open-data catalog) carries only
rounded shares of a few lists, so it is fetched to list the municipalities and is not otherwise loaded.
"""

import html
import itertools
import re
from collections import Counter
from datetime import date
from functools import cache
from uuid import uuid5

import duckdb
import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "es.valencia"
ARGOS = "http://www.argos.gva.es/ahe/pls/argos_elec/"
LAYER = (
    "http://terramapas.icv.gva.es/2001_EleccionesAutonomicas"
    "?request=GetFeature&service=WFS&version=2.0.0&typename=Autonomicas.WFS&outputformat=csv"
)
CATALOG = "https://dadesobertes.gva.es/en/dataset/e227e3d6-5d37-4f0c-bf40-f67f775515c9"
DAYS = {
    1983: date(1983, 5, 8),
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 4, 28),
    2023: date(2023, 5, 28),
}
PROVINCES = {"03": (3, "Alicante", "Alacant"), "12": (12, "Castellón", "Castelló"), "46": (46, "Valencia", "València")}


def province_url(province: int, year: int) -> str:
    return (
        f"{ARGOS}DMEDB_ElecProvincias.informeElecDetallado?aNProvid={province}&aNNumElec=1&aVTipoElec=A17"
        f"&aVFechaElec={year}&aVLengua=C"
    )


def town_url(code: str, year: int) -> str:
    return (
        f"{ARGOS}DMEDB_Elecmunicipios.informeElecDetallado?aNMuniId={int(code)}&aNNumElec=1&aVTipoElec=A17"
        f"&aVFechaElec={year}&aVLengua=C"
    )


def community_url(year: int) -> str:
    return (
        f"{ARGOS}DMEDB_ElecComunidades.informeElecDetallado?aNComuid=17&aNNumElec=1&aVTipoElec=A17"
        f"&aVFechaElec={year}&aVLengua=C"
    )


def town_codes() -> list[str]:
    import csv

    csv.field_size_limit(10**9)
    text = raw.cached(SOURCE, LAYER).path.read_text(encoding="utf-8")
    return sorted(r["cod_ine"] for r in csv.DictReader(text.splitlines(keepends=True)) if r["cod_ine"])


def fetch() -> None:
    raw.fetch(SOURCE, LAYER, suffix=".csv")
    raw.fetch(SOURCE, CATALOG, suffix=".html")
    urls = [province_url(p, y) for p, *_ in PROVINCES.values() for y in DAYS]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=4)
    raw.fetch_many(SOURCE, [community_url(y) for y in DAYS], suffix=".html", workers=2)
    urls = [town_url(c, y) for c in town_codes() for y in DAYS]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=6)


# ---------------------------------------------------------------------------------------------------------------
# Reading the pages

THRESHOLD = 5
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
GENERAL = {"Censo": "registered", "Votantes": "voters", "Válidos": "valid", "A candidatura": "cand", "Blancos": "blank"}


def number(text: str) -> int | None:
    text = text.replace(".", "").strip()
    return int(text) if text else None


class Page:
    """One ARGOS detail page: the general figures and each list's name, abbreviation, votes, and (provinces) seats."""

    def __init__(self, general: dict[str, int], lists: list[tuple[str, str, int | None, int | None]]) -> None:
        self.general, self.lists = general, lists

    @property
    def standing(self) -> bool:
        return "registered" in self.general


def read_page(url: str) -> Page:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
    general: dict[str, int] = {}
    lists: list[tuple[str, str, int | None, int | None]] = []
    in_lists = False
    for row in re.findall(r"<tr.*?</tr>", text, flags=re.DOTALL | re.IGNORECASE):
        cells = [
            html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
        ]
        if cells[:1] == ["Candidatura"]:
            in_lists = True
            continue
        if in_lists and len(cells) >= 4:
            lists.append((cells[0], cells[1], number(cells[2]), number(cells[4]) if len(cells) > 4 else None))
        elif not in_lists:
            for label, cell in itertools.pairwise(cells):
                if label in GENERAL and (value := number(cell)) is not None:
                    general[GENERAL[label]] = value
    return Page(general, lists)


def party_key(abbreviation: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", abbreviation.lower().replace("í", "i").replace("à", "a").replace("ò", "o")).strip(
        "-"
    )


SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-VC"))
PUBLISHER = "Generalitat Valenciana, Presidencia: Archivo Histórico Electoral (ARGOS)"
TITLE = "Archivo Histórico Electoral: elecciones a las Cortes Valencianas por provincia y municipio"
PORTAL = "http://www.argos.gva.es/ahe/index.html"
STATUTE = "Ley Orgánica 5/1982, de 1 de julio, de Estatuto de Autonomía de la Comunitat Valenciana"
SYSTEM = (
    "Closed party lists in three provincial constituencies, Alicante, Castellón, and Valencia. Seats go by the "
    "D'Hondt method within each province to the lists that won at least 5% of the valid votes cast in the whole "
    "Community, blank ballots counted as valid. The Statute (" + STATUTE + ") and the Valencian electoral law set the "
    "threshold and the number of seats."
)
METHOD = (
    "{unit} from the Archivo Histórico Electoral: registered voters, ballots cast, votes to candidacies, blank "
    "ballots, and every list's votes{seats}. Valid votes here are the votes to candidacies, with blank ballots stored "
    "apart, and null ballots are the ballots cast less the valid ballots the archive reports."
)
TOWNS_NOTE = (
    " The municipal pages leave out the votes of electors living abroad (CERA), which the provincial figures include, "
    "so the municipalities add up to {got:,} of the province's {official:,} votes to lists."
)


@cache
def reference() -> dict[str, dict[str, str]]:
    """The archive's province areas and units and its municipal units, each by INE code."""
    folder = DATA / "reference" / "es"
    con = duckdb.connect()
    codes = "('03', '12', '46')"
    areas = dict(
        con.execute(
            f"select c.code, a.id from '{folder / 'area.parquet'}' a join '{folder / 'area_code.parquet'}' c"
            f" on c.area_id = a.id where a.scheme_code = 'es.provincia' and c.code in {codes}"
        ).fetchall()
    )
    units = con.execute(
        f"select scheme_code, code, id from '{folder / 'reporting_unit.parquet'}' where valid_to is null and "
        f"((scheme_code = 'es.provincia' and code in {codes}) or (scheme_code = 'es.municipio' and "
        "left(code, 2) in ('03', '12', '46') and length(code) = 5))"
    ).fetchall()
    return {
        "area:es.provincia": areas,
        "unit:es.provincia": {c: i for s, c, i in units if s == "es.provincia"},
        "unit:es.municipio": {c: i for s, c, i in units if s == "es.municipio"},
    }


# The abbreviation the archive prints for one list differs between its province pages in these elections; each is
# read under the abbreviation of the community page, or, where that page leaves the list out, the first province's.
ALIASES = {
    (2003, "L'ENTESA"): "ENTESA",
    (2011, "VERDES"): "VERDS",
    (2011, "ESPAÑA-2000"): "ESPAÑA 2000",
    (2011, "PARTIDO POPULAR"): "PP",
    (2015, "EUPV-VERDS-ERPV-AS:AC"): "EUPV-EV-ERPV-AS:AC",
    (2015, "RECORTES CERO ALICANTE"): "RECORTES CERO",
    (2015, "RECORTES CERO CASTELLON"): "RECORTES CERO",
    (2015, "ESPAÑA-2000"): "ESPAÑA 2000",
    (2019, "AVANT - LOS VERDES"): "AVANT Los Verdes",
    (2019, "PCPE"): "P.C.P.E.",
    (2019, "PSOE"): "P.S.O.E.",
}


def alias(year: int, abbreviation: str) -> str:
    return ALIASES.get((year, abbreviation), abbreviation)


class Province:
    def __init__(self, year: int, code: str, page: Page, towns: dict[str, Page]) -> None:
        self.code, self.page = code, page
        self.names = {alias(year, a): name for name, a, *_ in page.lists}
        self.votes = {alias(year, a): v for _, a, v, _ in page.lists if v is not None}
        self.seats = {alias(year, a): n for _, a, _, n in page.lists if n}
        self.towns: dict[str, dict[str, int]] = {}
        self.general: dict[str, dict[str, int]] = {}
        for c, town in towns.items():
            if town.standing:
                self.general[c] = town.general
                self.towns[c] = {alias(year, a): v for _, a, v, _ in town.lists if v is not None}

    def town_votes(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for votes in self.towns.values():
            total.update(votes)
        return total


class Election:
    def __init__(self, year: int) -> None:
        self.year = year
        self.community = read_page(community_url(year))
        codes = town_codes()
        self.provinces: dict[str, Province] = {}
        for code, (number_, *_rest) in PROVINCES.items():
            towns = {c: read_page(town_url(c, year)) for c in codes if c.startswith(code)}
            self.provinces[code] = Province(year, code, read_page(province_url(number_, year)), towns)

    @property
    def seats(self) -> int:
        return sum(sum(p.seats.values()) for p in self.provinces.values())


def dhondt(votes: dict[str, int], seats: int, eligible: set[str]) -> Counter[str]:
    quotients = sorted(
        ((v / d, v, k) for k, v in votes.items() if k in eligible for d in range(1, seats + 1)), reverse=True
    )
    return Counter(k for *_, k in quotients[:seats])


def eligible(election: Election) -> set[str]:
    total: Counter[str] = Counter()
    for province in election.provinces.values():
        total.update(province.votes)
    valid = sum(p.page.general["valid"] for p in election.provinces.values())
    return {k for k, v in total.items() if v * 100 >= THRESHOLD * valid}


def verify(election: Election) -> None:
    """Check the provinces against the community page and the municipalities against the provinces."""
    year = election.year
    for field in ("registered", "voters", "valid", "cand", "blank"):
        whole = sum(p.page.general[field] for p in election.provinces.values())
        if whole != election.community.general[field]:
            print(f"{year} {field}: provinces {whole:,} against community page {election.community.general[field]:,}")
    for key in {k for p in election.provinces.values() for k in p.votes}:
        whole = sum(p.votes.get(key, 0) for p in election.provinces.values())
        published = {a: v for _, a, v, _ in election.community.lists}.get(key)
        if whole != published:
            print(f"{year} {key}: provinces {whole:,} against community page {published}")

    for province in election.provinces.values():
        if sum(province.votes.values()) != province.page.general["cand"]:
            print(
                f"{year} {province.code}: lists {sum(province.votes.values()):,} against {province.page.general['cand']:,}"
            )
        for c, general in province.general.items():
            if general["cand"] != sum(province.towns[c].values()):
                print(f"{year} {c}: lists {sum(province.towns[c].values())} against {general['cand']}")
        for key in province.town_votes():
            if key not in province.votes:
                print(f"{year} {province.code}: municipal list {key} is not in the province page")


def registry_id(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id() -> str:
    return registry_id("source", "argos")


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PORTAL,
        attribution="Fuente: Generalitat Valenciana, Archivo Histórico Electoral (ARGOS).",
        notes="The Generalitat's open-data catalog publishes the Institut Cartogràfic Valencià's municipal map of the "
        "autonomic elections, built from this archive, with license field cc-by (Creative Commons Attribution) and an "
        "access constraint pointing to the ICV's conditions of use of its geographic information.",
    )


def elections() -> list[Election]:
    out = [Election(year) for year in DAYS]
    for election in out:
        verify(election)
    return out


def registry(all_elections: list[Election]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Corts Valencianes, their electoral system and size, the Archivo Histórico Electoral as a source, and the "
        "lists that stood in the elections of 1983 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    p.uses(raw.cached(SOURCE, CATALOG))
    p.uses(raw.cached(SOURCE, LAYER))
    body = p.id("body", "corts")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "Valencian Courts", "en", "official")
    p.name(body, "Corts Valencianes", "es", "native")
    sizes: list[tuple[int, date]] = []
    for election in all_elections:
        if not sizes or sizes[-1][0] != election.seats:
            sizes.append((election.seats, DAYS[election.year]))
    for i, (seats, start) in enumerate(sizes):
        end = sizes[i + 1][1] if i + 1 < len(sizes) else None
        p.add("body_size", body_id=body, seats=seats, valid_from=start, valid_to=end)
    system = p.id("electoral_system", "corts")
    p.add(
        "electoral_system",
        id=system,
        polity_id=REGION,
        rules={
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "national", "percent": THRESHOLD},
        },
        rules_version=1,
    )
    p.name(system, "Corts Valencianes closed-list D'Hondt system with a 5% Community-wide threshold", "en", "official")
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    names: dict[str, str] = {}
    for election in all_elections:
        for province in election.provinces.values():
            names.update({party_key(a): n for a, n in province.names.items() if a in province.votes})
    for key, text in sorted(names.items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, text, "es", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key)
    return p


def accounting(general: dict[str, int]) -> dict[int, int]:
    return {
        REGISTERED: general["registered"],
        BALLOTS: general["voters"],
        VALID: general["cand"],
        INVALID: general["voters"] - general["valid"],
        BLANK: general["blank"],
    }


def build(election: Election) -> Package:
    ref = reference()
    year, day = election.year, DAYS[election.year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} election to the Corts Valencianes by province and municipality, with the seats each list won",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {SPAIN, REGION}
    src = source_id()
    p.artifact(raw.cached(SOURCE, community_url(year)), src)
    body, system = p.id("body", "corts"), p.id("electoral_system", "corts")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Valencian regional election", "en", "official")
    p.name(eid, f"Elecciones a las Cortes Valencianas de {year}", "es", "native")
    path = f"es/vc/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    passing = eligible(election)
    values: list[tuple[str, str, str | None, int, int, int]] = []
    short_towns = 0
    for code, province in election.provinces.items():
        number_, spanish, valencian = PROVINCES[code]
        area, unit = ref["area:es.provincia"][code], ref["unit:es.provincia"][code]
        p.external |= {area, unit}
        p.artifact(raw.cached(SOURCE, province_url(number_, year)), src)
        magnitude = sum(province.seats.values())
        contest = p.id("contest", f"{day}:{code}")
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=magnitude,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, spanish, "es", "official")
        p.name(contest, valencian, "ca", "native")
        p.add("slug", path=f"{path}/{slug(spanish)}", entity_id=contest, is_current=True)
        options: dict[str, str] = {}
        for abbreviation in sorted(province.votes, key=lambda a: -province.votes[a]):
            options[abbreviation] = p.id("option", f"{day}:{code}", party_key(abbreviation))
            p.add(
                "contest_option",
                id=options[abbreviation],
                contest_id=contest,
                kind="list",
                party_id=p.id("party", party_key(abbreviation)),
                ballot_label=abbreviation,
            )
        won = dhondt(province.votes, magnitude, passing)
        differs = sorted(a for a in options if won[a] != province.seats.get(a, 0))
        note = METHOD.format(
            unit="The province's figures, with the votes of electors abroad included", seats=", with the seats won"
        )
        if differs:
            note += (
                " D'Hondt with a 5% Community-wide threshold applied to these votes gives a different split from the "
                "declared seats, which are stored as published."
            )
        province_set = p.id("result_set", f"{day}:{code}", "province")
        p.add(
            "result_set",
            id=province_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=src,
            is_default=True,
            completeness=1,
        )
        p.add(
            "note", id=p.id("note", province_set), entity_id=province_set, kind="methodology", body_md=note, lang="en"
        )
        for measure, count in accounting(province.page.general).items():
            values.append((province_set, unit, None, measure, count, 0))
        for abbreviation, option in options.items():
            values.append((province_set, unit, option, VOTES, province.votes[abbreviation], 0))
            if province.seats.get(abbreviation):
                p.add(
                    "outcome",
                    result_set_id=province_set,
                    option_id=option,
                    kind="won",
                    seats=province.seats[abbreviation],
                )
        got = sum(sum(v.values()) for v in province.towns.values())
        official = sum(province.votes.values())
        town_set = p.id("result_set", f"{day}:{code}", "municipality")
        p.add(
            "result_set",
            id=town_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=src,
            is_default=False,
            completeness=min(got / official, 1.0),
        )
        town_note = METHOD.format(unit="Each municipality's figures", seats="") + TOWNS_NOTE.format(
            got=got, official=official
        )
        p.add("note", id=p.id("note", town_set), entity_id=town_set, kind="methodology", body_md=town_note, lang="en")
        for town, votes in province.towns.items():
            uid = ref["unit:es.municipio"][town]
            p.external.add(uid)
            p.artifact(raw.cached(SOURCE, town_url(town, year)), src)
            general = province.general[town]
            for measure, count in accounting(general).items():
                values.append((town_set, uid, None, measure, count, 0))
            for abbreviation, count in votes.items():
                values.append((town_set, uid, options[abbreviation], VOTES, count, 0))
            if sum(votes.values()) != general["cand"]:
                short_towns += 1
        print(
            f"{year} {code} seats={magnitude} lists={official} towns={len(province.towns)} dhondt_differs={differs}",
            flush=True,
        )
    if short_towns:
        p.accept(
            "accounting.votes_valid",
            f"In {short_towns} municipal pages of the {year} election the lists' votes differ slightly from the "
            "votes to candidacies the same page gives; both are stored as published.",
        )
    if any(sum(province.votes.values()) != province.page.general["cand"] for province in election.provinces.values()):
        p.accept(
            "accounting.votes_valid",
            f"In the archive's {year} province page the lists' votes differ slightly from the votes to candidacies; "
            "both are stored as published.",
        )
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "channel": pl.Int16,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    all_elections = elections()
    registry(all_elections).write()
    for election in all_elections:
        build(election).write()


def emit() -> None:
    package.run("load", packages())
