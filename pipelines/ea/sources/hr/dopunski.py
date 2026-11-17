"""Croatian supplementary elections of national minority members, 2021 and 2025, by polling station.

On 2021-10-03 and 2025-10-05 the State Electoral Commission ran supplementary elections for the seats that national
minorities hold on county assemblies, the Zagreb City Assembly, and town and municipal councils. Each election day's
zip holds one workbook per county (the county assembly) and one per town or municipality, with one sheet per minority.
The commission's county reports (PDF) print each contest's totals, the votes of every list, the list holders, and the
seats and candidates elected, so the pipeline reads them for the declared figures and the outcomes.
"""

import functools
import re
import subprocess
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.hr import lokalni
from ea.sources.hr.lokalni import (
    CAST,
    INVALID,
    REGISTERED,
    VALID,
    VOTERS,
    VOTES,
    Facts,
    Station,
    abbreviation,
    clean,
    county_title,
    fold,
    izbori_area,
    label,
    muni_title,
    number,
    party_kind,
    place,
    split_names,
    title,
)

SOURCE = "hr.dopunski"
SITE = lokalni.SITE
DATA = f"{SITE}/data/dopunski"
CROATIA = lokalni.CROATIA
ZAGREB = lokalni.ZAGREB
DAYS = {"20211003": date(2021, 10, 3), "20251005": date(2025, 10, 5)}
COUNTIES = {
    "20211003": ["03", "04", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "20", "21"],
    "20251005": ["03", "04", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "20"],
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)


def package_key(day: str) -> str:
    return f"{SOURCE}/{DAYS[day]}"


def zip_url(day: str) -> str:
    return f"{DATA}/{day}/zip/rezultati.zip"


def report_url(day: str, county: str) -> str:
    return f"{DATA}/{day}/rezultati/r_11_{county}_000_000.pdf"


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, zip_url(day), suffix=".zip")
        for county in COUNTIES[day]:
            raw.fetch(SOURCE, report_url(day, county), suffix=".pdf")


# The polling-station workbooks.


@dataclass
class Contest:
    day: str
    county: str
    kind: str  # assembly, council
    place: str  # the folded municipality name, or "" for a county-wide contest
    muni: str
    muni_kind: str
    county_name: str
    minority: str  # the sheet's own words, or "" when the sheet does not name one
    member: str
    header: list[str]
    stations: list[Station]
    report: Report | None = None
    link: list[int | None] = field(default_factory=list)

    @property
    def level(self) -> str:
        return "county" if self.kind == "assembly" else "muni"

    @property
    def totals(self) -> dict[int, int]:
        out: Counter[int] = Counter()
        for s in self.stations:
            out.update(s.counts)
        return dict(out)

    @property
    def sums(self) -> list[int]:
        return [sum(s.votes[i] for s in self.stations) for i in range(len(self.header))]

    @property
    def group(self) -> str:
        return minority_of(self.minority or (self.report.minority if self.report else ""))

    @property
    def ident(self) -> tuple[str, ...]:
        return (self.kind, self.county, "" if self.level == "county" else self.place, self.group)

    @property
    def body(self) -> str:
        """The council or assembly the minority's members sit on, which the local elections' registry holds."""
        return lokalni.key("body", self.kind, self.county, "" if self.level == "county" else self.place)


MINORITIES = {
    "srpsk": "Serb",
    "talijansk": "Italian",
    "madarsk": "Hungarian",
    "madjarsk": "Hungarian",
    "cesk": "Czech",
    "romsk": "Romani",
    "rusinsk": "Rusyn",
    "bosnjack": "Bosniak",
    "slovack": "Slovak",
    "albansk": "Albanian",
    "ukrajinsk": "Ukrainian",
    "slovensk": "Slovene",
    "makedonsk": "Macedonian",
    "njemack": "German",
    "hrvatsk": "Croat",
}


def minority_of(text: str) -> str:
    low = fold(text)
    for stem, english in MINORITIES.items():
        if stem in low:
            return english
    raise ValueError(f"no minority in {text!r}")


def _rows(sheet: fastexcel.ExcelSheet) -> list[list[str]]:
    frame = sheet.to_polars()
    rows = [["" if cell is None else str(cell) for cell in row] for row in frame.rows()]
    return rows[lokalni._start(rows) :]


