import html
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import ar_abstract_2002, medsl
from ea.store import DATA, connect

SOURCE = "us.fp_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOCUMENTS = {
    "AR": (
        ar_abstract_2002.SOURCE,
        ar_abstract_2002.URL,
        ar_abstract_2002.PUBLISHER,
        ar_abstract_2002.TITLE,
        "https://sos.arkansas.gov/elections/research/election-results",
    ),
    "CA2000": (
        SOURCE,
        "https://elections.cdn.sos.ca.gov/sov/2000-general/us-sen.pdf",
        "California Secretary of State",
        "Statement of Vote, November 7, 2000 General Election",
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
    ),
    **{
        f"IN2000{office[0]}": (
            SOURCE,
            f"https://web.archive.org/web/{stamp}id_/http://www.state.in.us:80/serv/sos_ge2000v?act=district&district=Statewide&office={office}",
            "Indiana Secretary of State",
            "2000 General Election Results",
            "http://www.state.in.us/serv/sos_ge2000v",
        )
        for office, stamp in (("US+Senator", "20020905180654"), ("Governor", "20021013053937"))
    },
    "ME2014": (
        SOURCE,
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2014/20141104/elec_20141104aj.xlsx",
        "Maine Department of the Secretary of State",
        "Election results: general election tabulations by municipality",
        "https://lldc.mainelegislature.org/Open/Elections/Returns/2014/20141104/",
    ),
}


def fetch() -> None:
    for cached, url, *_ in DOCUMENTS.values():
        if cached == SOURCE:
            raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def counties(conn: Connection, fips: str) -> dict[str, str]:
    return {
        re.sub(r"[^a-z]", "", text.lower()): code
        for text, code in conn.execute(
            """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code like %s""",
            [f"{fips}%"],
        ).fetchall()
    }


def arkansas(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "05")
    text = pdf("AR")
    line = re.compile(r"^(?:Governor )?(.+?) - (Democrat|Republican|Write-In)\s+([\d,]+)\s+[\d.]+%$")
    county = None
    statewide: dict[str, int] = {}
    rows = []
    reading = False
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if heading := re.fullmatch(r"Governor(?: \((.+?) County\))?", stripped):
            reading, county = True, heading[1]
            continue
        if not reading:
            continue
        if match := line.match(stripped):
            name = "WRITE-INS" if match[2] == "Write-In" else match[1].upper()
            votes = int(match[3].replace(",", ""))
            if county is None:
                statewide[name] = statewide.get(name, 0) + votes
            else:
                rows.append((date(2002, 11, 5), "governor", codes[re.sub(r"[^a-z]", "", county.lower())], name, votes))
            continue
        if not stripped.startswith(("Over", "Under")):
            reading = False
    for name, total in statewide.items():
        summed = sum(votes for *_, candidate, votes in rows if candidate == name)
        if summed != total:
            raise ValueError(f"AR governor {name}: counties sum to {summed}, statewide {total}")
    if len({code for _, _, code, _, _ in rows}) != 75:
        raise ValueError("AR governor: not 75 counties")
    return rows


def california(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "06")
    names = ["FEINSTEIN", "CAMPBELL", "TEMPLIN", "BENJAMIN", "LIGHTFOOT", "REES", "CAMAHORT", "JONES"]
    text = pdf("CA2000")
    rows = []
    totals = None
    for line in text.splitlines():
        if not (match := re.fullmatch(r"(\S.*?)\s{2,}((?:[\d,]+\s+){7}[\d,]+)", line.strip())):
            continue
        votes = [int(value.replace(",", "")) for value in match[2].split()]
        if match[1] == "State Totals":
            totals = votes
            continue
        code = codes[re.sub(r"[^a-z]", "", match[1].lower())]
        rows += [(date(2000, 11, 7), "senate", code, name, value) for name, value in zip(names, votes, strict=True)]
    if totals is None or len(rows) != 58 * 8:
        raise ValueError("CA Senate: missing totals or counties")
    for name, total in zip(names, totals, strict=True):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != total:
            raise ValueError(f"CA Senate {name}: counties sum to {summed}, statewide {total}")
    return rows


