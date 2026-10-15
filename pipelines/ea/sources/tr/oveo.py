import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr import ysk
from ea.sources.tr.ysk import CANDIDATES, PARTIES, PRESIDENT_CELLS, PROVINCES, ROUND_DAYS, title, upper
from ea.store import DATA, connect

SOURCE = "tr.oveo"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
REPOSITORY = "https://github.com/egeakman/2023-oveo-data"
URL = f"{REPOSITORY}/raw/master/oveo2023.sqlite3"
PUBLISHER = "Oy ve Ötesi Derneği"
TITLE = "2023 ballot-box results transcribed by Oy ve Ötesi volunteers"
NOTE = (
    "Volunteer transcriptions of the signed result forms from Turkey's 2023 elections, mirrored on GitHub. For the "
    "first round of the presidential election, they cover 130,883 of 191,884 domestic ballot boxes, about 76 percent "
    "of domestic valid votes. For the parliamentary election, they cover 98,046 boxes, about 57 percent of domestic "
    "votes for party lists. They record votes for party lists only, not for independent candidates. Coverage is "
    "better in cities. Box numbers are unique within a district. Polling places, districts, and neighborhoods use "
    "identifiers assigned by Oy ve Ötesi."
)
DISTRICTS = {
    "06": {
        1: ["Bala", "Çankaya", "Elmadağ", "Evren", "Gölbaşı", "Haymana", "Mamak", "Polatlı", "Şereflikoçhisar"],
        2: [
            "Akyurt",
            "Altındağ",
            "Çamlıdere",
            "Çubuk",
            "Güdül",
            "Kahramankazan",
            "Kalecik",
            "Keçiören",
            "Kızılcahamam",
            "Pursaklar",
        ],
        3: ["Ayaş", "Beypazarı", "Etimesgut", "Nallıhan", "Sincan", "Yenimahalle"],
    },
    "16": {
        1: ["Büyükorhan", "Karacabey", "Mustafakemalpaşa", "Nilüfer", "Orhaneli", "Osmangazi"],
        2: [
            "Gemlik",
            "Gürsu",
            "Harmancık",
            "İnegöl",
            "İznik",
            "Keles",
            "Kestel",
            "Mudanya",
            "Orhangazi",
            "Yenişehir",
            "Yıldırım",
        ],
    },
    "34": {
        1: [
            "Adalar",
            "Ataşehir",
            "Beykoz",
            "Çekmeköy",
            "Kadıköy",
            "Kartal",
            "Maltepe",
            "Pendik",
            "Sancaktepe",
            "Sultanbeyli",
            "Şile",
            "Tuzla",
            "Ümraniye",
            "Üsküdar",
        ],
        2: [
            "Bayrampaşa",
            "Beşiktaş",
            "Beyoğlu",
            "Esenler",
            "Eyüpsultan",
            "Fatih",
            "Gaziosmanpaşa",
            "Kağıthane",
            "Sarıyer",
            "Sultangazi",
            "Şişli",
            "Zeytinburnu",
        ],
        3: [
            "Arnavutköy",
            "Avcılar",
            "Bağcılar",
            "Bahçelievler",
            "Bakırköy",
            "Başakşehir",
            "Beylikdüzü",
            "Büyükçekmece",
            "Çatalca",
            "Esenyurt",
            "Güngören",
            "Küçükçekmece",
            "Silivri",
        ],
    },
    "35": {
        1: [
            "Balçova",
            "Buca",
            "Çeşme",
            "Gaziemir",
            "Güzelbahçe",
            "Karabağlar",
            "Karaburun",
            "Konak",
            "Menderes",
            "Narlıdere",
            "Seferihisar",
            "Selçuk",
            "Torbalı",
            "Urla",
        ],
        2: [
            "Aliağa",
            "Bayındır",
            "Bayraklı",
            "Bergama",
            "Beydağ",
            "Bornova",
            "Çiğli",
            "Dikili",
            "Foça",
            "Karşıyaka",
            "Kemalpaşa",
            "Kınık",
            "Kiraz",
            "Menemen",
            "Ödemiş",
            "Tire",
        ],
    },
}
DOMESTIC_VALID = PRESIDENT_CELLS[1]["domestic"][3][2]
VALID, VOTES = 4, 1


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".sqlite3")


