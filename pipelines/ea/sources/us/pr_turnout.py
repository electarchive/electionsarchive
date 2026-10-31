from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.pr_turnout"
VERSION = "1"
PUBLISHER = "Puerto Rico State Elections Commission"
URL = "https://ww2.ceepur.org/sites/ComisionEE/es-pr/Documents/Informe%20Estadistico%20EG2016%20-%20Parte2.pdf"
TITLE = "Informe Estadístico, Elecciones Generales 2016, parte 2, p. 198"
BALLOTS = {
    1972: 1308950,
    1976: 1464600,
    1980: 1619790,
    1984: 1741638,
    1988: 1812357,
    1992: 1901905,
    1996: 1971578,
    2000: 2014918,
    2004: 1993572,
    2008: 1941704,
    2012: 1879605,
    2016: 1590389,
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico's ballots cast on the state ballot at its 1972–2016 general elections, digitized from "
            "the State Elections Commission's statistical report on the 2016 general election (page 198, table read twice)",
            polity="US",
        )
        elections = {
            start.year: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':PR(:[a-z]+)?$' and e.kind = 'general'"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=URL,
                license="Public record",
            )
        cs.artifact(raw.cached(SOURCE, URL), source)
        for year, ballots in BALLOTS.items():
            if year not in elections:
                continue
            value_key, election_id = elections[year]
            result_set = cs.key(f"{SOURCE}.result_set", value_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=1,
            )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=cs.unit("us.state", "72"),
                option_id=None,
                channel=0,
                measure=3,
                count_round=0,
                flags=0,
                value=ballots,
            )
        return cs.write()
