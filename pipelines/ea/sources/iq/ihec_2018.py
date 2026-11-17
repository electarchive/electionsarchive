"""Iraq's Council of Representatives election of 12 May 2018, from the Independent High Electoral Commission.

`ea.sources.iq.ihec` builds this package alongside the 2021 election's, on the same governorates and body.

The Commission publishes two PDFs for 2018: the list of the 329 winning candidates, and the winners' votes at each
registration center. Both are printed from Excel, and their text layer holds the Arabic as letters in visual order
with the letters that hang below the line as separate runs; `ea.sources.iq.text` puts them back. The Commission
publishes no vote totals for lists or for the other candidates in these files, so the package holds each winner's
votes at each registration center, the declared winners, and nothing else.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cache

from ea import raw
from ea.package import Package
from ea.sources.iq import text
from ea.sources.iq.ihec import COUNCIL, GOVERNORATE_SCHEME, IRAQ, SOURCE, governorate_area, key

DAY = date(2018, 5, 12)
UPLOADS = "https://ihec.iq/wp-content/uploads"
WINNERS = f"{UPLOADS}/2022/07/نتائج-انتخابات-2018.pdf"
CENTERS = f"{UPLOADS}/2021/02/نتائج-المرشحين-الفائزين-على-مستوى-مركز-تسجيل-دائرة-الاعلام.pdf"
PAGE = "https://ihec.iq/results/"
PATH = f"iq/{DAY}-council-of-representatives"
KEY = f"{SOURCE}/cor-2018"
VOTES = 1

# The governorates by ISO 3166-2 code: Arabic name, English name, and the spellings the PDFs' text layer gives.
GOVERNORATES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "IQ-AN": ("الأنبار", "Al-Anbar", ("األنبار", "االنبار")),
    "IQ-AR": ("أربيل", "Erbil", ("أربيل", "اربيل")),
    "IQ-BA": ("البصرة", "Al-Basrah", ("البضة",)),
    "IQ-BB": ("بابل", "Babil", ("بابل",)),
    "IQ-BG": ("بغداد", "Baghdad", ("بغداد",)),
    "IQ-DA": ("دهوك", "Dohuk", ("دهوك",)),
    "IQ-DI": ("ديالى", "Diyala", ("دياىل",)),
    "IQ-DQ": ("ذي قار", "Dhi Qar", ("ذي قار",)),
    "IQ-KA": ("كربلاء", "Karbala", ("كربالء",)),
    "IQ-KI": ("كركوك", "Kirkuk", ("كركوك",)),
    "IQ-MA": ("ميسان", "Maysan", ("ميسان",)),
    "IQ-MU": ("المثنى", "Al-Muthanna", ("المثثن", "المثنر")),
    "IQ-NA": ("النجف", "An-Najaf", ("النجف",)),
    "IQ-NI": ("نينوى", "Ninawa", ("نينوى",)),
    "IQ-QA": ("القادسية", "Al-Qadisiyah", ("القادسية",)),
    "IQ-SD": ("صلاح الدين", "Salah al-Din", ("صالح الدين",)),
    "IQ-SU": ("السليمانية", "Al-Sulaimaniyah", ("السليمانية",)),
    "IQ-WA": ("واسط", "Wasit", ("واسط",)),
}
SPELLED = {spelling: iso for iso, (_, _, spellings) in GOVERNORATES.items() for spelling in spellings}

# The component seats, by the Commission's seat type: Arabic label, English label.
QUOTA = {
    "مسيحي": ("Christian", "المسيحيين"),
    "ايزيدي": ("Yazidi", "الإيزيديين"),
    "شبكي": ("Shabak", "الشبك"),
    "صابثي": ("Sabaean-Mandaean", "الصابئة المندائيين"),
    "فيلي": ("Faili Kurd", "الكرد الفيليين"),
}
# Lists numbered for one candidate, who stands alone: the Shabak, Sabaean-Mandaean, and Faili Kurd seats.
INDIVIDUALS = {"103", "116", "133"}

# The list names as the Commission prints them, where the text layer displaces letters.
LISTS = {
    "103": "قصي عباس (شبكي - مستقل)",
    "104": "الجماعة الإسلامية الكوردستانية / العراق",
    "106": "التحالف المدني الديمقراطي",
    "109": "تحالف الفتح",
    "113": "المجلس الشعبي الكلداني السرياني الآشوري",
    "114": "حزب التقدم الإيزيدي",
    "116": "نوفل الناصري",
    "118": "تيار الحكمة الوطني",
    "120": "حراك الجيل الجديد",
    "123": "جبهة تركمان كركوك",
    "128": "التحالف العربي في كركوك",
    "133": "مازن عبدالمنعم (أبو حيدر)",
    "134": "ائتلاف كفاءات للتغيير",
    "135": "نينوى هويتنا",
    "136": "تمدن",
    "139": "ائتلاف الكلدان",
    "142": "كوران (التغيير)",
    "144": "ائتلاف الرافدين",
    "145": "بيارق الخير",
    "146": "تجمع رجال العراق",
    "149": "تحالف بغداد",
    "152": "ائتلاف دولة القانون",
    "153": "التحالف من أجل الديمقراطية والعدالة",
    "156": "سائرون",
    "158": "ائتلاف النصر",
    "160": "النهج الديمقراطي",
    "162": "الاتحاد الوطني الكوردستاني",
    "163": "تحالف صلاح الدين هويتنا",
    "165": "الأنبار هويتنا",
    "166": "حركة بابليون",
    "168": "تحالف القرار العراقي",
    "172": "حزب الجماهير الوطنية",
    "175": "الاتحاد الإسلامي الكوردستاني",
    "178": "الحزب المدني",
    "180": "عابرون",
    "184": "الحزب الديمقراطي الكوردستاني",
    "185": "ائتلاف الوطنية",
    "187": "ائتلاف قلعة الجماهير الوطنية",
    "188": "حركة إرادة",
}
# Names the text layer spells with a displaced letter, as the same name reads in full elsewhere in the PDFs.
TOKENS = {
    "خضن": "خضر",
    "مصطفن": "مصطفى",
    "مثثن": "مثنى",
    "يحث": "يحيى",
    "لبثن": "لبنى",
    "نارص": "ناصر",
    "رسحان": "سرحان",
    "رسوة": "سروة",
    "رسكوت": "سركوت",
    "عبداالمب": "عبدالأمير",
    "عيىس": "عيسى",
    "موىس": "موسى",
}

METHOD = (
    "The declared winners' votes at each registration center, from the Commission's table of candidate votes by "
    "registration center. The Commission publishes no list totals, no governorate totals, and no ballots cast, valid, "
    "or invalid for 2018, so none are stored. The table also holds rows for candidates who did not win; they are "
    "left out because the table does not say which contest they stood in. It does not say whether it holds every "
    "center at which a winner received votes, so a winner's votes summed over centers need not equal the winner's "
    "total. Candidate and list names come from the PDFs' text layer, which stores some Arabic letters displaced or in "
    "reverse order; a short list of recurring names is corrected, and others may still carry a displaced letter."
)


IHEC = key("source", "ihec-2018")
GENERAL_SYSTEM = key("electoral_system", "open-list-modified-sainte-lague")
QUOTA_SYSTEM = key("electoral_system", "component-seat")


def fix(name: str) -> str:
    words = []
    for word in name.replace("هللا", "الله").split():
        word = TOKENS.get(word, word)
        if len(word) >= 4 and re.fullmatch(r"[ء-ي]+ى[ء-ي]", word):
            word = word[:-2] + word[-1] + "ى"
        words.append(word)
    return " ".join(words)


RASM = str.maketrans(
    {
        **dict.fromkeys("تثنب", "ب"),
        **dict.fromkeys("جخ", "ح"),
        "ذ": "د",
        "ز": "ر",
        "ش": "س",
        "ض": "ص",
        "ظ": "ط",
        "غ": "ع",
        "ق": "ف",
        **dict.fromkeys("أإآ", "ا"),
        "ة": "ه",
        "ى": "ي",
    }
)


def skeleton(name: str) -> str:
    """A name's letter shapes without dots, and without the letters the text layer drops or moves, so the two PDFs'
    readings of one name compare equal."""
    return re.sub(r"[\s\u064b-\u065fبير]", "", name.replace("هللا", "الله").translate(RASM))


@dataclass(frozen=True)
class Winner:
    governorate: str
    seat: str
    name: str
    gender: str


@cache
def winners() -> list[Winner]:
    found: list[Winner] = []
    for _, words in text.pages(raw.cached(SOURCE, WINNERS).path):
        anchors = sorted({w.y for w in words if w.x0 > 520 and w.text.isdigit()})
        if not anchors:
            continue
        rows: dict[float, list[text.Word]] = {y: [] for y in anchors}
        for word in words:
            if word.y >= anchors[0] - 8:
                rows[min(anchors, key=lambda y: abs(y - word.y))].append(word)
        for y in anchors:
            cells: dict[str, list[text.Word]] = defaultdict(list)
            for word in rows[y]:
                cell = (
                    "number"
                    if word.x0 > 520
                    else "seat"
                    if word.x0 >= 470
                    else "name"
                    if word.x0 >= 170
                    else "gender"
                    if word.x0 >= 140
                    else "governorate"
                )
                cells[cell].append(word)
            governorate = text.phrase(cells["governorate"])
            found.append(
                Winner(
                    SPELLED[governorate],
                    text.phrase(cells["seat"]),
                    text.phrase(cells["name"]),
                    "female" if text.phrase(cells["gender"]) == "انثى" else "male",
                )
            )
    return found


@dataclass(frozen=True)
class Row:
    page: int
    governorate: str
    center: str
    center_name: str
    list: str
    candidate: str
    candidate_name: str
    votes: int


@cache
def rows() -> list[Row]:
    return [
        Row(
            int(r["page"]),
            SPELLED[str(r["governorate"])],
            str(r["center_number"]),
            str(r["center"]),
            str(r["list_number"]),
            str(r["candidate_number"]),
            str(r["candidate"]),
            int(r["votes"]),
        )
        for r in text.table(raw.cached(SOURCE, CENTERS).path)
    ]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


@dataclass(frozen=True)
class Seat:
    governorate: str
    list: str
    candidate: str
    name: str
    kind: str
    gender: str


@cache
def seats() -> list[Seat]:
    """Each winner with the list and candidate number the vote table gives the same name.

    The table lists a candidate at every center that counted a vote for them, in any governorate, so a winner is
    found by name alone.
    """
    names: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for row in rows():
        names[skeleton(row.candidate_name)][(row.list, row.candidate)] += 1
    found: list[Seat] = []
    for winner in winners():
        hits = names.get(skeleton(winner.name))
        if not hits:
            raise ValueError(f"{winner} has no row in the vote table")
        if len(hits) > 1:
            raise ValueError(f"{winner} matches {len(hits)} candidates in the vote table")
        list_number, candidate = next(iter(hits))
        found.append(Seat(winner.governorate, list_number, candidate, winner.name, winner.seat, winner.gender))
    if len({skeleton(seat.name) for seat in found}) != len(found):
        raise ValueError("two winners share a name")
    return found


def fetch() -> None:
    for url in (WINNERS, CENTERS):
        raw.fetch(SOURCE, url, suffix=".pdf")


area = governorate_area


def party(number: str) -> str:
    return key("party", number)


def registry(p: Package) -> None:
    """The source, the electoral systems of 2018, and the lists with winners in 2018."""
    p.add(
        "source",
        id=IHEC,
        kind="official_body",
        publisher="Independent High Electoral Commission of Iraq",
        title="Results of the 2018 Council of Representatives election",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Independent High Electoral Commission of Iraq",
    )
    for url in (WINNERS, CENTERS):
        p.artifact(raw.cached(SOURCE, url), IHEC)
    systems = (
        (
            GENERAL_SYSTEM,
            {
                "formula": "modified_sainte_lague",
                "firstDivisor": 1.7,
                "lists": {"order": "open", "vacancy": "next_by_votes"},
            },
            (
                "Seats in each governorate are shared among lists by the modified Sainte-Laguë method with a first "
                "divisor of 1.7, and each list's seats go to its candidates in order of their votes (Council of "
                "Representatives Elections Law no. 45 of 2013, as amended in 2018). Women hold at least a quarter "
                "of the seats."
            ),
        ),
        (
            QUOTA_SYSTEM,
            {"formula": "fptp"},
            (
                "Nine seats are reserved for components: five for Christians, and one each for Yazidis, Shabaks, "
                "Sabaean-Mandaeans, and Faili Kurds. The candidate with the most votes in the governorate wins."
            ),
        ),
    )
    for id, rules, body in systems:
        p.add("electoral_system", id=id, polity_id=IRAQ, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    for number in sorted({seat.list for seat in seats() if seat.list not in INDIVIDUALS}, key=int):
        p.add("party", id=party(number), kind="list_vehicle", polity_id=IRAQ)
        p.name(party(number), LISTS[number], "ar", "official")


def election() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "Iraq's Council of Representatives election of 12 May 2018: the 329 declared winners and their votes at each "
        "registration center",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(IRAQ)
    registry(p)
    entity = key("election", DAY)
    p.add("election", id=entity, polity_id=IRAQ, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(entity, "2018 Iraqi Council of Representatives election", "en", "official")
    p.name(entity, "انتخابات مجلس النواب العراقي 2018", "ar", "official")
    p.add("slug", path=PATH, entity_id=entity, is_current=True)
    by_row: defaultdict[str, list[Row]] = defaultdict(list)
    for row in rows():
        by_row[skeleton(row.candidate_name)].append(row)
    contests: dict[tuple[str, str], list[Seat]] = defaultdict(list)
    for seat in seats():
        contests[(seat.governorate, seat.kind if seat.kind in QUOTA else "general")].append(seat)
    centers: dict[str, Row] = {row.center: row for row in rows()}
    for number, row in sorted(centers.items(), key=lambda t: int(t[0])):
        unit = key("unit", DAY.year, number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRAQ,
            scheme_code=GOVERNORATE_SCHEME,
            code=f"{DAY}:{number}",
            area_relation="within",
            area_id=area(row.governorate),
            kind="standard",
        )
        p.name(unit, fix(row.center_name), "ar", "official")
    for (governorate, seat_kind), members in sorted(contests.items()):
        _contest(p, entity, governorate, seat_kind, members, by_row)
    return p


def _contest(
    p: Package,
    entity: str,
    governorate: str,
    seat_kind: str,
    members: list[Seat],
    by_row: defaultdict[str, list[Row]],
) -> None:
    arabic, english, _ = GOVERNORATES[governorate]
    code = f"{governorate}:{seat_kind}"
    quota = seat_kind in QUOTA
    label = f"{QUOTA[seat_kind][0]} seat, " if quota else ""
    contest = key("contest", code)
    p.add(
        "contest",
        id=contest,
        election_id=entity,
        kind="body_seats",
        body_id=COUNCIL,
        district_area_id=area(governorate),
        magnitude=len(members),
        round_number=1,
        electoral_system_id=QUOTA_SYSTEM if quota else GENERAL_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"2018 {label}{english}", "en", "official")
    p.name(contest, f"{arabic} (مقاعد {QUOTA[seat_kind][1]})" if quota else arabic, "ar", "official")
    path = f"{PATH}/{slug(english)}" + (f"-{slug(QUOTA[seat_kind][0])}" if quota else "")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", code)
    p.add(
        "result_set",
        id=result_set,
        election_id=entity,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=IHEC,
        status="active",
        is_default=True,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD,
        lang="en",
    )
    for seat in sorted(members, key=lambda s: (int(s.list), int(s.candidate))):
        slot = f"{governorate}:{seat.list}:{seat.candidate}"
        individual = seat.list in INDIVIDUALS
        option, person = key("option", slot), key("person", slot)
        name = fix(seat.name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if individual else party(seat.list),
            ballot_position=int(seat.candidate),
            ballot_label=name,
        )
        p.add("person", id=person, gender=seat.gender)
        p.name(person, name, "ar", "official")
        p.add(
            "candidacy",
            id=key("candidacy", slot),
            person_id=person,
            option_id=option,
            role="list_member" if not individual else "head",
            list_position=int(seat.candidate),
            ballot_name=name,
            member_of_party_id=None if individual else party(seat.list),
            is_independent=True if individual else None,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for row in by_row[skeleton(seat.name)]:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=key("unit", DAY.year, row.center),
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=row.votes,
            )
