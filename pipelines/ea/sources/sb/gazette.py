"""Solomon Islands election results published in the Solomon Islands Gazette.

National general elections. After each general election the Governor-General publishes the official results in an
extra-ordinary Gazette under the National Parliament Electoral Provisions Act: every candidate's votes in every
constituency, and nothing else (no rejected votes, turnout, or registered electors). The archive reads the scans of
those gazettes that the Pacific Islands Legal Information Institute (PacLII) holds: 1997 (Gazette No. 44, notice 166),
2006 (Gazette No. 21, notices 81 to 130), 2010 (Gazette No. 90, notice 220), and 2014 (Gazette No. 124, notice 221). Its
scans of the 2001 results notice and of the returning officers' notices published with the 2019 cover notice are not
there. The 2006, 2010, and 2014 numbers were read twice, by Tesseract and from the PDF's own text layer (page 9 of the
2014 gazette has none), and every disagreement and every number either pass missed was read from the page image; the
1997 gazette, whose text layer is unusable, was read from the page images and checked against Tesseract.
`gazette_parliament.tsv` holds the result, with each number's box on its page in PDF points. The constituencies are the
50 that the 2024 results use, so the contests reuse the `sb.siec` constituency areas and reporting units.

Provincial assemblies. Under regulation 43(2) of the Provincial Government (Election) Regulations 2010, the Minister for
Provincial Government publishes each provincial assembly election's results in the Gazette: every candidate's votes by
ward (Part A), then each ward's valid, rejected, and cast votes and registered voters (Part B). The Government's gazette
archive on solomons.gov.sb begins in 2020, and the only provincial results it holds before the 2024 joint election are
in the extra-ordinary Gazette No. 1 of 3 January 2023: the Choiseul and Western Provincial Assembly elections of 14
December 2022 and the Guadalcanal Ward 19 (Vulolo) by-election of 21 December 2022. The 2024 results are the `sb.siec`
pipeline's.
"""

import csv
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import names, package, raw
from ea.package import Package
from ea.sources.sb import siec

SOURCE = "sb.gazette"
COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fSB"))
GAZETTE = "https://solomons.gov.sb/wp-content/uploads/2023/01/Gaz-No.-1-Tuesday-3rd-January-2023.pdf"
INDEX = (
    "https://solomons.gov.sb/ministry-of-justice-and-legal-affairs/attorney-generals-chambers/"
    "annual-indexes-of-gazette-notices-and-legal-notices/2023-annual-index-to-gazette-and-legal-notices/"
)
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
GENERAL = date(2022, 12, 14)
BY_ELECTION = date(2022, 12, 21)
PROVINCES = ("Choiseul", "Western")
# Choiseul Ward 5 (Babatana): the four candidates' printed votes sum to 821, while Part B prints 872 valid votes and
# 872 cast. The gazette has no other figure to reconcile them, so both are stored as printed.
SHORT = {("Choiseul", 5): 51}

# The gazette's text layer types a capital I at the start of a word as a lower-case l. Each correction is disclosed in
# the result set's note.
SPELLING = {"llala": "Ilala", "lnia": "Inia", "ltu": "Itu", "lriqila": "Iriqila"}

METHOD = (
    "Published by the Minister for Provincial Government and Institutional Strengthening under regulation 43(2) of the "
    "Provincial Government (Election) Regulations 2010, in the extra-ordinary Gazette No. 1 of 3 January 2023 (notice "
    "{notice}): each candidate's votes, and the valid, rejected, and cast votes and registered voters of {place}. "
    "Candidates are listed in the gazette's order. The notice does not name the winner; the candidate with the most "
    "votes is recorded as elected, as the regulations provide for single-member wards."
)
SHORT_NOTE = (
    " The candidates' printed votes sum to {total:,}, {gap} fewer than the {valid:,} valid votes the gazette prints; "
    "both are stored as printed."
)
CORRECTED = " The gazette's text prints a capital I as a lower-case l in {words}; the archive reads them as {fixed}."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


GAZETTE_SOURCE = key("source", "gazette-2023-1")


@dataclass
class Ward:
    notice: int
    province: str
    number: int
    name: str
    candidates: list[tuple[str, int]]
    valid: int
    rejected: int
    cast: int
    registered: int
    corrected: list[str]


