"""Polish gmina and city council elections of 21 October 2018, by constituency and polling district.

The Krajowe Biuro Wyborcze publishes one protocol workbook for the councils of gminas with more than 20,000 inhabitants
(list elections by D'Hondt in constituencies of five to eight seats, the votes of each committee's list in each polling
district) and one for the smaller gminas (single-member constituencies won by plurality, the votes of each candidate in
each polling district), a candidate file with each candidate's total in the constituency and whether the candidate was
elected, the constituencies, and the polling districts. A polling district that serves several constituencies appears
once in each, with that constituency's own ballot.
"""

import io
import zipfile
from collections import Counter
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package
from ea.names import display
from ea.package import Package
from ea.sources.pl import kbw
from ea.sources.pl.kbw import BALLOTS, INVALID, OBWOD, PREFERENCE, REGISTERED, VALID, VOTERS, clean, count, slug
from ea.sources.pl.kbw_gmina import _dhondt, _name, council_of, teryt

SOURCE = "pl.kbw_gmina2018"
DAY = date(2018, 10, 21)
POLAND = kbw.POLAND
VOTES = 1
FOLDER = "2018/samorzad/"
LARGE = FOLDER + "2018-du%c5%bce-gminy-po-obwodach.xlsx"
SMALL = FOLDER + "samo2018-gminy-do-20-tys.xlsx"
RESULTS = FOLDER + "2018-wyniki-wyborów-do-rad.zip"
CONSTITUENCIES = FOLDER + "okregi.zip"
DISTRICTS = FOLDER + "obwody_glosowania.zip"
FILES = [LARGE, SMALL, RESULTS, CONSTITUENCIES, DISTRICTS]
CANDIDATE_FILE = FOLDER + "2018-kand-rady.xlsx"
LEVELS = ["gmina do 20 tys.", "gmina pow. 20 tys.", "m.p.p."]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def fetch() -> None:
    for path in [*FILES, CANDIDATE_FILE]:
        kbw.fetch_one(path)


def _read(data: bytes | str) -> pl.DataFrame:
    """Read a workbook's only sheet as text, with the line breaks in its headers folded into spaces."""
    frame = fastexcel.read_excel(data).load_sheet(0, header_row=0, dtypes="string").to_polars()
    return frame.rename({column: clean(column) for column in frame.columns})


def _csv(path: str) -> pl.DataFrame:
    archive = zipfile.ZipFile(kbw.cached(path).path)
    [name] = [n for n in archive.namelist() if n.endswith(".csv")]
    return pl.read_csv(io.StringIO(kbw.text(archive.read(name))), separator=";", infer_schema_length=0)


def _candidates() -> pl.DataFrame:
    """Every candidate of a gmina council: constituency, list (0 in the single-member constituencies), and position."""
    frame = _read(kbw.cached(CANDIDATE_FILE).path.read_bytes())
    frame = frame.filter(pl.col("Szczebel").is_in(LEVELS))
    return frame.select(
        gmina=teryt("TERYT"),
        okr=pl.col("Nr okr.").cast(pl.Int64),
        level=pl.col("Szczebel"),
        place=pl.col("Jednostka"),
        list=pl.when(pl.col("Szczebel") == "gmina do 20 tys.")
        .then(pl.lit(0, pl.Int64))
        .otherwise(pl.col("Nr listy").cast(pl.Int64)),
        pos=pl.when(pl.col("Szczebel") == "gmina do 20 tys.")
        .then(pl.col("Nr listy").cast(pl.Int64))
        .otherwise(pl.col("Poz.").cast(pl.Int64)),
        name=pl.concat_str(["Nazwisko", "Imię", "Drugie imię"], separator=" ", ignore_nulls=True).map_elements(
            clean, return_dtype=pl.String
        ),
        committee=pl.col("Komitet").map_elements(clean, return_dtype=pl.String),
        signature=pl.col("Sygnatura").map_elements(clean, return_dtype=pl.String),
        sex=pl.col("Płeć"),
        votes=count("Liczba głosów"),
        elected=pl.col("Mandat").is_in(["T", "B"]),
        tacit=pl.col("Mandat") == "B",
    )


