import csv
from collections import defaultdict
from pathlib import Path

from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "splits"
VERSION = "1"


def emit() -> Path:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    with open(Path(__file__).with_name("splits.tsv"), newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            groups[row["person"], row["name"]].append(row["candidacy"])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Candidacies a source joined to the previous holder of the same seat, a relative or successor with "
                "another first name, moved to a person of their own"
            ),
            polity=None,
            kind="correction",
        )
        for (person, text), candidacies in groups.items():
            label = text.title() if text.isupper() else text
            new = cs.key(f"{SOURCE}.person", f"{person}:{slug(label)}")
            if new in cs.minted:
                cs.add("person", id=new)
                cs.add("name", entity_id=new, text=label, lang="en", kind="official", is_preferred=True)
            for (
                candidacy,
                option,
                role,
                ballot,
                independent,
                incumbent,
                position,
                member,
                lines,
                holder,
            ) in conn.execute(
                """select id, option_id, role, ballot_name, is_independent, is_incumbent, list_position,
                          member_of_party_id, ballot_lines, person_id
                   from candidacy where id = any(%s::uuid[])""",
                [candidacies],
            ).fetchall():
                if str(holder) not in (person, str(new)):
                    raise ValueError(f"{candidacy} is held by {holder}, not {person}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=new,
                    option_id=option,
                    role=role,
                    ballot_name=ballot,
                    is_independent=independent,
                    is_incumbent=incumbent,
                    list_position=position,
                    member_of_party_id=member,
                    ballot_lines=lines,
                )
            for scheme, value in conn.execute(
                """select distinct x.scheme, x.value from entity_merge m
                   join external_id_history y on y.entity_id = m.entity_id
                   join external_id x on x.scheme = y.scheme and x.value = y.value and x.entity_id = m.into_id
                   where m.into_id = %s::uuid and exists (select from candidacy_history h where h.person_id = m.entity_id)
                     and not exists (select from candidacy_history h
                                     where h.person_id = m.entity_id and h.id <> all(%s::uuid[]))""",
                [person, candidacies],
            ).fetchall():
                cs.add("external_id", entity_id=new, scheme=scheme, value=value)
        return cs.write()
