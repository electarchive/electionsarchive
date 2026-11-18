"""Pakistan's Election Commission: the 2013 general election, by constituency.

The National Assembly and the four provincial assemblies were elected on 11 May 2013. The results are read from the
text layer of the Commission's second report volume."""

import re
import subprocess
from datetime import date
from difflib import SequenceMatcher
from typing import NamedTuple

from ea import package, raw
from ea.package import Package
from ea.sources.pk import ecp

SOURCE = "pk.ecp"
DAY = date(2013, 5, 11)
PAGE = "https://web.archive.org/web/20251125182559id_/https://ecp.gov.pk/general-elections-2013"
VOLUME = "https://web.archive.org/web/20240227123216id_/https://ecp.gov.pk/storage/files/1/v2.pdf"
FILES = [PAGE, VOLUME]

HEADER = re.compile(r"^\s*(NA|PP|PS|PK|PB)\s*-?\s*(\d+)\s+(\S.*?)\s*$", re.IGNORECASE)
CANDIDATE = re.compile(r"^\s*(\d+)\s+(\S.*?)\s+(-?[\d,]+)\s*-?\s*$")
TOTAL = re.compile(
    r"^\s*(?:\d+\s+)?(Valid|Rejected|Total) Votes\s+([\d,.]+)\s*-?\s*$|^\s*(Regd)[.:]? Voters\s+([\d,.]+)\s*-?\s*$",
    re.IGNORECASE,
)


class Entry:
    def __init__(self, serial: int, name: str, party: str, votes: int, name_col: int, party_col: int | None) -> None:
        self.serial = serial
        self.name = name
        self.party = party
        self.votes = votes
        self.name_col = name_col
        self.party_col = party_col


class Seat:
    def __init__(self, code: str, name: str, line: int) -> None:
        self.code = code
        self.name = name
        self.line = line
        self.entries: list[Entry] = []
        self.totals: dict[str, int] = {}
        self.unpolled = ""
        self.open = False

    @property
    def prefix(self) -> str:
        return self.code.split("-")[0]

    @property
    def number(self) -> int:
        return int(self.code.split("-")[1])


def num(text: str) -> int:
    return int(re.sub(r"[,.]", "", text))


def read() -> list[Seat]:
    path = raw.cached(SOURCE, VOLUME).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    lines = text.split("\n")
    end = next(i for i, line in enumerate(lines) if line.startswith("BYE-ELECTIONS 2013"))
    seats: list[Seat] = []
    cur: Seat | None = None
    for n, line in enumerate(lines[:end], 1):
        stripped = line.strip()
        if not stripped:
            continue
        if match := HEADER.match(line):
            cur = Seat(f"{match.group(1).upper()}-{int(match.group(2))}", match.group(3), n)
            seats.append(cur)
        elif cur is None:
            continue
        elif match := TOTAL.match(line):
            key = (match.group(1) or match.group(3)).lower()
            cur.totals[key] = num(match.group(2) or match.group(4))
            cur.open = False
        elif "% of Votes" in line or "% Of Votes" in line:
            cur.open = False
        elif found := re.search(r"Postponed|Terminated|Stayy?ed", stripped):
            cur.unpolled = found.group(0).lower().replace("stayyed", "stayed")
        elif (match := CANDIDATE.match(line)) and not cur.totals:
            rest = match.group(2)
            chunks = re.split(r"\s{2,}", rest)
            name_col = match.start(2)
            party_col = name_col + rest.rindex(chunks[-1]) if len(chunks) > 1 else None
            name, party = (" ".join(chunks[:-1]), chunks[-1]) if len(chunks) > 1 else (rest, "")
            if party.startswith(name + " "):
                party = party[len(name) :].strip()  # the volume prints the name of one candidate in both columns
            cur.entries.append(Entry(int(match.group(1)), name, party, num(match.group(3)), name_col, party_col))
            cur.open = True
        elif cur.open and cur.entries and not re.fullmatch(r"[\d\s—–-]+", stripped) and not stripped.startswith("Sl."):
            entry = cur.entries[-1]
            col = len(line) - len(line.lstrip())
            if entry.party_col is not None and abs(col - entry.party_col) < abs(col - entry.name_col):
                entry.party += " " + stripped
            else:
                entry.name += " " + stripped
    return seats


