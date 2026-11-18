"""Palestine's local elections of 2017 from the Central Elections Commission (elections.ps).

The Commission publishes the 2017 local elections as spreadsheets of statistics, not as results by council: the
winners of the 13 May 2017 elections by governorate and political affiliation, and the winners of the complementary
elections of 29 July 2017 by political affiliation, each split between councils that voted and councils whose
candidates were declared elected by acclamation (tazkiya). The governorate is therefore the finest unit these
files reach. The 13 May contests are the eleven West Bank governorates, each holding the winners of its councils
(by vote and by acclamation together, the only split by governorate the Commission publishes); the 29 July
contests are the two national tallies, by vote and by acclamation.
"""

import re
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import uuid5

import fastexcel

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ps import cec_local

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
    raw.fetch(cec_local.GEOBOUNDARIES, cec_local.GEO_URL, suffix=".geojson")


def packages() -> list[str]:
    return [f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{MAY}", f"{SOURCE}/{JULY}"]


def parse() -> None:
    cec_local.geo().write()
    registry().write()
    may().write()
    july().write()


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
