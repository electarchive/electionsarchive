import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.it import senato
from ea.sources.it.senato import fold
from ea.store import connect

SOURCE = "it.tenures"
VERSION = "1"
SENATE = UUID("f024c5c6-4cf2-5173-b216-423d63f946b4")
ENDED = {
    "Deceduto": "died",
    "Dimissioni accettate": "resigned",
    "Dimissioni per incompatibilità": "resigned",
    "Annullata elezione": "annulled",
    "Mancata convalida dell'elezione": "annulled",
    "Nominato senatore a vita": "elevated",
    "Eletto membro del Consiglio Superiore della Magistratura": "appointed",
    "Eletto Presidente della Repubblica": "appointed",
    "Eletto membro Corte costituzionale": "appointed",
}


def fetch() -> None:
    for legislature in range(1, len(senato.DAYS) + 1):
        raw.fetch(senato.SOURCE, senato.mandates(legislature), suffix=".csv")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Elected Italian senators of every legislature since 1948 (dati.senato.it), as Senate tenures linked to their contests",
            polity="IT",
        )
        record = cs.source("Senato della Repubblica", "dati.senato.it: mandati dei senatori")
        if record in cs.minted:
            cs.add(
                "source",
                id=record,
                kind="official_body",
                publisher="Senato della Repubblica",
                title="dati.senato.it: mandati dei senatori",
                url="https://dati.senato.it/",
                license="CC BY 3.0",
            )
        known: dict[str, UUID] = {
            value: entity
            for value, entity in conn.execute(
                """select x.value, coalesce(m.into_id, x.entity_id) from external_id x
                   left join entity_merge m on m.entity_id = x.entity_id where x.scheme = 'it.senato.senatore'"""
            ).fetchall()
        }
        runs: dict[date, list[dict[str, object]]] = defaultdict(list)
        for day, person, top, place, ballot, elected, linked in conn.execute(
            """select e.date_from, cy.person_id, coalesce(c.allocation_parent_id, c.id),
                      (select text from name where entity_id = coalesce(c.allocation_parent_id, c.id) and is_preferred limit 1),
                      cy.ballot_name,
                      exists (select from outcome x join result_set rs on rs.id = x.result_set_id and rs.is_default
                              where x.option_id = o.id and x.kind in ('elected', 'won')),
                      exists (select from external_id s where s.entity_id = cy.person_id and s.scheme = 'it.senato.senatore')
               from candidacy cy join contest_option o on o.id = cy.option_id
               join contest c on c.id = o.contest_id join election e on e.id = c.election_id
               where c.body_id = %s and cy.person_id is not null
               and not exists (select from entity_merge m where m.entity_id = cy.person_id)""",
            [SENATE],
        ).fetchall():
            runs[day].append(
                {
                    "person": person,
                    "top": top,
                    "place": fold(place or ""),
                    "ballot": fold(ballot or ""),
                    "elected": elected,
                    "linked": linked,
                }
            )
        counts: dict[str, int] = defaultdict(int)
        claimed: set[UUID] = set()
        for legislature, day in enumerate(senato.DAYS, 1):
            artifact = raw.cached(senato.SOURCE, senato.mandates(legislature))
            cs.artifact(artifact, record)
            frame = pl.read_csv(artifact.path, infer_schema=False).filter(
                ~pl.col("tipo").fill_null("").str.starts_with("III")
            )
            opening = date.fromisoformat(frame["inizio"].min())
            entries = runs.get(day, [])
            by_person: dict[UUID, list[dict[str, object]]] = defaultdict(list)
            for entry in entries:
                by_person[entry["person"]].append(entry)
            for row in frame.unique(["s", "inizio"]).sort("s", "inizio").iter_rows(named=True):
                senator = row["s"].rsplit("/", 1)[1]
                surname, forename = row["cognome"] or "", row["nome"] or ""
                region = fold(row["regione"] or "")
                start = date.fromisoformat(row["inizio"])
                if senator in known:
                    person = known[senator]
                else:
                    full = fold(f"{surname} {forename}")
                    short = fold(f"{surname} {' '.join(word[0] for word in fold(forename).split())}")
                    found = {
                        entry["person"]
                        for entry in entries
                        if not entry["linked"] and entry["person"] not in claimed and entry["ballot"] in (full, short)
                    }
                    if len(found) > 1:
                        found = {
                            entry["person"]
                            for entry in entries
                            if entry["person"] in found and region and region in entry["place"]
                        }
                    if len(found) == 1:
                        person = found.pop()
                        claimed.add(person)
                        cs.add("external_id", entity_id=person, scheme="it.senato.senatore", value=senator)
                        counts["matched by name"] += 1
                    else:
                        person = cs.key("it.senato.senatore", senator)
                        cs.add(
                            "person",
                            id=person,
                            gender={"M": "male", "F": "female"}.get(row["genere"] or ""),
                            birth_date=row["nascita"][:10] if row["nascita"] else None,
                            birth_precision="day" if row["nascita"] else None,
                        )
                        cs.add(
                            "name",
                            entity_id=person,
                            text=f"{forename} {surname}".strip(),
                            lang="it",
                            kind="official",
                            is_preferred=True,
                        )
                        counts["new people"] += 1
                    known[senator] = person
                mine = by_person.get(person, [])
                won = [entry for entry in mine if entry["elected"]]
                pool = won or mine
                if len({entry["top"] for entry in pool}) > 1:
                    pool = [entry for entry in pool if region and region in entry["place"]] or pool
                tops = {entry["top"] for entry in pool}
                contest = tops.pop() if len(tops) == 1 else None
                kind = (row["tipo"] or "").lower()
                elected = bool(won) or start - opening <= timedelta(days=7) or "uninominale" in kind
                end = row["fine"]
                cs.add(
                    "tenure",
                    id=cs.key(f"{SOURCE}.tenure", f"S:{legislature}:{senator}:{start}"),
                    person_id=person,
                    office_id=None,
                    body_id=SENATE,
                    from_date=start,
                    from_precision="day",
                    to_date=date.fromisoformat(end) if end else None,
                    to_precision="day",
                    acquired_via="elected" if elected else "list_replacement",
                    contest_id=contest,
                    ended_via=ENDED.get(row["fineTipo"] or "", "term_end") if end else None,
                )
                counts["elected" if elected else "list_replacement"] += 1
                counts["with contest" if contest is not None else "without contest"] += 1
        print(dict(counts), file=sys.stderr)
        return cs.write()
