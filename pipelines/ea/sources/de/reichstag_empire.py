from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "de.reichstag_empire"
VERSION = "1"
URL = "https://www.bundeswahlleiterin.de/dam/jcr/397735e3-0585-46f6-a0b5-2c60c5b83de6/btw_ab49_gesamt.pdf"
PUBLISHER = "Die Bundeswahlleiterin"
TITLE = "Ergebnisse früherer Bundestagswahlen, table 17: Reichstagswahlen 1871 bis 1912"
NOTES = (
    "Table 17, Wahlberechtigte, Wählende und Stimmabgabe bei den Reichstagswahlen 1871 bis 1912, PDF pages 140 and "
    "141, which the Federal Returning Officer took from the Statistisches Jahrbuch für das Deutsche Reich 1923, "
    "pages 346 and 347. Figures are printed in thousands with one decimal."
)
VOTES, REGISTERED, CAST, VALID = 1, 2, 3, 4
RULES = {"formula": "two_round", "majority": 0.5, "advance": 2}
SYSTEM = (
    "Single-member constituencies. A candidate needed an absolute majority of the valid votes; otherwise the two "
    "leading candidates went to a runoff (Stichwahl). Every man who had reached the age of 25 could vote."
)
NOTE = (
    "The Reichsamt's table gives the whole Reich's votes by party, in thousands with one decimal, so every figure here "
    "is rounded to the nearest hundred votes and every party's votes are the sum of its candidates in the first "
    "ballot of all constituencies. Seats are the members at the start of the legislative period. Votes by "
    "constituency are not held. Ballots cast are the printed voters (Wähler), which include invalid ballots."
)

PARTIES = {
    "kons": ("Q458791", "Deutschkonservative Partei", "(Deutsch-) Konservativ"),
    "frei": ("Q455038", "Deutsche Reichspartei", "Deutsche Reichspartei (freikonservativ)"),
    "natlib": (None, "Nationalliberale Partei", "Nationalliberal"),
    "fort": ("Q694714", "Deutsche Fortschrittspartei", "Fortschrittspartei"),
    "dfp": ("Q560777", "Deutsche Freisinnige Partei", "Deutsch-Freisinnig"),
    "fv": ("Q633731", "Freisinnige Vereinigung", "Freisinnige Vereinigung"),
    "fvp": ("Q319457", "Freisinnige Volkspartei", "Freisinnige Volkspartei"),
    "fvolk": (None, "Fortschrittliche Volkspartei", "Fortschrittliche Volkspartei"),
    "dvp": (None, "Deutsche Volkspartei (1868)", "Deutsche Volkspartei"),
    "zentrum": ("Q157537", "Deutsche Zentrumspartei", "Zentrum"),
    "spd": ("Q49768", "Sozialdemokratische Partei Deutschlands", "Sozialdemokraten"),
}
LABELS = {
    "libother": "Liberale ohne nähere Bezeichnung (1881: Liberale Vereinigung)",
    "polen": "Polen",
    "antisem": "Antisemiten (Deutsche Reformpartei, christlich-sozial)",
    "welfen": "Welfen",
    "elsass": "Elsaß-Lothringer",
    "daenen": "Dänen",
}
OTHERS = {"andere": "Andere Parteien", "unbest": "Unbestimmt und zersplittert"}
ORDER = [
    "kons", "frei", "natlib", "libother", "fort", "dfp", "fv", "fvp", "fvolk", "dvp", "zentrum", "polen", "spd",
    "antisem", "welfen", "elsass", "daenen", "andere", "unbest",
]  # fmt: skip

