"""Liechtenstein's national popular votes (initiatives, referendums, and the votes on credits and treaties) from the
government's results site, by municipality, from the first vote it publishes in 2002 to the latest."""

import functools
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.li import regierung

SOURCE = "li.abstimmungen"
SITE = "https://abstimmungen.li"
LANDING = f"{SITE}/resultat/35"
LIECHTENSTEIN = str(uuid5(package.NAMESPACE, "polity\x1fLI"))
PAUSE = 0.3
# Reporting units on the results site: 1-11 are the municipalities, 12 and 13 the two districts, 14 the country.
MUNICIPALITIES = range(1, 12)
COUNTRY_UNIT = 14


def archive_url(year: int) -> str:
    return f"{SITE}/archiv/{year}"


def result_url(vote: int, unit: int) -> str:
    return f"{SITE}/resultat/{vote}/{unit}"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def page(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8")


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def number(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text))


def years() -> list[int]:
    """The years the archive menu lists."""
    return sorted({int(year) for year in re.findall(r'href="/archiv/(\d{4})', page(LANDING))})


def fetch() -> None:
    raw.fetch(SOURCE, LANDING, suffix=".html")
    raw.fetch(SOURCE, DATENSCHUTZ, suffix=".html")
    raw.fetch(GEOBOUNDARIES, ADM1, suffix=".geojson")
    for year in years():
        raw.fetch(SOURCE, archive_url(year), suffix=".html")
    for vote in listed():
        for unit in (*MUNICIPALITIES, COUNTRY_UNIT):
            raw.fetch(SOURCE, result_url(vote, unit), suffix=".html")
            time.sleep(PAUSE)


@dataclass(frozen=True)
class Vote:
    id: int
    short: str
    date_text: str
    description: str
    drafts: tuple[str, ...]
    listing: str


def listed() -> list[int]:
    """The ids of every vote the menu links, the current one included."""
    return sorted({int(vote) for vote in re.findall(r'href="/(?:abstimmung|resultat)/(\d+)', page(LANDING))})


@functools.cache
def votes() -> list[Vote]:
    found: list[Vote] = []
    for year in years():
        text = page(archive_url(year))
        for chunk in re.split(r'(?=<div class="ballot"\s+data-count=)', text)[1:]:
            identifier = re.search(r"/resultat/(\d+)'", chunk)
            short = re.search(r'class="shortitle">(.*?)</h2>', chunk, re.DOTALL)
            when = re.search(r'class="date">(.*?)</div>', chunk, re.DOTALL)
            description = re.search(r'<div class="text">\s*<p>(.*?)</p>', chunk, re.DOTALL)
            if identifier is None or short is None or when is None or description is None:
                raise ValueError(f"unreadable vote on the {year} archive page")
            titles = tuple(plain(title) for title in re.findall(r'<div class="title">(.*?)</div>', chunk, re.DOTALL))
            found.append(
                Vote(
                    int(identifier.group(1)),
                    plain(short.group(1)),
                    plain(when.group(1)),
                    plain(description.group(1)),
                    titles,
                    archive_url(year),
                )
            )
    current = sorted(set(listed()) - {vote.id for vote in found})
    for identifier in current:
        # The vote the site shows as current is not in the archive yet; its own results page names it.
        text = page(result_url(identifier, COUNTRY_UNIT))
        when = re.search(r'id="content_date"[^>]*>\s*vom\s*(.*?)\s*-\s*(.*?)</h3>', text, re.DOTALL)
        head = re.search(r'<h2 class="head">(.*?)</h2>', text, re.DOTALL)
        if when is None or head is None:
            raise ValueError(f"unreadable current vote {identifier}")
        found.append(Vote(identifier, plain(when.group(2)), plain(when.group(1)), "", draft_titles(text), LANDING))
    return sorted(found, key=lambda vote: vote.id)


def draft_titles(text: str) -> tuple[str, ...]:
    return tuple(
        plain(title)
        for title in re.findall(r'<div id="[^"]*" class="draft">\s*<div class="title">(.*?)</div>', text, re.DOTALL)
    )


MONTHS = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}


