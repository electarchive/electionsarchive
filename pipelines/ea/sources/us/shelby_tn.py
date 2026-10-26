import re
import subprocess
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.shelby_tn"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
UPLOADS = "https://www.electionsshelbytn.gov/wp-content/uploads"
GEMS = {
    date(2019, 10, 3): f"{UPLOADS}/2025/09/10.03.19-Official-Combined-GEMS-SOVC-REPORT.pdf",
    date(2019, 11, 14): f"{UPLOADS}/2025/09/11.14.19-Official-Combined-Results-GEMS-SOVC-REPORT.pdf",
}
CHANNELS = {"Polling": 1, "Absentee": 3, "Early Vote": 2, "Provisional": 5}
FIXED = re.compile(
    r"^(Reg\. Voters|Times|Counted|Times Counted|Total Votes|Total|Votes|Cards Cast|Cast|%|Turnout|Reg\.|Voters)$"
)
PCT = re.compile(r"^(-?[\d.]+%|-)$")
CODE = re.compile(r"^\s*([A-Z0-9]{3,6})\s*$")
SEGMENT = re.compile(r"\S+(?: \S+)*")


def fetch() -> None:
    for url in GEMS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def gems(day: date, text: str, rows: list[tuple[str, str, str, int, int]], problems: list[str]) -> None:
    current = None
    for page in text.split("\f"):
        lines = page.split("\n")
        at = next((i for i, line in enumerate(lines) if "SOVC For" in line), None)
        if at is None:
            continue
        title_at = at + 2
        while title_at < len(lines) and not lines[title_at].strip():
            title_at += 1
        title = " ".join(lines[title_at].split())
        head_at = title_at + 1
        if title == "CITY COUNCIL":
            while not lines[head_at].strip():
                head_at += 1
            title = f"{title} {' '.join(lines[head_at].split())}"
            head_at += 1
        header: list[str] = []
        while head_at < len(lines) and (
            not lines[head_at].strip()
            or "Reg." in lines[head_at]
            or not (
                CODE.match(lines[head_at]) or lines[head_at].strip().startswith((*CHANNELS, "Jurisdiction", "Total"))
            )
        ):
            if lines[head_at].strip():
                header.append(lines[head_at])
            head_at += 1
        if title == "TURN OUT":
            continue
        clusters: list[list] = []
        for line_no, line in enumerate(header):
            for found in SEGMENT.finditer(re.sub(r"(?<=\S) (?=\S)", " ", line)):
                text_ = found[0]
                center = (found.start() + found.end()) / 2
                for cluster in clusters:
                    if abs(cluster[0] - center) < 11 and cluster[3] < line_no:
                        cluster[1] = max(cluster[1], found.end())
                        cluster[2].append(text_)
                        cluster[3] = line_no
                        break
                else:
                    clusters.append([center, found.end(), [text_], line_no])
        clusters.sort(key=lambda c: c[0])
        names = [" ".join(c[2]) for c in clusters if not FIXED.match(" ".join(c[2]))]
        fixed = (
            0
            if not any("Reg." in " ".join(c[2]) for c in clusters)
            else 3
            if any("Times" in " ".join(c[2]) for c in clusters)
            else 2
        )
        for line in lines[head_at:]:
            stripped = line.strip()
            if not stripped or "Page:" in stripped or stripped.startswith(("Statement of Votes", "Shelby County")):
                continue
            label = stripped.split("  ")[0].strip()
            if stripped.startswith("Jurisdiction") or stripped == "Total":
                current = None
            elif current is not None and (label in CHANNELS or label == "Total"):
                if label == "Total":
                    continue
                tokens = [t for t in stripped[len(label) :].split() if not PCT.match(t)]
                if not tokens:
                    continue
                numbers = [int(t.replace(",", "")) for t in tokens]
                if len(numbers) != fixed + len(names):
                    problems.append(f"{day} {title} {current}: {len(numbers)} numbers for {len(names)} names")
                    continue
                for name, votes in zip(names, numbers[fixed:], strict=True):
                    rows.append(
                        (
                            f"{day}|{title}",
                            current,
                            "Write-in" if name.startswith("Write-In") else name,
                            CHANNELS[label],
                            votes,
                        )
                    )
            elif CODE.match(line):
                current = stripped


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str, int, int]] = []
    problems: list[str] = []
    for day, url in GEMS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        gems(day, text, rows, problems)
    frame = pl.DataFrame(rows, schema=["key", "precinct", "name", "channel", "votes"], orient="row")
    frame.write_parquet(PARSED / "rows.parquet")
    (PARSED / "problems.txt").write_text("\n".join(problems))
    print(frame["key"].n_unique(), "contests", frame.height, "rows", len(problems), "problems")


