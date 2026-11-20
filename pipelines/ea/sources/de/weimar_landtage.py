import csv
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.package import NAMESPACE, Draft
from ea.sources.licenses import UNLICENSED
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
VERFASSUNGEN = "https://www.verfassungen.de"
WIKIPEDIA = "https://de.wikipedia.org/wiki"
# The texts the successor links and the Weimar names cite: laws and constitutions as transcribed on verfassungen.de,
# each titled with the gazette that printed it where the page names one, and two German Wikipedia articles, pinned
# to the revision fetched. "Land (Deutschland)" lists the territories each Land of 1947 was formed from.
TEXTS: dict[str, tuple[str, str, str]] = {
    "laender": ("Wikipedia (de)", "Land (Deutschland)", f"{WIKIPEDIA}/Land_(Deutschland)"),
    "niedersachsen": ("Wikipedia (de)", "Niedersachsen", f"{WIKIPEDIA}/Niedersachsen"),
    "mecklenburg": (
        "Deutsches Reich",
        (
            "Gesetz über die Vereinigung von Mecklenburg-Strelitz mit Mecklenburg-Schwerin vom 15. Dezember 1933, "
            "RGBl. 1933 I S. 1065"
        ),
        f"{VERFASSUNGEN}/mv/vereinigung33.htm",
    ),
    "gross-hamburg": (
        "Deutsches Reich",
        (
            "Gesetz über Groß-Hamburg und andere Gebietsbereinigungen (Groß-Hamburg-Gesetz) vom 26. Januar 1937, "
            "RGBl. 1937 I S. 91"
        ),
        f"{VERFASSUNGEN}/hh/grosshamburg37.htm",
    ),
    "proklamation-2": (
        "Militärregierung Deutschland, Amerikanische Zone",
        "Proklamation Nr. 2 vom 19. September 1945",
        f"{VERFASSUNGEN}/bw/wuerttemberg-baden/proklamation2-45.htm",
    ),
    "lippe": (
        "Land Nordrhein-Westfalen",
        (
            "Gesetz über die Vereinigung des Landes Lippe mit dem Land Nordrhein-Westfalen (1948), Landtag "
            "Nordrhein-Westfalen Drucksache 01/644"
        ),
        f"{VERFASSUNGEN}/nrw/lippe/eingliederungsgesetz47.htm",
    ),
    "BY": (
        "Freistaat Bayern",
        "Verfassungsurkunde des Freistaats Bayern vom 14. August 1919, GVBl. 1919 Nr. 58",
        f"{VERFASSUNGEN}/by/verf19.htm",
    ),
    "SN": (
        "Freistaat Sachsen",
        "Verfassung des Freistaates Sachsen vom 1. November 1920, SächsGBl. 1920 Nr. 26 S. 445",
        f"{VERFASSUNGEN}/sac/verf20.htm",
    ),
    "TH": (
        "Deutsches Reich",
        "Gesetz, betreffend das Land Thüringen, vom 30. April 1920",
        f"{VERFASSUNGEN}/th/thueringengesetz20.htm",
    ),
    "HH": (
        "Freie und Hansestadt Hamburg",
        "Verfassung der Freien und Hansestadt Hamburg vom 7. Januar 1921, HmbGVBl. 1921 S. 9",
        f"{VERFASSUNGEN}/hh/verf21.htm",
    ),
    "HB": (
        "Freie Hansestadt Bremen",
        "Verfassung der Freien Hansestadt Bremen vom 18. Mai 1920, BremGBl. 1920 Nr. 46 S. 183",
        f"{VERFASSUNGEN}/hb/verf20.htm",
    ),
}
# Where each defunct state went, dated to its end in STATES: (state, kind, successor, date precision, text). A
# successor is a state code here, a Land's ISO code, or the de.polity key of a Land of 1945 to 1952 that de.landtag
# holds. Prussia's successors are the Länder that "Land (Deutschland)" forms from Prussian territory as of 21 January
# 1947, a month before Control Council Law No. 46 dissolved it; Berlin, then under four-power rule, and the Saar,
# outside the Control Council's authority since 1946, are left out. The People's State of Hesse split at the zone
# border: Proclamation No. 2 put its part east of the Rhine in Greater Hesse, and Rhenish Hesse went to
# Rhineland-Palatinate. Anhalt joined the Prussian Province of Saxony in July 1945. Württemberg and Baden are linked
# by de.landtag, to the three Länder of 1945 to 1952 that merged into Baden-Württemberg.
SUCCESSORS: list[tuple[str, str, str, str, str]] = [
    *(
        ("PR", "split_into", land, "day", "laender")
        for land in (
            "DE-SH",
            "DE-NI",
            "DE-NW",
            "DE-HE",
            "DE-RP",
            "wuerttemberg-hohenzollern",
            "DE-BB",
            "DE-MV",
            "DE-SN",
            "DE-ST",
            "DE-TH",
        )
    ),
    ("HE", "split_into", "DE-HE", "day", "proklamation-2"),
    ("HE", "split_into", "DE-RP", "day", "laender"),
    ("MS", "merged_into", "DE-MV", "day", "mecklenburg"),
    ("MST", "merged_into", "DE-MV", "day", "mecklenburg"),
    ("OL", "merged_into", "DE-NI", "day", "niedersachsen"),
    ("BS", "merged_into", "DE-NI", "day", "niedersachsen"),
    ("SL", "merged_into", "DE-NI", "day", "niedersachsen"),
    ("LI", "merged_into", "DE-NW", "day", "lippe"),
    ("LU", "merged_into", "PR", "day", "gross-hamburg"),
    ("AN", "merged_into", "DE-ST", "month", "laender"),
]
# The official names of the states that are Länder today, from their constitutions (Thuringia's from the Reich law
# that formed it on 1 May 1920), as historical names for the Weimar years: the date each took effect, or the
# Republic's start, to the Gleichschaltung of the states.
WEIMAR_NAMES = {"BY": WEIMAR, "SN": WEIMAR, "TH": date(1920, 5, 1), "HH": WEIMAR, "HB": WEIMAR}
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
    for _, _, link in TEXTS.values():
        raw.fetch(SOURCE, cached_url(link), suffix=".wiki" if link.startswith(WIKIPEDIA) else ".html")


