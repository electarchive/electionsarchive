import os
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.es.infoelectoral import APLIEXTR, COUNTS, LAYOUTS, PAGE, archive
from ea.sources.es.infoelectoral import SOURCE as INFOELECTORAL
from ea.store import DATA, connect

SOURCE = "es.senado"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: Senado (ficheros de datos)"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
ISLANDS = {"07", "35", "38"}
DAYS = {
    "202307": date(2023, 7, 23),
    "201911": date(2019, 11, 10),
    "201904": date(2019, 4, 28),
    "201606": date(2016, 6, 26),
    "201512": date(2015, 12, 20),
    "201111": date(2011, 11, 20),
    "200803": date(2008, 3, 9),
    "200403": date(2004, 3, 14),
    "200003": date(2000, 3, 12),
    "199603": date(1996, 3, 3),
    "199306": date(1993, 6, 6),
    "198910": date(1989, 10, 29),
    "198606": date(1986, 6, 22),
    "198210": date(1982, 10, 28),
    "197903": date(1979, 3, 1),
    "197706": date(1977, 6, 15),
}
LIMITED = (
    "Each province on the mainland elects four senators, and each voter may mark up to three candidates; Gran Canaria, "
    "Mallorca, and Tenerife elect three, and each voter may mark up to two. The candidates with the most votes win "
    "(Ley Orgánica 5/1985 del Régimen Electoral General, articles 165 and 166; in 1977 and 1979, Real Decreto-ley "
    "20/1977)."
)
BLOCK = (
    "Ceuta and Melilla each elect two senators, and each voter may mark up to two candidates. The two candidates with "
    "the most votes win (Ley Orgánica 5/1985 del Régimen Electoral General, articles 165 and 166; in 1977 and 1979, "
    "Real Decreto-ley 20/1977)."
)
SINGLE = (
    "Each of the smaller islands or island groups (Menorca, Ibiza-Formentera, Lanzarote, Fuerteventura, La Palma, La "
    "Gomera, and El Hierro) elects one senator, and the candidate with the most votes wins (Ley Orgánica 5/1985 del "
    "Régimen Electoral General, articles 165 and 166; in 1977 and 1979, Real Decreto-ley 20/1977)."
)
DISTRICT_METHOD = (
    "Candidate votes, registered voters, blank ballots, and null ballots by constituency from the Ministry of the "
    "Interior's data files, including the votes of residents abroad (CERA). Each voter may mark several candidates, "
    "so candidate votes add up to more than the ballots. Valid ballots here are the ballots with votes to candidates; "
    "blank ballots, which Spanish electoral law counts as valid, are stored apart. Each elected senator is the one the "
    "candidate file marks as elected."
)
NOT_OFFICIAL = (
    " The ministry does not flag this election's constituency figures as the official count; they are the figures it "
    "publishes."
)
MUNICIPALITY_METHOD = (
    "Candidate votes, registered voters, blank ballots, and null ballots by municipality from the Ministry of the "
    "Interior's data files, without the votes of residents abroad. The municipal figures add up to {votes:,} candidate "
    "votes, against {official:,} in the constituency results."
)
MESA_METHOD = (
    "Candidate votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the Ministry "
    "of the Interior's data files, with the votes of residents abroad (CERA) as one unit per constituency on the "
    "abroad channel. A mesa sits on its census section where the section code is in the 2025 INE section map, "
    "otherwise on its municipality. The mesa figures add up to {votes:,} candidate votes, against {official:,} in the "
    "constituency results."
)
PRELIMINARY = " The ministry flags these figures as not official."
TOP_DIFFERS = (
    " The candidates with the most votes printed here are not the ones the file marks as elected; the declared "
    "senators are stored as published."
)
FLAGS_DIFFER = (
    " The ministry's candidate file marks other candidates as elected than the seat flags in its results file, such "
    "as a substitute who took the seat later; the winners in the results file are stored."
)
OVER = " In {count} units the ballots cast exceed the registered voters, as the file prints them."
TABLES = (
    "lists",
    "congreso_lists",
    "candidates",
    "districts",
    "district_votes",
    "municipalities",
    "municipality_votes",
    "mesas",
    "mesa_votes",
)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", plain).split())