FIPS = "47157"
PUBLISHER = "Shelby County Election Commission"
TITLE = "Statement of Votes Cast"
NOTE = (
    "Precinct totals from the Shelby County Election Commission's certified Statement of Votes Cast, with polling "
    "place, early, absentee, and provisional votes recorded separately. The statement does not mark winners, so the "
    "outcomes recorded here are the leading candidates by votes for the seats at stake."
)
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")


def classify(title: str) -> dict[str, object] | None:
    if title == "MEMPHIS MAYOR":
        return {"type": "mayor", "seat": None}
    found = re.match(r"^CITY COUNCIL DIST (\d)$", title)
    if found:
        return {"type": "council", "seat": found[1], "label": f"District {found[1]}"}
    found = re.match(r"^(?:CITY COUNCIL )?SUPER DISTRICT (\d) POS (\d)$", title)
    if found:
        return {
            "type": "council",
            "seat": f"super-district-{found[1]}-position-{found[2]}",
            "label": f"Super District {found[1]}, Position {found[2]}",
        }
    found = re.match(r"^MUNICIPAL JUDGE DIVISION (\d)$", title)
    if found:
        return {
            "type": "office",
            "seat": None,
            "office": f"municipal-judge-division-{found[1]}",
            "label": f"Municipal Court Judge, Division {found[1]}",
        }
    if title == "CITY COURT CLERK":
        return {"type": "office", "seat": None, "office": "city-court-clerk", "label": "City Court Clerk"}
    return None


def candidate_name(raw_name: str) -> str:
    if raw_name == "Write-in":
        return raw_name
    return " ".join(w if not w.isupper() or len(w) < 3 else w.title() for w in raw_name.split())