def days(text: str) -> tuple[date, date]:
    """The first and last day of a printed vote date such as '3. / 5. November 2006', '29. Juni / 01. Juli 2012',
    'Sonntag, 25. November 2018', or '13.12.2015'."""
    text = re.sub(r"^\w+day,\s*|^Sonntag,\s*", "", text.strip())
    numeric = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if numeric:
        when = date(int(numeric.group(3)), int(numeric.group(2)), int(numeric.group(1)))
        return when, when
    found = re.search(r"(\d{4})$", text)
    if found is None:
        raise ValueError(f"no year in {text!r}")
    year = int(found.group(1))
    stamped = [
        (int(day), MONTHS.get(month)) for day, month in re.findall(r"(\d{1,2})\.\s*(" + "|".join(MONTHS) + r")?", text)
    ]
    last_month = stamped[-1][1]
    if last_month is None:
        raise ValueError(f"no month in {text!r}")
    dates = [date(year, month or last_month, day) for day, month in stamped]
    return dates[0], dates[-1]


@dataclass(frozen=True)
class Draft:
    title: str
    yes: int
    no: int


@dataclass(frozen=True)
class Unit:
    heading: str
    drafts: tuple[Draft, ...]
    protocol: tuple[tuple[str, str], ...]
    turnout: str | None


@functools.cache
def unit_page(vote: int, unit: int) -> Unit:
    text = page(result_url(vote, unit))
    head = re.search(r'id="content_date"[^>]*>(.*?)</h3>', text, re.DOTALL)
    drafts = []
    for chunk in re.split(r'(?=<div id="content_repBallots_repDrafts_)', text)[1:]:
        chunk = chunk.split('<p class="message">')[0]
        title = re.search(r'<div class="title">(.*?)</div>', chunk, re.DOTALL)
        counts = dict(re.findall(r'<span class="label">(Ja|Nein)</span>\s*<span class="number">([^<]*)</span>', chunk))
        if title is None or set(counts) != {"Ja", "Nein"}:
            raise ValueError(f"unreadable draft on {result_url(vote, unit)}")
        drafts.append(Draft(plain(title.group(1)), number(counts["Ja"]), number(counts["Nein"])))
    area = text.split('id="content_protocolarea"')[1].split('class="disclaimer"')[0]
    area = area.split('id="content_divCombi"')[0]
    protocol = tuple(
        (plain(label), plain(value))
        for label, value in re.findall(
            r"<td><span>(.*?)</span></td>\s*<td[^>]*><span>(.*?)</span></td>", area, re.DOTALL
        )
    )
    return Unit(plain(head.group(1)) if head else "", tuple(drafts), protocol, None)


# The English names of the votes and their questions, by site id.
ENGLISH: dict[int, tuple[str, ...]] = {
    1: ("Dog Act amendment",),
    2: ("Credit for extending the police building",),
    3: ("Initiative “For Life”", "Counter-proposal of the Landtag"),
    4: ("Accident insurance (NBU) contribution",),
    5: ("Constitutional initiative of the Princely House", "Initiative “Constitutional Peace”"),
    6: ("Spatial Planning Act",),
    7: ("Credit for the “Little Big One” festival 2002–2004",),
    8: ("Traffic initiative (Article 20 of the Constitution)",),
    9: ("School reform SPES I",),
    10: ("Protection from smoking",),
    11: ("Mobile phone antennas",),
    12: ("Schaan industrial feeder road",),
    13: ("Partnership Act for same-sex couples",),
    14: ("Initiative “Help instead of punishment”",),
    15: ("New national hospital",),
    16: ("Initiative “So your vote counts”",),
    17: ("Initiative “Pension fund win-win”", "Initiative “WinWin50”"),
    18: ("Health Insurance Act amendment",),
    19: ("Family Allowances Act",),
    20: ("Credit for the “Tour de Ski”",),
    21: ("Credit for the new national hospital",),
    22: ("Initiative “HalbeHalbe”",),
    23: ("Dual citizenship in naturalizations",),
    24: ("Credit for an S-Bahn Liechtenstein",),
    25: ("Initiative on the deductible for retirees",),
    26: ("Legal basis for a 2G rule",),
    27: ("Initiative “Casino ban”",),
    28: ("Photovoltaic obligation",),
    29: ("Adjustment of building regulations",),
    30: ("Initiative on the electronic health record",),
    31: ("Initiative on involving the people in appointing the government",),
    32: ("Supplementary credit for the new national hospital",),
    33: ("Accession to the International Monetary Fund",),
    34: ("Initiative to repeal the Liechtenstein Broadcasting Act",),
    35: ("State employees' pension scheme (SBPVG)",),
}


VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
DERIVED = 1  # result_value.flags: a figure the archive computed from the source's, not one the source prints
GEO = f"{SOURCE}/geo"
LI_GEO = f"{regierung.SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
SYSTEM = key("electoral_system", "popular_vote")
VOTE_SOURCE = key("source", SOURCE)
GEO_SOURCE = key("source", "geoboundaries")
GEOBOUNDARIES = "li.geoboundaries"
ADM1 = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/LIE/ADM1/"
    "geoBoundaries-LIE-ADM1_simplified.geojson"
)
DATENSCHUTZ = f"{SITE}/datenschutz"
SYSTEM_NOTE = (
    "A popular vote under Liechtenstein's constitution and its law on popular rights: each question is decided by a "
    "simple majority of the valid votes cast, with no quorum. When an initiative and a counter-proposal of the Landtag "
    "are on one ballot, each is a question of its own and the voter may say yes to both."
)
# Ballots the protocol counts in the categories that add up to the cards cast; the 2002 to 2006 protocols do not.
ACCOUNTED_FROM = 9


def ratio(text: str) -> float:
    return float(text.rstrip("%"))


def protocol(vote: int, unit: int) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for label, value in unit_page(vote, unit).protocol:
        if re.search(r"\d", value) and "%" not in value:
            found.setdefault(label, []).append(number(value))
    return found


def figures(vote: int, unit: int, prefer: int | None = None) -> dict[int, tuple[int, int]]:
    """The accounting figures of one unit as {measure: (value, flags)}."""
    rows = protocol(vote, unit)

    def one(*labels: str) -> int | None:
        for label in labels:
            if label in rows:
                if len(set(rows[label])) != 1:
                    # the protocol prints some totals twice, and the two disagree by one or two in a few units
                    return prefer if prefer in rows[label] else rows[label][0]
                return rows[label][0]
        return None

    found: dict[int, tuple[int, int]] = {}
    registered = one("Stimmberechtigte")
    cast = one("Total abgegebene Stimmkarten", "Total Abgegebene Stimmkarten", "Abgegebene Stimmkarten")
    valid = one("Total der gültigen Stimmen", "Gültige Stimmzettel")
    if registered is None or cast is None:
        raise ValueError(f"vote {vote} unit {unit}: no electorate or cards cast")
    found[REGISTERED] = (registered, 0)
    found[CAST] = (cast, 0)
    if valid is not None:
        found[VALID] = (valid, 0)
    if vote >= ACCOUNTED_FROM:
        if "Total der ungültigen Stimmen" in rows:
            summed = 0
            invalid = one("Total der ungültigen Stimmen")
            blank = one("Total der leeren und nicht eingelegten Stimmen", "Total der leeren Stimmen")
        else:
            postal = one("Brieflich ungültige Stimmabgaben")
            enclosed = one("Eingelegte, ungültige Stimmzettel", "Eingelegte, ungültige Stimmen")
            empty = one("Eingelegte, leere Stimmkuverts")
            loose = one("Total nicht eingelegte Stimmkuverts")
            if None in (postal, enclosed, empty, loose):
                raise ValueError(f"vote {vote} unit {unit}: the ballot categories are incomplete")
            summed = DERIVED
            invalid = (postal or 0) + (enclosed or 0)
            blank = (empty or 0) + (loose or 0)
        if invalid is None or blank is None:
            raise ValueError(f"vote {vote} unit {unit}: no invalid or blank count")
        found[INVALID] = (invalid, summed)
        found[BLANK] = (blank, summed)
    return found


@dataclass(frozen=True)
class Day:
    first: date
    last: date
    text: str
    votes: tuple[Vote, ...]

    @property
    def key(self) -> str:
        return f"{SOURCE}/{self.last}"


def vote_days(vote: Vote) -> tuple[date, date]:
    return days(vote.date_text)


@functools.cache
def calendar() -> list[Day]:
    grouped: dict[tuple[date, date], list[Vote]] = {}
    for vote in votes():
        grouped.setdefault(vote_days(vote), []).append(vote)
    return [
        Day(first, last, grouped[(first, last)][0].date_text, tuple(grouped[(first, last)]))
        for first, last in sorted(grouped, key=lambda span: span[1])
    ]


