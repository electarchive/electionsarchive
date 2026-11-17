"""Belgian election results from the federal results service's per-election XML files.

Each file holds one body's election on one date, with the results at every tier the service publishes (country or
region, electoral college, constituency, province, arrondissement, canton, district, and municipality). The files carry
no parent links between tiers and no NIS codes, so a body that sits in one contest takes its votes from the finest tier
that tiles the published total, and bodies whose units are their own districts (municipal and provincial councils, and
the earliest Chamber elections) take one contest per unit.
"""

import difflib
import functools
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5
from xml.etree import ElementTree

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "be.ibz"
BASE = "https://resultatselection.belgium.be"
INDEX = f"{BASE}/fr/XmlOverview/Index"
BELGIUM = str(uuid5(package.NAMESPACE, "polity\x1fBE"))
SCHEME = "be.ibz"
COVERED = 0.9995
TIERS = ("Municipality", "Canton", "District", "Arrondissement", "Constituency", "Province", "ElectoralCollege")
TOPS = ("Country", "Region")
UNIT_TIERS = {
    "Municipality": ("Municipality", "MunicipalDistrict", "OCMW"),
    "Province": ("District",),
}
FALLBACK_TIER = ("Arrondissement",)
# kind, body kind or None, English, Dutch, French/German name, and the language of the third name
BODIES: dict[str, tuple[str, str | None, str, str, str, str]] = {
    "Chamber": (
        "chamber",
        "lower_house",
        "Chamber of Representatives",
        "Kamer van Volksvertegenwoordigers",
        "Chambre des représentants",
        "fr",
    ),
    "Senate": ("senate", "upper_house", "Senate", "Senaat", "Sénat", "fr"),
    "ParlementEurope": (
        "european-parliament",
        None,
        "European Parliament",
        "Europees Parlement",
        "Parlement européen",
        "fr",
    ),
    "ParlementFlanders": (
        "flemish-parliament",
        "unicameral",
        "Flemish Parliament",
        "Vlaams Parlement",
        "Parlement flamand",
        "fr",
    ),
    "ParlementWallonia": (
        "walloon-parliament",
        "unicameral",
        "Walloon Parliament",
        "Waals Parlement",
        "Parlement wallon",
        "fr",
    ),
    "ParlementBrussels": (
        "brussels-parliament",
        "unicameral",
        "Brussels Parliament",
        "Brussels Parlement",
        "Parlement bruxellois",
        "fr",
    ),
    "ParlementGermanRegion": (
        "german-speaking-parliament",
        "unicameral",
        "Parliament of the German-speaking Community",
        "Parlement van de Duitstalige Gemeenschap",
        "Parlament der Deutschsprachigen Gemeinschaft",
        "de",
    ),
    "Province": (
        "provincial-councils",
        "council",
        "Provincial councils",
        "Provincieraden",
        "Conseils provinciaux",
        "fr",
    ),
    "Municipality": (
        "municipal-councils",
        "council",
        "Municipal councils",
        "Gemeenteraden",
        "Conseils communaux",
        "fr",
    ),
    "MunicipalDistrict": (
        "antwerp-district-councils",
        "council",
        "Antwerp district councils",
        "Districtsraden van Antwerpen",
        "Conseils de district d'Anvers",
        "fr",
    ),
    "OCMW": (
        "social-welfare-councils",
        "council",
        "Social welfare councils (directly elected)",
        "OCMW-raden (rechtstreeks verkozen)",
        "Conseils de l'action sociale (élus directement)",
        "fr",
    ),
}
LOCAL = {"Province", "Municipality"}
LANGS = {"fr": "fr", "nl": "nl", "de": "de", "en": "en"}
METHOD = (
    "Counted and published by the Belgian federal election service, as the XML files on its results site. Each file "
    "lists the votes per list for every tier the service publishes. The files do not say which units belong to which "
    "constituency, so a contest takes its votes from the finest tier that adds up to the published total. Valid votes "
    "are the votes for lists, and blank votes are blank and invalid ballots. The files hold each candidate's "
    "preference votes; they are not stored here yet."
)


@dataclass
class Level:
    tier: str
    attrs: dict[str, str]
    labels: dict[str, str]
    lists: list[tuple[str, str, int, int]] = field(default_factory=list)

    @property
    def votes(self) -> int:
        return sum(votes for _, _, _, votes in self.lists)

    @property
    def seats(self) -> int:
        return sum(seats for _, _, seats, _ in self.lists)


