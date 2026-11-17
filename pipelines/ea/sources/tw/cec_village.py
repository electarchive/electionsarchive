"""Taiwan's village and neighborhood chief elections (subject V0) from the Central Election Commission's election
database: 2010 (counties, in June, and the five special municipalities, in November), 2014, 2018, and 2022. Each
village or neighborhood is its own contest. The Commission publishes polling stations from the November 2010
election on, and for the county villages of June 2010 only the village's candidate votes. The packages share
`tw.cec/geo` and `tw.cec/registry`, which call `extend_geo` and `registry` here."""

import functools
import json
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.tw.cec import CEC, INDEPENDENT, SOURCE, TAIWAN, area, key
from ea.sources.tw.cec_township import MARKS, STATION, TOWN, VILLAGE, Theme, at, fold, local_url

LIST = "https://db.cec.gov.tw/static/elections/list/ELC_V0.json"
OFFICE = key("office", "village-chief")
SYSTEM = key("electoral_system", "village-chief")
SYSTEM_START = date(2010, 1, 1)
METHOD = (
    "Each voter casts one vote for one candidate for chief of the village or neighborhood. The candidate with the "
    "most votes in the village wins the office."
)
NOTE = (
    "Valid votes for each candidate and, where the Commission publishes them, the valid and invalid ballots, ballots "
    "cast, and registered voters at each {unit}, as published by the Central Election Commission. Voters registered "
    "is the number of voters on the roll. Official totals are the Commission's own."
)
MEASURES = ((2, "votable_population"), (3, "vote_ticket"), (4, "valid_ticket"), (5, "invalid_ticket"))


@functools.cache
def themes() -> list[tuple[Theme, frozenset[str]]]:
    data = json.loads(raw.fetch(SOURCE, LIST, suffix=".json").path.read_text(encoding="utf-8"))
    found = []
    for entry in data:
        for item in entry["theme_items"]:
            theme = Theme(
                "V0",
                item["legislator_type_id"],
                item["theme_id"],
                item["vote_date"],
                int(item["session"]),
                item["theme_name"],
            )
            found.append((theme, frozenset(item["data_tckt_seq"])))
    return sorted(found, key=lambda pair: pair[0].day)


def levels(theme: Theme) -> frozenset[str]:
    return dict(themes())[theme]


def counties(theme: Theme) -> list[str]:
    url = local_url("profiles", theme, "C")
    data = json.loads(raw.fetch(SOURCE, url, suffix=".json").path.read_text(encoding="utf-8"))
    return sorted({f"{row['prv_code']}_{row['city_code']}_00_000_0000" for row in next(iter(data.values()))})


def urls(theme: Theme) -> list[str]:
    out = [local_url("profiles", theme, "N"), local_url("profiles", theme, "C")]
    for code in counties(theme):
        out.extend(local_url("tickets", theme, level, code) for level in sorted(levels(theme)))
        out.extend(
            local_url("profiles", theme, level, code)
            for level in ("D", "L", "T")
            if "T" in levels(theme) or level == "D"
        )
    return out


def fetch() -> None:
    raw.fetch(SOURCE, LIST, suffix=".json")
    raw.fetch_many(SOURCE, [url for theme, _ in themes() for url in urls(theme)], suffix=".json", workers=8)


@functools.cache
def index() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


@functools.cache
def rows(theme: Theme, kind: str, level: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for county in counties(theme):
        url = local_url(kind, theme, level, county)
        if url in index():
            data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
            for part in data.values():
                out.extend(part)
    return out


def artifacts(theme: Theme) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in [*urls(theme), LIST] if url in index()]


@functools.cache
def places(theme: Theme) -> tuple[dict[tuple[str, ...], str], dict[tuple[str, ...], str], dict[tuple[str, ...], str]]:
    listing = json.loads(raw.cached(SOURCE, local_url("profiles", theme, "C")).path.read_text(encoding="utf-8"))
    county: dict[tuple[str, ...], str] = {
        (str(r["prv_code"]), str(r["city_code"])): fold(r["area_name"]) for r in next(iter(listing.values()))
    }
    township = {at(r, TOWN): fold(r["area_name"]) for r in rows(theme, "profiles", "D")}
    village = {at(r, VILLAGE): fold(r["area_name"]) for r in rows(theme, "tickets", "L")}
    return county, township, village


def geo_sets() -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    counties_: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    for theme, _ in themes():
        county, township, village = places(theme)
        for code, name in village.items():
            c, t = county[(code[0], code[1])], township[code[:3]]
            counties_.add(c)
            townships.add((c, t))
            villages.add((c, t, name))
    return counties_, townships, villages


def extend_geo(counties_: set[str], townships: set[tuple[str, str]], villages: set[tuple[str, str, str]]) -> None:
    more = geo_sets()
    counties_ |= more[0]
    townships |= more[1]
    villages |= more[2]


