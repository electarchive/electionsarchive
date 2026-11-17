"""Hong Kong District Council ordinary elections of 1999 to 2015 and by-elections of 2000 to 2019, from the reports the
Electoral Affairs Commission published.

Each ordinary election's report lists every constituency's candidates with their votes in a results appendix; each
by-election's report does the same for its one or two constituencies. A constituency with one nominated candidate has a
tacit result. The 18 District Council bodies and the electoral system come from the `hk.reo` registry.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.hk import reo

SOURCE = "hk.eac_dc"
HOST = "https://www.eac.hk"
PAGE = f"{HOST}/en/elections/distco.html"
BASE = f"{HOST}/pdf/distco"
HONG_KONG = reo.HONG_KONG
VOTES = reo.VOTES

DISTRICTS = {
    "A": "Central & Western",
    "B": "Wan Chai",
    "C": "Eastern",
    "D": "Southern",
    "E": "Yau Tsim Mong",
    "F": "Sham Shui Po",
    "G": "Kowloon City",
    "H": "Wong Tai Sin",
    "J": "Kwun Tong",
    "K": "Tsuen Wan",
    "L": "Tuen Mun",
    "M": "Yuen Long",
    "N": "North",
    "P": "Tai Po",
    "Q": "Sai Kung",
    "R": "Sha Tin",
    "S": "Kwai Tsing",
    "T": "Islands",
}
LETTERS = {re.sub(r"[^a-z]", "", name.lower()): letter for letter, name in DISTRICTS.items()}
LETTERS["tseunwan"] = "K"  # the 1999 appendix's spelling

# Polling day -> (year label, results appendix URL). Each report's appendix lists every constituency in the election.
ORDINARY = {
    date(1999, 11, 28): f"{BASE}/en/1999dc_report/appendix%20iv_english.pdf",
    date(2003, 11, 23): f"{BASE}/en/2003dc_report/appendix4_e.pdf",
    date(2007, 11, 18): f"{BASE}/en/2007dc_report/2007dcereport_appendix5_e.pdf",
    date(2011, 11, 6): f"{BASE}/en/2011dc_report/2011dcereport_appendix5.pdf",
    date(2015, 11, 22): f"{BASE}/en/2015dc_report/2015dcereport_appendix5.pdf",
}
ORDINARY_REPORTS = {
    date(1999, 11, 28): [f"{BASE}/en/1999dc_report/appendix%20iv_english.pdf"],
    date(2003, 11, 23): [f"{BASE}/en/2003dc_report/full_dc_report_e.pdf"],
    date(2007, 11, 18): [f"{BASE}/en/2007dc_report/2007dcereport_detailreport_e.pdf"],
    date(2011, 11, 6): [f"{BASE}/en/2011dc_report/2011dcereport_detailreport.pdf"],
    date(2015, 11, 22): [f"{BASE}/en/2015dc_report/2015dcereport_detailreport.pdf"],
}


@dataclass(frozen=True)
class By:
    """One by-election constituency: its polling day, district letter, name, and results appendix (with the section
    of the appendix when it covers two constituencies)."""

    day: date
    letter: str
    name: str
    url: str
    section: str = ""


def _by(year: int, month: int, day: int, letter: str, name: str, path: str, section: str = "") -> By:
    return By(date(year, month, day), letter, name, f"{BASE}/{path}", section)


BY = [
    By(date(2000, 7, 23), "N", "Fung Tsui", f"{HOST}/pdf/e_report.pdf"),
    _by(2002, 11, 3, "G", "Kai Tak", "en/2002dc_report/2002kckt_appendix4_e.pdf"),
    _by(2003, 2, 9, "G", "Hoi Sham", "en/2003dc_report/2003kchs_appendix4_e.pdf"),
    _by(2003, 4, 6, "F", "Mei Foo", "en/2003dc_report/2003sspmf_appendix4_e.pdf"),
    _by(2003, 8, 3, "F", "Lai Kok", "en/2003dc_report/2003ssplk_appendix4_e.pdf"),
    _by(2004, 11, 21, "D", "Tin Wan", "en/2004dc_report/2004sdctw_appendix4_e.pdf"),
    _by(2005, 3, 6, "C", "Fort Street", "en/2005dc_report/2005edc_sspdc_Appendix_4_1_e.pdf"),
    _by(2005, 3, 6, "F", "Nam Cheong Central", "en/2005dc_report/2005edc_sspdc_Appendix_4_2_e.pdf"),
    _by(2005, 3, 20, "D", "Ap Lei Chau North", "en/2005dc_report/2005sdc_alcn_appendix4_e.pdf"),
    _by(2005, 10, 23, "J", "King Tin", "en/2005dc_report/2005ktdc_kt_appendix4_e.pdf"),
    _by(2006, 6, 11, "A", "Centre Street", "en/2006dc_report/2006wcdc_cs_appendix4_e.pdf"),
    _by(2006, 9, 10, "C", "Tsui Wan", "en/2006dc_report/2006edc_tw_appendix4_e.pdf"),
    _by(2007, 3, 11, "R", "Kam Ying", "en/2007dc_report/2007std_ky_appendix4_e.pdf"),
    _by(2007, 5, 20, "J", "Kai Yip", "en/2007dc_report/2007ktd_ky_appendix4_e.pdf"),
    _by(2007, 6, 10, "P", "Hong Lok Yuen", "en/2007dc_report/2007tpd_hly_appendix4_e.pdf"),
    _by(2007, 7, 29, "G", "Hung Hom Bay", "en/2007dc_report/2007kcd_hhb_appendix4_e.pdf"),
    _by(2008, 10, 5, "E", "Jordan East", "en/2008dc_report/2008ytmd_je_appendix4_e.pdf"),
    _by(2008, 11, 30, "H", "Tsz Wan West", "en/2008dc_report/2008wtsd_tww_appendix4_e.pdf"),
    _by(2009, 3, 29, "R", "Tai Wai", "en/2008dc_report/2009std_tw_appendix4_e.pdf"),
    _by(2009, 6, 21, "B", "Canal Road", "en/2008dc_report/2009wcd_cr_appendix4_e.pdf"),
    _by(2009, 9, 6, "S", "Kwai Shing East Estate", "en/2008dc_report/2009ktd_ksee_appendix4_e.pdf"),
    _by(2010, 9, 5, "D", "Pokfulam", "en/2010dc_report/2010sd_p_appendix4_e.pdf"),
    _by(2011, 6, 26, "M", "Shap Pat Heung North", "en/2011dc_report/2011yld_sphn_appendix4.pdf"),
    _by(2011, 7, 24, "K", "Fuk Loi", "en/2011dc_report/2011twd_fl_appendix4.pdf"),
    _by(2012, 11, 4, "R", "On Tai", "2012std_ot_report/en/appendix4.pdf"),
    _by(2013, 3, 10, "R", "Tin Sum", "2013st_ts_report/en/appendix4.pdf"),
    _by(2013, 5, 26, "J", "Ping Shek", "2013ktd_ps_report/en/appendix4.pdf"),
    _by(2013, 10, 27, "E", "King's Park", "2013ytmd_kp_report/en/appendix4.pdf"),
    _by(2014, 3, 23, "D", "South Horizons West", "2014sd_shw_report/en/appendix4.pdf"),
    _by(2014, 4, 27, "T", "Tung Chung North", "2014id_tcn_report/en/appendix4.pdf"),
    _by(2014, 5, 18, "C", "Nam Fung", "2014ed_nf_report/en/appendix4.pdf"),
    _by(2014, 9, 7, "T", "Peng Chau and Hei Ling Chau", "2014id_pchlc_report/en/appendix4.pdf"),
    _by(2015, 7, 19, "P", "San Fu", "2015tpd_sf_report/en/appendix4.pdf"),
    _by(2017, 11, 26, "A", "Peak", "2017cwd_report/en/appendix4.pdf", "A"),
    _by(2017, 11, 26, "A", "Tung Wah", "2017cwd_report/en/appendix4.pdf", "B"),
    _by(2018, 6, 10, "C", "Kai Hiu", "2018ed_kh_report/en/appendix4.pdf"),
    _by(2019, 3, 24, "E", "Tai Nan", "2019ytmd_tn_report/en/appendix4.pdf"),
    _by(2019, 7, 14, "M", "San Tin", "2019yld_report/en/appendix4.pdf"),
]

# The 2000 by-election's report prints the result in its text.
RESULT_2000 = re.compile(
    r"obtained ([\d,]+) votes and\s+(?:Mr|Ms)\s+([A-Za-z-]+ [A-Za-z-]+)\s+obtained ([\d,]+) votes\.\s+(?:Mr|Ms) \w+ was elected"
)
FIRST_2000 = re.compile(r"result of the by-election:\s+(?:Mr|Ms)\s+([A-Za-z-]+ [A-Za-z-]+)\s+obtained")

CLOSED = {"Elected", "Uncontested", "Duly-Elected"}
COUNT = re.compile(r"\d{1,3}(?:[ ,]\d{3})+|\d+|-")
HEADER = re.compile(
    r"Constituency|Candidate|Code|Number|Votes|Result|election|Election|Appendix|\(Page|Declared|Obtained|Received|\bRO\b"
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EAC_SOURCE = key("source", "eac")


def urls() -> list[str]:
    found = {PAGE, *ORDINARY.values(), *(b.url for b in BY)}
    for group in ORDINARY_REPORTS.values():
        found.update(group)
    return sorted(found)


def _get(url: str) -> raw.Artifact:
    return raw.fetch(SOURCE, url, suffix=".html" if url.endswith(".html") else ".pdf")


def _own(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def fetch() -> None:
    for url in urls():
        _get(url)


@functools.cache
def text(url: str) -> str:
    done = subprocess.run(
        ["pdftotext", "-layout", str(_own(url).path), "-"], capture_output=True, text=True, check=True
    )
    return done.stdout


@dataclass
class Cand:
    number: int | None
    name: str
    votes: int | None
    elected: bool
    done: bool = False
    head: int = 0  # name fragments printed above its row, when its row carries none
    lots: bool = False


@dataclass
class Cons:
    code: str
    name: str
    letter: str
    cands: list[Cand] = field(default_factory=list)

    @property
    def tacit(self) -> bool:
        return len(self.cands) == 1 and self.cands[0].votes is None


def clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def count(value: str) -> int | None:
    return None if value == "-" else int(re.sub(r"[ ,]", "", value))


def parts(line: str) -> tuple[int | None, str, str | None, str | None]:
    """A candidate row's number, name text, vote count, and result, each as the page prints it or empty."""
    body = line.strip()
    result = None
    closing = re.search(r"\s(Elected|Uncontested|Duly-Elected)$", body)
    if closing:
        result, body = closing.group(1), body[: closing.start()].rstrip()
    body = body.rstrip("*").rstrip()
    tokens = re.split(r"\s{2,}", body) if body else []
    votes = None
    if tokens and COUNT.fullmatch(tokens[-1]):
        votes = tokens.pop()
    elif tokens:
        tail = re.search(r"\s(\d{1,3}(?:,\d{3})+|\d+|-)$", tokens[-1])
        if tail:
            votes, tokens[-1] = tail.group(1), tokens[-1][: tail.start()]
    number = (
        int(tokens.pop(0)) if tokens and re.fullmatch(r"\d{1,2}", tokens[0]) and (len(tokens) > 1 or votes) else None
    )
    return number, clean(" ".join(tokens)), votes, result