def names() -> list[str]:
    text = raw.cached(SOURCE, INDEX).path.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'/xml/([^"]+\.xml)', text)))


def url(name: str) -> str:
    return f"{BASE}/xml/{name}"


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for name in names():
        raw.fetch(SOURCE, url(name), suffix=".xml")


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def folded(label: str) -> str:
    return clean(label).casefold()


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def party(label: str) -> str:
    return key("party", folded(label))


SOURCE_ID = key("source", "results")
BODY_IDS = {kind: key("body", kind) for kind in BODIES}


@functools.lru_cache(maxsize=6)
def parse_file(name: str) -> tuple[date, str, tuple[Level, ...]]:
    path = raw.cached(SOURCE, url(name)).path
    levels: list[Level] = []
    day: date | None = None
    kind = ""
    for event, element in ElementTree.iterparse(path, events=("start", "end")):
        if event == "start":
            if element.tag == "election":
                day = date.fromisoformat(element.attrib["date"][:10])
                kind = element.attrib["type"]
            continue
        if element.tag == "level":
            labels = {
                LANGS.get(label.attrib.get("language", ""), label.attrib.get("language", "")): clean(label.text)
                for label in element.iter("label")
            }
            lists = [
                (
                    entry.attrib.get("nr", ""),
                    clean(entry.attrib.get("party")),
                    int(entry.attrib.get("seats") or 0),
                    int(entry.attrib.get("votes") or 0),
                )
                for entry in element.iter("list")
            ]
            levels.append(Level(element.attrib.get("description", ""), dict(element.attrib), labels, lists))
            element.clear()
        elif element.tag == "candidates":
            element.clear()
    assert day is not None
    return day, kind, tuple(levels)


@functools.cache
def dated() -> dict[date, list[str]]:
    days: dict[date, list[str]] = {}
    for name in names():
        days.setdefault(parse_file(name)[0], []).append(name)
    return dict(sorted(days.items()))


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in dated())]


def parse() -> None:
    registry().write()
    for day, files in dated().items():
        election(day, files).write()


def emit() -> None:
    package.run("load", packages())


def add_source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="FOD Binnenlandse Zaken / SPF Intérieur",
        title="Resultaten verkiezingen / Résultats électoraux: XML files",
        url=f"{BASE}/fr/XmlOverview/Index",
        attribution="FOD Binnenlandse Zaken, Directie Verkiezingen",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Belgian parties, representative bodies, and the reporting-unit scheme from the federal election service's "
        "XML results",
    )
    p.external.add(BELGIUM)
    add_source(p)
    p.artifact(raw.cached(SOURCE, INDEX), SOURCE_ID)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=BELGIUM,
        kind="electoral_district",
        rank=1,
        noun="reporting unit",
        code_system=None,
    )
    for kind, (_, body_kind, english, dutch, other, language) in BODIES.items():
        if body_kind is None:
            continue
        id = BODY_IDS[kind]
        p.add("body", id=id, polity_id=BELGIUM, kind=body_kind)
        p.name(id, english, "en", "official")
        p.name(id, dutch, "nl", "native", False)
        p.name(id, other, language, "native", False)
    labels: dict[str, str] = {}
    for name in names():
        p.artifact(raw.cached(SOURCE, url(name)), SOURCE_ID)
        for level in parse_file(name)[2]:
            for _, label, _, _ in level.lists:
                if label:
                    labels.setdefault(folded(label), label)
    for folded_label, label in sorted(labels.items()):
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=BELGIUM)
        p.name(id, label, "und", "official")
    return p


def number(value: str | None) -> int | None:
    return None if value in (None, "") else int(str(value))


def accounting(attrs: dict[str, str], listed: int) -> dict[int, int]:
    figures: dict[int, int] = {}
    registered = number(attrs.get("eligible-voters"))
    if registered is None:
        parts = [int(v) for k, v in attrs.items() if k.startswith("registered-voters")]
        registered = sum(parts) if parts else None
    valid = number(attrs.get("valid-votes"))
    blank = number(attrs.get("blank-votes"))
    cast_parts = [int(v) for k, v in attrs.items() if k.startswith("total-votes")]
    if registered:
        figures[2] = registered
    if cast_parts:
        figures[3] = sum(cast_parts)
    figures[4] = listed if valid is None else valid
    if blank is not None:
        figures[6] = blank
    return figures


