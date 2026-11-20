"""Sub-municipal councils and presidents of Italian cities (TUEL art. 17): Genoa's nine municipi and Bologna's six
quartieri, from the cities' own open data.

Each city publishes its sub-municipal results on its own portal, one file or a few per year, in its own layout. This
module reads them as published and builds one package per election, attached to the city's municipal election that the
archive already holds, plus a `geo` package for the areas and a `registry` package for the bodies and offices.
"""

import csv
import hashlib
import io
import json
import os
import re
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from ea import raw
from ea.package import NAMESPACE, Package
from ea.store import DATA

SOURCE = "it.municipi"
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"  # production's Italy polity, from the reference partition
MANUAL = DATA / "inbox" / "manual" / "MF-municipi"

GENOA_POLITY = "85b976d9-b10a-540b-93d9-7724fb08f494"
GENOA_AREA = "01a13bf7-2fa2-70fa-bb7c-df264bcb1efb"
BOLOGNA_POLITY = "5736b95d-7196-53ae-85d0-962100d8c757"
BOLOGNA_AREA = "01a13bf7-2fa3-700a-898b-794d67af9489"
# The municipal elections the archive already holds, by city and year.
ELECTIONS = {
    ("genova", 2007): ("41e4f6ed-3fee-5b6d-a94d-eed80d5aef6b", date(2007, 5, 27)),
    ("genova", 2012): ("576a2b6e-6eea-57bb-9737-b5daff5f489b", date(2012, 5, 6)),
    ("genova", 2017): ("ca028fb5-c761-5df9-942d-79df246844dc", date(2017, 6, 11)),
    ("genova", 2025): ("27c6453f-70c2-5e02-8468-4f7d3ca13f95", date(2025, 5, 25)),
    ("bologna", 2021): ("0401bdd1-eb81-59f1-9cbb-7b98ec33920e", date(2021, 10, 3)),
}
GENOA_DATA = "https://dati.comune.genova.it/dataset/50468b9c-a244-4d77-8796-25aecf6f1503/resource"
GENOA_S3 = "https://opendatacomunegenova.s3.eu-south-1.amazonaws.com/risorse"
G2025 = f"{GENOA_DATA}/3769adc1-da7b-4d17-b88d-9a53d8003926/download/municipi.zip"
G2012_LISTS = f"{GENOA_S3}/Municipi%20Liste_0.zip"
G2012_PREFS = f"{GENOA_S3}/Municipi%20Preferenze%20.zip"
G2017 = "browser://20261120-040628-6b0e4f/voti_validi_municipio_{n}.csv"
G2007 = "browser://20261120-040628-6b0e4f/2007Amm-Municipio{name}.csv"
B_LISTS = "browser://20261120-040628-6b0e4f/elezioni-amministrative-2021-consigli-circoscrizionali-voti-alle-liste.csv"
B_QUARTIERE = (
    "browser://20261120-040628-6b0e4f/elezioni-amministrative-2021-consigli-circoscrizionali-quartiere-{slug}.csv"
)
GENOA_SOURCE = str(uuid5(NAMESPACE_URL, "https://dati.comune.genova.it/"))
BOLOGNA_SOURCE = str(uuid5(NAMESPACE_URL, "https://opendata.comune.bologna.it/"))
REGISTERED, VALID, INVALID, BLANK, VOTES, VOTERS, PREFERENCES = 2, 4, 5, 6, 1, 14, 7

