"""Turkey's Supreme Election Council (YSK): ballot-box results of the 2007 and 2010 constitutional referendums, from
the council's Sandık Sonuçları site (sonuc.ysk.gov.tr).

The council publishes, for every ballot box, the registered voters, the voters who voted, the valid and invalid
ballots, and the votes for each answer. Each file here is the council's own response for one referendum, saved box by
box for every district of the 81 provinces. The elections, contests, and answers are the ones the archive already
holds from the council's provincial totals; this package adds a count at the finest unit and points at them."""

import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "tr.ysk_sonuc"
INBOX = DATA / "inbox" / SOURCE
SITE = "https://sonuc.ysk.gov.tr/"
ELECTIONS = "https://sonuc.ysk.gov.tr/api/getSecimList?secimSayi=100&araSecim=0&yenilemeSecimi=0"
FOLDER = "browser://20261116-222924-4772b5/"
AREAS = Path(__file__).with_name("ysk_sonuc_areas.tsv")

REGISTERED, VOTERS, VALID, INVALID, VOTES = 2, 14, 4, 5, 1
TURKEY = "01a13c0c-c7a6-7799-b107-b55e848cecf2"  # the polity row the archive holds for Türkiye
SANDIK = "tr.sandik"  # the scheme of the archive's ballot-box units


class Event(NamedTuple):
    day: date
    file: str
    election: str  # the rows the archive holds from the council's provincial totals
    contest: str
    yes: str
    no: str
    national_valid: int  # the valid votes the council prints for the whole country, abroad and customs included
    domestic: tuple[int, int, int]  # the council's printed domestic totals: valid, yes, no
    english: str


EVENTS = [
    Event(
        date(2007, 10, 21),
        FOLDER + "tr-2007-10-21-halkoylamasi-referendum-ballot-box-results.ndjson.gz",
        "01a13f11-e319-707f-9e17-a6c5f6cef7b8",
        "01a13f11-e319-707f-9e17-a6c69e5da004",
        "01a13f11-e319-707f-9e17-a6c87d256612",
        "01a13f11-e319-707f-9e17-a6c98ae24871",
        28160943,
        (28142586, 19403866, 8738720),
        "2007 constitutional referendum",
    ),
    Event(
        date(2010, 9, 12),
        FOLDER + "tr-2010-09-12-halkoylamasi-referendum-ballot-box-results.ndjson.gz",
        "01a13f04-e1b0-71ae-9ba0-c49741ac5cba",
        "01a13f04-e1b0-71ae-9ba0-c49815e7adf8",
        "01a13f04-e1b0-71ae-9ba0-c49ae0061e91",
        "01a13f04-e1b0-71ae-9ba0-c49b278f4d2b",
        37607997,
        (37449300, 21667427, 15781873),
        "2010 constitutional referendum",
    ),
]

