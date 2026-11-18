"""Slovakia's elections from the Statistical Office of the Slovak Republic (Štatistický úrad SR).

The office publishes every national, European, regional, and municipal election and every referendum it has counted
since 1994 as open-data tables on volby.statistics.sk. The finest unit differs by year: the polling district (okrsok)
from 2010 for referendums, 2016 for the National Council, 2017 for regional elections, 2019 for the presidency and the
European Parliament, and 2022 for municipal elections; the municipality (obec) before that; the district (okres)
for the 1998 National Council election; and the regional electoral district (volebný obvod) for the regional elections
of 2001 to 2013, whose chairs the office prints by region in the first round. One package covers each election day, both rounds included.
"""

import csv
import functools
import html
import io
import re
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "sk.volby"
SITE = "https://volby.statistics.sk"
SLOVAKIA = str(uuid5(package.NAMESPACE, "polity\x1fSK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fSVK"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
ABROAD = "599999"
"""The office's code for the postal vote from abroad, counted in its own polling districts."""
KRAJ, OKRES, OBEC, OKRSOK, VUC_OBVOD = "sk.kraj", "sk.okres", "sk.obec", "sk.okrsok", "sk.vuc_obvod"
TOWN_DISTRICT = "sk.volebny_obvod"
"""A city council's electoral district in Bratislava and Košice, the finest unit the office prints for the city
contests: its polling-district tables number districts within boroughs that one council district can join."""


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def clean(text: object) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", clean(text)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def number(text: object) -> int | None:
    s = str(text if text is not None else "").replace("\xa0", "").replace(" ", "").strip()
    if s.lower() in ("", "nan", "-", "x"):
        return None
    s = s.replace(",", ".")
    return round(float(s))


def url(path: str) -> str:
    if path.startswith("https://"):
        return path
    return f"{SITE}/opendata/{quote(path)}"


# Reading the open-data tables.


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1250")
    if text.count("Ă") > 10:
        return "\n".join(_unmangle(line) for line in text.split("\n"))
    return text


def _unmangle(line: str) -> str:
    """Lines of UTF-8 that were once read as Windows-1250 and saved again (the 1998 national table)."""
    try:
        return line.encode("cp1250").decode("utf-8")
    except UnicodeError:
        return " ".join(_unmangle(word) if " " in line.strip() else word for word in line.split(" "))


@functools.cache
def table(path: str) -> pl.DataFrame:
    """One open-data table as strings. The office's tables are comma, semicolon, or pipe separated, in UTF-8 or
    Windows-1250, some with a title row and a blank row before the header; headers may span lines."""
    text = _decode(raw.cached(SOURCE, url(path)).path.read_bytes())
    tail = [line for line in text.splitlines() if line.strip()][-5:]
    separator = max(";|,", key=lambda s: sum(line.count(s) for line in tail))
    rows = list(csv.reader(io.StringIO(text), delimiter=separator))
    while len(rows) > 1 and sum(1 for cell in rows[0] if cell.strip()) <= 1:
        rows.pop(0)
    header = [clean(cell) for cell in rows[0]]
    seen: Counter[str] = Counter()
    names = []
    for index, name in enumerate(header):
        name = name or f"_{index}"
        seen[name] += 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    width = len(names)
    rows = _rejoin(rows, width)
    body = [
        [clean(cell) or None for cell in (row + [""] * width)[:width]]
        for row in rows[1:]
        if any(c.strip() for c in row)
    ]
    return pl.DataFrame(body, schema={name: pl.String for name in names}, orient="row")


def _rejoin(rows: list[list[str]], width: int) -> list[list[str]]:
    """Rows that a line break inside an unquoted cell split in two (the 2022 regional candidate lists)."""
    out = [rows[0]]
    index = 1
    while index < len(rows):
        row = rows[index]
        while len(row) < width and index + 1 < len(rows) and len(row) + len(rows[index + 1]) - 1 <= width:
            following = rows[index + 1]
            row = [*row[:-1], f"{row[-1]} {following[0]}", *following[1:]] if row else following
            index += 1
        out.append(row)
        index += 1
    return out


def col(frame: pl.DataFrame, *words: str, exclude: Iterable[str] = ()) -> str:
    """The first column whose folded name holds every word and none of the excluded ones."""
    wanted = [fold(word) for word in words]
    unwanted = [fold(word) for word in exclude if fold(word)]
    for name in frame.columns:
        folded = fold(name)
        if all(word in folded for word in wanted) and not any(word in folded for word in unwanted):
            return name
    raise LookupError(f"no column with {words} in {frame.columns}")


def has(frame: pl.DataFrame, *words: str, exclude: Iterable[str] = ()) -> bool:
    try:
        col(frame, *words, exclude=exclude)
    except LookupError:
        return False
    return True


def ints(frame: pl.DataFrame, name: str) -> pl.Expr:
    return (
        pl.col(name)
        .str.replace_all(r"[\s\xa0]", "")
        .str.replace(",", ".")
        .replace({"nan": None, "": None, "-": None})
        .cast(pl.Float64)
        .round(0)
        .cast(pl.Int64)
    )


def precinct(text: object) -> str:
    """A polling district's number: the office prints it alone, or after the municipality's name."""
    s = clean(text)
    found = re.search(r"(\d+)\s*$", s)
    if found is None:
        raise ValueError(f"no polling district number in {text!r}")
    return str(int(found.group(1)))


# The normalized shape every reader returns.


@dataclass
class Option:
    key: str
    label: str
    kind: str
    position: int | None = None
    party: str | None = None
    person: str | None = None
    outcome: str | None = None
    seats: int | None = None
    independent: bool = False
    english: str | None = None


@dataclass
class Contest:
    key: str
    election: str
    kind: str
    name: str
    word: str
    system: str
    district: str = COUNTRY
    magnitude: int | None = None
    round: int = 1
    previous: str | None = None
    body: str | None = None
    office: str | None = None
    question: bool = False
    completeness: float | None = 1.0
    options: list[Option] = field(default_factory=list)
    declared: dict[tuple[str | None, int], int] = field(default_factory=dict)
    facts: list[pl.DataFrame] = field(default_factory=list)
    note: str = ""


@dataclass
class Election:
    key: str
    kind: str
    start: date
    end: date
    english: str
    slovak: str
    slug: str


@dataclass
class Day:
    key: str
    summary: str
    paths: list[str]
    elections: list[Election] = field(default_factory=list)
    contests: list[Contest] = field(default_factory=list)
    units: list[pl.DataFrame] = field(default_factory=list)
    method: str = ""
    accepts: dict[str, str] = field(default_factory=dict)


FACT_SCHEMA = pl.Schema(
    {
        "unit": pl.String(),
        "option": pl.String(),
        "measure": pl.Int16(),
        "value": pl.Int64(),
    }
)
UNIT_SCHEMA = pl.Schema({name: pl.String() for name in ("unit", "scheme", "obec", "okres", "kraj")})
UNIT_EXTRA = pl.Schema({"area": pl.String(), "relation": pl.String()})
"""A unit may name its area and its relation to it, where the area is not a municipality, a district, or a region."""


def units_of(
    frame: pl.DataFrame, day: date, *, okrsok: str | None, obec: str, okres: str | None = None
) -> pl.DataFrame:
    """Reporting units from a frame with a municipality code column and, at the finest level, a district column."""
    exprs = {
        "obec": pl.col(obec),
        "okres": pl.col(okres) if okres else pl.lit(None, pl.String),
        "kraj": pl.lit(None, pl.String),
    }
    if okrsok is None:
        unit = pl.concat_str(pl.lit(f"{day}:"), pl.col(obec))
        scheme = OBEC
    else:
        unit = pl.concat_str(
            pl.lit(f"{day}:"), pl.col(obec), pl.lit(":"), pl.col(okrsok).map_elements(precinct, pl.String)
        )
        scheme = OKRSOK
    return frame.select(unit=unit, scheme=pl.lit(scheme), **exprs).unique("unit", keep="first")


def unit_expr(day: date, obec: str, okrsok: str | None) -> pl.Expr:
    if okrsok is None:
        return pl.concat_str(pl.lit(f"{day}:"), pl.col(obec))
    return pl.concat_str(pl.lit(f"{day}:"), pl.col(obec), pl.lit(":"), pl.col(okrsok).map_elements(precinct, pl.String))


def accounting(frame: pl.DataFrame, unit: pl.Expr, columns: dict[int, list[str]]) -> pl.DataFrame:
    """Unit totals: each measure is one column or the sum of several."""
    parts = []
    for measure, names in columns.items():
        value = pl.sum_horizontal(*(ints(frame, name) for name in names)) if len(names) > 1 else ints(frame, names[0])
        parts.append(
            frame.select(unit=unit, option=pl.lit(None, pl.String), measure=pl.lit(measure, pl.Int16), value=value)
        )
    return pl.concat(parts).filter(pl.col("value").is_not_null())


def votes(frame: pl.DataFrame, unit: pl.Expr, option: pl.Expr, value: str) -> pl.DataFrame:
    return frame.select(
        unit=unit, option=option.cast(pl.String), measure=pl.lit(VOTES, pl.Int16), value=ints(frame, value)
    ).filter(pl.col("value").is_not_null())


def wide_votes(frame: pl.DataFrame, unit: pl.Expr, columns: dict[str, str]) -> pl.DataFrame:
    """Votes from a table with one column per option: {column: option key}."""
    parts = [
        frame.select(unit=unit, option=pl.lit(option), measure=pl.lit(VOTES, pl.Int16), value=ints(frame, name))
        for name, option in columns.items()
    ]
    return pl.concat(parts).filter(pl.col("value").is_not_null())


# National elections with polling-district files: the National Council since 2016, the presidency and the European
# Parliament since 2019, and referendums since 2010.

NRSR_DAYS = {
    1994: (date(1994, 9, 30), date(1994, 10, 1)),
    1998: (date(1998, 9, 25), date(1998, 9, 26)),
    2002: (date(2002, 9, 20), date(2002, 9, 21)),
    2006: (date(2006, 6, 17), date(2006, 6, 17)),
    2010: (date(2010, 6, 12), date(2010, 6, 12)),
    2012: (date(2012, 3, 10), date(2012, 3, 10)),
    2016: (date(2016, 3, 5), date(2016, 3, 5)),
    2020: (date(2020, 2, 29), date(2020, 2, 29)),
    2023: (date(2023, 9, 30), date(2023, 9, 30)),
}
EP_DAYS = {
    2004: date(2004, 6, 13),
    2009: date(2009, 6, 6),
    2014: date(2014, 5, 24),
    2019: date(2019, 5, 25),
    2024: date(2024, 6, 8),
}
EP_SEATS = {2004: 14, 2009: 13, 2014: 13, 2019: 14, 2024: 15}
PREZ_DAYS = {
    1999: (date(1999, 5, 15), date(1999, 5, 29)),
    2004: (date(2004, 4, 3), date(2004, 4, 17)),
    2009: (date(2009, 3, 21), date(2009, 4, 4)),
    2014: (date(2014, 3, 15), date(2014, 3, 29)),
    2019: (date(2019, 3, 16), date(2019, 3, 30)),
    2024: (date(2024, 3, 23), date(2024, 4, 6)),
}
REF_DAYS = {
    2003: (date(2003, 5, 16), date(2003, 5, 17)),
    2004: (date(2004, 4, 3), date(2004, 4, 3)),
    2010: (date(2010, 9, 18), date(2010, 9, 18)),
    2015: (date(2015, 2, 7), date(2015, 2, 7)),
    2023: (date(2023, 1, 21), date(2023, 1, 21)),
    2026: (date(2026, 7, 4), date(2026, 7, 4)),
}


def nrsr_election(year: int) -> Election:
    start, end = NRSR_DAYS[year]
    return Election(
        "nrsr",
        "general",
        start,
        end,
        f"{year} Slovak parliamentary election",
        f"Voľby do Národnej rady Slovenskej republiky {year}",
        f"sk/{start}-parliamentary",
    )


def nrsr_contest() -> Contest:
    return Contest(
        "nrsr",
        "nrsr",
        "list_allocation",
        "Národná rada Slovenskej republiky",
        "national-council",
        system("nrsr"),
        magnitude=150,
        body=NRSR_BODY,
    )


def ep_election(year: int) -> Election:
    day = EP_DAYS[year]
    return Election(
        "ep",
        "general",
        day,
        day,
        f"{year} European Parliament election in Slovakia, by municipality and polling district",
        f"Voľby do Európskeho parlamentu {year}",
        f"sk/{day}-european-parliament-units",
    )


def ep_contest(year: int) -> Contest:
    return Contest(
        "ep",
        "ep",
        "list_allocation",
        "Európsky parlament",
        "european-parliament",
        system("ep"),
        magnitude=EP_SEATS[year],
    )


def list_options(
    contest: Contest, rows: Iterable[tuple[str, str, str | None]], seats: dict[str, int] | None = None
) -> None:
    """Party lists as (ballot number, full name, abbreviation)."""
    for number, label, short in rows:
        n = (seats or {}).get(number)
        contest.options.append(
            Option(
                number,
                clean(label),
                "list",
                position=int(number),
                party=clean(label),
                english=clean(short) if short else None,
                outcome=("won" if n else "lost") if seats is not None else None,
                seats=n or None,
            )
        )


def nrsr_modern(year: int) -> Day:
    start, _ = NRSR_DAYS[year]
    t = (
        (lambda n: f"nrsr/{year}/NRSR2023_SK_{n}.csv")
        if year == 2023
        else (lambda n: f"nrsr/{year}/NRSR_{year}_{n}.csv")
    )
    d = Day(str(start), f"The {year} National Council election by polling district", [])
    d.elections.append(nrsr_election(year))
    c = nrsr_contest()
    if year == 2016:
        unit_file, vote_file, parties_file, national_file = t("tab07"), t("tab12"), t("tab20"), t("tab13")
    else:
        unit_file, vote_file, parties_file, national_file = t("tab02e"), t("tab03f"), t("tab0a"), t("tab03a")
    d.paths += [unit_file, vote_file, parties_file, national_file]
    units = table(unit_file)
    obec, okrsok = col(units, "kod obce"), col(units, "okrsok")
    u = unit_expr(start, obec, okrsok)
    d.units.append(units_of(units, start, okrsok=okrsok, obec=obec, okres=col(units, "kod okresu")))
    cast = (
        [col(units, "osobne", exclude=["podiel"]), col(units, "navratn", exclude=["podiel"])]
        if has(units, "navratn")
        else [col(units, "odovzdanych obalok", exclude=["podiel"])]
    )
    c.facts.append(
        accounting(
            units,
            u,
            {
                REGISTERED: [col(units, "zapisan")],
                VOTERS: [col(units, "zucastn", exclude=["podiel", "ucast volicov"])],
                CAST: cast,
                VALID: [col(units, "platnych hlasov", exclude=["podiel", "ucast volicov"])],
            },
        )
    )
    parties = table(parties_file)
    number, name = col(parties, "cislo"), col(parties, "nazov")
    short = col(parties, "skratka")
    national = table(national_file)
    seats = {
        row[col(national, "cislo")]: number_or_zero(row[col(national, "mandat")])
        for row in national.iter_rows(named=True)
    }
    list_options(c, parties.select(number, name, short).iter_rows(), seats)
    for row in national.iter_rows(named=True):
        c.declared[(row[col(national, "cislo")], VOTES)] = number_or_zero(row[col(national, "pocet platnych hlasov")])
    data = table(vote_file)
    if year == 2016:
        lookup = {fold(row[name]): row[number] for row in parties.iter_rows(named=True)}
        lookup |= {fold(row[short]): row[number] for row in parties.iter_rows(named=True)}
        option = pl.col(col(data, "nazov politickeho")).map_elements(lambda s: lookup[fold(s)], pl.String)
    else:
        option = pl.col(col(data, "cislo politickeho"))
    c.facts.append(votes(data, u, option, col(data, "pocet platnych hlasov", exclude=["spolu"])))
    if year == 2016:
        regions = table(t("tab03"))
        d.paths.append(t("tab03"))
        totals = {
            REGISTERED: "zapisan",
            VOTERS: "zucastnili",
            VALID: "pocet platnych hlasov",
        }
        for measure, word in totals.items():
            c.declared[(None, measure)] = int(regions.select(ints(regions, col(regions, word))).sum().item())
    else:
        summary = table(t("tab01"))
        d.paths.append(t("tab01"))
        row = summary.row(0, named=True)
        c.declared[(None, REGISTERED)] = number_or_zero(row[col(summary, "zapisan")])
        c.declared[(None, VOTERS)] = number_or_zero(row[col(summary, "zucastn", exclude=["%"])])
        c.declared[(None, VALID)] = number_or_zero(row[col(summary, "platnych hlasov spolu", exclude=["podiel"])])
    d.contests.append(c)
    return d


def number_or_zero(text: object) -> int:
    return number(text) or 0


def ep_modern(year: int) -> Day:
    day = EP_DAYS[year]
    t = (lambda n: f"eup/{year}/EP2024_SK_{n}.csv") if year == 2024 else (lambda n: f"eup/{year}/EP_{year}_SK_{n}.csv")
    d = Day(str(day), f"The {year} European Parliament election in Slovakia by polling district", [])
    d.elections.append(ep_election(year))
    c = ep_contest(year)
    paths = {n: t(n) for n in ("tab01", "tab02e", "tab03a", "tab03f", "tab04", "tab0a")}
    d.paths += list(paths.values())
    units = table(paths["tab02e"])
    obec, okrsok = col(units, "kod obce", exclude=["lsuj"]), col(units, "okrsok")
    u = unit_expr(day, obec, okrsok)
    d.units.append(units_of(units, day, okrsok=okrsok, obec=obec, okres=col(units, "kod okresu", exclude=["lsuj"])))
    c.facts.append(
        accounting(
            units,
            u,
            {
                REGISTERED: [col(units, "zapisan")],
                VOTERS: [col(units, "vydanych obalok")] if has(units, "vydanych obalok") else [col(units, "zucastn")],
                CAST: [col(units, "odovzdanych obalok", exclude=["podiel"])],
                VALID: [col(units, "platnych hlasov", exclude=["podiel"])],
            },
        )
    )
    parties = table(paths["tab0a"])
    allotted = table(paths["tab04"])
    seats = {
        row[col(allotted, "cislo")]: number_or_zero(row[col(allotted, "spolu")])
        for row in allotted.iter_rows(named=True)
    }
    list_options(
        c, parties.select(col(parties, "cislo"), col(parties, "nazov"), col(parties, "skratka")).iter_rows(), seats
    )
    national = table(paths["tab03a"])
    for row in national.iter_rows(named=True):
        c.declared[(row[col(national, "cislo")], VOTES)] = number_or_zero(row[col(national, "pocet platnych hlasov")])
    summary = table(paths["tab01"])
    row = summary.row(0, named=True)
    c.declared[(None, REGISTERED)] = number_or_zero(row[col(summary, "zapisan")])
    c.declared[(None, VALID)] = number_or_zero(row[col(summary, "platnych hlasov", exclude=["podiel"])])
    data = table(paths["tab03f"])
    c.facts.append(votes(data, u, pl.col(col(data, "cislo politickeho")), col(data, "pocet platnych hlasov")))
    d.contests.append(c)
    return d


def prez_elections(year: int) -> list[Election]:
    first, second = PREZ_DAYS[year]
    return [
        Election(
            "prez:1",
            "general",
            first,
            first,
            f"{year} Slovak presidential election",
            f"Voľba prezidenta Slovenskej republiky {year}, 1. kolo",
            f"sk/{first}-presidential",
        ),
        Election(
            "prez:2",
            "runoff",
            second,
            second,
            f"{year} Slovak presidential election, second round",
            f"Voľba prezidenta Slovenskej republiky {year}, 2. kolo",
            f"sk/{second}-presidential-runoff",
        ),
    ]


def prez_contest(year: int, round_number: int) -> Contest:
    return Contest(
        f"prez:{round_number}",
        f"prez:{round_number}",
        "office",
        "Prezident Slovenskej republiky",
        "president",
        system("president"),
        magnitude=1,
        round=round_number,
        previous="prez:1" if round_number == 2 else None,
        office=PRESIDENCY,
    )


def presidential_outcomes(contest: Contest) -> None:
    """Round one: the two leaders advance (no candidate won a majority of all registered voters in any year);
    round two: the leader is elected."""
    totals: Counter[str] = Counter()
    for frame in contest.facts:
        for option, value in (
            frame.filter(pl.col("measure") == VOTES).group_by("option").agg(pl.col("value").sum()).iter_rows()
        ):
            totals[option] += value
    ranked = [option for option, _ in totals.most_common()]
    for o in contest.options:
        if contest.round == 1:
            o.outcome = "advanced" if o.key in ranked[:2] else "eliminated"
        else:
            o.outcome = "elected" if o.key == ranked[0] else "lost"


def person_name(first: object, last: object, title: object = None) -> str:
    return clean(f"{clean(first)} {clean(last)}")


def prez_modern(year: int) -> Day:
    d = Day(str(PREZ_DAYS[year][0]), f"The {year} presidential election, both rounds, by polling district", [])
    d.elections += prez_elections(year)
    for round_number, day in enumerate(PREZ_DAYS[year], start=1):
        if year == 2024:
            t = lambda n, r=round_number: f"pre/{year}/{r}.kolo/PREZ2024_kolo{r}_SK_{n}.csv"
        else:
            t = lambda n, r=round_number: f"pre/{year}/{r}.kolo/PRE_{year}_KOLO{r}_{n}.csv"
        c = prez_contest(year, round_number)
        paths = {n: t(n) for n in ("tab01", "tab02e", "tab03f", "tab0a")}
        d.paths += list(paths.values())
        units = table(paths["tab02e"])
        obec, okrsok = col(units, "kod obce", exclude=["lsuj"]), col(units, "okrsok")
        u = unit_expr(day, obec, okrsok)
        d.units.append(units_of(units, day, okrsok=okrsok, obec=obec, okres=col(units, "kod okresu", exclude=["lsuj"])))
        c.facts.append(
            accounting(
                units,
                u,
                {
                    REGISTERED: [col(units, "zapisan")],
                    VOTERS: [col(units, "vydanych obalok")]
                    if has(units, "vydanych obalok")
                    else [col(units, "zucastn")],
                    CAST: [col(units, "odovzdanych obalok", exclude=["podiel"])],
                    VALID: [col(units, "platnych hlasov", exclude=["podiel"])],
                },
            )
        )
        candidates = table(paths["tab0a"])
        for row in candidates.iter_rows(named=True):
            number = row[col(candidates, "poradie")]
            name = person_name(row[col(candidates, "meno")], row[col(candidates, "priezvisko")])
            c.options.append(Option(number, name, "candidate", position=int(number), person=name))
        data = table(paths["tab03f"])
        c.facts.append(votes(data, u, pl.col(col(data, "poradie")), col(data, "pocet platnych hlasov")))
        summary = table(paths["tab01"])
        row = summary.row(0, named=True)
        c.declared[(None, REGISTERED)] = number_or_zero(row[col(summary, "zapisan")])
        c.declared[(None, VALID)] = number_or_zero(row[col(summary, "platnych hlasov", exclude=["podiel"])])
        presidential_outcomes(c)
        d.contests.append(c)
    return d


def ref_election(year: int) -> Election:
    start, end = REF_DAYS[year]
    return Election(
        "ref",
        "referendum",
        start,
        end,
        f"{year} Slovak referendum",
        f"Referendum {year}",
        f"sk/{start}-referendum",
    )


def ref_contest(question: int, text: str | None) -> Contest:
    c = Contest(
        f"ref:{question}",
        "ref",
        "ballot_question",
        f"Otázka č. {question}",
        f"question-{question}",
        system("referendum"),
        question=True,
        note=text or "",
    )
    c.options = [
        Option("yes", "Áno", "answer", position=1, english="Yes"),
        Option("no", "Nie", "answer", position=2, english="No"),
    ]
    return c


def referendum_outcomes(contest: Contest, registered: int) -> None:
    """A referendum is valid when more than half of the registered voters take part, and a proposal carries when more
    than half of those who took part vote yes (Constitution, Art. 98)."""
    totals: Counter[str] = Counter()
    voters = 0
    for frame in contest.facts:
        for option, measure, value in frame.group_by("option", "measure").agg(pl.col("value").sum()).iter_rows():
            if measure == VOTES:
                totals[option] += value
            elif measure == VOTERS and option is None:
                voters += value
    carried = voters * 2 > registered and totals["yes"] * 2 > voters
    for o in contest.options:
        o.outcome = "won" if (o.key == "yes") == carried else "lost"


def ref_modern(year: int) -> Day:
    start, _ = REF_DAYS[year]
    d = Day(str(start), f"The {year} referendum by polling district", [])
    d.elections.append(ref_election(year))
    t = lambda n: f"ref/{year}/REF{year}_SK_{n}.csv"
    paths = {n: t(n) for n in ("tab01", "tab02e", "tab03f", "tab0a")}
    d.paths += list(paths.values())
    units = table(paths["tab02e"])
    if year == 2023:
        obec, okrsok = "OBEC", "OKRSOK"
        columns = {REGISTERED: ["P_ZAP"], VOTERS: ["P_ZUC"], CAST: ["P_OOO", "P_NOC"], VALID: ["P_PHL"]}
    else:
        obec, okrsok = col(units, "kod obce"), col(units, "okrsok")
        columns = {
            REGISTERED: [col(units, "zapisan")],
            VOTERS: [col(units, "zucastn")],
            CAST: [col(units, "odovzdanych obalok", exclude=["podiel"])],
        }
    u = unit_expr(start, obec, okrsok)
    d.units.append(
        units_of(units, start, okrsok=okrsok, obec=obec, okres=None if year == 2023 else col(units, "kod okresu"))
    )
    shared = accounting(units, u, {m: c for m, c in columns.items() if m != VALID})
    questions = table(paths["tab0a"])
    texts = {row[col(questions, "cislo")]: row[col(questions, "znenie")] for row in questions.iter_rows(named=True)}
    data = table(paths["tab03f"])
    summary = table(paths["tab01"]).row(0, named=True)
    registered = number_or_zero(summary[next(k for k in summary if "zapísan" in k)])
    for question, text in sorted(texts.items(), key=lambda item: int(item[0])):
        c = ref_contest(int(question), text)
        c.facts.append(shared)
        if year == 2023:
            c.facts.append(accounting(units, u, {VALID: columns[VALID]}))
            here = data
            c.facts.append(votes(here, unit_expr(start, "OBEC", "OKRSOK"), pl.lit("yes"), "P_ANO"))
            c.facts.append(votes(here, unit_expr(start, "OBEC", "OKRSOK"), pl.lit("no"), "P_NIE"))
        else:
            here = data.filter(pl.col(col(data, "cislo otazky")) == question)
            hu = unit_expr(start, col(data, "kod obce"), col(data, "okrsok"))
            c.facts.append(votes(here, hu, pl.lit("yes"), col(here, "pocet hlasov ano")))
            c.facts.append(votes(here, hu, pl.lit("no"), col(here, "pocet hlasov nie")))
            c.facts.append(
                here.select(
                    unit=hu,
                    option=pl.lit(None, pl.String),
                    measure=pl.lit(INVALID, pl.Int16),
                    value=ints(here, col(here, "pocet neplatnych")),
                )
            )
        c.declared[(None, REGISTERED)] = registered
        referendum_outcomes(c, registered)
        d.contests.append(c)
    return d


# Places: regions (kraje), districts (okresy), and municipalities (obce) as the office codes them.

TERRITORY = [
    "oso/2026/OSO2026_SK_tab0dd.csv",
    "ref/2026/REF2026_SK_tab0b.csv",
    "eup/2024/EP2024_SK_tab0c.csv",
    "nrsr/2023/NRSR2023_SK_tab0c.csv",
    "oso/2022/OSO2022_SK_tab0dd.csv",
    "nrsr/2020/NRSR_2020_tab0c.csv",
    "pre/2019/1.kolo/PRE_2019_tab0c.csv",
    "oso/2018/OSO_2018_tab0c.csv",
    "nrsr/2016/NRSR_2016_tab07.csv",
    "ref/2010/REF_2010_tab02.csv",
    "nrsr/1994/NRSR_1994_tab05.csv",
]
"""Tables that print municipality codes with names, newest first: a municipality takes its newest name and district."""
DISTRICT_TABLES = ("osk/2017/OSK_2017_tab0db.csv", "osk/2022/OSK2022_SK_tab0db.csv", "osk/2026/OSK2026_SK_tab0db.csv")
"""The regional electoral districts and their seats, cited by the geo package."""
CITIES = {"582000": ("Bratislava", "1"), "599981": ("Košice", "8")}
"""Bratislava and Košice elect a city mayor and council over their boroughs (mestské časti), which the office codes as
municipalities."""


@dataclass
class Places:
    kraje: dict[str, str]
    okresy: dict[str, tuple[str, str]]
    obce: dict[str, tuple[str, str | None]]


@functools.cache
def places() -> Places:
    kraje: dict[str, str] = {}
    okresy: dict[str, tuple[str, str]] = {}
    obce: dict[str, tuple[str, str | None]] = {}
    for path in TERRITORY:
        frame = table(path)
        kraj = col(frame, "kod", "kraj", exclude=["rsuj"]) if has(frame, "kod", "kraj", exclude=["rsuj"]) else None
        kraj_name = col(frame, "kraj", exclude=["kod"]) if kraj and has(frame, "kraj", exclude=["kod"]) else None
        okres = col(frame, "kod okresu", exclude=["lsuj"]) if has(frame, "kod okresu", exclude=["lsuj"]) else None
        okres_name = col(frame, "okres", exclude=["kod"]) if okres and has(frame, "okres", exclude=["kod"]) else None
        obec = col(frame, "kod obce", exclude=["lsuj"])
        obec_name = (
            col(frame, "nazov obce") if has(frame, "nazov obce") else col(frame, "obec", exclude=["kod", "pocet"])
        )
        modern = okres is not None and not path.startswith("nrsr/1994")
        for row in frame.iter_rows(named=True):
            code = row[obec]
            if not code or not code.isdigit():
                continue
            if modern and kraj and kraj_name and row[kraj] and row[kraj].isdigit():
                kraje.setdefault(row[kraj], row[kraj_name])
            if modern and okres and okres_name and row[okres] and row[kraj or okres]:
                okresy.setdefault(row[okres], (row[okres_name], row[kraj] if kraj else ""))
            parent = row[okres] if modern and okres else None
            obce.setdefault(code, (clean(row[obec_name]), parent))
    return Places(kraje, okresy, obce)


def unit_area(obec: str | None, okres: str | None, kraj: str | None) -> str:
    known = places()
    if obec in known.obce or obec in CITIES:
        return area(OBEC, str(obec))
    if okres in known.okresy:
        return area(OKRES, str(okres))
    if kraj in known.kraje:
        return area(KRAJ, str(kraj))
    return COUNTRY


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Slovakia's 8 regions (kraje), 79 districts (okresy), and its municipalities and city boroughs (obce) as the "
        "Statistical Office codes them",
    )
    p.external |= {SLOVAKIA, COUNTRY}
    source(p)
    p.artifact(raw.cached(SOURCE, TREE), VOLBY_SOURCE)
    for path in TERRITORY:
        p.artifact(raw.cached(SOURCE, url(path)), VOLBY_SOURCE)
    for code, rank, kind, noun, system_ in (
        (KRAJ, 1, "administrative", "region", "nuts3"),
        (OKRES, 2, "administrative", "district", "lau1"),
        (OBEC, 3, "administrative", "municipality", "lau2"),
        (OKRSOK, 4, "precinct", "polling district", None),
        (VUC_OBVOD, 2, "electoral_district", "regional electoral district", None),
        (TOWN_DISTRICT, 4, "electoral_district", "municipal electoral district", None),
        (KRAJ_90, 1, "electoral_district", "electoral region (1990-1996)", None),
        (OKRES_90, 2, "administrative", "district (1990-1996)", None),
    ):
        p.add("scheme", code=code, polity_id=SLOVAKIA, kind=kind, rank=rank, noun=noun, code_system=system_)

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "sk", "official")

    known = places()
    for code, text in sorted(known.kraje.items()):
        place(KRAJ, code, text, COUNTRY)
    for code, (text, kraj) in sorted(known.okresy.items()):
        place(OKRES, code, text, area(KRAJ, kraj) if kraj in known.kraje else COUNTRY)
    for code, (text, okres) in sorted(known.obce.items()):
        place(OBEC, code, text, area(OKRES, okres) if okres in known.okresy else COUNTRY)
    for code, (text, kraj) in CITIES.items():
        if code not in known.obce:
            place(OBEC, code, text, area(KRAJ, kraj))
    for path in DISTRICT_TABLES:
        p.artifact(raw.cached(SOURCE, url(path)), VOLBY_SOURCE)
    for code, (text, kraj) in sorted(vuc_obvody().items()):
        place(
            VUC_OBVOD,
            code,
            f"{known.kraje[kraj]}, {text.lower() if text.startswith('Volebný') else text}",
            area(KRAJ, kraj),
        )
    for path in sorted(old_geo_paths()):
        p.artifact(raw.cached(SOURCE, url(path)), VOLBY_SOURCE)
    for code, (text, kraj, since, until) in sorted(old_districts().items()):
        id = area(VUC_OBVOD, code)
        p.add("area", id=id, scheme_code=VUC_OBVOD)
        p.add("area_code", area_id=id, code=code, valid_from=since, valid_to=until)
        p.add("area_parent", area_id=id, parent_id=area(KRAJ, kraj), valid_from=since, valid_to=until)
        p.name(id, f"{known.kraje[kraj]}, {text}", "sk", "official")
    for path in sorted(snr_geo_paths()):
        p.artifact(raw.cached(SOURCE, path), VOLBY_SOURCE)
    snr_kraje_, snr_okresy = snr_places()
    for slug, text in sorted(snr_kraje_.items()):
        id = area(KRAJ_90, slug)
        p.add("area", id=id, scheme_code=KRAJ_90)
        p.add("area_code", area_id=id, code=slug, valid_from=SNR_FROM, valid_to=SNR_UNTIL)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=SNR_FROM, valid_to=SNR_UNTIL)
        p.name(id, text, "sk", "official")
    for slug, (text, kraj) in sorted(snr_okresy.items()):
        id = area(OKRES_90, slug)
        p.add("area", id=id, scheme_code=OKRES_90)
        p.add("area_code", area_id=id, code=slug, valid_from=SNR_FROM, valid_to=SNR_UNTIL)
        p.add("area_parent", area_id=id, parent_id=area(KRAJ_90, kraj), valid_from=SNR_FROM, valid_to=SNR_UNTIL)
        p.name(id, text, "sk", "official")
    return p


