"""Polish gmina council elections of 19 June 1994.

The Krajowe Biuro Wyborcze publishes two files. One workbook holds the totals of each gmina: its population, the type of
council, seats, candidates, constituencies, polling districts, registered voters, ballots, invalid and valid votes, and
the valid ballots on which no candidate was marked. The other is a zip of one workbook for each voivodeship with every
candidate of every constituency: the constituency's seats, registered voters, ballots, and valid ballots, then the
candidate's list, position, committee, age, occupation, residence, supporting organization, votes, and whether elected.
Votes are published only as totals for the constituency, so each constituency is one aggregate reporting unit.

Councils were elected in gminas of more than 40,000 inhabitants from open lists in multi-member constituencies, with
seats shared by the modified Sainte-Laguë method, and in every other gmina in single-member constituencies. A voter
marked one candidate; ballots with no mark were still valid.
"""

import zipfile
from collections import Counter
from datetime import date
from uuid import uuid5

import polars as pl
import xlrd

from ea import package
from ea.names import display
from ea.package import Package
from ea.sources.pl import kbw
from ea.sources.pl.kbw import BALLOTS, OBWOD, PREFERENCE, REGISTERED, VALID, clean, slug
from ea.sources.pl.kbw_gmina import _name
from ea.sources.pl.kbw_gmina_2010_2014 import _frame

SOURCE = "pl.kbw_gmina_1994"
POLAND = kbw.POLAND
VOTES = 1
DAY = date(1994, 6, 19)
TOTALS = "1994/samo1994/samo1994gm.xls"
CANDIDATES = "1994/samo1994/samo1994kand.zip"
FILES = [TOTALS, CANDIDATES]
PACKAGE = f"{SOURCE}/{DAY}"
PATH = f"pl/{DAY}-gmina-councils"
FIRST_DIVISOR = 1.4
LIST_RULES: dict[str, object] = {
    "formula": "modified_sainte_lague",
    "firstDivisor": FIRST_DIVISOR,
    "lists": {"order": "open", "vacancy": "next_on_list"},
}
SINGLE_RULES: dict[str, object] = {"formula": "fptp"}
GENERIC = ("wyborc", "komitet", "lokaln", "obywatel", "nasz")
SHARED = 20


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [PACKAGE]