@functools.cache
def contests(day: str) -> tuple[Contest, ...]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, zip_url(day)).path)
    out: list[Contest] = []
    for member in sorted(archive.namelist()):
        if not member.lower().endswith(".xlsx"):
            continue
        reader = fastexcel.read_excel(archive.read(member))
        for name in reader.sheet_names:
            rows = _rows(reader.load_sheet(name, header_row=None, dtypes="string"))
            header, body = rows[0], [row for row in rows[1:] if any(cell.strip() for cell in row)]
            width = len(header)
            stations: list[Station] = []
            for row in body:
                if len(row) != width:
                    raise ValueError(f"{member}: a row of {len(row)} cells under {width} headings")
                counts = [number(cell) for cell in row[8:13]]
                stations.append(
                    Station(
                        row[0].zfill(2),
                        clean(row[3]),
                        clean(row[2]).lower(),
                        row[4],
                        clean(row[5]),
                        dict(zip((REGISTERED, VOTERS, CAST, VALID, INVALID), counts, strict=True)),
                        [number(cell) for cell in row[13:]],
                    )
                )
            low = name.lower()
            if low.startswith(("žs", "skupština")):
                kind = "assembly"
            elif low.startswith(("gv", "ov")):
                kind = "council"
            else:
                raise ValueError(f"{member}#{name}: of no known kind")
            counties = {s.county for s in stations}
            towns = {place(s.muni) for s in stations}
            if len(counties) != 1 or (kind == "council" and len(towns) != 1):
                raise ValueError(f"{member}#{name}: spans {sorted(counties)} and {len(towns)} towns")
            first = stations[0]
            minority = name.split(" ", 1)[1] if " " in name and not low.startswith("skupština") else ""
            out.append(
                Contest(
                    day,
                    first.county,
                    kind,
                    place(first.muni) if kind == "council" else "",
                    first.muni,
                    first.muni_kind,
                    clean(body[0][1]),
                    minority,
                    f"{member}#{name}",
                    [clean(cell) for cell in header[13:]],
                    stations,
                )
            )
    return tuple(out)


# The county reports.


@dataclass
class Entry:
    name: list[str]  # the lines of the list's name
    votes: int
    holder: str
    seats: int = 0
    elected: list[str] = field(default_factory=list)


@dataclass
class Report:
    url: str
    heading: str
    minority: str
    registered: int
    voters: int
    ballots: int
    valid: int
    invalid: int
    entries: list[Entry]

    @property
    def seats(self) -> int:
        return sum(e.seats for e in self.entries)


NOISE = re.compile(r"Informacija o dopunskim|samouprave iz reda pripadnika|^\s*\d*\s*[A-ZČĆŽŠĐ\- ]*ŽUPANIJA\s*\d*\s*$")
ENTRY = re.compile(r"^\s*(\d+)\.\s+(.+?)\s{2,}([\d.]+)\s+glas\w*\s+[\d,]+\s*%\s*$")
HOLDER = re.compile(r"^\s*Nositelj(?:ica)? liste:\s*(.+?)\s*$|^\s*(.+?)\s+[–-]\s+nositelj(?:ica)?\b.*$", re.IGNORECASE)
SEATS = re.compile(r"^\s*(\d+)\s+mjest\w*\s*$")
NUMBERED = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")


def _figure(lines: list[str], head: str) -> int:
    for line in lines:
        if re.match(rf"\s*{head}", line):
            return number(re.findall(r"\d[\d.]*", line.split(":", 1)[1])[0])
    raise ValueError(f"no {head!r} line")


