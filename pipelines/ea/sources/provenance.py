import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.sources.tr.lineage import COURT, COURT_PUBLISHER, COURT_TITLE
from ea.sources.us.fec import LRL, LRL_TITLE
from ea.sources.us.legislators import PUBLISHER as LEGISLATORS_PUBLISHER
from ea.sources.us.legislators import TITLE as LEGISLATORS_TITLE
from ea.store import connect

SOURCE = "provenance"
VERSION = "1"
FETCHED = [
    ("tr.lineage", COURT, "application/pdf", COURT_PUBLISHER, COURT_TITLE),
    ("us.fec", LRL, "text/html", "Minnesota Legislative Reference Library", LRL_TITLE),
]
TENURES = {
    "us.legislators.tenure": (LEGISLATORS_PUBLISHER, LEGISLATORS_TITLE, "term"),
    "gb.parliament.tenure": ("Wikidata", "Members of the 20th Parliament of the United Kingdom", "item"),
    "eu.europarl.tenure": (
        "European Parliament",
        "Open Data Portal: Members of the European Parliament and their memberships",
        "mep",
    ),
    "it.interno.tenure": ("Camera dei deputati", "Mandati dei deputati della XIX legislatura", "deputy"),
}


def fetch() -> None:
    for source, url, media_type, _, _ in FETCHED:
        try:
            raw.cached(source, url)
        except LookupError:
            content = subprocess.run(["curl", "-sSfL", url], capture_output=True, check=True).stdout
            raw.store(
                source,
                url,
                content,
                suffix=".pdf" if media_type == "application/pdf" else ".html",
                retrieved_at=datetime.now(UTC),
                media_type=media_type,
            )


def parse() -> None:
    pass


def emit() -> Path | None:
    """Record the files behind sources cited without one, and cite every uncited tenure: from the roster its pipeline
    read, or else from the source of its contest's default result set, which declared the holder elected."""
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cite tenures published without a citation, and record the files behind two sources cited "
            "without one",
            polity=None,
            kind="correction",
        )
        for source, url, _, publisher, title in FETCHED:
            cited = cs.source(publisher, title)
            if cited in cs.minted:
                raise ValueError(f"no source {publisher}: {title}")
            cs.artifact(raw.cached(source, url), cited)
        rows = conn.execute(
            """select distinct t.id, x.scheme, x.value, r.source_id from tenure t
               join external_id x on x.entity_id = t.id and x.scheme like '%%.tenure'
               left join result_set r on r.contest_id = t.contest_id and r.is_default
               where not exists (select from citation c where c.entity_id = t.id)"""
        ).fetchall()
        cited = 0
        for tenure, scheme, value, declared in rows:
            if scheme in TENURES:
                publisher, title, field = TENURES[scheme]
                source = cs.source(publisher, title)
                if source in cs.minted:
                    raise ValueError(f"no source {publisher}: {title}")
                locator = {field: value if field == "term" else value.rpartition(":")[2]}
            elif declared is not None:
                source, locator = declared, None
            else:
                continue
            cs.add("citation", entity_id=tenure, source_id=source, locator=locator)
            cited += 1
        print(f"{cited} of {len(rows)} uncited tenures cited")
        return cs.write()
