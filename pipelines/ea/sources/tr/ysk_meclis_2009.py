"""Municipal councils of the 2009 local elections that `tr.ysk_meclis` left out, by ballot box.

`tr.ysk_meclis` read the 2009 council results from the results service's municipality lists, and those lists did not
return a few dozen small municipalities (the belde of 2009) at all. YSK's ballot-box file for the same election does
hold them. This pipeline compares that file with the ballot boxes the archive already holds (`tr.sandik` units of
2009), and writes the councils and boxes the archive lacks. Each new council attaches to the election and the area of
the municipality's mayoral contest, which the archive already holds, and reuses the archive's party records.

The comparison needs the reference partitions of the archive (`data/reference/tr/`, from `ea package snapshot`).
"""

import gzip
import json
from datetime import date, datetime, timedelta
from typing import NamedTuple

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES, title
from ea.sources.tr.ysk_meclis import DATED, PARTIES, place_keys
from ea.sources.tr.ysk_muhtarlik import ILCES_WITHOUT_OWN_MAHALLES
from ea.store import DATA

SOURCE = "tr.ysk_meclis_2009"
TURKEY = "01a13c0c-c7a6-7799-b107-b55e848cecf2"
DAY = date(2009, 3, 29)
SITE = "https://sonuc.ysk.gov.tr"
BOXES_URL = f"{SITE}/api/getSecimSandikSonucList?secimId=4290&secimTuru=3&yurtIciDisi=1"
HEADERS_URL = f"{SITE}/api/getSandikSecimSonucBaslikList?secimId=4290&secimTuru=3&yurtIciDisi=1"
REFERENCE = DATA / "reference" / "tr"
INBOX = DATA / "inbox" / "manual" / "tr-2009-council-boxes"
TITLE = "Sandık Sonuçları: belediye meclisi üyeliği, 2009"
NOTE = (
    "Municipal council results of the 2009 local elections for each ballot box, read from the ballot-box file of the "
    "results service of YSK's Sandık Sonuçları site. The file gives each ballot box's registered voters, voters, "
    "valid and invalid votes, and the votes for each party list and each independent candidate. It gives no seats won. "
    "This source holds the councils of municipalities that the archive's earlier read of the same service left out."
)
# Municipalities whose boxes the ballot-box file lists under one district while their mayoral contest sits under
# another, as the file's key, then the contest's.
KEYS = {
    "diyarbakir-cinar-karacadag": "diyarbakir-baglar-karacadag",
    "zonguldak-merkez-catalagzi": "zonguldak-kilimli-catalagzi",
    "zonguldak-merkez-gelik": "zonguldak-kilimli-gelik",
    "zonguldak-merkez-muslu": "zonguldak-kilimli-muslu",
    "zonguldak-merkez-sivriler": "zonguldak-kozlu-sivriler",
}
RENAMED = {
    "afyonkarahisar-sinanpasa-savran": "afyonkarahisar-sinanpasa-serban",
    "antalya-alanya-obakoy": "antalya-alanya-oba",
    "antalya-kumluca-cavuskoy": "antalya-kumluca-adrasan",
    "balikesir-bigadic-iskele": "balikesir-bigadic-iskelekoy",
    "burdur-aglasun-canakli": "burdur-aglasun-mamak",
    "bursa-mudanya-zeytinbagi": "bursa-mudanya-tirilye",
    "canakkale-merkez-intepe": "canakkale-merkez-erenkoy",
    "cankiri-orta-elmali": "cankiri-orta-elmalik",
    "corum-bogazkale-evren": "corum-bogazkale-yekbas",
    "denizli-akkoy": "denizli-pamukkale",
    "elazig-palu-beyhani": "elazig-palu-beyhan",
    "erzincan-tercan-altunkent": "erzincan-tercan-kargin",
    "gaziantep-araban-elifkoy": "gaziantep-araban-elif",
    "gaziantep-nurdagi-sakcagoze": "gaziantep-nurdagi-sakcagozu",
    "gumushane-merkez-arzular": "gumushane-merkez-arzularkabakoy",
    "kayseri-tomarza-emirusagi": "kayseri-tomarza-avsarobasi",
    "kirklareli-babaeski-mandira": "kirklareli-babaeski-buyukmandira",
    "kirklareli-vize-cakili": "kirklareli-vize-cakilli",
    "konya-beysehir-kurucaova": "konya-beysehir-kurucuova",
    "konya-beysehir-bayafsar": "konya-beysehir-bayavsar",
    "konya-eregli-sazgecit": "konya-eregli-hortu",
    "konya-ilgin-asagicigil": "konya-ilgin-cigil",
    "konya-selcuklu-tepekoy": "konya-selcuklu-tepekent",
    "malatya-hekimhan-ipekyolu": "malatya-hekimhan-girmana",
    "manisa-soma-yagcilli": "manisa-soma-yagcili",
    "kahramanmaras-caglayancerit-duzbag": "kahramanmaras-caglayancerit-helete",
    "mus-malazgirt-golkoru": "mus-malazgirt-gulkoru",
    "nigde-ulukisla-aktoprak": "nigde-ulukisla-kilan",
    "siirt-baykan-ziyaret": "siirt-baykan-veyselkarani",
    "siirt-aydinlar": "siirt-tillo",
    "trabzon-besikduzu-turkelli": "trabzon-besikduzu-oguz",
    "sanliurfa-suruc-onbirnisan": "sanliurfa-suruc-aligor",
    "sanliurfa-viransehir-eyupnebi": "sanliurfa-viransehir-eyyupnebi",
    "yozgat-sorgun-karakiz": "yozgat-sorgun-yeniyer",
}


