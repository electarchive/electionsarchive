"""Dutch provincial (Provinciale Staten) and municipal (gemeenteraad) elections 1931-2011 by municipality, from the
Kiesraad's Databank Verkiezingsuitslagen (CC0).

For each election the databank serves a detail page, which embeds the national result, and a CSV of every
municipality's votes per list. A provincial election also has a page, a JSON document, and a CSV for each province,
which carry the province's electorate, ballots, and each list's votes and seats; the province's document names the
municipalities that lie in it.
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

SOURCE = "nl.verkiezingsuitslagen"
HOST = "https://www.verkiezingsuitslagen.nl"
# The host serves its leaf certificate without the intermediate that signed it.
INTERMEDIATE = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"
PROVINCIAL = [
    "PS19310422",
    "PS19350417",
    "PS19390419",
    "PS19460529",
    "PS19500426",
    "PS19540421",
    "PS19580326",
    "PS19620328",
    "PS19660323",
    "PS19700318",
    "PS19740327",
    "PS19780329",
    "PS19820324",
    "PS19870318",
    "PS19910306",
    "PS19950308",
    "PS19990303",
    "PS20030311",
    "PS20070307",
    "PS20110302",
]
MUNICIPAL = [
    "GR19310617",
    "GR19350626",
    "GR19390614",
    "GR19460726",
    "GR19490622",
    "GR19530527",
    "GR19531209",
    "GR19531231",
    "GR19541231",
    "GR19551231",
    "GR19560321",
    "GR19560620",
    "GR19571231",
    "GR19580528",
    "GR19611115",
    "GR19620502",
    "GR19620530",
    "GR19651118",
    "GR19660601",
    "GR19661231",
    "GR19700603",
    "GR19731231",
    "GR19740529",
    "GR19740626",
    "GR19780531",
    "GR19781231",
    "GR19790516",
    "GR19791231",
    "GR19811021",
    "GR19820602",
    "GR19820915",
    "GR19831231",
    "GR19841231",
    "GR19851127",
    "GR19860319",
    "GR19861126",
    "GR19900321",
    "GR19940302",
    "GR19980304",
    "GR20020306",
    "GR20060307",
    "GR20091122",
    "GR20100303",
    "GR20101124",
    "GR20111123",
]
CODES = [*PROVINCIAL, *MUNICIPAL]


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


@functools.cache
def context() -> ssl.SSLContext:
    """The platform's trusted roots plus the Sectigo intermediate the host leaves out of its chain."""
    artifact = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt")
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(artifact.path.read_bytes()))
    return ctx


def embedded(code: str) -> dict[str, Any]:
    """The result document a detail page embeds."""
    text = raw.cached(SOURCE, page_url(code)).path.read_text(encoding="utf-8")
    found = re.search(r'<textarea id="UitslagData"[^>]*>(.*?)</textarea>', text, re.DOTALL)
    assert found is not None, f"{code}: the page embeds no result"
    data = json.loads(html.unescape(found.group(1)))
    assert data["Info"]["Code"] == code
    return data


def _whole(text: object) -> int | None:
    digits = re.sub(r"\D", "", str(text or ""))
    return int(digits) if digits else None


def clean(text: object) -> str:
    return " ".join(str(text).split())


class Region(NamedTuple):
    id: int
    name: str
    registered: int | None
    valid: int | None
    blank: int | None
    invalid: int | None
    lists: dict[str, tuple[int | None, int | None]]
    members: list[tuple[int, str]]


def _region(data: dict[str, Any]) -> Region:
    stem = data["Stemregio"]
    assert isinstance(stem, dict)
    levels = data["Regios"]["Regios"]
    members = [
        (int(option["Id"]), clean(option["Value"]))
        for level in levels
        if level["Name"] == "Gemeente"
        for option in level["Options"]
        if option["Value"] != "-"
    ]
    return Region(
        id=int(stem["StemregioId"]),
        name=clean(stem["Naam"]),
        registered=_whole(stem["Kiesgerechtigden"]),
        valid=_whole(stem["AantalGeldigeStemmen"]),
        blank=_whole(stem["AantalBlancoStemmen"]),
        invalid=_whole(stem["AantalOngeldigeStemmen"]),
        lists={
            clean(row["Naam"]): (_whole(row["AantalStemmen"]), _whole(row["AantalZetels"])) for row in stem["Partij"]
        },
        members=members,
    )


