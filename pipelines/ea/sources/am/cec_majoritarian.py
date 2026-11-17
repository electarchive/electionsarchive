"""Armenia's National Assembly district contests, 2007 to 2013, from the Central Electoral Commission (elections.am).

The Assembly elected part of its members in 41 single-member districts beside the nationwide party lists until the
2016 Electoral Code ended them. The Commission publishes each district contest, and the by-elections that filled
vacated district seats, on one page that a form post returns: the page prints the district's totals and, for each
polling station the district's picker lists, that station's totals and each candidate's votes. Each page is kept in
the raw cache as the site served it, compressed.
"""

import functools
import gzip
import html
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid5

import httpx

from ea import package, raw

SOURCE = "am.cec_majoritarian"
SITE = "https://www.elections.am"
PAGE = f"{SITE}/Elections/ParliamentaryMajoritarian"
PICKER = f"{SITE}/Select/GetSubDistrictsByDistrict"
ARMENIA = str(uuid5(package.NAMESPACE, "polity\x1fAM"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARM"))
WORKERS = 6


@dataclass(frozen=True)
class Election:
    day: date
    id: int  # the Commission's election number
    general: bool  # the whole set of districts, rather than one district's by-election


ELECTIONS = [
    Election(date(2007, 5, 12), 83, True),
    Election(date(2007, 8, 26), 125, False),
    Election(date(2008, 8, 24), 22109, False),
    Election(date(2009, 12, 6), 23642, False),
    Election(date(2009, 12, 6), 23643, False),
    Election(date(2010, 1, 10), 23659, False),
    Election(date(2012, 5, 6), 24103, True),
    Election(date(2012, 12, 2), 25542, False),
    Election(date(2012, 12, 2), 25543, False),
    Election(date(2013, 9, 29), 25565, False),
]


def election_url(e: Election) -> str:
    return f"{PAGE}#electionId={e.id}"


def district_url(e: Election, district: int) -> str:
    return f"{PAGE}#electionId={e.id}&districtId={district}"


def station_url(e: Election, district: int, station: str) -> str:
    return f"{PAGE}#electionId={e.id}&districtId={district}&subDistrictId={station}"


def picker_url(e: Election, district: int) -> str:
    return f"{PICKER}?electionId={e.id}&districtId={district}&subDistrictId="


# Fetching. The pages come from a form post that carries an antiforgery token, so each is stored under a URL that names
# its election, district, and station, and its bytes are kept gzipped.


class Session:
    def __init__(self) -> None:
        self.client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)
        form = self.client.get(PAGE).text
        found = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', form)
        if found is None:
            raise LookupError(f"{PAGE} has no form")
        self.token = found.group(1)

    def post(self, e: Election, district: int | None, station: str = "") -> bytes:
        data = {
            "__RequestVerificationToken": self.token,
            "Culture": "hy",
            "Input.ElectionTypeId": "3",
            "Input.ElectionId": str(e.id),
            "Current.ElectionId": str(e.id),
            "Input.RegionCode": "",
            "Input.CommunityCode": "",
            "Input.DistrictId": "" if district is None else str(district),
            "Input.SubDistrictId": station,
            "Input.Tab1": "",
            "Input.Tab2": "",
            "Input.Tab3": "",
            "Input.Tab4": "",
            "Input.Tab5": "active-tab",
        }
        for attempt in range(5):
            try:
                response = self.client.post(PAGE, data=data)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError:
                if attempt == 4:
                    raise
                self.client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)
                form = self.client.get(PAGE).text
                found = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', form)
                if found is not None:
                    self.token = found.group(1)
        raise AssertionError("unreachable")


def store(session: Session, url: str, e: Election, district: int | None, station: str = "") -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    return raw.store(
        SOURCE,
        url,
        gzip.compress(session.post(e, district, station), mtime=0),
        suffix=".html.gz",
        retrieved_at=datetime.now(UTC),
        media_type="application/gzip",
    )


def text(artifact: raw.Artifact) -> str:
    return gzip.decompress(artifact.path.read_bytes()).decode("utf-8")


def districts_of(e: Election) -> list[int]:
    page = text(raw.cached(SOURCE, election_url(e)))
    start = page.index('id="Input_DistrictId"')
    found = re.findall(r'<option[^>]*value="(\d+)"', page[start : page.index("</select>", start)])
    return [int(value) for value in found]


