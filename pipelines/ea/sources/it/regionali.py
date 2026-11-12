import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES, proper
from ea.sources.it.storico import EXISTING, NEW, cased
from ea.store import DATA, connect

SOURCE = "it.regionali"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://dait.interno.gov.it/documenti/opendata/regionali"
DAYS = [
    date(1970, 6, 7),
    date(1975, 6, 15),
    date(1980, 6, 8),
    date(1985, 5, 12),
    date(1990, 5, 6),
    date(1995, 4, 23),
    date(2000, 4, 16),
    date(2001, 6, 24),
    date(2001, 11, 11),
    date(2005, 4, 3),
    date(2005, 4, 17),
    date(2006, 11, 5),
    date(2008, 12, 14),
    date(2010, 3, 28),
    date(2011, 10, 16),
    date(2013, 2, 24),
    date(2013, 11, 17),
    date(2014, 5, 25),
    date(2014, 11, 23),
    date(2015, 5, 31),
    date(2018, 3, 4),
    date(2019, 2, 10),
    date(2019, 3, 24),
    date(2019, 5, 26),
    date(2019, 10, 27),
    date(2020, 1, 26),
    date(2020, 9, 20),
    date(2021, 10, 3),
    date(2023, 2, 12),
    date(2024, 3, 10),
    date(2024, 4, 21),
    date(2024, 6, 9),
    date(2024, 10, 27),
    date(2024, 11, 17),
    date(2025, 10, 5),
    date(2025, 10, 12),
    date(2025, 11, 23),
]
REGIONS = {
    "PIEMONTE": "01",
    "LOMBARDIA": "03",
    "VENETO": "05",
    "LIGURIA": "07",
    "EMILIA-ROMAGNA": "08",
    "EMILIA ROMAGNA": "08",
    "TOSCANA": "09",
    "UMBRIA": "10",
    "MARCHE": "11",
    "LAZIO": "12",
    "ABRUZZO": "13",
    "MOLISE": "14",
    "CAMPANIA": "15",
    "PUGLIA": "16",
    "BASILICATA": "17",
    "CALABRIA": "18",
    "SICILIA": "19",
}
COLUMNS = {
    "REGIONE": ("REGIONE", "REG"),
    "CIRCOSCRIZIONE": ("CIRCOSCRIZIONE", "CIRCOSCR", "CIRC"),
    "PROVINCIA": ("PROVINCIA", "PROV"),
    "COMUNE": ("COMUNE",),
    "SEZIONE": ("SEZIONE",),
    "registered": ("ELETTORI", "ELETTORITOT", "ELETTORI_TOT", "NUMELETTORI", "ELETTORI_TOTALI"),
    "voters": ("VOTANTI", "VOTANTITOT", "VOTANTI_TOT", "VOTANTI_TOTALI"),
    "blank": ("SCHEDE_BIANCHE", "SKBIANCHE"),
    "null": ("SCHEDE_NULLE",),
    "contested": ("SCHEDE_CONTESTATE",),
    "surname": ("COGNOME", "COGNOME_CANDIDATO"),
    "forename": ("NOME", "NOME_CANDIDATO"),
    "candidate_votes": ("VOTI_CANDIDATO", "VOTICANDIDATO", "VOTICAND", "VOTICANDIDLEADER", "VOTICANDLEADER"),
    "list": ("LISTA", "DESCRLISTA"),
    "list_votes": ("VOTI_LISTA", "VOTILISTA"),
}
REGION_INFO = {
    "01": (
        "IT-21",
        "piemonte",
        "Piemonte",
        "Piedmont",
        "Consiglio regionale del Piemonte",
        "Regional Council of Piedmont",
    ),
    "03": (
        "IT-25",
        "lombardia",
        "Lombardia",
        "Lombardy",
        "Consiglio regionale della Lombardia",
        "Regional Council of Lombardy",
    ),
    "05": ("IT-34", "veneto", "Veneto", "Veneto", "Consiglio regionale del Veneto", "Regional Council of Veneto"),
    "07": (
        "IT-42",
        "liguria",
        "Liguria",
        "Liguria",
        "Consiglio regionale della Liguria",
        "Regional Council of Liguria",
    ),
    "08": (
        "IT-45",
        "emilia-romagna",
        "Emilia-Romagna",
        "Emilia-Romagna",
        "Assemblea legislativa dell'Emilia-Romagna",
        "Legislative Assembly of Emilia-Romagna",
    ),
    "09": (
        "IT-52",
        "toscana",
        "Toscana",
        "Tuscany",
        "Consiglio regionale della Toscana",
        "Regional Council of Tuscany",
    ),
    "10": (
        "IT-55",
        "umbria",
        "Umbria",
        "Umbria",
        "Assemblea legislativa dell'Umbria",
        "Legislative Assembly of Umbria",
    ),
    "11": ("IT-57", "marche", "Marche", "Marche", "Consiglio regionale delle Marche", "Regional Council of Marche"),
    "12": ("IT-62", "lazio", "Lazio", "Lazio", "Consiglio regionale del Lazio", "Regional Council of Lazio"),
    "13": ("IT-65", "abruzzo", "Abruzzo", "Abruzzo", "Consiglio regionale dell'Abruzzo", "Regional Council of Abruzzo"),
    "14": ("IT-67", "molise", "Molise", "Molise", "Consiglio regionale del Molise", "Regional Council of Molise"),
    "15": (
        "IT-72",
        "campania",
        "Campania",
        "Campania",
        "Consiglio regionale della Campania",
        "Regional Council of Campania",
    ),
    "16": ("IT-75", "puglia", "Puglia", "Apulia", "Consiglio regionale della Puglia", "Regional Council of Apulia"),
    "17": (
        "IT-77",
        "basilicata",
        "Basilicata",
        "Basilicata",
        "Consiglio regionale della Basilicata",
        "Regional Council of Basilicata",
    ),
    "18": (
        "IT-78",
        "calabria",
        "Calabria",
        "Calabria",
        "Consiglio regionale della Calabria",
        "Regional Council of Calabria",
    ),
    "19": ("IT-82", "sicilia", "Sicilia", "Sicily", "Assemblea regionale siciliana", "Sicilian Regional Assembly"),
}
COUNCIL_SIZES = [30, 40, 50, 60, 80]
AGGREGATED = (
    "Regional totals are the sums of the municipal (from 2024, polling-section) rows in the Interior Ministry's open "
    "data file for this election."
)
SEATS_NOTE = (
    "Seats are the candidates the open data marks as elected, counted by list; they add up to the whole council."
)
NO_SEATS = (
    "Seats are not shown: the candidates the open data marks as elected do not add up to the size of the council."
)
LIST_PART = (
    "These are the votes for the provincial lists, which share out most of the council's seats; the rest went to the "
    "regional list or coalition of the winning candidate for president, so seats by list are not shown."
)
LIST_SEATS = (
    "Seats are the candidates the open data marks as elected, counted by the list they were elected from. Lists "
    "without votes here are regional lists, each headed by a candidate for president: the winning one's seats "
    "include the majority bonus, and a losing one's seat went to its head. A president or runner-up who sits on the "
    "council without being elected from a list is not counted."
)
ELECTED_AS = {
    "FEDERAZIONE DEI VERDI": "FED.DEI VERDI",
    "LEGA NORD-BASTA ?URO": "LEGA NORD-BASTA €URO",
    "CENTRO DEMOCRATICO - ALTRI": "CENTRO DEMOCRATICO ¿ ALTRI",
    "COMUNITA' DEMOCRATICHE - PARTITO DEMOCRATICO": "COMUNITA' DEMOCRATICHE - PARTITO DEMOCRA",
    "CHIAMPARINO PER IL PIEMONTE DEL SI'": "CHIAMPARINO PER IL PIEMONTE DEL SÌ",
    "IO RESTO IN CALABRIA CON PIPPO CALLIPO PRESIDENTE": "IO RESTO IN CALABRIA CON CALLIPO",
}
LISTINO = (
    "In 1995 each voter could also choose a regional list headed by a candidate for president, and the head of the "
    "winning regional list was then elected president by the council, so no winner is marked here. Candidates' "
    "votes include those cast for them alone."
)
DIRECT = (
    "The candidate with the most votes is elected president. Candidates' votes include those cast for them alone, "
    "without a list."
)
NAMES = {
    ("D'ANTONI SERGIO ANTONIO", ""): ("Sergio Antonio", "D'Antoni"),
    ("ORLANDO LEOLUCA", ""): ("Leoluca", "Orlando"),
    ("R.FITTO", ""): ("Raffaele", "Fitto"),
    ("N.VENDOLA", ""): ("Nichi", "Vendola"),
    ("F.G.GALASSI", ""): ("F. G.", "Galassi"),
    ("L.SCALABRINI", ""): ("L.", "Scalabrini"),
    ("FAENZI", "M."): ("M.", "Faenzi"),
    ("BOSI", "F."): ("F.", "Bosi"),
    ("PALMISANI", "I."): ("I.", "Palmisani"),
    ("DE VIRGILIS", "A."): ("A.", "De Virgilis"),
    ("SPACCA", ""): ("Gian Mario", "Spacca"),
}
NAMES_IN = {
    ("09", "ROSSI", "E."): ("Enrico", "Rossi"),
    ("11", "ROSSI", ""): ("", "Rossi"),
    ("11", "MARINELLI", ""): ("", "Marinelli"),
}
MARCHE_DATA = "https://web.archive.org/web/20241007073555id_/https://dati.elezioni.marche.it/static_json"


