import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.sources.tr.ysk_meclis import DATED, PARTIES, SITE, place_keys
from ea.store import DATA

SOURCE = "tr.ysk_mayor_boxes"
KEY = f"{SOURCE}/mayor-2009"
INBOX = DATA / "inbox" / SOURCE
REFERENCE = DATA / "reference" / "tr"
DAY = date(2009, 3, 29)
TITLE = "Sandık Sonuçları: belediye başkanlığı"
SECIM_ID = 12972
SECIM_TURU = 2
PREFIX = "browser://"
NOTE = (
    "Mayoral results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the "
    "local elections of March 29, 2009. For each municipality, the service gives each ballot box's registered "
    "voters, voters, valid and invalid votes, and the votes for each party's candidate and for the independents. "
    "It gives the independents as one total and, in its box rows, one column for each independent candidate whose "
    "name appears only in a separate header the service serves for each municipality. It gives no outcome."
)
METHOD = (
    "Counted for each ballot box by YSK. Boxes are the same boxes that held the council vote, so a box that "
    "already has a unit in the archive keeps it. A party's votes go to its candidate's option. Where one "
    "independent candidate stood, their column is their option's votes; where several stood, the box rows name "
    "the independents only by column number, so the results give their votes as one option."
)
POOLED = (
    "Several independent candidates stood in this municipality, and the box rows name them only by column number, so "
    "the independents' votes are one option here."
)
Row = dict[str, object]


def num(value: object) -> int:
    return int(str(value))


def text(value: object) -> str:
    return str(value)


def fetch() -> None:
    """Load the province files saved from the results service (data/inbox/tr.ysk_mayor_boxes/) into the raw cache.
    The site's API answers a browser but not a script, so each province's responses were saved as one file."""
    index = json.loads((INBOX / "index.json").read_text())
    have = raw.keys(SOURCE)
    for key, entry in index.items():
        if key.startswith(PREFIX) and key not in have:
            raw.store(
                SOURCE,
                key,
                (INBOX / entry["file"]).read_bytes(),
                suffix=".json",
                retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
                media_type=entry["media_type"],
            )


def files() -> list[str]:
    return sorted(key for key in raw.keys(SOURCE) if key.startswith(PREFIX))


def read() -> tuple[list[Row], dict[int, dict[str, str]], list[Row], list[Row], list[str]]:
    """The places, each province's column labels, the totals the service gives for each place, and the box rows."""
    places: list[Row] = []
    labels: dict[int, dict[str, str]] = {}
    totals: list[Row] = []
    boxes: list[Row] = []
    used = []
    for key in files():
        responses = json.loads(raw.cached(SOURCE, key).path.read_text())
        found = False
        for response in responses:
            parsed = urlparse(response["url"])
            query = {name: values[0] for name, values in parse_qs(parsed.query).items()}
            if query.get("secimId") != str(SECIM_ID) or query.get("secimTuru") != str(SECIM_TURU):
                continue
            if response["status"] != 200:
                raise ValueError(f"{key}: {response['url']} answered {response['status']}")
            found = True
            path = parsed.path.rsplit("/", 1)[1]
            if path == "getIlceList":
                places += response["body"]
            elif path == "getSandikSecimSonucBaslikList":
                labels[num(query["ilId"])] = {str(c["column_NAME"]): str(c["ad"]) for c in response["body"]}
            elif path == "getSecimSandikSonucList":
                (boxes if "ilceId" in query else totals).extend(response["body"])
        if found:
            used.append(key)
    return places, labels, totals, boxes, used


def _party_ids() -> dict[str, str]:
    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value"])
    x = x.filter(pl.col("scheme") == "tr.party")
    return dict(zip(x["value"], x["entity_id"], strict=True))


