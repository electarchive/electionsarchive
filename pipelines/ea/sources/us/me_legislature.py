import difflib
import io
import re
import subprocess
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import elstats
from ea.store import DATA, connect

SOURCE = "us.me_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
RETURNS = "https://lldc.mainelegislature.org/Open/Elections/Returns/{year}/{day}/elec_{day}{letter}.pdf"
FILES = {
    (date(1996, 11, 5), "house"): "g",
    (date(1996, 11, 5), "senate"): "i",
    (date(1998, 11, 3), "house"): "g",
    (date(1998, 11, 3), "senate"): "i",
    (date(2000, 11, 7), "house"): "d",
    (date(2000, 11, 7), "senate"): "f",
    (date(2002, 11, 5), "house"): "g",
    (date(2002, 11, 5), "senate"): "i",
    (date(2004, 11, 2), "house"): "j",
    (date(2004, 11, 2), "senate"): "l",
    (date(2006, 11, 7), "house"): "q",
    (date(2006, 11, 7), "senate"): "s",
    (date(2008, 11, 4), "house"): "o",
    (date(2008, 11, 4), "senate"): "q",
    (date(2010, 11, 2), "house"): "m",
    (date(2010, 11, 2), "senate"): "o",
    (date(2012, 11, 6), "house"): "w",
    (date(2012, 11, 6), "senate"): "y",
    (date(2014, 11, 4), "house"): "y",
    (date(2014, 11, 4), "senate"): "aa",
}
COUNTIES = {
    "AND": "23001",
    "ARO": "23003",
    "CUM": "23005",
    "FRA": "23007",
    "HAN": "23009",
    "KEN": "23011",
    "KNO": "23013",
    "LIN": "23015",
    "OXF": "23017",
    "PEN": "23019",
    "PIS": "23021",
    "SAG": "23023",
    "SOM": "23025",
    "WAL": "23027",
    "WAS": "23029",
    "YOR": "23031",
}
NUMBER = re.compile(r"^[\d,.·'`IlOo]+$")
OCR = {"lllRAM": "HIRAM", "DTT9 & TIO TWP.": "DTT9 & T10 TWP."}
PUBLISHER = "Maine Secretary of State"
TITLE = "General Election Tabulations"


def url(day: date, letter: str) -> str:
    return RETURNS.format(year=day.year, day=day.strftime("%Y%m%d"), letter=letter)


def town(text: str) -> str:
    plain = re.sub(r"[^A-Z0-9 ]", "", text.upper().replace("ST.", "SAINT ").replace("ST ", "SAINT "))
    plain = re.sub(r"\b(PLT|PLANTATION|TWP|TOWNSHIP|TOWN|CITY|ISL|ISLAND|UT)\b", " ", plain)
    return re.sub(r"(TWP|PLT)$", "", re.sub(r"\s+", "", plain))


def fetch() -> None:
    for (day, _), letter in FILES.items():
        raw.fetch(SOURCE, url(day, letter), suffix=".pdf")


