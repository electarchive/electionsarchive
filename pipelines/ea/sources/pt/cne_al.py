"""The official results maps of Portugal's local elections of 1993 and 1997, by municipality, from the National
Elections Commission.

After each local election the National Elections Commission (CNE) published the official map of results (mapa
oficial) in the Diário da República, and its site carries each one as a PDF. The 1993 map (Diário da República, I-B,
n.º 52, 3 March 1994) prints the municipal chamber (câmara municipal) and municipal assembly (assembleia
municipal) of each of the 305 municipalities, with the names of the members elected; the parish assemblies came in
a second map. The 1997 map (Mapa Oficial n.º 1/98, Diário da República, I-B, n.º 51, 2 March 1998, supplement)
prints all three bodies. For each body the map gives the seats the law fixed, the registered voters, the voters,
the blank and invalid ballots, and each list's votes, percentage, and seats.

Both maps are scans with no text layer. Their figures were read by OCR: each page in strips, then each body's number
columns again with digits-only OCR, row by row, and with a second OCR engine. A body's figures are kept only when
every one is confirmed: each list's votes and the blank and invalid ballots by the percentage of the voters the map
prints beside them, the voters by their printed percentage of the registered voters, the registered voters by the
same figure in the municipality's other body, and the seats by the seats the law fixed and the D'Hondt allocation of
the votes. Some bodies that failed a check were keyed from the page image and pass the same checks; the rest are
left out until they are keyed.
Where the map's own figures do not add up (the lists, blank, and invalid ballots against the voters) while every
reading agrees, the figures are kept as printed with a caveat. The results are in `cne_al_<year>.tsv`, one row per
figure with its page and its box on the scan in pixels at 300 dpi, and whether it was keyed.

The maps of 1976 to 1985 are not on the Commission's site (the 1976 page notes the map was never published in the
Diário da República). The 1989 map is a table printed on its side and is left for later, as are the parish
assemblies.
"""

import csv
import struct
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pt import mai
from ea.store import DATA

SOURCE = "pt.cne"
HOST = "https://www.cne.pt/sites/default/files/dl"
VOTES, REGISTERED, CAST, INVALID, BLANK = mai.VOTES, mai.REGISTERED, mai.CAST, mai.INVALID, mai.BLANK
HERE = Path(__file__).parent
SCAN_DPI = 300  # the scans' own resolution: boxes in the tables are pixels at this resolution
DPI = 110  # the page images the archive serves


@dataclass(frozen=True)
class Map:
    year: int
    day: date
    file: str
    title: str


MAPS = (
    Map(
        1993,
        date(1993, 12, 12),
        "resultados_al_1993_cm_am.pdf",
        "Diário da República, I série-B, n.º 52, 3 March 1994",
    ),
    Map(
        1997,
        date(1997, 12, 14),
        "resultados_al_1997_0.pdf",
        "Mapa Oficial n.º 1/98, Diário da República, I série-B, n.º 51, 2 March 1998 (supplement)",
    ),
)
ORGANS = {"CM": ("Municipal Chamber", "Câmara Municipal"), "AM": ("Municipal Assembly", "Assembleia Municipal")}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CNE_SOURCE = key("source", "cne")


def url(spec: Map) -> str:
    return f"{HOST}/{spec.file}"


def fetch() -> None:
    for spec in MAPS:
        raw.fetch(SOURCE, url(spec), suffix=".pdf")


def artifact(spec: Map) -> raw.Artifact:
    return raw.cached(SOURCE, url(spec))


def table_path(spec: Map) -> Path:
    return HERE / f"cne_al_{spec.year}.tsv"


# ---------------------------------------------------------------------------------------------------------------------
# The digitized tables


@dataclass
class Figure:
    value: int
    page: int
    box: tuple[float, float, float, float]  # pixels on the scan at SCAN_DPI
    keyed: bool


@dataclass
class Body:
    code: str  # the Ministry's municipality code
    organ: str  # CM or AM
    seats: int
    registered: Figure
    cast: Figure
    blank: Figure
    invalid: Figure
    lists: list[tuple[str, Figure, int]] = field(default_factory=list)  # label, votes, seats won


FIELDS = ("code", "organ", "seats", "row", "value", "won", "page", "x0", "y0", "x1", "y1", "keyed")
MEASURES = {"i": REGISTERED, "v": CAST, "b": BLANK, "n": INVALID}


