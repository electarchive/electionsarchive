"""Turkey's provincial general council (il genel meclisi) elections of 2009, 2014, 2019, and 2024 by ballot box, from
the results service of the Supreme Election Council's Sandık Sonuçları site (sonuc.ysk.gov.tr).

The service gives the provincial council as the fourth part of each local election. Each district (ilçe) of a province
is one constituency that elects its members by party list. For each constituency the service gives the number of
members, and for each ballot box the registered voters, voters, valid and invalid votes, and the votes for each party
list and each independent candidate."""

import gzip
import json
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES, title
from ea.sources.tr.ysk_meclis import DATED, PARTIES
from ea.sources.tr.ysk_muhtarlik import API, DAYS, HEADERS
from ea.store import DATA

SOURCE = "tr.ysk_il_meclisi"
CACHE = DATA / "tmp" / SOURCE
PROVINCIAL = 4  # the service's number for the provincial council among the parts of a local election
SECIM_IDS = {2009: 12972, 2014: 36039, 2019: 49302, 2024: 60882}
Rows = list[dict[str, int | str]]
WORKERS = 5
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
_pause = threading.Lock()


def bundle_url(year: int) -> str:
    return f"{API}getSecimSandikSonucList?" + urlencode(
        {"secimId": SECIM_IDS[year], "secimTuru": PROVINCIAL, "yurtIciDisi": 1}
    )


def get(path: str, query: Mapping[str, int | str]) -> Rows:
    """One call to the service, tried again after a wait because the service resets many connections."""
    url = f"{API}{path}?{urlencode(query)}"
    waits = [1, 2, 3, 5, 8, 13, 20, 30, 45, 60] * 3
    for wait in waits:
        try:
            with httpx.Client(headers=HEADERS, timeout=40) as client:
                response = client.get(url)
            response.raise_for_status()
            body = response.json()
            time.sleep(0.3)
            return body
        except httpx.HTTPError, ValueError:
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def cached(file: Path, path: str, query: Mapping[str, int | str]) -> Rows:
    if not file.exists():
        file.parent.mkdir(parents=True, exist_ok=True)
        body = get(path, query)
        partial = file.with_suffix(".part")
        partial.write_text(json.dumps(body, ensure_ascii=False))
        partial.rename(file)
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
        "sorguTuru": 1,
    }


def crawl(year: int) -> list[tuple[str, Rows]]:
    """Every response the service gives for the year's provincial councils, with the request that fetched it."""
    folder = CACHE / str(year)
    base: dict[str, int | str] = {"secimId": SECIM_IDS[year], "secimTuru": PROVINCIAL}
    out: list[tuple[str, Rows]] = []

    def call(name: str, path: str, query: Mapping[str, int | str]) -> Rows:
        query = base | dict(query)
        rows = cached(folder / f"{name}.json", path, query)
        out.append((f"{API}{path}?{urlencode(query)}", rows))
        return rows

    ils = call("ils", "getIlList", {"sandikTuru": -1, "yurtIciDisi": 1})

    def province(il: dict[str, int | str]) -> list[tuple[str, Rows]]:
        il_id = int(il["il_ID"])
        mine: list[tuple[str, Rows]] = []

        def ask(name: str, path: str, query: Mapping[str, int | str]) -> Rows:
            query = base | dict(query)
            rows = cached(folder / f"{name}.json", path, query)
            mine.append((f"{API}{path}?{urlencode(query)}", rows))
            return rows

        places = ask(
            f"ilces-{il_id}",
            "getIlceList",
            {"ilId": il_id, "secimCevresiId": "", "sandikTuru": -1, "yurtIciDisi": 1},
        )
        ask(
            f"header-{il_id}",
            "getSandikSecimSonucBaslikList",
            {"secimCevresiId": "", "ilId": il_id, "bagimsiz": 1, "yurtIciDisi": 1},
        )
        ask(f"totals-{il_id}", "getSecimSandikSonucList", results(il_id, ""))
        independents: set[int] = set()
        for ilce_id in sorted({int(place["ilce_ID"]) for place in places}):
            boxes = ask(f"boxes-{il_id}-{ilce_id}", "getSecimSandikSonucList", results(il_id, ilce_id))
            if any(box.get("bagimsiz_TOPLAM_OY") for box in boxes):
                independents.add(ilce_id)
        for place in places:
            if int(place["ilce_ID"]) in independents:
                cevre = int(place["secim_CEVRESI_ID"])
                ask(
                    f"header-{il_id}-{cevre}",
                    "getSandikSecimSonucBaslikList",
                    {"secimCevresiId": cevre, "ilId": il_id, "bagimsiz": 1, "yurtIciDisi": 1},
                )
        print(year, il_id, il["il_ADI"], len(places), flush=True)
        return mine

    with ThreadPoolExecutor(WORKERS) as pool:
        for mine in pool.map(province, ils):
            out.extend(mine)
    return out


