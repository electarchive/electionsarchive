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

SOURCE = "us.slco_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Salt Lake County Clerk, Elections Division"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
BASE = "https://www.saltlakecounty.gov/globalassets/1-site-files/clerk/elections/election-results/"
SUMMARY_2021 = BASE + "2021/2021-general-election-ranked-choice-summary-report.pdf"
RECOUNT_2021 = BASE + "2021/2021-general-election-sandy-recount-results.pdf"
DATA_2025 = BASE + "2025-municipal-general-election-data.pdf"
TITLES = {
    SUMMARY_2021: "2021 Municipal General Election Official Final Ranked Choice Results",
    RECOUNT_2021: "2021 Municipal General Election Sandy City Mayor Recount Results",
    DATA_2025: "2025 Municipal General Election Data",
}
CITIES = {
    "4906810": "Bluffdale",
    "4916270": "Cottonwood Heights",
    "4920120": "Draper",
    "4949710": "Midvale",
    "4950150": "Millcreek",
    "4967000": "Salt Lake City",
    "4967440": "Sandy",
}
CONTESTS = {
    (2021, "CITY OF BLUFFDALE COUNCIL AT-LARGE - SEAT #1"): ("4906810", "At-Large Seat 1"),
    (2021, "CITY OF BLUFFDALE COUNCIL AT-LARGE - SEAT #2"): ("4906810", "At-Large Seat 2"),
    (2021, "COTTONWOOD HEIGHTS MAYOR"): ("4916270", None),
    (2021, "COTTONWOOD HEIGHTS COUNCIL DISTRICT 3"): ("4916270", "District 3"),
    (2021, "COTTONWOOD HEIGHTS COUNCIL DISTRICT 4"): ("4916270", "District 4"),
    (2021, "DRAPER CITY COUNCIL AT-LARGE - SEAT #1"): ("4920120", "At-Large Seat 1"),
    (2021, "DRAPER CITY COUNCIL AT-LARGE - SEAT #2"): ("4920120", "At-Large Seat 2"),
    (2021, "MIDVALE CITY MAYOR"): ("4949710", None),
    (2021, "MILLCREEK COUNCIL DISTRICT 2"): ("4950150", "District 2"),
    (2021, "MILLCREEK COUNCIL DISTRICT 4"): ("4950150", "District 4"),
    (2021, "SALT LAKE CITY COUNCIL DISTRICT 1"): ("4967000", "District 1"),
    (2021, "SALT LAKE CITY COUNCIL DISTRICT 2"): ("4967000", "District 2"),
    (2021, "SALT LAKE CITY COUNCIL DISTRICT 3"): ("4967000", "District 3"),
    (2021, "SALT LAKE CITY COUNCIL DISTRICT 5"): ("4967000", "District 5"),
    (2021, "SALT LAKE CITY COUNCIL DISTRICT 7"): ("4967000", "District 7"),
    (2021, "SANDY CITY MAYOR"): ("4967440", None),
    (2021, "SANDY CITY COUNCIL AT-LARGE"): ("4967440", "At-Large"),
    (2021, "SANDY CITY COUNCIL DISTRICT 1"): ("4967440", "District 1"),
    (2025, "SALT LAKE CITY COUNCIL DISTRICT 3"): ("4967000", "District 3"),
}
DAYS = {2021: date(2021, 11, 2), 2025: date(2025, 11, 4)}
METHOD = "Official rounds from Salt Lake County's ranked-choice report. Round 1 is the tally of first choices. Each round, the last-place candidate was eliminated, and their ballots moved to each voter's next continuing choice. Each candidate's value is a running total; an eliminated candidate shows 0 in the next round. Invalid counts are blank ballots and ballots overvoted at the first choice. Exhausted counts are valid ballots with no continuing choice, including ballots overvoted at a later choice."
SEQUENTIAL = "The city filled its two at-large council seats from one ballot with sequential ranked-choice tabulations. Seat 1 was tabulated first. For seat 2, the seat 1 winner was excluded and the same ballots were tabulated again."
RECOUNT = "Salt Lake County recounted this race. The recount report, tabulated on November 17, 2021 and marked unofficial, gives the same figures in every round."
TOTALS = ("Continuing Ballots Total", "Blanks", "Exhausted", "Overvotes", "Non Transferable Total")