def _section(url: str, lines: list[str]) -> Report:
    text = " ".join(clean(line) for line in lines)
    heading = clean(text.split("Broj birača")[0])
    stats = [line for line in lines if ":" in line]
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("Pojedine"))
    split = next((i for i, line in enumerate(lines) if "izabrani su" in line), len(lines))
    entries: list[Entry] = []
    for line in lines[start + 1 : split]:
        found = ENTRY.match(line)
        if found:
            entries.append(Entry([clean(found.group(2))], number(found.group(3)), ""))
            continue
        if not line.strip() or not entries:
            continue
        holder = HOLDER.match(line)
        if holder:
            entries[-1].holder = clean(holder.group(1) or holder.group(2))
        elif not entries[-1].holder:
            entries[-1].name.append(clean(line))
    index = -1
    owed = 0
    for line in lines[split + 1 :]:
        if not line.strip():
            continue
        seats = SEATS.match(line)
        if seats:
            entries[index].seats = int(seats.group(1))
            owed = int(seats.group(1))
            continue
        found = NUMBERED.match(line)
        if found and owed:
            entries[index].elected.append(clean(found.group(2)))
            owed -= 1
        elif found:
            index += 1
            first = clean(found.group(2))
            assert fold(entries[index].name[0]).startswith(fold(first)[:20]), (url, first, entries[index].name)
    return Report(
        url,
        heading,
        heading.split("PRIPADNIKA")[-1] if "PRIPADNIKA" in heading else heading,
        _figure(stats, "Broj birača"),
        _figure(stats, "Pristupilo"),
        _figure(stats, "Glasovalo"),
        _figure(stats, "Važećih"),
        _figure(stats, "Nevažećih"),
        entries,
    )


@functools.cache
def reports(day: str, county: str) -> tuple[Report, ...]:
    url = report_url(day, county)
    path = raw.cached(SOURCE, url).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    lines = [line for line in text.replace("\f", "\n").split("\n") if not NOISE.search(line)]
    starts = [i for i, line in enumerate(lines) if "DOPUNSKI IZBOR ČLANOVA" in line]
    ends = [*starts[1:], len(lines)]
    return tuple(_section(url, lines[a:b]) for a, b in zip(starts, ends, strict=True))


def similar(a: str, b: str) -> float:
    return lokalni.similar(a, b)


def attach(day: str) -> list[str]:
    """Give each contest its county report section: the one whose list votes, valid ballots, and minority agree."""
    problems: list[str] = []
    pool: dict[str, list[Report]] = {}
    for county in COUNTIES[day]:
        pool[county] = list(reports(day, county))
    for c in contests(day):
        sums = c.sums
        candidates = [
            r
            for r in pool[c.county]
            if sorted(e.votes for e in r.entries) == sorted(sums)
            and (not c.minority or minority_of(c.minority) == minority_of(r.minority))
            and (c.kind == "assembly") == ("SKUPŠTIN" in r.heading)
        ]
        if len(candidates) > 1:
            # Two bodies with the same counts: the report that names the municipality.
            named = [r for r in candidates if place(c.muni).split()[-1][:5] in fold(r.heading).replace(" ", "")[:400]]
            candidates = named or candidates
        if len(candidates) != 1:
            problems.append(f"{c.member}: {len(candidates)} report sections")
            continue
        report = candidates[0]
        pool[c.county].remove(report)
        c.report = report
        c.link = link(c, report)
    for county, left in pool.items():
        problems += [f"{day} {county}: report section unused: {r.heading}" for r in left]
    return problems


def link(c: Contest, report: Report) -> list[int | None]:
    """For each ballot column of the workbook, the report entry it is."""
    out: list[int | None] = [None] * len(c.header)
    used: set[int] = set()
    sums = c.sums
    order = sorted(range(len(sums)), key=lambda i: -sums[i])
    for column in order:
        options = [i for i, e in enumerate(report.entries) if e.votes == sums[column] and i not in used]
        if options:
            best = max(
                options,
                key=lambda i: similar(c.header[column], " ".join(report.entries[i].name + [report.entries[i].holder])),
            )
            out[column] = best
            used.add(best)
    return out


# Parties.


def party_id(name: str) -> str:
    return key("party", label(name))


def bloc_id(names: list[str]) -> str:
    return key("party", "bloc", *sorted(label(n) for n in names))


def column_names(text: str) -> list[str]:
    return [n for n in split_names(text.split(" - KANDIDACIJSKA")[0]) if party_kind(n)]


def option_party(text: str) -> str | None:
    names = column_names(text)
    if len(names) == 1:
        return party_id(names[0])
    if len(names) > 1:
        return bloc_id(names)
    return None


def holder_of(c: Contest, column: int) -> str:
    index = c.link[column] if c.link else None
    if c.report is None or index is None:
        return ""
    return c.report.entries[index].holder


# The registry.


def system(name: str) -> str:
    return key("electoral_system", name)