def fetch(years: list[int] | None = None) -> None:
    """Save the service's responses for each year into the raw cache as one file of JSON lines."""
    index = raw.keys(SOURCE)
    for year in years or list(SECIM_IDS):
        url = bundle_url(year)
        if url in index:
            continue
        lines = "".join(
            json.dumps({"url": request, "response": response}, ensure_ascii=False) + "\n"
            for request, response in crawl(year)
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


REFERENCE = DATA / "reference" / "tr"
AREAS = Path(__file__).with_name("ysk_sonuc_areas.tsv")
TURKEY = "01a13c0c-c7a6-7799-b107-b55e848cecf2"  # the polity row the archive holds for Türkiye
SITE = "https://sonuc.ysk.gov.tr"
SANDIK = "tr.sandik"
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fil-meclisi"))
TITLE = "Sandık Sonuçları: il genel meclisi üyeliği"
NOTE = (
    "Provincial general council results for each ballot box, read from the results service of YSK's Sandık Sonuçları "
    "site for the local elections of {year}. The service gives each district's number of members, and for each "
    "ballot box its registered voters, voters, valid and invalid votes, and the votes for each party list and each "
    "independent candidate. It gives no seats won."
)
METHOD = (
    "Counted by the Supreme Election Council for each ballot box and read from the results service of the council's "
    "Sandık Sonuçları site. Each district of the province is one constituency. A box's registered voters, voters, "
    "and valid and invalid ballots are the box's own counts, and a list's votes are the votes the box gives it. "
    "Where several independent candidates stood and the service names them, each one's votes are their own option; "
    "where the service gives the independents only as one total, the total is one option. The service gives no seats "
    "won, so the result set has no outcomes."
)
SYSTEM_NOTE = (
    "Members of a provincial general council are elected by closed party list in each district of the province, "
    "which is one constituency, with seats allocated by the D'Hondt method among the lists that pass a 10 percent "
    "threshold (Law 2972 arts. 5 and 23)."
)
# Districts the archive holds under another name or as another unit. Ondokuzmayıs of Samsun is the archive's "19-May",
# the district the council elections of 2009 list. Kemalpaşa of Artvin was the belde of Hopa until it became a
# district, so its contest takes the area the archive holds for the belde.
ILCE_AREAS = {
    (55, 723): "01a13c0f-5a0a-7383-b19f-f865eb3a25d3",
    (8, 11888): "01a13c0f-5a08-726e-905a-2cee5d8507c1",
}
Row = dict[str, int | str]


class Place(NamedTuple):
    il: int
    ilce: int
    cevre: int
    seats: int
    province: str
    name: str


class Year(NamedTuple):
    places: dict[tuple[int, int], Place]
    header: dict[int, dict[str, str]]  # a province's party columns
    named: dict[tuple[int, int], dict[str, str]]  # a province's independents' columns, by constituency
    totals: dict[tuple[int, int], Row]  # the service's total for each constituency
    boxes: list[Row]


def number(value: object) -> int:
    return int(str(value))


def read(year: int) -> Year:
    places: dict[tuple[int, int], Place] = {}
    header: dict[int, dict[str, str]] = {}
    named: dict[tuple[int, int], dict[str, str]] = {}
    totals: dict[tuple[int, int], Row] = {}
    boxes: list[Row] = []
    for path, query, response in pairs(year):
        if path == "getIlceList":
            for row in response:
                il, ilce = number(row["il_ID"]), number(row["ilce_ID"])
                if number(row["belde_ID"]):
                    raise ValueError(f"{year}: {il}/{ilce} is a belde")
                places[(il, ilce)] = Place(
                    il,
                    ilce,
                    number(row["secim_CEVRESI_ID"]),
                    number(row["secilecek_ADAY_SAYISI"]),
                    str(row["il_ADI"]),
                    str(row["ilce_ADI"]),
                )
        elif path == "getSandikSecimSonucBaslikList":
            labels = {str(c["column_NAME"]): str(c["ad"]) for c in response}
            if query.get("secimCevresiId"):
                named[(number(query["ilId"]), number(query["secimCevresiId"]))] = labels
            else:
                header[number(query["ilId"])] = labels
        elif path == "getSecimSandikSonucList":
            if "ilceId" in query:
                boxes += response
            else:
                for row in response:
                    totals[(number(row["il_ID"]), number(row["ilce_ID"]))] = row
    return Year(places, header, named, totals, boxes)


def ilce_areas() -> tuple[dict[tuple[int, int], str], dict[str, str]]:
    """The archive's area for each district, by the service's province and district numbers, then by the key of the
    district's unit in the mayoral results (the province's and the district's slug), then by a name no other district
    has."""
    rows = pl.read_csv(AREAS, separator="\t", schema_overrides={"key": pl.String}).filter(pl.col("kind") == "ilce")
    by_number = {}
    for key, area in zip(rows["key"], rows["area_id"], strict=True):
        il, ilce = key.split(":")
        by_number[(int(il), int(ilce))] = area
    units = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(pl.col("scheme_code") == "tr.ilce")
    by_key = dict(zip(units["code"], units["area_id"], strict=True))
    areas = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "tr.ilce")
    names = Counter(areas["name"])
    by_key |= {f"name:{name}": id for id, name in zip(areas["id"], areas["name"], strict=True) if names[name] == 1}
    return by_number, by_key


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of Turkey's provincial general councils",
    )
    p.external.add(TURKEY)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TURKEY,
        valid_from=date(2009, 1, 1),
        valid_to=None,
        rules={
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "contest", "percent": 10},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        rules_version=1,
    )
    p.add("note", id=p.id("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def build(year: int) -> Package:
    day = DAYS[year]
    data = read(year)
    external_id = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value"])
    parties = dict(external_id.filter(pl.col("scheme") == "tr.party").select("value", "entity_id").iter_rows())
    party_name = dict(pl.read_parquet(REFERENCE / "party.parquet").select("id", "name").iter_rows())
    muhtarlik = {
        int(value): entity
        for value, entity in external_id.filter(pl.col("scheme") == f"tr.ysk.muhtarlik.{year}")
        .select("value", "entity_id")
        .iter_rows()
    }
    elections = (
        pl.read_parquet(REFERENCE / "election.parquet")
        .filter((pl.col("name") == f"{year} Turkish local elections") & pl.col("parent_id").is_null())
        .filter(pl.col("kind") == "local")
    )
    if elections.height != 1:
        raise ValueError(f"{year}: {elections.height} local elections")
    local = elections["id"][0]
    units = {
        code: id
        for id, code in pl.read_parquet(REFERENCE / "reporting_unit.parquet")
        .filter((pl.col("scheme_code") == SANDIK) & pl.col("code").str.starts_with(f"{year}:"))
        .select("id", "code")
        .iter_rows()
    }
    by_number, by_slug = ilce_areas()

    def area_of(place: Place) -> str:
        if (place.il, place.ilce) in by_number:
            return by_number[(place.il, place.ilce)]
        district = title(place.name.split("/")[0])
        found = (
            by_slug.get(f"{slug(title(place.province))}-{slug(district)}")
            or by_slug.get(f"{place.il:02d}:{district}")
            or by_slug.get(f"name:{district}")
            or ILCE_AREAS.get((place.il, place.ilce))
        )
        if found is None:
            raise KeyError(f"{year}: no area for {place.province} / {place.name}")
        return found

    seen: dict[str, set[str]] = defaultdict(set)
    for labels in data.header.values():
        for column, label in labels.items():
            if column.startswith("parti"):
                seen[column].add(label)
    national = {column: next(iter(found)) for column, found in seen.items() if len(found) == 1}
    counted: dict[tuple[int, int], list[Row]] = defaultdict(list)
    for row in data.boxes:
        counted[(number(row["il_ID"]), number(row["ilce_ID"]))].append(row)
    if not set(counted) <= set(data.places):
        raise ValueError(f"{year}: boxes in districts the service does not list: {set(counted) - set(data.places)}")

    p = Package(
        f"{SOURCE}/il-meclisi-{year}",
        SOURCE,
        f"Turkey's {year} provincial general council elections by ballot box, from YSK's Sandık Sonuçları",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {TURKEY, SANDIK, local}
    title_text = f"{TITLE} ({year})"
    source = p.id("source", title_text)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=title_text,
        url=SITE,
        license=LICENSE,
        notes=NOTE.format(year=year),
    )
    p.artifact(raw.cached(SOURCE, bundle_url(year)), source)

    path = f"tr/{day}-local"
    province_election: dict[int, str] = {}
    contests: dict[tuple[int, int], tuple[str, str]] = {}
    used_slugs: set[str] = set()
    for (il, ilce), place in sorted(data.places.items()):
        if (il, ilce) not in counted:
            continue
        province = title(place.province)
        election = province_election.get(il)
        if election is None:
            election = province_election[il] = p.id("election", year, il)
            p.add(
                "election",
                id=election,
                polity_id=TURKEY,
                parent_id=local,
                kind="local",
                date_from=day,
                status="held",
            )
            p.name(election, f"{year} {province} provincial council election", "en", "official")
            p.name(election, f"{year} {province} İl Genel Meclisi Seçimi", "tr", "native", False)
            p.add("slug", path=f"{path}/{slug(province)}-provincial-council", entity_id=election, is_current=True)
        district = title(place.name.split("/")[0])
        contest = p.id("contest", year, il, ilce)
        area = area_of(place)
        p.external.add(area)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            district_area_id=area,
            magnitude=place.seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{district} ({province}) Provincial council", "en", "official")
        p.name(contest, f"{district} ({province}) İl Genel Meclisi Üyeliği", "tr", "native", False)
        tail = slug(district)
        if f"{il}/{tail}" in used_slugs:
            tail = f"{tail}-{ilce}"
        used_slugs.add(f"{il}/{tail}")
        p.add("slug", path=f"{path}/{slug(province)}-provincial-council/{tail}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", year, il, ilce)
        p.add(
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
        p.add(
            "note",
            id=p.id("note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        contests[(il, ilce)] = (contest, result_set)

    options: dict[tuple[int, int, str], str] = {}

    def option_of(il: int, ilce: int, owner: str, kind: str, label: str, party: str | None) -> str:
        found = options.get((il, ilce, owner))
        if found is None:
            found = options[(il, ilce, owner)] = p.id("option", year, il, ilce, owner)
            if party is not None:
                p.external.add(party)
            p.add(
                "contest_option",
                id=found,
                contest_id=contests[(il, ilce)][0],
                kind=kind,
                party_id=party,
                ballot_label=label,
            )
        return found

    new_units: dict[str, str] = {}
    unit_rows: list[dict[str, object]] = []
    values: list[tuple[str, str, str | None, int, int]] = []
    unknown: set[str] = set()
    for (il, ilce), rows in sorted(counted.items()):
        place = data.places[(il, ilce)]
        contest, result_set = contests[(il, ilce)]
        header = data.header[il]
        named = data.named.get((il, place.cevre), {})
        for row in sorted(rows, key=lambda r: number(r["sandik_ID"])):
            code = f"{year}:{il:02d}:{ilce}:{row['sandik_NO']}"
            if code in units:
                unit = units[code]
                p.external.add(unit)
            else:
                unit = new_units.get(code) or p.id("unit", code)
                if code not in new_units:
                    new_units[code] = unit
                    box_area = muhtarlik.get(number(row["muhtarlik_ID"])) if not row.get("cezaevi_ID") else None
                    unit_rows.append(
                        {
                            "id": unit,
                            "polity_id": TURKEY,
                            "scheme_code": SANDIK,
                            "code": code,
                            "area_relation": "within",
                            "area_id": box_area or area_of(place),
                            "kind": "standard",
                            "valid_from": day,
                            "valid_to": day + timedelta(days=1),
                        }
                    )
                    p.external.add(box_area or area_of(place))
            for measure, column in (
                (REGISTERED, "secmen_SAYISI"),
                (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
                (VALID, "gecerli_OY_TOPLAMI"),
                (INVALID, "gecersiz_OY_TOPLAMI"),
            ):
                if row.get(column) is not None:
                    values.append((result_set, unit, None, measure, number(row[column])))
            listed = 0
            for column, value in row.items():
                if not value or not column.endswith("_ALDIGI_OY"):
                    continue
                if column.startswith("parti"):
                    label = header.get(column) or named.get(column) or national.get(column)
                    if label is None:
                        raise ValueError(f"{year} {il}/{ilce}: no label for {column}")
                    if label not in PARTIES:
                        unknown.add(label)
                        continue
                    owner = DATED.get((label, year), PARTIES[label])
                    option = option_of(il, ilce, owner, "list", party_name[parties[owner]], parties[owner])
                elif column.startswith("bagimsiz") and column in named:
                    listed += number(value)
                    option = option_of(il, ilce, column, "candidate", title(named[column]), None)
                elif column.startswith("bagimsiz"):
                    continue
                else:
                    raise ValueError(f"{year} {il}/{ilce}: unexpected column {column}")
                values.append((result_set, unit, option, VOTES, number(value)))
            pooled = number(row.get("bagimsiz_TOPLAM_OY") or 0) - listed
            unnamed = sum(
                number(v)
                for c, v in row.items()
                if c.startswith("bagimsiz") and c.endswith("_ALDIGI_OY") and c not in named
            )
            if pooled < 0 or (unnamed and unnamed != pooled):
                raise ValueError(f"{year} box {row['sandik_ID']}: independents {listed}/{unnamed} of {pooled}")
            if pooled:
                option = option_of(il, ilce, "independents", "independents", INDEPENDENTS, None)
                values.append((result_set, unit, option, VOTES, pooled))
    if unknown:
        raise ValueError(f"{year}: unknown party labels {sorted(unknown)}")
    p.frame("reporting_unit", pl.DataFrame(unit_rows))
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
    over = [
        number(row["oy_KULLANAN_SECMEN_SAYISI"]) - number(row["secmen_SAYISI"])
        for row in data.boxes
        if number(row.get("oy_KULLANAN_SECMEN_SAYISI") or 0) > number(row.get("secmen_SAYISI") or 0)
    ]
    if over:
        p.accept(
            "accounting.turnout",
            f"In {len(over):,} boxes the service gives more voters than registered voters, by at most {max(over):,}; "
            "the boxes are stored as the service publishes them.",
        )
    off = [
        (row, listed)
        for row in data.boxes
        if (
            listed := sum(
                number(value)
                for column, value in row.items()
                if column.endswith("_ALDIGI_OY") and not column.startswith("bagimsiz") and value
            )
            + number(row.get("bagimsiz_TOPLAM_OY") or 0)
        )
        != number(row.get("gecerli_OY_TOPLAMI") or 0)
    ]
    if off:
        found = "; ".join(
            f"{title(str(row['ilce_ADI']))}, {title(str(row['il_ADI']))}, box {row['sandik_NO']}: the lists and "
            f"independents have {listed:,} votes, and the box's valid votes are {number(row['gecerli_OY_TOPLAMI']):,}"
            for row, listed in off
        )
        p.accept(
            "accounting.votes_valid",
            f"In {len(off)} {'box' if len(off) == 1 else 'boxes'} the service's own figures disagree: {found}. "
            "The box is stored as the service publishes it.",
        )
    compare(year, data, counted)
    print(f"{year}: {len(contests)} contests, {len(new_units)} new units, {len(data.boxes) - len(new_units)} held")
    return p


def compare(year: int, data: Year, counted: dict[tuple[int, int], list[Row]]) -> None:
    """The boxes of each district against the total the service gives for the district."""
    measures = ("secmen_SAYISI", "oy_KULLANAN_SECMEN_SAYISI", "gecerli_OY_TOPLAMI", "gecersiz_OY_TOPLAMI")
    off: Counter[str] = Counter()
    national: Counter[str] = Counter()
    boxed: Counter[str] = Counter()
    for key, total in data.totals.items():
        rows = counted.get(key, [])
        for column in measures:
            national[column] += number(total.get(column) or 0)
            boxed[column] += sum(number(row.get(column) or 0) for row in rows)
            if sum(number(row.get(column) or 0) for row in rows) != number(total.get(column) or 0):
                off[column] += 1
    print(year, "districts:", len(data.totals), "off:", dict(off))
    print(year, "service total:", dict(national))
    print(year, "box total:    ", dict(boxed))


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/il-meclisi-{year}" for year in SECIM_IDS)]


def parse() -> None:
    registry().write()
    for year in SECIM_IDS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())
