"""Poland's Sejm election of 5 November 1922 by constituency, from the Central Statistical Office's account.

Główny Urząd Statystyczny published the official returns of the Sejm and Senate elections of 5 and 12 November 1922
as volume 8 of Statystyka Polski (Warsaw, 1926), a 141-page scan without a text layer. Table 12 prints, for each of
the 64 Sejm constituencies, the polling districts, inhabitants, persons entitled to vote, votes cast, votes
invalidated by the district and constituency commissions, valid votes, and each list's valid votes, first for the
whole constituency and then by powiat, town, and rural gmina. Table 14 prints the seats each list won in each
constituency and on the state lists. The constituency total rows were read from the page images, and every
constituency's list votes add up to its valid votes, its votes cast less the constituency commission's
invalidations equal its valid votes, and the constituencies add up to the volume's own totals by voivodeship and for
the country.

Lists numbered 1 to 22 were linked to the state list of the same number and carry the same number and name in every
constituency; lists numbered from 23 up are each constituency's own, named in the table's footnotes. Lists that won
fewer than 100 votes in a constituency share one column, and a footnote gives each one's votes.

There are no constituency areas of the Second Republic in the archive, so the constituencies are reporting units
with no area, in their own scheme with dated codes.
"""

import json
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "pl.sejm_1922"
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
ELECTION = f"{SOURCE}/1922"
DAY = date(1922, 11, 5)
POLAND = str(uuid5(NAMESPACE, "polity\x1fPL"))
# The Sejm, minted by pl.parlament.
SEJM = "af8e4a13-58aa-5383-b995-012130bf1b55"
URL = "https://pbc.biaman.pl/Content/49289/PDF/Statystyka%20wyborow%20do%20sejmu%20i%20senatu.pdf"
PAGE = "https://pbc.biaman.pl/dlibra/publication/50772/edition/49289"
SCHEME = "pl.okreg_sejm_ii_rp"
PATH = f"pl/{DAY}-general"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8


class List(NamedTuple):
    native: str
    english: str


# The state lists and the constituency lists linked to them (page XVIII of the volume).
LISTS: dict[int, List] = {
    1: List("Polskie Stronnictwo Ludowe", "Polish People's Party"),
    2: List("Polska Partja Socjalistyczna", "Polish Socialist Party"),
    3: List('P. S. L. „Wyzwolenie" i Lewica Ludowa', "Polish People's Party \"Liberation\" and the People's Left"),
    4: List("Ogólny Żydowski Związek Robotniczy w Polsce", "General Jewish Labour Union in Poland"),
    5: List("Komunistyczny Związek Proletarjatu Miast i Wsi", "Communist Union of the Proletariat of Town and Country"),
    6: List("Związek Rad Ludowych", "Union of People's Councils"),
    7: List("Narodowa Partja Robotnicza", "National Workers' Party"),
    8: List("Chrześcijański Związek Jedności Narodowej", "Christian Union of National Unity"),
    10: List("Unja Narodowo-Państwowa", "National-State Union"),
    11: List("Żydowski Robotniczy Komitet Wyborczy", "Jewish Workers' Electoral Committee"),
    12: List('Narodowe Zjednoczenie Ludowe „Polskie Centrum"', 'National People\'s Union "Polish Centre"'),
    13: List('Polskie Stronnictwo Ludowe „Lewica"', 'Polish People\'s Party "Left"'),
    14: List("Centrum Mieszczańskie", "Burghers' Centre"),
    15: List("Chłopskie Stronnictwo Radykalne", "Peasant Radical Party"),
    16: List("Blok Mniejszości Narodowych Rzeczypospolitej Polskiej", "Bloc of National Minorities"),
    17: List("Komitet Zjednoczonych Stronnictw Narodowo-Żydowskich", "Committee of the United Jewish National Parties"),
    18: List(
        "Komitet Wyborczy Inwalidów i Zdemobilizowanych Wojskowych",
        "Electoral Committee of War Invalids and Demobilized Soldiers",
    ),
    20: List("Żydowski Demokratyczny Blok Ludowy", "Jewish Democratic People's Bloc"),
    22: List("Państwowe Zjednoczenie na Kresach", "State Union in the Borderlands"),
}