REASONS = {
    "accounting.ballots_sum": "The results site's protocol of some municipalities prints ballot categories that do not "
    "add up to the cards cast (an envelope counted in one total and not another, or a figure printed wrongly); "
    "every figure is stored as printed.",
    "accounting.declared": "The results site prints the country's valid votes twice, and the two figures differ by "
    "one; the municipalities add up to the other figure.",
}


def packages() -> list[str]:
    return [GEO, REGISTRY, *(day.key for day in calendar())]


def parse() -> None:
    geo().write()
    registry().write()
    for day in calendar():
        built = election(day)
        for code, _, _ in package.check(built.write()):
            if code not in REASONS:
                raise ValueError(f"{day.key}: finding {code} cannot be accepted")
            built.accept(code, REASONS[code])
        built.write()


def emit() -> None:
    package.run("load", packages())


def shape(geometry: dict[str, Any]) -> shapely.MultiPolygon:
    polygon = shapely.make_valid(shapely.geometry.shape(geometry))
    parts = [part for part in shapely.get_parts(polygon) if part.geom_type in {"Polygon", "MultiPolygon"}]
    flat = [g for part in parts for g in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(flat), 4326)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The outlines of Liechtenstein's 11 municipalities, for the municipalities its popular votes report",
        depends=[LI_GEO],
    )
    p.external.add(LIECHTENSTEIN)
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Liechtenstein ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(raw.cached(GEOBOUNDARIES, ADM1), GEO_SOURCE)
    p.add(
        "boundary_set",
        id=key("boundary_set", "li.gemeinde", "2017"),
        scheme_code="li.gemeinde",
        vintage="2017",
        valid_from=date(2002, 1, 1),
        as_of=date(2017, 1, 1),
    )
    features = json.loads(raw.cached(GEOBOUNDARIES, ADM1).path.read_text(encoding="utf-8"))["features"]
    outlines = {f["properties"]["shapeName"]: shape(f["geometry"]) for f in features}
    names = {name: code for name, code, _ in regierung.PLACES.values()}
    if set(outlines) != set(names):
        raise ValueError(f"outlines {sorted(outlines)} do not match the municipalities {sorted(names)}")
    for name, geometry in sorted(outlines.items()):
        p.add(
            "area_geometry",
            area_id=regierung.area("li.gemeinde", names[name]),
            valid_from=date(2002, 1, 1),
            geom=shapely.to_wkb(geometry, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(geometry), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=GEO_SOURCE,
        )
    return p


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Liechtenstein's popular votes: the results site they come from and the electoral system of a popular vote",
    )
    p.external.add(LIECHTENSTEIN)
    p.add(
        "source",
        id=VOTE_SOURCE,
        kind="official_body",
        publisher="Information und Kommunikation der Regierung",
        title="Abstimmungen Liechtenstein: Ergebnisse",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        license_url=DATENSCHUTZ,
        attribution="Regierung des Fürstentums Liechtenstein, abstimmungen.li",
    )
    p.artifact(raw.cached(SOURCE, DATENSCHUTZ), VOTE_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=LIECHTENSTEIN,
        valid_from=date(2002, 1, 1),
        valid_to=None,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=SYSTEM_NOTE,
        lang="en",
    )
    return p


def kind_of(vote: Vote, title: str) -> str:
    if "Gegenvorschlag" in title:
        return "counter_proposal"
    if "nitiativ" in vote.description or "nitiativ" in title:
        return "initiative"
    if "Referendumsbegehren" in vote.description:
        return "optional_referendum"
    return "referendum"


def english_date(day: Day) -> str:
    text = f"{day.last.day} {day.last:%B %Y}"
    return text


