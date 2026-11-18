"""New Zealand local elections: the 11 October 2025 regional council elections, from each council's declaration of
result under section 86 of the Local Electoral Act 2001, checked against the Local Authority Election Statistics."""

import csv
import functools
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from html import unescape
from typing import Any
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "nz.local"
NEW_ZEALAND = str(uuid5(package.NAMESPACE, "polity\x1fNZ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNZL"))
DAY = "2025-10-11"
DATE = date(2025, 10, 11)
STATS_URL = (
    "https://www.mcert.govt.nz/assets/Uploads/Local-government-documents/Local-elections-and-voting/"
    "Local-election-statistics/2025/2025-Local-Authority-Election-Data.csv"
)
STATS_PAGE = "https://www.mcert.govt.nz/our-work/local-government/local-elections-and-voting/local-authority-election-statistics/"

REGION, CONSTITUENCY = "nz.region", "nz.regional_constituency"


@dataclass(frozen=True)
class Council:
    key: str
    name: str
    region: str
    url: str
    suffix: str
    system: str  # fptp or stv
    publisher: str
    stats: str  # the council's name in the statistics file


COUNCILS = [
    Council(
        "bop",
        "Bay of Plenty Regional Council",
        "Bay of Plenty",
        "https://www.boprc.govt.nz/your-council/council-and-region/elections/previous-elections-information/",
        ".html",
        "fptp",
        "Bay of Plenty Regional Council",
        "Bay of Plenty Regional Council",
    ),
    Council(
        "hb",
        "Hawke's Bay Regional Council",
        "Hawke's Bay",
        "https://www.hbrc.govt.nz/assets/Document-Library/Elections/"
        "Hawkes-Bay-Regional-Council-Hawkes-Bay-Regional-Council-Final-FPP-Result.pdf",
        ".pdf",
        "fptp",
        "Hawke's Bay Regional Council",
        "Hawke's Bay Regional Council",
    ),
    Council(
        "horizons",
        "Manawatū-Whanganui Regional Council (Horizons)",
        "Manawatū-Whanganui",
        "https://www.horizons.govt.nz/news/horizons-regional-council-declaration-of-results-for-2025-election",
        ".html",
        "fptp",
        "Horizons Regional Council",
        "Manawat?-Whanganui Regional Council",
    ),
    Council(
        "gw",
        "Greater Wellington Regional Council",
        "Wellington",
        "https://www.gw.govt.nz/assets/Documents/Documents/2025/10/"
        "Wellington-Regional-Council-2025-Local-Government-Elections-Declaration-of-Result.pdf",
        ".pdf",
        "stv",
        "Greater Wellington Regional Council",
        "Wellington Regional Council",
    ),
    Council(
        "orc",
        "Otago Regional Council",
        "Otago",
        "https://www.orc.govt.nz/media/4ezeym4y/otago-regional-council-2025-triennial-elections-final-stv-result-2.pdf",
        ".pdf",
        "stv",
        "Otago Regional Council",
        "Otago Regional Council",
    ),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/regional-{DAY}"]


def fetch() -> None:
    raw.fetch(SOURCE, STATS_URL, suffix=".csv")
    for council in COUNCILS:
        raw.fetch(SOURCE, council.url, suffix=council.suffix)


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


# ---- reading the declarations -------------------------------------------------------------------------------------

HEADER = re.compile(r"^(?P<name>.+?) Regional Constituency \((?P<n>\d+) vacanc(?:y|ies)\)")
PLAIN_HEADER = re.compile(r"^(?P<name>.+?) Regional Constituency\s*$")
NUMBER = re.compile(r"^[\d,]+(?:\.\d+)?$")
UNOPPOSED = re.compile(r"^(?:As the number.*?vacancies, )?(?P<who>.+?) (?:is|are) (?:declared )?elected unopposed")


@dataclass
class Candidate:
    name: str
    party: str | None
    votes: int | None = None
    status: str | None = None


@dataclass
class Seat:
    """One regional constituency's declared result."""

    name: str
    vacancies: int
    candidates: list[Candidate] = field(default_factory=list)
    informal: int | None = None
    blank: int | None = None
    elected_text: str = ""
    unopposed: list[str] = field(default_factory=list)
    prose: list[str] = field(default_factory=list)


@dataclass
class Poll:
    """The poll on whether to keep the council's Maori constituencies."""

    answers: dict[str, int] = field(default_factory=dict)
    informal: int | None = None
    blank: int | None = None


@dataclass
class Declaration:
    seats: list[Seat] = field(default_factory=list)
    poll: Poll | None = None


POLL = re.compile(r"^M\S+ori Constituenc(?:y|ies) Poll")
ANSWER = re.compile(r"^(?P<what>I VOTE TO (?P<kind>REMOVE|KEEP) .+?)\s+(?P<votes>\d[\d,]*)$")


def text_lines(council: Council) -> list[str]:
    path = raw.cached(SOURCE, council.url).path
    if council.suffix == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
        lines = [re.sub(r"\s{2,}", " ", line.strip()) for line in out.stdout.splitlines()]
    else:
        html = path.read_text(encoding="utf-8", errors="replace")
        html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
        html = re.sub(r"</(p|div|tr|li|h\d|table|td|th)>|<br\s*/?>", "\n", html)
        text = unescape(re.sub(r"<[^>]+>", " ", html))
        lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
        start = next(i for i, line in enumerate(lines) if line.startswith("The final result for"))
        ends = ("Preliminary Results", "Updates", "Latest news")
        stop = next((i for i, line in enumerate(lines) if i > start and line.startswith(ends)), len(lines))
        lines = lines[start:stop]
    merged: list[str] = []
    for line in (unicodedata.normalize("NFC", x) for x in lines):
        if not line:
            continue
        previous = merged[-1] if merged else ""
        if (
            NUMBER.match(line)
            and previous
            and not HEADER.match(previous)
            and not NUMBER.match(previous)
            or previous.startswith("As the number of nominations")
            and "unopposed" not in previous
            or previous.startswith(("I declare that", "Votes Received")) is False
            and re.search(r"\b(?:are|is|are declared|declared)$", previous)
        ):
            merged[-1] = f"{previous} {line}"
        else:
            merged.append(line)
    return merged


def number(text: str) -> int:
    return int(text.replace(",", ""))


def split_party(rest: str) -> tuple[str, str | None]:
    """A printed candidate name and its trailing parenthesized label, if the parenthesis is not a maiden name."""
    rest = rest.strip()
    if not rest.endswith(")"):
        return rest, None
    depth = 0
    for i in range(len(rest) - 1, -1, -1):
        if rest[i] == ")":
            depth += 1
        elif rest[i] == "(":
            depth -= 1
            if depth == 0:
                label = rest[i + 1 : -1].strip()
                if label.upper().startswith("NEE") or i == 0:
                    return rest, None
                return rest[:i].strip(), label
    return rest, None


def votes_line(line: str) -> tuple[str, int] | None:
    m = re.match(r"^(?P<rest>.+?)\s+(?P<votes>\d[\d,]*)$", line)
    if m and not re.search(r"\d$", m["rest"]):
        return m["rest"], number(m["votes"])
    return None


def names_in(text: str) -> list[str]:
    return [n.strip() for n in re.split(r",\s+|\s+and\s+", text) if n.strip()]


def elected_names(text: str) -> list[str]:
    m = re.search(r"^(?P<who>.+?) (?:is|are) declared elected", " ".join(text.split()))
    return names_in(m["who"]) if m else []


def tokens(name: str) -> frozenset[str]:
    name = re.sub(r"\(.*?\)", " ", name)
    return frozenset(t for t in re.split(r"[^\w'’-]+", name.upper()) if t)


def count_cell(rest: str) -> int | None:
    return number(rest) if rest and NUMBER.match(rest) else None


def declaration(council: Council) -> Declaration:
    """Read one council's declaration: each constituency's candidates, the statuses and counts, and the poll."""
    out = Declaration()
    seat: Seat | None = None
    detail: str | None = None  # an STV table of counts, by constituency
    in_poll = False
    firsts: dict[str, dict[str, Decimal]] = {}
    for line in text_lines(council):
        h = HEADER.match(line)
        if h:
            seat = Seat(h["name"].strip(), int(h["n"]))
            out.seats.append(seat)
            detail = None
            continue
        p = PLAIN_HEADER.match(line)
        if p and council.system == "stv" and any(s.name == p["name"].strip() for s in out.seats):
            detail, seat, in_poll = p["name"].strip(), None, False
            firsts.setdefault(detail, {})
            continue
        if POLL.match(line):
            out.poll, seat, detail, in_poll = Poll(), None, None, True
            continue
        if in_poll and out.poll is not None:
            a = ANSWER.match(line)
            if a:
                out.poll.answers[a["kind"].lower()] = number(a["votes"])
            elif line.upper().startswith("INFORMAL"):
                out.poll.informal = count_cell(line[len("INFORMAL") :].strip())
            elif line.upper().startswith("BLANK VOTING PAPERS"):
                out.poll.blank = count_cell(line[len("BLANK VOTING PAPERS") :].strip())
            continue
        if detail is not None:
            m = re.match(r"^1\s+(?P<name>.+?)\s+(?P<v>[\d,]+\.\d+)$", line)
            if m:
                firsts[detail][" ".join(m["name"].split()).upper()] = Decimal(m["v"].replace(",", ""))
            q = re.match(r"^(?P<name>.+?)\s+(?P<status>elected|excluded)\s+\d+\s+", line)
            if q:
                for c in next(s for s in out.seats if s.name == detail).candidates:
                    if c.name.upper() == " ".join(q["name"].split()).upper():
                        c.status = q["status"]
            continue
        if seat is None:
            continue
        seat.prose.append(line)
        up = line.upper()
        if up.startswith("INFORMAL"):
            seat.informal = count_cell(line[len("INFORMAL") :].strip())
        elif up.startswith("BLANK VOTING PAPERS"):
            seat.blank = count_cell(line[len("BLANK VOTING PAPERS") :].strip())
        elif "elected unopposed" in line and (u := UNOPPOSED.match(line)):
            seat.unopposed = names_in(u["who"])
        elif "declared elected" in line:
            seat.elected_text = line
        elif council.system == "stv":
            m = re.match(r"^(?P<rest>.+?) (?P<status>elected|excluded)$", line)
            if m:
                name, label = split_party(m["rest"])
                seat.candidates.append(Candidate(" ".join(name.split()), label, None, m["status"]))
        elif not line.startswith("Votes Received") and (pair := votes_line(line)):
            name, label = split_party(pair[0])
            seat.candidates.append(Candidate(name, label, pair[1]))
    for seat in out.seats:
        words = " ".join(seat.prose)
        m = re.search(r"There were ([\d,]+) informal votes and ([\d,]+) blank votes", words)
        if m:
            seat.informal, seat.blank = number(m[1]), number(m[2])
        for c in seat.candidates:
            if c.votes is None:
                v = firsts.get(seat.name, {}).get(c.name.upper())
                if v is None or v != v.to_integral_value():
                    raise ValueError(f"{seat.name}: no whole first count for {c.name}: {v}")
                c.votes = int(v)
        decide(seat)
    return out


@functools.cache
def declaration_of(key_: str) -> Declaration:
    return declaration(next(c for c in COUNCILS if c.key == key_))


def seats_of(key_: str) -> list[Seat]:
    return declaration_of(key_).seats


def decide(seat: Seat) -> None:
    """Mark the candidates the declaration names as elected; an STV seat already carries its status."""
    if not seat.candidates or seat.candidates[0].status:
        return
    names = [tokens(n) for n in elected_names(seat.elected_text)]
    for c in seat.candidates:
        c.status = "elected" if tokens(c.name) in names else "excluded"
    if sum(c.status == "elected" for c in seat.candidates) != len(names):
        raise ValueError(f"{seat.name}: the declared winners {names} do not match the candidates")


@functools.cache
def statistics() -> dict[str, tuple[int, int]]:
    """Informal and blank votes of each regional council in the statistics file."""
    path = raw.cached(SOURCE, STATS_URL).path
    rows = list(csv.reader(path.read_text(encoding="cp1252").splitlines()))
    start = next(i for i, r in enumerate(rows) if r and r[0] == "Regional Councils Summary")
    head = next(r for r in rows[start:] if r and r[0] == "Authority")
    informal = next(i for i, h in enumerate(head) if h.startswith("Informal"))
    blank = next(i for i, h in enumerate(head) if h.startswith("Blank votes"))
    out: dict[str, tuple[int, int]] = {}
    for r in rows[start + 3 :]:
        if not r or not r[0]:
            break
        out[r[0]] = (number(r[informal] or "0"), number(r[blank] or "0"))
    return out


def verify() -> dict[str, Any]:
    """Each council's informal and blank votes and its candidates, set against the statistics file."""
    out: dict[str, Any] = {}
    for council in COUNCILS:
        seats = seats_of(council.key)
        informal = sum(s.informal or 0 for s in seats)
        blank = sum(s.blank or 0 for s in seats)
        want = statistics()[council.stats]
        out[council.key] = {
            "constituencies": len(seats),
            "informal": (informal, want[0]),
            "blank": (blank, want[1]),
            "match": (informal, blank) == want,
            "candidates": sum(len(s.candidates) for s in seats),
            "elected": sum(c.status == "elected" for s in seats for c in s.candidates)
            + sum(len(s.unopposed) for s in seats),
        }
    return out


# ---- the packages -------------------------------------------------------------------------------------------------


def source_id(council: Council) -> str:
    return key("source", council.key)


def source(p: Package, council: Council) -> None:
    p.add(
        "source",
        id=source_id(council),
        kind="official_body",
        publisher=f"{council.publisher}, Electoral Officer",
        title=f"{council.name}: declaration of result, 2025 triennial elections",
        url=council.url,
        license="No license stated; published by the electoral officer for public use",
        attribution=council.publisher,
    )


STATS = key("source", "stats")


def stats_source(p: Package) -> None:
    p.add(
        "source",
        id=STATS,
        kind="official_body",
        publisher="Ministry for Cities, Environment, Regions and Transport",
        title="2025 Local Authority Election Data",
        url=STATS_PAGE,
        license="CC BY 4.0",
        attribution="Ministry for Cities, Environment, Regions and Transport (formerly Department of Internal Affairs)",
    )


def region_area(council: Council) -> str:
    return key("area", REGION, council.key)


def seat_area(council: Council, seat: Seat) -> str:
    return key("area", CONSTITUENCY, council.key, changeset.slug(seat.name))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "New Zealand's regional council regions and the regional constituencies of the 2025 regional council "
        "elections, without boundaries",
    )
    p.external |= {NEW_ZEALAND, COUNTRY}
    stats_source(p)
    p.artifact(raw.cached(SOURCE, STATS_URL), STATS)
    for council in COUNCILS:
        source(p, council)
        p.artifact(raw.cached(SOURCE, council.url), source_id(council))
    for code, rank, noun in ((REGION, 2, "region"), (CONSTITUENCY, 3, "regional constituency")):
        p.add("scheme", code=code, polity_id=NEW_ZEALAND, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add("boundary_set", id=key("boundary_set", REGION, 2025), scheme_code=REGION, vintage="2025", as_of=DATE)
    p.add(
        "boundary_set", id=key("boundary_set", CONSTITUENCY, 2025), scheme_code=CONSTITUENCY, vintage="2025", as_of=DATE
    )
    for council in COUNCILS:
        r = region_area(council)
        p.add("area", id=r, scheme_code=REGION)
        p.add("area_code", area_id=r, code=council.key)
        p.add("area_parent", area_id=r, parent_id=COUNTRY)
        p.name(r, f"{council.region} Region", "en", "official")
        for seat in seats_of(council.key):
            a = seat_area(council, seat)
            p.add("area", id=a, scheme_code=CONSTITUENCY)
            p.add("area_code", area_id=a, code=f"{council.key}:{changeset.slug(seat.name)}")
            p.add("area_parent", area_id=a, parent_id=r)
            p.name(a, f"{seat.name} Regional Constituency", "en", "official")
    return p


FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
STV = key("electoral_system", "stv")


def body(council: Council) -> str:
    return key("body", council.key)


def party(label: str) -> str:
    return key("party", label)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The regional councils, electoral systems, and local tickets of the 2025 regional council elections",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NEW_ZEALAND}
    for council in COUNCILS:
        p.add("body", id=body(council), polity_id=NEW_ZEALAND, kind="council")
        p.name(body(council), council.name, "en", "official")
    for id, rules, method in (
        (FPTP, {"formula": "fptp"}, "One member, elected by the most votes."),
        (
            BLOCK,
            {"formula": "block"},
            "Several members, elected by the most votes: each elector marks as many candidates as there are vacancies.",
        ),
        (
            STV,
            {"formula": "stv", "quota": "droop", "surplus": "meek"},
            "Single transferable vote, counted by the Meek method with a Droop quota (Local Electoral Regulations 2001).",
        ),
    ):
        p.add(
            "electoral_system",
            id=id,
            polity_id=NEW_ZEALAND,
            valid_from=date(2001, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    labels = {
        c.party
        for council in COUNCILS
        for s in seats_of(council.key)
        for c in s.candidates
        if c.party and not independent(c.party)
    }
    for label in sorted(labels):
        p.add("party", id=party(label), kind="party", polity_id=NEW_ZEALAND)
        p.name(party(label), label, "en", "official")
    return p


def independent(label: str | None) -> bool:
    return bool(label) and label.lower().startswith("independent")  # type: ignore[union-attr]


METHOD = (
    "Counted by the electoral officer and printed in the council's declaration of result under section 86 of the "
    "Local Electoral Act 2001: the votes each candidate received, the informal votes, and the blank voting papers. "
    "Informal and blank counts are stored as the declaration prints them; the declaration gives no registered, "
    "cast, or valid totals."
)
STV_METHOD = (
    "Counted by single transferable vote. The votes stored are each candidate's first-preference votes, the "
    "first count of the council's printed table of counts; the declaration's later counts carry fractional "
    "transfers and are not stored. Informal and blank counts are as printed."
)
POLL_NOTE = (
    "The poll asked electors whether to keep or remove the council's Māori constituencies; the result binds the "
    "council's 2028 and 2031 elections. Counts are as printed in the declaration, with its informal and blank papers."
)
TACIT = (
    "As the number of nominations did not exceed the number of vacancies, the candidates were declared elected "
    "unopposed, and no votes were counted."
)


def election() -> Package:
    p = Package(
        f"{SOURCE}/regional-{DAY}",
        SOURCE,
        "The 11 October 2025 regional council elections, from the councils' declarations of result",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NEW_ZEALAND, COUNTRY}
    for council in COUNCILS:
        p.uses(raw.cached(SOURCE, council.url))
    path = f"nz/{DAY}-regional-councils"
    el = key("election", DAY, "regional")
    p.add(
        "election",
        id=el,
        polity_id=NEW_ZEALAND,
        kind="local",
        date_from=DATE,
        date_to=DATE,
        original_text="11 October 2025",
        status="held",
    )
    p.name(el, "2025 New Zealand regional council elections", "en", "official")
    p.add("slug", path=path, entity_id=el, is_current=True)
    for council in COUNCILS:
        sid = source_id(council)
        for seat in seats_of(council.key):
            slug = changeset.slug(seat.name)
            tag = f"{council.key}:{slug}"
            tacit = bool(seat.unopposed)
            c = key("contest", DAY, tag)
            system = STV if council.system == "stv" else (FPTP if seat.vacancies == 1 else BLOCK)
            p.add(
                "contest",
                id=c,
                election_id=el,
                kind="body_seats",
                body_id=body(council),
                district_area_id=seat_area(council, seat),
                magnitude=seat.vacancies,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="tacit" if tacit else "counted",
            )
            p.name(c, f"{council.name}, {seat.name} Regional Constituency, 2025", "en", "official")
            p.add("slug", path=f"{path}/{council.key}/{slug}", entity_id=c, is_current=True)
            rs = key("result_set", DAY, tag)
            p.add(
                "result_set",
                id=rs,
                election_id=el,
                contest_id=c,
                stage="official",
                kind="tacit" if tacit else "counted",
                source_id=sid,
                status="active",
                is_default=True,
                completeness=0.0 if tacit else 1.0,
            )
            note = TACIT if tacit else (STV_METHOD if council.system == "stv" else METHOD)
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
            unit = key("unit", DAY, tag)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEW_ZEALAND,
                scheme_code=CONSTITUENCY,
                code=f"{DAY}:{tag}",
                area_relation="equals",
                area_id=seat_area(council, seat),
                kind="standard",
            )
            people: list[tuple[str, str | None, int | None, bool]] = [
                (c_.name, c_.party, c_.votes, c_.status == "elected") for c_ in seat.candidates
            ] + [(n, None, None, True) for n in seat.unopposed]
            for name, label, votes, elected in people:
                option = key("option", DAY, tag, name)
                person = key("person", council.key, name)
                solo = independent(label) or not label
                member = None if solo else party(label)  # type: ignore[arg-type]
                p.add("person", id=person)
                p.name(person, name, "en", "official")
                p.add("contest_option", id=option, contest_id=c, kind="candidate", party_id=member, ballot_label=name)
                p.add(
                    "candidacy",
                    id=key("candidacy", DAY, tag, name),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=member,
                    is_independent=solo,
                )
                if votes is not None:
                    p.add(
                        "result_value",
                        result_set_id=rs,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                if elected:
                    p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
            for measure, value in ((5, seat.informal), (6, seat.blank)):
                if value is not None and not tacit:
                    p.add(
                        "result_value",
                        result_set_id=rs,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        poll = declaration_of(council.key).poll
        if poll:
            tag = f"{council.key}:maori-poll"
            c = key("contest", DAY, tag)
            p.add(
                "contest",
                id=c,
                election_id=el,
                kind="ballot_question",
                district_area_id=region_area(council),
                round_number=1,
                default_result_kind="counted",
            )
            p.add("ballot_question", contest_id=c, type="plebiscite")
            p.name(c, f"{council.name}: poll on the Māori constituencies, 2025", "en", "official")
            p.add("slug", path=f"{path}/{council.key}/maori-constituency-poll", entity_id=c, is_current=True)
            rs = key("result_set", DAY, tag)
            p.add(
                "result_set",
                id=rs,
                election_id=el,
                contest_id=c,
                stage="official",
                kind="counted",
                source_id=sid,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=POLL_NOTE, lang="en")
            unit = key("unit", DAY, tag)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NEW_ZEALAND,
                scheme_code=REGION,
                code=f"{DAY}:{tag}",
                area_relation="equals",
                area_id=region_area(council),
                kind="standard",
            )
            for position, answer in enumerate(("remove", "keep"), 1):
                option = key("option", DAY, tag, answer)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=c,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=f"I vote to {answer} the Māori constituencies",
                )
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=poll.answers[answer],
                )
            for measure, value in ((5, poll.informal), (6, poll.blank)):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=rs,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
    return p
