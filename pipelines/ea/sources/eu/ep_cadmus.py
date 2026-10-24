import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "eu.ep_cadmus"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://cadmus.eui.eu/bitstreams/9a5261b3-8dea-5650-988a-fac4460a8f3c/download"
WINDOWS = {
    1979: (date(1979, 6, 7), date(1979, 6, 10)),
    1984: (date(1984, 6, 14), date(1984, 6, 17)),
    1989: (date(1989, 6, 15), date(1989, 6, 18)),
    1994: (date(1994, 6, 9), date(1994, 6, 12)),
    1999: (date(1999, 6, 10), date(1999, 6, 13)),
    2004: (date(2004, 6, 10), date(2004, 6, 13)),
    2009: (date(2009, 6, 4), date(2009, 6, 7)),
    2014: (date(2014, 5, 22), date(2014, 5, 25)),
    2019: (date(2019, 5, 23), date(2019, 5, 26)),
}
ISO = {
    "Austria": "AT",
    "Belgium": "BE",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Cyprus": "CY",
    "Czech Republic": "CZ",
    "Denmark": "DK",
    "Estonia": "EE",
    "Finland": "FI",
    "France": "FR",
    "Germany": "DE",
    "Greece": "GR",
    "Hungary": "HU",
    "Ireland": "IE",
    "Italy": "IT",
    "Latvia": "LV",
    "Lithuania": "LT",
    "Luxembourg": "LU",
    "Malta": "MT",
    "Netherlands": "NL",
    "Poland": "PL",
    "Portugal": "PT",
    "Romania": "RO",
    "Slovakia": "SK",
    "Slovenia": "SI",
    "Spain": "ES",
    "Sweden": "SE",
    "United Kingdom": "GB",
}
CONSTITUENCIES = {
    ("FR", "Center"): "massif-central-centre",
    ("FR", "East"): "est",
    ("FR", "North-west"): "nord-ouest",
    ("FR", "Outre-Mer"): "outre-mer",
    ("FR", "South-East"): "sud-est",
    ("FR", "South-West"): "sud-ouest",
    ("FR", "West"): "ouest",
    ("FR", "Île-de-France"): "ile-de-france",
    ("IE", "Connaught Ulster"): "connacht-ulster",
    ("IE", "Dublin"): "dublin",
    ("IE", "East"): "east",
    ("IE", "Leinster"): "leinster",
    ("IE", "Midlands-North-West"): "midlands-north-west",
    ("IE", "Munster"): "munster",
    ("IE", "North-West"): "north-west",
    ("IE", "South"): "south",
    ("IT", "Central"): "centrale",
    ("IT", "Islands"): "insulare",
    ("IT", "North-East"): "nord-orientale",
    ("IT", "North-east"): "nord-orientale",
    ("IT", "North-west"): "nord-occidentale",
    ("IT", "Southern"): "meridionale",
    ("GB", "East Midlands Region"): "east-midlands",
    ("GB", "Eastern Region"): "eastern",
    ("GB", "London Region"): "london",
    ("GB", "North East Region"): "north-east",
    ("GB", "North West Region"): "north-west",
    ("GB", "Northern Ireland"): "northern-ireland",
    ("GB", "Scotland"): "scotland",
    ("GB", "South East Region"): "south-east",
    ("GB", "South West Region"): "south-west",
    ("GB", "Wales"): "wales",
    ("GB", "West Midlands Region"): "west-midlands",
    ("GB", "Yorkshire and the Humber"): "yorkshire-and-the-humber",
}
OTHERS = {"others", "other"}
MISSING = {".", ""}
UK_PARTIES = {
    "Conservative Party": "conservative",
    "NI Conservatives": "conservative",
    "Labour Party": "labour",
    "Labour": "labour",
    "Liberal Party": "liberal-party-1989",
    "Brexit Party": "reform-uk",
    "Green Party of England and Wales": "green-party",
    "Alliance Party": "alliance-party-of-northern-ireland",
    "CPA": "christian-peoples-alliance",
    "BNP": "british-national-party",
    "Ulster Popular Unionist Party": "ulster-popular-unionist",
    "Conservatives": "conservative",
    "Pirate": "pirate-party-uk",
    "Change UK": "the-independent-group-for-change",
    "Unionist Party": "unionist-party-ni",
    "Pensiones": "pensioners-party",
}
UK_SYSTEMS = {
    "ep-list": ("Regional closed lists, d'Hondt (European Parliament, Great Britain)", {"formula": "dhondt"}),
    "ep-stv": (
        "Single transferable vote (European Parliament, Northern Ireland)",
        {"formula": "stv", "quota": "droop"},
    ),
}
WINDOW_CAVEAT = (
    "The source gives only the year of the election. The dates are the European Parliament's voting window for that "
    "year, not the day this country voted."
)
EUC_NAMES = {
    (1979, "Cotswolds"): "The Cotswolds",
    (1979, "Tyne South and Wear"): "South Tyne and Wear",
    (1979, "South East Wales"): "South-East Wales",
    (1984, "Cotswolds"): "The Cotswolds",
    (1984, "Oxfordshire and Buckinghamshire"): "Oxford and Buckinghamshire",
    (1984, "Sussex East"): "East Sussex",
    (1984, "Sussex West"): "West Sussex",
}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join(re.findall(r"[a-z0-9]+", plain))


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".xlsx")