def pick(levels: tuple[Level, ...], tier: str) -> list[Level]:
    return [level for level in levels if level.tier == tier]


def label_of(level: Level) -> str:
    return level.labels.get("en") or level.labels.get("nl") or level.labels.get("fr") or level.tier


def unit_kind(level: Level) -> str:
    text = " ".join(level.labels.values())
    if re.search(r"\bE5\b|\(E5\)", text):
        return "postal_district"
    if re.search(r"Sp[ée]cia", text) or "Foreign Affairs" in text:
        return "abroad_mission"
    return "standard"


class Builder:
    def __init__(self, p: Package, day: date, base: str) -> None:
        self.p = p
        self.day = day
        self.base = base
        self.paths: Counter[str] = Counter()
        self.notes: list[str] = []
        self.compare: list[tuple[str, str, str, int, int, int, int]] = []
        self.valid_mismatch: Counter[str] = Counter()
        self.turnout: Counter[str] = Counter()
        self.ballots: Counter[str] = Counter()

    def slug(self, path: str) -> str:
        self.paths[path] += 1
        return path if self.paths[path] == 1 else f"{path}-{self.paths[path]}"

    def contest(
        self, tag: str, body: str, title: dict[str, str], seats: int, slug: str, order: list[tuple[str, str, int]]
    ) -> tuple[str, dict[str, str]]:
        p = self.p
        contest = p.id("contest", self.day, tag)
        p.add(
            "contest",
            id=contest,
            election_id=p.id("election", self.day),
            kind="list_allocation",
            body_id=BODY_IDS[body] if BODIES[body][1] else None,
            magnitude=seats or None,
            round_number=1,
            default_result_kind="counted",
        )
        names_seen: set[tuple[str, str]] = set()
        for language, text in title.items():
            if text and (language, text) not in names_seen:
                names_seen.add((language, text))
                p.name(contest, text, language, "official" if language == "en" else "native", language == "en")
        p.add("slug", path=self.slug(slug), entity_id=contest, is_current=True)
        options: dict[str, str] = {}
        for position, (_, label, _) in enumerate(order, 1):
            if folded(label) in options:
                continue
            option = options[folded(label)] = p.id("option", self.day, tag, folded(label))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(label),
                ballot_position=position,
                ballot_label=label,
            )
        return contest, options

    def result_set(self, tag: str, contest: str, default: bool, completeness: float) -> str:
        p = self.p
        result_set = p.id("result_set", self.day, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=p.id("election", self.day),
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=default,
            completeness=round(min(completeness, 1.0), 6),
        )
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        return result_set

    def unit(self, code: str, level: Level, parent: str | None = None) -> str:
        p = self.p
        unit = p.id("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELGIUM,
            scheme_code=SCHEME,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind=unit_kind(level),
            parent_id=parent,
        )
        seen: set[tuple[str, str]] = set()
        for language, text in level.labels.items():
            if text and (language, text) not in seen:
                seen.add((language, text))
                p.name(unit, text, language, "official" if language == "en" else "native", language == "en")
        return unit

    def values(
        self,
        result_set: str,
        unit: str,
        level: Level,
        options: dict[str, str],
        tag: str,
        resolve: Resolver | None = None,
    ) -> None:
        p = self.p
        votes: Counter[str] = Counter()
        for nr, label, _, count in level.lists:
            votes[folded(resolve(nr, label) if resolve else label)] += count
        for label, count in votes.items():
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=options[label],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=count,
            )
        figures = accounting(level.attrs, level.votes)
        valid = number(level.attrs.get("valid-votes"))
        if valid is not None and valid != level.votes:
            self.valid_mismatch[tag] += 1
        for measure, value in figures.items():
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
        if 3 in figures and 2 in figures and figures[3] > figures[2]:
            self.turnout[tag] += 1
        if 3 in figures and figures[4] + figures.get(6, 0) != figures[3]:
            self.ballots[tag] += 1

    def won(self, result_set: str, level: Level, options: dict[str, str]) -> None:
        seats: Counter[str] = Counter()
        for _, label, count, _ in level.lists:
            if count:
                seats[folded(label)] += count
        for label, count in seats.items():
            self.p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=count)


