"""Elections of the national minority councils of Serbia, from the Republic Electoral Commission.

Every four years the members of each national council of a national minority are elected, either directly, by the
voters of the minority's special register, or by an electoral assembly of electors. The Commission publishes, for each
direct election, a table of results by polling station and a report of the total results that prints each list's votes
and seats; for an electoral assembly it publishes the decision that allocates the seats.
"""

import functools
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik

SOURCE = "rs.rik_nsnm"
BATCH = "browser://20261117-213731-36bbd0"
SERBIA = rik.SERBIA
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    rik.VOTES,
    rik.REGISTERED,
    rik.CAST,
    rik.VALID,
    rik.INVALID,
    rik.VOTERS,
)


def locate(year: int, *words: str) -> str:
    """The one cached file of an election year whose name holds every word, ignoring case."""
    prefix = f"{BATCH}/{year}/"
    found = [k for k in raw.keys(SOURCE) if k.startswith(prefix) and all(w.lower() in k.lower() for w in words)]
    if len(found) != 1:
        raise LookupError(f"{year} {words}: {len(found)} cached files match: {found[:4]}")
    return found[0]


# reading -----------------------------------------------------------------------------------------


def clean(text: object) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


@functools.cache
def text(url: str) -> str:
    """The text of a cached report or decision, converted from the Word or PDF file the Commission posted."""
    path = raw.cached(SOURCE, url).path
    if path.suffix.lower() == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
        return out.stdout.decode("utf-8", "replace")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office is None:
        raise RuntimeError(f"LibreOffice is needed to read {url}")
    with tempfile.TemporaryDirectory() as work:
        folder = Path(work)
        copy = folder / f"source{path.suffix}"
        shutil.copy(path, copy)
        subprocess.run(
            [
                office,
                "--headless",
                "--convert-to",
                "txt:Text (encoded):UTF8",
                "--outdir",
                str(folder / "out"),
                str(copy),
            ],
            capture_output=True,
            check=True,
            cwd=folder,
        )
        return (folder / "out" / "source.txt").read_text(encoding="utf-8-sig")


NUMBER = re.compile(r"^\d{1,3}(?:\.\d{3})*$|^\d+$")


def _number(token: str) -> int:
    return int(token.replace(".", ""))


@dataclass
class Report:
    totals: dict[str, int]  # registered, stations, voters, received, unused, used, invalid, valid
    lists: list[tuple[int, str, int, int | None]]  # ballot number, name, votes, seats


TOTALS = ("registered", "stations", "voters", "received", "unused", "used", "invalid", "valid")


def report(url: str) -> Report:
    """The totals and the votes and seats of each list from a report of the total results of a direct election."""
    lines = [clean(line) for line in text(url).splitlines()]
    lines = [line for line in lines if line]
    totals: dict[str, int] = {}
    start = next(i for i, line in enumerate(lines) if re.match(r"^9\.\s", line))
    for i, line in enumerate(lines[:start]):
        m = re.match(r"^([1-8])\.\s", line)
        if m and i + 1 < len(lines) and NUMBER.match(lines[i + 1]):
            totals[TOTALS[int(m.group(1)) - 1]] = _number(lines[i + 1])
    if len(totals) != len(TOTALS):
        raise ValueError(f"{url}: the report's totals are {totals}")
    return Report(totals, _rows(url, lines[start + 1 :]))


def _rows(url: str, lines: list[str]) -> list[tuple[int, str, int, int | None]]:
    """The rows of a table of lists: the ballot number, the name, the votes and the seats, on lines of their own."""
    lists: list[tuple[int, str, int, int | None]] = []
    rank: int | None = None
    names: list[str] = []
    numbers: list[int] = []

    def close() -> None:
        if rank is None:
            return
        if len(numbers) < 2:
            raise ValueError(f"{url}: list {rank} has figures {numbers}")
        lists.append((rank, " / ".join(names), numbers[-2], numbers[-1]))

    for line in lines:
        if re.match(r"^(?:II|Овај извештај|Ова одлука|Ово решење)\b", line):
            break
        m = re.match(r"^(\d+)\.$", line)
        if m and (rank is None or (len(numbers) >= 2 and int(m.group(1)) == rank + 1)):
            close()
            rank, names, numbers = int(m.group(1)), [], []
        elif rank is not None:
            if NUMBER.match(line) and (names or numbers):
                numbers.append(_number(line))
            elif not numbers:
                names.append(line)
    close()
    return lists


