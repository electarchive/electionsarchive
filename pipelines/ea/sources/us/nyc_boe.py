import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_boe"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Board of Elections in the City of New York"
RESULTS = "https://www.vote.nyc/sites/default/files/pdf/election_results"
PRIMARIES = {
    date(2021, 6, 22): f"{RESULTS}/2021/20210622Primary%20Election/rcv",
    date(2025, 6, 24): f"{RESULTS}/2025/20250624Primary%20Election/rcv",
}
PAGES = {
    date(2021, 6, 22): [
        "024306",
        "024307",
        "024309",
        "024310",
        "024311",
        "024312",
        "024314",
        "024318",
        "024319",
        "024320",
        "024321",
        "024323",
        "024324",
        "024326",
        "024327",
        "024328",
        "024329",
        "024330",
        "024332",
        "024335",
        "024336",
        "024337",
        "024338",
        "024339",
        "024340",
        "024341",
        "024346",
        "024348",
        "024349",
        "024350",
        "024351",
        "024352",
        "024353",
        "024355",
        "024357",
        "024358",
        "024361",
        "024370",
        "024375",
        "024379",
        "024389",
        "024394",
        "024399",
        "024406",
        "024408",
        "024410",
        "024414",
        "024415",
        "024424",
        "024425",
        "024458",
        "024462",
        "024463",
        "024470",
        "024481",
        "024496",
        "024500",
        "024524",
        "024546",
        "024550",
        "024554",
        "24347",
        "24367",
    ],
    date(2025, 6, 24): [
        "026913",
        "026914",
        "026915",
        "026916",
        "026918",
        "026919",
        "026921",
        "026922",
        "026924",
        "026925",
        "026927",
        "026928",
        "026929",
        "026932",
        "026933",
        "026935",
        "026936",
        "026937",
        "026938",
        "026939",
        "026942",
        "026943",
        "026947",
        "026952",
        "026955",
        "026956",
        "026957",
        "026988",
        "026989",
        "026999",
        "027007",
        "027025",
        "027058",
        "027075",
        "027078",
        "027101",
        "027131",
    ],
}
COUNTIES = ("36005", "36047", "36061", "36081", "36085")
PARTIES = {
    "DEM": ("democratic-party", "Democratic"),
    "REP": ("republican-party", "Republican"),
    "CON": ("NY:conservative-party", "Conservative"),
}
OFFICES = {
    "Mayor": "mayor",
    "Public Advocate": "public-advocate",
    "City Comptroller": "comptroller",
    "Comptroller": "comptroller",
    "Borough President": "borough-president",
    "Council Member": "council",
}
BOROUGHS = {
    "New York": ("36061", "Manhattan"),
    "Bronx": ("36005", "Bronx"),
    "Kings": ("36047", "Brooklyn"),
    "Queens": ("36081", "Queens"),
    "Richmond": ("36085", "Staten Island"),
}
WRITE_INS = "Write-ins"
INACTIVE = "Inactive ballots"
PERSONS = {
    "Andrew Yang": ("fec.candidate", "P00006486"),
    "Andrew M. Cuomo": ("wikidata", "Q11673"),
    "Zohran Kwame Mamdani": ("us.klarner.person", "400070"),
    "Jessica Ramos": ("us.klarner.person", "387007"),
    "Zellnor Myrie": ("us.klarner.person", "387035"),
    "Scott M. Stringer": ("us.klarner.person", "176753"),
    "Adrienne E. Adams": ("us.klarner.person", "370290"),
    "Eric L. Adams": ("us.klarner.person", "277292"),
    "Brad Lander": ("us.alged.person", "34778"),
    "Kathryn A. Garcia": ("us.alged.person", "35586"),
    "Maya D. Wiley": ("us.alged.person", "35599"),
}


def url(day: date, page: str) -> str:
    return f"{PRIMARIES[day]}/{page}_1.html"


def fetch() -> None:
    for day, pages in PAGES.items():
        for page in pages:
            raw.fetch(SOURCE, url(day, page), suffix=".html")


