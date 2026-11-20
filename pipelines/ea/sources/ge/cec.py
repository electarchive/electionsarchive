"""Georgia's elections held by the Central Election Commission, by precinct, 2008 to 2026, and the 1919 Constituent
Assembly by district.

The Commission publishes each election's results as a workbook of precinct protocols. David Sichinava compiled them
in the Georgia Elections Data Archive (GEDA, CC BY 4.0), whose repository is read here at one pinned commit. Each
election's precinct workbook is reduced there to a long file of one row per precinct and ballot line, the files read
by this module, together with the registry of parties and the elections' configuration (seats, thresholds, and the
names of lists and candidates).

One package holds the elections of one date: the general, repeat, by-election, and runoff ballots held that day. Every
precinct of the date is one reporting unit, shared by the ballots held there. The votes are the precinct protocol's
votes for each list or candidate; the turnout figures are the registered voters, the voters who took part, and the
invalid ballots the protocol prints.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal
from uuid import uuid5

import polars as pl
import yaml

from ea import package, raw
from ea.package import Package

SOURCE = "ge.cec"
PIN = "764afb39eef36de97d910f1e131a33801149f4a0"
BASE = f"https://raw.githubusercontent.com/geoelectionsdata/geoelectionsdata.github.io/{PIN}/src/data/"
REPOSITORY = f"https://github.com/geoelectionsdata/geoelectionsdata.github.io/tree/{PIN}"
GEORGIA = str(uuid5(package.NAMESPACE, "polity\x1fGE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGEO"))
SCHEME = "ge.precinct"
DISTRICT_SCHEME = "ge.district"
REGISTRY = f"{SOURCE}/registry"
ORIGIN = (
    "Precinct protocols of the Central Election Commission of Georgia, as compiled by David Sichinava in the Georgia "
    "Elections Data Archive (GEDA, CC-BY-4.0) at commit 764afb39"
)

ASSEMBLY_DAY = date(1919, 2, 14)
ASSEMBLY = f"{BASE}results/parl1919_pr.csv"
Role = Literal["president", "list", "smd", "mayor", "council_pr", "council_smd"]
REGISTERED, VOTERS, INVALID = 2, 14, 5


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC = key("source", "cec-geda")
ARCHIVE = key("source", "national-archives-geda")
CONSTITUENT = key("body", "constituent-assembly")
COLORS = key("source", "geda-colors")
PARLIAMENT = key("body", "parliament")
AJARA = key("body", "ajara-supreme-council")
COUNCILS = key("body", "municipal-councils")
PRESIDENT = key("office", "president")
MAYOR = key("office", "mayor")
SYSTEM_PRESIDENT = key("electoral_system", "president")


@dataclass(frozen=True)
class Ballot:
    day: date
    family: str  # pres, parl, adj, local
    kind: str  # election kind: general, local, by_election, runoff, special
    stem: str
    role: Role
    round: int = 1
    first: str | None = None  # stem of the first round this ballot repeats


def b(day: str, family: str, kind: str, stem: str, role: Role, round: int = 1, first: str | None = None) -> Ballot:
    return Ballot(date.fromisoformat(day), family, kind, stem, role, round, first)


BALLOTS: tuple[Ballot, ...] = (
    b("2008-01-05", "pres", "general", "pres2008", "president"),
    b("2008-05-21", "parl", "general", "parl2008_pr", "list"),
    b("2008-05-21", "parl", "general", "parl2008_smd", "smd"),
    b("2008-11-03", "adj", "general", "adj2008_pr", "list"),
    b("2008-11-03", "adj", "general", "adj2008_smd", "smd"),
    b("2010-05-30", "local", "local", "local2010_pr", "council_pr"),
    b("2010-05-30", "local", "local", "local2010_smd", "mayor"),
    b("2010-05-30", "local", "local", "local2010_council_smd", "council_smd"),
    b("2012-10-01", "parl", "general", "parl2012_pr", "list"),
    b("2012-10-01", "parl", "general", "parl2012_smd", "smd"),
    b("2013-10-27", "pres", "general", "pres2013", "president"),
    b("2014-06-15", "local", "local", "local2014_pr", "council_pr"),
    b("2014-06-15", "local", "local", "local2014_smd", "mayor"),
    b("2014-06-15", "local", "local", "local2014_council_smd", "council_smd"),
    b("2014-07-12", "local", "runoff", "local2014_r2_smd", "mayor", 2, "local2014_smd"),
    b("2014-10-20", "local", "by_election", "local2014_2014_oct_byelection_council_smd", "council_smd"),
    b("2015-05-31", "local", "by_election", "local2014_2015_may_byelection_council_smd", "council_smd"),
    b("2015-10-31", "local", "by_election", "local2014_2015_oct_byelection_council_smd", "council_smd"),
    b("2015-10-31", "parl", "by_election", "parl2012_2015_byelection_smd", "smd"),
    b("2016-01-28", "local", "by_election", "local2014_2016_gardabani_byelection_smd", "mayor"),
    b("2016-05-22", "local", "by_election", "local2014_2016_may_byelection_council_smd", "council_smd"),
    b("2016-10-08", "parl", "general", "parl2016_pr", "list"),
    b("2016-10-08", "parl", "general", "parl2016_smd", "smd"),
    b("2016-10-08", "local", "by_election", "local2014_2016_oct_byelection_council_smd", "council_smd"),
    b("2016-10-08", "local", "by_election", "local2014_2016_oct_mayor_byelection_smd", "mayor"),
    b("2016-10-22", "parl", "special", "parl2016_pr_repeated", "list"),
    b("2016-10-22", "parl", "special", "parl2016_smd_repeated", "smd"),
    b("2016-10-30", "parl", "runoff", "parl2016_smd_runoff", "smd", 2, "parl2016_smd"),
    b(
        "2016-10-30",
        "local",
        "runoff",
        "local2014_2016_oct_mayor_byelection_r2_smd",
        "mayor",
        2,
        "local2014_2016_oct_mayor_byelection_smd",
    ),
    b("2016-10-30", "local", "by_election", "local2014_2016_oct30_byelection_council_smd", "council_smd"),
    b("2017-10-21", "local", "local", "local2017_pr", "council_pr"),
    b("2017-10-21", "local", "local", "local2017_smd", "mayor"),
    b("2017-10-21", "local", "local", "local2017_council_smd", "council_smd"),
    b("2017-11-12", "local", "runoff", "local2017_r2_smd", "mayor", 2, "local2017_smd"),
    b("2018-04-28", "parl", "by_election", "parl2016_major54_2018_smd", "smd"),
    b("2018-05-13", "local", "by_election", "local2017_zugdidi_2018_council_smd", "council_smd"),
    b("2018-10-28", "pres", "general", "pres2018_r1", "president"),
    b("2018-11-28", "pres", "runoff", "pres2018_r2", "president", 2, "pres2018_r1"),
    b("2019-05-19", "local", "by_election", "local2017_2019_byelections_smd", "mayor"),
    b("2019-05-19", "local", "by_election", "local2017_2019_byelections_council_smd", "council_smd"),
    b("2019-05-19", "parl", "by_election", "parl2016_mtatsminda_2019_smd", "smd"),
    b("2019-06-09", "parl", "runoff", "parl2016_mtatsminda_2019_smd_runoff", "smd", 2, "parl2016_mtatsminda_2019_smd"),
    b("2019-10-27", "local", "by_election", "local2017_2019_oct_byelections_council_smd", "council_smd"),
    b("2020-10-31", "parl", "general", "parl2020_pr", "list"),
    b("2020-10-31", "parl", "general", "parl2020_smd", "smd"),
    b("2020-10-31", "local", "by_election", "local2017_2020_byelections_smd", "mayor"),
    b("2020-10-31", "local", "by_election", "local2017_2020_byelections_council_smd", "council_smd"),
    b("2020-11-21", "parl", "runoff", "parl2020_smd_runoff", "smd", 2, "parl2020_smd"),
    b(
        "2020-11-21",
        "local",
        "runoff",
        "local2017_2020_byelections_smd_runoff",
        "mayor",
        2,
        "local2017_2020_byelections_smd",
    ),
    b("2021-10-02", "local", "local", "local2021_pr", "council_pr"),
    b("2021-10-02", "local", "local", "local2021_smd", "mayor"),
    b("2021-10-02", "local", "local", "local2021_council_smd", "council_smd"),
    b("2021-10-30", "local", "runoff", "local2021_r2_smd", "mayor", 2, "local2021_smd"),
    b("2021-10-30", "local", "runoff", "local2021_r2_council_smd", "council_smd", 2, "local2021_council_smd"),
    b("2024-10-26", "parl", "general", "parl2024_pr", "list"),
    b("2025-10-04", "local", "local", "local2025_pr", "council_pr"),
    b("2025-10-04", "local", "local", "local2025_smd", "mayor"),
    b("2025-10-04", "local", "local", "local2025_council_smd", "council_smd"),
    b("2026-10-03", "local", "by_election", "local2025_2026_byelections_council_smd", "council_smd"),
)
FIRST_ROUNDS = {ballot.stem: ballot for ballot in BALLOTS}
CONFIGS = (
    "parliamentary/parl_2008", "parliamentary/parl_2012", "parliamentary/parl_2016", "parliamentary/parl_2020",
    "parliamentary/parl_2024", "presidential/pres_2008", "presidential/pres_2013", "presidential/pres_2018",
    "adjara/adj_2008", "local/local_2010", "local/local_2014", "local/local_2017", "local/local_2021",
    "local/local_2025", "parliamentary/parl_1919",
)  # fmt: skip
FAMILY = {
    "pres": ("president", "president", "Presidential election", "საპრეზიდენტო არჩევნები"),
    "parl": ("parliament", "parliament", "Parliamentary election", "საპარლამენტო არჩევნები"),
    "adj": ("ajara", "ajara-supreme-council", "Ajara Supreme Council election", "აჭარის უმაღლესი საბჭოს არჩევნები"),
    "local": ("local", "local", "Local election", "ადგილობრივი თვითმმართველობის არჩევნები"),
}
KIND = {
    "general": ("", "", ""),
    "local": ("", "", ""),
    "by_election": (" by-elections", "-by-election", " შუალედური არჩევნები"),
    "runoff": (" runoff", "-runoff", " მეორე ტური"),
    "special": (" repeat vote", "-repeat", " განმეორებითი კენჭისყრა"),
}
MONTHS = (
    "იანვრის", "თებერვლის", "მარტის", "აპრილის", "მაისის", "ივნისის",
    "ივლისის", "აგვისტოს", "სექტემბრის", "ოქტომბრის", "ნოემბრის", "დეკემბრის",
)  # fmt: skip
ROLE_NAME = {
    "president": ("President", "პრეზიდენტი"),
    "list": ("Party lists", "პარტიული სიები"),
    "smd": ("Single-member district", "მაჟორიტარული ოლქი"),
    "mayor": ("Mayor", "მერი"),
    "council_pr": ("Council party lists", "საკრებულოს პარტიული სიები"),
    "council_smd": ("Council single-member district", "საკრებულოს მაჟორიტარული ოლქი"),
}


def url(name: str) -> str:
    return f"{BASE}{name}"


def result_url(stem: str, precincts: bool = True) -> str:
    return url(f"results/{stem}{'_precincts' if precincts else ''}.csv")


def district_file(ballot: Ballot) -> str | None:
    """The district-level file that names each single-member district's candidates."""
    return ballot.stem if ballot.role in ("smd", "president", "list") else None


