import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.leh"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/2026if_/https://commonslibrary.parliament.uk/content/uploads"
WORKBOOKS = {
    2021: f"{WAYBACK}/2023/02/local_elections_2021_results-2.xlsx",
    2022: f"{WAYBACK}/2023/02/local-elections-2022.xlsx",
}
PAGES = {
    2021: "https://commonslibrary.parliament.uk/data-tools-and-resources/parliament-elections-data/2021-local-elections-handbook-and-dataset/",
    2022: "https://commonslibrary.parliament.uk/data/parliament-elections-data/2022-local-elections-handbook-and-dataset/",
}
ONS = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
LOOKUPS = {
    2021: ("20", f"{ONS}/WD20_LAD20_CTY20_OTH_UK_LU_v2_27715a77546b4b5a9746baf703dd9a05/FeatureServer/0/query"),
    2022: ("22", f"{ONS}/WD22_LAD22_CTY22_OTH_UK_LU/FeatureServer/0/query"),
    2025: ("25", f"{ONS}/WD25_LAD25_CTYUA25_RGN25_CTRY25_UK_LU/FeatureServer/0/query"),
}
PAGE = 2000
RENAMES = {
    "County name": "county",
    "Local authority code": "la_code",
    "Local authority name": "la_name",
    "Ward/ED code": "ward_code",
    "Ward code": "ward_code",
    "Ward/ED name": "ward_name",
    "Ward name": "ward_name",
    "Candidate name": "name",
    "Candidate gender": "gender",
    "Incumbent": "incumbent",
    "Inumbent": "incumbent",
    "Votes": "votes",
    "Elected": "elected",
    "Party name": "party",
    "Vacancies": "seats",
    "Type": "type",
    "Electorate": "electorate",
    "Turnout (%)": "turnout",
    "Candidate number": "number",
}


def sheet(year: int, name: str) -> pl.DataFrame:
    path = raw.cached(SOURCE, WORKBOOKS[year]).path
    return pl.read_excel(path, sheet_name=name, has_header=True, read_options={"header_row": 1}, infer_schema_length=0)


def lookup_urls(year: int) -> list[str]:
    suffix, base = LOOKUPS[year]
    return [
        f"{base}?where=1%3D1&outFields=*&returnGeometry=false&orderByFields=WD{suffix}CD&resultOffset={offset}"
        f"&resultRecordCount={PAGE}&f=json"
        for offset in range(0, 10 * PAGE, PAGE)
    ]


def lookup(year: int) -> pl.DataFrame:
    suffix, _ = LOOKUPS[year]
    rows = []
    for url in lookup_urls(year):
        try:
            artifact = raw.cached(SOURCE, url)
        except LookupError:
            break
        features = json.loads(Path(artifact.path).read_text())["features"]
        rows.extend(f["attributes"] for f in features)
    return pl.DataFrame(rows, infer_schema_length=None).select(
        ward=pl.col(f"WD{suffix}CD"),
        ward_name=pl.col(f"WD{suffix}NM"),
        la=pl.col(f"LAD{suffix}CD"),
        la_name=pl.col(f"LAD{suffix}NM"),
        county=pl.col(f"CTY{'UA' if suffix == '25' else ''}{suffix}CD"),
        county_name=pl.col(f"CTY{'UA' if suffix == '25' else ''}{suffix}NM"),
    )


def mend(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeEncodeError, UnicodeDecodeError:
        return text


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", mend(text)).encode("ascii", "ignore").decode().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’‘`æ]", "", text)).strip()