def parse() -> None:
    frames = []
    for day, pages in PAGES.items():
        for page in pages:
            document = raw.cached(SOURCE, url(day, page)).path.read_text(encoding="utf-8")
            title = re.search(r"Contest:</td>\s*<td>\s*([^<]+?)\s*<", document)
            table = [
                [
                    html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
                ]
                for row in re.findall(r"<tr[^>]*>(.*?)</tr>", document, re.DOTALL)
            ]
            header = next(index for index, row in enumerate(table) if row and row[0] == "Votes in this round")
            rounds = table[header].count("# votes")
            counts: list[tuple[str, int, int]] = []
            for label, *cells in table[header + 1 :]:
                if label.startswith("*"):
                    break
                for number in range(rounds):
                    cell = cells[3 * number].replace(",", "") if 3 * number < len(cells) else ""
                    if cell.isdigit():
                        counts.append((label, number + 1, int(cell)))
            frame = pl.DataFrame(counts, schema=["label", "round", "votes"], orient="row")
            totals = frame.group_by("round").agg(pl.col("votes").sum()).sort("round")["votes"].to_list()
            final = frame.filter((pl.col("round") == rounds) & (pl.col("label") != INACTIVE)).sort(
                "votes", descending=True
            )
            winner = re.search(r"([^<>\n]+?) won the .+? Primary with\s+([\d,]+)", document)
            if (
                title is None
                or len(set(totals)) != 1
                or winner is None
                or final.row(0) != (winner[1].strip(), rounds, int(winner[2].replace(",", "")))
            ):
                raise ValueError(f"{day} {page}: rounds do not conserve ballots ({totals}) or the winner differs")
            office_match = re.fullmatch(rf"(\w+) ({'|'.join(OFFICES)}) (.+)", title[1])
            if office_match is None:
                raise ValueError(f"unknown contest {title[1]}")
            party_code, office, where = office_match[1], OFFICES[office_match[2]], office_match[3]
            district = (
                ""
                if where == "Citywide"
                else slug(BOROUGHS[where][1])
                if office == "borough-president"
                else str(int(re.fullmatch(r"(\d+)\w\w Council District", where)[1]))  # ty: ignore[not-subscriptable]
            )
            frames.append(
                frame.with_columns(
                    date=pl.lit(day),
                    url=pl.lit(url(day, page)),
                    page=pl.lit(page.zfill(6)),
                    contest=pl.lit(title[1]),
                    party=pl.lit(party_code),
                    office=pl.lit(office),
                    district=pl.lit(district),
                    key=pl.lit(
                        f"{day}:new-york-city:{office}{f'-{district}' if district else ''}:"
                        f"{PARTIES[party_code][1].lower()}"
                    ),
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "rounds.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "rounds.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's ranked-choice primaries, official rounds",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        city = cs.key("us.polity", "new-york-city")
        cs.add("polity", id=city, kind="municipality")
        name(city, "New York City")
        cs.add("polity_parent", polity_id=city, parent_id=cs.polity("US-NY"))
        cs.add("slug", path="us/ny/new-york-city", entity_id=city)
        cs.add("external_id", entity_id=city, scheme="wikidata", value="Q60")
        cs.add("scheme", code="us.place", polity_id=cs.polity("US"), kind="administrative", rank=3, noun="place")
        area = cs.area("us.place", "3651000")
        cs.add("area", id=area, scheme_code="us.place")
        cs.add("area_code", area_id=area, code="3651000", valid_from=None)
        cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "36"))
        name(area, "New York City")
        cs.add("polity_territory", polity_id=city, area_id=area)
        geom, point = conn.execute(
            """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
               from (select st_multi(st_union(g.geom)) as geom from area_geometry g
                     join area_code c on c.area_id = g.area_id
                     join area a on a.id = g.area_id and a.scheme_code = 'us.county'
                     where c.code = any(%s)) city""",
            [list(COUNTIES)],
        ).fetchone() or (None, None)
        cs.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            geom=geom,
            label_point=point,
            quality="official",
            source_id=cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000"),
        )
        unit = cs.unit("us.place", "3651000")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=city,
            scheme_code="us.place",
            code="3651000",
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
        )

        mayor = cs.key("us.office", "new-york-city:mayor")
        cs.add("office", id=mayor, polity_id=city, kind="mayor")
        name(mayor, "Mayor of New York City")
        irv = cs.key("us.electoral_system", "new-york-city:irv")
        cs.add(
            "electoral_system",
            id=irv,
            polity_id=city,
            rules={"formula": "irv", "maxRankings": 5, "batchElimination": True},
            rules_version=1,
        )
        name(irv, "New York City ranked-choice voting")
        council = cs.key("us.body", "NY:3651000:council")
        offices = {"mayor": (mayor, "Mayor of New York City")}
        for office, (key, title, county) in {
            "public-advocate": ("new-york-city:public-advocate", "New York City Public Advocate", None),
            "comptroller": ("new-york-city:comptroller", "New York City Comptroller", None),
            **{
                f"borough-president-{slug(borough)}": (
                    f"new-york-city:borough-president:{slug(borough)}",
                    f"{borough} Borough President",
                    county,
                )
                for county, borough in BOROUGHS.values()
            },
        }.items():
            offices[office] = (cs.key("us.office", key), title)
            polity = city if county is None else cs.key("us.polity", f"county:{county}")
            cs.add("office", id=offices[office][0], polity_id=polity, kind="other")
            name(offices[office][0], title)
        if cs.minted & {council, *(cs.key("us.polity", f"county:{county}") for county, _ in BOROUGHS.values())}:
            raise ValueError("the City Council and the borough polities must already exist")

        known = {
            value for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.person"])
        }
        persons: dict[str, UUID] = {}
        for label, (scheme, value) in PERSONS.items():
            persons[label] = cs.key(scheme, value)
            if persons[label] in cs.minted:
                raise ValueError(f"{label} must already exist")
            if slug(label) in known and (own := cs.key(f"{SOURCE}.person", slug(label))) != persons[label]:
                cs.merge(own, persons[label])

        for (day,) in frame.select("date").unique().sort("date").iter_rows():
            election = cs.key("us.election", f"{day}:new-york-city:primary")
            cs.add("election", id=election, polity_id=city, kind="primary", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} New York City primary election")
            cs.add("slug", path=f"us/ny/new-york-city/{day}-primary", entity_id=election)
            for (page,) in frame.filter(pl.col("date") == day).select("page").unique().sort("page").iter_rows():
                rounds_frame = frame.filter((pl.col("date") == day) & (pl.col("page") == page))
                title, party_code, office, district, contest_key, link = rounds_frame.select(
                    "contest", "party", "office", "district", "key", "url"
                ).row(0)
                party_key, adjective = PARTIES[party_code]
                party = cs.key("us.party", party_key)
                if party in cs.minted:
                    raise ValueError(f"the {adjective} Party must already exist")
                source_title = f"{day.year} Primary: Official Ranked Choice Rounds, {title}"
                source = cs.source(PUBLISHER, source_title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=source_title,
                    url=link,
                    license="Public record",
                )
                cs.artifact(raw.cached(SOURCE, link), source)

                rounds = rounds_frame["round"].max()
                contest = cs.key("us.contest", contest_key)
                seat = f"{office}-{district}" if district else office
                if office == "council":
                    district_area = cs.area("us.local_district", f"36:3651000:council:district-{district}")
                    if district_area in cs.minted:
                        raise ValueError(f"council district {district} must already exist")
                    owner, title = None, f"New York City Council, District {district}"
                elif office == "borough-president":
                    owner, title = offices[seat]
                    district_area = cs.area(
                        "us.county", next(county for county, borough in BOROUGHS.values() if slug(borough) == district)
                    )
                else:
                    owner, title = offices[office]
                    district_area = area
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if office == "council" else "office",
                    office_id=owner,
                    body_id=council if office == "council" else None,
                    party_scope_id=party,
                    district_area_id=district_area,
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=irv,
                    default_result_kind="counted",
                )
                name(contest, f"{day.year} {adjective} primary for {title}")
                cs.add(
                    "slug",
                    path=f"us/ny/new-york-city/{day}-primary/{seat}-{adjective.lower()}",
                    entity_id=contest,
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

                options: dict[str, UUID] = {}
                lasts = dict(rounds_frame.group_by("label").agg(pl.col("round").max()).iter_rows())
                for label in rounds_frame.filter(pl.col("round") == 1).sort("votes", descending=True)["label"]:
                    if label == INACTIVE:
                        continue
                    option_key = f"{contest_key}:{slug(label)}"
                    options[label] = cs.key("us.option", option_key)
                    cs.add(
                        "contest_option",
                        id=options[label],
                        contest_id=contest,
                        kind="write_in" if label == WRITE_INS else "candidate",
                        party_id=None if label == WRITE_INS else party,
                        ballot_label=label,
                    )
                    if label == WRITE_INS:
                        continue
                    if label in persons:
                        person = persons[label]
                    else:
                        person = cs.key(f"{SOURCE}.person", slug(label))
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, label)
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{option_key}:head"),
                        person_id=person,
                        option_id=options[label],
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                    )
                finalists = rounds_frame.filter((pl.col("round") == rounds) & (pl.col("label") != INACTIVE)).sort(
                    "votes", descending=True
                )["label"]
                for label, option in options.items():
                    if label != WRITE_INS:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="won" if label == finalists[0] else "lost" if lasts[label] == rounds else "eliminated",
                        )
                if result_set in cs.minted:
                    for label, count_round, votes in rounds_frame.select("label", "round", "votes").iter_rows():
                        inactive = label == INACTIVE
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None if inactive else options[label],
                            channel=0,
                            measure=21 if inactive else 1,
                            count_round=count_round,
                            flags=0,
                            value=votes,
                        )
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=4,
                        count_round=1,
                        flags=0,
                        value=rounds_frame.filter(pl.col("round") == 1)["votes"].sum(),
                    )
        return cs.write()
