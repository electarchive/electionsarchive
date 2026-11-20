"""Nova Scotia's provincial general elections and by-elections, by polling division, from Elections Nova Scotia.

The authority publishes one workbook of poll-by-poll results for each general election since 2017 (one sheet per
electoral district) and, for each by-election, a report whose last appendix holds the same table. The 2021 workbook
names candidates without their parties, so each candidate's party comes from the statement of votes printed in the
authority's report on that election, matched by the district and the candidate's votes.
"""

import functools
import io
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import uuid5

import fastexcel
import numpy
import pyproj
import shapefile
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.licenses import PUBLIC_RECORD, UNLICENSED

SOURCE: Final = "ca.ns"
SITE: Final = "https://electionsnovascotia.ca/files/"
# Canada's province polity and province area, which the federal pipeline holds under ids it minted itself.
NOVA_SCOTIA: Final = "01a13fb6-eab7-7501-a0f9-4901bd9f01f5"
BOUNDARIES: Final = SITE + "ElectoralDistrictMapInformation/NS_2026ED_Bnds.zip"
GENERALS: Final = {
    2017: SITE + "GeneralElection_40th/2017_PollbyPollResultsFinal.xlsx",
    2021: SITE + "GeneralElection_41st/41PGE_PollbyPoll_AllEDs_TurnOut_FINAL.xlsx",
    2024: SITE + "GeneralElection_42nd/42PGE_PollbyPoll_AllEDs_TurnOut_FINAL.xlsx",
}
GENERAL_DAYS: Final = {2017: date(2017, 5, 30), 2021: date(2021, 8, 17), 2024: date(2024, 11, 26)}
SOV_2021: Final = SITE + "GeneralElection_41st/ENS_Volume1_PostElectionReport.pdf"
SOV_2017: Final = SITE + "GeneralElection_40th/2017_SOVvolume1.pdf"
SOV_2024: Final = SITE + "GeneralElection_42nd/Volume1_Statistics_May2025.pdf"
PICTOU_WEST: Final = SITE + "PictouWestByElection/PictouWestByElection_Report.pdf"
CHETICAMP: Final = (
    SITE
    + "Ch%C3%A9ticamp-Margarees-Pleasant%20Bay%20By-election/PostElection/"
    + "Ch%C3%A9ticamp-Margarees-Pleasant%20Bay%202026%20byelection_ReportNonFinanacial_EN_FINAL.pdf"
)
REPORTS: Final = (PICTOU_WEST, CHETICAMP)


@dataclass
class Poll:
    code: str
    electors: int | None
    total: int
    votes: list[int]
    rejected: int
    declined: int


@dataclass
class District:
    number: int
    name: str
    candidates: list[tuple[str, str | None]]
    polls: list[Poll]
    totals: Poll
    elected: str
    notes: list[str] = field(default_factory=list)


def fetch() -> None:
    raw.fetch(SOURCE, BOUNDARIES, suffix=".zip")
    for url in GENERALS.values():
        raw.fetch(SOURCE, url, suffix=".xlsx")
    for url in (SOV_2017, SOV_2021, SOV_2024, *REPORTS):
        raw.fetch(SOURCE, url, suffix=".pdf")


def _number(text: str | None) -> int | None:
    text = (text or "").strip().replace(",", "")
    if not text:
        return None
    value = float(text)
    if value != int(value):
        raise ValueError(f"a count with a fraction: {text}")
    return int(value)


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _title(text: str) -> tuple[int, str]:
    found = re.fullmatch(r"\s*(\d+)\s*[- ]\s*(.*?)(?:\s+\d)?\s*", text)
    if found is None:
        raise ValueError(f"no district in the title {text!r}")
    return int(found.group(1)), _clean(found.group(2))


