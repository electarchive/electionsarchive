import json
from datetime import UTC, datetime
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.wikipedia_primaries import page
from ea.store import connect

SOURCE = "us.nominations"
VERSION = "1"
CONVENTIONS = [
    ("2004-01-13:president:democratic", "2004 Democratic National Convention", "2004-07-28", "Boston"),
    ("2004-01-19:president:republican", "2004 Republican National Convention", "2004-09-01", "New York City"),
    ("2008-01-03:president:democratic", "2008 Democratic National Convention", "2008-08-27", "Denver"),
    ("2008-01-03:president:republican", "2008 Republican National Convention", "2008-09-03", "Saint Paul"),
    ("2012-01-03:president:democratic", "2012 Democratic National Convention", "2012-09-05", "Charlotte"),
    ("2012-01-03:president:republican", "2012 Republican National Convention", "2012-08-28", "Tampa"),
    ("2016-02-01:president:democratic", "2016 Democratic National Convention", "2016-07-26", "Philadelphia"),
    ("2016-02-01:president:republican", "2016 Republican National Convention", "2016-07-19", "Cleveland"),
    ("2020-02-03:president:democratic", "2020 Democratic National Convention", "2020-08-18", "Milwaukee"),
    ("2020-02-03:president:republican", "2020 Republican National Convention", "2020-08-24", "Charlotte"),
    ("2024-01-23:president:democratic", "2024 Democratic National Convention", "2024-08-05", None),
    ("2024-01-15:president:republican", "2024 Republican National Convention", "2024-07-15", "Milwaukee"),
]
GENERALS = {
    "2004": "2004-11-02",
    "2008": "2008-11-04",
    "2012": "2012-11-06",
    "2016": "2016-11-08",
    "2020": "2020-11-03",
    "2024": "2024-11-05",
}
METHOD = (
    "The nominee as the party's national convention declared after its roll call of the states, from the English "
    "Wikipedia's article on the convention, pinned to the revision current when it was fetched. The nominee is the "
    "head of the party's ticket in that year's general election. The delegates' votes in the roll call are not "
    "recorded here, so this result set holds the declared outcome only."
)


def fetch() -> None:
    for _, title, _, _ in CONVENTIONS:
        raw.fetch(SOURCE, page(title), suffix=".json")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="The Democratic and Republican presidential nominees 2004–2024 as their national conventions declared them",
            polity="US",
        )
        for key, title, day, city in CONVENTIONS:
            party = key.rsplit(":", 1)[1]
            contest, election = conn.execute(
                """select c.id, c.election_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchone() or (None, None)
            if contest is None:
                raise ValueError(f"{key} is not held")
            person, label = conn.execute(
                """select cy.person_id, n.text from external_id x
                   join contest_option o on o.contest_id = x.entity_id
                   join external_id p on p.entity_id = o.party_id and p.scheme = 'us.party' and p.value = %s
                   join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where x.scheme = 'us.contest' and x.value = %s""",
                [f"{party}-party", f"{GENERALS[key[:4]]}:president"],
            ).fetchone() or (None, None)
            if person is None or label is None:
                raise ValueError(f"no {party} ticket in the {key[:4]} general election")
            text = json.loads(raw.cached(SOURCE, page(title)).path.read_text())["parse"]["wikitext"]
            infobox = text[: text.find("\n'''")]
            if label.split()[-1].removesuffix(".") not in infobox and label.split()[-2] not in infobox:
                raise ValueError(f"{title} does not name {label} in its infobox")
            source = cs.source("Wikipedia", title)
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="Wikipedia",
                title=title,
                url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                license="CC BY-SA 4.0",
            )
            cs.artifact(raw.cached(SOURCE, page(title)), source)
            option = cs.key("us.option", f"{key}:nominee")
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=cs.key("us.party", f"{party}-party"),
                ballot_label=label,
            )
            cs.add(
                "candidacy",
                id=cs.key("us.candidacy", f"{key}:nominee:head"),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                is_independent=False,
            )
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=0,
                as_of=datetime.fromisoformat(day).replace(tzinfo=UTC),
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD
                + (
                    f" The convention met in {city} and nominated {label} on {datetime.fromisoformat(day):%B %-d, %Y}."
                    if city
                    else f" The party's delegates nominated {label} in a virtual roll call that closed on "
                    f"{datetime.fromisoformat(day):%B %-d, %Y}, before the convention met in Chicago."
                ),
            )
            cs.add("outcome", result_set_id=result_set, option_id=option, kind="won")
            if key == "2024-01-23:president:democratic":
                incumbent = conn.execute(
                    """select cy.person_id, n.text from external_id x
                       join contest_option o on o.contest_id = x.entity_id
                       join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                       join name n on n.entity_id = cy.person_id and n.is_preferred
                       where x.scheme = 'us.contest' and x.value = '2020-11-03:president'
                         and o.party_id = (select entity_id from external_id where scheme = 'us.party'
                                           and value = 'democratic-party')"""
                ).fetchone()
                if incumbent is None:
                    raise ValueError("no 2020 Democratic ticket")
                withdrawn = cs.key("us.option", f"{key}:withdrew")
                cs.add(
                    "contest_option",
                    id=withdrawn,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", f"{party}-party"),
                    ballot_label=incumbent[1],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{key}:withdrew:head"),
                    person_id=incumbent[0],
                    option_id=withdrawn,
                    role="head",
                    ballot_name=incumbent[1],
                    is_independent=False,
                )
                cs.add("outcome", result_set_id=result_set, option_id=withdrawn, kind="withdrew")
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=(
                        f"{incumbent[1]} won the primaries and caucuses but withdrew from the race on July 21, 2024, "
                        f"before the roll call, and the delegates pledged to him nominated {label}."
                    ),
                )
        return cs.write()