METHODS = (
    "Seats reserved for a national minority on a county assembly, the Zagreb City Assembly, or a town or municipal "
    "council are filled in a supplementary election by the minority's voters, who choose among candidate lists. The "
    "seats go to the lists in proportion to their votes (Zakon o lokalnim izborima; Ustavni zakon o pravima "
    "nacionalnih manjina). The commission's county report prints the seats and the candidates each list won."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system, parties, and source of Croatia's supplementary elections of national minority members",
    )
    p.external.add(CROATIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Državno izborno povjerenstvo Republike Hrvatske",
        title="Arhiva izbora: dopunski izbori, rezultati po biračkim mjestima i informacije o dopunskim izborima",
        url=f"{SITE}/",
        license="Official results of the Republic of Croatia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Državno izborno povjerenstvo Republike Hrvatske (DIP), izbori.hr",
    )
    p.add(
        "electoral_system",
        id=system("minority"),
        polity_id=CROATIA,
        valid_from=date(2013, 1, 1),
        rules={"formula": "dhondt"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", system("minority")),
        entity_id=system("minority"),
        kind="methodology",
        body_md=METHODS,
        lang="en",
    )
    spellings: dict[str, Counter[str]] = {}
    blocs: dict[str, list[str]] = {}
    for day in DAYS:
        for c in contests(day):
            for text in c.header:
                names = column_names(text)
                for n in names:
                    spellings.setdefault(party_id(n), Counter())[n] += 1
                if len(names) > 1:
                    blocs[bloc_id(names)] = names
    for id, names in sorted(spellings.items()):
        name = names.most_common(1)[0][0]
        p.add("party", id=id, kind="party", polity_id=CROATIA)
        p.add("external_id", entity_id=id, scheme="hr.dopunski.stranka", value=label(name))
        p.name(id, name, "hr", "official")
        short = abbreviation(name)
        if short:
            p.name(id, short, "hr", "abbreviation", False)
    for id, members in sorted(blocs.items()):
        p.add("party", id=id, kind="bloc", polity_id=CROATIA)
        p.name(id, ", ".join(sorted({clean(m) for m in members})), "hr", "official")
    return p


# One package per election day.

METHOD = (
    "Counted per polling station (biračko mjesto) as published by the State Electoral Commission (Državno izborno "
    "povjerenstvo) in its results archive. Registered voters are the polling station's roll for the minority's "
    "election, voters are those who signed the roll, ballots cast are the ballots found in the box, and valid and "
    "invalid ballots are as printed. A polling station is placed in its town or municipality. The commission's county "
    "report supplies the totals of each contest, which are stored as declared values beside the station counts, and "
    "the seats and candidates each list won."
)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(package_key(day) for day in DAYS)]


def parse() -> None:
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def names_of(c: Contest) -> tuple[str, str, str, str]:
    """English and Croatian names of the contest, with the slug of its place and of the contest itself."""
    zagreb = c.county == ZAGREB
    if c.level == "county":
        who = "Zagreb" if zagreb else county_title(c.county_name)
        place_en = who if zagreb else f"{who} County"
        place_hr = "Grada Zagreba" if zagreb else f"{who} županija"
        where = f"{c.county}-{slug(who)}"
        english = f"{'Zagreb City Assembly' if zagreb else f'{place_en} Assembly'}, {c.group} minority seats"
        native = f"{'Skupština Grada Zagreba' if zagreb else f'Županijska skupština: {place_hr}'}, manjina: {c.group}"
    else:
        who = muni_title(c.muni)
        where = f"{c.county}-{slug(who)}"
        town = c.muni_kind == "grad"
        english = f"{who} {'Town' if town else 'Municipal'} Council, {c.group} minority seats"
        native = f"{'Gradsko' if town else 'Općinsko'} vijeće: {who}, manjina: {c.group}"
    return english, native, where, f"minority-{slug(c.group)}"


def district(c: Contest) -> str:
    if c.level == "county":
        return izbori_area("hr.zupanija", c.county)
    return izbori_area("hr.opcina", f"{c.county}:{c.place}")


def person(c: Contest, name: str) -> str:
    return key("person", c.day, *c.ident, " ".join(sorted(lokalni.words(name))))


