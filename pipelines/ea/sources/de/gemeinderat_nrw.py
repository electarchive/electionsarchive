"""Council elections of the municipalities of North Rhine-Westphalia (Gemeinderat, Stadtrat), 2009 to 2025.

The Landeswahlleiterin publishes each election's results by municipality: text files with the votes and seats of
every list for 2020 and 2025, and one page per municipality for 2014, which also prints the 2009 results as the
comparison. For 2025 the council elections of the 22 kreisfreie Städte also come by Stimmbezirk.
"""

import csv
import re
from datetime import date
from functools import cache
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "de.gemeinderat_nrw"
BASE = "https://www.wahlergebnisse.nrw/kommunalwahlen"
NRW = "01a13bb5-c563-741b-83f7-4b74611ca2d9"
DAYS = {2009: date(2009, 8, 30), 2014: date(2014, 5, 25), 2020: date(2020, 9, 13), 2025: date(2025, 9, 14)}
TEXTS = {
    2020: ("2020", "KW20_parla_{}_{}.txt"),
    2025: ("2025", "KW25_parla_{}_{}.txt"),
}
STIMMBEZIRKE = f"{BASE}/2025/KW25_Stimmbezirke.csv"


def text_url(year: int, part: str, kind: str) -> str:
    folder, name = TEXTS[year]
    return f"{BASE}/{folder}/{name.format(part, kind)}"


def index_url(year: int, name: str) -> str:
    return f"{BASE}/{year}/{name}.shtml"


def fetch() -> None:
    for year in TEXTS:
        for part in ("gemeinden", "insgesamt"):
            for kind in ("stimmen", "sitze"):
                raw.fetch(SOURCE, text_url(year, part, kind), suffix=".txt")
        raw.fetch(SOURCE, index_url(year, "index_gr"), suffix=".html")
        raw.fetch(SOURCE, index_url(year, "index_obb_lr"), suffix=".html")
    raw.fetch(SOURCE, STIMMBEZIRKE, suffix=".csv")
    raw.fetch(SOURCE, index_url(2014, "index_gr"), suffix=".html")
    raw.fetch(SOURCE, index_url(2014, "index_obb_lr"), suffix=".html")
    raw.fetch(SOURCE, f"{BASE}/2014/aktuell/a000000kw1400.html", suffix=".html")


class Result(NamedTuple):
    code: str
    name: str
    year: int
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    seats: int | None
    lists: list[tuple[str, int, int]]
    url: str


def whole(text: str | None) -> int | None:
    text = (text or "").strip().replace("\xa0", "").replace(" ", "")
    if not text or text in ("X", "-"):
        return None
    if text in ("—", "–", "—"):
        return 0
    return int(text.replace(".", ""))


def rows_of(name: str) -> list[list[str]]:
    return list(csv.reader(raw.cached(SOURCE, name).path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))


