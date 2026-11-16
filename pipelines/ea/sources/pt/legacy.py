"""Crawl and read the static results sites of 1999 to 2007 on eleicoes.mai.gov.pt.

These sites predate the JSON ones: each territory has an HTML page that prints its count, and its navigation lists the
codes of its children (district dd0000, municipality ddcc00, parish ddccff). Four layouts are in use; `Old.layout`
names the URL pattern of each. The crawl keeps every page byte for byte in one zip per election, like `crawl.store`.
"""

import html
import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ea import raw
from ea.sources.pt.crawl import HOST, WORKERS


@dataclass(frozen=True)
class Old:
    name: str
    layout: str  # "v1" (1999-2002), "v2" (2004-2005), "v3" (2006), "v4" (2007)
    types: tuple[str, ...]  # the page types: the site's organ code in each page's path
    country: str = "230000"


def bundle_url(site: Old) -> str:
    return f"{HOST}/{site.name}/#pages.zip"


def results_path(site: Old, code: str, kind: str) -> str:
    dd, cc, ff = code[:2], code[2:4], code[4:6]
    if site.layout == "v1":
        if cc == "00":
            return f"D{dd}0000/{dd}0000_{kind}.html"
        if ff == "00":
            return f"D{dd}0000/C{dd}{cc}00/{dd}{cc}00_{kind}.html"
        return f"D{dd}0000/C{dd}{cc}00/F{code}/{code}_{kind}.html"
    if site.layout == "v2":
        return f"{kind}/D{dd}/Results{code}.html"
    if site.layout == "v3":
        return f"html/{kind}/D{dd}/Results{code}.html"
    return f"R{code}.html"


def nav_path(site: Old, code: str, kind: str) -> str:
    """The page that lists a territory's children."""
    dd, cc = code[:2], code[2:4]
    if site.layout == "v2":
        return f"{kind}/D{dd}/index.html" if cc == "00" else f"{kind}/D{dd}/C{cc}.html"
    if site.layout == "v3":
        return f"html/{kind}D{dd}.html" if cc == "00" else f"html/{kind}D{dd}C{cc}.html"
    if site.layout == "v4" and code != site.country:
        return f"C{code}.html"
    return results_path(site, code, kind)


# A territory code in a page: an option value, or the argument of an image-map or menu call.
CODE = re.compile(r"""(?:value\s*=\s*["']?|\(\s*["'])(\d{6})["'\s>,)]""", re.IGNORECASE)


def codes(page: bytes) -> set[str]:
    return set(CODE.findall(page.decode("latin-1")))


def children(code: str, found: set[str], country: str) -> list[str]:
    if code == country and country in ("0", "230000"):
        return sorted(c for c in found if c.endswith("0000") and c != country and c not in ("990000", "230000"))
    if code.endswith("0000"):
        # The Azores' municipalities keep the codes of its three former districts: 19, 20, and 21.
        prefixes = ("19", "20", "21") if code == "210000" else (code[:2],)
        return sorted(c for c in found if c[:2] in prefixes and c.endswith("00") and not c.endswith("0000"))
    if code.endswith("00"):
        return sorted(c for c in found if c[:4] == code[:4] and not c.endswith("00"))
    return []


def crawl(site: Old) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    base = f"{HOST}/{site.name}/"
    nav_kind = site.types[0]
    with (
        httpx.Client(headers={"User-Agent": raw.USER_AGENT}, timeout=60, follow_redirects=True) as client,
        ThreadPoolExecutor(WORKERS) as pool,
    ):

        def get(path: str) -> tuple[str, bytes | None]:
            for attempt in range(6):
                try:
                    response = client.get(base + path)
                except httpx.TransportError:
                    continue
                if response.status_code == 404:
                    return path, None
                if response.status_code >= 500 and attempt < 5:
                    continue
                response.raise_for_status()
                return path, response.content
            raise RuntimeError(f"{base}{path}: no answer")

        level = [site.country]
        while level:
            wanted = {results_path(site, code, kind) for code in level for kind in site.types}
            wanted |= {nav_path(site, code, nav_kind) for code in level if code == site.country or code.endswith("00")}
            found = dict(pool.map(get, sorted(wanted)))
            following: list[str] = []
            for code in level:
                if not code.endswith("00") and code != site.country:
                    continue
                listed: set[str] = set()
                for path in (nav_path(site, code, nav_kind), results_path(site, code, nav_kind)):
                    body = found.get(path)
                    if body is not None:
                        listed |= codes(body)
                kids = children(code, listed, site.country)
                following += kids
                if code.endswith("00") and not code.endswith("0000") and code != site.country:
                    # Some navigation pages leave out parishes whose pages exist; probe the gaps in the codes.
                    top = max((int(kid[4:]) for kid in kids), default=0)
                    following += [f"{code[:4]}{n:02d}" for n in range(1, top + 4) if f"{code[:4]}{n:02d}" not in kids]
            files.update({path: body for path, body in found.items() if body is not None})
            level = sorted(set(following))
    return files


