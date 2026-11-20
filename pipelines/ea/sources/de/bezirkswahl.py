import re
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import UUID, uuid5

import polars as pl
import pymupdf

from ea import package, raw
from ea.changeset import Changeset, slug
from ea.package import Package
from ea.store import DATA, connect

SOURCE = "de.bezirkswahl"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Bayerisches Landesamt für Statistik"
BASE = "https://www.statistik.bayern.de/mam/wahlen/bezirkswahlen/ergebnisse"
RESULTS_URL = f"{BASE}/bzw_2023_ergebnisse_wahlkreise_stimmkreise.xlsx"
SCHWABEN_URL = "https://www.regierung.schwaben.bayern.de/mam/presse/pm_ergebnis_sitzung_endg._ergebnis_2023.pdf"
SCHWABEN_TITLE = "Endgültiges Ergebnis der Bezirkswahl im Wahlkreis Schwaben (Pressemitteilung, 20. Oktober 2023)"
SERIES_URL = f"{BASE}/b7610c_20235_bezirkswahlen_in_bayernzeitreihe_2023_.pdf"
RESULTS_TITLE = "Bezirkswahlen 2023: Ergebnisse Wahl- und Stimmkreise"
SERIES_TITLE = "Bezirkswahlen in Bayern 1954 bis 2023 (Statistischer Bericht B VII 6-1/Z 2023)"
PAGE_URL = "https://www.statistik.bayern.de/wahlen/bezirkswahlen/ergebnisse/index.html"
LICENSE = "Reproduction and distribution permitted with attribution (Bayerisches Landesamt für Statistik)"
DAY = date(2023, 10, 8)
SCHEME_BEZIRK = "de.by_regierungsbezirk"
SCHEME_STIMMKREIS = "de.by_stimmkreis"
SYSTEM_RULES = {"formula": "hare"}
SYSTEM_NOTE = (
    "Each voter has two votes: a first vote for a candidate in the constituency (Stimmkreis) and a second vote for a "
    "candidate on a party's list in the district (Wahlkreis, which is the Regierungsbezirk). The votes of both kinds "
    "are added up as total votes (Gesamtstimmen) for each party, and the seats of the Bezirkstag are shared among "
    "the parties by the Hare-Niemeyer largest remainder method on the total votes, with no threshold. Each party's "
    "seats then go first to its candidates who lead in their constituencies, then to list candidates in the order of "
    "their votes."
)
NOTE = (
    "The source publishes each party's first votes (direct votes for its constituency candidate) and second votes "
    "(votes for candidates on its district list) separately for every Stimmkreis. The value for each party's votes is "
    "the sum of the two, the total votes on which the seats are shared, and is marked as derived. The first votes are "
    "stored as candidate votes and the second votes as preference votes within the list. Valid votes and invalid "
    "votes are likewise the sums of the first and the second votes. The number of seats won by each party comes from "
    "the Landesamt's time series table of seats."
)
SCHWABEN_NOTE = (
    "The Landesamt's seat table shows the ÖDP's seat in Swabia under the Bayernpartei's column. The Regierung von "
    "Schwaben's press release on the final result of 20 October 2023 gives the seat to the ÖDP and none to the "
    "Bayernpartei, and the seats here follow it."
)
BEZIRKE = {
    "1": ("091", "Oberbayern", "Upper Bavaria", "Bezirkstag von Oberbayern", "District Assembly of Upper Bavaria"),
    "2": ("092", "Niederbayern", "Lower Bavaria", "Bezirkstag von Niederbayern", "District Assembly of Lower Bavaria"),
    "3": (
        "093",
        "Oberpfalz",
        "Upper Palatinate",
        "Bezirkstag der Oberpfalz",
        "District Assembly of the Upper Palatinate",
    ),
    "4": (
        "094",
        "Oberfranken",
        "Upper Franconia",
        "Bezirkstag von Oberfranken",
        "District Assembly of Upper Franconia",
    ),
    "5": (
        "095",
        "Mittelfranken",
        "Middle Franconia",
        "Bezirkstag von Mittelfranken",
        "District Assembly of Middle Franconia",
    ),
    "6": (
        "096",
        "Unterfranken",
        "Lower Franconia",
        "Bezirkstag von Unterfranken",
        "District Assembly of Lower Franconia",
    ),
    "7": ("097", "Schwaben", "Swabia", "Bezirkstag von Schwaben", "District Assembly of Swabia"),
}
PARTIES = {
    "CSU": "CSU",
    "GRÜNE": "GRÜNE",
    "FREIE WÄHLER": "FREIE WÄHLER",
    "AfD": "AFD",
    "SPD": "SPD",
    "FDP": "FDP",
    "DIE LINKE": "DIE LINKE",
    "BP": "BP",
    "ÖDP": "ÖDP",
    "Die PARTEI": "DIE PARTEI",
    "Tierschutz-partei": "TIERSCHUTZPARTEI",
    "V-Partei³": "V-PARTEI³",
    "dieBasis": "DIEBASIS",
    "Volt": "VOLT",
    "PIRATEN": "PIRATEN",
    "DIE FRANKEN": "DIE FRANKEN",
    "WiR e.V.": "WIR E.V.",
}
MINTED = {"WiR e.V.": "WiR e.V."}
SEATS = {
    "1": {"CSU": 27, "GRÜNE": 15, "FREIE WÄHLER": 13, "SPD": 7, "AfD": 9, "FDP": 3, "DIE LINKE": 1, "ÖDP": 2}
    | {"BP": 1, "Tierschutz-partei": 1, "Die PARTEI": 1, "dieBasis": 1, "Volt": 1},
    "2": {"CSU": 9, "GRÜNE": 2, "FREIE WÄHLER": 6, "SPD": 2, "AfD": 4, "ÖDP": 1},
    "3": {"CSU": 7, "GRÜNE": 2, "FREIE WÄHLER": 4, "SPD": 1, "AfD": 3},
    "4": {"CSU": 8, "GRÜNE": 2, "FREIE WÄHLER": 3, "SPD": 2, "AfD": 3},
    "5": {"CSU": 11, "GRÜNE": 5, "FREIE WÄHLER": 4, "SPD": 3, "AfD": 4, "FDP": 1, "DIE LINKE": 1, "ÖDP": 1},
    "6": {"CSU": 10, "GRÜNE": 3, "FREIE WÄHLER": 3, "SPD": 2, "AfD": 4, "FDP": 1},
    "7": {"CSU": 13, "GRÜNE": 4, "FREIE WÄHLER": 6, "SPD": 2, "AfD": 6, "FDP": 1, "DIE LINKE": 1, "ÖDP": 1},
}
SIZES = {"1": 82, "2": 24, "3": 17, "4": 18, "5": 30, "6": 23, "7": 34}
DERIVED = 1


