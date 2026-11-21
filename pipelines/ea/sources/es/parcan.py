"""Parlamento de Canarias elections, 1995 to 2023, by polling station (mesa), from the Parlamento's results API.

The Parlamento de Canarias publishes, for each election since 1995 and each constituency, one JSON row per mesa and
list (`api/resultados_electorales/{year}/{constituency}/`). Constituencies 1 to 7 are the islands (El Hierro,
Fuerteventura, Gran Canaria, La Gomera, Lanzarote, La Palma and Tenerife) and, from 2019, constituency 8 is the
regional list. A second API (`api2`) gives each constituency's official summary, with the 15 percent (or older)
threshold figure, from 1999, and the deputies elected, by seat, from 1999 as well. The data carry the ODC-PDDL license.

The island constituencies, the community, the municipalities, and most polling stations are rows the archive already
holds from the Ministry of the Interior's files; they are read from the Spanish reference partition
(`data/reference/es/`) and referenced, not written again. A mesa the archive does not hold is added here under the
municipality that holds it, or under its island for the residents abroad (CERA), whom the Parlamento's API files under
a code of their own on each island.
"""

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.names import display
from ea.package import Package
from ea.store import DATA

SOURCE = "es.parcan"
PAGE = "https://www.parcan.es/"
PUBLISHER = "Parlamento de Canarias"
TITLE = "Parlamento de Canarias: resultados electorales por mesa (datos abiertos)"
LICENSE = "ODC-PDDL"
LICENSE_URL = "https://opendatacommons.org/licenses/pddl/1-0/"
API = "https://www.parcan.es/api/resultados_electorales"
API2 = "https://www.parcan.es/api2/resultados_electorales"
REFERENCE = DATA / "reference" / "es"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-CN"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6

