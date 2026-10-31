import difflib
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.ar_abstract_1990"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.sos.arkansas.gov/uploads/elections/1990_Election_results.pdf"
PUBLISHER = "Arkansas Secretary of State"
TITLE = "Arkansas Election Results 1990"
PAGES = {
    61: (
        "Lieutenant Governor",
        ("Jim Guy Tucker", "DEMOCRATIC", 494943),
        ('Kenneth "Muskie" Harris', "REPUBLICAN", 174587),
    ),
    63: ("Secretary of State", ('W. J. "Bill" McCuen', "DEMOCRATIC", 397465), ("Bill Kerr", "REPUBLICAN", 245370)),
    65: ("Attorney General", ("Winston Bryant", "DEMOCRATIC", 368787), ("Asa Hutchinson", "REPUBLICAN", 307609)),
    67: (
        "Commissioner of State Lands",
        ("Charlie Daniels", "DEMOCRATIC", 417788),
        ("Irvin L. McKittrick", "REPUBLICAN", 202646),
    ),
}
READ_FIXES = {(63, "pike"): (2137, 1111), (67, "saline"): (11747, 7475)}
METHOD = (
    "County totals come from the Secretary of State's 1990 election results book (a scan), read by optical character "
    "recognition in three passes at different resolutions; a county is accepted only when its two candidates' votes sum "
    "to its printed total, and each candidate's 75 counties sum to the statewide total printed on the page."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '05%%'"""
            ).fetchall()
        }
    pdf = str(raw.cached(SOURCE, URL).path)
    rows = []
    with tempfile.TemporaryDirectory() as scratch:
        for page, (office, first, second) in PAGES.items():
            found: dict[str, set[tuple[int, int]]] = {}
            for dpi, psm in (
                (200, "4"),
                (250, "6"),
                (300, "4"),
                (300, "6"),
                (350, "4"),
                (400, "4"),
                (400, "6"),
                (500, "4"),
                (500, "6"),
            ):
                stem = f"{scratch}/{page}_{dpi}"
                subprocess.run(
                    ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-gray", "-png", pdf, stem],
                    check=True,
                )
                image = next(Path(scratch).glob(f"{page}_{dpi}*.png"))
                text = subprocess.run(
                    ["tesseract", str(image), "-", "--psm", psm], capture_output=True, text=True, check=False
                ).stdout
                for line in text.splitlines():
                    match = re.match(r"^\W*([A-Z][A-Z .]+?)\s+\W*(\d.*)$", line.strip())
                    if match is None:
                        continue
                    name = difflib.get_close_matches(re.sub(r"[^a-z]", "", match[1].lower()), codes, n=1, cutoff=0.8)
                    figures = [re.sub(r"[,.]", "", n) for n in re.findall(r"\d[\d,.]*", match[2])]
                    if name and len(figures) >= 5 and all(figures[i].isdigit() for i in (0, 2, 4)):
                        a, b, total = int(figures[0]), int(figures[2]), int(figures[4])
                        shares = re.findall(r"\d+[,.]\d\d(?!\d)", match[2])
                        share = float(shares[0].replace(",", ".")) if shares else None
                        if a + b == total:
                            found.setdefault(name[0], set()).add(
                                (a, b, share is not None and abs(a / total * 100 - share) < 0.02)
                            )
            found = {k: {t for t in v if t[2]} or v if len({t[:2] for t in v}) > 1 else v for k, v in found.items()}
            found = {k: {t[:2] for t in v} for k, v in found.items()}
            for k, v in found.items():
                if len(v) > 1:
                    v -= {t for other, w in found.items() if other != k and len(w) == 1 for t in w}
            for (fixed_page, county), votes in READ_FIXES.items():
                if fixed_page == page:
                    found[county] = {votes}
            if len(found) != 75 or any(len(v) != 1 for v in found.values()):
                raise ValueError(
                    f"page {page}: {len(found)} counties, ambiguous {[(k, v) for k, v in found.items() if len(v) != 1]}, missing {sorted(set(codes) - set(found))}"
                )
            for index, (candidate, party, total) in enumerate((first, second)):
                if sum(next(iter(v))[index] for v in found.values()) != total:
                    raise ValueError(
                        f"page {page} {candidate}: counties sum to {sum(next(iter(v))[index] for v in found.values())}, printed {total}"
                    )
                for name, v in found.items():
                    rows.append((office, codes[name], candidate.upper(), party, next(iter(v))[index]))
    frame = pl.DataFrame(rows, schema=["title", "county", "candidate", "party", "votes"], orient="row").select(
        year=pl.lit(1990, pl.Int32),
        day=pl.lit("1990-11-06"),
        state=pl.lit("AR"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes=pl.col("votes").cast(pl.Int64),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(75),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate", "party").agg(pl.col("votes").sum()).sort("title", "votes").rows())


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
                url="https://sos.arkansas.gov/elections/research/election-results",
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        return {"AR": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Arkansas's 1990 lieutenant governor, secretary of state, attorney general, and land commissioner "
        "by county from the Secretary of State's election results book",
        cite=cite,
        method=METHOD,
    )
