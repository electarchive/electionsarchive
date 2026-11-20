"""Quebec municipal general elections, 2005 to 2025.

The Ministère des Affaires municipales et de l'Habitation publishes, for every general election since 2005, a file
with one row per candidate: the municipality, the borough and district where there is one, the post (mayor or
councillor seat), the candidate's votes and status, and the electors and rejected ballots of the post. Each post
is a contest for one seat decided by plurality, so each contest has one result set. A post whose only candidate
was acclaimed carries no votes and a `tacit` result set.

Boundaries of the municipalities come from the Ministère des Ressources naturelles et des Forêts, in its
administrative divisions at 1:100,000 (CC BY 4.0), matched by the municipality's geographic code.
"""

import functools
import io
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from uuid import uuid5

import polars as pl
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "ca.qc_municipal"
SHAPES = "ca.qc_municipal.sda"
BASE = "https://donneesouvertes.affmunqc.net/election_municipale/"
SDA = "https://diffusion.mern.gouv.qc.ca/diffusion/RGQ/Vectoriel/Theme/Regional/SDA_100k/SHP/BDAT(adm)_SHP.zip"
DATASET = "https://www.donneesquebec.ca/recherche/dataset/resultats-des-elections-municipales-generales"
SDA_DATASET = "https://www.donneesquebec.ca/recherche/dataset/decoupages-administratifs"
# Production holds Canada, Quebec, and its province area under the ids their first pipeline gave them.
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
QUEBEC = "01a13fb6-eab7-7501-a0f9-4903a58f045b"
QUEBEC_AREA = "01a13fb6-eab7-7501-a0f9-48f63db097d3"
FILES = {
    2005: "elec2005.csv",
    2009: "elec2009.csv",
    2013: "elec2013.csv",
    2017: "elec2017.csv",
    2021: "Elec2021_Mun.csv",
    2025: "Elec2025_Mun.csv",
}
DAYS = {
    2005: date(2005, 11, 6),
    2009: date(2009, 11, 1),
    2013: date(2013, 11, 3),
    2017: date(2017, 11, 5),
    2021: date(2021, 11, 7),
    2025: date(2025, 11, 2),
}
COLUMNS = {
    "mun": ("Code de la municipalité", "mun_c"),
    "mun_name": ("Nom de la municipalité", "mun_n"),
    "arr": ("Code de l'arrondissement", "arr_c"),
    "arr_name": ("Nom de l'arrondissement", "arr_n"),
    "district": ("Nom du district électoral", "dist_n"),
    "poste": ("Identifiant du poste", "poste_c"),
    "ptype": ("Type de poste", "type_poste_d"),
    "last": ("Nom", "nom"),
    "first": ("Prénom", "prenom"),
    "inc": ("Sortant", "sortant"),
    "party": ("Nom du parti ou de l'équipe", "parti_n"),
    "electors": ("Nombre d'électeurs", "nb_electeurs_maire", "nb_electeurs"),
    "votes": (
        "Nombre de votes reçus par le candidat",
        "Nombre de votes reçus par la candidat",
        "nb_votes_candidat",
    ),
    "rejected": ("Nombre de votes rejetés", "nb_votes_rejetes"),
    "status": ("Statut du candidat", "statut_candidat_d"),
}
ROLES = {
    "mairie": "mayor",
    "maire": "mayor",
    "maire d'arrondissement": "borough_mayor",
    "conseiller": "councillor",
    "conseiller de ville": "councillor",
    "conseiller de ville de l'arrondissement": "councillor",
    "conseiller d'arrondissement": "borough_councillor",
}
WON = {"élu", "élu sans opposition"}
OUTCOMES = {
    "non élu": "lost",
    "désistement": "withdrew",
    "décès": "withdrew",
    "déces": "withdrew",
    "colistier remplacé": "withdrew",
    "égalité": "tied",
}
VACANT = {"vacant", "poste vacant"}
SYSTEM_NOTE = (
    "Each seat of a Quebec municipal council, and the mayoralty, is filled by its own vote: every elector marks one "
    "candidate for the post, and the candidate with the most votes wins. A post with a single candidate is filled "
    "without a vote."
)
SECTION = {
    "mayor": "Mayor",
    "borough_mayor": "Borough mayor",
    "councillor": "Councillor",
    "borough_councillor": "Borough councillor",
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MAMH = key("source", "mamh")
MRNF = key("source", "mrnf")
SYSTEM = key("electoral_system", "plurality")


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def url(year: int) -> str:
    return BASE + FILES[year]


def fetch() -> None:
    for year in FILES:
        raw.fetch(SOURCE, url(year), suffix=".csv")
    raw.fetch(SHAPES, SDA, suffix=".zip")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def _tidy(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return None if text in ("", ".") else text


def _name(value: str | None) -> str | None:
    text = _tidy(value)
    if text is not None and text.isupper() and len(text) > 1:
        text = text.title()
    return text


def _order(text: str | None) -> tuple[float, str]:
    try:
        return float(text or 0), ""
    except ValueError:
        return 0.0, text or ""


def _number(value: str | None) -> int | None:
    text = _tidy(value)
    return None if text is None else int(text)


@functools.cache
def table(year: int) -> pl.DataFrame:
    """One row per candidate, with the columns of every year's file under one set of names."""
    frame = pl.read_csv(raw.cached(SOURCE, url(year)).path, infer_schema_length=0)
    frame = frame.rename({column: " ".join(column.split()) for column in frame.columns})
    picked: dict[str, pl.Series] = {}
    for name, spellings in COLUMNS.items():
        found = next((spelling for spelling in spellings if spelling in frame.columns), None)
        picked[name] = frame[found] if found else pl.Series(name, [None] * frame.height, pl.String)
    rows = pl.DataFrame(picked).with_columns(pl.all().map_elements(_tidy, return_dtype=pl.String))
    rows = rows.with_columns(pl.col("mun").str.zfill(5))
    rows = rows.with_columns(pl.col("ptype").str.to_lowercase().replace_strict(ROLES).alias("role"))
    if rows["role"].null_count():
        raise ValueError(f"{year}: unknown post type {rows.filter(pl.col('role').is_null())['ptype'].unique()}")
    return rows


def municipalities() -> dict[str, str]:
    """Each municipality's name as the latest election that held it printed it, by geographic code."""
    names: dict[str, str] = {}
    for year in DAYS:
        for code, text in table(year).select("mun", "mun_name").unique().iter_rows():
            names[code] = str(text)
    return names


def boroughs() -> dict[tuple[str, str], str]:
    names: dict[tuple[str, str], str] = {}
    for year in DAYS:
        for mun, arr, text in (
            table(year).filter(pl.col("arr").is_not_null()).select("mun", "arr", "arr_name").unique().iter_rows()
        ):
            names[(mun, arr)] = str(text or f"Borough {arr}")
    return names


def paths() -> dict[str, str]:
    taken: Counter[str] = Counter()
    names = municipalities()
    for text in names.values():
        taken[slug(text)] += 1
    return {
        code: f"ca/qc/{slug(text)}" + (f"-{code}" if taken[slug(text)] > 1 else "")
        for code, text in sorted(names.items())
    }


def polity(code: str) -> str:
    return key("polity", code)


def area(code: str) -> str:
    return key("area", code)


def party(mun: str, text: str) -> str:
    return key("party", mun, fold(text))


@functools.cache
def _outlines() -> dict[str, shapely.Geometry]:
    archive = zipfile.ZipFile(raw.cached(SHAPES, SDA).path)
    reader = shapefile.Reader(
        shp=io.BytesIO(archive.read("Bdat/SHP/munic_s.shp")),
        dbf=io.BytesIO(archive.read("Bdat/SHP/munic_s.dbf")),
        encoding="utf8",
    )
    parts: defaultdict[str, list[shapely.Geometry]] = defaultdict(list)
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None or shape.shapeType == 0 or record["MUS_CO_DES"] == "NO":
            continue
        code = str(record["MUS_CO_GEO"])
        parts[code].append(shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__)))
    found: dict[str, shapely.Geometry] = {}
    for code, shapes in parts.items():
        shape = shapely.simplify(shapely.union_all(shapes), 0.0003, preserve_topology=True)
        pieces = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
        found[code] = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(pieces))), 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Quebec's municipalities that held a general election from 2005 to 2025, each as a polity and an area, with "
        "outlines from the Ministry of Natural Resources and Forests",
    )
    p.external |= {CANADA, QUEBEC, QUEBEC_AREA}
    p.add(
        "source",
        id=MRNF,
        kind="official_body",
        publisher="Ministère des Ressources naturelles et des Forêts",
        title="Découpages administratifs 1/100 000",
        url=SDA_DATASET,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Gouvernement du Québec, Ministère des Ressources naturelles et des Forêts",
    )
    p.artifact(raw.cached(SHAPES, SDA), MRNF)
    for year in DAYS:
        p.uses(raw.cached(SOURCE, url(year)))
    p.add(
        "scheme",
        code="ca-qc.municipality",
        polity_id=QUEBEC,
        kind="administrative",
        rank=3,
        noun="municipality",
        code_system="mamh",
    )
    p.add(
        "scheme",
        code="ca-qc.district",
        polity_id=QUEBEC,
        kind="electoral_district",
        rank=4,
        noun="electoral district",
        code_system=None,
    )
    retrieved = raw.cached(SHAPES, SDA).retrieved_at
    p.add(
        "boundary_set",
        id=key("boundary_set", "ca-qc.municipality", retrieved.year),
        scheme_code="ca-qc.municipality",
        vintage=str(retrieved.year),
        as_of=retrieved.date(),
    )
    shapes = _outlines()
    links = paths()
    for code, text in sorted(municipalities().items()):
        place = area(code)
        p.add("area", id=place, scheme_code="ca-qc.municipality")
        p.add("area_code", area_id=place, code=code)
        p.add("area_parent", area_id=place, parent_id=QUEBEC_AREA)
        p.name(place, text, "fr", "official")
        if code in shapes:
            shape = shapes[code]
            p.add(
                "area_geometry",
                area_id=place,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=MRNF,
            )
        town = polity(code)
        p.add("polity", id=town, kind="municipality", iso_code=None)
        p.name(town, text, "fr", "official")
        p.add("polity_parent", polity_id=town, parent_id=QUEBEC)
        p.add("polity_territory", polity_id=town, area_id=place)
        p.add("slug", path=links[code], entity_id=town, is_current=True)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils, mayoralties, municipal teams, and electoral system of Quebec's municipal elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, QUEBEC}
    p.add(
        "source",
        id=MAMH,
        kind="official_body",
        publisher="Ministère des Affaires municipales et de l'Habitation",
        title="Résultats des élections municipales générales",
        url=DATASET,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© Gouvernement du Québec, Ministère des Affaires municipales et de l'Habitation",
    )
    for year in DAYS:
        p.artifact(raw.cached(SOURCE, url(year)), MAMH)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=QUEBEC,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(SYSTEM, "Quebec municipal plurality voting", "en", "official")
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for code, text in sorted(municipalities().items()):
        body = key("body", code)
        p.add("body", id=body, polity_id=polity(code), kind="council")
        p.name(body, f"{text} municipal council", "en", "official")
        p.name(body, f"Conseil municipal de {text}", "fr", "native", False)
        office = key("office", code)
        p.add("office", id=office, polity_id=polity(code), kind="mayor")
        p.name(office, f"Mayor of {text}", "en", "official")
        p.name(office, f"Maire de {text}", "fr", "native", False)
    towns = municipalities()
    for (mun, arr), text in sorted(boroughs().items()):
        borough = key("body", mun, arr)
        p.add("body", id=borough, polity_id=polity(mun), kind="council")
        p.name(borough, f"{text} borough council, {towns[mun]}", "en", "official")
        office = key("office", mun, arr)
        p.add("office", id=office, polity_id=polity(mun), kind="other")
        p.name(office, f"Borough mayor of {text}, {towns[mun]}", "en", "official")
    for mun, names in sorted(teams().items()):
        for text in sorted(names.values()):
            id = party(mun, text)
            p.add("party", id=id, kind="party", polity_id=polity(mun))
            p.name(id, text, "fr", "official")
    return p


