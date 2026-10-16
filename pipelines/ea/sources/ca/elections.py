import codecs
import csv
import io
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "ca.elections"
VERSION = "7"
PARSED = DATA / "parsed" / SOURCE
MAPS = "https://ftp.maps.canada.ca/pub/elections_elections/Electoral-districts_Circonscription-electorale"
RESULTS = "https://www.elections.ca/res/rep/off"
ORDER = date(2025, 3, 23)
ORDERS = {
    "2003": (
        date(2004, 5, 23),
        date(2015, 8, 2),
        308,
        "https://ftp.geogratis.gc.ca/pub/nrcan_rncan/vector/electoral/2011/fed308.2011.zip",
        "6d1d8f90-1c25-5fd0-880d-138d27c8cb57",
    ),
    "2013": (
        date(2015, 8, 2),
        ORDER,
        338,
        f"{MAPS}/Elections_Canada_2021/FED_CA_2021_EN.zip",
        "47a0f098-7445-41bb-a147-41686b692887",
    ),
    "2023": (
        ORDER,
        None,
        343,
        f"{MAPS}/federal_electoral_districts_boundaries_2023/FED_CA_2023_EN-SHP.zip",
        "18bf3ea7-1940-46ec-af52-9ba3f77ed708",
    ),
}
ELECTIONS: dict[date, tuple[str, str, str, str, str | None]] = {
    date(2004, 6, 28): (
        "38th",
        "2003",
        "https://open.canada.ca/data/dataset/3d44e2da-3e36-45cf-ab2a-037801eaaa6b/resource/6ffdff30-1328-4b23-99c4-7d500b86709c/download",
        "38th-ge-poll-by-polls-tables.zip",
        None,
    ),
    date(2006, 1, 23): (
        "39th",
        "2003",
        "https://www.elections.ca/Scripts/OVR2006/25/data_donnees",
        "pollresults_resultatsbureau_canada.zip",
        "pollresults",
    ),
    date(2008, 10, 14): (
        "40th",
        "2003",
        "https://www.elections.ca/scripts/OVR2008/31/data",
        "pollresults_resultatsbureau_canada.zip",
        "pollresults",
    ),
    date(2011, 5, 2): (
        "41st",
        "2003",
        "https://www.elections.ca/scripts/OVR2011/34/data_donnees",
        "pollresults_resultatsbureau_canada.zip",
        "pollresults",
    ),
    date(2015, 10, 19): (
        "42nd",
        "2013",
        f"{RESULTS}/ovr2015app/41/data_donnees",
        "pollresults_resultatsbureauCanada.zip",
        "pollresults",
    ),
    date(2019, 10, 21): (
        "43rd",
        "2013",
        f"{RESULTS}/ovr2019app/51/data_donnees",
        "pollresults_resultatsbureauCanada.zip",
        "pollresults",
    ),
    date(2021, 9, 20): (
        "44th",
        "2013",
        f"{RESULTS}/ovr2021app/53/data_donnees",
        "pollresults_resultatsbureauCanada.zip",
        "pollresults",
    ),
    date(2025, 4, 28): (
        "45th",
        "2023",
        f"{RESULTS}/ovrGE45/62/data_donnees",
        "pollresults_resultatsbureauCanada.zip",
        "byed",
    ),
}
BYELECTIONS: dict[date, tuple[str, tuple[str, ...]]] = {
    date(2016, 10, 24): ("ovr_2016/43", ("48027",)),
    date(2017, 4, 3): ("ovr_2017b/44", ("24068", "35055", "35078", "48007", "48008")),
    date(2017, 10, 23): ("ovr_2017c/45", ("24035", "48033")),
    date(2017, 12, 11): ("ovr_2017d/46", ("10002", "35093", "47001", "59030")),
    date(2018, 6, 18): ("ovr_2018/47", ("24022",)),
    date(2018, 12, 3): ("ovr_2018b/48", ("35050",)),
    date(2019, 2, 25): ("ovr_2019/49", ("24054", "35119", "59003")),
    date(2019, 5, 6): ("ovr_2019b/50", ("59018",)),
    date(2020, 10, 26): ("ovr_2020/52", ("35108", "35118")),
    date(2022, 12, 12): ("ovr_2022/54", ("35061",)),
    date(2023, 6, 19): ("ovr_2023/55", ("24053", "35080", "46007", "46014")),
    date(2023, 7, 24): ("ovr_2023b/56", ("48007",)),
    date(2024, 3, 4): ("ovr_2024/57", ("35023",)),
    date(2024, 6, 24): ("ovr_2024b/58", ("35090",)),
    date(2024, 9, 16): ("ovr_2024c/59", ("24037", "46005")),
    date(2024, 12, 16): ("ovr_2024d/60", ("59007",)),
    date(2025, 8, 18): ("ovr_2025/63", ("48002",)),
    date(2026, 4, 13): ("ovr_2026/64", ("24073", "35096", "35112")),
}
UNALLOCATED = 32
PROVINCES = {
    "10": ("NL", "Newfoundland and Labrador"),
    "11": ("PE", "Prince Edward Island"),
    "12": ("NS", "Nova Scotia"),
    "13": ("NB", "New Brunswick"),
    "24": ("QC", "Quebec"),
    "35": ("ON", "Ontario"),
    "46": ("MB", "Manitoba"),
    "47": ("SK", "Saskatchewan"),
    "48": ("AB", "Alberta"),
    "59": ("BC", "British Columbia"),
    "60": ("YT", "Yukon"),
    "61": ("NT", "Northwest Territories"),
    "62": ("NU", "Nunavut"),
}
PARTIES = {
    "LIBERAL": ("Liberal Party of Canada", "LPC", "#d71920"),
    "CONSERVATIVE": ("Conservative Party of Canada", "CPC", "#1a4782"),
    "NDP-NEW DEMOCRATIC PARTY": ("New Democratic Party", "NDP", "#f37021"),
    "BLOC QUÉBÉCOIS": ("Bloc Québécois", "BQ", "#21b9e3"),
    "GREEN PARTY": ("Green Party of Canada", "GPC", "#3d9b35"),
    "PEOPLE'S PARTY - PPC": ("People's Party of Canada", "PPC", "#4e2a84"),
    "NATIONAL ADVANCEMENT": ("National Advancement Party of Canada", None, None),
    "COMMUNIST": ("Communist Party of Canada", None, "#ff6347"),
    "LIBERTARIAN": ("Libertarian Party of Canada", None, "#f2ba00"),
    "MARXIST-LENINIST": ("Marxist-Leninist Party of Canada", None, "#f5191a"),
    "PARTI RHINOCÉROS PARTY": ("Parti Rhinocéros Party", None, "#d8bfd8"),
    "CHRISTIAN HERITAGE PARTY": ("Christian Heritage Party of Canada", None, None),
    "CENTRIST": ("Centrist Party of Canada", None, None),
    "ANIMAL PROTECTION PARTY": ("Animal Protection Party of Canada", None, None),
}
RENAMED = {"ANIMAL PROTECTION PARTY": ("Animal Alliance Environment Voters Party of Canada", date(2016, 1, 1))}
ALIASES = {
    "PEOPLE'S PARTY": "PEOPLE'S PARTY - PPC",
    "ML": "MARXIST-LENINIST",
    "RHINOCEROS": "PARTI RHINOCÉROS PARTY",
    "RADICAL MARIJUANA": "MARIJUANA PARTY",
    "NEORHINO.CA": "PARTI RHINOCÉROS PARTY",
    "N.D.P.": "NDP-NEW DEMOCRATIC PARTY",
    "CANADIAN ACTION": "CAP",
    "AACEV PARTY OF CANADA": "ANIMAL PROTECTION PARTY",
    "AAEV PARTY OF CANADA": "ANIMAL PROTECTION PARTY",
    "ANIMAL ALLIANCE/ENVIRONMENT VOTERS": "ANIMAL PROTECTION PARTY",
    "CHP CANADA": "CHRISTIAN HERITAGE PARTY",
    "PIRATE PARTY": "PIRATE",
    "CFP": "CANADIAN FUTURE PARTY",
}
UNAFFILIATED = ("", "INDEPENDENT", "NO AFFILIATION")
COLUMNS = [
    "fed",
    "fed_name",
    "fed_name_fr",
    "poll",
    "poll_name",
    "void",
    "no_poll",
    "combined_with",
    "rejected",
    "electors",
    "family",
    "middle",
    "first",
    "party",
    "party_fr",
    "incumbent",
    "elected",
    "votes",
]


