from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.fec_dates"
VERSION = "1"
PUBLISHER = "Federal Election Commission"
FILES = {
    2006: "https://www.fec.gov/resources/cms-content/documents/2006pdates.pdf",
    2012: "https://www.fec.gov/resources/cms-content/documents/2012pdates.pdf",
}
TITLES = {
    2006: "2006 Congressional Primary Election Dates and Candidate Filing Deadlines for Ballot Access",
    2012: "2012 Presidential Primary Dates and Candidate Filing Deadlines for Ballot Access",
}
RENAMES = {("VA", date(2006, 9, 9)): date(2006, 6, 13), ("VI", date(2006, 6, 13)): date(2006, 9, 9)}
MERGES = {("TX", date(2012, 4, 3)): date(2012, 5, 29), ("OH", date(2012, 6, 12)): date(2012, 3, 6)}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Correct the dates of four primaries that the FEC's results workbooks print wrongly: Virginia and the Virgin Islands in 2006, and the Texas and Ohio presidential primaries in 2012",
            polity="US",
            kind="correction",
        )
        for year, url in FILES.items():
            source = cs.source(PUBLISHER, TITLES[year])
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLES[year],
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), source)
        for (state, old), new in {**RENAMES, **MERGES}.items():
            election, polity, kind, parent, status = conn.execute(
                """select e.id, e.polity_id, e.kind, e.parent_id, e.status from election e
                   join external_id x on x.entity_id = e.id
                   where x.scheme = 'us.election' and x.value = %s""",
                [f"{old}:{state}:primary"],
            ).fetchone()
            contests = conn.execute(
                """select c.id, x.value, (select path from slug where entity_id = c.id and is_current)
                   from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where c.election_id = %s and x.value like %s""",
                [election, f"{old}:%"],
            ).fetchall()
            (path,) = conn.execute("select path from slug where entity_id = %s and is_current", [election]).fetchone()
            if (state, old) in RENAMES:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=new,
                    date_to=new,
                    status=status,
                )
                cs.add("slug", path=path.replace(str(old), str(new)), entity_id=election)
                cs.add("external_id", entity_id=election, scheme="us.election", value=f"{new}:{state}:primary")
            else:
                (survivor,) = conn.execute(
                    "select entity_id from external_id where scheme = 'us.election' and value = %s",
                    [f"{new}:{state}:primary"],
                ).fetchone()
                cs.merge(election, survivor)
            for contest, value, slug_path in contests:
                cs.add("slug", path=slug_path.replace(str(old), str(new)), entity_id=contest)
                cs.add(
                    "external_id", entity_id=contest, scheme="us.contest", value=value.replace(str(old), str(new), 1)
                )
        return cs.write()
