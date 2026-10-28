import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_annual"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
INBOX = DATA / "inbox" / "manual" / "MF-210"
REPORTS = {
    date(1921, 11, 8): (
        "nyc-boe-annual-report-1921-uc1.b4565447.html",
        "https://babel.hathitrust.org/cgi/pt?id=uc1.b4565447",
        "Annual Report of the Board of Elections of the City of New York for the Year 1921",
        range(76, 120),
    ),
}
COUNTIES = {"NEW YORK": "36061", "BRONX": "36005", "KINGS": "36047", "QUEENS": "36081", "RICHMOND": "36085"}
OFFICES = {"MAYOR": "mayor"}
PUBLISHER = "Board of Elections in the City of New York"
NOTE = (
    "Assembly district returns from the Board of Elections' annual report canvass 'The City Vote for State, City, "
    "County and Borough Offices, by Assembly Districts', read from HathiTrust's page OCR. Only candidate columns whose "
    "assembly district lines add up to the report's printed county total are loaded."
)
ORDINAL = re.compile(r"^(\d{1,2})(st|d|nd|rd|th)$")
NUMBER = re.compile(r"^\d{1,3}(,\d{3})*$")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = []
    for day, (file, url, title, pages_wanted) in REPORTS.items():
        html = (INBOX / file).read_text(encoding="utf-8", errors="replace")
        pages = re.split(r'<div data-coords="[^"]*" dir="auto" class="ocr_page">', html)[1:]
        county = None
        for number in pages_wanted:
            words = [
                (int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5].strip())
                for m in re.finditer(
                    r'data-coords="(\d+) (\d+) (\d+) (\d+)" class="ocrx_word">([^<]*)<', pages[number - 1]
                )
            ]
            text = " ".join(w[4] for w in sorted(words, key=lambda w: (w[1] // 24, w[0])))
            for name in COUNTIES:
                if re.search(rf"\b{name} COUNTY\b", text):
                    county = name
            title_y = next(
                (
                    (y0 + y1) / 2
                    for x0, y0, x1, y1, w in sorted(words, key=lambda w: w[1])
                    if w in OFFICES and x1 > 600 and x1 < 1000
                ),
                None,
            )
            if title_y is None or county is None:
                continue
            office = next(w[4] for w in words if w[4] in OFFICES and abs((w[1] + w[3]) / 2 - title_y) < 2)
            total_y = min(
                (
                    (y0 + y1) / 2
                    for x0, y0, x1, y1, w in words
                    if w.startswith("Total") and x1 < 420 and (y0 + y1) / 2 > title_y
                ),
                default=None,
            )
            if total_y is None:
                continue
            labels = sorted(
                ((y0 + y1) / 2, int(ORDINAL.match(w)[1]))
                for x0, y0, x1, y1, w in words
                if x1 < 300 and ORDINAL.match(w) and title_y < (y0 + y1) / 2 < total_y
            )
            if not labels:
                continue
            numbers = [
                (x1, (y0 + y1) / 2, int(w.replace(",", "")))
                for x0, y0, x1, y1, w in words
                if x1 > 320 and NUMBER.match(w) and title_y < (y0 + y1) / 2 < total_y + 30
            ]
            totals = [(x1, v) for x1, y, v in numbers if abs(y - total_y) <= 30]
            for x1, y, value in numbers:
                if abs(y - total_y) <= 30:
                    continue
                ad = min(labels, key=lambda label: abs(label[0] - y))[1]
                column = min(totals, key=lambda t: abs(t[0] - x1), default=None)
                if column is None or abs(column[0] - x1) > 45:
                    continue
                rows.append((str(day), COUNTIES[county], OFFICES[office], number, ad, column[0], value))
            for x1, v in totals:
                rows.append((str(day), COUNTIES[county], OFFICES[office], number, 0, x1, v))
    pl.DataFrame(rows, schema=["day", "county", "office", "page", "ad", "column", "value"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City mayor 1921 by assembly district from the Board of Elections' annual report",
            polity="US",
        )
        for day_text, (file, url, title, _pages) in ((str(day), report) for day, report in REPORTS.items()):
            day = date.fromisoformat(day_text)
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
            for office_slug in OFFICES.values():
                contest_key = f"nyc_early_mayors:{day}:NY:3651000:{office_slug}"
                contest = cs.key("us.contest", contest_key)
                election = cs.key("us.election", f"{day}:NY:3651000")
                known = {
                    (fips, int(value)): label
                    for fips, label, value in conn.execute(
                        """select u.code, o.ballot_label, v.value from result_value v
                           join result_set rs on rs.k = v.result_set_k and not rs.is_default
                           join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.county'
                           join contest_option o on o.k = v.option_k
                           where rs.contest_id = %s""",
                        [contest],
                    ).fetchall()
                }
                (polity,) = conn.execute(
                    "select o.polity_id from contest c join office o on o.id = c.office_id where c.id = %s", [contest]
                ).fetchone()
                everyone = sum(value for _, value in known)
                ties = []
                for (county, column), group in lines.filter(pl.col("office") == office_slug).group_by(
                    ["county", "column"]
                ):
                    total = group.filter(pl.col("ad") == 0)["value"]
                    rows = group.filter(pl.col("ad") > 0)
                    if len(total) and rows["value"].sum() == total[0] and (county, total[0]) in known:
                        ties.append((county, total[0], rows))
                if conn.execute("select 1 from scheme where code = 'us.nyc_ad'").fetchone() is None:
                    cs.add("scheme", code="us.nyc_ad", polity_id=polity, kind="precinct", rank=2, noun="assembly district")
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:ad")
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
                    completeness=round(sum(total for _, total, _ in ties) / everyone, 4),
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=NOTE + " The assembly districts are those of 1921 and have no shape in the archive.",
                )
                units: dict[str, UUID] = {}
                for county, total, rows in ties:
                    label = known[(county, total)]
                    option = cs.key("us.option", f"{contest_key}:{slug(label)}")
                    for ad, value in rows.group_by("ad").agg(pl.col("value").sum()).iter_rows():
                        code = f"{county}:{ad:02d}"
                        if code not in units:
                            units[code] = cs.unit("us.nyc_ad", code, day)
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=polity,
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
                            unit_id=units[code],
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                print(
                    f"{day} {office_slug}: {sum(total for _, total, _ in ties)} of {everyone} county votes tie by assembly district"
                )
        return cs.write()
