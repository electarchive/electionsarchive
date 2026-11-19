"""Moldova's general local elections of 5 November 2023: the 898 mayors the Central Electoral Commission lists as
elected, one contest for each first-level locality and for the municipalities of Chișinău and Bălți. The list names
the winners only, as of 19 May 2024, after the second rounds and repeat votes, so the package holds declared outcomes
and no votes. The report on the elections gives the national totals the list is checked against."""

import re
from collections import Counter, defaultdict
from datetime import date
from functools import cache

from ea import raw
from ea.package import Package
from ea.sources.md import admin, cec, local, newlocal

SOURCE = "md.cec"
MOLDOVA = local.MOLDOVA
DAY = "2023-11-05"
ELECTION_KEY = f"md.cec/{DAY}"
REGISTRY = "md.cec/general-2023-registry"
PAGE = "https://a.cec.md/index.php/ro/lista-primarilor-alesi-15312.html"
MAYORS = (
    "https://a.cec.md/storage/ckfinder/files/ALG_05.11.2023/Rezultate/"
    "C2_Lista%20primarilor%20ale%C8%99i_ALG%202023_plasat%20pe%20sait.xlsx"
)
REPORT = "http://a.cec.md/storage/ckfinder/files/2355_Anexa_Raport%20ALG%205%20nov%202023.pdf"
URLS = (PAGE, MAYORS, REPORT)
TOTAL = 898
MUNICIPAL = {"chisinau", "balti"}
MARKS = {"sat": "sat", "oraș": "oras", "comuna": "comuna"}
LABELS = {"oras": "town", "sat": "village", "comuna": "commune"}
# Cyrillic letters that the annexes print in place of their Latin twins, as in "Pеlinia".
HOMOGLYPHS = str.maketrans("еаосрхуі", "eaocpxyi")
METHOD = (
    "The Central Electoral Commission's list of elected mayors names each winner and the subject that nominated "
    "them, as of 19 May 2024. It gives no votes, no turnout, and no losing candidates, and it does not say in which "
    "round a mayor won. A mayor elected in a second round (19 November, 3 December, or 17 December 2023) or in a "
    "repeat vote (up to 11 February 2024) is listed in the same way as one elected on 5 November 2023, so this "
    "result set holds the declared winner alone."
)
SUFFIX = re.compile(r"^(.*?)\s*\((sat|oraș|comuna)\)$", re.IGNORECASE)


def key(kind: str, *parts: object) -> str:
    return cec.key(f"general2023.{kind}", *parts)


def norm(text: object) -> str:
    return local.fold(local.fix(text).translate(HOMOGLYPHS))


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    raw.fetch(SOURCE, MAYORS, suffix=".xlsx")
    raw.fetch(SOURCE, REPORT, suffix=".pdf")


class Winner:
    """One mayor as the list prints them, with the unit the mayor governs."""

    def __init__(self, row: list[str | None]) -> None:
        self.district = local.district_key(row[1])
        self.printed = local.fix(row[2])
        self.name = f"{local.title(row[3])} {local.title(row[4])}"
        self.sex = local.clean(row[5])
        subject = local.fix(row[6])
        self.party = None if subject.lower().startswith("candidat independent") else subject
        found = SUFFIX.match(self.printed)
        self.mark = MARKS[found.group(2).lower()] if found else None
        self.place = found.group(1) if found else self.printed
        self.municipal = self.district in MUNICIPAL and norm(self.place) == self.district


@cache
def winners() -> list[Winner]:
    rows = local.sheet(MAYORS)
    found = [Winner(row) for row in rows if row[0] and row[0].strip().isdigit()]
    printed = [cell.strip() for row in rows for cell in row if cell and "TOTAL" in cell]
    if len(found) != TOTAL or printed != [f"TOTAL: {TOTAL}"]:
        raise ValueError(f"{len(found)} mayors in the list, which prints {printed}, not {TOTAL}")
    return found


@cache
def units() -> dict[Winner | str, admin.Unit]:
    """The first-level unit of each winner, by winner."""
    _, annex = admin.annexes()
    by_name: dict[tuple[str, str], list[admin.Unit]] = defaultdict(list)
    for unit in annex:
        by_name[(unit.district, norm(unit.name))].append(unit)
    listed: dict[tuple[str, str], set[str]] = defaultdict(set)
    for w in winners():
        if w.mark:
            listed[(w.district, norm(w.place))].add(w.mark)
    found: dict[Winner | str, admin.Unit] = {}
    for w in winners():
        if w.municipal:
            continue
        options = by_name.get((w.district, norm(w.place)), [])
        if len(options) > 1:
            kinds = {u.kind for u in options} - (listed[(w.district, norm(w.place))] if not w.mark else set())
            options = [u for u in options if u.kind == (w.mark or next(iter(kinds)))]
        if len(options) != 1:
            raise ValueError(f"{w.printed} in {w.district} matches {len(options)} units of the annexes")
        found[w] = options[0]
    if repeated := [k for k, n in Counter(u.key for u in found.values()).items() if n > 1]:
        raise ValueError(f"units with two mayors: {repeated}")
    if len(found) != admin.FIRST_LEVEL:
        raise ValueError(f"{len(found)} first-level units have a mayor, not {admin.FIRST_LEVEL}")
    return found


def area_of(w: Winner) -> str:
    if w.municipal:
        return local.district_area(w.district)
    return Package(admin.GEO, SOURCE, "").id("area", "md.localitate", units()[w].key)


