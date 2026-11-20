import csv
import shutil
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.package import Draft
from ea.store import DATA, connect

SOURCE = "de.weimar_landtage"
VERSION = "1"
PAGES = DATA / "pages" / SOURCE
ELECTIONS = Path(__file__).with_name("weimar_landtage_elections.tsv")
VOTES = Path(__file__).with_name("weimar_landtage_votes.tsv")
WEIMAR = date(1918, 11, 9)
GLEICHSCHALTUNG = date(1933, 3, 31)
ITEMS = {
    1926: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fr-das-deutsc_1926_45",
    1927: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fur-das-deutsc_1927_46",
    1928: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fur-das-deutsc_1928_47",
    1929: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fr-das-deutsc_1929_48",
    1930: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fr-das-deutsc_1930_49",
    1931: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fr-das-deutsc_1931_50",
    1932: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fr-das-deutsc_1932_51",
}
# (edition, printed page): archive.org leaf. Each table spans a left and a right page.
LEAVES = {
    (1926, 454): 501,
    (1926, 455): 502,
    (1927, 500): 551,
    (1927, 501): 552,
    (1928, 582): 633,
    (1928, 583): 634,
    (1929, 486): 537,
    (1929, 487): 538,
    (1930, 564): 613,
    (1930, 565): 614,
    (1931, 548): 595,
    (1931, 549): 596,
    (1932, 544): 587,
    (1932, 545): 588,
}
SCHEME = "de.land_1920"
STATES: dict[str, tuple[str | None, str, str, str, str, str, date | None]] = {
    "PR": (
        None,
        "preussen",
        "Prussia",
        "Freistaat Preußen",
        "Prussian Landtag",
        "Preußischer Landtag",
        date(1947, 2, 25),
    ),
    "BY": ("DE-BY", "by", "Bavaria", "Freistaat Bayern", "", "", None),
    "SN": ("DE-SN", "sn", "Saxony", "Freistaat Sachsen", "", "", None),
    "WU": (
        None,
        "wuerttemberg",
        "Württemberg",
        "Freier Volksstaat Württemberg",
        "Landtag of Württemberg",
        "Württembergischer Landtag",
        date(1945, 9, 19),
    ),
    "BA": (None, "baden", "Baden", "Republik Baden", "Landtag of Baden", "Badischer Landtag", date(1945, 9, 19)),
    "TH": ("DE-TH", "th", "Thuringia", "Land Thüringen", "", "", None),
    "HE": (
        None,
        "volksstaat-hessen",
        "People's State of Hesse",
        "Volksstaat Hessen",
        "Landtag of the People's State of Hesse",
        "Landtag des Volksstaates Hessen",
        date(1945, 9, 19),
    ),
    "HH": ("DE-HH", "hh", "Hamburg", "Freie und Hansestadt Hamburg", "", "", None),
    "MS": (
        None,
        "mecklenburg-schwerin",
        "Mecklenburg-Schwerin",
        "Freistaat Mecklenburg-Schwerin",
        "Landtag of Mecklenburg-Schwerin",
        "Landtag von Mecklenburg-Schwerin",
        date(1934, 1, 1),
    ),
    "OL": (
        None,
        "oldenburg",
        "Oldenburg",
        "Freistaat Oldenburg",
        "Landtag of Oldenburg",
        "Oldenburgischer Landtag",
        date(1946, 11, 1),
    ),
    "BS": (
        None,
        "braunschweig",
        "Brunswick",
        "Freistaat Braunschweig",
        "Landtag of Brunswick",
        "Braunschweigischer Landtag",
        date(1946, 11, 1),
    ),
    "AN": (None, "anhalt", "Anhalt", "Freistaat Anhalt", "Landtag of Anhalt", "Anhaltischer Landtag", date(1945, 7, 1)),
    "HB": ("DE-HB", "hb", "Bremen", "Freie Hansestadt Bremen", "", "", None),
    "LI": (None, "lippe", "Lippe", "Freistaat Lippe", "Landtag of Lippe", "Lippischer Landtag", date(1947, 1, 21)),
    "LU": (
        None,
        "luebeck",
        "Lübeck",
        "Freie und Hansestadt Lübeck",
        "Bürgerschaft of Lübeck",
        "Lübecker Bürgerschaft",
        date(1937, 4, 1),
    ),
    "MST": (
        None,
        "mecklenburg-strelitz",
        "Mecklenburg-Strelitz",
        "Freistaat Mecklenburg-Strelitz",
        "Landtag of Mecklenburg-Strelitz",
        "Landtag von Mecklenburg-Strelitz",
        date(1934, 1, 1),
    ),
    "WA": (None, "waldeck", "Waldeck", "Freistaat Waldeck", "Landtag of Waldeck", "Waldeckischer Landtag", date(1929, 4, 1)),
    "SL": (
        None,
        "schaumburg-lippe",
        "Schaumburg-Lippe",
        "Freistaat Schaumburg-Lippe",
        "Landtag of Schaumburg-Lippe",
        "Schaumburg-Lippischer Landtag",
        date(1946, 11, 1),
    ),
}
CITY_STATES = {"HH", "HB", "LU"}
WIKIDATA_NAMES = {
    "Q487129": "Kommunistische Partei Deutschlands (Opposition)",
    "Q689018": "Sozialistische Arbeiterpartei Deutschlands",
    "Q2596896": "Württembergischer Bauern- und Weingärtnerbund",
    "Q314832": "Christlich-sozialer Volksdienst",
}


