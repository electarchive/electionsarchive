"""Polish elections that the Krajowe Biuro Wyborcze publishes as data files, one package per election date.

The Presidential elections of 1990 and 1995 come by polling district (obwód). The elections to the European Parliament
come by polling district for 2014, 2019, and 2024, and by commune (gmina) for 2004 and 2009, the finest unit the
authority publishes for those two.
"""

import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "pl.kbw"
BASE = "https://danewyborcze.kbw.gov.pl/dane/"
PUBLISHER = "Krajowe Biuro Wyborcze"
TITLE = "Dane wyborcze"
PAGE = "https://danewyborcze.kbw.gov.pl/"
POLAND = str(uuid5(package.NAMESPACE, "polity\x1fPL"))
OBWOD = "pl.obwod"
GMINA = "pl.gmina"
REGISTERED, BALLOTS, VALID, INVALID, VOTERS, PREFERENCE = 2, 3, 4, 5, 14, 7

PRESIDENT = {
    date(1990, 11, 25): ("1990", 1),
    date(1990, 12, 9): ("1990", 2),
    date(1995, 11, 5): ("1995", 1),
    date(1995, 11, 19): ("1995", 2),
}
PRESIDENT_FILES = {
    "1990": ("1990/prezydent/prez90kraj.zip", "1990/prezydent/zagr-i-statki90.xls", "1990/prezydent/prez1990gm.xls"),
    "1995": ("1995/prezydent/Prez1995.zip",),
}
PRESIDENT_METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze per polling district "
    "(obwód głosowania). Registered voters are the electors in the roll, ballots cast are the ballots taken from the "
    "box, valid are the valid ballots, and invalid are the invalid ballots. The first round elects a candidate who "
    "wins more than half of the valid votes; otherwise the two leading candidates go to a second round two weeks later."
)
PRESIDENT_SYSTEM_NOTE = (
    "Direct election of the President for five years (since 1990 for seven, in 1990, and five from 1995): a candidate "
    "who wins more than half of the valid votes is elected in the first round; otherwise the two leading candidates "
    "stand in a second round two weeks later."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


KBW = key("source", "kbw")
PRESIDENCY = key("office", "president")
EP_BODY = key("body", "european-parliament")
PRESIDENT_SYSTEM = key("electoral_system", "president")


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def slug(text: str) -> str:
    folded = text.lower().translate(str.maketrans("ąćęłńóśźżäöüß", "acelnoszzaous"))
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def fetch_one(path: str) -> None:
    raw.fetch(SOURCE, BASE + path, suffix=Path(path).suffix)


def cached(path: str) -> raw.Artifact:
    return raw.cached(SOURCE, BASE + path)


def sheet(data: bytes, name: str | int = 0, header: int | None = 0) -> pl.DataFrame:
    frame = fastexcel.read_excel(data).load_sheet(name, header_row=header).to_polars()
    if header is None:
        return frame
    return frame.rename({column: clean(column) for column in frame.columns})


def text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def member(path: str, name: str) -> bytes:
    return zipfile.ZipFile(cached(path).path).read(name)


def csv_member(path: str, name: str) -> pl.DataFrame:
    return pl.read_csv(
        io.StringIO(text(member(path, name))), separator=";", infer_schema_length=0, truncate_ragged_lines=True
    )


def count(column: str | pl.Expr) -> pl.Expr:
    expr = pl.col(column) if isinstance(column, str) else column
    return (
        expr.cast(pl.String)
        .str.replace_all(r"\s|\xa0", "")
        .replace("", None)
        .cast(pl.Float64, strict=False)
        .cast(pl.Int64)
    )


def facts(
    result_sets: pl.DataFrame, units: pl.DataFrame, values: pl.DataFrame, measure: int, option: str | None = None
) -> pl.DataFrame:
    """Turn (unit code, option id, value) rows into result_value rows; every unit belongs to one result set."""
    frame = values.join(units.select("code", "unit_id", "group"), on="code").join(result_sets, on="group")
    return frame.select(
        "result_set_id",
        "unit_id",
        option_id=pl.col(option) if option else pl.lit(None, pl.String),
        channel=pl.lit(0, pl.Int16),
        measure=pl.lit(measure, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        value=pl.col("value"),
    )


def packages() -> list[str]:
    return [
        "pl.kbw/registry",
        *(f"pl.kbw/pres-{day}" for day in PRESIDENT),
        *(f"pl.kbw/ep-{day}" for day in EP),
    ]


def fetch() -> None:
    for files in PRESIDENT_FILES.values():
        for path in files:
            fetch_one(path)
    for files in EP_FILES.values():
        for path in files:
            fetch_one(path)


def parse() -> None:
    registry().write()
    for day in PRESIDENT:
        president(day).write()
    for day in EP:
        european(day).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        "pl.kbw/registry",
        SOURCE,
        "The Krajowe Biuro Wyborcze's data files as a source, the Polish presidency, and the electoral systems of "
        "the presidential and European Parliament elections",
    )
    p.external |= {POLAND}
    p.add(
        "source",
        id=KBW,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license="Official public record, no license stated",
        attribution="Źródło danych: Państwowa Komisja Wyborcza, Krajowe Biuro Wyborcze.",
    )
    p.add("office", id=PRESIDENCY, polity_id=POLAND, kind="head_of_state")
    p.name(PRESIDENCY, "President of Poland", "en", "official")
    p.name(PRESIDENCY, "Prezydent Rzeczypospolitej Polskiej", "pl", "native", False)
    p.add("body", id=EP_BODY, polity_id=POLAND, kind="council")
    p.name(EP_BODY, "Polish members of the European Parliament", "en", "official")
    p.name(EP_BODY, "Posłowie do Parlamentu Europejskiego z Polski", "pl", "native", False)
    p.add(
        "electoral_system",
        id=PRESIDENT_SYSTEM,
        polity_id=POLAND,
        valid_from=date(1990, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", PRESIDENT_SYSTEM),
        entity_id=PRESIDENT_SYSTEM,
        kind="methodology",
        body_md=PRESIDENT_SYSTEM_NOTE,
        lang="en",
    )
    for id, since, until, rules, method in EP_SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=POLAND, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    return p


# Presidential elections


def _candidate_names(columns: list[str]) -> list[str]:
    return [clean(column.replace("\n", " ")) for column in columns]


def _president_1990(round_number: int) -> tuple[pl.DataFrame, dict[int, list[str]]]:
    """Domestic polling districts for both rounds; the first round adds the districts abroad and on ships."""
    domestic = sheet(member("1990/prezydent/prez90kraj.zip", "prez90.xls"))
    cols = domestic.columns
    who = cols[13:19] if round_number == 1 else cols[23:25]
    start = 9 if round_number == 1 else 19
    frame = domestic.select(
        code=pl.format("{}:{}", pl.col("TERYT"), pl.col("Nr obw.").cast(pl.Float64).cast(pl.Int64)),
        name=pl.col("Nazwa").cast(pl.String),
        kind=pl.lit("standard"),
        registered=count(cols[start]),
        ballots=count(cols[start + 1]),
        valid=count(cols[start + 2]),
        invalid=count(cols[start + 3]),
        **{f"v{i}": count(c) for i, c in enumerate(who)},
    )
    if round_number == 2:
        return frame, {2: _candidate_names(who)}
    extra = sheet(cached(PRESIDENT_FILES["1990"][1]).path.read_bytes())
    surnames = ["Bartoszcze", "Cimoszewicz", "Mazowiecki", "Moczulski", "Tymiński", "Wałęsa"]
    abroad = pl.col("SIEDZIBA") == "WARSZAWA-SRODMIESCIE"
    ships = extra.select(
        code=pl.format(
            "{}:{}:{}:{}",
            pl.col("TERYT").cast(pl.Float64).cast(pl.Int64),
            pl.col("SIEDZIBA").fill_null(""),
            pl.col("ARMATOR").fill_null(""),
            pl.col("NR").cast(pl.Float64).cast(pl.Int64).cast(pl.String),
        ),
        name=pl.col("ADRES").cast(pl.String),
        kind=pl.when(abroad).then(pl.lit("abroad_mission")).otherwise(pl.lit("mobile")),
        registered=count("Upr."),
        ballots=count("Odd."),
        valid=count("Ważne"),
        invalid=count("Nieważne"),
        **{f"v{i}": count(c) for i, c in enumerate(surnames)},
    )
    return pl.concat([frame, ships]), {1: _candidate_names(who)}


def _president_1995(round_number: int) -> tuple[pl.DataFrame, dict[int, list[str]]]:
    name = f"1995-prez-obw-tura{round_number}.xls"
    data = sheet(member("1995/prezydent/Prez1995.zip", name))
    cols = data.columns
    who = cols[12:]
    frame = data.select(
        code=pl.format(
            "{}:{}:{}",
            pl.col("Kod gm."),
            pl.col("Typ"),
            pl.col("Nr obw.").cast(pl.Float64).cast(pl.Int64),
        ),
        name=pl.col("Nazwa gm.").cast(pl.String),
        kind=pl.lit("standard"),
        registered=count(cols[8]),
        ballots=count(cols[9]),
        valid=count(cols[11]),
        invalid=count(cols[10]),
        **{f"v{i}": count(c) for i, c in enumerate(who)},
    )
    return frame, {round_number: _candidate_names(who)}


def president(day: date) -> Package:
    year, round_number = PRESIDENT[day]
    p = Package(
        f"pl.kbw/pres-{day}",
        SOURCE,
        f"The {year} Polish presidential election, {'first' if round_number == 1 else 'second'} round, by polling "
        "district from the Krajowe Biuro Wyborcze",
        depends=["pl.kbw/registry", *([f"pl.kbw/pres-{_first(year)}"] if round_number == 2 else [])],
    )
    p.external |= {POLAND, OBWOD}
    for path in PRESIDENT_FILES[year]:
        if round_number == 2 and path.endswith("zagr-i-statki90.xls"):
            continue
        if round_number == 1:
            p.artifact(cached(path), KBW)
        else:
            p.uses(cached(path))
    if year == "1990":
        frame, names = _president_1990(round_number)
    else:
        frame, names = _president_1995(round_number)
    candidates = names[round_number]
    election = key("election", "pres", day)
    p.add(
        "election",
        id=election,
        polity_id=POLAND,
        kind="general" if round_number == 1 else "runoff",
        date_from=day,
        date_to=day,
        status="held",
    )
    ordinal = "" if round_number == 1 else ", second round"
    p.name(election, f"{year} Polish presidential election{ordinal}", "en", "official")
    p.name(
        election,
        f"Wybory Prezydenta Rzeczypospolitej Polskiej {year}{'' if round_number == 1 else ', ponowne głosowanie'}",
        "pl",
        "native",
        False,
    )
    path = f"pl/{day}-presidential" + ("" if round_number == 1 else "-runoff")
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", "pres", day)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=round_number,
        previous_round_id=key("contest", "pres", _first(year)) if round_number == 2 else None,
        electoral_system_id=PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(
        contest,
        f"{year} Polish presidential election, {'first' if round_number == 1 else 'second'} round",
        "en",
        "official",
    )
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", "pres", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=KBW,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=PRESIDENT_METHOD,
        lang="en",
    )
    totals = [int(frame[f"v{i}"].sum() or 0) for i in range(len(candidates))]
    order = sorted(range(len(candidates)), key=lambda i: -totals[i])
    won = totals[order[0]] * 2 > int(frame["valid"].sum() or 0)
    options: list[str] = []
    for i, label in enumerate(candidates):
        option = key("option", "pres", day, i)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="candidate", ballot_position=i + 1, ballot_label=label
        )
        p.add("person", id=key("person", "pres", day, i), birth_precision=None)
        p.name(key("person", "pres", day, i), label, "pl", "official")
        p.add(
            "candidacy",
            id=key("candidacy", "pres", day, i),
            person_id=key("person", "pres", day, i),
            option_id=option,
            role="head",
            ballot_name=label,
        )
        rank = order.index(i)
        if round_number == 2 or won:
            kind = "elected" if rank == 0 else "lost"
        else:
            kind = "advanced" if rank < 2 else "eliminated"
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    units = frame.select("code", "name", "kind").unique("code", keep="first")
    if units.height != frame.height:
        raise ValueError(f"{day}: polling district codes repeat ({frame.height - units.height} duplicates)")
    units = units.with_columns(
        unit_id=pl.col("code").map_elements(lambda code: key("unit", day, code), return_dtype=pl.String),
        group=pl.lit(0),
    )
    for row in units.iter_rows(named=True):
        p.add(
            "reporting_unit",
            id=row["unit_id"],
            polity_id=POLAND,
            scheme_code=OBWOD,
            code=f"{day}:{row['code']}",
            area_relation="unlocated",
            area_id=None,
            kind=row["kind"],
        )
    sets = pl.DataFrame({"group": [0], "result_set_id": [result_set]})
    for measure, column in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (VALID, "valid"), (INVALID, "invalid")):
        values = frame.select("code", value=pl.col(column)).drop_nulls()
        p.frame("result_value", facts(sets, units, values, measure))
    for i, option in enumerate(options):
        values = frame.select("code", value=pl.col(f"v{i}"), option=pl.lit(option)).drop_nulls()
        p.frame("result_value", facts(sets, units, values, 1, "option"))
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=_national(day, i))
    for measure, column in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (VALID, "valid"), (INVALID, "invalid")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=_national(day, column))
    return p