@dataclass(frozen=True)
class Regional:
    publisher: str
    title: str
    url: str
    candidates: str
    lists: str
    allocation: str
    size: int
    seats: dict[str, int]
    parties: dict[str, str]
    counted: str
    allocated: str


REGIONAL = {
    ("11", date(2020, 9, 20)): Regional(
        publisher="Regione Marche",
        title="Elezioni regionali 2020: risultati (dati.elezioni.marche.it)",
        url="https://dati.elezioni.marche.it/",
        candidates=f"{MARCHE_DATA}/raggrup_0.json?_=1728286548303",
        lists=f"{MARCHE_DATA}/liste_0.json?_=1728286548304",
        allocation="https://web.archive.org/web/2020id_/https://www.regione.marche.it/portals/0/Elezioni_2020/ripartizioneSeggi%20finale.pdf",
        size=30,
        seats={
            "LEGA SALVINI MARCHE": 8,
            "GIORGIA MELONI per ACQUAROLI - FRATELLI D'ITALIA": 7,
            "FORZA ITALIA - BERLUSCONI - CIVICI PER LE MARCHE": 2,
            "POPOLARI MARCHE - UNIONE DI CENTRO": 1,
            "CIVITAS CIVICI": 1,
            "PARTITO DEMOCRATICO": 7,
            "RINASCI MARCHE": 1,
            "ITALIA VIVA": 1,
            "MOVIMENTO 5 STELLE": 2,
        },
        parties={
            "LEGA SALVINI MARCHE": "LEGA",
            "GIORGIA MELONI per ACQUAROLI - FRATELLI D'ITALIA": "FRATELLI D'ITALIA",
            "FORZA ITALIA - BERLUSCONI - CIVICI PER LE MARCHE": "FORZA ITALIA",
        },
        counted=(
            "Results are the Marche Region's final count of all 1,576 polling sections, from its election results "
            "site as archived in October 2024."
        ),
        allocated=(
            "The 30 council seats are those of the region's seat allocation by list. The seat won by Italia Viva went "
            "to Maurizio Mangialardi, the runner-up for president, and the president, Francesco Acquaroli, also sits "
            "on the council."
        ),
    ),
}
PLACES = ("REGIONE", "CIRCOSCRIZIONE", "PROVINCIA", "COMUNE", "SEZIONE")
COUNTS = ("registered", "voters", "blank", "null", "contested")
RESULTS = re.compile(
    r"(?i)^(regionali-\d{8}\.(txt|csv|xlsx)|scrutini[-_]\d{8}\.txt|[^/]*scrutini\.(csv|xlsx)|liste&candidati_\d{8}\.txt)$"
)
PREFERENCES = re.compile(r"^Preferenze_Regionali\d+\.txt$")


