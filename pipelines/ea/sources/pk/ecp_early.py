"""Pakistan's Election Commission: the National Assembly general elections of 1970, 1977, 1988, 1990, 1993, and 1997.

The 1970 and 1977 elections are read from the Commission's notifications of the members returned, which name the
member for each constituency and print no votes. The 1988, 1990, 1993, and 1997 elections are read from the
Commission's constituency-wise detailed results, a scanned book that prints the four elections side by side for each
constituency: every candidate with the party and votes polled, then the valid, rejected, and total votes, the registered
voters, and the percentage of votes polled. A multimodal model transcribed each page image into `ecp_early.json`, and
every table was checked against its own printed totals: the candidates' votes must sum to the valid votes, valid and
rejected votes must sum to the total, and the total over the registered voters must match the printed percentage. The
volume prints the winner's name in capitals, and the declared outcomes here follow that."""

import json
import re
import statistics
import subprocess
from datetime import date
from pathlib import Path
from typing import TypedDict

from ea import package, raw
from ea.package import Package
from ea.sources.pk import ecp

SOURCE = "pk.ecp"
HOST = "https://ecp.gov.pk/storage/files"
NOTICE_1970 = f"{HOST}/1/GE%2007-12-1970.pdf"
NOTICE_1977 = f"{HOST}/1/GE%2007-03-1977.pdf"
DETAILED = f"{HOST}/1/ge1988-cw1.pdf"
FILES = [NOTICE_1970, NOTICE_1977, DETAILED]
DAYS = {
    1970: date(1970, 12, 7),
    1977: date(1977, 3, 7),
    1988: date(1988, 11, 16),
    1990: date(1990, 10, 24),
    1993: date(1993, 10, 6),
    1997: date(1997, 2, 3),
}
TRANSCRIPT = Path(__file__).with_name("ecp_early.json")
REGISTRY = "pk.ecp/registry-early"
ECP_1970 = ecp.key("source", "ecp-1970")
ECP_1977 = ecp.key("source", "ecp-1977")
ECP_DETAILED = ecp.key("source", "ecp-detailed-1988-1997")
NOTICE_SOURCES = {1970: ECP_1970, 1977: ECP_1977}
NOTICE_URLS = {1970: NOTICE_1970, 1977: NOTICE_1977}

# The notifications number the seats of the general electorate from 1; the Assembly's reserved seats for women follow.
GENERAL_SEATS = {1970: {"NE": 162, "NW": 138}, 1977: {"NA": 200}}
PROVINCES = {"NE": "East Pakistan", "NW": "West Pakistan"}

# Rows of the 1970 notification whose cells the text layer breaks across pages in a way no column rule recovers.
NOTICE_FIXES = {
    (1970, "NE-159"): ("Chittagong-VII", "Mr. Ataur Rahman Khan"),
    (1970, "NE-160"): ("Chittagong-VIII", "Mr. Abu Saleh"),
    (1970, "NE-161"): ("Chittagong-IX", "Mr. Noor Ahmad"),
}

METHOD_NOTICE = (
    "Published by the Election Commission of Pakistan in its notification of the candidates returned to the National "
    "Assembly, which names the member for each constituency, with the father's name, and prints no votes. The "
    "Commission's notification prints no party for the members, so none is stored."
)
METHOD_DETAILED = (
    "Counted and published by constituency by the Election Commission of Pakistan in its constituency-wise detailed "
    "results. Valid votes are the votes polled by the candidates, total votes are valid and rejected votes together, "
    "and registered voters are the voters on the electoral roll of the constituency. The Commission does not publish "
    "the count of each polling station in this volume. The page was transcribed from the scanned book and each "
    "figure checked against the totals printed with it."
)

