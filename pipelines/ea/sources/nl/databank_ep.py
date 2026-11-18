"""European Parliament elections 1979-2004 and the national referendum of 1 June 2005 by municipality, from the
Kiesraad's Databank Verkiezingsuitslagen (CC0).

For each European Parliament election the databank serves a detail page, which embeds the national result (the
figures and each list's votes and seats), and a CSV of every municipality's votes per list. The 2005 referendum's
municipal CSV is empty, so each municipality's page and CSV are read one by one: the page's JSON gives its votes for
and against, and the CSV gives its Statistics Netherlands code, electorate, turnout, blank and invalid votes.
"""

import functools
import html
import json
import os
import re
import ssl
from collections import Counter
from datetime import date
from typing import Any, NamedTuple
from uuid import uuid5

import certifi
import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "nl.databank_ep"
HOST = "https://www.verkiezingsuitslagen.nl"
# The host serves its leaf certificate without the intermediate that signed it.
INTERMEDIATE = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"
EUROPEAN = ["EP19790607", "EP19840614", "EP19890615", "EP19940609", "EP19990610", "EP20040610"]
REFERENDUM = "NR20050601"
CODES = [*EUROPEAN, REFERENDUM]
NETHERLANDS = str(uuid5(package.NAMESPACE, "polity\x1fNL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNLD"))
SCHEME = "nl.gemeente_ep"
REGISTERED, VALID, INVALID, BLANK, VOTERS, VOTES = 2, 4, 5, 6, 14, 1
# Lists the databank numbers when one party ran several lists in one election.
NUMBERED = re.compile(r" [123]$")
TOTALS_REPORT: dict[str, str] = {}


def day(code: str) -> date:
    return date.fromisoformat(f"{code[2:6]}-{code[6:8]}-{code[8:10]}")


def page_url(code: str) -> str:
    return f"{HOST}/verkiezingen/detail/{code}"


def csv_url(code: str) -> str:
    return f"{HOST}/uitslag/verkiezing/{code}/gemeenten"


def region_json_url(code: str, region: int) -> str:
    return f"{HOST}/verkiezingen/detailJson/{code}/{region}"


def region_csv_url(code: str, region: int) -> str:
    return f"{HOST}/uitslag/verkiezing/{code}/stemregio/{region}"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", changeset.slug(text).replace("-", " ")).split())


def clean(text: object) -> str:
    return " ".join(str(text).split())


def bare(label: str) -> str:
    return NUMBERED.sub("", clean(label))


def party(label: str) -> str:
    return key("party", fold(bare(label)))


def area(number: str) -> str:
    return key("area", SCHEME, number)


def unit(number: str) -> str:
    return key("unit", SCHEME, number)


KIESRAAD = key("source", "kiesraad.databank")
CBS = key("source", "cbs.gemeente")


@functools.cache
def context() -> ssl.SSLContext:
    """The platform's trusted roots plus the Sectigo intermediate the host leaves out of its chain."""
    artifact = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt")
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(artifact.path.read_bytes()))
    return ctx


def _whole(text: object) -> int | None:
    digits = re.sub(r"\D", "", str(text or ""))
    return int(digits) if digits else None


@functools.cache
def embedded(code: str) -> dict[str, Any]:
    """The result document a detail page embeds."""
    text = raw.cached(SOURCE, page_url(code)).path.read_text(encoding="utf-8")
    found = re.search(r'<textarea id="UitslagData"[^>]*>(.*?)</textarea>', text, re.DOTALL)
    assert found is not None, f"{code}: the page embeds no result"
    data: dict[str, Any] = json.loads(html.unescape(found.group(1)))
    assert data["Info"]["Code"] == code
    return data


class National(NamedTuple):
    registered: int | None
    voters: int | None
    valid: int
    blank: int | None
    invalid: int | None
    note: str
    lists: dict[str, tuple[int | None, int | None]]


