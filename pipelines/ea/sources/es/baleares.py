"""Parlament de les Illes Balears elections, 1983 to 2023, from the Govern's result PDFs.

The Govern de les Illes Balears publishes one PDF per election on its electoral-processes site. Each holds, for the
whole community and for each of the four island constituencies (Mallorca, Menorca, Eivissa and Formentera), the
electorate, abstention, ballots cast, null, valid and blank ballots, and every list's votes, share and seats. The
PDFs carry a text layer and credit IBESTAT as the source. They go no finer than the island, so the island is the
reporting unit; the PDFs hold no municipal or polling-station breakdown.

Shapes for the four constituencies are the union of the municipalities of each island in geoBoundaries' Spain ADM3
set (CC BY 4.0, from the Instituto Geográfico Nacional's municipal boundaries).
"""

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "es.baleares"
PAGE = "https://www.caib.es/sites/processoselectorals/ca/resultats_al_parlament_-64479/"
PUBLISHER = "Govern de les Illes Balears"
TITLE = "Eleccions al Parlament de les Illes Balears: resultats (Direcció General d'Interior i IBESTAT)"
STATUTE = "Ley Orgánica 2/1983, de 25 de febrero, de Estatuto de Autonomía de las Illes Balears"
SPAIN = str(uuid5(package.NAMESPACE, "polity\x1fES"))
REGION = str(uuid5(package.NAMESPACE, "polity\x1fES-IB"))
# The province's area and reporting unit that the archive already holds (es.ine and es.referendums).
PROVINCE_AREA = "e9beb324-6348-5663-8077-5ebbcd4a00e1"
PROVINCE_UNIT = "48d7e463-2475-5da0-8dcd-bc5b37ffb674"
GEOBOUNDARIES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ESP/ADM3/"
    "geoBoundaries-ESP-ADM3.geojson"
)
SCHEME = "es.circ_baleares"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
THRESHOLD = 5
# The result PDF of each election, by the id the site gives its link.
IDS = {
    1983: 281347,
    1987: 281203,
    1991: 281344,
    1995: 281287,
    1999: 281274,
    2003: 281254,
    2007: 281255,
    2011: 281340,
    2015: 281213,
    2019: 417042,
    2023: 550820,
}
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
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
}
ISLANDS = {
    "MALLORCA": ("mallorca", "Mallorca"),
    "MENORCA": ("menorca", "Menorca"),
    "EIVISSA": ("eivissa", "Eivissa"),
    "FORMENTERA": ("formentera", "Formentera"),
}
SEATS = {
    1983: {"mallorca": 30, "menorca": 12, "eivissa": 11, "formentera": 1},
    1987: {"mallorca": 33, "menorca": 13, "eivissa": 12, "formentera": 1},
}
SYSTEM = (
    "Closed party lists in four island constituencies: Mallorca, Menorca, Eivissa and Formentera. Seats go by the "
    "D'Hondt method, in each island, to the lists with at least 5% of the valid votes cast there, blank ballots "
    "counted as valid. The Parlament has had 54 members since 1983 (30 for Mallorca, 12 for Menorca, 11 for Eivissa "
    "and 1 for Formentera) and 59 since 1987 (33, 13, 12 and 1) (" + STATUTE + ")."
)
METHOD = (
    "Counted per island constituency, as the Govern de les Illes Balears publishes it from IBESTAT's tables. "
    "Registered voters are the electoral roll, ballots cast are the ballots in the boxes, null ballots are stored as "
    "invalid, and blank ballots are stored apart. Valid votes here are the votes to candidacies, so the ballots cast "
    "equal valid plus blank plus null. The PDF goes no finer than the island."
)