@functools.cache
def district(url: str, name: str) -> District:
    workbook = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = workbook.load_sheet_by_name(name, header_row=None, dtypes="string").to_polars()
    kept = [[(cell or "").replace("\xa0", " ") for cell in row] for row in frame.iter_rows()]
    rows = [[_clean(cell) for cell in row] for row in kept]
    number, title = _title(next(cell for cell in rows[0] if cell))
    head = next(i for i, row in enumerate(rows) if row[0] == "Poll")
    columns: dict[str, int] = {}
    candidates: list[tuple[int, str, str | None]] = []
    for index, label in enumerate(rows[head]):
        text = label.lower()
        if not text or index == 0:
            continue
        for key in ("polling location", "electors", "total", "turnout", "rejected", "declined"):
            if text.startswith(key):
                columns[key] = index
                break
        else:
            name_part, _, party = kept[head][index].partition("\n")
            if _clean(party).casefold() not in LABELS:
                name_part, party = _split_label(_clean(f"{name_part} {party}"))
            candidates.append((index, _clean(name_part), _clean(party) or None))
    polls: list[Poll] = []
    totals: Poll | None = None
    for row in rows[head + 1 :]:
        if row[columns["polling location"]].lower().startswith("total"):
            totals = _poll("Total", row, columns, candidates)
            break
        if row[0] and any(row[1:]):
            polls.append(_poll(row[0], row, columns, candidates))
    if totals is None:
        raise ValueError(f"{url} {name}: no total row")
    elected = ""
    for row in rows[head + 1 :]:
        for index, cell in enumerate(row):
            if cell.lower().startswith("elected"):
                elected = next((c for c in row[index + 1 :] if c), "")
    return District(number, title, [(n, p) for _, n, p in candidates], polls, totals, elected)


def _split_label(label: str) -> tuple[str, str]:
    """A header whose name and party share one line, as three of the 2017 headers do: the party is the longest
    ending the party labels know."""
    words = label.split()
    for count in (2, 1):
        if len(words) > count and " ".join(words[-count:]).casefold() in LABELS:
            return " ".join(words[:-count]), " ".join(words[-count:])
    return label, ""


def _poll(code: str, row: list[str], columns: dict[str, int], candidates: list[tuple[int, str, str | None]]) -> Poll:
    electors = _number(row[columns["electors"]]) if "electors" in columns else None
    return Poll(
        code=code,
        electors=electors,
        total=_number(row[columns["total"]]) or 0,
        votes=[_number(row[index]) or 0 for index, _, _ in candidates],
        rejected=_number(row[columns["rejected"]]) or 0 if "rejected" in columns else 0,
        declined=_number(row[columns["declined"]]) or 0 if "declined" in columns else 0,
    )


def sheets(url: str) -> list[str]:
    return list(fastexcel.read_excel(str(raw.cached(SOURCE, url).path)).sheet_names)


def pdf_text(url: str) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    )
    return result.stdout


STATEMENT_PARTIES: Final = (("Liberal", "NDP", "PC Party"), ("Green Party", "Atlantica", "Independent"))


def statement_parties(districts: list[District]) -> dict[tuple[int, int], str]:
    """The party of each 2021 candidate, read from the statement of votes the authority printed for that election.

    The statement lists the Liberal, NDP, and PC candidates beside each district's name, then the Green, Atlantica, and
    independent candidates on rows that carry only the district's total votes. A candidate's cell is found by its
    column, and it names the candidate by its printed first name and its votes.
    """
    lines = pdf_text(SOV_2021).split("\n")
    cells: dict[int, list[tuple[str, int, str]]] = {}
    totals = {d.totals.total: d for d in districts}
    mode, starts = 0, [0, 0, 0]
    for line in lines:
        found = [m.start() for m in re.finditer("Candidate Name", line)]
        if len(found) == 3:
            mode = 1 if line.lstrip().startswith("Electoral District") else 2
            starts = found
            continue
        if not mode:
            continue
        if mode == 1:
            label = re.match(r"^(\d\d) - ", line)
            if label is None:
                continue
            key = int(label.group(1))
            body = line[len(label.group(0)) :]
            offset = len(label.group(0))
        else:
            ending = re.search(r"(\d[\d,]*)\s*$", line)
            if ending is None or int(ending.group(1).replace(",", "")) not in totals:
                continue
            key = totals[int(ending.group(1).replace(",", ""))].number
            body, offset = line, 0
        names = STATEMENT_PARTIES[mode - 1]
        bounds = [start - 3 for start in starts] + [10_000]
        for index, party in enumerate(names):
            segment = body[max(bounds[index] - offset, 0) : max(bounds[index + 1] - offset, 0)]
            voted = re.search(r"([A-Za-z][^\d]*?)\s+(\d[\d,]*)\b", segment)
            if voted is not None:
                cells.setdefault(key, []).append(
                    (
                        _clean(voted.group(1)).replace("\u2019", "'").casefold(),
                        int(voted.group(2).replace(",", "")),
                        party,
                    )
                )
    found_party: dict[tuple[int, int], str] = {}
    for d in districts:
        for index, (name, _) in enumerate(d.candidates):
            first = name.split()[0].replace("\u2019", "'").casefold()
            votes = d.totals.votes[index]
            hits = {
                party for text, count, party in cells.get(d.number, []) if count == votes and text.startswith(first[:4])
            }
            if len(hits) != 1:
                raise ValueError(f"2021 {d.number} {d.name}: {name} with {votes} votes matches {sorted(hits)}")
            found_party[(d.number, index)] = hits.pop()
    return found_party