def num(cell: object) -> int:
    return 0 if cell is None or cell == "" else int(str(cell))


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS_URL, suffix=".xlsx")
    raw.fetch(SOURCE, SERIES_URL, suffix=".pdf")
    raw.fetch(SOURCE, SCHWABEN_URL, suffix=".pdf")
    fetch_2018()


def packages() -> list[str]:
    return [KEY_2018]


def label(cell: object) -> str:
    text = re.sub(r"\s+", " ", str(cell)).strip()
    return "dieBasis" if text == "die Basis" else text


def parse() -> None:
    path = raw.cached(SOURCE, RESULTS_URL).path
    units: list[dict[str, object]] = []
    votes: list[dict[str, object]] = []
    for wk, (_, name, *_rest) in BEZIRKE.items():
        frame = pl.read_excel(path, sheet_name=name, has_header=False, infer_schema_length=0)
        header = [str(cell) for cell in frame.row(0)]
        last = next(i for i, cell in enumerate(header) if cell.startswith("Listenstimmen ungueltige"))
        first = next(i for i, cell in enumerate(header) if cell.startswith("Direktstimmen gueltige")) + 1
        columns = {"A": 6, "B": 10, "C": 13, "D": 14, "E": last, "F": last + 1}
        direct = {label(header[i]): i for i in range(first, last)}
        lists = {
            label(match.group(2)): i for i, cell in enumerate(header) if (match := re.fullmatch(r"F(\d+) (.+)", cell))
        }
        if set(direct) != set(lists) or not set(direct) <= set(PARTIES):
            raise ValueError(f"{name}: party columns differ: {sorted(direct)} against {sorted(lists)}")
        rows = [frame.row(i) for i in range(1, frame.height)]
        wahlkreis = [row for row in rows if row[4] == "WAHLKREIS"]
        stimmkreise = [row for row in rows if row[4] == "STIMMKREIS"]
        if len(wahlkreis) != 1 or len(stimmkreise) + 1 != len([row for row in rows if row[4] is not None]):
            raise ValueError(f"{name}: unexpected area kinds")
        wahlkreis_row = wahlkreis[0]
        indexes = [*columns.values(), *direct.values(), *lists.values()]
        for index in indexes:
            if sum(num(row[index]) for row in stimmkreise) != num(wahlkreis_row[index]):
                raise ValueError(f"{name}: {header[index]} does not add up to the Wahlkreis")
        for row in stimmkreise:
            if sum(num(row[i]) for i in direct.values()) != num(row[14]):
                raise ValueError(f"{row[5]}: direct votes do not add up")
            if sum(num(row[i]) for i in lists.values()) != num(row[last + 1]):
                raise ValueError(f"{row[5]}: list votes do not add up")
            units.append(
                {
                    "wk": wk,
                    "code": str(row[3]),
                    "name": str(row[5]),
                    **{key: num(row[index]) for key, index in columns.items()},
                }
            )
            for party in direct:
                votes.append(
                    {
                        "code": str(row[3]),
                        "party": party,
                        "direct": num(row[direct[party]]),
                        "list": num(row[lists[party]]),
                    }
                )
        total = {party: num(wahlkreis_row[direct[party]]) + num(wahlkreis_row[lists[party]]) for party in direct}
        seats = SIZES[wk]
        declared = SEATS[wk]
        if sum(declared.values()) != seats or not set(declared) <= set(direct):
            raise ValueError(f"{name}: seats do not add up")
        for party, count in total.items():
            if abs(declared.get(party, 0) - count * seats / sum(total.values())) >= 2:
                raise ValueError(f"{name}: {party} seats {declared.get(party, 0)} are not within two of its quota")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(units).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(votes).write_parquet(PARSED / "votes.parquet")
    bzw_2018().write()


def emit() -> Path:
    path = emit_2023()
    package.run("load", packages())
    return path