def fetch() -> None:
    for path in FILES:
        kbw.fetch_one(path)


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def teryt(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strip_chars().str.replace(r"\.0$", "").str.zfill(7)


def _number(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Float64).cast(pl.Int64)


def _candidates() -> pl.DataFrame:
    """Every candidate: gmina, constituency, its accounting, list, position, committee, votes, and whether elected."""
    archive = zipfile.ZipFile(kbw.cached(CANDIDATES).path)
    frame = pl.concat([_frame(archive.read(name)) for name in sorted(archive.namelist())])
    # The workbooks repeat two headers: the list's candidates and seats follow the list's label.
    return frame.select(
        gmina=teryt("TERYT"),
        place=pl.col("Gmina").map_elements(clean, return_dtype=pl.String),
        kind="Typ",
        okr=_number("Nr okr."),
        seats=_number("L. mand."),
        registered=_number("Upr."),
        ballots=_number("Odd."),
        valid=_number("Gł. ważn."),
        nochoice=_number("Gł. bez wyb."),
        list=_number("Nr listy"),
        pos=_number("Poz."),
        committee=pl.col("Oznaczenie").map_elements(clean, return_dtype=pl.String),
        name=pl.concat_str(["Nazwisko", "Imiona"], separator=" ", ignore_nulls=True).map_elements(
            clean, return_dtype=pl.String
        ),
        sex=pl.col("Płeć"),
        votes=_number("Gł."),
        elected=pl.col("Mandat") == "T",
    ).with_columns(dup=pl.int_range(pl.len()).over("gmina", "okr", "list", "pos"))


def _totals() -> pl.DataFrame:
    sheet = xlrd.open_workbook(str(kbw.cached(TOTALS).path)).sheet_by_index(0)
    head = [clean(cell) for cell in sheet.row_values(0)]
    rows = [sheet.row_values(row) for row in range(1, sheet.nrows)]
    frame = pl.DataFrame(rows, schema=head, orient="row", infer_schema_length=None)
    return frame.select(
        gmina=teryt("TERYT"),
        seats=pl.col("L. mand.").cast(pl.Int64),
        candidates=pl.col("L. kand.").cast(pl.Int64),
        constituencies=pl.col("L. okr.").cast(pl.Int64),
        registered=pl.col("L. upr.").cast(pl.Int64),
        ballots=pl.col("Odd.").cast(pl.Int64),
        invalid=pl.col("Niew.").cast(pl.Int64),
        valid=pl.col("Ważne").cast(pl.Int64),
        nochoice=pl.col("Ważne bez dokonania wyboru").cast(pl.Int64),
    )


def _constituencies(candidates: pl.DataFrame) -> pl.DataFrame:
    keys = ["gmina", "okr"]
    frame = candidates.group_by(keys).agg(
        pl.col("place").first(),
        pl.col("kind").first(),
        pl.col("seats").first(),
        pl.col("registered").first(),
        pl.col("ballots").first(),
        pl.col("valid").first(),
        pl.col("nochoice").first(),
        pl.col("elected").sum().alias("elected"),
        pl.col("votes").sum().alias("cast"),
        pl.col("seats").n_unique().alias("seat_counts"),
        pl.col("valid").n_unique().alias("valid_counts"),
    )
    if frame.filter((pl.col("seat_counts") > 1) | (pl.col("valid_counts") > 1)).height:
        raise ValueError("constituency figures that differ between candidate rows")
    if frame.filter(pl.col("seats") != pl.col("elected")).height:
        raise ValueError("constituencies whose elected candidates differ from their seats")
    if frame.filter(pl.col("valid") - pl.col("cast") != pl.col("nochoice")).height:
        raise ValueError("constituencies whose valid ballots are not the candidates' votes plus the unmarked ballots")
    if frame.filter((pl.col("kind") == "W") & (pl.col("seats") != 1)).height:
        raise ValueError("single-member constituencies with several seats")
    return frame.with_columns(mode=pl.when(pl.col("kind") == "P").then(pl.lit("list")).otherwise(pl.lit("single")))


def _sainte_lague(votes: dict[int, int], seats: int) -> dict[int, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(
            sorted(votes),
            key=lambda n: (votes[n] / (FIRST_DIVISOR if won[n] == 0 else 2 * won[n] + 1), -n),
        )
        won[best] += 1
    return {n: x for n, x in won.items() if x}


class Data:
    def __init__(self) -> None:
        self.candidates = _candidates()
        self.constituencies = _constituencies(self.candidates)
        self.totals = _totals()
        self.checked = self.check()

    def check(self) -> dict[str, object]:
        """Compare the constituencies with the authority's totals for each gmina; the figures must agree."""
        cons, totals = self.constituencies, self.totals
        sums = cons.group_by("gmina").agg(
            pl.col("seats").sum().alias("seats"),
            pl.len().alias("constituencies"),
            pl.col("registered").sum().alias("registered"),
            pl.col("ballots").sum().alias("ballots"),
            pl.col("valid").sum().alias("valid"),
            pl.col("nochoice").sum().alias("nochoice"),
            pl.col("kind").first(),
        )
        joined = totals.join(sums, on="gmina", how="full", suffix="_found", coalesce=True)
        if joined.filter(pl.col("seats").is_null() | pl.col("seats_found").is_null()).height:
            raise ValueError("gminas in only one of the two files")
        for column in ("registered", "ballots", "valid", "nochoice"):
            if joined.filter(pl.col(column) != pl.col(f"{column}_found")).height:
                raise ValueError(f"gminas whose constituency {column} differ from the gmina totals")
        if totals.filter(pl.col("invalid") + pl.col("valid") != pl.col("ballots")).height:
            raise ValueError("gminas whose valid and invalid ballots do not add up")
        short = joined.filter(
            (pl.col("seats") != pl.col("seats_found")) | (pl.col("constituencies") != pl.col("constituencies_found"))
        )
        lists = self.candidates.filter(pl.col("kind") == "P")
        exact = total = 0
        for (_, _), part in lists.group_by("gmina", "okr"):
            votes = part.group_by("list").agg(pl.col("votes").sum())
            declared = Counter(part.filter(pl.col("elected"))["list"].to_list())
            total += 1
            exact += _sainte_lague(
                dict(zip(votes["list"], votes["votes"], strict=True)), int(part["seats"][0])
            ) == dict(declared)
        print(
            f"1994: {short.height} gminas with seats or constituencies missing from the candidate file; "
            f"modified Sainte-Laguë reproduces {exact:,} of {total:,} list constituencies",
            flush=True,
        )
        return {"short": short, "exact": exact, "total": total, "totals": totals}


SYSTEM_NOTES = {
    "list": (
        "The council of a gmina with more than 40,000 inhabitants was elected in multi-member constituencies from "
        "open lists that election committees registered. A voter marked one candidate. Seats went to the lists by the "
        "modified Sainte-Laguë method (first divisor 1.4) on the sum of their candidates' votes, and within each list "
        "to the candidates with the most votes. The method reproduces the declared seats in %s of the %s "
        "constituencies; the rest follow rules the files do not describe, so the declared seats are stored as printed."
    ),
    "single": (
        "The council of every other gmina was elected in single-member constituencies. A voter marked one candidate, "
        "and the candidate with the most votes won. A candidate without opponents was still voted on."
    ),
}
METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze as totals for the "
    "constituency: registered voters, ballots taken from the box, valid ballots, and each candidate's votes. %s "
    "Valid ballots include the ballots on which no candidate was marked. Elected members are from the candidate file."
)
LIST_METHOD = "A voter marks one candidate, so a list's votes are the sum of its candidates' votes."
SINGLE_METHOD = "A voter marks one candidate."


def english(place: str, gmina: str) -> str:
    return f"{place} {'city' if gmina.endswith('1') else 'gmina'} council"


def _scope(committee: str, gmina: str, shared: set[str]) -> str:
    """Committees printed in many gminas are one party; committees of voters and the rest belong to one gmina."""
    label = slug(committee)
    if label in shared and not any(word in committee.lower() for word in GENERIC):
        return key("party", label)
    return key("party", label, gmina)


def build() -> Package:
    data = Data()
    cands, cons = data.candidates, data.constituencies
    exact, total = int(str(data.checked["exact"])), int(str(data.checked["total"]))
    short = data.checked["short"]
    assert isinstance(short, pl.DataFrame)
    p = Package(
        PACKAGE,
        SOURCE,
        "The 1994 gmina council elections in Poland by constituency from the Krajowe Biuro Wyborcze",
        depends=["pl.kbw/registry"],
    )
    p.external |= {POLAND, OBWOD}
    for path in FILES:
        p.artifact(kbw.cached(path), kbw.KBW)
    election = key("election")
    p.add("election", id=election, polity_id=POLAND, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "1994 gmina council elections in Poland", "en", "official")
    p.name(election, "Wybory do rad gmin 1994", "pl", "native", False)
    p.add("slug", path=PATH, entity_id=election, is_current=True)
    systems = {mode: key("electoral_system", mode) for mode in cons["mode"].unique().sort()}
    titles = {"list": "Gmina council modified Sainte-Laguë system", "single": "Gmina council single-member system"}
    rules = {"list": LIST_RULES, "single": SINGLE_RULES}
    for mode, id in systems.items():
        p.add(
            "electoral_system",
            id=id,
            polity_id=POLAND,
            valid_from=date(1994, 1, 1),
            valid_to=date(1994, 12, 31),
            rules=rules[mode],
            rules_version=1,
        )
        p.name(id, titles[mode], "en", "official")
        note = SYSTEM_NOTES[mode] % (f"{exact:,}", f"{total:,}") if mode == "list" else SYSTEM_NOTES[mode]
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    gaps = cons.filter(pl.col("nochoice") > 0).height
    p.accept(
        "accounting.votes_valid",
        f"In {gaps:,} of the {cons.height:,} constituencies the published valid ballots exceed the sum of the "
        "candidates' votes by exactly the published count of valid ballots on which no candidate was marked.",
    )
    labels = {
        row["gmina"]: english(row["place"], row["gmina"])
        for row in cons.group_by("gmina").agg(pl.col("place").first()).iter_rows(named=True)
    }
    shared = set(
        cands.filter(pl.col("kind") == "P")
        .with_columns(label=pl.col("committee").map_elements(slug, return_dtype=pl.String))
        .group_by("label")
        .agg(pl.col("gmina").n_unique().alias("n"))
        .filter(pl.col("n") >= SHARED)["label"]
    )
    names: list[dict[str, object]] = []
    for gmina, text in sorted(labels.items()):
        body = key("body", gmina)
        p.add("body", id=body, polity_id=POLAND, kind="council")
        names.append(_name(p, body, text, "en", "official", True))
    rows: dict[str, list[dict[str, object]]] = {
        name: []
        for name in (
            "contest",
            "slug",
            "result_set",
            "note",
            "person",
            "candidacy",
            "outcome",
            "declared",
            "party",
            "contest_option",
            "reporting_unit",
            "result_value",
        )
    }
    parties: set[str] = set()

    def party(committee: str, gmina: str) -> str:
        id = _scope(committee, gmina, shared)
        if id not in parties:
            parties.add(id)
            rows["party"].append({"id": id, "kind": "list_vehicle", "polity_id": POLAND})
            names.append(_name(p, id, committee, "pl", "official", True))
        return id

    def fact(result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        rows["result_value"].append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": value,
            }
        )

    by_okr: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in cands.sort("gmina", "okr", "list", "pos", "dup").iter_rows(named=True):
        by_okr.setdefault((str(row["gmina"]), int(str(row["okr"]))), []).append(row)
    for row in cons.sort("gmina", "okr").iter_rows(named=True):
        gmina, okr, mode = str(row["gmina"]), int(row["okr"]), str(row["mode"])
        tag = (gmina, okr)
        listed = mode == "list"
        contest = key("contest", *tag)
        result_set = key("result_set", *tag)
        unit = key("unit", *tag)
        rows["contest"].append(
            {
                "id": contest,
                "election_id": election,
                "kind": "list_allocation" if listed else "body_seats",
                "body_id": key("body", gmina),
                "magnitude": int(row["seats"]),
                "round_number": 1,
                "electoral_system_id": systems[mode],
                "default_result_kind": "counted",
            }
        )
        names.append(_name(p, contest, f"{labels[gmina]}, constituency {okr}", "en", "official", True))
        rows["slug"].append(
            {"path": f"{PATH}/{gmina}-{slug(labels[gmina])}/{okr}", "entity_id": contest, "is_current": True}
        )
        rows["result_set"].append(
            {
                "id": result_set,
                "election_id": election,
                "contest_id": contest,
                "stage": "official",
                "kind": "counted",
                "source_id": kbw.KBW,
                "status": "active",
                "is_default": True,
                "completeness": 1.0,
            }
        )
        rows["note"].append(
            {
                "id": key("note", result_set),
                "entity_id": result_set,
                "kind": "methodology",
                "body_md": METHOD % (LIST_METHOD if listed else SINGLE_METHOD),
                "lang": "en",
            }
        )
        rows["reporting_unit"].append(
            {
                "id": unit,
                "polity_id": POLAND,
                "scheme_code": OBWOD,
                "code": f"{DAY}:{gmina}:{okr}:okreg",
                "area_relation": "unlocated",
                "kind": "aggregate",
            }
        )
        names.append(_name(p, unit, f"{labels[gmina]}, constituency {okr}", "en", "official", True))
        won: Counter[int] = Counter()
        sums: Counter[int] = Counter()
        list_options: dict[int, str] = {}
        for slot, member in enumerate(by_okr[tag], 1):
            number, pos = int(str(member["list"])), int(str(member["pos"]))
            committee = str(member["committee"])
            if listed and number not in list_options:
                option = list_options[number] = key("option", *tag, "list", number)
                rows["contest_option"].append(
                    {
                        "id": option,
                        "contest_id": contest,
                        "kind": "list",
                        "party_id": party(committee, gmina) if committee else None,
                        "ballot_position": number,
                        "ballot_label": committee or None,
                    }
                )
            ptag = (*tag, number, pos, member["dup"])
            person = key("person", *ptag)
            rows["person"].append({"id": person, "gender": {"M": "male", "K": "female"}[str(member["sex"])]})
            shown = display(str(member["name"]), "pl")
            names.append(_name(p, person, shown, "pl", "official", True))
            candidate_option = key("option", *ptag)
            rows["contest_option"].append(
                {
                    "id": candidate_option,
                    "contest_id": contest,
                    "kind": "candidate",
                    "parent_option_id": list_options[number] if listed else None,
                    "ballot_position": pos if listed else slot,
                    "ballot_label": shown,
                }
            )
            elected_option = list_options[number] if listed else candidate_option
            candidacy = key("candidacy", *ptag)
            rows["candidacy"].append(
                {
                    "id": candidacy,
                    "person_id": person,
                    "option_id": elected_option,
                    "role": "list_member" if listed else "head",
                    "list_position": pos if listed else None,
                    "ballot_name": str(member["name"]),
                }
            )
            votes = int(str(member["votes"]))
            sums[number] += votes
            if member["elected"]:
                won[number] += 1
                rows["outcome"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": elected_option,
                        "candidacy_id": candidacy if listed else None,
                        "kind": "elected",
                    }
                )
            rows["declared"].append(
                {
                    "result_set_id": result_set,
                    "option_id": candidate_option,
                    "measure": PREFERENCE if listed else VOTES,
                    "value": votes,
                }
            )
            fact(result_set, unit, candidate_option, PREFERENCE if listed else VOTES, votes)
        if listed:
            for number, seats in sorted(won.items()):
                rows["outcome"].append(
                    {"result_set_id": result_set, "option_id": list_options[number], "kind": "won", "seats": seats}
                )
            for number, option in list_options.items():
                fact(result_set, unit, option, VOTES, sums[number])
        rows["declared"].append(
            {"result_set_id": result_set, "option_id": None, "measure": VALID, "value": row["valid"]}
        )
        fact(result_set, unit, None, REGISTERED, int(row["registered"]))
        fact(result_set, unit, None, BALLOTS, int(row["ballots"]))
        fact(result_set, unit, None, VALID, int(row["valid"]))
    p.frame("name", pl.DataFrame(names))
    for table, items in rows.items():
        if items:
            p.frame(table, pl.DataFrame(items, infer_schema_length=None))
    return p