def _units(p: Package, day: date, rows: pl.DataFrame) -> dict[tuple[str, str | None, str | None], str]:
    """One reporting unit for each municipality, and one for each borough or district that voted on its own."""
    units: dict[tuple[str, str | None, str | None], str] = {}
    close = day + timedelta(days=1)
    for mun in sorted(rows["mun"].unique()):
        unit = units[(mun, None, None)] = key("unit", day, mun)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CANADA,
            scheme_code="ca-qc.municipality",
            code=f"{day}:{mun}",
            area_relation="equals",
            area_id=area(mun),
            kind="standard",
            valid_from=day,
            valid_to=close,
        )
    for mun, arr, district in sorted(
        rows.filter(pl.col("arr").is_not_null() | pl.col("district").is_not_null())
        .select("mun", "arr", "district")
        .unique()
        .iter_rows(),
        key=lambda item: (item[0], item[1] or "", item[2] or ""),
    ):
        unit = units[(mun, arr, district)] = key("unit", day, mun, arr, district)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CANADA,
            scheme_code="ca-qc.district",
            code=f"{day}:{mun}:{arr or ''}:{district or ''}",
            parent_id=units[(mun, None, None)],
            area_relation="within",
            area_id=area(mun),
            kind="standard",
            valid_from=day,
            valid_to=close,
        )
    return units


