import html
import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.mp_votecnmi"
VERSION = "1"
PUBLISHER = "Commonwealth Election Commission of the Northern Mariana Islands"
YEARS = {
    2016: date(2016, 11, 8),
    2018: date(2018, 11, 6),
    2020: date(2020, 11, 3),
    2022: date(2022, 11, 8),
    2024: date(2024, 11, 5),
}
CHANNELS = {"EV": 2, "ABS": 3}


def url(year: int) -> str:
    return f"https://www.votecnmi.gov.mp/election-results-archive/{year}-election-results"


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def fetch() -> None:
    for year in YEARS:
        try:
            raw.cached(SOURCE, url(year))
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "--compressed", "-A", raw.USER_AGENT, url(year)], capture_output=True, check=True
            ).stdout
            raw.store(SOURCE, url(year), body, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Mariana Islands delegate elections 2016-2024 by election district, with early and absentee voting",
            polity="US-MP",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-MP'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        for year, day in YEARS.items():
            title = f"{year} Election Results"
            source = cs.source(PUBLISHER, title)
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=url(year), license="Public record")
            cs.artifact(raw.cached(SOURCE, url(year)), source)
            page = raw.cached(SOURCE, url(year)).path.read_text(encoding="utf-8", errors="replace")
            start = re.search(r"(?i)delegate to u\.?s\.? congress", page).start()
            grid = []
            for row in re.findall(r"<tr.*?</tr>", page[start:], flags=re.S):
                cells = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
                if not cells:
                    continue
                grid.append(cells)
                if cells[0].upper() == "TOTAL":
                    break
            header, *body = grid
            columns = [
                re.match(r"ED\d[A-D]?", text.upper())[0] if text.upper().startswith("ED") else text.upper()
                for text in header[1:]
            ]
            body = body[:-1]
            contest, election = conn.execute(
                "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                [f"{day}:MP:house:0"],
            ).fetchone()
            labels = conn.execute("select id, ballot_label from contest_option where contest_id = %s", [contest]).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:MP:house:0:district")
            columns_set = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
            for prior in conn.execute(
                f"select {', '.join(columns_set)} from result_set where contest_id = %s and is_default and id <> %s",
                [contest, result_set],
            ).fetchall():
                cs.add("result_set", **dict(zip(columns_set, prior, strict=True)), is_default=False)
            for option, kind, seats in conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                [contest, result_set],
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            official = conn.execute(
                """select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k
                   where r.contest_id = %s and r.is_default and v.measure = 1""",
                [contest],
            ).fetchone()[0]
            counted = sum(int(row[1 + columns.index("TOTAL")].replace(",", "")) for row in body)
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
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by election district as published; early voting and absentee votes are separate aggregate units on their own channels where the page gives them separate columns.",
            )
            units = {}
            has_early = "EV" in columns
            for row in body:
                surname = fold(row[0].split(",")[0]).strip()
                found = [option for option, label in labels if surname in fold(label)]
                if len(found) != 1:
                    raise ValueError(f"{year}: {len(found)} options for {row[0]}")
                for name, cell in zip(columns, row[1:], strict=False):
                    if name == "TOTAL" or not cell.replace(",", "").isdigit():
                        continue
                    if name in CHANNELS:
                        code, scheme, channel, kind = f"MP:{year}:{name}", "us.aggregate", CHANNELS[name], "aggregate"
                    else:
                        code, scheme, channel, kind = f"{year}:MP:{name}", "us.precinct", 1 if has_early else 0, "standard"
                    if code not in units:
                        units[code] = cs.unit(scheme, code)
                        if units[code] in cs.minted:
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=state,
                                scheme_code=scheme,
                                code=code,
                                parent_id=parent,
                                area_relation="within",
                                area_id=area,
                                kind=kind,
                                valid_from=day,
                                valid_to=day + timedelta(days=1),
                            )
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[code],
                        option_id=found[0],
                        channel=channel,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=int(cell.replace(",", "")),
                    )
        return cs.write()
