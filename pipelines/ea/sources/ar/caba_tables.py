"""The polling tables (mesas) of the City of Buenos Aires's 2015 and 2025 elections, as the two results services list
them: the Tribunal Superior de Justicia's service for 2015 and the Tribunal Electoral's for 2025."""

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote_plus

from ea import raw

SOURCE = "ar.caba"
TSJ = "https://www.eleccionesciudad.gob.ar"
TE = "https://resultados.electoralcaba.gob.ar"
COMUNAS = range(1, 16)
WORKERS = 24


@dataclass(frozen=True)
class Table:
    comuna: int | None  # the comuna the service files the table under, if its URLs say
    establishment: str  # the school or other building where it sat
    number: str  # the table's number
    url: str  # its results page


BUILDINGS = 3  # the election whose listing names the buildings for all three of 2015
TEST = "prueba"  # the name the service gives the fifteen test tables numbered 50001 to 50015
CAP = 15  # the service lists at most this many establishments for one search term
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .ÑÁÉÍÓÚ"


def tsj_establishments_url(page: int, comuna: int, term: str = "") -> str:
    return f"{TSJ}/resultados/establecimientos/{page}?comuna={comuna}" + (f"&term={quote(term)}" if term else "")


def tsj_tables_url(page: int, comuna: int, code: str) -> str:
    return f"{TSJ}/resultados/mesas/{page}?comuna={comuna}&codigoestablecimiento={code}"


def tsj_table_url(page: int, comuna: int, code: str, number: str) -> str:
    return f"{TSJ}/resultados/eleccionReloaded/{page}/{comuna}/{code}/{number}/0"


def _json(url: str) -> list[dict[str, str]]:
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8", errors="replace")
    return json.loads(text)


def _codes(url: str) -> list[tuple[str, str]]:
    return [(item["codigo"], item["label"]) for item in _json(url) if item.get("codigo")]


def _explore(comuna: int, *, fetch: bool) -> dict[str, str]:
    """Every establishment of a comuna. The service matches a search term anywhere in a name and stops at CAP names, so
    a term that fills the list is narrowed by a character after it until no term does."""
    found: dict[str, str] = {}
    seen: set[str] = set()
    terms = list(ALPHABET)
    while terms:
        seen.update(terms)
        urls = [tsj_establishments_url(BUILDINGS, comuna, term) for term in terms]
        if fetch:
            raw.fetch_many(SOURCE, urls, suffix=".json", workers=WORKERS, keep_cookies=False)
        more: list[str] = []
        for term, url in zip(terms, urls, strict=True):
            listed = _codes(url)
            found.update(listed)
            if len(listed) >= CAP:
                more += [term + char for char in ALPHABET if term + char not in seen]
        terms = list(dict.fromkeys(more))
    return found


def tsj_establishments(comuna: int) -> list[tuple[str, str]]:
    return sorted(_explore(comuna, fetch=False).items(), key=lambda item: item[1])


def tsj_tables(page: int) -> list[Table]:
    out = []
    for comuna in COMUNAS:
        for code, name in tsj_establishments(comuna):
            for item in _json(tsj_tables_url(page, comuna, code)):
                if item["value"]:
                    out.append(Table(comuna, name, item["value"], tsj_table_url(page, comuna, code, item["value"])))
    return out


def fetch_tsj(pages: list[int]) -> list[str]:
    """Fetch the listings and then every table page of the given elections; return the table URLs."""
    for comuna in COMUNAS:
        _explore(comuna, fetch=True)
    raw.fetch_many(
        SOURCE,
        [
            tsj_tables_url(page, comuna, code)
            for page in pages
            for comuna in COMUNAS
            for code, _ in tsj_establishments(comuna)
        ],
        suffix=".json",
        workers=WORKERS,
        keep_cookies=False,
    )
    urls = [table.url for page in pages for table in tsj_tables(page)]
    fetch_pages(urls)
    return urls


def fetch_pages(urls: list[str]) -> None:
    """Fetch table pages. Under load the services sometimes answer 200 with an empty body; such an answer is dropped
    from the cache and fetched again."""
    for _ in range(5):
        raw.fetch_many(SOURCE, urls, suffix=".html", workers=WORKERS, batch=500, keep_cookies=False)
        with raw.locked(SOURCE) as index:
            empty = [url for url in urls if url in index and index[url]["bytes"] == 0]
            for url in empty:
                del index[url]
        if not empty:
            return
    raise RuntimeError(f"{len(empty)} table pages stay empty, such as {empty[0]}")


def te_section_url(comuna: int) -> str:
    return f"{TE}/seccion/?{comuna}"


def te_table_url(number: str) -> str:
    return f"{TE}/mesa/?{number}"


def _links(path: str, prefix: str) -> list[str]:
    text = raw.cached(SOURCE, path).path.read_text(encoding="utf-8", errors="replace")
    return [html.unescape(link) for link in re.findall(r'href="\.\./' + prefix + r'/\?([^"]*)"', text)]


def te_establishments(comuna: int) -> list[str]:
    return [unquote_plus(link) for link in _links(te_section_url(comuna), "establecimientos")]


def te_establishment_links(comuna: int) -> list[str]:
    return [f"{TE}/establecimientos/?{link}" for link in _links(te_section_url(comuna), "establecimientos")]


def te_tables(comuna: int) -> list[Table]:
    out = []
    for link, name in zip(te_establishment_links(comuna), te_establishments(comuna), strict=True):
        for number in _links(link, "mesa"):
            out.append(Table(comuna, name, number, te_table_url(number)))
    return out


def fetch_te() -> list[str]:
    raw.fetch_many(SOURCE, [te_section_url(c) for c in COMUNAS], suffix=".html", workers=WORKERS, keep_cookies=False)
    raw.fetch_many(
        SOURCE, [link for c in COMUNAS for link in te_establishment_links(c)], suffix=".html", workers=WORKERS
    )
    urls = [table.url for c in COMUNAS for table in te_tables(c)]
    fetch_pages(urls)
    return urls


def te_all_tables() -> list[Table]:
    """Every table once. The service lists a building's tables by its name, so two buildings with one name in
    different comunas each list the other's tables. Tables are numbered in runs by building, so a table listed under
    two comunas goes to the one that holds the nearest table number listed under it alone."""
    listed: dict[str, list[Table]] = {}
    for comuna in COMUNAS:
        for table in te_tables(comuna):
            listed.setdefault(table.number, []).append(table)
    alone: dict[int, list[int]] = {comuna: [] for comuna in COMUNAS}
    for tables in listed.values():
        if len(tables) == 1:
            alone[tables[0].comuna or 0].append(int(tables[0].number))
    out = []
    for number, tables in listed.items():
        if len(tables) > 1:
            tables = [min(tables, key=lambda t: min(abs(n - int(number)) for n in alone[t.comuna or 0]))]
        table = tables[0]
        if table.establishment == TEST and 50000 < int(number) <= 50000 + len(COMUNAS):
            # the fifteen test tables each sit in the comuna of its number, where the comuna's total counts them
            table = Table(int(number) - 50000, table.establishment, number, table.url)
        out.append(table)
    return out
