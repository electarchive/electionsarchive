import html
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.burlington_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Burlington Clerk/Treasurer's Office"
WAYBACK = "https://web.archive.org/web/{}id_/http://www.burlingtonvotes.org/{}"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
CONTESTS = {
    "2006-03-07:mayor": [
        WAYBACK.format("20061011052557", "20060307/2006%20Burlington%20Mayor%20Round.htm"),
        WAYBACK.format("20081121065733", "20060307/2006%20Burlington%20Mayor%20Round1.htm"),
        WAYBACK.format("20081121061507", "20060307/2006%20Burlington%20Mayor%20Round2.htm"),
        WAYBACK.format("20081121063115", "20060307/2006%20Burlington%20Mayor%20Round3.htm"),
    ],
    "2009-03-03:mayor": [
        WAYBACK.format("20091109022104", "20090303/2009%20Burlington%20Mayor%20Round.htm"),
        WAYBACK.format("20091109022120", "20090303/2009%20Burlington%20Mayor%20Round1.htm"),
        WAYBACK.format("20091109013806", "20090303/2009%20Burlington%20Mayor%20Round2.htm"),
        WAYBACK.format("20091109013814", "20090303/2009%20Burlington%20Mayor%20Round3.htm"),
        WAYBACK.format("20091109054049", "20090303/2009%20Burlington%20Mayor%20Round4.htm"),
    ],
    "2022-12-06:council:east": ["https://www.burlingtonvt.gov/DocumentCenter/View/420"],
}
WARDS = WAYBACK.format("20110725112701", "20090303/2009BurlingtonMayorIRVbyward.pdf")
CHOICEPLUS = "Official instant runoff count from the city's ChoicePlus Pro round-by-round report. Round 1 is the tally of first choices. Candidates whose defeat was mathematically certain were eliminated together, and their ballots moved to each voter's next continuing choice. Each candidate's value is a running total; an eliminated candidate shows 0 in the round that moves their ballots. Exhausted counts valid ballots with no continuing choice. The report's last round, which only declares the runner-up defeated, is excluded."
RECOUNT = "Kurt Wright requested a recount and halted it after Wards 1 through 3 and part of Ward 6 were recounted. The city marked this partial recount unofficial and uncertified; its figures are not included here."
BY_WARD = "Official count broken down by the seven wards of the time, from the city's ward-level IRV table. Ballots cast includes blank ballots, which are counted as invalid. Exhausted counts valid ballots with no continuing choice in that round. The wards sum to the citywide count."
DOMINION = "The city's RCV detailed report from the Dominion tabulator, marked unofficial, run on election night. The tabulator eliminated the last-place candidate until two remained, even though Maea Brandt had a majority of first choices. The Clerk/Treasurer's official declaration, signed December 7, 2022, names Brandt elected with 55.39 percent, her round 1 share. Each candidate's value is a running total; an eliminated candidate shows 0 in the next round. Exhausted counts blank ballots and ballots with no continuing choice."