def _constituencies() -> pl.DataFrame:
    frame = _csv(CONSTITUENCIES).filter(pl.col("Szczebel").str.starts_with("Rada gminy"))
    return frame.select(
        gmina=teryt("TERYT"),
        council=pl.col("Rada").map_elements(clean, return_dtype=pl.String),
        okr=pl.col("Numer okręgu").cast(pl.Int64),
        seats=pl.col("Liczba mandatów").cast(pl.Int64),
        lists=pl.col("Liczba list").cast(pl.Int64),
        candidates=pl.col("Liczba kandydatów").cast(pl.Int64),
        registered=count("Wyborcy"),
    )


def _districts() -> pl.DataFrame:
    frame = _csv(DISTRICTS)
    return frame.select(
        unit=teryt("Kod TERYT"),
        commission=pl.col("Nr obwodu").cast(pl.Int64),
        seat=pl.col("Pełna siedziba").map_elements(clean, return_dtype=pl.String),
        kind=pl.when(pl.col("Typ obwodu").str.to_lowercase().str.contains("zakład karny|areszt"))
        .then(pl.lit("prison"))
        .otherwise(pl.lit("standard")),
    )


def _large() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The large gminas' protocol as (accounting by polling district and constituency, list votes in long form)."""
    frame = _read(kbw.cached(LARGE).path.read_bytes()).with_row_index("row")
    first = frame.columns.index("Liczba głosów ważnych") + 1
    committees = frame.columns[first:]
    accounting = frame.select(
        "row",
        unit=teryt("TERYT"),
        okr=pl.col("Nr okr.").cast(pl.Int64),
        commission=pl.col("Nr obw.").cast(pl.Int64),
        registered=count("Liczba wyborców uprawnionych do głosowania"),
        voters=count("Liczba wyborców, którym wydano karty do głosowania"),
        ballots=count("Liczba kart wyjętych z urny"),
        invalid_cards=count("Liczba kart nieważnych"),
        invalid_votes=count("Liczba głosów nieważnych"),
        valid=count("Liczba głosów ważnych"),
    ).with_columns(gmina=council_of(pl.col("unit")))
    long = (
        frame.select("row", *committees)
        .unpivot(index="row", on=committees, variable_name="column", value_name="text")
        .drop_nulls("text")
        .filter(pl.col("text").str.strip_chars() != "")
        .with_columns(
            value=count("text"),
            signature=pl.col("column").str.extract(r" - ([^ ]+)$"),
            committee=pl.col("column").str.extract(r"^(.*) - [^ ]+$").map_elements(clean, return_dtype=pl.String),
        )
        .drop("text")
        .join(accounting.select("row", "unit", "gmina", "okr", "commission"), on="row")
    )
    if long["signature"].null_count():
        raise ValueError("committee columns without a signature")
    return accounting.drop("row"), long.drop("row")


def _small() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The small gminas' protocol as (accounting by polling district and constituency, candidate votes)."""
    frame = _read(kbw.cached(SMALL).path.read_bytes())
    common = {
        "unit": teryt("TERYT"),
        "gmina": teryt("TERYT"),
        "okr": pl.col("Nr okręgu wyborczego").cast(pl.Int64),
        "commission": pl.col("Nr obwodu głosowania").cast(pl.Int64),
    }
    accounting = frame.select(
        **common,
        registered=count("Liczba wyborców uprawnionych do głosowania"),
        voters=count("Liczba wyborców, którym wydano karty do głosowania"),
        ballots=count("Liczba kart wyjętych z urny"),
        invalid_cards=count("Liczba kart nieważnych"),
        invalid_votes=count("Liczba głosów nieważnych"),
        valid=count("Liczba głosów ważnych oddanych łącznie na wszystkie listy kandydatów"),
    )
    keys = ["unit", "okr", "commission"]
    unique = accounting.unique()
    if unique.unique(keys).height != unique.height:
        raise ValueError("a polling district and constituency has two different sets of totals")
    votes = frame.select(
        **common,
        number=pl.col("Nr listy").cast(pl.Int64),
        name=pl.col("Nazwisko i imiona").map_elements(clean, return_dtype=pl.String),
        value=count("Liczba głosów"),
    )
    return unique, votes


