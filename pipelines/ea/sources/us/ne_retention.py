import itertools
import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ne_retention"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://sos.nebraska.gov/sites/default/files/doc/"
LANDING = "https://sos.nebraska.gov/elections/previous-elections"
PUBLISHER = "Nebraska Secretary of State"
TITLE = "{year} General Election Canvass"
IMAGES = Path(__file__).with_name("ne_retention_images.tsv")
BOOKS = {
    2000: (date(2000, 11, 7), "elections/2000/2000%20General.pdf"),
    2002: (date(2002, 11, 5), "2002%20General.pdf"),
    2004: (date(2004, 11, 2), "2004%20General.pdf"),
    2006: (date(2006, 11, 7), "2006%20General.pdf"),
    2008: (date(2008, 11, 4), "elections/2008/2008%20General%20Canvass%20Book.pdf"),
    2010: (date(2010, 11, 2), "elections/2010/2010%20Gen%20Canvass%20Book%2011-30-Final.pdf"),
    2012: (date(2012, 11, 6), "elections/2012/2012-general-canvass.pdf"),
    2014: (date(2014, 11, 4), "elections/2014/2014-General-Canvass-Recount-Final.pdf"),
    2016: (date(2016, 11, 8), "elections/2016/2016-canvass-book.pdf"),
    2018: (date(2018, 11, 6), "elections/2018/2018-general-election-official-results.pdf"),
    2022: (date(2022, 11, 8), "elections/2022/2022%20General%20Canvass%20Book.pdf"),
}
SCANNED = {2000, 2002, 2006}
JUDGES = {
    (2004, 3): "John M. Gerrard",
    (2004, 5): "William M. Connolly",
    (2004, 6): "John F. Wright",
    (2008, 2): "Lindsey Miller-Lerman",
    (2010, 0): "Michael G. Heavican",
    (2010, 3): "John M. Gerrard",
    (2010, 5): "William M. Connolly",
    (2010, 6): "John F. Wright",
    (2012, 1): "Kenneth C. Stephan",
    (2012, 4): "Michael McCormack",
    (2014, 2): "Lindsey Miller-Lerman",
    (2016, 0): "Michael G. Heavican",
    (2016, 3): "William B. Cassel",
    (2016, 6): "John F. Wright",
    (2018, 1): "Stephanie F. Stacy",
    (2022, 0): "Michael G. Heavican",
    (2022, 3): "William B. Cassel",
    (2022, 4): "Jonathan J. Papik",
    (2022, 6): "John R. Freudenberg",
}
START = re.compile(r"Supreme Court (?:Judge|Justice)|Judge of the Supreme Court|Chief Justice of the Supreme Court")
STOP = re.compile(r"Court of Appeals|Workers")
ROW = re.compile(r"([A-Za-z][A-Za-z.'\-]*(?: [A-Za-z.'\-]+)*)\s+(\d[\d,.]*)\s+(\d[\d,.]*)(?=\s|$)")
NOISE = re.compile(r"(?:Page|Retention|District|Judge|Justice|November|General)")
SUPREME = "Justice of the Supreme Court"
CHIEF = "Chief Justice of the Supreme Court"


def fetch() -> None:
    for _, path in BOOKS.values():
        raw.fetch(SOURCE, BASE + path, suffix=".pdf")


def district_of(counties: set[str]) -> int:
    if len(counties) >= 85:
        return 0
    for district, marker in ((3, "Antelope"), (1, "Lancaster"), (6, "Arthur"), (5, "Adams")):
        if marker in counties:
            return district
    return 4 if "Sarpy" in counties else 2


def number(text: str) -> int:
    return int(re.sub(r"[,.]", "", text))


