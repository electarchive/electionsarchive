"""Malta's Electoral Commission: every general, European Parliament, local council, community, and referendum result.

The commission publishes one results page per election type. Each page lists its elections, and each election opens
into a summary, one set of results per district or locality, and the candidates elected. Results are single
transferable vote counts, so every table carries each candidate's running total at the end of every count.
"""

import functools
import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "mt.electoral"
BASE = "https://electoral.gov.mt"
INBOX = DATA / "inbox" / "manual" / "mt"
SECTION = re.compile(r"<!-- SOURCE: (.*?) -->")
STAT = re.compile(
    r'<span class="electionstatname">(.*?)</span>\s*(.*?)(?=<span class="electionstatname">|</div>\s*</div>\s*</div>)',
    re.DOTALL,
)


def text(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>|<style.*?</style>", "", fragment, flags=re.DOTALL)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def number(value: str) -> int | None:
    digits = re.sub(r"[^\d-]", "", value)
    return int(digits) if re.fullmatch(r"-?\d+", digits) else None


@dataclass
class Candidate:
    id: str
    name: str
    party: str
    rounds: dict[int, int]


@dataclass
class Zone:
    id: str
    label: str
    figures: dict[str, int] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    transferred: dict[int, int] = field(default_factory=dict)
    totals: dict[int, int] = field(default_factory=dict)
    elected: list[tuple[str, str, str, int | None, int | None, str]] = field(default_factory=list)
    counts: int = 0


@dataclass
class Election:
    id: str
    kind: str
    year: int
    date: str
    figures: dict[str, int]
    answers: dict[str, int]
    question: str
    link: str
    zones: list[Zone]
    options: list[tuple[str, str]]


def fetch() -> None:
    """Load the files saved from the commission's site into the raw cache; the site does not answer scripts."""
    index = json.loads((INBOX / "index.json").read_text())
    for url, entry in sorted(index.items()):
        content = (INBOX / entry["file"]).read_bytes()
        raw.store(
            SOURCE,
            entry.get("source", url),
            content,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def sections(content: str) -> list[tuple[str, str]]:
    parts = SECTION.split(content)
    return list(zip(parts[1::2], parts[2::2], strict=True))


def stats(fragment: str) -> dict[str, tuple[int | None, str]]:
    """Each labeled figure in a summary: its count and the text printed beside it."""
    found: dict[str, tuple[int | None, str]] = {}
    marks = list(re.finditer(r'<span class="electionstatname">(.*?)</span>', fragment, re.DOTALL))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(fragment)
        shown = re.findall(
            r'<(?:div|span) class="electionstatvalue">(.*?)</(?:div|span)>', fragment[mark.end() : end], re.DOTALL
        )
        if shown:
            found.setdefault(text(mark.group(1)), (number(text(shown[0])), text(shown[0])))
    return found


def table(fragment: str) -> tuple[list[Candidate], dict[int, int], dict[int, int], int]:
    fragment = re.sub(r"<script.*?</script>", "", fragment, flags=re.DOTALL)
    body = fragment[fragment.index("<tbody>") :]
    head = fragment[: fragment.index("<tbody>")]
    counts = len(re.findall(r'<th class="text-right">\s*Count \d+', head))
    candidates: list[Candidate] = []
    transferred: dict[int, int] = {}
    totals: dict[int, int] = {}
    for row in re.findall(r"<tr.*?</tr>", body, flags=re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
        label = text(cells[0])
        values = [text(c) for c in cells[3:]]
        # Cells alternate between a running total and the change into the next count.
        running = values[0 : 2 * counts - 1 : 2]
        rounds = {n: number(v) for n, v in enumerate(running, 1) if v not in ("", "...") and number(v) is not None}
        if label == "Totals":
            totals = {n: v for n, v in rounds.items() if v is not None}
        elif label.startswith("Non-Transferrable"):
            transferred = {n: v for n, v in rounds.items() if v is not None}
        else:
            ident = re.search(r'<div id="(\w+)"', cells[0])
            candidates.append(
                Candidate(
                    ident.group(1) if ident else "",
                    label,
                    text(cells[1]),
                    {n: v for n, v in rounds.items() if v is not None},
                )
            )
    return candidates, transferred, totals, counts


def elected(fragment: str) -> list[tuple[str, str, str, int | None, int | None, str]]:
    """Each candidate on an elected page: id, name, party, votes, the count elected on (if any), and the remark."""
    found: list[tuple[str, str, str, int | None, int | None, str]] = []
    for block in re.findall(r'<div class="candidate col.*?<p class="sub remarks">.*?</p>', fragment, re.DOTALL):
        ident = re.search(r"CandidateId=(\w+)", block)
        if ident is None:
            continue
        on = re.search(r'isElectedCount">\s*Elected on Count (\d+)', block)
        votes = re.search(r"<p>([\d,]+)</p>\s*<p>VOTES</p>", block)
        name = re.search(r'<p class="candidatename">(.*?)</p>', block, re.DOTALL)
        party = re.search(r'<p class="sub">(.*?)</p>', block, re.DOTALL)
        remark = re.search(r'<p class="sub remarks">(.*?)</p>', block, re.DOTALL)
        found.append(
            (
                ident.group(1),
                text(name.group(1)) if name else "",
                text(party.group(1)) if party else "",
                number(votes.group(1)) if votes else None,
                int(on.group(1)) if on else None,
                text(remark.group(1)) if remark else "",
            )
        )
    return found


TYPES = {"1": "general", "3": "local", "4": "european", "5": "community", "6": "referendum"}


def load(content: str) -> Election:
    """Read one saved election: its summary, then each district's summary, counts, and elected candidates."""
    parts = sections(content)
    head = parts[0][1]
    ident = re.search(r"ElectionID=(\d+)", parts[0][0])
    kind = re.search(r"ElectionType: '(\d)'", content)
    if not (ident and kind):
        raise ValueError(f"no election in {parts[0][0]}")
    figures = {name: value for name, (value, _) in stats(head).items() if value is not None}
    shown = stats(head)
    day = re.fullmatch(r"(\d\d)/(\d\d)/(\d{4})", shown["Date"][1])
    if day is None:
        raise ValueError(f"unreadable date {shown['Date'][1]}")
    title = re.search(r"title: \{ text: '(.*?)' \}", head)
    link = re.search(r'class="candidates-link" href="([^"]+)"', head)
    answers = {name: int(votes) for name, votes in re.findall(r"\{ color: '\w+', name: '(\w+)', y: (\d+) \}", head)}
    labels = {z: text(label) for z, label in re.findall(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', head)}
    zones: dict[str, Zone] = {}
    for url, body in parts[1:]:
        zone = re.search(r"(?:SelectedZone|zoneId)=(\d+)", url)
        if zone is None:
            continue
        found = zones.setdefault(zone.group(1), Zone(zone.group(1), labels.get(zone.group(1), "")))
        if "ZoneCriteriaChanged" in url:
            found.figures = {name: value for name, (value, _) in stats(body).items() if value is not None}
        elif "GetResultsTableByIds" in url:
            found.candidates, found.transferred, found.totals, found.counts = table(body)
        elif "ElectedZoneCandidates" in url:
            found.elected = elected(body)
    return Election(
        ident.group(1),
        TYPES[kind.group(1)],
        int(day.group(3)),
        f"{day.group(3)}-{day.group(2)}-{day.group(1)}",
        figures,
        answers,
        text(title.group(1)) if title else "",
        link.group(1) if link else "",
        [zones[z] for z in zones],
        [],
    )


def _read() -> list[tuple[str, Election]]:
    """Every saved election with the raw-cache key of its file, oldest first."""
    found: list[tuple[str, Election]] = []
    for key in sorted(raw.keys(SOURCE)):
        if "ElectionCriteriaChanged" not in key and not key.startswith("browser://"):
            continue
        content = raw.cached(SOURCE, key).path.read_text(encoding="utf-8")
        found.append((key, load(content)))
    return sorted(found, key=lambda item: (item[1].date, item[1].kind, item[1].id))


def elections() -> list[tuple[str, Election]]:
    return list(_elections())


MALTA = str(uuid5(package.NAMESPACE, "polity\x1fMT"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMLT"))
REGISTERED, CAST, VALID, INVALID, VOTES, EXHAUSTED, QUOTA = 2, 3, 4, 5, 1, 21, 26


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


COMMISSION = key("source", "commission")
HOUSE = key("body", "house-of-representatives")
COUNCILS = key("body", "local-councils")
COMMITTEES = key("body", "community-committees")
STV = key("electoral_system", "stv")
REFERENDUM = key("electoral_system", "referendum")

NAMES = {
    "general": "general election",
    "european": "European Parliament election",
    "local": "local council elections",
    "community": "community committee elections",
    "referendum": "referendum",
}
SLUGS = {key_: key_ for key_ in NAMES}
KINDS = {
    "general": "general",
    "european": "general",
    "local": "local",
    "community": "local",
    "referendum": "referendum",
}
INDEPENDENT = {"kandidat indipendenti", "independent candidate"}
ALIASES = {
    "malta labour party": "partit laburista",
    "alternattiva demokratika - the green party": "alternattiva demokratika",
    "ad+pd": "ad + pd",
}
BLOCS = {"ad + pd"}
COUNT_METHOD = (
    "Counted by the Electoral Commission by single transferable vote, with every ballot of a {place} counted "
    "together. Each candidate's running total at the end of every count is stored under that count, so the first "
    "count holds the first preferences and the last holds the final tallies. Votes the count could no longer "
    "transfer are stored as non-transferable votes for each count. Registered voters, ballots cast, valid votes, and "
    "invalid ballots are the figures printed in the {place}'s summary, and the valid total is also the sum of the "
    "candidates' first-count totals plus the first count's non-transferable votes. The quota is the one the "
    "commission prints."
)
TACIT_METHOD = (
    "The commission records that no election was held in this {place}, because the candidates who stood were all "
    "that the seats could take. The candidates it lists as elected are stored without votes."
)
BY = {"general": "by district", "local": "by locality", "community": "by community"}
PLACES = {"general": "district", "european": "country", "local": "locality", "community": "community"}


def party_key(label: str) -> str | None:
    """The source-local key of a party printed on a ballot, or nothing for an independent candidate."""
    folded = re.sub(r"\s+", " ", label).strip().lower()
    if folded in INDEPENDENT:
        return None
    return ALIASES.get(folded, folded)


def party(label: str) -> str | None:
    found = party_key(label)
    return None if found is None else key("party", found)


def title(label: str) -> str:
    return re.sub(r"\s+", " ", label.title()).replace("`", "'").replace("'S", "'s")


def code(election: Election, kind: str | None = None) -> str:
    return f"{kind or SLUGS[election.kind]}-{election.date}"


def packages() -> list[str]:
    return [
        "mt.electoral/geo",
        "mt.electoral/registry",
        *(f"mt.electoral/{code(election)}" for _, election in elections()),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for found_key, election in elections():
        build(found_key, election).write()


def emit() -> None:
    package.run("load", packages())


@functools.cache
def _elections() -> tuple[tuple[str, Election], ...]:
    return tuple(_read())


def contested(election: Election) -> list[Zone]:
    return [zone for zone in election.zones if zone.candidates]


def listed(election: Election) -> list[tuple[Zone, bool]]:
    """Each district or locality the election holds a contest for, flagged when no vote was taken."""
    found = []
    for zone in election.zones:
        figures = zone.figures or election.figures
        if zone.candidates:
            found.append((zone, False))
        elif figures.get("Seats") and zone.elected:
            found.append((zone, True))
    return found


def area(scheme: str, name: str) -> str:
    return key("area", scheme, changeset.slug(name))


def scheme_of(election: Election) -> str | None:
    return {"general": "mt.district", "local": "mt.locality", "community": "mt.community"}.get(election.kind)


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=COMMISSION,
        kind="official_body",
        publisher="Electoral Commission of Malta",
        title="Election results published by the Electoral Commission of Malta",
        url=f"{BASE}/ElectionResults/Index",
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Commission of Malta",
    )


def geo() -> Package:
    p = Package(
        "mt.electoral/geo",
        SOURCE,
        "Malta's electoral districts, local council localities, and community committee areas, named as the Electoral "
        "Commission prints them in its results",
    )
    p.external |= {TERRITORY, MALTA}
    p.add("scheme", code="mt.district", polity_id=MALTA, kind="electoral_district", rank=1, noun="electoral district")
    p.add("scheme", code="mt.locality", polity_id=MALTA, kind="administrative", rank=1, noun="locality")
    p.add("scheme", code="mt.community", polity_id=MALTA, kind="administrative", rank=2, noun="community")
    p.add("scheme", code="mt.count", polity_id=MALTA, kind="precinct", rank=3, noun="counting area")
    seen: set[tuple[str, str]] = set()
    for _, election in elections():
        for zone in election.zones:
            scheme = scheme_of(election)
            if scheme is None or (scheme, zone.label) in seen:
                continue
            seen.add((scheme, zone.label))
            id = area(scheme, zone.label)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=changeset.slug(zone.label))
            parent = TERRITORY
            if scheme == "mt.community":
                parent = area("mt.locality", zone.label.split(", ", 1)[1])
            p.add("area_parent", area_id=id, parent_id=parent)
            p.name(id, title(zone.label), "mt", "official")
    return p


def registry() -> Package:
    p = Package(
        "mt.electoral/registry",
        SOURCE,
        "Malta's parties, House of Representatives, local councils, community committees, and electoral systems from "
        "the Electoral Commission's results",
        depends=["mt.electoral/geo"],
    )
    p.external |= {TERRITORY, MALTA}
    source_row(p)
    for name in sorted(raw.keys(SOURCE)):
        p.artifact(raw.cached(SOURCE, name), COMMISSION)
    for id, kind, english, maltese in (
        (HOUSE, "unicameral", "House of Representatives of Malta", "Kamra tad-Deputati"),
        (COUNCILS, "council", "Local councils of Malta", "Kunsilli Lokali"),
        (COMMITTEES, "council", "Community committees of Malta", "Kumitati Komunitarji"),
    ):
        p.add("body", id=id, polity_id=MALTA, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, maltese, "mt", "native", False)
    p.add(
        "electoral_system",
        id=STV,
        polity_id=MALTA,
        rules={"formula": "stv", "quota": "droop", "surplus": "gregory"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", STV),
        entity_id=STV,
        kind="methodology",
        body_md=(
            "Malta elects its House of Representatives, its members of the European Parliament, its local councils, "
            "and its community committees by single transferable vote with a Droop quota. Voters rank candidates, a "
            "candidate who reaches the quota is elected, and a surplus or an eliminated candidate's ballots pass to "
            "the next preference. The commission's tables show the running totals after each count, and the rules "
            "here record the quota and the transfer of surpluses by their fractional value."
        ),
        lang="en",
    )
    p.add("electoral_system", id=REFERENDUM, polity_id=MALTA, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", REFERENDUM),
        entity_id=REFERENDUM,
        kind="methodology",
        body_md="Voters answer a yes or no question put to the whole electorate, or to the electorate it names.",
        lang="en",
    )
    labels: dict[str, dict[str, int]] = {}
    for _, election in elections():
        for zone in election.zones:
            for who in zone.candidates:
                labels.setdefault(who.party, {}).setdefault(who.party, 0)
                labels[who.party][who.party] += 1
            for entry in zone.elected:
                labels.setdefault(entry[2], {}).setdefault(entry[2], 0)
    names: dict[str, set[str]] = {}
    for label in labels:
        found = party_key(label)
        if found is not None:
            names.setdefault(found, set()).add(re.sub(r"\s+", " ", label).strip())
    for found, variants in sorted(names.items()):
        id = key("party", found)
        p.add("party", id=id, kind="bloc" if found in BLOCS else "party", polity_id=MALTA)
        p.add("external_id", entity_id=id, scheme="mt.electoral.party", value=found)
        main = min(variants, key=lambda v: (v.lower() != found, v.lower() in ALIASES, v))
        for variant in sorted(variants):
            lang = "en" if variant.lower() in ALIASES or variant == "Malta Labour Party" else "mt"
            p.name(id, variant, lang, "official", variant == main)
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, count: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=count,
        flags=0,
        value=value,
    )


def figures_of(election: Election, zone: Zone) -> dict[str, int]:
    return {**election.figures, **zone.figures} if election.kind == "european" else zone.figures


def verify(election: Election, zone: Zone, figures: dict[str, int]) -> None:
    """Refuse a table whose counts disagree with the summary the commission prints beside it."""
    where = f"{election.date} {zone.label}"
    if zone.totals.get(1) != figures["Valid Votes"]:
        raise ValueError(f"{where}: the table totals {zone.totals.get(1)}, the summary {figures['Valid Votes']}")
    for count in range(1, zone.counts + 1):
        held = sum(who.rounds.get(count, 0) for who in zone.candidates) + zone.transferred.get(count, 0)
        if held != figures["Valid Votes"]:
            raise ValueError(f"{where}: count {count} holds {held} of {figures['Valid Votes']} valid votes")
    if figures["Valid Votes"] + figures["Invalid Votes"] != figures["Votes Cast"]:
        raise ValueError(f"{where}: valid and invalid ballots do not add up to the ballots cast")
    for _, _, _, votes, on, _ in zone.elected:
        if on is not None and votes is None:
            raise ValueError(f"{where}: an elected candidate has no votes")


def build(found_key: str, election: Election) -> Package:
    year = election.year
    siblings = [e for _, e in elections() if e.kind == election.kind and e.year == year]
    when = date.fromisoformat(election.date)
    noun = NAMES[election.kind]
    suffix = f" ({when.day} {when:%B})" if len(siblings) > 1 else ""
    p = Package(
        f"mt.electoral/{code(election)}",
        SOURCE,
        f"The {when:%-d %B %Y} Maltese {noun}, {BY.get(election.kind, 'counted nationally')}",
        depends=["mt.electoral/geo", "mt.electoral/registry"],
    )
    p.external |= {MALTA, TERRITORY}
    p.uses(raw.cached(SOURCE, found_key))
    id = key("election", election.id)
    p.add(
        "election",
        id=id,
        polity_id=MALTA,
        kind=KINDS[election.kind],
        date_from=when if election.year > 1900 else date(year, 1, 1),
        date_to=when if election.year > 1900 else date(year, 12, 31),
        date_precision="day" if election.year > 1900 else "year",
        original_text=f"{when:%d/%m/%Y}" if election.year < 1900 else None,
        status="held",
    )
    english = {
        "general": f"{year} Maltese general election",
        "european": f"{year} European Parliament election in Malta",
        "local": f"{year} Maltese local council elections",
        "community": f"{year} Maltese community committee elections",
        "referendum": f"{year} Maltese referendum",
    }[election.kind]
    p.name(id, english + suffix, "en", "official")
    path = f"mt/{election.date}-{SLUGS[election.kind]}"
    p.add("slug", path=path, entity_id=id, is_current=True)
    if election.kind == "referendum":
        referendum(p, election, id, path)
        return p
    scheme = scheme_of(election)
    body = {"general": HOUSE, "local": COUNCILS, "community": COMMITTEES}.get(election.kind)
    method = COUNT_METHOD.format(place=PLACES[election.kind] if scheme else "count")
    people: set[str] = set()
    for zone, tacit in listed(election):
        figures = figures_of(election, zone)
        label = "Malta" if election.kind == "european" else title(zone.label)
        contest = key("contest", election.id, zone.id)
        area_id = TERRITORY if scheme is None else area(scheme, zone.label)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=body,
            district_area_id=area_id,
            magnitude=figures["Seats"],
            round_number=1,
            electoral_system_id=STV,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, label, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(label)}", entity_id=contest, is_current=True)
        result_set = key("result_set", election.id, zone.id)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="tacit" if tacit else "counted",
            source_id=COMMISSION,
            status="active",
            is_default=True,
            completeness=None if tacit else 1.0,
        )
        remarks = [f"{entry[1]}: {entry[5]}" for entry in zone.elected if entry[5]]
        text_ = TACIT_METHOD.format(place=PLACES[election.kind]) if tacit else method
        negative = [(count, held) for count, held in sorted(zone.transferred.items()) if held < 0]
        if negative:
            text_ += (
                " The commission prints negative non-transferable totals at "
                + ", ".join(f"count {count} ({held:,})" for count, held in negative)
                + ", which come from rounding the transfers of surpluses; they are not stored."
            )
        if remarks:
            text_ += " Remarks the commission prints beside its list of members: " + "; ".join(remarks) + "."
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=text_, lang="en")
        if tacit:
            for ident, name, label_, _, on, remark in zone.elected:
                if remark.lower().startswith("co-opted"):
                    continue
                candidate(p, election, zone, ident, name, label_, contest, people, None)
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=key("option", election.id, zone.id, ident),
                    kind="elected",
                )
            continue
        verify(election, zone, figures)
        unit = key("unit", election.id, zone.id)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MALTA,
            scheme_code="mt.count",
            code=f"{election.date}:{SLUGS[election.kind]}:{changeset.slug(label)}",
            area_relation="equals",
            area_id=area_id,
            kind="standard",
        )
        p.name(unit, label, "en", "official")
        for measure, name in (
            (REGISTERED, "Registered Voters"),
            (CAST, "Votes Cast"),
            (VALID, "Valid Votes"),
            (INVALID, "Invalid Votes"),
        ):
            put(p, result_set, unit, None, measure, 0, zone.totals[1] if measure == VALID else figures[name])
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=figures[name])
        if figures.get("Quota"):
            put(p, result_set, unit, None, QUOTA, 0, figures["Quota"])
        winners = {entry[0] for entry in zone.elected if entry[4] is not None}
        for who in zone.candidates:
            option = candidate(p, election, zone, who.id, who.name, who.party, contest, people, who)
            for count, value in sorted(who.rounds.items()):
                put(p, result_set, unit, option, VOTES, count, value)
            last = max(who.rounds, default=0)
            kind = "elected" if who.id in winners else "eliminated" if last < zone.counts else "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
        for count in range(1, zone.counts + 1):
            if zone.transferred.get(count, 0) >= 0:
                put(p, result_set, unit, None, EXHAUSTED, count, zone.transferred.get(count, 0))
    return p