def english(place: str) -> str:
    label, _, kind = place.rpartition(", ")
    if kind.startswith("m. st."):
        return "Warsaw City Council"
    return f"{label} {'city' if kind == 'm.' else 'gmina'} council"


class Data:
    """The 2018 gmina council files as frames keyed by council (gmina TERYT), constituency, and polling district."""

    def __init__(self) -> None:
        self.large_accounting, self.large_votes = _large()
        self.small_accounting, self.small_votes = _small()
        self.small_accounting = self.small_accounting.with_columns(size=pl.lit("S"))
        self.large_accounting = self.large_accounting.with_columns(size=pl.lit("L"))
        self.accounting = pl.concat(
            [self.large_accounting, self.small_accounting.select(self.large_accounting.columns)]
        )
        self.candidates = _candidates()
        self.constituencies = _constituencies()
        self.districts = _districts()


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
    "A voter marks one candidate, so a list's votes are the sum of its candidates' votes. The source gives each "
    "candidate's votes only as a total for the constituency, stored as preference votes within the list. Declared "
    "seats and elected members are from the candidate file."
)
SINGLE_METHOD = (
    "Counted by the polling-district commissions and published per polling district (obwód głosowania) by the "
    "Krajowe Biuro Wyborcze. A polling district that serves several constituencies appears once for each, with that "
    "constituency's own figures. Registered voters are the electors in the roll, ballots cast are the ballots taken "
    "from the box, valid are the valid votes, and invalid are the invalid ballots plus the invalid votes on valid "
    "ballots. A candidate with no figure in a polling district received no votes there. Elected members are from the "
    "candidate file."
)
TACIT_METHOD = (
    "The constituency had no more candidates than seats, so no vote was held and its candidates were declared "
    "elected (Krajowe Biuro Wyborcze, candidate file)."
)


def _party_key(committee: str, gmina: str) -> str:
    """Committees of voters (KWW) are local to one gmina; party and coalition committees are the same everywhere."""
    if committee.startswith("KOMITET WYBORCZY WYBORCÓW"):
        return key("party", slug(committee), gmina)
    return key("party", slug(committee))


