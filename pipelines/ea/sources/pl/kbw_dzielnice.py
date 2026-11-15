import io
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Package
from ea.store import connect

SOURCE = "pl.kbw_dzielnice"
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze: wybory do rad dzielnic m.st. Warszawy"
PAGE = "https://danewyborcze.kbw.gov.pl/"
BASE = "https://danewyborcze.kbw.gov.pl/dane"
PARSED = raw.parsed(SOURCE)
ELECTIONS = {
    2002: date(2002, 10, 27),
    2006: date(2006, 11, 12),
    2010: date(2010, 11, 21),
    2014: date(2014, 11, 16),
    2018: date(2018, 10, 21),
    2024: date(2024, 4, 7),
}
CANDIDATES = {
    2002: "2002/2002-kand-rady.xlsx",
    2006: "2006/2006-kand-rady-new.xlsx",
    2010: "2010/samo2010/2010-kand-rady.xlsx",
    2014: "2014/samorzad/2014-kand-rady.xlsx",
    2018: "2018/samorzad/2018-kand-rady.xlsx",
    2024: "2024/samorzad/kandydaci_rady_dzielnic_csv.zip",
}
PRECINCTS = {
    2014: "2014/samorzad/1461141888_227c4ffa2ca5a7c591813a3a55e92378.xls",
    2018: "2018/samorzad/samo2018-dz-obw.zip",
    2024: "2024/samorzad/protokoly_po_obwodach_rada_dzielnicy_csv.zip",
}
CONSTITUENCIES = "2024/samorzad/okregi_rady_dzielnic_csv.zip"
LEVEL = {2002: "dzielnica Warszawy", 2006: "DZ", 2010: "rada dzielnicy", 2014: "dzielnica Warszawy", 2018: "dzielnica"}
COLUMNS = {
    2002: {"okr": "Okręg", "list": "Nr listy", "pos": "Nr pozycji", "votes": "Głosy", "seat": "Mandat", "sex": "Płeć"},
    2006: {"okr": "Okręg", "list": "Lista", "pos": "Pozycja", "votes": "Gł. na kand.", "seat": "Mandat", "sex": "Płeć"},
    2010: {
        "okr": "Okręg",
        "list": "Nr listy",
        "pos": "Pozycja",
        "votes": "L. głosów",
        "seat": "Wybrany",
        "sex": "Plec",
    },
    2014: {"okr": "Nr okr.", "list": "Nr listy", "pos": "Poz.", "votes": "Głosy", "seat": "Mandat", "sex": "Płeć"},
    2018: {
        "okr": "Nr okr.",
        "list": "Nr listy",
        "pos": "Poz.",
        "votes": "Liczba głosów",
        "seat": "Mandat",
        "sex": "Płeć",
    },
}
GIVEN = {
    2002: ("1. imię", "2. imię"),
    2006: ("Imiona",),
    2010: ("Imiona",),
    2014: ("Imiona",),
    2018: ("Imię", "Drugie imię"),
}
SEX = {"K": "female", "M": "male", "Kobieta": "female", "Mężczyzna": "male"}
ELECTED = ["T", "B", "G", "L", "Tak"]
VOTES, REGISTERED, BALLOTS, VALID, INVALID, ISSUED = 1, 2, 3, 4, 5, 14
POLAND = "dcc34aa3-b916-549e-951a-bc7bd3c8c7db"
METHOD = (
    "List votes, declared seats, and elected members of one constituency of a Warsaw district council from the "
    "Krajowe Biuro Wyborcze's candidate file for the local elections. Voters choose one candidate, so each list's "
    "votes are the sum of its candidates' votes, and each elected member is the candidate the file marks as "
    "elected. Preference votes for individual candidates are not stored."
)
METHOD_PRECINCT = (
    "List votes and accounting figures for each polling district (obwód) of one constituency of a Warsaw district "
    "council from the Krajowe Biuro Wyborcze's polling-district protocols for the local elections; declared seats "
    "and elected members are from its candidate file. Voters choose one candidate, so each list's votes are the "
    "sum of its candidates' votes. Registered voters are the electoral roll, ballots cast are the ballot papers "
    "taken from the box, and invalid ballots are the invalid ballot papers plus the invalid votes on valid ones. Preference votes for "
    "individual candidates are not stored."
)
SYSTEM = (
    "Seats are shared in each constituency by the D'Hondt method among the lists of committees, with no "
    "threshold (Ordynacja wyborcza do rad gmin, rad powiatów i sejmików województw, then Kodeks wyborczy, which "
    "apply the rules for gmina councils to Warsaw's district councils, Statut m.st. Warszawy). Elected members "
    "are the candidates with the most votes on each list. D'Hondt reproduces the declared seats in %s of the "
    "constituencies."
)
SYSTEM_2006 = (
    "Seats are shared in each constituency by the D'Hondt method among the lists of committees, with no "
    "threshold, and lists could join in blocks that took seats as one list before sharing them (Ordynacja "
    "wyborcza do rad gmin, rad powiatów i sejmików województw, as amended on 6 September 2006). The file does not "
    "say which lists formed blocks, so the declared seats are stored as printed and D'Hondt reproduces them in "
    "%s of the constituencies."
)


