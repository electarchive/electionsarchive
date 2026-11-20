"""Côte d'Ivoire: municipal council elections of 2013, 2018, and 2023 from the Commission Électorale Indépendante.

The commission publishes each election as a PDF table with one block per commune: the commune's registration and
turnout figures on the left and its competing lists (2013) or candidates heading lists (2018, 2023) on the right.
The blocks are laid out by position, so the parser reads word coordinates from the text layer, rebuilds each row, and
assigns candidate rows to communes by walking both in table order until each commune's candidate votes equal its own
printed valid votes (the same method as the regional council tables, `ci.cei.regional`).

Each poll is one package, `ci.cei/municipal-<date>`, with one contest per commune. The 2018 table also holds the
communes that voted on 16 December 2018, and a separate table holds the partial polls of 2 December 2023.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.ci.cei import SOURCE, key
from ea.sources.ci.regional import (
    ENTITIES,
    HEADER,
    PERCENT,
    WORD,
    Candidate,
    Layout,
    Word,
    fix,
    fold,
    integers,
    lines,
)

COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fCI"))
UPLOADS = "https://www.cei.ci/wp-content/uploads"
FILES = {
    "2013": f"{UPLOADS}/2025/09/RESULTAT_MUNICIPALE-2013-FINAL.pdf",
    "2018": f"{UPLOADS}/2020/04/2-ELECTION-DES-CONSEILLERS-MUNICIPAUX.pdf",
    "2023-september": f"{UPLOADS}/2023/09/Municipales_2023.pdf",
    "2023-december": f"{UPLOADS}/2023/12/RESULTATS-EM-PARTIELS-2023_VF.pdf",
}


@dataclass(frozen=True)
class Shape(Layout):
    commune: float = 0  # words left of this point name a region, not a commune
    minimum: int = 0  # fewest printed figures that make a commune row


LAYOUTS = {
    "2013": Shape(700, 348, 490, 600, 668, header=100, blank_in_valid=False, minimum=6),
    "2018": Shape(1000, 715, 802, 729, 1030, header=190, minimum=8, commune=100),
    "2023": Shape(1000, 710, 832, 759, 1035, header=212, dossier=True, minimum=10, commune=50),
}
LAYOUT_OF = {"2013": "2013", "2018": "2018", "2023-september": "2023", "2023-december": "2023"}


@dataclass
class Row:
    code: str
    name: str
    numbers: list[int]
    day: date | None = None
    y: float = 0.0
    page: int = 0
    parts: list[tuple[float, str]] = field(default_factory=list)  # name pieces that wrap above or below the row


@dataclass
class Commune:
    code: str
    name: str
    figures: dict[str, int]
    candidates: list[Candidate]
    day: date | None = None
    y: float = 0.0


@functools.cache
def words(name: str) -> tuple[Word, ...]:
    path = raw.cached(SOURCE, FILES[name]).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found: list[Word] = []
    page = 0
    for line in out.splitlines():
        if "<page " in line:
            page += 1
        match = WORD.search(line)
        if match:
            text = match[5]
            for entity, char in ENTITIES.items():
                text = text.replace(entity, char)
            found.append(Word(page, float(match[1]), float(match[2]), float(match[3]), text))
    return tuple(found)


def figures(layout_key: str, numbers: list[int]) -> dict[str, int]:
    """Name the printed figures of a commune row, whose columns differ by table (percentages are already dropped)."""
    if layout_key == "2013":
        bureaus, registered, cast, invalid, valid = numbers
        return {"stations": bureaus, "registered": registered, "cast": cast, "invalid": invalid, "valid": valid}
    if layout_key == "2018":
        bureaus, registered, _, _, cast, invalid, blank, valid = numbers
    else:
        bureaus, _, _, registered, _, _, cast, invalid, valid, blank = numbers
    return {
        "stations": bureaus,
        "registered": registered,
        "cast": cast,
        "invalid": invalid,
        "blank": blank,
        "valid": valid,
    }


NAMES = {
    "TIORONIARADOUG OU": "TIORONIARADOUGOU",
    "NIAKARAMADOUGO U": "NIAKARAMADOUGOU",
    "OUANGOLODOUGO U": "OUANGOLODOUGOU",
    "M'BENGUE": "MBENGUE",
}  # a name that wraps mid-word in the 2013 table, and the one spelling that differs between tables
REACH = 40  # the most lists or candidates one commune can have
DAYS = ((re.compile(r"16 Dec 2018", re.IGNORECASE), date(2018, 12, 16)),)


def rows(name: str, layout_key: str) -> tuple[list[Row], dict[int, float]]:
    layout = LAYOUTS[layout_key]
    items = words(name)
    top: dict[int, float] = {}
    for word in items:
        if word.text in HEADER and word.y0 < layout.header:
            top[word.page] = max(top.get(word.page, 0), word.y0 + 5)
    left = [w for w in items if w.x0 < layout.right - 3 and w.y0 > top.get(w.page, 0)]
    found: list[Row] = []
    labels: list[tuple[int, float, str]] = []
    for row in lines(left):
        row = [w for w in row if w.x0 >= layout.commune]
        if not row:
            continue
        tokens = ["0" if t == "-" and layout_key != "2013" else t for t in integers(row)]
        numbers = [int(t) for t in tokens if t.isdigit()]
        text = [t for t in tokens if not t.isdigit() and not PERCENT.match(t)]
        if len(numbers) < layout.minimum:
            if any(PERCENT.match(t) for t in tokens):
                continue  # a commune that did not vote that day: the table prints percentages and no figures
            if tokens and len(numbers) < 3:
                labels.append((row[0].page, row[0].y0, fix(" ".join(tokens))))
            continue
        code = ""
        if layout_key == "2013":
            if not tokens[0].isdigit() or len(tokens[0]) != 3:
                continue
            code, numbers = tokens[0], numbers[1:]
        found.append(Row(code, fix(" ".join(text)), numbers, None, row[0].y0, row[0].page))
    for page, y, text in labels:
        near = [(abs(row.y - y), row) for row in found if row.page == page and abs(row.y - y) < 30]
        if not near:
            continue
        row = min(near, key=lambda t: t[0])[1]
        day = next((d for pattern, d in DAYS if pattern.search(text)), None)
        if day is not None:
            row.day = day
        else:
            row.parts.append((y, text))
    for row in found:
        pieces = sorted([(row.y, row.name), *row.parts]) if row.name else sorted(row.parts)
        row.name = ""
        for _, text in pieces:
            row.name += text if not row.name or row.name.endswith("-") else f" {text}"
        row.name = NAMES.get(row.name, row.name)
    return found, top


def candidates(name: str, layout_key: str, top: dict[int, float]) -> list[Candidate]:
    layout = LAYOUTS[layout_key]
    right = [w for w in words(name) if w.x0 >= layout.right - 3 and w.y0 > top.get(w.page, 0)]
    scored: list[tuple[int, float, list[Word]]] = []
    for line in lines(right, 2.0):
        if any(PERCENT.match(w.text) and w.x0 >= layout.width for w in line):
            scored.append((line[0].page, line[0].y0, line))
    found: list[Candidate] = []
    for page, y, line in scored:
        tokens = integers([w for w in line if w.x0 >= layout.score - 6])
        percent = next(t for t in tokens if PERCENT.match(t))
        earlier = [t for t in tokens[: tokens.index(percent)] if t.isdigit()]
        if not earlier and percent == "0,00%":
            continue  # a list of a commune that did not vote that day: the table prints a percentage and no votes
        score = int(earlier[-1])
        result = " ".join(t for t in tokens[tokens.index(percent) + 1 :])
        end = layout.head - 2 if layout.head > layout.party else layout.score - 6
        party = " ".join(w.text for w in line if layout.party - 6 <= w.x0 < end)
        dossier = " ".join(w.text for w in line if layout.dossier and w.x0 < layout.party - 6)
        found.append(
            Candidate(page, y, "", "", fix(party), score, float(percent[:-1].replace(",", ".")), dossier, result)
        )
        if found[-1].party.endswith("RDACOULIBALY"):  # the table prints a bloc and a surname without a space
            found[-1].party = found[-1].party.removesuffix("COULIBALY")
            found[-1].list_name = "COULIBALY"
    # wrapped names sit above or below the score line: give each word to the nearest score line on its page
    names: dict[int, list[tuple[float, Word]]] = {i: [] for i in range(len(found))}
    lists: dict[int, list[tuple[float, Word]]] = {i: [] for i in range(len(found))}
    listed = layout.head < layout.party  # the 2013 table prints a list's name before its head
    for word in right:
        if (
            word.x0 >= layout.score - 6
            or (listed and word.x0 >= layout.party - 6)
            or (not listed and word.x0 < layout.head - 2)
        ):
            continue
        near = [(abs(c.y - word.y0), i) for i, c in enumerate(found) if c.page == word.page]
        if not near or min(near)[0] > 25:
            continue
        (lists if word.x0 < layout.head - 2 else names)[min(near)[1]].append((word.y0 * 1000 + word.x0, word))
    for i, c in enumerate(found):
        prefix = c.list_name  # a surname the table ran into the party label
        c.name = fix(" ".join([prefix, *(w.text for _, w in sorted(names[i], key=lambda t: (t[1].y0, t[1].x0)))]))
        c.list_name = fix(" ".join(w.text for _, w in sorted(lists[i], key=lambda t: (t[1].y0, t[1].x0))))
    return found


def targets_of(commune: Commune) -> tuple[int, ...]:
    """What a commune's list votes can sum to: the 2018 and 2023 tables print valid votes with or without the blanks."""
    f = commune.figures
    blank = f.get("blank", 0)
    return (f["valid"], f["valid"] - blank, f["cast"] - f["invalid"] - blank, f["cast"] - f["invalid"])


