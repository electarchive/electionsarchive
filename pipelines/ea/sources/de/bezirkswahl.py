import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "de.bezirkswahl"
VERSION = "1"
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


def emit() -> Path:
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
