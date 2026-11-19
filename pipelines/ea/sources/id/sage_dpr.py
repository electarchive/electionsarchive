import re
from datetime import date
from uuid import uuid5

import polars as pl

from ea import raw
from ea.package import NAMESPACE, Package
from ea.sources.id import big, dapil_dpr
from ea.sources.id.kpu_dpr import NATIONAL
from ea.sources.id.kpu_pilpres import PROVINCES
from ea.sources.id.sage_pilpres import CACHE, DOI, RENAMED, SPLIT, URL, _bare
from ea.store import DATA

SOURCE = "id.sage_dpr"
REFERENCE = DATA / "reference" / "id"
DAY = date(2019, 4, 17)
INDONESIA = str(uuid5(NAMESPACE, "polity\x1fID"))
BALLOT = {
    "National Awakening Party": 1,
    "Gerindra Party": 2,
    "Indonesian Democratic Party of Struggle": 3,
    "Golkar": 4,
    "Nasdem Party": 5,
    "Garuda Party": 6,
    "Berkarya Party": 7,
    "Prosperous Justice Party": 8,
    "Perindo Party": 9,
    "United Development Party": 10,
    "Indonesian Solidarity Party": 11,
    "National Mandate Party": 12,
    "People's Conscience Party": 13,
    "Democratic Party": 14,
    "Crescent Star Party": 19,
    "Indonesian Justice and Unity Party": 20,
}
KPU = "Komisi Pemilihan Umum Republik Indonesia"
NOTE = (
    "Votes for each party's list at {stations:,} polling stations (tempat pemungutan suara, TPS), from a "
    "station-level deposit of the 2019 national election results that names the Komisi Pemilihan Umum as its "
    "upstream and gives no per-file URL or retrieval date. The deposit has no district column: each station is "
    "assigned to the House district (dapil) whose regencies and cities include the regency or city the station is "
    "placed in, using the district tables of KPU regulation 6/2023, whose districts hold the same regencies and "
    "cities as 2019's except in Papua and Papua Barat, which were one district each in 2019. {unassigned:,} stations in {provinces} cannot be assigned, because "
    "the deposit gives no regency name for them in provinces with several districts, so they are left out; abroad "
    "stations are not in the deposit. The deposit's {all_stations:,} stations sum to {total:,} votes against the {official:,} in KPU's "
    "district certificates ({difference:+,}){parties}; {votes:,} votes at the stations assigned to a district are "
    "stored. Station codes are the "
    "deposit's own sequence numbers, not KPU identifiers; the units are the ones the presidential supplement "
    "already holds, and {new:,} stations that only the House file has are added, placed in the village (BIG, 2026 "
    "boundaries) whose regency and village names match when that is unique, else in the regency, else in the "
    "province. This is a supplement to KPU's district sets, never the default, and carries no seats or winners."
)


def _contests() -> pl.DataFrame:
    contests = pl.read_parquet(REFERENCE / "contest.parquet").filter(
        (pl.col("pipeline") == "id.kpu_dpr") & (pl.col("election_id") == _election())
    )
    return contests.with_columns(label=pl.col("name").str.replace(r"^Dewan Perwakilan Rakyat, ", ""))


def _election() -> str:
    elections = pl.read_parquet(REFERENCE / "election.parquet").filter(
        (pl.col("pipeline") == "id.kpu_dpr") | (pl.col("date_from") == DAY)
    )
    published = pl.read_parquet(REFERENCE / "contest.parquet").filter(pl.col("pipeline") == "id.kpu_dpr")
    found = elections.filter(pl.col("id").is_in(published["election_id"].implode()) & (pl.col("date_from") == DAY))
    return found["id"].item()


def fetch() -> None:
    raw.fetch(CACHE, URL, suffix=".parquet")
    raw.fetch(dapil_dpr.SOURCE, dapil_dpr.PKPU, suffix=".pdf")


def _dapil_of_regency() -> dict[str, tuple[str, int]]:
    """The 2019 district label of every regency and city code that lies in a multi-district province."""
    areas = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "id.kabkota")
    codes = pl.read_parquet(REFERENCE / "area_code.parquet")
    kabkota: dict[tuple[str, str], str] = {}
    for code, text in areas.join(codes, left_on="id", right_on="area_id").select("code", "name").rows():
        bare = re.sub(r"^(Kabupaten Administrasi|Kota Administrasi|Kabupaten|Kota)\s+", "", text)
        city = text.startswith("Kota ") and not text.startswith("Kota Administrasi")
        kabkota[code[:2], dapil_dpr._squash(("Kota " if city else "") + bare)] = code
    found: dict[str, tuple[str, int]] = {}
    for title, name, _seats, listed in dapil_dpr._lampiran():
        province = dapil_dpr._province(title)
        for item in listed:
            label = re.sub(r"\s*\+\s*Luar\s+Negeri$", "", item)
            key = dapil_dpr.ALIASES.get(dapil_dpr._squash(label), dapil_dpr._squash(label))
            if (province, key) in kabkota:
                found[kabkota[province, key]] = (province, dapil_dpr.ROMAN.get(name.rsplit(" ", 1)[-1], 1))
    return found