def decoded(data: bytes) -> str:
    return data.decode("utf-8-sig") if data.startswith(codecs.BOM_UTF8) else data.decode("cp1252")


def fetch() -> None:
    for _, _, _, url, _ in ORDERS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    for _, _, folder, polls, document in ELECTIONS.values():
        raw.fetch(SOURCE, f"{folder}/{polls}", suffix=".zip")
        if document is not None:
            raw.fetch(SOURCE, f"{folder}/table_tableau08.csv", suffix=".csv")
    for folder, feds in BYELECTIONS.values():
        for file in (*(f"pollresults_resultatsbureau{fed}" for fed in feds), "table_tableau03"):
            raw.fetch(SOURCE, f"{RESULTS}/{folder}/data_donnees/{file}.csv", suffix=".csv")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for day in [*ELECTIONS, *BYELECTIONS]:
        rows: list[list[str]] = []
        tally_data = b""
        if day in BYELECTIONS:
            folder, feds = BYELECTIONS[day]
            for fed in feds:
                path = raw.cached(SOURCE, f"{RESULTS}/{folder}/data_donnees/pollresults_resultatsbureau{fed}.csv").path
                rows.extend(row for row in list(csv.reader(io.StringIO(decoded(path.read_bytes()))))[1:] if row)
        else:
            _, _, folder, archive, document = ELECTIONS[day]
            with zipfile.ZipFile(raw.cached(SOURCE, f"{folder}/{archive}").path) as z:
                if document is None:
                    standings: dict[str, list[tuple[str, str]]] = {}
                    for record in csv.DictReader(io.StringIO(decoded(z.read("38th/table_tableau12.csv")))):
                        standings.setdefault(record["Electoral District/Circonscription"].split("/")[0], []).append(
                            (record["Candidate/Candidat"], record["Majority/Majorité"])
                        )
                    for member in z.namelist():
                        if "pollbypoll" not in member:
                            continue
                        header, *lines = csv.reader(io.StringIO(decoded(z.read(member))))
                        fed_name, fed_name_fr = lines[0][0].split("/")
                        candidates = []
                        for display in header[3:-3]:
                            (entry, majority), *others = [
                                (entry, majority)
                                for entry, majority in standings[fed_name]
                                if entry.startswith(f"{display} ")
                            ]
                            if others:
                                raise ValueError(f"{day}: {display} matches several candidates in {fed_name}")
                            rest = entry.removeprefix(display).strip()
                            party, party_fr = rest.removeprefix("**").strip().split("/", 1)
                            candidates.append(
                                (
                                    display,
                                    party,
                                    party_fr,
                                    "Y" if rest.startswith("**") else "N",
                                    "Y" if majority else "N",
                                )
                            )
                        for line in lines:
                            if not line:
                                continue
                            spill = len(line) - len(header)
                            line = [*line[:2], ",".join(line[2 : 3 + spill]), *line[3 + spill :]]
                            if not line[1] and not line[2].startswith("Group "):
                                raise ValueError(f"{day}: unnumbered poll {line[2]} in {fed_name}")
                            status = line[3]
                            for (display, party, party_fr, incumbent, elected), votes in zip(
                                candidates, line[3:-3], strict=True
                            ):
                                rows.append(
                                    [
                                        member[-9:-4],
                                        fed_name,
                                        fed_name_fr,
                                        line[1] or f"S/R {line[2].removeprefix('Group ')[0]}",
                                        line[2],
                                        "Y" if status == "Void" else "N",
                                        "Y" if status == "No poll held" else "N",
                                        status.removeprefix("Merged with No. ") if status.startswith("Merged") else "",
                                        line[-3],
                                        line[-1],
                                        "",
                                        "",
                                        display,
                                        party,
                                        party_fr,
                                        incumbent,
                                        elected,
                                        votes if votes.strip().isdigit() else "",
                                    ]
                                )
                    tally_data = z.read("38th/table_tableau08.csv")
                else:
                    for member in z.namelist():
                        if member.startswith("pollresults"):
                            rows.extend(
                                row for row in list(csv.reader(io.StringIO(decoded(z.read(member)))))[1:] if row
                            )
                    tally_data = raw.cached(SOURCE, f"{folder}/table_tableau08.csv").path.read_bytes()
        polls = (
            pl.DataFrame(rows, schema=COLUMNS, orient="row")
            .with_columns(pl.col("fed_name").str.replace_all('"', ""))
            .with_columns(pl.all().str.strip_chars().replace("", None))
            .with_columns(
                pl.col("rejected", "electors", "votes").cast(pl.Int64),
                combined_with=pl.when(pl.col("combined_with") != pl.col("poll"))
                .then("combined_with")
                .max()
                .over("fed", "poll"),
            )
        )
        if polls.filter((pl.col("void") == "Y") | (pl.col("no_poll") == "Y") | pl.col("combined_with").is_not_null())[
            "votes"
        ].sum():
            raise ValueError(f"{day}: polls counted elsewhere carry votes")
        if day in BYELECTIONS:
            codes = {label: code for code, (_, label) in PROVINCES.items()}
            by_poll = polls.unique(["fed", "poll"])
            for record in csv.DictReader(
                io.StringIO(
                    decoded(
                        raw.cached(SOURCE, f"{RESULTS}/{folder}/data_donnees/table_tableau03.csv").path.read_bytes()
                    )
                )
            ):
                code = codes[record["Province"].split("/")[0]]
                printed = (
                    int(record["Valid Ballots/Bulletins valides"]),
                    int(record["Rejected Ballots/Bulletins rejetés"]),
                )
                summed = (
                    int(polls.filter(pl.col("fed").str.starts_with(code))["votes"].sum()),
                    int(by_poll.filter(pl.col("fed").str.starts_with(code))["rejected"].sum()),
                )
                if printed != summed:
                    raise ValueError(f"{day}: polls sum to {summed} in {code}, Elections Canada prints {printed}")
            affiliations = [(label, label) for label in polls["party"].unique(maintain_order=True)]
        else:
            tallies = {
                tuple(int(value or 0) for value in row[1:14]): row[0]
                for row in pl.read_csv(io.BytesIO(decoded(tally_data).encode()), infer_schema_length=0).iter_rows()
            }
            sums = polls.group_by("party", pl.col("fed").str.slice(0, 2).alias("province")).agg(pl.col("votes").sum())
            affiliations = []
            for label in polls["party"].unique(maintain_order=True):
                tally = tuple(
                    int(sums.filter((pl.col("party") == label) & (pl.col("province") == code))["votes"].sum())
                    for code in PROVINCES
                )
                if tally not in tallies:
                    raise ValueError(f"{day}: {label} does not match Elections Canada's votes by affiliation")
                affiliations.append((label, tallies[tally].split("/", 1)[0]))
        pl.DataFrame(affiliations, schema=["label", "name"], orient="row").write_parquet(
            PARSED / f"parties-{day}.parquet"
        )
        polls.write_parquet(PARSED / f"polls-{day}.parquet")

    for vintage, (_, _, _, url, _) in ORDERS.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as z:
            parts = {
                name.rsplit(".", 1)[1].lower(): io.BytesIO(z.read(name))
                for name in z.namelist()
                if "_EN" in name.upper() and name.rsplit(".", 1)[1].lower() in ("shp", "shx", "dbf")
            }
            encoding = "utf-8" if any(name.lower().endswith(".cpg") for name in z.namelist()) else "cp1252"
        reader = shapefile.Reader(shp=parts["shp"], shx=parts["shx"], dbf=parts["dbf"], encoding=encoding)
        records = [
            (record.as_dict(), shape)
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None
        ]
        with connect() as conn:
            conn.execute(
                """create temp table fed on commit drop as
                   select code, name_en, name_fr,
                          st_multi(st_collectionextract(st_makevalid(st_transform(
                            st_setsrid(st_geomfromgeojson(geojson), 3347), 4326)), 3)) as geom
                   from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name_en, name_fr, geojson)""",
                [
                    [str(record.get("FED_NUM") or record["FEDNUM"]) for record, _ in records],
                    [record.get("ED_NAMEE") or record["ENNAME"] for record, _ in records],
                    [record.get("ED_NAMEF") or record["FRNAME"] for record, _ in records],
                    [json.dumps(shape.__geo_interface__) for _, shape in records],
                ],
            )
            pl.DataFrame(
                conn.execute(
                    """select code, name_en, name_fr, encode(st_asewkb(geom), 'hex'),
                              encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                       from (select code, min(name_en) as name_en, min(name_fr) as name_fr,
                                    case when count(*) > 1 then st_multi(st_union(geom)) else (array_agg(geom))[1] end
                                      as geom
                             from fed group by code) as f
                       order by code"""
                ).fetchall(),
                schema=["code", "name_en", "name_fr", "geom", "label_point"],
                orient="row",
            ).write_parquet(PARSED / f"fed-{vintage}.parquet")
            conn.execute(
                "create temp table province on commit drop as"
                " select left(code, 2) as code, st_multi(st_union(geom)) as geom from fed group by 1"
            )
            pl.DataFrame(
                conn.execute(
                    """select code, encode(st_asewkb(geom), 'hex') from province
                       union all select 'CA', encode(st_asewkb(st_multi(st_union(geom))), 'hex') from province"""
                ).fetchall(),
                schema=["code", "geom"],
                orient="row",
            ).write_parquet(PARSED / f"province-{vintage}.parquet")


