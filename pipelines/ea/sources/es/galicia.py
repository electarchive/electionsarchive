import html
import re
import subprocess
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "es.galicia"
VERSION = "1"
PUBLISHER = "Xunta de Galicia"
TITLE = "Eleccións ao Parlamento de Galicia: resultados (Xunta de Galicia, datos abertos)"
PORTAL = "https://abertos.xunta.gal/catalogo/administracion-publica"
DOG_PUBLISHER = "Xunta Electoral de Galicia"
DOG_TITLE = "Acordos da Xunta Electoral de Galicia sobre os resultados e os deputados proclamados electos (DOG)"
DOG_PORTAL = "https://www.xunta.gal/diario-oficial-galicia"
STATUTE = "Lei orgánica 1/1981, do 6 de abril, de Estatuto de autonomía de Galicia"
LAW = "Lei 8/1985, do 13 de agosto, de eleccións ao Parlamento de Galicia"
BASE = "https://abertos.xunta.gal/catalogo/administracion-publica/-/dataset/"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 0.05
PROVINCES = {"15": "A Coruña", "27": "Lugo", "32": "Ourense", "36": "Pontevedra"}
FOUR = ("001", "005", "009", "013"), ("003", "007", "011", "015")
ELECTIONS = {
    2001: ("0263", date(2001, 10, 21), (("001",), ())),
    2005: ("0261", date(2005, 6, 19), (("001",), ("003",))),
    2009: ("0260", date(2009, 3, 1), (("001",), ("003",))),
    2012: ("0048", date(2012, 10, 21), (("001",), ("003",))),
    2016: ("0371", date(2016, 9, 25), (("001",), ("003",))),
    2020: ("0426", date(2020, 7, 12), FOUR),
    2024: ("0656", date(2024, 2, 18), FOUR),
}
DOG = {
    2001: ("https://www.xunta.gal/dog/Publicados/2001/20011113/AnuncioF762_es.html", "DOG 219, 13 November 2001"),
    2005: (
        "https://www.juntaelectoralcentral.es/cs/jec/documentos/GALICIA_2005_Resultados.pdf",
        "DOG 134, 13 July 2005",
    ),
    2009: ("https://www.xunta.gal/dog/Publicados/2009/20090320/Anuncio10A4A_es.html", "DOG 55, 20 March 2009"),
    2012: (
        "https://www.xunta.gal/dog/Publicados/2012/20121107/AnuncioG2453-061112-1_es.html",
        "DOG 212, 7 November 2012",
    ),
    2016: (
        "https://www.xunta.gal/dog/Publicados/2016/20161011/AnuncioG2453-101016-0004_es.html",
        "DOG 194, 11 October 2016",
    ),
    2020: (
        "https://www.xunta.gal/dog/Publicados/2020/20200729/AnuncioG2453-280720-0003_gl.html",
        "DOG 151, 29 July 2020",
    ),
    2024: ("https://www.xunta.gal/dog/Publicados/2024/20240306/AnuncioG2453-050324-1_gl.html", "DOG 47, 6 March 2024"),
}
FIXED = {
    "codprovincia": "province",
    "codcir": "province",
    "codmunicipio": "mun",
    "codcon": "mun",
    "municipio": "name",
    "concello": "name",
    "distrito": "district",
    "seccion": "section",
    "mesa": "mesa",
    "censototal": "electors",
    "totalvotos": "voters",
    "votostotais": "voters",
    "votosnulos": "invalid",
    "votosenbranco": "blank",
    "votosbrancos": "blank",
    "votosacandidaturas": "cand",
    "votoscandidaturas": "cand",
    "votosvalidos": "valid",
}
SKIP = {"provincia", "censo", "certifalta", "certifcorrec", "totalabstencion", "abstencion", "votoselectores"}
SKIP |= {"votosinterventores"}
ALIASES = {"esquerdaunidaanova": "age"}
FIELDS = ("electors", "voters", "invalid", "blank", "cand", "valid")


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


