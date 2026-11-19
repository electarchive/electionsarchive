"""Sindh's local government elections of 2015, from the Election Commission of Pakistan's notifications of the
members returned to the union councils and union committees, the town and municipal committees, and the district
councils.

Sindh polled in three phases: the districts of Sukkur and Larkana divisions on 31 October 2015, those of Hyderabad,
Mirpurkhas, and Shaheed Benazirabad divisions on 19 November, and Karachi's six districts on 5 December. The
Commission notified the returned candidates of each district separately; the source is one scanned volume that binds
the notifications of 25 of Sindh's 29 districts together (Sanghar, Thatta, and Karachi's district council outside
Malir and West are not in it). The notifications name each returned member, the council, the ward, the category of
seat, and the party, but print no votes, so each contest carries its winner only.

The scan has no text layer. A multimodal model transcribed every page, with the page each row stands on, into
`ecp_lg_sindh.json`; every page's rows were counted against the serial numbers it prints. The module reads that
transcription, puts each row in its council from the notification's tables, and checks each council's structure
(four wards to a union council or committee, one chairman and vice chairman ticket, one member of the district
council) before it builds anything."""

import json
import re
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "pk.ecp_lg_sindh"
PAKISTAN = str(uuid5(package.NAMESPACE, "polity\x1fPK"))
VOLUME = "https://pakvoter.org/wp-content/uploads/files/files/files/lg_sindh_punjab/Sindh%20LG%20Results%202015.pdf"
TRANSCRIPTION: list[list] = json.loads((Path(__file__).parent / "ecp_lg_sindh.json").read_text())

UNION_SCHEME = "pk.sd_union_council"
WARD_SCHEME = "pk.sd_ward"
NOUNS = {
    "union_council": "union council",
    "union_committee": "union committee",
    "town_committee": "town committee",
    "municipal_committee": "municipal committee",
}
LABELS = {
    "union_council": "Union Council",
    "union_committee": "Union Committee",
    "town_committee": "Town Committee",
    "municipal_committee": "Municipal Committee",
}
PREFIXES = {
    "union_council": "uc",
    "union_committee": "union-committee",
    "town_committee": "tc",
    "municipal_committee": "mc",
}
DAYS = [date(2015, 10, 31), date(2015, 11, 19), date(2015, 12, 5)]
PHASES = {DAYS[0]: "first", DAYS[1]: "second", DAYS[2]: "third"}
MONTHS = {"october": 10, "november": 11, "december": 12}
# The notifications spell some districts their own way.
DISTRICTS = {
    "Kashmore @ Kandhkot": "Kashmore",
    "Kamber-Shahdadkot": "Kamber Shahdadkot",
    "Korangi, Karachi": "Karachi Korangi",
    "Malir, Karachi": "Karachi Malir",
}

# Party cells as printed, folded, to the party's abbreviation. Cells that name an election symbol instead of a party
# (independents drew symbols such as the bottle or the goat) are left without a party.
PARTIES = {
    "PPPP": ("Pakistan Peoples Party Parliamentarians", ["pppp", "ppp", "pppw"]),
    "MQM": ("Muttahida Qaumi Movement", ["mqm", "mom"]),
    "PML-N": ("Pakistan Muslim League (N)", ["pmln", "pmna"]),
    "PML-F": ("Pakistan Muslim League (F)", ["pmlf"]),
    "PML-Q": ("Pakistan Muslim League (Q)", ["pmlq"]),
    "PTI": ("Pakistan Tehreek-e-Insaf", ["pti"]),
    "JUI-F": ("Jamiat Ulema-e-Islam (F)", ["juif", "juiff", "jui"]),
    "JUI-P": ("JUI-P", ["juip"]),
    "JI": (
        "Jamaat-e-Islami",
        ["ji", "jip", "jamateislami", "jamatislami", "jamiateislami", "jamaatislami", "jamiatislami", "jislami"]
        + ["jamaiteislami", "jamaitislamsindh"],
    ),
    "ANP": ("Awami National Party", ["anp"]),
    "PPP-SB": ("Pakistan Peoples Party (Shaheed Bhutto)", ["pppsb"]),
    "PML-SB": ("PML (S.B)", ["pmlsb"]),
    "SUP": ("Sindh United Party", ["sup"]),
    "QAT": ("Qaumi Awami Tehreek", ["qat", "qoomiawamitehreek"]),
    "MWM": ("Majlis Wahdat-e-Muslimeen", ["mwm"]),
    "PRHP": ("Pakistan Rah-e-Haq Party", ["prhp", "pakistanrahehaq", "rahehaqparty"]),
    "JUP-N": ("Jamiat Ulema-e-Pakistan (Noorani)", ["jupn"]),
    "JUP-F": ("JUP (F)", ["jupf"]),
    "AIP": ("AIP", ["aip"]),
    "STP": ("STP", ["stp"]),
    "PMA": ("Pak Muslim Alliance", ["pakmuslimalliance"]),
    "MP": ("Mustaqbil Pakistan", ["mustaqbilpakistan"]),
    "PP": ("Pasban Pakistan", ["pasbanpakistan"]),
}
INDEPENDENT = {
    "independent", "indp", "indep", "ind", "independet", "indepndent", "independend", "indepentent", "indpendent", "indepenedent",
    "azad",
}  # fmt: skip
SYMBOLS = {
    "bottle", "arrow", "goat", "suitcase", "rose", "hen", "bulb", "watermelon", "comb", "fist", "sexaphon",
    "sherebangal",
}  # fmt: skip
# Party cells whose print is plainly a slip for another party's abbreviation; each such result set says so.
SLIPS = {"pppw": "PPPW", "mom": "MOM", "pmna": "PMNA"}
PARTY_OF = {alias: abbr for abbr, (_, aliases) in PARTIES.items() for alias in aliases}


