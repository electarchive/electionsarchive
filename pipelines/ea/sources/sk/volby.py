"""Slovakia's elections from the Statistical Office of the Slovak Republic (Štatistický úrad SR).

The office publishes every national, European, regional, and municipal election and every referendum it has counted
since 1994 as open-data tables on volby.statistics.sk. The finest unit differs by year: the polling district (okrsok)
from 2010 for referendums, 2016 for the National Council, 2017 for regional elections, 2019 for the presidency and the
European Parliament, and 2022 for municipal elections; the municipality (obec) before that; and the district (okres)
for the 1998 National Council election. One package covers each election day, both rounds included.
"""

import csv
import functools
import io
import re
import unicodedata
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
    return int(round(float(s)))


def url(path: str) -> str:
    return f"{SITE}/opendata/{quote(path)}"


# Reading the open-data tables.


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250")


@functools.cache
def table(path: str) -> pl.DataFrame:
    """One open-data table as strings. The office's tables are comma, semicolon, or pipe separated, in UTF-8 or
    Windows-1250, some with a title row and a blank row before the header; headers may span lines."""
    text = _decode(raw.cached(SOURCE, url(path)).path.read_bytes())
    first = next(line for line in text.splitlines() if line.strip())
    separator = max(";|,", key=first.count)
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
    body = [
        [clean(cell) or None for cell in (row + [""] * width)[:width]] for row in rows[1:] if any(c.strip() for c in row)
    ]
    return pl.DataFrame(body, schema={name: pl.String for name in names}, orient="row")


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


FACT_SCHEMA = {"unit": pl.String, "option": pl.String, "measure": pl.Int16, "value": pl.Int64}
UNIT_SCHEMA = {"unit": pl.String, "scheme": pl.String, "obec": pl.String, "okres": pl.String, "kraj": pl.String}


