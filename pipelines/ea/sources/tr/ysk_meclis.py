import gzip
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES, title
from ea.sources.tr.ysk_muhtarlik import API, DAYS, HEADERS, ILCES_WITHOUT_OWN_MAHALLES, SECIM_IDS, get
from ea.store import DATA, connect

SOURCE = "tr.ysk_meclis"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CACHE = DATA / "tmp" / "ysk_meclis"
SECIM_IDS = SECIM_IDS | {2024: 60882}
COUNCIL = 3
SITE = "https://sonuc.ysk.gov.tr"
TITLE = "Sandık Sonuçları: belediye meclisi üyeliği"
NOTE = (
    "Municipal council results for each ballot box, read from the results service of YSK's Sandık Sonuçları site "
    "for the local elections of 2009, 2014, 2019, and 2024. The service gives each municipality's number of seats, "
    "and for each ballot box its registered voters, voters, valid and invalid votes, and the votes for each party "
    "list and each independent candidate. In some councils it gives independents only as one total. The ballot "
    "boxes of each municipality add up to the municipality totals the same service publishes, except in 2009 for Alanya "
    "and Mahmutlar, whose boxes count 820 registered voters fewer and more than their totals. It gives no seats won."
)
KEYS = {
    (2009, "denizli-akkoy-pamukkale-golemezli"): "denizli-pamukkale-golemezli",
    (2009, "kirikkale-bahsili-bahsili-karaahmetli"): "kirikkale-bahsili-karaahmetli",
}
ANNULLED = {(2014, "agri"), (2014, "tokat-yesilyurt")}
PARTIES = {
    "AB PARTİ": "adalet-birlik-partisi",
    "ABP": "anadolu-birligi-partisi",
    "ADP": "aydinlik-demokrasi-partisi",
    "AP": "adalet-partisi",
    "HEPAR": "hak-ve-esitlik-partisi",
    "HKP": "halkin-kurtulus-partisi",
    "TURK PARTİ": "toplumsal-uzlasma-reform-ve-kalkinma-partisi",
    "YTP": "yeni-turkiye-partisi-2024",
    "YURT-P": "yurt-partisi",
    "AK PARTİ": "adalet-ve-kalkinma-partisi",
    "ANAP": "anavatan-partisi",
    "ANAVATAN": "anavatan-partisi",
    "BBP": "buyuk-birlik-partisi",
    "BDP": "baris-ve-demokrasi-partisi",
    "BTP": "bagimsiz-turkiye-partisi",
    "BÜYÜK BİRLİK": "buyuk-birlik-partisi",
    "CHP": "cumhuriyet-halk-partisi",
    "DEM Parti": "halklarin-esitlik-ve-demokrasi-partisi",
    "DEVA PARTİSİ": "demokrasi-ve-atilim-partisi",
    "DP": "demokrat-parti",
    "DSP": "demokratik-sol-parti",
    "DTP": "demokratik-toplum-partisi",
    "DYP": "dogru-yol-partisi",
    "EMEP": "emek-partisi",
    "GELECEK PARTİSİ": "gelecek-partisi",
    "HAK-PAR": "hak-ve-ozgurlukler-partisi",
    "HDP": "halklarin-demokratik-partisi",
    "HYP": "halkin-yukselisi-partisi",
    "HÜDA PAR": "hur-dava-partisi",
    "LDP": "liberal-demokrat-parti",
    "MEMLEKET": "memleket-partisi",
    "MHP": "milliyetci-hareket-partisi",
    "MİLLET": "millet-partisi",
    "MİLLİ YOL": "milli-yol-partisi",
    "OCAK": "ocak-partisi",
    "SAADET": "saadet-partisi",
    "SOL PARTİ": "sol-parti",
    "TKH": "turkiye-komunist-hareketi",
    "TKP": "turkiye-komunist-partisi",
    "TİP": "turkiye-isci-partisi",
    "VATAN PARTİSİ": "vatan-partisi",
    "YENİDEN REFAH": "yeniden-refah-partisi",
    "ZAFER PARTİSİ": "zafer-partisi",
    "ÖDP": "sol-parti",
    "İP": "isci-partisi",
    "İYİ PARTİ": "iyi-parti",
}
DATED = {("ANAVATAN", 2009): "anavatan-partisi-1983"}
Rows = list[dict[str, int | str]]
EMPTY = (
    "birimId",
    "muhtarlikId",
    "cezaeviId",
    "sandikNoIlk",
    "sandikNoSon",
    "ulkeId",
    "disTemsilcilikId",
    "gumrukId",
    "sandikRumuzIlk",
    "sandikRumuzSon",
    "sandikId",
)