def election(day: str) -> Package:
    when = DAYS[day]
    english = f"{when.year} Croatian supplementary local elections for national minority members"
    native = f"Dopunski lokalni izbori {when.year}.: članovi iz reda nacionalnih manjina"
    path = f"hr/{when}-supplementary-local"
    p = Package(
        package_key(day),
        SOURCE,
        f"The {english} by polling station from the State Electoral Commission",
        [f"{SOURCE}/registry"],
    )
    p.external.add(CROATIA)
    p.artifact(raw.cached(SOURCE, zip_url(day)), SOURCE_ID)
    problems = attach(day)
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=CROATIA,
        kind="by_election",
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, native, "hr", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=METHOD, lang="en")
    values, units = Facts(), set()
    cited: set[str] = set()
    here = contests(day)
    for c in here:
        report = c.report
        if report is None:
            raise ValueError(f"{c.member}: no county report section")
        if report.url not in cited:
            cited.add(report.url)
            p.artifact(raw.cached(SOURCE, report.url), SOURCE_ID)
        english_name, native_name, where, tail = names_of(c)
        contest_id = key("contest", day, *c.ident)
        result_set = key("result_set", day, *c.ident)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation",
            body_id=c.body,
            district_area_id=district(c),
            magnitude=report.seats,
            round_number=1,
            electoral_system_id=system("minority"),
            default_result_kind="counted",
        )
        p.name(contest_id, english_name, "en", "official")
        p.name(contest_id, native_name, "hr", "native", False)
        p.add("slug", path=f"{path}/{where}/{tail}", entity_id=contest_id, is_current=True)
        total = c.totals
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=round(min(total[VALID] / report.valid, 1.0), 6) if report.valid else 1.0,
        )
        options: list[str] = []
        for column, name in enumerate(c.header):
            option = key("option", day, *c.ident, column + 1)
            options.append(option)
            party = option_party(name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="list",
                party_id=party,
                ballot_position=column + 1,
                ballot_label=name,
            )
            index = c.link[column]
            if index is None:
                continue
            entry = report.entries[index]
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if entry.seats else "lost",
                seats=entry.seats,
            )
            members = [(entry.holder, "head")] if entry.holder else []
            members += [(n, "list_member") for n in entry.elected if fold(n) != fold(entry.holder)]
            for position, (who_name, role) in enumerate(members, 1):
                who = person(c, who_name)
                p.add("person", id=who)
                p.name(who, title(who_name), "hr", "official")
                candidacy = key("candidacy", day, *c.ident, column + 1, " ".join(sorted(lokalni.words(who_name))))
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=who,
                    option_id=option,
                    role=role,
                    ballot_name=who_name,
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                if any(fold(who_name) == fold(n) for n in entry.elected):
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
        for s in c.stations:
            code = f"{when}:{s.county}:{place(s.muni)}:{s.bm}"
            unit = key("unit", code)
            if code not in units:
                units.add(code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=CROATIA,
                    scheme_code="hr.bm",
                    code=code,
                    area_relation="within",
                    area_id=izbori_area("hr.opcina", f"{s.county}:{place(s.muni)}"),
                    kind="standard",
                )
                p.name(unit, f"{s.name}, {muni_title(s.muni)}" if s.name else muni_title(s.muni), "hr", "official")
            for measure, value in s.counts.items():
                values.add(result_set, unit, None, measure, value)
            for column, value in enumerate(s.votes):
                values.add(result_set, unit, options[column], VOTES, value)
        for measure, value in (
            (REGISTERED, report.registered),
            (VOTERS, report.voters),
            (CAST, report.ballots),
            (VALID, report.valid),
            (INVALID, report.invalid),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for column, index in enumerate(c.link):
            if index is not None:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[column],
                    measure=VOTES,
                    value=report.entries[index].votes,
                )
    p.frame("result_value", values.frame())
    for line in problems:
        print(f"{p.key}: {line}", flush=True)
    _accept(p, list(here))
    return p


def _accept(p: Package, here: list[Contest]) -> None:
    short = [
        f"{names_of(c)[0]} (stations sum to {c.totals[VALID]:,} valid votes and {c.totals[REGISTERED]:,} registered "
        f"voters, the report {c.report.valid:,} and {c.report.registered:,})"
        for c in here
        if c.report is not None
        and (c.totals[VALID], c.totals[REGISTERED], c.totals[INVALID])
        != (c.report.valid, c.report.registered, c.report.invalid)
    ]
    if short:
        p.accept(
            "accounting.declared",
            "The commission's printed contest totals differ from the sum of its own polling-station tables in "
            f"{len(short)} contests: " + "; ".join(short),
        )
