"""Czech municipal council elections (volby do zastupitelstev obcí) 2002-2026 by polling district from volby.cz.

`cz.volby/kv-registry` holds what every date shares: the electoral system, one council body per council, and the
parties the main registry lacks. Each election date is one package, `cz.volby/kv-<date>`, built on `cz.volby/geo`,
`cz.volby/registry`, and `cz.volby/kv-registry`: every council that voted that day is one contest (one per election
district where a council has several), with its lists, candidates, and each candidate's preference votes per polling
district. The regular elections run over two days; the by-elections (new, supplementary, and repeat elections) run on
one. The open data also holds dates that only republish a few councils' counts after a court ruling; those are not
elections and are left out.
"""

import functools
import re
from collections import Counter
from datetime import date, timedelta
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.cz.volby import (
    BASE,
    CZECHIA,
    NO_PARTY,
    SOURCE,
    TERRITORY,
    VOLBY_SOURCE,
    _nuts,
    _parties,
    _places,
    _units,
    _utf8,
    area,
    clean,
    key,
    party,
    person_name,
    table,
    url,
)

MUNICIPAL = {
    2002: ("kv2002", "KV2002_data_20230224_csv.zip", "KV2002_reg_20230224_csv.zip", "KV2002_cisel_20230224_csv.zip"),
    2006: ("kv2006", "KV2006_data_20230224_csv.zip", "KV2006_reg_20230224_csv.zip", "KV2006_cisel_20230224_csv.zip"),
    2010: ("kv2010", "KV2010_data_20230224_csv.zip", "KV2010_reg_20230224_csv.zip", "KV2010_cisel_20230224_csv.zip"),
    2014: ("kv2014", "KV2014_data_20230224_csv.zip", "KV2014_reg_20230224_csv.zip", "KV2014_cisel_20230224_csv.zip"),
    2018: ("kv2018", "KV2018_data_20230224_csv.zip", "KV2018_reg_20230224_csv.zip", "KV2018_cisel_20230224_csv.zip"),
    2022: ("kv2022", "KV2022_data_20260328_csv.zip", "KV2022reg20260328_csv.zip", "KV2022ciselniky20260328_csv.zip"),
    2026: ("kv2026", "KV2026_data_20261011_csv.zip", "KV2026reg20261011_csv.zip", "KV2026ciselniky20261011_csv.zip"),
}
REGULAR = {
    2002: "20021101",
    2006: "20061020",
    2010: "20101015",
    2014: "20141010",
    2018: "20181005",
    2022: "20220923",
    2026: "20261009",
}
VOTES, PREFERENCE = 1, 7
FIRST = min(MUNICIPAL)
REGISTRY = "cz.volby/kv-registry"
ACCOUNTING = ((2, "VOL_SEZNAM"), (14, "VYD_OBALKY"), (3, "ODEVZ_OBAL"), (4, "PL_HL_CELK"))
SYSTEM = key("electoral_system", "kv", FIRST)
RULES: dict[str, object] = {
    "formula": "dhondt",
    "threshold": {"level": "list", "scope": "contest", "percent": 5},
    "lists": {"order": "flexible", "vacancy": "next_on_list"},
}
SYSTEM_METHOD = (
    "Each municipal council is elected for four years from competing candidate lists, in one constituency or, in a "
    "few statutory cities, in several election districts. Every voter has as many votes as the council has seats and "
    "may give them to candidates of any list or mark a whole list. A list with under 5% of the valid votes wins no "
    "seats. The seats go to lists in proportion to their votes, and within a list to the candidates with the most "
    "preference votes. The archive records the rules as D'Hondt with the 5% threshold, which reproduces the declared "
    "seats of 6,212 of the 6,388 councils of the 2022 regular election; it is an approximation of the statutory "
    "procedure, and the seats are stored as the Ministry and the Czech Statistical Office declared them."
)
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as published in the volby.cz open data. "
    "Registered voters are the electoral roll, voters are the official envelopes issued, and ballots cast are the "
    "envelopes put in the box. Each voter has as many votes as the council has seats, so the valid votes are the "
    "votes given to candidates and lists, not ballots, and exceed the envelopes. A list's votes per polling district "
    "are the sum of the preference votes of its candidates there; each candidate's votes are stored on a candidate "
    "line under the list, and zero counts are not stored. Polling districts of the statutory cities and of Prague "
    "are placed in the whole municipality. Seats and elected members are as declared in the candidate register."
)