def fetch() -> None:
    for pages in CONTESTS.values():
        for page in pages:
            raw.fetch(SOURCE, page, suffix=".pdf" if "DocumentCenter" in page else ".htm")
    raw.fetch(SOURCE, WARDS, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests: dict[str, dict[str, object]] = {}
    for key, pages in CONTESTS.items():
        if "DocumentCenter" in pages[0]:
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, pages[0]).path), "-"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            rounds: list[dict[str, int]] = []
            ballots = None
            for block in re.split(r"\n\s*Round \d+\n", text)[1:]:
                tally = {
                    label.title(): int(votes.replace(",", ""))
                    for label, votes in re.findall(r"(?m)^([A-Z][A-Za-z .'-]+?)\s{2,}([\d,]+)\s+[\d.]+%", block)
                }
                ((continuing, stopped),) = [
                    (int(first.replace(",", "")), int(second.replace(",", "")))
                    for first, second in re.findall(
                        r"Continuing Ballots Total\s+([\d,]+).*?Non Transferable Total\s+([\d,]+)", block, re.DOTALL
                    )
                ]
                if sum(tally.values()) != continuing or ballots not in (None, continuing + stopped):
                    raise ValueError(f"{key}: round {len(rounds) + 1}")
                ballots = continuing + stopped
                rounds.append(tally)
            elected = re.findall(r"(?m)^([A-Z][A-Z .'-]+?) is elected", text)
            contests[key] = {
                "ballots": ballots,
                "rounds": rounds,
                "elected": [label.title() for label in elected],
            }
            continue

        def clean(page: str) -> str:
            content = raw.cached(SOURCE, page).path.read_bytes().decode("cp1252")
            return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content))).strip()

        start = clean(pages[0])
        ((valid, threshold, invalid, processed),) = [
            tuple(int(group.replace(",", "")) for group in groups)
            for groups in re.findall(
                r"([\d,]+) valid ballots\. Electing 1 candidate\. Winning threshold is (\d+) votes\. "
                r"There were ([\d,]+) invalid ballots\. \(([\d,]+) total ballots processed",
                start,
            )
        ]
        if valid + invalid != processed or threshold != valid // 2 + 1:
            raise ValueError(f"{key}: {valid}, {invalid}, {processed}")
        listing = raw.cached(SOURCE, pages[0]).path.read_bytes().decode("cp1252").split("CANDIDATES", 1)[1]
        names = [html.unescape(item).strip() for item in re.findall(r"<br>([^<]+)", listing.split("</td>", 1)[0])]
        rounds = []
        status: dict[str, str] = {}
        for page in pages[1:]:
            text = clean(page)
            body = text[text.index("STATUS") + len("STATUS") : text.index("TOTALS:")]
            tally = {}
            for label, total, state in re.findall(
                r"(.+?) [+-]?\d+ (\d+) (CONTINUING|ELECTED -- \w+ round|DEFEATED -- \w+ round) ?", body
            ):
                label = label.strip()
                if label.endswith("."):
                    (label,) = [name for name in names if name.startswith(label[:-1])]
                tally[label] = int(total)
                status[label] = state
            (pile,) = [int(found) for found in re.findall(r"EXHAUSTED PILE: [+-]?\d+ (\d+)", body)]
            if set(tally) != set(names) or sum(tally.values()) + pile != valid:
                raise ValueError(f"{key} {page}: {sorted(tally)} against {names}")
            if not rounds or tally != rounds[-1]:
                rounds.append(tally)
        if key == "2009-03-03:mayor":
            checked = rounds
        contests[key] = {
            "processed": processed,
            "valid": valid,
            "invalid": invalid,
            "rounds": rounds,
            "elected": [label for label in names if status[label].startswith("ELECTED")],
        }
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, WARDS).path), "-"], check=True, capture_output=True, text=True
    ).stdout
    wards: list[dict[str, dict[str, int]]] = []
    for block in re.split(r"Round \d IRV totals by ward", text)[1:]:
        rows = {
            label.strip(): [int(value.replace(",", "")) for value in values.split()]
            for label, values in re.findall(
                r"(?m)^\s*([A-Za-z][A-Za-z -]+?)\s+(?:C0\d\s+)?((?:[\d,]+\s+){7}[\d,]+)\s*$", block
            )
        }
        wards.append(
            {label: dict(zip([*map(str, range(1, 8)), "city"], values, strict=True)) for label, values in rows.items()}
        )
    for number, tally in enumerate(checked):
        for label, votes in tally.items():
            if wards[number].get(label, {}).get("city", 0) != votes:
                raise ValueError(f"2009 wards round {number + 1}: {label}")
        for label in ("Total valid", "Exhausted", "Blank", "Total ballots"):
            if sum(wards[number][label][str(ward)] for ward in range(1, 8)) != wards[number][label]["city"]:
                raise ValueError(f"2009 wards round {number + 1}: {label}")
    contests["2009-03-03:mayor"]["wards"] = wards
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Burlington, Vermont, with its 2006 and 2009 instant runoff mayoral races and the 2022 East District "
            "special council race, from the city's round-by-round reports",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        state = cs.polity("US-VT")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if {state, census} & cs.minted:
            raise ValueError("Vermont and the Census boundary source must already exist")
        places = raw.cached("us.alged", PLACES)
        with zipfile.ZipFile(places.path) as archive:
            parts = {item.rsplit(".", 1)[1].lower(): archive.read(item) for item in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        (shape,) = [
            json.dumps(item.__geo_interface__)
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and item is not None and record["GEOID"] == "5010675"
        ]
        geom, point = conn.execute(
            """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from (select st_multi(st_collectionextract(st_makevalid(st_transform(
                       st_setsrid(st_geomfromgeojson(%s), 4269), 4326)), 3)) as geom) shape""",
            [shape],
        ).fetchone() or (None, None)

        polity = cs.key("us.polity", "place:5010675")
        city = cs.area("us.place", "5010675")
        whole = cs.unit("us.place", "5010675")
        cs.add("polity", id=polity, kind="municipality", iso_code=None)
        name(polity, "Burlington")
        cs.add("polity_parent", polity_id=polity, parent_id=state)
        cs.add("slug", path="us/vt/burlington", entity_id=polity)
        cs.add("area", id=city, scheme_code="us.place")
        cs.add("area_code", area_id=city, code="5010675", valid_from=None)
        cs.add("area_parent", area_id=city, parent_id=cs.area("us.state", "50"))
        name(city, "Burlington, VT")
        cs.add("polity_territory", polity_id=polity, area_id=city)
        cs.artifact(places, census)
        cs.add(
            "area_geometry",
            area_id=city,
            valid_from=None,
            geom=geom,
            label_point=point,
            quality="official",
            source_id=census,
        )
        cs.add(
            "reporting_unit",
            id=whole,
            polity_id=polity,
            scheme_code="us.place",
            code="5010675",
            area_relation="equals",
            area_id=city,
            kind="standard",
            valid_from=None,
        )
        mayor = cs.key("us.office", "VT:5010675:mayor")
        council = cs.key("us.body", "VT:5010675:council")
        cs.add("office", id=mayor, polity_id=polity, kind="mayor")
        name(mayor, "Mayor of Burlington")
        cs.add("body", id=council, polity_id=polity, kind="council")
        name(council, "Burlington City Council")
        irv = cs.key("us.electoral_system", "burlington:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Burlington ranked-choice voting")

        people: dict[tuple[str, str], set[tuple[UUID, bool]]] = {}
        for text, person, local in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id,
                 exists (select from name n where n.entity_id = k.id and n.text like '%%Chittenden%%') from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               where e.polity_id = %s or e.polity_id in (select polity_id from polity_parent where parent_id = %s)""",
            [state, state],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, local))

        for key, record in contests.items():
            day = date.fromisoformat(key[:10])
            kind = key[11:].split(":")[0]
            parent = cs.key("us.election", f"{day}:VT")
            if parent in cs.minted:
                cs.add(
                    "election",
                    id=parent,
                    polity_id=state,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(parent, f"{day.year} Vermont local elections")
                cs.add("slug", path=f"us/vt/{day}-local", entity_id=parent)
            election = cs.key("us.election", f"{day}:VT:5010675")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=parent,
                kind="local",
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            name(election, f"{day.year} Burlington local election")
            cs.add("slug", path=f"us/vt/burlington/{day}-local", entity_id=election)
            district, top = city, whole
            if kind == "council":
                code = "50:5010675:council:east-district"
                district = cs.area("us.local_district", code)
                top = cs.unit("us.local_district", code)
                cs.add("area", id=district, scheme_code="us.local_district")
                cs.add("area_code", area_id=district, code=code, valid_from=None)
                cs.add("area_parent", area_id=district, parent_id=city, valid_from=None)
                name(district, "Burlington City Council, East District")
                cs.add(
                    "reporting_unit",
                    id=top,
                    polity_id=polity,
                    scheme_code="us.local_district",
                    code=code,
                    area_relation="equals",
                    area_id=district,
                    kind="standard",
                )
            contest = cs.key("us.contest", f"{day}:VT:5010675:{key[11:]}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if kind == "mayor" else "body_seats",
                office_id=mayor if kind == "mayor" else None,
                body_id=None if kind == "mayor" else council,
                district_area_id=district,
                magnitude=1,
                round_number=1,
                electoral_system_id=irv,
                default_result_kind="counted",
                term="full" if kind == "mayor" else "remainder",
            )
            name(
                contest,
                f"{day.year} Burlington mayoral election"
                if kind == "mayor"
                else f"{day.year} Burlington City Council special election, East District",
            )
            cs.add(
                "slug",
                path=f"us/vt/burlington/{day}-local/" + ("mayor" if kind == "mayor" else "council-east-district"),
                entity_id=contest,
            )
            rounds = record["rounds"]
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                if label.casefold().startswith("write-in"):
                    options[label] = cs.key("us.option", f"{contest}:write_in:scattered")
                    cs.add(
                        "contest_option",
                        id=options[label],
                        contest_id=contest,
                        kind="write_in",
                        ballot_label="Write-in",
                    )
                    continue
                words = spell(label)
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, _ in known}
                local = {person for person, here in known if here}
                if len(local) == 1:
                    matches = local
                if len(matches) == 1:
                    (person,) = matches
                    for (own,) in conn.execute(
                        "select entity_id from external_id where scheme = %s and value = %s",
                        [f"{SOURCE}.person", slug(label)],
                    ).fetchall():
                        if cs.survivors.get(own, own) != person:
                            cs.merge(own, person)
                else:
                    person = cs.key(f"{SOURCE}.person", slug(label))
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, label)
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")

            pages = CONTESTS[key]
            title = f"{day:%B} {day.day}, {day.year} Burlington " + (
                "mayoral election, ChoicePlus Pro round reports"
                if kind == "mayor"
                else "East District special election, RCV detailed report"
            )
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=pages[0],
                license="Public record",
            )
            for page in pages:
                cs.artifact(raw.cached(SOURCE, page), source)

            def running(
                tally_rounds: list[dict[str, int]], unit: UUID, options: dict[str, UUID]
            ) -> list[tuple[UUID, UUID | None, int, int, int]]:
                return [
                    (unit, options[label], 1, number, votes)
                    for number, tally in enumerate(tally_rounds, 1)
                    for label, votes in tally.items()
                    if not any(earlier.get(label) == 0 for earlier in tally_rounds[: number - 1])
                ]

            if kind == "mayor":
                values = [
                    (whole, None, 3, 0, record["processed"]),
                    (whole, None, 5, 0, record["invalid"]),
                    (whole, None, 4, 1, record["valid"]),
                    *(
                        (whole, None, 21, number, record["valid"] - sum(tally.values()))
                        for number, tally in enumerate(rounds, 1)
                    ),
                    *running(rounds, whole, options),
                ]
                notes = [("methodology", CHOICEPLUS)] + ([("caveat", RECOUNT)] if day.year == 2009 else [])
                stage = "certified"
            else:
                values = [
                    (top, None, 3, 0, record["ballots"]),
                    (top, None, 4, 1, sum(rounds[0].values())),
                    *(
                        (top, None, 21, number, record["ballots"] - sum(tally.values()))
                        for number, tally in enumerate(rounds, 1)
                    ),
                    *running(rounds, top, options),
                ]
                notes = [("methodology", DOMINION)]
                stage = "preliminary"
            sets = [(cs.key(f"{SOURCE}.result_set", key), True, source, stage, notes, values)]

            if "wards" in record:
                title = f"{day:%B} {day.day}, {day.year} Burlington mayoral election, IRV results by ward"
                counted = cs.source(PUBLISHER, title)
                cs.add(
                    "source",
                    id=counted,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=WARDS,
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, WARDS), counted)
                values = []
                for ward in map(str, range(1, 8)):
                    code = f"{day}:VT:5010675:ward-{ward}"
                    area = cs.area("us.local_district", code)
                    unit = cs.unit("us.local_district", code)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=city, valid_from=None)
                    name(area, f"Burlington Ward {ward} ({day.year})")
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                    tables = record["wards"]
                    cast, blank = tables[0]["Total ballots"][ward], tables[0]["Blank"][ward]
                    values.extend(
                        [(unit, None, 3, 0, cast), (unit, None, 5, 0, blank), (unit, None, 4, 1, cast - blank)]
                    )
                    values.extend(
                        (unit, None, 21, number, table["Exhausted"][ward]) for number, table in enumerate(tables, 1)
                    )
                    values.extend(
                        running(
                            [{label: tables[n][label][ward] for label in rounds[n]} for n in range(len(rounds))],
                            unit,
                            options,
                        )
                    )
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:wards"),
                        False,
                        counted,
                        "certified",
                        [("methodology", BY_WARD)],
                        values,
                    )
                )

            for result, default, origin, level, remarks, rows in sets:
                if result not in cs.minted:
                    continue
                cs.add(
                    "result_set",
                    id=result,
                    election_id=election,
                    contest_id=contest,
                    stage=level,
                    kind="counted",
                    status="active",
                    source_id=origin,
                    is_default=default,
                    completeness=1,
                )
                for remark, body in remarks:
                    cs.add("note", entity_id=result, kind=remark, body_md=body)
                for unit, option, measure, number, votes in rows:
                    cs.add(
                        "result_value",
                        result_set_id=result,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
                if len(record["elected"]) != 1:
                    raise ValueError(f"{key}: {record['elected']}")
                final = rounds[-1]
                for label, option in options.items():
                    for outcome in (
                        ("won", "elected")
                        if label == record["elected"][0]
                        else ("lost" if final[label] else "eliminated",)
                    ):
                        cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
