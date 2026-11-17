"""Madagascar's Haute Cour Constitutionnelle: national totals of the presidential elections of 2006 and 2013 and the
constitutional referendum of 2007, from the court's proclamation judgments (arrêts)."""

import html
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.mg import ceni

SOURCE = "mg.hcc"
SHAPES = "mg.geoboundaries"
SITE = "https://www.hcc.gov.mg"
PAGES = {
    "pres2006": f"{SITE}/?p=709",
    "pres2013-1": f"{SITE}/?p=1218",
    "pres2013-2": f"{SITE}/?p=1286",
    "referendum2007": f"{SITE}/?p=489",
}
OUTLINE = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/MDG/ADM0/geoBoundaries-MDG-ADM0_simplified.geojson"
MADAGASCAR = ceni.MADAGASCAR
NATION_SCHEME = "mg.nation"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
PLAN_FROM = date(1960, 6, 26)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "hcc")
SHAPES_ID = key("source", SHAPES)
NATION = key("area", NATION_SCHEME, "MG")


@dataclass
class Totals:
    registered: int
    cast: int
    invalid: int
    valid: int
    votes: list[tuple[str, int]]
    numbers: list[int]
    turnout: str | None = None


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    raw.fetch(SHAPES, OUTLINE, suffix=".geojson")


