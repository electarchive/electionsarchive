"""Turkey's local elections of March 31, 2024 and the renewal elections of June 2, 2024, by ballot box, from the
Supreme Election Council's (YSK) Sandık Sonuçları site (sonuc.ysk.gov.tr).

For every ballot box the service gives the registered voters, voters, valid and invalid votes, and the votes for each
party's candidate and each independent candidate in the mayoral races, whose names it gives in a separate header
for each municipality. The contests are the ones the archive already holds from the council's totals; this package
adds a count at the finest unit and points at them. The renewal elections of June 2, 2024 (seven municipalities,
mayoral races and the councils of four of them) are counted the same way. The council and provincial council votes of
March 31 are in tr.ysk_meclis and tr.ysk_il_meclisi."""

import gzip
import json
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.tr.ysk import INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES, title
from ea.sources.tr.ysk_meclis import PARTIES as COUNCIL_PARTIES
from ea.sources.tr.ysk_meclis import SITE, place_keys
from ea.store import DATA

PARTIES = COUNCIL_PARTIES | {"GBP": "guc-birligi-partisi"}  # a mayoral candidate in Mudurnu only
SOURCE = "tr.ysk_boxes_2024"
LOCAL_KEY = f"{SOURCE}/local-2024"
RERUN_KEY = f"{SOURCE}/renewal-2024"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
DAY = date(2024, 3, 31)
RERUN = date(2024, 6, 2)
FOLDER = "browser://20261116-222924-4772b5/"
MAYORS = FOLDER + "tr-2024-03-31-mahalli-belediye-baskanligi-ballot-box-results.ndjson.gz"
METRO = FOLDER + "tr-2024-03-31-mahalli-buyuksehir-belediye-baskanligi-ballot-box-results.ndjson.gz"
REBOXES = FOLDER + "tr-2024-06-02-yenileme-belediye-baskanligi-ballot-box-results.ndjson.gz"
RECOUNCIL = FOLDER + "tr-2024-06-02-yenileme-belediye-meclisi-ballot-box-results.ndjson.gz"
LABELS = "browser://20261117-003207-85b5ea/tr-2024-03-31-local-header-labels.json"
RELABELS = "browser://20261117-003207-85b5ea/tr-2024-06-02-renewal-header-labels.json"
COUNCIL = "3"  # the service's number for the municipal council among the parts of a local election
RENEWED = {  # the service's (province, district, town) for each municipality that voted again, and the archive's key
    (38, 469, 0): "kayseri-pinarbasi",
    (39, 398, 4226): "kirklareli-luleburgaz-buyukkaristiran",
    (58, 624, 3800): "sivas-yildizeli-guneykaya",
    (62, 411, 3949): "tunceli-mazgirt-akpazar",
    (63, 290, 0): "sanliurfa-hilvan",
    (68, 20, 4061): "aksaray-merkez-saglik",
    (68, 754, 0): "aksaray-guzelyurt",
}
MAYOR, METROPOLITAN = "2", "6"  # the service's numbers for the mayoral races among the parts of a local election
TITLE = "Sandık Sonuçları: belediye başkanlığı"
NOTE = (
    "Mayoral results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the local "
    "elections of March 31, 2024 and the renewal elections of June 2, 2024 (mayoral races and municipal councils). "
    "For each municipality and each metropolitan municipality, the service gives each ballot box's registered "
    "voters, voters, valid and invalid votes, and the votes for each party's candidate or list and for each "
    "independent candidate. It gives the independents' names in a header served for each municipality. It gives no "
    "outcome."
)
METHOD = (
    "Counted for each ballot box by YSK. Boxes are the same boxes that held the council vote, so a box that already "
    "has a unit in the archive keeps it. A party's votes go to its candidate's option, and each independent "
    "candidate's votes go to an option of their own, named by the municipality's header. Where the archive already "
    "holds an option for that independent, the votes go to it."
)
RERUN_METHOD = (
    "Counted for each ballot box by YSK at the renewal election of June 2, 2024. Each box is its own unit, dated "
    "June 2, 2024, inside the neighborhood the service places it in. A party's votes go to its candidate's or "
    "list's option, and each independent candidate's votes go to an option of their own, named by the "
    "municipality's header. Where the archive already holds an option for that candidate or list, the votes go to "
    "it."
)
Row = dict[str, object]


class Place(NamedTuple):
    il: int
    ilce: int
    belde: int


