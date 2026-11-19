"""Panama's Tribunal Electoral: the national referendums of 1992, 1998, and 2006, from the Tribunal's statistical
tables of each event."""

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import fastexcel
import xlrd

from ea import package, raw
from ea.package import Package
from ea.sources.pa import te
from ea.store import DATA

SOURCE = "pa.te_referendum"
SITE = te.SITE
INBOX = DATA / "inbox" / SOURCE
BROWSER = "browser://20261118-203423-b80187/"
WAYBACK = "https://web.archive.org/web/"
PAGE = {
    1992: WAYBACK + "20230929013045id_/" + SITE + "/referendum/referendum-1992/",
    1998: WAYBACK + "20240614090701id_/" + SITE + "/referendum/referendum-1998/",
    2006: WAYBACK + "20240710230438id_/" + SITE + "/referendum/referendum-2006/",
}
COMPENDIUM = WAYBACK + "20231202112321id_/" + SITE + "/compendio-electoral-al-1-mayo-2001/"
SUMMARY = BROWSER + "Cuadro_02_-_Resumen.xls"
OAS_2006 = (
    BROWSER + "Panama-Informe-de-la-mision-de-observacion-electoral-referendum-nacional-sobre-la-ampliacion-del-"
    "canal-de-panama-del-22-de-octubre-OAS-2006.pdf"
)

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = te.VOTES, te.REGISTERED, te.CAST, te.VALID, te.INVALID, te.BLANK
DERIVED = te.DERIVED
PANAMA, COUNTRY = te.PANAMA, te.COUNTRY
CIRCUIT, CORREGIMIENTO = "pa.circuito", "pa.corregimiento"

GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"