def fetch() -> None:
    raw.fetch(SOURCE, VOLUME, suffix=".pdf")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _case(text: str) -> str:
    text = _space(text).strip(" .,;:-/")
    if not (text.isupper() and len(text) > 3):
        return text
    titled = re.sub(r"(?<=[A-Za-z])'S\b", "'s", text.title())
    return re.sub(r"\b(?:Ii|Iii|Iv|Vi|Vii|Viii|Ix)\b", lambda m: m.group().upper(), titled)


def _day(text: str) -> date:
    found = re.search(r"(\d+)\s*(?:st|nd|rd|th)?\W*\s*(october|november|december)\W*\s*(\d{4})", text, re.IGNORECASE)
    if found is None:
        raise ValueError(f"no polling day in {text!r}")
    return date(int(found.group(3)), MONTHS[found.group(2).lower()], int(found.group(1)))


def party(text: str | None) -> tuple[str | None, bool]:
    """The party's abbreviation, or None, and whether the candidate stood as an independent."""
    if text is None:
        return None, False
    folded = _fold(text)
    if folded in INDEPENDENT:
        return None, True
    if folded in SYMBOLS:
        return None, False
    if folded in PARTY_OF:
        return PARTY_OF[folded], False
    raise ValueError(f"unknown party cell {text!r}")


ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50}


def _roman(text: str) -> int:
    total = 0
    for this, after in zip(text, [*text[1:], ""], strict=True):
        value = ROMAN[this]
        total += -value if after and ROMAN[after] > value else value
    return total


def ward_number(text: str) -> int:
    found = re.search(r"\d+", text)
    if found:
        return int(found.group())
    roman = re.search(r"\b([ivxl]+)\b", text, re.IGNORECASE)
    if roman:
        return _roman(roman.group(1).lower())
    raise ValueError(f"no ward number in {text!r}")


class Council(NamedTuple):
    district: str
    kind: str
    number: int | None
    name: str


class Seat(NamedTuple):
    day: date
    district: str
    tier: str
    council: Council
    seat: str
    ward: int | None
    name: str | None
    party: str | None
    remark: str | None
    page: int


SEAT_KINDS = (
    (re.compile(r"\bv(?:ice)?\b|^v\W|vice", re.IGNORECASE), "vice_chairman"),
    (re.compile(r"chairm", re.IGNORECASE), "chairman"),
    (re.compile(r"distt?|distirct", re.IGNORECASE), "district"),
)
UNION = re.compile(r"\b(?:union|u\.?\s?c\b|uc\b|uc-|rc-)|^\d+\s*uc\b|^uc", re.IGNORECASE)
COMMITTEE = re.compile(
    r"\b(?:m\.?\s*-?\s*c\b|mc\b|(?:municipal|muncipal|minicipal)\s+committee|t\.?\s*c\b|tc\b|town\s+committee)",
    re.IGNORECASE,
)