BY_ELECTIONS: Final = {
    date(2024, 5, 21): (
        PICTOU_WEST,
        "Pictou West",
        ["Clare Brett", "Melinda MacKenzie", "Marco MacLeod", "Mary Wooldridge-Elliott"],
        ["Green Party", "NSNDP", "PC", "Liberal"],
        ["Marco MacLeod"],
    ),
    date(2026, 6, 23): (
        CHETICAMP,
        "Chéticamp-Margarees-Pleasant Bay",
        ["Nik Boisvert", "Claude Bourgeois", "Denis Cormier", "Trevor Poirier"],
        ["Green Party", "PC", "Liberal", "NSNDP"],
        ["Claude Bourgeois"],
    ),
}
# The poll codes in the order the appendix prints them. The report prints a code beside, above, or below its figures,
# so the figures are read in order and each row takes the next code.
BY_ELECTION_POLLS: Final = {
    date(2024, 5, 21): [
        *(f"{n:03d}" for n in range(1, 22)),
        "022/023",
        "024",
        "025",
        "026",
        "027/028",
        "ADV1",
        "RO-CP",
        "WI",
    ],
    date(2026, 6, 23): [*(f"{n:03d}" for n in range(1, 10)), "010 Mobile1", "CPoll1", "CPoll2", "RO-CP", "WI"],
}


def by_election(day: date) -> District:
    """The poll-by-poll appendix of a by-election report: each row ends in its total, the candidates, and the rejected
    and declined ballots, and a row for an ordinary poll starts with the electors on the list."""
    url, name, candidates, parties, elected = BY_ELECTIONS[day]
    codes = BY_ELECTION_POLLS[day]
    text = pdf_text(url)
    start = text.index("Poll-by-Poll Results", text.rindex("Appendix C") if "Appendix C" in text else 0)
    width = len(candidates) + 3
    rows: list[list[int]] = []
    total: list[int] | None = None
    for line in text[start:].split("\n"):
        tokens = line.split()
        figures = 0
        while figures < len(tokens) and re.fullmatch(r"\d[\d,]*", tokens[-1 - figures]):
            figures += 1
        if figures < width:
            continue
        values = [int(token.replace(",", "")) for token in tokens[len(tokens) - figures :]]
        if tokens[0] == "Total" or (len(tokens) > len(values) and tokens[len(tokens) - figures - 1] == "Total"):
            total = values[-width:]
            continue
        rows.append(values)
    if total is None or len(rows) != len(codes):
        raise ValueError(f"{url}: {len(rows)} poll rows for {len(codes)} codes")
    polls: list[Poll] = []
    for code, values in zip(codes, rows, strict=True):
        electors = values[-width - 1] if len(values) > width else None
        polls.append(Poll(code, electors, values[-width], values[-width + 1 : -2], values[-2], values[-1]))
    totals = Poll("Total", None, total[0], total[1:-2], total[-2], total[-1])
    candidate_votes: list[tuple[str, str | None]] = [(n, p) for n, p in zip(candidates, parties, strict=True)]
    return District(0, name, candidate_votes, polls, totals, f"{elected[0]} ({parties[candidates.index(elected[0])]})")