def fix(text: str, corrected: list[str]) -> str:
    words = []
    for word in text.split():
        if word in SPELLING:
            corrected.append(word)
            word = SPELLING[word]
        words.append(word)
    return " ".join(words)


def text() -> str:
    path = raw.cached(SOURCE, GAZETTE).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True)
    return re.sub(r"^[ \t\f]*\d+[ \t]*\n", "", done.stdout, flags=re.MULTILINE)  # page numbers


def notices() -> dict[int, str]:
    parts = re.split(r"^\[(\d+)\][ \t]*$", text(), flags=re.MULTILINE)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def _statistics(part: str) -> dict[int, dict[str, int]]:
    found: dict[int, dict[str, int]] = {}
    current = 0
    for line in part.splitlines():
        ward = re.match(r"\s*Ward\s+(\d+)", line)
        if ward:
            current = int(ward.group(1))
        stat = re.search(r"Total (valid votes|rejected votes|votes cast|registered voters)\s+([\d,]+)\s*$", line)
        if stat:
            found.setdefault(current, {})[stat.group(1).split()[0]] = int(stat.group(2).replace(",", ""))
    return found


def general(province: str) -> list[Ward]:
    """The wards of one province's 14 December 2022 election, from notice 2 (Choiseul) or 3 (Western)."""
    notice = 2 + PROVINCES.index(province)
    part = notices()[notice]
    if not re.search(rf"PROVINCIAL ASSEMBLY OF\s+{province.upper()} PROVINCE", part):
        raise ValueError(f"notice {notice} is not the {province} results")
    votes, stats = part.split("PART B", 1)
    # Part B's ward names, which mark where a Part A heading ends when it runs into the first candidate's name.
    headings = {
        int(n): " ".join(name.split()) for n, name in re.findall(r"Ward\s+(\d+)\s*-?\s*(.+?)\s+Total valid", stats)
    }
    candidates: dict[int, tuple[str, list[tuple[str, int]]]] = {}
    current = 0
    for line in votes.split("PART A", 1)[1].splitlines():
        row = re.match(r"\s*(?:Ward\s+(\d+)\s*-?\s*)?(\S.*?)\s{2,}(\d+)\s*$", line)
        if row is None or row.group(2) == "Ward":
            continue
        label = row.group(2)
        if row.group(1):
            current = int(row.group(1))
            split = re.split(r"\s{2,}", label, maxsplit=1)
            if len(split) == 2:
                name, label = split
            elif label.startswith(headings[current] + " "):
                name, label = headings[current], label[len(headings[current]) :]
            else:
                raise ValueError(f"{province}: unreadable heading {line!r}")
            candidates[current] = (name.strip(), [])
        candidates[current][1].append((" ".join(label.split()), int(row.group(3))))
    counts = _statistics(stats)
    wards = []
    for number, (name, rows) in sorted(candidates.items()):
        corrected: list[str] = []
        stat = counts[number]
        ward = Ward(
            notice,
            province,
            number,
            fix(name, corrected),
            [(fix(label, corrected), votes) for label, votes in rows],
            stat["valid"],
            stat["rejected"],
            stat["votes"],
            stat["registered"],
            corrected,
        )
        _verify(ward)
        wards.append(ward)
    expected = {"Choiseul": 16, "Western": 26}[province]
    if [w.number for w in wards] != list(range(1, expected + 1)) or len(counts) != expected:
        raise ValueError(f"{province}: expected {expected} wards, found {[w.number for w in wards]}")
    return wards


def by_election() -> Ward:
    """The Guadalcanal Ward 19 (Vulolo) by-election of 21 December 2022, notice 1."""
    part = notices()[1]
    if "ward 19 (known as Vulolo)" not in " ".join(part.split()):
        raise ValueError("notice 1 is not the Vulolo by-election")
    votes, stats = part.split("PART B", 1)
    rows = re.findall(
        r"^\s{4,}([A-Z][A-Za-z .'’-]+?)\s{2,}(\d+)\s*$", votes.split("Number of votes received", 1)[1], re.MULTILINE
    )
    found = {
        k.split()[0]: int(v)
        for k, v in re.findall(r"Total (valid votes|rejected votes|votes case|registered voters)\s+(\d+)", stats)
    }
    ward = Ward(
        1,
        "Guadalcanal",
        19,
        "Vulolo",
        [(" ".join(label.split()), int(v)) for label, v in rows],
        found["valid"],
        found["rejected"],
        found["votes"],  # printed "Total votes case"
        found["registered"],
        [],
    )
    _verify(ward)
    return ward


