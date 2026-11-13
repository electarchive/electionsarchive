import io
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "es.cat"
VERSION = "4"
API = "https://resultats.dadeselectorals.gencat.cat/webelec/api"
PUBLISHER = "Generalitat de Catalunya"
TITLE = "Resultats electorals: eleccions al Parlament de Catalunya (Departament de la Presidència)"
PORTAL = "https://resultats.dadeselectorals.gencat.cat/"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 0.03
PROVINCES = {"08": "Barcelona", "17": "Girona", "25": "Lleida", "43": "Tarragona"}
MUNICIPALITY_ONLY = {1980, 1984}
LEVELS = ("PR", "MU", "ME")
SPLIT = {"SI", "PED"}
WIKIDATA = {
    "PP": "Q185088",
    "CS": "Q1393123",
    "CIU": "Q150398",
    "VOX": "Q15630787",
    "PACMA": "Q82558",
    "ERC": "Q150068",
}
DOGC = "https://portaldogc.gencat.cat/utilsEADOP/AppJava/PdfProviderServlet?documentId={}&type=01&language=ca_ES"
DOGC_TITLE = "Diari Oficial de la Generalitat de Catalunya: proclamations of members elected to the Parlament"
PROCLAIMED: dict[tuple[str, str], tuple[tuple[int, ...], dict[str, list[str]]]] = {
    ("A19841", "17"): (
        (234980, 233638),
        {
            "CIU": [
                "Arcadi Calzada i Salavedra",
                "Concepció Ferrer i Casals",
                "Pompeu Pascual i Busquets",
                "Ricard Masó i Llunes",
                "Jordi Martínez i Planas",
                "Trinitat Neras i Plaja",
                "Josep Moliner i Florensa",
                "Josep Oriol Suñer i Carbó",
                "Josep Montalat i Cufí",
                "Federic Martínez Ibáñez",
                "Pere Casals i Lezcano",
            ]
        },
    ),
    ("A19881", "17"): (
        (25962, 52426),
        {
            "CIU": [
                "Arcadi Calzada i Salavedra",
                "Ricard Masó i Llunes",
                "Josep Oriol Suñer i Carbó",
                "Jordi Martínez i Planas",
                "Trinitat Neras i Plaja",
                "Josep Moliner i Florensa",
                "Josep Montalat i Cufí",
                "Marta Gràcia Bosch i Agustí",
                "Frederic Martínez i Ibañez",
                "Pere Casals i Lezcano",
                "Josep Coll i Bertran",
            ],
            "PSC": [
                "Joaquim Nadal i Farreras",
                "Lluís Medir i Huerta",
                "Manel Nadal i Farreras",
                "Daniel Terradellas i Redon",
                "Arseni Gibert i Bosch",
            ],
            "ERC": ["Marçal Casanovas i Guerri"],
        },
    ),
    ("A19881", "43"): (
        (25961,),
        {
            "PSC": [
                "Joan Reventós i Carner",
                "Joan M. Abelló i Alfonso",
                "Ramon Aleu i Jornet",
                "Martí Carnicer i Vidal",
                "Dionisio Garcia i Guillamon",
            ],
            "CIU": [
                "Josep Laporte i Salas",
                "Josep Sendra i Navarro",
                "Joan-Josep Martí i Ferré",
                "Enric Vendrell i Duran",
                "Joan Descals i Esquius",
                "Vicenç de Paul Gonzàlez Castells",
                "Joan-Antoni Mur i Borràs",
                "Josep-Joan Grau i Folch",
                "Miquel-Àngel Marimon i Fort",
                "Francesc Vernet i Mateu",
            ],
            "AP": ["José Curto Casado"],
            "IC": ["Víctor Gimeno Sanz"],
            "ERC": ["Joan Manel Sabanza i March"],
        },
    ),
    ("A19881", "25"): (
        (25967,),
        {
            "CIU": [
                "Jaume Manel Oronich i Miravet",
                "Josep Borràs i Gené",
                "Teresa Ribes i Utge",
                "Francesc Guasch i Fernàndez",
                "Miquel Padilla i Díaz",
                "Josep M. Graells i Forcada",
                "Jaume Aligué i Escudé",
                "Francesc Xavier Bada i Amatller",
                "Maria Pilar Busquets i Medan",
            ],
            "PSC": [
                "Antoni Siurana i Zaragoza",
                "Pere Ayguadé i Ayguadé",
                "Joan Ganyet i Solé",
                "Maria Teresa Utges i Nogues",
            ],
            "AP": ["Víctor M. Colomé Farré"],
        },
    ),
    ("A19951", "17"): (
        (136167,),
        {
            "PSC": [
                "Manel Nadal i Farreras",
                "Daniel Terradellas i Redon",
                "Martí Sans i Pairuto",
                "Pere Jordi Piella i Vilaregut",
                "Marina Geli i Fàbrega",
            ]
        },
    ),
    ("A20031", "08"): (
        (314360,),
        {
            "PP": [
                "Josep Piqué i Camps",
                "Dolors Nadal i Aymerich",
                "M. Belén Pajares Ribas",
                "Francesc Vendrell i Bayona",
                "M. Dolors Montserrat i Culleré",
                "Daniel Sirera i Bellés",
                "Josep Llobet Navarro",
                "Eva García Rodríguez",
                "M. Angeles Olano García",
                "Rafael López Rueda",
                "Santiago Rodríguez Serra",
            ]
        },
    ),
    ("A20031", "43"): (
        (314362,),
        {"ERC": ["Ernest Benach i Pascual", "Marta Cid i Pañella", "Sergi de los Ríos i Martínez"]},
    ),
    ("A20061", "17"): (
        (433711,),
        {"ERC": ["Pere Vigo i Sallent", "Maria Mercè Roca i Perich", "Carme Capdevila i Palau", "Pere Bosch i Cuenca"]},
    ),
    ("A20061", "25"): ((433709,), {"PSCCPC": ["Joaquim Llena Cortina", "Agnès Pardell Vea", "Marta Camps Torrens"]}),
    ("A20061", "08"): (
        (399805,),
        {
            "CIU": [
                "Artur Mas i Gavarró",
                "Núria De Gispert i Català",
                "Felip Puig i Godes",
                "Meritxell Borràs i Solé",
                "Ramon Espadaler i Parcerisas",
                "Lluís Corominas i Diaz",
                "Antoni Castellà i Clavé",
                "Irene Rigau i Oliver",
                "Antoni Fernàndez i Teixidó",
                "Maria Pilar Pifarré i Matas",
                "Ramon Camp i Batalla",
                "Marta Llorens i Garcia",
                "Oriol Pujol i Ferrusola",
                "Francesc Homs i Molist",
                "Ma. Glòria Renom i Vallbona",
                "Josep Rull i Andreu",
                "Joana Ortega i Alemany",
                "Josep Lluís Cleries i Gonzàlez",
                "Anna Figueras i Ibañez",
                "Joan Recasens i Guinot",
                "Jordi Turull i Negre",
                "Assumpció Laïlla i Jou",
                "Joan Raventós i Pujadó",
                "Ma. Rosa Fortuny i Torroella",
                "Esteve Orriols i Sendra",
                "Joan Morell i Comas",
                "Benet Maimí i Pou",
            ],
            "PP": [
                "Josep Piqué i Camps",
                "Montserrat Nebrera González",
                "Francesc Vendrell i Bayona",
                "Mª Dolors Montserrat i Culleré",
                "Belén Pajares Ribas",
                "Daniel Sirera i Bellés",
                "Carina Mejías Sánchez",
                "Santiago Rodríguez Serra",
                "Mª Ángeles Olano García",
                "Rafael López Rueda",
            ],
        },
    ),
    ("A20061", "43"): (
        (399805,),
        {"ERC": ["Ernest Benach i Pascual", "Josep Bargalló i Valls", "Marta Cid i Pañella"]},
    ),
    ("A20101", "08"): (
        (567126,),
        {
            "CIU": [
                "Artur Mas i Gavarró",
                "Joana Ortega i Alemany",
                "Felip Puig i Godes",
                "Meritxell Borràs i Solé",
                "Ramon Espadaler i Parcerisas",
                "Oriol Pujol i Ferrusola",
                "Antoni Castellà i Clavé",
                "Lluís Recoder i Miralles",
                "Irene Rigau i Oliver",
                "Neus Munté i Fernàndez",
                "Lluís Corominas i Díaz",
                "Núria de Gispert i Català",
                "Francesc Homs i Molist",
                "Antoni Fernàndez i Teixidó",
                "Cristina Iniesta i Blasco",
                "Josep Rull i Andreu",
                "Josep Maria Vila d'Abadal i Serra",
                "Josep Lluís Cleries i Gonzàlez",
                "Maria Glòria Renom i Vallbona",
                "Marta Llorens i Garcia",
                "Jordi Turull i Negre",
                "Joan Recasens i Guinot",
                "David Bonvehí i Torras",
                "Anna Figueras i Ibáñez",
                "Montserrat Ribera i Puig",
                "Joan Morell i Comas",
                "Assumpció Laïlla i Jou",
                "Jordi Cuminal i Roquet",
                "Pere Regull i Riba",
                "M. Mercè Jou i Torras",
                "Gerard M. Figueras i Albà",
                "Dolors Gordi i Julià",
                "Francesc Bragulat i Bosom",
                "Maria Senserrich i Guitart",
                "Roger Montañola i Busquets",
            ]
        },
    ),
    ("A20121", "08"): (
        (627162,),
        {
            "ICVEUIA": [
                "Joan Herrera Torres",
                "Dolors Camats Luis",
                "Juan Miguel Mena Arca",
                "Jaume Bosch Mestres",
                "Marta Ribas Frias",
                "Laura Massana Mas",
                "Salvador Milà Solsona",
                "Lorena Vicioso Adrià",
                "David Compañon Costa",
                "Josep Vendrell Gardeñes",
            ]
        },
    ),
    ("A20151", "08"): (
        (704870,),
        {
            "PSC": [
                "Miquel Iceta Llorens",
                "Eva Maria Granados Galiano",
                "Ferran Pedret i Santos",
                "Alícia Romero Llano",
                "Jordi Terrades i Santacreu",
                "Assumpta Escarp Gibert",
                "David Pérez Ibáñez",
                "Esther Niubó Cidoncha",
                "Pol Gibert Horcas",
                "Marta Moreta Rovira",
                "Raúl Moreno Montaña",
                "Eva Maria Martínez Morales",
            ]
        },
    ),
    ("A20151", "25"): ((704834,), {"CS": ["Jorge Soler González", "Javier Rivas Escamilla"]}),
    ("A20211", "08"): (
        (894181,),
        {
            "CUPG": [
                "Dolors Sabater Puig",
                "Carles Riera Albert",
                "Eulàlia Reguant Cura",
                "Xavier Pellicer Pareja",
                "Basharat (Basha) Changue Canalejo",
            ]
        },
    ),
    ("A20211", "17"): (
        (894132,),
        {
            "JXCAT": [
                "Gemma Geis i Carreras",
                "Marta Madrenas i Mir",
                "Salvador Vergés i Tejero",
                "Francesc Xavier Ten i Costa",
                "Maria Antònia Batlle i Andreu",
                "Ferran Roquer i Padrosa",
                "Jordi Munell i Garcia",
            ]
        },
    ),
    ("A20241", "08"): (
        (987024,),
        {
            "PP": [
                "Alejandro Fernández Álvarez",
                "Manuel Reyes López",
                "María Ángeles Esteller Ruedas",
                "Santiago (Santi) Rodríguez Serra",
                "Rosa del Amo Hernández",
                "Hugo Manchón García",
                "Juan Fernández Benítez",
                "Eva García Rodríguez",
                "Miriam Casanova Domènech",
                "Alberto Villagrasa Gil",
                "Cristian Escribano Ramírez",
            ]
        },
    ),
}


