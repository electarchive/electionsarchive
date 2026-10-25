import csv
import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nd_local"
VERSION = "1"
PUBLISHER = "North Dakota Secretary of State"
TITLE = "Election results portal"
EXPORT = "https://results.sos.nd.gov/ResultsExport.aspx?eid="
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_38_place_500k.zip"
KINDS = {"25": "City", "43": "Town"}
PARSED = DATA / "parsed" / SOURCE
ELECTIONS = [
    ("Ug32Itd2SDQ.", date(2000, 6, 13)),
    ("LMRgBX6mD1A.", date(2000, 11, 7)),
    ("UMBawkGvl-U.", date(2002, 6, 11)),
    ("uCduaMfbZTw.", date(2002, 11, 5)),
    ("NVuv10sW5FY.", date(2004, 6, 8)),
    ("f4_9wSod8rs.", date(2004, 11, 2)),
    ("w3d71_B1QYk.", date(2006, 6, 13)),
    ("YRm9d8aOerM.", date(2006, 11, 7)),
    ("3p15Rjra0is.", date(2008, 6, 3)),
    ("PyBKp-DbstU.", date(2008, 11, 4)),
    ("UyRxWo3653c.", date(2010, 6, 8)),
    ("SZ68n1F5X-M.", date(2010, 11, 2)),
    ("PJsJzM4LbMc.", date(2012, 6, 12)),
    ("vp_Q9Zt7wF4.", date(2012, 11, 6)),
    ("Q5exdCNiAsM.", date(2014, 6, 10)),
    ("XunCiWF0O8w.", date(2014, 11, 4)),
    ("gR2niKy4-fY.", date(2016, 6, 14)),
    ("NT3DecKycoM.", date(2016, 11, 8)),
    ("YWxkP9oDgHk.", date(2018, 6, 12)),
    ("bdXDzl1YpEQ.", date(2018, 11, 6)),
    ("ae0__NBsfOw.", date(2020, 6, 9)),
    ("StuhWbgeuSk.", date(2020, 11, 3)),
    ("Next2sICxjI.", date(2022, 6, 14)),
    ("vxUYQ0lrpP4.", date(2022, 11, 8)),
    ("OhimOTJtayc.", date(2024, 6, 11)),
    ("", date(2024, 11, 5)),
]
STATE = re.compile(
    r"(?i)^(?:united states|representative in congress|state senator|state representative|governor|secretary of state"
    r"|attorney general|public service|tax commissioner|agriculture|insurance|judge of the district|justice of the"
    r"|state senate|state house|president|initiated|constitutional|referred|legislatively|superintendent of public|state auditor|state treasurer"
    r"|auditor$|treasurer$|lieutenant|measure no|[a-z ]*statutory|constitutional measure)"
)
PARTIES = {"DEM": "democratic-party", "REP": "republican-party", "LIB": "libertarian-party"}
INDEPENDENT = {"IND"}
BODY = re.compile(r"(?i)commissioner|council|board|commission|committee|supervisor|director|trustee")
OFFICE = re.compile(
    r"(?i)^(?:county )?(commissioner|auditor|treasurer|recorder|sheriff|state's attorney|states attorney|clerk of|agent|"
    r"official|school board|council|mayor|city|park|supervisor|director|judge|municipal)"
)
METHOD = (
    "Totals are from the North Dakota Secretary of State's election results portal, which reports each local contest "
    "as one total for its county, city, or district. The portal declares no winners, so winners of general elections "
    "are the candidates with the most votes for the seats on the ballot."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def title_case(text: str) -> str:
    small = {"of", "and", "the", "for", "to", "a", "in", "on", "at", "by"}
    words = re.sub(r"\s+", " ", text).strip(" -.,").lower().split(" ")
    return " ".join(w if i and w in small else w.capitalize() for i, w in enumerate(words))


def fetch() -> None:
    raw.fetch(SOURCE, PLACES, suffix=".zip")
    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)
    for eid, day in ELECTIONS:
        try:
            raw.cached(SOURCE, EXPORT + eid)
            continue
        except LookupError:
            pass

        def hidden(page: str) -> dict[str, str]:
            form = {}
            for tag in re.findall(r'<input[^>]*type="hidden"[^>]*>', page):
                field = re.search(r'name="([^"]*)"', tag)
                value = re.search(r'value="([^"]*)"', tag)
                if field:
                    form[field[1]] = value[1] if value else ""
            return form

        client.get(
            f"https://results.sos.nd.gov/Default.aspx?map=Cty&eid={eid}"
            if eid
            else "https://results.sos.nd.gov/Default.aspx"
        )
        page = client.get(EXPORT + eid)
        form = hidden(page.text)
        if datetime.strptime(form["ctl00$hidElectionDate"], "%m/%d/%Y").date() != day:
            raise ValueError(f"{eid} is {form['ctl00$hidElectionDate']}, not {day}")
        form.update({"ctl00$MainContent$rblTypes": "1", "ctl00$MainContent$ctl01": "All Contests"})
        response = client.post(EXPORT + eid, data=form)
        if not response.content.startswith(b"ContestName"):
            raise ValueError(f"{eid}: no CSV in the response")
        raw.store(
            SOURCE,
            EXPORT + eid,
            response.content,
            suffix=".csv",
            retrieved_at=datetime.now(UTC),
            media_type="text/csv",
        )
        print(day, len(response.content), flush=True)


