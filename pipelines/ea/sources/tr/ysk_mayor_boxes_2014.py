import json
import zipfile
from datetime import date, datetime, timedelta

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.tr.ysk import INDEPENDENTS, INVALID, LICENSE, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.sources.tr.ysk_meclis import PARTIES, SITE
from ea.store import DATA

SOURCE = "tr.ysk_mayor_boxes_2014"
KEY = f"{SOURCE}/mayor-2014"
INBOX = DATA / "inbox" / SOURCE
# The reference partition downloaded beside the snapshot, or else the folder `ea package reference` publishes into.
REFERENCE = next(
    (folder for folder in (DATA / "reference" / "tr", DATA / "ops" / "reference" / "tr") if folder.exists()),
    DATA / "reference" / "tr",
)
DAY = date(2014, 3, 30)
LATER = date(2019, 3, 31)
ARCHIVE = "browser://20261120-002149-a3a8a6/ysk-tr-local-2009-2014-mayoral-sandik.zip"
FOLDER = "11979/tur2"
TITLE = "Sandık Sonuçları: belediye başkanlığı"
NOTE = (
    "Mayoral results for each ballot box, read from the results service of YSK's Sandık Sonuçları site for the "
    "local elections of March 30, 2014. For each municipality, the service gives each ballot box's registered "
    "voters, voters, valid and invalid votes, and the votes for each party's candidate and the independents' "
    "total. It gives no outcome."
)
METHOD = (
    "Counted for each ballot box by YSK. Boxes are the same boxes that held the council vote, so a box that "
    "already has a unit in the archive keeps it. A party's votes go to its candidate's option, and the votes of "
    "all independent candidates go to one option, because the box rows give them as one total."
)
ANNULLED_CAVEAT = (
    "YSK annulled this race and held it again on June 1, 2014. These are the ballot-box results of the annulled "
    "vote of March 30."
)
# The municipalities whose March 30 mayoral race the archive lacked, as their contest key, then the place the
# results service gives as its province, district, and belde ids. YSK annulled each of these races and held it again
# on June 1, 2014 (tr.ysk_yenileme).
PLACES = {
    "agri": (4, 11, 0),
    "aydin-buharkent": (9, 674, 0),
    "bitlis-guroymak": (13, 691, 0),
    "cankiri-sabanozu": (18, 542, 0),
    "corum-ortakoy-astavul": (19, 449, 4747),
    "erzincan-tercan-cadirkaya": (24, 568, 4895),
    "eskisehir-mahmudiye": (26, 401, 0),
    "kastamonu-catalzeytin": (37, 136, 0),
    "nigde-merkez-baglama": (51, 437, 3626),
    "tokat-yesilyurt": (60, 751, 0),
    "yozgat-sorgun-eymir": (66, 528, 4010),
    "bayburt-aydintepe": (69, 660, 0),
    "yalova": (77, 609, 0),
}
ANNULLED = set(PLACES)
Row = dict[str, object]


def num(value: object) -> int:
    return int(str(value))


def fetch() -> None:
    """Add the 13 municipalities' files from the zip saved from the results service (data/inbox/tr.ysk_mayor_boxes_2014/)
    to the raw cache, one entry for each, keyed by the zip's key and the member's name."""
    index = json.loads((INBOX / "index.json").read_text())
    entry = index[ARCHIVE]
    have = raw.keys(SOURCE)
    with zipfile.ZipFile(INBOX / entry["file"]) as archive:
        members = {}
        for name in archive.namelist():
            if name.startswith(FOLDER) and name.endswith(".json"):
                head = json.loads(archive.read(name))["unit"]
                place = (num(head["il_ID"]), num(head["ilce_ID"]), num(head["belde_ID"]))
                if place in PLACES.values():
                    members[name] = place
        if len(members) != len(PLACES):
            raise ValueError(f"found {len(members)} files for {len(PLACES)} municipalities")
        for name in sorted(members):
            key = f"{ARCHIVE}#{name}"
            if key not in have:
                raw.store(
                    SOURCE,
                    key,
                    archive.read(name),
                    suffix=".json",
                    retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
                    media_type="application/json",
                )


