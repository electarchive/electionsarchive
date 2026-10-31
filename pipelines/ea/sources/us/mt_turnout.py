import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mt_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Montana Secretary of State"
UPLOADS = "https://sosmt.gov/wp-content/uploads/"
FILES = {
    (date(2012, 11, 6), "general"): (UPLOADS + "attachments/2012_General_Canvass.pdf", None),
    (date(2014, 6, 3), "primary"): (UPLOADS + "attachments/2014-Primary-Official-Statewide-Canvass.pdf", None),
    (date(2014, 11, 4), "general"): (UPLOADS + "attachments/2014-General-Official-Statewide-Canvass.pdf", None),
    (date(2016, 6, 7), "primary"): (UPLOADS + "attachments/2016PrimaryStateCanvass.pdf", None),
    (date(2016, 11, 8), "general"): (UPLOADS + "attachments/2016GeneralStatewideCanvass.pdf", None),
    (date(2018, 11, 6), "general"): (UPLOADS + "2018GeneralReportStateCanvass.pdf", "20190101181853"),
    (date(2020, 6, 2), "primary"): (UPLOADS + "2020PrimaryReportStateCanvass.pdf", None),
    (date(2020, 11, 3), "general"): (UPLOADS + "State_Canvass_Report.pdf", None),
    (date(2022, 11, 8), "general"): (
        "https://sosmt.gov/docs/769/results/49081/2022general-statecanvassreport",
        "20230104151335",
    ),
    (date(2024, 11, 5), "general"): (
        "https://sosmt.gov/docs/31/post-election/66775/2024-general-election-report-state-canvass",
        "20241203210537",
    ),
}
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '30%'
"""
ROW = re.compile(r"^\s*([A-Za-z.&' ]+?)\s{2,}(\d+)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+[\d.]+\s*%?\s*$")


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name.replace("&", "and")).lower())


def fetch() -> None:
    for url, stamp in FILES.values():
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=90)
        if response.status_code != 200 and stamp is not None:
            response = httpx.get(f"https://web.archive.org/web/{stamp}id_/{url}", follow_redirects=True, timeout=90)
        response.raise_for_status()
        raw.store(
            SOURCE, url, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
        )


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for (day, stage), (url, _) in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        found = {}
        for line in text.splitlines():
            match = ROW.match(line)
            if match is None or plain(match[1]) not in codes:
                continue
            registered, voters = (int(match[i].replace(",", "")) for i in (3, 4))
            if voters > registered:
                print(f"{day} {match[1]}: {registered} {voters}")
                continue
            found.setdefault(codes[plain(match[1])], (registered, voters))
        print(day, stage, len(found))
        for code, (registered, voters) in found.items():
            rows.append({"date": day, "stage": stage, "code": code, "registered": registered, "voters": voters})
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(frame.group_by("date").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("date"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's registered voters and votes cast by county for its generals and primaries from the Secretary of State's "
            "statewide canvass reports",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':MT(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Statewide Canvass Report"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url="https://sosmt.gov/elections/results/",
                license="Public record",
            )
        for (day, stage), rows in frame.group_by("date", "stage", maintain_order=True):
            found = elections.get(day)
            if found is None:
                print(f"{day}: no election")
                continue
            value_key, election_id = found
            cs.artifact(raw.cached(SOURCE, FILES[(day, stage)][0]), source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / 56, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