def url(year: int, number: str) -> str:
    code = ELECTIONS[year][0]
    return f"{BASE}{code}/eleccions-parlamento-galicia-resultados-{year}/{number}/descarga-directa-ficheiro.csv"


def files(year: int) -> list[tuple[str, str]]:
    concello, mesa = ELECTIONS[year][2]
    return [("concello", number) for number in concello] + [("mesa", number) for number in mesa]


def fetch() -> None:
    for year in ELECTIONS:
        for _, number in files(year):
            raw.fetch(SOURCE, url(year, number), suffix=".csv")
        link = DOG[year][0]
        raw.fetch(SOURCE, link, suffix=".pdf" if link.endswith(".pdf") else ".html")


def number(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    return int(text.removesuffix(",00").replace(".", ""))


def abbreviation(label: str) -> str:
    return label.split("(")[0].strip() or label


def full_name(label: str) -> str | None:
    match = re.match(r"^[^(]+\((.+)\)$", label)
    if match is None:
        return None
    inner = match.group(1)
    if ")(" in inner:
        inner = inner.split(")(", 1)[1]
    return " ".join(inner.split())


def read(year: int, number_: str) -> tuple[list[dict], list[dict]]:
    content = raw.cached(SOURCE, url(year, number_)).path.read_bytes()
    text = content.decode("cp850" if b"C\xa2d" in content[:80] else "latin-1")
    lines = [line.split(";") for line in text.splitlines()]
    header = [fold(cell) for cell in lines[0]]
    columns: dict[str, int] = {}
    last = 0
    for index, key in enumerate(header):
        if key in FIXED and FIXED[key] not in columns:
            columns[FIXED[key]] = index
            last = index
        elif key in SKIP or (key in FIXED and FIXED[key] in columns):
            last = index
        elif not key and index < 6:
            columns.setdefault("mesa", index)
    if "electors" not in columns:
        columns["electors"] = header.index("censo")
    parties = [
        (index, " ".join(lines[0][index].split())) for index in range(last + 1, len(header)) if lines[0][index].strip()
    ]
    units: list[dict] = []
    votes: list[dict] = []
    for cells in lines[1:]:
        cells = [cell.strip() for cell in cells]
        if len(cells) <= last or not cells[columns["province"]].isdigit():
            continue
        province = cells[columns["province"]].zfill(2)
        mun = cells[columns["mun"]].zfill(3)
        unit: dict = {"year": year, "province": province, "mun": mun, "name": cells[columns["name"]]}
        if "district" in columns:
            unit["district"] = cells[columns["district"]].zfill(2)
            unit["section"] = cells[columns["section"]].zfill(3)
            unit["mesa"] = cells[columns["mesa"]]
        elif "mesa" in columns and cells[columns["mesa"]]:
            match = re.fullmatch(r"(\d+)-(\d+)\s*-\s*([A-Z])", cells[columns["mesa"]])
            if match is None:
                raise ValueError(f"{year} {number_}: mesa {cells[columns['mesa']]!r}")
            unit["district"], unit["section"], unit["mesa"] = match[1].zfill(2), match[2].zfill(3), match[3]
        for field in FIELDS:
            unit[field] = number(cells[columns[field]]) if field in columns else None
        unit["column"] = unit["cand"]
        unit["cand"] = sum(number(cells[index]) or 0 for index, _ in parties)
        if unit["valid"] is None and unit["blank"] is not None:
            unit["valid"] = unit["cand"] + unit["blank"]
        if mun.startswith("99") and not any(unit[field] for field in FIELDS):
            continue
        units.append(unit)
        keys = {key: unit[key] for key in ("year", "province", "mun", "district", "section", "mesa") if key in unit}
        for index, label in parties:
            if (value := number(cells[index])) is not None:
                votes.append({**keys, "party": label, "key": fold(abbreviation(label)), "votes": value})
    return units, votes


def dog_lines(year: int) -> list[str]:
    path = raw.cached(SOURCE, DOG[year][0]).path
    if path.suffix == ".pdf":
        parts = []
        for page, left in ((2, 298), (3, 0), (3, 298)):
            parts.append(
                subprocess.run(
                    ["pdftotext", "-f", str(page), "-l", str(page), "-x", str(left), "-y", "0", "-W", "298"]
                    + ["-H", "819", str(path), "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
        text = "\n".join(parts)
    else:
        content = path.read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line for line in lines if line]


def elected(year: int) -> tuple[list[dict], dict[str, str]]:
    lines = dog_lines(year)
    names: dict[str, str] = {}
    for line in lines:
        if match := re.fullmatch(r"(?:– |-)?(?:Po(?:r|lo) (?:el |la )?)?(.+?) \(([^()]+)\):?", line):
            names.setdefault(fold(match[2]), match[1].strip())
    start = next(index for index, line in enumerate(lines) if re.match(r"(?i)relaci[oó]n de (diputad|deputad)", line))
    out: list[dict] = []
    province: str | None = None
    party: str | None = None
    pending = ""
    for line in lines[start + 1 :]:
        if re.match(r"(?i)^(\* )?provincia d", line) or re.match(r"(?i)^• provincia", line):
            province = next(code for code, label in PROVINCES.items() if fold(label.removeprefix("A ")) in fold(line))
            party = None
            continue
        if re.match(r"^(– |-)?Po(r|lo) ", line) or pending:
            pending = f"{pending} {line}".strip()
            if not pending.endswith(":"):
                continue
            match = re.search(r"\(([^()]+)\):$", pending)
            if match is None:
                raise ValueError(f"{year}: list header {pending!r}")
            party, pending = match[1], ""
            continue
        if match := re.fullmatch(r"(\d+)\. ?[–-]?([^\d].+?)\.?", line):
            if province is None or party is None:
                raise ValueError(f"{year}: member {line!r} outside a list")
            name = re.sub(r"^(Don|Doña|D\.|Dña\.) ", "", match[2].strip())
            out.append(
                {"year": year, "province": province, "dog_party": party, "position": int(match[1]), "name": name}
            )
            continue
        if re.match(r"^(A Coruña|Santiago de Compostela), \d", line) or line.startswith("IV."):
            break
    return out, names


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[dict]] = defaultdict(list)
    full: dict[str, str] = {}
    for year in ELECTIONS:
        for kind, number_ in files(year):
            units, votes = read(year, number_)
            tables[kind].extend(units)
            tables[f"{kind}_votes"].extend(votes)
        totals: Counter[tuple[str, str]] = Counter()
        for row in tables["concello_votes"]:
            if row["year"] == year:
                totals[row["province"], row["key"]] += row["votes"]
        tallies: Counter[tuple[str, str]] = Counter()
        for row in tables["mesa_votes"]:
            if row["year"] == year:
                tallies[row["province"], row["key"]] += row["votes"]
        for province, key in sorted(tallies.keys() | totals.keys()):
            if tallies and tallies[province, key] != totals[province, key]:
                tables["mesa_dropped"].append(
                    {
                        "year": year,
                        "province": province,
                        "key": key,
                        "mesa": tallies[province, key],
                        "concello": totals[province, key],
                    }
                )
        dropped = {(row["province"], row["key"]) for row in tables["mesa_dropped"] if row["year"] == year}
        tables["concello_votes"] = [
            row for row in tables["concello_votes"] if row["year"] != year or totals[row["province"], row["key"]] > 0
        ]
        tables["mesa_votes"] = [
            row
            for row in tables["mesa_votes"]
            if row["year"] != year
            or (totals[row["province"], row["key"]] > 0 and (row["province"], row["key"]) not in dropped)
        ]
        kept: Counter[tuple[str, str, str, str, str]] = Counter()
        for row in tables["mesa_votes"]:
            if row["year"] == year:
                kept[row["province"], row["mun"], row["district"], row["section"], row["mesa"]] += row["votes"]
        for row in tables["mesa"]:
            if row["year"] == year:
                row["cand"] = kept[row["province"], row["mun"], row["district"], row["section"], row["mesa"]]
        labels = {row["party"] for row in tables["concello_votes"] if row["year"] == year}
        for label in labels:
            if name := full_name(label):
                full[fold(abbreviation(label))] = name
        members, dog_names = elected(year)
        for key, name in dog_names.items():
            full.setdefault(key, name)
        by_key = {
            (row["province"], row["key"]): row["party"] for row in tables["concello_votes"] if row["year"] == year
        }
        for member in members:
            key = fold(member["dog_party"])
            member["key"] = ALIASES.get(key, key)
            member["party"] = by_key[member["province"], member["key"]]
        tables["elected"].extend(members)
        counts = Counter((row["province"], row["party"]) for row in members)
        place_votes = [row for row in tables["concello_votes"] if row["year"] == year]
        print(year, "seats", sum(counts.values()))
        for province in PROVINCES:
            part: Counter[str] = Counter()
            for row in place_votes:
                if row["province"] == province:
                    part[row["party"]] += row["votes"]
            places = [row for row in tables["concello"] if row["year"] == year and row["province"] == province]
            valid = sum(row["valid"] for row in places)
            cand = sum(row["column"] for row in places)
            seats = sum(count for (code, _), count in counts.items() if code == province)
            won = dhondt(dict(part), valid, seats)
            wrong = sorted(p for p in set(part) if won[p] != counts[province, p])
            mesa = sum(
                row["votes"] for row in tables["mesa_votes"] if row["year"] == year and row["province"] == province
            )
            print(
                year,
                province,
                f"seats={seats}",
                f"voters={sum(row['voters'] for row in places)}",
                f"lists={sum(part.values())} cand={cand}",
                f"mesa={mesa}",
                f"dhondt_differs={wrong}",
            )
    tables["party"] = [{"key": key, "name": name} for key, name in sorted(full.items())]
    for name, rows in tables.items():
        frame = pl.DataFrame(rows, infer_schema_length=None)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


SYSTEM = (
    "Closed party lists in four constituencies, the provinces of A Coruña, Lugo, Ourense, and Pontevedra, which elect "
    "75 members: ten each, and the rest shared by population. Seats go by the D'Hondt method to the lists with at "
    "least 5% of the valid votes in the province, blank ballots counted as valid (" + LAW + "; " + STATUTE + "). "
    "The Xunta Electoral de Galicia publishes the members the provincial boards proclaimed in the Diario Oficial de "
    "Galicia, and those are the declared seats stored here."
)
PROVINCE_METHOD = (
    "Ballot counts and list votes by province as the Xunta Electoral de Galicia published them in its acordo on the "
    "results and the members proclaimed elected ({dog}). The figures are the sums of the Xunta de Galicia's results "
    "by concello, votes of residents abroad (CERA) included, which equal the acordo's voters and votes to candidacies "
    "for every province. Valid ballots here are the votes to candidacies, with blank ballots stored apart. The seats "
    "and members are the acordo's."
)
CANDIDACIES_DIFFER = (
    " The file's column of votes to candidacies adds up to {column:,} in this province, against {lists:,} in its list "
    "columns and in the acordo; the list votes are stored."
)
CONCELLO_METHOD = (
    "List votes and ballot counts by concello from the Xunta de Galicia's open-data results files, with the votes of "
    "residents abroad (CERA) on a separate row of the province. The concello figures add up to {votes:,} list votes, "
    "against {official:,} in the province results."
)
MESA_METHOD = (
    "List votes and ballot counts by polling station (mesa) from the Xunta de Galicia's open-data results files, each "
    "mesa on its census section where the 2025 INE section map has the same code, and otherwise on its concello; the "
    "votes of residents abroad (CERA) are one row per province. The mesa figures add up to {votes:,} list votes, "
    "against {official:,} in the province results{gap}.{inconsistent}"
)
MESA_INCONSISTENT = (
    " In {count:,} mesas the file's ballots cast differ from its valid, null, and blank ballots by up to {most} "
    "ballots; the figures are stored as published."
)
MESA_GAP = ", and the files give no unit for the difference"
MESA_DROPPED = (
    " The mesa file's column for {label} adds up to {mesa:,} votes in this province, against {concello:,} in the "
    "concello file, so its mesa figures are not stored."
)


def emit() -> Path:
    places = pl.read_parquet(PARSED / "concello.parquet")
    place_votes = pl.read_parquet(PARSED / "concello_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesa.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    names = dict(pl.read_parquet(PARSED / "party.parquet").iter_rows())
    dropped = pl.read_parquet(PARSED / "mesa_dropped.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parlamento de Galicia elections of 2001 to 2024 by province, concello, and polling station, with the "
                "members proclaimed elected, from the Xunta de Galicia's results files and the Xunta Electoral de "
                "Galicia's acordos in the Diario Oficial de Galicia"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, galicia = cs.polity("ES"), cs.polity("ES-GA")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Parlamento de Galicia")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Creative Commons Attribution-ShareAlike 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Fonte: Xunta de Galicia, abertos.xunta.gal (CC BY-SA 4.0).",
        )
        dog = cs.source(DOG_PUBLISHER, DOG_TITLE)
        cs.add(
            "source",
            id=dog,
            kind="official_body",
            publisher=DOG_PUBLISHER,
            title=DOG_TITLE,
            url=DOG_PORTAL,
            license=None,
            license_url=None,
            attribution="Fonte: Xunta Electoral de Galicia, Diario Oficial de Galicia.",
        )
        for year in ELECTIONS:
            for _, number_ in files(year):
                cs.artifact(raw.cached(SOURCE, url(year, number_)), source)
            cs.artifact(raw.cached(SOURCE, DOG[year][0]), dog)

        parties: dict[str, UUID] = {}
        for key, label in sorted(place_votes.select("key", "party").unique(subset="key", keep="last").iter_rows()):
            parties[key] = cs.key(f"{SOURCE}.party", key)
            cs.add("party", id=parties[key], kind="party", polity_id=galicia)
            name(parties[key], names.get(key) or abbreviation(label), "es")

        body = cs.key("es.body", "galician-parliament")
        cs.add("body", id=body, polity_id=galicia, kind="unicameral")
        name(body, "Parliament of Galicia")
        name(body, "Parlamento de Galicia", "gl", "native")
        system = cs.key("es.electoral_system", "galician-parliament")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=galicia,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": 5},
            },
            rules_version=1,
        )
        name(system, "Parliament of Galicia closed-list D'Hondt system with a 5% threshold")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
        cs.add("body_size", body_id=body, seats=75, valid_from=ELECTIONS[2001][1], valid_to=None)

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
        for code in PROVINCES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(province: str, mun: str) -> UUID:
            code = province + ("999" if mun.startswith("99") else mun)
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun.startswith("99"):
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

        for year, (_, day, _) in ELECTIONS.items():
            election = cs.key("es.election", f"galicia:{day}")
            cs.add(
                "election", id=election, polity_id=galicia, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{year} Galician regional election")
            name(election, f"Eleccións ao Parlamento de Galicia de {year}", "gl", "native")
            path = f"es/ga/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            stamp = f"{year}{day.month:02d}"
            for province, province_name in PROVINCES.items():
                where = (pl.col("year") == year) & (pl.col("province") == province)
                rows = (
                    place_votes.filter(where)
                    .group_by("party", "key")
                    .agg(pl.col("votes").sum())
                    .sort("votes", "party", descending=[True, False])
                )
                provincial = places.filter(where)
                declared = members.filter(where)
                seats_by_party = Counter(declared["key"].to_list())
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
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, province_name)
                cs.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for entry in rows.iter_rows(named=True):
                    option = options[entry["key"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['key']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[entry["key"]],
                        ballot_label=entry["party"],
                    )
                official = int(rows["votes"].sum())

                def make_set(label: str, default: bool, completeness: float, note: str, origin: UUID) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=origin,
                        is_default=default,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    return result_set

                note = PROVINCE_METHOD.format(dog=DOG[year][1])
                column = int(provincial["column"].fill_null(provincial["cand"]).sum())
                if column != official:
                    note += CANDIDACIES_DIFFER.format(column=column, lists=official)
                province_set = make_set("province", True, 1, note, dog)
                unit = province_units[province]
                total: dict[str, int | None] = {field: int(provincial[field].sum()) for field in FIELDS}
                total["cand"] = official
                accounting(province_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append((str(province_set), str(unit), str(options[entry["key"]]), VOTES, entry["votes"], 0))
                    if seats := seats_by_party.get(entry["key"], 0):
                        cs.add(
                            "outcome",
                            result_set_id=province_set,
                            option_id=options[entry["key"]],
                            kind="won",
                            seats=seats,
                        )
                seen: Counter[str] = Counter()
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['key']}:{fold(member['name'])}"
                    seen[person_key] += 1
                    if seen[person_key] > 1:
                        person_key += f":{seen[person_key]}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    plain = re.sub(r"\s*\([^)]*\)$", "", member["name"])
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, plain, "es")
                    party = parties[member["key"]]
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["key"]],
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
                    cs.add("outcome", result_set_id=province_set, option_id=option, kind="elected")

                vote_rows = place_votes.filter(where)
                got = int(vote_rows["votes"].sum())
                concello_set = make_set(
                    "concello",
                    False,
                    min(got / official, 1),
                    CONCELLO_METHOD.format(votes=got, official=official),
                    source,
                )
                for row in provincial.iter_rows(named=True):
                    abroad = ABROAD if row["mun"].startswith("99") else 0
                    accounting(concello_set, municipality_unit(province, row["mun"]), row, abroad)
                for row in vote_rows.iter_rows(named=True):
                    values.append(
                        (
                            str(concello_set),
                            str(municipality_unit(province, row["mun"])),
                            str(options[row["key"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["mun"].startswith("99") else 0,
                        )
                    )

                units = mesas.filter(where)
                if units.is_empty():
                    continue
                unit_votes = mesa_votes.filter(where)
                got = int(unit_votes["votes"].sum())
                off = units.with_columns(
                    off=pl.col("voters") - pl.col("cand") - pl.col("invalid") - pl.col("blank")
                ).filter(pl.col("off") != 0)
                mesa_set = make_set(
                    "mesa",
                    False,
                    min(got / official, 1),
                    MESA_METHOD.format(
                        votes=got,
                        official=official,
                        gap=MESA_GAP if got != official else "",
                        inconsistent=MESA_INCONSISTENT.format(
                            count=off.height, most=max(abs(value) for value in off["off"])
                        )
                        if off.height
                        else "",
                    )
                    + "".join(
                        MESA_DROPPED.format(
                            label=names.get(row["key"], row["key"]), mesa=row["mesa"], concello=row["concello"]
                        )
                        for row in dropped.filter(where).iter_rows(named=True)
                    ),
                    source,
                )
                mesa_units: dict[tuple[str, str, str, str], tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    abroad = row["mun"].startswith("99")
                    section = f"{province}{row['mun']}{row['district']}{row['section']}"
                    code = f"{stamp}:{section}{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    channel = 0
                    if abroad:
                        parent, area, relation, kind, channel = (
                            municipality_unit(province, row["mun"]),
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
                    values.append((str(mesa_set), str(unit), str(options[row["key"]]), VOTES, row["votes"], channel))

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