def candidate(
    p: Package,
    election: Election,
    zone: Zone,
    ident: str,
    name: str,
    label: str,
    contest: str,
    people: set[str],
    who: Candidate | None,
) -> str:
    option = key("option", election.id, zone.id, ident)
    member = party(label)
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=member,
        ballot_label=name,
    )
    person = key("person", election.id, ident)
    if person not in people:
        people.add(person)
        p.add("person", id=person)
        p.name(person, name, "mt", "official")
    p.add(
        "candidacy",
        id=key("candidacy", election.id, zone.id, ident),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=name,
        member_of_party_id=member,
        is_independent=member is None,
    )
    return option


def referendum(p: Package, election: Election, id: str, path: str) -> None:
    contest = key("contest", election.id)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="ballot_question",
        district_area_id=TERRITORY,
        round_number=1,
        electoral_system_id=REFERENDUM,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, election.question, "en", "official")
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    result_set = key("result_set", election.id)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=COMMISSION,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    text_ = (
        "Counted nationally by the Electoral Commission and published as one summary for the whole vote. Registered "
        "voters, ballots cast, valid votes, and invalid ballots are the figures the summary prints."
    )
    if election.year == 1870:
        text_ += " The commission prints 1 January 1870 as the date; the day of the vote is not given."
    if election.year == 1973:
        text_ += " Only the electorate of Gozo was asked."
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=text_, lang="en")
    unit = key("unit", election.id)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MALTA,
        scheme_code="mt.count",
        code=f"{election.date}:referendum",
        area_relation="equals",
        area_id=TERRITORY,
        kind="standard",
    )
    p.name(unit, "Malta", "en", "official")
    figures = election.figures
    for measure, name in (
        (REGISTERED, "Registered Voters"),
        (CAST, "Votes Cast"),
        (VALID, "Valid Votes"),
        (INVALID, "Invalid Votes"),
    ):
        put(p, result_set, unit, None, measure, 0, figures[name])
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=figures[name])
    if election.answers.get("Yes", 0) + election.answers.get("No", 0) != figures["Valid Votes"]:
        raise ValueError(f"{election.date}: the answers do not add up to the valid votes")
    for position, (name, label) in enumerate((("Yes", "Yes"), ("No", "No")), 1):
        option = key("option", election.id, name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=label,
        )
        put(p, result_set, unit, option, VOTES, 0, election.answers[name])
        favour = figures["Votes In Favour" if name == "Yes" else "Votes Against"]
        if favour != election.answers[name]:
            raise ValueError(f"{election.date}: the {name} votes differ between the chart and the summary")
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=favour)
