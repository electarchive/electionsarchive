import struct
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr import die_local
from ea.sources.tr.ysk import DIGITIZED, INDEPENDENTS, REGISTERED, VALID, VOTERS, VOTES
from ea.store import DATA, connect

SOURCE = "tr.ysk_by_elections"
VERSION = "2"
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
HATAY_DAY = date(1966, 6, 5)
HATAY_BOOK = f"{die_local.LIBRARY}/0015213.pdf"
HATAY_PUBLISHER = "Devlet İstatistik Enstitüsü"
HATAY_TITLE = (
    "5 Haziran 1966 Cumhuriyet Senatosu Üyeleri Kısmi Seçim Sonuçları ve Hatay İlinde Boş Bulunan 1 Milletvekili "
    "Ara Seçimi Sonucu (DİE yayın no. 525)"
)
HATAY_LICENSE = (
    "Official statistics published by the State Institute of Statistics under Laws 306 and 307; served by TÜİK's "
    "library, which states no license, retrieved through the Wayback Machine."
)
HATAY_PARTIES = (
    ("AP", "adalet-partisi-1961", "Adalet Partisi"),
    ("CHP", "cumhuriyet-halk-partisi", "Cumhuriyet Halk Partisi"),
    ("CKMP", "cumhuriyetci-koylu-millet-partisi", "Cumhuriyetçi Köylü Millet Partisi"),
    ("MP", "millet-partisi-1962", "Millet Partisi"),
    ("TİP", "turkiye-isci-partisi-1961", "Türkiye İşçi Partisi"),
    ("YTP", "yeni-turkiye-partisi-1961", "Yeni Türkiye Partisi"),
)
HATAY_DECLARED = (29, (199170, 105530, 100584, 44598, 36096, 1063, 1488, 4949, 0, 12390))
HATAY_ILCE = {
    "Merkez": "Hatay Merkez",
    "Altınözü": "Altınözü",
    "Dörtyol": "Dörtyol",
    "Hassa": "Hassa",
    "İskenderun": "İskenderun",
    "Kırıkhan": "Kırıkhan",
    "Reyhanlı": "Reyhanlı",
    "Samandağı": "Samandağ",
    "Yayladağı": "Yayladağı",
}
HATAY_TABLE = 38
HATAY_METHOD = (
    "The district figures are YSK's declared results as published in the Resmî Gazete of 9 and 11 June 1966, "
    "reprinted in table 1 of the State Institute of Statistics' book. The ilçe figures come from table 2, the "
    "ilçe election boards' consolidation reports as printed by the Institute."
)
HATAY_CAVEAT = (
    "The ilçe rows add up to 105,540 voters and 100,586 valid votes, against the 105,530 and 100,584 that YSK "
    "declared; party votes agree. In Merkez and Reyhanlı, party and independent votes add up to one fewer than "
    "the valid votes (22,297 against 22,298, and 7,751 against 7,752). The archive keeps every figure as printed. The book does not name the member elected for the "
    "Justice Party list."
)
PAGES = DATA / "pages" / SOURCE


def located(words: list[tuple], value: int) -> list[float] | None:
    target = f"{value:,}".replace(",", " ")
    tokens = [w for w in words if die_local.numeric(w[4])]
    for start in range(len(tokens)):
        for end in range(start + 1, min(start + 4, len(tokens)) + 1):
            if " ".join(t[4] for t in tokens[start:end]) == target:
                return [
                    min(t[0] for t in tokens[start:end]),
                    min(t[1] for t in tokens[start:end]),
                    max(t[2] for t in tokens[start:end]),
                    max(t[3] for t in tokens[start:end]),
                ]
    return None


