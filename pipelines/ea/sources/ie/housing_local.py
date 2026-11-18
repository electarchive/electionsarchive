"""Irish city and county council elections, 1999 to 2024, by local electoral area, from the Department's volumes.

The Department of Housing, Local Government and Heritage publishes a results volume for every local election on its
open data portal. Each prints one count table per local electoral area: the area's electorate, invalid ballot
papers, valid poll, seats, and quota, the names of the candidates elected, and every candidate's votes at each count.
`local_text` reads the tables from the text layer; this module builds one package per election date, the `geo`
package with the electoral areas and the boundaries Tailte Éireann publishes for the 2014, 2019, and 2024 areas, and
the `registry` package with the sources, the parties, the councils, and the electoral system.

Each candidate's votes are the first preferences, stored under count round 1 like the archive's other single
transferable vote counts, with the declared outcome of the full count. The 1999 and 2004 volumes also hold the town
and borough councils in a second volume; those councils are left for a later package.
"""

import functools
import json
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from uuid import uuid5

import numpy
import polars as pl
import pyproj
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ie import local_text
from ea.sources.ie.local_text import Candidate, Table

SOURCE = "ie.housing_local"
PORTAL = "https://opendata.housing.gov.ie/dataset"
# Ireland's records predate derived ids, so the archive keeps the ids it was first given
IRELAND = "01a13bd3-643c-74a1-9302-0a8df9ef9df8"
NATION = "01a13bd3-646d-7361-b1d6-2dc63ccf022c"
SCHEME = "ie.lea"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, QUOTA = 1, 2, 3, 4, 5, 26
DERIVED = 1
VOLUMES: dict[int, tuple[date, str]] = {
    1999: (
        date(1999, 6, 11),
        (
            f"{PORTAL}/0f259824-1e75-4e5d-b7df-573aa913b882/resource/50b18437-8d95-48ea-9f0e-9ce8a0e16ed1"
            "/download/1999-volume-1.pdf"
        ),
    ),
    2004: (
        date(2004, 6, 11),
        (
            f"{PORTAL}/b6764a8a-2b78-4fb5-a37f-33d5064acb79/resource/b66c4d1b-bc46-4036-8476-d20deb3e2206"
            "/download/local-election-results-2004-volume-1.pdf"
        ),
    ),
    2009: (
        date(2009, 6, 5),
        (
            f"{PORTAL}/0dc64295-a186-442c-ab8f-7633675aea19/resource/7f8a0f95-7cd7-4e7d-a1a2-9758a2df8c77"
            "/download/local-election-results-2009-volume-1.pdf"
        ),
    ),
    2014: (
        date(2014, 5, 23),
        (
            f"{PORTAL}/a346551c-eb44-4aee-9370-61b09880ee47/resource/908d7108-fe5f-4e0c-8b7a-c1e9c960ba26"
            "/download/local-election-results-2014.pdf"
        ),
    ),
    2019: (
        date(2019, 5, 24),
        (
            f"{PORTAL}/d8092fff-40f7-4bae-b9a6-297a86e7e9ea/resource/18d573f9-9e1f-4359-9185-16ca02c70419"
            "/download/local-election-results-2019.pdf"
        ),
    ),
    2024: (
        date(2024, 6, 7),
        (
            f"{PORTAL}/70055dc1-1f18-43ac-8501-f9e8ceb1eabf/resource/2100b4ff-b957-4b3b-9ff8-58cbf9c6e7a3"
            "/download/2024-local-election-results.pdf"
        ),
    ),
}
YEARS = sorted(VOLUMES)
HUB = "https://data-osi.opendata.arcgis.com/api/download/v1/items"
SERVICES = "https://services-eu1.arcgis.com/FH5XCsx8rYXqnjF5/arcgis/rest/services"
# Tailte Éireann's boundaries of the areas each election used; the 2014 areas are the 2015 administrative set
BOUNDARIES: dict[int, tuple[str, str, str]] = {
    2014: (f"{HUB}/7ad1fb9edfd64bb0b674f75cc8e371cf/geojson?layers=0", "LE_ENGLISH", ".geojson"),
    2019: (f"{HUB}/676ec3eff06e4d859a0415706811d9e1/geojson?layers=0", "ENGLISH", ".geojson"),
    2024: (
        (
            f"{SERVICES}/LocalElectoralArea_NationalStatutoryBoundaries_Ungeneralised_2024/FeatureServer/1/query"
            "?where=1%3D1&outFields=BDY_ID,ENG_NAME_VALUE,GLE_NAME_VALUE&outSR=4326&maxAllowableOffset=0.0002"
            "&geometryPrecision=6&f=geojson"
        ),
        "ENG_NAME_VALUE",
        ".geojson",
    ),
}
# the 2014 and 2019 sets come in Irish Transverse Mercator (EPSG:2157), the 2024 set in longitude and latitude
IRISH_GRID = {2014, 2019}
# names the volumes print differently from the boundary sets
ALIASES = {(2024, "cardonagh"): "carndonagh"}
# Each volume's abbreviations, by the letters of the printed code; a code means one party in every volume except
# NP, which in 1999 was the National Party of the 1990s, not the National Party founded in 2016 (TNP in 2024).
PARTIES: dict[str, tuple[str, str]] = {
    "FF": ("ff", "Fianna Fáil"),
    "FG": ("fg", "Fine Gael"),
    "LAB": ("lab", "Labour Party"),
    "SF": ("sf", "Sinn Féin"),
    "PD": ("pd", "Progressive Democrats"),
    "GP": ("gp", "Green Party"),
    "GRN": ("gp", "Green Party"),
    "NLP": ("nlp", "Natural Law Party"),
    "WP": ("wp", "Workers' Party"),
    "TWP": ("wp", "Workers' Party"),
    "CPI": ("cpi", "Communist Party of Ireland"),
    "CSP": ("csp", "Christian Solidarity Party"),
    "MNAH": ("mnah", "Muintir na hÉireann"),
    "SP": ("sp", "Socialist Party"),
    "NP": ("np-1995", "National Party (1995)"),
    "SWP": ("swp", "Socialist Workers Party"),
    "DPP": ("dpp", "Donegal Progressive Party"),
    "SKIA": ("skia", "South Kerry Independent Alliance"),
    "CD": ("cd", "Christian Democrats"),
    "PBPA": ("pbpa", "People Before Profit Alliance"),
    "SSPD": ("sspd", "Seniors Solidarity Party Dublin"),
    "SSPS": ("sspd", "Seniors Solidarity Party Dublin"),
    "WUAGST": ("wuag", "Workers and Unemployed Action Group"),
    "WUA": ("wuag", "Workers and Unemployed Action Group"),
    "LIB": ("lib", "Libertas"),
    "LRP": ("lrp", "Letterkenny Residents Party"),
    "AAA": ("aaa", "Anti-Austerity Alliance"),
    "DDI": ("ddi", "Direct Democracy Ireland"),
    "É": ("eirigi", "Éirígí"),
    "FN": ("fn", "Fís Nua"),
    "UL": ("ul", "United Left"),
    "A": ("aontu", "Aontú"),
    "ANT": ("aontu", "Aontú"),
    "HRRA": ("hrra", "Housing Rights and Reform Alliance"),
    "I4C": ("i4c", "Independents 4 Change"),
    "IDP": ("idp", "Irish Democratic Party"),
    "KIA": ("kia", "Kerry Independent Alliance"),
    "RI": ("ri", "Renua Ireland"),
    "SD": ("sd", "Social Democrats"),
    "SPBP": ("pbp-s", "People Before Profit–Solidarity"),
    "PBP": ("pbp-s", "People Before Profit–Solidarity"),
    "UP": ("up", "United People"),
    "100R": ("100r", "100% Redress"),
    "GVP": ("gvp", "Glór – Voice of the People"),
    "IF": ("if", "Ireland First"),
    "IFP": ("ifp", "Irish Freedom Party"),
    "IL": ("il", "Independent Left"),
    "INDI": ("indi", "Independent Ireland"),
    "PAW": ("paw", "Party for Animal Welfare"),
    "TIP": ("tip", "The Irish People"),
    "TNP": ("tnp", "The National Party"),
}
NON_PARTY = {"NONP"}
# Figures a volume misprints, corrected where its own other figures agree on the value
CORRECTIONS: dict[tuple[int, str], tuple[dict[str, int], str]] = {
    (2024, "ballyfermot-drimnagh"): (
        {"seats": 5},
        (
            "The volume prints 6 seats for this area, but its quota of 2,048 is the Droop quota for 5 seats on the "
            "valid poll of 12,287, it names 5 candidates elected, and its council and national seat totals count 5, "
            "so the archive stores 5 seats."
        ),
    ),
}
METHOD = (
    "The Department's results volume prints each local electoral area's count table. Each candidate's votes are the "
    "first preferences, the first count, stored as count 1, and who was elected follows the volume's list of the "
    "candidates elected. The volume prints the electorate, the invalid ballot papers, the valid poll, the seats, and "
    "the quota for each area; the ballots cast are the valid poll plus the invalid ballot papers. The candidates' "
    "first preferences add up to the valid poll and to the total the table prints."
)
SYSTEM_NOTE = (
    "Members of city and county councils are elected by the single transferable vote in multi-member local electoral "
    "areas, with the Droop quota; a surplus is transferred from the last parcel of papers that brought the "
    "candidate over the quota (Local Elections Regulations 1995)."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


VOLUME_SOURCE = key("source", "dhlgh-opendata")
TAILTE_SOURCE = key("source", "tailte")
SYSTEM = key("electoral_system", "stv")


def fetch() -> None:
    for _, url in VOLUMES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url, _, suffix in BOUNDARIES.values():
        raw.fetch(SOURCE, url, suffix=suffix)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/le-{year}" for year in YEARS)]


def parse() -> None:
    registry().write()
    geo().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---- reading the volumes -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Council:
    key: str
    name: str
    printed: str


def council(text: str) -> Council:
    """The council a table belongs to, from the name the volume prints, which changed in 2002 and 2014."""
    t = re.sub(r"\s+", " ", text.upper().replace("&", "AND").replace("CO. ", "COUNTY ")).strip()
    t = t.replace("COUNCIL COUNCIL", "COUNTY COUNCIL")
    t = re.sub(r"\s*\((?:N|S|W|E)\)$", "", t)
    if "CITY AND COUNTY" in t:
        kind = "City and County Council"
    elif "CORPORATION" in t or "CITY COUNCIL" in t:
        kind = "City Council"
    else:
        kind = "County Council"
    base = re.sub(r"\s*(CITY AND COUNTY COUNCIL|COUNTY COUNCIL|CITY COUNCIL|CORPORATION)$", "", t).strip()
    base = base.replace("TIPPERARY (NR)", "NORTH TIPPERARY").replace("TIPPERARY (SR)", "SOUTH TIPPERARY")
    if base in ("RATHDOWN", "DUN LAOGHAIRE-RATHDOWN", "DÚN LAOGHAIRE-RATHDOWN"):
        base = "DÚN LAOGHAIRE-RATHDOWN"
    name = f"{title(base)} {kind}"
    return Council(changeset.slug(name), name, text)


SMALL = {"and", "of", "na", "an"}


def title(text: str) -> str:
    """A name the volume prints in capitals, in ordinary case."""

    def word(part: str, first: bool) -> str:
        if part.lower() in SMALL and not first:
            return part.lower()
        if re.fullmatch(r"no\.?", part.lower()):
            return "No." if part.endswith(".") else "No"
        pieces = re.split(r"([-'’])", part)
        return "".join(piece.capitalize() if piece not in "-'’" else piece for piece in pieces)

    if text != text.upper():
        return text.strip()
    return " ".join(word(part, n == 0) for n, part in enumerate(text.split()))


def code_of(year: int, table: Table) -> str:
    return f"{year}:{council(table.council).key}:{changeset.slug(table.name)}"


@functools.cache
def tables(year: int) -> list[Table]:
    found = local_text.tables(raw.cached(SOURCE, VOLUMES[year][1]).path)
    for table in found:
        if not table.council:
            raise ValueError(f"{year} {table.name}: no council")
        if table.uncontested:
            continue
        votes = sum(c.votes or 0 for c in table.candidates)
        if any(c.votes is None for c in table.candidates):
            raise ValueError(f"{year} {table.name}: a candidate has no first count")
        if votes != table.figures.get("valid") or votes != table.total:
            raise ValueError(
                f"{year} {table.name}: first preferences sum to {votes:,}, the valid poll is "
                f"{table.figures.get('valid')}, the total {table.total}"
            )
    for table in found:
        fix = CORRECTIONS.get((year, changeset.slug(table.name)))
        if fix is not None:
            table.figures.update(fix[0])
    codes = [code_of(year, t) for t in found]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{year}: two areas share a code")
    return found


def party_code(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9É]", "", (text or "").upper())


def party_key(year: int, text: str | None) -> str | None:
    code = party_code(text)
    if code in NON_PARTY or not code:
        return None
    if code == "NP" and year != 1999:
        return None
    return PARTIES[code][0] if code in PARTIES else None


def party_id(year: int, text: str | None) -> str | None:
    found = party_key(year, text)
    return key("party", found) if found is not None else None


def clean(candidate: Candidate) -> tuple[str, str | None]:
    """The printed name and party, with a nickname the parser took for a party put back into the name."""
    label = re.sub(r"\s+", " ", re.sub(r"(?:^|\s)\d+(?=\s|$)", " ", candidate.label)).strip().rstrip(".")
    printed = candidate.party
    code = party_code(printed)
    if printed is not None and code not in PARTIES and code not in NON_PARTY and re.search(r"[a-z]", printed):
        surname, _, first = label.partition(",")
        label = f"{surname}, {first.strip()} ({printed})" if first else f"{label} ({printed})"
        printed = None
    return label, printed


def display(label: str) -> str:
    """A candidate as people write the name: the printed `SURNAME, Given` turned around, in ordinary case."""

    def word(text: str) -> str:
        if text.isupper() and len(text) > 1 and not re.fullmatch(r"(?:[A-Z]\.)+", text):
            text = title(text)
            return re.sub(r"^(Mc|Mac(?=[a-z]{3}))([a-z])", lambda hit: hit.group(1) + hit.group(2).upper(), text)
        return text

    if "," not in label:
        return " ".join(word(w) for w in label.split())
    surname, first = (part.strip() for part in label.split(",", 1))
    return " ".join(word(w) for w in [*first.split(), *surname.split()])


# ---- boundaries --------------------------------------------------------------------------------------------------


def _squash(text: str) -> str:
    text = re.sub(r"\s*LEA[\s-]*\d*$|\s*\(\d+\)$", "", text.strip(), flags=re.IGNORECASE)
    return local_text.fold(text)


def _seats(text: str) -> int | None:
    hit = re.search(r"LEA[\s-]*(\d+)$|\((\d+)\)$", text.strip(), flags=re.IGNORECASE)
    return int(hit.group(1) or hit.group(2)) if hit is not None else None


@functools.cache
def shapes(year: int) -> dict[str, shapely.Geometry]:
    """Each area's outline in the boundary set for an election, by the area's code."""
    url, field, _ = BOUNDARIES[year]
    body = json.loads(raw.cached(SOURCE, url).path.read_bytes())
    parts: dict[tuple[str, int | None], list[shapely.Geometry]] = {}
    for feature in body["features"]:
        name = str(feature["properties"][field])
        outline = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts.setdefault((_squash(name), _seats(name)), []).append(outline)
    pieces = {found: shapely.union_all(geoms) for found, geoms in parts.items()}
    found: dict[str, shapely.Geometry] = {}
    for table in tables(year):
        name = ALIASES.get((year, local_text.fold(table.name)), local_text.fold(table.name))
        seats = table.figures.get("seats")
        exact = [k for k in pieces if k[0] == name and k[1] == seats]
        named = [k for k in pieces if k[0] == name]
        close = sorted(((SequenceMatcher(None, name, k[0]).ratio(), k) for k in pieces), reverse=True)
        if len(exact) == 1:
            hit = exact[0]
        elif len(named) == 1:
            hit = named[0]
        elif close and close[0][0] >= 0.85 and (len(close) == 1 or close[1][0] < close[0][0] - 0.05):
            hit = close[0][1]
            print(f"{year} {table.name}: boundary {hit[0]} by a close name", flush=True)
        else:
            raise ValueError(f"{year} {table.name}: no boundary matches")
        found[code_of(year, table)] = pieces.pop(hit)
    if pieces:
        raise ValueError(f"{year}: boundaries left without an area: {sorted(pieces)[:5]}")
    return found


def _wgs(shape: shapely.Geometry, year: int) -> shapely.Geometry:
    if year in IRISH_GRID:
        project = pyproj.Transformer.from_crs(2157, 4326, always_xy=True)
        shape = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    shape = shapely.make_valid(shape)
    polygons = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(polygons))), 4326)