def maine(conn: Connection) -> list[tuple[date, str, str, str, int]]:
    towns: dict[str, list[str]] = {}
    for code, text in conn.execute(
        """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id
           where u.scheme_code = 'us.town' and u.code like '23%%'"""
    ).fetchall():
        towns.setdefault(re.sub(r"[^a-z]", "", text.removesuffix(", ME").lower()), []).append(code)
    codes = counties(conn, "23")
    source, url, *_ = DOCUMENTS["ME2014"]
    cells = pl.read_excel(raw.cached(source, url).path, has_header=False, infer_schema_length=0).rows()
    names = ["BELLOWS", "COLLINS", "WRITE-INS", "CHRISTOPHER A. LYONS", "DERIL V. STUBENROD"]
    rows: list[tuple[date, str, str, str, int]] = []
    pending: list[tuple[str, list[int]]] = []
    totals = None
    for row in cells[3:]:
        if row[0] is None:
            continue
        label = re.sub(r"\s+", " ", row[0]).strip()
        votes = [int(value or 0) for value in row[1:6]]
        if label == "Statewide Totals":
            totals = votes
            continue
        if label.endswith("County Totals"):
            county = codes[re.sub(r"[^a-z]", "", label.removesuffix(" County Totals").lower())]
            for place, counted in pending:
                key = re.sub(r"^saint", "st", re.sub(r"[^a-z]", "", place.lower()))
                found = {
                    code
                    for code in towns.get(key, []) + towns.get(key.removesuffix("plt") + "plantation", [])
                    if code.startswith(county)
                }
                code = found.pop() if len(found) == 1 else f"2014:ME:{county}:{place}"
                rows += [
                    (date(2014, 11, 4), "senate", code, name, value) for name, value in zip(names, counted, strict=True)
                ]
            pending = []
            continue
        if label in ("STATE UOCAVA", "EMERGENCY UTILITY WORKERS"):
            rows += [
                (date(2014, 11, 4), "senate", f"2014:ME:{label}", name, value)
                for name, value in zip(names, votes, strict=True)
            ]
            continue
        pending.append((label, votes))
    if totals is None or pending:
        raise ValueError("ME Senate: missing totals or a town after the last county")
    for index, name in enumerate(names[:2]):
        if (summed := sum(r[4] for r in rows if r[3] == name)) != totals[index]:
            raise ValueError(f"ME Senate {name}: towns sum to {summed}, statewide {totals[index]}")
    if sum(r[4] for r in rows) != sum(totals):
        raise ValueError("ME Senate: write-ins do not tie")
    return rows


def indiana(conn: Connection, document: str) -> list[tuple[date, str, str, str, int]]:
    codes = counties(conn, "18")
    rows = []
    chamber = "senate" if document == "IN2000U" else "governor"
    source, url, *_ = DOCUMENTS[document]
    text = raw.cached(source, url).path.read_text("latin-1")
    heads = [
        html.unescape(re.sub(r"<[^>]+>", "", cell)).replace("\xa0", " ").strip()
        for cell in re.findall(r"<th[^>]*>(.*?)</th>", text, re.DOTALL | re.IGNORECASE)
    ]
    names = [re.sub(r"\s*\(.*", "", head).upper() for head in heads[1:4]]
    totals = [int(value) for value in heads[7:10]]
    found = re.findall(
        r'county=([^"&]+)"[^>]*>[^<]*</a>\s*</td>((?:\s*<td[^>]*>[^<]*</td>)+)', text, re.DOTALL | re.IGNORECASE
    )
    if len(found) != 92:
        raise ValueError(f"IN {chamber}: {len(found)} counties")
    for county, cells in found:
        votes = [int(value) for value in re.findall(r">(\d+)", cells)[:3]]
        code = codes[re.sub(r"[^a-z]", "", html.unescape(county).replace("+", " ").lower())]
        rows += [(date(2000, 11, 7), chamber, code, name, value) for name, value in zip(names, votes, strict=True)]
    for name, total in zip(names, totals, strict=True):
        if (summed := sum(r[4] for r in rows if r[3] == name and r[1] == chamber)) != total:
            raise ValueError(f"IN {chamber} {name}: counties sum to {summed}, statewide {total}")
    return rows


