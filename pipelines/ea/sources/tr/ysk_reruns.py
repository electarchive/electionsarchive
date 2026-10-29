import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.store import connect

SOURCE = "tr.ysk_reruns"
VERSION = "4"
FAALIYET = "https://www.ysk.gov.tr/doc/dosyalar/docs/Faaliyet_Raporlari/2024_Yili_idari_Faaliyet_Raporu.pdf"
ANNEX_2019 = "https://www.ysk.gov.tr/doc/dosyalar/docs/2019MahalliIdareler/KesinSecimSonuclari/2019Mahalli-Ek-2.pdf"
AA_2024 = (
    "https://www.aa.com.tr/tr/gundem/ysk-2-haziranda-yapilan-yenileme-secimlerinin-kesin-sonuclarini-acikladi/3259545"
)
AA_2019 = "https://www.aa.com.tr/tr/politika/3-ilce-ve-bir-beldede-yenileme-secimleri-yapildi/1495170"
BOOK = "https://www.ysk.gov.tr/doc/dosyalar/docs/2009_2019_%C4%B0STAT%C4%B0ST%C4%B0K_K%C4%B0TABI.pdf"
SABAH_2019 = "https://www.sabah.com.tr/gundem/2019/08/04/adiyamanin-ilk-kadin-belediye-baskani-secildi"
WIKI = {
    "denizli-honaz": "Denizli'de 2019 Türkiye yerel seçimleri",
    "kirikkale-keskin": "Kırıkkale'de 2019 Türkiye yerel seçimleri",
    "artvin-yusufeli": "Artvin'de 2019 Türkiye yerel seçimleri",
    "adiyaman-besni-kesmetepe": "Adıyaman'da 2019 Türkiye yerel seçimleri",
    "kayseri-pinarbasi": "Kayseri'de 2024 Türkiye yerel seçimleri",
    "aksaray-guzelyurt": "Aksaray'da 2024 Türkiye yerel seçimleri",
    "sanliurfa-hilvan": "Şanlıurfa'da 2024 Türkiye yerel seçimleri",
    "kirklareli-luleburgaz-buyukkaristiran": "Kırklareli'de 2024 Türkiye yerel seçimleri",
    "tunceli-mazgirt-akpazar": "Tunceli'de 2024 Türkiye yerel seçimleri",
    "aksaray-merkez-saglik": "Aksaray'da 2024 Türkiye yerel seçimleri",
}
WIKI_SOURCES = {
    2019: UUID("993b963e-f0be-52f1-8bd7-cc2d77a068b9"),
    2024: UUID("bc682548-3abe-5e3c-9330-1c04702c76a0"),
}
RERUN_2019 = date(2019, 6, 2)
SUVARLI = date(2019, 8, 4)
RERUN_2024 = date(2024, 6, 2)
MARCH_2019 = date(2019, 3, 31)
MARCH_2024 = date(2024, 3, 31)
PARTIES = {
    "AK PARTİ": "adalet-ve-kalkinma-partisi",
    "BTP": "bagimsiz-turkiye-partisi",
    "BÜYÜK BİRLİK": "buyuk-birlik-partisi",
    "CHP": "cumhuriyet-halk-partisi",
    "DEM PARTİ": "halklarin-esitlik-ve-demokrasi-partisi",
    "DEVA PARTİSİ": "demokrasi-ve-atilim-partisi",
    "DP": "demokrat-parti",
    "DSP": "demokratik-sol-parti",
    "İYİ PARTİ": "iyi-parti",
    "MHP": "milliyetci-hareket-partisi",
    "SAADET": "saadet-partisi",
    "VATAN": "vatan-partisi",
    "YENİDEN REFAH": "yeniden-refah-partisi",
    "ZAFER PARTİSİ": "zafer-partisi",
    "EMEP": "emek-partisi",
    "HÜDA PAR": "hur-dava-partisi",
    "MİLLİ YOL": "milli-yol-partisi",
    "TİP": "turkiye-isci-partisi",
    "TKP": "turkiye-komunist-partisi",
    "VATAN PARTİSİ": "vatan-partisi",
    "BAĞIMSIZ": None,
}
RACES_2019 = {
    "denizli-honaz": ("20", "Honaz", None, "CHP", "Yüksel Kepenek", [21872, 19613, 19178, 435]),
    "kirikkale-keskin": ("71", "Keskin", None, "AK PARTİ", "Dede Yıldırım", None),
    "artvin-yusufeli": ("08", "Yusufeli", None, "AK PARTİ", "Eyüp Aytekin", None),
    "adiyaman-besni-kesmetepe": ("02", "Besni", "Kesmetepe", "DSP", "Ali Yılmaz", [None, 1115, 1083, 32]),
}
VOTES_2019 = {
    "denizli-honaz": {"CHP": 10697, "AK PARTİ": 8359, "SAADET": 24, "BTP": 14, "VATAN": 55, "DP": 29},
    "kirikkale-keskin": {"AK PARTİ": 2426, "İYİ PARTİ": 2162, "MHP": 627},
    "artvin-yusufeli": {"AK PARTİ": 2446, "CHP": 2079, "SAADET": 19},
    "adiyaman-besni-kesmetepe": {"AK PARTİ": 65, "CHP": 325, "DP": 320, "MHP": 3, "DSP": 370},
}
WINNERS_2024 = {
    "kayseri-pinarbasi": "Deniz Yağan",
    "aksaray-guzelyurt": "Ünal Demircioğlu",
    "sanliurfa-hilvan": "Serhan Paydaş",
    "sivas-yildizeli-guneykaya": "Hüseyin Kırışkuzu",
    "kirklareli-luleburgaz-buyukkaristiran": "Ertuğrul Çamlıca",
    "tunceli-mazgirt-akpazar": "Ali Aydın",
    "aksaray-merkez-saglik": "Aptullah Tosun",
}
PLACES_2024 = {
    "Kayseri Pınarbaşı İlçe": ("kayseri-pinarbasi", "38", "Pınarbaşı", None),
    "Aksaray Güzelyurt İlçe": ("aksaray-guzelyurt", "68", "Güzelyurt", None),
    "Şanlıurfa Hilvan İlçe": ("sanliurfa-hilvan", "63", "Hilvan", None),
    "Sivas Yıldızeli İlçesi Güneykaya": ("sivas-yildizeli-guneykaya", "58", "Yıldızeli", "Güneykaya"),
    "Tunceli Mazgirt İlçesi Akpazar": ("tunceli-mazgirt-akpazar", "62", "Mazgirt", "Akpazar"),
    "Kırklareli Lüleburgaz İlçesi Büyükkarıştıran": (
        "kirklareli-luleburgaz-buyukkaristiran",
        "39",
        "Lüleburgaz",
        "Büyükkarıştıran",
    ),
    "Aksaray Merkez İlçesi Sağlık": ("aksaray-merkez-saglik", "68", "Aksaray Merkez", "Sağlık"),
}
ANNULLED_COUNCILS = {"aksaray-guzelyurt", "kayseri-pinarbasi", "sanliurfa-hilvan", "sivas-yildizeli-guneykaya"}
SHORT_2024 = {("kayseri-pinarbasi", "Meclis Üyeliği"): 37}
OFFICIAL_2019 = (
    "YSK publishes the four June 2, 2019 races only as one combined table (Resmî Gazete 30828, decision 2019/5002, "
    "annex 2): 34,630 registered voters, 128 ballot boxes, 30,877 voters, and 30,140 valid votes, with AK Parti 13,297 "
    "votes and two mayoralties, CHP 13,103 and one, DSP 370 and one, İYİ Parti 2,165, MHP 718, Demokrat Parti 349, "
    "Saadet 56, Vatan 55, and Bağımsız Türkiye Partisi 27. The votes here are Anadolu Ajansı's unofficial "
    "election-night counts for this race and can differ slightly from the final ones."
)
OFFICIAL_2024 = (
    "YSK's 2024 annual report prints each of the seven June 2, 2024 races separately. The votes are the party totals "
    "it gives, and invalid votes are the voters minus the valid votes; it does not name the candidates, whose names for the winners come from Anadolu Ajansı."
)
SUVARLI_RACE = ("adiyaman-besni-suvarli", "02", "Besni", "Suvarlı", "AK PARTİ", "Gülfer Ağır")
SUVARLI_MAYOR = ([1867, 1700, 1602, 98], {"AK PARTİ": 894, "BTP": 11, "VATAN": 697})
SUVARLI_COUNCIL = ([1867, 1699, 1572, 127], {"AK PARTİ": 894, "VATAN": 678})
OFFICIAL_SUVARLI = (
    "YSK annulled Suvarlı's March 31, 2019 mayoral and council elections (decision 4711 of May 28, 2019) and held "
    "both again on August 4, 2019. The votes are the totals in YSK's 2009–2019 statistics book (section 6.4), and "
    "invalid votes are the voters minus the valid votes. The book does not name the candidates; the winner's name "
    "comes from İhlas Haber Ajansı's report in Sabah."
)
COUNCIL_2024 = (
    "YSK's 2024 annual report prints this June 2, 2024 council rerun with each party's votes but not its seats. "
    "Invalid votes are the voters minus the valid votes."
)
PARTIAL_2019 = (
    "Anadolu Ajansı's election-night count lists only the leading candidates, so other parties' votes are missing."
)


