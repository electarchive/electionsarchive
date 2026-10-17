import html
import io
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl, wa_local
from ea.sources.us.city_precincts import WA_COUNTIES
from ea.sources.us.ncsbe import COLUMNS, FILES, HEADERLESS, PUBLISHER, TITLE
from ea.store import DATA, connect

SOURCE = "us.state_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
NC_DAYS = {
    date(2002, 9, 10): None,
    date(2004, 7, 20): None,
    date(2004, 8, 17): date(2004, 7, 20),
    date(2006, 5, 2): None,
    date(2006, 5, 30): date(2006, 5, 2),
    date(2008, 5, 6): None,
    date(2010, 5, 4): None,
    date(2010, 6, 22): date(2010, 5, 4),
    date(2012, 5, 8): None,
    date(2012, 7, 17): date(2012, 5, 8),
    date(2014, 5, 6): None,
    date(2014, 7, 15): date(2014, 5, 6),
    date(2016, 3, 15): None,
    date(2018, 5, 8): None,
    date(2020, 3, 3): None,
    date(2020, 6, 23): date(2020, 3, 3),
    date(2022, 5, 17): None,
}
MN_DAYS = [date(2016, 8, 9), date(2018, 8, 14), date(2020, 8, 11), date(2022, 8, 9)]
MN_FILES = "https://electionresultsfiles.sos.mn.gov/{day:%Y%m%d}/{name}.txt"
WA_DAYS = [
    date(2015, 8, 4),
    date(2016, 8, 2),
    date(2017, 8, 1),
    date(2018, 8, 7),
    date(2019, 8, 6),
    date(2020, 8, 4),
    date(2022, 8, 2),
]
CA_SOV = "https://elections.cdn.sos.ca.gov/sov/{path}"
CA_KINDS = [
    (r"us.?s(en|-ft)|uss", "us-senate"),
    (r"congress|us.?rep|-cd$", "house"),
    (r"assem|-ad$", "assembly"),
    (r"senat|stsen", "senate"),
    (r"lt|lg|lieutenant", "lieutenant-governor"),
    (r"gov", "governor"),
    (r"sos|secretary", "secretary-of-state"),
    (r"cont", "controller"),
    (r"trea|tres", "treasurer"),
    (r"ag$|attorney", "attorney-general"),
    (r"ic$|ins|commissioner", "insurance-commissioner"),
]
CA_TITLE = re.compile(
    r"(\d+)(?:st|nd|rd|th) (?:Congressional|Senatorial|State Senate|Senate|Assembly) District", re.IGNORECASE
)
CA_PARTY = re.compile(r"(DEM|REP|LIB|NPP|GRN|PF|AI|REF|NL|Libertarian|Natural Law|Green|Reform)(?: \(W/I\))?")
CA_CODES = {"PF": "PAF", "AI": "AIP", "Libertarian": "LIB", "Natural Law": "NL", "Green": "GRN", "Reform": "REF"}
CA_STATEWIDE = (
    "governor",
    "lieutenant-governor",
    "secretary-of-state",
    "controller",
    "treasurer",
    "attorney-general",
    "insurance-commissioner",
)
CA_DAYS = {
    date(2002, 3, 5): (
        "2002-primary/{}.xls",
        ["gov", "ltgov", "sos", "controller", "treasurer", "ag", "ic", "congress", "state-senate", "state-assemb"],
    ),
    date(2004, 3, 2): ("2004-primary/{}.xls", ["us_senate_formatted", "congress", "senate", "assembly"]),
    date(2006, 6, 6): (
        "2006-primary/sov_detail_primary_{}.xls",
        ["gov", "ltgov", "sos", "controller", "treas", "ag", "inscomm", "ussenate", "congress", "senate", "assembly"],
    ),
    date(2008, 6, 3): (
        "2008-statewide-direct-primary/{}.xls",
        ["us_reps08primary", "stsen08primary", "stassem08primary"],
    ),
    date(2010, 6, 8): (
        "2010-primary/{}.xls",
        [
            "40-51-gov",
            "52-57-lg",
            "58-60-sos",
            "61-63-cont",
            "64-66-tres",
            "67-72-ag",
            "73-75-ins",
            "79-84-ussen",
            "85-95-cd",
            "96-100-senate",
            "101-118-ad",
        ],
    ),
    date(2012, 6, 5): (
        "2012-primary/{}.xls",
        [
            "70-us-senate-formatted",
            "82-us-reps-formatted",
            "97-state-senators-formatted",
            "102-state-assembly1-80formattted",
        ],
    ),
    date(2014, 6, 3): (
        "2014-primary/xls/{}.xls",
        [
            "33-governor",
            "42-lieutenant-governor",
            "45-secretary-of-state",
            "48-controller",
            "51-treasurer",
            "54-attorney-general",
            "57-insurance-commissioner",
            "63-congress",
            "78-state-senator",
            "84-state-assemblymember",
        ],
    ),
    date(2016, 6, 7): (
        "2016-primary/{}.xls",
        [
            "75-us-senate-formatted",
            "90-us-reps-formatted",
            "105-state-senators-formatted",
            "110-state-assembly-formatted",
        ],
    ),
    date(2018, 6, 5): (
        "2018-primary/sov/{}.xls",
        [
            "31-governor",
            "43-lieutenant-governor",
            "49-secretary-of-state",
            "52-controller",
            "55-treasurer",
            "58-attorney-general",
            "61-insurance-commissioner",
            "67-us-senators",
            "82-congress",
            "101-state-senator",
            "107-state-assemblymember",
        ],
    ),
    date(2020, 3, 3): (
        "2020-primary/sov/{}.xls",
        ["126-us-rep-congress", "143-state-senators-formatted", "148-state-assembly-formatted"],
    ),
    date(2022, 6, 7): (
        "2022-primary/sov/{}.xlsx",
        [
            "30-governor",
            "42-lieutenant-governor",
            "48-secretary-of-state",
            "51-controller",
            "54-treasurer",
            "57-ag",
            "60-commissioner",
            "68-uss-ft",
            "86-congress",
            "99-state-senator",
            "104-state-assemblymember",
        ],
    ),
}
WA_OFFICES = {
    "GOVERNOR": "governor",
    "LT. GOVERNOR": "lieutenant-governor",
    "SECRETARY OF STATE": "secretary-of-state",
    "STATE TREASURER": "treasurer",
    "STATE AUDITOR": "auditor",
    "ATTORNEY GENERAL": "attorney-general",
    "COMMISSIONER OF PUBLIC LANDS": "commissioner-of-public-lands",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "superintendent-of-public-instruction",
    "INSURANCE COMMISSIONER": "insurance-commissioner",
}
WA_LEGISLATIVE = re.compile(
    r"LEGISLATIVE DISTRICT 0*(\d+) (?:STATE )?(SENATOR|REPRESENTATIVE)(?:,? (?:POS\.?|POSITION) ?(\d))?"
)
WA_STATEWIDE = re.compile(rf"(?:WASHINGTON STATE )?({'|'.join(re.escape(office) for office in WA_OFFICES)})")
PREFERENCES = {
    "democratic": "DEM",
    "democrat": "DEM",
    "republican": "REP",
    "gop": "REP",
    "libertarian": "LIB",
    "green": "GRE",
    "constitution": "CST",
}
STATES = {
    "NC": (
        "37",
        "North Carolina",
        PUBLISHER,
        TITLE,
        "https://www.ncsbe.gov/results-data/election-results/historical-election-results-data",
    ),
    "WA": ("53", "Washington", wa_local.PUBLISHER, wa_local.TITLE, "https://results.vote.wa.gov"),
    "CA": (
        "06",
        "California",
        "California Secretary of State",
        "Statement of Vote",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    "MN": (
        "27",
        "Minnesota",
        "Minnesota Secretary of State",
        "Election results media files",
        "https://electionresults.sos.mn.gov",
    ),
}
OFFICES = {
    "GOVERNOR": ("governor", "gubernatorial"),
    "GOVERNOR & LT GOVERNOR": ("governor", "gubernatorial"),
    "LIEUTENANT GOVERNOR": ("lieutenant-governor", "Lieutenant Governor"),
    "ATTORNEY GENERAL": ("attorney-general", "Attorney General"),
    "SECRETARY OF STATE": ("secretary-of-state", "Secretary of State"),
    "AUDITOR": ("auditor", "State Auditor"),
    "TREASURER": ("treasurer", "State Treasurer"),
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": (
        "superintendent-of-public-instruction",
        "Superintendent of Public Instruction",
    ),
    "COMMISSIONER OF AGRICULTURE": ("commissioner-of-agriculture", "Commissioner of Agriculture"),
    "COMMISSIONER OF INSURANCE": ("commissioner-of-insurance", "Commissioner of Insurance"),
    "COMMISSIONER OF LABOR": ("commissioner-of-labor", "Commissioner of Labor"),
    "COMMISSIONER OF PUBLIC LANDS": ("commissioner-of-public-lands", "Commissioner of Public Lands"),
    "INSURANCE COMMISSIONER": ("insurance-commissioner", "Insurance Commissioner"),
    "CONTROLLER": ("controller", "Controller"),
}
LEGISLATIVE = re.compile(r"NC (?:STATE )?(HOUSE|SENATE)(?: OF REPRESENTATIVES)?(?: DISTRICT 0*(\d+)| \(0*(\d+)\))")
MN_LEGISLATIVE = re.compile(r"STATE (REPRESENTATIVE|SENATOR) DISTRICT 0*(\d+)([AB]?)")
STATEWIDE = re.compile(rf"(?:NC )?(?:STATE )?({'|'.join(OFFICES)})")
SUFFIX = re.compile(r"\s*(?:- |\()(DEM|REP|LIB|GRE|CST)\)?$")
PSEUDO = re.compile(r"ABSENTEE.*|PROV.*|TRANSFERS?|CURBSIDE|ONE ?STOP.*|ONESTOP.*|OS|OS[ _-].*")
PARTIES = {
    "DEM": ("democratic-party", "democratic", "dp", "Democratic"),
    "REP": ("republican-party", "republican", "rp", "Republican"),
    "LIB": ("libertarian-party", "libertarian", "liberp", "Libertarian"),
    "GRE": ("green-party", "green", "greenp", "Green"),
    "CST": ("constitution-party", "constitution", "constp", "Constitution"),
    "DFL": ("democratic-party", "democratic-farmer-labor", "dp", "Democratic-Farmer-Labor"),
    "R": ("republican-party", "republican", "rp", "Republican"),
    "IP": ("MN:independence-party", "independence", "indepp", "Independence"),
    "LMN": ("MN:legalize-marijuana-now-party", "legal-marijuana-now", "lmnp", "Legal Marijuana Now"),
    "": (None, None, "ttp", "top-two"),
    "GRN": ("green-party", "green", "greenp", "Green"),
    "AIP": ("CA:american-independent-party", "american-independent", "amerindepp", "American Independent"),
    "PAF": ("CA:peace-and-freedom-party", "peace-and-freedom", "peacefreedomp", "Peace and Freedom"),
    "GLC": (
        "MN:grassroots-legalize-cannabis-party",
        "grassroots-legalize-cannabis",
        "glcp",
        "Grassroots–Legalize Cannabis",
    ),
}
UNALLOCATED = 32
CA_METHOD = "Totals are the county totals of the Secretary of State's Statement of Vote. {rule}"
WA_METHOD = (
    "Totals are the precinct rows of the Secretary of State's statewide precinct export where they add up to its "
    "county results, and the county results elsewhere. The top two candidates advance to the general election."
)
MN_METHOD = (
    "Totals are the sums of the precinct rows in the Secretary of State's all-races file. The file does not mark "
    "winners, so the winner shown is the candidate with the most votes."
)
METHOD = (
    "Totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file; votes the file reports only county-wide are kept at the county level. The file does not mark "
    "winners, so the winner shown is the leading candidate when that candidate passed the {threshold} percent a "
    "primary needs, or when no runoff followed; when a runoff followed, the top two are shown as advancing."
)