def bundle_url(year: int) -> str:
    return f"{API}getSecimSandikSonucList?" + urlencode(
        {"secimId": SECIM_IDS[year], "secimTuru": COUNCIL, "yurtIciDisi": 1}
    )


def cached(client: httpx.Client, file: Path, path: str, query: dict[str, int | str]) -> Rows:
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(get(client, path, query), ensure_ascii=False))
    return json.loads(file.read_text())


def results(il: int, ilce: int | str) -> dict[str, int | str]:
    return {
        "ilId": il,
        "ilceId": ilce,
        "beldeId": "",
        "sandikTuru": -1,
        "yurtIciDisi": 1,
        "secimCevresiId": "",
        **dict.fromkeys(EMPTY, ""),
    }


def crawl(client: httpx.Client, year: int) -> list[tuple[str, Rows]]:
    folder = CACHE / str(year)
    base: dict[str, int | str] = {"secimId": SECIM_IDS[year], "secimTuru": COUNCIL}
    out = []

    def call(name: str, path: str, query: dict[str, int | str]) -> Rows:
        query = base | query
        rows = cached(client, folder / f"{name}.json", path, query)
        out.append((f"{API}{path}?{urlencode(query)}", rows))
        return rows

    ils = call("ils", "getIlList", {"sandikTuru": -1, "yurtIciDisi": 1})
    for il in ils:
        il_id = int(il["il_ID"])
        places = call(
            f"ilces-{il_id}",
            "getIlceList",
            {"ilId": il_id, "secimCevresiId": "", "sandikTuru": -1, "yurtIciDisi": 1},
        )
        call(
            f"header-{il_id}",
            "getSandikSecimSonucBaslikList",
            {"secimCevresiId": "", "ilId": il_id, "bagimsiz": 1, "yurtIciDisi": 1},
        )
        call(f"totals-{il_id}", "getSecimSandikSonucList", results(il_id, "") | {"sorguTuru": 1})
        independents: set[tuple[int, int]] = set()
        for ilce_id in sorted({int(place["ilce_ID"]) for place in places}):
            boxes = call(
                f"boxes-{il_id}-{ilce_id}", "getSecimSandikSonucList", results(il_id, ilce_id) | {"sorguTuru": 1}
            )
            independents |= {
                (int(box["ilce_ID"]), int(box["belde_ID"])) for box in boxes if box.get("bagimsiz_TOPLAM_OY")
            }
        for place in places:
            if (int(place["ilce_ID"]), int(place["belde_ID"])) in independents:
                cevre = int(place["secim_CEVRESI_ID"])
                call(
                    f"header-{il_id}-{cevre}",
                    "getSandikSecimSonucBaslikList",
                    {"secimCevresiId": cevre, "ilId": il_id, "bagimsiz": 1, "yurtIciDisi": 1},
                )
        print(year, il_id, il["il_ADI"], len(places), flush=True)
    return out


def fetch(years: list[int] | None = None) -> None:
    index = raw.keys(SOURCE)
    with httpx.Client(headers=HEADERS) as client:
        for year in years or list(DAYS):
            url = bundle_url(year)
            if url in index:
                continue
            lines = "".join(
                json.dumps({"url": request, "response": response}, ensure_ascii=False) + "\n"
                for request, response in crawl(client, year)
            )
            raw.store(
                SOURCE,
                url,
                gzip.compress(lines.encode(), mtime=0),
                suffix=".jsonl.gz",
                retrieved_at=datetime.now(UTC),
                media_type="application/gzip",
            )