class Layout(NamedTuple):
    """Where an edition's table sits on its two pages, in the units of a page `width` by `height` wide: the first
    state's vote line and the row pitch on each side (L, R), each column's side and horizontal span, the footnote block
    on each side, and how far a box reaches above and below a vote line for a total and for a party (votes and seats)."""

    width: float
    height: float
    rows: dict[str, tuple[float, float]]
    columns: dict[str, tuple[str, float, float]]
    notes: dict[str, tuple[float, float, float, float]]
    total: tuple[float, float]
    party: tuple[float, float]


LAYOUTS = {
    1926: Layout(
        1000,
        1457.6,
        {"L": (302, 40.88), "R": (270, 40.76)},
        {
            "registered": ("L", 318, 402),
            "voters": ("L", 405, 470),
            "valid": ("L", 500, 570),
            "DNVP": ("L", 565, 662),
            "NS": ("L", 698, 768),
            "DVP": ("L", 796, 872),
            "Z": ("R", 95, 178),
            "DDP": ("R", 180, 262),
            "SPD": ("R", 275, 352),
            "KPD": ("R", 370, 447),
            "ANDERE": ("R", 462, 540),
        },
        {"L": (85, 1020, 915, 1315), "R": (60, 1020, 570, 1295)},
        (12, 12),
        (12, 28),
    ),
    1927: Layout(
        1000,
        1409,
        {"L": (300, 37.76), "R": (280, 37.53)},
        {
            "registered": ("L", 335, 412),
            "voters": ("L", 413, 480),
            "valid": ("L", 500, 572),
            "DNVP": ("L", 582, 672),
            "NS": ("L", 733, 810),
            "DVP": ("L", 836, 910),
            "Z": ("R", 95, 165),
            "DDP": ("R", 185, 260),
            "SPD": ("R", 280, 355),
            "KPD": ("R", 375, 452),
            "ANDERE": ("R", 480, 555),
        },
        {"L": (150, 980, 945, 1300), "R": (85, 965, 575, 1290)},
        (11, 11),
        (11, 26),
    ),
    1928: Layout(
        1000,
        1447.3,
        {"L": (300, 24.41), "R": (272, 24.47)},
        {
            "registered": ("L", 380, 452),
            "voters": ("L", 455, 520),
            "valid": ("L", 585, 658),
            "SPD": ("L", 640, 705),
            "DNVP": ("L", 733, 800),
            "Z": ("L", 832, 900),
            "DVP": ("R", 95, 165),
            "KPD": ("R", 220, 290),
            "DDP": ("R", 298, 365),
            "WP": ("R", 398, 470),
            "NS": ("R", 500, 560),
            "LANDVOLK": ("R", 586, 655),
            "AUFWERTUNG": ("R", 680, 745),
            "ANDERE": ("R", 776, 845),
        },
        {"L": (100, 962, 930, 1427), "R": (80, 875, 920, 1362)},
        (8, 8),
        (8, 16),
    ),
    1929: Layout(
        1000,
        1503.8,
        {"L": (322, 25.81), "R": (298, 25.81)},
        {
            "registered": ("L", 385, 455),
            "voters": ("L", 455, 515),
            "valid": ("L", 560, 625),
            "SPD": ("L", 640, 700),
            "DNVP": ("L", 735, 800),
            "Z": ("L", 835, 905),
            "DVP": ("R", 100, 170),
            "KPD": ("R", 225, 295),
            "DDP": ("R", 315, 380),
            "WP": ("R", 420, 490),
            "NS": ("R", 505, 575),
            "LANDVOLK": ("R", 610, 680),
            "AUFWERTUNG": ("R", 705, 775),
            "ANDERE": ("R", 805, 875),
        },
        {"L": (90, 1175, 930, 1490), "R": (85, 1135, 905, 1495)},
        (8, 8),
        (8, 17),
    ),
    1930: Layout(
        1000,
        1517.2,
        {"L": (296, 25.69), "R": (272, 25.69)},
        {
            "registered": ("L", 370, 445),
            "voters": ("L", 450, 512),
            "valid": ("L", 560, 625),
            "SPD": ("L", 640, 700),
            "DNVP": ("L", 735, 800),
            "Z": ("L", 830, 905),
            "DVP": ("R", 100, 165),
            "KPD": ("R", 205, 265),
            "DDP": ("R", 300, 365),
            "WP": ("R", 400, 470),
            "NS": ("R", 500, 575),
            "LANDVOLK": ("R", 600, 670),
            "AUFWERTUNG": ("R", 695, 765),
            "ANDERE": ("R", 790, 860),
        },
        {"L": (95, 1060, 930, 1505), "R": (85, 1050, 905, 1365)},
        (8, 8),
        (8, 17),
    ),
    1931: Layout(
        1000,
        1449.6,
        {"L": (243, 25.75), "R": (208, 25.75)},
        {
            "registered": ("L", 345, 425),
            "voters": ("L", 430, 495),
            "valid": ("L", 540, 610),
            "SPD": ("L", 615, 680),
            "DNVP": ("L", 705, 775),
            "Z": ("L", 805, 880),
            "DVP": ("R", 100, 165),
            "KPD": ("R", 205, 265),
            "DDP": ("R", 300, 365),
            "WP": ("R", 400, 470),
            "NS": ("R", 500, 570),
            "LANDVOLK": ("R", 595, 665),
            "AUFWERTUNG": ("R", 695, 765),
            "ANDERE": ("R", 795, 860),
        },
        {"L": (90, 1105, 930, 1440), "R": (85, 1150, 905, 1430)},
        (8, 8),
        (8, 17),
    ),
    1932: Layout(
        900,
        1344.3,
        {"L": (284.6, 22.93), "R": (275.8, 22.99)},
        {
            "registered": ("L", 349, 404),
            "voters": ("L", 411, 467),
            "valid": ("L", 518, 568),
            "SPD": ("L", 585, 633),
            "NSDAP": ("L", 684, 728),
            "KPD": ("L", 762, 802),
            "Z": ("R", 55, 110),
            "DNVP": ("R", 150, 200),
            "DVP": ("R", 228, 291),
            "WP": ("R", 342, 382),
            "DSTP": ("R", 415, 473),
            "LANDVOLK": ("R", 500, 560),
            "AUFWERTUNG": ("R", 598, 651),
            "ANDERE": ("R", 680, 738),
        },
        {"L": (120, 990, 850, 1200), "R": (40, 968, 790, 1200)},
        (9, 9),
        (9, 20),
    ),
}
NOTE = (
    "Figures are from the table of the latest Landtag elections in the Statistisches Jahrbuch für das Deutsche Reich "
    "{edition} (pages {left} and {right}). It prints the electorate, the voters, and the valid votes, but not the "
    "invalid votes. Parties the table groups in one column are shown separately where its footnotes break the column "
    "down, and the seats are those it gives each party according to the result."
)


