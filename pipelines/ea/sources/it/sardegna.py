"""Sardinia's regional elections of 2004, 2009, 2019, and 2024, from the Region's own results sites.

The Interior Ministry's historical archive leaves the special-statute regions out, so these results come from the
Autonomous Region of Sardinia's election pages. For each election the Region publishes the votes for each candidate
for President and for each list, by municipality (2009, 2019, and 2024) or only for the whole region and its eight
electoral circumscriptions (2004), with the registered voters and voters of each municipality.

Production already holds the Sardinia region area and the 377 municipal reporting units (`sardegna_comuni.tsv`), so the
packages reference them and mint no places. The region's polity, its council, and its presidency are new, and so are
the lists and candidates, whose ids come from this pipeline's own keys.

The Region's pages give counts of sections, not proclamations. The Court of Appeal's proclamation minutes, which
declare the elected President and the council seats, are scanned forms that need digitizing, so the packages hold no
outcomes; the 2014 results site, and every election from 1949 through 1999, are not in a source the archive reached.
"""

import csv
import html
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import NAMESPACE, Package
from ea.sources.it.interno import REGISTERED, VOTERS, VOTES

SOURCE = "it.sardegna"
COMUNI = Path(__file__).with_name("sardegna_comuni.tsv")
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
SARDINIA = str(uuid5(NAMESPACE, "polity\x1fIT-88"))
# Rows production holds, written by it.interno and it.storico.
REGION_AREA = "01a13bf7-2f89-7329-a8e6-bc0848c79c1a"
REGION_UNIT = "82a56387-04b5-569e-aef6-a2177293e91b"
SARDO_PARTY = "f44ba4ac-89d6-5624-aadc-da597235b4d9"
SARDO_LABELS = {"PSDAZ", "PARTITO SARDO DAZIONE"}
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
OLD = "https://old.regione.sardegna.it"
NEW = "https://elezioniregionali2024.regione.sardegna.it"


@dataclass(frozen=True)
class Year:
    year: int
    first: date
    last: date
    seats: int
    pages: dict[str, str]
    stage: str
    legislature: str


YEARS = {
    2004: Year(
        2004,
        date(2004, 6, 12),
        date(2004, 6, 13),
        80,
        {
            "summary": f"{OLD}/Elezioni-2004/risultati/Riassuntivo_def.htm",
            "voters": f"{OLD}/Elezioni-2004/risultati/dati%20sui%20votanti.htm",
        },
        "official",
        "XIII",
    ),
    2009: Year(
        2009,
        date(2009, 2, 15),
        date(2009, 2, 16),
        80,
        {
            "summary": f"{OLD}/argomenti/attivita_istituzionali/elezioni2009/risultatiriassuntivi.html",
            "comuni": f"{OLD}/argomenti/attivita_istituzionali/elezioni2009/risultati_comune.html",
            "voters": f"{OLD}/argomenti/attivita_istituzionali/elezioni2009/votanti.html",
        },
        "official",
        "XIV",
    ),
    2019: Year(
        2019,
        date(2019, 2, 24),
        date(2019, 2, 24),
        60,
        {
            "summary": f"{OLD}/speciali/elezioniregionali2019/risultatiriassuntivi.html",
            "comuni": f"{OLD}/speciali/elezioniregionali2019/risultatipercomune.html",
            "voters": f"{OLD}/speciali/elezioniregionali2019/votanti.html",
        },
        "official",
        "XVI",
    ),
    2024: Year(
        2024,
        date(2024, 2, 25),
        date(2024, 2, 26),
        60,
        {
            "summary": f"{NEW}/risultati-riassuntivi",
            "comuni": f"{NEW}/risultati-per-comune",
            "voters": f"{NEW}/votanti",
        },
        "preliminary",
        "XVII",
    ),
}
CIRCUMSCRIPTIONS = [
    "CAGLIARI",
    "CARBONIA IGLESIAS",
    "MEDIO CAMPIDANO",
    "NUORO",
    "OGLIASTRA",
    "OLBIA TEMPIO",
    "ORISTANO",
    "SASSARI",
]
# Names the Region prints that differ from the municipality's name in the Interior Ministry's list.
ALIASES = {
    "TRINITA DAGULTU VIGNOLA": "TRINITA DAGULTU E VIGNOLA",
    "SANTULUSSURGIU": "SANTU LUSSURGIU",
    "VILLAVERDE": "VILLA VERDE",
    "SANTA TERESA DI GALLURA": "SANTA TERESA GALLURA",
}