def stations_of(e: Election, district: int) -> list[tuple[str, str]]:
    """The district's picker entries as (code, the Commission's id for the station)."""
    rows = json.loads(raw.cached(SOURCE, picker_url(e, district)).path.read_text(encoding="utf-8"))
    return [(row["text"], row["value"]) for row in rows if row["value"]]


def export_url(e: Election) -> str:
    return f"{SITE}/File/ElectionResult?electionId={e.id}"


def fetch() -> None:
    for e in ELECTIONS:
        if e.general:
            raw.fetch(SOURCE, export_url(e), suffix=".xlsx")
    session = Session()
    for e in ELECTIONS:
        store(session, election_url(e), e, None)
        for district in districts_of(e):
            raw.fetch(SOURCE, picker_url(e, district), suffix=".json")
    jobs: list[tuple[Election, int, str]] = []
    for e in ELECTIONS:
        for district in districts_of(e):
            jobs.append((e, district, ""))
            jobs.extend((e, district, value) for _, value in stations_of(e, district))
    local = threading.local()

    def one(index: int) -> None:
        e, district, station = jobs[index]
        url = district_url(e, district) if not station else station_url(e, district, station)
        if not hasattr(local, "session"):
            local.session = Session()
        try:
            store(local.session, url, e, district, station)
        except httpx.HTTPError as failure:
            print(f"{SOURCE}: {url} failed: {failure}", flush=True)

    with ThreadPoolExecutor(WORKERS) as pool:
        for done, _ in enumerate(pool.map(one, range(len(jobs))), 1):
            if done % 250 == 0:
                print(f"{SOURCE}: {done} of {len(jobs)} pages", flush=True)
    print(f"{SOURCE}: {len(jobs)} pages", flush=True)


# Reading the pages.


def clean(value: object) -> str:
    return " ".join(html.unescape(str(value if value is not None else "")).replace("\xa0", " ").split())


def number(value: str) -> int | None:
    digits = value.replace(" ", "").replace(",", "")
    return int(digits) if digits.isdigit() else None


LABELS = {
    "registered": "Ընտրողների ընդհանուր թիվը",
    "voters": "Գրանցված և քվեաթերթիկ ստացած քվեարկության մասնակիցների թիվը",
    "invalid": "Անվավեր քվեաթերթիկների թիվը",
    "valid": "Վավեր քվեաթերթիկների թիվը",
    "against": "Թեկնածուին դեմ քվեարկված քվեաթերթիկների թիվը",
}
INDEPENDENT = "ԱՆԿՈՒՍԱԿՑԱԿԱՆ"


@dataclass
class Page:
    totals: dict[str, int]  # registered, voters, invalid, valid, and against, where the page prints them
    candidates: dict[str, int]  # the name as printed with its votes, in printed order
    nominations: list[tuple[str, str]]  # the nominations tab: a candidate's name and the nominating party


def cells(row: str) -> list[str]:
    return [clean(re.sub(r"<[^>]+>", " ", cell)) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]


def read(page: str) -> Page:
    start = page.index('class="election-result"')
    totals: dict[str, int] = {}
    candidates: dict[str, int] = {}
    inner = re.search(r'<table class="election-result-cand-table">(.*?)</table>', page[start:], re.DOTALL)
    if inner is not None:
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", inner.group(1), re.DOTALL):
            name, votes = cells(row)[:2]
            candidates[name] = number(votes) or 0
    body = page[start : start + 12000]
    if inner is not None:
        body = body.replace(inner.group(0), "")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
        found = cells(row)
        if len(found) < 2 or (value := number(found[1])) is None:
            continue
        for name, label in LABELS.items():
            if found[0] == label or (name == "voters" and found[0].startswith(label)):
                totals[name] = value
    nominations: list[tuple[str, str]] = []
    if 'id="Tab4"' in page and 'id="Tab5"' in page:
        tab = page[page.index('id="Tab4"') : page.index('id="Tab5"')]
        nominations = [
            (clean(name), clean(party))
            for name, party in re.findall(r"<h3[^>]*>(.*?)</h3>.*?<span>(.*?)</span>", tab, re.DOTALL)
        ]
    return Page(totals, candidates, nominations)