def where(w: Winner) -> str:
    if w.municipal:
        return local.districts()[w.district][0]
    unit = units()[w]
    name = local.fix(unit.name)
    twins = [u for u in admin.annexes()[1] if u.district == unit.district and norm(u.name) == norm(unit.name)]
    return f"{name} ({LABELS[unit.kind]})" if len(twins) > 1 else name


def office_of(w: Winner) -> str:
    """The office an earlier list or report already names, else the one this election adds."""
    if w.municipal:
        base = local.mayor_office(w.district, None)
    elif (w.district, norm(w.place)) in held_places():
        base = local.mayor_office(w.district, norm(w.place))
    else:
        base = ""
    return base if base in held_offices() else key("office", "mayor", area_of(w))


@cache
def held_offices() -> set[str]:
    return {local.mayor_office(m.district_key, m.locality_key) for m in local.read_mayors_2019()} | {
        newlocal.mayor_office(at) for at in newlocal.places().values()
    }


@cache
def held_places() -> set[tuple[str, str]]:
    twins = {(u.district, norm(u.name)) for u in admin.annexes()[1]}
    ambiguous = {k for k in twins if len([u for u in admin.annexes()[1] if (u.district, norm(u.name)) == k]) > 1}
    return {(w.district, norm(w.place)) for w in winners() if not w.municipal} - ambiguous


def party_label(w: Winner) -> str | None:
    return None if w.party is None else newlocal.party_label(w.party)


@cache
def party_ids() -> dict[str, str]:
    labels = {label for w in winners() if (label := party_label(w))}
    return {label: local.party_ref(label, local.existing_parties()) for label in sorted(labels)}


@cache
def minted() -> set[str]:
    """The ids of the parties an earlier registry already adds."""
    return {*local.parties(), *newlocal.party_ids().values()}


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The mayors' offices and parties in the Central Electoral Commission's list of the mayors elected on "
        "5 November 2023 that its earlier lists do not name",
        depends=[local.GEO, admin.GEO, local.REGISTRY, newlocal.REGISTRY, "md.cec/registry"],
    )
    p.external.add(MOLDOVA)
    for url in URLS:
        p.artifact(raw.cached(SOURCE, url), cec.CEC_SOURCE)
    made: set[str] = set()
    for w in winners():
        office = office_of(w)
        if office in held_offices() or office in made:
            continue
        made.add(office)
        p.add("office", id=office, polity_id=MOLDOVA, kind="mayor")
        p.name(office, f"Mayor of {where(w)}", "en", "official")
        p.name(office, f"Primarul localității {where(w)}", "ro", "native", False)
    for label, id in party_ids().items():
        if id in local.existing_parties() or id in minted() or id in made:
            continue
        made.add(id)
        p.add("party", id=id, kind="bloc" if label.lower().startswith("blocul") else "party", polity_id=MOLDOVA)
        p.name(id, label, "ro", "official")
    return p


def build() -> Package:
    p = Package(
        ELECTION_KEY,
        SOURCE,
        "The mayors elected in the Moldovan general local elections of 5 November 2023, from the Central Electoral "
        "Commission's list of the elected",
        depends=[REGISTRY, local.GEO, admin.GEO, local.REGISTRY, newlocal.REGISTRY, "md.cec/registry"],
    )
    p.external.add(MOLDOVA)
    for url in URLS:
        p.uses(raw.cached(SOURCE, url))
    held = date.fromisoformat(DAY)
    election = key("election", DAY)
    path = f"md/{DAY}-local"
    p.add("election", id=election, polity_id=MOLDOVA, kind="local", date_from=held, date_to=held, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2023 Moldovan general local elections", "en", "official")
    p.name(election, "Alegeri locale generale 2023", "ro", "native", False)
    slugs: set[str] = set()
    for w in winners():
        area = area_of(w)
        name = where(w)
        scheme = "md.raion" if w.municipal else "md.localitate"
        unit_key = f"{w.district}:{'municipality' if w.municipal else units()[w].key}"
        contest = key("contest", DAY, unit_key)
        slug = f"{path}/{w.district.replace(' ', '-')}/{local.slug(name)}/mayor"
        while slug in slugs:
            slug += "-2"
        slugs.add(slug)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office_of(w),
            district_area_id=area,
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Mayor of {name}", "en", "official")
        p.name(contest, f"Primarul localității {name}", "ro", "native", False)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        result_set = key("result_set", DAY, unit_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=cec.CEC_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", DAY, unit_key)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MOLDOVA,
            scheme_code=scheme,
            code=f"{DAY}:{unit_key}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(unit, f"Mayor of {name}", "en", "official")
        label = party_label(w)
        party = party_ids()[label] if label else None
        option = key("option", DAY, unit_key)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", party_id=party, ballot_label=w.name)
        who = key("person", DAY, unit_key)
        p.add("person", id=who)
        p.name(who, w.name, "ro", "official")
        p.add(
            "candidacy",
            id=key("candidacy", DAY, unit_key),
            person_id=who,
            option_id=option,
            role="head",
            ballot_name=w.name,
            member_of_party_id=party,
            is_independent=party is None,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=local.SEATS,
            count_round=0,
            flags=0,
            value=1,
        )
    return p


def packages() -> list[str]:
    return [REGISTRY, ELECTION_KEY]


def parse() -> None:
    registry().write()
    build().write()
