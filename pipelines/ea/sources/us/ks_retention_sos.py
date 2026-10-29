import re
import subprocess
import time
import unicodedata
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.ks_pri_2014_clerks import LANDING, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.ks_retention_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.ks.gov/elections/"
SUPREME_2022 = BASE + "22elec/2022-General-Election-Supreme-Court-Results-By-Precinct.xlsx"
APPEALS_2024 = BASE + "24elec/2024-General-Election-Court-of-Appeals-Precinct-Results.xlsx"
TITLE = "Judicial retention questions: official results"
SUPREME = "justice-of-the-supreme-court"
APPEALS = "judge-of-the-court-of-appeals"
COUNTY_FILES = {
    "20091": "https://www.jocoelection.org/sites/default/files/2022-11/GN2022%20Official%20Final%20Results.pdf",
    "20173": "https://www.sedgwickcounty.org/media/62931/after-canvass-official-no-wi.pdf",
    "20177": "https://cdn.snco.gov/elections/document/2022G_Final_Official_Results.pdf",
    "20209": "https://www.wycovotes.org/s/GN2022_Official_Final.pdf",
}
SURNAMES = {
    (SUPREME, "standridge"): 1,
    (SUPREME, "biles"): 2,
    (SUPREME, "wall"): 3,
    (SUPREME, "luckert"): 5,
    (SUPREME, "wilson"): 6,
    (SUPREME, "stegall"): 7,
    (APPEALS, "hill"): 1,
    (APPEALS, "isherwood"): 2,
    (APPEALS, "cline"): 3,
    (APPEALS, "schroeder"): 5,
    (APPEALS, "green"): 7,
    (APPEALS, "malone"): 11,
    (APPEALS, "hurst"): 13,
}


def fetch() -> None:
    for url in (SUPREME_2022, APPEALS_2024, *COUNTY_FILES.values()):
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)
        time.sleep(1)


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    rows: list[tuple[str, str, str, int, str, int]] = []
    sheet = pl.read_excel(raw.cached(SOURCE, SUPREME_2022).path, infer_schema_length=0)
    for county, race, candidate, votes in sheet.select("County", "Race", "Candidate", "Votes").iter_rows():
        found = re.fullmatch(r"Supreme Court Justice (\d+)", race or "")
        answer = re.search(r'"(YES|NO)"', candidate or "")
        if found is None or answer is None or votes is None:
            continue
        rows.append(
            (
                "2022-11-08",
                SUPREME,
                fips[re.sub(r"[^a-z]", "", county.lower())],
                int(found[1]),
                answer[1],
                int(votes.replace(",", "")) if votes.replace(",", "").isdigit() else 0,
            )
        )
    sheet = pl.read_excel(raw.cached(SOURCE, APPEALS_2024).path, has_header=False, infer_schema_length=0)
    heads = [(cell or "").replace("\n", " ") for cell in sheet.row(0)]
    answers = sheet.row(1)
    for line in sheet.iter_rows(named=False):
        if line[0] is None or not line[0].strip().isdigit():
            continue
        county = f"20{int(line[0]):03d}"
        for index, head in enumerate(heads):
            found = re.search(r"KS Court of Appeals Pos (\d+)", head)
            if found is not None:
                rows.append(
                    (
                        "2024-11-05",
                        APPEALS,
                        county,
                        int(found[1]),
                        answers[index].upper(),
                        int(line[index]) if (line[index] or "").isdigit() else 0,
                    )
                )
    big = {"20091", "20173", "20177", "20209"}
    rows = [row for row in rows if not (row[0] == "2022-11-08" and row[2] in big)]
    for county, url in COUNTY_FILES.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
        ).stdout.splitlines()
        seen: set[tuple[str, int]] = set()
        for index, line in enumerate(lines):
            numbered = re.search(
                r"(Supreme Court|Court of Appeals)(?: Justice| Judge)? (?:Pos #|Position )(\d+)\s*(?:\S.*)?$", line
            )
            named = re.fullmatch(r"\s*(Supreme Court|Court of Appeals) - (.+?)\s*", line)
            if numbered is not None:
                office = SUPREME if numbered[1] == "Supreme Court" else APPEALS
                seat = int(numbered[2])
            elif named is not None:
                office = SUPREME if named[1] == "Supreme Court" else APPEALS
                seat = next(
                    (
                        value
                        for (kind, surname), value in SURNAMES.items()
                        if kind == office and surname in named[2].lower()
                    ),
                    0,
                )
            else:
                continue
            window = "\n".join(lines[index + 1 : index + 14])
            yes = re.search(r"^\s*Yes\s+([\d,]+)", window, re.MULTILINE)
            no = re.search(r"^\s*No\s+([\d,]+)", window, re.MULTILINE)
            if seat == 0 or yes is None or no is None or (office, seat) in seen:
                continue
            seen.add((office, seat))
            rows.append(("2022-11-08", office, county, seat, "YES", int(yes[1].replace(",", ""))))
            rows.append(("2022-11-08", office, county, seat, "NO", int(no[1].replace(",", ""))))
        print(county, len(seen), "retention races")
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, schema=["day", "office", "fips", "seat", "answer", "votes"], orient="row")
        .group_by("day", "office", "fips", "seat", "answer")
        .agg(pl.col("votes").sum())
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas retention questions for the 2022 Supreme Court and 2024 Court of Appeals by county, from the Secretary of State's precinct files",
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
        for url in (SUPREME_2022, APPEALS_2024, *COUNTY_FILES.values()):
            cs.artifact(raw.cached(SOURCE, url), source)
        for (day, office, seat), group in frame.group_by("day", "office", "seat", maintain_order=True):
            key = f"{day}:KS:{office}:seat-{seat}"
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
            sums = {answer: group.filter(pl.col("answer") == answer)["votes"].sum() for answer in ("YES", "NO")}
            if sums["YES"] > statewide["YES"] or sums["NO"] > statewide["NO"]:
                raise ValueError(f"{key}: counties exceed the statewide totals {sums} {statewide}")
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
                completeness=round(sum(sums.values()) / (statewide["YES"] + statewide["NO"]), 4),
            )
            if group["fips"].n_unique() < 105:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"County totals for {group['fips'].n_unique()} of Kansas's 105 counties; the Secretary of State publishes no other county retention results.",
                )
            for fips, answer, votes in group.select("fips", "answer", "votes").iter_rows():
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
        return cs.write()
