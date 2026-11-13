from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "ca.bc_history"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CATALOGUE = "https://catalogue.data.gov.bc.ca/dataset"
VOTES = f"{CATALOGUE}/44914a35-de9a-4830-ac48-870001ef8935"
REGISTERED = f"{CATALOGUE}/60bf8c02-1436-49fc-b350-f985618d6881"
BY_AREA = f"{VOTES}/resource/fb40239e-b718-4a79-b18f-7a62139d9792/download/provincial_voting_results_by_va.csv"
BY_PLACE = (
    f"{VOTES}/resource/4e7be8e3-0805-4f39-87f3-47c4c29e262d/download/provincial_voting_results_by_voting_place.csv"
)
COUNTS = f"{REGISTERED}/resource/c332d1dc-67aa-4a6a-883f-18a50751844d/download/provincial_registered_voter_counts.csv"
PUBLISHER = "Elections BC"
LICENSE = "Elections BC Open Data Licence"
LICENSE_URL = "https://www.elections.bc.ca/docs/EBC-Open-Data-Licence.pdf"
ATTRIBUTION = "Contains information licensed under the Elections BC Open Data Licence."
MAPS = {"2005": date(2005, 5, 17), "2009": date(2009, 5, 12), "2017": date(2017, 5, 9)}
CURRENT = date(2024, 10, 19)
SEATS = {"2005": 79, "2009": 85, "2017": 87}
SHARED = {
    "BC Green Party": "BC Green Party",
    "BC NDP": "BC NDP",
    "Communist Party of BC": "Communist Party of BC",
    "Libertarian": "Libertarian",
    "Freedom Party of BC": "Freedom Party of BC",
    "Conservative": "Conservative Party",
    "Conservative Party": "Conservative Party",
    "Christian Heritage Party of B.C.": "Christian Heritage Party of BC",
}
RENAMED = {"BC Liberal Party": "BC United"}
UNITED = date(2023, 4, 12)
COLORS = {
    "BC NDP": "#f58220",
    "BC United": "#d71920",
    "BC Green Party": "#3d9b35",
    "Conservative Party": "#1a4782",
}


def map_of(day: date) -> str | None:
    if day >= CURRENT:
        return None
    return max(vintage for vintage, start in MAPS.items() if start <= day)


def fetch() -> None:
    for url in (BY_AREA, BY_PLACE, COUNTS):
        raw.fetch(SOURCE, url, suffix=".csv")


def read(url: str, encoding: str) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, url).path, encoding=encoding, infer_schema_length=0)
    return frame.rename({column: column.removeprefix("\xef\xbb\xbf").lstrip("\ufeff") for column in frame.columns})


def parse() -> None:
    rows = pl.concat([read(BY_AREA, "windows-1252"), read(BY_PLACE, "utf8").drop("ID")], how="vertical").with_columns(
        event=pl.col("EVENT_NAME").str.to_lowercase().str.replace("provincial ", "", literal=True),
        votes=pl.col("VOTES_CONSIDERED").cast(pl.Int64),
        CANDIDATE=pl.col("CANDIDATE").str.strip_chars(),
        AFFILIATION=pl.col("AFFILIATION").str.strip_chars(),
    )
    counts = read(COUNTS, "utf8").with_columns(
        event=pl.col("EVENT_NAME").str.to_lowercase().str.replace("provincial ", "", literal=True),
        registered=pl.col("REGISTERED_VOTERS").cast(pl.Int64),
        date=pl.col("EVENT_DATE_TEXT").str.to_date("%m/%d/%Y"),
    )
    days = dict(counts.select("event", "date").unique().iter_rows())
    if missing := set(rows["event"]) - set(days):
        raise ValueError(f"no date for {sorted(missing)}")
    registered = counts.group_by("event", "ED_ABBREVIATION").agg(pl.col("registered").sum())
    if registered.join(rows.select("event", "ED_ABBREVIATION").unique(), on=["event", "ED_ABBREVIATION"]).height != (
        rows.select("event", "ED_ABBREVIATION").unique().height
    ):
        raise ValueError("a district has no registered voter count")
    districts = (
        rows.group_by("event", "ED_ABBREVIATION", "ED_NAME")
        .agg(
            rejected=pl.col("votes").filter(pl.col("VOTE_CATEGORY") == "Rejected").sum(),
            valid=pl.col("votes").filter(pl.col("VOTE_CATEGORY") == "Valid").sum(),
        )
        .join(registered, on=["event", "ED_ABBREVIATION"])
        .with_columns(
            date=pl.col("event").replace_strict(days, return_dtype=pl.Date),
            general=pl.col("event").str.contains("general election"),
        )
        .rename({"ED_ABBREVIATION": "district", "ED_NAME": "name"})
    )
    if districts.group_by("event", "district").len()["len"].max() != 1:
        raise ValueError("a district has two names in one event")
    candidates = (
        rows.filter(pl.col("VOTE_CATEGORY") == "Valid")
        .group_by("event", "ED_ABBREVIATION", "CANDIDATE", maintain_order=True)
        .agg(
            pl.col("AFFILIATION").unique().drop_nulls(),
            pl.col("votes").sum(),
            elected=pl.col("ELECTED").drop_nulls().unique(),
        )
        .rename({"ED_ABBREVIATION": "district", "CANDIDATE": "candidate"})
    )
    if (candidates["AFFILIATION"].list.len() > 1).any() or (candidates["elected"].list.len() != 1).any():
        raise ValueError("a candidate has two affiliations or no single elected flag")
    candidates = candidates.with_columns(
        affiliation=pl.col("AFFILIATION").list.first(), elected=pl.col("elected").list.first() == "Y"
    ).drop("AFFILIATION")
    for (key, district), group in candidates.group_by("event", "district"):
        top = group.filter(pl.col("votes") == group["votes"].max())
        if group["elected"].sum() != 1 or top.height != 1 or not top["elected"][0]:
            raise ValueError(f"{key} {district}: the elected candidate is not the single leader")
    sums = candidates.group_by("event", "district").agg(pl.col("votes").sum())
    if not districts.join(sums, on=["event", "district"]).filter(pl.col("votes") != pl.col("valid")).is_empty():
        raise ValueError("candidate votes do not sum to the valid votes")
    PARSED.mkdir(parents=True, exist_ok=True)
    districts.write_parquet(PARSED / "districts.parquet")
    candidates.write_parquet(PARSED / "candidates.parquet")
    print(
        f"{districts['event'].n_unique()} events, {districts.height} contests, {candidates.height} candidates, "
        f"{candidates['affiliation'].n_unique()} affiliations"
    )