def source(p: Package) -> None:
    p.add(
        "source",
        id=VOLBY_SOURCE,
        kind="official_body",
        publisher="Štatistický úrad Slovenskej republiky",
        title="Voľby a referendá: údaje na stiahnutie (open data)",
        url=f"{SITE}/tree.html",
        license="Open data of the Statistical Office of the Slovak Republic: free reuse with the source acknowledged",
        license_url="https://slovak.statistics.sk",
        attribution="Štatistický úrad Slovenskej republiky, volby.statistics.sk",
    )


VOLBY_SOURCE = key("source", SOURCE)
NRSR_BODY = key("body", "nrsr")
PRESIDENCY = key("office", "president")


def regional_council(kraj: str) -> str:
    return key("body", "vuc", kraj)


def regional_chair(kraj: str) -> str:
    return key("office", "vuc", kraj)


def town_council(obec: str) -> str:
    return key("body", "obec", obec)


def mayor(obec: str) -> str:
    return key("office", "obec", obec)


def system(name: str) -> str:
    return key("electoral_system", name)


def party(label: str) -> str:
    return key("party", fold(label))


SYSTEMS: dict[str, tuple[date, dict[str, object], str]] = {
    "nrsr": (
        date(1993, 1, 1),
        {
            "formula": "hagenbach_bischoff",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
        },
        (
            "The National Council's 150 seats are allocated by the Hagenbach-Bischoff quota (the republic electoral number, "
            "valid votes for qualifying lists divided by 151) and the largest remainders, among the lists that pass the "
            "threshold: 5% for a party, 7% for a coalition of two or three parties, and 10% for four or more (the 1994 "
            "election had four constituencies with a national threshold). Preference votes move a candidate up the list "
            "once they reach 3% of the list's votes (10% before 2006)."
        ),
    ),
    "ep": (
        date(2004, 1, 1),
        {
            "formula": "hagenbach_bischoff",
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
        },
        (
            "Slovakia elects its members of the European Parliament in one national constituency by the Hagenbach-Bischoff "
            "quota and the largest remainders, among the lists with at least 5% of the valid votes."
        ),
    ),
    "president": (
        date(1999, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "Direct election since 1999: a candidate who wins the votes of more than half of all registered voters in the "
            "first round is elected; otherwise the two leading candidates meet in a second round two weeks later, which the "
            "candidate with more valid votes wins."
        ),
    ),
    "referendum": (
        date(1993, 1, 1),
        {"formula": "referendum"},
        (
            "A national referendum is valid when more than half of the registered voters take part, and a proposal carries "
            "when more than half of the voters who took part vote yes."
        ),
    ),
    "vuc-chair-2001": (
        date(2001, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "The chair of a self-governing region was elected in two rounds until 2013: a candidate with a majority of the "
            "valid votes was elected in the first round, otherwise the two leading candidates met two weeks later."
        ),
    ),
    "vuc-chair-2017": (
        date(2017, 1, 1),
        {"formula": "fptp"},
        "Since 2017 the chair of a self-governing region is elected in one round by plurality.",
    ),
    "vuc-council": (
        date(2001, 1, 1),
        {"formula": "block"},
        (
            "Regional councillors are elected in multi-member electoral districts: each voter may vote for as many "
            "candidates as the district elects, and the candidates with the most votes win."
        ),
    ),
    "mayor": (
        date(1990, 1, 1),
        {"formula": "fptp"},
        "Mayors (starosta, primátor) are elected in one round by plurality.",
    ),
    "town-council": (
        date(1990, 1, 1),
        {"formula": "block"},
        (
            "Municipal councillors are elected in one or more multi-member electoral districts: each voter may vote for as "
            "many candidates as the district elects, and the candidates with the most votes win."
        ),
    ),
}


SNR_SYSTEMS = {1990: (date(1990, 1, 1), date(1990, 12, 31), 3), 1992: (date(1991, 1, 1), date(1992, 12, 31), 5)}
SNR_METHOD = (
    "The 150 members of the Slovak National Council were elected by party-list proportional representation in four "
    "electoral regions, the Bratislava region and the West, Central, and East Slovak regions, among the lists that won "
    "at least {percent}% of the valid votes in the republic."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Slovak parties and coalitions on the Statistical Office's ballots, the National Council, the presidency, the "
        "regional and municipal councils and executives, and their electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {SLOVAKIA}
    p.add("body", id=NRSR_BODY, polity_id=SLOVAKIA, kind="unicameral")
    p.name(NRSR_BODY, "National Council of the Slovak Republic", "en", "official")
    p.name(NRSR_BODY, "Národná rada Slovenskej republiky", "sk", "native", False)
    p.add("body", id=SNR_BODY, polity_id=SLOVAKIA, kind="unicameral")
    p.name(SNR_BODY, "Slovak National Council", "en", "official")
    p.name(SNR_BODY, "Slovenská národná rada", "sk", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=SLOVAKIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Slovak Republic", "en", "official")
    p.name(PRESIDENCY, "Prezident Slovenskej republiky", "sk", "native", False)
    for name, (since, rules, method) in SYSTEMS.items():
        id = system(name)
        p.add(
            "electoral_system", id=id, polity_id=SLOVAKIA, valid_from=since, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for year, (since, until, percent) in SNR_SYSTEMS.items():
        id = system(f"snr-{year}")
        rules = {
            "formula": "hagenbach_bischoff",
            "threshold": {"level": "list", "scope": "national", "percent": percent},
        }
        p.add(
            "electoral_system",
            id=id,
            polity_id=SLOVAKIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="methodology",
            body_md=SNR_METHOD.format(percent=percent),
            lang="en",
        )
    known = places()
    bodies: set[str] = set()
    offices: set[str] = set()
    labels: dict[str, tuple[date, str, str | None]] = {}
    for d in days():
        for c in d.contests:
            if c.body and c.body not in (NRSR_BODY, SNR_BODY):
                bodies.add(c.body)
            if c.office and c.office != PRESIDENCY:
                offices.add(c.office)
            for o in c.options:
                if o.party:
                    when = d.elections[0].start
                    seen = labels.get(fold(o.party))
                    if seen is None or seen[0] <= when:
                        labels[fold(o.party)] = (when, o.party, o.english or (seen[2] if seen else None))
    for kraj, text in sorted(known.kraje.items()):
        if regional_council(kraj) in bodies:
            p.add("body", id=regional_council(kraj), polity_id=SLOVAKIA, kind="council")
            p.name(regional_council(kraj), f"Zastupiteľstvo samosprávneho kraja, {text}", "sk", "official")
        if regional_chair(kraj) in offices:
            p.add("office", id=regional_chair(kraj), polity_id=SLOVAKIA, kind="governor")
            p.name(regional_chair(kraj), f"Predseda samosprávneho kraja, {text}", "sk", "official")
    towns = {code: text for code, (text, _) in known.obce.items()} | {c: t for c, (t, _) in CITIES.items()}
    for code, text in sorted(towns.items()):
        if town_council(code) in bodies:
            p.add("body", id=town_council(code), polity_id=SLOVAKIA, kind="council")
            p.name(town_council(code), f"Zastupiteľstvo, {text}", "sk", "official")
        if mayor(code) in offices:
            p.add("office", id=mayor(code), polity_id=SLOVAKIA, kind="mayor")
            p.name(mayor(code), f"Starosta, {text}", "sk", "official")
    missing = (bodies - {town_council(c) for c in towns} - {regional_council(k) for k in known.kraje}) | (
        offices - {mayor(c) for c in towns} - {regional_chair(k) for k in known.kraje}
    )
    if missing:
        raise ValueError(f"{len(missing)} bodies or offices have no place")
    for folded, (_, label, short) in sorted(labels.items()):
        id = party(label)
        p.add("party", id=id, kind="bloc" if "," in label else "party", polity_id=SLOVAKIA)
        p.add("external_id", entity_id=id, scheme="sk.volby.party", value=folded)
        p.name(id, label, "sk", "official")
        if short and fold(short) != folded:
            p.name(id, short, "sk", "abbreviation", False)
    return p


METHOD = (
    "Counted by the election commissions and published by the Statistical Office of the Slovak Republic "
    "(Štatistický úrad SR) in its open data on volby.statistics.sk. Registered voters are the electoral roll, voters "
    "are those who were issued an envelope, ballots cast are the envelopes put in the box (with the return envelopes "
    "mailed from abroad where the office counts them), and valid votes are the valid votes for the contest's options."
)


def build(d: Day) -> Package:
    p = Package(f"{SOURCE}/{d.key}", SOURCE, d.summary, depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"])
    p.external |= {SLOVAKIA, COUNTRY}
    stored: set[str] = set()
    for path in dict.fromkeys(d.paths):
        cached = raw.cached(SOURCE, url(path))
        if path in TERRITORY or path in DISTRICT_TABLES or path in old_geo_paths() or path in snr_geo_paths():
            p.uses(cached)
        elif cached.storage_key not in stored:  # two tables with the same bytes are one artifact
            stored.add(cached.storage_key)
            p.artifact(cached, VOLBY_SOURCE)
    elections: dict[str, str] = {}
    slugs: dict[str, str] = {}
    for e in d.elections:
        id = elections[e.key] = key("election", d.key, e.key)
        slugs[e.key] = e.slug
        p.add(
            "election",
            id=id,
            polity_id=SLOVAKIA,
            kind=e.kind,
            date_from=e.start,
            date_to=e.end,
            date_precision="day",
            status="held",
        )
        p.name(id, e.english, "en", "official")
        p.name(id, e.slovak, "sk", "native", False)
        p.add("slug", path=e.slug, entity_id=id, is_current=True)
    both = pl.Schema({**UNIT_SCHEMA, **UNIT_EXTRA})
    units = pl.concat(
        [
            u.with_columns(**{n: pl.lit(None, pl.String) for n in UNIT_EXTRA if n not in u.columns})
            .select(list(both))
            .cast(both)
            for u in d.units
        ]
        or [pl.DataFrame(schema=both)]
    ).unique("unit", keep="first")
    unit_ids = {code: key("unit", code) for code in units["unit"]}
    p.frame(
        "reporting_unit",
        units.select(
            id=pl.col("unit").replace_strict(unit_ids),
            polity_id=pl.lit(SLOVAKIA),
            scheme_code="scheme",
            code="unit",
            area_relation=pl.when(pl.col("obec") == ABROAD)
            .then(pl.lit("unlocated"))
            .otherwise(pl.col("relation").fill_null("within")),
            area_id=pl.when(pl.col("obec") == ABROAD)
            .then(pl.lit(None, pl.String))
            .otherwise(
                pl.coalesce(
                    pl.col("area"),
                    pl.struct("obec", "okres", "kraj").map_elements(
                        lambda r: unit_area(r["obec"], r["okres"], r["kraj"]), pl.String
                    ),
                )
            ),
            kind=pl.when(pl.col("obec") == ABROAD).then(pl.lit("postal_district")).otherwise(pl.lit("standard")),
        ),
    )
    known = {party(label) for label in _party_labels()}
    contests: dict[str, str] = {}
    sets: dict[str, str] = {}
    caveats: Counter[str] = Counter()
    people: set[str] = set()
    for c in d.contests:
        contest = contests[c.key] = key("contest", d.key, c.key)
        result_set = sets[c.key] = key("result_set", d.key, c.key)
        p.add(
            "contest",
            id=contest,
            election_id=elections[c.election],
            kind=c.kind,
            office_id=c.office,
            body_id=c.body,
            district_area_id=c.district,
            magnitude=c.magnitude,
            round_number=c.round,
            previous_round_id=contests[c.previous] if c.previous else None,
            electoral_system_id=c.system,
            default_result_kind="counted",
        )
        p.name(contest, c.name, "sk", "official")
        p.add("slug", path=f"{slugs[c.election]}/{c.word}", entity_id=contest, is_current=True)
        if c.question:
            p.add("ballot_question", contest_id=contest, type="referendum")
        if c.note:
            p.add("note", id=key("note", contest), entity_id=contest, kind="context", body_md=c.note, lang="sk")
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[c.election],
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VOLBY_SOURCE,
            status="active",
            is_default=True,
            completeness=c.completeness,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + d.method,
            lang="en",
        )
        options: dict[str, str] = {}
        for o in c.options:
            option = options[o.key] = key("option", d.key, c.key, o.key)
            party_id = party(o.party) if o.party and party(o.party) in known else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=o.kind,
                party_id=party_id,
                ballot_position=o.position,
                ballot_label=o.label,
            )
            if o.english and o.kind == "answer":
                p.name(option, o.english, "en", "official")
            candidacy = None
            if o.person:
                if c.key.startswith("prez"):
                    person = key("person", d.key, "prez", fold(o.person))
                else:
                    person = key("person", d.key, c.key, o.key)
                if person not in people:
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, o.person, "sk", "official")
                candidacy = key("candidacy", d.key, c.key, o.key)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=o.person,
                    member_of_party_id=None,
                    is_independent=o.independent,
                )
            if o.outcome:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=o.outcome, seats=o.seats)
        for (option_key, measure), value in c.declared.items():
            if option_key is not None and option_key not in options:
                raise ValueError(f"{d.key} {c.key}: declared option {option_key} is not on the ballot")
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=None if option_key is None else options[option_key],
                measure=measure,
                value=value,
            )
        if not c.facts:
            continue
        facts = pl.concat([f.select(list(FACT_SCHEMA)).cast(FACT_SCHEMA) for f in c.facts])
        stray = set(facts["option"].drop_nulls().unique()) - set(options)
        if stray:
            raise ValueError(f"{d.key} {c.key}: votes for options not on the ballot: {sorted(stray)[:5]}")
        lost = set(facts["unit"].unique()) - set(unit_ids)
        if lost:
            raise ValueError(f"{d.key} {c.key}: values for unknown units: {sorted(lost)[:5]}")
        twice = facts.filter(pl.struct("unit", "option", "measure").is_duplicated())
        if twice.height:
            raise ValueError(f"{d.key} {c.key}: {twice.height} values appear twice: {twice.head(4)}")
        option_map = {k: v for k, v in options.items()}
        p.frame(
            "result_value",
            facts.select(
                result_set_id=pl.lit(result_set),
                unit_id=pl.col("unit").replace_strict(unit_ids),
                option_id=pl.col("option").replace_strict(option_map, default=None),
                channel=pl.lit(0),
                measure="measure",
                count_round=pl.lit(0),
                flags=pl.lit(0),
                value="value",
            ),
        )
        caveats.update(_caveats(facts))
        _declared(d, c, facts, caveats)
    _accept(p, d, caveats)
    return p