# The names the Commission's volumes give for the party abbreviations they print.
PARTY_NAMES = {
    "ANP": "Awami National Party",
    "ANP(A)": "Awami National Party (Ainee Group)",
    "AT(PG)": "Awami Tehreek (Paleejo Group)",
    "BNA": "Baluchistan National Alliance",
    "BNM": "Baluchistan National Movement",
    "HF": "Hazara Front",
    "HPG": "Haq Parast Group",
    "IJI": "Islami Jamhoori Ittehad",
    "JAH": "Jamiat Ahl-e-Hadis",
    "JAS": "Jamaat Ahl-e-Sunnat",
    "JASP": "Jamaat-e-Ahl-e-Sunnat Pakistan",
    "JUI(D)": "Jamiat-e-Ulema-e-Islam (Darkhasti Group)",
    "JUI(F)": "Jamiat-e-Ulema-e-Islam (Fazal-ur-Rehman Group)",
    "JUP(Niazi)": "Jamiat-ul-Ulema-e-Pakistan (Niazi Group)",
    "JUP(Noorani)": "Jamiat-ul-Ulema-e-Pakistan (Noorani Group)",
    "JWP": "Jamhoori Wattan Party",
    "NDP": "National Democratic Party",
    "NML(M)": "National Muslim League (Muhasba Group)",
    "NPP(K)": "National Peoples Party (Khar Group)",
    "PAI": "Pakistan Awami Ittehad",
    "PAT": "Pakistan Awami Tehreek",
    "PDP": "Pakistan Democratic Party",
    "PIP": "Pakistan Insaf Party",
    "PKMAP": "Pakhtoon Khawa Milli Awami Party",
    "PMAI": "Pakistan Milli Awami Ittehad (Pushtoon Khaw)",
    "PMKP": "Pakistan Mazdoor Kissan Party",
    "PML(F)": "Pakistan Muslim League (Forward Block)",
    "PML(MQ)": "Pakistan Muslim League (Malik Qasim Group)",
    "PML(Q)": "Pakistan Muslim League (Qaiyum Group)",
    "PNDA": "Pakistan National Democratic Alliance",
    "PNP": "Pakistan National Party",
    "PPIS": "Punjabi Pakhtoon Ittehad Sind",
    "PPP": "Pakistan Peoples Party",
    "PQLP": "Pakistan Qaumi Labour Party",
    "PQMA": "Pakistan Qaumi Mahaz-e-Azadi",
    "TIIP": "Tehreek-e-Inqalab-e-Islam Pakistan",
    "TNFJ": "Tehreek-e-Nafaz-e-Fiqah-e-Jafria",
    "WP": "Wattan Party",
}
# One abbreviation printed in several spellings, by the spelling without spaces.
PARTY_SPELLINGS = {
    "PML(J)(Chatta)": "PML(J)(Chattha)",
    "PML(J)(Chatha)": "PML(J)(Chattha)",
    "PPP(Z.A.Bhutto)": "PPP(ZAB)",
    "PK-QP": "PKQP",
}


class Member(TypedDict):
    code: str
    place: str
    name: str


ANCHOR = re.compile(r"^(\s*)(\d+)(\s+)(N[AEW]-\d+)(\s+)(?=\S)")
HEADER = re.compile(r"^\s*1\s+2\s+3\s+4\s*$")


def cells(line: str, base: int = 0) -> list[tuple[int, str]]:
    return [(base + m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def notice_lines(text: str) -> list[str]:
    """The notification's lines without its preamble, which a table's header row ends."""
    out: list[str] = []
    skip = False
    for line in text.split("\n"):
        if "NOTIFICATION" in line:
            skip = True
        if skip:
            skip = HEADER.match(line) is None
            continue
        out.append(line)
    return out


def tidy_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"^M\.r\s", "Mr. ", name)
    name = re.sub(r"^Mr\.?\s+", "", name)
    if sum(ch.isalpha() for ch in name) >= 3 and name == name.upper():
        name = re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), name)
    return name


def read_notice(year: int) -> list[Member]:
    """Each row of a notification, whose cells wrap over lines, by the paragraph its lines make and the column
    each cell starts in."""
    path = raw.cached(SOURCE, NOTICE_URLS[year]).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    rows: list[dict[str, object]] = []
    last: dict[str, object] | None = None
    for page in "\n".join(notice_lines(text)).split("\f"):
        lines = page.split("\n")
        anchors = [i for i, line in enumerate(lines) if ANCHOR.match(line)]
        if not anchors:
            continue
        starts: list[list[int]] = [[], [], []]
        mine: dict[int, dict[str, object]] = {}
        for i in anchors:
            m = ANCHOR.match(lines[i])
            assert m is not None
            rest = cells(lines[i][m.end() :], m.end())
            mine[i] = {"code": m.group(4), "items": [(i, p, t) for p, t in rest]}
            if len(rest) == 3:
                for k in range(3):
                    starts[k].append(rest[k][0])
        med = [statistics.median(s) for s in starts]
        for row in mine.values():
            row["med"] = med
        paragraphs: list[list[int]] = []
        current: list[int] = []
        for j, line in enumerate(lines):
            if line.strip():
                current.append(j)
            elif current:
                paragraphs.append(current)
                current = []
        if current:
            paragraphs.append(current)
        for n, paragraph in enumerate(paragraphs):
            inside = [j for j in paragraph if j in mine]
            for j in paragraph:
                if j in mine:
                    continue
                if inside:
                    target = mine[min(inside, key=lambda a: abs(a - j))]
                elif n == 0 and last is not None and j < anchors[0]:
                    target = last
                else:
                    target = mine[min(anchors, key=lambda a: abs(a - j))]
                items = target["items"]
                assert isinstance(items, list)
                items.extend((j, p, t) for p, t in cells(lines[j]))
        rows.extend(mine[i] for i in anchors)
        last = mine[anchors[-1]]
    found: list[Member] = []
    for row in rows:
        med = row["med"]
        items = row["items"]
        assert isinstance(med, list) and isinstance(items, list)
        columns: list[list[tuple[int, int, str]]] = [[], [], []]
        for j, p, t in items:
            columns[min(range(3), key=lambda k: abs(p - med[k]))].append((j, p, t))
        place, name, _ = (" ".join(t for _, _, t in sorted(column)) for column in columns)
        code = str(row["code"])
        if (year, code) in NOTICE_FIXES:
            place, name = NOTICE_FIXES[year, code]
        prefix, number = code.split("-")
        if int(number) > GENERAL_SEATS[year].get(prefix, 0):
            continue
        found.append({"code": code, "place": re.sub(r"\s*-\s*", "-", place).strip(), "name": tidy_name(name)})
    return found