def fetch() -> None:
    """Load the files saved from the Tribunal's site (data/inbox/pa.te_referendum/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    for key, entry in index.items():
        raw.store(
            SOURCE,
            entry.get("source", key),
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


# Tables -----------------------------------------------------------------------------------------------------------

# Where each count sits among the numbers of a row, after its label, in the three layouts the Tribunal used.
L1992 = {"reg": 3, "cast": 4, "no": 7, "yes": 9, "null": 11}
L1998 = {"reg": 0, "cast": 3, "no": 6, "yes": 8, "blank": 10, "null": 12}
L2006 = {"reg": 0, "cast": 3, "no": 7, "yes": 9, "blank": 11, "null": 13}
L1998_C3 = {
    "reg": 0,
    "cast": 1,
    "no": 4,
    "yes": 6,
    "blank": 8,
    "null": 10,
}  # the 1998 table that goes to polling centers


@dataclass(frozen=True)
class Event:
    day: date
    layout: dict[str, int]
    circuits: str  # the table by province and circuit
    fine: str | None  # the table down to corregimientos
    english: str
    spanish: str
    contest_en: str
    contest_es: str
    pages: tuple[str, ...]  # the pages the packages cite, besides the tables

    @property
    def tables(self) -> list[str]:
        return [self.circuits, *([self.fine] if self.fine else [])]


EVENTS = [
    Event(
        date(1992, 11, 15),
        L1992,
        BROWSER + "CUADRO21.xls",
        None,
        "1992 Panamanian constitutional referendum",
        "Referéndum de 1992",
        "Constitutional reforms",
        "Reformas constitucionales",
        (PAGE[1992], COMPENDIUM),
    ),
    Event(
        date(1998, 8, 30),
        L1998,
        BROWSER + "referendum_98b.xls",
        BROWSER + "referendum_98a.xls",
        "1998 Panamanian constitutional referendum",
        "Referéndum de 1998",
        "Constitutional reforms",
        "Reformas constitucionales",
        (PAGE[1998], COMPENDIUM, BROWSER + "referendum_98C.xls"),
    ),
    Event(
        date(2006, 10, 22),
        L2006,
        te.DOWNLOAD + "7001",
        BROWSER + "Cuadro_03_-_Por_corregimiento.xls",
        "2006 Panama Canal expansion referendum",
        "Referéndum de 2006 sobre la ampliación del Canal de Panamá",
        "Third set of locks of the Panama Canal",
        "Tercer juego de esclusas del Canal de Panamá",
        (PAGE[2006], OAS_2006),
    ),
]


@dataclass
class Line:
    label: str
    indent: int
    nums: list[float]


def grid(url: str) -> list[list[object]]:
    data = raw.cached(SOURCE, url).path.read_bytes()
    if data[:2] == b"PK":
        book = fastexcel.read_excel(data)
        frame = book.load_sheet(book.sheet_names[0], header_row=None).to_polars()
        return [list(row) for row in frame.iter_rows()]
    sheet = xlrd.open_workbook(file_contents=data).sheets()[0]
    return [[cell if cell != "" else None for cell in sheet.row_values(i)] for i in range(sheet.nrows)]


def count(cell: object) -> float | None:
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, int | float):
        return float(cell)
    try:
        return float(str(cell).replace(",", ""))
    except ValueError:
        return None


def lines(url: str, need: int) -> list[Line]:
    """The rows of a table that carry a full set of figures, with their label and its indent."""
    out = []
    for cells in grid(url):
        label = cells[0] if isinstance(cells[0], str) else None
        nums = [n for n in map(count, cells[1:]) if n is not None]
        if label and len(nums) >= need and any(ch.isalpha() for ch in label):
            out.append(Line(label.rstrip(), len(label) - len(label.lstrip()), nums))
    return out


@dataclass
class Counts:
    reg: int
    cast: int
    no: int
    yes: int
    blank: int | None
    null: int

    @property
    def valid(self) -> int:
        return self.no + self.yes


def counts(line: Line, layout: dict[str, int]) -> Counts:
    def pick(name: str) -> int | None:
        if name not in layout:
            return None
        value = line.nums[layout[name]]
        if value != int(value):
            raise ValueError(f"{line.label}: {name} is not a count: {value}")
        return int(value)

    got = {name: pick(name) for name in ("reg", "cast", "no", "yes", "blank", "null")}
    assert got["reg"] is not None and got["cast"] is not None and got["no"] is not None
    assert got["yes"] is not None and got["null"] is not None
    return Counts(got["reg"], got["cast"], got["no"], got["yes"], got["blank"], got["null"])


CIRCUIT_LABEL = re.compile(r"circuito\s+(\d+\.\d+)", re.IGNORECASE)


@dataclass
class Unit:
    scheme: str
    code: str
    label: str
    counts: Counts
    province: str = ""
    district: str = ""
    circuit: str = ""


@dataclass
class Table:
    total: Counts
    circuits: list[Unit] = field(default_factory=list)
    corregimientos: list[Unit] = field(default_factory=list)
    provinces: dict[str, Counts] = field(default_factory=dict)


# Names the tables spell another way than the archive's areas, by district and corregimiento.
DISTRICT_ALIASES: dict[str, str] = {}
CORREGIMIENTO_ALIASES = {
    **te.CORREGIMIENTO_ALIASES,
    ("changuinola", "el valle de risco"): "valle de risco",
    ("bugaba", "bongo"): "el bongo",
    ("gualaca", "hornitos"): "hornito",
    ("los santos", "la cruces"): "las cruces",
    ("santa fe@veraguas", "gatu o gatuncillo"): "gatu o gatuncito",
    ("sambu", "jingurudu"): "jingurudo",
    ("alanje", "canto gallo"): "canta gallo",
    ("chepigana", "agua fria"): "aguas frias",
    ("tonosi", "isla de canas"): "isla canas",
    ("mariato", "arenas"): "arena",
    ("kankintu", "bisira"): "bisira (cab)",
    ("kusapin", "kusapin"): "kusapin (cab)",
    ("kusapin", "calovebora"): "calovebora o sta. catalina",
    ("besiko", "soloy"): "soloy (cab)",
    ("mirono", "hato pilon"): "hato pilon (cab)",
    ("mirono", "quebrada de oro"): "quebrada de loro",
    ("nole duima", "cerro iglesias"): "cerro iglesias (cab)",
    ("muna", "chichica"): "chichica (cab)",
}
COMARCAS = {"comarca wargandi": "comarca kuna de wargandi", "comarca madungandi": "comarca kuna de madungandi"}


def province_key(label: str) -> str:
    return te.place_key(label)


def district_code(province: str, label: str) -> str:
    key = te.place_key(label)
    key = DISTRICT_ALIASES.get(key, key)
    return f"{key}@{province}" if key == "santa fe" else key


FOOTNOTE = re.compile(r"\s+[a-z0-9]/\s*$")


def clean(label: str) -> str:
    """A label without its footnote mark."""
    return FOOTNOTE.sub("", label.strip())


def table(event: Event, url: str, fine: bool) -> Table:
    layout = event.layout
    need = max(layout.values()) + 1
    rows = lines(url, need)
    assert rows[0].label.strip().upper() == "TOTAL", rows[0].label
    out = Table(counts(rows[0], layout))
    province = district = circuit = ""
    parent_left = 0  # registered voters of a district's blocks still to come
    for i, line in enumerate(rows[1:], 1):
        label = clean(line.label)
        found = CIRCUIT_LABEL.fullmatch(label)
        if found:
            circuit = found[1]
            out.circuits.append(Unit(CIRCUIT, circuit, label, counts(line, layout), province))
            parent_left = 0
            continue
        if line.indent == 0 and not label.isupper():
            # A district the table prints at the province's margin: Kuna Yala's, each with one corregimiento.
            if fine:
                key_ = COMARCAS.get(te.place_key(label), te.place_key(label))
                unit = Unit(CORREGIMIENTO, f"{key_}:{key_}", label, counts(line, layout), province, key_, circuit)
                out.corregimientos.append(unit)
            parent_left = 0
        elif line.indent == 0:
            province = province_key(label)
            out.provinces[province] = counts(line, layout)
            parent_left = 0
        elif not fine or line.indent >= 5 or label.lower().startswith("esc."):
            continue  # polling centers, which this module does not read
        elif line.indent == 2:
            c = counts(line, layout)
            if parent_left:
                # A block of a district the table splits ("Ciudad de Colón", "Resto del Distrito").
                parent_left -= c.reg
                if parent_left < 0:
                    raise ValueError(f"{label}: blocks add up to more than their district")
            elif i + 1 < len(rows) and rows[i + 1].indent == 2:
                district = district_code(province, label)
                parent_left = c.reg
            else:
                district = district_code(province, label)
        elif te.place_key(label).startswith("comarca "):
            code = COMARCAS.get(te.place_key(label), te.place_key(label))
            out.corregimientos.append(
                Unit(CORREGIMIENTO, f"{code}:{code}", label, counts(line, layout), province, code, circuit)
            )
        else:
            key_ = te.place_key(label)
            code = f"{district}:{CORREGIMIENTO_ALIASES.get((district, key_), key_)}"
            out.corregimientos.append(
                Unit(CORREGIMIENTO, code, label, counts(line, layout), province, district, circuit)
            )
    return out


def merged(units: list[Unit]) -> list[Unit]:
    """Units one table prints in several rows, such as a corregimiento divided between circuits, added up."""
    by_code: dict[str, Unit] = {}
    for u in units:
        if u.code not in by_code:
            by_code[u.code] = replace(u)
            continue
        a, b = by_code[u.code].counts, u.counts
        by_code[u.code].counts = Counts(
            a.reg + b.reg,
            a.cast + b.cast,
            a.no + b.no,
            a.yes + b.yes,
            None if a.blank is None or b.blank is None else a.blank + b.blank,
            a.null + b.null,
        )
    return list(by_code.values())


# Pucuro: the one corregimiento the 1998 table by corregimiento leaves out, though its district and circuit totals
# include it. The table by polling center prints it.
def pucuro() -> Unit:
    event = EVENTS[1]
    path = next(e for e in event.pages if e.endswith("referendum_98C.xls"))
    for line in lines(path, 11):
        if line.indent == 4 and clean(line.label) == "Púcuro":
            c = counts(line, L1998_C3)
            return Unit(CORREGIMIENTO, "pinogana:pucuro", "Púcuro", c, "darien", "pinogana", "5.2")
    raise ValueError("no Púcuro row")


def fine_rows(event: Event) -> list[Unit]:
    """The corregimiento rows of an event's table, one per row it prints."""
    assert event.fine
    units = table(event, event.fine, True).corregimientos
    return [*units, pucuro()] if event.day.year == 1998 else units


