import base64
import json
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.nc_abstract_1988"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Abstract of votes cast for Council of State at the general election held on November 8, 1988"
RECORD = "https://digital.ncdcr.gov/Documents/Detail/4199398"
RECORD_GOVERNOR = "https://digital.ncdcr.gov/Documents/Detail/4199437"
DAY = date(1988, 11, 8)
HELD: set[str] = set()
GOVERNOR = ("Governor", [("Robert B. Jordan III", "DEMOCRATIC"), ("James G. Martin", "REPUBLICAN")])
VARIANTS = (200, 300)
PROMPT = (
    "This is a typed table of county vote totals from a North Carolina State Board of Elections abstract. "
    "Transcribe every county row and the final TOTAL row, if present on this page, exactly as printed, "
    "using only the first two numeric columns (the governor's race: Jordan, then Martin). "
    "Where a figure is crossed out with a handwritten replacement, give the handwritten figure. "
    'Answer with only a JSON array of arrays: ["COUNTY NAME", Jordan votes, Martin votes]. '
    "Votes are integers without commas."
)
SHEETS = {
    (1, 2): [
        ("Secretary of State", [("Rufus L. Edmisten", "DEMOCRATIC"), ("John H. Carrington", "REPUBLICAN")]),
        ("Attorney General", [("Lacy H. Thornburg", "DEMOCRATIC"), ("Sam Wilson", "REPUBLICAN")]),
        ("Commissioner of Labor", [("John C. Brooks", "DEMOCRATIC"), ("Richard D. (Dick) Levy", "REPUBLICAN")]),
        ("Treasurer", [("Harlan E. Boyles", "DEMOCRATIC"), ("Nancy Lake Coward", "REPUBLICAN")]),
    ],
    (5, 6): [("Lieutenant Governor", [("Tony Rand", "DEMOCRATIC"), ("Jim Gardner", "REPUBLICAN")])],
    (3, 4): [
        ("Auditor", [("Edward Renfrow", "DEMOCRATIC"), ("Edward Garner, Jr.", "REPUBLICAN")]),
        ("Commissioner of Agriculture", [("James A. (Jim) Graham", "DEMOCRATIC"), ("Leo Tew", "REPUBLICAN")]),
        ("Commissioner of Insurance", [("Jim Long", "DEMOCRATIC"), ("H. L. (Pete) Rednour", "REPUBLICAN")]),
        ("Superintendent of Public Instruction", [("Bob Etheridge", "DEMOCRATIC"), ("Tom Rogers", "REPUBLICAN")]),
    ],
}
METHOD = (
    "County totals come from the State Board of Elections' abstract of votes cast for the governor and Council of State at the 1988 "
    "general election (a dot-matrix scan), transcribed from page images in two independent reads and checked so that "
    "every candidate's county sum equals the printed state total."
)


def url(page: int) -> str:
    return f"{RECORD_GOVERNOR if page > 4 else RECORD}#page-{page - 4 if page > 4 else page:02d}"


def addresses() -> list[str]:
    return [url(page) for pair in SHEETS for page in pair]


def fetch() -> None:
    for record, offset in ((RECORD, 0), (RECORD_GOVERNOR, 4)):
        detail = subprocess.run(
            ["curl", "-sL", "-m", "60", "-A", "Mozilla/5.0", record], capture_output=True, text=True, check=True
        ).stdout
        items = json.loads(re.search(r"var viewerItems = (\[.*?\]);\n", detail, re.DOTALL)[1])
        for item in items:
            number = int(item["path"].rsplit("-", 1)[1]) + offset
            pdf = subprocess.run(
                [
                    "curl",
                    "-sL",
                    "-m",
                    "120",
                    "-A",
                    "Mozilla/5.0",
                    item["pdfUrl"].replace("[IMAGETOKEN]", item["imageToken"]),
                ],
                capture_output=True,
                check=True,
            ).stdout
            raw.store(
                SOURCE, url(number), pdf, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf"
            )
    print(scan())


def key(page: int, dpi: int) -> str:
    return f"seen:{page}:{dpi}"


