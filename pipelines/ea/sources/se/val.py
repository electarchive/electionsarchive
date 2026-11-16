import html
import io
import re
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import NamedTuple
from uuid import uuid5
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "se.val"
SITE = "https://www.val.se"
HISTORY = "https://historik.val.se/val"
DOWNLOAD = f"{SITE}/download"
PARSED = DATA / "parsed" / SOURCE
WORKERS = 6
SWEDEN = str(uuid5(package.NAMESPACE, "polity\x1fSE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fSWE"))

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


# Sources.


def xlsx_url(path: str) -> str:
    return f"{DOWNLOAD}/{path}"


def history(path: str) -> str:
    return f"{HISTORY}/{path}"


LONG = {
    2026: {
        "R": "18.7faaad3f1a0b0c300e458a/1790069872234/roster-per-distrikt-slutligt-antal-roster-inklusive-totalt-valdeltagande-riksdagsvalet-2026.xlsx",
        "L": "18.7faaad3f1a0b0c300e4183a/1791371611110/roster-per-distrikt-slutligt-antal-roster-inklusive-totalt-valdeltagande-regionvalen-2026.xlsx",
        "K": "18.70e610f1a1abb5e3c818/1793892152055/roster-per-distrikt-slutligt-antal-roster-inklusive-totalt-valdeltagande-kommunvalen-2026.xlsx",
    },
    2022: {
        "R": "18.162047b519a91d0533118f4b/1764336897948/Roster-per-distrikt-slutligt-antal-roster-inklusive-totalt-valdeltagande-riksdagsvalet-2022.xlsx",
        "L": "18.162047b519a91d0533118f4d/1764336772618/roster-per-distrikt-slutligt-antal-roster-inklusive-totalt-valdeltagande-regionval-2022.xlsx",
        "K": "18.162047b519a91d0533118f4e/1764337121617/roster-per-distrikt-slutligt-antal-roster-inklusive-totalt-valdeltagande-kommunval-2022.xlsx",
    },
}
NATIONAL = {
    2026: "18.7faaad3f1a0b0c300e47fc/1790317850404/slutligt-valresultat-riksdagen-jamforande-statistik-2022-2026.xlsx",
    2022: "18.162047b519a91d05331197bd/1786611369096/slutligt-valresultat-riksdagen-jamforande-statistik-2018-2022.xlsx",
}
EP_2024 = xlsx_url(
    "18.162047b519a91d05331178aa/1718718968853/slutligt-valresultat-europaparlamentet-2024-vallokalernas-rostrakning.xlsx"
)
SHEETS_2018 = {body: history(f"val2018/statistik/2018_{body}_per_valdistrikt.xlsx") for body in "RLK"}
PARTIES_2018 = history("val2018/valsedlar/partier/deltagande_partier.skv")
SKV_2014 = {
    "R": history("val2014/statistik/2014_riksdagsval_per_valdistrikt.skv"),
    "L": history("val2014/statistik/2014_landstingsval_per_valdistrikt.skv"),
    "K": history("val2014/statistik/2014_kommunval_per_valdistrikt.skv"),
}
PARTIES_2014 = history("val2014/statistik/partier_som_anmalt_kandidater_RLK_uppdaterad.xls")
ZIPS = {
    "2010": history("val2010/slutresultat/slutresultat.zip"),
    "ep2009": history("ep2009/slutresultat/slutresultat.zip"),
    "ep2014": history("ep2014/slutresultat/slutresultat.zip"),
    "omval2015": history("omval2015/slutresultat/slutresultat.zip"),
    "omval2019": history("omval2019/slutresultat/slutresultat.zip"),
}
NAMES_2010 = {
    "R": history("val2010/statistik/slutligt_valresultat_valdistrikt_R.skv"),
    "L": history("val2010/statistik/slutligt_valresultat_valdistrikt_L.skv"),
    "K": history("val2010/statistik/slutligt_valresultat_valdistrikt_K_antal.skv"),
}
EP_2019 = history("ep2019/statistik/ep2019_rostfordelning_per_valdistrikt.xlsx")
PARTIES_EP_2019 = history("ep2019/valsedlar/partier/deltagande_partier.skv")
INDEX_2006 = history("val2006/slutlig/xml/index.html")
XML_DIRS = {
    "2006": ("val2006/slutlig/xml", "RLK"),
    "emu2003": ("emu2003/resultat/slutresultat", "F"),
    "ep2004": ("ep2004/resultat/slutresultat", "E"),
}
TEXT_2002 = "val_02/slutresultat"
TOTALS = {
    "2002-09-15": {body: history(f"{TEXT_2002}/00{body}/00-text.html") for body in "RLK"},
    "2014-09-14": {
        "R": history("val2014/statistik/2014_riksdagsval_per_kommun.skv"),
        "L": history("val2014/statistik/2014_landstingsval_per_kommun.skv"),
        "K": history("val2014/statistik/2014_kommunval_per_kommun.skv"),
    },
    "2018-09-09": {body: history(f"val2018/statistik/2018_{body}_per_kommun.xlsx") for body in "RLK"},
    "2019-05-26": {"E": history("ep2019/statistik/ep2019_rostfordelning_per_kommun.xlsx")},
}
"""The authority's own national or per-municipality tables, against which the district sums are compared."""


RENUMBERED = {"1917": "0331"}
"""Heby moved from Västmanland to Uppsala county in 2007 and took a new code; earlier files print the old one."""


def kommuner() -> list[str]:
    """The 290 municipality codes, read from the 2006 results index (unchanged since Knivsta's 2003 split)."""
    text = raw.cached(SOURCE, INDEX_2006).path.read_text("latin-1")
    return sorted(set(re.findall(r"slutresultat_(\d{4})[RLK]\.xml", text)))


def xml_url(folder: str, code: str, body: str) -> str:
    return history(f"{XML_DIRS[folder][0]}/slutresultat_{code}{body}.xml")


def text_url(body: str, code: str) -> str:
    return history(f"{TEXT_2002}/{code[:2]}{body}/{code}/{code}-text.html")


def page_url(body: str, code: str) -> str:
    return history(f"{TEXT_2002}/{code[:2]}{body}/{code}/{code}.html")


def county_url(county: str) -> str:
    return history(f"{TEXT_2002}/{county}R/{county}.html")


def constituency_urls() -> list[str]:
    """The 2002 Riksdag constituency pages, linked from each county's page."""
    found: list[str] = []
    for county in sorted({code[:2] for code in kommuner()}):
        text = raw.cached(SOURCE, county_url(county)).path.read_text("utf-8", "replace")
        found += [
            history(f"{TEXT_2002}/{path}")
            for path in sorted(
                set(re.findall(r'/val/val_02/slutresultat/(\d{2}R/\d{4}KR\.html)"', text, re.IGNORECASE))
            )
        ]
    return found


def _try(url: str, suffix: str) -> None:
    try:
        raw.fetch(SOURCE, url, suffix=suffix)
    except httpx.HTTPStatusError as failure:
        print(f"{url}: {failure.response.status_code}", flush=True)


def fetch() -> None:
    for year, files in LONG.items():
        for path in files.values():
            raw.fetch(SOURCE, xlsx_url(path), suffix=".xlsx")
        raw.fetch(SOURCE, xlsx_url(NATIONAL[year]), suffix=".xlsx")
    for url in [EP_2024, *SHEETS_2018.values(), EP_2019]:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for url in [*SKV_2014.values(), *NAMES_2010.values(), PARTIES_2018, PARTIES_EP_2019]:
        raw.fetch(SOURCE, url, suffix=".skv")
    raw.fetch(SOURCE, PARTIES_2014, suffix=".xls")
    for files in TOTALS.values():
        for url in files.values():
            raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
    for url in ZIPS.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    raw.fetch(SOURCE, INDEX_2006, suffix=".html")
    codes = kommuner()
    jobs = [
        (xml_url(folder, code, body), ".xml")
        for folder, (_, bodies) in XML_DIRS.items()
        for body in bodies
        for code in ["00", *codes, *RENUMBERED]
    ]
    jobs += [
        (url(body, code), ".html") for url in (text_url, page_url) for body in "RLK" for code in [*codes, *RENUMBERED]
    ]
    jobs += [(county_url(county), ".html") for county in sorted({code[:2] for code in codes})]
    known = raw.keys(SOURCE)
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda job: _try(*job), [job for job in jobs if job[0] not in known]))
    for url in constituency_urls():
        raw.fetch(SOURCE, url, suffix=".html")


# The parsed form every reader returns.

FACTS = {
    "body": pl.String,
    "contest": pl.String,
    "unit": pl.String,
    "label": pl.String,
    "measure": pl.Int16,
    "value": pl.Int64,
}
UNITS = {"unit": pl.String, "kommun": pl.String, "name": pl.String}
OPTIONS = {"body": pl.String, "contest": pl.String, "label": pl.String, "kind": pl.String, "abbr": pl.String}
SUMMARY_SCHEMA = {"body": pl.String, "contest": pl.String, "label": pl.String, "measure": pl.Int16, "value": pl.Int64}
SEATS = {"body": pl.String, "contest": pl.String, "label": pl.String, "seats": pl.Int32}