def parse() -> None:
    rows = []
    districts: dict[tuple[date, str, int], int] = {}
    for (day, chamber), letter in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url(day, letter)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        if (day, chamber) == (date(2004, 11, 2), "house"):
            text = re.sub(
                r"(WALDO\s+216\s+53\.9%)(\s+0\s+0\.0%)(.*?)\n\s+185\s+46\.1%\n",
                r"\1   185   46.1%\2\3\n",
                text,
                flags=re.DOTALL,
            )
        coded = day.year >= 2012
        block = -1
        district = None
        closed = False
        heading = None
        for line in text.splitlines():
            if not line.strip():
                continue
            stripped = re.sub(r"\S*\d%\S*|\bI\b", " ", line)
            if coded:
                row = re.match(r"\s*(\d+)\s+([A-Z]{3})?\s+(.+?)\s{2,}([\d,]+(?:\s+[\d,]+)+)\s*$", stripped)
                total = re.match(r"\s*Totals\s+([\d,]+(?:\s+[\d,]+)+)\s*$", stripped)
                if row:
                    if district != row[1] or closed:
                        block += 1
                        district, closed = row[1], False
                    numbers = [int(n.replace(",", "")) for n in row[4].split()][:-2]
                    rows.extend(
                        (day, chamber, block, row[3].strip(), row[2], column, votes)
                        for column, votes in enumerate(numbers)
                    )
                elif total and block >= 0 and not closed:
                    numbers = [int(n.replace(",", "")) for n in total[1].split()][:-2]
                    rows.extend(
                        (day, chamber, block, None, None, column, votes) for column, votes in enumerate(numbers)
                    )
                    closed = True
                continue
            if re.match(r"\s*D[il1]s\w{2,4}t\b", line) and not re.search(r"(?i)\btotals\b", line):
                number = re.match(r"\s*D[il1]s\w{2,4}t\s*#?\s*([\dIlOoJ][\dIlOoJ ]*)", line)
                number = re.sub(r"\D", "", number[1].translate(str.maketrans("IlOoJ", "11001"))) if number else None
                if day.year != 2004 or number is None or number != heading:
                    block += 1
                heading = number
                if number:
                    districts[(day, chamber, block)] = int(number)
                continue
            if block < 0:
                continue
            if not coded and re.search(r"\d%", line) and block >= 0:
                if day.year == 2004:
                    line = re.sub(r"(\d+\.\d%)(\d+)\b", r"\2 \1", line)
                head = re.match(r"\s*(\S.*?)\s{2,}(?=[\d<Il!|])", line)
                tokens = (
                    [
                        t
                        for t in re.sub(r"\s+%", "%", line[len(head[0]) :]).split()
                        if not re.fullmatch(r"[*.\-'`·•,]+", t)
                    ]
                    if head
                    else []
                )
                groups = []
                shares = []
                for position, token in enumerate(tokens):
                    if "%" in token:
                        previous = tokens[position - 1] if position and "%" not in tokens[position - 1] else None
                        if day.year == 2004:
                            previous = previous.translate(str.maketrans("IlOo!|", "110011")) if previous else None
                        groups.append(re.sub(r"[^\d]", "", previous) if previous else "")
                        share = re.search(r"(\d+\.\d)%", token)
                        shares.append(float(share[1]) if share else None)
                if day.year == 2004:
                    cells = [int(g) if g else None for g in groups]

                    def fits(row: list[int | None], shares: list[float | None] = shares) -> bool:
                        total = sum(x or 0 for x in row)
                        return all(
                            p is None or abs((100 * (x or 0) / total if total else 0) - p) <= 0.0501
                            for x, p in zip(row, shares)
                        )

                    if None in cells and cells.count(None) == 1:
                        spot = cells.index(None)
                        ceiling = 3 * max((c for c in cells if c is not None), default=0) + 10
                        hits = [x for x in range(ceiling) if fits([*cells[:spot], x, *cells[spot + 1 :]])]
                        cells = [*cells[:spot], hits[0], *cells[spot + 1 :]] if len(hits) == 1 else cells
                    elif None not in cells and not fits(cells):
                        ceiling = 3 * max(c or 0 for c in cells) + 10
                        hits = [
                            (spot, x)
                            for spot in range(len(cells))
                            for x in range(ceiling)
                            if fits([*cells[:spot], x, *cells[spot + 1 :]])
                        ]
                        if len(hits) == 1:
                            cells[hits[0][0]] = hits[0][1]
                        else:
                            cells = [None] * len(cells)
                    groups = [str(c) if c is not None else "" for c in cells]
                values = groups[:-1]
                if day.year == 2004 and len(shares) > 2 and shares[-1] and sum(s or 0 for s in shares[:-1]) < 99.5:
                    values = groups
                if head and values and all(values):
                    name = head[1].strip()
                    total = bool(re.search(r"(?i)totals?(:|$)", name))
                    coded_town = re.fullmatch(r"([A-Z]{3})\s+(.+)", name)
                    county = coded_town[1] if coded_town and coded_town[1] in COUNTIES else None
                    rows.extend(
                        (
                            day,
                            chamber,
                            block,
                            None if total else coded_town[2] if county else name,
                            county,
                            column,
                            int(votes.replace(",", "")),
                        )
                        for column, votes in enumerate(values)
                    )
                continue
            if re.search(r"(?i)office\s*t\S*:|\bt[o0]t\S{1,3}s:", stripped):
                values = [
                    int(re.sub(r"[^\d]", "", t.translate(str.maketrans("IlOo", "1100"))) or 0)
                    for t in stripped.split()
                    if NUMBER.match(t)
                ][:-1]
                rows.extend((day, chamber, block, None, None, column, votes) for column, votes in enumerate(values))
                continue
            match = re.match(r"\s*(.+?)\s{2,}(\S.*)$", stripped)
            if not match or not re.search(r"[A-Za-z]{2}", match[1]):
                continue
            rest = match[2].split()
            if rest and all(NUMBER.match(t) for t in rest):
                values = [re.sub(r"[^\d]", "", t.translate(str.maketrans("IlOo", "1100"))) for t in rest][:-1]
                if all(values):
                    rows.extend(
                        (day, chamber, block, match[1].strip(), None, column, int(votes))
                        for column, votes in enumerate(values)
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "block": pl.Int32,
            "town": pl.String,
            "county": pl.String,
            "column": pl.Int32,
            "votes": pl.Int64,
        },
        orient="row",
    ).join(
        pl.DataFrame(
            [(*key, number) for key, number in districts.items()],
            schema={"day": pl.Date, "chamber": pl.String, "block": pl.Int32, "district": pl.Int32},
            orient="row",
        ),
        on=["day", "chamber", "block"],
        how="left",
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet").filter(pl.col("day").dt.year() == 2004)
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS") == "ME")
    places: dict[str, set[str]] = {}
    spelled: dict[str, str] = {}
    for geoid, name in gazetteer.select("GEOID", "NAME").iter_rows():
        places.setdefault(town(name), set()).add(geoid[:5])
        spelled.setdefault(town(name), re.sub(r" (town|city|plantation|UT)$", "", name).upper())
    for name, county in lines.filter(pl.col("county").is_not_null()).select("town", "county").unique().iter_rows():
        places.setdefault(town(name), set()).add(COUNTIES[county])
        spelled[town(name)] = name
    with connect() as conn:
        for (code,) in conn.execute(
            "select code from reporting_unit where scheme_code = 'us.precinct' and code ~ '^(199[0-9]|20(0[0-9]|1[0-4])):ME:23[0-9]{3}::'"
        ).fetchall():
            _, _, fips, _, label = code.split(":", 4)
            label = re.sub(r"( \d+[ -]\d+)+$", "", label)
            places.setdefault(town(label), set()).add(fips)
            spelled.setdefault(town(label), label)
        for label, fips in (
            ("LANGTOWN TWP", "23017"),
            ("PERKINS TWP", "23007"),
            ("NO. 14 TWP", "23009"),
            ("NO. 7 TWP", "23009"),
        ):
            places.setdefault(town(label), set()).add(fips)
            spelled.setdefault(town(label), label)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine House and Senate general elections by municipality 2004, from the Secretary of "
            "State's tabulations",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^(199[68]|20(0[0-9]|1[0-4]))-11-\\d\\d:ME:(house|senate):'
                     and rs.changeset_id not in (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        official: dict[tuple[date, str], list[tuple[str, str, str, int, list[tuple[str, int]]]]] = {}
        for key, contest, election, first, options in conn.execute(
            """select x.value, c.id, c.election_id, min(v.count_round),
                      array_agg(distinct row(o.id::text, coalesce(t.votes, 0)::bigint)::text)
               from external_id x join contest c on c.id = x.entity_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join result_value v on v.result_set_k = rs.k and v.measure = 1
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               left join lateral (select sum(w.value) votes from result_value w
                                  where w.result_set_k = rs.k and w.option_k = o.k and w.measure = 1) t on true
               where x.scheme = 'us.contest' and x.value ~ '^(199[68]|20(0[0-9]|1[0-4]))-11-\\d\\d:ME:(house|senate):[0-9]+:g$'
               group by 1, 2, 3""",
        ).fetchall():
            pairs = [tuple(item.strip("()").split(",")) for item in options]
            official.setdefault((date.fromisoformat(key[:10]), key.split(":")[2]), []).append(
                (key, contest, election, first, [(option, int(votes)) for option, votes in pairs if int(votes)])
            )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://lldc.mainelegislature.org/Open/Elections/Returns/",
            license="Public record",
        )
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '23%%'"
            ).fetchall()
        }
        minted: dict[str, tuple[str, date]] = {}
        skipped: dict[str, list[str]] = {}
        values = []
        done: set[str] = set()
        for (day, chamber, block), rows in lines.group_by("day", "chamber", "block", maintain_order=True):
            totals = rows.filter(pl.col("town").is_null()).sort("column")["votes"].to_list()
            body = rows.filter(
                pl.col("town").is_not_null(),
                ~pl.col("town").str.contains(r"(?i)uocava|emergency|included with"),
                pl.col("town").str.contains(r"[A-Za-z]{2}"),
            )
            towns = body["town"].unique(maintain_order=True).to_list()
            if not totals or not towns:
                skipped.setdefault("no totals", []).append(f"{day} {chamber} {block}")
                continue
            columns = {
                column: sum(group["votes"])
                for (column,), group in rows.filter(pl.col("town").is_not_null()).group_by("column")
            }
            if [columns.get(c, 0) for c in range(len(totals))] != totals or any(
                len(group) != len(totals) for (_,), group in rows.filter(pl.col("town").is_not_null()).group_by("town")
            ):
                skipped.setdefault("columns do not add up to the printed totals", []).append(f"{day} {chamber} {block}")
                continue
            district = rows["district"][0]
            hits = [entry for entry in official.get((day, chamber), []) if int(entry[0].split(":")[3]) == district]
            if len(hits) != 1:
                skipped.setdefault("no single contest in this district", []).append(f"{day} {chamber} {block}")
                continue
            key, contest, election, first, options = hits[0]
            if key in filled or key in done:
                skipped.setdefault("already has a precinct set", []).append(key)
                continue
            ranked = sorted((column for column, votes in enumerate(totals) if votes), key=lambda c: totals[c])
            held = sorted(options, key=lambda item: item[1])
            if (
                len({totals[c] for c in ranked}) != len(ranked)
                or len(ranked) != len(held)
                or any(abs(totals[c] - votes) > votes / 10 for c, (_, votes) in zip(ranked, held))
            ):
                skipped.setdefault("totals do not match the held contest", []).append(key)
                continue
            mapping = {column: option for column, (option, _) in zip(ranked, held)}
            resolved = {}
            labels = {}
            candidates = {}
            for name, county in body.select("town", "county").unique().iter_rows():
                spelling = OCR.get(name, re.sub(r"^T[lI]", "T1", name))
                close = difflib.get_close_matches(
                    town(re.sub(r"II", "H", re.sub(r"[1Il]3", "B", spelling))), list(places), n=1, cutoff=0.8
                )
                match = town(spelling) if town(spelling) in places else close[0] if close else None
                candidates[name] = {COUNTIES[county]} if county else places[match] if match else set()
                if (chamber, town(spelling)) == ("senate", town("T3 ND TWP")):
                    candidates[name] = {"23009"}
                resolved[name] = next(iter(candidates[name])) if len(candidates[name]) == 1 else None
                labels[name] = name if county or match is None else spelled[match]
            known = {fips for fips in resolved.values() if fips}
            for name, found in candidates.items():
                if resolved[name] is None and len(found & known) == 1:
                    resolved[name] = next(iter(found & known))
            if len(known) == 1:
                resolved = {name: fips or next(iter(known)) for name, fips in resolved.items()}
            if not all(resolved.values()):
                skipped.setdefault("a municipality has no county", []).append(
                    f"{key} {[n for n, f in resolved.items() if not f]}"
                )
                continue
            done.add(key)
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=min(
                    1,
                    round(
                        sum(body.filter(pl.col("column").is_in(list(mapping)))["votes"]) / sum(v for _, v in options),
                        4,
                    ),
                ),
            )
            picked = body.filter(pl.col("column").is_in(list(mapping)), pl.col("votes") > 0).with_columns(
                fips=pl.col("town").replace_strict(resolved),
                label=pl.col("town")
                .replace_strict(labels)
                .str.replace_all(r"[^A-Za-z0-9 .'()-]", "")
                .str.replace_all(r"\s+", " "),
            )
            picked = picked.with_columns(
                code=pl.concat_str(pl.lit(str(day.year)), pl.lit("ME"), "fips", pl.lit(""), "label", separator=":")
            )
            picked = picked.group_by("code", "fips", "column").agg(pl.col("votes").sum())
            for code, fips in picked.select("code", "fips").unique().iter_rows():
                minted.setdefault(code, (fips, day))
            values.append(
                picked.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").map_elements(
                        lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String
                    ),
                    option_id=pl.col("column").replace_strict(mapping, return_dtype=pl.String),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
        for reason, keys in skipped.items():
            print(f"{len(keys)} skipped: {reason}", keys[:30])
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code = any(%s)", [list(minted)]
            ).fetchall()
        }
        for code, (fips, day) in minted.items():
            if code in existing:
                continue
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", code),
                polity_id=cs.polity("US-ME"),
                scheme_code="us.precinct",
                code=code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for (day, _), letter in FILES.items():
            cs.artifact(raw.cached(SOURCE, url(day, letter)), source)
        print(f"{len(values)} precinct sets, {len(minted) - len(existing)} new precincts, {len(existing)} reused")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
