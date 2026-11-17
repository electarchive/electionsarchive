import functools
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.fi import early, pages, tilastokeskus

SOURCE = "fi.vaalit"
BASE = "https://tulospalvelu.vaalit.fi"
FINLAND = str(uuid5(package.NAMESPACE, "polity\x1fFI"))


@dataclass(frozen=True)
class Vote:
    """One election as the Ministry of Justice's results service publishes it."""

    page: str
    prefix: str
    day: date
    kind: str
    round: int
    parties: bool
    guide: str


VOTES = [
    Vote("EKV-2011", "e-2011", date(2011, 4, 17), "E", 1, True, "EKV2011_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("TPV-2012_1", "tp1-2012", date(2012, 1, 22), "P", 1, False, "TPV2012_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("TPV-2012_2", "tpv-2012_2", date(2012, 2, 5), "P", 2, False, "TPV2012_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("KV-2012", "kv-2012", date(2012, 10, 28), "K", 1, True, "KV2012_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("EPV-2014", "epv-2014", date(2014, 5, 25), "EP", 1, True, "EPV2014_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("E-2015", "e-2015", date(2015, 4, 19), "E", 1, True, "EKV2015_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("KV-2017", "kv-2017", date(2017, 4, 9), "K", 1, True, "KV2017_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("TPV-2018_1", "tpv-2018_1", date(2018, 1, 28), "P", 1, False, "TPV2018_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("EKV-2019", "ekv-2019", date(2019, 4, 14), "E", 1, True, "EKV-EPV-2019_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("EPV-2019", "epv-2019", date(2019, 5, 26), "EP", 1, True, "EKV-EPV-2019_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("KV-2021", "kv-2021", date(2021, 6, 13), "K", 1, True, "KV2021_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("AV-2022", "av-2022", date(2022, 1, 23), "A", 1, True, "AV2022_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("EKV-2023", "ekv-2023", date(2023, 4, 2), "E", 1, True, "EKV2023_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("TPV-2024_1", "tpv-2024_1", date(2024, 1, 28), "P", 1, False, "TPV2024_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("TPV-2024_2", "tpv-2024_2", date(2024, 2, 11), "P", 2, False, "TPV2024_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("EPV-2024", "epv-2024", date(2024, 6, 9), "EP", 1, True, "EPV2024_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("KV-2025", "kv-2025", date(2025, 4, 13), "K", 1, True, "AKV2025_CSV-tiedostojen_kuvaus_fi.pdf"),
    Vote("AV-2025", "av-2025", date(2025, 4, 13), "A", 1, True, "AKV2025_CSV-tiedostojen_kuvaus_fi.pdf"),
]


def url(vote: Vote, part: str) -> str:
    return f"{BASE}/{vote.page}/{vote.prefix}_{part}_maa.csv.zip"


def guide_url(vote: Vote) -> str:
    return f"{BASE}/{vote.page}/ohje/{vote.guide}"


def parts(vote: Vote) -> list[str]:
    return ["alu", "ehd", "puo"] if vote.parties else ["alu", "ehd"]


def files(vote: Vote) -> list[str]:
    return [*(url(vote, part) for part in parts(vote)), guide_url(vote)]


def fetch() -> None:
    tilastokeskus.fetch()
    for vote in VOTES:
        for address in files(vote):
            raw.fetch(SOURCE, address, suffix=".pdf" if address.endswith(".pdf") else ".zip")
    pages.fetch()


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


VOLBY = key("source", "results-service")
STATFIN = key("source", "statfin")
BOUNDARIES = key("source", "statistics-finland-boundaries")
LEVEL = {"E": "V", "EP": "M", "P": "M", "K": "K", "A": "R"}
NATIONAL = {"EP", "P"}
WIDTH = {"alu": 74, "ehd": 45, "puo": 53}
MEMBER = {"alu": "taat", "ehd": "teat", "puo": "tpat"}
REGISTERED, CAST, VALID, INVALID = 2, 3, 4, 5
VOTES_MEASURE, PREFERENCE = 1, 7

COUNTRY = key("area", "fi.maa", "FI")
PARLIAMENT = key("body", "eduskunta")
PRESIDENCY = key("office", "president")
PHASE = "T"

SCHEMES = (
    ("fi.maa", "administrative", 0, "country"),
    ("fi.vaalipiiri", "electoral_district", 1, "constituency"),
    ("fi.hyvinvointialue", "administrative", 1, "wellbeing services county"),
    ("fi.maakunta", "administrative", 1, "region"),
    ("fi.kunta", "administrative", 2, "municipality"),
    ("fi.aanestysalue", "precinct", 3, "voting district"),
    ("fi.kuntalaskenta", "precinct", 3, "municipal count"),
)


def system(name: str) -> str:
    return key("electoral_system", name)


SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        system("parliament"),
        date(1906, 1, 1),
        None,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        (
            "Each of the constituencies elects its members of the Eduskunta by D'Hondt among the candidate lists, "
            "which are open: a voter marks one candidate, the votes for a candidate count for the list, and the "
            "candidates of a list are ranked by their own votes. The constituency of Åland elects one member, the "
            "candidate with the most votes. A member who leaves is replaced by the next candidate of the list by votes."
        ),
    ),
    (
        system("aland"),
        date(1948, 1, 1),
        None,
        {"formula": "fptp"},
        (
            "The constituency of Åland elects one member of the Eduskunta. A voter marks one candidate, and the "
            "candidate with the most votes is elected."
        ),
    ),
    (
        system("european"),
        date(1995, 1, 1),
        None,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        (
            "Finland is one constituency for the European Parliament. Seats go to the candidate lists by D'Hondt, with "
            "no threshold. A voter marks one candidate, the votes for a candidate count for the list, and the "
            "candidates of a list are ranked by their own votes."
        ),
    ),
    (
        system("local"),
        date(2004, 10, 24),
        None,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        (
            "Each municipality, and from 2022 each wellbeing services county, is one constituency whose council seats "
            "go to the candidate lists by D'Hondt. A voter marks one candidate, the votes for a candidate count for "
            "the list, and the candidates of a list are ranked by their own votes. The validity start marks the first "
            "election the archive holds from this source."
        ),
    ),
    (
        system("kainuu"),
        date(2004, 10, 24),
        None,
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        (
            "The region of Kainuu elects its regional council, whose seats go to the candidate lists by D'Hondt across "
            "the region. A voter marks one candidate, the votes for a candidate count for the list, and the "
            "candidates of a list are ranked by their own votes. The validity start marks the first election the "
            "archive holds from this source."
        ),
    ),
    (
        system("president"),
        date(1994, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "Direct election: a candidate with more than half of the valid votes in the first round is elected. "
            "Otherwise the two leading candidates go to a second round, held about two weeks later."
        ),
    ),
]
WHICH = {"E": "parliament", "EP": "european", "K": "local", "A": "local", "P": "president", "M": "kainuu"}
TITLES = {
    "E": ("Finnish parliamentary election", "Eduskuntavaalit", "Riksdagsval"),
    "EP": ("European Parliament election in Finland", "Europarlamenttivaalit", "Europaparlamentsval"),
    "K": ("Finnish municipal elections", "Kuntavaalit", "Kommunalval"),
    "A": ("Finnish wellbeing services county elections", "Aluevaalit", "Landskapsval"),
    "P": ("Finnish presidential election", "Presidentinvaali", "Presidentval"),
    "M": ("Kainuu regional council election", "Kainuun maakuntavaalit", "Landskapsval i Kajanaland"),
}
SLUGS = {"E": "general", "EP": "european", "K": "municipal", "A": "county", "P": "presidential", "M": "regional"}


def read(vote: Vote, part: str) -> pl.DataFrame:
    """One of the results service's fixed-width CSV files as stripped text, with columns c1, c2, and so on."""
    artifact = raw.cached(SOURCE, url(vote, part))
    archive = zipfile.ZipFile(artifact.path)
    (name,) = [n for n in archive.namelist() if n.endswith(f"_{MEMBER[part]}_maa.csv")]
    text = archive.read(name).decode("latin-1").encode()
    width = WIDTH[part]
    frame = pl.read_csv(
        io.BytesIO(text),
        has_header=False,
        separator=";",
        quote_char=None,
        infer_schema=False,
        columns=list(range(width)),
    )
    frame = frame.rename({f"column_{i}": f"c{i + 1}" for i in range(width)})
    return frame.with_columns(pl.all().str.strip_chars())


def count(column: str) -> pl.Expr:
    return pl.col(column).str.to_integer(strict=False, base=10).fill_null(0).cast(pl.Int64)


def number(text: str) -> int:
    return int(text) if text.isdigit() else 0


def contest_column(kind: str) -> pl.Expr:
    return {"E": pl.col("c2"), "A": pl.col("c2"), "K": pl.col("c3")}.get(kind, pl.lit("0"))


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def days() -> list[date]:
    return sorted({vote.day for vote in VOTES} | {page.day for page in pages.PAGES})


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        if day in {page.day for page in pages.PAGES}:
            early.election(day).write()
        else:
            election(day).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def places() -> dict[str, dict[str, tuple[str, str]]]:
    """Every constituency, wellbeing services county, and municipality the files name, with its Finnish and Swedish names."""
    found: dict[str, dict[str, tuple[str, str]]] = {
        "fi.vaalipiiri": {},
        "fi.hyvinvointialue": {},
        "fi.kunta": {},
        "council": {},
    }
    for vote in VOTES:
        alu = read(vote, "alu").filter(pl.col("c4").is_in(["K", "V", "R"]))
        for row in alu.iter_rows(named=True):
            names = (row["c10"], row["c11"])
            if row["c4"] == "K":
                found["fi.kunta"][row["c3"]] = names
                if vote.kind == "K":
                    found["council"][row["c3"]] = names
            elif row["c4"] == "V":
                found["fi.vaalipiiri"][row["c6"]] = names
            elif vote.kind == "A":
                found["fi.hyvinvointialue"][row["c2"]] = names
    older = early.places()
    for code, names in older["fi.kunta"].items():
        found["fi.kunta"].setdefault(code, names)
    for code, names in older["council"].items():
        found["council"].setdefault(code, names)
    found["fi.maakunta"] = older["fi.maakunta"]
    return found


@functools.cache
def former_names() -> dict[str, list[str]]:
    """Names that a municipality carried in the elections before 2011, when they differ from its later name."""
    current = places()["fi.kunta"]
    return {
        code: [name for name in names if name != current[code][0]]
        for code, names in early.places_names().items()
        if any(name != current[code][0] for name in names)
    }


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Finland's constituencies, wellbeing services counties, and municipalities as the Ministry of Justice's "
        "results service names them, with Statistics Finland's boundaries, and the voting district scheme",
    )
    tilastokeskus.add_source(p, BOUNDARIES)
    p.external.add(FINLAND)
    for code, kind, rank, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=FINLAND, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=COUNTRY, scheme_code="fi.maa")
    p.add("area_code", area_id=COUNTRY, code="FI")
    p.name(COUNTRY, "Finland", "en", "official")
    p.name(COUNTRY, "Suomi", "fi", "native", False)
    p.name(COUNTRY, "Finland", "sv", "native", False)
    for scheme in ("fi.vaalipiiri", "fi.hyvinvointialue", "fi.kunta", "fi.maakunta"):
        for code, (fi, sv) in sorted(places()[scheme].items()):
            id = area(scheme, code)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=code)
            p.add("area_parent", area_id=id, parent_id=COUNTRY)
            p.name(id, fi, "fi", "official")
            if sv and sv != fi:
                p.name(id, sv, "sv", "official", False)
            if scheme == "fi.kunta":
                for name in former_names().get(code, []):
                    p.name(id, name, "fi", "historical", False)
        if scheme in tilastokeskus.SCHEMES:
            _outlines(p, scheme, places()[scheme])
    return p


def _outlines(p: Package, scheme: str, found: dict[str, tuple[str, str]]) -> None:
    """Statistics Finland's shapes for the areas the results service names, matched by code. A code Statistics Finland
    maps that no election here names gets no area. Statistics Finland numbers the constituencies where the results
    service gives them letters, so those match by their Finnish names."""
    codes = {code: code for code in found}
    if scheme == "fi.vaalipiiri":
        by_name = {fi: code for code, (fi, _) in found.items()}
        codes = {number: by_name[name] for number, name in tilastokeskus.names(scheme).items()}
    for number, runs in sorted(tilastokeskus.spans(scheme).items()):
        if number not in codes:
            continue
        code = codes[number]
        for since, until, shape in runs:
            tilastokeskus.outline(p, area(scheme, code), BOUNDARIES, since, until, shape)


@functools.cache
def parties() -> dict[str, dict[str, list[tuple[date, str]]]]:
    """Each registered party by its permanent identifier, with the names and abbreviations it carried by election."""
    fields = {"fi": "c16", "sv": "c17", "en": "c18", "fi_short": "c11", "sv_short": "c12", "en_short": "c13"}
    found: dict[str, dict[str, list[tuple[date, str]]]] = {}
    for vote in VOTES:
        if not vote.parties:
            continue
        frame = read(vote, "puo").filter(pl.col("c4") == LEVEL[vote.kind], pl.col("c9") != "99", pl.col("c8") != "")
        for row in frame.iter_rows(named=True):
            entry = found.setdefault(row["c8"], {name: [] for name in fields})
            for name, column in fields.items():
                if row[column]:
                    entry[name].append((vote.day, clean(row[column])))
    return found


def party(permanent: str) -> str:
    return key("party", permanent)


def page_party(name: str) -> str:
    """A party that only the pages before 2011 name has no permanent identifier, so it is keyed by its name."""
    return key("party", "page", name)


def fold(name: str) -> str:
    return "".join(ch for ch in re.sub(r"\(.*?\)", "", name) if ch.isalnum()).casefold()


@functools.cache
def known_names() -> dict[str, str]:
    """The id of each registered party by every name it carried from 2011, folded for comparison."""
    out: dict[str, str] = {}
    for permanent, names in parties().items():
        for field in ("fi", "sv", "en"):
            for _, text in names[field]:
                out[fold(text)] = party(permanent)
    for name, permanent in early.ALIASES.items():
        out[fold(name)] = party(permanent)
    for name in early.NEW_PARTIES:
        out[fold(name)] = page_party(name)
    return out


def list_party(name: str) -> str | None:
    """The party that a list on a page before 2011 belongs to, or none for a joint list or an electors' association."""
    return known_names().get(fold(name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry", SOURCE, "Finnish parties, the Eduskunta, the presidency, councils, and electoral systems"
    )
    p.external.add(FINLAND)
    p.add(
        "source",
        id=VOLBY,
        kind="official_body",
        publisher="Oikeusministeriö",
        title="Vaalien tieto- ja tulospalvelu (Ministry of Justice election information and results service)",
        url=f"{BASE}/fi/index.html",
        license="Public data, free to reuse; the service states that the data is public and may be used free of charge",
        license_url=f"{BASE}/fi/index.html",
        attribution="Oikeusministeriö, vaalit.fi",
    )
    cited = [raw.cached(SOURCE, address) for vote in VOTES for address in files(vote)]
    cited += early.cited()
    for artifact in {artifact.storage_key: artifact for artifact in cited}.values():
        p.artifact(artifact, STATFIN if artifact.url.startswith(pages.STATFIN) else VOLBY)
    p.add(
        "source",
        id=STATFIN,
        kind="official_body",
        publisher="Tilastokeskus",
        title="StatFin passive tables (Statistics Finland), municipality numbers",
        url="https://pxdata.stat.fi/PxWeb/pxweb/fi/StatFin_Passiivi/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Tilastokeskus, StatFin",
    )
    p.add("body", id=PARLIAMENT, polity_id=FINLAND, kind="unicameral")
    p.name(PARLIAMENT, "Parliament of Finland", "en", "official")
    p.name(PARLIAMENT, "Eduskunta", "fi", "native", False)
    p.name(PARLIAMENT, "Riksdagen", "sv", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=FINLAND, kind="head_of_state")
    p.name(PRESIDENCY, "President of Finland", "en", "official")
    p.name(PRESIDENCY, "Tasavallan presidentti", "fi", "native", False)
    p.name(PRESIDENCY, "Republikens president", "sv", "native", False)
    for code, (fi, sv) in sorted(places()["council"].items()):
        id = council(code)
        p.add("body", id=id, polity_id=FINLAND, kind="council")
        p.name(id, f"Municipal council of {fi}", "en", "official")
    for code, (fi, sv) in sorted(places()["fi.hyvinvointialue"].items()):
        id = council(code, "county")
        p.add("body", id=id, polity_id=FINLAND, kind="council")
        p.name(id, f"County council of {fi}", "en", "official")
    for code, (fi, sv) in sorted(places()["fi.maakunta"].items()):
        id = council(code, "regional")
        p.add("body", id=id, polity_id=FINLAND, kind="council")
        p.name(id, f"Regional council of {fi}", "en", "official")
        p.name(id, "Kainuun maakuntavaltuusto", "fi", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=FINLAND, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in early.NEW_PARTIES:
        id = page_party(name)
        p.add("party", id=id, kind="party", polity_id=FINLAND)
        p.name(id, name, "fi", "official")
    for permanent, names in sorted(parties().items()):
        id = party(permanent)
        p.add("party", id=id, kind="party", polity_id=FINLAND)
        p.add("external_id", entity_id=id, scheme="fi.vaalit.party", value=permanent)
        seen: set[tuple[str, str]] = set()
        for lang, field in (("fi", "fi"), ("sv", "sv"), ("en", "en")):
            ordered = sorted(names[field], reverse=True)
            for index, text in enumerate(dict.fromkeys(text for _, text in ordered)):
                if (lang, text) in seen:
                    continue
                seen.add((lang, text))
                current = index == 0
                p.name(id, text, lang, "official" if current else "historical", current and lang == "fi")
        for lang, field in (("fi", "fi_short"), ("sv", "sv_short"), ("en", "en_short")):
            ordered = sorted(names[field], reverse=True)
            for text in dict.fromkeys(text for _, text in ordered):
                if (lang, text) in seen:
                    continue
                seen.add((lang, text))
                p.name(id, text, lang, "abbreviation", False)
    return p


def council(code: str, level: str = "municipal") -> str:
    return key("body", level, code)


def slug_of(text: str) -> str:
    return changeset.slug(text)


def election(day: date) -> Package:
    votes = [vote for vote in VOTES if vote.day == day]
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    for vote in votes:
        if vote.round > 1:
            depends.append(
                f"{SOURCE}/{next(v for v in VOTES if v.kind == vote.kind and v.round == 1 and v.day.year == vote.day.year).day}"
            )
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The " + " and the ".join(TITLES[vote.kind][0] for vote in votes) + f" of {day} by voting district from the "
        "Ministry of Justice's results service",
        depends=depends,
    )
    p.external.add(FINLAND)
    units: dict[tuple[str, str], str] = {}
    for vote in votes:
        for address in files(vote):
            p.uses(raw.cached(SOURCE, address))
        _units(p, vote, units)
    off = [_vote(p, vote, units) for vote in votes]
    total, together = sum(n for n, _ in off), sum(t for _, t in off)
    if total:
        p.accept(
            "accounting.ballots_sum",
            f"the results service prints valid and invalid ballots that differ from the voters in {total} voting "
            f"districts, {together} of them districts whose name marks a count together with another district; "
            "stored as printed",
        )
    return p


def _units(p: Package, vote: Vote, units: dict[tuple[str, str], str]) -> None:
    alu = read(vote, "alu").filter(pl.col("c4") == "A")
    fresh = [row for row in alu.iter_rows(named=True) if (row["c3"], row["c5"]) not in units]
    for row in fresh:
        units[(row["c3"], row["c5"])] = key("unit", vote.day, row["c3"], row["c5"])
    for row in fresh:
        id = units[(row["c3"], row["c5"])]
        p.add(
            "reporting_unit",
            id=id,
            polity_id=FINLAND,
            scheme_code="fi.aanestysalue",
            code=f"{vote.day}:{row['c3']}:{row['c5']}",
            area_relation="within",
            area_id=area("fi.kunta", row["c3"]),
            kind="standard",
            counted_with_id=units[(row["c3"], row["c8"])] if row["c8"] else None,
        )
        p.name(id, row["c10"], "fi", "official")
        if row["c11"] and row["c11"] != row["c10"]:
            p.name(id, row["c11"], "sv", "official", False)


METHOD = (
    "Counted per voting district (äänestysalue) in the Ministry of Justice's election information system and published "
    "in its results service. Registered voters are the persons entitled to vote in the election, ballots cast are the "
    "persons who voted, and valid and invalid ballots are as counted. The "
    "totals are stored as advance votes and election-day votes; a district that the service marks as counted "
    "the two together has one total for all channels. A district that the municipality counted "
    "together with another has no count of its own, and only its registered voters are stored. The figures are "
    "those of the checked count."
)
LIST_METHOD = (
    " Each list holds its votes, which are the sum of the votes for its candidates, and each candidate holds the "
    "votes for the candidate as preference votes."
)


def _contest_key(kind: str, row: dict[str, str]) -> str:
    return {"E": row["c2"], "A": row["c2"], "K": row["c3"]}.get(kind, "0")


def _district(kind: str, row: dict[str, str]) -> str:
    return {
        "E": area("fi.vaalipiiri", row["c6"]),
        "A": area("fi.hyvinvointialue", row["c2"]),
        "K": area("fi.kunta", row["c3"]),
    }.get(kind, COUNTRY)


def _vote(p: Package, vote: Vote, units: dict[tuple[str, str], str]) -> tuple[int, int]:
    kind, level, year = vote.kind, LEVEL[vote.kind], vote.day.year
    runoff = vote.round > 1
    election = key("election", vote.day, kind)
    p.add(
        "election",
        id=election,
        polity_id=FINLAND,
        kind="runoff" if runoff else ("local" if kind in ("K", "A") else "general"),
        date_from=vote.day,
        date_to=vote.day,
        status="held",
    )
    english, finnish, swedish = TITLES[kind]
    suffix = (", second round", ", 2. kierros", ", andra omgången") if runoff else ("", "", "")
    p.name(election, f"{year} {english}{suffix[0]}", "en", "official")
    p.name(election, f"{finnish} {year}{suffix[1]}", "fi", "native", False)
    p.name(election, f"{swedish} {year}{suffix[2]}", "sv", "native", False)
    path = f"fi/{vote.day}-{SLUGS[kind]}" + ("-runoff" if runoff else "")
    p.add("slug", path=path, entity_id=election, is_current=True)
    system = system_of(vote)
    alu = read(vote, "alu")
    sets: dict[str, str] = {}
    used: set[str] = set()
    for row in alu.filter(pl.col("c4") == level).iter_rows(named=True):
        number_ = _contest_key(kind, row)
        contest = key("contest", vote.day, kind, number_)
        if kind == "P":
            title = f"{year} {english}, {'second' if runoff else 'first'} round"
        elif kind in NATIONAL:
            title = f"{year} {english}"
        else:
            title = row["c10"]
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office" if kind == "P" else "list_allocation",
            office_id=PRESIDENCY if kind == "P" else None,
            body_id={"E": PARLIAMENT, "K": council(row["c3"]), "A": council(row["c2"], "county")}.get(kind),
            district_area_id=_district(kind, row),
            magnitude=1 if kind == "P" else number(row["c20"]),
            round_number=vote.round,
            previous_round_id=key(
                "contest",
                next(v.day for v in VOTES if v.kind == "P" and v.day.year == year and v.round == 1),
                kind,
                "0",
            )
            if runoff
            else None,
            electoral_system_id=key("electoral_system", "aland") if kind == "E" and row["c6"] == "AHV" else system,
            default_result_kind="counted",
        )
        p.name(contest, title, "en" if kind in NATIONAL else "fi", "official")
        if kind not in NATIONAL and row["c11"] and row["c11"] != row["c10"]:
            p.name(contest, row["c11"], "sv", "official", False)
        slug = f"{path}/{'president' if kind == 'P' else 'finland' if kind == 'EP' else slug_of(title)}"
        if slug in used:
            slug = f"{slug}-{number_}"
        used.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        result_set = sets[number_] = key("result_set", vote.day, kind, number_)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VOLBY,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + (LIST_METHOD if vote.parties else ""),
            lang="en",
        )
        for measure, column in ((REGISTERED, "c14"), (CAST, "c41"), (VALID, "c66"), (INVALID, "c69")):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=number(row[column]))
        if row["c73"] != PHASE:
            raise ValueError(f"{vote.page} {number_} is not the checked count: {row['c73']!r}")
    lists = _lists(p, vote, sets)
    options = _candidates(p, vote, sets, lists)
    return _values(p, vote, alu, units, sets, lists, options)


def system_of(vote: Vote) -> str:
    return system_of_kind(vote.kind)


def system_of_kind(kind: str) -> str:
    return key("electoral_system", WHICH[kind])


def _lists(p: Package, vote: Vote, sets: dict[str, str]) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    if not vote.parties:
        return found
    kind = vote.kind
    known = parties()
    for row in read(vote, "puo").filter(pl.col("c4") == LEVEL[kind]).iter_rows(named=True):
        ck = _contest_key(kind, row)
        option = found[(ck, row["c10"])] = key("option", vote.day, kind, ck, "list", row["c10"])
        permanent = row["c8"] if row["c9"] != "99" and row["c8"] in known else None
        p.add(
            "contest_option",
            id=option,
            contest_id=key("contest", vote.day, kind, ck),
            kind="list",
            party_id=party(permanent) if permanent else None,
            ballot_position=number(row["c10"]),
            ballot_label=row["c16"],
        )
        p.add("declared", result_set_id=sets[ck], option_id=option, measure=VOTES_MEASURE, value=number(row["c41"]))
        if number(row["c45"]):
            p.add("outcome", result_set_id=sets[ck], option_id=option, kind="won", seats=number(row["c45"]))
    return found


def _candidates(
    p: Package, vote: Vote, sets: dict[str, str], lists: dict[tuple[str, str], str]
) -> dict[tuple[str, str], str]:
    kind = vote.kind
    known = parties()
    later = any(v.kind == "P" and v.day.year == vote.day.year and v.round > vote.round for v in VOTES)
    found: dict[tuple[str, str], str] = {}
    for row in read(vote, "ehd").filter(pl.col("c4") == LEVEL[kind]).iter_rows(named=True):
        ck = _contest_key(kind, row)
        tag = (vote.day, kind, ck, row["c15"])
        name = clean(f"{row['c18']} {row['c19']}")
        person = key("person", *tag)
        p.add("person", id=person, birth_precision=None, gender={"1": "male", "2": "female"}.get(row["c20"]))
        p.name(person, name, "fi", "official")
        permanent = row["c8"] if row["c9"] != "99" and row["c8"] in known else None
        option = found[(ck, row["c15"])] = key("option", *tag, "candidate")
        candidacy = key("candidacy", *tag)
        parent = lists[(ck, row["c10"])] if vote.parties else None
        p.add(
            "contest_option",
            id=option,
            contest_id=key("contest", vote.day, kind, ck),
            kind="candidate",
            parent_option_id=parent,
            party_id=party(permanent) if permanent else None,
            ballot_position=number(row["c15"]),
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=parent or option,
            role="list_member" if vote.parties else "head",
            list_position=number(row["c15"]) if vote.parties else None,
            ballot_name=name,
            member_of_party_id=party(permanent) if permanent else None,
        )
        elected = row["c39"] == "1"
        result_set = sets[ck]
        if vote.parties:
            if elected:
                p.add("outcome", result_set_id=result_set, option_id=parent, candidacy_id=candidacy, kind="elected")
        else:
            status = (
                "elected"
                if elected
                else "advanced"
                if later and number(row["c42"]) <= 2
                else "eliminated"
                if later
                else "lost"
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind=status)
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=option,
            measure=PREFERENCE if vote.parties else VOTES_MEASURE,
            value=number(row["c35"]),
        )
    return found


