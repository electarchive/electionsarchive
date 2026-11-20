"""Pakistan's 2018 general election by polling station, from a hand-keyed academic transcription of the Election
Commission's result forms.

The Commission published its polling-station results of 25 July 2018 as scanned forms: the consolidated statement of
each returning officer (Form 48) and the result of the count at each polling station (Form 45), with the final
consolidated result of each constituency (Form 49). Data entry operators keyed them by hand for the 2019 dataset
"2018 Pakistani General Election Polling Station Data" (Luke Sonnet, Open Science Framework, MIT License). The
dataset covers 252 National Assembly and 540 provincial assembly constituencies; the forms of the rest were missing or
unreadable.

The Commission's gazette notifications of 2 October 2018 list each constituency's candidates with their votes and name
the candidate declared elected (the returned candidate). Those name the winner of every constituency whose
notification was not stayed by a court; the keyed constituencies the notification stayed carry no outcomes.

Each contest gets two result sets. The default holds the Form 49 total of each candidate at the constituency, with the
registered voters of the constituency, as the dataset keys them. A second set holds the polling-station counts as
keyed, with its completeness, its keyed votes divided by the Form 49 votes."""

import json
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "pk.ps2018"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
DAY = date(2018, 7, 25)
STAMP = DAY.isoformat()
OSF = "https://osf.io/mtsnd/"
WIDE = "https://osf.io/69gpn/download"
LONG = "https://osf.io/2sdzg/download"
GAZETTE_FOLDER = "https://web.archive.org/web/20230613221223id_/https://ecp.gov.pk/storage/files/1/"
GAZETTES = {
    "NA": GAZETTE_FOLDER + "National%20Assembly1.pdf",
    "PP": GAZETTE_FOLDER + "Provincial%20Punjab1.pdf",
    "PS": GAZETTE_FOLDER + "Provincial%20Sindh1.pdf",
    "PK": GAZETTE_FOLDER + "Provincial%20KPK1.pdf",
    "PB": GAZETTE_FOLDER + "Provincial%20Balochistan1.pdf",
}
GAZETTE_PAGE = "https://ecp.gov.pk/detailed-gazette-notification"
# The candidate each gazette notification declares elected, by constituency: the keyed candidate's ballot position
# and the gazette page of the declaration. Notifications stayed by the High Court of Sindh (PS-29, PS-48, PS-54) name
# no one.
RETURNED: dict[str, dict] = json.loads((Path(__file__).parent / "ps2018_returned.json").read_text())
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5

# Constituencies whose keyed Form 49 cannot stand as the constituency's result: PP-8's keyed Form 49 figures are
# multiples of 116 that bear no relation to its keyed polling-station counts (20,184 votes in all, against 142,202).
FORM49_UNUSABLE = {"PP8"}

PACKAGES = [f"{SOURCE}/registry", f"{SOURCE}/{STAMP}"]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DATASET = key("source", "osf")
GAZETTE = key("source", "gazette")
SYSTEM = key("electoral_system", "fptp")
STATION = "pk.station"
PA_SCHEME = "pk.pa"


class Assembly(NamedTuple):
    body: str
    name: str
    title: str
    kind: str


ASSEMBLIES = {
    "NA": Assembly(
        key("body", "na"), "National Assembly of Pakistan", "2018 Pakistani National Assembly", "lower_house"
    ),
    "PP": Assembly(
        key("body", "pp"), "Provincial Assembly of the Punjab", "2018 Punjab Provincial Assembly", "unicameral"
    ),
    "PS": Assembly(key("body", "ps"), "Provincial Assembly of Sindh", "2018 Sindh Provincial Assembly", "unicameral"),
    "PK": Assembly(
        key("body", "pk"),
        "Provincial Assembly of Khyber Pakhtunkhwa",
        "2018 Khyber Pakhtunkhwa Provincial Assembly",
        "unicameral",
    ),
    "PB": Assembly(
        key("body", "pb"), "Provincial Assembly of Balochistan", "2018 Balochistan Provincial Assembly", "unicameral"
    ),
}

