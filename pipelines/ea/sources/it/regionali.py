import csv
import gzip
import html
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.it import interno, istat
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES, proper
from ea.sources.it.storico import EXISTING, NEW, cased
from ea.store import DATA, connect

SOURCE = "it.regionali"
VERSION = "2"
KEY = f"{SOURCE}/all"
PARSED = DATA / "parsed" / SOURCE
# The package format has no table for these rows, so they stay with the changesets this pipeline published before it
# moved to packages, which its package adopts.
CHANGESET_ONLY = ("candidacy_nomination",)
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
    "Results are by municipality, from the Interior Ministry's open data file for this election; where the file "
    "reports polling sections, they are summed by municipality."
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
ALIASES = {
    ("01", "CAMAGNA"): "Camagna Monferrato",
    ("01", "CERRETO DELLE LANGHE"): "Cerretto Langhe",
    ("01", "CERRETTO DELLE LANGHE"): "Cerretto Langhe",
    ("01", "CERRINA"): "Cerrina Monferrato",
    ("01", "RORETO CHISONE"): "Roure",
    ("01", "VENARIA"): "Venaria Reale",
    ("01", "VERBANO-CUSIO-OSSOLA"): "Verbania",
    ("01", "VIALE D'ASTI"): "Viale",
    ("03", "COGOLE"): "Cigole",
    ("03", "COMEZZANO-CIZZANO"): "Comezzano-Cizzago",
    ("03", "COSTA DI SERINA"): "Costa Serina",
    ("03", "ISOLATO"): "Madesimo",
    ("03", "MONTICELLO"): "Monticello Brianza",
    ("03", "PUEGNAGO SUL GARDA"): "Puegnago del Garda",
    ("03", "TRUGGIO"): "Triuggio",
    ("03", "ZEME LOMELLINA"): "Zeme",
    ("05", "CARRARA SAN GIORGIO"): "028024",
    ("05", "CARRARA SANTO STEFANO"): "028025",
    ("05", "CASTELLO LAVAZZO"): "Castellavazzo",
    ("05", "DANTA"): "Danta di Cadore",
    ("05", "SANTO STINO DI LIVENZA"): "San Stino di Livenza",
    ("07", "AQUILA DI ARROSCIA"): "Aquila d'Arroscia",
    ("07", "COSIO DI ARROSCIA"): "Cosio d'Arroscia",
    ("08", "FARINI D'OLMO"): "Farini",
    ("08", "FORLI'-CESENA"): "Forlì",
    ("08", "GRIZZANA"): "Grizzana Morandi",
    ("08", "RO FERRARESE"): "Ro",
    ("10", "BASTIA"): "Bastia Umbra",
    ("12", "BOVILLE"): "058121",
    ("12", "CIVITELLA S. PAOLO"): "Civitella San Paolo",
    ("12", "PAGANICO"): "Paganico Sabino",
    ("12", "POZZAGLIA SABINO"): "Pozzaglia Sabina",
    ("14", "RIPALIMOSANO"): "Ripalimosani",
    ("15", "CONTURSI"): "Contursi Terme",
    ("15", "GALLO"): "Gallo Matese",
    ("15", "PIANA DI CAIAZZO"): "Piana di Monte Verna",
    ("15", "TORELLA DE' LOMBARDI"): "Torella dei Lombardi",
    ("16", "CEGLIE MESSAPICO"): "Ceglie Messapica",
    ("18", "CASSANO ALLO IONIO"): "Cassano all'Ionio",
    ("18", "NOCERA TIRINESE"): "Nocera Terinese",
    ("19", "CASTRONUOVO DI SICILIA"): "Castronovo di Sicilia",
}
# it.interno, it.eligendo, and it.eligendo_referendum each hold a source with the ministry's publisher and title, so a
# lookup by name returns whichever the database lists last. This pipeline's result sets cite it.eligendo's.
MINISTRY = UUID("334003ae-d1bb-51ca-8f2d-e31ff156a658")
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
    unaccounted: int = 0


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
    ("11", date(2025, 9, 28)): Regional(
        publisher="Regione Marche",
        title="Elezioni regionali 2025: risultati (regione.marche.it)",
        url="https://www.regione.marche.it/elezioni2025/",
        candidates="https://www.regione.marche.it/elezioni2025/static_json/raggrup_0.json",
        lists="https://www.regione.marche.it/elezioni2025/static_json/liste_0.json",
        allocation="https://www.regione.marche.it/elezioni2025/static_json/provvisorio_seggi.pdf",
        size=30,
        seats={
            "LEGA SALVINI MARCHE": 3,
            "GIORGIA MELONI PER ACQUAROLI - FRATELLI D'ITALIA": 10,
            "FORZA ITALIA BERLUSCONI": 3,
            "CIVICI MARCHE PER ACQUAROLI PRESIDENTE": 1,
            "I MARCHIGIANI PER ACQUAROLI PRESIDENTE": 1,
            "LISTE CIVICHE LIBERTAS UNIONE DI CENTRO": 1,
            "PD - PARTITO DEMOCRATICO": 6,
            "ALLEANZA VERDI E SINISTRA": 1,
            "LISTA CIVICA MATTEO RICCI PRESIDENTE": 2,
            "MOVIMENTO 5 STELLE 2050": 1,
            "PROGETTO MARCHE VIVE MATTEO RICCI PRESIDENTE": 1,
        },
        parties={
            "LEGA SALVINI MARCHE": "LEGA",
            "GIORGIA MELONI PER ACQUAROLI - FRATELLI D'ITALIA": "FRATELLI D'ITALIA",
            "FORZA ITALIA BERLUSCONI": "FORZA ITALIA",
            "PD - PARTITO DEMOCRATICO": "PARTITO DEMOCRATICO",
            "NOI MODERATI PER ACQUAROLI": "NOI MODERATI",
            "DEMOCRAZIA SOVRANA POPOLARE DSP CON MARCO RIZZO": "DEMOCRAZIA SOVRANA POPOLARE",
            "PCI - PARTITO COMUNISTA ITALIANO": "PARTITO COMUNISTA ITALIANO",
        },
        counted=(
            "Results are the Marche Region's count of all 1,572 polling sections, from the results files on its 2025 "
            "election site. The region gives 662,883 voters, one more than the 662,882 valid, blank, null, and "
            "contested ballots it counts."
        ),
        allocated=(
            "The 30 council seats are those of the region's provisional seat allocation by list, which the region "
            "labels unofficial. The seat it marks as due to a candidate for president, Matteo Ricci, the runner-up, is "
            "counted on his own list, Progetto Marche Vive Matteo Ricci Presidente."
        ),
        unaccounted=1,
    ),
}
ELIGENDO = "https://web.archive.org/web/{}id_/https://eleapi.interno.gov.it/siel/PX/{}/DE/{}/TE/{}/RE/{}"
MINISTRY_PUBLISHER = "Ministero dell'Interno"
MINISTRY_SITE = "https://elezioni.interno.gov.it/"
CAMPANIA_SITE = "https://elezioni.regione.campania.it/siel/risultati.html"
NOT_COUNTED = (
    "The president and the runner-up for president, who sit on the council without being elected from a list, are not "
    "counted."
)
ELECTED_NOTE = (
    "Seats are the councillors the Interior Ministry's results site lists as elected, counted by the list they were "
    f"elected from, majority bonus included. {NOT_COUNTED}"
)
ALLOCATED_NOTE = (
    "Seats are the {} allocation of council seats by list on its results site, once every polling section was "
    "counted, majority bonus included; the official proclamation rests with the regional central electoral office. "
    f"{NOT_COUNTED}"
)


