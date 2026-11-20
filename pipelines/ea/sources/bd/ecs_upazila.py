"""Bangladesh Election Commission: the upazila parishad elections of 2024, from the Commission's results portal.

The results portal (`http://103.183.38.66:81/election-result`) serves, for each phase, contest, and district, the
returning officer's result sheet of each upazila (its "barta sheet", a preliminary unofficial result) together with
the upazila's registered voters, valid and cancelled votes, and polling centres as text, and the same figures for
each polling centre. The candidates' votes appear only on the result sheet, a scanned one-page form; `sheet.py`
reads them, and a sheet is used only when its candidates' votes add up to the valid votes the portal states for it.
The Commission's candidate portal (`http://103.183.38.66/`) lists each upazila's final candidates with their names
and parties as text; the sheet lists them in the ballot's Bengali alphabetical order, which is how the two are
matched.

Only the four phases of the sixth upazila parishad elections of 2024 hold results on the portal; its 2019 entries
and the by-elections since return no rows. The candidate portal lists no candidates for the first phase (8 May
2024), so that phase's sheets serve only as known digits for reading the others.
"""

import functools
import json
import os
import pickle
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid5

import polars as pl
import pymupdf
from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.sources.bd import sheet
from ea.store import DATA

SOURCE = "bd.ecs_upazila"
BANGLADESH = str(uuid5(package.NAMESPACE, "polity\x1fBD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBGD"))
RESULTS = "http://103.183.38.66:81/"
API = RESULTS + "api/"
CANDIDATES = "http://103.183.38.66/api/"
UPAZILA_TYPE = 2
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DERIVED, DIGITIZED = 1, 8
FINAL = 11  # the candidate portal's status of a final candidate
INDEPENDENT = "স্বতন্ত্র"
DISTRICT, UPAZILA, CENTRE = "bd.district", "bd.upazila", "bd.polling_center"
START = date(2024, 5, 8)

# The portal's election ids for the four phases of the sixth upazila parishad elections.
PHASES = {81: date(2024, 5, 8), 90: date(2024, 5, 21), 103: date(2024, 5, 29), 109: date(2024, 6, 5)}
BUILT = (90, 103, 109)  # the phases whose candidates the candidate portal names
ORDINAL = {81: "first", 90: "second", 103: "third", 109: "fourth"}


@dataclass(frozen=True)
class Post:
    slug: str
    english: str
    bengali: str
    kind: str


# The portal's candidate types.
POSTS = {
    2: Post("chairman", "Chairman", "চেয়ারম্যান", "mayor"),
    3: Post("vice-chairman", "Vice-Chairman", "ভাইস চেয়ারম্যান", "deputy_mayor"),
    4: Post("woman-vice-chairman", "Woman Vice-Chairman", "মহিলা ভাইস চেয়ারম্যান", "deputy_mayor"),
}
LIMIT = 500  # the most rows the portal returns in one page

# The Commission's district ids and the districts' English names.
DISTRICTS = {
    1: "Barguna", 2: "Barishal", 3: "Bhola", 4: "Jhalokati", 5: "Patuakhali", 6: "Pirojpur", 7: "Bandarban",
    8: "Chattogram", 9: "Cox's Bazar", 10: "Khagrachhari", 11: "Rangamati", 12: "Brahmanbaria", 13: "Chandpur",
    14: "Feni", 15: "Noakhali", 16: "Lakshmipur", 17: "Cumilla", 18: "Panchagarh", 19: "Thakurgaon", 20: "Dinajpur",
    21: "Nilphamari", 22: "Lalmonirhat", 23: "Rangpur", 24: "Kurigram", 25: "Gaibandha", 26: "Joypurhat",
    27: "Bogura", 28: "Chapainawabganj", 29: "Naogaon", 30: "Rajshahi", 31: "Natore", 32: "Sirajganj", 33: "Pabna",
    34: "Meherpur", 35: "Kushtia", 36: "Chuadanga", 37: "Jhenaidah", 38: "Jashore", 39: "Magura", 40: "Narail",
    41: "Bagerhat", 42: "Khulna", 43: "Satkhira", 44: "Tangail", 45: "Jamalpur", 46: "Sherpur", 47: "Mymensingh",
    48: "Netrokona", 49: "Kishoreganj", 50: "Manikganj", 51: "Munshiganj", 52: "Gazipur", 53: "Narsingdi",
    54: "Narayanganj", 55: "Dhaka", 56: "Gopalganj", 57: "Madaripur", 58: "Shariatpur", 59: "Rajbari",
    60: "Faridpur", 61: "Sunamganj", 62: "Sylhet", 63: "Moulvibazar", 64: "Habiganj",
}  # fmt: skip


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECS = key("source", "ecs")
FPTP = key("electoral_system", "fptp")
ELECTION = key("election", "2024")


