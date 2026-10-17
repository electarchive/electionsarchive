import csv
import html
import io
import json
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.utahco_rcv"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Utah County Clerk/Auditor, Elections Division"
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_place_500k.zip"
RESULTS = "https://vote.utahcounty.gov/results"
RANKED_2021 = "https://web.archive.org/web/20211218025942id_/https://www.utahcounty.gov/Dept/ClerkAud/Elections/2021RankedResults.asp"
SOVC_2021 = (
    "https://web.archive.org/web/20220115113931id_/"
    "https://www.utahcounty.gov/Dept/ClerkAud/Elections/ElectRslts/2021General/21GCountywide%20SOVC.CSV"
)
TABULAR = "https://rcvis.com/vo/{}/tabular-by-candidate"
CITIES = {
    "4922370": ("Elk Ridge", "City"),
    "4928810": ("Genola", "Town"),
    "4944320": ("Lehi", "City"),
    "4958730": ("Payson", "City"),
    "4972280": ("Springville", "City"),
    "4980420": ("Vineyard", "City"),
    "4985050": ("Woodland Hills", "City"),
}
CONTESTS = {
    "payson-seat-1-updated-2019-11-19_11-18-27json": ("4958730", "Seat 1", None),
    "payson-seat-2-2019-11-19_11-18-27_summary_2json": ("4958730", "Seat 2", None),
    "payson-seat-3-2019-11-19_11-18-27_summary_3json": ("4958730", "Seat 3", None),
    "vineyard-seat-1-updated-2019-11-19_11-20-30json": ("4980420", "Seat 1", None),
    "vineyard-seat-2-2019-11-19_11-20-30_summary_2json": ("4980420", "Seat 2", None),
    "21g_er_m_u4": ("4922370", None, "Elk Ridge Mayor 1st Choice"),
    "21g_le_cc_1_u4": ("4944320", "Seat 1", "Lehi City Council 1st Choice"),
    "21g_le_cc_2_u4": ("4944320", "Seat 2", None),
    "21g_sp_cc4y_1_u4": ("4972280", "Seat 1", "Springville City Council (4 year) 1st Choice"),
    "21g_sp_cc4y_2_u4": ("4972280", "Seat 2", None),
    "21g_sp_cc2y_u4": ("4972280", "Two-Year Seat", "Springville City Council (2 year) 1st Choice"),
    "21g_vi_cc_1_u4": ("4980420", "Seat 1", "Vineyard City Council 1st Choice"),
    "21g_vi_cc_2_u4": ("4980420", "Seat 2", None),
    "21g_wh_m_u4": ("4985050", None, "Woodland Hills Mayor 1st Choice"),
    "21g_wh_cc_1_u4": ("4985050", "Seat 1", "Woodland Hills City Council 1st Choice"),
    "21g_wh_cc_2_u4": ("4985050", "Seat 2", None),
    "2023-genola-city-council-12": ("4928810", "Seat 1", None),
    "2023-genola-city-council-16": ("4928810", "Seat 2", None),
    "2023-lehi-city-council-18": ("4944320", "Seat 1", None),
    "2023-lehi-city-council-19": ("4944320", "Seat 2", None),
    "2023-lehi-city-council-20": ("4944320", "Seat 3", None),
    "2023-payson-city-council-33": ("4958730", "Seat 1", None),
    "2023-payson-city-council-34": ("4958730", "Seat 2", None),
    "2023-payson-city-council-35": ("4958730", "Seat 3", None),
    "2023-vineyard-city-council-20": ("4980420", "Seat 1", None),
    "2023-vineyard-city-council-21": ("4980420", "Seat 2", None),
}
DAYS = {2019: date(2019, 11, 5), 2021: date(2021, 11, 2), 2023: date(2023, 11, 21)}
METHOD = "Rounds from the ranked-choice results Utah County published as RCVis visualizations, linked from its election results page. Round 1 is the tally of first choices. After each round the last-place candidate was eliminated, and their ballots moved to each voter's next continuing choice. Each candidate's value is a running total; an eliminated candidate shows 0 in the next round. Exhausted counts are ballots with no continuing choice. The county did not publish blank or overvoted ballots with these rounds."
DRIFT = "The published rounds do not balance exactly; the total of candidate votes and exhausted ballots changes by up to {count} between rounds. The figures are shown as published."
CANVASS = "The county's official canvass reports {name} {votes} first choices; round 1 here shows {published}."
SEQUENTIAL = "The city filled its council seats using sequential ranked-choice tabulations from a single ballot, one seat at a time. Each seat's winner was excluded from the tabulations for the remaining seats."