def listed(items: list[str]) -> str:
    return (
        items[0]
        if len(items) == 1
        else " and ".join(items)
        if len(items) == 2
        else f"{', '.join(items[:-1])}, and {items[-1]}"
    )


def person_name(text: str) -> str:
    family, _, given = text.partition(",")
    return f"{given.strip()} {family.strip()}".strip()


def emit() -> Path:
    districts = pl.read_parquet(PARSED / "districts.parquet").sort("date", "district")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "British Columbia's general elections from 2005 to 2024 and its by-elections from 2008 to 2023, "
                "by electoral district, from Elections BC's open data"
            ),
            polity="CA-BC",
        )

        def name(
            entity: UUID,
            text: str,
            kind: str = "official",
            preferred: bool | None = None,
            valid: tuple[date, date] | None = None,
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang="en",
                kind=kind,
                is_preferred=(kind == "official") if preferred is None else preferred,
                **({"valid_from": valid[0], "valid_to": valid[1]} if valid else {}),
            )

        bc = cs.polity("CA-BC")
        province = cs.area("ca.province", "59")
        assembly = cs.key("ca.bc.body", "legislative_assembly")
        fptp = cs.key("ca.bc.electoral_system", "fptp")
        if {bc, province, assembly, fptp} & cs.minted:
            raise ValueError("run ca.elections and ca.bc first: British Columbia's assembly must exist")

        sources: dict[str, UUID] = {}
        for label, title, page, urls in (
            ("votes", "Provincial Voting Results (open data)", VOTES, (BY_AREA, BY_PLACE)),
            ("registered", "Provincial Registered Voter Counts (open data)", REGISTERED, (COUNTS,)),
        ):
            sources[label] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[label],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=page,
                license=LICENSE,
                license_url=LICENSE_URL,
                attribution=ATTRIBUTION,
            )
            for url in urls:
                cs.artifact(raw.cached(SOURCE, url), sources[label])

        vintages = sorted(MAPS)
        ends = dict(zip(vintages, [*[MAPS[v] for v in vintages[1:]], CURRENT], strict=True))
        for vintage in vintages:
            cs.add(
                "body_size", body_id=assembly, seats=SEATS[vintage], valid_from=MAPS[vintage], valid_to=ends[vintage]
            )
            cs.add(
                "boundary_set",
                scheme_code="ca.bc",
                vintage=vintage,
                valid_from=MAPS[vintage],
                valid_to=ends[vintage],
                as_of=MAPS[vintage],
            )
        areas: dict[str, UUID] = {}
        districts = districts.with_columns(
            area=pl.struct("date", "district").map_elements(
                lambda r: r["district"] if (v := map_of(r["date"])) is None else f"{v}:{r['district']}",
                return_dtype=pl.String,
            )
        )
        for code, labels, days in (
            districts.group_by("area", maintain_order=True).agg(pl.col("name"), pl.col("date")).iter_rows()
        ):
            vintage = map_of(days[0])
            if vintage is None:
                areas[code] = cs.area("ca.bc", code, CURRENT)
                if areas[code] in cs.minted:
                    raise ValueError(f"{code} is not on the 2023 map")
                continue
            if any(map_of(day) != vintage for day in days):
                raise ValueError(f"{code} spans two maps")
            area = areas[code] = cs.area("ca.bc", code, MAPS[vintage])
            cs.add("area", id=area, scheme_code="ca.bc")
            cs.add("area_code", area_id=area, code=code, valid_from=MAPS[vintage], valid_to=ends[vintage])
            cs.add("area_parent", area_id=area, parent_id=province)
            name(area, labels[-1])
            for label in dict.fromkeys(labels[:-1]):
                if label != labels[-1]:
                    name(area, label, "historical", False)

        party_ids: dict[str, UUID] = {}
        affiliations = (
            candidates.join(districts.select("event", "district", "date"), on=["event", "district"])
            .filter(pl.col("affiliation").is_not_null() & (pl.col("affiliation") != "Independent"))
            .group_by("affiliation")
            .agg(first=pl.col("date").min(), last=pl.col("date").max())
            .sort("affiliation")
        )
        for affiliation, first, last in affiliations.iter_rows():
            if affiliation in SHARED:
                key = SHARED[affiliation]
                party_ids[affiliation] = cs.key("ca.bc.party", key)
                if party_ids[affiliation] not in cs.minted:
                    continue
                cs.add("party", id=party_ids[affiliation], kind="party", polity_id=bc)
                name(party_ids[affiliation], key)
                continue
            key = RENAMED.get(affiliation, affiliation)
            party = party_ids[affiliation] = cs.key(f"{SOURCE}.party", key)
            if affiliation in RENAMED:
                name(party, affiliation, "historical", False, (first, UNITED))
                continue
            cs.add("party", id=party, kind="party", polity_id=bc)
            name(party, affiliation)
        for key, color in COLORS.items():
            party = cs.key("ca.bc.party", key) if key in SHARED.values() else cs.key(f"{SOURCE}.party", key)
            cs.add("party_color", party_id=party, color=color)

        columns = {
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "channel": pl.Int16,
            "measure": pl.Int16,
            "count_round": pl.Int16,
            "flags": pl.Int16,
            "value": pl.Int64,
        }
        for (day, general), rows in districts.group_by("date", "general", maintain_order=True):
            kind = "general" if general else "by-election"
            labels = rows["name"].to_list()
            election = cs.key(f"{SOURCE}.election", day.isoformat())
            cs.add(
                "election",
                id=election,
                polity_id=bc,
                kind="general" if general else "by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            spoken = f"{day:%B} {day.day}, {day.year}"
            if general:
                name(election, f"{day.year} British Columbia general election")
            else:
                name(
                    election,
                    f"{listed(labels)} provincial by-election{'s' if len(labels) > 1 else ''}, {spoken}",
                )
            cs.add("slug", path=f"ca/bc/{day}-{kind}", entity_id=election)
            values: list[tuple[UUID, UUID, UUID | None, int, int]] = []
            for row in rows.iter_rows(named=True):
                district = row["district"]
                contest = cs.key(f"{SOURCE}.contest", f"{day}:{district}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=assembly,
                    district_area_id=areas[row["area"]],
                    magnitude=1,
                    electoral_system_id=fptp,
                )
                name(contest, f"{day.year} {'election' if general else 'by-election'} in {row['name']}")
                cs.add("slug", path=f"ca/bc/{day}-{kind}/{slug(row['name'])}", entity_id=contest)
                unit = cs.unit("ca.bc", f"{day}:{district}", day)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=bc,
                    scheme_code="ca.bc",
                    code=f"{day}:{district}",
                    area_relation="equals",
                    area_id=areas[row["area"]],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
                result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{district}")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    source_id=sources["votes"],
                    status="active",
                    is_default=True,
                    completeness=1,
                )
                for measure, value in (
                    (2, row["registered"]),
                    (3, row["valid"] + row["rejected"]),
                    (4, row["valid"]),
                    (5, row["rejected"]),
                ):
                    values.append((result_set, unit, None, measure, value))
                for candidate in candidates.filter(
                    (pl.col("event") == row["event"]) & (pl.col("district") == district)
                ).iter_rows(named=True):
                    display = person_name(candidate["candidate"])
                    tag = f"{day}:{district}:{candidate['candidate']}"
                    person = cs.key(f"{SOURCE}.person", tag)
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, display)
                    party = party_ids.get(candidate["affiliation"] or "")
                    option = cs.key(f"{SOURCE}.option", tag)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=display,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", tag),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=display,
                        member_of_party_id=party,
                        is_independent=party is None,
                    )
                    if candidate["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                    values.append((result_set, unit, option, 1, candidate["votes"]))
            cs.frame(
                "result_value",
                pl.DataFrame(
                    {
                        "result_set_id": [str(v[0]) for v in values],
                        "unit_id": [str(v[1]) for v in values],
                        "option_id": [None if v[2] is None else str(v[2]) for v in values],
                        "channel": [0] * len(values),
                        "measure": [v[3] for v in values],
                        "count_round": [0] * len(values),
                        "flags": [0] * len(values),
                        "value": [v[4] for v in values],
                    },
                    schema=columns,
                ),
            )
        return cs.write()
