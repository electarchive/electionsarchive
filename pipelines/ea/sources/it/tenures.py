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
VERSION = "2"
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
PDS = UUID("ffd85eec-93a6-55a9-a605-c85b0298d57e")
DS = UUID("89dbd20e-b8d8-5f27-a683-b9925885f09d")
FI = UUID("36a01b67-0c2f-5d0a-aa7c-f02dcf712f61")
LEGA = UUID("3955e9a5-c36d-5457-bb55-ed5ffc41eed8")
CCD = UUID("de4bce47-c006-5775-bd58-f63c6ea0bfe7")
VERDI = UUID("2568942f-9acf-54d4-98c4-740515ba4846")
MARGHERITA = UUID("530d39c9-2732-50dd-b806-fcbf34e9b54f")
FORZA_ITALIA = UUID("01a13bf7-300c-7784-acd9-f60dd88e38dd")
SALVINI = UUID("01a13bf7-300c-7784-acd9-f60cef597cca")
GROUPS = {
    "Progressisti - Federativo": PDS,
    "Sinistra Democratica": PDS,
    "Sinistra Democratica - l'Ulivo": PDS,
    "Democratici di Sinistra - l'Ulivo": DS,
    "Lega Nord": LEGA,
    "Lega": LEGA,
    "Lega Nord Padania": LEGA,
    "Lega Nord - Per la Padania indipendente": LEGA,
    "Lega Forza Padania per l'indipendenza del Nord": LEGA,
    "Alleanza Nazionale - Movimento Sociale Italiano": UUID("e598bbed-f242-5883-a4d1-be5965f4a898"),
    "Alleanza Nazionale": UUID("e598bbed-f242-5883-a4d1-be5965f4a898"),
    "Forza Italia": FI,
    "Partito Popolare Italiano": UUID("a3fcca6f-22a3-51cd-9d80-055c6c82d1d2"),
    "Rifondazione Comunista - Progressisti": UUID("33660a26-d195-50a2-a505-f90a9daecb58"),
    "Centro Cristiano Democratico": CCD,
    "Federazione Cristiano Democratica - CCD": CCD,
    "Federazione Cristiano Democratica - CDU": UUID("96ec53f2-5fba-5a15-a547-add8c48e501a"),
    "CCD-CDU: Biancofiore": UUID("f1b8862f-f8be-56b0-bc45-e11c3cfba57f"),
    "Progressista - PSI": UUID("faedbc2a-60ca-5d04-ad04-365924e04536"),
    "Verdi - l'Ulivo": VERDI,
    "Rinnovamento Italiano": UUID("c5d89182-fc3e-525c-b4d3-c40b2ed2ffff"),
    "Margherita": MARGHERITA,
    "Margherita - DL - L'Ulivo": MARGHERITA,
    "MoVimento 5 Stelle": UUID("01a13bf7-300c-7784-acd9-f60b9930672c"),
    "Forza Italia-Berlusconi Presidente": FORZA_ITALIA,
    "Forza Italia Berlusconi Presidente-UDC": FORZA_ITALIA,
    "Forza Italia - Berlusconi Presidente - PPE": FORZA_ITALIA,
    "Lega-Salvini Premier": SALVINI,
    "Lega-Salvini Premier-Partito Sardo d'Azione": SALVINI,
    "Lega Salvini Premier - Partito Sardo d'Azione": SALVINI,
    "Partito Democratico": UUID("e2dd275d-8eff-5620-8364-588dd9a80a10"),
    "Partito Democratico - Italia Democratica e Progressista": UUID("e2dd275d-8eff-5620-8364-588dd9a80a10"),
    "Fratelli d'Italia": UUID("01a13bf7-300c-7784-acd9-f60922c39888"),
    "Civici d'Italia - Noi Moderati (UDC - Coraggio Italia - Noi con l'Italia - Italia al Centro) - MAIE": UUID(
        "01a13bf7-300d-73bb-b6c0-8d0a3f26ac93"
    ),
}


def fetch() -> None:
    for legislature in range(1, len(senato.DAYS) + 1):
        raw.fetch(senato.SOURCE, senato.mandates(legislature), suffix=".csv")
        raw.fetch(senato.SOURCE, senato.adhesions(legislature), suffix=".csv")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Elected Italian senators of every legislature since 1948 (dati.senato.it), as Senate tenures linked to their contests, with the party of their Senate group where the ballot names none",
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
        for day, person, top, place, ballot, elected, linked, partied in conn.execute(
            """select e.date_from, cy.person_id, coalesce(c.allocation_parent_id, c.id),
                      (select text from name where entity_id = coalesce(c.allocation_parent_id, c.id) and is_preferred limit 1),
                      cy.ballot_name,
                      exists (select from outcome x join result_set rs on rs.id = x.result_set_id and rs.is_default
                              where x.option_id = o.id and x.kind in ('elected', 'won')),
                      exists (select from external_id s where s.entity_id = cy.person_id and s.scheme = 'it.senato.senatore'),
                      coalesce(o.party_id, po.party_id, cy.member_of_party_id) is not null
               from candidacy cy join contest_option o on o.id = cy.option_id
               left join contest_option po on po.id = o.parent_option_id
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
                    "partied": partied,
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
            adhesions = raw.cached(senato.SOURCE, senato.adhesions(legislature))
            cs.artifact(adhesions, record)
            groups: dict[str, list[tuple[date, str]]] = defaultdict(list)
            for uri, since, title in (
                pl.read_csv(adhesions.path, infer_schema=False)
                .unique(["s", "inizio"])
                .sort("inizio")
                .select("s", "inizio", "titolo")
                .iter_rows()
            ):
                groups[uri.rsplit("/", 1)[1]].append((date.fromisoformat(since[:10]), title))
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
                tenure = cs.key(f"{SOURCE}.tenure", f"S:{legislature}:{senator}:{start}")
                cs.add(
                    "tenure",
                    id=tenure,
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
                if contest is None or not any(entry["partied"] for entry in pool if entry["top"] == contest):
                    stop = date.fromisoformat(end) if end else None
                    spells = groups.get(senator, [])
                    staged: list[list[object]] = []
                    for index, (since, title) in enumerate(spells):
                        upto = spells[index + 1][0] if index + 1 < len(spells) else None
                        since, upto = (
                            max(since, start),
                            upto if stop is None or upto is not None and upto < stop else stop,
                        )
                        party = GROUPS.get(title)
                        if party is None or upto is not None and upto <= since:
                            continue
                        if staged and staged[-1][0] == party and staged[-1][2] == since:
                            staged[-1][2] = upto
                        else:
                            staged.append([party, since, upto])
                    for party, since, upto in staged:
                        cs.add(
                            "tenure_affiliation",
                            tenure_id=tenure,
                            kind="party",
                            party_id=party,
                            valid_from=since,
                            valid_from_precision="day",
                            valid_to=upto,
                            valid_to_precision="day",
                        )
                    counts["with group party" if staged else "without party"] += 1
                counts["with contest" if contest is not None else "without contest"] += 1
        print(dict(counts), file=sys.stderr)
        return cs.write()
