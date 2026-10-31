import csv
from collections import defaultdict
from pathlib import Path

from ea.changeset import Changeset
from ea.sources.tr import tbmm
from ea.store import DATA, connect

SOURCE = "tr.tbmm_identity"
VERSION = "1"
SAME = {
    frozenset({"Q106239096", "Q19611225"}): (
        "Wikidata holds this member twice, as Q106239096 and Q19611225: both are members of the Grand National Assembly "
        "born in 1961 in İzmir, and the TBMM lists her under one person ID in terms 22, 23, 25, and 26."
    )
}
TERMS = (9, 10, 11, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="One person for each Grand National Assembly member who served in several terms, by the TBMM's person ID",
            polity="TR",
        )
        owner = {
            key: person
            for key, person in conn.execute(
                "select x.value, t.person_id from external_id x join tenure t on t.id = x.entity_id where x.scheme = 'tr.tbmm.tenure'"
            )
        }
        created = {p for (p,) in conn.execute("select entity_id from external_id where scheme = 'tr.tbmm.person'")}
        wikidata = defaultdict(set)
        for person, value in conn.execute("select entity_id, value from external_id where scheme = 'wikidata'"):
            wikidata[person].add(value)
        served = defaultdict(list)
        for term in TERMS:
            for name, _, member in tbmm.members(term):
                served[member].append(
                    (term, name, cs.survivors.get(owner[f"{term}:{member}"], owner[f"{term}:{member}"]))
                )
        review = []
        merged = 0
        for member, terms in served.items():
            people = sorted({person for _, _, person in terms}, key=str)
            if len(people) < 2:
                continue
            items = frozenset(value for person in people for value in wikidata[person])
            if len(items) > 1 and items not in SAME:
                review.append((member, terms[0][1], "persons carry different Wikidata items"))
                continue
            survivor = min(
                people,
                key=lambda person: (
                    not wikidata[person],
                    person in created,
                    min(term for term, _, owner_ in terms if owner_ == person),
                    str(person),
                ),
            )
            if items in SAME:
                cs.add("note", entity_id=survivor, kind="context", body_md=SAME[items])
            for person in people:
                if person != survivor:
                    cs.merge(person, survivor)
                    merged += 1
        names = defaultdict(set)
        for member, terms in served.items():
            for term, name, _ in terms:
                names[tbmm.fold(name)].add(member)
        for text, members in names.items():
            if len(members) > 1:
                review.append((",".join(sorted(members)), text, "same name under different TBMM person IDs"))
        folder = DATA / "parsed" / SOURCE
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "review.csv").open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["tbmm_ids", "name", "reason"])
            writer.writerows(review)
        print(f"{merged} merges, {len(review)} on the review list ({folder / 'review.csv'})")
        return cs.write()