Resolver = Callable[[str, str], str]


def resolver(top: Level, by_number: bool = True) -> Resolver:
    """Map a list's label in a finer tier to the label the country or region level gives the same list.

    The service aggregates a list under one label, while the finer tiers print the label in the unit's language and
    reuse one list number for several unrelated lists in different constituencies, so a label is matched exactly,
    then by the list number among the labels that share it, choosing the closest spelling."""
    labels = {folded(label): label for _, label, _, _ in top.lists}
    numbers: dict[str, list[str]] = {}
    for nr, label, _, _ in top.lists:
        numbers.setdefault(nr, []).append(label)
    cache: dict[tuple[str, str], str] = {}

    def resolve(nr: str, label: str) -> str:
        found = cache.get((nr, label))
        if found is not None:
            return found
        if folded(label) in labels:
            found = labels[folded(label)]
        elif not by_number or not nr:
            found = label
        elif len(numbers.get(nr, [])) == 1:
            found = numbers[nr][0]
        elif numbers.get(nr):
            score, best = max((difflib.SequenceMatcher(None, folded(label), folded(c)).ratio(), c) for c in numbers[nr])
            found = best if score >= 0.5 else label
        else:
            found = label
        cache[(nr, label)] = found
        return found

    return resolve


def mismatched(top: Level, units: list[Level], resolve: Resolver) -> int:
    got: Counter[str] = Counter()
    for level in units:
        for nr, label, _, count in level.lists:
            got[folded(resolve(nr, label))] += count
    want: Counter[str] = Counter()
    for _, label, _, count in top.lists:
        want[folded(label)] += count
    return sum(1 for label, count in want.items() if got[label] != count)


def order_of(levels: list[Level], resolve: Resolver | None = None) -> list[tuple[str, str, int]]:
    seen: dict[str, tuple[str, str, int]] = {}
    for level in levels:
        for nr, label, _, _ in level.lists:
            label = resolve(nr, label) if resolve else label
            if label and folded(label) not in seen:
                seen[folded(label)] = (nr, label, int(nr) if nr.isdigit() else 0)
    return list(seen.values())


def election(day: date, files: list[str]) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day} Belgian elections by reporting unit from the federal election service",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(BELGIUM)
    for name in files:
        p.uses(raw.cached(SOURCE, url(name)))
    kinds = {parse_file(name)[1] for name in files}
    local = kinds <= LOCAL
    election_id = p.id("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=BELGIUM,
        kind="local" if local else "general",
        date_from=day,
        date_to=day,
        status="held",
    )
    title = f"{day.year} Belgian local elections" if local else f"{day.year} Belgian elections"
    p.name(election_id, title, "en", "official")
    base = f"be/{day}-{'local' if local else 'general'}"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    build = Builder(p, day, base)
    for name in sorted(files):
        _, kind, levels = parse_file(name)
        slug, _, english, dutch, other, language = BODIES[kind]
        title_map = {"en": english, "nl": dutch, language: other}
        tops = [tier for tier in TOPS if pick(levels, tier)]
        single = pick(levels, tops[0]) if tops else []
        if not single:
            constituencies = pick(levels, "Constituency")
            if len(constituencies) == 1 and not tops:
                single = constituencies
        if single:
            one_contest(build, kind, name, slug, title_map, single[0], levels)
        else:
            per_unit(build, kind, name, slug, levels)
    for tag, n in sorted(build.valid_mismatch.items()):
        build.notes.append(f"{tag}: {n} units print a valid-vote figure that differs from the sum of their lists")
    if any(build.valid_mismatch.values()):
        p.accept(
            "accounting.votes_valid",
            "the XML prints a valid-vote total per unit that differs from the sum of the unit's own list votes in "
            f"{sum(build.valid_mismatch.values())} units; both figures are stored as printed",
        )
    if any(build.ballots.values()):
        p.accept(
            "accounting.ballots_sum",
            "the XML prints a total-ballot figure that differs from its valid plus blank votes in "
            f"{sum(build.ballots.values())} units; stored as printed",
        )
    if any(build.turnout.values()):
        p.accept(
            "accounting.turnout",
            f"the XML prints more ballots than registered voters in {sum(build.turnout.values())} units, "
            "from voters who vote outside their home unit; stored as printed",
        )
    return p