# What the authority's own report on each general election prints for the whole province: the votes each party
# received, the candidates it nominated, and the seats it won. The build checks the poll-by-poll figures against them.
# The 2017 workbook leaves the Elected cell of two districts empty, and the 2024 workbook spells the member for Victoria-The
# Lakes Timmons where its own ballot header and the Statement of Votes say Timmins. The members the Statements of Votes
# list for these districts are the candidates with the most votes there.
ELECTED: Final = {
    (2017, 15): "Jamie Baillie (PC)",
    (2017, 50): "Bill Horne (NSLP)",
    (2024, 53): "Dianne Timmins (PC Party)",
}
REPORT_OF: Final = {2017: SOV_2017, 2021: SOV_2021, 2024: SOV_2024}
PUBLISHED: Final[dict[int, dict[str, dict[str, int]]]] = {
    2017: {
        "votes": {"lib": 158383, "pc": 143354, "ndp": 86299, "gpns": 11127, "atl": 1632, "ind": 447},
        "candidates": {"lib": 51, "pc": 51, "ndp": 51, "gpns": 32, "atl": 15, "ind": 3},
        "seats": {"lib": 27, "pc": 17, "ndp": 7},
    },
    2021: {
        "votes": {"pc": 162473, "lib": 155026, "ndp": 88477, "gpns": 9042, "atl": 1023, "ind": 4960},
        "candidates": {"lib": 55, "pc": 55, "ndp": 55, "gpns": 43, "atl": 16, "ind": 6},
        "seats": {"pc": 31, "lib": 17, "ndp": 6, "ind": 1},
    },
    2024: {
        "votes": {"pc": 187045, "lib": 80849, "ndp": 79068, "gpns": 2940, "ind": 4220},
        "candidates": {"lib": 55, "pc": 55, "ndp": 54, "gpns": 23, "ind": 4},
        "seats": {"pc": 43, "lib": 2, "ndp": 9, "ind": 1},
    },
}
PARTY_CODES: Final = {
    "pc": ("Progressive Conservative Association of Nova Scotia", "PC", "PC Party"),
    "lib": ("The Nova Scotia Liberal Party", "Liberal", "NSLP"),
    "ndp": ("Nova Scotia New Democratic Party", "NSNDP", "NDP"),
    "gpns": ("Green Party of Nova Scotia", "Green Party", "GPNS"),
    "atl": ("Atlantica", "Atlantica", "Atlantica"),
}
LABELS: Final = {
    "pc": "pc",
    "pc party": "pc",
    "nslp": "lib",
    "liberal": "lib",
    "nsndp": "ndp",
    "ndp": "ndp",
    "gpns": "gpns",
    "green party": "gpns",
    "green party of ns": "gpns",
    "atlantica": "atl",
    "independent": "ind",
    "ind": "ind",
}
EDS_FROM: Final = date(2019, 10, 30)
NEW_FROM: Final = date(2026, 4, 12)
PROVINCE_AREA: Final = "01a13fb6-eab7-7501-a0f9-48f48b94166e"
OFFICIAL: Final = "https://electionsnovascotia.ca/past-election-results"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ENS = key("source", "ens")
BOUNDARY_SOURCE = key("source", "boundaries")
HOUSE = key("body", "house_of_assembly")
FPTP = key("electoral_system", "fptp")
SET_2019 = key("boundary_set", "ca.ns", "2019")
SET_2026 = key("boundary_set", "ca.ns", "2026")


def party_code(label: str | None) -> str:
    code = LABELS.get((label or "").strip().casefold())
    if code is None:
        raise ValueError(f"unknown party label {label!r}")
    return code


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/ge-{year}" for year in GENERALS),
        *(f"{SOURCE}/be-{day}" for day in BY_ELECTIONS),
    ]


def area_2019(name: str) -> str:
    return key("area", "ed", changeset.slug(name))


def area_2012(name: str) -> str:
    return key("area", "ed2012", changeset.slug(name))


def poll_unit(day: date, number: int, code: str) -> str:
    return key("unit", f"{day}:{number:02d}:{code}")