def parse() -> None:
    artifact = raw.cached(SOURCE, URL)
    frame = pl.read_excel(artifact.path)
    count = pl.col("votes_total").str.strip_chars()
    eligible = pl.col("eligible").str.strip_chars()
    frame = frame.with_columns(
        pl.col("country").str.strip_chars(),
        pl.col("constituency").str.strip_chars(),
        pl.col("party").str.strip_chars(),
        pl.col("party_original").str.strip_chars(),
        cast=pl.when(count.str.contains(r"^\d+$")).then(count.cast(pl.Int64, strict=False)),
        registered=pl.when(eligible.str.contains(r"^\d+$")).then(eligible.cast(pl.Int64, strict=False)),
    )
    sums = frame.group_by("country", "constituency", "year").agg(
        pl.col("seats").sum().alias("allocated"), pl.col("seats_total").first().alias("declared")
    )
    wrong = sums.filter(pl.col("allocated") != pl.col("declared"))
    if wrong.height:
        raise ValueError(f"seats differ from the constituency total:\n{wrong}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        "country",
        "constituency",
        "year",
        "party",
        "party_original",
        "seats_total",
        "seats",
        "registered",
        pl.col("cast").alias("ballots_cast"),
        "votes_valid",
        "votes",
    ).write_parquet(PARSED / "results.parquet")