def election(day: Day) -> Package:
    p = Package(
        day.key,
        SOURCE,
        f"The Liechtenstein popular vote of {english_date(day)} by municipality",
        depends=[LI_GEO, REGISTRY],
    )
    p.external.add(LIECHTENSTEIN)
    election_id = key("election", day.last)
    p.add(
        "election",
        id=election_id,
        polity_id=LIECHTENSTEIN,
        kind="referendum",
        date_from=day.first,
        date_to=day.last,
        date_precision="day",
        original_text=day.text,
        status="held",
    )
    p.name(election_id, f"{english_date(day)} Liechtenstein popular vote", "en", "english")
    p.name(election_id, f"Volksabstimmung vom {day.text}", "de", "native", False)
    path = f"li/{day.last}-referendum"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    units = {}
    for number_, (name, code, _) in regierung.PLACES.items():
        units[number_] = key("unit", day.last, code)
        p.add(
            "reporting_unit",
            id=units[number_],
            polity_id=LIECHTENSTEIN,
            scheme_code="li.gemeinde",
            code=f"{day.last}:{code}",
            area_relation="equals",
            area_id=regierung.area("li.gemeinde", code),
            kind="standard",
        )
    p.uses(raw.cached(SOURCE, LANDING))
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    for vote in day.votes:
        p.uses(raw.cached(SOURCE, vote.listing))
        for unit in (*MUNICIPALITIES, COUNTRY_UNIT):
            p.uses(raw.cached(SOURCE, result_url(vote.id, unit)))
        titles = unit_page(vote.id, COUNTRY_UNIT).drafts
        english = ENGLISH[vote.id]
        if len(titles) != len(english) or [t.title for t in titles] != list(vote.drafts):
            raise ValueError(f"vote {vote.id}: the questions {vote.drafts} do not match the names {english}")
        for index, draft in enumerate(titles):
            contest = key("contest", vote.id, index)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="ballot_question",
                electoral_system_id=SYSTEM,
                round_number=1,
                default_result_kind="counted",
            )
            p.add("ballot_question", contest_id=contest, type=kind_of(vote, draft.title))
            p.name(contest, draft.title, "de", "official", False)
            p.name(contest, english[index], "en", "english")
            p.add("slug", path=f"{path}/{changeset.slug(english[index])}", entity_id=contest, is_current=True)
            if vote.description:
                p.add(
                    "note",
                    id=key("note", contest, "question"),
                    entity_id=contest,
                    kind="context",
                    body_md=vote.description,
                    lang="de",
                )
            options = {}
            for answer, position, de, en in (("yes", 1, "Ja", "Yes"), ("no", 2, "Nein", "No")):
                options[answer] = key("option", vote.id, index, answer)
                p.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=de,
                )
                p.name(options[answer], en, "en", "english")
                p.name(options[answer], de, "de", "official", False)
            result_set = key("result_set", vote.id, index)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=VOTE_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            for number_, unit in units.items():
                page_ = unit_page(vote.id, number_)
                counts = page_.drafts[index]
                accounted = figures(vote.id, number_, counts.yes + counts.no)
                if len(titles) > 1:
                    accounted = {m: f for m, f in accounted.items() if m in (REGISTERED, CAST)}
                    accounted[VALID] = (counts.yes + counts.no, DERIVED)
                else:
                    accounted.setdefault(VALID, (counts.yes + counts.no, DERIVED))
                for measure, (value, flags) in accounted.items():
                    rows.append((result_set, unit, None, measure, value, flags))
                rows.append((result_set, unit, options["yes"], VOTES, counts.yes, 0))
                rows.append((result_set, unit, options["no"], VOTES, counts.no, 0))
            national = unit_page(vote.id, COUNTRY_UNIT)
            top = national.drafts[index]
            totals = figures(vote.id, COUNTRY_UNIT, top.yes + top.no)
            if len(titles) > 1:
                totals = {m: f for m, f in totals.items() if m in (REGISTERED, CAST)}
                totals[VALID] = (top.yes + top.no, DERIVED)
            else:
                totals.setdefault(VALID, (top.yes + top.no, DERIVED))
            for measure, (value, _) in totals.items():
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            p.add("declared", result_set_id=result_set, option_id=options["yes"], measure=VOTES, value=top.yes)
            p.add("declared", result_set_id=result_set, option_id=options["no"], measure=VOTES, value=top.no)
            winner, loser = ("yes", "no") if top.yes > top.no else ("no", "yes")
            p.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won")
            p.add("outcome", result_set_id=result_set, option_id=options[loser], kind="lost")
    import polars as pl

    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema=["result_set_id", "unit_id", "option_id", "measure", "value", "flags"],
            orient="row",
            infer_schema_length=None,
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            flags=pl.col("flags").cast(pl.Int16),
        ),
    )
    return p