def build() -> Package:
    data = Data()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2018 gmina and city council elections in Poland by constituency and polling district from the "
        "Krajowe Biuro Wyborcze",
        depends=["pl.kbw/registry"],
    )
    p.external |= {POLAND, OBWOD}
    for path in [*FILES, CANDIDATE_FILE]:
        p.artifact(kbw.cached(path), kbw.KBW)
    cands, cons = data.candidates, data.constituencies
    check_sources(data)
    p.accept(
        "accounting.turnout",
        "In the large-gmina protocol for polling district 39 of constituency 2 of the gmina with TERYT code 246601, the "
        "commission reports 201 ballots taken from the box against 162 registered voters; the figures are the "
        "source's own.",
    )

    p.add("election", id=ELECTION, polity_id=POLAND, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(ELECTION, "2018 gmina and city council elections in Poland", "en", "official")
    p.name(ELECTION, "Wybory do rad gmin i rad miast 2018", "pl", "native", False)
    p.add("slug", path=PATH, entity_id=ELECTION, is_current=True)
    exact = 0
    list_total = 0
    protocol = (
        data.large_votes.group_by("gmina", "okr", "signature")
        .agg(pl.col("value").sum())
        .join(
            cands.filter(pl.col("list") > 0).select("gmina", "okr", "signature", "list").unique(),
            on=["gmina", "okr", "signature"],
        )
    )
    for (gmina, okr), part in cands.filter(pl.col("list") > 0).group_by(["gmina", "okr"]):
        seats = int(cons.filter((pl.col("gmina") == gmina) & (pl.col("okr") == okr))["seats"][0])
        found = protocol.filter((pl.col("gmina") == gmina) & (pl.col("okr") == okr))
        totals = dict.fromkeys(part["list"].to_list(), 0) | dict(zip(found["list"], found["value"], strict=True))
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

    places = cands.group_by("gmina").agg(pl.col("place").first()).sort("gmina")
    councils = cons.group_by("gmina").agg(pl.col("council").first()).join(places, on="gmina", how="inner").sort("gmina")
    labels = {row["gmina"]: english(row["place"]) for row in councils.iter_rows(named=True)}
    bodies: list[dict[str, object]] = []
    body_names: list[dict[str, object]] = []
    for row in councils.iter_rows(named=True):
        body = key("body", row["gmina"])
        bodies.append({"id": body, "polity_id": POLAND, "kind": "council"})
        for text, lang, kind, preferred in (
            (labels[row["gmina"]], "en", "official", True),
            (row["council"], "pl", "native", False),
        ):
            body_names.append(_name(p, body, text, lang, kind, preferred))
    p.frame("body", pl.DataFrame(bodies))
    p.frame("name", pl.DataFrame(body_names))
    councils_by = {row["gmina"]: row["council"] for row in councils.iter_rows(named=True)}
    return finish(p, data, labels, councils_by)


def check_sources(data: Data) -> None:
    """Refuse a parse whose files disagree: the figures must add up the way the authority's own totals say."""
    cands, cons = data.candidates, data.constituencies
    # Every constituency the candidate file lists is in the constituency file, and the reverse except where no one ran.
    keys = ["gmina", "okr"]
    if cands.select(keys).unique().join(cons.select(keys), on=keys, how="anti").height:
        raise ValueError("candidates in constituencies missing from the constituency file")
    empty = cons.join(cands.select(keys).unique(), on=keys, how="anti")
    if empty["candidates"].sum() != 0:
        raise ValueError("constituencies with candidates but no candidate rows")
    # Large gminas: committee columns add up to the valid votes of each polling district.
    summed = data.large_votes.group_by("unit", "okr", "commission").agg(pl.col("value").sum().alias("sum"))
    off = data.large_accounting.join(summed, on=["unit", "okr", "commission"], how="left").filter(
        pl.col("sum").fill_null(0) != pl.col("valid")
    )
    if off.height:
        raise ValueError(f"{off.height} large-gmina polling districts whose list votes differ from the valid votes")
    # Small gminas: candidate votes add up to the valid votes of each polling district.
    summed = data.small_votes.group_by("unit", "okr", "commission").agg(pl.col("value").sum().alias("sum"))
    off = data.small_accounting.join(summed, on=["unit", "okr", "commission"], how="left").filter(
        pl.col("sum").fill_null(0) != pl.col("valid")
    )
    if off.height:
        raise ValueError(
            f"{off.height} small-gmina polling districts whose candidate votes differ from the valid votes"
        )


def finish(p: Package, data: Data, english: dict[str, str], councils: dict[str, str]) -> Package:
    cands, cons = data.candidates, data.constituencies
    names: list[dict[str, object]] = []
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

    valid = {
        (str(r["gmina"]), int(r["okr"])): int(r["valid"])
        for r in data.accounting.group_by("gmina", "okr").agg(pl.col("valid").sum()).iter_rows(named=True)
    }
    by_okr: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in cands.iter_rows(named=True):
        by_okr.setdefault((str(row["gmina"]), int(str(row["okr"]))), []).append(row)
    option_rows: list[dict[str, object]] = []
    for row in cons.sort("gmina", "okr").iter_rows(named=True):
        gmina, okr = str(row["gmina"]), int(row["okr"])
        tag = (gmina, okr)
        members = by_okr.get(tag)
        if members is None:
            continue
        single = members[0]["list"] == 0
        tacit = all(bool(member["tacit"]) for member in members)
        voted = not tacit
        contest = key("contest", *tag)
        result_set = key("result_set", *tag)
        title = f"{english[gmina]}, constituency {okr}"
        rows["contest"].append(
            {
                "id": contest,
                "election_id": ELECTION,
                "kind": "body_seats" if single else "list_allocation",
                "body_id": key("body", gmina),
                "magnitude": int(row["seats"]),
                "round_number": 1,
                "electoral_system_id": SYSTEM_SINGLE if single else SYSTEM_LIST,
                "default_result_kind": "counted" if voted else "tacit",
            }
        )
        names.append(_name(p, contest, title, "en", "official", True))
        names.append(_name(p, contest, f"{councils[gmina]}, okręg wyborczy nr {okr}", "pl", "native", False))
        rows["slug"].append(
            {"path": f"{PATH}/{gmina}-{slug(english[gmina])}/{okr}", "entity_id": contest, "is_current": True}
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
                        "ballot_label": committee,
                    }
                )
            ptag = (*tag, number, pos)
            person = key("person", *ptag)
            gender = {"M": "male", "K": "female"}[str(member["sex"])]
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
                    "value": valid[tag],
                }
            )
    p.frame("name", pl.DataFrame(names))
    p.frame("contest_option", pl.DataFrame(option_rows))
    for table, items in rows.items():
        if items:
            p.frame(table, pl.DataFrame(items, infer_schema_length=None))
    values(p, data, english)
    return p