DAYS = {
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
# Elections the API does not carry: held, with no results published there.
EARLY = {1983: date(1983, 5, 8), 1987: date(1987, 6, 10), 1991: date(1991, 5, 26)}
YEARS = tuple(DAYS)
REGIONAL_FROM = 2019

# Constituency number: (code, name, province code of its municipalities, es.isla code).
ISLANDS = {
    1: ("el-hierro", "El Hierro", "38", "387"),
    2: ("fuerteventura", "Fuerteventura", "35", "353"),
    3: ("gran-canaria", "Gran Canaria", "35", "351"),
    4: ("la-gomera", "La Gomera", "38", "386"),
    5: ("lanzarote", "Lanzarote", "35", "352"),
    6: ("la-palma", "La Palma", "38", "385"),
    7: ("tenerife", "Tenerife", "38", "384"),
}
REGIONAL = 8
REGIONAL_NAME = "Autonómica"
BODY_SEATS = ((60, date(1983, 5, 8), DAYS[2019]), (70, DAYS[2019], None))

STATUTE_1982 = "Ley Orgánica 10/1982, de 10 de agosto, de Estatuto de Autonomía de Canarias (transitional provision)"
STATUTE_1996 = "Ley Orgánica 4/1996, de 30 de diciembre, de reforma del Estatuto de Autonomía de Canarias"
LAW_2022 = "Ley 1/2022, de 12 de abril, del Parlamento de Canarias (art. 19, BOE-A-2022-10043)"
LISTS = {"order": "closed", "vacancy": "next_on_list"}
ISLAND_1995 = {
    "formula": "dhondt",
    "lists": LISTS,
    "threshold": {
        "percent": 20,
        "level": "list",
        "scope": "contest",
        "alternative": {"percent": 3, "scope": "national"},
    },
}
ISLAND_1999 = {
    "formula": "dhondt",
    "lists": LISTS,
    "threshold": {
        "percent": 30,
        "level": "list",
        "scope": "contest",
        "leader": True,
        "alternative": {"percent": 6, "scope": "national"},
    },
}
ISLAND_2019 = {
    "formula": "dhondt",
    "lists": LISTS,
    "threshold": {
        "percent": 15,
        "level": "list",
        "scope": "contest",
        "alternative": {"percent": 4, "scope": "national"},
    },
}
REGIONAL_2019 = {
    "formula": "dhondt",
    "lists": LISTS,
    "threshold": {"percent": 4, "level": "list", "scope": "contest"},
}
SYSTEMS = {
    "island-1995": (
        ISLAND_1995,
        "Parliament of the Canary Islands island-constituency D'Hondt system with a 20% island or 3% regional threshold",
        "Closed lists in seven island constituencies. Seats go by the D'Hondt method to the lists with at least 20% "
        "of the valid votes of the island or at least 3% of the valid votes of the whole Canary Islands, blank "
        "ballots counted as valid (" + STATUTE_1982 + "). The sources give no first-placed-list clause for these "
        "elections, so none is stored.",
    ),
    "island-1999": (
        ISLAND_1999,
        "Parliament of the Canary Islands island-constituency D'Hondt system with a 30% island or 6% regional threshold",
        "Closed lists in seven island constituencies. Seats go by the D'Hondt method to the lists that have at least "
        "30% of the valid votes of the island, are the most voted list of the island, or have at least 6% of the "
        "valid votes of the whole Canary Islands, blank ballots counted as valid (" + STATUTE_1996 + "). The "
        "regional percentage is a share of the votes cast in all seven islands together.",
    ),
    "island-2019": (
        ISLAND_2019,
        "Parliament of the Canary Islands island-constituency D'Hondt system with a 15% island or 4% regional threshold",
        "Closed lists in seven island constituencies, 61 deputies. Seats go by the D'Hondt method to the lists that "
        "have at least 15% of the valid votes of the island or at least 4% of the valid votes of the whole Canary "
        "Islands, blank ballots counted as valid (" + LAW_2022 + "; the thresholds applied already in 2019).",
    ),
    "regional-2019": (
        REGIONAL_2019,
        "Parliament of the Canary Islands regional-list D'Hondt system with a 4% threshold",
        "From 2019, nine deputies are elected on a single regional list in a constituency of the whole Canary "
        "Islands. Seats go by the D'Hondt method to the lists with at least 4% of the valid votes of the Canary "
        "Islands, blank ballots counted as valid (" + LAW_2022 + ").",
    ),
}
SYSTEM_OF = {
    1995: "island-1995",
    1999: "island-1999",
    2003: "island-1999",
    2007: "island-1999",
    2011: "island-1999",
    2015: "island-1999",
    2019: "island-2019",
    2023: "island-2019",
}
METHOD = (
    "Registered voters, null ballots, blank ballots, and each list's votes by polling station (mesa) from the "
    "Parlamento de Canarias's results API, one row per mesa and list. Valid votes here are the votes to lists, so "
    "ballots cast equal valid plus blank plus null; the Parlamento counts blank ballots among the valid votes. "
    "The constituency totals are the Parlamento's own island summary, stored as declared totals."
)
NO_SUMMARY = (
    "The Parlamento's API holds no island summary and no elected deputies for this election, so there are no "
    "declared totals or seats to store, and the number of seats of each island is not published there."
)
EARLY_NOTE = (
    "The Parlamento de Canarias's results API starts with the 1995 election and holds no results for this one, "
    "so the archive records the election without results."
)
CASE = {"Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ü": "U"}

# The elected-deputies file names a few lists differently from the mesa file; each is the list it means.
ALIASES: dict[tuple[int, str, str], str] = {
    (2003, "El Hierro", "COALICION CANARIA"): "COALICION CANARIA-AGRUPACION HERREÑA INDEPENDIENTE",
    (2003, "Lanzarote", "FEDERACION NACIONALISTA CANARIA"): "COALICION PIL-FNC",
    (2011, "El Hierro", "AGRUPACION HERREREÑA INDEPENDIENTE - COALICION CANARIA"): (
        "AGRUPACION HERREÑA INDEPENDIENTE - COALICION CANARIA"
    ),
    (2011, "Lanzarote", "NUEVA CANARIAS"): (
        "PARTIDO DE INDEPENDIENTES DE LANZAROTE-PARTIDO NACIONALISTA DE LANZAROTE-NUEVA CANARIAS"
    ),
    (2019, "El Hierro", "COALICION CANARIA-PARTIDO NACIONALISTA CANARIO"): (
        "AGRUPACION HERREÑA INDEPENDIENTE-COALICION CANARIA"
    ),
    (2019, "Lanzarote", "SI PODEMOS CANARIAS"): "LANZAROTE EN PIE-SI PODEMOS",
}


def circs(year: int) -> range:
    return range(1, 9 if year >= REGIONAL_FROM else 8)


def mesa_url(year: int, circ: int) -> str:
    return f"{API}/{year}/{circ}/?format=json"


def resumen_url(year: int) -> str:
    return f"{API2}/{year}/resumen/?format=csv"


def electos_url(year: int) -> str:
    return f"{API2}/{year}/diputados_electos/"


def fetch() -> None:
    for year in YEARS:
        for circ in circs(year):
            raw.fetch(SOURCE, mesa_url(year, circ), suffix=".json")
        if year > 1995:
            raw.fetch(SOURCE, resumen_url(year), suffix=".csv")
        raw.fetch(SOURCE, electos_url(year), suffix=".json")


def fold(text: str) -> str:
    """A name without accents, upper-cased, with spaces collapsed, so spellings of one list compare equal."""
    plain = "".join(
        c
        for c in unicodedata.normalize("NFD", text.replace("Ñ", "\x01").replace("ñ", "\x01"))
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", plain.replace("\x01", "Ñ").upper()).strip()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fparcan"))


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Fuente: Parlamento de Canarias, datos abiertos (ODC-PDDL).",
    )


