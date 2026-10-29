from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources import thea
from ea.sources.tr.ysk import FORMER, INDEPENDENTS, PROVINCES, REGISTERED, RENAMED, VALID, VOTERS, VOTES
from ea.store import connect

SOURCE = "tr.thea"
VERSION = "4"
TURKEY = 792
WORKBOOK = f"{thea.UPLOADS}/2025/10/clea_lc_20251015-L-Z.xlsx"
DAYS = {
    1950: date(1950, 5, 14),
    1954: date(1954, 5, 2),
    1957: date(1957, 10, 27),
    1961: date(1961, 10, 15),
    1965: date(1965, 10, 10),
    1969: date(1969, 10, 12),
    1973: date(1973, 10, 14),
    1977: date(1977, 6, 5),
    1983: date(1983, 11, 6),
    1987: date(1987, 11, 29),
    1991: date(1991, 10, 20),
    1995: date(1995, 12, 24),
    1999: date(1999, 4, 18),
    2002: date(2002, 11, 3),
}
SEATS = {
    1950: 487,
    1954: 541,
    1957: 610,
    1961: 450,
    1965: 450,
    1969: 450,
    1973: 450,
    1977: 450,
    1983: 400,
    1987: 450,
    1991: 450,
    1995: 550,
    1999: 550,
    2002: 550,
}
WON = 16
MONTHS = {1987: 10}
UNFILLED = {1983: 1}
SIVAS, SIRNAK = 58, 73
SWAPPED = {1991, 1995, 1999, 2002}
FIXED = {(1987, 11, 37): 0.0, (1987, 11, 43): 1.0, (1987, 24, 43): 1.0}
SIZE_RAISED = date(1995, 7, 1)
COUP_1960 = date(1960, 5, 27)
COUP_1980 = date(1980, 9, 12)
MARAS_RENAMED = date(1973, 2, 7)
PLATES = {slug(name): plate for plate, name in enumerate(PROVINCES, 1)} | {
    "afyon": 3,
    "k-maras": 46,
    "icel": 33,
    "mersin-icel": 33,
}
FORMER_NAMES = {3: "Afyon", 33: "İçel", 63: "Urfa"}
CKMP_RENAMED = date(1969, 2, 8)
BP_RENAMED = date(1971, 11, 27)
MGP_RENAMED = date(1971, 1, 29)
PARTIES = {
    "demokrat-parti-1946": [("Demokrat Parti", "DP", None, None)],
    "millet-partisi-1948": [("Millet Partisi", "MP", None, None)],
    "turkiye-koylu-partisi": [("Türkiye Köylü Partisi", "TKP", None, None)],
    "cumhuriyetci-millet-partisi": [("Cumhuriyetçi Millet Partisi", "CMP", None, None)],
    "hurriyet-partisi": [("Hürriyet Partisi", "HP", None, None)],
    "vatan-partisi-1954": [("Vatan Partisi", "VP", None, None)],
    "adalet-partisi-1961": [("Adalet Partisi", "AP", None, None)],
    "yeni-turkiye-partisi-1961": [("Yeni Türkiye Partisi", "YTP", None, None)],
    "cumhuriyetci-koylu-millet-partisi": [
        ("Cumhuriyetçi Köylü Millet Partisi", "CKMP", None, CKMP_RENAMED),
        ("Milliyetçi Hareket Partisi", "MHP", CKMP_RENAMED, None),
    ],
    "millet-partisi-1962": [("Millet Partisi", "MP", None, None)],
    "turkiye-isci-partisi-1961": [("Türkiye İşçi Partisi", "TİP", None, None)],
    "turkiye-birlik-partisi": [
        ("Birlik Partisi", "BP", None, BP_RENAMED),
        ("Türkiye Birlik Partisi", "TBP", BP_RENAMED, None),
    ],
    "guven-partisi": [
        ("Güven Partisi", "GP", None, MGP_RENAMED),
        ("Millî Güven Partisi", "MGP", MGP_RENAMED, None),
    ],
    "cumhuriyetci-guven-partisi": [("Cumhuriyetçi Güven Partisi", "CGP", None, None)],
    "demokratik-parti": [("Demokratik Parti", "DP", None, None)],
    "milli-selamet-partisi": [("Milli Selamet Partisi", "MSP", None, None)],
    "cumhuriyet-halk-partisi": [("Cumhuriyet Halk Partisi", "CHP", None, None)],
    "anavatan-partisi-1983": [("Anavatan Partisi", "ANAP", None, None)],
    "halkci-parti": [("Halkçı Parti", "HP", None, None)],
    "milliyetci-demokrasi-partisi": [("Milliyetçi Demokrasi Partisi", "MDP", None, None)],
    "sosyaldemokrat-halkci-parti": [("Sosyaldemokrat Halkçı Parti", "SHP", None, None)],
    "refah-partisi": [("Refah Partisi", "RP", None, None)],
    "sosyalist-parti": [("Sosyalist Parti", "SP", None, None)],
    "halkin-demokrasi-partisi": [("Halkın Demokrasi Partisi", "HADEP", None, None)],
    "yeni-demokrasi-hareketi": [("Yeni Demokrasi Hareketi", "YDH", None, None)],
    "yeni-parti": [("Yeni Parti", "Yeni Parti", None, None)],
    "fazilet-partisi": [("Fazilet Partisi", "FP", None, None)],
    "demokrat-turkiye-partisi": [("Demokrat Türkiye Partisi", "DTP", None, None)],
    "demokrat-parti-1992": [("Demokrat Parti", "DP", None, None)],
    "baris-partisi": [("Barış Partisi", "BP", None, None)],
    "degisen-turkiye-partisi": [("Değişen Türkiye Partisi", "DEPAR", None, None)],
    "demokrasi-ve-baris-partisi": [("Demokrasi ve Barış Partisi", "DBP", None, None)],
    "demokratik-halk-partisi": [("Demokratik Halk Partisi", "DEHAP", None, None)],
    "yeni-turkiye-partisi": [("Yeni Türkiye Partisi", "YTP", None, None)],
    "demokratik-sol-parti": [("Demokratik Sol Parti", "DSP", None, None)],
    "buyuk-birlik-partisi": [("Büyük Birlik Partisi", "BBP", None, None)],
    "sol-parti": [
        ("Özgürlük ve Dayanışma Partisi", "ÖDP", None, RENAMED["Özgürlük ve Dayanışma Partisi"][1]),
        ("Sol Parti", "SOL Parti", RENAMED["Özgürlük ve Dayanışma Partisi"][1], None),
    ],
    "liberal-demokrat-parti": [("Liberal Demokrat Parti", "LDP", None, None)],
    "emek-partisi": [
        ("Emeğin Partisi", "EMEP", None, FORMER["Emek Partisi"][2]),
        ("Emek Partisi", "EMEP", FORMER["Emek Partisi"][2], None),
    ],
    "isci-partisi": [("İşçi Partisi", "İP", None, None)],
    "adalet-ve-kalkinma-partisi": [("Adalet ve Kalkınma Partisi", "AK Parti", None, None)],
    "genc-parti": [
        ("Yeniden Doğuş Partisi", "YDP", None, FORMER["Genç Parti"][2]),
        ("Genç Parti", "Genç Parti", FORMER["Genç Parti"][2], None),
    ],
    "saadet-partisi": [("Saadet Partisi", "Saadet", None, None)],
    "yurt-partisi": [("Yurt Partisi", "YURT-P", None, None)],
    "bagimsiz-turkiye-partisi": [("Bağımsız Türkiye Partisi", "BTP", None, None)],
    "demokrat-parti": [
        ("Doğru Yol Partisi", "DYP", None, FORMER["Demokrat Parti"][2]),
        ("Demokrat Parti", "DP", FORMER["Demokrat Parti"][2], None),
    ],
    "milliyetci-hareket-partisi": [
        ("Milliyetçi Çalışma Partisi", "MÇP", None, FORMER["Milliyetçi Hareket Partisi"][2]),
        ("Milliyetçi Hareket Partisi", "MHP", FORMER["Milliyetçi Hareket Partisi"][2], None),
    ],
    "millet-partisi": [
        ("Islahatçı Demokrasi Partisi", "IDP", None, FORMER["Millet Partisi"][2]),
        ("Millet Partisi", "MP", FORMER["Millet Partisi"][2], None),
    ],
    "turkiye-komunist-partisi": [
        ("Sosyalist İktidar Partisi", "SİP", None, FORMER["Türkiye Komünist Partisi"][2]),
        ("Türkiye Komünist Partisi", "TKP", FORMER["Türkiye Komünist Partisi"][2], None),
    ],
}
CODES = {
    5: "demokrat-parti-1946",
    7: "demokratik-parti",
    12: "hurriyet-partisi",
    15: "vatan-partisi-1954",
    17: "adalet-partisi-1961",
    21: "millet-partisi-1962",
    22: "milli-selamet-partisi",
    25: "cumhuriyetci-koylu-millet-partisi",
    29: "yeni-turkiye-partisi-1961",
    31: "turkiye-koylu-partisi",
    38: "cumhuriyetci-millet-partisi",
    39: "cumhuriyetci-koylu-millet-partisi",
    40: "cumhuriyet-halk-partisi",
    41: "cumhuriyetci-guven-partisi",
    47: "turkiye-birlik-partisi",
    48: "guven-partisi",
}
CODES_1983 = {
    2: "degisen-turkiye-partisi",
    3: "turkiye-komunist-partisi",
    4: "demokrasi-ve-baris-partisi",
    5: "demokrat-parti-1992",
    6: "demokratik-sol-parti",
    8: "demokratik-halk-partisi",
    9: "demokrat-turkiye-partisi",
    10: "saadet-partisi",
    11: "sol-parti",
    13: "bagimsiz-turkiye-partisi",
    14: "buyuk-birlik-partisi",
    16: "adalet-ve-kalkinma-partisi",
    18: "emek-partisi",
    19: "liberal-demokrat-parti",
    20: "anavatan-partisi-1983",
    21: "millet-partisi",
    24: "milliyetci-demokrasi-partisi",
    25: "milliyetci-hareket-partisi",
    26: "milliyetci-hareket-partisi",
    27: "yeni-demokrasi-hareketi",
    28: "yeni-parti",
    29: "yeni-turkiye-partisi",
    30: "baris-partisi",
    33: "halkin-demokrasi-partisi",
    34: "halkci-parti",
    36: "genc-parti",
    37: "millet-partisi",
    40: "cumhuriyet-halk-partisi",
    43: "sosyaldemokrat-halkci-parti",
    44: "sosyalist-parti",
    45: "turkiye-komunist-partisi",
    46: "demokrat-parti",
    50: "fazilet-partisi",
    51: "refah-partisi",
    52: "isci-partisi",
    54: "genc-parti",
    55: "yurt-partisi",
}
OVERRIDES = {(1950, 21): "millet-partisi-1948", (1965, 47): "turkiye-isci-partisi-1961"}
COMBINED = {
    1969: "Türkiye İşçi Partisi ve Birlik Partisi",
    1977: "Türkiye Birlik Partisi ve Türkiye İşçi Partisi",
}
BLOCK_METHODOLOGY = (
    "Each province elected all its deputies at large by plurality from open party lists. Voters could strike names "
    "from a list and write in candidates from other lists, so a province's seats could split between parties."
)
QUOTA_METHODOLOGY = (
    "Seats in each province went by D'Hondt among the lists whose votes reached the provincial quota, the province's "
    "valid votes divided by its seats. In Bitlis, Hakkari, and Tunceli no list reached the quota, and seats went by "
    "D'Hondt among all lists."
)
REMAINDER_METHODOLOGY = (
    "Each province first gave a list one seat for every full provincial quota, the province's valid votes divided by "
    "its seats. The votes left over and the seats left unfilled were pooled nationally, allocated in a second count, "
    "and assigned back to provinces. Law 533 set the Hagenbach-Bischoff quota for two-seat provinces, and the "
    "independents' votes counted toward the quota without winning a pooled seat; the rules engine models both, so "
    "its seats match the declared ones except for THEA's İstanbul artifact."
)
BLOCK_CAVEAT = (
    "THEA gives one vote figure per party list. Voters could mix candidates from several lists, so in some provinces "
    "these figures add up to more than the votes cast. The archive stores no valid-vote count for this election."
)
LABEL_1965_CAVEAT = (
    "THEA gives the Workers' Party of Turkey (TİP) the code and name of the Unity Party, which was founded in 1966. "
    "The rows add up to TİP's published national total of 276,101 votes, so the archive shows them as TİP's."
)
ISTANBUL_1965_CAVEAT = (
    "THEA credits one of İstanbul's seats to independents, although independents won no seats in 1965 by published "
    "accounts, and gives TİP 14 seats nationally against a published 15."
)
DOUBLE_METHODOLOGY = (
    "Only parties that won at least 10 percent of the valid votes nationally could win seats. In each district, seats "
    "were allocated by D'Hondt among party lists that reached the district quota (the district's valid votes divided "
    "by its seats). Provinces with many seats were divided into several districts."
)
KONTENJAN_METHODOLOGY = (
    "Only parties that won at least 10 percent of the valid votes nationally could win seats, and party lists also had "
    "to reach the district quota (the district's valid votes divided by its seats). In districts with a kontenjan "
    "seat, the list with the most votes won the seat regardless of the quota; remaining seats were allocated by "
    "D'Hondt. Provinces with many seats were divided into several districts."
)
PROVINCIAL_METHODOLOGY = (
    "Seats in each district were allocated by D'Hondt among independent candidates and the lists of parties that won "
    "at least 10 percent of the valid votes nationally. Each province was one district, except that provinces with 19 "
    "to 35 seats were divided into two districts and those with 36 or more into three."
)
PROVINCES_CAVEAT = (
    "THEA reports results by province, but some provinces were divided into several electoral districts. The seats "
    "shown for those provinces are the sum of the seats won in each of their districts."
)
UNFILLED_CAVEAT = "399 deputies were elected to 400 seats; Bingöl elected two of its three seats."
FIXED_CAVEAT = (
    "THEA gives Bilecik's second seat to the Reformist Democracy Party (903 votes) and leaves one of Erzincan's three "
    "seats unfilled. This archive assigns both seats to the Social Democratic Populist Party (SHP), matching its "
    "published total of 99 seats and the deputies elected in both provinces."
)
SWAPPED_CAVEAT = (
    "THEA swapped the seats won in Sivas and Şırnak, as the provinces' votes and seat counts show. This archive gives "
    "each province its own seats."
)
JOINT_LISTS_1991 = (
    "The Welfare Party's lists carried candidates of the Nationalist Work Party and the Reformist Democracy Party; the "
    "Social Democratic Populist Party's lists carried candidates of the People's Labor Party (HEP)."
)
JOINT_LISTS_2002 = (
    "The True Path Party's lists carried candidates of the Democrat Turkey Party and the Bright Turkey Party; the "
    "Democratic People's Party's (DEHAP) lists carried candidates of the People's Democracy Party (HADEP), the Labor "
    "Party (EMEP), and the Socialist Democracy Party (SDP)."
)
COMBINED_CAVEAT = (
    "THEA gives the Workers' Party of Turkey (TİP) and the Unity Party the same code in this election, so where both "
    "ran their rows cannot be told apart. Each province shows the two parties' votes and seats together on one line."
)