def pdf(document: str) -> str:
    source, url, *_ = DOCUMENTS[document]
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(source, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def parse() -> None:
    with connect() as conn:
        rows = [
            (document, *row)
            for document, read in (
                ("AR", arkansas),
                ("CA2000", california),
                ("ME2014", maine),
                ("IN2000U", lambda conn: indiana(conn, "IN2000U")),
                ("IN2000G", lambda conn: indiana(conn, "IN2000G")),
            )
            for row in read(conn)
        ]
    frame = (
        pl.DataFrame(rows, schema=["source", "day", "chamber", "code", "candidate", "votes"], orient="row")
        .with_columns(state=pl.col("source").str.slice(0, 2))
        .group_by("source", "state", "day", "chamber", "code", "candidate")
        .agg(pl.col("votes").sum())
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            district=pl.lit(None, pl.Int16),
            special=pl.lit(False),
            writein=pl.col("candidate") == "WRITE-INS",
            measure=pl.lit(1, pl.Int16),
            kind=pl.when(pl.col("code").str.contains(r"^\d{5}$"))
            .then(pl.lit("county"))
            .when(pl.col("code").str.contains(r"^\d{10}$"))
            .then(pl.lit("town"))
            .otherwise(pl.lit("aggregate")),
        )
    )
    print(frame.group_by("state", "chamber", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("state", "votes"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor and U.S. Senate generals by county from state canvasses, for contests whose only "
            "county results were Algara and Amlani's or ICPSR's",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for document in frame["source"].unique().to_list():
            cached, url, publisher, title, home = DOCUMENTS[document]
            source = cs.source(publisher, title)
            if source in cs.minted and source not in sources.values():
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=home,
                    license="Public record",
                )
            sources[document] = source
            cs.artifact(raw.cached(cached, url), source)
        cs.add("scheme", code="us.aggregate", polity_id=cs.polity("US"), kind="synthetic", rank=3, noun="aggregate")
        for code, state in frame.filter(pl.col("kind") == "aggregate").select("code", "state").unique().iter_rows():
            polity, unit, area = conn.execute(
                """select p.id, u.id, u.area_id from reporting_unit u join polity p on p.id = u.polity_id
                   where u.scheme_code = 'us.state' and p.iso_code = %s""",
                [f"US-{state}"],
            ).fetchone()
            county = code.split(":")[2] if re.fullmatch(r"\d{5}", code.split(":")[2]) else None
            cs.add(
                "reporting_unit",
                id=cs.unit("us.aggregate", code),
                polity_id=polity,
                scheme_code="us.aggregate",
                code=code,
                parent_id=unit if county is None else cs.unit("us.county", county),
                area_relation="within",
                area_id=area if county is None else cs.area("us.county", county),
                kind="aggregate",
                valid_from=None,
                valid_to=None,
            )
        towns = frame.group_by("source").agg((pl.col("kind") != "county").any().alias("towns"))
        for grain, documents in (
            ("county", towns.filter(~pl.col("towns"))["source"]),
            ("town", towns.filter(pl.col("towns"))["source"]),
        ):
            if len(documents):
                medsl.attach(
                    cs,
                    conn,
                    frame.filter(pl.col("source").is_in(documents)),
                    sources,
                    grain=grain,
                    strict=True,
                    extend=True,
                )
        return cs.write()