@dataclass
class Reference:
    """Rows production already holds, from the Spanish reference partition."""

    islands: dict[str, tuple[str, str]]  # es.isla code -> (unit id, area id)
    towns: dict[str, tuple[str, str | None]]  # es.municipio code -> (unit id, area id)
    mesas: dict[str, str]  # es.mesa code -> unit id
    community_area: str = ""
    nation: str = ""


def reference() -> Reference:
    units = pl.scan_parquet(REFERENCE / "reporting_unit.parquet")
    wanted = [f"{y.year}{y.month:02d}:" for y in DAYS.values()]
    rows = units.filter(pl.col("scheme_code") == "es.isla").collect()
    islands = {r["code"]: (r["id"], r["area_id"]) for r in rows.iter_rows(named=True)}
    towns_frame = units.filter(
        (pl.col("scheme_code") == "es.municipio") & pl.col("code").str.contains(r"^(35|38)\d{3}$")
    ).collect()
    towns = {r["code"]: (r["id"], r["area_id"]) for r in towns_frame.iter_rows(named=True)}
    mesa_frame = units.filter(
        (pl.col("scheme_code") == "es.mesa") & pl.col("code").str.contains(r"^\d{6}:(35|38)")
    ).collect()
    mesas = {
        r["code"]: r["id"] for r in mesa_frame.iter_rows(named=True) if any(r["code"].startswith(w) for w in wanted)
    }
    areas = pl.read_parquet(REFERENCE / "area.parquet")
    community = areas.filter((pl.col("scheme_code") == "es.comunidad") & (pl.col("name") == "Canarias"))
    if community.height != 1 or len(islands) < 7:
        raise ValueError("the Spanish reference partition lacks the Canary Islands rows")
    nation = units.filter(pl.col("scheme_code") == "es.nation").collect()
    if nation.height != 1:
        raise ValueError("the Spanish reference partition lacks the nation's reporting unit")
    return Reference(islands, towns, mesas, community["id"][0], nation["id"][0])


def island_unit_codes() -> list[str]:
    return [island[3] for island in ISLANDS.values()]


@dataclass
class Mesa:
    code: str
    town: str  # es.municipio code, or "" for the residents abroad
    island: int
    registered: int
    null: int
    blank: int
    votes: dict[str, int] = field(default_factory=dict)  # list name as printed -> votes


