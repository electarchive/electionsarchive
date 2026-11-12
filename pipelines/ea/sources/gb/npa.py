import html
import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.npa"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
HERE = Path(__file__).parent
SOURCES = HERE / "npa_sources.tsv"
WARDS = HERE / "npa_wards.tsv"
CANDIDATES = HERE / "npa_candidates.tsv"
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
SHAPES = (
    "https://maps.data.gov.scot/arcgis/rest/services/NationalParks/MapServer/0/query"
    "?where=1%3D1&outFields=npcode,npname&outSR=27700&f=geojson"
)
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
SHAPES_TITLE = "National Parks - Scotland"
SHAPES_PUBLISHER = "Scottish Government"
LICENSE = "Open Government Licence v3.0"
LICENSE_URL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
PARKS = {
    "S21000002": (
        "Loch Lomond and The Trossachs National Park",
        "Loch Lomond and The Trossachs National Park Authority",
        {
            "1": "Cowal and North Loch Lomond",
            "2": "Breadalbane and The Trossachs",
            "3": "Callander",
            "4": "East Loch Lomond and Port of Menteith",
            "5": "West Loch Lomond and Balloch",
        },
        (
            "The Loch Lomond and The Trossachs National Park Elections (Scotland) Order 2002 (SSI 2002/202) divides "
            "the park into five wards, each electing one member of the National Park Authority's board by first past "
            "the post for four years; the rest of the board is appointed by Scottish Ministers and the councils. "
            "Stirling Council runs the poll by post for the whole park. Ward names follow the Authority's own usage."
        ),
    ),
    "S21000003": (
        "Cairngorms National Park",
        "Cairngorms National Park Authority",
        {
            "1": "Ward 1 (Highland and Perth and Kinross)",
            "2": "Ward 2 (Highland)",
            "3": "Ward 3 (Highland)",
            "4": "Ward 4 (Moray)",
            "5": "Ward 5 (Aberdeenshire, Angus, and Perth and Kinross)",
        },
        (
            "The Cairngorms National Park Elections (Scotland) Order 2003 divides the park into five wards, each "
            "electing one member of the National Park Authority's board by first past the post for four years; the "
            "rest of the board is appointed by Scottish Ministers and the councils. Highland Council runs the poll by "
            "post on behalf of the Authority. The wards are numbered, not named; the council areas in brackets follow "
            "Highland Council's 2023 results release."
        ),
    ),
}
WARD3_2019 = (
    "Highland Council's Ward 3 declaration is headed Thursday 28 March 2019, a week after the other wards "
    "polled on 21 March 2019."
)
UNNAMED_2017 = (
    "The declaration leaves the elected candidate's name blank. Geva Blackett, who polled the most votes, is "
    "recorded as elected, as the plurality winner the figures show."
)
DISAGREES = (
    "The sheet prints {ballots:,} total votes and {rejected:,} rejected ballot papers, but the candidates' votes "
    "add up to {valid:,}, so neither the ballot total nor the rejected count is stored."
)
UNPRINTED = (
    "The declaration prints {ballots:,} total votes but no count of rejected ballot papers; the candidates' votes "
    "add up to {valid:,}."
)


def suffix(url: str) -> str:
    if url.endswith(".pdf"):
        return ".pdf"
    if "/downloads/file/544" in url:
        return ".doc"
    if "/downloads/file/" in url:
        return ".pdf"
    return ".html"


def fetch() -> None:
    for url in pl.read_csv(SOURCES, separator="\t")["url"]:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=60, follow_redirects=True)
            response.raise_for_status()
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=suffix(url),
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", ""),
            )
    raw.fetch(SOURCE, SHAPES, suffix=".geojson")


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    kind = suffix(url)
    if kind == ".pdf":
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
    elif kind == ".doc":
        out = subprocess.run(["antiword", str(path)], capture_output=True, text=True, check=True).stdout
    else:
        page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", path.read_text(errors="replace"), flags=re.DOTALL)
        out = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return " ".join(out.replace("|", " ").split()).casefold()