def _verify(ward: Ward) -> None:
    total = sum(v for _, v in ward.candidates) + SHORT.get((ward.province, ward.number), 0)
    if total != ward.valid or ward.valid + ward.rejected != ward.cast or ward.cast > ward.registered:
        raise ValueError(f"{ward.province} ward {ward.number}: {total} {ward.valid} {ward.rejected} {ward.cast}")
    top = sorted((v for _, v in ward.candidates), reverse=True)
    if len(top) > 1 and top[0] == top[1]:
        raise ValueError(f"{ward.province} ward {ward.number}: tied for first")


def slug(text: str) -> str:
    return siec.slug(text)


def code(ward: Ward) -> str:
    return f"provincial:{slug(ward.province)}:{slug(ward.name)}"


def province_area(name: str) -> str:
    return key("area", "province", slug(name))


def ward_area(ward: Ward) -> str:
    if ward.province == "Guadalcanal":
        return siec.area_id("provincial", f"{ward.province} {ward.name}")
    return key("area", code(ward))


def body(province: str) -> str:
    return siec.key("body", "assembly", "guadalcanal") if province == "Guadalcanal" else key("body", slug(province))


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{GENERAL}-{slug(p)}" for p in PROVINCES),
        f"{SOURCE}/{BY_ELECTION}-vulolo",
        *general_packages(),
    ]


def fetch() -> None:
    raw.fetch(SOURCE, GAZETTE, suffix=".pdf")
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for g in GENERALS.values():
        raw.fetch(SOURCE, g.cached, suffix=".pdf")
    raw.fetch(SOURCE, BYE_NOTICE, suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for province in PROVINCES:
        election(province).write()
    vulolo().write()
    for day in GENERALS:
        general_election(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Choiseul and Western provinces and their provincial assembly wards of 2022, by name",
    )
    p.external.update((COUNTRY, "sb.province", "sb.ward"))
    for province in PROVINCES:
        area = province_area(province)
        p.add("area", id=area, scheme_code="sb.province")
        p.add("area_code", area_id=area, code=slug(province))
        p.name(area, province, "en", "official")
        for ward in general(province):
            ward_id = ward_area(ward)
            p.add("area", id=ward_id, scheme_code="sb.ward")
            p.add("area_code", area_id=ward_id, code=code(ward))
            p.name(ward_id, ward.name, "en", "official")
            p.add("area_parent", area_id=ward_id, parent_id=area)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Solomon Islands Gazette's provincial results notices of January 2023, and the Choiseul and Western "
        "Provincial Assemblies",
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=GAZETTE_SOURCE,
        kind="official_body",
        publisher="Solomon Islands Government",
        title="Solomon Islands Gazette, extra-ordinary No. 1 of 3 January 2023: provincial assembly election results",
        url=GAZETTE,
        license="Official publication; reuse terms are not stated",
        attribution="Solomon Islands Government, Ministry of Provincial Government and Institutional Strengthening",
    )
    p.artifact(raw.cached(SOURCE, GAZETTE), GAZETTE_SOURCE)
    p.artifact(raw.cached(SOURCE, INDEX), GAZETTE_SOURCE)
    for province in PROVINCES:
        entity = body(province)
        p.add("body", id=entity, polity_id=COUNTRY, kind="council")
        p.name(entity, f"{province} Provincial Assembly", "en", "official")
    return p


def election(province: str) -> Package:
    p = Package(
        f"{SOURCE}/{GENERAL}-{slug(province)}",
        SOURCE,
        f"The {province} Provincial Assembly election of 14 December 2022 by ward, from the Solomon Islands Gazette",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", "sb.siec/registry"],
    )
    p.external.update((COUNTRY, "sb.ward"))
    p.uses(raw.cached(SOURCE, GAZETTE))
    if province == "Choiseul":
        p.accept(
            "accounting.votes_valid",
            "The gazette's Ward 5 (Babatana) candidates' votes sum to 821 while it prints 872 valid votes and 872 cast; "
            "both are stored as printed and the gap is disclosed in the result set's note",
        )
    entity = key("election", GENERAL, slug(province))
    p.add(
        "election",
        id=entity,
        polity_id=COUNTRY,
        kind="local",
        date_from=GENERAL,
        date_to=GENERAL,
        status="held",
    )
    p.name(entity, f"2022 {province} Provincial Assembly election", "en", "official")
    path = f"sb/{GENERAL}-{slug(province)}-provincial"
    p.add("slug", path=path, entity_id=entity, is_current=True)
    for ward in general(province):
        _contest(p, entity, path, ward, None)
    return p