@functools.cache
def national(code: str) -> Region:
    """The page's own result: the whole country in a regular election, else the first municipality listed."""
    return _region(embedded(code))


def has_national(code: str) -> bool:
    return national(code).name == "Nederland"


def provinces(code: str) -> list[tuple[int, str]]:
    """The province regions of a provincial election's page, as (region id, name)."""
    levels = embedded(code)["Regios"]["Regios"]
    options = next(level["Options"] for level in levels if level["Name"] == "Provincie")
    return [(int(option["Id"]), clean(option["Value"])) for option in options if option["Value"] != "-"]


@functools.cache
def province(code: str, region: int) -> Region:
    path = raw.cached(SOURCE, region_json_url(code, region)).path
    return _region(json.loads(path.read_text(encoding="utf-8")))


@functools.cache
def municipal(code: str) -> pl.DataFrame:
    """Each municipality's votes for each list, as the databank's CSV gives them. Lists that drew no votes in a
    municipality are left out: the file names every list of the election in every municipality."""
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
        seats=pl.col("AantalZetels").str.strip_chars().cast(pl.Int64, strict=False),
    )
    assert frame["code"].str.contains(r"^\d{4}$").all(), f"{code}: unexpected municipality codes"
    # The 2006 file repeats a blank list in two municipalities, identically.
    frame = frame.unique(maintain_order=True)
    assert not frame.select("code", "list").is_duplicated().any(), f"{code}: a list repeats in a municipality"
    return frame.filter(pl.col("votes") > 0)


@functools.cache
def spellings(code: str) -> dict[str, str]:
    """Each municipality's name in one election."""
    frame = pl.read_csv(
        raw.cached(SOURCE, csv_url(code)).path, separator=";", infer_schema=False, truncate_ragged_lines=True
    )
    return {
        row[1].strip().removeprefix("G"): clean(row[0]) for row in frame.select("Gemeente", "Code").unique().iter_rows()
    }


def ambiguous(code: str) -> set[str]:
    """Names that two municipalities of one election share, as Bergen and Hengelo do."""
    names = Counter(spellings(code).values())
    return {name for name, n in names.items() if n > 1}


def fetch() -> None:
    ctx = context()
    for code in CODES:
        raw.fetch(SOURCE, page_url(code), suffix=".html", verify=ctx)
        raw.fetch(SOURCE, csv_url(code), suffix=".csv", verify=ctx)
    for code in PROVINCIAL:
        twins = ambiguous(code)
        for region, _ in provinces(code):
            raw.fetch(SOURCE, region_json_url(code, region), suffix=".json", verify=ctx)
            raw.fetch(SOURCE, region_csv_url(code, region), suffix=".csv", verify=ctx)
            # A name two municipalities share needs the municipality's own file to tell which one lies here.
            for member, name in province(code, region).members:
                if name in twins:
                    raw.fetch(SOURCE, region_csv_url(code, member), suffix=".csv", verify=ctx)
    fetch_shapes()


@functools.cache
def membership(code: str) -> dict[str, tuple[int, str]]:
    """The province (region id, name) of each municipality code in a provincial election."""
    names = spellings(code)
    by_name: dict[str, list[str]] = {}
    for number, name in names.items():
        by_name.setdefault(name, []).append(number)
    out: dict[str, tuple[int, str]] = {}
    for region, label in provinces(code):
        for member, name in province(code, region).members:
            numbers = by_name.get(name, [])
            if len(numbers) > 1:
                path = raw.cached(SOURCE, region_csv_url(code, member)).path
                found = pl.read_csv(path, separator=";", infer_schema=False, truncate_ragged_lines=True)["Code"]
                numbers = [found[0].removeprefix("G")]
            for number in numbers:
                assert out.setdefault(number, (region, label)) == (region, label), f"{code}: {number} in two provinces"
    return out


