"""Tweede Kamer elections 1918-2006 by municipality, from the Kiesraad's Databank Verkiezingsuitslagen (CC0).

For each election the databank serves a detail page, which embeds the national result (registered voters, valid,
blank and invalid votes, and each list's votes and seats), and a CSV of every municipality's votes per list.
"""

import functools
import html
import json
import os
import re
import ssl
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import certifi
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "nl.databank"
HOST = "https://www.verkiezingsuitslagen.nl"
# The host serves its leaf certificate without the intermediate that signed it.
INTERMEDIATE = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"
DAYS = [
    date.fromisoformat(day)
    for day in (
        "1918-07-02",
        "1922-07-05",
        "1925-07-01",
        "1929-07-03",
        "1933-04-26",
        "1937-05-26",
        "1946-05-17",
        "1948-07-07",
        "1952-06-25",
        "1956-06-13",
        "1959-03-12",
        "1963-05-15",
        "1967-02-15",
        "1971-04-28",
        "1972-11-29",
        "1977-05-25",
        "1981-05-26",
        "1982-09-08",
        "1986-05-21",
        "1989-09-06",
        "1994-05-03",
        "1998-05-06",
        "2002-05-15",
        "2003-01-22",
        "2006-11-22",
    )
]
SCHEME = "nl.gemeente_historic"
VOTES = 1
NETHERLANDS = str(uuid5(package.NAMESPACE, "polity\x1fNL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNLD"))
# Lists the databank numbers when one party ran several lists in one election.
NUMBERED = re.compile(r" [123]$")
METHOD = (
    "Votes for each list in each municipality, as the Kiesraad's Databank Verkiezingsuitslagen publishes them in its "
    "CSV of municipal results (CC0). Municipalities are the ones of the election's day, identified by their "
    "Statistics Netherlands code, and the archive keeps one reporting unit for each code across elections; a "
    "municipality's territory changed when it merged or exchanged land, and no boundary is stored for the older "
    "ones. The databank publishes no municipal electorate, blank votes, or invalid votes before 2010, so only list "
    "votes are stored by municipality. The national figures and the seats of each list are the databank's own."
)
TOTALS_REPORT: dict[str, str] = {}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(code: str) -> str:
    return key("area", SCHEME, code)


def unit(code: str) -> str:
    return key("unit", SCHEME, code)


def fold(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", changeset.slug(text).replace("-", " ")).split())


def party(label: str) -> str:
    return key("party", fold(NUMBERED.sub("", label)))


def body() -> str:
    return key("body", "TK", "NL")


KIESRAAD = key("source", "kiesraad.databank")


def code(day: date) -> str:
    return f"TK{day:%Y%m%d}"


def page_url(day: date) -> str:
    return f"{HOST}/verkiezingen/detail/{code(day)}"


def csv_url(day: date) -> str:
    return f"{HOST}/uitslag/verkiezing/{code(day)}/gemeenten"


def _context() -> ssl.SSLContext:
    """The platform's trusted roots plus the Sectigo intermediate the host leaves out of its chain."""
    artifact = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt")
    context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(artifact.path.read_bytes()))
    return context


def fetch() -> None:
    context = _context()
    for day in DAYS:
        raw.fetch(SOURCE, page_url(day), suffix=".html", verify=context)
        raw.fetch(SOURCE, csv_url(day), suffix=".csv", verify=context)


def _whole(text: str | None) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


class National(NamedTuple):
    registered: int | None
    valid: int
    blank: int | None
    invalid: int | None
    note: str
    lists: dict[str, tuple[int | None, int | None]]


@functools.cache
def national(day: date) -> National:
    """The national result a detail page embeds: its figures, its note, and its lists with votes and seats."""
    text = raw.cached(SOURCE, page_url(day)).path.read_text(encoding="utf-8")
    found = re.search(r'<textarea id="UitslagData"[^>]*>(.*?)</textarea>', text, re.DOTALL)
    assert found is not None, f"{day}: the page embeds no result"
    data = json.loads(html.unescape(found.group(1)))
    assert data["Info"]["Code"] == code(day)
    region = data["Stemregio"]
    valid = _whole(region["AantalGeldigeStemmen"])
    assert valid is not None
    return National(
        registered=_whole(region["Kiesgerechtigden"]) or None,
        valid=valid,
        blank=_whole(region["AantalBlancoStemmen"]),
        invalid=_whole(region["AantalOngeldigeStemmen"]),
        note=" ".join(str(data["Info"]["Opmerking"] or "").split()),
        lists={
            " ".join(str(row["Naam"]).split()): (_whole(row["AantalStemmen"]), _whole(row["AantalZetels"]))
            for row in region["Partij"]
        },
    )