def text(name: str) -> list[str]:
    """The lines of the judgment's text, with table cells joined by ` | `."""
    page = raw.cached(SOURCE, PAGES[name]).path.read_text(errors="replace")
    body = re.search(r"<article.*?</article>", page, re.DOTALL)
    if body is None:
        raise ValueError(f"{name}: no article in the page")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", body.group(0), flags=re.DOTALL)
    s = re.sub(r"</(p|tr|div|h\d|li)>", "\n", s)
    s = re.sub(r"</t[dh]>", " | ", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace(" ", " ").replace(" ", " ")
    return [line.strip() for line in s.split("\n") if line.strip()]


def number(raw_text: str) -> int:
    digits = re.sub(r"[ .,]", "", raw_text)
    if not digits.isdigit():
        raise ValueError(f"not a count: {raw_text!r}")
    return int(digits)


def figure(lines: list[str], label: str, start: int = 0) -> int:
    pattern = re.compile(rf"^[–-]?\s*{label}[ .…:=]*?([\d][\d .]*)\s*$", re.IGNORECASE)
    for line in lines[start:]:
        if m := pattern.match(line):
            return number(m.group(1))
    raise ValueError(f"no line for {label!r}")


def article(lines: list[str], pattern: str) -> int:
    for index, line in enumerate(lines):
        if re.match(pattern, line):
            return index
    raise ValueError(f"no article matching {pattern!r}")


def president_2006() -> Totals:
    lines = text("pres2006")
    start = article(lines, r"Article 6\.")
    end = article(lines, r"Article 7\.")
    block = lines[start:end]
    votes = []
    for line in block:
        if m := re.match(r"^([A-ZÉ][A-Za-zÀ-ÿ' -]+?) ([\d.]+) \(([\d,]+) ?%\)$", line):
            votes.append((m.group(1), number(m.group(2))))
    return Totals(
        figure(block, "nombre des électeurs inscrits"),
        figure(block, "nombre des votants"),
        figure(block, "nombre des bulletins blancs et nuls"),
        figure(block, "nombre des suffrages exprimés"),
        votes,
        list(range(1, len(votes) + 1)),
    )


def president_2013_1() -> Totals:
    lines = text("pres2013-1")
    start = article(lines, r"Article 10\.")
    end = article(lines, r"Article 11\.")
    block = lines[start:end]
    votes, numbers = [], []
    index = 0
    while index < len(block):
        if m := re.match(r"^(?:\| )?(\d{2}) \|$", block[index]):
            name = block[index + 1].removesuffix(" |").strip()
            count = number(block[index + 2].removesuffix(" |"))
            numbers.append(int(m.group(1)))
            votes.append((name, count))
            index += 4
        else:
            index += 1
    return Totals(
        figure(block, "nombre des électeurs inscrits", 0),
        figure(block, "nombre des votants"),
        figure(block, "nombre des bulletins blancs et nuls"),
        figure(block, "nombre des suffrages exprimés"),
        votes,
        numbers,
    )


def president_2013_2() -> Totals:
    lines = text("pres2013-2")
    start = article(lines, r"Article 10\.")
    end = article(lines, r"Article 11\.")
    block = lines[start:end]
    votes = []
    for line in block:
        if m := re.match(r"^([A-ZÉ][A-Za-zÀ-ÿ' -]+?[A-Za-z]) ?: ([\d.]+) soit", line):
            votes.append((m.group(1).strip(), number(m.group(2))))
    return Totals(
        figure(block, "nombre des électeurs inscrits"),
        figure(block, "nombre des votants"),
        figure(block, "nombre des bulletins blancs et nuls"),
        figure(block, "nombre des suffrages exprimés"),
        votes,
        list(range(1, len(votes) + 1)),
    )


def referendum_2007() -> Totals:
    lines = text("referendum2007")
    start = article(lines, r"Article 5\.")
    end = article(lines, r"Article 6\.")
    block = lines[start:end]
    yes = re.search(r"O U I : ([\d ]+)\(", " ".join(block))
    no = re.search(r"N O N : ([\d ]+)\(", " ".join(block))
    if yes is None or no is None:
        raise ValueError("no yes or no line")
    return Totals(
        figure(block, "Electeurs inscrits :"),
        figure(block, "Votants :"),
        figure(block, "Blancs et nuls :"),
        figure(block, "Suffrages exprimés :"),
        [("Oui", number(yes.group(1))), ("Non", number(no.group(1)))],
        [1, 2],
    )


@dataclass
class Election:
    day: date
    pages: tuple[str, ...]
    kind: str
    round: int
    en: str
    fr: str
    totals: Totals
    declared_registered: int
    winner: str | None


def parsed() -> dict[str, Election]:
    pres06, pres13a, pres13b, ref = president_2006(), president_2013_1(), president_2013_2(), referendum_2007()
    return {
        "2006-12-03": Election(
            date(2006, 12, 3),
            ("pres2006",),
            "general",
            1,
            "2006 Malagasy presidential election",
            "Élection présidentielle de 2006",
            pres06,
            7317790,
            "RAVALOMANANA Marc",
        ),
        "2007-04-04": Election(
            date(2007, 4, 4),
            ("referendum2007",),
            "referendum",
            1,
            "2007 Malagasy constitutional referendum",
            "Référendum constitutionnel de 2007",
            ref,
            7381091,
            "Oui",
        ),
        "2013-10-25": Election(
            date(2013, 10, 25),
            ("pres2013-1",),
            "general",
            1,
            "2013 Malagasy presidential election",
            "Élection présidentielle de 2013",
            pres13a,
            7839268,
            None,
        ),
        "2013-12-20": Election(
            date(2013, 12, 20),
            ("pres2013-2",),
            "runoff",
            2,
            "2013 Malagasy presidential election, second round",
            "Élection présidentielle de 2013, second tour",
            pres13b,
            7971790,
            "RAJAONARIMAMPIANINA RAKOTOARIMANANA Hery Martial",
        ),
    }


def packages() -> list[str]:
    return [f"{SOURCE}/geo", *(f"{SOURCE}/{day}" for day in ("2006-12-03", "2007-04-04", "2013-10-25", "2013-12-20"))]


def parse() -> None:
    for day in ELECTION_DAYS:
        build(day).write()
    geo().write()


ELECTION_DAYS = ("2006-12-03", "2007-04-04", "2013-10-25", "2013-12-20")


def emit() -> None:
    package.run("load", packages())


def multipolygon(shape: shapely.Geometry) -> shapely.Geometry:
    shape = shapely.make_valid(shape)
    parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Madagascar as one area, with its outline from geoBoundaries, for the national totals of the "
        "constitutional court",
    )
    p.external.add(MADAGASCAR)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Madagascar ADM0 (simplified)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0",
        license_url="https://www.openstreetmap.org/copyright",
        attribution="geoBoundaries (William & Mary geoLab); original data from OpenStreetMap contributors",
    )
    p.artifact(raw.cached(SHAPES, OUTLINE), SHAPES_ID)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Haute Cour Constitutionnelle de Madagascar",
        title="Arrêts of the Haute Cour Constitutionnelle and the Cour Electorale Spéciale proclaiming election results",
        url=f"{SITE}/",
        license="Official judgments published by the court; reuse terms are not stated",
        attribution="Haute Cour Constitutionnelle de Madagascar",
    )
    p.add("scheme", code=NATION_SCHEME, polity_id=MADAGASCAR, kind="administrative", rank=0, noun="country")
    p.add(
        "boundary_set",
        id=key("boundary_set", NATION_SCHEME, "1960"),
        scheme_code=NATION_SCHEME,
        vintage="1960",
        valid_from=PLAN_FROM,
        as_of=date(2023, 1, 19),
    )
    p.add("area", id=NATION, scheme_code=NATION_SCHEME)
    p.add("area_code", area_id=NATION, code="MG")
    p.name(NATION, "Madagascar", "en", "official")
    p.name(NATION, "Madagascar", "fr", "native", False)
    path: Path = raw.cached(SHAPES, OUTLINE).path
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    shape = multipolygon(shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in features]))
    p.add(
        "area_geometry",
        area_id=NATION,
        valid_from=PLAN_FROM,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=SHAPES_ID,
    )
    return p