STATS = (
    ("registered", re.compile(r"Cens electoral")),
    ("abstention", re.compile(r"Abstenci[óo]")),
    ("ballots", re.compile(r"Vot[o]? em[éèe]s|Voto emitido")),
    ("invalid", re.compile(r"Vot nul|Voto nulo")),
    ("valid", re.compile(r"Vot[o]? v[àa]lid")),
    ("blank", re.compile(r"Vot en blanc|Voto en blanco")),
    ("candidacies", re.compile(r"Vot[o]? a candidatura")),
)
STAT = re.compile(
    r"^\s*(Cens electoral|Abstenci[óo]|Vot[o]? em[éèe]s|Voto emitido|Vot nul|Voto nulo|Vot[o]? v[àa]lid|Vot en blanc"
    r"|Voto en blanco|Vot[o]? a candidatura)\s+([\d.]+)(?:\s+([\d,]+)\s*%?)?"
)
ROW = re.compile(r"^(.*?\S)\s+([\d.]+)\s+(\d+(?:,\d+)?)\s*%?(?:\s+(\d+))?\s*$")
NUMS = re.compile(r"^\s*([\d.]+)\s+(\d+(?:,\d+)?)\s*%?(?:\s+(\d+))?\s*$")
# The 2019 Formentera list is printed over two lines with the electorate between them.
FORMENTERA_2019 = "GxF+PSIB-PSOE+EUIB - GENT PER FORMENTERA+PARTIT SOCIALISTA DE LES ILLES BALEARS+ESQUERRA UNIDA DE LES ILLES BALEARS"

# Lists the PDFs print under several spellings or abbreviations, as one source-local party each.
ALIASES = {
    "pp": ("pp", "p-p"),
    "psoe": ("psoe", "p-s-o-e", "psib-psoe"),
    "um": ("um",),
    "upyd": ("upyd", "upd"),
    "asi": ("asi", "a-s-i"),
    "plie": ("plie", "p-li-e"),
    "pref": ("p-r-e-f",),
    "mc-epic": ("mc-epic", "m-c-epic"),
    "mxme": ("mpm", "mxme", "mxme"),
    "mes": ("mes",),
    "el-pi": ("el-pi",),
    "cs": ("cs", "c-s"),
    "vox": ("vox", "vox-actua-baleares"),
    "sa-unio": ("sa-unio",),
    "gxf-psoe": ("gxf-psoe", "psoe-gxf", "gxf-psib-psoe"),
    "erc": ("erc", "esquerra-republicana"),
    "td": ("td",),
    "upb": ("upb", "u-p-b"),
    "uc": ("uc", "u-c"),
    "ucm": ("ucm", "u-c-m"),
    "prb": ("prb", "prb-i"),
    "cenb": ("cenb", "c-en-b"),
    "gve": ("gve", "g-v-e"),
    "evme": ("e-v-me",),
    "clau": ("clau", "cm"),
    "podemos-euib": ("podemos-euib", "euib-podemos"),
    "aipf": ("aipf",),
    "ene": ("ene",),
    "fief": ("fief",),
    "pacma": ("pacma",),
    "progreso-en-verde-pacma": ("progreso-en-verde-pacma",),
}
ALIAS = {form: key for key, forms in ALIASES.items() for form in forms}
# Abbreviations two different lists use, kept apart by the full label.
BY_LABEL = {"iu", "psm", "pacte", "cop", "ppg", "tdp"}


@dataclass
class Row:
    label: str
    votes: int
    seats: int


@dataclass
class Block:
    stats: dict[str, int]
    rows: list[Row]


def pdf_url(year: int) -> str:
    return f"{PAGE}archivopub.do?ctrl=MCRST10385ZI{IDS[year]}&id={IDS[year]}"


def fetch() -> None:
    for year in IDS:
        raw.fetch(SOURCE, pdf_url(year), suffix=".pdf")
    raw.fetch(SOURCE, GEOBOUNDARIES, suffix=".geojson")


def text(year: int) -> str:
    path = raw.cached(SOURCE, pdf_url(year)).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def number(text_: str) -> int:
    return int(text_.replace(".", ""))


def stat_key(name: str) -> str:
    return next(key for key, pattern in STATS if pattern.match(name))


