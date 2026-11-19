"""Greenland's elections before 2005 from Statistics Greenland's StatBank.

Three tables hold every election from the start of home rule: SAXLANST (the Inatsisartut, called the Landsting until
2009), SAXFOLK (the two seats Greenland sends to the Danish Folketing) and SAXKOMST (the municipal councils). Each lists,
for an election date and a place, the votes of every party, the individual candidates, the valid, invalid and blank
ballots, the ballots counted and the registered voters. The places are the 18 municipalities that existed until the end
of 2008 and, up to 1995, the three districts that group them (the Sydkredsen, Midtkredsen and Diskokredsen); the table
also carries empty rows for the municipalities created in 2009 and 2018. A dash marks a figure that does not exist and
two dots a figure that is not available.

This pipeline loads the elections the later valg.gl pipeline does not cover: the Inatsisartut from 1979 to 2002, the
Folketing from 1984 to 2005 and the municipal councils from 1979 to 2001. The tables print figures as the authority
holds them, and several do not add up (see `NOTES`); they are stored as printed and the findings accepted.
"""

import csv
import functools
import io
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.gl import kommune_pre2009

SOURCE = "gl.statbank"
API = "https://bank.stat.gl/api/v1/en/Greenland/SA/SA99/SA30"
YEARBOOK = "http://stat.gl/publ/da/SA/201001/oversigt/Valg.htm"
BODY: dict[str, object] = {"query": [], "response": {"format": "csv"}}
GREENLAND = str(uuid5(package.NAMESPACE, "polity\x1fGL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRL"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
MONTHS = {
    m: n for n, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}
SCHEME = "gl.kommune_pre2009"
OLD_MUNICIPALITIES_END = date(2009, 1, 1)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
YEARBOOK_ID = key("source", "yearbook")
INATSISARTUT = key("body", "inatsisartut")
FOLKETING = key("body", "folketing")

FAMILIES = {
    "inatsisartut": ("SAXLANST", "{year} Greenlandic parliamentary election", "Landstingsvalg {year}"),
    "folketing": ("SAXFOLK", "{year} Danish general election in Greenland", "Folketingsvalg {year}"),
    "local": ("SAXKOMST", "{year} Greenlandic municipal elections", "Kommunevalg {year}"),
}
# The elections the valg.gl pipeline does not hold: those before the first one on that site in each family.
LAST = {"inatsisartut": 2002, "folketing": 2005, "local": 2001}

# The table's column headings, each mapped to the party it stands for. A party's name differs between the tables, and
# the municipal table prints the candidate associations' list twice, under "Kandidat forbund 1" and under
# "Kattusseqatigiit Partiiat", with the same national total.
PARTIES: dict[str, tuple[str, str]] = {
    "Akullit Partiiat": ("akulliit", "Akulliit Partiiat"),
    "Akulliit Partiiat": ("akulliit", "Akulliit Partiiat"),
    "Arnat Partiiat": ("arnat", "Arnat Partiiat"),
    "Atassut": ("atassut", "Atassut"),
    "Demokraterne": ("demokraterne", "Demokraterne"),
    "Inuit Ataqatigiit": ("ia", "Inuit Ataqatigiit"),
    "Issittup Partiia": ("issittup", "Issittup Partiia"),
    "Siumut": ("siumut", "Siumut"),
    "Sulisartut Partiiat": ("sulisartut", "Sulisartut Partiiat"),
    "Sulissartut Partiat": ("sulisartut", "Sulisartut Partiiat"),
    "Kandidatforbundets Parti": ("kandidatforbund", "Kandidatforbundets Parti"),
    "Kattusseqatgiit partiiat": ("kandidatforbund", "Kandidatforbundets Parti"),
    "Kattusseqatigiit Partiiat": ("kandidatforbund", "Kandidatforbundets Parti"),
    "Kandidat forbund 1": ("kandidatforbund", "Kandidatforbundets Parti"),
    "Sorlaat Partiiat": ("sorlaat", "Sorlaat Partiiat"),
    "Sorlaat Partiaat": ("sorlaat", "Sorlaat Partiiat"),
    "Parti Inuit": ("inuit", "Parti Inuit"),
    "Partii Inuit": ("inuit", "Parti Inuit"),
    "Naleraq (Partii Naleraq)": ("naleraq", "Naleraq"),
    "Partii Naleraq": ("naleraq", "Naleraq"),
    "Nunatta Qitornai": ("nunatta", "Nunatta Qitornai"),
    "Samarbejdspartiet": ("samarbejds", "Samarbejdspartiet"),
    "Kandidatforbund (Siumut/IA)": ("kandidatforbund_siumut_ia", "Kandidatforbund (Siumut/IA)"),
    "Kandidatforbund allat katillugit": ("kandidatforbund_others", "Kandidatforbund allat katillugit"),
}
OTHERS = {"Øvrige stemmer (Individual candidates)", "Individual candidates"}
ACCOUNTING = {
    "Valid votes in all": VALID,
    "Sum of Voids votes": INVALID,
    "Sum of Blank votes": BLANK,
    "Tallied Votes": BALLOTS,
    "Eligible Votes": REGISTERED,
}
NOTE_VOIDS = (
    'The table\'s count of void ballots ("Sum of Voids votes") is stored as invalid ballots. Before 2002 it has no '
    "separate count of blank ballots, so it may include them."
)
METHOD = (
    "Counted by the municipalities and published by Statistics Greenland in its StatBank tables of election results. "
    "The tables give each municipality's votes for every party and for individual candidates, its valid and void "
    "ballots, the ballots counted and the registered voters. They print no seats, candidates or polling places."
)


@dataclass(frozen=True)
class Election:
    family: str
    day: date

    @property
    def stamp(self) -> str:
        return f"{self.day.isoformat()}-{self.family}"

    @property
    def table(self) -> str:
        return FAMILIES[self.family][0]


@dataclass(frozen=True)
class Row:
    day: date
    place: str
    figures: dict[str, int | None]  # the table's headings and what it prints under each; None marks a dash or dots


def url(table: str) -> str:
    return f"{API}/{table}.px"


def fetch() -> None:
    for table, _, _ in FAMILIES.values():
        raw.fetch(SOURCE, url(table), suffix=".csv", body=BODY)
    raw.fetch(SOURCE, YEARBOOK, suffix=".htm")
    kommune_pre2009.fetch()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.stamp}" for e in elections())]


def parse() -> None:
    geo().write()
    registry().write()
    for e in elections():
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def figure(text: str) -> int | None:
    text = text.strip()
    return None if text in ("-", "..", "...", "") else int(text)


def day_of(text: str) -> date:
    d, month, year = text.strip().split("-")
    two = int(year)
    return date(1900 + two if two >= 50 else 2000 + two, MONTHS[month.lower()], int(d))


@functools.cache
def rows(table: str) -> tuple[Row, ...]:
    text = raw.cached(SOURCE, url(table), BODY).path.read_bytes().decode("cp1252")
    reader = csv.reader(io.StringIO(text))
    heading = next(reader)
    out = []
    for line in reader:
        out.append(Row(day_of(line[0]), line[1], {h: figure(v) for h, v in zip(heading[2:], line[2:], strict=True)}))
    return tuple(out)


@functools.cache
def elections() -> list[Election]:
    found = []
    for family, (table, _, _) in FAMILIES.items():
        for day in sorted({r.day for r in rows(table)}):
            if day.year <= LAST[family]:
                found.append(Election(family, day))
    return sorted(found, key=lambda e: (e.day, e.family))


NATIONAL = "Greenland, total"
OLD = [
    "Nanortalik",
    "Qaqortoq",
    "Narsaq",
    "Ivittuut",
    "Paamiut",
    "Nuuk",
    "Maniitsoq",
    "Sisimiut",
    "Kangaatsiaq",
    "Aasiaat",
    "Qasigiannguit",
    "Ilulissat",
    "Qeqertarsuaq",
    "Uummannaq",
    "Upernavik",
    "Qaanaaq",
    "Tasiilaq",
    "Ittoqqortoormiit",
]


def slug(text: str) -> str:
    return changeset.slug(text)


def area(name: str) -> str:
    return key("area", SCHEME, slug(name))


def council(name: str) -> str:
    return key("body", "council", slug(name))


def party_key(code: str) -> str:
    return key("party", code)


def collapse(figures: dict[str, int | None]) -> tuple[dict[str, int], int | None]:
    """A row's party votes by party code, and its individual candidates' votes. A party printed under two headings
    counts once; two different figures under them would be a conflict."""
    votes: dict[str, int] = {}
    others: int | None = None
    for heading, value in figures.items():
        if value is None:
            continue
        if heading in OTHERS:
            others = value
        elif heading in PARTIES:
            code = PARTIES[heading][0]
            if votes.setdefault(code, value) != value:
                raise ValueError(f"{heading}: {value} conflicts with {votes[code]} for the same party")
        elif heading not in ACCOUNTING:
            raise ValueError(f"unknown column: {heading}")
    return votes, others


def places(e: Election) -> dict[str, Row]:
    """The municipality rows of an election that print any result, and its national row."""
    found = {r.place: r for r in rows(e.table) if r.day == e.day}
    return {
        name: found[name]
        for name in [NATIONAL, *OLD]
        if name in found and found[name].figures["Tallied Votes"] is not None
    }


def accounting(row: Row) -> dict[int, int]:
    return {measure: v for heading, measure in ACCOUNTING.items() if (v := row.figures.get(heading)) is not None}


# Packages.


def source_rows(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Statistics Greenland (Kalaallit Nunaanni Naatsorsueqqissaartarfik)",
        title="StatBank Greenland: results of the Inatsisartut, Folketing and municipal elections",
        url="https://bank.stat.gl/",
        license="Official statistics of Greenland, published openly; reuse with the source acknowledged",
        license_url="https://bank.stat.gl/",
        attribution="Statistics Greenland, StatBank Greenland",
    )
    p.add(
        "source",
        id=YEARBOOK_ID,
        kind="official_body",
        publisher="Statistics Greenland (Kalaallit Nunaanni Naatsorsueqqissaartarfik)",
        title="Greenland in Figures and Statistical Yearbook 2010: elections",
        url=YEARBOOK,
        license="Official statistics of Greenland, published openly; reuse with the source acknowledged",
        license_url="https://stat.gl/",
        attribution="Statistics Greenland, Statistical Yearbook 2010",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo", SOURCE, "Greenland's 18 municipalities of 1979 to 2008 as Statistics Greenland names them"
    )
    p.external |= {GREENLAND, COUNTRY}
    source_rows(p)
    for table, _, _ in FAMILIES.values():
        p.artifact(raw.cached(SOURCE, url(table), BODY), SOURCE_ID)
    p.artifact(raw.cached(SOURCE, YEARBOOK), YEARBOOK_ID)
    p.add("scheme", code=SCHEME, polity_id=GREENLAND, kind="administrative", rank=1, noun="municipality")
    for name in OLD:
        id = area(name)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=slug(name), valid_from=None, valid_to=OLD_MUNICIPALITIES_END)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=None, valid_to=OLD_MUNICIPALITIES_END)
        p.name(id, name, "da", "official")
    kommune_pre2009.add(p, {name: area(name) for name in OLD})
    return p