@dataclass(frozen=True)
class Seated:
    """Seats by list from a results site: the list of elected councillors (elettiR), the region-wide results with
    each list's seats in the ministry's format (scrutiniR), or a region's results page (table); and the list seats the
    council's size leaves once the president and the runner-up for president take theirs, which the seats must fill."""

    endpoint: str
    url: str
    expected: int
    publisher: str = MINISTRY_PUBLISHER
    title: str = "Eligendo: elezioni regionali, risultati e seggi per lista"
    site: str = MINISTRY_SITE
    note: str = ALLOCATED_NOTE.format("Interior Ministry's")
    body: dict[str, object] | None = None


def eligendo(endpoint: str, captured: str, day: str, kind: str, region: str) -> tuple[str, str]:
    """A Wayback capture of the Interior Ministry's results API; kind is the election type code (07 for regional
    elections, 12 for Tuscany's, which can go to a runoff)."""
    return endpoint, ELIGENDO.format(captured, endpoint, day, kind, region)


# A region-wide capture can predate the proclamation: the ministry's for Tuscany of 15 October 2025 gives 38 list
# seats, so Tuscany comes from its list of elected councillors instead.
ELECTED = {
    ("07", date(2024, 10, 27)): Seated(
        *eligendo("elettiR", "20241109173440", "20241027", "07", "07"),
        29,
        title="Eligendo: elezioni regionali, consiglieri eletti",
        note=ELECTED_NOTE,
    ),
    ("09", date(2025, 10, 12)): Seated(
        *eligendo("elettiR", "20251031204510", "20251012", "12", "09"),
        39,
        title="Eligendo: elezioni regionali, consiglieri eletti",
        note=ELECTED_NOTE,
    ),
    ("13", date(2024, 3, 10)): Seated(*eligendo("scrutiniR", "20240624063011", "20240310", "07", "13"), 29),
    ("17", date(2024, 4, 21)): Seated(*eligendo("scrutiniR", "20240610053857", "20240421", "07", "17"), 19),
    ("18", date(2025, 10, 5)): Seated(*eligendo("scrutiniR", "20251007050359", "20251005", "07", "18"), 29),
    ("05", date(2025, 11, 23)): Seated(*eligendo("scrutiniR", "20260510162251", "20251123", "07", "05"), 49),
    ("16", date(2025, 11, 23)): Seated(*eligendo("scrutiniR", "20251228183525", "20251123", "07", "16"), 49),
    ("08", date(2024, 11, 17)): Seated(
        "table",
        "https://alservizi.regione.emilia-romagna.it/elezioni2024/Risultati",
        48,
        publisher="Regione Emilia-Romagna",
        title="Elezioni regionali 2024: riepilogo regionale dei risultati (alservizi.regione.emilia-romagna.it)",
        site="https://www.regione.emilia-romagna.it/elezioni/elezioni-regionali-del-17-e-18-novembre-2024/risultati",
        note=ALLOCATED_NOTE.format("Emilia-Romagna Region's"),
    ),
    ("15", date(2025, 11, 23)): Seated(
        "scrutiniR",
        "https://elezioni.regione.campania.it/siel/sieljson.asmx/GetJson",
        49,
        publisher="Regione Campania",
        title="Elezioni regionali 2025: risultati (elezioni.regione.campania.it)",
        site=CAMPANIA_SITE,
        note=ALLOCATED_NOTE.format("Campania Region's"),
        body={"livello": "scrutinio_reg", "circ": "0", "com": "0", "sezione": "0"},
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


def elected(region: str, day: date, counted: dict[str, int]) -> dict[str, int]:
    """Seats by list from a results site: the list of elected councillors, checked against the councillors it names,
    or the region-wide results, checked against each coalition's seats. Either way the seats must fill the council but
    for the president and the runner-up."""
    held = ELECTED[region, day]
    body = raw.cached(SOURCE, held.url, held.body).path.read_bytes()
    if held.endpoint == "table":
        seats = tabled(region, day, body.decode(), counted)
    else:
        data = json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)
        if "d" in data:
            # The Campania Region's web service wraps the ministry's document as a string under "d".
            data = json.loads(data["d"]) if isinstance(data["d"], str) else data["d"]
        seats = allocated(region, day, data, counted) if held.endpoint == "scrutiniR" else named(region, day, data)
    if sum(seats.values()) != held.expected:
        raise ValueError(f"{region} {day}: {sum(seats.values())} list seats, where the council leaves {held.expected}")
    return seats


def named(region: str, day: date, data: dict) -> dict[str, int]:
    if data["int"]["f_elet"] != "ELETTI" or int(data["int"]["cod_reg"]) != int(region):
        raise ValueError(f"{region} {day}: not the list of elected councillors for this region")
    seats: dict[str, int] = {}
    for coalition in data["coal"]:
        lists = coalition["liste"] or []
        named = [person["cod_lis"] for person in coalition["cand"] or []]
        if sum(entry["n_eletti"] for entry in lists) != coalition["n_eletti"] or len(named) != coalition["n_eletti"]:
            raise ValueError(f"{region} {day}: {coalition['cogn']}'s lists do not add up to its elected councillors")
        for entry in lists:
            if named.count(entry["pos"]) != entry["n_eletti"]:
                raise ValueError(f"{region} {day}: {entry['desc_lis']} names a different number of councillors")
            if entry["n_eletti"]:
                seats[entry["desc_lis"].strip()] = entry["n_eletti"]
    return seats


def matched(region: str, day: date, label: str, votes: int, counted: dict[str, int]) -> str:
    """The open data's label for a list: the same label, or the one list with the same votes where the open data
    shortens the label."""
    if label in counted:
        return label
    matches = [other for other, count in counted.items() if count == votes]
    if len(matches) != 1:
        raise ValueError(f"{region} {day}: {label} matches no list of the open data by its votes")
    return matches[0]


def tabled(region: str, day: date, page: str, counted: dict[str, int]) -> dict[str, int]:
    """Seats by list from a region's results page that prints each coalition's candidate, lists, and total seats
    once every section is counted; each coalition's list seats and its candidate's seat must make its total."""
    scrutinized = re.findall(r"scrutinate:\s*(\d+)\s+su\s+(\d+)", re.sub(r"<[^>]+>", " ", page))
    if not scrutinized or any(done != of for done, of in scrutinized):
        raise ValueError(f"{region} {day}: the results page does not count every section")
    seats: dict[str, int] = {}
    owned = 0
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)
        ]
        if len(cells) < 4 or cells[0] == "Candidato e liste":
            continue
        won = int(cells[-1]) if cells[-1].isdigit() else 0
        if cells[0] == "TOTALI COALIZIONE":
            if owned != won:
                raise ValueError(f"{region} {day}: a coalition's seats do not add up to its total")
        elif cells[0]:
            owned = won
        else:
            owned += won
            if won:
                seats[matched(region, day, cells[1], int(cells[2].replace(".", "")), counted)] = won
    return seats