def build() -> Package:
    places, labels, totals, boxes, used = read()
    place_frame = pl.DataFrame(
        [
            {
                "year": 2009,
                "il": num(r["il_ID"]),
                "ilce": num(r["ilce_ID"]),
                "belde": num(r["belde_ID"]),
                "name": r["ilce_ADI"],
                "province": r["il_ADI"],
            }
            for r in places
        ]
    )
    keys = {place[1:]: key for place, key in place_keys(place_frame).items()}
    belde_place = defaultdict(list)
    for il, ilce, belde in keys:
        if belde:
            belde_place[(il, belde)].append((il, ilce, belde))

    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value"])
    contests = {
        value.removeprefix(f"{DAY}:").removesuffix("-mayor"): entity
        for value, entity in x.filter(
            (pl.col("scheme") == "tr.contest")
            & pl.col("value").str.starts_with(f"{DAY}:")
            & pl.col("value").str.ends_with("-mayor")
        )
        .select("value", "entity_id")
        .iter_rows()
    }
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
        options[row["contest_id"]].append(row)
    held = set(
        pl.read_parquet(REFERENCE / "result_set.parquet").filter(
            pl.col("is_default") & (pl.col("stage") == "official") & pl.col("contest_id").is_in(list(contests.values()))
        )["contest_id"]
    )
    parties = _party_ids()
    party_name = dict(pl.read_parquet(REFERENCE / "party.parquet").select("id", "name").iter_rows())
    units = {
        code: (id, polity)
        for id, code, polity in pl.read_parquet(REFERENCE / "reporting_unit.parquet")
        .filter((pl.col("scheme_code") == "tr.sandik") & pl.col("code").str.starts_with(f"{DAY.year}:"))
        .select("id", "code", "polity_id")
        .iter_rows()
    }
    polity = next(iter(units.values()))[1]

    national: dict[str, set[str]] = defaultdict(set)
    for header in labels.values():
        for column, label in header.items():
            if column.startswith("parti"):
                national[column].add(label)
    national_label = {column: next(iter(found)) for column, found in national.items() if len(found) == 1}

    def place_of(row: Row) -> tuple[int, int, int]:
        place = (num(row["il_ID"]), num(row["ilce_ID"]), num(row["belde_ID"]))
        if place in keys:
            return place
        found = belde_place.get((place[0], place[2]), [])
        if len(found) != 1:
            raise ValueError(f"box {row['sandik_ID']} belongs to no place: {place}")
        return found[0]

    unlabeled = [
        row for row in boxes if any(c.startswith("parti") and v and c not in national_label for c, v in row.items())
    ]
    dropped = {id(row) for row in unlabeled}
    counted = [row for row in boxes if id(row) not in dropped]

    columns: dict[tuple[int, int, int], Counter[str]] = defaultdict(Counter)
    for row in counted:
        for column, value in row.items():
            if column.startswith("bagimsiz") and column.endswith("_ALDIGI_OY") and value:
                columns[place_of(row)][column] += 1

    p = Package(
        KEY,
        SOURCE,
        "Turkey's 2009 mayoral votes by ballot box, from YSK's Sandık Sonuçları",
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
    for key in used:
        p.artifact(raw.cached(SOURCE, key), source)

    external: set[str] = {polity, "tr.sandik"}
    result_sets: dict[tuple[int, int, int], tuple[str, str, str]] = {}
    chosen: dict[tuple[int, int, int], dict[str, str]] = {}
    option_rows: dict[str, str] = {}
    pooled_places = set()
    counted_places = {place_of(row) for row in counted}
    for place, key in sorted(keys.items()):
        contest_id = contests.get(key)
        if contest_id is None or place not in counted_places:
            continue
        row = contest[contest_id]
        external |= {contest_id, row["election_id"]}
        result_set = p.id("result_set", f"{DAY}:{key}")
        result_sets[place] = (key, contest_id, result_set)
        p.add(
            "result_set",
            id=result_set,
            election_id=row["election_id"],
            contest_id=contest_id,
            stage="official",
            kind="counted",
            status="active",
            source_id=source,
            is_default=contest_id not in held,
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
        independent = [o for o in options[contest_id] if o["party_id"] is None]
        named = columns.get(place, Counter())
        if len(independent) == 1 and len(named) == 1:
            chosen[place] = {next(iter(named)): text(independent[0]["id"])}
            external.add(text(independent[0]["id"]))
        elif named:
            pooled_places.add(place)
            p.add(
                "note",
                id=p.id("note", result_set, "pooled"),
                entity_id=result_set,
                kind="caveat",
                body_md=POOLED,
                lang="en",
            )

    def option_for(place: tuple[int, int, int], column: str, label: str | None) -> str:
        key, contest_id, _ = result_sets[place]
        if column.startswith("bagimsiz"):
            if column in chosen.get(place, {}):
                return chosen[place][column]
            option = p.id("option", f"{DAY}:{key}", "independents")
            if option not in option_rows:
                option_rows[option] = option
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="independents",
                    ballot_label=INDEPENDENTS,
                )
            return option
        assert label is not None
        slug = DATED.get((label, DAY.year), PARTIES[label])
        party = parties[slug]
        found = [o for o in options[contest_id] if o["party_id"] == party]
        if len(found) > 1:
            raise ValueError(f"{key}: {len(found)} options for {slug}")
        if found:
            external.add(text(found[0]["id"]))
            return text(found[0]["id"])
        option = p.id("option", f"{DAY}:{key}", slug)
        if option not in option_rows:
            option_rows[option] = option
            external.add(party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=party,
                ballot_label=party_name[party],
            )
        return option

    values: list[tuple[str, str, str | None, int, int]] = []
    new_units: dict[str, str] = {}
    unit_rows: list[Row] = []
    left_out: list[tuple[int, int, int]] = []
    for row in sorted(counted, key=lambda r: num(r["sandik_ID"])):
        place = place_of(row)
        if place not in result_sets:
            left_out.append(place)
            continue
        _, contest_id, result_set = result_sets[place]
        code = f"{DAY.year}:{num(row['il_ID']):02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"
        if code in units:
            unit = units[code][0]
            external.add(unit)
        else:
            unit = new_units.get(code) or p.id("unit", code)
            if code not in new_units:
                new_units[code] = unit
                area = contest[contest_id]["district_area_id"]
                external.add(area)
                unit_rows.append(
                    {
                        "id": unit,
                        "polity_id": polity,
                        "scheme_code": "tr.sandik",
                        "code": code,
                        "area_relation": "within",
                        "area_id": area,
                        "kind": "standard",
                        "valid_from": DAY,
                        "valid_to": DAY + timedelta(days=1),
                    }
                )
        for measure, column in (
            (REGISTERED, "secmen_SAYISI"),
            (VOTERS, "oy_KULLANAN_SECMEN_SAYISI"),
            (VALID, "gecerli_OY_TOPLAMI"),
            (INVALID, "gecersiz_OY_TOPLAMI"),
        ):
            if row.get(column) is not None:
                values.append((result_set, unit, None, measure, num(row[column])))
        header = labels[num(row["il_ID"])]
        pooled = 0
        for column, value in row.items():
            if not value or not column.endswith("_ALDIGI_OY"):
                continue
            if column.startswith("parti"):
                label = header.get(column) or national_label[column]
                values.append((result_set, unit, option_for(place, column, label), VOTES, num(value)))
            elif column.startswith("bagimsiz") and place in chosen:
                values.append((result_set, unit, option_for(place, column, None), VOTES, num(value)))
            elif column.startswith("bagimsiz"):
                pooled += num(value)
            else:
                raise ValueError(f"unexpected column {column}")
        if place not in chosen:
            total = num(row.get("bagimsiz_TOPLAM_OY") or 0)
            if total != pooled:
                raise ValueError(f"box {row['sandik_ID']}: independents {pooled} against total {total}")
            if total:
                values.append((result_set, unit, option_for(place, "bagimsiz", None), VOTES, total))
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
    p.external |= external
    p.accept(
        "accounting.turnout",
        "In some boxes the service gives more voters than registered voters, by a few votes; the boxes are stored as "
        "the service publishes them.",
    )
    report(p, totals, counted, place_of, result_sets, unlabeled, left_out, pooled_places, chosen)
    return p


def report(
    p: Package,
    totals: list[Row],
    counted: list[Row],
    place_of: object,
    result_sets: dict[tuple[int, int, int], tuple[str, str, str]],
    unlabeled: list[Row],
    left_out: list[tuple[int, int, int]],
    pooled: set[tuple[int, int, int]],
    chosen: dict[tuple[int, int, int], dict[str, str]],
) -> None:
    print(f"{len(result_sets)} contests, {len(counted)} boxes, {len(pooled)} with pooled independents")
    print(
        f"dropped {len(unlabeled)} boxes with unlabeled party columns: {[(r['il_ADI'], r['ilce_ADI'], r['sandik_NO']) for r in unlabeled]}"
    )
    print(f"boxes in places without a contest: {len(left_out)}")


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())