def assign(found: list[Row], cands: list[Candidate], layout_key: str) -> list[Commune]:
    """Split the candidate rows between the communes, in table order, so that each commune's votes equal its own."""
    communes = [Commune(row.code, row.name, figures(layout_key, row.numbers), [], row.day, row.y) for row in found]
    targets = [targets_of(c) for c in communes]
    count, total = len(communes), len(cands)
    infinity = float("inf")
    scores = [0]
    for c in cands:
        scores.append(scores[-1] + c.score)
    # best[i][j]: the cheapest way to give the first j candidates to the first i communes
    best = [[infinity] * (total + 1) for _ in range(count + 1)]
    back = [[0] * (total + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for i in range(1, count + 1):
        y = communes[i - 1].y
        for j in range(total + 1):
            near = 0.0
            for k in range(j, max(j - REACH, 0) - 1, -1):
                if k < j:
                    near += abs(cands[k].y - y) / 20
                if best[i - 1][k] == infinity:
                    continue
                cost = best[i - 1][k] + 5 * min(abs(scores[j] - scores[k] - t) for t in targets[i - 1]) + near
                if cost < best[i][j]:
                    best[i][j], back[i][j] = cost, k
    j = total
    for i in range(count, 0, -1):
        k = back[i][j]
        communes[i - 1].candidates = cands[k:j]
        j = k
    return communes


@functools.cache
def parse_table(name: str) -> list[Commune]:
    layout_key = LAYOUT_OF[name]
    found, top = rows(name, layout_key)
    seen: set[str] = set()
    unique: list[Row] = []
    for row in found:
        label = row.code or fold(row.name)
        if label not in seen:  # a row the table repeats at the head of later pages
            seen.add(label)
            unique.append(row)
    return assign(unique, candidates(name, layout_key, top), layout_key)


def printed_total(name: str) -> dict[str, int]:
    """The national row of a table, as printed."""
    layout_key = LAYOUT_OF[name]
    layout = LAYOUTS[layout_key]
    first = [w for w in words(name) if w.x0 < layout.right - 3]
    for row in lines(first):
        tokens = integers(row)
        if tokens[0].upper().startswith("TOTAL") or (layout_key != "2013" and tokens[0].isdigit() and row[0].page == 1):
            numbers = [int(t) for t in tokens if t.isdigit()]
            if len(numbers) >= layout.minimum - (layout_key == "2013"):
                return figures(layout_key, numbers[-(layout.minimum - (layout_key == "2013")) :])
    raise ValueError(f"{name}: no national row")


def listed(commune: Commune, layout_key: str) -> int:
    """The valid votes a commune's lists share: its tables print them with or without the blank ballots."""
    f = commune.figures
    votes = sum(c.score for c in commune.candidates)
    blank = f.get("blank", 0)
    if votes in (f["valid"], f["valid"] - blank) or f["cast"] == f["invalid"] + blank + votes:
        return votes
    return f["valid"] - (blank if LAYOUTS[layout_key].blank_in_valid else 0)


@dataclass(frozen=True)
class Plan:
    day: date
    file: str
    kind: str  # local or by_election
    rerun_of: date | None = None


PLANS = [
    Plan(date(2013, 4, 21), "2013", "local"),
    Plan(date(2018, 10, 13), "2018", "local"),
    Plan(date(2018, 12, 16), "2018", "by_election"),
    Plan(date(2023, 9, 2), "2023-september", "local"),
    Plan(date(2023, 12, 2), "2023-december", "by_election", rerun_of=date(2023, 9, 2)),
]
TITLES = {
    "2013": "Election des conseillers municipaux, scrutin du 21 avril 2013: résultats des scrutins (25 avril 2013)",
    "2018": "Election des conseillers municipaux, scrutins du 13 octobre et du 16 décembre 2018",
    "2023-september": "Election des conseillers municipaux, scrutin du 2 septembre 2023: résultats du scrutin",
    "2023-december": "Election des conseillers municipaux, scrutin partiel du 2 décembre 2023: résultats du scrutin",
}
BODY = key("body", "municipal-council")
REGISTRY = "ci.cei/municipal-registry"
GEO = "ci.cei/commune-geo"
SCHEME = "ci.commune"
ALIASES = {"PPACI": "PPA-CI", "PPA CI": "PPA-CI", "PPA.CI": "PPA-CI", "PDCI": "PDCI-RDA", "PDCI-RDA-RDA": "PDCI-RDA"}
UNAFFILIATED = {"INDEPENDANT", ""}
ACCENTS = {
    "ADJAME": "Adjamé", "ATTECOUBE": "Attécoubé", "BOUAKE": "Bouaké", "BOUAFLE": "Bouaflé", "DUEKOUE": "Duékoué",
    "ADZOPE": "Adzopé", "ADIAKE": "Adiaké", "ALEPE": "Alépé", "DANANE": "Danané", "ODIENNE": "Odienné",
    "SEGUELA": "Séguéla", "SOUBRE": "Soubré", "TIEBISSOU": "Tiébissou", "AKOUPE": "Akoupé", "BEOUMI": "Béoumi",
    "HIRE": "Hiré", "TAI": "Taï", "TIEME": "Tiémé", "ZUENOULA": "Zuénoula", "YAKASSE-ATTOBROU": "Yakassé-Attobrou",
    "GRAND-BEREBY": "Grand-Béréby", "MEAGUI": "Méagui", "DIEGONEFLA": "Diégonéfla", "DIDIEVI": "Didiévi",
    "DJEBONOUA": "Djébonoua", "DJEKANOU": "Djékanou", "BIN HOUYE": "Bin-Houyé", "TIE N'DIEKRO": "Tié-N'Diékro",
    "MBENGUE": "M'Bengué",
}  # fmt: skip
METHOD = (
    "Counted by the Commission Électorale Indépendante per commune, as printed in its results table. Each commune "
    "elects its council in one circumscription, from lists that each name a head of list. Registered voters are the "
    "electoral roll, ballots cast are the voters, and invalid ballots are the null ballots. {blank}The table prints "
    "no seats."
)
TWO_PERCENT = "The table prints percentages of its valid votes."


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"ci.cei/municipal-{plan.day}" for plan in PLANS)]


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    geo().write()
    registry().write()
    for plan in PLANS:
        build(plan).write()