def squash(text: str) -> str:
    """The comparison form of a party's printed name: lowercase letters only, with the I the volume prints as l."""
    return re.sub(r"[^a-z]", "", text.lower()).replace("l", "i")


# Printed forms of one party that the comparison of squashed names does not join, by printed form.
PRINTED = {
    "MQM": "Muttahidda Qaumi Movement",
    "Muhttahid Qaumi Movement (MQM)": "Muttahidda Qaumi Movement",
    "Mutahida Qaumi Moment Pakistan": "Muttahidda Qaumi Movement",
    "Muttahida Qaumi Movement Pakistan": "Muttahidda Qaumi Movement",
    "PML (N)": "Pakistan Muslim League (N)",
    "PML(N)": "Pakistan Muslim League (N)",
    "PML (F)": "Pakistan Muslim League (F)",
    "PML(F)": "Pakistan Muslim League (F)",
    "PPP (Shaheed Bhutto)": "Pakistan Peoples Party (Shaheed Bhutto)",
    "Majlis-e-Wahdat-ul-Muslimeen": "Majlis-e-Wahdat-e-Muslimeen Pakistan",
    "Jamiat-e-Ullama-e-Pakistan (Norrani)": "Jamiat Ulama-e-Pakistan (Noorani)",
    "PTI": "Pakistan Tehreek-e-Insaf",
    "PPPP": "Pakistan Peoples Party Parliamentarians",
    "JUI (F)": "Jamiat Ulama-e-Islam (F)",
    "JUI P(F)": "Jamiat Ulama-e-Islam (F)",
    "Jamat-e-Ulema Islam (F)": "Jamiat Ulama-e-Islam (F)",
    "PNDP": "Pakistan National Democratic Party",
    "Jamat-e-Islami": "Jamaat-e-Islami Pakistan",
    "Jamiat Ulama-e-Islam (Noorani)": "Jamiat Ulama-e-Pakistan (Noorani)",
    "Jamiat Ulama-e-Pakistan (N)": "Jamiat Ulama-e-Pakistan (Noorani)",
    "Mutthida Majlis-e-Amal Pakistan": "Muttahidda Majlis-e-Amal Pakistan",
    "People Party (Shaheed Bhutto)": "Pakistan Peoples Party (Shaheed Bhutto)",
    "Pakistan Peoples Party (Shaheed Bhutoo)": "Pakistan Peoples Party (Shaheed Bhutto)",
    "Awami National Party (ANP)": "Awami National Party",
    "(NPP National People Party": "National Peoples Party",
    "Pakistan Human Party": "Pakistan Human Party",
    "Pakistan Aman Party": "Pakistan Aman Party",
    "All Pakistan Muslim League": "All Pakistan Muslim League",
    "Pakistan Muslim League": "Pakistan Muslim League",
    "Pakistan Peoples Party": "Pakistan Peoples Party",
}
ALIASES = {squash(printed): name for printed, name in PRINTED.items()}


def unwrap(text: str) -> str:
    """A party's printed name without the brackets some rows wrap around it."""
    text = text.strip()
    if text.count("(") < text.count(")") or text.startswith(("(", "[")):
        text = text.lstrip("([").rstrip(")]")
        if text.count("(") > text.count(")"):
            text += ")"
    return text


def parts(text: str) -> tuple[str, str]:
    """The squashed name of a party without its bracketed suffix, and the squashed suffix."""
    found = re.search(r"\(([^)]*)\)?\s*$", text)
    return squash(re.sub(r"\([^)]*\)?\s*$", "", text)), squash(found.group(1)) if found else ""