# The seat of each constituency as Table 3 names it.
SEATS_OF = {
    1: "M. st. Warszawa", 2: "Warszawa powiat", 3: "Siedlce", 4: "Ostrów", 5: "Białystok", 6: "Grodno",
    7: "Łomża", 8: "Ciechanów", 9: "Płock", 10: "Włocławek", 11: "Łowicz", 12: "Błonie", 13: "M. Łódź",
    14: "Łódź powiat", 15: "Konin", 16: "Kalisz", 17: "Częstochowa-Radomsko", 18: "Piotrków", 19: "Radom",
    20: "Kielce", 21: "Będzin", 22: "Sandomierz", 23: "Iłża (Wierzbnik)", 24: "Łuków", 25: "Biała Podlaska",
    26: "Lublin", 27: "Zamość", 28: "Krasnystaw", 29: "Tczew", 30: "Grudziądz", 31: "Toruń", 32: "Bydgoszcz",
    33: "Gniezno", 34: "M. Poznań", 35: "Poznań Wschód i Zachód", 36: "Szamotuły", 37: "Ostrów", 38: "Huta Królewska",
    39: "Katowice", 40: "Cieszyn", 41: "M. Kraków", 42: "Kraków powiat", 43: "Wadowice", 44: "Nowy Sącz",
    45: "Tarnów", 46: "Jasło", 47: "Rzeszów", 48: "Przemyśl", 49: "Sambor", 50: "M. Lwów", 51: "Lwów powiat",
    52: "Stryj", 53: "Stanisławów", 54: "Tarnopol", 55: "Złoczów", 56: "Kowel", 57: "Łuck", 58: "Krzemieniec",
    59: "Brześć", 60: "Pińsk", 61: "Nowogródek", 62: "Lida", 63: "Wilno", 64: "Święciany",
}  # fmt: skip

# Seats won from the constituency lists (Table 14, page 118 of the volume), by list number. Table 14 puts the seats
# of constituency lists 23 and up in two columns, "Związek narodowo-żydowski" and "Partje ukraińskie"; they are
# entered here under the list number that won them in each constituency.
SEATS: dict[int, dict[int, int]] = {
    1: {2: 3, 5: 1, 8: 7, 16: 2, 20: 1}, 2: {2: 1, 8: 4}, 3: {8: 4}, 4: {8: 3, 16: 1},
    5: {1: 1, 8: 2, 12: 1, 16: 2}, 6: {1: 1, 8: 2, 16: 1}, 7: {8: 4}, 8: {3: 1, 8: 4}, 9: {2: 1, 3: 1, 8: 3},
    10: {2: 1, 3: 1, 8: 2, 16: 1}, 11: {1: 1, 2: 1, 8: 3}, 12: {1: 1, 2: 1, 3: 1, 8: 3}, 13: {7: 2, 8: 3, 16: 2},
    14: {1: 1, 3: 1, 8: 3, 16: 1}, 15: {2: 1, 3: 1, 8: 3, 16: 1}, 16: {1: 1, 2: 1, 3: 2, 8: 2, 16: 1},
    17: {2: 1, 3: 2, 8: 3}, 18: {1: 1, 8: 3, 16: 1}, 19: {1: 2, 2: 1, 3: 1, 8: 3}, 20: {3: 3, 8: 2},
    21: {2: 2, 5: 1, 8: 2, 16: 1}, 22: {1: 2, 3: 2, 8: 1}, 23: {1: 1, 2: 1, 3: 3, 8: 1}, 24: {1: 1, 3: 2, 8: 2, 15: 1},
    25: {1: 1, 8: 2, 16: 1}, 26: {1: 1, 2: 1, 3: 2, 8: 1, 16: 1}, 27: {3: 1, 15: 2, 16: 2},
    28: {2: 1, 3: 2, 15: 1, 16: 1}, 29: {7: 1, 8: 4}, 30: {7: 1, 8: 2, 16: 1}, 31: {7: 1, 8: 4},
    32: {7: 2, 8: 3, 16: 1}, 33: {7: 2, 8: 3}, 34: {8: 4}, 35: {7: 1, 8: 3}, 36: {7: 1, 8: 3, 16: 1},
    37: {1: 1, 7: 2, 8: 3}, 38: {8: 3, 16: 2}, 39: {2: 1, 7: 1, 8: 1, 16: 2}, 40: {2: 1, 7: 1, 8: 4, 16: 1},
    41: {2: 1, 8: 2}, 42: {1: 1, 2: 3, 3: 2, 8: 2}, 43: {1: 2, 2: 1, 3: 1, 8: 2, 12: 1}, 44: {1: 3, 2: 1, 8: 1, 12: 1},
    45: {1: 5, 12: 2}, 46: {1: 4, 12: 1, 13: 1}, 47: {1: 5, 8: 1}, 48: {1: 2, 2: 1, 8: 1, 13: 1, 17: 1},
    49: {1: 3, 8: 1, 17: 1}, 50: {2: 1, 8: 2, 17: 1}, 51: {1: 4, 2: 1, 8: 1, 17: 1}, 52: {1: 1, 2: 1, 8: 1, 17: 2},
    53: {1: 1, 8: 2, 17: 3}, 54: {1: 5, 8: 3, 17: 2}, 55: {1: 4, 8: 3, 17: 2}, 56: {16: 5}, 57: {16: 6}, 58: {16: 5},
    59: {2: 1, 3: 1, 16: 3}, 60: {2: 3, 3: 1, 16: 1}, 61: {3: 2, 16: 4}, 62: {1: 1, 3: 3, 8: 1, 16: 2},
    63: {1: 1, 2: 1, 3: 1, 8: 2}, 64: {3: 4, 16: 2},
}  # fmt: skip
# Table 14's "Związek narodowo-żydowski" (41, 47) and "Partje ukraińskie" (49, 52, 53) seats: constituency, seats.
JEWISH_NATIONAL = {41: 1, 47: 1}
UKRAINIAN = {49: 1, 52: 1, 53: 3}
# Table 14's column totals of seats from constituency lists.
SEAT_TOTALS = {1: 58, 2: 34, 3: 41, 5: 2, 7: 15, 8: 134, 12: 6, 13: 2, 15: 4, 16: 55, 17: 13, 20: 1}