@functools.cache
def national(code: str) -> National:
    data = embedded(code)
    region = data["Stemregio"]
    valid = _whole(region["AantalGeldigeStemmen"])
    assert valid is not None
    return National(
        registered=_whole(region["Kiesgerechtigden"]),
        voters=_whole(region["Opkomst"]),
        valid=valid,
        blank=_whole(region["AantalBlancoStemmen"]),
        invalid=_whole(region["AantalOngeldigeStemmen"]),
        note=clean(data["Info"]["Opmerking"] or ""),
        lists={
            clean(row["Naam"]): (_whole(row["AantalStemmen"]), _whole(row["AantalZetels"])) for row in region["Partij"]
        },
    )


def regions(code: str) -> list[tuple[int, str]]:
    """The municipality regions of a page, as (region id, name)."""
    levels = embedded(code)["Regios"]["Regios"]
    options = next(level["Options"] for level in levels if level["Name"] == "Gemeente")
    return [(int(option["Id"]), clean(option["Value"])) for option in options if option["Value"] != "-"]


def region_files(code: str) -> list[raw.Artifact]:
    out: list[raw.Artifact] = []
    for region, _ in regions(code):
        out.append(raw.cached(SOURCE, region_json_url(code, region)))
        out.append(raw.cached(SOURCE, region_csv_url(code, region)))
    return out