def units_of(frame: pl.DataFrame, day: date, *, okrsok: str | None, obec: str, okres: str | None = None) -> pl.DataFrame:
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
    t = (lambda n: f"nrsr/{year}/NRSR2023_SK_{n}.csv") if year == 2023 else (lambda n: f"nrsr/{year}/NRSR_{year}_{n}.csv")
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
        row[col(national, "cislo")]: number_or_zero(row[col(national, "mandat")]) for row in national.iter_rows(named=True)
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
    seats = {row[col(allotted, "cislo")]: number_or_zero(row[col(allotted, "spolu")]) for row in allotted.iter_rows(named=True)}
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
        for option, value in frame.filter(pl.col("measure") == VOTES).group_by("option").agg(pl.col("value").sum()).iter_rows():
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
            t = lambda n, r=round_number: f"pre/{year}/{r}.kolo/PREZ2024_kolo{r}_SK_{n}.csv"  # noqa: E731
        else:
            t = lambda n, r=round_number: f"pre/{year}/{r}.kolo/PRE_{year}_KOLO{r}_{n}.csv"  # noqa: E731
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
                    VOTERS: [col(units, "vydanych obalok")] if has(units, "vydanych obalok") else [col(units, "zucastn")],
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
        for option, measure, value in (
            frame.group_by("option", "measure").agg(pl.col("value").sum()).iter_rows()
        ):
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
    t = lambda n: f"ref/{year}/REF{year}_SK_{n}.csv"  # noqa: E731
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
    d.units.append(units_of(units, start, okrsok=okrsok, obec=obec, okres=None if year == 2023 else col(units, "kod okresu")))
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
        "The National Council's 150 seats are allocated by the Hagenbach-Bischoff quota (the republic electoral number, "
        "valid votes for qualifying lists divided by 151) and the largest remainders, among the lists that pass the "
        "threshold: 5% for a party, 7% for a coalition of two or three parties, and 10% for four or more (the 1994 "
        "election had four constituencies with a national threshold). Preference votes move a candidate up the list "
        "once they reach 3% of the list's votes (10% before 2006).",
    ),
    "ep": (
        date(2004, 1, 1),
        {
            "formula": "hagenbach_bischoff",
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
        },
        "Slovakia elects its members of the European Parliament in one national constituency by the Hagenbach-Bischoff "
        "quota and the largest remainders, among the lists with at least 5% of the valid votes.",
    ),
    "president": (
        date(1999, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        "Direct election since 1999: a candidate who wins the votes of more than half of all registered voters in the "
        "first round is elected; otherwise the two leading candidates meet in a second round two weeks later, which the "
        "candidate with more valid votes wins.",
    ),
    "referendum": (
        date(1993, 1, 1),
        {"formula": "referendum"},
        "A national referendum is valid when more than half of the registered voters take part, and a proposal carries "
        "when more than half of the voters who took part vote yes.",
    ),
    "vuc-chair-2001": (
        date(2001, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        "The chair of a self-governing region was elected in two rounds until 2013: a candidate with a majority of the "
        "valid votes was elected in the first round, otherwise the two leading candidates met two weeks later.",
    ),
    "vuc-chair-2017": (
        date(2017, 1, 1),
        {"formula": "fptp"},
        "Since 2017 the chair of a self-governing region is elected in one round by plurality.",
    ),
    "vuc-council": (
        date(2001, 1, 1),
        {"formula": "block"},
        "Regional councillors are elected in multi-member electoral districts: each voter may vote for as many "
        "candidates as the district elects, and the candidates with the most votes win.",
    ),
    "mayor": (
        date(1990, 1, 1),
        {"formula": "fptp"},
        "Mayors (starosta, primátor) are elected in one round by plurality.",
    ),
    "town-council": (
        date(1990, 1, 1),
        {"formula": "block"},
        "Municipal councillors are elected in one or more multi-member electoral districts: each voter may vote for as "
        "many candidates as the district elects, and the candidates with the most votes win.",
    ),
}


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
    p.add("office", id=PRESIDENCY, polity_id=SLOVAKIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Slovak Republic", "en", "official")
    p.name(PRESIDENCY, "Prezident Slovenskej republiky", "sk", "native", False)
    for name, (since, rules, method) in SYSTEMS.items():
        id = system(name)
        p.add("electoral_system", id=id, polity_id=SLOVAKIA, valid_from=since, valid_to=None, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    known = places()
    bodies: set[str] = set()
    offices: set[str] = set()
    labels: dict[str, tuple[date, str, str | None]] = {}
    for d in days():
        for c in d.contests:
            if c.body and c.body != NRSR_BODY:
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
    for path in dict.fromkeys(d.paths):
        if path in TERRITORY:
            p.uses(raw.cached(SOURCE, url(path)))
        else:
            p.artifact(raw.cached(SOURCE, url(path)), VOLBY_SOURCE)
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
    units = pl.concat([u.select(list(UNIT_SCHEMA)).cast(UNIT_SCHEMA) for u in d.units]).unique("unit", keep="first")
    unit_ids = {code: key("unit", code) for code in units["unit"]}
    p.frame(
        "reporting_unit",
        units.select(
            id=pl.col("unit").replace_strict(unit_ids),
            polity_id=pl.lit(SLOVAKIA),
            scheme_code="scheme",
            code="unit",
            area_relation=pl.when(pl.col("obec") == ABROAD).then(pl.lit("unlocated")).otherwise(pl.lit("within")),
            area_id=pl.when(pl.col("obec") == ABROAD)
            .then(pl.lit(None, pl.String))
            .otherwise(
                pl.struct("obec", "okres", "kraj").map_elements(
                    lambda r: unit_area(r["obec"], r["okres"], r["kraj"]), pl.String
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
            completeness=1.0,
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
            caveats[f"declared:the national roll includes {value - got:,} voters on the list for the postal vote from "
                    f"abroad, whose polling districts (code {ABROAD}) print no roll"] += 1
            continue
        reason = EXPLAINED.get((d.key, c.key, measure))
        if reason is None:
            raise ValueError(f"{d.key} {c.key}: declared {option} measure {measure} is {value:,}, the units sum to {got}")
        caveats[f"declared:{reason}"] += 1


def _accept(p: Package, d: Day, caveats: Counter[str]) -> None:
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


_register()


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
    for path in files():
        raw.fetch(SOURCE, url(path), suffix=".csv")


def parse() -> None:
    geo().write()
    registry().write()
    for d in days():
        build(d).write()


def emit() -> None:
    package.run("load", packages())