PRESENT = 20  # the measure for the members present at an assembly vote
ASSEMBLY = (
    (PRESENT, r"присуствовали"),
    (VOTERS, r"преузели гласачки листић"),
    (CAST, r"налазили у гласачкој кутији"),
    (INVALID, r"Број неважећих"),
    (VALID, r"Број важећих"),
)


def assembly_vote(url: str) -> tuple[dict[int, int], list[tuple[int, str, int, int | None]]]:
    """The figures and the votes and seats of each list from a decision on the results of an electoral assembly."""
    body = text(url)
    figures: dict[int, int] = {}
    for measure, words in ASSEMBLY:
        m = re.search(rf"{words}[^:\n]*:\s*([\d.]+)\s*;", body)
        if m is None:
            raise ValueError(f"{url}: no figure for {words}")
        figures[measure] = _number(m.group(1))
    rows: list[tuple[int, str, int, int | None]] = []
    for line in body.splitlines():  # the Statistical Office's table prints each list on a line of its own
        m = re.match(r"^\s*(\d+)\.\s+(\S.*?)\s{2,}(\d+)\s+(\d+)\s*$", line)
        if m:
            rows.append((int(m.group(1)), clean(m.group(2)), int(m.group(3)), int(m.group(4))))
    if not rows:
        lines = [clean(line) for line in body.splitlines()]
        start = next(i for i, line in enumerate(lines) if re.match(r"^6\.\s", line))
        rows = _rows(url, [line for line in lines[start + 1 :] if line])
    return figures, sorted(rows)


@dataclass
class Station:
    okrug: str
    opstina: str
    opstina_name: str
    number: int
    figures: dict[int, int]  # measure -> value
    lists: list[int]


def _int(value: object) -> int:
    return 0 if value is None or str(value).strip() == "" else int(float(str(value)))


def stations(url: str) -> tuple[list[Station], list[str]]:
    """The polling-station table of a direct election, and the names of its lists in ballot order."""
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    rows = [list(r) for r in book.load_sheet_by_name(book.sheet_names[0], header_row=None).to_polars().iter_rows()]
    head = next(i for i, r in enumerate(rows) if clean(r[0]).startswith("Округ"))
    labels = [clean(c) for c in rows[head]]
    first = 5
    if labels[5].startswith("Примљени"):  # the 2014 tables put the ballots before the voters
        order = {REGISTERED: 7, VOTERS: 8, CAST: 9, INVALID: 10, VALID: 11}
    else:
        order = {REGISTERED: 5, VOTERS: 6, CAST: 9, INVALID: 10, VALID: 11}
    first = 12
    names = [clean(c) for c in rows[head][first:] if clean(c)]
    out: list[Station] = []
    for r in rows[head + 1 :]:
        if not clean(r[2]):
            continue
        out.append(
            Station(
                okrug=clean(r[0]),
                opstina=clean(r[2]),
                opstina_name=clean(r[3]),
                number=_int(r[4]),
                figures={m: _int(r[c]) for m, c in order.items()},
                lists=[_int(r[first + i]) for i in range(len(names))],
            )
        )
    return out, names


# the councils ------------------------------------------------------------------------------------

