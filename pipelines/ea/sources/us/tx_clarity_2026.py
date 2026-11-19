import json
import re
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from typing import NamedTuple
from uuid import uuid5
from xml.etree import ElementTree

import duckdb
import httpx

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "us.tx_clarity_2026"
DAY = date(2026, 11, 3)
BASE = "https://results.enr.clarityelections.com/TX/{county}/{election}/"
USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"
# The county canvass of a November election is due on the fourteenth day after it. A county's results site counts as
# canvassed only when it was last updated after that day.
CANVASS = date(2026, 11, 17)
REFERENCE = DATA / "reference" / "us"
UNITS = f"{SOURCE}/units"
COUNTIES = {
    "Bastrop": ("48021", 127476),
    "Denton": ("48121", 127398),
    "El_Paso": ("48141", 127348),
    "Galveston": ("48167", 127478),
    "Gregg": ("48183", 127493),
    "Hood": ("48221", 127400),
    "Kaufman": ("48257", 127485),
    "Lamar": ("48277", 127484),
    "Midland": ("48329", 127299),
    "Rockwall": ("48397", 127294),
    "Rusk": ("48401", 127574),
    "Travis": ("48453", 127492),
    "Williamson": ("48491", 127488),
    "Wood": ("48499", 127355),
}
ZONES = {"CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "EST": -5, "EDT": -4}
# Statewide, federal, legislative, and state judicial contests belong to the Secretary of State's canvass.
OUT_OF_SCOPE = re.compile(
    r"^(?:United States|U\. ?S\.|Governor|Lieutenant Governor|Attorney General|Comptroller|Commissioner of (?:the General"
    r" Land Office|Agriculture)|Railroad Commissioner|(?:Chief )?Justice, (?:Supreme Court|\d+(?:st|nd|rd|th) Court)"
    r"|Judge, Court of Criminal Appeals|State Senator|State Representative|Member, State Board of Education"
    r"|District Judge)",
    re.IGNORECASE,
)
QUESTION = re.compile(r"^(.*?),?\s+Proposition\s+([A-Z])\s*$")
METHOD = (
    "Votes are the county's election results report for the November 3, 2026 election, summed over the ways of "
    "voting the report lists. The county's results site was last updated on {updated}, after the November 17 "
    "canvass deadline. The report declares no winners, so the set holds none."
)
PARTIAL = (
    " The report counts only the {county} County part of this contest's electorate. The contest's other counties "
    "report their parts separately, so these votes are not its total."
)
PRECINCT_METHOD = (
    "Precinct lines from the county's election results report for the contest, summed over the ways of voting. They "
    "add up to the county's total in the county's result set for the contest."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def words(text: str) -> set[str]:
    return {w for w in slug(text).split("-") if w not in {"of", "the", "and", "tx", "2026", "election", "city"}}


def site(county: str) -> str:
    return f"https://results.enr.clarityelections.com/TX/{county}/{COUNTIES[county][1]}/"


def versioned(county: str, version: str, name: str) -> str:
    return f"{site(county)}{version}/{name}"


def fetch() -> None:
    client = httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": USER_AGENT})
    for county in COUNTIES:
        version = client.get(site(county) + "current_ver.txt").raise_for_status().text.strip()
        settings = raw.fetch(SOURCE, versioned(county, version, "json/en/electionsettings.json"), suffix=".json")
        if canvassed(settings.path.read_text()) is not None:
            raw.fetch(SOURCE, versioned(county, version, "reports/detailxml.zip"), suffix=".zip")
        else:
            print(f"{county}: results site not updated after the canvass deadline; detail not fetched")


def updated(text: str) -> datetime:
    found = re.fullmatch(r"(\d+/\d+/\d{4} \d+:\d+:\d+ [AP]M) ([A-Z]{3})", json.loads(text)["websiteupdatedat"].strip())
    if found is None:
        raise ValueError(f"unreadable update stamp {json.loads(text)['websiteupdatedat']!r}")
    stamp = datetime.strptime(found[1], "%m/%d/%Y %I:%M:%S %p")
    return stamp.replace(tzinfo=timezone(timedelta(hours=ZONES[found[2]])))


def canvassed(text: str) -> datetime | None:
    """When the county's results site was last updated, if that is after the canvass deadline."""
    stamp = updated(text)
    return stamp if stamp.astimezone(timezone(timedelta(hours=-6))).date() > CANVASS else None


class Feed(NamedTuple):
    county: str
    fips: str
    version: str
    stamp: datetime
    settings: raw.Artifact
    detail: raw.Artifact


def feeds() -> list[Feed]:
    found = []
    for county, (fips, _) in COUNTIES.items():
        latest = {}
        for url in raw.keys(SOURCE):
            at = re.fullmatch(re.escape(site(county)) + r"(\d+)/json/en/electionsettings\.json", url)
            if at:
                latest[int(at[1])] = url
        if not latest:
            continue
        version = str(max(latest))
        settings = raw.cached(SOURCE, versioned(county, version, "json/en/electionsettings.json"))
        stamp = canvassed(settings.path.read_text())
        if stamp is None:
            continue
        detail = raw.cached(SOURCE, versioned(county, version, "reports/detailxml.zip"))
        found.append(Feed(county, fips, version, stamp, settings, detail))
    return found


class Contest(NamedTuple):
    key: str
    election: str
    name: str
    default: bool
    options: dict[str, str]  # normalized label -> option id
    kinds: dict[str, str]  # option id -> kind
    sets: list[tuple[str, str, bool]]  # existing result sets: id, stage, default


class Reference(NamedTuple):
    county_units: dict[str, tuple[str, str, str]]  # fips -> unit id, area id, polity id
    contests: dict[str, Contest]  # contest id -> contest


def reference() -> Reference:
    if not (REFERENCE / "contest.parquet").exists():
        raise FileNotFoundError(f"{REFERENCE} is missing the `us` reference partition")
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in (
            "reporting_unit",
            "contest",
            "contest_option",
            "candidacy",
            "external_id",
            "result_set",
        )
    }
    con = duckdb.connect()
    counties = {
        row[0]: (row[1], row[2], row[3])
        for row in con.execute(
            f"select code, id, area_id, polity_id from '{path['reporting_unit']}' where scheme_code = 'us.county' "
            f"and code = any(?)",
            [[fips for fips, _ in COUNTIES.values()]],
        ).fetchall()
    }
    con.execute(
        f"""create table k as select c.id, c.election_id, c.name, x.value k from '{path["contest"]}' c
            join '{path["external_id"]}' x on x.entity_id = c.id and x.scheme = 'us.contest'
            where x.value like '{DAY}:TX:%'"""
    )
    contests: dict[str, Contest] = {}
    sets: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for contest, result, stage, default in con.execute(
        f"select contest_id, id, stage, is_default from '{path['result_set']}' where contest_id in (select id from k)"
    ).fetchall():
        sets[contest].append((result, stage, default))
    options: dict[str, dict[str, str]] = defaultdict(dict)
    kinds: dict[str, dict[str, str]] = defaultdict(dict)
    for contest, option, kind, label, name in con.execute(
        f"""select o.contest_id, o.id, o.kind, o.ballot_label, ca.ballot_name from '{path["contest_option"]}' o
            left join '{path["candidacy"]}' ca on ca.option_id = o.id
            where o.contest_id in (select id from k)"""
    ).fetchall():
        options[contest][norm(name or label or "")] = option
        kinds[contest][option] = kind
    for contest, election, name, value in con.execute("select id, election_id, name, k from k").fetchall():
        contests[contest] = Contest(
            value, election, name or "", False, options[contest], kinds[contest], sets.get(contest, [])
        )
    return Reference(counties, contests)


class Choice(NamedTuple):
    label: str
    total: int
    precincts: dict[str, int]


class Race(NamedTuple):
    title: str
    seats: int
    question: bool
    choices: list[Choice]
    reporting: tuple[int, int]


def races(feed: Feed) -> list[Race]:
    with zipfile.ZipFile(feed.detail.path) as archive:
        root = ElementTree.fromstring(archive.read("detail.xml"))
    found = []
    for node in root.iter("Contest"):
        choices = []
        for choice in node.iter("Choice"):
            lines: dict[str, int] = defaultdict(int)
            counted = True
            for kind in choice.findall("VoteType"):
                for precinct in kind.findall("Precinct"):
                    if not (precinct.get("votes") or "").isdigit():
                        counted = False
                        break
                    lines[precinct.get("name") or ""] += int(precinct.get("votes") or 0)
            total = choice.get("totalVotes") or ""
            if not total.isdigit():
                raise ValueError(f"{feed.county} {node.get('text')}: unreadable total {total!r}")
            choices.append(
                Choice(re.sub(r"\s+", " ", choice.get("text") or "").strip(), int(total), lines if counted else {})
            )
        found.append(
            Race(
                re.sub(r"\s+", " ", node.get("text") or "").strip(),
                int(node.get("voteFor") or 1),
                node.get("isQuestion") == "true",
                choices,
                (int(node.get("precinctsReported") or 0), int(node.get("precinctsReporting") or 0)),
            )
        )
    return found


class Match(NamedTuple):
    contest: str
    options: dict[str, str | None]  # choice label -> option id (None: a write-in line the contest lacks)


def match(race: Race, fips: str, ref: Reference) -> Match | str:
    """The existing contest for a race and each choice's option, or why there is none."""
    if race.question:
        found = QUESTION.fullmatch(race.title)
        if found is None:
            return "its title names no proposition"
        entity = norm(re.sub(r"^City of ", "", found[1]))
        hits = []
        for contest, held in ref.contests.items():
            at = re.fullmatch(rf"{DAY.year} (.+?): Proposition ([A-Z]):.*", held.name)
            if at and norm(at[1]) == entity and at[2] == found[2] and ":TX:" in held.key:
                hits.append(contest)
        if len(hits) != 1:
            return f"{len(hits)} existing measures carry its entity and proposition letter"
        options = ref.contests[hits[0]].options
        wanted = {"for": "yes", "against": "no", "yes": "yes", "no": "no"}
        try:
            return Match(hits[0], {c.label: options[wanted[c.label.lower()]] for c in race.choices})
        except KeyError:
            return "its answers don't map onto the measure's yes and no options"
    names = {norm(c.label) for c in race.choices if norm(c.label) not in ("writein", "writeins")}
    if not names:
        return "it lists no named candidate"
    scored = []
    for contest, held in ref.contests.items():
        if not names <= set(held.options):
            continue
        shared = words(race.title) & (words(held.name) | words(held.key.split(":", 2)[2]))
        scored.append((len(names) / len(held.options), len(shared), local(fips, held.key), contest))
    if not scored:
        return "no existing contest holds its candidates"
    scored.sort(reverse=True)
    ties = [hit for hit in scored if hit[:3] == scored[0][:3]]
    if len(ties) != 1:
        return f"{len(ties)} existing contests tie on its candidates and title"
    contest = ties[0][3]
    held = ref.contests[contest]
    out: dict[str, str | None] = {}
    for choice in race.choices:
        label = norm(choice.label)
        if label in ("writein", "writeins"):
            writes = [o for o, kind in held.kinds.items() if kind == "write_in"]
            out[choice.label] = writes[0] if writes else None
        else:
            out[choice.label] = held.options[label]
    return Match(contest, out)


def local(county_fips: str, contest_key: str) -> bool:
    """Whether the contest's electorate lies inside the county, which its key names."""
    segment = contest_key.split(":")[2]
    return segment == county_fips or segment.startswith(f"{county_fips}-")


def precinct_code(fips: str, name: str) -> str:
    return f"{DAY}:TX:{fips}::{name}"


def source_id(county: str) -> str:
    return key("source", county)


class Built(NamedTuple):
    packages: list[Package]
    gaps: dict[str, list[str]]  # county -> contest titles with no existing contest, with the reason


def build() -> Built:
    ref = reference()
    run = feeds()
    gaps: dict[str, list[str]] = defaultdict(list)
    hits: dict[str, list[tuple[Feed, Race, Match]]] = defaultdict(list)
    for feed in run:
        for race in races(feed):
            if OUT_OF_SCOPE.match(race.title):
                continue
            found = match(race, feed.fips, ref)
            if isinstance(found, str):
                gaps[feed.county].append(f"{race.title}: {found}")
                continue
            hits[ref.contests[found.contest].election].append((feed, race, found))
    minted: dict[str, tuple[Feed, str]] = {}
    for items in hits.values():
        for feed, race, _ in items:
            for choice in race.choices:
                for name in choice.precincts:
                    minted.setdefault(precinct_code(feed.fips, name), (feed, name))
    units = Package(
        UNITS,
        SOURCE,
        "County results sources and the precinct reporting units of November 3, 2026 that the archive did not yet hold",
    )
    for feed in run:
        src = source_id(feed.county)
        units.add(
            "source",
            id=src,
            kind="official_body",
            publisher=f"{feed.county.replace('_', ' ')} County, Texas",
            title="Election results, November 3, 2026",
            url=site(feed.county),
            license=None,
            attribution=f"{feed.county.replace('_', ' ')} County, Texas",
        )
        units.artifact(feed.detail, src)
        units.artifact(feed.settings, src)
    for code, (feed, name) in sorted(minted.items()):
        unit, area, polity = ref.county_units[feed.fips]
        units.external |= {unit, area, polity, "us.precinct"}
        units.add(
            "reporting_unit",
            id=key("unit", code),
            polity_id=polity,
            scheme_code="us.precinct",
            code=code,
            parent_id=unit,
            area_relation="within",
            area_id=area,
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
    built = [units]
    for election, items in sorted(hits.items()):
        built.append(election_package(election, items, ref))
    return Built(built, dict(gaps))


def package_key(election: str) -> str:
    return f"{SOURCE}/{election[:8]}"


def election_package(election: str, items: list[tuple[Feed, Race, Match]], ref: Reference) -> Package:
    p = Package(
        package_key(election),
        SOURCE,
        "Counties' canvassed results for contests of one existing November 3, 2026 election",
        depends=[UNITS],
    )
    p.external.add(election)
    seen: set[tuple[str, str]] = set()
    for feed, race, found in items:
        held = ref.contests[found.contest]
        if (feed.county, found.contest) in seen:
            raise ValueError(f"{feed.county}: {race.title} duplicates another race in contest {held.key}")
        seen.add((feed.county, found.contest))
        p.uses(feed.detail)
        p.uses(feed.settings)
        p.external.add(found.contest)
        county_unit, _, _ = ref.county_units[feed.fips]
        p.external.add(county_unit)
        options: dict[str, str] = {}
        for choice in race.choices:
            option = found.options[choice.label]
            if option is None:
                option = key("option", feed.county, found.contest, "write-in")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=found.contest,
                    kind="write_in",
                    ballot_label="Write-in",
                )
            else:
                p.external.add(option)
            options[choice.label] = option
        whole = local(feed.fips, held.key)
        certified_default = any(stage == "certified" and default for _, stage, default in held.sets)
        default = whole and not certified_default
        county_set = key("result_set", feed.county, found.contest, "county")
        method = METHOD.format(updated=f"{feed.stamp:%B} {feed.stamp.day}, {feed.stamp.year}")
        if not whole:
            method += PARTIAL.format(county=feed.county.replace("_", " "))
        for name, is_default, text, unit_kind in (
            (county_set, default, method, "county"),
            (key("result_set", feed.county, found.contest, "precinct"), False, PRECINCT_METHOD, "precinct"),
        ):
            if unit_kind == "precinct" and not any(c.precincts for c in race.choices):
                continue
            p.add(
                "result_set",
                id=name,
                election_id=election,
                contest_id=found.contest,
                stage="certified",
                kind="counted",
                source_id=source_id(feed.county),
                status="active",
                is_default=is_default,
                as_of=feed.stamp.astimezone(UTC),
            )
            p.add("note", id=key("note", name), entity_id=name, kind="methodology", body_md=text, lang="en")
            for choice in race.choices:
                if unit_kind == "county":
                    p.add(
                        "result_value",
                        result_set_id=name,
                        unit_id=county_unit,
                        option_id=options[choice.label],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=choice.total,
                    )
                    if whole:
                        p.add(
                            "declared",
                            result_set_id=name,
                            option_id=options[choice.label],
                            measure=1,
                            value=choice.total,
                        )
                    continue
                for precinct, votes in sorted(choice.precincts.items()):
                    p.add(
                        "result_value",
                        result_set_id=name,
                        unit_id=key("unit", precinct_code(feed.fips, precinct)),
                        option_id=options[choice.label],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        for choice in race.choices:
            if choice.precincts and sum(choice.precincts.values()) != choice.total:
                raise ValueError(f"{feed.county} {race.title}: {choice.label} precincts don't sum to the county total")
    return p


def parse() -> None:
    built = build()
    for item in built.packages:
        item.write()
    for county, titles in sorted(built.gaps.items()):
        print(f"{county}: {len(titles)} contests without a match")
        for title in titles:
            print("   ", title)


def packages() -> list[str]:
    return [item.key for item in build().packages]


def emit() -> None:
    package.run("load", packages())