# Checks -------------------------------------------------------------------------------------------------------------


def summary() -> dict[str, list[int | None]]:
    """The Tribunal's summary table: for each line, a count for each event, in the order 1992, 1998, 2006."""
    out: dict[str, list[int | None]] = {}
    for cells in grid(SUMMARY):
        label = cells[0]
        if not isinstance(label, str) or label.strip() not in SUMMARY_LINES:
            continue
        tokens = [c for c in cells[1:] if c is not None]
        found: list[int | None] = []
        if label.strip() == "Padrón Electoral":
            found = [int(n) for n in map(count, tokens) if n is not None]
        else:
            if tokens[0] == "…":  # a line the Tribunal prints nothing for in 1992
                found = [None]
                tokens = tokens[1:]
            found += [int(n) for n in map(count, tokens[::2]) if n is not None]
        out[label.strip()] = found
    return out


SUMMARY_LINES = {"Padrón Electoral", "Votos Emitidos", "Votos 'NO'", "Votos 'SI'", "Votos Blancos", "Votos Nulos"}


def total_of(c: Counts) -> dict[str, int | None]:
    return {
        "Padrón Electoral": c.reg,
        "Votos Emitidos": c.cast,
        "Votos 'NO'": c.no,
        "Votos 'SI'": c.yes,
        "Votos Blancos": c.blank,
        "Votos Nulos": c.null,
    }