# slug, English adjective, Serbian (Cyrillic) adjective in the genitive feminine as the Commission prints it
COUNCILS: dict[str, tuple[str, str]] = {
    "albanian": ("Albanian", "албанске"),
    "ashkali": ("Ashkali", "ашкалијске"),
    "bosniak": ("Bosniak", "бошњачке"),
    "bulgarian": ("Bulgarian", "бугарске"),
    "bunjevac": ("Bunjevac", "буњевачке"),
    "croat": ("Croat", "хрватске"),
    "czech": ("Czech", "чешке"),
    "egyptian": ("Egyptian", "египатске"),
    "goran": ("Goran", "горанске"),
    "greek": ("Greek", "грчке"),
    "hungarian": ("Hungarian", "мађарске"),
    "macedonian": ("Macedonian", "македонске"),
    "german": ("German", "немачке"),
    "montenegrin": ("Montenegrin", "црногорске"),
    "polish": ("Polish", "пољске"),
    "roma": ("Roma", "ромске"),
    "romanian": ("Romanian", "румунске"),
    "russian": ("Russian", "руске"),
    "rusyn": ("Rusyn", "русинске"),
    "slovak": ("Slovak", "словачке"),
    "slovene": ("Slovene", "словеначке"),
    "ukrainian": ("Ukrainian", "украјинске"),
    "vlach": ("Vlach", "влашке"),
}

# How each direct election's files are named in the cache: the polling-station table and the report of total results.
# The 2014 files name the minority in the Serbian plural, the 2018 table names the council's adjective.
PLURAL = {
    "albanian": "Albanci",
    "ashkali": "Askalije",
    "bosniak": "Bosnjaci",
    "bulgarian": "Bugari",
    "bunjevac": "Bunjevci",
    "czech": "Cesi",
    "egyptian": "Egipcani",
    "greek": "Grci",
    "hungarian": "Madjari",
    "german": "Nemci",
    "polish": "Poljaci",
    "roma": "Romi",
    "romanian": "Rumuni",
    "rusyn": "Rusini",
    "slovak": "Slovaci",
    "slovene": "Slovenci",
    "ukrainian": "Ukrajinci",
    "vlach": "Vlasi",
}
ADJECTIVE_2018 = {
    "albanian": "Albanski",
    "ashkali": "Askalijski",
    "bosniak": "Bosnjacki",
    "bulgarian": "Bugarski",
    "bunjevac": "Bunjevacki",
    "egyptian": "Egipatski",
    "greek": "Grcki",
    "hungarian": "Madjarski",
    "german": "Nemacki",
    "polish": "Poljski",
    "roma": "Romski",
    "rusyn": "Rusinski",
    "slovak": "Slovacki",
    "slovene": "Slovenacki",
    "ukrainian": "Ukrajinski",
    "vlach": "Vlaski",
}


def table_url(year: int, slug: str) -> str:
    if year == 2014:
        return locate(2014, "Tabela_3", PLURAL[slug])
    if slug in ("czech", "romanian"):
        return locate(2018, "Tabela po BM", PLURAL[slug])
    return locate(2018, ADJECTIVE_2018[slug], "nacionallni savet BM")


def report_url(year: int, slug: str) -> str:
    if year == 2014:
        return locate(2014, f"2. {PLURAL[slug].upper()} ", "izvestaj")
    return locate(2018, "Izvestaj", PLURAL[slug])


def pair(printed: list[int], summed: list[int]) -> list[int]:
    """For each column of the polling-station table, the index of the list the report prints for it.

    The 2014 reports order the lists by their votes and the tables by ballot number, so the lists are matched by
    rank; the votes the stations sum to are close to the printed ones, never far enough to swap two lists.
    """
    ranked = sorted(range(len(summed)), key=lambda i: (-summed[i], i))
    printed_rank = sorted(range(len(printed)), key=lambda i: (-printed[i], i))
    out = [0] * len(summed)
    for column, row in zip(ranked, printed_rank, strict=True):
        out[column] = row
    return out