def allocated(region: str, day: date, data: dict, counted: dict[str, int]) -> dict[str, int]:
    """Seats by list from the region-wide results once the allocation is final. Each list is matched to the open data
    by its label, or by its votes where the open data shortens the label."""
    head = data["int"]
    if head["f_elet"] != "SCRUTINI" or int(head["cod_reg"]) != int(region):
        raise ValueError(f"{region} {day}: not the region-wide results for this region")
    if head["fine_rip"] != "S" or head["sz_cons"] != head["sz_tot"] or head["sg_ass_r"]:
        raise ValueError(f"{region} {day}: the seat allocation is not final, or seats go to a regional list")
    seats: dict[str, int] = {}
    for candidate in data["cand"]:
        lists = candidate["liste"] or []
        if sum(entry["seggi"] or 0 for entry in lists) != (candidate["sg_ass_c"] or 0):
            raise ValueError(f"{region} {day}: {candidate['cogn']}'s lists do not add up to the coalition's seats")
        for entry in lists:
            if entry["seggi"]:
                seats[matched(region, day, entry["desc_lis_c"].strip(), entry["voti"], counted)] = entry["seggi"]
    if sum(seats.values()) != head["sg_ass_c"]:
        raise ValueError(f"{region} {day}: list seats do not add up to the seats allocated to coalitions")
    return seats


