"""Belgium's popular consultation of 12 March 1950 on the return of King Leopold III, by electoral arrondissement.

The Interior Minister sent the complete results to the Senate on 13 March 1950 under article 5 of the law of
11 February 1950. Senate document no. 208 prints them as a scanned table, one row for each of the 30 electoral
arrondissements and a row for the Kingdom. Each figure was read from the page image and keeps its page and box.
"""

import json
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import numpy
import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.be import nis
from ea.sources.be.vlaanderen import PLANS, SECTORS, TOLERANCE

SOURCE = "be.senat1950"
BELGIUM = str(uuid5(package.NAMESPACE, "polity\x1fBE"))
URL = "https://www.senate.be/lexdocs/S0712/S07122278.pdf"
DAY = date(1950, 3, 12)
KEY = f"{SOURCE}/{DAY}"
SCHEME = "be.arrondissement_1950"
BOXES = Path(__file__).with_name("senat1950_boxes.json")
VOTES, CAST, VALID, INVALID = 1, 3, 4, 5
DIGITIZED = 8
GEO = f"{SOURCE}/geo"
STATBEL = "be.statbel"
TERRITORY = nis.TERRITORY
# the municipal plan whose municipalities and arrondissements are dissolved into the 1950 arrondissements
PLAN = "2018"
UNTIL = date(1950, 3, 13)


class Row(NamedTuple):
    dutch: str
    french: str
    cast: int
    blank_null: int  # "bulletins blancs et nuls": blank and spoiled ballots printed together
    valid: int
    yes: int
    no: int


# Each row is read from the page image of the table, in printed order (pages 2 and 3 of the document).
ROWS = [
    Row(*row)
    for row in [
        ("Antwerpen", "Anvers", 484936, 12236, 472700, 297863, 174837),
        ("Mechelen", "Malines", 156099, 3905, 152194, 106450, 45744),
        ("Turnhout", "Turnhout", 134684, 3678, 131006, 110576, 20430),
        ("Brussel", "Bruxelles", 832087, 26773, 805314, 387914, 417400),
        ("Leuven", "Louvain", 197540, 7530, 190010, 125944, 64066),
        ("Nijvel", "Nivelles", 119686, 3097, 116589, 43777, 72812),
        ("Brugge", "Bruges", 119573, 3677, 115896, 83623, 32273),
        ("Veurne-Diksmuide-Oostende", "Furnes-Dixmude-Ostende", 118265, 4615, 113650, 82652, 30998),
        ("Roeselare-Tielt", "Roulers-Tielt", 116945, 3372, 113573, 96196, 17377),
        ("Kortrijk", "Courtrai", 167772, 5163, 162609, 114198, 48411),
        ("Ieper", "Ypres", 74101, 3011, 71090, 54109, 16981),
        ("Gent-Eeklo", "Gand-Eeklo", 328298, 9792, 318506, 224874, 93632),
        ("Sint-Niklaas", "Saint-Nicolas", 111048, 1833, 109215, 84955, 24260),
        ("Dendermonde", "Termonde", 97756, 2150, 95606, 72223, 23383),
        ("Aalst", "Alost", 145994, 2924, 143070, 100130, 42940),
        ("Oudenaarde", "Audenarde", 73500, 2371, 71125, 47607, 23522),
        ("Bergen", "Mons", 162250, 4467, 157783, 49243, 108540),
        ("Zinnik", "Soignies", 103952, 2565, 101387, 34875, 66512),
        ("Charleroi", "Charleroi", 268375, 7209, 261166, 86003, 175163),
        ("Thuin", "Thuin", 82540, 2294, 80246, 34529, 45717),
        ("Doornik-Aat", "Tournai-Ath", 147992, 4056, 143936, 62661, 81275),
        ("Luik", "Liège", 353598, 10095, 343503, 119161, 224342),
        ("Hoei-Borgworm", "Huy-Waremme", 109286, 3132, 106154, 44445, 61709),
        ("Verviers", "Verviers", 141253, 4998, 136255, 81238, 55017),
        ("Hasselt", "Hasselt", 109472, 3513, 105959, 87241, 18718),
        ("Tongeren-Maaseik", "Tongres-Maaseik", 124333, 3616, 120717, 101783, 18934),
        ("Aarlen-Marche-Bastenaken", "Arlon-Marche-Bastogne", 72526, 2078, 70448, 46296, 24152),
        ("Neufchâteau-Virton", "Neufchâteau-Virton", 59223, 1595, 57628, 37443, 20185),
        ("Namen", "Namur", 135600, 3495, 132105, 64112, 67993),
        ("Dinant-Philippeville", "Dinant-Philippeville", 88056, 2237, 85819, 51261, 34558),
    ]
]
# The row "Le Royaume - Het Rijk": ballots cast, blank and null, valid, yes, no.
KINGDOM = Row("Het Rijk", "Le Royaume", 5236740, 151477, 5085263, 2933382, 2151881)
METHOD = (
    "Results of the non-binding popular consultation on the Royal Question, held on 12 March 1950 under the law of "
    "11 February 1950, as the Minister of the Interior communicated them to the Senate on 13 March 1950 (Senate "
    "document no. 208). They are the figures by electoral arrondissement that match those the presidents of the "
    "main polling boards proclaimed, final unless a complaint reached the Council of State within three days. "
    "Digitized from a scanned table: every figure keeps its page and bounding box. The table prints blank and "
    "spoiled ballots as one column, stored here as invalid ballots. The percentages it prints are not stored. "
    "The Kingdom row prints 5,085,263 valid ballots, which equals ballots cast minus blank and null ballots; the "
    "arrondissement rows add up to 5,085,259, because the Oudenaarde row prints 71,125 valid ballots where its "
    "own yes and no votes add up to 71,129. Both figures are kept as printed."
)


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def packages() -> list[str]:
    return [GEO, KEY]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")
    raw.fetch(STATBEL, SECTORS + PLANS[PLAN][0], suffix=".zip")


