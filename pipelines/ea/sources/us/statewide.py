import html
import io
import json
import re
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import httpx
import polars as pl

from ea import names, raw
from ea.changeset import Changeset, slug
from ea.names import initials
from ea.sources.us.county_changes import ct_regions
from ea.sources.us.elstats import (
    FORMER,
    GAZETTEER,
    MA_PUBLISHER,
    MASSACHUSETTS,
    SEARCH,
    SUFFIXES,
    TENANTS,
    TITLE,
    plain,
)
from ea.store import DATA, connect

SOURCE = "us.statewide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DECADES = range(1780, 2030, 10)
OFFICES = [
    "Governor",
    "Attorney General",
    "Secretary of",
    "Treasurer",
    "Auditor",
    "Comptroller",
    "Controller",
    "Superintendent",
    "Supreme Court",
    "Court of Appeals",
    "Appellate",
    "Public Lands",
    "Regent",
    "Board of Education",
    "Corporation Commission",
    "Mine Inspector",
]
CIVERA = {state: tenant for state, tenant in TENANTS.items() if state != "NY"}
MA_OFFICES = {
    3: "Governor",
    4: "Lieutenant Governor",
    12: "Attorney General",
    45: "Secretary of the Commonwealth",
    53: "Treasurer",
    90: "Auditor",
}
MERGED = {
    ("VA", "nansemond"): "51800",
    ("VA", "princessanne"): "51810",
    ("VA", "norfolk"): "51550",
    ("VA", "southnorfolkcity"): "51550",
    ("VA", "warwick"): "51700",
    ("VA", "warwickcity"): "51700",
    ("VA", "elizabethcity"): "51650",
    ("VT", "essexjunction"): "50007",
    ("VT", "unorganizedterritory"): "50009",
    ("CT", "saybrook"): "09007",
    ("CT", "huntington"): "09001",
    ("CT", "chatham"): "09007",
    ("CT", "southlyme"): "09011",
    ("CT", "northharington"): "09005",
    ("VT", "salem"): "50019",
}
OFFICE_NAMES = {
    "State Treasurer": "Treasurer",
    "State Auditor": "Auditor",
    "State Controller": "Controller",
    "Supreme Court": "Justice of the Supreme Court",
    "Supreme Court Justice": "Justice of the Supreme Court",
    "Justice of The Supreme Court": "Justice of the Supreme Court",
    "Appellate Court Judge": "Judge of the Court of Appeals",
    "State Railroad Commissioner": "Railroad Commissioner",
    "State Board of Education": "Member of the State Board of Education",
}
LABELS = {
    "Democratic": "democratic-party",
    "Democrat": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
    "Constitution": "constitution-party",
    "Prohibition": "prohibition-party",
    "Natural Law": "natural-law-party",
    "Reform": "reform-party",
    "Socialist Workers": "socialist-workers-party",
    "Progressive": "VT:progressive-party",
    "Liberty Union": "VT:liberty-union-party",
    "Working Families": "{state}:working-families-party",
    "Green-Rainbow": "MA:green-rainbow-party",
}
INDEPENDENT = {
    "Independent",
    "Unaffiliated",
    "Petitioning Candidate",
    "Unenrolled",
    "Independent Candidate",
    "No Party",
}
NO_PARTY = {"Nonpartisan", "Write In", "Write-In", "Past labels not allowed in current law", "None"}
TICKET = r" and | & "
LEGISLATURE = {"Governor", "Lieutenant Governor", "Treasurer"}
RETIRED_OFFICE = (
    'This statewide "County Treasurer" office came from a county race that the Idaho archive filed as a statewide '
    "contest. The race now belongs to its county's treasurer office."
)
VERMONT = (
    "No candidate won a majority of the popular vote. Under Vermont's constitution, the General Assembly then chose "
    "the winner from the top three candidates."
)
MA_ROW = re.compile(
    r'id="election-id-(\d+)"(?:(?!id="election-id-).)*?<td[^>]*>(\d{4})</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>((?:(?!id="election-id-).)*?)(?:</table>|(?=<tr id="election-id-)|\Z)',
    re.DOTALL,
)


def url(tenant: str, decade: int) -> str:
    search = {
        "global": {"years": {"from": decade, "to": decade + 9}},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office} for office in OFFICES]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return SEARCH.format(tenant=tenant) + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def ma_search(office: int) -> str:
    return f"{MASSACHUSETTS}search/year_from:1970/year_to:2026/office_id:{office}"


