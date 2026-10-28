import html
import io
import json
import os
import re
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl
from PIL import Image

from ea import precinct_code, raw
from ea.changeset import Changeset
from ea.sources.us import (
    county_measures,
    elstats,
    la_sos,
    me_canvass,
    measures,
    medsl,
    medsl_measures,
    medsl_state,
    medsl_state_precincts,
    oe_precincts,
    openelections,
    swdb_precincts,
    wa_local,
)
from ea.store import DATA, connect

SOURCE = "us.measure_precincts"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
FIRST = 2000
LAST = 2025
ANSWERS = {"YES": "YES", "NO": "NO", "FOR": "YES", "AGAINST": "NO", "APPROVE": "YES", "REJECT": "NO"}
ARCHIVE = "Precinct rows of the state's election statistics archive, which add up to its county totals for each answer."
TOWNS = ("VT", "CT", "NH")
TOWN_ARCHIVE = (
    "City and town rows of the state's election statistics archive, which add up to its county totals for each "
    "answer. The state reports by municipality, so the units are whole towns rather than precincts."
)
WITHDRAWN = (
    "Withdrawn: this question's default totals were replaced by the state's certified results, and these precinct "
    "rows no longer add up to them."
)
MEDSL = (
    "Precinct rows of MEDSL's precinct returns, which compile each state's certified canvass. Each answer's precinct "
    "rows add up to the totals of this question's default results{counties}."
)

OPENELECTIONS = (
    "Precinct rows of OpenElections' volunteer-transcribed precinct files. Each answer's precinct rows add up to the "
    "totals of this question's default results{counties}."
)
LOUISIANA = (
    "Precinct rows of the Louisiana Secretary of State's official results for this question, one file per parish. "
    "Each answer's precinct rows add up to the totals of this question's default results{counties}."
)
WASHINGTON = (
    "Precinct rows of the Washington Secretary of State's statewide precinct results export. Each answer's precinct "
    "rows add up to the totals of this question's default results{counties}."
)
WA_COUNTIES = {
    "AD": 1,
    "AS": 3,
    "BE": 5,
    "CH": 7,
    "CM": 9,
    "CR": 11,
    "CU": 13,
    "CZ": 15,
    "DG": 17,
    "FE": 19,
    "FR": 21,
    "GA": 23,
    "GR": 25,
    "GY": 27,
    "IS": 29,
    "JE": 31,
    "KI": 33,
    "KP": 35,
    "KS": 37,
    "KT": 39,
    "LE": 41,
    "LI": 43,
    "MA": 45,
    "OK": 47,
    "PA": 49,
    "PE": 51,
    "PI": 53,
    "SJ": 55,
    "SK": 57,
    "SM": 59,
    "SN": 61,
    "SP": 63,
    "ST": 65,
    "TH": 67,
    "WK": 69,
    "WL": 71,
    "WM": 73,
    "WT": 75,
    "YA": 77,
}
MAINE = (
    "Town and ward rows of the Maine Secretary of State's referendum tabulation, from the Wayback Machine's copy. "
    "Maine reports by municipality, so most units here are whole towns rather than precincts. Each answer's rows add "
    "up to the totals of this question's default results{counties}."
)
ME_FILES = {
    "2012-11-06": ("20250324173053", "2012-13/refstate1112.xls"),
    "2013-11-05": ("20250320045244", "2012-13/bondresults.xlsx"),
    "2014-11-04": ("20250324114911", "2014-15/bondsgen.xlsx"),
    "2015-11-03": ("20250320022437", "2014-15/referendum.xlsx"),
    "2016-11-08": ("20250306110844", "2016/referendum16.xlsx"),
    "2017-06-13": ("20250320045124", "2017/results61317.xlsx"),
    "2017-11-07": ("20250320232336", "2017/results1117.xlsx"),
    "2018-06-12": ("20250404024137", "2018/referendum618.xlsx"),
    "2018-11-06": ("20250404054610", "2018/referendum11-6-18.xlsx"),
    "2019-11-05": ("20250324070340", "2019/refer1119.xlsx"),
    "2020-03-03": ("20250405141335", "2020/ref320.xlsx"),
    "2020-07-14": ("20250405141224", "2020/reftab720.xlsx"),
    "2021-11-02": ("20240405170851", "2021/ref1121.xlsx"),
    "2023-11-07": ("20250318204154", "2023/referendum%20election%20nov%202023.xlsx"),
}
ME_NAMES = {
    name: name[:3]
    for name in [
        "ANDROSCOGGIN",
        "AROOSTOOK",
        "CUMBERLAND",
        "FRANKLIN",
        "HANCOCK",
        "KENNEBEC",
        "KNOX",
        "LINCOLN",
        "OXFORD",
        "PENOBSCOT",
        "PISCATAQUIS",
        "SAGADAHOC",
        "SOMERSET",
        "WALDO",
        "WASHINGTON",
        "YORK",
    ]
}
ME_WAYBACK = "https://web.archive.org/web/{}id_/https://www.maine.gov/sos/cec/elec/results/{}"
ALABAMA = (
    "Precinct rows of the Alabama Secretary of State's precinct-level results, one file per county, with each "
    "county's absentee and provisional ballots as reported. Each answer's rows add up to the totals of this "
    "question's default results{counties}."
)
AL_FILES = {
    "2016-03-01": "2017-06/2016-Primary-PrecinctLevel.zip",
    "2016-11-08": "2017-06/2016-General-PrecinctLevel.zip",
    "2018-11-06": "2018-11/2018-Official-General-Precinct-Results.zip",
    "2020-03-03": "2020-06/2020%20Primary%20Precinct%20Results.zip",
    "2020-11-03": "2020-12/2020%20General%20Precinct%20Results.zip",
    "2024-03-05": "2024-04/2024%20Primary%20Precinct%20Results.ZIP",
}
AL_DATA = "https://www.sos.alabama.gov/sites/default/files/election-data/"
ME_COUNTIES = [
    "AND",
    "ARO",
    "CUM",
    "FRA",
    "HAN",
    "KEN",
    "KNO",
    "LIN",
    "OXF",
    "PEN",
    "PIS",
    "SAG",
    "SOM",
    "WAL",
    "WAS",
    "YOR",
]
MASSACHUSETTS = (
    "Precinct rows of the Secretary of the Commonwealth's election statistics archive for this question. Each "
    "answer's precinct rows add up to the totals of this question's default results{counties}."
)
OR_RECORDS = {
    date(2012, 11, 6): 5993047,
    date(2014, 11, 4): 5993052,
    date(2016, 11, 8): 6570960,
    date(2018, 1, 23): 7132821,
    date(2018, 11, 6): 7132820,
    date(2020, 11, 3): 7879432,
}
OR_2024 = {
    "Baker": 10575038,
    "Benton": 10575039,
    "Clackamas": 10575040,
    "Clatsop": 10575041,
    "Columbia": 10574984,
    "Coos": 10574985,
    "Crook": 10574986,
    "Curry": 10574987,
    "Deschutes": 10574988,
    "Douglas": 10574989,
    "Gilliam": 10574990,
    "Grant": 10574991,
    "Harney": 10574992,
    "Hood River": 10574993,
    "Jackson": 10574994,
    "Jefferson": 10574995,
    "Josephine": 10574996,
    "Klamath": 10574997,
    "Lake": 10574998,
    "Lane": 10574999,
    "Lincoln": 10575000,
    "Linn": 10575001,
    "Malheur": 10575002,
    "Marion": 10575003,
    "Morrow": 10575004,
    "Multnomah": 10575026,
    "Polk": 10575028,
    "Sherman": 10575029,
    "Tillamook": 10575030,
    "Umatilla": 10575031,
    "Union": 10575032,
    "Wallowa": 10575033,
    "Wasco": 10575034,
    "Washington": 10575036,
    "Yamhill": 10575037,
}
OR_DETAIL = "https://records.sos.state.or.us/ORSOSCMSearch/Search/SearchDetail.aspx?uri={}"
OR_PUBLISHER = "Oregon Secretary of State"
OR_TITLE = "County precinct results filed with the Secretary of State"
OR_LICENSE = "Public record"
OR_TURNED = ("Clackamas", "Union")
OREGON = (
    "Precinct rows of the counties' official precinct results, filed with the Oregon Secretary of State, one report "
    "per county. Reports with a text layer and spreadsheets are read directly; scanned reports are digitized in two "
    "passes, a transcription and a machine read of each value's cell, which must agree, and each value keeps its page "
    "and position. Each answer's precinct rows add up to the totals of this question's default results{counties}."
)
OR_2020_NOTE = " Harney County's report labels the state measures Question 13-107 to 13-110, after its county number."
OR_2020_110_NOTE = (
    " Hood River County's report also lists a county measure numbered Question 14-110; it is not Measure 110 and is "
    "left out."
)
OR_2024_NOTE = (
    " Each county's report is its own record. Wheeler County filed no precinct report, so its votes are one "
    "county-level row taken from its totals in the Secretary of State's certified abstract of votes."
)
CA_ELECTIONS = ("P12", "G12", "P14", "P16", "G16", "P18", "P20", "G20", "P24")
CA_SOV = swdb_precincts.SWDB + "{0}/state/state_{1}_sov_data_by_{1}_svprec.zip"
CA_TITLE = "Statement of Vote data by SOV precinct"
CALIFORNIA = (
    "Precinct rows of the Statewide Database's Statement of Vote data by SOV precinct, the counties' own reporting "
    "precincts, with their vote-by-mail precincts. Each answer's precinct rows add up to the totals of this question's "
    "default results{counties}. The Database's permission to redistribute its files is pending, so this set is held "
    "under that pending license."
)
MONO_2016 = (
    "https://web.archive.org/web/20170124201915id_/http://www.monocounty.ca.gov/sites/default/files/fileattachments/"
    "elections/page/227/official-statement_of_vote_book.pdf"
)
MONO_NOTE = (
    " The Database's Mono County rows trail the county's certified totals, so Mono's precincts come from the county's "
    "own statement of vote (page 16), its vote-by-mail rows coded with the Database's `A` suffix."
)
COMMITS = openelections.COMMITS | county_measures.COMMITS
FILE = re.compile(
    r"(?:.*/)?(\d{8})__([a-z]{2})__((?:general|primary|special|runoff)(?:__(?:general|primary|runoff))?)(__[a-z_]+)?__(precinct|town)\.csv"
)