def _first(year: str) -> date:
    return next(day for day, (y, r) in PRESIDENT.items() if y == year and r == 1)


def _national(day: date, which: int | str) -> int:
    """The authority's own commune-level totals for the election, summed."""
    year, round_number = PRESIDENT[day]
    if year == "1990":
        data = sheet(cached(PRESIDENT_FILES["1990"][2]).path.read_bytes(), header=None).slice(2)
        data.columns = [f"c{i}" for i in range(data.width)]
        base = 4 if round_number == 1 else 14
        fields = {"registered": base, "ballots": base + 1, "invalid": base + 2, "valid": base + 3}
        first = base + 4
    else:
        name = f"prez1995gm{round_number}tura.xls"
        data = sheet(member("1995/prezydent/Prez1995.zip", name), header=None).slice(1)
        data.columns = [f"c{i}" for i in range(data.width)]
        fields = {"registered": 5, "ballots": 6, "invalid": 7, "valid": 8}
        first = 9
    column = f"c{first + which}" if isinstance(which, int) else f"c{fields[which]}"
    return int(data.select(count(column).sum()).item())


# European Parliament elections

EP_FILES: dict[date, list[str]] = {
    date(2004, 6, 13): ["2004/parlament_eu/pe2004-kand.xls", "2004/parlament_eu/pe2004-lis-gm.xls"],
    date(2009, 6, 7): ["2009/parlament_eu/kandpe2009.xls", "2009/parlament_eu/pe2009.zip"],
    date(2014, 5, 25): [
        "2014/parlament_eu/pe2014kand.xls",
        "2014/parlament_eu/pe2014-gl-lis-obw.zip",
        "2014/parlament_eu/pe2014-gl-lis-okr.zip",
        "2014/parlament_eu/pe2014-kand-obw.zip",
    ],
    date(2019, 5, 26): [
        "2019/parlament_eu/kandydaci_csv.zip",
        "2019/parlament_eu/wykaz_list_csv.zip",
        "2019/parlament_eu/wyniki_gl_na_kand_po_obwodach_xlsx.zip",
        "2019/parlament_eu/wyniki_gl_na_listy_po_okregach_csv.zip",
    ],
    date(2024, 6, 9): [
        "2024/parlament_eu/kandydaci_csv.zip",
        "2024/parlament_eu/wykaz_list_csv.zip",
        "2024/parlament_eu/protokoly_po_obwodach_csv.zip",
        "2024/parlament_eu/wyniki_gl_na_listy_po_okregach_csv.zip",
    ],
}
EP = {day: day.year for day in EP_FILES}
EP_SEATS = {2004: 54, 2009: 50, 2014: 51, 2019: 51, 2024: 53}
EP_SYSTEM_2004 = key("electoral_system", "ep", 2004)
EP_SYSTEM_2009 = key("electoral_system", "ep", 2009)
EP_SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        EP_SYSTEM_2004,
        date(2003, 1, 1),
        date(2009, 1, 1),
        {
            "formula": "hare",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "open", "vacancy": "next_on_list"},
        },
        (
            "Poland's members of the European Parliament, elected in 13 constituencies from open lists of candidates "
            "that committees register in each. Seats go first to the committees that won at least 5 percent of the valid "
            "votes nationwide, in proportion to their votes by the Hare-Niemeyer method, and then to each committee's "
            "constituency lists in the same way. Within a list, the candidates with the most votes win."
        ),
    ),
    (
        EP_SYSTEM_2009,
        date(2009, 1, 1),
        None,
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
            "lists": {"order": "open", "vacancy": "next_on_list"},
        },
        (
            "Poland's members of the European Parliament, elected in 13 constituencies from open lists of candidates "
            "that committees register in each. Seats go first to the committees that won at least 5 percent of the valid "
            "votes nationwide, by the D'Hondt method (8 percent for a coalition in 2004 and 2009 did not apply), and "
            "then to each committee's constituency lists by the Hare-Niemeyer method. Within a list, the candidates with "
            "the most votes win."
        ),
    ),
]
EP_METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze{level}. Registered "
    "voters are the electors in the roll, ballots cast are the ballots taken from the box, valid votes are the valid "
    "votes for the lists, and invalid are the invalid ballots and the invalid votes on valid ballots. The votes for "
    "each candidate are counted within the list's votes{candidates}."
)
UNLOCATED = (
    "Polling districts are stored without a place; their commune, county, and voivodeship are in the source files."
)