NETHERLANDS = str(uuid5(package.NAMESPACE, "polity\x1fNL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNLD"))
MUNICIPALITY_SCHEME = "nl.gemeente_local"
PROVINCE_SCHEME = "nl.provincie_local"
VOTES = 1
PROVINCE_CODES = {
    "groningen": "NL-GR",
    "fryslan": "NL-FR",
    "drenthe": "NL-DR",
    "overijssel": "NL-OV",
    "flevoland": "NL-FL",
    "gelderland": "NL-GE",
    "utrecht": "NL-UT",
    "noord holland": "NL-NH",
    "zuid holland": "NL-ZH",
    "zeeland": "NL-ZE",
    "noord brabant": "NL-NB",
    "limburg": "NL-LI",
}
# Lists the databank numbers when one party ran several lists in one election.
NUMBERED = re.compile(r" [123]$")
OTHERS = re.compile(r"^Overige partij", re.IGNORECASE)
TOTALS_REPORT: dict[str, str] = {}
SEATS_NOTE = (
    "The databank's CSV carries a seat count beside some provincial lists in some municipalities; those are the "
    "lists' seats in the whole province, repeated on each municipality's row, and are not stored by municipality."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", changeset.slug(text).replace("-", " ")).split())


KIESRAAD = key("source", "kiesraad.databank")
GEO_SOURCES = {
    "gemeente": key("source", "cbs.gemeente"),
    "provincie": key("source", "cbs.provincie"),
}


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit(number: str) -> str:
    return key("unit", MUNICIPALITY_SCHEME, number)


def province_code(name: str) -> str:
    return PROVINCE_CODES[fold(name)]


def kind_of(election: str) -> str:
    return "provinciale-staten" if election.startswith("PS") else "gemeenteraad"


def package_key(code: str) -> str:
    return f"{SOURCE}/{code[:2].lower()}-{day(code)}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(code) for code in CODES)]


def bare(label: str) -> str:
    return NUMBERED.sub("", clean(label))


@functools.cache
def party_uris() -> dict[str, str]:
    """The databank's own party key for a list label, where its pages tie one to a party page."""
    found: dict[str, set[str]] = {}
    pages = []
    for code in CODES:
        try:
            pages.append(embedded(code))
        except AssertionError:
            continue
    for code in PROVINCIAL:
        for region, _ in provinces(code):
            pages.append(json.loads(raw.cached(SOURCE, region_json_url(code, region)).path.read_text()))
    for page in pages:
        for row in page["Stemregio"]["Partij"]:
            if row["PartijUri"]:
                found.setdefault(fold(bare(row["Naam"])), set()).add(str(row["PartijUri"]))
    return {label: next(iter(uris)) for label, uris in found.items() if len(uris) == 1}


def party(label: str) -> str:
    folded = fold(bare(label))
    return key("party", party_uris().get(folded, folded))


def is_others(label: str) -> bool:
    return bool(OTHERS.match(label))


@functools.cache
def labels() -> dict[str, Counter[str]]:
    """Every spelling of each party's list label, with how many elections use it."""
    out: dict[str, Counter[str]] = {}
    for code in CODES:
        found = set(municipal(code)["list"].to_list())
        if code in PROVINCIAL:
            for region, _ in provinces(code):
                found |= set(province(code, region).lists)
        else:
            try:
                found |= set(national(code).lists) if has_national(code) else set()
            except AssertionError:
                pass
        for text in found:
            if not is_others(text):
                out.setdefault(party(text), Counter())[bare(text)] += 1
    return out


@functools.cache
def placement(code: str) -> tuple[dict[str, tuple[int, str]], list[str]]:
    """Where each municipality's votes belong in a provincial election. The databank's province pages list a
    municipality in one province in some years while that province's own totals count its votes in another (Urk in
    1950 and 1954, for one). When one province's municipalities fall short of its list totals by exactly one
    municipality's votes that another province's municipalities exceed theirs by, that municipality goes where the
    totals count it. Returns the placement and a sentence for each move."""
    homes = dict(membership(code))
    frame = municipal(code)
    totals = dict(frame.group_by("code").agg(pl.col("votes").sum()).iter_rows())
    moves: list[str] = []
    by_id = {region: label for region, label in provinces(code)}
    gap: dict[int, int] = {}
    for region, _ in provinces(code):
        listed = sum(votes or 0 for votes, _ in province(code, region).lists.values())
        held = sum(totals.get(number, 0) for number, (home, _) in homes.items() if home == region)
        gap[region] = held - listed
    for short in [region for region, diff in gap.items() if diff < 0]:
        for over in [region for region, diff in gap.items() if diff == -gap[short] and diff > 0]:
            found = [number for number, (home, _) in homes.items() if home == over and totals.get(number) == gap[over]]
            if len(found) == 1:
                number = found[0]
                moves.append(
                    f"The databank's province pages list {spellings(code)[number]} in {by_id[over]}, but its totals "
                    f"for {by_id[short]} count the municipality's {gap[over]:,} votes; they are stored in "
                    f"{by_id[short]}."
                )
                homes[number] = (short, by_id[short])
                gap[short] = gap[over] = 0
                break
    return homes, moves


@functools.cache
def province_of() -> dict[str, str]:
    """Each municipality's latest province, by the latest provincial election it took part in."""
    out: dict[str, str] = {}
    for code in PROVINCIAL:
        out |= {number: label for number, (_, label) in membership(code).items()}
    return out


@functools.cache
def municipalities() -> dict[str, dict[str, date]]:
    """Each municipality code's spellings, with the latest day each was used."""
    out: dict[str, dict[str, date]] = {}
    for code in CODES:
        for number, name in spellings(code).items():
            out.setdefault(number, {})[name] = max(out.get(number, {}).get(name, day(code)), day(code))
    return out


def body(kind: str, code: str) -> str:
    return key("body", kind, code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Dutch provinces, municipalities, provincial and municipal councils, and parties of the elections of 1931-2011 "
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
        p.artifact(raw.cached(SOURCE, csv_url(code)), KIESRAAD)
        p.artifact(raw.cached(SOURCE, page_url(code)), KIESRAAD)
    for code in PROVINCIAL:
        for artifact in provincial_files(code):
            p.artifact(artifact, KIESRAAD)
    p.add(
        "scheme",
        code=PROVINCE_SCHEME,
        polity_id=NETHERLANDS,
        kind="administrative",
        rank=1,
        noun="province",
        code_system="iso_3166_2",
    )
    p.add(
        "scheme",
        code=MUNICIPALITY_SCHEME,
        polity_id=NETHERLANDS,
        kind="administrative",
        rank=2,
        noun="municipality",
        code_system="cbs",
    )
    for label in sorted(set(province_of().values())):
        number = province_code(label)
        id = area(PROVINCE_SCHEME, number)
        p.add("area", id=id, scheme_code=PROVINCE_SCHEME)
        p.add("area_code", area_id=id, code=number)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, label, "nl", "official")
        p.add("body", id=body("PS", number), polity_id=NETHERLANDS, kind="council")
        p.name(body("PS", number), f"Provinciale Staten van {label}", "nl", "official")
        p.name(body("PS", number), f"Provincial Council of {label}", "en", "official", False)
    home = province_of()
    for number, spelt in sorted(municipalities().items()):
        id = area(MUNICIPALITY_SCHEME, number)
        ranked = sorted(spelt, key=lambda text: (spelt[text], text), reverse=True)
        p.add("area", id=id, scheme_code=MUNICIPALITY_SCHEME)
        p.add("area_code", area_id=id, code=number)
        p.add(
            "area_parent",
            area_id=id,
            parent_id=area(PROVINCE_SCHEME, province_code(home[number])) if number in home else COUNTRY,
        )
        for rank, text in enumerate(ranked):
            p.name(id, text, "nl", "official", rank == 0)
        p.add(
            "reporting_unit",
            id=unit(number),
            polity_id=NETHERLANDS,
            scheme_code=MUNICIPALITY_SCHEME,
            code=number,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
        p.add("body", id=body("GR", number), polity_id=NETHERLANDS, kind="council")
        p.name(body("GR", number), f"Gemeenteraad van {ranked[0]}", "nl", "official")
        p.name(body("GR", number), f"Municipal Council of {ranked[0]}", "en", "official", False)
    for id, counts in sorted(labels().items()):
        ranked = sorted(counts, key=lambda text: (counts[text], len(text), text), reverse=True)
        p.add("party", id=id, kind="party", polity_id=NETHERLANDS)
        p.add("external_id", entity_id=id, scheme="nl.verkiezingsuitslagen.party", value=fold(ranked[0]))
        for rank, text in enumerate(ranked):
            p.name(id, text, "nl", "official", rank == 0)
    return p


def provincial_files(code: str) -> list[raw.Artifact]:
    out = []
    twins = ambiguous(code)
    for region, _ in provinces(code):
        out.append(raw.cached(SOURCE, region_json_url(code, region)))
        out.append(raw.cached(SOURCE, region_csv_url(code, region)))
        out += [
            raw.cached(SOURCE, region_csv_url(code, member))
            for member, name in province(code, region).members
            if name in twins
        ]
    return out


METHOD = (
    "Votes for each list in each municipality, as the Kiesraad's Databank Verkiezingsuitslagen publishes them in its "
    "CSV of municipal results (CC0). Municipalities are the ones of the election's day, identified by their "
    "Statistics Netherlands code, and the archive keeps one reporting unit for each code across elections. The "
    "databank publishes no municipal electorate, blank votes, or invalid votes for these elections, so only list "
    "votes are stored by municipality. A list that drew no votes in a municipality has no value there. The "
    "seats of each list are the databank's own."
)


def _options(p: Package, contest: str, code: str, texts: list[str], scope: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for text in sorted(set(texts), key=lambda text: (fold(text), text)):
        options[text] = key("option", code, scope, text)
        others = is_others(text)
        p.add(
            "contest_option",
            id=options[text],
            contest_id=contest,
            kind="others" if others else "list",
            party_id=None if others or not fold(bare(text)) or fold(text) == "blanco lijst" else party(text),
            ballot_label=text,
        )
    return options


def _election(p: Package, code: str) -> tuple[str, str, str]:
    """The election row, its names and slug; returns the election id and its slug base."""
    when = day(code)
    provincial = code.startswith("PS")
    election = key("election", code)
    nominal = code.endswith("1231") and code.startswith("GR")
    regular = provincial or has_national(code)
    p.add(
        "election",
        id=election,
        polity_id=NETHERLANDS,
        kind="local",
        date_from=date(when.year, 1, 1) if nominal else when,
        date_to=when,
        date_precision="year" if nominal else "day",
        original_text=embedded(code)["Info"]["Date"] if nominal else None,
        status="held",
    )
    if provincial:
        english, native, base = (
            f"{when.year} Dutch provincial elections",
            f"Provinciale Statenverkiezingen {when.year}",
            f"nl/{when}-provinciale-staten",
        )
    elif regular:
        english, native, base = (
            f"{when.year} Dutch municipal elections",
            f"Gemeenteraadsverkiezingen {when.year}",
            f"nl/{when}-gemeenteraad",
        )
    elif nominal:
        english, native, base = (
            f"{when.year} Dutch municipal elections after reorganization",
            f"Gemeenteraadsverkiezingen {when.year} na herindeling",
            f"nl/{when.year}-gemeenteraad-herindeling",
        )
    else:
        english, native, base = (
            f"Dutch municipal elections of {when}",
            f"Gemeenteraadsverkiezingen van {when}",
            f"nl/{when}-gemeenteraad",
        )
    p.name(election, english, "en", "official")
    p.name(election, native, "nl", "native", False)
    p.add("slug", path=base, entity_id=election, is_current=True)
    return election, base, english


def _artifacts(p: Package, code: str) -> None:
    p.uses(raw.cached(SOURCE, csv_url(code)))
    p.uses(raw.cached(SOURCE, page_url(code)))
    if code.startswith("PS"):
        for artifact in provincial_files(code):
            p.uses(artifact)


def build(code: str) -> Package:
    when = day(code)
    kind_word = "provincial" if code.startswith("PS") else "municipal"
    p = Package(
        package_key(code),
        SOURCE,
        f"Dutch {kind_word} elections of {when} by municipality from the Kiesraad's databank",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NETHERLANDS, COUNTRY}
    _artifacts(p, code)
    election, base, _ = _election(p, code)
    frame = municipal(code)
    if code.startswith("PS"):
        values = _provincial(p, code, election, base, frame)
    else:
        values = _municipal(p, code, election, base, frame)
    p.frame("result_value", values)
    return p


def _totals(p: Package, code: str, election: str, tally: int) -> None:
    """Compare the municipal list votes with the databank's national figures, and record the result."""
    when = day(code)
    if code.startswith("PS"):
        valid = sum(province(code, region).valid or 0 for region, _ in provinces(code))
        whole = national(code)
        assert whole.valid == valid, f"{code}: the provinces' valid votes do not add up to the national figure"
    elif has_national(code):
        whole = national(code)
        valid = whole.valid or 0
    else:
        TOTALS_REPORT[str(when) + code[:2]] = (
            f"{code}: municipal list votes {tally:,}; the page gives no national figure (a partial election)"
        )
        return
    figures = [
        ("registered voters", whole.registered),
        ("valid votes", whole.valid),
        ("blank votes", whole.blank),
        ("invalid votes", whole.invalid),
    ]
    stated = "; ".join(f"{label} {number:,}" for label, number in figures if number is not None)
    share = tally / valid if valid else 0.0
    text = (
        f"The databank's national figures for this election: {stated}. The municipal list votes add up to "
        f"{tally:,}, {share:.2%} of the national valid votes."
    )
    if share < 0.98:
        text += (
            " The databank's municipal file is not complete: it leaves out lists or municipalities the national "
            "figure counts."
        )
    p.add("note", id=key("note", election, "national"), entity_id=election, kind="caveat", body_md=text, lang="en")
    TOTALS_REPORT[code] = f"{code}: municipal list votes {tally:,} vs national valid {valid:,} ({share:.4%})"


def _provincial(p: Package, code: str, election: str, base: str, frame: pl.DataFrame) -> pl.DataFrame:
    homes, moves = placement(code)
    assert set(frame["code"]) <= set(homes), f"{code}: municipalities in no province: {set(frame['code']) - set(homes)}"
    for index, text in enumerate(moves):
        p.add(
            "note",
            id=key("note", election, "placement", index),
            entity_id=election,
            kind="caveat",
            body_md=text,
            lang="en",
        )
    pieces: list[pl.DataFrame] = []
    taken: set[str] = set()
    tally = 0
    differing: list[str] = []
    for region, label in provinces(code):
        result = province(code, region)
        number = province_code(label)
        rows = frame.filter(pl.col("code").map_elements(lambda n: homes[n][0], return_dtype=pl.Int64) == region)
        contest = key("contest", code, number)
        seats = sum(won or 0 for _, won in result.lists.values())
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body("PS", number),
            district_area_id=area(PROVINCE_SCHEME, number),
            magnitude=seats or None,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Provinciale Staten {label}", "nl", "official")
        slug = f"{base}/{changeset.slug(label)}"
        assert slug not in taken
        taken.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        wanted = set(rows["list"].to_list()) | {text for text, (votes, _) in result.lists.items() if votes}
        options = _options(p, contest, code, list(wanted), str(number))
        summed = rows.group_by("list").agg(pl.col("votes").sum())
        total = int(summed["votes"].sum())
        tally += total
        valid = result.valid or 0
        result_set = key("result_set", code, number, "municipal")
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
            completeness=round(min(total / valid, 1.0), 6) if valid else None,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        figures = [
            ("registered voters", result.registered),
            ("valid votes", result.valid),
            ("blank votes", result.blank),
            ("invalid votes", result.invalid),
        ]
        stated = "; ".join(f"{name} {number_:,}" for name, number_ in figures if number_ is not None)
        p.add(
            "note",
            id=key("note", result_set, "province"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The databank's figures for the province: {stated}. The municipal list votes add up to "
            f"{total:,}" + (f", {total / valid:.2%} of its valid votes." if valid else "."),
            lang="en",
        )
        got = dict(summed.iter_rows())
        for text, (votes, won) in sorted(result.lists.items()):
            if votes and got.get(text) == votes:
                p.add("declared", result_set_id=result_set, option_id=options[text], measure=VOTES, value=votes)
            elif votes:
                differing.append(f"{label}, {text}: province {votes:,}, municipalities {got.get(text, 0):,}")
            if won:
                p.add("outcome", result_set_id=result_set, option_id=options[text], kind="won", seats=won)
        pieces.append(
            rows.select(
                result_set_id=pl.lit(result_set),
                unit_id=pl.col("code").map_elements(unit, return_dtype=pl.String),
                option_id=pl.col("list").replace_strict(options, return_dtype=pl.String),
                measure=pl.lit(VOTES, pl.Int16),
                value=pl.col("votes"),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            )
        )
    if differing:
        shown = "; ".join(differing[:8]) + ("; and others" if len(differing) > 8 else "")
        p.add(
            "note",
            id=key("note", election, "differing"),
            entity_id=election,
            kind="caveat",
            body_md=f"For {len(differing)} lists the databank's province total is not the sum of its municipal "
            f"rows ({shown}). A province's list total is stored as declared only where the municipal rows add up "
            "to it.",
            lang="en",
        )
    _totals(p, code, election, tally)
    return pl.concat(pieces)


def _municipal(p: Package, code: str, election: str, base: str, frame: pl.DataFrame) -> pl.DataFrame:
    pieces: list[pl.DataFrame] = []
    names = spellings(code)
    counts = Counter(names.values())
    taken: set[str] = set()
    tally = int(frame["votes"].sum())
    for number in sorted(names):
        rows = frame.filter(pl.col("code") == number)
        name = names[number]
        contest = key("contest", code, number)
        seats = int(rows["seats"].sum()) if rows["seats"].is_not_null().any() else None
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body("GR", number),
            district_area_id=area(MUNICIPALITY_SCHEME, number),
            magnitude=seats or None,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Gemeenteraad {name}", "nl", "official")
        slug = f"{base}/{changeset.slug(name)}"
        if counts[name] > 1 or slug in taken:
            slug = f"{slug}-{number}"
        assert slug not in taken
        taken.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        options = _options(p, contest, code, rows["list"].to_list(), number)
        result_set = key("result_set", code, number, "municipal")
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
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for text, won in rows.filter(pl.col("seats") > 0).select("list", "seats").iter_rows():
            p.add("outcome", result_set_id=result_set, option_id=options[text], kind="won", seats=won)
        pieces.append(
            rows.select(
                result_set_id=pl.lit(result_set),
                unit_id=pl.lit(unit(number)),
                option_id=pl.col("list").replace_strict(options, return_dtype=pl.String),
                measure=pl.lit(VOTES, pl.Int16),
                value=pl.col("votes"),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            )
        )
    assert set(frame["code"]) <= set(names)
    _totals(p, code, election, tally)
    return pl.concat(pieces)


PLAN_YEARS = range(1995, 2012)
PDOK = "https://service.pdok.nl/cbs/gebiedsindelingen"
PROVINCE_STAT = {
    "PV20": "NL-GR",
    "PV21": "NL-FR",
    "PV22": "NL-DR",
    "PV23": "NL-OV",
    "PV24": "NL-FL",
    "PV25": "NL-GE",
    "PV26": "NL-UT",
    "PV27": "NL-NH",
    "PV28": "NL-ZH",
    "PV29": "NL-ZE",
    "PV30": "NL-NB",
    "PV31": "NL-LI",
}


def shape_url(year: int, layer: str) -> str:
    return (
        f"{PDOK}/{year}/wfs/v1_0?service=WFS&version=2.0.0&request=GetFeature&typeNames=gebiedsindelingen:"
        f"{layer}_gegeneraliseerd&outputFormat=application/json&srsName=EPSG:4326&count=2000"
    )


def fetch_shapes() -> None:
    for year in PLAN_YEARS:
        for layer in ("gemeente", "provincie"):
            raw.fetch(SOURCE, shape_url(year, layer), suffix=".json")


def _polygons(feature: dict[str, object]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))  # type: ignore[arg-type]
    parts = [part for part in shapely.get_parts(found) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _same(a: shapely.Geometry, b: shapely.Geometry) -> bool:
    return shapely.symmetric_difference(a, b).area <= 0.002 * max(a.area, b.area)


@functools.cache
def shapes(layer: str) -> dict[str, list[tuple[int, shapely.Geometry]]]:
    """The shapes of a layer by area code, each kept from the first plan year it appears in or changes in."""
    out: dict[str, list[tuple[int, shapely.Geometry]]] = {}
    for year in PLAN_YEARS:
        data = json.loads(raw.cached(SOURCE, shape_url(year, layer)).path.read_bytes())
        for feature in data["features"]:
            stat = str(feature["properties"]["statcode"])
            number = PROVINCE_STAT[stat] if layer == "provincie" else stat.removeprefix("GM")
            shape = _polygons(feature)
            history = out.setdefault(number, [])
            if not history or not _same(history[-1][1], shape):
                history.append((year, shape))
    return out


@functools.cache
def last_plan(layer: str) -> dict[str, int]:
    """The last plan year each area code appears in."""
    out: dict[str, int] = {}
    for year in PLAN_YEARS:
        data = json.loads(raw.cached(SOURCE, shape_url(year, layer)).path.read_bytes())
        for feature in data["features"]:
            stat = str(feature["properties"]["statcode"])
            out[PROVINCE_STAT[stat] if layer == "provincie" else stat.removeprefix("GM")] = year
    return out


def unshaped() -> list[str]:
    """The municipality codes the databank's elections use that the plans of 1995-2011 do not draw."""
    drawn = shapes("gemeente")
    return sorted(number for number in municipalities() if number not in drawn)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of the Dutch provinces and of the municipalities that existed from 1995, from Statistics "
        "Netherlands' generalized area divisions of each year 1995-2011",
        depends=[f"{SOURCE}/registry"],
    )
    for layer, noun in (("provincie", "provinces"), ("gemeente", "municipalities")):
        p.add(
            "source",
            id=GEO_SOURCES[layer],
            kind="official_body",
            publisher="Centraal Bureau voor de Statistiek (CBS), through PDOK",
            title=f"CBS Gebiedsindelingen, {noun} (generalized), 1995-2011",
            url="https://www.pdok.nl/introductie/-/article/cbs-gebiedsindelingen",
            license="Creative Commons Attribution 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="Centraal Bureau voor de Statistiek (CBS), Kadaster; PDOK",
            notes="One plan for each year 1995-2011, as the area division stood on 1 January.",
        )
        for year in PLAN_YEARS:
            p.artifact(raw.cached(SOURCE, shape_url(year, layer)), GEO_SOURCES[layer])
    for layer, scheme in (("provincie", PROVINCE_SCHEME), ("gemeente", MUNICIPALITY_SCHEME)):
        ends = last_plan(layer)
        for number, history in sorted(shapes(layer).items()):
            if layer == "gemeente" and number not in municipalities():
                continue
            for index, (year, shape) in enumerate(history):
                following = history[index + 1][0] if index + 1 < len(history) else None
                if following is None and ends[number] < PLAN_YEARS[-1]:
                    following = ends[number] + 1
                p.add(
                    "area_geometry",
                    area_id=area(scheme, number),
                    valid_from=date(year, 1, 1),
                    valid_to=date(following, 1, 1) if following else None,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                    ),
                    quality="official",
                    source_id=GEO_SOURCES[layer],
                )
    return p


def parse() -> None:
    registry().write()
    geo().write()
    for code in CODES:
        build(code).write()
    for line in sorted(TOTALS_REPORT.values()):
        print(line)


def emit() -> None:
    package.run("load", packages())
