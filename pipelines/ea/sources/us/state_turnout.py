import html
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.state_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SERIES = {
    "OR": (
        "37",
        "Oregon Secretary of State",
        "Voter Turnout History for General Elections",
        "https://sos.oregon.gov/elections/Documents/History/Voter_Turnout_History_General_Election.pdf",
    ),
    "MN": (
        "27",
        "Minnesota Secretary of State",
        "Minnesota Election Statistics, 1950–2022",
        "https://sos.mn.gov/media/6027/minnesota-election-statistics-1950-to-2022.pdf",
    ),
    "NC": (
        "37",
        "North Carolina State Board of Elections",
        "Voter Turnout",
        "https://www.ncsbe.gov/node/446",
    ),
}
FIPS = {"OR": "41", "MN": "27", "NC": "37"}


def number(text: str) -> int:
    return int(text.replace(",", ""))


def pdf_text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def fetch() -> None:
    for _, _, _, url in SERIES.values():
        raw.fetch(SOURCE, url, suffix=".html" if url.endswith("446") else ".pdf")


def parse() -> None:
    rows = []
    for line in pdf_text(SERIES["OR"][3]).splitlines():
        match = re.match(r"\s*((?:19|20)\d\d)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%", line)
        if match:
            rows.append({"state": "OR", "year": int(match[1]), "stage": "general", "registered": number(match[2]), "voters": number(match[3])})
    stage = None
    for line in pdf_text(SERIES["MN"][3]).splitlines():
        if "State Primary Statistics" in line:
            stage = "primary"
        elif "State General Election Statistics" in line:
            stage = "general"
        match = re.match(r"\s*((?:19|20)\d\d)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%", line)
        if match and stage:
            rows.append({"state": "MN", "year": int(match[1]), "stage": stage, "registered": None, "voters": number(match[3])})
    text = raw.cached(SOURCE, SERIES["NC"][3]).path.read_text(encoding="utf8", errors="replace")
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", text)
    text = html.unescape(re.sub(r"(?i)</t[dh]>", "|", re.sub(r"(?i)</tr>", "\n", text)))
    for line in re.sub(r"<[^>]+>", " ", text).splitlines():
        match = re.match(r"\s*((?:19|20)\d\d) (General|Primary)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|", line)
        if match:
            rows.append({"state": "NC", "year": int(match[1]), "stage": match[2].lower(), "registered": None, "voters": number(match[4])})
    frame = pl.DataFrame(rows, schema_overrides={"registered": pl.Int64})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")
    print(frame.group_by("state", "stage").len().sort("state", "stage"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oregon's registered voters and ballots cast for its generals from 1960, and the ballots cast in Minnesota's "
            "generals and primaries from 1950 and North Carolina's from 1972 and 1988, statewide, from the states' own election offices",
            polity="US",
        )
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        for state, (_, publisher, title, url) in SERIES.items():
            elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
            for value, entity_id, kind, start in conn.execute(
                """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ %s and e.kind in ('general', 'primary')""",
                [f":{state}(:[a-z]+)?$"],
            ):
                elections.setdefault((start.year, kind), []).append((value, entity_id))
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url, license="Public record")
            cs.artifact(raw.cached(SOURCE, url), source)
            for year, stage, registered, voters in frame.filter(pl.col("state") == state).select(
                "year", "stage", "registered", "voters"
            ).iter_rows():
                found = elections.get((year, stage), [])
                if len(found) > 1 and stage == "general":
                    first = date(year, 11, 1)
                    day = date(year, 11, 2 + (7 - first.weekday()) % 7)
                    found = [f for f in found if f[0].startswith(day.isoformat())]
                if len(found) != 1:
                    print(f"{state} {year} {stage}: {len(found)} elections")
                    continue
                value_key, election_id = found[0]
                result_set = cs.key(f"{SOURCE}.result_set", value_key)
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
                    completeness=1,
                )
                for measure, value in ((2, registered), (14, voters)):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.state", FIPS[state]),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