def url(base: str, endpoint: str, /, **params: object) -> str:
    return f"{base}{endpoint}?{urlencode({k: str(v) for k, v in params.items()})}"


def zillas_url(election: int) -> str:
    return url(API, "zillas", electionID=election)


def upazilas_url(election: int, zilla: str) -> str:
    return url(API, "upazila", electionID=election, zillaID=zilla)


def sheets_url(election: int, zilla: str, post: int) -> str:
    return url(
        API,
        "bartasheet-list",
        election_type_id=UPAZILA_TYPE,
        election_id=election,
        candidate_type=post,
        zilla_id=zilla,
        page=1,
        limit=LIMIT,
    )


def centres_url(election: int, zilla: str, post: int, page: int) -> str:
    return url(
        API,
        "center-wise-results",
        election_type_id=UPAZILA_TYPE,
        election_id=election,
        candidate_type=post,
        zilla_id=zilla,
        page=page,
        limit=LIMIT,
    )


def candidates_url(election: int, zilla: str, post: int) -> str:
    return url(CANDIDATES, "candidates", electionID=election, zillaID=zilla, candidate_TypeID=post)


def document_url(path: str) -> str:
    """The portal's link to a stored document; the filename only names the download."""
    return url(API, "document", path=path, filename=path.split("?")[0].rsplit("/", 1)[-1])


def schedules_url() -> str:
    return url(API, "election-schedules", election_typeID=UPAZILA_TYPE)


def load(link: str) -> Any:
    return json.loads(raw.cached(SOURCE, link).path.read_bytes())


def zillas(election: int) -> list[dict[str, str]]:
    return load(zillas_url(election))