def resolve(wards: pl.DataFrame, candidates: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    wards = wards.filter(pl.col("ward_code") != "NA").with_columns(
        pl.col("ward_name").map_elements(mend, return_dtype=pl.String)
    )
    candidates = candidates.filter(pl.col("ward_code") != "NA").with_columns(
        pl.col("ward_name").map_elements(mend, return_dtype=pl.String)
    )
    by_name: dict[tuple[str, str], set[str]] = {}
    for year in LOOKUPS:
        for row in lookup(year).iter_rows(named=True):
            by_name.setdefault((row["la"], fold(row["ward_name"])), set()).add(row["ward"])
    keys = ["year", "la_code", "ward_code", "ward_name"]
    seen = pl.concat([wards.select(keys), candidates.select(keys)]).unique()
    clashes = (
        seen.with_columns(folded=pl.col("ward_name").map_elements(fold, return_dtype=pl.String))
        .group_by("year", "la_code", "ward_code")
        .agg(pl.col("folded").n_unique().alias("names"))
        .filter(pl.col("names") > 1)
    )
    fixes: list[tuple[int, str, str, str, str]] = []
    for year, la, code in clashes.select("year", "la_code", "ward_code").iter_rows():
        names = seen.filter((pl.col("year") == year) & (pl.col("la_code") == la) & (pl.col("ward_code") == code))[
            "ward_name"
        ]
        for name in names:
            found = by_name.get((la, fold(name)), set())
            fixes.append((year, la, code, name, found.pop() if len(found) == 1 else code))
    print("clashing codes:", fixes)
    fixed = pl.DataFrame(
        fixes, schema=["year", "la_code", "ward_code", "ward_name", "fixed"], orient="row"
    ).with_columns(pl.col("year").cast(pl.Int32))
    wards = (
        wards.join(fixed, on=keys, how="left").with_columns(ward_code=pl.coalesce("fixed", "ward_code")).drop("fixed")
    )
    candidates = (
        candidates.join(fixed, on=keys, how="left")
        .with_columns(ward_code=pl.coalesce("fixed", "ward_code"))
        .drop("fixed")
    )
    return wards, candidates


def fetch() -> None:
    for url in WORKBOOKS.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for year in LOOKUPS:
        for url in lookup_urls(year):
            artifact = raw.fetch(SOURCE, url, suffix=".json")
            if not json.loads(Path(artifact.path).read_text())["features"]:
                break


def parse() -> None:
    candidates, wards, best = [], [], []
    for year in WORKBOOKS:
        found = sheet(year, "Candidates-results")
        found = found.rename({old: new for old, new in RENAMES.items() if old in found.columns})
        found = found.select(
            year=pl.lit(year),
            county=pl.col("county"),
            la_code=pl.col("la_code"),
            ward_code=pl.col("ward_code"),
            ward_name=pl.col("ward_name"),
            number=pl.col("number").cast(pl.Int64),
            name=pl.col("name"),
            gender=pl.col("gender"),
            incumbent=pl.col("incumbent").cast(pl.Int64) == 1,
            votes=pl.col("votes").cast(pl.Int64),
            elected=pl.col("elected").cast(pl.Int64) == 1,
            party=pl.col("party"),
        )
        candidates.append(found)
        ward = sheet(year, "Wards-results")
        ward = ward.rename({old: new for old, new in RENAMES.items() if old in ward.columns})
        parties = [column for column in ward.columns if column.isupper() and column != "NA"]
        full = ward.with_columns(year=pl.lit(year))
        ward = ward.select(
            year=pl.lit(year),
            la_code=pl.col("la_code"),
            la_name=pl.col("la_name"),
            county=pl.col("county"),
            ward_code=pl.col("ward_code"),
            ward_name=pl.col("ward_name"),
            seats=pl.col("seats").cast(pl.Int64, strict=False),
            type=pl.col("type"),
            electorate=pl.col("electorate").cast(pl.Int64, strict=False),
            turnout=pl.col("turnout").cast(pl.Float64, strict=False),
        )
        wards.append(ward)
        best.append(
            full.select("year", "ward_code", *parties)
            .unpivot(parties, index=["year", "ward_code"], variable_name="party", value_name="votes")
            .with_columns(pl.col("votes").cast(pl.Int64, strict=False))
            .filter(pl.col("votes") > 0)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    wards, candidates = resolve(pl.concat(wards), pl.concat(candidates))
    wards = [wards]
    candidates = [candidates]
    pl.concat(candidates).write_parquet(PARSED / "candidates.parquet")
    pl.concat(wards).write_parquet(PARSED / "wards.parquet")
    pl.concat(best).write_parquet(PARSED / "best.parquet")
    print(pl.concat(candidates).group_by("year").len(), pl.concat(wards).group_by("year").len())


OPL = "https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/"
DAYS = {2021: date(2021, 5, 6), 2022: date(2022, 5, 5)}
VOTES, REGISTERED = 1, 2
GENDERS = {"M": "male", "F": "female"}
NOT_PARTIES = {"IND", "ND"}
ALIASES = {
    "UKIP": "UK Independence Party",
    "TUSC": "Trade Unionist and Socialist Coalition",
}
METHODOLOGY = (
    "Compiled by the Elections Centre (Rallings, Thrasher, and Bunting) from council websites and correspondence with "
    "councils and released by the House of Commons Library. The workbook gives the year, not the poll day; the poll day "
    "is the one on which the year's local elections were held. Candidate names are printed as surname and initials. "
    "Wards without a vote count were won without a poll."
)


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    wards = pl.read_parquet(PARSED / "wards.parquet")
    names: dict[str, str] = {}
    for year in LOOKUPS:
        for row in lookup(year).sort("ward").iter_rows(named=True):
            names[row["ward"]] = row["ward_name"]
    parties: dict[str, str] = {}
    for year in WORKBOOKS:
        sheet_ = pl.read_excel(
            raw.cached(SOURCE, WORKBOOKS[year]).path,
            sheet_name="Party names",
            has_header=False,
            infer_schema_length=0,
        )
        for abbreviation, label, *_ in sheet_.iter_rows():
            if abbreviation and label and abbreviation != "Party abbreviation":
                parties[abbreviation] = ALIASES.get(abbreviation, mend(label))
    counties: dict[str, tuple[str, str]] = {}
    lads: dict[str, str] = {}
    for year in LOOKUPS:
        for row in lookup(year).iter_rows(named=True):
            lads[row["la"]] = row["la_name"]
            if row["county"] is not None:
                counties[fold(row["county_name"])] = (row["county"], row["county_name"])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English and Welsh council elections 2021 and 2022 by ward, from the Elections Centre's handbook datasets",
            polity="GB",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        gb = cs.polity("GB")
        for code, label, rank, noun, kind in (
            ("gb.lad", "council area", 2, "council area", "administrative"),
            ("gb.ward", "ward", 3, "ward", "electoral_district"),
        ):
            if conn.execute("select 1 from scheme where code = %s", [code]).fetchone() is None:
                cs.add("scheme", code=code, polity_id=gb, kind=kind, rank=rank, noun=noun)
        countries = {"E": cs.area("gb.country", "ENG"), "W": cs.area("gb.country", "WLS")}
        if cs.minted & set(countries.values()):
            raise ValueError("publish gb.umit first: the countries must exist")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.parliament first: the electoral systems must exist")

        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB' order by p.id desc"
        ).fetchall():
            known[fold(text)] = id
        party_ids: dict[str, UUID | None] = {}

        def party_of(abbreviation: str) -> UUID | None:
            if abbreviation in NOT_PARTIES:
                return None
            if abbreviation not in party_ids:
                label = parties[abbreviation]
                found = known.get(fold(label))
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted and found not in party_ids.values():
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                party_ids[abbreviation] = found
            return party_ids[abbreviation]

        sources = {}
        for year, url in WORKBOOKS.items():
            sources[year] = cs.source("House of Commons Library", f"Local election results {year}")
            cs.add(
                "source",
                id=sources[year],
                kind="compendium",
                publisher="House of Commons Library",
                title=f"Local election results {year}",
                url=PAGES[year],
                license="Open Parliament Licence v3.0",
                license_url=OPL,
                attribution="Contains Parliamentary information licensed under the Open Parliament Licence v3.0. Data collected by the Elections Centre.",
                notes=METHODOLOGY,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[year])

        slugs: set[str] = set()
        council_paths: dict[str, str] = {}
        areas: dict[str, UUID] = {}
        polities: dict[str, UUID] = {}
        bodies: dict[str, UUID] = {}
        seen_wards: set[str] = set()

        def council_of(la_code: str, ward_code: str, county: str) -> tuple[str, str, bool]:
            if ward_code.startswith("E58"):
                code, label = counties[fold(county)]
                return code, label, True
            return la_code, lads.get(la_code, la_code), False

        def council(code: str, label: str, is_county: bool) -> None:
            if code in polities:
                return
            areas[code] = cs.area("gb.lad", code)
            if areas[code] in cs.minted:
                cs.add("area", id=areas[code], scheme_code="gb.lad")
                cs.add("area_code", area_id=areas[code], code=code, valid_from=None, valid_to=None)
                cs.add("area_parent", area_id=areas[code], parent_id=countries[code[0]], valid_from=None, valid_to=None)
                name(areas[code], label)
            polities[code] = cs.key("gb.polity", code)
            path = f"gb/{slug(label)}"
            if path in slugs:
                path = f"{path}-{code.lower()}"
            slugs.add(path)
            council_paths[code] = path
            if polities[code] in cs.minted:
                cs.add("polity", id=polities[code], kind="county" if is_county else "municipality", iso_code=None)
                name(polities[code], f"{label} Council")
                cs.add("polity_parent", polity_id=polities[code], parent_id=cs.polity("GB-WLS" if code.startswith("W") else "GB-ENG"))
                cs.add("slug", path=path, entity_id=polities[code])
                cs.add("polity_territory", polity_id=polities[code], area_id=areas[code])
            bodies[code] = cs.key("gb.body", code)
            if bodies[code] in cs.minted:
                cs.add("body", id=bodies[code], polity_id=polities[code], kind="council")
                name(bodies[code], f"{label} Council")

        for year, day in DAYS.items():
            held = wards.filter(pl.col("year") == year)
            ballots = candidates.filter(pl.col("year") == year)
            ward_rows = {(row["la_code"], row["ward_code"]): row for row in held.iter_rows(named=True)}
            groups = ballots.group_by("la_code", "ward_code", "ward_name", maintain_order=True)
            for (la_code, ward_code, ward_name), lines in groups:
                lines = lines.sort("number")
                ward_row = ward_rows.get((la_code, ward_code))
                code, label, is_county = council_of(la_code, ward_code, lines["county"][0])
                council(code, label, is_county)
                path = council_paths[code]
                election = cs.key("gb.election", f"{day}:local:{code}")
                if election in cs.minted and election not in seen_wards:
                    seen_wards.add(election)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=polities[code],
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{year} {label} Council election")
                    cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                ward_area = cs.area("gb.ward", ward_code)
                display = names.get(ward_code)
                display = display if display and fold(display) == fold(ward_name) else ward_name
                display = display.title() if display.isupper() else display
                if ward_code not in areas:
                    areas[ward_code] = ward_area
                    if ward_area in cs.minted:
                        cs.add("area", id=ward_area, scheme_code="gb.ward")
                        cs.add("area_code", area_id=ward_area, code=ward_code, valid_from=None, valid_to=None)
                        cs.add("area_parent", area_id=ward_area, parent_id=areas[code], valid_from=None, valid_to=None)
                        name(ward_area, display)
                unit = cs.unit("gb.ward", ward_code)
                if unit in cs.minted and ward_code not in seen_wards:
                    seen_wards.add(ward_code)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polities[code],
                        scheme_code="gb.ward",
                        code=ward_code,
                        area_relation="equals",
                        area_id=ward_area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, display)
                seats = (ward_row["seats"] if ward_row else None) or int(lines["elected"].sum())
                if int(lines["elected"].sum()) != seats:
                    raise ValueError(f"{year} {ward_code}: {lines['elected'].sum()} elected for {seats} seats")
                tacit = ward_row is None or int(lines["votes"].sum()) == 0
                contest_key = f"{day}:local:{ward_code}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[code],
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{display} ward, {label} Council, {year}")
                ward_slug = slug(display)
                cs.add("slug", path=f"{path}/{day}-local/{ward_slug}-{ward_code.lower()}", entity_id=contest)
                result_set = cs.key("gb.leh.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=sources[year],
                    is_default=True,
                    completeness=1,
                )
                if ward_row and ward_row["electorate"]:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=REGISTERED,
                        count_round=0,
                        flags=0,
                        value=ward_row["electorate"],
                    )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    option = cs.key("gb.leh.option", option_key)
                    surname_first = line["name"]
                    parts = surname_first.split()
                    initials = []
                    while len(parts) > 1 and re.fullmatch(r"(?:[A-Z]\.?)+", parts[-1]):
                        initials.insert(0, parts.pop())
                    full = " ".join([*initials, *parts])
                    person = cs.key("gb.leh.person", option_key)
                    cs.add("person", id=person, gender=GENDERS.get(line["gender"]))
                    name(person, full)
                    owner = party_of(line["party"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.leh.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=surname_first,
                        member_of_party_id=owner,
                        is_independent=line["party"] == "IND",
                        is_incumbent=line["incumbent"],
                    )
                    if not tacit:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=0,
                            flags=0,
                            value=line["votes"],
                        )
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