def fetch() -> None:
    for day in NC_DAYS:
        raw.fetch(SOURCE, FILES.format(day=day), suffix=".zip")
    for day in MN_DAYS:
        for name in ("allracesbyprecinct", "PrctTbl"):
            raw.fetch(SOURCE, MN_FILES.format(day=day, name=name), suffix=".txt")
    for path, names in CA_DAYS.values():
        for name in names:
            raw.fetch(SOURCE, CA_SOV.format(path=path.format(name)), suffix=Path(path).suffix)


def parse() -> None:
    with connect() as conn:
        counties = {
            (code[:2], re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower())): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code ~ '^(37|53|06)'"""
            ).fetchall()
        }
    frames = []
    for day in NC_DAYS:
        with zipfile.ZipFile(raw.cached(SOURCE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0]).replace(b"\x00", b"")
        first = text.split(b"\n", 1)[0]
        headed = first.lower().lstrip(b'"').startswith(b"county")
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            has_header=headed,
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        if not headed:
            frame.columns = (
                HEADERLESS
                if frame.width == 9
                else [
                    "county",
                    "date",
                    "precinct",
                    "contest",
                    "choice",
                    "party",
                    "eday",
                    "absentee",
                    "provisional",
                    "votes",
                ]
            )
        frame = frame.rename(
            {
                column: {"party_cd": "party", "choice party": "party", "precinct": "precinct"}.get(
                    column.strip().lower(), COLUMNS.get(column.strip().lower(), column)
                )
                for column in frame.columns
            }
        )
        frame = frame.select(
            pl.col("county").str.strip_chars(),
            pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " "),
            pl.col("contest").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("choice").str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("party").fill_null("").str.strip_chars().str.to_uppercase(),
            pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
        ).filter(pl.col("choice").is_not_null(), pl.col("votes").is_not_null())
        labels = []
        for contest in frame["contest"].unique():
            suffix = SUFFIX.search(contest)
            bare = SUFFIX.sub("", contest)
            if found := LEGISLATIVE.fullmatch(bare):
                office, code = found[1].lower(), str(int(found[2] or found[3]))
            elif found := STATEWIDE.fullmatch(bare):
                office, code = OFFICES[found[1]][0], None
            else:
                continue
            labels.append({"contest": contest, "office": office, "code": code, "named": suffix[1] if suffix else None})
        if not labels:
            continue
        frame = frame.join(pl.DataFrame(labels, schema_overrides={"code": pl.String, "named": pl.String}), on="contest")
        writein = pl.col("choice").str.contains(r"(?i)write-?\s*in")
        frame = frame.with_columns(
            party=pl.coalesce("named", pl.when(pl.col("party").is_in(list(PARTIES))).then("party")),
            writein=writein,
        )
        sole = frame.filter(pl.col("party").is_not_null()).group_by("contest").agg(sole=pl.col("party").unique())
        frame = (
            frame.join(sole, on="contest", how="left")
            .with_columns(
                party=pl.coalesce(
                    "party",
                    pl.when(pl.col("writein") & (pl.col("sole").list.len() == 1)).then(pl.col("sole").list.first()),
                )
            )
            .drop("sole")
        )
        lost = frame.filter(pl.col("party").is_null())
        if lost.height:
            print(
                day, "rows without a party:", lost.group_by("contest", "choice").agg(pl.col("votes").sum()).rows()[:10]
            )
        pseudo = pl.col("precinct").str.to_uppercase().str.contains(f"^(?:{PSEUDO.pattern})$")
        frames.append(
            frame.filter(pl.col("party").is_not_null())
            .with_columns(
                fips=pl.col("county").map_elements(
                    lambda county: counties[("37", re.sub(r"[^a-z]", "", county.lower()))], return_dtype=pl.String
                )
            )
            .select(
                state=pl.lit("NC"),
                day=pl.lit(day),
                office="office",
                code="code",
                party="party",
                label="party",
                fips="fips",
                scheme=pl.when(pseudo).then(pl.lit("us.aggregate")).otherwise(pl.lit("us.precinct")),
                unit=pl.when(pseudo)
                .then(pl.concat_str(pl.lit("NC"), "fips", pl.lit("county-wide"), separator=":"))
                .otherwise(
                    pl.concat_str(
                        pl.lit(f"{day}:NC"), "fips", pl.col("county").str.to_uppercase(), "precinct", separator=":"
                    )
                ),
                choice=pl.when("writein").then(pl.lit("Write-ins")).otherwise("choice"),
                writein="writein",
                votes="votes",
            )
        )
    for day in MN_DAYS:
        names = pl.read_csv(
            io.BytesIO(
                raw.cached(SOURCE, MN_FILES.format(day=day, name="PrctTbl")).path.read_bytes().decode("cp1252").encode()
            ),
            separator=";",
            has_header=False,
            infer_schema=False,
        ).select(county="column_0", precinct="column_1", name=pl.col("column_2").str.strip_chars())
        frame = (
            pl.read_csv(
                io.BytesIO(
                    raw.cached(SOURCE, MN_FILES.format(day=day, name="allracesbyprecinct"))
                    .path.read_bytes()
                    .decode("cp1252")
                    .encode()
                ),
                separator=";",
                has_header=False,
                infer_schema=False,
                quote_char=None,
            )
            .select(
                county="column_1",
                precinct="column_2",
                contest=pl.col("column_4").str.to_uppercase().str.strip_chars(),
                choice=pl.col("column_7").str.strip_chars(),
                party=pl.col("column_10").str.strip_chars(),
                votes=pl.col("column_13").cast(pl.Int64),
            )
            .join(names, on=["county", "precinct"], how="left", validate="m:1")
        )
        labels = []
        for contest in frame["contest"].unique():
            if found := MN_LEGISLATIVE.fullmatch(contest):
                office = "house" if found[1] == "REPRESENTATIVE" else "senate"
                code = str(int(found[2])) + (f"-{found[3].lower()}" if found[3] else "")
            elif found := STATEWIDE.fullmatch(contest):
                office, code = OFFICES[found[1]][0], None
            else:
                continue
            labels.append({"contest": contest, "office": office, "code": code})
        frame = frame.join(pl.DataFrame(labels, schema_overrides={"code": pl.String}), on="contest")
        unknown = frame.filter(~pl.col("party").is_in(list(PARTIES)))
        if unknown.height:
            print(
                day,
                "rows of other parties:",
                unknown.group_by("contest", "party").agg(pl.col("votes").sum()).rows()[:10],
            )
        lost = frame.filter(pl.col("name").is_null())
        if lost.height:
            raise ValueError(f"{day}: precincts missing from the precinct table: {lost.head(5).rows()}")
        fips = pl.lit("27") + (pl.col("county").cast(pl.Int64) * 2 - 1).cast(pl.String).str.zfill(3)
        frames.append(
            frame.filter(pl.col("party").is_in(list(PARTIES))).select(
                state=pl.lit("MN"),
                day=pl.lit(day),
                office="office",
                code="code",
                party="party",
                label="party",
                fips=fips,
                scheme=pl.lit("us.precinct"),
                unit=pl.concat_str(
                    pl.lit(f"{day}:MN"), fips, pl.lit(""), pl.col("name").str.to_uppercase(), separator=":"
                ),
                choice="choice",
                writein=pl.lit(False),
                votes="votes",
            )
        )
    washington = {name: code for (prefix, name), code in counties.items() if prefix == "53"}
    for day in WA_DAYS:
        race = pl.col("Race").str.to_uppercase().str.replace_all(r"\s+-\s+|\s+", " ").str.strip_chars().alias("race")
        choice = (
            pl.col("Candidate")
            .map_elements(html.unescape, return_dtype=pl.String)
            .str.replace_all(r"\s+", " ")
            .str.strip_chars()
            .alias("choice")
        )
        totals = pl.read_csv(raw.cached(wa_local.SOURCE, wa_local.FILES[day]).path, infer_schema_length=0).select(
            race,
            choice,
            fips=pl.col("County")
            .str.to_lowercase()
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(washington, default=None),
            preference=pl.col("Party")
            .str.extract(r"\(Prefers (.+?) ?Party\)")
            .str.to_lowercase()
            .str.replace_all(r"\s+", " ")
            .str.strip_chars(),
            votes=pl.col("Votes").cast(pl.Int64),
        )
        rows = (
            pl.read_csv(
                raw.cached(wa_local.SOURCE, wa_local.PRECINCTS.format(day=day)).path,
                infer_schema=False,
                encoding="utf8-lossy",
            )
            .select(
                race,
                choice,
                fips=pl.col("CountyCode")
                .replace_strict(WA_COUNTIES, default=None)
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(washington, default=None),
                precinct=pl.col("PrecinctName").str.strip_chars(),
                votes=pl.col("Votes").cast(pl.Int64),
            )
            .filter(pl.col("precinct") != "Total")
        )
        labels = []
        for contest in pl.concat([totals["race"], rows["race"]]).unique():
            if found := WA_LEGISLATIVE.fullmatch(contest):
                office = "senate" if found[2] == "SENATOR" else "house"
                code = found[1] + (f":{found[3]}" if office == "house" else "")
            elif found := WA_STATEWIDE.fullmatch(contest):
                office, code = WA_OFFICES[found[1]], None
            else:
                continue
            labels.append({"race": contest, "office": office, "code": code})
        if not labels:
            continue
        named = pl.DataFrame(labels, schema_overrides={"code": pl.String})
        totals = totals.join(named, on="race").filter(~pl.col("choice").str.contains(r"(?i)rejected"))
        rows = rows.join(named, on="race").filter(~pl.col("choice").str.contains(r"(?i)rejected"))
        if totals["fips"].is_null().any() or rows["fips"].is_null().any():
            raise ValueError(f"{day}: unmatched Washington counties")
        sums = rows.group_by("office", "code", "fips", "choice").agg(pl.col("votes").sum())
        exact = (
            totals.join(sums, on=["office", "code", "fips", "choice"], how="left", nulls_equal=True)
            .group_by("office", "code", "fips")
            .agg(exact=(pl.col("votes") == pl.col("votes_right")).fill_null(False).all())
            .filter("exact")
            .drop("exact")
        )
        preferences = totals.group_by("office", "code", "choice").agg(pl.col("preference").drop_nulls().first())
        lines = pl.concat(
            [
                rows.join(exact, on=["office", "code", "fips"], nulls_equal=True).with_columns(
                    scheme=pl.lit("us.precinct"),
                    unit=pl.concat_str(pl.lit(f"{day}:WA"), "fips", pl.lit(""), "precinct", separator=":"),
                ),
                totals.join(exact, on=["office", "code", "fips"], how="anti", nulls_equal=True)
                .drop("preference")
                .with_columns(scheme=pl.lit("us.county"), unit=pl.col("fips")),
            ],
            how="diagonal",
        ).join(preferences, on=["office", "code", "choice"], how="left", nulls_equal=True)
        print(
            day,
            "Washington counties by precinct:",
            exact.height,
            "of",
            totals.select("office", "code", "fips").unique().height,
        )
        writein = pl.col("choice").str.contains(r"(?i)write-?\s*in")
        frames.append(
            lines.select(
                state=pl.lit("WA"),
                day=pl.lit(day),
                office="office",
                code="code",
                party=pl.lit(""),
                label=pl.when(writein)
                .then(None)
                .otherwise(pl.col("preference").replace_strict(PREFERENCES, default=None)),
                fips="fips",
                scheme="scheme",
                unit="unit",
                choice=pl.when(writein).then(pl.lit("Write-ins")).otherwise("choice"),
                writein=writein,
                votes="votes",
            )
        )
    rows = []
    for day, (path, names) in CA_DAYS.items():
        for name in names:
            office = next(office for pattern, office in CA_KINDS if re.search(pattern, name))
            sheet = fastexcel.read_excel(raw.cached(SOURCE, CA_SOV.format(path=path.format(name))).path)
            district = columns = previous = None
            for row in sheet.load_sheet(0, header_row=None).to_polars().iter_rows():
                cells = [None if cell is None else re.sub(r"\s+", " ", str(cell)).strip() or None for cell in row]
                filled = [(index, cell) for index, cell in enumerate(cells) if cell is not None]
                if len(filled) == 1 and (title := CA_TITLE.match(filled[0][1])):
                    district, columns = title[1], None
                elif (
                    previous is not None
                    and filled
                    and filled[0][0] > 0
                    and all(CA_PARTY.fullmatch(cell) for _, cell in filled)
                ):
                    columns = [
                        (
                            index,
                            re.sub(r"\s*\(write-in\)$", "", who).rstrip("*").strip(),
                            CA_CODES.get(found[1], found[1]),
                            cell.endswith("(W/I)") or who.endswith("(write-in)"),
                        )
                        for index, cell in filled
                        if (found := CA_PARTY.fullmatch(cell))
                        and index < len(previous)
                        and (who := previous[index]) is not None
                        and not who.lower().startswith("votes not cast")
                    ]
                elif columns and (fips := counties.get(("06", re.sub(r"[^a-z]", "", (cells[0] or "").lower())))):
                    rows.extend(
                        (day, office, district, party, writein, fips, choice, int(float(value)))
                        for index, choice, party, writein in columns
                        if (value := cells[index]) is not None
                    )
                previous = cells
    ca = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "office": pl.String,
            "code": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "fips": pl.String,
            "choice": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(code=pl.col("code").cast(pl.Int64).cast(pl.String))
    for (day, office), lines in ca.group_by("day", "office"):
        print(
            day, office, lines["code"].n_unique(), "districts,", lines.select("code", "choice").n_unique(), "candidates"
        )
    federal = ca.filter(pl.col("office").is_in(["house", "us-senate"]))
    PARSED.mkdir(parents=True, exist_ok=True)
    federal.select(
        year=pl.col("day").dt.year(),
        chamber=pl.concat_str(
            pl.col("office").replace({"us-senate": "senate"}),
            pl.when(pl.col("day").dt.year() < 2012).then(
                pl.concat_str(
                    pl.lit(":"),
                    pl.col("party").replace_strict(
                        {code: word for code, (_, word, *_) in PARTIES.items()}, default=None
                    ),
                )
            ),
            ignore_nulls=True,
        ),
        day="day",
        special=pl.lit(False),
        state=pl.lit("CA"),
        district=pl.col("code").cast(pl.Int64),
        candidate="choice",
        writein="writein",
        votes="votes",
        measure=pl.lit(1, pl.Int16),
        code="fips",
        kind=pl.lit("county"),
        source=pl.lit("CA"),
    ).filter(pl.col("chamber").str.contains(":") | (pl.col("year") >= 2012)).write_parquet(PARSED / "congress.parquet")
    partisan = pl.col("day").dt.year() < 2012
    frames.append(
        ca.filter(~pl.col("office").is_in(["house", "us-senate"]))
        .with_columns(party=pl.when(partisan).then("party").otherwise(pl.lit("")), label="party")
        .filter(pl.col("party").is_in(list(PARTIES)))
        .select(
            state=pl.lit("CA"),
            day="day",
            office="office",
            code="code",
            party="party",
            label=pl.when(pl.col("label").is_in(list(PARTIES))).then("label"),
            fips="fips",
            scheme=pl.lit("us.county"),
            unit="fips",
            choice="choice",
            writein=pl.lit(False),
            votes="votes",
        )
    )
    (
        pl.concat(frames)
        .group_by("state", "day", "office", "code", "party", "fips", "scheme", "unit", "choice", "writein")
        .agg(pl.col("label").drop_nulls().first(), pl.col("votes").sum())
        .sort("state", "day", "office", "code", "party", "fips", "unit", "choice")
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative and statewide primaries and runoffs by precinct from state canvasses: "
            "North Carolina 2002–2022, California 2004–2022, Minnesota 2016–2022, and Washington 2015–2022",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for state, (_, _, publisher, title, url) in STATES.items():
            source = sources[state] = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        for day in NC_DAYS:
            cs.artifact(raw.cached(SOURCE, FILES.format(day=day)), sources["NC"])
        for day in MN_DAYS:
            for file in ("allracesbyprecinct", "PrctTbl"):
                cs.artifact(raw.cached(SOURCE, MN_FILES.format(day=day, name=file)), sources["MN"])
        for day in WA_DAYS:
            for url in (wa_local.FILES[day], wa_local.PRECINCTS.format(day=day)):
                cs.artifact(raw.cached(wa_local.SOURCE, url), sources["WA"])
        for path, names in CA_DAYS.values():
            for file in names:
                cs.artifact(raw.cached(SOURCE, CA_SOV.format(path=path.format(file))), sources["CA"])
        polities = {state: cs.polity(f"US-{state}") for state in STATES}
        keys = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e join contest c on c.id = e.entity_id
                   join changeset h on h.id = c.changeset_id
                   where e.scheme = 'us.contest' and e.value ~ '^20[0-9-]+:(NC|MN|WA|CA):' and h.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        slugs: dict[str, UUID] = dict(
            conn.execute("select path, entity_id from slug where path ~ '^us/(nc|mn|wa|ca)/'").fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code ~ '^(37|27|53|06)'"
            ).fetchall()
        }
        nominees: dict[tuple[str, int, str, str | None, UUID, str, str], set[UUID]] = {}
        for value, ballot, person, party in conn.execute(
            """select e.value, coalesce(d.ballot_name, n.text, o.ballot_label), d.person_id, o.party_id
               from external_id e
               join contest_option o on o.contest_id = e.entity_id
               join candidacy d on d.option_id = o.id and d.role = 'head'
               left join name n on n.entity_id = d.person_id and n.is_preferred and n.lang = 'en'
               where e.scheme = 'us.contest'
                 and e.value ~ '^20[0-2][0-9]-11-[0-9]{2}:(NC|MN|WA|CA):[a-z-]+(:[0-9]+(-[a-z])?(:[0-9])?:g)?$'
                 and o.party_id is not null"""
        ).fetchall():
            parts = value.split(":")
            words = [
                word
                for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", ballot).lower().split()
                if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
            ]
            if words:
                signature = (
                    parts[1],
                    int(parts[0][:4]),
                    parts[2],
                    (parts[3] + (f":{parts[4]}" if len(parts) == 6 else "")) if len(parts) > 3 else None,
                    party,
                    words[0][0],
                    slug(words[-1]),
                )
                nominees.setdefault(signature, set()).add(person)
        parties = {code: cs.key("us.party", key) for code, (key, *_) in PARTIES.items() if key}
        systems = {
            key: cs.key("us.electoral_system", key) for key in ("fptp", "top_two", "two_round_40", "two_round_30")
        }
        offices = {
            (state, office): cs.key("us.office", f"{office}:{state}")
            for state in STATES
            for office in {office for office, _ in OFFICES.values()}
            if not lines.filter(pl.col("state") == state, pl.col("office") == office).is_empty()
        }
        chambers: dict[str, str] = dict(
            conn.execute(
                """select e.value, n.text from external_id e
                   join name n on n.entity_id = e.entity_id and n.is_preferred and n.lang = 'en'
                   where e.scheme = 'us.body' and e.value ~ '^(NC|MN|WA|CA):(house|senate|assembly)$'"""
            ).fetchall()
        )
        bodies = {
            (state, office): cs.key("us.body", f"{state}:{office}")
            for state, office in lines.filter(pl.col("code").is_not_null())
            .select("state", "office")
            .unique()
            .iter_rows()
        }
        if cs.minted & {*parties.values(), *systems.values(), *offices.values(), *bodies.values()}:
            raise ValueError("the parties, systems, offices, and bodies must already exist")
        titles = dict(OFFICES.values())
        elections: dict[tuple[str, date], UUID] = {}
        for state, day, first in [
            *(("NC", day, first) for day, first in NC_DAYS.items()),
            *(("MN", day, None) for day in MN_DAYS),
            *(("WA", day, None) for day in WA_DAYS),
            *(("CA", day, None) for day in CA_DAYS),
        ]:
            kind = "runoff" if first else "primary"
            election = elections[(state, day)] = cs.key("us.election", f"{day}:{state}:{kind}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polities[state],
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    election,
                    f"{day:%B} {day.day}, {day.year} {STATES[state][1]} primary {'runoff ' if first else ''}election",
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-{kind}", entity_id=election)
        later = {(first, day) for day, first in NC_DAYS.items() if first}
        contests = {
            group: rows.sort("votes", descending=True)
            for group, rows in lines.group_by("state", "day", "office", "code", "party", "choice", "writein")
            .agg(pl.col("label").drop_nulls().first(), pl.col("votes").sum())
            .group_by("state", "day", "office", "code", "party")
        }
        held = {(day, office, code, party) for state, day, office, code, party in contests if state == "NC"}
        persons: dict[str, UUID] = {}
        options: dict[tuple[str, date, str, str | None, str, str], UUID] = {}
        sets: dict[tuple[str, date, str, str | None, str], UUID] = {}
        made: dict[tuple[str, date, str, str | None, str], UUID] = {}
        skipped = 0
        for (state, day, office, code, party), rows in sorted(
            contests.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            assert isinstance(state, str) and isinstance(day, date) and isinstance(office, str)
            assert isinstance(party, str) and (code is None or isinstance(code, str))
            first = NC_DAYS.get(day) if state == "NC" else None
            fips, place = STATES[state][0], STATES[state][1]
            _, word, etype, label = PARTIES[party]
            if code is not None:
                base = f"{day}:{state}:{office}:{code}:{etype}" + ("runoff" if first else "")
                taken = re.compile(rf"{re.escape(base)}(f\w*)?")
            else:
                base = f"{day}:{state}:{office}" + (f":{word}" if word else "")
                taken = re.compile(re.escape(base))
            if any(taken.fullmatch(value) for value in keys):
                skipped += 1
                continue
            contest = made[(state, day, office, code, party)] = cs.key("us.contest", base)
            previous = None
            if first is not None:
                previous = made.get((state, first, office, code, party))
                if previous is None:
                    prior = f"{first}:NC:{office}:{code}:{etype}" if code is not None else f"{first}:NC:{office}:{word}"
                    previous = next(
                        (
                            cs.key("us.contest", value)
                            for value in sorted(keys)
                            if re.fullmatch(rf"{re.escape(prior)}(f\w*)?", value)
                        ),
                        None,
                    )
            kind = "runoff" if first else "primary"
            stage = f"{label} primary" + (" runoff" if first else "")
            district, _, post = (code or "").partition(":")
            remainder = state == "WA" and day.year % 2 == 1
            cs.add(
                "contest",
                id=contest,
                election_id=elections[(state, day)],
                kind="office" if code is None else "body_seats",
                office_id=offices[(state, office)] if code is None else None,
                body_id=bodies[(state, office)] if code is not None else None,
                district_area_id=cs.area("us.state", fips)
                if code is None
                else cs.area("us.sldl" if office in ("house", "assembly") else "us.sldu", f"{state}:{district}"),
                magnitude=1,
                term="remainder" if remainder else "full",
                round_number=2 if first else 1,
                previous_round_id=previous,
                electoral_system_id=systems[
                    "top_two"
                    if party == ""
                    else "fptp"
                    if state in ("MN", "CA")
                    else "two_round_40"
                    if day.year < 2018
                    else "two_round_30"
                ],
                default_result_kind="counted",
                party_scope_id=parties.get(party),
            )
            if code is None:
                name(contest, f"{day.year} {place} {titles[office]} {stage}")
                path = f"us/{state.lower()}/{day}-{kind}/{office}" + (f"-{word}" if word else "")
            else:
                name(
                    contest,
                    f"{day.year} {chambers[f'{state}:{office}']} {stage}, District {district.replace('-', '').upper()}"
                    + (f", Position {post}" if post else "")
                    + (" (unexpired term)" if remainder else ""),
                )
                path = (
                    f"us/{state.lower()}/{day}-{kind}/{'state-' if office in ('house', 'senate') else ''}{office}-{district}"
                    + (f"-post-{post}" if post else "")
                    + f"-{slug(stage)}"
                )
            if slugs.setdefault(path, contest) != contest:
                raise ValueError(f"slug {path} is taken")
            cs.add("slug", path=path, entity_id=contest)
            result_set = sets[(state, day, office, code, party)] = cs.key(f"{SOURCE}.result_set", f"{base}:precinct")
            threshold = 40 if day.year < 2018 else 30
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[(state, day)],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=WA_METHOD
                if state == "WA"
                else CA_METHOD.format(
                    rule="The top two candidates advance to the general election."
                    if party == ""
                    else "The file does not mark winners, so the winner shown is the candidate with the most votes."
                )
                if state == "CA"
                else MN_METHOD
                if state == "MN"
                else METHOD.format(threshold=threshold),
            )
            total = rows["votes"].sum()
            ranked = rows.filter(~pl.col("writein"))
            matched: list[bool] = []
            for choice, writein, preference in rows.select("choice", "writein", "label").iter_rows():
                if writein:
                    option = cs.key("us.option", f"{base}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    options[(state, day, office, code, party, choice)] = option
                    continue
                ticket = re.fullmatch(r"(.+?) and (.+)", choice) if state == "MN" and office == "governor" else None
                names = [ticket[1], ticket[2]] if ticket else [choice]
                people = []
                for role, text in zip(("head", "running_mate"), names, strict=False):
                    words = [
                        word
                        for word in re.sub(r"\(.*?\)|\".*?\"|,", " ", text).lower().split()
                        if word.strip(".") not in ("jr", "sr", "ii", "iii", "iv")
                    ]
                    found = (
                        nominees.get(
                            (state, day.year, office, code, parties.get(preference), words[0][0], slug(words[-1])),
                            set(),
                        )
                        if role == "head" and preference in parties
                        else set()
                    )
                    local = f"{state}:{office if role == 'head' else 'lieutenant-governor'}:{code or ''}:{party}:{slug(text)}"
                    if len(found) == 1:
                        person = next(iter(found))
                    else:
                        person = persons.get(local) or cs.key(f"{SOURCE}.person", local)
                        if person in cs.minted and local not in persons:
                            cs.add("person", id=person)
                            name(person, text)
                    persons[local] = person
                    if role == "head":
                        matched.append(len(found) == 1)
                    people.append((role, text, person))
                option = cs.key("us.option", f"{base}:{people[0][2]}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="ticket" if ticket else "candidate",
                    party_id=parties.get(preference),
                    ballot_label=choice,
                )
                for role, text, person in people:
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{base}:{person}:{role}"),
                        person_id=person,
                        option_id=option,
                        role=role,
                        ballot_name=text,
                        is_independent=False,
                    )
                options[(state, day, office, code, party, choice)] = option
            if party == "":
                for choice in ranked["choice"][:2]:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[(state, day, office, code, party, choice)],
                        kind="advanced",
                    )
                continue
            votes = ranked["votes"].to_list()
            if not votes or (len(votes) > 1 and votes[0] == votes[1]):
                continue
            leader = options[(state, day, office, code, party, ranked["choice"][0])]
            runoff = state == "NC" and (
                any((day, later_day) in later and (later_day, office, code, party) in held for later_day in NC_DAYS)
                or any(
                    re.fullmatch(
                        rf"{later_day}:NC:{office}:" + (f"{code}:{etype}runoff" if code is not None else str(word)),
                        value,
                    )
                    for later_day in NC_DAYS
                    if (day, later_day) in later
                    for value in keys
                )
            )
            if first is None and runoff and len(votes) > 1:
                for choice in ranked["choice"][:2]:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[(state, day, office, code, party, choice)],
                        kind="advanced",
                    )
            elif (
                state in ("MN", "CA")
                or first is not None
                or len(votes) == 1
                or votes[0] * 100 > threshold * total
                or matched[0]
            ):
                cs.add("outcome", result_set_id=result_set, option_id=leader, kind="won")
        print(f"{len(made)} contests, {skipped} already held")
        frame = lines.join(
            pl.DataFrame(
                [
                    (state, day, office, code, party, choice, str(option), str(sets[(state, day, office, code, party)]))
                    for (state, day, office, code, party, choice), option in options.items()
                ],
                schema={
                    "state": pl.String,
                    "day": pl.Date,
                    "office": pl.String,
                    "code": pl.String,
                    "party": pl.String,
                    "choice": pl.String,
                    "option_id": pl.String,
                    "result_set_id": pl.String,
                },
                orient="row",
            ),
            on=["state", "day", "office", "code", "party", "choice"],
            nulls_equal=True,
        ).filter(pl.col("result_set_id").is_in([str(value) for value in sets.values() if value in cs.minted]))
        frame = frame.with_columns(
            flags=pl.when(pl.col("scheme") == "us.aggregate").then(UNALLOCATED).otherwise(0).cast(pl.Int16)
        )
        units: dict[str, UUID] = {}
        reused = 0
        for state, day, fips, scheme, unit, flags in (
            frame.select("state", "day", "fips", "scheme", "unit", "flags").unique().iter_rows()
        ):
            if unit in units:
                continue
            units[unit] = counties[fips][0] if scheme == "us.county" else cs.unit(scheme, unit)
            if units[unit] not in cs.minted:
                reused += 1
                continue
            cs.add(
                "reporting_unit",
                id=units[unit],
                polity_id=polities[state],
                scheme_code=scheme,
                code=unit,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="aggregate" if flags else "standard",
                valid_from=None if flags else day,
                valid_to=None if flags else day + timedelta(days=1),
            )
        print(f"{len(units)} units, {reused} already held")
        cs.frame(
            "result_value",
            frame.with_columns(unit_id=pl.col("unit").replace_strict({key: str(value) for key, value in units.items()}))
            .group_by("result_set_id", "unit_id", "option_id", "flags")
            .agg(value=pl.col("votes").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
            ),
        )
        held = [
            value
            for (value,) in conn.execute(
                """select e.value from external_id e
                   where e.scheme = 'us.contest' and e.value ~ '^20[0-2][0-9]-0[2-6]-[0-9]{2}:CA:(house|senate)'
                     and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                 where rs.contest_id = e.entity_id
                                   and s.publisher not in ('Federal Election Commission', %s))""",
                [STATES["CA"][2]],
            ).fetchall()
        ]
        congress = pl.read_parquet(PARSED / "congress.parquet").filter(
            ~pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("CA"),
                pl.when(pl.col("district").is_not_null())
                .then(
                    pl.concat_str(
                        pl.lit("house:"),
                        pl.col("district").cast(pl.String),
                        pl.col("chamber").str.strip_prefix("house"),
                    )
                )
                .otherwise("chamber"),
                separator=":",
            ).is_in(held)
        )
        medsl.attach(cs, conn, congress, sources)
        return cs.write()