def content(raw_text: str) -> list[str]:
    """The lines of a results appendix without blank lines, page numbers, and the headings repeated on each page."""
    lines = [line for line in raw_text.splitlines() if line.strip()]
    kept: list[str] = []
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if re.fullmatch(r"\s*\d+\s*", line) and "Appendix" in following:
            continue
        if HEADER.search(line) and not re.match(r"\s+[A-T]\d\d\s", line):
            continue
        kept.append(line)
    return kept


def _split(previous: Cand, pending: list[str], own_name: bool) -> int:
    """How many of the name fragments between two rows finish the previous candidate's name. A name printed over
    several lines is centered on its row, so a candidate whose row holds no name text has as many fragments below it
    as above it."""
    if own_name:
        return len(pending)
    name = previous.name
    taken = 0
    while taken < len(pending) and (pending[taken].startswith("(") or name.count("(") > name.count(")")):
        name = f"{name} {pending[taken]}"
        taken += 1
    if previous.head and not taken:
        taken = min(len(pending), previous.head)
    return taken


def collect(lines: list[str], cons: Cons) -> None:
    """Fills a constituency's candidates from the rows between its heading and the next, joining names that wrap."""
    pending: list[str] = []

    def start(number: int | None, name: str) -> Cand:
        nonlocal pending
        if cons.cands:
            taken = _split(cons.cands[-1], pending, bool(name))
            if taken:
                cons.cands[-1].name = clean(f"{cons.cands[-1].name} {' '.join(pending[:taken])}")
            pending = pending[taken:]
        cand = Cand(number, clean(" ".join([*pending, name])), None, False, head=0 if name else len(pending))
        pending = []
        cons.cands.append(cand)
        return cand

    for line in lines:
        number, name, votes, result = parts(line)
        last = cons.cands[-1] if cons.cands else None
        if number is None and votes is None and result is None:
            if last is not None and not last.done:
                last.name = clean(f"{last.name} {name}")
            elif not cons.cands and not pending and name != name.upper():
                cons.name = clean(f"{cons.name} {name}")  # a constituency name wrapped onto the next line
            else:
                pending.append(name)
            continue
        if number is not None:
            cand = start(number, name)
        elif name:
            cand = start(None, name)
        elif last is not None and not last.done:
            cand = last
        else:
            cand = start(None, "")
        if votes is not None:
            cand.votes = count(votes)
            cand.done = True
        if result is not None:
            cand.elected = True
            cand.done = True
    if pending and cons.cands:
        cons.cands[-1].name = clean(f"{cons.cands[-1].name} {' '.join(pending)}")