def _wgs(shape: shapely.Geometry) -> shapely.Geometry:
    project = pyproj.Transformer.from_crs(2961, 4326, always_xy=True)
    moved = shapely.transform(shape, lambda xy: numpy.column_stack(project.transform(xy[:, 0], xy[:, 1])))
    parts = [part for part in shapely.get_parts(shapely.make_valid(moved)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)


def _outline(p: Package, area: str, shape: shapely.Geometry, since: date, until: date | None, quality: str) -> None:
    shape = _wgs(shape)
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=since,
        valid_to=until,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality=quality,
        source_id=BOUNDARY_SOURCE,
    )


def _districts() -> list[tuple[str, str, shapely.Geometry]]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, BOUNDARIES).path)
    parts = {name.rsplit(".", 1)[1]: archive.read(name) for name in archive.namelist() if "." in name}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), dbf=io.BytesIO(parts["dbf"]), shx=io.BytesIO(parts["shx"]))
    merged: dict[str, tuple[str, list[shapely.Geometry]]] = {}
    for record, shape in zip(reader.records(), reader.shapes(), strict=True):
        if record is None or shape is None:
            raise ValueError("a boundary record without its shape")
        number = str(record[0]).strip().rstrip("-")
        geometry = shapely.make_valid(shapely.geometry.shape(shape.__geo_interface__))
        merged.setdefault(number, (str(record[1]).strip(), []))[1].append(geometry)
    return [(number, name, shapely.union_all(shapes)) for number, (name, shapes) in sorted(merged.items())]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Nova Scotia's electoral districts: the 55 of 2019 and the 56 of 2026, from Elections Nova Scotia's "
        "electoral district boundaries, and the 51 districts of the 2017 election by name",
    )
    p.external |= {NOVA_SCOTIA, PROVINCE_AREA}
    artifact = raw.cached(SOURCE, BOUNDARIES)
    p.add(
        "source",
        id=BOUNDARY_SOURCE,
        kind="official_body",
        publisher="Elections Nova Scotia",
        title="Electoral district boundaries, 2026 (shapefile)",
        url="https://electionsnovascotia.ca/files/ElectoralDistrictMapInformation/NS_2026ED_Bnds.zip",
        license=UNLICENSED,
        license_url=None,
        attribution="Elections Nova Scotia",
    )
    p.artifact(artifact, BOUNDARY_SOURCE)
    p.add(
        "scheme",
        code="ca.ns",
        polity_id=NOVA_SCOTIA,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca.ns_poll",
        polity_id=NOVA_SCOTIA,
        kind="precinct",
        rank=2,
        noun="polling division",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=SET_2019,
        scheme_code="ca.ns",
        vintage="2019",
        valid_from=EDS_FROM,
        valid_to=NEW_FROM,
        as_of=EDS_FROM,
    )
    p.add(
        "boundary_set",
        id=SET_2026,
        scheme_code="ca.ns",
        vintage="2026",
        valid_from=NEW_FROM,
        valid_to=None,
        as_of=NEW_FROM,
    )
    shapes = {number: (name, shape) for number, name, shape in _districts()}
    for number, (name, shape) in shapes.items():
        area = area_2019(name)
        new = number == "56"
        p.add("area", id=area, scheme_code="ca.ns")
        p.add("area_code", area_id=area, code=number, valid_from=NEW_FROM if new else EDS_FROM, valid_to=None)
        p.add("area_parent", area_id=area, parent_id=PROVINCE_AREA, valid_from=NEW_FROM if new else EDS_FROM)
        p.name(area, name, "en", "official")
        if name == "Inverness":
            # The 2026 act cut the new district out of Inverness, so the district as it stood in 2019 is the two
            # shapes together.
            whole = shapely.union_all([shape, shapes["56"][1]])
            _outline(p, area, whole, EDS_FROM, NEW_FROM, "synthetic")
            _outline(p, area, shape, NEW_FROM, None, "official")
        else:
            _outline(p, area, shape, NEW_FROM if new else EDS_FROM, None, "official")
    for number, name in sorted(_names_2012().items()):
        area = area_2012(name)
        p.add("area", id=area, scheme_code="ca.ns")
        p.add("area_code", area_id=area, code=f"{number:02d}", valid_from=None, valid_to=EDS_FROM)
        p.add("area_parent", area_id=area, parent_id=PROVINCE_AREA, valid_from=None, valid_to=EDS_FROM)
        p.name(area, name, "en", "official")
    return p


