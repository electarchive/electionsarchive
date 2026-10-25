import csv
import html
import json
import re
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import cauth, leh, london
from ea.store import DATA, connect

SOURCE = "gb.pcc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LAYER = "Police_Force_Areas_Dec_2024_EW_BGC"
TITLE = "Police Force Areas (December 2024) Boundaries EW BGC"
AREAS = {
    "E23000002": "Cumbria",
    "E23000003": "Lancashire",
    "E23000004": "Merseyside",
    "E23000005": "Greater Manchester",
    "E23000006": "Cheshire",
    "E23000007": "Northumbria",
    "E23000008": "Durham",
    "E23000009": "North Yorkshire",
    "E23000010": "West Yorkshire",
    "E23000011": "South Yorkshire",
    "E23000012": "Humberside",
    "E23000013": "Cleveland",
    "E23000014": "West Midlands",
    "E23000015": "Staffordshire",
    "E23000016": "West Mercia",
    "E23000017": "Warwickshire",
    "E23000018": "Derbyshire",
    "E23000019": "Nottinghamshire",
    "E23000020": "Lincolnshire",
    "E23000021": "Leicestershire",
    "E23000022": "Northamptonshire",
    "E23000023": "Cambridgeshire",
    "E23000024": "Norfolk",
    "E23000025": "Suffolk",
    "E23000026": "Bedfordshire",
    "E23000027": "Hertfordshire",
    "E23000028": "Essex",
    "E23000029": "Thames Valley",
    "E23000030": "Hampshire",
    "E23000031": "Surrey",
    "E23000032": "Kent",
    "E23000033": "Sussex",
    "E23000035": "Devon and Cornwall",
    "E23000036": "Avon and Somerset",
    "E23000037": "Gloucestershire",
    "E23000038": "Wiltshire",
    "E23000039": "Dorset",
    "W15000001": "North Wales",
    "W15000002": "Gwent",
    "W15000003": "South Wales",
    "W15000004": "Dyfed-Powys",
}
FIRE = {
    "E23000028": date(2017, 10, 1),
    "E23000015": date(2018, 8, 1),
    "E23000009": date(2018, 11, 15),
    "E23000022": date(2019, 1, 1),
    "E23000002": date(2023, 4, 1),
}
BY_ELECTIONS = {"2014-08-21", "2014-10-30", "2021-08-19", "2021-11-25"}

VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED = 1, 2, 3, 4, 5, 21
MEASURES = {"REJECTED": INVALID, "ELECTORATE": REGISTERED, "BALLOTS": BALLOTS}
SV_UNTIL = date(2023, 5, 4)
RESULTS = Path(__file__).with_name("pcc_results.tsv")
with Path(__file__).with_name("pcc_sources.tsv").open() as handle:
    SOURCES = {row["url"]: (row["publisher"], row["title"]) for row in csv.DictReader(handle, delimiter="\t")}
with Path(__file__).with_name("pcc_people.tsv").open() as handle:
    PEOPLE = {(row["area"], row["name"]): (row["qid"], row["label"]) for row in csv.DictReader(handle, delimiter="\t")}
PARTIES: dict[str, str | None] = cauth.PARTIES | {
    "Conservative": "Conservative Party",
    "Labour": "Labour Party",
    "Liberal Democrat": "Liberal Democrats",
    "UKIP": "UK Independence Party",
    "English Democrat": "English Democrats",
}
DESCRIPTIONS = (
    (r"(The |Welsh )*Conservative", "Conservative Party"),
    (r"(The )?Labour", "Labour Party"),
    (r"(Welsh )?Liberal Democrat", "Liberal Democrats"),
    (r"English Democrat", "English Democrats"),
    (r"(The )?Green Party", "Green Party"),
    (r"Zero Tolerance Policing", "Zero Tolerance Policing ex Chief"),
)


def office_name(code: str, day: date) -> str:
    fire = FIRE.get(code)
    kind = (
        "Police, Fire and Crime Commissioner" if fire is not None and day >= fire else "Police and Crime Commissioner"
    )
    return f"{AREAS[code]} {kind}"


