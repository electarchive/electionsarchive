"""Austrian Volksbegehren (citizens' initiatives), 1964 to 2026, from the Federal Ministry of the Interior.

The ministry lists every initiative with a signing period in one ranking table. Since 1996 each initiative has its
own page with the final result, and the pages carry two kinds of figures. Those of 2018 and later come as a CSV or
workbook with one row per municipality. The earlier ones come as tables by state and political district on the page
itself. The 17 initiatives of 1964 to 1995 have only the national count of valid signatures in the ranking.
"""

import csv
import difflib
import functools
import html
import io
import re
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.bmi"
BASE = "https://www.bmi.gv.at/411/"
RANKING = f"{BASE}alle_volksbegehren_der_zweiten_republik.html"
OVERVIEW = f"{BASE}volksbegehren_der_xx_gesetzgebungsperiode/start.html"
PAGES = f"{BASE}volksbegehren_der_xx_gesetzgebungsperiode/"
MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "Jänner",
            "Februar",
            "März",
            "April",
            "Mai",
            "Juni",
            "Juli",
            "August",
            "September",
            "Oktober",
            "November",
            "Dezember",
        ],
        start=1,
    )
}
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))
GEO_KEY = "at.bmi/geo"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, "vbg", kind, *(str(part) for part in parts)])))


def old_key(kind: str, *parts: object) -> str:
    """Ids as the package of the ministry's election results derives them, to reach the areas it already holds."""
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return old_key("area", scheme, code)


SOURCE_ID = key("source")
OFFICE = key("office", "volksbegehren")


def text_of(cell: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).replace("\xa0", " ").split())


def number(text: str) -> int:
    """A count, with dots as the thousands separator; a few cells in the ministry's tables use commas or both."""
    text = text.replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", text):
        return int(re.sub(r"[.,]", "", text))
    return int(text.replace(".", "").split(",")[0])


@dataclass
class Row:
    """One line of the ministry's ranking of all initiatives."""

    title: str
    start: date
    end: date
    valid: int
    percent: float
    href: str | None


def _period(text: str) -> tuple[date, date]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.?-(\d+)\.(\d+)\.(\d{4})", re.sub(r"\s+", "", text))
    assert match is not None, text
    d1, m1, d2, m2, y2 = (int(g) for g in match.groups())
    return date(y2 - (1 if m1 > m2 else 0), m1, d1), date(y2, m2, d2)


def _read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")


@functools.cache
def ranking() -> list[Row]:
    page = _read(RANKING)
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", page, flags=re.DOTALL)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)
        start, end = _period(text_of(cells[2]).replace(" ", ""))
        link = re.search(r'href="(volksbegehren_der_xx_gesetzgebungsperiode/[^"#]+)"', tr)
        title = re.sub(r"\s*\(pdf,.*?\)", "", text_of(cells[1]))
        rows.append(
            Row(
                title.replace("„", '"').replace("“", '"').strip(),
                start,
                end,
                number(text_of(cells[3])),
                float(text_of(cells[4]).replace(",", ".")),
                link.group(1) if link else None,
            )
        )
    return rows


@dataclass
class Page:
    folder: str
    title: str
    start: date
    end: date

    @property
    def url(self) -> str:
        return f"{PAGES}{self.folder}"


@functools.cache
def pages() -> list[Page]:
    overview = _read(OVERVIEW)
    found = []
    for block in re.findall(
        r"<h2>(.*?)</h2>\s*<p><strong>\(([^)]*)\)\s*</strong></p>\s*<ul>(.*?)</ul>", overview, flags=re.DOTALL
    ):
        title, span, links = block
        link = re.search(r'href="([^":#]+/start[^":#]*\.html)', links)
        assert link is not None, title
        period = re.fullmatch(r"(\d+)\.\s*(?:(\w+)\s+)?bis\s+(\d+)\.\s*(\w+)\s+(\d{4})", text_of(span))
        assert period is not None, span
        d1, m1, d2, m2, y2 = period.groups()
        end = date(int(y2), MONTHS[m2], int(d2))
        month = MONTHS[m1 or m2]
        start = date(int(y2) - (1 if month > MONTHS[m2] else 0), month, int(d1))
        found.append(Page(link.group(1), text_of(title), start, end))
    return found


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("volksbegehren", ""))
    return re.sub(r"[^a-z0-9]+", " ", text.encode("ascii", "ignore").decode()).strip()


@dataclass
class Initiative:
    row: Row
    page: Page | None
    files: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.page.title if self.page else self.row.title


