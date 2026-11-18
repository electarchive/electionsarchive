"""Tanzania's Union presidential elections of 2015 and 2020, from the Government Gazette.

The National Electoral Commission published the 2015 result as General Notice No. 1545 (Gazette of 28 October 2016):
the national votes of each candidate as text on page 57 of the Gazette, then a table of the votes of each candidate in
each of the 264 constituencies on pages 58 to 67. The table is printed as page images with no text layer, so its
figures were keyed from the page images and checked against a second, machine reading (Tesseract, cell by cell);
`president2015.tsv` holds every keyed figure with its PDF page and its box on the page, normalized to the page's width
and height. Each candidate's constituency figures add up to the national total the notice prints, and `rows()` checks
that again on every build.

The 2020 result is General Notice No. 30A (special Gazette of 5 January 2021), which prints only each candidate's
national votes.

Neither notice prints registered voters, ballots cast, or valid votes.
"""

import csv
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.tz import inec, text

TSV = Path(__file__).with_name("president2015.tsv")
PRESIDENCY = inec.key("office", "president")
SYSTEM = inec.key("electoral_system", "presidential-plurality")
key = inec.key
SCHEME = "tz.nation"
VOTES = 1
DIGITIZED = 8
DPI = 150

# The 2015 table's columns, in order, with the party each heads and the party's own spelling in the registry.
COLUMNS = ("ACT", "ADC", "CCM", "CHADEMA", "CHAUMA", "NRA", "TLP", "UPDP")
SPELLING = {"ACT": "ACT-Wazalendo", "ATC": "ACT-Wazalendo", "CHAUMA": "CHAUMMA", "CHAUMM": "CHAUMMA"}
# The table's PDF pages (pages 58 to 67 of the Gazette), and the page with the national figures (page 57).
PAGES = range(12, 22)
NATIONAL_PAGE = 11
# Constituencies the table names otherwise than the National Assembly notice of the same Gazette does.
ALIASES = {
    'NYANG"HWALE': "NYANG”HWALE",
    "MPANDA VIJIJINI": "MPANDAVIJIJINI",
    "KIJITOUPELE": "KIJITO UPELE",
    "ULYANKULU": "ULYANKURU",
    "HANDENI MJI": "HANDENI MJINI",
}
# The table prints Morogoro Kusini twice; the second row is Morogoro Kusini Mashariki.
RENUMBERED = {169: "MOROGORO KUSINI MASHARIKI"}


@dataclass(frozen=True)
class Candidate:
    name: str
    party: str  # the registry's spelling
    votes: int
    elected: bool


@cache
def national(year: int) -> tuple[Candidate, ...]:
    """Each candidate's national votes, from the notice's text."""
    path = str(raw.cached(inec.SOURCE, inec.ELECTIONS[year].url).path)
    out: list[Candidate] = []
    if year == 2015:
        page = text.pdftext(path, NATIONAL_PAGE, NATIONAL_PAGE)
        for hit in re.finditer(
            r"^\s*\d\.\s+(?P<name>\S.*?\S)\s{2,}(?:ME|KE)\s+(?P<party>[A-Z]+)\s+(?P<votes>[\d,]+)(?P<won>\s+Aliyeshinda)?\s*$",
            page,
            re.MULTILINE,
        ):
            party = SPELLING.get(hit["party"], hit["party"])
            name = "Chief L. Yemba" if hit["name"] == "Chied L. Yemba" else hit["name"]
            out.append(Candidate(name, party, int(hit["votes"].replace(",", "")), bool(hit["won"])))
        if len(out) != 8 or sum(c.elected for c in out) != 1:
            raise ValueError(f"2015: {len(out)} candidates on page {NATIONAL_PAGE}")
        return tuple(out)
    # The notice's figures sit in the right-hand column of the first page.
    column = text.pdftext(path, 1, 1, 300, 320).split("MATOKEO YA UCHAGUZI WA RAIS, 2020", 1)[1]
    body = re.sub(r"\s+", " ", column.split("WILSON MAHERA")[0])
    body = re.sub(r"\b([A-Z]) (?=[A-Z])", r"\1", body)  # LISSU T U NDU A NTIPHAS: letters the text layer splits
    body = body.replace("CHAUMM…", "CHAUMMA …")
    for hit in re.finditer(r"(?P<label>(?:DKT\.\s*)?[A-Z][^.…\d]*?)\s*[.…]+[\s.…]*(?P<votes>\d{1,3}(?:,\d{3})*)", body):
        found = text.split_party(hit["label"])
        if found is None:
            raise ValueError(f"2020: no party in {hit['label']!r}")
        name, party = found
        out.append(Candidate(name, party, int(hit["votes"].replace(",", "")), False))
    if len(out) != 15:
        raise ValueError(f"2020: {len(out)} candidates")
    # The notice prints the votes only; the Commission declared the candidate with the most votes elected.
    best = max(out, key=lambda c: c.votes)
    return tuple(Candidate(c.name, c.party, c.votes, c is best) for c in out)


