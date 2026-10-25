import json
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import leh
from ea.store import connect

SOURCE = "gb.london"
VERSION = "3"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED = 1, 2, 3, 4, 5, 21
REPORT_2004 = (
    "https://data.london.gov.uk/download/e585w/94efc8af-b7ce-4d60-9b18-e587084e207f/GLA-mayoral-election-2004.pdf"
)
GLA = "Greater London Authority"
GLRO = "Greater London Returning Officer"
PARTIES = {
    "LAB": "Labour Party",
    "CON": "Conservative Party",
    "LD": "Liberal Democrats",
    "UKIP": "UK Independence Party",
    "R": "Respect",
    "BNP": "British National Party",
    "GRE": "Green Party",
    "CPA": "Christian Peoples Alliance",
    "IWCA": "Independent Working Class Association",
    "PMSS": "Pro-Motorist Small Shop",
    "NLP": "Natural Law Party",
    "CC": "Christian Choice",
    "LL": "Left List",
    "ED": "English Democrats",
}
PEOPLE = {
    "Ken Livingstone": "Q220723",
    "Boris Johnson": "Q180589",
    "Sadiq Khan": "Q334155",
    "Zac Goldsmith": "Q391562",
    "Steven Norris": "Q7615093",
    "Frank Dobson": "Q300182",
    "Susan Kramer": "Q332968",
    "Simon Hughes": "Q334313",
    "George Galloway": "Q332483",
    "Gerard Batten": "Q1510131",
    "Siân Berry": "Q7533470",
}
Candidate = tuple[str, str | None, int, int | None]
ELECTIONS: dict[int, tuple[date, str, str, str, int | None, list[Candidate]]] = {
    2000: (
        date(2000, 5, 4),
        REPORT_2004,
        GLA,
        "The 2004 London Elections",
        None,
        [
            ("Ken Livingstone", None, 667877, 776427),
            ("Steven Norris", "CON", 464434, 564137),
            ("Frank Dobson", "LAB", 223884, None),
            ("Susan Kramer", "LD", 203452, None),
            ("Balram Gidoomal", "CPA", 42060, None),
            ("Darren Johnson", "GRE", 38121, None),
            ("Michael Newland", "BNP", 33569, None),
            ("Nicholas Hockney", "UKIP", 16324, None),
            ("Geoffrey Ben-Nathan", "PMSS", 9956, None),
            ("Ashwinkumar Tanna", None, 9015, None),
            ("Geoffrey Clements", "NLP", 5470, None),
        ],
    ),
    2004: (
        date(2004, 6, 10),
        REPORT_2004,
        GLA,
        "The 2004 London Elections",
        None,
        [
            ("Ken Livingstone", "LAB", 685548, 828390),
            ("Steven Norris", "CON", 542423, 667180),
            ("Simon Hughes", "LD", 284647, None),
            ("Francis Maloney", "UKIP", 115666, None),
            ("Lindsey German", "R", 61731, None),
            ("Julian Leppert", "BNP", 58407, None),
            ("Darren Johnson", "GRE", 57332, None),
            ("Balram Gidoomal", "CPA", 41698, None),
            ("Lorna Reid", "IWCA", 9542, None),
            ("Puvanarani Nagalingam", None, 6692, None),
        ],
    ),
    2008: (
        date(2008, 5, 1),
        "https://data.london.gov.uk/download/2nplx/53d5718d-9e17-47fc-811e-6f95a4d3f293/GLA-mayoral-elections-2008.pdf",
        GLA,
        "The 2008 London Elections",
        None,
        [
            ("Boris Johnson", "CON", 1044067, 1169046),
            ("Ken Livingstone", "LAB", 894317, 1029406),
            ("Brian Paddick", "LD", 236752, None),
            ("Siân Berry", "GRE", 77396, None),
            ("Richard Barnbrook", "BNP", 69753, None),
            ("Alan Craig", "CC", 39266, None),
            ("Gerard Batten", "UKIP", 22435, None),
            ("Lindsey German", "LL", 16803, None),
            ("Matt O'Connor", "ED", 10700, None),
            ("Winston McKenzie", None, 5396, None),
        ],
    ),
    2012: (
        date(2012, 5, 3),
        "https://www.londonelects.org.uk/files/Results%20factsheets%202012.pdf",
        "London Elects",
        "2012 election results for the Mayor of London and the London Assembly",
        None,
        [
            ("Boris Johnson", "Conservative Party", 971931, 1054811),
            ("Ken Livingstone", "Labour Party", 889918, 992273),
            ("Jenny Jones", "Green Party", 98913, None),
            ("Brian Paddick", "Liberal Democrats", 91774, None),
            ("Siobhan Benita", None, 83914, None),
            ("Lawrence Webb", "UK Independence Party", 43274, None),
            ("Carlos Cortiglia", "British National Party", 28751, None),
        ],
    ),
    2016: (
        date(2016, 5, 5),
        "https://www.londonelects.org.uk/files/Mayor%20of%20London%202016.pdf",
        GLRO,
        "Election of the London Mayor: Declaration of Results of Poll, 5 May 2016",
        49871,
        [
            ("Sadiq Khan", "Labour Party", 1148716, 1310143),
            ("Zac Goldsmith", "The Conservative Party Candidate", 909755, 994614),
            ("Siân Berry", "Green Party", 150673, None),
            ("Caroline Pidgeon", "London Liberal Democrats", 120005, None),
            ("Peter Whittle", "UK Independence Party (UKIP)", 94373, None),
            ("Sophie Walker", "Women's Equality Party", 53055, None),
            ("George Galloway", "Respect (George Galloway)", 37007, None),
            ("Paul Golding", "Britain First - Putting British people first", 31372, None),
            ("Lee Harris", "Cannabis is Safer than Alcohol", 20537, None),
            ("David Furness", "British National Party", 13325, None),
            ("Prince Zylinski", None, 13202, None),
            ("Ankit Love", "One Love Party", 4941, None),
        ],
    ),
    2021: (
        date(2021, 5, 6),
        "https://www.londonelects.org.uk/files/Mayoral%20Final%20Results%202021.pdf",
        GLRO,
        "Election of the Mayor of London: Official Declaration of Results of Poll, 6 May 2021",
        114201,
        [
            ("Sadiq Khan", "Labour Party", 1013721, 1206034),
            ("Shaun Bailey", "Conservative Party Candidate", 893051, 977601),
            ("Siân Berry", "Green Party", 197976, None),
            ("Luisa Porritt", "Liberal Democrats", 111716, None),
            ("Niko Omilana", None, 49628, None),
            ("Laurence Fox", "The Reclaim Party", 47634, None),
            ("Brian Rose", "London Real Party", 31111, None),
            ("Richard Hewison", "Rejoin EU", 28012, None),
            ("Count Binface", "Count Binface for Mayor of London", 24775, None),
            ("Mandu Reid", "Vote Women's Equality Party on orange", 21182, None),
            ("Piers Corbyn", "Let London Live", 20604, None),
            ("Vanessa Hudson", "Animal Welfare Party-People, Animals, Environment", 16826, None),
            ("Peter Gammons", "UKIP", 14393, None),
            ("Farah London", None, 11869, None),
            ("David Kurten", "Heritage Party", 11025, None),
            ("Nims Obunge", None, 9682, None),
            ("Steve Kelleher", "Social Democratic Party", 8764, None),
            ("Kam Balayev", "Renew", 7774, None),
            ("Max Fosh", None, 6309, None),
            ("Valerie Brown", "The Burning Pink Party", 5305, None),
        ],
    ),
    2024: (
        date(2024, 5, 2),
        "https://www.londonelects.org.uk/files/total_mayoral_results_2024.pdf",
        GLRO,
        "Election of the Mayor of London: Official Declaration of Result of Poll, 2 May 2024",
        11127,
        [
            ("Sadiq Khan", "Labour Party", 1088225, None),
            ("Susan Hall", "The Conservative Party Candidate", 812397, None),
            ("Rob Blackie", "Liberal Democrat", 145184, None),
            ("Zoë Garbett", "The Green Party", 145114, None),
            ("Howard Cox", "ReformUK – London Deserves Better", 78865, None),
            ("Natalie Campbell", None, 47815, None),
            ("Amy Gallagher", "Social Democratic Party", 34449, None),
            ("Femy Amin", "Animal Welfare Party - People, Animals, Environment", 29280, None),
            ("Andreas Michli", None, 26121, None),
            ("Tarun Ghulati", None, 24702, None),
            ("Count Binface", "Count Binface for Mayor of London", 24260, None),
            ("Nick Scanlon", "Britain First - No To Immigration", 20519, None),
            ("Brian Rose", "London Real Party – Transform London", 7501, None),
        ],
    ),
}
SV_UNTIL = 2021
COUNTED: dict[int, tuple[int, int, int]] = {
    2000: (5089300, 1752303, 38141),
    2004: (5197792, 1920560, 56874),
    2008: (5435612, 2457918, 41032),
    2024: (6162428, 2495559, 11127),
}
CONSTITUENCIES = {
    "E32000001": "Barnet and Camden",
    "E32000002": "Bexley and Bromley",
    "E32000003": "Brent and Harrow",
    "E32000004": "City and East",
    "E32000005": "Croydon and Sutton",
    "E32000006": "Ealing and Hillingdon",
    "E32000007": "Enfield and Haringey",
    "E32000008": "Greenwich and Lewisham",
    "E32000009": "Havering and Redbridge",
    "E32000010": "Lambeth and Southwark",
    "E32000011": "Merton and Wandsworth",
    "E32000012": "North East",
    "E32000013": "South West",
    "E32000014": "West Central",
}
LAC_SHAPES = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/London_Assembly_Constituencies_Dec_2018_FCB_EN_2022"
    "/FeatureServer/0/query?where=1%3D1&outFields=*&outSR=27700&f=geojson"
)
LAC_NOTE = (
    "The 14 London Assembly constituencies have kept the boroughs the Greater London Authority Act 1999 gave them "
    "since the first election in 2000, so the Office for National Statistics' December 2018 full-extent boundaries "
    "are drawn for every election."
)
DATASTORE = "https://data.london.gov.uk/download"
FILE_2000 = f"{DATASTORE}/2l8n5/8b819146-fafb-4b75-86b4-619519857ad1/gla-elections-2000.xls"
LONDON_ELECTS = "https://www.londonelects.org.uk/files"
WARDS_2021 = f"{LONDON_ELECTS}/2021%20London%20Elects%20-%20Ward%20Level%20Data%20Release.xlsx"
Sheet = tuple[str, str, str, int, int, int | None, list[int], int, int, int, int | None]
SHEETS: dict[int, list[Sheet]] = {
    2004: [
        (
            "lad",
            f"{DATASTORE}/e585w/db2ee60f-6788-438f-85a5-ada35ef8a3e7/gla-elections-votes-party-borough-2004.xls",
            "2004",
            2,
            1,
            2,
            list(range(3, 13)),
            13,
            18,
            34,
            None,
        )
    ],
    2008: [
        (
            "lad",
            f"{DATASTORE}/2nplx/5b19e7ba-3014-4cd5-a13a-dca1c6c40a44/gla-elections-votes-party-borough-2008.xls",
            "2008",
            2,
            1,
            2,
            list(range(3, 13)),
            13,
            20,
            35,
            None,
        )
    ],
    2012: [
        (
            "lac",
            f"{DATASTORE}/e79kk/0de17f91-560c-42a9-9128-c2530a430972/gla-elections-2012-constituency.xls",
            "Mayoral",
            1,
            0,
            None,
            list(range(5, 12)),
            12,
            13,
            2,
            None,
        )
    ],
    2016: [
        (
            "lad",
            f"{DATASTORE}/emq4p/01f4ff3a-c562-4d61-977f-c2dfb36694ce/gla-elections-votes-all-2016.xlsx",
            "Boroughs",
            2,
            0,
            2,
            list(range(8, 20)),
            20,
            26,
            5,
            None,
        ),
        (
            "lac",
            f"{DATASTORE}/emq4p/01f4ff3a-c562-4d61-977f-c2dfb36694ce/gla-elections-votes-all-2016.xlsx",
            "Constituencies",
            2,
            1,
            None,
            list(range(6, 18)),
            18,
            24,
            3,
            None,
        ),
    ],
    2021: [("ward", WARDS_2021, "All", 1, 0, 3, list(range(5, 25)), 25, 31, 4, 32)],
    2024: [
        (
            "lac",
            f"{LONDON_ELECTS}/GLA%20Election%20May%202024%20Results%20Summary.xlsx",
            "1. Mayor of London",
            2,
            0,
            None,
            list(range(1, 14)),
            14,
            19,
            21,
            20,
        )
    ],
}
SHEET_TITLES = {
    FILE_2000: (GLA, "London Elections Results, 2000"),
    SHEETS[2004][0][1]: (GLA, "London Elections Results 2004, Borough"),
    SHEETS[2008][0][1]: (GLA, "London Elections Results 2008, Borough"),
    SHEETS[2012][0][1]: (GLA, "London Elections Results 2012, Constituency"),
    SHEETS[2016][0][1]: (GLA, "London Elections Results 2016, Wards, Boroughs, Constituency"),
    WARDS_2021: ("London Elects", "2021 London Elects Ward Level Data Release"),
    SHEETS[2024][0][1]: ("London Elects", "GLA Election May 2024 Results Summary"),
}
DECLARATIONS_2021 = {
    code: f"{LONDON_ELECTS}/{name.replace(' ', '%20')}%202021.pdf" for code, name in CONSTITUENCIES.items()
}
CORRECTED = {
    (2008, "E09000016", "Boris Johnson"): 50506,
    (2008, "E09000030", "Ken Livingstone"): 37631,
    (2008, "E09000030", "Lindsey German"): 796,
}
CORRECTED_NOTE = (
    "The GLA's 2008 borough file gives Boris Johnson 50,507 votes in Havering, and Ken Livingstone 37,630 and "
    "Lindsey German 797 in Tower Hamlets. Its report, The 2008 London Elections, prints 50,506, 37,631, and 796, "
    "which sum to the declared London totals, so the report's figures are shown."
)
RESOLVED = {
    "The Conservative Party Candidate": "Conservative Party",
    "Conservative Party Candidate": "Conservative Party",
    "London Liberal Democrats": "Liberal Democrats",
    "Liberal Democrat": "Liberal Democrats",
    "The Green Party": "Green Party",
    "UK Independence Party (UKIP)": "UK Independence Party",
    "UKIP": "UK Independence Party",
    "Respect (George Galloway)": "Respect",
    "Britain First - Putting British people first": "Britain First",
    "Britain First - No To Immigration": "Britain First",
    "ReformUK – London Deserves Better": "Reform UK",
    "Count Binface for Mayor of London": "Count Binface Party",
    "London Real Party – Transform London": "London Real Party",
    "Vote Women's Equality Party on orange": "Women's Equality Party",
    "Animal Welfare Party-People, Animals, Environment": "Animal Welfare Party",
    "Animal Welfare Party - People, Animals, Environment": "Animal Welfare Party",
}


