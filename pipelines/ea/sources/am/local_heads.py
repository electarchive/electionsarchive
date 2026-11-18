"""Armenia's community head elections from the Central Electoral Commission (elections.am), 2005 to 2026.

The Commission lists every community head poll on one page that only a form post returns. Each poll's page lists its
candidates and, for the whole community or for one polling station chosen by a second form post, the protocol figures
and the votes of each candidate. The region and community pickers name the places.
"""

import functools
import html
import json
import re
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import uuid5

import httpx
import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "am.local_heads"
SITE = "https://www.elections.am"
SECTION = f"{SITE}/Elections/HeadofCommunity"
TYPE = "4"
PAGE = 1000
WORKERS = 8


def clean(text: object) -> str:
    return " ".join(html.unescape(str(text if text is not None else "")).replace("\xa0", " ").split())


def list_url(page: int) -> str:
    return f"{SECTION}#list-page={page}-size={PAGE}"


def poll_url(poll: int, station: str = "") -> str:
    return f"{SECTION}#electionId={poll}" + (f"&subDistrictId={station}" if station else "")


def stations_url(poll: int) -> str:
    return f"{SITE}/Select/GetSubDistricts?electionId={poll}&regionCode=&communityCode=forElection&subDistrictId="


def regions_url() -> str:
    return f"{SITE}/Select/GetRegionsByElectionType?electionTypeId={TYPE}&regionCode="


def communities_url(region: str) -> str:
    return f"{SITE}/Select/GetCommunitiesByElectionType?electionTypeId={TYPE}&regionCode={region}&communityCode="