class Ep:
    """One European Parliament election as normalized frames, whatever the year's file layout."""

    def __init__(
        self,
        day: date,
        level: str,
        units: pl.DataFrame,
        votes: pl.DataFrame,
        lists: pl.DataFrame,
        candidates: pl.DataFrame,
        declared_lists: pl.DataFrame,
        declared_accounting: pl.DataFrame | None = None,
        preferences: pl.DataFrame | None = None,
    ) -> None:
        self.day = day
        self.level = level
        self.units = units
        self.votes = votes
        self.lists = lists
        self.candidates = candidates
        self.declared_lists = declared_lists
        self.declared_accounting = declared_accounting
        self.preferences = preferences


def _committee(name: str) -> str:
    return clean(re.sub(r"\s+-\s+ZPOW-[\d/\-]+$", "", name.replace("\xa0", " ")))


def _by_number(prefixed: list[str]) -> list[tuple[int, str]]:
    found = []
    for column in prefixed:
        match = re.match(r"^(\d+)\s*-\s*(.*)$", column.replace("\xa0", " "))
        if match is None:
            raise ValueError(f"list column without a number: {column!r}")
        found.append((int(match.group(1)), _committee(match.group(2))))
    return found


def _unit_code(teryt: pl.Expr, number: pl.Expr, fallback: pl.Expr) -> pl.Expr:
    return pl.format("{}:{}", pl.when(teryt.is_null() | (teryt == "")).then(fallback).otherwise(teryt), number)


