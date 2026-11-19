"""Colombia's Senate and House elections of 11 March 2018 by polling table from the Registraduría's escrutinio files.

The Registraduría Nacional del Estado Civil publishes the 2018 congressional escrutinio on its own site, and the
Observatorio's catalog links only that site. The polling-table (mesa) results come as one CSV per department and
chamber, saved from the site, with names rather than codes for departments, municipalities, parties, and candidates.
This module reads those files, matches the department and municipality names to the codes of `co.registraduria`, and
builds one package, `co.registraduria_congress/2018-03-11`, on top of that pipeline's geography, bodies, and electoral
system. Its parties and candidates are its own, keyed by name.

The Senate's national and indigenous circumscriptions and the House's department, indigenous, Afro-descendant, and
international circumscriptions are all there. The Senate file of Cesar and the candidate names of most lists in the two
special House circumscriptions are missing from the files, and the package says so on the affected result sets.
"""

import functools
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl
import shapely
from shapely.geometry import shape

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.co import registraduria as rnec
from ea.store import DATA

SOURCE = "co.registraduria_congress"
DAY = date(2018, 3, 11)
KEY = f"{SOURCE}/{DAY}"
PARSED = DATA / "parsed" / SOURCE
MUNICIPALITIES = Path(__file__).with_name("registraduria_congress_municipalities.tsv")
OUTLINES = Path(__file__).with_name("registraduria_congress_outlines.tsv")
BOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/COL/"
ADM1 = BOUNDARIES + "ADM1/geoBoundaries-COL-ADM1.geojson"
ADM2 = BOUNDARIES + "ADM2/geoBoundaries-COL-ADM2.geojson"
DEPARTMENT_OUTLINES = {
    "BOGOTA CAPITAL DISTRICT": "BOGOTA D C",
    "ARCHIPIELAGO DE SAN ANDRES PROVIDENCIA Y SANTA CATALINA": "SAN ANDRES PROVIDENCIA Y SANTA CATALINA",
}
SITE = "https://escr_congreso_2018.registraduria.gov.co/"
FILE = re.compile(r"/((senado|camara)2018_[A-Z_]+)\.csv$")
ABROAD = rnec.ABROAD
SENATE, HOUSE = "senado", "camara"
CIRCUMSCRIPTIONS = {
    "NACIONAL": "nacional",
    "INDIGENAS": "indigena",
    "AFRO-DESCENDIENTES": "afro",
    "TERRITORIAL DEPARTAMENTAL": "territorial",
}
BLANK, NULL, UNMARKED, LEGEND = "VOTOS EN BLANCO", "VOTOS NULOS", "VOTOS NO MARCADOS", "SOLO POR EL PARTIDO"
SPECIAL = {BLANK: "blank", NULL: "null", UNMARKED: "unmarked", LEGEND: "legend"}
MEASURES = {"null": 5, "unmarked": 6}
VOTES = {
    "corp": pl.String,
    "circ": pl.String,
    "dep": pl.String,
    "mun": pl.String,
    "zona": pl.String,
    "puesto": pl.String,
    "mesa": pl.String,
    "kind": pl.String,
    "party": pl.String,
    "cand": pl.String,
    "votes": pl.Int64,
}
PLACES = {
    "dep": pl.String,
    "mun": pl.String,
    "zona": pl.String,
    "puesto": pl.String,
    "dep_name": pl.String,
    "mun_name": pl.String,
    "puesto_name": pl.String,
}


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.upper().replace("?", "N").replace("Ñ", "N"))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.encode("ascii", "ignore").decode()).split())


def _place(column: str) -> pl.Expr:
    """A zone or polling-place code: numbers are padded to two digits, and the few alphanumeric ones are kept."""
    text = pl.col(column).str.strip_chars().str.to_uppercase()
    return pl.when(text.str.contains(r"^\d+$")).then(text.cast(pl.Int64).cast(pl.String).str.zfill(2)).otherwise(text)


def _text(column: str) -> pl.Expr:
    text = pl.col(column).str.replace_all("�", "").str.replace_all(r"\s+", " ").str.strip_chars()
    return pl.when(text == "").then(None).otherwise(text)


def key(kind: str, *parts: object) -> str:
    return package.Package(SOURCE, SOURCE, "").id(kind, *parts)