def _tables(year: int, part: str) -> dict[str, Result]:
    url_votes = text_url(year, part, "stimmen")
    url_seats = text_url(year, part, "sitze")
    votes = rows_of(url_votes)
    seats = rows_of(url_seats)
    start = next(i for i, row in enumerate(votes) if row and row[0].startswith("Verwaltungsbezirks-Nr"))
    head = votes[start]
    first = head.index("Gültige Stimmen") + 1
    labels = [label.strip() for label in head[first:]]
    names = {
        "registered": "Wahlberechtigte insgesamt",
        "voters": next(c for c in head if c.startswith("Wähler/-innen")),
        "invalid": "Ungültige Stimmen",
        "valid": "Gültige Stimmen",
    }
    start_seats = next(i for i, row in enumerate(seats) if row and row[0].startswith("Verwaltungsbezirks-Nr"))
    seat_head = seats[start_seats]
    seat_names = [seat_head[2 + 3 * i].strip() for i in range((len(seat_head) - 2) // 3)]
    if seat_names[0] != "Insgesamt" or seat_names[1:] != [label for label in labels if label][: len(seat_names) - 1]:
        trimmed = [label for label in labels if label]
        if seat_names[1:] != trimmed[: len(seat_names) - 1]:
            raise ValueError(f"{year} {part}: the lists of the vote and seat tables differ")
    by_code = {row[0]: row for row in seats[start_seats + 2 :] if row and row[0].isdigit()}
    out: dict[str, Result] = {}
    for row in votes[start + 1 :]:
        if not row or not row[0].isdigit() or row[0] == "000000":
            continue
        if part == "insgesamt" and "Krfr." not in row[1]:
            continue
        figure = {key: whole(row[head.index(name)]) for key, name in names.items()}
        mine = by_code[row[0]]
        total = whole(mine[2])
        lists = []
        for index, label in enumerate(labels):
            if not label:
                continue
            number = whole(row[first + index] if first + index < len(row) else None) or 0
            at = seat_names.index(label) if label in seat_names else None
            won = whole(mine[2 + 3 * at]) or 0 if at is not None else 0
            if number or won:
                lists.append((label, number, won))
        out[row[0]] = Result(
            row[0],
            re.sub(r"^Krfr\. Stadt ", "", row[1]).strip(),
            year,
            figure["registered"],
            figure["voters"],
            figure["invalid"],
            figure["valid"],
            total,
            lists,
            url_votes,
        )
    return out


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)


def codes_2014() -> list[str]:
    found: set[str] = set()
    for name in ("index_gr", "index_obb_lr"):
        text = raw.cached(SOURCE, index_url(2014, name)).path.read_text(errors="replace")
        for letter, code in re.findall(r"aktuell[\\/]([ac])(\d{6})kw1400", text):
            if (letter == "c") or (code in KREISFREI_2014):
                found.add(code)
    return sorted(found)


KREISFREI_2014 = {
    "111000", "112000", "113000", "114000", "116000", "117000", "119000", "120000", "122000", "124000",
    "313000", "314000", "315000", "316000", "512000", "513000", "515000", "711000",
    "911000", "913000", "914000", "915000", "916000",
}  # fmt: skip


def page_url_2014(code: str) -> str:
    letter = "a" if code in KREISFREI_2014 else "c"
    return f"{BASE}/2014/aktuell/{letter}{code}kw1400.html"


def page_2014(code: str) -> tuple[Result, Result]:
    url = page_url_2014(code)
    parser = _Table()
    parser.feed(raw.cached(SOURCE, url).path.read_text(errors="replace"))
    flat = " ".join(" ".join(parser.text).split())
    named = re.search(r"Ergebnis für:\s*(\d{6})\s+(.+?)\s+zur Übersicht", flat)
    if named is None or named.group(1) != code:
        raise ValueError(f"{url} names another municipality")
    name = re.sub(r"^Krfr\. Stadt ", "", named.group(2)).strip()
    head = next(i for i, row in enumerate(parser.rows) if row and row[0] == "Merkmal")
    figures: dict[str, tuple[int | None, int | None]] = {}
    seats: tuple[int, int] | None = None
    lists: tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]] = ([], [])
    keys = {
        "Wahlberechtigte insgesamt": "registered",
        "Wähler/-innen": "voters",
        "Ungültige Stimmen": "invalid",
        "Gültige Stimmen": "valid",
    }
    listing = False
    for row in parser.rows[head + 2 :]:
        label, cells = row[0], row[1:]
        if label in keys and len(cells) >= 11:
            figures[keys[label]] = (whole(cells[0]), whole(cells[2]))
        elif label == "Vertreter/-innen":
            seats = (whole(cells[1]) or 0, whole(cells[4]) or 0)
            listing = True
        elif listing and len(cells) == 11:
            now, before = whole(cells[0]) or 0, whole(cells[2]) or 0
            won_now, won_before = whole(cells[5]) or 0, whole(cells[8]) or 0
            if now or won_now:
                lists[0].append((label, now, won_now))
            if before or won_before:
                lists[1].append((label, before, won_before))
        elif listing and label.startswith("Download"):
            break
    if seats is None:
        raise ValueError(f"{url} prints no seats")
    out = []
    for index, year in enumerate((2014, 2009)):
        out.append(
            Result(
                code,
                name,
                year,
                figures["registered"][index],
                figures["voters"][index],
                figures["invalid"][index],
                figures["valid"][index],
                seats[index],
                lists[index],
                url,
            )
        )
    return out[0], out[1]