def wanted() -> list[str]:
    chosen = os.environ.get("ES_SENADO", "")
    return [stamp for stamp in DAYS if not chosen or stamp in chosen.split(",")]


def fetch() -> None:
    pass


def section_of(field: str) -> str:
    digits = field[: len(field) - len(field.lstrip("0123456789"))]
    return f"{int(digits or 0):03d}{field[len(digits) :]}"


def read(kind: str, stamp: str, number: str) -> pl.DataFrame:
    held = set(raw.keys(INFOELECTORAL))
    for extent in ("TOTA", "MESA", "MUNI"):
        if f"{APLIEXTR}{kind}{stamp}_{extent}.zip" not in held:
            continue
        with zipfile.ZipFile(archive(kind, stamp, extent)) as bundle:
            name = f"{number}{kind}{stamp[2:]}.DAT"
            if name not in bundle.namelist():
                continue
            lines = [line for line in bundle.read(name).decode("latin-1").split("\n") if line.strip()]
        if lines:
            return pl.DataFrame({"line": lines}).select(
                (
                    pl.col("line").str.slice(start - 1, end - start + 1).str.strip_chars().cast(pl.Int64, strict=False)
                    if field in COUNTS
                    else pl.col("line").str.slice(start - 1, end - start + 1).str.strip_chars()
                )
                .fill_null(0 if field in COUNTS else "")
                .alias(field)
                for field, start, end in LAYOUTS[number]
            )
    return pl.DataFrame()


def mesa_code() -> pl.Expr:
    section = pl.col("section").map_elements(section_of, return_dtype=pl.String)
    return (
        pl.when(pl.col("municipality") == "999")
        .then(pl.concat_str("province", pl.lit("999"), "municipal_district", pl.lit("0000"), "mesa"))
        .otherwise(pl.concat_str("province", "municipality", "municipal_district", section, "mesa"))
        .alias("code")
    )