def vulolo() -> Package:
    p = Package(
        f"{SOURCE}/{BY_ELECTION}-vulolo",
        SOURCE,
        "The Guadalcanal Provincial Assembly by-election in Ward 19 (Vulolo) of 21 December 2022, from the Solomon "
        "Islands Gazette",
        depends=[f"{SOURCE}/registry", "sb.siec/geo", "sb.siec/registry", f"sb.siec/{siec.DAY}"],
    )
    p.external.update((COUNTRY, "sb.ward"))
    p.uses(raw.cached(SOURCE, GAZETTE))
    ward = by_election()
    entity = key("election", BY_ELECTION, "vulolo")
    p.add(
        "election",
        id=entity,
        polity_id=COUNTRY,
        kind="by_election",
        date_from=BY_ELECTION,
        date_to=BY_ELECTION,
        status="held",
    )
    p.name(entity, "2022 Vulolo ward by-election, Guadalcanal Province", "en", "official")
    path = f"sb/{BY_ELECTION}-guadalcanal-vulolo-by-election"
    p.add("slug", path=path, entity_id=entity, is_current=True)
    # The ward's reporting unit is the one the 2024 results use.
    _contest(p, entity, path, ward, siec.key("unit", code(ward)))
    return p


def _contest(p: Package, election: str, path: str, ward: Ward, unit: str | None) -> None:
    where = code(ward)
    place = f"{ward.name} ward (Ward {ward.number}), {ward.province} Province"
    contest = key("contest", election, where)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body(ward.province),
        district_area_id=ward_area(ward),
        magnitude=1,
        round_number=1,
        electoral_system_id=siec.SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{path[3:7]} {place}", "en", "official")
    p.add("slug", path=f"{path}/{slug(ward.name)}", entity_id=contest, is_current=True)
    result_set = key("result_set", election, where)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=GAZETTE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    note = METHOD.format(notice=ward.notice, place=place)
    if ward.corrected:
        note += CORRECTED.format(
            words=", ".join(f'"{w}"' for w in ward.corrected),
            fixed=", ".join(f'"{SPELLING[w]}"' for w in ward.corrected),
        )
    if (ward.province, ward.number) in SHORT:
        note += SHORT_NOTE.format(
            total=ward.valid - SHORT[(ward.province, ward.number)],
            valid=ward.valid,
            gap=SHORT[(ward.province, ward.number)],
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    if unit is None:
        unit = key("unit", where)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=COUNTRY,
            scheme_code="sb.ward",
            code=where,
            area_relation="equals",
            area_id=ward_area(ward),
            kind="standard",
        )
        p.name(unit, place, "en", "official")
    for measure, value in (
        (REGISTERED, ward.registered),
        (CAST, ward.cast),
        (VALID, ward.valid),
        (INVALID, ward.rejected),
    ):
        _put(p, result_set, unit, None, measure, value)
    best = max(v for _, v in ward.candidates)
    for position, (label, votes) in enumerate(ward.candidates, 1):
        option = key("option", election, where, position)
        person = key("person", election, where, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=label,
        )
        p.add("person", id=person)
        p.name(person, label, "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", election, where, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
        )
        if votes == best:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        _put(p, result_set, unit, option, VOTES, votes)


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


# National general elections.

PACLII = "https://www.paclii.org/sb/other/SBGovGaz"
TRANSCRIPTION = Path(__file__).with_name("gazette_parliament.tsv")


@dataclass(frozen=True)
class General:
    day: date
    url: str  # the gazette on PacLII
    cached: str  # the copy the raw cache holds, a web.archive.org capture of `url` where PacLII refused
    gazette: str
    notices: str
    act: str