def _caveats(facts: pl.DataFrame) -> Counter[str]:
    wide = (
        facts.filter(pl.col("option").is_null())
        .pivot(on="measure", index="unit", values="value", aggregate_function="sum")
        .rename(lambda name: f"m{name}" if name != "unit" else name)
    )
    found: Counter[str] = Counter()
    cast = "m3" if "m3" in wide.columns else "m14" if "m14" in wide.columns else None
    if cast and "m2" in wide.columns:
        found["turnout"] += wide.filter(pl.col(cast) > pl.col("m2")).height
    if "m3" in wide.columns and "m4" in wide.columns:
        found["ballots"] += wide.filter(pl.col("m4") > pl.col("m3")).height
    return found


EXPLAINED: dict[tuple[str, str, int], str] = {}
"""Declared totals that the office's finest table does not add up to, each with the reason the office's own figures
give."""


def _declared(d: Day, c: Contest, facts: pl.DataFrame, caveats: Counter[str]) -> None:
    sums = {
        (option, measure): value
        for option, measure, value in facts.group_by("option", "measure").agg(pl.col("value").sum()).iter_rows()
    }
    for (option, measure), value in c.declared.items():
        got = sums.get((option, measure))
        if got == value:
            continue
        roll = facts.filter(pl.col("unit").str.contains(f":{ABROAD}") & (pl.col("measure") == REGISTERED)).height
        abroad = facts.filter(pl.col("unit").str.contains(f":{ABROAD}")).height
        if measure == REGISTERED and option is None and abroad and not roll and got is not None and value > got:
            caveats[
                f"declared:the national roll includes {value - got:,} voters on the list for the postal vote from "
                f"abroad, whose polling districts (code {ABROAD}) print no roll"
            ] += 1
            continue
        reason = EXPLAINED.get((d.key, c.key, measure))
        if reason is None:
            raise ValueError(
                f"{d.key} {c.key}: declared {option} measure {measure} is {value:,}, the units sum to {got}"
            )
        caveats[f"declared:{reason}"] += 1


def _accept(p: Package, d: Day, caveats: Counter[str]) -> None:
    for code, reason in d.accepts.items():
        p.accept(code, reason)
    reasons = sorted({k.removeprefix("declared:") for k in caveats if k.startswith("declared:")})
    if reasons:
        p.accept("accounting.declared", "; ".join(reasons))
    if caveats["turnout"]:
        p.accept(
            "accounting.turnout",
            f"{caveats['turnout']} units took more ballots than their roll lists: voters with a voting certificate "
            "(voličský preukaz) vote outside their home polling district, and return envelopes from abroad are counted "
            "where the office assigns them; the office's figures are stored as printed",
        )
    if caveats["ballots"]:
        p.accept(
            "accounting.ballots_sum",
            f"{caveats['ballots']} units print more valid votes than envelopes in the box; stored as printed",
        )


@functools.cache
def _party_labels() -> frozenset[str]:
    return frozenset(o.party for d in days() for c in d.contests for o in c.options if o.party)


READERS: dict[str, Callable[[], Day]] = {}


def reader(fn: Callable[[], Day], name: str) -> None:
    READERS[name] = fn


def _register() -> None:
    for year in (2016, 2020, 2023):
        reader(functools.partial(nrsr_modern, year), f"nrsr-{year}")
    for year in (2019, 2024):
        reader(functools.partial(ep_modern, year), f"ep-{year}")
        reader(functools.partial(prez_modern, year), f"prez-{year}")
    for year in (2023, 2026):
        reader(functools.partial(ref_modern, year), f"ref-{year}")
    reader(nrsr_1994, "nrsr-1994")
    reader(nrsr_1998, "nrsr-1998")
    for year in SNR_DAYS:
        reader(functools.partial(snr, year), f"snr-{year}")
    for year in NRSR_OLD:
        reader(functools.partial(nrsr_municipal, year), f"nrsr-{year}")
    for year in (2004, 2009, 2014):
        reader(functools.partial(ep_municipal, year), f"ep-{year}")
    for year in PREZ_OLD:
        reader(functools.partial(prez_municipal, year), f"prez-{year}")
    reader(prez_1999, "prez-1999")
    for year in REF_OLD:
        reader(functools.partial(ref_municipal, year), f"ref-{year}")
    for year in (2010, 2015):
        reader(functools.partial(ref_precinct, year), f"ref-{year}")
    for year in (2017, 2022, 2026):
        reader(functools.partial(osk_modern, year), f"osk-{year}")
    for year in OSK_OLD:
        reader(functools.partial(osk_old, year), f"osk-{year}")
    for year in (2022, 2026):
        reader(functools.partial(oso_modern, year), f"oso-{year}")
    for year in (2014, 2018):
        reader(functools.partial(oso_mayors, year), f"oso-{year}")
    for year in OSO_OLD:
        reader(functools.partial(oso_old, year), f"oso-{year}")
    for day in NOVE_DAYS:
        reader(functools.partial(nove_day, day), f"nove-{day}")


@functools.cache
def days() -> list[Day]:
    """Every election day, with elections held on the same day merged into one package."""
    merged: dict[str, Day] = {}
    for name, fn in READERS.items():
        d = fn()
        print(f"{SOURCE}: read {name}", flush=True)
        if d.key in merged:
            m = merged[d.key]
            m.summary = f"{m.summary}; {d.summary[0].lower()}{d.summary[1:]}"
            m.paths += d.paths
            m.elections += d.elections
            m.contests += d.contests
            m.units += d.units
            m.method += d.method
        else:
            merged[d.key] = d
    return [merged[k] for k in sorted(merged)]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d.key}" for d in days())]


TREE = f"{SITE}/tree.html"
SKIPPED = re.compile(r"tab07[a-f]|NRSR_20\d\d_tab1[45678]|EUP_2014_tab0[1-4]|EUP_2009_tab01|Prednostne|\.xls$")
"""Preference votes for candidates within lists, which the archive does not store yet, and spreadsheet copies."""


def files() -> list[str]:
    """Every open-data table the office lists on its download page, but the preference-vote tables."""
    text = raw.cached(SOURCE, TREE).path.read_text("utf-8", errors="replace")
    found = [path.replace("\\", "/") for path in re.findall(r'href="opendata[\\/]([^"]+)"', text)]
    return [path for path in dict.fromkeys(found) if not SKIPPED.search(path)]


def fetch() -> None:
    raw.fetch(SOURCE, TREE, suffix=".html")
    pages = [*PREZ_1999_SUMMARY.values(), *PREZ_1999_VOTES.values(), *PREZ_1999_NATIONAL.values(), *PREZ_1999_INFO]
    for page in dict.fromkeys(pages):
        raw.fetch(SOURCE, page_url(page), suffix=".html")
    for year in SNR_DAYS:
        for page in ("zvk", "zps", "pph"):
            raw.fetch(SOURCE, snr_page(year, page), suffix=".html")
        raw.fetch(SOURCE, snr_image(year), suffix=".jpg")
    for path in files():
        raw.fetch(SOURCE, url(path), suffix=".csv")
    for page in NOVE_PAGES:
        raw.fetch(SOURCE, page, suffix=".html")
    for _, source in NOVE_DAYS.values():
        if source.endswith(".zip"):
            raw.fetch(SOURCE, source, suffix=".zip")


def parse() -> None:
    geo().write()
    registry().write()
    for d in days():
        build(d).write()


def emit() -> None:
    package.run("load", packages())


# Municipalities named without codes. Tables before 2016 print a municipality by its name, grouped by district (okres)
# or by territorial district (územný obvod) in a column or in total rows after each group; a name is resolved within
# its group, and a name shared within one group by the neighbouring rows' district.

SUMMARY = re.compile(r"^(spolu|okres |obvod |kraj |v tom|obce|mesta|sr$|s p o l u|za sr|slovenska republika)")


@functools.cache
def directory() -> dict[str, list[tuple[str, frozenset[str]]]]:
    """Folded municipality name → [(code, folded names of its district and territorial district)]."""
    groups: dict[str, set[str]] = {}
    names: dict[str, set[str]] = {}
    for path in TERRITORY:
        frame = table(path)
        obec = col(frame, "kod obce", exclude=["lsuj"])
        obec_name = (
            col(frame, "nazov obce") if has(frame, "nazov obce") else col(frame, "obec", exclude=["kod", "pocet"])
        )
        labels = [
            name
            for name in frame.columns
            if fold(name) in ("okres", "nazov okresu", "obvod", "nazov uzemneho obvodu", "nazov obvodu")
        ]
        for row in frame.iter_rows(named=True):
            code = row[obec]
            if not code or not code.isdigit():
                continue
            names.setdefault(code, set()).add(fold(row[obec_name]))
            groups.setdefault(code, set()).update(group_key(row[label]) for label in labels if row[label])
    for code, (text, _) in CITIES.items():
        names.setdefault(code, set()).update({fold(text), fold(f"{text} hl m sr"), fold(f"{text} mesto")})
        groups.setdefault(code, set()).update({fold(f"{text} mesto"), fold(f"{text} - mesto")})
    out: dict[str, list[tuple[str, frozenset[str]]]] = {}
    for code, folded in names.items():
        for name in folded:
            out.setdefault(name, []).append((code, frozenset(groups.get(code, set()))))
    return out


def group_key(text: object) -> str:
    return re.sub(r"^(okres|obvod|spolu za okres|spolu za obvod|spolu za) ", "", fold(text))


RENAMED: dict[str, str] = {}
"""Municipality names in old tables that no later table prints, mapped to the later name."""


def resolve(rows: list[tuple[str, str | None]]) -> list[str | None]:
    """Codes for (name, group) rows in the table's order."""
    found: list[str | None] = []
    options: list[list[tuple[str, frozenset[str]]]] = []
    known = directory()
    for name, group in rows:
        folded = fold(name)
        folded = RENAMED.get(folded, folded)
        here = known.get(folded, [])
        if group and len(here) > 1:
            narrowed = [c for c in here if group_key(group) in c[1]]
            here = narrowed or here
        options.append(here)
        found.append(here[0][0] if len(here) == 1 else None)
    for index, here in enumerate(options):
        if len(here) <= 1:
            continue
        near: Counter[str] = Counter()
        for step in (1, -1, 2, -2, 3, -3):
            other = index + step
            if 0 <= other < len(options) and len(options[other]) == 1:
                near.update(options[other][0][1])
        best = max(here, key=lambda c: sum(near[g] for g in c[1]))
        found[index] = best[0]
    return found


def municipal_rows(frame: pl.DataFrame, name: str, group: str | None = None) -> pl.DataFrame:
    """The municipality rows of a table, each with its group (from a column, or from the total row that closes it)
    and its resolved code; total rows are dropped."""
    names = frame[name].to_list()
    groups: list[str | None]
    if group is not None:
        groups = frame[group].fill_null(strategy="forward").to_list()
    else:
        groups = [None] * len(names)
        current: list[int] = []
        for index, text in enumerate(names):
            if text is None:
                continue
            if SUMMARY.match(fold(text)):
                for row in current:
                    groups[row] = text
                current = []
            else:
                current.append(index)
    keep = [i for i, text in enumerate(names) if text is not None and not SUMMARY.match(fold(text))]
    rows = frame[keep].with_columns(_group=pl.Series([groups[i] for i in keep], dtype=pl.String))
    codes = resolve([(names[i], groups[i]) for i in keep])
    missing = [names[i] for i, code in zip(keep, codes, strict=True) if code is None]
    if missing:
        raise ValueError(f"{len(missing)} municipalities without a code: {missing[:10]}")
    return rows.with_columns(_obec=pl.Series(codes, dtype=pl.String))


def total_row(frame: pl.DataFrame, name: str) -> dict[str, str | None]:
    """The national total row ("Spolu za SR")."""
    for row in frame.iter_rows(named=True):
        text = fold(row[name])
        if text in ("spolu za sr", "sr", "spolu", "s p o l u", "slovenska republika"):
            return row
    raise LookupError(f"no national total in {name}")


def wide_options(frame: pl.DataFrame, pattern: str) -> dict[str, str]:
    """Option columns in a wide table: {column: label}, where the label is the column name before the pattern."""
    out = {}
    for name in frame.columns:
        found = re.match(rf"^(.*?)\s*-?\s*{pattern}", name, re.IGNORECASE)
        if found and found.group(1).strip():
            out[name] = clean(found.group(1)).rstrip(" -")
    return out


def obec_units(rows: pl.DataFrame, day: date) -> tuple[pl.DataFrame, pl.Expr]:
    unit = pl.concat_str(pl.lit(f"{day}:"), pl.col("_obec"))
    units = rows.select(
        unit=unit,
        scheme=pl.lit(OBEC),
        obec=pl.col("_obec"),
        okres=pl.lit(None, pl.String),
        kraj=pl.lit(None, pl.String),
    )
    return units, unit


def okres_code(name: str) -> str:
    folded = group_key(name)
    for code, (text, _) in places().okresy.items():
        if fold(text) == folded:
            return code
    raise LookupError(f"no district named {name}")


def okres_rows(frame: pl.DataFrame, name: str) -> pl.DataFrame:
    keep = frame.filter(~pl.col(name).map_elements(lambda s: bool(SUMMARY.match(fold(s))), pl.Boolean))
    return keep.with_columns(_okres=pl.col(name).map_elements(okres_code, pl.String))


def okres_units(rows: pl.DataFrame, day: date) -> tuple[pl.DataFrame, pl.Expr]:
    unit = pl.concat_str(pl.lit(f"{day}:okres:"), pl.col("_okres"))
    units = rows.select(
        unit=unit,
        scheme=pl.lit(OKRES),
        obec=pl.lit(None, pl.String),
        okres=pl.col("_okres"),
        kraj=pl.lit(None, pl.String),
    )
    return units, unit


def by_totals(columns: dict[str, int], national: dict[str, int], what: str) -> dict[str, str]:
    """Pair each option column with the national option that has the same vote total (the office's municipal tables
    print abbreviations, its national tables full names)."""
    out: dict[str, str] = {}
    free = dict(national)
    for column, total in sorted(columns.items(), key=lambda item: -item[1]):
        match = [k for k, v in free.items() if v == total]
        if len(match) != 1:
            raise ValueError(f"{what}: {column} ({total:,}) matches {match} among the national totals")
        out[column] = match[0]
        del free[match[0]]
    return out


def column_sums(frame: pl.DataFrame, columns: Iterable[str]) -> dict[str, int]:
    return {name: int(frame.select(ints(frame, name)).sum().item() or 0) for name in columns}


def title_name(text: str) -> str:
    """Candidate names: the 2014 tables print surnames in capitals."""
    return " ".join(word.capitalize() if word.isupper() and len(word) > 1 else word for word in clean(text).split())


NRSR_OLD = {
    2002: ("nrsr/2002/NRSR_2002_tab10.csv", "Obec", None),
    2006: ("nrsr/2006/NRSR_2006_tab10.csv", "Okres, obec", None),
    2010: ("nrsr/2010/NRSR_2010_tab06.csv", "Obec, obvod", "Obvod"),
    2012: ("nrsr/2012/NRSR_2012_tab06.csv", "Obec, obvod", "Obvod"),
}


def nrsr_municipal(year: int) -> Day:
    """2002-2012: valid votes and party votes by municipality, no roll or turnout below the district."""
    start, _ = NRSR_DAYS[year]
    d = Day(str(start), f"The {year} National Council election by municipality", [])
    d.method = (
        " The office's municipal table for this election gives each municipality's valid votes and its votes for each "
        "party, but not its roll or turnout, and names each municipality without its code; the code is resolved from "
        "the name within its district."
    )
    d.elections.append(nrsr_election(year))
    c = nrsr_contest()
    path, name, group = NRSR_OLD[year]
    d.paths.append(path)
    rows = municipal_rows(table(path), name, group)
    units, u = obec_units(rows, start)
    d.units.append(units)
    c.facts.append(accounting(rows, u, {VALID: [col(rows, "pocet platnych hlasov spolu")]}))
    national: list[tuple[str, str, int, int | None]] = []
    if year == 2002:
        listed, seated = table("nrsr/2002/NRSR_2002_tab03.csv"), table("nrsr/2002/NRSR_2002_tab04.csv")
        d.paths += ["nrsr/2002/NRSR_2002_tab03.csv", "nrsr/2002/NRSR_2002_tab04.csv"]
        seats = {
            r[col(seated, "cislo")]: number_or_zero(r[col(seated, "spolu")])
            for r in seated.iter_rows(named=True)
            if r[col(seated, "cislo")]
        }
        for r in listed.iter_rows(named=True):
            n = r[col(listed, "cislo")]
            national.append(
                (n, r[col(listed, "nazov")], number_or_zero(r[col(listed, "pocet platnych")]), seats.get(n, 0))
            )
    else:
        file = {2006: "tab05", 2010: "tab09", 2012: "tab09"}[year]
        listed = table(f"nrsr/{year}/NRSR_{year}_{file}.csv")
        d.paths.append(f"nrsr/{year}/NRSR_{year}_{file}.csv")
        for position, r in enumerate(listed.iter_rows(named=True), start=1):
            label = r[col(listed, "nazov")]
            national.append(
                (
                    str(position),
                    label,
                    number_or_zero(r[col(listed, "pocet platnych")]),
                    number(r[col(listed, "mandat")]),
                )
            )
    options = wide_options(rows, "platn[ée] hlasy po[čc]et")
    pairs = by_totals(column_sums(rows, options), {k: v for k, _, v, _ in national}, f"nrsr {year}")
    for k, label, total, seats in national:
        c.options.append(
            Option(
                k,
                clean(label),
                "list",
                position=int(k),
                party=clean(label),
                outcome="won" if seats else "lost",
                seats=seats or None,
            )
        )
        c.declared[(k, VOTES)] = total
    c.declared[(None, VALID)] = sum(total for _, _, total, _ in national)
    c.facts.append(wide_votes(rows, u, pairs))
    d.contests.append(c)
    return d


def nrsr_1994() -> Day:
    start, _ = NRSR_DAYS[1994]
    d = Day(str(start), "The 1994 National Council election by municipality", [])
    d.elections.append(nrsr_election(1994))
    c = nrsr_contest()
    d.method = (
        " In 1994 the National Council was elected in four constituencies (Bratislava and the West, Central, and East "
        "Slovak regions) with a national threshold; the office's open data gives the results by municipality without "
        "the seats each list won, so the contest is stored nationally without seats."
    )
    path = "nrsr/1994/NRSR_1994_tab05.csv"
    d.paths += [path, "nrsr/1994/NRSR_1994_tab02.csv", "nrsr/1994/NRSR_1994_tab03.csv"]
    data = table(path).filter(pl.col("Kód obce").str.contains(r"^\d+$"))
    data = data.with_columns(_obec=pl.col("Kód obce"))
    first = data.unique("_obec", keep="first", maintain_order=True)
    units, u = obec_units(first, start)
    d.units.append(units)
    c.facts.append(
        accounting(
            first,
            u,
            {
                REGISTERED: [col(first, "zapisanych")],
                VOTERS: [col(first, "vydanych")],
                CAST: [col(first, "odovzdanych")],
                VALID: [col(first, "platne hlasy pre vsetky")],
            },
        )
    )
    party = col(data, "politicky subjekt")
    abbreviations = data.group_by(party).agg(ints(data, col(data, "pocet platnych hlasov")).sum().alias("v"))
    regions = table("nrsr/1994/NRSR_1994_tab03.csv")
    full = regions.group_by(col(regions, "nazov")).agg(
        ints(regions, col(regions, "pocet platnych hlasov", exclude=["spolu"])).sum().alias("v")
    )
    national = {clean(k): int(v) for k, v in full.iter_rows()}
    pairs = by_totals({clean(k): int(v) for k, v in abbreviations.iter_rows()}, national, "nrsr 1994")
    for position, (label, total) in enumerate(sorted(national.items(), key=lambda item: -item[1]), start=1):
        short = next(k for k, v in pairs.items() if v == label)
        c.options.append(Option(fold(label), label, "list", position=None, party=label, english=short))
        c.declared[(fold(label), VOTES)] = total
    option = pl.col(party).map_elements(lambda s: fold(pairs[clean(s)]), pl.String)
    c.facts.append(votes(data, u, option, col(data, "pocet platnych hlasov")))
    turnout = table("nrsr/1994/NRSR_1994_tab02.csv")
    for measure, word in (
        (REGISTERED, "zoznamu"),
        (VOTERS, "vydane"),
        (CAST, "odovzdanych"),
        (VALID, "platnych hlasov spolu"),
    ):
        c.declared[(None, measure)] = int(
            turnout.select(ints(turnout, col(turnout, word, exclude=["podiel"]))).sum().item()
        )
    d.contests.append(c)
    return d


def nrsr_1998() -> Day:
    start, _ = NRSR_DAYS[1998]
    d = Day(str(start), "The 1998 National Council election by district", [])
    d.method = " The office's open data gives the 1998 results by district (okres), its finest unit for this election."
    d.elections.append(nrsr_election(1998))
    c = nrsr_contest()
    path, national_path = "nrsr/1998/NRSR_1998_tab03.csv", "nrsr/1998/NRSR_1998_tab02.csv"
    d.paths += [path, national_path]
    data = table(path).with_columns(_okres=pl.col("Kód okresu"))
    first = data.unique("_okres", keep="first", maintain_order=True)
    units, u = okres_units(first, start)
    d.units.append(units)
    c.facts.append(accounting(first, u, {VALID: [col(first, "platne hlasy spolu")]}))
    national = table(national_path)
    for position, r in enumerate(national.iter_rows(named=True), start=1):
        label = clean(r[col(national, "nazov")])
        seats = number(r[col(national, "mandat")])
        c.options.append(
            Option(
                fold(label),
                label,
                "list",
                position=position,
                party=label,
                outcome="won" if seats else "lost",
                seats=seats or None,
            )
        )
        c.declared[(fold(label), VOTES)] = number_or_zero(r[col(national, "pocet platnych")])
    c.declared[(None, VALID)] = sum(v for (k, m), v in c.declared.items() if m == VOTES)
    option = pl.col(col(data, "nazov politickeho")).map_elements(fold, pl.String)
    c.facts.append(votes(data, u, option, col(data, "pocet platnych hlasov")))
    d.contests.append(c)
    return d