def url(edition: int, leaf: int) -> str:
    item = ITEMS[edition]
    return f"https://archive.org/download/{item}/{item}_jp2.zip/{item}_jp2%2F{item}_{leaf:04d}.jp2"


def fetch() -> None:
    for (edition, _), leaf in LEAVES.items():
        raw.fetch(SOURCE, url(edition, leaf), suffix=".jp2")


def image(edition: int, page: int) -> tuple[str, int, int]:
    artifact = raw.cached(SOURCE, url(edition, LEAVES[edition, page]))
    PAGES.mkdir(parents=True, exist_ok=True)
    png = PAGES / f"{artifact.sha256}.png"
    if not png.exists():
        if shutil.which("magick"):
            subprocess.run(["magick", str(artifact.path), "-resize", "1500x", str(png)], check=True)
        else:
            with Image.open(artifact.path) as scan:
                size = (1500, round(scan.height * 1500 / scan.width))
                scan.resize(size, Image.Resampling.LANCZOS).save(png)
    with Image.open(png) as render:
        width, height = render.size
    return f"pages/{SOURCE}/{png.name}", width, height


def box(layout: Layout, x: tuple[float, float], y: tuple[float, float]) -> tuple[float, float, float, float]:
    return (x[0] / layout.width, y[0] / layout.height, x[1] / layout.width, y[1] / layout.height)


