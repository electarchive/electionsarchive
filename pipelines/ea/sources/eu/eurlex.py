import html
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "eu.eurlex"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CELLAR = "https://publications.europa.eu/resource/cellar"
DECISIONS = {
    "2018/937": (f"{CELLAR}/6104c405-7dcd-11e8-ac6a-01aa75ed71a1.0006.03/DOC_1", 705),
    "2023/2061": (f"{CELLAR}/cb24351b-5d18-11ee-9220-01aa75ed71a1.0006.03/DOC_1", 720),
}
WITHDRAWAL = date(2020, 2, 1)
CONSTITUTED = date(2024, 7, 16)
VOTING = (date(2024, 6, 6), date(2024, 6, 9))
BODY_NOTE = (
    "The European Council sets each member state's number of seats before every term. The Parliament had 705 seats "
    "from the United Kingdom's withdrawal on 1 February 2020 (European Council Decision (EU) 2018/937). For the "
    "2024–2029 term it has 720 seats, as set by European Council Decision (EU) 2023/2061."
)
ELECTION_NOTE = (
    "Each member state held its own election between 6 and 9 June 2024, the period set by Council Decision (EU, "
    "Euratom) 2023/1584, under its own electoral law. The 2024 European Parliament election groups these national "
    "elections; it has no contests of its own. European Council Decision (EU) 2023/2061 divided the 720 seats among "
    "the member states in advance."
)


def fetch() -> None:
    for url, _ in DECISIONS.values():
        raw.fetch(SOURCE, url, suffix=".xhtml")


def parse() -> None:
    rows: list[tuple[str, str, int]] = []
    for decision, (url, total) in DECISIONS.items():
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, url).path.read_text())))
        if f"EUROPEAN COUNCIL DECISION (EU) {decision}" not in text:
            raise ValueError(f"{url} is not European Council Decision (EU) {decision}")
        article = re.search(r"Article 3 (.*?) Article 4", text)
        if article is None:
            raise ValueError(f"no Article 3 in Decision {decision}")
        table = article[1].partition("as follows")[2].partition(" 2. ")[0]
        seats = [(state, int(count)) for state, count in re.findall(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) (\d+)", table)]
        if len(seats) != 27 or sum(count for _, count in seats) != total:
            raise ValueError(f"Decision {decision} does not apportion {total} seats among 27 member states: {seats}")
        rows.extend((decision, state, count) for state, count in seats)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["decision", "member_state", "seats"], orient="row").write_parquet(
        PARSED / "seats.parquet"
    )


def emit() -> Path:
    seats = pl.read_parquet(PARSED / "seats.parquet")
    totals = dict(seats.group_by("decision").agg(pl.col("seats").sum()).iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="European Union, the European Parliament's seats, and the 2024 European Parliament election",
            polity="EU",
        )
        title = "European Council decisions establishing the composition of the European Parliament"
        source = cs.source("Publications Office of the European Union", title)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Publications Office of the European Union",
            title=title,
            url="https://eur-lex.europa.eu/eli/dec/2023/2061/oj",
            license="Commission Decision 2011/833/EU on the reuse of Commission documents",
            license_url="https://eur-lex.europa.eu/eli/dec/2011/833/oj",
            attribution="© European Union, https://eur-lex.europa.eu",
        )
        for url, _ in DECISIONS.values():
            cs.artifact(raw.cached(SOURCE, url), source)

        union = cs.polity("EU")
        cs.add("polity", id=union, kind="supranational", iso_code="EU")
        cs.add("name", entity_id=union, text="European Union", lang="en", kind="official", is_preferred=True)
        cs.add("slug", path="eu", entity_id=union)
        cs.add("external_id", entity_id=union, scheme="wikidata", value="Q458")
        parliament = cs.key("eu.body", "european-parliament")
        cs.add("body", id=parliament, polity_id=union, kind="unicameral")
        cs.add("name", entity_id=parliament, text="European Parliament", lang="en", kind="official", is_preferred=True)
        cs.add("external_id", entity_id=parliament, scheme="wikidata", value="Q8889")
        cs.add(
            "body_size",
            body_id=parliament,
            seats=totals["2018/937"],
            valid_from=WITHDRAWAL,
            valid_to=CONSTITUTED,
        )
        cs.add("body_size", body_id=parliament, seats=totals["2023/2061"], valid_from=CONSTITUTED)
        cs.add("note", entity_id=parliament, kind="context", body_md=BODY_NOTE)
        election = cs.key("eu.election", f"{VOTING[0]}:european-parliament")
        cs.add(
            "election",
            id=election,
            polity_id=union,
            kind="general",
            date_from=VOTING[0],
            date_to=VOTING[1],
            status="held",
        )
        cs.add(
            "name",
            entity_id=election,
            text="2024 European Parliament election",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add("slug", path=f"eu/{VOTING[0]}-european-parliament", entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=ELECTION_NOTE)
        return cs.write()
