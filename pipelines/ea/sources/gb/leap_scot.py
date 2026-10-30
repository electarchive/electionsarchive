import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import dc_local, leap, leh, scottish_parliament, umit
from ea.sources.gb.scot_council import name_key
from ea.store import DATA, connect

SOURCE = "gb.leap_scot"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAYS = {2007: date(2007, 5, 3), 2012: date(2012, 5, 3), 2017: date(2017, 5, 4)}
NEXT = date(2022, 5, 5)
LAYERS = {2007: ("wd16", "lad16", 2016), 2012: ("wd16", "lad16", 2016), 2017: ("wd17", "lad16", 2017)}
OLD_CODES = {"S12000009": "S12000045", "S12000043": "S12000046"}
RECODED = {"S12000015": "S12000047", "S12000024": "S12000048", "S12000046": "S12000049", "S12000044": "S12000050"}
WARD_ALIASES = {("Dumfries and Galloway", "annandaleeast"): "annandaleeastandeskdale"}
LABELS = {
    "C": "Conservative",
    "Lab": "Labour",
    "LD": "Liberal Democrats",
    "SNP": "Scottish National Party",
    "Grn": "Scottish Green Party",
    "SSP": "Scottish Socialist Party",
    "TUSC": "Scottish Trade Unionist and Socialist Coalition",
    "UKIP": "UK Independence Party",
    "BNP": "British National Party",
    "Lib": "Liberal",
    "SDP": "Social Democratic Party",
    "Sol": "Solidarity",
    "SocLab": "Socialist Labour Party",
    "CPA": "Christian Peoples Alliance",
    "NF": "National Front",
    "Pirate": "Pirate Party UK",
    "Loony": "Official Monster Raving Loony Party",
    "Comm": "Communist Party of Britain",
    "Chr": "Scottish Christian Party",
    "SUP": "Scottish Unionist Party",
    "SSCUP": "Scottish Senior Citizens Unity Party",
    "EDIA": "East Dunbartonshire Independent Alliance",
    "9%": "9% Growth Party",
    "CICA": "Cumbernauld Independent Councillors Alliance",
    "Borders": "The Borders Party",
    "EKA": "East Kilbride Alliance",
    "StJH": "Action to Save St Johns Hospital",
    "ASPP": "All Scotland Pensioners Party",
    "Glas1st": "Glasgow First",
    "Brittca": "Britannica",
    "Rubbish": "The Rubbish Party",
    "NRMUPB": "No Referendum, Maintain Union, Pro-Brexit",
    "IANL": "Independent Alliance North Lanarkshire",
    "OMG": "Orkney Manifesto Group",
    "WDuns": "West Dunbartonshire Community Party",
    "Libtn": "Scottish Libertarian Party",
    "RISE": "RISE - Respect, Independence, Socialism and Environmentalism",
    "LHC": "Health Concern",
    "HEP": "Had Enough Party",
    "PICP": "Perth Independent Candidates Party",
    "IndNwk": "Scotland Independent Network",
    "Clydebk": "A Strong Voice for Clydebank",
}
KEYS = {"LHC": "health-concern-fife"}
VOTES = 1
CAVEAT = (
    "The Local Elections Archive Project gives each candidate's first preferences and whether they were elected; "
    "the later stages of the count, the electorate, and rejected ballots are not included."
)


def files() -> dict[int, str]:
    return {year: leap.SITE + leap.FILES[year][0] for year in DAYS}


def fetch() -> None:
    for url in files().values():
        raw.cached(leap.SOURCE, url)
    for key in ("wd16", "wd17", "lad16"):
        for url in dc_local.layer_pages(key):
            raw.cached(dc_local.SOURCE, url)
    for year in (2016, 2017):
        for url in dc_local.lookup_pages(year):
            raw.cached(dc_local.SOURCE, url)


