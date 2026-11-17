"""Polish gmina and city council elections of 7 April 2024, by constituency and polling district.

The Krajowe Biuro Wyborcze publishes one protocol file per voivodeship for the councils of gminas with more than 20,000
inhabitants (list elections by D'Hondt in constituencies of five to eight seats) and for the smaller gminas (single-member
constituencies, won by plurality), a candidate file for each group, and the constituencies. Every protocol row is one
polling district (obwód) in one constituency: in the small gminas a polling district often serves several constituencies,
each with its own ballot, so a polling district appears once in each.
"""

import io
import re
import zipfile
from collections import Counter
from datetime import date
from itertools import batched
from uuid import uuid5

import polars as pl

from ea import package
from ea.names import display
from ea.package import Package
from ea.sources.pl import kbw
from ea.sources.pl.kbw import BALLOTS, INVALID, OBWOD, PREFERENCE, REGISTERED, VALID, VOTERS, clean, count, slug

SOURCE = "pl.kbw_gmina"
DAY = date(2024, 4, 7)
POLAND = kbw.POLAND
VOTES = 1
FOLDER = "2024/samorzad/"
LARGE = FOLDER + "protokoly_po_obwodach_rady_gmin_powyzej_20k_csv.zip"
SMALL = FOLDER + "protokoly_po_obwodach_rady_gmin_do_20k_csv.zip"
LARGE_CANDIDATES = FOLDER + "kandydaci_rady_gmin_powyzej_20k_csv.zip"
SMALL_CANDIDATES = FOLDER + "kandydaci_rady_gmin_do_20k_xlsx.zip"
CONSTITUENCIES = FOLDER + "okregi_rady_gmin_csv.zip"
DISTRICTS = FOLDER + "obwody_glosowania_csv.zip"
FILES = [LARGE, SMALL, LARGE_CANDIDATES, SMALL_CANDIDATES, CONSTITUENCIES, DISTRICTS]
CHUNK = 400


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def fetch() -> None:
    for path in FILES:
        kbw.fetch_one(path)


def teryt(column: str | pl.Expr) -> pl.Expr:
    expr = pl.col(column) if isinstance(column, str) else column
    return expr.cast(pl.String).str.strip_chars().str.replace(r"\.0$", "").str.zfill(6)


def _members(path: str) -> list[tuple[str, bytes]]:
    archive = zipfile.ZipFile(kbw.cached(path).path)
    return [(name, archive.read(name)) for name in sorted(archive.namelist()) if not name.endswith("/")]


