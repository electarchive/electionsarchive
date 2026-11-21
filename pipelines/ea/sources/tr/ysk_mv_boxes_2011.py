"""Turkey's 2011 general election by ballot box, from the Supreme Election Council's (YSK) Sandık Sonuçları site
(sonuc.ysk.gov.tr).

For each of the 81 provinces the results service lists every ballot box under its district with the registered voters,
voters, valid and invalid votes, the votes for each party list, and the votes for the independents. Istanbul, Ankara,
and Izmir are split into electoral districts that cut across the service's own districts. The contests are the ones
the archive already holds from the Official Gazette's district tables; this package adds a count at the finest unit
and points at them. The service gives no figures for the customs gates or for voters abroad."""

import gzip
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.sources.tr.ysk_meclis import SITE
from ea.store import DATA

SOURCE = "tr.ysk_mv_boxes_2011"
KEY = f"{SOURCE}/parliament-2011"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
DAY = date(2011, 6, 12)
BOXES = "browser://20261116-222924-4772b5/tr-2011-06-12-milletvekili-general-ballot-box-results.ndjson.gz"
TITLE = "Sandık Sonuçları: milletvekili genel seçimi"
NOTE = (
    "Parliamentary results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for "
    "the general election of June 12, 2011. For each of the 81 provinces, the service lists every ballot box under "
    "its district, with the registered voters, voters, valid and invalid votes, and the votes for each party list "
    "and for the independents together; the files saved from the service carry no column labels, so the party "
    "columns are matched to the lists by their national totals. It gives no figures for the customs gates or for voters abroad, and no outcome."
)
METHOD = (
    "Counted for each ballot box by YSK. A party's votes go to its list's option and the independents' votes go to "
    "the contest's one option for independents. Each box belongs to the electoral district of its service district; "
    "Istanbul, Ankara, and Izmir are split into two or three electoral districts, which the service marks in the "
    "district summaries. Boxes listed more than once are stored once. The boxes of each "
    "district add up to the district totals the service prints."
)
# The service's party columns, as the lists' ballot labels in the archive. The saved files carry no header, so the
# columns are matched to the lists by their national totals, which are the figures the archive holds from the
# Official Gazette (the list order of the gazette's table is the column order; every total is distinct).
PARTIES = {
    "parti1": ("Adalet ve Kalkınma Partisi", 21320207),
    "parti2": ("Demokrat Parti", 278775),
    "parti3": ("Cumhuriyet Halk Partisi", 11122420),
    "parti4": ("Emek Partisi", 31766),
    "parti5": ("Millet Partisi", 60673),
    "parti6": ("Liberal Demokrat Parti", 15166),
    "parti7": ("Saadet Partisi", 541470),
    "parti8": ("Hak ve Eşitlik Partisi", 124082),
    "parti9": ("Halkın Sesi Partisi", 329358),
    "parti10": ("Milliyetçi Hareket Partisi", 5575010),
    "parti11": ("Doğru Yol Partisi", 64453),
    "parti12": ("Türkiye Komünist Partisi", 63786),
    "parti13": ("Milliyetçi ve Muhafazakar Parti", 36105),
    "parti14": ("Büyük Birlik Partisi", 322819),
    "parti15": ("Demokratik Sol Parti", 107889),
}
Row = dict[str, object]


def num(value: object) -> int:
    return int(str(value))


def sources() -> list[str]:
    return [BOXES]


def fetch() -> None:
    """Load the files saved from the service (data/inbox/tr.ysk_mv_boxes/) into the raw cache."""
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


class Read:
    def __init__(self) -> None:
        self.boxes: list[Row] = []
        self.circles: dict[tuple[int, int], str] = {}
        self.summary: dict[str, int] = {}
        self.national: Row = {}
        self.prison_rows = 0
        self.prison_votes = 0
        self.repeated = 0


def read() -> Read:
    """The service's boxes with the electoral district of each, its printed totals, and its column labels."""
    out = Read()
    with gzip.open(raw.cached(SOURCE, BOXES).path, "rt") as handle:
        records = [json.loads(line) for line in handle]
    names: dict[int, set[str]] = defaultdict(set)
    for record in records:
        if record["type"] == "national_summary":
            out.summary = record["data"][0]
        elif record["type"] == "national_party_rows":
            out.national = record["data"][0]
        elif record["type"] == "il_list":
            for circle in record["data"]:
                names[num(circle["il_ID"])].add(circle["il_ADI"])
    seen: dict[int, Row] = {}
    for record in records:
        if record["type"] != "il":
            continue
        il = num(record["il_id"])
        for row in record["ilce_summary"]:
            if row["il_ADI"] in names[il]:
                out.circles[(il, num(row["ilce_ID"]))] = row["il_ADI"]
            else:
                out.prison_rows += 1
                out.prison_votes += num(row["gecerli_OY_TOPLAMI"])
        for district in record["ilceler"]:
            for box in district["sandik"]:
                if num(box["il_ID"]) != il or num(box["ilce_ID"]) != num(district["ilce_id"]):
                    raise ValueError(f"box {box['sandik_ID']} listed under the wrong district")
                box["il"] = il
                key = num(box["sandik_ID"])
                if key in seen:
                    if seen[key] != box:
                        raise ValueError(f"box {key} listed twice with different figures")
                    out.repeated += 1
                    continue
                seen[key] = box
                out.boxes.append(box)
    return out