def _seat_kind(text: str | None) -> str:
    if text is None:
        return "general"
    for pattern, kind in SEAT_KINDS:
        if pattern.search(text):
            return kind
    return "general"


def _tier(text: str, karachi: bool) -> str | None:
    lowered = text.lower()
    if "corporation" in lowered:
        return "corporation"
    if re.search(r"municipal|muncipal|minicipal", lowered):
        return "municipal"
    if "town" in lowered:
        return "town"
    if "union council" in lowered or ("district" in lowered and not karachi):
        return "district"
    return None


def _union(district: str, kind: str, text: str) -> Council:
    text = _space(re.sub(r"^name of\s+", "", text, flags=re.IGNORECASE))
    numbers = re.findall(r"\d+", text)
    number = int(numbers[0]) if numbers else None
    name = text
    if numbers:
        name = name.replace(numbers[0], " ", 1)
    name = re.sub(
        r"\b(?:union\s+(?:council|concil|coucil|committee)|u\.?\s?c|rc|no)\b\.?|[()#]", " ", name, flags=re.IGNORECASE
    )
    name = _case(re.sub(r"^[\s.,:-]+|[\s.,:-]+$", "", _space(name)))
    return Council(district, kind, number, name)


def _committee(district: str, kind: str, text: str) -> Council:
    name = _space(text)
    name = re.sub(
        r"^(?:municipal|muncipal|minicipal|town)\s+committee\b|^m\.?\s*-?\s*c\b\.?|^t\.?\s*c\b\.?",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"^[\s.,:-]*(?:no\.?)?[\s.,:-]*\d+\b", "", name, flags=re.IGNORECASE)
    return Council(district, kind, None, _case(re.sub(r"^[\s.,:-]+", "", name)))


def _committee_kind(text: str, tier: str) -> str:
    if re.search(r"town|^t\.?\s*c\b", text, re.IGNORECASE):
        return "town_committee"
    if re.search(r"municipal|muncipal|minicipal|^m\.?\s*-?\s*c\b", text, re.IGNORECASE):
        return "municipal_committee"
    return "town_committee" if tier == "town" else "municipal_committee"


def seats() -> list[Seat]:
    """Every row of the transcription in its council, in page order. Rows that name no member (a postponed, withheld,
    or vacant seat) are kept with no name."""
    out: list[Seat] = []
    day, district, tier, karachi = DAYS[0], "", "district", False
    for row in TRANSCRIPTION:
        kind = row[0]
        if kind == "n":
            _, page, printed, _, _, poll, _ = row
            day, karachi = _day(poll), printed.startswith("Karachi") or "Karachi" in printed
            district = DISTRICTS.get(printed, printed)
            tier = "karachi" if karachi else "district"
            continue
        if kind == "h":
            found = _tier(row[2], karachi)
            if found is not None:
                tier = found
            continue
        if kind != "s":
            continue
        _, page, council_text, ward_text, seat_text, name, party_text, remark = row
        seat = _seat_kind(seat_text)
        if UNION.search(council_text) and not COMMITTEE.search(council_text):
            union_kind = "union_committee" if tier in ("corporation", "karachi") else "union_council"
            council = _union(district, union_kind, council_text)
        elif COMMITTEE.search(council_text) or tier in ("municipal", "town"):
            council = _committee(district, _committee_kind(council_text, tier), council_text)
        else:
            union_kind = "union_committee" if tier in ("corporation", "karachi") else "union_council"
            council = _union(district, union_kind, council_text)
        ward = None
        if seat == "general" and ward_text is not None:
            ward = ward_number(ward_text)
        elif seat == "general" and name is not None:
            remark = BLANK_WARD
        out.append(Seat(day, district, tier, council, seat, ward, _clean(name), party_text, remark, page))
    return _number_blank_wards(out)


BLANK_WARD = "The ward cell is blank in the notification; the ward is numbered by its place in the council's table."