def fetch() -> None:
    for year in YEARS.values():
        for url in year.pages.values():
            raw.fetch(SOURCE, url, suffix=".html")


def ingest(folder: Path, index: dict[str, dict[str, str]]) -> None:
    """Split the saved page sets, one file of several pages per election, into one cached file per page."""
    for entry in index.values():
        content = (folder / entry["file"]).read_bytes()
        text = content.decode("utf-8", errors="replace")
        parts = re.split(r"<!-- SOURCE: ([^>]*?) -->", text)
        for page, body in zip(parts[1::2], parts[2::2], strict=True):
            if any(page == url for year in YEARS.values() for url in year.pages.values()):
                raw.store(
                    SOURCE,
                    page,
                    body.encode("utf-8"),
                    suffix=".html",
                    retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
                    media_type="text/html",
                )


def packages() -> list[str]:
    return ["it.sardegna/registry", *(f"it.sardegna/{year}" for year in YEARS)]


def parse() -> None:
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- reading the pages


def number(text: str) -> int:
    return int(re.sub(r"[.\s]", "", text))


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("`", "'").replace("\\", ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9' ]", " ", text).replace("'", "")).strip()


class _Rows(HTMLParser):
    """The leaf rows of a page's tables, in order. A row that holds a municipality's or a circumscription's
    figures is marked by the collapsed row that follows it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, object]] = []
        self._stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            row_id = dict(attrs).get("id") or ""
            if self._stack:
                self._stack[-1]["leaf"] = False
            if self.rows and row_id.startswith("tabcirc"):
                self.rows[-1]["kind"] = "circ"
            elif self.rows and row_id.startswith("tabcoaliz"):
                self.rows[-1]["kind"] = "comune"
            self._stack.append({"cells": [], "leaf": True, "kind": "", "text": None})
        elif tag in ("td", "th") and self._stack:
            self._stack[-1]["text"] = []
        elif tag == "br" and self._stack and self._stack[-1]["text"] is not None:
            cast_list(self._stack[-1]["text"]).append(" ")

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1]["text"] is not None:
            cast_list(self._stack[-1]["text"]).append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        top = self._stack[-1]
        if tag in ("td", "th") and top["text"] is not None:
            cast_list(top["cells"]).append(re.sub(r"\s+", " ", "".join(cast_list(top["text"]))).strip())
            top["text"] = None
        elif tag == "tr":
            self._stack.pop()
            if top["leaf"]:
                self.rows.append({"cells": top["cells"], "kind": top["kind"]})


def cast_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return value


def rows_of(page: str) -> list[dict[str, object]]:
    parser = _Rows()
    parser.feed(page)
    parser.close()
    return parser.rows


@dataclass
class Candidate:
    name: str
    coalition: str
    votes: int
    lists: dict[str, int] = field(default_factory=dict)


@dataclass
class Place:
    name: str
    circumscription: str
    candidates: list[Candidate] = field(default_factory=list)


def _candidates(rows: Iterable[list[str]]) -> list[Candidate]:
    found: list[Candidate] = []
    for cells in rows:
        if len(cells) != 3 or cells[0] in ("Lista", "") or not re.fullmatch(r"[\d.]+", cells[2]):
            continue
        label = cells[0]
        if "Coalizione:" in label:
            name, coalition = label.split(" - Coalizione:", 1)
            found.append(Candidate(name.strip(), coalition.strip(), number(cells[2])))
        elif label.strip().lower().startswith("totale"):
            continue
        else:
            found[-1].lists[label] = number(cells[2])
    return found


def comuni_page(page: str) -> list[Place]:
    """Each municipality's candidates, with their coalition's lists, from a results-by-municipality page."""
    places: list[Place] = []
    circumscription = ""
    block: list[list[str]] = []

    open_place: list[Place] = []

    def close() -> None:
        if open_place:
            open_place.pop().candidates = _candidates(block)
        block.clear()

    for row in rows_of(page):
        cells = [str(cell) for cell in cast_cells(row["cells"])]
        if row["kind"] == "circ":
            close()
            circumscription = cells[1]
        elif row["kind"] == "comune":
            close()
            places.append(Place(cells[1], circumscription))
            open_place.append(places[-1])
        elif open_place:
            block.append(cells)
    close()
    return places


def cast_cells(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected cells")
    return value


def summary_page(page: str) -> list[Candidate]:
    return _candidates([[str(cell) for cell in cast_cells(row["cells"])] for row in rows_of(page)])


def summary_2004(page: str) -> list[Candidate]:
    """The 2004 summary prints each coalition's lists, a total, then the candidate's votes and name."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", page)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    tokens = [re.sub(r"\s+", " ", html.unescape(token)).strip() for token in text.split("|")]
    tokens = [token for token in tokens if token]

    def percent(index: int) -> bool:
        return index < len(tokens) and re.fullmatch(r"[\d,]+%", tokens[index]) is not None

    found: list[Candidate] = []
    lists: dict[str, int] = {}
    index = 0
    while index < len(tokens):
        if not (percent(index + 1) and re.fullmatch(r"[\d.]+", tokens[index + 2] if index + 2 < len(tokens) else "")):
            index += 1
            continue
        label, votes = tokens[index], number(tokens[index + 2])
        index += 3
        if label.upper() == "TOTALE":
            continue
        if percent(index + 1):
            lists[label] = votes
        else:
            found.append(Candidate(tokens[index], label, votes, lists))
            lists = {}
            index += 1
    return found


