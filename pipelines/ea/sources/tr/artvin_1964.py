from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.artvin_1964"
VERSION = "1"
PUBLISHER = "Cumhuriyet Senatosu"
CONTEST = UUID("e5e4bfba-9131-5383-9cdd-2caab902189a")
RESULT_SET = UUID("841a2f91-adc3-56d7-8a1f-d26612465cba")
CHP = UUID("01a13c0c-c7c5-74b3-a52f-540b0464c07c")
ANNULMENT = "https://www5.tbmm.gov.tr/tutanaklar/TUTANAK/CS__/t03/c021/cs__03021088.pdf"
CERTIFICATE = "https://www5.tbmm.gov.tr/tutanaklar/TUTANAK/CS__/t04/c022/cs__04022001.pdf"
TITLES = {
    ANNULMENT: "Cumhuriyet Senatosu Tutanak Dergisi, 3. Dönem, Cilt 21, 88. Birleşim (Yüksek Seçim Kurulu'nun Artvin kararı)",
    CERTIFICATE: "Cumhuriyet Senatosu Tutanak Dergisi, 4. Dönem, Cilt 22, 1. Birleşim (Artvin seçim tutanağı ve andiçme)",
}
LICENSE = "No license stated. The Assembly's published proceedings."
ANNULLED = (
    "The Supreme Election Council annulled this Artvin Senate election on 8 July 1964 (decision 1476, objection 1308) and "
    "ordered a new vote, which was held on 20 September 1964."
)
NOT_ONLINE = (
    "Only the winner is recorded: the Senate's minutes print the provincial election board's certificate for Fehmi "
    "Alpaslan, and no free online source gives the rerun's votes (they are in the State Institute of Statistics' "
    "1966 results book)."
)


def fetch() -> None:
    for url in TITLES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Artvin's Senate election rerun of 20 September 1964, winner only, and the annulment of the June vote",
            polity="TR",
        )
        june, body, area, system = conn.execute(
            "select election_id, body_id, district_area_id, electoral_system_id from contest where id = %s", [CONTEST]
        ).fetchone()
        sources = {}
        for url, title in TITLES.items():
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license=LICENSE,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        cs.add("note", entity_id=RESULT_SET, kind="caveat", body_md=ANNULLED)
        cs.add("citation", entity_id=RESULT_SET, source_id=sources[ANNULMENT], locator=None)
        election = cs.key("tr.election", "1964-09-20:artvin")
        cs.add(
            "election",
            id=election,
            polity_id=conn.execute("select polity_id from election where id = %s", [june]).fetchone()[0],
            kind="by_election",
            date_from="1964-09-20",
            date_to="1964-09-20",
            status="held",
            rerun_of_id=june,
        )
        cs.add("name", entity_id=election, text="1964 Artvin Senate rerun", lang="en", kind="official", is_preferred=True)
        cs.add(
            "name",
            entity_id=election,
            text="20 Eylül 1964 Artvin Cumhuriyet Senatosu Üyeliği Yenileme Seçimi",
            lang="tr",
            kind="native",
            is_preferred=False,
        )
        cs.add("slug", path="tr/1964-09-20-rerun", entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=ANNULLED)
        contest = cs.key("tr.contest", "1964-09-20:senate:artvin")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("name", entity_id=contest, text="Senate of the Republic, Artvin", lang="en", kind="official", is_preferred=True)
        cs.add("slug", path="tr/1964-09-20-rerun/artvin", entity_id=contest)
        declared = cs.key(f"{SOURCE}.result_set", "1964-09-20:senate:artvin")
        cs.add(
            "result_set",
            id=declared,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=sources[CERTIFICATE],
            is_default=True,
            completeness=0,
        )
        cs.add("note", entity_id=declared, kind="caveat", body_md=NOT_ONLINE)
        option = cs.key("tr.option", "1964-09-20:senate:artvin:cumhuriyet-halk-partisi")
        cs.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=CHP,
            ballot_label="Cumhuriyet Halk Partisi",
        )
        person = cs.key(f"{SOURCE}.person", "fehmi-alpaslan")
        cs.add("person", id=person)
        cs.add("name", entity_id=person, text="Fehmi Alpaslan", lang="tr", kind="official", is_preferred=True)
        candidacy = cs.key("tr.candidacy", "1964-09-20:senate:artvin:fehmi-alpaslan")
        cs.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=None,
            ballot_name=None,
            member_of_party_id=None,
            is_independent=False,
        )
        cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=CHP, kind="nominated")
        cs.add("outcome", result_set_id=declared, option_id=option, candidacy_id=candidacy, kind="elected", seats=None, notes=None)
        cs.add("citation", entity_id=candidacy, source_id=sources[CERTIFICATE], locator=None)
        return cs.write()