@functools.cache
def municipal(day: date) -> pl.DataFrame:
    frame = pl.read_csv(
        raw.cached(SOURCE, csv_url(day)).path,
        separator=";",
        encoding="utf8",
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    frame = frame.select(
        name=pl.col("Gemeente").str.strip_chars(),
        code=pl.col("Code").str.strip_chars().str.strip_prefix("G"),
        list=pl.col("Partij").str.strip_chars().str.replace_all(r"\s+", " "),
        votes=pl.col("AantalStemmen").str.strip_chars().cast(pl.Int64),
    )
    assert frame["code"].str.contains(r"^\d{4}$").all(), f"{day}: unexpected municipality codes"
    assert not frame.select("code", "list").is_duplicated().any(), f"{day}: a list repeats in a municipality"
    return frame


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()
    for day, line in TOTALS_REPORT.items():
        print(day, line)


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Dutch municipalities and parties of the Tweede Kamer elections 1918-2006 from the Kiesraad's databank",
    )
    p.external |= {NETHERLANDS, COUNTRY}
    p.add(
        "source",
        id=KIESRAAD,
        kind="official_body",
        publisher="Kiesraad",
        title="Databank Verkiezingsuitslagen",
        url=HOST,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kiesraad",
    )
    for day in DAYS:
        p.artifact(raw.cached(SOURCE, csv_url(day)), KIESRAAD)
        p.artifact(raw.cached(SOURCE, page_url(day)), KIESRAAD)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=NETHERLANDS,
        kind="administrative",
        rank=2,
        noun="municipality",
        code_system="cbs",
    )
    p.add("body", id=body(), polity_id=NETHERLANDS, kind="lower_house")
    p.name(body(), "Tweede Kamer der Staten-Generaal", "nl", "official")
    p.name(body(), "House of Representatives", "en", "official", False)
    # A municipality is named as its latest election calls it, and keeps the spellings of earlier ones.
    names: dict[str, dict[str, date]] = {}
    labels: dict[str, str] = {}
    for day in DAYS:
        frame = municipal(day)
        for number, text in frame.select("code", "name").unique().iter_rows():
            names.setdefault(number, {})[text] = day
        for text in frame["list"].unique().to_list():
            labels.setdefault(fold(NUMBERED.sub("", text)), NUMBERED.sub("", text))
        for text in national(day).lists:
            labels.setdefault(fold(NUMBERED.sub("", text)), NUMBERED.sub("", text))
    for number, spellings in sorted(names.items()):
        id = area(number)
        ranked = sorted(spellings, key=lambda text: (spellings[text], text), reverse=True)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=number)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        for rank, text in enumerate(ranked):
            p.name(id, text, "nl", "official", rank == 0)
        p.add(
            "reporting_unit",
            id=unit(number),
            polity_id=NETHERLANDS,
            scheme_code=SCHEME,
            code=number,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
    for folded, text in sorted(labels.items()):
        id = key("party", folded)
        p.add("party", id=id, kind="party", polity_id=NETHERLANDS)
        p.add("external_id", entity_id=id, scheme="nl.databank.party", value=folded)
        p.name(id, text, "nl", "official")
    return p


def build(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Dutch general election of {day} by municipality from the Kiesraad's databank",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NETHERLANDS, COUNTRY}
    p.uses(raw.cached(SOURCE, csv_url(day)))
    p.uses(raw.cached(SOURCE, page_url(day)))
    whole = national(day)
    lists = whole.lists
    frame = municipal(day)
    election, contest = key("election", day), key("contest", day)
    base = f"nl/{day}-tweede-kamer"
    p.add(
        "election",
        id=election,
        polity_id=NETHERLANDS,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{day.year} Dutch general election", "en", "official")
    p.name(election, f"Tweede Kamerverkiezing {day.year}", "nl", "native", False)
    p.add("slug", path=base, entity_id=election, is_current=True)
    seats = sum(won or 0 for _, won in lists.values())
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=body(),
        district_area_id=COUNTRY,
        magnitude=seats,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Tweede Kamer", "nl", "official")
    p.add("slug", path=f"{base}/tweede-kamer", entity_id=contest, is_current=True)
    options: dict[str, str] = {}
    names = sorted(set(frame["list"].to_list()) | set(lists), key=lambda text: (fold(text), text))
    for text in names:
        options[text] = key("option", day, text)
        p.add(
            "contest_option",
            id=options[text],
            contest_id=contest,
            kind="list",
            party_id=party(text),
            ballot_label=text,
        )
    result_set = key("result_set", day, "municipal")
    summed = frame.group_by("list").agg(pl.col("votes").sum())
    tally = int(summed["votes"].sum())
    valid = whole.valid
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=KIESRAAD,
        status="active",
        is_default=True,
        completeness=round(min(tally / valid, 1.0), 6),
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    figures = [
        ("registered voters", whole.registered),
        ("valid votes", valid),
        ("blank votes", whole.blank),
        ("invalid votes", whole.invalid),
    ]
    stated = "; ".join(f"{label} {int(number):,}" for label, number in figures if isinstance(number, int))
    p.add(
        "note",
        id=key("note", result_set, "national"),
        entity_id=result_set,
        kind="caveat",
        body_md=f"The databank's national figures for this election: {stated}. The municipal list votes add up to "
        f"{tally:,}, {tally / valid:.2%} of the national valid votes.",
        lang="en",
    )
    if whole.note:
        p.add(
            "note",
            id=key("note", result_set, "source"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The databank notes (in Dutch): {whole.note}",
            lang="nl",
        )
    declared = {text: votes for text, (votes, _) in lists.items() if votes is not None}
    agree: list[str] = []
    for text in names:
        if text in declared and declared[text] == summed.filter(pl.col("list") == text)["votes"].sum():
            agree.append(text)
    for text in agree:
        p.add("declared", result_set_id=result_set, option_id=options[text], measure=VOTES, value=declared[text])
    for text, (_, won) in sorted(lists.items()):
        if won:
            p.add("outcome", result_set_id=result_set, option_id=options[text], kind="won", seats=won)
    TOTALS_REPORT[str(day)] = (
        f"municipal list votes {tally:,} vs national valid {valid:,} ({tally / valid:.4%}); "
        f"{len(agree)} of {len(declared)} national list totals agree; seats {seats}"
    )
    p.frame(
        "result_value",
        frame.select(
            result_set_id=pl.lit(result_set),
            unit_id=pl.col("code").map_elements(unit, return_dtype=pl.String),
            option_id=pl.col("list").replace_strict(options, return_dtype=pl.String),
            measure=pl.lit(VOTES, pl.Int16),
            value=pl.col("votes"),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
        ),
    )
    return p
