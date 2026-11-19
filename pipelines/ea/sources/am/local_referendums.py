"""Armenia's community merger referendums of 17 May 2015 from the Central Electoral Commission (elections.am).

Twenty-two communities in Lori, Syunik, and Tavush each voted on joining a new, larger community. The Commission's
list of local referendums gives each poll, its page gives the question and the protocol for the whole community, and
one spreadsheet per region gives the figures of every polling station.
"""

import functools
import html
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid5

import httpx
import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "am.local_referendums"
SITE = "https://www.elections.am"
SECTION = f"{SITE}/Referendum/LocalReferendum"
DOCS = "https://res.elections.am/images/doc"
TYPE = "8"
DAY = date(2015, 5, 17)
ARMENIA = str(uuid5(package.NAMESPACE, "polity\x1fAM"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fARM"))
VOTES, REGISTERED, INVALID, VOTERS = 1, 2, 5, 14
REGIONS = {
    "06": ("Լոռի", "Lori", "lfLori"),
    "09": ("Սյունիք", "Syunik", "lfSyunik"),
    "11": ("Տավուշ", "Tavush", "lfTavush"),
}
GEOBOUNDARIES = "am.local_referendums.geoboundaries"
OUTLINES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/ARM/ADM1/"
    "geoBoundaries-ARM-ADM1_simplified.geojson"
)
GEO_SOURCE = str(uuid5(package.NAMESPACE, "am.local_referendums\x1fsource\x1fgeoboundaries"))
# The am.marz boundary set of 2005, which am.local_councils/geo mints from the same geoBoundaries release.
MARZ_2005 = str(uuid5(package.NAMESPACE, "am.local_councils\x1fboundary_set\x1fam.marz\x1f2005"))
METHOD = (
    "Counted by the precinct commissions and published by the Central Electoral Commission in its regional results "
    "spreadsheet, with the protocol it prints on each poll's page for the whole community. Registered voters are the "
    "electors in the main and supplementary lists, voters are the participants in the vote, and the answers are the "
    "ballots marked yes («ԱՅՈ») and no («ՈՉ»). Invalid ballots are as printed. The community's own totals are stored "
    "as declared."
)


def clean(text: object) -> str:
    return " ".join(html.unescape(str(text if text is not None else "")).replace("\xa0", " ").split())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
SYSTEM = key("electoral_system", "local_referendum")


def list_url() -> str:
    return f"{SECTION}#list"


def poll_url(poll: int) -> str:
    return f"{SECTION}#electionId={poll}"


def sheet_url(region: str) -> str:
    return f"{DOCS}/{REGIONS[region][2]}.xlsx"


def post(poll: int) -> httpx.Response:
    headers = {"User-Agent": raw.USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=180) as client:
        form = client.get(SECTION).text
        token = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', form)
        if token is None:
            raise LookupError(f"{SECTION} has no form")
        data = {
            "__RequestVerificationToken": token.group(1),
            "Culture": "hy",
            "Input.ElectionTypeId": TYPE,
            "Input.ElectionId": str(poll),
            "Current.ElectionId": str(poll),
            "Input.RegionCode": "",
            "Input.CommunityCode": "",
            "Input.SubDistrictId": "",
        }
        if poll == 0:
            data |= {"ElectionPaging.PageIndex": "1", "ElectionPaging.PageSize": "1000"}
        for _ in range(5):
            response = client.post(SECTION, data=data)
            if response.status_code < 500:
                response.raise_for_status()
                return response
        raise LookupError(f"{SECTION} did not answer {poll}")


def keep(url: str, poll: int) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        response = post(poll)
        return raw.store(
            SOURCE,
            url,
            response.content,
            suffix=".html",
            retrieved_at=datetime.now(UTC),
            media_type=response.headers.get("content-type", "text/html"),
        )


@dataclass(frozen=True)
class Poll:
    id: int
    region: str  # the region's name as the list prints it
    community: str  # the community's name as the list prints it


def listing() -> list[Poll]:
    text = keep(list_url(), 0).path.read_text(encoding="utf-8")
    out: list[Poll] = []
    for day, cell in re.findall(r"<td>(\d\d\.\d\d\.\d{4})</td>\s*<td>(.*?)</td>", text, re.DOTALL):
        number = re.search(r"OpenElection\((\d+)\)", cell)
        label = clean(re.sub("<[^>]+>", " ", cell))
        if number is not None and day == DAY.strftime("%d.%m.%Y") and " - " in label:
            region, community = label.split(" - ", 1)
            out.append(Poll(int(number.group(1)), region, community))
    return sorted(out, key=lambda poll: poll.id)


def fetch() -> None:
    raw.fetch(GEOBOUNDARIES, OUTLINES, suffix=".geojson")
    for poll in listing():
        keep(poll_url(poll.id), poll.id)
    for region in REGIONS:
        raw.fetch(SOURCE, sheet_url(region), suffix=".xlsx")


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/referendum-{DAY}"]


# Reading the pages and spreadsheets.


def number(text: str) -> int | None:
    digits = re.sub(r"[\s\xa0]", "", text)
    return int(digits) if digits.isdigit() else None


@dataclass
class Page:
    question: str
    protocol: dict[str, int]  # label -> figure


@functools.cache
def page(poll: Poll) -> Page:
    text = raw.cached(SOURCE, poll_url(poll.id)).path.read_text(encoding="utf-8")
    text = re.sub(r"(?s)<script.*?</script>", "", text)
    tab = re.search(r'(?s)<div id="Tab2" class="tab">\s*<h4>(.*?)</h4>', text)
    if tab is None:
        raise LookupError(f"poll {poll.id} has no question")
    protocol: dict[str, int] = {}
    for row in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", text):
        cells = [clean(re.sub("<[^>]+>", " ", c)) for c in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", row)]
        cells = [c for c in cells if c]
        if len(cells) == 2 and (value := number(cells[1])) is not None:
            protocol.setdefault(cells[0], value)
    return Page(clean(re.sub("<[^>]+>", " ", tab.group(1))), protocol)


@dataclass
class Row:
    region: str
    community: str
    station: str
    registered: int
    voters: int
    invalid: int
    yes: int
    no: int


@functools.cache
def sheet(region: str) -> tuple[str, list[Row]]:
    """The region's question and one row per polling station of its communities."""
    z = zipfile.ZipFile(raw.cached(SOURCE, sheet_url(region)).path)
    shared = [
        clean(re.sub("<[^>]+>", "", s))
        for s in re.findall(r"(?s)<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"))
    ]
    rows: list[list[str]] = []
    for row in re.findall(r"(?s)<row[^>]*>.*?</row>", z.read("xl/worksheets/sheet1.xml").decode("utf-8")):
        cells: list[str] = []
        for attrs, body in re.findall(r"(?s)<c ([^>]*?)(?:/>|>(.*?)</c>)", row):
            value = re.search(r"<v>(.*?)</v>", body or "")
            ref = re.search(r'r="([A-Z]+)\d+"', attrs)
            column = 0
            for letter in ref.group(1) if ref else "A":
                column = column * 26 + ord(letter) - 64
            cells.extend([""] * (column - 1 - len(cells)))
            cells.append((shared[int(value.group(1))] if 't="s"' in attrs else value.group(1)) if value else "")
        rows.append(cells)
    question = rows[1][0]
    out: list[Row] = []
    current = ""
    for cells in rows[3:]:
        cells = cells + [""] * (12 - len(cells))
        if re.fullmatch(r"\d+/\d+", cells[1]) and number(cells[2]) is not None:
            start = 0
        elif re.fullmatch(r"\d+/\d+", cells[2]) and number(cells[3]) is not None:
            start = 1
        else:
            continue
        name = cells[start] or current
        current = name
        figures = [number(c) for c in cells[start + 2 : start + 11]]
        registered, voters, _, _, invalid, yes, no = figures[:7]
        assert None not in (registered, voters, invalid, yes, no)
        out.append(
            Row(
                REGIONS[region][0],
                name,
                cells[start + 1],
                registered or 0,
                voters or 0,
                invalid or 0,
                yes or 0,
                no or 0,
            )
        )
    return question, out


# Places.

ENGLISH = {
    "Թումանյան": "Tumanyan",
    "Աթան": "Atan",
    "Ահնիձոր": "Ahnidzor",
    "Լորուտ": "Lorut",
    "Մարց": "Marts",
    "Շամուտ": "Shamut",
    "Քարինջ": "Karinj",
    "Դիլիջան": "Dilijan",
    "Հաղարծին": "Haghartsin",
    "Թեղուտ": "Teghut",
    "Գոշ": "Gosh",
    "Աղավնավանք": "Aghavnavank",
    "Խաչարձան": "Khachardzan",
    "Հովք": "Hovk",
    "Խոտ": "Khot",
    "Հալիձոր": "Halidzor",
    "Հարժիս": "Harzhis",
    "Շինուհայր": "Shinuhayr",
    "Սվարանց": "Svarants",
    "Տաթեվ": "Tatev",
    "Տանձատափ": "Tandzatap",
    "Քաշունի": "Kashuni",
}
MERGED = {"Թումանյան": "Tumanyan", "Տաթև": "Tatev", "Տաթեվ": "Tatev", "Դիլիջան": "Dilijan"}


def short(name: str) -> str:
    return re.sub(r" (քաղաք|գյուղ)$", "", name)


def area(scheme: str, text: str) -> str:
    return key("area", scheme, text)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The three regions (marzer) and the 22 communities that voted in the community merger referendums of 2015",
    )
    p.external |= {ARMENIA, COUNTRY, "am.marz", "am.community", "am.precinct"}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Հայաստանի Հանրապետության կենտրոնական ընտրական հանձնաժողով",
        title="Local referendums: polls, protocols, and results by polling station",
        url=SECTION,
        license="Official results of the Republic of Armenia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Central Electoral Commission of the Republic of Armenia, elections.am",
    )
    for marz, english, _ in REGIONS.values():
        id = area("am.marz", marz)
        p.add("area", id=id, scheme_code="am.marz")
        p.add("area_code", area_id=id, code=marz)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, marz, "hy", "official")
        p.name(id, english, "en", "english", False)
    outlines(p)
    for poll in listing():
        id = area("am.community", f"{poll.region}:{poll.community}")
        p.add("area", id=id, scheme_code="am.community")
        p.add("area_code", area_id=id, code=f"{poll.region}:{poll.community}")
        p.add("area_parent", area_id=id, parent_id=area("am.marz", poll.region))
        p.name(id, poll.community, "hy", "official")
        p.name(id, ENGLISH[short(poll.community)], "en", "english", False)
    return p


