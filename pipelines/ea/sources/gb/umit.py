import itertools
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.umit"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile"
RESULTS = f"{DATAVERSE}/14037637?format=original"
README = f"{DATAVERSE}/10549598"
PUBLISHER = "Resul Umit, Durham University"
TITLE = "UK House of Commons Election Results at Candidate Level, version 13 (2026-06-30)"
LANDING = "https://doi.org/10.7910/DVN/S83HOA"
NOTES = (
    "Candidate-level general election results for the House of Commons, 1832–2024, compiled by Resul Umit from "
    "electionhub.co.uk with corrections from the House of Commons, Wikipedia, and Eggers and Spirling (2014); 2024 "
    "comes from the House of Commons Library (Cracknell and Baker, CBP-10009). It marks no winners."
)
SKIPPED = date(1868, 11, 17)
PARTITION = date(1922, 12, 6)
TWO_MEMBER = {"eng-sheffield"}
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
COUNTRIES = {
    "England": "ENG",
    "Wales": "WLS",
    "Scotland": "SCT",
    "Ireland": "IRL",
    "Northern Ireland": "NIR",
}
NOT_PARTIES = ("Independent", "Speaker")
SHAPED = date(2010, 5, 6)
REDISTRIBUTIONS = (1832, 1868, 1885, 1918, 1950, 1955, 1974, 1983, 1997)
MISSPELLED = {"RoxBoroughshire": "Roxburghshire", "East Suex": "East Sussex"}
RENAMED = {
    "Central Newcastle upon Tyne": "Newcastle upon Tyne Central",
    "Central Stoke-on-Trent": "Stoke-on-Trent Central",
    "East Kingston upon Hull": "Kingston upon Hull East",
    "East Newcastle upon Tyne and Wallsend": "Newcastle upon Tyne East and Wallsend",
    "North East Wolverhampton": "Wolverhampton North East",
    "North Kingston upon Hull": "Kingston upon Hull North",
    "North Newcastle upon Tyne": "Newcastle upon Tyne North",
    "North Stoke-on-Trent": "Stoke-on-Trent North",
    "Regent's Park and Kensington North": "Regent's Park and North Kensington",
    "South East Wolverhampton": "Wolverhampton South East",
    "South Middlesbrough and East Cleveland": "Middlesbrough South and East Cleveland",
    "South Stoke-on-Trent": "Stoke-on-Trent South",
    "South West Milton Keynes": "Milton Keynes South West",
    "South West Wolverhampton": "Wolverhampton South West",
    "West Kingston upon Hull and Hessle": "Kingston upon Hull West and Hessle",
}
EXISTING = {
    "conservative": ("wikidata", "Q9626"),
    "labour": ("wikidata", "Q9630"),
    "liberal": ("wikidata", "Q622441"),
    "liberal-national": ("wikidata", "Q1354368"),
    "national-liberal-1922": ("wikidata", "Q6974098"),
    "national-labour": ("wikidata", "Q2184291"),
    "independent-conservative": ("gb.parliament.party", "11"),
    "independent-liberal": ("gb.parliament.party", "13"),
    "liberal-conservative": ("gb.parliament.party", "19"),
    "liberal-labour": ("gb.parliament.party", "20"),
    "independent-nationalist": ("gb.parliament.party", "21"),
}
ALIASES = {
    "Conservative": "conservative",
    "Tory": "conservative",
    "Unionist with Coalition Coupon": "conservative",
    "Conservative and National Liberal": "conservative",
    "National Liberal and Conservative": "conservative",
    "NI Conservatives": "conservative",
    "Scottish Unionist": "scottish-unionist",
    "Scottish Unionist with Coalition Coupon": "scottish-unionist",
    "Labour": "labour",
    "Labour Co-op": "labour",
    "Labour and Co-operative": "labour",
    "Labour Representation Committee": "labour",
    "Liberal with Coalition Coupon": "liberal",
    "Liberal (Local)": "liberal",
    "Liberal National": "liberal-national",
    "National Labour": "national-labour",
    "Independent Conservative": "independent-conservative",
    "Independent Liberal": "independent-liberal",
    "Independent Nationalist": "independent-nationalist",
    "Liberal-Conservative": "liberal-conservative",
    "Liberal-Labour": "liberal-labour",
    "Liberal Democrat": "liberal-democrats",
    "Liberal Democrats": "liberal-democrats",
    "Ecology": "green-party",
    "Greens": "green-party",
    "Green Party": "green-party",
    "Green": "green-party",
    "Scottish Green Party": "scottish-greens",
    "Scottish Greens": "scottish-greens",
    "SNP": "scottish-national-party",
    "Scottish National Party": "scottish-national-party",
    "UKIP": "uk-independence-party",
    "UK Independence Party": "uk-independence-party",
    "Reform UK": "reform-uk",
    "The Brexit Party": "reform-uk",
    "BNP": "british-national-party",
    "British National Party": "british-national-party",
    "DUP": "democratic-unionist-party",
    "Democratic Unionist Party": "democratic-unionist-party",
    "SDLP": "social-democratic-and-labour-party",
    "Social Democratic & Labour Party": "social-democratic-and-labour-party",
    "Social Democratic and Labour Party": "social-democratic-and-labour-party",
    "Sinn Féin": "sinn-fein",
    "Sinn Fein": "sinn-fein",
    "UUP": "ulster-unionist-party",
    "Ulster Unionist Party": "ulster-unionist-party",
    "Alliance": "alliance-party-of-northern-ireland",
    "Monster Raving Loony": "official-monster-raving-loony-party",
    "Monster Raving Loony Party": "official-monster-raving-loony-party",
    "Official Monster Raving Loony Party": "official-monster-raving-loony-party",
    "Christian Peoples": "christian-peoples-alliance",
    "Christian Peoples Alliance": "christian-peoples-alliance",
    "Christian Peoples Alliance Party": "christian-peoples-alliance",
    "Socialist Labour Party": "socialist-labour-party",
    "Workers Revolutionary": "workers-revolutionary-party",
    "Workers Revolutionary Party": "workers-revolutionary-party",
    "Mebyon Kernow - The Party for Cornwall": "mebyon-kernow",
    "Respect": "respect-party",
    "Respect Party": "respect-party",
    "Alliance For Green Socialism": "alliance-for-green-socialism",
    "Alliance for Green Socialism": "alliance-for-green-socialism",
    "Communist League Election Campaign": "communist-league",
    "Scottish Socialist": "scottish-socialist-party",
    "Scottish Socialist Party": "scottish-socialist-party",
    "Workers' Party": "workers-party",
    "The Workers' Party": "workers-party",
    "Communist Party of Britain": "communist-party-of-britain",
    "Social Democratic Party": "social-democratic-party-1990",
}
ERAS = {
    ("Liberal", 1992): "liberal-party-1989",
    ("Social Democratic", 1983): "social-democratic-party-1981",
    ("Social Democratic", 1992): "social-democratic-party-1990",
    ("Communist", 1922): "communist-party-of-great-britain",
    ("Communist", 1992): "communist-party-of-britain",
    ("National Liberal", 1922): "national-liberal-1922",
    ("National Liberal", 1931): "liberal-national",
    ("Socialist Labour", 1918): "socialist-labour-party-1903",
    ("Socialist Labour", 1992): "socialist-labour-party",
}
NAMES = {
    "scottish-unionist": "Unionist Party (Scotland)",
    "liberal-democrats": "Liberal Democrats",
    "green-party": "Green Party",
    "scottish-greens": "Scottish Green Party",
    "scottish-national-party": "Scottish National Party",
    "uk-independence-party": "UK Independence Party",
    "reform-uk": "Reform UK",
    "british-national-party": "British National Party",
    "democratic-unionist-party": "Democratic Unionist Party",
    "social-democratic-and-labour-party": "Social Democratic and Labour Party",
    "sinn-fein": "Sinn Féin",
    "ulster-unionist-party": "Ulster Unionist Party",
    "alliance-party-of-northern-ireland": "Alliance Party of Northern Ireland",
    "official-monster-raving-loony-party": "Official Monster Raving Loony Party",
    "christian-peoples-alliance": "Christian Peoples Alliance",
    "socialist-labour-party": "Socialist Labour Party",
    "socialist-labour-party-1903": "Socialist Labour Party",
    "workers-revolutionary-party": "Workers Revolutionary Party",
    "mebyon-kernow": "Mebyon Kernow",
    "respect-party": "Respect Party",
    "alliance-for-green-socialism": "Alliance for Green Socialism",
    "communist-league": "Communist League",
    "scottish-socialist-party": "Scottish Socialist Party",
    "workers-party": "Workers' Party",
    "communist-party-of-britain": "Communist Party of Britain",
    "communist-party-of-great-britain": "Communist Party of Great Britain",
    "social-democratic-party-1981": "Social Democratic Party",
    "social-democratic-party-1990": "Social Democratic Party",
    "liberal-party-1989": "Liberal Party",
}
DERIVED_CAVEAT = (
    "Umit's dataset marks no winners. The candidates shown as elected are those with the most votes, one for each seat."
)
STV_CAVEAT = (
    "University seats elected their members by the single transferable vote from 1918 to 1945. Umit's dataset gives "
    "first preferences only and marks no winners, so no candidate is shown as elected."
)
MISSING_CAVEAT = (
    "Umit's dataset lists no more candidates than seats in this contested race, so some candidates are missing and no "
    "candidate is shown as elected."
)
TIE_CAVEAT = (
    "Two or more candidates tie for the last seat in Umit's dataset, so the tied candidates are not shown as elected."
)
UNOPPOSED_CAVEAT = (
    "Umit's dataset marks every candidate here as unopposed but lists more candidates than seats, so no candidate is "
    "shown as elected."
)
STV = "Single transferable vote (university seats, 1918–1945)"


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def fetch() -> None:
    raw.fetch(SOURCE, RESULTS, suffix=".csv")
    raw.fetch(SOURCE, README, suffix=".pdf")


