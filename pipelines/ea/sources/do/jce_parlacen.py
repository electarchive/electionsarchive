"""The Dominican deputies to the Central American Parliament (PARLACEN) in 2016, 2020, and 2024.

The Board allots the twenty seats nationally, by D'Hondt among the parties' national totals. For 2024 it prints the
seat table (relación general definitiva) with every party's votes and seats; for 2016 and 2020 only its lists of the
elected, whose first candidate of each winning party carries the party's national total, are published.
"""

import functools
import re
import subprocess
from datetime import date
from typing import NamedTuple

import fastexcel
import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.do import jce

TABLE_2024 = jce.entry(30615)
SEATS = 20
BODY = jce.key("body", "parlacen")
SYSTEM = jce.system("parlacen")
NATIONAL = "do.nacion"
DAYS = ("2016-05-15", "2020-07-05", "2024-05-19")

METHOD = (
    "Counted by the Junta Central Electoral. The twenty Dominican deputies to the Central American Parliament are "
    "allotted nationally among the parties by D'Hondt from each party's national total of votes. Each total is that "
    "of the party's list, whether it ran alone or in an alliance, so the figures are stored for the party that "
    "presented the list."
)
METHOD_TABLE = (
    " The 2024 figures are the national seat table of the definitive results tables (relación general definitiva del "
    "cómputo electoral): every party's total votes and its seats. The Board prints no ballots-cast, valid, or null "
    "figures for this contest."
)
METHOD_LISTS = (
    " For this election the Board publishes only its list of the elected, which gives the votes of each list's "
    "candidates at the D'Hondt quotient that elected them (the first candidate carries the party's whole national "
    "total); the totals stored are those of the parties that won seats, and the totals of the parties that won none "
    "are not published. The alternates (suplentes) are listed without votes."
)


class Member(NamedTuple):
    party: str
    own_party: str | None
    name: str
    role: str
    sex: str | None
    born: date | None
    position: int | None


class Result(NamedTuple):
    votes: dict[str, int]
    seats: dict[str, int]
    members: list[Member]
    skipped: int
    source: str


def fetch() -> None:
    raw.fetch(jce.SOURCE, TABLE_2024, suffix=".pdf")


def _int(text: str | None) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _rows(url: str, sheet: str) -> list[dict[str, str | None]]:
    book = fastexcel.read_excel(str(raw.cached(jce.SOURCE, url).path))
    frame = book.load_sheet_by_name(sheet, dtypes="string").to_polars()
    return [
        {k: (v.strip() if v is not None and v.strip() not in ("", "NULL") else None) for k, v in row.items()}
        for row in frame.iter_rows(named=True)
    ]


