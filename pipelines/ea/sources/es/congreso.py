import os
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import display
from ea.sources.es.infoelectoral import APLIEXTR, PAGE, records
from ea.store import DATA, connect

SOURCE = "es.congreso"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Ministerio del Interior"
TITLE = "Resultados electorales: Congreso de los Diputados (ficheros de datos)"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 0.03
SINGLE = {"51", "52"}
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
TWICE = {"201904": ("April", "abril de "), "201911": ("November", "noviembre de ")}
PARTIES: dict[str, tuple[str | None, set[str], str, str, str | None]] = {
    "psoe": ("Q138198", {"PSOE", "PSOEPROGR"}, "SOCIALISTA OBRERO", "Partido Socialista Obrero Español", None),
    "pp": ("Q185088", {"PP"}, "PARTIDO POPULAR", "Partido Popular", "People's Party"),
    "ucd": ("Q549209", {"UCD"}, "CENTRO DEMOCRATICO", "Unión de Centro Democrático", "Union of the Democratic Centre"),
    "cs": ("Q1393123", {"CS"}, "CIUDADANIA", "Ciudadanos-Partido de la Ciudadanía", "Citizens"),
    "iu": ("Q623740", {"IU"}, "IZQUIERDA UNIDA", "Izquierda Unida", "United Left"),
    "vox": ("Q15630787", {"VOX"}, "VOX", "Vox", None),
    "ciu": ("Q150398", {"CIU"}, "CONVERGENCI", "Convergència i Unió", "Convergence and Union"),
    "ap": ("Q2838243", {"AP"}, "ALIANZA POPULAR", "Alianza Popular", "People's Alliance"),
    "cp": ("Q3393021", {"APPDPPL"}, "COALICION POPULAR", "Coalición Popular", "People's Coalition"),
    "pnv": (
        "Q43093",
        {"EAJPNV", "PNV"},
        "NACIONALISTA VASCO",
        "Euzko Alderdi Jeltzalea-Partido Nacionalista Vasco",
        "Basque Nationalist Party",
    ),
    "cds": ("Q788345", {"CDS"}, "CENTRO DEMOCRATICO", "Centro Democrático y Social", "Democratic and Social Centre"),
    "pce": ("Q478811", {"PCE"}, "COMUNISTA DE ESPANA", "Partido Comunista de España", "Communist Party of Spain"),
    "podemos": ("Q16059622", {"PODEMOS"}, "PODEMOS", "Podemos", None),
    "unidos-podemos": ("Q24039754", {"PODEMOSIUEQUO"}, "UNIDOS PODEMOS", "Unidos Podemos", None),
    "unidas-podemos": ("Q62426028", {"PODEMOSIU"}, "UNIDAS PODEMOS", "Unidas Podemos", None),
    "sumar": ("Q112098527", {"SUMAR"}, "SUMAR", "Sumar", None),
    "erc": (
        "Q150068",
        {"ERC", "ESQUERRA"},
        "ESQUERRA REPUBLICANA",
        "Esquerra Republicana de Catalunya",
        "Republican Left of Catalonia",
    ),
    "erc-catsi": (
        "Q5840472",
        {"ERCCATS", "ERCCATSI"},
        "CATALUNYA SI",
        "Esquerra Republicana de Catalunya-Catalunya Sí",
        "Republican Left of Catalonia–Catalonia Yes",
    ),
    "erc-sobiranistes": (
        "Q62080880",
        {"ERCSOBIRAN", "ERCSOBIRANISTES"},
        "SOBIRANISTES",
        "Esquerra Republicana de Catalunya-Sobiranistes",
        None,
    ),
    "bng": ("Q885433", {"BNG"}, "NACIONALISTA GALEGO", "Bloque Nacionalista Galego", "Galician Nationalist Bloc"),
    "izquierda-plural": ("Q19297905", {"IULV"}, "IZQUIERDA PLURAL", "La Izquierda Plural", "Plural Left"),
    "upyd": (
        "Q1144342",
        {"UPYD"},
        "PROGRESO Y DEMOCRACIA",
        "Unión Progreso y Democracia",
        "Union, Progress and Democracy",
    ),
    "ehbildu": ("Q1029761", {"EHBILDU"}, "BILDU", "Euskal Herria Bildu", None),
    "hb": ("Q2748435", {"HB"}, "HERRI BATASUNA", "Herri Batasuna", None),
    "ecp": ("Q21272208", {"ENCOM", "ECP", "ECPGUANYEM"}, "EN COMU", "En Comú Podem", None),
    "cd": ("Q3393974", {"CD"}, "COALICION DEMOCRATICA", "Coalición Democrática", "Democratic Coalition"),
    "pa": ("Q1278980", {"PA", "PSAPA"}, "ANDALU", "Partido Andalucista", "Andalusian Party"),
    "cc": ("Q1104327", {"CC", "CCA"}, "COALICION CANARIA", "Coalición Canaria", "Canarian Coalition"),
    "dl": ("Q21401515", {"DL"}, "DEMOCRACIA I LLIBERTAT", "Democràcia i Llibertat", "Democracy and Freedom"),
    "ea": ("Q1125061", {"EA"}, "EUSKO ALKARTASUNA", "Eusko Alkartasuna", None),
    "cdc": (
        "Q1129443",
        {"CDC"},
        "CONVERGENCIA DEMOCRATICA",
        "Convergència Democràtica de Catalunya",
        "Democratic Convergence of Catalonia",
    ),
    "uv": ("Q3087523", {"UV"}, "UNIO VALENCIANA", "Unió Valenciana", "Valencian Union"),
    "ee": ("Q2531228", {"EE"}, "EUSKADIKO EZKERRA", "Euskadiko Ezkerra", None),
    "par": ("Q2054574", {"PAR"}, "ARAGONES", "Partido Aragonés", "Aragonese Party"),
    "en-marea": ("Q21526202", {"PODEMOSEN", "PODEMOSENMAREAANOVAEU"}, "EN MAREA", "En Marea", None),
    "amaiur": ("Q452571", {"AMAIUR"}, "AMAIUR", "Amaiur", None),
}
SYSTEM = (
    "Closed party lists in 50 provincial constituencies. Each province elects at least two deputies and the rest of "
    "the 350 seats are shared among the provinces by population; seats go by the D'Hondt method to the lists that win "
    "at least 3% of the valid votes in the province, blank ballots included (Ley Orgánica 5/1985 del Régimen "
    "Electoral General, articles 161 to 163; in 1977 and 1979, Real Decreto-ley 20/1977)."
)
SINGLE_SYSTEM = (
    "Ceuta and Melilla each elect one deputy, and the candidacy with the most votes wins the seat (Ley Orgánica "
    "5/1985 del Régimen Electoral General, article 162.4; in 1977 and 1979, Real Decreto-ley 20/1977)."
)
PROVINCE_METHOD = (
    "List votes, seats, registered voters, blank ballots, and null ballots by province from the Ministry of the "
    "Interior's data files, including the votes of residents abroad (CERA). Valid ballots here are the votes to "
    "candidacies; blank ballots, which Spanish electoral law counts as valid, are stored apart. Registered voters are "
    "the census used in the count. The seats are those the file declares for each list, and each elected deputy is "
    "the one the candidate file marks as elected."
)
NOT_OFFICIAL = (
    " The ministry does not flag this election's province figures as the official count; they are the figures it "
    "publishes."
)
MUNICIPALITY_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots by municipality from the Ministry of the "
    "Interior's data files, without the votes of residents abroad. The municipal figures add up to {votes:,} list "
    "votes, against {official:,} in the province results."
)
MESA_METHOD = (
    "List votes, registered voters, blank ballots, and null ballots by polling station (mesa) from the Ministry of "
    "the Interior's data files, with the votes of residents abroad (CERA) as one unit per province on the abroad "
    "channel. A mesa sits on its census section where the section code is in the 2025 INE section map, otherwise on "
    "its municipality. The mesa figures add up to {votes:,} list votes, against {official:,} in the province results."
)
PRELIMINARY = " The ministry flags these figures as not official."
LISTS_DIFFER = (
    " The list votes of this province add up to {lists:,}, against {candidacies:,} votes to candidacies in its own "
    "totals."
)
DHONDT_DIFFERS = (
    " D'Hondt with a 3% threshold applied to the votes printed here gives a different split from the declared "
    "seats, which are stored as published."
)
SUMS_DIFFER = (
    " In {count} units the list votes add up to {diff:,} {direction} votes than the file's own total of votes to "
    "candidacies; both are stored as printed."
)
RECODED = (
    " The file codes {votes:,} votes in this province's mesas to {coded}, which the province results count for "
    "{listed}; they are stored under {listed}."
)
NO_CENSUS = " The file prints a census of zero for {units}; their registered voters are left out."
OVER = " In {count} units the ballots cast exceed the registered voters, as the file prints them."


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", plain).split())