# ---- packages ----------------------------------------------------------------------------------------------------


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Department of Housing's local election results volumes and Tailte Éireann's boundaries as sources, the "
        "parties that stood at the 1999 to 2024 city and county council elections, the councils, and their "
        "electoral system",
    )
    p.external.add(IRELAND)
    p.add(
        "source",
        id=VOLUME_SOURCE,
        kind="official_body",
        publisher="Department of Housing, Local Government and Heritage",
        title="Local election results, transfer of votes and statistics (open data portal)",
        url="https://opendata.housing.gov.ie/dataset?q=local+election+results",
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Department of Housing, Local Government and Heritage",
    )
    p.add(
        "source",
        id=TAILTE_SOURCE,
        kind="official_body",
        publisher="Tailte Éireann",
        title="Local electoral areas, national statutory and administrative boundaries",
        url="https://data-osi.opendata.arcgis.com/",
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Tailte Éireann",
    )
    for _, url in VOLUMES.values():
        p.artifact(raw.cached(SOURCE, url), VOLUME_SOURCE)
    for url, _, _ in BOUNDARIES.values():
        p.artifact(raw.cached(SOURCE, url), TAILTE_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=IRELAND,
        valid_from=None,
        valid_to=None,
        rules={"formula": "stv", "quota": "droop", "surplus": "last_parcel"},
        rules_version=1,
    )
    p.name(SYSTEM, "Single transferable vote (local elections)", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    councils: dict[str, Council] = {}
    for year in YEARS:
        for table in tables(year):
            found = council(table.council)
            councils.setdefault(found.key, found)
            if "CORPORATION" in found.printed.upper():
                councils[f"{found.key}\x1fold"] = found
    for code, found in sorted(councils.items()):
        if code.endswith("\x1fold"):
            continue
        body = key("body", found.key)
        p.add("body", id=body, polity_id=IRELAND, kind="council")
        p.name(body, found.name, "en", "official")
        old = councils.get(f"{code}\x1fold")
        if old is not None:
            p.name(
                body, f"{title(old.printed.upper().replace(' CORPORATION', ''))} Corporation", "en", "historical", False
            )
    used = {party_key(year, clean(c)[1]) for year in YEARS for table in tables(year) for c in table.candidates} - {None}
    for party_code_key in sorted(k for k in used if k is not None):
        name = next(name for k, name in PARTIES.values() if k == party_code_key)
        party = key("party", party_code_key)
        p.add("party", id=party, kind="party", polity_id=IRELAND)
        p.name(party, name, "en", "official")
    return p


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Ireland's local electoral areas for the 1999 to 2024 city and county council elections, with Tailte "
        "Éireann's boundaries for the 2014, 2019, and 2024 areas",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {IRELAND, NATION}
    for _, url in VOLUMES.values():
        p.uses(raw.cached(SOURCE, url))
    for url, _, _ in BOUNDARIES.values():
        p.uses(raw.cached(SOURCE, url))
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=IRELAND,
        kind="electoral_district",
        rank=3,
        noun="local electoral area",
        code_system=None,
    )
    for n, year in enumerate(YEARS):
        day = VOLUMES[year][0]
        until = VOLUMES[YEARS[n + 1]][0] if n + 1 < len(YEARS) else None
        outlines = shapes(year) if year in BOUNDARIES else {}
        if outlines:
            p.add(
                "boundary_set",
                id=key("boundary_set", year),
                scheme_code=SCHEME,
                vintage=str(year),
                valid_from=day,
                valid_to=until,
                as_of=day,
            )
        for table in tables(year):
            code = code_of(year, table)
            place = area(code)
            p.add("area", id=place, scheme_code=SCHEME)
            p.add("area_code", area_id=place, code=code, valid_from=day, valid_to=until)
            p.add("area_parent", area_id=place, parent_id=NATION, valid_from=day, valid_to=until)
            p.name(place, title(table.name), "en", "official")
            if code in outlines:
                shape = _wgs(outlines[code], year)
                p.add(
                    "area_geometry",
                    area_id=place,
                    valid_from=day,
                    valid_to=until,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                    ),
                    quality="official",
                    source_id=TAILTE_SOURCE,
                )
    return p


