import html
import re
import subprocess
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ks_amend_counties as amend
from ea.sources.us.ks_pri_2014_clerks import LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ks_retention_counties"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "Judicial retention questions: official results by county"
DAYS = {2014: "2014-11-04", 2016: "2016-11-08"}
SUPREME = "justice-of-the-supreme-court"
APPEALS = "judge-of-the-court-of-appeals"
SEDGWICK = "https://www.sedgwickcounty.org/elections/election-results/{}-general/"
WYANDOTTE_2016 = next(file[3] for file in amend.FILES if file[0] == 2016 and file[2] == "Wyandotte")
URLS = {
    **{(file[0], file[1]): file[3] for file in amend.FILES},
    **{(file[0], file[1]): file[3] for file in amend.SCANNED},
}
COUNTIES = {"20177": "Shawnee", "20209": "Wyandotte", "20045": "Douglas", "20173": "Sedgwick"}
# Haiku 5.5 read of the scanned canvass pages, each row's Yes plus No equal to the printed Total Votes line.
SCANNED = [
    (2014, '20177', 'judge-of-the-court-of-appeals', 10, 34460, 20307),
    (2014, '20177', 'judge-of-the-court-of-appeals', 11, 34942, 19782),
    (2014, '20177', 'justice-of-the-supreme-court', 4, 33403, 23639),
    (2014, '20177', 'judge-of-the-court-of-appeals', 12, 32127, 22133),
    (2014, '20177', 'justice-of-the-supreme-court', 6, 31379, 24735),
    (2014, '20177', 'judge-of-the-court-of-appeals', 13, 33152, 21276),
    (2014, '20177', 'judge-of-the-court-of-appeals', 1, 33717, 21569),
    (2014, '20177', 'judge-of-the-court-of-appeals', 4, 32528, 22305),
    (2014, '20177', 'judge-of-the-court-of-appeals', 5, 33208, 21559),
    (2014, '20177', 'judge-of-the-court-of-appeals', 7, 35099, 19571),
    (2014, '20209', 'justice-of-the-supreme-court', 4, 14894, 8283),
    (2014, '20209', 'justice-of-the-supreme-court', 6, 14688, 8189),
    (2014, '20209', 'judge-of-the-court-of-appeals', 1, 15044, 7547),
    (2014, '20209', 'judge-of-the-court-of-appeals', 4, 15259, 7726),
    (2014, '20209', 'judge-of-the-court-of-appeals', 5, 15185, 7657),
    (2014, '20209', 'judge-of-the-court-of-appeals', 7, 16314, 6603),
    (2014, '20209', 'judge-of-the-court-of-appeals', 10, 15689, 7105),
    (2014, '20209', 'judge-of-the-court-of-appeals', 11, 15804, 6995),
    (2014, '20209', 'judge-of-the-court-of-appeals', 12, 14740, 7858),
    (2014, '20209', 'judge-of-the-court-of-appeals', 13, 15318, 7429),
    (2014, '20045', 'judge-of-the-court-of-appeals', 4, 21160, 9791),
    (2014, '20045', 'judge-of-the-court-of-appeals', 5, 20890, 10096),
    (2014, '20045', 'judge-of-the-court-of-appeals', 7, 21488, 9091),
    (2014, '20045', 'judge-of-the-court-of-appeals', 10, 20025, 10393),
    (2014, '20045', 'judge-of-the-court-of-appeals', 11, 21957, 8944),
    (2014, '20045', 'judge-of-the-court-of-appeals', 12, 20622, 9663),
    (2014, '20045', 'judge-of-the-court-of-appeals', 13, 21205, 9111),
    (2014, '20045', 'justice-of-the-supreme-court', 4, 21180, 10919),
    (2014, '20045', 'justice-of-the-supreme-court', 6, 20208, 11087),
    (2014, '20045', 'judge-of-the-court-of-appeals', 1, 21769, 9651),
    (2016, '20177', 'judge-of-the-court-of-appeals', 2, 45530, 24591),
    (2016, '20177', 'judge-of-the-court-of-appeals', 3, 44849, 24701),
    (2016, '20177', 'judge-of-the-court-of-appeals', 6, 52707, 17437),
    (2016, '20177', 'judge-of-the-court-of-appeals', 8, 44591, 24939),
    (2016, '20177', 'judge-of-the-court-of-appeals', 9, 45153, 24333),
    (2016, '20177', 'judge-of-the-court-of-appeals', 14, 53622, 16636),
    (2016, '20177', 'justice-of-the-supreme-court', 1, 46835, 26799),
    (2016, '20177', 'justice-of-the-supreme-court', 2, 45629, 27346),
    (2016, '20177', 'justice-of-the-supreme-court', 3, 45822, 27185),
    (2016, '20177', 'justice-of-the-supreme-court', 5, 46584, 26351),
    (2016, '20177', 'justice-of-the-supreme-court', 7, 51514, 21910),
    (2016, '20045', 'justice-of-the-supreme-court', 1, 34335, 12626),
    (2016, '20045', 'justice-of-the-supreme-court', 2, 33690, 12982),
    (2016, '20045', 'justice-of-the-supreme-court', 3, 33344, 13314),
    (2016, '20045', 'justice-of-the-supreme-court', 5, 33780, 12781),
    (2016, '20045', 'justice-of-the-supreme-court', 7, 33748, 12803),
    (2016, '20045', 'judge-of-the-court-of-appeals', 2, 33884, 11263),
    (2016, '20045', 'judge-of-the-court-of-appeals', 3, 34564, 10749),
    (2016, '20045', 'judge-of-the-court-of-appeals', 6, 34789, 10333),
    (2016, '20045', 'judge-of-the-court-of-appeals', 8, 33563, 11444),
    (2016, '20045', 'judge-of-the-court-of-appeals', 9, 34047, 11020),
    (2016, '20045', 'judge-of-the-court-of-appeals', 14, 35076, 9779),
]


