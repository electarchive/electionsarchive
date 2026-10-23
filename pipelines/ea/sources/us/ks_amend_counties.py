import html
import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.ks_pri_2014_clerks import LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ks_amend_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Constitutional amendments: official results by county"
CONTESTS = {
    2012: "2012-11-06:KS:question:wikipedia:kansas-boat-property-tax-amendment-hcr-5017",
    2014: "2014-11-04:KS:question:wikipedia:kansas-charitable-gaming-measure-scr-1618",
    2016: "2016-11-08:KS:question:wikipedia:kansas-right-to-hunt-and-fish-constitutional-amendment-1",
    2019: "2019-11-05:KS:question:wikipedia:kansas-eliminate-revision-of-census-population-amendment",
}
DOUGLAS = "https://web.archive.org/web/{}id_/http://apps.douglascountyks.org/mycounty/voting-and-elections/docs/electionresults/electionresults_general_{}.pdf"
FILES = [
    (
        2012,
        "20177",
        "Shawnee",
        "https://cdn.snco.gov/elections/prev/20121106-general.htm",
        "Constitutional Amendment",
        0,
    ),
    (
        2019,
        "20177",
        "Shawnee",
        "https://cdn.snco.gov/elections/prev/2019_general_results.pdf",
        "Constitutional Amendment",
        0,
    ),
    (
        2016,
        "20209",
        "Wyandotte",
        (
            "https://static1.squarespace.com/static/56606b47e4b0b9403ad6ff96/t/582e2fab8419c28d0b746cea/1479421867718/"
            "GEMS+ELECTION+RESULTS+11-17.pdf"
        ),
        "Constitutional Amend.",
        0,
    ),
    (
        2019,
        "20091",
        "Johnson",
        "https://www.jocoelection.org/sites/default/files/2022-05/2019_general_election_-_official_final_results.pdf",
        "Question Submitted",
        0,
    ),
    (2012, "20045", "Douglas", DOUGLAS.format("20200928012636", 2012), "Constitutional Amendment", 0),
    (2019, "20045", "Douglas", DOUGLAS.format("20210922000455", 2019), "Constitutional Amend", 60),
    *(
        (
            year,
            "20173",
            "Sedgwick",
            f"https://www.sedgwickcounty.org/elections/election-results/{year}-general/",
            label,
            0,
        )
        for year, label in (
            (2012, "Consitutional Amendment"),
            (2014, "Constitutional Amendment"),
            (2016, "Constitutional Amendment"),
        )
    ),
]
SCANNED = [
    (2014, "20177", "Shawnee", "https://cdn.snco.gov/elections/prev/2014GeneralElectionSOVC.pdf", 42497, 15901),
    (
        2016,
        "20177",
        "Shawnee",
        "https://cdn.snco.gov/elections/document/2016_General_Final_Official_Results.pdf",
        56106,
        15155,
    ),
    (
        2014,
        "20209",
        "Wyandotte",
        "https://www.wycovotes.org/s/2014-General-Abstract-November-4-2014.pdf",
        19021,
        7102,
    ),
    (2014, "20045", "Douglas", DOUGLAS.format("20200928012636", 2014), 27371, 8547),
    (2016, "20045", "Douglas", DOUGLAS.format("20200928012636", 2016), 31653, 17214),
]


def fetch() -> None:
    for url in [*(file[-3] for file in FILES), *(file[3] for file in SCANNED)]:
        raw.fetch(SOURCE, url, suffix=Path(url.split("?")[0]).suffix or ".html")


def parse() -> None:
    rows = []
    for year, fips, county, url, label, column in FILES:
        path = raw.cached(SOURCE, url).path
        if path.suffix == ".pdf":
            lines = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True
            ).stdout.splitlines()
        else:
            text = re.sub(
                r"<script.*?</script>|<style.*?</style>", "", path.read_text(errors="replace"), flags=re.DOTALL
            )
            lines = html.unescape(re.sub(r"<[^>]+>", "\n", text)).splitlines()
        lines = [line[column:].strip() for line in lines if line[column:].strip()]
        start = next(k for k, line in enumerate(lines) if line == label)
        window = " ".join(lines[start + 1 : start + 16])
        votes = {
            answer: int(re.search(rf"(?i)\b{answer}\b[\s.]*([\d,]+)", window)[1].replace(",", ""))
            for answer in ("Yes", "No")
        }
        print(year, county, votes)
        rows += [(year, fips, county, url, answer.upper(), value) for answer, value in votes.items()]
    for year, fips, county, url, yes, no in SCANNED:
        rows += [(year, fips, county, url, "YES", yes), (year, fips, county, url, "NO", no)]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "fips": pl.String,
            "county": pl.String,
            "url": pl.String,
            "answer": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas constitutional amendments of 2012, 2014, 2016, and 2019 by county, from the county "
            "election offices' official results where a text copy survives",
            polity="US-KS",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        artifacts = {
            url: cs.artifact(raw.cached(SOURCE, url), source)
            for url in [*(file[-3] for file in FILES), *(file[3] for file in SCANNED)]
        }
        for year, key in CONTESTS.items():
            counted = frame.filter(pl.col("year") == year)
            election, contest, statewide = conn.execute(
                """select rs.election_id, rs.contest_id,
                          jsonb_object_agg(upper(o.ballot_label), t.votes)
                   from external_id x
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join lateral (select v.option_k, sum(v.value) as votes from result_value v
                                 where v.result_set_k = rs.k and v.measure = 1 group by v.option_k) t on true
                   join contest_option o on o.k = t.option_k
                   where x.scheme = 'us.contest' and x.value = %s
                   group by 1, 2""",
                [key],
            ).fetchone()
            options = dict(
                conn.execute(
                    "select upper(ballot_label), id from contest_option where contest_id = %s", [contest]
                ).fetchall()
            )
            for answer in ("YES", "NO"):
                share = counted.filter(pl.col("answer") == answer)["votes"].sum()
                if share > statewide[answer]:
                    raise ValueError(f"{key}: counties give {answer} {share}, above the statewide {statewide[answer]}")
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=source,
                status="active",
                is_default=False,
                completeness=round(counted["votes"].sum() / (statewide["YES"] + statewide["NO"]), 4),
            )
            names = counted["county"].unique(maintain_order=True).to_list()
            cs.add(
                "note",
                entity_id=result_set,
                kind="caveat",
                body_md=f"County totals for {len(names)} of Kansas's 105 counties ({', '.join(names)}), from each "
                "county election office's official results; the Secretary of State published statewide totals only.",
            )
            for fips, url, answer, votes in counted.select("fips", "url", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", fips),
                    option_id=options[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
                if answer == "YES":
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"url": url, "artifact": str(artifacts[url]), "unit": fips},
                    )
        return cs.write()