def _places(stations: pl.DataFrame) -> pl.DataFrame:
    """Place stations that have no unit yet in a village, a regency, or a province, from names alone."""
    areas = pl.read_parquet(REFERENCE / "area.parquet")
    codes = pl.read_parquet(REFERENCE / "area_code.parquet")
    named = areas.join(codes, left_on="id", right_on="area_id").select("id", "scheme_code", "name", "code")
    regencies: dict[tuple[str, bool, str], list[tuple[str, str]]] = {}
    for area, text, code in named.filter(pl.col("scheme_code") == "id.kabkota").select("id", "name", "code").rows():
        regencies.setdefault((SPLIT.get(code[:2], code[:2]), text.startswith("Kota"), _bare(text)), []).append(
            (area, code)
        )
    villages: dict[tuple[str, str], list[str]] = {}
    for area, text, code in named.filter(pl.col("scheme_code") == "id.desa").select("id", "name", "code").rows():
        villages.setdefault((code[:5], _bare(text)), []).append(area)
    provinces = dict(named.filter(pl.col("scheme_code") == "id.provinsi").select("code", "id").rows())
    rows = []
    for province, regency, village in stations.select("NAME1", "NAME2", "NAME4").unique().rows():
        area = provinces[PROVINCES[province.upper()]]
        if regency is not None:
            label = RENAMED.get(regency, regency)
            if province.upper() == "DKI JAKARTA" and label.startswith("Jakarta"):
                label = f"Kota {label}"
            found = regencies.get((PROVINCES[province.upper()], label.startswith("Kota"), _bare(label)), [])
            if len(found) == 1:
                area, code = found[0]
                if village is not None:
                    near = villages.get((code, _bare(village)), [])
                    if len(near) == 1:
                        area = near[0]
        rows.append((province, regency, village, area))
    return stations.join(
        pl.DataFrame(rows, schema=["NAME1", "NAME2", "NAME4", "area"], orient="row"),
        on=["NAME1", "NAME2", "NAME4"],
        how="left",
        nulls_equal=True,
    )


