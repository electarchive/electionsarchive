import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.kr import na
from ea.sources.kr.nec import BASE, LICENSE, PUBLISHER, REPORT, SIDO_CODES, _get
from ea.store import DATA, connect

SOURCE = "kr.referendums"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TITLE = "선거통계시스템: 역대선거 실시상황 국민투표"
LANDING = f"{BASE}/main/showDocument.xhtml?electionId=0000000000&topMenuId=BI&secondMenuId=BIGI06"
FORM = {
    "electionId": "0000000000",
    "requestURI": "/electioninfo/0000000000/bi/bigi06.jsp",
    "topMenuId": "BI",
    "secondMenuId": "BIGI06",
    "menuId": "BIGI06",
    "statementId": "BIGI06",
    "electionType": "9",
}
KEY = f"{REPORT}?{urlencode(FORM)}"
NAMES = {
    "19621217": "1962 South Korean constitutional referendum",
    "19691017": "1969 South Korean constitutional referendum",
    "19721121": "1972 South Korean constitutional referendum",
    "19750212": "1975 South Korean referendum on the constitution and confidence in the government",
    "19801022": "1980 South Korean constitutional referendum",
    "19871027": "1987 South Korean constitutional referendum",
}
SYSTEM = (
    "Voters answer yes or no to one question. The National Election Commission records yes votes, no votes, invalid "
    "ballots, and abstentions; the answer with more valid votes carries."
)
METHOD = (
    "Electors, ballots cast, yes votes, no votes, and invalid ballots by sido, as the National Election Commission's "
    "statistics system prints them for each referendum, with the national total from the same table. Sidos are those "
    "of the day of the vote: Busan has its own row only from 1969, Daegu, Incheon, and Gwangju from 1987, and "
    "Daejeon and Ulsan appear in none. The system also prints a second, parenthesized figure beside the electors and "
    "ballots of 1969 and later; it is unlabeled and is not stored."
)


def fetch() -> None:
    if KEY not in raw.keys(SOURCE):
        content = _get(REPORT, FORM)
        if b'id="table01"' not in content:
            raise ValueError("no referendum table")
        raw.store(SOURCE, KEY, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


def _tables(html: str) -> list[list[list[str]]]:
    found = []
    for table in re.findall(r"<table[^>]*>(.*?)</table>", html, re.DOTALL):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL)
        found.append(
            [[na._text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)] for row in rows]
        )
    return found


def _count(cell: str) -> int:
    return int(cell.split("(")[0].replace(",", "").strip())


def parse() -> None:
    tables = _tables(raw.cached(SOURCE, KEY).path.read_text())
    summary = next(rows for rows in tables if rows and rows[0][0] == "시행일")
    days = []
    for row in summary[1:]:
        match = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", row[0])
        if match:
            days.append((match.group(1) + match.group(2) + match.group(3), row))
    detail = [rows for rows in tables if rows and rows[0][0] == "시도명"]
    if len(detail) != len(days):
        raise ValueError(f"{len(days)} referendums, {len(detail)} sido tables")
    results: list[dict[str, Any]] = []
    elections: list[dict[str, Any]] = []
    for (day, row), rows in zip(days, detail, strict=True):
        elections.append(
            {
                "day": day,
                "population": _count(row[3]),
                "announced_amendment": row[1],
                "announced_vote": row[2],
                "reason": row[8],
            }
        )
        for cells in rows[2:]:
            if len(cells) != 10:
                continue
            yes, no, valid, invalid, abstain = (_count(cell) for cell in cells[3:8])
            results.append(
                {
                    "day": day,
                    "sido": cells[0],
                    "registered": _count(cells[1]),
                    "ballots": _count(cells[2]),
                    "yes": yes,
                    "no": no,
                    "valid": valid,
                    "invalid": invalid,
                    "abstain": abstain,
                }
            )
    frame = pl.DataFrame(results)
    for day in frame["day"].unique():
        part = frame.filter(pl.col("day") == day)
        total = part.filter(pl.col("sido") == "합계").row(0, named=True)
        rest = part.filter(pl.col("sido") != "합계")
        for column in ("registered", "ballots", "yes", "no", "valid", "invalid", "abstain"):
            if rest[column].sum() != total[column]:
                raise ValueError(f"{day}: {column} sums to {rest[column].sum()}, total {total[column]}")
        if total["yes"] + total["no"] != total["valid"] or total["valid"] + total["invalid"] != total["ballots"]:
            raise ValueError(f"{day}: total does not add up")
        if total["registered"] - total["ballots"] != total["abstain"]:
            raise ValueError(f"{day}: abstentions do not add up")
        for row in rest.iter_rows(named=True):
            if row["yes"] + row["no"] != row["valid"] or row["valid"] + row["invalid"] != row["ballots"]:
                raise ValueError(f"{day} {row['sido']}: row does not add up")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    pl.DataFrame(elections).write_parquet(PARSED / "elections.parquet")
    print(frame.filter(pl.col("sido") == "합계").sort("day"))