class Parsed(NamedTuple):
    facts: pl.DataFrame
    units: pl.DataFrame
    options: pl.DataFrame
    summary: pl.DataFrame
    seats: pl.DataFrame


class Builder:
    def __init__(self) -> None:
        self.facts: list[tuple[str, str, str, str | None, int, int]] = []
        self.units: dict[str, tuple[str, str | None]] = {}
        self.options: dict[tuple[str, str, str], tuple[str, str | None]] = {}
        self.summary: list[tuple[str, str, str | None, int, int]] = []
        self.seats: list[tuple[str, str, str, int]] = []
        self.missing: list[tuple[str, str]] = []

    def unit(self, unit: str, kommun: str, name: str | None) -> None:
        name = clean(name) or None
        if name is not None and fold(name) == "namnet gallrat":
            name = None
        known = self.units.get(unit)
        if known is not None and known[0] != kommun:
            raise ValueError(f"unit {unit} is in {known[0]} and {kommun}")
        if known is None or known[1] is None:
            self.units[unit] = (kommun, name)

    def option(self, body: str, contest: str, label: str, kind: str = "list", abbr: str | None = None) -> str:
        label = clean(label)
        self.options.setdefault((body, contest, label), (kind, abbr))
        return label

    def fact(self, body: str, contest: str, unit: str, label: str | None, measure: int, value: int | None) -> None:
        if value is not None:
            self.facts.append((body, contest, unit, label, measure, value))

    def done(self) -> Parsed:
        facts = pl.DataFrame(self.facts, schema=FACTS, orient="row")
        facts = facts.group_by(list(FACTS)[:5], maintain_order=True).agg(pl.col("value").sum())
        return Parsed(
            facts,
            pl.DataFrame([(u, k, n) for u, (k, n) in self.units.items()], schema=UNITS, orient="row"),
            pl.DataFrame([(*k, *v) for k, v in self.options.items()], schema=OPTIONS, orient="row"),
            pl.DataFrame(self.summary, schema=SUMMARY_SCHEMA, orient="row")
            .group_by(list(SUMMARY_SCHEMA)[:4], maintain_order=True)
            .agg(pl.col("value").sum()),
            pl.DataFrame(self.seats, schema=SEATS, orient="row"),
        )


def number(text: object) -> int | None:
    if text is None:
        return None
    value = str(text).strip().replace(" ", "").replace("\xa0", "")
    if value in ("", "-", "None", "null"):
        return None
    if re.fullmatch(r"-?\d+([.,]0+)?", value):
        return int(float(value.replace(",", ".")))
    return int(value) if value.lstrip("-").isdigit() else None


# Riksdag constituencies: the municipality-to-constituency map is unchanged since 1998.


def _long(year: int, body: str) -> pl.DataFrame:
    frame = pl.read_excel(
        raw.cached(SOURCE, xlsx_url(LONG[year][body])).path,
        sheet_name=f"roster_{_sheet(year, body)}",
        infer_schema_length=0,
    )
    frame = frame.rename({c: c.strip() for c in frame.columns})
    return frame.with_columns(pl.col(c).str.strip_chars() for c in frame.columns)


def _sheet(year: int, body: str) -> str:
    return {"R": "RD", "L": "RF", "K": "KF"}[body]


def constituencies() -> dict[str, str]:
    """The 29 Riksdag constituencies, code by name, numbered as in the 2026 district file."""
    frame = _long(2026, "R").select("Valkretskod", "Valkretsnamn").unique()
    return {name: code for code, name in frame.iter_rows()}


def riksdag(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Each municipality's Riksdag constituency code, from (municipality, constituency name) pairs; the boundaries
    between Västra Götaland's four constituencies moved, so each election's own files place its municipalities."""
    codes = {fold(name): code for name, code in constituencies().items()}
    found: dict[str, str] = {}
    for kommun, name in pairs:
        code = codes[fold(name)]
        if found.setdefault(kommun, code) != code:
            raise ValueError(f"{kommun} is in two Riksdag constituencies")
    return found


def places() -> tuple[dict[str, str], dict[str, str]]:
    """The county (län) and municipality names, from the 2018 and 2026 district files."""
    counties = {
        f"{int(a):02d}": b
        for a, b in pl.read_excel(
            raw.cached(SOURCE, SHEETS_2018["R"]).path, sheet_name="R antal", infer_schema_length=0
        )
        .select("LÄNSKOD", "LÄNSNAMN")
        .unique()
        .iter_rows()
    }
    towns = dict(_long(2026, "R").select("Kommunkod", "Kommunnamn").unique().iter_rows())
    return counties, towns


def contest_of(body: str, kommun: str, valkrets: dict[str, str]) -> str:
    if body == "R":
        return valkrets[kommun]
    if body == "L":
        return kommun[:2]
    if body == "K":
        return RENUMBERED.get(kommun, kommun)
    return "00"


# Readers.

LONG_CATEGORIES = {
    "valdeltagande": (CAST, None),
    "summa giltiga roster": (VALID, None),
    "rostberattigade": (None, None),
    "blanka roster": (BLANK, None),
    "ogiltiga roster blanka": (BLANK, None),
    "ovriga ogiltiga": (INVALID, None),
    "ogiltiga roster ovriga": (INVALID, None),
    "ogiltiga roster ej anmalda partier": (INVALID, None),
    "ej anmalt deltagande": (INVALID, None),
    "ovriga anmalda partier": (VOTES, "others"),
}


def read_long(year: int) -> Parsed:
    frame = _long(year, "R")
    valkrets = riksdag(frame.select(pl.col("Valdistriktskod").str.slice(0, 4), "Valkretsnamn").unique().iter_rows())
    b = Builder()
    for body in "RLK":
        _long_rows(b, _long(year, body), body, valkrets, f"{year} {body}")
    return b.done()


def _long_rows(b: Builder, frame: pl.DataFrame, body: str, valkrets: dict[str, str], where: str) -> None:
    """Rows of (district, party or category, votes), with each district's roll in its own column."""
    party = next(c for c in frame.columns if c.startswith("Parti"))
    district = "Valdistriktskod"
    name = next(c for c in frame.columns if c.startswith("Valdistrikt") and c != district)
    for code, text in frame.select(district, name).unique().iter_rows():
        b.unit(code, code[:4], text)
    registered = frame.select(district, pl.col("Röstberättigade").cast(pl.Int64)).unique()
    if registered[district].is_duplicated().any():
        raise ValueError(f"{where}: a district has two rolls")
    for code, value in registered.iter_rows():
        if value:
            b.fact(body, contest_of(body, code[:4], valkrets), code, None, REGISTERED, value)
    for code, label, value in frame.select(district, party, pl.col("Röster").cast(pl.Int64)).iter_rows():
        contest = contest_of(body, code[:4], valkrets)
        category = LONG_CATEGORIES.get(fold(label))
        if category is None:
            if "giltig" in fold(label):
                raise ValueError(f"{where}: unknown category {label}")
            b.fact(body, contest, code, b.option(body, contest, label), VOTES, value)
        elif category[1] == "others":
            b.fact(body, contest, code, b.option(body, contest, label, "others"), VOTES, value)
        elif category[0] is not None:
            b.fact(body, contest, code, None, category[0], value)


def read_2018() -> Parsed:
    frame = pl.read_excel(raw.cached(SOURCE, SHEETS_2018["R"]).path, sheet_name="R antal", infer_schema_length=0)
    valkrets = riksdag(
        (f"{int(a):02d}{int(b):02d}", c)
        for a, b, c in frame.select("LÄNSKOD", "KOMMUNKOD", "VALKRETSNAMN").unique().iter_rows()
    )
    labels = _labels_2018()
    b = Builder()
    specials = {"ÖVR", "OGEJ", "BLANK", "OG", "RÖSTER GILTIGA", "RÖSTANDE", "RÖSTBERÄTTIGADE", "VALDELTAGANDE"}
    for body in "RLK":
        frame = pl.read_excel(
            raw.cached(SOURCE, SHEETS_2018[body]).path, sheet_name=f"{body} antal", infer_schema_length=0
        )
        parties = [c for c in frame.columns[8:] if c not in specials]
        seen: set[str] = set()
        for row in frame.iter_rows(named=True):
            kommun = f"{int(row['LÄNSKOD']):02d}{int(row['KOMMUNKOD']):02d}"
            district = int(row["VALDISTRIKTSKOD"])
            code = f"{kommun}{district:04d}" if district else f"{kommun}{int(row['VALKRETSKOD'] or 0):02d}"
            if body == "R" and not district:
                code = f"{kommun}00"
            if code in seen:
                raise ValueError(f"2018 {body}: district {code} twice")
            seen.add(code)
            b.unit(code, kommun, row["VALDISTRIKTSNAMN"])
            contest = contest_of(body, kommun, valkrets)
            for abbr in parties:
                value = number(row[abbr])
                if value is not None:
                    label = labels.get((body, contest, abbr)) or labels.get((body, "", abbr)) or abbr
                    b.fact(body, contest, code, b.option(body, contest, label, abbr=abbr), VOTES, value)
            value = number(row["ÖVR"])
            if value is not None:
                b.fact(body, contest, code, b.option(body, contest, "Övriga partier", "others", "ÖVR"), VOTES, value)
            b.fact(body, contest, code, None, BLANK, number(row["BLANK"]))
            invalid = [v for v in (number(row["OG"]), number(row["OGEJ"])) if v is not None]
            if invalid:
                b.fact(body, contest, code, None, INVALID, sum(invalid))
            b.fact(body, contest, code, None, VALID, number(row["RÖSTER GILTIGA"]))
            b.fact(body, contest, code, None, CAST, number(row["RÖSTANDE"]))
            if number(row["RÖSTBERÄTTIGADE"]):
                b.fact(body, contest, code, None, REGISTERED, number(row["RÖSTBERÄTTIGADE"]))
    return b.done()


def _labels_2018() -> dict[tuple[str, str, str], str]:
    text = raw.cached(SOURCE, PARTIES_2018).path.read_bytes().decode("latin-1")
    frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema_length=0, quote_char=None)
    found: dict[tuple[str, str, str], str] = {}
    national: dict[tuple[str, str], set[str]] = {}
    for row in frame.iter_rows(named=True):
        body, abbr, label = row["VALTYP"], row["PARTIFÖRKORTNING"], clean(row["PARTIBETECKNING"])
        if not abbr or body not in ("R", "L", "K"):
            continue
        area = row["VALOMRÅDESKOD"] or ""
        contest = area if body != "R" else ""
        found[(body, contest, abbr)] = label
        national.setdefault((body, abbr), set()).add(label)
    for (body, abbr), names in national.items():
        if len(names) == 1:
            found[(body, "", abbr)] = next(iter(names))
    return found