def emit() -> None:
    package.run("load", packages())


def source_id(name: str) -> str:
    return key("source", "municipal", name)


def area(name: str) -> str:
    return key("area", SCHEME, fold(name))


def title(name: str) -> str:
    """A commune's name as the tables print it, in capitals, set in title case."""
    if name in ACCENTS:
        return ACCENTS[name]
    return re.sub(r"[A-Z]+", lambda m: m[0].capitalize(), name)


def day_communes(plan: Plan) -> list[Commune]:
    """The communes that voted on a plan's day: the 2018 table marks the ones that voted on 16 December."""
    found = parse_table(plan.file)
    if plan.file == "2018":
        return [c for c in found if (c.day or date(2018, 10, 13)) == plan.day]
    return found


def all_communes() -> dict[str, Commune]:
    """Every commune the tables list, by folded name, with the table that gave its 2013 number first."""
    out: dict[str, Commune] = {}
    for file in FILES:
        for commune in parse_table(file):
            out.setdefault(fold(commune.name), commune)
    return out


def party_label(text: str) -> str | None:
    """A party or bloc label as the tables print it, with spelling variants joined and a bloc's members in order."""
    parts = [ALIASES.get(part.strip(), part.strip()) for part in " ".join(text.upper().split()).split("/")]
    label = "/".join(sorted(set(parts)))
    return None if label in UNAFFILIATED else label