def _total(frame: pl.DataFrame, total: str, measure: int, early: str, day: str) -> pl.DataFrame:
    """A measure by channel: early (2) and election day (1) for a district counted on its own, and one total on all
    channels (0) for a district whose area-file flag says the two were counted together."""

    def rows(select: pl.DataFrame, channel: int, column: str) -> pl.DataFrame:
        return select.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(channel),
            measure=pl.lit(measure),
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value=count(column),
        )

    apart = frame.filter(~pl.col("together"))
    return pl.concat([rows(apart, 2, early), rows(apart, 1, day), rows(frame.filter(pl.col("together")), 0, total)])


def _values(
    p: Package,
    vote: Vote,
    alu: pl.DataFrame,
    units: dict[tuple[str, str], str],
    sets: dict[str, str],
    lists: dict[tuple[str, str], str],
    options: dict[tuple[str, str], str],
) -> tuple[int, int]:
    kind = vote.kind
    ck = contest_column(kind).alias("ck")
    merged = {units[(c3, c5)] for c3, c5 in alu.filter(pl.col("c8") != "").select("c3", "c5").iter_rows()}
    together = {units[(c3, c5)] for c3, c5 in alu.filter(pl.col("c9") == "1").select("c3", "c5").iter_rows()} | merged
    unit_frame = pl.DataFrame(
        {
            "c3": [code[0] for code in units],
            "c5": [code[1] for code in units],
            "unit_id": list(units.values()),
            "merged": [id in merged for id in units.values()],
            "together": [id in together for id in units.values()],
        }
    )
    set_frame = pl.DataFrame({"ck": list(sets), "result_set_id": list(sets.values())})

    def attach(frame: pl.DataFrame) -> pl.DataFrame:
        return (
            frame.filter(pl.col("c4") == "A")
            .with_columns(ck)
            .join(unit_frame, on=["c3", "c5"])
            .join(set_frame, on="ck")
        )

    nothing = pl.lit(None, pl.String).alias("option_id")
    base = attach(alu).with_columns(nothing)
    counted = base.filter(~pl.col("merged"))
    frames = [
        base.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure=pl.lit(REGISTERED),
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value=count("c14"),
        ),
        _total(base, "c41", CAST, "c37", "c39"),
        _total(counted, "c66", VALID, "c64", "c65"),
        _total(counted, "c69", INVALID, "c67", "c68"),
    ]
    mismatch = _mismatch(p, counted)
    if vote.parties:
        names = {"ck": [k[0] for k in lists], "c10": [k[1] for k in lists], "option_id": list(lists.values())}
        votes = attach(read(vote, "puo")).filter(~pl.col("merged")).join(pl.DataFrame(names), on=["ck", "c10"])
        frames.append(_total(votes, "c41", VOTES_MEASURE, "c39", "c40"))
    names = {"ck": [k[0] for k in options], "c15": [k[1] for k in options], "option_id": list(options.values())}
    people = attach(read(vote, "ehd")).filter(~pl.col("merged")).join(pl.DataFrame(names), on=["ck", "c15"])
    frames.append(_total(people, "c35", PREFERENCE if vote.parties else VOTES_MEASURE, "c33", "c34"))
    p.frame("result_value", pl.concat(frames))
    return mismatch


def _mismatch(p: Package, counted: pl.DataFrame) -> tuple[int, int]:
    """Districts whose valid and invalid ballots do not add up to the voters the source prints."""
    off = counted.filter(count("c66") + count("c69") != count("c41"))
    for result_set, n in off.group_by("result_set_id").len().iter_rows():
        p.add(
            "note",
            id=key("note", result_set, "mismatch"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {n} voting district{'s' if n > 1 else ''}, the printed valid and invalid ballots differ from "
            "the printed voters. A district counted together with another has the other's voters on its own row. "
            "The figures are stored as printed.",
            lang="en",
        )
    return off.height, off.filter(pl.col("c10").str.contains(r"\(\+")).height