def one_contest(
    build: Builder, kind: str, name: str, slug: str, title: dict[str, str], top: Level, levels: tuple[Level, ...]
) -> None:
    p, day = build.p, build.day
    total = top.votes or 1
    ranked: list[tuple[str, list[Level], float]] = []
    for tier in TIERS:
        found = [level for level in pick(levels, tier) if level.tier != top.tier]
        if found:
            ranked.append((tier, found, sum(level.votes for level in found) / total))
    primary = next((entry for entry in ranked if entry[2] >= COVERED and entry[2] <= 1 + (1 - COVERED)), None)
    if primary is None and ranked:
        primary = max(ranked, key=lambda entry: (round(entry[2], 4), -TIERS.index(entry[0])))
    assert primary is not None, name
    chosen, best = primary[1], primary[2]
    extras = [entry for entry in ranked if entry[0] == "Municipality" and entry[0] != primary[0]]
    pool = [top, *chosen, *(level for entry in extras for level in entry[1])]
    resolve = min((resolver(top, by_number) for by_number in (False, True)), key=lambda r: mismatched(top, chosen, r))
    contest, options = build.contest(slug, kind, title, top.seats, f"{build.base}/{slug}", order_of(pool, resolve))
    tag = f"{kind}"
    result_set = build.result_set(f"{slug}:{primary[0]}", contest, True, best)
    for index, level in enumerate(chosen, 1):
        unit = build.unit(f"{day}:{kind}:{primary[0]}:{index:04d}", level)
        build.values(result_set, unit, level, options, tag, resolve)
    build.won(result_set, top, options)
    declared: Counter[str] = Counter()
    for _, label, _, count in top.lists:
        declared[folded(label)] += count
    for label, count in declared.items():
        p.add("declared", result_set_id=result_set, option_id=options[label], measure=1, value=count)
    blanks = all(number(level.attrs.get("blank-votes")) is not None for level in chosen)
    for measure, value in accounting(top.attrs, top.votes).items():
        if measure in (2, 3) or (measure == 6 and not blanks):
            continue
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    wrong = mismatched(top, chosen, resolve)
    valid_summed = sum(accounting(level.attrs, level.votes)[4] for level in chosen)
    valid_top = accounting(top.attrs, top.votes)[4]
    build.compare.append((name, top.tier, primary[0], valid_top, valid_summed, wrong, len(declared)))
    if (
        wrong
        or valid_summed != valid_top
        or (
            blanks
            and sum(number(level.attrs.get("blank-votes")) or 0 for level in chosen)
            != (number(top.attrs.get("blank-votes")) or 0)
        )
    ):
        p.accept(
            "accounting.declared",
            f"the {primary[0]} tier the service publishes adds up to {valid_summed:,} valid votes against the "
            f"{valid_top:,} it prints for the {top.tier.lower()}, and {wrong} of {len(declared)} lists differ, "
            "because the tiers label and group lists differently and some votes appear only in the higher tier",
        )
    for tier, found, share in extras:
        extra = build.result_set(f"{slug}:{tier}", contest, False, share)
        for index, level in enumerate(found, 1):
            unit = build.unit(f"{day}:{kind}:{tier}:{index:04d}", level)
            build.values(extra, unit, level, options, tag + ":" + tier, resolve)


def per_unit(build: Builder, kind: str, name: str, slug: str, levels: tuple[Level, ...]) -> None:
    day = build.day
    tiers = UNIT_TIERS.get(kind, FALLBACK_TIER)
    for tier in tiers:
        for index, level in enumerate(pick(levels, tier), 1):
            label = label_of(level)
            tag = f"{slug}:{tier}:{index:04d}"
            title = {language: text for language, text in level.labels.items()}
            tier_slug = changeset.slug(tier)
            contest, options = build.contest(
                tag,
                tier if tier in BODIES else kind,
                title,
                level.seats,
                f"{build.base}/{slug}/{tier_slug}/{changeset.slug(label)}",
                order_of([level]),
            )
            result_set = build.result_set(tag, contest, True, 1.0)
            unit = build.unit(f"{day}:{kind}:{tier}:{index:04d}", level)
            build.values(result_set, unit, level, options, kind)
            build.won(result_set, level, options)
