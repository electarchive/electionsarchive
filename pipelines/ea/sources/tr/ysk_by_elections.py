from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import INDEPENDENTS, REGISTERED, VALID, VOTERS, VOTES
from ea.store import connect

SOURCE = "tr.ysk_by_elections"
VERSION = "1"
PUBLISHER = "Yüksek Seçim Kurulu"
ARCHIVE = "https://www.resmigazete.gov.tr/arsiv"
WAYBACK = "https://web.archive.org/web/2023id_"
LICENSE = (
    "Official notice published in the Resmî Gazete; art. 31 of Law 5846 frees officially published laws, "
    "regulations, and court decisions."
)
PARTIES = {
    "ANAP": ("anavatan-partisi-1983", "Anavatan Partisi"),
    "BAYRAK": ("bayrak-partisi", "Bayrak Partisi"),
    "BAP": ("buyuk-anadolu-partisi", "Büyük Anadolu Partisi"),
    "BVP": ("buyuk-vatan-partisi", "Büyük Vatan Partisi"),
    "DSP": ("demokratik-sol-parti", "Demokratik Sol Parti"),
    "DYP": ("demokrat-parti", "Doğru Yol Partisi"),
    "HDP": ("hur-demokrat-parti", "Hür Demokrat Parti"),
    "IDP": ("millet-partisi", "Islahatçı Demokrasi Partisi"),
    "MÇP": ("milliyetci-hareket-partisi", "Milliyetçi Çalışma Partisi"),
    "RP": ("refah-partisi", "Refah Partisi"),
    "SHP": ("sosyaldemokrat-halkci-parti", "Sosyaldemokrat Halkçı Parti"),
    "VP": ("vatandas-partisi", "Vatandaş Partisi"),
}
NEW_PARTIES = {"BAYRAK", "BAP", "BVP", "VP"}
BY_ELECTIONS = {
    date(1986, 9, 28): (
        19247,
        "Yüksek Seçim Kurulu duyurusu 1986/15: 28 Eylül 1986 Milletvekili Ara Seçimi sonuçları (Resmî Gazete 19247)",
        date(1983, 11, 6),
        ("ANAP", "BAYRAK", "BAP", "BVP", "DSP", "DYP", "HDP", "IDP", "MÇP", "RP", "SHP", "VP", INDEPENDENTS),
        """
06-2 344556 285289 278000 103674 842 - 3658 21355 54960 7857 2687 12318 11762 55697 2186 1004
12 75440 68258 64140 21743 163 428 536 1324 13369 692 394 1008 9202 14577 372 332
15 125085 117828 113081 41975 588 1204 1872 5889 28502 1442 1077 3789 3950 21291 1502 -
27 351406 305039 295337 104516 854 1846 3933 10140 52083 3120 2015 8326 17283 88941 2280 -
34-6 484642 376942 364492 138840 900 1993 4422 50280 47958 3909 1107 3891 31247 74256 3717 1972
35-2 419210 374520 363703 106735 827 1686 4055 33529 70907 3260 1207 3454 7787 125568 3296 1392
45-1 331535 311641 302673 82481 1049 1974 3745 18688 99798 2824 1352 5070 14585 68077 3030 -
45-2 193287 184049 178325 48624 680 1494 2131 11216 64823 1823 1106 2526 6897 35103 1902 -
51 212004 188702 180777 56537 1097 - 2802 9135 49109 2852 1707 6732 16206 31973 2627 -
55-2 178325 162906 158555 47079 714 - 2190 13565 52870 1782 1177 4359 6252 26165 1796 606
67-2 234664 217578 208129 53063 1344 2872 2959 38047 55690 4756 1900 3671 12314 28407 3106 -
""",
        (2950154, 2592752, 2507212),
        (805267, 9058, 13497, 32303, 213168, 590069, 34317, 15729, 55144, 137485, 570055, 25814, 5306),
        {
            "06-2": ("ANAP", "Vahit Melih Halefoğlu"),
            "12": ("ANAP", "Mahmut Sönmez"),
            "15": ("ANAP", "Sait Ekinci"),
            "27": ("ANAP", "Hasan Celal Güzel"),
            "34-6": ("ANAP", "Hüsnü Doğan"),
            "35-2": ("SHP", "Erdal İnönü"),
            "45-1": ("DYP", "Sümer Oral"),
            "45-2": ("DYP", "Ümit Canuyar"),
            "51": ("ANAP", "Akın Gönen"),
            "55-2": ("DYP", "Hüsamettin Cindoruk"),
            "67-2": ("DYP", "Köksal Toptan"),
        },
        "Grand National Assembly by-elections, 1986",
        (
            "Each by-election filled one vacant seat of a 1983 electoral district under the same D'Hondt rule as "
            "the general election, so the list or independent with the most votes took the seat. YSK publishes no "
            "national threshold for these votes."
        ),
    ),
}