def parse_page(page: str, year: int) -> tuple[str, Block] | None:
    lines = page.split("\n")
    if not any("CANDIDATURES" in line for line in lines):
        return None
    names = [line.strip() for line in lines[:12] if line.strip()]
    where = next((n for n, line in enumerate(names) if re.fullmatch(r"[ÀÁ]MBIT (INSULAR|AUTON[ÒO]MIC)", line)), None)
    if where is None:
        raise ValueError(f"{year}: page without a scope {names[:3]!r}")
    scope = names[where + 1]
    if scope not in ISLANDS and scope != "ILLES BALEARS":
        raise ValueError(f"{year}: unknown scope {scope!r}")
    stats: dict[str, int] = {}
    rows: list[Row] = []
    extra: list[str] = []
    for line in lines:
        found = STAT.match(line)
        rest = line
        if found:
            stats[stat_key(found.group(1))] = number(found.group(2))
            rest = re.sub(r"^\s*%?\s*", "", line[found.end() :])
        numbers = NUMS.match(line) if not found else None
        if numbers and extra and extra[-1] and "CANDIDATURES" not in extra[-1]:
            name = extra.pop()
            rows.append(Row(name.removeprefix("L:").strip(), number(numbers.group(1)), int(numbers.group(3) or 0)))
            continue
        row = ROW.match(rest.strip()) if rest.strip() else None
        if row and "CANDIDATURES" not in rest:
            rows.append(Row(row.group(1).strip(), number(row.group(2)), int(row.group(4) or 0)))
        elif rest.strip() and "CANDIDATURES" not in rest:
            extra.append(("L:" if found else "") + rest.strip())
    # Seat numbers printed on the pie charts can read as rows.
    return scope, Block(stats, [row for row in rows if not row.label.isdigit() and not re.match(r"\d+\s", row.label)])


@dataclass
class Election:
    year: int
    islands: dict[str, Block]
    community: dict[str, int]


def load(year: int) -> Election:
    islands: dict[str, Block] = {}
    community: dict[str, int] = {}
    for page in text(year).split("\f"):
        parsed = parse_page(page, year)
        if parsed is None:
            continue
        scope, block = parsed
        if scope == "ILLES BALEARS":
            community = block.stats
            continue
        if year == 2019 and scope == "FORMENTERA":
            first = block.rows[0]
            block.stats["registered"] = number(first.label)
            block.rows[0] = Row(FORMENTERA_2019, first.votes, first.seats)
        islands[ISLANDS[scope][0]] = block
    if sorted(islands) != sorted(code for code, _ in ISLANDS.values()):
        raise ValueError(f"{year}: islands {sorted(islands)}")
    for code, block in islands.items():
        stats, rows = block.stats, block.rows
        if (
            sum(r.votes for r in rows) != stats["candidacies"]
            or stats["registered"] - stats["abstention"] != stats["ballots"]
            or stats["ballots"] != stats["invalid"] + stats["valid"]
            or stats["valid"] != stats["blank"] + stats["candidacies"]
        ):
            raise ValueError(f"{year} {code}: figures do not add up {stats} {sum(r.votes for r in rows)}")
        if year in SEATS and sum(r.seats for r in rows) != SEATS[year][code]:
            raise ValueError(f"{year} {code}: seats {sum(r.seats for r in rows)}")
    return Election(year, islands, community)


def split(label: str) -> tuple[str, str]:
    """A list's abbreviation and name from its printed label, `ABBR - NAME` or, from 2023, `Name (ABBR)`."""
    named = re.fullmatch(r"(.+) \(([^()]+)\)", label)
    if named and re.search(r"[a-zà-ÿ]", named.group(1)):
        return named.group(2), named.group(1)
    if " - " in label:
        abbreviation, name = label.split(" - ", 1)
        return abbreviation.strip(), name.strip()
    return label, label


def party_key(label: str) -> str:
    abbreviation, _ = split(label)
    key = slug(abbreviation)
    if key in BY_LABEL:
        return slug(label)[:60]
    return ALIAS.get(key, key)


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {k: v for k, v in votes.items() if v * 100 >= THRESHOLD * valid}
    quotients = sorted(((v / d, v, k) for k, v in eligible.items() for d in range(1, seats + 1)), reverse=True)
    return Counter(k for *_, k in quotients[:seats])


def source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fcaib"))


def geo_source_id() -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fgeoboundaries"))