def code(text: str) -> str:
    """A polling station's number as '<district>/<station>' without leading zeros."""
    return "/".join(str(int(part)) for part in text.split("/"))


@dataclass
class District:
    number: int
    page: Page
    stations: list[tuple[str, Page]]  # code and page; empty where the Commission reports the district as one table


def blank(page: Page) -> bool:
    return not any(page.totals.values()) and not any(page.candidates.values())


@functools.cache
def district(e: Election, number: int) -> District:
    whole = read(text(raw.cached(SOURCE, district_url(e, number))))
    stations = [
        (code(label), read(text(raw.cached(SOURCE, station_url(e, number, value)))))
        for label, value in stations_of(e, number)
    ]
    present = [(label, page) for label, page in stations if not blank(page)]
    if len(stations) > 1 and len(present) == 1 and present[0][1] == whole:
        return District(number, whole, [])
    return District(number, whole, present)


def tokens(name: str) -> set[str]:
    return set(name.replace(".", " ").casefold().split())


def nominated(page: Page) -> dict[str, str | None]:
    """The party the nominations tab names for each candidate, where one name on the tab fits that candidate alone."""
    claims = {
        i: [c for c in page.candidates if tokens(name) <= tokens(c)] for i, (name, _) in enumerate(page.nominations)
    }
    out: dict[str, str | None] = {}
    for candidate in page.candidates:
        hits = [i for i, claimed in claims.items() if candidate in claimed]
        out[candidate] = page.nominations[hits[0]][1] if len(hits) == 1 and len(claims[hits[0]]) == 1 else None
    return out


# Identifiers.


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SANGSAD = key("body", "national_assembly")
SYSTEM = key("electoral_system", "plurality")
SCHEME = "am.na_district"
PRECINCT = "am.precinct"
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
SINCE, UNTIL = date(1995, 7, 5), date(2016, 6, 1)

METHOD = (
    "Counted by the Central Electoral Commission and printed on its page for the district contest: one table for "
    "the district and one for each polling station its picker lists. Registered voters are the electors in the "
    "main and supplementary lists, voters are the participants who signed the roll, and invalid ballots are as "
    "printed. The page prints no winner, so the candidate with the most votes is stored as elected."
)


def area(number: int) -> str:
    return key("area", SCHEME, number)


def fold(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).upper()
    return " ".join(re.sub(r"[«»\"“”'`´՛]", " ", name).split())


def party(name: str) -> str:
    return key("party", fold(name))


def numbers() -> list[int]:
    return sorted({d for e in ELECTIONS for d in districts_of(e)})


def parties() -> dict[str, str]:
    """Every nominating party named on a page, by its id, with its first printed name."""
    out: dict[str, str] = {}
    for e in ELECTIONS:
        for number in districts_of(e):
            for _, name in district(e, number).page.nominations:
                if name != INDEPENDENT:
                    out.setdefault(party(name), name)
    return dict(sorted(out.items()))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(day) for day in sorted({e.day for e in ELECTIONS}))]


def package_key(day: date) -> str:
    kind = "general" if any(e.general and e.day == day for e in ELECTIONS) else "by"
    return f"{SOURCE}/{kind}-{day}"


def parse() -> None:
    geo().write()
    registry().write()
    for day in sorted({e.day for e in ELECTIONS}):
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> package.Package:
    p = package.Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 41 single-member districts of Armenia's National Assembly, as the Central Electoral Commission numbers them",
    )
    p.external |= {ARMENIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Հայաստանի Հանրապետության կենտրոնական ընտրական հանձնաժողով",
        title="National Assembly majoritarian elections and by-elections: results by district and polling station",
        url=PAGE,
        license="Official results of the Republic of Armenia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Central Electoral Commission of the Republic of Armenia, elections.am",
    )
    p.add(
        "scheme", code=SCHEME, polity_id=ARMENIA, kind="electoral_district", rank=2, noun="district", code_system=None
    )
    for number in numbers():
        id = area(number)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=str(number))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"Թիվ {number} ընտրատարածք", "hy", "official")
        p.name(id, f"Electoral district {number}", "en", "english", False)
    p.add(
        "note",
        id=key("note", "districts"),
        entity_id=area(1),
        kind="methodology",
        body_md="The Commission numbers the districts from 1 to 41 and publishes no boundaries for them. The archive "
        "holds one area for each number and does not compare the districts' territory between elections.",
        lang="en",
    )
    return p


