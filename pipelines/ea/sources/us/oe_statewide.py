import io
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PARTIES = {
    "D": "DEMOCRAT",
    "R": "REPUBLICAN",
    "DFL": "DEMOCRATIC-FARMER-LABOR",
    "L": "LIBERTARIAN",
    "LBT": "LIBERTARIAN",
    "LIBERTARIN": "LIBERTARIAN",
    "G": "GREEN",
    "GP": "GREEN",
    "GRN": "GREEN",
    "GRE": "GREEN",
    "WGR": "GREEN",
    "C": "CONSTITUTION",
    "CST": "CONSTITUTION",
    "CNST": "CONSTITUTION",
    "CON": "CONSTITUTION",
    "I": "INDEPENDENT",
    "IND": "INDEPENDENT",
    "AI": "AMERICAN INDEPENDENT",
    "PF": "PEACE AND FREEDOM",
    "NLP": "NATURAL LAW",
    "UST": "U.S. TAXPAYERS",
    "WF": "WORKING FAMILIES",
    "WOR": "WORKING FAMILIES",
    "W": "WORKING FAMILIES",
    "RTL": "RIGHT TO LIFE",
    "IP": "INDEPENDENCE",
    "LMN": "LEGAL MARIJUANA NOW",
    "GLC": "GRASSROOTS",
    "P": "PROGRESSIVE",
    "PG": "PACIFIC GREEN",
    "AMC": "AMERICAN",
    "UNC": "UNITED CITIZENS",
    "CTRY": "COUNTRY",
    "BLUE ENIGM": "BLUE ENIGMA",
    "IND OF DEL": "INDEPENDENT PARTY OF DELAWARE",
    "INDEPENDENCE PARTY O": "INDEPENDENCE",
    "NOMINATED BY PETITION": "INDEPENDENT",
    "NONE": "",
    "NP-W": "",
    "WRITE-IN": "",
    "WRITE-IN GRN": "",
    "WI": "",
    "NA": "",
    "AME": "",
    "AMR": "",
}
STATE_PARTIES = {("NY", "CON"): "CONSERVATIVE", ("NY", "IND"): "INDEPENDENCE"}
METHOD = (
    "County totals are the sums of the rows in OpenElections' results file for the state and year, which is a "
    "volunteer transcription of the state's official returns. The winner shown is the candidate with the most votes "
    "in those sums."
)
MISSING = "OpenElections' file has no rows for {counties} in this race, so those votes are missing from these totals."
CLOSE = (
    "The race was within 0.5% in these sums, closer than transcription differences from the official canvass can be "
    "trusted to settle, so no winner is recorded."
)
MISSOURI = "https://web.archive.org/web/{stamp}id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/AllRacesGeneralNovember{year}.pdf"
NEW_MEXICO = "https://nm.elstats2.civera.com/eng/files/serve/18"
OREGON = (
    "https://web.archive.org/web/2025id_/https://records.sos.state.or.us/ORSOSWebDrawer/Record/6873690/File/document"
)
NEW_MEXICO_2014 = "https://nm.elstats2.civera.com/eng/files/serve/791"
SOUTH_CAROLINA = "https://scvotes.gov/wp-content/uploads/2022/08/Election_Report_2006.pdf"
NORTH_DAKOTA = (
    "https://vip.sos.nd.gov/pdfs/Abstracts%20by%20Year/2000%20through%202008%20Statewide%20Election%20Results/2000/"
    "General%20Election%2011-07-2000.pdf"
)
ILLINOIS = "https://www.elections.il.gov/Downloads/ElectionOperations/VoteTotals/2014GEOfficialVote.pdf"
MICHIGAN = (
    "https://web.archive.org/web/20160614235202id_/http://miboecfr.nictusa.com/election/results.old/02GEN/02GENall.html"
)
WISCONSIN = "https://docs.legis.wisconsin.gov/misc/lrb/blue_book/2007_2008/960_elections.pdf"
MONTANA = "https://sosmt.gov/wp-content/uploads/attachments/2012_General_Canvass.pdf"
ARKANSAS = "https://web.archive.org/web/20180216174010id_/http://results.enr.clarityelections.com/AR/53237/149792/json/sum.json"
CANVASSES = {
    "MO": (
        "Missouri Secretary of State",
        "Official election returns",
        "https://www.sos.mo.gov/elections/resultsandstats",
    ),
    "NM": ("New Mexico Secretary of State", "Election statistics archive", "https://electionresults.sos.nm.gov"),
    "OR": (
        "Oregon Secretary of State",
        "Official results, abstract of votes",
        "https://sos.oregon.gov/elections/Pages/electionhistory.aspx",
    ),
    "AR": (
        "Arkansas Secretary of State",
        "2014 General Election, official results",
        "https://results.enr.clarityelections.com/AR/53237/149792/Web01/en/summary.html",
    ),
    "SC": ("South Carolina Election Commission", "Election results", "https://scvotes.gov/election-results/"),
    "ND": (
        "North Dakota Secretary of State",
        "Official abstracts of votes cast at the general elections, 2000–2008",
        "https://www.sos.nd.gov/elections/election-results",
    ),
    "IL": (
        "Illinois State Board of Elections",
        "Official Vote Cast at the General Election, November 4, 2014",
        ILLINOIS,
    ),
    "MI": (
        "Michigan Department of State, Bureau of Elections",
        "Official election results",
        "https://mielections.us/election/results/",
    ),
    "MT": ("Montana Secretary of State", "2012 Statewide General Election Canvass", MONTANA),
    "WI": (
        "Wisconsin Legislative Reference Bureau",
        "Wisconsin Blue Book 2007-2008",
        "https://docs.legis.wisconsin.gov/misc/lrb/blue_book/2007_2008",
    ),
}
DECLARATIONS = {
    "2000-11-07:MO:lieutenant-governor": (MISSOURI.format(stamp=2024, year=2000), {"page": 1}),
    "2000-11-07:MO:secretary-of-state": (MISSOURI.format(stamp=2024, year=2000), {"page": 2}),
    "2000-11-07:MO:treasurer": (MISSOURI.format(stamp=2024, year=2000), {"page": 2}),
    "2000-11-07:MO:attorney-general": (MISSOURI.format(stamp=2024, year=2000), {"page": 2}),
    "2004-11-02:MO:treasurer": (MISSOURI.format(stamp=2025, year=2004), {"page": 2}),
    "2006-11-07:MO:auditor": (MISSOURI.format(stamp=2025, year=2006), {"page": 1}),
    "2008-11-04:MO:lieutenant-governor": (MISSOURI.format(stamp=2025, year=2008), {"page": 1}),
    "2002-11-05:NM:secretary-of-state": (NEW_MEXICO, {"document": 18, "page": 1}),
    "2002-11-05:NM:commissioner-of-public-lands": (NEW_MEXICO, {"document": 18, "page": 2}),
    "2012-11-06:OR:attorney-general": (OREGON, {"page": 6}),
    "2014-11-04:AR:attorney-general": (ARKANSAS, {"contest": "Attorney General"}),
    "2014-11-04:AR:auditor": (ARKANSAS, {"contest": "Auditor of State"}),
    "2014-11-04:AR:treasurer": (ARKANSAS, {"contest": "State Treasurer"}),
    "2014-11-04:AR:lieutenant-governor": (ARKANSAS, {"contest": "Lieutenant Governor"}),
    "2014-11-04:AR:secretary-of-state": (ARKANSAS, {"contest": "Secretary of State"}),
    "2014-11-04:AR:commissioner-of-state-lands": (ARKANSAS, {"contest": "Commissioner of State Lands"}),
    "2006-11-07:SC:lieutenant-governor": (SOUTH_CAROLINA, {"pages": [43, 149]}),
    "2006-11-07:SC:superintendent-of-education": (SOUTH_CAROLINA, {"pages": [48, 149]}),
    "2014-11-04:NM:commissioner-of-public-lands": (NEW_MEXICO_2014, {"document": 791, "page": 1}),
    "2000-11-07:ND:public-service-commissioner": (NORTH_DAKOTA, {"page": 1}),
    "2014-11-04:IL:treasurer": (ILLINOIS, {"page": 54}),
    "2002-11-05:MI:attorney-general": (MICHIGAN, {"section": "Attorney General 4 Year Term (1) Position"}),
    "2012-11-06:MT:superintendent-of-public-instruction": (MONTANA, {"page": 9}),
    "2006-11-07:WI:treasurer": (WISCONSIN, {"page": 9, "printed_page": 932}),
    "2006-11-07:WI:attorney-general": (WISCONSIN, {"page": 7, "printed_page": 930}),
}
CLOSE_WINNER = (
    "The race was within 0.5% in these sums. {winner} was declared the winner in the state's official canvass and "
    "took office."
)
ABSENT_WINNER = (
    "{county} is missing from these sums, but it cast {ballots:,} votes for {office} in the same election. Since this "
    "is fewer than {winner}'s {lead:,}-vote lead, the missing votes could not change the result. {winner} was "
    "declared the winner."
)
CLOSE_WINNERS = {
    "2006-11-07:SC:lieutenant-governor": ("Andre Bauer", "André Bauer"),
    "2006-11-07:SC:superintendent-of-education": ("Jim Rex", "Jim Rex"),
    "2014-11-04:NM:commissioner-of-public-lands": ("Aubrey Dunn", "Aubrey Dunn"),
    "2000-11-07:ND:public-service-commissioner": ("Tony Clark", "Tony Clark"),
    "2014-11-04:IL:treasurer": ("Michael W. Frerichs", "Michael Frerichs"),
    "2006-11-07:WI:treasurer": ("Dawn Marie Sass", "Dawn Marie Sass"),
    "2006-11-07:WI:attorney-general": ("J.B. Van Hollen", "J.B. Van Hollen"),
    "2002-11-05:MI:attorney-general": ("Mike Cox", "Mike Cox"),
    "2012-11-06:MT:superintendent-of-public-instruction": ("Denise Juneau", "Denise Juneau"),
}
ABSENT_WINNERS = {
    "2000-11-07:MO:secretary-of-state": ("Matt Blunt", "Matt Blunt", "Shannon County", 3726, "governor", 142506),
    "2000-11-07:MO:attorney-general": (
        "Jeremiah W. (Jay) Nixon",
        "Jay Nixon",
        "Shannon County",
        3726,
        "governor",
        521677,
    ),
    "2000-11-07:MO:treasurer": ("Nancy Farmer", "Nancy Farmer", "Shannon County", 3726, "governor", 129217),
    "2000-11-07:MO:lieutenant-governor": ("Joe Maxwell", "Joe Maxwell", "Shannon County", 3726, "governor", 187932),
    "2004-11-02:MO:treasurer": ("Sarah Steelman", "Sarah Steelman", "Washington County", 9177, "governor", 128791),
    "2008-11-04:MO:lieutenant-governor": (
        "Peter Kinder",
        "Peter Kinder",
        "Christian County",
        37697,
        "president",
        57214,
    ),
    "2002-11-05:NM:secretary-of-state": (
        "Rebecca D. Vigil-Giron",
        "Rebecca Vigil-Giron",
        "Cibola County",
        5525,
        "governor",
        69808,
    ),
    "2002-11-05:NM:commissioner-of-public-lands": (
        "Patrick H. Lyons",
        "Patrick Lyons",
        "Cibola County",
        5525,
        "governor",
        13414,
    ),
    "2012-11-06:OR:attorney-general": (
        "Ellen Rosenblum",
        "Ellen Rosenblum",
        "Yamhill County",
        42907,
        "president",
        278465,
    ),
    "2014-11-04:AR:attorney-general": (
        "Leslie Rutledge",
        "Leslie Rutledge",
        "Arkansas County",
        5096,
        "governor",
        70152,
    ),
    "2014-11-04:AR:auditor": ("Representative Andrea Lea", "Andrea Lea", "Arkansas County", 5096, "governor", 163873),
    "2014-11-04:AR:treasurer": (
        "Circuit Clerk Dennis Milligan",
        "Dennis Milligan",
        "Arkansas County",
        5096,
        "governor",
        157884,
    ),
    "2014-11-04:AR:lieutenant-governor": (
        "Congressman Tim Griffin",
        "Tim Griffin",
        "Arkansas County",
        5096,
        "governor",
        154990,
    ),
    "2014-11-04:AR:secretary-of-state": (
        "Secretary Of State Mark Martin",
        "Mark Martin",
        "Arkansas County",
        5096,
        "governor",
        210409,
    ),
    "2014-11-04:AR:commissioner-of-state-lands": (
        "Commissioner John Thurston",
        "John Thurston",
        "Arkansas County",
        5096,
        "governor",
        169068,
    ),
}