def voters_table(page: str) -> dict[str, tuple[int, int]]:
    """Registered voters and voters by municipality."""
    out: dict[str, tuple[int, int]] = {}
    for row in rows_of(page):
        cells = [str(cell) for cell in cast_cells(row["cells"])]
        if (
            len(cells) in (5, 6)
            and cells[0] not in ("Comune", "Totale", "Regionale", "")
            and re.fullmatch(r"[\d.]+", cells[1])
        ):
            out[canon(cells[0])] = (number(cells[1]), number(cells[-2]))
    return out


def circumscription_voters_2004(page: str) -> tuple[int, int]:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    match = re.search(r"Iscritti:\s*([\d.]+)", text)
    total = re.search(r"Totale\s+([\d.]+)\s+100%\s+([\d.]+)\s+([\d.]+)", text)
    if match is None or total is None:
        raise ValueError("the 2004 voters page has changed")
    return number(match[1]), number(total[3])


def origin_data(page: str) -> dict[str, object]:
    match = re.search(r"var origin_data\s*=\s*", page)
    if match is None:
        raise ValueError("the page holds no origin_data")
    return obj(json.JSONDecoder().raw_decode(page[match.end() :])[0])


def obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("expected an object")
    return value


def arr(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected an array")
    return value


def text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


def count(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError("expected a whole number")
    return value


def comuni_2024(page: str) -> list[Place]:
    places = []
    for circ in map(obj, arr(origin_data(page)["circoscrizioni"])):
        for comune in map(obj, arr(circ["comuni"])):
            place = Place(text(comune["nomeComune"]), text(circ["denominazione"]).upper())
            for entry in map(obj, arr(comune["candidati"])):
                candidate = Candidate(text(entry["nomeCognome"]), "", count(entry["voti"]))
                for item in map(obj, arr(entry["coalizioni"])):
                    candidate.lists[text(item["nomeLista"])] = count(item["votiCoalizione"])
                place.candidates.append(candidate)
            places.append(place)
    return places


def summary_2024(page: str) -> list[Candidate]:
    found = []
    for entry in map(obj, arr(origin_data(page)["candidatiPresidenti"])):
        candidate = Candidate(text(entry["candidatoPresidente"]), "", count(entry["voti"]))
        for item in map(obj, arr(entry["listeCollegate"])):
            candidate.lists[text(item["nomeLista"])] = count(item["votiLista"])
        found.append(candidate)
    return found


def voters_2024(page: str) -> dict[str, tuple[int, int]]:
    out = {}
    for circ in map(obj, arr(origin_data(page)["circoscrizioni"])):
        for comune in map(obj, arr(circ["comuni"])):
            out[canon(text(comune["nomeComune"]))] = (
                count(comune["elettoriComune"]),
                count(comune["votantiTotaliComune"]),
            )
    return out


def sections(page: str) -> tuple[int, int]:
    """Sections counted for President, and sections in all, as the summary page prints them."""
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    match = re.search(r"(?:presidente|regionali scrutinate):\s*([\d.]+) su ([\d.]+)", plain)
    if match is not None:
        return number(match[1]), number(match[2])
    data = origin_data(page)
    return count(data["numeroSezioniScrutinatePerPresidente"]), count(data["numeroSezioniTotali"])


def units() -> dict[str, tuple[str, str]]:
    with COMUNI.open(encoding="utf-8", newline="") as file:
        table = list(csv.DictReader(file, delimiter="\t"))
    out = {norm(row["name"]): (row["code"], row["unit_id"]) for row in table}
    if len(table) != 377 or len(out) != 377:
        raise ValueError("expected 377 municipalities with distinct names")
    return out


def canon(name: str) -> str:
    return ALIASES.get(norm(name), norm(name))


def unit_of(name: str, known: dict[str, tuple[str, str]]) -> str:
    key = canon(name)
    if key not in known:
        raise KeyError(f"{name} is not one of the municipalities production holds")
    return known[key][1]


# ---------------------------------------------------------------- building the packages


def key(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id() -> str:
    return key("source", "regione")


def page(year: Year, name: str) -> str:
    return raw.cached(SOURCE, year.pages[name]).path.read_text(encoding="utf-8", errors="replace")


def party_key(label: str) -> str:
    return norm(label)


@dataclass
class Loaded:
    region: list[Candidate]
    places: list[Place]
    voters: dict[str, tuple[int, int]]
    total: tuple[int, int] | None
    sections: tuple[int, int]


def load(year: Year) -> Loaded:
    """Everything one election's pages give: the region's candidates, the places, and the voters."""
    if year.year == 2004:
        return Loaded(
            summary_2004(page(year, "summary")),
            [],
            {},
            circumscription_voters_2004(page(year, "voters")),
            (1784, 1784),
        )
    if year.year == 2024:
        return Loaded(
            summary_2024(page(year, "summary")),
            comuni_2024(page(year, "comuni")),
            voters_2024(page(year, "voters")),
            None,
            sections(page(year, "summary")),
        )
    return Loaded(
        summary_page(page(year, "summary")),
        comuni_page(page(year, "comuni")),
        voters_table(page(year, "voters")),
        None,
        sections(page(year, "summary")),
    )


def registry() -> Package:
    p = Package(
        "it.sardegna/registry",
        SOURCE,
        "Sardinia's polity, its regional council and presidency, and the lists that stood in 2004, 2009, 2019, and 2024",
    )
    p.external |= {ITALY, REGION_AREA, SARDO_PARTY}
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher="Regione Autonoma della Sardegna",
        title="SardegnaElezioni: regional election results",
        url="https://elezioni.regione.sardegna.it/regionali",
        license=LICENSE,
        attribution="Regione Autonoma della Sardegna",
    )
    p.add("polity", id=SARDINIA, kind="region", iso_code="IT-88")
    p.add("polity_parent", polity_id=SARDINIA, parent_id=ITALY)
    p.add("polity_territory", polity_id=SARDINIA, area_id=REGION_AREA)
    p.add("slug", path="it/sardegna", entity_id=SARDINIA, is_current=True)
    p.name(SARDINIA, "Sardegna", "it", "official")
    p.name(SARDINIA, "Sardinia", "en", "english", False)
    body = key("body", "council")
    p.add("body", id=body, polity_id=SARDINIA, kind="unicameral")
    p.name(body, "Regional Council of Sardinia", "en", "official")
    p.name(body, "Consiglio regionale della Sardegna", "it", "native", False)
    office = key("office", "president")
    p.add("office", id=office, polity_id=SARDINIA, body_id=None, kind="governor")
    p.name(office, "President of the Region of Sardinia", "en", "official")
    p.name(office, "Presidente della Regione Sardegna", "it", "native", False)
    labels: dict[str, str] = {}
    for year in YEARS.values():
        for candidate in load(year).region:
            for label in candidate.lists:
                shown = labels.get(party_key(label))
                if shown is None or (shown.isupper() and not label.isupper()):
                    labels[party_key(label)] = label
    for name, label in sorted(labels.items()):
        if name in SARDO_LABELS:
            continue
        id = key("party", name)
        p.add("party", id=id, kind="list_vehicle", polity_id=ITALY)
        p.name(id, label if not label.isupper() else label.title(), "it", "official")
    return p


def election(year_number: int) -> Package:
    year = YEARS[year_number]
    data = load(year)
    p = Package(
        f"it.sardegna/{year.year}",
        SOURCE,
        f"The {year.year} Sardinian regional election: votes for President and for the council's lists"
        + (" by municipality" if data.places else " for the whole region"),
        depends=["it.sardegna/registry"],
    )
    p.external |= {ITALY, REGION_AREA, REGION_UNIT, SARDO_PARTY}
    for url in year.pages.values():
        p.artifact(raw.cached(SOURCE, url), source_id())
    known = units()
    region, places, voters = data.region, data.places, data.voters
    counted, total = data.sections
    election_id = key("election", year.year)
    p.add(
        "election",
        id=election_id,
        polity_id=SARDINIA,
        kind="general",
        date_from=year.first,
        date_to=year.last,
        status="held",
    )
    p.name(election_id, f"{year.year} Sardinian regional election", "en", "official")
    p.name(election_id, f"Elezioni regionali in Sardegna del {year.year}", "it", "native", False)
    path = f"it/sardegna/{year.first}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    note = (
        f"Votes are as the Autonomous Region of Sardinia's results pages print them, with {counted:,} of {total:,} "
        "sections counted for President."
    )
    if year.stage == "preliminary":
        note += (
            " The page is labeled partial data, as of 27 February 2024 at 16:33, and the archive holds no later count."
        )
    p.add("note", id=key("note", year.year), entity_id=election_id, kind="methodology", body_md=note, lang="en")
    parties: dict[str, str] = {}
    president = key("contest", year.year, "president")
    p.add(
        "contest",
        id=president,
        election_id=election_id,
        kind="office",
        office_id=key("office", "president"),
        district_area_id=REGION_AREA,
        magnitude=1,
        default_result_kind="counted",
    )
    p.name(president, f"{year.year} Sardinian regional election, President of the Region", "en", "official")
    p.add("slug", path=f"{path}/presidente", entity_id=president, is_current=True)
    council = key("contest", year.year, "council")
    p.add(
        "contest",
        id=council,
        election_id=election_id,
        kind="list_allocation",
        body_id=key("body", "council"),
        district_area_id=REGION_AREA,
        magnitude=year.seats,
        default_result_kind="counted",
    )
    p.name(council, f"{year.year} Sardinian regional election, council lists", "en", "official")
    p.add("slug", path=f"{path}/consiglio", entity_id=council, is_current=True)
    sets = {}
    for name, contest in (("president", president), ("council", council)):
        sets[name] = key("result_set", year.year, name)
        p.add(
            "result_set",
            id=sets[name],
            election_id=election_id,
            contest_id=contest,
            stage=year.stage,
            kind="counted",
            source_id=source_id(),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", sets[name]), entity_id=sets[name], kind="methodology", body_md=note, lang="en")
    candidate_options: dict[str, str] = {}
    list_options: dict[str, str] = {}
    for position, candidate in enumerate(region, 1):
        option = candidate_options[norm(candidate.name)] = key("option", year.year, "candidate", norm(candidate.name))
        person = key("person", year.year, norm(candidate.name))
        shown = candidate.name if not candidate.name.isupper() else candidate.name.title()
        p.add("person", id=person)
        p.name(person, shown, "it", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=president,
            kind="candidate",
            ballot_position=position,
            ballot_label=candidate.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", year.year, norm(candidate.name)),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
        )
        for label in candidate.lists:
            name = party_key(label)
            if name in list_options:
                continue
            list_options[name] = key("option", year.year, "list", name)
            p.add(
                "contest_option",
                id=list_options[name],
                contest_id=council,
                kind="list",
                party_id=SARDO_PARTY if name in SARDO_LABELS else key("party", name),
                ballot_position=len(list_options),
                ballot_label=label,
            )
            parties[name] = label

    def put(result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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

    def figures(result_set: str, unit: str, candidates: list[Candidate], council_set: str) -> None:
        for candidate in candidates:
            put(result_set, unit, candidate_options[norm(candidate.name)], VOTES, candidate.votes)
            for label, votes in candidate.lists.items():
                put(council_set, unit, list_options[party_key(label)], VOTES, votes)

    p.external |= {REGION_UNIT}
    if year.year == 2019:
        p.accept(
            "accounting.declared",
            "The Region's 2019 municipal pages add up to 14 fewer votes for Riformatori sardi (36,285 against 36,299) "
            "and 19 fewer for Pro Sardinia - Unione di centro (26,929 against 26,948) than its summary page prints; "
            "every other figure agrees exactly.",
        )
    if places:
        seen = set()
        for place in places:
            unit = unit_of(place.name, known)
            if unit in seen:
                raise ValueError(f"{place.name} appears twice")
            seen.add(unit)
            p.external.add(unit)
            figures(sets["president"], unit, place.candidates, sets["council"])
            electors, ballots = voters[canon(place.name)]
            put(sets["president"], unit, None, REGISTERED, electors)
            put(sets["president"], unit, None, VOTERS, ballots)
        missing = {code for code, _ in known.values()} - {code for code, unit in known.values() if unit in seen}
        if missing:
            raise ValueError(f"{len(missing)} municipalities have no results: {sorted(missing)[:5]}")
    else:
        if data.total is None:
            raise ValueError("no regional voters")
        electors, ballots = data.total
        figures(sets["president"], REGION_UNIT, region, sets["council"])
        put(sets["president"], REGION_UNIT, None, REGISTERED, electors)
        put(sets["president"], REGION_UNIT, None, VOTERS, ballots)
    for candidate in region:
        p.add(
            "declared",
            result_set_id=sets["president"],
            option_id=candidate_options[norm(candidate.name)],
            measure=VOTES,
            value=candidate.votes,
        )
        for label, votes in candidate.lists.items():
            p.add(
                "declared",
                result_set_id=sets["council"],
                option_id=list_options[party_key(label)],
                measure=VOTES,
                value=votes,
            )
    return p