def party_labels() -> list[str]:
    found: set[str] = set()
    for file in FILES:
        for commune in parse_table(file):
            found |= {label for c in commune.candidates if (label := party_label(c.party))}
    return sorted(found)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The communes of Côte d'Ivoire that elected municipal councils in 2013, 2018, and 2023, named as the "
        "Commission Électorale Indépendante's municipal council tables name them, with the 2013 table's numbers",
    )
    p.external.add(COUNTRY)
    p.add("scheme", code=SCHEME, polity_id=COUNTRY, kind="administrative", rank=2, noun="commune")
    first_name = {fold(c.name): c for c in parse_table("2013")}
    for folded, commune in sorted(all_communes().items()):
        id = area(commune.name)
        p.add("area", id=id, scheme_code=SCHEME)
        if folded in first_name:
            p.add("area_code", area_id=id, code=first_name[folded].code)
        p.name(id, title(commune.name), "fr", "official")
    return p


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The municipal councils of Côte d'Ivoire and the parties on their lists, with the Commission Électorale "
        "Indépendante's municipal council tables as sources",
    )
    p.external.add(COUNTRY)
    for name, url in FILES.items():
        id = source_id(name)
        p.add(
            "source",
            id=id,
            kind="official_body",
            publisher="Commission Électorale Indépendante",
            title=TITLES[name],
            url=url,
            license="Official publication; reuse terms are not stated",
            attribution="Commission Électorale Indépendante de Côte d'Ivoire",
        )
        p.artifact(raw.cached(SOURCE, url), id)
    p.add("body", id=BODY, polity_id=COUNTRY, kind="council")
    p.name(BODY, "Municipal councils of Côte d'Ivoire", "en", "official")
    p.name(BODY, "Conseils municipaux de Côte d'Ivoire", "fr", "native", False)
    for label in party_labels():
        id = p.id("party", label)
        p.add("party", id=id, kind="bloc" if "/" in label else "party", polity_id=COUNTRY)
        p.name(id, label, "fr", "abbreviation")
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