def expected(year: int) -> list[str]:
    return [f"{prefix}-{n}" for prefix, count in GENERAL_SEATS[year].items() for n in range(1, count + 1)]


def notice_members(year: int) -> list[Member]:
    found = read_notice(year)
    codes = [m["code"] for m in found]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{year}: a constituency appears twice in the notification")
    for m in found:
        if len(m["name"]) < 4 or len(m["place"]) < 3:
            raise ValueError(f"{year}: {m['code']} read as {m['place']!r}, {m['name']!r}")
    return found


def fetch() -> None:
    for url in FILES:
        raw.fetch(SOURCE, url, suffix=".pdf")


Transcript = dict[str, dict[str, dict[str, object]]]


def parties_of(transcript: Transcript) -> dict[str, str]:
    """Each party abbreviation the detailed results print, with the name it stands for or the abbreviation itself."""
    found: dict[str, str] = {}
    for seats in transcript.values():
        for seat in seats.values():
            candidates = seat.get("candidates")
            assert isinstance(candidates, list)
            for c in candidates:
                abbreviation = abbreviate(str(c["party"] or ""))
                if abbreviation not in ("IND", ""):
                    found[abbreviation] = PARTY_NAMES.get(abbreviation, abbreviation)
    return found


def abbreviate(printed: str) -> str:
    squashed = re.sub(r"\s+", "", printed)
    return PARTY_SPELLINGS.get(squashed, squashed)


def party_id(abbreviation: str) -> str:
    return ecp.key("party", "early", abbreviation)


def transcript() -> Transcript:
    return json.loads(TRANSCRIPT.read_text())


def packages() -> list[str]:
    return [REGISTRY, *(f"pk.ecp/{day.isoformat()}" for day in DAYS.values())]


def parse() -> None:
    registry().write()
    for year in DAYS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Pakistani parties of the 1988 to 1997 National Assembly elections and the Election Commission's sources "
        "for the 1970 to 1997 elections",
        depends=["pk.ecp/registry"],
    )
    p.external.add(ecp.PAKISTAN)
    sources = (
        (ECP_1970, "General Election 1970: candidates returned to the National Assembly", [NOTICE_1970]),
        (ECP_1977, "General Election 1977: candidates returned to the National Assembly", [NOTICE_1977]),
        (ECP_DETAILED, "General Elections 1988, 1990, 1993, and 1997: constituency-wise detailed results", [DETAILED]),
    )
    for source, title, urls in sources:
        p.add(
            "source",
            id=source,
            kind="official_body",
            publisher="Election Commission of Pakistan",
            title=title,
            url=urls[0],
            license="Official publication; reuse terms are not stated",
            attribution="Election Commission of Pakistan",
        )
        for url in urls:
            p.artifact(raw.cached(SOURCE, url), source)
    for abbreviation, name in sorted(parties_of(transcript()).items()):
        p.add("party", id=party_id(abbreviation), kind="party", polity_id=ecp.PAKISTAN)
        p.name(party_id(abbreviation), name, "en", "official")
    return p