@dataclass
class Contest:
    circ: int
    mesas: list[Mesa]
    summary: dict[str, int] | None = None
    elected: list[tuple[int, str, str]] = field(default_factory=list)  # seat, list as printed, deputy
    initials: dict[str, str] = field(default_factory=dict)  # list name as printed -> its abbreviation


def town_number(row: dict[str, Any]) -> str:
    """The INE municipality number: the file's own, except El Pinar de El Hierro, which it numbers 01 and INE 901."""
    if fold(row["municipio"]) == "EL PINAR DE EL HIERRO":
        return "901"
    return str(row["id_municipio"]).zfill(3)


def mesa_code(year: int, province: str, row: dict[str, Any]) -> str:
    day = DAYS[year]
    return (
        f"{day.year}{day.month:02d}:{province}{town_number(row)}"
        f"{str(row['distrito']).zfill(2)}{str(row['seccion']).zfill(3)}{row['mesa']}"
    )


def read_json(url: str) -> list[dict[str, Any]]:
    data = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    if not isinstance(data, list):
        raise TypeError(f"{url}: not a list")
    return data


def island_of(name: str, towns: dict[str, tuple[int, str]]) -> int:
    folded = fold(name)
    if folded == "RESIDENTES AUSENTES CANARIAS":
        return 0
    if folded.startswith("RESIDENTES AUSENTES"):
        for circ, (_, island, _, _) in ISLANDS.items():
            if folded.endswith(fold(island)):
                return circ
        raise ValueError(f"residents abroad of no island: {name!r}")
    return towns[folded][0]


def load(year: int) -> dict[int, Contest]:
    contests: dict[int, Contest] = {}
    towns: dict[str, tuple[int, str]] = {}
    for circ in circs(year):
        rows = read_json(mesa_url(year, circ))
        if circ < REGIONAL:
            for row in rows:
                if int(row["id_municipio"]) < 90:
                    towns[fold(row["municipio"])] = (circ, str(row["id_municipio"]))
        by_mesa: dict[int, Mesa] = {}
        initials: dict[str, str] = {}
        for row in rows:
            if row["id_circunscripcion"] != circ or row["eleccion"] != year:
                raise ValueError(f"{year}/{circ}: row of {row['eleccion']}/{row['id_circunscripcion']}")
            island = circ if circ < REGIONAL else island_of(row["municipio"], towns)
            if circ == REGIONAL and island:
                # The regional file numbers some municipalities differently; the island files' number is the INE one.
                row = (
                    {**row, "id_municipio": towns[fold(row["municipio"])][1]} if int(row["id_municipio"]) < 90 else row
                )
            province = ISLANDS[island][2] if island else "99"
            mesa = by_mesa.get(row["id_mesa"])
            if mesa is None:
                abroad = int(row["id_municipio"]) >= 90
                code = mesa_code(year, province, row)
                mesa = by_mesa[row["id_mesa"]] = Mesa(
                    code,
                    "" if abroad else f"{province}{town_number(row)}",
                    island,
                    row["censados"],
                    row["papeletas_nulas"],
                    row["votos_blancos"],
                )
            name = clean(row["partido"])
            if name in mesa.votes:
                raise ValueError(f"{year}/{circ}: mesa {row['id_mesa']} lists {name} twice")
            mesa.votes[name] = row["votos"]
            initials[name] = re.sub(r"[\s.]+", "", fold(row["siglas"]))
        codes = Counter(m.code for m in by_mesa.values())
        twice = [c for c, n in codes.items() if n > 1]
        if twice:
            raise ValueError(f"{year}/{circ}: one code for two mesas: {twice[:3]}")
        contests[circ] = Contest(circ, list(by_mesa.values()), initials=initials)
    if year > 1995:
        names = {
            circ: (island[1] if circ < REGIONAL else REGIONAL_NAME)
            for circ, island in {**ISLANDS, REGIONAL: (0, REGIONAL_NAME, 0, 0)}.items()
        }
        with raw.cached(SOURCE, resumen_url(year)).path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                circ = next(c for c, n in names.items() if n == row["circunscripcion"].strip())
                if int(row["elecciones"]) != year or circ not in contests:
                    raise ValueError(f"{year}: summary row {row}")
                contests[circ].summary = {
                    "registered": int(row["censo"]),
                    "ballots": int(row["total_votos"]),
                    "invalid": int(row["votos_nulos"]),
                    "blank": int(row["votos_blancos"]),
                    "barrier": int(row["barrera"]),
                }
        for circ in circs(year):
            if contests[circ].summary is None:
                raise ValueError(f"{year}/{circ}: no summary row")
    names_by_fold = {n: circ for circ, (_, n, _, _) in ISLANDS.items() for n in (fold(n),)}
    for row in read_json(electos_url(year)):
        if row["elecciones"] != year:
            raise ValueError(f"{year}: elected row of {row['elecciones']}")
        name = clean(row["isla"])
        circ = REGIONAL if fold(name) == fold(REGIONAL_NAME) else names_by_fold[fold(name)]
        contests[circ].elected.append((int(row["escaño"]), clean(row["partido"]), clean(row["diputado"])))
    return contests