def emit() -> Path:
    rows = pl.read_parquet(PARSED / "rows.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Memphis's mayoral, city council, and city court contests by precinct from the Shelby County Election Commission's statements of votes cast",
            polity="US-TN",
        )

        def name(entity: UUID, label: str) -> None:
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-TN")
        county_unit, county_area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()
        city = conn.execute(
            "select entity_id from external_id where scheme = 'us.polity' and value = 'place:4748000'"
        ).fetchone()[0]
        city_area = conn.execute(
            "select area_id from polity_territory where polity_id = %s order by valid_from desc nulls last limit 1",
            [city],
        ).fetchone()[0]
        body = conn.execute(
            "select entity_id from external_id where scheme = 'us.body' and value = 'TN:4748000:council'"
        ).fetchone()[0]
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block")}
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.electionsshelbytn.gov/past-elections/",
                license="Public record",
            )
        for url in GEMS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        report: dict[str, int] = {}
        people: dict[tuple[str, str], list[UUID]] = {}
        for person_id, label in conn.execute(
            """select distinct p.id, n.text from person p join name n on n.entity_id = p.id and n.is_preferred
               join candidacy y on y.person_id = p.id join contest_option o on o.id = y.option_id
               join contest k on k.id = o.contest_id join election e on e.id = k.election_id where e.polity_id = %s""",
            [city],
        ).fetchall():
            parts = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in SUFFIXES]
            if len(parts) >= 2:
                people.setdefault((parts[0], parts[-1]), []).append(person_id)
        taken = {
            path for (path,) in conn.execute("select path from slug where path like 'us/tn/memphis/%%'").fetchall()
        }
        precinct_units: dict[date, dict[str, UUID]] = {}
        values: list[pl.DataFrame] = []
        made_people: set[UUID] = set()
        taken_targets: set[UUID] = set()
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "completeness")
        keys = sorted(rows["key"].unique().to_list())
        for key in keys:
            day_text, _, title = key.partition("|")
            day = date.fromisoformat(day_text)
            info = classify(title)
            if info is None:
                report["skipped"] = report.get("skipped", 0) + 1
                continue
            frame = rows.filter(pl.col("key") == key).with_columns(
                display=pl.col("name").map_elements(candidate_name, return_dtype=pl.String)
            )
            sums = frame.group_by("display").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0)
            if sums.is_empty():
                continue
            election_row = conn.execute(
                """select e.id, s.path from election e join slug s on s.entity_id = e.id and s.is_current
                   join changeset c on c.id = e.changeset_id and c.pipeline <> %s
                   where e.polity_id = %s and (e.date_from = %s or (e.date_precision = 'month' and date_trunc('month', e.date_from) = date_trunc('month', %s::date)))
                   order by e.date_from = %s desc limit 1""",
                [SOURCE, city, day, day, day],
            ).fetchone()
            if election_row is None:
                election = cs.key("us.election", f"{day}:TN:4748000")
                election_path = f"us/tn/memphis/{day}-local"
                if election in cs.minted and not any(r["id"] == election for r in cs.rows.get("election", [])):
                    cs.add(
                        "election", id=election, polity_id=city, kind="local", date_from=day, date_to=day, status="held"
                    )
                    name(
                        election,
                        f"{day.year} Memphis municipal election"
                        if day.month == 10 and day.day == 3
                        else f"{day.year} Memphis city council runoff election",
                    )
                    cs.add("slug", path=election_path, entity_id=election)
            else:
                election, election_path = election_row
            surnames = {
                [w for w in re.sub(r"[^a-z ]", " ", c.lower()).split() if w not in SUFFIXES][-1]
                for c in sums["display"]
                if c != "Write-in"
            }
            target = None
            if election_row is not None:
                best = None
                found_contests: dict[UUID, list] = {}
                for contest_id, contest_key_, label, kind, parent, option_id in conn.execute(
                    """select k.id, (select x.value from external_id x where x.entity_id = k.id and x.scheme = 'us.contest' limit 1),
                              o.ballot_label, o.kind, o.parent_option_id, o.id from contest k
                       left join contest_option o on o.contest_id = k.id
                       where k.election_id = %s""",
                    [election],
                ).fetchall():
                    entry = found_contests.setdefault(contest_id, [contest_key_, []])
                    if label is not None:
                        entry[1].append((label, kind, parent, option_id))
                for contest_id, (contest_key_, listed) in found_contests.items():
                    theirs = {
                        [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in SUFFIXES][-1]
                        for label, kind, parent, _ in listed
                        if kind in ("candidate", "ticket") and parent is None and label.split()
                    }
                    if surnames <= theirs and (best is None or len(theirs) < best[0]):
                        best = (len(theirs), contest_id, contest_key_, listed)
                if best is not None and best[2] is not None and best[1] not in taken_targets:
                    target = best[1:]
            seat = info["seat"]
            contest_key = (
                f"{day}:TN:4748000:{info['type']}"
                + (f":{seat}" if seat else "")
                + (f":{info['office']}" if info.get("office") else "")
            )
            contest_slug = (info.get("office") or info["type"]) + (f"-{seat}" if seat else "")
            area = city_area
            office_id = body_id = None
            kind_ = "office"
            if info["type"] == "council":
                body_id, kind_ = body, "body_seats"
                code = f"47:4748000:council:{seat if seat.startswith('super') else 'district-' + seat}"
                area = cs.area("us.local_district", code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=city_area)
                    name(area, f"Memphis City Council, {info['label']}")
            elif info["type"] == "mayor":
                office_id = conn.execute(
                    "select entity_id from external_id where scheme = 'us.office' and value = 'TN:4748000:mayor'"
                ).fetchone()[0]
            else:
                office_key = f"TN:4748000:{info['office']}"
                found_office = conn.execute(
                    "select entity_id from external_id where scheme = 'us.office' and value = %s", [office_key]
                ).fetchone()
                office_id = found_office[0] if found_office else cs.key("us.office", office_key)
                if found_office is None and office_id not in made_people:
                    made_people.add(office_id)
                    cs.add("office", id=office_id, polity_id=city, kind="other")
                    name(office_id, f"Memphis {info['label']}")
            if target is None:
                if f"{election_path}/{contest_slug}" in taken:
                    report["exists"] = report.get("exists", 0) + 1
                    continue
                taken.add(f"{election_path}/{contest_slug}")
                contest = cs.key("us.contest", contest_key)
                magnitude = 1
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind=kind_,
                    office_id=office_id,
                    body_id=body_id,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=systems["fptp"],
                    default_result_kind="counted",
                    term="full",
                )
                name(
                    contest,
                    f"{day.year} Memphis "
                    + (
                        "mayoral election"
                        if info["type"] == "mayor"
                        else f"City Council, {info['label']}, election"
                        if info["type"] == "council"
                        else f"{info['label']} election"
                    ),
                )
                cs.add("slug", path=f"{election_path}/{contest_slug}", entity_id=contest)
                listed = []
            else:
                contest, contest_key, listed = target
                taken_targets.add(contest)
                election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            by_last: dict[str, list[UUID]] = {}
            by_label: dict[tuple[str, str], UUID] = {}
            for label, kind__, parent, option_id in listed:
                if parent is None and label.split() and kind__ != "write_in":
                    words = [w for w in re.sub(r"[^a-z ]", " ", label.lower()).split() if w not in SUFFIXES]
                    by_label.setdefault((words[-1], words[0][:1]), option_id)
                    by_last.setdefault(words[-1], []).append(option_id)
            ordered = sums.sort("votes", descending=True)["display"].to_list()
            lasts = Counter(
                [w for w in re.sub(r"[^a-z ]", " ", candidate.lower()).split() if w not in SUFFIXES][-1]
                for candidate in ordered
                if candidate != "Write-in"
            )
            options: dict[str, UUID] = {}
            missed = []
            for candidate in ordered:
                if candidate == "Write-in":
                    existing_write = next((o for label, k, parent, o in listed if k == "write_in"), None)
                    options[candidate] = existing_write or cs.key("us.option", f"{contest_key}:write-in")
                    if existing_write is None:
                        cs.add(
                            "contest_option",
                            id=options[candidate],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label=candidate,
                        )
                    continue
                words = [w for w in re.sub(r"[^a-z ]", " ", candidate.lower()).split() if w not in SUFFIXES]
                if target is not None:
                    match = by_label.get((words[-1], words[0][:1]))
                    if match is None and len(by_last.get(words[-1], [])) == 1 and lasts[words[-1]] == 1:
                        match = by_last[words[-1]][0]
                    if match is None:
                        missed.append(candidate)
                    else:
                        options[candidate] = match
                    continue
                option = options[candidate] = cs.key("us.option", f"{contest_key}:{slug(candidate)}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=candidate)
                matched = people.get((words[0], words[-1])) if len(words) >= 2 else None
                if matched is not None and len(matched) == 1:
                    person_id = matched[0]
                else:
                    person_id = cs.key(f"{SOURCE}.person", f"TN:{slug(candidate)}")
                    if person_id in cs.minted and person_id not in made_people:
                        made_people.add(person_id)
                        cs.add("person", id=person_id)
                        name(person_id, candidate)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(candidate)}:head"),
                    person_id=person_id,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
            if missed:
                report["supersede skipped"] = report.get("supersede skipped", 0) + 1
                taken_targets.discard(contest)
                print("missed", key, missed)
                continue
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            prior = replaced = None
            if target is not None:
                prior = conn.execute(
                    f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
                ).fetchone()
                if prior is not None and prior[0] == result_set:
                    replaced = conn.execute(
                        "select supersedes_id from result_set where id = %s", [prior[0]]
                    ).fetchone()[0]
                    prior = None
                if prior is not None:
                    cs.add(
                        "result_set", **dict(zip(columns, prior, strict=True)), is_default=False, status="superseded"
                    )
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
                completeness=1,
                supersedes_id=prior[0] if prior else replaced,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            ranked = sums.filter(pl.col("display") != "Write-in").sort("votes", descending=True)
            for candidate in ranked["display"].to_list()[:1]:
                if candidate in options:
                    cs.add(
                        "outcome", result_set_id=result_set, option_id=options[candidate], kind="elected", seats=None
                    )
            if day not in precinct_units:
                precinct_units[day] = {}
            unit_ids: dict[str, UUID] = {}
            for precinct in frame["precinct"].unique().to_list():
                unit_code = f"{day}:TN:{FIPS}:SHELBY:{precinct}"
                unit_ids[precinct] = precinct_units[day].get(precinct) or cs.unit("us.precinct", unit_code)
                if precinct not in precinct_units[day]:
                    precinct_units[day][precinct] = unit_ids[precinct]
                    if unit_ids[precinct] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit_ids[precinct],
                            polity_id=state,
                            scheme_code="us.precinct",
                            code=unit_code,
                            parent_id=county_unit,
                            area_relation="within",
                            area_id=county_area,
                            kind="standard",
                            valid_from=day,
                            valid_to=day + timedelta(days=1),
                        )
            lines = (
                frame.filter(pl.col("display").is_in(list(options)))
                .group_by("precinct", "display", "channel")
                .agg(pl.col("votes").sum())
            )
            values.append(
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("precinct").replace_strict({k: str(v) for k, v in unit_ids.items()}),
                    option_id=pl.col("display").replace_strict({k: str(v) for k, v in options.items()}),
                    channel=pl.col("channel").cast(pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(1, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value=pl.col("votes"),
                )
            )
            report["superseded" if target is not None else "new"] = (
                report.get("superseded" if target is not None else "new", 0) + 1
            )
        print(report)
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