def elections() -> list[dict[str, object]]:
    listing = raw.fetch(SOURCE, f"{API}/tipusProces/A/proces", suffix=".json")
    return sorted(json.loads(listing.path.read_bytes()), key=lambda election: str(election["codi"]), reverse=True)


def export(code: str, level: str) -> str:
    return f"{API}/proces/{code}/nivellTerritorial/{level}/exportacio"


def fetch() -> None:
    for election in elections():
        code = election["codi"]
        raw.fetch(SOURCE, f"{API}/proces/{code}/nivellTerritorial", suffix=".json")
        for level in ("CA", "PR", "MU", "ME"):
            raw.fetch(SOURCE, export(str(code), level), suffix=".zip")
        if isinstance(url := election.get("urlElectes"), str):
            raw.fetch(SOURCE, url, suffix=".pdf")
    for documents, _ in PROCLAIMED.values():
        for document in documents:
            raw.fetch(SOURCE, DOGC.format(document), suffix=".pdf")


def cached() -> list[tuple[str, date]]:
    listing = json.loads(raw.cached(SOURCE, f"{API}/tipusProces/A/proces").path.read_bytes())
    return sorted(
        ((str(row["codi"]), datetime.fromtimestamp(int(row["data"]) / 1000, UTC).date()) for row in listing),
        reverse=True,
    )