def folded(text: str) -> str:
    return interno.key(text).replace("J", "I")


def municipalities() -> tuple[Callable[[str, str, date], str], pl.DataFrame, pl.DataFrame]:
    with raw.cached(interno.SOURCE, interno.ISTAT).path.open(encoding="latin-1", newline="") as file:
        current = {row[4]: row for row in list(csv.reader(file, delimiter=";"))[1:]}
    with zipfile.ZipFile(raw.cached(istat.SOURCE, istat.VARIATIONS).path) as archive:
        changes = list(csv.reader(io.StringIO(archive.read(istat.VARIATIONS_CSV).decode("cp1252")), delimiter=";"))[1:]
    named: dict[tuple[str, str], set[str]] = {}
    for row in current.values():
        for text in {row[5], row[6], row[7]} - {""}:
            named.setdefault((row[0], folded(text)), set()).add(row[4])
    moved: dict[str, str] = {}
    ended: dict[str, tuple[str, str, date | None]] = {}
    created: dict[str, date] = {}
    origins: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    regions: list[tuple[str, str, str, date]] = []
    for year, kind, region, _, code, label, other_region, _, other, _, _, _, day, *_ in changes:
        kind = kind.strip()
        on = datetime.strptime(day, "%d/%m/%Y").date() if day else None
        if kind in ("CD", "AP", "ES"):
            named.setdefault((region, folded(label)), set()).add(code)
            labels.setdefault(code, label)
        if kind == "AP":
            moved[code] = other
            if region != other_region and on is not None:
                regions.append((other, region, other_region, on))
        if kind == "ES" and code not in current:
            ended[code] = (label, region, on)
        if kind == "CS":
            created[code] = on or date(int(year), 1, 1)
            origins.setdefault(code, []).append(other)
        if kind == "CECS":
            created[other] = on or date(int(year), 1, 1)

    def last(code: str) -> str:
        seen = set()
        while code not in current and code in moved and code not in seen:
            seen.add(code)
            code = moved[code]
        return code

    def resolve(region: str, label: str, day: date) -> str:
        alias = ALIASES.get((region, label))
        if alias is not None and alias.isdigit():
            return last(alias)
        wanted = folded(alias or label)
        found: set[str] = set()
        for code in named.get((region, wanted), set()):
            code = last(code)
            if code in ended and (ended[code][2] or date.max) <= day:
                continue
            if code in created and created[code] > day:
                found |= {last(o) for o in origins.get(code, []) if folded(labels.get(o, "")) == wanted}
                continue
            found.add(code)
        if len(found) != 1:
            raise ValueError(f"{day}: {label} in region {region} matches municipalities {sorted(found)}")
        return found.pop()

    extinct = pl.DataFrame(
        [(code, label, region, on) for code, (label, region, on) in ended.items()],
        schema={"code": pl.String, "name": pl.String, "region": pl.String, "ended": pl.Date},
        orient="row",
    )
    transferred = pl.DataFrame(
        regions, schema={"code": pl.String, "before": pl.String, "after": pl.String, "on": pl.Date}, orient="row"
    )
    return resolve, extinct, transferred


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")
    for held in REGIONAL.values():
        raw.fetch(SOURCE, held.candidates, suffix=".json")
        raw.fetch(SOURCE, held.lists, suffix=".json")
        raw.fetch(SOURCE, held.allocation, suffix=".pdf")
    for seated in ELECTED.values():
        raw.fetch(SOURCE, seated.url, suffix=".html" if seated.endpoint == "table" else ".json", body=seated.body)