def bodies(spec: Map) -> list[Body]:
    """The bodies of a map as `digitize` wrote them."""
    out: dict[tuple[str, str], Body] = {}
    rows: dict[tuple[str, str], dict[str, Figure]] = {}
    with table_path(spec).open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            figure = Figure(
                int(row["value"]),
                int(row["page"]),
                (float(row["x0"]), float(row["y0"]), float(row["x1"]), float(row["y1"])),
                row["keyed"] == "1",
            )
            which = (row["code"], row["organ"])
            if which not in out:
                rows[which] = {}
                out[which] = Body(row["code"], row["organ"], int(row["seats"]), figure, figure, figure, figure)
            if row["row"] in MEASURES:
                rows[which][row["row"]] = figure
            else:
                out[which].lists.append((row["row"], figure, int(row["won"])))
    for which, body in out.items():
        found = rows[which]
        body.registered, body.cast, body.blank, body.invalid = found["i"], found["v"], found["b"], found["n"]
    return list(out.values())


def labels() -> list[str]:
    """Every list label of the maps."""
    found: dict[str, None] = {}
    for spec in MAPS:
        for body in bodies(spec):
            for label, _, _ in body.lists:
                found.setdefault(label, None)
    return list(found)


# ---------------------------------------------------------------------------------------------------------------------
# Packages

METHOD = (
    "Counted by municipality, as published in the National Elections Commission's official map of results (mapa "
    "oficial) in the {title}. The map prints, for each body, the seats the law fixed, the registered voters, the "
    "voters, the blank and invalid (nulos) ballots, and each list's votes and seats; it goes no finer than the "
    "municipality for these bodies. The map is a scanned page: its figures were read by OCR and each was confirmed "
    "by the percentage the map prints beside it, the body's sums, and the D'Hondt allocation of its seats, or keyed "
    "from the page image and checked the same way. Seats are as the map declares them."
)


def day_key(spec: Map) -> str:
    return f"{SOURCE}/{spec.day}-local"


def packages() -> list[str]:
    return [day_key(spec) for spec in MAPS]


def artifact_id(spec: Map) -> str:
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CNE_SOURCE}\x1f{artifact(spec).storage_key}"))


def render(spec: Map, pages: set[int]) -> dict[int, tuple[str, int, int]]:
    """The cited pages as grayscale images, with each one's key and size in pixels."""
    source = artifact(spec)
    folder = DATA / "pages" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[int, tuple[str, int, int]] = {}
    for page in sorted(pages):
        stem = folder / f"{source.sha256}-{page:03d}"
        image = stem.with_suffix(".png")
        if not image.exists():
            subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                + [str(source.path), str(stem)],
                check=True,
            )
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        out[page] = (f"pages/{SOURCE}/{image.name}", width, height)
    return out