def party_key(abbrev: str, name: str) -> str:
    short = fold(abbrev).replace(" ", "")
    for key, (_item, aliases, fragment, *_rest) in PARTIES.items():
        if short in aliases and fragment in fold(name):
            return key
    return "n:" + fold(name)


def wanted() -> list[str]:
    chosen = os.environ.get("ES_CONGRESO", "")
    return [stamp for stamp in DAYS if not chosen or stamp in chosen.split(",")]


def fetch() -> None:
    pass


def section_of(field: str) -> str:
    digits = field[: len(field) - len(field.lstrip("0123456789"))]
    return f"{int(digits or 0):03d}{field[len(digits) :]}"


def parse() -> None:
    tables: dict[str, list[dict[str, object]]] = {}

    def keep(table: str, stamp: str, rows: list[dict[str, str | int]]) -> None:
        tables.setdefault(table, []).extend({"stamp": stamp, **row} for row in rows)

    for stamp in DAYS:
        lists = records("02", stamp, "TOTA", "03")
        keep("lists", stamp, [{k: row[k] for k in ("list", "abbrev", "name", "national_head")} for row in lists])
        keep(
            "candidates",
            stamp,
            [
                {
                    k: row[k]
                    for k in (
                        "province",
                        "list",
                        "position",
                        "kind",
                        "given",
                        "surname",
                        "second_surname",
                        "sex",
                        "elected",
                    )
                }
                for row in records("02", stamp, "TOTA", "04")
            ],
        )
        real = [
            row for row in records("02", stamp, "TOTA", "07") if row["community"] != "99" and row["province"] != "99"
        ]
        keep(
            "provinces",
            stamp,
            [
                {
                    k: row[k]
                    for k in ("province", "name", "census", "blank", "invalid", "list_votes", "seats", "official")
                }
                for row in real
            ],
        )
        real = [
            row for row in records("02", stamp, "TOTA", "08") if row["community"] != "99" and row["province"] != "99"
        ]
        keep("province_votes", stamp, [{k: row[k] for k in ("province", "list", "votes", "seats")} for row in real])
        municipal = [row for row in records("02", stamp, "MESA", "05") if row["municipal_district"] == "99"]
        keep(
            "municipalities",
            stamp,
            [
                {
                    "code": f"{row['province']}{row['municipality']}",
                    **{k: row[k] for k in ("census", "blank", "invalid", "list_votes")},
                }
                for row in municipal
            ],
        )
        municipal = [row for row in records("02", stamp, "MESA", "06") if row["municipal_district"] == "99"]
        keep(
            "municipality_votes",
            stamp,
            [
                {"code": f"{row['province']}{row['municipality']}", "list": row["list"], "votes": row["votes"]}
                for row in municipal
            ],
        )

        def mesa_code(row: dict[str, str | int]) -> str:
            if row["municipality"] == "999":
                return f"{row['province']}999{row['municipal_district']}0000{row['mesa']}"
            section = section_of(str(row["section"]))
            return f"{row['province']}{row['municipality']}{row['municipal_district']}{section}{row['mesa']}"

        mesas = [
            row for row in records("02", stamp, "MESA", "09") if row["community"] != "99" and row["province"] != "99"
        ]
        keep(
            "mesas",
            stamp,
            [
                {
                    "code": mesa_code(row),
                    "official": row["official"],
                    **{k: row[k] for k in ("census", "blank", "invalid", "list_votes")},
                }
                for row in mesas
            ],
        )
        votes = [
            row for row in records("02", stamp, "MESA", "10") if row["community"] != "99" and row["province"] != "99"
        ]
        keep(
            "mesa_votes",
            stamp,
            [{"code": mesa_code(row), "list": int(row["list"]) % 900000, "votes": row["votes"]} for row in votes],
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    for table, rows in tables.items():
        pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / f"{table}.parquet")


