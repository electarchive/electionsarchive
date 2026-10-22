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

SOURCE = "us.ga_psc_1992"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = {
    "psc": ("Public Service Commissioner", False),
    "labor": ("Commissioner of Labor", True),
}
URL = "http://web.archive.org/web/0id_/http://www.sos.state.ga.us/elections/election_results/1992/{}.htm"
DAY = date(1992, 11, 3)
PUBLISHER = "Georgia Secretary of State"
TITLE = "General Election Results"
PARTIES = {"D": "DEMOCRAT", "R": "REPUBLICAN", "Lib": "LIBERTARIAN", "L": "LIBERTARIAN"}
METHOD = (
    "County totals come from the Georgia Secretary of State's 1992 general election results pages for the Public Service "
    "Commission and the Labor Commissioner (the unexpired term), archived from sos.state.ga.us. Every candidate's county votes sum to the statewide total the page prints."
)


def fetch() -> None:
    for page in PAGES:
        raw.fetch(SOURCE, URL.format(page), suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = openelections.counties(conn)["GA"]
    rows: list[dict[str, object]] = []
    for page, (title, special) in PAGES.items():
        text = re.sub(
            r"(?s)<script.*?</script>|<style.*?</style>|<!--.*?-->",
            "",
            raw.cached(SOURCE, URL.format(page)).path.read_text(errors="ignore"),
        )
        text = re.sub(r"(?i)</t[dr]>|<br\s*/?>|</p>", "\n", text)
        lines = [re.sub(r"\s+", " ", line).strip() for line in html.unescape(re.sub(r"<[^>]+>", " ", text)).split("\n")]
        lines = [line for line in lines if line]
        head = lines.index("Vote by County")
        parties = [i for i, line in enumerate(lines[:head]) if re.fullmatch(r"\((D|R|Lib|L)\)", line)]
        names = lines[parties[0] - len(parties) : parties[0]]
        printed = [int(re.sub(r"\D", "", line)) for line in lines[parties[-1] + 1 : parties[-1] + 1 + len(parties)]]
        cells: dict[str, list[int]] = {}
        county = ""
        for line in lines[head + 1 :]:
            if re.fullmatch(r"[\d,]+", line):
                cells[county].append(int(line.replace(",", "")))
            else:
                county = line
                cells[county] = []
        if len(cells) != 159 or any(len(votes) != len(names) for votes in cells.values()):
            raise ValueError({k: v for k, v in cells.items() if len(v) != len(names)})
        for column, (name, party) in enumerate(zip(names, parties, strict=True)):
            if sum(votes[column] for votes in cells.values()) != printed[column]:
                raise ValueError(f"{name}: counties do not add to {printed[column]}")
            rows.extend(
                {
                    "year": 1992,
                    "day": DAY,
                    "state": "GA",
                    "title": title,
                    "post": None,
                    "special": special,
                    "magnitude": 1,
                    "kind": "county",
                    "code": "county",
                    "county": codes[re.sub(r"[^a-z]", "", place.lower().replace("saint ", "st "))],
                    "candidate": name.upper(),
                    "party": PARTIES[lines[party][1:-1]],
                    "writein": False,
                    "measure": 1,
                    "votes": votes[column],
                }
                for place, votes in cells.items()
            )
    frame = pl.DataFrame(rows, schema_overrides={"magnitude": pl.Int16, "measure": pl.Int16, "post": pl.String})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")
    frame.group_by(["year", "day", "state", "title", "post", "special"], maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(159),
    ).write_parquet(PARSED / "races.parquet")
    print(frame.group_by("candidate", "party").agg(pl.col("votes").sum()).rows())


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
        for page in PAGES:
            cs.artifact(raw.cached(SOURCE, URL.format(page)), source)
        return {"GA": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Georgia's 1992 public service commissioner election by county from the Secretary of State's results page",
        cite=cite,
        method=METHOD,
    )
