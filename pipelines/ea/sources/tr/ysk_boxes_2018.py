"""Turkey's June 24, 2018 presidential and general elections by ballot box, from the Supreme Election Council's (YSK)
Sandık Sonuçları site (sonuc.ysk.gov.tr).

For each of the 81 provinces the results service lists every ballot box under its district with the registered
voters, voters, valid and invalid votes, and the votes for each presidential candidate; the same boxes carry the
votes for each party list, each alliance, and the independents together in the general election. Two further files
give the votes cast abroad, by diplomatic mission, and at the customs gates, by ballot box. The contests and their
options are the ones the archive already holds from YSK's provincial totals; this package adds a count at the finest
unit and points at them. Both elections were held with the same boxes, so one package holds both and the two share
their reporting units."""

import gzip
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.sources.tr.ysk_meclis import SITE
from ea.store import DATA

SOURCE = "tr.ysk_boxes_2018"
KEY = f"{SOURCE}/general-2018"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
AREAS = Path(__file__).with_name("ysk_sonuc_areas.tsv")
DAY = date(2018, 6, 24)
FOLDER = "browser://20261116-222924-4772b5/"
PRESIDENT = FOLDER + "tr-2018-06-24-cumhurbaskani-ballot-box-results.ndjson.gz"
PARLIAMENT = FOLDER + "tr-2018-06-24-milletvekili-general-ballot-box-results.ndjson.gz"
LABELS = "browser://20261117-003207-85b5ea/tr-2018-06-24-president-parliament-header-labels.json"
ABROAD = "browser://20261119-203153-64251f/tr-ysk-abroad-and-customs-ballot-box-all-elections.ndjson.gz"
ABROAD_NAMES = {"president": "2018-presidential", "parliament": "2018-parliamentary"}
TITLE = "Sandık Sonuçları: cumhurbaşkanı ve milletvekili genel seçimi"
CANDIDATES = {  # the service's column -> the candidate's ballot label in the archive
    "bagimsiz1_ALDIGI_OY": "Muharrem İnce",
    "bagimsiz2_ALDIGI_OY": "Meral Akşener",
    "bagimsiz3_ALDIGI_OY": "Recep Tayyip Erdoğan",
    "bagimsiz4_ALDIGI_OY": "Selahattin Demirtaş",
    "bagimsiz5_ALDIGI_OY": "Temel Karamollaoğlu",
    "bagimsiz6_ALDIGI_OY": "Doğu Perinçek",
}
LISTS = {  # the service's party and alliance columns -> the option's ballot label in the archive
    "parti1_ALDIGI_OY": "Adalet ve Kalkınma Partisi",
    "parti2_ALDIGI_OY": "Milliyetçi Hareket Partisi",
    "parti3_ALDIGI_OY": "Hür Dava Partisi",
    "parti4_ALDIGI_OY": "Vatan Partisi",
    "parti5_ALDIGI_OY": "Halkların Demokratik Partisi",
    "parti6_ALDIGI_OY": "Cumhuriyet Halk Partisi",
    "parti7_ALDIGI_OY": "Saadet Partisi",
    "parti8_ALDIGI_OY": "İYİ Parti",
    "ittifak1_ALDIGI_OY": "Cumhur İttifakı",
    "ittifak2_ALDIGI_OY": "Millet İttifakı",
}
HEADERS = {  # the labels the service prints over those columns
    "parti1_ALDIGI_OY": "AK PARTİ",
    "parti2_ALDIGI_OY": "MHP",
    "parti3_ALDIGI_OY": "HÜDA PAR",
    "parti4_ALDIGI_OY": "VATAN PARTİSİ",
    "parti5_ALDIGI_OY": "HDP",
    "parti6_ALDIGI_OY": "CHP",
    "parti7_ALDIGI_OY": "SAADET",
    "parti8_ALDIGI_OY": "İYİ PARTİ",
    "ittifak1_ALDIGI_OY": "CUMHUR İTTİFAKI",
    "ittifak2_ALDIGI_OY": "MİLLET İTTİFAKI",
}
NOTE = (
    "Results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the presidential "
    "and general elections of June 24, 2018. For each of the 81 provinces, the service lists every ballot box under "
    "its district, with the registered voters, voters, valid and invalid votes, the votes for each presidential "
    "candidate, and the votes for each party list, each alliance, and the independents together. Separate files give "
    "the same figures for each diplomatic mission abroad and for each ballot box at the customs gates."
)
METHOD_PRESIDENT = (
    "Counted for each ballot box by YSK. A candidate's votes go to that candidate's option. Boxes in Turkey are "
    "placed in their neighborhood where the archive holds the neighborhood the service names, else in their "
    "district, else in their province. Votes abroad are given by diplomatic mission, not by box, and are stored as "
    "one unit for each mission, which has no area. Customs-gate boxes are placed in the province of their gate and "
    "open from the date the service gives for each box until the election day. The three groups add up to the "
    "national totals the service prints."
)
METHOD_DISTRICT = (
    "Counted for each ballot box by YSK. A party's votes go to its list's option, an alliance's votes to the "
    "alliance's option, and the independents' votes to the contest's one option for independents. Each box belongs "
    "to the electoral district of its service district; Ankara, Bursa, Istanbul, and Izmir are split into two or "
    "three electoral districts, which the service marks in the district summaries. Boxes are placed in their "
    "neighborhood where the archive holds the neighborhood the service names, else in their district, else in their "
    "province. The boxes of each district add up to the district totals the service prints. Votes abroad and at the "
    "customs gates belong to no electoral district and are in the national contest's set."
)
METHOD_NATIONAL = (
    "Counted for each ballot box by YSK. This set holds the votes cast abroad, by diplomatic mission, and at the "
    "customs gates, by ballot box, which count toward the national totals but toward no electoral district. A "
    "party's votes go to its list's option and an alliance's votes to the alliance's option. Missions have no area; "
    "customs-gate boxes are placed in the province of their gate and open from the date the service gives for each "
    "box until the election day. The votes in Turkey are in the electoral districts' sets, and together the two add "
    "up to the national totals the service prints."
)
Row = dict[str, object]
Value = tuple[str, str, str | None, int, int]