def packages() -> list[str]:
    return [
        f"{SOURCE}/registry",
        f"{SOURCE}/{ASSEMBLY_DAY}",
        *(f"{SOURCE}/{day}" for day in sorted({ballot.day for ballot in BALLOTS})),
    ]


def fetch() -> None:
    raw.fetch(SOURCE, url("config/parties.yml"), suffix=".yml")
    raw.fetch(SOURCE, ASSEMBLY, suffix=".csv")
    for config in CONFIGS:
        raw.fetch(SOURCE, url(f"config/elections/{config}.yml"), suffix=".yml")
    for ballot in BALLOTS:
        raw.fetch(SOURCE, result_url(ballot.stem), suffix=".csv")
        if district_file(ballot):
            raw.fetch(SOURCE, result_url(ballot.stem, False), suffix=".csv")


def parse() -> None:
    registry().write()
    assembly().write()
    for day in sorted({ballot.day for ballot in BALLOTS}):
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def table(name: str) -> pl.DataFrame:
    return pl.read_csv(raw.cached(SOURCE, name).path, infer_schema_length=0, null_values=["NA"])


def yml(name: str) -> dict[str, Any]:
    return yaml.safe_load(raw.cached(SOURCE, url(name)).path.read_text(encoding="utf-8"))


def names() -> dict[str, dict[str, object]]:
    """Every list or candidate the configuration names, by id: its names, type, color, and party."""
    found: dict[str, dict[str, object]] = {}
    for item in yml("config/parties.yml")["parties"]:
        found[item["id"]] = {
            "en": item["name"].get("en"),
            "ka": item["name"].get("ka"),
            "type": item["type"],
            "color": item.get("color"),
        }
    for config in CONFIGS:
        document = yml(f"config/elections/{config}.yml")
        for kind in ("parties", "candidates"):
            for item in document.get(kind) or []:
                if item["id"] in found:
                    continue
                label = item.get("alias") or item.get("name") or {}
                found[item["id"]] = {
                    "en": label.get("en"),
                    "ka": label.get("ka"),
                    "type": "candidate" if kind == "candidates" else "party",
                    "color": item.get("color"),
                    "party": item.get("party"),
                }
    return found


