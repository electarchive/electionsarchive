"""Khyber Pakhtunkhwa's direct elections of tehsil council chairmen and city mayors in December 2021 and March 2022,
by council, from the Election Commission of Pakistan's scanned result forms.

The Commission's pages for the 2021-22 local government elections in Khyber Pakhtunkhwa link shared folders of
scanned forms. For the first phase (17 districts, polled on 19 December 2021) a folder per district holds the final
consolidated result (Form XXI) of each tehsil or city council chairman's seat, and another folder holds the
provisional consolidated statements (Form XIX) of the same seats; a third holds the Forms XIX of the seats polled on
13 February 2022, where the first-phase poll had been postponed or was held again. For the second phase (18
districts, polled on 31 March 2022) a folder holds the Form XIX of each seat. Each form prints the registered voters,
each candidate's party and valid votes, and the valid and rejected or excluded votes in all.

The scans carry no usable text layer, so each figure was read from the page image, and each is stored with its page
and the box of its cell. The candidates' votes were checked against the valid votes each form prints. Each council's
seat is taken from its Form XXI where the folders hold one, and otherwise from its Form XIX. The forms do not name the
winner, and no outcome is stored. The transcriptions, with each figure's page and box, are in `ecp_lg_kp.json`.

The councils' shapes come from geoBoundaries' Pakistan tehsils (boundary year 2017), matched to the councils whose
tehsil they cover; a council that a 2017 tehsil does not match one to one has no shape."""

import json
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl
import shapely
from PIL import Image

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pk.ecp_lg_kp"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
SCHEME = "pk.kp_tehsil"
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download"
FOLDER = "https://drive.google.com/embeddedfolderview?id={}"
PAGE_2021 = "https://web.archive.org/web/20251124035402id_/https://ecp.gov.pk/lg-kp-2021-2022"
PAGE_2021_OLD = "https://web.archive.org/web/20230420022703id_/https://ecp.gov.pk/frmGenericPage.aspx?PageID=3283"
PAGE_2021_PHASE2 = (
    "https://web.archive.org/web/20230530052712id_/https://www.ecp.gov.pk/frmGenericPage.aspx?PageID=3294"
)
SITE = "https://ecp.gov.pk/lg-kp-2021-2022"
FOLDERS = {
    "p1_form21": "1jkQNS_pG4ZFbmUYARIR4Bw8s5Ojei645",
    "p1_form19": "1MuDJn2k6ZpW-pdItD1cs3w66W7kErZEa",
    "p2_form19": "1yKOvrCRGfZofVi9QiYbip70PcyNtcIvT",
    "feb_form19": "1O4Y8NHlTxREcNIowb8sNniimYsxdttnQ",
}
TEHSILS = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/PAK/ADM3/"
    "geoBoundaries-PAK-ADM3.geojson"
)
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
DATA: dict[str, Any] = json.loads((Path(__file__).parent / "ecp_lg_kp.json").read_text())
PAGES = raw.DATA / "pages" / SOURCE
PHASES = {
    1: (date(2021, 12, 19), "first"),
    2: (date(2022, 3, 31), "second"),
}
PLAN = date(2021, 12, 19)
PACKAGES = [f"{SOURCE}/registry", f"{SOURCE}/geo", f"{SOURCE}/2021-12-19", f"{SOURCE}/2022-03-31"]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DATASET = key("source", "ecp-forms")
SHAPES = key("source", "geoboundaries-adm3")
SYSTEM = key("electoral_system", "fptp")


def _not_html(path: Path) -> None:
    if path.read_bytes()[:64].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("the host returned a web page, not the scan")


def _url(form: dict[str, Any]) -> str:
    return DRIVE.format(form["drive"])


def fetch() -> None:
    """The scans and folder listings come from Google Drive; the Commission's own pages, which its firewall refuses
    to scripts, were fetched as web.archive.org captures and added to the cache with `raw.store`."""
    for folder in FOLDERS.values():
        raw.fetch(SOURCE, FOLDER.format(folder), suffix=".html")
    for form in _used():
        raw.fetch(SOURCE, _url(form), suffix=form["suffix"], check=_not_html)
    raw.fetch(SOURCE, TEHSILS, suffix=".geojson")
    for page in (PAGE_2021, PAGE_2021_OLD, PAGE_2021_PHASE2):
        raw.cached(SOURCE, page)