def registry() -> package.Package:
    p = package.Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The National Assembly, its plurality system for district seats, and the parties that nominated district "
        "candidates",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(ARMENIA)
    p.add("body", id=SANGSAD, polity_id=ARMENIA, kind="unicameral")
    p.name(SANGSAD, "National Assembly", "en", "official")
    p.name(SANGSAD, "Ազգային ժողով", "hy", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ARMENIA,
        valid_from=SINCE,
        valid_to=UNTIL,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each of the 41 districts elected one member of the National Assembly, beside the nationwide party "
        "lists, until the Electoral Code of 2016 ended district seats. The candidate with the most votes is elected.",
        lang="en",
    )
    for id, name in parties().items():
        p.add("party", id=id, kind="party", polity_id=ARMENIA)
        p.add("external_id", entity_id=id, scheme="am.cec_majoritarian.party", value=fold(name))
        p.name(id, name, "hy", "official")
    return p


class Facts:
    def __init__(self, p: package.Package, result_set: str) -> None:
        self.p = p
        self.result_set = result_set

    def put(self, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add(
            "result_value",
            result_set_id=self.result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )


ACCOUNTING = {"registered": REGISTERED, "voters": VOTERS, "invalid": INVALID, "valid": VALID}


def election(day: date) -> package.Package:
    held = [e for e in ELECTIONS if e.day == day]
    general = held[0].general
    p = package.Package(
        package_key(day),
        SOURCE,
        f"The {day:%-d %B %Y} National Assembly "
        + ("district contests" if general else "district by-election" + ("s" if len(held) > 1 else ""))
        + " by polling station from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA, COUNTRY, PRECINCT}
    election_id = key("election", day)
    if general:
        names = (
            f"{day.year} Armenian parliamentary election, district contests",
            f"Ազգային ժողովի ընտրություններ, {day.year}, մեծամասնական ընտրակարգ",
        )
        path = f"am/{day}-majoritarian"
    else:
        names = (
            f"{day:%-d %B %Y} Armenian National Assembly by-election" + ("s" if len(held) > 1 else ""),
            f"Ազգային ժողովի մեծամասնական արտահերթ ընտրություններ, {day.day}.{day.month:02d}.{day.year}",
        )
        path = f"am/{day}-majoritarian-by-election"
    p.add(
        "election",
        id=election_id,
        polity_id=ARMENIA,
        kind="general" if general else "by_election",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election_id, names[0], "en", "official")
    p.name(election_id, names[1], "hy", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    unprinted: list[str] = []
    differ: list[str] = []
    over: list[str] = []
    for e in held:
        p.uses(raw.cached(SOURCE, election_url(e)))
        if e.general:
            p.artifact(raw.cached(SOURCE, export_url(e)), SOURCE_ID)
        for number in districts_of(e):
            p.uses(raw.cached(SOURCE, picker_url(e, number)))
            for _, value in stations_of(e, number):
                p.uses(raw.cached(SOURCE, station_url(e, number, value)))
            found = contest(p, e, election_id, path, names, district(e, number))
            unprinted += found[0]
            differ += found[1]
            over += found[2]
    if unprinted or differ:
        reason = (
            "The Commission's own tables print a count of valid ballots that differs from the votes for the candidates"
        )
        if unprinted:
            reason += (
                f"; in {len(unprinted)} tables of a district where one candidate ran, the page prints no votes against "
                "the candidate, and the difference is those votes"
            )
        if differ:
            reason += f"; the tables that differ otherwise are {', '.join(differ)}"
        p.accept("accounting.votes_valid", reason)
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own tables print more participants than registered voters at " + ", ".join(over),
        )
    return p


def contest(
    p: package.Package, e: Election, election_id: str, path: str, names: tuple[str, str], d: District
) -> tuple[list[str], list[str], list[str]]:
    """Stage the district's contest; return the units whose printed figures break an accounting check, as the check
    sees them: valid ballots that differ from the votes with no votes against printed, valid ballots that differ
    from the votes otherwise, and more participants than registered voters."""
    tag = f"{e.id}:{d.number}"
    p.artifact(raw.cached(SOURCE, district_url(e, d.number)), SOURCE_ID)
    contest_id = key("contest", tag)
    p.add(
        "contest",
        id=contest_id,
        election_id=election_id,
        kind="body_seats",
        body_id=SANGSAD,
        district_area_id=area(d.number),
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest_id, f"{names[0]}, district {d.number}", "en", "official")
    p.name(contest_id, f"{names[1]}, թիվ {d.number} ընտրատարածք", "hy", "native", False)
    p.add("slug", path=f"{path}/district-{d.number}", entity_id=contest_id, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest_id,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD
    if not d.stations:
        method += (
            " The Commission reports this district as one table, with no figures for its polling stations, so the "
            "figures are stored for the district as a whole."
        )
    if "valid" not in d.page.totals:
        method += " The page prints no count of valid ballots."
    if "against" in d.page.totals or len(d.page.candidates) == 1:
        method += (
            " Where one candidate ran, the page counts votes for the candidate and, where it prints them, votes "
            "against the candidate; the votes against are stored as their own option."
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    parties_of = nominated(d.page)
    options: dict[str, str] = {}
    for position, name in enumerate(d.page.candidates, 1):
        option = options[name] = key("option", tag, name)
        nominator = parties_of[name]
        solo = nominator == INDEPENDENT
        member = party(nominator) if nominator not in (None, INDEPENDENT) else None
        person = key("person", tag, name)
        p.add("person", id=person)
        p.name(person, name, "hy", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="candidate",
            party_id=member,
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=member,
            is_independent=True if solo else None,
        )
    against = None
    if "against" in d.page.totals:
        against = key("option", tag, "against")
        p.add(
            "contest_option",
            id=against,
            contest_id=contest_id,
            kind="none_of_the_above",
            ballot_position=len(options) + 1,
            ballot_label="Թեկնածուին դեմ",
        )
        p.name(against, "Against the candidate", "en", "official")
    facts = Facts(p, result_set)
    units: list[tuple[str, str, Page]] = []
    if d.stations:
        for label, page in d.stations:
            unit = key("unit", f"{e.day}:{label}")
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ARMENIA,
                scheme_code=PRECINCT,
                code=f"{e.day}:maj:{label}",
                area_relation="within",
                area_id=area(d.number),
                kind="standard",
            )
            units.append((label, unit, page))
    else:
        unit = key("unit", f"{e.day}:district:{d.number}")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ARMENIA,
            scheme_code=SCHEME,
            code=f"{e.day}:maj:{d.number}",
            area_relation="equals",
            area_id=area(d.number),
            kind="standard",
        )
        units.append((str(d.number), unit, d.page))
    for _, unit, page in units:
        for name, measure in ACCOUNTING.items():
            facts.put(unit, None, measure, page.totals.get(name))
        for name, option in options.items():
            facts.put(unit, option, VOTES, page.candidates.get(name))
        if against is not None:
            facts.put(unit, against, VOTES, page.totals.get("against"))
    unprinted: list[str] = []
    differ: list[str] = []
    over: list[str] = []
    for label, _, page in units:
        where = f"{e.day}, district {d.number}" + ("" if not d.stations else f", station {label}")
        if "valid" in page.totals and page.totals["valid"] != sum(page.candidates.values()) + page.totals.get(
            "against", 0
        ):
            (unprinted if len(page.candidates) == 1 and "against" not in page.totals else differ).append(where)
        if "registered" in page.totals and page.totals.get("voters", 0) > page.totals["registered"]:
            over.append(f"{where} ({page.totals['voters']} participants, {page.totals['registered']} registered)")
    for name, measure in ACCOUNTING.items():
        if name in d.page.totals:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=d.page.totals[name])
    for name, option in options.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=d.page.candidates[name])
    if against is not None:
        p.add("declared", result_set_id=result_set, option_id=against, measure=VOTES, value=d.page.totals["against"])
    ranked = sorted(d.page.candidates.items(), key=lambda item: -item[1])
    if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) and ranked[0][1] > d.page.totals.get("against", 0):
        p.add("outcome", result_set_id=result_set, option_id=options[ranked[0][0]], kind="elected", seats=1)
        for name, _ in ranked[1:]:
            p.add("outcome", result_set_id=result_set, option_id=options[name], kind="lost", seats=0)
    return unprinted, differ, over
