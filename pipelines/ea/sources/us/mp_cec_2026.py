import html
import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.mp_cec_live import PAGE, key, pick, words
from ea.sources.us.mp_votecnmi import MUNICIPIO, PUBLISHER
from ea.store import DATA, connect

SOURCE = "us.mp_cec_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "2026 General Election Results"
RUNOFF = {"2026-11-03:MP:governor", "2026-11-03:MP:69:attorney-general"}


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")


def text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def number(cell: str) -> int | None:
    digits = cell.replace(",", "")
    return int(digits) if digits.isdigit() else None


def grab(pattern: str, fragment: str) -> str:
    found = re.search(pattern, fragment, re.DOTALL)
    if found is None:
        raise ValueError(f"no match for {pattern}")
    return found[1]


def parse() -> None:
    page = raw.cached(SOURCE, PAGE).path.read_text(encoding="utf-8", errors="replace")
    if "Certified on November 6, 2026" not in page:
        raise ValueError("the page no longer says the results are certified")
    rows: list[tuple[str, str, str, bool, str, int]] = []
    for block in re.split(r'<div class="er-office" id="office-\d+">', page)[1:]:
        block = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", block, flags=re.DOTALL)
        title = text(grab(r'er-office__title">(.*?)</h2>', block))
        printed = number(grab(r'er-office__total">([\d,]+) votes', block))
        contest_key = key(title)
        if contest_key is None:
            raise ValueError(f"no contest mapping for {title}")
        found: list[tuple[str, str, str, bool, str, int]] = []
        for table in re.findall(r'<table class="er-retention-table.*?</table>', block, re.DOTALL):
            grid = [
                [text(cell) for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.DOTALL)]
                for row in re.findall(r"<tr.*?</tr>", table, re.DOTALL)
            ]
            heads = grid[0][1:]
            for cells in grid[1:]:
                if cells[0] not in ("YES", "NO"):
                    continue
                values = dict(zip(heads, cells[1:], strict=True))
                for unit, cell in values.items():
                    if unit.upper() != "TOTAL" and (votes := number(cell)) is not None:
                        found.append((contest_key, title, cells[0].title(), False, unit, votes))
                if sum(row[5] for row in found if row[2] == cells[0].title()) != number(values["TOTAL"]):
                    raise ValueError(f"{title} {cells[0]}: units do not add to the total")
        for candidate in re.split(r'<div class="er-candidate[ "]', block)[1:]:
            name = text(grab(r'er-candidate__name">(.*?)</span>', candidate))
            leading = candidate.startswith("er-candidate--leading")
            heads = [
                text(cell)
                for cell in re.findall(r'<th class="er-precinct-table__head[^>]*>(.*?)</th>', candidate, re.DOTALL)
            ]
            cells = [
                text(cell)
                for cell in re.findall(r'<td class="er-precinct-table__cell[^>]*>(.*?)</td>', candidate, re.DOTALL)
            ]
            values = dict(zip(heads, cells, strict=True))
            total = number(grab(r'er-candidate__votes">([\d,]+) votes?', candidate))
            units = [
                (unit, votes)
                for unit, cell in values.items()
                if unit != "Total" and (votes := number(cell)) is not None
            ]
            if sum(votes for _, votes in units) != total or number(values["Total"]) != total:
                raise ValueError(f"{title} {name}: units do not add to the total")
            found.extend((contest_key, title, name, leading, unit, votes) for unit, votes in units)
        if sum(row[5] for row in found) != printed:
            raise ValueError(f"{title}: {sum(row[5] for row in found)} counted, {printed} printed")
        rows.extend(found)
    frame = pl.DataFrame(rows, schema=["contest", "title", "name", "leading", "unit", "votes"], orient="row")
    print(frame["contest"].n_unique(), "contests,", frame["unit"].n_unique(), "units,", len(frame), "rows")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Mariana Islands general election of November 3, 2026, certified results by election district, early voting, and absentee",
            polity="US-MP",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-MP'").fetchall()[0]
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchall()[0]
        municipios = {
            code: (unit, unit_area)
            for code, unit, unit_area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and polity_id = %s",
                [state],
            ).fetchall()
        }
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, PAGE), source)
        units = {}
        for unit_name in frame["unit"].unique(maintain_order=True):
            aggregate = unit_name in ("EV", "ABS")
            code = f"MP:{DAY.year}:{unit_name}" if aggregate else f"{DAY.year}:MP:{unit_name}"
            scheme = "us.aggregate" if aggregate else "us.precinct"
            units[unit_name] = cs.unit(scheme, code)
            municipio = None if aggregate else municipios[MUNICIPIO.get(unit_name, "69110")]
            cs.add(
                "reporting_unit",
                id=units[unit_name],
                polity_id=state,
                scheme_code=scheme,
                code=code,
                parent_id=municipio[0] if municipio else parent,
                area_relation="within",
                area_id=municipio[1] if municipio else area,
                kind="aggregate" if aggregate else "standard",
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
            )
        for (contest_key,), rows in frame.group_by("contest", maintain_order=True):
            contest, election, contest_kind = conn.execute(
                """select c.id, c.election_id, c.kind from contest c
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s""",
                [contest_key],
            ).fetchall()[0]
            options = conn.execute(
                """select o.id, case when o.kind = 'candidate' then coalesce(nullif(ca.ballot_name, ''), o.ballot_label)
                   else o.ballot_label end from contest_option o
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.contest_id = %s and o.parent_option_id is null and o.kind <> 'write_in'""",
                [contest],
            ).fetchall()
            labels = {option: words(label or "") for option, label in options}
            chosen = {}
            for name in rows["name"].unique(maintain_order=True):
                option = pick(name, labels)
                if option is None or option in chosen.values():
                    raise ValueError(f"{contest_key}: no single option for {name}")
                chosen[name] = option
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
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Election-day votes by election district as certified on November 6, 2026; early voting and absentee votes are separate aggregate units on their own channels.",
            )
            totals = dict(rows.group_by("name").agg(pl.col("votes").sum()).iter_rows())
            ranked = sorted(totals, key=lambda name: -totals[name])
            if contest_key in RUNOFF:
                for place, name in enumerate(ranked):
                    kind = "advanced" if place < 2 else "eliminated"
                    cs.add("outcome", result_set_id=result_set, option_id=chosen[name], kind=kind, seats=None)
            elif set(totals) == {"Yes", "No"}:
                passed = totals["Yes"] > totals["No"]
                kind = ("elected" if passed else "lost") if contest_kind == "office" else ("won" if passed else "lost")
                seats = 1 if contest_kind == "office" and passed else None
                cs.add("outcome", result_set_id=result_set, option_id=chosen["Yes"], kind=kind, seats=seats)
            else:
                seats = 1 if contest_kind == "body_seats" else None
                for name in rows.filter(pl.col("leading"))["name"].unique(maintain_order=True):
                    cs.add("outcome", result_set_id=result_set, option_id=chosen[name], kind="elected", seats=seats)
            for name, unit_name, votes in rows.select("name", "unit", "votes").iter_rows():
                aggregate = unit_name in ("EV", "ABS")
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[unit_name],
                    option_id=chosen[name],
                    channel=(2 if unit_name == "EV" else 3) if aggregate else 1,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