HELD_ELECTIONS = {date(2023, 12, 2)}


def own_election(p: Package, plan: Plan, election: str, path: str, main: bool, year: int) -> None:
    """Write the record, names, and slug of an election no other package holds."""
    p.add(
        "election",
        id=election,
        polity_id=COUNTRY,
        kind=plan.kind,
        date_from=plan.day,
        date_to=plan.day,
        status="held",
        rerun_of_id=p.id("election", plan.rerun_of) if plan.rerun_of else None,
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    when = f"{plan.day.day} {plan.day.strftime('%B %Y')}"
    p.name(
        election,
        f"{year} Ivorian municipal council elections" if main else f"{year} Ivorian municipal council poll of {when}",
        "en",
        "official",
    )
    p.name(
        election,
        f"Élection des conseillers municipaux de {year}"
        if main
        else f"Élection des conseillers municipaux, scrutin du {plan.day.day:02d}/{plan.day.month:02d}/{year}",
        "fr",
        "native",
        False,
    )


def build(plan: Plan) -> Package:
    key_ = f"ci.cei/municipal-{plan.day}"
    depends = [GEO, REGISTRY]
    if plan.rerun_of:
        depends.append(f"ci.cei/municipal-{plan.rerun_of}")
    communes = day_communes(plan)
    layout = LAYOUTS[LAYOUT_OF[plan.file]]
    p = Package(
        key_,
        SOURCE,
        f"The {plan.day.year} municipal council election{'s' if len(communes) > 2 else ''} of {plan.day} in "
        f"{len(communes)} commune{'s' if len(communes) > 1 else ''} of Côte d'Ivoire, from the Commission "
        "Électorale Indépendante's results table",
        depends=depends,
    )
    p.external.add(COUNTRY)
    p.uses(raw.cached(SOURCE, FILES[plan.file]))
    election = p.id("election", plan.day)
    main = plan.kind == "local"
    year = plan.day.year
    path = f"ci/{plan.day}-municipal"
    if plan.day in HELD_ELECTIONS:
        # the regional package of this polling day holds the election's record, names, and slug: attach to it
        p.external.add(election)
    else:
        own_election(p, plan, election, path, main, year)
    held = held_dossiers(plan)
    slugs: set[str] = set()
    off: list[tuple[str, dict[str, int], int]] = []
    for commune in communes:
        name = title(commune.name)
        folded = fold(commune.name)
        contest = p.id("contest", plan.day, folded)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=BODY,
            district_area_id=area(commune.name),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{year} Ivorian municipal council election, {name} commune", "en", "official")
        p.name(contest, f"Conseil municipal de {name}", "fr", "native", False)
        slug = f"{path}/{folded.lower()}"
        assert slug not in slugs, slug
        slugs.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        unit = p.id("unit", plan.day, folded)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=COUNTRY,
            scheme_code=SCHEME,
            code=f"{plan.day}:{folded}",
            area_relation="equals",
            area_id=area(commune.name),
            kind="standard",
        )
        p.name(unit, name, "fr", "official")
        valid = listed(commune, LAYOUT_OF[plan.file])
        f = commune.figures
        if f["cast"] != f["invalid"] + valid + f.get("blank", 0):
            off.append((name, f, valid))
        votes = sum(c.score for c in commune.candidates)
        if f["valid"] not in (valid, valid + f.get("blank", 0)) or votes != valid:
            p.add(
                "note",
                id=p.id("note", contest, "figures"),
                entity_id=contest,
                kind="caveat",
                body_md=f"The table prints {f['valid']:,} valid votes for this commune and {votes:,} votes for its "
                f"lists, and the lists' votes and the commune's other figures do not reconcile. The valid votes stored "
                f"here are {valid:,}, which "
                + (
                    "equal the lists' votes."
                    if valid == votes
                    else "are the printed valid votes, less any blank ballots."
                ),
                lang="en",
            )
        result_set(p, plan, election, contest, unit, commune, folded, valid, layout.blank_in_valid, held)
        if plan.rerun_of and any(fold(c.name) == folded for c in parse_table(FIRST_OF[plan.file])):
            p.add(
                "note",
                id=p.id("note", contest, "rerun"),
                entity_id=contest,
                kind="caveat",
                body_md=f"The commune voted on {plan.rerun_of.day} {plan.rerun_of.strftime('%B %Y')} as well, and the "
                "commission's table of that poll prints a result for it. The poll of this day is stored apart.",
                lang="en",
            )
    over = [(title(c.name), c.figures) for c in communes if c.figures["cast"] > c.figures["registered"]]
    if over:
        p.accept(
            "accounting.turnout",
            "; ".join(
                f"in {name} the table prints {f['cast']:,} voters on a roll of {f['registered']:,} registered voters"
                for name, f in over
            )
            + "; stored as printed",
        )
    if off:
        p.accept(
            "accounting.ballots_sum",
            "; ".join(
                f"in {name} the table prints {f['invalid']:,} null ballots"
                + (f" and {f['blank']:,} blank ballots" if "blank" in f else "")
                + f", but its ballots cast ({f['cast']:,}) differ from the {valid:,} valid votes it counts for the lists"
                for name, f, valid in off
            )
            + "; stored as printed",
        )
    return p


