"""Turkey's 2009 metropolitan mayoral votes by ballot box, from the Supreme Election Council's (YSK) Sandık Sonuçları
site (sonuc.ysk.gov.tr).

The council elected the mayor of each of the 16 metropolitan municipalities by a vote over the whole province. For every
ballot box of those provinces the council gives the registered voters, voters, valid and invalid votes, and the
votes for each party's candidate and for each independent candidate, whose names it gives in a separate header for
each province. The contests are the ones the archive already holds from the council's provincial totals; this package
adds a count at the finest unit and points at them."""

import gzip
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.tr.ysk import INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES, title
from ea.sources.tr.ysk_meclis import DATED, PARTIES, SITE
from ea.store import DATA

SOURCE = "tr.ysk_metro_boxes"
KEY = f"{SOURCE}/metropolitan-mayor-2009"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
DAY = date(2009, 3, 29)
FOLDER = "browser://20261116-222924-4772b5/"
BOXES = FOLDER + "tr-2009-03-29-mahalli-buyuksehir-belediye-baskanligi-ballot-box-results.ndjson.gz"
LABELS = "browser://20261117-003207-85b5ea/tr-2009-03-29-local-header-labels.json"
METROPOLITAN = "6"  # the service's number for the metropolitan mayoral race among the parts of a local election
TITLE = "Sandık Sonuçları: büyükşehir belediye başkanlığı"
NOTE = (
    "Metropolitan mayoral results for each ballot box, read from the results service of YSK's Sandık Sonuçları site "
    "for the local elections of March 29, 2009. For each of the 16 metropolitan municipalities, the service gives "
    "each ballot box's registered voters, voters, valid and invalid votes, and the votes for each party's candidate "
    "and for each independent candidate. It gives the independents' names in a header served for each province. It "
    "gives no outcome."
)
METHOD = (
    "Counted for each ballot box by YSK. Boxes are the same boxes that held the council vote, so a box that already "
    "has a unit in the archive keeps it. A party's votes go to its candidate's option, and each independent "
    "candidate's votes go to an option of their own, named by the province's header. The boxes listed under each "
    "district add up to the district totals the service prints. The service's national summary counts 74,975 boxes "
    "and lists 117 more valid votes than the 74,973 boxes it gives here."
)
Row = dict[str, object]


class Place(NamedTuple):
    il: int
    name: str


def fetch() -> None:
    """Load the files saved from the service (data/inbox/tr.ysk_metro_boxes/) into the raw cache."""
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


def num(value: object) -> int:
    return int(str(value))


def read() -> tuple[list[Row], dict[str, int], dict[int, dict[str, str]]]:
    """The council's box rows, its printed national summary, and each province's column labels."""
    boxes: list[Row] = []
    summary: dict[str, int] = {}
    with gzip.open(raw.cached(SOURCE, BOXES).path, "rt") as handle:
        for line in handle:
            record = json.loads(line)
            if record["type"] == "national_summary":
                summary = record["data"][0]
            elif record["type"] == "il":
                for district in record["ilceler"]:
                    boxes.extend(district["sandik"])
    header = json.loads(raw.cached(SOURCE, LABELS).path.read_text())["contests"][METROPOLITAN]["basliklar"]
    labels = {num(h["ilId"]): {str(c["column_NAME"]): str(c["ad"]) for c in h["basliklar"]} for h in header}
    return boxes, summary, labels