def outlines(p: Package) -> None:
    """Attach the geoBoundaries outlines of the three regions to the am.marz 2005 boundary set am.local_councils owns.

    The communities as they stood before the 2016-2017 mergers have no open outlines.
    """
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from Wikimedia Commons",
        title="geoBoundaries gbOpen Armenia ADM1, 2005",
        url="https://www.geoboundaries.org/",
        license="CC BY 2.5",
        license_url="https://creativecommons.org/licenses/by/2.5/",
        attribution="geoBoundaries (William & Mary geoLab), Wikimedia Commons",
    )
    p.artifact(raw.cached(GEOBOUNDARIES, OUTLINES), GEO_SOURCE)
    # The boundary set of am.marz at the 2005 vintage belongs to am.local_councils/geo; these outlines fall under it.
    p.external |= {MARZ_2005}
    wanted = {english: marz for marz, english, _ in REGIONS.values()}
    found: dict[str, shapely.geometry.base.BaseGeometry] = {}
    for feature in json.loads(raw.cached(GEOBOUNDARIES, OUTLINES).path.read_text("utf-8"))["features"]:
        name = feature["properties"]["shapeName"]
        if name in wanted:
            found[wanted[name]] = shapely.geometry.shape(feature["geometry"])
    if set(found) != set(wanted.values()):
        raise ValueError(f"the geoBoundaries file lacks regions: {sorted(set(wanted.values()) - set(found))}")
    for marz, shape in sorted(found.items()):
        if shape.geom_type == "Polygon":
            shape = shapely.geometry.MultiPolygon([shape])
        p.add(
            "area_geometry",
            area_id=area("am.marz", marz),
            valid_from=DAY,
            valid_to=None,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GEO_SOURCE,
        )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of Armenian local referendums",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARMENIA}
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ARMENIA,
        valid_from=date(2002, 1, 1),
        valid_to=None,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A local referendum question is decided by the votes for and against it.",
        lang="en",
    )
    return p


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