def fetch() -> None:
    """Take the two files a person saved from the results service into the raw cache."""
    index = raw.keys(SOURCE)
    for url, name, retrieved in (
        (BOXES_URL, "tr-2009-03-29-mahalli-belediye-meclisi-ballot-box-results.ndjson.gz", "2026-11-16T23:37:39+00:00"),
        (HEADERS_URL, "tr-2009-03-29-local-header-labels.json", "2026-11-17T00:39:53+00:00"),
    ):
        if url in index:
            continue
        content = (INBOX / name).read_bytes()
        raw.store(
            SOURCE,
            url,
            content,
            suffix=".ndjson.gz" if name.endswith(".gz") else ".json",
            retrieved_at=datetime.fromisoformat(retrieved),
            media_type="application/gzip" if name.endswith(".gz") else "application/json",
        )


def parse() -> None:
    councils().write()


def emit() -> None:
    package.run("load", packages())


def packages() -> list[str]:
    return [f"{SOURCE}/councils"]


class Read(NamedTuple):
    places: pl.DataFrame
    boxes: pl.DataFrame
    seats: dict[tuple[int, int, int], int]
    named: dict[tuple[int, int, int], dict[str, str]]
    parties: dict[int, dict[str, str]]


def read() -> Read:
    """The ballot-box file's places and boxes, and the labels of each party and independent column."""
    places: list[dict[str, object]] = []
    boxes: list[dict[str, object]] = []
    with gzip.open(raw.cached(SOURCE, BOXES_URL).path, "rt") as file:
        for line in file:
            data = json.loads(line)
            if data["type"] != "il":
                continue
            for row in data["ilce_summary"]:
                places.append(
                    {
                        "year": 2009,
                        "il": row["il_ID"],
                        "ilce": row["ilce_ID"],
                        "belde": row["belde_ID"],
                        "province": row["il_ADI"],
                        "name": row["ilce_ADI"],
                    }
                )
            for district in data["ilceler"]:
                boxes += district["sandik"]
    headers = json.loads(raw.cached(SOURCE, HEADERS_URL).path.read_text())["contests"]["3"]
    labels: dict[tuple[int, int], dict[str, str]] = {}
    for entry in headers["basliklar"]:
        labels[(entry["ilId"], entry["secimCevresiId"])] = {b["column_NAME"]: b["ad"] for b in entry["basliklar"]}
    seats = {
        (row["il_ID"], row["ilce_ID"], row["belde_ID"]): row["secilecek_ADAY_SAYISI"]
        for rows in headers["ilceler"].values()
        for row in rows
    }
    named = {
        (e["ilId"], e["ilceId"], e["beldeId"]): {b["column_NAME"]: b["ad"] for b in e["basliklar"]}
        for e in headers["ilceBasliklar"]
    }
    # The header file files a few municipalities under another district than the ballot boxes do, but a municipality's
    # id is unique within its province.
    for table in (seats, named):
        for (il, ilce, belde), item in list(table.items()):
            if belde:
                table.setdefault((il, 0, belde), item)
    return Read(
        pl.DataFrame(places, infer_schema_length=None),
        pl.DataFrame(boxes, infer_schema_length=None),
        seats,
        named,
        {il: label for (il, cevre), label in labels.items() if cevre == 0},
    )