def read_2014() -> Parsed:
    valkrets = _skv_riksdag(SKV_2014["R"])
    labels = _labels_2014()
    b = Builder()
    for body in "RLK":
        text = raw.cached(SOURCE, SKV_2014[body]).path.read_bytes().decode("latin-1")
        frame = pl.read_csv(io.StringIO(text), separator=";", infer_schema_length=0, quote_char=None)
        columns = frame.columns
        lan, kom = columns[0], columns[1]
        code_column = "VALDIST" if "VALDIST" in columns else "VALDISTRIKT"
        name_column = "Valdistrikt" if "Valdistrikt" in columns else "VALDISTRIKT_duplicated_0"
        tallies = [c.removesuffix(" tal") for c in columns if c.endswith(" tal")]
        seen: set[str] = set()
        for row in frame.iter_rows(named=True):
            kommun = f"{int(row[lan]):02d}{int(row[kom]):02d}"
            district = int(row[code_column])
            collecting = "uppsamling" in fold(row[name_column] or "")
            code = f"{kommun}{district:02d}" if collecting else f"{kommun}{district:04d}"
            if code in seen:
                raise ValueError(f"2014 {body}: district {code} twice")
            seen.add(code)
            b.unit(code, kommun, row[name_column])
            contest = contest_of(body, kommun, valkrets)
            for abbr in tallies:
                value = number(row[f"{abbr} tal"])
                if abbr in ("BL", "BLANK"):
                    b.fact(body, contest, code, None, BLANK, value)
                elif abbr == "OG":
                    b.fact(body, contest, code, None, INVALID, value)
                elif value is not None:
                    if abbr in ("OVR", "ÖVR"):
                        label = b.option(body, contest, "Övriga partier", "others", "ÖVR")
                    else:
                        label = b.option(body, contest, labels.get((body, abbr), abbr), abbr=abbr)
                    b.fact(body, contest, code, label, VOTES, value)
            b.fact(body, contest, code, None, VALID, number(row["Rost Giltiga"]))
            b.fact(body, contest, code, None, CAST, number(row["Rostande"]))
            if number(row["Rostb"]):
                b.fact(body, contest, code, None, REGISTERED, number(row["Rostb"]))
    return b.done()


def _skv(url: str) -> pl.DataFrame:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
    return pl.read_csv(io.StringIO(text), separator=";", infer_schema_length=0, quote_char=None)


def _skv_riksdag(url: str) -> dict[str, str]:
    frame = _skv(url)
    return riksdag(
        (f"{int(a):02d}{int(b):02d}", c)
        for a, b, c in frame.select("LAN", "KOM", "RIKSDAGSVALKRETS").unique().iter_rows()
    )


def _labels_2014() -> dict[tuple[str, str], str]:
    frame = pl.read_excel(raw.cached(SOURCE, PARTIES_2014).path, has_header=False, infer_schema_length=0)
    found: dict[tuple[str, str], set[str]] = {}
    for body, _, _, abbr, label in frame.slice(3).iter_rows():
        if body in ("R", "L", "K") and abbr:
            found.setdefault((body, abbr), set()).add(clean(label))
    clashes = {k: v for k, v in found.items() if len(v) > 1}
    if clashes:
        raise ValueError(f"2014 abbreviations name several parties: {list(clashes.items())[:5]}")
    return {k: next(iter(v)) for k, v in found.items()}


XML_BODY = {
    "Riksdagsval": "R",
    "Landstingsfullmäktigval": "L",
    "Landstingsval": "L",
    "Regionfullmäktigval": "L",
    "Kommunfullmäktigval": "K",
    "Kommunval": "K",
    "Europaparlamentsval": "E",
    "Val till Europaparlamentet": "E",
    "Folkomröstning": "F",
}
SUMMARY = {"R": "KRETS_RIKSDAG", "L": "LÄN", "K": "KOMMUN", "E": "NATION", "F": "NATION"}
WRITE_IN = "Övriga partier (handskrivna)"


def _root(data: bytes) -> ElementTree.Element:
    data = re.sub(rb"<!DOCTYPE[^>]*>", b"", data, count=1)
    return ElementTree.fromstring(data)


def _tally(
    b: Builder, body: str, contest: str, element: ElementTree.Element, labels: dict[str, str]
) -> Iterator[tuple[str | None, int, int | None]]:
    """The values one district or summary element holds: (option label, measure, value)."""
    answer = body == "F"
    for child in element:
        if child.tag == "GILTIGA":
            yield from _giltig(b, body, contest, child, labels, answer)
        elif child.tag == "ÖVRIGA_GILTIGA":
            for sub in child:
                if sub.tag == "GILTIGA":
                    yield from _giltig(b, body, contest, sub, labels, answer)
                elif sub.tag == "HANDSKRIVNA":
                    yield b.option(body, contest, WRITE_IN, "write_in"), VOTES, number(sub.get("RÖSTER"))
        elif child.tag == "HANDSKRIVNA":
            yield b.option(body, contest, WRITE_IN, "write_in"), VOTES, number(child.get("RÖSTER"))
        elif child.tag == "OGILTIGA":
            yield None, BLANK if child.get("TEXT") == "BLANK" else INVALID, number(child.get("RÖSTER"))
        elif child.tag == "VALDELTAGANDE":
            registered = number(child.get("RÖSTBERÄTTIGADE"))
            if registered:
                yield None, REGISTERED, registered
            yield None, CAST, number(child.get("SUMMA_RÖSTER"))
    yield None, VALID, number(element.get("RÖSTER"))


def _giltig(
    b: Builder, body: str, contest: str, element: ElementTree.Element, labels: dict[str, str], answer: bool
) -> Iterator[tuple[str | None, int, int | None]]:
    """A valid-vote line; an 'others' line that lists its parties (VARAV_ÖVRIGA) yields them instead."""
    parts = element.findall("VARAV_ÖVRIGA")
    if parts:
        for part in parts:
            yield from _giltig(b, body, contest, part, labels, answer)
        return
    code = element.get("PARTI") or ""
    label = labels.get(code, code)
    kind = "answer" if answer else "others" if code == "ÖVR" else "list"
    yield b.option(body, contest, label, kind, code), VOTES, number(element.get("RÖSTER"))