def fetch() -> None:
    for url in TITLES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    found: dict[tuple[int, str, str], dict[str, object]] = {}
    for url in TITLES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for block in re.split(r"\n(?=[A-Z][A-Z0-9 #.'-]+\n(?:Official|Unofficial) results\n)", text)[1:]:
            title = block.split("\n", 1)[0].strip()
            body = block[block.index("Candidate") : block.index("Threshold")]
            names: list[str] = []
            rows: dict[str, list[int]] = {}
            pending: list[int] | None = None
            prefix = ""
            for line in body.splitlines():
                match = re.fullmatch(r"\s*([A-Za-z].*?)?\s{2,}((?:-?[\d,]+(?:\.\d+%)?\s*)+)", line)
                if match and not line.strip().startswith(("Round", "Votes")):
                    numbers = [int(item.replace(",", "")) for item in match[2].split() if not item.endswith("%")]
                    if match[1] is None:
                        pending = numbers
                        continue
                    rows[match[1].strip()] = numbers
                    if match[1].strip() not in TOTALS:
                        names.append(match[1].strip())
                elif line.strip() and pending is None and re.fullmatch(r"[A-Z][A-Z .'\"-]*-", line.strip()):
                    prefix = line.strip()
                elif line.strip() and pending is not None:
                    rows[prefix + line.strip()] = pending
                    names.append(prefix + line.strip())
                    pending, prefix = None, ""
            continuing = rows["Continuing Ballots Total"]
            dropped = rows["Non Transferable Total"]
            rounds = [{name: rows[name][2 * number] for name in names} for number in range(len(continuing))]
            ballots = {left + right for left, right in zip(continuing, dropped, strict=True)}
            if len(ballots) != 1:
                raise ValueError(f"{title}: ballots change between rounds")
            for number, tally in enumerate(rounds):
                if sum(tally.values()) != continuing[number]:
                    raise ValueError(f"{title}: round {number + 1} does not sum")
            if 2 * max(rounds[-1].values()) <= continuing[-1]:
                raise ValueError(f"{title}: no majority in the final round")
            year = 2025 if url == DATA_2025 else 2021
            found[year, title, url] = {
                "ballots": ballots.pop(),
                "invalid": rows["Blanks"][0] + rows["Overvotes"][0],
                "rounds": rounds,
            }
    if found[2021, "SANDY CITY MAYOR", RECOUNT_2021] != found[2021, "SANDY CITY MAYOR", SUMMARY_2021]:
        raise ValueError("the Sandy recount differs from the official summary")
    contests = {}
    for (year, title, url), record in found.items():
        if (year, title) in CONTESTS and url != RECOUNT_2021:
            contests[f"{year}:{title}"] = record | {"url": url}
    if len(contests) != len(CONTESTS):
        raise ValueError(f"found {len(contests)} of {len(CONTESTS)} contests")
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ranked-choice rounds for Salt Lake County's 2021 and 2025 municipal contests that went past round 1, "
            "from the county's official reports, with every 2021 Salt Lake City and Sandy contest restated as IRV",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w-]+", plain) if word not in ("jr", "sr")]

        state = cs.polity("US-UT")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if {state, census} & cs.minted:
            raise ValueError("Utah and the Census boundary source must already exist")
        places = raw.cached("us.alged", PLACES)
        with zipfile.ZipFile(places.path) as archive:
            parts = {item.rsplit(".", 1)[1].lower(): archive.read(item) for item in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        shapes = {
            record["GEOID"]: json.dumps(item.__geo_interface__)
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and item is not None and record["GEOID"] in CITIES
        }
        cs.artifact(places, census)

        polities: dict[str, UUID] = {}
        systems: dict[str, UUID] = {}
        for geoid, city in CITIES.items():
            area = cs.area("us.place", geoid)
            if area not in cs.minted:
                ((polities[geoid],),) = conn.execute(
                    "select polity_id from polity_territory where area_id = %s", [area]
                ).fetchall()
            else:
                polities[geoid] = cs.key("us.polity", f"place:{geoid}")
                geom, point = conn.execute(
                    """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                       from (select st_multi(st_collectionextract(st_makevalid(st_transform(
                               st_setsrid(st_geomfromgeojson(%s), 4269), 4326)), 3)) as geom) shape""",
                    [shapes[geoid]],
                ).fetchone() or (None, None)
                cs.add("polity", id=polities[geoid], kind="municipality", iso_code=None)
                name(polities[geoid], city)
                cs.add("polity_parent", polity_id=polities[geoid], parent_id=state)
                cs.add("slug", path=f"us/ut/{slug(city)}", entity_id=polities[geoid])
                cs.add("area", id=area, scheme_code="us.place")
                cs.add("area_code", area_id=area, code=geoid, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "49"))
                name(area, f"{city}, UT")
                cs.add("polity_territory", polity_id=polities[geoid], area_id=area)
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=None,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=census,
                )
                cs.add(
                    "reporting_unit",
                    id=cs.unit("us.place", geoid),
                    polity_id=polities[geoid],
                    scheme_code="us.place",
                    code=geoid,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            systems[geoid] = cs.key("us.electoral_system", f"{slug(city)}:irv")
            cs.add(
                "electoral_system",
                id=systems[geoid],
                polity_id=polities[geoid],
                rules={"formula": "irv"},
                rules_version=1,
            )
            name(systems[geoid], f"{city} ranked-choice voting")

        columns = (
            "election_id",
            "kind",
            "office_id",
            "body_id",
            "district_area_id",
            "seat_id",
            "boundary_set_id",
            "magnitude",
            "party_scope_id",
            "round_number",
            "previous_round_id",
            "allocation_parent_id",
            "rules_override",
            "default_result_kind",
            "term",
        )
        for geoid in ("4967000", "4967440"):
            for (contest,) in conn.execute(
                """select k.id from contest k join election e on e.id = k.election_id
                   where e.polity_id = %s and e.date_from = '2021-11-02' and k.kind in ('office', 'body_seats')""",
                [polities[geoid]],
            ).fetchall():
                row = conn.execute(f"select {', '.join(columns)} from contest where id = %s", [contest]).fetchone()
                cs.add(
                    "contest",
                    id=contest,
                    **dict(zip(columns, row or (), strict=True)),
                    electoral_system_id=systems[geoid],
                )

        people: dict[tuple[str, str], set[tuple[UUID, UUID]]] = {}
        for text, person, polity in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id, e.polity_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               where e.polity_id in (select polity_id from polity_parent where parent_id = %s)""",
            [state],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add((person, polity))

        sources: dict[str, UUID] = {}
        for url, title in TITLES.items():
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
            year = int(key[:4])
            title = key[5:]
            day = DAYS[year]
            geoid, seat = CONTESTS[year, title]
            city = CITIES[geoid]
            polity = polities[geoid]
            whole = cs.area("us.place", geoid)
            parent = cs.key("us.election", f"{day}:UT")
            if parent in cs.minted and parent not in done:
                done.add(parent)
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
                name(parent, f"{year} Utah local elections")
                cs.add("slug", path=f"us/ut/{day}-local", entity_id=parent)
            election = cs.key("us.election", f"{day}:UT:{geoid}")
            if election in cs.minted and election not in done:
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
                name(election, f"{year} {city} local election")
                cs.add("slug", path=f"us/ut/{slug(city)}/{day}-local", entity_id=election)
            owner = cs.key(
                "us.office" if seat is None else "us.body", f"UT:{geoid}:{'mayor' if seat is None else 'council'}"
            )
            if owner in cs.minted and owner not in done:
                done.add(owner)
                if seat is None:
                    cs.add("office", id=owner, polity_id=polity, kind="mayor")
                    name(owner, f"Mayor of {city}")
                else:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                    name(owner, re.sub(r"\bCity City\b", "City", f"{city} City Council"))
            district, top = whole, cs.unit("us.place", geoid)
            if seat is not None and seat.startswith("District"):
                code = f"49:{geoid}:council:{slug(seat)}"
                district, top = cs.area("us.local_district", code), cs.unit("us.local_district", code)
                if district in cs.minted and code not in done:
                    done.add(code)
                    cs.add("area", id=district, scheme_code="us.local_district")
                    cs.add("area_code", area_id=district, code=code, valid_from=None)
                    cs.add("area_parent", area_id=district, parent_id=whole, valid_from=None)
                    name(district, re.sub(r"\bCity City\b", "City", f"{city} City Council, {seat}"))
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
            heading = re.sub(
                r"\bCity City\b",
                "City",
                f"{year} {city} mayoral election" if seat is None else f"{year} {city} City Council election, {seat}",
            )
            existing = conn.execute(
                "select k.id from contest k join name n on n.entity_id = k.id where n.text = %s and k.election_id = %s",
                [heading, election],
            ).fetchall()
            if len(existing) > 1:
                raise ValueError(f"{key}: {len(existing)} contests named {heading}")
            if existing:
                ((contest,),) = existing
                for values in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where contest_id = %s and is_default
                         and changeset_id not in (select id from changeset where pipeline = %s)""",
                    [contest, SOURCE],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
                                    "id",
                                    "election_id",
                                    "contest_id",
                                    "stage",
                                    "kind",
                                    "status",
                                    "source_id",
                                    "as_of",
                                    "completeness",
                                    "supersedes_id",
                                ),
                                values,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
            else:
                contest = cs.key("us.contest", f"{day}:UT:{geoid}:{'mayor' if seat is None else slug(seat)}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office" if seat is None else "body_seats",
                    office_id=owner if seat is None else None,
                    body_id=None if seat is None else owner,
                    district_area_id=district,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=systems[geoid],
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, heading)
                cs.add(
                    "slug",
                    path=f"us/ut/{slug(city)}/{day}-local/{'mayor' if seat is None else 'council-' + slug(seat)}",
                    entity_id=contest,
                )
            current = {
                tuple(spell(text)[:1] + spell(text)[-1:]): option
                for option, text in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
            }
            rounds = [{label: votes for label, votes in tally.items()} for tally in record["rounds"]]
            excluded = [label for label, votes in rounds[0].items() if votes == 0]
            options: dict[str, UUID] = {}
            for label in rounds[0]:
                if label in excluded:
                    continue
                words = spell(label)
                if (words[0], words[-1]) in current:
                    options[label] = current[words[0], words[-1]]
                    continue
                text = re.sub(r"\bMc([a-z])", lambda found: "Mc" + found[1].upper(), label.title())
                text = text.replace("Dekeyzer", "DeKeyzer")
                known = people.get((words[0], words[-1]), set())
                matches = {person for person, place in known if place == polity}
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]} {city}"))
                    if person in cs.minted and person not in done:
                        done.add(person)
                        cs.add("person", id=person)
                        name(person, text)
                    people[(words[0], words[-1])] = {(person, polity)}
                options[label] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[label], contest_id=contest, kind="candidate", ballot_label=text)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[label],
                    role="head",
                    ballot_name=text,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if len(set(options.values())) != len(options):
                raise ValueError(f"{key}: two candidates share an option")

            result = cs.key(f"{SOURCE}.result_set", key)
            if result not in cs.minted:
                continue
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[record["url"]],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            if excluded:
                cs.add("note", entity_id=result, kind="methodology", body_md=SEQUENTIAL)
            if title == "SANDY CITY MAYOR":
                cs.add("note", entity_id=result, kind="caveat", body_md=RECOUNT)
            valid = sum(rounds[0].values())
            values = [(None, 3, 0, record["ballots"]), (None, 5, 0, record["invalid"]), (None, 4, 1, valid)]
            if record["ballots"] - record["invalid"] != valid:
                raise ValueError(f"{key}: ballots, invalid, and valid disagree")
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, valid - sum(tally.values())))
                values.extend(
                    (options[label], 1, number, votes)
                    for label, votes in tally.items()
                    if label in options and not any(earlier[label] == 0 for earlier in rounds[: number - 1])
                )
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
                for outcome in ("won", "elected") if label == winner else ("lost" if final[label] else "eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