def parse() -> None:
    rows = list(SCANNED)
    for year in DAYS:
        path = raw.cached(amend.SOURCE, SEDGWICK.format(year)).path
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", path.read_text(errors="replace"), flags=re.DOTALL)
        lines = [line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "\n", text)).splitlines() if line.strip()]
        for index, line in enumerate(lines):
            found = re.fullmatch(r"(Supreme Court|Appeals Judge) Position #(\d+)", line)
            if found:
                window = lines[index + 1 : index + 16]
                yes = int(window[window.index("YES") + 1].replace(",", ""))
                no = int(window[window.index("NO") + 1].replace(",", ""))
                rows.append((year, "20173", SUPREME if found[1] == "Supreme Court" else APPEALS, int(found[2]), yes, no))
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(amend.SOURCE, WYANDOTTE_2016).path), "-"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    for index, line in enumerate(lines):
        found = re.fullmatch(r"Pos (\d+) KS (Supreme|Appeals) Court", line.strip())
        if found:
            window = " ".join(lines[index + 1 : index + 12])
            yes = int(re.search(r"\bYES\s+([\d,]+)", window)[1].replace(",", ""))
            no = int(re.search(r"\bNO\s+([\d,]+)", window)[1].replace(",", ""))
            rows.append((2016, "20209", SUPREME if found[2] == "Supreme" else APPEALS, int(found[1]), yes, no))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "fips": pl.String,
            "office": pl.String,
            "seat": pl.Int32,
            "yes": pl.Int64,
            "no": pl.Int64,
        },
        orient="row",
    ).write_parquet(PARSED / "counties.parquet")
    print(len(rows), "county rows")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas Supreme Court and Court of Appeals retention questions of 2014 and 2016 by county, "
            "from county election offices' official results",
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
        artifacts: dict[str, object] = {}
        for (year, office, seat), group in frame.group_by("year", "office", "seat", maintain_order=True):
            key = f"{DAYS[year]}:KS:{office}:seat-{seat}"
            found = conn.execute(
                """select rs.election_id, rs.contest_id, jsonb_object_agg(upper(o.ballot_label), t.votes)
                   from external_id x
                   join result_set rs on rs.contest_id = x.entity_id and rs.is_default
                   join lateral (select v.option_k, sum(v.value) as votes from result_value v
                                 where v.result_set_k = rs.k and v.measure = 1 group by v.option_k) t on true
                   join contest_option o on o.k = t.option_k
                   where x.scheme = 'us.contest' and x.value = %s
                   group by 1, 2""",
                [key],
            ).fetchone()
            if found is None:
                print(key, "not held, skipped")
                continue
            election, contest, statewide = found
            if group["yes"].sum() > statewide["YES"] or group["no"].sum() > statewide["NO"]:
                raise ValueError(f"{key}: counties exceed the statewide totals")
            options = dict(
                conn.execute(
                    "select upper(ballot_label), id from contest_option where contest_id = %s", [contest]
                ).fetchall()
            )
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
                completeness=round(
                    (group["yes"].sum() + group["no"].sum()) / (statewide["YES"] + statewide["NO"]), 4
                ),
            )
            names = [COUNTIES[fips] for fips in group["fips"]]
            cs.add(
                "note",
                entity_id=result_set,
                kind="caveat",
                body_md=f"County totals for {len(names)} of Kansas's 105 counties ({', '.join(names)}), from each "
                "county election office's official results; the Secretary of State published statewide totals only.",
            )
            for fips, yes, no in group.select("fips", "yes", "no").iter_rows():
                url = (
                    SEDGWICK.format(year)
                    if fips == "20173"
                    else WYANDOTTE_2016
                    if (year, fips) == (2016, "20209")
                    else URLS[(year, fips)]
                )
                if url not in artifacts:
                    artifacts[url] = cs.artifact(raw.cached(amend.SOURCE, url), source)
                for answer, votes in (("YES", yes), ("NO", no)):
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
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"url": url, "artifact": str(artifacts[url]), "unit": fips},
                )
        return cs.write()
