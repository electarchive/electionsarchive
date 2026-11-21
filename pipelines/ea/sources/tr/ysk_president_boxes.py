"""Turkey's 2014 presidential election by ballot box, from the Supreme Election Council's (YSK) Sandık Sonuçları site
(sonuc.ysk.gov.tr).

For each of the 81 provinces the results service lists every ballot box under its district with the registered voters,
voters, valid and invalid votes, and the votes for each of the three candidates. Two further files give the votes
cast abroad, by diplomatic mission, and at the customs gates, by ballot box. The contest and the candidates' options
are the ones the archive already holds from YSK's provincial totals; this package adds a count at the finest unit
and points at them."""

import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.sources.tr.ysk_meclis import SITE
from ea.store import DATA

SOURCE = "tr.ysk_president_boxes"
KEY = f"{SOURCE}/president-2014"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
AREAS = Path(__file__).with_name("ysk_sonuc_areas.tsv")
DAY = date(2014, 8, 10)
FOLDER = "browser://20261116-222924-4772b5/"
BOXES = FOLDER + "tr-2014-08-10-cumhurbaskani-ballot-box-results.ndjson.gz"
LABELS = "browser://20261117-003207-85b5ea/tr-2014-08-10-president-header-labels.json"
ABROAD = "browser://20261119-203153-64251f/tr-ysk-abroad-and-customs-ballot-box-all-elections.ndjson.gz"
ABROAD_NAME = "2014-presidential"
TITLE = "Sandık Sonuçları: cumhurbaşkanı seçimi"
CANDIDATES = {  # the service's column -> the candidate's ballot label in the archive
    "bagimsiz1_ALDIGI_OY": "Recep Tayyip Erdoğan",
    "bagimsiz2_ALDIGI_OY": "Selahattin Demirtaş",
    "bagimsiz3_ALDIGI_OY": "Ekmeleddin Mehmet İhsanoğlu",
}
NOTE = (
    "Results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the presidential "
    "election of August 10, 2014. For each of the 81 provinces, the service lists every ballot box under its "
    "district, with the registered voters, voters, valid and invalid votes, and the votes for each candidate. "
    "Separate files give the same figures for each diplomatic mission abroad and for each ballot box at the customs "
    "gates."
)
METHOD = (
    "Counted for each ballot box by YSK. A candidate's votes go to that candidate's option. Boxes in Turkey are "
    "placed in their neighborhood where the archive holds the neighborhood the service names, else in their "
    "district, else in their province. Votes abroad are given by diplomatic mission, not by box, and are stored as "
    "one unit for each mission, which has no area. Customs-gate boxes are placed in the province of their gate and "
    "open from the date the service gives for each box until the election day. The three groups add up to the "
    "national totals the service prints."
)
Row = dict[str, object]


def num(value: object) -> int:
    return int(str(value))


def sources() -> list[str]:
    return [BOXES, LABELS, ABROAD]


def fetch() -> None:
    """Load the files saved from the service (data/inbox/tr.ysk_president_boxes/) into the raw cache."""
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


def upper(text: str) -> str:
    """Turkish upper case, as the service writes its labels."""
    return text.replace("i", "İ").replace("ı", "I").upper()


class Read:
    def __init__(self) -> None:
        self.boxes: list[Row] = []
        self.districts: dict[tuple[int, int], Row] = {}
        self.summary: Row = {}
        self.national: Row = {}
        self.missions: list[Row] = []
        self.gates: list[Row] = []
        self.abroad_total: Row = {}
        self.customs_total: Row = {}


def read() -> Read:
    out = Read()
    with gzip.open(raw.cached(SOURCE, BOXES).path, "rt") as handle:
        records = [json.loads(line) for line in handle]
    seen: set[int] = set()
    for record in records:
        if record["type"] == "national_summary":
            out.summary = record["data"][0]
        elif record["type"] == "national_party_rows":
            out.national = record["data"][0]
        elif record["type"] == "il":
            il = num(record["il_id"])
            for row in record["ilce_summary"]:
                out.districts[(il, num(row["ilce_ID"]))] = row
            for district in record["ilceler"]:
                for box in district["sandik"]:
                    if num(box["il_ID"]) != il or num(box["ilce_ID"]) != num(district["ilce_id"]):
                        raise ValueError(f"box {box['sandik_ID']} listed under the wrong district")
                    if num(box["sandik_ID"]) in seen:
                        raise ValueError(f"box {box['sandik_ID']} listed twice")
                    seen.add(num(box["sandik_ID"]))
                    out.boxes.append(box)
        elif record["type"] == "ulke":
            out.missions.extend(record["data"])
    with gzip.open(raw.cached(SOURCE, ABROAD).path, "rt") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("name") != ABROAD_NAME:
                continue
            if record["type"] == "sandik_gumruk":
                out.gates.extend(record["rows"])
            elif record["type"] == "abroadAndCustoms_all":
                for row in record["data"]:
                    if row["il_ADI"].startswith("ÜLKELER"):
                        out.abroad_total = row
                    elif row["il_ADI"].startswith("GÜMRÜK"):
                        out.customs_total = row
    return out