def ep_municipal(year: int) -> Day:
    day = EP_DAYS[year]
    d = Day(str(day), f"The {year} European Parliament election in Slovakia by municipality", [])
    d.elections.append(ep_election(year))
    c = ep_contest(year)
    d.method = " The office's open data gives this election's results by municipality, without the seats won."
    if year == 2004:
        path, parties_path, total_path = (
            "eup/2004/EP_2004_Politicke_strany_obce.csv",
            "eup/2004/koal04.csv",
            "eup/2004/EUP_2004_tab01.csv",
        )
        d.paths += [path, parties_path, total_path]
        rows = table(path).with_columns(_obec=pl.col("Kód obce"))
        turnout = rows
        parties = table(parties_path)
        abbreviation = {r[col(parties, "skratka")]: r for r in parties.iter_rows(named=True)}
        start = rows.columns.index(col(rows, "platnych hlasov")) + 1
        pairs = {name: abbreviation[name][col(parties, "cislo")] for name in rows.columns[start:] if name != "_obec"}
        for r in parties.iter_rows(named=True):
            number_ = r[col(parties, "cislo")]
            c.options.append(
                Option(
                    number_,
                    clean(r[col(parties, "nazov")]),
                    "list",
                    position=int(number_),
                    party=clean(r[col(parties, "nazov")]),
                    english=r[col(parties, "skratka")],
                )
            )
        totals = table(total_path)
        total = total_row(totals, "Obvod")
        for measure, word in (
            (REGISTERED, "zapisanych"),
            (VOTERS, "vydane"),
            (CAST, "odovzdanych"),
            (VALID, "platnych hlasov spolu"),
        ):
            c.declared[(None, measure)] = number_or_zero(total[col(totals, word, exclude=["podiel"])])
    else:
        path, turnout_path = {
            2009: ("eup/2009/EUP_2009_tab10.csv", "eup/2009/EUP_2009_tab14.csv"),
            2014: ("eup/2014/EUP_2014_tab05.csv", "eup/2014/EUP_2014_tab09.csv"),
        }[year]
        d.paths += [path, turnout_path]
        rows = municipal_rows(table(path), "Obec", "Obvod")
        turnout = municipal_rows(table(turnout_path), "Obec", "Obvod")
        if year == 2009:
            options = wide_options(rows, "platn[ée] hlasy po[čc]et")
            totals = table("eup/2009/EUP_2009_tab02.csv")
            d.paths.append("eup/2009/EUP_2009_tab02.csv")
            total = total_row(totals, "Obvod")
            for measure, word in (
                (REGISTERED, "zapisanych"),
                (VOTERS, "vydane"),
                (CAST, "odovzdanych"),
                (VALID, "platnych hlasov spolu"),
            ):
                c.declared[(None, measure)] = number_or_zero(total[col(totals, word, exclude=["podiel"])])
            for position, (column, label) in enumerate(options.items(), start=1):
                c.options.append(Option(label, label, "list", position=position, party=label))
            pairs = {column: label for column, label in options.items()}
        else:
            options = wide_options(rows, "po[čc]et platn[ýy]ch hlasov")
            national = table("eup/2014/EUP_2014_tab08.csv")
            d.paths.append("eup/2014/EUP_2014_tab08.csv")
            totals = {
                fold(r[col(national, "politicky")]): (
                    r[col(national, "politicky")],
                    number_or_zero(r[col(national, "pocet")]),
                )
                for r in national.iter_rows(named=True)
            }
            pairs = {}
            for position, (column, label) in enumerate(options.items(), start=1):
                full = totals[fold(label)][0] if fold(label) in totals else label
                c.options.append(Option(fold(full), clean(full), "list", position=position, party=clean(full)))
                pairs[column] = fold(full)
                if fold(label) in totals:
                    c.declared[(fold(full), VOTES)] = totals[fold(label)][1]
            total = total_row(turnout, "Obvod") if False else None
            summary = total_row(table(turnout_path), "Obvod")
            for measure, word in (
                (REGISTERED, "zapisanych"),
                (VOTERS, "vyda"),
                (CAST, "odovzdanych"),
                (VALID, "platnych hlasov"),
            ):
                c.declared[(None, measure)] = number_or_zero(
                    summary[col(table(turnout_path), word, exclude=["podiel"])]
                )
    units, u = obec_units(turnout, day)
    d.units.append(units)
    c.facts.append(
        accounting(
            turnout,
            u,
            {
                REGISTERED: [col(turnout, "zapisanych")],
                VOTERS: [
                    col(turnout, "vydan", exclude=["podiel"])
                    if has(turnout, "vydan", exclude=["podiel"])
                    else col(turnout, "vyda")
                ],
                CAST: [col(turnout, "odovzdanych", exclude=["podiel"])],
                VALID: [col(turnout, "platnych hlasov", exclude=["podiel"])],
            },
        )
    )
    if year != 2004:
        _, rows_unit = obec_units(rows, day)
        c.facts.append(wide_votes(rows, rows_unit, pairs))
    else:
        c.facts.append(wide_votes(rows, u, pairs))
    d.contests.append(c)
    return d


PREZ_OLD: dict[int, tuple[tuple[str, str | None, str, str], ...]] = {
    # (results by unit, its turnout table or None when the same table holds both, national candidates, unit level)
    2004: (
        (
            "pre/2004/1.kolo/PRE_2004_1kolo_tab04.csv",
            "pre/2004/1.kolo/PRE_2004_1kolo_tab02.csv",
            "pre/2004/1.kolo/PRE_2004_1kolo_tab07.csv",
            "okres",
        ),
        ("pre/2004/2.kolo/PRE_2004_2kolo_tab13.csv", None, "pre/2004/2.kolo/PRE_2004_2kolo_tab14.csv", "obec"),
    ),
    2009: (
        (
            "pre/2009/1.kolo/PRE_2009_1kolo_tab07.csv",
            "pre/2009/1.kolo/PRE_2009_1kolo_tab03.csv",
            "pre/2009/1.kolo/PRE_2009_1kolo_tab08.csv",
            "obec",
        ),
        ("pre/2009/2.kolo/PRE_2009_2kolo_tab12.csv", None, "pre/2009/2.kolo/PRE_2009_2kolo_tab13.csv", "obec"),
    ),
    2014: (
        (
            "pre/2014/1.kolo/PRE_2014_1kolo_tab06.csv",
            "pre/2014/1.kolo/PRE_2014_1kolo_tab03.csv",
            "pre/2014/1.kolo/PRE_2014_1kolo_tab04.csv",
            "obec",
        ),
        (
            "pre/2014/2.kolo/PRE_2014_2kolo_tab14.csv",
            "pre/2014/2.kolo/PRE_2014_2kolo_tab11.csv",
            "pre/2014/2.kolo/PRE_2014_2kolo_tab12.csv",
            "obec",
        ),
    ),
}
CANDIDATE_VOTES = r"(platn[ée] hlasy po[čc]et|po[čc]et platn[ýy]ch hlasov|po[čc]et hlasov$|po[čc]et$)"


def _units_rows(frame: pl.DataFrame, level: str) -> pl.DataFrame:
    if level == "okres":
        return okres_rows(frame, frame.columns[0])
    group = "Obvod" if "Obvod" in frame.columns else None
    name = next(c for c in frame.columns if fold(c).startswith("obec"))
    return municipal_rows(frame, name, group)


def prez_municipal(year: int) -> Day:
    d = Day(str(PREZ_DAYS[year][0]), f"The {year} presidential election, both rounds, by municipality", [])
    d.elections += prez_elections(year)
    if year == 2004:
        d.method = (
            " The office's open data gives the first round of 2004 by district (okres) and the second by municipality."
        )
    else:
        d.method = " The office's open data gives this election by municipality, naming each without its code; the code is resolved from the name within its district."
    for round_number, (path, turnout_path, national_path, level) in enumerate(PREZ_OLD[year], start=1):
        day = PREZ_DAYS[year][round_number - 1]
        c = prez_contest(year, round_number)
        d.paths += [p for p in (path, turnout_path, national_path) if p]
        rows = _units_rows(table(path), level)
        turnout = rows if turnout_path is None else _units_rows(table(turnout_path), level)
        if level == "okres":
            units, u = okres_units(turnout, day)
            _, vu = okres_units(rows, day)
        else:
            units, u = obec_units(turnout, day)
            _, vu = obec_units(rows, day)
        d.units.append(units)
        c.facts.append(
            accounting(
                turnout,
                u,
                {
                    REGISTERED: [col(turnout, "zapisanych")],
                    VOTERS: [
                        col(turnout, "vydan", exclude=["podiel"])
                        if has(turnout, "vydan", exclude=["podiel"])
                        else col(turnout, "vyda")
                    ],
                    CAST: [col(turnout, "odovzdanych obalok", exclude=["podiel"])],
                    VALID: [
                        col(turnout, "platnych hlasov", exclude=["podiel", "-"])
                        if has(turnout, "platnych hlasov", exclude=["podiel", "-"])
                        else col(turnout, "pocet hlasov pre vsetkych")
                    ],
                },
            )
        )
        options = {
            column: title_name(label)
            for column, label in wide_options(rows, CANDIDATE_VOTES).items()
            if not fold(label).startswith("pocet")
        }
        for position, (column, label) in enumerate(options.items(), start=1):
            c.options.append(Option(fold(label), label, "candidate", position=position, person=label))
        c.facts.append(wide_votes(rows, vu, {column: fold(label) for column, label in options.items()}))
        national = table(national_path)
        name = national.columns[0]
        figures = [c_ for c_ in national.columns if "počet" in c_.lower() and "podiel" not in c_.lower()]
        if fold(name) in ("meno a priezvisko",):
            for r in national.iter_rows(named=True):
                if fold(title_name(r[name] or "")) in {o.key for o in c.options}:
                    c.declared[(fold(title_name(r[name])), VOTES)] = number_or_zero(r[figures[0]])
        else:
            total = total_row(national, name)
            for column, label in options.items():
                if column in national.columns:
                    c.declared[(fold(label), VOTES)] = number_or_zero(total[column])
        presidential_outcomes(c)
        d.contests.append(c)
    return d


# The 1999 presidential election, the first by direct vote, from the office's archived pages. The pages that list
# results by municipality are one dynamic page per district that the archive did not keep, so the district (okres) is
# the finest unit it holds.

PREZ_1999 = "https://volby.statistics.sk/prez/prez1999/prezid99"
PREZ_1999_SUMMARY = {1: "results/tab1", 2: "results/tab7"}
PREZ_1999_VOTES = {1: "results/tab3", 2: "results/tab7"}
PREZ_1999_NATIONAL = {1: "results/tab6", 2: "results/tab10"}
PREZ_1999_INFO = ("info/kand99", "info/kand99_2", "info/okr99")
CELL = re.compile(r"(?is)<t[dh]\b[^>]*>(.*?)(?=<t[dh]\b|</tr|</table|\Z)")
ROW = re.compile(r"(?is)<tr\b[^>]*>(.*?)(?=<tr\b|</table|\Z)")
WITHDRAWN = "Michal Kováč"
"""The candidate who withdrew after the ballots were printed; the district tables total his votes as "ineligible
candidates"."""


def page_url(page: str) -> str:
    return f"{PREZ_1999}/{page}.jsp.htm"


def html_rows(page: str) -> list[list[str]]:
    """The cells of every table row on one of the office's 1999 pages (Windows-1250 HTML)."""
    text = raw.cached(SOURCE, page_url(page)).path.read_bytes().decode("cp1250")
    rows = []
    for row in ROW.findall(text):
        cells = [clean(re.sub(r"(?s)<[^>]*>", " ", cell.replace("&nbsp;", " "))) for cell in CELL.findall(row)]
        if any(cells):
            rows.append(cells)
    return rows


def table_after(page: str, header: str, width: int) -> list[list[str]]:
    """The rows under the first row that starts with `header`, up to the national total row if there is one."""
    rows = html_rows(page)
    start = next(i for i, r in enumerate(rows) if fold(r[0]) == header)
    out = []
    for r in rows[start + 1 :]:
        if len(r) < width or not re.fullmatch(r"[\d\s]+", r[1]):
            continue
        out.append(r)
        if fold(r[0]).startswith("spolu"):
            break
    return out


def flat_table(page: str, width: int) -> tuple[list[str], list[list[str]]]:
    """The header cells and district rows of a 1999 page whose markup has no row tags: every cell after "OKRES" in
    one run, the header first, then `width` cells per district and a final national row."""
    cells = [c for row in html_rows(page) for c in row]
    start = next(i for i, c in enumerate(cells) if fold(c) == "okres")
    cells = cells[start:]
    first = next(i for i in range(1, len(cells) - 1) if re.fullmatch(r"\d+", cells[i + 1]) and not cells[i].isdigit())
    header, body = cells[:first], cells[first:]
    rows = []
    for i in range(0, len(body) - width + 1, width):
        rows.append(body[i : i + width])
        if fold(body[i]).startswith("spolu"):
            break
    return header, rows


def prez_1999() -> Day:
    first, second = PREZ_DAYS[1999]
    d = Day(str(first), "The 1999 presidential election, both rounds, by district", [])
    d.method = (
        " The office's archived pages give the 1999 election by district (okres), the finest unit they hold; the pages "
        "that list each district's municipalities were not archived."
    )
    d.elections += prez_elections(1999)
    pages = [*PREZ_1999_SUMMARY.values(), *PREZ_1999_VOTES.values(), *PREZ_1999_NATIONAL.values(), *PREZ_1999_INFO]
    d.paths += [page_url(page) for page in dict.fromkeys(pages)]
    for round_number, day in enumerate((first, second), start=1):
        c = prez_contest(1999, round_number)
        national = {
            fold(r[0]): (clean(r[0]), number_or_zero(r[1]))
            for r in table_after(PREZ_1999_NATIONAL[round_number], "meno a priezvisko", 2)
            if not fold(r[0]).startswith("spolu")
        }
        if round_number == 1:
            _, rows = flat_table(PREZ_1999_SUMMARY[1], 6)
            header, tally = flat_table(PREZ_1999_VOTES[1], 12)
            heads = header[2:]
            surnames = {key_.split()[-1]: key_ for key_ in national}
            order = [
                fold(WITHDRAWN) if fold(h).startswith("nevolitel") else surnames[fold(h).split()[-1]] for h in heads
            ]
            votes_of = {fold(r[0]): [number(x) for x in r[2:]] for r in tally}
        else:
            header, rows = flat_table(PREZ_1999_SUMMARY[2], 10)
            heads = header[6:8]
            order = [fold(h) for h in heads]
            votes_of = {fold(r[0]): [number(r[6]), number(r[8])] for r in rows}
        for position, key_ in enumerate(order, start=1):
            label = national[key_][0]
            c.options.append(Option(key_, label, "candidate", position=position, person=label))
        c.declared.update({(key_, VOTES): value for key_, (_, value) in national.items()})
        total = next(r for r in rows if fold(r[0]).startswith("spolu"))
        body = [r for r in rows if not fold(r[0]).startswith("spolu")]
        for measure, figure in ((REGISTERED, total[2]), (VOTERS, total[3]), (CAST, total[4]), (VALID, total[5])):
            c.declared[(None, measure)] = number_or_zero(figure)
        frame = pl.DataFrame(
            {
                "okres": [r[0] for r in body],
                "reg": [r[2] for r in body],
                "issued": [r[3] for r in body],
                "cast": [r[4] for r in body],
                "valid": [r[5] for r in body],
            }
        ).with_columns(_okres=pl.col("okres").map_elements(okres_code, pl.String))
        units, u = okres_units(frame, day)
        d.units.append(units)
        c.facts.append(
            accounting(frame, u, {REGISTERED: ["reg"], VOTERS: ["issued"], CAST: ["cast"], VALID: ["valid"]})
        )
        c.facts.append(
            pl.DataFrame(
                [
                    {"unit": f"{day}:okres:{okres_code(r[0])}", "option": key_, "measure": VOTES, "value": value}
                    for r in body
                    for key_, value in zip(order, votes_of[fold(r[0])], strict=True)
                    if value is not None
                ],
                schema=FACT_SCHEMA,
            )
        )
        presidential_outcomes(c)
        d.contests.append(c)
    return d


# The National Council elections of 1990 and 1992, before independence, from the office's archived pages. The pages
# print the votes of each party by district (okres) with a total for each of the four electoral regions (volebné
# kraje) and the republic, and one chart of the seats each party won in the republic.

SNR_DAYS = {1990: (date(1990, 6, 8), date(1990, 6, 9)), 1992: (date(1992, 6, 5), date(1992, 6, 6))}
SNR_SITE = {1990: f"{SITE}/nrsr/snr1990/volby90_s", 1992: f"{SITE}/nrsr/snr1992/volby92s"}
SNR_SEATS = {
    1990: {"VPN": 48, "KDH": 31, "KSČ": 22, "SNS": 22, "ESWMK": 14, "DS": 7, "SZ": 6},
    1992: {"HZDS": 74, "SDĽ": 29, "KDH": 18, "SNS": 15, "MKM-EGY": 14},
}
"""The seats printed on the pie chart of each year's mandates page, read from the chart's labels and legend."""
SNR_THRESHOLD = {1990: 3, 1992: 5}
KRAJ_90, OKRES_90 = "sk.volebny_kraj_1990", "sk.okres_1990"
SNR_FROM, SNR_UNTIL = date(1990, 6, 8), date(1996, 7, 23)
"""The regions and districts of these elections are dated from the first election that printed them until the day before
the 1996 reform replaced them with 8 regions and 79 districts."""
SNR_BODY = key("body", "snr")
SNR_REPUBLIC = "Spolu za SR"
SNR_KRAJ_NAMES = {"Hlavné mesto SR Bratislava": "Hl. m. SR Bratislava"}
SNR_FIXES = {"Koš ice-mesto": "Košice-mesto"}
"""The office's tables misprint one district name and the list of regions prints Bratislava in short."""


def snr_page(year: int, page: str) -> str:
    return f"{SNR_SITE[year]}/{page}{year % 100}.htm"


def snr_image(year: int) -> str:
    return f"{SNR_SITE[year]}/pmp901.jpg" if year == 1990 else f"{SNR_SITE[year]}/pmp92.jpg"


def snr_geo_paths() -> set[str]:
    return {snr_page(year, page) for year in SNR_DAYS for page in ("zvk", "pph")}


def snr_rows(path: str) -> list[list[str]]:
    text = raw.cached(SOURCE, path).path.read_bytes().decode("cp1250")
    rows = []
    for row in ROW.findall(text):
        cells = [clean(html.unescape(re.sub(r"(?s)<[^>]*>", " ", cell))) for cell in CELL.findall(row)]
        if any(cells):
            rows.append(cells)
    return rows


@dataclass
class SnrRow:
    name: str
    registered: int
    issued: int
    valid: int
    votes: list[int]

    def totals(self) -> list[int]:
        return [self.registered, self.issued, self.valid, *self.votes]


@dataclass
class SnrTable:
    parties: list[tuple[int, str, str]]
    """Each party's ballot number, abbreviation, and full name."""
    groups: dict[str, list[SnrRow]]
    """Each region's districts, in the office's order, by the region's name."""
    kraje: dict[str, SnrRow]
    republic: SnrRow


def snr_party_list(year: int) -> list[tuple[int, str, str]]:
    found = []
    for r in snr_rows(snr_page(year, "zps")):
        if len(r) == 3 and r[0].isdigit():
            found.append((int(r[0]), "".join(r[1].split()), r[2]))
    return found


def snr_kraje(year: int) -> list[tuple[str, int]]:
    """The regions the office lists, with their polling-district counts, the republic's total last."""
    return [(r[0], number_or_zero(r[1])) for r in snr_rows(snr_page(year, "zvk")) if len(r) == 2 and r[1][:1].isdigit()]


@functools.cache
def snr_table(year: int) -> SnrTable:
    parties = snr_party_list(year)
    rows = snr_rows(snr_page(year, "pph"))
    head = next(r for r in rows if r and fold(r[0]) == "vsetky politicke strany")
    abbreviations = ["".join(cell.split()) for cell in head[1:]]
    if abbreviations != [abbr for _, abbr, _ in parties]:
        raise ValueError(f"snr {year}: the vote table's columns {abbreviations} are not the list of parties")
    width = 5 + 2 * len(parties)
    groups: dict[str, list[SnrRow]] = {}
    kraje: dict[str, SnrRow] = {}
    republic: SnrRow | None = None
    pending: list[SnrRow] = []
    for r in rows:
        if len(r) != width or not re.fullmatch(r"[\d ]+", r[1]):
            continue
        row = SnrRow(
            SNR_FIXES.get(r[0], r[0]),
            number_or_zero(r[1]),
            number_or_zero(r[2]),
            number_or_zero(r[4]),
            [number_or_zero(r[5 + 2 * i]) for i in range(len(parties))],
        )
        if row.name == SNR_REPUBLIC:
            republic = row
        elif row.name == "Hlavné mesto SR Bratislava" or row.name.endswith(" kraj"):
            name = SNR_KRAJ_NAMES.get(row.name, row.name)
            kraje[name] = row
            groups[name] = pending
            pending = []
        else:
            pending.append(row)
    if republic is None or pending:
        raise ValueError(f"snr {year}: the vote table has no republic total or ends inside a region")
    table_ = SnrTable(parties, groups, kraje, republic)
    for r in [*(r for rows_ in groups.values() for r in rows_), *kraje.values(), republic]:
        if sum(r.votes) != r.valid:
            raise ValueError(f"snr {year}: {r.name} lists {sum(r.votes)} votes for parties but {r.valid} valid votes")
    for name, rows_ in groups.items():
        for i, total in enumerate(kraje[name].totals()):
            if sum(r.totals()[i] for r in rows_) != total:
                raise ValueError(f"snr {year}: the districts of {name} do not add up to its total (column {i})")
    for i, total in enumerate(republic.totals()):
        if sum(r.totals()[i] for r in kraje.values()) != total:
            raise ValueError(f"snr {year}: the regions do not add up to the republic (column {i})")
    listed = [name for name, _ in snr_kraje(year)][:-1]
    if listed != list(kraje):
        raise ValueError(f"snr {year}: the regions {list(kraje)} are not the office's list {listed}")
    return table_


def snr_slug(name: str) -> str:
    return fold(name).replace(" ", "-")


@functools.cache
def snr_places() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """The regions by slug and the districts by slug with their region, the same in both years."""
    kraje: dict[str, str] = {}
    okresy: dict[str, tuple[str, str]] = {}
    for year in SNR_DAYS:
        for kraj, rows in snr_table(year).groups.items():
            kraje[snr_slug(kraj)] = kraj
            for r in rows:
                seen = okresy.setdefault(snr_slug(r.name), (r.name, snr_slug(kraj)))
                if seen != (r.name, snr_slug(kraj)):
                    raise ValueError(f"snr: district {r.name} differs between the elections")
    return kraje, okresy


def snr_election(year: int) -> Election:
    start, end = SNR_DAYS[year]
    return Election(
        "snr",
        "general",
        start,
        end,
        f"{year} Slovak National Council election",
        f"Voľby do Slovenskej národnej rady {year}",
        f"sk/{start}-parliamentary",
    )