@functools.cache
def initiatives() -> list[Initiative]:
    """Match each ranking line to its page by the link the ranking carries, else by period and title."""
    known = {page.folder.split("/")[0]: page for page in pages()}
    taken: set[str] = set()
    found = []
    for row in ranking():
        page = known.get(row.href.split("/")[1]) if row.href else None
        if page is None:
            options = [
                p for p in pages() if p.start == row.start and p.end == row.end and p.folder.split("/")[0] not in taken
            ]
            if options:
                page = max(
                    options, key=lambda p: difflib.SequenceMatcher(None, _plain(p.title), _plain(row.title)).ratio()
                )
        if page is not None:
            taken.add(page.folder.split("/")[0])
        found.append(Initiative(row, page))
    return found


def data_file(item: Initiative) -> str | None:
    """The CSV or workbook with one row per municipality, for the initiatives that have one."""
    if item.page is None:
        return None
    page = _read(item.page.url)
    base = item.page.url.rsplit("/", 1)[0] + "/"
    links = re.findall(r'href="(files/[^"]+\.(?:csv|xlsx))"[^>]*>([^<]*)', page, flags=re.IGNORECASE)
    named = [name for name, label in links if label.strip()]
    return base + named[0] if named else None


def workbooks() -> list[str]:
    """The zipped workbooks of 1997 to 2003, whose sheets print district codes next to the names."""
    found = []
    for page in pages():
        base = page.url.rsplit("/", 1)[0] + "/"
        found += [base + f for f in re.findall(r'href="(files/[^"]+\.zip)"', _read(page.url), flags=re.IGNORECASE)]
    return list(dict.fromkeys(found))


def fetch() -> None:
    raw.fetch_many(SOURCE, [RANKING, OVERVIEW], suffix=".html")
    raw.fetch_many(SOURCE, [page.url for page in pages()], suffix=".html")
    files = [file for item in initiatives() if (file := data_file(item))]
    for suffix in ("csv", "xlsx"):
        raw.fetch_many(SOURCE, [f for f in files if f.lower().endswith(suffix)], suffix=f".{suffix}")
    raw.fetch_many(SOURCE, workbooks(), suffix=".zip")


@dataclass
class Count:
    """One reporting unit of one initiative: its code in a scheme, name, and the figures the ministry prints."""

    scheme: str
    code: str
    name: str
    registered: int | None
    invalid: int | None
    entries: int | None
    declarations: int | None
    valid: int


@dataclass
class Result:
    country: Count
    units: list[Count]


def _grid(path_url: str) -> list[list[str]]:
    path = raw.cached(SOURCE, path_url).path
    if path_url.lower().endswith(".xlsx"):
        frame = fastexcel.read_excel(path).load_sheet(0, header_row=None, dtypes="string").to_polars()
        return [["" if c is None else c.strip() for c in row] for row in frame.iter_rows()]
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=";") if any(row)]


def municipal(url: str) -> Result:
    """The 2018-and-later files: one row per state, political district, and municipality, each with its code."""
    grid = _grid(url)
    head = [c.lower() for c in grid[0]]
    column = {
        "registered": head.index("wahlberechtigte"),
        "valid": head.index("summe"),
        "declarations": next(i for i, c in enumerate(head) if c.startswith("unterst")),
        "entries": head.index("eintragungen"),
    }
    country = None
    units = []
    for row in grid[1:]:
        code = row[0].removeprefix("G")
        figures = {name: number(row[i]) for name, i in column.items()}
        count = Count(
            "at.gemeinde",
            code,
            row[1],
            figures["registered"],
            None,
            figures["entries"],
            figures["declarations"],
            figures["valid"],
        )
        if code == "00000":
            country = count
        elif len(code) == 5 and code[3:] != "00" and code != "90001":
            units.append(count)
    assert country is not None
    return Result(country, units)


LAENDER = [
    "BURGENLAND",
    "KÄRNTEN",
    "NIEDERÖSTERREICH",
    "OBERÖSTERREICH",
    "SALZBURG",
    "STEIERMARK",
    "TIROL",
    "VORARLBERG",
    "WIEN",
]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text.lower().replace("-", "").replace("\xad", ""))