def package() -> Package:
    codes = pl.read_parquet(REFERENCE / "area_code.parquet").select("area_id", "code")
    votes = (
        pl.scan_parquet(raw.cached(CACHE, URL).path)
        .filter(pl.col("election_type") == "Legislative (lower house)")
        .select("NAME1", "NAME2", "NAME3", "NAME4", "NAME5", "party", "votes", "total_votes", "latitude", "longitude")
        .collect()
    )
    if set(votes["party"].unique()) != set(BALLOT):
        raise ValueError(f"unexpected parties {votes['party'].unique().to_list()}")
    stations = votes.group_by("NAME5").agg(
        pl.col("NAME1").first(),
        pl.col("NAME2").first(),
        pl.col("NAME3").first(),
        pl.col("NAME4").first(),
        pl.col("latitude").first(),
        pl.col("longitude").first(),
        total=pl.col("total_votes").first(),
        sum=pl.col("votes").sum(),
        n=pl.len(),
    )
    if (stations["n"] != len(BALLOT)).any():
        raise ValueError("every station needs all 16 parties")
    if set(stations["NAME1"].str.to_uppercase()) - set(PROVINCES):
        raise ValueError("unknown provinces")

    held = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(
        (pl.col("pipeline") == "id.sage_pilpres") & (pl.col("scheme_code") == "id.tps")
    )
    areas = pl.read_parquet(REFERENCE / "area.parquet").select(area_id="id", scheme="scheme_code")
    held = (
        held.join(areas, on="area_id", how="left")
        .join(codes, left_on="area_id", right_on="area_id", how="left", suffix="_area")
        .select(unit="id", NAME5="code", parent="parent_id", scheme="scheme", area_code="code_area")
        .unique("NAME5")
    )
    stations = stations.join(held, on="NAME5", how="left")
    fresh = stations.filter(pl.col("unit").is_null())
    parents = (
        stations.filter(pl.col("parent").is_not_null())
        .group_by(pl.col("NAME1").str.to_uppercase().alias("province"))
        .agg(pl.col("parent").first())
    )
    parent_of = dict(parents.rows())
    areas_new = _places(fresh)

    # the regency or city code of every station, from the unit it already has or from its names
    regency = (
        pl.when(pl.col("scheme") == "id.desa")
        .then(pl.col("area_code").str.slice(0, 5))
        .when(pl.col("scheme") == "id.kabkota")
        .then(pl.col("area_code"))
    )
    stations = stations.with_columns(regency=regency)
    fresh_codes = (
        areas_new.join(areas.rename({"area_id": "area"}), on="area", how="left")
        .join(codes.rename({"area_id": "area"}), on="area", how="left")
        .select(
            "NAME5",
            "area",
            regency=pl.when(pl.col("scheme") == "id.desa")
            .then(pl.col("code").str.slice(0, 5))
            .when(pl.col("scheme") == "id.kabkota")
            .then(pl.col("code")),
        )
    )
    stations = (
        stations.join(fresh_codes, on="NAME5", how="left", suffix="_new")
        .with_columns(regency=pl.coalesce("regency", "regency_new"))
        .drop("regency_new")
    )

    # the district of every station
    contests = _contests()
    by_label = _numbered(contests)
    by_regency = _dapil_of_regency()
    dapils = stations.select(
        "NAME5",
        contest=pl.struct("NAME1", "regency").map_elements(
            lambda row: _contest(row["NAME1"], row["regency"], by_regency, by_label),
            return_dtype=pl.String,
        ),
    )
    stations = stations.join(dapils, on="NAME5")
    unassigned = stations.filter(pl.col("contest").is_null())
    stations = stations.filter(pl.col("contest").is_not_null())
    print(
        f"{stations.height:,} stations assigned to {stations['contest'].n_unique()} contests, "
        f"{unassigned.height:,} left out ({sorted(unassigned['NAME1'].unique())}), {fresh.height:,} new units"
    )
    members: dict[str, set[str]] = {}
    kabkota = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "id.kabkota")
    for (code,) in kabkota.join(codes, left_on="id", right_on="area_id").select("code").unique().rows():
        province = SPLIT.get(code[:2], code[:2])
        key = (province, 1) if (province, 2) not in by_label else by_regency.get(code)
        if key in by_label:
            members.setdefault(by_label[key], set()).add(code)
    covered = {
        contest: {c for c in codes_ if c is not None}
        for contest, codes_ in stations.group_by("contest").agg(pl.col("regency").unique()).rows()
    }
    return _build(votes, stations, unassigned, contests, parent_of, fresh, members, covered)


def _numbered(contests: pl.DataFrame) -> dict[tuple[str, int], str]:
    """The contest of each district, keyed by its province's code and its number within the province."""
    squashed = {dapil_dpr._squash(name): code for name, code in PROVINCES.items()}
    squashed |= {dapil_dpr._squash(name): code for code, (_, _, name, _) in big.PROVINCES.items()}
    found: dict[tuple[str, int], str] = {}
    for label, contest in contests.select("label", "id").rows():
        head, _, tail = label.rpartition(" ")
        number = dapil_dpr.ROMAN.get(tail)
        code = squashed[dapil_dpr._squash(head if number else label)]
        found[code, number or 1] = contest
    return found


def _contest(
    province: str,
    regency: str | None,
    by_regency: dict[str, tuple[str, int]],
    by_contest: dict[tuple[str, int], str],
) -> str | None:
    code = PROVINCES[province.upper()]
    if (code, 2) not in by_contest:
        return by_contest.get((code, 1))
    return by_contest.get(by_regency.get(regency, ("", 0))) if regency is not None else None


