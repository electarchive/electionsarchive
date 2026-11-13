import csv
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "de.weimar_landtage"
VERSION = "1"
PAGES = DATA / "pages" / SOURCE
ELECTIONS = Path(__file__).with_name("weimar_landtage_elections.tsv")
VOTES = Path(__file__).with_name("weimar_landtage_votes.tsv")
WEIMAR = date(1918, 11, 9)
GLEICHSCHALTUNG = date(1933, 3, 31)
ITEMS = {
    1932: "per_statistisches-jahrbuch-fur-das-deutsche-reich_statistisches-jahrbuch-fr-das-deutsc_1932_51",
}
LEAVES = {(1932, 544): 587, (1932, 545): 588}
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
LEFT_COLUMNS = {
    "registered": (349, 404),
    "voters": (411, 467),
    "valid": (518, 568),
    "SPD": (585, 633),
    "NSDAP": (684, 728),
    "KPD": (762, 802),
}
RIGHT_COLUMNS = {
    "Z": (55, 110),
    "DNVP": (150, 200),
    "DVP": (228, 291),
    "WP": (342, 382),
    "DSTP": (415, 473),
    "LANDVOLK": (500, 560),
    "AUFWERTUNG": (598, 651),
    "ANDERE": (680, 738),
}
WIKIDATA_NAMES = {
    "Q487129": "Kommunistische Partei Deutschlands (Opposition)",
    "Q689018": "Sozialistische Arbeiterpartei Deutschlands",
    "Q2596896": "Württembergischer Bauern- und Weingärtnerbund",
    "Q314832": "Christlich-sozialer Volksdienst",
}
LEFT_FOOTNOTES = {"3", "4", "5", "9", "12"}
WIDTH, HEIGHT = 900, 1344.3
NOTE = (
    "Figures are from the table of the latest Landtag elections in the Statistisches Jahrbuch für das Deutsche Reich "
    "1932 (pages 544 and 545). It prints the electorate, the voters, and the valid votes, but not the invalid votes. "
    "Parties the table groups in one column are shown separately where its footnotes break the column down, and the "
    "seats are those it gives each party according to the result."
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
        subprocess.run(["magick", str(artifact.path), "-resize", "1500x", str(png)], check=True)
    out = subprocess.run(
        ["magick", "identify", "-format", "%w %h", str(png)], check=True, capture_output=True, text=True
    )
    width, height = map(int, out.stdout.split())
    return f"pages/{SOURCE}/{png.name}", width, height


def box(x: tuple[float, float], y: tuple[float, float]) -> tuple[float, float, float, float]:
    return (x[0] / WIDTH, y[0] / HEIGHT, x[1] / WIDTH, y[1] / HEIGHT)


def emit() -> Path:
    elections = list(csv.DictReader(ELECTIONS.open(), delimiter="\t"))
    votes = list(csv.DictReader(VOTES.open(), delimiter="\t"))
    by_state: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in votes:
        by_state[row["state"]].append(row)
    for election in elections:
        rows = by_state[election["state"]]
        if sum(int(r["votes"]) for r in rows) != int(election["valid"]):
            raise ValueError(f"{election['state']}: party votes do not add up to the valid votes")
        if sum(int(r["seats"]) for r in rows) != int(election["seats"]):
            raise ValueError(f"{election['state']}: seats do not add up")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Landtag elections of the German states under the Weimar Republic, by state, from the "
            "Statistisches Jahrbuch für das Deutsche Reich: the latest election in each of the 17 states as of August "
            "1932, with the defunct states as historical polities",
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

        parties: dict[tuple[str, str], UUID] = {}
        added: set[UUID] = set()
        for row in votes:
            label, wikidata, code = row["label"], row["wikidata"], row["state"]
            if wikidata:
                party = cs.key("wikidata", wikidata)
                if party in cs.minted and party not in added:
                    cs.add("party", id=party, kind="party", polity_id=germany)
                    name(party, WIKIDATA_NAMES.get(wikidata, label), "de")
            else:
                party = cs.key("de.party", f"{code}:{label}")
                if party in cs.minted and party not in added:
                    joint = " u. " in label or label.startswith(("Nationale Einheitsliste", "Bürgerliche"))
                    cs.add("party", id=party, kind="list_vehicle" if joint else "party", polity_id=polities[code])
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
            row_index = int(election["row"])
            left_y = 284.6 + 22.93 * row_index
            right_y = 275.8 + 22.99 * row_index
            left_page = pages[edition, int(election["page"])]
            right_page = pages[edition, int(election["page"]) + 1]
            seats = int(election["seats"])
            cs.add("body_size", body_id=bodies[code], seats=seats, valid_from=day, valid_to=GLEICHSCHALTUNG)
            election_id = cs.key(f"{SOURCE}.election", f"{code}:{day}")
            cs.add(
                "election",
                id=election_id,
                polity_id=polities[code],
                kind="general",
                date_from=day,
                date_to=day,
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
            cs.add("note", entity_id=contest, kind="context", body_md=NOTE)
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
                values.append((sets[set_key], unit, None, measure, int(election[column])))
                locators.append(
                    (
                        sets[set_key],
                        unit,
                        None,
                        measure,
                        str(left_page),
                        *box(LEFT_COLUMNS[column], (left_y - 9, left_y + 9)),
                    )
                )
            for position, row in enumerate(by_state[code], 1):
                option = cs.key(f"{SOURCE}.option", f"{code}:{day}:{row['label']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[code, row["label"]],
                    ballot_label=row["label"],
                    ballot_position=position,
                )
                values.append((sets["list"], unit, str(option), 1, int(row["votes"])))
                split = sum(other["column"] == row["column"] for other in by_state[code]) > 1
                if split and row["footnote"] in LEFT_FOOTNOTES:
                    where = (left_page, box((120, 850), (990, 1200)))
                elif split:
                    where = (right_page, box((40, 790), (968, 1200)))
                elif row["column"] in LEFT_COLUMNS:
                    where = (left_page, box(LEFT_COLUMNS[row["column"]], (left_y - 9, left_y + 20)))
                else:
                    where = (right_page, box(RIGHT_COLUMNS[row["column"]], (right_y - 9, right_y + 20)))
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