def fetch() -> None:
    for files in (CANDIDATES, PRECINCTS, {0: CONSTITUENCIES}):
        for path in files.values():
            raw.fetch(SOURCE, f"{BASE}/{path}", suffix=Path(path).suffix)


def _csv(path: str, member: str | None = None) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, f"{BASE}/{path}").path)
    names = [name for name in archive.namelist() if name.endswith(".csv")] if member is None else [member]
    [name] = names
    frame = pl.read_csv(io.BytesIO(archive.read(name)), separator=";", infer_schema_length=0)
    return frame.rename({column: column.lstrip("﻿") for column in frame.columns})


def _number(value: str | None) -> int | None:
    return None if value in (None, "") else int(float(value))


def candidates(year: int) -> pl.DataFrame:
    if year == 2024:
        frame = _csv(CANDIDATES[year])
        return frame.select(
            year=pl.lit(year, pl.Int32),
            teryt=pl.col("TERYT Dzielnicy"),
            okr=pl.col("Nr okręgu").cast(pl.Int64),
            list=pl.col("Nr listy").cast(pl.Int64),
            committee=pl.col("Nazwa komitetu").str.strip_chars(),
            pos=pl.col("Pozycja na liście").cast(pl.Int64),
            name=pl.col("Nazwisko i imiona").str.strip_chars(),
            sex=pl.col("Płeć").replace_strict(SEX),
            votes=pl.col("Liczba głosów").cast(pl.Int64, strict=False),
            seat=pl.col("Czy uzyskał mandat"),
            valid=pl.col("Liczba głosów ważnych oddanych w okręgu").cast(pl.Int64),
            registered=pl.lit(None, pl.Int64),
            cast=pl.lit(None, pl.Int64),
            body=pl.col("Rada"),
        )
    spec = COLUMNS[year]
    frame = pl.read_excel(raw.cached(SOURCE, f"{BASE}/{CANDIDATES[year]}").path, infer_schema_length=0)
    frame = frame.rename({column: " ".join(column.split()) for column in frame.columns})
    frame = frame.filter(pl.col("Szczebel") == LEVEL[year])
    given = pl.concat_str([pl.col(c) for c in GIVEN[year]], separator=" ", ignore_nulls=True)
    out = frame.select(
        year=pl.lit(year, pl.Int32),
        teryt=pl.col("TERYT"),
        okr=pl.col(spec["okr"]).cast(pl.Int64),
        list=pl.col(spec["list"]).cast(pl.Int64),
        committee=pl.col("Komitet").str.strip_chars(),
        pos=pl.col(spec["pos"]).cast(pl.Int64),
        name=pl.concat_str([pl.col("Nazwisko"), given], separator=" ", ignore_nulls=True).str.strip_chars(),
        sex=pl.col(spec["sex"]).replace_strict(SEX, default=None),
        votes=pl.col(spec["votes"]).cast(pl.Int64),
        seat=pl.col(spec["seat"]),
        body=pl.lit(None, pl.String),
    )
    if year == 2006:
        return out.with_columns(
            valid=frame["Ważne"].cast(pl.Int64),
            registered=frame["Upr."].cast(pl.Int64),
            cast=frame["Odd."].cast(pl.Int64),
        )
    if year == 2014:
        return out.with_columns(
            valid=frame["Ważne"].cast(pl.Int64), registered=pl.lit(None, pl.Int64), cast=pl.lit(None, pl.Int64)
        )
    return out.with_columns(
        valid=pl.lit(None, pl.Int64), registered=pl.lit(None, pl.Int64), cast=pl.lit(None, pl.Int64)
    )


