"""Turkey's 2007 general election: the votes cast at the customs gates, from the Supreme Election Council's (YSK)
archive page for the 2007 parliamentary election.

The council prints, as three one-page tables, the results of the electoral districts without the customs gates, of
the customs gates alone, and of the country with the gates included. The gates belong to no electoral district. The
council gives no figures for single gates or ballot boxes there, so the gates are one reporting unit holding the
gates' totals, in a count that attaches to the archive's national contest."""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pymupdf

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.sources.tr.ysk_mv_boxes import PARTIES
from ea.store import DATA

SOURCE = "tr.ysk_mv_customs"
KEY = f"{SOURCE}/parliament-2007"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
DAY = date(2007, 7, 22)
BASE = "https://www.ysk.gov.tr/doc/dosyalar/docs/2007MilletvekiliSecimi/"
GATES = BASE + "gumrukkapilari/gumrukkapilari.pdf"
WITHOUT = BASE + "gumrukharic/gumrukharic.pdf"
WITH = BASE + "gumrukdahil/gumrukdahil.pdf"
PAGE = "https://www.ysk.gov.tr/tr/2007-milletvekili-genel-secimi/"
TITLE = "2007 Milletvekili Genel Seçimi: gümrük kapıları seçim sonucu"
NOTE = (
    "Results of the July 22, 2007 parliamentary election at the customs gates, from the tables YSK published on "
    "its archive page and approved on July 30, 2007 (decision 739, Official Gazette 26598): the gates alone, the "
    "electoral districts without the gates, and the country with the gates. The council gives no figures for single "
    "gates, and no votes for independents at the gates."
)
METHOD = (
    "The customs gates are one reporting unit with no area, holding the registered voters, voters, valid and "
    "invalid votes of all gates together and the votes for each party list, as the council prints them. They count "
    "toward the national totals but toward no electoral district. The set adds up to the difference between the "
    "council's table with the gates and its table without them."
)
NAMES = {
    "AYDINLIK TÜRKİYE PARTİSİ": "ATP",
    "BAĞIMSIZ TÜRKİYE PARTİSİ": "BTP",
    "SAADET PARTİSİ": "SAADET",
    "İŞÇİ PARTİSİ": "İP",
    "CUMHURİYET HALK PARTİSİ": "CHP",
    "HALKIN YÜKSELİŞİ PARTİSİ": "HYP",
    "ÖZGÜRLÜK VE DAYANIŞMA PARTİSİ": "ÖDP",
    "GENÇ PARTİ": "GENÇPARTİ",
    "DEMOKRAT PARTİ": "DP",
    "LİBERAL DEMOKRAT PARTİ": "LDP",
    "MİLLİYETÇİ HAREKET PARTİSİ": "MHP",
    "ADALET VE KALKINMA PARTİSİ": "AK PARTİ",
    "EMEK PARTİSİ": "EMEP",
    "TÜRKİYE KOMÜNİST PARTİSİ": "TKP",
}
TOTALS = {
    "KAYITLI SEÇMEN SAYISI": "registered",
    "OY KULLANAN SEÇMEN SAYISI": "voters",
    "GEÇERLİ OY SAYISI": "valid",
    "GEÇERSİZ OY SAYISI": "invalid",
}
Row = dict[str, object]


def sources() -> list[str]:
    return [GATES, WITHOUT, WITH]


def fetch() -> None:
    """Load the files saved from the council's page (data/inbox/tr.ysk_mv_customs/) into the raw cache."""
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


def table(url: str) -> dict[str, int]:
    """The figures of one table: the turnout lines and the votes of each party, by the name the table prints."""
    with pymupdf.open(raw.cached(SOURCE, url).path) as doc:
        text = "\n".join(page.get_text(sort=True) for page in doc)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: dict[str, int] = {}
    for i, line in enumerate(lines):
        label = re.sub(r"^TOPLAM ", "", line)
        name = max((n for n in [*NAMES, *TOTALS] if label.startswith(n)), key=len, default=None)
        if name is None:
            continue
        # the figure follows the label on the same line or is on the next one; the ratio after it starts with "%"
        rest = label[len(name) :].strip() or lines[i + 1]
        match = re.match(r"\d[\d.,]*", rest)
        if match is None:
            raise ValueError(f"{url}: no figure for {name}")
        out[name] = int(re.sub(r"[.,]", "", match[0]))
    return out


def figures() -> dict[str, dict[str, int]]:
    found = {"gates": table(GATES), "without": table(WITHOUT), "with": table(WITH)}
    for key, one in found.items():
        missing = sorted((set(NAMES) | set(TOTALS)) - set(one))
        if missing:
            raise ValueError(f"{key}: no figures for {missing}")
    return found