def dhondt(votes: dict[int, int], valid: int, seats: int) -> Counter[int]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def emit() -> Path:
    stamps = wanted()
    frames = {
        table: pl.read_parquet(PARSED / f"{table}.parquet").filter(pl.col("stamp").is_in(stamps))
        for table in (
            "lists",
            "candidates",
            "provinces",
            "province_votes",
            "municipalities",
            "municipality_votes",
            "mesas",
            "mesa_votes",
        )
    }
    with connect() as conn:
        years = ", ".join(str(DAYS[stamp].year) for stamp in stamps)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                f"Congreso de los Diputados elections ({years}) by province, municipality, and polling station, with "
                "lists, candidates, declared seats, and elected deputies, from the Ministry of the Interior's data files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain = cs.polity("ES")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Congreso")
        congreso = cs.key("es.body", "congreso")
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
        for stamp in stamps:
            for extent in ("TOTA", "MESA"):
                cs.artifact(raw.cached("es.infoelectoral", f"{APLIEXTR}02{stamp}_{extent}.zip"), source)

        systems = {}
        for key, rules, english, body in (
            (
                "congreso",
                {
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
                },
                "Congreso de los Diputados closed-list D'Hondt system",
                SYSTEM,
            ),
            (
                "congreso-single",
                {"formula": "fptp"},
                "Congreso de los Diputados single-seat plurality system",
                SINGLE_SYSTEM,
            ),
        ):
            systems[key] = cs.key("es.electoral_system", key)
            cs.add("electoral_system", id=systems[key], polity_id=spain, rules=rules, rules_version=1)
            name(systems[key], english)
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=body)

        by_item = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x join party p on p.id = x.entity_id
                   where x.scheme = 'wikidata' and p.polity_id = %s
                   and not exists (select from entity_merge m where m.entity_id = p.id)""",
                (spain,),
            ).fetchall()
        )
        existing = {row[0] for row in conn.execute("select id from party where polity_id = %s", (spain,))}
        parties: dict[str, UUID] = {}

        def party_for(abbrev: str, label: str) -> UUID:
            key = party_key(abbrev, label)
            if key in parties:
                return parties[key]
            entry = PARTIES.get(key)
            if entry is not None and entry[0] in by_item:
                party = parties[key] = by_item[entry[0]]
                return party
            party = parties[key] = cs.key("es.party", key)
            if party in existing:
                return party
            cs.add("party", id=party, kind="party", polity_id=spain)
            if entry is None:
                name(party, " ".join(label.split()), "es")
            else:
                item, _aliases, _fragment, native, english = entry
                name(party, native, "es")
                if english is not None:
                    name(party, english, "en", "english")
                cs.add("external_id", entity_id=party, scheme="wikidata", value=item)
            name(party, " ".join(abbrev.split()), "es", "abbreviation")
            existing.add(party)
            return party

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in sorted(set(frames["provinces"]["province"])):
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(code: str) -> UUID:
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                area = municipality_areas.get(code)
                relation = "equals" if area is not None else "unlocated"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[code[:2]], relation, area, "standard")
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
                if measure != REGISTERED or value:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        def over(frame: pl.DataFrame, votes: pl.DataFrame) -> str:
            count = frame.filter(
                (pl.col("census") > 0) & (pl.col("list_votes") + pl.col("blank") + pl.col("invalid") > pl.col("census"))
            ).height
            sums = (
                votes.group_by("code")
                .agg(pl.col("votes").sum())
                .join(frame, on="code")
                .filter(pl.col("votes") != pl.col("list_votes"))
            )
            text = OVER.format(count=count) if count else ""
            if missing := frame.filter(pl.col("census") == 0).height:
                text += NO_CENSUS.format(units=f"{missing:,} unit{'' if missing == 1 else 's'}")
            if sums.height:
                diff = int((sums["votes"] - sums["list_votes"]).sum())
                text += SUMS_DIFFER.format(count=sums.height, diff=abs(diff), direction="more" if diff > 0 else "fewer")
            return text

        names = dict(
            pl.read_parquet(PARSED / "provinces.parquet")
            .filter(pl.col("stamp") == "202307")
            .select("province", "name")
            .iter_rows()
        )
        mismatched, unmatched = [], 0
        for stamp in stamps:
            day = DAYS[stamp]
            lists = {row["list"]: row for row in frames["lists"].filter(pl.col("stamp") == stamp).iter_rows(named=True)}
            election = cs.key("es.election", f"{day}:general")
            cs.add("election", id=election, polity_id=spain, kind="general", date_from=day, date_to=day, status="held")
            english, native = TWICE.get(stamp, ("", ""))
            name(election, f"{english} {day.year} Spanish general election".strip())
            name(election, f"Elecciones generales de {native}{day.year}", "es", "native")
            path = f"es/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            totals = frames["provinces"].filter(pl.col("stamp") == stamp)
            list_votes = frames["province_votes"].filter(pl.col("stamp") == stamp)
            candidates = frames["candidates"].filter(pl.col("stamp") == stamp)
            places = frames["municipalities"].filter(pl.col("stamp") == stamp)
            place_votes = frames["municipality_votes"].filter(pl.col("stamp") == stamp)
            mesas = frames["mesas"].filter(pl.col("stamp") == stamp)
            mesa_votes = frames["mesa_votes"].filter(pl.col("stamp") == stamp)
            for total in totals.sort("province").iter_rows(named=True):
                province = total["province"]
                key = f"{day}:{province}"
                rows = list_votes.filter(pl.col("province") == province).sort("votes", descending=True)
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=congreso,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=total["seats"],
                    electoral_system_id=systems["congreso-single" if province in SINGLE else "congreso"],
                    default_result_kind="counted",
                )
                name(contest, names[province])
                cs.add("slug", path=f"{path}/congress/{slug(names[province])}", entity_id=contest)
                options: dict[int, UUID] = {}
                for entry in rows.iter_rows(named=True):
                    own = lists[entry["list"]]
                    head = lists.get(own["national_head"]) or own
                    option = options[entry["list"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['list']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_for(head["abbrev"], head["name"]),
                        ballot_label=" ".join(own["name"].split()),
                    )
                if int(rows["seats"].sum()) != total["seats"]:
                    raise ValueError(f"{key}: declared seats differ from the province total")
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

                note = PROVINCE_METHOD + ("" if total["official"] == "S" else NOT_OFFICIAL)
                if official != total["list_votes"]:
                    note += LISTS_DIFFER.format(lists=official, candidacies=total["list_votes"])
                won = dhondt(
                    dict(zip(rows["list"], rows["votes"])),
                    0 if province in SINGLE else total["list_votes"] + total["blank"],
                    total["seats"],
                )
                if any(won[code] != seats for code, seats in zip(rows["list"], rows["seats"])):
                    note += DHONDT_DIFFERS
                    mismatched.append(key)
                province_set = make_set("province", True, True, 1, note)
                unit = province_units[province]
                accounting(province_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append((str(province_set), str(unit), str(options[entry["list"]]), VOTES, entry["votes"], 0))
                    if entry["seats"]:
                        cs.add(
                            "outcome",
                            result_set_id=province_set,
                            option_id=options[entry["list"]],
                            kind="won",
                            seats=entry["seats"],
                        )
                elected = Counter()
                for person_row in candidates.filter(pl.col("province") == province).iter_rows(named=True):
                    option = options.get(person_row["list"])
                    if option is None:
                        unmatched += 1
                        continue
                    full = " ".join(
                        part
                        for part in (person_row["given"], person_row["surname"], person_row["second_surname"])
                        if part
                    )
                    tag = f"{key}:{person_row['list']}:{person_row['kind']}{person_row['position']}"
                    person = cs.key(f"{SOURCE}.person", tag)
                    cs.add("person", id=person, gender={"M": "male", "F": "female"}.get(person_row["sex"]))
                    name(person, display(full, "es"))
                    candidacy = cs.key(f"{SOURCE}.candidacy", tag)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="list_member" if person_row["kind"] == "T" else "substitute",
                        list_position=person_row["position"],
                        ballot_name=full,
                        is_independent=False,
                    )
                    if person_row["elected"] == "S":
                        elected[person_row["list"]] += 1
                        cs.add(
                            "outcome",
                            result_set_id=province_set,
                            option_id=option,
                            candidacy_id=candidacy,
                            kind="elected",
                        )
                for code, seats in zip(rows["list"], rows["seats"]):
                    if elected[code] != seats:
                        raise ValueError(f"{key}: list {code} has {elected[code]} elected deputies for {seats} seats")

                town = places.filter(pl.col("code").str.starts_with(province))
                town_votes = place_votes.filter(pl.col("code").str.starts_with(province))
                got = int(town_votes["votes"].sum())
                municipality_set = make_set(
                    "municipality",
                    False,
                    total["official"] == "S",
                    min(got / official, 1),
                    MUNICIPALITY_METHOD.format(votes=got, official=official) + over(town, town_votes),
                )
                for row in town.iter_rows(named=True):
                    accounting(municipality_set, municipality_unit(row["code"]), row)
                for row in town_votes.iter_rows(named=True):
                    if row["list"] not in options:
                        raise ValueError(f"{key}: municipality {row['code']} has votes for list {row['list']}")
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

                units = mesas.filter(pl.col("code").str.starts_with(province))
                if not units.height:
                    continue
                unit_votes = mesa_votes.filter(pl.col("code").str.starts_with(province))
                got = int(unit_votes["votes"].sum())
                note = MESA_METHOD.format(votes=got, official=official) + over(units, unit_votes)
                recoded = {}
                for code in set(unit_votes["list"]) - set(options):
                    head = lists[code]["national_head"]
                    if head not in options:
                        raise ValueError(f"{key}: the mesas have votes for list {code}")
                    recoded[code] = head
                    note += RECODED.format(
                        votes=int(unit_votes.filter(pl.col("list") == code)["votes"].sum()),
                        coded=" ".join(lists[code]["name"].split()),
                        listed=" ".join(lists[head]["name"].split()),
                    )
                is_official = (units["official"] == "S").all()
                if not is_official:
                    note += PRELIMINARY
                mesa_set = make_set("mesa", False, is_official, min(got / official, 1), note)
                mesa_units: dict[str, tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    code = row["code"]
                    unit = cs.unit("es.mesa", f"{stamp}:{code}")
                    channel = 0
                    if code[2:5] == "999":
                        parent, area, relation, kind, channel = (
                            province_units[province],
                            cs.area("es.provincia", province),
                            "within",
                            "aggregate",
                            ABROAD,
                        )
                    else:
                        parent = municipality_unit(code[:5])
                        area = sections.get(code[:10]) or municipality_areas.get(code[:5])
                        relation, kind = ("within" if area is not None else "unlocated"), "standard"
                    if code in mesa_units:
                        raise ValueError(f"{key}: mesa {code} appears twice")
                    unit_rows[unit] = (unit, "es.mesa", f"{stamp}:{code}", parent, relation, area, kind)
                    mesa_units[code] = (unit, channel)
                    accounting(mesa_set, unit, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    unit, channel = mesa_units[row["code"]]
                    option = options[recoded.get(row["list"], row["list"])]
                    values.append((str(mesa_set), str(unit), str(option), VOTES, row["votes"], channel))

        print(f"D'Hondt differs from the declared seats in {len(mismatched)} contests: {mismatched}")
        print(f"{unmatched} candidates on lists with no votes in their province")
        minted = [
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
        ]
        if minted:
            cs.frame(
                "reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String, "area_id": pl.String})
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
