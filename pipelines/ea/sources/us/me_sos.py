import io
import json
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.fec import NOTES
from ea.store import DATA, connect

SOURCE = "us.me_sos"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
ORIGIN = "https://www.maine.gov/sos/cec/elec/results/"
WAYBACK = "https://web.archive.org/web/{}id_/" + ORIGIN + "{}"
SUBDIVISIONS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_23_cousub_500k.zip"
PUBLISHER = "Maine Department of the Secretary of State"
WRITE_IN = "Write-in"
DISTRICT = "2302"
OTHER = "ME:2302:other"
CONTESTS: dict[
    str,
    tuple[str, tuple[str, str], tuple[str, str] | None, list[tuple[str, str]], list[dict[str | None, int]], int],
] = {
    "2018-06-12:ME:house:2:democratic": (
        "2018 Democratic Primary Election: Representative to Congress, District 2",
        ("20200622145205", "2018/Democraticcgfinal.xls"),
        None,
        [
            ("20200621083859", "2018/congressd2-1.xlsx"),
            ("20200621133913", "2018/congressd2-2.xlsx"),
            ("20200621152143", "2018/congressd2-3.xlsx"),
            ("20200623112017", "2018/congressd2-4.xlsx"),
        ],
        [
            {"Golden": 20987, "St. Clair": 17742, "Olson": 3993, "Fulford": 2489, None: 5634},
            {"Golden": 23611, "St. Clair": 19853, None: 7381},
        ],
        50845,
    ),
    "2018-06-12:ME:governor:democratic": (
        "2018 Democratic Primary Election: Governor",
        ("20200622224601", "2018/democraticgovfinal.xls"),
        None,
        [
            ("20200622224602", "2018/govd-1.xlsx"),
            ("20200622180202", "2018/govd-2.xlsx"),
            ("20200622145207", "2018/govd-3.xlsx"),
            ("20200622132212", "2018/govd-4.xlsx"),
            ("20200622065941", "2018/govd-5.xlsx"),
        ],
        [
            {
                "Mills, Janet T.": 41735,
                "Cote, Adam Roland": 35478,
                "Sweet, Elizabeth A.": 20767,
                "Eves, Mark W.": 17887,
                "Dion, Mark N.": 5200,
                "Russell, Diane Marie": 2728,
                "Dion, Donna J.": 1596,
                WRITE_IN: 748,
                None: 6111,
            },
            {
                "Mills, Janet T.": 44042,
                "Cote, Adam Roland": 37543,
                "Sweet, Elizabeth A.": 22987,
                "Eves, Mark W.": 19521,
                None: 8157,
            },
            {"Mills, Janet T.": 49945, "Cote, Adam Roland": 42623, "Sweet, Elizabeth A.": 29944, None: 9738},
            {"Mills, Janet T.": 63384, "Cote, Adam Roland": 53866, None: 15000},
        ],
        132250,
    ),
    "2018-11-06:ME:house:2": (
        "2018 General Election: Representative to Congress, District 2",
        ("20181214213948", "2018/updated-summary-report-CD2.xls"),
        None,
        [
            ("20200622163023", "2018/Nov18CVRExportFINAL1.xlsx"),
            ("20200621145127", "2018/Nov18CVRExportFINAL2.xlsx"),
            ("20200622170131", "2018/Nov18CVRExportFINAL3.xlsx"),
            ("20200621133915", "2018/RepCD2-8final.xlsx"),
            ("20200621145936", "2018/UOCAVA-FINALRepCD2.xlsx"),
            ("20200622063544", "2018/UOCAVA-AUX-CVRRepCD2.xlsx"),
            ("20200622175000", "2018/UOCAVA2CVRRepCD2.xlsx"),
            ("20200621084118", "2018/AUXCVRProofedCVR95RepCD2.xlsx"),
        ],
        [
            {"Poliquin": 134184, "Golden": 132013, "Bond": 16552, "Hoar": 6875, None: 6453},
            {"Golden": 142440, "Poliquin": 138931, None: 14706},
        ],
        296077,
    ),
    "2020-07-14:ME:house:2:republican": (
        "2020 Republican Primary Election: Representative to Congress, District 2",
        ("20201020060900", "2020/offcg2summary.xls"),
        None,
        [
            ("20201022050357", "2020/offCG2CVR.xlsx"),
            ("20201020193634", "2020/offCR2manual1.xlsx"),
            ("20201022052626", "2020/offRC2manual2.xlsx"),
            ("20201020213227", "2020/offrc2manual3.xlsx"),
            ("20201020062446", "2020/offrc2UOCAVA.xlsx"),
        ],
        [
            {"Crafts": 23665, "Bennett": 16920, "Brakey": 11976, None: 5945},
            {"Crafts": 28019, "Bennett": 20295, None: 10192},
        ],
        58506,
    ),
    "2020-07-14:ME:senate:11:dp": (
        "2020 Democratic Primary Election: State Senator, District 11",
        ("20201020031303", "2020/offss11summary.xls"),
        None,
        [
            ("20201020081412", "2020/offss11cvr.xlsx"),
            ("20201020130911", "2020/offss11manual.xlsx"),
            ("20201020074944", "2020/offss11dUOCAVA.xlsx"),
        ],
        [
            {"Curry, Glenn Chip": 2294, "Stanicki, Robyn M.": 1669, "Pattavina, Charles F.": 1316, None: 444},
            {"Curry, Glenn Chip": 2807, "Stanicki, Robyn M.": 2212, None: 704},
        ],
        5723,
    ),
    "2020-07-14:ME:house:41:dp": (
        "2020 Democratic Primary Election: Representative to the Legislature, District 41",
        ("20201022080414", "2020/offsr41summary.xls"),
        None,
        [
            ("20201022052555", "2020/offsr41cvr.xlsx"),
            ("20201022050325", "2020/offsr41dmanual.xlsx"),
            ("20201022080351", "2020/offsr41dUOCAVA.xlsx"),
        ],
        [
            {"Zager, Samuel Lewis": 1239, "Grant, Benjamin K.": 912, "Davis, Laurie J.": 368, None: 119},
            {"Zager, Samuel Lewis": 1397, "Grant, Benjamin K.": 1067, None: 174},
        ],
        2638,
    ),
    "2020-07-14:ME:house:47:dp": (
        "2020 Democratic Primary Election: Representative to the Legislature, District 47",
        ("20201020081418", "2020/offsr47summary.xls"),
        None,
        [
            ("20201020062516", "2020/offsr47cvr20200803.xlsx"),
            ("20201020031306", "2020/offsr47manual.xlsx"),
            ("20201021075429", "2020/offsr47UOCAVA.xlsx"),
        ],
        [
            {"Bell, Arthur L.": 978, "Abbott, Heather": 892, "Fromuth, Peter J.": 623, None: 131},
            {"Bell, Arthur L.": 1274, "Abbott, Heather": 1139, None: 211},
        ],
        2624,
    ),
    "2020-07-14:ME:house:49:dp": (
        "2020 Democratic Primary Election: Representative to the Legislature, District 49",
        ("20201022050330", "2020/offsr49summary.xls"),
        None,
        [
            ("20201020202049", "2020/offcvrsr49.xlsx"),
            ("20201020213051", "2020/offsr49dmanual.xlsx"),
            ("20201020213755", "2020/offsr49dUOCAVA.xlsx"),
        ],
        [
            {"Arford, Poppy": 899, "Wilson, Katherine E.": 657, "Perreault, Corinne Ann": 613, None: 189},
            {"Arford, Poppy": 1189, "Wilson, Katherine E.": 886, None: 283},
        ],
        2358,
    ),
    "2020-07-14:ME:house:90:dp": (
        "2020 Democratic Primary Election: Representative to the Legislature, District 90",
        ("20201020081405", "2020/offsr90summary.xls"),
        None,
        [
            ("20201020130827", "2020/offsr90cvr20200803.xlsx"),
            ("20201020024534", "2020/offsr90dmanual.xlsx"),
            ("20201022205738", "2020/offsr90dUOCAVA.xlsx"),
        ],
        [
            {"Crafts, Lydia V.": 1010, "Pieh, Wendy": 741, "Levesque, David J.": 273, None: 59},
            {"Crafts, Lydia V.": 1143, "Pieh, Wendy": 838, None: 102},
        ],
        2083,
    ),
    "2022-06-14:ME:senate:16:rp": (
        "2022 Republican Primary Election: State Senator, District 16",
        ("20220930192040", "2022/ss16rep622.xls"),
        None,
        [
            ("20220930113202", "2022/ss16622.xlsx"),
            ("20220929204101", "2022/ss16uocava622.xlsx"),
        ],
        [
            {"Perkins, Michael D.": 846, "Kitchin, Kevin D.": 733, "Andre, Mark R.": 335, None: 65},
            {"Perkins, Michael D.": 922, "Kitchin, Kevin D.": 845, None: 212},
        ],
        1979,
    ),
    "2022-11-08:ME:house:2": (
        "2022 General Election: Representative to Congress, District 2",
        ("20221206204345", "2022/Rep%20to%20Congress%20Dist%202%20RCV%20results.pdf"),
        None,
        [
            ("20221207114512", "2022/reptocongress2-1.xlsx"),
            ("20221206190929", "2022/reptocong2-2.xlsx"),
            ("20221206223216", "2022/reptocong2-3.xlsx"),
            ("20221206223824", "2022/reptocong2-4.xlsx"),
            ("20221206200915", "2022/reptocong2-5.xlsx"),
            ("20221207013238", "2022/reptocong6.xlsx"),
            ("20221206213348", "2022/reptocong2-7.xlsx"),
        ],
        [
            {"Golden": 153074, "Poliquin": 141260, "Bond": 21655, WRITE_IN: 393, None: 6396},
            {"Golden": 165136, "Poliquin": 146142, None: 11500},
        ],
        322778,
    ),
    "2024-11-05:ME:house:2": (
        "2024 General Election: Representative to Congress, District 2",
        ("20250114102637", "2024/Nov24%20RCV%20CG2%20Summary%20Report%2020241115.pdf"),
        ("20250327213535", "2024/Nov24%20CG2%201st%20Choices.xlsx"),
        [
            ("20250114235535", "2024/Nov24%20CVR%20Export%2020241115%20-%201.xlsx"),
            ("20250114122623", "2024/Nov24%20CVR%20Export%2020241115%20-%202.xlsx"),
            ("20250114235928", "2024/Nov24%20CVR%20Export%2020241115%20-%203.xlsx"),
            ("20250114230056", "2024/Nov24%20CVR%20Export%2020241115%20-%204.xlsx"),
            ("20250114154325", "2024/UOCAVA%205%2020241115%20Final.xlsx"),
            ("20250114100619", "2024/CD%202%20Aux%20Master%206%2020242215%20Final.xlsx"),
        ],
        [
            {"Golden": 196388, "Theriault": 194130, WRITE_IN: 421, None: 11997},
            {"Golden": 197151, "Theriault": 194445, None: 11340},
        ],
        402936,
    ),
}
KEPT = {"2022-11-08:ME:house:2"}
RECORDS = {
    ("2018-06-12:ME:governor:democratic", 4): {"Mills, Janet T.": 63387, "Cote, Adam Roland": 53866, None: 14997},
}
PRIMARIES = {
    "2018-06-12:ME:governor:democratic": (
        "2018-11-06:ME:governor",
        "democratic-party",
        "2018 Maine gubernatorial Democratic primary",
        "us/me/2018-06-12-primary/governor-democratic",
    ),
    "2020-07-14:ME:senate:11:dp": (
        "2020-11-03:ME:senate:11:g",
        "democratic-party",
        "2020 Maine Senate Democratic primary, District 11",
        "us/me/2020-07-14-primary/state-senate-11-democratic-primary",
    ),
    "2020-07-14:ME:house:41:dp": (
        "2020-11-03:ME:house:41:g",
        "democratic-party",
        "2020 Maine House of Representatives Democratic primary, District 41",
        "us/me/2020-07-14-primary/state-house-41-democratic-primary",
    ),
    "2020-07-14:ME:house:47:dp": (
        "2020-11-03:ME:house:47:g",
        "democratic-party",
        "2020 Maine House of Representatives Democratic primary, District 47",
        "us/me/2020-07-14-primary/state-house-47-democratic-primary",
    ),
    "2020-07-14:ME:house:49:dp": (
        "2020-11-03:ME:house:49:g",
        "democratic-party",
        "2020 Maine House of Representatives Democratic primary, District 49",
        "us/me/2020-07-14-primary/state-house-49-democratic-primary",
    ),
    "2020-07-14:ME:house:90:dp": (
        "2020-11-03:ME:house:90:g",
        "democratic-party",
        "2020 Maine House of Representatives Democratic primary, District 90",
        "us/me/2020-07-14-primary/state-house-90-democratic-primary",
    ),
    "2022-06-14:ME:senate:16:rp": (
        "2022-11-08:ME:senate:16:g",
        "republican-party",
        "2022 Maine Senate Republican primary, District 16",
        "us/me/2022-06-14-primary/state-senate-16-republican-primary",
    ),
}
PEOPLE = {
    "Mills, Janet T.": "2018-11-06:ME:governor",
    "Cote, Adam Roland": "2008-06-10:ME:house:1:democratic",
    "Sweet, Elizabeth A.": "2020-07-14:ME:senate:democratic",
    "Eves, Mark W.": "2014-11-04:ME:house:6:g",
    "Dion, Mark N.": "2016-11-08:ME:senate:28:g",
    "Russell, Diane Marie": "2014-11-04:ME:house:39:g",
    "Curry, Glenn Chip": "2020-11-03:ME:senate:11:g",
    "Stanicki, Robyn M.": "2022-11-08:ME:house:38:g",
    "Zager, Samuel Lewis": "2020-11-03:ME:house:41:g",
    "Bell, Arthur L.": "2020-11-03:ME:house:47:g",
    "Arford, Poppy": "2020-11-03:ME:house:49:g",
    "Crafts, Lydia V.": "2020-11-03:ME:house:90:g",
    "Pieh, Wendy": "2008-11-04:ME:house:50:g",
    "Perkins, Michael D.": "2022-11-08:ME:senate:16:g",
    "Andre, Mark R.": "2020-11-03:ME:house:110:g",
}
RENAMES = {
    "Mattawamkeg": "Mattawamkeag",
    "Penobscott Nation": "Penobscot Indian Island",
    "Penobscot Nation Voting District": "Penobscot Indian Island",
    "Indian Township": "Passamaquoddy Indian Township",
    "Pleasant Point Voting District": "Passamaquoddy Pleasant Point",
    "Carrabassett": "Carrabassett Valley",
    "Monhegan Island": "Monhegan",
}
OFFICIAL = (
    "The Secretary of State's ranked-choice tabulation for the district. Ballots exhausted in a round are the "
    "report's overvotes, undervotes, and exhausted choices together, and include blank ballots from the first round. "
    "Each candidate's value at round n is their running total; an eliminated candidate has 0 at the round of their "
    "elimination."
)
FIRST = (
    " The Secretary of State's tabulation drops write-ins before its first round, so its report shows only the final "
    "round, which is the second round here. The first round is the first-choice count the Secretary of State "
    "published by municipality, summed over the district; its blank ballots are counted as exhausted."
)
DERIVED = (
    "Tabulated by electionsarchive.com from the Secretary of State's cast vote records, municipality by "
    "municipality. A ballot exhausts at an overvote or after two skipped rankings in a row, a repeated candidate "
    "counts once, and blank ballots are exhausted from the first round, as Maine's rules have them. Each round drops "
    "the candidates the official count dropped. Wards and voting districts are summed into their municipality, "
    "townships counted with a town are counted in that town, and ballots from unorganized townships, from voters "
    "overseas, and from records that name no municipality are one unit for the rest of the district."
)
EXACT = " Summed over the district, every round reproduces the Secretary of State's count exactly."
EXACT_GOVERNOR = (
    " Summed over the state, rounds 1 to 3 reproduce the Secretary of State's count exactly. In round 4 the records "
    "give Mills 3 more votes and leave 3 fewer ballots exhausted than the report."
)
EXACT_FINAL = (
    " Summed over the district, the final round reproduces the Secretary of State's count exactly. The first round "
    "differs from the published first-choice count: the records give Golden 145 more votes, Theriault 129 more, and "
    "write-ins 1,605 more, and 1,879 fewer ballots exhausted in round 1."
)