def groups(lines: list[str]) -> list[tuple[list[tuple[str, int, int]], tuple[int, int] | None]]:
    rows = [
        (index, match.start(), match[1].strip(), number(match[2]), number(match[3]))
        for index, line in enumerate(lines)
        for match in ROW.finditer(line)
        if not NOISE.match(match[1])
    ]
    breaks = {
        index
        for index, line in enumerate(lines)
        if re.search(r"(?:Chief Justice|Justice|Judge)\s+[A-Z]", line)
        or re.match(r"\s*[A-Z][\w.\-]+(?: [A-Z][\w.\-]+)+ 6 yr", line)
    }
    clusters: list[list[tuple[int, int, str, int, int]]] = []
    for row in sorted(rows, key=lambda row: (row[1], row[0])):
        for cluster in clusters:
            if abs(cluster[0][1] - row[1]) <= 3:
                cluster.append(row)
                break
        else:
            clusters.append([row])
    segments: list[list[tuple[int, int, str, int, int]]] = []
    for cluster in clusters:
        cluster.sort()
        current = [cluster[0]]
        for row in cluster[1:]:
            if any(current[-1][0] < index <= row[0] for index in breaks) or row[0] - current[-1][0] > 25:
                segments.append(current)
                current = [row]
            else:
                current.append(row)
        segments.append(current)
    totals = [(k, row) for k, segment in enumerate(segments) for row in segment if row[2].lower() == "total"]
    body = [[row for row in segment if row[2].lower() != "total"] for segment in segments]
    sums = [(sum(row[3] for row in part), sum(row[4] for row in part)) for part in body]
    if not totals:
        return [([(row[2], row[3], row[4]) for row in part], None) for part in body]
    anchor = {k: t for t, (k, _) in enumerate(totals)}
    free = [k for k in range(len(segments)) if k not in anchor]
    found = None
    for pick in itertools.product(range(len(totals)), repeat=len(free)):
        assign = {**anchor, **dict(zip(free, pick, strict=True))}
        if all(
            (
                sum(sums[k][0] for k in assign if assign[k] == t),
                sum(sums[k][1] for k in assign if assign[k] == t),
            )
            == (row[3], row[4])
            for t, (_, row) in enumerate(totals)
        ):
            found = assign
            break
    if found is None:
        found = {**anchor, **{k: 0 for k in free}} if not free else None
    if found is None:
        raise ValueError(f"no assignment of {len(segments)} columns ties to totals {[row[3:] for _, row in totals]}")
    return [
        (
            [(row[2], row[3], row[4]) for k in sorted(found) if found[k] == t for row in body[k]],
            (row[3], row[4]),
        )
        for t, (_, row) in enumerate(totals)
    ]


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '31%%'"""
            ).fetchall()
        }
    out: list[tuple[int, int, str, str, int, int, int | None, bool]] = []
    for year, (_, path) in BOOKS.items():
        if year in SCANNED:
            continue
        pdf = raw.cached(SOURCE, BASE + path).path
        text = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, check=True, text=True
        ).stdout
        started = False
        for page, content in enumerate(text.split("\f"), start=1):
            lines = content.split("\n")
            if not started:
                begin = next(
                    (
                        i
                        for i, line in enumerate(lines)
                        if START.fullmatch(line.strip()) and not re.search(r"\.{5}", line)
                    ),
                    None,
                )
                if begin is None:
                    continue
                started = True
                lines = lines[begin:]
                first = True
            else:
                first = False
            end = next(
                (
                    i
                    for i, line in enumerate(lines)
                    if (i > 0 or not first) and STOP.search(line) and not re.search(r"\.{5}", line)
                ),
                None,
            )
            for body, total in groups(lines if end is None else lines[:end]):
                if not body:
                    continue
                district = district_of({name for name, _, _ in body})
                judge = JUDGES[(year, district)]
                yes, no = sum(row[1] for row in body), sum(row[2] for row in body)
                for name, votes_yes, votes_no in body:
                    out.append(
                        (
                            year,
                            district,
                            judge,
                            fips[re.sub(r"[^a-z]", "", name.lower())],
                            votes_yes,
                            votes_no,
                            page,
                            total is None or (yes, no) == total,
                        )
                    )
            if end is not None:
                break
    printed: dict[tuple[int, int], tuple[int, int]] = {}
    for line in IMAGES.read_text().splitlines()[1:]:
        year, district, judge, county, yes, no, page = line.split("\t")
        key = (int(year), int(district))
        if county == "TOTAL":
            printed[key] = (int(yes), int(no))
            continue
        out.append(
            (
                int(year),
                int(district),
                judge,
                fips[re.sub(r"[^a-z]", "", county.lower())],
                int(yes),
                int(no),
                int(page),
                True,
            )
        )
    frame = pl.DataFrame(
        out,
        schema={
            "year": pl.Int32,
            "district": pl.Int32,
            "judge": pl.String,
            "fips": pl.String,
            "yes": pl.Int64,
            "no": pl.Int64,
            "page": pl.Int32,
            "tied": pl.Boolean,
        },
        orient="row",
    )
    for (year, district), (yes, no) in printed.items():
        part = frame.filter((pl.col("year") == year) & (pl.col("district") == district))
        if (part["yes"].sum(), part["no"].sum()) != (yes, no):
            raise ValueError(f"{year} district {district}: counties do not sum to the printed total")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    summary = frame.group_by("year", "district", "judge").agg(
        pl.len().alias("counties"), pl.col("yes").sum(), pl.col("no").sum(), pl.col("tied").all()
    )
    with pl.Config(tbl_rows=60, tbl_width_chars=160):
        print(summary.sort("year", "district"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nebraska Supreme Court retention questions 2000–2018 and 2022 by county, from the Secretary of "
            "State's official canvass books",
            polity="US-NE",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state_area = conn.execute(
            "select area_id from reporting_unit where scheme_code = 'us.state' and code = '31'"
        ).fetchone()[0]
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/ne/%'").fetchall()}
        elections = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        system = cs.key("us.electoral_system", "retention")
        districts: dict[int, object] = {}
        offices: set[str] = set()
        persons: set[str] = set()
        artifacts: dict[str, object] = {}
        sources: set[object] = set()
        for (year, district, judge), group in frame.group_by("year", "district", "judge", maintain_order=True):
            day, path = BOOKS[year]
            election_key = f"{day.isoformat()}:NE"
            if election_key not in elections:
                print(election_key, "no election, skipped")
                continue
            url = BASE + path
            source = cs.source(PUBLISHER, TITLE.format(year=year))
            if source in cs.minted and source not in sources:
                sources.add(source)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=TITLE.format(year=year),
                    url=LANDING,
                    license="Public record",
                )
            if url not in artifacts:
                artifacts[url] = cs.artifact(raw.cached(SOURCE, url), source)
            title = CHIEF if district == 0 else SUPREME
            office_key = f"{slug(title)}:NE"
            office = cs.key("us.office", office_key)
            if office in cs.minted and office_key not in offices:
                cs.add("office", id=office, polity_id=cs.polity("US-NE"), kind="other")
                name(office, f"{title} of Nebraska")
            offices.add(office_key)
            parts = judge.replace(".", "").split()
            person_key = f"NE:{slug(parts[0])}-{slug(parts[-1])}"
            person = cs.key("us.retention.person", person_key)
            if person in cs.minted and person_key not in persons:
                cs.add("person", id=person)
                name(person, judge)
            persons.add(person_key)
            contest_key = f"{day.isoformat()}:NE:{slug(title)}:{person_key.split(':')[1]}"
            contest = cs.key("us.contest", contest_key)
            if district == 0:
                area = state_area
            else:
                code = f"NE:supreme-{district}"
                if district not in districts:
                    districts[district] = cs.area("us.judicial_district", code)
                    if districts[district] in cs.minted:
                        cs.add("area", id=districts[district], scheme_code="us.judicial_district")
                        cs.add("area_code", area_id=districts[district], code=code, valid_from=None)
                        cs.add("area_parent", area_id=districts[district], parent_id=state_area)
                        name(districts[district], f"Supreme Court Judicial District {district}, NE")
                area = districts[district]
            cs.add(
                "contest",
                id=contest,
                election_id=cs.key("us.election", election_key),
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            if contest in cs.minted:
                name(contest, f"{year} Nebraska {title} retention: {judge}")
                base = f"us/ne/{day.isoformat()}-general/{slug(title)}-{person_key.split(':')[1]}"
                slug_path, count = base, 1
                while slug_path in taken:
                    count += 1
                    slug_path = f"{base}-{count}"
                taken.add(slug_path)
                cs.add("slug", path=slug_path, entity_id=contest)
            options = {}
            for position, answer in enumerate(("yes", "no")):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.capitalize(),
                )
            candidacy = cs.key("us.candidacy", f"{contest_key}:yes:head")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options["yes"],
                role="head",
                ballot_name=judge,
                is_independent=False,
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:official")
            cs.add(
                "result_set",
                id=result_set,
                election_id=cs.key("us.election", election_key),
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
                body_md="Yes and No votes by county from the Nebraska Secretary of State's official canvass book"
                + (", read from the scanned page and checked against the printed total" if year in SCANNED else "")
                + ".",
            )
            if not group["tied"].all():
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The county rows do not add up to the printed total, and are given as printed.",
                )
            if district:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="A county that this district shares with another district is counted for its part "
                    "inside this district, as the book prints it.",
                )
            for fips, yes, no, page in group.select("fips", "yes", "no", "page").iter_rows():
                for answer, votes in (("yes", yes), ("no", no)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", fips),
                        option_id=options[answer],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            for page in sorted(set(group["page"])):
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=source,
                    locator={"url": url, "artifact": str(artifacts[url]), "page": page},
                )
            yes, no = group["yes"].sum(), group["no"].sum()
            retained = yes > no
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options["yes"],
                candidacy_id=candidacy,
                kind="elected" if retained else "lost",
                seats=1 if retained else None,
            )
        return cs.write()
