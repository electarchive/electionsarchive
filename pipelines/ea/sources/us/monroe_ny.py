import json
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.monroe_ny"
VERSION = "1"
PUBLISHER = "Monroe County Board of Elections"
URL = "https://www.monroecounty.gov/files/boe/Election%20Results/Election%20Canvass%20Books/2024%20Cavnass%20Book.pdf"
ONTARIO = "https://app.enhancedvoting.com/results/public/api/elections/ontario-county-ny/GE24/data/ballot-item/01000000-4629-5a76-4887-08ddf6f51c5b"
DAY = date(2024, 11, 5)
PARTIES = {"dem": "(democratic)", "rep": "sadwick", "wor": "(working families)", "scatter": "scattered"}


def fetch() -> None:
    for url, suffix, media_type in ((URL, ".pdf", "application/pdf"), (ONTARIO, ".json", "application/json")):
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(["curl", "-skL", "-m", "300", "-A", "Mozilla/5.0", url], capture_output=True, check=True).stdout
            raw.store(SOURCE, url, body, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type)


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    lines = subprocess.run(["pdftotext", "-layout", str(artifact.path), "-"], capture_output=True, check=True, text=True).stdout.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.strip() == "25TH DISTRICT" and lines[index - 1].strip() == "REPRESENTATIVE IN CONGRESS" and "DETAIL" in lines[index + 1]
    )
    end = next(index for index in range(start, len(lines)) if re.match(r"\s*STATE SENATOR", lines[index]))
    rows: dict[str, dict[str, int]] = {}
    group = None
    body = [line.strip() for line in lines[start:end] if line.strip()]
    for index, line in enumerate(body):
        numbers = re.findall(r"\d[\d,]*", line)
        if re.match(r"^\d+\s+\d", line) and len(numbers) >= 7:
            values = [int(number.replace(",", "")) for number in numbers[1:7]]
            rows[f"{group}:{numbers[0]}"] = dict(zip(("total", "dem", "rep", "wor", "scatter", "void"), values, strict=True))
        elif index + 1 < len(body) and re.match(r"^\d+\s+\d", body[index + 1]) and len(re.findall(r"\d[\d,]*", body[index + 1])) >= 7 and not re.search(r"[A-Z]{4}", line):
            group = re.sub(r"[^a-z0-9]+", "-", line.lower()).strip("-")
    ontario_artifact = raw.cached(SOURCE, ONTARIO)
    for precinct in json.loads(ontario_artifact.path.read_text(encoding="utf-8"))["ballotItemWithBreakdown"]["breakdownResults"]:
        values = {"dem": 0, "rep": 0, "wor": 0, "scatter": 0}
        for option in precinct["ballotOptions"]:
            name = option["name"][0]["text"]
            party = (option["party"] or {}).get("abbreviation")
            values["scatter" if option["isWriteIn"] else "rep" if "Sadwick" in name else "wor" if party == "WOR" else "dem"] += option["voteCount"]
        rows[f"ontario:{re.sub(r'[^a-z0-9]+', '-', precinct['precinct']['name'][0]['text'].lower()).strip('-')}"] = values
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Monroe County, New York's 2024 general election for Representative in Congress, 25th District, by election district from the Board of Elections' canvass book",
            polity="US-NY",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-NY'").fetchone()
        counties = {
            code: conn.execute("select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [code]).fetchone() for code in ("36055", "36069")
        }
        key = f"{DAY}:NY:house:25"
        contest, election = conn.execute(
            "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s", [key]
        ).fetchone()
        labels = conn.execute("select id, lower(ballot_label) from contest_option where contest_id = %s", [contest]).fetchall()
        picked = {}
        for column, needle in PARTIES.items():
            found = [option for option, label in labels if needle in label]
            if len(found) != 1:
                raise ValueError(f"{len(found)} options for {column}")
            picked[column] = found[0]
        title = "2024 canvass book: general election results by election district"
        source = cs.source(PUBLISHER, title)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=URL, license="Public record")
        cs.artifact(artifact, source)
        ontario_source = cs.source("Ontario County Board of Elections", "2024 general election official results by election district")
        cs.add(
            "source",
            id=ontario_source,
            kind="official_body",
            publisher="Ontario County Board of Elections",
            title="2024 general election official results by election district",
            url="https://app.enhancedvoting.com/results/public/ontario-county-ny/elections/GE24",
            license="Public record",
        )
        cs.artifact(ontario_artifact, ontario_source)
        result_set = cs.key(f"{SOURCE}.result_set", f"{key}:election-district")
        columns = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
        for prior in conn.execute(f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s", [contest, result_set]).fetchall():
            cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False)
        for option, outcome_kind, seats in conn.execute(
            """select o.option_id, o.kind, o.seats from outcome o
               join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
            [contest, result_set],
        ).fetchall():
            cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
        official = conn.execute(
            "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
            [contest],
        ).fetchone()[0]
        counted = sum(values[column] for values in rows.values() for column in PARTIES)
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=round(float(counted) / float(official), 4),
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md="Votes by election district: Monroe County from its Board of Elections' 2024 canvass book, where the city rows are grouped by legislative district, and the district's 14 election districts in Ontario County from that county's official results.")
        for code, values in rows.items():
            county = "36069" if code.startswith("ontario:") else "36055"
            parent, area = counties[county]
            unit = cs.unit("us.precinct", f"{DAY}:NY:{county}:{code}")
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=state,
                scheme_code="us.precinct",
                code=f"{DAY}:NY:{county}:{code}",
                parent_id=parent,
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
            for column, option in picked.items():
                cs.add("result_value", result_set_id=result_set, unit_id=unit, option_id=option, channel=0, measure=1, count_round=0, flags=0, value=values[column])
        return cs.write()