def parse() -> None:
    rows = []
    for year, url in files().items():
        (content,) = leap.members(url, ".csv").values()
        for number, line in enumerate(csv.reader(io.StringIO(content.decode("utf-8")))):
            council, la, ward, ward_code, person, party, votes, elected = (
                None if value.strip() == "NULL" else value.strip() for value in line
            )
            if la is None or not (la.startswith("S") or la[:3] in {"00Q", "00R"}):
                continue
            if party not in LABELS and party != "Ind":
                raise ValueError(f"{year} {council} {ward}: party code {party!r} has no label")
            rows.append(
                {
                    "year": year,
                    "number": number,
                    "council": council,
                    "old_la": OLD_CODES.get(la, la),
                    "ward": ward,
                    "ward_code": ward_code,
                    "name": person,
                    "party": party,
                    "votes": int(votes or 0),
                    "elected": elected == "1",
                }
            )
    councils = {row["council"]: row["old_la"] for row in rows if row["year"] == 2012}
    wards = {(row["council"], name_key(row["ward"])): row["ward_code"] for row in rows if row["year"] == 2012}
    for row in rows:
        if row["year"] == 2007:
            key = name_key(row["ward"])
            row["old_la"] = councils[row["council"]]
            row["ward_code"] = wards[row["council"], WARD_ALIASES.get((row["council"], key), key)]
        row["la_code"] = RECODED.get(row["old_la"], row["old_la"])
    frame = pl.DataFrame(rows, infer_schema_length=None)

    lookups = {}
    for year in (2016, 2017):
        suffix = str(year)[2:]
        for url in dc_local.lookup_pages(year):
            for feature in json.loads(raw.cached(dc_local.SOURCE, url).path.read_text())["features"]:
                attributes = feature["attributes"]
                if attributes[f"WD{suffix}CD"].startswith("S"):
                    lookups.setdefault(year, {})[attributes[f"WD{suffix}CD"]] = (
                        attributes[f"LAD{suffix}CD"],
                        attributes[f"WD{suffix}NM"],
                    )
    by_name = {
        year: {(la, name_key(label)): code for code, (la, label) in found.items()} for year, found in lookups.items()
    }
    wanted: dict[tuple[str, str], str] = {}
    names: dict[str, str] = {}
    for year, la, old_la, ward, ward_code in (
        frame.select("year", "la_code", "old_la", "ward", "ward_code").unique().sort("year").iter_rows()
    ):
        ward_layer, lad_layer, lookup = LAYERS[year]
        found = ward_code if ward_code in lookups[lookup] else by_name[lookup].get((old_la, name_key(ward)))
        if found is None:
            raise ValueError(f"{year} {ward} ({ward_code}): no {ward_layer} ward")
        wanted.setdefault((ward_layer, found), ward_code)
        names.setdefault(ward_code, lookups[lookup][found][1])
        wanted.setdefault((lad_layer, old_la), la)
    with connect() as conn:
        conn.execute("create temp table feature (layer text, code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for key in ("wd16", "wd17", "lad16"):
                _, field, count, _, _ = dc_local.LAYERS[key]
                features = [
                    feature
                    for url in dc_local.layer_pages(key)
                    for feature in json.loads(raw.cached(dc_local.SOURCE, url).path.read_text())["features"]
                ]
                if len(features) != count:
                    raise ValueError(f"{key}: {len(features)} features, expected {count}")
                for feature in features:
                    if (key, feature["properties"][field]) in wanted:
                        code = wanted[key, feature["properties"][field]]
                        copy.write_row((key, code, json.dumps(feature["geometry"])))
        shapes = conn.execute(
            """select layer, code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select layer, code, st_multi(st_collectionextract(st_makevalid(st_union(
                       st_makevalid(st_force2d(st_setsrid(st_geomfromgeojson(geojson), 27700))))), 3)) as geom
                     from feature group by layer, code) whole"""
        ).fetchall()
    missing = {(layer, code) for (layer, _), code in wanted.items()} - {(layer, code) for layer, code, _, _ in shapes}
    if missing:
        raise ValueError(f"no shape for {sorted(missing)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(ons_name=pl.col("ward_code").replace_strict(names)).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(
        shapes,
        schema={"layer": pl.String, "code": pl.String, "geom": pl.String, "label_point": pl.String},
        orient="row",
    ).write_parquet(PARSED / "shapes.parquet")
    for (year,), lines in frame.group_by("year", maintain_order=True):
        print(
            f"{year}: {len(lines):,} candidacies, {lines['ward_code'].n_unique()} wards, "
            f"{lines['la_code'].n_unique()} councils, {lines['elected'].sum():,} elected"
        )


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    shapes = {
        (layer, code): (geom, point) for layer, code, geom, point in pl.read_parquet(PARSED / "shapes.parquet").rows()
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish council elections of 2007, 2012, and 2017 by ward, first preferences and the elected, "
            "from Andrew Teale's Local Elections Archive Project, with ONS ward and council shapes",
            polity="GB-SCT",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        scotland = cs.polity("GB-SCT")
        system = cs.key("gb.electoral_system", "stv")
        sources = {year: cs.source("Andrew Teale", f"Local Elections Archive Project: {year} results") for year in DAYS}
        if cs.minted & {system, *sources.values()}:
            raise ValueError("publish gb.leap and the stv electoral system first")
        ons = {
            key: cs.source("Office for National Statistics", dc_local.LAYERS[key][4])
            for key in ("wd16", "wd17", "lad16")
        }
        for key, id in ons.items():
            service, _, _, _, title = dc_local.LAYERS[key]
            cs.add(
                "source",
                id=id,
                kind="official_body",
                publisher="Office for National Statistics",
                title=title,
                url=f"{dc_local.ONS}/{service}/FeatureServer",
                license="Open Government Licence v3.0",
                license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                attribution=(
                    "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
                    "Contains OS data © Crown copyright and database right"
                ),
                notes="Generalized boundaries (20 m) clipped to the coastline, as of December of the year.",
            )
            for url in dc_local.layer_pages(key):
                cs.artifact(raw.cached(dc_local.SOURCE, url), id)
            if key.startswith("wd"):
                for url in dc_local.lookup_pages(int(key[-2:]) + 2000):
                    cs.artifact(raw.cached(dc_local.SOURCE, url), id)

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        parties: dict[str, UUID] = {}
        for code, label in LABELS.items():
            key = KEYS.get(code) or scottish_parliament.PARTY_KEYS.get(label.lower()) or umit.ALIASES.get(label)
            if key is None and leh.fold(label) in known:
                parties[code] = known[leh.fold(label)]
                continue
            key = key or slug(label)
            parties[code] = cs.key(*umit.EXISTING[key]) if key in umit.EXISTING else cs.key("gb.party", key)
            if parties[code] in cs.minted:
                cs.add("party", id=parties[code], kind="party", polity_id=gb)
                name(parties[code], label)
                print(f"new party {code}: {label}")

        councils: dict[str, tuple[UUID, UUID, UUID, str, str]] = {}
        for code in sorted(set(candidates["la_code"])):
            area = cs.area("gb.lad", code)
            polity = cs.key("gb.polity", code)
            body = cs.key("gb.body", code)
            if cs.minted & {area, polity, body}:
                raise ValueError(f"{code}: publish gb.scot_council first")
            path = conn.execute(
                "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
            ).fetchone()[0]
            authority = conn.execute(
                "select text from name where entity_id = %s and is_preferred limit 1", [body]
            ).fetchone()[0]
            councils[code] = (area, polity, body, path, authority)

        geometry: dict[UUID, tuple[str, str, date]] = {}
        seen: set[UUID] = set()
        days = sorted(DAYS.items())
        for index, (year, day) in enumerate(days):
            end = days[index + 1][1] if index + 1 < len(days) else NEXT
            ward_layer, lad_layer, _ = LAYERS[year]
            national = cs.key("gb.election", f"{day}:local:sct")
            cs.add("election", id=national, polity_id=scotland, kind="local", date_from=day, date_to=day, status="held")
            name(national, f"{year} Scottish local elections")
            cs.add("slug", path=f"gb/scotland/{day}-local", entity_id=national)
            held = candidates.filter(pl.col("year") == year)
            for (code,), lines in held.group_by("la_code", maintain_order=True):
                area, polity, body, path, authority = councils[code]
                geometry.setdefault(area, (lad_layer, code, day))
                cs.add(
                    "body_size",
                    body_id=body,
                    seats=int(lines["elected"].sum()),
                    valid_from=day,
                    valid_from_precision="day",
                    valid_to=end,
                    valid_to_precision="day",
                )
                election = cs.key("gb.election", f"{day}:local:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {authority} election")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                displays = lines.select("ward_code", "ons_name").unique()
                if displays["ons_name"].map_elements(slug, return_dtype=pl.String).n_unique() != len(displays):
                    raise ValueError(f"{year} {code}: two wards share a slug")
                for (ward_code,), ward_lines in lines.group_by("ward_code", maintain_order=True):
                    ward_lines = ward_lines.sort("number")
                    display = ward_lines["ons_name"][0]
                    ward = cs.area("gb.ward", ward_code)
                    if ward not in seen:
                        seen.add(ward)
                        if ward in cs.minted:
                            cs.add("area", id=ward, scheme_code="gb.ward")
                            cs.add("area_code", area_id=ward, code=ward_code, valid_from=None, valid_to=None)
                            cs.add("area_parent", area_id=ward, parent_id=area, valid_from=None, valid_to=None)
                            name(ward, display)
                        geometry.setdefault(ward, (ward_layer, ward_code, day))
                    unit = cs.unit("gb.ward", ward_code)
                    if unit not in seen:
                        seen.add(unit)
                        if unit in cs.minted:
                            cs.add(
                                "reporting_unit",
                                id=unit,
                                polity_id=polity,
                                scheme_code="gb.ward",
                                code=ward_code,
                                area_relation="equals",
                                area_id=ward,
                                kind="standard",
                                valid_from=None,
                            )
                            name(unit, display)
                    tacit = int(ward_lines["votes"].sum()) == 0
                    contest_key = f"{day}:local:{ward_code}"
                    contest = cs.key("gb.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats",
                        body_id=body,
                        district_area_id=ward,
                        magnitude=int(ward_lines["elected"].sum()),
                        round_number=1,
                        electoral_system_id=system,
                        default_result_kind="tacit" if tacit else "counted",
                    )
                    name(contest, f"{display} ward, {authority}, {year}")
                    cs.add("slug", path=f"{path}/{day}-local/{slug(display)}", entity_id=contest)
                    result_set = cs.key("gb.leap_scot.result_set", contest_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="tacit" if tacit else "counted",
                        status="active",
                        source_id=sources[year],
                        is_default=True,
                        completeness=1,
                    )
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=sources[year],
                        locator={"council": ward_lines["council"][0], "ward": ward_lines["ward"][0]},
                    )
                    if not tacit:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEAT)
                    for line in ward_lines.iter_rows(named=True):
                        option_key = f"{contest_key}:{line['number']}"
                        owner = parties.get(line["party"])
                        full = re.sub(r"(?<=\bO)[?']{1,2}(?=[A-Z])", "'", line["name"])
                        option = cs.key("gb.leap_scot.option", option_key)
                        person = cs.key("gb.leap_scot.person", option_key)
                        cs.add("person", id=person)
                        name(person, full)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=owner,
                            ballot_label=full,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("gb.leap_scot.candidacy", option_key),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=line["name"],
                            member_of_party_id=owner,
                            is_independent=line["party"] == "Ind",
                        )
                        if not tacit:
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=VOTES,
                                count_round=1,
                                flags=0,
                                value=line["votes"],
                            )
                        if line["elected"]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")

        others: dict[UUID, list[tuple[date | None, date | None]]] = {}
        for area, start, end in conn.execute(
            "select g.area_id, lower(g.valid), upper(g.valid) from area_geometry g join changeset c on c.id = g.changeset_id"
            " where g.area_id = any(%s) and c.pipeline <> %s",
            [list(geometry), SOURCE],
        ).fetchall():
            others.setdefault(area, []).append((start, end))
        for area, (layer, code, day) in geometry.items():
            spans = others.get(area, [])
            if any((start is None or start <= day) and (end is None or end > day) for start, end in spans):
                continue
            later = [start for start, _ in spans if start is not None and start > day]
            geom, point = shapes[(layer, code)]
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=day,
                valid_to=min(later) if later else None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ons[layer],
            )
        plans = {
            (scheme, vintage): (id, start)
            for id, scheme, vintage, start in conn.execute(
                "select id, scheme_code, vintage, lower(valid) from boundary_set where scheme_code in ('gb.ward', 'gb.lad')"
            ).fetchall()
        }
        starts = sorted(
            {
                *DAYS.values(),
                *(
                    start
                    for (scheme, vintage), (_, start) in plans.items()
                    if start is not None and vintage not in {str(year) for year in DAYS}
                ),
            }
        )
        for scheme in ("gb.ward", "gb.lad"):
            for year, day in days:
                cs.add(
                    "boundary_set",
                    **({"id": plans[(scheme, str(year))][0]} if (scheme, str(year)) in plans else {}),
                    scheme_code=scheme,
                    vintage=str(year),
                    valid_from=day,
                    valid_to=next((start for start in starts if start > day), None),
                    as_of=day,
                )
        return cs.write()