def _protocol(data: bytes) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read one voivodeship's protocol file as (accounting by polling district and constituency, votes in long form)."""
    raw = pl.read_csv(
        io.StringIO(kbw.text(data)),
        separator=";",
        has_header=False,
        infer_schema_length=0,
        truncate_ragged_lines=True,
        quote_char='"',
    )
    header = [clean(value) for value in raw.row(0)]
    body = raw.slice(1)
    body.columns = [f"c{i}" for i in range(len(header))]

    def at(prefix: str, exact: bool = False) -> str:
        found = [i for i, name in enumerate(header) if (name == prefix if exact else name.startswith(prefix))]
        if len(found) != 1:
            raise ValueError(f"{len(found)} columns start with {prefix!r}")
        return f"c{found[0]}"

    keys = body.select(
        gmina=teryt(at("Teryt Gminy")),
        commission=pl.col(at("Nr komisji")).cast(pl.Int64),
        okr=pl.col(at("Nr okręgu")).cast(pl.Int64),
    )
    accounting = body.with_columns(
        gmina=keys["gmina"],
        commission=keys["commission"],
        okr=keys["okr"],
        registered=count(at("Liczba wyborców uprawnionych")),
        voters=count(at("Liczba wyborców, którym wydano karty do głosowania w lokalu")),
        ballots=count(at("Liczba kart wyjętych z urny")),
        invalid_cards=count(at("Liczba kart nieważnych")),
        invalid_votes=count(at("Liczba głosów nieważnych")),
        valid=count(at("Liczba głosów ważnych oddanych łącznie")),
    ).select("gmina", "commission", "okr", "registered", "voters", "ballots", "invalid_cards", "invalid_votes", "valid")
    pattern = re.compile(r"^Głosy na (listę|kandydata) nr (\d+)(?: na liście nr (\d+))? - (.*)$")
    columns: list[tuple[str, str, int, int | None, str, str]] = []
    for i, name in enumerate(header):
        match = pattern.match(name)
        if match is None:
            continue
        label, _, registrar = match.group(4).partition(" zarejestrowanego przez ")
        label, registrar = clean(label), clean(registrar)
        if match.group(1) == "listę":
            columns.append((f"c{i}", "list", int(match.group(2)), None, label, registrar))
        elif match.group(3) is not None:
            columns.append((f"c{i}", "candidate", int(match.group(3)), int(match.group(2)), label, registrar))
        else:
            columns.append((f"c{i}", "candidate", 0, int(match.group(2)), label, registrar))
    pieces: list[pl.DataFrame] = []
    indexed = keys.with_row_index("row")
    for batch in batched(columns, CHUNK):
        names = [column for column, *_ in batch]
        long = (
            body.select(names)
            .with_row_index("row")
            .unpivot(index="row", on=names, variable_name="column", value_name="text")
            .drop_nulls("text")
            .filter(pl.col("text").str.strip_chars() != "")
        )
        pieces.append(long)
    long = (
        pl.concat(pieces) if pieces else pl.DataFrame(schema={"row": pl.UInt32, "column": pl.String, "text": pl.String})
    )
    meta = pl.DataFrame(
        columns,
        schema={
            "column": pl.String,
            "kind": pl.String,
            "list": pl.Int64,
            "pos": pl.Int64,
            "label": pl.String,
            "registrar": pl.String,
        },
        orient="row",
    )
    votes = (
        long.join(meta, on="column")
        .join(indexed, on="row")
        .select("gmina", "commission", "okr", "kind", "list", "pos", "label", "registrar", value=count("text"))
    )
    return accounting, votes


def _candidates() -> pl.DataFrame:
    """Every candidate with the constituency, list (0 in the single-member constituencies), and position."""
    [(_, data)] = _members(LARGE_CANDIDATES)
    large = pl.read_csv(io.StringIO(kbw.text(data)), separator=";", infer_schema_length=0)
    large = large.rename({column: clean(column) for column in large.columns})
    big = large.select(
        gmina=teryt("TERYT Gminy"),
        council=pl.col("Rada"),
        okr=pl.col("Nr okręgu").cast(pl.Int64),
        list=pl.col("Nr listy").cast(pl.Int64),
        pos=pl.col("Pozycja na liście").cast(pl.Int64),
        name=pl.col("Nazwisko i imiona"),
        committee=pl.col("Nazwa komitetu"),
        short=pl.col("Skrót nazwy komitetu"),
        sex=pl.col("Płeć"),
        votes=count("Liczba głosów"),
        valid=count("Liczba głosów ważnych oddanych w okręgu"),
        elected=pl.col("Czy uzyskał mandat") == "Tak",
    )
    [(_, data)] = _members(SMALL_CANDIDATES)
    small = kbw.sheet(data)
    small = small.select(
        gmina=teryt("TERYT Gminy"),
        council=pl.col("Rada"),
        okr=pl.col("Nr okręgu").cast(pl.Int64),
        list=pl.lit(0, pl.Int64),
        pos=pl.col("Numer na karcie do głosowania").cast(pl.Int64),
        name=pl.col("Nazwisko i imiona"),
        committee=pl.col("Nazwa komitetu"),
        short=pl.col("Skrót nazwy komitetu"),
        sex=pl.col("Płeć"),
        votes=count("Liczba głosów"),
        valid=count("Liczba głosów ważnych oddanych w okręgu"),
        elected=pl.col("Czy uzyskał mandat") == "Tak",
    )
    return pl.concat([big, small]).with_columns(
        pl.col("name", "committee", "short", "council").map_elements(clean, return_dtype=pl.String)
    )