def fetch() -> None:
    for issue, *_ in BY_ELECTIONS.values():
        url = f"{ARCHIVE}/{issue}.pdf"
        try:
            raw.fetch(SOURCE, url, suffix=".pdf")
        except httpx.HTTPError:
            response = httpx.get(f"{WAYBACK}/{url}", follow_redirects=True, timeout=180)
            response.raise_for_status()
            raw.store(
                SOURCE,
                url,
                response.content,
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 1986 National Assembly by-elections by electoral district, from YSK's notice",
            polity="TR",
        )
        turkey = cs.polity("TR")

        def name(entity, text, lang="tr", kind="official", preferred=True):
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        for day, (issue, title, general, parties, table, totals, votes, winners, label, rules) in BY_ELECTIONS.items():
            rows = {
                unit: [0 if cell == "-" else int(cell) for cell in cells]
                for unit, *cells in (line.split() for line in table.strip().splitlines())
            }
            for unit, (registered, voters, valid, *counts) in rows.items():
                if sum(counts) != valid or not valid < voters < registered:
                    raise ValueError(f"{day} {unit}: the notice's figures do not add up")
            columns = list(zip(*rows.values(), strict=True))
            if [sum(column) for column in columns] != [*totals, *votes]:
                raise ValueError(f"{day}: the districts do not sum to the notice's totals")
            url = f"{ARCHIVE}/{issue}.pdf"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license=LICENSE,
            )
            cs.artifact(raw.cached(SOURCE, url), source)
            system = cs.key("tr.electoral_system", f"by_election_{day.year}")
            if system in cs.minted:
                cs.add(
                    "electoral_system",
                    id=system,
                    polity_id=turkey,
                    rules={"lists": {"order": "closed", "vacancy": "by_election"}, "formula": "dhondt"},
                    rules_version=1,
                )
                name(system, label, "en")
                cs.add("note", entity_id=system, kind="methodology", body_md=rules)
            election = cs.key("tr.election", f"{day}:by-election")
            cs.add(
                "election",
                id=election,
                polity_id=turkey,
                kind="by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day.year} Turkish Grand National Assembly by-elections", "en")
            name(election, f"{day.day} Eylül {day.year} Milletvekili Ara Seçimi", kind="native", preferred=False)
            cs.add("slug", path=f"tr/{day}-by-election", entity_id=election)
            party_ids = {}
            for short in parties[:-1]:
                key, full = PARTIES[short]
                party_ids[short] = cs.key("tr.party", key)
                if (party_ids[short] in cs.minted) != (short in NEW_PARTIES):
                    raise ValueError(f"tr.party {key}: expected {'new' if short in NEW_PARTIES else 'existing'}")
                if short in NEW_PARTIES:
                    cs.add("party", id=party_ids[short], kind="party", polity_id=turkey)
                    name(party_ids[short], full)
            members = {}
            for person, text in conn.execute(
                """select n.entity_id, n.text from name n
                   where exists (select from external_id x where x.entity_id = n.entity_id
                                 and x.scheme = 'tr.tbmm.person' and x.value like '17:%%')"""
            ).fetchall():
                members.setdefault(text, set()).add(person)
            for unit, (registered, voters, valid, *counts) in rows.items():
                found = conn.execute(
                    """select c.body_id, c.district_area_id, u.id, n.text from external_id x
                       join contest c on c.id = x.entity_id
                       join reporting_unit u on u.scheme_code = 'tr.secim_cevresi' and u.code = %s
                       join name n on n.entity_id = c.id and n.lang = 'en'
                       where x.scheme = 'tr.contest' and x.value = %s""",
                    [unit, f"{general}:parliament:{unit}"],
                ).fetchone()
                if found is None:
                    raise ValueError(f"{general}:parliament:{unit} is not in the archive")
                body, area, district, place = found
                place = place.removeprefix("Grand National Assembly, ")
                contest_key = f"{day}:by-election:{unit}"
                contest = cs.key("tr.contest", contest_key)
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
                name(contest, f"Grand National Assembly, {place}", "en")
                cs.add("slug", path=f"tr/{day}-by-election/{slug(place)}", entity_id=contest)
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                for measure, value in ((REGISTERED, registered), (VOTERS, voters), (VALID, valid)):
                    cs.add(
                        "result_value",
                        result_set_id=declared,
                        unit_id=district,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
                won, winner = winners[unit]
                for short, value in zip(parties, counts, strict=True):
                    if not value:
                        continue
                    independent = short == INDEPENDENTS
                    option = cs.key(
                        "tr.option", f"{contest_key}:{slug(INDEPENDENTS) if independent else PARTIES[short][0]}"
                    )
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if independent else "list",
                        party_id=None if independent else party_ids[short],
                        ballot_label=INDEPENDENTS if independent else PARTIES[short][1],
                    )
                    cs.add(
                        "result_value",
                        result_set_id=declared,
                        unit_id=district,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
                    if short != won:
                        continue
                    matched = members.get(winner, set())
                    if len(matched) != 1:
                        raise ValueError(f"{contest_key}: {winner} matches {len(matched)} term-17 members")
                    candidacy = cs.key("tr.candidacy", f"{contest_key}:1")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=next(iter(matched)),
                        option_id=option,
                        role="list_member",
                        list_position=None,
                        ballot_name=None,
                        member_of_party_id=None,
                        is_independent=False,
                    )
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party_ids[short], kind="nominated")
                    cs.add("outcome", result_set_id=declared, option_id=option, kind="won", seats=1)
                    cs.add("outcome", result_set_id=declared, option_id=option, candidacy_id=candidacy, kind="elected")
                    cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
        return cs.write()