def read_xml(
    files: Iterable[tuple[str, bytes]], valkrets: dict[str, str] | None = None, only: set[str] | None = None
) -> Parsed:
    b = Builder()
    valkrets = dict(valkrets or {})
    seen: set[tuple[str, str]] = set()
    totals: dict[tuple[str, str], tuple[ElementTree.Element, dict[str, str]]] = {}
    for name, data in files:
        found = re.search(r"slutresultat_(\d+)([A-Z])\.xml$", name)
        if found is None:
            continue
        code = found.group(1)
        if only is not None and code != "00" and code not in only:
            continue
        root = _root(data)
        body = XML_BODY.get(root.get("VALTYP") or "", found.group(2))
        labels = {p.get("FÖRKORTNING") or "": clean(p.get("BETECKNING")) for p in root.iter("PARTI")}
        if code == "00":
            if body == "R" and not valkrets:
                valkrets.update(
                    riksdag(
                        (kommun.get("KOD") or "", krets.get("NAMN") or "")
                        for krets in root.iter("KRETS_RIKSDAG")
                        if krets.get("TYP") == "Valkrets"
                        for kommun in krets.iter("KOMMUN")
                    )
                )
            for element in root.iter("KOMMUN"):
                totals[(body, element.get("KOD") or "")] = (element, labels)
            if body == "K":
                continue
            for element in root.iter(SUMMARY[body]):
                if element.get("TYP") not in ("Valkrets", "Valområde"):
                    continue
                kod = element.get("KOD") or ""
                if only is not None:
                    continue
                if body == "R":
                    contest = valkrets[next(element.iter("KOMMUN")).get("KOD") or ""]
                else:
                    contest = "00" if body in "EF" else kod
                _summary(b, body, contest, element, labels)
            continue
        seen.add((body, code))
        if body == "K":
            for element in root.iter("KOMMUN"):
                if element.get("TYP") == "Valområde":
                    _summary(b, body, contest_of(body, code, valkrets), element, labels)
        for element in root.iter():
            if element.tag not in ("VALDISTRIKT", "ONSDAGSDISTRIKT"):
                continue
            unit = element.get("KOD") or ""
            if element.tag == "ONSDAGSDISTRIKT" and not unit.startswith(body):
                unit = f"{body}-{unit}"
            contest = contest_of(body, code, valkrets)
            b.unit(unit, code, element.get("NAMN"))
            for label, measure, value in _tally(b, body, contest, element, labels):
                b.fact(body, contest, unit, label, measure, value)
    for (body, code), (element, labels) in totals.items():
        if (body, code) in seen or (only is not None and code not in only):
            continue
        contest = contest_of(body, code, valkrets)
        b.unit(code, code, element.get("NAMN"))
        b.missing.append((body, code))
        if body == "K":
            _summary(b, body, contest, element, labels)
        for label, measure, value in _tally(b, body, contest, element, labels):
            b.fact(body, contest, code, label, measure, value)
    return b.done()


def _summary(b: Builder, body: str, contest: str, element: ElementTree.Element, labels: dict[str, str]) -> None:
    for label, measure, value in _tally(b, body, contest, element, labels):
        if value is not None and measure != CAST:
            b.summary.append((body, contest, label, measure, value))
    for child in [*element.findall("GILTIGA"), *element.findall("ÖVRIGA_GILTIGA/GILTIGA")]:
        seats = number(child.get("MANDAT"))
        if seats:
            code = child.get("PARTI") or ""
            b.seats.append((body, contest, clean(labels.get(code, code)), seats))


def _zip(url: str) -> Iterator[tuple[str, bytes]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, url).path)
    for name in archive.namelist():
        if name.endswith(".xml"):
            yield name, archive.read(name)


def _files(folder: str) -> Iterator[tuple[str, bytes]]:
    _path, bodies = XML_DIRS[folder]
    known = raw.keys(SOURCE)
    for body in bodies:
        for code in ["00", *kommuner(), *RENUMBERED]:
            url = xml_url(folder, code, body)
            if url in known:
                yield url, raw.cached(SOURCE, url).path.read_bytes()


def read_2010() -> Parsed:
    parsed = read_xml(_zip(ZIPS["2010"]), _skv_riksdag(NAMES_2010["R"]))
    names: dict[str, str] = {}
    for url in NAMES_2010.values():
        text = raw.cached(SOURCE, url).path.read_bytes().decode("latin-1")
        frame = pl.read_csv(
            io.StringIO(text), separator=";", infer_schema_length=0, quote_char=None, has_header=False, skip_rows=1
        )
        for row in frame.iter_rows():
            names.setdefault(f"{row[0]}{row[1]}{row[2]}", clean(row[5]))
    units = parsed.units.with_columns(name=pl.coalesce("name", pl.col("unit").replace_strict(names, default=None)))
    return parsed._replace(units=units)


def _page(url: str) -> list[str]:
    text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", "replace")
    return html.unescape(re.sub(r"<[^>]+>", "", text)).splitlines()


def riksdag_2002() -> dict[str, str]:
    pairs: list[tuple[str, str]] = []
    for url in constituency_urls():
        text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", "replace")
        title = re.search(r"<title>\s*(.*?) - Valkrets", text, re.IGNORECASE | re.DOTALL)
        if title is None:
            raise ValueError(f"{url}: no constituency title")
        name = clean(html.unescape(title.group(1)))
        towns = set(re.findall(r'/val/val_02/slutresultat/\d{2}R/(\d{4})/\1\.html"', text, re.IGNORECASE))
        pairs += [(town, name) for town in towns]
    return riksdag(pairs)


LATE = "i vallokal ej raknade roster"
PARTIES_2002 = {
    "M": "Moderata samlingspartiet",
    "C": "Centerpartiet",
    "FP": "Folkpartiet liberalerna",
    "KD": "Kristdemokraterna",
    "S": "Arbetarepartiet-Socialdemokraterna",
    "V": "Vänsterpartiet",
    "MP": "Miljöpartiet de gröna",
}


def read_2002() -> Parsed:
    """Per-municipality text pages: one block per municipal, county, or Riksdag constituency, with a line per
    district and a line of votes not counted at the polling stations."""
    valkrets = riksdag_2002()
    b = Builder()
    known = raw.keys(SOURCE)
    for body in "RLK":
        for kommun in [*kommuner(), *RENUMBERED]:
            url = text_url(body, kommun)
            if url in known:
                labels = _labels_2002(page_url(body, kommun)) if page_url(body, kommun) in known else {}
                _text_page(b, body, kommun, contest_of(body, kommun, valkrets), _page(url), url, labels)
    return b.done()


def _columns(header: list[str], total: str) -> list[str]:
    """The party columns of a block, from its header lines and its right-aligned 'Summa' line."""
    split = re.split(r"\s{2,}", " ".join(line.strip() for line in header))
    numbers = [m for m in re.finditer(r"[\d,]+", total.split("Summa", 1)[1]) if "," not in m.group()]
    if len(split) - 1 == len(numbers):
        return split[:-1]
    if len(header) != 1:
        raise ValueError(f"unreadable header {header}")
    offset = total.index("Summa") + len("Summa")
    ends = [offset + m.end() for m in numbers]
    starts = [offset, *ends[:-1]]
    return [clean(header[0][a:b]) for a, b in zip(starts, ends, strict=True)]


def _labels_2002(url: str) -> dict[str, str]:
    """The party table ('Partier') of a 2002 municipality page: abbreviation and name."""
    text = raw.cached(SOURCE, url).path.read_bytes().decode("utf-8", "replace")
    found = re.search(r">Partier</TH>(.*?)</TABLE>", text, re.IGNORECASE | re.DOTALL)
    if found is None:
        return {}
    rows = re.findall(
        r"<TR><TD[^>]*>(.*?)</TD>\s*<TD[^>]*>.*?</TD>\s*<TD[^>]*>(.*?)</TD></TR>",
        found.group(1),
        re.IGNORECASE | re.DOTALL,
    )
    return {clean(html.unescape(a)): clean(html.unescape(n)) for a, n in rows}


def _text_page(
    b: Builder, body: str, kommun: str, contest: str, lines: list[str], url: str, labels: dict[str, str]
) -> None:
    header: list[str] = []
    pending: list[str] = []
    columns: list[str] = []
    late = 0
    names: set[str] = set()
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        if line.startswith(" " * 10) and not any(re.fullmatch(r"[\d,+-]+", t) for t in tokens):
            pending.append(line)
            if tokens[-1] == "Vdt%":
                header, pending = pending, []
            continue
        if " Summa " in line and not line.startswith(" "):
            columns = _columns(header, line)
            continue
        if not columns or not line.startswith("  "):
            continue
        if "," in tokens[-1]:
            tokens = tokens[:-1]
        n = len(columns)
        if len(tokens) <= n or not all(t.isdigit() for t in tokens[-n:]):
            continue
        name = clean(" ".join(tokens[:-n]))
        if "distrikt" in name or name.startswith(("%", "+-", "M(U)")):
            continue
        if fold(name) == LATE:
            late += 1
            unit = f"{kommun}-{body}{late:02d}"
            b.unit(unit, kommun, None)
        else:
            if name in names:
                raise ValueError(f"{url}: district {name} twice")
            names.add(name)
            unit = f"{kommun}:{name}"
            b.unit(unit, kommun, name)
        for column, value in zip(columns, (int(t) for t in tokens[-n:]), strict=True):
            if column == "OG":
                b.fact(body, contest, unit, None, INVALID, value)
            elif column in ("ÖVR", "OVR"):
                b.fact(body, contest, unit, b.option(body, contest, "Övriga partier", "others", "ÖVR"), VOTES, value)
            else:
                label = labels.get(column) or PARTIES_2002.get(column, column)
                b.fact(body, contest, unit, b.option(body, contest, label, abbr=column), VOTES, value)