PARTY_KIND = {"party": "party", "bloc": "bloc", "alliance": "bloc"}


def registered() -> dict[str, dict[str, object]]:
    """The ids that become party entities: named lists, not candidates, options, or independents."""
    return {
        id: item
        for id, item in names().items()
        if item["type"] in PARTY_KIND and (item.get("en") or item.get("ka")) and not id.startswith("initiative")
    }


def party(id: str) -> str:
    return key("party", id)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Georgia's parties, Parliament, Ajara Supreme Council, municipal councils, president and mayors, with the "
        "sources of the Central Election Commission's precinct results",
    )
    p.external.add(GEORGIA)
    p.add(
        "source",
        id=CEC,
        kind="official_body",
        publisher="Central Election Commission of Georgia",
        title=ORIGIN,
        url=REPOSITORY,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Central Election Commission of Georgia; compiled by David Sichinava, Georgia Elections Data Archive",
    )
    p.add(
        "source",
        id=COLORS,
        kind="community",
        publisher="Georgia Elections Data Archive",
        title="Party colors of the Georgia Elections Data Archive, at commit 764afb39",
        url=REPOSITORY,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="David Sichinava, Georgia Elections Data Archive",
    )
    p.add(
        "source",
        id=ARCHIVE,
        kind="compendium",
        publisher="National Archives of Georgia, Central Historical Archive",
        title="Results of the 1919 Constituent Assembly election, from the Central Historical Archive (fund 1834), "
        "as compiled by David Sichinava in the Georgia Elections Data Archive (GEDA, CC-BY-4.0) at commit 764afb39",
        url=REPOSITORY,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="National Archives of Georgia; compiled by David Sichinava, Georgia Elections Data Archive",
    )
    p.artifact(raw.cached(SOURCE, ASSEMBLY), ARCHIVE)
    p.add("body", id=CONSTITUENT, polity_id=GEORGIA, kind="constituent_assembly")
    p.name(CONSTITUENT, "Constituent Assembly of Georgia", "en", "english")
    p.name(CONSTITUENT, "საქართველოს დამფუძნებელი კრება", "ka", "official")
    p.add(
        "scheme",
        code=DISTRICT_SCHEME,
        polity_id=GEORGIA,
        kind="electoral_district",
        rank=0,
        noun="district",
        code_system=None,
    )
    for ballot in BALLOTS:
        p.artifact(raw.cached(SOURCE, result_url(ballot.stem)), CEC)
        if district_file(ballot):
            p.artifact(raw.cached(SOURCE, result_url(ballot.stem, False)), CEC)
    p.artifact(raw.cached(SOURCE, url("config/parties.yml")), COLORS)
    for config in CONFIGS:
        p.artifact(raw.cached(SOURCE, url(f"config/elections/{config}.yml")), CEC)
    for id, text_en, text_ka, kind in (
        (PARLIAMENT, "Parliament of Georgia", "საქართველოს პარლამენტი", "unicameral"),
        (
            AJARA,
            "Supreme Council of the Autonomous Republic of Ajara",
            "აჭარის ავტონომიური რესპუბლიკის უმაღლესი საბჭო",
            "unicameral",
        ),
        (COUNCILS, "Municipal councils (sakrebulo) of Georgia", "მუნიციპალიტეტების საკრებულოები", "council"),
    ):
        p.add("body", id=id, polity_id=GEORGIA, kind=kind)
        p.name(id, text_en, "en", "english")
        p.name(id, text_ka, "ka", "official")
    p.add("office", id=PRESIDENT, polity_id=GEORGIA, kind="head_of_state")
    p.name(PRESIDENT, "President of Georgia", "en", "english")
    p.name(PRESIDENT, "საქართველოს პრეზიდენტი", "ka", "official")
    p.add("office", id=MAYOR, polity_id=GEORGIA, kind="mayor")
    p.name(MAYOR, "Mayor", "en", "english")
    p.name(MAYOR, "მერი", "ka", "official")
    p.add(
        "electoral_system",
        id=SYSTEM_PRESIDENT,
        polity_id=GEORGIA,
        valid_from=date(2008, 1, 5),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_PRESIDENT),
        entity_id=SYSTEM_PRESIDENT,
        kind="methodology",
        body_md="A candidate wins in the first round with more than half of the votes; otherwise the two leading "
        "candidates meet in a second round.",
        lang="en",
    )
    p.add("scheme", code=SCHEME, polity_id=GEORGIA, kind="precinct", rank=1, noun="precinct", code_system=None)
    for id, item in sorted(registered().items()):
        entity = party(id)
        p.add("party", id=entity, kind=PARTY_KIND[str(item["type"])], polity_id=GEORGIA)
        if item.get("ka"):
            p.name(entity, text(str(item["ka"])), "ka", "official")
        if item.get("en"):
            p.name(entity, text(str(item["en"])), "en", "english")
        color = str(item.get("color") or "").lower()
        if re.fullmatch(r"#[0-9a-f]{6}", color):
            p.add("party_color", party_id=entity, color=color, valid_from=None, valid_to=None, source_id=COLORS)
    return p