# packages ----------------------------------------------------------------------------------------


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM = {"direct": key("electoral_system", "direct"), "assembly": key("electoral_system", "assembly")}
UNIT_SCHEME = "rs.nsnm"
STATION_SCHEME = "rs.biracko_mesto"
METHOD_DIRECT = (
    "Counted by the polling board at each polling station and reported to the Republic Electoral Commission, which "
    "published the table of results by polling station and a report of the total results. Registered voters are the "
    "minority's special voter register extract for the station, voters are those who voted, ballots cast are the "
    "ballots used, and valid votes are the valid ballots for the lists. The ballots received and left unused at each "
    "station are published and not stored here. A polling station carries the municipality's code and its number "
    "within the municipality; the council's elections use the same station numbers as one another on one day. Seats "
    "are as the Commission declared them for each list in its report of the total results."
)
METHOD_ASSEMBLY = (
    "The members of the council are elected by the electors of the minority's electoral assembly, who vote for lists "
    "of candidates. The committee that conducts the election declared the electors present, the ballots, and each "
    "list's votes and seats in its decision on the results of the vote and its decision allocating the seats."
)


def council(slug: str) -> str:
    return key("body", slug)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The national councils of the national minorities of Serbia and the electoral systems of their elections",
    )
    p.external.add(SERBIA)
    p.external.add(STATION_SCHEME)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Републичка изборна комисија",
        title="Elections of the members of the national councils of national minorities, Republic Electoral "
        "Commission of Serbia",
        url=rik.BASE,
        license=rik.LICENSE,
        license_url="https://creativecommons.org/licenses/by-nc-nd/3.0/rs/",
        attribution="Републичка изборна комисија, rik.parlament.gov.rs",
    )
    p.add("scheme", code=UNIT_SCHEME, polity_id=SERBIA, kind="synthetic", rank=1, noun="electorate", code_system=None)
    for slug, (english, native) in COUNCILS.items():
        body = council(slug)
        p.add("body", id=body, polity_id=SERBIA, kind="council")
        p.name(body, f"National Council of the {english} National Minority", "en", "official")
        p.name(body, f"Национални савет {native} националне мањине", "sr", "native", False)
    rules = {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}}
    systems = (
        (
            "direct",
            (
                "The members of a national council are elected directly, by the voters on the minority's special "
                "voter register, from closed lists in one constituency, and the seats go to the lists by the D'Hondt "
                "method. The number of members, 15 to 35, depends on the minority's share of the population."
            ),
        ),
        (
            "assembly",
            (
                "A minority that holds its election by electoral assembly elects electors, who elect the members of "
                "the council from closed lists of candidates in one constituency, and the seats go to the lists by "
                "the D'Hondt method."
            ),
        ),
    )
    for name, method in systems:
        p.add(
            "electoral_system",
            id=SYSTEM[name],
            polity_id=SERBIA,
            valid_from=None,
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add(
            "note", id=key("note", SYSTEM[name]), entity_id=SYSTEM[name], kind="methodology", body_md=method, lang="en"
        )
    return p


@dataclass
class Contest:
    slug: str
    mode: str  # direct, assembly
    labels: list[str]  # the lists in ballot order
    seats: list[int | None]  # as declared, in ballot order
    files: list[str]  # the cached files the figures come from
    stations: list[Station] | None = None  # the table by polling station, for a direct election with one
    national: dict[int, int] | None = None  # the figures the Commission prints for the whole electorate
    national_lists: list[int | None] | None = None
    declared: dict[int, int] | None = None  # the printed totals, to compare the stations with
    declared_lists: list[int | None] | None = None
    registered_note: str | None = None
    scan: str | None = None  # the scanned file the figures were read from
    locators: list[tuple[int | None, int, list[float]]] | None = None  # (list index, measure, [page, x0, y0, x1, y1])


@dataclass(frozen=True)
class Edition:
    day: date
    last: date  # the day of the last repeated vote, when polling was repeated
    english: str


EDITIONS = {
    2014: Edition(date(2014, 10, 26), date(2014, 11, 2), "2014 national minority council elections"),
    2018: Edition(date(2018, 11, 4), date(2018, 11, 14), "2018 national minority council elections"),
    2022: Edition(date(2022, 11, 13), date(2022, 11, 13), "2022 national minority council elections"),
    2026: Edition(date(2026, 11, 8), date(2026, 11, 8), "2026 national minority council elections"),
}


def direct(year: int, slug: str) -> Contest:
    rows, names = stations(table_url(year, slug))
    printed = report(report_url(year, slug))
    summed = [sum(s.lists[i] for s in rows) for i in range(len(names))]
    if year == 2018:
        order = list(range(len(names)))
    else:
        order = pair([v for _, _, v, _ in printed.lists], summed)
    if len(printed.lists) != len(names):
        raise ValueError(f"{year} {slug}: {len(names)} lists in the table and {len(printed.lists)} in the report")
    declared = {
        REGISTERED: printed.totals["registered"],
        VOTERS: printed.totals["voters"],
        CAST: printed.totals["used"],
        INVALID: printed.totals["invalid"],
        VALID: printed.totals["valid"],
    }
    return Contest(
        slug=slug,
        mode="direct",
        labels=names,
        seats=[printed.lists[order[i]][3] for i in range(len(names))],
        files=[table_url(year, slug), report_url(year, slug)],
        stations=rows,
        declared=declared,
        declared_lists=[printed.lists[order[i]][2] for i in range(len(names))],
    )


def _sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "999", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def _unit_name(s: Station) -> str:
    return f"{s.opstina_name}, бирачко место {s.number}"


def build(year: int, contests: list[Contest]) -> Package:
    e = EDITIONS[year]
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"The {year} elections of the national councils of the national minorities of Serbia",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(SERBIA)
    p.external.add(STATION_SCHEME)
    election = key("election", e.day)
    path = f"rs/{e.day}-minority-councils"
    p.add(
        "election",
        id=election,
        polity_id=SERBIA,
        kind="general",
        date_from=e.day,
        date_to=e.last,
        date_precision="day",
        status="held",
    )
    p.name(election, e.english, "en", "official")
    p.add("slug", path=path, entity_id=election, is_current=True)
    units: dict[tuple[str, int], str] = {}
    odd: list[str] = []
    cited: dict[str, str] = {}
    pages: dict[tuple[str, int], str] = {}
    for c in contests:
        for url in c.files:
            if url not in cited:
                cited[url] = p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
        english, _ = COUNCILS[c.slug]
        contest = key("contest", e.day, c.slug)
        total = sum(s or 0 for s in c.seats)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=council(c.slug),
            magnitude=total or None,
            round_number=1,
            electoral_system_id=SYSTEM[c.mode],
            default_result_kind="counted",
        )
        p.name(contest, f"National Council of the {english} National Minority, {year}", "en", "official")
        p.add("slug", path=f"{path}/{c.slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", e.day, c.slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        method = METHOD_DIRECT if c.mode == "direct" and c.stations else METHOD_ASSEMBLY if c.mode == "assembly" else ""
        if c.mode == "direct" and not c.stations:
            method = (
                "Counted by the polling boards of the polling stations and reported to the Republic Electoral "
                "Commission, which published the total results of the election in a report: the voters on the "
                "minority's special register, the voters who voted, the ballots used, the invalid and valid "
                "ballots, and each list's votes and seats. The Commission's polling-station records for this "
                "election are not stored here, so every figure is for the whole electorate. The Commission "
                "posted the report as a scan, so each figure was read from the page image by optical character "
                "recognition and checked against the report's own arithmetic (ballots used equal valid plus "
                "invalid, and the lists' votes add up to the valid votes); a digit the check or a comparison "
                "with the image showed to be misread was corrected, and each figure keeps its page and box. "
                "Voters are as printed, and can exceed the ballots used. Ballots received and unused, and the "
                "number of polling stations, are published and not stored here."
            )
        if c.registered_note:
            method += " " + c.registered_note
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: list[str] = []
        for i, label in enumerate(c.labels, 1):
            option = key("option", e.day, c.slug, i)
            options.append(option)
            p.add("contest_option", id=option, contest_id=contest, kind="list", ballot_position=i, ballot_label=label)
            if c.seats[i - 1]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=c.seats[i - 1])
        rows: list[tuple[str, str | None, int, int]] = []
        if c.stations:
            for s in c.stations:
                code = f"{e.day}:{s.opstina}:{s.number}"
                unit = units.get((s.opstina, s.number))
                if unit is None:
                    unit = units[(s.opstina, s.number)] = key("unit", code)
                    p.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=SERBIA,
                        scheme_code=STATION_SCHEME,
                        code=code,
                        area_relation="unlocated",
                        area_id=None,
                        kind="standard",
                    )
                    p.name(unit, _unit_name(s), "sr", "official")
                for measure, value in s.figures.items():
                    rows.append((unit, None, measure, value))
                for i, votes in enumerate(s.lists):
                    rows.append((unit, options[i], VOTES, votes))
            if c.declared:
                for measure, value in c.declared.items():
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for i, value in enumerate(c.declared_lists or []):
                if value is not None:
                    p.add("declared", result_set_id=result_set, option_id=options[i], measure=VOTES, value=value)
            odd += _difference(year, c)
        else:
            unit = key("unit", e.day, c.slug)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=SERBIA,
                scheme_code=UNIT_SCHEME,
                code=f"{e.day}:{c.slug}",
                area_relation="unlocated",
                area_id=None,
                kind="aggregate",
            )
            p.name(
                unit,
                f"{english} national minority, all polling stations"
                if c.mode == "direct"
                else f"Electoral assembly of the {english} national minority",
                "en",
                "official",
            )
            for measure, value in (c.national or {}).items():
                rows.append((unit, None, measure, value))
            for i, votes in enumerate(c.national_lists or []):
                if votes is not None:
                    rows.append((unit, options[i], VOTES, votes))
        if c.scan and c.locators:
            stored = raw.cached(SOURCE, c.scan)
            sizes = _sizes(stored.path)
            places = []
            for index, measure, (number, x0, y0, x1, y1) in c.locators:
                page = pages.get((c.scan, int(number)))
                if page is None:
                    page = pages[(c.scan, int(number))] = p.id("page", stored.sha256, int(number))
                    width, height = sizes[int(number)]
                    p.add(
                        "source_page",
                        id=page,
                        artifact_id=cited[c.scan],
                        page_number=int(number),
                        image_key=f"pages/{SOURCE}/{stored.sha256}-{int(number)}.png",
                        width=round(width * 100 / 72),
                        height=round(height * 100 / 72),
                    )
                places.append((unit, None if index is None else options[index], measure, page, x0, y0, x1, y1))
            p.frame(
                "value_locator",
                pl.DataFrame(
                    places,
                    schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                    orient="row",
                    schema_overrides={"option_id": pl.String},
                ).with_columns(
                    result_set_id=pl.lit(result_set),
                    channel=pl.lit(0, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    source_id=pl.lit(None, pl.String),
                    measure=pl.col("measure").cast(pl.Int16),
                ),
            )
        p.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": [result_set] * len(rows),
                    "unit_id": [r[0] for r in rows],
                    "option_id": [r[1] for r in rows],
                    "channel": [0] * len(rows),
                    "measure": [r[2] for r in rows],
                    "count_round": [0] * len(rows),
                    "flags": [0] * len(rows),
                    "value": [r[3] for r in rows],
                },
                schema_overrides={"option_id": pl.String},
            ),
        )
    if odd:
        p.accept(
            "accounting.declared",
            "The Commission's printed totals differ from the sum of its polling-station rows (station sum against "
            "printed): " + "; ".join(odd),
        )
    return p