def parse() -> None:
    db = sqlite3.connect(f"file:{raw.cached(SOURCE, URL).path}?mode=ro", uri=True)
    plates = {upper(label): f"{plate:02}" for plate, label in enumerate(PROVINCES, 1)}
    cities = {id: plates[label] for id, label in db.execute("select id, name from city")}
    if len(cities) != 81:
        raise ValueError(f"{len(cities)} provinces, expected 81")
    if [label for _, label in db.execute("select id, name from cm_candidate order by id")] != [
        upper(label) for label, _ in CANDIDATES
    ]:
        raise ValueError("candidate order differs from the gazette")
    if [label for _, label in db.execute("select id, name from mv_candidate order by id")] != [
        upper(full) for full, *_ in PARTIES
    ]:
        raise ValueError("party order differs from the gazette")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [(id, cities[city], title(label)) for id, city, label in db.execute("select id, city_id, name from district")],
        schema=["id", "plate", "name"],
        orient="row",
    ).write_parquet(PARSED / "ilce.parquet")
    pl.DataFrame(
        db.execute("select id, district_id, name from neighborhood").fetchall(),
        schema=["id", "ilce", "name"],
        orient="row",
    ).with_columns(pl.col("name").map_elements(title, return_dtype=pl.String)).write_parquet(PARSED / "mahalle.parquet")
    pl.DataFrame(
        [
            (id, cities[city], mahalle, title(label))
            for id, city, mahalle, label in db.execute("select id, city_id, neighborhood_id, name from school")
        ],
        schema=["id", "plate", "mahalle", "name"],
        orient="row",
    ).write_parquet(PARSED / "schools.parquet")
    boxes = []
    for city, ilce, mahalle, school, number, *results in db.execute(
        "select city_id, district_id, neighborhood_id, school_id, number, cm_result, mv_result from ballotbox"
    ):
        counts: list[int | None] = []
        for result, size in zip(results, (len(CANDIDATES), len(PARTIES)), strict=True):
            if result is None:
                counts += [None] * (size + 1)
                continue
            data = json.loads(result)
            votes: list[int] = [data["votes"].get(str(i), 0) for i in range(1, size + 1)]
            if sum(votes) != data["total_vote"]:
                raise ValueError(f"box {city}:{ilce}:{number}: votes do not sum to the total")
            counts += [data["total_vote"], *votes]
        boxes.append((cities[city], ilce, number, school, mahalle, *counts))
    frame = pl.DataFrame(
        boxes,
        schema=[
            "plate",
            "ilce",
            "number",
            "school",
            "mahalle",
            "valid",
            *(f"c{i}" for i in range(len(CANDIDATES))),
            "listed",
            *(f"p{i}" for i in range(len(PARTIES))),
        ],
        orient="row",
    )
    if frame.select("plate", "ilce", "number").is_duplicated().any():
        raise ValueError("box numbers repeat within a district")
    frame.write_parquet(PARSED / "boxes.parquet")
    counted = frame.filter(pl.col("valid").is_not_null())
    listed = frame.filter(pl.col("listed").is_not_null())
    print(
        f"boxes: {frame.height}, with presidential results: {counted.height}, "
        f"share: {counted['valid'].sum() / DOMESTIC_VALID:.4f}, with parliamentary results: {listed.height}, "
        f"share of party votes: {listed['listed'].sum() / sum(domestic for *_, domestic in PARTIES):.4f}"
    )