def person_name(row: dict[str, str]) -> tuple[str, str]:
    surname = re.sub(r"\s+", " ", row["surname"]).strip()
    forename = re.sub(r"\s+", " ", row["forename"]).strip()
    if (surname, forename) in NAMES:
        return NAMES[surname, forename]
    if (row["region"], surname, forename) in NAMES_IN:
        return NAMES_IN[row["region"], surname, forename]
    mixed = re.fullmatch(r"([A-Z][a-z]+(?: [A-Z][a-z]+)*) ([A-Z'][A-Z' ]+)", surname)
    if mixed and not forename:
        return mixed[1], proper(mixed[2])
    forename = re.split(r"(?i)\s+dett[oa]\s+", forename)[-1]
    return proper(forename), proper(surname)


def url(day: date) -> str:
    return f"{ARCHIVE}/regionali-{day:%Y%m%d}.zip"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")
    for held in REGIONAL.values():
        raw.fetch(SOURCE, held.candidates, suffix=".json")
        raw.fetch(SOURCE, held.lists, suffix=".json")
        raw.fetch(SOURCE, held.allocation, suffix=".pdf")


def regional(code: str, day: date, held: Regional) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    groups = json.loads(raw.cached(SOURCE, held.candidates).path.read_bytes())
    lists = json.loads(raw.cached(SOURCE, held.lists).path.read_bytes())
    totals = groups["votiRaggrup"]
    if int(totals["tot_sezio"]) != groups["totSezioni"]["TOT_SEZIO"] or lists["votiListe"]["tot_sezio"] != int(
        totals["tot_sezio"]
    ):
        raise ValueError(f"{code} {day}: the count is not final")
    if totals["tvotival"] + totals["tbianche"] + totals["tnulle"] + totals["sk_contestate"] != totals["tvotanti"]:
        raise ValueError(f"{code} {day}: ballots do not add up to voters")
    accounting = pl.DataFrame(
        {
            "day": [day],
            "region": [code],
            "registered": [int(totals["iscri_totali"])],
            "voters": [int(totals["tvotanti"])],
            "blank": [int(totals["tbianche"])],
            "null": [int(totals["tnulle"])],
            "contested": [int(totals["sk_contestate"])],
            "places": [int(totals["tot_sezio"])],
        }
    )
    named = {entry["nlistamacro"]: entry["nome"].strip() for entry in lists["anagrListe"]}
    list_rows = pl.DataFrame(
        [
            {"day": day, "region": code, "list": named[int(number)], "list_votes": votes["voti"], "places": 1}
            for number, votes in lists["votiListe"]["arrVotiListe"].items()
        ]
    )
    candidates = pl.DataFrame(
        [
            {
                "day": day,
                "region": code,
                "surname": group["cognome_cand"].strip().upper(),
                "forename": group["nome_cand"].strip().upper(),
                "candidate_votes": totals["arrVotiRaggrup"][str(group["nraggrupmacro"])]["voti"],
            }
            for group in groups["anagrRaggrup"]
        ]
    )
    if candidates["candidate_votes"].sum() != totals["tvotival"]:
        raise ValueError(f"{code} {day}: candidate votes do not add up to valid votes")
    if list_rows["list_votes"].sum() != lists["votiListe"]["tvotival"]:
        raise ValueError(f"{code} {day}: list votes do not add up to valid list votes")
    if sum(held.seats.values()) != held.size or not set(held.seats) <= set(named.values()):
        raise ValueError(f"{code} {day}: seats do not match the lists")
    return accounting, list_rows, candidates