class District(NamedTuple):
    code: str
    number: str
    postal: bool
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    votes: dict[str, int]


def districts() -> dict[str, list[District]]:
    """The council elections of the kreisfreie Städte by Stimmbezirk in 2025, by municipality code."""
    rows = rows_of(STIMMBEZIRKE)
    labels = [label.strip() for label in rows[1][13:]]
    out: dict[str, list[District]] = {}
    for row in rows[3:]:
        if len(row) < 13 or not row[0].isdigit():
            continue
        votes = {label: whole(row[13 + i]) or 0 for i, label in enumerate(labels) if label and 13 + i < len(row)}
        out.setdefault(row[1], []).append(
            District(
                row[1],
                row[3].strip(),
                row[4].strip() != "Urne",
                whole(row[8]),
                whole(row[9]),
                whole(row[11]),
                whole(row[12]),
                {label: v for label, v in votes.items() if v},
            )
        )
    return out


def canonical(label: str) -> str:
    found = re.fullmatch(r"WGR\s*(\d+)", label)
    return f"Wählergruppe {found.group(1)}" if found else label


NRW_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fnrw"))
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fsainte_lague_schepers"))
SCHEME = "de.nw_gemeinderat"
SCHEME_DISTRICT = "de.nw_stimmbezirk"
AREAS = Path(__file__).with_name("buergermeister_nw_areas.tsv")
AGGREGATES = {"Wählergruppen": "others", "Sonstige": "others", "Einzelbewerber/-innen": "independents"}
METHOD = (
    "Counted by the Landeswahlleiterin of North Rhine-Westphalia and published for each municipality. Each voter "
    "has one vote, cast for a candidate in one of the electoral districts of the municipality or for the list of "
    "that candidate's party or voters' group; the source prints each list's votes as the votes of its candidates "
    "added together, so the lists' votes sum to the valid votes. Seats are stored as the source declares them, "
    "including the seats added to balance direct mandates above a list's proportional share. The source prints "
    "each local voters' group (Wählergruppe) as a numbered line that stands for a different group in each "
    "municipality, and the independent candidates (Einzelbewerber) as one line."
)
METHOD_2009 = (
    " The 2009 figures are printed as the comparison column of the 2014 results pages, and the source prints a "
    "list's votes there as zero when it stood in neither election."
)
METHOD_DISTRICT = (
    " The source also prints the votes of each polling district (Stimmbezirk) and postal-vote district "
    "(Briefwahl) of the city, which add up to the printed city totals; the districts are the reporting units."
)


@cache
def areas() -> dict[str, str]:
    lines = AREAS.read_text().splitlines()[1:]
    return dict(line.split("\t") for line in lines)


def ags(code: str) -> str:
    """The municipal key of a code the source prints; the source keeps Aachen under its old kreisfrei code."""
    return "05334002" if code == "313000" else "05" + code


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")


def norm(label: str) -> str:
    return re.sub(r"\s+", "", label).casefold()


def is_party(label: str) -> bool:
    return label not in AGGREGATES and not label.startswith("Wählergruppe")


@cache
def results() -> dict[int, dict[str, Result]]:
    out: dict[int, dict[str, Result]] = {2009: {}, 2014: {}}
    for year in TEXTS:
        out[year] = {**_tables(year, "gemeinden"), **_tables(year, "insgesamt")}
    for code in codes_2014():
        now, before = page_2014(code)
        out[2014][code] = now
        out[2009][code] = before
    for year, pool in out.items():
        if set(pool) != set(out[2025]):
            raise ValueError(f"{year} holds other municipalities than 2025")
        for result in pool.values():
            if ags(result.code) not in areas():
                raise ValueError(f"{result.code} {result.name} has no area")
            if (
                sum(v for _, v, _ in result.lists) != result.valid
                or sum(w for *_, w in result.lists) != result.seats
                or result.voters != (result.valid or 0) + (result.invalid or 0)
            ):
                raise ValueError(f"{year} {result.name}: the lists differ from the printed totals")
    return dict(sorted(out.items()))


