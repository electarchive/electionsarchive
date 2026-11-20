"""Palestine's local elections of 2017 from the Central Elections Commission (elections.ps).

The Commission publishes the 2017 local elections twice over. Its spreadsheets of statistics give the winners of the
13 May 2017 elections by governorate and political affiliation, and the winners of the complementary elections of
29 July 2017 by political affiliation, each split between councils that voted and councils whose candidates were
declared elected by acclamation (tazkiya). The 13 May contests are the eleven West Bank governorates, each holding
the winners of its councils (by vote and by acclamation together, the only split by governorate those files give);
the 29 July contests are the two national tallies, by vote and by acclamation.

Its PDF tables give the results of each council (`councils` and `councils17`): the registered voters, ballots cast,
and each list's votes and seats of the councils that voted, the winners' names, and the list each acclaimed council
declared. Those packages hold one contest for each council and attach to the elections above.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from uuid import uuid5

import fastexcel
import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ps import cec_local, councils17
from ea.sources.ps.councils17 import Cell, Row, Winner

SOURCE = "ps.cec_local17"
SITE = "https://www.elections.ps"
PALESTINE = cec_local.PALESTINE
MAY = date(2017, 5, 13)
JULY = date(2017, 7, 29)
FILES = {
    "may-page": f"{SITE}/tabid/178/language/en-US/Default.aspx",
    "july-page": f"{SITE}/tabid/357/language/en-US/Default.aspx",
    "may-affiliation": f"{SITE}/Portals/30/excel/LE2017/WinnersAccordingToPoliticalAffiliation-en.xlsx",
    "may-acclaimed": f"{SITE}/Portals/30/excel/LE2017/WinnersByAcclamationAccordingToPoliticalAffiliation-en.xlsx",
    "summary": f"{SITE}/Portals/30/excel/LE2017/SummaryOfLocalElections.xlsx",
    "july-voted": f"{SITE}/Portals/30/pdf/LE2017-CMP/LE2017-CMP-ElectoralStatisticsByVotingEN.xlsx",
    "july-acclaimed": f"{SITE}/Portals/30/pdf/LE2017-CMP/LE2017-CMP-ElectoralStatisticsTazkiehEN.xlsx",
}
# The winners' affiliations as the Commission prints them in Arabic (the district table) and in English (the national
# tables): the key used here, the Arabic label, the English label, and the party behind it.
AFFILIATIONS = {
    "independent": ("مستقل", "Independent", None),
    "coalition": ("ائتلاف", "Coalition", None),
    "fatah": ("حركة فتح", "Fatah", "fatah"),
    "dflp": ("الجبهة الديمقراطية لتحرير فلسطين", "The Democratic Front for the Liberation of Palestine", "dflp"),
    "fida": ("الاتحاد الديمقراطي الفلسطيني - فدا", "Palestinian Democratic Union-Fida", "fida"),
    "democratic_coalition": ("التحالف الديمقراطي", "Democratic Coalition", "democratic_coalition"),
    "pni": ("المبادرة الوطنية الفلسطينية", "Palestinian National Initiative", "pni"),
    "ppsf": ("جبهة النضال الشعبي الفلسطيني", "Palestinian Popular Struggle Front", "ppsf"),
    "ppp": ("حزب الشعب الفلسطيني", "Palestinian People Party", "ppp"),
}
BY_ARABIC = {arabic: k for k, (arabic, _, _) in AFFILIATIONS.items()}
BY_ENGLISH = {english: k for k, (_, english, _) in AFFILIATIONS.items()}
# The English district names of the gender tables, by the Arabic names of the district table.
ENGLISH = {
    "القدس": "Jerusalem",
    "جنين": "Jenin",
    "طولكرم": "Tulkarem",
    "طوباس": "Tubas",
    "نابلس": "Nablus",
    "قلقيلية": "Qalqilya",
    "سلفيت": "Salfit",
    "رام الله والبيرة": "Ramallah & Al-Bireh",
    "أريحا": "Jericho",
    "بيت لحم": "Bethlehem",
    "الخليل": "Hebron",
}
METHOD_MAY = (
    "The Central Elections Commission publishes the winners of the 13 May 2017 local elections as statistics: the "
    "number of winners of each political affiliation by governorate, for the councils that voted (145 localities) "
    "and the councils whose candidates were declared elected by acclamation (181 localities) together. It "
    "publishes no results by council, no votes, and no candidate names in these files. Each governorate's winners "
    "are stored as seats won by affiliation, as declared. Independents are every winner the Commission lists as "
    "independent; Coalition is every winner on a local coalition list; the named affiliations are the parties the "
    "Commission names. The governorate's winners add up to the winners of the national tables, and the "
    "Commission's gender table by governorate gives the same totals."
)
METHOD_VOTED = (
    "The Commission publishes the winners of the councils that voted in the complementary local elections of "
    "29 July 2017 (14 localities) by political affiliation for the whole West Bank, with no votes and no results by "
    "council. The seats are stored as declared."
)
METHOD_ACCLAIMED = (
    "No vote was held in 18 localities of the complementary local elections of 29 July 2017: the Commission "
    "declared the candidates who nominated for the councils' seats elected by acclamation (tazkiya). It publishes "
    "the winners by political affiliation for the whole West Bank, with no results by council. The seats are "
    "stored as declared."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
WEST_BANK_BODY = key("body", "west_bank")


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".aspx" if url.endswith(".aspx") else ".xlsx")
    for url in COUNCIL_FILES.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix if url.endswith((".pdf", ".xlsx")) else ".aspx")
    raw.fetch(cec_local.GEOBOUNDARIES, cec_local.GEO_URL, suffix=".geojson")


def packages() -> list[str]:
    return [
        f"{cec_local.SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/{MAY}",
        f"{SOURCE}/{JULY}",
        f"{SOURCE}/{MAY}-councils",
        f"{SOURCE}/{JULY}-councils",
    ]


def parse() -> None:
    cec_local.geo().write()
    registry().write()
    may().write()
    july().write()
    councils(MAY).write()
    councils(JULY).write()
    render_pages()


def emit() -> None:
    package.run("load", packages())


def path(name: str) -> Path:
    return raw.cached(SOURCE, FILES[name]).path


# --- the Commission's tables ----------------------------------------------------------------------------------------


def _sheet(name: str, title: str) -> list[tuple[object, ...]]:
    frame = fastexcel.read_excel(str(path(name))).load_sheet_by_name(title, header_row=None, dtypes="string")
    return [tuple(_cell(c) for c in row) for row in frame.to_polars().iter_rows()]


def _cell(text: str | None) -> int | str | None:
    """A cell as the workbook prints it: a whole number as an int, other numbers and text as they are."""
    if text is None:
        return None
    text = text.strip()
    return int(text) if re.fullmatch(r"\d+", text) else text


def _count(value: object) -> int:
    if not isinstance(value, int):
        raise TypeError(f"expected a count, found {value!r}")
    return value


def district_winners() -> dict[str, dict[str, int]]:
    """The winners of 13 May by district (Arabic name) and affiliation, from the Commission's long table."""
    out: dict[str, dict[str, int]] = {}
    for body, _, district, count in _sheet("july-voted", "Sheet1")[1:]:
        if body is None:
            continue
        out.setdefault(str(district).strip(), {})[BY_ARABIC[str(body).strip()]] = _count(count)
    return out


