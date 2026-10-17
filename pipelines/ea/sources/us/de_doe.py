import html
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.de_doe"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = Path.home() / "Downloads" / "ea-manual" / "delaware"
ARCHIVE = "https://elections.delaware.gov/elections/resultsarchive/"
FILES = {
    2000: ("Election Results - Department of Elections - State of Delaware.html", ARCHIVE + "elect00/2000gened.shtml"),
    2002: ("elect02_edrd.txt", ARCHIVE + "elect02/elect02_general/elect02_edrd.txt"),
    2004: ("elect04_general_edrd.txt", ARCHIVE + "elect04/elect04_general/elect04_general_edrd.txt"),
    2006: ("elect06_general.edrd.txt", ARCHIVE + "elect06/elect06_general/elect06_general.edrd.txt"),
    2008: ("elect08_gen_edrd.txt", ARCHIVE + "elect08/elect08_general_election/elect08_gen_edrd.txt"),
    2010: ("elect10_stwoff_kns.txt", ARCHIVE + "elect10/elect10_General/data/stwoff_kns.txt"),
    2012: ("elect12_stwoff_kns.txt", ARCHIVE + "elect12/elect12_general/data/stwoff_kns.txt"),
    2014: ("elect14_stwoff_kns.txt", ARCHIVE + "elect14/elect14_General/data/stwoff_kns.txt"),
}
OFFICES = {
    "STATE TREASURER": "Treasurer",
    "AUDITOR OF ACCOUNTS": "Auditor",
    "INSURANCE COMMISSIONER": "Insurance Commissioner",
}
COUNTIES = {"New Castle": "10003", "Kent": "10001", "Sussex": "10005"}
PARTIES = {
    "DEMOCRATIC": "DEMOCRAT",
    "REPUBLICAN": "REPUBLICAN",
    "LIBERTARIN": "LIBERTARIAN",
    "LIBERTARIAN": "LIBERTARIAN",
    "GREEN": "GREEN",
    "CONSTITUTN": "CONSTITUTION",
    "IND OF DEL": "INDEPENDENT PARTY OF DELAWARE",
    "NATURAL LW": "NATURAL LAW",
    "BLUE ENAMEL": "BLUE ENAMEL",
    "WFP": "WORKING FAMILIES",
}
PUBLISHER = "Delaware Department of Elections"
TITLE = "Election results archive"
METHOD = (
    "Totals come from the Delaware Department of Elections' official results archive. For 2000–2008, totals are the "
    "sums of the election district rows, checked against the representative district and candidate totals in the same "
    "file. For 2010–2014, totals are the county columns of the statewide offices by county file. The 2010–2014 files "
    "list candidates without party labels."
)


def fetch() -> None:
    for name, url in FILES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / name
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".html" if name.endswith(".html") else ".txt",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="text/html" if name.endswith(".html") else "text/plain",
            )