def fetch() -> None:
    try:
        raw.cached(SOURCE, HATAY_BOOK)
    except LookupError:
        response = httpx.get(f"{WAYBACK}/{HATAY_BOOK}", follow_redirects=True, timeout=600)
        response.raise_for_status()
        raw.store(
            SOURCE,
            HATAY_BOOK,
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )
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
            summary="Turkey's 1966 and 1986 National Assembly by-elections: 1966 Hatay by ilçe from DİE, 1986 by district",
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
                if party_ids[short] in cs.minted and short not in NEW_PARTIES:
                    raise ValueError(f"tr.party {key} is not in the archive")
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
                        kind="independents" if independent else "list",
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
        book = raw.cached(SOURCE, HATAY_BOOK)
        source = cs.source(HATAY_PUBLISHER, HATAY_TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=HATAY_PUBLISHER,
            title=HATAY_TITLE,
            url=HATAY_BOOK,
            license=HATAY_LICENSE,
        )
        artifact = cs.artifact(book, source)
        read = die_local.pages(str(book.path), HATAY_DECLARED[0], HATAY_TABLE)
        page_ids = {}
        for number in (HATAY_DECLARED[0], HATAY_TABLE):
            image = PAGES / f"{book.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "100",
                        "-png",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-singlefile",
                        str(book.path),
                        str(image.with_suffix("")),
                    ],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            page_ids[number] = cs.page(artifact, number)
            cs.add(
                "source_page",
                id=page_ids[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        width, height, words = read[HATAY_TABLE]
        rows = {}
        for line in die_local.lines(words, 0):
            label = line[0][4]
            if label in HATAY_ILCE and len(line) > 10:
                rows[HATAY_ILCE[label]] = line
        expected = {
            "Hatay Merkez": (50311, 23542, 22298, 9704, 5500, 215, 201, 1839, 0, 4838),
            "Altınözü": (16454, 6786, 6474, 2635, 3064, 87, 110, 476, 0, 102),
            "Dörtyol": (18171, 10655, 10196, 5438, 4239, 143, 194, 172, 0, 10),
            "Hassa": (9301, 5779, 5571, 3798, 1426, 64, 215, 58, 0, 10),
            "İskenderun": (44037, 24462, 23434, 10551, 9836, 195, 343, 487, 0, 2022),
            "Kırıkhan": (18127, 11268, 10878, 4079, 5689, 106, 149, 819, 0, 36),
            "Reyhanlı": (13468, 8207, 7752, 2899, 4071, 112, 119, 498, 0, 52),
            "Samandağ": (20146, 10362, 9674, 2648, 1087, 83, 74, 470, 0, 5312),
            "Yayladağı": (9155, 4479, 4309, 2846, 1184, 58, 83, 130, 0, 8),
        }
        if set(rows) != set(expected):
            raise ValueError(f"1966 Hatay: the ilçe table has {sorted(rows)}")
        sums = [sum(column) for column in zip(*expected.values(), strict=True)]
        if sums[3:] != list(HATAY_DECLARED[1][3:]) or sums[:3] != [199170, 105540, 100586]:
            raise ValueError("1966 Hatay: the ilçe rows do not add up to the book's totals")
        system = cs.key("tr.electoral_system", "by_election_1966")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=turkey,
            rules={"lists": {"order": "closed", "vacancy": "by_election"}, "formula": "dhondt"},
            rules_version=1,
        )
        name(system, "National Assembly by-election, 1966", "en")
        cs.add(
            "note",
            entity_id=system,
            kind="methodology",
            body_md="The by-election filled one vacant seat of a province's list, so the list or independent with "
            "the most votes took it.",
        )
        election = cs.key("tr.election", f"{HATAY_DAY}:by-election")
        cs.add(
            "election",
            id=election,
            polity_id=turkey,
            kind="by_election",
            date_from=HATAY_DAY,
            date_to=HATAY_DAY,
            status="held",
        )
        name(election, "1966 Turkish National Assembly by-election", "en")
        name(election, "5 Haziran 1966 Milletvekili Ara Seçimi", kind="native", preferred=False)
        cs.add("slug", path=f"tr/{HATAY_DAY}-by-election", entity_id=election)
        contest_key = f"{HATAY_DAY}:by-election:31"
        contest = cs.key("tr.contest", contest_key)
        assembly = cs.key("tr.body", "millet-meclisi")
        if assembly in cs.minted:
            raise ValueError("publish tr.thea before tr.ysk_by_elections")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=assembly,
            district_area_id=cs.area("tr.il", "31"),
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, "National Assembly, Hatay", "en")
        cs.add("slug", path=f"tr/{HATAY_DAY}-by-election/hatay", entity_id=contest)
        declared = cs.key(f"{SOURCE}.result_set", contest_key)
        cs.add(
            "result_set",
            id=declared,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
        )
        cs.add("note", entity_id=declared, kind="methodology", body_md=HATAY_METHOD)
        cs.add("note", entity_id=declared, kind="caveat", body_md=HATAY_CAVEAT)
        options = []
        for short, key, full in HATAY_PARTIES:
            party = cs.key("tr.party", key)
            if party in cs.minted:
                raise ValueError(f"tr.party {key} is not in the archive")
            options.append(cs.key("tr.option", f"{contest_key}:{key}"))
            cs.add("contest_option", id=options[-1], contest_id=contest, kind="list", party_id=party, ballot_label=full)
        options.append(cs.key("tr.option", f"{contest_key}:{slug(INDEPENDENTS)}"))
        cs.add(
            "contest_option",
            id=options[-1],
            contest_id=contest,
            kind="independents",
            party_id=None,
            ballot_label=INDEPENDENTS,
        )
        cs.add("outcome", result_set_id=declared, option_id=options[0], kind="won", seats=1)
        measures = [(REGISTERED, None), (VOTERS, None), (VALID, None), *((VOTES, option) for option in options)]
        district = cs.unit("tr.secim_cevresi", "31")
        placed = [
            (
                district,
                HATAY_DECLARED[0],
                HATAY_DECLARED[1],
                [w for w in read[HATAY_DECLARED[0]][2] if 712 < w[1] < 726],
            )
        ]
        for code, values in expected.items():
            unit = cs.unit("tr.ilce", f"31:{code}")
            if unit in cs.minted:
                raise ValueError(f"tr.ilce 31:{code} is not in the archive")
            placed.append((unit, HATAY_TABLE, values, rows[code]))
        for unit, number, values, line in placed:
            if unit in cs.minted:
                raise ValueError("publish tr.thea before tr.ysk_by_elections")
            width, height, _ = read[number]
            for (measure, option), value in zip(measures, values, strict=True):
                if option is not None and value == 0:
                    continue
                fact = {
                    "result_set_id": declared,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=DIGITIZED, value=value)
                found = located(line, value)
                if found is None:
                    raise ValueError(f"1966 Hatay: {value:,} is not on page {number}")
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=page_ids[number],
                    source_id=None,
                    x0=found[0] / width,
                    y0=found[1] / height,
                    x1=found[2] / width,
                    y1=found[3] / height,
                )
        return cs.write()
