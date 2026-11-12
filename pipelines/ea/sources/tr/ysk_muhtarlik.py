import gzip
import itertools
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "tr.ysk_muhtarlik"
API = "https://sonuc.ysk.gov.tr/api/"
PARSED = DATA / "parsed" / SOURCE
CACHE = DATA / "tmp" / "ysk_muhtarlik"
REGISTER_2024 = Path(__file__).with_name("muhtarlik_2024.tsv")
HISTORY = Path(__file__).with_name("muhtarlik_history.tsv")
KIND_CHANGES = Path(__file__).with_name("muhtarlik_kind_changes.tsv")
SECIM_IDS = {2009: 12972, 2014: 36039, 2019: 49302}
TYPES = {2: "mahalle", 4: "köy"}
YSK_TOTALS = {
    2009: (52879, None, None, None),
    2014: (49776, 30062, 1610, 18104),
    2019: (50217, 30451, 1587, 18179),
}
Rows = list[dict[str, int | str]]
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://sonuc.ysk.gov.tr/"}
PAUSE = 0.8
ILCES_WITHOUT_OWN_MAHALLES = {(67, 1043): "KİLİMLİ", (67, 1044): "KOZLU"}


def bundle_url(year: int, secim_turu: int) -> str:
    return f"{API}getMuhtarlikList?" + urlencode(
        {"secimId": SECIM_IDS[year], "secimTuru": secim_turu, "yurtIciDisi": 1}
    )


def get(client: httpx.Client, path: str, query: dict[str, int]) -> Rows:
    url = f"{API}{path}?{urlencode(query)}"
    for wait in (2, 5, 15, 40, 90, 180):
        try:
            response = client.get(url, timeout=40)
            response.raise_for_status()
            body = response.json()
            time.sleep(PAUSE)
            return body
        except (httpx.HTTPError, ValueError) as error:
            print(url, error, flush=True)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def cached(client: httpx.Client, file: Path, path: str, query: dict[str, int]) -> Rows:
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(get(client, path, query), ensure_ascii=False))
    return json.loads(file.read_text())


def crawl(client: httpx.Client, year: int, secim_turu: int) -> list[tuple[str, Rows]]:
    secim_id = SECIM_IDS[year]
    folder = CACHE / str(year) / str(secim_turu)
    base = {"secimId": secim_id, "secimTuru": secim_turu}
    out = []
    query = base | {"yurtIciDisi": 1}
    ils = cached(client, folder / "ils.json", "getIlList", query)
    out.append((f"{API}getIlList?{urlencode(query)}", ils))
    for il in ils:
        il_id = int(il["il_ID"])
        query = base | {"ilId": il_id, "secimCevresiId": 0, "sandikTuru": -1, "yurtIciDisi": 1}
        ilces = cached(client, folder / f"ilces-{il_id}.json", "getIlceList", query)
        out.append((f"{API}getIlceList?{urlencode(query)}", ilces))
        for ilce in ilces:
            keys = ("ilce_ID", "belde_ID", "birim_ID", "secim_CEVRESI_ID")
            ilce_id, belde_id, birim_id, cevre_id = (int(ilce[key]) for key in keys)
            query = base | {
                "ilId": il_id,
                "ilceId": ilce_id,
                "beldeId": belde_id,
                "birimId": birim_id,
                "secimCevresiId": cevre_id,
                "sandikTuru": -1,
                "yurtIciDisi": 1,
            }
            name = f"m-{il_id}-{ilce_id}-{belde_id}-{birim_id}-{cevre_id}.json"
            out.append(
                (f"{API}getMuhtarlikList?{urlencode(query)}", cached(client, folder / name, "getMuhtarlikList", query))
            )
    return out


def fetch() -> None:
    index = raw.keys(SOURCE)
    with httpx.Client(headers=HEADERS) as client:
        for year in SECIM_IDS:
            for secim_turu in TYPES:
                url = bundle_url(year, secim_turu)
                if url in index:
                    continue
                lines = "".join(
                    json.dumps({"url": request, "response": response}, ensure_ascii=False) + "\n"
                    for request, response in crawl(client, year, secim_turu)
                )
                raw.store(
                    SOURCE,
                    url,
                    gzip.compress(lines.encode(), mtime=0),
                    suffix=".jsonl.gz",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/gzip",
                )


def read(year: int, secim_turu: int) -> pl.DataFrame:
    ilces: dict[tuple[int, int], str] = dict(ILCES_WITHOUT_OWN_MAHALLES)
    beldes: list[tuple[int, int, int, str]] = []
    lists = []
    with gzip.open(raw.cached(SOURCE, bundle_url(year, secim_turu)).path, "rt") as file:
        for line in file:
            data = json.loads(line)
            parsed = urlparse(data["url"])
            query = {key: int(value[0]) for key, value in parse_qs(parsed.query).items()}
            match parsed.path.rsplit("/", 1)[1]:
                case "getIlceList":
                    for ilce in data["response"]:
                        if ilce["belde_ID"] == 0:
                            ilces[(ilce["il_ID"], ilce["ilce_ID"])] = ilce["ilce_ADI"]
                        else:
                            beldes.append((ilce["il_ID"], ilce["ilce_ID"], ilce["belde_ID"], ilce["ilce_ADI"]))
                case "getMuhtarlikList":
                    lists.append((query, data["response"]))
    belde_names = {}
    for il_id, ilce_id, belde_id, printed in beldes:
        prefix = ilces[(il_id, ilce_id)] + " "
        belde_names[(il_id, ilce_id, belde_id)] = printed.removeprefix(prefix)
    rows = []
    for query, response in lists:
        for row in response:
            if row["cezaevi_ID"]:
                continue
            key = (query["ilId"], query["ilceId"])
            rows.append(
                (
                    year,
                    secim_turu,
                    f"{query['ilId']:02}",
                    query["ilceId"],
                    ilces[key],
                    query["beldeId"] or None,
                    belde_names.get((*key, query["beldeId"])),
                    row["muhtarlik_ID"],
                    row["muhtarlik_ADI"].strip(),
                    int(row["min_SANDIK_NO"]),
                    int(row["max_SANDIK_NO"]),
                )
            )
    return pl.DataFrame(
        rows,
        schema=[
            "year",
            "secim_turu",
            "plate",
            "ilce_id",
            "ilce",
            "belde_id",
            "belde",
            "muhtarlik_id",
            "name",
            "first_box",
            "last_box",
        ],
        orient="row",
        infer_schema_length=None,
    )