def values(p: Package, data: Data, english: dict[str, str]) -> None:
    cands, cons = data.candidates, data.constituencies
    tacit = cands.group_by("gmina", "okr").agg(pl.col("tacit").all().alias("tacit"))
    sets = (
        cons.join(tacit, on=["gmina", "okr"])
        .filter(~pl.col("tacit"))
        .select(
            "gmina",
            "okr",
            result_set_id=pl.struct("gmina", "okr").map_elements(
                lambda row: key("result_set", row["gmina"], row["okr"]), return_dtype=pl.String
            ),
        )
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
    p.frame(
        "name",
        pl.DataFrame(
            [_name(p, row["unit_id"], row["seat"], "pl", "official", True) for row in districts.iter_rows(named=True)]
        ),
    )
    # The large gminas' candidate votes are published only as a total for the constituency: one aggregate unit each.
    totals_by = (
        cands.filter((pl.col("list") > 0) & pl.col("votes").is_not_null())
        .join(sets, on=["gmina", "okr"])
        .with_columns(
            unit_id=pl.struct("gmina", "okr").map_elements(
                lambda row: key("unit", row["gmina"], row["okr"], "total"), return_dtype=pl.String
            ),
            option=pl.struct("gmina", "okr", "list", "pos").map_elements(
                lambda row: key("option", row["gmina"], row["okr"], row["list"], row["pos"]), return_dtype=pl.String
            ),
        )
    )
    aggregates = totals_by.select("gmina", "okr", "unit_id").unique().sort("gmina", "okr")
    p.frame(
        "reporting_unit",
        aggregates.select(
            id="unit_id",
            polity_id=pl.lit(POLAND),
            scheme_code=pl.lit(OBWOD),
            code=pl.format("{}:{}:{}:okreg", pl.lit(str(DAY)), pl.col("gmina"), pl.col("okr")),
            area_relation=pl.lit("unlocated"),
            kind=pl.lit("aggregate"),
        ),
    )
    p.frame(
        "name",
        pl.DataFrame(
            [
                _name(p, row["unit_id"], f"{english[row['gmina']]}, constituency {row['okr']}", "en", "official", True)
                for row in aggregates.iter_rows(named=True)
            ]
        ),
    )
    p.frame(
        "result_value",
        totals_by.select(
            "result_set_id",
            "unit_id",
            option_id="option",
            channel=pl.lit(0, pl.Int16),
            measure=pl.lit(PREFERENCE, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="votes",
        ),
    )
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
        "signature",
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
            "signature",
            option=pl.struct("gmina", "okr", "list").map_elements(
                lambda row: key("option", row["gmina"], row["okr"], "list", row["list"]), return_dtype=pl.String
            ),
        )
    )
    if lists.unique(["gmina", "okr", "signature"]).height != lists.height:
        raise ValueError("two lists of one constituency share a committee signature")
    # Small gminas: the votes of each candidate, matched on the number on the ballot.
    named = ids.filter(pl.col("list") == 0).select("gmina", "okr", number="pos", ballot="name", option="option")
    small = data.small_votes.join(named, on=["gmina", "okr", "number"], how="left")
    unmatched = small.filter(pl.col("option").is_null())
    if unmatched["value"].fill_null(0).sum():
        raise ValueError("votes for candidates not in the candidate file")
    # A candidate removed from the ballot after printing keeps a blank cell in the protocol and no row in the candidate file.
    small = small.drop_nulls("option")
    same = small.filter(
        pl.col("name").str.replace_all(r"[^\p{L} ]", "") != pl.col("ballot").str.replace_all(r"[^\p{L} ]", "")
    )
    if same.height > 25:
        raise ValueError(f"{same.height} candidates whose name differs between the protocol and the candidate file")
    small_votes = small.select("unit", "commission", "gmina", "okr", "option", "value", measure=pl.lit(VOTES))
    large = data.large_votes.join(lists, on=["gmina", "okr", "signature"], how="left")
    stray = large.filter(pl.col("option").is_null())
    if stray.filter(pl.col("value") != 0).height:
        raise ValueError(f"{stray.filter(pl.col('value') != 0).height} votes for lists not in the candidate file")
    large_votes = large.drop_nulls("option").select(
        "unit", "commission", "gmina", "okr", "option", "value", measure=pl.lit(VOTES)
    )
    cast = pl.concat([small_votes.with_columns(value=pl.col("value").fill_null(0)), large_votes])
    cast = cast.join(sets, on=["gmina", "okr"]).join(unit_ids, on=["unit", "commission"])
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
    totals(data)