def read() -> tuple[list[dict[str, str]], dict[tuple[str, str], list[dict[str, str]]]]:
    """The elections and each election's party rows, after checking that the rows add up to the printed totals."""
    elections = list(csv.DictReader(ELECTIONS.open(), delimiter="\t"))
    votes = list(csv.DictReader(VOTES.open(), delimiter="\t"))
    by_election: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in votes:
        by_election[row["state"], row["day"]].append(row)
    seen = set()
    for election in elections:
        key = (election["state"], election["day"])
        if key in seen:
            raise ValueError(f"{key}: printed twice")
        seen.add(key)
        rows = by_election[key]
        if sum(int(r["votes"]) for r in rows) != int(election["valid"]):
            raise ValueError(f"{key}: party votes do not add up to the valid votes")
        if sum(int(r["seats"]) for r in rows) != int(election["seats"]):
            raise ValueError(f"{key}: seats do not add up")
        if not int(election["valid"]) <= int(election["voters"]) <= int(election["registered"] or election["voters"]):
            raise ValueError(f"{key}: valid votes, voters, and electorate are out of order")
        layout = LAYOUTS[int(election["edition"])]
        for row in rows:
            split = sum(other["column"] == row["column"] for other in rows) > 1
            if split != bool(row["side"]):
                raise ValueError(f"{key} {row['label']}: a footnote row needs the side its figures are printed on")
            if row["column"] not in layout.columns:
                raise ValueError(f"{key}: the {election['edition']} layout lacks the column {row['column']}")
    if set(by_election) - seen:
        raise ValueError(f"party rows for elections not in the elections table: {sorted(set(by_election) - seen)}")
    return elections, by_election