def fetch() -> None:
    for url in (RESULTS, RANKED_2021):
        raw.fetch(SOURCE, url, suffix=".htm")
    raw.fetch(SOURCE, SOVC_2021, suffix=".csv")
    for key in CONTESTS:
        raw.fetch(SOURCE, TABULAR.format(key), suffix=".htm")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    listed = raw.cached(SOURCE, RESULTS).path.read_text(encoding="utf-8") + raw.cached(
        SOURCE, RANKED_2021
    ).path.read_text(encoding="utf-8", errors="replace")
    with raw.cached(SOURCE, SOVC_2021).path.open(encoding="utf-8-sig", errors="replace") as handle:
        rows = list(csv.reader(handle))
    first = {
        (contest, name): int(total)
        for contest, name, total in zip(rows[0], rows[1], rows[-1], strict=True)
        if total.isdigit()
    }
    contests = {}
    for key, (_, _, column) in CONTESTS.items():
        if key not in listed:
            raise ValueError(f"{key} is not linked from the county's results pages")
        page = raw.cached(SOURCE, TABULAR.format(key)).path.read_text(encoding="utf-8")
        title = html.unescape(re.findall(r"<title>(.*?):", page)[0])
        page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.DOTALL)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
        parts = text.split(": what changed and total votes in each round Round What changed Current total votes ")
        tails = [part.rsplit(" votes ", 1)[-1].rsplit(" ballots ", 1)[-1] for part in parts[:-1]]
        tails[0] = tails[0].rsplit(f" {title} ", 1)[-1]
        lines: dict[str, list[tuple[int, int, bool]]] = {}
        for tail, body in zip(tails, parts[1:], strict=True):
            label = tail[: len(tail) // 2]
            if tail != f"{label} {label}":
                raise ValueError(f"{key}: cannot read the name in {tail}")
            found = re.findall(
                r"Round (\d+) (.*?)(?:[\d.]+% )?([\d,]+) (?:votes|ballots)(?=$| Round \d| [A-Z'\"])", body
            )
            if [int(number) for number, _, _ in found] != list(range(1, len(found) + 1)):
                raise ValueError(f"{key}: {label} rounds are out of order")
            lines[label] = [
                (int(number), int(votes.replace(",", "")), "Eliminated" in what) for number, what, votes in found
            ]
        inactive = [votes for _, votes, _ in lines.pop("Inactive Ballots", [])]
        count = max(len(line) for line in lines.values())
        inactive += [inactive[-1] if inactive else 0] * (count - len(inactive))
        rounds = []
        for number in range(count):
            tally = {}
            for label, line in lines.items():
                if number < len(line):
                    tally[label] = line[number][1]
                elif number == len(line) and line[-1][2]:
                    tally[label] = 0
                elif not line[-1][2]:
                    raise ValueError(f"{key}: {label} stops in round {len(line)} without being eliminated")
            rounds.append(tally)
        if 2 * max(rounds[-1].values()) <= sum(rounds[-1].values()):
            raise ValueError(f"{key}: no majority in the final round")
        canvass = None
        if column is not None:
            canvass = {label: first.get((column, label)) for label in rounds[0]}
        year = 2019 if "2019" in key else 2021 if key.startswith("21g") else 2023
        contests[key] = {
            "year": year,
            "title": title,
            "rounds": rounds,
            "inactive": inactive,
            "canvass": canvass,
            "totals": [sum(tally.values()) + inactive[number] for number, tally in enumerate(rounds)],
        }
    (PARSED / "official.json").write_text(json.dumps(contests))


def emit() -> Path:
    contests = json.loads((PARSED / "official.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ranked-choice rounds for Utah County's 2019, 2021, and 2023 municipal races that went past round 1, "
            "from the county's published RCV results, with seven cities new to the archive",
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
        for geoid, (city, _) in CITIES.items():
            area = cs.area("us.place", geoid)
            polities[geoid] = cs.key("us.polity", f"place:{geoid}")
            if area not in cs.minted or polities[geoid] not in cs.minted:
                raise ValueError(f"{city} already exists")
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

        sources: dict[int, UUID] = {}
        for year, url, title in (
            (2019, RESULTS, "Election results: 2019 General RCV"),
            (2021, RANKED_2021, "2021 Ranked Choice Election Results"),
            (2023, RESULTS, "Election results: 2023 General RCV"),
        ):
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), sources[year])
            for key, record in contests.items():
                if record["year"] == year:
                    cs.artifact(raw.cached(SOURCE, TABULAR.format(key)), sources[year])
        cs.artifact(raw.cached(SOURCE, SOVC_2021), sources[2021])

        people: dict[tuple[str, str, UUID], UUID] = {}
        done: set[object] = set()
        for key, record in contests.items():
            year = record["year"]
            day = DAYS[year]
            geoid, seat, _ = CONTESTS[key]
            city, kind = CITIES[geoid]
            polity = polities[geoid]
            whole = cs.area("us.place", geoid)
            top = cs.unit("us.place", geoid)
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
                name(election, f"{year} {city} local election")
                cs.add("slug", path=f"us/ut/{slug(city)}/{day}-local", entity_id=election)
            owner = cs.key(
                "us.office" if seat is None else "us.body", f"UT:{geoid}:{'mayor' if seat is None else 'council'}"
            )
            if owner not in done:
                done.add(owner)
                if seat is None:
                    cs.add("office", id=owner, polity_id=polity, kind="mayor")
                    name(owner, f"Mayor of {city}")
                else:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                    name(owner, f"{city} {kind} Council")
            contest = cs.key("us.contest", f"{day}:UT:{geoid}:{'mayor' if seat is None else slug(seat)}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if seat is None else "body_seats",
                office_id=owner if seat is None else None,
                body_id=None if seat is None else owner,
                district_area_id=whole,
                magnitude=1,
                round_number=1,
                electoral_system_id=systems[geoid],
                default_result_kind="counted",
                term="full",
            )
            name(
                contest,
                f"{year} {city} mayoral election" if seat is None else f"{year} {city} {kind} Council election, {seat}",
            )
            cs.add(
                "slug",
                path=f"us/ut/{slug(city)}/{day}-local/{'mayor' if seat is None else 'council-' + slug(seat)}",
                entity_id=contest,
            )
            rounds = record["rounds"]
            options: dict[str, UUID] = {}
            options_text: dict[str, str] = {}
            for label in rounds[0]:
                if label == "Undeclared Write-ins":
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
                text = re.sub(r"\bMc([a-z])", lambda found: "Mc" + found[1].upper(), label.title())
                options_text[label] = text
                person = people.get((words[0], words[-1], polity))
                if person is None:
                    person = cs.key(f"{SOURCE}.person", slug(f"{words[0]} {words[-1]} {city}"))
                    people[words[0], words[-1], polity] = person
                    cs.add("person", id=person)
                    name(person, text)
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
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            if seat is not None and seat.startswith("Seat"):
                cs.add("note", entity_id=result, kind="methodology", body_md=SEQUENTIAL)
            if max(record["totals"]) != min(record["totals"]):
                drift = max(record["totals"]) - min(record["totals"])
                cs.add("note", entity_id=result, kind="caveat", body_md=DRIFT.format(count=drift))
            for label, votes in (record["canvass"] or {}).items():
                if votes is not None and votes != rounds[0][label]:
                    body = CANVASS.format(
                        name=options_text[label], votes=f"{votes:,}", published=f"{rounds[0][label]:,}"
                    )
                    cs.add("note", entity_id=result, kind="caveat", body_md=body)
            values = [(None, 4, 1, sum(rounds[0].values()))]
            for number, tally in enumerate(rounds, 1):
                values.append((None, 21, number, record["inactive"][number - 1]))
                values.extend((options[label], 1, number, votes) for label, votes in tally.items())
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
                for outcome in (
                    ("won", "elected") if label == winner else ("lost" if final.get(label) else "eliminated",)
                ):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
        return cs.write()