@functools.cache
def european(code: str) -> pl.DataFrame:
    """Each municipality's votes for each list in a European Parliament election."""
    frame = pl.read_csv(
        raw.cached(SOURCE, csv_url(code)).path,
        separator=";",
        encoding="utf8",
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    frame = frame.select(
        name=pl.col("Gemeente").str.strip_chars().str.replace_all(r"\s+", " "),
        code=pl.col("Code").str.strip_chars().str.strip_prefix("G"),
        list=pl.col("Partij").str.strip_chars().str.replace_all(r"\s+", " "),
        votes=pl.col("AantalStemmen").str.strip_chars().cast(pl.Int64),
    )
    assert frame["code"].str.contains(r"^\d{4}$").all(), f"{code}: unexpected municipality codes"
    assert not frame.select("code", "list").is_duplicated().any(), f"{code}: a list repeats in a municipality"
    return frame


class Municipality(NamedTuple):
    code: str
    name: str
    figures: dict[int, int]
    answers: dict[str, int]


@functools.cache
def referendum() -> list[Municipality]:
    """Each municipality's figures and votes in the referendum, from its own page and CSV."""
    out: list[Municipality] = []
    for region, label in regions(REFERENDUM):
        data = json.loads(raw.cached(SOURCE, region_json_url(REFERENDUM, region)).path.read_text(encoding="utf-8"))
        stem = data["Stemregio"]
        assert int(stem["StemregioId"]) == region
        rows = pl.read_csv(
            raw.cached(SOURCE, region_csv_url(REFERENDUM, region)).path,
            separator=";",
            encoding="utf8",
            infer_schema=False,
            truncate_ragged_lines=True,
        ).select(
            pl.col("Regio").str.strip_chars(),
            pl.col("Code").str.strip_chars().str.strip_prefix("G"),
            pl.col("Type").str.strip_chars(),
            pl.col("AantalStemmen").str.strip_chars(),
        )
        codes = rows["Code"].unique().to_list()
        assert len(codes) == 1 and re.fullmatch(r"\d{4}", codes[0]), f"{label}: unexpected municipality codes"
        found = {kind: _whole(text) for kind, text in rows.select("Type", "AantalStemmen").iter_rows()}
        measures = {
            "Kiesgerechtigden": REGISTERED,
            "Opkomst": VOTERS,
            "AantalGeldigeStemmen": VALID,
            "AantalBlancoStemmen": BLANK,
            "AantalOngeldigeStemmen": INVALID,
        }
        assert set(found) <= set(measures), f"{label}: unexpected figures {set(found) - set(measures)}"
        figures = {measures[kind]: number for kind, number in found.items() if number is not None}
        answers = {clean(row["Naam"]): _whole(row["AantalStemmen"]) for row in stem["Partij"]}
        assert all(number is not None for number in answers.values()), f"{label}: an answer has no votes"
        out.append(Municipality(codes[0], clean(rows["Regio"][0]), figures, {k: v or 0 for k, v in answers.items()}))
    assert len({m.code for m in out}) == len(out), "a municipality code repeats in the referendum"
    return out


def spellings(code: str) -> dict[str, str]:
    """Each municipality's name in one election, by code."""
    if code == REFERENDUM:
        return {m.code: m.name for m in referendum()}
    return {number: name for number, name in european(code).select("code", "name").unique().iter_rows()}


@functools.cache
def municipalities() -> dict[str, dict[str, date]]:
    """Each municipality code's spellings, with the latest day each was used."""
    out: dict[str, dict[str, date]] = {}
    for code in CODES:
        for number, name in spellings(code).items():
            seen = out.setdefault(number, {})
            seen[name] = max(seen.get(name, day(code)), day(code))
    return out


def package_key(code: str) -> str:
    return f"{SOURCE}/{code[:2].lower()}-{day(code)}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(package_key(code) for code in CODES)]


PLAN_YEARS = range(1995, 2006)
PDOK = "https://service.pdok.nl/cbs/gebiedsindelingen"


def shape_url(year: int) -> str:
    return (
        f"{PDOK}/{year}/wfs/v1_0?service=WFS&version=2.0.0&request=GetFeature&typeNames=gebiedsindelingen:"
        "gemeente_gegeneraliseerd&outputFormat=application/json&srsName=EPSG:4326&count=2000"
    )


def fetch() -> None:
    ctx = context()
    for code in CODES:
        raw.fetch(SOURCE, page_url(code), suffix=".html", verify=ctx)
        if code != REFERENDUM:
            raw.fetch(SOURCE, csv_url(code), suffix=".csv", verify=ctx)
    for region, _ in regions(REFERENDUM):
        raw.fetch(SOURCE, region_json_url(REFERENDUM, region), suffix=".json", verify=ctx)
        raw.fetch(SOURCE, region_csv_url(REFERENDUM, region), suffix=".csv", verify=ctx)
    for year in PLAN_YEARS:
        raw.fetch(SOURCE, shape_url(year), suffix=".json")


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    registry().write()
    geo().write()
    for code in CODES:
        build(code).write()
    for line in TOTALS_REPORT.values():
        print(line)
    print(f"{len(unshaped())} of {len(municipalities())} municipality codes have no shape")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Dutch municipalities and parties of the European Parliament elections 1979-2004 and the 2005 referendum "
        "from the Kiesraad's databank",
    )
    p.external |= {NETHERLANDS, COUNTRY}
    p.add(
        "source",
        id=KIESRAAD,
        kind="official_body",
        publisher="Kiesraad",
        title="Databank Verkiezingsuitslagen",
        url=HOST,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kiesraad",
    )
    for code in CODES:
        p.artifact(raw.cached(SOURCE, page_url(code)), KIESRAAD)
        if code != REFERENDUM:
            p.artifact(raw.cached(SOURCE, csv_url(code)), KIESRAAD)
    for artifact in region_files(REFERENDUM):
        p.artifact(artifact, KIESRAAD)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=NETHERLANDS,
        kind="administrative",
        rank=2,
        noun="municipality",
        code_system="cbs",
    )
    for number, spelt in sorted(municipalities().items()):
        id = area(number)
        ranked = sorted(spelt, key=lambda text: (spelt[text], text), reverse=True)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=number)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        for rank, text in enumerate(ranked):
            p.name(id, text, "nl", "official", rank == 0)
        p.add(
            "reporting_unit",
            id=unit(number),
            polity_id=NETHERLANDS,
            scheme_code=SCHEME,
            code=number,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
    labels: dict[str, Counter[str]] = {}
    for code in EUROPEAN:
        for text in set(european(code)["list"].to_list()) | set(national(code).lists):
            labels.setdefault(fold(bare(text)), Counter())[bare(text)] += 1
    for folded, counts in sorted(labels.items()):
        id = key("party", folded)
        ranked = sorted(counts, key=lambda text: (counts[text], len(text), text), reverse=True)
        p.add("party", id=id, kind="party", polity_id=NETHERLANDS)
        p.add("external_id", entity_id=id, scheme="nl.databank_ep.party", value=folded)
        for rank, text in enumerate(ranked):
            p.name(id, text, "nl", "official", rank == 0)
    return p


def _polygons(feature: dict[str, Any]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
    parts = [part for part in shapely.get_parts(found) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _same(a: shapely.Geometry, b: shapely.Geometry) -> bool:
    return shapely.symmetric_difference(a, b).area <= 0.002 * max(a.area, b.area)


@functools.cache
def shapes() -> dict[str, list[tuple[int, shapely.Geometry]]]:
    """The shapes by municipality code, each kept from the first plan year it appears in or changes in."""
    out: dict[str, list[tuple[int, shapely.Geometry]]] = {}
    for year in PLAN_YEARS:
        data = json.loads(raw.cached(SOURCE, shape_url(year)).path.read_bytes())
        for feature in data["features"]:
            number = str(feature["properties"]["statcode"]).removeprefix("GM")
            shape = _polygons(feature)
            history = out.setdefault(number, [])
            if not history or not _same(history[-1][1], shape):
                history.append((year, shape))
    return out


@functools.cache
def last_plan() -> dict[str, int]:
    """The last plan year each municipality code appears in."""
    out: dict[str, int] = {}
    for year in PLAN_YEARS:
        data = json.loads(raw.cached(SOURCE, shape_url(year)).path.read_bytes())
        for feature in data["features"]:
            out[str(feature["properties"]["statcode"]).removeprefix("GM")] = year
    return out


def unshaped() -> list[str]:
    """The municipality codes the elections use that the plans of 1995-2005 do not draw."""
    return sorted(number for number in municipalities() if number not in shapes())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of the Dutch municipalities that existed from 1995, from Statistics Netherlands' generalized "
        "area divisions of each year 1995-2005",
        depends=[f"{SOURCE}/registry"],
    )
    p.add(
        "source",
        id=CBS,
        kind="official_body",
        publisher="Centraal Bureau voor de Statistiek (CBS), through PDOK",
        title="CBS Gebiedsindelingen, municipalities (generalized), 1995-2005",
        url="https://www.pdok.nl/introductie/-/article/cbs-gebiedsindelingen",
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Centraal Bureau voor de Statistiek (CBS), Kadaster; PDOK",
        notes="One plan for each year 1995-2005, as the area division stood on 1 January.",
    )
    for year in PLAN_YEARS:
        p.artifact(raw.cached(SOURCE, shape_url(year)), CBS)
    ends = last_plan()
    for number, history in sorted(shapes().items()):
        if number not in municipalities():
            continue
        for index, (year, shape) in enumerate(history):
            following = history[index + 1][0] if index + 1 < len(history) else None
            if following is None and ends[number] < PLAN_YEARS[-1]:
                following = ends[number] + 1
            p.add(
                "area_geometry",
                area_id=area(number),
                valid_from=date(year, 1, 1),
                valid_to=date(following, 1, 1) if following else None,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=CBS,
            )
    return p


METHOD = (
    "Votes for each list in each municipality, as the Kiesraad's Databank Verkiezingsuitslagen publishes them in its "
    "CSV of municipal results (CC0). Municipalities are the ones of the election's day, identified by their "
    "Statistics Netherlands code, and the archive keeps one reporting unit for each code across elections. The "
    "databank publishes no municipal electorate, turnout, blank votes, or invalid votes for these elections, so only "
    "list votes are stored by municipality. The national figures and the seats of each list are the databank's own."
)
REFERENDUM_METHOD = (
    "Votes for and against in each municipality, with its electorate, turnout, valid, blank, and invalid votes, as "
    "the Kiesraad's Databank Verkiezingsuitslagen publishes them on each municipality's page and in its CSV (CC0). "
    "Municipalities are the ones of the day, identified by their Statistics Netherlands code. Valid votes are the "
    "votes for and against; blank votes are counted apart, and turnout is the valid and blank votes together. The "
    "databank publishes no invalid votes for some municipalities."
)
QUESTION_PREFIX = "The question on the ballot (in Dutch): "


def _election(p: Package, code: str) -> tuple[str, str]:
    when = day(code)
    election = key("election", code)
    p.add(
        "election",
        id=election,
        polity_id=NETHERLANDS,
        kind="referendum" if code == REFERENDUM else "general",
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    if code == REFERENDUM:
        p.name(election, f"{when.year} Dutch advisory referendum", "en", "official")
        p.name(election, f"Raadgevend referendum {when.year}", "nl", "native", False)
        base = f"nl/{when}-referendum"
    else:
        p.name(election, f"{when.year} European Parliament election in the Netherlands", "en", "official")
        p.name(election, f"Europese verkiezing {when.year}", "nl", "native", False)
        base = f"nl/{when}-europees-parlement"
    p.add("slug", path=base, entity_id=election, is_current=True)
    return election, base


def build(code: str) -> Package:
    when = day(code)
    p = Package(
        package_key(code),
        SOURCE,
        (
            f"Dutch advisory referendum of {when} by municipality from the Kiesraad's databank"
            if code == REFERENDUM
            else f"European Parliament election in the Netherlands of {when} by municipality from the Kiesraad's "
            "databank"
        ),
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NETHERLANDS, COUNTRY}
    p.uses(raw.cached(SOURCE, page_url(code)))
    if code == REFERENDUM:
        for artifact in region_files(code):
            p.uses(artifact)
    else:
        p.uses(raw.cached(SOURCE, csv_url(code)))
    election, base = _election(p, code)
    p.frame(
        "result_value", _referendum(p, election, base) if code == REFERENDUM else _european(p, code, election, base)
    )
    return p


def _european(p: Package, code: str, election: str, base: str) -> pl.DataFrame:
    whole = national(code)
    frame = european(code)
    contest = key("contest", code)
    seats = sum(won or 0 for _, won in whole.lists.values())
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        district_area_id=COUNTRY,
        magnitude=seats or None,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Europees Parlement", "nl", "official")
    p.add("slug", path=f"{base}/europees-parlement", entity_id=contest, is_current=True)
    options: dict[str, str] = {}
    for text in sorted(set(frame["list"].to_list()) | set(whole.lists), key=lambda text: (fold(text), text)):
        options[text] = key("option", code, text)
        p.add(
            "contest_option",
            id=options[text],
            contest_id=contest,
            kind="list",
            party_id=party(text),
            ballot_label=text,
        )
    summed = dict(frame.group_by("list").agg(pl.col("votes").sum()).iter_rows())
    tally = int(frame["votes"].sum())
    valid = whole.valid
    result_set = key("result_set", code, "municipal")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=KIESRAAD,
        status="active",
        is_default=True,
        completeness=round(min(tally / valid, 1.0), 6),
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    figures = [
        ("registered voters", whole.registered),
        ("voters", whole.voters),
        ("valid votes", valid),
        ("blank votes", whole.blank),
        ("invalid votes", whole.invalid),
    ]
    stated = "; ".join(f"{label} {number:,}" for label, number in figures if number is not None)
    text = (
        f"The databank's national figures for this election: {stated}. The municipal list votes add up to {tally:,}, "
        f"{tally / valid:.2%} of the national valid votes."
    )
    if tally != valid:
        text += f" The national figure is {valid - tally:,} votes {'higher' if valid > tally else 'lower'}."
    p.add("note", id=key("note", result_set, "national"), entity_id=result_set, kind="caveat", body_md=text, lang="en")
    if whole.note:
        p.add(
            "note",
            id=key("note", result_set, "source"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The databank notes (in Dutch): {whole.note}",
            lang="nl",
        )
    agree = 0
    declared = {text: votes for text, (votes, _) in whole.lists.items() if votes is not None}
    for text, votes in sorted(declared.items()):
        if summed.get(text) == votes:
            agree += 1
            p.add("declared", result_set_id=result_set, option_id=options[text], measure=VOTES, value=votes)
    for text, (_, won) in sorted(whole.lists.items()):
        if won:
            p.add("outcome", result_set_id=result_set, option_id=options[text], kind="won", seats=won)
    TOTALS_REPORT[code] = (
        f"{code}: municipal list votes {tally:,} vs national valid {valid:,} ({tally / valid:.4%}); "
        f"{agree} of {len(declared)} national list totals agree; {seats} seats"
    )
    return frame.select(
        result_set_id=pl.lit(result_set),
        unit_id=pl.col("code").map_elements(unit, return_dtype=pl.String),
        option_id=pl.col("list").replace_strict(options, return_dtype=pl.String),
        measure=pl.lit(VOTES, pl.Int16),
        value=pl.col("votes"),
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
    )


def _referendum(p: Package, election: str, base: str) -> pl.DataFrame:
    code = REFERENDUM
    whole = national(code)
    contest = key("contest", code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        district_area_id=COUNTRY,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Nationaal referendum", "nl", "official")
    p.add("slug", path=f"{base}/referendum", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type="referendum")
    question = clean(embedded(code)["Stemregio"]["Referendumvraag"] or "")
    if question:
        p.add(
            "note",
            id=key("note", contest, "question"),
            entity_id=contest,
            kind="context",
            body_md=f"{QUESTION_PREFIX}{question}",
            lang="nl",
        )
    options = {text: key("option", code, text) for text in sorted(whole.lists)}
    for text, id in options.items():
        p.add("contest_option", id=id, contest_id=contest, kind="answer", ballot_label=text)
    members = referendum()
    got: Counter[str] = Counter()
    for m in members:
        assert set(m.answers) == set(options), f"{m.name}: unexpected answers {set(m.answers)}"
        got.update(m.answers)
    tally = sum(got.values())
    registered = sum(m.figures.get(REGISTERED, 0) for m in members)
    result_set = key("result_set", code, "municipal")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=KIESRAAD,
        status="active",
        is_default=True,
        completeness=round(min(tally / whole.valid, 1.0), 6),
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=REFERENDUM_METHOD,
        lang="en",
    )
    figures = [
        ("registered voters", whole.registered),
        ("voters", whole.voters),
        ("valid votes", whole.valid),
        ("blank votes", whole.blank),
        ("invalid votes", whole.invalid),
    ]
    stated = "; ".join(f"{label} {number:,}" for label, number in figures if number is not None)
    p.add(
        "note",
        id=key("note", result_set, "national"),
        entity_id=result_set,
        kind="caveat",
        body_md=f"The databank's national figures for this referendum: {stated}. The {len(members)} municipalities' "
        f"votes for and against add up to {tally:,}, {tally / whole.valid:.2%} of the national valid votes, and "
        f"their electorates to {registered:,}.",
        lang="en",
    )
    agree = 0
    for text, (votes, _) in sorted(whole.lists.items()):
        if votes is not None and got[text] == votes:
            agree += 1
            p.add("declared", result_set_id=result_set, option_id=options[text], measure=VOTES, value=votes)
    top = max(whole.lists, key=lambda text: whole.lists[text][0] or 0)
    p.add("outcome", result_set_id=result_set, option_id=options[top], kind="won")
    TOTALS_REPORT[code] = (
        f"{code}: municipal votes for and against {tally:,} vs national valid {whole.valid:,}; "
        f"{agree} of {len(whole.lists)} national answer totals agree; electorates {registered:,} vs "
        f"{whole.registered or 0:,}"
    )
    rows: list[tuple[str, str | None, int, int]] = []
    for m in members:
        for text, votes in m.answers.items():
            rows.append((m.code, options[text], VOTES, votes))
        for measure, number in m.figures.items():
            rows.append((m.code, None, measure, number))
    return pl.DataFrame(
        {
            "result_set_id": [result_set] * len(rows),
            "unit_id": [unit(number) for number, *_ in rows],
            "option_id": [option for _, option, *_ in rows],
            "measure": pl.Series([measure for _, _, measure, _ in rows], dtype=pl.Int16),
            "value": pl.Series([value for *_, value in rows], dtype=pl.Int64),
            "channel": pl.Series([0] * len(rows), dtype=pl.Int16),
            "count_round": pl.Series([0] * len(rows), dtype=pl.Int16),
            "flags": pl.Series([0] * len(rows), dtype=pl.Int16),
        },
        schema_overrides={"option_id": pl.String},
    )