def display(result: Result) -> str:
    return re.sub(r",\s*Stadt$", "", result.name)


def is_city(code: str) -> bool:
    return results()[2025][code].name.endswith(", Stadt") or code in KREISFREI_2014


def body(code: str) -> str:
    return key("body", code)


def party(label: str) -> str:
    return key("party", norm(label))


def election_path(year: int) -> str:
    return f"de/{DAYS[year]}-nw-municipal-councils"


def raw_urls() -> list[str]:
    urls = [
        text_url(year, part, kind)
        for year in TEXTS
        for part in ("gemeinden", "insgesamt")
        for kind in ("stimmen", "sitze")
    ]
    urls += [STIMMBEZIRKE, f"{BASE}/2014/aktuell/a000000kw1400.html"]
    urls += [index_url(year, name) for year in (*TEXTS, 2014) for name in ("index_gr", "index_obb_lr")]
    urls += [page_url_2014(code) for code in codes_2014()]
    return urls


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The councils of the municipalities of North Rhine-Westphalia, the seat-allocation rules, and the parties and "
        "voters' groups of their council elections",
    )
    p.external |= {NRW, *areas().values()}
    p.add(
        "source",
        id=NRW_SOURCE,
        kind="official_body",
        publisher="Landeswahlleiterin des Landes Nordrhein-Westfalen",
        title="Wahlergebnisse in NRW: Kommunalwahlen",
        url="https://www.wahlergebnisse.nrw/kommunalwahlen/",
        license="Datenlizenz Deutschland, Zero, Version 2.0",
        license_url="https://www.govdata.de/dl-de/zero-2-0",
        attribution="Landeswahlleiterin NRW, wahlergebnisse.nrw",
    )
    for url in raw_urls():
        p.artifact(raw.cached(SOURCE, url), NRW_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=NRW,
        valid_from=date(2009, 1, 1),
        valid_to=None,
        rules={"formula": "sainte_lague_schepers"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The seats of a municipal council go to the lists in proportion to their votes by the "
            "Sainte-Laguë/Schepers divisor method; the votes of a list are the votes of its candidates added together."
        ),
        lang="en",
    )
    p.add("scheme", code=SCHEME, polity_id=NRW, kind="electoral_district", rank=1, noun="municipal council electorate")
    p.add(
        "scheme",
        code=SCHEME_DISTRICT,
        polity_id=NRW,
        kind="precinct",
        rank=6,
        noun="polling district",
        code_system=None,
    )
    for code, result in sorted(results()[2025].items()):
        text = display(result)
        city = is_city(code)
        id = body(code)
        p.add("body", id=id, polity_id=NRW, kind="council")
        p.name(id, f"Rat der {'Stadt' if city else 'Gemeinde'} {text}", "de", "official")
        p.name(id, f"{text} {'City' if city else 'Municipal'} Council", "en", "english", False)
        p.add("external_id", entity_id=id, scheme="de.gemeinderat_nrw.body", value=ags(code))
    labels: dict[str, str] = {}
    for pool in results().values():
        for result in pool.values():
            for label, _, _ in result.lists:
                if is_party(label):
                    labels[norm(label)] = label
    for label in sorted(labels.values()):
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=NRW)
        p.add("external_id", entity_id=id, scheme="de.gemeinderat_nrw.party", value=norm(label))
        p.name(id, label, "de", "official")
    return p


FIGURES = {"registered": 2, "voters": 3, "invalid": 5, "valid": 4}