def ordinary(day: date) -> list[Cons]:
    """Every constituency in an ordinary election's results appendix, in the order printed."""
    out: list[Cons] = []
    district = ""
    blocks: list[tuple[Cons, list[str]]] = []
    for line in content(text(ORDINARY[day])):
        cells = re.split(r"\s{2,}", line.strip())
        if len(cells) == 1 and re.sub(r"[^a-z]", "", cells[0].lower()) in LETTERS:
            district = LETTERS[re.sub(r"[^a-z]", "", cells[0].lower())]
            continue
        found = re.match(r"\s+([A-T]\d\d)\s{2,}(\S.*?)\s*$", line)
        if found:
            if found.group(1)[0] != district:
                raise ValueError(f"{found.group(1)} is under district {district} in {day}")
            name, *rest = re.split(r"\s{2,}", found.group(2))
            cons = Cons(found.group(1), clean(name), district)
            out.append(cons)
            blocks.append((cons, ["    " + "  ".join(rest)] if rest else []))
        elif blocks:
            blocks[-1][1].append(line)
        else:
            raise ValueError(f"unplaced line {line!r}")
    for cons, lines in blocks:
        collect(lines, cons)
    return out


def by_election(b: By) -> Cons:
    """The result of one by-election constituency from its report."""
    cons = Cons(slug(b.name), b.name, b.letter)
    body = text(b.url)
    if b.url.endswith("e_report.pdf"):
        flat = " ".join(body.split())
        first, second, votes = FIRST_2000.search(flat), RESULT_2000.search(flat), None
        if first is None or second is None:
            raise ValueError("unparsed 2000 by-election result")
        won = clean(first.group(1)).upper().replace("-", " ")
        other = clean(second.group(2)).upper().replace("-", " ")
        votes = (count(second.group(1)) or 0, count(second.group(3)) or 0)
        cons.cands = [Cand(1, won, votes[0], True), Cand(2, other, votes[1], False)]
        return cons
    lines = body.splitlines()
    if b.section:
        marks = [i for i, line in enumerate(lines) if re.match(r"\s*\([A-Z]\)\s", line)]
        at = ord(b.section) - ord("A")
        lines = lines[marks[at] : marks[at + 1] if at + 1 < len(marks) else len(lines)]
    drawn = any("drawing lots" in line for line in lines)
    lines = lines[: next((i for i, line in enumerate(lines) if line.lstrip().startswith("* ")), len(lines))]
    rows = content("\n".join(lines))
    collect(rows, cons)
    for cand in cons.cands:
        cand.lots = drawn and cand.elected
    return cons


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