def regional(code: str, day: date, held: Regional) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    groups = json.loads(raw.cached(SOURCE, held.candidates).path.read_bytes())
    lists = json.loads(raw.cached(SOURCE, held.lists).path.read_bytes())
    totals = groups["votiRaggrup"]
    if int(totals["tot_sezio"]) != groups["totSezioni"]["TOT_SEZIO"] or lists["votiListe"]["tot_sezio"] != int(
        totals["tot_sezio"]
    ):
        raise ValueError(f"{code} {day}: the count is not final")
    if (
        totals["tvotival"] + totals["tbianche"] + totals["tnulle"] + totals["sk_contestate"]
        != totals["tvotanti"] - held.unaccounted
    ):
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
    named = {
        entry["nlistamacro"]: entry["nome"].strip() for group in groups["anagrRaggrup"] for entry in group["arrListe"]
    }
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
    pairs = pl.DataFrame(
        [
            {
                "day": day,
                "region": code,
                "list": named[entry["nlistamacro"]],
                "surname": group["cognome_cand"].strip().upper(),
                "forename": group["nome_cand"].strip().upper(),
            }
            for group in groups["anagrRaggrup"]
            for entry in group["arrListe"]
        ]
    )
    return accounting, list_rows, candidates, pairs


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
    links: list[pl.DataFrame] = []
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
                    per_place.group_by("day", "region", "COMUNE").agg(
                        [pl.col(c).sum() for c in COUNTS if c in frame.columns], places=pl.len()
                    )
                )
            if "list" in frame.columns:
                lists.append(
                    frame.filter(pl.col("list") != "")
                    .group_by("day", "region", "COMUNE", "list")
                    .agg(pl.col("list_votes").sum(), places=pl.len())
                )
            if "candidate_votes" in frame.columns and day.year >= 1995:
                per_place = frame.select(*places, "region", "day", "surname", "forename", "candidate_votes").unique()
                if per_place.select(*places, "surname", "forename").is_duplicated().any():
                    raise ValueError(f"{day} {member}: candidate votes differ within a place")
                candidates.append(
                    per_place.group_by("day", "region", "COMUNE", "surname", "forename").agg(
                        pl.col("candidate_votes").sum()
                    )
                )
                links.append(
                    frame.filter(pl.col("list") != "").select("day", "region", "list", "surname", "forename").unique()
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
        counts, by_list, running, pairs = regional(code, day, held)
        accounting.append(counts)
        lists.append(by_list)
        candidates.append(running)
        links.append(pairs)
    linked = pl.concat(links).unique()
    clashes = linked.filter(pl.len().over("day", "region", "list") > 1)
    if clashes.height:
        raise ValueError(
            f"lists linked to more than one presidential candidate: {clashes.sort('day', 'region').rows()}"
        )
    resolve, extinct, transferred = municipalities()
    labels = pl.concat(
        [
            frame.select("day", "region", "COMUNE")
            for frame in accounting + lists + candidates
            if "COMUNE" in frame.columns
        ]
    ).unique()
    located = labels.with_columns(
        code=pl.struct("region", "COMUNE", "day").map_elements(
            lambda row: resolve(row["region"], row["COMUNE"], row["day"]), return_dtype=pl.String
        )
    )
    clashes = located.filter(pl.len().over("day", "region", "code") > 1)
    if clashes.height:
        raise ValueError(f"municipalities named twice in one election: {clashes.sort('code').rows()}")

    def coded(frames: list[pl.DataFrame]) -> pl.DataFrame:
        return (
            pl.concat(frames, how="diagonal_relaxed")
            .join(located, on=["day", "region", "COMUNE"], how="left")
            .drop("COMUNE")
        )

    extinct.write_parquet(PARSED / "extinct.parquet")
    transferred.write_parquet(PARSED / "transferred.parquet")
    coded(accounting).write_parquet(PARSED / "accounting.parquet")
    coded(lists).write_parquet(PARSED / "lists.parquet")
    coded(candidates).with_columns(
        pl.struct("region", "surname", "forename")
        .map_elements(lambda row: list(person_name(row)), return_dtype=pl.List(pl.String))
        .alias("named")
    ).with_columns(given=pl.col("named").list.get(0), family=pl.col("named").list.get(1)).drop("named").write_parquet(
        PARSED / "candidates.parquet"
    )
    pl.concat(seats).write_parquet(PARSED / "seats.parquet")
    linked.write_parquet(PARSED / "links.parquet")
    build()


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    extinct = pl.read_parquet(PARSED / "extinct.parquet")
    transferred = pl.read_parquet(PARSED / "transferred.parquet")
    nominated = {
        (day, region, surname, forename): lists
        for day, region, surname, forename, lists in pl.read_parquet(PARSED / "links.parquet")
        .group_by("day", "region", "surname", "forename")
        .agg(pl.col("list"))
        .iter_rows()
    }
    totals = accounting.group_by("day", "region").agg(pl.all().exclude("day", "region", "code").sum())
    list_totals = lists.group_by("day", "region", "list").agg(pl.col("list_votes").sum())
    candidate_totals = candidates.group_by("day", "region", "surname", "forename", "given", "family").agg(
        pl.col("candidate_votes").sum()
    )
    flagged = seats.filter(
        (pl.col("day").dt.year() >= 1995) | pl.col("seats").sum().over("day", "region").is_in(COUNCIL_SIZES)
    )
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian regional council and presidential elections 1970–2025 from the Interior Ministry's open data and the regions' own results, by municipality",
            polity="IT",
        )
        cs.accept(
            "accounting.turnout",
            "In 64 municipality rows of the Interior Ministry's open data files, the voters printed exceed the "
            "registered electors printed; the figures are kept as published.",
        )

        def fresh(entity: UUID) -> bool:
            return entity in cs.minted or entity in cs.own_entities

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        if cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data") in cs.minted:
            raise ValueError("publish it.interno before it.regionali")
        ministry = cs.survivors.get(MINISTRY, MINISTRY)
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), ministry)
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
        seat_sources: dict[tuple[str, date], UUID] = {}
        for place, seated in ELECTED.items():
            seat_sources[place] = cs.source(seated.publisher, seated.title)
            cs.add(
                "source",
                id=seat_sources[place],
                kind="official_body",
                publisher=seated.publisher,
                title=seated.title,
                url=seated.site,
            )
            cs.artifact(raw.cached(SOURCE, seated.url, seated.body), seat_sources[place])
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

        links: list[tuple[date, str, str, str, str, str | None]] = []
        units: dict[str, str] = {}
        ended = {row[0]: row for row in extinct.iter_rows()}
        for code in sorted(
            {*accounting["code"].drop_nulls(), *lists["code"].drop_nulls(), *candidates["code"].drop_nulls()}
        ):
            unit = cs.unit("it.comune", code)
            units[code] = str(unit)
            if not fresh(unit):
                continue
            _, label, region, until = ended[code]
            area = cs.area("it.comune", code)
            if fresh(area):
                cs.add("area", id=area, scheme_code="it.comune")
                cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=until)
                cs.add("area_parent", area_id=area, parent_id=cs.area("it.regione", region), valid_from=None)
                name(area, label, "it")
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=italy,
                scheme_code="it.comune",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_to=until,
            )
            name(unit, label, "it")
        for code, before, after, on in transferred.filter(pl.col("code").is_in(list(units))).iter_rows():
            area = cs.area("it.comune", code)
            cs.add("area_parent", area_id=area, parent_id=cs.area("it.regione", before), valid_from=None, valid_to=on)
            cs.add("area_parent", area_id=area, parent_id=cs.area("it.regione", after), valid_from=on, valid_to=None)

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
            units[f"regione:{code}"] = str(unit)
            if fresh(region):
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
                own = REGIONAL.get((code, day))
                aliases = {} if own is None else own.parties
                source = ministry if own is None else regional_sources[code, day]
                accounting_set = result_set(f"{code}:{day}:election", election, None, source)
                links.append((day, code, "election", "", str(accounting_set), None))
                won = flagged.filter(pl.col("day") == day, pl.col("region") == code)
                size = int(won["seats"].sum()) if won.height else None
                counted = list_totals.filter(pl.col("day") == day, pl.col("region") == code)
                listed = (
                    elected(code, day, dict(counted.select("list", "list_votes").iter_rows()))
                    if (code, day) in ELECTED
                    else None
                )
                if listed is not None:
                    if won.height or own is not None:
                        raise ValueError(f"{code} {day}: seats from more than one source")
                    size = sum(listed.values())
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
                elif listed is not None:
                    notes.append(ELECTED[code, day].note)
                    cs.add("citation", entity_id=contest, source_id=seat_sources[code, day])
                elif day.year >= 1995:
                    notes.append(LIST_PART if size is None else LIST_SEATS)
                elif size is not None:
                    notes.append(SEATS_NOTE)
                else:
                    notes.append(NO_SEATS)
                cs.add("note", entity_id=contest, kind="context", body_md=" ".join(notes))
                list_set = result_set(f"{code}:{day}:list", election, contest, source)
                seats_by_list = (
                    own.seats
                    if own is not None
                    else listed
                    if listed is not None
                    else dict(won.select("list", "seats").iter_rows())
                )
                regional_lists = sorted(set(seats_by_list) - set(counted["list"]))
                if regional_lists and (own is not None or listed is not None or day.year < 1995):
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
                        party_id=party_of(label_, day, aliases),
                        ballot_label=label_,
                    )
                    links.append((day, code, "list", label_, str(list_set), str(option)))
                    if seats_by_list.get(label_):
                        cs.add(
                            "outcome", result_set_id=list_set, option_id=option, kind="won", seats=seats_by_list[label_]
                        )
                if size is not None and own is None and day.year < 1995:
                    following = next((other for other in days if other > day), None)
                    cs.add("body_size", body_id=body, seats=size, valid_from=day, valid_to=following)
                running = candidate_totals.filter(pl.col("day") == day, pl.col("region") == code).sort(
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
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{code}:{day}:president:{ballot}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person_of(row["given"], row["family"], day, code),
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                    )
                    for party in dict.fromkeys(
                        party_of(label_, day, aliases)
                        for label_ in nominated.get((day, code, row["surname"], row["forename"]), [])
                    ):
                        cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
                    links.append((day, code, "president", ballot, str(race_set), str(option)))
                    if day.year >= 2000 and row["candidate_votes"] == top:
                        cs.add("outcome", result_set_id=race_set, option_id=option, kind="elected")
        keys = pl.DataFrame(
            links,
            schema={
                "day": pl.Date,
                "region": pl.String,
                "kind": pl.String,
                "label": pl.String,
                "result_set_id": pl.String,
                "option_id": pl.String,
            },
            orient="row",
        )
        measures = {"registered": REGISTERED, "voters": VOTERS, "blank": BLANK, "null": INVALID}
        place = pl.coalesce("code", pl.lit("regione:") + pl.col("region")).replace_strict(units).alias("unit_id")
        values = pl.concat(
            [
                accounting.unpivot(list(measures), index=["day", "region", "code"], variable_name="measure")
                .drop_nulls("value")
                .with_columns(
                    kind=pl.lit("election"), label=pl.lit(""), measure=pl.col("measure").replace_strict(measures)
                ),
                lists.select(
                    "day",
                    "region",
                    "code",
                    kind=pl.lit("list"),
                    label="list",
                    measure=pl.lit(VOTES),
                    value="list_votes",
                ),
                candidates.select(
                    "day",
                    "region",
                    "code",
                    kind=pl.lit("president"),
                    label=pl.concat_str("surname", pl.lit(" "), "forename").str.strip_chars(),
                    measure=pl.lit(VOTES),
                    value="candidate_votes",
                ),
            ],
            how="diagonal_relaxed",
        ).join(keys, on=["day", "region", "kind", "label"], how="left")
        if values["result_set_id"].null_count():
            raise ValueError(f"values without a result set: {values.filter(pl.col('result_set_id').is_null()).head()}")
        cs.frame(
            "result_value",
            values.select(
                "result_set_id",
                place,
                "option_id",
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                value=pl.col("value").cast(pl.Int64),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        for table in CHANGESET_ONLY:
            for staged in (cs.rows, cs.frames, cs.chunks):
                staged.pop(table, None)
        return cs.write()
