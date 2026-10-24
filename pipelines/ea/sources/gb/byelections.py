import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import mnis, umit
from ea.store import DATA, connect

SOURCE = "gb.byelections"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://raw.githubusercontent.com/ukparliament/psephology/main/db/data/results-by-parliament/{}/by-elections/{}.csv"
PARLIAMENTS = range(55, 60)
TITLE = "Election results: by-elections in the 55th to 59th Parliaments"
UMIT = f"{umit.DATAVERSE}/6815697?format=original"
UMIT_README = f"{umit.DATAVERSE}/6811223"
UMIT_TITLE = "By-elections to the UK House of Commons, version 3"
UMIT_LANDING = "https://doi.org/10.7910/DVN/KN5Y7V"
OFFICIAL_FROM = date(2010, 5, 6)
NIR_FROM = date(1921, 6, 7)
SIX_COUNTIES = "Antrim|Armagh|Belfast|Down|Fermanagh|Londonderry|Tyrone|Queen's University"
NATIONS = {"ENG": "England", "SCT": "Scotland", "WLS": "Wales", "NIR": "Northern Ireland"}
VOTES, REGISTERED, VALID, INVALID = 1, 2, 4, 5
ALIASES = {
    "Social and Liberal Democrats": "liberal-democrats",
    "The Greens": "green-party",
    "Workers Revolutonary": "workers-revolutionary-party",
    "Indpendent Labour Party": "independent-labour-party",
    "Captain Rainbows Universal Party": "captain-rainbow-s-universal-party",
    "Death off Road: Freight on Rail": "death-off-roads-freight-on-rail",
    "Death off Roads: Freight on Rails": "death-off-roads-freight-on-rail",
}
NAMED_TWO = re.compile(
    r"\bboth\b|\w(?:'s?|s'|') (?:and|&) [A-Z][\w-]+(?:'s?|s'|')? |[A-Z][\w-]+ (?:and|&) [A-Z][\w-]+(?:'s?|s'|') |"
    r"\w(?:'s?|s'|') (?:death|resignation|appointment|elevation|succession)[^;]*? and [A-Z][\w-]+(?:'s?|s'|') "
)
WHOLE = re.compile(r"(?:the|general|previous|\d{4}) election (?:being|was) declared void")
HANSARD = "https://api.parliament.uk/historic-hansard/constituencies/{}"
HANSARD_TITLE = "Historic Hansard: constituencies"
CORRECTED = {
    "1901-03-01:eng-maidstone": ("Francis Henry Evans", "Evans, Francis Henry", "Liberal", None, "maidstone", False),
    "1914-06-29:eng-brighton": (
        "Charles Thomas-Stanford",
        "Thomas-Stanford, Charles",
        "Unionist",
        None,
        "brighton",
        True,
    ),
    "1937-06-22:eng-hemel-hempstead": (
        "Frances Davidson",
        "Davidson, Frances",
        "Conservative",
        "2415",
        "hemel-hempstead",
        False,
    ),
    "1950-10-25:sct-glasgow-scotstoun": (
        "James Riley Holt Hutchison",
        "Hutchison, James Riley Holt",
        "Scottish Unionist",
        None,
        "glasgow-scotstoun",
        False,
    ),
    "1960-11-16:eng-ludlow": ("Jasper More", "More, Jasper", "Conservative", None, "ludlow", False),
    "1960-11-16:eng-tiverton": (
        "Robin John Maxwell-Hyslop",
        "Maxwell-Hyslop, Robin John",
        "Conservative",
        "1296",
        None,
        False,
    ),
    "1961-03-16:eng-colchester": (
        "Philip Antony Fyson Buck",
        "Buck, Philip Antony Fyson",
        "Conservative",
        "1111",
        None,
        False,
    ),
    "1963-03-21:eng-colne-valley": (
        "Albert Edward Patrick Duffy",
        "Duffy, Albert Edward Patrick",
        "Labour",
        "806",
        None,
        False,
    ),
    "1969-03-27:eng-weston-super-mare": ("Jerry Wiggin", "Wiggin, Jerry", "Conservative", "1270", None, False),
    "1978-04-13:sct-glasgow-garscadden": ("Donald Dewar", "Dewar, Donald", "Labour", "678", None, False),
    "1982-12-02:sct-glasgow-queen-s-park": ("Helen McElhone", "McElhone, Helen", "Labour", "977", None, False),
}
CAUSES = {"1914-06-29:eng-brighton": "By-election held due to Gordon's resignation"}
LIBRARY = "https://researchbriefings.files.parliament.uk/documents/SN02383/SN02383.xlsx"
LIBRARY_TITLE = "UK parliamentary by-elections since 1945 (SN02383)"
DELIVERED = DATA / "inbox" / "manual" / "MF-071" / "SN02383.xlsx"
CAVEATS = {
    "winners": "Umit's by-election dataset repeats the previous general election's result for this by-election, so it "
    "is left out. Only the winner is shown, from Parliament's member records, and the other candidates and the votes "
    "are not loaded.",
    "tacit": "Umit's by-election dataset repeats the previous general election's result for this by-election, so it "
    "is left out. The winner, returned unopposed, is from Parliament's member records.",
    "derived": "Umit's by-election dataset marks no winners. The candidates shown as elected are those with the most "
    "votes, one for each seat filled.",
    "tie": "Two or more candidates tie for the last seat in Umit's by-election dataset, so the tied candidates are not "
    "shown as elected.",
    "missing": "Umit's by-election dataset lists no more candidates than seats in this contested race, so some "
    "candidates are missing and no candidate is shown as elected.",
    "unopposed": "Umit's by-election dataset marks every candidate here as unopposed but lists more candidates than "
    "seats, so no candidate is shown as elected.",
}