def parse() -> None:
    frame = (
        pl.read_csv(raw.cached(SOURCE, RESULTS).path, infer_schema_length=0)
        .with_columns(
            pl.col("date_start", "date_end").str.to_date(),
            pl.col("year", "seats").cast(pl.Int32),
            pl.col("votes", "electorate", "votes_valid", "votes_invalid").cast(pl.Int64),
            country=pl.col("country").replace_strict(COUNTRIES),
            unopposed=pl.col("unopposed") == "1",
        )
        .filter(pl.col("date_start") != SKIPPED)
        .filter(pl.col("date_start") == pl.col("date_start").mode().first().over("election"))
    )
    frame = frame.with_columns(
        unit=pl.col("country").str.to_lowercase() + "-" + pl.col("constituency").map_elements(slug, pl.String)
    )
    usual = frame.group_by("unit").agg(usual=pl.col("seats").drop_nulls().mode().max())
    usual = usual.with_columns(usual=pl.when(pl.col("unit").is_in(TWO_MEMBER)).then(2).otherwise("usual"))
    frame = (
        frame.join(usual, on="unit")
        .with_columns(seats=pl.col("seats").max().over("date_start", "unit").fill_null(pl.col("usual")))
        .drop("usual")
    )
    if frame["seats"].is_null().any():
        raise ValueError("a constituency has no seat count in any election")
    contested = ~pl.col("unopposed").all().over("date_start", "unit")
    candidates = pl.len().over("date_start", "unit")
    stv = (
        pl.col("constituency_type").str.starts_with("Universit").fill_null(False)
        & pl.col("year").is_between(1918, 1945)
        & (pl.col("seats") > 1)
    )
    rank = pl.col("votes").rank("max", descending=True).over("date_start", "unit")
    frame = frame.with_columns(
        contested=contested,
        status=pl.when(~contested & (candidates > pl.col("seats")))
        .then(pl.lit("unopposed"))
        .when(~contested)
        .then(pl.lit("tacit"))
        .when(stv)
        .then(pl.lit("stv"))
        .when(candidates <= pl.col("seats"))
        .then(pl.lit("missing"))
        .when((rank <= pl.col("seats")).sum().over("date_start", "unit") < pl.col("seats"))
        .then(pl.lit("tie"))
        .otherwise(pl.lit("derived")),
    ).with_columns(
        elected=pl.when(pl.col("status") == "tacit")
        .then(True)
        .when(pl.col("status").is_in(["derived", "tie"]))
        .then(rank <= pl.col("seats"))
        .otherwise(False)
    )
    member = (
        frame.filter(pl.col("mp_id_mnis").is_not_null())
        .group_by("unit", "candidate")
        .agg(inherited=pl.col("mp_id_mnis").unique())
        .filter(pl.col("inherited").list.len() == 1)
        .with_columns(pl.col("inherited").list.first())
    )
    frame = frame.join(member, on=["unit", "candidate"], how="left").with_columns(
        member=pl.coalesce("mp_id_mnis", "inherited")
    )
    frame = frame.sort("date_start", "unit", "votes", descending=[False, False, True]).with_columns(
        suffix=pl.int_range(pl.len()).over("date_start", "unit", "candidate")
    )
    frame = frame.with_columns(
        member=pl.when(pl.col("suffix") == 0).then("member"),
        suffix=pl.when(pl.col("suffix") > 0).then(pl.format("~{}", pl.col("suffix") + 1)).otherwise(pl.lit("")),
    )
    clashes = frame.group_by("date_start", "constituency").agg(pl.col("unit").n_unique()).filter(pl.col("unit") > 1)
    if clashes.height:
        raise ValueError(f"constituency names repeat within an election: {clashes}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        "election",
        "year",
        "date_start",
        "date_end",
        "country",
        "unit",
        "constituency",
        "constituency_type",
        "seats",
        "electorate",
        "votes_valid",
        "votes_invalid",
        "party",
        "candidate",
        "name",
        "surname",
        "gender",
        "member",
        "suffix",
        "votes",
        "contested",
        "status",
        "elected",
        "notes",
    ).sort("date_start", "unit", pl.col("votes"), descending=[False, False, True]).write_parquet(
        PARSED / "results.parquet"
    )
    print(frame.group_by("status").agg(pl.col("unit").n_unique()).sort("status"))
    print(f"{frame.height} candidacies in {frame['date_start'].n_unique()} elections")