def fetch() -> None:
    raw.fetch(SOURCE, schedules_url(), suffix=".json")
    raw.fetch(SOURCE, url(API, "candidate-types", election_typeID=UPAZILA_TYPE), suffix=".json")
    many = lambda links: raw.fetch_many(SOURCE, links, suffix=".json", workers=2)
    many([zillas_url(e) for e in PHASES])
    first: list[str] = []
    for e in PHASES:
        for z in zillas(e):
            zilla = z["zillaID"]
            first.append(upazilas_url(e, zilla))
            for post in POSTS:
                first += [sheets_url(e, zilla, post), centres_url(e, zilla, post, 1), candidates_url(e, zilla, post)]
    many(first)
    more: list[str] = []
    for e in PHASES:
        for z in zillas(e):
            for post in POSTS:
                total = int(load(centres_url(e, z["zillaID"], post, 1))["total"])
                more += [centres_url(e, z["zillaID"], post, page) for page in range(2, -(-total // LIMIT) + 1)]
    many(more)
    documents: list[str] = []
    for e in PHASES:
        for z in zillas(e):
            for post in POSTS:
                for row in load(sheets_url(e, z["zillaID"], post))["data"]:
                    if row["ro_barta_sheet"]:
                        documents.append(document_url(row["ro_barta_sheet"]))
    raw.fetch_many(SOURCE, documents, suffix=".pdf", workers=2)


# ----------------------------------------------------------------------------------------------------------------
# The crawl, as the portal served it.


@dataclass
class Contest:
    phase: int
    zilla: int
    upazila: str
    post: int
    sheet: dict[str, Any]  # the upazila's row in the barta sheet list
    sheets_url: str
    centres: list[tuple[dict[str, Any], str]]  # each centre's row and the page that served it
    candidates: list[dict[str, Any]]  # the final candidates, in ballot order
    candidates_url: str

    @property
    def document(self) -> str | None:
        path = self.sheet["ro_barta_sheet"]
        return document_url(path) if path else None


def collation(name: str) -> str:
    """A candidate's name as the ballot sorts it: Bengali alphabetical order, which the decomposed code points
    follow, without the invisible joiners some names carry."""
    text = name.replace("‌", "").replace("‍", "").replace("﻿", "")
    return unicodedata.normalize("NFD", " ".join(text.split()))


def clean(text: str) -> str:
    return " ".join(text.replace("‌", "").replace("‍", "").replace("﻿", "").split())


@functools.cache
def crawl() -> tuple[dict[int, str], dict[str, tuple[int, str]], list[Contest]]:
    districts: dict[int, str] = {}
    upazilas: dict[str, tuple[int, str]] = {}
    contests: list[Contest] = []
    for e in PHASES:
        for z in zillas(e):
            zilla = int(z["zillaID"])
            districts[zilla] = clean(z["zilla_name"])
            for u in load(upazilas_url(e, z["zillaID"])):
                upazilas[str(u["upazilaID"])] = (zilla, clean(u["upazila_name"]))
            for post in POSTS:
                centres: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
                page = 1
                while True:
                    link = centres_url(e, z["zillaID"], post, page)
                    data = load(link)
                    for row in data["data"]:
                        centres[str(row["upazilla_id"])].append((row, link))
                    if page * LIMIT >= int(data["total"]):
                        break
                    page += 1
                link = candidates_url(e, z["zillaID"], post)
                candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in load(link)["candidates"]:
                    if row["nomination_status"] == FINAL:
                        candidates[str(row["upazilaID"])].append(row)
                for row in load(sheets_url(e, z["zillaID"], post))["data"]:
                    u = str(row["upazilla_id"])
                    upazilas.setdefault(u, (zilla, clean(row["upazilla_name"])))
                    contests.append(
                        Contest(
                            e,
                            zilla,
                            u,
                            post,
                            row,
                            sheets_url(e, z["zillaID"], post),
                            centres.get(u, []),
                            sorted(candidates.get(u, []), key=lambda c: collation(c["candidate_name"])),
                            link,
                        )
                    )
    return districts, upazilas, contests


# ----------------------------------------------------------------------------------------------------------------
# Reading the result sheets.


@dataclass
class Reading:
    votes: list[int]
    boxes: list[tuple[float, float, float, float]]


def layouts(contests: list[Contest]) -> dict[str, sheet.Layout | str]:
    """Each result sheet's layout by its sha256, kept between runs in the parsed folder."""
    cache = raw.parsed(SOURCE) / f"layouts-{sheet.VERSION}.pickle"
    cache.parent.mkdir(parents=True, exist_ok=True)
    known: dict[str, sheet.Layout | str] = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    paths = {}
    for c in contests:
        if c.document:
            artifact = raw.cached(SOURCE, c.document)
            paths[artifact.sha256] = artifact.path
    todo = [sha for sha in paths if sha not in known]
    if todo:
        with ProcessPoolExecutor(max(1, min(8, (os.cpu_count() or 2) - 1))) as pool:
            for sha, found in zip(todo, pool.map(sheet.attempt, [paths[s] for s in todo], chunksize=8), strict=True):
                known[sha] = found
        cache.write_bytes(pickle.dumps(known))
    return {sha: known[sha] for sha in paths}


def readings(contests: list[Contest]) -> tuple[dict[int, Reading], dict[str, int]]:
    """The candidates' votes read from each contest's sheet, by the contest's index, and why the rest were not."""
    found = layouts(contests)
    shas = {i: raw.cached(SOURCE, c.document).sha256 for i, c in enumerate(contests) if c.document}
    samples: list[Any] = []
    for i, sha in shas.items():
        lay = found[sha]
        if isinstance(lay, sheet.Layout):
            c = contests[i]
            samples += sheet.labelled(lay, int(c.sheet["clear_total_vote"]), int(c.sheet["cancel_total_vote"]))
    reader = sheet.Reader(samples)
    out: dict[int, Reading] = {}
    why: dict[str, int] = defaultdict(int)
    shared = {sha for sha, n in Counter(shas.values()).items() if n > 1}
    for i, c in enumerate(contests):
        if c.phase not in BUILT:
            continue
        if i not in shas:
            why["no result sheet"] += 1
            continue
        if shas[i] in shared:
            why["the same sheet is served for another contest"] += 1
            continue
        lay = found[shas[i]]
        if not isinstance(lay, sheet.Layout):
            why["the sheet's table was not found"] += 1
            continue
        if len(lay.votes) != len(c.candidates):
            why["the sheet's rows differ in number from the candidate list"] += 1
            continue
        found_votes = reader.votes(lay.votes, int(c.sheet["clear_total_vote"]))
        if found_votes is None:
            why["the votes read do not add up to the valid votes"] += 1
            continue
        votes, corrected = found_votes
        if corrected:
            why["read after changing its least certain digit to close the gap (kept)"] += 1
        out[i] = Reading(votes, [sheet.normalized(lay, n.box) for n in lay.votes])
    return out, dict(why)


# ----------------------------------------------------------------------------------------------------------------
# Packages.


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{PHASES[e]}" for e in BUILT)]


def parse() -> None:
    districts, upazilas, contests = crawl()
    read, why = readings(contests)
    used = sorted({c.upazila for i, c in enumerate(contests) if i in read})
    print(f"{SOURCE}: read {len(read)} sheets; left out {why}")
    print(geo(districts, upazilas, used).write())
    print(registry(upazilas, used, contests, read).write())
    for phase in BUILT:
        print(election(phase, districts, upazilas, contests, read).write())


def emit() -> None:
    package.run("load", packages())


def district_area(zilla: int) -> str:
    return key("area", DISTRICT, zilla)


def upazila_area(upazila: str) -> str:
    return key("area", UPAZILA, upazila)


def upazila_polity(upazila: str) -> str:
    return key("polity", upazila)


def upazila_unit(upazila: str) -> str:
    return key("unit", UPAZILA, upazila)


def slugify(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower().replace("'", "")).strip("-")


def geo(districts: dict[int, str], upazilas: dict[str, tuple[int, str]], used: list[str]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bangladesh's districts and the upazilas that held upazila parishad elections in 2024, with each upazila's "
        "polity and reporting unit, as the Election Commission's results portal names them",
    )
    p.external.update({BANGLADESH, COUNTRY})
    for link in [zillas_url(e) for e in PHASES]:
        p.uses(raw.cached(SOURCE, link))
    p.add(
        "scheme", code=DISTRICT, polity_id=BANGLADESH, kind="administrative", rank=2, noun="district", code_system="ecs"
    )
    p.add(
        "scheme", code=UPAZILA, polity_id=BANGLADESH, kind="administrative", rank=3, noun="upazila", code_system="ecs"
    )
    p.add("scheme", code=CENTRE, polity_id=BANGLADESH, kind="precinct", rank=4, noun="polling centre")
    for zilla, name in sorted(districts.items()):
        area = district_area(zilla)
        p.add("area", id=area, scheme_code=DISTRICT)
        p.add("area_code", area_id=area, code=str(zilla), valid_from=START)
        p.add("area_parent", area_id=area, parent_id=COUNTRY, valid_from=START)
        p.name(area, f"{DISTRICTS[zilla]} District", "en", "official")
        p.name(area, f"{name} জেলা", "bn", "official", False)
    for upazila in used:
        zilla, name = upazilas[upazila]
        area = upazila_area(upazila)
        p.add("area", id=area, scheme_code=UPAZILA)
        p.add("area_code", area_id=area, code=upazila, valid_from=START)
        p.add("area_parent", area_id=area, parent_id=district_area(zilla), valid_from=START)
        p.name(area, f"{name} উপজেলা", "bn", "official")
        polity = upazila_polity(upazila)
        p.add("polity", id=polity, kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=polity, parent_id=BANGLADESH, valid_from=START)
        p.add("polity_territory", polity_id=polity, area_id=area, valid_from=START)
        p.name(polity, f"{name} উপজেলা", "bn", "official")
        p.add(
            "reporting_unit",
            id=upazila_unit(upazila),
            polity_id=BANGLADESH,
            scheme_code=UPAZILA,
            code=upazila,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=START,
        )
    return p


def party_id(name: str) -> str:
    return key("party", name)


def office_id(upazila: str, post: int) -> str:
    return key("office", upazila, post)


def registry(
    upazilas: dict[str, tuple[int, str]], used: list[str], contests: list[Contest], read: dict[int, Reading]
) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Election Commission's results portal, the upazila parishads of 2024 with their chairmen and "
        "vice-chairmen, the parties their candidates stood for, and the plurality system that elects them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BANGLADESH)
    p.add(
        "source",
        id=ECS,
        kind="official_body",
        publisher="Bangladesh Election Commission",
        title="Election results portal and candidate information portal",
        url=RESULTS + "election-result",
        license="No license stated; published by the electoral authority for public use",
        attribution="Bangladesh Election Commission",
    )
    p.artifact(raw.cached(SOURCE, schedules_url()), ECS)
    p.add("electoral_system", id=FPTP, polity_id=BANGLADESH, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Under the Upazila Parishad Act 1998 each upazila elects its chairman, a vice-chairman, and a woman "
        "vice-chairman on three separate ballots; on each, the candidate with the most votes is elected.",
        lang="en",
    )
    parties = sorted(
        {clean(c["party_name"]) for i, x in enumerate(contests) if i in read for c in x.candidates} - {INDEPENDENT}
    )
    for name in parties:
        p.add("party", id=party_id(name), kind="party", polity_id=BANGLADESH)
        p.name(party_id(name), name, "bn", "official")
    p.add(
        "election",
        id=ELECTION,
        polity_id=BANGLADESH,
        kind="local",
        date_from=PHASES[81],
        date_to=PHASES[109],
        status="held",
    )
    p.add("slug", path="bd/2024-upazila", entity_id=ELECTION, is_current=True)
    p.name(ELECTION, "2024 Bangladeshi upazila parishad elections", "en", "official")
    p.name(ELECTION, "৬ষ্ঠ উপজেলা পরিষদ নির্বাচন ২০২৪", "bn", "official", False)
    for upazila in used:
        _, name = upazilas[upazila]
        polity = upazila_polity(upazila)
        body = key("body", upazila)
        p.add("body", id=body, polity_id=polity, kind="council")
        p.name(body, f"{name} উপজেলা পরিষদ", "bn", "official")
        for post, spec in POSTS.items():
            office = office_id(upazila, post)
            p.add("office", id=office, polity_id=polity, body_id=body, kind=spec.kind)
            p.name(office, f"{name} উপজেলা পরিষদ {spec.bengali}", "bn", "official")
    return p


METHOD = (
    "From the Bangladesh Election Commission's results portal: the returning officer's result sheet for the "
    "upazila, a preliminary unofficial result. The portal gives the registered voters and the valid and cancelled "
    "votes as text; votes cast are their sum. Each candidate's votes were read by machine from the scanned sheet, "
    "and the reading was kept only because the candidates' votes add up exactly to the valid votes the portal "
    "states. The portal names the candidates on its candidate list; the sheet lists them in the ballot's Bengali "
    "alphabetical order, and they were matched in that order. The sheet declares no winner, so none is stored."
)
CENTRE_METHOD = (
    "From the Bangladesh Election Commission's results portal: each polling centre's registered voters and valid "
    "and cancelled votes for this contest, as the portal states them, with votes cast as their sum. The portal "
    "gives no candidate's votes by centre."
)


def election(
    phase: int,
    districts: dict[int, str],
    upazilas: dict[str, tuple[int, str]],
    contests: list[Contest],
    read: dict[int, Reading],
) -> Package:
    day = PHASES[phase]
    mine = [(i, c) for i, c in enumerate(contests) if c.phase == phase and i in read]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {ORDINAL[phase]} phase of Bangladesh's 2024 upazila parishad elections, on {day:%-d %B %Y}: the "
        "chairman, vice-chairman, and woman vice-chairman of each upazila by upazila, with the turnout of each "
        "polling centre, from the Election Commission's results portal",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BANGLADESH)
    p.accept(
        "accounting.votes_valid",
        "The portal states each polling centre's valid votes but no candidate's votes by centre, so each contest's "
        "non-default set of polling centres holds valid votes with no option votes; the default set of the upazila "
        "holds the candidates' votes, which add up to the valid votes exactly.",
    )
    election_id = key("election", day)
    root = f"bd/{day}-upazila"
    p.add(
        "election",
        id=election_id,
        polity_id=BANGLADESH,
        parent_id=ELECTION,
        kind="local",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=root, entity_id=election_id, is_current=True)
    p.name(election_id, f"2024 Bangladeshi upazila parishad elections, {ORDINAL[phase]} phase", "en", "official")
    artifacts: dict[str, str] = {}

    def cite(link: str) -> str:
        if link not in artifacts:
            artifacts[link] = p.artifact(raw.cached(SOURCE, link), ECS)
        return artifacts[link]

    values: list[tuple[str, str, str | None, int, int, int]] = []
    locators: list[dict[str, Any]] = []
    centres_seen: set[str] = set()
    for i, c in mine:
        reading = read[i]
        spec = POSTS[c.post]
        zilla, name = upazilas[c.upazila]
        cite(c.sheets_url)
        cite(c.candidates_url)
        document = c.document
        assert document is not None
        stored = raw.cached(SOURCE, document)
        artifact = cite(document)
        page = key("page", stored.sha256)
        image_key = f"pages/{SOURCE}/{stored.sha256}/1.png"
        width, height = page_image(stored.path, image_key)
        p.add(
            "source_page",
            id=page,
            artifact_id=artifact,
            page_number=1,
            image_key=image_key,
            width=width,
            height=height,
        )
        tag = (day, c.upazila, c.post)
        contest = key("contest", *tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office",
            office_id=office_id(c.upazila, c.post),
            district_area_id=upazila_area(c.upazila),
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        p.name(contest, f"{name} উপজেলা পরিষদ {spec.bengali} নির্বাচন ২০২৪", "bn", "official")
        p.add(
            "slug",
            path=f"{root}/{slugify(DISTRICTS[zilla])}/{c.upazila}/{spec.slug}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", *tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=ECS,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        unit = upazila_unit(c.upazila)
        for position, (candidate, votes, box) in enumerate(
            zip(c.candidates, reading.votes, reading.boxes, strict=True), start=1
        ):
            option = key("option", *tag, candidate["id"])
            person = key("person", *tag, candidate["id"])
            party = clean(candidate["party_name"])
            independent = party == INDEPENDENT
            ballot = clean(candidate["candidate_name"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if independent else party_id(party),
                ballot_position=position,
                ballot_label=ballot,
            )
            p.add("person", id=person)
            p.name(person, ballot, "bn", "official")
            p.add(
                "candidacy",
                id=key("candidacy", *tag, candidate["id"]),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=ballot,
                member_of_party_id=None if independent else party_id(party),
                is_independent=independent,
            )
            p.add("external_id", entity_id=person, scheme=f"{SOURCE}.candidate", value=str(candidate["id"]))
            values.append((result_set, unit, option, VOTES, votes, DIGITIZED))
            locators.append(
                {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": VOTES,
                    "count_round": 0,
                    "page_id": page,
                    "source_id": None,
                    "x0": box[0],
                    "y0": box[1],
                    "x1": box[2],
                    "y1": box[3],
                }
            )
        valid, invalid = int(c.sheet["clear_total_vote"]), int(c.sheet["cancel_total_vote"])
        values += [
            (result_set, unit, None, REGISTERED, int(c.sheet["total_voters"]), 0),
            (result_set, unit, None, VALID, valid, 0),
            (result_set, unit, None, INVALID, invalid, 0),
            (result_set, unit, None, CAST, valid + invalid, DERIVED),
        ]
        # The polling centres, in a second result set of their own.
        if not c.centres:
            continue
        centre_set = key("result_set", *tag, "centres")
        p.add(
            "result_set",
            id=centre_set,
            election_id=election_id,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=ECS,
            status="active",
            is_default=False,
            completeness=len(c.centres) / max(int(c.sheet["total_center"]), len(c.centres)),
        )
        p.add(
            "note",
            id=key("note", centre_set),
            entity_id=centre_set,
            kind="methodology",
            body_md=CENTRE_METHOD,
            lang="en",
        )
        for row, link in c.centres:
            cite(link)
            code = str(row["center_id"])
            centre = key("unit", CENTRE, code)
            if code not in centres_seen:
                centres_seen.add(code)
                p.add(
                    "reporting_unit",
                    id=centre,
                    polity_id=BANGLADESH,
                    scheme_code=CENTRE,
                    code=code,
                    parent_id=unit,
                    area_relation="within",
                    area_id=upazila_area(c.upazila),
                    kind="standard",
                    valid_from=day,
                )
                label = f"{row['center_serial']}. {clean(str(row['center_name']))}"
                p.name(centre, label, "bn", "official")
            valid, invalid = int(row["clear_total_vote"]), int(row["cancel_total_vote"])
            values += [
                (centre_set, centre, None, REGISTERED, int(row["total_voter"]), 0),
                (centre_set, centre, None, VALID, valid, 0),
                (centre_set, centre, None, INVALID, invalid, 0),
                (centre_set, centre, None, CAST, valid + invalid, DERIVED),
            ]
    p.frame(
        "result_value",
        pl.DataFrame(
            values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "page_id": pl.String,
                "source_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
        ),
    )
    return p


def page_image(path: Path, image_key: str) -> tuple[int, int]:
    """Render the sheet to `data/<image_key>` at 100 dpi and return its size in pixels."""
    target = DATA / image_key
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        pix = pymupdf.open(path)[0].get_pixmap(dpi=100)
        partial = target.with_suffix(".part")
        pix.save(str(partial), output="png")
        partial.replace(target)
    with Image.open(target) as image:
        return image.size