def num(value: object) -> int:
    return int(str(value))


def sources() -> list[str]:
    return [PRESIDENT, PARLIAMENT, LABELS, ABROAD]


def fetch() -> None:
    """Load the files saved from the service (data/inbox/tr.ysk_boxes_2018/) into the raw cache."""
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
        self.circles: dict[tuple[int, int], str] = {}
        self.districts: dict[tuple[int, int], Row] = {}
        self.summary: Row = {}
        self.national: Row = {}
        self.missions: list[Row] = []
        self.gates: list[Row] = []
        self.abroad_total: Row = {}
        self.customs_total: Row = {}
        self.prison_rows = 0


def read(url: str, abroad_name: str) -> Read:
    out = Read()
    with gzip.open(raw.cached(SOURCE, url).path, "rt") as handle:
        records = [json.loads(line) for line in handle]
    names: dict[int, set[str]] = defaultdict(set)
    for record in records:
        if record["type"] == "il_list":
            for circle in record["data"]:
                names[num(circle["il_ID"])].add(circle["il_ADI"])
    seen: set[int] = set()
    for record in records:
        if record["type"] == "national_summary":
            out.summary = record["data"][0]
        elif record["type"] == "national_party_rows":
            out.national = record["data"][0]
        elif record["type"] == "il":
            il = num(record["il_id"])
            for row in record["ilce_summary"]:
                if row["il_ADI"] in names[il]:
                    out.circles[(il, num(row["ilce_ID"]))] = row["il_ADI"]
                    out.districts[(il, num(row["ilce_ID"]))] = row
                else:
                    out.prison_rows += 1
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
    gates: dict[int, Row] = {}
    with gzip.open(raw.cached(SOURCE, ABROAD).path, "rt") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("name") != abroad_name:
                continue
            if record["type"] == "sandik_gumruk":
                for row in record["rows"]:
                    key = num(row["sandik_ID"])
                    if key in gates and gates[key] != row:
                        raise ValueError(f"customs box {key} listed twice with different figures")
                    gates[key] = row
            elif record["type"] == "abroadAndCustoms_all":
                for row in record["data"]:
                    if row["il_ADI"].startswith("ÜLKELER"):
                        out.abroad_total = row
                    elif row["il_ADI"].startswith("GÜMRÜK"):
                        out.customs_total = row
    out.gates = list(gates.values())
    if len({(num(r["ulke_ID"]), num(r["dis_TEMSILCILIK_ID"])) for r in out.missions}) != len(out.missions):
        raise ValueError("a mission is listed twice")
    return out