def _names_2012() -> dict[int, str]:
    url = GENERALS[2017]
    names: dict[int, str] = {}
    for sheet_name in sheets(url):
        number, name = _title(district_title(url, sheet_name))
        names[number] = name
    return names


def district_title(url: str, name: str) -> str:
    workbook = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = workbook.load_sheet_by_name(name, header_row=None, dtypes="string").to_polars()
    return next(cell for cell in frame.row(0) if cell)


@dataclass
class Candidate:
    printed: str
    name: str
    party: str


@functools.cache
def _surnames() -> dict[str, str]:
    """Surnames as the authority spells them in mixed case, for the workbooks that print them in capitals."""
    known: dict[str, str] = {}
    sources: list[str] = []
    for url in GENERALS.values():
        for sheet_name in sheets(url):
            d = district(url, sheet_name)
            sources += [name for name, _ in d.candidates if name != name.upper()]
            sources.append(re.sub(r"\s*\(.*\)$", "", d.elected))
    for _, _, names, _, _ in BY_ELECTIONS.values():
        sources += names
    for url in (SOV_2021, SOV_2024):
        for word in set(re.findall(r"\b(?:Mc|Mac|O')?[A-Z][a-z]+[A-Z][A-Za-z]+\b", pdf_text(url))):
            known.setdefault(word.casefold(), word)
    for name in sources:
        for word in name.split()[1:]:
            if not word.isupper():
                known.setdefault(word.casefold(), word)
    return known


def _plain(name: str) -> str:
    return re.sub(r"[\W_]+", " ", name.casefold()).strip()


def _same(elected: str, candidate: Candidate) -> bool:
    """The declared member's name, which may drop a middle initial, and party are the candidate's."""
    declared = _plain(re.sub(r"\s*\([^)]*\)\s*$", "", elected)).split()
    printed = _plain(candidate.name).split()
    return bool(declared) and declared[0] == printed[0] and declared[-1] == printed[-1]


def proper(printed: str) -> str:
    """A name with its capitals restored: `Cheryl BURBIDGE` is Cheryl Burbidge."""
    known = _surnames()
    words: list[str] = []
    for word in printed.split():
        if len(word) > 1 and word.isupper():
            word = known.get(word.casefold()) or _capitalize(word)
        words.append(word)
    return " ".join(words)


def _capitalize(word: str) -> str:
    parts = []
    for piece in re.split(r"([-'\u2019])", word):
        text = piece.capitalize() if piece.isalpha() else piece
        if re.match(r"Ma?c[a-z]{3,}", text) and text.startswith("Mc"):
            text = "Mc" + text[2:].capitalize()
        parts.append(text)
    return "".join(parts)


def _candidates(d: District, parties: dict[tuple[int, int], str] | None = None) -> list[Candidate]:
    found = []
    for index, (printed, label) in enumerate(d.candidates):
        party = party_code(label if parties is None else parties[(d.number, index)])
        found.append(Candidate(printed, proper(printed), party))
    return found


@functools.cache
def general(year: int) -> list[tuple[District, list[Candidate]]]:
    url = GENERALS[year]
    districts = [district(url, name) for name in sheets(url)]
    parties = statement_parties(districts) if year == 2021 else None
    for d in districts:
        if (year, d.number) in ELECTED:
            d.elected = ELECTED[(year, d.number)]
            d.notes.append(
                "the workbook's Elected cell is empty or misspelled; the member is the one the Statement of Votes lists"
            )
    found = [(d, _candidates(d, parties)) for d in districts]
    expected = PUBLISHED[year]
    votes: dict[str, int] = {}
    nominated: dict[str, int] = {}
    seats: dict[str, int] = {}
    for d, candidates in found:
        for candidate, count in zip(candidates, d.totals.votes, strict=True):
            votes[candidate.party] = votes.get(candidate.party, 0) + count
            nominated[candidate.party] = nominated.get(candidate.party, 0) + 1
        winner = max(range(len(candidates)), key=lambda i: d.totals.votes[i])
        if not _same(d.elected, candidates[winner]):
            raise ValueError(
                f"{year} {d.name}: the authority declares {d.elected!r}, the most votes are {candidates[winner]}"
            )
        seats[candidates[winner].party] = seats.get(candidates[winner].party, 0) + 1
    for label, got in (("votes", votes), ("candidates", nominated), ("seats", seats)):
        if got != expected[label]:
            raise ValueError(f"{year} {label}: built {got}, the authority prints {expected[label]}")
    return found