def pages() -> list[str]:
    services = cauth.SERVICES
    count = json.loads(
        raw.fetch(
            SOURCE, f"{services}/{LAYER}/FeatureServer/0/query?where=1%3D1&returnCountOnly=true&f=json", suffix=".json"
        ).path.read_text()
    )["count"]
    if count > 1000:
        raise ValueError(f"{LAYER} has {count} features")
    return [f"{services}/{LAYER}/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"]


def fetch() -> None:
    for url in SOURCES:
        raw.cached(SOURCE, url)
    for url in pages():
        if "features" not in json.loads(raw.fetch(SOURCE, url, suffix=".geojson").path.read_text()):
            raise ValueError(f"{url} returned no features")


def parse() -> None:
    drawn = {
        str(feature["properties"]["PFA24CD"]): json.dumps(feature["geometry"])
        for url in pages()
        for feature in json.loads(raw.cached(SOURCE, url).path.read_text())["features"]
    }
    if missing := set(AREAS) - set(drawn):
        raise ValueError(f"no shapes for {sorted(missing)}")
    with connect() as conn:
        conn.execute("create temp table feature (code text, geojson text) on commit drop")
        with conn.cursor() as cursor, cursor.copy("copy feature from stdin") as copy:
            for code in sorted(AREAS):
                copy.write_row((code, drawn[code]))
        rows = conn.execute(
            """select code,
                      encode(st_asewkb(st_multi(st_transform(geom, 4326))), 'hex'),
                      encode(st_asewkb(st_transform(st_pointonsurface(geom), 4326)), 'hex')
               from (select code, st_multi(st_collectionextract(st_makevalid(st_force2d(
                       st_setsrid(st_geomfromgeojson(geojson), 27700))), 3)) as geom from feature) shape"""
        ).fetchall()
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows, schema={"code": pl.String, "geom": pl.String, "label_point": pl.String}, orient="row"
    ).write_parquet(PARSED / "areas.parquet")


