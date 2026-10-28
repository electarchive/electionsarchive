from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.contest_merges"
VERSION = "5"
ELECTIONS = {
    "2016-11:ID:primary": "2016-05-17:ID:primary",
    "2005-11-08:CO": "2005-11-01:CO",
    "2011-11-08:CO": "2011-11-01:CO",
    "2007:KY": "2007-11-06:KY",
    "2011:KY": "2011-11-08:KY",
    "2015:KY": "2015-11-03:KY",
    "2019:KY": "2019-11-05:KY",
}
OPTIONS = {
    "2016-11:ID:senate:5:rp:363818": "2016-05-17:ID:senate:5:rp:1984104a-862f-5ae9-bcfa-17f733bb61f6",
    "2011:KY:governor:7158c9bf-ac7a-593a-b1a9-879fc53071a3": "2011-11-08:KY:governor:34a7e511-aa3f-569e-814e-c6b6a1154fcd",
}
PEOPLE = {UUID("7158c9bf-ac7a-593a-b1a9-879fc53071a3"): UUID("34a7e511-aa3f-569e-814e-c6b6a1154fcd")}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Merge contests recorded twice under a misdated election into the contests of the real one: Klarner's November 2016 Idaho primary into the May 17 primary, and us.measures' Colorado 2005 and 2011 coordinated elections, dated November 8 instead of November 1, and the 2021-03-29 Illinois elections that us.enr_clarity first dated a week early, into the April 6 ones, and Algara and Amlani's Kentucky governor races of 2007, 2011, 2015, and 2019, filed under elections dated only by year, into the November elections, with Algara's David Lynn Williams joined to David Lewis Williams, the 2011 Republican nominee",
            polity="US",
            kind="correction",
        )

        def keyed(scheme: str, value: str) -> UUID:
            row = conn.execute(
                "select entity_id from external_id where scheme = %s and value = %s", [scheme, value]
            ).fetchone()
            assert row is not None, value
            return row[0]

        illinois = {
            value: value.replace("2021-03-29", "2021-04-06", 1)
            for (value,) in conn.execute(
                """select x.value from external_id x where x.scheme = 'us.election' and x.value like '2021-03-29:IL%'
                   and exists (select 1 from external_id y where y.scheme = 'us.election'
                               and y.value = replace(x.value, '2021-03-29', '2021-04-06'))"""
            ).fetchall()
        }
        for old, new in {**ELECTIONS, **illinois}.items():
            duplicate, survivor = keyed("us.election", old), keyed("us.election", new)
            before, after = old.split(":")[0], new.split(":")[0]
            for contest, value in conn.execute(
                """select c.id, x.value from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where c.election_id = %s and x.value like %s""",
                [duplicate, f"{before}:%"],
            ).fetchall():
                row = conn.execute(
                    "select entity_id from external_id where scheme = 'us.contest' and value = %s",
                    [value.replace(before, after, 1)],
                ).fetchone()
                if row is not None and row[0] != contest:
                    cs.merge(contest, row[0])
            cs.merge(duplicate, survivor)
        for old, new in OPTIONS.items():
            cs.merge(keyed("us.option", old), keyed("us.option", new))
        for old, new in PEOPLE.items():
            cs.merge(old, new)
        return cs.write()