def _table(markup: str) -> list[Count]:
    """Read one results table of a page: a header row naming the columns, then one line per area."""
    rows = [
        [text_of(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", markup, flags=re.DOTALL)
    ]
    heads = [_squash(h) for h in rows[0]]

    def find(test: Callable[[str], bool]) -> int | None:
        return next((i for i, h in enumerate(heads) if test(h)), None)

    column = {
        "registered": find(lambda h: "berechtigte" in h),
        "invalid": find(lambda h: h.startswith("ungültige")),
        "entries": find(lambda h: h.startswith("gültigeeintragungen") and "+" not in h),
        "declarations": find(lambda h: h.startswith("unterstützungserklärungen") and "+" not in h),
        "valid": find(lambda h: h.startswith("gültigeeintragungen") and "+" in h),
    }

    def cell(row: list[str], name: str) -> int | None:
        index = column[name]
        return None if index is None else number(row[index])

    counts = []
    for row in rows[1:]:
        entries = cell(row, "entries")
        declarations = cell(row, "declarations")
        valid = cell(row, "valid")
        assert entries is not None
        counts.append(
            Count(
                "at.bezirk",
                "",
                " ".join(row[0].replace("\xa0", " ").split()),
                cell(row, "registered"),
                cell(row, "invalid"),
                entries,
                declarations,
                valid if valid is not None else entries,
            )
        )
    return counts


def printed(page: Page) -> Result:
    """The tables of the pages before 2018: states, then each state's political districts."""
    markup = _read(page.url)
    tables = [_table(t) for t in re.findall(r"<table.*?</table>", markup, flags=re.DOTALL) if "Gebiet" in t[:900]]
    overview = next(t for t in tables if t[-1].name == "ÖSTERREICH")
    country = overview[-1]
    states = {c.name: c for c in overview[:-1]}
    units: list[Count] = []
    for table in tables:
        if table is overview:
            continue
        land = table[-1]
        assert land.name in states, (page.folder, land.name)
        digit = str(LAENDER.index(land.name) + 1)
        for count in table[:-1]:
            count.scheme = "at.bezirk"
            count.code = roster()[(digit, _norm(count.name))]
            units.append(count)
    if not units:
        for count in overview[:-1]:
            count.scheme = "at.land"
            count.code = str(LAENDER.index(count.name) + 1)
            units.append(count)
    return Result(country, units)


def _norm(text: str) -> str:
    text = re.sub(r"\(.*?\)", "", text.lower().replace("wr.", "wiener").replace("ß", "ss"))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]+", "", text.replace("sankt", "st"))


@functools.cache
def roster() -> dict[tuple[str, str], str]:
    """Political districts by state digit and name, from the files that print codes."""
    found: dict[tuple[str, str], str] = {}
    for url in workbooks():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            try:
                book = fastexcel.read_excel(archive.read(archive.namelist()[0]))
            except fastexcel.CalamineError:  # the 2002 workbook is a format the reader cannot open
                continue
        for row in book.load_sheet("Daten", header_row=None, dtypes="string").to_polars().iter_rows():
            if row[0] and re.fullmatch(r"\d{3}", row[0]) and row[1]:
                found.setdefault((row[0][0], _norm(row[1])), row[0])
    for item in initiatives():
        if (file := data_file(item)) is None:
            continue
        for row in _grid(file)[1:]:
            code = row[0].removeprefix("G")
            if len(code) == 5 and code[3:] == "00" and code[1:3] != "00":
                found.setdefault((code[0], _norm(row[1])), code[:3])
            elif len(code) == 5 and code[0] == "9" and code[3:] == "01" and code != "90001":
                found.setdefault(("9", _norm(row[1])), code[:3])
    return found


def result_of(item: Initiative) -> Result | None:
    if item.page is None:
        return None
    file = data_file(item)
    return municipal(file) if file else printed(item.page)


def election_key(start: date) -> str:
    return f"at.bmi/vbg-{start}"


def periods() -> dict[date, list[Initiative]]:
    found: dict[date, list[Initiative]] = defaultdict(list)
    for item in initiatives():
        found[item.row.start].append(item)
    return dict(sorted(found.items()))


def packages() -> list[str]:
    return [f"{SOURCE}/vbg-geo", f"{SOURCE}/vbg-registry", *(election_key(start) for start in periods())]


def parse() -> None:
    geo().write()
    registry().write()
    for start, items in periods().items():
        election(start, items).write()


def emit() -> None:
    package.run("load", packages())


def artifacts(item: Initiative) -> list[raw.Artifact]:
    urls = [RANKING]
    if item.page is not None:
        urls.append(item.page.url)
        if (file := data_file(item)) is not None:
            urls.append(file)
    return [raw.cached(SOURCE, url) for url in urls]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/vbg-registry",
        SOURCE,
        "The source and the office that stands for a Volksbegehren in the Federal Ministry of the Interior's results",
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Bundesministerium für Inneres",
        title="Volksbegehren: Alle Volksbegehren der Zweiten Republik, with the results of each",
        url=RANKING,
        attribution="Bundesministerium für Inneres",
    )
    seen = set()
    for item in initiatives():
        for artifact in artifacts(item):
            if artifact.url not in seen:
                seen.add(artifact.url)
                p.artifact(artifact, SOURCE_ID)
    p.add("office", id=OFFICE, polity_id=AUSTRIA, kind="other")
    p.name(OFFICE, "Citizens' initiative (Volksbegehren)", "en", "official")
    p.name(OFFICE, "Volksbegehren", "de", "native", False)
    return p