def _number_blank_wards(found: list[Seat]) -> list[Seat]:
    """A union council whose table leaves some ward cells blank lists its wards in order, so the blank ones take the
    numbers its printed wards leave free, in turn."""
    out = list(found)
    councils: dict[Council, list[int]] = {}
    for index, seat in enumerate(out):
        councils.setdefault(seat.council, []).append(index)
    for council, indexes in councils.items():
        blank = [i for i in indexes if out[i].remark == BLANK_WARD]
        if not blank:
            continue
        taken = {out[i].ward for i in indexes if out[i].ward is not None}
        free = [n for n in range(1, 5) if n not in taken]
        if len(free) != len(blank) or not council.kind.startswith("union"):
            raise ValueError(f"{council}: {len(blank)} blank ward cells for wards {free}")
        for i, number in zip(blank, free, strict=True):
            out[i] = out[i]._replace(ward=number)
    return out


def _clean(name: str | None) -> str | None:
    if name is None:
        return None
    return re.sub(r"^chairman\s+", "", _space(name).strip(" ,;/"), flags=re.IGNORECASE)


ECP_SOURCE = key("source", "ecp-lg-2015")
SYSTEM = key("electoral_system", "ward", "fptp")
TICKET_SYSTEM = key("electoral_system", "chairman", "fptp")
KARACHI = ("Karachi Central", "Karachi East", "Karachi South", "Karachi West", "Karachi Korangi", "Karachi Malir")


def council_area(council: Council) -> str:
    return key("area", UNION_SCHEME, council.district, council.kind, council.number, _fold(council.name))


def ward_area(council: Council, ward: int) -> str:
    return key("area", WARD_SCHEME, council.district, council.kind, council.number, _fold(council.name), ward)


def council_body(council: Council) -> str:
    return key("body", council.district, council.kind, council.number, _fold(council.name))


def district_body(district: str) -> str:
    return key("body", "Karachi" if district in KARACHI else district, "district_council")


def district_body_name(district: str) -> str:
    return f"District Council {'Karachi' if district in KARACHI else district}"


def chairman_office(council: Council) -> str:
    return key("office", "chairman", council.district, council.kind, council.number, _fold(council.name))


def council_label(council: Council) -> str:
    """The council's name: "Union Council 1 Bossan", "Union Committee 12", "Town Committee Guddu"."""
    parts = [LABELS[council.kind]]
    if council.number is not None:
        parts.append(str(council.number))
    if council.name:
        parts.append(council.name)
    return " ".join(parts)


def council_name(council: Council) -> str:
    label = council_label(council)
    return label if council.district.lower() in label.lower() else f"{label}, {council.district}"


def council_slug(council: Council) -> str:
    parts = [PREFIXES[council.kind]]
    if council.number is not None:
        parts.append(str(council.number))
    if council.name:
        parts.append(changeset.slug(council.name))
    return "-".join(parts)


def _named() -> list[Seat]:
    """The seats the notifications fill with a member."""
    return [s for s in seats() if s.name]