ELECTIONS: dict[date, dict[str, dict[str, float]]] = {
    date(1871, 3, 3): {
        "total": {"registered": 7656.3, "cast": 3907.1, "invalid": 19.0, "valid": 3888.1},
        "votes": {
            "kons": 548.9,
            "frei": 345.7,
            "natlib": 1171.1,
            "libother": 281.5,
            "fort": 342.4,
            "dvp": 18.7,
            "zentrum": 724.2,
            "polen": 176.3,
            "spd": 124.0,
            "welfen": 60.9,
            "daenen": 18.2,
            "unbest": 76.2,
        },
        "seats": {
            "kons": 57,
            "frei": 37,
            "natlib": 125,
            "libother": 30,
            "fort": 46,
            "dvp": 1,
            "zentrum": 63,
            "polen": 13,
            "spd": 2,
            "welfen": 7,
            "elsass": 1,
        },
    },
    date(1874, 1, 10): {
        "total": {"registered": 8523.4, "cast": 5219.9, "invalid": 29.6, "valid": 5190.3},
        "votes": {
            "kons": 360.0,
            "frei": 375.5,
            "natlib": 1542.5,
            "libother": 53.9,
            "fort": 447.5,
            "dvp": 21.7,
            "zentrum": 1446.0,
            "polen": 198.4,
            "spd": 352.0,
            "welfen": 92.1,
            "elsass": 234.5,
            "daenen": 19.9,
            "unbest": 46.3,
        },
        "seats": {
            "kons": 22,
            "frei": 33,
            "natlib": 155,
            "libother": 3,
            "fort": 49,
            "dvp": 1,
            "zentrum": 91,
            "polen": 14,
            "spd": 9,
            "welfen": 4,
            "elsass": 15,
            "daenen": 1,
        },
    },
    date(1877, 1, 10): {
        "total": {"registered": 8943.0, "cast": 5422.6, "invalid": 21.6, "valid": 5401.0},
        "votes": {
            "kons": 526.0,
            "frei": 426.6,
            "natlib": 1469.5,
            "libother": 134.8,
            "fort": 417.8,
            "dvp": 44.9,
            "zentrum": 1341.3,
            "polen": 216.2,
            "spd": 493.3,
            "welfen": 85.6,
            "elsass": 200.0,
            "daenen": 17.3,
            "andere": 11.6,
            "unbest": 16.1,
        },
        "seats": {
            "kons": 40,
            "frei": 38,
            "natlib": 128,
            "libother": 13,
            "fort": 35,
            "dvp": 4,
            "zentrum": 93,
            "polen": 14,
            "spd": 12,
            "welfen": 4,
            "elsass": 15,
            "daenen": 1,
        },
    },
    date(1878, 7, 30): {
        "total": {"registered": 9124.3, "cast": 5780.9, "invalid": 20.0, "valid": 5760.9},
        "votes": {
            "kons": 749.5,
            "frei": 785.8,
            "natlib": 1330.6,
            "libother": 156.1,
            "fort": 385.1,
            "dvp": 66.1,
            "zentrum": 1328.1,
            "polen": 210.1,
            "spd": 437.2,
            "welfen": 100.3,
            "elsass": 178.9,
            "daenen": 16.1,
            "andere": 2.3,
            "unbest": 14.7,
        },
        "seats": {
            "kons": 59,
            "frei": 57,
            "natlib": 99,
            "libother": 10,
            "fort": 26,
            "dvp": 3,
            "zentrum": 94,
            "polen": 14,
            "spd": 9,
            "welfen": 10,
            "elsass": 15,
            "daenen": 1,
        },
    },
    date(1881, 10, 27): {
        "total": {"registered": 9090.4, "cast": 5118.4, "invalid": 20.6, "valid": 5097.8},
        "votes": {
            "kons": 830.8,
            "frei": 379.3,
            "natlib": 746.6,
            "libother": 429.2,
            "fort": 649.3,
            "dvp": 103.4,
            "zentrum": 1182.9,
            "polen": 194.9,
            "spd": 312.0,
            "welfen": 86.7,
            "elsass": 153.0,
            "daenen": 14.4,
            "unbest": 15.3,
        },
        "seats": {
            "kons": 50,
            "frei": 28,
            "natlib": 47,
            "libother": 46,
            "fort": 60,
            "dvp": 9,
            "zentrum": 100,
            "polen": 18,
            "spd": 12,
            "welfen": 10,
            "elsass": 15,
            "daenen": 2,
        },
    },
    date(1884, 10, 28): {
        "total": {"registered": 9383.1, "cast": 5681.7, "invalid": 18.7, "valid": 5663.0},
        "votes": {
            "kons": 861.1,
            "frei": 387.7,
            "natlib": 997.0,
            "dfp": 997.0,
            "dvp": 95.9,
            "zentrum": 1282.0,
            "polen": 203.2,
            "spd": 550.0,
            "welfen": 96.4,
            "elsass": 165.6,
            "daenen": 14.4,
            "unbest": 12.7,
        },
        "seats": {
            "kons": 78,
            "frei": 28,
            "natlib": 51,
            "dfp": 67,
            "dvp": 7,
            "zentrum": 99,
            "polen": 16,
            "spd": 24,
            "welfen": 11,
            "elsass": 15,
            "daenen": 1,
        },
    },
    date(1887, 2, 21): {
        "total": {"registered": 9769.8, "cast": 7570.7, "invalid": 29.8, "valid": 7540.9},
        "votes": {
            "kons": 1147.2,
            "frei": 736.4,
            "natlib": 1678.0,
            "dfp": 973.1,
            "dvp": 88.8,
            "zentrum": 1516.2,
            "polen": 220.0,
            "spd": 763.1,
            "antisem": 11.6,
            "welfen": 112.8,
            "elsass": 233.7,
            "daenen": 12.4,
            "unbest": 47.6,
        },
        "seats": {
            "kons": 80,
            "frei": 41,
            "natlib": 99,
            "dfp": 32,
            "zentrum": 98,
            "polen": 13,
            "spd": 11,
            "antisem": 1,
            "welfen": 4,
            "elsass": 15,
            "daenen": 1,
            "andere": 2,
        },
    },
    date(1890, 2, 20): {
        "total": {"registered": 10145.0, "cast": 7261.6, "invalid": 33.1, "valid": 7228.5},
        "votes": {
            "kons": 895.1,
            "frei": 482.3,
            "natlib": 1177.8,
            "dfp": 1159.9,
            "dvp": 147.6,
            "zentrum": 1342.1,
            "polen": 246.8,
            "spd": 1427.3,
            "antisem": 47.5,
            "welfen": 112.7,
            "elsass": 101.1,
            "daenen": 13.7,
            "unbest": 74.6,
        },
        "seats": {
            "kons": 73,
            "frei": 20,
            "natlib": 42,
            "dfp": 66,
            "dvp": 10,
            "zentrum": 106,
            "polen": 16,
            "spd": 35,
            "antisem": 5,
            "welfen": 11,
            "daenen": 1,
            "elsass": 10,
            "andere": 2,
        },
    },
    date(1893, 6, 15): {
        "total": {"registered": 10628.3, "cast": 7702.3, "invalid": 28.3, "valid": 7674.0},
        "votes": {
            "kons": 1038.4,
            "frei": 438.4,
            "natlib": 997.0,
            "fv": 258.5,
            "fvp": 666.4,
            "dvp": 166.8,
            "zentrum": 1468.5,
            "polen": 229.5,
            "spd": 1786.7,
            "antisem": 263.9,
            "welfen": 101.8,
            "elsass": 114.7,
            "daenen": 14.4,
            "andere": 70.3,
            "unbest": 58.7,
        },
        "seats": {
            "kons": 72,
            "frei": 28,
            "natlib": 53,
            "fv": 13,
            "fvp": 24,
            "dvp": 11,
            "zentrum": 96,
            "polen": 19,
            "spd": 44,
            "antisem": 16,
            "welfen": 7,
            "daenen": 1,
            "elsass": 8,
            "andere": 5,
        },
    },
    date(1898, 6, 16): {
        "total": {"registered": 11441.1, "cast": 7786.7, "invalid": 34.0, "valid": 7752.7},
        "votes": {
            "kons": 859.2,
            "frei": 343.7,
            "natlib": 971.3,
            "fv": 195.7,
            "fvp": 558.3,
            "dvp": 108.5,
            "zentrum": 1455.1,
            "polen": 244.1,
            "spd": 2107.1,
            "antisem": 284.3,
            "welfen": 105.2,
            "elsass": 107.4,
            "daenen": 15.4,
            "andere": 290.9,
            "unbest": 106.5,
        },
        "seats": {
            "kons": 56,
            "frei": 23,
            "natlib": 46,
            "fv": 12,
            "fvp": 29,
            "dvp": 8,
            "zentrum": 102,
            "polen": 14,
            "spd": 56,
            "antisem": 13,
            "welfen": 9,
            "daenen": 1,
            "elsass": 10,
            "andere": 18,
        },
    },
    date(1903, 6, 16): {
        "total": {"registered": 12531.2, "cast": 9533.8, "invalid": 38.2, "valid": 9495.6},
        "votes": {
            "kons": 948.5,
            "frei": 333.4,
            "natlib": 1317.4,
            "fv": 243.2,
            "fvp": 538.2,
            "dvp": 91.2,
            "zentrum": 1875.3,
            "polen": 347.8,
            "spd": 3010.8,
            "antisem": 244.5,
            "welfen": 94.3,
            "elsass": 101.9,
            "daenen": 14.8,
            "andere": 267.1,
            "unbest": 67.2,
        },
        "seats": {
            "kons": 54,
            "frei": 21,
            "natlib": 51,
            "fv": 9,
            "fvp": 21,
            "dvp": 6,
            "zentrum": 100,
            "polen": 16,
            "spd": 81,
            "antisem": 11,
            "welfen": 6,
            "daenen": 1,
            "elsass": 9,
            "andere": 11,
        },
    },
    date(1907, 1, 25): {
        "total": {"registered": 13352.9, "cast": 11293.5, "invalid": 30.7, "valid": 11262.8},
        "votes": {
            "kons": 1060.2,
            "frei": 471.9,
            "natlib": 1630.6,
            "fv": 359.3,
            "fvp": 736.0,
            "dvp": 138.6,
            "zentrum": 2179.8,
            "polen": 453.9,
            "spd": 3259.0,
            "antisem": 248.5,
            "welfen": 78.2,
            "elsass": 103.6,
            "daenen": 15.4,
            "andere": 319.1,
            "unbest": 208.7,
        },
        "seats": {
            "kons": 60,
            "frei": 24,
            "natlib": 54,
            "fv": 14,
            "fvp": 28,
            "dvp": 7,
            "zentrum": 105,
            "polen": 20,
            "spd": 43,
            "antisem": 16,
            "welfen": 1,
            "daenen": 1,
            "elsass": 7,
            "andere": 17,
        },
    },
    date(1912, 1, 12): {
        "total": {"registered": 14442.4, "cast": 12260.7, "invalid": 53.1, "valid": 12207.6},
        "votes": {
            "kons": 1126.3,
            "frei": 367.2,
            "natlib": 1662.7,
            "fvolk": 1497.0,
            "zentrum": 1996.8,
            "polen": 441.7,
            "spd": 4250.4,
            "antisem": 51.9,
            "welfen": 84.6,
            "elsass": 162.0,
            "daenen": 17.3,
            "andere": 428.0,
            "unbest": 121.7,
        },
        "seats": {
            "kons": 43,
            "frei": 14,
            "natlib": 45,
            "fvolk": 42,
            "zentrum": 91,
            "polen": 18,
            "spd": 110,
            "antisem": 3,
            "welfen": 5,
            "daenen": 1,
            "elsass": 9,
            "andere": 16,
        },
    },
}
SEATS = {date(1871, 3, 3): 382}