def read_wide(url: str, sheet: str, body: str, labels: dict[str, str]) -> Parsed:
    """The 2019 European Parliament district sheet: one column per party abbreviation."""
    frame = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=sheet, infer_schema_length=0)
    specials = {"ÖVR", "OGEJ", "BLANK", "OG", "RÖSTER GILTIGA", "RÖSTANDE", "RÖSTBERÄTTIGADE", "VALDELTAGANDE"}
    names = frame.columns.index("VALDISTRIKTSNAMN")
    parties = [c for c in frame.columns[names + 1 :] if c not in specials]
    b = Builder()
    contest = "00"
    for row in frame.iter_rows(named=True):
        kommun = f"{int(row['LÄNSKOD']):02d}{int(row['KOMMUNKOD']):02d}"
        district = int(row["VALDISTRIKTSKOD"])
        code = f"{kommun}{district:04d}" if district else f"{kommun}00"
        b.unit(code, kommun, row["VALDISTRIKTSNAMN"])
        for abbr in parties:
            value = number(row[abbr])
            if value is not None:
                b.fact(body, contest, code, b.option(body, contest, labels.get(abbr, abbr), abbr=abbr), VOTES, value)
        value = number(row["ÖVR"])
        if value is not None:
            b.fact(body, contest, code, b.option(body, contest, "Övriga partier", "others", "ÖVR"), VOTES, value)
        b.fact(body, contest, code, None, BLANK, number(row["BLANK"]))
        invalid = [v for v in (number(row["OG"]), number(row["OGEJ"])) if v is not None]
        if invalid:
            b.fact(body, contest, code, None, INVALID, sum(invalid))
        b.fact(body, contest, code, None, VALID, number(row["RÖSTER GILTIGA"]))
        b.fact(body, contest, code, None, CAST, number(row["RÖSTANDE"]))
        if number(row["RÖSTBERÄTTIGADE"]):
            b.fact(body, contest, code, None, REGISTERED, number(row["RÖSTBERÄTTIGADE"]))
    return b.done()


def read_ep2019() -> Parsed:
    frame = _skv(PARTIES_EP_2019)
    labels = {
        abbr: clean(label)
        for body, abbr, label in frame.select("VALTYP", "PARTIFÖRKORTNING", "PARTIBETECKNING").iter_rows()
        if body == "E" and abbr
    }
    return read_wide(EP_2019, "antal", "E", labels)


def read_ep2024() -> Parsed:
    frame = pl.read_excel(raw.cached(SOURCE, EP_2024).path, sheet_name="rosterEU", infer_schema_length=0)
    frame = frame.rename({c: c.strip() for c in frame.columns})
    frame = frame.with_columns(pl.col(c).str.strip_chars() for c in frame.columns)
    b = Builder()
    _long_rows(b, frame, "E", {}, "2024 E")
    return b.done()


# Elections.


class Day(NamedTuple):
    key: str
    day: date
    bodies: str
    reader: Callable[[], Parsed]
    rerun_of: date | None = None
    rerun_in: str | None = None


def _omval(name: str, kommun: str) -> Callable[[], Parsed]:
    return lambda: read_xml(_zip(ZIPS[name]), None, {kommun})


DAYS = [
    Day("2002-09-15", date(2002, 9, 15), "RLK", read_2002),
    Day("2003-09-14", date(2003, 9, 14), "F", lambda: read_xml(_files("emu2003"))),
    Day("2004-06-13", date(2004, 6, 13), "E", lambda: read_xml(_files("ep2004"))),
    Day("2006-09-17", date(2006, 9, 17), "RLK", lambda: read_xml(_files("2006"))),
    Day("2009-06-07", date(2009, 6, 7), "E", lambda: read_xml(_zip(ZIPS["ep2009"]))),
    Day("2010-09-19", date(2010, 9, 19), "RLK", read_2010),
    Day("2014-05-25", date(2014, 5, 25), "E", lambda: read_xml(_zip(ZIPS["ep2014"]))),
    Day("2014-09-14", date(2014, 9, 14), "RLK", read_2014),
    Day("2015-05-10", date(2015, 5, 10), "K", _omval("omval2015", "1278"), date(2014, 9, 14), "1278"),
    Day("2018-09-09", date(2018, 9, 9), "RLK", read_2018),
    Day("2019-04-07", date(2019, 4, 7), "K", _omval("omval2019", "2080"), date(2018, 9, 9), "2080"),
    Day("2019-05-26", date(2019, 5, 26), "E", read_ep2019),
    Day("2022-09-11", date(2022, 9, 11), "RLK", lambda: read_long(2022)),
    Day("2024-06-09", date(2024, 6, 9), "E", read_ep2024),
    Day("2026-09-13", date(2026, 9, 13), "RLK", lambda: read_long(2026)),
]
BY_KEY = {d.key: d for d in DAYS}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d.key}" for d in DAYS)]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for d in DAYS:
        parsed = d.reader()
        folder = PARSED / d.key
        folder.mkdir(exist_ok=True)
        for name, frame in parsed._asdict().items():
            frame.write_parquet(folder / f"{name}.parquet")
        print(f"{d.key}: {parsed.facts.height:,} values, {parsed.units.height:,} units", flush=True)
    geo().write()
    registry().write()
    for d in DAYS:
        election(d).write()


def emit() -> None:
    package.run("load", packages())


def parsed(d: Day) -> Parsed:
    folder = PARSED / d.key
    return Parsed(*(pl.read_parquet(folder / f"{name}.parquet") for name in Parsed._fields))


# Parties: the parliamentary parties keep one entity across their renamings; every other party is keyed by its
# printed name, within its county for a regional list and within its municipality for a municipal list.

CANONICAL = {
    "M": ("moderata samlingspartiet", "moderaterna"),
    "C": ("centerpartiet",),
    "L": ("folkpartiet liberalerna", "folkpartiet", "liberalerna", "liberalerna tidigare folkpartiet"),
    "KD": ("kristdemokraterna", "kristdemokratiska samhallspartiet"),
    "S": ("arbetarepartiet socialdemokraterna", "socialdemokraterna"),
    "V": ("vansterpartiet",),
    "MP": ("miljopartiet de grona", "miljopartiet"),
    "SD": ("sverigedemokraterna",),
    "FI": ("feministiskt initiativ",),
    "PP": ("piratpartiet",),
    "JL": ("junilistan",),
}
ALIASES = {name: abbr for abbr, names in CANONICAL.items() for name in names}
COLORS = {
    "M": "#52BDEC",
    "C": "#009933",
    "L": "#006AB3",
    "KD": "#000077",
    "S": "#E8112D",
    "V": "#DA291C",
    "MP": "#83CF39",
    "SD": "#DDDD00",
    "FI": "#CD1B68",
    "PP": "#572B85",
}


def party_key(body: str, contest: str, label: str) -> str:
    name = fold(label)
    if name in ALIASES:
        return ALIASES[name]
    scope = "national" if body in "REF" else "county" if body == "L" else "municipality"
    return f"{scope}:{'' if body in 'REF' else contest}:{name}"


def party(key_: str) -> str:
    return key("party", key_)


# Places and bodies.

COUNTY = "se.lan"
MUNICIPALITY = "se.kommun"
CONSTITUENCY = "se.riksdagsvalkrets"
DISTRICT = "se.valdistrikt"
VAL_SOURCE = key("source", SOURCE)
RIKSDAG = key("body", "riksdag")
LICENSE = (
    "Public records of the Swedish Election Authority (Valmyndigheten), published as open data for free reuse with "
    "the source acknowledged"
)


def _source(p: Package) -> None:
    p.add(
        "source",
        id=VAL_SOURCE,
        kind="official_body",
        publisher="Valmyndigheten",
        title="Valresultat och rådata från val 2002–2026",
        url=f"{SITE}/valresultat-och-statistik/statistik-och-data.html",
        license=LICENSE,
        license_url=f"{SITE}/valresultat-och-statistik/statistik-och-data/om-var-oppna-data",
        attribution="Valmyndigheten",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Sweden's 21 counties (län), 290 municipalities (kommuner), and 29 Riksdag constituencies as Valmyndigheten codes them",
    )
    p.external |= {SWEDEN, COUNTRY}
    _source(p)
    p.uses(raw.cached(SOURCE, SHEETS_2018["R"]))
    p.uses(raw.cached(SOURCE, xlsx_url(LONG[2026]["R"])))
    for code, rank, kind, noun, system in (
        (COUNTY, 1, "administrative", "county", "lan"),
        (MUNICIPALITY, 2, "administrative", "municipality", "kommun"),
        (CONSTITUENCY, 1, "electoral_district", "Riksdag constituency", None),
        (DISTRICT, 3, "precinct", "election district", None),
    ):
        p.add("scheme", code=code, polity_id=SWEDEN, kind=kind, rank=rank, noun=noun, code_system=system)

    def place(scheme: str, code: str, text: str, parent: str) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, text, "sv", "official")

    counties, towns = places()
    for code, text in sorted(counties.items()):
        place(COUNTY, code, text, COUNTRY)
    for code, text in sorted(towns.items()):
        place(MUNICIPALITY, code, text, area(COUNTY, code[:2]))
    for text, code in sorted(constituencies().items(), key=lambda item: item[1]):
        place(CONSTITUENCY, code, text, COUNTRY)
    return p


def region_body(county: str) -> str:
    return key("body", "region", county)


def council(kommun: str) -> str:
    return key("body", "kommun", kommun)


def system(name: str) -> str:
    return key("electoral_system", name)