METHOD = (
    "The final consolidated result of the constituency (Form 49) issued by the Election Commission of Pakistan for the "
    "general election of 25 July 2018, as keyed by hand from the Commission's scanned forms for the 2019 dataset "
    '"2018 Pakistani General Election Polling Station Data" (Luke Sonnet, Open Science Framework). Registered '
    "voters are those of the constituency's electoral roll, keyed from the polling scheme (Form 28). The keyed data "
    "holds no total of valid, rejected, or total votes for the constituency, so none is stored. The returned "
    "candidate is the one the Commission's gazette notification of 2 October 2018 declares elected; every other "
    "candidate is recorded as having lost."
)
METHOD_STATIONS = (
    "The count at each polling station, keyed by hand from the Election Commission's scanned statements of the "
    "count (Form 45) and the returning officer's consolidated statement (Form 48) for the 2019 dataset \"2018 "
    'Pakistani General Election Polling Station Data" (Luke Sonnet, Open Science Framework). Registered voters at '
    "each polling station are keyed from the polling scheme (Form 28). A cell the operators could not read is left "
    "out. Pages missing from the Commission's scans leave polling stations out, and keying errors remain, so the "
    "polling stations do not always add up to the constituency's Form 49 totals; completeness is the keyed "
    "candidate votes divided by the Form 49 candidate votes. The totals of valid, rejected, and total votes are "
    "stored as keyed, and do not always equal the sum of the keyed candidate votes. Where this set is the "
    "constituency's default, its outcomes are those of the Commission's gazette notification of 2 October 2018."
)


def fetch() -> None:
    for url in (WIDE, LONG):
        raw.fetch(SOURCE, url, suffix=".csv")
    for url in GAZETTES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", PACKAGES)


def packages() -> list[str]:
    return PACKAGES


def _read(url: str) -> pl.DataFrame:
    return pl.read_csv(raw.cached(SOURCE, url).path, infer_schema_length=0)


def _int(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.Int64, strict=False)


def _code(constituency: str) -> str:
    """NA1 -> NA-1."""
    return f"{constituency[:2]}-{constituency[2:]}"


def long() -> pl.DataFrame:
    return _read(LONG).select(
        "constituency_id",
        "constituency_ps_id",
        _int("candidate_id").alias("position"),
        pl.col("candidate_name").str.strip_chars().alias("name"),
        pl.col("candidate_party").str.strip_chars().alias("party"),
        _int("candidate_votes").alias("votes"),
        # The dataset codes a Form 49 figure it lacks as -88 or -99.
        _int("candidate_total_valid_votes_polled_49").alias("form49"),
    )


def wide() -> pl.DataFrame:
    return _read(WIDE).select(
        "constituency_id",
        "constituency_ps_id",
        "province",
        "constituency_name",
        _int("ps_id").alias("station"),
        pl.col("ps_name").str.strip_chars().alias("ps_name"),
        pl.col("ps_gender_type").alias("gender"),
        # -88 marks a missing figure, and -198 two of the -99 marks summed.
        pl.when(_int("constituency_registered_voters_total") >= 0)
        .then(_int("constituency_registered_voters_total"))
        .alias("constituency_registered"),
        _int("total_voters").alias("registered"),
        _int("valid_votes").alias("valid"),
        _int("invalid_votes").alias("invalid"),
        _int("total_votes").alias("cast"),
    )


def party_id(name: str) -> str:
    return key("party", name)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Pakistan's National Assembly, its four provincial assemblies, their electoral system, the parties that "
        "contested the 2018 general election, and the constituency and polling-station schemes, from a keyed "
        "transcription of the Election Commission's 2018 result forms",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=DATASET,
        kind="academic",
        publisher="Open Science Framework",
        title="2018 Pakistani General Election Polling Station Data (Luke Sonnet, 2019), keyed from the Election "
        "Commission of Pakistan's Forms 28, 45, 48, and 49",
        url=OSF,
        license="MIT License",
        license_url="https://opensource.org/licenses/MIT",
        attribution="Sonnet, Luke. 2019. 2018 Pakistani General Election Polling Station Data. "
        "https://doi.org/10.17605/osf.io/mtsnd",
    )
    for url in (WIDE, LONG):
        p.artifact(raw.cached(SOURCE, url), DATASET)
    p.add(
        "source",
        id=GAZETTE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Gazette of Pakistan, Extra., 2 October 2018, Part III: notifications of the returned candidates of the "
        "general election of 25 July 2018",
        url=GAZETTE_PAGE,
    )
    for url in GAZETTES.values():
        p.artifact(raw.cached(SOURCE, url), GAZETTE)
    p.add(
        "scheme", code=PA_SCHEME, polity_id=PAKISTAN, kind="electoral_district", rank=1, noun="provincial constituency"
    )
    p.add("scheme", code=STATION, polity_id=PAKISTAN, kind="precinct", rank=2, noun="polling station")
    for assembly in ASSEMBLIES.values():
        p.add("body", id=assembly.body, polity_id=PAKISTAN, kind=assembly.kind)
        p.name(assembly.body, assembly.name, "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each general constituency of the National Assembly and of the provincial assemblies elects one "
        "member by plurality. Seats reserved for women and non-Muslims are filled from party lists in proportion to "
        "the general seats won, and are not part of the constituency results stored here.",
        lang="en",
    )
    for name in sorted(set(long()["party"].unique().to_list()) - {"Independent"}):
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, name, "en", "official")
    return p