def snr(year: int) -> Day:
    start, _ = SNR_DAYS[year]
    t = snr_table(year)
    d = Day(str(start), f"The {year} Slovak National Council election by district", [])
    d.method = (
        f" In {year} the 150 members of the Slovak National Council were elected in four electoral regions (Bratislava "
        "and the West, Central, and East Slovak regions). The office's archived pages print each party's votes by "
        "district (okres), the finest unit it holds, with a total for each region and the republic; the totals match "
        "the districts. The pages give the seats of each party only for the republic, on a chart whose labels this "
        "package reads, so the contest is stored nationally with those seats and without seats per region."
    )
    d.elections.append(snr_election(year))
    d.paths += [snr_page(year, page) for page in ("zvk", "zps", "pph")] + [snr_image(year)]
    c = Contest(
        "snr",
        "snr",
        "list_allocation",
        "Slovenská národná rada",
        "national-council",
        system(f"snr-{year}"),
        magnitude=150,
        body=SNR_BODY,
    )
    seats = SNR_SEATS[year]
    if sum(seats.values()) != 150 or set(seats) - {abbr for _, abbr, _ in t.parties}:
        raise ValueError(f"snr {year}: the chart's seats {seats} are not 150 seats for listed parties")
    option_of = {}
    for number_, abbr, label in t.parties:
        option_of[abbr] = fold(label)
        won = seats.get(abbr)
        c.options.append(
            Option(
                fold(label),
                label,
                "list",
                position=number_,
                party=label,
                outcome="won" if won else "lost",
                seats=won,
                english=abbr,
            )
        )
    for (_, abbr, _), total in zip(t.parties, t.republic.votes, strict=True):
        c.declared[(option_of[abbr], VOTES)] = total
    c.declared[(None, REGISTERED)] = t.republic.registered
    c.declared[(None, VOTERS)] = t.republic.issued
    c.declared[(None, VALID)] = t.republic.valid
    accounting_rows: list[dict[str, object]] = []
    vote_rows: list[dict[str, object]] = []
    units = []
    for rows in t.groups.values():
        for r in rows:
            unit = f"{start}:okres:{snr_slug(r.name)}"
            units.append(
                {
                    "unit": unit,
                    "scheme": OKRES_90,
                    "obec": None,
                    "okres": None,
                    "kraj": None,
                    "area": area(OKRES_90, snr_slug(r.name)),
                    "relation": None,
                }
            )
            for measure, value in ((REGISTERED, r.registered), (VOTERS, r.issued), (VALID, r.valid)):
                accounting_rows.append({"unit": unit, "option": None, "measure": measure, "value": value})
            for (_, abbr, _), value in zip(t.parties, r.votes, strict=True):
                vote_rows.append({"unit": unit, "option": option_of[abbr], "measure": VOTES, "value": value})
    d.units.append(pl.DataFrame(units, schema={**UNIT_SCHEMA, **UNIT_EXTRA}))
    c.facts.append(pl.DataFrame(accounting_rows, schema=FACT_SCHEMA))
    c.facts.append(pl.DataFrame(vote_rows, schema=FACT_SCHEMA))
    d.contests.append(c)
    return d


REF_OLD = {
    2003: ("ref/2003/REF_2003_tab01.csv", "Okres"),
    2004: ("ref/2004/REF_2004_tab01.csv", "Okres"),
}
REF_QUESTIONS = {
    2003: {1: "Súhlasíte s tým, aby sa Slovenská republika stala členským štátom Európskej únie?"},
    2004: {
        1: "Ste za to, aby poslanci Národnej rady Slovenskej republiky prijali ústavný zákon o skrátení volebného "
        "obdobia Národnej rady Slovenskej republiky tak, aby sa voľby do Národnej rady Slovenskej republiky konali do "
        "konca roku 2004?"
    },
}
"""Question texts the 2003 and 2004 open-data tables leave out, from the presidential decisions that called them."""


def ref_municipal(year: int) -> Day:
    start, _ = REF_DAYS[year]
    d = Day(str(start), f"The {year} referendum by municipality", [])
    d.method = " The office's open data gives this referendum's roll, ballots issued, and answers by municipality."
    d.elections.append(ref_election(year))
    path, group = REF_OLD[year]
    d.paths.append(path)
    rows = municipal_rows(table(path), "Obec", group)
    units, u = obec_units(rows, start)
    d.units.append(units)
    c = ref_contest(1, REF_QUESTIONS[year][1])
    c.facts.append(accounting(rows, u, {REGISTERED: [col(rows, "opravnenych")], VOTERS: [col(rows, "vydanych")]}))
    c.facts.append(votes(rows, u, pl.lit("yes"), col(rows, "pocet hlasov ano")))
    c.facts.append(votes(rows, u, pl.lit("no"), col(rows, "pocet hlasov nie")))
    registered = int(rows.select(ints(rows, col(rows, "opravnenych"))).sum().item())
    if year == 2003:
        totals = table("ref/2003/REF_2003_tab02.csv")
        d.paths.append("ref/2003/REF_2003_tab02.csv")
        sums = {
            k: int(totals.select(ints(totals, col(totals, w))).sum().item())
            for k, w in (
                ("r", "opravnenych"),
                ("v", "vydanych"),
                ("yes", "pocet hlasov ano"),
                ("no", "pocet hlasov nie"),
            )
        }
    else:
        totals = table("ref/2004/REF_2004_tab03.csv")
        d.paths.append("ref/2004/REF_2004_tab03.csv")
        row = total_row(totals, "Okres")
        sums = {
            k: number_or_zero(row[col(totals, w)])
            for k, w in (
                ("r", "opravnenych"),
                ("v", "vydanych"),
                ("yes", "pocet hlasov ano"),
                ("no", "pocet hlasov nie"),
            )
        }
    c.declared[(None, REGISTERED)], c.declared[(None, VOTERS)] = sums["r"], sums["v"]
    c.declared[("yes", VOTES)], c.declared[("no", VOTES)] = sums["yes"], sums["no"]
    referendum_outcomes(c, registered)
    d.contests.append(c)
    return d


def ref_precinct(year: int) -> Day:
    """2010 and 2015: several questions on one ballot, by polling district."""
    start, _ = REF_DAYS[year]
    d = Day(str(start), f"The {year} referendum by polling district", [])
    d.elections.append(ref_election(year))
    if year == 2010:
        path = "ref/2010/REF_2010_tab02.csv"
        rows = table(path).with_columns(_obec=pl.col("Kód obce"), _okrsok=pl.col("Číslo okrsku"))
        count = 6
    else:
        path = "ref/2015/REF_2015_tab04.csv"
        rows = municipal_rows(table(path), "Obec", "Obvod").with_columns(_okrsok=pl.col("Okrsok"))
        count = 3
    d.paths.append(path)
    d.units.append(units_of(rows, start, okrsok="_okrsok", obec="_obec"))
    u = unit_expr(start, "_obec", "_okrsok")
    shared = accounting(
        rows,
        u,
        {
            REGISTERED: [col(rows, "zapisani") if has(rows, "zapisani") else col(rows, "opravnenych")],
            VOTERS: [col(rows, "vydane")],
            CAST: [col(rows, "odovzdane")],
            VALID: [col(rows, "platne")],
            INVALID: [col(rows, "neplatne")],
        },
    )
    registered = int(
        rows.select(ints(rows, col(rows, "zapisani") if has(rows, "zapisani") else col(rows, "opravnenych")))
        .sum()
        .item()
    )
    if year == 2010:
        summary = table("ref/2010/REF_2010_tab03.csv")
        total = total_row(summary, "Samosprávny kraj")
    else:
        summary = table("ref/2015/REF_2015_tab01.csv")
        total = total_row(summary, "Obvod")
    d.paths.append("ref/2010/REF_2010_tab03.csv" if year == 2010 else "ref/2015/REF_2015_tab01.csv")
    declared = {
        REGISTERED: number_or_zero(total[col(summary, "opravnenych", exclude=["percento", "ucast"])]),
        VOTERS: number_or_zero(total[col(summary, "vydanych")]),
        VALID: number_or_zero(total[col(summary, "platnych", exclude=["neplatnych"])]),
        INVALID: number_or_zero(total[col(summary, "neplatnych")]),
    }
    for question in range(1, count + 1):
        c = ref_contest(question, None)
        c.declared |= {(None, m): v for m, v in declared.items()}
        c.facts.append(shared)
        yes = next(n for n in rows.columns if re.fullmatch(rf"Otázka (č\. )?{question} ÁNO", n))
        no = next(n for n in rows.columns if re.fullmatch(rf"Otázka (č\. )?{question} NIE", n))
        c.facts.append(votes(rows, u, pl.lit("yes"), yes))
        c.facts.append(votes(rows, u, pl.lit("no"), no))
        referendum_outcomes(c, registered)
        d.contests.append(c)
    return d


# Regional (samosprávne kraje) and municipal elections by polling district: the chair and the council of each region
# since 2017, and the mayor and council of each municipality since 2022.

OSK_DAYS = {
    2001: (date(2001, 12, 1), date(2001, 12, 15)),
    2005: (date(2005, 11, 26), date(2005, 12, 10)),
    2009: (date(2009, 11, 14), date(2009, 11, 28)),
    2013: (date(2013, 11, 9), date(2013, 11, 23)),
    2017: (date(2017, 11, 4), None),
    2022: (date(2022, 10, 29), None),
    2026: (date(2026, 10, 24), None),
}
OSO_DAYS = {
    2002: date(2002, 12, 6),
    2006: date(2006, 12, 2),
    2010: date(2010, 11, 27),
    2014: date(2014, 11, 15),
    2018: date(2018, 11, 10),
    2022: date(2022, 10, 29),
    2026: date(2026, 10, 24),
}
INDEPENDENT = {"neka", "nk neka", "nezavisly kandidat", "nezavisla kandidatka", "nk", "001"}


@dataclass
class Local:
    """One family of local contests on one day, in long form.

    candidates: contest, number, name, party, status (elected, withdrew, or null)
    votes: contest, obec, okrsok, number, value
    units: obec, okrsok, and the accounting columns registered, voters, cast, and valid_<kind> (kind is the contest
    key's first part), each nullable."""

    candidates: pl.DataFrame
    votes: pl.DataFrame
    units: pl.DataFrame | None
    meta: dict[str, Contest]
    bare: frozenset[str] = frozenset()
    """Contests that take no unit totals, because the unit covers more than the contest's electoral district."""


def vuc_obvody() -> dict[str, tuple[str, str]]:
    """Regional electoral districts: code → (name, region)."""
    out: dict[str, tuple[str, str]] = {}
    for path in ("osk/2017/OSK_2017_tab0db.csv", "osk/2026/OSK2026_SK_tab0db.csv"):
        frame = table(path)
        for row in frame.iter_rows(named=True):
            out[row[col(frame, "kod volebneho obvodu")]] = (
                clean(row[col(frame, "volebneho obvodu", exclude=["kod", "pocet"])]),
                row[col(frame, "kod kraja")],
            )
    frame = table("osk/2022/OSK2022_SK_tab0db.csv")
    for row in frame.iter_rows(named=True):
        out.setdefault(
            row[col(frame, "kod volebneho obvodu")],
            (clean(row[col(frame, "nazov volebneho obvodu")]), row[col(frame, "kod kraja")]),
        )
    return out


def town_name(code: str) -> str:
    known = places().obce
    return known[code][0] if code in known else CITIES[code][0]


def status_of(text: object) -> str | None:
    folded = fold(text)
    if folded in ("x",):
        return "withdrew"
    if folded in ("1", "2", "zvoleny", "zvolena"):
        return "elected"
    return None


def local_party(label: object) -> tuple[str | None, bool]:
    text = clean(label)
    if not text or fold(text) in INDEPENDENT:
        return None, True
    return text, False


def mayor_contest(obec: str) -> Contest:
    name = town_name(obec)
    return Contest(
        f"mayor:{obec}",
        "oso",
        "office",
        f"{'Primátor' if obec in CITIES else 'Starosta'}, {name}",
        f"{changeset.slug(name)}-{obec}-mayor",
        system("mayor"),
        district=area(OBEC, obec),
        magnitude=1,
        office=mayor(obec),
    )


def council_contest(obec: str, vo: str | None, seats: int | None) -> Contest:
    """A municipal council's contest in one electoral district, or, where the office prints no districts, in the
    whole municipality (vo is None)."""
    name = town_name(obec)
    return Contest(
        f"council:{obec}:{vo}" if vo else f"council:{obec}",
        "oso",
        "body_seats",
        f"Zastupiteľstvo, {name}, volebný obvod č. {vo}" if vo else f"Zastupiteľstvo, {name}",
        f"{changeset.slug(name)}-{obec}-council-{vo}" if vo else f"{changeset.slug(name)}-{obec}-council",
        system("town-council"),
        district=area(OBEC, obec),
        magnitude=seats,
        body=town_council(obec),
    )


def chair_contest(kraj: str, year: int, round_number: int = 1) -> Contest:
    text = places().kraje[kraj]
    return Contest(
        f"chair:{kraj}:{round_number}",
        f"osk:{round_number}",
        "office",
        f"Predseda, {text}",
        f"{changeset.slug(text)}-chair",
        system("vuc-chair-2017" if year >= 2017 else "vuc-chair-2001"),
        district=area(KRAJ, kraj),
        magnitude=1,
        round=round_number,
        previous=f"chair:{kraj}:1" if round_number == 2 else None,
        office=regional_chair(kraj),
    )


def vuc_contest(obvod: str, seats: int | None) -> Contest:
    name, kraj = vuc_obvody()[obvod]
    text = places().kraje[kraj]
    return Contest(
        f"vuc:{obvod}",
        "osk:1",
        "body_seats",
        f"Zastupiteľstvo, {text}, {name.lower() if name.startswith('Volebný') else name}",
        f"{changeset.slug(text)}-council-{obvod}",
        system("vuc-council"),
        district=area(VUC_OBVOD, obvod),
        magnitude=seats,
        body=regional_council(kraj),
    )


def osk_election(year: int, round_number: int = 1) -> Election:
    first, _ = OSK_DAYS[year]
    day = first if round_number == 1 else OSK_DAYS[year][1] or first
    suffix = "" if round_number == 1 else ", second round"
    return Election(
        f"osk:{round_number}",
        "local" if round_number == 1 else "runoff",
        day,
        day,
        f"{year} Slovak regional elections{suffix}",
        f"Voľby do orgánov samosprávnych krajov {year}" + ("" if round_number == 1 else ", 2. kolo"),
        f"sk/{day}-regional" + ("" if round_number == 1 else "-runoff"),
    )


def oso_election(year: int) -> Election:
    day = OSO_DAYS[year]
    return Election(
        "oso",
        "local",
        day,
        day,
        f"{year} Slovak municipal elections",
        f"Voľby do orgánov samosprávy obcí {year}",
        f"sk/{day}-municipal",
    )


def local_unit(day: date) -> pl.Expr:
    """A polling district, a city council district (okrsok "vo<n>"), or a whole municipality (no okrsok)."""
    return (
        pl.when(pl.col("okrsok").is_null())
        .then(pl.concat_str(pl.lit(f"{day}:"), "obec"))
        .otherwise(pl.concat_str(pl.lit(f"{day}:"), "obec", pl.lit(":"), "okrsok"))
    )


def add_local(d: Day, day: date, local: Local) -> None:
    """Contests, options, outcomes, and facts from a family's long tables."""
    votes_ = local.votes.with_columns(unit=local_unit(day))
    d.units.append(
        votes_.select(
            "unit",
            scheme=pl.when(pl.col("okrsok").is_null())
            .then(pl.lit(OBEC))
            .when(pl.col("okrsok").str.starts_with("vo"))
            .then(pl.lit(TOWN_DISTRICT))
            .otherwise(pl.lit(OKRSOK)),
            obec="obec",
            okres=pl.lit(None, pl.String),
            kraj=pl.lit(None, pl.String),
        ).unique("unit")
    )
    by_contest = votes_.partition_by("contest", as_dict=True)
    accounting_parts: dict[str, pl.DataFrame] = {}
    if local.units is not None:
        units = local.units.with_columns(unit=local_unit(day))
        pairs = votes_.select("contest", "unit").unique().filter(~pl.col("contest").is_in(list(local.bare)))
        kinds = pairs.with_columns(kind=pl.col("contest").str.split(":").list.first())
        long_parts = []
        for measure, name in ((REGISTERED, "registered"), (VOTERS, "voters"), (CAST, "cast")):
            if name in units.columns:
                long_parts.append(
                    units.select(
                        "unit",
                        kind=pl.lit(None, pl.String),
                        measure=pl.lit(measure, pl.Int16),
                        value=pl.col(name).cast(pl.Int64),
                    )
                )
        for name in units.columns:
            if name.startswith("valid_"):
                long_parts.append(
                    units.select(
                        "unit",
                        kind=pl.lit(name.removeprefix("valid_")),
                        measure=pl.lit(VALID, pl.Int16),
                        value=pl.col(name).cast(pl.Int64),
                    )
                )
        long = pl.concat(long_parts).filter(pl.col("value").is_not_null())
        shared = kinds.join(long.filter(pl.col("kind").is_null()).drop("kind"), on="unit")
        own = kinds.join(long.filter(pl.col("kind").is_not_null()), on=["unit", "kind"])
        joined = pl.concat(
            [shared.select("contest", "unit", "measure", "value"), own.select("contest", "unit", "measure", "value")]
        )
        accounting_parts = {k[0]: v for k, v in joined.partition_by("contest", as_dict=True).items()}
    candidates = local.candidates.partition_by("contest", as_dict=True)
    for (contest_key,), frame in sorted(by_contest.items()):
        c = local.meta[contest_key]
        for row in candidates[(contest_key,)].sort(pl.col("number").cast(pl.Int32)).iter_rows(named=True):
            label, independent = local_party(row["party"])
            name = clean(row["name"])
            c.options.append(
                Option(
                    row["number"],
                    name,
                    "candidate",
                    position=int(row["number"]),
                    party=label,
                    person=name,
                    independent=independent,
                    outcome=row["status"],
                )
            )
        c.facts.append(frame.select("unit", option="number", measure=pl.lit(VOTES, pl.Int16), value="value"))
        if contest_key in accounting_parts:
            c.facts.append(
                accounting_parts[contest_key].select(
                    "unit", option=pl.lit(None, pl.String), measure="measure", value="value"
                )
            )
        if c.magnitude is None:
            c.magnitude = sum(1 for o in c.options if o.outcome == "elected") or None
        winners = [o for o in c.options if o.outcome == "elected"]
        if c.magnitude and len(winners) > c.magnitude:
            # Namesakes on one ballot both match the office's list of the elected: the one with more votes won.
            totals = dict(frame.group_by("number").agg(pl.col("value").sum()).iter_rows())
            for o in sorted(winners, key=lambda o: -totals.get(o.key, 0))[c.magnitude :]:
                o.outcome = None
        for o in c.options:
            if o.outcome is None:
                o.outcome = "lost"
        d.contests.append(c)


def _names(frame: pl.DataFrame) -> pl.Expr:
    if has(frame, "meno a priezvisko"):
        return pl.col(col(frame, "meno a priezvisko"))
    first = col(frame, "meno", exclude=["priezvisko"]) if "MENO" not in frame.columns else "MENO"
    last = col(frame, "priezvisko") if "PRIEZVISKO" not in frame.columns else "PRIEZVISKO"
    return pl.concat_str(pl.col(first), pl.lit(" "), pl.col(last))


def oso_modern(year: int) -> Day:
    day = OSO_DAYS[year]
    d = Day(str(day), f"The {year} municipal elections (mayors and councils) by polling district", [])
    d.method = (
        " In municipal elections each voter casts one ballot for the mayor and one for the council of the electoral "
        "district (volebný obvod) the polling district belongs to, with as many votes as the district elects; valid "
        "ballots are counted per ballot. Bratislava and Košice also elect a city mayor and council over their "
        "boroughs; those city contests are stored by city council district, because the office's polling-district "
        "tables for them repeat district numbers across boroughs."
    )
    d.elections.append(oso_election(year))
    t = lambda n: f"oso/{year}/OSO{year}_SK_{n}.csv"
    files = {
        "units": t("tab02bf") if year == 2022 else t("tab02f"),
        "units_x": t("tab02bex") if year == 2022 else t("tab02ex"),
        "mayor": t("tab05f"),
        "mayor_x": t("tab05ex"),
        "council": t("tab08f"),
        "council_x": t("tab08ex"),
        "mayors": t("tab0ad"),
        "mayors_x": t("tab0ax"),
        "councillors": t("tab0bd"),
        "councillors_x": t("tab0bx"),
        "elected": t("tab06d"),
        "elected_x": t("tab06x"),
        "elected_mayors": t("tab04d"),
    }
    d.paths += list(files.values())
    unit_frames = []
    for name, city in (("units", False), ("units_x", True)):
        f = table(files[name])
        if not city:
            f = f.filter(~pl.col(col(f, "kod obce")).is_in(list(CITIES)))
        obec = col(f, "kod mesta") if city else col(f, "kod obce")
        okrsok = pl.concat_str(pl.lit("vo"), pl.col(col(f, "volebny obvod"))) if city else pl.col(col(f, "okrsok"))
        unit_frames.append(
            f.select(
                obec=pl.col(obec),
                okrsok=okrsok,
                registered=ints(f, col(f, "zapisanych")),
                voters=ints(f, col(f, "zucastnenych")),
                cast=ints(f, col(f, "odovzdanych obalok", exclude=["podiel"])),
                valid_mayor=ints(f, col(f, "starost") if has(f, "starost") else col(f, "primator")),
                valid_council=ints(f, col(f, "zastupitel")),
            )
        )
    units = pl.concat(unit_frames)
    vote_frames, cand_frames = [], []
    meta: dict[str, Contest] = {}
    elected = _elected_councillors(files["elected"], files["elected_x"])
    elected_mayors = _elected_mayors(files["elected_mayors"], files["mayors_x"], files["mayor_x"])
    for kind in ("mayor", "council"):
        for city in (False, True):
            f = table(files[f"{kind}_x" if city else kind])
            if not city:
                f = f.filter(~pl.col("OBEC" if "OBEC" in f.columns else col(f, "kod obce")).is_in(list(CITIES)))
            people = table(files[(f"{kind}s" if kind == "mayor" else "councillors") + ("_x" if city else "")])
            if "OBEC" in f.columns:
                obec, vo, okrsok, number, value = "OBEC", "VOOBEC", "OKRSOK", "PC_HL", "P_HL"
            else:
                obec = col(f, "kod mesta") if city else col(f, "kod obce")
                vo, number, value = col(f, "volebny obvod"), col(f, "poradie"), col(f, "pocet platnych hlasov")
                okrsok = vo if city else col(f, "okrsok")
            contest = (
                pl.concat_str(pl.lit("mayor:"), pl.col(obec))
                if kind == "mayor"
                else pl.concat_str(pl.lit("council:"), pl.col(obec), pl.lit(":"), pl.col(vo))
            )
            okrsok_expr = pl.concat_str(pl.lit("vo"), pl.col(vo)) if city else pl.col(okrsok)
            vote_frames.append(
                f.select(
                    contest=contest,
                    obec=pl.col(obec),
                    okrsok=okrsok_expr,
                    number=pl.col(number).cast(pl.Int32).cast(pl.String),
                    value=ints(f, value),
                ).filter(pl.col("value").is_not_null())
            )
            pobec = col(people, "kod mesta") if city else col(people, "kod obce")
            pcontest = (
                pl.concat_str(pl.lit("mayor:"), pl.col(pobec))
                if kind == "mayor"
                else pl.concat_str(pl.lit("council:"), pl.col(pobec), pl.lit(":"), pl.col(col(people, "volebny obvod")))
            )
            note = col(people, "poznamka") if has(people, "poznamka") else None
            cand_frames.append(
                people.select(
                    contest=pcontest,
                    number=pl.col(col(people, "poradie")).cast(pl.Int32).cast(pl.String),
                    name=_names(people),
                    party=pl.col(col(people, "politicky subjekt")),
                    status=pl.col(note).map_elements(lambda s: "withdrew" if fold(s) == "x" else None, pl.String)
                    if note
                    else pl.lit(None, pl.String),
                )
            )
    votes_ = pl.concat(vote_frames)
    candidates = pl.concat(cand_frames).with_columns(
        status=pl.when(pl.col("status").is_not_null())
        .then("status")
        .when(
            pl.struct("contest", "name").map_elements(
                lambda r: (
                    (r["contest"], fold(r["name"])) in elected or (r["contest"], fold(r["name"])) in elected_mayors
                ),
                pl.Boolean,
            )
        )
        .then(pl.lit("elected"))
        .otherwise(pl.lit(None, pl.String))
    )
    seats = Counter(contest for contest, _ in elected)
    for contest_key in candidates["contest"].unique().to_list():
        parts = contest_key.split(":")
        meta[contest_key] = (
            mayor_contest(parts[1])
            if parts[0] == "mayor"
            else council_contest(parts[1], parts[2], seats.get(contest_key))
        )
    unknown = set(votes_["contest"].unique()) - set(meta)
    if unknown:
        raise ValueError(f"oso {year}: votes for contests without candidates: {sorted(unknown)[:5]}")
    add_local(d, day, Local(candidates, votes_, units, meta))
    return d