NAMES = {REGISTERED: "registered", VOTERS: "voters", CAST: "ballots used", INVALID: "invalid", VALID: "valid"}


def _difference(year: int, c: Contest) -> list[str]:
    """How a direct election's station rows add up against the totals the Commission printed for it."""
    assert c.stations is not None
    off = []
    for measure, printed in (c.declared or {}).items():
        got = sum(s.figures[measure] for s in c.stations)
        if got != printed:
            off.append(f"{c.slug} {year} {NAMES[measure]} {got:,} against {printed:,}")
    for i, printed in enumerate(c.declared_lists or []):
        got = sum(s.lists[i] for s in c.stations)
        if printed is not None and got != printed:
            off.append(f"{c.slug} {year} list {i + 1} {got:,} against {printed:,}")
    return off


SCANS = Path(__file__).with_name("rik_nsnm_scans.json")
SCANNED_TOTALS = {"registered": REGISTERED, "voters": VOTERS, "used": CAST, "invalid": INVALID, "valid": VALID}


@functools.cache
def scans() -> dict[str, dict]:
    return json.loads(SCANS.read_text())


def scanned(year: int, slug: str) -> Contest:
    """A direct election whose report of total results is a scan: the figures are for the whole electorate."""
    row = scans()[f"{year}/{slug}"]
    locators: list[tuple[int | None, int, list[float]]] = []
    national: dict[int, int] = {}
    for name, measure in SCANNED_TOTALS.items():
        value, where = row["totals"][name]
        national[measure] = value
        locators.append((None, measure, where))
    labels, seats, votes = [], [], []
    for i, entry in enumerate(row["lists"]):
        labels.append(entry["name"])
        seats.append(entry["seats"][0] if entry["seats"] else None)
        votes.append(entry["votes"][0])
        locators.append((i, VOTES, entry["votes"][1]))
    return Contest(
        slug=slug,
        mode="direct",
        labels=labels,
        seats=seats,
        files=[row["file"]],
        national=national,
        national_lists=votes,
        scan=row["file"],
        locators=locators,
    )