METHOD = (
    "The constituency's total row in Table 12 of Statystyka Polski vol. 8 (Główny Urząd Statystyczny, 1926), read "
    "from the page image: persons entitled to vote, votes cast, the votes the constituency commission invalidated, "
    "valid votes, and each list's valid votes. The list votes add up to the valid votes, and the votes cast less the "
    "constituency commission's invalidations equal the valid votes, as in the volume's totals by voivodeship. The "
    "table also prints the votes invalidated earlier by the district commissions, which are not part of the votes "
    "cast it prints and which the volume's introduction calls inconsistent from one commission to the next; they are "
    "not stored. Seats are those Table 14 gives the constituency lists; the 72 state-list seats are not stored here."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id() -> str:
    return key("source", "gus")


# Footnotes that say the list had no name.
NAMELESS = {"bez nazwy", "bezimienna"}


def party_id(label: str, constituency: int | None = None) -> str:
    """A list's party, keyed by its name; a one-word generic name ("Bezpartyjna") is keyed within its constituency."""
    text = " ".join(label.split()).casefold().replace("ceirej", "cejrej")
    return key("party", text) if constituency is None or " " in text else key("party", constituency, text)


def unit(number: int) -> str:
    return key("unit", f"{DAY}:{number}")


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def read() -> dict[int, dict[str, object]]:
    found = json.loads(Path(__file__).with_name("sejm_1922.json").read_text())
    return {int(k): v for k, v in found["constituencies"].items()}


def packages() -> list[str]:
    return [GEO, REGISTRY, ELECTION]


class Option(NamedTuple):
    number: str
    label: str
    party: str | None
    votes: int
    seats: int


def options(number: int, row: dict[str, object]) -> list[Option]:
    """The constituency's lists, with combined columns split by their footnotes where every member has one."""
    lists = row["lists"]
    footnotes = row.get("footnotes") or []
    local = row.get("local_names") or {}
    assert isinstance(lists, dict) and isinstance(footnotes, list) and isinstance(local, dict)
    noted = {str(f["list"]): f for f in footnotes}
    won = dict(SEATS[number])
    for extra in (JEWISH_NATIONAL, UKRAINIAN):
        if number in extra:
            column = row["minority_list"]
            assert isinstance(column, int)
            won[column] = won.get(column, 0) + extra[number]
    out: list[Option] = []

    def local_name(n: str) -> str:
        text = local.get(n) or noted.get(n, {}).get("name")
        return " ".join(str(text).split()) if text else ""

    for column, votes in lists.items():
        assert isinstance(votes, int)
        members = column.split("+")
        if len(members) > 1:
            parts = [noted.get(m) for m in members]
            if all(p is not None and isinstance(p.get("votes"), int) for p in parts) and (
                sum(p["votes"] for p in parts if p is not None) == votes
            ):
                for m, p in zip(members, parts, strict=True):
                    assert p is not None
                    out.append(single(number, m, p["votes"], local_name, won))
                continue
            raise ValueError(f"constituency {number}: column {column} has no footnote for every list")
        out.append(single(number, column, votes, local_name, won))
    given = {o.number: o.seats for o in out if o.seats}
    if given != {str(k): v for k, v in won.items()}:
        raise ValueError(f"constituency {number}: seats {given} do not match Table 14 {won}")
    return out


def single(number: int, column: str, votes: int, local_name: object, won: dict[int, int]) -> Option:
    n = int(column)
    seats = won.get(n, 0)
    if n in LISTS:
        return Option(column, LISTS[n].native, party_id(LISTS[n].native), votes, seats)
    assert callable(local_name)
    label = str(local_name(column))
    if not label or label.casefold() in NAMELESS:
        # The table prints the list's votes but no name.
        return Option(column, f"Lista nr {n}", None, votes, seats)
    return Option(column, label, party_id(label, number), votes, seats)


def check(data: dict[int, dict[str, object]]) -> dict[str, object]:
    """Tie every constituency to its own figures and to the volume's totals; return the national totals."""
    if sorted(data) != list(range(1, 65)):
        raise ValueError("Table 12 has 64 constituencies")
    printed = json.loads(Path(__file__).with_name("sejm_1922.json").read_text())["national"]
    national = {"entitled": 0, "cast": 0, "invalid_constituency": 0, "valid": 0}
    by_list: dict[int, int] = {}
    seats: dict[int, int] = {}
    for number, row in data.items():
        opts = options(number, row)
        valid, cast, invalid = row["valid"], row["cast"], row["invalid_constituency"]
        assert isinstance(valid, int) and isinstance(cast, int) and isinstance(invalid, int)
        if sum(o.votes for o in opts) != valid:
            raise ValueError(f"constituency {number}: lists add up to {sum(o.votes for o in opts)}, not {valid}")
        if cast - invalid != valid:
            raise ValueError(f"constituency {number}: {cast} cast less {invalid} invalid is not {valid} valid")
        if sum(o.seats for o in opts) != sum(SEATS[number].values()) + JEWISH_NATIONAL.get(number, 0) + UKRAINIAN.get(
            number, 0
        ):
            raise ValueError(f"constituency {number}: seats do not add up")
        for field in national:
            value = row[field]
            assert isinstance(value, int)
            national[field] += value
        for o in opts:
            if int(o.number) in LISTS:
                by_list[int(o.number)] = by_list.get(int(o.number), 0) + o.votes
                seats[int(o.number)] = seats.get(int(o.number), 0) + o.seats
    for field, label in (
        ("entitled", "entitled"),
        ("cast", "cast"),
        ("invalid_constituency", "invalid"),
        ("valid", "valid"),
    ):
        if national[field] != printed[label]:
            raise ValueError(
                f"the constituencies add up to {national[field]} {field}, the volume prints {printed[label]}"
            )
    # Lists of fewer than 100 votes share a column; their footnoted votes are split out and counted here too.
    for number, total in printed["lists"].items():
        if by_list.get(int(number), 0) != total:
            raise ValueError(f"list {number}: the constituencies add up to {by_list.get(int(number), 0)}, not {total}")
    if {k: v for k, v in seats.items() if v} != SEAT_TOTALS:
        raise ValueError(f"seats by list {seats} do not match Table 14 {SEAT_TOTALS}")
    return {"national": national, "lists": by_list}


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Central Statistical Office's account of the Sejm and Senate elections of 1922 as a source, the Sejm "
        "electoral system of 1922, and the lists that ran for the Sejm",
    )
    p.external.add(POLAND)
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher="Główny Urząd Statystyczny Rzeczypospolitej Polskiej",
        title="Statystyka wyborów do Sejmu i Senatu odbytych w dniu 5 i 12 listopada 1922 roku (Statystyka Polski, "
        "tom VIII, Warszawa 1926)",
        url=PAGE,
        license="Public domain: an official publication of 1926 whose authors died more than 70 years ago",
        license_url=PAGE,
        attribution="Główny Urząd Statystyczny; scan by Podlaska Biblioteka Cyfrowa",
        notes="A scan without a text layer; the figures were read from the page images.",
    )
    p.artifact(raw.cached(SOURCE, URL), source_id())
    system = key("electoral_system", "sejm-1922")
    p.add(
        "electoral_system",
        id=system,
        polity_id=POLAND,
        rules={"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        rules_version=1,
    )
    p.name(system, "Sejm closed-list D'Hondt system (1922)", "en", "official")
    p.add(
        "note",
        id=p.id("note", system, "methodology"),
        entity_id=system,
        kind="methodology",
        body_md=(
            "372 of the 444 members were elected in 64 constituencies of 3 to 14 seats by the D'Hondt method over "
            "closed lists, and the other 72 from state lists, shared among the state lists in proportion to the "
            "seats their linked constituency lists won (Ordynacja wyborcza do Sejmu of 28 July 1922, Dz.U. 1922 "
            "nr 66 poz. 590). Constituency lists that declared themselves linked to a state list carried its "
            "number."
        ),
        lang="en",
    )
    data = read()
    seen: set[str] = set()
    for number, row in sorted(data.items()):
        for o in options(number, row):
            if o.party is None or o.party in seen:
                continue
            seen.add(o.party)
            p.add("party", id=o.party, kind="party", polity_id=POLAND)
            p.name(o.party, o.label, "pl", "official")
            if int(o.number) in LISTS:
                p.name(o.party, LISTS[int(o.number)].english, "en", "english", False)
    return p


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The 64 Sejm constituencies of 1922 as reporting units with dated codes and no areas",
    )
    p.external.add(POLAND)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=POLAND,
        kind="electoral_district",
        rank=1,
        noun="Sejm constituency of the Second Republic",
    )
    for number, seat in SEATS_OF.items():
        id = unit(number)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=POLAND,
            scheme_code=SCHEME,
            code=f"{DAY}:{number}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(id, f"Okręg wyborczy nr {number} ({seat})", "pl", "official")
        p.name(id, f"Constituency {number} ({seat})", "en", "english", False)
    return p