def parse() -> None:
    rows: list[dict[str, Any]] = []
    for eid, day in ELECTIONS:
        text = raw.cached(SOURCE, EXPORT + eid).path.read_text(encoding="utf-8", errors="replace")
        for record in list(csv.reader(io.StringIO(text)))[1:]:
            if len(record) < 9:
                continue
            contest = re.sub(r"\s+", " ", record[0].replace("#DistrictName#", "")).strip()
            area = re.sub(r"\s+", " ", record[2]).strip()
            if (
                not area
                or STATE.match(contest)
                or re.search(r"(?i)legislative district", area)
                or re.fullmatch(r"(District )?\d+", area)
            ):
                continue
            rows.append(
                {
                    "day": day,
                    "contest": contest,
                    "area": area,
                    "party": record[1].strip().upper(),
                    "candidate": re.sub(r"\s+", " ", record[3]).strip(),
                    "allowed": int(record[4] or 1),
                    "votes": int(record[5] or 0),
                    "position": len(rows),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "rows.parquet")
    print(frame.group_by("day").agg(pl.struct("contest", "area").n_unique()).sort("day"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        records = []
        with zipfile.ZipFile(raw.cached(SOURCE, PLACES).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        for record, shape in zip(reader.records(), reader.shapes(), strict=True):
            if record is None or shape is None or record["LSAD"] not in KINDS:
                continue
            records.append((record.as_dict(), shape.__geo_interface__))
        places = {
            str(record["NAME"]).upper(): (str(record["GEOID"]), KINDS[str(record["LSAD"])], shape)
            for record, shape in records
        }
        counties = {
            re.sub(r" County$", "", text).upper(): (code, polity, text, path)
            for path, text, code, polity in conn.execute(
                """select s.path, n.text, c.code, p.id from polity p join slug s on s.entity_id = p.id
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join polity_territory t on t.polity_id = p.id join area a on a.id = t.area_id
                   join area_code c on c.area_id = a.id
                   where s.path ~ '^us/nd/[a-z-]+-county$' and p.kind = 'county' and a.scheme_code = 'us.county'"""
            ).fetchall()
        }
        if len(counties) != 53:
            raise ValueError("North Dakota needs 53 counties")
        held = {
            code: (polity, text, path)
            for code, polity, text, path in conn.execute(
                """select c.code, p.id, n.text, s.path from polity p join slug s on s.entity_id = p.id
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join polity_territory t on t.polity_id = p.id join area a on a.id = t.area_id
                   join area_code c on c.area_id = a.id
                   where s.path ~ '^us/nd/[a-z-]+$' and p.kind = 'municipality' and a.scheme_code = 'us.place'"""
            ).fetchall()
        }
        foreign = {
            table: dict(
                conn.execute(
                    f"""select s.path, s.entity_id from slug s join {table} t on t.id = s.entity_id
                        join changeset c on c.id = t.changeset_id
                        where s.path like 'us/nd/%%' and c.pipeline <> %s""",
                    [SOURCE],
                ).fetchall()
            )
            for table in ("election", "contest")
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/nd/%'").fetchall()}

        cs = Changeset(
            conn, pipeline=SOURCE, version=VERSION, summary="North Dakota's local elections, 2000–2024", polity="US-ND"
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://results.sos.nd.gov/",
                license="Public record",
            )
        for eid, _ in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, EXPORT + eid), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.artifact(raw.cached(SOURCE, PLACES), census)
        us = cs.polity("US")
        state = cs.polity("US-ND")
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "referendum")}
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the electoral systems and parties must already exist")
        cs.add(
            "scheme", code="us.special_district", polity_id=us, kind="administrative", rank=3, noun="special district"
        )

        polities: dict[str, tuple[UUID, str, str, str, str]] = {}

        def resolve(area: str, contest: str) -> tuple[str, str | None]:
            found = re.fullmatch(r"(?:\d+ - )?(.+?)(?: - (District \d+))?", area)
            base = found[1].upper() if found else area.upper()
            seat = found[2] if found else None
            if base in counties:
                return counties[base][0], seat
            if base in places:
                return places[base][0], seat
            ward = re.fullmatch(r"(.+?) (Ward \d+)", base, re.IGNORECASE)
            if ward and ward[1].upper() in places:
                return places[ward[1].upper()][0], seat or ward[2].title()
            return f"special:{slug(area)}", seat

        owners: dict[tuple[str, str], UUID] = {}
        persons: dict[UUID, str] = {}
        elections: dict[tuple[date, str, str], UUID] = {}
        districts: set[str] = set()
        paths: set[str] = set()
        by_contest = frame.group_by("day", "contest", "area", maintain_order=True)
        order = []
        for (day, contest_name, area), rows in by_contest:
            seen = {p for p in rows["party"].to_list() if p in PARTIES}
            primary = day.month == 6 and bool(seen)
            if len(seen) > 1:
                continue
            labels = {c.lower() for c in rows["candidate"].unique().to_list()}
            question = bool(labels) and labels <= {"yes", "no"}
            code, seat = resolve(area, contest_name)
            text = re.sub(r"(?i)\s*[-,]?\s*unexpired[^,]*$", "", contest_name)
            text = re.sub(r"(?i)\s*\bat[ -]large\b( by district)?", "", text) if not question else text
            text = re.sub(r"(?i)\s*\bby district\b|\s*district\s*#\d+|\s*\d+(st|nd|rd|th) position.*$", "", text)
            text = re.sub(r"(?i)^county commissioner$|^commissioner$", "County Commissioner", text.strip())
            if not seat and re.search(r"(?i)at[ -]large", contest_name) and not question:
                seat = "At-Large"
            order.append(
                {
                    "day": day,
                    "name": contest_name,
                    "area": area,
                    "code": code,
                    "seat": seat,
                    "text": text.strip(" -,."),
                    "question": question,
                    "remainder": bool(re.search(r"(?i)unexpired", contest_name)),
                    "scope": next(iter(seen)) if primary and seen else None,
                    "primary": primary,
                    "allowed": int(rows["allowed"][0]),
                    "rows": rows,
                }
            )
        used_places = {row["code"] for row in order if row["code"] in {v[0] for v in places.values()}}
        for text, (code, kind, shape) in places.items():
            if code not in used_places:
                continue
            title = title_case(text)
            if code in held:
                polities[code] = (*held[code], "place", code)
                continue
            polity = cs.key("us.polity", f"place:{code}")
            path = f"us/nd/{slug(title)}"
            polities[code] = (polity, title, path, "place", code)
            if polity not in cs.minted:
                continue
            if path in taken:
                path = f"{path}-{kind.lower()}"
                polities[code] = (polity, title, path, "place", code)
                if path in taken:
                    raise ValueError(f"{title} would take {path}")
            geom, point = conn.execute(
                """select encode(st_asewkb(g), 'hex'), encode(st_asewkb(st_pointonsurface(g)), 'hex') from (
                       select st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(%s), 4269), 4326)), 3)) as g) s""",
                [json.dumps(shape)],
            ).fetchone()
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, title)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area_id = cs.area("us.place", code)
            cs.add("area", id=area_id, scheme_code="us.place")
            cs.add("area_code", area_id=area_id, code=code, valid_from=None)
            cs.add("area_parent", area_id=area_id, parent_id=cs.area("us.state", "38"))
            name(area_id, f"{title}, ND")
            cs.add("polity_territory", polity_id=polity, area_id=area_id)
            cs.add(
                "area_geometry",
                area_id=area_id,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.place", code),
                polity_id=polity,
                scheme_code="us.place",
                code=code,
                area_relation="equals",
                area_id=area_id,
                kind="standard",
                valid_from=None,
            )
        for code, polity_info in {v[0]: v for v in counties.values()}.items():
            polities[code] = (polity_info[1], polity_info[2], polity_info[3], "county", code)
        for item in order:
            code = item["code"]
            if code.startswith("special:") and code not in polities:
                title = title_case(item["area"])
                polity = cs.key("us.polity", f"special:{code}")
                path = f"us/nd/{slug(title)}"
                while path in taken or path in {p[2] for p in polities.values()}:
                    path += "-district"
                polities[code] = (polity, title, path, "special", code)
                if polity in cs.minted:
                    cs.add("polity", id=polity, kind="special_district", iso_code=None)
                    name(polity, title)
                    cs.add("polity_parent", polity_id=polity, parent_id=state)
                    cs.add("slug", path=path, entity_id=polity)
                    area_id = cs.area("us.special_district", code)
                    cs.add("area", id=area_id, scheme_code="us.special_district")
                    cs.add("area_code", area_id=area_id, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area_id, parent_id=cs.area("us.state", "38"))
                    name(area_id, f"{title}, ND")
                    cs.add("polity_territory", polity_id=polity, area_id=area_id)
                    cs.add(
                        "reporting_unit",
                        id=cs.unit("us.special_district", code),
                        polity_id=polity,
                        scheme_code="us.special_district",
                        code=code,
                        area_relation="equals",
                        area_id=area_id,
                        kind="standard",
                        valid_from=None,
                    )

        keys: dict[int, str] = {}
        for index, item in enumerate(order):
            office = "question" if item["question"] else slug(item["text"] or "office")
            value = (
                f"{item['day']}:ND:{item['code']}:{office}:{slug(item['seat'] or '')}"
                f":{(item['scope'] or '').lower()}{':remainder' if item['remainder'] else ''}"
                + (f":q{slug(item['name'])[:60]}" if item["question"] else "")
            )
            while value in keys.values():
                value += f":{index}"
            keys[index] = value
        for index, item in sorted(enumerate(order), key=lambda pair: (pair[1]["day"], pair[1]["code"], keys[pair[0]])):
            day, code, question = item["day"], item["code"], item["question"]
            polity, place, path, owner_kind, _ = polities[code]
            election_kind = "primary" if item["primary"] else "general" if day.month == 11 else "local"
            label = {"general": "general", "primary": "primary", "local": "municipal"}[election_kind]
            scheme = {"county": "us.county", "place": "us.place", "special": "us.special_district"}[owner_kind]
            text = item["text"]
            if not question and not text:
                continue
            body = (
                not question
                and bool(BODY.search(text))
                and not re.search(r"(?i)auditor|treasurer|recorder|sheriff|attorney|clerk|mayor|judge|agent", text)
            )
            heading = title_case(text)
            if owner_kind == "county" and heading.startswith("County "):
                heading = heading[7:]
            if question:
                heading = title_case(re.split(r"[:.]", item["name"], maxsplit=1)[0])[:90]
            office = "question" if question else slug(text)
            if not question and (code, office) not in owners:
                owner = owners[(code, office)] = cs.key("us.body" if body else "us.office", f"ND:{code}:{office}")
                if owner in cs.minted:
                    if body:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add("office", id=owner, polity_id=polity, kind="mayor" if office == "mayor" else "other")
                    name(owner, f"{place} {heading}")
            if (day, code, election_kind) not in elections and f"{path}/{day}-{election_kind}" in foreign["election"]:
                elections[(day, code, election_kind)] = foreign["election"][f"{path}/{day}-{election_kind}"]
            if (day, code, election_kind) not in elections:
                election = elections[(day, code, election_kind)] = cs.key(
                    "us.election", f"{day}:ND:{code}:{election_kind}"
                )
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=election_kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {place} {label} election")
                cs.add("slug", path=f"{path}/{day}-{election_kind}", entity_id=election)
            election = elections[(day, code, election_kind)]
            whole = area = cs.area(scheme, code)
            unit = cs.unit(scheme, code)
            seat = item["seat"]
            district = not question and seat is not None and seat != "At-Large"
            if district:
                key = f"38:{code}:{office}:{slug(seat)}"
                area = cs.area("us.local_district", key)
                unit = cs.unit("us.local_district", key)
                if key not in districts:
                    districts.add(key)
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=key, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{place} {heading}, {seat}")
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=key,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
            contest_key = keys[index]
            contest_path = (
                f"{path}/{day}-{election_kind}/"
                + (slug(heading)[:70] if question else office)
                + (f"-{slug(seat)}" if seat and not question else "")
                + ("-unexpired" if item["remainder"] else "")
                + (f"-{item['scope'].lower()}" if item["scope"] else "")
            )
            while contest_path in paths:
                contest_path += "-2"
            paths.add(contest_path)
            if contest_path in foreign["contest"]:
                continue
            contest = cs.key("us.contest", contest_key)
            magnitude = item["allowed"] or 1
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question" if question else "body_seats" if body else "office",
                office_id=None if question or body else owners[(code, office)],
                body_id=owners[(code, office)] if body else None,
                district_area_id=area,
                magnitude=None if question else magnitude,
                round_number=1,
                previous_round_id=None,
                party_scope_id=parties.get(item["scope"]),
                electoral_system_id=systems["referendum"]
                if question
                else systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="counted",
                term=None if question else "remainder" if item["remainder"] else "full",
            )
            if question:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1ftext\x1fen\x1f{source}"),
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=item["name"],
                    url=EXPORT,
                    source_id=source,
                )
            name(
                contest,
                f"{day.year} {place}: {heading}"
                if question
                else f"{day.year} {place} {heading} election"
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if item["remainder"] else "")
                + (f" ({item['scope']} primary)" if item["scope"] else ""),
            )
            cs.add("slug", path=contest_path, entity_id=contest)
            if district:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=DISTRICT.format(whole="county" if owner_kind == "county" else "jurisdiction"),
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            totals = (
                item["rows"]
                .group_by("candidate", maintain_order=True)
                .agg(pl.col("votes").sum(), pl.col("party").first())
                .sort("votes", descending=True)
            )
            yes = sum(v for c, v in totals.select("candidate", "votes").iter_rows() if c.lower() == "yes")
            no = sum(v for c, v in totals.select("candidate", "votes").iter_rows() if c.lower() == "no")
            ranked = 0
            for candidate, votes, party in totals.select("candidate", "votes", "party").iter_rows():
                if question:
                    answer = candidate.title()
                    option = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "Yes" else 2,
                        ballot_label=answer,
                    )
                    if answer == "Yes" and yes > no:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=None)
                elif re.search(r"(?i)write-?\s*in", candidate):
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    ranked += 1
                    who = cs.key(f"{SOURCE}.person", f"{code}:{slug(candidate)}")
                    if who not in persons:
                        persons[who] = candidate
                        cs.add("person", id=who)
                        name(who, candidate)
                    option = cs.key("us.option", f"{contest_key}:{who}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{who}:head"),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party in INDEPENDENT,
                    )
                    if not item["primary"] and ranked <= magnitude and votes > 0:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