def fetch() -> None:
    for parliament in PARLIAMENTS:
        for table in ("constituencies", "candidacies"):
            raw.fetch(SOURCE, BASE.format(parliament, table), suffix=".csv")
    raw.fetch(SOURCE, UMIT, suffix=".csv")
    raw.fetch(SOURCE, UMIT_README, suffix=".txt")
    for *_, page, _ in CORRECTED.values():
        if page:
            raw.fetch(SOURCE, HANSARD.format(page), suffix=".html")
    try:
        raw.cached(SOURCE, LIBRARY)
    except LookupError:
        raw.store(
            SOURCE,
            LIBRARY,
            DELIVERED.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime.fromtimestamp(DELIVERED.stat().st_mtime, UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def read(table: str) -> pl.DataFrame:
    return pl.concat(
        [
            pl.read_csv(raw.cached(SOURCE, BASE.format(parliament, table)).path, infer_schema_length=0)
            for parliament in PARLIAMENTS
        ],
        how="diagonal_relaxed",
    )


def parse() -> None:
    general = pl.read_parquet(DATA / "parsed" / umit.SOURCE / "results.parquet")
    held = read("constituencies").select(
        day=pl.col("By-election date").str.to_date("%d/%m/%Y"),
        ons=pl.col("ONS ID"),
        constituency=pl.col("Constituency name"),
        member=pl.concat_str(
            pl.col("Member first name").str.strip_chars(), pl.col("Member surname").str.strip_chars(), separator=" "
        ),
        electorate=pl.col("Electorate").cast(pl.Int64, strict=False),
        valid=pl.col("Valid votes").cast(pl.Int64, strict=False),
        invalid=pl.col("Invalid votes").cast(pl.Int64, strict=False),
    )
    stood = read("candidacies").select(
        day=pl.col("Election date").str.to_date("%d/%m/%Y"),
        ons=pl.coalesce("ONS ID", "ONS ID2"),
        party=pl.col("Party name"),
        first=pl.col("Candidate first name").str.strip_chars(),
        surname=pl.col("Candidate surname").str.strip_chars(),
        gender=pl.col("Candidate gender").str.to_lowercase(),
        mnis=pl.col("Member MNIS ID"),
        votes=pl.col("Votes").cast(pl.Int64),
    )
    seats = (
        pl.read_csv(raw.cached(umit.SOURCE, umit.RESULTS).path, infer_schema_length=0)
        .select(
            date_start=pl.col("date_start").str.to_date(),
            constituency=pl.col("constituency"),
            ons=pl.col("constituency_id"),
        )
        .unique()
        .join(
            general.select("date_start", "constituency", "unit", "country").unique(), on=["date_start", "constituency"]
        )
        .sort("date_start")
    )
    held = held.sort("day").join_asof(
        seats.rename({"date_start": "general"}), left_on="day", right_on="general", by="ons", strategy="backward"
    )
    if held["unit"].is_null().any():
        raise ValueError(
            f"no general election seat for {held.filter(pl.col('unit').is_null())['constituency'].to_list()}"
        )
    official = (
        stood.join(held.drop("constituency_right", strict=False), on=["day", "ons"], how="left")
        .with_columns(
            candidate=pl.concat_str("first", "surname", separator=" "),
        )
        .with_columns(
            identity=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit(":"),
                "unit",
                pl.lit(":"),
                pl.col("candidate").map_elements(slug, return_dtype=pl.String),
            )
        )
        .with_columns(
            elected=pl.col("candidate") == pl.col("member"),
            general=pl.concat_str(pl.col("general").cast(pl.String), pl.lit(":"), "unit"),
            vacancies=pl.lit(1),
            status=pl.lit("declared"),
            unopposed=pl.lit(False),
            day_to=pl.col("day"),
            cause=pl.lit(None, pl.String),
            origin=pl.lit("parliament"),
        )
    )
    if official["unit"].is_null().any():
        raise ValueError("a candidacy has no by-election")
    reasons: dict[date, list[tuple[set[str], str]]] = {}
    for _, place, reason, day, *_ in (
        pl.read_excel(
            raw.cached(SOURCE, LIBRARY).path, sheet_name="By-elections since 2005", read_options={"header_row": 1}
        )
        .drop_nulls("Date of by-election")
        .iter_rows()
    ):
        reason = re.sub(r"\s*\d*\s*$", "", reason.replace("\xa0", " ")).replace("  ", " ")
        reasons.setdefault(day.date() if isinstance(day, datetime) else day, []).append(
            (set(re.findall(r"[a-z]+", place.lower())), f"By-election held due to the {reason[0].lower()}{reason[1:]}")
        )
    causes = {}
    for day, place in official.select("day", "constituency").unique().iter_rows():
        words = set(re.findall(r"[a-z]+", place.lower()))
        found = sorted(reasons.get(day, []), key=lambda row: -len(row[0] & words))
        if not found or not found[0][0] & words:
            raise ValueError(f"the Library lists no by-election in {place} on {day}")
        causes[(day, place)] = found[0][1]
    official = official.with_columns(
        cause=pl.struct("day", "constituency").map_elements(
            lambda row: causes[(row["day"], row["constituency"])], return_dtype=pl.String
        )
    )
    winners = official.group_by("day", "ons").agg(pl.col("elected").sum())
    if winners.filter(pl.col("elected") != 1).height:
        raise ValueError(f"no single declared member: {winners.filter(pl.col('elected') != 1).rows()}")

    contests: dict[str, list[tuple[date, str, int]]] = {}
    with connect() as conn:
        for day, place, key, magnitude in conn.execute(
            """select e.date_from, split_part(n.text, ' general election in ', 2), x.value, c.magnitude
               from contest c join election e on e.id = c.election_id
               join name n on n.entity_id = c.id and n.is_preferred
               join external_id x on x.entity_id = c.id and x.scheme = 'gb.contest'
               where e.kind = 'general' and c.body_id = (
                   select entity_id from external_id where scheme = 'gb.body' and value = 'house-of-commons')
               order by 1"""
        ).fetchall():
            contests.setdefault(place, []).append((day, key.split(":", 1)[1], magnitude))
    places = general.select("constituency", "date_start", "unit", "country").unique()
    rows = []
    missing: set[tuple[str, str]] = set()
    for line in (
        pl.read_csv(raw.cached(SOURCE, UMIT).path, infer_schema_length=0)
        .with_columns(day=pl.col("date").str.to_date())
        .filter(pl.col("day") < OFFICIAL_FROM)
        .with_columns(cause=pl.col("cause").unique(maintain_order=True).str.join("; ").over("day", "constituency"))
        .unique(["day", "constituency", "candidate"], keep="first", maintain_order=True)
        .iter_rows(named=True)
    ):
        place = line["constituency"]
        names = {place, place.replace(" Burghs", " Boroughs")}
        held = sorted(row for name in names for row in contests.get(name, []))
        found = next((row for row in reversed(held) if row[0] <= line["day"]), held[0] if held else None)
        if found is None:
            missing.add((line["election"], place))
            continue
        (day, unit_code, magnitude) = found
        nearest = (
            places.filter(pl.col("constituency").is_in(names))
            .with_columns(gap=(pl.col("date_start") - line["day"]).dt.total_days().abs())
            .sort("gap")
        )
        if nearest.height == 0:
            missing.add((line["election"], place))
            continue
        person_unit = unit_code if not unit_code.isdigit() else nearest["unit"][0]
        identity = line["candidate"]
        for code, text, since, until, mark in umit.NAMESAKES:
            if person_unit == code and identity == text and since <= line["day"].year <= until:
                identity += mark
        surname, _, given = line["candidate"].partition(", ")
        span = re.match(r"^\d+-(\d+) (\w+) (\d{4}) by-election$", line["election"])
        rows.append(
            {
                "day": line["day"],
                "day_to": datetime.strptime(f"{span[1]} {span[2][:3]} {span[3]}", "%d %b %Y").date()
                if span
                else line["day"],
                "constituency": place,
                "country": nearest["country"][0],
                "unit": unit_code,
                "general": f"{day}:{unit_code}",
                "magnitude": magnitude,
                "candidate": f"{given} {surname}".strip(),
                "surname": surname.lower(),
                "identity": f"{person_unit}:{slug(line['candidate'])}{identity.removeprefix(line['candidate'])}",
                "party": line["party"],
                "votes": int(line["votes"]) if line["votes"] else None,
                "unopposed": line["unopposed"] == "1",
                "cause": line["cause"],
            }
        )
    if missing:
        print(f"{len(missing)} by-elections with no general election seat: {sorted(missing)[:40]}")
    frame = pl.DataFrame(rows)
    by = ["day", "unit"]
    candidates = pl.len().over(by)
    two = (
        (pl.col("magnitude") >= 2)
        & (candidates >= 3)
        & ~pl.col("unopposed")
        & (pl.col("cause").str.contains(NAMED_TWO.pattern) | pl.col("cause").str.contains(WHOLE.pattern))
    )
    frame = frame.with_columns(
        vacancies=pl.when(pl.col("unopposed").all().over(by))
        .then(pl.min_horizontal(candidates, pl.col("magnitude")))
        .when(two)
        .then(pl.lit(2))
        .otherwise(pl.lit(1))
    )
    rank = pl.col("votes").rank("max", descending=True).over(by)
    contested = ~pl.col("unopposed").all().over(by)
    frame = frame.with_columns(
        status=pl.when(~contested & (candidates > pl.col("vacancies")))
        .then(pl.lit("unopposed"))
        .when(~contested)
        .then(pl.lit("tacit"))
        .when(candidates <= pl.col("vacancies"))
        .then(pl.lit("missing"))
        .when((rank <= pl.col("vacancies")).sum().over(by) < pl.col("vacancies"))
        .then(pl.lit("tie"))
        .otherwise(pl.lit("derived"))
    ).with_columns(
        elected=pl.when(pl.col("status") == "tacit")
        .then(True)
        .when(pl.col("status").is_in(["derived", "tie"]))
        .then(rank <= pl.col("vacancies"))
        .otherwise(False),
        origin=pl.lit("umit"),
    )
    previous = {
        (f"{day}:{unit}", tuple(sorted(zip(rows["surname"].fill_null("").str.to_lowercase(), rows["votes"]))))
        for (day, unit), rows in general.group_by("date_start", "unit")
    }
    copies = sorted(
        f"{day}:{unit}"
        for (day, unit), rows in frame.group_by("day", "unit")
        if rows["votes"].is_not_null().any()
        and (rows["general"][0], tuple(sorted(zip(rows["surname"], rows["votes"])))) in previous
    )
    if set(copies) - set(CORRECTED):
        raise ValueError(
            f"by-elections repeating the previous general election: {sorted(set(copies) - set(CORRECTED))}"
        )
    print(f"{len(copies)} by-elections repeating the previous general election, replaced by their winners")
    key = pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":"), "unit")
    replaced = []
    for contest, (candidate, listed, party, member, page, unopposed) in CORRECTED.items():
        dropped = frame.filter(key == contest)
        if dropped.height == 0:
            raise ValueError(f"no Umit by-election {contest}")
        first = dropped.row(0, named=True)
        day = first["day"]
        if page:
            text = re.sub(
                r"\s+", " ", re.sub(r"<[^>]+>", " ", raw.cached(SOURCE, HANSARD.format(page)).path.read_text())
            )
            if f"{day:%B} {day.day}, {day.year} -" not in text:
                raise ValueError(f"Historic Hansard does not seat a member for {contest}")
        elif not any(
            seat["startDate"].startswith(str(day))
            for seat in json.loads(raw.cached(mnis.SOURCE, mnis.BIOGRAPHY.format(member)).path.read_text())["value"][
                "representations"
            ]
        ):
            raise ValueError(f"MNIS does not seat member {member} from {contest}")
        replaced.append(
            first
            | {
                "candidate": candidate,
                "surname": listed.split(", ")[0].lower(),
                "identity": f"{first['identity'].split(':')[0]}:{slug(listed)}",
                "party": party,
                "votes": None,
                "unopposed": unopposed,
                "vacancies": 1,
                "status": "tacit" if unopposed else "winners",
                "elected": True,
                "origin": "hansard" if page else "mnis",
                "mnis": member,
            }
        )
    frame = pl.concat(
        [frame.filter(~key.is_in(list(CORRECTED))), pl.DataFrame(replaced, schema_overrides=frame.schema)],
        how="diagonal_relaxed",
    )
    if frame.filter(pl.len().over(*by, "identity") > 1).height:
        raise ValueError(f"a candidate stands twice: {frame.filter(pl.len().over(*by, 'identity') > 1).rows()[:3]}")
    print(
        "two seats filled:",
        sorted(
            {
                (str(day), place)
                for day, place in frame.filter(pl.col("vacancies") == 2).select("day", "constituency").iter_rows()
            }
        ),
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat([frame, official], how="diagonal_relaxed").with_columns(
        cause=pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":"), "unit").replace_strict(
            CAUSES, default=pl.col("cause")
        )
    ).sort("day", "unit", pl.col("votes"), descending=[False, False, True], nulls_last=True).write_parquet(
        PARSED / "results.parquet"
    )
    print(
        f"{frame.select(by).n_unique()} by-elections from Umit, {official.select('day', 'ons').n_unique()} official, "
        f"{frame.height + official.height} candidacies"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    moved = (pl.col("country") == "IRL") & (pl.col("day") >= NIR_FROM)
    stray = frame.filter(moved & ~pl.col("constituency").str.contains(SIX_COUNTIES))["constituency"].unique()
    if stray.len():
        raise ValueError(f"Irish seats after {NIR_FROM} outside Northern Ireland: {stray.to_list()}")
    frame = frame.with_columns(country=pl.when(moved).then(pl.lit("NIR")).otherwise("country"))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House of Commons by-elections since 1833 by constituency, from Resul Umit's by-election dataset "
            "and, since 2010, Parliament's election results data",
            polity="GB",
        )
        sources = {
            "parliament": cs.source("UK Parliament", TITLE),
            "umit": cs.source(umit.PUBLISHER, UMIT_TITLE),
            "mnis": cs.source("UK Parliament", mnis.TITLE),
            "hansard": cs.source("UK Parliament", HANSARD_TITLE),
        }
        if sources["mnis"] in cs.minted:
            raise ValueError("gb.mnis must publish its source first")
        cs.add(
            "source",
            id=sources["hansard"],
            kind="official_body",
            publisher="UK Parliament",
            title=HANSARD_TITLE,
            url="https://api.parliament.uk/historic-hansard/constituencies/",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Each constituency's members with the dates they sat, from Parliament's digitized Hansard 1803–2005.",
        )
        sources["library"] = cs.source("House of Commons Library", LIBRARY_TITLE)
        cs.add(
            "source",
            id=sources["library"],
            kind="official_body",
            publisher="House of Commons Library",
            title=LIBRARY_TITLE,
            url="https://commonslibrary.parliament.uk/research-briefings/sn02383/",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="The by-elections since 2005 table gives the reason for each by-election, which the cause notes "
            "since 2010 quote.",
        )
        cs.artifact(raw.cached(SOURCE, LIBRARY), sources["library"])
        for *_, member, page, _ in CORRECTED.values():
            if page:
                cs.artifact(raw.cached(SOURCE, HANSARD.format(page)), sources["hansard"])
            else:
                cs.artifact(raw.cached(mnis.SOURCE, mnis.BIOGRAPHY.format(member)), sources["mnis"])
        cs.add(
            "source",
            id=sources["parliament"],
            kind="official_body",
            publisher="UK Parliament",
            title=TITLE,
            url="https://electionresults.parliament.uk/elections.html",
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Read from the House of Commons Library's psephology repository (github.com/ukparliament/psephology), "
            "the data behind electionresults.parliament.uk.",
        )
        cs.add(
            "source",
            id=sources["umit"],
            kind="academic",
            publisher=umit.PUBLISHER,
            title=UMIT_TITLE,
            url=UMIT_LANDING,
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            notes="Compiled from electionhub.co.uk with corrections from the House of Commons, Wikipedia, and Eggers and "
            "Spirling. It marks no winners, and dates of older by-elections held over several days may be inexact.",
        )
        for parliament in PARLIAMENTS:
            for table in ("constituencies", "candidacies"):
                cs.artifact(raw.cached(SOURCE, BASE.format(parliament, table)), sources["parliament"])
        for url in (UMIT, UMIT_README):
            cs.artifact(raw.cached(SOURCE, url), sources["umit"])
        gb = cs.polity("GB")
        commons = cs.key("gb.body", "house-of-commons")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if commons in cs.minted or any(system in cs.minted for system in systems.values()):
            raise ValueError("the House of Commons and its electoral systems must already exist")
        known = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = 'gb.umit.person'").fetchall()
        }
        parties: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}

        titles: dict[UUID, list[tuple[UUID, str]]] = {}
        for id, entity, text in conn.execute(
            """select n.id, n.entity_id, n.text from name n join changeset c on c.id = n.changeset_id and c.pipeline = %s
               join election e on e.id = n.entity_id where n.is_preferred and n.lang = 'en'""",
            [SOURCE],
        ).fetchall():
            titles.setdefault(entity, []).append((id, text))

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)
            for id, old in titles.get(entity, []):
                if old != text:
                    cs.add("name", id=id, entity_id=entity, text=old, lang="en", kind="official", is_preferred=False)

        def party_of(label: str, year: int, country: str | None) -> UUID | None:
            if label in umit.NOT_PARTIES:
                return None
            key = next(
                (
                    key
                    for (era, since), key in sorted(umit.ERAS.items(), reverse=True)
                    if era == label and year >= since
                ),
                None,
            )
            if key is None and label == "Unionist":
                key = "scottish-unionist" if country == "SCT" else "conservative"
            key = key or umit.ALIASES.get(label) or ALIASES.get(label) or slug(label)
            if key not in parties:
                parties[key] = cs.key(*umit.EXISTING[key]) if key in umit.EXISTING else cs.key("gb.party", key)
                if parties[key] in cs.minted:
                    if key in umit.EXISTING:
                        raise ValueError(f"{key} is not in the database")
                    cs.add(
                        "party",
                        id=parties[key],
                        kind="independent_group" if key == "speaker" else "party",
                        polity_id=gb,
                    )
                    name(parties[key], umit.NAMES.get(key, label))
            return parties[key]

        for (day,), on_day in frame.group_by("day", maintain_order=True):
            election = cs.key(f"{SOURCE}.election", str(day))
            spoken = f"{day.day} {day:%B %Y}"
            cs.add(
                "election",
                id=election,
                polity_id=gb,
                kind="by_election",
                date_from=day,
                date_to=on_day["day_to"].max(),
                status="held",
            )
            name(election, f"By-elections, {spoken}")
            path = f"gb/{day}-by-election"
            cs.add("slug", path=path, entity_id=election)
            children: dict[str, UUID] = {}
            for (country,), in_nation in on_day.group_by("country", maintain_order=True):
                if country not in NATIONS or (country == "NIR" and day < NIR_FROM):
                    continue
                child = children[country] = cs.key(f"{SOURCE}.election", f"{day}:{country}")
                cs.add(
                    "election",
                    id=child,
                    polity_id=cs.polity(f"GB-{country}"),
                    parent_id=election,
                    kind="by_election",
                    date_from=day,
                    date_to=in_nation["day_to"].max(),
                    status="held",
                )
                seats = in_nation["constituency"].unique(maintain_order=True).to_list()
                name(
                    child,
                    f"{seats[0]} by-election, {spoken}"
                    if len(seats) == 1
                    else f"By-elections in {NATIONS[country]}, {spoken}",
                )
                cs.add("slug", path=f"{path}/{slug(NATIONS[country])}", entity_id=child)
            for (unit_code,), lines in on_day.group_by("unit", maintain_order=True):
                first = lines.row(0, named=True)
                held = children.get(first["country"], election)
                general = cs.key("gb.contest", first["general"])
                if general in cs.minted:
                    raise ValueError(f"no general election contest {first['general']}")
                (district,) = conn.execute("select district_area_id from contest where id = %s", [general]).fetchone()
                tacit = first["status"] in ("tacit", "unopposed")
                contest_key = f"{day}:{unit_code}"
                contest = cs.key(f"{SOURCE}.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=held,
                    kind="body_seats",
                    body_id=commons,
                    district_area_id=district,
                    magnitude=first["vacancies"],
                    round_number=1,
                    electoral_system_id=systems["fptp" if first["vacancies"] == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{day.year} by-election in {first['constituency']}")
                cs.add("slug", path=f"{path}/{slug(first['constituency'])}", entity_id=contest)
                if first["cause"]:
                    cs.add("note", entity_id=contest, kind="context", body_md=first["cause"])
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=held,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=sources[first["origin"]],
                    is_default=True,
                    completeness=0 if first["status"] == "winners" else 1,
                )
                if first["status"] in CAVEATS and (first["status"] != "tacit" or contest_key in CORRECTED):
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEATS[first["status"]])
                unit = cs.unit("gb.pcon", unit_code)
                for measure, value in (
                    (REGISTERED, first["electorate"]),
                    (VALID, first["valid"]),
                    (INVALID, first["invalid"]),
                ):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{slug(line['candidate'])}"
                    option = cs.key(f"{SOURCE}.option", option_key)
                    identity = line["identity"]
                    if identity not in persons:
                        persons[identity] = (
                            cs.key("mnis", line["mnis"])
                            if line["mnis"]
                            else cs.key("gb.umit.person", identity)
                            if identity in known
                            else cs.key(f"{SOURCE}.person", identity)
                        )
                        if persons[identity] in cs.minted:
                            cs.add("person", id=persons[identity], gender=line["gender"])
                            name(persons[identity], line["candidate"])
                    party = party_of(line["party"], day.year, line["country"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=line["candidate"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=persons[identity],
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=line["party"] == "Independent",
                    )
                    if line["votes"] is not None:
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
        print(
            f"{frame['day'].n_unique()} days; contests on the umbrella (Ireland before {NIR_FROM}):",
            frame.filter(
                ~pl.col("country").is_in(list(NATIONS)) | ((pl.col("country") == "NIR") & (pl.col("day") < NIR_FROM))
            )
            .select("day", "unit")
            .unique()
            .height,
        )
        corrected = [cs.key(f"{SOURCE}.result_set", key) for key in CORRECTED]
        cs.manifest["scope"] = {"polity": "GB", "clears": [str(id) for id in corrected]}
        if any(row["result_set_id"] in corrected for row in cs.rows["result_value"]):
            raise ValueError("a winners-only by-election set carries vote values")
        kept = {row["id"] for row in cs.rows["contest_option"]}
        noted = {(row["entity_id"], row["body_md"]) for row in cs.rows["note"]}
        dropped = 0
        for result_set, contest in conn.execute(
            "select id, contest_id from result_set where id = any(%s)", [corrected]
        ).fetchall():
            for option, candidacies, outcomes in conn.execute(
                """select o.id, array(select y.id from candidacy y where y.option_id = o.id),
                          exists (select from outcome t where t.option_id = o.id)
                   from contest_option o where o.contest_id = %s and not exists (
                     select from result_value v join result_set r on r.k = v.result_set_k
                     where r.contest_id = o.contest_id and v.option_k = o.k)""",
                [contest],
            ).fetchall():
                if option in kept or outcomes:
                    continue
                dropped += 1
                for candidacy in candidacies:
                    cs.add(
                        "entity_retirement",
                        entity_id=candidacy,
                        reason="Umit's rows for this by-election repeated the previous general election's candidates.",
                    )
                if not candidacies:
                    cs.add(
                        "option_drop",
                        option_id=option,
                        contest_id=contest,
                        reason="Umit's rows for this by-election repeated the previous general election's candidates.",
                    )
            for note, text in conn.execute(
                """select n.id, n.body_md from note n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.kind = 'caveat' and c.pipeline = %s""",
                [result_set, SOURCE],
            ).fetchall():
                if (result_set, text) not in noted:
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=result_set,
                        body_md=text,
                        reason="The caveat described Umit's rows, which the winners-only set replaces.",
                    )
        print(f"{len(corrected)} winners-only sets cleared of facts, {dropped} copied candidacies or options dropped")
        return cs.write()
