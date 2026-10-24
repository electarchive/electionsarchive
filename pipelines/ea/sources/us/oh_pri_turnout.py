import html
import re
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import oh_primaries
from ea.store import DATA, connect

SOURCE = "us.oh_pri_turnout"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ohio Secretary of State"
TITLE = "Primary election voter turnout by county"
WAYBACK = "https://web.archive.org/web/{}id_/https://www.ohiosos.gov/{}"
PAGES = "elections/election-results-and-data/{}-elections-results/{}/"
FILES = {
    date(2000, 3, 7): WAYBACK.format("20190826023738", PAGES.format(2000, "voter-turnout-march-7-2000")),
    date(2002, 5, 7): WAYBACK.format("20190915073952", PAGES.format(2002, "voter-turnout")),
    date(2004, 3, 2): WAYBACK.format("20190819102410", PAGES.format(2004, "voter-turnout-march-2-2004")),
    date(2006, 5, 2): WAYBACK.format("20190819094824", PAGES.format(2006, "voter-turnout-may-2-2006")),
    date(2008, 3, 4): WAYBACK.format(
        "20200807183317", "elections/election-results-and-data/2008-election-results/voter-turnout-march-4-2008/"
    ),
    date(2006, 11, 7): WAYBACK.format("20190826023927", PAGES.format(2006, "voter-turnout-november-7-2006")),
    date(2010, 5, 4): WAYBACK.format("20191118035925", PAGES.format(2010, "voter-turnout-may-4-2010")),
    date(2012, 3, 6): WAYBACK.format("20191118133520", PAGES.format(2012, "voter-turnout-march-6-2012")),
    date(2014, 5, 6): WAYBACK.format("20200612164028", "globalassets/elections/2014/primary/turnout.xlsx"),
    date(2024, 3, 19): WAYBACK.format(
        "20240420171448", "globalassets/elections/2024/pri/official/turnoutbycountybyparty.xlsx"
    ),
}
MASTERS = {date(2016, 3, 15), date(2018, 5, 8), date(2020, 4, 28), date(2022, 5, 3)}
BALLOTS = (
    "total votes cast",
    "total ballots cast",
    "total electors voting",
    "voter turnout",
    "total voters",
    "ballots counted",
)


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".xlsx" if url.endswith(".xlsx") else ".html")


def masters() -> dict[date, tuple[str, str]]:
    return {
        day: (oh_primaries.SOURCE, url)
        for (day, party), url in oh_primaries.urls().items()
        if day in MASTERS and party == "democratic"
    }


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%'"""
            ).fetchall()
        }
    tables: dict[date, list[list[str | None]]] = {}
    for day, url in FILES.items():
        path = raw.cached(SOURCE, url).path
        if url.endswith(".xlsx"):
            tables[day] = [
                list(row) for row in pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
            ]
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            tables[day] = [
                [
                    html.unescape(re.sub(r"<[^>]+>", "", cell)).strip()
                    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
                ]
                for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.DOTALL)
            ]
    for day, (source, url) in masters().items():
        tables[day] = [
            list(row)
            for row in pl.read_excel(
                raw.cached(source, url).path, sheet_name="Master", has_header=False, infer_schema_length=0
            ).rows()
        ]
    rows = []
    for day, table in sorted(tables.items()):
        header = next(row for row in table if row and (row[0] or "").lower().startswith("county"))
        labels = [" ".join((cell or "").lower().split()) for cell in header]
        registered = next(index for index, label in enumerate(labels) if "regist" in label)
        ballots = next(index for index, label in enumerate(labels) if label in BALLOTS)
        values: dict[str, tuple[int, int]] = {}
        total = None
        for row in table:
            if len(row) <= max(registered, ballots) or not all(
                re.fullmatch(r"[\d,]+(\.0)?", str(row[index] or "")) for index in (registered, ballots)
            ):
                continue
            numbers = tuple(int(float(str(row[index]).replace(",", ""))) for index in (registered, ballots))
            name = re.sub(r"[^a-z]", "", (row[0] or "").lower())
            if name in counties:
                values[counties[name]] = numbers
            elif name.startswith("total"):
                total = numbers
        sums = tuple(sum(value[index] for value in values.values()) for index in (0, 1))
        if (
            len(values) != 88
            or total is not None
            and any(abs(a - b) > b / 1000 for a, b in zip(sums, total, strict=True))
        ):
            raise ValueError(f"{day}: {len(values)} counties sum to {sums}, the total row says {total}")
        print(day, labels[registered], "/", labels[ballots], sums, "checked" if total else "no total row", total)
        note = (
            None
            if total is None or sums == total
            else " ".join(
                f"The county figures add up to {mine:,} {label}; the Secretary of State's total row says {theirs:,}."
                for label, mine, theirs in zip(("registered voters", "ballots"), sums, total, strict=True)
                if mine != theirs
            )
        )
        rows.extend(
            {"day": day, "code": code, "registered": value[0], "ballots": value[1], "note": note}
            for code, value in values.items()
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "turnout.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's registered voters and ballots cast by county for the primaries of 2000 to 2024 and the 2006 general, from the "
            "Secretary of State's turnout pages and official canvass workbooks",
            polity="US",
        )
        elections = {
            start: (entity_id, kind)
            for entity_id, start, kind in conn.execute(
                """select x.entity_id, e.date_from, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^\\d{4}-\\d\\d-\\d\\d:OH(:primary|:special)?$'
                   and e.kind in ('primary', 'general', 'special', 'referendum')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.ohiosos.gov/elections/election-results-and-data/",
                license="Public record",
            )
        for url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        for cached, url in masters().values():
            cs.artifact(raw.cached(cached, url), source)
        for (day,), rows in frame.group_by("day", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            election_id, kind = elections[day]
            result_set = cs.key(f"{SOURCE}.result_set", f"{day}:OH:{kind}:county")
            note = rows["note"].drop_nulls().first()
            if note is not None:
                cs.add("note", id=uuid5(result_set, "total"), entity_id=result_set, kind="caveat", body_md=note)
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
            for code, registered, ballots in rows.select("code", "registered", "ballots").iter_rows():
                for measure, value in ((2, registered), (3, ballots)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
