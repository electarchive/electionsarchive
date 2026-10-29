import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.it import senato
from ea.sources.it.interno import proper
from ea.sources.it.senato import fold
from ea.store import connect

SOURCE = "it.deputati"
VERSION = "1"
CHAMBER = UUID("01a13bf7-300d-73bb-b6c0-8d169d9f6058")
LEGISLATURES = range(1, 19)
PREFIXES = """PREFIX ocd: <http://dati.camera.it/ocd/>
PREFIX dc: <http://purl.org/dc/elements/1.1/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX bio: <http://purl.org/vocab/bio/0.1/>
"""
MANDATES = (
    PREFIXES
    + """SELECT DISTINCT ?m ?d ?persona ?nome ?cognome ?genere ?nascita ?inizio ?fine ?motivo ?proclamazione ?tipo ?collegio WHERE {
 ?m a ocd:mandatoCamera ; ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_%02d> ; ocd:rif_deputato ?d ; ocd:startDate ?inizio .
 ?persona a foaf:Person ; ocd:rif_mandatoCamera ?m .
 OPTIONAL { ?persona foaf:firstName ?nome } OPTIONAL { ?persona foaf:surname ?cognome } OPTIONAL { ?persona foaf:gender ?genere }
 OPTIONAL { ?persona bio:Birth ?b . ?b bio:date ?nascita }
 OPTIONAL { ?m ocd:endDate ?fine } OPTIONAL { ?m ocd:motivoTermine ?motivo }
 OPTIONAL { ?m ocd:tipoProclamazione ?p . ?p dc:type ?proclamazione }
 OPTIONAL { ?m ocd:rif_elezione ?e . OPTIONAL { ?e ocd:tipoElezione ?tipo } OPTIONAL { ?e dc:coverage ?collegio } }
}"""
)
ADHESIONS = (
    PREFIXES
    + """SELECT DISTINCT ?d ?g ?inizio ?fine WHERE {
 ?d a ocd:deputato ; ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_%02d> ; ocd:aderisce ?a .
 ?a ocd:rif_gruppoParlamentare ?g ; ocd:startDate ?inizio . OPTIONAL { ?a ocd:endDate ?fine }
}"""
)
ENDED = {
    "Decesso": "died",
    "Dimissioni": "resigned",
    "Dimissioni per incompatibilita'": "resigned",
    "Annullamento": "annulled",
    "Annullamento Elezione": "annulled",
    "Scioglimento della Camera": "dissolved",
    "Nomina ad altro incarico": "appointed",
    "Decadenza": "disqualified",
    "Nomina a senatore": "elevated",
    "Nominato Senatore a vita": "elevated",
    "Eletto Presidente della Repubblica": "appointed",
    "Opzione": "chose_another_seat",
    "Rifiuto di giuramento": "withdrew",
    "Sospensiva": "removed",
}
ABROAD = {
    "EUROPA": "Europe",
    "AMERICA MERIDIONALE": "South America",
    "AMERICA SETTENTRIONALE E CENTRALE": "North and Central America",
    "AFRICA ASIA OCEANIA ANTARTIDE": "Africa, Asia",
}
DC = UUID("95366a4f-8ee2-5ca6-a589-002ece4dc438")
PCI = UUID("eece4d6d-577f-584c-ac86-8aac140f08d8")
PSI = UUID("faedbc2a-60ca-5d04-ad04-365924e04536")
PSDI = UUID("a78261da-8611-5247-a61d-2cd2f972b5bb")
PLI = UUID("036339ce-7413-537d-9d6e-123405b679a0")
PRI = UUID("3fcac41a-1aa0-5f80-a01e-70b7b3c30a4c")
MSI = UUID("9eb469e8-40e5-5ecf-ac86-87c21697eebb")
PNM = UUID("93e9ac39-bf8d-5ef1-8927-3c0804b46a70")
PMP = UUID("00fd1787-f124-598e-be2f-a61e33eee131")
PDIUM = UUID("5876d34c-1bba-57b9-a051-a72462153b80")
PSIUP = UUID("0cb036ee-e21a-5e13-8ebe-d15bd72a11cd")
PR = UUID("d90e8853-b57a-5096-b3da-b3c2ad1c4812")
DP = UUID("9bec8a3d-176e-5a93-89f1-b73f5c399b9f")
PDS = UUID("ffd85eec-93a6-55a9-a605-c85b0298d57e")
DS = UUID("89dbd20e-b8d8-5f27-a683-b9925885f09d")
LEGA = UUID("3955e9a5-c36d-5457-bb55-ed5ffc41eed8")
RC = UUID("33660a26-d195-50a2-a505-f90a9daecb58")
CCD = UUID("de4bce47-c006-5775-bd58-f63c6ea0bfe7")
VERDI = UUID("2568942f-9acf-54d4-98c4-740515ba4846")
FI = UUID("36a01b67-0c2f-5d0a-aa7c-f02dcf712f61")
AN = UUID("e598bbed-f242-5883-a4d1-be5965f4a898")
PPI = UUID("a3fcca6f-22a3-51cd-9d80-055c6c82d1d2")
PDCI = UUID("18067f0f-dad9-5adc-a90c-b65720b62952")
UDEUR = UUID("6fd40362-6019-579b-9f85-6ae48e96a420")
UDC = UUID("55ca99d8-7f43-579c-aaaa-3fc2b03d8f0a")
IDV = UUID("11b9851a-f37c-567f-92cf-6827c77dbeb2")
PDL = UUID("8c7c443a-62cd-5054-b46b-791ab39669be")
PD = UUID("e2dd275d-8eff-5620-8364-588dd9a80a10")
M5S = UUID("01a13bf7-300c-7784-acd9-f60b9930672c")
FDI = UUID("01a13bf7-300c-7784-acd9-f60922c39888")
GROUPS = {
    "gr202": DC,
    "gr201": PCI,
    "gr206": PSI,
    "gr209": UUID("00b94549-f1fb-564b-b2a7-ef67d8473e8b"),
    "gr212": PSDI,
    "gr204": PNM,
    "gr210": PSDI,
    "gr203": PLI,
    "gr205": PRI,
    "gr214": DC,
    "gr213": PCI,
    "gr218": PSI,
    "gr217": PNM,
    "gr216": MSI,
    "gr219": PSDI,
    "gr221": PMP,
    "gr215": PLI,
    "gr223": DC,
    "gr222": PCI,
    "gr226": PSI,
    "gr231": PDIUM,
    "gr227": PSDI,
    "gr225": MSI,
    "gr224": PLI,
    "gr230": PMP,
    "gr229": PNM,
    "gr235": DC,
    "gr234": PCI,
    "gr244": UUID("a99feab2-8cef-5048-91bd-27b23b849882"),
    "gr243": PSI,
    "gr236": PLI,
    "gr242": PSDI,
    "gr240": MSI,
    "gr241": PSIUP,
    "gr238": PDIUM,
    "gr239": PRI,
    "gr246": DC,
    "gr245": PCI,
    "gr257": PSI,
    "gr249": PLI,
    "gr255": PSDI,
    "gr247": MSI,
    "gr252": PSIUP,
    "gr250": PRI,
    "gr248": PDIUM,
    "gr261": DC,
    "gr260": PCI,
    "gr265": PSI,
    "gr262": MSI,
    "gr264": PSDI,
    "gr263": PLI,
    "gr266": PRI,
    "gr274": DC,
    "gr273": PCI,
    "gr276": PSI,
    "gr282": MSI,
    "gr281": UUID("a3660c4e-5465-5e3b-b3c7-a51ac05bccfe"),
    "gr277": PSDI,
    "gr278": PRI,
    "gr280": PR,
    "gr283": DP,
    "gr275": PLI,
    "gr287": DC,
    "gr286": PCI,
    "gr291": PSI,
    "gr288": MSI,
    "gr293": PR,
    "gr290": PSDI,
    "gr294": PRI,
    "gr289": PLI,
    "gr292": UUID("1c586875-696c-510f-8a49-d939eecd8db4"),
    "gr302": DC,
    "gr301": PCI,
    "gr305": PSI,
    "gr303": MSI,
    "gr306": PRI,
    "gr304": PSDI,
    "gr314": PR,
    "gr310": PLI,
    "gr312": DP,
    "gr368": DC,
    "gr367": PCI,
    "gr373": PSI,
    "gr370": MSI,
    "gr374": PRI,
    "gr377": PR,
    "gr376": DP,
    "gr372": PSDI,
    "gr378": UUID("6f29832c-eb9a-5fb5-b29d-b44feaf97608"),
    "gr371": PLI,
    "gr397": DC,
    "gr394": PDS,
    "gr386": PSI,
    "gr389": LEGA,
    "gr399": RC,
    "gr392": MSI,
    "gr385": PRI,
    "gr396": CCD,
    "gr384": VERDI,
    "gr390": PLI,
    "gr393": PSDI,
    "gr388": UUID("b9395fad-9b32-5a6a-9244-dea28d9a7278"),
    "gr413": PDS,
    "gr410": LEGA,
    "gr407": FI,
    "gr404": AN,
    "gr405": CCD,
    "gr414": RC,
    "gr412": PPI,
    "gr409": UUID("def2a04d-d0c0-5339-addc-bbbe637b7353"),
    "gr439": DS,
    "gr418": FI,
    "gr417": AN,
    "gr420": PPI,
    "gr441": LEGA,
    "gr431": CCD,
    "gr421": UUID("c5d89182-fc3e-525c-b4d3-c40b2ed2ffff"),
    "gr458": UDEUR,
    "gr438": UUID("ccb3cb16-adeb-56ef-8b8e-45bbbae93e86"),
    "gr433": PDCI,
    "gr141": FI,
    "gr142": DS,
    "gr139": AN,
    "gr140": UUID("530d39c9-2732-50dd-b806-fcbf34e9b54f"),
    "gr143": UUID("f1b8862f-f8be-56b0-bc45-e11c3cfba57f"),
    "gr145": LEGA,
    "gr153": RC,
    "gr333": UUID("f04fa8dd-0ff3-5971-be77-19a9c0f4bf4f"),
    "gr327": FI,
    "gr328": AN,
    "gr334": RC,
    "gr331": UDC,
    "gr329": LEGA,
    "gr359": UUID("42461dd1-0a68-554d-86ac-0ee4f750bb35"),
    "gr330": IDV,
    "gr361": VERDI,
    "gr360": PDCI,
    "gr362": UDEUR,
    "gr477": PDL,
    "gr475": PD,
    "gr478": LEGA,
    "gr476": UDC,
    "gr729": UUID("8036b090-0e6b-54ef-a597-05c03865f20c"),
    "gr474": IDV,
    "gr1611": PD,
    "gr1610": PDL,
    "gr1612": M5S,
    "gr1613": UUID("5830011c-c697-5ce6-8e8d-60412cfdddcb"),
    "gr1609": UUID("e6b68b22-e54c-5858-a050-ed6df271ebea"),
    "gr1849": UUID("31ec1356-56a0-5039-bf9b-86578e9afd2b"),
    "gr1614": LEGA,
    "gr1889": UUID("10ca7bf3-840b-578a-9af2-f63deb0e3715"),
    "gr1709": FDI,
    "gr3051": M5S,
    "gr3055": UUID("01a13bf7-300c-7784-acd9-f60cef597cca"),
    "gr3054": PD,
    "gr3053": UUID("01a13bf7-300c-7784-acd9-f60dd88e38dd"),
    "gr3892": UUID("01a13bf7-300d-73bb-b6c0-8d0dafcf2a66"),
    "gr3052": FDI,
    "gr3070": UUID("e42315f1-ce3b-59b0-af9c-4bbb8383b75d"),
}