def fetch() -> None:
    """Load the files saved from the service (data/inbox/tr.ysk_boxes_2024/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    have = raw.keys(SOURCE)
    for name, entry in index.items():
        key = entry.get("source", name)
        if key not in have:
            raw.store(
                SOURCE,
                key,
                (INBOX / entry["file"]).read_bytes(),
                suffix=Path(entry["file"]).suffix,
                retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
                media_type=entry["media_type"],
            )


def num(value: object) -> int:
    return int(str(value))


def fold(text: str) -> str:
    """A name without case, accents, or spaces, so the header's spelling matches the archive's."""
    plain = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "I")).casefold()
    return "".join(c for c in plain if c.isalnum() and not unicodedata.combining(c))


def boxes_of(path: str) -> tuple[list[Row], dict[str, int]]:
    """A file's box rows and the national summary it prints."""
    boxes: list[Row] = []
    summary: dict[str, int] = {}
    with gzip.open(raw.cached(SOURCE, path).path, "rt") as handle:
        for line in handle:
            record = json.loads(line)
            if record["type"] == "national_summary":
                summary = record["data"][0]
            elif record["type"] == "il":
                for district in record["ilceler"]:
                    boxes.extend(district["sandik"])
    return boxes, summary


def header_of(columns: list[dict[str, object]]) -> dict[str, str]:
    return {str(c["column_NAME"]): str(c["ad"]) for c in columns}


class Reference:
    """The rows production holds that the packages attach to."""

    def __init__(self) -> None:
        x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value", "pipeline"])
        self.external_id = x
        parties = x.filter(pl.col("scheme") == "tr.party")
        self.party_id = dict(zip(parties["value"], parties["entity_id"], strict=True))
        self.party_name = dict(pl.read_parquet(REFERENCE / "party.parquet").select("id", "name").iter_rows())
        units = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(
            (pl.col("scheme_code") == "tr.sandik") & pl.col("code").str.starts_with(f"{DAY.year}:")
        )
        self.units = dict(zip(units["code"], units["id"], strict=True))
        self.polity = units["polity_id"][0]
        muhtarlik = x.filter(pl.col("scheme") == "tr.ysk.muhtarlik.2024")
        self.area_of = dict(zip(muhtarlik["value"], muhtarlik["entity_id"], strict=True))
        self._options = pl.read_parquet(REFERENCE / "contest_option.parquet")
        self._contests = pl.read_parquet(REFERENCE / "contest.parquet")

    def contests(self, day: date, suffix: str = "-mayor") -> dict[str, str]:
        x = self.external_id.filter(
            (pl.col("scheme") == "tr.contest")
            & pl.col("value").str.starts_with(f"{day}:")
            & pl.col("value").str.ends_with(suffix)
        )
        return {v.removeprefix(f"{day}:").removesuffix(suffix): e for v, e in zip(x["value"], x["entity_id"])}

    def contest(self, ids: list[str]) -> dict[str, Row]:
        return {r["id"]: r for r in self._contests.filter(pl.col("id").is_in(ids)).iter_rows(named=True)}

    def options(self, ids: list[str]) -> dict[str, list[Row]]:
        out: dict[str, list[Row]] = defaultdict(list)
        for row in self._options.filter(pl.col("contest_id").is_in(ids)).iter_rows(named=True):
            out[row["contest_id"]].append(row)
        return out