def table(code: str, level: str, kind: str) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, export(code, level)).path)
    [member] = [name for name in archive.namelist() if f"-{kind}-" in name]
    frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0)
    return frame.rename({column: column.lstrip("﻿") for column in frame.columns}).with_columns(
        pl.col(pl.String).str.strip_chars()
    )


def party_key(sigles: str, name: str) -> str:
    key = re.sub(r"[^A-Z0-9+]", "", sigles.upper())
    if key in SPLIT:
        folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
        key += ":" + re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return key


def accounting(frame: pl.DataFrame, *keys: pl.Expr) -> pl.DataFrame:
    electors = "Total cens electoral" if "Total cens electoral" in frame.columns else "Cens"
    voters = "Total votants" if "Total votants" in frame.columns else "Participació 20:00"
    return frame.select(
        *keys,
        electors=pl.col(electors).cast(pl.Int64),
        voters=pl.col(voters).cast(pl.Int64),
        invalid=pl.col("Vots nuls").cast(pl.Int64),
        blank=pl.col("Vots en blanc").cast(pl.Int64),
        candidacies=pl.col("Vots a candidatures").cast(pl.Int64),
        valid=pl.col("Vots vàlids").cast(pl.Int64),
    )


def votes(frame: pl.DataFrame, *keys: pl.Expr) -> pl.DataFrame:
    return frame.select(
        *keys,
        party=pl.struct("Sigles Candidatura", "Nom Candidatura").map_elements(
            lambda row: party_key(row["Sigles Candidatura"], row["Nom Candidatura"]), return_dtype=pl.String
        ),
        votes=pl.col("Vots").cast(pl.Int64),
    )


