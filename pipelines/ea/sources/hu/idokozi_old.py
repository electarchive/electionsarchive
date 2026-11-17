"""Hungary's local and minority by-elections held before 2015, built into packages from the office's archive.

The parsing of the archive's pages lives in `idokozi_archive`. This module turns each date's elections into one
package, and builds the settlement, county, party, body, and office records those packages share. Every id here is
minted in its own namespace, so these packages sit beside the ones the office's later by-elections load into.
"""

import difflib
import functools
import re
import unicodedata
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import cast
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.hu import idokozi as ik
from ea.sources.hu import idokozi_archive as arc
from ea.sources.hu import valasztas as hv

SOURCE = ik.SOURCE
NS = "hu.idokozi.archiv1"
HUNGARY = hv.HUNGARY
TERRITORY = hv.TERRITORY
END = date(2015, 1, 1)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([NS, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", NS)
MAYOR = key("electoral_system", "local-mayor")
BLOCK = key("electoral_system", "local-block")
WARD = key("electoral_system", "local-ward")
MINORITY = key("electoral_system", "minority-block")
SYSTEMS: list[tuple[str, dict[str, object], str]] = [
    (
        MAYOR,
        {"formula": "fptp"},
        "Mayors are elected directly in a single round; the candidate with the most votes wins.",
    ),
    (
        BLOCK,
        {"formula": "block"},
        (
            "In small settlements the council is elected on one ballot from individual candidates: each voter may vote "
            "for as many candidates as there are seats, and the candidates with the most votes win."
        ),
    ),
    (
        WARD,
        {"formula": "fptp"},
        (
            "In larger settlements and in Budapest's districts, part of the council is elected in single-member wards "
            "by the most votes; the rest of the seats are filled from compensation lists."
        ),
    ),
    (
        MINORITY,
        {"formula": "block"},
        (
            "A settlement's minority self-government is elected by the voters on the minority's roll, each voting for "
            "as many candidates as there are seats; the candidates with the most votes win."
        ),
    ),
]
METHOD_LOCAL = (
    "Counted by the settlement's election commission and published by the National Election Office (Nemzeti "
    "Választási Iroda) for the settlement or ward as a whole, in the final protocol data. Registered voters are the "
    "roll when voting closed, voters are those who appeared, and valid and invalid are the valid and invalid ballots."
)
METHOD_MINORITY = (
    "Counted by the settlement's election commission and published by the National Election Office (Nemzeti "
    "Választási Iroda) for the settlement as a whole. Registered voters are the electors of the minority "
    "self-government, voters are those who appeared, and valid and invalid are the valid and invalid ballots; each "
    "voter had as many votes as there were seats."
)


@dataclass
class State:
    places: dict[str, tuple[str, str]]
    counties: set[str]
    parties: dict[str, str]
    bodies: dict[str, tuple[str, str, str]]
    offices: dict[str, tuple[str, str]]
    cited: set[str]


STATE = State({}, set(), {}, {}, {}, set())


def place(code: str, name: str) -> str:
    STATE.places.setdefault(code, (code[:2], name))
    STATE.counties.add(code[:2])
    return key("area", "settlement", code)


def party_id(label: str | None) -> str | None:
    if label is None:
        return None
    label = hv.party_label(label)
    if hv.INDEPENDENT.match(label):
        return None
    folded = label.upper()
    STATE.parties.setdefault(folded, label)
    return key("party", folded)


def council_body(code: str, name: str, nation: str | None = None, english: str | None = None) -> str:
    if nation is None:
        id = key("body", "council", code)
        STATE.bodies[id] = (f"{name} council", f"{name} képviselő-testülete", "council")
    else:
        id = key("body", "minority", code, nation)
        STATE.bodies[id] = (
            f"{english or nation} self-government of {name}",
            f"{name} {nation.lower()} kisebbségi önkormányzat",
            "council",
        )
    return id


def mayor_office(code: str, name: str) -> str:
    id = key("office", "mayor", code)
    STATE.offices[id] = (f"Mayor of {name}", f"{name} polgármestere")
    return id


class Day(ik.Day):
    """One package: every archive by-election held on one date."""

    def __init__(self, day: date, summary: str) -> None:
        super().__init__(day, summary)
        self.p = Package(
            f"{SOURCE}/{day}", SOURCE, summary, depends=[f"{SOURCE}/geo-archiv1", f"{SOURCE}/registry-archiv1"]
        )
        self.p.external |= {HUNGARY, TERRITORY}

    def reconcile(self) -> None:
        super().reconcile()
        over = {
            rs
            for (rs, unit, option, measure), amount in self.values.items()
            if option is None and measure == 14 and amount > self.values.get((rs, unit, None, 2), amount)
        }
        if over and "accounting.turnout" not in self.p.accepted:
            what, reason = hv.CAVEATS["accounting.turnout"]
            self.p.accept(
                "accounting.turnout",
                f"{len(over)} contests: the office's table prints more voters than the electors on the roll; stored as printed",
            )
            for rs in over:
                self.p.add(
                    "note",
                    id=hv.key("note", rs, "accounting.turnout"),
                    entity_id=rs,
                    kind="caveat",
                    body_md="The office's table prints more voters than electors on the roll. The figures are stored as printed.",
                    lang="en",
                )
            _ = (what, reason)

    def cite(self, name: str) -> None:
        if name not in self.cited:
            self.cited.add(name)
            STATE.cited.add(name)
            self.p.uses(raw.cached(SOURCE, name))

    def contest(  # type: ignore[override]
        self,
        tag: str,
        election: str,
        kind: str,
        name: str,
        path: str,
        *,
        district_area: str = TERRITORY,
        magnitude: int | None = 1,
        round_number: int = 1,
        previous: str | None = None,
        system: str | None = None,
        body: str | None = None,
        office: str | None = None,
        method: str,
        english: str | None = None,
    ) -> str:
        contest = hv.key("contest", tag)
        self.p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district_area,
            magnitude=magnitude,
            round_number=round_number,
            previous_round_id=None,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        self.p.name(contest, name, "hu", "official")
        if english:
            self.p.name(contest, english, "en", "english", False)
        self.p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = hv.key("result_set", tag)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        self.p.add(
            "note", id=hv.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        self.sets[tag] = result_set
        return result_set

    def option(  # type: ignore[override]
        self,
        tag: str,
        label: str,
        kind: str,
        *,
        nominator: str | None = None,
        position: int | None = None,
        ident: str | None = None,
    ) -> str:
        index = (tag, ident or hv.same_name(label) or label)
        found = self.options.get(index)
        if found is not None:
            return found
        id = hv.key("option", tag, index[1])
        self.p.add(
            "contest_option",
            id=id,
            contest_id=hv.key("contest", tag),
            kind=kind,
            party_id=party_id(nominator),
            ballot_position=position,
            ballot_label=re.sub(r"\s+", " ", label).strip(),
        )
        self.options[index] = id
        return id

    def settlement_unit(self, area_code: str) -> str:
        code = f"{self.day}:{area_code}"
        found = self.units.get(code)
        if found is None:
            found = self.units[code] = hv.key("unit", "ik-archiv1", code)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=HUNGARY,
                scheme_code="hu.idokozi_archiv_telepules",
                code=code,
                area_relation="equals",
                area_id=key("area", "settlement", area_code),
                kind="standard",
            )
        return found


# ---------------------------------------------------------------------------------------------------------------
# Places


@functools.cache
def place_index() -> tuple[dict[tuple[str, str], str], dict[str, list[str]], dict[str, str]]:
    names, by_name = ik.settlement_codes()
    by_folded: dict[str, list[str]] = defaultdict(list)
    for (_, folded), code in by_name.items():
        by_folded[folded].append(code)
    return by_name, by_folded, names


def settlement(maz: str, name: str) -> tuple[str, str]:
    """The office's settlement code and name for a place as the archive prints it, else a name-based code."""
    by_name, by_folded, names = place_index()
    printed = re.sub(r"\s*\(.*\)", "", name).strip()
    district = re.fullmatch(
        r"(?:bp\.?|budapest)?\s*([IVXL]+)\.?(?:\s*ker\.?|\s*kerület)?", printed, flags=re.IGNORECASE
    )
    if district and (maz == "01" or printed.lower().startswith(("bp", "budapest"))):
        printed = f"Budapest {district[1].upper()}. kerület"
        maz = "01"
    folded = hv.fold(printed)
    code = by_name.get((maz, folded))
    if code is None and len(by_folded.get(folded, [])) == 1:
        code = by_folded[folded][0]
    if code is None:
        close = difflib.get_close_matches(folded, [f for (m, f) in by_name if m == maz], n=1, cutoff=0.88)
        if close:
            code = by_name[(maz, close[0])]
    if code is None:
        return f"{maz or '00'}-{changeset.slug(printed)}", printed
    return code, names[code]


# ---------------------------------------------------------------------------------------------------------------
# One date


def contest_for(d: Day, tree: ik.Tree, result: arc.Result, code: str, name: str) -> str | None:
    """Stage the contest of one election and return its tag, or None when the date already holds it."""
    if result.role == "minority":
        if result.nation is None:
            return None
        english, hungarian = next(v for v in arc.NATIONS.values() if v[1] == result.nation)
        tag = f"ik:{d.day}:nationality:{code}:{changeset.slug(hungarian)}"
        if tag in d.sets:
            return None
        election, _ = tree.child("nationality")
        d.contest(
            tag,
            election,
            "body_seats",
            f"{name} {hungarian.lower()} kisebbségi önkormányzat",
            tree.slug("nationality", f"{name}-{hungarian}", code),
            magnitude=max(result.seats or len(result.winners) or 1, 1),
            method=METHOD_MINORITY,
            english=f"{english} minority self-government of {name}",
            body=council_body(code, name, hungarian, f"{english} minority"),
            district_area=place(code, name),
            system=MINORITY,
        )
        return tag
    seats = result.seats or len(result.winners) or sum(c.elected for c in result.candidates) or 1
    ward = result.ward
    tag = f"ik:{d.day}:local:{result.role}:{code}" + (f":{ward}" if ward else "")
    if tag in d.sets:
        return None
    election, _ = tree.child("local")
    if result.role == "mayor":
        title, english = f"{name} polgármester", f"Mayor of {name}"
        office, body, system, kind, magnitude = mayor_office(code, name), None, MAYOR, "office", 1
        slug = tree.slug("local", f"{name}-mayor", code)
    elif result.role == "council":
        title, english = f"{name} képviselő-testület", f"{name} council"
        office, body, system, kind, magnitude = None, council_body(code, name), BLOCK, "body_seats", max(seats, 1)
        slug = tree.slug("local", f"{name}-council", code)
    else:
        number = int(cast(str, ward)) if ward else None
        title = f"{name} {number}. egyéni választókerület" if number else f"{name} egyéni választókerület"
        english = f"{name} ward {number}" if number else f"{name} ward"
        office, body, system, kind, magnitude = None, council_body(code, name), WARD, "body_seats", 1
        slug = tree.slug("local", f"{name}-ward-{number}" if number else f"{name}-ward", code)
    d.contest(
        tag,
        election,
        kind,
        title,
        slug,
        magnitude=magnitude,
        method=METHOD_LOCAL,
        english=english,
        office=office,
        body=body,
        district_area=place(code, name),
        system=system,
    )
    return tag


def words(name: str) -> tuple[str, ...]:
    """The words of a printed name without titles or accents, so spellings that differ in them still line up."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return tuple(w for w in re.split(r"[^a-z]+", plain) if w and w not in ("dr", "id", "ifj", "ozv", "prof"))


def match(candidates: list[arc.Candidate], name: str) -> int | None:
    """The index of the candidate a declared winner's printed name belongs to."""
    wanted = words(name)
    for i, candidate in enumerate(candidates):
        if words(candidate.name) == wanted:
            return i
    hits = [
        i
        for i, candidate in enumerate(candidates)
        if hv.is_subsequence(wanted, words(candidate.name)) or hv.is_subsequence(words(candidate.name), wanted)
    ]
    return hits[0] if len(hits) == 1 else None


UNMATCHED: list[str] = []


def build(day: date) -> Day:
    d = Day(day, f"Hungary's local and minority by-elections of {day} by settlement, from the National Election Office")
    tree = ik.Tree(d)
    crawl = arc.crawl_key(day)
    files: dict[str, bytes] = {}
    if crawl in raw.keys(SOURCE):
        with zipfile.ZipFile(raw.cached(SOURCE, crawl).path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        d.cite(crawl)
    d.cite(arc.ARCHIVE)
    d.reasons["accounting.votes_valid"] = (
        "the office's table prints the valid votes of a settlement without every candidate's votes, or the candidate "
        "votes in its protocol do not add up to the valid ballots it prints; stored as printed"
    )
    d.reasons["accounting.ballots_sum"] = (
        "the office's protocol prints valid and invalid ballots that do not add up to the ballots cast; stored as printed"
    )
    d.reasons["accounting.turnout"] = (
        "the office's protocol prints more ballots cast than voters on the roll; stored as printed"
    )
    for result in arc.results(day, files):
        for source in result.sources:
            if source in raw.keys(SOURCE):
                d.cite(source)
        code, name = settlement(result.maz, result.place)
        tag = contest_for(d, tree, result, code, name)
        if tag is None:
            continue
        unit = d.settlement_unit(code)
        roll = result.roll
        if roll:
            d.accounting(
                tag,
                unit,
                roll.get("registered"),
                roll.get("voters"),
                roll.get("cast"),
                roll.get("invalid"),
                roll.get("valid"),
            )
        compensated: list[str] = []
        for position, candidate in enumerate(result.candidates, 1):
            nominator = candidate.nominator if candidate.nominator.strip(" -") else "Független"
            if len(nominator) > 120 or re.search(r"\d[,.]\d\d", nominator):
                raise ValueError(f"{day} {result.place}: {nominator!r}")
            option = d.option(
                tag, candidate.name, "candidate", nominator=nominator, position=position, ident=f"n{position}"
            )
            d.candidate(tag, option, f"{tag}:{position}", candidate.name, nominator=nominator)
            d.value(tag, unit, option, 1, candidate.votes)
            if candidate.elected:
                compensated.append(candidate.name)
        for winner, nominator in result.winners:
            if not words(winner):
                continue
            index = match(result.candidates, winner) if result.candidates else None
            if index is not None:
                option = d.option(tag, result.candidates[index].name, "candidate", ident=f"n{index + 1}")
            else:
                if result.candidates:
                    UNMATCHED.append(f"{day} {result.place} {result.role}: {winner}")
                nominator = nominator or "Független"
                ident = f"w:{hv.same_name(winner)}"
                option = d.option(tag, winner, "candidate", nominator=nominator, ident=ident)
                d.candidate(tag, option, f"{tag}:{ident}", winner, nominator=nominator)
            d.outcome(tag, option, "elected")
        if compensated:
            d.p.add(
                "note",
                id=hv.key("note", d.sets[tag], "compensation"),
                entity_id=d.sets[tag],
                kind="caveat",
                body_md=(
                    "The office marks these candidates as elected from the compensation list, not in this ward: "
                    + "; ".join(compensated)
                    + "."
                ),
                lang="en",
            )
        status = result.status.lower()
        if "eredménytelen" in status or "érvénytelen" in status:
            d.p.add(
                "note",
                id=hv.key("note", d.sets[tag], "invalid"),
                entity_id=d.sets[tag],
                kind="caveat",
                body_md="The office declared this election without a valid result.",
                lang="en",
            )
        if result.scan and not result.candidates and not result.winners:
            d.p.add(
                "note",
                id=hv.key("note", d.sets[tag], "scan"),
                entity_id=d.sets[tag],
                kind="caveat",
                body_md=(
                    "The office published this election's protocol only as a scanned image, so the archive holds no "
                    "figures for it."
                ),
                lang="en",
            )
    return d


# ---------------------------------------------------------------------------------------------------------------
# Shared records


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo-archiv1",
        SOURCE,
        "The counties and settlements of Hungary's by-elections held before 2015, as the National Election Office names them",
    )
    p.external |= {HUNGARY, TERRITORY}
    for code, rank, kind, noun in (
        ("hu.idokozi_archiv_megye", 1, "administrative", "county"),
        ("hu.idokozi_archiv_telepules", 3, "administrative", "settlement"),
    ):
        p.add("scheme", code=code, polity_id=HUNGARY, kind=kind, rank=rank, noun=noun, code_system=None)
    for maz in sorted(STATE.counties):
        id = key("area", "county", maz)
        p.add("area", id=id, scheme_code="hu.idokozi_archiv_megye")
        p.add("area_code", area_id=id, code=maz)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        name = hv.COUNTIES.get(maz, maz)
        p.name(id, "Budapest" if maz == "01" else f"{name} vármegye", "hu", "official")
    for code, (maz, name) in sorted(STATE.places.items()):
        id = key("area", "settlement", code)
        p.add("area", id=id, scheme_code="hu.idokozi_archiv_telepules")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=key("area", "county", maz))
        p.name(id, hv.person_text(name) if name.isupper() else name, "hu", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry-archiv1",
        SOURCE,
        "The source, electoral systems, parties, bodies, and offices of Hungary's by-elections held before 2015",
        depends=[f"{SOURCE}/geo-archiv1"],
    )
    p.external |= {HUNGARY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Nemzeti Választási Iroda",
        title="Időközi önkormányzati választások, archívum (valasztas.hu)",
        url=arc.ARCHIVE,
        license="Public information under Act CXII of 2011; reuse with the source acknowledged",
        license_url="https://www.valasztas.hu/impresszum",
        attribution="Nemzeti Választási Iroda, valasztas.hu",
    )
    stored: set[str] = set()
    for url in sorted(STATE.cited):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in stored:
            stored.add(artifact.storage_key)
            p.artifact(artifact, SOURCE_ID)
    for id, rules, text in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=HUNGARY,
            valid_from=date(1990, 1, 1),
            valid_to=None,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for id, (english, native, kind) in sorted(STATE.bodies.items()):
        p.add("body", id=id, polity_id=HUNGARY, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, native, "hu", "native", False)
    for id, (english, native) in sorted(STATE.offices.items()):
        p.add("office", id=id, polity_id=HUNGARY, kind="mayor")
        p.name(id, english, "en", "official")
        p.name(id, native, "hu", "native", False)
    for folded, label in sorted(STATE.parties.items()):
        id = key("party", folded)
        p.add("party", id=id, kind="bloc" if re.search(r",|-KDNP|\bÉS\b", label) else "party", polity_id=HUNGARY)
        p.name(id, label, "hu", "official")
    return p


@functools.cache
def built() -> dict[date, Day]:
    """The package of every date before 2015 on which the archive holds at least one by-election."""
    found = {day: build(day) for day in sorted({e.day for e in arc.all_entries() if e.day < END})}
    return {day: d for day, d in found.items() if d.sets}


def packages() -> list[str]:
    return [f"{SOURCE}/geo-archiv1", f"{SOURCE}/registry-archiv1", *(f"{SOURCE}/{day}" for day in built())]


def parse(only: str | None = None) -> None:
    for day, d in built().items():
        if not only or only in str(day):
            d.write()
    geo().write()
    registry().write()
