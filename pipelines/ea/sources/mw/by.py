"""The Malawi Electoral Commission's by-elections and fresh elections, read from its results declaration speeches.

Each speech names every contest, its registered voters, the voters who turned up, the null and void votes, and each
candidate's votes, and then declares the winner. The speeches give one count for each constituency or ward, so each
contest has one reporting unit, equal to its area.

The 2020 and 2024 polls used the 2019 constituencies and wards, which `mw.mec/geo` holds. The 2026 polls used the
boundaries of the 2025 general election, which the 2019 tables do not describe, so the `geo-by` package carries those
areas under their own schemes.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.mw import mec
from ea.sources.mw.mec import ASSEMBLY, COUNCILS, COUNTRY, MALAWI, MEC_SOURCE, SYSTEM_FPTP, key, party

SOURCE = mec.SOURCE
BASE = mec.BASE
FILES = {
    date(2020, 12, 15): "2024/02/2020-12-15-Results-Declaration-of-results-Mangochi-Phalombe-by-elections-1.pdf",
    date(2024, 3, 26): "2024/04/Results-declaration-for-Kaporo-Rukuru-Chilanga-wards.pdf",
    date(2026, 3, 17): "2026/08/Declaration-of-results-17-March-2026.pdf",
    date(2026, 6, 30): "2026/08/20260702-Final-Declaration-Speech-for-30th-June-2026.pdf",
}
FRESH = "Balaka Mulunguzi"
SCHEME_CONSTITUENCY = "mw.constituency_2026"
SCHEME_WARD = "mw.ward_2026"
# The 2019 areas that the 2020 and 2024 polls used, by the code `mw.mec/geo` gives each.
CONSTITUENCIES_2019 = {"Mangochi West": "115", "Mangochi North East": "108", "Phalombe North": "171"}
WARDS_2019 = {"Kaporo": "0012", "Chilanga": "0013", "Rukuru": "0014"}
# The printed abbreviation of each party, by the name or abbreviation the speeches use.
PARTIES = {
    "malawi congress party": "MCP",
    "mcp": "MCP",
    "democratic progressive party": "DPP",
    "democratic progressive": "DPP",
    "dpp": "DPP",
    "united democratic front": "UDF",
    "udf": "UDF",
    "utm": "UTM",
    "alliance for democracy": "AFORD",
    "aford": "AFORD",
    "people's party": "PP",
    "peoples' party": "PP",
    "pp": "PP",
    "national development party": "NDP",
    "ndp": "NDP",
    "peoples' development party": "PDP",
    "pdp": "PDP",
    "muviwachilungamo revolutionary party": "MRP",
    "mrp": "MRP",
    "peoples' transformation party": "PETRA",
    "petra": "PETRA",
    "odya zake alibe mlandu": "ODYA ZAKE",
    "odya zake": "ODYA ZAKE",
    "umodzi party": "UP",
    "up": "UP",
    "ppm": "PPM",
}
# One printed registered-voter figure has a stray comma, `3,1501`; its printed 61.42 percent turnout of 19,348 voters
# fixes it at 31,501.
FIXES = {"3,1501": 31501}


@dataclass
class Contest:
    name: str
    kind: str  # "parliament" or "local"
    constituency: str
    registered: int
    cast: int
    void: int
    turnout: float
    candidates: list[tuple[str, str, int]] = field(default_factory=list)
    winner: str = ""
    gap: int = 0


def url(day: date) -> str:
    return f"{BASE}/{FILES[day]}"


def fetch() -> None:
    for day in FILES:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def number(text: str) -> int:
    text = text.strip()
    return FIXES[text] if text in FIXES else int(text.replace(",", ""))


def flat(day: date) -> str:
    """The speech's text from its declaration of results on, as one line without page footers."""
    path = raw.DATA / raw.cached(SOURCE, url(day)).storage_key
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    text = done.stdout.replace("\f", " ").replace("’", "'").replace("‘", "'")
    text = re.sub(r"\d+\s*\|\s*P\s*a\s*g\s*e", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text[text.index("Declaration of Results") :]
    stop = [
        text.index(mark)
        for mark in ("Commission observations", "Closing remarks", "Holding of by-election")
        if mark in text
    ]
    return text[: min(stop)] if stop else text


def party_code(text: str) -> str:
    """The printed abbreviation of a party as a speech describes it, or `IND` for an independent candidate."""
    text = re.sub(r"^(?:,\s*)?(?:of the |of |an |the )", "", text.strip(" ,;"), flags=re.IGNORECASE).strip()
    text = re.sub(r"[,;]?\s*(?:and)?$", "", text).strip()
    if text.lower().startswith("independent"):
        return "IND"
    found = re.fullmatch(r"(.*?)\s*\(([^)]*)\)", text)
    for candidate in ([found[2], found[1]] if found else []) + [text]:
        candidate = candidate.strip().lower()
        if candidate in PARTIES:
            return PARTIES[candidate]
    raise ValueError(f"unknown party {text!r}")


NAME = r"((?:(?!Ward\b|Constituency\b)[A-Z][A-Za-z'/-]*\s)+?)"
HEADER = re.compile(
    r"(?:\d+\.\s+)?"
    + NAME
    + r"(Ward|Constituency)(?: in "
    + NAME
    + r"Constituency)?\s+(?:"
    + NAME
    + r"(?:(?:Ward|Constituency)\s+)?)?(?:had|There was)"
)
ITEM = re.compile(r"^(.*?)\s*\(([MF])\)\s*,?\s*(.*?)\s*,?\s+got\s+([\d,]+)")


def contests(day: date) -> list[Contest]:
    text = flat(day)
    out: list[Contest] = []
    start = 0
    for found in re.finditer(r"as follows:", text):
        context = text[start : found.start()]
        header = HEADER.search(context)
        if header is None:
            raise ValueError(f"{day}: no contest header before {context[-120:]!r}")
        end = text.index("Distinguished", found.end())
        region = text[found.end() : end]
        registered = re.search(r"total of ([\d,]+) registered", context)
        cast = re.search(r"([\d,]+) (?:voters )?turned (?:up|out)", context)
        void = re.search(r"(?:[Tt]here were ([\d,]+) (?:null and )?void|[Tt]otal void votes were ([\d,]+))", context)
        pct = re.search(r"([\d.]+)%? percent voter turnout", context)
        if not (registered and cast and void and pct):
            raise ValueError(f"{day}: figures missing for {header[0]!r}")
        name = header[1].strip()
        if header[2] == "Constituency":
            kind, constituency = "parliament", name
        else:
            kind, constituency = "local", (header[3] or "").strip()
        contest = Contest(
            name=name,
            kind=kind,
            constituency=constituency,
            registered=number(registered[1]),
            cast=number(cast[1]),
            void=number(void[1] or void[2]),
            turnout=float(pct[1]),
        )
        items = re.split(r"\s(?=\d{1,2}\.\s+[A-Z])", " " + region)
        for item in items:
            item = re.sub(r"^\s*\d{1,2}\.\s+", "", item).strip()
            if not item:
                continue
            parts = ITEM.match(item)
            if parts is None:
                raise ValueError(f"{day} {name}: cannot read candidate {item!r}")
            contest.candidates.append((parts[1].strip(), party_code(parts[3]), number(parts[4])))
        declared = re.search(r"declare (.*?)(?:,)? who contested", text[end : end + 600])
        if declared is None:
            declared = re.search(r"declare (.*?), who contested", text[end : end + 600])
        top = max(contest.candidates, key=lambda row: row[2])
        # The speeches spell a few declared names differently from the candidate lists, so any shared word will do.
        if declared is not None and not any(part.lower() in top[0].lower() for part in declared[1].split()):
            raise ValueError(f"{day} {name}: declared {declared[1]!r} but {top[0]!r} has the most votes")
        contest.winner = top[0]
        votes = sum(row[2] for row in contest.candidates)
        contest.gap = contest.cast - votes - contest.void
        if abs(contest.cast / contest.registered * 100 - contest.turnout) > 0.6:
            raise ValueError(f"{day} {name}: printed turnout {contest.turnout} does not match the counts")
        out.append(contest)
        start = end
    return out


@functools.cache
def book(day: date) -> list[Contest]:
    return contests(day)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def place(day: date, contest: Contest) -> tuple[str, str, str]:
    """The scheme, code, and area id of a contest's constituency or ward."""
    if day.year < 2026:
        if contest.kind == "parliament":
            code = CONSTITUENCIES_2019[contest.name]
            return "mw.constituency", code, area("mw.constituency", code)
        code = WARDS_2019[contest.name]
        return "mw.ward", code, area("mw.ward", code)
    if contest.kind == "parliament":
        code = changeset.slug(contest.name)
        return SCHEME_CONSTITUENCY, code, area(SCHEME_CONSTITUENCY, code)
    code = f"{changeset.slug(contest.constituency)}-{changeset.slug(contest.name)}"
    return SCHEME_WARD, code, area(SCHEME_WARD, code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo-by", *(f"{SOURCE}/{day}" for day in FILES)]


def parse() -> None:
    geo().write()
    for day in FILES:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-by",
        SOURCE,
        "The constituencies and wards of the 2026 by-elections, on the boundaries of the 2025 general election",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {MALAWI, COUNTRY}
    for code, rank, noun in ((SCHEME_CONSTITUENCY, 3, "constituency"), (SCHEME_WARD, 4, "ward")):
        p.add("scheme", code=code, polity_id=MALAWI, kind="electoral_district", rank=rank, noun=noun, code_system=None)
    constituencies: dict[str, str] = {}
    wards: dict[str, tuple[str, str]] = {}
    for day in FILES:
        if day.year < 2026:
            continue
        for contest in book(day):
            if contest.kind == "parliament":
                constituencies[contest.name] = contest.name
            else:
                constituencies.setdefault(contest.constituency, contest.constituency)
                wards[f"{contest.constituency}\x1f{contest.name}"] = (contest.constituency, contest.name)
    for name in sorted(constituencies):
        code = changeset.slug(name)
        id = area(SCHEME_CONSTITUENCY, code)
        p.add("area", id=id, scheme_code=SCHEME_CONSTITUENCY)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
    for constituency, name in sorted(wards.values()):
        code = f"{changeset.slug(constituency)}-{changeset.slug(name)}"
        id = area(SCHEME_WARD, code)
        p.add("area", id=id, scheme_code=SCHEME_WARD)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area(SCHEME_CONSTITUENCY, changeset.slug(constituency)))
        p.name(id, name, "en", "official")
    return p


def election(day: date) -> Package:
    contests_ = book(day)
    fresh = [c for c in contests_ if c.name == FRESH]
    regular = [c for c in contests_ if c.name != FRESH]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} by-elections" + (" and the Balaka Mulunguzi fresh election" if fresh else ""),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"] + ([f"{SOURCE}/geo-by"] if day.year >= 2026 else []),
    )
    p.external |= {MALAWI, COUNTRY}
    p.artifact(raw.cached(SOURCE, url(day)), MEC_SOURCE)
    off = [c for c in contests_ if c.gap]
    if off:
        p.accept(
            "accounting.ballots_sum",
            "The speech's printed voters who turned up differ from the candidates' votes plus the null and void votes "
            "for "
            + "; ".join(
                f"{c.name} (turned up {c.cast:,}, candidates {c.cast - c.void - c.gap:,}, null and void {c.void:,})"
                for c in off
            )
            + ". Each is stored as printed.",
        )
    for group, kind, path, title in (
        (regular, "by_election", f"mw/{day}", f"{day:%-d %B %Y} Malawi by-elections"),
        (fresh, "special", f"mw/{day}-balaka-mulunguzi", f"{day:%-d %B %Y} Balaka Mulunguzi fresh election"),
    ):
        if not group:
            continue
        id = key("election", day, kind)
        p.add("election", id=id, polity_id=MALAWI, kind=kind, date_from=day, date_to=day, status="held")
        p.add("slug", path=path, entity_id=id, is_current=True)
        p.name(id, title, "en", "official")
        for contest in group:
            _contest(p, day, id, path, contest)
    return p


