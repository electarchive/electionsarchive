import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.ga_sos_web"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGE = "http://web.archive.org/web/2005id_/http://www.sos.state.ga.us/elections/election_results/1990/{}.htm"
PAGE98 = "http://web.archive.org/web/0id_/http://www.sos.state.ga.us/elections/election_results/1998_1103/{}.htm"
DAY = date(1990, 11, 6)
DAY98 = date(1998, 11, 3)
PUBLISHER = "Georgia Secretary of State"
TITLE = "General Election Results"
OFFICES = {
    "ltgov": ("Lieutenant Governor", None),
    "secstat": ("Secretary of State", None),
    "attygen": ("Attorney General", None),
    "agri": ("Commissioner of Agriculture", None),
    "insur": ("Commissioner of Insurance", None),
    "labor": ("Commissioner of Labor", None),
    "school": ("School Superintendent", None),
    "psc1": ("Public Service Commissioner", "1"),
    "psc2": ("Public Service Commissioner", "2"),
}
OFFICES98 = {
    "0000300": ("Lieutenant Governor", False, ["Mark Taylor", "Mitch Skandalakis", "Lloyd E. Russell", "Michael A. Novosel"]),
    "0000400": ("Secretary of State", False, ["Cathy Cox", "John A. McCallum", "Mark Antieau"]),
    "0000500": ("Attorney General", False, ["Thurbert Baker", "David Ralston", "Walker Chandler"]),
    "0000600": ("Commissioner of Agriculture", False, ["Tommy Irvin", "Bob Greer", "Bierley"]),
    "0000700": ("Commissioner of Insurance", False, ["Henrietta Canty", "John Oxendine", "Joshua Batchelder"]),
    "0000800": ("School Superintendent", False, ["Joe Martin", "Linda Schrenko", "Michael Cartwright"]),
    "0000900": ("Commissioner of Labor", False, ["Mike Thurmond", "John F. Collins"]),
    "0001000": ("Public Service Commissioner", False, ["Anna Hargis", "Bobby Baker"]),
    "0001100": ("Public Service Commissioner", True, ["Jim Cole", "Jim Kulstad", "Lauren McDonald"]),
}
PARTIES = {"Dem": "DEMOCRAT", "Rep": "REPUBLICAN", "Lib": "LIBERTARIAN", "D": "DEMOCRAT", "R": "REPUBLICAN", "Reform": "REFORM"}
METHOD = (
    "County totals come from the Georgia Secretary of State's General Election Results pages for 1990 and 1998, archived from "
    "sos.state.ga.us. Every candidate's county votes sum to the statewide total the page prints."
)


def fetch() -> None:
    for page in OFFICES:
        raw.fetch(SOURCE, PAGE.format(page), suffix=".html")
    for page in OFFICES98:
        raw.fetch(SOURCE, PAGE98.format(page), suffix=".html")


def _lines(path: Path, split: str) -> list[str]:
    text = path.read_text(errors="ignore")
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<!--.*?-->", "", text)
    text = re.sub(split, "\n", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in html.unescape(re.sub(r"<[^>]+>", " ", text)).split("\n")]
    return [line for line in lines if line]


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["GA"]
    rows: list[tuple[int, str, bool, str, str, str | None, int]] = []

    def take(year: int, title: str, special: bool, cells: dict[str, list[int]], candidates: list[tuple[str, str | None]], printed: list[int]) -> None:
        if len(cells) != 159 or any(len(votes) != len(candidates) for votes in cells.values()):
            raise ValueError(f"{year} {title}: {len(cells)} counties, row lengths {sorted({len(v) for v in cells.values()})}")
        for column, (name, party) in enumerate(candidates):
            summed = sum(votes[column] for votes in cells.values())
            if summed != printed[column]:
                raise ValueError(f"{year} {title} {name}: counties sum to {summed}, printed {printed[column]}")
            for place, votes in cells.items():
                county = codes[re.sub(r"[^a-z]", "", place.lower().replace("saint ", "st "))]
                rows.append((year, title, special, county, name.upper(), party, votes[column]))

    for page, (title, post) in OFFICES.items():
        lines = _lines(raw.cached(SOURCE, PAGE.format(page)).path, r"(?i)</t[dr]>|<br\s*/?>|</p>")
        head = next(i for i, line in enumerate(lines) if line == "Vote by County")
        parties = [i for i, line in enumerate(lines[:head]) if re.fullmatch(r"\((Dem|Rep|Lib)\)", line)]
        names = lines[parties[0] - len(parties) : parties[0]]
        printed = [int(re.sub(r"\D", "", line)) for line in lines[parties[-1] + 1 : parties[-1] + 1 + len(parties)]]
        cells: dict[str, list[int]] = {}
        county = None
        for line in lines[head + 1 :]:
            if re.fullmatch(r"[\d,]+", line):
                cells[county].append(int(line.replace(",", "")))
            else:
                county = line
                cells[county] = []
        take(1990, title if post is None else f"{title} {post}", False, cells, [(n, PARTIES[lines[i][1:-1]]) for n, i in zip(names, parties, strict=True)], printed)

    for page, (title, special, candidates) in OFFICES98.items():
        lines = _lines(raw.cached(SOURCE, PAGE98.format(page)).path, r"(?i)</t[dr]>|<br\s*/?>|</p>|</?pre>")
        head = next(i for i, line in enumerate(lines) if line.startswith("County PR TP"))
        party_line = re.findall(r"\((\w+)\)", lines[head - 3]) if lines[head - 3].startswith("(") else []
        printed = [int(v.replace(",", "")) for v in lines[head - 2].split()]
        cells = {}
        for line in lines[head + 1 :]:
            found = re.fullmatch(r"([A-Z][A-Z .'-]*?) (\d+) (\d+) ([\d, ]+)", line)
            if found:
                cells[found[1]] = [int(v.replace(",", "")) for v in found[4].split()]
        take(1998, title, special, cells, [(name, PARTIES[party_line[i]] if party_line else None) for i, name in enumerate(candidates)], printed)
    frame = pl.DataFrame(
        rows,
        schema={"year": pl.Int32, "title": pl.String, "special": pl.Boolean, "county": pl.String, "candidate": pl.String, "party": pl.String, "votes": pl.Int64},
        orient="row",
    ).select(
        year="year",
        day=pl.when(pl.col("year") == 1990).then(pl.lit(DAY)).otherwise(pl.lit(DAY98)),
        state=pl.lit("GA"),
        title=pl.col("title").str.replace(r" [12]$", ""),
        post=pl.col("title").str.extract(r" ([12])$"),
        special="special",
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code=pl.lit("county"),
        county="county",
        candidate="candidate",
        party="party",
        writein=pl.lit(False),
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(159),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("year", "title", "post", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("year", "title", "post").rows())


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
                url="https://sos.ga.gov/elections",
                license="Public record",
            )
        for page in OFFICES:
            cs.artifact(raw.cached(SOURCE, PAGE.format(page)), source)
        for page in OFFICES98:
            cs.artifact(raw.cached(SOURCE, PAGE98.format(page)), source)
        return {"GA": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Georgia's 1990 and 1998 lieutenant governor, secretary of state, attorney general, commissioners of agriculture, "
        "insurance, and labor, school superintendent, and two public service commissioner seats by county from the "
        "Secretary of State's results pages",
        cite=cite,
        method=METHOD,
    )