def build() -> Package:
    data = read()
    boxes = data.boxes
    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value", "pipeline"])
    contests = {
        value.removeprefix(f"{DAY}:parliament:"): entity
        for value, entity in x.filter(
            (pl.col("scheme") == "tr.contest")
            & (pl.col("pipeline") == "tr.ysk")
            & pl.col("value").str.starts_with(f"{DAY}:parliament:")
        )
        .select("value", "entity_id")
        .iter_rows()
    }
    if len(contests) != 85:
        raise ValueError(f"{len(contests)} district contests")
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
    elections = pl.read_parquet(REFERENCE / "election.parquet").filter(
        pl.col("id").is_in([row["election_id"] for row in contest.values()])
    )
    if elections["polity_id"].n_unique() != 1:
        raise ValueError("the contests' elections are not in one country")
    polity = str(elections["polity_id"][0])

    columns = {f"{key}_ALDIGI_OY": label for key, (label, _) in PARTIES.items()}  # column -> list label
    for key, (label, total) in PARTIES.items():
        if num(data.national[f"{key}_ALDIGI_OY"]) != total:
            raise ValueError(f"{key} is {data.national[f'{key}_ALDIGI_OY']} nationally, not {total} for {label}")
    option_of: dict[tuple[str, str], str] = {}
    independents: dict[str, str] = {}
    for key, contest_id in contests.items():
        for column, label in columns.items():
            found = [o for o in options[str(contest_id)] if o["ballot_label"] == label]
            if len(found) == 1:
                option_of[(key, column)] = str(found[0]["id"])
            elif found:
                raise ValueError(f"contest {key}: {len(found)} options for {label}")
        found = [o for o in options[str(contest_id)] if o["kind"] == "independents"]
        if len(found) != 1:
            raise ValueError(f"contest {key}: {len(found)} options for independents")
        independents[key] = str(found[0]["id"])

    def contest_key(il: int, ilce: int) -> str:
        label = data.circles[(il, ilce)]
        match = re.fullmatch(r".*-(\d)", label)
        return f"{il:02d}-{match[1]}" if match else f"{il:02d}"

    p = Package(KEY, SOURCE, "Turkey's 2011 general election by ballot box, from YSK's Sandık Sonuçları")
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
    cited: set[str] = set()
    for url in [BOXES]:
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in cited:
            cited.add(artifact.storage_key)
            p.artifact(artifact, source)

    external: set[str] = {polity, "tr.sandik"}
    result_sets: dict[str, str] = {}
    for key, contest_id in sorted(contests.items()):
        row = contest[contest_id]
        external |= {str(contest_id), str(row["election_id"]), *option_of_values(option_of, key), independents[key]}
        result_set = p.id("result_set", f"{DAY}:{key}")
        result_sets[key] = result_set
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

    values: list[tuple[str, str, str | None, int, int]] = []
    new_rows: list[Row] = []
    totals = {"boxes": 0, "registered": 0, "voters": 0, "valid": 0, "invalid": 0}
    for row in sorted(boxes, key=lambda r: num(r["sandik_ID"])):
        il, ilce = num(row["il"]), num(row["ilce_ID"])
        key = contest_key(il, ilce)
        result_set = result_sets[key]
        code = f"{DAY.year}:{il:02d}:{ilce}:{row['sandik_NO']}"
        unit = p.id("unit", code)
        area = str(contest[contests[key]]["district_area_id"])
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
        for measure, column, name in (
            (REGISTERED, "secmen_SAYISI", "registered"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI", "voters"),
            (VALID, "gecerli_OY_TOPLAMI", "valid"),
            (INVALID, "gecersiz_OY_TOPLAMI", "invalid"),
        ):
            values.append((result_set, unit, None, measure, num(row[column])))
            totals[name] += num(row[column])
        totals["boxes"] += 1
        for column, label in columns.items():
            value = num(row.get(column) or 0)
            if value:
                if (key, column) not in option_of:
                    raise ValueError(f"box {code}: votes for {label}, which is not on the contest's ballot")
                values.append((result_set, unit, option_of[(key, column)], VOTES, value))
        stray = [
            c for c, v in row.items() if c.endswith("_ALDIGI_OY") and v and c not in columns and "bagimsiz" not in c
        ]
        if stray:
            raise ValueError(f"box {code}: columns {stray} are not a known list")
        loose = sum(num(v) for c, v in row.items() if re.fullmatch(r"bagimsiz\d+_ALDIGI_OY", c) and v)
        if loose != num(row["bagimsiz_TOPLAM_OY"]):
            raise ValueError(f"box {code}: independents {loose} against total {row['bagimsiz_TOPLAM_OY']}")
        if num(row["bagimsiz_TOPLAM_OY"]):
            values.append((result_set, unit, independents[key], VOTES, num(row["bagimsiz_TOPLAM_OY"])))

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
        "In 3,459 boxes the service gives more voters than registered voters, by a few votes; the boxes are stored as "
        "the service publishes them.",
    )
    print(
        f"{len(result_sets)} contests, {totals['boxes']} boxes ({data.repeated} repeated rows dropped, "
        f"{data.prison_rows} prison summary rows set aside); registered {totals['registered']} voters "
        f"{totals['voters']} valid {totals['valid']} invalid {totals['invalid']}; printed domestic valid "
        f"{data.national['gecerli_OY_TOPLAMI']} registered {data.national['secmen_SAYISI']} voters "
        f"{data.national['oy_KULLANAN_SECMEN_SAYISI']} invalid {data.national['gecersiz_OY_TOPLAMI']}",
        flush=True,
    )
    return p


def option_of_values(option_of: dict[tuple[str, str], str], key: str) -> list[str]:
    return [option for (k, _), option in option_of.items() if k == key]


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