def parse() -> None:
    frame = pl.concat([read(year, secim_turu) for year in SECIM_IDS for secim_turu in TYPES])
    register = (
        frame.group_by("year", "muhtarlik_id")
        .agg(
            pl.col("plate", "ilce_id", "ilce", "belde_id", "belde", "name").first(),
            ranges=pl.col("first_box").len(),
            boxes=(pl.col("last_box") - pl.col("first_box") + 1).sum(),
            in_lists=pl.col("secim_turu").unique().sort(),
        )
        .with_columns(
            kind=pl.when(pl.col("name").str.ends_with(" MAH.")).then(pl.lit("mahalle")).otherwise(pl.lit("köy")),
            in_lists=pl.col("in_lists").list.eval(pl.element().cast(pl.String)).list.join("+"),
        )
        .sort("year", "plate", "ilce", "belde", "name", nulls_last=False)
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    register.write_parquet(PARSED / "register.parquet")
    current = pl.read_csv(REGISTER_2024, separator="\t", schema_overrides={"plate": pl.String})
    ids_2024 = set(current["muhtarlik_id"])
    counts = (
        register.group_by("year")
        .agg(
            total=pl.len(),
            mahalle=((pl.col("kind") == "mahalle") & pl.col("belde_id").is_null()).sum(),
            belde_mahalle=((pl.col("kind") == "mahalle") & pl.col("belde_id").is_not_null()).sum(),
            koy=(pl.col("kind") == "köy").sum(),
            in_2024=pl.col("muhtarlik_id").is_in(list(ids_2024)).sum(),
        )
        .sort("year")
    )
    print(counts)
    for year, (total, mahalle, belde, koy) in YSK_TOTALS.items():
        print(year, "YSK", total, mahalle, belde, koy)
    history = (
        register.filter(~pl.col("muhtarlik_id").is_in(list(ids_2024)))
        .group_by("muhtarlik_id")
        .agg(
            pl.col("plate", "ilce", "belde", "name", "kind").last(),
            years=pl.col("year").sort().cast(pl.String).str.join(","),
        )
        .sort("plate", "ilce", "belde", "name", "muhtarlik_id", nulls_last=False)
    )
    history.select("muhtarlik_id", "plate", "ilce", "belde", "name", "kind", "years").write_csv(HISTORY, separator="\t")
    stems = pl.col("name").str.replace(r" (KÖYÜ|MAH\.)$", "").str.strip_chars()
    every = pl.concat(
        [
            register.select("year", "muhtarlik_id", "plate", "ilce", "name", "kind"),
            current.select(
                year=pl.lit(2024, dtype=pl.Int64),
                muhtarlik_id="muhtarlik_id",
                plate="plate",
                ilce="ilce",
                name="name",
                kind="kind",
            ),
        ]
    ).with_columns(stem=stems, place=pl.col("ilce").str.replace(r"/.*$", ""))
    years = sorted(every["year"].unique())
    changes = []
    for before, after in itertools.pairwise(years):
        gone = every.filter(pl.col("year") == before).join(
            every.filter(pl.col("year") == after), on="muhtarlik_id", how="anti"
        )
        new = every.filter(pl.col("year") == after).join(
            every.filter(pl.col("year") == before), on="muhtarlik_id", how="anti"
        )
        for match, keys in (("ilce", ["plate", "place", "stem"]), ("province", ["plate", "stem"])):
            joined = gone.join(new, on=keys, how="inner", suffix="_to")
            pairs = joined.filter(
                (pl.len().over("muhtarlik_id") == 1) & (pl.len().over("muhtarlik_id_to") == 1)
            ).select(
                from_id="muhtarlik_id",
                to_id="muhtarlik_id_to",
                plate="plate",
                ilce="ilce_to",
                from_year="year",
                from_name="name",
                from_kind="kind",
                to_year="year_to",
                to_name="name_to",
                to_kind="kind_to",
                match=pl.lit(match),
            )
            changes.append(pairs)
            gone = gone.join(pairs.select(muhtarlik_id="from_id"), on="muhtarlik_id", how="anti")
            new = new.join(pairs.select(muhtarlik_id="to_id"), on="muhtarlik_id", how="anti")
    pairs = pl.concat(changes)
    print(pairs.group_by("from_year", "from_kind", "to_year", "to_kind").len().sort("from_year", "from_kind"))
    pairs.filter(pl.col("from_kind") != pl.col("to_kind")).sort("plate", "ilce", "to_name", "to_id").write_csv(
        KIND_CHANGES, separator="\t"
    )
    print("history rows", history.height)