def by_election_candidates(day: date) -> tuple[District, list[Candidate]]:
    d = by_election(day)
    return d, [Candidate(n, n, party_code(p)) for n, p in d.candidates]


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Nova Scotia's parties, the House of Assembly, and its single-member plurality system, from Elections Nova "
        "Scotia",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NOVA_SCOTIA}
    p.add(
        "source",
        id=ENS,
        kind="official_body",
        publisher="Elections Nova Scotia",
        title="Past election results: poll-by-poll results and post-election reports",
        url=OFFICIAL,
        license=PUBLIC_RECORD,
        license_url=None,
        attribution="Elections Nova Scotia",
    )
    for url in (*GENERALS.values(), SOV_2017, SOV_2021, SOV_2024, *REPORTS):
        p.artifact(raw.cached(SOURCE, url), ENS)
    p.add("body", id=HOUSE, polity_id=NOVA_SCOTIA, kind="unicameral")
    p.name(HOUSE, "Nova Scotia House of Assembly", "en", "official")
    p.add(
        "electoral_system",
        id=FPTP,
        polity_id=NOVA_SCOTIA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="One member is elected for each electoral district, by the most votes.",
        lang="en",
    )
    used: set[str] = set()
    for year in GENERALS:
        used |= {c.party for _, candidates in general(year) for c in candidates}
    for day in BY_ELECTIONS:
        used |= {c.party for c in by_election_candidates(day)[1]}
    for code in sorted(used - {"ind"}):
        official, short, abbreviation = PARTY_CODES[code]
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=NOVA_SCOTIA)
        p.add("external_id", entity_id=id, scheme="ca.ns.party", value=code)
        p.name(id, official, "en", "official")
        if short != official:
            p.name(id, short, "en", "short", False)
        if abbreviation not in (official, short):
            p.name(id, abbreviation, "en", "abbreviation", False)
    return p


ACCOUNTING_NOTE: Final = (
    "Elections Nova Scotia prints each polling division's electors on the final list, the total votes (all ballots "
    "cast, counting rejected and declined ballots), each candidate's votes, and the rejected and declined ballots. "
    "Valid votes here are the sum of the candidates' votes, rejected ballots are stored as invalid, and declined "
    "ballots, which an elector returned unmarked, are stored as blank. Advance polls, the continuous poll and "
    "write-in ballots at the returning office, and ballots cast by electors from other districts are reported for the "
    "district as a whole and stored as aggregate units."
)


def _kind(code: str) -> str:
    if "mobile" in code.lower():
        return "mobile"
    return "aggregate" if not code[0].isdigit() else "standard"