def url(query: str, legislature: int) -> str:
    return "https://dati.camera.it/sparql?" + urlencode({"query": query % legislature, "format": "text/csv"})


def fetch() -> None:
    for legislature in LEGISLATURES:
        raw.fetch(SOURCE, url(MANDATES, legislature), suffix=".csv")
        raw.fetch(SOURCE, url(ADHESIONS, legislature), suffix=".csv")


def day_of(text: str | None) -> date | None:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8])) if text else None


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Members of the Italian Chamber of Deputies for legislatures 1 to 18 (dati.camera.it), as Chamber tenures linked to their contests, with the party of their Chamber group where the ballot names none",
            polity="IT",
        )
        record = cs.source("Camera dei deputati", "dati.camera.it: mandati dei deputati")
        if record in cs.minted:
            cs.add(
                "source",
                id=record,
                kind="official_body",
                publisher="Camera dei deputati",
                title="dati.camera.it: mandati dei deputati",
                url="https://dati.camera.it/",
                license="CC BY 4.0",
            )
        known: dict[str, UUID] = {
            value: entity
            for value, entity in conn.execute(
                """select x.value, coalesce(m.into_id, x.entity_id) from external_id x
                   left join entity_merge m on m.entity_id = x.entity_id where x.scheme = 'it.camera.persona'"""
            ).fetchall()
        }
        interno: dict[str, UUID] = {
            fold(value): entity
            for value, entity in conn.execute(
                """select x.value, coalesce(m.into_id, x.entity_id) from external_id x
                   left join entity_merge m on m.entity_id = x.entity_id where x.scheme = 'it.interno.person'"""
            ).fetchall()
        }
        senators: dict[tuple[str, date], set[UUID]] = defaultdict(set)
        for text, born, entity in conn.execute(
            """select n.text, p.birth_date, p.id from external_id x join person p on p.id = x.entity_id
               join name n on n.entity_id = p.id
               where x.scheme = 'it.senato.senatore' and p.birth_date is not null
               and not exists (select from entity_merge m where m.entity_id = p.id)"""
        ).fetchall():
            senators[(fold(text), born)].add(entity)
        contests: dict[date, dict[str, UUID]] = defaultdict(dict)
        for day, contest, label in conn.execute(
            """select e.date_from, c.id, (select text from name where entity_id = c.id and is_preferred limit 1)
               from contest c join election e on e.id = c.election_id
               where c.body_id = %s and c.allocation_parent_id is null""",
            [CHAMBER],
        ).fetchall():
            contests[day][label or ""] = contest
        runs: dict[date, list[dict[str, object]]] = defaultdict(list)
        for day, person, top, ballot, elected, linked, partied in conn.execute(
            """select e.date_from, cy.person_id, coalesce(c.allocation_parent_id, c.id), cy.ballot_name,
                      exists (select from outcome x join result_set rs on rs.id = x.result_set_id and rs.is_default
                              where x.option_id = o.id and x.kind in ('elected', 'won')),
                      exists (select from external_id s where s.entity_id = cy.person_id and s.scheme = 'it.camera.persona'),
                      coalesce(o.party_id, po.party_id, cy.member_of_party_id) is not null
               from candidacy cy join contest_option o on o.id = cy.option_id
               left join contest_option po on po.id = o.parent_option_id
               join contest c on c.id = o.contest_id join election e on e.id = c.election_id
               where c.body_id = %s and cy.person_id is not null
               and not exists (select from entity_merge m where m.entity_id = cy.person_id)""",
            [CHAMBER],
        ).fetchall():
            runs[day].append(
                {
                    "person": person,
                    "top": top,
                    "ballot": fold(ballot or ""),
                    "elected": elected,
                    "linked": linked,
                    "partied": partied,
                }
            )
        counts: dict[str, int] = defaultdict(int)
        claimed: set[UUID] = set()
        for legislature in LEGISLATURES:
            day = senato.DAYS[legislature - 1]
            artifact = raw.cached(SOURCE, url(MANDATES, legislature))
            cs.artifact(artifact, record)
            frame = pl.read_csv(artifact.path, infer_schema=False).unique("m", keep="first", maintain_order=True)
            opening = day_of(frame["inizio"].min())
            assert opening is not None
            adhesions = raw.cached(SOURCE, url(ADHESIONS, legislature))
            cs.artifact(adhesions, record)
            groups: dict[str, list[tuple[date, date | None, str]]] = defaultdict(list)
            for deputy, group, since, until in (
                pl.read_csv(adhesions.path, infer_schema=False)
                .unique(["d", "g", "inizio"])
                .sort("inizio")
                .select("d", "g", "inizio", "fine")
                .iter_rows()
            ):
                start = day_of(since)
                assert start is not None
                groups[deputy].append((start, day_of(until), group.rsplit("/", 1)[1]))
            entries = runs.get(day, [])
            by_person: dict[UUID, list[dict[str, object]]] = defaultdict(list)
            for entry in entries:
                by_person[entry["person"]].append(entry)
            national = next((contest for label, contest in contests[day].items() if "national" in label), None)
            if national is None and len(contests[day]) == 1:
                national = next(iter(contests[day].values()))
            for row in frame.sort("persona", "inizio").iter_rows(named=True):
                persona = row["persona"].rsplit("/p", 1)[1]
                surname, forename = (row["cognome"] or "").strip(), (row["nome"] or "").strip()
                born = day_of(row["nascita"])
                start = day_of(row["inizio"])
                assert start is not None
                if persona in known:
                    person = known[persona]
                else:
                    full = fold(f"{surname} {forename}")
                    short = fold(f"{surname} {' '.join(word[0] for word in fold(forename).split())}")
                    found = {
                        entry["person"]
                        for entry in entries
                        if not entry["linked"] and entry["person"] not in claimed and entry["ballot"] in (full, short)
                    }
                    if len(found) != 1 and born is not None:
                        found = (
                            {interno[key]}
                            if (key := fold(f"{surname}:{forename}:{born.isoformat()}")) in interno
                            else senators.get((fold(f"{forename} {surname}"), born), set())
                        )
                    if len(found) == 1:
                        person = found.pop()
                        claimed.add(person)
                        cs.add("external_id", entity_id=person, scheme="it.camera.persona", value=persona)
                        counts["matched"] += 1
                    else:
                        person = cs.key("it.camera.persona", persona)
                        cs.add(
                            "person",
                            id=person,
                            gender={"male": "male", "female": "female"}.get(row["genere"] or ""),
                            birth_date=born,
                            birth_precision="day" if born else None,
                        )
                        cs.add(
                            "name",
                            entity_id=person,
                            text=f"{proper(forename)} {proper(surname)}".strip(),
                            lang="it",
                            kind="official",
                            is_preferred=True,
                        )
                        counts["new people"] += 1
                    known[persona] = person
                mine = by_person.get(person, [])
                won = [entry for entry in mine if entry["elected"]]
                pool = won or mine
                tops = {entry["top"] for entry in pool}
                kind = row["tipo"] or ""
                place = row["collegio"] or ""
                if len(tops) == 1:
                    contest = tops.pop()
                elif "suppletiva" in kind:
                    contest = None
                elif place in ABROAD:
                    contest = next((value for label, value in contests[day].items() if ABROAD[place] in label), None)
                elif kind == "proporzionale":
                    contest = national
                else:
                    contest = None
                replaced = (row["proclamazione"] or "").startswith("Subentrato")
                elected = not replaced and ("suppletiva" in kind or bool(won) or start - opening <= timedelta(days=30))
                end = day_of(row["fine"])
                why = row["motivo"] or ""
                tenure = cs.key(f"{SOURCE}.tenure", f"C:{legislature}:{persona}:{start}")
                cs.add(
                    "tenure",
                    id=tenure,
                    person_id=person,
                    office_id=None,
                    body_id=CHAMBER,
                    from_date=start,
                    from_precision="day",
                    to_date=end,
                    to_precision="day",
                    acquired_via="elected" if elected else "list_replacement",
                    contest_id=contest,
                    ended_via=ENDED.get(why, "term_end") if end else None,
                )
                counts["elected" if elected else "list_replacement"] += 1
                counts["with contest" if contest is not None else "without contest"] += 1
                if contest is not None and any(entry["partied"] for entry in pool if entry["top"] == contest):
                    continue
                staged: list[list[object]] = []
                for since, until, group in groups.get(row["d"], []):
                    since, until = max(since, start), until if end is None or until is not None and until < end else end
                    party = GROUPS.get(group)
                    if party is None or until is not None and until <= since:
                        continue
                    if staged and staged[-1][0] == party and staged[-1][2] == since:
                        staged[-1][2] = until
                    else:
                        staged.append([party, since, until])
                for party, since, until in staged:
                    cs.add(
                        "tenure_affiliation",
                        tenure_id=tenure,
                        kind="party",
                        party_id=party,
                        valid_from=since,
                        valid_from_precision="day",
                        valid_to=until,
                        valid_to_precision="day",
                    )
                counts["with group party" if staged else "without party"] += 1
        print(dict(counts), file=sys.stderr)
        return cs.write()