def _fold(text: str) -> str:
    return " ".join(text.replace("„", '"').replace("”", '"').split()).casefold()


def precincts_2024() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    frame = _csv(PRECINCTS[2024])
    header = frame.columns
    first = next(i for i, column in enumerate(header) if column.startswith("Głosy na listę nr"))
    lists = [
        (i, int(re.findall(r"\d+", header[i])[0]))
        for i in range(first, len(header))
        if header[i].startswith("Głosy na listę nr")
    ]
    units, votes = [], []
    for row in frame.iter_rows():
        key = {"year": 2024, "teryt": row[header.index("Teryt Gminy")], "okr": int(row[1]), "obw": int(row[0])}
        units.append(
            key
            | {
                "registered": _number(row[header.index("Liczba wyborców uprawnionych do głosowania")]),
                "issued": _number(row[header.index("Liczba wyborców, którym wydano karty do głosowania")]),
                "cast": _number(row[header.index("Liczba kart wyjętych z urny")]),
                "invalid": _number(row[header.index("Liczba głosów nieważnych")]),
                "cards": _number(row[header.index("Liczba kart nieważnych")]),
                "valid": _number(
                    row[header.index("Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów")]
                ),
            }
        )
        for i, number in lists:
            if row[i] not in (None, ""):
                votes.append(key | {"list": number, "votes": int(row[i])})
    return units, votes


def precincts_2018() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, f"{BASE}/{PRECINCTS[2018]}").path)
    units, votes = [], []
    for member in sorted(archive.namelist()):
        data = archive.read(member)
        for sheet in fastexcel.read_excel(data).sheet_names:
            frame = pl.read_excel(io.BytesIO(data), sheet_name=sheet, has_header=False, infer_schema_length=0)
            header = [str(value or "") for value in frame.row(0)]
            okr = int(re.findall(r"\d+", sheet)[-1])

            def at(prefix: str) -> int:
                return next(i for i, text in enumerate(header) if text.startswith(prefix))

            columns = {
                "registered": at("Liczba wyborców uprawnionych do głosowania"),
                "issued": at("Liczba wyborców, którym wydano karty do głosowania"),
                "cast": at("Liczba kart wyjętych z urny"),
                "invalid": at("Liczba głosów nieważnych"),
                "cards": at("Liczba kart nieważnych"),
                "valid": at("Liczba głosów ważnych oddanych łącznie"),
            }
            lists, current = [], None
            for i, text in enumerate(header):
                if match := re.match(r"Lista nr (\d+) ", text):
                    current = int(match.group(1))
                elif text == "Razem" and current is not None:
                    lists.append((i, current))
                    current = None
            for row in frame.slice(1).iter_rows():
                if row[1] is None or not str(row[2]).isdigit():
                    continue
                key = {"year": 2018, "teryt": row[1], "okr": okr, "obw": int(row[2])}
                units.append(key | {name: _number(row[i]) for name, i in columns.items()})
                for i, number in lists:
                    if row[i] not in (None, ""):
                        votes.append(key | {"list": number, "votes": int(row[i])})
    return units, votes