def dhondt(counts: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {party: count for party, count in counts.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, party) for party, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(party for *_, party in quotients[:seats])


def elected(code: str) -> pl.DataFrame:
    [url] = [row["urlElectes"] for row in elections() if row["codi"] == code]
    path = raw.cached(SOURCE, str(url)).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    rows: list[dict[str, object]] = []
    province = heading = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("eleccions", "Candidats i candidates")) or "Parlament de" in line:
            continue
        if line.startswith("Circumscripció de "):
            province = {value: key for key, value in PROVINCES.items()}[line.removeprefix("Circumscripció de ")]
            heading = None
        elif match := re.fullmatch(r"(\d+)\.\s*(.+)", line):
            assert province is not None and heading is not None, line
            rows.append({"province": province, "list": heading, "rank": int(match[1]), "name": match[2]})
        elif line.startswith("Candidatura "):
            heading = line.removeprefix("Candidatura ")
        else:
            heading = f"{heading}{'' if heading and heading.endswith('-') else ' '}{line}"
    return pl.DataFrame(rows)


def words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return set(re.findall(r"[a-z0-9]+", folded)) - {"de", "i", "per", "la", "del", "dels", "catalunya"}


def lead(sigles: str) -> str:
    return re.split(r"[^A-Z]+", sigles.upper().replace(".", ""))[0]


def match(heading: str, lists: pl.DataFrame) -> str:
    sigles = re.search(r"\(([^()]*)\)$", heading)
    name = re.sub(r"\s*\([^()]*\)$", "", heading)
    key = party_key(sigles[1] if sigles else "", name)
    rows = list(lists.filter(pl.col("seats") > 0).iter_rows(named=True))
    for test in (
        lambda row: words(row["name"]) == words(name),
        lambda row: row["party"] == key,
        lambda row: key.startswith(row["party"]) or row["party"].startswith(key),
        lambda row: sigles is not None and lead(row["sigles"]) == lead(sigles[1]),
    ):
        if len(hits := [row["party"] for row in rows if test(row)]) == 1:
            return hits[0]
    scores = sorted(
        ((len(words(row["name"]) & words(name)) / len(words(row["name"]) | words(name)), row["party"]) for row in rows),
        reverse=True,
    )
    if scores[0][0] < 0.25 or (len(scores) > 1 and scores[1][0] == scores[0][0]):
        raise ValueError(f"no list matches {heading}")
    return scores[0][1]


def same(a: str, b: str) -> float:
    return len(words(a) & words(b)) / len(words(a) | words(b))