def _volume() -> raw.Artifact:
    return raw.cached(SOURCE, VOLUME)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/{day.isoformat()}" for day in DAYS)]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Sindh's union councils and union committees, town and municipal committees, and their wards, as the "
        "Election Commission's notifications of the 2015 local government results name them",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(PAKISTAN)
    p.uses(_volume())
    p.add(
        "scheme",
        code=UNION_SCHEME,
        polity_id=PAKISTAN,
        kind="administrative",
        rank=4,
        noun="local council",
        kinds=NOUNS,
        code_system=None,
    )
    p.add(
        "scheme", code=WARD_SCHEME, polity_id=PAKISTAN, kind="electoral_district", rank=5, noun="ward", code_system=None
    )
    councils: dict[str, tuple[Council, date]] = {}
    wards: dict[str, tuple[Council, int, date]] = {}
    for seat in seats():
        councils.setdefault(council_area(seat.council), (seat.council, seat.day))
        if seat.ward is not None:
            wards.setdefault(ward_area(seat.council, seat.ward), (seat.council, seat.ward, seat.day))
    for id, (council, _) in councils.items():
        p.add("area", id=id, scheme_code=UNION_SCHEME)
        p.add("area_kind", area_id=id, kind=council.kind, source_id=ECP_SOURCE)
        p.name(id, council_name(council), "en", "official")
    for id, (council, ward, day) in wards.items():
        p.add("area", id=id, scheme_code=WARD_SCHEME)
        p.add("area_parent", area_id=id, parent_id=council_area(council), valid_from=day)
        p.name(id, f"Ward {ward}, {council_name(council)}", "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Election Commission of Pakistan's notifications of Sindh's 2015 local government results, the parties "
        "they name, the councils and chairmanships they fill, and their electoral systems",
    )
    p.external.add(PAKISTAN)
    p.add(
        "source",
        id=ECP_SOURCE,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="Local Government Elections Sindh 2015: notifications of returned candidates, by district",
        url=VOLUME,
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    p.artifact(_volume(), ECP_SOURCE)
    found = _named()
    used = sorted({abbr for s in found if (abbr := party(s.party)[0]) is not None})
    for abbr in used:
        id = party_id(abbr)
        p.add("party", id=id, kind="party", polity_id=PAKISTAN)
        p.name(id, PARTIES[abbr][0], "en", "official")
        if PARTIES[abbr][0] != abbr:
            p.name(id, abbr, "en", "short")
    bodies: dict[str, str] = {}
    offices: dict[str, Council] = {}
    for seat in found:
        bodies.setdefault(council_body(seat.council), council_name(seat.council))
        if seat.seat == "district":
            bodies.setdefault(district_body(seat.district), district_body_name(seat.district))
        if seat.seat in ("chairman", "vice_chairman"):
            offices.setdefault(chairman_office(seat.council), seat.council)
    for body, name in bodies.items():
        p.add("body", id=body, polity_id=PAKISTAN, kind="council")
        p.name(body, name, "en", "official")
    for office, council in offices.items():
        p.add("office", id=office, polity_id=PAKISTAN, body_id=council_body(council), kind="mayor")
        p.name(office, f"Chairman of {council_name(council)}", "en", "official")
    p.add("electoral_system", id=SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add("electoral_system", id=TICKET_SYSTEM, polity_id=PAKISTAN, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under the Sindh Local Government Act 2013, each union council and union committee has four wards "
        "and each town or municipal committee its own wards, and every ward elects one general member by "
        "plurality. Each union council also elects one member of its district council the same way. Seats for "
        "women, peasants and workers, youth, and non-Muslims are filled afterwards by the elected members and are "
        "not part of these results.",
        lang="en",
    )
    p.add(
        "note",
        id=key("note", TICKET_SYSTEM),
        entity_id=TICKET_SYSTEM,
        kind="methodology",
        body_md="The chairman and vice chairman of each union council and union committee stand together on one "
        "joint ticket, elected by plurality of the whole council's voters.",
        lang="en",
    )
    return p


def party_id(abbr: str) -> str:
    return key("party", abbr)


METHOD = (
    "From the Election Commission of Pakistan's notification of the members returned in {district} in Sindh's "
    "local government elections polled on {day}, as bound into one scanned volume with the other districts' "
    "notifications. The notification names the member returned for each ward, the chairman and vice chairman of each "
    "union council or committee, and each union council's member of the district council, with their parties, but "
    "prints no votes, so each contest stores its winner only. The scan has no text layer; its pages were transcribed "
    "and each page's rows counted against the serial numbers it prints."
)
CAVEATS = {
    "winner by toss": "The notification marks this member as returned by the toss of a coin after a tie.",
    "duplicat": "The notification prints the same member as returned for two wards of this council; both are stored "
    "as printed.",
    "wards 3 and 4": "The notification prints the same member as returned for two wards of this council; both are "
    "stored as printed.",
}


def election(day: date) -> Package:
    stamp = day.isoformat()
    p = Package(
        f"{SOURCE}/{stamp}",
        SOURCE,
        f"The {PHASES[day]} phase of Sindh's 2015 local government elections, polled on {day.day} "
        f"{day.strftime('%B')} 2015: the member returned for every ward, chairmanship, and district council seat "
        "the Election Commission notified",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    p.external.add(PAKISTAN)
    p.uses(_volume())
    election_id = key("election", stamp)
    root = f"pk/{stamp}-sindh-local"
    title = f"2015 Sindh local government elections ({PHASES[day]} phase)"
    p.add("election", id=election_id, polity_id=PAKISTAN, kind="local", date_from=day, date_to=day, status="held")
    p.add("slug", path=root, entity_id=election_id, is_current=True)
    p.name(election_id, title, "en", "official")
    found = [s for s in _named() if s.day == day]
    tickets: dict[Council, list[Seat]] = {}
    for seat in found:
        if seat.seat in ("chairman", "vice_chairman"):
            tickets.setdefault(seat.council, []).append(seat)
    paths: set[str] = set()
    done: set[Council] = set()
    for seat in found:
        council = seat.council
        if seat.seat in ("chairman", "vice_chairman"):
            if council in done:
                continue
            done.add(council)
            members = sorted(tickets[council], key=lambda s: s.seat != "chairman")
            tag: tuple[object, ...] = (
                stamp,
                council.district,
                council.kind,
                council.number,
                _fold(council.name),
                "chairman",
            )
            label = f"{council_name(council)}, chairman and vice chairman"
            path = f"{root}/{changeset.slug(council.district)}/{council_slug(council)}/chairman"
            contest = {
                "kind": "office",
                "office_id": chairman_office(council),
                "district_area_id": council_area(council),
            }
            system = TICKET_SYSTEM
        elif seat.seat == "district":
            members = [seat]
            tag = (stamp, council.district, council.kind, council.number, _fold(council.name), "district")
            label = f"{district_body_name(council.district)}, {council_label(council)} seat"
            path = f"{root}/{changeset.slug(council.district)}/{council_slug(council)}/district-council"
            contest = {
                "kind": "body_seats",
                "body_id": district_body(council.district),
                "district_area_id": council_area(council),
            }
            system = SYSTEM
        else:
            assert seat.ward is not None
            members = [seat]
            tag = (stamp, council.district, council.kind, council.number, _fold(council.name), "ward", seat.ward)
            label = f"{council_name(council)}, Ward {seat.ward}"
            path = f"{root}/{changeset.slug(council.district)}/{council_slug(council)}/ward-{seat.ward}"
            contest = {
                "kind": "body_seats",
                "body_id": council_body(council),
                "district_area_id": ward_area(council, seat.ward),
            }
            system = SYSTEM
        if path in paths:
            raise ValueError(f"two contests share the path {path}")
        paths.add(path)
        tacit = any((m.remark or "").lower().startswith("unopposed") for m in members)
        result_kind = "tacit" if tacit else "counted"
        contest_id = key("contest", *tag)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            magnitude=1,
            round_number=1,
            electoral_system_id=system,
            default_result_kind=result_kind,
            **contest,
        )
        p.name(contest_id, f"{title}, {label}", "en", "official")
        p.add("slug", path=path, entity_id=contest_id, is_current=True)
        result_set = key("result_set", *tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind=result_kind,
            source_id=ECP_SOURCE,
            status="active",
            is_default=True,
            completeness=0,
        )
        method = METHOD.format(district=council.district, day=f"{day.day} {day.strftime('%B')} 2015")
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        caveats = [text for m in members for word, text in CAVEATS.items() if word in (m.remark or "").lower()]
        caveats += [
            f"The notification prints the party as {SLIPS[_fold(m.party)]}, read here as {PARTY_OF[_fold(m.party)]}."
            for m in members
            if m.party and _fold(m.party) in SLIPS
        ]
        if seat.seat in ("chairman", "vice_chairman") and len(members) == 1:
            only = "chairman" if members[0].seat == "chairman" else "vice chairman"
            caveats.append(f"The notification names only the {only} of this ticket.")
        if blank := [m for m in members if m.remark == BLANK_WARD]:
            caveats.append(blank[0].remark or "")
        for index, text in enumerate(dict.fromkeys(caveats)):
            p.add(
                "note",
                id=key("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=text,
                lang="en",
            )
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=ECP_SOURCE,
            locator={"url": VOLUME, "pages": sorted({m.page for m in members})},
        )
        option = key("option", *tag)
        head = members[0]
        if len(members) > 1:
            ballot = f"{members[0].name} / {members[1].name}"
        else:
            ballot = head.name or ""
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="ticket" if seat.seat in ("chairman", "vice_chairman") else "candidate",
            ballot_label=ballot,
        )
        for index, member in enumerate(members):
            assert member.name is not None
            person = key("person", *tag, index)
            p.add("person", id=person)
            p.name(person, member.name, "en", "official")
            abbr, independent = party(member.party)
            p.add(
                "candidacy",
                id=key("candidacy", *tag, index),
                person_id=person,
                option_id=option,
                role="running_mate" if member.seat == "vice_chairman" else "head",
                ballot_name=member.name,
                member_of_party_id=party_id(abbr) if abbr else None,
                is_independent=independent,
            )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    return p


def parse() -> None:
    for build in (registry, geo):
        build().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())
