import csv
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.guam_gec_precincts"
VERSION = "1"
PUBLISHER = "Guam Election Commission"
REPORTS = {
    2016: (date(2016, 11, 8), "https://gec.guam.gov/wp-content/uploads/2016/11/2016-ge-unofficial-prec{:04d}.htm"),
    2018: (date(2018, 11, 6), "https://gec.guam.gov/wp-content/uploads/2018/11/2018-ge-official-prec{:04d}.htm"),
}
CSV24 = (date(2024, 11, 5), "https://drive.google.com/uc?export=download&id=1EZZ2Qjrl8JOSAaFyKtPxppaQCNTjVKUt")


def fetch() -> None:
    for _, pattern in REPORTS.values():
        for number in range(1, 81):
            url = pattern.format(number)
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                body = subprocess.run(["curl", "-skL", "-m", "60", "-A", raw.USER_AGENT, "-w", "%{http_code}", url], capture_output=True, check=True).stdout
                if body.endswith(b"200"):
                    raw.store(SOURCE, url, body[:-3], suffix=".htm", retrieved_at=datetime.now(UTC), media_type="text/html")
    try:
        raw.cached(SOURCE, CSV24[1])
    except LookupError:
        body = subprocess.run(["curl", "-sL", "-m", "120", CSV24[1]], capture_output=True, check=True).stdout
        raw.store(SOURCE, CSV24[1], body, suffix=".csv", retrieved_at=datetime.now(UTC), media_type="text/csv")


def emit() -> Path:
    results: dict[tuple[date, str], dict[str, dict[str, int]]] = {}
    artifacts = {}
    for year, (day, pattern) in REPORTS.items():
        for number in range(1, 81):
            try:
                artifact = raw.cached(SOURCE, pattern.format(number))
            except LookupError:
                continue
            artifacts.setdefault(day, []).append(artifact)
            text = artifact.path.read_text(encoding="latin-1")
            code = f"{year}:GU:{number:04d}"
            section = None
            for line in text.splitlines():
                stripped = line.strip()
                found = re.match(r"^\s*(?:\d+\.\s+)?(.*?)[\s.]*\s(\d[\d,]*)\s+[\d.]+\s*$", line)
                if found and section is not None:
                    results.setdefault((day, section), {}).setdefault(code, {})[found[1].strip(" .")] = int(found[2].replace(",", ""))
                elif stripped and not re.match(r"(?i)(vote for|mark no more|total|over votes|under votes|\(with|votes|registered|ballots|voter)", stripped):
                    lowered = stripped.lower()
                    section = "governor" if lowered.startswith("governor") else "house:0" if lowered.startswith("non-voting delegate") else None
    day, url = CSV24
    artifacts[day] = [raw.cached(SOURCE, url)]
    grid = list(csv.reader(artifacts[day][0].path.read_text(encoding="utf-8-sig").splitlines()))
    for row in grid[3:]:
        if not row[1].strip().isdigit():
            continue
        names = {grid[2][index]: int(row[index] or 0) for index in range(len(row)) if grid[0][index] == "Delegate to Congress" and not grid[2][index].endswith("VOTES")}
        results.setdefault((day, "house:0"), {})[f"2024:GU:{int(row[1]):04d}"] = names
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Guam's governor and delegate elections 2016, 2018, and 2024 by precinct from the Guam Election Commission",
            polity="US-GU",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-GU'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        for (day, section), precincts in results.items():
            key = f"{day}:GU:{section}"
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            labels = conn.execute("select id, lower(ballot_label), kind from contest_option where contest_id = %s", [contest]).fetchall()
            picked = {}
            for name in {name for votes in precincts.values() for name in votes}:
                lowered = re.sub(r"\s*\((dem|rep|ind)\)\s*$", "", re.sub(r"^(DEM|REP|IND|NPP) ", "", name).lower()).strip()
                if lowered in ("write-in", "write-ins"):
                    found = [option for option, _, kind in labels if kind == "write_in"] or [option for option, label, _ in labels if "aguon" in label]
                elif lowered.startswith(("over", "under")):
                    continue
                elif section == "governor":
                    head = lowered.split("/")[0].strip()
                    found = [option for option, label, _ in labels if head in label.split(" and ")[0]]
                else:
                    surname = lowered.split(",")[0].strip()
                    found = [option for option, label, _ in labels if surname in label]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {name}")
                picked[name] = found[0]
            source_title = f"{day.year} General Election official results by precinct"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted and not any(r["id"] == source for r in cs.rows.get("source", [])):
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=source_title, url="https://gec.guam.gov/", license="Public record")
            for artifact in artifacts[day]:
                cs.artifact(artifact, source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            columns = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
            for prior in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s", [contest, result_set]
            ).fetchall():
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
            counted = sum(votes for precinct in precincts.values() for name, votes in precinct.items() if name in picked)
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md="Votes by precinct from the Guam Election Commission's official precinct reports.")
            for code, votes in precincts.items():
                unit = cs.unit("us.precinct", code)
                if unit in cs.minted and not any(r["id"] == unit for r in cs.rows.get("reporting_unit", [])):
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=parent,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                for name, count in votes.items():
                    if name in picked:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=picked[name],
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=count,
                        )
        return cs.write()