def sums(units: list[Unit]) -> Counts:
    blank = [u.counts.blank for u in units]
    return Counts(
        sum(u.counts.reg for u in units),
        sum(u.counts.cast for u in units),
        sum(u.counts.no for u in units),
        sum(u.counts.yes for u in units),
        None if any(b is None for b in blank) else sum(b for b in blank if b is not None),
        sum(u.counts.null for u in units),
    )


def compare(event: Event) -> list[str]:
    """Check each table's rows against its printed Total row, and the Total row against the summary table."""
    said: list[str] = []
    found = summary()
    column = EVENTS.index(event)
    t = table(event, event.circuits, False)
    for name, want in total_of(t.total).items():
        got = found[name][column]
        if got != want:
            raise ValueError(f"{event.day}: the summary prints {got} for {name}, the table {want}")
    said.append(f"{event.day}: the table's Total row matches the summary table on {len(found)} lines")
    rows = sums(t.circuits)
    if rows != t.total:
        raise ValueError(f"{event.day}: circuits add up to {rows}, the Total row prints {t.total}")
    said.append(f"{event.day}: {len(t.circuits)} circuits add up to the Total row")
    if event.fine:
        printed = fine_rows(event)
        for c in t.circuits:
            rows = sums([u for u in printed if u.circuit == c.code])
            if rows != c.counts:
                raise ValueError(f"{event.day}: circuit {c.code}: corregimientos add up to {rows}, not {c.counts}")
        units = merged(printed)
        if sums(units) != t.total:
            raise ValueError(f"{event.day}: corregimientos add up to {sums(units)}, the Total row prints {t.total}")
        said.append(f"{event.day}: {len(units)} corregimientos add up to every circuit and to the Total row")
    return said


# Packages -----------------------------------------------------------------------------------------------------------


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


# Areas the archive's other Panama packages do not hold: circuits of 1992 and two corregimientos of 1998.
NEW_CIRCUITS = {"3.3": "colon", "8.11": "panama", "11.1": "embera", "11.2": "embera"}
NEW_CORREGIMIENTOS = {
    "bocas del toro:rio chiriqui": ("Río Chiriquí", "bocas del toro"),
    "chiriqui grande:guariviara": ("Guariviara", "chiriqui grande"),
}


def area(scheme: str, code: str) -> str:
    new = (scheme == CIRCUIT and code in NEW_CIRCUITS) or (scheme == CORREGIMIENTO and code in NEW_CORREGIMIENTOS)
    return key("area", scheme, code) if new else te.area(scheme, code)


def packages() -> list[str]:
    return [GEO, *(f"{SOURCE}/{e.day}" for e in EVENTS)]


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Electoral circuits and corregimientos that the Tribunal Electoral's referendum tables name and the "
        "archive's other Panama areas do not",
    )
    p.external |= {PANAMA, COUNTRY, CIRCUIT, CORREGIMIENTO}  # the polity, the country, and the Tribunal's area schemes
    for code, province in sorted(NEW_CIRCUITS.items(), key=lambda item: [int(x) for x in item[0].split(".")]):
        id = area(CIRCUIT, code)
        p.add("area", id=id, scheme_code=CIRCUIT)
        p.add("area_code", area_id=id, code=code)
        parent = te.area("pa.provincia", province)
        p.external.add(parent)
        p.add("area_parent", area_id=id, parent_id=parent, valid_from=None, valid_to=None)
        p.name(id, f"Circuito {code}", "es", "official")
    for code, (label, district) in sorted(NEW_CORREGIMIENTOS.items()):
        id = area(CORREGIMIENTO, code)
        p.add("area", id=id, scheme_code=CORREGIMIENTO)
        p.add("area_code", area_id=id, code=code)
        parent = te.area("pa.distrito", district)
        p.external.add(parent)
        p.add("area_parent", area_id=id, parent_id=parent, valid_from=None, valid_to=None)
        p.name(id, label, "es", "official")
    return p