def proclaimed(code: str, members: pl.DataFrame) -> pl.DataFrame:
    members = members.with_columns(key=pl.col("rank").cast(pl.String), proclaimed=pl.lit(False))
    rows: list[dict[str, object]] = []
    for (election, province), (_, lists) in PROCLAIMED.items():
        if election != code:
            continue
        for party, names in lists.items():
            listed = members.filter((pl.col("province") == province) & (pl.col("party") == party))
            taken: set[str] = set()
            for rank, name in enumerate(names, 1):
                scores = sorted(
                    (
                        (same(name, row["name"]), row["key"])
                        for row in listed.iter_rows(named=True)
                        if row["key"] not in taken
                    ),
                    reverse=True,
                )
                key = scores[0][1] if scores and scores[0][0] >= 0.5 else f"dogc{rank}"
                taken.add(key)
                rows.append(
                    {"province": province, "rank": rank, "name": name, "party": party, "key": key, "proclaimed": True}
                )
            members = members.filter((pl.col("province") != province) | (pl.col("party") != party))
    return pl.concat([members, pl.DataFrame(rows, schema=members.schema)]) if rows else members


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[pl.DataFrame]] = defaultdict(list)
    for code, day in cached():
        year = day.year
        province = pl.col("Codi Província").str.zfill(2).alias("province")
        municipality = pl.col("Codi Municipi").alias("municipality")
        mesa = (
            pl.col("Codi Municipi") + pl.col("Districte").str.zfill(2) + pl.col("Secció").str.zfill(3) + pl.col("Mesa")
        ).alias("mesa")
        lists = table(code, "PR", "Vots")
        lists = votes(lists, province).with_columns(
            name=lists["Nom Candidatura"], sigles=lists["Sigles Candidatura"], seats=lists["Escons"].cast(pl.Int64)
        )
        if lists.select("province", "party").is_duplicated().any():
            raise ValueError(f"{code}: two lists share a party key in one province")
        totals = accounting(table(code, "PR", "Participació"), province)
        for row in totals.iter_rows(named=True):
            part = lists.filter(pl.col("province") == row["province"])
            if row["candidacies"] + row["blank"] != row["valid"] or row["valid"] + row["invalid"] != row["voters"]:
                raise ValueError(f"{code} {row['province']}: turnout lines do not add up")
            if part["votes"].sum() != row["candidacies"]:
                raise ValueError(f"{code} {row['province']}: list votes differ from votes to candidacies")
            won = dhondt(dict(zip(part["party"], part["votes"])), row["valid"], int(part["seats"].sum()))
            if any(won[party] != seats for party, seats in zip(part["party"], part["seats"])):
                print(f"{code} {row['province']}: D'Hondt with a 3% threshold does not give the declared seats")
        members = elected(code)
        members = members.with_columns(
            party=pl.struct("province", "list").map_elements(
                lambda row: match(row["list"], lists.filter(pl.col("province") == row["province"])),
                return_dtype=pl.String,
            )
        ).drop("list")
        members = proclaimed(code, members)
        if members.select("province", "party", "rank").is_duplicated().any():
            raise ValueError(f"{code}: two elected members share a list rank")
        counts = members.group_by("province", "party").agg(named=pl.len())
        checked = counts.join(lists.select("province", "party", "seats"), on=["province", "party"])
        kept = checked.filter(pl.col("named") <= pl.col("seats"))
        for row in checked.join(kept, on=["province", "party"], how="anti").iter_rows(named=True):
            print(f"{code} {row['province']} {row['party']}: {row['named']} names for {row['seats']} seats, skipped")
        for row in kept.filter(pl.col("named") < pl.col("seats")).iter_rows(named=True):
            print(f"{code} {row['province']} {row['party']}: {row['named']} names for {row['seats']} seats")
        pieces = {
            "lists": lists,
            "elected": members.join(kept.select("province", "party"), on=["province", "party"], how="semi"),
            "provinces": totals,
            "municipalities": accounting(
                table(code, "MU", "Participació"), province, municipality, pl.col("Nom Municipi").alias("name")
            ),
            "municipality_votes": votes(table(code, "MU", "Vots"), province, municipality),
        }
        if year not in MUNICIPALITY_ONLY:
            pieces["mesas"] = accounting(table(code, "ME", "Participació"), province, municipality, mesa)
            pieces["mesa_votes"] = votes(table(code, "ME", "Vots"), province, municipality, mesa)
        for level in ("municipality", "mesa"):
            if f"{level}_votes" not in pieces:
                continue
            got = pieces[f"{level}_votes"].group_by("province", "party").agg(got=pl.col("votes").sum())
            joined = lists.join(got, on=["province", "party"], how="full", coalesce=True)
            off = joined.filter(pl.col("votes").fill_null(0) != pl.col("got").fill_null(0))
            print(f"{code} {level}: {off.height} province lists differ from the province file")
            if off.height:
                print(off)
        ran = lists.select("province", "party")
        for level in ("municipality_votes", "mesa_votes"):
            if level in pieces:
                stray = pieces[level].join(ran, on=["province", "party"], how="anti")
                if stray["votes"].sum():
                    raise ValueError(f"{code} {level}: votes for a list that did not stand in the province")
                pieces[level] = pieces[level].join(ran, on=["province", "party"], how="semi")
        for name, frame in pieces.items():
            tables[name].append(frame.with_columns(year=pl.lit(year, pl.Int32), day=pl.lit(day)))
    for name, frames in tables.items():
        frame = pl.concat(frames, how="diagonal")
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