SYSTEMS: dict[str, tuple[date, date | None, dict[str, object], str]] = {
    "riksdag-1998": (
        date(1998, 1, 1),
        date(2018, 1, 1),
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.4,
            "threshold": {"percent": 4, "level": "party", "scope": "national"},
        },
        (
            "The Riksdag's 349 seats: 310 fixed constituency seats are allocated within each of the 29 constituencies by "
            "the modified Sainte-Laguë method (first divisor 1.4), and 39 adjustment seats bring each party's national "
            "total to its proportional share. Parties need 4% of the national vote, or 12% in a constituency for that "
            "constituency's seats."
        ),
    ),
    "riksdag-2018": (
        date(2018, 1, 1),
        None,
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.2,
            "threshold": {"percent": 4, "level": "party", "scope": "national"},
        },
        (
            "The Riksdag's 349 seats: 310 fixed constituency seats are allocated within each of the 29 constituencies by "
            "the modified Sainte-Laguë method, with a first divisor of 1.2 since 2018, and 39 adjustment seats bring each "
            "party's national total to its proportional share. Parties need 4% of the national vote, or 12% in a "
            "constituency for that constituency's seats."
        ),
    ),
    "region-1998": (
        date(1998, 1, 1),
        date(2018, 1, 1),
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "Each county council (landsting, region since 2019) is elected by the modified Sainte-Laguë method (first "
            "divisor 1.4) across its constituencies, with adjustment seats for proportionality across the county."
        ),
    ),
    "region-2018": (
        date(2018, 1, 1),
        None,
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.2,
            "threshold": {"percent": 3, "level": "party", "scope": "contest"},
        },
        (
            "Each regional council is elected by the modified Sainte-Laguë method (first divisor 1.2 since 2018) across "
            "its constituencies, with adjustment seats; parties need 3% of the county's votes."
        ),
    ),
    "kommun-1998": (
        date(1998, 1, 1),
        date(2018, 1, 1),
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "Each municipal council is elected by the modified Sainte-Laguë method (first divisor 1.4); a municipality "
            "with several constituencies adds adjustment seats for proportionality across the municipality."
        ),
    ),
    "kommun-2018": (
        date(2018, 1, 1),
        None,
        {"formula": "modified_sainte_lague", "firstDivisor": 1.2},
        (
            "Each municipal council is elected by the modified Sainte-Laguë method (first divisor 1.2 since 2018); a "
            "municipality with several constituencies adds adjustment seats, and there parties need 2% of the "
            "municipality's votes."
        ),
    ),
    "ep-1995": (
        date(1995, 1, 1),
        date(2018, 1, 1),
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.4,
            "threshold": {"percent": 4, "level": "party", "scope": "contest"},
        },
        (
            "Sweden's members of the European Parliament are elected in one national constituency by the modified "
            "Sainte-Laguë method (first divisor 1.4) among parties with at least 4% of the votes."
        ),
    ),
    "ep-2018": (
        date(2018, 1, 1),
        None,
        {
            "formula": "modified_sainte_lague",
            "firstDivisor": 1.2,
            "threshold": {"percent": 4, "level": "party", "scope": "contest"},
        },
        (
            "Sweden's members of the European Parliament are elected in one national constituency by the modified "
            "Sainte-Laguë method (first divisor 1.2 since 2018) among parties with at least 4% of the votes."
        ),
    ),
    "referendum": (
        date(1980, 1, 1),
        None,
        {"formula": "referendum"},
        "A consultative national referendum: the answer with the most votes carries, without binding effect.",
    ),
}
FAMILY = {"R": "riksdag", "L": "region", "K": "kommun", "E": "ep", "F": "referendum"}