class Builder:
    """Shared state of one package: the options and units it adds, and the values it collects."""

    def __init__(self, p: Package, ref: Reference, day: date, source: str) -> None:
        self.p, self.ref, self.day, self.source = p, ref, day, source
        self.external: set[str] = {ref.polity, "tr.sandik"}
        self.values: list[tuple[str, str, str | None, int, int]] = []
        self.new_units: dict[str, str] = {}
        self.unit_rows: list[Row] = []
        self.fuzzy: list[str] = []
        self.created: list[str] = []

    def option(
        self, contest_id: str, key: str, existing: list[Row], column: str, label: str, rerun: bool = False
    ) -> str:
        p, ref = self.p, self.ref
        if column.startswith("parti"):
            party_slug = PARTIES[label]
            party = ref.party_id[party_slug]
            found = [o for o in existing if o["party_id"] == party]
            if len(found) > 1:
                raise ValueError(f"{key}: {len(found)} options for {party_slug}")
            if found:
                self.external.add(str(found[0]["id"]))
                return str(found[0]["id"])
            option = p.id("option", f"{self.day}:{key}", party_slug)
            self.external.add(party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=party,
                ballot_label=ref.party_name[party],
            )
            return option
        if not column.startswith("bagimsiz"):
            raise ValueError(f"{key}: unexpected column {column}")
        target = fold(label)
        named = [o for o in existing if o["party_id"] is None and o["ballot_label"]]
        found = [o for o in named if fold(str(o["ballot_label"])) == target]
        if not found:
            close = [
                (SequenceMatcher(None, fold(str(o["ballot_label"])), target).ratio(), o)
                for o in named
                if SequenceMatcher(None, fold(str(o["ballot_label"])), target).ratio() >= 0.88
            ]
            close.sort(key=lambda t: -t[0])
            if close and (len(close) == 1 or close[0][0] - close[1][0] > 0.05):
                found = [close[0][1]]
                self.fuzzy.append(f"{key}: {label} -> {close[0][1]['ballot_label']}")
        if len(found) > 1:
            raise ValueError(f"{key}: {len(found)} options for {label}")
        if found:
            self.external.add(str(found[0]["id"]))
            return str(found[0]["id"])
        option = p.id("option", f"{self.day}:{key}", column)
        self.created.append(f"{key}: {label}")
        p.add("contest_option", id=option, contest_id=contest_id, kind="candidate", ballot_label=title(label))
        return option

    def unit(self, row: Row, fallback_area: object, code: str) -> str:
        if code in self.ref.units:
            unit = self.ref.units[code]
            self.external.add(unit)
            return unit
        if code in self.new_units:
            return self.new_units[code]
        unit = self.p.id("unit", code)
        self.new_units[code] = unit
        area = None if row.get("cezaevi_ID") else self.ref.area_of.get(str(row["muhtarlik_ID"]))
        area = area or str(fallback_area)
        self.external.add(area)
        self.unit_rows.append(
            {
                "id": unit,
                "polity_id": self.ref.polity,
                "scheme_code": "tr.sandik",
                "code": code,
                "area_relation": "within",
                "area_id": area,
                "kind": "prison" if row.get("cezaevi_ID") else "standard",
                "valid_from": self.day,
                "valid_to": self.day + timedelta(days=1),
            }
        )
        return unit

    def box_values(self, result_set: str, unit: str, row: Row, code: str, option_of: dict[str, str], where: str) -> int:
        for measure, column in (
            (REGISTERED, "secmen_SAYISI"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
            (VALID, "gecerli_OY_TOPLAMI"),
            (INVALID, "gecersiz_OY_TOPLAMI"),
        ):
            if row.get(column) is not None:
                self.values.append((result_set, unit, None, measure, num(row[column])))
        votes = 0
        for column, value in row.items():
            if not value or not column.endswith("_ALDIGI_OY") or column.startswith("ittifak"):
                continue
            if column not in option_of:
                raise ValueError(f"box {code}: column {column} has no label in {where}")
            self.values.append((result_set, unit, option_of[column], VOTES, num(value)))
            votes += num(value)
        if votes != num(row["gecerli_OY_TOPLAMI"]):
            raise ValueError(f"box {code}: votes {votes} against valid {row['gecerli_OY_TOPLAMI']}")
        return votes

    def result_set(self, key: str, election_id: object, contest_id: str, source: str, method: str) -> str:
        p = self.p
        result_set = p.id("result_set", f"{self.day}:{key}")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
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
            body_md=method,
            lang="en",
        )
        self.external |= {contest_id, str(election_id)}
        return result_set

    def finish(self) -> None:
        p = self.p
        p.frame("reporting_unit", pl.DataFrame(self.unit_rows, schema=_UNIT_SCHEMA))
        p.frame(
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
        p.external |= self.external
        p.accept(
            "accounting.turnout",
            "In 2,612 mayoral boxes the service gives more voters than registered voters (by up to 351 in one box, "
            "usually by far fewer); the boxes are stored as the service publishes them.",
        )


def source_row(p: Package, files: list[str]) -> str:
    source = p.id("source", TITLE, p.key)
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
    for url in files:
        p.artifact(raw.cached(SOURCE, url), source)
    return source


def compare(name: str, rows: list[Row], summary: dict[str, int]) -> None:
    for label, column in (
        ("registered", "secmen_SAYISI"),
        ("voters", "oy_KULLANAN_SECMEN_SAYISI"),
        ("valid", "gecerli_OY_TOPLAMI"),
        ("invalid", "gecersiz_OY_TOPLAMI"),
    ):
        printed = summary["acilan_SECMEN_SAYISI" if column == "secmen_SAYISI" else column]  # voters of opened boxes
        total = sum(num(r[column]) for r in rows)
        print(f"  {name} {label}: boxes {total} of {printed} printed ({total - printed:+d})", flush=True)


def build_local() -> Package:
    ref = Reference()
    labels = json.loads(raw.cached(SOURCE, LABELS).path.read_text())["contests"]
    mayor_boxes, mayor_summary = boxes_of(MAYORS)
    metro_boxes, metro_summary = boxes_of(METRO)

    # Municipalities: the places the service lists, keyed the way the archive keys its mayoral contests.
    listing = labels[MAYOR]
    places = pl.DataFrame(
        [
            {
                "year": DAY.year,
                "il": num(r["il_ID"]),
                "ilce": num(r["ilce_ID"]),
                "belde": num(r["belde_ID"]),
                "name": r["ilce_ADI"],
                "province": r["il_ADI"],
            }
            for rows in listing["ilceler"].values()
            for r in rows
        ]
    )
    keys = {place[1:]: key for place, key in place_keys(places).items()}
    place_header = {
        Place(num(h["ilId"]), num(h["ilceId"]), num(h["beldeId"])): header_of(h["basliklar"])
        for h in listing["ilceBasliklar"]
    }
    belde_place: dict[tuple[int, int], list[Place]] = defaultdict(list)
    for il, ilce, belde in keys:
        if belde:
            belde_place[(il, belde)].append(Place(il, ilce, belde))

    contests = ref.contests(DAY)
    contest = ref.contest(list(contests.values()))
    existing = ref.options(list(contests.values()))

    p = Package(
        LOCAL_KEY,
        SOURCE,
        "Turkey's 2024 mayoral votes by ballot box (municipalities and metropolitan municipalities), from YSK's "
        "Sandık Sonuçları",
    )
    source = source_row(p, [MAYORS, METRO, LABELS])
    b = Builder(p, ref, DAY, source)
    option_of: dict[str, dict[str, str]] = {}
    result_sets: dict[str, str] = {}

    def prepare(key: str, header: dict[str, str]) -> str:
        c = contest[contests[key]]
        result_set = b.result_set(key, c["election_id"], str(c["id"]), source, METHOD)
        result_sets[key] = result_set
        option_of[key] = {
            column: b.option(str(c["id"]), key, existing[str(c["id"])], column, label)
            for column, label in sorted(header.items())
            if not column.startswith("bagimsiz_TOPLAM")
        }
        return result_set

    # Metropolitan mayors: one race over each of the 30 provinces.
    metro_key: dict[int, str] = {}
    for h in labels[METROPOLITAN]["basliklar"]:
        key = slug(title(str(h["ilAdi"])))
        if key not in contests:
            raise ValueError(f"no contest for metropolitan mayor {key}")
        metro_key[num(h["ilId"])] = key
        prepare(key, header_of(h["basliklar"]))

    # Municipal mayors.
    mayor_key: dict[Place, str] = {}
    left_out: list[Place] = []
    for place, key in sorted(keys.items()):
        pl_ = Place(*place)
        if key not in contests:
            left_out.append(pl_)
            continue
        if pl_ not in place_header:
            left_out.append(pl_)
            continue
        mayor_key[pl_] = key
        prepare(key, place_header[pl_])

    def place_of(row: Row) -> Place:
        place = Place(num(row["il_ID"]), num(row["ilce_ID"]), num(row["belde_ID"]))
        if place in mayor_key:
            return place
        found = [q for q in belde_place.get((place.il, place.belde), []) if q in mayor_key]
        if len(found) != 1:
            raise ValueError(f"box {row['sandik_ID']} belongs to no place: {place}")
        return found[0]

    seen: set[tuple[str, str]] = set()
    for row in sorted(mayor_boxes, key=lambda r: num(r["sandik_ID"])):
        place = place_of(row)
        key = mayor_key[place]
        code = f"{DAY.year}:{num(row['il_ID']):02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"
        if (code, key) in seen:
            raise ValueError(f"box {code} twice in {key}")
        seen.add((code, key))
        unit = b.unit(row, contest[contests[key]]["district_area_id"], code)
        b.box_values(result_sets[key], unit, row, code, option_of[key], key)

    metro_seen: set[str] = set()
    for row in sorted(metro_boxes, key=lambda r: num(r["sandik_ID"])):
        key = metro_key[num(row["il_ID"])]
        code = f"{DAY.year}:{num(row['il_ID']):02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"
        if code in metro_seen:
            raise ValueError(f"box {code} twice in {key}")
        metro_seen.add(code)
        unit = b.unit(row, contest[contests[key]]["district_area_id"], code)
        b.box_values(result_sets[key], unit, row, code, option_of[key], key)

    b.finish()
    print(
        f"{len(mayor_key)} municipal and {len(metro_key)} metropolitan contests, {len(mayor_boxes)} + "
        f"{len(metro_boxes)} boxes, {len(b.new_units)} new units, {len(b.values)} values; "
        f"{len(left_out)} places without a contest or header: {left_out[:10]}",
        flush=True,
    )
    compare("mayor", mayor_boxes, mayor_summary)
    compare("metro", metro_boxes, metro_summary)
    print(f"  {len(b.fuzzy)} independents matched by near-identical name:", *b.fuzzy[:15], sep="\n    ")
    print(f"  {len(b.created)} independents without an existing option; first: {b.created[:5]}", flush=True)
    return p


def build_rerun() -> Package:
    ref = Reference()
    labels = json.loads(raw.cached(SOURCE, RELABELS).path.read_text())["contests"]
    p = Package(
        RERUN_KEY,
        SOURCE,
        "Turkey's renewal elections of June 2, 2024 (mayors and councils of seven municipalities) by ballot box, "
        "from YSK's Sandık Sonuçları",
    )
    source = source_row(p, [REBOXES, RECOUNCIL, RELABELS])
    b = Builder(p, ref, RERUN, source)
    total = 0
    for part, path, suffix in ((MAYOR, REBOXES, "-mayor"), (COUNCIL, RECOUNCIL, "-council")):
        boxes, summary = boxes_of(path)
        contests = ref.contests(RERUN, suffix)
        contest = ref.contest(list(contests.values()))
        existing = ref.options(list(contests.values()))
        header = {
            Place(num(h["ilId"]), num(h["ilceId"]), num(h["beldeId"])): header_of(h["basliklar"])
            for h in labels[part]["ilceBasliklar"]
        }
        result_sets: dict[Place, str] = {}
        option_of: dict[Place, dict[str, str]] = {}
        for place, key in RENEWED.items():
            if key not in contests:
                continue  # only four of the seven municipalities renewed their councils
            pl_ = Place(*place)
            c = contest[contests[key]]
            result_sets[pl_] = b.result_set(f"{key}{suffix}", c["election_id"], str(c["id"]), source, RERUN_METHOD)
            option_of[pl_] = {
                column: b.option(str(c["id"]), f"{key}{suffix}", existing[str(c["id"])], column, label)
                for column, label in sorted(header[pl_].items())
                if not column.startswith("bagimsiz_TOPLAM")
            }
        seen: set[tuple[str, Place]] = set()
        for row in sorted(boxes, key=lambda r: num(r["sandik_ID"])):
            place = Place(num(row["il_ID"]), num(row["ilce_ID"]), num(row["belde_ID"]))
            if place not in result_sets:
                raise ValueError(f"box {row['sandik_ID']} belongs to no renewed place: {place}")
            code = f"{RERUN}:{num(row['il_ID']):02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"
            if (code, place) in seen:
                raise ValueError(f"box {code} twice in {RENEWED[place]}")
            seen.add((code, place))
            unit = b.unit(row, contest[contests[RENEWED[place]]]["district_area_id"], code)
            b.box_values(result_sets[place], unit, row, code, option_of[place], RENEWED[place])
        total += len(boxes)
        compare(f"renewal{suffix}", boxes, summary)
    b.finish()
    print(f"renewal: {total} box counts, {len(b.new_units)} new units, {len(b.values)} values", flush=True)
    print("  independents matched by name:", *b.fuzzy, sep="\n    ")
    print(f"  independents without an existing option: {b.created}", flush=True)
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
    return [LOCAL_KEY, RERUN_KEY]


def parse() -> None:
    fetch()
    build_local().write()
    build_rerun().write()


def emit() -> None:
    package.run("load", packages())