def councils() -> Package:
    places, boxes, seats, named, parties = read()
    if not (REFERENCE / "reporting_unit.parquet").exists():
        raise FileNotFoundError(f"{REFERENCE} needs the reference partition of the archive for TR")
    held = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(
        (pl.col("scheme_code") == "tr.sandik") & pl.col("code").str.starts_with(f"{DAY.year}:")
    )
    external = pl.read_parquet(REFERENCE / "external_id.parquet")
    contests = pl.read_parquet(REFERENCE / "contest.parquet")
    names = pl.read_parquet(REFERENCE / "party.parquet")
    keyed = {
        value: entity
        for scheme, value, entity in external.select("scheme", "value", "entity_id").iter_rows()
        if scheme == "tr.contest"
    }
    party_ids = {
        value: entity
        for scheme, value, entity in external.select("scheme", "value", "entity_id").iter_rows()
        if scheme == "tr.party"
    }
    party_names = dict(names.select("id", "name").iter_rows())
    areas = {
        int(value): entity
        for scheme, value, entity in external.select("scheme", "value", "entity_id").iter_rows()
        if scheme == f"tr.ysk.muhtarlik.{DAY.year}"
    }
    mayors = {
        row["id"]: (row["election_id"], row["district_area_id"], row["name"]) for row in contests.iter_rows(named=True)
    }

    own = {place for place in places.select("il", "ilce").unique().iter_rows()}
    present = {(il, ilce) for il, ilce, belde in places.select("il", "ilce", "belde").iter_rows() if belde == 0}
    for il, ilce in own - present:
        base = places.filter((pl.col("il") == il) & (pl.col("ilce") == ilce))["name"][0].split(" - ")[0]
        ILCES_WITHOUT_OWN_MAHALLES.setdefault((il, ilce), base)
    keys = {
        place: KEYS.get(key, RENAMED.get(key, key)) for place, key in place_keys(places).items()
    }  # (year, il, ilce, belde) -> contest key

    boxes = boxes.with_columns(
        code=pl.format(
            "{}:{}:{}:{}", pl.lit(str(DAY.year)), pl.col("il_ID").cast(pl.String).str.zfill(2), "ilce_ID", "sandik_NO"
        )
    )
    lacking = boxes.filter(~pl.col("code").is_in(held["code"].implode()))
    here = {
        (il, ilce, belde) for il, ilce, belde in lacking.select("il_ID", "ilce_ID", "belde_ID").unique().iter_rows()
    }
    whole = boxes.join(
        pl.DataFrame(sorted(here), schema=["il_ID", "ilce_ID", "belde_ID"], orient="row"),
        on=["il_ID", "ilce_ID", "belde_ID"],
    )
    if whole.height != lacking.height:
        raise ValueError("a municipality with missing boxes also has boxes the archive holds")
    print(f"{lacking.height} boxes of {len(here)} municipalities are missing")
    unmatched = []
    chosen: dict[tuple[int, int, int], tuple[str, str]] = {}
    for place in sorted(here):
        key = keys[(DAY.year, *place)]
        if f"{DAY}:{key}-council" in keyed:
            raise ValueError(f"{key}: the archive already holds its council")
        mayor = keyed.get(f"{DAY}:{key}-mayor")
        if mayor is None:
            unmatched.append((place, key))
        else:
            chosen[place] = (key, mayor)
    if unmatched:
        raise ValueError(f"no mayoral contest for {unmatched}")

    p = Package(
        f"{SOURCE}/councils",
        SOURCE,
        "The 2009 Turkish municipal councils that the archive's earlier read of the results service left out, by "
        "ballot box, from YSK's Sandık Sonuçları",
    )
    # Production holds Turkey under the id its first pipeline minted, which the 2009 boxes already use, not the
    # standard uuid5 of the ISO code.
    turkey = TURKEY
    p.external |= {turkey, "tr.sandik"}
    source = p.id("source", PUBLISHER, TITLE)
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
    p.artifact(raw.cached(SOURCE, BOXES_URL), source)
    p.artifact(raw.cached(SOURCE, HEADERS_URL), source)

    result_sets: dict[tuple[int, int, int], tuple[str, str, str]] = {}
    for place, (key, mayor) in sorted(chosen.items()):
        election, area, name = mayors[mayor]
        english = name.removeprefix("Mayor of ")
        contest_key = f"{DAY}:{key}-council"
        contest = p.id("contest", contest_key)
        p.external |= {election, area}
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            district_area_id=area,
            magnitude=seats.get(place) or seats[(place[0], 0, place[2])],
            round_number=1,
            default_result_kind="counted",
        )
        p.add("external_id", entity_id=contest, scheme="tr.contest", value=contest_key)
        p.name(contest, f"{english} municipal council", "en", "official")
        p.name(contest, f"{english} Belediye Meclisi Üyeliği", "tr", "native", False)
        result_set = p.id("result_set", contest_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        result_sets[place] = (contest_key, contest, result_set)

    options: dict[tuple[str, str], str] = {}
    unknown = set()
    for row in whole.sort("il_ID", "ilce_ID", "sandik_NO").iter_rows(named=True):
        place = (row["il_ID"], row["ilce_ID"], row["belde_ID"])
        contest_key, contest, result_set = result_sets[place]
        key, mayor = chosen[place]
        unit = p.id("unit", "tr.sandik", f"{DAY.year}:{row['code'][5:]}")
        area = mayors[mayor][1] if row["cezaevi_ID"] or row["muhtarlik_ID"] not in areas else areas[row["muhtarlik_ID"]]
        p.external.add(area)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=turkey,
            scheme_code="tr.sandik",
            code=row["code"],
            area_relation="within",
            area_id=area,
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
        for measure, column in (
            (REGISTERED, "secmen_SAYISI"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
            (VALID, "gecerli_OY_TOPLAMI"),
            (INVALID, "gecersiz_OY_TOPLAMI"),
        ):
            if row[column] is not None:
                value(p, result_set, unit, None, measure, row[column])
        listed = 0
        for column, count in sorted(row.items()):
            if not count or not column.endswith("_ALDIGI_OY"):
                continue
            if column.startswith("parti"):
                label = parties[place[0]].get(column) or (named.get(place) or named[(place[0], 0, place[2])]).get(
                    column
                )
                if label not in PARTIES:
                    unknown.add(label)
                    continue
                owner = DATED.get((label, DAY.year), PARTIES[label])
                option_key = f"{contest_key}:{owner}"
                kind, party, ballot = "list", party_ids[owner], party_names[party_ids[owner]]
            elif column.startswith("bagimsiz"):
                label = (named.get(place) or named[(place[0], 0, place[2])])[column]
                listed += int(count)
                option_key = f"{contest_key}:{column.removesuffix('_ALDIGI_OY')}"
                kind, party, ballot = "candidate", None, title(label)
            else:
                raise ValueError(f"{place}: unexpected column {column}")
            option = options.get((contest_key, option_key))
            if option is None:
                option = options[(contest_key, option_key)] = p.id("option", option_key)
                if party is not None:
                    p.external.add(party)
                p.add("contest_option", id=option, contest_id=contest, kind=kind, party_id=party, ballot_label=ballot)
                p.add("external_id", entity_id=option, scheme="tr.option", value=option_key)
            value(p, result_set, unit, option, VOTES, count)
        pooled = int(row.get("bagimsiz_TOPLAM_OY") or 0) - listed
        if pooled < 0:
            raise ValueError(f"box {row['code']}: named independents exceed the total")
        if pooled:
            option_key = f"{contest_key}:independents"
            option = options.get((contest_key, option_key))
            if option is None:
                option = options[(contest_key, option_key)] = p.id("option", option_key)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="independents",
                    ballot_label=INDEPENDENTS,
                )
                p.add("external_id", entity_id=option, scheme="tr.option", value=option_key)
            value(p, result_set, unit, option, VOTES, pooled)
    if unknown:
        raise ValueError(f"unknown party labels {sorted(unknown, key=str)}")
    return p


def value(p: Package, result_set: str, unit: str, option: str | None, measure: int, count: object) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=count,
    )