def _overcount(p: Package, found: list[tuple[str, District]]) -> None:
    """Note, for each result set, the polling divisions that cast more ballots than their electors on the final list."""
    total = 0
    for result_set, d in found:
        over = [poll.code for poll in d.polls if poll.electors is not None and poll.total > poll.electors]
        if not over:
            continue
        total += len(over)
        listed = ", ".join(over)
        p.add(
            "note",
            id=key("note", result_set, "overcount"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"The authority prints more ballots cast than electors on the final list in polling division(s) {listed}. "
            "The figures are stored as printed.",
            lang="en",
        )
    if total:
        p.accept(
            "accounting.turnout",
            f"Elections Nova Scotia prints more ballots cast than electors on the final list in {total} polling "
            "divisions; stored as printed",
        )


def _election_package(
    p: Package,
    day: date,
    election: str,
    items: list[tuple[District, list[Candidate]]],
    area: dict[int, str],
    boundary_set: str | None,
    path: str,
) -> None:
    found: list[tuple[str, District]] = []
    for d, candidates in items:
        ed = area[d.number]
        contest = key("contest", day, d.number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=HOUSE,
            district_area_id=ed,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
            boundary_set_id=boundary_set,
        )
        p.name(contest, d.name, "en", "official")
        p.add("slug", path=f"{path}/{changeset.slug(d.name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, d.number)
        found.append((result_set, d))
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=ENS,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=ACCOUNTING_NOTE,
            lang="en",
        )
        winner = max(range(len(candidates)), key=lambda i: d.totals.votes[i])
        options = []
        for index, candidate in enumerate(candidates):
            tag = f"{day}:{d.number}:{index + 1}"
            option = key("option", tag)
            person = key("person", tag)
            options.append(option)
            p.add("person", id=person)
            p.name(person, candidate.name, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if candidate.party == "ind" else key("party", candidate.party),
                ballot_position=index + 1,
                ballot_label=candidate.printed,
            )
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.printed,
                member_of_party_id=None if candidate.party == "ind" else key("party", candidate.party),
                is_independent=candidate.party == "ind",
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if index == winner else "lost")
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=d.totals.votes[index])
        for poll in d.polls:
            unit = poll_unit(day, d.number, poll.code)
            kind = _kind(poll.code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NOVA_SCOTIA,
                scheme_code="ca.ns_poll",
                code=f"{day}:{d.number:02d}:{poll.code}",
                area_relation="equals" if kind == "aggregate" else "within",
                area_id=ed,
                kind=kind,
            )
            accounting = [
                (2, poll.electors),
                (3, poll.total),
                (4, sum(poll.votes)),
                (5, poll.rejected),
                (6, poll.declined),
            ]
            for measure, value in accounting:
                if value is not None:
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
            for option, count in zip(options, poll.votes, strict=True):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=count,
                )
    _overcount(p, found)


def _area_map(year_or_day: object, names: dict[int, str], vintage: str) -> dict[int, str]:
    return {number: (area_2012 if vintage == "2012" else area_2019)(name) for number, name in names.items()}


def election(year: int) -> Package:
    day = GENERAL_DAYS[year]
    p = Package(
        f"{SOURCE}/ge-{year}",
        SOURCE,
        f"The {year} Nova Scotia general election by polling division, from Elections Nova Scotia",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NOVA_SCOTIA, PROVINCE_AREA}
    p.uses(raw.cached(SOURCE, GENERALS[year]))
    p.uses(raw.cached(SOURCE, REPORT_OF[year]))
    items = general(year)
    vintage = "2012" if year == 2017 else "2019"
    area = _area_map(year, {d.number: d.name for d, _ in items}, vintage)
    election_id = key("election", day)
    path = f"ca/ns/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=NOVA_SCOTIA,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{year} Nova Scotia general election", "en", "official")
    _election_package(p, day, election_id, items, area, None if year == 2017 else SET_2019, path)
    return p


def by_election_package(day: date) -> Package:
    d, candidates = by_election_candidates(day)
    url = BY_ELECTIONS[day][0]
    p = Package(
        f"{SOURCE}/be-{day}",
        SOURCE,
        f"The {d.name} by-election of {day} by polling division, from Elections Nova Scotia",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NOVA_SCOTIA, PROVINCE_AREA}
    p.uses(raw.cached(SOURCE, url))
    election_id = key("election", day)
    path = f"ca/ns/{day}-by-election"
    p.add(
        "election",
        id=election_id,
        polity_id=NOVA_SCOTIA,
        kind="by_election",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, f"{d.name} by-election, {day:%-d %B %Y}", "en", "official")
    d.number = int(day.strftime("%j"))
    _election_package(
        p,
        day,
        election_id,
        [(d, candidates)],
        {d.number: area_2019(d.name)},
        SET_2026 if day >= NEW_FROM else SET_2019,
        path,
    )
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in GENERALS:
        election(year).write()
    for day in BY_ELECTIONS:
        by_election_package(day).write()


def emit() -> None:
    package.run("load", packages())