def sources_of(year: int) -> list[str]:
    if year in (2009, 2014):
        return [page_url_2014(code) for code in codes_2014()]
    return [text_url(year, part, kind) for part in ("gemeinden", "insgesamt") for kind in ("stimmen", "sitze")]


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/nw-{year}",
        SOURCE,
        f"The {year} council elections of the municipalities of North Rhine-Westphalia, by municipality"
        + (" and, for the kreisfreie Städte, by polling district" if year == 2025 else ""),
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NRW, *areas().values()}
    for url in sources_of(year) + ([STIMMBEZIRKE] if year == 2025 else []):
        p.uses(raw.cached(SOURCE, url))
    pool = results()[year]
    id = key("election", year)
    path = election_path(year)
    p.add("election", id=id, polity_id=NRW, kind="local", date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{day:%B} {day.day}, {day.year} North Rhine-Westphalia municipal council elections", "en", "official")
    p.name(id, f"Kommunalwahlen in Nordrhein-Westfalen am {day:%d.%m.%Y}: Räte der Gemeinden", "de", "native", False)
    by_district = districts() if year == 2025 else {}
    taken: set[str] = set()
    for code, result in sorted(pool.items(), key=lambda item: (display(item[1]), item[0])):
        text = display(result)
        area = areas()[ags(code)]
        contest = key("contest", year, code)
        result_set = key("result_set", year, code)
        city = is_city(code)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="list_allocation",
            body_id=body(code),
            district_area_id=area,
            magnitude=result.seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {text} {'city' if city else 'municipal'} council election", "en", "official")
        p.name(contest, f"Ratswahl {text} {year}", "de", "native", False)
        tail = slug(text)
        if f"{path}/{tail}" in taken:
            tail = f"{tail}-{code}"
        taken.add(f"{path}/{tail}")
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        ahead = min((y for y in results() if y > year), default=None)
        p.add(
            "body_size",
            body_id=body(code),
            seats=result.seats,
            valid_from=day,
            valid_to=DAYS[ahead] if ahead else None,
        )
        method = METHOD + (METHOD_2009 if year == 2009 else "")
        split = by_district.get(code) if code in KREISFREI_2014 else None
        if split:
            method += METHOD_DISTRICT
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NRW_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: dict[str, str] = {}
        for position, (label, votes, won) in enumerate(result.lists, start=1):
            option = options[label] = key("option", year, code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=AGGREGATES.get(label, "list"),
                party_id=party(label) if is_party(label) else None,
                ballot_position=position,
                ballot_label=label,
            )
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        total = {
            "registered": result.registered,
            "voters": result.voters,
            "invalid": result.invalid,
            "valid": result.valid,
        }
        if split:
            for field, measure in FIGURES.items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total[field])
            for label, votes, _ in result.lists:
                p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=votes)
            sums = {field: 0 for field in FIGURES}
            counted: dict[str, int] = {}
            for district in split:
                unit = key("unit", year, code, district.number, "postal" if district.postal else "urn")
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=NRW,
                    scheme_code=SCHEME_DISTRICT,
                    code=f"{day}:{code}:{'B' if district.postal else 'U'}{district.number}",
                    area_relation="within",
                    area_id=area,
                    kind="postal_district" if district.postal else "standard",
                )
                values = {
                    "registered": district.registered,
                    "voters": district.voters,
                    "invalid": district.invalid,
                    "valid": district.valid,
                }
                for field, measure in FIGURES.items():
                    if values[field] is not None:
                        sums[field] += values[field] or 0
                        p.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=values[field],
                        )
                mapped: dict[str, int] = {}
                for label, votes in district.votes.items():
                    mapped[canonical(label)] = mapped.get(canonical(label), 0) + votes
                if set(mapped) - set(options):
                    raise ValueError(f"{result.name}: a district votes for a list the city does not print")
                for label, option in options.items():
                    counted[label] = counted.get(label, 0) + mapped.get(label, 0)
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=mapped.get(label, 0),
                    )
            if sums != total or counted != {label: votes for label, votes, _ in result.lists}:
                raise ValueError(f"{result.name} {year}: the districts differ from the printed city totals")
        else:
            unit = key("unit", year, code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NRW,
                scheme_code=SCHEME,
                code=f"{day}:{code}",
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
            for field, measure in FIGURES.items():
                if total[field] is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=total[field],
                    )
            for label, votes, _ in result.lists:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=options[label],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
    if year == 2025:
        p.accept(
            "accounting.turnout",
            "A postal-vote district (Briefwahl) has no roll of its own: the source prints 0 registered voters for each "
            "of them, and the voters with a voting certificate are counted on the rolls of the polling districts "
            "they live in, so a postal district reports voters against no registered voters.",
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/nw-{year}" for year in DAYS)]


def parse() -> None:
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