def read(archive: zipfile.ZipFile, member: str) -> pl.DataFrame:
    data = archive.read(member)
    if member.lower().endswith(".xlsx"):
        frame = pl.read_excel(io.BytesIO(data), infer_schema_length=0)
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema_length=0, quote_char='"')
    frame = frame.rename({column: column.strip().upper() for column in frame.columns})
    renamed: dict[str, str] = {}
    for canonical, aliases in COLUMNS.items():
        for alias in aliases:
            if alias in frame.columns:
                renamed[alias] = canonical
                break
    frame = frame.select(list(renamed)).rename(renamed)
    return frame.with_columns(
        [
            pl.col(c).str.strip_chars().fill_null("")
            for c in frame.columns
            if c not in COUNTS + ("candidate_votes", "list_votes")
        ]
        + [
            pl.col(c).str.strip_chars().str.replace(r"[.,]00?$", "").cast(pl.Int64, strict=True)
            for c in frame.columns
            if c in COUNTS + ("candidate_votes", "list_votes")
        ]
    )


def parse() -> None:
    accounting: list[pl.DataFrame] = []
    lists: list[pl.DataFrame] = []
    candidates: list[pl.DataFrame] = []
    seats: list[pl.DataFrame] = []
    PARSED.mkdir(parents=True, exist_ok=True)
    for day in DAYS:
        archive = zipfile.ZipFile(raw.cached(SOURCE, url(day)).path)
        members = [m for m in archive.namelist() if RESULTS.match(m)]
        if not members:
            raise ValueError(f"{day}: no results member in {archive.namelist()}")
        for member in members:
            frame = read(archive, member)
            places = [c for c in PLACES if c in frame.columns]
            frame = frame.with_columns(
                region=pl.col("REGIONE").str.to_uppercase().replace_strict(REGIONS), day=pl.lit(day)
            )
            if "registered" in frame.columns:
                rows = frame.select(*places, "region", "day", *[c for c in COUNTS if c in frame.columns])
                per_place = rows.unique()
                if per_place.select(places).is_duplicated().any():
                    raise ValueError(f"{day} {member}: accounting differs within a place")
                accounting.append(
                    per_place.group_by("day", "region").agg(
                        [pl.col(c).sum() for c in COUNTS if c in frame.columns], places=pl.len()
                    )
                )
            if "list" in frame.columns:
                lists.append(
                    frame.filter(pl.col("list") != "")
                    .group_by("day", "region", "list")
                    .agg(pl.col("list_votes").sum(), places=pl.len())
                )
            if "candidate_votes" in frame.columns and day.year >= 1995:
                per_place = frame.select(*places, "region", "day", "surname", "forename", "candidate_votes").unique()
                if per_place.select(*places, "surname", "forename").is_duplicated().any():
                    raise ValueError(f"{day} {member}: candidate votes differ within a place")
                candidates.append(
                    per_place.group_by("day", "region", "surname", "forename").agg(pl.col("candidate_votes").sum())
                )
        for member in archive.namelist():
            if PREFERENCES.match(member):
                text = archive.read(member).decode("cp1252")
                frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema_length=0, quote_char='"')
                frame = frame.rename({column: column.strip().upper() for column in frame.columns})
                region = "REGIONE" if "REGIONE" in frame.columns else "ENTE1"
                where = next(c for c in ("CIRCOSCR", "PROVINCIA", "ENTE2") if c in frame.columns)
                seats.append(
                    frame.filter(pl.col("CODTIPOELETTO").str.strip_chars() == "E")
                    .select(
                        pl.col("COGNOME", "NOME", "DATANASCITA", where).str.strip_chars(),
                        day=pl.lit(day),
                        region=pl.col(region).str.strip_chars().str.to_uppercase().replace_strict(REGIONS),
                        list=pl.col("DESCRLISTA").str.strip_chars().replace(ELECTED_AS),
                    )
                    .unique()
                    .group_by("day", "region", "list")
                    .agg(seats=pl.len())
                )
    for (code, day), held in REGIONAL.items():
        counts, by_list, running = regional(code, day, held)
        accounting.append(counts)
        lists.append(by_list)
        candidates.append(running)
    pl.concat(accounting, how="diagonal_relaxed").write_parquet(PARSED / "accounting.parquet")
    pl.concat(lists, how="vertical_relaxed").write_parquet(PARSED / "lists.parquet")
    pl.concat(candidates, how="vertical_relaxed").with_columns(
        pl.struct("region", "surname", "forename")
        .map_elements(lambda row: list(person_name(row)), return_dtype=pl.List(pl.String))
        .alias("named")
    ).with_columns(given=pl.col("named").list.get(0), family=pl.col("named").list.get(1)).drop("named").write_parquet(
        PARSED / "candidates.parquet"
    )
    pl.concat(seats).write_parquet(PARSED / "seats.parquet")