def build(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"pk.ecp/{day.isoformat()}",
        SOURCE,
        f"The Pakistani National Assembly general election of {year} by constituency, from the Election "
        "Commission's " + ("notification of the members returned" if year in NOTICE_URLS else "detailed results"),
        depends=["pk.ecp/registry", REGISTRY],
    )
    p.external.add(ecp.PAKISTAN)
    p.uses(raw.cached(SOURCE, NOTICE_URLS.get(year, DETAILED)))
    election = ecp.key("election", str(year))
    p.add("election", id=election, polity_id=ecp.PAKISTAN, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=f"pk/{day.isoformat()}-general", entity_id=election, is_current=True)
    p.name(election, f"{year} Pakistani general election", "en", "official")
    if year in NOTICE_URLS:
        members = notice_members(year)
        add_members(p, year, election, members)
    else:
        add_counted(p, year, election)
    return p


def contest_rows(
    p: Package, year: int, election: str, code: str, label: str, source: str, counted: bool, tacit: bool = False
) -> str:
    day = DAYS[year].isoformat()
    contest = ecp.key("contest", str(year), code)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=ecp.ASSEMBLY,
        magnitude=1,
        round_number=1,
        electoral_system_id=ecp.SYSTEM,
        default_result_kind="tacit" if tacit else "counted",
    )
    p.name(contest, f"{year} Pakistani National Assembly election, {code} {label}", "en", "official")
    p.add("slug", path=f"pk/{day}-general/{code.lower()}", entity_id=contest, is_current=True)
    result_set = ecp.key("result_set", str(year), code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="tacit" if tacit else "counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=None if tacit else 1.0 if counted else 0.0,
    )
    return result_set


