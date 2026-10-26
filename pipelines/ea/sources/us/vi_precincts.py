import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.vi_precincts"
VERSION = "1"
PUBLISHER = "Elections System of the Virgin Islands"
BASE = "https://www.vivote.gov/sites/default/files/officialresults/"
REPORTS = {
    date(2018, 11, 6): BASE + "Official%20Precinct%20Report20181117.HTM",
    date(2020, 11, 3): BASE + "Official%20Precinct%20Report.pdf",
    date(2022, 11, 8): BASE + "Precinct%20Report%20%281%29.pdf",
}
SECTIONS = {"governor": "governor", "delegate to the house": "house:0"}


def fetch() -> None:
    for url in REPORTS.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(["curl", "-skL", "-m", "120", url], capture_output=True, check=True).stdout
            raw.store(
                SOURCE,
                url,
                body,
                suffix=".htm" if url.endswith("HTM") else ".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="text/html" if url.endswith("HTM") else "application/pdf",
            )


def emit() -> Path:
    results: dict[tuple[date, str], dict[str, dict[str, int]]] = {}
    artifacts = {}
    for day, url in REPORTS.items():
        artifact = raw.cached(SOURCE, url)
        artifacts[day] = [artifact]
        if url.endswith("HTM"):
            text = artifact.path.read_text(encoding="latin-1")
        else:
            text = subprocess.run(["pdftotext", "-layout", str(artifact.path), "-"], capture_output=True, check=True, text=True).stdout
        precinct = None
        section = None
        previous = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "TOTAL":
                continue
            heading = re.match(r"^(\d{4}) (.+)$", stripped) if url.endswith("HTM") else None
            if heading:
                precinct = heading[2].lower()
                section = None
            elif not url.endswith("HTM") and stripped.lower() == "statistics":
                precinct = previous.lower()
                section = None
            found = re.match(r"^(?:(?:DEM|REP|IND)\s+)?(.*?)[\s.]*\s(\d[\d,]*)(?:\s+[\d,.]+)*\s*$", stripped)
            if found and section is not None and not re.match(r"(?i)(total votes|vote for|\(vote|precinct summary|report generated)", found[1]):
                name = re.sub(r"\s*\((dem|rep|ind)\)\s*$", "", found[1].strip(" ."), flags=re.I)
                results.setdefault((day, section), {}).setdefault(f"{day.year}:VI:{precinct}", {})[name] = int(found[2].replace(",", ""))
            elif stripped:
                lowered = stripped.lower()
                for prefix, key in SECTIONS.items():
                    if lowered.startswith(prefix):
                        section = key
                        break
                else:
                    if (stripped.isupper() and not stripped.startswith("(")) or re.match(r"(?i)^legislature", stripped):
                        section = None
                previous = stripped
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The Virgin Islands' governor and delegate elections 2018, 2020, and 2022 by precinct from the Elections System of the Virgin Islands",
            polity="US-VI",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-VI'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        for (day, section), precincts in results.items():
            key = f"{day}:VI:{section}"
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            labels = conn.execute("select id, lower(ballot_label), kind from contest_option where contest_id = %s", [contest]).fetchall()
            picked = {}
            for name in {name for votes in precincts.values() for name in votes}:
                lowered = name.lower()
                if lowered.startswith("write-in"):
                    found = [option for option, _, kind in labels if kind == "write_in"]
                elif section == "governor":
                    found = [option for option, label, _ in labels if lowered in label.split(" and ")[0]]
                else:
                    found = [option for option, label, _ in labels if lowered.split()[-1] in label]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {name}")
                picked[name] = found[0]
            source_title = f"{day.year} General Election official results by precinct"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted and not any(r["id"] == source for r in cs.rows.get("source", [])):
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=source_title, url="https://vivote.gov/", license="Public record")
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md="Votes by precinct from the Elections System of the Virgin Islands' official precinct reports.")
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