def packages() -> list[str]:
    return [f"tw.cec/village-chief-{theme.day}" for theme, _ in themes()]


def election(theme: Theme) -> Package:
    day = theme.day
    year = day[:4]
    when = date.fromisoformat(day)
    stations = "T" in levels(theme)
    county_of, township_of, village_of = places(theme)
    scope = "municipal" if theme.name.startswith(f"{theme.session}年直轄市") else "county"
    p = Package(
        f"tw.cec/village-chief-{day}",
        SOURCE,
        f"The {year} Taiwanese village and neighborhood chief elections "
        f"{'by polling station ' if stations else ''}from the Central Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for artifact in artifacts(theme):
        p.uses(artifact)
    elect = key("election", f"V0-{day}")
    path = f"tw/{day}-village-chiefs"
    p.add("election", id=elect, polity_id=TAIWAN, kind="local", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=elect, is_current=True)
    label = {
        "municipal": f"{year} Taiwanese special municipality village and neighborhood chief elections",
        "county": f"{year} Taiwanese county and city village chief elections",
    }[scope]
    if year != "2010":
        label = f"{year} Taiwanese village and neighborhood chief elections"
    p.name(elect, label, "en", "official")
    p.name(elect, theme.name, "zh", "native", False)
    totals = {at(r, VILLAGE): r for r in rows(theme, "profiles", "L")}
    accounts = {at(r, STATION): r for r in rows(theme, "profiles", "T")}
    by_village: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for r in rows(theme, "tickets", "L"):
        by_village.setdefault(at(r, VILLAGE), []).append(r)
    stations_of: dict[tuple[str, ...], dict[tuple[str, ...], list[dict[str, object]]]] = {}
    for r in rows(theme, "tickets", "T"):
        stations_of.setdefault(at(r, VILLAGE), {}).setdefault(at(r, STATION), []).append(r)
    unit_rows: dict[str, dict[str, object]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    differences: list[str] = []
    caveats: list[str] = []
    for code, group in sorted(by_village.items()):
        cands = sorted(group, key=lambda r: int(str(r["cand_no"])))
        tag = "_".join(code)
        county = county_of[(code[0], code[1])]
        township = township_of[code[:3]]
        village = village_of[code]
        district = area("tw.village", county, township, village)
        contest = key("contest", f"V0-{day}", tag)
        result_set = key("result_set", f"V0-{day}", tag)
        account = totals.get(code)
        winners = sum(1 for r in cands if MARKS[str(r["is_victor"])] == "elected")
        english = f"{year} {township} {village} chief election"
        p.add(
            "contest",
            id=contest,
            election_id=elect,
            kind="office",
            office_id=OFFICE,
            district_area_id=district,
            magnitude=int(str(account["elected_num"])) if account else winners,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} {county} {township} {village} chief election", "en", "official")
        p.name(contest, f"{county}{township}{village}長選舉", "zh", "native", False)
        p.add("slug", path=f"{path}/{'-'.join(code)}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=elect,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=NOTE.format(unit="polling station" if stations else "village"),
            lang="en",
        )
        option_of: dict[int, str] = {}
        for r in cands:
            no = int(str(r["cand_no"]))
            party_name = str(r["party_name"])
            independent = party_name in INDEPENDENT
            party_id = None if independent else key("party", party_name)
            option = key("option", f"V0-{day}", tag, no)
            option_of[no] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party_id,
                ballot_position=no,
                ballot_label=str(r["cand_name"]),
            )
            person = key("person", f"V0-{day}", tag, no)
            birth = str(r.get("cand_birthyear") or "").strip()
            p.add(
                "person",
                id=person,
                birth_date=date(int(birth), 1, 1) if birth.isdigit() else None,
                birth_precision="year" if birth.isdigit() else None,
                gender={"1": "male", "2": "female"}.get(str(r.get("cand_sex") or "")),
            )
            p.name(person, str(r["cand_name"]), "zh", "official")
            p.add(
                "candidacy",
                id=key("candidacy", f"V0-{day}", tag, no),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=str(r["cand_name"]),
                member_of_party_id=party_id,
                is_independent=independent,
                is_incumbent=True if r.get("is_current") == "Y" else None,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind=MARKS[str(r["is_victor"])])
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=int(str(r["ticket_num"])))
        if account:
            for measure, field in MEASURES:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=None,
                    measure=measure,
                    value=int(str(account[field])),
                )
        sums: dict[int, int] = {}
        ballots = {m: 0 for m, _ in MEASURES}
        have = False
        if stations:
            for scode, srows in stations_of.get(code, {}).items():
                ucode = f"V0:{day}:{'_'.join(scode[:4])}:{scode[4]}"
                unit = key("unit", ucode)
                unit_rows.setdefault(
                    unit,
                    {
                        "id": unit,
                        "polity_id": TAIWAN,
                        "scheme_code": "tw.station",
                        "code": ucode,
                        "area_relation": "within",
                        "area_id": district,
                        "kind": "standard",
                    },
                )
                for r in srows:
                    no = int(str(r["cand_no"]))
                    sums[no] = sums.get(no, 0) + int(str(r["ticket_num"]))
                    values.append((result_set, unit, option_of[no], 1, int(str(r["ticket_num"]))))
                acc = accounts.get(scode)
                if acc:
                    have = True
                    for measure, field in MEASURES:
                        ballots[measure] += int(str(acc[field]))
                        values.append((result_set, unit, None, measure, int(str(acc[field]))))
        else:
            ucode = f"V0:{day}:{tag}"
            unit = key("unit", ucode)
            unit_rows.setdefault(
                unit,
                {
                    "id": unit,
                    "polity_id": TAIWAN,
                    "scheme_code": "tw.village",
                    "code": ucode,
                    "area_relation": "equals",
                    "area_id": district,
                    "kind": "aggregate",
                },
            )
            for r in cands:
                no = int(str(r["cand_no"]))
                sums[no] = int(str(r["ticket_num"]))
                values.append((result_set, unit, option_of[no], 1, sums[no]))
            if account:
                have = True
                for measure, field in MEASURES:
                    ballots[measure] = int(str(account[field]))
                    values.append((result_set, unit, None, measure, ballots[measure]))
        for r in cands:
            no = int(str(r["cand_no"]))
            if stations and sums.get(no, 0) != int(str(r["ticket_num"])):
                differences.append(
                    f"{english}: the station figures for {r['cand_name']} add up to {sums.get(no, 0)}, and the "
                    f"Commission's total is {r['ticket_num']}"
                )
        if stations and account and have:
            for measure, field in MEASURES:
                if ballots[measure] != int(str(account[field])):
                    differences.append(
                        f"{english}: the station figures add up to {ballots[measure]} for {field}, and the "
                        f"Commission's total is {account[field]}"
                    )
    sums_by_township: dict[tuple[str, ...], int] = {}
    for r in rows(theme, "tickets", "L"):
        sums_by_township[at(r, TOWN)] = sums_by_township.get(at(r, TOWN), 0) + int(str(r["ticket_num"]))
    for r in rows(theme, "profiles", "D"):
        found = sums_by_township.get(at(r, TOWN), 0)
        if found != int(str(r["valid_ticket"])):
            caveats.append(
                f"{county_of[(str(r['prv_code']), str(r['city_code']))]}{fold(r['area_name'])}: the Commission's "
                f"township total is {r['valid_ticket']} valid votes, and its village files add up to {found}"
            )
    for code, group in sorted(by_village.items()):
        if not any(MARKS[str(r["is_victor"])] == "elected" for r in group):
            caveats.append(
                f"{township_of[code[:3]]}{village_of[code]}: the Commission's file marks no candidate as elected"
            )
    if caveats:
        p.add(
            "note",
            id=key("note", "caveats", f"V0-{day}"),
            entity_id=elect,
            kind="caveat",
            body_md="Differences within the Commission's own files, stored as published: " + "; ".join(caveats) + ".",
            lang="en",
        )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's published village totals differ from its own station figures. "
            + "; ".join(differences[:20]),
        )
        p.add(
            "note",
            id=key("note", "differences", f"V0-{day}"),
            entity_id=elect,
            kind="caveat",
            body_md="The Commission's published village totals differ from its own station figures in this election: "
            + "; ".join(differences)
            + ". The station figures are stored as published.",
            lang="en",
        )
    p.frame("reporting_unit", pl.DataFrame(list(unit_rows.values())))
    facts = pl.DataFrame(
        values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
    ).filter(pl.col("value") >= 0)
    p.frame(
        "result_value",
        facts.select(
            result_set_id="result_set_id",
            unit_id="unit_id",
            option_id="option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    return p


def registry(p: Package) -> None:
    """Add the village chief office and its electoral system, and the cited files, to the registry package."""
    cited: dict[str, raw.Artifact] = {}
    for theme, _ in themes():
        for artifact in artifacts(theme):
            cited.setdefault(artifact.storage_key, artifact)
    for artifact in cited.values():
        p.artifact(artifact, CEC)
    p.add("office", id=OFFICE, polity_id=TAIWAN, kind="other")
    p.name(OFFICE, "Chief of a village or neighborhood", "en", "official")
    p.name(OFFICE, "村里長", "zh", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=TAIWAN,
        valid_from=SYSTEM_START,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")


def parties() -> set[str]:
    return {str(r["party_name"]) for theme, _ in themes() for r in rows(theme, "tickets", "L")}


def parse() -> None:
    for theme, _ in themes():
        election(theme).write()