def columns_of(labels: dict[str, Any], contest: str, wanted: dict[str, str], partial: bool = False) -> None:
    """Check that every province or district prints each wanted column under the label expected for it. In the
    general election a list that stood in only some districts has no column in the others."""
    block = labels["contests"][contest]
    for circle in block["basliklar"]:
        found = {
            h["column_NAME"]: h["ad"]
            for h in circle["basliklar"]
            if re.fullmatch(r"(parti|ittifak|bagimsiz)\d+_ALDIGI_OY", h["column_NAME"])
        }
        for column, label in wanted.items():
            if found.get(column) != label and not (partial and column not in found):
                raise ValueError(f"{circle['ilAdi']}: {column} is {found.get(column)!r}, not {label!r}")
        stray = [c for c in found if c not in wanted and not (contest == "8" and c.startswith("bagimsiz"))]
        if stray:
            raise ValueError(f"{circle['ilAdi']}: columns {sorted(stray)}")


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
    president = read(PRESIDENT, ABROAD_NAMES["president"])
    parliament = read(PARLIAMENT, ABROAD_NAMES["parliament"])
    labels = json.loads(raw.cached(SOURCE, LABELS).path.read_text())
    columns_of(labels, "9", {c: upper(n) for c, n in CANDIDATES.items()})
    columns_of(labels, "8", HEADERS, partial=True)

    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value", "pipeline"])
    found = {
        value: entity
        for value, entity in x.filter(
            (pl.col("scheme") == "tr.contest")
            & (pl.col("pipeline") == "tr.ysk")
            & pl.col("value").str.starts_with(f"{DAY}:")
        )
        .select("value", "entity_id")
        .iter_rows()
    }
    contests = {v.removeprefix(f"{DAY}:parliament:"): e for v, e in found.items() if v.startswith(f"{DAY}:parliament:")}
    if len(contests) != 87 or f"{DAY}:president" not in found or f"{DAY}:parliament" not in found:
        raise ValueError(f"{len(contests)} district contests")
    president_contest = found[f"{DAY}:president"]
    national_contest = found[f"{DAY}:parliament"]
    contest = {
        row["id"]: row
        for row in pl.read_parquet(REFERENCE / "contest.parquet")
        .filter(pl.col("id").is_in([*contests.values(), president_contest, national_contest]))
        .iter_rows(named=True)
    }
    options: dict[str, list[Row]] = defaultdict(list)
    for row in (
        pl.read_parquet(REFERENCE / "contest_option.parquet")
        .filter(pl.col("contest_id").is_in(list(contest)))
        .iter_rows(named=True)
    ):
        options[str(row["contest_id"])].append(row)
    elections = pl.read_parquet(REFERENCE / "election.parquet").filter(
        pl.col("id").is_in([row["election_id"] for row in contest.values()])
    )
    if elections.height != 1:
        raise ValueError("the contests are not in one election")
    election_id = str(elections["id"][0])
    polity = str(elections["polity_id"][0])

    def option_for(contest_id: str, label: str, kind: str | None = None) -> str:
        match = [o for o in options[contest_id] if o["ballot_label"] == label and (kind is None or o["kind"] == kind)]
        if len(match) != 1:
            raise ValueError(f"contest {contest_id}: {len(match)} options for {label}")
        return str(match[0]["id"])

    def independents_of(contest_id: str) -> str:
        match = [o for o in options[contest_id] if o["kind"] == "independents"]
        if len(match) != 1:
            raise ValueError(f"contest {contest_id}: {len(match)} options for independents")
        return str(match[0]["id"])

    candidate_option = {c: option_for(president_contest, label) for c, label in CANDIDATES.items()}
    list_option = {
        key: {c: option_for(str(contest_id), label) for c, label in LISTS.items()}
        for key, contest_id in [*contests.items(), ("", national_contest)]
    }
    independents = {key: independents_of(str(contests[key])) for key in contests}

    # The printed national totals, for the candidates and for every list and alliance.
    for name, data, wanted in (
        ("president", president, CANDIDATES),
        ("parliament", parliament, LISTS),
    ):
        for column, label in wanted.items():
            for group, rows, printed_row in (
                ("domestic", data.boxes, data.national),
                ("abroad", data.missions, data.abroad_total),
                ("customs", data.gates, data.customs_total),
            ):
                if sum(num(r.get(column) or 0) for r in rows) != num(printed_row[column]):
                    raise ValueError(f"{name} {label}: the {group} units do not add up to the printed total")

    where = places()
    province = provinces()
    p = Package(
        KEY, SOURCE, "Turkey's 2018 presidential and general elections by ballot box, from YSK's Sandık Sonuçları"
    )
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

    def valid_of(rows: list[Row]) -> int:
        return sum(num(r["gecerli_OY_TOPLAMI"]) for r in rows)

    def contest_key(il: int, ilce: int) -> str:
        label = parliament.circles[(il, ilce)]
        match = re.fullmatch(r".*-(\d)", label)
        return f"{il:02d}-{match[1]}" if match else f"{il:02d}"

    external: set[str] = {polity, "tr.sandik", election_id, str(president_contest), str(national_contest)}
    external |= {*candidate_option.values(), *(o for m in list_option.values() for o in m.values())}
    external |= {str(c) for c in contests.values()} | set(independents.values())

    def make_set(key: str, contest_id: str, completeness: float, method: str) -> str:
        result_set = p.id("result_set", f"{DAY}:{key}")
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
            completeness=round(completeness, 6),
        )
        p.add(
            "note",
            id=p.id("note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=method,
            lang="en",
        )
        return result_set

    circle_valid: dict[str, int] = defaultdict(int)
    circle_printed: dict[str, int] = defaultdict(int)
    for box in parliament.boxes:
        circle_valid[contest_key(num(box["il_ID"]), num(box["ilce_ID"]))] += num(box["gecerli_OY_TOPLAMI"])
    for (il, ilce), summary in parliament.districts.items():
        circle_printed[contest_key(il, ilce)] += num(summary["gecerli_OY_TOPLAMI"])
    for key in contests:
        if circle_valid[key] != circle_printed[key]:
            raise ValueError(f"district {key}: boxes add up to {circle_valid[key]}, printed {circle_printed[key]}")

    set_president = make_set(
        "president",
        str(president_contest),
        (valid_of(president.boxes) + valid_of(president.missions) + valid_of(president.gates))
        / num(president.summary["gecerli_OY_TOPLAMI"]),
        METHOD_PRESIDENT,
    )
    set_district = {
        key: make_set(f"parliament:{key}", str(contests[key]), circle_valid[key] / circle_printed[key], METHOD_DISTRICT)
        for key in sorted(contests)
    }
    abroad_valid = valid_of(parliament.missions) + valid_of(parliament.gates)
    set_national = make_set(
        "parliament",
        str(national_contest),
        abroad_valid / num(parliament.summary["gecerli_OY_TOPLAMI"]),
        METHOD_NATIONAL,
    )

    units: dict[str, Row] = {}
    values: list[Value] = []
    count = {"domestic": 0, "abroad": 0, "customs": 0, "mahalle": 0, "ilce": 0, "il": 0}

    def unit_of(code: str, kind: str, area: str | None, start: date) -> str:
        unit = p.id("unit", code)
        row: Row = {
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
        if code in units and units[code] != row:
            raise ValueError(f"unit {code} differs between the two elections")
        units[code] = row
        if area:
            external.add(area)
        return unit

    def put(
        result_set: str,
        unit: str,
        row: Row,
        code: str,
        wanted: dict[str, str],
        option_of: dict[str, str],
        loose: str | None,
    ) -> None:
        for measure, column in (
            (REGISTERED, "secmen_SAYISI"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
            (VALID, "gecerli_OY_TOPLAMI"),
            (INVALID, "gecersiz_OY_TOPLAMI"),
        ):
            values.append((result_set, unit, None, measure, num(row[column])))
        total = 0
        for column in wanted:
            votes = num(row.get(column) or 0)
            total += votes
            if votes:
                values.append((result_set, unit, option_of[column], VOTES, votes))
        if loose is not None:
            spread = sum(num(v) for c, v in row.items() if re.fullmatch(r"bagimsiz\d+_ALDIGI_OY", c) and v)
            if spread != num(row["bagimsiz_TOPLAM_OY"]):
                raise ValueError(f"unit {code}: independents {spread} against total {row['bagimsiz_TOPLAM_OY']}")
            if num(row["bagimsiz_TOPLAM_OY"]):
                values.append((result_set, unit, loose, VOTES, num(row["bagimsiz_TOPLAM_OY"])))
            total += num(row["bagimsiz_TOPLAM_OY"])
        if total != num(row["gecerli_OY_TOPLAMI"]):
            raise ValueError(f"unit {code}: options add up to {total}, valid votes {row['gecerli_OY_TOPLAMI']}")
        stray = [
            c
            for c, v in row.items()
            if re.fullmatch(r"(parti|ittifak|bagimsiz)\d+_ALDIGI_OY", c)
            and v
            and c not in wanted
            and not (loose is not None and c.startswith("bagimsiz"))
        ]
        if stray:
            raise ValueError(f"unit {code}: columns {stray} are not on the ballot")

    parliament_box = {num(b["sandik_ID"]): b for b in parliament.boxes}
    if set(parliament_box) != {num(b["sandik_ID"]) for b in president.boxes}:
        raise ValueError("the two elections do not share their boxes")
    for row in sorted(president.boxes, key=lambda r: num(r["sandik_ID"])):
        il, ilce = num(row["il_ID"]), num(row["ilce_ID"])
        twin = parliament_box[num(row["sandik_ID"])]
        if (il, ilce, row["sandik_NO"], row["muhtarlik_ID"], row.get("cezaevi_ID")) != (
            num(twin["il_ID"]),
            num(twin["ilce_ID"]),
            twin["sandik_NO"],
            twin["muhtarlik_ID"],
            twin.get("cezaevi_ID"),
        ):
            raise ValueError(f"box {row['sandik_ID']} is not the same box in the two elections")
        if ("mahalle", str(row["muhtarlik_ID"])) in where:
            area, level = where[("mahalle", str(row["muhtarlik_ID"]))], "mahalle"
        elif ("ilce", f"{il}:{ilce}") in where:
            area, level = where[("ilce", f"{il}:{ilce}")], "ilce"
        else:
            area, level = province[il], "il"
        count[level] += 1
        count["domestic"] += 1
        code = f"{DAY}:{il:02d}:{ilce}:{row['sandik_NO']}"
        unit = unit_of(code, "prison" if row.get("cezaevi_ID") else "standard", area, DAY)
        put(set_president, unit, row, code, CANDIDATES, candidate_option, None)
        key = contest_key(il, ilce)
        put(set_district[key], unit, twin, code, LISTS, list_option[key], independents[key])
    for rows_p, rows_q, kind in (
        (president.missions, parliament.missions, "abroad"),
        (president.gates, parliament.gates, "customs"),
    ):
        for data, rows, result_set, wanted, option_of in (
            (president, rows_p, set_president, CANDIDATES, candidate_option),
            (parliament, rows_q, set_national, LISTS, list_option[""]),
        ):
            for row in sorted(
                rows, key=lambda r: (num(r["ulke_ID"]), num(r["dis_TEMSILCILIK_ID"]), num(r["sandik_ID"]))
            ):
                if kind == "abroad":
                    code = f"{DAY}:abroad:{row['ulke_ID']}:{row['dis_TEMSILCILIK_ID']}"
                    unit = unit_of(code, "abroad_mission", None, DAY)
                else:
                    code = f"{DAY}:customs:{row['sandik_ID']}"
                    opened = datetime.fromisoformat(str(row["gumruk_SANDIK_TARIHI"]).replace("+0000", "+00:00")).date()
                    unit = unit_of(code, "customs_gate", province[num(row["il_ID"])], opened)
                if data is president:
                    count[kind] += 1
                put(result_set, unit, row, code, wanted, option_of, None)

    p.frame("reporting_unit", pl.DataFrame(list(units.values()), schema=_UNIT_SCHEMA))
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
        "In 6,961 unit-and-contest pairs (ballot boxes and missions, counting each election separately) the service "
        "gives more voters than registered voters, by a few votes; both counts are stored as the service prints them.",
    )
    for name, data in (("president", president), ("parliament", parliament)):
        everything = [*data.boxes, *data.missions, *data.gates]
        stored = {
            m: sum(num(r[c]) for r in everything)
            for m, c in (
                ("registered", "secmen_SAYISI"),
                ("voters", "oy_KULLANAN_SECMEN_SAYISI"),
                ("valid", "gecerli_OY_TOPLAMI"),
                ("invalid", "gecersiz_OY_TOPLAMI"),
            )
        }
        print(
            f"{name}: units {count}; stored {stored}; printed national valid {data.summary['gecerli_OY_TOPLAMI']} "
            f"voters {data.summary['oy_KULLANAN_SECMEN_SAYISI']} invalid {data.summary['gecersiz_OY_TOPLAMI']} "
            f"registered (all) {data.summary['secmen_SAYISI']} registered (boxes opened) "
            f"{data.summary['acilan_SECMEN_SAYISI']}; {data.prison_rows} prison summary rows set aside",
            flush=True,
        )
    for (il, ilce), summary in sorted(president_districts(president).items()):
        mine = [b for b in president.boxes if num(b["il_ID"]) == il and num(b["ilce_ID"]) == ilce]
        if valid_of(mine) != num(summary["gecerli_OY_TOPLAMI"]):
            print(f"president district {il}:{ilce}: boxes do not add up to the printed valid votes", flush=True)
    return p


def president_districts(data: Read) -> dict[tuple[int, int], Row]:
    return data.districts


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