def parse() -> None:
    tables: dict[str, list[pl.DataFrame]] = {table: [] for table in TABLES}

    def keep(table: str, stamp: str, frame: pl.DataFrame) -> None:
        if frame.height:
            tables[table].append(frame.with_columns(stamp=pl.lit(stamp)))

    real = (pl.col("community") != "99") & (pl.col("province") != "99")
    for stamp in DAYS:
        keep("lists", stamp, read("03", stamp, "03").select("list", "abbrev", "name"))
        keep("congreso_lists", stamp, read("02", stamp, "03").select("list", "abbrev", "name"))
        keep(
            "candidates",
            stamp,
            read("03", stamp, "04").select(
                "province",
                "district",
                pl.col("municipality").cast(pl.Int64).alias("order"),
                "list",
                "position",
                "kind",
                "given",
                "surname",
                "second_surname",
                "sex",
                "elected",
            ),
        )
        frame = read("03", stamp, "07")
        if frame.height:
            keep(
                "districts",
                stamp,
                frame.filter(real).select(
                    "province", "district", "name", "census", "blank", "invalid", "list_votes", "seats", "official"
                ),
            )
        frame = read("03", stamp, "08")
        if frame.height:
            keep(
                "district_votes",
                stamp,
                frame.filter(real).select("province", "district", "list", "votes", "seats"),
            )
        frame = read("03", stamp, "05")
        if frame.height:
            keep(
                "municipalities",
                stamp,
                frame.filter(pl.col("municipal_district") == "99").select(
                    pl.concat_str("province", "municipality").alias("code"),
                    "district",
                    "census",
                    "blank",
                    "invalid",
                    "list_votes",
                ),
            )
        frame = read("03", stamp, "06")
        if frame.height:
            keep(
                "municipality_votes",
                stamp,
                frame.filter(pl.col("municipal_district") == "99").select(
                    pl.concat_str("province", "municipality").alias("code"), "list", "votes"
                ),
            )
        abroad_total = (
            (pl.col("municipality") == "999")
            & pl.col("province").is_in(ISLANDS)
            & (pl.col("municipal_district") == "09")
        )
        frame = read("03", stamp, "09")
        if frame.height:
            keep(
                "mesas",
                stamp,
                frame.filter(real & ~abroad_total).select(
                    mesa_code(), "official", "census", "blank", "invalid", "list_votes"
                ),
            )
        frame = read("03", stamp, "10")
        if frame.height:
            keep(
                "mesa_votes",
                stamp,
                frame.filter(real & ~abroad_total).select(mesa_code(), "list", "votes"),
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    for table, frames in tables.items():
        pl.concat(frames, how="diagonal").write_parquet(PARSED / f"{table}.parquet")


def emit() -> Path:
    stamps = wanted()
    frames = {
        table: pl.read_parquet(PARSED / f"{table}.parquet").filter(pl.col("stamp").is_in(stamps)) for table in TABLES
    }
    with connect() as conn:
        years = ", ".join(str(DAYS[stamp].year) for stamp in stamps)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Senado elections ({years}) by constituency, municipality, and polling station, with candidates and "
                "elected senators, from the Ministry of the Interior's data files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain = cs.polity("ES")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Senado")
        senado = cs.key("es.body", "senado")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Reuse under Ley 37/2007 with the source cited",
            attribution="Origen de los datos: Ministerio del Interior.",
        )
        held = set(raw.keys(INFOELECTORAL))
        for stamp in stamps:
            for extent in ("TOTA", "MESA", "MUNI"):
                url = f"{APLIEXTR}03{stamp}_{extent}.zip"
                if url in held:
                    cs.artifact(raw.cached(INFOELECTORAL, url), source)

        systems = {}
        for key, rules, english, body in (
            ("senado-limited", {"formula": "limited"}, "Senado limited vote", LIMITED),
            ("senado-block", {"formula": "block"}, "Senado block vote in Ceuta and Melilla", BLOCK),
            ("senado-single", {"formula": "fptp"}, "Senado single-seat plurality in the smaller islands", SINGLE),
        ):
            systems[key] = cs.key("es.electoral_system", key)
            cs.add("electoral_system", id=systems[key], polity_id=spain, rules=rules, rules_version=1)
            name(systems[key], english)
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=body)

        municipalities = frames["municipalities"]
        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        island_of = {
            code: f"{code[:2]}{district}"
            for code, district in pl.read_parquet(PARSED / "municipalities.parquet")
            .filter((pl.col("stamp") == "202307") & pl.col("code").str.slice(0, 2).is_in(ISLANDS))
            .select("code", "district")
            .iter_rows()
        }
        island_names = {
            f"{province}{district}": text
            for province, district, text in pl.read_parquet(PARSED / "districts.parquet")
            .filter((pl.col("stamp") == "202307") & pl.col("province").is_in(ISLANDS) & (pl.col("district") != "9"))
            .select("province", "district", "name")
            .iter_rows()
        }
        if conn.execute("select 1 from scheme where code = 'es.isla'").fetchone() is None:
            cs.add(
                "scheme", code="es.isla", polity_id=spain, kind="electoral_district", rank=3, noun="island constituency"
            )
        for code, label in sorted(island_names.items()):
            area = cs.area("es.isla", code)
            if area not in cs.minted:
                continue
            cs.add("area", id=area, scheme_code="es.isla")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("es.provincia", code[:2]), valid_from=None)
            name(area, label, "es")
            members = sorted(town for town, island in island_of.items() if island == code)
            shape = conn.execute(
                """select encode(st_asewkb(st_multi(st_union(g.geom))), 'hex'),
                          encode(st_asewkb(st_pointonsurface(st_union(g.geom))), 'hex'), min(g.source_id::text)
                   from area_code c join area a on a.id = c.area_id and a.scheme_code = 'es.municipio'
                   join area_geometry g on g.area_id = a.id and upper_inf(g.valid)
                   where c.code = any(%s)""",
                (members,),
            ).fetchone()
            if shape is None or shape[0] is None:
                raise ValueError(f"{code}: no municipality shapes")
            geom, point, ine = shape
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=UUID(ine),
            )
            for town in members:
                if town in municipality_areas:
                    cs.add("area_membership", area_id=municipality_areas[town], parent_id=area, valid_from=None)

        congreso_parties: dict[tuple[str, int], UUID] = {}
        for value, party in conn.execute(
            """select x.value, o.party_id from external_id x join contest_option o on o.id = x.entity_id
               where x.scheme = 'es.congreso.option' and o.party_id is not null"""
        ).fetchall():
            day, _province, code = value.split(":")
            congreso_parties[(day, int(code))] = party
        existing = {row[0] for row in conn.execute("select id from party where polity_id = %s", (spain,))}
        minted_parties: dict[str, UUID] = {}

        def party_for(stamp: str, code: int, lists: dict[int, dict], congreso: dict[int, dict]) -> UUID:
            own = lists[code]
            day = str(DAYS[stamp])
            match = None
            other = congreso.get(code)
            if other is not None and (
                fold(other["abbrev"]).startswith(fold(own["abbrev"])) or fold(own["name"]) == fold(other["name"])
            ):
                match = code
            else:
                for other_code, other in congreso.items():
                    if fold(own["name"]) == fold(other["name"]) or (
                        fold(own["abbrev"]) and fold(own["abbrev"]) == fold(other["abbrev"])
                    ):
                        match = other_code
                        break
            if match is not None and (day, match) in congreso_parties:
                return congreso_parties[(day, match)]
            key = "n:" + fold(own["name"])
            if key in minted_parties:
                return minted_parties[key]
            party = minted_parties[key] = cs.key("es.party", key)
            if party not in existing:
                cs.add("party", id=party, kind="party", polity_id=spain)
                name(party, " ".join(own["name"].split()), "es")
                name(party, " ".join(own["abbrev"].split()), "es", "abbreviation")
                existing.add(party)
            return party

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}

        def province_unit(code: str) -> UUID:
            if code not in province_units:
                unit = province_units[code] = cs.unit("es.provincia", code)
                unit_rows[unit] = (
                    unit,
                    "es.provincia",
                    code,
                    None,
                    "equals",
                    cs.area("es.provincia", code),
                    "standard",
                )
            return province_units[code]

        municipality_units: dict[str, UUID] = {}

        def municipality_unit(code: str) -> UUID:
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                area = municipality_areas.get(code)
                relation = "equals" if area is not None else "unlocated"
                unit_rows[unit] = (unit, "es.municipio", code, province_unit(code[:2]), relation, area, "standard")
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int], channel: int = 0) -> None:
            figures = {
                REGISTERED: row["census"],
                BALLOTS: row["list_votes"] + row["blank"] + row["invalid"],
                VALID: row["list_votes"],
                INVALID: row["invalid"],
                BLANK: row["blank"],
            }
            for measure, value in figures.items():
                values.append((str(result_set), str(unit), None, measure, value, channel))

        def over(frame: pl.DataFrame) -> str:
            count = frame.filter(pl.col("list_votes") + pl.col("blank") + pl.col("invalid") > pl.col("census")).height
            return OVER.format(count=count) if count else ""

        mismatched, relabeled, contests, elected_total = [], [], 0, 0
        for stamp in stamps:
            day = DAYS[stamp]
            lists = {row["list"]: row for row in frames["lists"].filter(pl.col("stamp") == stamp).iter_rows(named=True)}
            congreso = {
                row["list"]: row
                for row in frames["congreso_lists"].filter(pl.col("stamp") == stamp).iter_rows(named=True)
            }
            election = cs.key("es.election", f"{day}:general")
            if election in cs.minted:
                raise ValueError(f"publish the Congreso for {day} before the Senado")
            path = f"es/{day}-general"
            districts = frames["districts"].filter(
                (pl.col("stamp") == stamp)
                & pl.when(pl.col("province").is_in(ISLANDS))
                .then(pl.col("district") != "9")
                .otherwise(pl.col("district") == "9")
            )
            district_votes = frames["district_votes"].filter(pl.col("stamp") == stamp)
            candidates = frames["candidates"].filter(pl.col("stamp") == stamp)
            places = municipalities.filter(pl.col("stamp") == stamp).with_columns(
                circ=pl.when(pl.col("code").str.slice(0, 2).is_in(ISLANDS))
                .then(pl.concat_str(pl.col("code").str.slice(0, 2), "district"))
                .otherwise(pl.col("code").str.slice(0, 2))
            )
            circ_of = dict(places.select("code", "circ").iter_rows())
            place_votes = frames["municipality_votes"].filter(pl.col("stamp") == stamp)
            mesas = frames["mesas"].filter(pl.col("stamp") == stamp)
            mesa_votes = frames["mesa_votes"].filter(pl.col("stamp") == stamp)

            def circ_expr() -> pl.Expr:
                province = pl.col("code").str.slice(0, 2)
                return (
                    pl.when(~province.is_in(ISLANDS))
                    .then(province)
                    .when(pl.col("code").str.slice(2, 3) == "999")
                    .then(pl.concat_str(province, pl.col("code").str.slice(6, 1)))
                    .otherwise(pl.col("code").str.slice(0, 5).replace_strict(circ_of, default=None))
                    .alias("circ")
                )

            if mesas.height:
                mesas = mesas.with_columns(circ_expr())
                mesa_votes = mesa_votes.with_columns(circ_expr())
                if mesas["circ"].null_count():
                    raise ValueError(f"{stamp}: mesas in municipalities without a constituency")
            done: set[str] = set()
            for total in districts.sort("province", "district").iter_rows(named=True):
                province, island = total["province"], total["province"] in ISLANDS
                circ = f"{province}{total['district']}" if island else province
                if circ in done:
                    continue
                done.add(circ)
                key = f"{day}:{circ}"
                rows = district_votes.filter(
                    (pl.col("province") == province) & (pl.col("district") == total["district"])
                ).sort("votes", descending=True)
                elected = {code for code, won in zip(rows["list"], rows["seats"]) if won}
                seats = len(elected)
                contest = cs.key(f"{SOURCE}.contest", key)
                area = cs.area("es.isla", circ) if island else cs.area("es.provincia", province)
                system = (
                    "senado-single" if seats == 1 else "senado-block" if province in {"51", "52"} else "senado-limited"
                )
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=senado,
                    district_area_id=area,
                    magnitude=seats,
                    electoral_system_id=systems[system],
                    default_result_kind="counted",
                )
                label = " ".join(total["name"].split())
                name(contest, label)
                cs.add("slug", path=f"{path}/senate/{slug(label)}", entity_id=contest)
                contests += 1
                if island:
                    unit = cs.unit("es.isla", circ)
                    unit_rows[unit] = (unit, "es.isla", circ, province_unit(province), "equals", area, "standard")
                else:
                    unit = province_unit(province)

                people = candidates.filter((pl.col("province") == province) & (pl.col("district") == total["district"]))
                options: dict[int, UUID] = {}
                flagged: set[int] = set()
                for person_row in people.sort("order", "kind", descending=[False, True]).iter_rows(named=True):
                    code = int(f"{province}{total['district']}{person_row['order']:03d}")
                    if code not in options:
                        option = options[code] = cs.key(f"{SOURCE}.option", f"{key}:{person_row['order']}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=party_for(stamp, person_row["list"], lists, congreso),
                            ballot_position=person_row["order"],
                            ballot_label=" ".join(lists[person_row["list"]]["name"].split()),
                        )
                    full = " ".join(
                        part
                        for part in (person_row["given"], person_row["surname"], person_row["second_surname"])
                        if part
                    )
                    tag = f"{key}:{person_row['order']}:{person_row['kind']}{person_row['position']}"
                    person = cs.key(f"{SOURCE}.person", tag)
                    cs.add("person", id=person, gender={"M": "male", "F": "female"}.get(person_row["sex"]))
                    name(person, full)
                    candidacy = cs.key(f"{SOURCE}.candidacy", tag)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=options[code],
                        role="substitute" if person_row["kind"] == "S" else "head",
                        ballot_name=full,
                        is_independent=False,
                    )
                    if person_row["elected"] == "S":
                        flagged.add(code)
                missing = set(rows["list"]) - set(options)
                if missing:
                    raise ValueError(f"{key}: votes for unknown candidates {sorted(missing)}")
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, stage: bool, completeness: float, note: str) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official" if stage else "preliminary",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = DISTRICT_METHOD + ("" if total["official"] == "S" else NOT_OFFICIAL)
                top = set(rows["list"][:seats])
                if top != elected:
                    note += TOP_DIFFERS
                    mismatched.append(key)
                if flagged and flagged != elected:
                    note += FLAGS_DIFFER
                    relabeled.append(key)
                district_set = make_set("district", True, True, 1, note)
                accounting(district_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append((str(district_set), str(unit), str(options[entry["list"]]), VOTES, entry["votes"], 0))
                for code in sorted(elected):
                    cs.add(
                        "outcome",
                        result_set_id=district_set,
                        option_id=options[code],
                        kind="elected",
                        seats=None,
                    )
                elected_total += len(elected)

                town = places.filter(pl.col("circ") == circ)
                if town.height:
                    town_votes = place_votes.filter(pl.col("code").is_in(town["code"]))
                    got = int(town_votes["votes"].sum())
                    municipality_set = make_set(
                        "municipality",
                        False,
                        total["official"] == "S",
                        min(got / official, 1),
                        MUNICIPALITY_METHOD.format(votes=got, official=official) + over(town),
                    )
                    for row in town.iter_rows(named=True):
                        accounting(municipality_set, municipality_unit(row["code"]), row)
                    for row in town_votes.iter_rows(named=True):
                        if row["list"] not in options:
                            raise ValueError(f"{key}: municipality {row['code']} has votes for {row['list']}")
                        values.append(
                            (
                                str(municipality_set),
                                str(municipality_unit(row["code"])),
                                str(options[row["list"]]),
                                VOTES,
                                row["votes"],
                                0,
                            )
                        )

                if not mesas.height:
                    continue
                units = mesas.filter(pl.col("circ") == circ)
                unit_votes = mesa_votes.filter(pl.col("circ") == circ)
                got = int(unit_votes["votes"].sum())
                note = MESA_METHOD.format(votes=got, official=official) + over(units)
                is_official = (units["official"] == "S").all()
                if not is_official:
                    note += PRELIMINARY
                mesa_set = make_set("mesa", False, is_official, min(got / official, 1), note)
                mesa_units: dict[str, tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    code = row["code"]
                    mesa = cs.unit("es.mesa", f"{stamp}:{code}")
                    channel = 0
                    if code[2:5] == "999":
                        parent, place, relation, kind, channel = unit, area, "within", "aggregate", ABROAD
                    else:
                        parent = municipality_unit(code[:5])
                        place = sections.get(code[:10]) or municipality_areas.get(code[:5])
                        relation, kind = ("within" if place is not None else "unlocated"), "standard"
                    if code in mesa_units:
                        raise ValueError(f"{key}: mesa {code} appears twice")
                    unit_rows[mesa] = (mesa, "es.mesa", f"{stamp}:{code}", parent, relation, place, kind)
                    mesa_units[code] = (mesa, channel)
                    accounting(mesa_set, mesa, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    mesa, channel = mesa_units[row["code"]]
                    if row["list"] not in options:
                        raise ValueError(f"{key}: mesa {row['code']} has votes for {row['list']}")
                    values.append((str(mesa_set), str(mesa), str(options[row["list"]]), VOTES, row["votes"], channel))

        print(f"{contests} contests, {elected_total} senators elected")
        print(f"the candidate file's elected flags differ in {len(relabeled)} contests: {relabeled}")
        print(f"the most-voted candidates are not the elected ones in {len(mismatched)} contests: {mismatched}")
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit),
                        "polity_id": str(spain),
                        "scheme_code": scheme,
                        "code": code,
                        "parent_id": None if parent is None else str(parent),
                        "area_relation": relation,
                        "area_id": None if area is None else str(area),
                        "kind": kind,
                    }
                    for unit, scheme, code, parent, relation, area, kind in unit_rows.values()
                    if unit in cs.minted
                ],
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
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
                    "channel": pl.Int16,
                },
                orient="row",
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