class Session:
    """The Commission's form, with its antiforgery token, which every listing and poll page is a post of."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.begin()

    def begin(self) -> None:
        self.client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=180)
        form = self.client.get(SECTION).text
        token = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', form)
        if token is None:
            raise LookupError(f"{SECTION} has no form")
        self.token = token.group(1)
        self.started = time.time()

    def post(self, **fields: str) -> httpx.Response:
        refreshed = False
        for attempt in range(5):
            with self.lock:
                client, token, started = self.client, self.token, self.started
            data = {
                "__RequestVerificationToken": token,
                "Culture": "hy",
                "Input.ElectionTypeId": TYPE,
                "Input.ElectionId": "0",
                "Current.ElectionId": "0",
                "Input.RegionCode": "",
                "Input.CommunityCode": "",
                "Input.SubDistrictId": "",
            }
            data.update(fields)
            try:
                response = client.post(SECTION, data=data)
                if response.status_code in (400, 403) and not refreshed:
                    # The form's antiforgery token lapses after a while; take a new one once, and give up if the
                    # Commission still refuses.
                    refreshed = True
                    with self.lock:
                        if self.started == started:
                            self.begin()
                    continue
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
            except httpx.TransportError:
                pass
            time.sleep(2**attempt)
        raise LookupError(f"{SECTION} did not answer {fields}")


def keep(url: str, response: httpx.Response) -> raw.Artifact:
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", "text/html"),
    )


def listing() -> list[tuple[date, int, str]]:
    """Every poll the Commission lists, as (day, poll number, "region - community")."""
    out: list[tuple[date, int, str]] = []
    session: Session | None = None
    page = 1
    while True:
        try:
            text = raw.cached(SOURCE, list_url(page)).path.read_text(encoding="utf-8")
        except LookupError:
            session = session or Session()
            text = keep(
                list_url(page),
                session.post(**{"ElectionPaging.PageIndex": str(page), "ElectionPaging.PageSize": str(PAGE)}),
            ).path.read_text(encoding="utf-8")
        rows = re.findall(r"<td>(\d\d)\.(\d\d)\.(\d{4})</td>\s*<td>(.*?)</td>", text, re.DOTALL)
        for day, month, year, cell in rows:
            number = re.search(r"OpenElection\((\d+)\)", cell)
            if number is not None:
                out.append(
                    (date(int(year), int(month), int(day)), int(number.group(1)), clean(re.sub("<[^>]+>", " ", cell)))
                )
        if len(rows) < PAGE:
            return sorted(out)
        page += 1


def stations(poll: int) -> list[tuple[str, str]]:
    """The polling stations of a poll, as (station id, number)."""

    found = json.loads(raw.cached(SOURCE, stations_url(poll)).path.read_text(encoding="utf-8"))
    return [(str(row["value"]), clean(row["text"])) for row in found if row["value"]]


def fetch() -> None:
    raw.fetch(SOURCE, REGION_SHAPES, suffix=".geojson")
    polls = listing()
    print(f"{SOURCE}: {len(polls)} polls listed", file=sys.stderr, flush=True)
    regions = raw.fetch(SOURCE, regions_url(), suffix=".json")

    for row in json.loads(regions.path.read_text(encoding="utf-8")):
        if row["value"]:
            raw.fetch(SOURCE, communities_url(str(row["value"])), suffix=".json")
    raw.fetch_many(SOURCE, [stations_url(poll) for _, poll, _ in polls], suffix=".json", workers=WORKERS)
    session = Session()

    def one(job: tuple[int, str]) -> None:
        poll, station = job
        try:
            raw.cached(SOURCE, poll_url(poll, station))
            return
        except LookupError:
            pass
        response = session.post(
            **{
                "Input.ElectionId": str(poll),
                "Current.ElectionId": str(poll),
                "Input.SubDistrictId": station,
                "Input.Tab1": "",
                "Input.Tab5": "active-tab",
            }
        )
        keep(poll_url(poll, station), response)

    jobs: list[tuple[int, str]] = []
    for _, poll, _ in polls:
        jobs.append((poll, ""))
        found = stations(poll)
        if len(found) > 1:
            jobs.extend((poll, station) for station, _ in found)
    print(f"{SOURCE}: {len(jobs)} pages to fetch", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        for count, _ in enumerate(pool.map(one, jobs), 1):
            if count % 250 == 0:
                print(f"{SOURCE}: {count}/{len(jobs)}", file=sys.stderr, flush=True)


# Reading a poll's pages.

ARMENIA = str(uuid5(package.NAMESPACE, "polity\x1fAM"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARM"))
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
HEAD_SYSTEM = key("electoral_system", "fptp")
SHAPES_ID = key("source", "geoboundaries.arm.adm1")
REGION_SHAPES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ARM/ADM1/"
    "geoBoundaries-ARM-ADM1.geojson"
)
REGIONS = {
    "ԵՐԵՒԱՆ": "Yerevan",
    "ԱՐԱԳԱԾՈՏՆ": "Aragatsotn",
    "ԱՐԱՐԱՏ": "Ararat",
    "ԱՐՄԱՎԻՐ": "Armavir",
    "ԳԵՂԱՐՔՈՒՆԻՔ": "Gegharkunik",
    "ԼՈՌԻ": "Lori",
    "ԿՈՏԱՅՔ": "Kotayk",
    "ՇԻՐԱԿ": "Shirak",
    "ՍՅՈՒՆԻՔ": "Syunik",
    "ՎԱՅՈՑ ՁՈՐ": "Vayots Dzor",
    "ՏԱՎՈՒՇ": "Tavush",
}


def number(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).upper().split())


def figure(label: str) -> str | None:
    """The accounting figure a result row of the protocol states, by its Armenian label."""
    if label == "Ընտրողների ընդհանուր թիվը":
        return "registered"
    if label.startswith("Գրանցված և քվեաթերթիկ ստացած") or label == "Քվեարկության մասնակիցների ընդհանուր թիվը":
        return "voters"
    if label == "Ընտրողների ցուցակներում ընդգրկված ընտրողների ընդհանուր թիվը":
        return "listed"
    if label in ("Թեկնածուին դեմ քվեարկված քվեաթերթիկների թիվը", "Դեմ քվեարկված քվեաթերթիկների թիվը"):
        return "against"
    if label == "Անվավեր քվեաթերթիկների թիվը":
        return "invalid"
    if label == "Վավեր քվեաթերթիկների թիվը":
        return "valid"
    return None


@dataclass
class Page:
    figures: dict[str, int] = field(default_factory=dict)
    candidates: list[tuple[str, int | None]] = field(default_factory=list)
    elected: list[str] = field(default_factory=list)
    parties: dict[str, str] = field(default_factory=dict)


def tab(text: str, name: str) -> str:
    start = text.find(f'id="{name}"')
    if start < 0:
        return ""
    end = text.find('id="Tab', start + 10)
    return text[start : end if end > 0 else None]


def cells(row: str) -> list[str]:
    return [clean(re.sub(r"<[^>]+>", " ", cell)) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]


@functools.cache
def read(poll: int, station: str = "") -> Page:
    return parse_page(raw.cached(SOURCE, poll_url(poll, station)).path.read_text(encoding="utf-8"))


def parse_page(text: str) -> Page:
    out = Page()
    results = tab(text, "Tab5")
    block = re.search(r'<table class="election-result-cand-table".*?</table>', results, re.DOTALL)
    if block is not None:
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", block.group(0), re.DOTALL):
            found = cells(row)
            if len(found) == 2 and found[0]:
                out.candidates.append((found[0], number(found[1])))
        results = results.replace(block.group(0), "")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", results, re.DOTALL):
        found = cells(row)
        if len(found) == 2 and (name := figure(found[0])) is not None and (value := number(found[1])) is not None:
            out.figures.setdefault(name, value)
    if "registered" not in out.figures and "listed" in out.figures:
        out.figures["registered"] = out.figures["listed"]
    decision = tab(text, "Tab6")
    marker = decision.find("ընտրվել")
    if marker > 0:
        for line in re.findall(r"([^<>\n]+?)\s*(?:<br\s*/?>|</p>|</div>)", decision[marker:]):
            name = clean(line)
            if name and re.fullmatch(r"[Ա-Ֆ]+(?: [Ա-Ֆ\-]+){1,3}", name):
                out.elected.append(name)
    people = tab(text, "Tab2")
    for name, rest in re.findall(r"<h3[^>]*>(.*?)</h3>\s*</div>\s*<p>(.*?)</p>", people, re.DOTALL):
        note = clean(re.sub(r"<[^>]+>", " ", rest))
        if note and "ծնվել" not in note and len(note) < 80:
            out.parties[clean(name)] = note
    return out


# Names.

ARMENIAN = dict(
    zip(
        "աբգդեզէըթժիլխծկհձղճմյնշոչպջռսվտրցփքօֆ",
        ["a", "b", "g", "d", "e", "z", "e", "y", "t", "zh", "i", "l", "kh", "ts", "k", "h", "dz", "gh", "ch", "m"]
        + ["y", "n", "sh", "o", "ch", "p", "j", "r", "s", "v", "t", "r", "ts", "p", "k", "o", "f"],
        strict=True,
    )
)


def latin(text: str) -> str:
    """A plain Latin spelling of Armenian text, for slugs and English names."""
    text = unicodedata.normalize("NFKC", text).lower().replace("ու", "u").replace("և", "ev")
    out = "".join(ARMENIAN.get(c, c) for c in text)
    return re.sub(r"[^a-z0-9]+", " ", out).strip()


def slugify(text: str) -> str:
    return latin(text).replace(" ", "-")


def title(text: str) -> str:
    return " ".join(word.capitalize() for word in latin(text).split())


@dataclass(frozen=True)
class Poll:
    day: date
    number: int
    region: str
    community: str


@functools.cache
def listed() -> list[Poll]:
    out: list[Poll] = []
    for day, number, label in listing():
        region, _, community = label.partition(" - ")
        out.append(Poll(day, number, clean(region), clean(community)))
    return out


@functools.cache
def polls() -> list[Poll]:
    """The listed polls with candidate results; one the Commission lists with none is left out."""
    return [poll for poll in listed() if read(poll.number).candidates]


def package_key(day: date) -> str:
    return f"{SOURCE}/head-{day.isoformat()}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(day) for day in sorted({p.day for p in polls()}))]


def area(scheme: str, text: str) -> str:
    return key("area", scheme, text)


def community_area(poll: Poll) -> str:
    return area("am.community", f"{poll.region}:{poll.community}")


def office_id(poll: Poll) -> str:
    return key("office", poll.region, poll.community)


def party_id(name: str) -> str:
    return key("party", fold(name))


INDEPENDENT = {"ԱՆԿՈՒՍԱԿՑԱԿԱՆ", "Ա/Կ"}


def source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Հայաստանի Հանրապետության կենտրոնական ընտրական հանձնաժողով",
        title="Elections: community head election results by polling station",
        url=f"{SITE}/",
        license="Official results of the Republic of Armenia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Central Electoral Commission of the Republic of Armenia, elections.am",
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The regions (marzer) and communities named in the Central Electoral Commission's list of community head polls",
    )
    p.external |= {ARMENIA, COUNTRY, "am.marz", "am.community", "am.precinct"}
    source(p)
    shapes = raw.cached(SOURCE, REGION_SHAPES)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Armenia ADM1 (province and city outlines, 2005)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 2.5 Generic",
        license_url="https://creativecommons.org/licenses/by/2.5/",
        attribution="geoBoundaries (William & Mary geoLab), from Wikimedia Commons",
    )
    p.artifact(shapes, SHAPES_ID)
    p.add(
        "boundary_set",
        id=key("boundary_set", "am.marz", 2005),
        scheme_code="am.marz",
        vintage="2005",
        as_of=shapes.retrieved_at.date(),
    )
    outlines = {
        feature["properties"]["shapeName"]: shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    regions = sorted({poll.region for poll in polls()})
    for region in regions:
        id = area("am.marz", region)
        p.add("area", id=id, scheme_code="am.marz")
        outline = outlines.get(REGIONS.get(fold(region), ""))
        if outline is not None:
            outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(shapely.make_valid(outline)))), 4326)
            p.add(
                "area_geometry",
                area_id=id,
                geom=shapely.to_wkb(outline, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
                ),
                quality="academic",
                source_id=SHAPES_ID,
            )
        p.add("area_code", area_id=id, code=region)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, region, "hy", "official")
        p.name(id, REGIONS.get(fold(region), title(region)), "en", "english", False)
    for region, community in sorted({(poll.region, poll.community) for poll in polls()}):
        id = area("am.community", f"{region}:{community}")
        p.add("area", id=id, scheme_code="am.community")
        p.add("area_code", area_id=id, code=f"{region}:{community}")
        p.add("area_parent", area_id=id, parent_id=area("am.marz", region))
        p.name(id, community, "hy", "official")
        p.name(id, title(community), "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The community head offices, the plurality electoral system, and the parties named on the "
        "Central Electoral Commission's candidate lists",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARMENIA}
    seen: set[tuple[str, str]] = set()
    for poll in polls():
        if (poll.region, poll.community) in seen:
            continue
        seen.add((poll.region, poll.community))
        office = office_id(poll)
        p.add("office", id=office, polity_id=ARMENIA, kind="mayor")
        p.name(office, f"{poll.community} համայնքի ղեկավար ({poll.region})", "hy", "official", False)
        p.name(office, f"Head of {title(poll.community)} community, {title(poll.region)}", "en", "english")
    p.add(
        "electoral_system",
        id=HEAD_SYSTEM,
        polity_id=ARMENIA,
        valid_from=date(2005, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", HEAD_SYSTEM),
        entity_id=HEAD_SYSTEM,
        kind="methodology",
        body_md=(
            "Each voter marks one candidate for community head, and the candidate with the most votes is elected."
        ),
        lang="en",
    )
    parties: dict[str, str] = {}
    for name in sorted({name for poll in polls() for name in names(poll)}):
        if fold(name) not in INDEPENDENT:
            parties.setdefault(party_id(name), name)
    for id, name in parties.items():
        p.add("party", id=id, kind="party", polity_id=ARMENIA)
        p.add("external_id", entity_id=id, scheme="am.local_heads.party", value=fold(name))
        p.name(id, name, "hy", "official")
    return p


def names(poll: Poll) -> set[str]:
    return set(read(poll.number).parties.values())


# One package per election day.

METHOD = (
    "Counted by the Central Electoral Commission and published per polling station on the community's poll page. "
    "Registered voters are the electors in the polling station's lists, voters are the participants who signed for a "
    "ballot, and valid and invalid ballots are as printed. The "
    "Commission's page names the elected candidate only where it publishes the decision that declares them."
)


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


MEASURES = {"registered": REGISTERED, "voters": VOTERS, "invalid": INVALID, "valid": VALID}


def match(names: list[str], elected: list[str]) -> list[int] | None:
    """The positions of the candidates the decision names, or None when a name does not match exactly one."""
    folded = [fold(name).split() for name in names]
    found: list[int] = []
    for line in elected:
        words = fold(line).split()
        hits = [
            i
            for i, w in enumerate(folded)
            if i not in found and (w == words or (len(w) == 2 and set(w) <= set(words) and words[:2] == w))
        ]
        if len(hits) != 1:
            return None
        found.append(hits[0])
    return found


def election(day: date, group: list[Poll]) -> Package:
    p = Package(
        package_key(day),
        SOURCE,
        f"The community head elections of {day.isoformat()} by polling station from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA, COUNTRY, "am.precinct"}
    path = f"am/{day.isoformat()}-community-heads"
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=ARMENIA, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Armenian community head elections, {day:%B} {day.day}", "en", "official")
    p.name(
        election_id,
        f"Համայնքի ղեկավարի ընտրություններ, {day.day:02d}.{day.month:02d}.{day.year}",
        "hy",
        "native",
        False,
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    used: set[str] = set()
    values = Facts()
    outcomes = unmatched = 0
    over: list[str] = []
    short: list[str] = []
    for poll in sorted(group, key=lambda x: (x.region, x.community, x.number)):
        page = read(poll.number)
        p.artifact(raw.cached(SOURCE, poll_url(poll.number)), SOURCE_ID)
        p.uses(raw.cached(SOURCE, stations_url(poll.number)))
        slug = slugify(f"{poll.region} {poll.community}")
        if slug in used:
            slug = f"{slug}-{poll.number}"
        used.add(slug)
        contest = key("contest", poll.number)
        elected = match([name for name, _ in page.candidates], page.elected) if page.elected else None
        if page.elected and elected is None:
            unmatched += 1
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office_id(poll),
            district_area_id=community_area(poll),
            magnitude=1,
            round_number=1,
            electoral_system_id=HEAD_SYSTEM,
            default_result_kind="counted",
        )
        p.name(
            contest, f"{poll.community} համայնքի ղեկավար ({poll.region}), {day.isoformat()}", "hy", "official", False
        )
        p.name(
            contest,
            f"Head of {title(poll.community)} community, {title(poll.region)}, {day.isoformat()}",
            "en",
            "english",
        )
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", poll.number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options: list[str] = []
        by_name: dict[str, list[int]] = defaultdict(list)
        for position, (name, _) in enumerate(page.candidates, 1):
            option = key("option", poll.number, position)
            options.append(option)
            by_name[name].append(position - 1)
            party = party_for(page, name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            person = key("person", poll.number, position)
            p.add("person", id=person)
            p.name(person, name, "hy", "official")
            p.add(
                "candidacy",
                id=key("candidacy", poll.number, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party_id(party) if party and fold(party) not in INDEPENDENT else None,
                is_independent=party is not None and fold(party) in INDEPENDENT,
            )
            if elected is not None:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if position - 1 in elected else "lost",
                )
                outcomes += 1
        listed = stations(poll.number)
        against = key("option", poll.number, "against")
        if "against" in page.figures:
            p.add(
                "contest_option",
                id=against,
                contest_id=contest,
                kind="none_of_the_above",
                ballot_position=len(options) + 1,
                ballot_label="Against the candidate",
            )
        pages: list[tuple[str, Page]]
        if len(listed) > 1:
            repeated = {n for _, n in listed if sum(1 for _, other in listed if other == n) > 1}
            pages = [
                (f"{number_}~{station}" if number_ in repeated else number_, read(poll.number, station))
                for station, number_ in listed
            ]
            for station, _ in listed:
                p.artifact(raw.cached(SOURCE, poll_url(poll.number, station)), SOURCE_ID)
        else:
            pages = [(listed[0][1] if listed else "community", page)]
        for station, unit_page in pages:
            unit = key("unit", poll.number, station)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ARMENIA,
                scheme_code="am.precinct",
                code=f"head:{poll.number}:{station}",
                area_relation="within",
                area_id=community_area(poll),
                kind="standard" if listed else "aggregate",
            )
            for name, measure in MEASURES.items():
                values.add(result_set, unit, None, measure, unit_page.figures.get(name))
            if "against" in page.figures:
                values.add(result_set, unit, against, VOTES, unit_page.figures.get("against"))
            counted = sum(votes or 0 for _, votes in unit_page.candidates) + unit_page.figures.get("against", 0)
            valid = unit_page.figures.get("valid")
            if valid is not None and counted != valid:
                short.append(
                    f"{poll.community} ({poll.region}), station {station} ({counted} to candidates and against, {valid} valid)"
                )
            registered, voters = unit_page.figures.get("registered"), unit_page.figures.get("voters")
            if registered is not None and voters is not None and voters > registered:
                over.append(
                    f"{poll.community} ({poll.region}), station {station} ({voters} voters, {registered} registered)"
                )
            taken: dict[str, int] = defaultdict(int)
            for name, votes in unit_page.candidates:
                positions = by_name.get(name, [])
                if taken[name] < len(positions):
                    values.add(result_set, unit, options[positions[taken[name]]], VOTES, votes)
                    taken[name] += 1
        for name, measure in MEASURES.items():
            if name in page.figures:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=page.figures[name])
        if "against" in page.figures:
            p.add("declared", result_set_id=result_set, option_id=against, measure=VOTES, value=page.figures["against"])
        for (name, votes), option in zip(page.candidates, options, strict=True):
            if votes is not None:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    p.frame("result_value", values.frame())
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own protocol prints more participants than registered voters at " + "; ".join(over),
        )
    if short:
        p.accept(
            "accounting.votes_valid",
            "The Commission's own protocol prints candidate votes that differ from its valid ballot count (against votes included) at "
            + "; ".join(short),
        )
    print(f"{package_key(day)}: {len(group)} contests, {outcomes} outcomes, {unmatched} unmatched", flush=True)
    return p


def party_for(page: Page, name: str) -> str | None:
    words = set(name.split())
    for person, party in page.parties.items():
        if set(person.split()) == words:
            return party
    return None


def parse() -> None:
    geo().write()
    registry().write()
    by_day: dict[date, list[Poll]] = defaultdict(list)
    for poll in polls():
        by_day[poll.day].append(poll)
    for day, group in sorted(by_day.items()):
        election(day, group).write()


def emit() -> None:
    package.run("load", packages())