def read() -> dict[tuple[int, int, int], tuple[str, Row, list[Row], dict[str, str]]]:
    out = {}
    for key in sorted(raw.keys(SOURCE)):
        if not key.startswith(f"{ARCHIVE}#"):
            continue
        data = json.loads(raw.cached(SOURCE, key).path.read_text())
        unit = data["unit"]
        place = (num(unit["il_ID"]), num(unit["ilce_ID"]), num(unit["belde_ID"]))
        header = {str(c["column_NAME"]): str(c["ad"]) for c in data["columns"] if c["column_NAME"].startswith("parti")}
        out[place] = (key, unit, data["rows"], header)
    return out


def build() -> Package:
    found = read()
    x = pl.read_parquet(REFERENCE / "external_id.parquet", columns=["entity_id", "scheme", "value"])
    parties = dict(zip(*x.filter(pl.col("scheme") == "tr.party").select("value", "entity_id"), strict=True))
    areas = {
        int(v): e
        for v, e in x.filter(pl.col("scheme") == "tr.ysk.muhtarlik.2014").select("value", "entity_id").iter_rows()
    }
    parent = x.filter((pl.col("scheme") == "tr.election") & (pl.col("value") == f"{DAY}:local"))["entity_id"].item()
    later = {
        value.removeprefix(f"{LATER}:").removesuffix("-mayor"): entity
        for value, entity in x.filter(
            (pl.col("scheme") == "tr.contest")
            & pl.col("value").str.starts_with(f"{LATER}:")
            & pl.col("value").str.ends_with("-mayor")
        )
        .select("value", "entity_id")
        .iter_rows()
    }
    ids = [later[key] for key in PLACES]
    contest = {
        r["id"]: r
        for r in pl.read_parquet(REFERENCE / "contest.parquet").filter(pl.col("id").is_in(ids)).iter_rows(named=True)
    }
    election = {
        r["id"]: r
        for r in pl.read_parquet(REFERENCE / "election.parquet")
        .filter(pl.col("id").is_in([c["election_id"] for c in contest.values()]))
        .iter_rows(named=True)
    }
    party_name = dict(pl.read_parquet(REFERENCE / "party.parquet").select("id", "name").iter_rows())
    units = {
        code: id
        for id, code in pl.read_parquet(REFERENCE / "reporting_unit.parquet")
        .filter(
            (pl.col("scheme_code") == "tr.sandik")
            & pl.col("code").str.starts_with(f"{DAY.year}:")
            & (pl.col("pipeline") != SOURCE)
        )
        .select("id", "code")
        .iter_rows()
    }
    turkey = pl.read_parquet(REFERENCE / "polity.parquet").filter(pl.col("iso_code") == "TR")["id"].item()

    p = Package(
        KEY, SOURCE, "Turkey's 2014 mayoral votes by ballot box for 13 municipalities, from YSK's Sandık Sonuçları"
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
    for key, _, _, _ in found.values():
        p.artifact(raw.cached(SOURCE, key), source)
    external: set[str] = {turkey, "tr.sandik", parent}
    values: list[tuple[str, str, str | None, int, int]] = []
    unit_rows: list[Row] = []
    seen_units: set[str] = set()
    for name, place in PLACES.items():
        _, _, rows, header = found[place]
        c = contest[later[name]]
        e = election[c["election_id"]]
        english = str(c["name"]).removeprefix("Mayor of ")
        path = "tr/" + name.replace("-", "/")
        external |= {c["office_id"], c["district_area_id"], e["polity_id"]}
        contest_key = f"{DAY}:{name}-mayor"
        election_id = p.id("election", contest_key)
        contest_id = p.id("contest", contest_key)
        annulled = name in ANNULLED
        p.add(
            "election",
            id=election_id,
            polity_id=e["polity_id"],
            parent_id=parent,
            kind="local",
            date_from=DAY,
            date_to=DAY,
            status="annulled" if annulled else "held",
        )
        p.name(election_id, f"{DAY.year} {english} mayoral election", "en", "official")
        p.name(election_id, f"{DAY.year} {english} Belediye Başkanlığı Seçimi", "tr", "native", False)
        p.add("slug", path=f"{path}/{DAY}-local", entity_id=election_id, is_current=True)
        p.add("external_id", entity_id=election_id, scheme="tr.election", value=contest_key)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="office",
            office_id=c["office_id"],
            district_area_id=c["district_area_id"],
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest_id, f"Mayor of {english}", "en", "official")
        p.add("slug", path=f"{path}/{DAY}-local/mayor", entity_id=contest_id, is_current=True)
        p.add("external_id", entity_id=contest_id, scheme="tr.contest", value=contest_key)
        result_set = p.id("result_set", contest_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            status="annulled" if annulled else "active",
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
        if annulled:
            p.add(
                "note",
                id=p.id("note", result_set, "annulled"),
                entity_id=result_set,
                kind="caveat",
                body_md=ANNULLED_CAVEAT,
                lang="en",
            )
        options: dict[str, str] = {}

        def option(label: str | None, contest_id: str = contest_id, contest_key: str = contest_key) -> str:
            if label is None:
                slug = "independents"
                party = None
            else:
                slug = PARTIES[label]
                party = parties[slug]
            if slug not in options:
                options[slug] = p.id("option", contest_key, slug)
                if party is None:
                    p.add(
                        "contest_option",
                        id=options[slug],
                        contest_id=contest_id,
                        kind="independents",
                        ballot_label=INDEPENDENTS,
                    )
                else:
                    external.add(party)
                    p.add(
                        "contest_option",
                        id=options[slug],
                        contest_id=contest_id,
                        kind="candidate",
                        party_id=party,
                        ballot_label=party_name[party],
                    )
            return options[slug]

        for row in sorted(rows, key=lambda r: num(r["sandik_ID"])):
            code = f"{DAY.year}:{num(row['il_ID']):02d}:{num(row['ilce_ID'])}:{row['sandik_NO']}"
            if code in seen_units:
                raise ValueError(f"{code} appears twice")
            seen_units.add(code)
            if code in units:
                unit = units[code]
                external.add(unit)
            else:
                unit = p.id("unit", code)
                area = areas.get(num(row.get("muhtarlik_ID") or 0)) if not row.get("cezaevi_ID") else None
                area = area or c["district_area_id"]
                external.add(area)
                unit_rows.append(
                    {
                        "id": unit,
                        "polity_id": turkey,
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
                values.append((result_set, unit, None, measure, num(row.get(column) or 0)))
            counted = 0
            for column, value in row.items():
                if column.startswith("parti") and column.endswith("_ALDIGI_OY") and value:
                    values.append((result_set, unit, option(header[column]), VOTES, num(value)))
                    counted += num(value)
                elif (
                    column.startswith("bagimsiz")
                    and column.endswith("_ALDIGI_OY")
                    and value
                    and column != "bagimsiz1_ALDIGI_OY"
                ):
                    raise ValueError(f"{code}: unexpected column {column}")
            independents = num(row.get("bagimsiz_TOPLAM_OY") or 0)
            if independents:
                values.append((result_set, unit, option(None), VOTES, independents))
                counted += independents
            if counted != num(row.get("gecerli_OY_TOPLAMI") or 0):
                raise ValueError(f"{code}: votes {counted} against valid {row.get('gecerli_OY_TOPLAMI')}")
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
        "In a few boxes the service gives more voters than registered voters, by a few votes; the boxes are stored "
        "as the service publishes them.",
    )
    print(f"{len(PLACES)} contests, {len(seen_units)} boxes, {len(unit_rows)} new units, {len(values)} values")
    return p


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())