def precincts_2014(cands: pl.DataFrame) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    frame = pl.read_excel(raw.cached(SOURCE, f"{BASE}/{PRECINCTS[2014]}").path, has_header=False, infer_schema_length=0)
    header = [str(value or "") for value in frame.row(0)]
    first = header.index("Głosy ważne") + 1
    last = next(i for i, text in enumerate(header) if text.startswith("Pkt "))
    numbers = {
        (row["teryt"], row["okr"], _fold(row["committee"])): row["list"]
        for row in cands.filter(pl.col("year") == 2014)
        .select("teryt", "okr", "committee", "list")
        .unique()
        .iter_rows(named=True)
    }
    units, votes = [], []
    for row in frame.slice(1).iter_rows():
        if row[0] is None:
            continue
        key = {"year": 2014, "teryt": row[0], "okr": int(row[2]), "obw": int(row[3])}
        units.append(
            key
            | {
                "registered": _number(row[6]),
                "issued": _number(row[11]),
                "cast": _number(row[22]),
                "invalid": _number(row[26]),
                "cards": _number(row[24]),
                "valid": _number(row[27]),
            }
        )
        for i in range(first, last):
            if row[i] not in (None, ""):
                votes.append(
                    key | {"list": numbers[(key["teryt"], key["okr"], _fold(header[i]))], "votes": int(row[i])}
                )
    return units, votes


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    cands = pl.concat([(frame := candidates(year)).select(sorted(frame.columns)) for year in ELECTIONS])
    cands.write_parquet(PARSED / "candidates.parquet")
    print("candidates", cands.height, flush=True)
    units, votes = [], []
    for found in (precincts_2014(cands), precincts_2018(), precincts_2024()):
        units += found[0]
        votes += found[1]
    pl.DataFrame(units, infer_schema_length=None).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(votes, infer_schema_length=None).write_parquet(PARSED / "votes.parquet")
    print("units", len(units), "votes", len(votes), flush=True)
    seats = _csv(CONSTITUENCIES).select(
        teryt=pl.col("TERYT Dzielnicy"),
        okr=pl.col("Numer okręgu").cast(pl.Int64),
        seats=pl.col("Liczba mandatów").cast(pl.Int64),
    )
    seats.write_parquet(PARSED / "seats2024.parquet")
    parse_packages()


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def _dhondt(votes: dict[int, int], seats: int) -> dict[int, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda i: (votes[i] / (won[i] + 1), -i))
        won[best] += 1
    return {i: n for i, n in won.items() if n}


def places() -> dict[str, dict[str, str]]:
    with connect() as conn:
        rows = conn.execute(
            """select ac.code, a.id, pt.polity_id, sl.path from area a
               join area_code ac on ac.area_id = a.id
               join polity_territory pt on pt.area_id = a.id
               join slug sl on sl.entity_id = pt.polity_id
               where a.scheme_code = 'pl.dzielnica_warszawy'"""
        ).fetchall()
        names = dict(
            conn.execute(
                """select ac.code, n.text from area a join area_code ac on ac.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   where a.scheme_code = 'pl.dzielnica_warszawy'"""
            ).fetchall()
        )
        elections = {
            row[0]: str(row[1])
            for row in conn.execute(
                "select date_from, id from election where kind = 'local' and polity_id = %s", (UUID(POLAND),)
            )
        }
    out = {
        code[:6]: {"area": str(area), "polity": str(polity), "path": path, "name": names[code]}
        for code, area, polity, path in rows
    }
    out["elections"] = {str(day): elections[day] for day in (ELECTIONS.values())}
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/{day}" for day in ELECTIONS.values()]