SOURCE_ID = key("source", "escrutinio")
LICENSE_URL = "https://www.registraduria.gov.co/Politicas-de-privacidad.html"
METHOD = (
    "Counted per polling table (mesa) from the Registraduría Nacional del Estado Civil's escrutinio files for the "
    "11 March 2018 congressional elections, one file per department and chamber. The files do not state their "
    "stage; they come from the escrutinio site. Votes for a list alone (solo por el partido) are stored on a legend "
    "line under the list and preference votes on candidate lines under it; a list without preference votes holds its "
    "votes itself. The blank vote is a valid option, null votes are stored as invalid ballots, and unmarked ballots "
    "as blank ballots. Rows with zero votes are left out."
)
UNNAMED_METHOD = (
    " The files print no name for some candidates of the lists in this contest, so their preference votes are "
    "stored together on one line per list, labeled as candidates without a published name."
)
MISSING_METHOD = (
    " The Senate file of the department of Cesar is not among the files this tabulation was built from, so Cesar's "
    "polling tables are missing and the totals here fall short of the national count."
)
UNNAMED = "Candidatos sin nombre publicado"


def files() -> list[tuple[str, str]]:
    found = [(m.group(1), k) for k in raw.keys(SOURCE) if (m := FILE.search(k))]
    return sorted(found)


def fetch() -> None:
    for url in (ADM1, ADM2):
        raw.fetch(SOURCE, url, suffix=".geojson")
    if not files():
        raise SystemExit(
            f"{SOURCE}: the files were saved by hand from {SITE}; add them to the raw cache with raw.store, one key "
            "per file, named senado2018_<DEPARTMENT>.csv or camara2018_<DEPARTMENT>.csv"
        )


def departments() -> dict[str, str]:
    names = {fold(text): code for code, text in rnec.DEPARTMENTS.items() if code != ABROAD}
    names["CONSULADOS"] = ABROAD
    return names


def _department_code(name: str, names: dict[str, str]) -> str:
    """The Registraduría prints department names cut to 12 letters, so a name may be a prefix of the full one."""
    folded = fold(name)
    found = {code for full, code in names.items() if full == folded or full.startswith(folded)}
    if len(found) != 1:
        raise LookupError(f"department {name!r} matches {sorted(found)}")
    return found.pop()


def municipalities() -> pl.DataFrame:
    return pl.read_csv(MUNICIPALITIES, separator="\t", infer_schema=False)


def parse() -> None:
    parse_votes()
    build()


def parse_votes() -> None:
    scans = []
    for name, url in files():
        scan = pl.scan_csv(raw.cached(SOURCE, url).path, infer_schema=False)
        scans.append(scan.with_columns(corp=pl.lit(name[:6]).replace_strict({"senado": SENATE, "camara": HOUSE})))
    frame = pl.concat(scans)
    pairs = frame.select("ndepto", "nmpio").unique().collect()
    names = departments()
    codes = municipalities().with_columns(key=pl.col("dep") + ":" + pl.col("name"))
    mapped = pairs.with_columns(
        dep=pl.col("ndepto").map_elements(lambda n: _department_code(n, names), return_dtype=pl.String),
        folded=pl.col("nmpio").map_elements(fold, return_dtype=pl.String),
    )
    mapped = (
        mapped.with_columns(key=pl.col("dep") + ":" + pl.col("folded"))
        .join(codes.select("key", code="mun"), on="key", how="left")
        .with_columns(mun=pl.when(pl.col("dep") == ABROAD).then(pl.col("folded")).otherwise(pl.col("code")))
    )
    lost = mapped.filter(pl.col("mun").is_null())
    if lost.height:
        raise LookupError(f"municipalities without a code: {lost.select('ndepto', 'nmpio').rows()[:10]}")
    special = pl.col("candidato").replace_strict(SPECIAL, default=None)
    rows = (
        frame.join(mapped.select("ndepto", "nmpio", "dep", "mun").lazy(), on=["ndepto", "nmpio"])
        .with_columns(
            circ=pl.col("ncircunscripcion").replace_strict(CIRCUMSCRIPTIONS),
            zona=_place("zz"),
            puesto=_place("pp"),
            mesa=pl.col("mesa").str.strip_chars().cast(pl.Int64).cast(pl.String),
            votes=pl.col("votos").str.strip_chars().cast(pl.Int64),
            party=_text("partido"),
            cand=_text("candidato"),
        )
        .with_columns(
            circ=pl.when((pl.col("dep") == ABROAD) & (pl.col("circ") == "territorial"))
            .then(pl.lit("exterior"))
            .otherwise(pl.col("circ")),
            kind=pl.when(pl.col("party").is_null())
            .then(special)
            .when(pl.col("cand").is_null())
            .then(pl.lit("unnamed"))
            .when(pl.col("cand") == LEGEND)
            .then(pl.lit("legend"))
            .otherwise(pl.lit("candidate")),
        )
        .filter(pl.col("votes") > 0)
        .with_columns(cand=pl.when(pl.col("kind") == "candidate").then(pl.col("cand")))
    )
    bad = rows.filter(pl.col("kind").is_null()).select(pl.len()).collect().item()
    if bad:
        raise ValueError(f"{bad:,} rows are neither a list, a candidate, nor a special line")
    out = PARSED
    out.mkdir(parents=True, exist_ok=True)
    rows.select([pl.col(c).cast(t) for c, t in VOTES.items()]).sink_parquet(out / "votes.parquet", engine="streaming")
    places = (
        frame.join(mapped.select("ndepto", "nmpio", "dep", "mun").lazy(), on=["ndepto", "nmpio"])
        .select(
            "dep",
            "mun",
            zona=_place("zz"),
            puesto=_place("pp"),
            dep_name=pl.col("ndepto"),
            mun_name=pl.col("nmpio"),
            puesto_name=pl.col("npuesto"),
        )
        .unique()
        .collect(engine="streaming")
        .sort("dep", "mun", "zona", "puesto", "puesto_name")
        .unique(["dep", "mun", "zona", "puesto"], keep="first", maintain_order=True)
    )
    places.select([pl.col(c).cast(t) for c, t in PLACES.items()]).write_parquet(out / "places.parquet")
    totals = (
        pl.scan_parquet(out / "votes.parquet")
        .group_by("corp", "circ", "kind")
        .agg(pl.col("votes").sum(), pl.len().alias("rows"))
        .sort("corp", "circ", "kind")
        .collect()
    )
    print(totals, flush=True)
    print(f"{places.height:,} polling places", flush=True)


