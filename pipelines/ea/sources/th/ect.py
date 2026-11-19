"""Thailand's House of Representatives general elections of 14 May 2023 and 8 February 2026, from the Election
Commission of Thailand (ECT).

The Commission's results sites (ectreport66 for 2023, ectreport69 for 2026) serve the count as JSON feeds and as one
Excel workbook per election. The feeds hold the reference lists (provinces, constituencies, candidates, parties, party
lists) and the results by constituency: the accounting of each constituency, each candidate's votes, and each party's
list votes. Neither site publishes figures for the polling units, so the finest unit here is the constituency. The
workbook holds the same candidate and party-list votes by constituency, and the module checks the feeds against it.

Each election has one package with 400 constituency contests (one seat each, plurality) and the national party-list
contest (100 seats, one ballot), whose votes are reported by constituency.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "th.ect"
THAILAND = str(uuid5(package.NAMESPACE, "polity\x1fTH"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fTHA"))
PROVINCES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/THA/ADM1/"
    "geoBoundaries-THA-ADM1.geojson"
)
SHAPES_AS_OF = date(2025, 1, 1)  # the vintage of the province shapes
REGISTERED, BALLOTS, VALID, INVALID, BLANK = 2, 3, 4, 5, 6
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", "geoboundaries")
HOUSE = key("body", "house")
FPTP = key("electoral_system", "constituency")
LIST = key("electoral_system", "party_list")
SYSTEMS_FROM = date(2023, 5, 14)  # the first election built here; the rules were set in the 2021 amendment

CONSTITUENCY_METHOD = (
    "One seat in a single-member constituency, won by the candidate with the most votes. The Election Commission's "
    "results sites report the count by constituency, with the votes for each candidate, the valid, invalid, and blank "
    "(no-vote) ballots, and the ballots cast; it publishes no figures for the polling units. Registered voters are the "
    "electoral roll of the constituency. Valid ballots are those with a vote for a candidate, so blank ballots are "
    "counted apart from them."
)
LIST_METHOD = (
    "One hundred seats allocated from a single national list on a second ballot, with no threshold, by the largest "
    "remainder method. The Election Commission's results sites report the party-list votes by constituency, with the "
    "valid, invalid, and blank (no-vote) ballots of the party-list ballot in each constituency, and declare each "
    "party's national list votes and list seats. They publish no figures for the polling units, so each constituency "
    "is the finest unit. The seats are stored as declared, by party; the sites do not name the members elected from "
    "each list."
)


@dataclass(frozen=True)
class Edition:
    year: int
    day: date
    site: str
    until: date | None  # the day the constituency plan stopped being used
    workbook: tuple[str, ...]
    feeds: dict[str, tuple[str, ...]]

    def files(self) -> list[tuple[str, ...]]:
        return [self.workbook, *self.feeds.values()]


def _feeds(site: str, snapshots: dict[str, str]) -> dict[str, tuple[str, ...]]:
    paths = {
        "province": "refs/info_province.json",
        "constituency": "refs/info_constituency.json",
        "candidate": "refs/info_mp_candidate.json",
        "party": "refs/info_party_overview.json",
        "list": "refs/info_party_candidate.json",
        "cons": "stats/stats_cons.json",
        "parties": "stats/stats_party.json",
    }
    out: dict[str, tuple[str, ...]] = {}
    for name, path in paths.items():
        url = f"https://{site}.ect.go.th/data/{path}"
        out[name] = (url, *([snapshots[path]] if path in snapshots else []))
    return out


def _snapshot(stamp: str, site: str, path: str) -> str:
    return f"https://web.archive.org/web/{stamp}id_/https://{site}.ect.go.th/data/{path}"


EDITIONS = {
    2023: Edition(
        2023,
        date(2023, 5, 14),
        "ectreport66",
        date(2026, 2, 8),
        (
            "https://ectreport66.ect.go.th/data/excel/election_result.xlsx",
            _snapshot("20250206171506", "ectreport66", "excel/2566_election_result.xlsx"),
        ),
        _feeds(
            "ectreport66",
            {
                "refs/info_province.json": _snapshot("20250202111207", "ectreport66", "refs/info_province.json"),
                "refs/info_constituency.json": _snapshot(
                    "20250202111207", "ectreport66", "refs/info_constituency.json"
                ),
                "refs/info_mp_candidate.json": _snapshot(
                    "20250202111207", "ectreport66", "refs/info_mp_candidate.json"
                ),
                "refs/info_party_overview.json": _snapshot(
                    "20250202111207", "ectreport66", "refs/info_party_overview.json"
                ),
                "refs/info_party_candidate.json": _snapshot(
                    "20240526131109", "ectreport66", "refs/info_party_candidate.json"
                ),
                "stats/stats_cons.json": _snapshot("20250202111208", "ectreport66", "stats/stats_cons.json"),
                "stats/stats_party.json": _snapshot("20250202111208", "ectreport66", "stats/stats_party.json"),
            },
        ),
    ),
    2026: Edition(
        2026,
        date(2026, 2, 8),
        "ectreport69",
        None,
        ("https://ectreport69.ect.go.th/data/excel/election_result.xlsx",),
        _feeds("ectreport69", {}),
    ),
}


def fetch() -> None:
    raw.fetch("th.geoboundaries", PROVINCES, suffix=".geojson")
    for edition in EDITIONS.values():
        for urls in edition.files():
            if not _have(urls):
                raw.fetch(SOURCE, urls[0], suffix=".xlsx" if urls[0].endswith(".xlsx") else ".json")


def _have(urls: tuple[str, ...]) -> bool:
    for url in urls:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            continue
        return True
    return False


def artifact(urls: tuple[str, ...]) -> raw.Artifact:
    for url in urls:
        try:
            return raw.cached(SOURCE, url)
        except LookupError:
            continue
    raise LookupError(f"none of {urls} is in the raw cache")


def feed(edition: Edition, name: str) -> Any:
    return json.loads(artifact(edition.feeds[name]).path.read_text(encoding="utf-8-sig"))


def packages() -> list[str]:
    return ["th.ect/geo", "th.ect/registry", *(f"th.ect/{edition.day}" for edition in EDITIONS.values())]


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS.values():
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def province_area(iso: str) -> str:
    return key("area", "th.province", iso)


def constituency_area(edition: Edition, cons_id: str) -> str:
    return key("area", "th.constituency", f"{edition.year}:{cons_id}")


def party(name: str) -> str:
    return key("party", name)


def _provinces() -> dict[str, dict[str, Any]]:
    """The provinces by ISO 3166-2 code, with their ECT abbreviation and Thai name."""
    out: dict[str, dict[str, Any]] = {}
    for edition in EDITIONS.values():
        for row in feed(edition, "province")["province"]:
            out[f"TH-{row['province_id']}"] = {"abbr": row["prov_id"], "th": clean(row["province"])}
    return out


def _constituencies(edition: Edition) -> list[dict[str, Any]]:
    """The 400 constituencies of an election; the 2026 feed also lists one empty row per province, numbered 0."""
    rows = [row for row in feed(edition, "constituency") if int(row["cons_no"]) > 0]
    if len(rows) != 400:
        raise ValueError(f"{edition.year}: {len(rows)} constituencies")
    return rows


def geo() -> Package:
    p = Package(
        "th.ect/geo",
        SOURCE,
        "Thailand's 77 provinces from geoBoundaries and the 400 House constituencies of the 2023 and 2026 elections",
    )
    p.external |= {THAILAND, COUNTRY}
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Thailand ADM1 (provinces)",
        url="https://www.geoboundaries.org/",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="geoBoundaries (William & Mary geoLab)",
    )
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Election Commission of Thailand",
        title="Results sites of the 2023 and 2026 House of Representatives elections",
        url="https://www.ect.go.th/",
        license="Official election results of the Kingdom of Thailand, published for public use",
        license_url="https://www.ect.go.th/",
        attribution="Election Commission of Thailand, ect.go.th",
    )
    shapes = raw.cached("th.geoboundaries", PROVINCES)
    p.artifact(shapes, SHAPES_ID)
    for edition in EDITIONS.values():
        for urls in edition.files():
            p.artifact(artifact(urls), SOURCE_ID)
    for code, rank, kind, noun in (
        ("th.province", 1, "administrative", "province"),
        ("th.constituency", 2, "electoral_district", "constituency"),
    ):
        p.add(
            "scheme",
            code=code,
            polity_id=THAILAND,
            kind=kind,
            rank=rank,
            noun=noun,
            code_system="iso_3166_2" if code == "th.province" else None,
        )
    p.add(
        "boundary_set",
        id=key("boundary_set", "th.province", SHAPES_AS_OF.year),
        scheme_code="th.province",
        vintage=str(SHAPES_AS_OF.year),
        as_of=SHAPES_AS_OF,
    )
    names = _provinces()
    features = {f["properties"]["shapeISO"]: f for f in json.loads(shapes.path.read_bytes())["features"]}
    if set(features) != set(names):
        raise ValueError(f"province shapes and ECT provinces differ: {sorted(set(features) ^ set(names))}")
    english: dict[str, str] = {}
    for iso, row in sorted(names.items()):
        feature = features[iso]
        id = province_area(iso)
        english[iso] = re.sub(r" Province$", "", feature["properties"]["shapeName"])
        p.add("area", id=id, scheme_code="th.province")
        p.add("area_code", area_id=id, code=iso)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, row["th"], "th", "official")
        p.name(id, english[iso], "en", "english", False)
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
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
    by_abbr = {row["abbr"]: iso for iso, row in names.items()}
    for edition in EDITIONS.values():
        for row in _constituencies(edition):
            iso = by_abbr[row["prov_id"]]
            id = constituency_area(edition, row["cons_id"])
            p.add("area", id=id, scheme_code="th.constituency")
            p.add("area_code", area_id=id, code=row["cons_id"], valid_from=edition.day, valid_to=edition.until)
            p.add(
                "area_parent", area_id=id, parent_id=province_area(iso), valid_from=edition.day, valid_to=edition.until
            )
            p.name(id, f"{names[iso]['th']} เขต {row['cons_no']}", "th", "official")
            p.name(id, f"{english[iso]} constituency {row['cons_no']}", "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        "th.ect/registry",
        SOURCE,
        "Thai parties, the House of Representatives, and the electoral systems of its 2023 and 2026 elections",
        depends=["th.ect/geo"],
    )
    p.external |= {THAILAND, COUNTRY}
    for edition in EDITIONS.values():
        for urls in edition.files():
            p.uses(artifact(urls))
    p.add("body", id=HOUSE, polity_id=THAILAND, kind="lower_house")
    p.name(HOUSE, "House of Representatives", "en", "official")
    p.name(HOUSE, "สภาผู้แทนราษฎร", "th", "native", False)
    for id, rules, method in (
        (FPTP, {"formula": "fptp"}, CONSTITUENCY_METHOD),
        (LIST, {"formula": "hare"}, LIST_METHOD),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=THAILAND,
            valid_from=SYSTEMS_FROM,
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names: dict[str, str | None] = {}
    for edition in EDITIONS.values():
        used = _used_parties(edition)
        for row in feed(edition, "party"):
            name = clean(row["name"])
            if int(row["id"]) in used:
                names[name] = clean(row.get("abbr")) or None
    for name, abbr in sorted(names.items()):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=THAILAND)
        p.name(id, name, "th", "official")
        if abbr and abbr != name:
            p.name(id, abbr, "th", "abbreviation", False)
    return p


def _used_parties(edition: Edition) -> set[int]:
    used = {int(row["mp_app_party_id"]) for row in feed(edition, "candidate")}
    used |= {int(row["party_id"]) for row in feed(edition, "parties")["result_party"]}
    return used


def _whole(cell: object) -> bool:
    return isinstance(cell, str) and cell.strip().isdigit()


def _cells(edition: Edition) -> tuple[dict[tuple[str, int, int], tuple[str, int]], dict[tuple[str, int, int], int]]:
    """The workbook's candidate votes by province, constituency, and number, and its list votes by party number."""
    path = artifact(edition.workbook).path
    candidates: dict[tuple[str, int, int], tuple[str, int]] = {}
    lists: dict[tuple[str, int, int], int] = {}
    for row in pl.read_excel(path, sheet_id=1, has_header=False, engine="calamine").iter_rows():
        if _whole(row[1]) and _whole(row[2]) and _whole(row[5]):
            candidates[(clean(row[0]), int(row[1]), int(row[2]))] = (clean(row[4]), int(row[5]))
    for row in pl.read_excel(path, sheet_id=2, has_header=False, engine="calamine").iter_rows():
        if _whole(row[1]) and _whole(row[2]) and _whole(row[4]):
            lists[(clean(row[0]), int(row[1]), int(row[2]))] = int(row[4])
    return candidates, lists