def declared_url(day: str) -> str:
    year = _owner(day)
    if year == max(MUNICIPAL):
        return f"{BASE}/appdata/kv{year}/{day}/odata/vysledky.xml"
    return f"{BASE}/pls/kv{year}/vysledky?datumvoleb={day}"


def fetch() -> None:
    for folder, *names in MUNICIPAL.values():
        for name in names:
            raw.fetch(SOURCE, url(folder, name), suffix=".zip")
    for day in elections():
        try:
            raw.fetch(SOURCE, declared_url(day), suffix=".xml")
        except httpx.HTTPStatusError as failure:
            print(f"{declared_url(day)}: {failure.response.status_code}", flush=True)


@functools.cache
def _notice(year: int) -> dict[str, str]:
    """The description the open data gives each of a year's dates."""
    folder, _, _, cis = MUNICIPAL[year]
    try:
        rows = table(folder, cis, "kvdatumvoleb").iter_rows(named=True)
    except LookupError:
        return {REGULAR[year]: "Řádné volby"}
    return {row["DATUMVOLEB"]: row["POPISVOLEB"] or "" for row in rows}


@functools.cache
def elections() -> list[str]:
    """Every date that held an election: the regular dates, and the dates a Ministry notice called new elections."""
    days: list[str] = []
    for year, (folder, data_zip, _, _) in MUNICIPAL.items():
        counted = set(table(folder, data_zip, "kvt3")["DATUMVOLEB"]) if year != max(MUNICIPAL) else {REGULAR[year]}
        for day, text in _notice(year).items():
            if text.startswith(("Řádné", "Sdělení")) and day in counted:
                days.append(day)
    return sorted(days)


def packages() -> list[str]:
    return [REGISTRY, *(f"cz.volby/kv-{_iso(day)}" for day in elections())]


def parse() -> None:
    registry().write()
    for year in MUNICIPAL:
        for day in elections():
            if _owner(day) == year:
                municipal(day).write()
        _load.cache_clear()


def emit() -> None:
    package.run("load", packages())


def _iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


@functools.cache
def _owner(day: str) -> int:
    for year in MUNICIPAL:
        if day in _notice(year):
            return year
    raise LookupError(day)


def body(code: str) -> str:
    return key("body", "kv", code)


@functools.lru_cache(maxsize=1)
def _load(year: int) -> dict[str, pl.DataFrame]:
    """A year's tables, the date column filled in where the open data leaves it out."""
    folder, data_zip, reg, cis = MUNICIPAL[year]
    tables = {
        **{name: table(folder, data_zip, name) for name in ("kvt3", "kvhl")},
        **{name: table(folder, reg, name) for name in ("kvrk", "kvros", "kvrzcoco")},
        "kvcoco": table(folder, cis, "kvcoco"),
    }
    for name, frame in tables.items():
        if "DATUMVOLEB" not in frame.columns:
            tables[name] = frame.with_columns(DATUMVOLEB=pl.lit(REGULAR[year]))
    return tables


@functools.cache
def _independent() -> frozenset[str]:
    """The codes any year's code list marks as independent candidates; the 2002 list leaves the kind blank."""
    return frozenset(
        row["VSTRANA"]
        for folder, _, _, cis in MUNICIPAL.values()
        for row in table(folder, cis, "cvs").iter_rows(named=True)
        if row.get("TYPVS") == "N"
    )