def system_for(body: str, day: date) -> str:
    family = FAMILY[body]
    for name, (since, until, _, _) in SYSTEMS.items():
        if name.split("-")[0] == family and since <= day and (until is None or day < until):
            return system(name)
    raise LookupError(f"no electoral system for {body} on {day}")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Swedish parties on Valmyndigheten's ballots from 2002 to 2026, the Riksdag, the county and regional councils, "
        "the municipal councils, and their electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {SWEDEN}
    p.add(
        "source",
        id=COLORS_SOURCE,
        kind="community",
        publisher="electionsarchive.com",
        title="Colors of Sweden's parliamentary parties, taken from their logos",
        url="https://electionsarchive.com",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="electionsarchive.com",
    )
    p.add("body", id=RIKSDAG, polity_id=SWEDEN, kind="unicameral")
    p.name(RIKSDAG, "Riksdag", "en", "official")
    p.name(RIKSDAG, "Sveriges riksdag", "sv", "native", False)
    counties, towns = places()
    for code, text in sorted(counties.items()):
        if code == "09":
            continue
        p.add("body", id=region_body(code), polity_id=SWEDEN, kind="council")
        p.name(region_body(code), f"Regionfullmäktige, {text}", "sv", "official")
    for code, text in sorted(towns.items()):
        p.add("body", id=council(code), polity_id=SWEDEN, kind="council")
        p.name(council(code), f"Kommunfullmäktige i {text}", "sv", "official")
    for name, (since, until, rules, method) in SYSTEMS.items():
        id = system(name)
        p.add(
            "electoral_system", id=id, polity_id=SWEDEN, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    labels: dict[str, list[tuple[date, str, str | None]]] = {}
    for d in DAYS:
        options = parsed(d).options.filter(pl.col("kind") == "list")
        for body, contest, label, _, abbr in options.iter_rows():
            labels.setdefault(party_key(body, contest, label), []).append((d.day, label, abbr))
    for key_, seen in sorted(labels.items()):
        id = party(key_)
        p.add("party", id=id, kind="party", polity_id=SWEDEN)
        p.add("external_id", entity_id=id, scheme="se.val.party", value=key_)
        latest = max(seen)[1]
        p.name(id, latest, "sv", "official")
        for text in sorted({text for _, text, _ in seen} - {latest}):
            p.name(id, text, "sv", "official", False)
        abbrs = {abbr for _, _, abbr in seen if abbr and not abbr.isdigit() and fold(abbr) != fold(latest)}
        if key_ in CANONICAL:
            abbrs = {key_}
        for abbr in sorted(abbrs)[:1]:
            p.name(id, abbr, "sv", "abbreviation", False)
        if key_ in COLORS:
            p.add("party_color", party_id=id, color=COLORS[key_], source_id=COLORS_SOURCE)
    return p


COLORS_SOURCE = key("source", "se.val.party_colors")


ELECTIONS = {
    "R": ("general", "{year} Swedish general election", "Riksdagsvalet {year}", "general"),
    "L": ("local", "{year} Swedish regional elections", "Regionvalen {year}", "regional"),
    "K": ("local", "{year} Swedish municipal elections", "Kommunvalen {year}", "municipal"),
    "E": (
        "general",
        "{year} European Parliament election in Sweden",
        "Europaparlamentsvalet {year}",
        "european-parliament-districts",
    ),
    "F": ("referendum", "{year} Swedish euro referendum", "Folkomröstningen om euron {year}", "referendum"),
}
METHOD = (
    "Counted by Valmyndigheten and the county administrative boards per election district (valdistrikt), from the "
    "authority's final count. Valid votes are the votes for parties, including votes for unlisted parties where the "
    "source counts them as valid; blank ballots and other invalid ballots are stored apart, and ballots cast are all "
    "ballots including blank and invalid ones. Votes that were not counted at the polling stations, mostly early "
    "votes that arrived late, are counted by the municipality on the Wednesday after election day and reported "
    "as collection districts (uppsamlingsdistrikt or onsdagsdistrikt), stored as aggregate units without a roll."
)
NOTES = {
    "2002-09-15": " The 2002 results come from the authority's per-municipality text tables, which give each "
    "district's party votes and its invalid ballots (blank and other invalid ballots together) but not its roll or "
    "ballots cast. Districts are identified by their printed names. Parties outside the parliament are summed as "
    "'other parties' (övriga partier) wherever the tables print them so.",
    "2003-09-14": " Blank ballots (Avstår) were a valid answer in the 2003 referendum. Knivsta has no district file; "
    "its municipality total is stored as one aggregate unit.",
    "2004-06-13": " Knivsta has no district file; its municipality total is stored as one aggregate unit.",
    "2014-09-14": " Parties without seats in the Riksdag are summed as 'other parties' (övriga partier) in the "
    "Riksdag district file.",
    "2019-04-07": " The authority publishes this re-run's districts only as web pages; the municipality total from "
    "its final count is stored as one aggregate unit.",
    "2024-06-09": " The 2024 file gives each district's party votes, blank and invalid ballots, and roll, but no "
    "separate total of valid votes or ballots cast.",
}


def riksdag_seats(year: int) -> dict[tuple[str, str], int]:
    """Seats won per (constituency code, party key) in the Riksdag, from the authority's national result files:
    the 2026 file gives 2026 and 2022, the 2022 file's side table gives 2018."""
    codes = {fold(name): code for name, code in constituencies().items()}
    found: dict[tuple[str, str], int] = {}
    if year in (2022, 2026):
        frame = pl.read_excel(
            raw.cached(SOURCE, xlsx_url(NATIONAL[2026])).path, sheet_name="Per valkrets", infer_schema_length=0
        )
        for name, label, seats in frame.select("Riksdagsvalkrets", "Parti/kategori", f"Mandat {year}").iter_rows():
            if number(seats):
                found[(codes[fold(name)], party_key("R", "", label))] = number(seats) or 0
    elif year == 2018:
        frame = pl.read_excel(
            raw.cached(SOURCE, xlsx_url(NATIONAL[2022])).path,
            sheet_name="Valkrets",
            has_header=False,
            infer_schema_length=0,
        )
        for row in frame.slice(1).iter_rows():
            if row[0] and row[10] and fold(row[10]) != "summa" and number(row[13]):
                found[(codes[fold(row[0])], party_key("R", "", row[10]))] = number(row[13]) or 0
    return found


def _name(body: str, contest: str, counties: dict[str, str], towns: dict[str, str], ridings: dict[str, str]) -> str:
    if body == "R":
        return ridings[contest]
    if body == "L":
        return counties[contest]
    if body == "K":
        return towns[contest]
    return "Europaparlamentet" if body == "E" else "Införande av euron"


def election(d: Day) -> Package:
    data = parsed(d)
    original = None if d.rerun_of is None else str(d.rerun_of)
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([] if original is None else [f"{SOURCE}/{original}"])
    reruns = [r for r in DAYS if r.rerun_of == d.day]
    p = Package(
        f"{SOURCE}/{d.key}",
        SOURCE,
        f"Sweden's {'re-run ' if original else ''}elections of {d.day:%-d %B %Y} by election district from "
        "Valmyndigheten's final count",
        depends,
    )
    p.external |= {SWEDEN, COUNTRY}
    for url in _artifacts(d):
        p.uses(raw.cached(SOURCE, url))
    counties, towns = places()
    ridings = {code: name for name, code in constituencies().items()}
    year = d.day.year
    elections: dict[str, str] = {}
    for body in d.bodies:
        kind, english, swedish, word = ELECTIONS[body]
        id = elections[body] = key("election", d.day, body)
        slug = f"se/{d.day}-{word}"
        if original is not None:
            town = towns[d.rerun_in or ""]
            english, swedish = (
                f"{year} {town} municipal election re-run",
                f"Omval till kommunfullmäktige i {town} {year}",
            )
            slug = f"se/{d.day}-municipal-rerun"
        if body == "L" and year < 2019:
            swedish = "Landstingsvalen {year}"
        p.add(
            "election",
            id=id,
            polity_id=SWEDEN,
            kind=kind,
            date_from=d.day,
            date_to=d.day,
            date_precision="day",
            status="held",
            rerun_of_id=None if original is None else key("election", original, body),
        )
        p.name(id, english.format(year=year), "en", "official")
        p.name(id, swedish.format(year=year), "sv", "native", False)
        p.add("slug", path=slug, entity_id=id, is_current=True)
    units = data.units.with_columns(
        aggregate=~pl.col("unit").str.contains(r"^\d{8}$")
        | pl.col("name").fill_null("").str.to_lowercase().str.contains("uppsamling|ej räknade"),
    )
    unit_ids = {code: key("unit", d.day, code) for code in units["unit"]}
    p.frame(
        "reporting_unit",
        units.select(
            id=pl.col("unit").replace_strict(unit_ids),
            polity_id=pl.lit(SWEDEN),
            scheme_code=pl.lit(DISTRICT),
            code=pl.concat_str(pl.lit(f"{d.day}:"), "unit"),
            area_relation=pl.lit("within"),
            area_id=pl.col("kommun").map_elements(
                lambda k: area(MUNICIPALITY, RENUMBERED.get(k, k)), return_dtype=pl.String
            ),
            kind=pl.when("aggregate").then(pl.lit("aggregate")).otherwise(pl.lit("standard")),
        ),
    )
    for code, name in units.filter(pl.col("name").is_not_null()).select("unit", "name").iter_rows():
        p.name(unit_ids[code], name, "sv", "official")
    seats = {(b, c, label): n for b, c, label, n in data.seats.iter_rows()}
    if "R" in d.bodies and not data.seats.filter(pl.col("body") == "R").height:
        national = riksdag_seats(year)
        for body, contest, label, kind, _ in data.options.filter(pl.col("body") == "R").iter_rows():
            n = national.pop((contest, party_key(body, contest, label)), 0) if kind == "list" else 0
            if n:
                seats[(body, contest, label)] = n
        if national:
            raise ValueError(f"{d.key}: Riksdag seats without an option: {national}")
    contests = data.facts.select("body", "contest").unique().sort("body", "contest")
    sets: dict[tuple[str, str], str] = {}
    words = [
        (body, changeset.slug(_name(body, contest, counties, towns, ridings))) for body, contest in contests.iter_rows()
    ]
    for body, contest in contests.iter_rows():
        name = _name(body, contest, counties, towns, ridings)
        word = changeset.slug(name)
        if words.count((body, word)) > 1:
            word = f"{word}-{contest}"
        contest_id = key("contest", d.day, body, contest)
        won = {label: n for (b, c, label), n in seats.items() if b == body and c == contest}
        p.add(
            "contest",
            id=contest_id,
            election_id=elections[body],
            kind="ballot_question" if body == "F" else "list_allocation",
            body_id=RIKSDAG
            if body == "R"
            else region_body(contest)
            if body == "L"
            else council(contest)
            if body == "K"
            else None,
            district_area_id=area(CONSTITUENCY, contest)
            if body == "R"
            else area(COUNTY, contest)
            if body == "L"
            else area(MUNICIPALITY, contest)
            if body == "K"
            else COUNTRY,
            magnitude=sum(won.values()) or None,
            round_number=1,
            electoral_system_id=system_for(body, d.day),
            default_result_kind="counted",
        )
        if body == "F":
            p.add("ballot_question", contest_id=contest_id, type="referendum")
        p.name(contest_id, name, "sv", "official")
        slug = f"se/{d.day}-{ELECTIONS[body][3]}" if original is None else f"se/{d.day}-municipal-rerun"
        p.add("slug", path=f"{slug}/{word}", entity_id=contest_id, is_current=True)
        result_set = sets[(body, contest)] = key("result_set", d.day, body, contest)
        annulled = any(r.rerun_in == contest and body == "K" for r in reruns)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[body],
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=VAL_SOURCE,
            status="annulled" if annulled else "active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD + NOTES.get(d.key, ""),
            lang="en",
        )
        if annulled:
            p.add(
                "note",
                id=key("note", result_set, "annulled"),
                entity_id=result_set,
                kind="caveat",
                body_md="The Election Review Board (Valprövningsnämnden) annulled this municipal election, and it was "
                "held again; the re-run's result replaced it.",
                lang="en",
            )
    options = data.options.with_columns(
        id=pl.struct("body", "contest", "label").map_elements(
            lambda r: key("option", d.day, r["body"], r["contest"], fold(r["label"]) or r["label"]),
            return_dtype=pl.String,
        )
    )
    if options["id"].is_duplicated().any():
        raise ValueError(
            f"{d.key}: two options fold to one key: {options.filter(pl.col('id').is_duplicated()).head(4)}"
        )
    option_ids = {(b, c, label): id for b, c, label, id in options.select("body", "contest", "label", "id").iter_rows()}
    for position, (body, contest, label, kind, _, id) in enumerate(options.iter_rows(), start=1):
        p.add(
            "contest_option",
            id=id,
            contest_id=key("contest", d.day, body, contest),
            kind=kind,
            party_id=party(party_key(body, contest, label)) if kind == "list" else None,
            ballot_position=None,
            ballot_label=label,
        )
        n = seats.get((body, contest, label))
        if n:
            p.add("outcome", result_set_id=sets[(body, contest)], option_id=id, kind="won", seats=n)
    for body, contest, label, measure, value in data.summary.iter_rows():
        p.add(
            "declared",
            result_set_id=sets[(body, contest)],
            option_id=None if label is None else option_ids[(body, contest, label)],
            measure=measure,
            value=value,
        )
    set_ids = {f"{b}\x1f{c}": s for (b, c), s in sets.items()}
    option_map = {f"{b}\x1f{c}\x1f{label}": id for (b, c, label), id in option_ids.items()}
    facts = data.facts.with_columns(
        result_set_id=pl.concat_str("body", "contest", separator="\x1f").replace_strict(set_ids),
        unit_id=pl.col("unit").replace_strict(unit_ids),
        option_id=pl.concat_str("body", "contest", "label", separator="\x1f").replace_strict(option_map, default=None),
    )
    if facts.filter(pl.col("label").is_not_null() & pl.col("option_id").is_null()).height:
        raise ValueError(f"{d.key}: values without an option")
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            "measure",
            "value",
            channel=pl.lit(0),
            count_round=pl.lit(0),
            flags=pl.lit(0),
        ),
    )
    return p