def used_parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for e in elections():
        for row in places(e).values():
            for heading, value in row.figures.items():
                if value is not None and heading in PARTIES:
                    code, name = PARTIES[heading]
                    found[code] = name
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Inatsisartut, Greenland's seats in the Folketing, the municipal councils and the parties on the ballots "
        "before 2005 from Statistics Greenland",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {GREENLAND, COUNTRY}
    for table, _, _ in FAMILIES.values():
        p.uses(raw.cached(SOURCE, url(table), BODY))
    p.add("body", id=INATSISARTUT, polity_id=GREENLAND, kind="unicameral")
    p.name(INATSISARTUT, "Parliament of Greenland", "en", "official")
    p.name(INATSISARTUT, "Inatsisartut", "kl", "native", False)
    p.name(INATSISARTUT, "Landstinget", "da", "historical", False)
    p.add("body", id=FOLKETING, polity_id=GREENLAND, kind="unicameral")
    p.name(FOLKETING, "Folketing (Greenland's seats)", "en", "official")
    p.name(FOLKETING, "Folketinget", "da", "native", False)
    for name in OLD:
        p.add("body", id=council(name), polity_id=GREENLAND, kind="council")
        p.name(council(name), f"{name} council", "en", "official")
    for code, name in sorted(used_parties().items()):
        id = party_key(code)
        p.add("party", id=id, kind="party", polity_id=GREENLAND)
        p.add("external_id", entity_id=id, scheme="gl.statbank.party", value=code)
        p.name(id, name, "da", "official")
    return p