def candidate_columns() -> dict[str, str]:
    """The service's candidate columns checked against the labels the service gives for every province."""
    labels = json.loads(raw.cached(SOURCE, LABELS).path.read_text())
    for province in labels["contests"]["9"]["basliklar"]:
        found = {h["column_NAME"]: h["ad"] for h in province["basliklar"]}
        for column, label in CANDIDATES.items():
            if found.get(column) != upper(label):
                raise ValueError(f"province {province['ilAdi']}: {column} is {found.get(column)!r}, not {label}")
        if len(found) != len(CANDIDATES):
            raise ValueError(f"province {province['ilAdi']}: columns {sorted(found)}")
    return CANDIDATES


def places() -> dict[tuple[str, str], str]:
    rows = pl.read_csv(AREAS, separator="\t", schema_overrides={"key": pl.String}).iter_rows()
    return {(kind, code): area for kind, code, area in rows}


def provinces() -> dict[int, str]:
    """The archive's province area for each plate number."""
    area = pl.read_parquet(REFERENCE / "area.parquet", columns=["id", "scheme_code"]).filter(
        pl.col("scheme_code") == "tr.il"
    )
    codes = pl.read_parquet(REFERENCE / "area_code.parquet", columns=["area_id", "code"]).filter(
        ~pl.col("code").str.contains("-")
    )
    joined = codes.join(area, left_on="area_id", right_on="id").select("area_id", "code")
    found = {int(code): str(area_id) for area_id, code in joined.iter_rows()}
    if sorted(found) != list(range(1, 82)):
        raise ValueError("the archive does not hold all 81 provinces")
    return found