def _contest_name(corp: str, circ: str, dep: str) -> str:
    chamber = "Senado" if corp == SENATE else "Cámara"
    special = {"indigena": "Circunscripción Especial Indígena", "afro": "Circunscripción Especial Afrodescendiente"}
    if circ in special:
        return f"{chamber}, {special[circ]}"
    if circ == "nacional":
        return f"{chamber}, Circunscripción Nacional"
    if circ == "exterior":
        return f"{chamber}, Circunscripción Internacional"
    return f"{chamber}, {rnec.title(rnec.DEPARTMENTS[dep])}"


def _district(corp: str, circ: str, dep: str) -> str:
    return dep if circ == "territorial" else "CO" if circ != "exterior" else ABROAD


def build() -> None:
    geo()
    election()


def election() -> None:
    folder = PARSED
    votes = pl.scan_parquet(folder / "votes.parquet")
    places = pl.read_parquet(folder / "places.parquet")
    towns = {(d, m) for d, m in municipalities().select("dep", "mun").iter_rows()}
    p = Package(
        KEY,
        SOURCE,
        "Colombia's Senate and House elections of 11 March 2018 by polling table from the Registraduría's escrutinio "
        "files",
    )
    country = rnec.COUNTRY
    p.external.update([rnec.COLOMBIA, country, rnec.SENATE_BODY, rnec.HOUSE_BODY])
    p.external.update(["co.puesto", "co.mesa"])  # the polling-place and polling-table schemes of co.registraduria/geo
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Registraduría Nacional del Estado Civil",
        title="Escrutinio de las elecciones de Congreso de la República 2018, votación mesa a mesa",
        url=SITE,
        license=rnec.LICENSE,
        license_url=LICENSE_URL,
        attribution="Registraduría Nacional del Estado Civil",
    )
    for _, url in files():
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    rules, method = rnec.SYSTEMS["lists"]
    system = p.id("electoral_system", "lists")
    p.add("electoral_system", id=system, polity_id=rnec.COLOMBIA, rules=rules, rules_version=1)
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=method, lang="en")
    election = p.id("election", DAY)
    p.add("election", id=election, polity_id=rnec.COLOMBIA, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2018 Colombian parliamentary election", "en", "official")
    p.name(election, "Elecciones legislativas de 2018", "es", "native", False)
    base = f"co/{DAY}-congress"
    p.add("slug", path=base, entity_id=election, is_current=True)

    contests = votes.select("corp", "circ", "dep").filter(pl.col("circ") == "territorial").unique().collect()
    special = votes.select("corp", "circ").filter(pl.col("circ") != "territorial").unique().collect()
    wanted = sorted(
        {(c, ci, d if ci == "territorial" else "") for c, ci, d in contests.iter_rows()}
        | {(c, ci, ABROAD if ci == "exterior" else "") for c, ci in special.iter_rows()}
    )
    # the House's international seat is filed under the department "88" of the territorial files
    parties: set[str] = set()
    options: list[dict[str, object]] = []
    sets: dict[str, str] = {}
    paths: set[str] = set()
    for corp, circ, dep in wanted:
        district = _district(corp, circ, dep)
        ck = f"{corp}:{circ}:{district}"
        contest = p.id("contest", ck)
        text = _contest_name(corp, circ, dep)
        if circ == "territorial":
            place = rnec.area("co.departamento", dep)
        else:
            place = country
        here = votes.filter((pl.col("corp") == corp) & (pl.col("circ") == circ))
        if circ == "territorial":
            here = here.filter(pl.col("dep") == dep)
        if place != country:
            p.external.add(place)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=rnec.SENATE_BODY if corp == SENATE else rnec.HOUSE_BODY,
            district_area_id=place,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, text, "es", "official")
        slug = changeset.slug(text) or district
        path = f"{base}/{slug}"
        if path in paths:
            path = f"{base}/{slug}-{changeset.slug(district)}"
        paths.add(path)
        p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = sets[ck] = p.id("result_set", ck)
        method = METHOD
        structure = here.group_by("party").agg(
            pl.col("kind").is_in(["candidate", "unnamed"]).any().alias("open"),
            (pl.col("kind") == "unnamed").any().alias("unnamed"),
        )
        listed = structure.filter(pl.col("party").is_not_null()).collect().sort("party")
        if listed["unnamed"].any():
            method += UNNAMED_METHOD
        if corp == SENATE:
            method += MISSING_METHOD
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
            completeness=None if corp == SENATE else 1.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        blank = p.id("option", ck, "blank")
        p.add("contest_option", id=blank, contest_id=contest, kind="none_of_the_above", ballot_label="Voto en blanco")
        options.append({"ck": ck, "party": "", "kind": "blank", "cand": "", "option_id": blank, "measure": 1})
        for kind in ("null", "unmarked"):
            options.append(
                {"ck": ck, "party": "", "kind": kind, "cand": "", "option_id": None, "measure": MEASURES[kind]}
            )
        names = here.filter(pl.col("party").is_not_null()).select("party", "cand").unique().collect()
        for party, is_open, has_unnamed in listed.iter_rows():
            party_id = p.id("party", fold(party))
            if party_id not in parties:
                parties.add(party_id)
                p.add("party", id=party_id, kind="party", polity_id=rnec.COLOMBIA)
                p.add("external_id", entity_id=party_id, scheme=f"{SOURCE}.party", value=fold(party))
                p.name(party_id, party, "es", "official")
            listing = p.id("option", ck, fold(party), "list")
            p.add(
                "contest_option",
                id=listing,
                contest_id=contest,
                kind="list",
                party_id=party_id,
                ballot_label=party,
            )
            if not is_open:
                for kind in ("legend", "candidate", "unnamed"):
                    options.append(
                        {"ck": ck, "party": party, "kind": kind, "cand": "", "option_id": listing, "measure": 1}
                    )
                continue
            legend = p.id("option", ck, fold(party), "legend")
            p.add(
                "contest_option",
                id=legend,
                contest_id=contest,
                kind="legend",
                parent_option_id=listing,
                party_id=party_id,
                ballot_label=f"{party} (solo por la lista)",
            )
            options.append({"ck": ck, "party": party, "kind": "legend", "cand": "", "option_id": legend, "measure": 1})
            if has_unnamed:
                unnamed = p.id("option", ck, fold(party), "unnamed")
                p.add(
                    "contest_option",
                    id=unnamed,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=listing,
                    ballot_label=UNNAMED,
                )
                options.append(
                    {"ck": ck, "party": party, "kind": "unnamed", "cand": "", "option_id": unnamed, "measure": 1}
                )
            for (name,) in sorted(
                names.filter((pl.col("party") == party) & pl.col("cand").is_not_null()).select("cand").iter_rows()
            ):
                option = p.id("option", ck, fold(party), "candidate", name)
                label = rnec.title(name)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=listing,
                    ballot_label=label,
                )
                person = p.id("person", ck, fold(party), name)
                p.add("person", id=person)
                p.name(person, label, "es", "official")
                p.add(
                    "candidacy",
                    id=p.id("candidacy", ck, fold(party), name),
                    person_id=person,
                    option_id=listing,
                    role="list_member",
                    ballot_name=label,
                    member_of_party_id=party_id,
                )
                options.append(
                    {"ck": ck, "party": party, "kind": "candidate", "cand": name, "option_id": option, "measure": 1}
                )
    units = _units(p, places, towns, votes)
    mapping = pl.DataFrame(
        options,
        schema={
            "ck": pl.String,
            "party": pl.String,
            "kind": pl.String,
            "cand": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
        },
    ).lazy()
    district = (
        pl.when(pl.col("circ") == "territorial")
        .then(pl.col("dep"))
        .when(pl.col("circ") == "exterior")
        .then(pl.lit(ABROAD))
        .otherwise(pl.lit("CO"))
    )
    values = (
        votes.with_columns(
            ck=pl.concat_str("corp", "circ", district, separator=":"),
            code=pl.concat_str("dep", "mun", "zona", "puesto", "mesa", separator=":"),
            party=pl.col("party").fill_null(""),
            cand=pl.col("cand").fill_null(""),
        )
        .join(mapping, on=["ck", "party", "kind", "cand"], how="left")
        .join(pl.DataFrame({"ck": list(sets), "result_set_id": list(sets.values())}).lazy(), on="ck")
        .join(units.lazy(), on="code")
        .group_by("result_set_id", "unit_id", "option_id", "measure")
        .agg(pl.col("votes").sum(), pl.len().alias("rows"))
        .collect(engine="streaming")
    )
    total = votes.select(pl.len()).collect().item()
    if values["rows"].sum() != total or values["measure"].null_count():
        raise ValueError(f"{KEY}: {total:,} rows, {values['rows'].sum():,} mapped")
    p.frame(
        "result_value",
        values.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="votes",
        ),
    )
    p.write()