def build() -> Package:
    found = figures()
    gates = found["gates"]
    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value", "pipeline"])
    national = str(
        x.filter(
            (pl.col("scheme") == "tr.contest")
            & (pl.col("pipeline") == "tr.ysk")
            & (pl.col("value") == f"{DAY}:parliament")
        )["entity_id"][0]
    )
    contest = pl.read_parquet(REFERENCE / "contest.parquet").filter(pl.col("id") == national).row(0, named=True)
    election = str(contest["election_id"])
    polity = str(pl.read_parquet(REFERENCE / "election.parquet").filter(pl.col("id") == election)["polity_id"][0])
    parties = x.filter(pl.col("scheme") == "tr.party")
    party_id = dict(zip(parties["value"], parties["entity_id"], strict=True))
    options = pl.read_parquet(REFERENCE / "contest_option.parquet").filter(pl.col("contest_id") == national)
    option_of: dict[str, str] = {}
    for name, label in NAMES.items():
        match = options.filter(pl.col("party_id") == party_id[PARTIES[label]])
        if match.height != 1:
            raise ValueError(f"{name}: {match.height} options in the national contest")
        option_of[name] = str(match["id"][0])

    # each figure against the council's other two tables
    for name in [*NAMES, *TOTALS]:
        if name == "EMEK PARTİSİ":
            # the table without the gates prints this party's votes as "25,77", a figure cut short
            if found["with"][name] - gates[name] != 25770 or found["without"][name] != 2577:
                raise ValueError(f"{name}: the table without the gates no longer reads 25,77")
            continue
        if found["with"][name] - found["without"][name] != gates[name]:
            raise ValueError(f"{name}: gates {gates[name]} against with minus without")
    if sum(gates[n] for n in NAMES) != gates["GEÇERLİ OY SAYISI"]:
        raise ValueError("party votes at the gates do not add up to the valid votes")

    p = Package(KEY, SOURCE, "Turkey's 2007 general election: customs gates, from YSK's archive tables")
    source = p.id("source", TITLE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PAGE,
        license=LICENSE,
        notes=NOTE,
    )
    for url in sources():
        p.artifact(raw.cached(SOURCE, url), source)
    result_set = p.id("result_set", f"{DAY}:customs")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=national,
        stage="official",
        kind="counted",
        status="active",
        source_id=source,
        is_default=False,
        completeness=1,
    )
    p.add(
        "note",
        id=p.id("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD,
        lang="en",
    )
    unit = p.id("unit", f"{DAY.year}:customs")
    p.frame(
        "reporting_unit",
        pl.DataFrame(
            [
                {
                    "id": unit,
                    "polity_id": polity,
                    "scheme_code": "tr.sandik",
                    "code": f"{DAY.year}:customs",
                    "area_relation": "unlocated",
                    "area_id": None,
                    "kind": "customs_gate",
                    "valid_from": DAY,
                    "valid_to": DAY + timedelta(days=1),
                }
            ],
            schema=_UNIT_SCHEMA,
        ),
    )
    rows: list[tuple[str, str, str | None, int, int]] = [
        (result_set, unit, None, REGISTERED, gates["KAYITLI SEÇMEN SAYISI"]),
        (result_set, unit, None, VOTERS, gates["OY KULLANAN SEÇMEN SAYISI"]),
        (result_set, unit, None, VALID, gates["GEÇERLİ OY SAYISI"]),
        (result_set, unit, None, INVALID, gates["GEÇERSİZ OY SAYISI"]),
        *((result_set, unit, option_of[name], VOTES, gates[name]) for name in NAMES),
    ]
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    p.external |= {polity, "tr.sandik", national, election, *option_of.values()}
    print(
        f"gates: registered {gates['KAYITLI SEÇMEN SAYISI']} voters {gates['OY KULLANAN SEÇMEN SAYISI']} "
        f"valid {gates['GEÇERLİ OY SAYISI']} invalid {gates['GEÇERSİZ OY SAYISI']}; with minus without: valid "
        f"{found['with']['GEÇERLİ OY SAYISI'] - found['without']['GEÇERLİ OY SAYISI']}",
        flush=True,
    )
    return p


_UNIT_SCHEMA = {
    "id": pl.String,
    "polity_id": pl.String,
    "scheme_code": pl.String,
    "code": pl.String,
    "area_relation": pl.String,
    "area_id": pl.String,
    "kind": pl.String,
    "valid_from": pl.Date,
    "valid_to": pl.Date,
}


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())