def fetch() -> None:
    for election in CA_ELECTIONS:
        raw.fetch(SOURCE, CA_SOV.format(election, election.lower()), suffix=".zip")
    raw.fetch(SOURCE, MONO_2016, suffix=".pdf")
    measures.fetch()
    for uri in [*OR_RECORDS.values(), *OR_2024.values()]:
        url = OR_DETAIL.format(uri)
        try:
            record = raw.cached(SOURCE, url)
        except LookupError:
            with httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=900) as client:
                page = client.get(url).text
                fields = {
                    name: html.unescape(value)
                    for name, value in re.findall(
                        r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page
                    )
                }
                response = client.post(url, data=fields | {"ctl00$ContentPlaceHolder1$lnkDownload": "Download"})
                response.raise_for_status()
            media_type = response.headers.get("content-type", "application/zip").split(";")[0]
            record = raw.store(
                SOURCE,
                url,
                response.content,
                suffix={"application/pdf": ".pdf", "text/csv": ".csv"}.get(media_type, ".zip"),
                retrieved_at=datetime.now(UTC),
                media_type=media_type,
            )
        if record.path.suffix != ".zip":
            continue
        with zipfile.ZipFile(record.path) as archive:
            for member in archive.namelist():
                try:
                    raw.cached(SOURCE, f"{url}#{member}")
                except LookupError:
                    raw.store(
                        SOURCE,
                        f"{url}#{member}",
                        archive.read(member),
                        suffix=Path(member).suffix.lower(),
                        retrieved_at=record.retrieved_at,
                        media_type="application/pdf" if member.lower().endswith(".pdf") else "text/csv",
                    )
    with connect() as conn:
        dates = conn.execute(
            """select distinct substr(p.iso_code, 4), e.date_from from contest c
               join election e on e.id = c.election_id
               join polity p on p.id = e.polity_id and p.iso_code ~ '^US-..$'
               where c.kind = 'ballot_question' and e.date_from between %s and %s
                 and not exists (select 1 from result_set rs join result_value v on v.result_set_k = rs.k
                                 join reporting_unit u on u.k = v.unit_k
                                 where rs.contest_id = c.id and u.scheme_code = 'us.precinct')
               order by 1, 2""",
            [date(FIRST, 1, 1), date(LAST, 12, 31)],
        ).fetchall()
    for state, day in dates:
        if state == "WA" and wa_local.PRECINCTS.format(day=day) not in json.loads(
            (DATA / "raw" / wa_local.SOURCE / "index.json").read_text()
        ):
            try:
                raw.fetch(SOURCE, wa_local.PRECINCTS.format(day=day), suffix=".csv")
            except httpx.HTTPStatusError:
                print(f"{day}: no Washington precinct export")
        if state == "AL" and f"{day}" in AL_FILES:
            raw.fetch(SOURCE, AL_DATA + AL_FILES[f"{day}"], suffix=".zip")
        if state == "ME" and f"{day}" in ME_FILES:
            stamp, path = ME_FILES[f"{day}"]
            raw.fetch(SOURCE, ME_WAYBACK.format(stamp, path), suffix="." + path.rsplit(".", 1)[1])
        if state == "MA":
            for id, year, *_ in measures.MA_ROW.findall(raw.cached(measures.SOURCE, measures.MA_LIST).path.read_text()):
                if int(year) == day.year:
                    raw.fetch(
                        SOURCE,
                        measures.MA_DOWNLOAD.format(id=id).replace("precincts_include:0", "precincts_include:1"),
                        suffix=".csv",
                    )
        if state != "LA":
            continue
        races = json.loads(
            raw.fetch(SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm"), suffix=".json").path.read_text()
        )
        links = [
            la_sos.blob(day, f"VotesRaceByPrecinct/Votes_{race['ID']}_{parish}.htm")
            for race in la_sos.many((races.get("Races") or {}).get("Race"))
            if race["GeneralTitle"] == "Constitutional Amendment" and race.get("IsMultiParish") == "1"
            for parish in la_sos.PARISHES
        ]
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda link: raw.fetch(SOURCE, link, suffix=".json"), links))
    trees: dict[str, list[str]] = {}
    for state, day in dates:
        if state not in COMMITS or day.year < (2012 if state == "RI" else 2015):
            continue
        if state not in trees:
            tree = raw.fetch(
                SOURCE, openelections.TREE.format(state=state.lower(), commit=COMMITS[state]), suffix=".json"
            )
            trees[state] = [
                entry["path"] for entry in json.loads(tree.path.read_text())["tree"] if entry["type"] == "blob"
            ]
        found = [
            (path, matched)
            for path in trees[state]
            if (matched := FILE.fullmatch(path))
            and matched[1] == f"{day:%Y%m%d}"
            and "diff/" not in path
            and matched[5] == ("town" if state == "RI" and day.year < 2018 else "precinct")
        ]
        whole = [path for path, matched in found if matched[4] is None]
        for path in whole or [path for path, _ in found]:
            raw.fetch(
                SOURCE,
                openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path),
                suffix=".csv",
            )


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, tuple[str, str]]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            city = code[:2] == "51" and int(code[2:]) >= 500
            if int(code[2:]) >= 500:
                names[f"{elstats.plain(text)}city"] = (code, f"{text.upper()} CITY" if city else text.upper())
            names.setdefault(elstats.plain(text), (code, f"{text.upper()} CITY" if city else text.upper()))
    frames = []
    for state, district in measures.STATEWIDE.items():
        frame = (
            pl.scan_csv(
                raw.cached(measures.SOURCE, measures.url(elstats.TENANTS[state][0], district)).path,
                infer_schema=False,
            )
            .with_row_index("row")
            .filter(
                pl.col("retention_candidate_id").is_null(),
                pl.col("district_type") == "State",
                pl.col("election_date").str.slice(0, 4).cast(pl.Int32).is_between(FIRST, LAST),
            )
            .with_columns(
                top=pl.when(pl.col("division_type").is_in(["County", "Locality"])).then(
                    pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
                )
            )
            .with_columns(county=pl.col("top").forward_fill().over("contest_id", order_by="row"))
            .filter(
                pl.col("division_type") == "Precinct",
                pl.col("candidate_name").str.to_uppercase().str.strip_chars().is_in(["YES", "NO"]),
            )
            .collect()
        )
        if frame.height == 0:
            continue
        plain = (
            pl.col("county")
            .str.to_lowercase()
            .str.replace_all(r"saint ", "st ")
            .str.replace_all("&", "and")
            .str.replace_all(r"\b(county|parish|borough)\b", "")
            .str.replace_all(r"[^a-z]", "")
        )
        frames.append(
            frame.select(
                state=pl.lit(state),
                question=pl.col("contest_id"),
                printed=pl.col("election_date").str.slice(0, 10).str.to_date(),
                fips=plain.replace_strict({k: v[0] for k, v in counties[state].items()}, default=None),
                jurisdiction=plain.replace_strict({k: v[1] for k, v in counties[state].items()}, default=None),
                division=pl.col("division_id"),
                precinct=pl.col("division_name").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
                answer=pl.col("candidate_name").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in([*TOWNS, "RI"]))
    places: dict[str, dict[str, str]] = {}
    for state, geoid, name in gazetteer.select("USPS", "GEOID", "NAME").iter_rows():
        places.setdefault(state, {}).setdefault(elstats.plain(name), geoid[:5])
        places[state].setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    places["VT"]["essexjunction"] = "50007"
    places["NH"] |= {"hartslocation": "33003", "wentworthslocation": "33007", "wentworth": "33009"}
    for state in TOWNS:
        frames = [table for table in frames if table["state"][0] != state]
        frames.append(
            pl.read_csv(
                raw.cached(measures.SOURCE, measures.url(elstats.TENANTS[state][0], measures.STATEWIDE[state])).path,
                infer_schema=False,
            )
            .filter(
                pl.col("retention_candidate_id").is_null(),
                pl.col("district_type") == "State",
                pl.col("election_date").str.slice(0, 4).cast(pl.Int32).is_between(FIRST, LAST),
                pl.col("division_type") == "City/Town",
                pl.col("candidate_name").str.to_uppercase().str.strip_chars().is_in(["YES", "NO"]),
            )
            .select(
                state=pl.lit(state),
                question=pl.col("contest_id"),
                printed=pl.col("election_date").str.slice(0, 10).str.to_date(),
                fips=pl.col("division_name")
                .map_elements(elstats.plain, return_dtype=pl.String)
                .replace_strict(places[state], default=None),
                jurisdiction=pl.lit(""),
                division=pl.col("division_id"),
                precinct=pl.col("division_name").str.replace_all(r"\s+", " ").str.strip_chars().str.to_uppercase(),
                answer=pl.col("candidate_name").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = pl.concat(frames).with_columns(
        day=pl.struct("state", "printed").map_elements(
            lambda row: measures.DATES.get((row["state"], row["printed"]), measures.election_day(row["printed"])),
            return_dtype=pl.Date,
        )
    )
    shared = (
        frame.group_by("state", "question", "fips", "precinct")
        .agg(pl.col("division").n_unique())
        .filter(pl.col("division") > 1)
    )
    if shared.height:
        print(f"{shared.height} precinct names shared by several divisions in a county, summed")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.group_by("state", "question", "day", "fips", "jurisdiction", "precinct", "answer")
        .agg(pl.col("votes").sum())
        .sort("state", "question", "fips", "precinct", "answer")
        .write_parquet(PARSED / "archives.parquet")
    )

    affirmative = pl.col("candidate").str.contains(medsl_measures.AFFIRMATIVE.pattern)
    negative = pl.col("candidate").str.contains(medsl_measures.NEGATIVE.pattern)
    frames = []
    for year, files in medsl_state_precincts.FILES.items():
        for source, url in files:
            path = raw.cached(source, url).path
            scan = pl.scan_csv(
                path,
                separator="\t" if "\t" in path.open(errors="replace").readline() else ",",
                infer_schema_length=0,
                encoding="utf8-lossy",
            )
            if year == 2016:
                scan = scan.rename(
                    {"state_postal": "state_po", "jurisdiction": "jurisdiction_name"}, strict=False
                ).with_columns(date=pl.lit("2016-11-08"))
            columns = scan.collect_schema().names()
            fips = pl.col("county_fips").str.replace(r"\.0$", "")
            frames.append(
                scan.with_columns(
                    pl.col("precinct", "mode").fill_null(""),
                    candidate=pl.col("candidate").fill_null("").str.to_uppercase().str.strip_chars(),
                    office=pl.col("office").fill_null("").str.to_uppercase().str.strip_chars(),
                    district=(pl.col("district") if "district" in columns else pl.lit(None, pl.String))
                    .fill_null("")
                    .str.to_uppercase(),
                    stage=(pl.col("stage") if "stage" in columns else pl.lit("GEN"))
                    .fill_null("GEN")
                    .str.to_uppercase(),
                )
                .filter(
                    pl.col("stage") == "GEN",
                    ~pl.col("office").str.contains("RETAIN|RETENTION|JUDGE|JUSTICE|COURT"),
                    ~pl.col("precinct").str.to_uppercase().str.contains(r"TOTALS$|^COUNTY TOTAL$|- TOTAL$"),
                    ~(
                        (pl.col("precinct").str.to_uppercase() == "TOTAL")
                        & (pl.col("mode").str.to_uppercase() == "TOTAL")
                    ),
                )
                .filter(
                    affirmative.any().over("state_po", "office", "district")
                    & negative.any().over("state_po", "office", "district"),
                    affirmative | negative,
                )
                .select(
                    year=pl.lit(year),
                    day=pl.col("date").replace({"11/8/22": "2022-11-08"}).str.to_date(),
                    state="state_po",
                    office="office",
                    district="district",
                    fips=pl.when(fips.str.contains(r"^\d{4,7}$")).then(fips.str.zfill(5).replace(medsl.RECODED)),
                    jurisdiction=pl.col("jurisdiction_name").fill_null("").str.to_uppercase().str.strip_chars(),
                    precinct=pl.col("precinct").str.strip_chars(),
                    mode=pl.col("mode").str.to_uppercase(),
                    answer=pl.when(affirmative).then(pl.lit("YES")).otherwise(pl.lit("NO")),
                    votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
                )
                .collect()
            )
    precinct = ["year", "state", "office", "district", "fips", "jurisdiction", "precinct", "answer"]
    frame = pl.concat(frames)
    (
        frame.filter((pl.col("mode") == "TOTAL").any().over(precinct).not_() | (pl.col("mode") == "TOTAL"))
        .group_by("year", "day", *precinct[1:])
        .agg(pl.col("votes").sum())
        .sort(precinct)
        .write_parquet(PARSED / "medsl.parquet")
    )

    candidate = pl.col("candidate").str.to_uppercase().str.strip_chars()
    affirmative = candidate.str.contains(medsl_measures.AFFIRMATIVE.pattern + r"|^IN FAVOR\b")
    negative = candidate.str.contains(medsl_measures.NEGATIVE.pattern)
    (
        pl.scan_parquet(oe_precincts.PARSED / "lines.parquet")
        .with_columns(
            office=pl.col("office").str.to_uppercase().str.replace_all(r"[^A-Z0-9]+", " ").str.strip_chars(),
            district=pl.col("district").fill_null("").str.to_uppercase(),
        )
        .filter(
            ~pl.col("office").str.contains("RETAIN|RETENTION|JUDGE|JUSTICE|COURT"),
            affirmative.any().over("state", "year", "office", "district")
            & negative.any().over("state", "year", "office", "district"),
            affirmative | negative,
        )
        .group_by(
            "year",
            "state",
            "office",
            "district",
            "fips",
            pl.lit("").alias("jurisdiction"),
            "precinct",
            answer=pl.when(affirmative).then(pl.lit("YES")).otherwise(pl.lit("NO")),
        )
        .agg(pl.col("votes").sum())
        .sort("year", "state", "office", "district", "fips", "precinct", "answer")
        .collect()
        .write_parquet(PARSED / "openelections.parquet")
    )

    with connect() as conn:
        fips = openelections.counties(conn)
    for (state, typo), plain in oe_precincts.COUNTIES.items():
        if plain in fips.get(state, {}):
            fips[state][typo] = fips[state][plain]
    index = json.loads((DATA / "raw" / SOURCE / "index.json").read_text())
    frames = []
    for url in index:
        matched = FILE.fullmatch(url.rsplit("/", 1)[1])
        if not url.startswith("https://raw.githubusercontent.com/openelections/") or matched is None:
            continue
        state, day = matched[2].upper(), date(int(matched[1][:4]), int(matched[1][4:6]), int(matched[1][6:]))
        if matched[5] == "town" and state != "RI":
            continue
        text = raw.cached(SOURCE, url).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            name = oe_precincts.COLUMNS.get(column.strip().lower(), column.strip().lower())
            if name not in renamed.values():
                renamed[column] = name
        frame = frame.rename(renamed)
        if "votes" not in frame.columns and "total" in frame.columns:
            frame = frame.rename({"total": "votes"})
        for column in ("vote_type", "votetype"):
            if column in frame.columns:
                frame = frame.filter(pl.col(column).fill_null("").str.to_lowercase() != "total")
        if "votes" not in frame.columns and any(column.endswith("_votes") for column in frame.columns):
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in frame.columns
                    if column.endswith("_votes")
                ).cast(pl.String)
            )
        if matched[5] == "town":
            frame = frame.rename({"town": "county"}, strict=False)
            frame = frame.with_columns(precinct=pl.col("county"))
        if "county" not in frame.columns and matched[4]:
            frame = frame.with_columns(county=pl.lit(matched[4].strip("_").replace("_", " ")))
        if not {"county", "precinct", "office", "candidate", "votes"} <= set(frame.columns):
            print(f"{url}: columns {frame.columns}, skipped")
            continue
        frames.append(
            frame.select(
                state=pl.lit(state),
                day=pl.lit(day),
                fips=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(places["RI"] if state == "RI" else fips.get(state, {}), default=None),
                precinct=pl.col("precinct").fill_null("").str.strip_chars().str.replace_all(r"\s+", " "),
                office=pl.col("office")
                .fill_null("")
                .str.to_uppercase()
                .str.replace_all(r"[^A-Z0-9]+", " ")
                .str.strip_chars(),
                district=(pl.col("district") if "district" in frame.columns else pl.lit(None, pl.String))
                .fill_null("")
                .str.to_uppercase(),
                candidate=pl.col("candidate").fill_null("").str.replace(r"(?i)^question \d+\s+", ""),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    (
        pl.concat(frames)
        .filter(
            pl.col("votes").is_not_null(),
            pl.col("precinct") != "",
            ~pl.col("precinct").str.contains(oe_precincts.TOTALS),
            ~pl.col("office").str.contains("RETAIN|RETENTION|JUDGE|JUSTICE|COURT"),
            affirmative.any().over("state", "day", "office", "district")
            & negative.any().over("state", "day", "office", "district"),
            affirmative | negative,
        )
        .group_by(
            pl.col("day").dt.year().alias("year"),
            "day",
            "state",
            "office",
            "district",
            "fips",
            pl.lit("").alias("jurisdiction"),
            "precinct",
            answer=pl.when(affirmative).then(pl.lit("YES")).otherwise(pl.lit("NO")),
        )
        .agg(pl.col("votes").sum())
        .sort("day", "state", "office", "district", "fips", "precinct", "answer")
        .write_parquet(PARSED / "openelections_recent.parquet")
    )

    rows = []
    for url in index:
        found = re.fullmatch(re.escape(la_sos.DATA_URL) + r"(\d{8})/VotesRaceByPrecinct/Votes_(\d+)_(\d{2})\.htm", url)
        if found is None:
            continue
        day = date(int(found[1][:4]), int(found[1][4:6]), int(found[1][6:]))
        races = json.loads(raw.cached(SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")).path.read_text())
        race = next(race for race in la_sos.many(races["Races"]["Race"]) if race["ID"] == found[2])
        answers = {choice["ID"]: ANSWERS.get(choice["Desc"].strip().upper()) for choice in la_sos.many(race["Choice"])}
        listed = json.loads(raw.cached(SOURCE, url).path.read_text())
        for precinct in la_sos.many((listed.get("Precincts") or {}).get("Precinct")):
            for choice in la_sos.many(precinct.get("Choice")):
                rows.append(
                    {
                        "year": day.year,
                        "day": day,
                        "state": "LA",
                        "office": race["SpecificTitle"],
                        "district": None,
                        "fips": f"22{2 * int(found[3]) - 1:03}",
                        "jurisdiction": "",
                        "precinct": precinct["Precinct"].strip(),
                        "answer": answers[choice["ID"]],
                        "votes": int(choice["VoteTotal"]),
                    }
                )
    washington = json.loads((DATA / "raw" / wa_local.SOURCE / "index.json").read_text())
    frames = []
    for url in [*index, *washington]:
        found = re.fullmatch(r"https://results\.vote\.wa\.gov/results/(\d{8})/export/\d{8}_allstateprecincts\.csv", url)
        if found is None:
            continue
        day = date(int(found[1][:4]), int(found[1][4:6]), int(found[1][6:]))
        frames.append(
            pl.read_csv(
                raw.cached(SOURCE if url in index else wa_local.SOURCE, url).path,
                infer_schema=False,
                encoding="utf8-lossy",
            ).select(
                pl.lit(day.year, pl.Int32).alias("year"),
                pl.lit(day).alias("day"),
                pl.lit("WA").alias("state"),
                pl.col("Race").str.replace_all(r"\s+", " ").str.strip_chars().alias("office"),
                pl.lit(None, pl.String).alias("district"),
                pl.col("CountyCode")
                .replace_strict({code: f"53{number:03}" for code, number in WA_COUNTIES.items()}, default=None)
                .alias("fips"),
                pl.lit("").alias("jurisdiction"),
                pl.col("PrecinctName").str.strip_chars().alias("precinct"),
                pl.col("Candidate")
                .str.strip_chars()
                .str.to_uppercase()
                .replace_strict(
                    {
                        "YES": "YES",
                        "APPROVED": "YES",
                        "REPEALED": "YES",
                        "NO": "NO",
                        "REJECTED": "NO",
                        "MAINTAINED": "NO",
                    },
                    default=None,
                )
                .alias("answer"),
                pl.col("Votes").cast(pl.Int64, strict=False).alias("votes"),
            )
        )
    pl.concat(frames).filter(
        pl.col("precinct").str.to_uppercase() != "TOTAL",
        pl.col("answer").is_not_null(),
        pl.col("answer").n_unique().over("day", "office") == 2,
        pl.col("fips").n_unique().over("day", "office") == len(WA_COUNTIES),
    ).group_by("year", "day", "state", "office", "district", "fips", "jurisdiction", "precinct", "answer").agg(
        pl.col("votes").sum()
    ).sort("day", "office", "fips", "precinct", "answer").write_parquet(PARSED / "washington.parquet")

    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "MA")
    places: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        places.setdefault(elstats.plain(name), geoid[:5])
        places.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    frames = []
    for id, year, *_ in measures.MA_ROW.findall(raw.cached(measures.SOURCE, measures.MA_LIST).path.read_text()):
        url = measures.MA_DOWNLOAD.format(id=id).replace("precincts_include:0", "precincts_include:1")
        if url not in index:
            continue
        table = pl.read_csv(raw.cached(SOURCE, url).path, infer_schema=False)
        town = (
            pl.col("Locality")
            .str.replace(r"^N\. ", "North ")
            .str.replace(r"^S\. ", "South ")
            .str.replace(r"^E\. ", "East ")
            .str.replace(r"^W\. ", "West ")
        )
        frames.append(
            table.filter(pl.col("Locality").is_not_null(), pl.col("Locality") != "TOTALS")
            .unpivot(["Yes", "No"], index=["Locality", "Ward", "Pct"])
            .select(
                year=pl.lit(int(year), pl.Int32),
                day=pl.lit(None, pl.Date),
                state=pl.lit("MA"),
                office=pl.lit(f"ma:{id}"),
                district=pl.lit(None, pl.String),
                fips=town.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(places, default=None),
                jurisdiction=pl.lit(""),
                precinct=pl.concat_str(
                    pl.col("Locality"),
                    pl.when(pl.col("Ward") != "-").then(pl.lit(" Ward ") + pl.col("Ward")).otherwise(pl.lit("")),
                    pl.lit(" Precinct "),
                    pl.col("Pct"),
                ),
                answer=pl.col("variable").str.to_uppercase(),
                votes=pl.col("value").str.replace_all(",", "").cast(pl.Int64),
            )
        )
    pl.concat(frames).write_parquet(PARSED / "massachusetts.parquet")

    tallies = []
    towns: dict[str, str] = {}
    for held, (stamp, path) in sorted(ME_FILES.items(), key=lambda item: item[1][1].startswith(("2012-13", "2014-15"))):
        day = date.fromisoformat(held)
        reader = fastexcel.read_excel(raw.cached(SOURCE, ME_WAYBACK.format(stamp, path)).path.read_bytes())
        sheet = next(
            frame
            for name in reader.sheet_names
            if (frame := reader.load_sheet(name, header_row=None).to_polars()).height >= 100
        )
        cells = [[str(cell).strip().upper() if cell is not None else "" for cell in row] for row in sheet.iter_rows()]
        header = next(number for number, row in enumerate(cells) if "YES" in row)
        labels = [cells[header][column] or cells[header - 1][column] for column in range(len(cells[header]))]
        town = next(column for column, label in enumerate(labels) if label in ("MUNICIPALITY", "TOWN"))
        county = next((column for column, label in enumerate(labels) if label in ("CTY", "COUNTY")), None)
        ward = next((column for column, label in enumerate(labels) if label == "W-P"), None)
        pairs = [
            (column, next(other for other in range(column + 1, len(labels)) if labels[other] == "NO"))
            for column, label in enumerate(labels)
            if label == "YES"
        ]
        sections: list[str | None] = []
        pending = None
        for row in reversed(cells):
            for cell in row[: town + 2]:
                words = cell.replace(":", "").split()
                named = [word for word in words if word in ME_NAMES]
                if "TOTAL" in cell and named and ("COUNTY" in words or words[0] in ME_NAMES):
                    pending = ME_NAMES[named[0]]
            sections.append(pending)
        sections.reverse()
        current = None
        for row, section in zip(cells[header + 1 :], sections[header + 1 :], strict=True):
            if county is not None and row[county]:
                current = row[county][:3]
            overseas = any("UOCAVA" in cell and "INCLUDES" not in cell for cell in row[: town + 2])
            emergency = "EMERGENCY" in row[town]
            if not overseas and (not row[town] or any("TOTAL" in cell for cell in row[: town + 2])):
                continue
            if county is not None:
                towns.setdefault(row[town], current or "")
            code = current if county is not None else towns.get(row[town], section)
            for number, (yes, no) in enumerate(pairs, 1):
                for answer, column in (("YES", yes), ("NO", no)):
                    tallies.append(
                        {
                            "year": day.year,
                            "day": day,
                            "state": "ME",
                            "office": f"QUESTION {number}",
                            "district": None,
                            "fips": "ME:uocava"
                            if overseas
                            else "ME:emergency"
                            if emergency
                            else f"23{2 * ME_COUNTIES.index(code) + 1:03}"
                            if code in ME_COUNTIES
                            else None,
                            "jurisdiction": "",
                            "precinct": "UOCAVA"
                            if overseas
                            else " ".join(part for part in (row[town], row[ward] if ward is not None else "") if part),
                            "answer": answer,
                            "votes": int(float(row[column] or 0)),
                        }
                    )
    pl.DataFrame(
        tallies, schema_overrides={"year": pl.Int32, "district": pl.String}, infer_schema_length=None
    ).group_by("year", "day", "state", "office", "district", "fips", "jurisdiction", "precinct", "answer").agg(
        pl.col("votes").sum()
    ).sort("day", "office", "fips", "precinct", "answer").write_parquet(PARSED / "maine.parquet")

    tallies = []
    for held, path in AL_FILES.items():
        day = date.fromisoformat(held)
        with zipfile.ZipFile(raw.cached(SOURCE, AL_DATA + path).path) as archive:
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                county = counties["AL"].get(
                    elstats.plain(re.sub(r"^\d{4}-\w+-", "", name.rsplit("/", 1)[-1].rsplit(".", 1)[0]))
                )
                fips = county[0] if county else None
                try:
                    reader = fastexcel.read_excel(archive.read(name))
                except fastexcel.CalamineError:
                    print(f"{held} {name}: not a spreadsheet the reader opens, skipped")
                    continue
                sheet = reader.load_sheet(0).to_polars()
                if "Contest Title" not in sheet.columns:
                    print(f"{held} {name}: no Contest Title column, skipped")
                    continue
                polls = [column for column in sheet.columns[3:] if column]
                for title, candidate, *counts in sheet.select("Contest Title", "Candidate", *polls).iter_rows():
                    answer = re.match(r"\s*(yes|no)\b", candidate or "", re.IGNORECASE)
                    if answer is None:
                        continue
                    for place, count in zip(polls, counts, strict=True):
                        tallies.append((day, title.strip(), fips, place.strip(), answer[1].upper(), count))
    pl.DataFrame(
        tallies, schema=["day", "office", "fips", "precinct", "answer", "votes"], orient="row", infer_schema_length=None
    ).select(
        pl.col("day").dt.year().cast(pl.Int32).alias("year"),
        "day",
        pl.lit("AL").alias("state"),
        "office",
        pl.lit(None, pl.String).alias("district"),
        "fips",
        pl.lit("").alias("jurisdiction"),
        "precinct",
        "answer",
        pl.col("votes").cast(pl.String).str.replace(r"\.0$", "").cast(pl.Int64, strict=False),
    ).group_by("year", "day", "state", "office", "district", "fips", "jurisdiction", "precinct", "answer").agg(
        pl.col("votes").sum()
    ).sort("day", "office", "fips", "precinct", "answer").write_parquet(PARSED / "alabama.parquet")

    pl.DataFrame(rows, schema_overrides={"year": pl.Int32, "district": pl.String}).sort(
        "day", "office", "fips", "precinct", "answer"
    ).write_parquet(PARSED / "louisiana.parquet")

    frames = []
    for election in CA_ELECTIONS:
        with zipfile.ZipFile(raw.cached(SOURCE, CA_SOV.format(election, election.lower())).path) as archive:
            table = pl.read_csv(
                io.BytesIO(archive.read(next(name for name in archive.namelist() if name.endswith(".csv")))),
                infer_schema=False,
            )
        day = swdb_precincts.DAYS[election]
        frames.append(
            table.filter(~pl.col("SVPREC").str.contains("TOT"), ~pl.col("SVPREC").str.starts_with("SOV_"))
            .unpivot(
                [column for column in table.columns if re.fullmatch(r"PR_\w+_[YN]", column)],
                index=["FIPS", "SVPREC"],
                variable_name="column",
                value_name="votes",
            )
            .select(
                year=pl.lit(day.year, pl.Int32),
                day=pl.lit(day),
                state=pl.lit("CA"),
                office=pl.concat_str(pl.lit("PROPOSITION "), pl.col("column").str.extract(r"^PR_0*(\w+)_[YN]$")),
                district=pl.lit(None, pl.String),
                fips="FIPS",
                jurisdiction=pl.lit(""),
                precinct="SVPREC",
                answer=pl.when(pl.col("column").str.ends_with("_Y")).then(pl.lit("YES")).otherwise(pl.lit("NO")),
                votes=pl.col("votes").cast(pl.Int64, strict=False),
            )
            .filter(pl.col("votes").is_not_null().all().over("office"))
        )
    text = subprocess.run(
        ["pdftotext", "-f", "16", "-l", "16", "-layout", str(raw.cached(SOURCE, MONO_2016).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    mono = [
        (number + ("A" if "- Vot" in line else ""), answer, int(votes))
        for line in text.splitlines()
        if (found := re.match(r"^(\d\d) .*?\s+\d+\s+\d+\s+[\d.]+\s+(\d+)\s+(\d+)$", line))
        for number, yes, no in [found.groups()]
        for answer, votes in (("YES", yes), ("NO", no))
    ]
    day = swdb_precincts.DAYS["P16"]
    replaced = (pl.col("day") == day) & (pl.col("fips") == "06051") & (pl.col("office") == "PROPOSITION 50")
    pl.concat(
        [
            pl.concat(frames).filter(~replaced),
            pl.DataFrame(mono, schema=["precinct", "answer", "votes"], orient="row").select(
                year=pl.lit(day.year, pl.Int32),
                day=pl.lit(day),
                state=pl.lit("CA"),
                office=pl.lit("PROPOSITION 50"),
                district=pl.lit(None, pl.String),
                fips=pl.lit("06051"),
                jurisdiction=pl.lit(""),
                precinct="precinct",
                answer="answer",
                votes=pl.col("votes").cast(pl.Int64),
            ),
        ]
    ).sort("day", "office", "fips", "precinct", "answer").write_parquet(PARSED / "california.parquet")

    pl.DataFrame(
        [
            (day.year, day, "OR", f"MEASURE {measure}", *counties["OR"][elstats.plain(county)], precinct, answer, votes)
            + (
                file
                if day.year == 2024
                else f"{OR_DETAIL.format(OR_RECORDS[day])}#{day:%m.%d.%y}/{county}.{'csv' if page is None else 'pdf'}",
                page,
                *(box or (None, None, None, None)),
                8 if digitized and page is not None else 0,
            )
            for day in (date(2020, 11, 3), date(2024, 11, 5))
            for county, measure, precinct, yes, no, *cells, digitized in json.loads(
                (PARSED / "model" / f"or-{day.year}.json").read_text()
            )
            for file in [OR_DETAIL.format(OR_2024[county]) if county in OR_2024 else None]
            for answer, votes, page, box in (("YES", yes, cells[0], cells[1]), ("NO", no, cells[2], cells[3]))
        ],
        schema={
            "year": pl.Int32,
            "day": pl.Date,
            "state": pl.String,
            "office": pl.String,
            "fips": pl.String,
            "jurisdiction": pl.String,
            "precinct": pl.String,
            "answer": pl.String,
            "votes": pl.Int64,
            "file": pl.String,
            "page": pl.Int32,
            "x0": pl.Float64,
            "y0": pl.Float64,
            "x1": pl.Float64,
            "y1": pl.Float64,
            "flags": pl.Int16,
        },
        orient="row",
    ).with_columns(district=pl.lit(None, pl.String)).write_parquet(PARSED / "oregon.parquet")


def emit() -> Path:
    archives = pl.read_parquet(PARSED / "archives.parquet").with_columns(
        key=pl.concat_str(pl.col("day").cast(pl.String), "state", pl.lit("question"), "question", separator=":")
    )
    returns = pl.concat(
        [
            pl.read_parquet(PARSED / "medsl.parquet").with_columns(origin=pl.lit("medsl")),
            pl.read_parquet(PARSED / "openelections.parquet").with_columns(origin=pl.lit("openelections")),
            pl.read_parquet(PARSED / "openelections_recent.parquet").with_columns(origin=pl.lit("openelections")),
            pl.read_parquet(PARSED / "louisiana.parquet").with_columns(origin=pl.lit("louisiana")),
            pl.read_parquet(PARSED / "washington.parquet").with_columns(origin=pl.lit("washington")),
            pl.read_parquet(PARSED / "maine.parquet").with_columns(origin=pl.lit("maine")),
            pl.read_parquet(PARSED / "massachusetts.parquet").with_columns(origin=pl.lit("massachusetts")),
            pl.read_parquet(PARSED / "alabama.parquet").with_columns(origin=pl.lit("alabama")),
            pl.read_parquet(PARSED / "california.parquet").with_columns(origin=pl.lit("california")),
            pl.read_parquet(PARSED / "oregon.parquet").with_columns(origin=pl.lit("oregon")),
        ],
        how="diagonal_relaxed",
    )
    returns = returns.filter(~((pl.col("origin") == "medsl") & (pl.col("state") == "OR") & (pl.col("year") == 2024)))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions by precinct 2000–2025 from the states' election statistics archives "
            "and MEDSL's and OpenElections' precinct returns",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and rs.changeset_id not in
                       (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        states = {
            iso.removeprefix("US-"): polity
            for iso, polity in conn.execute("select iso_code, id from polity where iso_code like 'US-%%'").fetchall()
        }
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.state', 'us.county', 'us.place')"
            ).fetchall()
        }
        aggregates: dict[str, UUID] = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.aggregate'").fetchall()
        )
        elections = {
            id: election
            for id, election in conn.execute(
                """select rs.id, rs.election_id::text from result_set rs
                   join external_id x on x.entity_id = rs.id and x.scheme = %s""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        }
        defaults = pl.DataFrame(
            conn.execute(
                """select cx.value, substr(p.iso_code, 4), e.date_from, rs.election_id::text, rs.contest_id::text,
                          rs.source_id::text, upper(o.ballot_label), o.id::text, u.scheme_code, u.code,
                          v.value::bigint
                   from contest c
                   join election e on e.id = c.election_id
                   join polity p on p.id = e.polity_id and p.iso_code ~ '^US-..$'
                   join external_id cx on cx.entity_id = c.id and cx.scheme = 'us.contest'
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   join contest_option o on o.k = v.option_k
                   join reporting_unit u on u.k = v.unit_k
                   where c.kind = 'ballot_question' and e.date_from between %s and %s""",
                [date(FIRST, 1, 1), date(LAST, 12, 31)],
            ).fetchall(),
            schema=[
                "key",
                "state",
                "day",
                "election",
                "contest",
                "source",
                "answer",
                "option",
                "scheme",
                "code",
                "total",
            ],
            orient="row",
        ).with_columns(answer=pl.col("answer").replace_strict(ANSWERS, default=None))
        totals = (
            defaults.group_by("key", "state", "day")
            .agg(
                yes=pl.col("total").filter(pl.col("answer") == "YES").sum(),
                no=pl.col("total").filter(pl.col("answer") == "NO").sum(),
            )
            .filter((pl.col("yes") > 0) & (pl.col("no") > 0))
        )
        sources: dict[tuple[str, str | int], tuple[UUID, str]] = (
            {
                ("medsl", year): (cs.source(medsl_state.PUBLISHER, title), MEDSL)
                for year, (title, _) in medsl_state_precincts.TITLES.items()
            }
            | {
                ("openelections", state): (cs.source("OpenElections", f"{state} precinct-level results"), OPENELECTIONS)
                for state in returns.filter(pl.col("origin") == "openelections")["state"].unique().to_list()
            }
            | {
                ("louisiana", "LA"): (cs.source(la_sos.PUBLISHER, la_sos.TITLE), LOUISIANA),
                ("washington", "WA"): (cs.source(wa_local.PUBLISHER, wa_local.TITLE), WASHINGTON),
                ("maine", "ME"): (cs.source(me_canvass.PUBLISHER, me_canvass.TITLE), MAINE),
                ("massachusetts", "MA"): (cs.source(elstats.MA_PUBLISHER, elstats.TITLE), MASSACHUSETTS),
                ("alabama", "AL"): (
                    cs.source("Alabama Secretary of State", "Election data downloads"),
                    ALABAMA,
                ),
                ("california", "CA"): (cs.source(swdb_precincts.PUBLISHER, CA_TITLE), CALIFORNIA),
                ("oregon", "OR"): (cs.source(OR_PUBLISHER, OR_TITLE), OREGON),
            }
        )
        if sources[("oregon", "OR")][0] in cs.minted:
            cs.add(
                "source",
                id=sources[("oregon", "OR")][0],
                kind="official_body",
                publisher=OR_PUBLISHER,
                title=OR_TITLE,
                url="https://records.sos.state.or.us/ORSOSCMSearch/",
                license=OR_LICENSE,
            )
        pages: dict[tuple[str, int], UUID] = {}
        artifacts: dict[str, UUID] = {}
        locators: list[pl.DataFrame] = []
        if sources[("california", "CA")][0] in cs.minted:
            cs.add(
                "source",
                id=sources[("california", "CA")][0],
                kind="academic",
                publisher=swdb_precincts.PUBLISHER,
                title=CA_TITLE,
                url=swdb_precincts.SWDB,
                license=swdb_precincts.LICENSE,
                attribution=swdb_precincts.PUBLISHER,
            )
            for election in CA_ELECTIONS:
                cs.artifact(
                    raw.cached(SOURCE, CA_SOV.format(election, election.lower())), sources[("california", "CA")][0]
                )

        candidates: list[tuple[str, str, UUID | None, str, pl.DataFrame]] = [
            (key, state, None, TOWN_ARCHIVE if state in TOWNS else ARCHIVE, rows)
            for (key, state), rows in archives.group_by("key", "state", maintain_order=True)
        ]
        question = ["origin", "year", "day", "state", "office", "district"]
        questions = returns.group_by(question).agg(
            yes=pl.col("votes").filter(pl.col("answer") == "YES").sum(),
            no=pl.col("votes").filter(pl.col("answer") == "NO").sum(),
        )
        matched = (
            questions.join(
                totals.rename({"day": "held"}).with_columns(year=pl.col("held").dt.year()),
                on=["state", "year", "yes", "no"],
            )
            .filter(pl.col("day").is_null() | (pl.col("day") == pl.col("held")))
            .filter(pl.len().over(question) == 1)
        )
        for origin, key in matched.filter(pl.len().over("origin", "key") > 1).select("origin", "key").unique().rows():
            print(f"{key}: several {origin} questions share its totals, skipped")
        matched = (
            matched.filter(pl.len().over("origin", "key") == 1)
            .sort("origin")
            .unique("key", keep="first", maintain_order=True)
        )
        print(f"{matched.group_by('origin').len().rows()} questions match a statewide question's totals exactly")
        picked = returns.join(matched.select(*question, "key"), on=question, nulls_equal=True)
        del returns
        for (origin, year, state, key), rows in picked.group_by("origin", "year", "state", "key", maintain_order=True):
            source, method = sources[(str(origin), year if origin == "medsl" else state)]
            candidates.append((str(key), str(state), source, method, rows))

        landed: dict[str, int] = {}
        kept: set[UUID | None] = set()
        skipped = 0
        scope = [state for state in os.environ.get("MEASURE_STATES", "").split(",") if state]
        values = []
        parts = pl.col("code").str.splitn(":", 5)
        held = (
            precinct_code.held(conn, SOURCE, scope)
            .with_columns(
                year=parts.struct.field("field_0"),
                state=parts.struct.field("field_1"),
                fips=parts.struct.field("field_2"),
                jurisdiction=parts.struct.field("field_3"),
                precinct=parts.struct.field("field_4"),
            )
            .with_columns(
                canonical=precinct_code.expr(
                    pl.col("year"),
                    pl.col("state"),
                    pl.col("fips"),
                    pl.when(
                        (
                            pl.col("jurisdiction")
                            .filter(pl.col("jurisdiction") != "")
                            .n_unique()
                            .over("result_set_id", "fips")
                            > 1
                        )
                        & ~pl.col("precinct")
                        .str.to_uppercase()
                        .str.starts_with(pl.col("jurisdiction").str.to_uppercase())
                    )
                    .then(pl.concat_str("jurisdiction", "precinct", separator=" "))
                    .otherwise("precinct"),
                )
            )
        )
        used = set(held["canonical"])
        moved, moved_locators = precinct_code.remap(cs, conn, held)
        values.extend(moved)
        locators.extend(moved_locators)
        for key, state, source, method, rows in candidates:
            if scope and state not in scope:
                continue
            official = defaults.filter(pl.col("key") == key)
            if official.height == 0:
                continue
            if key in filled:
                print(f"{key}: already has a precinct set")
                continue
            if rows.filter(pl.col("fips").is_null() | (pl.col("precinct") == ""))["votes"].sum():
                print(
                    f"{key}: {rows.filter(pl.col('fips').is_null() | (pl.col('precinct') == ''))['votes'].sum()} "
                    "votes outside any county's precincts, skipped"
                )
                skipped += 1
                continue
            rows = rows.filter(pl.col("fips").is_not_null(), pl.col("precinct") != "")
            sums = rows.group_by("answer").agg(pl.col("votes").sum())
            expected = official.group_by("answer").agg(pl.col("total").sum())
            joined = expected.join(sums, on="answer", how="full", coalesce=True).fill_null(0)
            if (joined["votes"] != joined["total"]).any():
                print(f"{key}: precincts {sorted(sums.rows())} against totals {sorted(expected.rows())}, skipped")
                skipped += 1
                continue
            by_county = official["scheme"].unique().to_list() == ["us.county"]
            if by_county:
                differ = (
                    official.group_by("code", "answer")
                    .agg(pl.col("total").sum())
                    .join(
                        rows.group_by("fips", "answer").agg(pl.col("votes").sum()).rename({"fips": "code"}),
                        on=["code", "answer"],
                        how="full",
                        coalesce=True,
                    )
                    .fill_null(0)
                    .filter(pl.col("votes") != pl.col("total"))
                )
                if differ.height:
                    print(f"{key}: county sums differ in {differ['code'].n_unique()} counties, skipped")
                    skipped += 1
                    continue
            if rows["fips"].is_in([*counties, *aggregates, "ME:emergency"]).not_().any():
                print(f"{key}: precincts in counties not on file, skipped")
                skipped += 1
                continue
            first = official.row(0, named=True)
            options = dict(official.select("answer", "option").unique().iter_rows())
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            present = pl.DataFrame(
                conn.execute(
                    """select u.id::text, o.id::text from result_value v join result_set rs on rs.k = v.result_set_k
                       join reporting_unit u on u.k = v.unit_k join contest_option o on o.k = v.option_k
                       where rs.id = %s""",
                    [result_set],
                ).fetchall(),
                schema={"unit_id": pl.String, "option_id": pl.String},
                orient="row",
            )
            day = first["day"]
            rows = rows.with_columns(
                code=precinct_code.expr(
                    pl.lit(day.year),
                    pl.lit(state),
                    pl.col("fips"),
                    pl.when(
                        (pl.col("jurisdiction").filter(pl.col("jurisdiction") != "").n_unique().over("fips") > 1)
                        & ~pl.col("precinct")
                        .str.to_uppercase()
                        .str.starts_with(pl.col("jurisdiction").fill_null("").str.to_uppercase())
                    )
                    .then(pl.concat_str("jurisdiction", "precinct", separator=" "))
                    .otherwise("precinct"),
                )
            )
            codes = {}
            precincts = set()
            for code, fips, precinct in rows.select("code", "fips", "precinct").unique().iter_rows():
                if precinct == "county":
                    codes[code] = str(counties[fips][0])
                    continue
                if fips == "ME:emergency" and fips not in aggregates:
                    aggregates[fips] = cs.unit("us.aggregate", fips)
                    cs.add(
                        "reporting_unit",
                        id=aggregates[fips],
                        polity_id=states["ME"],
                        scheme_code="us.aggregate",
                        code=fips,
                        parent_id=counties["23"][0],
                        area_relation="within",
                        area_id=counties["23"][1],
                        kind="aggregate",
                    )
                if fips in aggregates and not fips.isdigit():
                    codes[code] = str(aggregates[fips])
                    continue
                codes[code] = str(cs.unit("us.precinct", code))
                precincts.add(code)
            complete = (
                rows.group_by("code", "answer")
                .agg(
                    pl.col("votes").sum(),
                    pl.col("flags").max().fill_null(0) if "flags" in rows.columns else pl.lit(0).alias("flags"),
                )
                .select(
                    unit_id=pl.col("code").replace_strict(codes),
                    option_id=pl.col("answer").replace_strict(options),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.col("flags").cast(pl.Int16),
                    value=pl.col("votes"),
                )
            )
            staged, supersedes = result_set, None
            if result_set not in cs.minted:
                gap = complete.join(present, on=["unit_id", "option_id"], how="anti")
                moved = elections.get(result_set, first["election"]) != first["election"]
                if not moved and (gap.height == 0 or not present["unit_id"].is_in(list(codes.values())).all()):
                    continue
                staged = cs.key(
                    f"{SOURCE}.result_set", f"{key}:precinct:{day}" if moved else f"{key}:precinct:complete"
                )
                supersedes = result_set
                if staged not in cs.minted:
                    continue
                print(
                    f"{key}: moved to {day}"
                    if moved
                    else f"{key}: {present.height} of its values on file, superseded by a complete set"
                )
            kept.update({staged, supersedes})
            for id, status in [(supersedes, "superseded"), (staged, "active")] if supersedes else [(staged, "active")]:
                cs.add(
                    "result_set",
                    id=id,
                    election_id=UUID(first["election"]),
                    contest_id=UUID(first["contest"]),
                    stage="certified",
                    kind="counted",
                    status=status,
                    source_id=source or UUID(first["source"]),
                    is_default=False,
                    supersedes_id=supersedes if id == staged else None,
                    completeness=1,
                )
            mono = method == CALIFORNIA and day == swdb_precincts.DAYS["P16"]
            cs.add(
                "note",
                entity_id=staged,
                kind="methodology",
                body_md=method.format(counties=" and to each county's" if by_county else "")
                + (MONO_NOTE if mono else "")
                + (OR_2020_NOTE if method == OREGON and day == date(2020, 11, 3) else "")
                + (OR_2020_110_NOTE if method == OREGON and day == date(2020, 11, 3) and "measure-110" in key else "")
                + (OR_2024_NOTE if method == OREGON and day == date(2024, 11, 5) else ""),
            )
            if method == OREGON:
                source = sources[("oregon", "OR")][0]
                for record in (
                    [OR_DETAIL.format(uri) for uri in OR_2024.values()]
                    if day.year == 2024
                    else [OR_DETAIL.format(OR_RECORDS[day])]
                ):
                    if record not in artifacts:
                        artifacts[record] = cs.artifact(raw.cached(SOURCE, record), source)
                    cs.add(
                        "citation",
                        entity_id=staged,
                        source_id=source,
                        locator={"url": record, "artifact": str(artifacts[record])},
                    )
                if day.year == 2024:
                    cs.add(
                        "citation",
                        entity_id=staged,
                        source_id=UUID(first["source"]),
                        locator={
                            "url": conn.execute("select url from source where id = %s", [first["source"]]).fetchall()[
                                0
                            ][0],
                            "text": "Wheeler",
                        },
                    )
                located = rows.filter(pl.col("page").is_not_null())
                for file, number in located.select("file", "page").unique().iter_rows():
                    if (file, number) in pages:
                        continue
                    pdf = raw.cached(SOURCE, file)
                    image = DATA / "pages" / SOURCE / f"{pdf.sha256}-{number}.png"
                    if not image.exists():
                        image.parent.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                            + [str(pdf.path), str(image.with_suffix(""))],
                            check=True,
                        )
                        if Path(file).stem in OR_TURNED:
                            with Image.open(image) as rendered:
                                rendered.rotate(-90, expand=True).save(image)
                    if file not in artifacts:
                        artifacts[file] = cs.artifact(pdf, source)
                    pages[(file, number)] = cs.page(artifacts[file], number)
                    with Image.open(image) as opened:
                        cs.add(
                            "source_page",
                            id=pages[(file, number)],
                            artifact_id=artifacts[file],
                            page_number=number,
                            image_key=f"pages/{SOURCE}/{image.name}",
                            width=opened.width,
                            height=opened.height,
                        )
                locators.append(
                    located.select(
                        result_set_id=pl.lit(str(staged)),
                        unit_id=pl.col("code").replace_strict(codes),
                        option_id=pl.col("answer").replace_strict(options),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.lit(1, pl.Int16),
                        count_round=pl.lit(0, pl.Int16),
                        page_id=pl.concat_str("file", "page", separator="\x1f").replace_strict(
                            {f"{file}\x1f{number}": str(page) for (file, number), page in pages.items()}
                        ),
                        source_id=pl.lit(None, pl.String),
                        x0="x0",
                        y0="y0",
                        x1="x1",
                        y1="y1",
                    )
                )
            if mono:
                clerk = cs.source("Mono County Elections", "Statement of Vote, June 7, 2016")
                if clerk in cs.minted:
                    cs.add(
                        "source",
                        id=clerk,
                        kind="official_body",
                        publisher="Mono County Elections",
                        title="Statement of Vote, June 7, 2016",
                        url="https://monocounty.ca.gov/1910/Elections",
                        license="Public record",
                    )
                cs.add(
                    "citation",
                    entity_id=staged,
                    source_id=clerk,
                    locator={
                        "url": MONO_2016,
                        "artifact": str(cs.artifact(raw.cached(SOURCE, MONO_2016), clerk)),
                        "page": 16,
                    },
                )
            values.append(complete.select(pl.lit(str(staged)).alias("result_set_id"), pl.all()))
            used |= precincts
            landed[state] = landed.get(state, 0) + 1
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
        active = pl.DataFrame(
            conn.execute(
                """select rs.id::text, rs.election_id::text, rs.contest_id::text, rs.stage, rs.kind, rs.source_id::text,
                          rs.is_default, rs.completeness::float8,
                          case when u.scheme_code = 'us.precinct' then pu.code else u.code end,
                           upper(o.ballot_label), sum(v.value)::bigint
                    from result_set rs join changeset g on g.id = rs.changeset_id
                    join result_value v on v.result_set_k = rs.k and v.measure = 1
                    join reporting_unit u on u.k = v.unit_k
                    left join reporting_unit pu on pu.id = u.parent_id
                    join contest_option o on o.k = v.option_k
                    where g.pipeline = %s and rs.status = 'active'
                    group by rs.id, 9, 10""",
                [SOURCE],
            ).fetchall(),
            schema=[*columns, "code", "answer", "votes"],
            orient="row",
        ).with_columns(answer=pl.col("answer").replace_strict(ANSWERS, default=None))
        expected = defaults.group_by("contest", "scheme", "code", "answer").agg(pl.col("total").sum())
        whole = expected.group_by("contest", "answer").agg(pl.col("total").sum())
        county_level = expected.group_by("contest").agg(by_county=(pl.col("scheme") == "us.county").all())
        broken = set(
            active.group_by("id", "contest_id", "answer")
            .agg(pl.col("votes").sum())
            .join(whole, left_on=["contest_id", "answer"], right_on=["contest", "answer"], how="left", nulls_equal=True)
            .filter(pl.col("votes") != pl.col("total").fill_null(0))["id"]
            .to_list()
        )
        for (id,), rows in active.join(
            county_level.filter("by_county"), left_on="contest_id", right_on="contest"
        ).group_by("id"):
            mine = rows.group_by("code", "answer").agg(pl.col("votes").sum())
            theirs = expected.filter(pl.col("contest") == rows["contest_id"][0]).select("code", "answer", "total")
            joined = mine.join(theirs, on=["code", "answer"], how="full", coalesce=True, nulls_equal=True)
            if (joined["votes"].fill_null(0) != joined["total"].fill_null(0)).any():
                broken.add(id)
        for row in active.filter(pl.col("id").is_in(list(broken))).unique("id").iter_rows(named=True):
            if UUID(row["id"]) in kept:
                continue
            print(f"{row['id']}: no longer adds up to its question's default, withdrawn")
            cs.add(
                "result_set",
                **{column: UUID(str(row[column])) if column.endswith("id") else row[column] for column in columns},
                status="withdrawn",
            )
            cs.add("note", entity_id=UUID(row["id"]), kind="caveat", body_md=WITHDRAWN)
        precinct_code.units(cs, conn, pl.Series(sorted(used), dtype=pl.String), held)
        if values:
            cs.frame("result_value", pl.concat(values))
        if locators:
            cs.frame("value_locator", pl.concat(locators, how="vertical_relaxed"))
        print(f"{sum(landed.values())} precinct sets {sorted(landed.items())}, {skipped} skipped, {len(used)} units")
        return cs.write()