def affiliations(name: str, title: str, first: str) -> dict[str, int]:
    """A national table of winners by affiliation: the rows from the one that starts with `first`."""
    rows = _sheet(name, title)
    start = max(i for i, row in enumerate(rows) if first in row)
    out: dict[str, int] = {}
    for row in rows[start + 1 :]:
        label = next((c for c in row if isinstance(c, str)), None)
        if label is None:
            continue
        if label.strip() == "Total":
            out["total"] = _count(next(c for c in row if isinstance(c, int)))
            break
        out[BY_ENGLISH[label.strip()]] = _count(next(c for c in row if isinstance(c, int)))
    return out


def genders(name: str) -> dict[str, int]:
    """The winners by district (English name) from a workbook's Gender sheet, with their total under `total`."""
    rows = _sheet(name, "Gender")
    start = next(i for i, row in enumerate(rows) if row[0] == "District")
    out: dict[str, int] = {}
    for row in rows[start + 1 :]:
        label = re.sub(r"\s*&\s*", " & ", str(row[0]).strip())
        if label == "Total":
            out["total"] = _count(row[5])
            break
        out[label] = _count(row[5])
    return out


def gender_total(name: str) -> int:
    """The winners' total on a workbook's Gender sheet, whatever its layout."""
    row = next(r for r in _sheet(name, "Gender") if r[0] == "Total")
    return max(c for c in row if isinstance(c, int))


def locality_counts() -> dict[str, tuple[int, int]]:
    """The Commission's pages name the localities that voted and those declared by acclamation."""
    out: dict[str, tuple[int, int]] = {}
    for name in ("may-page", "july-page"):
        text = re.sub(r"<[^>]*>|&nbsp;", " ", path(name).read_text("utf-8", "replace"))
        voted = re.search(r"Winners By elections in (\d+) localities", text)
        acclaimed = re.search(r"Winners By Acclamation in (\d+) localities", text)
        if voted is None or acclaimed is None:
            raise ValueError(f"{name}: the page does not give its locality counts")
        out[name] = (int(voted.group(1)), int(acclaimed.group(1)))
    return out


# --- packages -------------------------------------------------------------------------------------------------------


def party(name: str) -> str:
    return key("party", name)


def party_of(nomination: str) -> str | None:
    """The registry's party behind a nomination type; independents and coalitions are not parties."""
    tag = BY_ARABIC[nomination]
    named = AFFILIATIONS[tag][2]
    return party(named) if named else None


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Central Elections Commission as the source of the 2017 Palestinian local elections, the parties "
        " the winners name, and the bodies the contests fill",
        depends=[f"{cec_local.SOURCE}/geo"],
    )
    p.external |= {PALESTINE, cec_local.COUNTRY}
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title="Statistics of the local elections of 2017 published by the Central Elections Commission",
        url=FILES["may-page"],
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    for tag, (arabic, english, _) in AFFILIATIONS.items():
        named = AFFILIATIONS[tag][2]
        if named is None:
            continue
        id = party(named)
        p.add("party", id=id, kind="party", polity_id=PALESTINE)
        p.name(id, arabic, "ar", "official")
        p.name(id, english, "en", "english", False)
        p.add("external_id", entity_id=id, scheme="ps.cec_local17.party", value=named)
    for arabic, english_name in ENGLISH.items():
        id = key("body", "district", arabic)
        p.add("body", id=id, polity_id=PALESTINE, kind="council")
        p.name(id, f"المجالس المحلية في محافظة {arabic}", "ar", "official")
        p.name(id, f"Local councils of {english_name} governorate", "en", "english", False)
    p.add("body", id=WEST_BANK_BODY, polity_id=PALESTINE, kind="council")
    p.name(WEST_BANK_BODY, "المجالس المحلية في الضفة الغربية", "ar", "official")
    p.name(WEST_BANK_BODY, "Local councils of the West Bank", "en", "english", False)
    return p


def _election(p: Package, day: date, name_en: str, name_ar: str) -> str:
    election = key("election", day)
    for url in FILES.values():
        p.uses(raw.cached(SOURCE, url))
    p.add("election", id=election, polity_id=PALESTINE, kind="local", date_from=day, date_to=day, status="held")
    p.name(election, name_en, "en", "official")
    p.name(election, name_ar, "ar", "native", False)
    p.add("slug", path=f"ps/{day}-local", entity_id=election, is_current=True)
    return election


def _contest(
    p: Package,
    election: str,
    tag: str,
    body: str,
    area: str | None,
    magnitude: int,
    names: tuple[str, str],
    slug: str,
    kind: str,
    method: str,
) -> tuple[str, str]:
    contest = key("contest", tag)
    result_set = key("result_set", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=body,
        district_area_id=area,
        magnitude=magnitude,
        round_number=1,
        default_result_kind=kind,
    )
    p.name(contest, names[0], "en", "official")
    p.name(contest, names[1], "ar", "official", False)
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind=kind,
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return contest, result_set


def _seats(p: Package, contest: str, result_set: str, tag: str, counts: dict[str, int]) -> None:
    for position, (affiliation, seats) in enumerate(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])), 1):
        _, english, named = AFFILIATIONS[affiliation]
        option = key("option", tag, affiliation)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="independents" if affiliation == "independent" else "list",
            party_id=party(named) if named else None,
            ballot_position=position,
            ballot_label=english,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)


def may() -> Package:
    p = Package(
        f"{SOURCE}/{MAY}",
        SOURCE,
        "The Palestinian local elections of 13 May 2017: the winners of the West Bank's local councils by "
        "governorate and political affiliation, from the Central Elections Commission's statistics",
        depends=[f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PALESTINE, cec_local.COUNTRY}
    election = _election(p, MAY, "2017 Palestinian local elections", "الانتخابات المحلية 2017")
    districts = district_winners()
    voted = affiliations("may-affiliation", "Political Affiliation", "Political affiliation")
    acclaimed = affiliations("may-acclaimed", "Political Affiliation", "Political Affiliation")
    _verify_may(districts, voted, acclaimed)
    for arabic, counts in districts.items():
        slug = f"ps/{MAY}-local/{changeset.slug(ENGLISH[arabic])}"
        contest, result_set = _contest(
            p,
            election,
            f"may:{arabic}",
            key("body", "district", arabic),
            cec_local.governorate(arabic),
            sum(counts.values()),
            (f"{ENGLISH[arabic]} governorate local councils, 2017", f"المجالس المحلية في محافظة {arabic}، 2017"),
            slug,
            "counted",
            METHOD_MAY,
        )
        _seats(p, contest, result_set, f"may:{arabic}", counts)
    p.add(
        "note",
        id=key("note", election, "split"),
        entity_id=election,
        kind="caveat",
        body_md=(
            f"The councils of {voted['total']} winners were elected by vote (145 localities) and those of "
            f"{acclaimed['total']} winners by acclamation (181 localities), 326 localities in all. The Commission "
            "splits the winners by vote and by acclamation by affiliation only for the whole West Bank, so the "
            "governorate contests hold both together: by vote, "
            + ", ".join(f"{AFFILIATIONS[k][1]} {v}" for k, v in voted.items() if k != "total")
            + "; by acclamation, "
            + ", ".join(f"{AFFILIATIONS[k][1]} {v}" for k, v in acclaimed.items() if k != "total" and v)
            + "."
        ),
        lang="en",
    )
    return p


def _verify_may(districts: dict[str, dict[str, int]], voted: dict[str, int], acclaimed: dict[str, int]) -> None:
    if set(districts) != set(ENGLISH):
        raise ValueError(f"the district table names {set(districts) ^ set(ENGLISH)}")
    by_affiliation: Counter[str] = Counter()
    for counts in districts.values():
        by_affiliation.update(counts)
    expected = {k: voted.get(k, 0) + acclaimed.get(k, 0) for k in AFFILIATIONS}
    expected = {k: v for k, v in expected.items() if v}
    if dict(by_affiliation) != expected:
        raise ValueError(f"the district table's affiliations differ from the national tables: {by_affiliation}")
    if sum(by_affiliation.values()) != voted["total"] + acclaimed["total"] or voted["total"] != 1552:
        raise ValueError("the winners differ from the Commission's totals")
    gender = Counter(genders("may-affiliation"))
    gender.update(genders("may-acclaimed"))
    for arabic, counts in districts.items():
        if gender[ENGLISH[arabic]] != sum(counts.values()):
            raise ValueError(f"{arabic}: {sum(counts.values())} winners against {gender[ENGLISH[arabic]]} by gender")
    pages = locality_counts()
    if pages["may-page"] != (145, 181) or pages["july-page"] != (14, 18):
        raise ValueError(f"the localities differ from the Commission's pages: {pages}")


def july() -> Package:
    p = Package(
        f"{SOURCE}/{JULY}",
        SOURCE,
        "The Palestinian complementary local elections of 29 July 2017: the winners of 32 West Bank local councils "
        "by political affiliation, by vote and by acclamation, from the Central Elections Commission's statistics",
        depends=[f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PALESTINE, cec_local.COUNTRY}
    election = _election(p, JULY, "2017 Palestinian complementary local elections", "الانتخابات المحلية التكميلية 2017")
    voted = affiliations("july-voted", "Political affiliation", "Political Affiliation")
    acclaimed = affiliations("july-acclaimed", "political affiliation", "Political Affiliation")
    if (voted["total"], acclaimed["total"]) != (142, 166):
        raise ValueError(f"the winners differ from the Commission's totals: {voted}, {acclaimed}")
    for name, total in (("july-voted", 142), ("july-acclaimed", 166)):
        if gender_total(name) != total:
            raise ValueError(f"{name}: the gender table does not total {total}")
    for tag, counts, kind, method, names, slug in (
        (
            "voted",
            voted,
            "counted",
            METHOD_VOTED,
            ("West Bank local councils elected by vote, complementary elections 2017", "المجالس المحلية المنتخبة"),
            f"ps/{JULY}-local/voted",
        ),
        (
            "acclaimed",
            acclaimed,
            "tacit",
            METHOD_ACCLAIMED,
            ("West Bank local councils elected by acclamation, complementary elections 2017", "المجالس بالتزكية"),
            f"ps/{JULY}-local/acclaimed",
        ),
    ):
        seats = {k: v for k, v in counts.items() if k != "total" and v}
        contest, result_set = _contest(
            p, election, f"july:{tag}", WEST_BANK_BODY, None, sum(seats.values()), names, slug, kind, method
        )
        _seats(p, contest, result_set, f"july:{tag}", seats)
    return p


# --- the results by council -----------------------------------------------------------------------------------------

COUNCIL_FILES = {
    "may-page": f"{SITE}/tabid/735/language/ar-PS/Default.aspx",
    "july-page": f"{SITE}/tabid/736/language/ar-PS/Default.aspx",
    "may-results": f"{SITE}/Portals/30/pdf/LE2017/LE2017_finalResultSeats.pdf",
    "may-winners": f"{SITE}/Portals/30/pdf/LE2017/LE2017_finalResultWinnerNames.pdf",
    "may-acclaimed": f"{SITE}/Portals/30/pdf/LE2017/LE2017-WinnerListsByAcclamation.pdf",
    "july-results": f"{SITE}/Portals/30/pdf/LE2017-CMP/LE2017_PreResultSeats.pdf",
    "july-winners": f"{SITE}/Portals/30/pdf/LE2017-CMP/LE2017_PreResultWinnerNames.pdf",
    "july-acclaimed": f"{SITE}/Portals/30/pdf/LE2017-CMP/LE2017-WinnerListsByAcclamation.pdf",
    "report": f"{SITE}/Portals/30/pdf/FinalReport/LE2017-FinalReportAr.pdf",
    "authorities": f"{SITE}/Portals/30/excel/LE2017/Authorities%202017-en.xlsx",
    "turnout": f"{SITE}/Portals/30/excel/LE2017/LE2017-FinalTurnoutStatistics.xlsx",
}
# The files each day's package reads, and the family of page layout that reads them.
DAY_FILES = {
    MAY: ("may-page", "may-results", "may-winners", "may-acclaimed", "report", "authorities", "turnout"),
    JULY: ("july-page", "july-results", "july-winners", "july-acclaimed", "authorities"),
}
FAMILY = {"may-results": "slides", "may-winners": "slides", "may-acclaimed": "slides", "report": "slides"}
VOTES, REGISTERED, CAST = 1, 2, 3
THRESHOLD = 8
# The acclaimed table of 13 May spells one governorate wrongly.
DISTRICT_SPELLING = {"رام والله والبيرة": "رام الله والبيرة", "رام الله": "رام الله والبيرة"}
ENGLISH_HEADS = {
    "Jenin": "جنين",
    "Tubas": "طوباس",
    "Tulkarem": "طولكرم",
    "Tulkarm": "طولكرم",
    "Nablus": "نابلس",
    "Qalqilia": "قلقيلية",
    "Salfit": "سلفيت",
    "Ramallah": "رام الله والبيرة",
    "Jerusalem": "القدس",
    "Bethlehem": "بيت لحم",
    "Hebron": "الخليل",
    "Jericho": "أريحا",
}
LIST_METHOD_17 = (
    "Counted by the Central Elections Commission for each local council. The Commission's table prints the council's "
    "registered voters, the ballots cast, and for each list its votes and seats; it prints no valid, invalid, or "
    "blank totals, so none is stored. Seats are stored as declared and the winners are the candidates the Commission "
    "names; the seats of each list agree with the Sainte-Laguë method at the 8 percent threshold."
)
TACIT_METHOD_17 = (
    "No vote was held for this council: the Commission declared the list that nominated elected by acclamation "
    "(tazkiya) and prints the list and its seats, with no count of ballots."
)
SYSTEM_17 = (
    "Local Elections Law no. 10 of 2005 as amended sets proportional representation from closed lists for every "
    "local council: a list's votes are divided by the odd numbers (the Sainte-Laguë method), lists below 8 percent "
    "of the valid votes win no seats, and a list's seats go to its candidates in list order, with the representation "
    "of women and the Muslim-Christian quota taken into account."
)


def council_path(name: str) -> Path:
    return raw.cached(SOURCE, COUNCIL_FILES[name]).path


@dataclass
class Authority:
    day: date
    district: str
    name: str
    seats: int
    voted: bool
    file: str
    voters: Cell | None = None
    cast: Cell | None = None
    rows: list[Row] = field(default_factory=list)
    winners: list[Winner] = field(default_factory=list)
    english: str | None = None
    unheld: bool = False

    @property
    def ident(self) -> str:
        return f"{self.day}:{self.district}:{self.name}"

    @property
    def matching(self) -> tuple[str, str]:
        return councils17.fold(self.district), councils17.fold(self.name)


@cache
def council_pages(name: str) -> list[councils17.Page]:
    return councils17.read_pages(council_path(name), FAMILY.get(name, "ligature"))


@cache
def council_table(name: str) -> list[councils17.Council]:
    return councils17.READERS[name](council_pages(name))


def _cell_text(cell: Cell | None) -> str:
    assert cell is not None
    return cell.text


def english_names() -> tuple[dict[tuple[str, str], str], dict[tuple[str, int, int], str]]:
    """The Commission's English names of the authorities: by district and folded Arabic name (the list of every
    authority), and by district, registered voters, and seats (the authorities that voted)."""
    workbook = fastexcel.read_excel(str(council_path("authorities")))
    by_name: dict[tuple[str, str], str] = {}
    district = ""
    for row in workbook.load_sheet_by_name("المحافظات", header_row=None, dtypes="string").to_polars().rows():
        head = row[0]
        if head and head.endswith("District"):
            district = ENGLISH_HEADS.get(re.split(r"[ &]", head)[0], "")
        elif head and head.isdigit() and row[2] and row[3] and district:
            by_name[(district, councils17.fold(row[2]))] = re.sub(r"\s+", " ", row[3]).strip()
    by_figures: dict[tuple[str, int, int], str] = {}
    for row in workbook.load_sheet_by_name("Districts", header_row=None, dtypes="string").to_polars().rows():
        head = row[0]
        if head and head.endswith("District"):
            district = ENGLISH_HEADS.get(re.split(r"[ &]", head)[0], "")
        elif head and head.isdigit() and row[1] and row[3] and row[4]:
            key_ = (district, int(row[4]), int(row[3]))
            if key_ in by_figures:
                raise ValueError(f"two authorities share {key_}")
            by_figures[key_] = re.sub(r"\s+", " ", row[1]).strip()
    return by_name, by_figures


def turnout_table() -> dict[tuple[str, str], tuple[int, int]]:
    """The Commission's final turnout table: registered voters and ballots cast by district and folded name."""
    out: dict[tuple[str, str], tuple[int, int]] = {}
    sheet = fastexcel.read_excel(str(council_path("turnout")))
    for row in sheet.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars().rows():
        if row[0] != "المجموع" and row[2] and row[2].isdigit() and row[3] and row[3].isdigit():
            out[(councils17.fold(row[0] or ""), councils17.fold(row[1] or ""))] = (int(row[3]), int(row[4] or 0))
    return out


def _district(name: str) -> str:
    name = DISTRICT_SPELLING.get(name, name)
    if name not in cec_local.DISTRICTS:
        raise ValueError(f"an unknown district {name!r}")
    return name


def _match(
    authorities: list[Authority], table: list[councils17.Council], what: str
) -> dict[tuple[str, str], councils17.Council]:
    found = {(councils17.fold(_district(c.district)), councils17.fold(c.authority)): c for c in table}
    wanted = {a.matching for a in authorities}
    if set(found) != wanted:
        raise ValueError(f"{what} names different councils: {sorted(set(found) ^ wanted)}")
    return found


def anata() -> Authority:
    """Anata, whose lists the results slides leave out: its lists, votes, and seats come from the final report's table
    of councils, its winners from the report's annex, and its registered voters and ballots from the turnout table."""
    report = councils17.fix_ligatures(council_path("report"))
    page = report[76]
    words = [(w[0], w[1], w[2], w[3], councils17.clean(w[4])) for w in page.get_text("words")]
    head = next(w for w in words if w[4] == "عناتا" and w[0] > 400)
    rows: list[Row] = []
    for lo, hi in ((160, 180), (180, 197)):
        line = [w for w in words if lo <= (w[1] + w[3]) / 2 < hi and w[0] < 400 and head[1] - 12 < w[1]]
        nums = sorted((w for w in line if re.fullmatch(r"\d+", w[4]) and w[0] < 200), key=lambda w: w[0])
        names = [w for w in line if 240 < w[0] < 340 and not re.fullmatch(r"\d+", w[4])]
        if len(nums) != 2 or not names:
            raise ValueError(f"the report's row of Anata reads {[w[4] for w in line]}")
        rows.append(
            Row(
                councils17.cell(77, names),
                None,
                councils17.cell(77, [nums[1]]),
                councils17.cell(77, [nums[0]]),
            )
        )
    winners: list[Winner] = []
    for number_ in (107, 108):
        for block in report[number_ - 1].get_text("blocks"):
            text = block[4]
            found = re.match(
                r"(\d+)\s*(.+?)\n(العهد والوفاء|عناتا للجميع)\s*\n(فائزة?)", text.replace("\xa0", " "), re.DOTALL
            )
            if found:
                winners.append(
                    Winner(
                        int(found.group(1)),
                        Cell(councils17.clean(found.group(2)), None),
                        Cell(found.group(3), None),
                    )
                )
    figures = turnout_table()[(councils17.fold("القدس"), councils17.fold("عناتا"))]
    return Authority(
        MAY,
        "القدس",
        "عناتا",
        11,
        True,
        "report",
        Cell(str(figures[0]), None),
        Cell(str(figures[1]), None),
        rows,
        winners,
    )


@cache
def authorities_of(day: date) -> list[Authority]:
    by_name, by_figures = english_names()
    out: list[Authority] = []
    key_ = "may" if day == MAY else "july"
    voted = [
        Authority(
            day,
            _district(c.district),
            c.authority,
            councils17.number(_cell_text(c.seats)),
            True,
            f"{key_}-results",
            c.voters,
            c.cast,
            c.rows,
        )
        for c in council_table(f"{key_}-results")
    ]
    for a in voted:
        a.unheld = a.cast is not None and a.cast.text == "0" and all(r.votes and r.votes.text == "0" for r in a.rows)
    named = _match(voted, council_table(f"{key_}-winners"), "the winners table")
    for a in voted:
        a.winners = named[a.matching].winners
        if a.unheld == bool(a.winners):
            raise ValueError(f"{a.ident}: the winners table does not agree on whether the council voted")
    acclaimed = [
        Authority(
            day,
            _district(c.district),
            c.authority,
            councils17.number(_cell_text(c.seats)),
            False,
            f"{key_}-acclaimed",
            None,
            None,
            c.rows,
            c.winners,
        )
        for c in council_table(f"{key_}-acclaimed")
    ]
    out = voted + acclaimed
    if day == MAY:
        out.append(anata())
    for a in out:
        listed = by_name.get((a.district, councils17.fold(a.name)))
        a.english = listed
        if day == MAY and a.voted and a.voters is not None:
            figured = by_figures.get((a.district, councils17.number(a.voters.text), a.seats))
            if figured is None or (listed is not None and councils17.fold(listed) != councils17.fold(figured)):
                raise ValueError(f"{a.ident}: the Commission's English lists name it {figured!r} and {listed!r}")
            a.english = figured
    idents = [a.ident for a in out]
    if len(set(idents)) != len(idents):
        raise ValueError(f"two councils share a name: {[i for i, n in Counter(idents).items() if n > 1]}")
    return out


def party_list(name: str, labels: dict[str, str]) -> str | None:
    """The list of a council a winners table names: spelled the same, with the same words in another order, or nearly
    the same."""
    wanted = councils17.fold(name)
    if wanted in labels:
        return wanted
    words = sorted(re.findall(r"[^\W_]+", name))
    same = [label for label, printed in labels.items() if sorted(re.findall(r"[^\W_]+", printed)) == words]
    if len(same) == 1:
        return same[0]
    ranked = sorted(((SequenceMatcher(None, wanted, label).ratio(), label) for label in labels), reverse=True)
    if ranked and ranked[0][0] >= 0.8 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0] - 0.1):
        return ranked[0][1]
    return None


def _row_key(voted: bool, row: Row | councils17.Nomination) -> tuple[str, str, int]:
    """What a list is told by in both tables: its folded name, its votes ("tazkiya" when acclaimed), and its seats."""
    if isinstance(row, councils17.Nomination):
        votes = row.votes if row.votes == councils17.TAZKIYA else str(councils17.number(row.votes))
        return councils17.fold(row.list_name.text), votes, row.seats
    return (
        councils17.fold(row.list_name.text),
        str(councils17.number(_cell_text(row.votes))) if voted else councils17.TAZKIYA,
        councils17.number(_cell_text(row.seats)),
    )


def nominations(sheet: list[Authority]) -> tuple[dict[tuple[str, int], councils17.Nomination], list[str]]:
    """The nomination type of each list of the 13 May councils, from the final report's table: by the council's
    identifier and the list's position, with the lists the report leaves out or that cannot be paired safely.

    The report's table does not print every council, and prints a council's name over several lines, so a list is
    paired by its name, votes, and seats. Lists that agree on all three (the acclaimed Fatah lists) are paired by the
    council's name when their nomination types differ, and any type is the same one when they do not."""
    report = councils17.report_nominations(councils17.fix_ligatures(council_path("report")))
    groups: dict[tuple[str, str, int], list[councils17.Nomination]] = {}
    for n in report:
        groups.setdefault(_row_key(False, n), []).append(n)
    rows: dict[tuple[str, str, int], list[tuple[Authority, int]]] = {}
    for a in sheet:
        for position, r in enumerate(a.rows, 1):
            rows.setdefault(_row_key(a.voted, r), []).append((a, position))
    out: dict[tuple[str, int], councils17.Nomination] = {}
    unpaired: list[tuple[Authority, int]] = []
    spare: list[councils17.Nomination] = []
    for k in set(rows) | set(groups):
        mine, theirs = rows.get(k, []), groups.get(k, [])
        if len({n.nomination.text for n in theirs}) <= 1 and theirs and len(mine) <= len(theirs):
            for a, position in mine:
                out[(a.ident, position)] = theirs[0]
            spare += theirs[len(mine) :]
            continue
        unpaired += mine
        spare += theirs
        if theirs and mine:
            scores = sorted(
                (
                    (SequenceMatcher(None, councils17.fold(a.name), councils17.fold(n.council.text)).ratio(), i, j)
                    for i, (a, _) in enumerate(mine)
                    for j, n in enumerate(theirs)
                ),
                reverse=True,
            )
            used_a: set[int] = set()
            used_n: set[int] = set()
            for score, i, j in scores:
                if i in used_a or j in used_n or score < 0.6:
                    continue
                used_a.add(i)
                used_n.add(j)
                out[(mine[i][0].ident, mine[i][1])] = theirs[j]
    left = [(a, position) for a, position in unpaired if (a.ident, position) not in out]
    paired: set[int] = set()
    for a, position in left:
        r = a.rows[position - 1]
        votes, seats = _row_key(a.voted, r)[1:]
        for i, n in enumerate(spare):
            if i not in paired and _row_key(False, n)[1:] == (votes, seats):
                ours, theirs = councils17.fold(r.list_name.text), councils17.fold(n.list_name.text)
                if SequenceMatcher(None, ours, theirs).ratio() >= 0.85 or ours.startswith(theirs):
                    paired.add(i)
                    out[(a.ident, position)] = n
                    break
    missing = sorted(
        f"{a.ident}: {a.rows[position - 1].list_name.text}"
        for a in sheet
        for position in range(1, len(a.rows) + 1)
        if (a.ident, position) not in out
    )
    return out, missing


def sainte_lague(votes: list[int], seats: int) -> list[int]:
    total = sum(votes)
    eligible = [v if v * 100 >= THRESHOLD * total else 0 for v in votes]
    won = [0] * len(votes)
    for _ in range(seats):
        best = max(range(len(votes)), key=lambda i: eligible[i] / (2 * won[i] + 1))
        won[best] += 1
    return won


def verify_authority(a: Authority) -> None:
    if a.unheld:
        return
    listed = sum(councils17.number(_cell_text(r.seats)) for r in a.rows)
    if listed != a.seats:
        raise ValueError(f"{a.ident}: the lists hold {listed} seats of {a.seats}")
    if a.voted:
        votes = [councils17.number(_cell_text(r.votes)) for r in a.rows]
        if sainte_lague(votes, a.seats) != [councils17.number(_cell_text(r.seats)) for r in a.rows]:
            raise ValueError(f"{a.ident}: the lists' seats differ from the Sainte-Laguë allocation of their votes")
        if sum(votes) > councils17.number(_cell_text(a.cast)):
            raise ValueError(f"{a.ident}: more votes than ballots cast")
        if councils17.number(_cell_text(a.cast)) > councils17.number(_cell_text(a.voters)):
            raise ValueError(f"{a.ident}: more ballots than registered voters")
    if a.winners:
        labels = {councils17.fold(r.list_name.text): r.list_name.text for r in a.rows}
        won: Counter[str | None] = Counter(
            party_list(w.list_name.text, labels) if w.list_name else next(iter(labels)) for w in a.winners
        )
        for r in a.rows:
            if won[councils17.fold(r.list_name.text)] != councils17.number(_cell_text(r.seats)):
                raise ValueError(
                    f"{a.ident}: list {r.list_name.text!r} has {won[councils17.fold(r.list_name.text)]} winners"
                )
        if None in won:
            raise ValueError(f"{a.ident}: a winner on an unknown list")


def render_pages() -> None:
    """Draws the pages the values cite to `data/pages/...`, the images the package's page rows name."""
    for name in ("may-results", "july-results", "report"):
        artifact = raw.cached(SOURCE, COUNCIL_FILES[name])
        document = pymupdf.open(artifact.path)
        for number_ in sorted(_cited_pages(name)):
            image = Path(raw.DATA) / f"pages/{SOURCE}/{artifact.path.stem}/{number_}.png"
            if not image.exists():
                image.parent.mkdir(parents=True, exist_ok=True)
                page = document[number_ - 1]
                zoom = 1.0 if page.rect.width > 1000 else 2.0
                page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(image)


def _cited_pages(name: str) -> set[int]:
    if name == "report":
        return {77}
    return {n for n in range(1, len(council_pages(name)) + 1)}


def _unit(p: Package, a: Authority) -> str:
    unit = p.id("unit", a.ident)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PALESTINE,
        scheme_code="ps.local_authority",
        code=a.ident,
        area_relation="within",
        area_id=cec_local.governorate(a.district),
        kind="standard",
        valid_from=a.day,
        valid_to=a.day + timedelta(days=1),
    )
    p.name(unit, a.name, "ar", "official")
    if a.english:
        p.name(unit, a.english, "en", "english", False)
    return unit


def councils(day: date) -> Package:
    key_ = "may" if day == MAY else "july"
    election_id = key("election", day)
    p = Package(
        f"{SOURCE}/{day}-councils",
        SOURCE,
        f"The Palestinian {'' if day == MAY else 'complementary '}local elections of {day:%-d %B %Y} by local council: "
        "the registered voters, ballots, votes, and seats of each list, and the winners, from the Central Elections "
        "Commission's tables",
        depends=[f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{day}"],
    )
    p.external |= {PALESTINE, cec_local.COUNTRY, election_id}
    source_id = key("source", "councils", day)
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title=f"Results of the {'' if day == MAY else 'complementary '}local elections of {day:%-d %B %Y} by local "
        "council published by the Central Elections Commission",
        url=COUNCIL_FILES[f"{key_}-page"],
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    artifacts = {name: p.artifact(raw.cached(SOURCE, COUNCIL_FILES[name]), source_id) for name in DAY_FILES[day]}
    pages: dict[tuple[str, int], str] = {}
    for name in (f"{key_}-results", "report") if day == MAY else (f"{key_}-results",):
        artifact = raw.cached(SOURCE, COUNCIL_FILES[name])
        document = pymupdf.open(artifact.path)
        for number_ in sorted(_cited_pages(name)):
            rect = document[number_ - 1].rect
            pages[(name, number_)] = p.id("page", name, number_)
            p.add(
                "source_page",
                id=pages[(name, number_)],
                artifact_id=artifacts[name],
                page_number=number_,
                image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number_}.png",
                width=round(rect.width),
                height=round(rect.height),
            )
    sheet = authorities_of(day)
    nominated, unnominated = nominations(sheet) if day == MAY else ({}, [])
    totals: Counter[str] = Counter()
    ordinal: Counter[str] = Counter()
    prefix = f"ps/{day}-local"
    stage = "official" if day == MAY else "preliminary"
    for a in sorted(sheet, key=lambda a: (list(cec_local.DISTRICTS).index(a.district), a.name)):
        verify_authority(a)
        if a.unheld:
            totals["unheld"] += 1
            continue
        ordinal[a.district] += 1
        body = p.id("body", a.ident)
        p.add("body", id=body, polity_id=PALESTINE, kind="council")
        p.name(body, f"الهيئة المحلية {a.name}", "ar", "official")
        contest = p.id("contest", a.ident)
        result_set = p.id("result_set", a.ident)
        kind = "counted" if a.voted else "tacit"
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body,
            magnitude=a.seats,
            round_number=1,
            default_result_kind=kind,
        )
        p.name(contest, f"الهيئة المحلية {a.name}، {day.year}", "ar", "official")
        if a.english:
            p.name(contest, f"{a.english} local council, {day.year}", "en", "english", False)
        p.add(
            "slug",
            path=f"{prefix}/{changeset.slug(cec_local.DISTRICTS[a.district][0])}-{ordinal[a.district]:03d}",
            entity_id=contest,
            is_current=True,
        )
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage=stage,
            kind=kind,
            source_id=source_id,
            status="active",
            is_default=True,
            completeness=1.0 if a.voted else None,
        )
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=(LIST_METHOD_17 if a.voted else TACIT_METHOD_17)
            + (
                " The Commission's report prints the council's lists, votes, and seats and its annex the winners."
                if a.file == "report"
                else ""
            ),
            lang="en",
        )
        unit = _unit(p, a) if a.voted else None
        if unit:
            for measure, cell in ((REGISTERED, a.voters), (CAST, a.cast)):
                totals["registered" if measure == REGISTERED else "cast"] += _put(
                    p, result_set, unit, None, measure, cell, source_id, pages, a.file
                )
        options: dict[str, str] = {}
        labels: dict[str, str] = {}
        for position, row in enumerate(a.rows, 1):
            label = councils17.fold(row.list_name.text)
            if label in options:
                raise ValueError(f"{a.ident}: two lists named {row.list_name.text!r}")
            labels[label] = row.list_name.text
            option = options[label] = p.id("option", a.ident, position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                ballot_position=position,
                ballot_label=row.list_name.text,
                party_id=party_of(nominated[(a.ident, position)].nomination.text)
                if (a.ident, position) in nominated
                else None,
            )
            if (a.ident, position) in nominated:
                n = nominated[(a.ident, position)]
                p.add(
                    "note",
                    id=p.id("note", option),
                    entity_id=option,
                    kind="context",
                    body_md=f"Nomination type: {n.nomination.text}, as the Commission's final report prints it for "
                    f"this list (page {n.page} of the report).",
                    lang="en",
                )
                totals["nominated"] += 1
            seats = councils17.number(_cell_text(row.seats))
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if seats else "lost", seats=seats)
            totals["seats"] += seats
            if unit:
                totals["votes"] += _put(p, result_set, unit, option, VOTES, row.votes, source_id, pages, a.file)
        for w in a.winners:
            label = party_list(w.list_name.text, labels) if w.list_name else next(iter(labels))
            assert label is not None
            tag = f"{a.ident}:{w.position}"
            person = p.id("person", tag)
            p.add("person", id=person)
            p.name(person, w.name.text, "ar", "official")
            option = p.id("option", tag)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=options[label],
                ballot_position=w.position,
                ballot_label=w.name.text,
            )
            candidacy = p.id("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=options[label],
                role="list_member",
                ballot_name=w.name.text,
            )
            p.add("outcome", result_set_id=result_set, option_id=options[label], candidacy_id=candidacy, kind="elected")
        totals["winners"] += len(a.winners)
        totals["voted" if a.voted else "acclaimed"] += 1
    _verify_councils(day, sheet, totals)
    p.add(
        "note",
        id=p.id("note", election_id, "councils"),
        entity_id=election_id,
        kind="caveat",
        body_md=_council_note(day, sheet, totals)
        + (
            f" The final report's table of lists gives a nomination type (Fatah, independent, coalition, or a party) "
            f"for {totals['nominated']} of the {totals['nominated'] + len(unnominated)} lists; it leaves out the "
            f"other {len(unnominated)}, which carry no nomination type here."
            if day == MAY
            else ""
        ),
        lang="en",
    )
    return p


def _put(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    cell: Cell | None,
    source_id: str,
    pages: dict[tuple[str, int], str],
    file: str,
) -> int:
    assert cell is not None
    value = councils17.number(cell.text)
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
    if cell.box is not None:
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=pages[(file, cell.box.page)],
            source_id=source_id,
            x0=cell.box.x0,
            y0=cell.box.y0,
            x1=cell.box.x1,
            y1=cell.box.y1,
        )
    return value


def _verify_councils(day: date, sheet: list[Authority], totals: Counter[str]) -> None:
    if day == JULY:
        if (totals["voted"], totals["acclaimed"], totals["winners"]) != (14, 18, 142 + 166):
            raise ValueError(f"the complementary councils differ from the Commission's counts: {dict(totals)}")
        return
    figures = turnout_table()
    got = Counter()
    for a in sheet:
        if a.voted:
            got["voters"] += councils17.number(_cell_text(a.voters))
    if got["voters"] != sum(v for v, _ in figures.values()):
        raise ValueError(f"the councils' registered voters differ from the turnout table: {got['voters']}")
    if totals["acclaimed"] != 181 or totals["voted"] + totals["unheld"] != 145:
        raise ValueError(f"the councils differ from the Commission's counts: {dict(totals)}")
    by_district: Counter[str] = Counter()
    for a in sheet:
        by_district[ENGLISH[a.district]] += len(a.winners) if a.voted else a.seats
    expected = {ENGLISH[d]: sum(c.values()) for d, c in district_winners().items()}
    if dict(by_district) != expected:
        raise ValueError(
            f"the winners by governorate differ from the Commission's table: {dict(by_district)} against {expected}"
        )


def _council_note(day: date, sheet: list[Authority], totals: Counter[str]) -> str:
    if day == JULY:
        return (
            f"By council, {totals['voted']} councils voted ({sum(len(a.winners) for a in sheet if a.voted)} winners) "
            f"and {totals['acclaimed']} were declared elected by acclamation "
            f"({sum(len(a.winners) for a in sheet if not a.voted)} winners), the Commission's figures for the "
            "complementary elections. The Commission publishes these as preliminary results."
        )
    text = (
        f"By council, {totals['voted']} councils voted and {totals['acclaimed']} were declared elected by "
        f"acclamation, {totals['voted'] + totals['acclaimed']} of the 326 localities the Commission counts. The "
        "winners add up to the Commission's table by governorate. The lists hold "
        f"{totals['votes']:,} votes for {totals['registered']:,} registered voters and {totals['cast']:,} ballots cast."
    )
    figures = turnout_table()
    apart = [
        a for a in sheet if a.voted and not a.unheld and figures[a.matching][1] != councils17.number(_cell_text(a.cast))
    ]
    if apart:
        text += (
            f" The Commission's separate turnout table gives {sum(c for _, c in figures.values()):,} ballots cast, "
            f"{sum(figures[a.matching][1] - councils17.number(_cell_text(a.cast)) for a in apart):,} more than the "
            f"results tables, and differs from them for {len(apart)} councils; the same table's registered voters "
            f"({sum(v for v, _ in figures.values()):,}) agree. The values here are the results tables'."
        )
    unheld = [a for a in sheet if a.unheld]
    for a in unheld:
        text += (
            f" The Commission counts {a.name} among the 145 localities that voted. The table of lists prints {a.name} ({a.district} governorate) with {a.seats} seats and "
            f"{a.voters.text if a.voters else ''} registered voters but no ballots, votes, or winners, so this election "
            "holds no contest for it."
        )
    return text