def needed() -> dict[str, dict[str, str]]:
    """Every area the initiatives use, by scheme and code, with its name."""
    found: dict[str, dict[str, str]] = {"at.land": {}, "at.bezirk": {}, "at.gemeinde": {}}
    for item in initiatives():
        if (result := result_of(item)) is None:
            continue
        for unit in result.units:
            found[unit.scheme].setdefault(unit.code, unit.name)
    return found


def geo() -> Package:
    from ea.sources.at import bmi

    p = Package(
        f"{SOURCE}/vbg-geo",
        SOURCE,
        "Municipalities and political districts that the Volksbegehren files print and the ministry's other election "
        "results do not",
        depends=[GEO_KEY],
    )
    p.external |= {AUSTRIA, COUNTRY}
    held = bmi.places()
    for scheme, parent_scheme in (("at.land", None), ("at.bezirk", "at.land"), ("at.gemeinde", "at.bezirk")):
        for code, name in sorted(needed()[scheme].items()):
            if code in held[scheme]:
                continue
            id = area(scheme, code)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=code)
            parent = (
                COUNTRY
                if parent_scheme is None
                else area(parent_scheme, code[0] if parent_scheme == "at.land" else code[:3])
            )
            p.add("area_parent", area_id=id, parent_id=parent)
            p.name(id, bmi.name_of(name), "de", "official")
    return p


METHOD = (
    "Final result of the signing period (Eintragungszeitraum) as the Federal Ministry of the Interior prints it. "
    "Each reporting unit holds the registered voters (Stimmberechtigte) and the valid signatures, split into those "
    "signed during the period (gültige Eintragungen) and the supporting declarations (Unterstützungserklärungen) "
    "collected before it, which count toward the total since 1997. Signatures are not votes for or against anything, "
    "and the ministry does not publish a turnout in the usual sense: the percentage it prints is signatures over "
    "registered voters."
)
NATIONAL_METHOD = (
    "The ministry's ranking of all initiatives prints only the national count of valid signatures and the share of "
    "registered voters it makes, so the result holds one national unit. The figure is the sum of the valid entries "
    "(gültige Eintragungen) and any supporting declarations (Unterstützungserklärungen) that counted at the time."
)


def _title(item: Initiative) -> str:
    return item.page.title if item.page else item.row.title


def _names(start: date, end: date) -> tuple[str, str]:
    month = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ][end.month - 1]
    german = list(MONTHS)[end.month - 1]
    if start.month == end.month:
        return (
            f"{start.day}–{end.day} {month} {end.year}",
            f"{start.day}.–{end.day}. {german} {end.year}",
        )
    other = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ][start.month - 1]
    return (
        f"{start.day} {other}–{end.day} {month} {end.year}",
        f"{start.day}. {list(MONTHS)[start.month - 1]}–{end.day}. {german} {end.year}",
    )


def _sum(units: list[Count], field_name: str) -> int | None:
    values = [getattr(u, field_name) for u in units]
    return None if any(v is None for v in values) else sum(values)