def fetch() -> None:
    for url, suffix in (
        (FAALIYET, ".pdf"),
        (ANNEX_2019, ".pdf"),
        (AA_2024, ".html"),
        (AA_2019, ".html"),
        (BOOK, ".pdf"),
        (SABAH_2019, ".html"),
    ):
        raw.fetch(SOURCE, url, suffix=suffix)


def emit() -> Path:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FAALIYET).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    text = re.sub(r"^\s*(?:T \. C \..*|2 0 2 4 Y I L I.*)\|\s*\d+\s*$", "", text, flags=re.MULTILINE)
    found: dict[str, tuple[list[int], dict[str, int]]] = {}
    councils: dict[str, tuple[list[int], dict[str, int]]] = {}
    for place, office in [(place, office) for place in PLACES_2024 for office in ("Başkanlığı", "Meclis Üyeliği")]:
        match = re.search(
            re.escape(place) + r"[^\n]* Belediye " + office + r" Seçim Sonuçları\s+2 Haziran 2024\s+"
            r"Sandık Sayısı\s+[\d.]+\s+Kayıtlı Seçmen Sayısı\s+([\d.]+)\s+Oy Kullanan Seçmen Sayısı\s+([\d.]+)\s+"
            r"Katılım Oranı\s*\(%\)\s+[\d,]+\s+Geçerli Oy Sayısı\s+([\d.]+)\s+Geçerli Oyların Siyasi Partilere "
            r"Dağılımı(.*?)(?=\n\s*\S[^\n]*Seçim Sonuçları|\n2 HAZİRAN|\nBilgi Güvenliği)",
            text,
            re.DOTALL,
        )
        if match is None:
            if office != "Başkanlığı" and PLACES_2024[place][0] not in ANNULLED_COUNCILS:
                continue
            raise ValueError(f"no {office} table for {place}")
        registered, voters, valid = (int(match.group(i).replace(".", "")) for i in (1, 2, 3))
        votes: dict[str, int] = {}
        for label, count in re.findall(
            r"^\s*([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ .]+?)\s{2,}([\d.]+)\s+[\d,]+\s*$", match.group(4), re.MULTILINE
        ):
            votes[label.strip()] = int(count.replace(".", ""))
        if sum(votes.values()) != valid - SHORT_2024.get((PLACES_2024[place][0], office), 0):
            raise ValueError(f"{place} {office}: parties sum to {sum(votes.values())}, not {valid}")
        (found if office == "Başkanlığı" else councils)[PLACES_2024[place][0]] = (
            [registered, voters, valid, voters - valid],
            votes,
        )
    if set(councils) != ANNULLED_COUNCILS:
        raise ValueError(f"council reruns found: {sorted(councils)}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Turkey's reruns of annulled local races: mayors on June 2, 2019 and June 2, 2024, Suvarlı's mayor "
                "and council on August 4, 2019, and four municipal councils on June 2, 2024"
            ),
            polity="TR",
        )
        turkey = cs.polity("TR")
        official_2024 = cs.source(PUBLISHER, "2024 Yılı Faaliyet Raporu")
        official_2019 = cs.source(PUBLISHER, "2 Haziran 2019 Mahalli İdareler Yenileme Seçimi Sonuçları (Ek-2)")
        press = {
            RERUN_2019: cs.source("Anadolu Ajansı", "3 ilçe ve bir beldede yenileme seçimleri yapıldı"),
            RERUN_2024: cs.source(
                "Anadolu Ajansı", "YSK, 2 Haziran'da yapılan yenileme seçimlerinin kesin sonuçlarını açıkladı"
            ),
            SUVARLI: cs.source("Sabah", "Adıyaman'ın ilk kadın belediye başkanı seçildi"),
        }
        book = cs.source(PUBLISHER, "2009-2019 Seçim İstatistikleri Kitabı")
        for id, url, title_text, publisher, kind in (
            (official_2024, FAALIYET, "2024 Yılı Faaliyet Raporu", PUBLISHER, "official_body"),
            (
                official_2019,
                ANNEX_2019,
                "2 Haziran 2019 Mahalli İdareler Yenileme Seçimi Sonuçları (Ek-2)",
                PUBLISHER,
                "official_body",
            ),
            (
                press[RERUN_2019],
                AA_2019,
                "3 ilçe ve bir beldede yenileme seçimleri yapıldı",
                "Anadolu Ajansı",
                "news_agency",
            ),
            (
                press[RERUN_2024],
                AA_2024,
                "YSK, 2 Haziran'da yapılan yenileme seçimlerinin kesin sonuçlarını açıkladı",
                "Anadolu Ajansı",
                "news_agency",
            ),
            (book, BOOK, "2009-2019 Seçim İstatistikleri Kitabı", PUBLISHER, "official_body"),
            (press[SUVARLI], SABAH_2019, "Adıyaman'ın ilk kadın belediye başkanı seçildi", "Sabah", "news_agency"),
        ):
            cs.add(
                "source",
                id=id,
                kind=kind,
                publisher=publisher,
                title=title_text,
                url=url,
                license=LICENSE if kind == "official_body" else None,
            )
            cs.artifact(raw.cached(SOURCE, url), id)
        sources = {RERUN_2019: official_2019, RERUN_2024: official_2024, SUVARLI: book}

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        parties = {label: None if owner is None else cs.key("tr.party", owner) for label, owner in PARTIES.items()}
        party_names = dict(
            conn.execute(
                """select e.value, (select text from name n where n.entity_id = e.entity_id and n.kind = 'official'
                                    order by n.valid_from desc nulls last limit 1)
                   from external_id e where e.scheme = 'tr.party'"""
            ).fetchall()
        )
        districts = {
            (plate, text): id
            for plate, text, id in conn.execute(
                """select pc.code, n.text, a.id from area a
                   join area_parent ap on ap.area_id = a.id
                   join area p on p.id = ap.parent_id and p.scheme_code = 'tr.il'
                   join area_code pc on pc.area_id = p.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'tr.ilce'"""
            ).fetchall()
        }
        mayoral = conn.execute(
            "select electoral_system_id from contest c join external_id e on e.entity_id = c.id "
            "where e.scheme = 'tr.contest' and e.value = %s",
            ["2024-03-31:sanliurfa-hilvan-mayor"],
        ).fetchone()
        assert mayoral is not None
        cycle = {}
        for day, label in (
            (RERUN_2019, "2019 Turkish local election reruns"),
            (RERUN_2024, "2024 Turkish local election reruns"),
            (SUVARLI, "August 2019 Turkish local election rerun"),
        ):
            cycle[day] = cs.key("tr.election", f"{day}:local-rerun")
            cs.add("election", id=cycle[day], polity_id=turkey, kind="local", date_from=day, date_to=day, status="held")
            name(cycle[day], label, "en")
            cs.add("slug", path=f"tr/{day}-local-rerun", entity_id=cycle[day])
        everything: list[
            tuple[date, date, str, str, str | None, str | None, str | None, list[int | None], dict[str, int]]
        ] = []
        for key, (plate, district, belde, _, _, totals) in RACES_2019.items():
            everything.append(
                (RERUN_2019, MARCH_2019, key, plate, district, belde, None, totals or [None] * 4, VOTES_2019[key])
            )
        everything.append(
            (SUVARLI, MARCH_2019, SUVARLI_RACE[0], *SUVARLI_RACE[1:4], None, SUVARLI_MAYOR[0], SUVARLI_MAYOR[1])
        )
        for key, (totals, votes) in found.items():
            place = next(row for row in PLACES_2024.values() if row[0] == key)
            everything.append((RERUN_2024, MARCH_2024, key, place[1], place[2], place[3], None, totals, votes))
        units: dict[tuple[date, str], UUID] = {}
        places: dict[tuple[date, str], tuple[str, str, UUID, UUID]] = {}
        held = {(RERUN_2024, key) for key in councils} | {(SUVARLI, SUVARLI_RACE[0])}
        for day, march, key, plate, district, belde, _, totals, votes in everything:
            march_election = cs.key("tr.election", f"{march}:{key}-mayor")
            march_contest = cs.key("tr.contest", f"{march}:{key}-mayor")
            base = conn.execute(
                "select s.path, c.district_area_id, n.text from contest c "
                "join slug s on s.entity_id = c.election_id "
                "join name n on n.entity_id = c.id and n.lang = 'en' where c.id = %s",
                [march_contest],
            ).fetchone()
            if base is None:
                raise ValueError(f"no March contest for {key}")
            path = base[0].replace(f"/{march}-local", "")
            english = base[2].removeprefix("Mayor of ")
            office = cs.key("tr.office", f"{key}-mayor")
            polity = cs.key("tr.polity", key)
            election = cs.key("tr.election", f"{day}:{key}-mayor")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=cycle[day],
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
                rerun_of_id=march_election,
            )
            name(
                election,
                f"{day.year} {english} {'local' if (day, key) in held else 'mayoral'} election rerun",
                "en",
            )
            places[(day, key)] = (path, english, base[1], election)
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            contest_key = f"{day}:{key}-mayor"
            contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=base[1],
                magnitude=1,
                round_number=1,
                electoral_system_id=mayoral[0],
                default_result_kind="counted",
            )
            name(contest, f"Mayor of {english}", "en")
            cs.add("slug", path=f"{path}/{day}-local/mayor", entity_id=contest)
            if belde is None:
                unit = cs.unit("tr.ilce", f"{plate}:{district}")
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=turkey,
                        scheme_code="tr.ilce",
                        code=f"{plate}:{district}",
                        area_relation="equals",
                        area_id=districts[(plate, district)],
                        kind="aggregate",
                    )
                    name(unit, district)
            else:
                unit = cs.unit("tr.belde", f"{plate}:{district}:{belde}")
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=turkey,
                        scheme_code="tr.belde",
                        code=f"{plate}:{district}:{belde}",
                        area_relation="within",
                        area_id=districts[(plate, district)],
                        kind="aggregate",
                    )
                    name(unit, belde)
            units[(day, key)] = unit
            declared = cs.key(f"{SOURCE}.result_set", contest_key)
            partial = day == RERUN_2019 and key in ("kirikkale-keskin", "artvin-yusufeli")
            cs.add(
                "result_set",
                id=declared,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=press[day] if day == RERUN_2019 else sources[day],
                is_default=True,
                completeness=0 if partial else 1,
            )
            cs.add(
                "note",
                entity_id=declared,
                kind="methodology",
                body_md={RERUN_2019: OFFICIAL_2019, RERUN_2024: OFFICIAL_2024, SUVARLI: OFFICIAL_SUVARLI}[day],
            )
            if partial:
                cs.add("note", entity_id=declared, kind="caveat", body_md=PARTIAL_2019)
            winner_label = max(votes, key=lambda label: votes[label])
            winner_name = {RERUN_2024: WINNERS_2024, RERUN_2019: {k: v[4] for k, v in RACES_2019.items()}}.get(
                day, {SUVARLI_RACE[0]: SUVARLI_RACE[5]}
            )[key]
            options: dict[str, UUID] = {}
            for label in sorted(votes):
                owner = PARTIES[label]
                options[label] = cs.key("tr.option", f"{contest_key}:{owner or 'independents'}")
                cs.add(
                    "contest_option",
                    id=options[label],
                    contest_id=contest,
                    kind="independents" if owner is None else "candidate",
                    party_id=parties[label],
                    ballot_label=INDEPENDENTS if owner is None else party_names[owner],
                )
            person = cs.key("tr.ysk_local.person", f"{key}:{slug(winner_name)}")
            if person in cs.minted:
                cs.add("person", id=person)
                name(person, winner_name)
            owner = PARTIES[winner_label]
            candidacy = cs.key("tr.candidacy", f"{contest_key}:{owner}:{slug(winner_name)}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[winner_label],
                role="head",
                member_of_party_id=parties[winner_label],
                is_independent=False,
            )
            cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[winner_label], kind="nominated")
            cs.add("outcome", result_set_id=declared, option_id=options[winner_label], kind="elected")
            start = date(day.year, day.month, 1)
            tenure = cs.key(f"{SOURCE}.tenure", contest_key)
            cs.add(
                "tenure",
                id=tenure,
                person_id=person,
                office_id=office,
                from_date=start,
                from_precision="month",
                to_date=date(2024, 4, 1) if day.year == 2019 else None,
                to_precision="month",
                acquired_via="elected",
                contest_id=contest,
                ended_via="term_end" if day.year == 2019 else None,
            )
            cs.add(
                "tenure_affiliation",
                tenure_id=tenure,
                kind="party",
                party_id=parties[winner_label],
                valid_from=start,
                valid_from_precision="month",
            )
            cs.add("citation", entity_id=tenure, source_id=sources[day], locator={"contest": str(contest)})
            cs.add("citation", entity_id=tenure, source_id=press[day], locator={"contest": str(contest)})
            if key in WIKI:
                cs.add("citation", entity_id=tenure, source_id=WIKI_SOURCES[day.year], locator={"page": WIKI[key]})
            for measure, value in zip((REGISTERED, VOTERS, VALID, INVALID), totals, strict=True):
                if value is not None:
                    cs.add(
                        "result_value",
                        result_set_id=declared,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            for label, count in votes.items():
                cs.add(
                    "result_value",
                    result_set_id=declared,
                    unit_id=unit,
                    option_id=options[label],
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=count,
                )
        for day, races in (
            (RERUN_2024, councils),
            (SUVARLI, {SUVARLI_RACE[0]: SUVARLI_COUNCIL}),
        ):
            pooled = conn.execute(
                "select entity_id from external_id where scheme = 'tr.contest' and value = %s",
                [f"{day}:municipal-councils"],
            ).fetchone()
            if pooled is not None:
                cs.add(
                    "entity_retirement",
                    entity_id=pooled[0],
                    reason="Each council rerun now sits in its own town's rerun election, beside the mayoral rerun.",
                )
            for key, (totals, votes) in sorted(races.items()):
                path, english, district, election = places[(day, key)]
                contest_key = f"{day}:{key}-council"
                contest = cs.key("tr.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    district_area_id=district,
                    magnitude=None,
                    round_number=1,
                    default_result_kind="counted",
                )
                name(contest, f"{english} municipal council", "en")
                name(contest, f"{english} Belediye Meclisi Üyeliği", kind="native")
                cs.add("slug", path=f"{path}/{day}-local/municipal-council", entity_id=contest)
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[day],
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=declared,
                    kind="methodology",
                    body_md=COUNCIL_2024 if day == RERUN_2024 else OFFICIAL_SUVARLI,
                )
                if (key, "Meclis Üyeliği") in SHORT_2024:
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=f"The report's party rows add up to {SHORT_2024[(key, 'Meclis Üyeliği')]} votes fewer "
                        "than its valid votes; those votes are counted as valid but given to no party.",
                    )
                for label in sorted(votes):
                    owner = PARTIES[label]
                    option = cs.key("tr.option", f"{contest_key}:{owner or 'independents'}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="independents" if owner is None else "list",
                        party_id=parties[label],
                        ballot_label=INDEPENDENTS if owner is None else party_names[owner],
                    )
                    cs.add(
                        "result_value",
                        result_set_id=declared,
                        unit_id=units[(day, key)],
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=votes[label],
                    )
                for measure, value in zip((REGISTERED, VOTERS, VALID, INVALID), totals, strict=True):
                    cs.add(
                        "result_value",
                        result_set_id=declared,
                        unit_id=units[(day, key)],
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