def store(site: Old) -> raw.Artifact:
    try:
        return raw.cached("pt.mai", bundle_url(site))
    except LookupError:
        pass
    started = datetime.now(UTC)
    files = crawl(site)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[path])
    print(f"pt.mai: {site.name}: {len(files):,} pages", flush=True)
    return raw.store(
        "pt.mai", bundle_url(site), buffer.getvalue(), suffix=".zip", retrieved_at=started, media_type="application/zip"
    )


# ---------------------------------------------------------------------------------------------------------------------
# Reading one page


def text(page: bytes) -> str:
    body = page.decode("latin-1")
    if "Ã" in body and "�" not in body:
        try:
            body = page.decode("utf-8")
        except UnicodeDecodeError:
            pass
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    body = re.sub(r"<(script|style|select)\b.*?</\1>", " ", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    return " ".join(html.unescape(body).replace("\xa0", " ").split())


NUMBER = re.compile(r"^(?:-|\d+(?:[.,]\d+)?%?)$")


def figure(token: str) -> int | None:
    return None if token == "-" else int(token)


ACCOUNTING = (
    ("registered", r"Inscritos (\d+)"),
    ("cast", r"Votantes (\d+)"),
    ("blank", r"(?:Em )?Brancos? (\d+)"),
    ("invalid", r"Nulos (\d+)"),
)
AFTER = re.compile(
    r"(?:Inscritos|Votantes|Abstenção|(?:Em )?Brancos?|Nulos) \d+(?: [\d,.]+%?)?|Freguesias por apurar \d+"
    r"|Total de mandatos a atribuir(?: em \d{4})? \d+",
    re.IGNORECASE,
)


def _accounting(words: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, pattern in ACCOUNTING:
        found = re.search(pattern, words, re.IGNORECASE)
        if found:
            out[name] = int(found.group(1))
    return out


TABLE_START = re.compile(
    r"(?:Listas|Partidos|Candidatos?|Opções|Resultados \d{4} votos|\b\d{4} Votos %(?: Mandatos %?)?)"
    r"(?: Votos)?(?: %)?(?: Mand(?:atos|\.)?)?(?: %)?(?: Presid\.)?(?: Maiorias Absolutas)?(?: Concelhos a que "
    r"Concorreu)?"
)


def rows(words: str, layout: str) -> list[tuple[str, int | None, int | None]]:
    """The option rows of a page: label, votes, and seats when printed."""
    if layout == "v1":
        start = TABLE_START.search(words)
        end = words.find("Inscritos", start.end() if start else 0)
        segment = words[start.end() : end] if start else ""
    else:
        ends = [m.end() for m in AFTER.finditer(words)]
        rest = words[max(ends) :] if ends else ""
        header = re.search(r"Votos %(?: Mandatos)?(?: %)?", rest)
        if header:
            rest = rest[header.end() :]
        rest = re.sub(r"^\s*(?:Resultados \d{4} )?votos(?: mandatos)?", "", rest, flags=re.IGNORECASE)
        segment = re.split(r"STAPE|Desenvolvido|Developed|Percentagem|PERC\.", rest)[0]
    out: list[tuple[str, int | None, int | None]] = []
    label: list[str] = []
    numbers: list[str] = []
    for token in segment.split():
        if NUMBER.match(token):
            if label:
                numbers.append(token)
            continue
        if numbers:
            out.append(_row(label, numbers))
            label, numbers = [], []
        label.append(token)
    if label and numbers:
        out.append(_row(label, numbers))
    return out


def _row(label: list[str], numbers: list[str]) -> tuple[str, int | None, int | None]:
    votes = int(numbers[0]) if numbers[0].isdigit() else None
    seats = None
    if len(numbers) >= 3 and "%" not in numbers[2] and "," not in numbers[2] and "." not in numbers[2]:
        seats = figure(numbers[2]) or 0
    return " ".join(label), votes, seats


def read(page: bytes, layout: str) -> tuple[dict[str, int], list[tuple[str, int | None, int | None]], str]:
    words = text(page)
    return _accounting(words), rows(words, layout), words