def cached_url(link: str) -> str:
    """The file cached for a text: a Wikipedia article's wikitext, which the cache pins to its revision, or the page."""
    if link.startswith(WIKIPEDIA):
        return f"https://de.wikipedia.org/w/index.php?title={link.removeprefix(f'{WIKIPEDIA}/')}&action=raw"
    return link


def image(edition: int, page: int) -> tuple[str, int, int]:
    artifact = raw.cached(SOURCE, url(edition, LEAVES[edition, page]))
    PAGES.mkdir(parents=True, exist_ok=True)
    png = PAGES / f"{artifact.sha256}.png"
    if not png.exists():
        subprocess.run(["magick", str(artifact.path), "-resize", "1500x", str(png)], check=True)
    # The PNG header holds the size (IHDR, bytes 16 to 24), so reading it needs no image tool.
    header = png.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{png} is not a PNG")
    width, height = int.from_bytes(header[16:20]), int.from_bytes(header[20:24])
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
        cs = Draft(
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

        texts: dict[str, UUID] = {}
        for key, (publisher, title, link) in TEXTS.items():
            texts[key] = cs.source(publisher, title)
            wikipedia = link.startswith(WIKIPEDIA)
            if texts[key] in cs.minted:
                cs.add(
                    "source",
                    id=texts[key],
                    kind="community" if wikipedia else "official_body",
                    publisher=publisher,
                    title=title,
                    url=link,
                    license="CC BY-SA 4.0" if wikipedia else UNLICENSED,
                    license_url="https://creativecommons.org/licenses/by-sa/4.0/" if wikipedia else None,
                    notes=None if wikipedia else "Text as printed, transcribed on verfassungen.de.",
                )
            cs.artifact(raw.cached(SOURCE, cached_url(link)), texts[key])
        for code, kind, successor, precision, text in SUCCESSORS:
            source_polity = polities[code]
            if successor in STATES:
                target = polities[successor]
            else:
                target = cs.polity(successor) if successor.startswith("DE-") else cs.key("de.polity", successor)
                if target in cs.minted:
                    raise ValueError(f"run de.bundeswahlleiterin and de.landtag first: {successor} must exist")
            cs.add(
                "polity_relation",
                id=uuid5(NAMESPACE, f"polity_relation\x1f{source_polity}\x1f{kind}\x1f{target}"),
                from_polity_id=source_polity,
                to_polity_id=target,
                kind=kind,
                date=STATES[code][6],
                date_precision=precision,
                source_id=texts[text],
            )
        for code, since in WEIMAR_NAMES.items():
            cs.add(
                "name",
                entity_id=polities[code],
                text=STATES[code][3],
                lang="de",
                kind="historical",
                valid_from=since,
                valid_to=GLEICHSCHALTUNG,
                is_preferred=False,
                source_id=texts[code],
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