def fetch() -> None:
    downloads = [(SUBDIVISIONS, ".zip")]
    for _, summary, first, records, _, _ in CONTESTS.values():
        for stamp, path in [summary, *([] if first is None else [first]), *records]:
            downloads.append((WAYBACK.format(stamp, path), "." + path.rsplit(".", 1)[1]))
    for url, suffix in downloads:
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url, suffix=suffix)
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw.cached(SOURCE, SUBDIVISIONS).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    towns: dict[tuple[str, bool], tuple[str, str, int, str]] = {}
    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record is None or shape is None:
            continue
        proper, whole = str(record["NAME"]), str(record["NAMELSAD"])
        kind = whole[len(proper) :].strip().lower()
        if kind not in ("town", "city", "plantation", "reservation"):
            continue
        key = (re.sub(r"[^a-z]", "", proper.lower()), kind == "plantation")
        if key not in towns or int(str(record["ALAND"])) > towns[key][2]:
            towns[key] = (
                str(record["GEOID"]),
                proper if kind in ("town", "city") else f"{proper} {kind.title()}",
                int(str(record["ALAND"])),
                json.dumps(shape.__geo_interface__),
            )

    def town(precinct: str) -> str | None:
        name = re.sub(r"\s+(?:W\d.*|Ward\s*\d.*|All)$", "", precinct.split("/")[0].strip())
        plantation = name.endswith(" Plt")
        name = name.removesuffix(" Plt")
        name = RENAMES.get(name, re.sub(r"^Saint ", "St. ", name))
        found = towns.get((re.sub(r"[^a-z]", "", name.lower()), plantation))
        return None if found is None else found[0]

    frames = []
    for key, (_, _, first, records, rounds, total) in CONTESTS.items():
        ballots = []
        for stamp, path in records:
            sheet = (
                fastexcel.read_excel(raw.cached(SOURCE, WAYBACK.format(stamp, path)).path)
                .load_sheet(0, dtypes="string")
                .to_polars()
            )
            ranks = sorted(
                (column for column in sheet.columns if re.search(r"\d(?:st|nd|rd|th) Choice", column)),
                key=lambda column: int(re.findall(r"(\d)(?:st|nd|rd|th) Choice", column)[0]),
            )
            ballots.append(
                sheet.select(
                    precinct=pl.col("Precinct").str.strip_chars(),
                    choices=pl.concat_list(
                        (choice if key in PRIMARIES else choice.str.replace(r",.*$", "")).str.strip_chars()
                        for choice in (
                            pl.col(column)
                            .str.replace(r"\s*\(\d+\)$", "")
                            .str.replace(r"^(?:DEM|REP|GRN|GRE|LIB|IND|UNE) ", "")
                            for column in ranks
                        )
                    ),
                )
            )
        ballots_frame = pl.concat(ballots)
        if ballots_frame.height != total:
            raise ValueError(f"{key}: {ballots_frame.height} records, the official count has {total} ballots")
        continuing = [
            {
                choice
                for choice in ballots_frame["choices"].explode().drop_nulls().unique()
                if choice.lower() not in ("undervote", "overvote")
            },
            *({label for label in tally if label is not None} for tally in rounds[1:]),
        ]
        counts: dict[tuple[str, int, str | None], int] = {}
        for precinct, choices in ballots_frame.iter_rows():
            for number, standing in enumerate(continuing, 1):
                top, skips, seen = None, 0, set()
                for choice in choices:
                    if choice is None or choice.lower() == "undervote":
                        skips += 1
                        if skips == 2:
                            break
                        continue
                    skips = 0
                    if choice.lower() == "overvote":
                        break
                    if choice in seen:
                        continue
                    seen.add(choice)
                    if choice in standing:
                        top = choice
                        break
                counts[(precinct, number, top)] = counts.get((precinct, number, top), 0) + 1
        precincts = sorted({precinct for precinct, _, _ in counts})
        tally = pl.DataFrame(
            [
                *((precinct, number, label, votes) for (precinct, number, label), votes in counts.items()),
                *(
                    (precinct, number, label, 0)
                    for number in range(2, len(continuing) + 1)
                    for label in sorted(continuing[number - 2] - continuing[number - 1])
                    for precinct in precincts
                ),
            ],
            schema={"precinct": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
            orient="row",
        )
        sums = tally.group_by("round", "label").agg(pl.col("votes").sum())
        for number, official in enumerate(rounds, 1):
            if first is not None and number == 1:
                continue
            found = {label: votes for _, label, votes in sums.filter(pl.col("round") == number).iter_rows() if votes}
            if found != {label: votes for label, votes in RECORDS.get((key, number), official).items() if votes}:
                raise ValueError(f"{key} round {number}: the records give {found}, the official count {official}")
        frames.append(
            tally.with_columns(key=pl.lit(key), town=pl.col("precinct").map_elements(town, return_dtype=pl.String))
        )
    tallies = pl.concat(frames)
    print(
        tallies.filter(
            pl.col("round") == 1, pl.col("town").is_null(), ~pl.col("precinct").str.contains(r"^\d+$|^UOCAVA$")
        )
        .group_by("precinct")
        .agg(pl.col("votes").sum())
        .sort("votes", descending=True)
        .head(30)
    )
    tallies.write_parquet(PARSED / "tallies.parquet")
    used = set(tallies["town"].drop_nulls())
    shapes = sorted((code, display, geojson) for code, display, _, geojson in towns.values() if code in used)
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select f.code, f.display, encode(st_asewkb(s.geom), 'hex'),
                     encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                   from unnest(%s::text[], %s::text[], %s::text[]) as f(code, display, geojson)
                   cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(
                     st_setsrid(st_geomfromgeojson(f.geojson), 4269), 4326)), 3)) as geom) s""",
                [[shape[0] for shape in shapes], [shape[1] for shape in shapes], [shape[2] for shape in shapes]],
            ).fetchall(),
            schema=["code", "display", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "towns.parquet")


def emit() -> Path:
    tallies = pl.read_parquet(PARSED / "tallies.parquet")
    with connect() as conn:
        polities = {
            code
            for (code,) in conn.execute(
                """select c.code from area a join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   where a.scheme_code = 'us.town' and c.code like '23%'"""
            ).fetchall()
        }
        slugs = {path for (path,) in conn.execute("select path from slug").fetchall()}
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's ranked-choice contests for the 2nd congressional district, 2018 to 2024, and its state "
            "primaries that went past round 1, 2018 to 2022, with each round by municipality from the cast vote records",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-ME")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        if census in cs.minted:
            raise ValueError("the Census Bureau's cartographic boundary source must already exist")
        cs.artifact(raw.cached(SOURCE, SUBDIVISIONS), census)
        for code, display, geom, point in pl.read_parquet(PARSED / "towns.parquet").iter_rows():
            if code in polities:
                continue
            polity = cs.key("us.polity", f"town:{code}")
            path = f"us/me/{slug(display)}"
            for fallback in (f"{path}-town", f"{path}-{code}"):
                if path in slugs:
                    path = fallback
            slugs.add(path)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, display)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.town", code)
            cs.add("area", id=area, scheme_code="us.town")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", code[:5]))
            name(area, f"{display}, ME")
            cs.add("polity_territory", polity_id=polity, area_id=area)
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
                id=cs.unit("us.town", code),
                polity_id=polity,
                scheme_code="us.town",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        cs.add(
            "boundary_set",
            scheme_code="us.town",
            vintage="2020",
            valid_from=None,
            valid_to=None,
            as_of=date(2020, 1, 1),
        )
        aggregates: set[UUID] = set()
        for key, (title, summary, first, records, rounds, total) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            seat = re.fullmatch(r".*:(house|senate):(\d+):[dr]p", key)
            scheme, code = (
                ("us.state", "23")
                if ":governor:" in key
                else ("us.cd", DISTRICT)
                if seat is None
                else ("us.sldl" if seat[1] == "house" else "us.sldu", f"ME:{seat[2]}")
            )
            top = cs.unit(scheme, code, day) if scheme == "us.cd" else cs.unit(scheme, code)
            area = cs.area(scheme, code)
            rest = OTHER if scheme == "us.cd" else f"ME:{scheme.removeprefix('us.')}:{code.removeprefix('ME:')}:other"
            where = (lambda text: text.replace("the district", "the state")) if scheme == "us.state" else str
            contest = cs.key("us.contest", key)
            if key in PRIMARIES:
                general_key, party_key, contest_name, path = PRIMARIES[key]
                general = cs.key("us.contest", general_key)
                party = cs.key("us.party", party_key)
                election = cs.key("us.election", f"{key[:10]}:ME:primary")
                irv = cs.key("us.electoral_system", "irv")
                row = conn.execute(
                    "select kind, office_id, body_id, district_area_id, boundary_set_id, term from contest where id = %s",
                    [general],
                ).fetchone()
                if row is None or cs.minted & {general, party, election, irv}:
                    raise ValueError(f"{key}: its general, party, primary election, and IRV must already exist")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind=row[0],
                    office_id=row[1],
                    body_id=row[2],
                    district_area_id=row[3],
                    seat_id=None,
                    boundary_set_id=row[4],
                    term=row[5],
                    party_scope_id=party,
                    magnitude=1,
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=irv,
                    default_result_kind="counted",
                )
                name(contest, contest_name)
                cs.add("slug", path=path, entity_id=contest)
            else:
                row = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
                if contest in cs.minted or row is None:
                    raise ValueError(f"{key} must already exist")
                election = row[0]
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ORIGIN + summary[1],
                license="Public record",
            )
            for stamp, path in [summary, *([] if first is None else [first]), *records]:
                cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, path)), source)

            existing = conn.execute(
                "select id, kind, ballot_label from contest_option where contest_id = %s", [contest]
            ).fetchall()
            options: dict[str, UUID] = {}
            for label in sorted(label for label in rounds[0] if label is not None):
                if label == WRITE_IN:
                    found = [option for option, kind, _ in existing if kind == "write_in"]
                    if not found:
                        options[label] = cs.key("us.option", f"{key}:write_in:scattered")
                        cs.add(
                            "contest_option",
                            id=options[label],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Scattered",
                        )
                        continue
                elif key in PRIMARIES:
                    surname, given = label.split(", ")
                    display = f"{given} {surname}"
                    if label in PEOPLE:
                        reference = cs.key("us.contest", PEOPLE[label])
                        people = conn.execute(
                            """select c.person_id from candidacy c join contest_option o on o.id = c.option_id
                               where o.contest_id = %s and o.ballot_label ilike %s""",
                            [reference, f"%{surname}%"],
                        ).fetchall()
                        if reference in cs.minted or len(people) != 1:
                            raise ValueError(f"{key}: {len(people)} candidates for {label} in {PEOPLE[label]}")
                        person = people[0][0]
                    else:
                        person = cs.key(f"{SOURCE}.person", slug(display))
                        cs.add("person", id=person)
                        name(person, display)
                    options[label] = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=options[label],
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=display,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=options[label],
                        role="head",
                        ballot_name=display,
                        member_of_party_id=party,
                        is_independent=False,
                        is_incumbent=None,
                    )
                    continue
                else:
                    found = [option for option, kind, text in existing if kind == "candidate" and label in text]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {label}")
                options[label] = found[0]

            sets = []
            if key not in KEPT:
                if key not in PRIMARIES:
                    federal = cs.key("us.fec.result_set", key)
                    if federal in cs.minted:
                        raise ValueError(f"{key}: us.fec's result set must already exist")
                    if key in NOTES:
                        kind, body = NOTES[key]
                        for (note,) in conn.execute(
                            "select id from note where entity_id = %s and kind = %s", [federal, kind]
                        ).fetchall():
                            cs.add("note", id=note, entity_id=federal, kind=kind, body_md=body)
                    for previous in conn.execute(
                        """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                             supersedes_id from result_set where id = %s""",
                        [federal],
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
                                    previous,
                                    strict=True,
                                )
                            ),
                            is_default=False,
                        )
                official = cs.key(f"{SOURCE}.result_set", key)
                final = rounds[-1]
                winner = max((label for label in final if label is not None), key=lambda label: final[label])
                for label, option in options.items():
                    if label != WRITE_IN:
                        cs.add(
                            "outcome",
                            result_set_id=official,
                            option_id=option,
                            kind="won" if label == winner else "lost" if label in final else "eliminated",
                        )
                values = pl.DataFrame(
                    [
                        ("top", number, label, tally.get(label, 0))
                        for number, tally in enumerate(rounds, 1)
                        for label in rounds[0]
                        if label in tally or label in rounds[number - 2]
                    ],
                    schema={"town": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                    orient="row",
                )
                sets.append((official, True, where(OFFICIAL + ("" if first is None else FIRST)), values, 0))

            rows = tallies.filter(pl.col("key") == key).with_columns(pl.col("town").fill_null("other"))
            towns = sorted(set(rows["town"]) - {"other"})
            if len(towns) > 1:
                other = cs.unit("us.aggregate", rest)
                if other not in aggregates:
                    aggregates.add(other)
                    cs.add(
                        "reporting_unit",
                        id=other,
                        polity_id=state,
                        scheme_code="us.aggregate",
                        code=rest,
                        area_relation="within",
                        area_id=area,
                        kind="aggregate",
                        valid_from=None,
                    )
                if scheme != "us.state":
                    cs.frame(
                        "reporting_unit_area",
                        pl.DataFrame(
                            {
                                "unit_id": [str(cs.unit("us.town", town)) for town in towns],
                                "area_id": str(area),
                                "valid_from": day,
                                "valid_to": day + timedelta(days=1),
                            }
                        ),
                    )
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:municipalities"),
                        False,
                        where(
                            DERIVED
                            + (EXACT_GOVERNOR if scheme == "us.state" else EXACT if first is None else EXACT_FINAL)
                        ),
                        rows.group_by("town", "round", "label").agg(pl.col("votes").sum()),
                        1,
                    )
                )

            for result_set, default, note, values, flags in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if result_set not in cs.minted:
                    continue
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                units = {
                    town: str(top if town == "top" else other if town == "other" else cs.unit("us.town", town))
                    for town in values["town"].unique()
                }
                unknown = set(values["label"].drop_nulls()) - set(options)
                if unknown:
                    raise ValueError(f"{key}: no options for {unknown}")
                frame = values.with_columns(unit_id=pl.col("town").replace_strict(units))
                if default:
                    frame = pl.concat(
                        [
                            frame,
                            pl.DataFrame(
                                {
                                    "town": ["top"],
                                    "round": [0],
                                    "label": ["ballots"],
                                    "votes": [total],
                                    "unit_id": [units["top"]],
                                },
                                schema=frame.schema,
                            ),
                        ]
                    )
                else:
                    frame = pl.concat(
                        [
                            frame,
                            frame.filter(pl.col("round") == 1)
                            .group_by("town", "unit_id")
                            .agg(pl.col("votes").sum())
                            .select(
                                "town",
                                round=pl.lit(0, pl.Int16),
                                label=pl.lit("ballots"),
                                votes="votes",
                                unit_id="unit_id",
                            ),
                        ]
                    )
                frame = pl.concat(
                    [
                        frame,
                        frame.filter(pl.col("round") == 1, pl.col("label").is_not_null())
                        .group_by("town", "unit_id")
                        .agg(pl.col("votes").sum())
                        .select(
                            "town", round=pl.lit(1, pl.Int16), label=pl.lit("valid"), votes="votes", unit_id="unit_id"
                        ),
                    ]
                )
                cs.frame(
                    "result_value",
                    frame.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit_id",
                        option_id=pl.col("label").replace_strict(
                            {label: str(option) for label, option in options.items()}, default=None
                        ),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.when(pl.col("label").is_null())
                        .then(21)
                        .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                        .cast(pl.Int16),
                        count_round=pl.col("round").cast(pl.Int16),
                        flags=pl.lit(flags, pl.Int16),
                        value="votes",
                    ),
                )
        return cs.write()
