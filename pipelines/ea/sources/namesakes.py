from pathlib import Path

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "namesakes"
VERSION = "1"
MOVES = {
    ("us/ca/2010-06-08-primary/governor-democratic", 'Edmund G. "Jerry" Brown'): ("wikidata", "Q152451"),
    ("us/ca/2014-06-03-primary/governor", 'Edmund G. "Jerry" Brown'): ("wikidata", "Q152451"),
}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Candidacies a source joined to a namesake moved to the person who ran, where the source's own "
                "pipeline matched them to that namesake's general-election record before it was split"
            ),
            polity=None,
            kind="correction",
        )
        for (path, ballot), (scheme, value) in MOVES.items():
            rows = conn.execute(
                """select cy.id, cy.option_id, cy.role, cy.ballot_name, cy.is_independent, cy.is_incumbent,
                          coalesce(m.into_id, x.entity_id)
                   from slug s join contest_option o on o.contest_id = s.entity_id
                   join candidacy cy on cy.option_id = o.id and cy.ballot_name = %s
                   join external_id x on x.scheme = %s and x.value = %s
                   left join entity_merge m on m.entity_id = x.entity_id
                   where s.path = %s and s.is_current""",
                [ballot, scheme, value, path],
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(f"{path}: {len(rows)} candidacies for {ballot}")
            candidacy, option, role, name, independent, incumbent, person = rows[0]
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role=role,
                ballot_name=name,
                is_independent=independent,
                is_incumbent=incumbent,
            )
        return cs.write()