def parse() -> None:
    with connect() as conn:
        districts = {
            (int(code[:4]), code.rpartition(":")[2]): code.split(":")[2]
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(0[0-8]):DE:100[0-9]{2}::'"
            ).fetchall()
        }
    rows: list[tuple[int, str, str, str | None, str, str, int]] = []
    for year, (_, url) in FILES.items():
        text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
        if year >= 2010:
            office = None
            for line in text.splitlines():
                cells = [cell.strip() for cell in line.split(";")]
                if len(cells) < 5:
                    continue
                if not any(cells[1:5]):
                    office = OFFICES.get(cells[0])
                    continue
                if office is None or not re.fullmatch(r"[\d,]+", cells[4]) or re.search(r"(?i)\btotal\b", cells[0]):
                    continue
                counts = [int(cell.replace(",", "") or 0) for cell in cells[1:5]]
                if sum(counts[:3]) != counts[3]:
                    raise ValueError(f"{year} {office} {cells[0]}: counties don't add up to {counts[3]}")
                for county, votes in zip(COUNTIES.values(), counts[:3], strict=True):
                    words = cells[0].split()
                    suffix = [word.rstrip(".") for word in words if re.fullmatch(r"(JR|SR|II|III|IV)\.?", word)]
                    rest = [word for word in words if word.rstrip(".") not in suffix]
                    name = " ".join([*rest[1:], rest[0], *suffix])
                    rows.append((year, office, name, None, county, county, votes))
            continue
        if year == 2000:
            text = html.unescape(re.sub(r"<[^>]+>", "", re.split(r"</?pre[^>]*>", text, flags=re.IGNORECASE)[1]))
        lines = text.splitlines()
        sums: dict[str, list[int]] = {}
        widths: dict[str, int] = {}
        printed: dict[str, str] = {}
        for index, line in enumerate(lines):
            header = re.match(r"\s*(DISTRICT|District)\s+(TOTAL|Total)", line)
            if header is None:
                continue
            title = next(lines[back].strip() for back in range(index - 1, -1, -1) if lines[back].strip())
            if title not in OFFICES:
                continue
            following = [other for other in lines[index + 1 : index + 12] if other.strip()]
            names = [(match.start(), match[0]) for match in re.finditer(r"\S+(?: \S+)*", line[header.end() :])]
            names = [(start + header.end(), name) for start, name in names]
            parties = {match.start(): match[0] for match in re.finditer(r"\S+(?: \S+)*", following[0])}
            labels = [match.end() for match in re.finditer(r"M/C|ABSNT|ABS|TOTAL", following[1])]
            ends = [header.end() - 1, *(labels or [start + 9 for start, _ in names])]
            width = widths[title] = 3 if labels else 1
            candidates = [
                (name, PARTIES.get(next((text for at, text in parties.items() if abs(at - start) <= 2), ""), None))
                for start, name in names
            ]
            page = [0] * (len(ends) - 1)
            for row in lines[index + 1 :]:
                found = re.match(r"\s*(\d\d-\d\d|RD TOT|RD Tot|CAND TOT|Cand Tot)\s", row)
                if re.match(r"\s*(DISTRICT|District)\s+(TOTAL|Total)", row) or (row.strip() in OFFICES):
                    break
                if found is None:
                    continue
                if found[1].upper() == "CAND TOT":
                    printed[title] = re.sub(r"\D", "", row)
                    break
                slots = [0] * (len(ends) - 1)
                for number in re.finditer(r"\d+", row[found.end() :]):
                    end = number.end() + found.end()
                    slot = min(range(len(ends)), key=lambda at: abs(ends[at] - end))
                    if abs(ends[slot] - end) > 2:
                        raise ValueError(f"{year} {title} {found[1]}: {number[0]} lines up with no column")
                    if slot > 0:
                        slots[slot - 1] = int(number[0])
                if width == 3 and any(slots[at] + slots[at + 1] != slots[at + 2] for at in range(0, len(slots), 3)):
                    raise ValueError(f"{year} {title} {found[1]}: machine and absentee don't add up")
                if found[1].upper() == "RD TOT":
                    if slots != page:
                        raise ValueError(f"{year} {title}: a representative district's rows don't add up")
                    sums[title] = [a + b for a, b in zip(sums.get(title, [0] * len(slots)), slots, strict=True)]
                    page = [0] * len(slots)
                    continue
                page = [a + b for a, b in zip(page, slots, strict=True)]
                county = districts.get((year, found[1]))
                if county is None:
                    raise ValueError(f"{year}: no county for election district {found[1]}")
                for (name, party), votes in zip(candidates, slots[width - 1 :: width], strict=True):
                    rows.append(
                        (
                            year,
                            OFFICES[title],
                            re.sub(r"^(.+?)((?: [A-Z])+)$", r"\2 \1", name).strip(),
                            party,
                            county,
                            found[1],
                            votes,
                        )
                    )
        for title, digits in printed.items():
            if "".join(str(value) for value in sums[title]) not in digits:
                raise ValueError(f"{year} {title}: the candidate totals don't match the printed line {digits}")
            print(year, title, sums[title][widths[title] - 1 :: widths[title]])
        if set(printed) != {title for title in OFFICES if title in sums}:
            raise ValueError(f"{year}: an office has no printed candidate totals")
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "title": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "county": pl.String,
            "district": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        day=pl.col("year").map_elements(
            lambda year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), return_dtype=pl.Date
        ),
        writein=pl.col("candidate").str.contains("(?i)write"),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("district") != pl.col("county")).write_parquet(PARSED / "precincts.parquet")
    returns = (
        frame.group_by("year", "day", "title", "county", "candidate", "party", "writein", maintain_order=True)
        .agg(pl.col("votes").sum())
        .select(
            "year",
            "day",
            state=pl.lit("DE"),
            title="title",
            post=pl.lit(None, pl.String),
            special=pl.lit(False),
            magnitude=pl.lit(1, pl.Int16),
            kind=pl.lit("county"),
            code="county",
            county="county",
            candidate=pl.col("candidate").str.to_uppercase(),
            party="party",
            writein="writein",
            measure=pl.lit(1, pl.Int16),
            votes="votes",
        )
    )
    contest = ["year", "day", "state", "title", "post", "special"]
    returns.write_parquet(PARSED / "returns.parquet")
    returns.group_by(contest, maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(3),
    ).write_parquet(PARSED / "races.parquet")
    print(
        returns.group_by("year", "title", "candidate")
        .agg(pl.col("votes").sum())
        .sort("year", "title", "votes", descending=[False, False, True])
        .rows()
    )


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elections.delaware.gov/elections/resultsarchive/",
                license="Public record",
            )
        for _, url in FILES.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        return {"DE": source}

    races = pl.read_parquet(PARSED / "races.parquet")
    precincts = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        keys = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"])
        }
        if len(keys) < races.height:
            return medsl_statewide.emit(
                pipeline=SOURCE,
                version=VERSION,
                parsed=PARSED,
                summary="Delaware's treasurer, auditor, and insurance commissioner elections 2000–2014 from the "
                "Department of Elections' results archive",
                cite=cite,
                method=METHOD,
            )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Delaware's treasurer, auditor, and insurance commissioner elections 2000–2008 by election "
            "district from the Department of Elections' results archive",
            polity="US",
        )
        source = cite(cs)["DE"]
        values = []
        for (day, office), rows in precincts.group_by("day", "title", maintain_order=True):
            key = f"{day}:DE:{slug(str(office))}"
            if f"{key}:precinct" in keys:
                continue
            ((contest, election, default),) = conn.execute(
                """select c.id, c.election_id, rs.id from external_id x join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            totals = {
                int(total): option
                for option, total in conn.execute(
                    """select o.id, sum(v.value) from contest_option o
                       join result_set rs on rs.id = %s
                       join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                       where o.contest_id = %s group by o.id""",
                    [default, contest],
                ).fetchall()
            }
            sums = rows.group_by("candidate", "party").agg(pl.col("votes").sum())
            if sums.height != len(totals) or set(sums["votes"]) != set(totals):
                raise ValueError(f"{key}: precinct sums don't match the county set's options")
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
                completeness=1,
            )
            for option, kind, seats in conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [default]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            options = {
                f"{candidate}|{party or ''}": str(totals[int(votes)]) for candidate, party, votes in sums.iter_rows()
            }
            values.append(
                rows.filter(pl.col("votes") > 0).select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.struct("year", "county", "district").map_elements(
                        lambda row: str(cs.unit("us.precinct", f"{row['year']}:DE:{row['county']}::{row['district']}")),
                        return_dtype=pl.String,
                    ),
                    option_id=pl.concat_str("candidate", pl.lit("|"), pl.col("party").fill_null("")).replace_strict(
                        options
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
        print(f"{len(values)} precinct sets")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