def _kind(gmina: pl.Expr) -> pl.Expr:
    folded = gmina.cast(pl.String).str.to_lowercase()
    return (
        pl.when(folded.str.contains("zagranic|abroad"))
        .then(pl.lit("abroad_mission"))
        .when(folded.str.contains("statki|statek"))
        .then(pl.lit("mobile"))
        .otherwise(pl.lit("standard"))
    )


def _elected(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.String).str.strip_chars().str.to_lowercase()
    return value.is_in(["t", "tak", "x", "1", "m", "mandat"]).fill_null(False)


def _load_2024() -> Ep:
    day = date(2024, 6, 9)
    protocols = csv_member("2024/parlament_eu/protokoly_po_obwodach_csv.zip", "protokoly_po_obwodach_utf8.csv")
    cols = protocols.columns
    units = protocols.select(
        code=_unit_code(pl.col(cols[3]), pl.col(cols[0]), pl.col(cols[2])),
        group=pl.col(cols[1]).cast(pl.Int64),
        kind=_kind(pl.col(cols[2])),
        registered=count(cols[9]),
        ballots=count(cols[26]),
        valid=count(cols[34]),
        invalid=count(cols[28]) + count(cols[30]),
        row=pl.int_range(pl.len()),
    )
    protocols = protocols.with_columns(code=units["code"], group=units["group"])
    list_votes: list[pl.DataFrame] = []
    pref_votes: list[pl.DataFrame] = []
    list_pattern = re.compile(r"^Okreg (\d+) - Głosy na listę nr (\d+) - ")
    cand_pattern = re.compile(r"^Okreg (\d+) - Głosy na kandydata nr (\d+) na liście nr (\d+) - ")
    for number in sorted(units["group"].unique().to_list()):
        rows = protocols.filter(pl.col("group") == number)
        for column in cols[35:]:
            match = list_pattern.match(column)
            if match and int(match.group(1)) == number:
                part = rows.select("code", nr=pl.lit(int(match.group(2))), value=count(column)).drop_nulls("value")
                list_votes.append(part)
                continue
            match = cand_pattern.match(column)
            if match and int(match.group(1)) == number:
                part = rows.select(
                    "code",
                    nr=pl.lit(int(match.group(3))),
                    pos=pl.lit(int(match.group(2))),
                    value=count(column),
                ).drop_nulls("value")
                pref_votes.append(part)
    wykaz = csv_member("2024/parlament_eu/wykaz_list_csv.zip", "wykaz_list_utf8.csv")
    lists = wykaz.select(
        group=pl.col("Numer okręgu").cast(pl.Int64),
        nr=pl.col("Nr listy").cast(pl.Int64),
        committee=pl.col("Nazwa Komitetu").map_elements(_committee, return_dtype=pl.String),
    )
    declared_lists = wykaz.select(
        group=pl.col("Numer okręgu").cast(pl.Int64),
        nr=pl.col("Nr listy").cast(pl.Int64),
        votes=count("Liczba głosów"),
        seats=count("Liczba mandatów"),
    )
    cands = csv_member("2024/parlament_eu/kandydaci_csv.zip", "kandydaci_utf8.csv")
    candidates = cands.select(
        group=pl.col("Nr okręgu").cast(pl.Int64),
        nr=pl.col("Nr listy").cast(pl.Int64),
        pos=pl.col("Pozycja na liście").cast(pl.Int64),
        name=pl.col("Nazwisko i imiona").map_elements(clean, return_dtype=pl.String),
        gender=pl.col("Płeć"),
        occupation=pl.col("Zawód"),
        residence=pl.col("Miejsce zamieszkania"),
        party=pl.col("Przynależność do partii"),
        votes=count("Liczba głosów"),
        elected=pl.col("Czy uzyskał mandat").str.to_lowercase() == "tak",
    )
    totals = csv_member(
        "2024/parlament_eu/wyniki_gl_na_listy_po_okregach_csv.zip", "wyniki_gl_na_listy_po_okregach_utf8.csv"
    )
    tc = totals.columns
    accounting = pl.concat(
        [
            totals.select(
                group=pl.col("Nr okręgu").cast(pl.Int64),
                measure=pl.lit(REGISTERED),
                value=count(tc[_at(tc, "Liczba wyborców uprawnionych")]),
            ),
            totals.select(
                group=pl.col("Nr okręgu").cast(pl.Int64),
                measure=pl.lit(BALLOTS),
                value=count(tc[_at(tc, "Liczba kart wyjętych z urny")]),
            ),
            totals.select(
                group=pl.col("Nr okręgu").cast(pl.Int64),
                measure=pl.lit(VALID),
                value=count(tc[_at(tc, "Liczba głosów ważnych oddanych łącznie")]),
            ),
            totals.select(
                group=pl.col("Nr okręgu").cast(pl.Int64),
                measure=pl.lit(INVALID),
                value=count(tc[_at(tc, "Liczba kart nieważnych")]) + count(tc[_at(tc, "Liczba głosów nieważnych")]),
            ),
        ]
    )
    return Ep(
        day,
        "obwod",
        units.drop("row"),
        pl.concat(list_votes),
        lists,
        candidates,
        declared_lists,
        accounting,
        pl.concat(pref_votes),
    )


def _trailing(text: str) -> int:
    match = re.search(r"(\d+)$", text)
    if match is None:
        raise ValueError(f"no number at the end of {text!r}")
    return int(match.group(1))