def pairs(year: int) -> list[tuple[str, dict[str, str], Rows]]:
    out = []
    with gzip.open(raw.cached(SOURCE, bundle_url(year)).path, "rt") as file:
        for line in file:
            data = json.loads(line)
            parsed = urlparse(data["url"])
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            out.append((parsed.path.rsplit("/", 1)[1], query, data["response"]))
    return out


def parse() -> None:
    places, boxes, votes, totals = [], [], [], []
    for year in DAYS:
        headers: dict[tuple[int, int], dict[str, str]] = {}
        cevres: dict[tuple[int, int, int], int] = {}
        found = pairs(year)
        for path, query, response in found:
            if path == "getIlceList":
                for place in response:
                    il, ilce, belde = (int(place[key]) for key in ("il_ID", "ilce_ID", "belde_ID"))
                    cevres[(il, ilce, belde)] = int(place["secim_CEVRESI_ID"])
                    places.append(
                        {
                            "year": year,
                            "il": il,
                            "ilce": ilce,
                            "belde": belde,
                            "cevre": int(place["secim_CEVRESI_ID"]),
                            "seats": int(place["secilecek_ADAY_SAYISI"]),
                            "province": place["il_ADI"],
                            "name": place["ilce_ADI"],
                        }
                    )
            elif path == "getSandikSecimSonucBaslikList":
                key = (int(query["ilId"]), int(query.get("secimCevresiId", 0)))
                headers[key] = {str(column["column_NAME"]): str(column["ad"]) for column in response}
        seen: dict[str, set[str]] = {}
        for (_, cevre), header in headers.items():
            for column, label in header.items():
                if not cevre and column.startswith("parti"):
                    seen.setdefault(column, set()).add(label)
        national = {column: next(iter(labels)) for column, labels in seen.items() if len(labels) == 1}
        for path, query, response in found:
            if path != "getSecimSandikSonucList":
                continue
            target = totals if "ilceId" not in query else boxes
            for row in response:
                il, ilce, belde = (int(row[key]) for key in ("il_ID", "ilce_ID", "belde_ID"))
                cevre = cevres.get((il, ilce, belde), 0)
                parties = headers[(il, 0)]
                named = headers.get((il, cevre), {})
                box = row.get("sandik_ID") if target is boxes else None
                record = {
                    "year": year,
                    "il": il,
                    "ilce": ilce,
                    "belde": belde,
                    "box": box,
                    "number": row.get("sandik_NO"),
                    "muhtarlik": row.get("muhtarlik_ID") or None,
                    "muhtarlik_name": row.get("muhtarlik_ADI"),
                    "prison": row.get("cezaevi_ID") or None,
                    "registered": row.get("secmen_SAYISI"),
                    "voters": row.get("oy_KULLANAN_SECMEN_SAYISI"),
                    "valid": row.get("gecerli_OY_TOPLAMI"),
                    "invalid": row.get("gecersiz_OY_TOPLAMI"),
                }
                target.append(record)
                if target is totals:
                    continue
                listed = 0
                for column, value in row.items():
                    if not value or not column.endswith("_ALDIGI_OY"):
                        continue
                    if column.startswith("parti"):
                        label, kind = parties.get(column) or national[column], "party"
                    elif column.startswith("bagimsiz"):
                        label, kind = named[column], "independent"
                        listed += int(value)
                    else:
                        raise ValueError(f"{year} {il} {ilce} {belde}: unexpected column {column}")
                    votes.append(
                        {"year": year, "box": box, "label": label, "kind": kind, "column": column, "votes": value}
                    )
                pooled = int(row.get("bagimsiz_TOPLAM_OY") or 0) - listed
                if pooled < 0:
                    raise ValueError(f"{year} box {box}: named independents exceed the total")
                if pooled:
                    votes.append(
                        {
                            "year": year,
                            "box": box,
                            "label": None,
                            "kind": "independents",
                            "column": None,
                            "votes": pooled,
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(places).write_parquet(PARSED / "places.parquet")
    pl.DataFrame(boxes, infer_schema_length=None).write_parquet(PARSED / "boxes.parquet")
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    pl.DataFrame(votes, infer_schema_length=None).write_parquet(PARSED / "votes.parquet")


def place_keys(places: pl.DataFrame) -> dict[tuple[int, int, int, int], str]:
    keys = {}
    for (year,), frame in places.group_by("year"):
        ilces = {
            (row["il"], row["ilce"]): row["name"] for row in frame.filter(pl.col("belde") == 0).iter_rows(named=True)
        }
        for row in frame.iter_rows(named=True):
            province = slug(title(row["province"]))
            base = ilces.get((row["il"], row["ilce"])) or ILCES_WITHOUT_OWN_MAHALLES[(row["il"], row["ilce"])]
            own = row["name"].removeprefix(base).removeprefix(base.split("/")[0]).strip() if row["belde"] else ""
            district = slug(title(base.split("/")[0]))
            parts = [province]
            if district != f"{province}-merkez":
                parts.append(district)
            elif own:
                parts.append("merkez")
            if own:
                parts.append(slug(title(own)))
            key = "-".join(parts)
            keys[(int(year), row["il"], row["ilce"], row["belde"])] = KEYS.get((int(year), key), key)
    return keys


def emit() -> Path:
    year = int(os.environ["TR_MECLIS_YEAR"])
    day = DAYS[year]
    places = pl.read_parquet(PARSED / "places.parquet").filter(pl.col("year") == year)
    boxes = pl.read_parquet(PARSED / "boxes.parquet").filter(pl.col("year") == year)
    votes = pl.read_parquet(PARSED / "votes.parquet").filter(pl.col("year") == year)
    keys = place_keys(places)
    with connect() as conn:
        mayors = {
            key.removeprefix(f"{day}:").removesuffix("-mayor"): (election, area, path, text.removeprefix("Mayor of "))
            for key, election, area, path, text in conn.execute(
                """select x.value, c.election_id, c.district_area_id, s.path, n.text
                   from external_id x join contest c on c.id = x.entity_id
                   join slug s on s.entity_id = c.election_id
                   join name n on n.entity_id = c.id and n.lang = 'en' and n.kind = 'official'
                   where x.scheme = 'tr.contest' and x.value like %s""",
                [f"{day}:%-mayor"],
            ).fetchall()
        }
        areas = dict(
            conn.execute(
                "select value::int, entity_id from external_id where scheme = %s", [f"tr.ysk.muhtarlik.{year}"]
            ).fetchall()
        )
        party_names = dict(
            conn.execute(
                """select e.value, (select text from name n where n.entity_id = e.entity_id and n.kind = 'official'
                                    and (n.valid_from is null or n.valid_from <= %s)
                                    order by n.valid_from desc nulls last limit 1)
                   from external_id e where e.scheme = 'tr.party'""",
                [day],
            ).fetchall()
        )
        counted = set(boxes.select("il", "ilce", "belde").unique().iter_rows())
        skipped = {
            place: key
            for place, key in keys.items()
            if key not in mayors or (year, key) in ANNULLED or place[1:] not in counted
        }
        print(f"{year}: {len(skipped)} councils left out: {sorted(skipped.values())}")
        kept = pl.DataFrame(
            [place[1:] for place in keys if place not in skipped], schema=["il", "ilce", "belde"], orient="row"
        )
        places, boxes = (frame.join(kept, on=["il", "ilce", "belde"]) for frame in (places, boxes))
        votes = votes.join(boxes.select("box"), on="box")
        unknown = sorted(set(votes.filter(pl.col("kind") == "party")["label"]) - set(PARTIES))
        if unknown:
            raise ValueError(f"{year}: unknown party labels {unknown}")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Turkey's {year} municipal council elections by ballot box, from YSK's Sandık Sonuçları",
            polity="TR",
        )
        turkey = cs.polity("TR")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=SITE,
            license=LICENSE,
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, bundle_url(year)), source)
        cs.scope_units("tr.sandik", f"{year}:%")
        place_rows = {(row["il"], row["ilce"], row["belde"]): row for row in places.iter_rows(named=True)}
        contests: dict[tuple[int, int, int], tuple[str, UUID, UUID]] = {}
        for (il, ilce, belde), row in sorted(place_rows.items()):
            key = keys[(year, il, ilce, belde)]
            election, area, path, english = mayors[key]
            contest_key = f"{day}:{key}-council"
            contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                district_area_id=area,
                magnitude=row["seats"],
                round_number=1,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{english} municipal council",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"{english} Belediye Meclisi Üyeliği",
                lang="tr",
                kind="native",
                is_preferred=False,
            )
            cs.add("slug", path=f"{path}/municipal-council", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            contests[(il, ilce, belde)] = (contest_key, contest, result_set)
        boxes = boxes.with_columns(
            code=pl.format("{}:{}:{}", pl.col("il").cast(pl.String).str.zfill(2), "ilce", "number"),
            place=pl.struct("il", "ilce", "belde"),
        )
        unit_ids = {code: str(cs.unit("tr.sandik", f"{year}:{code}")) for code in boxes["code"]}
        place_area = {place: mayors[keys[(year, *place)]][1] for place in contests}
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": unit_ids[row["code"]],
                        "polity_id": str(turkey),
                        "scheme_code": "tr.sandik",
                        "code": f"{year}:{row['code']}",
                        "parent_id": None,
                        "area_relation": "within",
                        "area_id": str(
                            place_area[(row["il"], row["ilce"], row["belde"])]
                            if row["prison"] or row["muhtarlik"] not in areas
                            else areas[row["muhtarlik"]]
                        ),
                        "kind": "standard",
                        "valid_from": day,
                        "valid_to": day + timedelta(days=1),
                    }
                    for row in boxes.iter_rows(named=True)
                ],
                schema_overrides={"parent_id": pl.String},
            ),
        )
        box_place = {
            row["box"]: (row["il"], row["ilce"], row["belde"])
            for row in boxes.select("box", "il", "ilce", "belde").iter_rows(named=True)
        }
        box_unit = {row["box"]: unit_ids[row["code"]] for row in boxes.select("box", "code").iter_rows(named=True)}
        options: dict[tuple[str, str], str] = {}
        values = []
        for row in boxes.iter_rows(named=True):
            _, _, result_set = contests[box_place[row["box"]]]
            for measure, column in (
                (REGISTERED, "registered"),
                (VOTERS, "voters"),
                (VALID, "valid"),
                (INVALID, "invalid"),
            ):
                if row[column] is not None:
                    values.append((str(result_set), box_unit[row["box"]], None, measure, row[column]))
        for row in votes.iter_rows(named=True):
            contest_key, contest, result_set = contests[box_place[row["box"]]]
            label = row["label"]
            owner = DATED.get((label, year), PARTIES[label]) if row["kind"] == "party" else None
            option_key, kind, ballot_label = (
                (f"{contest_key}:{owner}", "list", party_names[owner])
                if owner is not None
                else (f"{contest_key}:{row['column'].removesuffix('_ALDIGI_OY')}", "candidate", title(label))
                if row["kind"] == "independent"
                else (f"{contest_key}:independents", "independents", INDEPENDENTS)
            )
            if (contest_key, option_key) not in options:
                option = cs.key("tr.option", option_key)
                options[(contest_key, option_key)] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind=kind,
                    party_id=None if owner is None else cs.key("tr.party", owner),
                    ballot_label=ballot_label,
                )
            values.append(
                (str(result_set), box_unit[row["box"]], options[(contest_key, option_key)], VOTES, row["votes"])
            )
        cs.frame(
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
            ).select(
                "result_set_id",
                "unit_id",
                "option_id",
                channel=pl.lit(0, pl.Int16),
                measure="measure",
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        return cs.write()