ASSEMBLIES: dict[int, dict[str, tuple[str, ...]]] = {
    2014: {
        "croat": ("Resenje o rezultatima izbora", "HRVATI"),
        "macedonian": ("Resenje o rezultatima izbora", "MAKEDONCI"),
        "montenegrin": ("graficki prikaz", "crnogorci"),
    },
    2018: {
        "croat": ("Resenje-o-rezultatima", "HRVATI"),
        "macedonian": ("Resenje-o-rezultatima", "MAKEDONCI"),
        "montenegrin": ("Resenje-o-rezultatima", "CRNOGORCI"),
        "russian": ("Resenje-o-rezultatima", "RUSI"),
    },
}


def assembly(year: int, slug: str) -> Contest:
    url = locate(year, *ASSEMBLIES[year][slug])
    figures, rows = assembly_vote(url)
    return Contest(
        slug=slug,
        mode="assembly",
        labels=[name for _, name, _, _ in rows],
        seats=[seats for _, _, _, seats in rows],
        files=[url],
        national=figures,
        national_lists=[votes for _, _, votes, _ in rows],
    )


def contests(year: int) -> list[Contest]:
    out = []
    if year in (2014, 2018):
        out += [direct(year, slug) for slug in PLURAL if not (year == 2014 and slug == "polish")]
    out += [assembly(year, slug) for slug in ASSEMBLIES.get(year, {})]
    out += [scanned(year, key.split("/")[1]) for key in scans() if key.startswith(f"{year}/")]
    return sorted(out, key=lambda c: c.slug)


YEARS = (2014, 2018, 2022, 2026)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{EDITIONS[y].day}" for y in YEARS)]


def parse() -> None:
    registry().write()
    for year in YEARS:
        build(year, contests(year)).write()


def fetch() -> None:
    for url in sorted(raw.keys(SOURCE)):
        raw.cached(SOURCE, url)


def emit() -> None:
    package.run("load", packages())