def _leading(text: str) -> int:
    match = re.match(r"^(\d+) ", text)
    if match is None:
        raise ValueError(f"no number at the start of {text!r}")
    return int(match.group(1))


def _at(columns: list[str], prefix: str) -> int:
    found = [i for i, column in enumerate(columns) if column.startswith(prefix)]
    if len(found) != 1:
        raise LookupError(f"{prefix!r} matches {len(found)} columns")
    return found[0]


def _int(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Float64).cast(pl.Int64)


def _unit_total(frame: pl.DataFrame, group: str, measure: int, value: pl.Expr) -> pl.DataFrame:
    return frame.select(group=_int(group), measure=pl.lit(measure), value=value)


def _declared_accounting(
    frame: pl.DataFrame, group: str, registered: str, urn: str, valid: str, invalid: pl.Expr
) -> pl.DataFrame:
    return pl.concat(
        [
            _unit_total(frame, group, REGISTERED, count(registered)),
            _unit_total(frame, group, BALLOTS, count(urn)),
            _unit_total(frame, group, VALID, count(valid)),
            _unit_total(frame, group, INVALID, invalid),
        ]
    )


def _load_2019() -> Ep:
    day = date(2019, 5, 26)
    path = "2019/parlament_eu/wyniki_gl_na_kand_po_obwodach_xlsx.zip"
    data = member(path, "wyniki_gl_na_kand_po_obwodach.xlsx")
    names = fastexcel.read_excel(data).sheet_names
    units: list[pl.DataFrame] = []
    list_votes: list[pl.DataFrame] = []
    pref_votes: list[pl.DataFrame] = []
    lists: list[pl.DataFrame] = []
    for sheet_name in names:
        group = _trailing(sheet_name)
        frame = sheet(data, sheet_name)
        cols = frame.columns
        coded = frame.with_columns(
            code=_unit_code(pl.col(cols[0]).cast(pl.String), _int(cols[1]), pl.col("Gmina")),
        )
        units.append(
            coded.select(
                "code",
                group=pl.lit(group),
                kind=_kind(pl.col("Gmina")),
                registered=count(cols[_at(cols, "Liczba wyborców uprawnionych")]),
                ballots=count(cols[_at(cols, "Liczba kart wyjętych z urny")]),
                valid=count(cols[_at(cols, "Liczba głosów ważnych oddanych łącznie")]),
                invalid=count(cols[_at(cols, "Liczba kart nieważnych")])
                + count(cols[_at(cols, "Liczba głosów nieważnych")]),
            )
        )
        current = 0
        position = 0
        for column in cols[_at(cols, "Liczba głosów ważnych oddanych łącznie") + 1 :]:
            match = re.match(r"^Lista nr (\d+) - (.*)$", column)
            if match:
                current = int(match.group(1))
                position = 0
                lists.append(
                    pl.DataFrame({"group": [group], "nr": [current], "committee": [_committee(match.group(2))]})
                )
                list_votes.append(coded.select("code", nr=pl.lit(current), value=count(column)).drop_nulls("value"))
            elif re.match(r"^Razem", column):
                continue
            else:
                position += 1
                number = _leading(column)
                if number != position:
                    raise ValueError(f"{sheet_name}: candidate {column!r} out of order in list {current}")
                pref_votes.append(
                    coded.select("code", nr=pl.lit(current), pos=pl.lit(number), value=count(column)).drop_nulls(
                        "value"
                    )
                )
    committees = pl.concat(lists)
    wykaz = csv_member("2019/parlament_eu/wykaz_list_csv.zip", "wykaz_list.csv")
    declared_lists = wykaz.select(
        group=pl.col("Numer okręgu").cast(pl.Int64),
        nr=pl.col("Numer listy").cast(pl.Int64),
        votes=count("Liczba głosów"),
        seats=count("Liczba mandatów"),
    )
    cands = csv_member("2019/parlament_eu/kandydaci_csv.zip", "kandydaci.csv")
    candidates = cands.select(
        group=pl.col("Numer okręgu").cast(pl.Int64),
        nr=pl.col("Numer listy").cast(pl.Int64),
        pos=pl.col("Numer na liście").cast(pl.Int64),
        name=pl.format("{} {}", pl.col("Imiona"), pl.col("Nazwisko")).map_elements(clean, return_dtype=pl.String),
        gender=pl.col("Płeć"),
        votes=count("Liczba głosów"),
        elected=pl.col(cands.columns[-1]).str.to_lowercase() == "tak",
    )
    totals = csv_member(
        "2019/parlament_eu/wyniki_gl_na_listy_po_okregach_csv.zip", "wyniki_gl_na_listy_po_okregach.csv"
    )
    tc = totals.columns
    accounting = _declared_accounting(
        totals,
        "Nr okręgu",
        tc[_at(tc, "Liczba wyborców uprawnionych")],
        tc[_at(tc, "Liczba kart wyjętych z urny")],
        tc[_at(tc, "Liczba głosów ważnych oddanych łącznie")],
        count(tc[_at(tc, "Liczba kart nieważnych")]) + count(tc[_at(tc, "Liczba głosów nieważnych")]),
    )
    return Ep(
        day,
        "obwod",
        pl.concat(units),
        pl.concat(list_votes),
        committees.sort("group", "nr"),
        candidates,
        declared_lists,
        accounting,
        pl.concat(pref_votes),
    )