def area(code: str) -> str:
    return key("area", SCHEME, code)


def election(year: int) -> Package:
    day, url = VOLUMES[year]
    artifact = raw.cached(SOURCE, url)
    p = Package(
        f"{SOURCE}/le-{year}",
        SOURCE,
        f"The {year} city and county council elections in Ireland, first preferences in each local electoral area",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.uses(artifact)
    p.external.add(IRELAND)
    n = YEARS.index(year)
    until = VOLUMES[YEARS[n + 1]][0] if n + 1 < len(YEARS) else None
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=IRELAND, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{year} Irish local elections", "en", "official")
    p.name(election_id, f"Toghcháin Áitiúla {year}", "ga", "native", False)
    base = f"ie/{day}-local"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    values: list[dict[str, object]] = []
    sums = {"electorate": 0, "invalid": 0, "valid": 0, "seats": 0}
    for table in tables(year):
        code = code_of(year, table)
        found = council(table.council)
        figures = table.figures
        seats = figures["seats"]
        for field in sums:
            sums[field] += figures.get(field, 0)
        contest = key("contest", day, code)
        place = area(code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=key("body", found.key),
            district_area_id=place,
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="tacit" if table.uncontested else "counted",
        )
        p.name(contest, f"{found.name}, {title(table.name)}", "en", "official")
        p.add(
            "slug",
            path=f"{base}/{found.key}/{changeset.slug(table.name)}",
            entity_id=contest,
            is_current=True,
        )
        unit = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRELAND,
            scheme_code=SCHEME,
            code=code,
            area_relation="equals",
            area_id=place,
            kind="standard",
            valid_from=day,
            valid_from_precision="day",
            valid_to=until,
            valid_to_precision="day" if until else None,
        )
        result_set = key("result_set", day, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="tacit" if table.uncontested else "counted",
            source_id=VOLUME_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = (
            "No poll was taken: the candidates nominated equalled the seats, and the volume prints them as declared "
            "elected without a poll."
            if table.uncontested
            else METHOD
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        fix = CORRECTIONS.get((year, changeset.slug(table.name)))
        if fix is not None:
            p.add(
                "note",
                id=key("note", result_set, "fix"),
                entity_id=result_set,
                kind="caveat",
                body_md=fix[1],
                lang="en",
            )
        for measure, field in ((REGISTERED, "electorate"), (INVALID, "invalid"), (VALID, "valid"), (QUOTA, "quota")):
            if field not in figures:
                continue
            values.append(fact(result_set, unit, None, measure, 0, figures[field]))
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=figures[field])
        if "valid" in figures and "invalid" in figures:
            values.append(fact(result_set, unit, None, BALLOTS, 0, figures["valid"] + figures["invalid"], DERIVED))
        elected = [local_text.match(name, table.candidates) for name in table.elected]
        winners = {id(c) for c in elected if c is not None}
        complete = len(winners) == seats and None not in elected and len(elected) == seats
        if not complete:
            listed = len(winners)
            p.add(
                "note",
                id=key("note", result_set, "elected"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The volume's list of the candidates elected names {listed} of the area's {seats} members"
                    + ("" if listed else ", and names candidates of another area")
                    + ", so the archive records only those it names as elected and no outcome for the others."
                ),
                lang="en",
            )
        tags: set[str] = set()
        for candidate in table.candidates:
            label, printed = clean(candidate)
            tag = f"{day}:{code}:{local_text.fold(label)}:{party_code(printed)}"
            if tag in tags:
                raise ValueError(f"{year} {table.name}: {label} is printed twice")
            tags.add(tag)
            option = key("option", tag)
            person = key("person", tag)
            member = party_id(year, printed)
            code_printed = party_code(printed)
            independent = True if code_printed in NON_PARTY or printed is None else (False if member else None)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_label=f"{label} ({printed})" if printed else label,
            )
            p.add("person", id=person, gender={"M": "male", "F": "female"}.get(candidate.gender or ""))
            p.name(person, display(re.sub(r"\s*\([^()]*\)", "", label)), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=member,
                is_independent=independent,
                is_incumbent=candidate.outgoing,
            )
            if id(candidate) in winners or table.uncontested:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            elif complete:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
            if candidate.votes is not None:
                values.append(fact(result_set, unit, option, VOTES, 1, candidate.votes))
    p.frame("result_value", pl.DataFrame(values, schema=package.FACTS["result_value"], orient="row"))
    print(f"{year}: {len(tables(year))} areas, " + ", ".join(f"{k} {v:,}" for k, v in sums.items()), flush=True)
    return p


def fact(
    result_set: str, unit: str, option: str | None, measure: int, count_round: int, value: int, flags: int = 0
) -> dict[str, object]:
    return {
        "result_set_id": result_set,
        "unit_id": unit,
        "option_id": option,
        "channel": 0,
        "measure": measure,
        "count_round": count_round,
        "flags": flags,
        "value": value,
    }