def _rows(edition: Edition) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for province in feed(edition, "cons")["result_province"]:
        for cons in province["constituencies"]:
            if cons["cons_id"].endswith("_0"):
                if cons["valid_votes"] or cons["turn_out"] or cons["party_list_turn_out"]:
                    raise ValueError(f"{cons['cons_id']} holds votes")
                continue
            yield province, cons


def election(edition: Edition) -> Package:
    year, day = edition.year, edition.day
    p = Package(
        f"th.ect/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} Thai House of Representatives election by constituency from the Election Commission",
        depends=["th.ect/geo", "th.ect/registry"],
    )
    p.external |= {THAILAND, COUNTRY}
    for urls in edition.files():
        p.uses(artifact(urls))
    path = f"th/{day}-general"
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=THAILAND, kind="general", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{year} Thai general election", "en", "official")
    p.name(election_id, f"การเลือกตั้งทั่วไป พ.ศ. {year + 543}", "th", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)

    names = _provinces()
    by_abbr = {row["abbr"]: iso for iso, row in names.items()}
    parties = {int(row["id"]): row for row in feed(edition, "party")}
    cons_rows = {row["cons_id"]: row for row in _constituencies(edition)}
    candidates = {row["mp_app_id"]: row for row in feed(edition, "candidate")}
    book_candidates, book_lists = _cells(edition)
    sp = feed(edition, "parties")
    declared_votes = {
        cand["mp_app_id"]: int(cand["mp_app_vote"]) for row in sp["result_party"] for cand in row["candidates"]
    }
    cons_total = feed(edition, "cons")
    problems: list[str] = []
    gaps: list[tuple[str, int]] = []

    # the national party-list contest
    list_contest = key("contest", day, "list")
    list_set = key("result_set", day, "list")
    p.add(
        "contest",
        id=list_contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=HOUSE,
        district_area_id=COUNTRY,
        magnitude=100,
        round_number=1,
        electoral_system_id=LIST,
        default_result_kind="counted",
    )
    p.name(list_contest, f"{year} Thai House of Representatives election, party list", "en", "official")
    p.name(list_contest, "สภาผู้แทนราษฎร แบบบัญชีรายชื่อ", "th", "native", False)
    p.add("slug", path=f"{path}/party-list", entity_id=list_contest, is_current=True)
    p.add(
        "result_set",
        id=list_set,
        election_id=election_id,
        contest_id=list_contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", list_set), entity_id=list_set, kind="methodology", body_md=LIST_METHOD, lang="en")
    list_options: dict[int, str] = {}
    party_no: dict[int, int] = {}
    on_ballot = {int(row["party_id"]) for _, cons in _rows(edition) for row in cons["result_party"]}
    for row in sp["result_party"]:
        pid = int(row["party_id"])
        meta = parties[pid]
        if pid not in on_ballot:
            if int(row["party_vote"]) or int(row["party_list_count"]):
                raise ValueError(f"party {pid} has list votes and no place on the ballot")
            continue  # a party with no list votes in any constituency
        if not meta["party_no"]:
            if int(row["party_vote"]) or int(row["party_list_count"]):
                raise ValueError(f"party {pid} has list votes and no ballot number")
            continue  # a party that ran in constituencies only has no place on the list ballot
        no = int(meta["party_no"])
        party_no[pid] = no
        option = list_options[pid] = key("option", day, "list", no)
        p.add(
            "contest_option",
            id=option,
            contest_id=list_contest,
            kind="list",
            party_id=party(clean(meta["name"])),
            ballot_position=no,
            ballot_label=clean(meta["name"]),
        )
        p.add("declared", result_set_id=list_set, option_id=option, measure=1, value=int(row["party_vote"]))
        seats = int(row["party_list_count"])
        if seats:
            p.add("outcome", result_set_id=list_set, option_id=option, kind="won", seats=seats)
    if sum(int(row["party_list_count"]) for row in sp["result_party"]) != 100:
        problems.append("the declared list seats do not add up to 100")
    by_number = {no: pid for pid, no in party_no.items()}
    for entry in feed(edition, "list"):
        pid = by_number.get(int(entry["party_no"]))
        if pid is None:
            continue
        for index, member in enumerate(entry["party_list_candidates"], 1):
            tag = f"{day}:list:{entry['party_no']}:{index}"
            person = key("person", tag)
            p.add("person", id=person)
            p.name(person, clean(member["name"]), "th", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=list_options[pid],
                role="list_member",
                list_position=int(member["list_no"]),
                ballot_name=clean(member["name"]),
                member_of_party_id=party(clean(parties[pid]["name"])),
                is_independent=False,
            )
    for measure, field in (
        (BALLOTS, "party_list_turn_out"),
        (VALID, "party_list_valid_votes"),
        (INVALID, "party_list_invalid_votes"),
        (BLANK, "party_list_blank_votes"),
    ):
        p.add("declared", result_set_id=list_set, option_id=None, measure=measure, value=int(cons_total[field]))
    p.add(
        "declared",
        result_set_id=list_set,
        option_id=None,
        measure=REGISTERED,
        value=int(feed(edition, "province")["total_registered_vote"]),
    )

    def value(result_set: str, unit: str, option: str | None, measure: int, count: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=count,
        )

    for province, cons in _rows(edition):
        cons_id = cons["cons_id"]
        meta = cons_rows[cons_id]
        iso = by_abbr[meta["prov_id"]]
        area = constituency_area(edition, cons_id)
        unit = key("unit", day, cons_id)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=THAILAND,
            scheme_code="th.constituency",
            code=f"{day}:{cons_id}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        number = int(meta["cons_no"])
        # the constituency contest
        contest = key("contest", day, cons_id)
        result_set = key("result_set", day, cons_id)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=area,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        label = f"{names[iso]['th']} เขต {number}"
        p.name(contest, label, "th", "native", False)
        p.name(
            contest,
            f"{year} Thai House of Representatives election, {_english(iso)} constituency {number}",
            "en",
            "official",
        )
        p.add("slug", path=f"{path}/{changeset.slug(_english(iso))}-{number}", entity_id=contest, is_current=True)
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
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=CONSTITUENCY_METHOD,
            lang="en",
        )
        for field, kind in (("note_cons", "constituency ballot"), ("note_candidate_cons", "candidate count")):
            text = clean(cons.get(field))
            if text:
                p.add(
                    "note",
                    id=key("note", result_set, field),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The Election Commission notes on the {kind}: {text}",
                    lang="en",
                )
        ranked = sorted(cons["candidates"], key=lambda c: int(c["mp_app_rank"]))
        for cand in ranked:
            info = candidates[cand["mp_app_id"]]
            no = int(info["mp_app_no"])
            pid = int(cand["party_id"])
            if int(info["mp_app_party_id"]) != pid:
                problems.append(f"{cand['mp_app_id']}: the candidate list and the count disagree on the party")
            name = clean(info["mp_app_name"])
            member = party(clean(parties[pid]["name"]))
            option = key("option", day, cons_id, no)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_position=no,
                ballot_label=name,
            )
            person = key("person", f"{day}:{cand['mp_app_id']}")
            p.add("person", id=person)
            p.name(person, name, "th", "official")
            candidacy = key("candidacy", f"{day}:{cand['mp_app_id']}")
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=member,
                is_independent=False,
            )
            votes = int(cand["mp_app_vote"])
            won = int(cand["mp_app_rank"]) == 1
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if won else "lost",
            )
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=1,
                value=declared_votes[cand["mp_app_id"]],
            )
            value(result_set, unit, option, 1, votes)
            if declared_votes[cand["mp_app_id"]] != votes:
                problems.append(
                    f"{cand['mp_app_id']}: {votes} votes in the constituency feed, {declared_votes[cand['mp_app_id']]} in the party feed"
                )
            book = book_candidates.get((names[iso]["th"], number, no))
            if book is None and votes:
                problems.append(f"{cand['mp_app_id']} is not in the workbook")
            elif book is not None and book[1] != votes:
                gaps.append(("candidate", abs(votes - book[1])))
        for measure, amount in (
            (REGISTERED, meta["registered_vote"]),
            (BALLOTS, cons["turn_out"]),
            (VALID, cons["valid_votes"]),
            (INVALID, cons["invalid_votes"]),
            (BLANK, cons["blank_votes"]),
        ):
            value(result_set, unit, None, measure, int(amount))
        # the party-list votes of the constituency
        for row in cons["result_party"]:
            pid = int(row["party_id"])
            amount = int(row["party_list_vote"])
            value(list_set, unit, list_options[pid], 1, amount)
            book = book_lists.get((names[iso]["th"], number, party_no[pid]))
            if book is None:
                problems.append(f"{cons_id} party {party_no[pid]} is not in the workbook")
            elif book != amount:
                gaps.append(("list", abs(amount - book)))
        for measure, amount in (
            (REGISTERED, meta["registered_vote"]),
            (BALLOTS, cons["party_list_turn_out"]),
            (VALID, cons["party_list_valid_votes"]),
            (INVALID, cons["party_list_invalid_votes"]),
            (BLANK, cons["party_list_blank_votes"]),
        ):
            value(list_set, unit, None, measure, int(amount))
    p.accept(
        "accounting.ballots_sum",
        "The Commission's own figures: in some constituencies the valid, invalid, and blank ballots it reports do not "
        "add up to the ballots cast, by a few ballots.",
    )
    if year == 2023:
        p.accept(
            "accounting.votes_valid",
            "The Commission's own figures: in some constituencies the votes it reports for the candidates or lists do not "
            "add up to its valid ballots, by a few votes.",
        )
        p.accept(
            "accounting.declared",
            "The Commission's own figures: a few of its national totals differ by a few votes from the sum of its "
            "constituency figures.",
        )
    if problems:
        raise ValueError(f"{day}: " + "; ".join(problems[:20]))
    if gaps:
        by_kind = {kind: [gap for k, gap in gaps if k == kind] for kind in ("candidate", "list")}
        print(
            f"{day}: the workbook differs from the feeds in {len(gaps)} cells: "
            + ", ".join(f"{len(v)} {k} (up to {max(v)})" for k, v in by_kind.items() if v),
            flush=True,
        )
        p.add(
            "note",
            id=key("note", election_id, "workbook"),
            entity_id=election_id,
            kind="caveat",
            body_md=(
                "The results feeds and the Excel workbook of the Election Commission's results site differ in "
                f"{len(by_kind['candidate'])} candidate cells and {len(by_kind['list'])} party-list cells, by at most "
                f"{max(gap for _, gap in gaps)} votes each. The feeds are stored."
            ),
            lang="en",
        )
    return p


_ENGLISH: dict[str, str] = {}


def _english(iso: str) -> str:
    if not _ENGLISH:
        shapes = raw.cached("th.geoboundaries", PROVINCES)
        for feature in json.loads(shapes.path.read_bytes())["features"]:
            _ENGLISH[feature["properties"]["shapeISO"]] = re.sub(r" Province$", "", feature["properties"]["shapeName"])
    return _ENGLISH[iso]