def _kand(
    frame: pl.DataFrame,
    group: str,
    number: str,
    committee: str,
    position: str,
    last: str,
    first: str,
    gender: str,
    *,
    votes: str,
    listed: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Lists, candidates, and declared list totals from a candidate file that prints list votes or candidate votes."""
    frame = frame.with_columns(
        _g=_int(group),
        _n=_int(number),
        _p=_int(position),
        _name=pl.format("{} {}", pl.col(first), pl.col(last)).map_elements(clean, return_dtype=pl.String),
        _elected=pl.col("Mandat").is_not_null(),
    )
    lists = (
        frame.group_by("_g", "_n")
        .agg(pl.col(committee).first().map_elements(_committee, return_dtype=pl.String).alias("committee"))
        .rename({"_g": "group", "_n": "nr"})
    )
    candidates = frame.select(
        group="_g",
        nr="_n",
        pos="_p",
        name="_name",
        gender=pl.col(gender),
        votes=count(votes),
        elected="_elected",
    )
    if listed is None:
        declared = candidates.group_by("group", "nr").agg(
            pl.col("votes").sum().alias("votes"), pl.col("elected").sum().alias("seats")
        )
    else:
        declared = (
            frame.group_by("_g", "_n")
            .agg(count(listed).first().alias("votes"), pl.col("_elected").sum().alias("seats"))
            .rename({"_g": "group", "_n": "nr"})
        )
    return lists.sort("group", "nr"), candidates, declared


def _candidate_votes_2014(units: pl.DataFrame, candidates: pl.DataFrame) -> pl.DataFrame:
    """Candidate votes by polling district from the per-commission CSV files of the 2014 election.

    Each file has a preamble, a row that names every list above its columns, and a header row that repeats the list's
    vote column and then names the list's candidates in order, ending in a "Razem" column. The files count candidates
    from 1 without gaps, while the candidate file keeps each candidate's ballot position, so a candidate is matched to
    it by constituency, list number, and name.
    """
    path = "2014/parlament_eu/pe2014-kand-obw.zip"
    positions: dict[tuple[int, int, tuple[str, ...]], int] = {}
    for row in candidates.iter_rows(named=True):
        tag = (row["group"], row["nr"], tuple(sorted(row["name"].casefold().split())))
        if tag in positions:
            raise ValueError(f"{tag}: two candidates share a name on one list")
        positions[tag] = row["pos"]
    groups = dict(units.select("code", "group").iter_rows())
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for name in sorted(zipfile.ZipFile(cached(path).path).namelist()):
        table = list(csv.reader(io.StringIO(member(path, name).decode("cp1250")), delimiter=";"))
        head = next(i for i, row in enumerate(table) if row and row[0] == "Lp.")
        header = table[head]
        data = [row for row in table[head + 1 :] if row and row[0].strip()]
        codes = [f"{re.sub(r'\D', '', row[1])}:{int(row[5])}" for row in data]
        if seen & set(codes) or len(set(codes)) != len(codes):
            raise ValueError(f"{name}: polling districts repeat")
        seen |= set(codes)
        if unknown := [code for code in codes if code not in groups]:
            raise ValueError(f"{name}: polling districts {unknown[:3]} are not in the results file")
        found_groups = {groups[code] for code in codes}
        if len(found_groups) != 1:
            raise ValueError(f"{name}: spans constituencies {sorted(found_groups)}")
        (group,) = found_groups
        layout: list[tuple[int, int, int]] = []
        for start, cell in enumerate(table[head - 1]):
            found = re.search(r"Lista nr (\d+)", cell)
            if found is None:
                continue
            number = int(found.group(1))
            last = 0
            for column in range(start + 1, header.index("Razem", start)):
                label = tuple(sorted(clean(header[column].removeprefix("=").strip('"')).casefold().split()))
                position = positions.get((group, number, label))
                if position is None:
                    if {row[column].strip() for row in data} - {"Skreślony", ""}:
                        raise ValueError(f"{name}: {header[column]!r} is not a candidate of list {number}")
                    continue
                if position <= last:
                    raise ValueError(f"{name}: {header[column]!r} is out of order on list {number}")
                last = position
                layout.append((column, number, position))
        for code, row in zip(codes, data, strict=True):
            for column, number, position in layout:
                value = row[column].strip() if column < len(row) else ""
                rows.append(
                    {
                        "code": code,
                        "nr": number,
                        "pos": position,
                        "value": None if value in {"", "Skreślony"} else int(value),
                    }
                )
    if missing := set(groups) - seen:
        raise ValueError(f"{len(missing)} polling districts have no candidate votes, such as {sorted(missing)[:3]}")
    return pl.DataFrame(
        rows, schema={"code": pl.String, "nr": pl.Int64, "pos": pl.Int64, "value": pl.Int64}
    ).drop_nulls("value")


def _load_2014() -> Ep:
    day = date(2014, 5, 25)
    data = sheet(member("2014/parlament_eu/pe2014-gl-lis-obw.zip", "pe2014-gl-lis-obw.xls"))
    cols = data.columns
    coded = data.with_columns(code=_unit_code(pl.col("TERYT").cast(pl.String), _int("Nr obw."), pl.col("Gmina")))
    units = coded.select(
        "code",
        group=_int("Nr okr."),
        kind=_kind(pl.col("Gmina")),
        registered=count("Liczba uprawnionych do głosowania"),
        ballots=count("Kart wyjęte z urn"),
        valid=count("Głosy ważne"),
        invalid=count("Karty nieważne") + count("Głosy nieważne"),
    )
    listed = [c for c in cols if re.match(r"^\d+ - ", c)]
    votes = pl.concat(
        [
            coded.select("code", nr=pl.lit(number), value=count(column)).drop_nulls("value")
            for column, (number, _) in zip(listed, _by_number(listed), strict=True)
        ]
    )
    kand = sheet(cached("2014/parlament_eu/pe2014kand.xls").path.read_bytes())
    lists, candidates, declared = _kand(
        kand,
        "Nr okr.",
        "Nr listy",
        "Komitet",
        "Poz.",
        "Nazwisko",
        "Imiona",
        "Płeć",
        votes="Gł. na kand.",
        listed="Gł. na listę",
    )
    totals = sheet(member("2014/parlament_eu/pe2014-gl-lis-okr.zip", "pe2014-gl-lis-okr.xls"))
    accounting = _declared_accounting(
        totals,
        "Nr okr.",
        "Liczba uprawnionych do głosowania",
        "Kart wyjęte z urn",
        "Głosy ważne",
        count("Karty nieważne") + count("Głosy nieważne"),
    )
    preferences = _candidate_votes_2014(units, candidates)
    return Ep(day, "obwod", units, votes, lists, candidates, declared, accounting, preferences)


def _load_commune(
    day: date,
    frame: pl.DataFrame,
    group: str,
    teryt: str,
    gmina: str,
    registered: str,
    issued: str,
    valid: str,
    kand: pl.DataFrame,
    kcols: tuple[str, ...],
    by_name: bool,
) -> Ep:
    cols = frame.columns
    coded = frame.with_columns(code=pl.col(teryt).cast(pl.String))
    units = coded.select(
        "code",
        group=_int(group),
        kind=_kind(pl.col(gmina)),
        registered=count(registered),
        voters=count(issued),
        valid=count(valid),
    )
    lists, candidates, declared = _kand(kand, *kcols[:-1], votes=kcols[-1])
    listed = [c for c in cols[cols.index(valid) + 1 :]]
    if by_name:
        names = {(_committee(c)): c for c in listed}
        votes = []
        for row in lists.iter_rows(named=True):
            column = names.get(row["committee"])
            if column is None:
                raise LookupError(f"{day}: no commune column for {row['committee']!r}")
            part = coded.select("code", group=_int(group), nr=pl.lit(row["nr"]), value=count(column)).filter(
                pl.col("group") == row["group"]
            )
            votes.append(part.drop_nulls("value").select("code", "nr", "value"))
    else:
        votes = []
        for column, (number, _) in zip(listed, _by_number(listed), strict=True):
            votes.append(coded.select("code", nr=pl.lit(number), value=count(column)).drop_nulls("value"))
    return Ep(day, "gmina", units, pl.concat(votes), lists, candidates, declared)


def _load_2009() -> Ep:
    day = date(2009, 6, 7)
    frame = sheet(member("2009/parlament_eu/pe2009.zip", "pe2009-lis-gm.xls"))
    kand = sheet(cached("2009/parlament_eu/kandpe2009.xls").path.read_bytes())
    return _load_commune(
        day,
        frame,
        "Okręg",
        "TERYT",
        "Gmina",
        "Upr.",
        "Wydane karty",
        "Głosy ważne",
        kand,
        ("Okręg", "Nr listy", "Komitet", "Nr", "Nawisko", "Imiona", "Płeć", "Głosy"),
        False,
    )


def _load_2004() -> Ep:
    day = date(2004, 6, 13)
    frame = sheet(cached("2004/parlament_eu/pe2004-lis-gm.xls").path.read_bytes())
    kand = sheet(cached("2004/parlament_eu/pe2004-kand.xls").path.read_bytes())
    seats = dict(frame.select("Siedziba OKW", "Nr okr.").unique().iter_rows())
    kand = kand.with_columns(
        pl.when(pl.col("Nr okr.").cast(pl.Float64, strict=False).is_null())
        .then(pl.col("Siedziba OKW").replace_strict(seats, default=None))
        .otherwise(pl.col("Nr okr."))
        .alias("Nr okr.")
    )
    return _load_commune(
        day,
        frame,
        "Nr okr.",
        "TERYT gminy",
        "Gmina",
        "Liczba uprawnionych do głosowania",
        "Liczba kart wydanych",
        "Liczba głosów ważnych",
        kand,
        ("Nr okr.", "Lista", "Komitet", "Nr", "Nazwisko", "Imiona", "Płeć", "Głosy"),
        True,
    )


def european(day: date) -> Package:
    loaders = {
        date(2004, 6, 13): _load_2004,
        date(2009, 6, 7): _load_2009,
        date(2014, 5, 25): _load_2014,
        date(2019, 5, 26): _load_2019,
        date(2024, 6, 9): _load_2024,
    }
    return _european(loaders[day]())


def _european(ep: Ep) -> Package:
    day = ep.day
    year = day.year
    p = Package(
        f"pl.kbw/ep-{day}",
        SOURCE,
        f"The {year} European Parliament election in Poland by "
        + ("polling district" if ep.level == "obwod" else "commune")
        + " from the Krajowe Biuro Wyborcze",
        depends=["pl.kbw/registry"],
    )
    p.external |= {POLAND, OBWOD if ep.level == "obwod" else GMINA}
    for path in EP_FILES[day]:
        p.artifact(cached(path), KBW)
    system = EP_SYSTEM_2004 if year == 2004 else EP_SYSTEM_2009
    election = key("election", "ep", day)
    suffix = "polling-districts" if ep.level == "obwod" else "communes"
    p.add("election", id=election, polity_id=POLAND, kind="general", date_from=day, date_to=day, status="held")
    where = "polling district" if ep.level == "obwod" else "commune"
    p.name(election, f"{year} European Parliament election in Poland, by {where}", "en", "official")
    p.name(election, f"Wybory do Parlamentu Europejskiego {year}", "pl", "native", False)
    path = f"pl/{day}-european-parliament-{suffix}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    groups = sorted(ep.units["group"].unique().to_list())
    seats = ep.declared_lists.group_by("group").agg(pl.col("seats").sum())
    magnitude = dict(seats.iter_rows())
    sets: dict[int, str] = {}
    committees: dict[str, str] = {}
    options: dict[tuple[int, int], str] = {}
    candidates: dict[tuple[int, int, int], str] = {}
    for group in groups:
        contest = key("contest", "ep", day, group)
        title = f"{year} European Parliament election in Poland, constituency {group}"
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=EP_BODY,
            magnitude=int(magnitude[group]) if group in magnitude else None,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        p.name(contest, f"Wybory do Parlamentu Europejskiego {year}, okręg wyborczy nr {group}", "pl", "native", False)
        p.add("slug", path=f"{path}/constituency-{group}", entity_id=contest, is_current=True)
        result_set = sets[group] = key("result_set", "ep", day, group)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=KBW,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=EP_METHOD.format(
                level=""
                if ep.level == "obwod"
                else ", summed to each commune, the finest unit the authority publishes for this election",
                candidates=""
                if ep.preferences is not None
                else " but are stored only as totals in the source's candidate file",
            ),
            lang="en",
        )
        for row in ep.lists.filter(pl.col("group") == group).sort("nr").iter_rows(named=True):
            name = row["committee"]
            if name not in committees:
                committees[name] = key("party", year, slug(name))
                p.add("party", id=committees[name], kind="list_vehicle", polity_id=POLAND)
                p.name(committees[name], name, "pl", "official")
            option = options[group, row["nr"]] = key("option", "ep", day, group, row["nr"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=committees[name],
                ballot_position=row["nr"],
                ballot_label=name,
            )
    for row in ep.candidates.iter_rows(named=True):
        tag = (row["group"], row["nr"], row["pos"])
        person = key("person", "ep", day, *tag)
        p.add(
            "person",
            id=person,
            birth_precision=None,
            gender={"Kobieta": "female", "Mężczyzna": "male", "K": "female", "M": "male"}.get(row["gender"] or ""),
        )
        p.name(person, row["name"], "pl", "official")
        list_option = options[row["group"], row["nr"]]
        candidacy = key("candidacy", "ep", day, *tag)
        if ep.preferences is not None:
            parent = candidates[tag] = key("option", "ep", day, *tag, "candidate")
            p.add(
                "contest_option",
                id=parent,
                contest_id=key("contest", "ep", day, row["group"]),
                kind="candidate",
                parent_option_id=list_option,
                ballot_position=row["pos"],
                ballot_label=row["name"],
            )
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=list_option,
            role="list_member",
            list_position=row["pos"],
            ballot_name=row["name"],
        )
        if row["elected"]:
            p.add(
                "outcome",
                result_set_id=sets[row["group"]],
                option_id=list_option,
                candidacy_id=candidacy,
                kind="elected",
            )
    for row in ep.declared_lists.iter_rows(named=True):
        option = options[row["group"], row["nr"]]
        if row["seats"]:
            p.add("outcome", result_set_id=sets[row["group"]], option_id=option, kind="won", seats=int(row["seats"]))
        p.add("declared", result_set_id=sets[row["group"]], option_id=option, measure=1, value=int(row["votes"]))
    if ep.declared_accounting is not None:
        for row in ep.declared_accounting.iter_rows(named=True):
            p.add(
                "declared",
                result_set_id=sets[row["group"]],
                option_id=None,
                measure=row["measure"],
                value=int(row["value"]),
            )
    if ep.preferences is not None:
        by = ep.candidates.filter(pl.col("votes").is_not_null())
        for row in by.iter_rows(named=True):
            tag = (row["group"], row["nr"], row["pos"])
            p.add(
                "declared",
                result_set_id=sets[row["group"]],
                option_id=candidates[tag],
                measure=PREFERENCE,
                value=int(row["votes"]),
            )
    units = ep.units.with_columns(
        unit_id=pl.col("code").map_elements(lambda code: key("unit", day, code), return_dtype=pl.String)
    )
    if units["code"].n_unique() != units.height:
        raise ValueError(f"{day}: unit codes repeat")
    scheme = OBWOD if ep.level == "obwod" else GMINA
    p.frame(
        "reporting_unit",
        units.select(
            id="unit_id",
            polity_id=pl.lit(POLAND),
            scheme_code=pl.lit(scheme),
            code=pl.format("{}:{}", pl.lit(str(day)), pl.col("code")),
            area_relation=pl.lit("unlocated"),
            kind="kind",
        ),
    )
    set_frame = pl.DataFrame(
        {"group": list(sets), "result_set_id": list(sets.values())},
        schema={"group": pl.Int64, "result_set_id": pl.String},
    )
    for measure, column in (
        (REGISTERED, "registered"),
        (VOTERS, "voters"),
        (BALLOTS, "ballots"),
        (VALID, "valid"),
        (INVALID, "invalid"),
    ):
        if column not in units.columns:
            continue
        values = units.select("code", value=pl.col(column)).drop_nulls()
        p.frame("result_value", facts(set_frame, units, values, measure))
    option_frame = pl.DataFrame(
        [(g, n, o) for (g, n), o in options.items()],
        schema={"group": pl.Int64, "nr": pl.Int64, "option": pl.String},
        orient="row",
    )
    list_values = ep.votes.join(units.select("code", "group"), on="code").join(
        option_frame, on=["group", "nr"], how="left"
    )
    if list_values["option"].null_count():
        stray = list_values.filter(pl.col("option").is_null())
        if stray["value"].max() != 0:
            raise ValueError(f"{day}: votes for lists the contest does not have")
        list_values = list_values.drop_nulls("option")
    p.frame("result_value", facts(set_frame, units, list_values.select("code", "value", "option"), 1, "option"))
    if ep.preferences is not None:
        cand_frame = pl.DataFrame(
            [(g, n, k, o) for (g, n, k), o in candidates.items()],
            schema={"group": pl.Int64, "nr": pl.Int64, "pos": pl.Int64, "option": pl.String},
            orient="row",
        )
        pref = ep.preferences.join(units.select("code", "group"), on="code").join(
            cand_frame, on=["group", "nr", "pos"], how="left"
        )
        if pref["option"].null_count():
            raise ValueError(f"{day}: votes for candidates the contest does not have")
        p.frame("result_value", facts(set_frame, units, pref.select("code", "value", "option"), PREFERENCE, "option"))
    _discrepancies(
        p,
        sets,
        ep.level,
        "constituency totals"
        if ep.declared_accounting is not None
        else "candidate file, whose candidate votes add up to the list totals,",
    )
    return p


def _discrepancies(p: Package, sets: dict[int, str], level: str, source: str) -> None:
    """Accept, with the constituencies named, declared totals that the source's own unit figures do not add up to."""
    summed = (
        pl.concat(p.frames["result_value"])
        .group_by("result_set_id", "option_id", "measure")
        .agg(pl.col("value").sum().alias("summed"))
    )
    declared = pl.DataFrame(
        p.rows["declared"],
        schema={"result_set_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
        orient="row",
    )
    bad = declared.join(summed, on=["result_set_id", "option_id", "measure"], how="left", nulls_equal=True).filter(
        pl.col("summed").is_null() | (pl.col("summed") != pl.col("value"))
    )
    if bad.is_empty():
        return
    names = {value: group for group, value in sets.items()}
    where = sorted({names[i] for i in bad["result_set_id"]})
    most = max(abs(int(a) - int(b or 0)) for a, b in zip(bad["value"], bad["summed"], strict=True))
    p.accept(
        "accounting.declared",
        f"the source's {source} differ from the sum of its {'polling district' if level == 'obwod' else 'commune'} "
        f"figures in constituenc{'y' if len(where) == 1 else 'ies'} {', '.join(map(str, where))}: {bad.height} "
        f"figures, by at most {most:,}",
    )