def election() -> Package:
    p = Package(
        f"{SOURCE}/{STAMP}",
        SOURCE,
        "Pakistan's general election of 25 July 2018 for the National Assembly and the four provincial assemblies, "
        "by constituency (Form 49) and by polling station (Forms 45 and 48), from a keyed transcription of the "
        "Election Commission's result forms",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {PAKISTAN, "pk.na"}
    for url in (WIDE, LONG, *GAZETTES.values()):
        p.uses(raw.cached(SOURCE, url))
    rows = long()
    stations = wide()
    election = key("election", STAMP)
    p.add("election", id=election, polity_id=PAKISTAN, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=f"pk/{STAMP}-general", entity_id=election, is_current=True)
    p.name(election, "2018 Pakistani general election", "en", "official")
    p.add(
        "note",
        id=key("note", election, "coverage"),
        entity_id=election,
        kind="context",
        body_md="The National Assembly and the provincial assemblies of the Punjab, Sindh, Khyber Pakhtunkhwa, and "
        "Balochistan were elected on the same day. The keyed forms cover 252 of the National Assembly's general "
        "constituencies and 540 of the provincial assemblies' general constituencies; the constituencies of "
        "Islamabad, the polls postponed to later dates, and the constituencies whose forms the keyed dataset lacks "
        "are not stored here.",
        lang="en",
    )

    constituencies = (
        stations.group_by("constituency_id")
        .agg(
            pl.col("province").first(),
            pl.col("constituency_name").first(),
            pl.col("constituency_registered").first(),
        )
        .sort("constituency_id")
    )
    candidates = (
        rows.group_by("constituency_id", "position")
        .agg(
            pl.col("name").first(),
            pl.col("party").first(),
            pl.col("form49").first(),
            pl.col("votes").sum().alias("keyed"),
        )
        .sort("constituency_id", "position")
    )
    by_seat = candidates.partition_by("constituency_id", as_dict=True)

    values: list[pl.DataFrame] = []
    for seat in constituencies.iter_rows(named=True):
        cid = seat["constituency_id"]
        prefix = cid[:2]
        code = _code(cid)
        assembly = ASSEMBLIES[prefix]
        name = " ".join(seat["constituency_name"].split()).title()
        label = f"{code} {name}"
        contest = key("contest", STAMP, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=assembly.body,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{assembly.title} election, {label}", "en", "official")
        p.add("slug", path=f"pk/{STAMP}-general/{code.lower()}", entity_id=contest, is_current=True)
        unit = key("unit", STAMP, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PAKISTAN,
            scheme_code="pk.na" if prefix == "NA" else PA_SCHEME,
            code=f"{STAMP}:{code}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        seats = by_seat[(cid,)]
        usable = cid not in FORM49_UNUSABLE and bool((seats["form49"] >= 0).all())
        form49 = int(seats["form49"].sum())
        keyed = int(seats["keyed"].sum())
        official = key("result_set", STAMP, code)
        by_station = key("result_set", STAMP, code, "stations")
        if usable:
            p.add(
                "result_set",
                id=official,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=DATASET,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add("note", id=key("note", official), entity_id=official, kind="methodology", body_md=METHOD, lang="en")
            if seat["constituency_registered"] is not None:
                _put(p, official, unit, None, REGISTERED, seat["constituency_registered"])
                p.add(
                    "declared",
                    result_set_id=official,
                    option_id=None,
                    measure=REGISTERED,
                    value=seat["constituency_registered"],
                )
        p.add(
            "result_set",
            id=by_station,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=DATASET,
            status="active",
            is_default=not usable,
            completeness=round(keyed / form49, 6) if usable and form49 else None,
        )
        p.add(
            "note",
            id=key("note", by_station),
            entity_id=by_station,
            kind="methodology",
            body_md=METHOD_STATIONS,
            lang="en",
        )
        if not usable:
            p.add(
                "note",
                id=key("note", by_station, "caveat"),
                entity_id=by_station,
                kind="caveat",
                body_md="The keyed dataset lacks a usable Form 49 for this constituency, so its consolidated result "
                "is not stored, and the polling-station counts stand alone.",
                lang="en",
            )
        elif keyed != form49:
            p.add(
                "note",
                id=key("note", by_station, "caveat"),
                entity_id=by_station,
                kind="caveat",
                body_md=f"The keyed polling-station counts sum to {keyed:,} candidate votes, against {form49:,} in "
                "the constituency's Form 49.",
                lang="en",
            )
        for c in seats.iter_rows(named=True):
            position = c["position"]
            option = key("option", STAMP, code, position)
            person = key("person", STAMP, code, position)
            independent = c["party"] == "Independent"
            p.add("person", id=person)
            p.name(person, c["name"], "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=c["name"],
            )
            p.add(
                "candidacy",
                id=key("candidacy", STAMP, code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c["name"],
                member_of_party_id=None if independent else party_id(c["party"]),
                is_independent=independent,
            )
            returned = RETURNED.get(code.replace("-", ""))
            if returned is not None:
                p.add(
                    "outcome",
                    result_set_id=official if usable else by_station,
                    option_id=option,
                    kind="elected" if returned["position"] == position else "lost",
                )
            if usable:
                _put(p, official, unit, option, VOTES, c["form49"])
                p.add("declared", result_set_id=official, option_id=option, measure=VOTES, value=c["form49"])

    # Polling stations, in bulk.
    stations = stations.with_columns(
        pl.col("constituency_id").map_elements(_code, return_dtype=pl.String).alias("code"),
    ).with_columns(
        pl.struct("code", "station")
        .map_elements(lambda r: key("unit", STAMP, r["code"], r["station"]), return_dtype=pl.String)
        .alias("unit_id"),
        pl.col("code").map_elements(lambda c: key("unit", STAMP, c), return_dtype=pl.String).alias("parent_id"),
        pl.col("code")
        .map_elements(lambda c: key("result_set", STAMP, c, "stations"), return_dtype=pl.String)
        .alias("result_set_id"),
    )
    p.frame(
        "reporting_unit",
        stations.select(
            pl.col("unit_id").alias("id"),
            pl.lit(PAKISTAN).alias("polity_id"),
            pl.lit(STATION).alias("scheme_code"),
            pl.format("{}:{}:{}", pl.lit(STAMP), "code", "station").alias("code"),
            pl.lit("unlocated").alias("area_relation"),
            pl.lit("standard").alias("kind"),
            "parent_id",
        ),
    )
    named = stations.select(
        "unit_id",
        pl.format(
            "{} polling station {}{}",
            "code",
            "station",
            pl.when(pl.col("ps_name").is_not_null() & (pl.col("ps_name") != "NA"))
            .then(pl.lit(", ") + pl.col("ps_name").str.replace_all(r"\s+", " "))
            .otherwise(pl.lit("")),
        ).alias("text"),
    )
    p.frame(
        "name",
        named.select(
            pl.struct("unit_id", "text")
            .map_elements(lambda r: p.id("name", r["unit_id"], "en", "official", r["text"]), return_dtype=pl.String)
            .alias("id"),
            pl.col("unit_id").alias("entity_id"),
            "text",
            pl.lit("en").alias("lang"),
            pl.lit("official").alias("kind"),
            pl.lit(True).alias("is_preferred"),
        ),
    )
    for measure, column in ((REGISTERED, "registered"), (CAST, "cast"), (VALID, "valid"), (INVALID, "invalid")):
        values.append(
            stations.filter(pl.col(column).is_not_null()).select(
                "result_set_id",
                "unit_id",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(measure).alias("measure"),
                pl.col(column).alias("value"),
            )
        )
    votes = (
        rows.filter(pl.col("votes").is_not_null())
        .join(stations.select("constituency_ps_id", "code", "unit_id", "result_set_id"), on="constituency_ps_id")
        .with_columns(
            pl.struct("code", "position")
            .map_elements(lambda r: key("option", STAMP, r["code"], r["position"]), return_dtype=pl.String)
            .alias("option_id")
        )
    )
    values.append(
        votes.select(
            "result_set_id", "unit_id", "option_id", pl.lit(VOTES).alias("measure"), pl.col("votes").alias("value")
        )
    )
    facts = pl.concat(values).with_columns(
        pl.lit(0).alias("channel"), pl.lit(0).alias("count_round"), pl.lit(0).alias("flags")
    )
    p.frame("result_value", facts)
    for code, reason in ACCEPTED.items():
        p.accept(code, reason)
    return p


ACCEPTED = {
    "accounting.votes_valid": "At 696 of the 156,114 keyed polling stations, the keyed candidate votes do not sum to "
    "the keyed valid votes: the operators keyed both from the Commission's scanned forms, some candidate cells were "
    "unreadable and are left out, and some forms' own figures disagree. Both are stored as keyed, and each "
    "polling-station set carries a methodology note saying so.",
    "accounting.ballots_sum": "At 2 keyed polling stations (PP-230 station 36 and PS-33 station 62) the keyed valid votes exceed "
    "the keyed total votes, with the rejected votes unreadable; all are stored as keyed.",
    "accounting.turnout": "At 82 keyed polling stations the keyed total votes exceed the registered voters keyed from "
    "the polling scheme (Form 28); both are stored as keyed.",
}


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