def build() -> Package:
    data = read()
    columns = candidate_columns()
    contests = pl.read_parquet(REFERENCE / "contest.parquet").filter(
        pl.col("election_id").is_in(
            pl.read_parquet(REFERENCE / "election.parquet").filter(pl.col("date_from") == DAY)["id"].to_list()
        )
    )
    if contests.height != 1:
        raise ValueError(f"{contests.height} contests on {DAY}")
    contest = contests.row(0, named=True)
    contest_id = str(contest["id"])
    election_id = str(contest["election_id"])
    polity = str(pl.read_parquet(REFERENCE / "election.parquet").filter(pl.col("id") == election_id)["polity_id"][0])
    options = pl.read_parquet(REFERENCE / "contest_option.parquet").filter(pl.col("contest_id") == contest_id)
    option_of: dict[str, str] = {}
    for column, label in columns.items():
        match = options.filter(pl.col("ballot_label") == label)
        if match.height != 1:
            raise ValueError(f"{match.height} options for {label}")
        option_of[column] = str(match["id"][0])
        for group, rows, printed_row in (
            ("domestic", data.boxes, data.national),
            ("abroad", data.missions, data.abroad_total),
            ("customs", data.gates, data.customs_total),
        ):
            if sum(num(r[column]) for r in rows) != num(printed_row[column]):
                raise ValueError(f"{label}: the {group} units do not add up to the printed total")

    where = places()
    province = provinces()
    p = Package(KEY, SOURCE, "Turkey's 2014 presidential election by ballot box, from YSK's Sandık Sonuçları")
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
    for url in sources():
        p.artifact(raw.cached(SOURCE, url), source)
    result_set = p.id("result_set", f"{DAY}:president")
    valid = sum(num(b["gecerli_OY_TOPLAMI"]) for b in data.boxes) + sum(
        num(r["gecerli_OY_TOPLAMI"]) for r in [*data.missions, *data.gates]
    )
    printed = num(data.summary["gecerli_OY_TOPLAMI"])
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest_id,
        stage="alternative",
        kind="counted",
        status="active",
        source_id=source,
        is_default=False,
        completeness=round(valid / printed, 6),
    )
    p.add(
        "note",
        id=p.id("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD,
        lang="en",
    )

    units: list[Row] = []
    values: list[tuple[str, str, str | None, int, int]] = []
    external: set[str] = {polity, "tr.sandik", contest_id, election_id, *option_of.values()}
    count = {"domestic": 0, "abroad": 0, "customs": 0, "mahalle": 0, "ilce": 0, "il": 0}
    seen: set[str] = set()

    def add(code: str, kind: str, area: str | None, row: Row, start: date) -> None:
        if code in seen:
            raise ValueError(f"unit {code} twice")
        seen.add(code)
        unit = p.id("unit", code)
        units.append(
            {
                "id": unit,
                "polity_id": polity,
                "scheme_code": "tr.sandik",
                "code": code,
                "area_relation": "unlocated" if area is None else "within",
                "area_id": area,
                "kind": kind,
                "valid_from": start,
                "valid_to": DAY + timedelta(days=1),
            }
        )
        if area:
            external.add(area)
        for measure, column in (
            (REGISTERED, "secmen_SAYISI"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
            (VALID, "gecerli_OY_TOPLAMI"),
            (INVALID, "gecersiz_OY_TOPLAMI"),
        ):
            values.append((result_set, unit, None, measure, num(row[column])))
        for column, option in option_of.items():
            if num(row.get(column) or 0):
                values.append((result_set, unit, option, VOTES, num(row[column])))
        if sum(num(row.get(c) or 0) for c in option_of) != num(row["gecerli_OY_TOPLAMI"]):
            raise ValueError(f"unit {code}: candidates do not add up to the valid votes")

    for row in sorted(data.boxes, key=lambda r: num(r["sandik_ID"])):
        il, ilce = num(row["il_ID"]), num(row["ilce_ID"])
        if ("mahalle", str(row["muhtarlik_ID"])) in where:
            area, level = where[("mahalle", str(row["muhtarlik_ID"]))], "mahalle"
        elif ("ilce", f"{il}:{ilce}") in where:
            area, level = where[("ilce", f"{il}:{ilce}")], "ilce"
        else:
            area, level = province[il], "il"
        count[level] += 1
        count["domestic"] += 1
        add(
            f"{DAY}:{il:02d}:{ilce}:{row['sandik_NO']}",
            "prison" if row.get("cezaevi_ID") else "standard",
            area,
            row,
            DAY,
        )
    for row in sorted(data.missions, key=lambda r: (num(r["ulke_ID"]), num(r["dis_TEMSILCILIK_ID"]))):
        count["abroad"] += 1
        add(f"{DAY}:abroad:{row['ulke_ID']}:{row['dis_TEMSILCILIK_ID']}", "abroad_mission", None, row, DAY)
    for row in sorted(data.gates, key=lambda r: num(r["sandik_ID"])):
        count["customs"] += 1
        opened = datetime.fromisoformat(str(row["gumruk_SANDIK_TARIHI"]).replace("+0000", "+00:00")).date()
        add(f"{DAY}:customs:{row['sandik_ID']}", "customs_gate", province[num(row["il_ID"])], row, opened)

    p.frame("reporting_unit", pl.DataFrame(units, schema=_UNIT_SCHEMA))
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
        "In 1,743 boxes the service's printed voters exceed its printed registered voters, by a few votes; both counts "
        "are stored as the service prints them.",
    )
    total = {
        name: sum(num(r[column]) for r in [*data.boxes, *data.missions, *data.gates])
        for name, column in (
            ("registered", "secmen_SAYISI"),
            ("voters", "oy_KULLANAN_SECMEN_SAYISI"),
            ("valid", "gecerli_OY_TOPLAMI"),
            ("invalid", "gecersiz_OY_TOPLAMI"),
        )
    }
    print(
        f"units {count}; stored {total}; printed national valid {printed} voters "
        f"{data.summary['oy_KULLANAN_SECMEN_SAYISI']} invalid {data.summary['gecersiz_OY_TOPLAMI']} "
        f"registered (all) {data.summary['secmen_SAYISI']} registered (boxes opened) "
        f"{data.summary['acilan_SECMEN_SAYISI']}",
        flush=True,
    )
    for (il, ilce), summary in sorted(data.districts.items()):
        mine = [b for b in data.boxes if num(b["il_ID"]) == il and num(b["ilce_ID"]) == ilce]
        if sum(num(b["gecerli_OY_TOPLAMI"]) for b in mine) != num(summary["gecerli_OY_TOPLAMI"]):
            print(f"district {il}:{ilce}: boxes do not add up to the printed valid votes", flush=True)
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
