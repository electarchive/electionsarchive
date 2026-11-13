from collections import Counter, defaultdict

import polars as pl

from ea import raw
from ea.sources.es import infoelectoral
from ea.store import DATA

SOURCE = "es.cabildos"
PARSED = DATA / "parsed" / SOURCE
KIND = "06"
THRESHOLD = 0.05
Row = dict[str, str | int]


def fetch() -> None:
    infoelectoral.fetch()


def _island(row: Row) -> str:
    return f"{row['province']}{row['district']}"


def _dhondt(votes: dict[int, int], valid: int, seats: int) -> Counter[int]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def _processes() -> list[str]:
    return [
        process
        for process in infoelectoral.processes(KIND)
        if f"{infoelectoral.APLIEXTR}{KIND}{process}_MESA.zip" in raw.keys(infoelectoral.SOURCE)
    ]


def _islands_of(processes: list[str]) -> dict[tuple[str | int, str | int], str]:
    return {
        (row["province"], row["municipality"]): _island(row)
        for process in processes
        for row in infoelectoral.records(KIND, process, "MESA", "05")
        if row["municipal_district"] == "99"
    }


def _ties(votes: list[Row], islands: list[Row], units: list[Row], tallies: list[Row]) -> bool:
    lists: Counter[tuple[str | int, str | int]] = Counter()
    for row in tallies:
        lists[row["island"], row["list"]] += int(row["votes"])
    totals: dict[str | int, Counter[str]] = defaultdict(Counter)
    for row in units:
        for field in ("blank", "invalid", "list_votes"):
            totals[row["island"]][field] += int(row[field])
    return (
        bool(tallies)
        and all(lists[row["island"], row["list"]] == row["votes"] for row in votes)
        and all(
            not units or totals[row["island"]][field] == row[field]
            for row in islands
            for field in ("blank", "invalid", "list_votes")
        )
    )


def _process(process: str, islands_of: dict[tuple[str | int, str | int], str]) -> dict[str, list[Row]]:
    year, month = int(process[:4]), int(process[4:])
    stamp: Row = {"year": year, "month": month}
    read = lambda number: infoelectoral.records(KIND, process, "MESA", number)
    lists = {row["list"]: row for row in read("03")}
    islands = [{**stamp, "island": _island(row), **row} for row in read("07") if row["district"] != "9"]
    names = {row["island"] for row in islands}
    votes = [
        {
            **stamp,
            "island": _island(row),
            "list": row["list"],
            "abbrev": lists[row["list"]]["abbrev"],
            "name": lists[row["list"]]["name"],
            "votes": row["votes"],
            "seats": row["seats"],
        }
        for row in read("08")
        if _island(row) in names
    ]
    candidates = [{**stamp, "island": _island(row), **row} for row in read("04")]
    located = lambda rows: [
        {**stamp, "island": islands_of[row["province"], row["municipality"]], **row} for row in rows
    ]
    municipalities = [row for row in located(read("05")) if row["municipal_district"] == "99"]
    municipality_votes = [row for row in located(read("06")) if row["municipal_district"] == "99"]
    mesas, mesa_votes = located(read("09")), located(read("10"))
    _check(process, islands, votes, candidates)
    election: Row = {
        **stamp,
        "municipalities_tie": _ties(votes, islands, municipalities, municipality_votes),
        "mesas_tie": _ties(votes, islands, mesas, mesa_votes),
    }
    return {
        "elections": [election],
        "islands": islands,
        "lists": votes,
        "candidates": candidates,
        "municipalities": municipalities,
        "municipality_votes": municipality_votes,
        "mesas": mesas,
        "mesa_votes": mesa_votes,
    }


def _check(process: str, islands: list[Row], votes: list[Row], candidates: list[Row]) -> None:
    problems = []
    elected = Counter((row["island"], row["list"]) for row in candidates if row["elected"] == "S")
    for island in islands:
        key, seats = island["island"], int(island["seats"])
        rows = [row for row in votes if row["island"] == key]
        if sum(int(row["seats"]) for row in rows) != seats:
            problems.append(f"{key}: list seats do not sum to {seats}")
        if sum(int(row["votes"]) for row in rows) != island["list_votes"]:
            problems.append(f"{key}: list votes do not sum to {island['list_votes']}")
        problems += [
            f"{key} list {row['list']}: {elected[key, row['list']]} elected against {row['seats']}"
            for row in rows
            if candidates and elected[key, row["list"]] != row["seats"]
        ]
        won = _dhondt(
            {int(row["list"]): int(row["votes"]) for row in rows},
            int(island["list_votes"]) + int(island["blank"]),
            seats,
        )
        if any(won[int(row["list"])] != row["seats"] for row in rows):
            print(f"{process} {key}: D'Hondt with a 5% threshold does not give the declared seats")
    if problems:
        raise ValueError(f"{process}: " + "; ".join(problems[:20]))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    processes = _processes()
    islands_of = _islands_of(processes)
    tables: dict[str, list[Row]] = defaultdict(list)
    for process in processes:
        for name, rows in _process(process, islands_of).items():
            tables[name] += rows
    for name, rows in tables.items():
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {len(rows)} rows")
    print(pl.DataFrame(tables["elections"]))