def fetch() -> None:
    openelections.fetch()
    for url in {url for url, _ in DECLARATIONS.values()}:
        raw.fetch(SOURCE, url, suffix=next((end for end in (".json", ".html") if url.endswith(end)), ".pdf"))


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for state, year, path in openelections.picks():
        text = raw.cached(
            openelections.SOURCE,
            openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path),
        ).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            if column.strip().lower() not in renamed.values():
                renamed[column] = column.strip().lower()
        frame = frame.rename(renamed)
        if "votes" not in frame.columns and "total_votes" in frame.columns:
            frame = frame.rename({"total_votes": "votes"})
        if "votes" not in frame.columns and "election_day_votes" in frame.columns:
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).cast(pl.Int64, strict=False).fill_null(0) for column in openelections.CHANNELS
                ).cast(pl.String)
            )
        if "county" not in frame.columns and {"jurisdiction", "reporting_level"} <= set(frame.columns):
            frame = frame.filter(pl.col("reporting_level") == "county").rename({"jurisdiction": "county"})
        if not {"county", "office", "candidate", "votes"} <= set(frame.columns):
            continue
        office = pl.col("office").fill_null("").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars()
        frames.append(
            frame.select(
                year=pl.lit(year),
                day=pl.lit(date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)),
                state=pl.lit(state),
                office=office.str.replace(r"\s*\((UNEXPIRED|SPECIAL)[^)]*\)$|\s+-?\s*UNEXPIRED.*$", ""),
                special=office.str.contains("SPECIAL|UNEXPIRED"),
                county=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(
                    {**fips.get(state, {}), **({"kansascity": "2938000"} if state == "MO" else {})}, default=None
                ),
                candidate=pl.col("candidate")
                .fill_null("")
                .str.to_uppercase()
                .str.replace_all(r"\s+", " ")
                .str.strip_chars(),
                party=(pl.col("party") if "party" in frame.columns else pl.lit(None, pl.String))
                .str.to_uppercase()
                .str.strip_chars()
                .str.replace(r"\s*[,/].*$", "")
                .str.replace(r" PARTY$", "")
                .str.replace(r"^(WRITE-?IN|NP\b).*$", "")
                .replace({code: name for (place, code), name in STATE_PARTIES.items() if place == state})
                .replace(PARTIES)
                .replace(medsl_statewide.LABELS),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = (
        pl.concat(frames)
        .filter(
            pl.col("votes").is_not_null(),
            pl.col("county").is_not_null(),
            pl.col("candidate") != "",
            ~pl.col("candidate").str.contains(openelections.NOT_COUNTED),
            ~pl.col("candidate").str.contains(medsl_statewide.PSEUDO),
        )
        .with_columns(
            party=pl.when(pl.col("party") != "").then("party"),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            candidate=pl.when(
                pl.col("candidate").str.contains(r"^[^,]+, [^,]+$")
                & ~pl.col("candidate").str.contains(r", (JR|SR|II|III|IV)\.?$")
            )
            .then(pl.col("candidate").str.replace(r"^([^,]+), (.+)$", "$2 $1"))
            .otherwise("candidate"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
    )
    named = []
    for (office,) in frame.select("office").unique().iter_rows():
        for pattern, title in medsl_statewide.OFFICES:
            if (
                re.fullmatch(pattern, office)
                and title != "Governor"
                and "Justice" not in title
                and "Judge" not in title
            ):
                named.append((office, title))
                break
    frame = frame.join(pl.DataFrame(named, schema=["office", "title"], orient="row"), on="office").with_columns(
        post=pl.lit(None, pl.String),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code="county",
        measure=pl.lit(1, pl.Int16),
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    counties = pl.DataFrame(
        [(state, len(set(codes.values()))) for state, codes in fips.items()], schema=["state", "counties"], orient="row"
    )
    races = (
        frame.group_by(*contest, "candidate", "writein")
        .agg(
            pl.col("votes").sum(),
            reach=pl.col("county").filter(pl.col("votes") > 0, pl.col("county") != "2938000").n_unique(),
        )
        .group_by(contest)
        .agg(
            pl.col("reach").max(),
            top=pl.col("votes").filter(~pl.col("writein")).sort(descending=True).head(2).sum() / pl.col("votes").sum(),
        )
        .join(
            frame.group_by(contest).agg(
                pl.col("county")
                .filter(pl.col("votes") > 0, pl.col("county") != "2938000")
                .unique()
                .sort()
                .alias("present"),
                pl.col("magnitude").max(),
            ),
            on=contest,
            nulls_equal=True,
        )
        .join(counties, on="state")
        .filter(
            pl.col("reach") >= 0.9 * pl.col("counties"),
            (pl.col("title").is_in(list(medsl_statewide.EXECUTIVE)) | (pl.col("top") >= 0.8)),
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.join(races.select(contest), on=contest, nulls_equal=True).group_by(
        *contest, "magnitude", "kind", "code", "county", "candidate", "party", "writein", "measure", maintain_order=True
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "returns.parquet")
    races.select(*contest, "magnitude", "present", "counties").write_parquet(PARSED / "races.parquet")
    print(races.group_by("state").len().sort("state").rows())


def emit() -> Path:
    states = pl.read_parquet(PARSED / "races.parquet")["state"].unique().sort().to_list()

    def cite(cs: Changeset) -> dict[str, UUID]:
        sources = {}
        for state in states:
            title = f"{state} county-level results"
            sources[state] = cs.source("OpenElections", title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
        for state, _, path in openelections.picks():
            if state in sources:
                cs.artifact(
                    raw.cached(
                        openelections.SOURCE,
                        openelections.REPOSITORY.format(
                            state=state.lower(), commit=openelections.COMMITS[state], path=path
                        ),
                    ),
                    sources[state],
                )
        canvasses = {}
        for state, (publisher, title, url) in CANVASSES.items():
            canvasses[state] = cs.source(publisher, title)
            if canvasses[state] in cs.minted:
                cs.add(
                    "source",
                    id=canvasses[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        cited = {
            (str(entity), str(source))
            for entity, source in cs.conn.execute("select entity_id, source_id from citation").fetchall()
        }
        for url in {url for url, _ in DECLARATIONS.values()}:
            cs.artifact(
                raw.cached(SOURCE, url), canvasses[next(k for k, (u, _) in DECLARATIONS.items() if u == url)[11:13]]
            )
        for key, (url, locator) in DECLARATIONS.items():
            source = canvasses[key[11:13]]
            ((result_set,),) = cs.conn.execute(
                """select rs.id from external_id x join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            if (str(result_set), str(source)) not in cited:
                cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
        return sources

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Statewide executive and board elections 2000–2014 by county from OpenElections' results files",
        cite=cite,
        method=METHOD,
        absent=MISSING,
        close=CLOSE,
        declared={
            **{key: (label, CLOSE_WINNER.format(winner=winner)) for key, (label, winner) in CLOSE_WINNERS.items()},
            **{
                key: (
                    label,
                    ABSENT_WINNER.format(winner=winner, county=county, ballots=ballots, office=office, lead=lead),
                )
                for key, (label, winner, county, ballots, office, lead) in ABSENT_WINNERS.items()
            },
            "2006-11-07:MO:auditor": (
                "Susan Montee",
                (
                    "Camden and Dade counties are missing from these sums, but together they cast 19,399 votes for U.S. "
                    "Senator in the same election. Since this is fewer than Susan Montee's 201,959-vote lead, the missing "
                    "votes could not change the result. Susan Montee was declared the winner."
                ),
            ),
        },
    )