METHOD = (
    "Votes are those the Electoral Affairs Commission printed for each candidate in the results appendix of its "
    "report on the election. Each elector votes for one candidate and the candidate with the most votes wins the "
    "constituency's one seat. A constituency with a single validly nominated candidate has a tacit result: the "
    "candidate was declared elected without a poll, so no votes are recorded. The appendix does not give registered "
    "electors, ballots cast, or invalid ballots for each constituency."
)
LOTS = (
    "Two candidates received the same number of votes, and the returning officer declared the elected candidate by "
    "drawing lots, in accordance with section 41(3) of the District Councils Ordinance."
)
FUNG_TSUI = (
    "One candidate in this constituency was ruled invalidly nominated, so the other was returned uncontested. On "
    "4 May 2000 the Court of First Instance found that ruling wrong and declared the winner not duly elected; the "
    "2000 by-election filled the seat."
)


def days() -> list[date]:
    return sorted({*ORDINARY, *(b.day for b in BY)})


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day.isoformat()}" for day in days())]


def parse() -> None:
    registry().write()
    for day in days():
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Electoral Affairs Commission as the source of Hong Kong's District Council election results",
    )
    p.add(
        "source",
        id=EAC_SOURCE,
        kind="official_body",
        publisher="Electoral Affairs Commission, Hong Kong",
        title="Reports on the District Council elections and by-elections",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Electoral Affairs Commission, Hong Kong Special Administrative Region Government",
    )
    for url in urls():
        p.artifact(_own(url), EAC_SOURCE)
    return p