PRINTED = {
    "registered": ("Հանրաքվեին մասնակցելու իրավունք ունեցող անձանց ընդհանուր թիվը", REGISTERED),
    "voters": ("Քվեարկության մասնակիցների ընդհանուր թիվը", VOTERS),
    "invalid": ("Անվավեր քվեաթերթիկների թիվը", INVALID),
    "yes": ('"ԱՅՈ" քվեարկված քվեաթերթիկների ընդհանուր թիվը', VOTES),
    "no": ('"ՈՉ" քվեարկված քվեաթերթիկների ընդհանուր թիվը', VOTES),
}


def election() -> Package:
    p = Package(
        f"{SOURCE}/referendum-{DAY}",
        SOURCE,
        "The 22 community merger referendums of 17 May 2015 by polling station from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA, COUNTRY, "am.precinct"}
    for poll in listing():
        p.artifact(raw.cached(SOURCE, poll_url(poll.id)), SOURCE_ID)
    for region in REGIONS:
        p.artifact(raw.cached(SOURCE, sheet_url(region)), SOURCE_ID)
    election_id = key("election", DAY)
    p.add("election", id=election_id, polity_id=ARMENIA, kind="referendum", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "2015 Armenian community merger referendums", "en", "official")
    p.name(election_id, "Տեղական հանրաքվեներ համայնքների միավորման վերաբերյալ, 2015", "hy", "native", False)
    path = f"am/{DAY}-local-referendums"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    rows: dict[str, list[Row]] = {}
    for region in REGIONS:
        for row in sheet(region)[1]:
            rows.setdefault(f"{row.region}:{row.community}", []).append(row)
    values = Facts()
    mismatches: list[str] = []
    for poll in listing():
        community = short(poll.community)
        stations = rows[f"{poll.region}:{community}"]
        shown = page(poll)
        merged = re.search(r"՝ (.*?) համայնք անվանմամբ", shown.question)
        new = MERGED[merged.group(1)] if merged else None
        english = ENGLISH[community]
        contest = key("contest", poll.id)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            office_id=None,
            body_id=None,
            district_area_id=area("am.community", f"{poll.region}:{poll.community}"),
            magnitude=None,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Merger of {english} into {new} community", "en", "official")
        p.name(contest, shown.question, "hy", "native", False)
        p.add("slug", path=f"{path}/{english.lower()}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="referendum")
        result_set = key("result_set", poll.id)
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
        options: dict[str, str] = {}
        for position, label in enumerate(("Yes", "No"), 1):
            option = options[label] = key("option", poll.id, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, label, "en", "official")
        for row in stations:
            unit = key("unit", poll.id, row.station)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ARMENIA,
                scheme_code="am.precinct",
                code=f"{DAY}:{poll.id}:{row.station}",
                area_relation="within",
                area_id=area("am.community", f"{poll.region}:{poll.community}"),
                kind="standard",
            )
            values.add(result_set, unit, None, REGISTERED, row.registered)
            values.add(result_set, unit, None, VOTERS, row.voters)
            values.add(result_set, unit, None, INVALID, row.invalid)
            values.add(result_set, unit, options["Yes"], VOTES, row.yes)
            values.add(result_set, unit, options["No"], VOTES, row.no)
        sums = {
            "registered": sum(r.registered for r in stations),
            "voters": sum(r.voters for r in stations),
            "invalid": sum(r.invalid for r in stations),
            "yes": sum(r.yes for r in stations),
            "no": sum(r.no for r in stations),
        }
        declared: dict[str, int] = {}
        for name, (label, measure) in PRINTED.items():
            value = shown.protocol[label]
            declared[name] = value
            option = options["Yes"] if name == "yes" else options["No"] if name == "no" else None
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
            if value != sums[name]:
                mismatches.append(f"{poll.id} {english} {name}: stations {sums[name]}, page {value}")
        winner = "Yes" if declared["yes"] > declared["no"] else "No"
        for label, option in options.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if label == winner else "lost")
    p.frame("result_value", values.frame())
    print(f"{p.key}: {len(listing())} polls, {len(mismatches)} figures differ from the pages", flush=True)
    for line in mismatches:
        print("  " + line, flush=True)
    return p