def _units(p: Package, places: pl.DataFrame, towns: set[tuple[str, str]], votes: pl.LazyFrame) -> pl.DataFrame:
    columns = ["dep", "mun", "zona", "puesto", "mesa"]
    mesas = votes.select(columns).unique().collect().sort(columns)
    named = {(d, m, z, s): n for d, m, z, s, n in places.select("dep", "mun", "zona", "puesto", "puesto_name").rows()}
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    codes, ids = [], []
    for dep, mun, zona, puesto, mesa in mesas.iter_rows():
        if dep == ABROAD:
            relation, place, kind = "unlocated", None, "abroad_mission"
        elif (dep, mun) in towns:
            relation, place, kind = "within", rnec.area("co.municipio", f"{dep}:{mun}"), "standard"
        else:
            relation, place, kind = "within", rnec.area("co.departamento", dep), "standard"
        if place is not None:
            p.external.add(place)
        parent_code = f"{DAY}:{dep}:{mun}:{zona}:{puesto}"
        parent = p.id("unit", "co.puesto", parent_code)
        if (dep, mun, zona, puesto) not in seen:
            seen.add((dep, mun, zona, puesto))
            rows.append(
                {
                    "id": parent,
                    "polity_id": rnec.COLOMBIA,
                    "scheme_code": "co.puesto",
                    "code": parent_code,
                    "area_relation": relation,
                    "area_id": place,
                    "kind": kind,
                }
            )
            text = named.get((dep, mun, zona, puesto))
            if text:
                p.name(parent, rnec.title(text), "es", "official")
        code = f"{parent_code}:{mesa}"
        unit = p.id("unit", "co.mesa", code)
        codes.append(f"{dep}:{mun}:{zona}:{puesto}:{mesa}")
        ids.append(unit)
        rows.append(
            {
                "id": unit,
                "polity_id": rnec.COLOMBIA,
                "scheme_code": "co.mesa",
                "code": code,
                "parent_id": parent,
                "area_relation": relation,
                "area_id": place,
                "kind": kind,
            }
        )
    p.frame("reporting_unit", pl.DataFrame(rows, infer_schema_length=None))
    return pl.DataFrame({"code": codes, "unit_id": ids})