def _constituencies() -> pl.DataFrame:
    [(_, data)] = _members(CONSTITUENCIES)
    frame = pl.read_csv(io.StringIO(kbw.text(data)), separator=";", infer_schema_length=0)
    return frame.select(
        gmina=teryt("TERYT Gminy"),
        council=pl.col("Wybierany organ").map_elements(clean, return_dtype=pl.String),
        place=pl.col("Gmina").map_elements(clean, return_dtype=pl.String),
        okr=pl.col("Numer okręgu").cast(pl.Int64),
        seats=pl.col("Liczba mandatów").cast(pl.Int64),
        lists=pl.col("Liczba list").cast(pl.Int64),
        candidates=pl.col("Liczba kandydatów").cast(pl.Int64),
        voted=pl.col("Sposób wyborów w okręgu") == "Przeprowadza się głosowanie",
        registered=count("Wyborcy"),
    )


def _districts() -> pl.DataFrame:
    [(_, data)] = _members(DISTRICTS)
    frame = pl.read_csv(io.StringIO(kbw.text(data)), separator=";", infer_schema_length=0)
    return frame.select(
        unit=teryt("TERYT gminy"),
        commission=pl.col("Numer").cast(pl.Int64),
        seat=pl.col("Pełna siedziba").map_elements(clean, return_dtype=pl.String),
        kind=pl.when(pl.col("Typ obwodu").str.contains("zakład karny|areszt"))
        .then(pl.lit("prison"))
        .otherwise(pl.lit("standard")),
    )


def council_of(gmina: pl.Expr) -> pl.Expr:
    """The gmina whose council a protocol row belongs to: Warsaw's districts all vote for the one city council."""
    return pl.when(gmina.str.starts_with("1465")).then(pl.lit("146501")).otherwise(gmina)


class Data:
    """The 2024 gmina council files as frames keyed by council (gmina TERYT), constituency, and polling district."""

    def __init__(self) -> None:
        accounting: list[pl.DataFrame] = []
        votes: list[pl.DataFrame] = []
        for path, size in ((LARGE, "L"), (SMALL, "S")):
            for _, data in _members(path):
                found, cast = _protocol(data)
                accounting.append(found.with_columns(size=pl.lit(size)))
                votes.append(cast.with_columns(size=pl.lit(size)))
        both = [frame.rename({"gmina": "unit"}).with_columns(gmina=council_of(pl.col("unit"))) for frame in accounting]
        self.accounting = pl.concat(both)
        self.votes = pl.concat(
            [frame.rename({"gmina": "unit"}).with_columns(gmina=council_of(pl.col("unit"))) for frame in votes]
        )
        self.candidates = _candidates()
        self.constituencies = _constituencies()
        self.districts = _districts()


def _party_key(committee: str, gmina: str) -> str:
    """Committees of voters (KWW) are local to one gmina; party and coalition committees are the same everywhere."""
    if committee.startswith("KOMITET WYBORCZY WYBORCÓW"):
        return key("party", slug(committee), gmina)
    return key("party", slug(committee))


def _english(council: str, place: str) -> str:
    label = place.split(" ", 1)[1] if " " in place else place
    if council.startswith("Rada m. st. Warszawy"):
        return "Warsaw City Council"
    return f"{label} {'city' if place.startswith('m.') else 'gmina'} council"


