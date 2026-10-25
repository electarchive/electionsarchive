import html
import re
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.phila_questions"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Philadelphia City Commissioners"
TITLE = "Archived election results: ballot questions"
LISTING = "https://vote.phila.gov/resources-data/past-election-results/archived-data-sets/"
BASE = "https://vote.phila.gov/archive/en/"
PAGES = {
    date(2007, 5, 15): "2007-primary/2007-primary-ballot-questions.html",
    date(2007, 11, 6): "2007-general/2007-general-ballot-questions.html",
    date(2008, 4, 22): "2008-primary/2008-primary--ballot-questions.html",
    date(2008, 11, 4): "2008-general/2008-general--ballot-questions.html",
    date(2010, 5, 18): "2010-primary/2010-primary--ballot-questions.html",
    date(2010, 11, 2): "2010-general/2010-general--ballot-questions.html",
    date(2011, 5, 17): "2011-primary/2011-primary--ballot-questions.html",
    date(2011, 11, 8): "2011-general/2011-general--ballot-questions.html",
    date(2012, 11, 6): "2012-general/2012-general--ballot-questions.html",
}
METHOD = (
    "Citywide totals printed on the City Commissioners' archived ballot question page for the election. The page "
    "prints no division detail, no winners, and no write-ins."
)
PLACE = "4260000"
FIPS = "42101"
HEADER = {"Candidate", "Party", "Votes", "Percentage"}


def fetch() -> None:
    for name in PAGES.values():
        raw.fetch(SOURCE, BASE + name, suffix=".html")


def parse() -> None:
    rows = []
    for day, name in PAGES.items():
        text = raw.cached(SOURCE, BASE + name).path.read_text(errors="replace")
        text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.DOTALL)
        lines = [line.strip() for line in html.unescape(re.sub(r"<[^>]+>", "\n", text)).split("\n") if line.strip()]
        start = next(i for i, line in enumerate(lines) if line.startswith("Return to"))
        order = 0
        for i in range(start, len(lines) - 1):
            if lines[i] != "Votes" or lines[i + 1] != "Percentage":
                continue
            j = i - 1
            while lines[j] in HEADER:
                j -= 1
            title = lines[j].strip('"').strip()
            answers = {}
            k = i + 2
            while k + 2 < len(lines) and len(answers) < 2 and re.match(r"(?i)^(?:question \d+ )?(yes|no)\b", lines[k]):
                answers["Yes" if re.search(r"(?i)\byes\b", lines[k]) else "No"] = int(lines[k + 1].replace(",", ""))
                k += 3
            if len(answers) != 2:
                raise ValueError(f"{day}: no two answers after {title[:40]}")
            order += 1
            rows.append((day, BASE + name, order, title, answers["Yes"], answers["No"]))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, schema=["day", "url", "order", "title", "yes", "no"], orient="row")
    frame.write_parquet(PARSED / "questions.parquet")
    print(frame.select("day", "order", pl.col("title").str.slice(0, 60), "yes", "no"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Philadelphia city ballot questions 2007-2012 from the City Commissioners' archived pages",
            polity="US-PA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LISTING,
                license="Public record",
            )
        for url in sorted(set(frame["url"])):
            cs.artifact(raw.cached(SOURCE, url), source)
        city, _name, city_path = conn.execute(
            """select t.polity_id, n.text, s.path from area_code c
               join polity_territory t on t.area_id = c.area_id
               join polity p on p.id = t.polity_id and p.kind = 'municipality'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id where c.code = %s""",
            [PLACE],
        ).fetchone()
        county_unit = conn.execute(
            "select id from reporting_unit where scheme_code = 'us.county' and code = %s", [FIPS]
        ).fetchone()[0]
        place_area = cs.area("us.place", PLACE)
        referendum = cs.key("us.electoral_system", "referendum")
        state_questions = {
            day
            for (day,) in conn.execute(
                """select e.date_from from contest k join election e on e.id = k.election_id
                   join slug s on s.entity_id = e.polity_id and s.path = 'us/pa' where k.kind = 'ballot_question'"""
            ).fetchall()
        }
        elections = {}
        folders = {}
        report = {"questions": 0, "skipped": 0}
        for day, url, order, title, yes, no in frame.iter_rows():
            if re.search(r"(?i)commonwealth|constitution", title):
                report["skipped"] += 1
                continue
            if day not in elections:
                found = conn.execute(
                    "select e.id from election e where e.date_from = %s and e.polity_id = %s limit 1", [day, city]
                ).fetchone()
                if found is None:
                    kind = "primary" if day.month < 7 else "general"
                    elections[day] = cs.key("us.election", f"{day}:PA:{PLACE}")
                    cs.add(
                        "election",
                        id=elections[day],
                        polity_id=city,
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    cs.add(
                        "name",
                        entity_id=elections[day],
                        text=f"{day.year} Philadelphia {kind} election",
                        lang="en",
                        kind="official",
                        is_preferred=True,
                    )
                    folders[day] = f"{city_path}/{day}-{kind}"
                    cs.add("slug", path=folders[day], entity_id=elections[day])
                else:
                    elections[day] = found[0]
                    folders[day] = conn.execute("select path from slug where entity_id = %s", [found[0]]).fetchone()[0]
            election = elections[day]
            folder = folders[day]
            contest_key = f"{day}:PA:{PLACE}:question:{order}"
            contest = cs.key("us.contest", contest_key)
            wording = len(title) > 60
            label = (
                f"{day.year} Philadelphia: ballot question {order}"
                if wording
                else f"{day.year} Philadelphia: {title.title()}"
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=place_area,
                round_number=1,
                electoral_system_id=referendum,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=label, lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"{folder}/question-{order}-{slug(title)[:40].rstrip('-')}", entity_id=contest)
            if wording:
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE_URL, f"{SOURCE}/{contest_key}/text"),
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=title,
                    url=url,
                    source_id=source,
                    locator=None,
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            for position, (answer, votes) in enumerate((("Yes", yes), ("No", no)), start=1):
                option = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=answer,
                )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=county_unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            report["questions"] += 1
        print(report, f"statewide days in archive: {sorted(d.isoformat() for d in state_questions if d.year < 2013)}")
        return cs.write()