def build() -> Package:
    boxes, summary, labels = read()
    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value", "pipeline"])
    contests = {
        value.removeprefix(f"{DAY}:").removesuffix("-mayor"): entity
        for value, entity in x.filter(
            (pl.col("scheme") == "tr.contest")
            & (pl.col("pipeline") == "tr.ysk_local")
            & pl.col("value").str.starts_with(f"{DAY}:")
            & pl.col("value").str.ends_with("-mayor")
        )
        .select("value", "entity_id")
        .iter_rows()
    }
    contest = {
        row["id"]: row
        for row in pl.read_parquet(REFERENCE / "contest.parquet")
        .filter(pl.col("id").is_in(list(contests.values())))
        .iter_rows(named=True)
    }
    options: dict[str, list[Row]] = defaultdict(list)
    for row in (
        pl.read_parquet(REFERENCE / "contest_option.parquet")
        .filter(pl.col("contest_id").is_in(list(contests.values())))
        .iter_rows(named=True)
    ):
        options[str(row["contest_id"])].append(row)
    parties = x.filter(pl.col("scheme") == "tr.party")
    party_id = dict(zip(parties["value"], parties["entity_id"], strict=True))
    party_name = dict(pl.read_parquet(REFERENCE / "party.parquet").select("id", "name").iter_rows())
    unit_rows = (
        pl.read_parquet(REFERENCE / "reporting_unit.parquet")
        .filter((pl.col("scheme_code") == "tr.sandik") & pl.col("code").str.starts_with(f"{DAY.year}:"))
        .select("id", "code", "polity_id")
    )
    units = dict(zip(unit_rows["code"], unit_rows["id"], strict=True))
    polity = unit_rows["polity_id"][0]
    muhtarlik = x.filter(pl.col("scheme") == "tr.ysk.muhtarlik.2009")
    area_of = dict(zip(muhtarlik["value"], muhtarlik["entity_id"], strict=True))

    province_key: dict[int, str] = {}
    for row in boxes:
        province_key.setdefault(num(row["il_ID"]), slug(title(str(row["il_ADI"]))))
    if set(province_key.values()) != set(contests):
        raise ValueError(f"provinces {sorted(province_key.values())} against contests {sorted(contests)}")

    p = Package(KEY, SOURCE, "Turkey's 2009 metropolitan mayoral votes by ballot box, from YSK's Sandık Sonuçları")
    source = p.id("source", TITLE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=SITE,
        license=LICENSE,
        notes=NOTE,
    )
    for url in (BOXES, LABELS):
        p.artifact(raw.cached(SOURCE, url), source)

    external: set[str] = {polity, "tr.sandik"}
    result_sets: dict[int, tuple[str, str]] = {}
    option_ids: dict[tuple[int, str], str] = {}
    for il, key in sorted(province_key.items()):
        contest_id = str(contests[key])
        row = contest[contest_id]
        external |= {contest_id, str(row["election_id"])}
        result_set = p.id("result_set", f"{DAY}:{key}")
        result_sets[il] = (contest_id, result_set)
        p.add(
            "result_set",
            id=result_set,
            election_id=row["election_id"],
            contest_id=contest_id,
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
        for column, label in sorted(labels[il].items()):
            if column.startswith("parti"):
                slug_ = DATED.get((label, DAY.year), PARTIES[label])
                party = party_id[slug_]
                found = [o for o in options[contest_id] if o["party_id"] == party]
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {slug_}")
                if found:
                    option = str(found[0]["id"])
                    external.add(option)
                else:
                    option = p.id("option", f"{DAY}:{key}", slug_)
                    external.add(party)
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest_id,
                        kind="candidate",
                        party_id=party,
                        ballot_label=party_name[party],
                    )
            elif column.startswith("bagimsiz"):
                option = p.id("option", f"{DAY}:{key}", column)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="candidate",
                    ballot_label=title(label),
                )
            else:
                raise ValueError(f"unexpected column {column}")
            option_ids[(il, column)] = option

    values: list[tuple[str, str, str | None, int, int]] = []
    new_units: dict[str, str] = {}
    new_rows: list[Row] = []
    on_district = 0
    seen: set[str] = set()
    for row in sorted(boxes, key=lambda r: num(r["sandik_ID"])):
        il = num(row["il_ID"])
        contest_id, result_set = result_sets[il]
        code = f"{DAY.year}:{il:02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"
        if code in seen:
            raise ValueError(f"box {code} twice")
        seen.add(code)
        if code in units:
            unit = units[code]
            external.add(unit)
        else:
            unit = p.id("unit", code)
            new_units[code] = unit
            area = area_of.get(str(row["muhtarlik_ID"]))
            if area is None:
                area = str(contest[contest_id]["district_area_id"])
                on_district += 1
            external.add(area)
            new_rows.append(
                {
                    "id": unit,
                    "polity_id": polity,
                    "scheme_code": "tr.sandik",
                    "code": code,
                    "area_relation": "within",
                    "area_id": area,
                    "kind": "prison" if row.get("cezaevi_ID") else "standard",
                    "valid_from": DAY,
                    "valid_to": DAY + timedelta(days=1),
                }
            )
        for measure, column in (
            (REGISTERED, "secmen_SAYISI"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
            (VALID, "gecerli_OY_TOPLAMI"),
            (INVALID, "gecersiz_OY_TOPLAMI"),
        ):
            if row.get(column) is not None:
                values.append((result_set, unit, None, measure, num(row[column])))
        votes = 0
        for column, value in row.items():
            if not value or not column.endswith("_ALDIGI_OY"):
                continue
            if (il, column) not in option_ids:
                raise ValueError(f"box {code}: column {column} has no label in province {il}")
            values.append((result_set, unit, option_ids[(il, column)], VOTES, num(value)))
            votes += num(value)
        if votes != num(row["gecerli_OY_TOPLAMI"]):
            raise ValueError(f"box {code}: votes {votes} against valid {row['gecerli_OY_TOPLAMI']}")

    p.frame("reporting_unit", pl.DataFrame(new_rows, schema=_UNIT_SCHEMA))
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
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
    p.external |= external
    p.accept(
        "accounting.turnout",
        "In some boxes the service gives more voters than registered voters, by a few votes; the boxes are stored as "
        "the service publishes them.",
    )
    valid = sum(num(r["gecerli_OY_TOPLAMI"]) for r in boxes)
    print(
        f"{len(result_sets)} contests, {len(boxes)} boxes ({len(new_units)} new units, {on_district} on a district "
        f"area); valid {valid} of {summary['gecerli_OY_TOPLAMI']} printed; registered "
        f"{sum(num(r['secmen_SAYISI']) for r in boxes)} of {summary['secmen_SAYISI']}",
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