def election(start: date, items: list[Initiative]) -> Package:
    end = items[0].row.end
    p = Package(
        election_key(start),
        SOURCE,
        f"Austrian Volksbegehren with the signing period of {start} to {end}, from the Federal Ministry of the Interior",
        depends=[GEO_KEY, f"{SOURCE}/vbg-geo", f"{SOURCE}/vbg-registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    english, german = _names(start, end)
    id = key("election", start)
    p.add(
        "election",
        id=id,
        polity_id=AUSTRIA,
        kind="referendum",
        date_from=start,
        date_to=end,
        status="held",
    )
    plural = "initiatives" if len(items) > 1 else "initiative"
    p.name(id, f"Austrian citizens' {plural}, {english}", "en", "official")
    p.name(id, f"Volksbegehren, {german}", "de", "native", False)
    path = f"at/{start}-volksbegehren"
    p.add("slug", path=path, entity_id=id, is_current=True)
    units: dict[str, str] = {}
    mismatches: list[str] = []
    placed: set[str] = set()

    def unit_of(count: Count, country: bool = False) -> str:
        code = "0" if country else count.code
        scheme = "at.land" if country else count.scheme
        name = (count.name if not country else "Österreich").replace("\xa0", " ")
        ident = f"{scheme}:{code}"
        if ident not in units:
            unit = units[ident] = key("unit", start, scheme, code)
            area_id = COUNTRY if country else area(scheme, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=AUSTRIA,
                scheme_code=scheme,
                code=f"{start}:{code}",
                area_relation="equals",
                area_id=area_id,
                kind="aggregate" if country else "standard",
            )
            p.name(unit, name if country else bmi_name(name), "de", "official")
        return units[ident]

    for item in items:
        title = _title(item)
        slug = changeset.slug(title)
        assert slug not in placed, slug
        placed.add(slug)
        result = result_of(item)
        contest = key("contest", start, slug)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="ballot_question",
            office_id=OFFICE,
            district_area_id=COUNTRY,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, title, "de", "official")
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="initiative")
        for artifact in artifacts(item):
            p.uses(artifact)
        result_set = key("result_set", start, slug)
        entries = key("option", start, slug, "entries")
        declarations = key("option", start, slug, "declarations")
        has_declarations = result is not None and any(
            u.declarations is not None for u in [result.country, *result.units]
        )
        p.add(
            "contest_option",
            id=entries,
            contest_id=contest,
            kind="answer",
            ballot_position=1,
            ballot_label="Gültige Eintragungen",
        )
        if has_declarations:
            p.add(
                "contest_option",
                id=declarations,
                contest_id=contest,
                kind="answer",
                ballot_position=2,
                ballot_label="Unterstützungserklärungen",
            )
        if result is None:
            completeness = 1.0
        else:
            completeness = round(sum(u.valid for u in result.units) / result.country.valid, 4)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=min(completeness, 1.0),
        )
        p.add(
            "note",
            id=key("note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD if result is not None else NATIONAL_METHOD,
            lang="en",
        )
        p.add(
            "note",
            id=key("note", result_set, "ranking"),
            entity_id=result_set,
            kind="context",
            body_md=f"The ministry's ranking lists {item.row.valid:,} valid signatures, {item.row.percent:.2f}% "
            "of registered voters.",
            lang="en",
        )

        def declare(measure: int, option: str | None, value: int | None, rs: str = result_set) -> None:
            if value is not None:
                p.add("declared", result_set_id=rs, option_id=option, measure=measure, value=value)

        if result is None:
            country = Count("at.land", "0", "Österreich", None, None, item.row.valid, None, item.row.valid)
            declare(4, None, country.valid)
            declare(1, entries, country.valid)
            unit = unit_of(country, True)
            for measure, option, value in ((4, None, country.valid), (1, entries, country.valid)):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            continue
        country = result.country
        declare(2, None, country.registered)
        declare(5, None, country.invalid)
        declare(4, None, country.valid)
        declare(1, entries, country.entries)
        if has_declarations:
            declare(1, declarations, country.declarations)
        for count in result.units:
            unit = unit_of(count)
            figures = [(2, None, count.registered), (5, None, count.invalid), (4, None, count.valid)]
            figures.append((1, entries, count.entries))
            if has_declarations:
                figures.append((1, declarations, count.declarations))
            for measure, option, value in figures:
                if value is None:
                    continue
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for label, field_name in (
            ("registered voters", "registered"),
            ("invalid entries", "invalid"),
            ("valid signatures", "valid"),
            ("valid entries", "entries"),
            ("supporting declarations", "declarations"),
        ):
            total, printed_total = _sum(result.units, field_name), getattr(country, field_name)
            if printed_total is not None and total != printed_total:
                mismatches.append(f"{title}: {label} printed {printed_total:,}, units add up to {total:,}")
    if mismatches:
        reason = (
            "The ministry's own tables disagree: "
            + "; ".join(mismatches)
            + ". The figures are stored as printed, and the national totals are the ones the ministry prints."
        )
        p.accept("accounting.declared", reason)
    return p


def bmi_name(text: str) -> str:
    text = " ".join(text.split())
    return text.title() if text.isupper() else text