def emit() -> Path:
    rows = pl.read_parquet(thea.PARSED / "lower.parquet").filter(
        (pl.col("ctr") == TURKEY) & pl.col("yr").is_in(list(DAYS))
    )
    rows = rows.with_columns(plate=pl.col("cst_n").map_elements(lambda name: PLATES[slug(name)], pl.Int64))
    pair = pl.col("yr").is_in(list(SWAPPED)) & pl.col("plate").is_in([SIVAS, SIRNAK])
    swapped = rows.filter(pair).select("yr", "pty", plate=SIVAS + SIRNAK - pl.col("plate"), swapped="seat")
    corrected = rows.join(swapped, on=["yr", "plate", "pty"], how="left").with_columns(
        seat=pl.when(pair).then(pl.col("swapped").fill_null(0.0)).otherwise("seat")
    )
    if corrected.height != rows.height:
        raise ValueError("Sivas or Şırnak repeats a party code")
    rows = corrected.drop("swapped")
    for (year, plate, code), seats in FIXED.items():
        rows = rows.with_columns(
            seat=pl.when((pl.col("yr") == year) & (pl.col("plate") == plate) & (pl.col("pty") == code))
            .then(seats)
            .otherwise("seat")
        )
    for year, day in DAYS.items():
        held = rows.filter(pl.col("yr") == year)
        elected = SEATS[year] - UNFILLED.get(year, 0)
        if set(held["mn"]) != {MONTHS.get(year, day.month)} or held["seat"].sum() != elected:
            raise ValueError(f"{year}: THEA's month or seats differ from {day} and {elected}")
        if held.group_by("plate").agg(pl.col("cst").n_unique()).filter(pl.col("cst") > 1).height:
            raise ValueError(f"{year}: a province has several constituencies")
        unknown = {code for code in held["pty"] if code < 6000} - set(CODES if year < 1983 else CODES_1983)
        if unknown:
            raise ValueError(f"{year}: unknown party codes {unknown}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkish parliamentary elections from 1950 to 2002 by province, from THEA's lower-chamber archive",
            polity="TR",
        )

        def name(
            entity: UUID,
            text: str,
            lang: str = "tr",
            kind: str = "official",
            start: date | None = None,
            end: date | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" and end is None,
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision="day",
            )

        source = cs.source(thea.PUBLISHER, thea.TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=thea.PUBLISHER,
            title=thea.TITLE,
            url=thea.LANDING,
            license=thea.LICENSE,
            license_url=thea.LICENSE_URL,
            attribution=thea.ATTRIBUTION,
            notes=thea.NOTES,
        )
        cs.artifact(raw.cached(thea.SOURCE, WORKBOOK), source)
        turkey = cs.polity("TR")
        nation = cs.area("tr.nation", "TR")
        provinces = {plate: cs.area("tr.il", f"{plate:02}") for plate in range(1, len(PROVINCES) + 1)}
        if turkey in cs.minted or nation in cs.minted or cs.minted & set(provinces.values()):
            raise ValueError("publish tr.ysk before tr.thea")

        units: dict[int, UUID] = {}
        for plate in sorted(set(rows.filter(pl.col("yr") < 1983)["plate"])):
            units[plate] = cs.unit("tr.secim_cevresi", f"{plate:02}")
            if units[plate] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[plate],
                    polity_id=turkey,
                    scheme_code="tr.secim_cevresi",
                    code=f"{plate:02}",
                    parent_id=None,
                    area_relation="equals",
                    area_id=provinces[plate],
                    kind="aggregate",
                )
                name(units[plate], PROVINCES[plate - 1])

        parties: dict[str, UUID] = {}
        for key, versions in PARTIES.items():
            parties[key] = cs.key("tr.party", key)
            if parties[key] not in cs.minted:
                continue
            cs.add("party", id=parties[key], kind="party", polity_id=turkey)
            for full, short, start, end in versions:
                name(parties[key], full, start=start, end=end)
                name(parties[key], short, kind="abbreviation", start=start, end=end)

        assembly = cs.key("tr.body", "tbmm")
        if assembly in cs.minted:
            raise ValueError("publish tr.ysk before tr.thea")
        for seats, start, end, precision in (
            (487, DAYS[1950], DAYS[1954], "day"),
            (541, DAYS[1954], DAYS[1957], "day"),
            (610, DAYS[1957], COUP_1960, "day"),
            (400, DAYS[1983], DAYS[1987], "day"),
            (450, DAYS[1987], SIZE_RAISED, "month"),
        ):
            cs.add(
                "body_size",
                body_id=assembly,
                seats=seats,
                valid_from=start,
                valid_from_precision="day",
                valid_to=end,
                valid_to_precision=precision,
            )
        name(assembly, "National Assembly", "en", start=DAYS[1961], end=COUP_1980)
        name(assembly, "Millet Meclisi", kind="native", start=DAYS[1961], end=COUP_1980)
        cs.add(
            "body_size",
            body_id=assembly,
            seats=450,
            valid_from=DAYS[1961],
            valid_from_precision="day",
            valid_to=COUP_1980,
            valid_to_precision="day",
        )
        for (lower,) in conn.execute(
            "select entity_id from external_id where scheme = 'tr.body' and value = 'millet-meclisi'"
        ).fetchall():
            cs.add(
                "entity_retirement", entity_id=lower, reason="the Millet Meclisi is the TBMM's lower chamber, 1961–1980"
            )

        systems: dict[str, UUID] = {}
        lists = {"order": "closed", "vacancy": "by_election"}
        for code, rules, label, methodology in (
            (
                "parliament_1946",
                {"formula": "block"},
                "Grand National Assembly plurality, 1946 to 1957",
                BLOCK_METHODOLOGY,
            ),
            (
                "parliament_1961",
                {"formula": "dhondt", "lists": lists},
                "National Assembly D'Hondt with a provincial quota, 1961",
                QUOTA_METHODOLOGY,
            ),
            (
                "parliament_1965",
                {
                    "formula": "hare",
                    "remainders": "national",
                    "districtFormulas": [{"seats": 2, "formula": "hagenbach_bischoff_quota"}],
                    "lists": lists,
                },
                "National Assembly national remainder, 1965",
                REMAINDER_METHODOLOGY,
            ),
            ("parliament_1969", {"formula": "dhondt", "lists": lists}, "National Assembly D'Hondt, 1969 to 1977", None),
            (
                "parliament_1983",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "party", "scope": "national"},
                    "lists": lists,
                },
                "Grand National Assembly proportional representation with district quotas, 1983",
                DOUBLE_METHODOLOGY,
            ),
            (
                "parliament_1987",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "party", "scope": "national"},
                    "lists": lists,
                },
                (
                    "Grand National Assembly proportional representation with district quotas and kontenjan seats, "
                    "1987 to 1991"
                ),
                KONTENJAN_METHODOLOGY,
            ),
            (
                "parliament_1995",
                {
                    "formula": "dhondt",
                    "threshold": {"percent": 10, "level": "party", "scope": "national"},
                    "lists": lists,
                },
                "Grand National Assembly proportional representation, 1995 to 2002",
                PROVINCIAL_METHODOLOGY,
            ),
        ):
            systems[code] = cs.key("tr.electoral_system", code)
            cs.add("electoral_system", id=systems[code], polity_id=turkey, rules=rules, rules_version=1)
            name(systems[code], label, "en")
            if methodology is not None:
                cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)

        def result_set(contest_key: str, election: UUID, contest: UUID, completeness: int) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=completeness,
            )
            return id

        def option(contest_key: str, contest: UUID, party: str | None, day: date) -> UUID:
            label = (
                INDEPENDENTS
                if party is None
                else party
                if party not in PARTIES
                else next(
                    full
                    for full, _, start, end in PARTIES[party]
                    if (start is None or start <= day) and (end is None or day < end)
                )
            )
            id = cs.key("tr.option", f"{contest_key}:{slug(label)}")
            cs.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="list" if party in PARTIES else "independents" if party is None else "others",
                party_id=parties.get(party or ""),
                ballot_label=label,
            )
            return id

        for year, day in DAYS.items():
            held = rows.filter(pl.col("yr") == year).with_columns(
                party=pl.col("pty").replace_strict(
                    {
                        code: OVERRIDES.get((year, code), key)
                        for code, key in (CODES if year < 1983 else CODES_1983).items()
                    },
                    default=None,
                    return_dtype=pl.String,
                )
            )
            if year in COMBINED:
                held = held.with_columns(
                    party=pl.when(pl.col("pty") == 47).then(pl.lit(COMBINED[year])).otherwise("party")
                )
            kind = "body_seats" if year < 1961 else "list_allocation"
            system = (
                "parliament_1946"
                if year < 1961
                else "parliament_1961"
                if year == 1961
                else "parliament_1965"
                if year == 1965
                else "parliament_1969"
                if year < 1983
                else "parliament_1983"
                if year == 1983
                else "parliament_1987"
                if year < 1995
                else "parliament_1995"
            )
            path = f"tr/{day}-general"
            election = cs.key("tr.election", str(day))
            cs.add("election", id=election, polity_id=turkey, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Turkish general election", "en")
            cs.add("slug", path=path, entity_id=election)
            if year < 1961:
                cs.add("note", entity_id=election, kind="caveat", body_md=BLOCK_CAVEAT)
            if year == 1965:
                cs.add("note", entity_id=election, kind="caveat", body_md=LABEL_1965_CAVEAT)
            if year in COMBINED:
                cs.add("note", entity_id=election, kind="caveat", body_md=COMBINED_CAVEAT)
            if year == 1991:
                cs.add("note", entity_id=election, kind="context", body_md=JOINT_LISTS_1991)
            if year == 2002:
                cs.add("note", entity_id=election, kind="context", body_md=JOINT_LISTS_2002)

            if year >= 1983:
                contest_key = f"{day}:parliament"
                contest = cs.key("tr.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=assembly,
                    district_area_id=nation,
                    magnitude=SEATS[year],
                    round_number=1,
                    electoral_system_id=systems[system],
                    default_result_kind="counted",
                )
                name(contest, "Grand National Assembly", "en")
                cs.add("slug", path=f"{path}/parliament", entity_id=contest)
                declared = result_set(contest_key, election, contest, 1)
                fresh = declared in cs.minted
                for caveat, applies in (
                    (PROVINCES_CAVEAT, True),
                    (UNFILLED_CAVEAT, year in UNFILLED),
                    (FIXED_CAVEAT, any(fixed == year for fixed, _, _ in FIXED)),
                    (SWAPPED_CAVEAT, year in SWAPPED),
                ):
                    if applies:
                        cs.add("note", entity_id=declared, kind="caveat", body_md=caveat)
                options = {party: option(contest_key, contest, party, day) for party in set(held["party"])}
                for party, seats in held.group_by("party").agg(pl.col("seat").sum()).iter_rows():
                    if seats > 0:
                        cs.add(
                            "outcome", result_set_id=declared, option_id=options[party], kind="won", seats=int(seats)
                        )
                for plate in sorted(set(held["plate"])):
                    province = held.filter(pl.col("plate") == plate)
                    unit = cs.unit("tr.il", f"{plate:02}")
                    if unit in cs.minted:
                        raise ValueError("publish tr.ysk before tr.thea")
                    if not fresh:
                        continue
                    totals = province.row(0, named=True)
                    for measure, column in ((REGISTERED, "pev1"), (VOTERS, "vot1"), (VALID, "vv1")):
                        if totals[column] >= 0:
                            cs.add(
                                "result_value",
                                result_set_id=declared,
                                unit_id=unit,
                                option_id=None,
                                channel=0,
                                measure=measure,
                                count_round=0,
                                flags=0,
                                value=int(totals[column]),
                            )
                    for party, votes, seats, lines in (
                        province.group_by("party").agg(pl.col("pv1").sum(), pl.col("seat").sum(), pl.len()).iter_rows()
                    ):
                        for measure, value in ((VOTES, votes), (WON, seats)):
                            if measure == VOTES or value > 0:
                                cs.add(
                                    "result_value",
                                    result_set_id=declared,
                                    unit_id=unit,
                                    option_id=options[party],
                                    channel=0,
                                    measure=measure,
                                    count_round=0,
                                    flags=1 if lines > 1 else 0,
                                    value=int(value),
                                )
                continue

            national = None
            if year == 1965:
                national_key = f"{day}:parliament"
                national = cs.key("tr.contest", national_key)
                cs.add(
                    "contest",
                    id=national,
                    election_id=election,
                    kind="list_allocation",
                    body_id=assembly,
                    district_area_id=nation,
                    magnitude=SEATS[year],
                    round_number=1,
                    electoral_system_id=systems[system],
                    default_result_kind="counted",
                )
                name(national, "National Assembly, national remainder and seats", "en")
                cs.add("slug", path=f"{path}/parliament", entity_id=national)
                declared = result_set(national_key, election, national, 0)
                for party, seats in held.group_by("party").agg(pl.col("seat").sum()).sort("party").iter_rows():
                    id = option(national_key, national, party, day)
                    if seats > 0:
                        cs.add("outcome", result_set_id=declared, option_id=id, kind="won", seats=int(seats))

            for plate in sorted(set(held["plate"])):
                province = held.filter(pl.col("plate") == plate)
                place = (
                    "Maraş" if plate == 46 and day < MARAS_RENAMED else FORMER_NAMES.get(plate, PROVINCES[plate - 1])
                )
                contest_key = f"{day}:parliament:{plate:02}"
                contest = cs.key("tr.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind=kind,
                    body_id=assembly,
                    district_area_id=provinces[plate],
                    magnitude=int(province["seat"].sum()),
                    round_number=1,
                    allocation_parent_id=national,
                    electoral_system_id=systems[system],
                    default_result_kind="counted",
                )
                name(contest, f"{'Grand National Assembly' if year < 1961 else 'National Assembly'}, {place}", "en")
                cs.add("slug", path=f"{path}/parliament/{slug(place)}", entity_id=contest)
                if year == 1965 and plate == 34:
                    cs.add("note", entity_id=contest, kind="caveat", body_md=ISTANBUL_1965_CAVEAT)
                declared = result_set(contest_key, election, contest, 1)
                fresh = declared in cs.minted
                totals = province.row(0, named=True)
                for measure, column in ((REGISTERED, "pev1"), (VOTERS, "vot1"), (VALID, "vv1")):
                    if fresh and totals[column] >= 0 and (measure != VALID or year >= 1961):
                        cs.add(
                            "result_value",
                            result_set_id=declared,
                            unit_id=units[plate],
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=int(totals[column]),
                        )
                for party, votes, seats, lines in (
                    province.group_by("party")
                    .agg(pl.col("pv1").sum(), pl.col("seat").sum(), pl.len())
                    .sort("party")
                    .iter_rows()
                ):
                    id = option(contest_key, contest, party, day)
                    if seats > 0:
                        cs.add("outcome", result_set_id=declared, option_id=id, kind="won", seats=int(seats))
                    if fresh:
                        cs.add(
                            "result_value",
                            result_set_id=declared,
                            unit_id=units[plate],
                            option_id=id,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=1 if lines > 1 else 0,
                            value=int(votes),
                        )
        return cs.write()