def thousands(value: float) -> int:
    return round(value * 1000)


def check() -> None:
    for day, spec in ELECTIONS.items():
        total = spec["total"]
        if abs(sum(spec["votes"].values()) - total["valid"]) > 0.35:
            raise ValueError(f"{day}: parties add up to {sum(spec['votes'].values()):.1f}, not {total['valid']}")
        if abs(total["cast"] - total["invalid"] - total["valid"]) > 0.15:
            raise ValueError(f"{day}: cast less invalid is not valid")
        if sum(spec["seats"].values()) != SEATS.get(day, 397):
            raise ValueError(f"{day}: seats add up to {sum(spec['seats'].values())}")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    check()


def emit() -> Path:
    check()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Reichstag elections 1871 to 1912, national party votes and seats from the Federal Returning Officer",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        nation = cs.area("de.nation", "DE")
        unit = cs.unit("de.nation", "DE")
        if unit in cs.minted:
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=germany,
                scheme_code="de.nation",
                code="DE",
                area_relation="equals",
                area_id=nation,
                kind="standard",
                valid_from=None,
            )
        reichstag = cs.key("de.body", "reichstag")
        if reichstag in cs.minted:
            cs.add("body", id=reichstag, polity_id=germany, kind="lower_house")
            name(reichstag, "Reichstag")
            name(reichstag, "Reichstag", "de", "native")
        parties: dict[str, UUID] = {}
        for key, (item, label, short) in PARTIES.items():
            parties[key] = cs.key("wikidata", item) if item is not None else cs.key("de.party", f"empire:{key}")
            if parties[key] in cs.minted:
                cs.add("party", id=parties[key], kind="party", polity_id=germany)
                name(parties[key], label, "de")
                name(parties[key], short, "de", "abbreviation")
        system = cs.key("de.electoral_system", "reichstag-empire")
        cs.add("electoral_system", id=system, polity_id=germany, rules=RULES, rules_version=1)
        name(system, "Reichstag single-member constituencies with runoff, 1871-1912")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=f"{URL}#page=140",
                license="Datenlizenz Deutschland – Namensnennung – Version 2.0",
                license_url="https://www.govdata.de/dl-de/by-2-0",
                attribution="© Die Bundeswahlleiterin, Wiesbaden",
                notes=NOTES,
            )
        cs.artifact(raw.cached(SOURCE, URL), source)

        values: list[tuple[str, str, str | None, int, int, int]] = []
        for day, spec in ELECTIONS.items():
            election = cs.key("de.election", f"{day}:general")
            cs.add(
                "election", id=election, polity_id=germany, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{day.strftime('%B')} {day.year} German federal election")
            name(election, f"Reichstagswahl {day.year}", "de", "native")
            path = f"de/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("de.contest", f"{day}:reichstag-empire")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=reichstag,
                magnitude=SEATS.get(day, 397),
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, "Reichstag")
            cs.add("slug", path=f"{path}/reichstag", entity_id=contest)
            cs.add("note", entity_id=contest, kind="methodology", body_md=f"{SYSTEM}\n\n{NOTE}")
            result_set = cs.key(f"{SOURCE}.result_set", str(day))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            options: dict[str, UUID] = {}
            for key in ORDER:
                if key not in spec["votes"] and key not in spec["seats"]:
                    continue
                option = cs.key("de.option", f"{day}:reichstag-empire:{key}")
                options[key] = option
                if key in OTHERS:
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label=OTHERS[key])
                elif key in LABELS:
                    cs.add("contest_option", id=option, contest_id=contest, kind="list", ballot_label=LABELS[key])
                else:
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[key],
                        ballot_label=PARTIES[key][2],
                    )
            for key, seats in spec["seats"].items():
                cs.add("outcome", result_set_id=result_set, option_id=options[key], kind="won", seats=seats)
            row = (str(result_set), str(unit))
            for column, measure in (("registered", REGISTERED), ("cast", CAST), ("valid", VALID)):
                values.append((*row, None, measure, thousands(spec["total"][column]), 0))
            for key, votes in spec["votes"].items():
                values.append((*row, str(options[key]), VOTES, thousands(votes), 0))

        frame = pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16))
        cs.frame("result_value", frame)
        return cs.write()
