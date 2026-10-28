from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.sources.tr import ysk
from ea.store import connect

SOURCE = "tr.lineage"
VERSION = "4"
COURT = "https://anayasa.gov.tr/media/3617/tulensiyasipartilerinkapatilmasi.pdf"
COURT_PUBLISHER = "Anayasa Mahkemesi"
COURT_TITLE = "Son Üç Kararı Çerçevesinde Anayasa Mahkemesinin Siyasi Partilerin Kapatılmasına İlişkin İçtihadı"
COURT_NOTES = (
    "Presentation by Constitutional Court rapporteur Hikmet Tülen, dated December 2, 2010. Tables list all parties "
    "closed under the 1961 and 1982 constitutions, including docket numbers, decision numbers, decision dates, and "
    "the Resmî Gazete issue."
)
WIKIPEDIA_LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
WIKIPEDIA_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
PAGE = "https://{}.wikipedia.org/wiki/{}"
WIKITEXT = "https://{}.wikipedia.org/w/index.php?title={}&action=raw"
LIST = ("tr", "Türkiye'deki eski siyasi partiler listesi")
DBP_RENAMED = date(2014, 7, 11)
MINTED: dict[str, tuple[str, list[tuple[str, str | None, date | None, date | None]]]] = {
    "milli-nizam-partisi": ("Q834409", [("Millî Nizam Partisi", "MNP", None, None)]),
    "cumhuriyetci-parti": ("Q250316", [("Cumhuriyetçi Parti", "CP", None, None)]),
    "sosyal-demokrasi-partisi": ("Q1759655", [("Sosyal Demokrasi Partisi", "SODEP", None, None)]),
    "hur-demokrat-parti": ("Q3462668", [("Hür Demokrat Parti", None, None, None)]),
    "halkin-emek-partisi": ("Q3674950", [("Halkın Emek Partisi", "HEP", None, None)]),
    "demokrasi-partisi": ("Q3366313", [("Demokrasi Partisi", "DEP", None, None)]),
    "demokratik-toplum-partisi": ("Q1138606", [("Demokratik Toplum Partisi", "DTP", None, None)]),
    "sosyalist-demokrasi-partisi": ("Q7551546", [("Sosyalist Demokrasi Partisi", "SDP", None, None)]),
    "baris-ve-demokrasi-partisi": (
        "Q809719",
        [
            ("Barış ve Demokrasi Partisi", "BDP", None, DBP_RENAMED),
            ("Demokratik Bölgeler Partisi", "DBP", DBP_RENAMED, None),
        ],
    ),
}
MERGED = {
    "ozgurluk-ve-dayanisma-partisi": "sol-parti",
    "yeniden-dogus-partisi": "genc-parti",
    "halklarin-esitlik-ve-demokrasi-partisi": "yesiller-ve-sol-gelecek-partisi",
}
DEM = ("tr", "Halkların Eşitlik ve Demokrasi Partisi")
HEDEP = date(2023, 10, 15)
DEM_SHORT = date(2023, 12, 11)
NAMES = {
    "yesiller-ve-sol-gelecek-partisi": [
        ("Yeşiller ve Sol Gelecek Partisi", "official", date(2012, 11, 25), HEDEP),
        ("YSP", "abbreviation", date(2012, 11, 25), HEDEP),
        ("Halkların Eşitlik ve Demokrasi Partisi", "official", HEDEP, None),
        ("HEDEP", "abbreviation", HEDEP, DEM_SHORT),
        ("DEM Parti", "abbreviation", DEM_SHORT, None),
    ]
}
HDP_OPEN = (
    "HDP was not closed. Facing a closure case at the Constitutional Court, it handed its active political work to "
    "the Yeşiller ve Sol Gelecek Partisi (now DEM Parti) at its fourth congress on August 27, 2023, and it still exists "
    "with its own co-chairs."
)
RELATIONS: list[tuple[str, str, str | None, date, str, tuple[str, str] | str]] = [
    ("millet-partisi-1948", "closed_by_court", None, date(1954, 1, 27), "day", LIST),
    ("vatan-partisi-1954", "closed_by_court", None, date(1957, 1, 30), "day", LIST),
    ("demokrat-parti-1946", "closed_by_court", None, date(1960, 9, 29), "day", LIST),
    ("milli-nizam-partisi", "closed_by_court", None, date(1971, 5, 20), "day", COURT),
    ("turkiye-isci-partisi-1961", "closed_by_court", None, date(1971, 7, 20), "day", COURT),
    ("sosyalist-parti", "closed_by_court", None, date(1992, 7, 10), "day", COURT),
    ("halkin-emek-partisi", "closed_by_court", None, date(1993, 7, 14), "day", COURT),
    ("demokrasi-partisi", "closed_by_court", None, date(1994, 6, 16), "day", COURT),
    ("refah-partisi", "closed_by_court", None, date(1998, 1, 16), "day", COURT),
    ("fazilet-partisi", "closed_by_court", None, date(2001, 6, 22), "day", COURT),
    ("halkin-demokrasi-partisi", "closed_by_court", None, date(2003, 3, 13), "day", COURT),
    ("demokratik-toplum-partisi", "closed_by_court", None, date(2009, 12, 11), "day", COURT),
    (
        "cumhuriyetci-millet-partisi",
        "successor_of",
        "millet-partisi-1948",
        date(1954, 2, 10),
        "day",
        ("en", "Republican Nation Party"),
    ),
    (
        "adalet-partisi-1961",
        "successor_of",
        "demokrat-parti-1946",
        date(1961, 2, 11),
        "day",
        ("en", "Justice Party (Turkey)"),
    ),
    (
        "milli-selamet-partisi",
        "successor_of",
        "milli-nizam-partisi",
        date(1972, 10, 11),
        "day",
        ("en", "National Salvation Party"),
    ),
    ("demokrat-parti", "successor_of", "adalet-partisi-1961", date(1983, 6, 23), "day", ("en", "True Path Party")),
    (
        "milliyetci-hareket-partisi",
        "successor_of",
        "cumhuriyetci-koylu-millet-partisi",
        date(1983, 7, 7),
        "day",
        ("tr", "Milliyetçi Hareket Partisi"),
    ),
    ("refah-partisi", "successor_of", "milli-selamet-partisi", date(1983, 7, 19), "day", ("en", "Welfare Party")),
    (
        "hur-demokrat-parti",
        "successor_of",
        "milliyetci-demokrasi-partisi",
        date(1986, 5, 1),
        "month",
        ("tr", "Hür Demokrat Parti (Türkiye)"),
    ),
    ("isci-partisi", "successor_of", "sosyalist-parti", date(1992, 7, 10), "day", ("tr", "İşçi Partisi (Türkiye)")),
    ("demokrasi-partisi", "successor_of", "halkin-emek-partisi", date(1993, 7, 14), "day", ("tr", "Demokrasi Partisi")),
    (
        "halkin-demokrasi-partisi",
        "successor_of",
        "demokrasi-partisi",
        date(1994, 6, 16),
        "day",
        ("en", "People's Democracy Party"),
    ),
    ("fazilet-partisi", "successor_of", "refah-partisi", date(1998, 1, 16), "day", ("en", "Virtue Party")),
    ("saadet-partisi", "successor_of", "fazilet-partisi", date(2001, 7, 20), "day", ("tr", "Saadet Partisi")),
    (
        "demokratik-halk-partisi",
        "successor_of",
        "halkin-demokrasi-partisi",
        date(2003, 3, 13),
        "day",
        ("en", "Democratic People's Party (Turkey)"),
    ),
    (
        "demokratik-toplum-partisi",
        "successor_of",
        "demokratik-halk-partisi",
        date(2005, 11, 1),
        "month",
        ("tr", "Demokratik Toplum Partisi"),
    ),
    (
        "baris-ve-demokrasi-partisi",
        "successor_of",
        "demokratik-toplum-partisi",
        date(2009, 12, 11),
        "day",
        ("en", "Peace and Democracy Party"),
    ),
    ("halklarin-demokratik-partisi", "successor_of", "baris-ve-demokrasi-partisi", date(2014, 4, 28), "day", LIST),
    (
        "yesiller-ve-sol-gelecek-partisi",
        "successor_of",
        "halklarin-demokratik-partisi",
        date(2023, 8, 27),
        "day",
        ("tr", "Halkların Demokratik Partisi"),
    ),
    (
        "cumhuriyetci-millet-partisi",
        "merged_into",
        "cumhuriyetci-koylu-millet-partisi",
        date(1958, 10, 16),
        "day",
        LIST,
    ),
    ("turkiye-koylu-partisi", "merged_into", "cumhuriyetci-koylu-millet-partisi", date(1958, 10, 16), "day", LIST),
    ("guven-partisi", "merged_into", "cumhuriyetci-guven-partisi", date(1973, 3, 1), "month", LIST),
    ("cumhuriyetci-parti", "merged_into", "cumhuriyetci-guven-partisi", date(1973, 3, 1), "month", LIST),
    ("halkci-parti", "merged_into", "sosyaldemokrat-halkci-parti", date(1985, 11, 3), "day", LIST),
    ("sosyal-demokrasi-partisi", "merged_into", "sosyaldemokrat-halkci-parti", date(1985, 11, 3), "day", LIST),
    ("hurriyet-partisi", "absorbed_by", "cumhuriyet-halk-partisi", date(1958, 11, 24), "day", LIST),
    ("hur-demokrat-parti", "absorbed_by", "anavatan-partisi-1983", date(1986, 11, 30), "day", LIST),
    ("sosyaldemokrat-halkci-parti", "absorbed_by", "cumhuriyet-halk-partisi", date(1995, 2, 18), "day", LIST),
    ("yeni-demokrasi-hareketi", "absorbed_by", "baris-partisi", date(1997, 11, 1), "day", LIST),
    ("yeni-parti", "absorbed_by", "demokrat-parti-1992", date(1997, 11, 21), "day", LIST),
    ("demokrasi-ve-baris-partisi", "absorbed_by", "hak-ve-ozgurlukler-partisi", date(2002, 3, 2), "day", LIST),
    ("yeni-turkiye-partisi", "absorbed_by", "cumhuriyet-halk-partisi", date(2004, 10, 24), "day", LIST),
    ("demokrat-parti-1992", "absorbed_by", "anavatan-partisi-1983", date(2005, 5, 8), "day", LIST),
    ("anavatan-partisi-1983", "absorbed_by", "demokrat-parti", date(2009, 10, 31), "day", LIST),
    ("aydinlik-turkiye-partisi", "absorbed_by", "milliyetci-hareket-partisi", date(2010, 4, 11), "day", LIST),
    ("halkin-sesi-partisi", "absorbed_by", "adalet-ve-kalkinma-partisi", date(2012, 9, 19), "day", LIST),
    ("memleket-partisi", "absorbed_by", "cumhuriyet-halk-partisi", date(2025, 7, 22), "day", LIST),
    (
        "millet-partisi-1962",
        "split_from",
        "cumhuriyetci-koylu-millet-partisi",
        date(1962, 6, 1),
        "month",
        ("en", "Nation Party (Turkey, 1962)"),
    ),
    (
        "demokratik-parti",
        "split_from",
        "adalet-partisi-1961",
        date(1970, 12, 18),
        "day",
        ("en", "Democratic Party (Turkey, 1970)"),
    ),
    (
        "buyuk-birlik-partisi",
        "split_from",
        "milliyetci-hareket-partisi",
        date(1993, 1, 29),
        "day",
        ("en", "Great Unity Party"),
    ),
    (
        "yeni-parti",
        "split_from",
        "anavatan-partisi-1983",
        date(1993, 10, 7),
        "day",
        ("en", "New Party (Turkey, 1993)"),
    ),
    (
        "demokrat-turkiye-partisi",
        "split_from",
        "demokrat-parti",
        date(1997, 1, 7),
        "day",
        ("en", "Democrat Turkey Party"),
    ),
    (
        "aydinlik-turkiye-partisi",
        "split_from",
        "milliyetci-hareket-partisi",
        date(1998, 1, 1),
        "year",
        ("en", "Bright Turkey Party"),
    ),
    (
        "adalet-ve-kalkinma-partisi",
        "split_from",
        "fazilet-partisi",
        date(2001, 8, 14),
        "day",
        ("en", "Justice and Development Party"),
    ),
    (
        "yurt-partisi",
        "split_from",
        "anavatan-partisi-1983",
        date(2002, 1, 1),
        "year",
        ("en", "Homeland Party (Turkey)"),
    ),
    (
        "yeni-turkiye-partisi",
        "split_from",
        "demokratik-sol-parti",
        date(2002, 7, 22),
        "day",
        ("en", "New Turkey Party (2002)"),
    ),
    ("dogru-yol-partisi", "split_from", "demokrat-parti", date(2007, 5, 28), "day", ("en", "True Path Party (2007)")),
    ("halkin-sesi-partisi", "split_from", "saadet-partisi", date(2010, 11, 1), "day", ("en", "People's Voice Party")),
    (
        "anavatan-partisi",
        "split_from",
        "demokrat-parti",
        date(2011, 9, 1),
        "month",
        ("en", "Motherland Party (Turkey, 2011)"),
    ),
    (
        "komunist-parti",
        "split_from",
        "turkiye-komunist-partisi",
        date(2014, 7, 17),
        "day",
        ("en", "Communist Party (Turkey, 2014)"),
    ),
    ("anadolu-partisi", "split_from", "cumhuriyet-halk-partisi", date(2014, 11, 14), "day", ("en", "Anatolia Party")),
    ("iyi-parti", "split_from", "milliyetci-hareket-partisi", date(2017, 10, 25), "day", ("en", "Good Party")),
    ("yeniden-refah-partisi", "split_from", "saadet-partisi", date(2018, 11, 23), "day", ("en", "New Welfare Party")),
    (
        "yenilik-partisi",
        "split_from",
        "cumhuriyet-halk-partisi",
        date(2020, 7, 20),
        "day",
        ("en", "Innovation Party (Turkey)"),
    ),
    ("memleket-partisi", "split_from", "cumhuriyet-halk-partisi", date(2021, 5, 17), "day", ("tr", "Memleket Partisi")),
    ("zafer-partisi", "split_from", "iyi-parti", date(2021, 8, 26), "day", ("en", "Victory Party (Turkey)")),
    (
        "milli-yol-partisi",
        "split_from",
        "buyuk-birlik-partisi",
        date(2021, 12, 14),
        "day",
        ("en", "National Path Party"),
    ),
]
VEHICLES = [
    ("demokratik-halk-partisi", "halkin-demokrasi-partisi", date(2002, 11, 3), ("tr", "2002 Türkiye genel seçimleri")),
]
JOINT_LISTS = [
    (date(1991, 10, 20), "refah-partisi", ("milliyetci-hareket-partisi", "millet-partisi")),
    (date(1991, 10, 20), "sosyaldemokrat-halkci-parti", ("halkin-emek-partisi",)),
    (date(2002, 11, 3), "dogru-yol-partisi", ("demokrat-turkiye-partisi", "aydinlik-turkiye-partisi")),
    (
        date(2002, 11, 3),
        "demokratik-halk-partisi",
        ("halkin-demokrasi-partisi", "emek-partisi", "sosyalist-demokrasi-partisi"),
    ),
    (date(2018, 6, 24), "adalet-ve-kalkinma-partisi", ("buyuk-birlik-partisi",)),
    (date(2018, 6, 24), "cumhuriyet-halk-partisi", ("saadet-partisi",)),
    (date(2018, 6, 24), "iyi-parti", ("demokrat-parti",)),
    (date(2018, 6, 24), "halklarin-demokratik-partisi", ("turkiye-isci-partisi",)),
    (date(2023, 5, 14), "adalet-ve-kalkinma-partisi", ("demokratik-sol-parti", "hur-dava-partisi")),
    (
        date(2023, 5, 14),
        "cumhuriyet-halk-partisi",
        ("iyi-parti", "demokrasi-ve-atilim-partisi", "demokrat-parti", "gelecek-partisi", "saadet-partisi"),
    ),
    (
        date(2023, 5, 14),
        "yesiller-ve-sol-gelecek-partisi",
        ("baris-ve-demokrasi-partisi", "halklarin-demokratik-partisi", "emek-partisi"),
    ),
]
ARTICLES = sorted(
    {cite for *_, cite in RELATIONS if isinstance(cite, tuple)}
    | {cite for *_, cite in VEHICLES}
    | {LIST, ("en", "Left Party (Turkey)"), ("tr", "Genç Parti"), DEM}
    | {("tr", "2018 Türkiye genel seçimleri"), ("tr", "2023 Türkiye genel seçimleri")}
)