METHOD = (
    "The court's national totals as its proclamation judgment states them, with no finer breakdown. "
    "{blanks} The judgment prints the number of registered voters, voters, and valid votes."
)

ACCEPT: dict[str, dict[str, str]] = {}


def build(day: str) -> Package:
    e = parsed()[day]
    t = e.totals
    referendum = e.kind == "referendum"
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {e.en.removeprefix(str(e.day.year) + ' ')} of {e.day.year}, national totals, from the Haute Cour "
        "Constitutionnelle's proclamation judgment",
        depends=["mg.ceni/registry", f"{SOURCE}/geo", *(["mg.hcc/2013-10-25"] if e.round == 2 else [])],
    )
    p.external.add(MADAGASCAR)
    for name in e.pages:
        p.artifact(raw.cached(SOURCE, PAGES[name]), SOURCE_ID)

    election = p.id("election", day)
    p.add(
        "election",
        id=election,
        polity_id=MADAGASCAR,
        kind=e.kind,
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    p.name(election, e.en, "en", "official")
    p.name(election, e.fr, "fr", "native", False)
    base = f"mg/{day}-{'referendum' if referendum else 'presidential'}"
    p.add("slug", path=base, entity_id=election, is_current=True)

    contest = key("contest", day)
    if referendum:
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(
            contest,
            "Do you approve this draft Constitution for the rapid and lasting development of the regions "
            "and the betterment of the living conditions of the Malagasy?",
            "en",
            "official",
        )
        p.name(
            contest,
            "Ekenao ve ity volavolam-panitsiana ny Lalàmpanorenana ity ho fampandrosoana haingana sy maharitra "
            "ny isam-paritra hanatsarana ny fari-piainan’ny Malagasy ?",
            "mg",
            "native",
            False,
        )
        p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    else:
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=ceni.PRESIDENCY,
            magnitude=1,
            round_number=e.round,
            previous_round_id=key("contest", "2013-10-25") if e.round == 2 else None,
            electoral_system_id=ceni.SYSTEM_PRESIDENT,
            default_result_kind="counted",
        )
        p.name(contest, e.en, "en", "official")
        p.name(contest, e.fr, "fr", "native", False)
        p.add("slug", path=f"{base}/president", entity_id=contest, is_current=True)

    options: dict[str, str] = {}
    for position, (label, _) in enumerate(t.votes):
        number_ = t.numbers[position]
        option = p.id("option", day, number_)
        options[label] = option
        if referendum:
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position + 1,
                ballot_label="Yes (Eny)" if label == "Oui" else "No (Tsia)",
            )
            continue
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=number_,
            ballot_label=label,
        )
        person = key("person", "pres2013" if day.startswith("2013") else day, ceni.norm(label))
        if e.round == 1:
            p.add("person", id=person)
            p.name(person, label, "fr", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", day, number_),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
        )
    return finish(p, e, day, election, contest, options)


def finish(p: Package, e: Election, day: str, election: str, contest: str, options: dict[str, str]) -> Package:
    t = e.totals
    result_set = p.id("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD.format(
            blanks="Blank and null ballots are printed together and stored as invalid ballots, with no separate blank count."
        ),
        lang="en",
    )
    unit = p.id("unit", day)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MADAGASCAR,
        scheme_code=NATION_SCHEME,
        code=day,
        area_relation="equals",
        area_id=NATION,
        kind="standard",
    )
    p.name(unit, "Madagascar", "fr", "official")
    accounting = [(REGISTERED, t.registered), (CAST, t.cast), (VALID, t.valid), (INVALID, t.invalid)]
    rows: list[tuple[str, str | None, int, int]] = [(unit, None, m, v) for m, v in accounting]
    for label, count in t.votes:
        rows.append((unit, options[label], VOTES, count))
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    for measure, value in accounting:
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for label, count in t.votes:
        p.add("declared", result_set_id=result_set, option_id=options[label], measure=VOTES, value=count)
    ranked = sorted(t.votes, key=lambda item: -item[1])
    if e.kind == "referendum":
        p.add("outcome", result_set_id=result_set, option_id=options["Oui"], kind="won")
    else:
        for index, (label, _) in enumerate(ranked):
            if e.winner is not None:
                kind = "elected" if label == e.winner else "lost"
            else:
                kind = "advanced" if index < 2 else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=options[label], kind=kind)
    for code, reason in ACCEPT.get(day, {}).items():
        p.accept(code, reason)
    return p
