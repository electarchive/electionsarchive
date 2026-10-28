import re
from collections import defaultdict
from itertools import combinations
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.nyc_annual"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-210"
REPORTS = (
    (
        "nyc-boe-annual-report-1921-uc1.b4565447.html",
        "https://babel.hathitrust.org/cgi/pt?id=uc1.b4565447",
        "Annual Report of the Board of Elections of the City of New York for the Year 1921",
        (date(1921, 1, 1), date(1921, 12, 31)),
    ),
    (
        "nyc-boe-annual-reports-1918-23-nyp.33433075933410.html",
        "https://babel.hathitrust.org/cgi/pt?id=nyp.33433075933410",
        "Annual Reports of the Board of Elections of the City of New York for the Years 1918 to 1923",
        (date(1918, 1, 1), date(1923, 12, 31)),
    ),
)
COUNTIES = {"NEW YORK": "36061", "BRONX": "36005", "KINGS": "36047", "QUEENS": "36081", "RICHMOND": "36085"}
PUBLISHER = "Board of Elections in the City of New York"
NOTE = (
    "Assembly district returns from the Board of Elections' annual report canvasses of the vote by assembly "
    "districts, read from HathiTrust's page OCR. Only candidate columns whose assembly district lines add up to the "
    "report's printed county total and equal the county total already in the archive are loaded. The assembly "
    "districts are those of the year and have no shape in the archive."
)
ORDINAL = re.compile(r"^(\d{1,2})(st|d|nd|rd|th)$")
NUMBER = re.compile(r"^\d{1,3}(,\d{3})*$")
HEADING = re.compile(r"(NEW YORK|BRONX|KINGS|QUEENS|RICHMOND) COUNTY")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = []
    for file, *_ in REPORTS:
        html = (INBOX / file).read_text(encoding="utf-8", errors="replace")
        pages = re.split(r'<div data-coords="[^"]*" dir="auto" class="ocr_page">', html)[1:]
        county = None
        carry: list[tuple[int, int, int]] = []
        for number, page in enumerate(pages, 1):
            words = [
                (int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5].strip())
                for m in re.finditer(r'data-coords="(\d+) (\d+) (\d+) (\d+)" class="ocrx_word">([^<]*)<', page)
            ]
            ordered = sorted(words, key=lambda w: (w[1] // 24, w[0]))
            headings = [
                ((w[1] + w[3]) / 2, HEADING.search(" ".join(v[4] for v in ordered[i : i + 3] if abs(v[1] - w[1]) < 40)))
                for i, w in enumerate(ordered)
                if w[4] in COUNTIES or w[4] == "NEW"
            ]
            headings = [(y, m[1]) for y, m in headings if m is not None]
            totals = sorted(
                (y0 + y1) / 2 for x0, y0, x1, y1, w in words if w.startswith("Total") and x1 < 420 and ORDINAL
            )
            start = 0
            ends = [*totals, 10**6]
            for total_y in ends:
                final = total_y != 10**6
                labels = sorted(
                    ((y0 + y1) / 2, int(ORDINAL.match(w)[1]))
                    for x0, y0, x1, y1, w in words
                    if x1 < 300 and ORDINAL.match(w) and start < (y0 + y1) / 2 < total_y
                )
                here = []
                sums = []
                if labels:
                    numbers = [
                        (x1, (y0 + y1) / 2, int(w.replace(",", "")))
                        for x0, y0, x1, y1, w in words
                        if x1 > 320
                        and NUMBER.match(w)
                        and labels[0][0] - 30 < (y0 + y1) / 2 < (total_y + 30 if final else total_y)
                    ]
                    for x1, y, value in numbers:
                        if final and abs(y - total_y) <= 30:
                            sums.append((x1, value))
                        else:
                            here.append((min(labels, key=lambda label: abs(label[0] - y))[1], x1, value))
                    above = [name for y, name in headings if y < labels[0][0]]
                    if above:
                        county = above[-1]
                if final and sums and (labels or carry) and county is not None:
                    block = f"{file}:{number}:{int(total_y)}"
                    for ad, x1, value in [*carry, *here]:
                        column = min(sums, key=lambda t: abs(t[0] - x1))
                        if abs(column[0] - x1) <= 45:
                            rows.append((file, block, COUNTIES[county], number, ad, column[0], value))
                    for x1, v in sums:
                        rows.append((file, block, COUNTIES[county], number, 0, x1, v))
                    carry = []
                elif not final:
                    carry = here if labels else []
                start = total_y + 30
    pl.DataFrame(rows, schema=["file", "block", "county", "page", "ad", "column", "value"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City 1918 to 1923 city and statewide contests by assembly district from the Board of "
            "Elections' annual reports",
            polity="US",
        )
        done: set[UUID] = set()
        for file, url, title, (first, last) in REPORTS:
            report = lines.filter(pl.col("file") == file)
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
                notes=NOTE,
            )
            cs.artifact(
                raw.store(
                    SOURCE,
                    url,
                    (INBOX / file).read_bytes(),
                    suffix=".html",
                    retrieved_at=datetime.now(UTC),
                    media_type="text/html",
                ),
                source,
            )
            known: dict[tuple[str, int], set[tuple[UUID, date, str, UUID, bool]]] = defaultdict(set)
            for contest, day, fips, option, value, extern, city in conn.execute(
                """select distinct c.id, e.date_from, u.code, o.id, v.value, x.value,
                          exists (select from area_code ac where ac.area_id = c.district_area_id and ac.code = '3651000')
                   from result_value v
                   join result_set rs on rs.k = v.result_set_k and rs.status = 'active'
                   join contest c on c.id = rs.contest_id
                   join election e on e.id = c.election_id
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.county'
                        and u.code in ('36005', '36047', '36061', '36081', '36085')
                   join contest_option o on o.k = v.option_k
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where e.date_from between %s and %s and v.measure = 1 and v.channel = 0 and v.value >= 100""",
                [first, last],
            ).fetchall():
                known[(fips, int(value))].add((contest, day, extern, option, city))
            matched: dict[tuple[UUID, str], dict[UUID, tuple[date, str, bool, dict[str, int], int]]] = defaultdict(dict)
            for (block,), group in report.group_by(["block"]):
                columns = []
                for (column,), cells in group.group_by(["column"]):
                    total = cells.filter(pl.col("ad") == 0)["value"]
                    rows = cells.filter(pl.col("ad") > 0)
                    if len(total) and total[0] >= 100 and rows["value"].sum() == total[0]:
                        columns.append((total[0], dict(rows.group_by("ad").agg(pl.col("value").sum()).iter_rows())))
                votes = defaultdict(int)
                for total, _ in columns:
                    for fips in COUNTIES.values():
                        for contest, *_ in known.get((fips, total), ()):
                            votes[(fips, contest)] += 1
                if not votes:
                    continue
                (county, contest), hits = max(votes.items(), key=lambda item: item[1])
                if hits < 2:
                    continue
                leftover = []
                for total, ads in columns:
                    found = [hit for hit in known.get((county, total), ()) if hit[0] == contest]
                    if len(found) == 1:
                        _, day, extern, option, city = found[0]
                        matched[(contest, county)][option] = (day, extern, city, ads, total)
                    else:
                        leftover.append((total, ads))
                day, extern, city = next(iter(matched[(contest, county)].values()))[:3]
                missing = {
                    option: value
                    for (fips, value), hits in known.items()
                    if fips == county
                    for hit in hits
                    if hit[0] == contest
                    for option in [hit[3]]
                    if option not in matched[(contest, county)]
                }
                for option, value in missing.items():
                    for size in range(1, min(len(leftover), 6) + 1):
                        pick = next((c for c in combinations(leftover, size) if sum(t for t, _ in c) == value), None)
                        if pick is not None:
                            ads = defaultdict(int)
                            for _, column_ads in pick:
                                for ad, votes_ in column_ads.items():
                                    ads[ad] += votes_
                            matched[(contest, county)][option] = (day, extern, city, dict(ads), value)
                            leftover = [c for c in leftover if c not in pick]
                            break
            units: dict[tuple[date, str], UUID] = {}
            polity = conn.execute("select id from polity where iso_code = 'US-NY'").fetchone()[0]
            city_polity = conn.execute(
                "select o.polity_id from office o join external_id x on x.entity_id = o.id "
                "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
            ).fetchone()[0]
            if conn.execute("select 1 from scheme where code = 'us.nyc_ad'").fetchone() is None:
                cs.add(
                    "scheme", code="us.nyc_ad", polity_id=city_polity, kind="precinct", rank=2, noun="assembly district"
                )
            by_contest = defaultdict(dict)
            for (contest, county), options in matched.items():
                by_contest[contest][county] = options
            for contest, counties in by_contest.items():
                if contest in done:
                    continue
                done.add(contest)
                day, extern, city, *_ = next(iter(next(iter(counties.values())).values()))
                (election,) = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
                everyone = sum(value for (fips, value), hits in known.items() if any(hit[0] == contest for hit in hits))
                tied = sum(total for options in counties.values() for *_, total in options.values())
                result_set = cs.key(f"{SOURCE}.result_set", f"{extern}:ad")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=city,
                    completeness=round(min(tied / everyone, 1), 4) if city else None,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
                for county, options in counties.items():
                    for option, (_, _, _, ads, _) in options.items():
                        for ad, value in ads.items():
                            code = f"{county}:{ad:02d}"
                            if (day, code) not in units:
                                units[(day, code)] = cs.unit("us.nyc_ad", code, day)
                                cs.add(
                                    "reporting_unit",
                                    id=units[(day, code)],
                                    polity_id=city_polity,
                                    scheme_code="us.nyc_ad",
                                    code=code,
                                    parent_id=cs.unit("us.county", county),
                                    area_relation="unlocated",
                                    area_id=None,
                                    kind="standard",
                                    valid_from=day,
                                    valid_to=day + timedelta(days=1),
                                )
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=units[(day, code)],
                                option_id=option,
                                channel=0,
                                measure=1,
                                count_round=0,
                                flags=0,
                                value=value,
                            )
                print(f"{day} {extern}: {tied} of {everyone} county votes tie in {len(counties)} counties")
        return cs.write()
