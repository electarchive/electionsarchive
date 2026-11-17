"""The Dominican Republic's simultaneous party primaries, from the Junta Central Electoral's transparency portal.

The Board ran the primaries of 1 October 2023 for the one party that held them, the PRM, and published a summary by
demarcation and office and a detail by polling table (mesa) of each precandidate's votes. Each office and demarcation
is one nominating contest, and each polling table is one reporting unit of the `do.colegio` scheme.
"""

from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.do import jce

SOURCE = "do.jce_primarias"
DAY = date(2023, 10, 1)
KEY = f"{SOURCE}/{DAY:%Y-%m-%d}"
SUMMARY = jce.entry(27571)
TABLES = jce.entry(27569)
PRM = "PRM"
SOURCE_ID = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1frepositorio"))
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fplurality"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
CARGOS = {
    "PRESIDENTE(A)": "president",
    "ALCALDE(SA)": "mayor",
    "REGIDOR(A)": "council",
    "DIRECTOR(A)": "director",
    "VOCAL": "vocal",
}
METHOD = (
    "A nominating contest: each precandidate of the party stood on the party's primary ballot and the Board counted "
    "votes for individual precandidates at each polling table. The files give no winners, only votes, so no outcome "
    "is stored. The Board's summary prints the registered voters, valid votes, ballots with multiple marks, blank "
    "ballots, and ballots cast for each demarcation; those are stored as the published totals, since the table file "
    "reports only precandidate votes."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    raw.fetch(SOURCE, SUMMARY, suffix=".xlsx")
    raw.fetch(SOURCE, TABLES, suffix=".xlsx")


def _read(url: str) -> pl.DataFrame:
    artifact = raw.cached(SOURCE, url)
    return pl.read_excel(artifact.path, engine="calamine")


def _strip(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col(c).str.strip_chars() for c, t in frame.schema.items() if t == pl.String)


def summary() -> pl.DataFrame:
    return _strip(_read(SUMMARY)).with_columns(office=pl.col("Cargo").replace_strict(CARGOS))


def tables() -> pl.DataFrame:
    frame = _strip(_read(TABLES))
    return frame.with_columns(office=pl.col("DescripcionCargo").replace_strict(CARGOS)).with_columns(
        code=pl.format(
            "{}:{}:{}:{}:{}",
            pl.lit(f"{DAY:%Y-%m-%d}"),
            pl.col("IdMunicipio"),
            pl.col("IdDM"),
            pl.col("CodRecinto"),
            pl.col("Mesa"),
        )
    )


def _circs(frame: pl.DataFrame, found: pl.DataFrame) -> pl.DataFrame:
    """Number each municipality's council circumscriptions 1 to n in the order the tables code them."""
    several = found.filter(pl.col("DescripcionCircunscripcion").is_not_null())
    counts = several.group_by("IdMunicipio").len()
    rows: list[tuple[int, int, int]] = []
    council = frame.filter(pl.col("office") == "council")
    for mun, n in counts.iter_rows():
        coded = sorted(council.filter(pl.col("IdMunicipio") == mun)["Circunscripcion"].unique().to_list())
        if len(coded) != n:
            raise ValueError(f"municipality {mun}: {len(coded)} table circumscriptions for {n} in the summary")
        rows += [(mun, code, index + 1) for index, code in enumerate(coded)]
    return pl.DataFrame(rows, schema=["mun", "circ_code", "circ"], orient="row")


def package_() -> Package:
    places = jce.places()
    summary_rows = summary()
    mesa = tables()
    p = Package(
        KEY,
        SOURCE,
        "The Dominican Republic's simultaneous party primaries of 1 October 2023 by polling table from the Junta "
        "Central Electoral",
        depends=[f"{jce.SOURCE}/geo", f"{jce.SOURCE}/registry"],
    )
    p.external |= {jce.DOMINICAN, jce.COUNTRY}
    p.artifact(raw.cached(SOURCE, SUMMARY), SOURCE_ID)
    p.artifact(raw.cached(SOURCE, TABLES), SOURCE_ID)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Junta Central Electoral",
        title="Junta Central Electoral, Portal de Transparencia: resultados de las primarias por mesa",
        url=f"{jce.SITE}/portaltransparencia",
        license=jce.LICENSE,
        license_url=f"{jce.SITE}/portaltransparencia",
        attribution="Junta Central Electoral (JCE), República Dominicana",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=jce.DOMINICAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", "system"),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Precandidates are ranked by the votes they receive at the party's primary; the party's rules, not "
        "the electoral law of the general election, decide how many of them are nominated.",
        lang="en",
    )
    election = key("election", KEY)
    p.add(
        "election",
        id=election,
        polity_id=jce.DOMINICAN,
        kind="primary",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(election, "2023 Dominican Republic simultaneous party primaries", "en", "official")
    p.name(election, "Elecciones primarias simultáneas de 2023", "es", "native", False)
    base = f"do/{DAY:%Y-%m-%d}-primarias"
    p.add("slug", path=base, entity_id=election, is_current=True)

    # Demarcations the Board prints a summary row for, and the council circumscriptions of its tables.
    national = summary_rows.filter(pl.col("Alcance") == "TOTAL NACIONAL")
    found = summary_rows.filter(pl.col("Alcance") != "TOTAL NACIONAL")
    circs = _circs(mesa, found.filter(pl.col("office") == "council"))
    mesa = (
        mesa.join(circs, left_on=["IdMunicipio", "Circunscripcion"], right_on=["mun", "circ_code"], how="left")
        .with_columns(
            seat=pl.when(pl.col("office").is_in(["director", "vocal"]))
            .then(pl.col("IdDM"))
            .otherwise(pl.col("IdMunicipio")),
        )
        .with_columns(
            contest=pl.when(pl.col("office") == "president")
            .then(pl.lit("president"))
            .when(pl.col("office") == "council")
            .then(
                pl.when(pl.col("circ").is_null())
                .then(pl.format("council:{}", pl.col("IdMunicipio")))
                .otherwise(pl.format("council:{}:{}", pl.col("IdMunicipio"), pl.col("circ")))
            )
            .otherwise(pl.format("{}:{}", pl.col("office"), pl.col("seat")))
        )
    )

    mun_slug = jce._slugs({m: t for m, (_, t) in places.municipalities.items()})
    dm_slug = jce._slugs({m: t for m, (_, t) in places.districts.items()})

    # Reporting units: one per polling table.
    unit_rows = (
        mesa.select("code", "IdMunicipio", "IdDM", "CodRecinto", "DescripcionRecinto", "Mesa", "DescripcionMunicipio")
        .unique()
        .sort("code")
    )
    if unit_rows["code"].n_unique() != unit_rows.height:
        raise ValueError("a polling table sits in two municipalities or municipal districts")
    units: list[dict[str, object]] = []
    for code, mun, dm, recinto, text, number, mun_name in unit_rows.iter_rows():
        if dm != mun and dm not in places.districts:
            raise ValueError(f"{code}: unknown municipal district {dm}")
        if mun not in places.municipalities:
            raise ValueError(f"{code}: unknown municipality {mun}")
        id = key("unit", code)
        place = jce.area("do.distrito_municipal", str(dm)) if dm != mun else jce.area("do.municipio", str(mun))
        units.append(
            {
                "id": id,
                "polity_id": jce.DOMINICAN,
                "scheme_code": "do.colegio",
                "code": code,
                "area_relation": "within",
                "area_id": place,
                "kind": "standard",
            }
        )
        p.name(
            id,
            f"Mesa {number}, recinto {recinto} {jce.title(text)}, {jce.place_name(mun_name)}",
            "es",
            "official",
        )
    p.frame("reporting_unit", pl.DataFrame(units, infer_schema_length=None))

    prm = jce.party(PRM)
    values: list[pl.DataFrame] = []
    mismatches: list[str] = []
    contest_keys = mesa["contest"].unique().sort().to_list()
    for name in contest_keys:
        part = mesa.filter(pl.col("contest") == name)
        office = part["office"][0]
        mun = int(part["IdMunicipio"][0])
        seat = int(part["seat"][0])
        circ = part["circ"][0]
        if office == "president":
            text, slug, area = "Presidente de la República", "presidente", jce.COUNTRY
            office_id, body_id, kind, magnitude = jce.PRESIDENCY, None, "office", 1
            printed = national.filter(pl.col("office") == "president")
        elif office == "mayor":
            place = places.municipalities[mun][1]
            text, slug, area = f"Alcalde de {place}", f"alcalde-{mun_slug[mun]}", jce.area("do.municipio", str(mun))
            office_id, body_id, kind, magnitude = jce.mayor("do.municipio", str(mun)), None, "office", 1
            printed = found.filter((pl.col("office") == "mayor") & (pl.col("IdMunicipio") == mun))
        elif office == "council":
            place = places.municipalities[mun][1]
            if circ is None:
                text, slug = f"Regidores de {place}", f"regidores-{mun_slug[mun]}"
                area = jce.area("do.municipio", str(mun))
                printed = found.filter((pl.col("office") == "council") & (pl.col("IdMunicipio") == mun))
            else:
                text, slug = f"Regidores de {place}, circunscripción {circ}", f"regidores-{mun_slug[mun]}-{circ}"
                area = (
                    jce.area("do.circunscripcion_municipal", f"{mun}:{circ}")
                    if (mun, circ) in places.city_circs
                    else jce.area("do.municipio", str(mun))
                )
                printed = found.filter(
                    (pl.col("office") == "council")
                    & (pl.col("IdMunicipio") == mun)
                    & (pl.col("DescripcionCircunscripcion") == f"CIRC. {circ}")
                )
            office_id, body_id, kind, magnitude = None, jce.council("do.municipio", str(mun)), "body_seats", None
        else:
            place = places.districts[seat][1]
            area = jce.area("do.distrito_municipal", str(seat))
            printed = found.filter((pl.col("office") == office) & (pl.col("IdDistritoMunicipal") == seat))
            if office == "director":
                text, slug = f"Director del distrito municipal {place}", f"director-{dm_slug[seat]}"
                office_id, body_id, kind, magnitude = jce.mayor("do.distrito_municipal", str(seat)), None, "office", 1
            else:
                text, slug = f"Vocales del distrito municipal {place}", f"vocales-{dm_slug[seat]}"
                office_id, body_id, kind, magnitude = (
                    None,
                    jce.council("do.distrito_municipal", str(seat)),
                    "body_seats",
                    None,
                )
        text = f"Primarias del PRM: {text}"
        contest = key("contest", name)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            office_id=office_id,
            body_id=body_id,
            district_area_id=area,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, text, "es", "official")
        p.add("slug", path=f"{base}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", name)
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
        candidates = (
            part.select("PosicionCandidato", "NombreCandidato")
            .unique("PosicionCandidato", keep="first")
            .sort("PosicionCandidato")
        )
        clash = (
            part.group_by("PosicionCandidato")
            .agg(pl.col("NombreCandidato").n_unique())
            .filter(pl.col("NombreCandidato") > 1)
        )
        if clash.height:
            raise ValueError(f"{name}: one ballot position prints two names")
        options: dict[int, str] = {}
        for position, label in candidates.iter_rows():
            option = options[position] = key("option", name, position)
            person = key("person", name, position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=prm,
                ballot_position=position,
                ballot_label=jce.title(label),
            )
            p.add("person", id=person)
            p.name(person, jce.title(label), "es", "official")
            p.add(
                "candidacy",
                id=key("candidacy", name, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=jce.title(label),
                member_of_party_id=prm,
                is_independent=False,
            )
        values.append(
            part.select(
                result_set_id=pl.lit(result_set),
                unit_id=pl.col("code").map_elements(lambda c: key("unit", c), return_dtype=pl.String),
                option_id=pl.col("PosicionCandidato").replace_strict(options, return_dtype=pl.String),
                measure=pl.lit(VOTES, pl.Int16),
                value=pl.col("Votos"),
            )
        )
        total = int(part["Votos"].sum())
        if printed.height != 1:
            raise ValueError(f"{name}: {printed.height} summary rows")
        row = printed.row(0, named=True)
        for measure, field in (
            (REGISTERED, "TotalInscritos"),
            (VALID, "VotosValidos"),
            (INVALID, "MultiplesMarcas"),
            (BLANK, "Blancos"),
            (CAST, "VotosEmitidos"),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=row[field])
        if row["VotosValidos"] != total:
            mismatches.append(f"{name}: valid {row['VotosValidos']:,}, table votes {total:,}")
    facts = pl.concat(values)
    p.frame(
        "result_value",
        facts.select(
            "result_set_id",
            "unit_id",
            "option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    declared = int(national["VotosValidos"][0])
    got = int(facts.filter(pl.col("result_set_id") == key("result_set", "president"))["value"].sum())
    print(f"{KEY}: {len(contest_keys)} contests, {facts.height:,} values, {len(units):,} units", flush=True)
    print(f"presidential valid votes printed {declared:,}, tables {got:,}", flush=True)
    print(f"summary and tables disagree in {len(mismatches)} contests: {mismatches[:10]}", flush=True)
    if mismatches:
        raise ValueError(f"the summary and the tables disagree: {mismatches[:5]}")
    p.accept(
        "accounting.declared",
        f"the Board's summary prints registered voters, valid votes, ballots with multiple marks, blank ballots, and "
        f"ballots cast for each of {len(contest_keys)} demarcations and offices, and the table file reports only "
        "precandidate votes, so those five figures have no stored values to compare with; the summary's valid votes "
        "equal the sum of the table votes in every contest, which the build checks, and both are stored as published",
    )
    return p


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    package_().write()


def emit() -> None:
    package.run("load", packages())