def _features(url: str) -> list[dict[str, object]]:
    return list(json.loads(raw.cached(SOURCE, url).path.read_bytes())["features"])


def _outline(feature: dict[str, object]) -> shapely.Geometry:
    found = shape(feature["geometry"])  # type: ignore[arg-type]
    found = found if found.is_valid else shapely.make_valid(found)
    polygons = [g for g in shapely.get_parts(found) if g.geom_type == "Polygon" and not g.is_empty]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _folded_name(feature: dict[str, object]) -> str:
    props = feature["properties"]
    assert isinstance(props, dict)
    return fold(str(props["shapeName"]))


@functools.cache
def outlines() -> tuple[dict[str, shapely.Geometry], dict[tuple[str, str], shapely.Geometry]]:
    """The department outlines by code and the municipal outlines by (department code, folded name).

    The second level carries no department, so each municipal outline takes the department whose first-level outline
    holds its representative point.
    """
    names = {fold(text): code for code, text in rnec.DEPARTMENTS.items() if code != ABROAD}
    deps = {names[DEPARTMENT_OUTLINES.get(n := _folded_name(f), n)]: _outline(f) for f in _features(ADM1)}
    codes = list(deps)
    tree = shapely.STRtree([deps[c] for c in codes])
    renamed = {(d, n): m for d, n, m in pl.read_csv(OUTLINES, separator="\t", infer_schema=False).iter_rows()}
    towns: dict[tuple[str, str], shapely.Geometry] = {}
    for feature in _features(ADM2):
        outline = _outline(feature)
        hit = tree.query(shapely.point_on_surface(outline), predicate="intersects")
        if len(hit) != 1:
            hit = [max(range(len(codes)), key=lambda i: shapely.area(shapely.intersection(deps[codes[i]], outline)))]
        dep = codes[hit[0]]
        name = _folded_name(feature)
        key = (dep, renamed.get((dep, name), name))
        if key in towns:
            raise ValueError(f"two outlines for {key}")
        towns[key] = outline
    return deps, towns


