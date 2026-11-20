"""Islamabad Capital Territory's local government elections of 30 November 2015, from the Election Commission of
Pakistan's notification of 5 December 2015 naming the candidates returned in each of the 50 union councils.

The notification is a 51-page scan. It names the chairman and vice chairman returned in each union council, the member
for each general and women's ward, and the peasant or worker, youth, and non-Muslim members, but prints no votes and no
party. Each contest therefore carries its winner only. The scan was read with two passes (a neural line reader and
Tesseract on each cell); where they disagreed the cell was read from the page image. Three pages that the scanner
skewed too far for the table grid were read from the page image alone. The result is `ecp_lg_ict.json`, which holds
every row with its page and the box of its name cell, in pixels of the page rendered at 200 dpi (straightened for the
pages whose grid was detected)."""

import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "pk.ecp_lg_ict"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
DAY = date(2015, 11, 30)
PAGE = "https://web.archive.org/web/20251123100438id_/https://ecp.gov.pk/local-government-elections-ict-2015"
NOTIFICATION = "https://web.archive.org/web/20240703154524id_/https://ecp.gov.pk/storage/files/1/111.pdf"
DATA = Path(__file__).with_suffix(".json")
UNION_SCHEME = "pk.is_union_council"
WARD_SCHEME = "pk.is_ward"
DPI = 200


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECP_SOURCE = key("source", "ecp-ict-2015")
SYSTEM = key("electoral_system", "fptp")
TICKET_SYSTEM = key("electoral_system", "ticket", "fptp")
ELECTION = key("election", "2015")
ROOT = "pk/2015-11-30-islamabad-local"

LABELS = {
    "general": "general member",
    "woman": "women's member",
    "peasant_worker": "peasant or worker member",
    "youth": "youth member",
    "non_muslim": "non-Muslim member",
}


def load() -> tuple[dict[int, str], list[dict[str, Any]]]:
    data = json.loads(DATA.read_text())
    return {int(k): v for k, v in data["councils"].items()}, data["rows"]


def fetch() -> None:
    raw.fetch(SOURCE, NOTIFICATION, suffix=".pdf")
    raw.fetch(SOURCE, PAGE, suffix=".html")


def council_area(number: int) -> str:
    return key("area", UNION_SCHEME, number)


def ward_area(number: int, kind: str, ward: int) -> str:
    return key("area", WARD_SCHEME, number, kind, ward)


def council_body(number: int) -> str:
    return key("body", number)


def chairman_office(number: int) -> str:
    return key("office", "chairman", number)


def council_name(number: int, names: dict[int, str]) -> str:
    return f"Union Council {number}, {names[number]}"


def ward_label(kind: str, ward: int) -> str:
    return f"{'Woman' if kind == 'woman' else 'General'} Ward {ward}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", f"{SOURCE}/{DAY.isoformat()}"]


def _wards(rows: list[dict[str, Any]]) -> dict[tuple[int, str, int], None]:
    return {(r["uc"], r["seat"], r["ward"]): None for r in rows if r["seat"] in ("general", "woman")}


def geo() -> Package:
    names, rows = load()
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Islamabad's 50 union councils of 2015 and their general and women's wards, as the Election Commission's "
        "notification of the results names them",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(PAKISTAN)
    p.uses(raw.cached(SOURCE, NOTIFICATION))
    p.add(
        "scheme",
        code=UNION_SCHEME,
        polity_id=PAKISTAN,
        kind="administrative",
        rank=4,
        noun="union council",
        kinds=None,
        code_system=None,
    )
    p.add(
        "scheme", code=WARD_SCHEME, polity_id=PAKISTAN, kind="electoral_district", rank=5, noun="ward", code_system=None
    )
    for number in sorted(names):
        area = council_area(number)
        p.add("area", id=area, scheme_code=UNION_SCHEME)
        p.add("area_code", area_id=area, code=str(number), valid_from=DAY)
        p.add("area_kind", area_id=area, kind="union_council", source_id=ECP_SOURCE)
        p.name(area, council_name(number, names), "en", "official")
    for number, kind, ward in _wards(rows):
        area = ward_area(number, kind, ward)
        p.add("area", id=area, scheme_code=WARD_SCHEME)
        p.add("area_parent", area_id=area, parent_id=council_area(number), valid_from=DAY)
        p.name(area, f"{ward_label(kind, ward)}, {council_name(number, names)}", "en", "official")
    return p