SYSTEM = (
    "Closed party lists in four provincial constituencies: Barcelona (85 seats), Girona (17), Lleida (15), and "
    "Tarragona (18), 135 in all, as fixed by the fourth transitory provision of the 1979 Statute of Autonomy, which "
    "Catalonia has never replaced with an electoral law of its own. Seats are shared by the D'Hondt method among the "
    "lists that win at least 3% of the valid votes in the province, and blank ballots count as valid votes, under the "
    "rules of the Spanish general electoral law."
)
PROVINCE_METHOD = (
    "List votes, seats, registered voters, ballots cast, blank ballots, and null ballots by province from the "
    "Generalitat de Catalunya's results exports. Valid ballots here are the votes to candidacies; blank ballots, "
    "which Spanish electoral law counts as valid, are stored apart. The seats are the numbers the file declares for "
    "each list. Votes of residents abroad (CERA) are included."
)
ELECTED = (
    " The elected members of each list, with their places on it, come from the Generalitat's list of members elected "
    "in each constituency."
)
PROCLAIMED_NOTE = (
    " The Generalitat's list is incomplete or wrong for {lists}, so those members, in the order the board names them, "
    "come instead from the provincial electoral board's proclamation of elected members in the Diari Oficial de la "
    "Generalitat de Catalunya."
)
SHORT = (
    " That list names {named} of the {seats} members elected here, so the others are not named; for any list it "
    "names more members than the list won seats, none of that list's members are stored."
)
DHONDT_DIFFERS = (
    " D'Hondt with a 3% threshold applied to the votes printed here gives a different split from the declared "
    "seats, which are stored as published."
)
MUNICIPALITY_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots by municipality from the "
    "Generalitat de Catalunya's results exports. Valid ballots are the votes to candidacies, with blank ballots "
    "stored apart. Votes of residents abroad (CERA) are a separate unit for the province, as the file prints them."
)
MESA_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots by polling station (mesa) from the "
    "Generalitat de Catalunya's results exports, with each mesa on its municipality{section}. Votes of residents "
    "abroad (CERA) are a separate unit for the province, as the file prints them."
)
SECTION = " and, where the 2025 census section map still holds its section code, on that section"
OVER = " In {count} units the ballots cast exceed the registered voters, as the file prints them."


def over(frame: pl.DataFrame) -> str:
    count = frame.filter(pl.col("voters") > pl.col("electors")).height
    return OVER.format(count=count) if count else ""


