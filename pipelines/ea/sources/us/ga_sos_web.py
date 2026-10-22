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
DAY = date(1990, 11, 6)
PUBLISHER = "Georgia Secretary of State"
TITLE = "1990 General Election Results"
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
PARTIES = {"Dem": "DEMOCRAT", "Rep": "REPUBLICAN", "Lib": "LIBERTARIAN"}
METHOD = (
    "County totals come from the Georgia Secretary of State's 1990 General Election Results pages, archived from "
    "sos.state.ga.us. Every candidate's county votes sum to the statewide total the page prints."
)


def fetch() -> None:
    for page in OFFICES:
        raw.fetch(SOURCE, PAGE.format(page), suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["GA"]
    rows = []
    for page, (title, post) in OFFICES.items():
        text = raw.cached(SOURCE, PAGE.format(page)).path.read_text(errors="ignore")
        text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<!--.*?-->", "", text)
        text = re.sub(r"(?i)</t[dr]>|<br\s*/?>|</p>", "\n", text)
        lines = [re.sub(r"\s+", " ", line).strip() for line in html.unescape(re.sub(r"<[^>]+>", " ", text)).split("\n")]
        lines = [line for line in lines if line]
        head = next(i for i, line in enumerate(lines) if line == "Vote by County")
        parties = [i for i, line in enumerate(lines[:head]) if re.fullmatch(r"\((Dem|Rep|Lib)\)", line)]
        names = lines[parties[0] - len(parties) : parties[0]]
        printed = [int(re.sub(r"\D", "", line)) for line in lines[parties[-1] + 1 : parties[-1] + 1 + len(parties)]]
        body = lines[head + 1 :]
        county = None
        cells: dict[str, list[int]] = {}
        for line in body:
            if re.fullmatch(r"[\d,]+", line):
                if county is None:
                    raise ValueError(f"{page}: a vote with no county")
                cells[county].append(int(line.replace(",", "")))
            else:
                county = line
                cells[county] = []
        if len(cells) != 159 or any(len(votes) != len(names) for votes in cells.values()):
            raise ValueError(f"{page}: {len(cells)} counties, row lengths {sorted({len(v) for v in cells.values()})}")
        for column, (name, party) in enumerate(zip(names, [lines[i][1:-1] for i in parties], strict=True)):
            if sum(votes[column] for votes in cells.values()) != printed[column]:
                raise ValueError(f"{page} {name}: counties sum to {sum(v[column] for v in cells.values())}, printed {printed[column]}")
            for place, votes in cells.items():
                rows.append(
                    (
                        title,
                        post,
                        codes[re.sub(r"[^a-z]", "", place.lower().replace("saint ", "st "))],
                        name.upper(),
                        PARTIES[party],
                        votes[column],
                    )
                )
    frame = pl.DataFrame(rows, schema={"title": pl.String, "post": pl.String, "county": pl.String, "candidate": pl.String, "party": pl.String, "votes": pl.Int64}, orient="row").select(
        year=pl.lit(1990),
        day=pl.lit(DAY),
        state=pl.lit("GA"),
        title="title",
        post="post",
        special=pl.lit(False),
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
    print(frame.group_by("title", "post", "candidate").agg(pl.col("votes").sum(), pl.len()).sort("title", "post").rows())


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
        return {"GA": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Georgia's 1990 lieutenant governor, secretary of state, attorney general, commissioners of agriculture, "
        "insurance, and labor, school superintendent, and two public service commissioner seats by county from the "
        "Secretary of State's results pages",
        cite=cite,
        method=METHOD,
    )
