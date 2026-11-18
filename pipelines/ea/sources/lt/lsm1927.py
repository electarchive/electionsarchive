"""The Constituent Seimas (1920) and the I, II, and III Seimas (1922, 1923, 1926) by county, from the 1927 yearbook.

The Central Statistical Bureau's *Lietuvos statistikos metraštis 1924-1926 m.* (Kaunas, 1927) prints, in tables 6 to
9 of its chapter on intellectual statistics, the votes each candidate list received in every county (apskritis) and
county town that formed a constituency (apygarda), with a subtotal for each constituency and a total for Lithuania.
The 1926 table also prints each county's electorate and the number who voted. The yearbook's scans are public domain
on epaveldas.lt. Every figure was read from the page image and keeps its page and box.

The Constituent Seimas's seats per constituency come from the Chief Electoral Commission's final announcement, as the
daily *Lietuva* printed it on 4 May 1920. The yearbook does not print seats, and the seats each list won in 1922,
1923, and 1926 are not stored yet.
"""

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from PIL import Image

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.lt import vrk

SOURCE = "lt.lsm1927"
LITHUANIA = vrk.LITHUANIA
ITEM = "C100000278981"
RECORD = f"https://www.epaveldas.lt/vepis-api/internal/findById?id={ITEM}"
PREVIEW = f"https://www.epaveldas.lt/preview?id={ITEM}"
IMAGE = "https://www.epaveldas.lt/static/lmb-bucket/{item}/original/{n:04d}-{item}.jpg"
LIETUVA_ITEM = "LNB00AD2076-1920-Geg.4"
LIETUVA = (
    "https://www.epaveldas.lt/static/lnb-bucket/LNB00AD2076/LNB00AD2076-1920-Geg.4/original/"
    "0001-8DFF937BD3B651AE32571BA65289B18D.png"
)
TABLES = Path(__file__).with_name("lsm1927.json")
SCHEME = "lt.apskritis_1920"
VOTES, REGISTERED, VALID, VOTERS = 1, 2, 4, 14
DIGITIZED = 8
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "X": 10}


class Election(NamedTuple):
    first: date
    last: date
    images: tuple[int, ...]
    slug: str
    english: str
    native: str
    seats: int