def _artifacts(d: Day) -> list[str]:
    known = raw.keys(SOURCE)
    if d.key == "2002-09-15":
        return [u for u in known if f"/{TEXT_2002}/" in u]
    if d.key == "2003-09-14":
        return [u for u in known if "/emu2003/" in u]
    if d.key == "2004-06-13":
        return [u for u in known if "/ep2004/" in u]
    if d.key == "2006-09-17":
        return [u for u in known if "/val2006/" in u]
    return {
        "2009-06-07": [ZIPS["ep2009"]],
        "2010-09-19": [ZIPS["2010"], *NAMES_2010.values()],
        "2014-05-25": [ZIPS["ep2014"]],
        "2014-09-14": [*SKV_2014.values(), PARTIES_2014],
        "2015-05-10": [ZIPS["omval2015"]],
        "2019-04-07": [ZIPS["omval2019"]],
        "2019-05-26": [EP_2019, PARTIES_EP_2019],
        "2018-09-09": [*SHEETS_2018.values(), PARTIES_2018, xlsx_url(NATIONAL[2022])],
        "2022-09-11": [*(xlsx_url(u) for u in LONG[2022].values()), xlsx_url(NATIONAL[2026])],
        "2024-06-09": [EP_2024],
        "2026-09-13": [*(xlsx_url(u) for u in LONG[2026].values()), xlsx_url(NATIONAL[2026])],
    }[d.key]


# National totals against the authority's own tables.


def _mine(d: Day, body: str) -> tuple[dict[str, int], dict[str, int]]:
    """This election's district sums: by measure name, and by party (folded label and abbreviation)."""
    data = parsed(d)
    facts = data.facts.filter(pl.col("body") == body)
    measures = {
        name: int(facts.filter(pl.col("measure") == m)["value"].sum())
        for name, m in (("valid", VOTES), ("cast", CAST), ("blank", BLANK), ("invalid", INVALID))
        if facts.filter(pl.col("measure") == m).height
    }
    votes = (
        facts.filter(pl.col("measure") == VOTES)
        .join(data.options.filter(pl.col("body") == body), on=["body", "contest", "label"])
        .group_by("label", "abbr")
        .agg(pl.col("value").sum())
    )
    parties: dict[str, int] = {}
    for label, abbr, value in votes.iter_rows():
        for name in {fold(label), fold(abbr or "")} - {""}:
            parties[name] = parties.get(name, 0) + int(value)
    return measures, parties


def _published(d: Day, body: str) -> tuple[str, dict[str, int], dict[str, int]]:
    """The authority's own national figures for one election: (where from, measures, party votes)."""
    xml = {"2003-09-14": "emu2003", "2004-06-13": "ep2004", "2006-09-17": "2006"}
    zips = {"2009-06-07": "ep2009", "2010-09-19": "2010", "2014-05-25": "ep2014"}
    if d.key in xml or d.key in zips:
        if d.key in xml:
            url = xml_url(xml[d.key], "00", body)
            data = raw.cached(SOURCE, url).path.read_bytes()
        else:
            url = ZIPS[zips[d.key]]
            data = zipfile.ZipFile(raw.cached(SOURCE, url).path).read(
                next(
                    n for n in zipfile.ZipFile(raw.cached(SOURCE, url).path).namelist() if n.endswith(f"_00{body}.xml")
                )
            )
        root = _root(data)
        labels = {p.get("FÖRKORTNING") or "": clean(p.get("BETECKNING")) for p in root.iter("PARTI")}
        nation = root.find("NATION")
        if nation is None:
            raise LookupError(f"{url}: no national total")
        turnout = nation.find("VALDELTAGANDE")
        measures = {"valid": number(nation.get("RÖSTER")) or 0}
        if turnout is not None:
            measures["cast"] = number(turnout.get("SUMMA_RÖSTER")) or 0
        parties: dict[str, int] = {}
        for element in [
            *nation.findall("GILTIGA"),
            *nation.findall("ÖVRIGA_GILTIGA/GILTIGA"),
            *nation.findall("GILTIGA/VARAV_ÖVRIGA"),
        ]:
            if element.find("VARAV_ÖVRIGA") is not None:
                continue
            parties[fold(labels.get(element.get("PARTI") or "", ""))] = number(element.get("RÖSTER")) or 0
        return url, measures, parties
    files = TOTALS.get(d.key, {})
    if body in files:
        url = files[body]
        if d.key == "2002-09-15":
            lines = _page(url)
            header = [line for line in lines if line.strip().endswith("Vdt%")][:1]
            total = next(line for line in lines if " Summa " in line)
            columns = _columns(header, total)
            values = [int(t) for t in total.split("Summa", 1)[1].split()[: len(columns)]]
            row = dict(zip(columns, values, strict=True))
            invalid = row.pop("OG")
            parties = {fold(PARTIES_2002.get(k, k)): v for k, v in row.items()}
            return url, {"valid": sum(row.values()), "invalid": invalid}, parties
        if url.endswith(".skv"):
            frame = _skv(url)
            sums = {
                c: int(frame[c].cast(pl.Int64, strict=False).sum())
                for c in frame.columns
                if c.endswith(" tal") or c.startswith("Rost")
            }
            parties = {
                fold(c.removesuffix(" tal")): v
                for c, v in sums.items()
                if c.endswith(" tal") and c[:-4] not in ("BL", "BLANK", "OG", "OVR", "ÖVR")
            }
            return url, {"valid": sums["Rost Giltiga"], "cast": sums["Rostande"]}, parties
        sheet = "antal" if body == "E" else f"{body} antal"
        frame = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=sheet, infer_schema_length=0)
        sums = {
            c: int(frame[c].cast(pl.Int64, strict=False).sum())
            for c in frame.columns
            if not c.endswith(("KOD", "NAMN"))
        }
        skip = {"ÖVR", "OGEJ", "BLANK", "OG", "RÖSTER GILTIGA", "RÖSTANDE", "RÖSTBERÄTTIGADE", "VALDELTAGANDE"}
        parties = {fold(c): v for c, v in sums.items() if c not in skip}
        return url, {"valid": sums["RÖSTER GILTIGA"], "cast": sums["RÖSTANDE"]}, parties
    year = d.day.year
    if body == "R" and year in NATIONAL:
        url = xlsx_url(NATIONAL[year])
        frame = pl.read_excel(
            raw.cached(SOURCE, url).path,
            sheet_name="Riket" if year == 2022 else "Hela riket",
            has_header=False,
            infer_schema_length=0,
        )
        rows = {fold(a or ""): number(b) for a, b in frame.select(frame.columns[:2]).iter_rows()}
        parties = {
            k: v
            for k, v in rows.items()
            if v is not None
            and k not in ("giltiga roster", "valdeltagande", "rostberattigade", "ovriga anmalda partier")
            and not k.startswith("ogiltiga")
        }
        return url, {"valid": rows["giltiga roster"] or 0, "cast": rows["valdeltagande"] or 0}, parties
    if year in LONG or body == "E":
        url = EP_2024 if body == "E" else xlsx_url(LONG[year][body])
        if year == 2026:
            sheet = {"L": "Antal_region", "K": "Antal_kommun"}[body]
            frame = pl.read_excel(raw.cached(SOURCE, url).path, sheet_name=sheet, infer_schema_length=0)
            sums = {
                fold(c): int(frame[c].cast(pl.Int64, strict=False).sum())
                for c in frame.columns
                if frame[c].cast(pl.Int64, strict=False).null_count() < frame.height
            }
        else:
            frame = pl.read_excel(
                raw.cached(SOURCE, url).path, sheet_name="PivotAntal", has_header=False, infer_schema_length=0
            )
            header = [clean(c) for c in frame.row(1)]
            total = next(r for r in frame.iter_rows() if fold(r[0] or "") == "totalsumma")
            sums = {
                fold(h): number(v) or 0
                for h, v in zip(header, total, strict=True)
                if h and fold(h) not in ("radetiketter", "totalsumma")
            }
        measures = {k: sums.pop(k) for k in ("summa giltiga roster", "valdeltagande") if k in sums}
        for k in [
            k
            for k in sums
            if "ogiltig" in k
            or "blank" in k
            or k in ("rostberattigade", "ej anmalt deltagande", "kommunkod", "lanskod", "valkretskod", "regionkod")
        ]:
            sums.pop(k)
        renamed = {"summa giltiga roster": "valid", "valdeltagande": "cast"}
        return url, {renamed[k]: v for k, v in measures.items()}, sums
    return "", {}, {}


def compare() -> dict[str, dict[str, object]]:
    report: dict[str, dict[str, object]] = {}
    for d in DAYS:
        for body in d.bodies:
            url, published, parties = _published(d, body)
            measures, mine = _mine(d, body)
            if not url:
                report[f"{d.key} {body}"] = {"source": "declared contest totals", "measures": measures}
                continue
            differs = {k: (v, measures.get(k)) for k, v in published.items() if measures.get(k) != v}
            party_diffs = {k: (v, mine.get(k)) for k, v in parties.items() if v and mine.get(k) != v}
            report[f"{d.key} {body}"] = {
                "source": url,
                "measures": {k: (v, measures.get(k)) for k, v in published.items()},
                "differs": differs,
                "parties_compared": len(parties),
                "party_differs": party_diffs,
            }
    return report