def _posts(rows: pl.DataFrame) -> pl.DataFrame:
    """The candidates of each post once, and posts that the file codes alike but that held separate votes apart.

    A candidate who withdrew can appear twice, once as a withdrawal and once with the votes or the acclamation, and the
    file repeats some withdrawals. The withdrawal is dropped when the same name has another row in the post. Where
    the file gives one post code to several winners with different electorates, each electorate is its own post.
    """
    kept: list[dict[str, object]] = []
    for group in rows.partition_by(["mun", "arr", "role", "poste"], maintain_order=True):
        found: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in group.iter_rows(named=True):
            found.setdefault((fold(str(row["last"] or "")), fold(str(row["first"] or ""))), []).append(row)
        members: list[dict[str, object]] = []
        for same in found.values():
            members += [row for row in same if fold(str(row["status"] or "")) != "desistement"] or same[:1]
        winners = [row for row in members if str(row["status"] or "").lower() in WON]
        electors = {row["electors"] for row in members if row["electors"]}
        apart = len(winners) >= 2 and len(electors) >= 2
        for row in members:
            row["split"] = str(row["electors"] or "") if apart else ""
        kept += members
    return pl.DataFrame(kept, schema={**rows.schema, "split": pl.String})


def _modal(values: list[int | None]) -> tuple[int | None, bool]:
    found = Counter(value for value in values if value is not None)
    if not found:
        return None, False
    return found.most_common(1)[0][0], len(found) > 1


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Quebec municipal general election: every mayor and councillor post, by candidate",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, QUEBEC}
    p.uses(raw.cached(SOURCE, url(year)))
    rows = table(year).filter(~pl.col("status").str.to_lowercase().is_in(list(VACANT)) | pl.col("last").is_not_null())
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=QUEBEC, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} Quebec municipal general election", "en", "official")
    p.name(umbrella, f"Élections municipales générales de {year} au Québec", "fr", "native", False)
    umbrella_path = f"ca/qc/{day}-local"
    p.add("slug", path=umbrella_path, entity_id=umbrella, is_current=True)
    towns = municipalities()
    links = paths()
    arrondissements = boroughs()
    units = _units(p, day, rows)
    elections: dict[str, str] = {}
    for mun in sorted(rows["mun"].unique()):
        id = elections[mun] = key("election", year, mun)
        p.add(
            "election",
            id=id,
            polity_id=polity(mun),
            parent_id=umbrella,
            kind="local",
            date_from=day,
            date_to=day,
            status="held",
        )
        p.name(id, f"{year} {towns[mun]} municipal election", "en", "official")
        p.name(id, f"Élections municipales de {year} à {towns[mun]}", "fr", "native", False)
        p.add("slug", path=f"{links[mun]}/{day}-local", entity_id=id, is_current=True)
    contests = _posts(rows).partition_by(["mun", "arr", "role", "poste", "split"], maintain_order=True, as_dict=True)
    for (mun, arr, role, poste, split), group in sorted(
        contests.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2], _order(item[0][3]), item[0][4])
    ):
        mun, arr, role, poste = str(mun), arr and str(arr), str(role), str(poste)
        tag = f"{mun}:{arr or ''}:{role}:{poste}" + (f":{split}" if split else "")
        seats = (
            1
            if role in ("mayor", "borough_mayor")
            else max(1, group["status"].str.to_lowercase().is_in(list(WON)).sum())
        )
        contest = key("contest", year, tag)
        borough = arr is not None and (mun, arr) in arrondissements
        mayoral = role in ("mayor", "borough_mayor")
        tacit = group["status"].str.to_lowercase().eq("élu sans opposition").any()
        district = group["district"].drop_nulls().head(1).to_list()
        shared = units[(mun, arr, district[0] if district else None)] if (arr or district) else units[(mun, None, None)]
        office = key("office", mun, arr) if role == "borough_mayor" and borough else key("office", mun)
        body = key("body", mun, arr) if role == "borough_councillor" and borough else key("body", mun)
        p.add(
            "contest",
            id=contest,
            election_id=elections[mun],
            kind="office" if mayoral else "body_seats",
            office_id=office if mayoral else None,
            body_id=None if mayoral else body,
            district_area_id=area(mun),
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="tacit" if tacit else "counted",
        )
        label = SECTION[role] + ("" if mayoral else f" {poste}")
        p.name(
            contest,
            f"{towns[mun]}: {label}" + (f" ({arrondissements[(mun, arr)]})" if borough and arr else ""),
            "en",
            "official",
        )
        suffix = (f"borough-{arr}-" if arr else "") + role.replace("_", "-")
        suffix += "" if mayoral else f"-{slug(poste)}"
        suffix += f"-{split}" if split else ""
        p.add("slug", path=f"{links[mun]}/{day}-local/{suffix}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[mun],
            contest_id=contest,
            stage="official",
            kind="tacit" if tacit else "counted",
            source_id=MAMH,
            status="active",
            is_default=True,
            completeness=None if tacit else 1.0,
        )
        electors, electors_differ = _modal([_number(value) for value in group["electors"]])
        rejected, rejected_differ = _modal([_number(value) for value in group["rejected"]])
        if electors_differ or rejected_differ:
            p.add(
                "note",
                id=key("note", result_set, "rows"),
                entity_id=result_set,
                kind="caveat",
                body_md="The candidates of this post carry different figures for the electors or the rejected "
                "ballots. The figures shown are the ones most candidates' rows share.",
                lang="en",
            )
        if not tacit:
            if electors:
                _value(p, result_set, shared, None, 2, electors)
            if rejected is not None:
                _value(p, result_set, shared, None, 5, rejected)
        for position, row in enumerate(group.iter_rows(named=True), start=1):
            if row["last"] is None and row["first"] is None:
                continue
            seq = f"{tag}:{position}"
            person = key("person", year, seq)
            p.add("person", id=person, birth_precision=None)
            ballot = " ".join(part for part in (_name(row["first"]), _name(row["last"])) if part)
            p.name(person, ballot, "fr", "official")
            option = key("option", year, seq)
            team = row["party"]
            member = party(mun, team) if team and fold(team) in teams().get(mun, ()) else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_position=position,
                ballot_label=ballot,
            )
            candidacy = key("candidacy", year, seq)
            incumbent = row["inc"]
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=ballot,
                member_of_party_id=member,
                is_independent=bool(team) and fold(team) in INDEPENDENT[:2],
                is_incumbent=None
                if incumbent is None
                else fold(incumbent).startswith("sortant") and "autre" not in fold(incumbent),
            )
            status = str(row["status"]).lower() if row["status"] else ""
            votes = _number(row["votes"])
            if votes is not None and not tacit:
                _value(p, result_set, shared, option, 1, votes)
            kind = "elected" if status in WON else OUTCOMES.get(status)
            if kind is not None:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    return p


INDEPENDENT = ("independant", "independante", "sans parti", "aucun")


@functools.cache
def teams() -> dict[str, dict[str, str]]:
    """Each municipality's teams across the elections: the name folded to ASCII, and the name as the latest file prints it."""
    found: defaultdict[str, dict[str, str]] = defaultdict(dict)
    for year in DAYS:
        for mun, text in table(year).filter(pl.col("party").is_not_null()).select("mun", "party").unique().iter_rows():
            if fold(str(text)) not in INDEPENDENT:
                found[str(mun)][fold(str(text))] = str(text)
    return dict(found)


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