ELECTIONS = (
    Election(
        date(1920, 4, 14),
        date(1920, 4, 16),
        (86, 87, 88),
        "constituent",
        "1920 Lithuanian Constituent Seimas election",
        "1920 m. Steigiamojo Seimo rinkimai",
        112,
    ),
    Election(
        date(1922, 10, 10),
        date(1922, 10, 11),
        (89, 90, 91),
        "parliamentary",
        "1922 Lithuanian Seimas election (I Seimas)",
        "1922 m. I Seimo rinkimai",
        78,
    ),
    Election(
        date(1923, 5, 12),
        date(1923, 5, 13),
        (92, 93, 94, 95),
        "parliamentary",
        "1923 Lithuanian Seimas election (II Seimas)",
        "1923 m. II Seimo rinkimai",
        78,
    ),
    Election(
        date(1926, 5, 8),
        date(1926, 5, 10),
        (96, 97, 98, 99, 100, 101),
        "parliamentary",
        "1926 Lithuanian Seimas election (III Seimas)",
        "1926 m. III Seimo rinkimai",
        85,
    ),
)
# The constituencies' seat centres, as the tables and the 1920 announcement name them.
CONSTITUENCIES = {
    1: ("Marijampolės apygarda", "Marijampolė"),
    2: ("Kauno apygarda", "Kaunas"),
    3: ("Raseinių apygarda", "Raseiniai"),
    4: ("Telšių apygarda", "Telšiai"),
    5: ("Panevėžio apygarda", "Panevėžys"),
    6: ("Utenos apygarda", "Utena"),
    10: ("Klaipėdos apygarda", "Klaipėda"),
}
# Seats per constituency in 1920, from the Chief Electoral Commission's final announcement (Lietuva, 4 May 1920).
SEATS_1920 = {1: 21, 2: 20, 3: 17, 4: 14, 5: 24, 6: 16}
# Each printed row label, by its leading word, to a county or county town: code, Lithuanian name, English name.
UNITS: dict[str, tuple[str, str, str]] = {
    "alytaus": ("alytus-county", "Alytaus apskritis", "Alytus County"),
    "marijampolės": ("marijampole-county", "Marijampolės apskritis", "Marijampolė County"),
    "seinų": ("seinai-county", "Seinų apskritis", "Seinai County"),
    "kauno apskr": ("kaunas-county", "Kauno apskritis", "Kaunas County"),
    "kauno miest": ("kaunas-city", "Kauno miestas", "City of Kaunas"),
    "šakių": ("sakiai-county", "Šakių apskritis", "Šakiai County"),
    "trakų": ("trakai-county", "Trakų apskritis", "Trakai County"),
    "vilkaviškio": ("vilkaviskis-county", "Vilkaviškio apskritis", "Vilkaviškis County"),
    "kėdainių": ("kedainiai-county", "Kėdainių apskritis", "Kėdainiai County"),
    "kedainių": ("kedainiai-county", "Kėdainių apskritis", "Kėdainiai County"),
    "raseinių": ("raseiniai-county", "Raseinių apskritis", "Raseiniai County"),
    "tauragės": ("taurage-county", "Tauragės apskritis", "Tauragė County"),
    "kretingos": ("kretinga-county", "Kretingos apskritis", "Kretinga County"),
    "mažeikių": ("mazeikiai-county", "Mažeikių apskritis", "Mažeikiai County"),
    "telšių": ("telsiai-county", "Telšių apskritis", "Telšiai County"),
    "biržų": ("birzai-county", "Biržų apskritis", "Biržai County"),
    "panevėžio apskr": ("panevezys-county", "Panevėžio apskritis", "Panevėžys County"),
    "panevėžio miest": ("panevezys-city", "Panevėžio miestas", "City of Panevėžys"),
    "šiaulių apskr": ("siauliai-county", "Šiaulių apskritis", "Šiauliai County"),
    "šiaulių miest": ("siauliai-city", "Šiaulių miestas", "City of Šiauliai"),
    "rokiškio": ("rokiskis-county", "Rokiškio apskritis", "Rokiškis County"),
    "utenos": ("utena-county", "Utenos apskritis", "Utena County"),
    "ukmergės apskr": ("ukmerge-county", "Ukmergės apskritis", "Ukmergė County"),
    "ukmergės miest": ("ukmerge-city", "Ukmergės miestas", "City of Ukmergė"),
    "ežerėnų": ("ezerenai-county", "Ežerėnų (Zarasų) apskritis", "Ežerėnai (Zarasai) County"),
    "klaipėdos apskr": ("klaipeda-county", "Klaipėdos apskritis", "Klaipėda County"),
    "klaipėdos miest": ("klaipeda-city", "Klaipėdos miestas", "City of Klaipėda"),
    "pagėgių": ("pagegiai-county", "Pagėgių apskritis", "Pagėgiai County"),
    "šilutės": ("silute-county", "Šilutės apskritis", "Šilutė County"),
}
# Sums the printed figures themselves fail, each re-read at high zoom; the figures are stored as printed.
SEINAI_1920 = (
    "The Seinai County row prints 536 votes for the Economic and Political Union of Lithuanian Farmers (Ekonominės "
    "bei politinės žemdirbių sąjungos), where the constituency I subtotal (3,424), the national total (7,651), and the "
    "county's own total (16,268) all imply 586; the figure is stored as printed."
)
KRETINGA_1923 = (
    "In constituency IV the list votes of Kretinga (42,032), Mažeikiai (31,864), and Telšiai (37,525) counties differ "
    "from the totals printed for them (42,029, 31,869, and 37,523), though every list's county figures add up to its "
    "printed subtotal and the differences cancel in the constituency total (111,421); all are stored as printed."
)
MISPRINTS: dict[date, tuple[set[str], int, str, dict[str, str]]] = {
    date(1920, 4, 14): (
        {
            "1920-04-14 1 Ekonominės bei politinės žemdirb. sąjungos: counties sum to 3374, printed 3424",
            "1920-04-14 (1, 'seinai-county'): lists sum to 16218, printed total 16268",
        },
        1,
        SEINAI_1920,
        {"accounting.votes_valid": SEINAI_1920, "accounting.declared": SEINAI_1920},
    ),
    date(1923, 5, 12): (
        {
            "1923-05-12 (4, 'kretinga-county'): lists sum to 42032, printed total 42029",
            "1923-05-12 (4, 'mazeikiai-county'): lists sum to 31864, printed total 31869",
            "1923-05-12 (4, 'telsiai-county'): lists sum to 37525, printed total 37523",
        },
        4,
        KRETINGA_1923,
        {"accounting.votes_valid": KRETINGA_1923},
    ),
}
NOT_LISTS = {"rinkikų skaičius": REGISTERED, "balsavo": VOTERS, "iš viso": VALID}
SKIPPED = ("balsavusių", "%")  # the 1926 turnout percentage, derived from the two columns before it
METHOD = (
    "Votes for each candidate list by county and county town, as the Central Statistical Bureau printed them in "
    "*Lietuvos statistikos metraštis 1924-1926 m.* (Kaunas, 1927), table {table}, pages {pages}. Digitized from the "
    "page scans that the Lithuanian National Library publishes on epaveldas.lt: every "
    "figure keeps its page and bounding box. The tables give a list's votes as the number of ballots cast for it, so "
    "the list votes of a county add up to the county's valid votes, which the table prints as its total. "
    "The constituency subtotals and the total for Lithuania are kept as declared figures."
)
TURNOUT = (
    " The 1926 table also prints each county's electorate (rinkikų skaičius) and the number who voted (balsavo); its "
    "turnout percentage is not stored, and invalid ballots, the difference between voters and valid votes, are not "
    "derived."
)
SEATS_NOTE = (
    "The yearbook prints votes, not seats. The seats each list won in this constituency are not stored yet; "
    "the Seimas history pages give only the national seat totals by party."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SRC = key("source", "metrastis")
LIETUVA_SRC = key("source", "lietuva")


def image_url(n: int) -> str:
    return IMAGE.format(item=ITEM, n=n)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{e.first}" for e in ELECTIONS)]


def fetch() -> None:
    raw.fetch(SOURCE, RECORD, suffix=".json")
    raw.fetch(SOURCE, LIETUVA, suffix=".png")
    for e in ELECTIONS:
        for n in e.images:
            raw.fetch(SOURCE, image_url(n), suffix=".jpg")


def tables() -> dict[int, dict]:
    return {int(n): page for n, page in json.loads(TABLES.read_text()).items()}


def fold(text: str) -> str:
    return " ".join(text.casefold().replace("„", "").replace("“", "").replace('"', "").split())


def unit_of(name: str) -> tuple[str, str, str]:
    """The county or county town a printed row label names: "Kauno miest.", "Panevėžio m.", "Telšių apskr.", "Utenos"."""
    folded = fold(name).replace("mariamp", "marijamp")
    folded = re.sub(r"^(\S+) m(\.|iest)", r"\1 miest", folded)
    words = folded.split()
    for prefix in sorted(UNITS, key=len, reverse=True):
        if folded.startswith(prefix):
            return UNITS[prefix]
    # A county printed without "apskr.": the bare genitive names the county.
    if words and f"{words[0]} apskr" in UNITS:
        return UNITS[f"{words[0]} apskr"]
    raise LookupError(f"no county for the row {name!r}")


class Cell(NamedTuple):
    image: int
    value: int
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in pixels


class Table(NamedTuple):
    lists: list[str]  # list labels in printed order
    french: dict[str, str | None]
    # (constituency, unit code) -> column label -> cell
    counties: dict[tuple[int, str], dict[str, Cell]]
    subtotals: dict[int, dict[str, int]]
    total: dict[str, int]
    tables: list[str]
    pages: list[int]


def measure_of(label: str) -> int | None:
    """The measure of a column that is not a list, or None for a list."""
    return next((measure for prefix, measure in NOT_LISTS.items() if fold(label).startswith(prefix)), None)


def clean_label(label: str) -> str:
    return " ".join(label.replace("\n", " ").split())


def read(e: Election) -> Table:
    pages = tables()
    lists: list[str] = []
    french: dict[str, str | None] = {}
    counties: dict[tuple[int, str], dict[str, Cell]] = {}
    subtotals: dict[int, dict[str, int]] = {}
    total: dict[str, int] = {}
    labels: list[str] = []
    printed: list[int] = []
    for n in e.images:
        page = pages[n]
        labels.append(page["table"])
        printed.append(int(page["printed_page"]))
        columns = []
        for c in page["columns"]:
            label = clean_label(c["label"])
            if any(fold(label).startswith(skip) for skip in SKIPPED):
                columns.append(None)
                continue
            measure = measure_of(label)
            if measure is None:
                if label in lists:
                    raise ValueError(f"{e.first}: list {label!r} printed twice")
                lists.append(label)
                french[label] = c.get("label_fr")
            columns.append((label if measure is None else f"#{measure}", c["x"]))
        for row in page["rows"]:
            if len(row["values"]) != len(page["columns"]):
                raise ValueError(f"image {n} row {row['name']!r}: {len(row['values'])} values, not {len(columns)}")
            if row["kind"] == "county":
                circ = ROMAN[row["circ"]]
                code = unit_of(row["name"])[0]
                target = counties.setdefault((circ, code), {})
                for column, value in zip(columns, row["values"], strict=True):
                    if column is None or value is None:
                        continue
                    label, (x0, x1) = column
                    if label in target:
                        raise ValueError(f"{e.first} {code} {label}: printed twice")
                    target[label] = Cell(n, number(value), (x0, row["y"][0], x1, row["y"][1]))
            else:
                into = subtotals.setdefault(ROMAN[row["circ"]], {}) if row["kind"] == "subtotal" else total
                for column, value in zip(columns, row["values"], strict=True):
                    if column is not None and value is not None:
                        into[column[0]] = number(value)
    return Table(lists, french, counties, subtotals, total, labels, printed)


def number(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d[\d ]*", value.strip()):
        return int(value.replace(" ", ""))
    raise ValueError(f"{value!r} is not a count")


def check(e: Election, t: Table) -> list[str]:
    """The printed sums the figures fail to reach, each as a sentence; parse raises on any not explained."""
    failures = []
    columns = [*t.lists, *(f"#{m}" for m in (REGISTERED, VOTERS, VALID))]
    for column in columns:
        for circ, printed in t.subtotals.items():
            got = sum(cells[column].value for (c, _), cells in t.counties.items() if c == circ and column in cells)
            if column in printed and got != printed[column]:
                failures.append(f"{e.first} {circ} {column}: counties sum to {got}, printed {printed[column]}")
            if column not in printed and got:
                failures.append(f"{e.first} {circ} {column}: counties sum to {got}, no subtotal printed")
        summed = sum(printed.get(column, 0) for printed in t.subtotals.values())
        if column in t.total and summed != t.total[column]:
            failures.append(f"{e.first} {column}: subtotals sum to {summed}, printed total {t.total[column]}")
    for where, cells in t.counties.items():
        lists = sum(cell.value for label, cell in cells.items() if not label.startswith("#"))
        valid = cells.get(f"#{VALID}")
        if valid is not None and lists != valid.value:
            failures.append(f"{e.first} {where}: lists sum to {lists}, printed total {valid.value}")
    return failures


def parse() -> None:
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def party(label: str) -> str:
    return key("party", fold(label))


def constituent_assembly() -> str:
    return key("body", "steigiamasis_seimas")


def seimas() -> str:
    return key("body", "seimas")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The 1927 statistical yearbook and the 1920 announcement as sources, the Constituent Seimas, the interwar "
        "counties as reporting units, and the candidate lists of 1920-1926",
    )
    p.external.add(LITHUANIA)
    p.add(
        "source",
        id=SRC,
        kind="official_body",
        publisher="Centralinis statistikos biuras (Finansų, prekybos ir pramonės ministerija), Kaunas",
        title="Lietuvos statistikos metraštis 1924-1926 m. / Annuaire statistique de la Lithuanie (1927), tables 6-9",
        url=PREVIEW,
        license="Public domain (scan published by the Lithuanian National Library on epaveldas.lt, rights: PD)",
        license_url=PREVIEW,
        attribution="Centralinis statistikos biuras, Lietuvos statistikos metraštis 1924-1926 m., Kaunas 1927; "
        "scan: epaveldas.lt",
    )
    p.artifact(raw.cached(SOURCE, RECORD), SRC)
    p.add(
        "source",
        id=LIETUVA_SRC,
        kind="official_body",
        publisher="Vyriausioji rinkimų komisija, in the daily Lietuva (Kaunas), via the Elta news agency",
        title="Į Steigiamąjį Seimą: galutiniai vaisiai rinkimų į St. Seimą (Lietuva, no. 97 (379), 4 May 1920, p. 1)",
        url=f"https://www.epaveldas.lt/preview?id={LIETUVA_ITEM}",
        license="Public domain (scan published by the Lithuanian National Library on epaveldas.lt, rights: PD)",
        license_url=f"https://www.epaveldas.lt/preview?id={LIETUVA_ITEM}",
        attribution="Lietuva, 4 May 1920; scan: Lietuvos nacionalinė Martyno Mažvydo biblioteka, epaveldas.lt",
    )
    p.artifact(raw.cached(SOURCE, LIETUVA), LIETUVA_SRC)
    body = constituent_assembly()
    p.add("body", id=body, polity_id=LITHUANIA, kind="constituent_assembly")
    p.name(body, "Constituent Seimas of Lithuania", "en", "official")
    p.name(body, "Steigiamasis Seimas", "lt", "native", False)
    body = seimas()
    p.add("body", id=body, polity_id=LITHUANIA, kind="unicameral")
    p.name(body, "Seimas of the Republic of Lithuania (1922-1940)", "en", "official")
    p.name(body, "Lietuvos Respublikos Seimas (1922-1940)", "lt", "native", False)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=LITHUANIA,
        kind="administrative",
        rank=1,
        noun="county",
        kinds={"county": "county", "city": "city"},
    )
    for code, lt, en in sorted(set(UNITS.values())):
        id = key("unit", code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=LITHUANIA,
            scheme_code=SCHEME,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(id, en, "en", "official")
        p.name(id, lt, "lt", "native", False)
    seen: dict[str, tuple[str, str | None]] = {}
    for e in ELECTIONS:
        t = read(e)
        for label in t.lists:
            seen.setdefault(party(label), (label, t.french[label]))
    for id, (label, french) in sorted(seen.items()):
        folded = fold(label)
        kind = (
            "bloc"
            if "blokas" in folded
            else "list_vehicle"
            if re.search(r"kuop|grup|sąraš|komitet", folded)
            else "party"
        )
        p.add("party", id=id, kind=kind, polity_id=LITHUANIA)
        p.name(id, label, "lt", "official")
        if french:
            p.name(id, " ".join(french.split()), "fr", "official", False)
    return p


def election(e: Election) -> Package:
    t = read(e)
    misprints, misprinted, misprint_note, accepted = MISPRINTS.get(e.first, (set(), 0, "", {}))
    unexplained = set(check(e, t)) - misprints
    if unexplained:
        raise ValueError("printed sums not reached:\n" + "\n".join(sorted(unexplained)))
    p = Package(
        f"{SOURCE}/{e.first}",
        SOURCE,
        f"The {e.english} by county, digitized from the 1927 statistical yearbook",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(LITHUANIA)
    constituent = e.slug == "constituent"
    body = constituent_assembly() if constituent else seimas()
    pages: dict[int, str] = {}
    for n in e.images:
        stored = raw.cached(SOURCE, image_url(n))
        artifact = p.artifact(stored, SRC)
        with Image.open(stored.path) as im:
            width, height = im.size
        pages[n] = key("page", n)
        p.add(
            "source_page",
            id=pages[n],
            artifact_id=artifact,
            page_number=1,
            image_key=f"pages/{SOURCE}/{stored.sha256}.jpg",
            width=width,
            height=height,
        )
    sizes = {n: Image.open(raw.cached(SOURCE, image_url(n)).path).size for n in e.images}
    election_id = key("election", e.first)
    p.add(
        "election",
        id=election_id,
        polity_id=LITHUANIA,
        kind="general",
        date_from=e.first,
        date_to=e.last,
        date_precision="day",
        status="held",
    )
    p.name(election_id, e.english, "en", "official")
    p.name(election_id, e.native, "lt", "native", False)
    path = f"lt/{e.first}-{e.slug}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add(
        "citation",
        id=key("citation", election_id),
        entity_id=election_id,
        source_id=SRC,
        locator=json.dumps(
            {"url": PREVIEW, "tables": t.tables, "pages": f"{min(t.pages)}-{max(t.pages)}"}, ensure_ascii=False
        ),
    )
    lietuva = raw.cached(SOURCE, LIETUVA) if constituent else None
    for circ in sorted({c for c, _ in t.counties}):
        native, centre = CONSTITUENCIES[circ]
        roman = next(r for r, v in ROMAN.items() if v == circ)
        contest = key("contest", e.first, circ)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body,
            magnitude=SEATS_1920[circ] if constituent else None,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(
            contest,
            f"{'Constituent Seimas' if constituent else 'Seimas'}, constituency {roman} ({centre})",
            "en",
            "official",
        )
        p.name(contest, f"{roman} {native}", "lt", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(f'{roman} {centre}')}", entity_id=contest, is_current=True)
        if lietuva is not None:
            p.add(
                "citation",
                id=key("citation", contest),
                entity_id=contest,
                source_id=LIETUVA_SRC,
                locator=json.dumps(
                    {"url": lietuva.url, "page": 1, "text": "Galutiniai vaisiai rinkimų į St. Seimą"},
                    ensure_ascii=False,
                ),
            )
        set_id = key("result_set", e.first, circ)
        p.add(
            "result_set",
            id=set_id,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SRC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD.format(table=", ".join(t.tables), pages=f"{min(t.pages)}-{max(t.pages)}")
        if e.first.year == 1926:
            method += TURNOUT
        if not constituent:
            method += " " + SEATS_NOTE
        own = [misprint_note] if circ == misprinted else []
        p.add(
            "note",
            id=key("note", set_id),
            entity_id=set_id,
            kind="methodology",
            body_md=" ".join([method, *own]),
            lang="en",
        )
        rows = {code: cells for (c, code), cells in t.counties.items() if c == circ}
        running = [label for label in t.lists if any(label in cells for cells in rows.values())]
        options: dict[str, str] = {}
        for position, label in enumerate(running, 1):
            option = options[label] = key("option", e.first, circ, fold(label))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(label),
                ballot_position=position,
                ballot_label=label,
            )
        for code, cells in rows.items():
            unit = key("unit", code)
            for label, cell in cells.items():
                if label.startswith("#"):
                    option, measure = None, int(label[1:])
                else:
                    option, measure = options[label], VOTES
                width, height = sizes[cell.image]
                x0, y0, x1, y1 = cell.box
                p.add(
                    "result_value",
                    result_set_id=set_id,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=DIGITIZED,
                    value=cell.value,
                )
                p.add(
                    "value_locator",
                    result_set_id=set_id,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=pages[cell.image],
                    source_id=None,
                    x0=round(x0 / width, 4),
                    y0=round(y0 / height, 4),
                    x1=round(x1 / width, 4),
                    y1=round(y1 / height, 4),
                )
        for label, value in t.subtotals.get(circ, {}).items():
            if label.startswith("#"):
                p.add("declared", result_set_id=set_id, option_id=None, measure=int(label[1:]), value=value)
            elif label in options:
                p.add("declared", result_set_id=set_id, option_id=options[label], measure=VOTES, value=value)
    for code, reason in accepted.items():
        p.accept(code, reason)
    return p