METHOD = (
    "Counted by the Malawi Electoral Commission and declared in the Chairperson's results speech: each candidate's "
    "votes, the registered voters, the voters who turned up, and the null and void votes. Valid votes are the sum of "
    "the candidates' votes. The speech gives one count for the whole constituency or ward, so it is the only unit."
)


def _contest(p: Package, day: date, election_id: str, path_: str, contest: Contest) -> None:
    scheme, code, area_id = place(day, contest)
    tag = f"{day}:{contest.kind}:{code}"
    local = contest.kind == "local"
    slug = changeset.slug(contest.name)
    unit = key("unit", tag)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MALAWI,
        scheme_code=scheme,
        code=f"{day}:{code}",
        area_relation="equals",
        area_id=area_id,
        kind="standard",
    )
    c = key("contest", tag)
    p.add(
        "contest",
        id=c,
        election_id=election_id,
        kind="body_seats",
        body_id=COUNCILS if local else ASSEMBLY,
        district_area_id=area_id,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM_FPTP,
        default_result_kind="counted",
    )
    if local:
        title = f"{day.year} local government by-election, {contest.name} ward ({contest.constituency})"
    elif contest.name == FRESH:
        title = f"{day.year} National Assembly fresh election, {contest.name}"
    else:
        title = f"{day.year} National Assembly by-election, {contest.name}"
    p.name(c, title, "en", "official")
    p.add(
        "slug",
        path=f"{path_}/{'local' if local else 'parliament'}/{code if day.year < 2026 else slug}"
        + (f"-{slug}" if day.year < 2026 else ""),
        entity_id=c,
        is_current=True,
    )
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=c,
        stage="official",
        kind="counted",
        source_id=MEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    for name, code_, votes in contest.candidates:
        option = key("option", tag, name)
        person = key("person", tag, name)
        solo = code_ == "IND"
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=c,
            kind="candidate",
            party_id=None if solo else party(code_),
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=None if solo else party(code_),
            is_independent=solo,
        )
        p.add(
            "outcome", result_set_id=result_set, option_id=option, kind="elected" if name == contest.winner else "lost"
        )
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=1,
            count_round=0,
            flags=0,
            value=votes,
        )
    valid = sum(votes for _, _, votes in contest.candidates)
    for measure, value in ((2, contest.registered), (3, contest.cast), (4, valid), (5, contest.void)):
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=None,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )
    for measure, value in ((2, contest.registered), (3, contest.cast), (5, contest.void)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