def _elected_councillors(path: str, city_path: str) -> set[tuple[str, str]]:
    out = set()
    for p_, city in ((path, False), (city_path, True)):
        f = table(p_)
        obec = col(f, "kod mesta") if city else col(f, "kod obce")
        for row in f.select(obec=pl.col(obec), vo=pl.col(col(f, "volebny obvod")), name=_names(f)).iter_rows(
            named=True
        ):
            out.add((f"council:{row['obec']}:{row['vo']}", fold(row["name"])))
    return out


def _elected_mayors(path: str, city_people: str, city_votes: str) -> set[tuple[str, str]]:
    out = set()
    f = table(path)
    for row in f.select(obec=pl.col(col(f, "kod obce")), name=_names(f)).iter_rows(named=True):
        out.add((f"mayor:{row['obec']}", fold(row["name"])))
    votes_ = table(city_votes)
    city = col(votes_, "kod mesta")
    totals = votes_.group_by(city, col(votes_, "meno"), col(votes_, "priezvisko")).agg(
        ints(votes_, col(votes_, "pocet platnych hlasov")).sum().alias("v")
    )
    for code in totals[city].unique().to_list():
        top = totals.filter(pl.col(city) == code).sort("v", descending=True).row(0)
        out.add((f"mayor:{code}", fold(f"{top[1]} {top[2]}")))
    return out


def osk_modern(year: int) -> Day:
    day, _ = OSK_DAYS[year]
    d = Day(str(day), f"The {year} regional elections (chairs and councils) by polling district", [])
    d.method = (
        " In regional elections each voter casts one ballot for the region's chair and one for the council of the "
        "regional electoral district, with as many votes as the district elects; valid ballots are counted per ballot."
    )
    d.elections.append(osk_election(year))
    meta: dict[str, Contest] = {}
    if year == 2017:
        paths = {
            "units": "osk/2017/OSK_2017_tab02d.csv",
            "chair": "osk/2017/OSK_2017_tab06e.csv",
            "council": "osk/2017/OSK_2017_tab09e.csv",
            "seats": "osk/2017/OSK_2017_tab0db.csv",
        }
    elif year == 2022:
        paths = {
            "units": "osk/2022/OSK2022_SK_tab02bd.csv",
            "chair": "osk/2022/OSK2022_SK_tab06d.csv",
            "council": "osk/2022/OSK2022_SK_tab09d.csv",
            "seats": "osk/2022/OSK2022_SK_tab0db.csv",
            "chairs": "osk/2022/OSK2022_SK_tab0a.csv",
            "members": "osk/2022/OSK2022_SK_tab0b.csv",
            "parties": "osk/2022/OSK2022_SK_tab0c.csv",
            "map": "osk/2022/OSK2022_SK_tab0dc.csv",
        }
    else:
        paths = {
            "chair": "osk/2026/OSK2026_SK_tab06d.csv",
            "council": "osk/2026/OSK2026_SK_tab09d.csv",
            "seats": "osk/2026/OSK2026_SK_tab0db.csv",
        }
    d.paths += list(paths.values())
    seats_frame = table(paths["seats"])
    seats = {
        r[col(seats_frame, "kod volebneho obvodu")]: number(r[col(seats_frame, "volenych poslancov")])
        for r in seats_frame.iter_rows(named=True)
    }
    units = None
    if year == 2022:
        mapping = table(paths["map"])
        kraj_of = {r["OBEC"]: r["KRAJ"] for r in mapping.iter_rows(named=True)}
        obvod_of = {r["OBEC"]: r["VOBVOD"] for r in mapping.iter_rows(named=True)}
        parties = {r["PS"]: r["NPS"] for r in table(paths["parties"]).iter_rows(named=True)}

        def label(codes: str | None) -> str | None:
            listed = [c.strip() for c in (codes or "").split(",") if c.strip()]
            if not listed or listed == ["001"]:
                return None
            return ", ".join(parties.get(c, c) for c in listed)

        u = table(paths["units"])
        units = u.select(
            obec="OBEC",
            okrsok="OKRSOK",
            registered=ints(u, "P_ZAP"),
            voters=ints(u, "P_ZUC"),
            cast=ints(u, "P_OO"),
            valid_chair=ints(u, "P_HL_PRE"),
            valid_vuc=ints(u, "P_HL_POS"),
        )
        ch, co = table(paths["chair"]), table(paths["council"])
        votes_ = pl.concat(
            [
                ch.select(
                    contest=pl.concat_str(pl.lit("chair:"), pl.col("OBEC").replace_strict(kraj_of), pl.lit(":1")),
                    obec="OBEC",
                    okrsok="OKRSOK",
                    number="PC_HL",
                    value=ints(ch, "P_HL"),
                    flag="KANDIDAT",
                ),
                co.select(
                    contest=pl.concat_str(pl.lit("vuc:"), pl.col("OBEC").replace_strict(obvod_of)),
                    obec="OBEC",
                    okrsok="OKRSOK",
                    number="PC_HL",
                    value=ints(co, "P_HL"),
                    flag="KANDIDAT",
                ),
            ]
        )
        cands = []
        for path, key_ in ((paths["chairs"], "chair"), (paths["members"], "vuc")):
            f = table(path)
            contest = (
                pl.concat_str(pl.lit("chair:"), "KRAJ", pl.lit(":1"))
                if key_ == "chair"
                else pl.concat_str(pl.lit("vuc:"), "VOBVOD")
            )
            cands.append(
                f.select(
                    contest=contest,
                    number="PC_HL",
                    name=pl.concat_str("MENO", pl.lit(" "), "PRIEZVISKO"),
                    party=pl.col("PS").map_elements(label, pl.String),
                    note="POZNAMKA",
                )
            )
        people = pl.concat(cands)
    else:
        ch, co = table(paths["chair"]), table(paths["council"])
        frames = []
        cand_frames = []
        for f, key_ in ((ch, "chair"), (co, "vuc")):
            okrsok = pl.col(col(f, "okrsok")).map_elements(precinct, pl.String)
            contest = (
                pl.concat_str(pl.lit("chair:"), pl.col(col(f, "kod", "kraja")), pl.lit(":1"))
                if key_ == "chair"
                else pl.concat_str(pl.lit("vuc:"), pl.col(col(f, "kod volebneho obvodu")))
            )
            flag = col(f, "poznamka") if has(f, "poznamka") else col(f, "kandidat", exclude=["meno"])
            number_ = col(f, "poradie")
            frames.append(
                f.select(
                    contest=contest,
                    obec=pl.col(col(f, "kod obce")),
                    okrsok=okrsok,
                    number=pl.col(number_),
                    value=ints(f, col(f, "pocet platnych hlasov")),
                    flag=pl.col(flag),
                )
            )
            cand_frames.append(
                f.select(
                    contest=contest,
                    number=pl.col(number_),
                    name=_names(f),
                    party=pl.col(col(f, "politicky subjekt")),
                    note=pl.col(flag),
                ).unique(["contest", "number"])
            )
        votes_ = pl.concat(frames)
        people = pl.concat(cand_frames)
        if year == 2017:
            u = table(paths["units"])
            units = u.select(
                obec=pl.col(col(u, "kod obce")),
                okrsok=pl.col(col(u, "okrsok")).map_elements(precinct, pl.String),
                registered=ints(u, col(u, "zapisanych")),
                voters=ints(u, col(u, "zucastnili")),
                cast=ints(u, col(u, "odovzdanych obalok", exclude=["podiel"])),
                valid_chair=ints(u, col(u, "predsedu")),
                valid_vuc=ints(u, col(u, "zastupitel")),
            )
    flags = votes_.group_by("contest", "number").agg(pl.col("flag").drop_nulls().first())

    def status(note: object, flag: object) -> str | None:
        if fold(note) == "x" or fold(flag) == "x" or (year == 2017 and fold(flag) == "0"):
            return "withdrew"
        if (year == 2017 and fold(flag) == "2") or (year != 2017 and status_of(flag) == "elected"):
            return "elected"
        return None

    candidates = (
        people.unique(["contest", "number"])
        .join(flags, on=["contest", "number"], how="left")
        .select(
            "contest",
            "number",
            "name",
            party=pl.col("party"),
            status=pl.struct("note", "flag").map_elements(lambda r: status(r["note"], r["flag"]), pl.String),
        )
    )
    for contest_key in candidates["contest"].unique().to_list():
        kind, code = contest_key.split(":")[:2]
        meta[contest_key] = chair_contest(code, year) if kind == "chair" else vuc_contest(code, seats.get(code))
    if units is not None:
        units = units.with_columns(okrsok=pl.col("okrsok").map_elements(precinct, pl.String))
    add_local(
        d,
        day,
        Local(
            candidates,
            votes_.drop("flag").with_columns(okrsok=pl.col("okrsok").map_elements(precinct, pl.String)),
            units,
            meta,
        ),
    )
    return d


def oso_mayors(year: int) -> Day:
    """2014 and 2018: the mayors' votes and the municipal roll and turnout by municipality, and the councils. The
    office prints every council candidate's votes only for the ten cities of 2014 (by polling district, with the
    mayors'); for every other municipality it prints the votes of the elected councillors alone."""
    day = OSO_DAYS[year]
    d = Day(str(day), f"The {year} municipal elections, mayors and councils", [])
    d.method = (
        " The office's open data gives the mayors' votes in this election by municipality, and the votes of the "
        "elected councillors, who are the only council candidates it lists, by municipality (by council district in "
        "2018). Each voter casts one vote for the mayor and as many for the council as the council district elects."
    )
    d.elections.append(oso_election(year))
    if year == 2018:
        paths = ["oso/2018/OSO_2018_tab05.csv", "oso/2018/OSO_2018_tab02bd.csv"]
        f, u = table(paths[0]), table(paths[1])
        town = table("oso/2018/OSO_2018_tab02bx.csv")
        paths.append("oso/2018/OSO_2018_tab02bx.csv")
        town = town.rename(
            {
                "Kód mesta": "Kód obce",
                col(town, "platnych", "primator"): col(u, "platnych", "starost"),
            }
        )
        u = pl.concat([u, town], how="diagonal_relaxed")
        f = f.with_columns(_obec=pl.col("Kód obce"))
        u = u.with_columns(_obec=pl.col("Kód obce"))
        elected = {
            (r["Kód obce"], fold(f"{r['Meno']} {r['Priezvisko']}"))
            for r in table("oso/2018/OSO_2018_tab04.csv").iter_rows(named=True)
        }
        paths.append("oso/2018/OSO_2018_tab04.csv")
        names = pl.concat_str(pl.col("Meno"), pl.lit(" "), pl.col("Priezvisko"))
        number = pl.col("Poradie na hlasovacom lístku")
        status = pl.struct("_obec", "_name").map_elements(
            lambda r: "elected" if (r["_obec"], fold(r["_name"])) in elected else None, pl.String
        )
        party = pl.col("Politický subjekt")
    else:
        paths = ["oso/2014/OSO_2014_tab11.csv", "oso/2014/OSO_2014_tab07.csv"]
        big = cities_2014_codes()
        f = municipal_rows(table(paths[0]), "Obec, mesto, mestská časť", "Obvod")
        u = municipal_rows(table(paths[1]), "Obec, mesto, mestská časť", "Obvod")
        f, u = f.filter(~pl.col("_obec").is_in(big)), u.filter(~pl.col("_obec").is_in(big))
        names = pl.col("Meno a priezvisko")
        number = pl.int_range(1, pl.len() + 1).over("_obec").cast(pl.String)
        status = (
            pl.when(pl.col("Zvolený starosta/primátor") == "1")
            .then(pl.lit("elected"))
            .otherwise(pl.lit(None, pl.String))
        )
        party = pl.col("Skratka politického subjektu")
    d.paths += paths
    f = f.with_columns(_name=names, _number=number)
    contest = pl.concat_str(pl.lit("mayor:"), pl.col("_obec"))
    votes_ = f.select(
        contest=contest,
        obec="_obec",
        okrsok=pl.lit(None, pl.String),
        number=pl.col("_number").cast(pl.Int32).cast(pl.String),
        value=ints(f, col(f, "pocet", "hlas")),
    )
    candidates = f.select(
        contest=contest,
        number=pl.col("_number").cast(pl.Int32).cast(pl.String),
        name="_name",
        party=party,
        status=status,
    )
    units = u.select(
        obec="_obec",
        okrsok=pl.lit(None, pl.String),
        registered=ints(u, col(u, "zapisanych")),
        voters=ints(u, col(u, "vydan", exclude=["podiel"])),
        cast=ints(u, col(u, "odovzdanych obalok", exclude=["podiel"])),
        valid_mayor=ints(u, col(u, "platnych", "starost", exclude=["podiel"])),
        valid_council=ints(u, col(u, "platnych", "zastupitel", exclude=["podiel"])),
    )
    meta = {k: mayor_contest(k.split(":")[1]) for k in candidates["contest"].unique().to_list()}
    summed = votes_.group_by("obec").agg(pl.col("value").sum()).join(units.select("obec", "valid_mayor"), on="obec")
    short = summed.filter(pl.col("value") != pl.col("valid_mayor")).height
    if short:
        d.accepts["accounting.votes_valid"] = (
            "in some municipalities the office's votes for the mayoral candidates add up to a different number "
            "than the valid mayoral ballots it prints for the municipality; both are stored as printed"
        )
    add_local(d, day, Local(candidates, votes_, units, meta))
    if year == 2014:
        d.paths += [
            "oso/2014/OSO_2014_tab12.csv",
            "oso/2014/OSO_2014_tab13.csv",
            "oso/2014/OSO_2014_tab14.csv",
            "oso/2014/OSO_2014_tab10.csv",
        ]
        add_local(d, day, cities_2014())
        add_local(d, day, elected_2014(units))
    else:
        d.paths += ["oso/2018/OSO_2018_tab06.csv", "oso/2018/OSO_2018_tab06d.csv", "oso/2018/OSO_2018_tab0c.csv"]
        add_local(d, day, councils_2018(units))
    return d


def plain_name(text: object) -> str:
    """A candidate's name without the academic titles and birth surname the office prints after a comma."""
    return clean(str(text or "").split(",")[0])


def cities_2014_codes() -> list[str]:
    """The ten cities whose candidates' votes the office prints by polling district."""
    return table("oso/2014/OSO_2014_tab12.csv")["Obec"].unique().sort().to_list()


def number_text(name: str) -> pl.Expr:
    return pl.col(name).cast(pl.Int32).cast(pl.String)


def cities_2014() -> Local:
    """Bratislava, Košice, and eight other cities: every mayoral and council candidate with votes by polling district
    (by council district for the two city councils, whose polling-district numbers repeat across boroughs)."""
    units12, mayors, councillors = (table(f"oso/2014/OSO_2014_tab{n}.csv") for n in ("12", "13", "14"))
    city = pl.col("Obec").is_in(list(CITIES))
    spot = (
        pl.when(city)
        .then(pl.concat_str(pl.lit("vo"), pl.col("Volebný obvod")))
        .otherwise(number_text("Číslo volebného okrsku"))
        .alias("okrsok")
    )
    key_columns = ["obec", "okrsok"]
    units = (
        units12.select(
            obec="Obec",
            okrsok=spot,
            registered=ints(units12, col(units12, "zapisanych")),
            voters=ints(units12, col(units12, "vydan")),
            cast=ints(units12, col(units12, "odovzdanych obalok")),
            valid_mayor=ints(units12, col(units12, "platnych", "starost")),
            valid_council=ints(units12, col(units12, "platnych", "zastupitel")),
        )
        .group_by(key_columns)
        .agg(pl.all().sum())
    )
    if units.height != units.select(key_columns).unique().height:
        raise ValueError("oso 2014: polling districts repeat")
    parts: dict[str, pl.DataFrame] = {}
    for kind, frame, owner, ballot, status in (
        ("mayor", mayors, ["Obec"], "Poradie kandidáta na hlasovacom lístku", "Zvolený starosta/primátor"),
        (
            "council",
            councillors,
            ["Obec", "Volebný obvod"],
            "Poradie kandidáta na hlasovacom lístku",
            "Zvolený poslanec",
        ),
    ):
        contest = (
            pl.concat_str(pl.lit("mayor:"), pl.col("Obec"))
            if kind == "mayor"
            else pl.concat_str(pl.lit("council:"), pl.col("Obec"), pl.lit(":"), pl.col("Volebný obvod"))
        )
        flags = [pl.col(status).eq("1").any().alias("won")]
        if kind == "mayor":
            flags.append(pl.col("Vzdaný/odvolaný kandidát").eq("1").any().alias("out"))
        base = frame.with_columns(
            contest=contest,
            number=number_text(ballot),
            okrsok=spot,
            value=ints(frame, col(frame, "pocet", "hlas")),
        )
        candidates = base.group_by("contest", "number").agg(
            pl.col("Meno a priezvisko").first().alias("name"),
            pl.col("Skratka politického subjektu").first().alias("party"),
            *flags,
        )
        candidates = candidates.with_columns(
            name=pl.col("name").map_elements(plain_name, pl.String),
            status=pl.when(pl.col("won"))
            .then(pl.lit("elected"))
            .when(pl.col("out") if kind == "mayor" else pl.lit(False))
            .then(pl.lit("withdrew"))
            .otherwise(pl.lit(None, pl.String)),
        )
        votes_ = (
            base.select(
                "contest",
                obec="Obec",
                okrsok="okrsok",
                number="number",
                value="value",
            )
            .filter(pl.col("value").is_not_null())
            .group_by("contest", "obec", "okrsok", "number")
            .agg(pl.col("value").sum())
        )
        parts[kind] = candidates.select("contest", "number", "name", "party", "status")
        parts[f"{kind}_votes"] = votes_
    candidates = pl.concat([parts["mayor"], parts["council"]])
    votes_ = pl.concat([parts["mayor_votes"], parts["council_votes"]])
    meta = {
        k: mayor_contest(k.split(":")[1]) if k.startswith("mayor") else council_contest(*k.split(":")[1:], None)
        for k in candidates["contest"].unique().to_list()
    }
    return Local(candidates, votes_, units, meta)


def elected_2014(units: pl.DataFrame) -> Local:
    """The other municipalities' councils: the elected councillors' votes by municipality."""
    tab07 = municipal_rows(table("oso/2014/OSO_2014_tab07.csv"), "Obec, mesto, mestská časť", "Obvod")
    seats = {
        row["_obec"]: number(row[col(tab07, "poslancov", "mal zvolit")])
        for row in tab07.iter_rows(named=True)
        if row["_obec"] not in cities_2014_codes()
    }
    f = table("oso/2014/OSO_2014_tab10.csv")
    f = municipal_rows(f, "Obec, mesto, mestská časť", "Obvod")
    f = f.filter(~pl.col("_obec").is_in(cities_2014_codes())).with_columns(
        contest=pl.concat_str(pl.lit("council:"), pl.col("_obec")),
        number=pl.int_range(1, pl.len() + 1).over("_obec").cast(pl.String),
    )
    candidates = f.select(
        "contest",
        "number",
        name=pl.col("Meno a priezvisko poslanca").map_elements(plain_name, pl.String),
        party="Skratka politického subjektu",
        status=pl.lit("elected"),
    )
    votes_ = f.select(
        "contest",
        obec="_obec",
        okrsok=pl.lit(None, pl.String),
        number="number",
        value=ints(f, col(f, "pocet", "hlasov")),
    )
    meta = {}
    for k in candidates["contest"].unique().to_list():
        c = council_contest(k.split(":")[1], None, seats.get(k.split(":")[1]))
        c.completeness = None
        meta[k] = c
    return Local(
        candidates, votes_, units.select("obec", "okrsok", "registered", "voters", "cast", "valid_council"), meta
    )


def councils_2018(units: pl.DataFrame) -> Local:
    """The elected councillors and their votes by council district. Where the office lists a municipality in both of
    its tables the one with the ballot numbers is used."""
    numbered, ranked = table("oso/2018/OSO_2018_tab06.csv"), table("oso/2018/OSO_2018_tab06d.csv")
    ranked = ranked.filter(~pl.col("Kód obce").is_in(numbered["Kód obce"].unique().to_list()))
    frames = []
    for f, number_column in ((numbered, "Poradie na hlasovacom lístku"), (ranked, "Poradie zvolenia v obci")):
        frames.append(
            f.select(
                obec="Kód obce",
                vo="Volebný obvod v obci",
                number=number_text(number_column),
                name=_names(f).map_elements(plain_name, pl.String),
                party="Politický subjekt",
                value=ints(f, col(f, "pocet", "hlasov")),
            )
        )
    f = pl.concat(frames).with_columns(contest=pl.concat_str(pl.lit("council:"), "obec", pl.lit(":"), "vo"))
    if f.select("contest", "number").is_duplicated().any():
        raise ValueError("oso 2018: a council district prints a candidate number twice")
    candidates = f.select("contest", "number", "name", "party", status=pl.lit("elected"))
    votes_ = f.select(
        "contest",
        "obec",
        okrsok=pl.when(pl.col("obec").is_in(list(CITIES)))
        .then(pl.concat_str(pl.lit("vo"), pl.col("vo")))
        .otherwise(pl.lit(None, pl.String)),
        number="number",
        value="value",
    ).filter(pl.col("value").is_not_null())
    districts = table("oso/2018/OSO_2018_tab0c.csv")
    seats = {
        row["Kód obce"]: (number(row["Počet volebných obvodov v obci"]), number(row["Počet volených poslancov"]))
        for row in districts.iter_rows(named=True)
    }
    meta, bare = {}, set()
    for k in candidates["contest"].unique().to_list():
        _, obec, vo = k.split(":")
        count, total = seats[obec]
        c = council_contest(obec, vo, total if count == 1 else None)
        c.completeness = None
        meta[k] = c
        if count != 1 or obec in CITIES:
            bare.add(k)
    return Local(
        candidates,
        votes_,
        units.select("obec", "okrsok", "registered", "voters", "cast", "valid_council"),
        meta,
        frozenset(bare),
    )


# Municipal elections of 2002, 2006, and 2010. The office prints the elected mayors with their parties for 2002, and for
# 2006 and 2010 the roll, turnout, and valid ballots of each municipality, with the seats of its council and, for 2006,
# the councillors each party or coalition won. It prints no votes for any candidate or list.

OSO_OLD = (2002, 2006, 2010)


def party_label(text: object, *, order: bool) -> str | None:
    """Parties and coalitions as one label: the printed abbreviations joined by commas, in alphabetical order for
    2002 (the tables list a coalition in different orders), as printed for 2006 (its tables follow the office's
    codes)."""
    parts = [clean(part) for part in str(text or "").split(",") if clean(part)]
    if not parts:
        return None
    return ", ".join(sorted(parts, key=fold) if order else parts)


def old_mayor_option(c: Contest, name: str, label: str | None) -> None:
    text, independent = local_party(label)
    c.options.append(
        Option(
            "1",
            name,
            "candidate",
            position=None,
            party=text,
            person=name,
            independent=independent,
            outcome="elected",
        )
    )


