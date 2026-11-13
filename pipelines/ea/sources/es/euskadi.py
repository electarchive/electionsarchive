import csv
import io
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "es.euskadi"
VERSION = "1"
PUBLISHER = "Gobierno Vasco"
TITLE = "Resultados de las elecciones al Parlamento Vasco (euskadi.eus)"
PORTAL = "https://www.euskadi.eus/informacion/descargas-de-ficheros-de-resultados-de-elecciones/web01-a2haukon/es/"
BASE = "https://www.euskadi.eus/contenidos/informacion/w_em_descargas/es_def/adjuntos/csv/"
STATUTE = "Ley Orgánica 3/1979, de 18 de diciembre, de Estatuto de Autonomía para el País Vasco"
LAW = "Ley 5/1990, de 15 de junio, de Elecciones al Parlamento Vasco"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
TERRITORIES = {"01": "Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
NATIVE = {"01": "Araba/Álava", "20": "Gipuzkoa", "48": "Bizkaia"}
ELECTIONS = {
    1980: ("P80_c.zip", date(1980, 3, 9)),
    1984: ("P84_c.zip", date(1984, 2, 26)),
    1986: ("P86_c.zip", date(1986, 11, 30)),
    1990: ("P90_c.zip", date(1990, 10, 28)),
    1994: ("P94_c.zip", date(1994, 10, 23)),
    1998: ("P98_c.zip", date(1998, 10, 25)),
    2001: ("P01_c.zip", date(2001, 5, 13)),
    2005: ("P05_c.zip", date(2005, 4, 17)),
    2009: ("P09_c.zip", date(2009, 3, 1)),
    2012: ("P12_c.zip", date(2012, 10, 21)),
    2016: ("P16_c.zip", date(2016, 9, 25)),
    2020: ("P20_c.zip", date(2020, 7, 12)),
    2024: ("Cas-csv.zip", date(2024, 4, 21)),
}
FIXED = {
    "th": "th",
    "ambito": "name",
    "municipio": "name",
    "codmunicipio": "mun",
    "codcomarca": "comarca",
    "distrito": "district",
    "seccion": "section",
    "mesa": "mesa",
    "nota": "note",
    "censo": "electors",
    "votantes": "voters",
    "votos": "voters",
    "nulos": "invalid",
    "validos": "valid",
    "blancos": "blank",
    "votoscand": "cand",
    "votoscandidatura": "cand",
    "votoscandidaturas": "cand",
    "abstenciones": "abstentions",
    "abstencion": "abstentions",
}
FIELDS = ("electors", "voters", "invalid", "blank", "cand")
Row = dict[str, str]


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


def territory(text: str) -> str:
    key = fold(text)
    if key.startswith(("araba", "alava")):
        return "01"
    if key.startswith("gipuzkoa"):
        return "20"
    if key.startswith("bizkaia"):
        return "48"
    raise ValueError(f"unknown territory {text!r}")


def url(year: int) -> str:
    return BASE + quote(ELECTIONS[year][0])


def fetch() -> None:
    for year in ELECTIONS:
        raw.fetch(SOURCE, url(year), suffix=".zip")


def sheets(year: int) -> dict[str, list[list[str]]]:
    found: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, url(year)).path) as archive:
        for entry in archive.namelist():
            stem = entry.rsplit("/", 1)[-1].lower()
            if not stem.endswith(".csv"):
                continue
            kind = next(kind for kind in ("cir", "elec", "ele", "mes", "mun") if stem.startswith(kind))
            text = archive.read(entry).decode("latin-1")
            delimiter = ";" if text.count(";") > text.count(",") else ","
            found["elec" if kind == "ele" else kind] = [
                [cell.strip() for cell in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)
            ]
    return found