def fetch() -> None:
    for host, title in ARTICLES:
        raw.fetch(SOURCE, WIKITEXT.format(host, quote(title.replace(" ", "_"))), suffix=".wiki")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Turkish party lineage from 1946 to 2025: court closures, mergers, parties that joined another, "
                "successors, and splits within a tradition, with the renames of ÖDP to Sol Parti and Yeniden Doğuş "
                "Partisi to Genç Parti merged into one party each, YSP renamed HEDEP and then DEM Parti, and the joint "
                "lists of 1991, 2002, 2018, and 2023 that carried other parties' candidates"
            ),
            polity="TR",
            kind="edit",
        )
        turkey = cs.polity("TR")

        sources: dict[tuple[str, str] | str, UUID] = {}
        sources[COURT] = cs.source(COURT_PUBLISHER, COURT_TITLE)
        cs.add(
            "source",
            id=sources[COURT],
            kind="official_body",
            publisher=COURT_PUBLISHER,
            title=COURT_TITLE,
            url=COURT,
            license=ysk.LICENSE,
            notes=COURT_NOTES,
        )
        for host, title in ARTICLES:
            sources[host, title] = cs.source("Wikipedia", title)
            cs.add(
                "source",
                id=sources[host, title],
                kind="community",
                publisher="Wikipedia",
                title=title,
                url=PAGE.format(host, quote(title.replace(" ", "_"))),
                license=WIKIPEDIA_LICENSE,
                license_url=WIKIPEDIA_LICENSE_URL,
            )
            cs.artifact(raw.cached(SOURCE, WIKITEXT.format(host, quote(title.replace(" ", "_")))), sources[host, title])

        parties: dict[str, UUID] = {}
        for key, (item, versions) in MINTED.items():
            parties[key] = cs.key("tr.party", key)
            if parties[key] not in cs.minted:
                continue
            cs.add("party", id=parties[key], kind="party", polity_id=turkey)
            cs.add("external_id", entity_id=parties[key], scheme="wikidata", value=item)
            for full, short, start, end in versions:
                for text, kind in ((full, "official"), (short, "abbreviation")):
                    if text is None:
                        continue
                    cs.add(
                        "name",
                        entity_id=parties[key],
                        text=text,
                        lang="tr",
                        kind=kind,
                        is_preferred=kind == "official" and end is None,
                        valid_from=start,
                        valid_from_precision="day",
                        valid_to=end,
                        valid_to_precision="day",
                    )
        known: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'tr.party'").fetchall()
        )
        for key in (
            {key for relation in RELATIONS for key in (relation[0], relation[2]) if key is not None}
            | {key for vehicle in VEHICLES for key in vehicle[:2]}
            | {key for _, host, guests in JOINT_LISTS for key in (host, *guests)}
            | set(NAMES)
        ):
            if key not in parties:
                parties[key] = known[key]

        for duplicate, survivor in MERGED.items():
            if duplicate in known and known[duplicate] != known[survivor]:
                cs.add("entity_merge", entity_id=known[duplicate], into_id=known[survivor])

        for key, versions in NAMES.items():
            for text, kind, start, end in versions:
                cs.add(
                    "name",
                    entity_id=parties[key],
                    text=text,
                    lang="tr",
                    kind=kind,
                    is_preferred=kind == "official" and end is None,
                    valid_from=start,
                    valid_from_precision="day",
                    valid_to=end,
                    valid_to_precision="day",
                )
                cs.add("citation", entity_id=parties[key], source_id=sources[DEM], locator={"name": text})
            for name_id, text in conn.execute(
                """select id, text from name where entity_id = %s and lang = 'tr'
                   and kind in ('official', 'abbreviation') and valid_from is null and valid_to is null""",
                [parties[key]],
            ).fetchall():
                cs.add(
                    "name_drop",
                    name_id=name_id,
                    entity_id=parties[key],
                    text=text,
                    reason="Replaced by the party's dated names: YSP from 2012, HEDEP from October 2023, DEM Parti from December 2023.",
                )
        cs.add("note", entity_id=parties["halklarin-demokratik-partisi"], kind="context", body_md=HDP_OPEN)
        for origin, kind, target, day, precision, cite in RELATIONS:
            cs.add(
                "party_relation",
                from_party_id=parties[origin],
                to_party_id=None if target is None else parties[target],
                kind=kind,
                date=day,
                date_precision=precision,
                source_id=sources[cite],
            )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'tr.election'").fetchall()
        )
        for vehicle, party, day, cite in VEHICLES:
            cs.add(
                "party_relation",
                from_party_id=parties[vehicle],
                to_party_id=parties[party],
                kind="list_vehicle_for",
                date=None,
                date_precision="day",
                election_id=elections[str(day)],
                source_id=sources[cite],
            )
        for day, host, guests in JOINT_LISTS:
            alliance = cs.key("tr.alliance", f"{day}:joint-list:{host}")
            cs.add("alliance", id=alliance, kind="joint_list", election_id=elections[str(day)])
            cs.add("alliance_member", alliance_id=alliance, party_id=parties[host], role="list")
            for guest in guests:
                cs.add("alliance_member", alliance_id=alliance, party_id=parties[guest], role="guest")
        return cs.write()