def resolve(seats: list[Seat]) -> None:
    """Give every candidate the one spelling of their party that the volume prints most often, and find the party
    of a candidate whose party the volume runs into their name."""
    counts: dict[str, int] = {}
    for seat in seats:
        for entry in seat.entries:
            if entry.party and not re.search(r"\d", entry.party):
                text = unwrap(entry.party)
                counts[text] = counts.get(text, 0) + 1
    heads: list[tuple[str, str, str]] = []
    canonical: dict[str, str] = {}
    for text, _ in sorted(counts.items(), key=lambda item: -item[1]):
        if squash(text) in ALIASES:
            canonical[text] = ALIASES[squash(text)]
            continue
        base, suffix = parts(text)
        for head_base, head_suffix, name in heads:
            near = SequenceMatcher(None, head_base, base).ratio() >= 0.9
            nested = head_base != base and (head_base in base or base in head_base)
            if head_suffix == suffix and near and not nested:
                canonical[text] = name
                break
        else:
            heads.append((base, suffix, text))
            canonical[text] = text
    canonical |= PRINTED
    by_length = sorted(canonical, key=len, reverse=True)
    for seat in seats:
        for entry in seat.entries:
            if entry.party and not re.search(r"\d", entry.party):
                entry.party = canonical[unwrap(entry.party)]
                continue
            text = entry.name + " " + entry.party
            found: tuple[int, int, str] | None = None
            for printed in by_length:
                at = text.find(printed)
                end = at + len(printed)
                if at >= 0 and (end == len(text) or text[end] == " ") and (found is None or at < found[0]):
                    found = (at, end, printed)
            if found:
                before, after = text[: found[0]].strip(), text[found[1] :].strip()
                entry.name = f"{before} {after}".strip()
                entry.party = canonical[found[2]]
            else:
                entry.party = ""


def tidy(name: str) -> str:
    """A candidate's printed name with the I and W the volume prints as l and VV put right and its spaces collapsed."""
    name = re.sub(r"(?<![A-Za-z])l(?=[bcdfgjkmnpqrstvwxz])", "I", name)
    name = re.sub(r"VV(?=[a-z])", "W", name)
    return " ".join(name.split())


def place(name: str) -> str:
    """A constituency's printed name in capitals and small letters, with its roman numerals kept."""

    def word(match: re.Match[str]) -> str:
        text = match.group(0)
        if re.fullmatch(r"[IVXivx]+", text) and text.upper() in ROMAN:
            return text.upper()
        return text.lower() if text.lower() == "cum" else text.capitalize()

    text = re.sub(r"\s*-\s*", "-", " ".join(name.split()))
    text = re.sub(r"(?<=\S)\(", " (", re.sub(r"[A-Za-z]+", word, text))
    return text


ROMAN = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII"}
ROMAN |= {"XVIII", "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX"}


class Provincial(NamedTuple):
    prefix: str  # the key of the assembly in ecp.ASSEMBLIES
    label: str
    seats: int


ASSEMBLIES = {
    "NA": Provincial("NA", "National Assembly", 272),
    "PP": Provincial("PP", "Punjab", 297),
    "PS": Provincial("PS", "Sindh", 130),
    "PK": Provincial("PF", "Khyber Pakhtunkhwa", 99),
    "PB": Provincial("PB", "Balochistan", 51),
}


def seats() -> list[Seat]:
    found = read()
    resolve(found)
    for seat in found:
        seat.name = place(seat.name)
        for entry in seat.entries:
            entry.name = tidy(entry.name)
    for prefix, assembly in ASSEMBLIES.items():
        numbers = sorted(seat.number for seat in found if seat.prefix == prefix)
        if numbers != list(range(1, assembly.seats + 1)):
            raise ValueError(f"the volume does not list {prefix}-1 to {prefix}-{assembly.seats} once each")
    return found