def packages() -> list[str]:
    return PACKAGES


def parse() -> None:
    registry().write()
    geo().write()
    for phase in PHASES:
        election(phase).write()


def emit() -> None:
    package.run("load", PACKAGES)


def council_area(form: dict[str, Any]) -> str:
    return key("area", SCHEME, changeset.slug(form["district_en"]), changeset.slug(form["council_en"]))


def office(form: dict[str, Any]) -> str:
    return key("office", changeset.slug(form["district_en"]), changeset.slug(form["council_en"]))


def council_label(form: dict[str, Any]) -> str:
    kind = "City Council" if form["kind"] == "city" else "Tehsil Council"
    return f"{form['council_en']} {kind}, {form['district_en']} District"


def office_label(form: dict[str, Any]) -> str:
    if form["kind"] == "city":
        return f"Mayor of {form['council_en']}"
    return f"Chairman of {form['council_en']} Tehsil"


def party_id(name: str) -> str:
    return key("party", name)


def _render(form: dict[str, Any], page: int) -> tuple[str, int, int]:
    """The page image, a PNG named for the file's hash and page number, and its size."""
    artifact = raw.cached(SOURCE, _url(form))
    image = PAGES / f"{artifact.sha256}-{page}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        if form["suffix"] == ".pdf":
            with tempfile.TemporaryDirectory() as folder:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "110",
                        "-png",
                        "-f",
                        str(page),
                        "-l",
                        str(page),
                        "-singlefile",
                        str(artifact.path),
                        f"{folder}/p",
                    ],
                    check=True,
                )
                Image.open(f"{folder}/p.png").convert("RGB").save(image)
        else:
            picture = Image.open(artifact.path).convert("RGB")
            picture.thumbnail((1400, 1900))
            picture.save(image)
    width, height = Image.open(image).size
    return f"pages/{SOURCE}/{image.name}", width, height


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The tehsil council chairmen and city mayors of Khyber Pakhtunkhwa elected in 2021 and 2022, the parties "
        "that stood for them, and the Election Commission's scanned result forms they come from",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=DATASET,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Local Government Elections Khyber Pakhtunkhwa 2021-2022: consolidated results of the tehsil and city "
        "councils (Forms XIX and XXI)",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for page in (PAGE_2021, PAGE_2021_OLD, PAGE_2021_PHASE2):
        p.artifact(raw.cached(SOURCE, page), DATASET)
    for folder in FOLDERS.values():
        p.artifact(raw.cached(SOURCE, FOLDER.format(folder)), DATASET)
    for url in sorted({_url(form) for form in _used()}):
        p.artifact(raw.cached(SOURCE, url), DATASET)
    for form in _used():
        id = office(form)
        p.add("office", id=id, polity_id=PAKISTAN, kind="mayor")
        p.name(id, office_label(form), "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under the Khyber Pakhtunkhwa Local Government Act 2013 as amended in 2019, the chairman of each "
        "tehsil local government, and the mayor of each city local government, is elected directly by the voters of "
        "the tehsil or city by plurality.",
        lang="en",
    )
    for name in sorted({c["party_en"] for form in _used() for c in form["candidates"]} - {"Independent", None}):
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def _used() -> list[dict[str, Any]]:
    return [form for form in DATA["forms"] if form.get("use")]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Khyber Pakhtunkhwa's tehsil and city councils whose chairmen and mayors were elected in 2021 and 2022, with "
        "the shapes of those that a 2017 tehsil of geoBoundaries matches",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(PAKISTAN)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=PAKISTAN,
        kind="administrative",
        rank=3,
        noun="tehsil council",
        kinds={"tehsil_council": "tehsil council", "city_council": "city council"},
        code_system=None,
    )
    p.add(
        "source",
        id=SHAPES,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Pakistan ADM3 (tehsils)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), from Pathways Data Pvt. Ltd.",
        notes="Boundary year 2017, built December 2023. Generalized shapes of the revenue tehsils, matched by name to "
        "the tehsil and city councils whose area they cover.",
    )
    p.artifact(raw.cached(SOURCE, TEHSILS), SHAPES)
    outlines: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, TEHSILS).path.read_bytes())["features"]:
        found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        polygons = [
            q
            for part in shapely.get_parts(found)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        outlines[feature["properties"]["shapeName"]] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    seen: set[str] = set()
    for form in _used():
        id = council_area(form)
        if id in seen:
            continue
        seen.add(id)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add(
            "area_kind",
            area_id=id,
            kind="city_council" if form["kind"] == "city" else "tehsil_council",
            valid_from=PLAN,
            source_id=DATASET,
        )
        p.name(id, council_label(form), "en", "official")
        if form.get("shape"):
            outline = outlines[form["shape"]]
            p.add(
                "area_geometry",
                area_id=id,
                valid_from=PLAN,
                geom=shapely.to_wkb(outline, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
                ),
                quality="academic",
                source_id=SHAPES,
            )
    return p


def election(phase: int) -> Package:
    day, ordinal = PHASES[phase]
    stamp = day.isoformat()
    p = Package(
        f"{SOURCE}/{stamp}",
        SOURCE,
        f"The {ordinal} phase of Khyber Pakhtunkhwa's local government elections, {day:%-d %B %Y}: the direct "
        "elections of tehsil council chairmen and city mayors, by council, from the Election Commission's scanned "
        "result forms",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external.add(PAKISTAN)
    election = key("election", stamp)
    root = f"pk/{stamp}-khyber-pakhtunkhwa-local"
    p.add("election", id=election, polity_id=PAKISTAN, kind="local", date_from=day, date_to=day, status="held")
    p.add("slug", path=root, entity_id=election, is_current=True)
    p.name(election, f"{day.year} Khyber Pakhtunkhwa local government elections ({ordinal} phase)", "en", "official")
    p.add(
        "note",
        id=key("note", election, "coverage"),
        entity_id=election,
        kind="context",
        body_md=COVERAGE[phase],
        lang="en",
    )
    values: list[dict[str, Any]] = []
    locators: list[dict[str, Any]] = []
    declared: list[tuple[str, str | None, int, int]] = []
    paths: set[str] = set()
    for form in _used():
        if form["phase"] != phase:
            continue
        tag = (stamp, changeset.slug(form["district_en"]), changeset.slug(form["council_en"]))
        artifact = raw.cached(SOURCE, _url(form))
        p.uses(artifact)
        contest = key("contest", *tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office(form),
            district_area_id=council_area(form),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        title = f"{day.year} Khyber Pakhtunkhwa local government elections, {office_label(form)}"
        if form["kind"] != "city":
            title += f" ({form['district_en']} District)"
        p.name(contest, title, "en", "official")
        path = f"{root}/{tag[1]}/{tag[2]}"
        if path in paths:
            raise ValueError(f"two contests share the path {path}")
        paths.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        unit = key("unit", *tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code=SCHEME,
            code=f"{stamp}:{tag[1]}:{tag[2]}",
            area_relation="equals",
            area_id=council_area(form),
            kind="standard",
        )
        p.name(unit, council_label(form), "en", "official")
        result_set = key("result_set", *tag)
        final = form["form"] == "XXI"
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official" if final else "preliminary",
            kind="counted",
            source_id=DATASET,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD_XXI if final else METHOD_XIX,
            lang="en",
        )
        for index, text in enumerate(form.get("notes", [])):
            p.add(
                "note",
                id=key("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=text,
                lang="en",
            )
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=DATASET,
            locator={"url": _url(form), "file": form["name"]},
        )
        pages: dict[int, str] = {}
        numbers = {c["page"] for c in form["candidates"]} | {
            form[field]["page"] for field in ("registered", "polled", "valid", "invalid") if form.get(field)
        }
        for number in sorted(numbers):
            image, width, height = _render(form, number)
            pages[number] = id = key("page", *tag, number)
            p.add(
                "source_page",
                id=id,
                artifact_id=str(uuid5(package.NAMESPACE, f"source_artifact\x1f{DATASET}\x1f{artifact.storage_key}")),
                page_number=number,
                image_key=image,
                width=width,
                height=height,
            )

        def put(option: str | None, measure: int, value: int, page: int, box: list[float]) -> None:
            values.append(
                {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": measure, "value": value}
            )
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "measure": measure,
                    "page_id": pages[page],
                    "x0": box[0],
                    "y0": box[1],
                    "x1": box[2],
                    "y1": box[3],
                }
            )
            declared.append((result_set, option, measure, value))

        for field, measure in (("registered", REGISTERED), ("polled", CAST), ("valid", VALID), ("invalid", INVALID)):
            figure = form.get(field)
            if figure:
                put(None, measure, figure["value"], figure["page"], figure["box"])
        for c in form["candidates"]:
            position = c["position"]
            option = key("option", *tag, position)
            person = key("person", *tag, position)
            independent = c["party_en"] == "Independent"
            party = None if independent or c["party_en"] is None else party_id(c["party_en"])
            p.add("person", id=person)
            p.name(person, c["name"], "ur" if c["script"] == "arab" else "en", "official")
            if c.get("name_ur"):
                p.name(person, c["name_ur"], "ur", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=c["name"],
                party_id=party,
            )
            p.add(
                "candidacy",
                id=key("candidacy", *tag, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c["name"],
                member_of_party_id=party,
                is_independent=True if independent else (False if party else None),
            )
            put(option, VOTES, c["votes"], c["page"], c["box"])
    for code, reason in ACCEPT.get(phase, {}).items():
        p.accept(code, reason)
    p.frame(
        "result_value",
        pl.DataFrame(values, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(locators, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )
    for rs, option, measure, value in declared:
        p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=value)
    return p


VOTES_VALID = (
    "The forms' own figures: on these councils the candidates' votes, each re-read on an enlarged crop of the scan, "
    "do not add up to the valid votes the returning officer printed (Hangu, Shabqadar, Chamkani, Lower Mohmand, "
    "Bannu, Bettani, Lahor, Thall, Upper Mohmand, and Serai Naurang in the first phase; Allai, Lalqilla, Oghi, "
    "Chakisar, Bahrain, Khwazakhela, Kalkot, and Larjam in the second); both are stored as printed"
)
BALLOTS_SUM = (
    "The forms' own figures: on these councils the valid votes and the votes excluded from the count that the "
    "returning officer printed do not add up to the total votes polled printed on the same form; all three are "
    "stored as printed"
)
ACCEPT: dict[int, dict[str, str]] = {
    1: {"accounting.votes_valid": VOTES_VALID, "accounting.ballots_sum": BALLOTS_SUM},
    2: {"accounting.votes_valid": VOTES_VALID, "accounting.ballots_sum": BALLOTS_SUM},
}

COVERAGE = {
    1: "The first phase polled the tehsil and city councils of 17 districts on 19 December 2021. Where the poll of "
    "a council was postponed, or held again at some polling stations, on 13 February 2022, its result here is the "
    "Commission's statement after that poll. A council whose form the Commission's folders do not hold is missing.",
    2: "The second phase polled the tehsil and city councils of the other 18 districts on 31 March 2022. A council "
    "whose form the Commission's folders do not hold is missing.",
}

METHOD_XXI = (
    "The final consolidated result (Form XXI) that the returning officer issued for the seat, read from the Election "
    "Commission of Pakistan's scanned form. The scan has no usable text layer, so each figure was read from the page "
    "image and is stored with its page and the box of its cell. The candidates' votes were checked against the valid "
    "votes the form prints. The form does not name the winner, and none is stored."
)
METHOD_XIX = (
    "The provisional consolidated statement of the results of the count (Form XIX) that the returning officer issued "
    "for the seat, read from the Election Commission of Pakistan's scanned form; the Commission's folders hold no "
    "final consolidated result (Form XXI) for this seat. The scan has no usable text layer, so each figure was read "
    "from the page image and is stored with its page and the box of its cell. The candidates' votes were checked "
    "against the valid votes the form prints. The form does not name the winner, and none is stored."
)