def emit_2023() -> Path:
    units = pl.read_parquet(PARSED / "units.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Bavarian district assembly (Bezirkstag) elections: 2023 in each of the seven Bezirke by Stimmkreis "
            "from the Bayerisches Landesamt für Statistik",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        bavaria = cs.polity("DE-BY")
        land_area = cs.area("de.land", "09")
        if {germany, bavaria, land_area} & cs.minted:
            raise ValueError("run de.bundeswahlleiterin first: Bavaria must exist")
        parties: dict[str, UUID] = {}
        for label in sorted(set(votes["party"])):
            parties[label] = cs.key("de.party", PARTIES[label])
            if parties[label] in cs.minted:
                if label not in MINTED:
                    raise ValueError(f"party {PARTIES[label]} must exist")
                cs.add("party", id=parties[label], kind="party", polity_id=germany)
                name(parties[label], MINTED[label], "de", "official")
        source = cs.source(PUBLISHER, RESULTS_TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=RESULTS_TITLE,
                url=PAGE_URL,
                license=LICENSE,
                license_url=None,
            )
        series = cs.source(PUBLISHER, SERIES_TITLE)
        if series in cs.minted:
            cs.add(
                "source",
                id=series,
                kind="official_body",
                publisher=PUBLISHER,
                title=SERIES_TITLE,
                url=SERIES_URL,
                license=LICENSE,
                license_url=None,
            )
        schwaben = cs.source("Regierung von Schwaben", SCHWABEN_TITLE)
        if schwaben in cs.minted:
            cs.add(
                "source",
                id=schwaben,
                kind="official_body",
                publisher="Regierung von Schwaben",
                title=SCHWABEN_TITLE,
                url=SCHWABEN_URL,
                license=None,
                license_url=None,
            )
        cs.artifact(raw.cached(SOURCE, SCHWABEN_URL), schwaben)
        cs.artifact(raw.cached(SOURCE, RESULTS_URL), source)
        cs.artifact(raw.cached(SOURCE, SERIES_URL), series)
        for scheme, noun, kind, rank in (
            (SCHEME_BEZIRK, "district", "administrative", 2),
            (SCHEME_STIMMKREIS, "constituency", "electoral_district", 3),
        ):
            cs.add("scheme", code=scheme, polity_id=bavaria, kind=kind, rank=rank, noun=noun)
        system = cs.key(f"{SOURCE}.electoral_system", "bezirkswahl")
        cs.add("electoral_system", id=system, polity_id=bavaria, rules=SYSTEM_RULES, rules_version=1)
        name(system, "Bavarian district assembly seat allocation")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM_NOTE)

        umbrella = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add("election", id=umbrella, polity_id=bavaria, kind="local", date_from=DAY, date_to=DAY, status="held")
        name(umbrella, "2023 Bavarian district elections")
        name(umbrella, "Bezirkswahlen in Bayern 2023", "de", "native")
        cs.add("slug", path=f"de/by/{DAY}-district", entity_id=umbrella)

        rows: list[dict[str, object]] = []

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, count: int, flags: int = 0) -> None:
            rows.append(
                {
                    "result_set_id": str(result_set),
                    "unit_id": str(unit),
                    "option_id": None if option is None else str(option),
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "flags": flags,
                    "value": count,
                }
            )

        cs.add("boundary_set", scheme_code=SCHEME_STIMMKREIS, vintage="2023", valid_from=DAY, valid_to=None, as_of=DAY)
        for wk, (code, german, english, body_de, body_en) in BEZIRKE.items():
            polity = cs.key(f"{SOURCE}.polity", code)
            cs.add("polity", id=polity, kind="region", iso_code=None)
            name(polity, f"Bezirk {german}", "de", "native")
            name(polity, english)
            path = f"de/by/bezirk-{slug(german)}"
            cs.add("slug", path=path, entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=bavaria, valid_from=None, valid_to=None)
            area = cs.area(SCHEME_BEZIRK, code)
            cs.add("area", id=area, scheme_code=SCHEME_BEZIRK)
            cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
            cs.add("area_parent", area_id=area, parent_id=land_area, valid_from=None, valid_to=None)
            name(area, german, "de", "official")
            name(area, english, "en", "english")
            cs.add("polity_territory", polity_id=polity, area_id=area, valid_from=None, valid_to=None)
            seats = SIZES[wk]
            body = cs.key(f"{SOURCE}.body", code)
            cs.add("body", id=body, polity_id=polity, kind="council")
            name(body, body_de, "de", "native")
            name(body, body_en)
            cs.add("body_size", body_id=body, seats=seats, valid_from=DAY, valid_to=None)

            election = cs.key(f"{SOURCE}.election", f"{code}:{DAY}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=umbrella,
                kind="general",
                date_from=DAY,
                date_to=DAY,
                status="held",
            )
            name(election, f"2023 {english} district election")
            name(election, f"Bezirkswahl {german} 2023", "de", "native")
            cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            contest = cs.key(f"{SOURCE}.contest", f"{code}:{DAY}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=seats,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"2023 {english} district assembly election, seat allocation")
            cs.add("slug", path=f"{path}/{DAY}-general/bezirkstag", entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=NOTE)
            if wk == "7":
                cs.add("note", entity_id=contest, kind="caveat", body_md=SCHWABEN_NOTE)
            sets: dict[str, UUID] = {}
            for key, contest_id in (("election", None), ("list", contest)):
                sets[key] = cs.key(f"{SOURCE}.result_set", f"{code}:{DAY}:{key}")
                cs.add(
                    "result_set",
                    id=sets[key],
                    election_id=election,
                    contest_id=contest_id,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
            options: dict[str, UUID] = {}
            present = [
                label
                for label in PARTIES
                if label in set(votes.filter(pl.col("code").is_in(units.filter(pl.col("wk") == wk)["code"]))["party"])
            ]
            for position, label in enumerate(present, 1):
                options[label] = cs.key(f"{SOURCE}.option", f"{code}:{DAY}:{label}")
                cs.add(
                    "contest_option",
                    id=options[label],
                    contest_id=contest,
                    kind="list",
                    party_id=parties[label],
                    ballot_label=label,
                    ballot_position=position,
                )
            for label, count in SEATS[wk].items():
                if count:
                    cs.add("outcome", result_set_id=sets["list"], option_id=options[label], kind="won", seats=count)
            for row in units.filter(pl.col("wk") == wk).iter_rows(named=True):
                stimmkreis = cs.area(SCHEME_STIMMKREIS, row["code"], DAY)
                cs.add("area", id=stimmkreis, scheme_code=SCHEME_STIMMKREIS)
                cs.add("area_code", area_id=stimmkreis, code=row["code"], valid_from=DAY, valid_to=None)
                cs.add("area_parent", area_id=stimmkreis, parent_id=area, valid_from=DAY, valid_to=None)
                name(stimmkreis, row["name"], "de", "official")
                unit = cs.unit(SCHEME_STIMMKREIS, row["code"], DAY)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=SCHEME_STIMMKREIS,
                    code=row["code"],
                    area_relation="equals",
                    area_id=stimmkreis,
                    kind="standard",
                    valid_from=DAY,
                    valid_to=None,
                )
                value(sets["election"], unit, None, 2, row["A"])
                value(sets["election"], unit, None, 3, row["B"])
                value(sets["list"], unit, None, 5, row["C"] + row["E"], DERIVED)
                value(sets["list"], unit, None, 4, row["D"] + row["F"], DERIVED)
                for vote in votes.filter(pl.col("code") == row["code"]).iter_rows(named=True):
                    option = options[vote["party"]]
                    value(sets["list"], unit, option, 1, vote["direct"] + vote["list"], DERIVED)
                    value(sets["list"], unit, option, 10, vote["direct"])
                    value(sets["list"], unit, option, 7, vote["list"])
        cs.frame(
            "result_value",
            pl.DataFrame(
                rows,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        return cs.write()


# The 2018 elections, built as a package. The Landesamt publishes no per-election results for the Bezirkswahlen
# before 2023, so the 2018 figures come from the result each district returning officer (Wahlkreisleiter) declared
# for the whole Bezirk. The figures are transcribed below and each row is checked against the document's text layer:
# its numbers must stand on one printed line in the order given. The Oberbayern notice is a scan whose text layer
# the Regierung made by OCR, so its values carry the page and box they were read from.

DAY_2018 = date(2018, 10, 14)
KEY_2018 = f"{SOURCE}/{DAY_2018}"
BAVARIA = "01a13bb5-c563-741b-83f7-4b7cdad043bd"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
PARTY_IDS = {
    "CSU": "01a13bb5-c6e1-7410-816e-3b27950925b9",
    "SPD": "01a13bb5-c6e1-7410-816e-3b22bd40874f",
    "FREIE WÄHLER": "01a13bb5-c6e1-7410-816e-3b29049511af",
    "GRÜNE": "01a13bb5-c6e1-7410-816e-3b24305503a8",
    "FDP": "01a13bb5-c6e1-7410-816e-3b2599d640df",
    "DIE LINKE": "01a13bb5-c6e1-7410-816e-3b28cf8ec1ca",
    "BP": "01a13bb5-c6e1-7410-816e-3b35aded064a",
    "ÖDP": "01a13bb5-c6e1-7410-816e-3b306b9f0b7e",
    "PIRATEN": "01a13bb5-c6e1-7410-816e-3b2e34cdd413",
    "AfD": "01a13bb5-c6e1-7410-816e-3b26fc9b8ec8",
    "Die PARTEI": "01a13bb5-c6e1-7410-816e-3b2cfa7dc0d3",
    "Tierschutzpartei": "01a13bb5-c6e1-7410-816e-3b2ac2f89ea1",
    "V-Partei³": "01a13bc4-54a3-704e-abec-9ce009decfd9",
    "DIE FRANKEN": "7a2dc5fc-c8e5-5f18-bacd-9f0c6578d5ea",
    "LKR": "44d553e0-09b0-5dc8-89c4-112935b38e65",
}
# Parties the archive does not hold yet: label -> (de.party key, full name).
NEW_PARTIES = {"FLO": ("FLO", "Freie Liste Oberbayern e.V.")}
SEAT_NOTE = (
    "The seats are those the district returning committee declared, overhang and balancing seats included, so the "
    "assembly can be larger than its regular size."
)
NOTE_2018 = (
    "The district returning committee's declared result gives each list's first votes (for its constituency "
    "candidates) and second votes (for its district list) for the whole Bezirk. Each list's total votes, on which "
    "the seats are shared, are stored as its votes, the first votes as candidate votes, and the second votes as "
    "preference votes within the list. The Landesamt für Statistik publishes no results by Stimmkreis for this "
    "election."
)


class Notice(NamedTuple):
    publisher: str
    title: str
    url: str
    license: str | None
    scanned: bool


OFFICIAL = "Official notice, not subject to copyright (§ 5 UrhG)"
NOTICES = {
    "091": Notice(
        "Regierung von Oberbayern",
        "Ergebnis der Wahl zum Bezirkstag von Oberbayern am 14. Oktober 2018 (Bekanntmachung der Wahlkreisleiterin "
        "vom 25. Oktober 2018)",
        "https://www.regierung.oberbayern.bayern.de/mam/dokumente/2018-10-25_bekanntmachung_ergebnis.pdf",
        OFFICIAL,
        True,
    ),
    "092": Notice(
        "Regierung von Niederbayern",
        "Bezirkstag von Niederbayern: endgültiges Wahlergebnis 2018 (Anlage zur Pressemitteilung)",
        "https://bezirk-niederbayern.de/fileadmin/user_upload/bezirk/Dateien/Bezirkstag/"
        "PM-RegvNB-Anlage-Feststellung_des_endgueltigen_Wahlergebnisses_der_Bezirkswahl_2018_Anlage.pdf",
        None,
        False,
    ),
    "094": Notice(
        "Regierung von Oberfranken",
        "Feststellung des endgültigen Wahlergebnisses der Bezirkswahl 2018 in Oberfranken (Pressemitteilung Nr. "
        "114/2018)",
        "https://regierung.oberfranken.bayern.de/mam/service/wahlen/pm2018_10_114.pdf",
        None,
        False,
    ),
    "095": Notice(
        "Wahlkreisleiter des Wahlkreises Mittelfranken",
        "Bekanntmachung des Ergebnisses der Wahl zum Bezirkstag Mittelfranken vom 14. Oktober 2018",
        "https://www.bezirk-mittelfranken.de/fileadmin/user_upload/bezirk-mittelfranken/pdf/Bezirk/Wahlen/"
        "Bekanntmachung_Wahlergebnis_Mittelfranken_2018.pdf",
        OFFICIAL,
        False,
    ),
    "096": Notice(
        "Regierung von Unterfranken",
        "Amtsblatt der Regierung von Unterfranken Nr. 21/2018: Ergebnis der Wahl zum Bezirkstag Unterfranken am "
        "14. Oktober 2018",
        "https://www.regierung.unterfranken.bayern.de/mam/Bekanntmachungen/amtsblatt/2018/nr21-18.pdf",
        OFFICIAL,
        False,
    ),
    "097": Notice(
        "Bezirk Schwaben",
        "Bezirkswahl am 14. Oktober 2018 im Wahlkreis Schwaben: endgültiges Endergebnis nach Stimmenanteil",
        "https://www.bezirk-schwaben.de/media/4053/ergebnis_stimmenanteil.pdf",
        None,
        False,
    ),
}
SCHWABEN_SEATS_URL = "https://bezirk-schwaben.de/media/4090/2018_bezirks-info_17_wahlen.pdf"
SCHWABEN_SEATS_TITLE = "Bezirks-Info 17: Künftig 36 Vertreter im Schwabenparlament (2018)"


class Row(NamedTuple):
    label: str
    number: int | None  # the list's number (Wahlkreisvorschlag Nr.), where the notice prints it
    first: int
    second: int
    total: int
    seats: int
    page: int  # zero-based page of the notice the row stands on
    line: tuple[str, ...]  # what the printed line holds, in order, from the first vote count on


# Per Bezirk: rows, then accounting (measure -> (value, page, printed text, derived)).
ROWS_2018: dict[str, list[Row]] = {
    "091": [
        Row("CSU", 1, 734849, 730251, 1465100, 26, 1, ("734.849", "730.251", "12.234", "1.465.100")),
        Row("SPD", 2, 236801, 213323, 450124, 8, 1, ("236.801", "213.323", "4.071", "450.124")),
        Row("FREIE WÄHLER", 3, 292221, 275171, 567392, 10, 1, ("292.221", "275.171", "5.054", "567.392")),
        Row("GRÜNE", 4, 498373, 509119, 1007492, 18, 1, ("498.373", "509.119", "7.430", "1.007.492")),
        Row("FDP", 5, 153430, 151519, 304949, 5, 1, ("153.430", "151.519", "2.028", "304.949")),
        Row("DIE LINKE", 6, 77708, 79685, 157393, 3, 1, ("77.708", "79.685", "1.220", "157.393")),
        Row("BP", 7, 63174, 62471, 125645, 2, 1, ("63.174", "62.471", "696", "125.645")),
        Row("ÖDP", 8, 61698, 59524, 121222, 2, 1, ("61.698", "59.524", "1.112", "121.222")),
        Row("PIRATEN", 9, 11780, 13701, 25481, 0, 1, ("11.780", "13.701", "194", "25.481")),
        Row("AfD", 10, 200028, 198684, 398712, 7, 1, ("200.028", "198.684", "2.113", "398.712")),
        Row("FLO", 11, 13953, 10890, 24843, 0, 1, ("13.953", "10.890", "88", "24.843")),
        Row("LKR", 12, 461, 1043, 1504, 0, 1, ("461", "1.043", "28", "1.504")),
        Row("Die PARTEI", 13, 11723, 16775, 28498, 0, 1, ("11.723", "16.775", "174", "28.498")),
        Row("Tierschutzpartei", 14, 9109, 24496, 33605, 1, 2, ("9.109", "24.496", "298", "33.605")),
    ],
    "092": [
        Row("CSU", 1, 235280, 264343, 499623, 9, 0, ("235.280", "264.343", "499.623")),
        Row("SPD", 2, 57926, 44266, 102192, 2, 0, ("57.926", "44.266", "102.192")),
        Row("FREIE WÄHLER", 3, 115096, 95185, 210281, 4, 0, ("115.096", "95.185", "210.281")),
        Row("GRÜNE", 4, 62740, 61841, 124581, 2, 0, ("62.740", "61.841", "124.581")),
        Row("FDP", 5, 24172, 24827, 48999, 1, 0, ("24.172", "24.827", "48.999")),
        Row("DIE LINKE", 6, 14487, 15262, 29749, 1, 0, ("14.487", "15.262", "29.749")),
        Row("BP", 7, 20871, 22209, 43080, 1, 0, ("20.871", "22.209", "43.080")),
        Row("ÖDP", 8, 25287, 21433, 46720, 1, 0, ("25.287", "21.433", "46.720")),
        Row("PIRATEN", 9, 325, 2541, 2866, 0, 0, ("325", "2.541", "2.866")),
        Row("AfD", 10, 83780, 82216, 165996, 3, 0, ("83.780", "82.216", "165.996")),
        Row("Tierschutzpartei", 11, 1409, 7081, 8490, 0, 0, ("1.409", "7.081", "8.490")),
    ],
    "094": [
        Row("CSU", 1, 221385, 215103, 436488, 8, 1, ("221.385", "215.103", "436.488", "37,24", "8")),
        Row("SPD", 2, 97536, 85478, 183014, 3, 1, ("97.536", "85.478", "183.014", "15,61", "3")),
        Row("FREIE WÄHLER", 3, 75972, 75321, 151293, 3, 1, ("75.972", "75.321", "151.293", "12,91", "3")),
        Row("GRÜNE", 4, 72341, 74548, 146889, 3, 1, ("72.341", "74.548", "146.889", "12,53", "3")),
        Row("FDP", 5, 22707, 24076, 46783, 1, 1, ("22.707", "24.076", "46.783", "3,99", "1")),
        Row("DIE LINKE", 6, 14047, 16238, 30285, 1, 1, ("14.047", "16.238", "30.285", "2,58", "1")),
        Row("BP", 7, 6209, 4464, 10673, 0, 1, ("6.209", "4.464", "10.673", "0,91", "–")),
        Row("ÖDP", 8, 6176, 6179, 12355, 0, 1, ("6.176", "6.179", "12.355", "1,05", "–")),
        Row("PIRATEN", 9, 1243, 3724, 4967, 0, 1, ("1.243", "3.724", "4.967", "0,42", "–")),
        Row("DIE FRANKEN", 10, 5863, 7178, 13041, 0, 1, ("5.863", "7.178", "13.041", "1,11", "–")),
        Row("AfD", 11, 63159, 62697, 125856, 2, 1, ("63.159", "62.697", "125.856", "10,74", "2")),
        Row("Die PARTEI", 12, 1493, 6305, 7798, 0, 1, ("1.493", "6.305", "7.798", "0,67", "–")),
        Row("V-Partei³", 13, 615, 2194, 2809, 0, 1, ("615", "2.194", "2.809", "0,24", "–")),
    ],
    "095": [
        Row("CSU", 1, 278065, 296347, 574412, 11, 1, ("278.065", "296.347", "574.412")),
        Row("SPD", 2, 122799, 117882, 240681, 4, 1, ("122.799", "117.882", "240.681")),
        Row("FREIE WÄHLER", 3, 117414, 95979, 213393, 4, 1, ("117.414", "95.979", "213.393")),
        Row("GRÜNE", 4, 160921, 162537, 323458, 6, 1, ("160.921", "162.537", "323.458")),
        Row("FDP", 5, 37703, 39903, 77606, 1, 1, ("37.703", "39.903", "77.606")),
        Row("DIE LINKE", 6, 44947, 43082, 88029, 2, 1, ("44.947", "43.082", "88.029")),
        Row("ÖDP", 7, 17213, 16521, 33734, 1, 1, ("17.213", "16.521", "33.734")),
        Row("PIRATEN", 8, 11966, 11115, 23081, 0, 1, ("11.966", "11.115", "23.081")),
        Row("DIE FRANKEN", 9, 15448, 13181, 28629, 1, 1, ("15.448", "13.181", "28.629")),
        Row("AfD", 10, 88028, 87308, 175336, 3, 1, ("88.028", "87.308", "175.336")),
        Row("Die PARTEI", 11, 1141, 8545, 9686, 0, 1, ("1.141", "8.545", "9.686")),
        Row("V-Partei³", 12, 3946, 3382, 7328, 0, 1, ("3.946", "3.382", "7.328")),
    ],
    "096": [
        Row("CSU", 1, 259703, 280651, 540354, 10, 0, ("259.703", "280.651", "540.354")),
        Row("SPD", 2, 82130, 67481, 149611, 3, 0, ("82.130", "67.481", "149.611")),
        Row("FREIE WÄHLER", 3, 97908, 82512, 180420, 3, 0, ("97.908", "82.512", "180.420")),
        Row("GRÜNE", 4, 114087, 114420, 228507, 4, 0, ("114.087", "114.420", "228.507")),
        Row("FDP", 5, 35687, 33754, 69441, 1, 0, ("35.687", "33.754", "69.441")),
        Row("DIE LINKE", 6, 25108, 24629, 49737, 1, 0, ("25.108", "24.629", "49.737")),
        Row("BP", 7, 7720, 7035, 14755, 0, 0, ("7.720", "7.035", "14.755")),
        Row("ÖDP", 8, 11426, 8965, 20391, 0, 0, ("11.426", "8.965", "20.391")),
        Row("PIRATEN", 9, 1078, 3819, 4897, 0, 0, ("1.078", "3.819", "4.897")),
        Row("DIE FRANKEN", 10, 6299, 7954, 14253, 0, 0, ("6.299", "7.954", "14.253")),
        Row("AfD", 11, 69871, 68497, 138368, 2, 0, ("69.871", "68.497", "138.368")),
        Row("Die PARTEI", 12, 4239, 6804, 11043, 0, 0, ("4.239", "6.804", "11.043")),
        Row("Tierschutzpartei", 13, 4340, 7036, 11376, 0, 0, ("4.340", "7.036", "11.376")),
        Row("V-Partei³", 14, 1862, 2522, 4384, 0, 0, ("1.862", "2.522", "4.384")),
    ],
    "097": [
        Row("CSU", None, 348591, 335022, 683613, 13, 0, ("348.591", "335.022", "683.613")),
        Row("SPD", None, 76227, 73147, 149374, 3, 0, ("76.227", "73.147", "149.374")),
        Row("FREIE WÄHLER", None, 130217, 149484, 279701, 5, 0, ("130.217", "149.484", "279.701")),
        Row("GRÜNE", None, 158272, 151034, 309306, 6, 0, ("158.272", "151.034", "309.306")),
        Row("FDP", None, 45846, 40295, 86141, 2, 0, ("45.846", "40.295", "86.141")),
        Row("DIE LINKE", None, 27943, 27664, 55607, 1, 0, ("27.943", "27.664", "55.607")),
        Row("BP", None, 18865, 16078, 34943, 1, 0, ("18.865", "16.078", "34.943")),
        Row("ÖDP", None, 19692, 18705, 38397, 1, 0, ("19.692", "18.705", "38.397")),
        Row("PIRATEN", None, 2004, 4278, 6282, 0, 0, ("2.004", "4.278", "6.282")),
        Row("AfD", None, 105127, 103144, 208271, 4, 0, ("105.127", "103.144", "208.271")),
        Row("LKR", None, 77, 395, 472, 0, 0, ("77", "395", "472")),
        Row("Die PARTEI", None, 1935, 7700, 9635, 0, 0, ("1.935", "7.700", "9.635")),
        Row("V-Partei³", None, 5845, 5040, 10885, 0, 0, ("5.845", "5.040", "10.885")),
    ],
}


class Figure(NamedTuple):
    measure: int
    value: int
    page: int
    printed: tuple[str, ...]  # the printed numbers the value is, or is the sum of
    total_line: bool = False  # True for the line of list totals, checked as a run of numbers


# Turnout and ballot figures: registered voters (2) and voters (3) go to the election's set, valid (4) and invalid
# (5) votes to the list set. A value with two printed numbers is the sum of first and second votes, marked derived.
ACCOUNTING_2018: dict[str, list[Figure]] = {
    "091": [
        Figure(2, 3199086, 1, ("3.199.086",)),
        Figure(3, 2388311, 1, ("2.388.311",)),
        Figure(4, 4711960, 1, ("4.711.960",)),
        Figure(5, 62967, 1, ("62.967",)),
    ],
    "092": [
        Figure(2, 933273, 0, ("933.273",)),
        Figure(3, 650020, 0, ("650.020",)),
        Figure(4, 1282577, 0, ("641.373", "641.204", "1.282.577"), True),
        Figure(5, 17440, 0, ("8.638", "8.802")),
    ],
    "094": [
        Figure(2, 837034, 0, ("837.034",)),
        Figure(3, 593635, 0, ("593.635",)),
        Figure(4, 1172251, 0, ("1.172.251",)),
        Figure(5, 14978, 0, ("14.978",)),
    ],
    "095": [
        Figure(2, 1273066, 0, ("1.273.066",)),
        Figure(3, 909431, 0, ("909.431",)),
        Figure(4, 1795373, 1, ("899.591", "895.782", "1.795.373"), True),
        Figure(5, 22639, 0, ("9.434", "13.205")),
    ],
    "096": [
        Figure(2, 1011793, 0, ("1.011.793",)),
        Figure(3, 728604, 0, ("728.604",)),
        Figure(4, 1437537, 0, ("721.458", "716.079", "1.437.537"), True),
        Figure(5, 19625, 0, ("19.625",)),
    ],
    "097": [
        Figure(4, 1872627, 0, ("940.641", "931.986", "1.872.627"), True),
    ],
}
# Text each notice prints for its seats, checked against the notice (whitespace collapsed).
SEAT_TEXT_2018 = {
    "091": [
        "CSU 26",
        "SPD 8",
        "FREIE WÄHLER 10",
        "GRÜNE 18",
        "FDP 5",
        "DIE LINKE 3",
        "BP 2",
        "ODP 2",
        "PIRATEN 0",
        "AfD 7",
        "FLO 0",
        "LKR 0",
        "Die PARTEI 0",
        "Tierschutzpartei 1",
        "Insgesamt: 82",
    ],
    "094": ["Von den mithin 21 Sitzen"],
    "095": [
        "Die 33 Sitze im Bezirkstag",
        "CSU: 11 Sitze",
        "GRÜNE: 6 Sitze",
        "SPD: 4 Sitze",
        "FREIE WÄHLER: 4 Sitze",
        "AfD: 3 Sitze",
        "DIE LINKE: 2 Sitze",
        "FDP: 1 Sitz",
        "ÖDP: 1 Sitz",
        "DIE FRANKEN 1 Sitz",
    ],
    "096": [
        "Nr. 1 CSU 10 Sitze",
        "Nr. 2 SPD 3 Sitze",
        "Nr. 3 FW FREIE WÄHLER 3 Sitze",
        "Nr. 4 GRÜNE 4 Sitze",
        "Nr. 5 FDP 1 Sitz",
        "Nr. 6 DIE LINKE 1 Sitz",
        "Nr. 11 AfD 2 Sitze",
        "Insgesamt 24 Sitze",
    ],
}
SCHWABEN_SEAT_TEXT = [
    "von derzeit 27 auf 36 Bezirksrätinnen und Bezirksräte",
    "Die 13 Direktmandate eroberte sich die CSU",
    "Die Grünen entsenden künftig sechs Vertreter, die Freien Wähler fünf, die Afd 4, SPD 3 und FDP 2",
    "Mit je einem Sitz sind die Linke, Bayernpartei und ÖDP vertreten",
]
# Niederbayern's notice lists the members elected; each list's seats are its members there.
NIEDERBAYERN_PARTIES = {"CSU", "SPD", "FREIE WÄHLER", "GRÜNE", "FDP", "DIE LINKE", "BP", "ÖDP", "AfD"}
Box = tuple[float, float, float, float]
PAD = 3.0  # points around a number's words, so its crop shows the whole figure


def _words(page: pymupdf.Page) -> list[list[tuple[str, Box]]]:
    """The page's words in printed lines, each line's words from left to right."""
    lines: dict[int, list[tuple[str, Box]]] = {}
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        lines.setdefault(round((y0 + y1) / 8), []).append((text, (x0, y0, x1, y1)))
    return [sorted(lines[key], key=lambda word: word[1][0]) for key in sorted(lines)]


def _run(words: list[tuple[str, Box]], printed: tuple[str, ...]) -> list[Box] | None:
    """The boxes of the printed numbers where they stand in this order, as whole words or split across words."""
    for start in range(len(words)):
        boxes: list[Box] = []
        i = start
        for target in printed:
            text = ""
            box: Box | None = None
            while i < len(words) and len(text) < len(target):
                word, (x0, y0, x1, y1) = words[i]
                text += word
                box = (x0, y0, x1, y1) if box is None else (min(box[0], x0), min(box[1], y0), max(box[2], x1), y1)
                i += 1
            if text.rstrip(".,;:") != target or box is None:
                break
            boxes.append(box)
        else:
            return boxes
    return None


def locate(page: pymupdf.Page, printed: tuple[str, ...]) -> list[Box]:
    """Find the printed numbers on one line of the page, or on two adjacent lines when the scan splits a row."""
    lines = _words(page)
    for i in range(len(lines)):
        for window in (lines[i], sorted(lines[i] + lines[i + 1], key=lambda w: w[1][0]) if i + 1 < len(lines) else []):
            boxes = _run(window, printed)
            if boxes is not None:
                w, h = page.rect.width, page.rect.height
                return [
                    (
                        max(0, (b[0] - PAD) / w),
                        max(0, (b[1] - PAD) / h),
                        min(1, (b[2] + PAD) / w),
                        min(1, (b[3] + PAD) / h),
                    )
                    for b in boxes
                ]
    raise ValueError(f"page {(page.number or 0) + 1}: {' '.join(printed)} not found on one line")


def _text(document: pymupdf.Document) -> str:
    return " ".join(" ".join(page.get_text("text").split()) for page in document)


def _check_2018(code: str, document: pymupdf.Document) -> None:
    rows = ROWS_2018[code]
    for row in rows:
        if row.first + row.second != row.total:
            raise ValueError(f"{code} {row.label}: first and second votes do not add up to the total")
        locate(document[row.page], row.line)
    for figure in ACCOUNTING_2018[code]:
        if figure.total_line:
            first, second, total = (int(text.replace(".", "")) for text in figure.printed)
            if (first, second, total) != (
                sum(row.first for row in rows),
                sum(row.second for row in rows),
                sum(row.total for row in rows),
            ) or total != figure.value:
                raise ValueError(f"{code}: the list totals do not add up to the printed totals")
        elif sum(int(text.replace(".", "")) for text in figure.printed) != figure.value:
            raise ValueError(f"{code}: measure {figure.measure} does not add up")
        for text in figure.printed:
            locate(document[figure.page], (text,))
    text = _text(document)
    for phrase in SEAT_TEXT_2018.get(code, []):
        if phrase not in text:
            raise ValueError(f"{code}: the notice does not print {phrase!r}")
    if code == "092":
        # The two headings, (Direktkandidaten) and (Listenkandidaten), stand in parentheses too.
        elected = re.findall(r"\(([^)]+)\)", text.split("Gewählte Stimmkreisbewerber", 1)[1])
        counted = {label: elected.count(label) for label in NIEDERBAYERN_PARTIES}
        if counted != {row.label: row.seats for row in rows if row.seats} or len(elected) - sum(counted.values()) != 2:
            raise ValueError(f"092: elected members {counted} do not match the seats")


def fetch_2018() -> None:
    for notice in NOTICES.values():
        raw.fetch(SOURCE, notice.url, suffix=".pdf")
    raw.fetch(SOURCE, SCHWABEN_SEATS_URL, suffix=".pdf")


def _id(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def _held(cache: str, key: str) -> str:
    """The id the 2023 changeset gave an entity it minted under this cache key."""
    return str(uuid5(package.NAMESPACE, f"{cache}\x1f{key}"))


def bzw_2018() -> Package:
    p = Package(KEY_2018, SOURCE, "The 2018 Bavarian district assembly elections by Bezirk, from the district notices")
    p.external |= {BAVARIA, GERMANY, SCHEME_BEZIRK, *PARTY_IDS.values()}
    sources: dict[str, str] = {}
    for code, notice in NOTICES.items():
        source = sources[code] = _id("source", notice.url)
        p.add(
            "source",
            id=source,
            kind="official_body",
            publisher=notice.publisher,
            title=notice.title,
            url=notice.url,
            license=notice.license,
            license_url=None,
            attribution=notice.publisher,
        )
    seats_source = _id("source", SCHWABEN_SEATS_URL)
    p.add(
        "source",
        id=seats_source,
        kind="official_body",
        publisher="Bezirk Schwaben",
        title=SCHWABEN_SEATS_TITLE,
        url=SCHWABEN_SEATS_URL,
        license=None,
        license_url=None,
        attribution="Bezirk Schwaben",
    )
    p.artifact(raw.cached(SOURCE, SCHWABEN_SEATS_URL), seats_source)
    seat_text = _text(pymupdf.open(raw.cached(SOURCE, SCHWABEN_SEATS_URL).path))
    for phrase in SCHWABEN_SEAT_TEXT:
        if phrase not in seat_text:
            raise ValueError(f"097: the newsletter does not print {phrase!r}")
    parties = dict(PARTY_IDS)
    for label, (value, full) in NEW_PARTIES.items():
        party = parties[label] = _id("party", value)
        p.add("party", id=party, kind="party", polity_id=BAVARIA)
        p.name(party, full, "de", "official")
        p.add("external_id", entity_id=party, scheme="de.party", value=value)

    umbrella = _id("election", DAY_2018)
    p.add("election", id=umbrella, polity_id=BAVARIA, kind="local", date_from=DAY_2018, date_to=DAY_2018, status="held")
    p.name(umbrella, "2018 Bavarian district elections", "en", "official")
    p.name(umbrella, "Bezirkswahlen in Bayern 2018", "de", "native", False)
    p.add("slug", path=f"de/by/{DAY_2018}-district", entity_id=umbrella, is_current=True)

    def value(result_set: str, unit: str, option: str | None, measure: int, count: int, flags: int = 0) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=count,
        )

    for code, notice in NOTICES.items():
        wk = next(key for key, entry in BEZIRKE.items() if entry[0] == code)
        _, german, english, _, _ = BEZIRKE[wk]
        artifact = raw.cached(SOURCE, notice.url)
        artifact_id = p.artifact(artifact, sources[code])
        document = pymupdf.open(artifact.path)
        _check_2018(code, document)
        polity = _held(f"external_id:{SOURCE}.polity", code)
        area = _held(f"area:{SCHEME_BEZIRK}@None", code)
        body = _held(f"external_id:{SOURCE}.body", code)
        p.external |= {polity, area, body}
        unit = _id("unit", SCHEME_BEZIRK, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=polity,
            scheme_code=SCHEME_BEZIRK,
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
            valid_to=None,
        )
        election = _id("election", code, DAY_2018)
        p.add(
            "election",
            id=election,
            polity_id=polity,
            parent_id=umbrella,
            kind="general",
            date_from=DAY_2018,
            date_to=DAY_2018,
            status="held",
        )
        p.name(election, f"2018 {english} district election", "en", "official")
        p.name(election, f"Bezirkswahl {german} 2018", "de", "native", False)
        path = f"de/by/bezirk-{slug(german)}/{DAY_2018}-general"
        p.add("slug", path=path, entity_id=election, is_current=True)
        rows = ROWS_2018[code]
        contest = _id("contest", code, DAY_2018)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=sum(row.seats for row in rows),
            default_result_kind="counted",
        )
        p.name(contest, f"2018 {english} district assembly election, seat allocation", "en", "official")
        p.add("slug", path=f"{path}/bezirkstag", entity_id=contest, is_current=True)
        p.add(
            "note", id=_id("note", contest, "context"), entity_id=contest, kind="context", body_md=NOTE_2018, lang="en"
        )
        p.add("note", id=_id("note", contest, "seats"), entity_id=contest, kind="context", body_md=SEAT_NOTE, lang="en")
        sets = {
            key: _id("result_set", code, DAY_2018, key)
            for key in ("election", "list")
            if key == "list" or any(f.measure in (2, 3) for f in ACCOUNTING_2018[code])
        }
        for key, result_set in sets.items():
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest if key == "list" else None,
                stage="official",
                kind="counted",
                source_id=sources[code],
                status="active",
                is_default=True,
                completeness=1.0,
            )
        pages: dict[int, str] = {}
        if notice.scanned:
            for number in sorted({row.page for row in rows} | {f.page for f in ACCOUNTING_2018[code]}):
                image_key = f"pages/{SOURCE}/{DAY_2018}-{code}/{number + 1}.jpg"
                pixmap = document[number].get_pixmap(dpi=100, colorspace=pymupdf.csGRAY)
                target = DATA / image_key
                target.parent.mkdir(parents=True, exist_ok=True)
                pixmap.save(target, jpg_quality=80)
                pages[number] = _id("page", code, DAY_2018, number + 1)
                p.add(
                    "source_page",
                    id=pages[number],
                    artifact_id=artifact_id,
                    page_number=number + 1,
                    image_key=image_key,
                    width=pixmap.width,
                    height=pixmap.height,
                )

        def place(result_set: str, option: str | None, measure: int, page: int, box: Box) -> None:
            if page not in pages:
                return
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=pages[page],
                source_id=sources[code],
                x0=box[0],
                y0=box[1],
                x1=box[2],
                y1=box[3],
            )

        for figure in ACCOUNTING_2018[code]:
            result_set = sets["election" if figure.measure in (2, 3) else "list"]
            derived = len(figure.printed) == 2
            value(result_set, unit, None, figure.measure, figure.value, DERIVED if derived else 0)
            if not derived:
                place(result_set, None, figure.measure, figure.page, locate(document[figure.page], figure.printed)[-1])
        for row in rows:
            option = _id("option", code, DAY_2018, row.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=parties[row.label],
                ballot_label=row.label,
                ballot_position=row.number,
            )
            if row.seats:
                p.add("outcome", result_set_id=sets["list"], option_id=option, kind="won", seats=row.seats)
            value(sets["list"], unit, option, 1, row.total)
            value(sets["list"], unit, option, 10, row.first)
            value(sets["list"], unit, option, 7, row.second)
            boxes = locate(document[row.page], row.line)
            totals = boxes[3] if len(row.line) == 4 and code == "091" else boxes[2]
            for measure, box in ((10, boxes[0]), (7, boxes[1]), (1, totals)):
                place(sets["list"], option, measure, row.page, box)
    return p