def build(day: date) -> Package:
    iso = day.isoformat()
    p = Package(
        f"{SOURCE}/{iso}",
        SOURCE,
        f"Hong Kong District Council {'ordinary election' if day in ORDINARY else 'by-election'} of {day} by "
        "constituency, from the Electoral Affairs Commission's report",
        depends=["hk.reo/registry", f"{SOURCE}/registry"],
    )
    p.external.add(HONG_KONG)
    if day in ORDINARY:
        conses = ordinary(day)
        urls_used = [ORDINARY[day]]
        election_kind, noun = "local", "election"
        name = f"{day.year} Hong Kong District Council ordinary election"
        slug_ = f"hk/{iso}-district-councils"
    else:
        found = [b for b in BY if b.day == day]
        conses = [by_election(b) for b in found]
        urls_used = sorted({b.url for b in found})
        election_kind, noun = "by_election", "by-election"
        names = " and ".join(b.name for b in found)
        name = f"{day.day} {day:%B %Y} Hong Kong District Council by-election ({names})"
        slug_ = f"hk/{iso}-district-council-by-election"
    for url in urls_used:
        p.uses(_own(url))
    election = p.id("election", iso)
    p.add(
        "election",
        id=election,
        polity_id=HONG_KONG,
        kind=election_kind,
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=slug_, entity_id=election, is_current=True)
    p.name(election, name, "en", "official")
    if day in ORDINARY:
        p.name(election, f"{day.year}年區議會選舉", "zh", "native", False)
    for cons in conses:
        _contest(p, day, election, noun, slug_, cons)
    return p


def _contest(p: Package, day: date, election: str, noun: str, base: str, cons: Cons) -> None:
    iso = day.isoformat()
    tag = cons.code if day in ORDINARY else slug(cons.name)
    district = DISTRICTS[cons.letter]
    label = f"{day.year} {district} District Council {noun}, {cons.name} constituency" + (
        f" ({cons.code})" if day in ORDINARY else ""
    )
    contest = p.id("contest", iso, tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=reo.key("body", "dc", cons.letter),
        magnitude=1,
        round_number=1,
        electoral_system_id=reo.FPTP,
        default_result_kind="tacit" if cons.tacit else "counted",
    )
    p.name(contest, label, "en", "official")
    p.add("slug", path=f"{base}/{tag.lower()}", entity_id=contest, is_current=True)
    result_set = p.id("result_set", iso, tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if cons.tacit else "counted",
        source_id=EAC_SOURCE,
        status="active",
        is_default=True,
        completeness=None if cons.tacit else 1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    caveats = []
    if any(c.lots for c in cons.cands):
        caveats.append(LOTS)
    if day == date(1999, 11, 28) and district == "North" and cons.name == "Fung Tsui":
        caveats.append(FUNG_TSUI)
    for index, text_ in enumerate(caveats):
        p.add(
            "note",
            id=p.id("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=text_,
            lang="en",
        )
    unit = p.id("unit", iso, tag)
    if not cons.tacit:
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=HONG_KONG,
            scheme_code=reo.CONSTITUENCY_SCHEME,
            code=f"{iso}:{tag}",
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(unit, cons.name, "en", "official")
    for position, cand in enumerate(cons.cands, 1):
        person = p.id("person", iso, tag, position)
        option = p.id("option", iso, tag, position)
        p.add("person", id=person)
        p.name(person, cand.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=cand.number or position,
            ballot_label=cand.name,
        )
        p.add(
            "candidacy",
            id=p.id("candidacy", iso, tag, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=cand.name,
        )
        if cand.elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if cand.votes is not None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=cand.votes,
            )
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=cand.votes)