def yearbook_ballots() -> dict[date, int]:
    """The ballots counted in each Inatsisartut election, from the first overview table of the 2010 Yearbook page."""
    text = raw.cached(SOURCE, YEARBOOK).path.read_bytes().decode("utf-8", "replace")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    start = text.find("Oversigt 1")
    table = text[start : text.find("Kilde", start)]
    months = [
        "januar",
        "februar",
        "marts",
        "april",
        "maj",
        "juni",
        "juli",
        "august",
        "september",
        "oktober",
        "november",
        "december",
    ]
    out: dict[date, int] = {}
    for d, month, year, count in re.findall(r"(\d+)\. (\w+) (\d{4}) (\d+\.\d{3})", table):
        out[date(int(year), months.index(month) + 1, int(d))] = int(count.replace(".", ""))
    return out


def election(e: Election) -> Package:
    _, name_en, name_da = FAMILIES[e.family]
    year = e.day.year
    p = Package(
        f"{SOURCE}/{e.stamp}",
        SOURCE,
        f"The {name_en.format(year=year)} by municipality from Statistics Greenland",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {GREENLAND, COUNTRY}
    p.uses(raw.cached(SOURCE, url(e.table), BODY))
    found = places(e)
    national = found[NATIONAL]
    election = key("election", e.stamp)
    p.add(
        "election",
        id=election,
        polity_id=GREENLAND,
        kind="local" if e.family == "local" else "general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        original_text=name_da.format(year=year),
        status="held",
    )
    p.name(election, name_en.format(year=year), "en", "official")
    p.name(election, name_da.format(year=year), "da", "native", False)
    path = f"gl/{e.stamp}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    municipalities = {n: r for n, r in found.items() if n != NATIONAL}
    if e.family == "local":
        for n, row in municipalities.items():
            contest(p, e, election, path, n, {n: row}, None)
        totals = sum(accounting(r).get(BALLOTS, 0) for r in municipalities.values())
        p.add(
            "note",
            id=key("note", election, "national"),
            entity_id=election,
            kind="context",
            body_md=f"The table's national row counts {national.figures['Tallied Votes']:,} ballots from "
            f"{national.figures['Eligible Votes']:,} registered voters; the municipalities' rows add up to "
            f"{totals:,} ballots.",
            lang="en",
        )
    else:
        contest(p, e, election, path, "Greenland", municipalities, national)
    return p


def contest(
    p: Package, e: Election, election: str, path: str, title: str, units: dict[str, Row], national: Row | None
) -> None:
    code = slug(title)
    local = e.family == "local"
    contest = key("contest", e.stamp, code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=council(title) if local else INATSISARTUT if e.family == "inatsisartut" else FOLKETING,
        district_area_id=area(title) if local else COUNTRY,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(
        contest, title if local else FAMILIES[e.family][1].format(year=e.day.year), "da" if local else "en", "official"
    )
    if local:
        p.add("slug", path=f"{path}/{code}", entity_id=contest, is_current=True)
    result_set = key("result_set", e.stamp, code)
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    parsed = {name: collapse(row.figures) for name, row in units.items()}
    if national is not None:
        parsed[NATIONAL] = collapse(national.figures)
    codes = sorted({c for votes, _ in parsed.values() for c in votes})
    options = {c: key("option", e.stamp, code, c) for c in codes}
    for c in codes:
        name = next(n for h, (k, n) in PARTIES.items() if k == c)
        p.add(
            "contest_option", id=options[c], contest_id=contest, kind="list", party_id=party_key(c), ballot_label=name
        )
    individual = key("option", e.stamp, code, "others")
    if any(o is not None for _, o in parsed.values()):
        p.add("contest_option", id=individual, contest_id=contest, kind="others", ballot_label="Individual candidates")
    for name, row in units.items():
        unit = key("unit", e.stamp, slug(name))
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=GREENLAND,
            scheme_code=SCHEME,
            code=f"{e.stamp}:{slug(name)}",
            area_relation="equals",
            area_id=area(name),
            kind="standard",
        )
        votes, others = parsed[name]
        values: list[tuple[str | None, int, int]] = [(options[c], VOTES, v) for c, v in votes.items()]
        if others is not None:
            values.append((individual, VOTES, others))
        values += [(None, m, v) for m, v in accounting(row).items()]
        for option, measure, value in values:
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
    if national is not None:
        votes, others = parsed[NATIONAL]
        for c, v in votes.items():
            p.add("declared", result_set_id=result_set, option_id=options[c], measure=VOTES, value=v)
        if others is not None:
            p.add("declared", result_set_id=result_set, option_id=individual, measure=VOTES, value=others)
        for m, v in accounting(national).items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=m, value=v)
    found = problems(e, units, national)
    for n, (code_, text) in enumerate(found):
        p.add(
            "note",
            id=key("note", result_set, "caveat", n),
            entity_id=result_set,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    if e.day.year < 2002:
        p.add(
            "note",
            id=key("note", result_set, "voids"),
            entity_id=result_set,
            kind="context",
            body_md=NOTE_VOIDS,
            lang="en",
        )
    for code_ in dict.fromkeys(c for c, _ in found):
        p.accept(code_, REASONS[code_])
    if e.family == "inatsisartut":
        printed = yearbook_ballots().get(e.day)
        counted = national.figures["Tallied Votes"] if national is not None else None
        if printed is not None and counted != printed:
            p.uses(raw.cached(SOURCE, YEARBOOK))
            p.add(
                "note",
                id=key("note", result_set, "yearbook"),
                entity_id=result_set,
                kind="caveat",
                body_md=f"Statistics Greenland's 2010 Statistical Yearbook gives {printed:,} ballots counted for this "
                f"election; the StatBank table gives {counted:,}. The StatBank figures are stored as printed.",
                lang="en",
            )


REASONS = {
    "accounting.votes_valid": "the table prints more or fewer valid votes than its own party and candidate columns "
    "add up to in some rows; stored as printed, with a note on the result set for each",
    "accounting.ballots_sum": "the table prints a ballots-counted figure that is not the sum of its own valid, void "
    "and blank columns in some rows; stored as printed, with a note on the result set for each",
    "accounting.declared": "the table's national row differs from the sum of its municipality rows; stored as "
    "printed, with a note on the result set for each",
}


def problems(e: Election, units: dict[str, Row], national: Row | None) -> list[tuple[str, str]]:
    """What the table's figures contradict, found by adding them up."""
    found: list[tuple[str, str]] = []
    every = dict(units)
    if national is not None:
        every[NATIONAL] = national
    for name, row in every.items():
        votes, others = collapse(row.figures)
        valid = row.figures["Valid votes in all"]
        total = sum(votes.values()) + (others or 0)
        if valid is not None and total != valid:
            found.append(
                (
                    "accounting.votes_valid",
                    f"{name}: the parties' votes add up to {total:,} but the table prints {valid:,} valid votes.",
                )
            )
        counted = row.figures["Tallied Votes"]
        parts = [row.figures[h] or 0 for h in ("Valid votes in all", "Sum of Voids votes", "Sum of Blank votes")]
        if valid is not None and counted is not None and sum(parts) != counted:
            found.append(
                (
                    "accounting.ballots_sum",
                    f"{name}: the valid, void and blank ballots add up to {sum(parts):,} but the table prints {counted:,} ballots counted.",
                )
            )
    if national is not None:
        for heading in ACCOUNTING:
            want = national.figures[heading]
            have = sum(r.figures[heading] or 0 for r in units.values())
            if want is not None and want != have:
                found.append(
                    (
                        "accounting.declared",
                        f"{heading}: the national row prints {want:,} and the municipalities' rows add up to {have:,}.",
                    )
                )
        want_votes, _ = collapse(national.figures)
        for c, v in want_votes.items():
            have = sum(collapse(r.figures)[0].get(c, 0) for r in units.values())
            if have != v:
                found.append(
                    (
                        "accounting.declared",
                        f"{c}: the national row prints {v:,} votes and the municipalities' rows add up to {have:,}.",
                    )
                )
    return found
