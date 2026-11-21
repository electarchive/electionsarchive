"""Direct presidential elections of the First Republic (1894 to 1922), by state.

Under the 1891 Constitution (art. 47) the president was chosen by direct vote on 1 March of the last year of the
term, and Congress counted the votes. The state totals come from the Portuguese Wikipedia article on each election,
pinned to the revision fetched, whose tables transcribe the congressional counting reports and the TSE's almanac of
the First Republic. Shapes for the four units of the period that no current state matches (the old Federal District,
Rio de Janeiro without it, Mato Grosso with Mato Grosso do Sul, and Goiás with Tocantins) are drawn from IBGE's
current meshes.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote
from uuid import uuid5

import shapely

from ea import package, raw
from ea.package import NAMESPACE, Package

SOURCE = "br.primeira_republica"
# Production holds Brazil under the id br.tse minted, not the uuid5 of its ISO code.
BRAZIL = "01a13ba1-cd2c-7065-8397-2903884f736e"
NATION = "01a13ba1-cd80-7234-b7d9-32ca8179b966"
PRESIDENCY = "01a13ba1-d3a7-7589-a5ab-432323275643"
WIKI = "https://pt.wikipedia.org/wiki/"
DAYS = {
    1894: date(1894, 3, 1),
    1898: date(1898, 3, 1),
    1902: date(1902, 3, 1),
    1906: date(1906, 3, 1),
    1910: date(1910, 3, 1),
    1914: date(1914, 3, 1),
    1918: date(1918, 3, 1),
    1919: date(1919, 4, 13),
    1922: date(1922, 3, 1),
    1926: date(1926, 3, 1),
}
# The reporting units production holds for the states of the period, as the 1945 elections use them: the Federal
# District of 1891 to 1960 (the city of Rio de Janeiro), the 1963 states where a later split or merger changed them,
# and the current states elsewhere.
UNITS = {
    "AL": "3dd68b01-ff20-55cb-b28e-c442b40bc155",
    "AM": "f21c5446-ebe2-5656-9d75-834d55c25e74",
    "BA": "ca6354ac-c0f7-5b5b-9f08-8e2876e410ed",
    "CE": "135c5c98-b577-5f47-9ac4-3f3d5cd6cc5b",
    "DF": "e662e828-4d27-5df4-8722-b65b457ad8fa",
    "ES": "f28727d4-ff8a-56a3-ac26-adbe83c93709",
    "GO": "ae9ddd23-0e60-5be8-9e92-31780286ef69",
    "MA": "a9f08461-4699-5b8f-85b8-6eccc6b50c1b",
    "MG": "26d44ced-e531-5f76-8c39-445c8eb02c12",
    "MT": "f2b8d43c-0b66-5aae-bc3f-6d54747a6c89",
    "PA": "8281ba64-00c3-5c6b-b7d6-9d1e45f5b34d",
    "PB": "6ddd2658-e35c-59d7-9915-0dfad35386e1",
    "PE": "dbea6155-fe2d-5f49-8fc1-fcb91f51a9e5",
    "PI": "020c90be-91bb-5bff-ae75-072179866a08",
    "PR": "29fa59a1-9008-519b-b197-50a8bb29adc8",
    "RJ": "0f0da47f-b103-58c6-b929-12c071213de4",
    "RN": "ed54e274-f54f-5d41-8b20-7e17390793fd",
    "RS": "7586c965-2392-58a5-9a97-e70e3693eed0",
    "SC": "52fdbe94-4e8d-5bd5-8667-1c6904af5572",
    "SE": "36e7ba26-bb2b-56b9-aca6-edf0a5b9934c",
    "SP": "18a6b749-cee1-5fdf-a0b6-d002d76915af",
}
STATES = {
    "Alagoas": "AL",
    "Amazonas": "AM",
    "Bahia": "BA",
    "Ceará": "CE",
    "Distrito Federal do Brasil (1891–1960)": "DF",
    "Espírito Santo": "ES",
    "Goiás": "GO",
    "Maranhão": "MA",
    "Minas Gerais": "MG",
    "Mato Grosso": "MT",
    "Pará": "PA",
    "Paraíba": "PB",
    "Pernambuco": "PE",
    "Piauí": "PI",
    "Paraná": "PR",
    "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS",
    "Santa Catarina": "SC",
    "Sergipe": "SE",
    "São Paulo": "SP",
}
# The areas of the 1963 scheme whose units the period uses, with the end of the plan each shape belongs to: the
# city of Rio de Janeiro was the Federal District and then Guanabara until the 1975 merger, Mato Grosso split in 1979,
# and Goiás in 1988.
AREAS_1963 = {
    "GB": ("a0e6f5b0-649a-53e1-9163-ce1e0d7dffdd", date(1975, 3, 15)),
    "RJ": ("b4eebe02-b0fe-54c2-96b1-e3e39f070e8d", date(1975, 3, 15)),
    "MT": ("07f33ec3-8002-55b0-98eb-15afa12ea6b1", date(1979, 1, 1)),
    "GO": ("ca67e6b7-2ff0-53de-8e65-01d238d6e6d5", date(1989, 1, 1)),
}
RIO = "3304557"
MALHAS = "https://servicodados.ibge.gov.br/api/v3/malhas/estados/"
MESHES = {
    "RJ": f"{MALHAS}RJ?formato=application/vnd.geo%2Bjson&qualidade=intermediaria&intrarregiao=municipio",
    **{
        uf: f"{MALHAS}{uf}?formato=application/vnd.geo%2Bjson&qualidade=intermediaria"
        for uf in ("MT", "MS", "GO", "TO")
    },
}
JOINT = "BA-RJ-DF"
NO_ELECTION = "Não houve eleição"
FOOTERS = {
    "Total conforme a fonte": True,
    "Total": False,
    "Total dos valores listados": False,
    "Total transcrito das linhas": False,
    "Soma": False,
}
WIKIPEDIA_SOURCE = (
    "The state figures are those of the table in the Portuguese Wikipedia article on the election, pinned to the "
    "revision fetched. The article cites {cited} for them."
)
SKIPPED = 'The table records no election ("Não houve eleição") in {states} in {year}, so those states have no figures.'
# The articles link one candidate under two spellings of his name.
SAME_PERSON = {"Ruy Barbosa": "Rui Barbosa"}
SEPARATE = (
    "The table also lists {votes} votes taken in separate ballots (votos em separado), which the count set aside; "
    "they are not stored, and the candidates' votes are the ordinary votes only."
)
JOINT_NOTE = (
    "The article gives Bahia, Rio de Janeiro, and the Federal District a single combined row for 1906, so their votes "
    "are stored on one aggregate unit for the three."
)
DECLARED_NOTE = (
    "The table's national totals are those its source prints, which differ from the sum of the state rows by "
    "{diff}; the state rows are stored as given, and the printed totals as declared."
)
FOOTER_NOTE = (
    "The table's footer, which it labels as the sum of its rows, differs from the sum of the state rows by {diff}; "
    "the state rows are stored as given."
)
SYSTEM_NOTE = (
    "Under article 47 of the 1891 Constitution the president was elected by direct vote, and a candidate needed an "
    "absolute majority of the votes; otherwise Congress chose between the two with the most. Every winner here had "
    "an absolute majority."
)


@dataclass
class Candidate:
    person: str
    party: str | None
    abbreviation: str | None


@dataclass
class Table:
    year: int
    title: str
    cited: str
    candidates: list[Candidate]
    separate: bool
    rows: dict[str, list[int | None]]
    skipped: list[str]
    footer: list[str]
    footer_from_source: bool


def title(year: int) -> str:
    return f"Eleição presidencial no Brasil em {year}"


def wiki_url(year: int) -> str:
    return f"https://pt.wikipedia.org/w/index.php?title={quote(title(year).replace(' ', '_'))}&action=raw"


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, wiki_url(year), suffix=".wiki")
    for url in MESHES.values():
        raw.fetch(SOURCE, url, suffix=".json")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/president-{year}" for year in DAYS)]


def parse() -> None:
    tables = {year: table(year) for year in DAYS}
    geo().write()
    registry(tables).write()
    for found in tables.values():
        election(found).write()


def emit() -> None:
    package.run("load", packages())


LINK = re.compile(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]")


def number(cell: str) -> int | None:
    text = cell.strip()
    if text in ("—", "-", ""):
        return None
    if not re.fullmatch(r"\d{1,3}(\.\d{3})*", text):
        raise ValueError(f"not a vote count: {cell!r}")
    return int(text.replace(".", ""))


def cell(line: str) -> str:
    """The text of a wikitext cell line, without its attributes."""
    body = line[1:].strip()
    if "|" in body and not body.startswith("[["):
        attributes, _, rest = body.partition(" | ")
        if rest and ("=" in attributes or attributes.startswith("align")):
            return rest.strip()
    return body


def table(year: int) -> Table:
    text = raw.cached(SOURCE, wiki_url(year)).path.read_text()
    start = text.index('{| class="wikitable')
    lines = text[start : text.index("\n|}", start)].splitlines()
    head = "\n".join(lines[: next(i for i, line in enumerate(lines) if line.startswith("|- align"))])
    cited = re.search(r"\|titulo=([^\n|]+)", head)
    website = re.search(r"\|website=([^\n|]+)", head)
    if cited is None or website is None:
        raise ValueError(f"{year}: the table cites no source")
    candidates = []
    for line in head.splitlines():
        if line.startswith('! colspan="2" |'):
            links = LINK.findall(line)
            person = SAME_PERSON.get(links[0][0], links[0][0])
            party = links[1] if len(links) > 1 else None
            candidates.append(Candidate(person, party[0] if party else None, (party[1] or party[0]) if party else None))
    separate = "Votos em separado" in head
    width = 2 * len(candidates) + separate + 1
    rows: dict[str, list[int | None]] = {}
    skipped: list[str] = []
    footer: list[str] = []
    footer_from_source = False
    block: list[str] = []
    blocks: list[list[str]] = []
    for line in lines[len(head.splitlines()) :]:
        if line.startswith("|-"):
            if block:
                blocks.append(block)
            block = []
        else:
            block.append(line)
    blocks.append(block)
    for block in blocks:
        if block[0].startswith("!"):
            label = block[0][1:].strip()
            if label not in FOOTERS:
                raise ValueError(f"{year}: unknown footer {label!r}")
            footer_from_source = FOOTERS[label]
            footer = [line[1:].strip() for line in block[1:]]
            continue
        states = [STATES[target] for target, _ in LINK.findall(block[0])]
        code = JOINT if len(states) > 1 else states[0]
        if len(states) > 1 and sorted(states) != sorted(JOINT.split("-")):
            raise ValueError(f"{year}: unexpected combined row {states}")
        cells = [cell(line) for line in block[1:]]
        if len(cells) == 1 and cells[0] == NO_ELECTION:
            skipped.append(states[0])
            continue
        if len(cells) != width:
            raise ValueError(f"{year} {code}: {len(cells)} cells, expected {width}")
        rows[code] = [number(cells[2 * i]) for i in range(len(candidates))] + [
            number(cells[-2]) if separate else None,
            number(cells[-1]),
        ]
    found = Table(
        year,
        title(year),
        f"{cited.group(1).strip()} ({website.group(1).strip()})",
        candidates,
        separate,
        rows,
        skipped,
        footer,
        footer_from_source,
    )
    verify(found)
    return found


def verify(found: Table) -> None:
    n = len(found.candidates)
    for code, row in found.rows.items():
        if sum(v or 0 for v in row[:n]) != row[-1]:
            raise ValueError(f"{found.year} {code}: candidates do not add up to the row's total")
    covered = {code for code in found.rows if code != JOINT} | set(found.skipped)
    if JOINT in found.rows:
        covered |= set(JOINT.split("-"))
    if covered != set(STATES.values()):
        raise ValueError(f"{found.year}: states missing {sorted(set(STATES.values()) - covered)}")
    if len(found.footer) != 2 * n + found.separate + 1:
        raise ValueError(f"{found.year}: the footer has {len(found.footer)} cells")


def difference(found: Table) -> list[int]:
    n = len(found.candidates)
    sums = [sum(row[i] or 0 for row in found.rows.values()) for i in range(n)]
    printed = [number(found.footer[2 * i]) or 0 for i in range(n)]
    return [p - s for p, s in zip(printed, sums, strict=True)]


def wiki_source(p: Package, year: int) -> str:
    id = p.id("source", "wikipedia", year)
    p.add(
        "source",
        id=id,
        kind="community",
        publisher="Wikipedia (pt)",
        title=title(year),
        url=WIKI + quote(title(year).replace(" ", "_")),
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="Wikipedia contributors",
    )
    p.artifact(raw.cached(SOURCE, wiki_url(year)), id)
    return id


def _outline(shape: shapely.Geometry) -> tuple[str, str]:
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    polygons = shapely.MultiPolygon(list(shapely.get_parts(parts)))
    polygons = shapely.set_srid(polygons, 4326)
    point = shapely.set_srid(shapely.point_on_surface(polygons), 4326)
    return (
        str(shapely.to_wkb(polygons, hex=True, include_srid=True)),
        str(shapely.to_wkb(point, hex=True, include_srid=True)),
    )


def _features(uf: str) -> list[tuple[str, shapely.Geometry]]:
    data = json.loads(raw.cached(SOURCE, MESHES[uf]).path.read_bytes())
    return [
        (str(feature["properties"].get("codarea", "")), shapely.geometry.shape(feature["geometry"]))
        for feature in data["features"]
    ]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Approximate outlines, drawn from IBGE's current meshes, of the Federal District of 1891 to 1960 and of the "
        "states of Rio de Janeiro, Mato Grosso, and Goiás before their later splits and mergers",
    )
    ibge = p.id("source", "ibge-malhas")
    p.add(
        "source",
        id=ibge,
        kind="official_body",
        publisher="IBGE",
        title="API de malhas geográficas (v3)",
        url="https://servicodados.ibge.gov.br/api/docs/malhas?versao=3",
        attribution="Source: IBGE",
        notes="No license stated; IBGE says its limits are approximate and not an official demarcation.",
    )
    for url in MESHES.values():
        p.artifact(raw.cached(SOURCE, url), ibge)
    rio = _features("RJ")
    city = [shape for code, shape in rio if code == RIO]
    if len(city) != 1 or len(rio) < 90:
        raise ValueError("the Rio de Janeiro mesh lacks the city or its municipalities")
    shapes = {
        "GB": city[0],
        "RJ": shapely.union_all([shape for code, shape in rio if code != RIO], grid_size=1e-6),
        "MT": shapely.union_all([s for uf in ("MT", "MS") for _, s in _features(uf)], grid_size=1e-6),
        "GO": shapely.union_all([s for uf in ("GO", "TO") for _, s in _features(uf)], grid_size=1e-6),
    }
    for code, (area, until) in AREAS_1963.items():
        p.external.add(area)
        geom, point = _outline(shapes[code])
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=None,
            valid_to=until,
            geom=geom,
            label_point=point,
            quality="synthetic",
            source_id=ibge,
        )
    return p


def person(name: str) -> str:
    return str(uuid5(NAMESPACE, f"{SOURCE}\x1fperson\x1f{name}"))


def party(name: str) -> str:
    return str(uuid5(NAMESPACE, f"{SOURCE}\x1fparty\x1f{name}"))


def registry(tables: dict[int, Table]) -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "Candidates and parties of the First Republic's presidential elections")
    p.external.add(BRAZIL)
    people: set[str] = set()
    parties: dict[str, str] = {}
    for found in tables.values():
        for candidate in found.candidates:
            people.add(candidate.person)
            if candidate.party is not None and candidate.abbreviation is not None:
                parties.setdefault(candidate.party, candidate.abbreviation)
    for name in sorted(people):
        id = person(name)
        p.add("person", id=id, gender="male")
        p.name(id, name, "pt", "official")
    for name, abbreviation in sorted(parties.items()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=BRAZIL)
        p.name(id, name, "pt", "official")
        if abbreviation != name:
            p.name(id, abbreviation, "pt", "abbreviation", False)
    return p


def election(found: Table) -> Package:
    year, day = found.year, DAYS[found.year]
    p = Package(
        f"{SOURCE}/president-{year}",
        SOURCE,
        f"The {year} Brazilian presidential election by state, from the Portuguese Wikipedia article's table",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external |= {BRAZIL, NATION, PRESIDENCY}
    source = wiki_source(p, year)
    special = year == 1919
    election_id = p.id("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=BRAZIL,
        kind="special" if special else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    path = f"br/{day}-presidential-election"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    label = "special presidential election" if special else "presidential election"
    p.name(election_id, f"{year} Brazilian {label}", "en", "official")
    p.name(election_id, title(year), "pt", "native", False)
    contest = p.id("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION,
        magnitude=1,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Brazilian {label}", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    p.add("note", id=p.id("note", contest, "system"), entity_id=contest, kind="context", body_md=SYSTEM_NOTE, lang="en")
    result_set = p.id("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )

    def note(key: str, kind: str, text: str) -> None:
        p.add("note", id=p.id("note", result_set, key), entity_id=result_set, kind=kind, body_md=text, lang="en")

    note("source", "methodology", WIKIPEDIA_SOURCE.format(cited=found.cited))
    if found.skipped:
        names = {code: name for name, code in STATES.items()}
        listed = [names[code] for code in sorted(found.skipped)]
        note("skipped", "caveat", SKIPPED.format(states=", ".join(listed[:-1]) + " and " + listed[-1], year=year))
    n = len(found.candidates)
    if found.separate:
        votes = sum(row[n] or 0 for row in found.rows.values())
        note("separate", "caveat", SEPARATE.format(votes=f"{votes:,}"))
    if JOINT in found.rows:
        note("joint", "caveat", JOINT_NOTE)
    diff = difference(found)
    if any(diff):
        shown = ", ".join(f"{c.person} {d:+,}" for c, d in zip(found.candidates, diff, strict=True) if d)
        note("declared", "caveat", (DECLARED_NOTE if found.footer_from_source else FOOTER_NOTE).format(diff=shown))
        if found.footer_from_source:
            p.accept(
                "accounting.declared",
                f"The table prints national totals from its source that differ from the sum of its own state rows "
                f"({shown}); both are stored as printed.",
            )
    units: dict[str, str] = {}
    for code in found.rows:
        if code == JOINT:
            unit = p.id("unit", year, JOINT)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BRAZIL,
                scheme_code="br.uf_1945",
                code=f"{JOINT}:{year}",
                area_relation="within",
                area_id=NATION,
                kind="aggregate",
                valid_from=day,
                valid_to=day,
            )
            p.name(unit, "Bahia, Rio de Janeiro, and the Federal District", "en", "official")
            p.external.add("br.uf_1945")
            units[code] = unit
        else:
            units[code] = UNITS[code]
            p.external.add(UNITS[code])
    totals = [sum(row[i] or 0 for row in found.rows.values()) for i in range(n)]
    winner = max(range(n), key=lambda i: totals[i])
    for position, candidate in enumerate(found.candidates, 1):
        option = p.id("option", year, candidate.person)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(candidate.party) if candidate.party else None,
            ballot_position=position,
            ballot_label=candidate.person,
        )
        p.add(
            "candidacy",
            id=p.id("candidacy", year, candidate.person),
            person_id=person(candidate.person),
            option_id=option,
            role="head",
            ballot_name=candidate.person,
            member_of_party_id=party(candidate.party) if candidate.party else None,
            is_independent=False,
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="elected" if position - 1 == winner else "lost",
        )
        if found.footer_from_source:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=1,
                value=number(found.footer[2 * (position - 1)]),
            )
        for code, row in found.rows.items():
            value = row[position - 1]
            if value is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=units[code],
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=value,
            )
    return p