def area_id(code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1farea\x1f{SCHEME}\x1f{code}"))


def unit_id(code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1funit\x1f{SCHEME}\x1f{code}"))


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        attribution="Font: Govern de les Illes Balears, a partir de les dades de l'Institut d'Estadística de les Illes Balears (IBESTAT).",
    )


def outlines() -> dict[str, shapely.MultiPolygon]:
    """Each island's outline, the union of its municipalities in geoBoundaries: Formentera by name, and the rest
    of the Balearic municipalities by where they lie."""
    features = json.loads(raw.cached(SOURCE, GEOBOUNDARIES).path.read_bytes())["features"]
    parts: dict[str, list[shapely.Geometry]] = {code: [] for code, _ in ISLANDS.values()}
    for feature in features:
        shape = shapely.geometry.shape(feature["geometry"])
        x, y = shape.centroid.x, shape.centroid.y
        if not (1.0 < x < 4.5 and 38.5 < y < 40.2):
            continue
        name = str(feature["properties"]["shapeName"])
        if x < 1.7:
            code = "formentera" if name == "Formentera" else "eivissa"
        elif x < 3.6:
            code = "mallorca"
        else:
            code = "menorca"
        parts[code].append(shapely.make_valid(shape))
    expected = {"mallorca": 53, "menorca": 8, "eivissa": 5, "formentera": 1}
    if {code: len(found) for code, found in parts.items()} != expected:
        raise ValueError(f"municipalities per island: { {c: len(f) for c, f in parts.items()} }")
    found: dict[str, shapely.MultiPolygon] = {}
    for code, geoms in parts.items():
        union = shapely.union_all(geoms).simplify(0.0003)
        polygons = [
            q
            for part in shapely.get_parts(union)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        found[code] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The four island constituencies of the Parlament de les Illes Balears (Mallorca, Menorca, Eivissa and "
        "Formentera) as areas and reporting units, with outlines from geoBoundaries",
    )
    p.external |= {SPAIN, PROVINCE_AREA, PROVINCE_UNIT}
    p.add(
        "source",
        id=geo_source_id(),
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen Spain ADM3 (municipalities), 2018",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab), from the Instituto Geográfico Nacional's municipal boundaries",
    )
    p.artifact(raw.cached(SOURCE, GEOBOUNDARIES), geo_source_id())
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=SPAIN,
        kind="electoral_district",
        rank=3,
        noun="island constituency",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=p.id("boundary_set", SCHEME, "2018"),
        scheme_code=SCHEME,
        vintage="2018",
        valid_from=DAYS[1983],
        as_of=date(2018, 1, 1),
    )
    shapes = outlines()
    for code, name in ISLANDS.values():
        area = area_id(code)
        p.add("area", id=area, scheme_code=SCHEME)
        p.add("area_code", area_id=area, code=code)
        p.name(area, name, "ca", "official")
        p.add("area_parent", area_id=area, parent_id=PROVINCE_AREA)
        shape = shapes[code]
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=DAYS[1983],
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=geo_source_id(),
        )
        p.add(
            "reporting_unit",
            id=unit_id(code),
            polity_id=SPAIN,
            scheme_code=SCHEME,
            code=code,
            parent_id=PROVINCE_UNIT,
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
    return p


def elections() -> list[Election]:
    return [load(year) for year in DAYS]


def labels(all_: list[Election]) -> dict[str, tuple[str, str]]:
    """Each party's key with its name as the latest PDF printing it gives it, and its abbreviation."""
    found: dict[str, tuple[str, str]] = {}
    for e in sorted(all_, key=lambda e: -e.year):
        for block in e.islands.values():
            for row in block.rows:
                abbreviation, name = split(row.label)
                found.setdefault(party_key(row.label), (name, abbreviation))
    return found


def registry(all_: list[Election]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Parlament de les Illes Balears, its electoral system and size, the Govern's result PDFs as a source, "
        "and the lists that stood in the elections of 1983 to 2023",
    )
    p.external.add(REGION)
    add_source(p)
    body = p.id("body", "parlament")
    p.add("body", id=body, polity_id=REGION, kind="unicameral")
    p.name(body, "Parliament of the Balearic Islands", "en", "official")
    p.name(body, "Parlament de les Illes Balears", "ca", "native")
    p.add("body_size", body_id=body, seats=54, valid_from=DAYS[1983], valid_to=DAYS[1987])
    p.add("body_size", body_id=body, seats=59, valid_from=DAYS[1987], valid_to=None)
    system = p.id("electoral_system", "parlament")
    p.add(
        "electoral_system",
        id=system,
        polity_id=REGION,
        rules={
            "formula": "dhondt",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD},
        },
        rules_version=1,
    )
    p.name(
        system,
        "Parliament of the Balearic Islands island-constituency D'Hondt system with a 5% threshold",
        "en",
        "official",
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM, lang="en")
    for key, (name, _) in sorted(labels(all_).items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="party", polity_id=REGION)
        p.name(party, name, "ca", "official")
        p.add("external_id", entity_id=party, scheme=f"{SOURCE}.party", value=key)
    return p