def add_members(p: Package, year: int, election: str, members: list[Member]) -> None:
    found = {m["code"] for m in members}
    absent = [code for code in expected(year) if code not in found]
    prefixes = {code.split("-")[0] for code in expected(year)}
    seats = "; ".join(
        f"{prefix}-1 to {prefix}-{GENERAL_SEATS[year][prefix]}"
        + (f" ({PROVINCES[prefix]})" if len(prefixes) > 1 else "")
        for prefix in GENERAL_SEATS[year]
    )
    body = (
        f"The Commission's notification of the members returned numbers the seats of the general electorate "
        f"{seats} and names a member for {len(members)} of them."
    )
    if absent:
        body += " It names no member for " + ", ".join(absent) + ", which are not stored here."
    if year == 1970:
        body += (
            " It also names the members of the Assembly's seats reserved for women, which the members of the "
            "general seats elected, and those are not stored here."
        )
    p.add("note", id=ecp.key("note", election, "notice"), entity_id=election, kind="context", body_md=body, lang="en")
    for member in members:
        code = member["code"]
        label = member["place"]
        result_set = contest_rows(p, year, election, code, label, NOTICE_SOURCES[year], False)
        p.add(
            "note",
            id=ecp.key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD_NOTICE,
            lang="en",
        )
        contest = ecp.key("contest", str(year), code)
        option = ecp.key("option", str(year), code, 1)
        person = ecp.key("person", str(year), code, 1)
        p.add("person", id=person)
        p.name(person, member["name"], "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=1,
            ballot_label=member["name"],
        )
        p.add(
            "candidacy",
            id=ecp.key("candidacy", str(year), code, 1),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=member["name"],
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")


def is_winner(name: str) -> bool:
    """The volume prints the name of the member returned in capitals."""
    return sum(ch.isalpha() for ch in name) >= 3 and name == name.upper()


def seat_name(heading: str, code: str) -> str:
    name = re.sub(r"^\s*NA\s*-?\s*\d+\s*", "", heading).strip()
    return re.sub(r"\s+", " ", name) or code


def figures(t: dict[str, object]) -> list[tuple[int, str, str]]:
    return [
        (ecp.REGISTERED, "registered", "registered voters"),
        (ecp.CAST, "total", "total votes"),
        (ecp.INVALID, "rejected", "rejected votes"),
        (ecp.VALID, "valid", "valid votes"),
    ]


def caveats(t: dict[str, object], candidates: list[dict[str, object]], year: int) -> list[str]:
    found: list[str] = []
    votes = [c["votes"] for c in candidates if isinstance(c["votes"], int)]
    valid = t.get("valid")
    if isinstance(valid, int) and len(votes) == len(candidates) and sum(votes) != valid:
        found.append(
            f"The candidates' votes printed in the Commission's volume sum to {sum(votes):,}, while it prints "
            f"{valid:,} valid votes for the constituency. Both are stored as printed."
        )
    elif len(votes) < len(candidates):
        found.append(
            "The scan of the volume hides the votes of some candidates here, so those candidates are stored without "
            "votes."
        )
    valid, rejected, total = (t.get(k) for k in ("valid", "rejected", "total"))
    if isinstance(valid, int) and isinstance(rejected, int) and isinstance(total, int) and valid + rejected != total:
        found.append(
            f"The printed valid and rejected votes sum to {valid + rejected:,}, while the volume prints {total:,} "
            "total votes. All three are stored as printed."
        )
    missing = [label for _, key, label in figures(t) if not isinstance(t.get(key), int)]
    if missing:
        found.append(f"The volume's scan shows no {' or '.join(missing)} for this constituency, so it is not stored.")
    registered, cast, turnout = (t.get(k) for k in ("registered", "total", "turnout"))
    if (
        isinstance(registered, int)
        and isinstance(cast, int)
        and isinstance(turnout, (int, float))
        and registered
        and abs(cast / registered * 100 - turnout) > 0.02
    ):
        found.append(
            f"The volume prints {turnout}% of registered voters as the share polled, while its total and registered "
            f"voters give {cast / registered * 100:.2f}%."
        )
    if t.get("note"):
        found.append(f"The transcription of this table notes: {t['note']}")
    return found


def add_counted(p: Package, year: int, election: str) -> None:
    seats = transcript()[str(year)]
    left_out: list[str] = []
    for code, t in seats.items():
        if not re.fullmatch(r"NA-\d+", code):
            continue
        listed = t.get("candidates", [])
        assert isinstance(listed, list)
        candidates: list[dict[str, object]] = [c for c in listed if c["name"] or c["votes"] is not None]
        if not candidates:
            left_out.append(code)
            continue
        tacit = all(c["votes"] is None for c in candidates) and "ncontested" in str(t.get("note"))
        label = seat_name(str(t["heading"]), code)
        result_set = contest_rows(p, year, election, code, label, ECP_DETAILED, True, tacit)
        contest = ecp.key("contest", str(year), code)
        p.add(
            "note",
            id=ecp.key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD_DETAILED,
            lang="en",
        )
        for index, caveat in enumerate(caveats(t, candidates, year)):
            p.add(
                "note",
                id=ecp.key("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        unit = ecp.key("unit", str(year), code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ecp.PAKISTAN,
            scheme_code="pk.na",
            code=f"{DAYS[year].isoformat()}:{code}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, f"{code} {label}", "en", "official")
        for measure, key, _ in figures(t):
            value = t.get(key)
            if isinstance(value, int):
                ecp.put(p, result_set, unit, None, measure, value)
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        winners = [c for c in candidates if is_winner(str(c["name"] or ""))]
        for c in candidates:
            position = int(str(c["n"]))
            name = tidy_name(str(c["name"])) if c["name"] else f"Candidate {position} (name illegible in the scan)"
            abbreviation = abbreviate(str(c["party"] or ""))
            independent = abbreviation == "IND"
            option = ecp.key("option", str(year), code, position)
            person = ecp.key("person", str(year), code, position)
            p.add("person", id=person)
            p.name(person, name, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=ecp.key("candidacy", str(year), code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=None if independent or not abbreviation else party_id(abbreviation),
                is_independent=independent,
            )
            if len(winners) == 1:
                p.add(
                    "outcome", result_set_id=result_set, option_id=option, kind="elected" if c is winners[0] else "lost"
                )
            elif tacit:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if isinstance(c["votes"], int):
                ecp.put(p, result_set, unit, option, ecp.VOTES, c["votes"])
                p.add("declared", result_set_id=result_set, option_id=option, measure=ecp.VOTES, value=c["votes"])
    p.accept(
        "accounting.votes_valid",
        "On some tables the candidates' votes printed in the Commission's volume do not sum to the valid votes printed "
        "with them, or the scan hides the votes of some candidates; both figures are stored as printed, with a caveat "
        "on each table.",
    )
    absent = [f"NA-{n}" for n in range(1, 208) if f"NA-{n}" not in seats]
    body = (
        "The Commission's constituency-wise detailed results print the four elections of 1988, 1990, 1993, and 1997 "
        "side by side. The copy read here prints no table for " + ", ".join(absent) + ". "
        if absent
        else ""
    )
    if left_out:
        body += (
            "Its tables for " + ", ".join(left_out) + " hold no candidate for this election, so they are not stored."
        )
    p.add(
        "note",
        id=ecp.key("note", election, "left-out"),
        entity_id=election,
        kind="context",
        body_md=body or "None.",
        lang="en",
    )
