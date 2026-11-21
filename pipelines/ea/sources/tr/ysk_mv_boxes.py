"""Turkey's 2007 general election by ballot box, from the Supreme Election Council's (YSK) Sandık Sonuçları site
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

SOURCE = "tr.ysk_mv_boxes"
KEY = f"{SOURCE}/parliament-2007"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
DAY = date(2007, 7, 22)
BOXES = "browser://20261116-222924-4772b5/tr-2007-07-22-milletvekili-general-ballot-box-results.ndjson.gz"
LABELS = (
    "https://sonuc.ysk.gov.tr/api/getSandikSecimSonucBaslikList?secimId=10701&secimTuru=8&secimCevresiId="
    "&ilId={il}&bagimsiz=1&yurtIciDisi=1"
)
TITLE = "Sandık Sonuçları: milletvekili genel seçimi"
NOTE = (
    "Parliamentary results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for "
    "the general election of July 22, 2007. For each of the 81 provinces, the service lists every ballot box under "
    "its district, with the registered voters, voters, valid and invalid votes, and the votes for each party list "
    "and for the independents together. It names the independents only in a header for each province, which this "
    "package does not use. It gives no figures for the customs gates or for voters abroad, and no outcome."
)
METHOD = (
    "Counted for each ballot box by YSK. A party's votes go to its list's option and the independents' votes go to "
    "the contest's one option for independents. Each box belongs to the electoral district of its service district; "
    "Istanbul, Ankara, and Izmir are split into two or three electoral districts, which the service marks in the "
    "district summaries. The service also prints each district's prison boxes as a separate summary row under the "
    "name of another province; those rows repeat boxes that are listed under the district and are not used. Boxes "
    "listed more than once are stored once. The boxes of each district add up to the district totals the service "
    "prints, apart from its prison boxes."
)
# the party each column of the service's headers stands for, as the label the header gives it
PARTIES = {
    "ATP": "aydinlik-turkiye-partisi",
    "BTP": "bagimsiz-turkiye-partisi",
    "SAADET": "saadet-partisi",
    "İP": "isci-partisi",
    "CHP": "cumhuriyet-halk-partisi",
    "HYP": "halkin-yukselisi-partisi",
    "ÖDP": "ozgurluk-ve-dayanisma-partisi",
    "GENÇPARTİ": "yeniden-dogus-partisi",
    "DP": "demokrat-parti",
    "LDP": "liberal-demokrat-parti",
    "MHP": "milliyetci-hareket-partisi",
    "AK PARTİ": "adalet-ve-kalkinma-partisi",
    "EMEP": "emek-partisi",
    "TKP": "turkiye-komunist-partisi",
}
Row = dict[str, object]


def num(value: object) -> int:
    return int(str(value))


def sources() -> list[str]:
    return [BOXES] + [LABELS.format(il=il) for il in range(1, 82)]


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
        self.columns: dict[str, str] = {}
        self.headers: list[str] = []


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
    for il in range(1, 82):
        if not raw.keys(SOURCE) >= {LABELS.format(il=il)}:
            continue
        header = json.loads(raw.cached(SOURCE, LABELS.format(il=il)).path.read_text())
        out.headers.append(LABELS.format(il=il))
        for column in header:
            if not str(column["column_NAME"]).startswith("parti"):
                continue
            if out.columns.setdefault(str(column["column_NAME"]), str(column["ad"])) != column["ad"]:
                raise ValueError(f"province {il}: column {column['column_NAME']} is labeled {column['ad']}")
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
    parties = x.filter(pl.col("scheme") == "tr.party")
    party_id = dict(zip(parties["value"], parties["entity_id"], strict=True))
    elections = pl.read_parquet(REFERENCE / "election.parquet").filter(
        pl.col("id").is_in([row["election_id"] for row in contest.values()])
    )
    if elections["polity_id"].n_unique() != 1:
        raise ValueError("the contests' elections are not in one country")
    polity = str(elections["polity_id"][0])

    if set(data.columns.values()) - set(PARTIES):
        raise ValueError(f"labels {sorted(set(data.columns.values()) - set(PARTIES))} have no party")
    option_of: dict[tuple[str, str], str] = {}
    independents: dict[str, str] = {}
    for key, contest_id in contests.items():
        for column, label in data.columns.items():
            found = [o for o in options[str(contest_id)] if o["party_id"] == party_id[PARTIES[label]]]
            if len(found) != 1:
                raise ValueError(f"contest {key}: {len(found)} options for {label}")
            option_of[(key, column)] = str(found[0]["id"])
        found = [o for o in options[str(contest_id)] if o["kind"] == "independents"]
        if len(found) != 1:
            raise ValueError(f"contest {key}: {len(found)} options for independents")
        independents[key] = str(found[0]["id"])

    def contest_key(il: int, ilce: int) -> str:
        label = data.circles[(il, ilce)]
        match = re.fullmatch(r".*-(\d)", label)
        return f"{il:02d}-{match[1]}" if match else f"{il:02d}"

    p = Package(KEY, SOURCE, "Turkey's 2007 general election by ballot box, from YSK's Sandık Sonuçları")
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
    for url in [BOXES, *data.headers]:
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
        for column in data.columns:
            value = num(row.get(f"{column}") or 0)
            if value:
                values.append((result_set, unit, option_of[(key, column)], VOTES, value))
        stray = [
            c
            for c, v in row.items()
            if c.endswith("_ALDIGI_OY") and v and c not in data.columns and "bagimsiz" not in c
        ]
        if stray:
            raise ValueError(f"box {code}: columns {stray} have no label")
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
        "In some boxes the service gives more voters than registered voters, by a few votes; the boxes are stored as "
        "the service publishes them.",
    )
    p.accept(
        "accounting.votes_valid",
        "In 8,637 Istanbul boxes the service's valid votes exceed the sum of the list votes and the independents' "
        "votes by up to 94 votes. The shortfall is in the independents' columns, which the service stops at 50 for "
        "each province, and the boxes are stored as the service publishes them.",
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