def election() -> Package:
    p = Package(
        ELECTION,
        SOURCE,
        "Poland's Sejm election of 5 November 1922 in 64 constituencies: entitled voters, votes cast, invalid and "
        "valid votes, list votes, and seats, read from the Central Statistical Office's tables",
        depends=[GEO, REGISTRY],
    )
    p.external |= {POLAND, SEJM}
    data = read()
    check(data)
    p.uses(raw.cached(SOURCE, URL))
    system = key("electoral_system", "sejm-1922")
    election = p.id("election", DAY)
    p.add(
        "election",
        id=election,
        polity_id=POLAND,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election, "1922 Polish parliamentary election", "en", "official")
    p.name(election, "Wybory do Sejmu 1922", "pl", "native", False)
    p.add("slug", path=PATH, entity_id=election, is_current=True)
    values: list[tuple[str, str, str | None, int, int]] = []
    for number, row in sorted(data.items()):
        seat = SEATS_OF[number]
        contest = p.id("contest", DAY, number)
        opts = options(number, row)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=SEJM,
            magnitude=sum(o.seats for o in opts),
            electoral_system_id=system,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Constituency {number} ({seat})", "en", "official")
        p.name(contest, f"Okręg wyborczy nr {number} ({seat})", "pl", "native", False)
        p.add("slug", path=f"{PATH}/sejm/{number}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", DAY, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        page = row["page"]
        p.add(
            "note",
            id=p.id("note", result_set, "methodology"),
            entity_id=result_set,
            kind="methodology",
            body_md=f"{METHOD} The total row is on page {page} of the PDF.",
            lang="en",
        )
        caveat = row.get("caveat")
        if caveat:
            p.add(
                "note",
                id=p.id("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=str(caveat),
                lang="en",
            )
        here = unit(number)
        for measure, field in (
            (REGISTERED, "entitled"),
            (CAST, "cast"),
            (INVALID, "invalid_constituency"),
            (VALID, "valid"),
        ):
            value = row[field]
            assert isinstance(value, int)
            values.append((result_set, here, None, measure, value))
        for position, o in enumerate(sorted(opts, key=lambda o: int(o.number)), 1):
            option = p.id("option", DAY, number, o.number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=o.party,
                ballot_position=position,
                ballot_label=f"{o.number}. {o.label}",
            )
            values.append((result_set, here, option, VOTES, o.votes))
            if o.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=o.seats)
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
        ),
    )
    return p


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())