GENERALS = {
    g.day: g
    for g in (
        General(
            date(1997, 8, 6),
            f"{PACLII}/1997/87.pdf",
            "https://web.archive.org/web/20241230091135id_/https://paclii.org/sb/other/SBGovGaz/1997/87.pdf",
            "extra-ordinary Gazette No. 44 of 12 September 1997",
            "notice 166",
            "section 56(2)(b) of the National Parliament Electoral Provisions Act 1980",
        ),
        General(
            date(2006, 4, 5),
            f"{PACLII}/2006/21.pdf",
            "https://web.archive.org/web/20241230111142id_/https://paclii.org/sb/other/SBGovGaz/2006/21.pdf",
            "extra-ordinary Gazette No. 21 of 11 May 2006",
            "notices 81 to 130, one for each constituency",
            "section 57 of the National Parliament (Electoral Provisions) Act (Cap. 87)",
        ),
        General(
            date(2010, 8, 4),
            f"{PACLII}/2010/90.pdf",
            "https://web.archive.org/web/20241230104055id_/https://paclii.org/sb/other/SBGovGaz/2010/90.pdf",
            "extra-ordinary Gazette No. 90 of 16 August 2010",
            "notice 220",
            "section 57(2)(b) of the National Parliament (Electoral Provisions) Act (Cap. 87)",
        ),
        General(
            date(2014, 11, 19),
            f"{PACLII}/2014/122.pdf",
            "https://web.archive.org/web/20241230092022id_/https://paclii.org/sb/other/SBGovGaz/2014/122.pdf",
            "extra-ordinary Gazette No. 124 of 26 November 2014",
            "notice 221",
            "section 57(2)(b) of the National Parliament (Electoral Provisions) Act (Cap. 87)",
        ),
    )
}
# Gazette No. 38 of 12 August 1997, notice 143: the Governor-General appoints 24 September 1997 for a by-election in
# Rennell and Bellona, whose two leading candidates tied on 415 votes on 6 August.
BYE_NOTICE = f"{PACLII}/1997/81.pdf"
TIED = {(date(1997, 8, 6), "Rennell and Bellona")}

GENERAL_METHOD = (
    "Published by the Governor-General under {act} in the {gazette} ({notices}): each candidate's votes in the {place}. "
    "The gazette prints no rejected votes, turnout, or registered electors, and does not name the winner; the "
    "candidate with the most votes is recorded as elected. The numbers were read from PacLII's scan of the gazette "
    "(page {page}), and each one's position on the page is recorded."
)
TIE_NOTE = (
    " The two leading candidates tied on {votes:,} votes, so no one is recorded as elected: Gazette No. 38 of 12 August "
    "1997 (notice 143) appointed 24 September 1997 for a by-election in the constituency."
)
UNOPPOSED_METHOD = (
    "Published by the Governor-General under {act} in the {gazette} ({notices}), which prints the {place}'s sole "
    "candidate as unopposed (page {page}). No votes were cast."
)


@dataclass
class Candidate:
    position: int
    label: str
    votes: int | None
    page: int
    box: tuple[float, float, float, float] | None


@dataclass
class Seat:
    name: str  # as the 2024 gazette prints it, which keys the sb.siec area and unit
    printed: str
    candidates: list[Candidate]


def seats(day: date) -> list[Seat]:
    found: dict[str, Seat] = {}
    with TRANSCRIPTION.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            if int(row["year"]) != day.year:
                continue
            seat = found.setdefault(row["constituency"], Seat(row["constituency"], row["printed"], []))
            box = (float(row["x0"]), float(row["y0"]), float(row["x1"]), float(row["y1"])) if row["x0"] else None
            seat.candidates.append(
                Candidate(
                    int(row["position"]),
                    row["candidate"],
                    int(row["votes"]) if row["votes"] else None,
                    int(row["page"]),
                    box,
                )
            )
    if len(found) != 50:
        raise ValueError(f"{day}: expected 50 constituencies, found {len(found)}")
    for seat in found.values():
        if [c.position for c in seat.candidates] != list(range(1, len(seat.candidates) + 1)):
            raise ValueError(f"{day} {seat.name}: positions are not 1 to {len(seat.candidates)}")
        if any(c.votes is None for c in seat.candidates) and len(seat.candidates) != 1:
            raise ValueError(f"{day} {seat.name}: a contested seat lacks votes")
    return list(found.values())