METHOD = (
    "Counted by the Tribunal Electoral and printed in its statistical tables of the referendum. Registered voters "
    'are the electoral roll and ballots cast are the votes issued. The Tribunal prints the votes for "Sí" and '
    '"No", blank votes, and null votes. Valid votes here are the votes for "Sí" and "No", which the '
    "Tribunal groups as valid in its tables, so they are derived rather than printed."
)
METHOD_1992 = " The 1992 table prints no blank votes."
METHOD_FINE = (
    " This count is by corregimiento, from the Tribunal's table of the same event, and replaces the count by "
    "electoral circuit as the default."
)
SHARED = (
    "The corregimiento of Ailigandí is divided between circuits 10.1 and 10.2, and the table prints a row for each "
    "part. The row here adds the two."
)
PUCURO = (
    "The Tribunal's table by corregimiento leaves out Púcuro, though its district and circuit totals include it. "
    "The row for Púcuro here is the one the Tribunal's table by polling center prints."
)


def election(event: Event) -> Package:
    day = event.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} Panamanian referendum by "
        + ("circuit and corregimiento" if event.fine else "electoral circuit")
        + " from the Tribunal Electoral",
        depends=[GEO],
    )
    p.external |= {PANAMA, COUNTRY, CIRCUIT, CORREGIMIENTO}  # the polity, the country, and the Tribunal's area schemes
    source = key("source", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Tribunal Electoral de Panamá",
        title=f"Resultados oficiales del referéndum, {day.year}",
        url=PAGE[day.year],
        license="Publicly posted official results; no license stated",
        license_url=SITE,
        attribution="Tribunal Electoral de Panamá",
    )
    for url in [*event.tables, *event.pages, SUMMARY]:
        artifact = raw.cached(SOURCE, url)
        p.artifact(artifact, source)
        p.uses(artifact)
    eid = key("election", day)
    p.add("election", id=eid, polity_id=PANAMA, kind="referendum", date_from=day, date_to=day, status="held")
    p.name(eid, event.english, "en", "official")
    p.name(eid, event.spanish, "es", "native", False)
    path = f"pa/{day}-referendum"
    p.add("slug", path=path, entity_id=eid, is_current=True)
    cid = key("contest", day)
    p.add(
        "contest",
        id=cid,
        election_id=eid,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(cid, event.contest_en, "en", "official")
    p.name(cid, event.contest_es, "es", "native", False)
    p.add("slug", path=f"{path}/question", entity_id=cid, is_current=True)
    p.add("ballot_question", contest_id=cid, type="referendum")
    if day.year == 2006:
        oas = key("source", "oas", day)
        p.add(
            "source",
            id=oas,
            kind="official_body",
            publisher="Organization of American States",
            title="Informe de la Misión de Observación Electoral, referéndum nacional sobre la ampliación del Canal de "
            "Panamá, 22 de octubre de 2006",
            url=OAS_2006,
            license="Publicly posted report; no license stated",
            license_url="https://www.oas.org",
            attribution="Organization of American States",
        )
        p.artifact(raw.cached(SOURCE, OAS_2006), oas)
        p.add(
            "note",
            id=key("note", cid, "question"),
            entity_id=cid,
            kind="context",
            lang="en",
            body_md="The question on the ballot, as the observation mission of the Organization of American States "
            "quotes Article 30 of the Tribunal's Decree 9 of 18 July 2006: \u201c\u00bfAprueba usted la propuesta de "
            "construcci\u00f3n del tercer juego de esclusas en el Canal de Panam\u00e1?\u201d (Do you approve the proposal to build "
            "a third set of locks in the Panama Canal?)",
        )
        p.add(
            "note",
            id=key("note", eid, "oas"),
            entity_id=eid,
            kind="caveat",
            lang="en",
            body_md="The observation mission of the Organization of American States prints the result the Tribunal "
            "published three days after the vote: 705,144 votes for Sí, 201,247 for No, 9,966 blank, and 7,672 null. "
            "The Tribunal's final tables, stored here, print 705,284, 201,105, 9,967, and 7,673.",
        )
    if day.year in (1992, 1998):
        p.add(
            "note",
            id=key("note", eid, "context"),
            entity_id=eid,
            kind="context",
            lang="en",
            body_md="The Tribunal's compendium lists the referendums of 1983, 1992, and 1998 as votes to approve "
            "reforms to the Constitution, and says that only those of 1983 were approved by the people.",
        )
    options = {}
    for position, (answer, label) in enumerate((("yes", "Sí"), ("no", "No")), 1):
        options[answer] = key("option", day, answer)
        p.add("contest_option", id=options[answer], contest_id=cid, kind="answer", ballot_position=position,
              ballot_label=label)  # fmt: skip
        p.name(options[answer], label, "es", "native")
        p.name(options[answer], "Yes" if answer == "yes" else "No", "en", "official", False)
    units: dict[tuple[str, str], str] = {}

    def unit(u: Unit) -> str:
        if (u.scheme, u.code) not in units:
            uid = units[(u.scheme, u.code)] = key("unit", day, u.scheme, u.code)
            if area(u.scheme, u.code) == te.area(u.scheme, u.code):
                p.external.add(area(u.scheme, u.code))  # a row the archive already holds
            p.add("reporting_unit", id=uid, polity_id=PANAMA, scheme_code=u.scheme, code=f"{day}:{u.code}",
                  area_relation="equals", area_id=area(u.scheme, u.code), kind="standard")  # fmt: skip
        return units[(u.scheme, u.code)]

    def result_set(tag: str, note: str, default: bool, supersedes: str | None) -> str:
        rid = key("result_set", day, tag)
        p.add("result_set", id=rid, election_id=eid, contest_id=cid, stage="official", kind="counted", source_id=source,
              status="active", is_default=default, supersedes_id=supersedes, completeness=1.0)  # fmt: skip
        p.add("note", id=key("note", rid), entity_id=rid, kind="methodology", body_md=note, lang="en")
        return rid

    def values(rid: str, uid: str, c: Counts, flags: int = 0) -> None:
        for measure, value, flag in (
            (REGISTERED, c.reg, 0),
            (CAST, c.cast, 0),
            (VALID, c.valid, DERIVED),
            (INVALID, c.null, 0),
            (BLANK, c.blank, 0),
        ):
            if value is not None:
                p.add("result_value", result_set_id=rid, unit_id=uid, option_id=None, channel=0, measure=measure,
                      count_round=0, flags=flag, value=value)  # fmt: skip
        for answer, value in (("yes", c.yes), ("no", c.no)):
            p.add("result_value", result_set_id=rid, unit_id=uid, option_id=options[answer], channel=0, measure=VOTES,
                  count_round=0, flags=flags, value=value)  # fmt: skip

    def declare(rid: str, c: Counts) -> None:
        for measure, value in ((REGISTERED, c.reg), (CAST, c.cast), (INVALID, c.null), (BLANK, c.blank)):
            if value is not None:
                p.add("declared", result_set_id=rid, option_id=None, measure=measure, value=value)
        for answer, value in (("yes", c.yes), ("no", c.no)):
            p.add("declared", result_set_id=rid, option_id=options[answer], measure=VOTES, value=value)

    whole = table(event, event.circuits, False)
    method = METHOD + (METHOD_1992 if day.year == 1992 else "")
    by_circuit = result_set("circuit", method, not event.fine, None)
    for c in whole.circuits:
        values(by_circuit, unit(c), c.counts)
    declare(by_circuit, whole.total)
    if event.fine:
        fine = result_set("corregimiento", method + METHOD_FINE, True, by_circuit)
        printed = fine_rows(event)
        for u in merged(printed):
            values(fine, unit(u), u.counts)
        declare(fine, whole.total)
        p.add("note", id=key("note", fine, "shared"), entity_id=fine, kind="caveat", lang="en", body_md=SHARED)
        if day.year == 1998:
            p.add("note", id=key("note", fine, "pucuro"), entity_id=fine, kind="caveat", lang="en", body_md=PUCURO)
    return p


def parse() -> None:
    for event in EVENTS:
        for line in compare(event):
            print(line, flush=True)
    geo().write()
    for event in EVENTS:
        election(event).write()


def emit() -> None:
    package.run("load", packages())
