"""Turkey's May 2023 presidential and general elections by ballot box, from the Supreme Election Council's (YSK)
Sandık Sonuçları site (sonuc.ysk.gov.tr).

For each of the 81 provinces the results service lists every ballot box under its district with the registered
voters, voters, valid and invalid votes, and the votes for each presidential candidate; the same boxes carry the
votes for each party list, each alliance, and the independents together in the general election of May 14. The
runoff of May 28 has its own listing. A further file gives the votes cast at the customs gates, by ballot box, and
the votes abroad, by diplomatic mission, for each election. The contests and their options are the ones the archive
already holds from YSK's provincial totals; these packages add a count at the finest unit and point at them. The
first round and the general election were held with the same boxes, so one package holds both and the two share
their reporting units; the runoff is a package of its own."""

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

SOURCE = "tr.ysk_boxes_2023"
KEY_GENERAL = f"{SOURCE}/general-2023"
KEY_RUNOFF = f"{SOURCE}/runoff-2023"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
AREAS = Path(__file__).with_name("ysk_sonuc_areas.tsv")
DAY = date(2023, 5, 14)
RUNOFF_DAY = date(2023, 5, 28)
FOLDER = "browser://20261116-222924-4772b5/"
ROUND_1 = FOLDER + "tr-2023-05-14-cumhurbaskani-round1-ballot-box-results.ndjson.gz"
PARLIAMENT = FOLDER + "tr-2023-05-14-milletvekili-general-ballot-box-results.ndjson.gz"
ROUND_2 = FOLDER + "tr-2023-05-28-cumhurbaskani-runoff-ballot-box-results.ndjson.gz"
LABELS = "browser://20261117-003207-85b5ea/tr-2023-05-14-president-parliament-header-labels.json"
LABELS_RUNOFF = "browser://20261117-003207-85b5ea/tr-2023-05-28-president-runoff-header-labels.json"
ABROAD = "browser://20261119-203153-64251f/tr-ysk-abroad-and-customs-ballot-box-all-elections.ndjson.gz"
ABROAD_NAMES = {
    "round1": "2023-presidential-round1",
    "parliament": "2023-parliamentary",
    "round2": "2023-presidential-round2",
}
TITLE = "Sandık Sonuçları: cumhurbaşkanı ve milletvekili genel seçimi"
TITLE_RUNOFF = "Sandık Sonuçları: cumhurbaşkanı seçimi, ikinci tur"
CANDIDATES = {  # the service's column -> the candidate's ballot label in the archive
    "bagimsiz1_ALDIGI_OY": "Recep Tayyip Erdoğan",
    "bagimsiz2_ALDIGI_OY": "Muharrem İnce",
    "bagimsiz3_ALDIGI_OY": "Kemal Kılıçdaroğlu",
    "bagimsiz4_ALDIGI_OY": "Sinan Oğan",
}
CANDIDATES_RUNOFF = {
    "bagimsiz1_ALDIGI_OY": "Recep Tayyip Erdoğan",
    "bagimsiz2_ALDIGI_OY": "Kemal Kılıçdaroğlu",
}
LISTS = {  # the service's party and alliance columns -> the option's ballot label in the archive
    "parti1_ALDIGI_OY": "Millet Partisi",
    "parti2_ALDIGI_OY": "Hak ve Özgürlükler Partisi",
    "parti3_ALDIGI_OY": "Türkiye Komünist Partisi",
    "parti4_ALDIGI_OY": "Türkiye Komünist Hareketi",
    "parti5_ALDIGI_OY": "Sol Parti",
    "parti6_ALDIGI_OY": "Genç Parti",
    "parti7_ALDIGI_OY": "Memleket Partisi",
    "parti8_ALDIGI_OY": "Büyük Birlik Partisi",
    "parti9_ALDIGI_OY": "Adalet ve Kalkınma Partisi",
    "parti10_ALDIGI_OY": "Yeniden Refah Partisi",
    "parti11_ALDIGI_OY": "Milliyetçi Hareket Partisi",
    "parti12_ALDIGI_OY": "Yeşiller ve Sol Gelecek Partisi",
    "parti13_ALDIGI_OY": "Türkiye İşçi Partisi",
    "parti14_ALDIGI_OY": "Adalet Birlik Partisi",
    "parti15_ALDIGI_OY": "Anavatan Partisi",
    "parti16_ALDIGI_OY": "Yenilik Partisi",
    "parti17_ALDIGI_OY": "Halkın Kurtuluş Partisi",
    "parti18_ALDIGI_OY": "Milli Yol Partisi",
    "parti19_ALDIGI_OY": "Vatan Partisi",
    "parti20_ALDIGI_OY": "Güç Birliği Partisi",
    "parti21_ALDIGI_OY": "Cumhuriyet Halk Partisi",
    "parti22_ALDIGI_OY": "İYİ Parti",
    "parti23_ALDIGI_OY": "Adalet Partisi",
    "parti24_ALDIGI_OY": "Zafer Partisi",
    "ittifak1_ALDIGI_OY": "Sosyalist Güç Birliği İttifakı",
    "ittifak2_ALDIGI_OY": "Cumhur İttifakı",
    "ittifak3_ALDIGI_OY": "Emek ve Özgürlük İttifakı",
    "ittifak4_ALDIGI_OY": "Millet İttifakı",
    "ittifak5_ALDIGI_OY": "ATA İttifakı",
}
HEADERS = {  # the labels the service prints over those columns
    "parti1_ALDIGI_OY": "MİLLET",
    "parti2_ALDIGI_OY": "HAK-PAR",
    "parti3_ALDIGI_OY": "TKP",
    "parti4_ALDIGI_OY": "TKH",
    "parti5_ALDIGI_OY": "SOL PARTİ",
    "parti6_ALDIGI_OY": "GENÇPARTİ",
    "parti7_ALDIGI_OY": "MEMLEKET",
    "parti8_ALDIGI_OY": "BBP",
    "parti9_ALDIGI_OY": "AK PARTİ",
    "parti10_ALDIGI_OY": "YENİDEN REFAH",
    "parti11_ALDIGI_OY": "MHP",
    "parti12_ALDIGI_OY": "YEŞİL SOL PARTİ",
    "parti13_ALDIGI_OY": "TİP",
    "parti14_ALDIGI_OY": "AB",
    "parti15_ALDIGI_OY": "ANAP",
    "parti16_ALDIGI_OY": "YP",
    "parti17_ALDIGI_OY": "HKP",
    "parti18_ALDIGI_OY": "MİLLİ YOL",
    "parti19_ALDIGI_OY": "VATAN PARTİSİ",
    "parti20_ALDIGI_OY": "GBP",
    "parti21_ALDIGI_OY": "CHP",
    "parti22_ALDIGI_OY": "İYİ PARTİ",
    "parti23_ALDIGI_OY": "AP",
    "parti24_ALDIGI_OY": "ZAFER PARTİSİ",
    "ittifak1_ALDIGI_OY": "SOSYALİST GÜÇ BİRLİĞİ İTTİFAKI",
    "ittifak2_ALDIGI_OY": "CUMHUR İTTİFAKI",
    "ittifak3_ALDIGI_OY": "EMEK VE ÖZGÜRLÜK İTTİFAKI",
    "ittifak4_ALDIGI_OY": "MİLLET İTTİFAKI",
    "ittifak5_ALDIGI_OY": "ATA İTTİFAKI",
}
NOTE = (
    "Results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the presidential "
    "election and the general election of May 14, 2023. For each of the 81 provinces, the service lists every ballot "
    "box under its district, with the registered voters, voters, valid and invalid votes, the votes for each "
    "presidential candidate, and the votes for each party list, each alliance, and the independents together. "
    "Separate files give the same figures for each diplomatic mission abroad and for each ballot box at the customs "
    "gates."
)
NOTE_RUNOFF = (
    "Results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the presidential "
    "runoff of May 28, 2023. For each of the 81 provinces, the service lists every ballot box under its district, "
    "with the registered voters, voters, valid and invalid votes, and the votes for each of the two candidates. "
    "Separate files give the same figures for each diplomatic mission abroad and for each ballot box at the customs "
    "gates."
)
METHOD_PRESIDENT = (
    "Counted for each ballot box by YSK. A candidate's votes go to that candidate's option. Boxes in Turkey are "
    "placed in their neighborhood where the archive holds the neighborhood the service names, else in their "
    "district, else in their province. Votes abroad are given by diplomatic mission, not by box, and are stored as "
    "one unit for each mission, which has no area and states no registered voters. Customs-gate boxes are placed in "
    "the province of their gate and open from the date the service gives for each box until the election day. The "
    "three groups add up to the national totals the service prints."
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
    "party's votes go to its list's option and an alliance's votes to the alliance's option. Missions have no area "
    "and state no registered voters; customs-gate boxes are placed in the province of their gate and open from the "
    "date the service gives for each box until the election day. The votes in Turkey are in the electoral "
    "districts' sets, and together the two add up to the national totals the service prints."
)
Row = dict[str, object]
Value = tuple[str, str, str | None, int, int]
MEASURES = (
    (REGISTERED, "secmen_SAYISI"),
    (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
    (VALID, "gecerli_OY_TOPLAMI"),
    (INVALID, "gecersiz_OY_TOPLAMI"),
)
COLUMN = re.compile(r"(parti|ittifak|bagimsiz)\d+_ALDIGI_OY")
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


def num(value: object) -> int:
    return int(str(value))


def fetch() -> None:
    """Load the files saved from the service (data/inbox/tr.ysk_boxes_2023/) into the raw cache."""
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

    def everything(self) -> list[Row]:
        return [*self.boxes, *self.missions, *self.gates]


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


def circle_columns(labels: dict[str, Any], contest: str, wanted: dict[str, str]) -> dict[str, set[str]]:
    """Check that every province or district prints each column it carries under the label expected for it, and
    return the columns each one carries. In the general election a list that stood in only some districts has no
    column in the others."""
    found_by: dict[str, set[str]] = {}
    for circle in labels["contests"][contest]["basliklar"]:
        found = {h["column_NAME"]: h["ad"] for h in circle["basliklar"] if COLUMN.fullmatch(h["column_NAME"])}
        for column, label in found.items():
            if column in wanted and label != wanted[column]:
                raise ValueError(f"{circle['ilAdi']}: {column} is {label!r}, not {wanted[column]!r}")
        stray = [c for c in found if c not in wanted and not (contest == "8" and c.startswith("bagimsiz"))]
        if stray:
            raise ValueError(f"{circle['ilAdi']}: columns {sorted(stray)}")
        found_by[circle["ilAdi"]] = {c for c in found if c in wanted}
    return found_by


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


def opened(row: Row) -> date:
    return datetime.fromisoformat(str(row["gumruk_SANDIK_TARIHI"]).replace("+0000", "+00:00")).date()


def gate_code(day: date, row: Row) -> str:
    """The customs boxes of the two elections of one day are the same boxes, but the service numbers them anew for
    each election, so a box is named by its gate, its election board, its opening day, and its letter and number."""
    return (
        f"{day}:customs:{row['gumruk_ID']}:{row['birim_ID']}:{opened(row)}:{row.get('sandik_RUMUZ') or ''}"
        f"{row['sandik_NO']}"
    )


class Reference:
    """The rows production already holds for the 2023 contests."""

    def __init__(self, day: date, parliament: bool) -> None:
        x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value", "pipeline"])
        self.found = {
            value: str(entity)
            for value, entity in x.filter(
                (pl.col("scheme") == "tr.contest")
                & (pl.col("pipeline") == "tr.ysk")
                & pl.col("value").str.starts_with(f"{day}:")
            )
            .select("value", "entity_id")
            .iter_rows()
        }
        self.president = self.found[f"{day}:president"]
        self.contests: dict[str, str] = {}
        self.national = ""
        if parliament:
            self.contests = {
                v.removeprefix(f"{day}:parliament:"): e
                for v, e in self.found.items()
                if v.startswith(f"{day}:parliament:")
            }
            if len(self.contests) != 87 or f"{day}:parliament" not in self.found:
                raise ValueError(f"{len(self.contests)} district contests")
            self.national = self.found[f"{day}:parliament"]
        ids = [self.president, *self.contests.values(), *([self.national] if parliament else [])]
        self.contest = {
            str(row["id"]): row
            for row in pl.read_parquet(REFERENCE / "contest.parquet")
            .filter(pl.col("id").is_in(ids))
            .iter_rows(named=True)
        }
        self.options: dict[str, list[Row]] = defaultdict(list)
        for row in (
            pl.read_parquet(REFERENCE / "contest_option.parquet")
            .filter(pl.col("contest_id").is_in(list(self.contest)))
            .iter_rows(named=True)
        ):
            self.options[str(row["contest_id"])].append(row)
        elections = pl.read_parquet(REFERENCE / "election.parquet").filter(
            pl.col("id").is_in([row["election_id"] for row in self.contest.values()])
        )
        if elections.height != 1:
            raise ValueError("the contests are not in one election")
        self.election_id = str(elections["id"][0])
        self.polity = str(elections["polity_id"][0])

    def option_for(self, contest_id: str, label: str) -> str:
        match = [o for o in self.options[contest_id] if o["ballot_label"] == label]
        if len(match) != 1:
            raise ValueError(f"contest {contest_id}: {len(match)} options for {label}")
        return str(match[0]["id"])

    def independents_of(self, contest_id: str) -> str:
        match = [o for o in self.options[contest_id] if o["kind"] == "independents"]
        if len(match) != 1:
            raise ValueError(f"contest {contest_id}: {len(match)} options for independents")
        return str(match[0]["id"])


class Builder:
    def __init__(self, key: str, title: str, day: date, ref: Reference, source_title: str, note: str) -> None:
        self.day = day
        self.ref = ref
        self.p = Package(key, SOURCE, title)
        self.where = places()
        self.province = provinces()
        self.source = self.p.id("source", source_title)
        self.p.add(
            "source",
            id=self.source,
            kind="official_body",
            publisher=PUBLISHER,
            title=source_title,
            url=SITE,
            license=LICENSE,
            notes=note,
        )
        self.units: dict[str, Row] = {}
        self.values: list[Value] = []
        self.external: set[str] = {ref.polity, "tr.sandik", ref.election_id}
        self.count = {"domestic": 0, "abroad": 0, "customs": 0, "mahalle": 0, "ilce": 0, "il": 0}
        self.over = 0

    def make_set(self, key: str, contest_id: str, completeness: float, method: str) -> str:
        result_set = self.p.id("result_set", f"{self.day}:{key}")
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.ref.election_id,
            contest_id=contest_id,
            stage="alternative",
            kind="counted",
            status="active",
            source_id=self.source,
            is_default=False,
            completeness=round(completeness, 6),
        )
        self.p.add(
            "note",
            id=self.p.id("note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=method,
            lang="en",
        )
        self.external.add(contest_id)
        return result_set

    def unit_of(self, code: str, kind: str, area: str | None, start: date) -> str:
        unit = self.p.id("unit", code)
        row: Row = {
            "id": unit,
            "polity_id": self.ref.polity,
            "scheme_code": "tr.sandik",
            "code": code,
            "area_relation": "unlocated" if area is None else "within",
            "area_id": area,
            "kind": kind,
            "valid_from": start,
            "valid_to": self.day + timedelta(days=1),
        }
        if code in self.units and self.units[code] != row:
            raise ValueError(f"unit {code} differs between the two elections")
        self.units[code] = row
        if area:
            self.external.add(area)
        return unit

    def put(
        self,
        result_set: str,
        unit: str,
        row: Row,
        code: str,
        option_of: dict[str, str],
        loose: str | None = None,
        carried: set[str] | None = None,
    ) -> None:
        for measure, column in MEASURES:
            number = num(row.get(column) or 0)
            if column == "secmen_SAYISI" and not number:
                continue  # abroad and at the customs gates, where the service states no registered voters
            self.values.append((result_set, unit, None, measure, number))
        if num(row.get("oy_KULLANAN_SECMEN_SAYISI") or 0) > num(row.get("secmen_SAYISI") or 0) > 0:
            self.over += 1
        total = 0
        for column, option in option_of.items():
            votes = num(row.get(column) or 0)
            total += votes
            if votes:
                if carried is not None and column not in carried:
                    raise ValueError(f"unit {code}: votes in {column}, which the district's ballot does not carry")
                self.values.append((result_set, unit, option, VOTES, votes))
        if loose is not None:
            spread = sum(num(v) for c, v in row.items() if re.fullmatch(r"bagimsiz\d+_ALDIGI_OY", c) and v)
            if spread != num(row["bagimsiz_TOPLAM_OY"]):
                raise ValueError(f"unit {code}: independents {spread} against total {row['bagimsiz_TOPLAM_OY']}")
            if num(row["bagimsiz_TOPLAM_OY"]):
                self.values.append((result_set, unit, loose, VOTES, num(row["bagimsiz_TOPLAM_OY"])))
            total += num(row["bagimsiz_TOPLAM_OY"])
        if total != num(row["gecerli_OY_TOPLAMI"]):
            raise ValueError(f"unit {code}: options add up to {total}, valid votes {row['gecerli_OY_TOPLAMI']}")
        stray = [
            c
            for c, v in row.items()
            if COLUMN.fullmatch(c) and v and c not in option_of and not (loose is not None and c.startswith("bagimsiz"))
        ]
        if stray:
            raise ValueError(f"unit {code}: columns {stray} are not on the ballot")

    def area_of(self, row: Row) -> tuple[str, str]:
        il, ilce = num(row["il_ID"]), num(row["ilce_ID"])
        if ("mahalle", str(row["muhtarlik_ID"])) in self.where:
            return self.where[("mahalle", str(row["muhtarlik_ID"]))], "mahalle"
        if ("ilce", f"{il}:{ilce}") in self.where:
            return self.where[("ilce", f"{il}:{ilce}")], "ilce"
        return self.province[il], "il"

    def box_code(self, row: Row) -> str:
        return f"{self.day}:{num(row['il_ID']):02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"

    def domestic(self, row: Row) -> str:
        area, level = self.area_of(row)
        self.count[level] += 1
        self.count["domestic"] += 1
        return self.unit_of(self.box_code(row), "prison" if row.get("cezaevi_ID") else "standard", area, self.day)

    def mission(self, row: Row, counted: bool = True) -> tuple[str, str]:
        code = f"{self.day}:abroad:{row['ulke_ID']}:{row['dis_TEMSILCILIK_ID']}"
        self.count["abroad"] += counted
        return code, self.unit_of(code, "abroad_mission", None, self.day)

    def gate(self, row: Row, counted: bool = True) -> tuple[str, str]:
        code = gate_code(self.day, row)
        self.count["customs"] += counted
        return code, self.unit_of(code, "customs_gate", self.province[num(row["il_ID"])], opened(row))

    def finish(self) -> Package:
        self.p.frame("reporting_unit", pl.DataFrame(list(self.units.values()), schema=_UNIT_SCHEMA))
        self.p.frame(
            "result_value",
            pl.DataFrame(
                self.values,
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
        self.p.external |= self.external
        return self.p


def check_totals(name: str, data: Read, wanted: dict[str, str]) -> None:
    """The printed national totals, for every candidate, list, and alliance and for the turnout."""
    for group, rows, printed_row in (
        ("domestic", data.boxes, data.national),
        ("abroad", data.missions, data.abroad_total),
        ("customs", data.gates, data.customs_total),
    ):
        for column, label in wanted.items():
            if sum(num(r.get(column) or 0) for r in rows) != num(printed_row.get(column) or 0):
                raise ValueError(f"{name} {label}: the {group} units do not add up to the printed total")
        for column in ("gecerli_OY_TOPLAMI", "gecersiz_OY_TOPLAMI", "oy_KULLANAN_SECMEN_SAYISI"):
            if sum(num(r.get(column) or 0) for r in rows) != num(printed_row[column]):
                raise ValueError(f"{name} {column}: the {group} units do not add up to the printed total")
    for column in ("gecerli_OY_TOPLAMI", "gecersiz_OY_TOPLAMI", "oy_KULLANAN_SECMEN_SAYISI"):
        if sum(num(r.get(column) or 0) for r in data.everything()) != num(data.summary[column]):
            raise ValueError(f"{name} {column}: the three groups do not add up to the printed national total")


def report(name: str, b: Builder, data: Read) -> None:
    stored = {
        m: sum(num(r.get(c) or 0) for r in data.everything())
        for m, c in (
            ("registered", "secmen_SAYISI"),
            ("voters", "oy_KULLANAN_SECMEN_SAYISI"),
            ("valid", "gecerli_OY_TOPLAMI"),
            ("invalid", "gecersiz_OY_TOPLAMI"),
        )
    }
    print(
        f"{name}: units {b.count}; stored {stored}; printed national valid {data.summary['gecerli_OY_TOPLAMI']} "
        f"voters {data.summary['oy_KULLANAN_SECMEN_SAYISI']} invalid {data.summary['gecersiz_OY_TOPLAMI']} "
        f"registered (all) {data.summary['secmen_SAYISI']} registered (boxes opened) "
        f"{data.summary['acilan_SECMEN_SAYISI']}; {data.prison_rows} prison summary rows set aside",
        flush=True,
    )


def valid_of(rows: list[Row]) -> int:
    return sum(num(r["gecerli_OY_TOPLAMI"]) for r in rows)


def check_districts(data: Read) -> None:
    """Each district's boxes against the district total the service prints."""
    for (il, ilce), summary in sorted(data.districts.items()):
        mine = [b for b in data.boxes if num(b["il_ID"]) == il and num(b["ilce_ID"]) == ilce]
        for column in ("gecerli_OY_TOPLAMI", "gecersiz_OY_TOPLAMI", "oy_KULLANAN_SECMEN_SAYISI", "secmen_SAYISI"):
            if sum(num(b[column]) for b in mine) != num(summary[column]):
                raise ValueError(f"district {il}:{ilce}: boxes do not add up to the printed {column}")


def build_general() -> Package:
    president = read(ROUND_1, ABROAD_NAMES["round1"])
    parliament = read(PARLIAMENT, ABROAD_NAMES["parliament"])
    labels = json.loads(raw.cached(SOURCE, LABELS).path.read_text())
    circle_columns(labels, "9", {c: upper(n) for c, n in CANDIDATES.items()})
    carried = circle_columns(labels, "8", HEADERS)
    check_totals("president", president, CANDIDATES)
    check_totals("parliament", parliament, LISTS)
    check_districts(president)
    check_districts(parliament)

    ref = Reference(DAY, parliament=True)
    b = Builder(
        KEY_GENERAL,
        "Turkey's 2023 presidential first round and general election by ballot box, from YSK's Sandık Sonuçları",
        DAY,
        ref,
        TITLE,
        NOTE,
    )
    p = b.p
    for url in (ROUND_1, PARLIAMENT, LABELS, ABROAD):
        p.artifact(raw.cached(SOURCE, url), b.source)

    candidate_option = {c: ref.option_for(ref.president, label) for c, label in CANDIDATES.items()}
    list_option = {
        key: {c: ref.option_for(contest_id, label) for c, label in LISTS.items()}
        for key, contest_id in [*ref.contests.items(), ("", ref.national)]
    }
    independents = {key: ref.independents_of(contest_id) for key, contest_id in ref.contests.items()}
    b.external |= {*candidate_option.values(), *(o for m in list_option.values() for o in m.values())}
    b.external |= set(independents.values())

    def contest_key(il: int, ilce: int) -> str:
        label = parliament.circles[(il, ilce)]
        match = re.fullmatch(r".*-(\d)", label)
        return f"{il:02d}-{match[1]}" if match else f"{il:02d}"

    key_of_name = {label: contest_key(il, ilce) for (il, ilce), label in parliament.circles.items()}
    if set(carried) != set(key_of_name):
        raise ValueError("the header labels and the district summaries name different electoral districts")
    carried_by_key = {key_of_name[name]: columns for name, columns in carried.items()}
    circle_valid: dict[str, int] = defaultdict(int)
    circle_printed: dict[str, int] = defaultdict(int)
    for box in parliament.boxes:
        circle_valid[contest_key(num(box["il_ID"]), num(box["ilce_ID"]))] += num(box["gecerli_OY_TOPLAMI"])
    for (il, ilce), summary in parliament.districts.items():
        circle_printed[contest_key(il, ilce)] += num(summary["gecerli_OY_TOPLAMI"])
    if set(circle_valid) != set(ref.contests) or set(circle_printed) != set(ref.contests):
        raise ValueError("the service's electoral districts are not the archive's")
    for key in ref.contests:
        if circle_valid[key] != circle_printed[key]:
            raise ValueError(f"district {key}: boxes add up to {circle_valid[key]}, printed {circle_printed[key]}")

    set_president = b.make_set(
        "president",
        ref.president,
        (valid_of(president.everything())) / num(president.summary["gecerli_OY_TOPLAMI"]),
        METHOD_PRESIDENT,
    )
    set_district = {
        key: b.make_set(
            f"parliament:{key}", ref.contests[key], circle_valid[key] / circle_printed[key], METHOD_DISTRICT
        )
        for key in sorted(ref.contests)
    }
    set_national = b.make_set(
        "parliament",
        ref.national,
        (valid_of(parliament.missions) + valid_of(parliament.gates)) / num(parliament.summary["gecerli_OY_TOPLAMI"]),
        METHOD_NATIONAL,
    )

    twin_of = {num(x["sandik_ID"]): x for x in parliament.boxes}
    if set(twin_of) != {num(x["sandik_ID"]) for x in president.boxes}:
        raise ValueError("the two elections do not share their boxes")
    for row in sorted(president.boxes, key=lambda r: num(r["sandik_ID"])):
        twin = twin_of[num(row["sandik_ID"])]
        same = ("il_ID", "ilce_ID", "sandik_NO", "muhtarlik_ID", "cezaevi_ID")
        if any(row.get(k) != twin.get(k) for k in same):
            raise ValueError(f"box {row['sandik_ID']} is not the same box in the two elections")
        unit = b.domestic(row)
        code = b.box_code(row)
        b.put(set_president, unit, row, code, candidate_option)
        key = contest_key(num(row["il_ID"]), num(row["ilce_ID"]))
        b.put(set_district[key], unit, twin, code, list_option[key], independents[key], carried_by_key[key])
    for kind in ("mission", "gate"):
        both = (president.missions, parliament.missions) if kind == "mission" else (president.gates, parliament.gates)
        for rows, result_set, option_of, is_president in (
            (both[0], set_president, candidate_option, True),
            (both[1], set_national, list_option[""], False),
        ):
            for row in sorted(
                rows,
                key=lambda r: (num(r.get("ulke_ID") or 0), num(r.get("dis_TEMSILCILIK_ID") or 0), num(r["sandik_ID"])),
            ):
                code, unit = (b.mission if kind == "mission" else b.gate)(row, is_president)
                b.put(result_set, unit, row, code, option_of)
    if {gate_code(DAY, g) for g in president.gates} != {gate_code(DAY, g) for g in parliament.gates}:
        raise ValueError("the two elections do not share their customs boxes")
    if {(r["ulke_ID"], r["dis_TEMSILCILIK_ID"]) for r in president.missions} != {
        (r["ulke_ID"], r["dis_TEMSILCILIK_ID"]) for r in parliament.missions
    }:
        raise ValueError("the two elections do not share their missions")

    p = b.finish()
    p.accept(
        "accounting.turnout",
        f"In {b.over} unit-and-contest pairs (ballot boxes, counting the presidential and the general election "
        "separately) the service gives more voters than registered voters, usually by a few votes (a median of 3, at "
        "most 189); both counts are stored as the service prints them.",
    )
    report("president", b, president)
    report("parliament", b, parliament)
    return p


def build_runoff() -> Package:
    president = read(ROUND_2, ABROAD_NAMES["round2"])
    labels = json.loads(raw.cached(SOURCE, LABELS_RUNOFF).path.read_text())
    circle_columns(labels, "9", {c: upper(n) for c, n in CANDIDATES_RUNOFF.items()})
    check_totals("runoff", president, CANDIDATES_RUNOFF)
    check_districts(president)

    ref = Reference(RUNOFF_DAY, parliament=False)
    b = Builder(
        KEY_RUNOFF,
        "Turkey's 2023 presidential runoff by ballot box, from YSK's Sandık Sonuçları",
        RUNOFF_DAY,
        ref,
        TITLE_RUNOFF,
        NOTE_RUNOFF,
    )
    for url in (ROUND_2, LABELS_RUNOFF, ABROAD):
        b.p.artifact(raw.cached(SOURCE, url), b.source)
    candidate_option = {c: ref.option_for(ref.president, label) for c, label in CANDIDATES_RUNOFF.items()}
    b.external |= set(candidate_option.values())
    set_president = b.make_set(
        "president",
        ref.president,
        valid_of(president.everything()) / num(president.summary["gecerli_OY_TOPLAMI"]),
        METHOD_PRESIDENT,
    )
    for row in sorted(president.boxes, key=lambda r: num(r["sandik_ID"])):
        unit = b.domestic(row)
        b.put(set_president, unit, row, b.box_code(row), candidate_option)
    for rows, make in ((president.missions, b.mission), (president.gates, b.gate)):
        for row in sorted(
            rows, key=lambda r: (num(r.get("ulke_ID") or 0), num(r.get("dis_TEMSILCILIK_ID") or 0), num(r["sandik_ID"]))
        ):
            code, unit = make(row)
            b.put(set_president, unit, row, code, candidate_option)
    p = b.finish()
    p.accept(
        "accounting.turnout",
        f"In {b.over} ballot boxes the service gives more voters than registered voters, usually by a few votes (a "
        "median of 3, at most 2,215); both counts are stored as the service prints them.",
    )
    report("runoff", b, president)
    return p


def packages() -> list[str]:
    return [KEY_GENERAL, KEY_RUNOFF]


def parse() -> None:
    build_general().write()
    build_runoff().write()


def emit() -> None:
    package.run("load", packages())