def emit() -> Path:
    elections, by_election = read()
    following: dict[tuple[str, str], date] = {}
    for code in STATES:
        days = sorted(date.fromisoformat(e["day"]) for e in elections if e["state"] == code)
        ended = STATES[code][6]
        end = GLEICHSCHALTUNG if ended is None else min(ended, GLEICHSCHALTUNG)
        for day, after in zip(days, [*days[1:], end], strict=False):
            following[code, str(day)] = after
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Landtag elections of the German states under the Weimar Republic, by state, from the tables of the "
            "latest election in each state in the Statistisches Jahrbuch für das Deutsche Reich, editions 1926 to 1932, "
            "with the defunct states as historical polities",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        nation = cs.area("de.nation", "DE")
        if {germany, nation} & cs.minted:
            raise ValueError("run de.bundeswahlleiterin first")
        publisher = "Statistisches Reichsamt"
        sources: dict[int, UUID] = {}
        pages: dict[tuple[int, int], UUID] = {}
        for edition, item in ITEMS.items():
            title = f"Statistisches Jahrbuch für das Deutsche Reich {edition}"
            sources[edition] = cs.source(publisher, title)
            if sources[edition] in cs.minted:
                cs.add(
                    "source",
                    id=sources[edition],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=f"https://archive.org/details/{item}",
                    license="Public domain (official work of the German Reich)",
                    license_url=None,
                )
        for (edition, page), leaf in LEAVES.items():
            artifact = cs.artifact(raw.cached(SOURCE, url(edition, leaf)), sources[edition])
            key, width, height = image(edition, page)
            pages[edition, page] = cs.page(artifact, 1)
            cs.add(
                "source_page",
                id=pages[edition, page],
                artifact_id=artifact,
                page_number=1,
                image_key=key,
                width=width,
                height=height,
            )

        cs.add("scheme", code=SCHEME, polity_id=germany, kind="administrative", rank=1, noun="state")
        polities: dict[str, UUID] = {}
        areas: dict[str, UUID] = {}
        units: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        for code, (iso, slug, english, native, body_en, body_de, ended) in STATES.items():
            if iso is not None:
                polities[code] = cs.polity(iso)
                bodies[code] = cs.key("de.landtag.body", f"landtag:{iso[3:]}")
                if {polities[code], bodies[code]} & cs.minted:
                    raise ValueError(f"run de.bundeswahlleiterin and de.landtag first: {iso} must exist")
            else:
                polities[code] = cs.key("de.polity", slug)
                cs.add("polity", id=polities[code], kind="federal_unit", iso_code=None)
                cs.add("slug", path=f"de/{slug}", entity_id=polities[code])
                cs.add("polity_parent", polity_id=polities[code], parent_id=germany, valid_from=WEIMAR, valid_to=ended)
                name(polities[code], english)
                name(polities[code], native, "de", "native")
                bodies[code] = cs.key(f"{SOURCE}.body", code)
                cs.add("body", id=bodies[code], polity_id=polities[code], kind="unicameral")
                name(bodies[code], body_en)
                name(bodies[code], body_de, "de", "native")
            areas[code] = cs.area(SCHEME, code)
            cs.add("area", id=areas[code], scheme_code=SCHEME)
            cs.add("area_code", area_id=areas[code], code=code, valid_from=WEIMAR, valid_to=ended)
            cs.add("area_parent", area_id=areas[code], parent_id=nation, valid_from=WEIMAR, valid_to=ended)
            name(areas[code], native, "de")
            name(areas[code], english, "en", "english")
            if iso is None:
                cs.add(
                    "polity_territory", polity_id=polities[code], area_id=areas[code], valid_from=WEIMAR, valid_to=ended
                )
            units[code] = cs.unit(SCHEME, code)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=polities[code],
                scheme_code=SCHEME,
                code=code,
                area_relation="equals",
                area_id=areas[code],
                kind="standard",
                valid_from=WEIMAR,
                valid_to=ended,
            )

        parties: dict[tuple[str, str], UUID | None] = {}
        added: set[UUID] = set()
        for row in (row for rows in by_election.values() for row in rows):
            label, wikidata, code = row["label"], row["wikidata"], row["state"]
            if row["kind"] == "others":
                parties[code, label] = None
                continue
            if wikidata:
                party = cs.key("wikidata", wikidata)
                if party in cs.minted and party not in added:
                    cs.add("party", id=party, kind="party", polity_id=germany)
                    name(party, WIKIDATA_NAMES.get(wikidata, label), "de")
            else:
                party = cs.key("de.party", f"{code}:{label}")
                if party in cs.minted and party not in added:
                    cs.add("party", id=party, kind=row["kind"], polity_id=polities[code])
                    name(party, label, "de")
            added.add(party)
            parties[code, label] = party

        values: list[tuple[str, str, str | None, int, int]] = []
        locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
        for election in elections:
            code = election["state"]
            day = date.fromisoformat(election["day"])
            edition = int(election["edition"])
            iso, slug, english, native, *_ = STATES[code]
            layout = LAYOUTS[edition]
            row_index = int(election["row"])
            ys = {side: y0 + pitch * row_index for side, (y0, pitch) in layout.rows.items()}
            page = int(election["page"])
            side_pages = {"L": pages[edition, page], "R": pages[edition, page + 1]}
            seats = int(election["seats"])
            cs.add(
                "body_size",
                body_id=bodies[code],
                seats=seats,
                valid_from=day,
                valid_to=following[code, election["day"]],
            )
            election_id = cs.key(f"{SOURCE}.election", f"{code}:{day}")
            cs.add(
                "election",
                id=election_id,
                polity_id=polities[code],
                kind="general",
                date_from=day,
                date_to=date.fromisoformat(election["until"]) if election["until"] else day,
                status="held",
            )
            kind = "Bürgerschaftswahl" if code in CITY_STATES else "Landtagswahl"
            label = english.removeprefix("People's State of ")
            name(election_id, f"{day.year} {label} state election")
            name(election_id, f"{kind} in {native.split()[-1]} {day.year}", "de", "native")
            path = f"de/{slug}/{day}-general"
            cs.add("slug", path=path, entity_id=election_id)
            contest = cs.key(f"{SOURCE}.contest", f"{code}:{day}:list")
            cs.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="list_allocation",
                body_id=bodies[code],
                district_area_id=areas[code],
                magnitude=seats,
                electoral_system_id=None,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} {label} state election, seat allocation")
            cs.add("slug", path=f"{path}/landtag", entity_id=contest)
            cs.add(
                "note",
                entity_id=contest,
                kind="context",
                body_md=NOTE.format(edition=edition, left=page, right=page + 1),
            )
            sets = {}
            for key, contest_id in (("election", None), ("list", contest)):
                sets[key] = str(cs.key(f"{SOURCE}.result_set", f"{code}:{day}:{key}"))
                cs.add(
                    "result_set",
                    id=sets[key],
                    election_id=election_id,
                    contest_id=contest_id,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[edition],
                    is_default=True,
                    completeness=1,
                )
            unit = str(units[code])
            for set_key, measure, column in (
                ("election", 2, "registered"),
                ("election", 3, "voters"),
                ("list", 4, "valid"),
            ):
                if not election[column]:
                    continue
                values.append((sets[set_key], unit, None, measure, int(election[column])))
                side, x0, x1 = layout.columns[column]
                y = ys[side]
                span = (y - layout.total[0], y + layout.total[1])
                locators.append(
                    (sets[set_key], unit, None, measure, str(side_pages[side]), *box(layout, (x0, x1), span))
                )
            for position, row in enumerate(by_election[code, election["day"]], 1):
                option = cs.key(f"{SOURCE}.option", f"{code}:{day}:{row['label']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if row["kind"] == "others" else "list",
                    party_id=parties[code, row["label"]],
                    ballot_label=row["label"],
                    ballot_position=position,
                )
                values.append((sets["list"], unit, str(option), 1, int(row["votes"])))
                if row["side"]:
                    x0, y0, x1, y1 = layout.notes[row["side"]]
                    where = (side_pages[row["side"]], box(layout, (x0, x1), (y0, y1)))
                else:
                    side, x0, x1 = layout.columns[row["column"]]
                    y = ys[side]
                    span = (y - layout.party[0], y + layout.party[1])
                    where = (side_pages[side], box(layout, (x0, x1), span))
                locators.append((sets["list"], unit, str(option), 1, str(where[0]), *where[1]))
                if int(row["seats"]):
                    cs.add("outcome", result_set_id=sets["list"], option_id=option, kind="won", seats=int(row["seats"]))

        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        cs.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        return cs.write()