def emit() -> Path:
    shapes = pl.read_parquet(PARSED / "areas.parquet")
    table = pl.read_csv(RESULTS, separator="\t", infer_schema=False)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Police and crime commissioners in England and Wales: the 41 police areas outside London "
            "(ONS shapes) as polities with their commissioners, and every commissioner election 2012-2024 "
            "with the 2014 and 2021 by-elections, from the Commons Library and the police area returning "
            "officers' declarations",
            polity="GB",
        )
        gb = cs.polity("GB")

        def name(
            entity: UUID,
            text: str,
            valid_from: date | None = None,
            valid_to: date | None = None,
            preferred: bool = True,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang="en",
                kind="official",
                is_preferred=preferred,
                valid_from=valid_from,
                valid_to=valid_to,
            )

        if conn.execute("select 1 from scheme where code = 'gb.pfa'").fetchone() is None:
            cs.add("scheme", code="gb.pfa", polity_id=gb, kind="administrative", rank=2, noun="police area")
        ons = cs.source(cauth.PUBLISHER, TITLE)
        cs.add(
            "source",
            id=ons,
            kind="official_body",
            publisher=cauth.PUBLISHER,
            title=TITLE,
            url=f"{cauth.SERVICES}/{LAYER}/FeatureServer",
            license=cauth.LICENSE,
            license_url=cauth.LICENSE_URL,
            attribution=cauth.ATTRIBUTION,
        )
        for url in pages():
            cs.artifact(raw.cached(SOURCE, url), ons)
        cs.add("boundary_set", scheme_code="gb.pfa", vintage="2024", valid_from=None, valid_to=None, as_of="2024-12-01")
        for code, geom, point in shapes.sort("code").rows():
            area = cs.area("gb.pfa", code)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                valid_to=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ons,
            )
            nation = "ENG" if code.startswith("E") else "WLS"
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.pfa")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add(
                    "area_parent", area_id=area, parent_id=cs.area("gb.country", nation), valid_from=None, valid_to=None
                )
                name(area, f"{AREAS[code]} police area")
            polity = cs.key("gb.polity", code)
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="special_district", iso_code=None)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"GB-{nation}"))
                cs.add("polity_territory", polity_id=polity, area_id=area)
                name(polity, f"{AREAS[code]} police area")
                cs.add("slug", path=f"gb/{slug(AREAS[code])}-police-area", entity_id=polity)
            office = cs.key("gb.office", f"pcc:{code}")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=polity, body_id=None, kind="other")
                fire = FIRE.get(code)
                if fire is None:
                    name(office, office_name(code, date(2012, 11, 15)))
                else:
                    name(office, office_name(code, fire), fire)
                    name(office, office_name(code, date(2012, 11, 15)), None, fire, False)

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        parties: dict[str, UUID | None] = {}

        def party(label: str) -> UUID | None:
            label = " ".join(label.split())
            if label not in parties:
                resolved = (
                    PARTIES[label]
                    if label in PARTIES
                    else london.RESOLVED[label]
                    if label in london.RESOLVED
                    else next((party for pattern, party in DESCRIPTIONS if re.match(pattern, label)), label)
                )
                if resolved is None or resolved.startswith("Independent"):
                    parties[label] = None
                elif resolved in cauth.PARTY_KEYS:
                    parties[label] = cs.key(*cauth.PARTY_KEYS[resolved])
                elif resolved in leh.RESOLVED:
                    parties[label] = cs.key(*leh.RESOLVED[resolved])
                elif (found := known.get(leh.fold(resolved))) is not None:
                    parties[label] = found
                else:
                    minted = cs.key("gb.party", slug(resolved))
                    if minted not in cs.minted:
                        raise ValueError(f"party label {label!r} ({resolved!r}) does not resolve")
                    if minted not in parties.values():
                        cs.add("party", id=minted, kind="party", polity_id=gb)
                        name(minted, resolved)
                    parties[label] = minted
            return parties[label]

        persons: dict[str, UUID] = {}

        def person(code: str, full: str) -> UUID:
            qid, label = PEOPLE.get((code, full), (None, full))
            key = qid or full
            if key not in persons:
                persons[key] = cs.key("wikidata", qid) if qid is not None else cs.key("gb.pcc.person", full)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], label)
            return persons[key]

        printed_cache: dict[str, set[str]] = {}

        def printed(url: str) -> set[str]:
            if url not in printed_cache:
                path = raw.cached(SOURCE, url).path
                if path.suffix == ".pdf":
                    text = subprocess.run(
                        ["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True
                    ).stdout.decode(errors="replace")
                elif path.suffix == ".xlsx":
                    book = fastexcel.read_excel(path)
                    text = " ".join(
                        str(cell)
                        for sheet in book.sheet_names
                        for row in book.load_sheet_by_name(sheet, header_row=None).to_polars().rows()
                        for cell in row
                        if cell is not None
                    )
                elif path.suffix == ".docx":
                    with zipfile.ZipFile(path) as archive:
                        text = re.sub(
                            r"<[^>]+>", "", re.sub(r"</w:p>|</w:tc>", " ", archive.read("word/document.xml").decode())
                        )
                else:
                    text = html.unescape(re.sub(r"<[^>]+>", " ", path.read_text(errors="replace")))
                printed_cache[url] = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3}\b)", "", text)))
            return printed_cache[url]

        units: dict[str, UUID] = {}

        def unit(code: str, polity: UUID) -> UUID:
            if code not in units:
                units[code] = cs.unit("gb.pfa", code)
                if units[code] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=polity,
                        scheme_code="gb.pfa",
                        code=code,
                        area_relation="equals",
                        area_id=cs.area("gb.pfa", code),
                        kind="standard",
                        valid_from=None,
                    )
                    name(units[code], f"{AREAS[code]} police area")
            return units[code]

        fptp = cs.key("gb.electoral_system", "fptp")
        sv = cs.key("gb.electoral_system", "sv")
        if fptp in cs.minted or sv in cs.minted:
            raise ValueError("publish gb.parliament and gb.mayors first")
        sources: dict[str, UUID] = {}

        def cite(url: str) -> UUID:
            if url not in sources:
                publisher, title = SOURCES[url]
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(raw.cached(SOURCE, url), sources[url])
            return sources[url]

        for day_text, code in sorted({(day, code) for day, code in table.select("day", "area").rows()}):
            day = date.fromisoformat(day_text)
            polity = cs.key("gb.polity", code)
            office_label = office_name(code, day)
            rows = table.filter((pl.col("day") == day_text) & (pl.col("area") == code))
            candidates: list[tuple[str, str, str, int, int | None]] = []
            figures: dict[int, tuple[int, str]] = {}
            for row in rows.iter_rows(named=True):
                value = int(row["round1"])
                if str(value) not in printed(row["source"]):
                    raise ValueError(f"{day} {code}: {row['printed']} {value} not printed in {row['source']}")
                if row["printed"] in MEASURES:
                    figures[MEASURES[row["printed"]]] = (value, row["source"])
                    continue
                shown = row["round2"] or row["second"]
                if shown and shown not in printed(row["source2"] or row["source"]):
                    raise ValueError(
                        f"{day} {code}: {row['printed']} {shown} not printed in {row['source2'] or row['source']}"
                    )
                final = int(row["round2"]) if row["round2"] else value + int(row["second"]) if row["second"] else None
                candidates.append((row["name"], row["printed"], row["party"], value, final))
            supplementary = day < SV_UNTIL and len(candidates) > 2
            first_round = 1 if supplementary else 0
            second = supplementary and any(final is not None for *_, final in candidates)
            if (
                supplementary
                and not second
                and max(first for *_, first, _ in candidates) * 2 <= sum(first for *_, first, _ in candidates)
            ):
                raise ValueError(f"{day} {code}: no first-round majority and no second round")
            by_election = day_text in BY_ELECTIONS
            national = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s", [f"{day}:local:gb"]
            ).fetchone()
            election = cs.key("gb.election", f"{day}:pcc:{code}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None if national is None or by_election else national[0],
                kind="by_election" if by_election else "general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day.year} {office_label} {'by-election' if by_election else 'election'}")
            path = f"gb/{slug(AREAS[code])}-police-area/{day}-{'pcc-by-election' if by_election else 'pcc'}"
            cs.add("slug", path=path, entity_id=election)
            contest_key = f"{day}:pcc:{code}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=cs.key("gb.office", f"pcc:{code}"),
                body_id=None,
                district_area_id=cs.area("gb.pfa", code),
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="remainder" if by_election else "full",
            )
            name(contest, f"{office_label}, {day.year}")
            cs.add("slug", path=f"{path}/commissioner", entity_id=contest)
            source = cite(rows["source"][0])
            result_set = cs.key("gb.pcc.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("citation", entity_id=result_set, source_id=source)
            for url in (set(rows["source"]) | set(rows["source2"])) - {None, rows["source"][0]}:
                cs.add("citation", entity_id=result_set, source_id=cite(url))
            whole = unit(code, polity)
            valid = sum(first for *_, first, _ in candidates)
            cells = [(VALID, first_round, valid)] + [(measure, 0, value) for measure, (value, _) in figures.items()]
            if second:
                cells.append((EXHAUSTED, 2, valid - sum(final or 0 for *_, final in candidates)))
            for measure, number, value in cells:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=whole,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=value,
                )
            winner = max(candidates, key=lambda row: (row[4] or 0, row[3]))[0]
            for full, ballot, label_text, first, final in candidates:
                owner = party(label_text) if label_text else None
                option_key = f"{contest_key}:{slug(full)}"
                option = cs.key("gb.pcc.option", option_key)
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
                    id=cs.key("gb.pcc.candidacy", option_key),
                    person_id=person(code, full),
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    member_of_party_id=owner,
                    is_independent=owner is None,
                )
                rounds = {first_round: first}
                if second and final is not None:
                    rounds[2] = final
                for number, votes in rounds.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=whole,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
                if full == winner:
                    kinds: tuple[str, ...] = ("won", "elected")
                elif second and final is None:
                    kinds = ("eliminated",)
                else:
                    kinds = ("lost",)
                for kind in kinds:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        return cs.write()