def results(rows: list[list[str]]) -> list[Row]:
    start = next(index for index, row in enumerate(rows) if "censo" in map(fold, row))
    header = [fold(cell) for cell in rows[start]]
    above = rows[start - 1] if start else []
    last = max(index for index, key in enumerate(header) if key in FIXED and FIXED[key] == "abstentions")
    groups: list[tuple[str, int, int]] = []
    column = last + 1
    width = len(header)
    while column < width:
        named = column < len(above) and above[column].strip()
        label = above[column].strip() if named else rows[start][column]
        if not label:
            column += 1
            continue
        size = 1
        if named:
            while column + size < len(above) and above[column + size].strip() == label:
                size += 1
        groups.append((label, column, size))
        column += size
    out: list[Row] = []
    for raw_row in rows[start + 1 :]:
        if len(raw_row) < last or not any(raw_row):
            continue
        row: Row = {}
        for index, key in enumerate(header[: last + 1]):
            if key in FIXED:
                row[FIXED[key]] = raw_row[index].strip()
        if not row.get("th") and (row.get("name", "").upper().startswith("TOTAL") or not row.get("name")):
            continue
        votes: dict[str, str] = {}
        for label, column, size in groups:
            if column < len(raw_row) and raw_row[column].strip():
                votes[label] = raw_row[column].strip()
        row["votes"] = ";".join(f"{label}\t{value}" for label, value in votes.items())
        out.append(row)
    return out


def number(text: str) -> int | None:
    return int(text.replace(".", "")) if text.strip() else None