# Genoa's municipi, in the order of their numbers, with the names the city prints in its 2007 files.
MUNICIPI = [
    ("1", "Centro Est", "Centroest"),
    ("2", "Centro Ovest", "CentroOvest"),
    ("3", "Bassa Val Bisagno", "BassaValBisagno"),
    ("4", "Media Val Bisagno", "MediaValBisagno"),
    ("5", "Val Polcevera", "ValPolcevera"),
    ("6", "Medio Ponente", "MedioPonente"),
    ("7", "Ponente", "Ponente"),
    ("8", "Medio Levante", "MedioLevante"),
    ("9", "Levante", "Levante"),
]
ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII", "8": "VIII", "9": "IX"}
# Bologna's six quartieri, as the city prints them in the section files, with the slug of its dataset names.
QUARTIERI = [
    ("1", "Borgo Panigale - Reno", "BORGO PANIGALE - RENO", "borgo-panigale-"),
    ("2", "Navile", "NAVILE", "navile"),
    ("3", "Porto - Saragozza", "PORTO - SARAGOZZA", "porto-saragozza"),
    ("4", "San Donato - San Vitale", "SAN DONATO - SAN VITALE", "san-donato-san-"),
    ("5", "Santo Stefano", "SANTO STEFANO", "santo-stefano"),
    ("6", "Savena", "SAVENA", "savena"),
]
METHOD = {
    "genova": (
        "Valid votes counted per polling section, as published in the City of Genoa's open data. Votes for a list "
        "are valid votes for that list; preference votes are the votes marked for a candidate on the list and are "
        "not added to the list's votes."
    ),
    "bologna": (
        "Votes counted per polling section, as published in the City of Bologna's open data. The city prints each "
        "list and candidate as a column name in lower case with underscores, and a long name is cut off; the labels "
        "here are those names with capitals restored, not the ballot's own spelling. A voter may mark preferences "
        "for candidates of the chosen list, and those are not added to the list's votes."
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def municipio_area(number: str) -> str:
    return key("area", "genova", number)


def quartiere_area(number: str) -> str:
    return key("area", "bologna", number)


def fetch() -> None:
    """Store the files a person saved from the city portals into the raw cache, keyed by their source URL."""
    index = json.loads((MANUAL / "index.json").read_text())
    for url, entry in index.items():
        content = (MANUAL / entry["file"]).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if entry.get("sha256") and digest != entry["sha256"]:
            raise ValueError(f"{entry['file']} ({url}) does not match its sha256")
        suffix = Path(entry["file"]).suffix
        raw.store(
            SOURCE,
            entry.get("source", url),
            content,
            suffix=suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def inbox(base: str, folder: str, user: str, password: str) -> None:
    """Copy a folder of saved files, with its index.json, from a WebDAV share into the manual-download folder."""
    import base64

    token = base64.b64encode(f"{user}:{password}".encode()).decode()

    def get(name: str) -> bytes:
        request = urllib.request.Request(f"{base}/{folder}/{name}", headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()

    MANUAL.mkdir(parents=True, exist_ok=True)
    index = json.loads(get("index.json"))
    (MANUAL / "index.json").write_text(json.dumps(index))
    for entry in index.values():
        (MANUAL / entry["file"]).write_bytes(get(entry["file"]))


def stage() -> None:
    base, user, password = os.environ["EA_INBOX_URL"], os.environ["EA_INBOX_USER"], os.environ["EA_INBOX_PASSWORD"]
    inbox(base, "fetched/2619", user, password)
    fetch()


def text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read(url: str) -> bytes:
    return raw.cached(SOURCE, url).path.read_bytes()


def zipped(url: str) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(read(url))) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}


def rows(data: bytes) -> list[list[str]]:
    body = text(data)
    head = body.split("\n", 1)[0]
    delimiter = max(";\t,", key=head.count)
    return [row for row in csv.reader(io.StringIO(body), delimiter=delimiter) if any(cell.strip() for cell in row)]


def count(value: str | None) -> int | None:
    """A count as the city prints it: blank for none, and a dot as the thousands mark in the 2007 files."""
    cleaned = (value or "").strip().replace(".", "")
    return int(cleaned) if cleaned else None


def title(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.split("_"))


def person(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip()


class Table:
    """One city file: identifying columns, then numbered list or candidate columns, then the printed total."""

    def __init__(self, data: bytes) -> None:
        table = rows(data)
        self.header = [cell.strip() for cell in table[0]]
        body = [[cell.strip() for cell in row] for row in table[1:]]
        self.section = next(i for i, name in enumerate(self.header) if "SEZIONE" in name.upper())
        self.total = next(i for i, name in enumerate(self.header) if name.upper() == "TOTALI")
        self.rows = [row for row in body if len(row) > self.total and row[self.section].isdigit()]
        # Some files end with a printed row of totals per column, which parsing checks and keeps as declared.
        self.printed = next(
            (row for row in body if len(row) > self.total and row[self.section].lower() == "totali"), None
        )
        self.columns: list[tuple[int, int, str]] = []
        for i in range(self.section + 1, self.total):
            match = re.match(r"(?:LISTA|N)\s+(\d+)\s*(?:[|\-\u2013\u2014\ufffd]\s*)?(.*)", self.header[i], re.DOTALL)
            if match is None:
                raise ValueError(f"unexpected column {self.header[i]!r}")
            self.columns.append((i, int(match.group(1)), person(match.group(2)).strip('"')))

    def sections(self) -> list[tuple[str, dict[int, int], int | None, int | None]]:
        out = []
        for row in self.rows:
            if len(row) <= self.total or not row[self.section]:
                continue
            values = {number: count(row[i]) or 0 for i, number, _ in self.columns}
            out.append((str(int(row[self.section])), values, count(row[self.total]), None))
        return out


def number(label: str) -> str:
    return str(int(label))


def contest_name(city: str, name: str, year: int, kind: str) -> str:
    word = "Municipio" if city == "genova" else "Quartiere"
    return f"{year} {city.capitalize()} {word} {name} {kind}"


def geo() -> Package:
    p = Package(
        "it.municipi/geo",
        SOURCE,
        "Genoa's nine municipi and Bologna's six quartieri, as areas without shapes",
    )
    p.external |= {ITALY, GENOA_AREA, BOLOGNA_AREA}
    for scheme, rank, noun in (("it.genova_municipio", 6, "municipio"), ("it.bologna_quartiere", 6, "quartiere")):
        p.add("scheme", code=scheme, polity_id=ITALY, kind="administrative", rank=rank, noun=noun)
    for scheme, rank, noun in (
        ("it.genova_sezione", 7, "polling section"),
        ("it.bologna_sezione", 7, "polling section"),
    ):
        p.add("scheme", code=scheme, polity_id=ITALY, kind="precinct", rank=rank, noun=noun)
    for number_, name, _ in MUNICIPI:
        area = municipio_area(number_)
        p.add("area", id=area, scheme_code="it.genova_municipio")
        p.add("area_code", area_id=area, code=number_)
        p.add("area_parent", area_id=area, parent_id=GENOA_AREA)
        p.name(area, f"Municipio {ROMAN[number_]} {name}", "it", "official")
        p.name(area, f"Genoa Municipio {ROMAN[number_]} {name}", "en", "english", False)
    for number_, name, _, _ in QUARTIERI:
        area = quartiere_area(number_)
        p.add("area", id=area, scheme_code="it.bologna_quartiere")
        p.add("area_code", area_id=area, code=number_)
        p.add("area_parent", area_id=area, parent_id=BOLOGNA_AREA)
        p.name(area, f"Quartiere {name}", "it", "official")
        p.name(area, f"Bologna Quartiere {name}", "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        "it.municipi/registry",
        SOURCE,
        "The councils of Genoa's municipi and Bologna's quartieri, and the presidents of Genoa's municipi",
        depends=["it.municipi/geo"],
    )
    p.external |= {ITALY, GENOA_POLITY, BOLOGNA_POLITY}
    p.add(
        "source",
        id=GENOA_SOURCE,
        kind="official_body",
        publisher="Comune di Genova",
        title="Genoa open data: elections",
        url="https://dati.comune.genova.it/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Comune di Genova, dati.comune.genova.it",
    )
    p.add(
        "source",
        id=BOLOGNA_SOURCE,
        kind="official_body",
        publisher="Comune di Bologna",
        title="Bologna open data: administrative elections, neighborhood councils",
        url="https://opendata.comune.bologna.it/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Comune di Bologna, opendata.comune.bologna.it",
    )
    for number_, name, _ in MUNICIPI:
        body = key("body", "genova", number_)
        p.add("body", id=body, polity_id=GENOA_POLITY, kind="council")
        p.name(body, f"Municipio {ROMAN[number_]} {name} Council", "en", "english")
        p.name(body, f"Consiglio del Municipio {ROMAN[number_]} {name}", "it", "native", False)
        office = key("office", "genova", number_)
        p.add("office", id=office, polity_id=GENOA_POLITY, body_id=body, kind="other")
        p.name(office, f"President of Municipio {ROMAN[number_]} {name}", "en", "english")
        p.name(office, f"Presidente del Municipio {ROMAN[number_]} {name}", "it", "native", False)
    for number_, name, _, _ in QUARTIERI:
        body = key("body", "bologna", number_)
        p.add("body", id=body, polity_id=BOLOGNA_POLITY, kind="council")
        p.name(body, f"Quartiere {name} Council", "en", "english")
        p.name(body, f"Consiglio del Quartiere {name}", "it", "native", False)
    return p


class Builder:
    """Collects one election's contests, options, units, and values into a package."""

    def __init__(self, city: str, year: int, summary: str) -> None:
        self.city, self.year = city, year
        self.election, self.day = ELECTIONS[(city, year)]
        self.p = Package(
            f"it.municipi/{city}-{year}", SOURCE, summary, depends=["it.municipi/geo", "it.municipi/registry"]
        )
        self.p.external |= {ITALY, self.election}
        self.source = GENOA_SOURCE if city == "genova" else BOLOGNA_SOURCE
        self.units: dict[str, str] = {}
        self.sets: dict[str, str] = {}
        self.options: dict[tuple[str, str], str] = {}

    def use(self, url: str) -> None:
        self.p.artifact(raw.cached(SOURCE, url), self.source)

    def unit(self, area: str, section: str) -> str:
        scheme = f"it.{self.city}_sezione"
        code = f"{self.day}:{section}"
        if code not in self.units:
            unit = self.units[code] = key("unit", self.city, code)
            self.p.external.add(area)
            self.p.add(
                "reporting_unit",
                id=unit,
                polity_id=ITALY,
                scheme_code=scheme,
                code=code,
                area_relation="within",
                area_id=area,
                kind="standard",
            )
        return self.units[code]

    def contest(self, area_number: str, kind: str, label: str) -> str:
        """Open the council contest (`kind` council) or the president's contest of one municipio or quartiere."""
        slot = f"{area_number}:{kind}"
        if slot in self.sets:
            return slot
        p = self.p
        is_council = kind == "council"
        contest = key("contest", self.city, self.year, area_number, kind)
        area = municipio_area(area_number) if self.city == "genova" else quartiere_area(area_number)
        body = key("body", self.city, area_number)
        p.external.add(area)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind="list_allocation" if is_council else "office",
            office_id=None if is_council else key("office", self.city, area_number),
            body_id=body if is_council else None,
            district_area_id=area,
            round_number=1,
            default_result_kind="counted",
        )
        what = "council election" if is_council else "presidential election"
        name = contest_name(self.city, label, self.year, what)
        p.name(contest, name, "en", "english")
        result_set = key("result_set", self.city, self.year, area_number, kind)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=self.source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD[self.city],
            lang="en",
        )
        self.sets[slot] = result_set
        self.options[(slot, "contest")] = contest
        return slot

    def option(self, slot: str, number_: int | str, kind: str, label: str, parent: str | None = None) -> str:
        id_key = (slot, f"{kind}:{number_}")
        if id_key not in self.options:
            option = self.options[id_key] = key("option", self.city, self.year, slot, kind, number_)
            self.p.add(
                "contest_option",
                id=option,
                contest_id=self.options[(slot, "contest")],
                kind=kind,
                parent_option_id=parent,
                ballot_position=int(number_) if str(number_).isdigit() else None,
                ballot_label=label,
            )
        return self.options[id_key]

    def value(self, slot: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add(
            "result_value",
            result_set_id=self.sets[slot],
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )


def genova_lists(b: Builder, slot_kind: str, table: Table, area_number: str, label: str) -> str:
    slot = b.contest(area_number, slot_kind, label)
    area = municipio_area(area_number)
    for _, number_, name in table.columns:
        b.option(slot, number_, "list" if slot_kind == "council" else "candidate", name)
    for section, values, total, _ in table.sections():
        unit = b.unit(area, section)
        for number_, votes in values.items():
            kind = "list" if slot_kind == "council" else "candidate"
            b.value(slot, unit, b.options[(slot, f"{kind}:{number_}")], VOTES, votes)
        b.value(slot, unit, None, VALID, total)
    if table.printed is not None:
        kind = "list" if slot_kind == "council" else "candidate"
        for i, number_, _ in table.columns:
            b.p.add(
                "declared",
                result_set_id=b.sets[slot],
                option_id=b.options[(slot, f"{kind}:{number_}")],
                measure=VOTES,
                value=count(table.printed[i]),
            )
        b.p.add(
            "declared",
            result_set_id=b.sets[slot],
            option_id=None,
            measure=VALID,
            value=count(table.printed[table.total]),
        )
    return slot


def genova_preferences(b: Builder, slot: str, table: Table, area_number: str, list_number: int) -> None:
    area = municipio_area(area_number)
    parent = b.options[(slot, f"list:{list_number}")]
    named = next((i for i, name in enumerate(table.header) if name.upper() == "LISTA"), None)
    if named is not None and table.rows:
        have = person(table.rows[0][named]).upper()
        want = next(o for o in b.p.rows["contest_option"] if o["id"] == parent)["ballot_label"]
        if have != str(want).upper():
            raise ValueError(f"preferences of list {list_number} are for {have!r}, the list is {want!r}")
    for _, number_, name in table.columns:
        b.option(slot, f"{list_number}.{number_}", "candidate", name, parent)
    for row in table.rows:
        if len(row) <= table.total or not row[table.section]:
            continue
        unit = b.unit(area, str(int(row[table.section])))
        for i, number_, _ in table.columns:
            b.value(slot, unit, b.options[(slot, f"candidate:{list_number}.{number_}")], PREFERENCES, count(row[i]))


def check_sum(table: Table, what: str) -> None:
    for row in table.rows:
        if len(row) <= table.total or not row[table.section]:
            continue
        have = sum(count(row[i]) or 0 for i, _, _ in table.columns)
        if have != count(row[table.total]):
            raise ValueError(f"{what}: section {row[table.section]} sums to {have}, the file prints {row[table.total]}")


def genoa_2025() -> Package:
    b = Builder("genova", 2025, "The 2025 elections of Genoa's municipio councils and presidents by polling section")
    b.use(G2025)
    files = zipped(G2025)
    for number_, name, _ in MUNICIPI:
        label = f"{ROMAN[number_]} {name}"
        folder = f"MUNICIPIO_{number_}/MUNICIPIO {number_} "
        president = Table(files[next(n for n in files if n.startswith(f"{folder}(PRESIDENTE) - VALIDI"))])
        check_sum(president, f"president {number_}")
        genova_lists(b, "president", president, number_, label)
        lists = Table(files[next(n for n in files if n.startswith(f"{folder}(LISTE) - VALIDI"))])
        check_sum(lists, f"lists {number_}")
        slot = genova_lists(b, "council", lists, number_, label)
        for name_, data in files.items():
            match = re.fullmatch(
                rf"MUNICIPIO_{number_}/MUNICIPIO {number_} \(LISTE\) - PREFERENZE \(LISTA (\d+)\) VALIDI\.xls", name_
            )
            if match:
                prefs = Table(data)
                check_sum(prefs, f"preferences {number_} list {match.group(1)}")
                genova_preferences(b, slot, prefs, number_, int(match.group(1)))
    return b.p


def genoa_2012() -> Package:
    b = Builder("genova", 2012, "The 2012 elections of Genoa's municipio councils by polling section")
    b.use(G2012_LISTS)
    b.use(G2012_PREFS)
    lists = zipped(G2012_LISTS)
    for number_, name, _ in MUNICIPI:
        table = Table(lists[f"LISTE_2012_MUN{number_}.csv"])
        check_sum(table, f"lists {number_}")
        genova_lists(b, "council", table, number_, f"{ROMAN[number_]} {name}")
    outer = zipped(G2012_PREFS)
    for number_, name, _ in MUNICIPI:
        slot = f"{number_}:council"
        with zipfile.ZipFile(io.BytesIO(outer[f"municipio_{number_}.zip"])) as archive:
            for info in archive.infolist():
                match = re.search(r"PREFERENZE \(LISTA (\d+)\)", info.filename)
                if match:
                    prefs = Table(archive.read(info))
                    check_sum(prefs, f"preferences {number_} list {match.group(1)}")
                    genova_preferences(b, slot, prefs, number_, int(match.group(1)))
    return b.p


def genoa_2017() -> Package:
    b = Builder("genova", 2017, "The 2017 elections of Genoa's municipio councils by polling section")
    for number_, name, _ in MUNICIPI:
        url = G2017.format(n=number_)
        b.use(url)
        table = Table(read(url))
        check_sum(table, f"lists {number_}")
        genova_lists(b, "council", table, number_, f"{ROMAN[number_]} {name}")
    return b.p


def genoa_2007() -> Package:
    b = Builder("genova", 2007, "The 2007 elections of Genoa's municipio councils by municipio")
    for number_, name, file in MUNICIPI:
        url = G2007.format(name=file)
        b.use(url)
        slot = b.contest(number_, "council", f"{ROMAN[number_]} {name}")
        unit = b.unit(municipio_area(number_), f"municipio-{number_}")
        position = 0
        for row in rows(read(url))[1:]:
            if len(row) < 3 or not row[1].strip():
                continue
            position += 1
            option = b.option(slot, position, "list", person(row[1]))
            b.value(slot, unit, option, VOTES, count(row[2]))
    return b.p


def bologna_2021() -> Package:
    b = Builder("bologna", 2021, "The 2021 elections of Bologna's quartiere councils by polling section")
    b.use(B_LISTS)
    b.p.accept(
        "accounting.turnout",
        "Eight polling sections print no registered voters (iscritti 0) but report voters, as the city's file gives them",
    )
    table = rows(read(B_LISTS))
    header = table[0]
    first = header.index("zona") + 1
    last = header.index("totale_votanti")
    lists = [(i, header[i]) for i in range(first, last)]
    zone: dict[str, str] = {}
    by_name = {name: number_ for number_, _, name, _ in QUARTIERI}
    labels = {number_: name for number_, name, _, _ in QUARTIERI}
    for row in table[1:]:
        quartiere = by_name[row[2].strip()]
        section = str(int(row[0]))
        zone[section] = quartiere
        slot = b.contest(quartiere, "council", labels[quartiere])
        unit = b.unit(quartiere_area(quartiere), section)
        for i, slug in lists:
            option = b.option(slot, i - first + 1, "list", title(slug.replace("d_talia", "d_italia")))
            b.value(slot, unit, option, VOTES, count(row[i]))
        valid = count(row[header.index("totale_voti_validi")])
        total = sum(count(row[i]) or 0 for i, _ in lists)
        if total != valid:
            raise ValueError(f"section {section}: lists sum to {total}, the file prints {valid}")
        b.value(slot, unit, None, REGISTERED, count(row[1]))
        b.value(slot, unit, None, VOTERS, count(row[header.index("totale_votanti")]))
        b.value(slot, unit, None, VALID, valid)
        b.value(slot, unit, None, INVALID, count(row[header.index("totale_schede_nulle")]))
        b.value(slot, unit, None, BLANK, count(row[header.index("totale_schede_bianche")]))
    numbers = {slug: i - first + 1 for i, slug in lists}
    slugs = sorted(numbers, key=len, reverse=True)
    for number_, _, _, part in QUARTIERI:
        url = B_QUARTIERE.format(slug=part)
        b.use(url)
        candidates = rows(read(url))
        head = [
            column.replace("fratelli_d_italia", "fratelli_d_talia").replace("insieme_pe_bologna", "insieme_per_bologna")
            for column in candidates[0]
        ]
        start = head.index("zona") + 1
        slot = f"{number_}:council"
        owner: dict[int, int] = {}
        for i in range(start, len(head)):
            slug = next(s for s in slugs if head[i].startswith(s + "_"))
            owner[i] = numbers[slug]
            parent = b.options[(slot, f"list:{owner[i]}")]
            b.option(slot, f"{owner[i]}.{i}", "candidate", title(head[i][len(slug) + 1 :]), parent)
        for row in candidates[1:]:
            section = str(int(row[0]))
            if zone[section] != number_:
                raise ValueError(f"section {section} is in {zone[section]} in the list file and {number_} here")
            unit = b.unit(quartiere_area(number_), section)
            for i in range(start, len(head)):
                b.value(slot, unit, b.options[(slot, f"candidate:{owner[i]}.{i}")], PREFERENCES, count(row[i]))
    return b.p


BUILDS = {
    "genova-2007": genoa_2007,
    "genova-2012": genoa_2012,
    "genova-2017": genoa_2017,
    "genova-2025": genoa_2025,
    "bologna-2021": bologna_2021,
}


def packages() -> list[str]:
    return ["it.municipi/geo", "it.municipi/registry", *(f"it.municipi/{name}" for name in BUILDS)]


def parse() -> None:
    for build in (geo, registry, *BUILDS.values()):
        build().write()


def emit() -> None:
    parse()