def emit() -> Path:
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    totals = accounting.group_by("day", "region").agg(pl.all().exclude("day", "region").sum())
    list_totals = lists.group_by("day", "region", "list").agg(pl.col("list_votes").sum())
    elected = seats.filter(
        (pl.col("day").dt.year() >= 1995) | pl.col("seats").sum().over("day", "region").is_in(COUNCIL_SIZES)
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian regional council and presidential elections 1970–2025 from the Interior Ministry's open data and the regions' own results, by region",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        if interno in cs.minted:
            raise ValueError("publish it.interno before it.regionali")
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), interno)
        regional_sources: dict[tuple[str, date], UUID] = {}
        for place, held in REGIONAL.items():
            regional_sources[place] = cs.source(held.publisher, held.title)
            cs.add(
                "source",
                id=regional_sources[place],
                kind="official_body",
                publisher=held.publisher,
                title=held.title,
                url=held.url,
            )
            for link in (held.candidates, held.lists, held.allocation):
                cs.artifact(raw.cached(SOURCE, link), regional_sources[place])
        italy = cs.polity("IT")
        if italy in cs.minted:
            raise ValueError("publish it.interno before it.regionali")

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str, day: date, aliases: dict[str, str]) -> UUID:
            label = aliases.get(label, label)
            spec = ("it.party", "forza-italia") if label == "FORZA ITALIA" and day.year >= 2014 else EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = cs.key(*spec)
                    if parties[spec] in cs.minted:
                        raise ValueError(f"{spec} is not in the database")
                return parties[spec]
            full, abbreviation, kind = NEW.get(label) or (label, None, "list_vehicle")
            known = label in NEW
            key = ("it.party", slug(full)) if known else ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
                    name(parties[key], full if known else cased(label), "it")
            return parties[key]

        persons: dict[str, UUID] = {}

        def person_of(given: str, family: str, day: date, region: str) -> UUID:
            first = given.split(" ")[0] if given else ""
            key = f"{family}|{first}" if len(first) > 2 and "." not in first else f"{day}:{region}:{family}|{given}"
            if key not in persons:
                persons[key] = cs.key(f"{SOURCE}.person", key)
                if persons[key] in cs.minted:
                    cs.add("person", id=persons[key])
                    name(persons[key], f"{given} {family}".strip(), "it")
            return persons[key]

        rows: list[dict[str, object]] = []

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, count: int | None) -> None:
            if count is not None:
                rows.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None if option is None else str(option),
                        "measure": measure,
                        "value": count,
                    }
                )

        def result_set(key: str, election: UUID, contest: UUID | None, source: UUID) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            return id

        held = totals.select("day", "region").sort("day", "region")
        for code in sorted(held["region"].unique()):
            iso, slug_, native, english, council_it, council_en = REGION_INFO[code]
            region = cs.polity(iso)
            area = cs.area("it.regione", code)
            unit = cs.unit("it.regione", code)
            if area in cs.minted or unit in cs.minted:
                raise ValueError("publish it.istat and it.storico before it.regionali")
            if region in cs.minted:
                cs.add("polity", id=region, kind="region", iso_code=iso)
                cs.add("slug", path=f"it/{slug_}", entity_id=region)
                cs.add("polity_parent", polity_id=region, parent_id=italy)
                cs.add("polity_territory", polity_id=region, area_id=area)
                name(region, native, "it")
                if english != native:
                    name(region, english, "en", "english")
            body = cs.key(f"{SOURCE}.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=region, kind="unicameral")
                name(body, council_en)
                name(body, council_it, "it", "native")
            office = cs.key(f"{SOURCE}.office", code)
            if office in cs.minted:
                cs.add("office", id=office, polity_id=region, kind="head_of_government")
                name(office, f"President of {english}")
                name(office, f"Presidente della Regione {native}", "it", "native")
            days = held.filter(pl.col("region") == code)["day"].to_list()
            for day in days:
                label = str(day.year) if sum(other.year == day.year for other in days) == 1 else f"{day:%B} {day.year}"
                election = cs.key(f"{SOURCE}.election", f"{code}:{day}")
                cs.add(
                    "election", id=election, polity_id=region, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{label} {english} regional election")
                name(election, f"Elezioni regionali in {native} del {day.year}", "it", "native")
                path = f"it/{slug_}/{day}-general"
                cs.add("slug", path=path, entity_id=election)
                total = totals.filter(pl.col("day") == day, pl.col("region") == code).row(0, named=True)
                own = REGIONAL.get((code, day))
                source = interno if own is None else regional_sources[code, day]
                accounting_set = result_set(f"{code}:{day}:election", election, None, source)
                value(accounting_set, unit, None, REGISTERED, total["registered"])
                value(accounting_set, unit, None, VOTERS, total["voters"])
                value(accounting_set, unit, None, BLANK, total["blank"])
                value(accounting_set, unit, None, INVALID, total.get("null"))
                won = elected.filter(pl.col("day") == day, pl.col("region") == code)
                size = int(won["seats"].sum()) if won.height else None
                if own is not None:
                    size = own.size
                contest = cs.key(f"{SOURCE}.contest", f"{code}:{day}:list")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=area,
                    magnitude=size,
                    default_result_kind="counted",
                )
                name(contest, f"{label} {english} regional election, council lists")
                cs.add("slug", path=f"{path}/consiglio", entity_id=contest)
                notes = [AGGREGATED]
                if own is not None:
                    notes = [own.counted, own.allocated]
                elif day.year >= 1995:
                    notes.append(LIST_PART if size is None else LIST_SEATS)
                elif size is not None:
                    notes.append(SEATS_NOTE)
                else:
                    notes.append(NO_SEATS)
                cs.add("note", entity_id=contest, kind="context", body_md=" ".join(notes))
                list_set = result_set(f"{code}:{day}:list", election, contest, source)
                seats_by_list = dict(won.select("list", "seats").iter_rows()) if own is None else own.seats
                counted = list_totals.filter(pl.col("day") == day, pl.col("region") == code)
                regional_lists = sorted(set(seats_by_list) - set(counted["list"]))
                if regional_lists and (own is not None or day.year < 1995):
                    raise ValueError(f"{code} {day}: seats for lists without votes: {regional_lists}")
                for label_ in regional_lists:
                    option = cs.key(f"{SOURCE}.option", f"{code}:{day}:regional:{label_}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_of(label_, day, {}),
                        ballot_label=label_,
                    )
                    cs.add("outcome", result_set_id=list_set, option_id=option, kind="won", seats=seats_by_list[label_])
                for label_, votes in (
                    counted.sort("list_votes", "list", descending=[True, False])
                    .select("list", "list_votes")
                    .iter_rows()
                ):
                    option = cs.key(f"{SOURCE}.option", f"{code}:{day}:list:{label_}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_of(label_, day, {} if own is None else own.parties),
                        ballot_label=label_,
                    )
                    value(list_set, unit, option, VOTES, votes)
                    if seats_by_list.get(label_):
                        cs.add(
                            "outcome", result_set_id=list_set, option_id=option, kind="won", seats=seats_by_list[label_]
                        )
                if size is not None and own is None and day.year < 1995:
                    following = next((other for other in days if other > day), None)
                    cs.add("body_size", body_id=body, seats=size, valid_from=day, valid_to=following)
                running = candidates.filter(pl.col("day") == day, pl.col("region") == code).sort(
                    "candidate_votes", "family", descending=[True, False]
                )
                if running.height == 0:
                    continue
                race = cs.key(f"{SOURCE}.contest", f"{code}:{day}:president")
                cs.add(
                    "contest",
                    id=race,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    magnitude=1,
                    default_result_kind="counted",
                )
                name(race, f"{label} {english} regional election, President")
                cs.add("slug", path=f"{path}/presidente", entity_id=race)
                cs.add(
                    "note",
                    entity_id=race,
                    kind="context",
                    body_md=" ".join(
                        [AGGREGATED if own is None else own.counted, LISTINO if day.year == 1995 else DIRECT]
                    ),
                )
                race_set = result_set(f"{code}:{day}:president", election, race, source)
                top = running["candidate_votes"].max()
                for row in running.iter_rows(named=True):
                    ballot = f"{row['surname']} {row['forename']}".strip()
                    option = cs.key(f"{SOURCE}.option", f"{code}:{day}:president:{ballot}")
                    cs.add("contest_option", id=option, contest_id=race, kind="candidate", ballot_label=ballot)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{code}:{day}:president:{ballot}"),
                        person_id=person_of(row["given"], row["family"], day, code),
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                    )
                    value(race_set, unit, option, VOTES, row["candidate_votes"])
                    if day.year >= 2000 and row["candidate_votes"] == top:
                        cs.add("outcome", result_set_id=race_set, option_id=option, kind="elected")
        cs.frame(
            "result_value",
            pl.DataFrame(rows, schema_overrides={"value": pl.Int64}).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