def parse() -> None:
    for row in ROWS:
        if row.yes + row.no != row.cast - row.blank_null and row.dutch != "Oudenaarde":
            raise ValueError(f"{row.dutch}: yes and no do not add up to the ballots cast less blank and null")
    for i, name in enumerate(Row._fields[2:], 2):
        summed = sum(int(row[i]) for row in ROWS)
        if name == "valid":
            if summed + 4 != KINGDOM[i]:
                raise ValueError(
                    f"valid: rows add up to {summed}, expected the printed total less the Oudenaarde misprint"
                )
        elif summed != KINGDOM[i]:
            raise ValueError(f"{name}: rows add up to {summed}, not the printed {KINGDOM[i]}")
    geo().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


# Each 1950 arrondissement is the union of the administrative arrondissements that the law of 1950 merged for the
# Chamber, keyed by the plain French name Statbel gives them. Brussels-Capital and Halle-Vilvoorde vote as Brussels.
MERGED = {
    "anvers": "Antwerpen",
    "malines": "Mechelen",
    "turnhout": "Turnhout",
    "bruxelles-capitale": "Brussel",
    "hal-vilvorde": "Brussel",
    "louvain": "Leuven",
    "nivelles": "Nijvel",
    "bruges": "Brugge",
    "dixmude": "Veurne-Diksmuide-Oostende",
    "ostende": "Veurne-Diksmuide-Oostende",
    "furnes": "Veurne-Diksmuide-Oostende",
    "roulers": "Roeselare-Tielt",
    "tielt": "Roeselare-Tielt",
    "courtrai": "Kortrijk",
    "ypres": "Ieper",
    "gand": "Gent-Eeklo",
    "eeklo": "Gent-Eeklo",
    "saint-nicolas": "Sint-Niklaas",
    "termonde": "Dendermonde",
    "alost": "Aalst",
    "audenarde": "Oudenaarde",
    "mons": "Bergen",
    "soignies": "Zinnik",
    "la-louviere": "Zinnik",
    "charleroi": "Charleroi",
    "thuin": "Thuin",
    "tournai-mouscron": "Doornik-Aat",
    "ath": "Doornik-Aat",
    "liege": "Luik",
    "huy": "Hoei-Borgworm",
    "waremme": "Hoei-Borgworm",
    "verviers": "Verviers",
    "hasselt": "Hasselt",
    "tongres": "Tongeren-Maaseik",
    "maaseik": "Tongeren-Maaseik",
    "arlon": "Aarlen-Marche-Bastenaken",
    "bastogne": "Aarlen-Marche-Bastenaken",
    "marche-en-famenne": "Aarlen-Marche-Bastenaken",
    "neufchateau": "Neufchâteau-Virton",
    "virton": "Neufchâteau-Virton",
    "namur": "Namen",
    "dinant": "Dinant-Philippeville",
    "philippeville": "Dinant-Philippeville",
}
# Municipalities that lay in another arrondissement in 1950 than in the municipal plan. The language-border law of 1962
# moved the first two from West Flanders to Hainaut and Fourons from Liège to Limburg.
MOVED = {"Mouscron": "Kortrijk", "Comines-Warneton": "Ieper", "Fourons": "Verviers"}
SHAPES_NOTE = (
    "The outline is reconstructed. Statbel's statistical sectors of the 2018 municipal plan are dissolved to "
    "municipalities, assigned to the arrondissements of the Chamber law of 1950 through the administrative "
    "arrondissements that law merged, and dissolved again. Mouscron, Comines-Warneton, and Fourons, which the "
    "language-border law of 1962 moved between provinces, are placed where they lay in 1950. Municipal mergers of "
    "1977 and other small post-1950 changes to arrondissement borders are not reversed, so an edge can differ from "
    "the 1950 one by part of a municipality."
)


