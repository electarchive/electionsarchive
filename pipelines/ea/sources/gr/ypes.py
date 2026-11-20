import functools
import json
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as default
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "gr.ypes"
HOME = "https://ekloges.ypes.gr/"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
VOTES, REGISTERED, VALID, INVALID, BLANK = 1, 2, 4, 5, 6
ACCOUNTING = ((REGISTERED, "Gramenoi"), (VALID, "Egkyra"), (INVALID, "Akyra"), (BLANK, "Leyka"))


@dataclass(frozen=True)
class Year:
    bundle: str  # the saved set of result files, as the raw cache keys it
    first: date
    second: date
    majority: float  # share of valid votes a first-round winner needs


SERVICE = "https://ekloges-prev.singularlogic.eu"
LEGEND = (
    "https://web.archive.org/web/20250319191034id_/"
    "http://ekloges-prev.singularlogic.eu/2023/october/d/home/dist/model.el.js"
)
LEGEND_2019 = (
    "https://web.archive.org/web/20240422123316id_/http://ekloges-prev.singularlogic.eu/2019/d/home/dist/model.el.js"
)
# The 2010 and 2014 sites have no text legend: each draws a support bit as a colored block and a party id as a colored
# figure, and these pages hold those images and the party logos.
IMAGES = {
    2010: "https://ekloges-prev.singularlogic.eu/dn2010/images/stirixi/flat/z{sid}.png",
    2014: "https://ekloges-prev.singularlogic.eu/may2014/images/stirixi/z{sid}.png",
}
YEARS: dict[int, Year] = {
    2010: Year(f"{SERVICE}/dn2010/{{stat,dyn}}/snom_n_{{id}}.js", date(2010, 11, 7), date(2010, 11, 14), 0.5),
    2014: Year(f"{SERVICE}/may2014/{{stat,dyn}}/dn/snom_n_{{id}}.js", date(2014, 5, 18), date(2014, 5, 25), 0.5),
    2019: Year(f"{SERVICE}/2019/{{stat,dyn}}/dn/snom_n_{{id}}.js", date(2019, 5, 26), date(2019, 6, 2), 0.5),
    2023: Year(f"{SERVICE}/2023/october/{{stat,dyn}}/dn/snom_n_{{id}}.js", date(2023, 10, 8), date(2023, 10, 15), 0.43),
}
METHOD = (
    "Counted by the Ministry of Interior's results service per municipal unit (dimotiki enotita) for municipalities "
    "and per regional unit (perifereiaki enotita) for regions, as published in the service's data files. Voters "
    "vote for one ticket: a mayoral or regional governor candidate and the council list that runs with them. "
    "Registered voters are the electorate the service lists, valid votes are the ballots that name a ticket, and "
    "blank ballots are counted apart from valid ones. Polling-station tables are not in the files the archive "
    "holds, so the municipal unit is the finest unit stored."
)
SEATS = (
    "Council seats are stored as the service prints them in the seats column of this round. The service does not "
    "say how the seats are allocated between the rounds."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
MUNICIPAL_COUNCIL = key("body", "municipal_council")
REGIONAL_COUNCIL = key("body", "regional_council")
MAYOR = key("office", "mayor")
GOVERNOR = key("office", "governor")


def system(year: int) -> str:
    return key("electoral_system", "two_round", 2023 if year >= 2023 else 2010)


ROMAN = [
    "a",
    "v",
    "g",
    "d",
    "e",
    "z",
    "i",
    "th",
    "i",
    "k",
    "l",
    "m",
    "n",
    "x",
    "o",
    "p",
    "r",
    "s",
    "t",
    "y",
    "f",
    "ch",
    "ps",
    "o",
]
GREEK = {**dict(zip("αβγδεζηθικλμνξοπρστυφχψω", ROMAN, strict=True)), "ς": "s"}
DIGRAPHS = {"ου": "ou", "αυ": "au", "ευ": "ev", "ηυ": "iy", "γγ": "ng", "γκ": "gk", "γχ": "nch", "μπ": "mp", "ντ": "nt"}


def latin(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    for pair, latin in DIGRAPHS.items():
        folded = folded.replace(pair, latin)
    spelled = "".join(GREEK.get(c, c) for c in folded)
    return re.sub(r"[^a-z0-9]+", "-", spelled).strip("-")


def clean(text: str | None) -> str:
    text = (text or "").replace("\x96", "–").replace("\x93", "“").replace("\x94", "”")
    text = re.sub(r"(?<![^\W\d_])’(?=[α-ω])", "Ά", text)
    return " ".join(text.split())


# ---------------------------------------------------------------- raw files


def fetch() -> None:
    """Reads the files saved from the results service into the raw cache.

    The service no longer answers scripts, so each election's result files were saved as one JSON-lines document
    (`{"url": ..., "data": ...}` per file). Point EA_GR_YPES_INBOX at the folder that holds them and its
    `index.json`, which maps each URL to its `file`, `retrieved_at`, `media_type`, and, when a copy was read from
    an archive, the `source` it came from. The party legend of the October 2023 site is stored with them.
    """
    inbox = Path(os.environ["EA_GR_YPES_INBOX"])
    index: dict[str, dict[str, str]] = json.loads((inbox / "index.json").read_text())
    wanted = {year.bundle for year in YEARS.values()} | {LEGEND, LEGEND_2019, *IMAGES.values()}
    for url, entry in index.items():
        origin = entry.get("source", url)
        if origin not in wanted:
            continue
        data = (inbox / entry["file"]).read_bytes()
        raw.store(
            SOURCE,
            origin,
            data,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def artifacts(year: int) -> list[raw.Artifact]:
    found = [raw.cached(SOURCE, YEARS[year].bundle)]
    if year == 2019:
        found.append(raw.cached(SOURCE, LEGEND_2019))
    elif year in IMAGES:
        found.append(raw.cached(SOURCE, IMAGES[year]))
    else:
        found.append(raw.cached(SOURCE, LEGEND))
    return found


@functools.cache
def files(year: int) -> Mapping[str, Any]:
    """Every file of one election, parsed, keyed by its name (`dyn/dhm_d_9001.js` becomes `dyn/dhm_d_9001`)."""
    out: dict[str, Any] = {}
    for line in raw.cached(SOURCE, YEARS[year].bundle).path.read_text(encoding="utf-8").split("\n"):
        if not line:
            continue
        row = json.loads(line)
        found = re.search(r"/(dyn|stat)/(?:dn/)?((?:dhm_d|snom_n|epikratia|dhm_synoptiki)\w*)\.js$", row["url"])
        if found is not None:
            out[f"{found[1]}/{found[2]}"] = row["data"]
    return out


# ---------------------------------------------------------------- party support


@dataclass(frozen=True)
class Support:
    """How one election's site writes a ticket's `ST_ID`.

    The code is 1000 times a bit set of the parties that back the ticket plus the id of the party its candidate
    belongs to. Each election has its own bits and its own party ids.
    """

    bits: Mapping[int, int]  # support bit -> id of the party it stands for
    other: int  # bit for parties the legend does not name, or 0
    independent: int  # candidate id for an independent
    unnamed: int = 0  # bits the site draws but no file names
    renumbered: Mapping[int, int] = default(default_factory=dict)  # ids the site rewrites to the legend's id
    other_only: int = 0  # whole bit set that means only other parties back the ticket


INDEPENDENT = 998  # the 2023 legend's entry for independents, which no row stands for

SUPPORTS: dict[int, Support] = {
    # Read from the colors of the support blocks (z{sid}.png) and party figures (p{pid}.png) on the 2010 site, whose
    # party logos name the parties.
    2010: Support({1: 1, 2: 2, 4: 3, 8: 4, 16: 16, 32: 28, 64: 56}, 0, 998),
    # The same reading for 2014. Bit 128 and the party the site gives id 90, with bit 256, are drawn but not named.
    2014: Support({1: 2, 2: 4, 4: 41, 8: 56, 16: 3}, 1024, 997, unnamed=128 | 256, renumbered={90: 0}, other_only=997),
    # From the `parseStirixi` function of the 2019 site and the one of the October 2023 site.
    2019: Support({1: 4, 2: 2, 4: 106, 8: 41, 16: 3, 32: 28, 128: 44, 256: 100}, 64, 998, renumbered={75: 44, 15: 28}),
    2023: Support({1: 2, 2: 4, 4: 106, 8: 3, 64: 100, 128: 157, 256: 44}, 16, 998, renumbered={75: 44, 15: 28}),
}
# Parties the 2019 legend or a logo names that the 2023 legend does not. A logo's own text is the name.
LOGOS = {1: "ΠΑΣΟΚ", 56: "Δημοκρατική Αριστερά"}


def _table(text: str, start: str, end: str) -> dict[int, tuple[str, str, str]]:
    table = text[text.index(start) : text.index(end, text.index(start))]
    found = re.findall(
        r"(\d+):\{id:\1,name:(?:\"(.*?)\"|'(.*?)'),shortName:\"(.*?)\"(?:,color:\"([0-9A-Fa-f]{6})\")?\}", table
    )
    return {int(n): (clean(a or b), clean(c), color.lower()) for n, a, b, c, color in found}


@functools.cache
def legend() -> dict[int, tuple[str, str, str]]:
    """The party table of the 2023 site: id -> name, short name, color."""
    text = raw.cached(SOURCE, LEGEND).path.read_text(encoding="utf-8")
    return _table(text, "s={2:{id:2,", "},g={1:{id:1,")


@functools.cache
def legend_2019() -> dict[int, tuple[str, str, str]]:
    """The party table of the 2019 site."""
    text = raw.cached(SOURCE, LEGEND_2019).path.read_text(encoding="utf-8")
    return _table(text, "s={4:{id:4,", "},g={")


@functools.cache
def parties() -> dict[int, tuple[str, str]]:
    """Every party a ticket's code can name: id -> name, short name. The newest legend that names one wins."""
    out: dict[int, tuple[str, str]] = {number: (name, name) for number, name in LOGOS.items()}
    used = {number for support in SUPPORTS.values() for number in support.bits.values()}
    for number, (name, short, _) in legend_2019().items():
        if number in used:
            out[number] = (name, short)
    for number, (name, short, _) in legend().items():
        if number != INDEPENDENT:
            out[number] = (name, short)
    return out


@dataclass(frozen=True)
class Code:
    member: int | None  # party the candidate belongs to
    independent: bool
    backers: list[int]  # parties that back the ticket and that the legend names
    others: bool  # whether parties the legend does not name back it too


def decode(year: int, code: int) -> Code:
    """Splits a ticket's `ST_ID` into its candidate's party and the parties backing it."""
    rule = SUPPORTS[year]
    party, support = code % 1000, code // 1000
    party = rule.renumbered.get(party, party)
    if support == rule.other_only:
        support = rule.other
    if support & ~(rule.other | rule.unnamed | sum(rule.bits)):
        raise ValueError(f"unknown support bits in {code} of {year}")
    backers = [number for bit, number in rule.bits.items() if support & bit]
    others = bool(support & (rule.other | rule.unnamed))
    return Code(None if party in (rule.independent, 0) else party, party == rule.independent, backers, others)


def party_id(number: int) -> str:
    return key("party", number)


@dataclass(frozen=True)
class Race:
    """One municipality or region: what it was called, who stood, and what each unit counted."""

    id: int
    name: str
    tier: str  # municipality or region
    parent: int  # regional unit of a municipality, or 0 for a region
    region: int
    tickets: dict[int, dict[str, str]]  # candidate number -> printed candidate and ticket names
    codes: dict[int, int]  # candidate number -> the stat file's party-support code, where it has one
    total: dict[str, Any]
    candidates: list[dict[str, Any]]
    units: list[tuple[int, str, dict[str, Any], dict[int, dict[str, int]]]]  # id, name, counts, votes by candidate


def _remap(race: dict[str, Any]) -> dict[str, int]:
    """Maps the candidate keys of a race's units to its candidate numbers.

    The units of some races list candidates under keys that are not the race's own candidate numbers, so a key
    is matched to the candidate whose votes in both rounds equal the key's sum over the units.
    """
    keys = list(race["ep"][0]["cds"])
    sums = {k: tuple(sum(e["cds"][k][f"{r}VOTES"] for e in race["ep"]) for r in "AB") for k in keys}
    totals = {str(c["CAND_ID"]): (c["AVOTES"], c["BVOTES"]) for c in race["cds"]}
    if all(totals.get(k) == v for k, v in sums.items()):
        return {k: int(k) for k in keys}
    by_votes: dict[tuple[int, int], list[str]] = {}
    for number, votes in totals.items():
        by_votes.setdefault(votes, []).append(number)
    mapped: dict[str, int] = {}
    for k, votes in sums.items():
        found = by_votes.get(votes, [])
        if len(found) != 1:
            raise ValueError(f"race {race.get('DHM_ID') or race.get('SNOM_ID')}: key {k} matches {found}")
        mapped[k] = int(found[0])
    if sorted(mapped.values()) != sorted(int(n) for n in totals):
        raise ValueError(f"race {race.get('DHM_ID') or race.get('SNOM_ID')}: keys map to {mapped}")
    return mapped


def races(year: int) -> list[Race]:
    data = files(year)
    out: list[Race] = []
    for tier, dyn_name, stat_name in (("municipality", "dhm_d", "dhm_d"), ("region", "snom_n", "snom_n")):
        ids = sorted(int(k.split("_")[-1]) for k in data if k.startswith(f"dyn/{dyn_name}_"))
        for number in ids:
            dyn, stat = data[f"dyn/{dyn_name}_{number}"], data[f"stat/{stat_name}_{number}"]
            moved = _remap(dyn)
            units = []
            for unit in dyn["ep"]:
                votes = {moved[k]: {"A": v["AVOTES"], "B": v["BVOTES"]} for k, v in unit["cds"].items()}
                units.append(
                    (unit.get("DEP_ID") or unit.get("DEN_ID") or unit["PEN_ID"], clean(unit["Descr"]), unit, votes)
                )
            out.append(
                Race(
                    id=number,
                    name=clean(stat["Descr"]),
                    tier=tier,
                    parent=int(stat.get("PEN_ID") or 0),
                    region=int(stat.get("SNOM_ID") or number),
                    tickets={
                        int(k): {"name": clean(v["Descr"]), "ticket": clean(v["Synd"])} for k, v in stat["cds"].items()
                    },
                    codes={int(k): int(v["ST_ID"]) for k, v in stat["cds"].items() if v.get("ST_ID") is not None},
                    total=dyn,
                    candidates=dyn["cds"],
                    units=units,
                )
            )
    return out


# ---------------------------------------------------------------- geography


def region_area(number: int) -> str:
    return key("area", "gr.perifereia", number)


def unit_area(number: int) -> str:
    return key("area", "gr.perifereiaki_enotita", number)


def town_area(number: int) -> str:
    return key("area", "gr.dimos", number)


def packages() -> list[str]:
    return ["gr.ypes/geo", "gr.ypes/registry", *(f"gr.ypes/local-{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        local(year).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        "gr.ypes/geo",
        SOURCE,
        "Greece's 13 regions, 74 regional units, and 332 municipalities as the Ministry of Interior's results "
        "service lists them",
    )
    p.external |= {GREECE, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών",
        title="Local government election results service",
        url=HOME,
        license="Not stated by the publisher",
        attribution="Υπουργείο Εσωτερικών",
    )
    for year in YEARS:
        for artifact in artifacts(year):
            p.artifact(artifact, SOURCE_ID)
    for code, rank, noun in (
        ("gr.perifereia", 1, "region"),
        ("gr.perifereiaki_enotita", 2, "regional unit"),
        ("gr.dimos", 3, "municipality"),
    ):
        p.add("scheme", code=code, polity_id=GREECE, kind="administrative", rank=rank, noun=noun)
    p.add("scheme", code="gr.dimotiki_enotita", polity_id=GREECE, kind="precinct", rank=4, noun="municipal unit")
    latest = max(YEARS)
    regions: dict[int, str] = {}
    pens: dict[int, tuple[str, int]] = {}
    towns: dict[int, tuple[str, int]] = {}
    for race in races(latest):
        if race.tier == "region":
            regions[race.id] = race.name
            for number, name, _, _ in race.units:
                pens[number] = (name, race.id)
        else:
            towns[race.id] = (race.name, race.parent)
    for number, name in sorted(regions.items()):
        id = region_area(number)
        p.add("area", id=id, scheme_code="gr.perifereia")
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "el", "official")
    for number, (name, parent) in sorted(pens.items()):
        id = unit_area(number)
        p.add("area", id=id, scheme_code="gr.perifereiaki_enotita")
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=region_area(parent))
        p.name(id, name, "el", "official")
    for number, (name, parent) in sorted(towns.items()):
        id = town_area(number)
        p.add("area", id=id, scheme_code="gr.dimos")
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=unit_area(parent))
        p.name(id, name, "el", "official")
    return p


def registry() -> Package:
    p = Package(
        "gr.ypes/registry",
        SOURCE,
        "Greek municipal and regional councils, the mayoralty and the regional governorship, and their two-round "
        "rules from the Ministry of Interior's results service",
        depends=["gr.ypes/geo"],
    )
    p.external |= {GREECE, COUNTRY}
    for url in (LEGEND, LEGEND_2019, *IMAGES.values()):
        p.uses(raw.cached(SOURCE, url))
    # The legend's colors are left out: the party rows are shared with the national results registry, and another
    # pipeline already holds an open-ended color for some of them, which a second color here would clash with.
    for number, (name, short) in sorted(parties().items()):
        p.add("party", id=party_id(number), kind="party", polity_id=GREECE)
        p.name(party_id(number), name, "el", "official")
        if short != name:
            p.name(party_id(number), short, "el", "abbreviation", False)
    p.add("body", id=MUNICIPAL_COUNCIL, polity_id=GREECE, kind="council")
    p.name(MUNICIPAL_COUNCIL, "Municipal council", "en", "official")
    p.name(MUNICIPAL_COUNCIL, "Δημοτικό συμβούλιο", "el", "native", False)
    p.add("body", id=REGIONAL_COUNCIL, polity_id=GREECE, kind="council")
    p.name(REGIONAL_COUNCIL, "Regional council", "en", "official")
    p.name(REGIONAL_COUNCIL, "Περιφερειακό συμβούλιο", "el", "native", False)
    p.add("office", id=MAYOR, polity_id=GREECE, body_id=MUNICIPAL_COUNCIL, kind="mayor")
    p.name(MAYOR, "Mayor", "en", "official")
    p.name(MAYOR, "Δήμαρχος", "el", "native", False)
    p.add("office", id=GOVERNOR, polity_id=GREECE, body_id=REGIONAL_COUNCIL, kind="governor")
    p.name(GOVERNOR, "Regional governor", "en", "official")
    p.name(GOVERNOR, "Περιφερειάρχης", "el", "native", False)
    for since, until, majority, threshold in (
        (date(2010, 1, 1), date(2023, 1, 1), 0.5, "more than half of the valid votes"),
        (date(2023, 1, 1), None, 0.43, "43% or more of the valid votes"),
    ):
        text = (
            f"A mayor or regional governor is elected in the first round with {threshold}. Otherwise the two "
            "leading tickets go to a second round a week later and the one with more votes wins."
        )
        id = key("electoral_system", "two_round", 2023 if since.year >= 2023 else 2010)
        p.add(
            "electoral_system",
            id=id,
            polity_id=GREECE,
            valid_from=since,
            valid_to=until,
            rules={"formula": "two_round", "majority": majority, "advance": 2},
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


# ---------------------------------------------------------------- elections


def _won(votes: Mapping[int, int]) -> list[int]:
    best = max(votes.values())
    return [n for n, v in votes.items() if v == best]


def local(year: int) -> Package:
    spec = YEARS[year]
    p = Package(
        f"gr.ypes/local-{year}",
        SOURCE,
        f"The {year} Greek municipal and regional elections, both rounds, by municipal unit and regional unit from "
        "the Ministry of Interior's results service",
        depends=["gr.ypes/geo", "gr.ypes/registry"],
    )
    p.external |= {GREECE, COUNTRY}
    for artifact in artifacts(year):
        p.uses(artifact)
    everything = races(year)
    stale: list[str] = []
    ties: list[str] = []
    slugs: dict[str, int] = {}
    for tier, noun in (("municipality", "municipal"), ("region", "regional")):
        rows = [r for r in everything if r.tier == tier]
        previous: dict[tuple[int, str], str] = {}
        for round_number, day in ((1, spec.first), (2, spec.second)):
            letter = "AB"[round_number - 1]
            held = [r for r in rows if r.total["BEgkyra"] > 0] if round_number == 2 else rows
            if not held:
                continue
            election = key("election", noun, year, round_number)
            path = f"gr/{day}-{noun}" + ("" if round_number == 1 else "-runoff")
            title = f"{year} Greek {noun} elections" + ("" if round_number == 1 else ", second round")
            p.add(
                "election",
                id=election,
                polity_id=GREECE,
                kind="local" if round_number == 1 else "runoff",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.add("slug", path=path, entity_id=election, is_current=True)
            p.name(election, title, "en", "official")
            native = "δημοτικές" if tier == "municipality" else "περιφερειακές"
            p.name(
                election,
                f"{native.capitalize()} εκλογές {year}" + ("" if round_number == 1 else ", β' γύρος"),
                "el",
                "native",
                False,
            )
            for race in held:
                runoff = race.total["BEgkyra"] > 0
                fin = [c for c in race.candidates if c["B"] == 1]
                contenders = (
                    [c for c in race.candidates] if round_number == 1 else sorted(fin, key=lambda c: c["CAND_ID"])
                )
                votes = {c["CAND_ID"]: c[f"{letter}VOTES"] for c in contenders}
                codes = {
                    **race.codes,
                    **{c["CAND_ID"]: c["ST_ID"] for c in race.candidates if c.get("ST_ID") is not None},
                }
                if round_number == 1 and not runoff:
                    winners = [race.total["WIN_ID"]]
                    outcome = {n: "elected" if n in winners else "lost" for n in votes}
                elif round_number == 1:
                    outcome = {n: "advanced" if n in {c["CAND_ID"] for c in fin} else "eliminated" for n in votes}
                else:
                    winners = _won({c["CAND_ID"]: c["BVOTES"] for c in fin})
                    outcome = {
                        n: ("tied" if len(winners) > 1 else "elected") if n in winners else "lost" for n in votes
                    }
                    if len(winners) > 1:
                        ties.append(race.name)
                    elif race.total["WIN_ID"] != winners[0]:
                        stale.append(race.name)
                base = f"{path}/{latin(race.name)}"
                if slugs.setdefault(base, race.id) != race.id:
                    base = f"{path}/{latin(race.name)}-{race.id}"
                office, body = (MAYOR, MUNICIPAL_COUNCIL) if tier == "municipality" else (GOVERNOR, REGIONAL_COUNCIL)
                district = town_area(race.id) if tier == "municipality" else region_area(race.id)
                seats = {c["CAND_ID"]: c[f"{letter}Edres"] for c in contenders if c[f"{letter}Edres"] > 0}
                moved = False
                if runoff and any(c["BEdres"] > 0 and c["B"] == 0 for c in race.candidates):
                    # The service prints the whole council in its second-round column and gives seats to lists
                    # that did not run again, so the seats sit on the first round, where every list is an option.
                    if any(c["AEdres"] > 0 for c in race.candidates):
                        raise ValueError(f"{year} {race.name}: seats in both rounds and a list outside the runoff")
                    moved = True
                    seats = (
                        {c["CAND_ID"]: c["BEdres"] for c in race.candidates if c["BEdres"] > 0}
                        if round_number == 1
                        else {}
                    )
                for kind in ("head", "council"):
                    if kind == "council" and not seats:
                        continue
                    contest = key("contest", noun, year, round_number, race.id, kind)
                    label = "mayor" if tier == "municipality" else "regional governor"
                    word = f"{label}" if kind == "head" else f"{noun} council"
                    english = f"{race.name}, {word}" + ("" if round_number == 1 else ", second round")
                    p.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="office" if kind == "head" else "list_allocation",
                        office_id=office if kind == "head" else None,
                        body_id=None if kind == "head" else body,
                        district_area_id=district,
                        magnitude=1 if kind == "head" else sum(seats.values()),
                        round_number=round_number,
                        previous_round_id=previous.get((race.id, kind)),
                        electoral_system_id=system(year) if kind == "head" else None,
                        default_result_kind="counted",
                    )
                    previous[(race.id, kind)] = contest
                    p.name(contest, english, "en", "official")
                    p.add(
                        "slug",
                        path=base if kind == "head" else f"{base}-council",
                        entity_id=contest,
                        is_current=True,
                    )
                    result_set = key("result_set", noun, year, round_number, race.id, kind)
                    p.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        source_id=SOURCE_ID,
                        status="active",
                        is_default=True,
                        completeness=1.0,
                    )
                    p.add(
                        "note",
                        id=key("note", result_set),
                        entity_id=result_set,
                        kind="methodology",
                        body_md=METHOD + ("" if kind == "head" else " " + SEATS),
                        lang="en",
                    )
                    caveats = []
                    if kind == "head" and round_number == 2 and len(winners) > 1:
                        caveats.append(
                            "The two tickets tied in the second round and the service names no winner, so both are "
                            "stored as tied."
                        )
                    if (
                        kind == "head"
                        and round_number == 2
                        and len(winners) == 1
                        and race.total["WIN_ID"] != winners[0]
                    ):
                        caveats.append(
                            "The service's winner field still names the leader of the first round although the "
                            "second-round votes put another ticket ahead. The outcome here follows the votes."
                        )
                    if kind == "council" and moved:
                        caveats.append(
                            "The service prints this council's seats in its second-round column, and gives seats to "
                            "lists that did not run in the second round. They are stored here, on the first round, "
                            "where every list is an option."
                        )
                    for index, text in enumerate(caveats):
                        p.add(
                            "note",
                            id=key("note", result_set, "caveat", index),
                            entity_id=result_set,
                            kind="caveat",
                            body_md=text,
                            lang="en",
                        )
                    options: dict[int, str] = {}
                    for number in votes:
                        ticket = race.tickets[number]
                        code = codes.get(number)
                        found = decode(year, code) if code else Code(None, False, [], False)
                        backers, others = found.backers, found.others
                        sole = backers[0] if len(backers) == 1 and not others else None
                        option = options[number] = key("option", noun, year, round_number, race.id, kind, number)
                        p.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate" if kind == "head" else "list",
                            ballot_position=number,
                            ballot_label=ticket["name"] if kind == "head" else ticket["ticket"],
                            party_id=party_id(sole) if sole else None,
                        )
                        if len(backers) + others > 1 or (others and backers):
                            named = ", ".join(parties()[n][1] for n in backers)
                            p.add(
                                "note",
                                id=key("note", option),
                                entity_id=option,
                                kind="caveat",
                                body_md=(
                                    "The service's party-support code for this ticket lists "
                                    + (f"{named}" + (" and other parties" if others else ""))
                                    + " as backing it, so no single party is linked to the ballot line."
                                ),
                                lang="en",
                            )
                        if kind == "head":
                            p.name(option, ticket["ticket"], "el", "ballot", False)
                        person = key("person", noun, year, race.id, number)
                        if round_number == 1 and kind == "head":
                            p.add("person", id=person)
                            p.name(person, ticket["name"], "el", "official")
                        p.add(
                            "candidacy",
                            id=key("candidacy", noun, year, round_number, race.id, kind, number),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=ticket["name"],
                            member_of_party_id=party_id(found.member) if found.member else None,
                            is_independent=found.independent,
                        )
                        if kind == "head":
                            p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome[number])
                        elif number in seats:
                            p.add(
                                "outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats[number]
                            )
                    for measure, field in ACCOUNTING:
                        p.add(
                            "declared",
                            result_set_id=result_set,
                            option_id=None,
                            measure=measure,
                            value=race.total[f"{letter}{field}"],
                        )
                    for number, option in options.items():
                        p.add(
                            "declared",
                            result_set_id=result_set,
                            option_id=option,
                            measure=VOTES,
                            value=votes[number],
                        )
                    for unit_number, name, counts, by_candidate in race.units:
                        code = f"{day}:{race.id}:{unit_number}"
                        unit = key("unit", noun, code)
                        if kind == "head":
                            if tier == "municipality":
                                p.add(
                                    "reporting_unit",
                                    id=unit,
                                    polity_id=GREECE,
                                    scheme_code="gr.dimotiki_enotita",
                                    code=code,
                                    area_relation="within",
                                    area_id=town_area(race.id),
                                    kind="standard",
                                )
                            else:
                                p.add(
                                    "reporting_unit",
                                    id=unit,
                                    polity_id=GREECE,
                                    scheme_code="gr.perifereiaki_enotita",
                                    code=code,
                                    area_relation="equals",
                                    area_id=unit_area(unit_number),
                                    kind="standard",
                                )
                            p.name(unit, name, "el", "official")
                        for measure, field in ACCOUNTING:
                            p.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=None,
                                channel=0,
                                measure=measure,
                                count_round=0,
                                flags=0,
                                value=counts[f"{letter}{field}"],
                            )
                        for number, option in options.items():
                            p.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=unit,
                                option_id=option,
                                channel=0,
                                measure=VOTES,
                                count_round=0,
                                flags=0,
                                value=by_candidate[number][letter],
                            )
    for line in (*(f"tie: {n}" for n in ties), *(f"winner field stale: {n}" for n in stale)):
        print(f"{year} {line}")
    return p


def totals(year: int) -> list[str]:
    """Compares the sums over every unit with the national totals the service prints."""
    data = files(year)
    printed = data["dyn/epikratia"]
    lines = []
    for tier, name in (("municipality", "dhm"), ("region", "snom")):
        rows = [r for r in races(year) if r.tier == tier]
        for letter in "AB":
            for field in ("Gramenoi", "Egkyra", "Akyra", "Leyka"):
                ours = sum(u[2][f"{letter}{field}"] for r in rows for u in r.units)
                theirs = printed[name][f"{letter}{field}"]
                lines.append(
                    f"{year} {tier} round {letter} {field}: units {ours:,}, national {theirs:,}, diff {ours - theirs:+,}"
                )
    return lines