def page_size(path: Path, page: int) -> tuple[int, int]:
    done = subprocess.run(
        ["pdfinfo", "-f", str(page), "-l", str(page), str(path)], check=True, capture_output=True, text=True
    )
    found = re.search(r"Page\s+\d+\s+size:\s+([\d.]+) x ([\d.]+) pts", done.stdout)
    if found is None:
        raise ValueError(f"no size for page {page} of {path}")
    return round(float(found.group(1))), round(float(found.group(2)))


def general_packages() -> list[str]:
    return [f"{SOURCE}/{day}-general" for day in GENERALS]


def seat_label(name: str) -> str:
    """The 2024 gazette's name for the seat, with its misprint of West Guadalcanal corrected."""
    return name.replace("Guaalcanal", "Guadalcanal")


def general_election(day: date) -> Package:
    g = GENERALS[day]
    p = Package(
        f"{SOURCE}/{day}-general",
        SOURCE,
        f"The Solomon Islands national general election of {day:%-d %B %Y} by constituency, from the {g.gazette}",
        depends=["sb.siec/geo", "sb.siec/registry", f"sb.siec/{siec.DAY}"],
    )
    p.external.add(COUNTRY)
    artifact = raw.cached(SOURCE, g.cached)
    source = key("source", "general", day)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Solomon Islands Government",
        title=f"Solomon Islands Gazette, {g.gazette}: {day.year} national general election results",
        url=g.url,
        license="Official publication; reuse terms are not stated",
        attribution="Solomon Islands Government; scan by the Pacific Islands Legal Information Institute (PacLII)",
    )
    artifact_id = p.artifact(artifact, source)
    if any(d == day for d, _ in TIED):
        p.artifact(raw.cached(SOURCE, BYE_NOTICE), source)
    found = seats(day)
    pages = sorted({c.page for s in found for c in s.candidates})
    for number in pages:
        width, height = page_size(artifact.path, number)
        p.add(
            "source_page",
            id=key("page", day, number),
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number}.png",
            width=width,
            height=height,
        )
    election = key("election", "general", day)
    p.add(
        "election",
        id=election,
        polity_id=COUNTRY,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(election, f"{day.year} Solomon Islands national general election", "en", "official")
    path = f"sb/{day}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    for seat in found:
        _general_contest(p, g, election, path, source, seat)
    return p


def _general_contest(p: Package, g: General, election: str, path: str, source: str, seat: Seat) -> None:
    code = f"general:{slug(seat.name)}"
    label = seat_label(seat.name)
    place = f"{label} constituency"
    unopposed = seat.candidates[0].votes is None
    contest = key("contest", g.day, code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=siec.PARLIAMENT,
        district_area_id=siec.area_id("general", seat.name),
        magnitude=1,
        round_number=1,
        electoral_system_id=siec.SYSTEM,
        default_result_kind="tacit" if unopposed else "counted",
    )
    p.name(contest, f"{g.day.year} {place}", "en", "official")
    p.add("slug", path=f"{path}/{slug(label)}", entity_id=contest, is_current=True)
    result_set = key("result_set", g.day, code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if unopposed else "counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=0.0 if unopposed else 1.0,
    )
    first = seat.candidates[0]
    template = UNOPPOSED_METHOD if unopposed else GENERAL_METHOD
    note = template.format(act=g.act, gazette=g.gazette, notices=g.notices, place=place, page=first.page)
    if (g.day, seat.name) in TIED:
        note += TIE_NOTE.format(votes=first.votes or 0)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    unit = siec.key("unit", code)
    best = max((c.votes or 0) for c in seat.candidates)
    leaders = [c for c in seat.candidates if (c.votes or 0) == best]
    for c in seat.candidates:
        option = key("option", g.day, code, c.position)
        person = key("person", g.day, code, c.position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=c.position,
            ballot_label=c.label,
        )
        p.add("person", id=person)
        p.name(person, names.display(c.label), "en", "official")
        p.add(
            "candidacy",
            id=key("candidacy", g.day, code, c.position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.label,
        )
        if len(leaders) == 1 and c is leaders[0]:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        if c.votes is None:
            continue
        _put(p, result_set, unit, option, VOTES, c.votes)
        if c.box is None:
            raise ValueError(f"{g.day} {seat.name} {c.label}: no box")
        x0, y0, x1, y1 = c.box
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            page_id=key("page", g.day, c.page),
            source_id=source,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )
