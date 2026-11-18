"""Palestine's local elections of 20 October 2012 from the Central Elections Commission (elections.ps).

The Cabinet called local elections in the West Bank for 20 October 2012. Of 353 localities, 272 took part: 93 voted
and 179 had one list or too few candidates for a contest and were declared elected by acclamation (tazkiya); 81 more
voted or were declared elected on 22 December 2012, and 36 on 1 June 2013. The Commission publishes the final results of
the 93 councils that voted as one PDF table per authority, with each list's candidates, votes, and seats and the
authority's registered voters and voters, and counts the 179 acclaimed councils' winners by gender on its page.
Every council is elected from closed lists by the Sainte-Laguë method with an 8 percent threshold. The tables carry a
text layer in English, and every figure keeps its page and box.
"""

import html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ps import cec_local
from ea.sources.ps.cec_local_tables import Box, Cell, number

SOURCE = "ps.cec_local12"
SITE = "https://www.elections.ps"
PALESTINE = cec_local.PALESTINE
COUNTRY = cec_local.COUNTRY
DAY = date(2012, 10, 20)
VOTES, REGISTERED, CAST = 1, 2, 3
PDF = f"{SITE}/Portals/0/pdf/LE2012"
FILES = {
    "page": f"{SITE}/tabid/426/language/en-US/Default.aspx",
    "results": f"{PDF}/LE2012_FinalResults_EN.pdf",
    "details": f"{PDF}/LE2012-LocalitiesDetails.pdf",
    "summary": f"{PDF}/LE2012-SUMMARY-LocalitiesHaveElections.pdf",
}
THRESHOLD = 8
# the governorates by the letters of the name the tables print, with the area of each
DISTRICTS = {
    "jerusalem": ("Jerusalem", "PS-JEM"),
    "jenin": ("Jenin", "PS-JEN"),
    "tulkarem": ("Tulkarm", "PS-TKM"),
    "tubas": ("Tubas", "PS-TBS"),
    "nablus": ("Nablus", "PS-NBS"),
    "qalqilia": ("Qalqilya", "PS-QQA"),
    "salfeet": ("Salfit", "PS-SLT"),
    "ramallah": ("Ramallah and al-Bireh", "PS-RBH"),
    "jericho": ("Jericho", "PS-JRH"),
    "bethlehem": ("Bethlehem", "PS-BTH"),
    "hebron": ("Hebron", "PS-HBN"),
}
SYSTEM_NOTE = (
    "Local Council Elections Law no. 10 of 2005 elects each council from closed lists by proportional "
    "representation. A list that gets at least 8 percent of the valid votes shares the seats by the Sainte-Laguë "
    "method, dividing its votes by 1, 3, 5, and so on, and its seats go to its candidates in the order of the list."
)
LIST_METHOD = (
    "Counted by the Central Elections Commission for one local authority. Each voter chose one closed list. The "
    "table gives each list's candidates, votes, and seats, with the number of seats on the council, the authority's "
    "registered voters, and the voters who took part. It prints no valid, invalid, or blank totals, so none is "
    "stored, and no candidate names, so the winners are the lists' seats. Seats are stored as declared."
)
TACIT_METHOD = (
    "Of the 272 localities in the elections of 20 October 2012, 179 had no contest: the Commission declared the "
    "candidates who nominated for their councils elected by acclamation (tazkiya). Its page counts the men and "
    "women among these winners for the West Bank as a whole and names no council, so the winners are stored as "
    "seats won by men and by women, as declared, for the 179 councils together."
)
# what the Commission's summary table prints for the 93 councils that voted: by governorate, the councils with 9, 11,
# 13, and 15 seats (the table's totals are 34, 24, 22, and 13)
SIZES = {"9": 34, "11": 24, "13": 22, "15": 13}
ACCLAIMED = {"localities": 179, "men": 1396, "women": 375, "9": 120, "11": 39, "13": 19, "15": 1}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
SYSTEM = key("electoral_system", "list")
WEST_BANK_BODY = key("body", "west_bank")


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".aspx" if url.endswith(".aspx") else ".pdf")
    raw.fetch(cec_local.GEOBOUNDARIES, cec_local.GEO_URL, suffix=".geojson")


def packages() -> list[str]:
    return [f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def parse() -> None:
    cec_local.geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


def path(name: str) -> Path:
    return raw.cached(SOURCE, FILES[name]).path


# --- the Commission's tables ----------------------------------------------------------------------------------------


def count(text: str) -> int:
    return int(re.sub(r"[^0-9]", "", text))


@dataclass(frozen=True)
class ListRow:
    candidates: Cell
    votes: Cell
    seats: Cell
    name: str


@dataclass
class Authority:
    district: str
    code: str
    name: str
    seats: Cell
    registered: Cell
    voters: Cell
    lists: list[ListRow]

    @property
    def ident(self) -> str:
        return f"{self.district}:{re.sub(r'[^a-z]', '', self.name.lower())}"


def _cell(words: list[tuple[float, float, float, float, str]], page: int) -> Cell:
    text = " ".join(w[4] for w in words)
    return Cell(
        text,
        Box(
            page, min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words)
        ),
    )


def _page(page: pymupdf.Page, number_: int) -> Authority:
    """One page's authority: its header and the lists on it (an authority with many lists fills two pages)."""
    words = [w[:5] for w in page.get_text("words")]
    header = sorted((w for w in words if 136 < w[1] < 140 and re.fullmatch(r"[\d,]+", w[4])), key=lambda w: w[0])
    if len(header) != 3:
        raise ValueError(f"page {number_}: the header holds {len(header)} figures")
    registered, voters, seats = (_cell([w], number_) for w in header)
    district = _cell([w for w in words if 76 < w[1] < 99 and 70 < w[0] < 160], number_).text
    name = _cell([w for w in words if 100 < w[1] < 123 and w[0] > 90], number_).text
    body = [w for w in words if w[1] > 225]
    figures = sorted(
        (w for w in body if re.fullmatch(r"[\d,]+", w[4]) and w[0] > 320), key=lambda w: (round(w[1] / 8), w[0])
    )
    if not figures or len(figures) % 3:
        raise ValueError(f"page {number_}: the table holds {len(figures)} figures")
    rows = []
    for i in range(0, len(figures), 3):
        triple = figures[i : i + 3]
        y = triple[0][1]
        if any(abs(w[1] - y) > 6 for w in triple):
            raise ValueError(f"page {number_}: the figures of a row do not line up: {[w[4] for w in triple]}")
        text = [w for w in body if w[0] < 320 and abs(w[1] - y) < 9]
        if not text:
            raise ValueError(f"page {number_}: a row at {y:.0f} has no list name")
        rows.append(
            ListRow(
                _cell([triple[0]], number_),
                _cell([triple[1]], number_),
                _cell([triple[2]], number_),
                " ".join(w[4] for w in sorted(text, key=lambda w: (round(w[1] / 4), w[0]))),
            )
        )
    label, code = DISTRICTS[re.sub(r"[^a-z]", "", district.lower())]
    return Authority(label, code, name, seats, registered, voters, rows)


def authorities() -> list[Authority]:
    """The councils that voted, in the order of the table; an authority's second page adds its lists to its first."""
    out: list[Authority] = []
    document = pymupdf.open(path("results"))
    for number_ in range(1, document.page_count + 1):
        authority = _page(document[number_ - 1], number_)
        if out and out[-1].ident == authority.ident:
            if count(out[-1].seats.text) != count(authority.seats.text):
                raise ValueError(f"{authority.name}: its pages print different seats")
            out[-1].lists.extend(authority.lists)
        else:
            out.append(authority)
    return out


def sainte_lague(votes: list[int], seats: int) -> list[int]:
    total = sum(votes)
    quotients = sorted(
        ((v / (2 * k + 1), -i) for i, v in enumerate(votes) if v * 100 >= THRESHOLD * total for k in range(seats)),
        reverse=True,
    )[:seats]
    won = [0] * len(votes)
    for _, i in quotients:
        won[-i] += 1
    return won


def _text(name: str) -> str:
    artifact = path(name)
    if artifact.suffix == ".aspx":
        text = re.sub(r"<[^>]*>", " ", artifact.read_text("utf-8", "replace"))
        return re.sub(r"\s+", " ", html.unescape(text))
    return " ".join(page.get_text() for page in pymupdf.open(artifact))


PAGE_NAMES = {
    "Jerusalem": "Jerusalem",
    "Jenin": "Jenin",
    "Tulkarem": "Tulkarm",
    "Tubas": "Tubas",
    "Nablus": "Nablus",
    "Qalqilia": "Qalqilya",
    "Salfit": "Salfit",
    "Ramallah& al-Bireh": "Ramallah and al-Bireh",
    "Jericho": "Jericho",
    "Bethlehem": "Bethlehem",
    "Hebron": "Hebron",
}


def page_figures() -> dict[str, int]:
    """The Commission's page counts the localities, the winners by acclamation by gender, and each governorate's."""
    text = _text("page")
    found: dict[str, int] = {}
    match = re.search(r"(\d+) out of a total of (\d+) localities participated", text)
    if match is None:
        raise ValueError("the Commission's page does not give its localities")
    found["participated"], found["total"] = int(match.group(1)), int(match.group(2))
    match = re.search(r"won by acclamation Number of men ([\d,]+) Number of women ([\d,]+)", text)
    if match is None:
        raise ValueError("the Commission's page does not give the winners by acclamation")
    found["men"], found["women"] = count(match.group(1)), count(match.group(2))
    rows = re.findall(rf"\b({'|'.join(re.escape(n) for n in PAGE_NAMES)}) (\d+) ([\d,]+) ([\d,]+) [\d.]+%", text)
    if len(rows) != len(PAGE_NAMES):
        raise ValueError(f"the Commission's page gives {len(rows)} governorates' turnout")
    for district, localities, eligible, electors in rows:
        label = PAGE_NAMES[district]
        found[f"localities:{label}"] = int(localities)
        found[f"eligible:{label}"] = count(eligible)
        found[f"electors:{label}"] = count(electors)
    return found


def summary_check() -> None:
    """The summary's totals: 93 councils of 9, 11, 13, and 15 seats, and the acclaimed councils' sizes."""
    text = _text("summary")
    for pattern in (r"34\s+24\s+22\s+13\s+93", r"120\s+39\s+19\s+1\s+179", r"505,600"):
        if re.search(pattern, text) is None:
            raise ValueError(f"the Commission's summary does not print {pattern}")


def details_check(found: list[Authority]) -> None:
    """Each authority's registered voters are the eligible voters the Commission's localities table prints."""
    printed = Counter(re.findall(r"(?<![\d,])\d{1,3}(?:,\d{3})*(?![\d,])", _text("details")))
    needed = Counter(a.registered.text for a in found)
    missing = {value: n for value, n in needed.items() if printed[value] < n}
    if missing:
        raise ValueError(f"the localities table does not print the registered voters {missing}")


def verify(found: list[Authority]) -> dict[str, str]:
    """Checks the tables against the Commission's page and summary; returns how the totals compared."""
    figures = page_figures()
    summary_check()
    details_check(found)
    report: dict[str, str] = {}
    if len(found) != 93:
        raise ValueError(f"the tables name {len(found)} authorities, and the Commission's summary 93")
    sizes = Counter(str(count(a.seats.text)) for a in found)
    if dict(sizes) != SIZES:
        raise ValueError(f"the council sizes differ from the Commission's summary: {sizes}")
    if figures["participated"] != 272 or figures["total"] != 353:
        raise ValueError(f"the Commission's page counts {figures}")
    if ACCLAIMED["men"] != figures["men"] or ACCLAIMED["women"] != figures["women"]:
        raise ValueError("the winners by acclamation differ from the Commission's page")
    if sum(v * int(k) for k, v in ACCLAIMED.items() if k.isdigit()) != figures["men"] + figures["women"]:
        raise ValueError("the councils by acclamation do not add up to the winners by gender")
    ties: list[str] = []
    by_district: Counter[str] = Counter()
    eligible: Counter[str] = Counter()
    voters: Counter[str] = Counter()
    for authority in found:
        by_district[authority.district] += 1
        eligible[authority.district] += count(authority.registered.text)
        voters[authority.district] += count(authority.voters.text)
        seats = count(authority.seats.text)
        votes = [count(row.votes.text) for row in authority.lists]
        won = [count(row.seats.text) for row in authority.lists]
        if sum(won) != seats:
            raise ValueError(f"{authority.name}: {sum(won)} seats for a council of {seats}")
        expected = sainte_lague(votes, seats)
        if expected != won:
            moved = [votes[i] for i in range(len(won)) if won[i] != expected[i]]
            if len(set(moved)) != 1:
                raise ValueError(f"{authority.name}: the seats differ from the Sainte-Laguë allocation of its votes")
            ties.append(authority.name)
    differences = []
    for district in by_district:
        if (
            figures.get(f"eligible:{district}") != eligible[district]
            or figures.get(f"localities:{district}") != by_district[district]
        ):
            raise ValueError(
                f"{district}: {by_district[district]} authorities with {eligible[district]} registered voters, "
                f"not the page's {figures.get(f'localities:{district}')} with {figures.get(f'eligible:{district}')}"
            )
        gap = figures[f"electors:{district}"] - voters[district]
        if gap:
            differences.append(f"{district} {gap:+,}")
    report["allocation"] = (
        f"{len(found) - len(ties)} of {len(found)} councils' seats are the Sainte-Laguë allocation of their lists' "
        "votes with the 8 percent threshold"
        + (f"; in {', '.join(ties)} two lists tied on votes and the Commission settled the last seat" if ties else "")
    )
    report["registered"] = (
        f"{sum(eligible.values()):,} registered voters in {len(found)} authorities, as the Commission's page and "
        "summary total them by governorate"
    )
    report["voters"] = (
        f"{sum(voters.values()):,} voters in the tables against {sum(figures[f'electors:{d}'] for d in voters):,} "
        "electors the page counts, which include the security forces (differences by governorate: "
        + ", ".join(differences)
        + ")"
    )
    return report


# --- packages -------------------------------------------------------------------------------------------------------


def governorate(code: str) -> str:
    return cec_local.key("area", "ps.governorate", code)


def body(authority: Authority) -> str:
    return key("body", authority.ident)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Central Elections Commission as the source of the 2012 Palestinian local elections, the electoral "
        "system of the councils, and the councils the contests fill",
        depends=[f"{cec_local.SOURCE}/geo"],
    )
    p.external |= {PALESTINE, COUNTRY}
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title="Results of the local elections of 20 October 2012 published by the Central Elections Commission",
        url=FILES["page"],
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=PALESTINE,
        rules={"formula": "sainte_lague", "threshold": {"percent": THRESHOLD, "level": "list", "scope": "contest"}},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for authority in authorities():
        id = body(authority)
        p.add("body", id=id, polity_id=PALESTINE, kind="council")
        p.name(id, f"{authority.name} local council", "en", "english")
    p.add("body", id=WEST_BANK_BODY, polity_id=PALESTINE, kind="council")
    p.name(WEST_BANK_BODY, "Local councils of the West Bank", "en", "english")
    return p


def _source_pages(p: Package, name: str) -> dict[int, str]:
    artifact = raw.cached(SOURCE, FILES[name])
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CEC_SOURCE}\x1f{artifact.storage_key}"))
    pages: dict[int, str] = {}
    document = pymupdf.open(artifact.path)
    for number_ in range(1, document.page_count + 1):
        page = document[number_ - 1]
        pages[number_] = key("page", name, number_)
        p.add(
            "source_page",
            id=pages[number_],
            artifact_id=artifact_id,
            page_number=number_,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number_}.png",
            width=round(page.rect.width),
            height=round(page.rect.height),
        )
    return pages


def _put(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, cell: Cell, pages: dict[int, str]
) -> int:
    value = number(cell.text)
    box = cell.box
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
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=pages[box.page],
        source_id=CEC_SOURCE,
        x0=box.x0,
        y0=box.y0,
        x1=box.x1,
        y1=box.y1,
    )
    return value


def election() -> Package:
    found = authorities()
    report = verify(found)
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The Palestinian local elections of 20 October 2012: the councils of the 93 West Bank local authorities that "
        "voted, by authority, and the winners by acclamation of 179 more, from the Central Elections Commission",
        depends=[f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PALESTINE, COUNTRY}
    for url in FILES.values():
        p.uses(raw.cached(SOURCE, url))
    pages = _source_pages(p, "results")
    election_id = key("election", DAY)
    p.add("election", id=election_id, polity_id=PALESTINE, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "2012 Palestinian local elections", "en", "official")
    prefix = f"ps/{DAY}-local"
    p.add("slug", path=prefix, entity_id=election_id, is_current=True)
    seen: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    for authority in sorted(found, key=lambda a: (a.district, a.name)):
        seen[authority.district] += 1
        contest = key("contest", authority.ident)
        result_set = key("result_set", authority.ident)
        unit = key("unit", authority.ident)
        seats = count(authority.seats.text)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(authority),
            magnitude=seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{authority.name} local council, 2012", "en", "official")
        p.add(
            "slug",
            path=f"{prefix}/{changeset.slug(authority.district)}-{seen[authority.district]:03d}",
            entity_id=contest,
            is_current=True,
        )
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CEC_SOURCE,
            status="active",
            is_default=True,
        )
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=LIST_METHOD, lang="en"
        )
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=PALESTINE,
            scheme_code="ps.local_authority",
            code=f"{DAY}:{authority.district}:{authority.name}",
            area_relation="within",
            area_id=governorate(authority.code),
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
        p.name(unit, authority.name, "en", "english")
        totals["registered"] += _put(p, result_set, unit, None, REGISTERED, authority.registered, pages)
        totals["cast"] += _put(p, result_set, unit, None, CAST, authority.voters, pages)
        for position, row in enumerate(authority.lists, 1):
            option = key("option", authority.ident, position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                ballot_position=position,
                ballot_label=row.name,
            )
            won = count(row.seats.text)
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if won else "lost", seats=won)
            totals["votes"] += _put(p, result_set, unit, option, VOTES, row.votes, pages)
            totals["seats"] += won
        totals["authorities"] += 1
    _acclaimed(p, election_id, prefix)
    p.add(
        "note",
        id=key("note", election_id, "totals"),
        entity_id=election_id,
        kind="caveat",
        body_md=(
            f"The tables hold {totals['authorities']} authorities that voted, with {totals['seats']:,} council "
            f"seats, {totals['registered']:,} registered voters, {totals['cast']:,} voters, and {totals['votes']:,} "
            f"list votes. {report['voters'][0].upper() + report['voters'][1:]}. {report['allocation'][0].upper() + report['allocation'][1:]}. The Commission's page counts "
            "272 of 353 localities in these elections, 93 that voted and 179 by acclamation; the other 82 had no "
            "elections on this day, and 81 voted or were declared elected on 22 December 2012 and 36 on 1 June 2013 "
            "in complementary elections whose results the Commission's page does not publish."
        ),
        lang="en",
    )
    return p


def _acclaimed(p: Package, election_id: str, prefix: str) -> None:
    contest = key("contest", "acclaimed")
    result_set = key("result_set", "acclaimed")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="body_seats",
        body_id=WEST_BANK_BODY,
        magnitude=ACCLAIMED["men"] + ACCLAIMED["women"],
        round_number=1,
        default_result_kind="tacit",
    )
    p.name(contest, "West Bank local councils elected by acclamation, 2012", "en", "official")
    p.add("slug", path=f"{prefix}/acclaimed", entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="tacit",
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=TACIT_METHOD, lang="en")
    for position, (tag, label) in enumerate((("men", "Men elected"), ("women", "Women elected")), 1):
        option = key("option", "acclaimed", tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=position,
            ballot_label=label,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=ACCLAIMED[tag])