def scan() -> str:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for page in (5, 6):
        for dpi in VARIANTS:
            with tempfile.TemporaryDirectory() as folder:
                prefix = Path(folder) / "page"
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        str(dpi),
                        "-gray",
                        "-png",
                        "-singlefile",
                        str(raw.cached(SOURCE, url(page)).path),
                        str(prefix),
                    ],
                    check=True,
                )
                data = base64.b64encode(prefix.with_suffix(".png").read_bytes()).decode()
            params = MessageCreateParamsNonStreaming(
                model="claude-haiku-5-5",
                max_tokens=4000,
                thinking={"type": "disabled"},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                            {"type": "text", "text": PROMPT},
                        ],
                    }
                ],
            )
            wanted[key(page, dpi)] = (params, (6000 * price_in + 2500 * price_out) / 1e6)
    return batch.ensure(SOURCE, "seen", wanted)[1]


def seen(page: int, dpi: int) -> dict[str, list[int]]:
    blocks = json.loads(raw.cached(SOURCE, key(page, dpi)).path.read_text())["content"]
    text = next(block["text"] for block in blocks if block.get("type") == "text")
    found = re.search(r"\[.*\]", text, re.DOTALL)
    return {
        str(row[0]).upper(): [int(str(v).replace(",", "")) for v in row[1:]]
        for row in (json.loads(found[0]) if found else [])
    }


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%%'"""
            ).fetchall()
        }
    table = pl.read_csv(Path(__file__).with_suffix(".tsv"), separator="\t")
    rows: list[tuple[str, str, str, str, int]] = []
    for pair, offices in SHEETS.items():
        body = table.filter(pl.col("page").is_in(pair))
        total = body.filter(pl.col("county") == "TOTAL")
        body = body.filter(pl.col("county") != "TOTAL")
        if body.height != 100:
            raise ValueError(f"pages {pair}: {body.height} counties")
        start = 0
        for office, candidates in offices:
            for offset, (candidate, party) in enumerate(candidates):
                column = f"c{start + offset}"
                if office in HELD:
                    continue
                if body[column].sum() != total[column].item():
                    raise ValueError(
                        f"{office} {candidate}: {body[column].sum()} against printed {total[column].item()}"
                    )
                for name, votes in zip(body["county"], body[column], strict=True):
                    rows.append((office, candidate, party, codes[re.sub(r"[^a-z]", "", name.lower())], votes))
            start += len(candidates)
    reads = [{**seen(5, dpi), **seen(6, dpi)} for dpi in VARIANTS]
    printed = next(read.get("TOTAL") for read in reads if "TOTAL" in read)
    governor: dict[str, list[int]] = {}
    bad = []
    for name in table.filter(pl.col("page").is_in((5, 6)) & (pl.col("county") != "TOTAL"))["county"]:
        first, second = (read.get(name) for read in reads)
        if first is None or first != second:
            bad.append((name, first, second))
        else:
            governor[name] = first
    if bad or printed is None:
        raise ValueError(f"governor reads disagree: {bad}")
    for offset, (candidate, party) in enumerate(GOVERNOR[1]):
        if sum(v[offset] for v in governor.values()) != printed[offset]:
            raise ValueError(
                f"Governor {candidate}: {sum(v[offset] for v in governor.values())} against printed {printed[offset]}"
            )
        for name, values in governor.items():
            rows.append((GOVERNOR[0], candidate, party, codes[re.sub(r"[^a-z]", "", name.lower())], values[offset]))
    frame = pl.DataFrame(rows, schema=["office", "candidate", "party", "county", "votes"], orient="row").select(
        year=pl.lit(1988, pl.Int32),
        day=pl.lit(str(DAY)),
        state=pl.lit("NC"),
        title="office",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate=pl.col("candidate").str.to_uppercase(),
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
        counties=pl.lit(100),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("title", "candidate").agg(pl.col("votes").sum()).sort("title", "votes").rows())


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
                url=RECORD,
                license="Public record",
            )
        for address in addresses():
            cs.artifact(raw.cached(SOURCE, address), source)
        return {"NC": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="North Carolina's 1988 governor, lieutenant governor, secretary of state, auditor, treasurer, superintendent of public instruction, "
        "attorney general, and commissioners of agriculture, insurance, and labor by county from the State Board of "
        "Elections' abstract of votes cast",
        cite=cite,
        method=METHOD,
        supersede=True,
    )