@dataclass(frozen=True)
class Row:
    number: int
    printed: str
    seat: inec.Seat
    values: dict[str, int | None]  # by column; None where the table prints a dash
    page: int
    boxes: dict[str, tuple[float, float, float, float]]


@cache
def rows() -> tuple[Row, ...]:
    """The 2015 table, row by row, each matched to its constituency in the National Assembly notice."""
    seats = {s.name: s for s in inec.seats(2015)}
    by_slug = {changeset.slug(name): s for name, s in seats.items()}
    keyed: dict[int, dict[str, list[str]]] = {}
    with TSV.open(newline="") as handle:
        for record in csv.DictReader(handle, delimiter="\t"):
            keyed.setdefault(int(record["number"]), {})[record["column"]] = [
                record["constituency"],
                record["value"],
                record["page"],
                record["x0"],
                record["y0"],
                record["x1"],
                record["y1"],
            ]
    out: list[Row] = []
    for number in sorted(keyed):
        cells = keyed[number]
        if tuple(cells) != COLUMNS:
            raise ValueError(f"row {number}: columns {tuple(cells)}")
        printed = cells["CCM"][0]
        name = RENUMBERED.get(number, ALIASES.get(printed, printed))
        seat = seats.get(name) or by_slug.get(changeset.slug(name))
        if seat is None:
            raise ValueError(f"row {number}: {printed} is not a constituency of the 2015 notice")
        out.append(
            Row(
                number,
                printed,
                seat,
                {c: None if cells[c][1] == "-" else int(cells[c][1]) for c in COLUMNS},
                int(cells["CCM"][2]),
                {c: (float(cells[c][3]), float(cells[c][4]), float(cells[c][5]), float(cells[c][6])) for c in COLUMNS},
            )
        )
    if [r.number for r in out] != list(range(1, 265)) or len({id(r.seat) for r in out}) != 264:
        raise ValueError("the 2015 table does not list each of the 264 constituencies once")
    totals = national(2015)
    for index, column in enumerate(COLUMNS):
        summed = sum(r.values[column] or 0 for r in out)
        if summed != totals[index].votes:
            raise ValueError(
                f"2015 {column}: the constituencies sum to {summed:,}, the notice prints {totals[index].votes:,}"
            )
    return tuple(out)


