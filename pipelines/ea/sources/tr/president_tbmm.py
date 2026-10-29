from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "tr.president_tbmm"
VERSION = "1"
WIKI = "https://tr.wikipedia.org/wiki/{}"
WIKITEXT = "https://tr.wikipedia.org/w/index.php?title={}&action=raw"
LICENSE = "Creative Commons Attribution-ShareAlike 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
LIST = "Türkiye'de cumhurbaşkanlığı seçimleri"
AA = "https://www.aa.com.tr/tr/turkiye/1923-ten-gunumuze-cumhurbaskani-secimleri/134629"
AA_TITLE = "1923'ten günümüze cumhurbaşkanı seçimleri"
ARTICLES = {
    1946: "1946 Türkiye cumhurbaşkanlığı seçimi",
    1950: "1950 Türkiye cumhurbaşkanlığı seçimi",
    1954: "1954 Türkiye cumhurbaşkanlığı seçimi",
    1957: "1957 Türkiye cumhurbaşkanlığı seçimi",
    1961: "1961 Türkiye cumhurbaşkanlığı seçimi",
    1966: "1966 Türkiye cumhurbaşkanlığı seçimi",
    1973: "1973 Türkiye cumhurbaşkanlığı seçimi",
    1989: "1989 Türkiye cumhurbaşkanlığı seçimi",
    1993: "1993 Türkiye cumhurbaşkanlığı seçimi",
    2000: "2000 Türkiye cumhurbaşkanlığı seçimi",
    2007: "2007 Türkiye cumhurbaşkanlığı seçimi",
}
PRESENT, VOTES, BLANK, INVALID = 20, 1, 6, 5
NOTE = (
    "Counts are transcribed from Turkish Wikipedia's articles on each election, cross-checked against the Anadolu "
    "Ajansı summary where it gives the same figure. Where the two disagree, the disputed count is left out and the "
    "outcome stands as declared. The TBMM's own minutes are scanned images with no text layer and have not been read."
)
METHODOLOGY = (
    "The Grand National Assembly elects the President of the Republic by secret ballot from among its own members "
    "(1924 Constitution art. 31, 1961 Constitution art. 95, 1982 Constitution art. 102 until 2007). Rounds are separate "
    "contests chained in order. The recorded vote counts are as the chair announced them."
)
NAMES = {
    "Atatürk": "Mustafa Kemal Atatürk",
    "İnönü": "İsmet İnönü",
    "Çakmak": "Fevzi Çakmak",
    "Bayar": "Celâl Bayar",
    "Gürsel": "Cemal Gürsel",
    "Sunay": "Cevdet Sunay",
    "Gürler": "Faruk Gürler",
    "Arıburun": "Tekin Arıburun",
    "Bozbeyli": "Ferruh Bozbeyli",
    "Korutürk": "Fahri Korutürk",
    "Özal": "Turgut Özal",
    "Çelikbaş": "Orhan Kemal Çelikbaş",
    "Demirel": "Süleyman Demirel",
    "Sezer": "Ahmet Necdet Sezer",
    "Yalçıntaş": "Nevzat Yalçıntaş",
    "Somuncuoğlu": "Sadi Somuncuoğlu",
    "Zaimoğlu": "Rasim Zaimoğlu",
    "Büyükerman": "Mehmet Mail Büyükerman",
    "Akbulut": "Yıldırım Akbulut",
    "Güreş": "Doğan Güreş",
    "İyimaya": "Ahmet İyimaya",
    "Güner": "Agah Oktay Güner",
    "Aygün": "Oğuz Aygün",
    "Gül": "Abdullah Gül",
    "Çakmakoğlu": "Sabahattin Çakmakoğlu",
}
D = date
ELECTIONS = [
    (1923, "Atatürk", [(D(1923, 10, 29), 158, None, {"Atatürk": 158}, {})]),
    (1927, "Atatürk", [(D(1927, 11, 1), 288, None, {"Atatürk": 288}, {})]),
    (1931, "Atatürk", [(D(1931, 5, 4), 289, None, {"Atatürk": 289}, {})]),
    (1935, "Atatürk", [(D(1935, 3, 1), 386, None, {"Atatürk": 386}, {})]),
    (1938, "İnönü", [(D(1938, 11, 11), 348, None, {"İnönü": 348}, {})]),
    (1939, "İnönü", [(D(1939, 4, 3), 413, None, {"İnönü": 413}, {})]),
    (1943, "İnönü", [(D(1943, 3, 8), 435, None, {"İnönü": 435}, {})]),
    (1946, "İnönü", [(D(1946, 8, 5), 451, None, {"İnönü": 388, "Çakmak": 59}, {})]),
    (1950, "Bayar", [(D(1950, 5, 22), 453, None, {"Bayar": 387}, {})]),
    (1954, "Bayar", [(D(1954, 5, 14), None, None, {"Bayar": 486}, {})]),
    (1957, "Bayar", [(D(1957, 11, 1), 413, None, {"Bayar": 413}, {})]),
    (1961, "Gürsel", [(D(1961, 10, 26), 607, None, {"Gürsel": 434}, {})]),
    (1966, "Sunay", [(D(1966, 3, 28), 532, None, {}, {})]),
    (
        1973,
        "Korutürk",
        [
            (D(1973, 3, 13), 517, None, {"Gürler": 175, "Arıburun": 282, "Bozbeyli": 45}, {}),
            (D(1973, 3, 13), 519, None, {"Gürler": 176, "Arıburun": 284, "Bozbeyli": 47}, {}),
            (D(1973, 3, 13), 527, None, {"Gürler": 186, "Arıburun": 285, "Bozbeyli": 47}, {}),
            (D(1973, 3, 13), 535, None, {"Gürler": 200, "Arıburun": 276, "Bozbeyli": 48}, {}),
            (D(1973, 3, 16), 498, None, {"Gürler": 149, "Arıburun": 293, "Bozbeyli": 48}, {}),
            (D(1973, 3, 16), 518, None, {"Gürler": 165, "Arıburun": 292, "Bozbeyli": 48}, {}),
            (D(1973, 3, 21), 142, None, {"Gürler": 11, "Arıburun": 0, "Bozbeyli": 92}, {}),
            (D(1973, 3, 24), 120, None, {"Gürler": 20, "Arıburun": 0, "Bozbeyli": 69}, {}),
            (D(1973, 3, 26), 152, None, {"Gürler": 51, "Arıburun": 0, "Bozbeyli": 59}, {}),
            (D(1973, 3, 27), 157, None, {"Gürler": 57, "Arıburun": 0, "Bozbeyli": 51}, {}),
            (D(1973, 3, 28), 178, None, {"Gürler": 81, "Arıburun": 0, "Bozbeyli": 51}, {}),
            (D(1973, 3, 30), 140, None, {"Gürler": 69, "Arıburun": 0, "Bozbeyli": 43}, {}),
            (D(1973, 4, 2), 149, None, {"Gürler": 64, "Arıburun": 2, "Bozbeyli": 55}, {}),
            (D(1973, 4, 4), 136, None, {"Gürler": 52, "Arıburun": 0, "Bozbeyli": 51}, {}),
            (D(1973, 4, 6), 557, None, {"Gürler": 87, "Arıburun": 17, "Bozbeyli": 51, "Korutürk": 365}, {}),
        ],
    ),
    (
        1989,
        "Özal",
        [
            (D(1989, 10, 20), 285, None, {"Özal": 247, "Çelikbaş": 18}, {BLANK: 17, INVALID: 3}),
            (D(1989, 10, 24), 284, None, {"Özal": 256, "Çelikbaş": 17}, {BLANK: 9, INVALID: 2}),
            (D(1989, 10, 31), 285, None, {"Özal": 263, "Çelikbaş": 14}, {BLANK: 8}),
        ],
    ),
    (
        1993,
        "Demirel",
        [
            (D(1993, 5, 8), 422, None, {"Demirel": 234, "İnan": 95, "Doğan": 46, "Cem": 25}, {BLANK: 19, INVALID: 3}),
            (D(1993, 5, 12), 426, None, {"Demirel": 235, "İnan": 95, "Doğan": 49, "Cem": 25}, {BLANK: 21, INVALID: 1}),
            (D(1993, 5, 16), 431, None, {"Demirel": 244, "İnan": 94, "Doğan": 47, "Cem": 27}, {BLANK: 19}),
        ],
    ),
    (
        2000,
        "Sezer",
        [
            (
                D(2000, 4, 27),
                530,
                367,
                {
                    "Sezer": 281,
                    "Yalçıntaş": 61,
                    "Somuncuoğlu": 58,
                    "Zaimoğlu": 7,
                    "Büyükerman": 3,
                    "Akbulut": 56,
                    "Güreş": 35,
                    "İyimaya": 10,
                    "Güner": 5,
                    "Aygün": 4,
                },
                {BLANK: 2, INVALID: 8},
            ),
            (
                D(2000, 5, 1),
                532,
                367,
                {
                    "Sezer": 314,
                    "Yalçıntaş": 66,
                    "Somuncuoğlu": 32,
                    "Zaimoğlu": 3,
                    "Büyükerman": 2,
                    "Akbulut": 88,
                    "Güreş": 22,
                },
                {BLANK: 3, INVALID: 2},
            ),
            (
                D(2000, 5, 5),
                533,
                276,
                {"Sezer": 330, "Yalçıntaş": 113, "Somuncuoğlu": 43, "Zaimoğlu": 24, "Büyükerman": 7},
                {BLANK: 8, INVALID: 8},
            ),
        ],
    ),
    (
        2007,
        "Gül",
        [
            (D(2007, 4, 27), 361, 367, {"Gül": 357}, {}),
            (D(2007, 8, 20), 448, 367, {"Gül": 341, "Çakmakoğlu": 70, "İçli": 13}, {BLANK: 23, INVALID: 1}),
            (D(2007, 8, 24), 446, 367, {"Gül": 337, "Çakmakoğlu": 71, "İçli": 14}, {BLANK: 24, INVALID: 0}),
            (D(2007, 8, 28), 448, 276, {"Gül": 339, "Çakmakoğlu": 70, "İçli": 13}, {BLANK: 24, INVALID: 2}),
        ],
    ),
]
PRESIDENTS = {
    "Atatürk": (D(1923, 10, 29), D(1938, 11, 10), "died"),
    "İnönü": (D(1938, 11, 11), D(1950, 5, 22), "term_end"),
    "Bayar": (D(1950, 5, 22), D(1960, 5, 27), "removed"),
    "Gürsel": (D(1961, 10, 26), D(1966, 3, 28), "resigned"),
    "Sunay": (D(1966, 3, 28), D(1973, 3, 28), "term_end"),
    "Korutürk": (D(1973, 4, 6), D(1980, 4, 6), "term_end"),
    "Özal": (D(1989, 11, 9), D(1993, 4, 17), "died"),
    "Demirel": (D(1993, 5, 16), D(2000, 5, 16), "term_end"),
    "Sezer": (D(2000, 5, 16), D(2007, 8, 28), "term_end"),
    "Gül": (D(2007, 8, 28), D(2014, 8, 28), "term_end"),
}