def tidy(rows: list[Row], year: int, keys: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    units: list[dict] = []
    votes: list[dict] = []
    for row in rows:
        if "th" in row and not row["th"]:
            continue
        unit = {"year": year}
        if "th" in row:
            unit["th"] = territory(row["th"])
        else:
            unit["th"] = territory(row["name"])
        for key in keys:
            if key == "mun":
                unit[key] = row[key].zfill(3)
            elif key == "district":
                unit[key] = row[key].zfill(2)
            elif key == "section":
                unit[key] = row[key].zfill(3)
            else:
                unit[key] = row[key]
        for field in FIELDS:
            unit[field] = number(row[field]) if field in row else None
        units.append(unit)
        for pair in row["votes"].split(";"):
            label, value = pair.split("\t")
            votes.append({**{key: unit[key] for key in ("year", "th", *keys)}, "party": label, "votes": int(value)})
    return units, votes


def elected(rows: list[list[str]], year: int) -> list[dict]:
    start = next(index for index, row in enumerate(rows) if "partido" in map(fold, row))
    header = [fold(cell) for cell in rows[start]]
    th, party = header.index("th"), header.index("partido")
    out = []
    for row in rows[start + 1 :]:
        if len(row) <= max(th, party) + 1 or not row[th]:
            continue
        name = ", ".join(part.strip() for part in row[-1].strip("'\" ").split(","))
        out.append({"year": year, "th": territory(row[th]), "party": row[party], "name": name})
    return out


def threshold(year: int) -> float:
    return 0.05 if 1984 <= year <= 2001 else 0.03


def dhondt(votes: dict[str, int], valid: int, seats: int, share: float) -> Counter[str]:
    eligible = {code: count for code, count in votes.items() if count >= share * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = defaultdict(list)
    for year in ELECTIONS:
        files = sheets(year)
        cir = results(files["cir"])
        mun = results(files["mun"])
        mes = results(files["mes"])
        for name, rows, keys in (
            ("terr", cir, ()),
            ("mun", mun, ("mun", "name")),
            ("mesa", mes, ("mun", "district", "section", "mesa", "name")),
        ):
            units, votes = tidy(rows, year, keys)
            tables[name].extend(units)
            tables[f"{name}_votes"].extend(votes)
        members = elected(files["elec"], year)
        ran = {row["party"] for row in tables["terr_votes"] if row["year"] == year}
        for member in members:
            if member["party"] not in ran:
                (member["party"],) = [p for p in ran if p.startswith(member["party"]) or member["party"].startswith(p)]
        tables["elected"].extend(members)
        counts = Counter((row["th"], row["party"]) for row in members)
        terr_votes = [row for row in tables["terr_votes"] if row["year"] == year]
        for th in TERRITORIES:
            totals = next(row for row in tables["terr"] if row["year"] == year and row["th"] == th)
            part = {row["party"]: row["votes"] for row in terr_votes if row["th"] == th}
            seats = sum(count for (code, _), count in counts.items() if code == th)
            won = dhondt(part, totals["cand"] + totals["blank"], seats, threshold(year))
            wrong = {p for p in set(part) | {p for (c, p) in counts if c == th} if won[p] != counts[th, p]}
            unknown = {p for (c, p) in counts if c == th} - set(part)
            print(
                year,
                th,
                f"seats={seats}",
                f"lists={sum(part.values()) == totals['cand']}",
                f"dhondt_differs={sorted(wrong)}",
                f"unknown={sorted(unknown)}",
            )
        for name in ("mun", "mesa"):
            for th in TERRITORIES:
                got = sum(r["votes"] for r in tables[f"{name}_votes"] if r["year"] == year and r["th"] == th)
                official = sum(r["votes"] for r in terr_votes if r["th"] == th)
                print(year, name, th, got, official, got - official)
    for name, rows in tables.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


FIRST_ELECTION_NOTE = (
    "The first Parliament of the autonomous community had 60 seats, 20 for each historic territory. From 1984 each "
    "territory elects 25, 75 in all, as the declared seats show (" + STATUTE + ")."
)
SYSTEM = (
    "Closed party lists in three constituencies, the historic territories of Álava, Gipuzkoa, and Bizkaia, which share "
    "the seats equally. Seats go by the D'Hondt method to the lists with at least {share}% of the valid votes in the "
    "territory, and blank ballots count as valid votes. The threshold is the one that reproduces the declared seats of "
    "every territory in the elections it covers; the publisher's files do not state it. The Basque Government "
    "publishes the elected members of each list, and those are the declared seats stored here ("
    + STATUTE
    + "; "
    + LAW
    + ")."
)
TERRITORY_METHOD = (
    "List votes, seats, registered voters, ballots cast, blank ballots, and null ballots by historic territory from the "
    "Basque Government's results files. Valid ballots here are the votes to candidacies, with blank ballots stored "
    "apart. The seats are the number of members the Elec file names for each list."
)
MUNICIPALITY_METHOD = (
    "List votes and ballot counts by municipality from the Basque Government's results files, with votes cast abroad "
    "on a separate row of the territory where the file prints one. The municipal figures add up to {votes:,} list "
    "votes, against {official:,} in the territory results."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Basque Government's results files, each mesa on "
    "its census section where the 2025 INE section map has the same code, and otherwise on its municipality. The mesa "
    "figures add up to {votes:,} list votes, against {official:,} in the territory results, and the files give no "
    "unit for the difference."
)
LISTS_DIFFER = (
    " In the source, the list votes of this territory add up to {lists:,}, against {candidacies:,} votes to "
    "candidacies in its own totals."
)
ABROAD = 4


def clean(text: str) -> str:
    return " ".join(text.split())


def emit() -> Path:
    terr = pl.read_parquet(PARSED / "terr.parquet")
    terr_votes = pl.read_parquet(PARSED / "terr_votes.parquet")
    muns = pl.read_parquet(PARSED / "mun.parquet")
    mun_votes = pl.read_parquet(PARSED / "mun_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesa.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parlamento Vasco elections of 1980 to 2024 by historic territory, municipality, and polling station, "
                "with the elected members, from the Basque Government's results files"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, basque = cs.polity("ES"), cs.polity("ES-PV")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Parlamento Vasco")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Gobierno Vasco, euskadi.eus.",
        )
        for year in ELECTIONS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)

        parties: dict[str, UUID] = {}
        for label in sorted(terr_votes["party"].unique()):
            parties[label] = cs.key(f"{SOURCE}.party", label)
            cs.add("party", id=parties[label], kind="party", polity_id=basque)
            name(parties[label], label, "es")

        body = cs.key("es.body", "basque-parliament")
        cs.add("body", id=body, polity_id=basque, kind="unicameral")
        name(body, "Basque Parliament")
        name(body, "Parlamento Vasco", "es", "native")
        name(body, "Eusko Legebiltzarra", "eu", "native")
        systems: dict[float, UUID] = {}
        for share in sorted({threshold(year) for year in ELECTIONS}):
            percent = int(share * 100)
            system = systems[share] = cs.key("es.electoral_system", f"basque-parliament-{percent}")
            cs.add(
                "electoral_system",
                id=system,
                polity_id=basque,
                rules={
                    "formula": "dhondt",
                    "lists": {"order": "closed", "vacancy": "next_on_list"},
                    "threshold": {"level": "list", "scope": "contest", "percent": percent},
                },
                rules_version=1,
            )
            name(system, f"Basque Parliament closed-list D'Hondt system with a {percent}% threshold")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM.format(share=percent))

        sizes = members.group_by("year").agg(seats=pl.len()).sort("year")
        previous: tuple[date, int] | None = None
        for year, seats in sizes.iter_rows():
            day = ELECTIONS[year][1]
            if previous is not None and previous[1] != seats:
                cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=day)
                previous = None
            if previous is None:
                previous = (day, seats)
        if previous is not None:
            cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=None)

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        section_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in TERRITORIES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(province: str, mun: str) -> UUID:
            code = province + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun == "999":
                    area, relation, kind = cs.area("es.provincia", province), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[province], relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None], channel: int = 0) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "cand"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        for year, (_, day) in ELECTIONS.items():
            election = cs.key("es.election", f"basque:{day}")
            cs.add("election", id=election, polity_id=basque, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Basque regional election")
            name(election, f"Elecciones al Parlamento Vasco de {year}", "es", "native")
            path = f"es/pv/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            if year == 1980:
                cs.add("note", entity_id=election, kind="context", body_md=FIRST_ELECTION_NOTE)
            stamp = f"{year}{day.month:02d}"
            year_filter = pl.col("year") == year
            for province, province_name in TERRITORIES.items():
                where = year_filter & (pl.col("th") == province)
                total = terr.filter(where).row(0, named=True)
                rows = terr_votes.filter(where).sort("votes", descending=True)
                declared = members.filter(where)
                seats_by_party = Counter(declared["party"].to_list())
                key = f"{day}:{province}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=declared.height,
                    electoral_system_id=systems[threshold(year)],
                    default_result_kind="counted",
                )
                name(contest, province_name)
                if NATIVE[province] != province_name:
                    name(contest, NATIVE[province], "es", "native")
                cs.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for entry in rows.iter_rows(named=True):
                    option = options[entry["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[entry["party"]],
                        ballot_label=entry["party"],
                    )
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, completeness: float, note: str) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = TERRITORY_METHOD
                if official != total["cand"]:
                    note += LISTS_DIFFER.format(lists=official, candidacies=total["cand"])
                territory_set = make_set("territory", True, 1, note)
                unit = province_units[province]
                accounting(territory_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append(
                        (str(territory_set), str(unit), str(options[entry["party"]]), VOTES, entry["votes"], 0)
                    )
                    if seats := seats_by_party.get(entry["party"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=territory_set,
                            option_id=options[entry["party"]],
                            kind="won",
                            seats=seats,
                        )
                seen: Counter[str] = Counter()
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['party']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, member["name"], "es")
                    party = parties[member["party"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["party"]],
                        party_id=party,
                        ballot_label=member["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", person_key),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=member["name"],
                        member_of_party_id=party,
                        is_independent=False,
                    )
                    cs.add("outcome", result_set_id=territory_set, option_id=option, kind="elected")

                places = muns.filter(where)
                place_votes = mun_votes.filter(where)
                got = int(place_votes["votes"].sum())
                municipality_set = make_set(
                    "municipality",
                    False,
                    min(got / official, 1),
                    MUNICIPALITY_METHOD.format(votes=got, official=official),
                )
                for row in places.iter_rows(named=True):
                    accounting(
                        municipality_set,
                        municipality_unit(province, row["mun"]),
                        row,
                        ABROAD if row["mun"] == "999" else 0,
                    )
                for row in place_votes.iter_rows(named=True):
                    values.append(
                        (
                            str(municipality_set),
                            str(municipality_unit(province, row["mun"])),
                            str(options[row["party"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["mun"] == "999" else 0,
                        )
                    )

                units = mesas.filter(where)
                unit_votes = mesa_votes.filter(where)
                got = int(unit_votes["votes"].sum())
                mesa_set = make_set(
                    "mesa", False, min(got / official, 1), MESA_METHOD.format(votes=got, official=official)
                )
                mesa_units: dict[tuple[str, str, str, str], tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    section = f"{province}{row['mun']}{row['district']}{row['section']}"
                    code = f"{stamp}:{section}{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    channel = 0
                    if row["mun"] == "999":
                        parent, area, relation, kind, channel = (
                            province_units[province],
                            cs.area("es.provincia", province),
                            "within",
                            "aggregate",
                            ABROAD,
                        )
                    else:
                        parent = municipality_unit(province, row["mun"])
                        area = (section_areas.get(section) if year >= 2020 else None) or municipality_areas.get(
                            province + row["mun"]
                        )
                        relation, kind = ("within" if area is not None else "unlocated"), "standard"
                    unit_rows[unit] = (unit, "es.mesa", code, parent, relation, area, kind)
                    mesa_units[row["mun"], row["district"], row["section"], row["mesa"]] = (unit, channel)
                    accounting(mesa_set, unit, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    unit, channel = mesa_units[row["mun"], row["district"], row["section"], row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["party"]]), VOTES, row["votes"], channel))

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
