from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.mayors_2004_2009 import fold
from ea.sources.tr.tbmm_album import TITLE as ALBUM_TITLE
from ea.sources.tr.ysk import PROVINCES
from ea.sources.tr.ysk_local import party
from ea.store import connect

SOURCE = "tr.by_elections_1951"
VERSION = "1"
DAY = date(1951, 9, 16)
URL = "https://www.tesav.org.tr/wp-content/uploads/2018/02/8.-milletvekiliarasecimsonuclari.pdf"
TITLE = "Osmanlı'dan Günümüze Seçimler (1877-2002), Milletvekili ara seçimi sonuçları"
TOTALS = (
    "State Institute of Statistics (DİE) prints only the totals over all 17 provinces: 3,168,423 registered voters, "
    "1,778,853 voters, 1,777,638 valid votes; DP 937,288, CHP 687,668, MP 142,359, and independents 10,323. It prints "
    "no province rows, so no contest here has vote counts; the winners and their parties are the TBMM Albümü's."
)


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def emit() -> Path:
    album = pl.read_parquet(raw.parsed("tr.tbmm_album") / "entries.parquet").filter(
        pl.col("term") == 9
    )
    affiliations = {
        (fold(province), fold(name.split()[-1])): parties[0]
        for province, name, parties in album.select("province", "name", "parties").iter_rows()
        if len(parties) > 0
    }
    plates = {fold(name): f"{plate:02d}" for plate, name in enumerate(PROVINCES, 1)}
    with connect() as conn:
        members: dict[tuple[str, str], tuple[object, str]] = {}
        for person, text, province in conn.execute(
            """select t.person_id, n.text, regexp_replace(note.body_md, 'Member for (.*), entering.*', '\\1')
               from tenure t
               join name n on n.entity_id = t.person_id and n.is_preferred
               join note on note.entity_id = t.id
               where t.from_date = %s and t.body_id = (select entity_id from external_id where scheme = 'tr.body' and value = 'tbmm')""",
            [DAY],
        ).fetchall():
            members[(fold(province), fold(text.split()[-1]))] = (person, text)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="National Assembly by-elections of 16 September 1951 in 17 provinces: winners and parties, no votes",
            polity="TR",
        )
        tesav = cs.source("Türkiye Ekonomik ve Sosyal Tarihi Vakfı", TITLE)
        cs.add(
            "source",
            id=tesav,
            kind="compendium",
            publisher="Türkiye Ekonomik ve Sosyal Tarihi Vakfı",
            title=TITLE,
            url=URL,
            license="Reproduction of an official statistical table, no license stated",
        )
        cs.artifact(raw.cached(SOURCE, URL), tesav)
        album_source = cs.source("Türkiye Büyük Millet Meclisi", ALBUM_TITLE)
        assembly = cs.key("tr.body", "tbmm")
        system = cs.key("tr.electoral_system", "parliament_1946")
        election = cs.key("tr.election", f"{DAY}:by-election")
        cs.add(
            "election",
            id=election,
            polity_id=cs.polity("TR"),
            kind="by_election",
            date_from=DAY,
            date_to=DAY,
            status="held",
        )
        for text, lang, kind in (
            ("1951 Turkish National Assembly by-elections", "en", "official"),
            ("16 Eylül 1951 Milletvekili Ara Seçimi", "tr", "native"),
        ):
            cs.add(
                "name",
                entity_id=election,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
            )
        cs.add("slug", path=f"tr/{DAY}-by-election", entity_id=election)
        cs.add("note", entity_id=election, kind="methodology", body_md=TOTALS)
        for province in sorted({province for province, _ in members}):
            plate = plates[province]
            winners = {key: value for key, value in members.items() if key[0] == province}
            place = PROVINCES[int(plate) - 1]
            contest = cs.key("tr.contest", f"{DAY}:by-election:{plate}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=assembly,
                district_area_id=cs.area("tr.il", plate),
                magnitude=len(winners),
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"National Assembly, {place}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"tr/{DAY}-by-election/{slug(place)}", entity_id=contest)
            declared = cs.key(f"{SOURCE}.result_set", f"{DAY}:{plate}")
            cs.add(
                "result_set",
                id=declared,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=album_source,
                is_default=True,
                completeness=0,
            )
            cs.add("citation", entity_id=declared, source_id=album_source, locator={"province": PROVINCES[int(plate) - 1]})
            cs.add("citation", entity_id=declared, source_id=tesav, locator={"table": "16 Eylül 1951"})
            for (_, surname), (person, preferred) in winners.items():
                owner = party(affiliations[(province, surname)], 1951)
                party_id = cs.key("tr.party", owner)
                option = cs.key("tr.option", f"{DAY}:by-election:{plate}:{slug(preferred)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_label=preferred,
                )
                candidacy = cs.key("tr.candidacy", f"{DAY}:by-election:{plate}:{slug(preferred)}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    member_of_party_id=party_id,
                    is_independent=False,
                )
                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party_id, kind="nominated")
                cs.add(
                    "outcome",
                    result_set_id=declared,
                    option_id=option,
                    candidacy_id=candidacy,
                    kind="elected",
                    seats=None,
                )
        return cs.write()