def emit() -> Path:
    ilce = pl.read_parquet(PARSED / "ilce.parquet")
    mahalle = pl.read_parquet(PARSED / "mahalle.parquet")
    schools = pl.read_parquet(PARSED / "schools.parquet")
    boxes = pl.read_parquet(PARSED / "boxes.parquet")
    day = ROUND_DAYS[1]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ballot-box results of Turkey's 2023 presidential first round and parliamentary election, from Oy ve Ötesi",
            polity="TR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher=PUBLISHER,
            title=TITLE,
            url=REPOSITORY,
            license="No license stated.",
            notes=NOTE,
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        turkey = cs.polity("TR")
        election = cs.key("tr.election", str(day))
        contest = cs.key("tr.contest", f"{day}:president")
        options = {
            index: cs.key("tr.option", f"{day}:president:{slug(label)}") for index, (label, _) in enumerate(CANDIDATES)
        }
        if turkey in cs.minted or contest in cs.minted or any(option in cs.minted for option in options.values()):
            raise ValueError("publish tr.ysk before tr.oveo")
        for code, rank, noun, kind in (
            ("tr.ilce", 2, "district", "administrative"),
            ("tr.mahalle", 3, "neighborhood", "administrative"),
            ("tr.okul", 4, "polling place", "precinct"),
            ("tr.sandik", 5, "ballot box", "precinct"),
        ):
            cs.add("scheme", code=code, polity_id=turkey, kind=kind, rank=rank, noun=noun)

        def names(frame: pl.DataFrame, entity: pl.Expr) -> pl.DataFrame:
            return frame.select(
                entity_id=entity, text="name", lang=pl.lit("tr"), kind=pl.lit("official"), is_preferred=pl.lit(True)
            )

        provinces = {f"{plate:02}": str(cs.area("tr.il", f"{plate:02}")) for plate in range(1, len(PROVINCES) + 1)}
        districts = {id: str(cs.key(f"{SOURCE}.ilce", str(id))) for id in ilce["id"]}
        neighborhood = {id: str(cs.key(f"{SOURCE}.mahalle", str(id))) for id in mahalle["id"]}
        for frame, scheme, ids, parent in (
            (ilce, "tr.ilce", districts, pl.col("plate").replace_strict(provinces)),
            (mahalle, "tr.mahalle", neighborhood, pl.col("ilce").replace_strict(districts)),
        ):
            area = pl.col("id").replace_strict(ids)
            cs.frame("area", frame.select(id=area, scheme_code=pl.lit(scheme)))
            cs.frame("area_parent", frame.select(area_id=area, parent_id=parent, valid_from=pl.lit(None, pl.Date)))
            cs.frame("name", names(frame, area))

        school_ids = {
            code: str(cs.unit("tr.okul", code))
            for code in schools.select(code=pl.col("plate") + ":" + pl.col("id").cast(pl.String))["code"]
        }
        box_codes = boxes.select(
            code=pl.col("plate") + ":" + pl.col("ilce").cast(pl.String) + ":" + pl.col("number").cast(pl.String)
        )["code"]
        box_ids = {code: str(cs.unit("tr.sandik", code)) for code in box_codes}
        validity = {"valid_from": pl.lit(day), "valid_to": pl.lit(day + timedelta(days=1))}
        school_unit = (pl.col("plate") + ":" + pl.col("id").cast(pl.String)).replace_strict(school_ids)
        cs.frame(
            "reporting_unit",
            schools.select(
                id=school_unit,
                polity_id=pl.lit(str(turkey)),
                scheme_code=pl.lit("tr.okul"),
                code=pl.col("plate") + ":" + pl.col("id").cast(pl.String),
                parent_id=pl.lit(None, pl.String),
                area_relation=pl.lit("within"),
                area_id=pl.col("mahalle").replace_strict(neighborhood),
                kind=pl.lit("standard"),
                **validity,
            ),
        )
        cs.frame("name", names(schools, school_unit))
        box_unit = (
            pl.col("plate") + ":" + pl.col("ilce").cast(pl.String) + ":" + pl.col("number").cast(pl.String)
        ).replace_strict(box_ids)
        cs.frame(
            "reporting_unit",
            boxes.select(
                id=box_unit,
                polity_id=pl.lit(str(turkey)),
                scheme_code=pl.lit("tr.sandik"),
                code=pl.col("plate") + ":" + pl.col("ilce").cast(pl.String) + ":" + pl.col("number").cast(pl.String),
                parent_id=(pl.col("plate") + ":" + pl.col("school").cast(pl.String)).replace_strict(school_ids),
                area_relation=pl.lit("within"),
                area_id=pl.col("mahalle").replace_strict(neighborhood),
                kind=pl.lit("standard"),
                **validity,
            ),
        )

        named = dict(ilce.select("id", pl.concat_str("plate", "name", separator=":")).iter_rows())
        sorted_into = {
            f"{plate}:{name}": f"{plate}-{number}"
            for plate, groups in DISTRICTS.items()
            for number, names in groups.items()
            for name in names
        }
        if missing := sorted_into.keys() ^ {label for label in named.values() if label[:2] in DISTRICTS}:
            raise ValueError(f"districts not matched to an electoral district: {sorted(missing)}")
        gazette = pl.read_parquet(ysk.PARSED / "district_values.parquet")
        listed = boxes.filter(pl.col("listed").is_not_null()).with_columns(
            code=pl.col("ilce").replace_strict({id: sorted_into.get(label, label[:2]) for id, label in named.items()})
        )
        for (code,), frame in sorted(listed.group_by("code"), key=lambda group: str(group[0][0])):
            contest_key = f"{day}:parliament:{code}"
            seat = cs.key("tr.contest", contest_key)
            printed = gazette.filter(pl.col("code") == code)
            running = set(printed.filter(pl.col("measure") == VOTES)["option"].drop_nulls())
            choices = {
                index: cs.key("tr.option", f"{contest_key}:{slug(full)}")
                for index, (full, *_) in enumerate(PARTIES)
                if full in running
            }
            if seat in cs.minted or any(option in cs.minted for option in choices.values()):
                raise ValueError("publish tr.ysk before tr.oveo")
            listed_votes = printed.filter(
                (pl.col("measure") == VOTES) & pl.col("option").is_in([full for full, *_ in PARTIES])
            )["value"].sum()
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=seat,
                stage="alternative",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(int(frame["listed"].sum()) / int(listed_votes), 4),
            )
            if result_set in cs.minted:
                cs.frame(
                    "result_value",
                    pl.concat(
                        [
                            frame.select(
                                result_set_id=pl.lit(str(result_set)),
                                unit_id=box_unit,
                                option_id=pl.lit(str(option)),
                                channel=pl.lit(0, pl.Int16),
                                measure=pl.lit(VOTES, pl.Int16),
                                count_round=pl.lit(0, pl.Int16),
                                flags=pl.lit(0, pl.Int16),
                                value=f"p{index}",
                            )
                            for index, option in choices.items()
                        ]
                    ),
                )

        counted = boxes.filter(pl.col("valid").is_not_null())
        completeness = round(counted["valid"].sum() / DOMESTIC_VALID, 4)
        result_set = cs.key(f"{SOURCE}.result_set", f"{day}:president")
        cs.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="alternative",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=completeness,
        )
        if result_set in cs.minted:
            values = counted.select(
                unit_id=box_unit,
                option_id=pl.lit(None, pl.String),
                measure=pl.lit(VALID, pl.Int16),
                value="valid",
            )
            for index, option in options.items():
                values = pl.concat(
                    [
                        values,
                        counted.select(
                            unit_id=box_unit,
                            option_id=pl.lit(str(option)),
                            measure=pl.lit(VOTES, pl.Int16),
                            value=f"c{index}",
                        ),
                    ]
                )
            cs.frame(
                "result_value",
                values.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id="unit_id",
                    option_id="option_id",
                    channel=pl.lit(0, pl.Int16),
                    measure="measure",
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="value",
                ),
            )
        return cs.write()
