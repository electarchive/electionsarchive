import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wi_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://elections.wi.gov/sites/default/files/documents/County%20by%20County%20Report%20-%202022%20Partisan%20Primary%20-%20"
FILES = {"Representative%20in%20Congress": "house", "US%20Senator": "senate"}
DAY = date(2022, 8, 9)
PARTIES = {"Democratic": "democratic", "Republican": "republican", "Libertarian": "libertarian", "Constitution": "constitution"}
PUBLISHER = "Wisconsin Elections Commission"
TITLE = "County by County Report, 2022 Partisan Primary"
NAMES = {
    "NEUMANN MARK": "MARK NEUMANN",
    "GODLEWSKI SARAH": "SARAH GODLEWSKI",
    "WILLIAMS DARRELL": "DARRELL WILLIAMS",
    "PECKARSKY PETER": "PETER PECKARSKY",
    "SCHROEDER DAVID": "DAVID SCHROEDER",
    "VANDENPLAS JACOB": "JACOB VANDENPLAS",
    "FITZGERALD SCOTT": "SCOTT FITZGERALD",
    "GROTHMAN GLENN": "GLENN GROTHMAN",
    "KUNELIUS DAVID": "DAVID KUNELIUS",
    "GALLAGHER MIKE": "MIKE GALLAGHER",
    "CLARMONT SHAUN": "SHAUN CLARMONT",
    "MIKE SOMEREN VAN": "MIKE VAN SOMEREN",
    "DERRICK ORDEN VAN": "DERRICK VAN ORDEN",
    "DEB MCGRATH BALDUS": "DEB MCGRATH",
    "RICHARD AUSMAN DICK": "RICHARD AUSMAN",
}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, f"{BASE}{name}.pdf", suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%' order by u.code"""
            ).fetchall()
        }
    rows = []
    for name, kind in FILES.items():
        html = subprocess.run(
            ["pdftotext", "-bbox", str(raw.cached(SOURCE, f"{BASE}{name}.pdf").path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        carry: tuple[int | None, str, list[str] | None] | None = None
        for page in html.split("<page")[1:]:
            words = [(float(a), float(b), float(c), float(d), t) for a, b, c, d, t in WORD.findall(page)]
            ordered: list[list[tuple[float, float, float, float, str]]] = []
            last = -99.0
            for w in sorted(words, key=lambda w: w[1]):
                if w[1] - last > 2.5:
                    ordered.append([])
                ordered[-1].append(w)
                last = w[1]
            ordered = [sorted(line) for line in ordered]
            title = next((" ".join(w[4] for w in line) for line in ordered if line[0][4] in ("REPRESENTATIVE", "UNITED")), "")
            found = re.match(r"(?:REPRESENTATIVE IN CONGRESS DISTRICT (\d+)|UNITED STATES SENATOR) - (\w+)", title)
            if found is None and carry is None:
                continue
            if found is not None:
                if found[2] not in PARTIES:
                    carry = None
                    continue
                carry = (int(found[1]) if found[1] else None, found[2], None)
            district, party = carry[0], carry[1]
            body = []
            for line in ordered:
                label = []
                for w in line:
                    if re.fullmatch(r"[\d,]+", w[4]):
                        break
                    label.append(w[4])
                key = re.sub(r"[^a-z]", "", "".join(label).lower())
                if key in fips and len(label) < len(line):
                    body.append((key, [w for w in line if re.fullmatch(r"[\d,]+", w[4])]))
            if not body:
                continue
            first_row = body[0][1]
            if found is None and carry[2] is not None:
                names = carry[2]
            else:
                names = None
            if names is None:
                top = min(w[1] for line in ordered for w in line if w[4] in ("Total",))
                counties_y = min(w[1] for line in ordered for w in line if re.fullmatch(r"[\d,]+", w[4]) and w[1] > top)
                centers = [(w[0] + w[2]) / 2 for w in first_row]
                heads: list[list[tuple[float, float, str]]] = [[] for _ in centers]
                for w in words:
                    if top - 1 <= w[1] < counties_y - 1 and w[4] not in ("County", "Votes", "Cast") and w[0] > min(c for c in centers) - 60:
                        if w[4].isupper() and w[4] != "SCATTERING":
                            continue
                        c = (w[0] + w[2]) / 2
                        heads[min(range(len(centers)), key=lambda i: abs(centers[i] - c))].append((w[1], w[0], w[4]))
                names = [
                    "WRITE-INS" if "WRITE-IN" in joined else NAMES.get(joined, joined)
                    for joined in (" ".join(t for _, _, t in sorted(h)).upper() for h in heads)
                ]
                carry = (carry[0], carry[1], names)
            for county, cells in body:
                if len(cells) != len(names):
                    continue
                for cand, cell in list(zip(names, cells, strict=True))[1:]:
                    rows.append(
                        (
                            2022,
                            f"{kind}:{PARTIES[party]}",
                            DAY,
                            False,
                            "WI",
                            district,
                            fips[county],
                            cand,
                            1,
                            cand == "SCATTERING",
                            int(cell[4].replace(",", "")),
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "measure": pl.Int16,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(kind=pl.lit("county"), source=pl.lit("WI")).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's 2022 U.S. Senate and House primaries by county",
            polity="US-WI",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://elections.wi.gov/elections/election-results",
            license="Public record",
        )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, f"{BASE}{name}.pdf"), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"WI": source}, strict=True)
        return cs.write()