METHOD = (
    "Counted by the Supreme Election Council per ballot box and saved from the council's Sandık Sonuçları site. "
    "Registered voters are the electoral roll of the box, voters are the voters who voted, and valid and invalid "
    "ballots are the box's own counts. Votes for Evet and Hayır are the box's answer counts. The files hold the "
    "boxes listed under each district of the 81 provinces. They hold no boxes at customs gates or abroad, and the "
    "council's printed total for the whole country is higher than the boxes here, by 56,429 valid votes in 2007 and "
    "158,697 in 2010. Where a box's voters exceed its registered voters, both are kept as printed. A box's place is its neighborhood where the council's "
    "neighborhood number is one the archive holds from the 2009 local elections, or, for the 2007 files, whose "
    "province, district, and neighborhood name match one of the 2009 local elections (the 2007 numbers are the council's "
    "own and differ from the 2009 ones), else its district, else its province."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    """Load the files saved from the council's site (data/inbox/tr.ysk_sonuc/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    for name, entry in index.items():
        raw.store(
            SOURCE,
            entry.get("source", name),
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def places() -> dict[tuple[str, str], str]:
    """The archive's area for a neighborhood number, a district, or a province, from ysk_sonuc_areas.tsv, which
    lists each place the council's referendum files name with the area the archive holds for it."""
    rows = pl.read_csv(AREAS, separator="\t", schema_overrides={"key": pl.String}).iter_rows()
    return {(kind, code): area for kind, code, area in rows}


class Box(NamedTuple):
    il: int
    ilce: int
    number: str
    neighborhood: str
    prison: bool
    registered: int
    voters: int
    valid: int
    invalid: int
    yes: int
    no: int


def boxes(event: Event) -> tuple[list[Box], dict[str, int]]:
    """Every box the council's file holds, and the printed national summary."""
    out: list[Box] = []
    summary: dict[str, int] = {}
    with gzip.open(raw.cached(SOURCE, event.file).path, "rt") as handle:
        for line in handle:
            record = json.loads(line)
            if record["type"] == "national_summary":
                summary = record["data"][0]
            elif record["type"] == "il":
                for district in record["ilceler"]:
                    for b in district["sandik"]:
                        out.append(
                            Box(
                                b["il_ID"],
                                b["ilce_ID"],
                                str(b["sandik_NO"]),
                                str(b["muhtarlik_ID"]),
                                bool(b["cezaevi_ID"]),
                                b["secmen_SAYISI"],
                                b["oy_KULLANAN_SECMEN_SAYISI"],
                                b["gecerli_OY_TOPLAMI"],
                                b["gecersiz_OY_TOPLAMI"],
                                b["bagimsiz1_ALDIGI_OY"],
                                b["bagimsiz2_ALDIGI_OY"],
                            )
                        )
    return out, summary


def packages() -> list[str]:
    return [f"{SOURCE}/{e.day}" for e in EVENTS]


def election(event: Event) -> Package:
    day = event.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} Turkish constitutional referendum by ballot box from the Supreme Election Council",
    )
    p.external |= {TURKEY, SANDIK, event.election, event.contest, event.yes, event.no}
    source = key("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Yüksek Seçim Kurulu (YSK)",
        title=f"Sandık sonuçları, {day.year} halkoylaması",
        url=SITE,
        license="No license stated.",
        license_url=SITE,
        attribution="Yüksek Seçim Kurulu",
    )
    for url in (event.file, ELECTIONS):
        p.artifact(raw.cached(SOURCE, url), source)
    where = places()
    data, summary = boxes(event)
    result_set = key("result_set", day, "ballot-box")
    valid_sum = sum(b.valid for b in data)
    p.add(
        "result_set",
        id=result_set,
        election_id=event.election,
        contest_id=event.contest,
        stage="alternative",
        kind="counted",
        source_id=source,
        status="active",
        is_default=False,
        completeness=round(valid_sum / summary["gecerli_OY_TOPLAMI"], 6),
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    end = day + timedelta(days=1)
    units: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    seen: set[str] = set()
    for b in data:
        code = f"{day.year}:{b.il:02d}:{b.ilce}:{b.number}"
        area = (
            where.get(("mahalle", b.neighborhood))
            or where.get(("ilce", f"{b.il}:{b.ilce}"))
            or where.get(("il", str(b.il)))
        )
        if area is None:
            raise LookupError(f"no area for box {code}")
        if code in seen:
            raise ValueError(f"box {code} twice")
        seen.add(code)
        unit = key("unit", code)
        p.external.add(area)
        units.append(
            {
                "id": unit,
                "polity_id": TURKEY,
                "scheme_code": SANDIK,
                "code": code,
                "area_relation": "within",
                "area_id": area,
                "kind": "prison" if b.prison else "standard",
                "valid_from": day,
                "valid_to": end,
            }
        )
        for measure, option, value in (
            (REGISTERED, None, b.registered),
            (VOTERS, None, b.voters),
            (VALID, None, b.valid),
            (INVALID, None, b.invalid),
            (VOTES, event.yes, b.yes),
            (VOTES, event.no, b.no),
        ):
            values.append(
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
    p.frame("reporting_unit", pl.DataFrame(units))
    p.frame("result_value", pl.DataFrame(values, schema_overrides={"value": pl.Int64}))
    p.accept(
        "accounting.turnout",
        "In some boxes the council's printed voters exceed its printed registered voters. Both counts are kept as "
        "the council printed them.",
    )
    return p


def compare(event: Event) -> list[str]:
    """How the boxes' totals compare with the council's printed totals."""
    data, summary = boxes(event)
    valid = sum(b.valid for b in data)
    yes, no = sum(b.yes for b in data), sum(b.no for b in data)
    domestic_valid, domestic_yes, domestic_no = event.domestic
    whole = summary["gecerli_OY_TOPLAMI"]
    return [
        (
            f"{event.day}: {len(data)} boxes; valid {valid} of {whole} printed for the whole country "
            f"({whole - valid} short); domestic printed {domestic_valid} ({domestic_valid - valid} short); "
            f"yes {yes} of {domestic_yes} domestic ({domestic_yes - yes} short); "
            f"no {no} of {domestic_no} ({domestic_no - no} short)"
        )
    ]


def parse() -> None:
    for event in EVENTS:
        for line in compare(event):
            print(line, flush=True)
        election(event).write()


def emit() -> None:
    package.run("load", packages())