FIRST_OF = {"2023-december": "2023-september"}


def held_dossiers(plan: Plan) -> dict[str, str]:
    """Map each dossier number the first poll of a rerun already holds to the person that poll's package wrote."""
    if not plan.rerun_of:
        return {}
    first = FIRST_OF[plan.file]
    held: dict[str, str] = {}
    for commune in parse_table(first):
        for position, candidate in enumerate(commune.candidates, 1):
            if candidate.dossier:
                held[candidate.dossier] = str(
                    uuid5(
                        package.NAMESPACE,
                        "\x1f".join([SOURCE, "person", str(plan.rerun_of), fold(commune.name), first, str(position)]),
                    )
                )
    return held


def result_set(
    p: Package,
    plan: Plan,
    election: str,
    contest: str,
    unit: str,
    commune: Commune,
    folded: str,
    valid: int,
    blank_in_valid: bool,
    held: dict[str, str],
) -> str:
    id = p.id("result_set", plan.day, folded, plan.file)
    p.add(
        "result_set",
        id=id,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source_id(plan.file),
        status="active",
        is_default=True,
        completeness=1.0,
    )
    blank = (
        "Blank ballots are counted apart from the valid votes for lists; the percentages divide the valid votes and "
        "the blank ballots together, or the valid votes alone, as each row prints them. "
        if "blank" in commune.figures
        else TWO_PERCENT + " "
    )
    p.add(
        "note",
        id=p.id("note", id),
        entity_id=id,
        kind="methodology",
        body_md=METHOD.format(blank=blank),
        lang="en",
    )
    figures_ = commune.figures
    put(p, id, unit, None, 2, figures_["registered"])
    put(p, id, unit, None, 3, figures_["cast"])
    put(p, id, unit, None, 5, figures_["invalid"])
    if "blank" in figures_:
        put(p, id, unit, None, 6, figures_["blank"])
    put(p, id, unit, None, 4, valid)
    for position, candidate in enumerate(commune.candidates, 1):
        option = p.id("option", plan.day, folded, plan.file, position)
        label = party_label(candidate.party)
        p.add(
            "contest_option",
            id=option,
            contest_id=p.id("contest", plan.day, folded),
            kind="list",
            party_id=p.id("party", label) if label else None,
            ballot_position=position,
            ballot_label=candidate.list_name or candidate.name,
        )
        person = held.get(candidate.dossier) if candidate.dossier else None
        if person is not None:
            p.external.add(person)
        else:
            person = p.id("person", plan.day, folded, plan.file, position)
            p.add("person", id=person)
            p.name(person, candidate.name, "fr", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", plan.day, folded, plan.file, position),
            person_id=person,
            option_id=option,
            role="head",
            list_position=1,
            ballot_name=candidate.name,
            member_of_party_id=p.id("party", label) if label else None,
            is_independent=candidate.party.upper() == "INDEPENDANT",
        )
        if candidate.dossier and candidate.dossier not in held:
            p.add("external_id", entity_id=option, scheme="ci.cei.dossier", value=candidate.dossier)
        put(p, id, unit, option, 1, candidate.score)
        if candidate.result:
            p.add(
                "outcome",
                result_set_id=id,
                option_id=option,
                kind="elected" if candidate.result.lower() == "vainqueur" else "lost",
            )
        elif any(c.result for c in commune.candidates):
            p.add("outcome", result_set_id=id, option_id=option, kind="lost")
    return id


def totals() -> list[str]:
    """Compare each table's printed national row with the sum of its communes, for the run report."""
    out = []
    for name in FILES:
        communes = parse_table(name)
        printed = printed_total(name)
        sums = {k: sum(c.figures[k] for c in communes) for k in printed}
        diffs = {k: sums[k] - v for k, v in printed.items() if sums[k] != v}
        out.append(f"{name}: {len(communes)} communes; {'equal' if not diffs else f'differs {diffs}'}")
    return out