def registry() -> Package:
    names, _ = load()
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Election Commission of Pakistan's notification of Islamabad's 2015 local government results, the "
        "union councils and chairmanships it fills, and their electoral systems",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Local Government Elections ICT 2015: notification of returned candidates (5 December 2015)",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    p.artifact(raw.cached(SOURCE, NOTIFICATION), ECP_SOURCE)
    for number in sorted(names):
        body = council_body(number)
        p.add("body", id=body, polity_id=PAKISTAN, kind="council")
        p.name(body, council_name(number, names), "en", "official")
        office = chairman_office(number)
        p.add("office", id=office, polity_id=PAKISTAN, body_id=body, kind="mayor")
        p.name(office, f"Chairman of {council_name(number, names)}", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add("electoral_system", id=TICKET_SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", TICKET_SYSTEM),
        entity_id=TICKET_SYSTEM,
        kind="methodology",
        body_md="Under the Islamabad Capital Territory Local Government Act 2015, a union council's chairman and vice "
        "chairman are elected together on one joint ticket, and the ticket with the most votes wins.",
        lang="en",
    )
    return p


METHOD = (
    "From the Election Commission of Pakistan's notification of 5 December 2015 naming the candidates returned in "
    "the Islamabad Capital Territory local government elections of 30 November 2015. The notification prints no votes "
    "and no party, and does not say which seats were won unopposed, so each contest stores its winner only. The "
    "names were read from a scan of the notification."
)


def election() -> Package:
    names, rows = load()
    p = Package(
        f"{SOURCE}/{DAY.isoformat()}",
        SOURCE,
        "Islamabad's local government elections of 30 November 2015: the chairman and vice chairman and every "
        "member returned to the 50 union councils, from the Election Commission's notification",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external.add(PAKISTAN)
    p.uses(raw.cached(SOURCE, NOTIFICATION))
    p.add("election", id=ELECTION, polity_id=PAKISTAN, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=ROOT, entity_id=ELECTION, is_current=True)
    p.name(ELECTION, "2015 Islamabad local government elections", "en", "official")
    title = "2015 Islamabad local government elections"
    by_ticket: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r["seat"] in ("chairman", "vice_chairman"):
            by_ticket.setdefault(r["uc"], {})[r["seat"]] = r
    contests: list[tuple[tuple[Any, ...], list[dict[str, Any]], dict[str, Any], str, str, str, str]] = []
    for number in sorted(by_ticket):
        members = [by_ticket[number]["chairman"], by_ticket[number]["vice_chairman"]]
        contests.append(
            (
                (number, "chairman"),
                members,
                {"kind": "office", "office_id": chairman_office(number), "district_area_id": council_area(number)},
                TICKET_SYSTEM,
                f"{council_name(number, names)}, chairman and vice chairman",
                f"{ROOT}/uc-{number}/chairman",
                "ticket",
            )
        )
    for r in rows:
        number, seat, ward = r["uc"], r["seat"], r["ward"]
        if seat in ("chairman", "vice_chairman"):
            continue
        if seat in ("general", "woman"):
            area = ward_area(number, seat, ward)
            label = f"{council_name(number, names)}, {ward_label(seat, ward)}"
            path = f"{ROOT}/uc-{number}/{'general' if seat == 'general' else 'woman'}-ward-{ward}"
            tag: tuple[Any, ...] = (number, seat, ward)
        else:
            area = council_area(number)
            label = f"{council_name(number, names)}, {LABELS[seat]} seat"
            path = f"{ROOT}/uc-{number}/{seat.replace('_', '-')}"
            tag = (number, seat)
        contests.append(
            (
                tag,
                [r],
                {"kind": "body_seats", "body_id": council_body(number), "district_area_id": area},
                SYSTEM,
                label,
                path,
                "candidate",
            )
        )
    paths: set[str] = set()
    for tag, members, contest, system, label, path, option_kind in contests:
        if path in paths:
            raise ValueError(f"two contests share the path {path}")
        paths.add(path)
        contest_id = key("contest", *tag)
        p.add(
            "contest",
            id=contest_id,
            election_id=ELECTION,
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
            **contest,
        )
        p.name(contest_id, f"{title}, {label}", "en", "official")
        p.add("slug", path=path, entity_id=contest_id, is_current=True)
        result_set = key("result_set", *tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=ECP_SOURCE,
            status="active",
            is_default=True,
            completeness=0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=ECP_SOURCE,
            locator={
                "url": NOTIFICATION,
                "pages": sorted({m["page"] for m in members}),
                "boxes": [{"page": m["page"], "box": m["box"], "dpi": DPI} for m in members],
            },
        )
        option = key("option", *tag)
        ballot = " / ".join(m["name"] for m in members)
        p.add("contest_option", id=option, contest_id=contest_id, kind=option_kind, ballot_label=ballot)
        for index, member in enumerate(members):
            person = key("person", *tag, index)
            p.add("person", id=person)
            p.name(person, member["name"], "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", *tag, index),
                person_id=person,
                option_id=option,
                role="running_mate" if member["seat"] == "vice_chairman" else "head",
                ballot_name=member["name"],
            )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    return p


def parse() -> None:
    for build in (registry, geo, election):
        print(build().write())


def emit() -> None:
    package.run("load", packages())