PACKAGES = ["pk.ecp/registry-2013", "pk.ecp/2013-05-11"]
ECP_2013 = ecp.key("source", "ecp", "2013")
METHOD = (
    "Counted and published by constituency by the Election Commission of Pakistan in the second volume of its report "
    "on the 2013 general elections. Valid votes are the votes polled by the candidates, total votes are valid and "
    "rejected votes together, and registered voters are the voters on the electoral roll of the constituency. The "
    "volume does not publish the count of each polling station, and it does not mark the elected candidate."
)
ACCEPTED = {
    "accounting.votes_valid": "In 136 constituencies the candidates' votes printed in the Commission's volume do not "
    "sum to the valid votes it prints for them; both are stored as printed, with a caveat on each.",
    "accounting.ballots_sum": "In 95 constituencies the valid and rejected votes printed in the Commission's volume "
    "do not sum to the total votes it prints for them; all three are stored as printed, with a caveat on each.",
    "accounting.turnout": "The volume prints 373,232 total votes for NA-68, which has 230,311 registered voters, and "
    "1,047,063 total votes for PP-24, which has 174,407; both are stored as printed.",
}


def party_id(name: str) -> str:
    return ecp.key("party", "2013", name.casefold())


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".html" if url == PAGE else ".pdf")


def packages() -> list[str]:
    return PACKAGES


def parse() -> None:
    registry().write()
    build().write()


def emit() -> None:
    package.run("load", PACKAGES)


def registry() -> Package:
    p = Package(
        "pk.ecp/registry-2013",
        SOURCE,
        "The parties of the 2013 Pakistani general election, and the Election Commission's page for it",
        depends=["pk.ecp/registry"],
    )
    p.add(
        "source",
        id=ECP_2013,
        kind="official_body",
        publisher="Election Commission of Pakistan",
        title="General Elections 2013: report and constituency-wise detailed results",
        url="https://ecp.gov.pk/general-elections-2013",
        license="Official publication; reuse terms are not stated",
        attribution="Election Commission of Pakistan",
    )
    for url in FILES:
        p.artifact(raw.cached(SOURCE, url), ECP_2013)
    names = {entry.party for seat in seats() for entry in seat.entries}
    for name in sorted(names - {"", "Independent"}):
        id = party_id(name)
        p.add("party", id=id, kind="party", polity_id=ecp.PAKISTAN)
        p.name(id, name, "en", "official")
    p.external.add(ecp.PAKISTAN)
    return p


def caveats(seat: Seat) -> list[str]:
    found: list[str] = []
    totals = seat.totals
    votes = sum(entry.votes for entry in seat.entries)
    if totals.get("valid") and votes != totals["valid"]:
        found.append(
            f"The candidates' votes printed in the Commission's volume sum to {votes:,}, while it prints "
            f"{totals['valid']:,} valid votes for the constituency. Both are stored as printed."
        )
    if all(totals.get(key) for key in ("valid", "rejected", "total")):
        ballots = totals["valid"] + totals["rejected"]
        if ballots != totals["total"]:
            found.append(
                f"The printed valid and rejected votes sum to {ballots:,}, while the volume prints "
                f"{totals['total']:,} total votes. All three are stored as printed."
            )
    missing = [label for key, label in MEASURES.items() if not totals.get(key)]
    if missing:
        found.append(
            f"The volume prints no {' or '.join(missing)} for this constituency, or prints 0, so "
            f"{'it is' if len(missing) == 1 else 'they are'} not stored."
        )
    if any(not entry.party for entry in seat.entries):
        found.append(
            "The volume prints no party for some or all of the candidates here, so those candidates are stored "
            "without one."
        )
    return found


MEASURES = {
    "regd": "registered voters",
    "total": "total votes",
    "rejected": "rejected votes",
    "valid": "valid votes",
}
MEASURE_IDS = {"regd": ecp.REGISTERED, "total": ecp.CAST, "rejected": ecp.INVALID, "valid": ecp.VALID}
REASONS = {
    "postponed": "its poll was postponed for law and order",
    "terminated": "its proceedings were terminated under section 18 of the Representation of the People Act, 1976, "
    "on the death of a contesting candidate",
    "stayed": "the Supreme Court of Pakistan stayed the notification of its result",
}