SYSTEM_LIST = key("electoral_system", "list")
SYSTEM_SINGLE = key("electoral_system", "single")
ELECTION = key("election", DAY)
PATH = f"pl/{DAY}-gmina-councils"
LIST_RULES: dict[str, object] = {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}}
LIST_SYSTEM_NOTE = (
    "The council of a gmina with more than 20,000 inhabitants is elected in constituencies of five to eight members "
    "from open lists that election committees register. A voter marks one candidate. Seats go to the lists by the "
    "D'Hondt method on the sum of their candidates' votes, with no threshold, and within each list to the candidates "
    "with the most votes. D'Hondt reproduces the declared seats in %s of the %s constituencies."
)
SINGLE_RULES: dict[str, object] = {"formula": "fptp"}
SINGLE_SYSTEM_NOTE = (
    "The council of a gmina with 20,000 inhabitants or fewer is elected in single-member constituencies. A voter marks "
    "one candidate, and the candidate with the most votes wins. A constituency with no more candidates than seats "
    "holds no vote, and its candidate is declared elected."
)
LIST_METHOD = (
    "Counted by the polling-district commissions and published per polling district (obwód głosowania) by the "
    "Krajowe Biuro Wyborcze. Registered voters are the electors in the roll, ballots cast are the ballots taken from "
    "the box, valid are the valid votes, and invalid are the invalid ballots plus the invalid votes on valid ballots. "
    "A voter marks one candidate, so a list's votes are the sum of its candidates' votes; candidates' votes are "
    "stored as preference votes within their list. Declared seats and elected members are from the candidate file."
)
SINGLE_METHOD = (
    "Counted by the polling-district commissions and published per polling district (obwód głosowania) by the "
    "Krajowe Biuro Wyborcze. A polling district that serves several constituencies appears once for each, with that "
    "constituency's own figures. Registered voters are the electors in the roll, ballots cast are the ballots taken "
    "from the box, valid are the valid votes, and invalid are the invalid ballots plus the invalid votes on valid "
    "ballots. Elected members are from the candidate file."
)
TACIT_METHOD = (
    "The constituency had no more candidates than seats, so no vote was held and its candidates were declared "
    "elected (Krajowe Biuro Wyborcze, candidate file)."
)


def _dhondt(votes: dict[int, int], seats: int) -> dict[int, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(sorted(votes), key=lambda number: (votes[number] / (won[number] + 1), -number))
        won[best] += 1
    return {number: n for number, n in won.items() if n}


def build() -> Package:
    data = Data()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2024 gmina and city council elections in Poland by constituency and polling district from the "
        "Krajowe Biuro Wyborcze",
        depends=["pl.kbw/registry"],
    )
    p.external |= {POLAND, OBWOD}
    for path in FILES:
        p.artifact(kbw.cached(path), kbw.KBW)
    cands, cons = data.candidates, data.constituencies

    # Election, systems, and one body per council.
    p.add("election", id=ELECTION, polity_id=POLAND, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(ELECTION, "2024 gmina and city council elections in Poland", "en", "official")
    p.name(ELECTION, "Wybory do rad gmin i rad miast 2024", "pl", "native", False)
    p.add("slug", path=PATH, entity_id=ELECTION, is_current=True)
    exact = 0
    list_total = 0
    for (gmina, okr), part in cands.filter(pl.col("list") > 0).group_by(["gmina", "okr"]):
        seats = int(cons.filter((pl.col("gmina") == gmina) & (pl.col("okr") == okr))["seats"][0])
        totals = {
            r["list"]: r["votes"] for r in part.group_by("list").agg(pl.col("votes").sum().fill_null(0)).to_dicts()
        }
        declared = Counter(part.filter(pl.col("elected"))["list"].to_list())
        if sum(totals.values()):
            list_total += 1
            exact += _dhondt(totals, seats) == dict(declared)
    p.add(
        "electoral_system",
        id=SYSTEM_LIST,
        polity_id=POLAND,
        valid_from=date(2018, 1, 1),
        rules=LIST_RULES,
        rules_version=1,
    )
    p.name(SYSTEM_LIST, "Gmina council open-list D'Hondt system", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM_LIST),
        entity_id=SYSTEM_LIST,
        kind="methodology",
        body_md=LIST_SYSTEM_NOTE % (f"{exact:,}", f"{list_total:,}"),
        lang="en",
    )
    p.add("electoral_system", id=SYSTEM_SINGLE, polity_id=POLAND, rules=SINGLE_RULES, rules_version=1)
    p.name(SYSTEM_SINGLE, "Gmina council single-member plurality system", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM_SINGLE),
        entity_id=SYSTEM_SINGLE,
        kind="methodology",
        body_md=SINGLE_SYSTEM_NOTE,
        lang="en",
    )
    print(f"D'Hondt reproduces {exact:,} of {list_total:,} voted list constituencies", flush=True)

    councils = cons.group_by("gmina").agg(pl.col("council").first(), pl.col("place").first()).sort("gmina")
    bodies: list[dict[str, object]] = []
    body_names: list[dict[str, object]] = []
    for row in councils.iter_rows(named=True):
        body = key("body", row["gmina"])
        bodies.append({"id": body, "polity_id": POLAND, "kind": "council"})
        for text, lang, kind, preferred in (
            (_english(row["council"], row["place"]), "en", "official", True),
            (row["council"], "pl", "native", False),
        ):
            body_names.append(_name(p, body, text, lang, kind, preferred))
    p.frame("body", pl.DataFrame(bodies))
    p.frame("name", pl.DataFrame(body_names))
    english = {row["gmina"]: _english(row["council"], row["place"]) for row in councils.iter_rows(named=True)}
    return finish(p, data, english)