def geo() -> Package:
    """Outlines for the departments and municipalities of `co.registraduria`, which holds the areas without shapes."""
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Outlines for Colombia's departments and municipalities, from geoBoundaries' open release",
    )
    sources = {
        ADM1: (
            key("source", "geoboundaries-adm1"),
            "geoBoundaries gbOpen Colombia ADM1, 2017",
            "Open Database License 1.0 (OpenStreetMap contributors), through geoBoundaries (CC BY 4.0)",
            "https://www.openstreetmap.org/copyright",
            "OpenStreetMap contributors (Wambacher), through geoBoundaries (William & Mary geoLab)",
        ),
        ADM2: (
            key("source", "geoboundaries-adm2"),
            "geoBoundaries gbOpen Colombia ADM2, 2020",
            "Creative Commons Attribution 4.0 International (CC BY 4.0), through geoBoundaries",
            "https://creativecommons.org/licenses/by/4.0/",
            "Departamento Administrativo Nacional de Estadística (DANE), through geoBoundaries (William & Mary geoLab)",
        ),
    }
    for url, (id, title, license, license_url, attribution) in sources.items():
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries",
            title=title,
            url="https://www.geoboundaries.org/",
            license=license,
            license_url=license_url,
            attribution=attribution,
        )
        p.artifact(raw.cached(SOURCE, url), id)
    p.external.update(["co.departamento", "co.municipio"])
    for scheme, vintage in (("co.departamento", date(2017, 1, 1)), ("co.municipio", date(2020, 1, 1))):
        p.add(
            "boundary_set",
            id=p.id("boundary_set", scheme, vintage.year),
            scheme_code=scheme,
            vintage=str(vintage.year),
            valid_from=None,
            valid_to=None,
            as_of=vintage,
        )
    deps, towns = outlines()
    codes = {(d, fold(n)): m for d, m, n in municipalities().select("dep", "mun", "name").iter_rows()}

    def add(id: str, outline: shapely.Geometry, source: str, quality: str) -> None:
        p.external.add(id)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=None,
            valid_to=None,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality=quality,
            source_id=source,
        )

    for dep, outline in sorted(deps.items()):
        add(rnec.area("co.departamento", dep), outline, sources[ADM1][0], "osm")
    for (dep, name), outline in sorted(towns.items()):
        mun = codes.get((dep, name))
        if mun is None:
            raise ValueError(f"outline {dep} {name} matches no municipality")
        add(rnec.area("co.municipio", f"{dep}:{mun}"), outline, sources[ADM2][0], "official")
    p.write()
    return p


def emit() -> None:
    package.run("load", [f"{SOURCE}/geo", KEY])
