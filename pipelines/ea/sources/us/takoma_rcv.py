import html
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.takoma_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "City of Takoma Park Board of Elections"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
PAST = "https://web.archive.org/web/20231216030040id_/https://takomaparkmd.gov/?p=15206"
CERTIFIED_2022 = "https://documents.takomaparkmd.gov/government/elections/2022/2022-Takoma-Park-Election-Results-r.pdf"
CERTIFIED_2024 = "https://documents.takomaparkmd.gov/government/elections/2024/2024-TP-Election-Certified-Results.pdf"
CAUCUS_2017 = (
    "https://documents.takomaparkmd.gov/government/elections/2024/Nominating%20Caucus/"
    "Nomination-Caucus-Minutes-2017-09-12.pdf"
)
CONTESTS: dict[str, tuple[str, int | None, int | None, list[dict[str, int]]]] = {
    "2012-07-17:5": (
        PAST,
        None,
        None,
        [
            {"Eric Hensal": 72, "Jarrett Smith": 84, "Melinda Ulloa": 33, "Write-in": 1},
            {"Eric Hensal": 81, "Jarrett Smith": 97},
        ],
    ),
    "2014-04-08:3": (
        PAST,
        None,
        None,
        [
            {"Jeffrey Noel-Nosbaum": 20, "Roger Schlegel": 315, "Kate Stewart": 323, "Write-in": 2},
            {"Roger Schlegel": 324, "Kate Stewart": 332},
        ],
    ),
    "2017-11-07:2": (
        PAST,
        883,
        6,
        [
            {"Cindy Dyballa": 410, "Joe Edgell": 123, "Ashleigh Nugent": 342, "Write-in": 2},
            {"Cindy Dyballa": 459, "Ashleigh Nugent": 383},
        ],
    ),
    "2022-11-08:3": (
        CERTIFIED_2022,
        None,
        None,
        [
            {"Randy Gibson": 601, "Mimi Diez": 485, "Alex Hadden": 285, "Write-in": 2},
            {"Randy Gibson": 726, "Mimi Diez": 622},
        ],
    ),
    "2022-11-08:5": (
        CERTIFIED_2022,
        None,
        None,
        [
            {"Cara Honzak": 236, "A. J. Campbell": 159, "Yared Tebabu": 92},
            {"Cara Honzak": 265, "A. J. Campbell": 187, "Write-in": 1},
        ],
    ),
    "2022-11-08:6": (
        CERTIFIED_2022,
        None,
        None,
        [
            {"Jason Small": 268, "Mike Moore": 222, "Ambroise Agosse": 100, "Raju Charles": 57, "Write-in": 5},
            {"Jason Small": 313, "Mike Moore": 294},
        ],
    ),
    "2024-11-05:3": (
        CERTIFIED_2024,
        None,
        None,
        [
            {"Roger Schlegel": 624, "Matt Bormet": 478, "Lucy Moore": 266, "Write-in": 36},
            {"Roger Schlegel": 715, "Matt Bormet": 654},
        ],
    ),
    "2024-11-05:6": (
        CERTIFIED_2024,
        None,
        None,
        [
            {"Amy Wesolek": 260, "Ambroise Agosse": 252, "Jason B. Small": 210, "Write-in": 24},
            {"Amy Wesolek": 380, "Ambroise Agosse": 334},
        ],
    ),
}
PAST_NOTE = "Official first and final rounds from the city's Results of Past Elections page. The city published no intermediate rounds; every candidate except the final two was eliminated before the final round. Each candidate's value is a running total. Exhausted counts valid ballots with no continuing choice in the final round."
PAST_2017_NOTE = "Official first and final rounds from the city's Results of Past Elections page. Candidates are listed by surname; full names are from the city clerk's minutes of the September 12, 2017 nominating caucus. Ballots cast includes 6 blank or invalid ballots. Exhausted counts valid ballots with no continuing choice in the final round."
CERTIFIED_NOTE = "Official rounds from the Board of Elections' certified results. Every candidate except the final two was eliminated together after round 1, and their ballots moved to each voter's next continuing choice. Each candidate's value is a running total. Exhausted counts valid ballots with no continuing choice in the final round."