def fetch() -> None:
    raw.fetch(SOURCE, WIKITEXT.format(quote(LIST.replace(" ", "_"))), suffix=".wiki")
    for title in ARTICLES.values():
        raw.fetch(SOURCE, WIKITEXT.format(quote(title.replace(" ", "_"))), suffix=".wiki")
    raw.fetch(SOURCE, AA, suffix=".html")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Elections of the President of the Republic by the Grand National Assembly, 1923-2007",
            polity="TR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        sources = {}
        for title in (LIST, *ARTICLES.values()):
            url = WIKI.format(quote(title.replace(" ", "_")))
            source = cs.source("Wikipedia", title)
            cs.add(
                "source",
                id=source,
                kind="community",
                publisher="Wikipedia",
                title=title,
                url=url,
                license=LICENSE,
                license_url=LICENSE_URL,
            )
            cs.artifact(raw.cached(SOURCE, WIKITEXT.format(quote(title.replace(" ", "_")))), source)
            sources[title] = source
        news = cs.source("Anadolu Ajansı", AA_TITLE)
        cs.add("source", id=news, kind="news_agency", publisher="Anadolu Ajansı", title=AA_TITLE, url=AA)
        cs.artifact(raw.cached(SOURCE, AA), news)

        turkey = cs.polity("TR")
        assembly = cs.key("tr.body", "tbmm")
        president = cs.key("tr.office", "president")
        if assembly in cs.minted or president in cs.minted:
            raise ValueError("publish tr.tbmm and tr.ysk before tr.president_tbmm")
        cs.add("scheme", code="tr.assembly", polity_id=turkey, kind="electoral_district", rank=0, noun="assembly")
        unit = cs.unit("tr.assembly", "tbmm")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=turkey,
            scheme_code="tr.assembly",
            code="tbmm",
            area_relation="unlocated",
            kind="aggregate",
        )
        name(unit, "Grand National Assembly in session")
        system = cs.key("tr.electoral_system", "president-tbmm")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=turkey,
            rules={
                "formula": "repeated_ballot",
                "majorities": [{"fromRound": 1, "share": 2 / 3}, {"fromRound": 3, "share": 0.5, "strict": True}],
            },
            rules_version=1,
        )
        name(system, "Election of the President by the Grand National Assembly")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)

        persons: dict[str, UUID] = {}
        for label, full in NAMES.items():
            rows = conn.execute(
                """select distinct n.entity_id, x.scheme from name n join person p on p.id = n.entity_id
                   left join external_id x on x.entity_id = n.entity_id and x.scheme in ('wikidata', 'tr.tbmm_album.person')
                   where n.text = %s""",
                [full],
            ).fetchall()
            chosen = (
                {id for id, scheme in rows if scheme == "wikidata"}
                or {id for id, scheme in rows if scheme is not None}
                or {id for id, _ in rows}
            )
            if len(chosen) == 1:
                persons[label] = next(iter(chosen))
            else:
                persons[label] = cs.key(f"{SOURCE}.person", slug(full))
                if persons[label] in cs.minted:
                    cs.add("person", id=persons[label])
                    name(persons[label], full, "tr")
                print("minted person", full, len(chosen))

        result_values = []
        for year, winner, rounds in ELECTIONS:
            first, last = rounds[0][0], rounds[-1][0]
            election = cs.key("tr.election", f"{first}:president-tbmm")
            cs.add(
                "election",
                id=election,
                polity_id=turkey,
                kind="indirect",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(election, f"{year} Turkish presidential election")
            path = f"tr/{first}-president"
            cs.add("slug", path=path, entity_id=election)
            cs.add("note", entity_id=election, kind="caveat", body_md=NOTE)
            previous = None
            contest = None
            for number, (day, present, required, votes, others) in enumerate(rounds, 1):
                contest_key = f"{first}:president-tbmm:{number}"
                contest = cs.key("tr.contest", contest_key)
                override = {} if required is None else {"required": required}
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=president,
                    body_id=assembly,
                    magnitude=1,
                    round_number=number,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    rules_override=override,
                    default_result_kind="counted",
                )
                label = f"Ballot {number}, {day:%B} {day.day}" if len(rounds) > 1 else f"{year} presidential vote"
                name(contest, label)
                cs.add("slug", path=f"{path}/ballot-{number}" if len(rounds) > 1 else f"{path}/vote", entity_id=contest)
                previous = contest
                if year == 2007 and number == 1:
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="caveat",
                        body_md="The Constitutional Court annulled this ballot on May 1, 2007, ruling that it lacked the two-thirds quorum.",
                    )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[ARTICLES.get(year, LIST)],
                    is_default=True,
                    completeness=0,
                )
                rows = [(PRESENT, None, present)] if present is not None else []
                rows += [(measure, None, value) for measure, value in others.items()]
                rows += [(VOTES, label, value) for label, value in votes.items()]
                for measure, candidate, value in rows:
                    option = None
                    if candidate is not None:
                        option = cs.key("tr.option", f"{contest_key}:{slug(candidate)}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            ballot_label=NAMES.get(candidate, candidate),
                        )
                        if candidate in persons:
                            cs.add(
                                "candidacy",
                                id=cs.key("tr.candidacy", f"{contest_key}:{slug(candidate)}"),
                                person_id=persons[candidate],
                                option_id=option,
                            )
                    result_values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": None if option is None else str(option),
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "value": value,
                            "flags": 0,
                        }
                    )
                if number == len(rounds):
                    won = cs.key("tr.option", f"{contest_key}:{slug(winner)}")
                    if winner not in votes:
                        cs.add(
                            "contest_option",
                            id=won,
                            contest_id=contest,
                            kind="candidate",
                            ballot_label=NAMES[winner],
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("tr.candidacy", f"{contest_key}:{slug(winner)}"),
                            person_id=persons[winner],
                            option_id=won,
                        )
                    cs.add("outcome", result_set_id=result_set, option_id=won, kind="elected")
            if winner in PRESIDENTS:
                start, end, via = PRESIDENTS[winner]
                cs.add(
                    "tenure",
                    id=cs.key(f"{SOURCE}.tenure", f"{first}:{slug(winner)}"),
                    person_id=persons[winner],
                    office_id=president,
                    from_date=start,
                    to_date=end,
                    acquired_via="elected",
                    ended_via=via,
                    contest_id=contest,
                )
        cs.frame(
            "result_value",
            pl.DataFrame(
                result_values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