def _named(codes: set[str], cvs: dict[str, dict[str, str | None]]) -> dict[str, dict[str, str | None]]:
    return {c: cvs[c] for c in codes - {None, "", *NO_PARTY} - _independent() if c in cvs}


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Czech municipal council bodies, their electoral system, and the parties of municipal lists from volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry"],
    )
    p.external |= {CZECHIA, TERRITORY}
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=CZECHIA,
        valid_from=date(2002, 1, 1),
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add("note", id=p.id("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
    kinds: dict[str, str] = {}
    names: dict[str, str] = {}
    used: set[str] = set()
    cvs: dict[str, dict[str, str | None]] = {}
    for folder, _, reg, cis in MUNICIPAL.values():
        for name in (reg, cis):
            p.artifact(raw.cached(SOURCE, url(folder, name)), VOLBY_SOURCE)
        for row in table(folder, cis, "kvcoco").iter_rows(named=True):
            names[row["KODZASTUP"]] = clean(row["NAZEVZAST"])
            kinds[row["KODZASTUP"]] = row["DRUHZASTUP"]
        for row in table(folder, cis, "cvs").iter_rows(named=True):
            cvs[row["VSTRANA"]] = row
        rk, ros = table(folder, reg, "kvrk"), table(folder, reg, "kvros")
        used |= set(rk["PSTRANA"]) | set(rk["NSTRANA"]) | set(ros["VSTRANA"])
    titles = {
        "1": "Zastupitelstvo obce",
        "2": "Zastupitelstvo města",
        "3": "Zastupitelstvo statutárního města",
        "4": "Zastupitelstvo hl.m.Prahy",
        "5": "Zastupitelstvo městské části nebo městského obvodu",
        "6": "Zastupitelstvo městysu",
    }
    for code, name in sorted(names.items()):
        p.add("body", id=body(code), polity_id=CZECHIA, kind="council")
        p.name(body(code), f"Council of {name}", "en", "english")
        p.name(body(code), f"{titles[kinds[code]]} {name}", "cs", "native", False)
    known = _parties()
    for code, row in sorted(_named(used, cvs).items(), key=lambda item: int(item[0])):
        id = party(code)
        if id in known:
            continue
        coalition = row.get("TYPVS") == "K" or (row.get("TYPVS") is None and int(row.get("POCSTR_SLO") or 1) > 1)
        p.add("party", id=id, kind="bloc" if coalition else "party", polity_id=CZECHIA)
        p.add("external_id", entity_id=id, scheme="cz.volby.party", value=code)
        p.name(id, clean(row["NAZEVCELK"]), "cs", "official")
        if row.get("ZKRATKAV30"):
            p.name(id, clean(row["ZKRATKAV30"]), "cs", "short", False)
        if row.get("ZKRATKAV8"):
            p.name(id, clean(row["ZKRATKAV8"]), "cs", "abbreviation", False)
    return p


def _declared(day: str) -> ElementTree.Element:
    data = raw.cached(SOURCE, declared_url(day)).path.read_bytes()
    data = re.sub(rb'encoding="[^"]+"', b'encoding="utf-8"', data, count=1)
    if not _utf8(data):
        data = data.decode("cp1250").encode()
    root = ElementTree.fromstring(data)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def _district(
    code: str,
    parent: str,
    okres: str,
    kraj: str,
    towns: set[str],
    districts: set[str],
    nuts: dict[str, str],
    wards: set[str],
) -> str:
    if code in wards:
        return area("cz.mestska_cast", code)
    if code in towns:
        return area("cz.obec", code)
    if parent in towns:
        return area("cz.obec", parent)
    if nuts.get(okres) in districts:
        return area("cz.okres", nuts[okres])
    return TERRITORY


def municipal(day: str) -> Package:
    year = _owner(day)
    folder, data_zip, reg, cis = MUNICIPAL[year]
    regular = day == REGULAR[year]
    when = date(int(day[:4]), int(day[4:6]), int(day[6:]))
    last = when + timedelta(days=1) if regular else when
    iso = _iso(day)
    p = Package(
        f"cz.volby/kv-{iso}",
        SOURCE,
        (
            f"The {year} Czech municipal council elections"
            if regular
            else f"The Czech municipal council elections of {when:%-d %B %Y}"
        )
        + ", with preference votes, by polling district from volby.cz",
        depends=["cz.volby/geo", "cz.volby/registry", REGISTRY],
    )
    p.external |= {CZECHIA, TERRITORY}
    p.artifact(raw.cached(SOURCE, url(folder, data_zip)), VOLBY_SOURCE)
    p.artifact(raw.cached(SOURCE, declared_url(day)), VOLBY_SOURCE)
    for name in (reg, cis):
        p.uses(raw.cached(SOURCE, url(folder, name)))

    data = _load(year)
    t3 = data["kvt3"].filter(pl.col("DATUMVOLEB") == day)
    hl = data["kvhl"].filter(pl.col("DATUMVOLEB") == day)
    rk = data["kvrk"].filter(pl.col("DATUMVOLEB") == day)
    ros = data["kvros"].filter(pl.col("DATUMVOLEB") == day)
    zast = data["kvrzcoco"].filter(pl.col("DATUMVOLEB") == day)
    parent = {row["KODZASTUP"]: row["NADRZASTUP"] for row in data["kvcoco"].iter_rows(named=True)}
    nuts = _nuts(folder, cis)
    places_built = _places()
    towns = set(places_built.filter(pl.col("scheme_code") == "cz.obec")["code"])
    districts = set(places_built.filter(pl.col("scheme_code") == "cz.okres")["code"])
    wards = set(places_built.filter(pl.col("scheme_code") == "cz.mestska_cast")["code"])

    election = p.id("election", "kv", iso)
    kind = "local" if regular else "by_election"
    p.add("election", id=election, polity_id=CZECHIA, kind=kind, date_from=when, date_to=last, status="held")
    if regular:
        p.name(election, f"{year} Czech municipal elections", "en", "official")
        p.name(election, f"Volby do zastupitelstev obcí {year}", "cs", "native", False)
        path = f"cz/{iso}-municipal"
    else:
        p.name(election, f"Czech municipal by-elections of {when:%-d %B %Y}", "en", "official")
        p.name(election, f"Doplňovací a nové volby do zastupitelstev obcí {when:%-d. %-m. %Y}", "cs", "native", False)
        path = f"cz/{iso}-municipal-by-election"
    p.add("slug", path=path, entity_id=election, is_current=True)

    units: dict[tuple[str, str], str] = {}
    coco = zast.select("OBEC", "OKRES", "KRAJ").unique("OBEC").with_columns(OBEC_PREZ=pl.col("OBEC"))
    first_rows = t3.unique(["OBEC", "OKRSEK"], keep="first", maintain_order=True)
    by_id = _units(p, when, nuts, coco, first_rows)
    for row in first_rows.iter_rows(named=True):
        units[(row["OBEC"], row["OKRSEK"])] = by_id[row["ID_OKRSKY"]]

    # one contest per council and election district
    contests: dict[tuple[str, str], str] = {}
    sets: dict[tuple[str, str], str] = {}
    sizes: dict[tuple[str, str], int] = {}
    running = set(zip(t3["KODZASTUP"], t3["COBVODU"], strict=True))
    voted = pl.col("KODZASTUP") + "|" + pl.col("COBVODU")
    running_keys = [f"{a}|{b}" for a, b in running]
    ros = ros.filter(voted.is_in(running_keys))
    rk = rk.filter(voted.is_in(running_keys))
    for row in (
        zast.unique(["KODZASTUP", "COBVODU"], keep="first", maintain_order=True)
        .sort("KODZASTUP", pl.col("COBVODU").cast(pl.Int32))
        .iter_rows(named=True)
    ):
        council, district = row["KODZASTUP"], row["COBVODU"]
        if (council, district) not in running:
            continue
        contest = contests[(council, district)] = p.id("contest", "kv", iso, council, district)
        sizes[(council, district)] = int(row["MANDATY"])
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body(council),
            district_area_id=_district(
                council, parent.get(council, ""), row["OKRES"], row["KRAJ"], towns, districts, nuts, wards
            ),
            magnitude=int(row["MANDATY"]),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        title = clean(row["NAZEVZAST"]) + (f", district {district}" if row["OBVODY"] != "0" else "")
        p.name(contest, title, "cs", "official")
        p.add(
            "slug",
            path=f"{path}/{changeset.slug(title)}-{council}" + (f"-{district}" if row["OBVODY"] != "0" else ""),
            entity_id=contest,
            is_current=True,
        )
        result_set = sets[(council, district)] = p.id("result_set", "kv", iso, council, district)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VOLBY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")

    cvs = {row["VSTRANA"]: row for row in table(folder, cis, "cvs").iter_rows(named=True)}
    registry_known = _parties() | {
        party(code) for code in _named(set(ros["VSTRANA"]) | set(rk["PSTRANA"]) | set(rk["NSTRANA"]), cvs)
    }

    def party_of(code: str | None) -> str | None:
        return party(code) if code is not None and party(code) in registry_known else None

    options: dict[tuple[str, str, str], str] = {}
    declared_rows: list[dict[str, object]] = []
    outcomes: list[dict[str, object]] = []
    won: Counter[tuple[str, str, str]] = Counter()
    # lists
    for row in ros.sort("KODZASTUP", pl.col("COBVODU").cast(pl.Int32), pl.col("POR_STR_HL").cast(pl.Int32)).iter_rows(
        named=True
    ):
        council, district, number = row["KODZASTUP"], row["COBVODU"], row["POR_STR_HL"]
        option = options[(council, district, number)] = p.id("option", "kv", iso, council, district, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contests[(council, district)],
            kind="list",
            party_id=party_of(row["VSTRANA"]) if row["POCSTR_SLO"] in (None, "1") else None,
            ballot_position=int(number),
            ballot_label=clean(row["NAZEVCELK"]),
        )
        if int(row["MAND_STR"] or 0):
            outcomes.append(
                {
                    "result_set_id": sets[(council, district)],
                    "option_id": option,
                    "candidacy_id": None,
                    "kind": "won",
                    "seats": int(row["MAND_STR"]),
                }
            )
        declared_rows.append(
            {
                "result_set_id": sets[(council, district)],
                "option_id": option,
                "measure": VOTES,
                "value": int(row["HLASY_STR"] or 0),
            }
        )
    # candidates
    people: list[dict[str, object]] = []
    person_names: list[dict[str, object]] = []
    candidacies: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    line_ids: dict[tuple[str, str, str, str], str] = {}
    struck: Counter[tuple[str, str]] = Counter()
    for row in rk.iter_rows(named=True):
        council, district, number = row["KODZASTUP"], row["COBVODU"], row["POR_STR_HL"]
        option = options.get((council, district, number))
        if option is None:
            continue
        if row["PLATNOST"] != "A":
            if row["MANDAT"] == "A":
                raise ValueError(f"kv {iso} {council}: struck candidate {person_name(row)} elected")
            struck[(council, district)] += 1
            continue
        tag = f"kv:{iso}:{council}:{district}:{number}:{row['PORCISLO']}"
        person = p.id("person", tag)
        people.append({"id": person})
        text = clean(f"{row['JMENO']} {row['PRIJMENI']}")
        person_names.append(
            {
                "id": p.id("name", person, "cs", "official", text),
                "entity_id": person,
                "text": text,
                "lang": "cs",
                "kind": "official",
                "is_preferred": True,
            }
        )
        member = row["PSTRANA"]
        candidacy = p.id("candidacy", tag)
        ballot_name = person_name(row)
        candidacies.append(
            {
                "id": candidacy,
                "person_id": person,
                "option_id": option,
                "role": "list_member",
                "list_position": int(row["PORCISLO"]),
                "ballot_name": ballot_name,
                "member_of_party_id": party_of(member),
                "is_independent": member in NO_PARTY,
            }
        )
        line = line_ids[(council, district, number, row["PORCISLO"])] = p.id("option", tag)
        lines.append(
            {
                "id": line,
                "contest_id": contests[(council, district)],
                "kind": "candidate",
                "parent_option_id": option,
                "ballot_position": int(row["PORCISLO"]),
                "ballot_label": ballot_name,
            }
        )
        if int(row["POCHLASU"] or 0):
            declared_rows.append(
                {
                    "result_set_id": sets[(council, district)],
                    "option_id": line,
                    "measure": PREFERENCE,
                    "value": int(row["POCHLASU"]),
                }
            )
        if row["MANDAT"] == "A":
            won[(council, district, number)] += 1
            outcomes.append(
                {
                    "result_set_id": sets[(council, district)],
                    "option_id": option,
                    "candidacy_id": candidacy,
                    "kind": "elected",
                    "seats": None,
                }
            )
    declared_won = {
        (row["KODZASTUP"], row["COBVODU"], row["POR_STR_HL"]): int(row["MAND_STR"] or 0)
        for row in ros.iter_rows(named=True)
    }
    differing = [k for k, n in declared_won.items() if n != won.get(k, 0)]
    if differing:
        raise ValueError(
            f"kv {iso}: {len(differing)} lists whose declared seats differ from the elected, e.g. {differing[:3]}"
        )
    p.frame("person", pl.DataFrame(people, schema={"id": pl.String}))
    p.frame("name", pl.DataFrame(person_names, infer_schema_length=None))
    p.frame("candidacy", pl.DataFrame(candidacies, infer_schema_length=None))
    p.frame("contest_option", pl.DataFrame(lines, infer_schema_length=None))
    p.frame(
        "outcome",
        pl.DataFrame(
            outcomes, schema_overrides={"candidacy_id": pl.String, "seats": pl.Int32}, infer_schema_length=None
        ),
    )
    p.frame("declared", pl.DataFrame(declared_rows, infer_schema_length=None))

    # accounting and votes per polling district
    sid = pl.DataFrame(
        {
            "k": [f"{a}|{b}" for a, b in sets],
            "rs": list(sets.values()),
        }
    )
    t3 = t3.with_columns(k=pl.col("KODZASTUP") + "|" + pl.col("COBVODU")).join(sid, on="k", how="inner")
    unit_of = pl.DataFrame(
        {"OBEC": [a for a, _ in units], "OKRSEK": [b for _, b in units], "unit": list(units.values())}
    )
    t3 = t3.join(unit_of, on=["OBEC", "OKRSEK"], how="left")
    if t3["unit"].null_count():
        raise ValueError(f"kv {iso}: polling districts without a unit")
    for measure, column in ACCOUNTING:
        if column not in t3.columns:
            continue
        part = t3.filter(pl.col(column).is_not_null() & (pl.col(column) != ""))
        p.frame(
            "result_value",
            part.select(
                result_set_id=pl.col("rs"),
                unit_id=pl.col("unit"),
                option_id=pl.lit(None, pl.String),
                channel=pl.lit(0),
                measure=pl.lit(measure),
                count_round=pl.lit(0),
                flags=pl.lit(0),
                value=pl.col(column).cast(pl.Int64),
            ),
        )
    short = sum(1 for k, size in sizes.items() if sum(n for (c, d, _), n in won.items() if (c, d) == k) < size)
    if short:
        p.accept(
            "outcomes.seats_magnitude",
            f"{short} councils have fewer members elected in the candidate register than seats; the register's "
            "figures and the council size are stored as printed",
        )
    if t3.filter(pl.col("ODEVZ_OBAL").cast(pl.Int64) > pl.col("VOL_SEZNAM").cast(pl.Int64)).height:
        p.accept(
            "accounting.turnout",
            "volby.cz prints more envelopes in the box than voters on the roll in some polling districts "
            "(voters with a voting certificate may vote in any polling district of the council); stored as printed",
        )
    if t3.filter(pl.col("PL_HL_CELK").cast(pl.Int64) > pl.col("ODEVZ_OBAL").cast(pl.Int64)).height:
        p.accept(
            "accounting.ballots_sum",
            "each voter in a municipal election has as many votes as the council has seats, so the valid votes "
            "volby.cz prints per polling district are votes, not ballots, and exceed the envelopes in the box",
        )
    ids = t3.select("ID_OKRSKY", "TYPZASTUP", "rs", "KODZASTUP", "COBVODU", "unit")
    hl = hl.join(ids, on=["ID_OKRSKY", "TYPZASTUP"], how="inner")
    marks = [c for c in hl.columns if c.startswith("HLASY_")]
    option_key = pl.DataFrame(
        {
            "k": [f"{a}|{b}|{c}" for a, b, c in options],
            "option": list(options.values()),
        }
    )
    hl = hl.with_columns(k=pl.col("KODZASTUP") + "|" + pl.col("COBVODU") + "|" + pl.col("POR_STR_HL")).join(
        option_key, on="k", how="inner"
    )
    p.frame(
        "result_value",
        hl.select(
            result_set_id=pl.col("rs"),
            unit_id=pl.col("unit"),
            option_id=pl.col("option"),
            channel=pl.lit(0),
            measure=pl.lit(VOTES),
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value=pl.col("POC_HLASU").cast(pl.Int64),
        ),
    )
    line_key = pl.DataFrame(
        {
            "k": [f"{a}|{b}|{c}|{int(d)}" for a, b, c, d in line_ids],
            "line": list(line_ids.values()),
        }
    )
    long = (
        hl.select("rs", "unit", "KODZASTUP", "COBVODU", "POR_STR_HL", *marks)
        .unpivot(
            marks, index=["rs", "unit", "KODZASTUP", "COBVODU", "POR_STR_HL"], variable_name="mark", value_name="v"
        )
        .filter(pl.col("v").is_not_null() & (pl.col("v") != "0") & (pl.col("v") != ""))
        .with_columns(
            k=pl.col("KODZASTUP")
            + "|"
            + pl.col("COBVODU")
            + "|"
            + pl.col("POR_STR_HL")
            + "|"
            + pl.col("mark").str.slice(6).cast(pl.Int32).cast(pl.String)
        )
        .join(line_key, on="k", how="inner")
    )
    p.frame(
        "result_value",
        long.select(
            result_set_id=pl.col("rs"),
            unit_id=pl.col("unit"),
            option_id=pl.col("line"),
            channel=pl.lit(0),
            measure=pl.lit(PREFERENCE),
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value=pl.col("v").cast(pl.Int64),
        ),
    )
    for (council, district), n in struck.items():
        p.add(
            "note",
            id=p.id("note", sets[(council, district)], "struck"),
            entity_id=sets[(council, district)],
            kind="caveat",
            body_md=f"The candidate register marks {n} candidacies on these lists as void, so they are not stored. "
            "The polling-district file may still count preference marks for some of them; those marks count for "
            "no one and are left out.",
            lang="en",
        )
    _compare(p, day, t3, ros, zast, sets)
    return p


def _compare(
    p: Package, day: str, t3: pl.DataFrame, ros: pl.DataFrame, zast: pl.DataFrame, sets: dict[tuple[str, str], str]
) -> None:
    """Check the date's national totals against the results service."""
    root = _declared(day)
    kinds = {"1": "OBEC", "2": "MCMO"}
    for element in root.iter("TYP_ZASTUP"):
        which = [t for t, code in kinds.items() if code == element.attrib["OZNAC_TYPU"]]
        turnout = next(element.iter("UCAST"))
        part = t3.filter(pl.col("TYPZASTUP") == which[0])
        for column, attribute in (
            ("VOL_SEZNAM", "ZAPSANI_VOLICI"),
            ("VYD_OBALKY", "VYDANE_OBALKY"),
            ("ODEVZ_OBAL", "ODEVZDANE_OBALKY"),
            ("PL_HL_CELK", "PLATNE_HLASY"),
        ):
            ours = int(part.select(pl.col(column).cast(pl.Int64).sum()).item() or 0)
            printed = int(turnout.attrib[attribute])
            if ours != printed:
                raise ValueError(f"kv {day} {element.attrib['OZNAC_TYPU']} {attribute}: {ours} here, {printed} printed")