def election(spec: Map) -> Package:
    from ea.sources.pt import cne

    day = spec.day
    p = Package(
        day_key(spec),
        SOURCE,
        f"The Portuguese local elections of {day}: the municipal chambers and assemblies by municipality, from the "
        "National Elections Commission's official results map",
        depends=[f"{SOURCE}/registry", "pt.mai/geo", "pt.mai/registry"],
    )
    p.external.add(mai.PORTUGAL)
    p.uses(artifact(spec))
    found = sorted(bodies(spec), key=lambda body: (body.code, body.organ))
    towns = municipalities()
    election_id = key("election", f"al{spec.year}")
    path = f"pt/{day}-local"
    p.add("election", id=election_id, polity_id=mai.PORTUGAL, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{spec.year} Portuguese local elections", "en", "official")
    p.name(election_id, f"Eleições autárquicas de {spec.year}", "pt", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    pages = {figure.page for body in found for figure in figures(body)}
    page_ids: dict[int, str] = {}
    for number, (image, width, height) in render(spec, pages).items():
        page_ids[number] = key("page", spec.year, number)
        p.add(
            "source_page",
            id=page_ids[number],
            artifact_id=artifact_id(spec),
            page_number=number,
            image_key=image,
            width=width,
            height=height,
        )
    held = len({(body.code, body.organ) for body in found})
    p.add(
        "note",
        id=key("note", spec.year, "coverage"),
        entity_id=election_id,
        kind="caveat",
        body_md=(
            f"This holds {held} of the 610 municipal chambers and assemblies on the map: the bodies whose every "
            "figure the checks confirmed. The others, and the parish assemblies, are not yet digitized."
        ),
        lang="en",
    )
    method = METHOD.format(title=spec.title)
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    slugs: set[str] = {path}
    units: set[str] = set()
    for body in found:
        town = towns[body.code][0]
        english, portuguese = ORGANS[body.organ]
        contest = key("contest", spec.year, body.organ, body.code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=mai.council(body.code) if body.organ == "CM" else mai.assembly(body.code),
            district_area_id=mai.town_area(body.code),
            magnitude=body.seats,
            round_number=1,
            electoral_system_id=mai.SYSTEM_LIST,
            default_result_kind="counted",
        )
        p.name(contest, f"{town} {english}", "en", "official")
        p.name(contest, f"{portuguese} ({town})", "pt", "native", False)
        tail = "municipal-chamber" if body.organ == "CM" else "municipal-assembly"
        slug = f"{path}/{changeset.slug(f'{town} {tail}')}"
        if slug in slugs:
            slug = f"{path}/{changeset.slug(f'{town} {towns[body.code][1]} {tail}')}"
        slugs.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        result_set = key("result_set", spec.year, body.organ, body.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        unit = key("unit", spec.year, body.code)
        if unit not in units:
            units.add(unit)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=mai.PORTUGAL,
                scheme_code="pt.concelho",
                code=f"{day}:{body.code}",
                area_relation="equals",
                area_id=mai.town_area(body.code),
                kind="aggregate",
            )
            p.name(unit, town, "pt", "official")
        for measure, figure in (
            (REGISTERED, body.registered),
            (CAST, body.cast),
            (BLANK, body.blank),
            (INVALID, body.invalid),
        ):
            _value(values, locators, page_ids, result_set, unit, None, measure, figure)
        for position, (label, figure, won) in enumerate(body.lists, 1):
            option = key("option", spec.year, body.organ, body.code, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=cne.party(label),
                ballot_position=position,
                ballot_label=label,
            )
            _value(values, locators, page_ids, result_set, unit, option, VOTES, figure)
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        total = body.blank.value + body.invalid.value + sum(figure.value for _, figure, _ in body.lists)
        if total != body.cast.value:
            p.add(
                "note",
                id=key("note", result_set, "sum"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"The map's own figures do not add up here: the blank and invalid ballots and the lists' votes "
                    f"come to {total:,}, against {body.cast.value:,} voters. They are stored as printed."
                ),
                lang="en",
            )
    _frames(p, values, locators)
    return p


def municipalities() -> dict[str, tuple[str, str]]:
    """The 305 municipalities of 1993 and 1997 by the Ministry's code, with their name and district or region, as
    the Ministry's 2021 local election site lists them."""
    with (HERE / "cne_al_municipalities.tsv").open(newline="") as handle:
        return {row["code"]: (row["name"], row["district"]) for row in csv.DictReader(handle, delimiter="\t")}


def figures(body: Body) -> list[Figure]:
    return [body.registered, body.cast, body.blank, body.invalid, *(figure for _, figure, _ in body.lists)]


def _value(
    values: list[tuple[str, str, str | None, int, int]],
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]],
    page_ids: dict[int, str],
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    figure: Figure,
) -> None:
    values.append((result_set, unit, option, measure, figure.value))
    scale = DPI / SCAN_DPI
    x0, y0, x1, y1 = (round(c * scale, 1) for c in figure.box)
    locators.append((result_set, unit, option, measure, page_ids[figure.page], x0, y0, x1, y1))


def _frames(
    p: Package,
    values: list[tuple[str, str, str | None, int, int]],
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]],
) -> None:
    key_schema = {"result_set_id": pl.String(), "unit_id": pl.String(), "option_id": pl.String(), "measure": pl.Int16()}
    p.frame(
        "result_value",
        pl.DataFrame(values, schema={**key_schema, "value": pl.Int64()}, orient="row").with_columns(
            channel=pl.lit(0, pl.Int16()), count_round=pl.lit(0, pl.Int16()), flags=pl.lit(0, pl.Int16())
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                **key_schema,
                "page_id": pl.String(),
                "x0": pl.Float64(),
                "y0": pl.Float64(),
                "x1": pl.Float64(),
                "y1": pl.Float64(),
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16()), count_round=pl.lit(0, pl.Int16())),
    )
