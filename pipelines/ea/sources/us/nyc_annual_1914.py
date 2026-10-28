import collections
import re
from datetime import UTC, date, datetime, timedelta

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_annual_1914"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILE = DATA / "inbox" / "manual" / "MF-218" / "1914-uc1.b4565440.html"
URL = "https://babel.hathitrust.org/cgi/pt?id=uc1.b4565440"
TITLE = "Annual Report of the Board of Elections of the City of New York for the Year 1914"
PUBLISHER = "Board of Elections in the City of New York"
DAY = date(1914, 11, 3)
FIRST_PAGE, LAST_PAGE = 81, 83
COUNTY = {"York": "36061", "Bronx": "36005", "Kings": "36047", "Queens": "36081", "Richmond": "36085"}
NUMBER = re.compile(r"^\d{1,3}(,\d{3})*$")
TOLERANCE = 0.001
COLUMNS = (
    ("Glynn", "Dem", "Martin H. Glynn", "Democratic", "democratic-party"),
    ("Glynn", "Ind", "Martin H. Glynn", "Independence League", "NY:independence-league"),
    ("Glynn", "Both", "Martin H. Glynn", "both emblems", None),
    ("Whitman", "", "Charles S. Whitman", None, "republican-party"),
    ("Davenport", "", "Frederick M. Davenport", None, "NY:progressive-party"),
    ("Strebel", "", "Gustave A. Strebel", None, "NY:socialist"),
    ("Sulzer", "Prohibition", "William Sulzer", "Prohibition", "prohibition-party"),
    ("Sulzer", "American", "William Sulzer", "American", "NY:american"),
    ("Sulzer", "Both", "William Sulzer", "both emblems", None),
    ("Hunter", "", "James T. Hunter", None, "NY:socialist-labor"),
)
NOTE = (
    "Assembly district returns for governor from the Board of Elections' annual report canvass of the vote cast at "
    "the general election, read from HathiTrust's page OCR. Candidates who ran on more than one line have a "
    "column per line and one for ballots marked on both. A column is loaded when its assembly district lines add up "
    "to the report's printed county total, or come within one in a thousand of it, which is noted. The assembly "
    "districts are those of 1914 and have no shape in the archive."
)