def ma_download(election: str) -> str:
    return f"{MASSACHUSETTS}download/{election}/precincts_include:0/"


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTEER, suffix=".zip")
    lock = threading.Lock()

    def get(link: str, suffix: str) -> None:
        try:
            raw.cached(SOURCE, link)
            return
        except LookupError:
            pass
        response = httpx.get(link, headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=1800)
        response.raise_for_status()
        with lock:
            raw.store(
                SOURCE,
                link,
                response.content,
                suffix=suffix,
                retrieved_at=datetime.now(UTC),
                media_type=response.headers.get("content-type", "text/csv"),
            )
        print(f"{len(response.content):>12} {link[:90]}", flush=True)

    links = [ma_search(office) for office in MA_OFFICES]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda link: get(link, ".html"), links))
    downloads = [
        ma_download(election)
        for office in MA_OFFICES
        for election, *_ in MA_ROW.findall(raw.cached(SOURCE, ma_search(office)).path.read_text())
    ]
    with ThreadPoolExecutor(8) as pool:
        list(
            pool.map(
                lambda link: get(link, ".csv"),
                [*downloads, *(url(tenant, decade) for tenant, _, _ in CIVERA.values() for decade in DECADES)],
            )
        )


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{plain(text)}city"] = code
            names.setdefault(plain(text), code)
    with zipfile.ZipFile(raw.cached(SOURCE, GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in([*CIVERA, "MA"]))
    towns: dict[str, dict[str, str]] = {}
    for state, geoid, name in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        names = towns.setdefault(state, {})
        names.setdefault(plain(name), geoid[:5])
        names.setdefault(plain(re.sub(SUFFIXES, "", name.lower())), geoid[:5])
    for (state, name), code in {**FORMER, **MERGED}.items():
        counties.setdefault(state, {})[name] = code
        towns.setdefault(state, {})[name] = code

    frames = []
    for state, (tenant, _, _) in CIVERA.items():
        for decade in DECADES:
            frame = pl.read_csv(raw.cached(SOURCE, url(tenant, decade)).path, infer_schema=False)
            if frame.height == 0:
                continue
            division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
            frames.append(
                frame.filter(
                    (pl.col("district_type") == "State")
                    & pl.col("office_name").is_not_null()
                    & (pl.col("office_name").str.strip_chars() != "Governor")
                    & ~pl.col("office_name").str.contains(r"(?i)U\.S\.|United States|President|Congress")
                    & pl.col("division_type").is_in(["City/Town", "Locality", "County"])
                )
                .with_columns(
                    channels=(pl.col("vote_channel").is_null().any().over("contest_id", "division_id")).not_()
                )
                .filter(pl.col("vote_channel").is_null() | pl.col("channels"))
                .select(
                    state=pl.lit(state),
                    contest=pl.concat_str(pl.lit(f"{state}:"), "contest_id"),
                    year=pl.col("election_date").str.slice(0, 4).cast(pl.Int32),
                    day=pl.col("election_date").str.slice(0, 10).str.to_date(),
                    stage=pl.col("election_type"),
                    scope=pl.col("primary_party"),
                    office=pl.col("office_name").str.strip_chars(),
                    modifier=pl.col("office_modifier").str.strip_chars(),
                    seats=pl.col("number_seats").cast(pl.Int16, strict=False),
                    person=pl.concat_str(pl.lit(f"{state}:"), "candidate_id"),
                    candidate=pl.col("candidate_name").str.replace_all(r"\s+", " ").str.strip_chars(),
                    party=pl.col("candidate_party_name").str.strip_chars(),
                    winner=pl.lit(None, pl.Boolean),
                    code=pl.when(pl.col("division_type") == "City/Town")
                    .then(
                        pl.coalesce(
                            division.map_elements(plain, return_dtype=pl.String).replace_strict(
                                towns.get(state, {}), default=None
                            ),
                            division.str.to_lowercase()
                            .str.replace_all(SUFFIXES, "")
                            .map_elements(plain, return_dtype=pl.String)
                            .replace_strict(towns.get(state, {}), default=None),
                        )
                    )
                    .otherwise(
                        division.str.to_lowercase()
                        .str.replace_all(r"saint ", "st ")
                        .str.replace_all("&", "and")
                        .str.replace_all(r"\b(county|parish|borough)\b", "")
                        .str.replace_all(r"[^a-z]", "")
                        .replace_strict(counties[state], default=None)
                    ),
                    division=pl.concat_str(pl.lit(f"{state}:"), division),
                    votes=pl.col("votes").cast(pl.Float64, strict=False).round().cast(pl.Int64),
                )
            )
    for office, office_name in MA_OFFICES.items():
        for election, year, _, _, stage, body in MA_ROW.findall(raw.cached(SOURCE, ma_search(office)).path.read_text()):
            listed = {
                html.unescape(name).strip(): (html.unescape(party).strip() or None, "is_winner" in classes)
                for classes, name, party in re.findall(
                    r'<tr class="([^"]*)">\s*<td class="candidate"><div class="name">(?:<a[^>]*>)?([^<]*).*?<div class="party">([^<]*)</div>',
                    body,
                    re.DOTALL,
                )
            }
            table = pl.read_csv(
                raw.cached(SOURCE, ma_download(election)).path, infer_schema=False, skip_rows_after_header=1
            )
            primary = stage.removesuffix(" Primary") if stage.endswith(" Primary") else None
            frames.append(
                table.filter(pl.col("City/Town").is_not_null() & (pl.col("City/Town") != "TOTALS"))
                .with_columns(
                    pl.col("City/Town")
                    .str.replace(r"^N\. ", "North ")
                    .str.replace(r"^S\. ", "South ")
                    .str.replace(r"^E\. ", "East ")
                    .str.replace(r"^W\. ", "West ")
                )
                .unpivot([column for column in table.columns[3:] if column], index="City/Town", variable_name="name")
                .select(
                    state=pl.lit("MA"),
                    contest=pl.lit(f"MA:{election}"),
                    year=pl.lit(int(year), pl.Int32),
                    day=pl.lit(None, pl.Date),
                    stage=pl.lit("Primary" if primary else "General"),
                    scope=pl.lit(primary),
                    office=pl.lit(office_name),
                    modifier=pl.lit(None, pl.String),
                    seats=pl.lit(1, pl.Int16),
                    person=pl.concat_str(pl.lit("MA:"), pl.col("name").map_elements(slug, return_dtype=pl.String)),
                    candidate=pl.col("name").str.strip_chars(),
                    party=pl.col("name").replace_strict({k: v[0] for k, v in listed.items()}, default=None),
                    winner=pl.col("name").replace_strict(
                        {k: v[1] for k, v in listed.items()}, default=False, return_dtype=pl.Boolean
                    ),
                    code=pl.col("City/Town")
                    .map_elements(plain, return_dtype=pl.String)
                    .replace_strict(towns["MA"], default=None),
                    division=pl.concat_str(pl.lit("MA:"), "City/Town"),
                    votes=pl.col("value").str.replace_all(",", "").cast(pl.Int64, strict=False),
                )
            )
    frame = (
        pl.concat(frames)
        .filter(pl.col("votes").is_not_null())
        .filter(pl.col("votes").filter(pl.col("candidate") != "Unknown Candidate").sum().over("contest") > 0)
    )
    regions = ct_regions()
    frame = frame.with_columns(
        code=pl.when((pl.col("state") == "CT") & (pl.col("year") >= 2022))
        .then(
            pl.col("division")
            .str.slice(3)
            .map_elements(plain, return_dtype=pl.String)
            .replace_strict(regions, default=None)
        )
        .otherwise("code")
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("division").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40).to_dicts())
    upper = pl.col("candidate").fill_null("").str.to_uppercase()
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.filter(pl.col("code").is_not_null())
        .with_columns(
            measure=pl.when(upper.str.contains(r"^(UNDERVOTES?|BLANKS?|BLANK VOTES)$"))
            .then(6)
            .when(upper.str.contains(r"^(OVERVOTES?|VOID|BLANK/VOID.*)$"))
            .then(5)
            .when(upper.str.contains(r"^(TOTAL|REGISTERED VOTERS)"))
            .then(0)
            .otherwise(1)
            .cast(pl.Int16),
            pooled=upper.str.contains(r"^(WRITE-?INS?|SCATTERING|ALL OTHERS|SCATTERED)$"),
        )
        .filter(pl.col("measure") > 0)
        .with_columns(
            person=pl.when(pl.col("pooled") | (pl.col("measure") != 1)).then(None).otherwise("person"),
            candidate=pl.when(pl.col("pooled"))
            .then(pl.lit("Write-ins"))
            .when(pl.col("measure") != 1)
            .then(None)
            .otherwise("candidate"),
            party=pl.when(pl.col("measure") == 1).then("party"),
        )
        .group_by(
            "state",
            "contest",
            "year",
            "day",
            "stage",
            "scope",
            "office",
            "modifier",
            "seats",
            "person",
            "candidate",
            "party",
            "code",
            "measure",
        )
        .agg(pl.col("winner").any(), pl.col("votes").sum())
        .sort("state", "contest", "code")
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide executive and judicial elections by county from state election archives, 1900–2026",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources: dict[str, UUID] = {}
        for state, (tenant, publisher, site) in [
            *CIVERA.items(),
            ("MA", ("", MA_PUBLISHER, "https://electionstats.state.ma.us")),
        ]:
            sources[state] = cs.source(publisher, TITLE)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=TITLE,
                    url=site,
                    license="Public record",
                )
            if state == "MA":
                for office in MA_OFFICES:
                    listing = raw.cached(SOURCE, ma_search(office))
                    cs.artifact(listing, sources[state])
                    for election, *_ in MA_ROW.findall(listing.path.read_text()):
                        cs.artifact(raw.cached(SOURCE, ma_download(election)), sources[state])
            else:
                for decade in DECADES:
                    artifact = raw.cached(SOURCE, url(tenant, decade))
                    if artifact.bytes > 1000:
                        cs.artifact(artifact, sources[state])
        gazetteer = cs.source("U.S. Census Bureau", "2020 Gazetteer Files")
        if gazetteer in cs.minted:
            cs.add(
                "source",
                id=gazetteer,
                kind="official_body",
                publisher="U.S. Census Bureau",
                title="2020 Gazetteer Files",
                url="https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html",
                license="Public domain",
            )
        cs.artifact(raw.cached(SOURCE, GAZETTEER), gazetteer)

        nation = cs.polity("US")
        states: dict[str, str] = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        areas = {
            state: cs.area("us.state", code)
            for state, code in conn.execute(
                """select substr(p.iso_code, 4), u.code from reporting_unit u
               join polity p on p.id = u.polity_id where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        elections_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'").fetchall()
        }
        parties_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }
        offices_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'").fetchall()
        }
        existing: dict[str, list[tuple[UUID, set[str], bool]]] = {}
        for key, option, label, kind in conn.execute(
            """select e.value, o.id,
                      coalesce(o.ballot_label, (select string_agg(a.ballot_name, ' ') from candidacy a where a.option_id = o.id)),
                      o.kind = 'write_in' from external_id e
               join contest c on c.id = e.entity_id
               join changeset s on s.id = c.changeset_id and s.pipeline <> %s
               join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               where e.scheme = 'us.contest' and (e.value like '%%:governor%%' or e.value like '%%:MA:%%')""",
            [SOURCE],
        ).fetchall():
            existing.setdefault(key, []).append((option, set(slug(label or "").split("-")), kind))
        slugs: dict[str, UUID | None] = dict(conn.execute("select path, entity_id from slug").fetchall())
        current = dict(conn.execute("select entity_id, path from slug where is_current").fetchall())
        published: dict[UUID, UUID | None] = dict(
            conn.execute(
                """select o.id, coalesce(h.party_id, o.party_id) from contest_option o
                   join changeset c on c.id = o.changeset_id
                   left join lateral (select party_id from contest_option_history h where h.id = o.id
                                      order by h.recorded_from limit 1) h on true
                   where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        kept: dict[UUID, bool] = dict(
            conn.execute(
                """select r.id, r.is_default from result_set r join changeset c on c.id = r.changeset_id
                   join external_id e on e.entity_id = r.id and e.scheme = %s where c.pipeline <> %s or r.is_default""",
                [f"{SOURCE}.result_set", SOURCE],
            ).fetchall()
        )
        mine = {
            id
            for (id,) in conn.execute(
                "select c.id from contest c join changeset s on s.id = c.changeset_id where s.pipeline = %s", [SOURCE]
            ).fetchall()
        }

        def unique(path: str, entity: UUID | None = None) -> str:
            if entity in current and re.fullmatch(re.escape(path) + r"(-\d+)?", current[entity]):
                return current[entity]
            base, index = path, 2
            while path in slugs and slugs[path] != entity:
                path, index = f"{base}-{index}", index + 1
            slugs[path] = entity
            return path

        for key in offices_known:
            if key.startswith("county-"):
                cs.add("entity_retirement", entity_id=cs.key("us.office", key), reason=RETIRED_OFFICE)
        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "two_round", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, two-round, and block systems must exist")

        def party(state: str, year: int, label: str | None) -> tuple[UUID | None, bool]:
            label = (label or "").split("/")[0].strip()
            if label in INDEPENDENT:
                return None, True
            if not label or label in NO_PARTY:
                return None, False
            if label == "Progressive" and state != "VT":
                key = (
                    "progressive-party-1912"
                    if 1912 <= year <= 1918
                    else "progressive-party-1924"
                    if year == 1924
                    else f"{state}:progressive-party"
                )
            elif label == "Republican" and year < 1854:
                key = (
                    "democratic-republican-party"
                    if year < 1825
                    else "national-republican-party"
                    if year < 1834
                    else "whig-party"
                )
            else:
                key = LABELS.get(label, "").format(state=state) or next(
                    (
                        key
                        for key in (f"{state}:{slug(label)}", f"{state}:{slug(label)}-party", f"{slug(label)}-party")
                        if key in parties_known
                    ),
                    f"{state}:{slug(label)}" + ("" if slug(label).endswith("party") else "-party"),
                )
            id = cs.key("us.party", key)
            if key not in parties_known:
                parties_known.add(key)
                cs.add("party", id=id, kind="party", polity_id=cs.polity(f"US-{state}"))
                name(id, label if re.search(r"Party|\bfor\b|Independent", label) else f"{label} Party")
            return id, False

        contests = (
            lines.group_by("contest", maintain_order=True)
            .agg(pl.col("state", "year", "day", "stage", "scope", "office", "modifier", "seats").first())
            .with_columns(
                office=pl.col("office").replace(OFFICE_NAMES),
                kind=pl.when(
                    pl.col("stage").str.contains("(?i)primary") & (pl.col("scope") != "Nonpartisan").fill_null(True)
                )
                .then(pl.lit("primary"))
                .when(pl.col("stage").str.contains("(?i)primary"))
                .then(pl.lit("nonpartisan"))
                .otherwise(pl.lit("general")),
                remainder=pl.col("modifier").fill_null("").str.contains("(?i)vacancy|short term|unexpired|to fill"),
            )
        )
        runoffs: dict[str, str] = {}
        rounds: dict[tuple[str, str, str | None, bool], list[tuple[date, str]]] = {}
        for row in contests.filter(pl.col("kind") == "primary", pl.col("day").is_not_null()).iter_rows(named=True):
            key = (row["state"], row["office"], row["scope"], row["remainder"])
            rounds.setdefault(key, []).append((row["day"], row["contest"]))
        for held in rounds.values():
            held.sort()
            for (before, first), (after, second) in pairwise(held):
                if 0 < (after - before).days <= 70:
                    runoffs[second] = first
        ours: dict[str, UUID] = {}
        days: dict[tuple[str, int, str], str] = {}
        for value in sorted(elections_known):
            parts = value.split(":")
            if len(parts) >= 2 and parts[1] in states and len(parts[0]) == 10:
                kind = parts[2] if len(parts) > 2 else "general"
                if kind in ("general", "primary") and (kind == "general") == (parts[0][5:7] == "11"):
                    days.setdefault((parts[1], int(parts[0][:4]), kind), parts[0])

        elections: dict[str, UUID] = {}

        def election(state: str, year: int, day: date | None, kind: str) -> tuple[UUID, str, str]:
            kind = "primary" if kind == "nonpartisan" else kind
            if day is None:
                datekey = days.get((state, year, kind)) or (
                    str(date(year, 11, 2) + timedelta(days=(1 - date(year, 11, 2).weekday()) % 7))
                    if kind == "general"
                    else f"{year}-09"
                )
            else:
                datekey = str(day)
            key = f"{datekey}:{state}" + ("" if kind == "general" else f":{kind}")
            if key not in elections:
                if kind == "runoff" and f"{datekey}:{state}:primary" in elections_known:
                    primary = cs.key("us.election", f"{datekey}:{state}:primary")
                    if primary != cs.key("us.election", key):
                        cs.merge(primary, cs.key("us.election", key))
                elections[key] = cs.key("us.election", key)
                if key not in elections_known:
                    elections_known.add(key)
                    on = date.fromisoformat(datekey if len(datekey) == 10 else f"{datekey}-01")
                    parent = None
                    if kind == "general" and on.month == 11 and year % 2 == 0:
                        parent = cs.key("us.election", datekey)
                        if datekey not in elections_known:
                            elections_known.add(datekey)
                            cs.add(
                                "election",
                                id=parent,
                                polity_id=nation,
                                kind="general",
                                date_from=on,
                                date_to=on,
                                date_precision="day",
                                status="held",
                            )
                            name(parent, f"{year} United States general election")
                            cs.add("slug", path=unique(f"us/{datekey}-general"), entity_id=parent)
                    cs.add(
                        "election",
                        id=elections[key],
                        polity_id=cs.polity(f"US-{state}"),
                        parent_id=parent,
                        kind=kind,
                        date_from=on,
                        date_to=on,
                        date_precision="day" if len(datekey) == 10 else "month",
                        status="held",
                    )
                    name(elections[key], f"{year} {states[state]} {kind} election")
                    cs.add("slug", path=unique(f"us/{state.lower()}/{datekey}-{kind}"), entity_id=elections[key])
            return elections[key], datekey, kind

        offices: dict[str, UUID] = {}
        persons: dict[str, UUID] = {}
        named: dict[tuple[str, str], str] = {}
        surnames: dict[tuple[str, int, str, str], str] = {}
        for person, state, year, office, candidate in (
            lines.filter(pl.col("person").is_not_null() & ~pl.col("candidate").str.contains(TICKET))
            .group_by("person", "state", "year", "office")
            .agg(pl.col("candidate").last())
            .sort("year")
            .iter_rows()
        ):
            if person not in persons:
                persons[person] = cs.key(f"{SOURCE}.person", person)
                cs.add("person", id=persons[person])
                if persons[person] in cs.minted:
                    name(persons[person], initials(candidate))
            named[(state, candidate)] = person
            surnames[(state, year, office, slug(candidate).split("-")[-1])] = person

        taken: set[str] = set()
        values: list[tuple[str, str, str, int, int]] = []
        folds: dict[UUID, list[UUID]] = {}
        for row in contests.sort("year", "day", "state", "contest", nulls_last=True).iter_rows(named=True):
            state, year, office, modifier, kind = row["state"], row["year"], row["office"], row["modifier"], row["kind"]
            group = lines.filter(pl.col("contest") == row["contest"])
            runoff = row["contest"] in runoffs
            election_id, datekey, election_kind = election(state, year, row["day"], "runoff" if runoff else kind)
            office_slug = slug(office)
            office_key = f"{office_slug}:{state}"
            county = str(group["code"][0]) if office.startswith("County ") else None
            if county is not None:
                election_id = cs.key("us.election", f"{datekey}:{state}:{county}")
                offices[office_key] = cs.key("us.office", f"{state}:{county}:{slug(office.removeprefix('County '))}")
            if office_key not in offices:
                offices[office_key] = cs.key("us.office", office_key)
                if office_key not in offices_known:
                    cs.add(
                        "office",
                        id=offices[office_key],
                        polity_id=cs.polity(f"US-{state}"),
                        kind="governor" if office == "Governor" else "other",
                    )
                    name(
                        offices[office_key],
                        office if states[state] in office else f"{office} of {states[state]}",
                    )
            scope = row["scope"] if kind == "primary" else None
            contest_key = (
                f"{datekey}:{state}:{office_slug}"
                + (f":{slug(scope)}" if scope else "")
                + (":special" if row["remainder"] else "")
            )
            shown = modifier if modifier and modifier not in ("At Large", "Full Term") else None
            if contest_key in taken:
                contest_key += f":{slug(shown)}" if shown else ""
            if contest_key in taken:
                contest_key += f":{row['contest'].split(':')[1]}"
            taken.add(contest_key)
            contest = cs.key("us.contest", contest_key)
            ours[row["contest"]] = contest
            advancing = row["contest"] in runoffs.values()
            reused = contest not in cs.minted and contest not in mine
            seats = row["seats"] or 1
            if not reused:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election_id,
                    kind="office",
                    office_id=offices[office_key],
                    district_area_id=areas[state] if county is None else cs.area("us.county", county),
                    term="remainder" if row["remainder"] else "full",
                    magnitude=seats,
                    round_number=2 if runoff else 1,
                    previous_round_id=ours.get(runoffs[row["contest"]]) if runoff else None,
                    electoral_system_id=systems["two_round"]
                    if kind == "nonpartisan"
                    or runoff
                    or advancing
                    or (state == "VA" and 1953 <= year <= 1969 and scope)
                    else systems["fptp"]
                    if seats == 1
                    else systems["block"],
                    default_result_kind="counted",
                    party_scope_id=party(state, year, scope)[0] if scope else None,
                )
                label = (
                    f"{scope} primary" + (" runoff" if runoff else "")
                    if scope
                    else "special election"
                    if row["remainder"]
                    else "primary"
                    if kind == "primary"
                    else "election"
                )
                if county is not None:
                    (title,) = conn.execute(
                        "select text from name where entity_id = %s and is_preferred", [offices[office_key]]
                    ).fetchone() or (office,)
                    name(contest, f"{year} {title} {label}")
                    cs.add(
                        "slug",
                        path=unique(f"{current[election_id]}/{slug(office.removeprefix('County '))}", contest),
                        entity_id=contest,
                    )
                else:
                    name(contest, f"{year} {states[state]} {office} {label}" + (f" ({shown})" if shown else ""))
                    cs.add(
                        "slug",
                        path=unique(
                            f"us/{state.lower()}/{datekey}-{election_kind}/{office_slug}"
                            + (f"-{slug(scope)}" if scope else "")
                            + ("-special" if row["remainder"] else "")
                            + (f"-{slug(shown)}" if shown and not row["remainder"] else ""),
                            contest,
                        ),
                        entity_id=contest,
                    )
            candidates = group.filter(pl.col("measure") == 1)
            entries = sorted(
                candidates.group_by("person", "candidate", maintain_order=True),
                key=lambda entry: -entry[1]["votes"].sum(),
            )
            single = [
                index
                for index, ((person, candidate), _) in enumerate(entries)
                if person is not None and not re.search(TICKET, str(candidate))
            ]
            folded = (
                {}
                if reused
                else {
                    single[index]: single[survivor]
                    for index, survivor in names.fold([str(entries[index][0][1]) for index in single]).items()
                }
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            previous = None
            regioned = state == "CT" and year >= 2022
            if (folded or regioned) and result_set not in cs.minted:
                previous = result_set
                cs.add(
                    "result_set",
                    id=previous,
                    election_id=election_id,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=sources[state],
                    is_default=False,
                    completeness=None,
                )
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:{'regions' if regioned else 'folded'}")
                for option, outcome, count in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [previous]
                ).fetchall():
                    cs.add("outcome", result_set_id=previous, option_id=option, kind=outcome, seats=count)
                    if regioned:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome, seats=count)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True if regioned else kept.get(previous or result_set, not reused),
                supersedes_id=previous,
                completeness=None,
            )
            totals: list[tuple[UUID, int, bool, bool]] = []
            groups: dict[int, list[int]] = {}
            for index in range(len(entries)):
                groups.setdefault(folded.get(index, index), []).append(index)
            for index, indices in groups.items():
                (person, candidate), _ = entries[index]
                rows = pl.concat(
                    [entries[member][1].with_columns(printed=pl.lit(str(entries[member][0][1]))) for member in indices]
                )
                lines_by_party = rows.group_by("printed", "party", maintain_order=True).agg(
                    pl.col("votes").sum(), pl.col("winner").any()
                )
                resolved = [party(state, year, label) for label in lines_by_party["party"]]
                write_in = person is None or all(label == "Write In" for label in lines_by_party["party"])
                ballot = initials(str(candidate))
                option_key = f"{contest_key}:{person or 'write-ins'}"
                matched = None
                if reused:
                    words = [
                        word
                        for word in slug(re.split(f"/|{TICKET}", ballot)[0]).split("-")
                        if len(word) > 1 and word not in ("jr", "sr", "ii", "iii")
                    ]
                    matched = next(
                        (
                            option
                            for option, texts, is_write_in in existing.get(contest_key, [])
                            if (is_write_in and write_in) or (words and not write_in and words[-1] in texts)
                        ),
                        None,
                    )
                if matched is not None:
                    twin = conn.execute(
                        "select entity_id from external_id where scheme = 'us.option' and value = %s", [option_key]
                    ).fetchone()
                    if twin is not None and twin[0] != matched:
                        cs.merge(twin[0], matched)
                        heads = conn.execute(
                            "select id, option_id from candidacy where option_id in (%s, %s) and role = 'head'",
                            [twin[0], matched],
                        ).fetchall()
                        if len(heads) == 2:
                            (gone,) = (id for id, option in heads if option == twin[0])
                            (kept_head,) = (id for id, option in heads if option == matched)
                            cs.merge(gone, kept_head)
                root = matched or cs.key("us.option", option_key)
                if matched is None:
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="write_in" if write_in else "ticket" if re.search(TICKET, ballot) else "candidate",
                        party_id=(resolved[0][0] if len(resolved) == 1 else None)
                        if year < 1854
                        else published.get(root, resolved[0][0] if len(resolved) == 1 else None),
                        ballot_label=ballot,
                    )
                    members = re.split(TICKET, str(candidate), maxsplit=1) if person is not None else []
                    for member, role, held in zip(
                        members, ("head", "running_mate"), ("Governor", "Lieutenant Governor"), strict=False
                    ):
                        who = (
                            person
                            if len(members) == 1
                            else named.get((state, member))
                            or surnames.get((state, year, held, slug(member).split("-")[-1]))
                            or f"{state}:{slug(member)}"
                        )
                        if who not in persons:
                            persons[who] = cs.key(f"{SOURCE}.person", who)
                            cs.add("person", id=persons[who])
                            if persons[who] in cs.minted:
                                name(persons[who], initials(member))
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{option_key}:{role}"),
                            person_id=persons[who],
                            option_id=root,
                            role=role,
                            ballot_name=initials(member),
                            is_independent=all(independent for _, independent in resolved),
                        )
                targets: list[UUID] = []
                for printed, label, (party_id, _) in zip(
                    lines_by_party["printed"], lines_by_party["party"], resolved, strict=True
                ):
                    if len(resolved) == 1 or reused:
                        targets.append(root)
                        continue
                    target = cs.key(
                        "us.option",
                        f"{option_key}:"
                        + ("" if printed == str(candidate) else f"{slug(printed)}:")
                        + slug(label or "no-party"),
                    )
                    cs.add(
                        "contest_option",
                        id=target,
                        contest_id=contest,
                        parent_option_id=root,
                        kind="write_in" if label == "Write In" else "candidate",
                        party_id=party_id if year < 1854 else published.get(target, party_id),
                        ballot_label=f"{initials(printed)} ({label or 'no party'})",
                    )
                    targets.append(target)
                lines_keys = list(zip(lines_by_party["printed"], lines_by_party["party"], strict=True))
                for member in indices if previous is not None else ():
                    gone_person, gone = entries[member][0]
                    gone_key = f"{contest_key}:{gone_person}"
                    old = cs.key("us.option", gone_key)
                    if member == index or old in cs.minted:
                        continue
                    lost = [(printed, label) for printed, label in lines_keys if printed == gone]
                    for printed, label in lost:
                        cs.merge(
                            old if len(lost) == 1 else cs.key("us.option", f"{gone_key}:{slug(label or 'no-party')}"),
                            targets[lines_keys.index((printed, label))],
                        )
                    if len(lost) > 1:
                        cs.merge(old, root)
                    cs.merge(cs.key("us.candidacy", f"{gone_key}:head"), cs.key("us.candidacy", f"{option_key}:head"))
                    folds.setdefault(cs.key(f"{SOURCE}.person", str(gone_person)), []).append(
                        cs.key(f"{SOURCE}.person", str(person))
                    )
                for (code, printed, label), cells in rows.group_by("code", "printed", "party", maintain_order=True):
                    target = targets[lines_keys.index((printed, label))]
                    if result_set in cs.minted:
                        values.append(
                            (
                                str(result_set),
                                str(cs.unit("us.county", str(code))),
                                str(target),
                                1,
                                int(cells["votes"].sum()),
                            )
                        )
                totals.append((root, int(rows["votes"].sum()), bool(lines_by_party["winner"].any()), person is None))
            for code, measure, votes in (
                group.filter(pl.col("measure") != 1).group_by("code", "measure").agg(pl.col("votes").sum()).iter_rows()
            ):
                if result_set in cs.minted:
                    values.append((str(result_set), str(cs.unit("us.county", str(code))), "", measure, int(votes)))
            if reused:
                continue
            ranked = sorted(
                ((votes, root) for root, votes, _, pooled in totals if not pooled), key=lambda pair: -pair[0]
            )
            total = sum(votes for _, votes, _, _ in totals)
            if advancing:
                winners = []
                for _, root in ranked[:2]:
                    cs.add("outcome", result_set_id=result_set, option_id=root, kind="advanced", seats=None)
            elif state == "MA":
                winners = [root for root, _, declared, _ in totals if declared] or [
                    root
                    for votes, root in ranked[:seats]
                    if votes > 0 and (len(ranked) <= seats or ranked[seats - 1][0] > ranked[seats][0])
                ]
            elif kind == "nonpartisan":
                winners = [root for votes, root in ranked[:1] if votes * 2 > total]
                advanced = [] if winners else [root for _, root in ranked[:2]]
                for root in advanced:
                    cs.add("outcome", result_set_id=result_set, option_id=root, kind="advanced", seats=None)
            else:
                winners = [root for votes, root in ranked[:seats] if votes > 0]
                if len(ranked) > seats and ranked[seats - 1][0] == ranked[seats][0]:
                    winners = []
            if state == "VT" and kind == "general" and office in LEGISLATURE and ranked and ranked[0][0] * 2 <= total:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=VERMONT)
                continue
            for root in winners:
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=root,
                    kind="won" if kind == "primary" else "elected",
                    seats=None if kind == "primary" else 1,
                )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            )
            .group_by("result_set_id", "unit_id", "option_id", "measure")
            .agg(pl.col("value").sum())
            .with_columns(
                option_id=pl.when(pl.col("option_id") == "").then(None).otherwise("option_id"),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        candidacies: dict[UUID, int] = dict(
            conn.execute(
                "select person_id, count(*) from candidacy where person_id = any(%s) group by 1", [list(folds)]
            ).fetchall()
        )
        for duplicate, into in folds.items():
            if len(set(into)) == 1 and candidacies.get(duplicate, 0) == len(into) and duplicate not in cs.minted:
                cs.merge(duplicate, into[0])
        return cs.write()