def emit() -> Path:
    results = pl.read_parquet(PARSED / "results.parquet")
    elections = pl.read_parquet(PARSED / "elections.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "South Korea's six national referendums, 1962–1987, by sido, from the National Election "
                "Commission's statistics system"
            ),
            polity="KR",
        )

        def name(entity: Any, text: str, lang: str = "ko", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=True)

        korea = cs.polity("KR")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license=LICENSE,
            license_url=BASE,
        )
        cs.artifact(raw.cached(SOURCE, KEY), source)
        nation_area = cs.area("kr.nation", "KR")
        nation = cs.unit("kr.nation", "KR")
        if nation in cs.minted:
            cs.add(
                "reporting_unit",
                id=nation,
                polity_id=korea,
                scheme_code="kr.nation",
                code="KR",
                parent_id=None,
                area_relation="equals",
                area_id=nation_area,
                kind="standard",
                valid_from=None,
            )
            name(nation, "대한민국")
            name(nation, "South Korea", "en", "english")
        system = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=korea, rules={"formula": "referendum"}, rules_version=1)
        name(system, "South Korean national referendum", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        for meta in elections.iter_rows(named=True):
            day = meta["day"]
            held = date(int(day[:4]), int(day[4:6]), int(day[6:]))
            english = NAMES[day]
            native = f"{held.year}년 국민투표 ({meta['reason']})"
            rows = results.filter(pl.col("day") == day)
            total = rows.filter(pl.col("sido") == "합계").row(0, named=True)
            election = cs.key(f"{SOURCE}.election", day)
            cs.add(
                "election",
                id=election,
                polity_id=korea,
                kind="referendum",
                date_from=held,
                date_to=held,
                date_precision="day",
                status="held",
            )
            name(election, native)
            name(election, english, "en", "english")
            path = f"kr/{held}-referendum"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key(f"{SOURCE}.contest", day)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(contest, native)
            name(contest, english, "en", "english")
            cs.add("slug", path=f"{path}/{slug(english)}", entity_id=contest)
            cs.add(
                "note",
                entity_id=contest,
                kind="context",
                body_md=(
                    f"The National Election Commission lists the reason as {meta['reason']}. The amendment was "
                    f"announced on {meta['announced_amendment'].split('(')[0]} and the date of the vote on "
                    f"{meta['announced_vote'].split('(')[0]}; the population stands at {meta['population']:,}."
                ),
            )
            result_set = cs.key(f"{SOURCE}.result_set", day)
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
            options = {}
            for position, (answer, label) in enumerate((("yes", "찬성"), ("no", "반대")), 1):
                options[answer] = cs.key(f"{SOURCE}.option", f"{day}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[answer],
                    kind="won" if total[answer] > total["no" if answer == "yes" else "yes"] else "lost",
                )
            for row in rows.iter_rows(named=True):
                if row["sido"] == "합계":
                    unit = nation
                else:
                    unit = cs.unit("kr.sido", SIDO_CODES[row["sido"]])
                for measure, option, value in (
                    (2, None, row["registered"]),
                    (3, None, row["ballots"]),
                    (4, None, row["valid"]),
                    (5, None, row["invalid"]),
                    (1, options["yes"], row["yes"]),
                    (1, options["no"], row["no"]),
                ):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