def registry(p: Package) -> None:
    """The office and electoral system, added to the registry package."""
    p.add("office", id=PRESIDENCY, polity_id=inec.TANZANIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the United Republic of Tanzania", "en", "official")
    p.name(PRESIDENCY, "Rais wa Jamhuri ya Muungano wa Tanzania", "sw", "official", False)
    p.add("electoral_system", id=SYSTEM, polity_id=inec.TANZANIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The President is elected by the voters of the whole United Republic, by the most votes.",
        lang="en",
    )


def parties() -> set[str]:
    return {c.party for year in (2015, 2020) for c in national(year)}


def geo(p: Package) -> None:
    """The scheme of the national reporting unit of the 2020 result, added to the geo package."""
    p.add("scheme", code=SCHEME, polity_id=inec.TANZANIA, kind="administrative", rank=0, noun="nation")


METHOD_2015 = (
    "Votes for each candidate in each constituency, from the table the National Electoral Commission published with "
    "General Notice No. 1545 in the Government Gazette of 28 October 2016 (pages 58 to 67). The table is printed as "
    "page images, so each figure was keyed from the page and checked against a second, machine reading, and every "
    "figure carries its page and its place on the page. Each candidate's constituency figures add up to the national "
    "total the notice prints on page 57, which is stored as declared. The notice prints no registered voters, ballots "
    "cast, or valid votes. The candidate the notice marks as the winner (aliyeshinda) is stored as elected."
)
CAVEATS_2015 = (
    (
        "The table prints a dash, and no figure, for UPDP in Busokelo and in Bububu, and for every candidate except "
        "those of ACT-Wazalendo, CCM, and CHADEMA in Madaba. No votes are stored for those cells."
    ),
    (
        "The table names two rows Morogoro Kusini. The second, numbered 169, is stored as Morogoro Kusini Mashariki, the "
        "name the National Assembly notice of the same Gazette gives the constituency."
    ),
    (
        "The notice's text names the second candidate Chied L. Yemba and the first candidate's party ATC; the table names "
        "them CHIEF L. YEMBA and ACT. The candidate is stored as Chief L. Yemba of ACT-Wazalendo."
    ),
)
METHOD_2020 = (
    "Votes for each candidate in the United Republic, as the National Electoral Commission published them in the "
    "special Government Gazette of 5 January 2021 (General Notice No. 30A). The notice prints no figures below the "
    "national level, and no registered voters, ballots cast, or valid votes. It lists the votes only; the candidate "
    "with the most votes, whom the Commission declared elected, is stored as elected."
)


def build(year: int) -> Package:
    election = inec.ELECTIONS[year]
    day = election.day
    p = Package(
        f"{inec.SOURCE}/president-{year}",
        inec.SOURCE,
        f"The {year} Union presidential election of Tanzania"
        + (", by constituency" if year == 2015 else ", national totals")
        + ", from the Government Gazette",
        depends=[f"{inec.SOURCE}/geo", f"{inec.SOURCE}/registry", f"{inec.SOURCE}/assembly-{year}"],
    )
    p.external |= {inec.TANZANIA, inec.TERRITORY}
    artifact = raw.cached(inec.SOURCE, election.url)
    p.uses(artifact)
    source = inec.SOURCES[year]
    election_id = inec.key("election", day)
    contest = key("contest", day, "president")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=inec.TERRITORY,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Tanzanian presidential election", "en", "official")
    p.add("slug", path=f"tz/{day}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", day, "president")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD_2015 if year == 2015 else METHOD_2020,
        lang="en",
    )
    for index, caveat in enumerate(CAVEATS_2015 if year == 2015 else ()):
        p.add("note", id=key("note", result_set, index), entity_id=result_set, kind="caveat", body_md=caveat, lang="en")
    options: list[str] = []
    for position, c in enumerate(national(year), 1):
        person = key("person", day, "president", position)
        option = key("option", day, "president", position)
        party = inec.party_id(c.party)
        options.append(option)
        p.add("person", id=person)
        p.name(person, c.name, "sw", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_position=position,
            ballot_label=c.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", day, "president", position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=c.name,
            member_of_party_id=party,
            is_independent=False,
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            candidacy_id=None,
            kind="elected" if c.elected else "lost",
            seats=1 if c.elected else None,
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=c.votes)
    if year == 2020:
        unit = key("unit", day, "nation")
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=inec.TANZANIA,
            scheme_code=SCHEME,
            code=f"{day}:tz",
            area_relation="equals",
            area_id=inec.TERRITORY,
            kind="aggregate",
        )
        p.name(unit, "Tanzania", "sw", "official")
        p.add(
            "reporting_unit_area",
            unit_id=unit,
            area_id=inec.TERRITORY,
            method="official_list",
            approximate=False,
            partial=False,
        )
        for option, c in zip(options, national(year), strict=True):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=c.votes,
            )
        return p
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{source}\x1f{artifact.storage_key}"))
    pages: dict[int, str] = {}
    for page in PAGES:
        pages[page] = key("page", day, page)
        p.add(
            "source_page",
            id=pages[page],
            artifact_id=artifact_id,
            page_number=page,
            image_key=f"pages/{inec.SOURCE}/{artifact.sha256}-{page}.png",
            width=round(595.32 * DPI / 72),
            height=round(841.92 * DPI / 72),
        )
    for r in rows():
        tag = f"{day}:{changeset.slug(r.seat.area_region)}:{changeset.slug(r.seat.name)}"
        unit = inec.key("unit", tag)
        for option, column in zip(options, COLUMNS, strict=True):
            value = r.values[column]
            if value is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=DIGITIZED,
                value=value,
            )
            x0, y0, x1, y1 = r.boxes[column]
            p.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                page_id=pages[r.page],
                source_id=source,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )
    return p