def precinct_code(frame: pl.DataFrame) -> pl.Expr:
    """The CEC district times 1000 plus the precinct number, which every file carries as `precinct_id` or in the
    dotted `precinct_key` (`smd.district.precinct`) that replaces it where the id is missing."""
    if "precinct_key" not in frame.columns:
        return pl.col("precinct_id")
    parts = pl.col("precinct_key").str.split(".")
    dotted = pl.col("precinct_key").str.count_matches(r"\.") == 2
    return (
        pl.when(dotted)
        .then((parts.list.get(1).cast(pl.Int64) * 1000 + parts.list.get(2).cast(pl.Int64)).cast(pl.String))
        .otherwise(pl.coalesce("precinct_id", "precinct_key"))
    )


def norm(ballot: Ballot) -> pl.DataFrame:
    """One row per precinct and ballot line: unit, contest group, line, votes, and the precinct's turnout."""
    frame = table(result_url(ballot.stem))
    if frame.height == 0:
        return frame
    name = pl.col("name_ka") if "name_ka" in frame.columns else pl.lit(None, pl.String)
    num = pl.col("party_num") if "party_num" in frame.columns else pl.lit(None, pl.String)
    if ballot.role in ("president", "list") or ballot.role == "smd":
        group = pl.lit(None, pl.String)
    elif ballot.role in ("council_pr",) or ballot.role == "council_smd":
        group = pl.col("district_id")
    else:
        group = pl.col("selfgov_id") if "selfgov_id" in frame.columns else pl.col("district_id")
    unit = precinct_code(frame)
    if "special_list" in frame.columns:
        # A precinct that also held a protocol for its special voter list reports the two as separate lines.
        unit = (
            pl.when((pl.col("main_list").cast(pl.Int64) == 0) & (pl.col("special_list").cast(pl.Int64) > 0))
            .then(unit + pl.lit("s"))
            .otherwise(unit)
        )
    out = frame.select(
        unit=unit,
        group=group,
        party=pl.col("party_id"),
        name=name,
        num=num,
        votes=pl.col("votes").cast(pl.Int64),
        registered=pl.col("registered").cast(pl.Int64),
        voted=pl.col("voted").cast(pl.Int64),
        invalid=pl.col("invalid_ballots").cast(pl.Int64),
        smd=pl.col("smd") if "smd" in frame.columns else pl.lit(None, pl.String),
        dotted=pl.col("precinct_key").str.split(".").list.get(0)
        if "precinct_key" in frame.columns
        else pl.lit(None, pl.String),
        electoral=pl.col("electoral_district_id")
        if "electoral_district_id" in frame.columns
        else pl.lit(None, pl.String),
    )
    if ballot.role == "smd":
        districts = table(result_url(ballot.stem, False))
        mapping = (
            districts.select(
                "district_id",
                party="party_id",
                name=pl.col("name_ka") if "name_ka" in districts.columns else pl.lit(None, pl.String),
            )
            .filter(pl.col("district_id") != "national")
            .unique()
            .rename({"district_id": "mapped"})
        )
        # Where the file names no district, the district is the one in which every candidate on the precinct's
        # ballot stood.
        votes = out.select("unit", "party", "name").unique().join(mapping, on=["party", "name"], how="left")
        counts = votes.group_by("unit", "mapped").len()
        best = counts.sort("len", descending=True).group_by("unit").first().select("unit", "mapped")
        out = out.join(best, on="unit", how="left")
        out = out.with_columns(
            group=pl.coalesce(
                "smd", "electoral", "dotted", "mapped", (pl.col("unit").cast(pl.Int64) // 1000).cast(pl.String)
            )
        )
    return out.drop("smd", "dotted", "electoral", *(["mapped"] if "mapped" in out.columns else []))


def seats(ballot: Ballot) -> dict[str, int]:
    """The seats each list won, as the election's configuration states them, when they add up to the seats
    contested."""
    if ballot.role != "list" or ballot.family != "parl":
        return {}
    config = yml(f"config/elections/parliamentary/parl_{ballot.day.year}.yml")
    won = {item["id"]: item["seats_pr"] for item in config["parties"] if item.get("seats_pr")}
    return won if sum(won.values()) == config["system"]["pr"]["seats"] else {}


def declared(ballot: Ballot) -> pl.DataFrame:
    """The votes the district-level file gives each line: the national totals of a list or presidential ballot, and
    each district's totals of a single-member ballot."""
    frame = table(result_url(ballot.stem, False))
    name = pl.col("name_ka") if "name_ka" in frame.columns else pl.lit(None, pl.String)
    lines = frame.select(group=pl.col("district_id"), party="party_id", name=name, votes=pl.col("votes").cast(pl.Int64))
    if ballot.role == "smd":
        return lines.filter(pl.col("group") != "national").group_by("group", "party", "name").agg(pl.col("votes").sum())
    national = lines.filter(pl.col("group") == "national")
    return (
        (national if national.height else lines)
        .group_by("party", "name")
        .agg(pl.col("votes").sum())
        .with_columns(group=pl.lit(None, pl.String))
    )


def humanize(id: str) -> str:
    return id.replace("_", " ").strip().title()


def build(day: date) -> Package:
    ballots = [ballot for ballot in BALLOTS if ballot.day == day]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The elections of {day:%-d %B %Y} by precinct from the Central Election Commission's protocols",
        depends=[REGISTRY],
    )
    p.external.add(GEORGIA)
    p.external.add(COUNTRY)
    known = registered()
    labels = names()
    units: dict[str, str] = {}
    elections: dict[tuple[str, str], str] = {}
    over: set[str] = set()
    short: list[str] = []
    for ballot in ballots:
        p.uses(raw.cached(SOURCE, result_url(ballot.stem)))
        if district_file(ballot):
            p.uses(raw.cached(SOURCE, result_url(ballot.stem, False)))
        frame = norm(ballot)
        if frame.height == 0:
            continue
        ekey = (ballot.family, ballot.kind)
        if ekey not in elections:
            id = elections[ekey] = key("election", day, *ekey)
            title_en, title_ka = FAMILY[ballot.family][2:]
            suffix_en, suffix_slug, suffix_ka = KIND[ballot.kind]
            if ballot.kind == "by_election":
                title_en, title_ka = title_en.replace("election", "").strip() + suffix_en, title_ka + suffix_ka
            elif ballot.kind in ("runoff", "special"):
                title_en, title_ka = title_en + suffix_en, title_ka + suffix_ka
            p.add(
                "election",
                id=id,
                polity_id=GEORGIA,
                kind=ballot.kind,
                date_from=day,
                date_to=day,
                status="held",
                rerun_of_id=key("election", date(2016, 10, 8), "parl", "general") if ballot.kind == "special" else None,
            )
            if ballot.kind == "special":
                p.external.add(key("election", date(2016, 10, 8), "parl", "general"))
            p.name(id, f"{day:%-d %B %Y} {title_en}".replace("  ", " "), "en", "english")
            p.name(id, f"{day.year} წლის {day.day} {MONTHS[day.month - 1]} {title_ka}", "ka", "official")
            p.add(
                "slug",
                path=f"ge/{day}-{FAMILY[ballot.family][0]}{suffix_slug}-precincts",
                entity_id=id,
                is_current=True,
            )
        election = elections[ekey]
        short += contests(p, day, ballot, frame, election, known, labels, units)
        over |= set(frame.filter(pl.col("voted") > pl.col("registered"))["unit"].to_list())
    if over:
        p.accept(
            "accounting.turnout",
            f"The protocols of {len(over)} precincts print more voters than registered voters; the figures are "
            "stored as printed.",
        )
    if short:
        p.accept("accounting.declared", " ".join(short))
    return p


def contests(
    p: Package,
    day: date,
    ballot: Ballot,
    frame: pl.DataFrame,
    election: str,
    known: dict[str, dict[str, object]],
    labels: dict[str, dict[str, object]],
    units: dict[str, str],
) -> list[str]:
    for code in frame["unit"].unique().sort().to_list():
        if code not in units:
            unit = units[code] = key("unit", day, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=GEORGIA,
                scheme_code=SCHEME,
                code=f"{day}:{code}",
                area_relation="unlocated",
                area_id=None,
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
    first_groups: set[str] = set()
    if ballot.first:
        first = FIRST_ROUNDS[ballot.first]
        first_groups = (
            {g for g in norm(first)["group"].unique().to_list() if g is not None}
            if first.role != "president"
            else set()
        )
    role = ballot.role
    totals = declared(ballot) if district_file(ballot) else None
    won = seats(ballot)
    short: list[str] = []
    # Where the file's invalid ballots equal the voters minus the votes in every precinct, the compiler derived them.
    closing = frame.group_by("group", "unit").agg(
        (pl.col("voted").first() - pl.col("votes").sum() - pl.col("invalid").first()).alias("gap")
    )
    derived = bool((closing["gap"] == 0).all())
    value_frames: list[pl.DataFrame] = []
    for group in frame["group"].unique(maintain_order=True).to_list() if role not in ("president", "list") else [None]:
        here = frame if group is None else frame.filter(pl.col("group") == group)
        tag = f"{ballot.stem}:{group}" if group is not None else ballot.stem
        contest = key("contest", day, tag)
        name_en, name_ka = ROLE_NAME[role]
        if group is not None:
            name_en, name_ka = f"{name_en} {group}", f"{name_ka} {group}"
        if role == "president":
            name_en = f"President, round {ballot.round}" if ballot.kind == "runoff" or ballot.round > 1 else "President"
        if ballot.round > 1 and role != "president":
            name_en += ", runoff"
            name_ka += ", მეორე ტური"
        kind = {
            "president": "office",
            "mayor": "office",
            "list": "list_allocation",
            "council_pr": "list_allocation",
        }.get(role, "body_seats")
        previous = None
        if ballot.first and (role == "president" or group in first_groups):
            first = FIRST_ROUNDS[ballot.first]
            previous = key("contest", first.day, f"{first.stem}:{group}" if group is not None else first.stem)
            p.external.add(previous)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            office_id=PRESIDENT if role == "president" else MAYOR if role == "mayor" else None,
            body_id=PARLIAMENT
            if ballot.family == "parl" and role in ("list", "smd")
            else AJARA
            if ballot.family == "adj"
            else COUNCILS
            if role in ("council_pr", "council_smd")
            else None,
            district_area_id=COUNTRY if group is None and ballot.family != "adj" else None,
            magnitude=sum(won.values())
            if won
            else (1 if role in ("smd", "mayor", "council_smd", "president") else None),
            round_number=ballot.round,
            previous_round_id=previous,
            electoral_system_id=SYSTEM_PRESIDENT if role == "president" else None,
            default_result_kind="counted",
        )
        p.name(contest, name_en, "en", "english")
        p.name(contest, name_ka, "ka", "official")
        slug_group = f"-{group}" if group is not None else ""
        p.add(
            "slug",
            path=f"ge/{day}-{role.replace('_', '-')}{slug_group}-r{ballot.round}-{ballot.stem}".lower(),
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md="Precinct protocol totals of the Central Election Commission, as compiled in the Georgia Elections "
            "Data Archive. Registered voters, voters who took part, and invalid ballots are those the precinct "
            "protocol prints for this ballot; valid votes are not stored.",
            lang="en",
        )
        official = (
            totals.filter(pl.col("group").is_null() if group is None else pl.col("group") == group)
            if totals is not None
            else None
        )
        lines = here.select("party", "name")
        if official is not None:
            lines = pl.concat([lines, official.select("party", "name")])
        lines = lines.unique(maintain_order=True).sort("party", "name", nulls_last=True)
        options: dict[tuple[str, str | None], str] = {}
        for position, row in enumerate(lines.iter_rows(named=True), 1):
            line = (row["party"], row["name"])
            option = options[line] = key("option", contest, row["party"], row["name"])
            item = labels.get(row["party"], {})
            candidate = role not in ("list", "council_pr")
            label = text(row["name"]) or text(str(item.get("ka") or "")) or humanize(row["party"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if row["party"] in ("other", "other_1992") else ("candidate" if candidate else "list"),
                party_id=party(row["party"]) if row["party"] in known else None,
                ballot_position=position,
                ballot_label=label,
            )
            if candidate:
                person = key("person", contest, row["party"], row["name"])
                p.add("person", id=person)
                person_ka = text(row["name"]) or text(str(item.get("ka") or "")) or None
                if person_ka:
                    p.name(person, person_ka, "ka", "official")
                if item.get("type") == "candidate" and item.get("en"):
                    p.name(person, text(str(item["en"])), "en", "english")
                if person_ka or item.get("en"):
                    affiliation = str(item.get("party") or row["party"])
                    p.add(
                        "candidacy",
                        id=key("candidacy", contest, row["party"], row["name"]),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=person_ka or text(str(item.get("en") or "")),
                        member_of_party_id=party(affiliation) if affiliation in known else None,
                        is_independent=row["party"].startswith(("independent", "initiative")),
                    )
                else:
                    p.add(
                        "candidacy",
                        id=key("candidacy", contest, row["party"], row["name"]),
                        person_id=person,
                        option_id=option,
                        role="head",
                    )
        for line, number in won.items():
            if (line, None) not in options:
                raise ValueError(f"{ballot.stem}: {line} won seats but has no list")
            p.add("outcome", result_set_id=result_set, option_id=options[(line, None)], kind="won", seats=number)
        keyed = pl.DataFrame(
            [(a, b_, c) for (a, b_), c in options.items()],
            schema={"party": pl.String, "name": pl.String, "option": pl.String},
            orient="row",
        )
        votes = here.join(keyed, on=["party", "name"], how="left", nulls_equal=True).with_columns(
            unit_id=pl.col("unit").replace_strict(units, return_dtype=pl.String), result_set_id=pl.lit(result_set)
        )
        repeats = votes.group_by("unit", "option").len().filter(pl.col("len") > 1)
        if repeats.height:
            raise ValueError(f"{ballot.stem} {group}: {repeats.height} precinct lines repeat: {repeats.head(3)}")
        value_frames.append(
            votes.select("result_set_id", "unit_id", option_id="option", value="votes").with_columns(
                measure=pl.lit(1, pl.Int16)
            )
        )
        turnout = here.group_by("unit").agg(
            pl.col("registered").first(),
            pl.col("voted").first(),
            pl.col("invalid").first(),
            pl.col("registered").n_unique().alias("r"),
            pl.col("voted").n_unique().alias("v"),
            pl.col("invalid").n_unique().alias("i"),
        )
        if (turnout["r"] > 1).any() or (turnout["v"] > 1).any() or (turnout["i"] > 1).any():
            raise ValueError(f"{ballot.stem} {group}: turnout differs between lines of one precinct")
        for column, measure in (("registered", REGISTERED), ("voted", VOTERS), ("invalid", INVALID)):
            kept = turnout.filter(pl.col(column).is_not_null() & (pl.col(column) >= 0))
            value_frames.append(
                kept.select(
                    result_set_id=pl.lit(result_set),
                    unit_id=pl.col("unit").replace_strict(units, return_dtype=pl.String),
                    option_id=pl.lit(None, pl.String),
                    value=pl.col(column),
                    flags=pl.lit(1 if column == "invalid" and derived else 0, pl.Int16),
                ).with_columns(measure=pl.lit(measure, pl.Int16))
            )
        if official is not None and official.height:
            matched = official.join(keyed, on=["party", "name"], how="left", nulls_equal=True)
            gap = (
                matched.join(
                    votes.group_by("option").agg(pl.col("votes").sum().alias("counted")), on="option", how="left"
                )
                .with_columns(gap=pl.col("votes") - pl.col("counted").fill_null(0))
                .filter(pl.col("gap") != 0)
            )
            if gap.height:
                detail = ", ".join(f"{row['party']} {row['gap']:,}" for row in gap.head(6).iter_rows(named=True))
                short.append(
                    f"{ballot.stem} ({group or 'national'}): the precinct protocols trail the district-level totals "
                    f"of the Commission by {int(gap['gap'].sum()):,} votes across {gap.height} lines ({detail})."
                )
            p.frame(
                "declared",
                matched.select(
                    result_set_id=pl.lit(result_set),
                    option_id="option",
                    measure=pl.lit(1, pl.Int16),
                    value="votes",
                ),
            )
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=CEC,
            locator={"file": f"results/{ballot.stem}_precincts.csv"},
        )
    if value_frames:
        p.frame(
            "result_value",
            pl.concat(value_frames, how="diagonal").with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.col("flags").fill_null(0).cast(pl.Int16),
            ),
        )
    return short


def assembly() -> Package:
    """The 14 to 16 February 1919 Constituent Assembly election, by electoral district."""
    day = ASSEMBLY_DAY
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The 1919 Constituent Assembly election of the Democratic Republic of Georgia by electoral district",
        depends=[REGISTRY],
    )
    p.external |= {GEORGIA, COUNTRY}
    p.uses(raw.cached(SOURCE, ASSEMBLY))
    p.uses(raw.cached(SOURCE, url("config/elections/parliamentary/parl_1919.yml")))
    frame = pl.read_csv(raw.cached(SOURCE, ASSEMBLY).path, infer_schema_length=0).with_columns(
        pl.col("votes", "registered", "voted", "invalid_ballots").cast(pl.Int64)
    )
    known = registered()
    election = key("election", day, "parl", "general")
    p.add(
        "election",
        id=election,
        polity_id=GEORGIA,
        kind="general",
        date_from=day,
        date_to=date(1919, 2, 16),
        status="held",
    )
    p.name(election, "14 to 16 February 1919 Constituent Assembly election", "en", "english")
    p.name(election, "1919 წლის 14-16 თებერვლის დამფუძნებელი კრების არჩევნები", "ka", "official")
    p.add("slug", path="ge/1919-02-14-constituent-assembly-districts", entity_id=election, is_current=True)
    contest = key("contest", day, "constituent-assembly")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=CONSTITUENT,
        district_area_id=COUNTRY,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Constituent Assembly party lists", "en", "english")
    p.name(contest, "დამფუძნებელი კრების პარტიული სიები", "ka", "official")
    p.add("slug", path="ge/1919-02-14-constituent-assembly-lists", entity_id=contest, is_current=True)
    result_set = key("result_set", contest)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ARCHIVE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md="District totals from the protocols of the Central Historical Archive of Georgia (fund 1834), as "
        "compiled in the Georgia Elections Data Archive. The archive holds no precinct figures. Registered voters, "
        "voters who took part, and invalid ballots are those the district protocol prints; the seats each party "
        "won are not stored because the compiled figures add up to 128 of the Assembly's 130.",
        lang="en",
    )
    options: dict[str, str] = {}
    for position, line in enumerate(sorted(frame["party_id"].unique().to_list()), 1):
        options[line] = key("option", contest, line)
        item = names().get(line, {})
        p.add(
            "contest_option",
            id=options[line],
            contest_id=contest,
            kind="list",
            party_id=party(line) if line in known else None,
            ballot_position=position,
            ballot_label=text(str(item.get("ka") or "")) or humanize(line),
        )
    units: dict[str, str] = {}
    for code in sorted(frame["district_id"].unique().to_list(), key=int):
        units[code] = key("unit", day, code)
        p.add(
            "reporting_unit",
            id=units[code],
            polity_id=GEORGIA,
            scheme_code=DISTRICT_SCHEME,
            code=f"{day}:{code}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
            valid_from=day,
            valid_to=date(1919, 2, 17),
        )
    rows = [
        {
            "result_set_id": result_set,
            "unit_id": units[row["district_id"]],
            "option_id": options[row["party_id"]],
            "measure": 1,
            "value": row["votes"],
        }
        for row in frame.iter_rows(named=True)
    ]
    for district in (
        frame.group_by("district_id")
        .agg(pl.col("registered").first(), pl.col("voted").first(), pl.col("invalid_ballots").first())
        .iter_rows(named=True)
    ):
        for column, measure in (("registered", REGISTERED), ("voted", VOTERS), ("invalid_ballots", INVALID)):
            if district[column] is not None:
                rows.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": units[district["district_id"]],
                        "option_id": None,
                        "measure": measure,
                        "value": district[column],
                    }
                )
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema_overrides={"option_id": pl.String}).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=ARCHIVE,
        locator={"file": "results/parl1919_pr.csv"},
    )
    return p