def fetch() -> None:
    for url in {PAST, CERTIFIED_2022, CERTIFIED_2024, CAUCUS_2017}:
        raw.fetch(SOURCE, url, suffix=".htm" if url == PAST else ".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    texts: dict[str, str] = {}
    for url in {PAST, CERTIFIED_2022, CERTIFIED_2024, CAUCUS_2017}:
        path = raw.cached(SOURCE, url).path
        if url == PAST:
            content = path.read_text(encoding="utf-8", errors="replace")
            content = re.sub(r"(?i)</t[dh]>", " | ", content[content.index("Results of Past Elections") :])
            texts[url] = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content)))
        else:
            texts[url] = re.sub(
                r"\s+",
                " ",
                subprocess.run(["pdftotext", str(path), "-"], check=True, capture_output=True, text=True).stdout,
            )
    for name in ("Ashleigh Nugent", "Cindy Dyballa", "Joe Edgell"):
        if name not in texts[CAUCUS_2017]:
            raise ValueError(f"{name} is not in the 2017 caucus minutes")
    contests: dict[str, dict[str, object]] = {}
    for key, (url, cast, invalid, rounds) in CONTESTS.items():
        text = texts[url]
        for tally in rounds:
            for label, votes in tally.items():
                word = re.escape(label.split()[-1] if url == PAST and key.startswith("2017") else label)
                if label == "Write-in":
                    word = "Write-?in"
                if not re.search(rf"(?i){word}[^A-Za-z]{{0,60}}?(?<![\d,]){votes:,}(?![\d,])", text):
                    raise ValueError(f"{key}: {label} {votes} is not in {url}")
        if cast is not None and invalid is not None and cast - invalid != sum(rounds[0].values()):
            raise ValueError(f"{key}: {cast} and {invalid}")
        final = rounds[-1]
        if 2 * max(final.values()) <= sum(final.values()):
            raise ValueError(f"{key}: no majority in the final round")
        contests[key] = {"cast": cast, "invalid": invalid, "rounds": rounds}
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Takoma Park, Maryland, with its eight ranked-choice council races that went past round 1, 2012 to "
            "2024, from the city's official results",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr")]

        state = cs.polity("US-MD")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if {state, census} & cs.minted:
            raise ValueError("Maryland and the Census boundary source must already exist")
        places = raw.cached("us.alged", PLACES)
        with zipfile.ZipFile(places.path) as archive:
            parts = {item.rsplit(".", 1)[1].lower(): archive.read(item) for item in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        (shape,) = [
            json.dumps(item.__geo_interface__)
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and item is not None and record["GEOID"] == "2476650"
        ]
        geom, point = conn.execute(
            """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from (select st_multi(st_collectionextract(st_makevalid(st_transform(
                       st_setsrid(st_geomfromgeojson(%s), 4269), 4326)), 3)) as geom) shape""",
            [shape],
        ).fetchone() or (None, None)

        polity = cs.key("us.polity", "place:2476650")
        city = cs.area("us.place", "2476650")
        whole = cs.unit("us.place", "2476650")
        cs.add("polity", id=polity, kind="municipality", iso_code=None)
        name(polity, "Takoma Park")
        cs.add("polity_parent", polity_id=polity, parent_id=state)
        cs.add("slug", path="us/md/takoma-park", entity_id=polity)
        cs.add("area", id=city, scheme_code="us.place")
        cs.add("area_code", area_id=city, code="2476650", valid_from=None)
        cs.add("area_parent", area_id=city, parent_id=cs.area("us.state", "24"))
        name(city, "Takoma Park, MD")
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
            code="2476650",
            area_relation="equals",
            area_id=city,
            kind="standard",
            valid_from=None,
        )
        council = cs.key("us.body", "MD:2476650:council")
        cs.add("body", id=council, polity_id=polity, kind="council")
        name(council, "Takoma Park City Council")
        irv = cs.key("us.electoral_system", "takoma-park:irv")
        cs.add("electoral_system", id=irv, polity_id=polity, rules={"formula": "irv"}, rules_version=1)
        name(irv, "Takoma Park ranked-choice voting")

        people: dict[tuple[str, str], set[tuple[UUID, bool]]] = {}
        for text, person, local in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id,
                 exists (select from name n where n.entity_id = k.id and n.text like '%%Montgomery%%') from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               where e.polity_id = %s or e.polity_id in (select polity_id from polity_parent where parent_id = %s)""",
            [state, state],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, local))

        sources: dict[str, UUID] = {}
        titles = {
            PAST: "Results of Past Elections",
            CERTIFIED_2022: "2022 Takoma Park Municipal Elections Certification Report",
            CERTIFIED_2024: "Certified Results of the 2024 Takoma Park City Election",
            CAUCUS_2017: "Nominating Caucus minutes, September 12, 2017",
        }
        for url, title in titles.items():
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])

        done: set[object] = set()
        for key, record in contests.items():
            day = date.fromisoformat(key[:10])
            ward = key[11:]
            special = day.month != 11
            parent = cs.key("us.election", f"{day}:MD")
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
                name(parent, f"{day.year} Maryland local elections")
                cs.add("slug", path=f"us/md/{day}-local", entity_id=parent)
            election = cs.key("us.election", f"{day}:MD:2476650")
            if election not in done:
                done.add(election)
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
                name(election, f"{day.year} Takoma Park {'special' if special else 'city'} election")
                cs.add("slug", path=f"us/md/takoma-park/{day}-local", entity_id=election)
            code = f"24:2476650:council:ward-{ward}"
            district = cs.area("us.local_district", code)
            top = cs.unit("us.local_district", code)
            if code not in done:
                done.add(code)
                cs.add("area", id=district, scheme_code="us.local_district")
                cs.add("area_code", area_id=district, code=code, valid_from=None)
                cs.add("area_parent", area_id=district, parent_id=city, valid_from=None)
                name(district, f"Takoma Park City Council, Ward {ward}")
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
            contest = cs.key("us.contest", f"{day}:MD:2476650:council:{ward}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                office_id=None,
                body_id=council,
                district_area_id=district,
                magnitude=1,
                round_number=1,
                electoral_system_id=irv,
                default_result_kind="counted",
                term="remainder" if special else "full",
            )
            name(
                contest,
                f"{day.year} Takoma Park City Council {'special ' if special else ''}election, Ward {ward}",
            )
            cs.add("slug", path=f"us/md/takoma-park/{day}-local/council-ward-{ward}", entity_id=contest)
            rounds = record["rounds"]
            options: dict[str, UUID] = {}
            for label in dict.fromkeys(label for tally in rounds for label in tally):
                if label == "Write-in":
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
                else:
                    person = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]}"))
                    if person in cs.minted and person not in done:
                        done.add(person)
                        cs.add("person", id=person)
                        name(person, label)
                    people[(words[0], words[-1])] = {(person, True)}
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

            result = cs.key(f"{SOURCE}.result_set", key)
            if result not in cs.minted:
                continue
            url = CONTESTS[key][0]
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
            )
            note = CERTIFIED_NOTE if url != PAST else PAST_2017_NOTE if day.year == 2017 else PAST_NOTE
            cs.add("note", entity_id=result, kind="methodology", body_md=note)
            valid = sum(rounds[0].values())
            values = [(None, 4, 1, valid)]
            if record["cast"] is not None:
                values += [(None, 3, 0, record["cast"]), (None, 5, 0, record["invalid"])]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
                values.extend((options[label], 1, number, votes) for label, votes in tally.items())
                if number > 1:
                    values.extend((options[label], 1, number, 0) for label in rounds[number - 2] if label not in tally)
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=top,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=0,
                    value=votes,
                )
            final = rounds[-1]
            winner = max(final, key=lambda label: final[label])
            for label, option in options.items():
                for outcome in ("won", "elected") if label == winner else ("lost" if label in final else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