def totals(data: Data) -> None:
    """Compare the polling districts with the authority's totals for each constituency in the candidate file."""
    cands = data.candidates
    keys = ["gmina", "okr"]
    own = data.small_accounting.group_by(keys).agg(pl.col("valid").sum().alias("sum"))
    declared = cands.filter(pl.col("list") == 0).group_by(keys).agg(pl.col("votes").sum().alias("declared"))
    gap = own.join(declared, on=keys, how="left").filter(pl.col("sum") != pl.col("declared"))
    if gap.height:
        raise ValueError(f"{gap.height} small-gmina constituencies that differ from the candidate file")
    own = data.large_accounting.group_by(keys).agg(pl.col("valid").sum().alias("sum"))
    declared = cands.filter(pl.col("list") > 0).group_by(keys).agg(pl.col("votes").sum().alias("declared"))
    gap = own.join(declared, on=keys, how="left").filter(pl.col("sum") != pl.col("declared"))
    if gap.filter(pl.col("sum") < pl.col("declared")).height:
        raise ValueError("candidate votes above the valid votes of a large-gmina constituency")
    print(
        f"{gap.height} large-gmina constituencies where the candidate file holds {int((gap['sum'] - gap['declared']).sum())}"
        " fewer votes than the polling districts' valid total",
        flush=True,
    )
    own = data.large_votes.group_by(keys + ["signature"]).agg(pl.col("value").sum().alias("sum"))
    declared = (
        cands.filter(pl.col("list") > 0).group_by(keys + ["signature"]).agg(pl.col("votes").sum().alias("declared"))
    )
    gap = own.join(declared, on=keys + ["signature"], how="left").filter(pl.col("sum") != pl.col("declared"))
    if gap.filter(pl.col("sum") < pl.col("declared")).height:
        raise ValueError("candidate votes above the list votes of a large-gmina list")
    print(
        f"{gap.height} lists whose candidates' votes in the candidate file fall short of the list's votes", flush=True
    )
    seats = cands.group_by(keys).agg(pl.col("elected").sum().alias("elected"))
    short = data.constituencies.join(seats, on=keys).filter(pl.col("elected") > pl.col("seats"))
    if short.height:
        raise ValueError(f"{short.height} constituencies with more members elected than seats")