def parse() -> None:
    html = FILE.read_text(encoding="utf-8", errors="replace")
    pages = re.split(r'<div data-coords="[^"]*" dir="auto" class="ocr_page">', html)[1:]
    rows = []
    carry = None
    for number in range(FIRST_PAGE, LAST_PAGE + 1):
        words = [
            (int(m[1]), int(m[2]), int(m[3]), int(m[4]), m[5].strip())
            for m in re.finditer(r'data-coords="(\d+) (\d+) (\d+) (\d+)" class="ocrx_word">([^<]*)<', pages[number - 1])
        ]
        heads = sorted(
            (y0, COUNTY[w])
            for x0, y0, x1, y1, w in words
            if w in COUNTY and y0 > 150 and any(v[4] in ("County", "of") and abs(v[1] - y0) < 20 for v in words)
        )
        numbers = [(x1, (y0 + y1) / 2, int(w.replace(",", ""))) for x0, y0, x1, y1, w in words if NUMBER.match(w)]
        bounds = []
        if not heads or heads[0][0] > 300:
            bounds.append((0, heads[0][0] if heads else 10**6, carry[0] if carry else None))
        for i, (y, county) in enumerate(heads):
            bounds.append((y, heads[i + 1][0] if i + 1 < len(heads) else 10**6, county))
        for low, high, county in bounds:
            if county is None:
                continue
            segment = [t for t in numbers if low <= t[1] < high]
            small = [t for t in segment if t[2] <= 40]
            sizes = collections.Counter(round(t[0] / 40) for t in small)
            label_bins = [b for b, c in sorted(sizes.items()) if c >= 4 and b * 40 < 350]
            if not label_bins:
                continue
            label_cells = [t for t in small if abs(t[0] / 40 - label_bins[0]) <= 0.6]
            labels = sorted((t[1], t[2]) for t in label_cells)
            height = sorted(b[0] - a[0] for a, b in zip(labels, labels[1:]))[len(labels) // 2]
            filled = [labels[0]]
            for (y0, n0), (y1, n1) in zip(labels, labels[1:]):
                rows_between = round((y1 - y0) / height)
                if rows_between == 1:
                    filled[-1] = (y0, n1 - 1)
                else:
                    filled.extend((y0 + (y1 - y0) * k / rows_between, n1 - rows_between + k) for k in range(1, rows_between))
                filled.append((y1, n1))
            labels = filled
            rest = [t for t in segment if t not in set(label_cells) and t[0] > label_bins[0] * 40 + 60]
            last = labels[-1][0]
            totals = [t for t in rest if t[1] > last + 35]
            body = [t for t in rest if labels[0][0] - 35 < t[1] <= last + 35]
            xs = sorted(t[0] for t in body)
            centers: list[list[int]] = []
            for x in xs:
                if centers and x - centers[-1][-1] <= 35:
                    centers[-1].append(x)
                else:
                    centers.append([x])
            means = [sum(c) / len(c) for c in centers]
            own = [
                (
                    min(labels, key=lambda label: abs(label[0] - t[1]))[1],
                    min(range(len(means)), key=lambda i: abs(means[i] - t[0])),
                    t[2],
                )
                for t in body
            ]
            before = carry[1] if carry and carry[0] == county else []
            if not totals:
                carry = (county, before + own, len(means), words, low, labels[0][0])
                continue
            by_row = collections.defaultdict(list)
            for t in totals:
                by_row[round(t[1] / 25)].append(t)
            total = sorted(max(by_row.values(), key=len))
            if before and carry[2] != len(means):
                before = []
            head_words, head_low, head_high = (carry[3], carry[4], carry[5]) if before else (words, low, labels[0][0])
            carry = None
            if len(total) != len(means):
                continue
            for index, (x1, _, value) in enumerate(total):
                header = " ".join(
                    w
                    for x0, y0, xx1, y1, w in head_words
                    if head_low <= y0 < head_high - 20 and abs(xx1 - x1) < 55 and re.match(r"[A-Za-z(]", w)
                )
                if before:
                    header = " ".join(
                        w
                        for x0, y0, xx1, y1, w in words
                        if low <= y0 < labels[0][0] - 20 and abs(xx1 - x1) < 55 and re.match(r"[A-Za-z(]", w)
                    ) or header
                for ad, column, cell in before + own:
                    if column == index:
                        rows.append((COUNTY_NAME[county], number, ad, index, cell, header))
                rows.append((COUNTY_NAME[county], number, 0, index, value, header))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["county", "page", "ad", "column", "value", "header"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


COUNTY_NAME = {fips: fips for fips in COUNTY.values()}


def emit():
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City's 1914 governor vote by assembly district from the Board of Elections' annual report",
            polity="US",
        )
        city_polity = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id "
            "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchone()[0]
        contest, election, contest_key = conn.execute(
            """select c.id, c.election_id, x.value from contest c
               join slug s on s.entity_id = c.id and s.path = 'us/ny/1914-11-03-general/governor'
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' limit 1"""
        ).fetchone()
        roots = {
            label: (option, key)
            for option, label, key in conn.execute(
                """select o.id, o.ballot_label, x.value from contest_option o
                   join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
                   where o.contest_id = %s and o.parent_option_id is null and o.kind = 'candidate'""",
                [contest],
            ).fetchall()
        }
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL, license="Public record", notes=NOTE)
        cs.artifact(
            raw.store(SOURCE, URL, FILE.read_bytes(), suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html"),
            source,
        )
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
        caveats = []
        values = []
        options = {}
        units = {}
        for (fips, page), group in lines.group_by(["county", "page"], maintain_order=True):
            for index in sorted(group["column"].unique()):
                column = group.filter(pl.col("column") == index)
                printed = column.filter(pl.col("ad") == 0)
                header = printed["header"][0]
                match = next(
                    (c for c in COLUMNS if c[0] in header and (not c[1] or c[1] in header)),
                    None,
                )
                if match is None:
                    continue
                surname, marker, name, line, party_key = match
                if surname == "Glynn" and marker == "Dem" and "Dem" not in header:
                    continue
                ads = column.filter(pl.col("ad") > 0)
                gap = printed["value"][0] - ads["value"].sum()
                if abs(gap) > printed["value"][0] * TOLERANCE:
                    caveats.append(
                        f"{name}'s {line or 'only'} column in {fips} is left out because its lines add up to {ads['value'].sum()} against the printed {printed['value'][0]}"
                    )
                    continue
                if gap:
                    caveats.append(
                        f"{name}'s {line} lines in {fips} add up to {ads['value'].sum()} against the printed {printed['value'][0]}"
                    )
                label = f"{name} ({line})" if line else name
                if label not in options:
                    root = roots.get(name)
                    if root is None:
                        root_key = f"{contest_key}:{slug(name)}"
                        option = cs.key("us.option", root_key)
                        person = cs.key(f"{SOURCE}.person", slug(name))
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                        cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=name)
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{root_key}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=name,
                            is_independent=False,
                        )
                        for other in COLUMNS:
                            if other[2] == name and other[4] is not None:
                                cs.add(
                                    "candidacy_nomination",
                                    candidacy_id=cs.key("us.candidacy", f"{root_key}:head"),
                                    party_id=cs.key("us.party", other[4]),
                                    kind="nominated",
                                )
                        roots[name] = (option, root_key)
                    root, root_key = roots[name]
                    if line is None:
                        options[label] = root
                    else:
                        options[label] = cs.key("us.option", f"{root_key}:{slug(line)}")
                        cs.add(
                            "contest_option",
                            id=options[label],
                            contest_id=contest,
                            parent_option_id=root,
                            kind="ticket",
                            party_id=None if party_key is None else cs.key("us.party", party_key),
                            ballot_label=label,
                        )
                for ad, value in ads.select("ad", "value").iter_rows():
                    code = f"{fips}:{ad:02d}"
                    if code not in units:
                        units[code] = cs.unit("us.nyc_ad", code, DAY)
                        if units[code] in cs.minted:
                            cs.add(
                                "reporting_unit",
                                id=units[code],
                                polity_id=city_polity,
                                scheme_code="us.nyc_ad",
                                code=code,
                                parent_id=cs.unit("us.county", fips),
                                area_relation="unlocated",
                                area_id=None,
                                kind="standard",
                                valid_from=DAY,
                                valid_to=DAY + timedelta(days=1),
                            )
                    values.append((units[code], label, value))
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
            completeness=None,
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE + "".join(f" Note: {c}." for c in caveats))
        for unit, label, value in values:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[label],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=value,
            )
        print(f"{len(options)} options, {len(units)} units, {len(values)} values; {caveats}")
        return cs.write()