def list_names(contest: Contest) -> dict[str, int]:
    """Each list's name as printed, with its votes in the constituency, most voted first."""
    total: Counter[str] = Counter()
    for mesa in contest.mesas:
        total.update(mesa.votes)
    return dict(sorted(total.items(), key=lambda item: (-item[1], item[0])))


def match_seats(year: int, circ: int, contest: Contest) -> dict[str, list[tuple[int, str]]]:
    """The elected deputies of each list, by the list's name as the mesa file prints it."""
    by_fold = {fold(n): n for n in list_names(contest)}
    island = ISLANDS[circ][1] if circ < REGIONAL else REGIONAL_NAME
    found: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for seat, name, deputy in sorted(contest.elected):
        key = ALIASES.get((year, island, fold(name)), fold(name))
        if key not in by_fold:
            raise ValueError(f"{year} {island}: elected list {name!r} is on no mesa list")
        found[by_fold[key]].append((seat, deputy))
    return found


def party_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(name).lower().replace("ñ", "n")).strip("-")[:90]


def dhondt(votes: dict[str, int], qualified: set[str], seats: int) -> Counter[str]:
    quotients = sorted(
        ((votes[k] / d, votes[k], k) for k in qualified for d in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(k for *_, k in quotients[:seats])


def engine_check(year: int, contests: dict[int, Contest]) -> dict[int, bool]:
    """Whether the era's rules, applied to the votes stored here, give the declared seats in each constituency.

    A list's regional share is counted over the lists that carry the same abbreviation on every island."""
    percent, regional, leader = (
        (20, 3, False) if year == 1995 else (30, 6, True) if year < REGIONAL_FROM else (15, 4, False)
    )
    totals: Counter[str] = Counter()
    base = 0
    for circ in ISLANDS:
        contest = contests[circ]
        names = list_names(contest)
        base += sum(names.values()) + sum(m.blank for m in contest.mesas)
        for name, votes in names.items():
            totals[contest.initials[name]] += votes
    answer: dict[int, bool] = {}
    for circ, contest in contests.items():
        if not contest.elected:
            continue
        names = list_names(contest)
        valid = sum(names.values()) + sum(m.blank for m in contest.mesas)
        if circ == REGIONAL:
            qualified = {n for n, v in names.items() if v * 100 >= 4 * valid}
        else:
            top = max(names.values())
            qualified = {
                name
                for name, votes in names.items()
                if votes * 100 >= percent * valid
                or totals[contest.initials[name]] * 100 >= regional * base
                or (leader and votes == top)
            }
        won = dhondt(names, qualified, len(contest.elected))
        declared = Counter({name: len(rows) for name, rows in match_seats(year, circ, contest).items()})
        answer[circ] = +won == +declared
    return answer


def registry(all_names: dict[str, str]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Parlamento de Canarias, its size and electoral systems, the Parlamento's results API as a source, and "
        "the lists that stood in the elections of 1995 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    body = p.id("body", "parlamento")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "Parliament of the Canary Islands", "en", "official")
    p.name(body, "Parlamento de Canarias", "es", "native")
    for seats, start, end in BODY_SEATS:
        p.add("body_size", body_id=body, seats=seats, valid_from=start, valid_to=end)
    for key, (rules, name, text) in SYSTEMS.items():
        system = p.id("electoral_system", key)
        p.add("electoral_system", id=system, polity_id=REGION, rules=rules, rules_version=1)
        p.name(system, name, "en", "official")
        p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=text, lang="en")
    for key, name in sorted(all_names.items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, name, "es", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key)
    return p


def build(year: int, contests: dict[int, Contest], ref: Reference) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} election to the Parlamento de Canarias, with the votes of every list by mesa in each island "
        + ("and the regional list" if year >= REGIONAL_FROM else "constituency"),
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {REGION, SPAIN, ref.community_area, "es.mesa"}
    src = source_id()
    for circ in circs(year):
        p.artifact(raw.cached(SOURCE, mesa_url(year, circ)), src)
    if year > 1995:
        p.artifact(raw.cached(SOURCE, resumen_url(year)), src)
    p.artifact(raw.cached(SOURCE, electos_url(year)), src)
    body = p.id("body", "parlamento")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Canary Islands regional election", "en", "official")
    p.name(eid, f"Elecciones al Parlamento de Canarias de {year}", "es", "native")
    path = f"es/cn/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    fits = engine_check(year, contests) if year > 1995 else {}
    units: dict[str, str] = {}
    values: list[tuple[str, str, str | None, int, int, int]] = []
    mismatches: list[str] = []
    over = empty = minted = 0
    for circ, contest in contests.items():
        regional = circ == REGIONAL
        code, name, _, island_code = ("regional", REGIONAL_NAME, "", "") if regional else ISLANDS[circ]
        seats = len(contest.elected) if contest.elected else None
        system = p.id("electoral_system", "regional-2019" if regional else SYSTEM_OF[year])
        if regional:
            area = ref.community_area
        else:
            island_unit, area = ref.islands[island_code]
            p.external |= {island_unit, area}
        c = p.id("contest", str(day), code)
        p.add(
            "contest",
            id=c,
            election_id=eid,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=seats,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(c, "Regional list of the Canary Islands" if regional else name, "en", "official")
        p.name(c, "Circunscripción autonómica" if regional else name, "es", "native")
        p.add("slug", path=f"{path}/{code}", entity_id=c, is_current=True)
        result_set = p.id("result_set", str(day), code)
        p.add(
            "result_set",
            id=result_set,
            election_id=eid,
            contest_id=c,
            stage="official",
            kind="counted",
            status="active",
            source_id=src,
            is_default=True,
            completeness=1,
        )
        note = METHOD if contest.summary else METHOD.split(" The constituency totals")[0] + " " + NO_SUMMARY
        if circ in fits and not fits[circ]:
            note += (
                " The rules of the system applied to these votes give a different split from the declared seats, "
                "which are stored as published."
            )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        options: dict[str, str] = {}
        for position, (label, _) in enumerate(list_names(contest).items(), start=1):
            options[label] = p.id("option", f"{day}:{code}:{party_key(label)}")
            p.add(
                "contest_option",
                id=options[label],
                contest_id=c,
                kind="list",
                party_id=p.id("party", party_key(label)),
                ballot_position=None,
                ballot_label=label,
            )
        sums: Counter[str] = Counter()
        for mesa in contest.mesas:
            unit = units.get(mesa.code)
            if unit is None:
                held = ref.mesas.get(mesa.code)
                if held is not None:
                    unit = units[mesa.code] = held
                    p.external.add(held)
                else:
                    unit = units[mesa.code] = p.id("unit", mesa.code)
                    minted += 1
                    if mesa.town:
                        parent, town_area = ref.towns[mesa.town]
                        p.external.add(parent)
                        located = town_area
                    else:
                        parent = ref.islands[ISLANDS[mesa.island][3]][0] if mesa.island else ref.nation
                        located = None
                        p.external.add(parent)
                    if located is not None:
                        p.external.add(located)
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=SPAIN,
                        scheme_code="es.mesa",
                        code=mesa.code,
                        parent_id=parent,
                        area_relation="within" if located else "unlocated",
                        area_id=located,
                        kind="standard",
                    )
            listed = sum(mesa.votes.values())
            if listed + mesa.blank + mesa.null > mesa.registered:
                over += 1
                empty += mesa.registered == 0
            figures = {
                REGISTERED: mesa.registered,
                BALLOTS: listed + mesa.blank + mesa.null,
                VALID: listed,
                INVALID: mesa.null,
                BLANK: mesa.blank,
            }
            for measure, value in figures.items():
                values.append((result_set, unit, None, measure, value, 0))
                sums[str(measure)] += value
            for label, votes in mesa.votes.items():
                values.append((result_set, unit, options[label], VOTES, votes, 0))
        if contest.summary:
            s = contest.summary
            declared = {
                REGISTERED: s["registered"],
                BALLOTS: s["ballots"],
                VALID: s["ballots"] - s["invalid"] - s["blank"],
                INVALID: s["invalid"],
                BLANK: s["blank"],
            }
            for measure, value in declared.items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            off = {m: (sums[str(m)], v) for m, v in declared.items() if sums[str(m)] != v}
            if off:
                labels = {
                    REGISTERED: "registered",
                    BALLOTS: "ballots",
                    VALID: "valid",
                    INVALID: "invalid",
                    BLANK: "blank",
                }
                mismatches.append(
                    f"{name}: "
                    + ", ".join(
                        f"{labels[m]} {got:,} by mesa against {want:,} declared" for m, (got, want) in off.items()
                    )
                )
        seat_rows = match_seats(year, circ, contest) if contest.elected else {}
        for label, members in seat_rows.items():
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=len(members))
            for seat, deputy in members:
                person = p.id("person", f"{day}:{code}:{seat}")
                p.add("person", id=person, birth_precision=None)
                p.name(person, display(deputy, "es"), "es", "official")
                candidacy = p.id("candidacy", f"{day}:{code}:{seat}")
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=options[label],
                    role="list_member",
                    ballot_name=display(deputy, "es"),
                    is_independent=False,
                )
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[label],
                    candidacy_id=candidacy,
                    kind="elected",
                )
        print(f"{year} {code}: mesas={len(contest.mesas)} lists={len(options)} seats={seats} rules_ok={fits.get(circ)}")
    if over:
        p.accept(
            "accounting.turnout",
            f"{over} mesa results report more ballots than registered voters, {empty} of them with no registered "
            "voters at all (mostly the residents abroad, whose roll the API does not split by mesa), as the source "
            "gives them",
        )
    if mismatches:
        p.accept(
            "accounting.declared",
            "The mesa rows sum differently from the Parlamento's own constituency summary, and both are stored as "
            "published: " + "; ".join(mismatches),
        )
    print(f"{year}: {minted} mesa units added, {len(units) - minted} referenced")
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


def early() -> Package:
    p = Package(
        f"{SOURCE}/early",
        SOURCE,
        "The 1983, 1987 and 1991 elections to the Parlamento de Canarias, recorded without results because the "
        "Parlamento's results API starts in 1995",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(REGION)
    for year, day in EARLY.items():
        eid = p.id("election", day)
        p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
        p.name(eid, f"{year} Canary Islands regional election", "en", "official")
        p.name(eid, f"Elecciones al Parlamento de Canarias de {year}", "es", "native")
        p.add("slug", path=f"es/cn/{day}-general", entity_id=eid, is_current=True)
        p.add("note", id=p.id("note", eid), entity_id=eid, kind="caveat", body_md=EARLY_NOTE, lang="en")
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/early", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    ref = reference()
    loaded = {year: load(year) for year in YEARS}
    names: dict[str, str] = {}
    for year in sorted(YEARS, reverse=True):
        for contest in loaded[year].values():
            for label in list_names(contest):
                names.setdefault(party_key(label), label)
    registry(names).write()
    early().write()
    for year in YEARS:
        build(year, loaded[year], ref).write()


def emit() -> None:
    package.run("load", packages())