def build() -> Package:
    p = Package(
        "pk.ecp/2013-05-11",
        SOURCE,
        "The Pakistani general election of 2013 for the National Assembly and the four provincial assemblies by "
        "constituency, from the Election Commission's detailed results",
        depends=["pk.ecp/registry", "pk.ecp/registry-2013"],
    )
    p.external.add(ecp.PAKISTAN)
    for url in FILES:
        p.uses(raw.cached(SOURCE, url))
    found = seats()
    election = ecp.key("election", "2013")
    p.add("election", id=election, polity_id=ecp.PAKISTAN, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path="pk/2013-05-11-general", entity_id=election, is_current=True)
    p.name(election, "2013 Pakistani general election", "en", "official")
    left_out = [seat for seat in found if not seat.entries]
    listed = "; ".join(f"{seat.code} ({REASONS[seat.unpolled]})" for seat in left_out)
    p.add(
        "note",
        id=ecp.key("note", election, "left-out"),
        entity_id=election,
        kind="context",
        body_md="The National Assembly and the provincial assemblies of the Punjab, Sindh, Khyber Pakhtunkhwa, and "
        "Balochistan were elected on the same day. The Commission's volume prints no result for "
        f"{len(left_out)} constituencies, so they are not stored here: {listed}.",
        lang="en",
    )
    for seat in found:
        if seat.entries:
            add_seat(p, seat, election)
    for code, reason in ACCEPTED.items():
        p.accept(code, reason)
    return p


def add_seat(p: Package, seat: Seat, election: str) -> None:
    assembly = ASSEMBLIES[seat.prefix]
    body = ecp.ASSEMBLIES[assembly.prefix]
    contest = ecp.key("contest", "2013", seat.code)
    label = f"{seat.code} {seat.name}"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body.body,
        magnitude=1,
        round_number=1,
        electoral_system_id=ecp.SYSTEM,
        default_result_kind="counted",
    )
    title = (
        "2013 Pakistani National Assembly election"
        if seat.prefix == "NA"
        else f"2013 {assembly.label} Provincial Assembly election"
    )
    p.name(contest, f"{title}, {label}", "en", "official")
    p.add("slug", path=f"pk/2013-05-11-general/{seat.code.lower()}", entity_id=contest, is_current=True)
    result_set = ecp.key("result_set", "2013", seat.code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ECP_2013,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=ecp.key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    for index, caveat in enumerate(caveats(seat)):
        p.add(
            "note",
            id=ecp.key("note", result_set, "caveat", index),
            entity_id=result_set,
            kind="caveat",
            body_md=caveat,
            lang="en",
        )
    unit = ecp.key("unit", "2013", seat.code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ecp.PAKISTAN,
        scheme_code=body.scheme,
        code=f"2013-05-11:{seat.code}",
        area_relation="unlocated",
        kind="standard",
    )
    p.name(unit, label, "en", "official")
    for key, measure in MEASURE_IDS.items():
        value = seat.totals.get(key)
        if value:
            ecp.put(p, result_set, unit, None, measure, value)
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, entry in enumerate(seat.entries, 1):
        option = ecp.key("option", "2013", seat.code, position)
        independent = entry.party == "Independent"
        person = ecp.key("person", "2013", seat.code, position)
        p.add("person", id=person)
        p.name(person, entry.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=entry.name,
        )
        p.add(
            "candidacy",
            id=ecp.key("candidacy", "2013", seat.code, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=entry.name,
            member_of_party_id=party_id(entry.party) if entry.party and not independent else None,
            is_independent=independent,
        )
        ecp.put(p, result_set, unit, option, ecp.VOTES, entry.votes)
        p.add("declared", result_set_id=result_set, option_id=option, measure=ecp.VOTES, value=entry.votes)