def emit() -> Path:
    provinces = dict(pl.read_parquet(PARSED / "province-2023.parquet").iter_rows())

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Canada's federal general elections from 2004 to 2025 and by-elections from 2016 to 2026 by poll",
            polity="CA",
        )
        cs.renamed["2023:"] = ""

        def name(
            entity: UUID,
            text: str,
            kind: str = "official",
            lang: str = "en",
            start: date | None = None,
            end: date | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" and end is None,
                valid_from=start,
                valid_from_precision="day" if start is None else "year",
                valid_to=end,
                valid_to_precision="day" if end is None else "year",
            )

        boundaries: dict[str, UUID] = {}
        for vintage, (_, _, _, url, dataset) in ORDERS.items():
            title = f"Federal electoral districts, {vintage} Representation Order"
            boundaries[vintage] = cs.source("Elections Canada and Natural Resources Canada", title)
            cs.add(
                "source",
                id=boundaries[vintage],
                kind="official_body",
                publisher="Elections Canada and Natural Resources Canada",
                title=title,
                url=f"https://open.canada.ca/data/en/dataset/{dataset}",
                license="Open Government Licence – Canada",
            )
            cs.artifact(raw.cached(SOURCE, url), boundaries[vintage])
        results: dict[date, UUID] = {}
        for day, (ordinal, _, folder, archive, document) in ELECTIONS.items():
            title = f"{ordinal} General Election, poll-by-poll results"
            results[day] = cs.source("Elections Canada", title)
            cs.add(
                "source",
                id=results[day],
                kind="official_body",
                publisher="Elections Canada",
                title=title,
                url="https://open.canada.ca/data/en/dataset/3d44e2da-3e36-45cf-ab2a-037801eaaa6b"
                if document is None
                else f"https://www.elections.ca/content.aspx?section=res&dir=rep/off/{ordinal[:2]}gedata&document={document}&lang=e",
                license="Open Government Licence – Canada",
            )
            for file in (archive,) if document is None else (archive, "table_tableau08.csv"):
                cs.artifact(raw.cached(SOURCE, f"{folder}/{file}"), results[day])
        for day, (folder, feds) in BYELECTIONS.items():
            title = f"{day:%B} {day.day}, {day.year}, by-election{'s' if len(feds) > 1 else ''}, poll-by-poll results"
            results[day] = cs.source("Elections Canada", title)
            cs.add(
                "source",
                id=results[day],
                kind="official_body",
                publisher="Elections Canada",
                title=title,
                url=f"{RESULTS}/{folder.split('/')[0]}/home.html",
                license="Open Government Licence – Canada",
            )
            for file in (*(f"pollresults_resultatsbureau{fed}" for fed in feds), "table_tableau03"):
                cs.artifact(raw.cached(SOURCE, f"{RESULTS}/{folder}/data_donnees/{file}.csv"), results[day])

        ca = cs.polity("CA")
        cs.add("polity", id=ca, kind="sovereign_state", iso_code="CA")
        name(ca, "Canada")
        cs.add("slug", path="ca", entity_id=ca)
        cs.add("external_id", entity_id=ca, scheme="wikidata", value="Q16")
        for code, rank, noun, kind in (
            ("ca.nation", 0, "country", "administrative"),
            ("ca.province", 1, "province or territory", "administrative"),
            ("ca.fed", 2, "electoral district", "electoral_district"),
            ("ca.pd", 3, "polling division", "precinct"),
        ):
            cs.add("scheme", code=code, polity_id=ca, kind=kind, rank=rank, noun=noun)

        nation = cs.area("ca.nation", "CA")
        province_areas = {code: cs.area("ca.province", code) for code in PROVINCES}
        cs.add("area", id=nation, scheme_code="ca.nation")
        for area in province_areas.values():
            cs.add("area", id=area, scheme_code="ca.province")
        cs.add("area_code", area_id=nation, code="CA", valid_from=None)
        name(nation, "Canada")
        cs.add("polity_territory", polity_id=ca, area_id=nation)
        cs.add(
            "area_geometry",
            area_id=nation,
            valid_from=None,
            geom=provinces["CA"],
            label_point=None,
            quality="official",
            source_id=boundaries["2023"],
        )
        for code, (abbreviation, label) in PROVINCES.items():
            province = cs.polity(f"CA-{abbreviation}")
            cs.add("polity", id=province, kind="federal_unit", iso_code=f"CA-{abbreviation}")
            name(province, label)
            cs.add("polity_parent", polity_id=province, parent_id=ca)
            cs.add("area_code", area_id=province_areas[code], code=code, valid_from=None)
            cs.add("area_parent", area_id=province_areas[code], parent_id=nation)
            name(province_areas[code], label)
            cs.add("polity_territory", polity_id=province, area_id=province_areas[code])
            cs.add(
                "area_geometry",
                area_id=province_areas[code],
                valid_from=None,
                geom=provinces[code],
                label_point=None,
                quality="official",
                source_id=boundaries["2023"],
            )
        cs.add(
            "boundary_set",
            scheme_code="ca.province",
            vintage="2023",
            valid_from=None,
            valid_to=None,
            as_of=max(ELECTIONS),
        )

        fed_areas: dict[tuple[str, str], UUID] = {}
        commons = cs.key(f"{SOURCE}.body", "house_of_commons")
        cs.add("body", id=commons, polity_id=ca, kind="lower_house")
        name(commons, "House of Commons")
        for vintage, (valid_from, valid_to, seats, _, _) in ORDERS.items():
            cs.add("body_size", body_id=commons, seats=seats, valid_from=valid_from, valid_to=valid_to)
            for fed in pl.read_parquet(PARSED / f"fed-{vintage}.parquet").iter_rows(named=True):
                area = fed_areas[vintage, fed["code"]] = cs.area("ca.fed", fed["code"], valid_from)
                cs.add("area", id=area, scheme_code="ca.fed")
                cs.add("area_code", area_id=area, code=fed["code"], valid_from=valid_from, valid_to=valid_to)
                cs.add("area_parent", area_id=area, parent_id=province_areas[fed["code"][:2]])
                name(area, fed["name_en"].replace("--", "—"))
                if fed["name_fr"] != fed["name_en"]:
                    name(area, fed["name_fr"].replace("--", "—"), "native", "fr")
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    geom=fed["geom"],
                    label_point=fed["label_point"],
                    quality="official",
                    source_id=boundaries[vintage],
                )
            cs.add(
                "boundary_set",
                scheme_code="ca.fed",
                vintage=vintage,
                valid_from=valid_from,
                valid_to=valid_to,
                as_of=max(day for day, election in ELECTIONS.items() if election[1] == vintage),
            )

        fptp = cs.key(f"{SOURCE}.electoral_system", "fptp")
        cs.add("electoral_system", id=fptp, polity_id=ca, rules={"formula": "fptp"}, rules_version=1)
        name(fptp, "First past the post")

        parties: dict[str, UUID] = {}
        for day in [*sorted(ELECTIONS, reverse=True), *BYELECTIONS]:
            for label, registered in pl.read_parquet(PARSED / f"parties-{day}.parquet").iter_rows():
                key = ALIASES.get(label.upper(), label.upper())
                if key in UNAFFILIATED or label in parties:
                    continue
                party = cs.key(f"{SOURCE}.party", key)
                if party not in parties.values() and (party in cs.minted or key in PARTIES):
                    full, short, color = PARTIES.get(key, (registered, None, None))
                    cs.add("party", id=party, kind="party", polity_id=ca)
                    if key in RENAMED:
                        former, renamed = RENAMED[key]
                        name(party, former, end=renamed)
                        name(party, full, start=renamed)
                    else:
                        name(party, full)
                    if short is not None:
                        name(party, short, "abbreviation")
                    elif label != full:
                        name(party, label, "ballot")
                    if color is not None:
                        cs.add("party_color", party_id=party, color=color)
                parties[label] = party

        persons: dict[str, UUID] = {}
        for day in [*ELECTIONS, *BYELECTIONS]:
            general = day in ELECTIONS
            kind = "general" if general else "by-election"
            vintage = (
                ELECTIONS[day][1]
                if general
                else next(
                    code for code, (start, end, *_) in ORDERS.items() if start <= day and (end is None or day < end)
                )
            )
            polls = (
                pl.read_parquet(PARSED / f"polls-{day}.parquet")
                .with_columns(clash=pl.col("party").n_unique().over("fed", "family", "middle", "first") > 1)
                .with_columns(
                    option=pl.concat_str(
                        "fed",
                        pl.lit(":"),
                        pl.col("family").fill_null(""),
                        pl.lit("|"),
                        pl.col("middle").fill_null(""),
                        pl.lit("|"),
                        "first",
                        pl.when("clash").then(pl.lit("|") + pl.col("party")).otherwise(pl.lit("")),
                    ),
                    number=pl.col("poll")
                    .str.split("-")
                    .list.first()
                    .str.replace_all(r"\D", "")
                    .cast(pl.Int32, strict=False)
                    .fill_null(0),
                )
                .with_columns(
                    mobile=pl.col("number").is_between(500, 599)
                    | pl.col("poll_name").str.contains(r"^(Mobile|Bureau itinérant)")
                )
            )
            feds = polls.select("fed", pl.col("fed_name").str.replace_all("--", "—")).unique(maintain_order=True)
            election = cs.key(f"{SOURCE}.election", day.isoformat())
            cs.add(
                "election",
                id=election,
                polity_id=ca,
                kind="general" if general else "by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            spoken = f"{day:%B} {day.day}, {day.year}"
            name(
                election,
                f"{day.year} Canadian federal election"
                if general
                else f"Canadian federal by-elections, {spoken}"
                if feds.height > 1
                else f"{feds['fed_name'][0]} federal by-election, {spoken}",
            )
            cs.add("slug", path=f"ca/{day}-{kind}", entity_id=election)

            unit_rows = polls.select("fed", "poll", "combined_with", "mobile").unique(maintain_order=True)
            units = {f"{fed}:{poll}": cs.unit("ca.pd", f"{fed}:{poll}", day) for fed, poll, *_ in unit_rows.iter_rows()}
            for fed, poll, host, mobile in unit_rows.iter_rows():
                cs.add(
                    "reporting_unit",
                    id=units[f"{fed}:{poll}"],
                    polity_id=ca,
                    scheme_code="ca.pd",
                    code=f"{fed}:{poll}",
                    counted_with_id=units.get(f"{fed}:{host}"),
                    area_relation="within",
                    area_id=fed_areas[vintage, fed],
                    kind="aggregate" if poll.startswith("S/R") else "mobile" if mobile else "standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )

            result_sets: dict[str, UUID] = {}
            for fed, fed_name in feds.iter_rows():
                contest = cs.key(f"{SOURCE}.contest", f"{day}:{fed}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=commons,
                    district_area_id=fed_areas[vintage, fed],
                    magnitude=1,
                    electoral_system_id=fptp,
                )
                name(contest, f"{day.year} {'election' if general else 'by-election'} in {fed_name}")
                cs.add("slug", path=f"ca/{day}-{kind}/{slug(fed_name)}", entity_id=contest)
                result_sets[fed] = cs.key(f"{SOURCE}.result_set", f"{day}:{fed}")
                cs.add(
                    "result_set",
                    id=result_sets[fed],
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    source_id=results[day],
                    status="active",
                    is_default=True,
                    completeness=1,
                )

            options: dict[str, UUID] = {}
            for candidate in (
                polls.select("fed", "option", "clash", "family", "middle", "first", "party", "incumbent", "elected")
                .unique(maintain_order=True)
                .iter_rows(named=True)
            ):
                display = " ".join(
                    part for part in (candidate["first"], candidate["middle"], candidate["family"]) if part
                )
                person = f"{vintage}:{candidate['fed']}:{display.lower()}"
                if candidate["clash"]:
                    person += f":{candidate['party'].lower()}"
                if person not in persons:
                    persons[person] = cs.key(f"{SOURCE}.person", person)
                    if persons[person] in cs.minted:
                        cs.add("person", id=persons[person])
                        name(persons[person], display)
                key = candidate["option"]
                party = parties.get(candidate["party"])
                options[key] = cs.key(f"{SOURCE}.option", f"{day}:{key}")
                cs.add(
                    "contest_option",
                    id=options[key],
                    contest_id=cs.key(f"{SOURCE}.contest", f"{day}:{candidate['fed']}"),
                    kind="candidate",
                    party_id=party,
                    ballot_label=display,
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{day}:{key}"),
                    person_id=persons[person],
                    option_id=options[key],
                    role="head",
                    ballot_name=display,
                    member_of_party_id=party,
                    is_independent=candidate["party"] == "Independent",
                    is_incumbent=candidate["incumbent"] == "Y",
                )
                if candidate["elected"] == "Y":
                    cs.add(
                        "outcome",
                        result_set_id=result_sets[candidate["fed"]],
                        option_id=options[key],
                        kind="elected",
                        seats=1,
                    )

            located = polls.filter(
                pl.col("fed").is_in([fed for fed, result_set in result_sets.items() if result_set in cs.minted])
            ).with_columns(
                result_set_id=pl.col("fed").replace_strict({fed: str(id) for fed, id in result_sets.items()}),
                unit_id=(pl.col("fed") + ":" + pl.col("poll")).replace_strict(
                    {code: str(id) for code, id in units.items()}
                ),
            )
            counted = located.filter(
                (pl.col("void") == "N") & (pl.col("no_poll") == "N") & pl.col("combined_with").is_null()
            ).with_columns(
                option_id=pl.col("option").replace_strict({key: str(id) for key, id in options.items()}),
                channel=pl.when(pl.col("poll").str.starts_with("S/R"))
                .then(9)
                .when("mobile")
                .then(6)
                .when(pl.col("number").is_between(600, 699))
                .then(2)
                .otherwise(1)
                .cast(pl.Int16),
                flags=pl.when(pl.col("poll").str.starts_with("S/R")).then(UNALLOCATED).otherwise(0).cast(pl.Int16),
            )
            registered = located.filter((pl.col("void") == "N") & (pl.col("electors") > 0)).unique(
                ["result_set_id", "unit_id"], maintain_order=True
            )
            by_poll = counted.unique(["result_set_id", "unit_id"], maintain_order=True)
            columns = ["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags", "value"]
            cs.frame(
                "result_value",
                pl.concat(
                    [
                        registered.select(
                            "result_set_id",
                            "unit_id",
                            option_id=pl.lit(None, pl.String),
                            channel=pl.lit(0, pl.Int16),
                            measure=pl.lit(2, pl.Int16),
                            count_round=pl.lit(0, pl.Int16),
                            flags=pl.lit(0, pl.Int16),
                            value=pl.col("electors"),
                        ),
                        by_poll.select(
                            "result_set_id",
                            "unit_id",
                            option_id=pl.lit(None, pl.String),
                            channel="channel",
                            measure=pl.lit(5, pl.Int16),
                            count_round=pl.lit(0, pl.Int16),
                            flags="flags",
                            value=pl.col("rejected"),
                        ),
                        counted.select(
                            "result_set_id",
                            "unit_id",
                            "option_id",
                            "channel",
                            measure=pl.lit(1, pl.Int16),
                            count_round=pl.lit(0, pl.Int16),
                            flags="flags",
                            value=pl.col("votes"),
                        ),
                    ]
                ).select(columns),
            )
        return cs.write()