def build(e: Election) -> Package:
    year, day = e.year, DAYS[e.year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} election to the Parlament de les Illes Balears, with the votes and seats of every list in each "
        "island constituency",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {SPAIN, REGION}
    src = source_id()
    p.artifact(raw.cached(SOURCE, pdf_url(year)), src)
    body, system = p.id("body", "parlament"), p.id("electoral_system", "parlament")
    eid = p.id("election", day)
    p.add("election", id=eid, polity_id=REGION, kind="general", date_from=day, date_to=day, status="held")
    p.name(eid, f"{year} Balearic regional election", "en", "official")
    p.name(eid, f"Eleccions al Parlament de les Illes Balears de {year}", "ca", "native")
    path = f"es/ib/{day}-general"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    values: list[tuple[str, str, str | None, int, int, int]] = []
    totals = Counter[str]()
    for code, name in ISLANDS.values():
        block = e.islands[code]
        stats = block.stats
        magnitude = sum(r.seats for r in block.rows)
        contest = p.id("contest", str(day), code)
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="list_allocation",
            body_id=body,
            district_area_id=area_id(code),
            magnitude=magnitude,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, name, "ca", "official")
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", str(day), code)
        options: dict[str, str] = {}
        votes: dict[str, int] = {}
        for row in sorted(block.rows, key=lambda r: -r.votes):
            key = party_key(row.label)
            if key in options:
                raise ValueError(f"{year} {code}: {key} twice")
            options[key] = p.id("option", f"{day}:{code}:{key}")
            votes[key] = row.votes
            p.add(
                "contest_option",
                id=options[key],
                contest_id=contest,
                kind="list",
                party_id=p.id("party", key),
                ballot_label=row.label,
            )
        note = METHOD
        won = dhondt(votes, stats["valid"], magnitude)
        declared = {party_key(r.label): r.seats for r in block.rows}
        agree = all(won[k] == declared[k] for k in options)
        if not agree:
            note += (
                " D'Hondt with a 5% threshold applied to these votes gives a different split from the declared "
                "seats, which are stored as published."
            )
        print(f"{year} {code} seats={magnitude} lists={len(options)} dhondt_ok={agree}")
        p.add(
            "result_set",
            id=result_set,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=src,
            is_default=True,
            completeness=1,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        unit = unit_id(code)
        values += [
            (result_set, unit, None, REGISTERED, stats["registered"], 0),
            (result_set, unit, None, BALLOTS, stats["ballots"], 0),
            (result_set, unit, None, VALID, stats["candidacies"], 0),
            (result_set, unit, None, INVALID, stats["invalid"], 0),
            (result_set, unit, None, BLANK, stats["blank"], 0),
        ]
        for key, option in options.items():
            values.append((result_set, unit, option, VOTES, votes[key], 0))
            if declared[key]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=declared[key])
        for field, value in stats.items():
            totals[field] += value
    check_community(e, totals)
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


def check_community(e: Election, totals: Counter[str]) -> None:
    """The four islands' figures against the community totals the same PDF prints."""
    off = {k: (totals[k], v) for k, v in e.community.items() if totals[k] != v}
    print(f"{e.year} community totals {'agree' if not off else 'differ: ' + str(off)}")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    all_ = elections()
    geo().write()
    registry(all_).write()
    for e in all_:
        build(e).write()


def emit() -> None:
    package.run("load", packages())