def emit() -> Path:
    rows = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons general elections 1832–2024 by constituency, from Umit's candidate-level results",
            polity="GB",
        )

        def name(entity: UUID, text: str, start: date | None = None) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang="en",
                kind="official",
                is_preferred=True,
                valid_from=start,
            )

        gb = cs.polity("GB")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            attribution="Umit, Resul. UK House of Commons Election Results at Candidate Level. Harvard Dataverse.",
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, RESULTS), source)
        cs.artifact(raw.cached(SOURCE, README), source)

        old = cs.area("gb.nation", "GBI")
        if old in cs.minted:
            raise ValueError("publish gb.parliament before gb.umit")
        nation = cs.area("gb.nation", "GBR")
        if nation in cs.minted:
            cs.add("area", id=nation, scheme_code="gb.nation")
            cs.add("area_code", area_id=nation, code="GBR", valid_from=PARTITION)
            name(nation, "United Kingdom of Great Britain and Northern Ireland")
            cs.add("polity_territory", polity_id=gb, area_id=nation, valid_from=PARTITION)
        countries = {code: cs.area("gb.country", code) for code in COUNTRIES.values()}
        if countries["NIR"] in cs.minted:
            cs.add("area", id=countries["NIR"], scheme_code="gb.country")
            cs.add("area_code", area_id=countries["NIR"], code="NIR", valid_from=PARTITION)
            cs.add("area_parent", area_id=countries["NIR"], parent_id=nation, valid_from=PARTITION)
            name(countries["NIR"], "Northern Ireland")
            for code in ("ENG", "WLS", "SCT"):
                cs.add("area_code", area_id=countries[code], code=code, valid_from=PARTITION)
                cs.add("area_parent", area_id=countries[code], parent_id=nation, valid_from=PARTITION)

        districts = {
            (fold(text), country, start): area
            for text, country, start, area in conn.execute(
                """select n.text, p.code, lower(c.valid), a.id from area a
                   join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent ap on ap.area_id = a.id and ap.valid && c.valid
                   join area_code p on p.area_id = ap.parent_id
                   where a.scheme_code = 'gb.pcon' and lower(c.valid) is not null"""
            ).fetchall()
        }
        shaped = sorted({start for _, _, start in districts})

        commons = cs.key("gb.body", "house-of-commons")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block", "limited", "stv")}
        if commons in cs.minted or systems["fptp"] in cs.minted:
            raise ValueError("publish gb.parliament before gb.umit")
        if systems["stv"] in cs.minted:
            cs.add("electoral_system", id=systems["stv"], polity_id=gb, rules={"formula": "stv"}, rules_version=1)
            name(systems["stv"], STV)

        parties: dict[str, UUID] = {}

        def party_of(label: str, year: int, country: str) -> UUID | None:
            if label in NOT_PARTIES:
                return None
            key = next(
                (key for (era, since), key in sorted(ERAS.items(), reverse=True) if era == label and year >= since),
                None,
            )
            if key is None and label == "Unionist":
                key = "scottish-unionist" if country == "SCT" else "conservative"
            key = key or ALIASES.get(label) or slug(label)
            if key not in parties:
                if key in EXISTING:
                    parties[key] = cs.key(*EXISTING[key])
                    if parties[key] in cs.minted:
                        raise ValueError(f"{key} is not in the database")
                else:
                    parties[key] = cs.key("gb.party", key)
                    if parties[key] in cs.minted:
                        cs.add("party", id=parties[key], kind="party", polity_id=gb)
                        name(parties[key], NAMES.get(key, label))
            return parties[key]

        units: dict[str, UUID] = {}
        for unit_code, country, label in (
            rows.sort("date_start").group_by("unit", maintain_order=True).agg(pl.col("country", "constituency").last())
        ).iter_rows():
            units[unit_code] = cs.unit("gb.pcon", unit_code)
            if units[unit_code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[unit_code],
                    polity_id=gb,
                    scheme_code="gb.pcon",
                    code=unit_code,
                    area_relation="within",
                    area_id=countries[country],
                    kind="standard",
                    valid_from=None,
                )
                name(units[unit_code], label)

        era = (
            pl.col("date_start")
            .dt.year()
            .replace_strict(
                {year: max(since for since in REDISTRIBUTIONS if since <= year) for year in range(1832, 2025)}
            )
        )
        starts = sorted(rows["date_start"].unique())
        following = dict(itertools.pairwise(starts))
        reformed = {
            (fold(text), country): area
            for text, country, area in conn.execute(
                """select distinct on (n.text, p.code) n.text, p.code, a.id from area a
                   join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id and n.is_preferred
                   join area_parent ap on ap.area_id = a.id
                   join area_code p on p.area_id = ap.parent_id
                   where a.scheme_code = 'gb.pcon' and lower(c.valid) is null
                   order by n.text, p.code, a.id"""
            ).fetchall()
        }
        plans = dict(conn.execute("select vintage, id from boundary_set where scheme_code = 'gb.pcon'").fetchall())
        historic: dict[tuple[int, str], UUID] = {}
        for since, unit_code, country, label, first, last in (
            rows.with_columns(era=era)
            .filter(
                ~pl.col("era").is_in([1868, 1997]),
                ~pl.col("constituency").str.contains("Universit"),
            )
            .sort("date_start")
            .group_by("era", "unit", maintain_order=True)
            .agg(
                pl.col("country").last(),
                pl.col("constituency").last(),
                pl.col("date_start").first().alias("first"),
                pl.col("date_start").last().alias("last"),
            )
            .iter_rows()
        ):
            label = MISSPELLED.get(label, label)
            if country == "SCT":
                label = label.replace(" Boroughs", " Burghs")
            code = f"{since}:{unit_code}"
            area = historic[(since, unit_code)] = cs.area("gb.pcon", code, on=first)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.pcon")
                cs.add("area_code", area_id=area, code=code, valid_from=first, valid_to=following[last])
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=countries[country],
                    valid_from=first,
                    valid_to=following[last],
                )
                name(area, label)
        for index, since in enumerate(REDISTRIBUTIONS[:-1]):
            if since == 1868:
                continue
            opening = min(day for day in starts if day.year >= since)
            closing = min(day for day in starts if day.year >= REDISTRIBUTIONS[index + 1])
            cs.add(
                "boundary_set",
                **({"id": plans[str(since)]} if str(since) in plans else {}),
                scheme_code="gb.pcon",
                vintage=str(since),
                valid_from=opening,
                valid_to=closing,
                as_of=opening,
            )

        persons: dict[str, UUID] = {}
        for (start, end, label, year), held in rows.group_by(
            "date_start", "date_end", "election", "year", maintain_order=True
        ):
            title = f"{label.removesuffix(' General Election')} United Kingdom general election"
            election = cs.key("gb.election", str(start))
            cs.add(
                "election",
                id=election,
                polity_id=gb,
                kind="general",
                date_from=start,
                date_to=end,
                status="held",
            )
            name(election, title)
            path = f"gb/{start}-general"
            cs.add("slug", path=path, entity_id=election)
            for (unit_code,), lines in held.group_by("unit", maintain_order=True):
                first = lines.row(0, named=True)
                seats = first["seats"]
                status = first["status"]
                contest_key = f"{start}:{unit_code}"
                contest = cs.key("gb.contest", contest_key)
                system = (
                    "stv"
                    if status == "stv"
                    else "fptp"
                    if seats == 1
                    else "limited"
                    if seats >= 3 and 1868 <= year <= 1880
                    else "block"
                )
                tacit = status in ("tacit", "unopposed")
                since = max((day for day in shaped if day <= start), default=None)
                constituency = first["constituency"]
                district = (
                    reformed.get(
                        (
                            fold(
                                MISSPELLED.get(constituency, constituency).replace(
                                    " Boroughs", " Burghs" if first["country"] == "SCT" else " Boroughs"
                                )
                            ),
                            first["country"],
                        )
                    )
                    if 1868 <= year < 1885
                    else historic.get((max(since for since in REDISTRIBUTIONS if since <= year), unit_code))
                    if since is None
                    else districts.get((fold(RENAMED.get(constituency, constituency)), first["country"], since))
                )
                if since is not None and district is None and not (first["country"] == "NIR" and start < SHAPED):
                    raise ValueError(f"{start}: no boundary for {constituency}")
                if district is None and "Universit" not in constituency and start < SHAPED:
                    print(f"{start}: no area for {constituency}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=commons,
                    district_area_id=district,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems[system],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{label.removesuffix(' General Election')} general election in {first['constituency']}")
                cs.add("slug", path=f"{path}/{slug(first['constituency'])}", entity_id=contest)
                for note in sorted(set(lines["notes"].drop_nulls())):
                    cs.add("note", entity_id=contest, kind="context", body_md=note)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                fresh = result_set in cs.minted
                caveat = {
                    "derived": DERIVED_CAVEAT,
                    "tie": TIE_CAVEAT,
                    "stv": STV_CAVEAT,
                    "missing": MISSING_CAVEAT,
                    "unopposed": UNOPPOSED_CAVEAT,
                }.get(status)
                if fresh and caveat is not None:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
                if fresh:
                    for measure, value in (
                        (REGISTERED, first["electorate"]),
                        (VALID, first["votes_valid"] if seats == 1 and not tacit else None),
                        (INVALID, first["votes_invalid"] if not tacit else None),
                    ):
                        if value:
                            cs.add(
                                "result_value",
                                result_set_id=result_set,
                                unit_id=units[unit_code],
                                option_id=None,
                                channel=0,
                                measure=measure,
                                count_round=0,
                                flags=0,
                                value=value,
                            )
                for line in lines.iter_rows(named=True):
                    full = " ".join(part for part in (line["name"], line["surname"]) if part) or line["candidate"]
                    identity = line["member"] or f"{unit_code}:{slug(line['candidate'])}{line['suffix']}"
                    if identity not in persons:
                        persons[identity] = (
                            cs.key("mnis", line["member"]) if line["member"] else cs.key(f"{SOURCE}.person", identity)
                        )
                        if persons[identity] in cs.minted:
                            cs.add(
                                "person",
                                id=persons[identity],
                                gender=line["gender"].lower() if line["gender"] else None,
                            )
                            name(persons[identity], full)
                    party = party_of(line["party"], year, line["country"])
                    option_key = f"{contest_key}:{slug(line['candidate'])}{line['suffix']}"
                    option = cs.key("gb.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=full,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.candidacy", option_key),
                        person_id=persons[identity],
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=line["party"] == "Independent",
                    )
                    if fresh and line["contested"]:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=units[unit_code],
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