@functools.cache
def table_2024() -> tuple[dict[str, int], dict[str, int]]:
    """Each party's votes and seats from the first page of the 2024 seat tables, alliance leads standing for them."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(jce.SOURCE, TABLE_2024).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    page = text.split("\f")[0]
    if "DIPUTADOS PARLACEN" not in page or not re.search(r"Total de Esca[ñn]os\s*:\s*20\b", page):
        raise ValueError("the first page of the 2024 seat tables is not the PARLACEN table")
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for line in page.splitlines():
        found = re.match(r"^\s*\d\d\s+(.+?)\s{2,}([\d,]+)\s+.*?(\d+)\s*$", line)
        if found is None:
            continue
        code = jce._alliance_lead(found[1])
        if code in votes:
            raise ValueError(f"the 2024 PARLACEN table lists {code} twice")
        votes[code] = jce._int(found[2])
        seats[code] = int(found[3])
    if sum(seats.values()) != SEATS or not votes:
        raise ValueError(f"the 2024 PARLACEN table allots {sum(seats.values())} seats, not {SEATS}")
    return votes, seats


def _result_2024() -> Result:
    votes, seats = table_2024()
    members: list[Member] = []
    for r in _rows(jce.WINNERS["2024-05-19"], "PARLACEN"):
        cargo = r["CARGO"] or ""
        role = "substitute" if cargo.startswith("SUPLENTE") else "list_member"
        members.append(
            Member(
                jce._alliance_lead(r["PARTIDO Y/O ALIANZAS DE PARTIDOS"] or ""),
                None,
                r["NOMBRE"] or "",
                role,
                jce._sex(r["SEXO"]),
                None,
                None,
            )
        )
    return Result(votes, seats, members, 0, jce.WINNERS["2024-05-19"])


def _result_2020() -> Result:
    members: list[Member] = []
    votes: dict[str, int] = {}
    for r in _rows(jce.WINNERS["2020-07-05"], "Sheet1"):
        cargo = r["CARGO"] or ""
        if cargo not in ("DIPUTADO PARLACEN", "SUPLENTE DIPUTADO PARLACEN"):
            continue
        party = jce.sigla(r["SIGLAS"] or "")
        role = "substitute" if cargo.startswith("SUPLENTE") else "list_member"
        own = r["PARTIDO DEL CANDIDATO"]
        members.append(
            Member(
                party,
                jce.sigla(own) if own else None,
                r["NOMBRE COMPLETO"] or "",
                role,
                jce._sex(r["SEXO"]),
                jce._born(r["FECHA NACIMIENTO"]),
                None,
            )
        )
        quotient = _int(r["VOTOS HONDT"])
        if role == "list_member" and quotient:
            votes[party] = max(votes.get(party, 0), quotient)
    return _finish(members, votes, jce.WINNERS["2020-07-05"])


def _result_2016() -> Result:
    members: list[Member] = []
    votes: dict[str, int] = {}
    skipped = 0
    for r in _rows(jce.WINNERS["2016-05-15"], "Sheet2"):
        if r["COD_CARGO"] not in ("7", "8"):
            continue
        role = "substitute" if r["COD_CARGO"] == "8" else "list_member"
        if not r["NOMBRE"]:
            skipped += 1
            continue
        own = r["PARTIDO_CANDIDATO"]
        party = jce.sigla(r["PARTIDO"] or "")
        members.append(
            Member(party, jce.sigla(own) if own else None, r["NOMBRE"], role, None, None, _int(r["POSICION"]))
        )
        quotient = _int(r["VOTOS_VALIDOS"])
        if role == "list_member" and quotient:
            votes[party] = max(votes.get(party, 0), quotient)
    return _finish(members, votes, jce.WINNERS["2016-05-15"], skipped)


def _finish(members: list[Member], votes: dict[str, int], source: str, skipped: int = 0) -> Result:
    seats: dict[str, int] = {}
    for m in members:
        if m.role == "list_member":
            seats[m.party] = seats.get(m.party, 0) + 1
    if sum(seats.values()) != SEATS or set(seats) != set(votes):
        raise ValueError(f"the PARLACEN list of the elected holds {sum(seats.values())} deputies, not {SEATS}")
    return Result(votes, seats, members, skipped, source)


def result(day: str) -> Result:
    return {"2016-05-15": _result_2016, "2020-07-05": _result_2020, "2024-05-19": _result_2024}[day]()


def check(day: str) -> list[str]:
    """Checks the D'Hondt allotment of a day's seats against its national totals, and returns the report lines."""
    r = result(day)
    won: list[tuple[float, str]] = []
    for code, total in r.votes.items():
        won += [(total / n, code) for n in range(1, SEATS + 1)]
    won.sort(reverse=True)
    top = {code: 0 for code in r.votes}
    for _, code in won[:SEATS]:
        top[code] += 1
    held = {code: n for code, n in r.seats.items() if n}
    lines = [
        (
            f"{day} PARLACEN: {sum(r.seats.values())} seats to {len(r.seats)} parties; the D'Hondt allotment of the "
            f"stored totals {'matches' if {k: v for k, v in top.items() if v} == held else 'differs from'} the Board's"
        )
    ]
    if day == "2024-05-19":
        lines.append(f"{day} PARLACEN: {len(r.votes)} parties, {sum(r.votes.values()):,} votes in the seat table")
    return lines


def add(p: Package, d: jce.Day, election_id: str, base: str) -> tuple[int, int]:
    """Stages the day's PARLACEN contest in the election's package; returns its contest and value counts."""
    r = result(d.key)
    if d.key == "2024-05-19":
        p.artifact(raw.cached(jce.SOURCE, TABLE_2024), jce.SOURCE_ID)
    contest = jce.key("contest", d.key, "parlacen")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=BODY,
        district_area_id=jce.COUNTRY,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "Diputados al Parlamento Centroamericano (PARLACEN)", "es", "official")
    p.add("slug", path=f"{base}/parlacen", entity_id=contest, is_current=True)
    result_set = jce.key("result_set", d.key, "parlacen")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=jce.SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD + (METHOD_TABLE if d.key == "2024-05-19" else METHOD_LISTS)
    p.add("note", id=jce.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: dict[str, str] = {}
    for position, code in enumerate(sorted(r.votes, key=lambda c: (-r.votes[c], c)), 1):
        if not r.votes[code]:
            continue
        option = options[code] = jce.key("option", d.key, "parlacen", code)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=jce.party(code),
            ballot_position=position,
            ballot_label=code,
        )
    unit = jce.key("unit", f"{d.key}:national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=jce.DOMINICAN,
        scheme_code=NATIONAL,
        code=f"{d.key}:national",
        area_relation="equals",
        area_id=jce.COUNTRY,
        kind="aggregate",
    )
    p.name(unit, "República Dominicana, total nacional", "es", "official")
    seen: dict[str, int] = {}
    for m in r.members:
        if m.party not in options:
            raise ValueError(f"{d.key} PARLACEN: {m.name} of {m.party} has no option")
        seen[m.party + m.role] = seen.get(m.party + m.role, 0) + 1
        tag = f"{d.key}:parlacen:{m.role}:{m.party}:{seen[m.party + m.role]}"
        person, candidacy = jce.key("person", tag), jce.key("candidacy", tag)
        p.add(
            "person",
            id=person,
            gender=m.sex,
            birth_date=m.born,
            birth_precision="day" if m.born else None,
        )
        p.name(person, jce.title(m.name), "es", "official")
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[m.party],
            role=m.role,
            list_position=m.position,
            ballot_name=jce.title(m.name),
            member_of_party_id=jce.party(m.own_party) if m.own_party else None,
            is_independent=False,
        )
        if m.role == "list_member":
            p.add(
                "outcome", result_set_id=result_set, option_id=options[m.party], candidacy_id=candidacy, kind="elected"
            )
    for code, n in r.seats.items():
        p.add("outcome", result_set_id=result_set, option_id=options[code], kind="won", seats=n)
    rows = [(unit, options[code], jce.VOTES, r.votes[code]) for code in options]
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return 1, len(rows)