def oso_old(year: int) -> Day:
    day = OSO_DAYS[year]
    d = Day(
        str(day), f"The {year} municipal elections, " + ("elected mayors" if year == 2002 else "municipalities"), []
    )
    d.elections.append(oso_election(year))
    if year == 2002:
        d.method = (
            " The office's open data lists the elected mayor of each municipality with the parties and coalitions "
            "that nominated them, and no votes, so these contests hold the winner and no results by unit."
        )
        paths = ["oso/2002/OSO_2002_tab01.csv", "oso/2002/OSO_2002_tab02.csv"]
        d.paths += paths
        towns, others = table(paths[0]), table(paths[1])
        towns = towns.select(towns.columns[:4])
        towns.columns = others.columns
        both = pl.concat([others, towns])
        name, group, person, parties = both.columns
        rows = both.with_columns(_label=pl.col(parties).map_elements(lambda s: party_label(s, order=True), pl.String))
        codes = resolve(list(zip(rows[name], rows[group], strict=True)))
        missing = [n for n, c in zip(rows[name], codes, strict=True) if c is None]
        if missing:
            raise ValueError(f"oso 2002: {len(missing)} municipalities without a code: {missing[:12]}")
        rows = rows.with_columns(
            _obec=pl.Series(codes, dtype=pl.String), _who=pl.col(person).map_elements(plain_name, pl.String)
        )
        # The table of towns and the table of the other municipalities both list the boroughs of Bratislava.
        clash = rows.group_by("_obec").agg(pl.col("_who").n_unique().alias("n")).filter(pl.col("n") > 1)
        if clash.height > 5:
            raise ValueError(f"oso 2002: two mayors for {clash['_obec'].to_list()[:10]}")
        if clash.height:
            d.method += (
                f" The office's two tables spell the mayor's name differently in {clash.height} municipalities; the "
                "table of all municipalities is used."
            )
        rows = rows.unique("_obec", keep="first", maintain_order=True)
        for row in rows.iter_rows(named=True):
            c = mayor_contest(row["_obec"])
            c.completeness = 0.0
            old_mayor_option(c, plain_name(row[person]), row["_label"])
            d.contests.append(c)
        return d
    return old_municipalities(d, year)


def old_municipalities(d: Day, year: int) -> Day:
    """2006 and 2010: each municipality's roll, turnout, and valid ballots for its mayor and its council, the council's
    seats, and for 2006 the seats of each party and coalition."""
    day = OSO_DAYS[year]
    path = f"oso/{year}/OSO_{year}_tab07.csv"
    d.paths.append(path)
    d.method = (
        " The office's open data gives, for 2006 and 2010, each municipality's roll, turnout, and valid ballots, and "
        "the seats of its council"
        + (", with the seats each party or coalition won in 2006" if year == 2006 else "")
        + ". It prints no votes for candidates or lists, so these contests hold the unit totals"
        + (" and the seats" if year == 2006 else "")
        + " but no votes for options."
    )
    d.accepts["accounting.votes_valid"] = (
        "the office prints the valid ballots for each mayoral contest but no votes for the candidates, so the "
        "contests hold no votes to add up to them"
    )
    frame = table(path)
    if year == 2006:
        rows = municipal_rows(frame, "Obec")
    else:
        frame = frame.filter(~pl.col("Obec").map_elements(lambda n: fold(n).endswith((" spolu", " spulu")), pl.Boolean))
        rows = municipal_rows(frame, "Obec", "Obvod")
    # The tables list Bratislava and Košice twice, as the city and as its "- mesto" part, with the same figures.
    plain = {fold(text) for text, _ in CITIES.values()}
    rows = rows.filter(
        ~(pl.col("_obec").is_in(list(CITIES)) & pl.col("Obec").map_elements(lambda n: fold(n) in plain, pl.Boolean))
    )
    if rows["_obec"].is_duplicated().any():
        raise ValueError(f"oso {year}: {rows.filter(pl.col('_obec').is_duplicated())['Obec'].to_list()[:10]} repeat")
    seats = ints(rows, col(rows, "poslancov", "mal zvolit"))
    totals = rows.select(
        obec="_obec",
        registered=ints(rows, col(rows, "zapisanych")),
        voters=ints(rows, col(rows, "vydan")),
        cast=ints(rows, col(rows, "odovzdanych obalok")),
        valid_council=ints(rows, col(rows, "platnych", "zastupitel", exclude=["podiel"])),
        valid_mayor=ints(rows, col(rows, "platnych", "starost", exclude=["podiel"])),
        seats=seats,
        elected=ints(rows, col(rows, "zvolenych poslancov")),
    )
    d.units.append(
        totals.select(
            unit=pl.concat_str(pl.lit(f"{day}:"), "obec"),
            scheme=pl.lit(OBEC),
            obec="obec",
            okres=pl.lit(None, pl.String),
            kraj=pl.lit(None, pl.String),
        )
    )
    parties: dict[str, list[tuple[str, str, int]]] = {}
    if year == 2006:
        path8 = "oso/2006/OSO_2006_tab08.csv"
        d.paths.append(path8)
        seated = table(path8)
        # The 2006 codes differ from the later ones for some boroughs, so the names, within their districts, give them.
        seat_codes = resolve(
            [(row[col(seated, "nazov obce")], row[col(seated, "nazov obvodu")]) for row in seated.iter_rows(named=True)]
        )
        if None in seat_codes:
            raise ValueError("oso 2006: municipalities without a code in the table of seats")
        for row, code in zip(seated.iter_rows(named=True), seat_codes, strict=True):
            parties.setdefault(str(code), []).append(
                (
                    row[col(seated, "kody politickych")],
                    party_label(row[col(seated, "skratky politickych")], order=False) or "",
                    int(row[col(seated, "zvolenych poslancov")] or 0),
                )
            )
        unknown = set(parties) - set(totals["obec"])
        if unknown:
            raise ValueError(f"oso 2006: seats for municipalities without a roll: {sorted(unknown)[:10]}")
    for row in totals.iter_rows(named=True):
        unit = f"{day}:{row['obec']}"
        for kind in ("mayor", "council"):
            c = mayor_contest(row["obec"]) if kind == "mayor" else council_contest(row["obec"], None, row["seats"])
            c.completeness = 0.0
            if kind == "council":
                if year == 2006 and sum(won for *_, won in parties.get(row["obec"], [])) != row["elected"]:
                    raise ValueError(f"oso 2006: {row['obec']} seats of parties differ from its elected councillors")
                for codes, label, won in parties.get(row["obec"], []):
                    independent = fold(label) in INDEPENDENT
                    c.options.append(
                        Option(
                            codes,
                            label,
                            "independents" if independent else "list",
                            position=None,
                            party=None if independent else label,
                            outcome="elected",
                            seats=won,
                        )
                    )
            valid = row[f"valid_{kind}"]
            measures = {REGISTERED: row["registered"], VOTERS: row["voters"], CAST: row["cast"], VALID: valid}
            present = {m: v for m, v in measures.items() if v is not None}
            c.facts.append(
                pl.DataFrame(
                    {
                        "unit": [unit] * len(present),
                        "option": [None] * len(present),
                        "measure": list(present),
                        "value": list(present.values()),
                    },
                    schema=FACT_SCHEMA,
                )
            )
            d.contests.append(c)
    vacant = [
        c
        for c in d.contests
        if c.key.startswith("council") and c.options and sum(o.seats or 0 for o in c.options) != c.magnitude
    ]
    if vacant:
        d.accepts["outcomes.seats_magnitude"] = (
            f"in {len(vacant)} municipalities the office prints fewer councillors elected than the council has seats "
            "to fill, so the party seats add up to less than the council's size; both are stored as printed"
        )
    return d


# Regional elections of 2001 to 2013. The office prints these by electoral district (volebný obvod): the council's
# votes for each candidate in each district (not for 2001), the chair's votes for each region, and, in the second
# round of 2005, 2009, and 2013, the chair's votes in each district. The first round was on the first day and the
# second round, where no candidate won a majority, two weeks later.

OSK_OLD = (2001, 2005, 2009, 2013)


def onum(text: object) -> int | None:
    """A count, which the 2005 tables sometimes print as "-" in quotation marks."""
    return number(str(text).strip('"') if text is not None else None)


OLD_TABLES: dict[int, dict[str, tuple[int, int]]] = {
    2001: {"obvod": (1, 1), "chair1": (1, 7), "obvod2": (2, 8), "chair2": (2, 9)},
    2005: {
        "obvod": (1, 2),
        "chair1": (1, 6),
        "elected": (1, 7),
        "others": (1, 8),
        "obvod2": (2, 1),
        "chair2": (2, 3),
    },
    2009: {
        "obvod": (1, 2),
        "chair1": (1, 5),
        "elected": (1, 8),
        "others": (1, 9),
        "obvod2": (2, 10),
        "chair2": (2, 7),
    },
    2013: {
        "obvod": (1, 2),
        "chair1": (1, 5),
        "all": (1, 8),
        "others": (1, 9),
        "obvod2": (2, 10),
        "chair2": (2, 7),
    },
}


def old_path(year: int, name: str) -> str:
    round_number, n = OLD_TABLES[year][name]
    return f"osk/{year}/{round_number}.kolo/OSK_{year}_{round_number}kolo_tab{n:02d}.csv"


def old_paths(year: int) -> list[str]:
    return [old_path(year, name) for name in OLD_TABLES[year]]


def kraj_of(text: object) -> str | None:
    """A region's code from the name in a table: 'Bratislavský', 'Bratislavský kraj', 'Spolu Bratislavský kraj'. The
    first three letters tell the regions apart, which also reads the 2005 table's 'žilinksy'."""
    first = {fold(name)[:3]: code for code, name in places().kraje.items()}
    words = fold(text).removeprefix("spolu").split()
    return first.get(words[0][:3]) if words else None


@dataclass
class OldRow:
    kraj: str | None
    name: str
    kind: str
    values: dict[str, int | None]
    cells: list[str | None]


def old_rows(frame: pl.DataFrame, obvod_kraj: dict[str, str] | None = None) -> list[OldRow]:
    """The rows of a district table, each a district, a region's total, or the republic's total. A region's total is
    printed in the district column as the region's name or 'Spolu', or in the first column as 'Spolu <region>'; 2005's
    first-round table has no region column, so a district's region comes from `obvod_kraj`."""
    names = {
        "registered": col(frame, "opravnenych"),
        "voters": col(frame, "vydane"),
        "cast": col(frame, "odovzdan", "obal", exclude=["podiel"]),
    }
    if has(frame, "platnych", "predsedu"):
        names["valid_chair"] = col(frame, "platnych", "predsedu")
    else:
        names["valid_chair"] = col(frame, "platnych", "listkov", exclude=["zastupitel"])
    if has(frame, "platnych", "zastupitel"):
        names["valid_vuc"] = col(frame, "platnych", "zastupitel")
        names["seats"] = col(frame, "poslancov", "mal")
        names["elected"] = col(frame, "zvolenych", "poslancov")
    out: list[OldRow] = []
    for row in frame.iter_rows(named=True):
        cells = list(row.values())
        values = {key_: onum(row[c]) for key_, c in names.items()}
        first = clean(cells[0])
        if obvod_kraj is not None:
            label = fold(first)
            if label.startswith(("spolu", "sr")):
                out.append(OldRow(None, first, "sr", values, cells))
            elif label.endswith("kraj") and kraj_of(first):
                out.append(OldRow(kraj_of(first), first, "kraj", values, cells))
            else:
                out.append(OldRow(obvod_kraj[fold(first)], first, "obvod", values, cells))
            continue
        second = clean(cells[1])
        label, name = fold(first), fold(second)
        if label == "sr" or "za sr" in label:
            out.append(OldRow(None, first, "sr", values, cells))
        elif label.startswith("spolu") or not name or name.startswith("spolu") or name == "sr spolu" or name == label:
            out.append(OldRow(kraj_of(first), first, "kraj", values, cells))
        else:
            kraj = kraj_of(first)
            if kraj is None:
                raise ValueError(f"no region in {first!r}")
            out.append(OldRow(kraj, second, "obvod", values, cells))
    return out


@functools.cache
def old_table(year: int, name: str) -> list[OldRow]:
    mapping = None
    if year == 2005 and name == "obvod":
        mapping = {}
        for c in old_candidates(year, "elected", council=True):
            kraj = kraj_of(c["kraj"])
            assert kraj is not None and c["obvod"]
            mapping[fold(c["obvod"])] = kraj
    return old_rows(table(old_path(year, name)), mapping)


@functools.cache
def old_candidates(year: int, name: str, *, council: bool) -> list[dict[str, str]]:
    """Rows of a candidate table: region, district (council tables), name, party label, votes, and the other cells."""
    frame = table(old_path(year, name))
    person, label, votes_ = col(frame, "meno"), col(frame, "skratka"), col(frame, "platnych hlasov", exclude=["podiel"])
    used = {frame.columns[0], person, label, votes_}
    if council:
        used.add(frame.columns[1])
    out = []
    for row in frame.iter_rows(named=True):
        if not clean(row[person]):
            continue  # a blank line the office leaves between a region's rows
        out.append(
            {
                "kraj": clean(row[frame.columns[0]]),
                "obvod": clean(row[frame.columns[1]]) if council else "",
                "name": clean(row[person]),
                "party": clean(row[label]),
                "votes": clean(row[votes_]),
                "notes": " ".join(clean(row[c]) for c in frame.columns if c not in used and row[c]),
            }
        )
    return out


def obvod_key(name: object) -> str:
    """A district's name as the candidate tables print it, some with 'obvod' in front."""
    return fold(name).removeprefix("obvod ")


def obvod_code(year: int, kraj: str, name: str) -> str:
    return f"{plan_of(year)}:{kraj}:{changeset.slug(name)}"


def plan_of(year: int) -> int:
    """The first election held under the district plan of `year`: a plan that a later election printed unchanged is
    one plan."""

    def names(y: int) -> set[tuple[str | None, str]]:
        return {(r.kraj, changeset.slug(r.name)) for r in old_table(y, "obvod") if r.kind == "obvod"}

    for earlier in OSK_OLD:
        if earlier < year and names(earlier) == names(year):
            return earlier
    return year


def old_geo_paths() -> set[str]:
    """The tables the geo package cites for the districts of 2001 to 2013: each plan's first district table, and the
    2005 candidate table that tells which region each of its districts is in."""
    return {old_path(plan, "obvod") for plan in {plan_of(y) for y in OSK_OLD}} | {old_path(2005, "elected")}


@functools.cache
def old_districts() -> dict[str, tuple[str, str, date, date]]:
    """Regional electoral districts of 2001 to 2013: code → (name, region, first day, last day). Each plan is valid
    from its first election to the day before the first election of the next plan (2017's for the last)."""
    plans = sorted({plan_of(year) for year in OSK_OLD})
    out: dict[str, tuple[str, str, date, date]] = {}
    for index, plan in enumerate(plans):
        following = OSK_DAYS[plans[index + 1]][0] if index + 1 < len(plans) else OSK_DAYS[2017][0]
        for r in old_table(plan, "obvod"):
            if r.kind == "obvod":
                assert r.kraj is not None
                out[obvod_code(plan, r.kraj, r.name)] = (
                    r.name,
                    r.kraj,
                    OSK_DAYS[plan][0],
                    date.fromordinal(following.toordinal() - 1),
                )
    return out


def old_council_contest(year: int, kraj: str, name: str, seats: int | None) -> Contest:
    text = places().kraje[kraj]
    code = obvod_code(year, kraj, name)
    return Contest(
        f"vuc:{code}",
        "osk:1",
        "body_seats",
        f"Zastupiteľstvo, {text}, {name}",
        f"{changeset.slug(text)}-council-{changeset.slug(name)}",
        system("vuc-council"),
        district=area(VUC_OBVOD, code),
        magnitude=seats,
        body=regional_council(kraj),
    )


CHAIR_NOTES = (("zvolen", "elected"), ("postupuj", "advanced"), ("nevoliteln", "disqualified"))


def chair_outcome(notes: str) -> str:
    folded = fold(notes)
    for word, outcome in CHAIR_NOTES:
        if word in folded:
            return outcome
    return "eliminated"


OLD_RELABELED = {2009: {("6", "banska stiavnica"): "Brezno"}}
"""A district whose candidates the office's list of those not elected prints under its neighbor's name: year →
(region, name printed) → the district's own name."""


def relabel_second_block(rows: list[dict[str, str]], region: str, printed: str, belongs: str) -> list[dict[str, str]]:
    """Rows printed under one district's name that are in two blocks, each in order of votes: the second block, where
    the votes rise again, belongs to `belongs`."""
    out = []
    previous: int | None = None
    second = False
    for c in rows:
        if kraj_of(c["kraj"]) == region and obvod_key(c["obvod"]) == printed:
            now = onum(c["votes"]) or 0
            second = second or (previous is not None and now > previous)
            previous = now
            if second:
                c = {**c, "obvod": belongs}
        out.append(c)
    return out


def old_facts(rows: list[tuple[str, str | None, int, int | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        [r for r in rows if r[3] is not None],
        schema={"unit": pl.String, "option": pl.String, "measure": pl.Int16, "value": pl.Int64},
        orient="row",
    )


def old_accounting(unit: str, row: OldRow, valid: str) -> list[tuple[str, str | None, int, int | None]]:
    return [
        (unit, None, REGISTERED, row.values["registered"]),
        (unit, None, VOTERS, row.values["voters"]),
        (unit, None, CAST, row.values["cast"]),
        (unit, None, VALID, row.values[valid]),
    ]


def osk_old(year: int) -> Day:
    first, second = OSK_DAYS[year]
    assert second is not None
    d = Day(
        str(first),
        f"The {year} regional elections: chairs by region and, in the second round, by electoral district"
        + (", councils by electoral district" if year != 2001 else ", and each council district's roll and turnout"),
        [],
    )
    d.method = (
        " The office prints these elections by electoral district and region, not by municipality or polling "
        "district. Each voter cast one ballot for the region's chair and one for the council of the district, with as "
        "many votes as the district elects; valid ballots are counted per ballot."
    )
    d.elections += [osk_election(year, 1), osk_election(year, 2)]
    d.paths += old_paths(year)
    table_units: dict[str, dict[str, str | None]] = {}

    def unit(day: date, scheme: str, code: str, area_id: str) -> str:
        name = f"{day}:{'vuc' if scheme == VUC_OBVOD else 'kraj'}:{code}"
        table_units[name] = {
            "unit": name,
            "scheme": scheme,
            "obec": None,
            "okres": None,
            "kraj": None,
            "area": area_id,
            "relation": "equals",
        }
        return name

    def kraj_unit(day: date, kraj: str) -> str:
        return unit(day, KRAJ, kraj, area(KRAJ, kraj))

    def obvod_unit(day: date, kraj: str, name: str) -> str:
        code = obvod_code(year, kraj, name)
        return unit(day, VUC_OBVOD, code, area(VUC_OBVOD, code))

    table1 = old_table(year, "obvod")
    obvody = [r for r in table1 if r.kind == "obvod"]
    totals = {r.kraj: r for r in table1 if r.kind == "kraj"}
    national = next(r for r in table1 if r.kind == "sr") if any(r.kind == "sr" for r in table1) else None
    for kraj in sorted(totals, key=str):
        mine = [r for r in obvody if r.kraj == kraj]
        for key_ in ("registered", "voters", "cast", "valid_chair", "valid_vuc"):
            if key_ in totals[kraj].values and sum(r.values[key_] or 0 for r in mine) != totals[kraj].values[key_]:
                raise ValueError(
                    f"{year} {kraj} {key_}: districts add up to {sum(r.values[key_] or 0 for r in mine)}, the "
                    f"office's total is {totals[kraj].values[key_]}"
                )
    if national is not None:
        for key_ in ("registered", "voters", "cast", "valid_chair", "seats"):
            got = sum(r.values[key_] or 0 for r in obvody)
            if got != national.values[key_]:
                raise ValueError(
                    f"{year} {key_}: districts add up to {got}, the national total is {national.values[key_]}"
                )

    # Chairs: the first round's votes by region, then the second round's.
    for round_number in (1, 2):
        day = first if round_number == 1 else second
        rows = old_candidates(year, f"chair{round_number}", council=False)
        if year == 2005 and round_number == 1:
            # 2005 flags a candidate who advances and a candidate who could not be elected in two columns.
            frame = table(old_path(year, "chair1"))
            flags = [(row[frame.columns[-2]], row[frame.columns[-1]]) for row in frame.iter_rows(named=True)]
            for c_, (advance, barred) in zip(rows, flags, strict=True):
                c_["notes"] = "postupuje" if advance == "1" else "nevoliteľný" if barred == "1" else ""
        if year == 2005 and round_number == 2:
            frame = table(old_path(year, "chair2"))
            for c_, row in zip(rows, frame.iter_rows(named=True), strict=True):
                c_["notes"] = "zvolený" if row[frame.columns[-1]] == "1" else ""
        by_obvod = round_number == 2 and year != 2001
        detail = old_table(year, "obvod2") if round_number == 2 else table1
        kraje = sorted({kraj_of(c_["kraj"]) or "" for c_ in rows})
        for kraj in kraje:
            mine = sorted((c_ for c_ in rows if kraj_of(c_["kraj"]) == kraj), key=lambda c_: -(onum(c_["votes"]) or 0))
            c = chair_contest(kraj, year, round_number)
            for n, cand in enumerate(mine, start=1):
                label, independent = local_party(cand["party"])
                outcome = chair_outcome(cand["notes"])
                if round_number == 2:
                    outcome = "elected" if outcome == "elected" else "lost"
                c.options.append(
                    Option(
                        str(n),
                        cand["name"],
                        "candidate",
                        party=label,
                        person=cand["name"],
                        independent=independent,
                        outcome=outcome,
                    )
                )
            region = next(r for r in detail if r.kind == "kraj" and r.kraj == kraj)
            if not by_obvod:
                u = kraj_unit(day, kraj)
                c.facts.append(
                    old_facts(
                        [(u, str(n), VOTES, onum(cand["votes"])) for n, cand in enumerate(mine, start=1)]
                        + old_accounting(u, region, "valid_chair")
                    )
                )
            else:
                header = table(old_path(year, "obvod2")).columns
                start = (
                    header.index(col(table(old_path(year, "obvod2")), "platnych", "listkov", exclude=["zastupitel"]))
                    + 1
                )
                mine_rows = [r for r in detail if r.kind == "obvod" and r.kraj == kraj]
                active = [
                    j for j in range(start, len(header), 2) if any(onum(r.cells[j]) is not None for r in mine_rows)
                ]
                column_of: dict[int, str] = {}
                for n, cand in enumerate(mine, start=1):
                    # 2009 prints some regions' columns twice; the first is read.
                    match = [j for j in active if sum(onum(r.cells[j]) or 0 for r in mine_rows) == onum(cand["votes"])]
                    if not match:
                        raise ValueError(f"{year} {kraj}: no column adds up to the votes of {cand['name']}")
                    column_of[match[0]] = str(n)
                parts: list[tuple[str, str | None, int, int | None]] = []
                for r in mine_rows:
                    u = obvod_unit(day, kraj, r.name)
                    parts += old_accounting(u, r, "valid_chair")
                    parts += [(u, option, VOTES, onum(r.cells[j])) for j, option in column_of.items()]
                c.facts.append(old_facts(parts))
                for n, cand in enumerate(mine, start=1):
                    c.declared[(str(n), VOTES)] = onum(cand["votes"]) or 0
                for key_, measure in (
                    ("registered", REGISTERED),
                    ("voters", VOTERS),
                    ("cast", CAST),
                    ("valid_chair", VALID),
                ):
                    if region.values[key_] is not None:
                        c.declared[(None, measure)] = region.values[key_] or 0
            d.contests.append(c)

    # Councils: the district's candidates and votes.
    if year != 2001:
        if year == 2013:
            # The office lists every candidate and, separately, those who were not elected.
            everyone = old_candidates(year, "all", council=True)
            left = Counter(
                (kraj_of(c_["kraj"]), fold(c_["name"]), onum(c_["votes"]))
                for c_ in old_candidates(year, "others", council=True)
            )
            names, others = [], []
            for c_ in everyone:
                k = (kraj_of(c_["kraj"]), fold(c_["name"]), onum(c_["votes"]))
                if left[k] > 0:
                    left[k] -= 1
                    others.append(c_)
                else:
                    names.append(c_)
            d.method += (
                " The office's list of candidates who were not elected prints the first district of the Banská Bystrica "
                "region under the eleventh, so each candidate is matched to the list of all candidates by region, name, "
                "and votes."
            )
        else:
            names = old_candidates(year, "elected", council=True)
            others = old_candidates(year, "others", council=True)
            for (region, printed), belongs in OLD_RELABELED.get(year, {}).items():
                others = relabel_second_block(others, region, printed, belongs)
        for r in obvody:
            assert r.kraj is not None
            wanted = (r.kraj, obvod_key(r.name))
            mine = [(c_, True) for c_ in names if (kraj_of(c_["kraj"]), obvod_key(c_["obvod"])) == wanted]
            mine += [(c_, False) for c_ in others if (kraj_of(c_["kraj"]), obvod_key(c_["obvod"])) == wanted]
            mine.sort(key=lambda t_: (-(onum(t_[0]["votes"]) or 0), t_[0]["name"]))
            c = old_council_contest(year, r.kraj, r.name, r.values["seats"])
            u = obvod_unit(first, r.kraj, r.name)
            for n, (cand, elected) in enumerate(mine, start=1):
                label, independent = local_party(cand["party"])
                c.options.append(
                    Option(
                        str(n),
                        cand["name"],
                        "candidate",
                        party=label,
                        person=cand["name"],
                        independent=independent,
                        outcome="elected" if elected else "lost",
                    )
                )
            winners = sum(1 for _, e in mine if e)
            if winners != r.values["seats"]:
                raise ValueError(f"{year} {r.name}: {winners} elected, {r.values['seats']} seats")
            lowest = min((onum(cand["votes"]) or 0 for cand, e in mine if e), default=0)
            highest = max((onum(cand["votes"]) or 0 for cand, e in mine if not e), default=0)
            if highest > lowest:
                raise ValueError(f"{year} {r.name}: a candidate not elected has more votes than one elected")
            c.facts.append(
                old_facts(
                    [(u, str(n), VOTES, onum(cand["votes"])) for n, (cand, _) in enumerate(mine, start=1)]
                    + old_accounting(u, r, "valid_vuc")
                )
            )
            d.contests.append(c)
    else:
        d.accepts["accounting.votes_valid"] = (
            "the office prints no candidate's votes for the 2001 councils, only each district's roll, turnout, and "
            "valid ballots, so the districts that elect one councillor have no votes to compare with the ballots"
        )
        for r in obvody:
            assert r.kraj is not None
            c = old_council_contest(year, r.kraj, r.name, r.values["seats"])
            c.facts.append(old_facts(old_accounting(obvod_unit(first, r.kraj, r.name), r, "valid_vuc")))
            d.contests.append(c)
    d.units.append(
        pl.DataFrame(list(table_units.values()), schema={**UNIT_SCHEMA, "area": pl.String(), "relation": pl.String()})
    )
    return d


# Municipal by-elections (nové voľby). The Speaker of the National Council calls them for a municipality whose mayor's
# office fell vacant or whose council could not be completed, on a few Saturdays a year. The office publishes each
# day's results as its own set of tables: by municipality name for the 2014 to 2018 term (open-data CSV for the first
# two days, spreadsheets after), by municipality code with the elected councillors' votes for 2019 to 2021, and by
# polling district with every candidate's votes from March 2022.

NOVE_FILES = "https://volby.statistics.sk/oso/doplnkove{term}/files/{name}"
NOVE_DAYS: dict[date, tuple[str, str]] = {
    date(2015, 3, 7): ("csv", "oso/2015/03/OSO_2015_03_tab{}.csv"),
    date(2015, 6, 13): ("csv", "oso/2015/06/OSO_2015_06_tab{}.csv"),
    date(2015, 10, 24): ("names", NOVE_FILES.format(term=2014, name="oso_201510_xlsx.zip")),
    date(2016, 4, 9): ("names", NOVE_FILES.format(term=2014, name="oso_201604_xlsx.zip")),
    date(2016, 11, 5): ("names", NOVE_FILES.format(term=2014, name="oso_201611_xlsx.zip")),
    date(2017, 4, 8): ("names", NOVE_FILES.format(term=2014, name="oso_201704_xlsx.zip")),
    date(2017, 10, 14): ("names", NOVE_FILES.format(term=2014, name="oso_201710_xlsx.zip")),
    date(2018, 1, 27): ("names", NOVE_FILES.format(term=2014, name="oso_201801_xlsx.zip")),
    date(2019, 4, 13): ("codes", NOVE_FILES.format(term=2018, name="OSO_201904_xlsx.zip")),
    date(2019, 9, 21): ("codes", NOVE_FILES.format(term=2018, name="OSO_201909_xlsx.zip")),
    date(2020, 10, 3): ("codes", NOVE_FILES.format(term=2018, name="OSO_202010_xlsx.zip")),
    date(2021, 5, 15): ("codes", NOVE_FILES.format(term=2018, name="OSO_202105_xlsx.zip")),
    date(2021, 10, 23): ("codes", NOVE_FILES.format(term=2018, name="OSO_202110_xlsx.zip")),
    date(2022, 3, 19): ("full", NOVE_FILES.format(term=2018, name="OSO_202203_xlsx.zip")),
    date(2023, 3, 25): ("full", NOVE_FILES.format(term=2022, name="OSO202303_SK_xlsx.zip")),
    date(2023, 9, 9): ("full", NOVE_FILES.format(term=2022, name="OSO202309_SK_xlsx.zip")),
    date(2024, 2, 10): ("full", NOVE_FILES.format(term=2022, name="OSO202402_SK_xlsx.zip")),
    date(2024, 10, 5): ("full", NOVE_FILES.format(term=2022, name="OSO202410_SK_xlsx.zip")),
    date(2025, 3, 15): ("full", NOVE_FILES.format(term=2022, name="OSO202503_SK_xlsx.zip")),
    date(2025, 9, 6): ("full", NOVE_FILES.format(term=2022, name="OSO202509_SK_xlsx.zip")),
    date(2026, 3, 21): ("full", NOVE_FILES.format(term=2022, name="OSO202603_SK_xlsx.zip")),
}
NOVE_PAGES = [f"http://volby.statistics.sk/oso/doplnkove{term}/sk/" for term in (2014, 2018, 2022)]
"""The pages that list each term's by-election days with the decisions of the Speaker that called them."""
SK_MONTHS = [
    "januára",
    "februára",
    "marca",
    "apríla",
    "mája",
    "júna",
    "júla",
    "augusta",
    "septembra",
    "októbra",
    "novembra",
    "decembra",
]


@functools.cache
def xbook(path: str) -> dict[str, list[list[str]]]:
    """A spreadsheet bundle: each worksheet's table suffix ("12", "02bf") → its rows of cleaned cells."""
    out: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, path).path) as bundle:
        for name in bundle.namelist():
            found = re.search(r"_tab(\w+)\.xlsx$", name)
            if found:
                sheet = pl.read_excel(io.BytesIO(bundle.read(name)), has_header=False, infer_schema_length=0)
                out[found.group(1)] = [[clean(cell) for cell in row] for row in sheet.iter_rows()]
    return out