def emit() -> Path:
    lists = pl.read_parquet(PARSED / "lists.parquet")
    provinces = pl.read_parquet(PARSED / "provinces.parquet")
    municipalities = pl.read_parquet(PARSED / "municipalities.parquet")
    municipality_votes = pl.read_parquet(PARSED / "municipality_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesas.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "elected.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parlament de Catalunya elections of 1980 to 2024 by province and municipality, and by polling "
                "station from 1988, from the Generalitat de Catalunya's results exports"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, catalonia = cs.polity("ES"), cs.polity("ES-CT")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Parlament de Catalunya")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Llicència oberta d'ús d'informació – Catalunya (or CC0), per the Generalitat's legal notice",
            license_url="https://tramits.gencat.cat/ca/ajuda/avis_legal/",
            attribution="Font: Generalitat de Catalunya, Departament de la Presidència.",
        )
        for code, _ in cached():
            for level in LEVELS:
                cs.artifact(raw.cached(SOURCE, export(code, level)), source)
        for election in elections():
            if isinstance(url := election.get("urlElectes"), str):
                cs.artifact(raw.cached(SOURCE, url), source)
        gazette = cs.source(PUBLISHER, DOGC_TITLE)
        cs.add(
            "source",
            id=gazette,
            kind="official_body",
            publisher=PUBLISHER,
            title=DOGC_TITLE,
            url="https://dogc.gencat.cat/",
            license="Llicència oberta d'ús d'informació – Catalunya (or CC0), per the Generalitat's legal notice",
            license_url="https://tramits.gencat.cat/ca/ajuda/avis_legal/",
            attribution="Font: Diari Oficial de la Generalitat de Catalunya.",
        )
        for documents, _ in PROCLAIMED.values():
            for document in documents:
                cs.artifact(raw.cached(SOURCE, DOGC.format(document)), gazette)

        known = dict(
            conn.execute(
                "select e.value, e.entity_id from external_id e join party p on p.id = e.entity_id "
                "where e.scheme = 'wikidata' and e.value = any(%s)",
                (list(WIKIDATA.values()),),
            ).fetchall()
        )
        parties: dict[str, UUID] = {}
        latest = lists.sort("day", descending=True).unique("party", keep="first", maintain_order=True)
        for row in latest.iter_rows(named=True):
            key = row["party"]
            if (qid := WIKIDATA.get(key)) is not None and qid in known:
                parties[key] = known[qid]
                continue
            parties[key] = cs.key(f"{SOURCE}.party", key)
            cs.add("party", id=parties[key], kind="party", polity_id=catalonia)
            name(parties[key], row["name"], "ca")
            if row["sigles"] and row["sigles"] != row["name"]:
                name(parties[key], row["sigles"], "ca", "abbreviation")

        body = cs.key("es.body", "catalonia-parlament")
        cs.add("body", id=body, polity_id=catalonia, kind="unicameral")
        name(body, "Parliament of Catalonia")
        name(body, "Parlament de Catalunya", "ca", "native")
        cs.add("body_size", body_id=body, seats=135, valid_from=date(1980, 3, 20), valid_to=None)
        system = cs.key("es.electoral_system", "catalonia-parlament")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=catalonia,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
            },
            rules_version=1,
        )
        name(system, "Parlament de Catalunya closed-list D'Hondt system")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

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

        def municipality_unit(code: str) -> UUID:
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if code.endswith("998"):
                    area, relation, kind = cs.area("es.provincia", code[:2]), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[code[:2]], relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def tally(result_set: UUID, unit: UUID, row: dict[str, int | None], channel: int = 0) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "candidacies"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        written: dict[UUID, str] = {}
        newest = max(provinces["year"])
        for code, day in cached():
            year = day.year
            election = cs.key("es.election", f"catalonia:{day}")
            cs.add(
                "election", id=election, polity_id=catalonia, kind="general", date_from=day, date_to=day, status="held"
            )
            name(election, f"{year} Catalan regional election")
            name(election, f"Eleccions al Parlament de Catalunya de {year}", "ca", "native")
            path = f"es/ct/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            stamp = f"{year}{day.month:02d}"
            year_lists = lists.filter(pl.col("year") == year)
            for province, province_name in PROVINCES.items():
                total = provinces.filter((pl.col("year") == year) & (pl.col("province") == province)).row(0, named=True)
                rows = year_lists.filter(pl.col("province") == province).sort("votes", descending=True)
                seats = int(rows["seats"].sum())
                key = f"{day}:{province}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=seats,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, province_name)
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
                        ballot_label=entry["name"],
                    )

                def make_set(label: str, default: bool, notes: list[str]) -> UUID:
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
                        completeness=1,
                    )
                    for body_md in notes:
                        cs.add("note", entity_id=result_set, kind="methodology", body_md=body_md)
                    return result_set

                declared = members.filter((pl.col("year") == year) & (pl.col("province") == province)).sort(
                    "party", "rank"
                )
                note = PROVINCE_METHOD + ELECTED
                fixed = rows.join(declared.filter("proclaimed").select("party").unique(), on="party", how="semi")
                if fixed.height:
                    labels = [f"{entry['name']} ({entry['sigles']})" for entry in fixed.iter_rows(named=True)]
                    note += PROCLAIMED_NOTE.format(
                        lists=labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + f", and {labels[-1]}"
                    )
                if declared.height < seats:
                    note += SHORT.format(named=declared.height, seats=seats)
                won = dhondt(dict(zip(rows["party"], rows["votes"])), total["valid"], seats)
                if any(won[party] != count for party, count in zip(rows["party"], rows["seats"])):
                    note += DHONDT_DIFFERS
                province_set = make_set("province", True, [note])
                written[province_set] = note
                unit = province_units[province]
                tally(province_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append(
                        (str(province_set), str(unit), str(options[entry["party"]]), VOTES, entry["votes"], 0)
                    )
                    if entry["seats"]:
                        cs.add(
                            "outcome",
                            result_set_id=province_set,
                            option_id=options[entry["party"]],
                            kind="won",
                            seats=entry["seats"],
                        )
                for member in declared.iter_rows(named=True):
                    person_key = f"{key}:{member['party']}:{member['key']}"
                    person = cs.key(f"{SOURCE}.person", person_key)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, member["name"], "ca")
                    option = cs.key(f"{SOURCE}.option", person_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["party"]],
                        party_id=parties[member["party"]],
                        ballot_label=member["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", person_key),
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        list_position=member["rank"],
                        ballot_name=member["name"],
                        member_of_party_id=parties[member["party"]],
                        is_independent=False,
                    )
                    cs.add("outcome", result_set_id=province_set, option_id=option, kind="elected")

                places = municipalities.filter((pl.col("year") == year) & (pl.col("province") == province))
                place_votes = municipality_votes.filter((pl.col("year") == year) & (pl.col("province") == province))
                municipality_set = make_set("municipality", False, [MUNICIPALITY_METHOD + over(places)])
                for row in places.iter_rows(named=True):
                    channel = ABROAD if row["municipality"].endswith("998") else 0
                    tally(municipality_set, municipality_unit(row["municipality"]), row, channel)
                for row in place_votes.iter_rows(named=True):
                    values.append(
                        (
                            str(municipality_set),
                            str(municipality_unit(row["municipality"])),
                            str(options[row["party"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["municipality"].endswith("998") else 0,
                        )
                    )

                if year in MUNICIPALITY_ONLY:
                    continue
                units = mesas.filter((pl.col("year") == year) & (pl.col("province") == province))
                unit_votes = mesa_votes.filter((pl.col("year") == year) & (pl.col("province") == province))
                mesa_set = make_set(
                    "mesa", False, [MESA_METHOD.format(section=SECTION if year == newest else "") + over(units)]
                )
                mesa_units: dict[str, tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    unit = cs.unit("es.mesa", f"{stamp}:{row['mesa']}")
                    channel = 0
                    if row["municipality"].endswith("998"):
                        parent, area, relation, kind, channel = (
                            province_units[province],
                            cs.area("es.provincia", province),
                            "within",
                            "aggregate",
                            ABROAD,
                        )
                    else:
                        parent = municipality_unit(row["municipality"])
                        section = section_areas.get(row["mesa"][:10]) if year == newest else None
                        area = section or municipality_areas.get(row["municipality"])
                        relation, kind = ("within" if area is not None else "unlocated"), "standard"
                    unit_rows[unit] = (unit, "es.mesa", f"{stamp}:{row['mesa']}", parent, relation, area, kind)
                    mesa_units[row["mesa"]] = (unit, channel)
                    tally(mesa_set, unit, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    unit, channel = mesa_units[row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["party"]]), VOTES, row["votes"], channel))

        orphans = conn.execute(
            """select o.id, array_remove(array_agg(y.id), null), array_remove(array_agg(y.person_id), null)
               from contest_option o
               join changeset c on c.id = o.changeset_id and c.pipeline = %s
               join entity e on e.id = o.id and e.retired_by is null
               left join candidacy y on y.option_id = o.id
               where o.kind = 'candidate' and o.contest_id = any(%s) and o.id <> all(%s)
                 and not exists (select 1 from result_value v where v.option_k = o.k)
               group by o.id""",
            [
                SOURCE,
                list({row["contest_id"] for row in cs.rows["contest_option"]}),
                [row["id"] for row in cs.rows["contest_option"]],
            ],
        ).fetchall()
        reason = (
            "The Generalitat's list of elected members named this member wrongly for the list; the provincial "
            "electoral board's proclamation in the DOGC replaces it."
        )
        staged = {row["person_id"] for row in cs.rows["candidacy"]}
        for option, candidacies, people in orphans:
            for candidacy in candidacies:
                cs.add("entity_retirement", entity_id=candidacy, reason=reason)
            cs.add("entity_retirement", entity_id=option, reason=reason)
            for person in people:
                if person not in staged:
                    cs.add("entity_retirement", entity_id=person, reason=reason)
        print(f"{len(orphans)} candidate options retired")
        for note_id, entity, body_md in conn.execute(
            "select id, entity_id, body_md from note where entity_id = any(%s) and kind = 'methodology'",
            (list(written),),
        ).fetchall():
            if body_md != written[entity]:
                cs.add(
                    "note_drop",
                    note_id=note_id,
                    entity_id=entity,
                    body_md=body_md,
                    reason="the method note was rewritten",
                )
        fresh = [
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
        if fresh:
            cs.frame(
                "reporting_unit", pl.DataFrame(fresh, schema_overrides={"parent_id": pl.String, "area_id": pl.String})
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