def _arrondissements() -> dict[str, list[shapely.Geometry]]:
    """The plan's municipalities, dissolved into the 30 arrondissements by code, in the plan's CRS."""
    reader, _ = nis._reader(PLAN, True)
    pieces: dict[str, list[shapely.Geometry]] = {}
    where: dict[str, tuple[str, str]] = {}
    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record is None or shape is None:
            continue
        row = dict(record.as_dict())
        code = nis._column(row, "CNIS5_")
        pieces.setdefault(code, []).append(shapely.geometry.shape(shape.__geo_interface__))
        where[code] = (slug(re.sub(r"^Arrondissement d[e’']\s*", "", str(row["T_ARRD_FR"]))), str(row["T_MUN_FR"]))
    grouped: dict[str, list[shapely.Geometry]] = {}
    for code, parts in pieces.items():
        arrondissement, town = where[code]
        if arrondissement not in MERGED:
            raise ValueError(f"{town}: the arrondissement {arrondissement!r} has no 1950 arrondissement")
        target = MOVED.get(town, MERGED[arrondissement])
        grouped.setdefault(slug(target), []).append(shapely.make_valid(shapely.coverage_union_all(parts)))
    if set(grouped) != {slug(row.dutch) for row in ROWS}:
        raise ValueError("the dissolved arrondissements are not the 30 of 1950")
    return grouped


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The 30 electoral arrondissements of Belgium in 1950, dissolved from Statbel's municipal boundaries",
    )
    p.external |= {BELGIUM, TERRITORY, SCHEME}  # the scheme row belongs to the 1950-03-12 package
    statbel = p.id("source", "statbel")
    p.add(
        "source",
        id=statbel,
        kind="official_body",
        publisher="Statbel (Algemene Directie Statistiek)",
        title="Statistical sectors (Shapefile)",
        url="https://statbel.fgov.be/en/open-data",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Statbel (Belgium)",
    )
    p.artifact(raw.cached(STATBEL, SECTORS + PLANS[PLAN][0]), statbel)
    p.add(
        "boundary_set",
        id=p.id("boundary_set", SCHEME),
        scheme_code=SCHEME,
        vintage="1950",
        valid_from=DAY,
        valid_to=UNTIL,
        as_of=DAY,
    )
    grouped = _arrondissements()
    codes = sorted(grouped)
    _, crs = nis._reader(PLAN, False)
    merged = [shapely.make_valid(shapely.coverage_union_all(grouped[code])) for code in codes]
    simple = shapely.make_valid(shapely.coverage_simplify(numpy.array(merged), TOLERANCE))
    rows = {slug(row.dutch): row for row in ROWS}
    for code, shape in zip(codes, simple, strict=True):
        id = p.id("area", code)
        row = rows[code]
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=code, valid_from=DAY, valid_to=UNTIL)
        p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=DAY, valid_to=UNTIL)
        p.name(id, row.dutch, "nl", "native", False)
        p.name(id, row.french, "fr", "native", False)
        p.name(id, f"{row.dutch} / {row.french}" if row.dutch != row.french else row.dutch, "en", "official")
        wgs = nis._wgs(shape, crs)
        point = shapely.set_srid(shapely.point_on_surface(wgs), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=DAY,
            valid_to=UNTIL,
            geom=shapely.to_wkb(wgs, hex=True, include_srid=True),
            label_point=shapely.to_wkb(point, hex=True, include_srid=True),
            quality="official",
            source_id=statbel,
        )
    return p


def sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "99", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def build() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The popular consultation of 12 March 1950 on the Royal Question by electoral arrondissement, digitized from "
        "the Senate's printed results",
        depends=[GEO],
    )
    p.external.add(BELGIUM)
    p.add("scheme", code=SCHEME, polity_id=BELGIUM, kind="electoral_district", rank=1, noun="electoral arrondissement")
    source = p.id("source")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Sénat de Belgique / Belgische Senaat (communication of the Minister of the Interior)",
        title="Consultation populaire du 12 mars 1950 au sujet de la Question Royale: résultats complets (document no 208)",
        url=URL,
        license="No license stated: official parliamentary document; facts published with attribution",
        attribution="Sénat de Belgique, document parlementaire no 208, session 1949-1950",
    )
    stored = raw.cached(SOURCE, URL)
    artifact = p.artifact(stored, source)

    election = p.id("election")
    p.add(
        "election",
        id=election,
        polity_id=BELGIUM,
        kind="referendum",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "1950 Belgian popular consultation on the Royal Question", "en", "official")
    p.name(election, "Consultation populaire du 12 mars 1950 au sujet de la Question Royale", "fr", "native", False)
    p.name(election, "Volksraadpleging van 12 maart 1950 over de Koningskwestie", "nl", "native", False)
    base = f"be/{DAY}-consultation"
    p.add("slug", path=base, entity_id=election, is_current=True)

    system = p.id("electoral_system")
    p.add("electoral_system", id=system, polity_id=BELGIUM, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=p.id("note", system),
        entity_id=system,
        kind="methodology",
        body_md="A non-binding consultation: the option with most valid votes carries.",
        lang="en",
    )
    contest = p.id("contest")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=system,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="plebiscite")
    p.name(contest, "Return of King Leopold III: should the King resume his constitutional powers?", "en", "official")
    p.name(contest, "Question Royale: le Roi doit-il reprendre ses pouvoirs constitutionnels?", "fr", "native", False)
    p.name(contest, "Koningskwestie: moet de Koning zijn grondwettelijke macht hervatten?", "nl", "native", False)
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    options = []
    for position, (label, kind) in enumerate((("OUI / JA", "yes"), ("NON / NEEN", "no")), 1):
        option = p.id("option", kind)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )

    result_set = p.id("result_set")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")

    boxes = json.loads(BOXES.read_text())
    pages: dict[int, str] = {}
    for number in sorted({box["page"] for box in boxes}):
        width, height = sizes(stored.path)[number]
        pages[number] = p.id("page", number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    values: list[tuple[str, str | None, int, int]] = []
    locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    for index, row in enumerate(ROWS):
        unit = p.id("unit", slug(row.dutch))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELGIUM,
            scheme_code=SCHEME,
            code=slug(row.dutch),
            area_relation="equals",
            area_id=p.id("area", slug(row.dutch)),
            kind="standard",
        )
        p.name(unit, row.dutch, "nl", "native", False)
        p.name(unit, row.french, "fr", "native", False)
        p.name(unit, f"{row.dutch} / {row.french}" if row.dutch != row.french else row.dutch, "en", "official")
        cells = [
            (None, CAST, row.cast),
            (None, INVALID, row.blank_null),
            (None, VALID, row.valid),
            (options[0], VOTES, row.yes),
            (options[1], VOTES, row.no),
        ]
        for (option, measure, value), box in zip(cells, boxes[index]["boxes"], strict=True):
            values.append((unit, option, measure, value))
            locators.append((unit, option, measure, pages[boxes[index]["page"]], *box))
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators, schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"], orient="row"
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    for option, measure, value in (
        (None, CAST, KINGDOM.cast),
        (None, INVALID, KINGDOM.blank_null),
        (None, VALID, KINGDOM.valid),
        (options[0], VOTES, KINGDOM.yes),
        (options[1], VOTES, KINGDOM.no),
    ):
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    p.accept(
        "accounting.ballots_sum",
        "the Oudenaarde row prints 73,500 ballots cast and 2,371 blank and null, but 71,125 valid where yes plus "
        "no is 71,129; stored as printed",
    )
    p.accept(
        "accounting.declared",
        "the Kingdom row prints 5,085,263 valid ballots (ballots cast less blank and null), while the arrondissement "
        "rows, with the Oudenaarde misprint of 71,125, add up to 5,085,259; the other four national figures match",
    )
    return p