def _name(p: Package, entity: str, text: str, lang: str, kind: str, preferred: bool) -> dict[str, object]:
    return {
        "id": p.id("name", entity, lang, kind, text),
        "entity_id": entity,
        "text": text,
        "lang": lang,
        "kind": kind,
        "is_preferred": preferred,
    }


def finish(p: Package, data: Data, english: dict[str, str]) -> Package:
    cands, cons = data.candidates, data.constituencies
    names: list[dict[str, object]] = []
    rows: dict[str, list[dict[str, object]]] = {
        name: []
        for name in (
            "contest",
            "slug",
            "result_set",
            "note",
            "contest_option",
            "person",
            "candidacy",
            "outcome",
            "declared",
            "party",
        )
    }
    parties: dict[str, str] = {}

    def party(committee: str, gmina: str) -> str:
        id = _party_key(committee, gmina)
        if id not in parties:
            parties[id] = committee
            rows["party"].append({"id": id, "kind": "list_vehicle", "polity_id": POLAND})
            names.append(_name(p, id, committee, "pl", "official", True))
        return id

    by_okr: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in cands.iter_rows(named=True):
        by_okr.setdefault((str(row["gmina"]), int(str(row["okr"]))), []).append(row)
    option_rows: list[dict[str, object]] = []
    for row in cons.sort("gmina", "okr").iter_rows(named=True):
        gmina, okr = str(row["gmina"]), int(row["okr"])
        tag = (gmina, okr)
        members = by_okr[tag]
        single = members[0]["list"] == 0
        voted = bool(row["voted"])
        contest = key("contest", *tag)
        result_set = key("result_set", *tag)
        title = f"{english[gmina]}, constituency {okr}"
        rows["contest"].append(
            {
                "id": contest,
                "election_id": ELECTION,
                "kind": "body_seats",
                "body_id": key("body", gmina),
                "magnitude": int(row["seats"]),
                "round_number": 1,
                "electoral_system_id": SYSTEM_SINGLE if single else SYSTEM_LIST,
                "default_result_kind": "counted" if voted else "tacit",
            }
        )
        if not single:
            rows["contest"][-1]["kind"] = "list_allocation"
        names.append(_name(p, contest, title, "en", "official", True))
        names.append(_name(p, contest, f"{row['council']}, okręg wyborczy nr {okr}", "pl", "native", False))
        rows["slug"].append(
            {
                "path": f"{PATH}/{gmina}-{slug(english[gmina])}/{okr}",
                "entity_id": contest,
                "is_current": True,
            }
        )
        rows["result_set"].append(
            {
                "id": result_set,
                "election_id": ELECTION,
                "contest_id": contest,
                "stage": "official",
                "kind": "counted" if voted else "tacit",
                "source_id": kbw.KBW,
                "status": "active",
                "is_default": True,
                "completeness": 1.0 if voted else None,
            }
        )
        rows["note"].append(
            {
                "id": key("note", result_set),
                "entity_id": result_set,
                "kind": "methodology",
                "body_md": SINGLE_METHOD if single and voted else TACIT_METHOD if not voted else LIST_METHOD,
                "lang": "en",
            }
        )
        won: Counter[int] = Counter()
        list_options: dict[int, str] = {}
        for member in members:
            number, pos = int(str(member["list"])), int(str(member["pos"]))
            committee = str(member["committee"])
            if not single and number not in list_options:
                option = list_options[number] = key("option", *tag, "list", number)
                option_rows.append(
                    {
                        "id": option,
                        "contest_id": contest,
                        "kind": "list",
                        "party_id": party(committee, gmina),
                        "ballot_position": number,
                        "ballot_label": str(member["short"]),
                    }
                )
            ptag = (*tag, number, pos)
            person = key("person", *ptag)
            gender = {"Mężczyzna": "male", "Kobieta": "female"}[str(member["sex"])]
            rows["person"].append({"id": person, "gender": gender})
            shown = display(str(member["name"]), "pl")
            names.append(_name(p, person, shown, "pl", "official", True))
            candidate_option = key("option", *ptag)
            option_rows.append(
                {
                    "id": candidate_option,
                    "contest_id": contest,
                    "kind": "candidate",
                    "parent_option_id": None if single else list_options[number],
                    "party_id": party(committee, gmina) if single else None,
                    "ballot_position": pos,
                    "ballot_label": shown,
                }
            )
            candidacy = key("candidacy", *ptag)
            rows["candidacy"].append(
                {
                    "id": candidacy,
                    "person_id": person,
                    "option_id": candidate_option if single else list_options[number],
                    "role": "head" if single else "list_member",
                    "list_position": None if single else pos,
                    "ballot_name": str(member["name"]),
                    "is_independent": False,
                }
            )
            if member["elected"]:
                won[number] += 1
                rows["outcome"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": candidate_option if single else list_options[number],
                        "candidacy_id": None if single else candidacy,
                        "kind": "elected",
                    }
                )
            if voted and member["votes"] is not None:
                rows["declared"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": candidate_option,
                        "measure": VOTES if single else PREFERENCE,
                        "value": int(str(member["votes"])),
                    }
                )
        if not single:
            for number, seats in sorted(won.items()):
                rows["outcome"].append(
                    {"result_set_id": result_set, "option_id": list_options[number], "kind": "won", "seats": seats}
                )
        if voted:
            rows["declared"].append(
                {
                    "result_set_id": result_set,
                    "option_id": None,
                    "measure": VALID,
                    "value": int(str(members[0]["valid"])),
                }
            )
    p.frame("name", pl.DataFrame(names))
    p.frame("contest_option", pl.DataFrame(option_rows))
    for table, items in rows.items():
        if items:
            p.frame(table, pl.DataFrame(items, infer_schema_length=None))
    values(p, data)
    return p


