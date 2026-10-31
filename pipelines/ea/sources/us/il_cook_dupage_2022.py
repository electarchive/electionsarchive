import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.il_cook_dupage_2022"
VERSION = "1"
INBOX = Path(__file__).parents[4] / "data/inbox/manual"
DAY = date(2022, 11, 8)
CHICAGO = "https://www.chicagoelections.gov/election-results#2022-general-ushouse-{}"
COOK = (
    "https://www.cookcountyclerkil.gov/elections/results-and-election-data/election-data/precinct-canvasses#2022-11-28"
)
DUPAGE = "https://dupageresults.gov/IL/DuPage/115972/#detail.xls"
FILES = {
    **{
        CHICAGO.format(n): (INBOX / "MF-123" / f"chicago-2022-general-ushouse-{n}.html", ".html", "text/html")
        for n in (3, 4, 7)
    },
    COOK: (
        INBOX / "MF-124" / "Precinct Canvass_20221128 (November).xlsx",
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    DUPAGE: (Path(__file__).parents[4] / "data/tmp/fp-il/detail.xls", ".xls", "application/vnd.ms-excel"),
}
COOK_SHEETS = {3: "Sheet11", 4: "Sheet12", 7: "Sheet15"}
DUPAGE_SHEETS = {3: "9", 4: "10"}
PUBLISHERS = {
    "chicago": "Chicago Board of Election Commissioners",
    "cook": "Cook County Clerk",
    "dupage": "DuPage County Election Commission",
}


def fetch() -> None:
    for url, (path, suffix, media_type) in FILES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            raw.store(
                SOURCE, url, path.read_bytes(), suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media_type
            )


def surname(name: str) -> str:
    words = re.sub(r"\(.*?\)|\b(jr|sr|ii|iii)\b\.?", "", name.lower().replace(",", " ")).split()
    return re.sub(r"[^a-z]", "", words[-1])


def emit() -> Path:
    results: dict[int, dict[tuple[str, str], dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for district in (3, 4, 7):
        text = raw.cached(SOURCE, CHICAGO.format(district)).path.read_text(errors="replace")
        body = text[text.find('id="drupal-modal"') :]
        head = re.search(r"<thead>(.*?)</thead>", body, flags=re.DOTALL)
        names = [
            name.strip()
            for name in re.findall(r"<th>(.*?)</th>", head[1], flags=re.DOTALL)
            if name.strip() not in ("Total Votes", "%")
        ]
        total = [
            int(cell.replace(",", ""))
            for cell in re.findall(r"<td>([\d,]+)</td>", re.search(r"<tbody>(.*?)</tbody>", body, flags=re.DOTALL)[1])
        ][1:]
        sums = [0] * len(names)
        for ward, table in re.findall(r"<h3>Ward (\d+)</h3>\s*(<table.*?</table>)", body, flags=re.DOTALL):
            for row in re.findall(r"<tr>\s*(<td>.*?)</tr>", table, flags=re.DOTALL):
                cells = [cell.strip() for cell in re.findall(r"<td>(.*?)</td>", row, flags=re.DOTALL)]
                if not cells[0].isdigit():
                    continue
                votes = [int(cell.replace(",", "")) for cell in cells[2::2]]
                code = f"2022:IL:17031:CITY OF CHICAGO:Ward {int(ward):02} Precinct {int(cells[0]):02}"
                if len(votes) != len(names):
                    raise ValueError(f"{district} {code}: {len(votes)} columns for {len(names)} candidates")
                for index, count in enumerate(votes):
                    results[district][("us.precinct", code)][names[index]] = count
                    sums[index] += count
        if sums != total[: len(names)]:
            raise ValueError(f"Chicago {district}: sums {sums} do not tie to the printed totals {total}")
        sheet = pl.read_excel(
            raw.cached(SOURCE, COOK).path, sheet_name=COOK_SHEETS[district], has_header=False, infer_schema_length=0
        )
        header = sheet.row(3)
        columns = {
            index: re.sub(r"^[A-Z]+ - ", "", str(cell))
            for index, cell in enumerate(header)
            if index > 5 and cell and str(cell).strip()
        }
        cook_sums: dict[str, int] = defaultdict(int)
        printed = {}
        for row in sheet.iter_rows():
            if row[0] == "Contest Total":
                printed = {name: int(row[index]) for index, name in columns.items()}
            elif row[0] and re.fullmatch(r"\d{7}", str(row[0])):
                for index, name in columns.items():
                    count = int(row[index] or 0)
                    results[district][("us.precinct", f"2022:IL:17031:COOK:{row[0]}.0")][name] = count
                    cook_sums[name] += count
        if dict(cook_sums) != printed:
            raise ValueError(f"Cook {district}: sums {dict(cook_sums)} do not tie to the printed totals {printed}")
    book = raw.cached(SOURCE, DUPAGE).path.read_text(encoding="utf-8-sig")
    for district, sheet_name in DUPAGE_SHEETS.items():
        sheet = book[book.find(f'<s:Worksheet s:Name="{sheet_name}"') :]
        sheet = sheet[: sheet.find("</s:Worksheet>")]
        rows = [
            [
                cell.strip()
                for cell in re.findall(r"<s:Data[^>]*>(.*?)</s:Data>", row, flags=re.DOTALL)
                if "<" not in cell
            ]
            for row in re.findall(r"<s:Row[^>]*>(.*?)</s:Row>", sheet, flags=re.DOTALL)
        ]
        names = [
            name
            for name in re.findall(
                r"<s:Data[^>]*>([A-Z][^<]*)</s:Data>",
                re.findall(r"<s:Row[^>]*>(.*?)</s:Row>", sheet, flags=re.DOTALL)[1],
                flags=re.DOTALL,
            )
        ]
        dupage_sums = [0] * len(names)
        printed = []
        for row in rows:
            if row and row[0] == "Total:":
                printed = [int(row[2 + 5 * k + 4]) for k in range(len(names))]
            elif len(row) == 2 + 5 * len(names) + 1 and re.fullmatch(r"[\d,]+", row[1]):
                for k, name in enumerate(names):
                    count = int(row[2 + 5 * k + 4])
                    results[district][("dupage", row[0].upper())][name] = count
                    dupage_sums[k] += count
        if dupage_sums != printed:
            raise ValueError(f"DuPage {district}: sums {dupage_sums} do not tie to the printed totals {printed}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois' 2022 U.S. House elections in the 3rd, 4th, and 7th Districts by precinct from the Chicago Board of Election Commissioners, the Cook County Clerk, and the DuPage County Election Commission",
            polity="US-IL",
        )
        state = cs.polity("US-IL")
        titles = {
            "chicago": "2022 General Election results by precinct, U.S. Representative",
            "cook": "2022 General Election precinct canvass, Cook County",
            "dupage": "2022 General Election detail results, DuPage County",
        }
        urls = {
            "chicago": "https://www.chicagoelections.gov/election-results",
            "cook": COOK,
            "dupage": "https://dupageresults.gov/IL/DuPage/115972/",
        }
        sources = {}
        for kind, publisher in PUBLISHERS.items():
            sources[kind] = cs.source(publisher, titles[kind])
            cs.add(
                "source",
                id=sources[kind],
                kind="official_body",
                publisher=publisher,
                title=titles[kind],
                url=urls[kind],
                license="Public record",
            )
        for district in (3, 4, 7):
            cs.artifact(raw.cached(SOURCE, CHICAGO.format(district)), sources["chicago"])
        cs.artifact(raw.cached(SOURCE, COOK), sources["cook"])
        cs.artifact(raw.cached(SOURCE, DUPAGE), sources["dupage"])
        dupage_codes: dict[str, list[str]] = defaultdict(list)
        for (code,) in conn.execute(
            "select code from reporting_unit where scheme_code = 'us.precinct' and code like '2022:IL:17043:DUPAGE:%'"
        ).fetchall():
            dupage_codes[re.sub(r" - \d+$", "", code.removeprefix("2022:IL:17043:DUPAGE:"))].append(code)
        county_unit, county_area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.county' and code = '17043'"
        ).fetchone()
        for district, units in results.items():
            key = f"2022-11-08:IL:house:{district}"
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [key],
            ).fetchone()
            labels = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s", [contest]
            ).fetchall()
            picked = {}
            for name in {name for votes in units.values() for name in votes}:
                found = [option for option, label in labels if surname(label) == surname(name)]
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {name}")
                picked[name] = found[0]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            official = conn.execute(
                "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and r.id <> %s and v.measure = 1",
                [contest, result_set],
            ).fetchone()[0]
            counted = sum(count for votes in units.values() for count in votes.values())
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources["chicago"],
                is_default=False,
                completeness=round(float(counted) / float(official), 4),
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by precinct from the Chicago Board of Election Commissioners, the Cook County Clerk's precinct canvass, and the DuPage County Election Commission's detail results. The three offices do not report write-in votes by precinct, so the county write-in totals in the default set are not here.",
            )
            for (kind, place), votes in units.items():
                if kind == "us.precinct":
                    unit = cs.unit("us.precinct", place)
                    if unit in cs.minted:
                        raise ValueError(f"{key}: unit {place} does not exist")
                else:
                    known = dupage_codes.get(place)
                    if known is not None and len(known) == 1:
                        unit = cs.unit("us.precinct", known[0])
                    else:
                        code = f"2022:IL:17043:DUPAGE:{place}"
                        unit = cs.unit("us.precinct", code)
                        if unit in cs.minted and not any(r["id"] == unit for r in cs.rows.get("reporting_unit", [])):
                            cs.add(
                                "reporting_unit",
                                id=unit,
                                polity_id=state,
                                scheme_code="us.precinct",
                                code=code,
                                parent_id=county_unit,
                                area_relation="within",
                                area_id=county_area,
                                kind="standard",
                                valid_from=DAY,
                                valid_to=DAY + timedelta(days=1),
                            )
                for name, count in votes.items():
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