def _build(
    votes: pl.DataFrame,
    stations: pl.DataFrame,
    unassigned: pl.DataFrame,
    contests: pl.DataFrame,
    parent_of: dict[str, str],
    fresh: pl.DataFrame,
    members: dict[str, set[str]],
    covered: dict[str, set[str]],
) -> Package:
    options = pl.read_parquet(REFERENCE / "contest_option.parquet").filter(
        (pl.col("pipeline") == "id.kpu_dpr") & (pl.col("kind") == "list")
    )
    option_of = {
        (contest, position): option
        for option, contest, position in options.select("id", "contest_id", "ballot_position").rows()
    }
    election = _election()
    included = votes.join(stations.select("NAME5", "contest", "unit"), on="NAME5")
    ours = {BALLOT[party]: total for party, total in votes.group_by("party").agg(pl.col("votes").sum()).rows()}
    total = sum(ours.values())
    official = sum(NATIONAL.values())
    over = [
        f"ballot number {number}: {ours[number]:,} against {NATIONAL[number]:,}"
        for number in ours
        if ours[number] > NATIONAL[number]
    ]
    parties = (
        f"; {len(over)} {'party exceeds' if len(over) == 1 else 'parties exceed'} the certified total "
        f"({'; '.join(over)})"
        if over
        else ""
    )

    p = Package(
        f"{SOURCE}/dpr-2019",
        SOURCE,
        "Indonesia's 2019 House of Representatives party-list votes by polling station, placed in villages, as a "
        "non-default supplement to the district sets",
    )
    p.external |= {INDONESIA, election, "id.tps"}
    source = p.id("source", "deposit")
    p.add(
        "source",
        id=source,
        kind="compendium",
        publisher="Noah Dasanaike",
        title="Indonesia 2019 House of Representatives election by polling station, Harvard Dataverse",
        url=DOI,
        license="CC0-1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        notes="Upstream: Komisi Pemilihan Umum Republik Indonesia.",
    )
    p.artifact(raw.cached(CACHE, URL), source)
    regulation = p.id("source", "pkpu-6-2023")
    p.add(
        "source",
        id=regulation,
        kind="official_body",
        publisher=KPU,
        title=dapil_dpr.PKPU_TITLE,
        url=dapil_dpr.PKPU,
        license="Public record: official regulation, no license stated",
        license_url="https://jdih.kpu.go.id",
    )
    p.artifact(raw.cached(dapil_dpr.SOURCE, dapil_dpr.PKPU), regulation)

    new = stations.filter(pl.col("unit").is_null()).height
    note = NOTE.format(
        stations=stations.height,
        unassigned=unassigned.height,
        provinces=", ".join(sorted(unassigned["NAME1"].unique())).replace("Dki ", "DKI ") or "none",
        all_stations=votes["NAME5"].n_unique(),
        total=total,
        votes=included["votes"].sum(),
        official=official,
        difference=total - official,
        parties=parties,
        new=new,
    )
    contest_ids = sorted(included["contest"].unique().to_list())
    sets = {contest: p.id("result_set", contest) for contest in contest_ids}
    p.external |= set(contest_ids)
    for contest, result_set in sets.items():
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=False,
            completeness=None,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note)
        missing = len(members.get(contest, set()) - covered.get(contest, set()))
        if missing:
            p.add(
                "note",
                id=p.id("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=(
                    f"Stations in {missing} of this district's {len(members[contest])} regencies and cities are "
                    "missing, because the deposit names no regency for them, so these votes cover part of the district."
                ),
            )

    # units that production lacks
    if fresh.height:
        scheme_area = stations.filter(pl.col("unit").is_null())
        scheme_area = scheme_area.with_columns(
            id=pl.col("NAME5").map_elements(lambda code: p.id("unit", code), return_dtype=pl.String),
            parent=pl.col("NAME1").str.to_uppercase().replace_strict(parent_of, default=None),
        )
        p.frame(
            "reporting_unit",
            scheme_area.select(
                "id",
                polity_id=pl.lit(INDONESIA),
                scheme_code=pl.lit("id.tps"),
                code="NAME5",
                parent_id="parent",
                area_relation=pl.lit("within"),
                area_id="area",
                kind=pl.lit("standard"),
            ),
        )
        p.frame(
            "name",
            scheme_area.select(
                id=pl.concat_str(pl.lit(SOURCE), pl.lit("name"), "id").map_elements(
                    lambda key: str(uuid5(NAMESPACE, key)), return_dtype=pl.String
                ),
                entity_id="id",
                text=pl.concat_str("NAME4", "NAME3", "NAME2", "NAME1", separator=", ", ignore_nulls=True),
                lang=pl.lit("id"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )
        p.external |= set(scheme_area["area"].drop_nulls().unique().to_list())
        p.external |= set(scheme_area["parent"].drop_nulls().unique().to_list())
    unit_of = stations.with_columns(
        unit=pl.coalesce("unit", pl.col("NAME5").map_elements(lambda code: p.id("unit", code), return_dtype=pl.String))
    )
    held = set(stations["unit"].drop_nulls().unique().to_list())
    p.external |= held

    table = included.join(unit_of.select("NAME5", unit_all="unit"), on="NAME5").with_columns(
        result_set=pl.col("contest").replace_strict(sets),
        option=pl.struct("contest", "party").map_elements(
            lambda row: option_of[row["contest"], BALLOT[row["party"]]], return_dtype=pl.String
        ),
    )
    p.external |= set(table["option"].unique().to_list())
    fact = table.select(
        result_set_id="result_set",
        unit_id="unit_all",
        option_id="option",
        channel=pl.lit(0, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
    )
    p.frame("result_value", fact.with_columns(flags=pl.lit(0, pl.Int16), value=table["votes"].cast(pl.Int64)))
    p.frame("value_locator", fact.with_columns(page_id=pl.lit(None, pl.String), source_id=pl.lit(source)))
    return p


def parse() -> None:
    p = package()
    p.write()