def build(year: int) -> Package:
    day = ELECTIONS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Warsaw district council elections by constituency"
        + (" and polling district" if year in PRECINCTS else "")
        + " from the Krajowe Biuro Wyborcze",
    )
    held = places()
    election = held["elections"][str(day)]
    p.external |= {POLAND, election, *(row["area"] for code, row in held.items() if code != "elections")}
    p.external |= {row["polity"] for code, row in held.items() if code != "elections"}
    source = key("source", "kbw")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license="Official public record, no license stated",
        attribution="Źródło danych: Państwowa Komisja Wyborcza, Krajowe Biuro Wyborcze.",
    )
    p.artifact(raw.cached(SOURCE, f"{BASE}/{CANDIDATES[year]}"), source)
    if year in PRECINCTS:
        p.artifact(raw.cached(SOURCE, f"{BASE}/{PRECINCTS[year]}"), source)
    for code, rank, kind, noun in (
        ("pl.okreg_dzielnicy", 5, "electoral_district", "Warsaw district council constituency"),
        ("pl.obwod_dzielnicy", 6, "precinct", "polling district"),
    ):
        p.add("scheme", code=code, polity_id=POLAND, kind=kind, rank=rank, noun=noun, code_system=None)

    cands = pl.read_parquet(PARSED / "candidates.parquet").filter(pl.col("year") == year)
    bodies = (
        pl.read_parquet(PARSED / "candidates.parquet").filter(pl.col("year") == 2024).select("teryt", "body").unique()
    )
    native = dict(bodies.iter_rows())
    cands = cands.with_columns(elected=pl.col("seat").is_in(ELECTED))
    units = pl.read_parquet(PARSED / "units.parquet").filter(pl.col("year") == year)
    votes = pl.read_parquet(PARSED / "votes.parquet").filter(pl.col("year") == year)

    contests: list[tuple[str, int]] = sorted(
        cands.select("teryt", "okr").unique().iter_rows(), key=lambda row: (row[0], row[1])
    )
    exact = 0
    results = {}
    for teryt, okr in contests:
        part = cands.filter((pl.col("teryt") == teryt) & (pl.col("okr") == okr))
        totals = {r["list"]: r["votes"] for r in part.group_by("list").agg(pl.col("votes").sum()).iter_rows(named=True)}
        declared = {
            r["list"]: r["n"]
            for r in part.filter(pl.col("elected")).group_by("list").agg(pl.len().alias("n")).iter_rows(named=True)
        }
        results[(teryt, okr)] = totals
        exact += _dhondt(totals, sum(declared.values())) == declared
    system = key("system", "dzielnica-2006" if year == 2006 else "dzielnica")
    rules: dict[str, object] = {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}}
    if year == 2006:
        rules["pooling"] = "alliance"
    p.add("electoral_system", id=system, polity_id=POLAND, valid_from=None, valid_to=None, rules=rules, rules_version=1)
    p.name(
        system,
        "Warsaw district council open-list D'Hondt system" + (" with blocks (2006)" if year == 2006 else ""),
        "en",
        "official",
    )
    p.add(
        "note",
        id=key("note", system, year),
        entity_id=system,
        kind="methodology",
        body_md=(SYSTEM_2006 if year == 2006 else SYSTEM) % f"{exact} of {len(contests)}",
        lang="en",
    )
    path = f"pl/{day}-local"
    parties: dict[str, str] = {}

    def party(committee: str) -> str:
        label = _fold(committee)
        if label not in parties:
            id = parties[label] = key("party", slug(label))
            p.add("party", id=id, kind="party", polity_id=POLAND)
            p.name(id, " ".join(committee.split()), "pl", "official")
        return parties[label]

    unit_rows: dict[str, str] = {}
    for teryt, okr in contests:
        place = held[teryt]
        label = place["name"]
        body = key("body", teryt)
        if body not in unit_rows:
            unit_rows[body] = ""
            p.add("body", id=body, polity_id=place["polity"], kind="council")
            p.name(body, f"{label} District Council", "en", "official")
            p.name(body, native.get(teryt, f"Rada Dzielnicy {label} m.st. Warszawy"), "pl", "native", False)
        part = cands.filter((pl.col("teryt") == teryt) & (pl.col("okr") == okr))
        seats = part.filter(pl.col("elected")).height
        tag = f"{day}:{teryt}:{okr}"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            magnitude=seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"{label} district council, constituency {okr}", "en", "official")
        p.name(contest, f"Rada Dzielnicy {label} m.st. Warszawy, okręg wyborczy nr {okr}", "pl", "native", False)
        p.add(
            "slug",
            path=f"{path}/rada-dzielnicy/{place['path'].rsplit('/', 1)[1]}/{okr}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD_PRECINCT if year in PRECINCTS else METHOD,
            lang="en",
        )
        options: dict[int, str] = {}
        committees = part.group_by("list").agg(pl.col("committee").first()).sort("list")
        for row in committees.iter_rows(named=True):
            option = options[row["list"]] = key("option", tag, row["list"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(row["committee"]),
                ballot_position=row["list"],
                ballot_label=" ".join(row["committee"].split()),
            )
        won = Counter(part.filter(pl.col("elected"))["list"].to_list())
        for number, n in won.items():
            p.add("outcome", result_set_id=result_set, option_id=options[number], kind="won", seats=n)
        for row in part.sort("list", "pos").iter_rows(named=True):
            ptag = f"{tag}:{row['list']}:{row['pos']}"
            person = key("person", ptag)
            p.add("person", id=person, gender=row["sex"])
            p.name(person, display(row["name"], "pl"), "pl", "official")
            candidacy = key("candidacy", ptag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[row["list"]],
                role="list_member",
                list_position=row["pos"],
                ballot_name=row["name"],
                is_independent=False,
            )
            if row["elected"]:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[row["list"]],
                    candidacy_id=candidacy,
                    kind="elected",
                )
        counted = sum(results[(teryt, okr)].values())
        valid = part["valid"][0] if part["valid"][0] is not None else counted
        if year in PRECINCTS:
            here = units.filter((pl.col("teryt") == teryt) & (pl.col("okr") == okr))
            by_unit = {row["obw"]: row for row in here.iter_rows(named=True)}
            cast_votes: dict[int, list[tuple[int, int]]] = {}
            for row in votes.filter((pl.col("teryt") == teryt) & (pl.col("okr") == okr)).iter_rows(named=True):
                cast_votes.setdefault(row["obw"], []).append((row["list"], row["votes"]))
            district = key("unit", "okreg", tag)
            p.add(
                "reporting_unit",
                id=district,
                polity_id=POLAND,
                scheme_code="pl.okreg_dzielnicy",
                code=tag,
                area_relation="within",
                area_id=place["area"],
                kind="standard",
            )
            for obw, row in sorted(by_unit.items()):
                unit = key("unit", "obwod", day, teryt, obw)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=POLAND,
                    scheme_code="pl.obwod_dzielnicy",
                    code=f"{day}:{teryt}:{obw}",
                    parent_id=district,
                    area_relation="within",
                    area_id=place["area"],
                    kind="standard",
                )
                row = row | {"invalid": None if row["invalid"] is None else row["invalid"] + (row["cards"] or 0)}
                for measure, name in (
                    (REGISTERED, "registered"),
                    (ISSUED, "issued"),
                    (BALLOTS, "cast"),
                    (VALID, "valid"),
                    (INVALID, "invalid"),
                ):
                    if row[name] is not None:
                        p.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=row[name],
                        )
                for number, count in cast_votes.get(obw, []):
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=options[number],
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=count,
                    )
        else:
            unit = key("unit", "okreg", tag)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=POLAND,
                scheme_code="pl.okreg_dzielnicy",
                code=tag,
                area_relation="within",
                area_id=place["area"],
                kind="standard",
            )
            figures = [(VALID, valid)]
            registered, cast = part["registered"][0], part["cast"][0]
            if registered is not None:
                figures.append((REGISTERED, registered))
            if cast is not None:
                figures.extend([(BALLOTS, cast), (INVALID, cast - valid)])
            for measure, value in figures:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            for number, count in results[(teryt, okr)].items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[number],
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=count,
                )
        if part["valid"][0] is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=part["valid"][0])
    if year in PRECINCTS:
        sums = votes.group_by("teryt", "okr", "obw").agg(pl.col("votes").sum().alias("listed"))
        off = units.join(sums, on=["teryt", "okr", "obw"]).filter(pl.col("valid") != pl.col("listed")).height
        if off:
            p.accept(
                "accounting.votes_valid",
                f"The protocols print more valid votes than the lists' votes in {off} polling districts "
                "(the list votes match the candidate file's totals for every constituency); stored as printed",
            )
    return p


def parse_packages() -> None:
    for year in ELECTIONS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())