def parse() -> None:
    wards = pl.read_csv(WARDS, separator="\t", schema_overrides={"ward": pl.String})
    candidates = pl.read_csv(CANDIDATES, separator="\t", schema_overrides={"ward": pl.String})
    known = set(pl.read_csv(SOURCES, separator="\t")["url"])
    texts = {url: text_of(url) for url in known}
    for ward in wards.iter_rows(named=True):
        if ward["source"] not in known:
            raise ValueError(f"{ward['source']} is not in {SOURCES.name}")
        text = texts[ward["source"]]
        lines = candidates.filter(
            (pl.col("day") == ward["day"]) & (pl.col("park") == ward["park"]) & (pl.col("ward") == ward["ward"])
        )
        if lines.is_empty() or int(lines["elected"].fill_null(0).sum()) != 1:
            raise ValueError(f"{ward['day']} {ward['park']} ward {ward['ward']}: needs one elected candidate")
        for line in lines.iter_rows(named=True):
            printed = " ".join(line["printed"].split()).casefold()
            if printed not in text:
                raise ValueError(f"{ward['day']} ward {ward['ward']}: {line['printed']!r} is not in the source")
            if line["votes"] is not None and not re.search(rf"{re.escape(printed)}\s+{line['votes']}\b", text):
                raise ValueError(f"{ward['day']} ward {ward['ward']}: {line['printed']} {line['votes']} not found")
        for field in ("electorate", "ballots"):
            value = ward[field]
            if value is not None and not re.search(rf"\b{value:,}\b|\b{value}\b", text):
                raise ValueError(f"{ward['day']} ward {ward['ward']}: {field} {value} not in the source")
    features = json.loads(raw.cached(SOURCE, SHAPES).path.read_text())["features"]
    if {feature["properties"]["npcode"] for feature in features} != set(PARKS):
        raise ValueError("the national park layer does not hold exactly the two parks")
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for feature in features:
                copy.write_row((feature["properties"]["npcode"], json.dumps(feature["geometry"])))
        rows = conn.execute(
            """select code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select code, st_multi(st_collectionextract(st_makevalid(st_force2d(
                       st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom from feature) shape
               order by code"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"code": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row"
    ).write_parquet(PARSED / "parks.parquet")


def emit() -> Path:
    parks = pl.read_parquet(PARSED / "parks.parquet")
    sources_table = pl.read_csv(SOURCES, separator="\t")
    wards = pl.read_csv(WARDS, separator="\t", schema_overrides={"ward": pl.String}, try_parse_dates=True)
    candidates = pl.read_csv(CANDIDATES, separator="\t", schema_overrides={"ward": pl.String}, try_parse_dates=True)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scotland's two national park authorities: the Loch Lomond and The Trossachs and Cairngorms "
            "parks (Scottish Government shapes) as polities, their five board wards each, and the local member "
            "elections of 2014, 2018, 2022, and 2026 (Loch Lomond and The Trossachs) and 2015, 2017, 2019, and "
            "2023 (Cairngorms), from the returning officers' declarations",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("publish gb.parliament first")
        for code, noun, kind, rank in (
            ("gb.npa", "national park", "administrative", 2),
            ("gb.npa_ward", "national park ward", "electoral_district", 3),
        ):
            if conn.execute("select 1 from scheme where code = %s", [code]).fetchone() is None:
                cs.add("scheme", code=code, polity_id=gb, kind=kind, rank=rank, noun=noun)

        sources: dict[str, UUID] = {}
        for url, publisher, title in sources_table.rows():
            sources[url] = cs.source(publisher, title)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        shapes = cs.source(SHAPES_PUBLISHER, SHAPES_TITLE)
        cs.add(
            "source",
            id=shapes,
            kind="official_body",
            publisher=SHAPES_PUBLISHER,
            title=SHAPES_TITLE,
            url="https://maps.data.gov.scot/arcgis/rest/services/NationalParks/MapServer/0",
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="© Scottish Government, contains Ordnance Survey data © Crown copyright and database right",
        )
        cs.artifact(raw.cached(SOURCE, SHAPES), shapes)
        cs.add("boundary_set", scheme_code="gb.npa", vintage="2026", valid_from=None, valid_to=None, as_of="2026-11-12")

        scotland = cs.area("gb.country", "SCT")
        polities: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        paths: dict[str, str] = {}
        ward_areas: dict[tuple[str, str], tuple[UUID, UUID]] = {}
        for code, geom, point in parks.rows():
            park_name, body_name, labels, _ = PARKS[code]
            area = cs.area("gb.npa", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.npa")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=scotland, valid_from=None, valid_to=None)
                name(area, park_name)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=shapes,
            )
            polity = polities[code] = cs.key("gb.polity", code)
            body = bodies[code] = cs.key("gb.body", code)
            paths[code] = f"gb/{slug(park_name)}"
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="special_district", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity("GB-SCT"))
                cs.add("polity_territory", polity_id=polity, area_id=area)
                name(polity, park_name)
                cs.add("slug", path=paths[code], entity_id=polity)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
                name(body, body_name)
            for number, label in labels.items():
                ward_code = f"{code}-{number}"
                ward = cs.area("gb.npa_ward", ward_code)
                unit = cs.unit("gb.npa_ward", ward_code)
                if ward in cs.minted:
                    cs.add("area", id=ward, scheme_code="gb.npa_ward")
                    cs.add("area_code", area_id=ward, code=ward_code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=ward, parent_id=area, valid_from=None, valid_to=None)
                    name(ward, label)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="gb.npa_ward",
                        code=ward_code,
                        area_relation="equals",
                        area_id=ward,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, label)
                ward_areas[code, number] = ward, unit

        people: dict[str, UUID] = {}
        for (day, code, kind), held in wards.group_by("day", "park", "kind", maintain_order=True):
            assert isinstance(day, date)
            park_name, body_name, labels, context = PARKS[code]
            by = kind == "by"
            election = cs.key("gb.election", f"{day}:{'by' if by else 'npa'}:{code}")
            last = day
            if code == "S21000003" and day == date(2019, 3, 21):
                last = date(2019, 3, 28)
            cs.add(
                "election",
                id=election,
                polity_id=polities[code],
                kind="by_election" if by else "local",
                date_from=day,
                date_to=last,
                status="held",
            )
            when = f"{day.day} {day:%B} {day.year}"
            name(election, f"{body_name} by-election, {when}" if by else f"{day.year} {body_name} election")
            election_path = f"{paths[code]}/{day}-{'by-election' if by else 'local'}"
            cs.add("slug", path=election_path, entity_id=election)
            cs.add("note", entity_id=election, kind="context", body_md=context)
            if last != day:
                cs.add("note", entity_id=election, kind="caveat", body_md=WARD3_2019)
            for ward in held.iter_rows(named=True):
                number = ward["ward"]
                area, unit = ward_areas[code, number]
                label = labels[number]
                source = sources[ward["source"]]
                lines = candidates.filter(
                    (pl.col("day") == day) & (pl.col("park") == code) & (pl.col("ward") == number)
                )
                tacit = lines["votes"].null_count() == lines.height
                contest_key = f"{day}:{'by' if by else 'npa'}:{code}-{number}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[code],
                    district_area_id=area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="tacit" if tacit else "counted",
                    **({"term": "remainder"} if by else {}),
                )
                ward_label = label if label.startswith("Ward") else f"{label} ward"
                name(contest, f"{ward_label}, {body_name}, {'by-election ' + when if by else day.year}")
                cs.add("slug", path=f"{election_path}/ward-{number}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source)
                result_set = cs.key("gb.npa.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                for number_on_sheet, line in enumerate(lines.iter_rows(named=True), start=1):
                    option_key = f"{contest_key}:{number_on_sheet}"
                    person_key = f"{code}:{line['person']}"
                    if person_key not in people:
                        people[person_key] = cs.key("gb.npa.person", person_key)
                        cs.add("person", id=people[person_key])
                        name(people[person_key], line["name"])
                    option = cs.key("gb.npa.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None,
                        ballot_label=line["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.npa.candidacy", option_key),
                        person_id=people[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=line["printed"],
                        member_of_party_id=None,
                        is_independent=False,
                    )
                    if line["votes"] is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"] == 1:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if tacit:
                    continue
                valid = int(lines["votes"].sum())
                ballots, rejected = ward["ballots"], ward["rejected"]
                accounting = [(REGISTERED, ward["electorate"])]
                if rejected is None:
                    accounting.append((BALLOTS, ballots))
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=UNPRINTED.format(ballots=ballots, valid=valid),
                    )
                elif valid + rejected == ballots:
                    accounting += [(BALLOTS, ballots), (INVALID, rejected)]
                else:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=DISAGREES.format(ballots=ballots, rejected=rejected, valid=valid),
                    )
                if by and code == "S21000003":
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=UNNAMED_2017)
                for measure, value in accounting:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