def values(p: Package, data: Data) -> None:
    cands, cons = data.candidates, data.constituencies
    sets = cons.filter(pl.col("voted")).select(
        "gmina",
        "okr",
        result_set_id=pl.struct("gmina", "okr").map_elements(
            lambda row: key("result_set", row["gmina"], row["okr"]), return_dtype=pl.String
        ),
    )
    unit_keys = data.accounting.select("unit", "commission").unique().sort("unit", "commission")
    unit_ids = unit_keys.with_columns(
        unit_id=pl.struct("unit", "commission").map_elements(
            lambda row: key("unit", row["unit"], row["commission"]), return_dtype=pl.String
        )
    )
    districts = unit_ids.join(data.districts, on=["unit", "commission"], how="left")
    if districts["seat"].null_count():
        raise ValueError("polling districts missing from the polling-district file")
    p.frame(
        "reporting_unit",
        districts.select(
            id="unit_id",
            polity_id=pl.lit(POLAND),
            scheme_code=pl.lit(OBWOD),
            code=pl.format("{}:{}:{}:rada", pl.lit(str(DAY)), pl.col("unit"), pl.col("commission")),
            area_relation=pl.lit("unlocated"),
            kind="kind",
        ),
    )
    unit_names = [
        _name(p, row["unit_id"], row["seat"], "pl", "official", True) for row in districts.iter_rows(named=True)
    ]
    p.frame("name", pl.DataFrame(unit_names))
    base = data.accounting.join(sets, on=["gmina", "okr"]).join(unit_ids, on=["unit", "commission"])
    if base.height != data.accounting.height:
        raise ValueError("protocol rows outside the voted constituencies")
    figures = base.with_columns(invalid=pl.col("invalid_cards") + pl.col("invalid_votes"))
    for measure, column in (
        (REGISTERED, "registered"),
        (VOTERS, "voters"),
        (BALLOTS, "ballots"),
        (VALID, "valid"),
        (INVALID, "invalid"),
    ):
        p.frame(
            "result_value",
            figures.select(
                "result_set_id",
                "unit_id",
                option_id=pl.lit(None, pl.String),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(measure, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value=pl.col(column),
            ),
        )
    ids = cands.select(
        "gmina",
        "okr",
        "list",
        "pos",
        "name",
        "short",
        option=pl.struct("gmina", "okr", "list", "pos").map_elements(
            lambda row: key("option", row["gmina"], row["okr"], row["list"], row["pos"]), return_dtype=pl.String
        ),
    )
    lists = (
        ids.filter(pl.col("list") > 0)
        .unique(["gmina", "okr", "list"])
        .select(
            "gmina",
            "okr",
            "list",
            list_option=pl.struct("gmina", "okr", "list").map_elements(
                lambda row: key("option", row["gmina"], row["okr"], "list", row["list"]), return_dtype=pl.String
            ),
        )
    )
    single = data.votes.filter((pl.col("kind") == "candidate") & (pl.col("size") == "S"))
    big = data.votes.filter((pl.col("kind") == "candidate") & (pl.col("size") == "L"))
    named = ids.filter(pl.col("list") == 0)
    if named.select(pl.struct("gmina", "okr", "name", "short").n_unique()).item() != named.height:
        raise ValueError("candidates of one constituency share a name and a committee")
    small_votes = single.join(
        named.select("gmina", "okr", "name", "short", "option"),
        left_on=["gmina", "okr", "label", "registrar"],
        right_on=["gmina", "okr", "name", "short"],
        how="left",
    ).select("unit", "commission", "gmina", "okr", "option", "value", measure=pl.lit(VOTES))
    large_votes = big.join(
        ids.select("gmina", "okr", "list", "pos", "option"), on=["gmina", "okr", "list", "pos"], how="left"
    ).select("unit", "commission", "gmina", "okr", "option", "value", measure=pl.lit(PREFERENCE))
    list_votes = (
        data.votes.filter(pl.col("kind") == "list")
        .join(lists, on=["gmina", "okr", "list"], how="left")
        .select("unit", "commission", "gmina", "okr", option="list_option", value="value", measure=pl.lit(VOTES))
    )
    cast = pl.concat([small_votes, large_votes, list_votes])
    stray = cast.filter(pl.col("option").is_null())
    if stray.filter(pl.col("value") != 0).height:
        raise ValueError(f"{stray.filter(pl.col('value') != 0).height} votes for candidates or lists not in the files")
    cast = cast.drop_nulls("option").join(sets, on=["gmina", "okr"]).join(unit_ids, on=["unit", "commission"])
    p.frame(
        "result_value",
        cast.select(
            "result_set_id",
            "unit_id",
            "option",
            channel=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="value",
        ).rename({"option": "option_id"}),
    )