def fetch() -> None:
    for url in [*(url for _, url, _, _, _, _ in ELECTIONS.values()), *DECLARATIONS_2021.values()]:
        raw.fetch(SOURCE, url, suffix=".pdf")
    for url in SHEET_TITLES:
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)
    raw.fetch(SOURCE, LAC_SHAPES, suffix=".geojson")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mayor of London elections 2000-2024, London-wide first and second rounds from the Greater London "
            "Returning Officer's declarations and the GLA's election reports, with first choices by Assembly "
            "constituency and borough from the GLA's and London Elects' result files",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        gb = cs.polity("GB")
        london = cs.key("gb.polity", "E12000007")
        region = cs.area("gb.region", "E12000007")
        if region in cs.minted:
            raise ValueError("publish gb.regions first: no London region area")
        cs.add("polity", id=london, kind="region", iso_code=None)
        cs.add("polity_parent", polity_id=london, parent_id=cs.polity("GB-ENG"))
        cs.add("polity_territory", polity_id=london, area_id=region)
        name(london, "Greater London")
        cs.add("slug", path="gb/london", entity_id=london)
        office = cs.key("gb.office", "mayor:london")
        cs.add("office", id=office, polity_id=london, body_id=None, kind="mayor")
        name(office, "Mayor of London")
        unit = cs.unit("gb.region", "E12000007")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=london,
            scheme_code="gb.region",
            code="E12000007",
            area_relation="equals",
            area_id=region,
            kind="standard",
            valid_from=None,
        )
        name(unit, "London")
        if conn.execute("select 1 from scheme where code = 'gb.lac'").fetchone() is None:
            cs.add(
                "scheme", code="gb.lac", polity_id=gb, kind="electoral_district", rank=3, noun="Assembly constituency"
            )
        shapes = raw.cached(SOURCE, LAC_SHAPES)
        ons = cs.source(
            "Office for National Statistics", "London Assembly Constituencies (December 2018) Boundaries EN BFC"
        )
        cs.add(
            "source",
            id=ons,
            kind="official_body",
            publisher="Office for National Statistics",
            title="London Assembly Constituencies (December 2018) Boundaries EN BFC",
            url="https://geoportal.statistics.gov.uk/datasets/6ab20f2a06a546eea6709ffa890362ca",
            license="Open Government Licence v3.0",
            license_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
            attribution="Source: Office for National Statistics licensed under the Open Government Licence v.3.0. "
            "Contains OS data © Crown copyright and database right",
        )
        cs.artifact(shapes, ons)
        first_day = ELECTIONS[2000][0]
        constituency_areas: dict[str, UUID] = {}
        constituency_units: dict[str, UUID] = {}
        for feature in json.loads(shapes.path.read_bytes())["features"]:
            code = feature["properties"]["lac18cd"]
            if feature["properties"]["lac18nm"] != CONSTITUENCIES[code]:
                raise ValueError(f"{code}: ONS names it {feature['properties']['lac18nm']}")
            area = constituency_areas[code] = cs.area("gb.lac", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.lac")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=area, parent_id=region, valid_from=None, valid_to=None)
            name(area, CONSTITUENCIES[code])
            geom, point = conn.execute(
                """select encode(st_asewkb(st_multi(st_transform(g, 4326))), 'hex'),
                          encode(st_asewkb(st_transform(st_pointonsurface(g), 4326)), 'hex')
                   from (select st_multi(st_collectionextract(st_makevalid(st_simplifypreservetopology(
                           st_setsrid(st_geomfromgeojson(%s), 27700), 5)), 3)) as g) s""",
                [json.dumps(feature["geometry"])],
            ).fetchone()
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=first_day,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
                geom=geom,
                label_point=point,
                quality="official",
                source_id=ons,
            )
            cs.add("note", entity_id=area, kind="context", body_md=LAC_NOTE)
            constituency_units[code] = cs.unit("gb.lac", code)
            cs.add(
                "reporting_unit",
                id=constituency_units[code],
                polity_id=london,
                scheme_code="gb.lac",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
            name(constituency_units[code], CONSTITUENCIES[code])
        if len(constituency_areas) != len(CONSTITUENCIES):
            raise ValueError(f"{len(constituency_areas)} ONS shapes for {len(CONSTITUENCIES)} constituencies")
        plans = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'gb.lac'").fetchall())
        cs.add(
            "boundary_set",
            **({"id": plans["2000"]} if "2000" in plans else {}),
            scheme_code="gb.lac",
            vintage="2000",
            valid_from=first_day,
            valid_to=None,
            as_of=first_day,
        )
        constituency_codes = {leh.fold(text): code for code, text in CONSTITUENCIES.items()}
        constituency_codes["city and east london"] = "E32000004"
        borough_names = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'gb.lad' and c.code like 'E09%'"""
            ).fetchall()
        )
        borough_names["E09000001"] = "City of London"
        borough_codes = {leh.fold(text): code for code, text in borough_names.items()}
        borough_units: dict[str, UUID] = {}
        fptp = cs.key("gb.electoral_system", "fptp")
        sv = cs.key("gb.electoral_system", "sv")
        if fptp in cs.minted or sv in cs.minted:
            raise ValueError("publish gb.parliament and gb.mayors first")
        parties: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        sources: dict[str, UUID] = {}
        for year, (day, url, publisher, title, rejected, candidates) in ELECTIONS.items():
            artifact = raw.cached(SOURCE, url)
            text = subprocess.run(
                ["pdftotext", "-layout", str(artifact.path), "-"], check=True, capture_output=True
            ).stdout.decode(errors="replace")
            printed = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)))
            figures = {votes for _, _, first, final in candidates for votes in (first, final) if votes is not None}
            if rejected is not None:
                figures.add(rejected)
            figures.update(COUNTED.get(year, ()))
            if missing := sorted(votes for votes in figures if str(votes) not in printed):
                raise ValueError(f"{year}: {missing} not printed in {url}")
            if url not in sources:
                sources[url] = cs.source(publisher, title)
                cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(artifact, sources[url])
            source = sources[url]
            names = [full for full, *_ in candidates]
            surnames = {leh.fold(full).split()[-1]: full for full in names}
            grains: dict[str, dict[str, dict[str, int | None]]] = {"lac": {}, "lad": {}}
            files: dict[str, str] = {}
            derived: set[str] = set()
            specs = SHEETS.get(year, [])
            if year == 2000:
                book = fastexcel.read_excel(raw.cached(SOURCE, FILE_2000).path)
                mayor, turnout, spoils = (
                    book.load_sheet(sheet, header_row=None).to_polars().rows()
                    for sheet in ("Mayoral by constituency", "Turnout", "Spoils")
                )
                specs = [("lac", FILE_2000, "", 1, 0, None, list(range(1, 23, 2)), 0, 0, 0, None)]
                merged: dict[str, dict[str, object]] = {}
                for row in turnout:
                    if (code := constituency_codes.get(leh.fold(str(row[0] or "")))) is not None:
                        merged.setdefault(code, {}).update(registered=row[1], ballots=row[2])
                for row in spoils[6:20]:
                    if (code := constituency_codes.get(leh.fold(str(row[0] or "")))) is not None:
                        merged.setdefault(code, {}).update(invalid=row[6], valid=row[8])
            for grain, file, sheet, header, name_col, lac_col, columns, good, spoiled, electorate, counted in specs:
                rows = (
                    mayor
                    if year == 2000
                    else fastexcel.read_excel(raw.cached(SOURCE, file).path)
                    .load_sheet(sheet, header_row=None)
                    .to_polars()
                    .rows()
                )
                order = []
                for column in columns:
                    person = leh.fold(re.split(r" - |\n| \(", str(rows[header][column]))[0]).split()
                    found = [full for surname, full in surnames.items() if surname in person]
                    if len(found) != 1:
                        raise ValueError(f"{year} {sheet}: {rows[header][column]!r} matches {found}")
                    order.append(found[0])
                if sorted(order) != sorted(names):
                    raise ValueError(f"{year} {sheet}: columns {order} against {names}")
                whole = any(spec[0] == "lac" for spec in specs)
                for row in rows[header + 1 :]:
                    place = leh.fold(str(row[name_col] or ""))
                    if grain == "lac":
                        targets = [("lac", constituency_codes.get(place))]
                    else:
                        targets = [("lad", borough_codes.get(place))]
                        if not whole:
                            targets.append(("lac", constituency_codes.get(leh.fold(str(row[lac_col] or "")))))
                    if any(code is None for _, code in targets):
                        continue
                    if year == 2000:
                        found = merged[targets[0][1]]
                        figures = {"valid": found["valid"], "invalid": found["invalid"]}
                        figures |= {"registered": found["registered"], "ballots": found["ballots"]}
                    else:
                        figures = {"valid": row[good], "invalid": row[spoiled], "registered": row[electorate]}
                        figures["ballots"] = None if counted is None else row[counted]
                    figures |= {full: row[column] for full, column in zip(order, columns, strict=True)}
                    if grain == "lad":
                        figures |= {full: CORRECTED.get((year, targets[0][1], full), figures[full]) for full in order}
                    for target, code in targets:
                        entry = grains[target].setdefault(code, {})
                        for key, value in figures.items():
                            entry[key] = None if value is None else (entry.get(key) or 0) + round(float(value))
                        files[target] = file
                    if grain == "ward":
                        derived.add("lad")
                    if not whole:
                        derived.add("lac")
            for entry in grains["lad"].values() if "lad" in derived else []:
                entry["registered"] = None
            for grain, entries in grains.items():
                if not entries:
                    continue
                if len(entries) != (len(CONSTITUENCIES) if grain == "lac" else 33):
                    raise ValueError(f"{year} {grain}: {len(entries)} areas")
                for full, _, first, _ in candidates:
                    if (summed := sum(entry[full] or 0 for entry in entries.values())) != first:
                        raise ValueError(f"{year} {grain}: {full} sums to {summed}, declared {first}")
            if "lac" in derived:
                declarations = (
                    {code: DECLARATIONS_2021[code] for code in CONSTITUENCIES}
                    if year == 2021
                    else {code: url for code in CONSTITUENCIES}
                )
                for code, entry in grains["lac"].items():
                    text = subprocess.run(
                        ["pdftotext", "-layout", str(raw.cached(SOURCE, declarations[code]).path), "-"],
                        check=True,
                        capture_output=True,
                    ).stdout.decode(errors="replace")
                    shown = set(re.findall(r"\d+", re.sub(r"(?<=\d)[,.](?=\d{3})", "", text)))
                    if year == 2021:
                        entry["registered"] = int(re.findall(r"Electorate +(\d+)", text)[-1])
                    if missing := sorted(
                        f"{key} {value}"
                        for key, value in entry.items()
                        if value is not None and str(value) not in shown
                    ):
                        raise ValueError(f"{year} {code}: {missing} not printed in {declarations[code]}")
                derived.discard("lac")
                if year == 2021:
                    files["lac"] = "declarations"
            national = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s", [f"{day}:local:gb"]
            ).fetchone()
            election = cs.key("gb.election", f"{day}:gla")
            cs.add(
                "election",
                id=election,
                polity_id=london,
                parent_id=None if national is None else national[0],
                kind="general",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{year} London elections")
            cs.add("slug", path=f"gb/london/{day}-general", entity_id=election)
            supplementary = year <= SV_UNTIL
            contest_key = f"{day}:mayor:london"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                body_id=None,
                district_area_id=region,
                magnitude=1,
                round_number=1,
                electoral_system_id=sv if supplementary else fptp,
                default_result_kind="counted",
                term="full",
            )
            name(contest, f"Mayor of London, {year}")
            cs.add("slug", path=f"gb/london/{day}-general/mayor", entity_id=contest)
            result_set = cs.key("gb.london.result_set", contest_key)
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
            first_round = 1 if supplementary else 0
            registered, ballots, rejected = COUNTED.get(year, (None, None, rejected))
            valid = sum(first for _, _, first, _ in candidates)
            totals = {REGISTERED: registered, BALLOTS: ballots, VALID: valid, INVALID: rejected}
            cells = [
                (measure, first_round if measure == VALID else 0, value, 0)
                for measure, value in totals.items()
                if value is not None
            ]
            if year not in COUNTED:
                for measure, key in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (INVALID, "invalid")):
                    summed = [entry[key] for entry in grains["lac"].values()]
                    if totals[measure] is None and summed and None not in summed:
                        cells.append((measure, 0, sum(summed), 1))
                    elif (
                        totals[measure] is not None and summed and None not in summed and sum(summed) != totals[measure]
                    ):
                        raise ValueError(
                            f"{year}: constituencies sum {key} to {sum(summed)}, declared {totals[measure]}"
                        )
            if supplementary:
                exhausted = valid - sum(final for _, _, _, final in candidates if final is not None)
                cells.append((EXHAUSTED, 2, exhausted, 0))
            for measure, number, value, flags in cells:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=flags,
                    value=value,
                )
            options: dict[str, UUID] = {}
            winner = max(candidates, key=lambda row: (row[3] or 0) if supplementary else row[2])[0]
            for full, party_code, first, final in candidates:
                label = PARTIES.get(party_code, party_code) if year <= 2008 else party_code
                owner = None
                if label is not None:
                    label = RESOLVED.get(label, label)
                    if label not in parties:
                        found = cs.key(*leh.RESOLVED[label]) if label in leh.RESOLVED else known.get(leh.fold(label))
                        if found is None:
                            found = cs.key("gb.party", slug(label))
                            if found in cs.minted:
                                cs.add("party", id=found, kind="party", polity_id=gb)
                                name(found, label)
                        parties[label] = found
                    owner = parties[label]
                if full not in persons:
                    persons[full] = (
                        cs.key("wikidata", PEOPLE[full]) if full in PEOPLE else cs.key("gb.london.person", full)
                    )
                    if persons[full] in cs.minted:
                        if full in PEOPLE:
                            raise ValueError(f"{full}: no person with {PEOPLE[full]}")
                        cs.add("person", id=persons[full])
                        name(persons[full], full)
                option_key = f"{contest_key}:{slug(full)}"
                option = options[full] = cs.key("gb.london.option", option_key)
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
                    id=cs.key("gb.london.candidacy", option_key),
                    person_id=persons[full],
                    option_id=option,
                    role="head",
                    ballot_name=full,
                    member_of_party_id=owner,
                    is_independent=owner is None,
                )
                rounds = {first_round: first}
                if supplementary:
                    rounds[2] = final or 0
                for number, votes in rounds.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=number,
                        flags=0,
                        value=votes,
                    )
                if full == winner:
                    kinds = ("won", "elected")
                elif supplementary and final is None:
                    kinds = ("eliminated",)
                else:
                    kinds = ("lost",)
                for kind in kinds:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            for grain, entries in grains.items():
                if not entries:
                    continue
                file = files[grain]
                if file not in sources:
                    if file == "declarations":
                        publisher, title = "London Elects", "GLA 2021 Elections: constituency results and local totals"
                        page = "https://www.londonelects.org.uk/im-voter/election-results/results-2021"
                    else:
                        (publisher, title), page = SHEET_TITLES[file], file
                    sources[file] = cs.source(publisher, title)
                    cs.add("source", id=sources[file], kind="official_body", publisher=publisher, title=title, url=page)
                    for link in DECLARATIONS_2021.values() if file == "declarations" else [file]:
                        cs.artifact(raw.cached(SOURCE, link), sources[file])
                own = source if grain == "lac" and year in (2004, 2008) else sources[file]
                grain_set = cs.key("gb.london.result_set", f"{contest_key}:{grain}")
                cs.add(
                    "result_set",
                    id=grain_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=own,
                    is_default=False,
                    completeness=1,
                )
                cs.add("citation", entity_id=grain_set, source_id=own)
                if own != sources[file]:
                    cs.add("citation", entity_id=grain_set, source_id=sources[file])
                if grain == "lad" and any(key[0] == year for key in CORRECTED):
                    cs.add("note", entity_id=grain_set, kind="context", body_md=CORRECTED_NOTE)
                flags = 1 if grain in derived else 0
                for code, entry in entries.items():
                    if grain == "lac":
                        place = constituency_units[code]
                    else:
                        if code not in borough_units:
                            borough_units[code] = cs.unit("gb.lad", code)
                            if borough_units[code] in cs.minted:
                                area = cs.area("gb.lad", code)
                                if area in cs.minted:
                                    cs.add("area", id=area, scheme_code="gb.lad")
                                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                                    cs.add(
                                        "area_parent",
                                        area_id=area,
                                        parent_id=cs.area("gb.country", "ENG"),
                                        valid_from=None,
                                        valid_to=None,
                                    )
                                    name(area, "City of London")
                                cs.add(
                                    "reporting_unit",
                                    id=borough_units[code],
                                    polity_id=london,
                                    scheme_code="gb.lad",
                                    code=code,
                                    area_relation="equals",
                                    area_id=area,
                                    kind="standard",
                                    valid_from=None,
                                )
                                name(borough_units[code], borough_names[code])
                        place = borough_units[code]
                    values = [
                        (None, measure, first_round if measure == VALID else 0, entry[key])
                        for measure, key in (
                            (REGISTERED, "registered"),
                            (BALLOTS, "ballots"),
                            (VALID, "valid"),
                            (INVALID, "invalid"),
                        )
                    ]
                    values += [(options[full], VOTES, first_round, entry[full]) for full in names]
                    for option_id, measure, number, value in values:
                        if value is None:
                            continue
                        cs.add(
                            "result_value",
                            result_set_id=grain_set,
                            unit_id=place,
                            option_id=option_id,
                            channel=0,
                            measure=measure,
                            count_round=number,
                            flags=flags,
                            value=value,
                        )
        return cs.write()