def emit() -> Path:
    artifact = raw.cached(SOURCE, URL)
    results = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="European Parliament elections 1979–2019 by member state and constituency, with votes and seats "
            "by national party, from Caramani's EUI Cadmus dataset",
            polity="EU",
        )
        union = cs.polity("EU")
        parliament = cs.key("eu.body", "european-parliament")
        if {union, parliament} & cs.minted:
            raise ValueError("publish eu.eurlex before the historical European Parliament elections")
        source = cs.source("European University Institute", "EP election results at constituency level, 1979–2019")
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher="European University Institute",
            title="Elections to the European Parliament, 1979–2019: results at constituency level",
            url="https://hdl.handle.net/1814/75475",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
        cs.artifact(artifact, source)

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        polities = {iso: cs.polity(iso) for iso in ISO.values()}
        systems = {code: cs.key("gb.electoral_system", code) for code in UK_SYSTEMS}
        for code, (label, rules) in UK_SYSTEMS.items():
            if systems[code] in cs.minted:
                cs.add("electoral_system", id=systems[code], polity_id=polities["GB"], rules=rules, rules_version=1)
                name(systems[code], label)
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("first past the post must exist")
        kingdom = cs.area("gb.nation", "GBR")
        if kingdom in cs.minted:
            raise ValueError("the United Kingdom's area must already exist")
        cs.add(
            "area_parent",
            area_id=cs.area("eu.member_state", "GB"),
            parent_id=kingdom,
            valid_from=date(1973, 1, 1),
            valid_from_precision="day",
            valid_to=date(2020, 1, 31),
            valid_to_precision="day",
        )
        gb_parties = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'gb.party'").fetchall()
        }
        uk_parties = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'eu.ep_cadmus.party' and value like 'GB:%%'"
            ).fetchall()
        }
        stale = conn.execute(
            """select n.id, n.entity_id from note n join changeset c on c.id = n.changeset_id
               join external_id x on x.entity_id = n.entity_id and x.scheme = 'eu.election' and x.value like 'GB:%%'
               where c.pipeline = %s and n.body_md = %s""",
            [SOURCE, WINDOW_CAVEAT],
        ).fetchall()
        for id, entity in stale:
            cs.add(
                "note_drop",
                note_id=id,
                entity_id=entity,
                body_md=WINDOW_CAVEAT,
                reason="UK polls for the European Parliament were held on a single day, the Thursday of the window",
            )
        retired = {
            value: entity
            for value, entity in conn.execute(
                """select x.value, x.entity_id from external_id x join entity e on e.id = x.entity_id
                   where x.scheme = 'eu.contest' and x.value = any(%s) and e.retired_by is null""",
                [["GB:1994:gb.ep_constituency:scotland", "GB:1994:gb.ep_constituency:wales"]],
            ).fetchall()
        }
        for entity in retired.values():
            cs.add(
                "entity_retirement",
                entity_id=entity,
                reason="Scotland and Wales elected their members in single-member constituencies by first past the post "
                "in 1994; the source's row is a national aggregate, not a contest",
            )
        umbrellas: dict[int, UUID] = {}
        for year, (start, end) in WINDOWS.items():
            umbrella = cs.key("eu.election", f"{start}:european-parliament")
            umbrellas[year] = umbrella
            cs.add(
                "election",
                id=umbrella,
                polity_id=union,
                kind="general",
                date_from=start,
                date_to=end,
                date_precision="day",
                status="held",
            )
            name(umbrella, f"{year} European Parliament election")
            cs.add("slug", path=f"eu/{start}-european-parliament", entity_id=umbrella)

        elections: dict[tuple[str, int], tuple[UUID, str]] = {}
        parties: dict[tuple[str, str], UUID] = {}
        units: dict[tuple[str, str], UUID] = {}
        skipped: dict[str, int] = {}
        sets = 0
        frames: list[pl.DataFrame] = []
        for (country, year), by_election in results.group_by(["country", "year"], maintain_order=True):
            iso = ISO[country]
            window = WINDOWS.get(year)
            election = cs.key("eu.election", f"{iso}:{year}:european-parliament")
            start = window[0] if window else date(year, 1, 1)
            path = f"{iso.lower()}/{start}-european-parliament"
            cs.add(
                "election",
                id=election,
                polity_id=polities[iso],
                parent_id=umbrellas.get(year),
                kind="general",
                date_from=start,
                date_to=start if iso == "GB" else window[1] if window else date(year, 12, 31),
                date_precision="day" if window else "year",
                original_text=str(year),
                status="held",
            )
            name(election, f"{year} European Parliament election in {country}")
            cs.add("slug", path=path, entity_id=election)
            if iso != "GB":
                cs.add(
                    "note",
                    entity_id=election,
                    kind="caveat",
                    body_md=(
                        WINDOW_CAVEAT
                        if window
                        else "The source gives only the year of the election, which took place between general "
                        "European Parliament elections following the country's accession."
                    ),
                )
            elections[(country, year)] = (election, path)
            for (constituency,), rows in by_election.group_by(["constituency"], maintain_order=True):
                euc = iso == "GB" and year < 1999 and constituency != "Northern Ireland"
                if euc and year not in (1979, 1984):
                    skipped[f"{country} {constituency}"] = skipped.get(f"{country} {constituency}", 0) + 1
                    continue
                college = iso == "BE" or constituency == "Greenland"
                if euc:
                    label = EUC_NAMES.get((year, constituency), constituency)
                    scheme, code = "gb.euc", f"{year}:{slug(label)}"
                    if int(rows["seats"].fill_null(0).sum()) != 1:
                        raise ValueError(f"{year} {constituency}: {rows['seats'].sum()} seats declared, not 1")
                elif constituency == "National":
                    scheme, code, label = "eu.member_state", iso, country
                elif college:
                    scheme, code, label = "eu.member_state", iso, constituency
                else:
                    code = CONSTITUENCIES.get((iso, constituency))
                    if code is None and iso == "PL":
                        code = constituency.split(" ", 1)[0]
                    scheme = f"{iso.lower()}.ep_constituency"
                    label = constituency
                    if code is None:
                        skipped[f"{country} {constituency}"] = skipped.get(f"{country} {constituency}", 0) + 1
                        continue
                area = cs.area(scheme, code)
                if area in cs.minted:
                    skipped[f"{country} {constituency}"] = skipped.get(f"{country} {constituency}", 0) + 1
                    continue
                unit = units.get((scheme, code))
                if unit is None:
                    unit = units[(scheme, code)] = cs.unit(scheme, code)
                    if unit in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polities[iso],
                            scheme_code=scheme,
                            code=code,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                            valid_from=WINDOWS[1979][0],
                        )
                seats_total = int(rows["seats_total"][0])
                contest_key = f"{iso}:{year}:{scheme}:{code}" + (f":{fold(constituency)}" if college else "")
                contest = cs.key("eu.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=parliament,
                    district_area_id=area,
                    magnitude=seats_total,
                    electoral_system_id=(
                        fptp
                        if euc
                        else systems["ep-stv" if code == "northern-ireland" else "ep-list"]
                        if iso == "GB"
                        else None
                    ),
                    default_result_kind="counted",
                )
                title = f"{year} European Parliament election in {country}" + (
                    "" if constituency == "National" else f", {label}"
                )
                name(contest, title)
                cs.add(
                    "slug",
                    path=f"{path}/european-parliament" if constituency == "National" else f"{path}/{fold(label)}",
                    entity_id=contest,
                )
                result_set = cs.key("eu.ep_cadmus.result_set", contest_key)
                listed = int(rows["votes"].sum())
                valid = rows["votes_valid"][0]
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=min(1, listed / valid) if valid else None,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The source omits parties with under 0.5 percent of the vote and no seats, so the listed "
                    "votes may fall short of the valid votes. Votes for the omitted parties are not recorded.",
                )
                values = []
                for position, row in enumerate(rows.iter_rows(named=True), start=1):
                    label_text = row["party_original"] or row["party"]
                    shown = row["party"] if iso == "GB" and row["party_original"] in MISSING else label_text
                    other = row["party"].lower() in OTHERS
                    party = None
                    if iso == "GB" and not other and not label_text.startswith("Independent"):
                        labels = (label_text, row["party"])
                        umit_key = next(
                            (
                                key
                                for key in (
                                    *(UK_PARTIES.get(text) for text in labels),
                                    "liberal" if label_text == "Liberal Democrats" and year < 1988 else None,
                                    *(umit.ALIASES.get(text) for text in labels),
                                    *(slug(text) + suffix for suffix in ("", "-party") for text in labels),
                                )
                                if key is not None and (key in umit.EXISTING or key in gb_parties or key == "liberal")
                            ),
                            slug(row["party"]),
                        )
                        party = parties.get((iso, umit_key))
                        if party is None:
                            party = parties[(iso, umit_key)] = (
                                cs.key(*umit.EXISTING[umit_key])
                                if umit_key in umit.EXISTING
                                else cs.key("gb.party", umit_key)
                            )
                            if party in cs.minted:
                                cs.add("party", id=party, kind="party", polity_id=polities[iso])
                                name(party, umit.NAMES.get(umit_key, row["party"]))
                        old = uk_parties.get(f"GB:{label_text}")
                        if old is not None and old != party and old not in cs.survivors and umit_key != "liberal":
                            cs.merge(old, party)
                    elif not other and iso != "GB":
                        key = (iso, label_text)
                        party = parties.get(key)
                        if party is None:
                            party = parties[key] = cs.key("eu.ep_cadmus.party", f"{iso}:{label_text}")
                            if party in cs.minted:
                                cs.add("party", id=party, polity_id=polities[iso])
                                name(party, row["party"])
                                if row["party_original"] and row["party_original"] != row["party"]:
                                    name(party, row["party_original"], "und", "native", False)
                    option = cs.key("eu.ep_cadmus.option", f"{contest_key}:{fold(label_text) or label_text}:{position}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if other else "candidate" if euc else "list",
                        party_id=party,
                        ballot_position=position,
                        ballot_label=shown,
                    )
                    if row["seats"]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="elected" if euc else "won",
                            seats=row["seats"],
                        )
                    values.append((str(option), 1, row["votes"]))
                    if row["seats"] is not None:
                        values.append((str(option), 16, row["seats"]))
                accounting = [
                    (None, 2, rows["registered"][0]),
                    (None, 3, rows["ballots_cast"][0]),
                    (None, 4, valid if valid is None or listed <= valid else None),
                ]
                values += [value for value in accounting if value[2] is not None]
                sets += 1
                frames.append(
                    pl.DataFrame(
                        {
                            "result_set_id": [str(result_set)] * len(values),
                            "unit_id": [str(unit)] * len(values),
                            "option_id": [value[0] for value in values],
                            "channel": [0] * len(values),
                            "measure": [value[1] for value in values],
                            "count_round": [0] * len(values),
                            "flags": [0] * len(values),
                            "value": [value[2] for value in values],
                        },
                        schema={
                            "result_set_id": pl.String,
                            "unit_id": pl.String,
                            "option_id": pl.String,
                            "channel": pl.Int16,
                            "measure": pl.Int16,
                            "count_round": pl.Int16,
                            "flags": pl.Int16,
                            "value": pl.Float64,
                        },
                    ),
                )
        cs.frame("result_value", pl.concat(frames))
        print(f"{sets} contests, {len(elections)} elections, {len(parties)} parties, skipped {skipped}")
        return cs.write()