def xframe(path: str, *suffixes: str) -> pl.DataFrame:
    """The first of these worksheets the bundle holds, as strings under its header row (the first with two cells)."""
    book = xbook(path)
    rows = book[next(s for s in suffixes if s in book)]
    start = next(i for i, row in enumerate(rows) if sum(1 for cell in row if cell) >= 2)
    names = [name or f"_{i}" for i, name in enumerate(rows[start])]
    body = [[cell or None for cell in row] for row in rows[start + 1 :] if any(row)]
    return pl.DataFrame(body, schema={n: pl.String for n in names}, orient="row")


def has_sheet(path: str, suffix: str) -> bool:
    return suffix in xbook(path)


def nove_election(day: date) -> Election:
    return Election(
        "oso",
        "by_election",
        day,
        day,
        f"{day:%-d %B %Y} Slovak municipal by-elections",
        f"Nové voľby do orgánov samosprávy obcí {day.day}. {SK_MONTHS[day.month - 1]} {day.year}",
        f"sk/{day}-municipal-by-election",
    )


@dataclass
class NoveRows:
    """One day's tables in the shape every reader returns."""

    units: pl.DataFrame  # obec, okrsok, registered, voters, cast, valid_mayor, valid_council
    candidates: pl.DataFrame  # contest, number, name, party, status
    votes: pl.DataFrame  # contest, obec, okrsok, number, value
    seats: dict[str, int]  # council contest → seats the office says were to be filled
    published: dict[str, int]  # the office's own totals for the day
    paths: list[str]
    note: str = ""


UNIT_COLUMNS = ("registered", "voters", "cast", "valid_mayor", "valid_council")
Cells = list[str]


def finish_rows(
    units: list[dict[str, str | int | None]],
    cand: list[tuple[str, str, str, str, str | None]],
    vote: list[tuple[str, str, str | None, str, int | None]],
    seats: dict[str, int],
    published: dict[str, int],
    paths: list[str],
) -> NoveRows:
    schema = {"obec": pl.String, "okrsok": pl.String, **{name: pl.Int64 for name in UNIT_COLUMNS}}
    return NoveRows(
        pl.DataFrame(units, schema=schema),
        pl.DataFrame(cand, schema=["contest", "number", "name", "party", "status"], orient="row"),
        pl.DataFrame(
            vote,
            schema={
                "contest": pl.String,
                "obec": pl.String,
                "okrsok": pl.String,
                "number": pl.String,
                "value": pl.Int64,
            },
            orient="row",
        ).filter(pl.col("value").is_not_null()),
        seats,
        published,
        paths,
    )


def grouped(rows: list[list[str]]) -> list[tuple[str, str, Cells]]:
    """Rows that print a district's name (obvod, the okres) only on the first of its rows, as (district, row)."""
    out, group = [], ""
    for row in rows:
        group = row[0] or group
        out.append((group, row[1], row[2:]))
    return out


def hierarchical(rows: list[list[str]]) -> list[tuple[str, str, Cells]]:
    """The candidate worksheets of 2015 to 2018, which print a district line, a municipality line, and then the
    municipality's people, each with a name and its figures: (district, municipality, cells)."""
    out: list[tuple[str, str, Cells]] = []
    pending: list[str] = []
    okres = ""
    for row in rows:
        if not any(row):
            continue
        if not any(row[1:]):
            pending.append(row[0])
            continue
        if len(pending) >= 2:
            okres = pending[-2]
        obec = pending[-1] if pending else out[-1][1]
        pending = []
        out.append((okres, obec, row))
    return out


def csv_rows(path: str) -> list[list[str]]:
    return [[cell or "" for cell in row] for row in table(path).iter_rows()]


def total_cells(rows: list[list[str]]) -> Cells:
    return next(row for row in rows if fold(row[0]).startswith(("spolu", "s p o l u")))


def nove_names(day: date, kind: str, source: str) -> NoveRows:
    """2015 to 2018: municipalities by name within their district. The councillors are those elected."""
    if kind == "csv":
        paths = [source.format(n) for n in ("01", "07", "10", "11")]
        summary, units_, councillors_, mayors_ = (csv_rows(p) for p in paths)
        unit_rows = grouped(units_)
        council_rows = grouped(councillors_)  # obvod, obec, name, party, votes
        mayor_rows = grouped(mayors_)  # obvod, obec, name, votes, elected, party
    else:
        paths = [source]
        book = xbook(source)
        summary = book["01"]
        unit_rows = grouped(
            [row for row in book["12"] if len(row) >= 13 and row[1] and re.fullmatch(r"[\d\s.]+", row[2])]
        )
        council_rows = [(g, o, c[:3]) for g, o, c in hierarchical(book["14"][2:])]  # name, party, votes
        mayor_rows = [(g, o, c[:4]) for g, o, c in hierarchical(book["15"][2:])]  # name, votes, elected, party
    codes = resolve([(name, group) for group, name, _ in unit_rows])
    missing = [name for (_, name, _), code in zip(unit_rows, codes, strict=True) if code is None]
    if missing:
        raise ValueError(f"{day}: municipalities without a code: {missing}")
    code_of = {(fold(group), fold(name)): str(code) for (group, name, _), code in zip(unit_rows, codes, strict=True)}
    unit_codes = [str(code) for code in codes]
    units = [
        {
            "obec": code,
            "okrsok": None,
            "registered": number(cells[0]),
            "voters": number(cells[1]),
            "cast": number(cells[3]),
            "valid_council": number(cells[4]),
            "valid_mayor": number(cells[5]),
        }
        for (_, _, cells), code in zip(unit_rows, unit_codes, strict=True)
    ]
    cand: list[tuple[str, str, str, str, str | None]] = []
    vote: list[tuple[str, str, str | None, str, int | None]] = []
    counted: Counter[str] = Counter()
    for group, name, cells in mayor_rows:
        obec = code_of[(fold(group), fold(name))]
        counted[obec] += 1
        contest, n = f"mayor:{obec}", str(counted[obec])
        elected = fold(cells[2]) in ("zvoleny", "1")
        cand.append((contest, n, clean(cells[0]), clean(cells[3]), "elected" if elected else None))
        vote.append((contest, obec, None, n, number(cells[1])))
    counted = Counter()
    for group, name, cells in council_rows:
        obec = code_of[(fold(group), fold(name))]
        counted[obec] += 1
        contest, n = f"council:{obec}:1", str(counted[obec])
        cand.append((contest, n, clean(cells[0]), clean(cells[1]), "elected"))
        vote.append((contest, obec, None, n, number(cells[2])))
    total = total_cells(summary)
    published = {
        "obec": number(total[1]) or 0,
        "registered": number(total[4]) or 0,
        "voters": number(total[5]) or 0,
        "cast": number(total[7]) or 0,
        "valid_council": number(total[8]) or 0,
        "valid_mayor": number(total[9]) or 0,
        "councillors": number(total[11]) or 0,
        "mayors": number(total[3]) or 0,
    }
    seats = {}
    for (group, name, cells), code in zip(unit_rows, unit_codes, strict=True):
        if (number(cells[9]) or 0) > 0:
            seats[f"council:{code}:1"] = number(cells[9]) or 0
    return finish_rows(units, cand, vote, seats, published, paths)


def nove_codes(day: date, source: str) -> NoveRows:
    """2019 to 2021: municipalities by code; every mayoral candidate's votes, and the elected councillors'."""
    units_ = xframe(source, "02bd")
    units = [
        {
            "obec": r[col(units_, "kod obce")],
            "okrsok": None,
            "registered": number(r[col(units_, "zapisanych")]),
            "voters": number(r[col(units_, "vydanych", "obalok")]),
            "cast": number(r[col(units_, "odovzdanych obalok", exclude=["podiel"])]),
            "valid_council": number(r[col(units_, "platnych", "zastupitel", exclude=["podiel"])]),
            "valid_mayor": number(r[col(units_, "platnych", "starost", exclude=["podiel"])]),
        }
        for r in units_.iter_rows(named=True)
    ]
    cand: list[tuple[str, str, str, str, str | None]] = []
    vote: list[tuple[str, str, str | None, str, int | None]] = []
    mayors_ = xframe(source, "05")
    for r in mayors_.iter_rows(named=True):
        obec = r[col(mayors_, "kod obce")]
        contest, n = f"mayor:{obec}", str(int(r[col(mayors_, "poradie")] or 0))
        name = clean(f"{r[col(mayors_, 'meno', exclude=['priezvisko'])]} {r[col(mayors_, 'priezvisko')]}")
        elected = fold(r[col(mayors_, "kandidat")]) == "zvoleny"
        cand.append((contest, n, name, r[col(mayors_, "politicky subjekt")] or "", "elected" if elected else None))
        vote.append((contest, obec, None, n, number(r[col(mayors_, "pocet platnych hlasov")])))
    councillors_ = xframe(source, "06")
    for r in councillors_.iter_rows(named=True):
        obec, vo = r[col(councillors_, "kod obce")], r[col(councillors_, "volebny obvod")]
        contest, n = f"council:{obec}:{vo}", str(int(r[col(councillors_, "poradie")] or 0))
        cand.append(
            (
                contest,
                n,
                clean(r[col(councillors_, "meno a priezvisko")]),
                r[col(councillors_, "politicky subjekt")] or "",
                "elected",
            )
        )
        vote.append((contest, obec, None, n, number(r[col(councillors_, "pocet platnych hlasov")])))
    terr = xframe(source, "0c")
    seats: dict[str, int] = {}
    counts = Counter(c for c, *_ in cand if c.startswith("council:"))
    for r in terr.iter_rows(named=True):
        want = number(r[col(terr, "pocet volenych")]) or 0
        obec = r[col(terr, "kod obce")]
        mine = [c for c in counts if c.startswith(f"council:{obec}:")]
        if want and len(mine) == 1:
            seats[mine[0]] = want
    by_obvod = xframe(source, "02ab")
    published = {
        "obec": sum(number(v) or 0 for v in by_obvod[col(by_obvod, "pocet obci", "konali")]),
        "councillors": sum(number(v) or 0 for v in by_obvod[col(by_obvod, "pocet zvolenych poslancov")]),
        "mayors": sum(number(v) or 0 for v in by_obvod[col(by_obvod, "zvoleny starosta")]),
    }
    return finish_rows(units, cand, vote, seats, published, [source])


def nove_full(day: date, source: str) -> NoveRows:
    """March 2022 on: polling districts, every candidate's votes."""

    def first(*names: str) -> pl.DataFrame:
        return xframe(source, *names)

    units_ = first("02bf")
    obec_c, okrsok_c = col(units_, "kod obce"), col(units_, "okrsok")
    voters_c = col(units_, "vydanych", "obalok") if has(units_, "vydanych", "obalok") else col(units_, "zucastnenych")
    units = [
        {
            "obec": r[obec_c],
            "okrsok": r[okrsok_c],
            "registered": number(r[col(units_, "zapisanych")]),
            "voters": number(r[voters_c]),
            "cast": number(r[col(units_, "odovzdanych obalok", exclude=["podiel"])]),
            "valid_council": number(r[col(units_, "platnych", "zastupitel", exclude=["podiel"])]),
            "valid_mayor": number(r[col(units_, "platnych", "starost", exclude=["podiel"])]),
        }
        for r in units_.iter_rows(named=True)
    ]
    elected_mayors = first("04d", "04")
    won_mayor = {
        (
            r[col(elected_mayors, "kod obce")],
            fold(f"{r[col(elected_mayors, 'meno', exclude=['priezvisko'])]} {r[col(elected_mayors, 'priezvisko')]}"),
        )
        for r in elected_mayors.iter_rows(named=True)
    }
    elected_councillors = first("06d", "06")
    won_council = set()
    for r in elected_councillors.iter_rows(named=True):
        who = pl.DataFrame([r]).select(_names(elected_councillors)).item()
        won_council.add(
            (
                f"council:{r[col(elected_councillors, 'kod obce')]}:{r[col(elected_councillors, 'volebny obvod')]}",
                fold(who),
            )
        )
    cand: list[tuple[str, str, str, str, str | None]] = []
    for sheet, kind in ((first("0ad", "0a"), "mayor"), (first("0bd", "0d"), "council")):
        note = col(sheet, "poznamka") if has(sheet, "poznamka") else None
        for r in sheet.iter_rows(named=True):
            obec = r[col(sheet, "kod obce")]
            contest = f"mayor:{obec}" if kind == "mayor" else f"council:{obec}:{r[col(sheet, 'volebny obvod')]}"
            name = pl.DataFrame([r]).select(_names(sheet)).item()
            status = "withdrew" if note and fold(r[note]) == "x" else None
            if status is None and (
                (contest.startswith("mayor") and (obec, fold(name)) in won_mayor)
                or (contest, fold(name)) in won_council
            ):
                status = "elected"
            cand.append(
                (
                    contest,
                    str(int(r[col(sheet, "poradie")])),
                    clean(name),
                    r[col(sheet, "politicky subjekt")] or "",
                    status,
                )
            )
    vote: list[tuple[str, str, str | None, str, int | None]] = []
    for sheet, kind in ((first("05f"), "mayor"), (first("08f"), "council")):
        for r in sheet.iter_rows(named=True):
            obec = r[col(sheet, "kod obce")]
            vo = r[col(sheet, "volebny obvod")]
            contest = f"mayor:{obec}" if kind == "mayor" else f"council:{obec}:{vo}"
            vote.append(
                (
                    contest,
                    obec,
                    r[col(sheet, "okrsok")],
                    str(int(r[col(sheet, "poradie")])),
                    number(r[col(sheet, "pocet platnych hlasov")]),
                )
            )
    terr = first("0dd", "0c")
    seats: dict[str, int] = {}
    for r in terr.iter_rows(named=True):
        want = number(r[col(terr, "pocet volenych")]) or 0
        obec = r[col(terr, "kod obce")]
        mine = sorted({c for c, *_ in cand if c.startswith(f"council:{obec}:")})
        if want and len(mine) == 1:
            seats[mine[0]] = want
    published: dict[str, int] = {}
    by_obvod = first("02ab")
    published = {
        "obec": sum(number(v) or 0 for v in by_obvod[col(by_obvod, "pocet obci", "konali")]),
        "councillors": sum(number(v) or 0 for v in by_obvod[col(by_obvod, "pocet zvolenych poslancov")]),
        "mayors": sum(number(v) or 0 for v in by_obvod[col(by_obvod, "zvoleny starosta")]),
    }
    if has_sheet(source, "01"):
        total = xbook(source)["01"][2]
        published |= {
            "registered": number(total[3]) or 0,
            "voters": number(total[4]) or 0,
            "cast": number(total[6]) or 0,
        }
    return finish_rows(units, cand, vote, seats, published, [source])


def nove_day(day: date) -> Day:
    kind, source = NOVE_DAYS[day]
    rows = (
        nove_names(day, kind, source)
        if kind in ("csv", "names")
        else nove_codes(day, source)
        if kind == "codes"
        else nove_full(day, source)
    )
    d = Day(
        str(day),
        f"The municipal by-elections of {day:%-d %B %Y}, mayors and councils",
        [],
    )
    d.method = (
        " By-elections fill a municipality's vacant mayor's office or a council that could not be completed from its "
        "substitutes. Each municipality elects as the regular elections do: the mayor by plurality and the council by "
        "block voting in its electoral districts."
    )
    d.paths += rows.paths
    d.elections.append(nove_election(day))
    meta: dict[str, Contest] = {}
    for contest_key in rows.candidates["contest"].unique().sort().to_list():
        parts = contest_key.split(":")
        meta[contest_key] = (
            mayor_contest(parts[1])
            if parts[0] == "mayor"
            else council_contest(parts[1], parts[2], rows.seats.get(contest_key))
        )
    unknown = set(rows.votes["contest"].unique()) - set(meta)
    if unknown:
        raise ValueError(f"{day}: votes for contests without candidates: {sorted(unknown)[:5]}")
    rows.units = rows.units.filter(pl.col("obec").is_not_null())
    if kind != "full":
        for c in meta.values():
            if c.body:
                c.note = (
                    "Úrad zverejňuje pre tieto voľby len hlasy zvolených poslancov, nie hlasy ostatných kandidátov."
                )
        d.accepts["accounting.votes_valid"] = (
            "the office prints only the elected councillors' votes for this day's councils, so a council that elects "
            "one councillor has one candidate's votes against all its valid ballots; the mayoral contests add up"
        )
    check_nove(day, rows)
    add_local(d, day, Local(rows.candidates, rows.votes, rows.units, meta))
    return d


NOVE_CHECKS: dict[date, str] = {}


def check_nove(day: date, rows: NoveRows) -> None:
    """Add up the municipalities and compare with the totals the office prints for the day."""
    elected = rows.candidates.filter(pl.col("status") == "elected")
    got = {
        "obec": rows.units["obec"].n_unique(),
        "mayors": elected.filter(pl.col("contest").str.starts_with("mayor:"))["contest"].n_unique(),
        "councillors": elected.filter(pl.col("contest").str.starts_with("council:")).height,
        **{name: int(rows.units[name].sum() or 0) for name in UNIT_COLUMNS},
    }
    if "okrsok" in rows.units.columns and rows.units["okrsok"].null_count() == 0:
        got["registered"] = int(rows.units["registered"].sum() or 0)
    wrong = {k: (got[k], v) for k, v in rows.published.items() if got.get(k) != v}
    if wrong:
        raise ValueError(f"{day}: totals differ from the office's (ours, theirs): {wrong}")
    NOVE_CHECKS[day] = ", ".join(f"{k} {v:,}" for k, v in sorted(rows.published.items()))


_register()
