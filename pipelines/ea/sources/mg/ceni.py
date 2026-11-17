"""Madagascar's national election authority (CENI): results by polling station, commune, and district from its PDFs."""

import re
from dataclasses import dataclass
from pathlib import Path

from ea import raw

SOURCE = "mg.ceni"
SITE = "https://www.ceni-madagascar.mg"
UPLOADS = f"{SITE}/wp-content/uploads"
LISTINGS = {
    "communal2024": f"{SITE}/resultats-provisoires-des-elections-communales-et-municipales-du-11-decembre-2024/",
}
FILES = {
    "pres2018-1": f"{UPLOADS}/2019/02/1recapparbv.pdf",
    "pres2018-1-commune": f"{UPLOADS}/2019/02/1recapcommune.pdf",
    "pres2018-1-national": f"{UPLOADS}/2019/02/1recapnationale.pdf",
    "pres2018-2": f"{UPLOADS}/2019/02/recapparbv.pdf",
    "pres2018-2-commune": f"{UPLOADS}/2019/02/recapcommune.pdf",
    "pres2018-2-national": f"{UPLOADS}/2019/02/recapnational.pdf",
    "pres2023": f"{UPLOADS}/2023/11/Recapitulation_par_Commune.pdf",
    "pres2023-national": f"{UPLOADS}/2023/11/RECAP-NATIONALE-ELECTION-PRESIDENTIELLE-16-11-2023.pdf",
    "leg2019": f"{UPLOADS}/2021/12/Resultats-legislatifs-2019-par-district.pdf",
    "leg2024": f"{UPLOADS}/2024/06/Tableau-recap-par-Commune-Legislatives-2024_sur-serveur.pdf",
    "leg2024-districts": f"{UPLOADS}/2024/06/Resultat-provisoire-legislatives-2024-1.pdf",
    "mayor2019": f"{UPLOADS}/2021/12/Election_provisoire_maire_2019.pdf",
    "councillor2019": f"{UPLOADS}/2021/12/Election_provisoire_conseiller_2019.pdf",
}


def listed(name: str) -> list[str]:
    html = raw.cached(SOURCE, LISTINGS[name]).path.read_text(errors="replace")
    return sorted(set(re.findall(r'https://www\.ceni-madagascar\.mg/wp-content/uploads/[^"\' ]+\.pdf', html)))


def pdfs(name: str) -> list[str]:
    """The result PDFs a listing page links to, leaving out the site-wide documents every page repeats."""
    return [url for url in listed(name) if "/2024/12/RESULTAT-" in url]


def fetch() -> None:
    for url in LISTINGS.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    for name in LISTINGS:
        for url in pdfs(name):
            raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, SENATE_LISTING, suffix=".html")
    for name, url in SENATE_FILES.items():
        if name not in SENATE_ABSENT:
            raw.fetch(SOURCE, url, suffix=".pdf")


def path(name: str) -> Path:
    return raw.cached(SOURCE, FILES[name]).path


import unicodedata
from collections import defaultdict
from datetime import date
from functools import cache
from uuid import uuid5

from ea import package
from ea.package import Package
from ea.sources.mg import read

MADAGASCAR = str(uuid5(package.NAMESPACE, "polity\x1fMG"))
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
PAGE = f"{SITE}/documents/"
DAYS = {
    "2018-11-07": date(2018, 11, 7),
    "2018-12-19": date(2018, 12, 19),
    "2019-05-27": date(2019, 5, 27),
    "2019-11-27": date(2019, 11, 27),
    "2020-12-11": date(2020, 12, 11),
    "2023-11-16": date(2023, 11, 16),
    "2024-05-29": date(2024, 5, 29),
    "2024-12-11": date(2024, 12, 11),
}
SENATE_DAY = "2020-12-11"
SENATE_PROVINCES = ("ANTANANARIVO", "ANTSIRANANA", "FIANARANTSOA", "MAHAJANGA", "TOAMASINA", "TOLIARY")
# The authority's page for the 2020 senatorial results links six province sheets; two of them answer 404.
SENATE_LISTING = f"{SITE}/resultat-des-elections-senatoriales-2020/"
SENATE_FILES = {name: f"{UPLOADS}/2021/12/{name}.pdf" for name in SENATE_PROVINCES}
SENATE_ABSENT = ("FIANARANTSOA", "TOAMASINA")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CENI = key("source", "ceni")
ASSEMBLY = key("body", "national_assembly")
COUNCIL = key("body", "communal_council")
PRESIDENCY = key("office", "president")
MAYOR = key("office", "mayor")
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")
SCHEMES = (("mg.region", "region", 1), ("mg.district", "district", 2), ("mg.commune", "commune", 3))


def norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", plain)


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


# Names the 2024 sheets give some districts differently from the other sheets.
DISTRICTS = {
    "ANTANANARIVO": "ANTANANARIVOI",
    "AMPANIHYANDREFANA": "AMPANIHYOUEST",
    "ANKAZOABOATSIMO": "ANKAZOABOSUD",
    "BEFANDRIANAAVARATRA": "BEFANDRIANANORD",
    "BETIOKYATSIMO": "BETIOKYSUD",
    "MANANARAAVARATRA": "MANANARANORD",
    "TOLIARAI": "TOLIARYI",
    "ANTANANRIVOVI": "ANTANANARIVOVI",
    "AMBATOBOENI": "AMBATOBOENY",
    "TAOLAGNARO": "TAOLANARO",
}


def pdf(name: str) -> Path:
    return path(name)


@cache
def stations_2018_1() -> list[read.Count]:
    return list(read.station_blocks(path("pres2018-1")))


@cache
def stations_2018_2() -> list[read.Row]:
    return list(read.runoff_2018(path("pres2018-2")))


@cache
def communes_2018(round_number: int) -> list[read.Count]:
    return list(read.commune_blocks(path(f"pres2018-{round_number}-commune")))


@cache
def stations_2023() -> list[read.Row]:
    return list(read.recap(path("pres2023"), 2023))


@cache
def stations_2024() -> list[read.Row]:
    return list(read.recap(path("leg2024"), 2024))


@cache
def districts_2019() -> list[read.Sheet]:
    return list(read.district_sheets_2019(path("leg2019")))


@cache
def districts_2024() -> list[read.Sheet]:
    return list(read.district_sheets_2024(path("leg2024-districts")))


# Councillor sheets whose vote figure is printed glued to a wrapped party name or percent, so the layout reader cannot
# tell where the figure ends. Each entry gives the candidates' votes and parties as the printed sheet reads, by ballot
# order; `_repair` checks them against the sheet's own total before they are used.
GLUED: dict[tuple[str, str], list[tuple[int, str]]] = {
    ("VANGAINDRANO", "LOHAFARY"): [
        (773, "GROUPEMENT DE P.P GROUPEMENT DE P.P ISIKA REHETRA MIARAKA @ ANDR"),
        (482, "GROUPEMENT DE P.P MANARANARA FANILO"),
        (474, "GROUPEMENT DE P.P GROUPEMENT DE P.P PARTI VERT HASIN'I MADAGASIKA"),
    ],
    ("AMBALAVAO", "ANJOMA"): [
        (1334, "INDEPENDANT NY OLO RO MAGNOVA NY TANY FA NY TANY TSA MBA MAGNOVA"),
        (1222, "IRD"),
    ],
    ("IFANADIANA", "TSARATANANA"): [
        (911, "IRD"),
        (1399, "INDEPENDANT RAMIANDRISOA HARIMANGA JEAN PAUL"),
    ],
}


def _repair(sheet: read.Sheet) -> read.Sheet:
    fixed = GLUED.get((sheet.place["district"], sheet.place["commune"]))
    if fixed is None or _sound(sheet) or len(fixed) != len(sheet.candidates):
        return sheet
    for candidate, (votes, party) in zip(sheet.candidates, fixed, strict=True):
        candidate.votes, candidate.party = votes, party
    if not _sound(sheet):
        raise ValueError(f"{sheet.place}: repaired votes do not add up to the sheet's total")
    return sheet


@cache
def communal_2019(office: str) -> list[read.Sheet]:
    sheets = read.commune_sheets_2019(path(f"{office}2019"))
    if office == "councillor":
        sheets = (_repair(s) for s in sheets)
    return [s for s in sheets if s.candidates and all(c.votes >= 0 for c in s.candidates) and _sound(s)]


def _sound(sheet: read.Sheet) -> bool:
    return sum(c.votes for c in sheet.candidates) in (sheet.count.total, sheet.count.valid)


@cache
def communal_2024() -> list[read.Sheet]:
    sheets: list[read.Sheet] = []
    for url in pdfs("communal2024"):
        sheets += read.commune_sheets_2024(raw.cached(SOURCE, url).path)
    return sheets


class Geo:
    """Regions, districts, and communes, named as the authority's sheets name them. Its numeric codes are not stable
    from one election to the next, so places are identified by name and nest as the sheets print them."""

    def __init__(self) -> None:
        self.region_names: dict[str, str] = {}
        self.district_names: dict[str, str] = {}
        self.district_region: dict[str, str] = {}
        self.commune_names: dict[tuple[str, str], str] = {}
        self.by_district: dict[str, list[str]] = defaultdict(list)

    def district_key(self, name: str) -> str:
        text = norm(name)
        text = DISTRICTS.get(text, text)
        if text not in self.district_names and len(text) >= 16:
            for known in self.district_names:
                if known.startswith(text):
                    return known
        return text

    def add(self, region: str, district: str, commune: str) -> None:
        d = self.district_key(district)
        if d not in self.district_names:
            self.district_names[d] = district.replace("_", " ").strip()
            self.region_names.setdefault(norm(region), region.strip())
            self.district_region[d] = norm(region)
        if commune:
            self.commune(d, commune, create=True, display=commune)

    def commune(self, d: str, name: str, create: bool = False, display: str = "") -> str:
        c = norm(name)
        if (d, c) in self.commune_names:
            return c
        for other in self.by_district[d]:
            if len(c) >= 16 and (other.startswith(c) or (len(other) >= 16 and c.startswith(other))):
                return other
        if not create:
            raise KeyError(f"no commune {name!r} in district {d}")
        self.commune_names[d, c] = display.strip()
        self.by_district[d].append(c)
        return c


@cache
def geo() -> Geo:
    """Every place any sheet names, read in the order of the sheets with the most complete and untruncated names."""
    g = Geo()
    for row in stations_2024():
        g.add(row.region, row.district, row.commune)
    for row in stations_2018_2():
        g.add(row.region, row.district, row.commune)
    for row in stations_2023():
        g.add(row.region, row.district, row.commune)
    for sheet in communal_2024():
        g.add(sheet.place["region"], sheet.place["district"], sheet.place["commune"])
    for office in ("mayor", "councillor"):
        for sheet in communal_2019(office):
            g.add(sheet.place["region"], sheet.place["district"], sheet.place["commune"])
    for count in communes_2018(1) + communes_2018(2):
        g.add(count.place["region"], count.place["district"], count.place["commune"])
    for count in stations_2018_1():
        g.add(count.place["region"], count.place["district"], count.place["commune"])
    for sheet in districts_2019() + districts_2024():
        g.add(sheet.place["region"], sheet.place["district"], "")
    return g


def area(scheme: str, *parts: str) -> str:
    return key("area", scheme, *parts)


def commune_area(d: str, c: str) -> str:
    return area("mg.commune", d, c)


def district_area(d: str) -> str:
    return area("mg.district", d)


def build_geo() -> Package:
    g = geo()
    p = Package(
        "mg.ceni/geo",
        SOURCE,
        "Madagascar's regions, districts, and communes, as the national election authority's result sheets name them",
    )
    p.external.add(MADAGASCAR)
    for code, kind, rank in SCHEMES:
        p.add("scheme", code=code, polity_id=MADAGASCAR, kind="administrative", rank=rank, noun=kind)
    for r, name in sorted(g.region_names.items()):
        p.add("area", id=area("mg.region", r), scheme_code="mg.region")
        p.name(area("mg.region", r), name.title() if name.isupper() else name, "fr", "official")
    for d, name in sorted(g.district_names.items()):
        p.add("area", id=district_area(d), scheme_code="mg.district")
        p.name(district_area(d), name.title() if name.isupper() else name, "fr", "official")
        p.add("area_parent", area_id=district_area(d), parent_id=area("mg.region", g.district_region[d]))
    for (d, c), name in sorted(g.commune_names.items()):
        p.add("area", id=commune_area(d, c), scheme_code="mg.commune")
        p.name(commune_area(d, c), name.title() if name.isupper() else name, "fr", "official")
        p.add("area_parent", area_id=commune_area(d, c), parent_id=district_area(d))
    return p


def independent(label: str) -> bool:
    return norm(label).startswith("INDEPENDANT")


def party_id(label: str, scope: str = "") -> str:
    return key("party", scope, norm(label)) if scope else key("party", norm(label))


@cache
def party_labels() -> dict[str, tuple[str, str]]:
    """Every list or party named on a candidate sheet, by party id, with the language of the sheet that names it."""
    found: dict[str, tuple[str, str]] = {}
    groups = (
        ("fr", districts_2019()),
        ("fr", communal_2019("mayor")),
        ("fr", communal_2019("councillor")),
        ("mg", districts_2024()),
        ("mg", communal_2024()),
    )
    for lang, sheets in groups:
        for sheet in sheets:
            for candidate in sheet.candidates:
                if candidate.party and not independent(candidate.party):
                    found.setdefault(party_id(candidate.party), (candidate.party, lang))
    return found


def build_registry() -> Package:
    p = Package(
        "mg.ceni/registry",
        SOURCE,
        "Malagasy parties and political groupings, the National Assembly, the communal councils, the presidency "
        "and the mayoralty, and the sources, from the national election authority's result sheets",
    )
    p.external.add(MADAGASCAR)
    p.add(
        "source",
        id=CENI,
        kind="official_body",
        publisher="Commission Electorale Nationale Indépendante de Madagascar",
        title="Provisional election results published by the Commission Electorale Nationale Indépendante",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Commission Electorale Nationale Indépendante de Madagascar",
    )
    for url in [*FILES.values(), *LISTINGS.values(), *pdfs("communal2024")]:
        p.artifact(raw.cached(SOURCE, url), CENI)
    for scheme, kind, rank in (("mg.bv", "precinct", 4),):
        p.add("scheme", code=scheme, polity_id=MADAGASCAR, kind=kind, rank=rank, noun="polling station")
    p.add("body", id=ASSEMBLY, polity_id=MADAGASCAR, kind="lower_house")
    p.name(ASSEMBLY, "National Assembly of Madagascar", "en", "official")
    p.name(ASSEMBLY, "Antenimierandoholona", "mg", "native", False)
    p.add("body", id=COUNCIL, polity_id=MADAGASCAR, kind="council")
    p.name(COUNCIL, "Communal councils of Madagascar", "en", "official")
    p.name(COUNCIL, "Conseils communaux", "fr", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=MADAGASCAR, kind="head_of_state")
    p.name(PRESIDENCY, "President of Madagascar", "en", "official")
    p.name(PRESIDENCY, "Président de la République de Madagascar", "fr", "native", False)
    p.add("office", id=MAYOR, polity_id=MADAGASCAR, kind="mayor")
    p.name(MAYOR, "Mayor of a commune of Madagascar", "en", "official")
    p.name(MAYOR, "Maire", "fr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM_PRESIDENT,
        polity_id=MADAGASCAR,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_PRESIDENT),
        entity_id=SYSTEM_PRESIDENT,
        kind="methodology",
        body_md="A candidate with an absolute majority of valid votes in the first round is elected; otherwise the "
        "two leading candidates go to a second round.",
        lang="en",
    )
    for id, (label, lang) in sorted(party_labels().items(), key=lambda item: item[1][0].casefold()):
        p.add("party", id=id, kind="party", polity_id=MADAGASCAR)
        p.name(id, label, lang, "official")
    return p


METHOD = (
    "Counted per {grain} and published as provisional results by the national election authority. Registered voters "
    "are the voters on the roll plus those added to it at the polling station. Ballots cast are the voters who took "
    "part. {blanks}"
)
SEPARATE = "Blank and null ballots are counted apart."
COMBINED = "The sheet prints blank and null ballots as one figure, which is stored as invalid ballots."


@dataclass
class Station:
    """One counted place's accounting and its votes by ballot number, from whichever sheet prints it."""

    code: str
    name: str
    district: str
    commune: str
    registered: int | None
    cast: int | None
    blank: int | None
    invalid: int | None
    valid: int | None
    votes: dict[int, int]


def _station(row: read.Row, offset: int = 1) -> Station:
    return Station(
        row.code,
        row.name,
        row.district,
        row.commune,
        row.registered + row.added,
        row.voters,
        row.blank,
        row.invalid,
        row.valid,
        {index + offset: value for index, value in enumerate(row.votes)},
    )


def _count_station(count: read.Count, code: str = "", name: str = "") -> Station:
    registered = None if count.registered is None else count.registered + (count.added or 0)
    return Station(
        code or count.place.get("code", ""),
        name or count.place.get("name", ""),
        count.place["district"],
        count.place.get("commune", ""),
        registered,
        count.voters,
        count.blank,
        count.blank_invalid if count.blank_invalid is not None else count.invalid,
        count.valid,
        dict(count.votes),
    )


class Writer:
    """Adds one package's elections, contests, units, and values with ids that rebuild the same."""

    def __init__(self, p: Package) -> None:
        self.p = p
        self.slugs: set[str] = set()

    def slug(self, base: str) -> str:
        path, number = base, 2
        while path in self.slugs:
            path, number = f"{base}-{number}", number + 1
        self.slugs.add(path)
        return path

    def election(self, name: str, day: str, kind: str, english: str, french: str, path: str) -> str:
        election = key("election", name)
        moment = DAYS[day]
        self.p.add(
            "election", id=election, polity_id=MADAGASCAR, kind=kind, date_from=moment, date_to=moment, status="held"
        )
        self.p.add("slug", path=self.slug(path), entity_id=election, is_current=True)
        self.p.name(election, english, "en", "official")
        self.p.name(election, french, "fr", "native", False)
        return election

    def contest(
        self, name: str, election: str, kind: str, english: str, french: str, path: str, **fields: object
    ) -> str:
        contest = key("contest", name)
        row = {"round_number": 1, "default_result_kind": "counted", **fields}
        self.p.add("contest", id=contest, election_id=election, kind=kind, **row)
        self.p.name(contest, english, "en", "official")
        self.p.name(contest, french, "fr", "native", False)
        self.p.add("slug", path=self.slug(path), entity_id=contest, is_current=True)
        return contest

    def result_set(
        self, name: str, election: str, contest: str, grain: str, blanks: str, completeness: float, default: bool
    ) -> str:
        result_set = key("result_set", name)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="preliminary",
            kind="counted",
            source_id=CENI,
            status="active",
            is_default=default,
            completeness=round(completeness, 6),
        )
        self.p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(grain=grain, blanks=blanks),
            lang="en",
        )
        return result_set

    def unit(self, kind: str, day: str, code: str, scheme: str, text: str, area_id: str, relation: str) -> str:
        unit = key("unit", kind, day, code)
        self.p.add(
            "reporting_unit",
            id=unit,
            polity_id=MADAGASCAR,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation=relation,
            area_id=area_id,
            kind="standard",
        )
        if text:
            self.p.name(unit, text, "fr", "official")
        return unit

    def put(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add(
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

    def accounting(self, result_set: str, unit: str, s: Station) -> None:
        for measure, value in (
            (REGISTERED, s.registered),
            (CAST, s.cast),
            (VALID, s.valid),
            (INVALID, s.invalid),
            (BLANK, s.blank),
        ):
            self.put(result_set, unit, None, measure, value)

    def declared(self, result_set: str, options: dict[int, str], s: Station) -> None:
        for measure, value in (
            (REGISTERED, s.registered),
            (CAST, s.cast),
            (VALID, s.valid),
            (INVALID, s.invalid),
            (BLANK, s.blank),
        ):
            if value is not None:
                self.p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        for number, value in s.votes.items():
            self.p.add("declared", result_set_id=result_set, option_id=options[number], measure=VOTES, value=value)

    def candidate(
        self,
        scope: str,
        contest: str,
        number: int,
        name: str,
        party: str = "",
        people: str | None = None,
        label: str = "",
        kind: str = "candidate",
        build_person: bool = True,
        party_scope: str = "",
    ) -> str:
        """One ballot line: its option, and for a person's candidacy the person and their party or independence."""
        option = key("option", scope, number)
        is_independent = bool(party) and independent(party)
        self.p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind=kind,
            party_id=party_id(party) if party and not is_independent and kind == "list" else None,
            ballot_position=number,
            ballot_label=label or name,
        )
        if kind == "candidate":
            person = key("person", people or scope, number)
            if build_person:
                self.p.add("person", id=person)
                self.p.name(person, name, "fr", "official")
            self.p.add(
                "candidacy",
                id=key("candidacy", scope, number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party_id(party, party_scope) if party and not is_independent else None,
                is_independent=is_independent if party else None,
            )
        return option


def titled(text: str) -> str:
    return text.title() if text.isupper() else text


def nationwide(nat: read.Count, combine: bool = False) -> Station:
    registered = None if nat.registered is None else nat.registered + (nat.added or 0)
    invalid = nat.blank_invalid if nat.blank_invalid is not None else nat.invalid
    blank = nat.blank
    if combine and blank is not None:
        invalid, blank = (invalid or 0) + blank, None
    return Station("", "", "", "", registered, nat.voters, blank, invalid, nat.valid, dict(nat.votes))


def station_unit(w: Writer, g: Geo, day: str, s: Station) -> str:
    d = g.district_key(s.district)
    c = g.commune(d, s.commune)
    text = f"{s.name} ({titled(s.commune)}, {titled(s.district)})" if s.name else ""
    return w.unit("bv", day, s.code, "mg.bv", text, commune_area(d, c), "within")


def commune_unit(w: Writer, g: Geo, day: str, district: str, commune: str, cache: dict[tuple[str, str], str]) -> str:
    d = g.district_key(district)
    c = g.commune(d, commune)
    if (d, c) not in cache:
        cache[d, c] = w.unit(
            "commune",
            day,
            f"{d}/{c}",
            "mg.commune",
            f"{titled(commune)} ({titled(district)})",
            commune_area(d, c),
            "equals",
        )
    return cache[d, c]


def _fill(w: Writer, result_set: str, unit: str, s: Station, options: dict[int, str]) -> None:
    w.accounting(result_set, unit, s)
    for number, value in s.votes.items():
        w.put(result_set, unit, options[number], VOTES, value)


def _uses(p: Package, *names: str) -> None:
    for name in names:
        p.uses(raw.cached(SOURCE, FILES[name]))


def _president_2018(day: str, round_number: int) -> Package:
    runoff = round_number == 2
    p = Package(
        f"mg.ceni/{day}",
        SOURCE,
        f"The {DAYS[day].year} Malagasy presidential election, {'second' if runoff else 'first'} round, by commune "
        "and polling station, from the national election authority's provisional results",
        depends=["mg.ceni/registry", "mg.ceni/geo", *(["mg.ceni/2018-11-07"] if runoff else [])],
    )
    p.external.add(MADAGASCAR)
    g = geo()
    w = Writer(p)
    name = f"pres2018-{round_number}"
    _uses(p, name, f"{name}-national", f"{name}-commune")
    nat = read.national(path(f"{name}-national"))
    names = read.national(path("pres2018-1-national")).names
    election = w.election(
        name,
        day,
        "runoff" if runoff else "general",
        f"2018 Malagasy presidential election{', second round' if runoff else ''}",
        f"Élection présidentielle de 2018{', second tour' if runoff else ''}",
        f"mg/{day}-presidential",
    )
    contest = w.contest(
        name,
        election,
        "office",
        f"2018 Malagasy presidential election, {'second' if runoff else 'first'} round",
        f"Élection présidentielle de 2018, {'second' if runoff else 'premier'} tour",
        f"mg/{day}-presidential/president",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=round_number,
        previous_round_id=key("contest", "pres2018-1") if runoff else None,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    options = {
        number: w.candidate(name, contest, number, names[number], people="pres2018-1", build_person=not runoff)
        for number in sorted(nat.votes)
    }
    blanks = SEPARATE if nat.blank is not None else COMBINED
    total = nat.valid or 0
    units: dict[tuple[str, str], str] = {}
    sheets = communes_2018(round_number)
    commune_set = w.result_set(
        f"{name}-commune", election, contest, "commune", blanks if not runoff else COMBINED, 1.0, True
    )
    for count in sheets:
        unit = commune_unit(w, g, day, count.place["district"], count.place.get("commune", ""), units)
        _fill(w, commune_set, unit, _count_station(count), options)
    w.declared(commune_set, options, nationwide(nat, combine=runoff))
    if runoff:
        stations = [
            Station(
                row.code,
                row.name,
                row.district,
                row.commune,
                row.registered + row.added,
                row.voters,
                row.blank,
                row.invalid,
                row.valid,
                dict(zip(sorted(nat.votes), row.votes, strict=True)),
            )
            for row in stations_2018_2()
        ]
    else:
        stations = [_count_station(count) for count in stations_2018_1()]
    covered = sum(s.valid or 0 for s in stations)
    station_set = w.result_set(
        f"{name}-bv", election, contest, "polling station", SEPARATE if runoff else COMBINED, covered / total, False
    )
    for s in stations:
        _fill(w, station_set, station_unit(w, g, day, s), s, options)
    return p


def _district_names(day_sheets: list[read.Sheet], g: Geo) -> dict[str, read.Sheet]:
    found: dict[str, read.Sheet] = {}
    for sheet in day_sheets:
        d = g.district_key(sheet.place["district"])
        if d in found:
            raise ValueError(f"two sheets for district {d}")
        found[d] = sheet
    return found


def _legislative_2019(day: str) -> Package:
    p = Package(
        f"mg.ceni/{day}",
        SOURCE,
        "The 2019 Malagasy legislative election by district, from the national election authority's provisional results",
        depends=["mg.ceni/registry", "mg.ceni/geo"],
    )
    p.external.add(MADAGASCAR)
    g = geo()
    w = Writer(p)
    _uses(p, "leg2019")
    election = w.election(
        "leg2019",
        day,
        "general",
        "2019 Malagasy legislative election",
        "Élections législatives de 2019",
        f"mg/{day}-legislative",
    )
    for d, sheet in sorted(_district_names(districts_2019(), g).items()):
        text = titled(g.district_names[d])
        contest = w.contest(
            f"leg2019-{d}",
            election,
            "body_seats",
            f"2019 Malagasy legislative election, {text}",
            f"Élections législatives de 2019, district de {text}",
            f"mg/{day}-legislative/{slug(text)}",
            body_id=ASSEMBLY,
            district_area_id=district_area(d),
        )
        options = {c.number: w.candidate(f"leg2019-{d}", contest, c.number, c.name, c.party) for c in sheet.candidates}
        result_set = w.result_set(f"leg2019-{d}", election, contest, "district", SEPARATE, 1.0, True)
        unit = w.unit("district", day, d, "mg.district", f"{text} district", district_area(d), "equals")
        s = _count_station(sheet.count)
        s.votes = {c.number: c.votes for c in sheet.candidates}
        _fill(w, result_set, unit, s, options)
    return p


SENATE = key("body", "senate")
PROVINCE = "mg.province"


def senate_sheets() -> list[read.Sheet]:
    return [
        read.senate_sheet(raw.cached(SOURCE, url).path)
        for name, url in SENATE_FILES.items()
        if name not in SENATE_ABSENT
    ]


def _senate_2020(day: str) -> Package:
    p = Package(
        f"mg.ceni/{day}",
        SOURCE,
        "The 2020 Malagasy senatorial election by province, from the national election authority's provisional results",
        depends=["mg.ceni/registry"],
    )
    p.external.add(MADAGASCAR)
    w = Writer(p)
    for url in (SENATE_LISTING, *(SENATE_FILES[n] for n in SENATE_PROVINCES if n not in SENATE_ABSENT)):
        p.artifact(raw.cached(SOURCE, url), CENI)
    p.add("scheme", code=PROVINCE, polity_id=MADAGASCAR, kind="electoral_district", rank=1, noun="province")
    p.add("body", id=SENATE, polity_id=MADAGASCAR, kind="upper_house")
    p.name(SENATE, "Senate of Madagascar", "en", "official")
    p.name(SENATE, "Sénat de Madagascar", "fr", "native", False)
    election = w.election(
        "senate2020",
        day,
        "indirect",
        "2020 Malagasy senatorial election",
        "Élections sénatoriales de 2020",
        f"mg/{day}-senatorial",
    )
    parties: dict[str, str] = {}
    for sheet in senate_sheets():
        name = sheet.place["province"]
        text = titled(name)
        province = area(PROVINCE, norm(name))
        p.add("area", id=province, scheme_code=PROVINCE)
        p.name(province, text, "fr", "official")
        scope = f"senate2020-{norm(name)}"
        contest = w.contest(
            scope,
            election,
            "body_seats",
            f"2020 Malagasy senatorial election, {text} province",
            f"Élections sénatoriales de 2020, province de {text}",
            f"mg/{day}-senatorial/{slug(text)}",
            body_id=SENATE,
            district_area_id=province,
        )
        options = {}
        for cand in sheet.candidates:
            options[cand.number] = w.candidate(
                scope, contest, cand.number, cand.name, cand.party, party_scope="senate2020"
            )
            if cand.party and not independent(cand.party):
                parties[party_id(cand.party, "senate2020")] = cand.party
        result_set = w.result_set(scope, election, contest, "province", SEPARATE, 1.0, True)
        unit = w.unit("province", day, norm(name), PROVINCE, f"{text} province", province, "equals")
        c = sheet.count
        registered = None if c.registered is None else c.registered + (c.added or 0)
        s = Station("", "", "", "", registered, c.voters, c.blank, c.invalid, c.valid, dict(c.votes))
        _fill(w, result_set, unit, s, options)
    for id, label in sorted(parties.items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="party", polity_id=MADAGASCAR)
        p.name(id, label, "fr", "official")
    return p


def _president_2023(day: str) -> Package:
    p = Package(
        f"mg.ceni/{day}",
        SOURCE,
        "The 2023 Malagasy presidential election by polling station, from the national election authority's "
        "provisional results",
        depends=["mg.ceni/registry", "mg.ceni/geo"],
    )
    p.external.add(MADAGASCAR)
    g = geo()
    w = Writer(p)
    _uses(p, "pres2023", "pres2023-national")
    nat = read.national_2023(path("pres2023-national"))
    election = w.election(
        "pres2023",
        day,
        "general",
        "2023 Malagasy presidential election",
        "Élection présidentielle de 2023",
        f"mg/{day}-presidential",
    )
    contest = w.contest(
        "pres2023",
        election,
        "office",
        "2023 Malagasy presidential election",
        "Élection présidentielle de 2023",
        f"mg/{day}-presidential/president",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    options = {n: w.candidate("pres2023", contest, n, nat.names[n]) for n in sorted(nat.votes)}
    stations = [_station(row) for row in stations_2023()]
    covered = sum(s.valid or 0 for s in stations)
    result_set = w.result_set(
        "pres2023", election, contest, "polling station", SEPARATE, covered / (nat.valid or 1), True
    )
    for s in stations:
        _fill(w, result_set, station_unit(w, g, day, s), s, options)
    w.declared(result_set, options, nationwide(nat))
    return p


def _legislative_2024(day: str) -> Package:
    p = Package(
        f"mg.ceni/{day}",
        SOURCE,
        "The 2024 Malagasy legislative election by polling station, from the national election authority's "
        "provisional results",
        depends=["mg.ceni/registry", "mg.ceni/geo"],
    )
    p.external.add(MADAGASCAR)
    g = geo()
    w = Writer(p)
    _uses(p, "leg2024", "leg2024-districts")
    election = w.election(
        "leg2024",
        day,
        "general",
        "2024 Malagasy legislative election",
        "Élections législatives de 2024",
        f"mg/{day}-legislative",
    )
    sheets = _district_names(districts_2024(), g)
    by_district: dict[str, list[read.Row]] = defaultdict(list)
    for row in stations_2024():
        by_district[g.district_key(row.district)].append(row)
    if set(by_district) != set(sheets):
        raise ValueError(f"districts differ: {sorted(set(by_district) ^ set(sheets))}")
    for d in sorted(sheets):
        sheet = sheets[d]
        text = titled(g.district_names[d])
        contest = w.contest(
            f"leg2024-{d}",
            election,
            "body_seats",
            f"2024 Malagasy legislative election, {text}",
            f"Élections législatives de 2024, district de {text}",
            f"mg/{day}-legislative/{slug(text)}",
            body_id=ASSEMBLY,
            district_area_id=district_area(d),
        )
        options = {c.number: w.candidate(f"leg2024-{d}", contest, c.number, c.name, c.party) for c in sheet.candidates}
        order = [c.number for c in sheet.candidates]
        official = _count_station(sheet.count)
        official.votes = {c.number: c.votes for c in sheet.candidates}
        stations = [
            Station(
                r.code,
                r.name,
                r.district,
                r.commune,
                r.registered + r.added,
                r.voters,
                r.blank,
                r.invalid,
                r.valid,
                dict(zip(order, r.votes, strict=True)),
            )
            for r in by_district[d]
        ]
        covered = sum(s.valid or 0 for s in stations)
        result_set = w.result_set(
            f"leg2024-{d}", election, contest, "polling station", SEPARATE, covered / (official.valid or 1), True
        )
        for s in stations:
            _fill(w, result_set, station_unit(w, g, day, s), s, options)
        w.declared(result_set, options, official)
    return p


def _communal(day: str, sets: list[tuple[str, str, list[read.Sheet]]], year: int) -> Package:
    p = Package(
        f"mg.ceni/{day}",
        SOURCE,
        f"The {year} Malagasy communal elections by commune, from the national election authority's provisional results",
        depends=["mg.ceni/registry", "mg.ceni/geo"],
    )
    p.external.add(MADAGASCAR)
    g = geo()
    w = Writer(p)
    if year == 2024:
        for url in pdfs("communal2024"):
            p.uses(raw.cached(SOURCE, url))
    else:
        _uses(p, *(name for name, _, _ in sets))
    election = w.election(
        f"communal{year}",
        day,
        "local",
        f"{year} Malagasy communal elections",
        f"Élections communales de {year}",
        f"mg/{day}-communal",
    )
    units: dict[tuple[str, str], str] = {}
    for office, noun, sheets in sets:
        seen: set[tuple[str, str]] = set()
        for sheet in sheets:
            d = g.district_key(sheet.place["district"])
            c = g.commune(d, sheet.place["commune"])
            if (d, c) in seen:
                continue
            seen.add((d, c))
            text = f"{titled(sheet.place['commune'])} ({titled(sheet.place['district'])})"
            mayor = noun == "mayor"
            scope = f"{noun}{year}-{d}-{c}"
            contest = w.contest(
                scope,
                election,
                "office" if mayor else "body_seats",
                f"{year} Malagasy {noun} election, {text}",
                f"Élections communales de {year}, {'maire' if mayor else 'conseillers'}, {text}",
                f"mg/{day}-communal/{slug(d + '-' + c)}-{noun}",
                **({"office_id": MAYOR, "magnitude": 1} if mayor else {"body_id": COUNCIL}),
                district_area_id=commune_area(d, c),
            )
            options = {
                cand.number: w.candidate(
                    scope,
                    contest,
                    cand.number,
                    cand.name,
                    cand.party,
                    label=cand.name if mayor else f"{cand.name} ({cand.party})",
                    kind="candidate" if mayor else "list",
                )
                for cand in sheet.candidates
            }
            result_set = w.result_set(scope, election, contest, "commune", SEPARATE, 1.0, True)
            unit = commune_unit(w, g, day, sheet.place["district"], sheet.place["commune"], units)
            s = _count_station(sheet.count)
            s.votes = {cand.number: cand.votes for cand in sheet.candidates}
            _fill(w, result_set, unit, s, options)
    return p


def packages() -> list[str]:
    return ["mg.ceni/registry", "mg.ceni/geo", *(f"mg.ceni/{day}" for day in DAYS)]


ACCEPT: dict[str, dict[str, str]] = {
    "2018-11-07": {
        "accounting.ballots_sum": "one polling station prints 366 voters but 258 valid and 8 blank-or-null ballots; stored as printed",
        "accounting.turnout": "one polling station prints 366 voters against 336 registered voters; stored as printed",
    },
    "2018-12-19": {
        "accounting.ballots_sum": "466 commune sheets print a blank-and-null figure that differs from voters minus valid votes "
        "(143,981 in all against 119,611 by subtraction); stored as printed",
        "accounting.declared": "the national sheet prints 41,146 blank and 78,465 null ballots (119,611 together) while the "
        "commune sheets' blank-and-null figures add up to 143,981; stored as printed",
        "accounting.turnout": "three polling stations print more voters than registered voters; stored as printed",
    },
    "2019-11-27": {
        "accounting.votes_valid": "two mayoral sheets (Analamary and Toliara I) print a valid-vote count that differs from "
        "the sum of their own candidate lines (1,520 against 1,473 and 17,856 against 17,712); stored as printed",
        "accounting.ballots_sum": "one commune sheet prints 970 voters against 950 valid, 6 blank, and 13 null ballots; stored as printed",
    },
    "2023-11-16": {
        "accounting.turnout": "202 polling stations print more voters than registered voters; stored as printed",
        "accounting.declared": "the national sheet prints 11,043,239 registered voters while the polling-station rows add up to "
        "11,041,643; the sheet counts 27,372 of 27,375 stations and the file lists 27,371; stored as printed",
    },
    "2024-05-29": {
        "accounting.turnout": "20 polling stations print more voters than registered voters; stored as printed",
        "accounting.declared": "two districts print a registered-voter total that differs from the sum of their polling-station "
        "rows (166,915 against 166,987 and 125,291 against 125,389); stored as printed",
    },
    "2024-12-11": {
        "accounting.votes_valid": "24 mayoral sheets print a valid-vote count that differs by a few votes from the sum of their own "
        "candidate lines; stored as printed",
        "accounting.ballots_sum": "78 commune sheets print blank and null ballots that do not add up with valid votes to the voters; "
        "stored as printed",
    },
}


def build(day: str) -> Package:
    p = _build(day)
    for code, reason in ACCEPT.get(day, {}).items():
        p.accept(code, reason)
    return p


def _build(day: str) -> Package:
    if day == "2018-11-07":
        return _president_2018(day, 1)
    if day == "2018-12-19":
        return _president_2018(day, 2)
    if day == "2019-05-27":
        return _legislative_2019(day)
    if day == "2019-11-27":
        sets = [
            ("mayor2019", "mayor", communal_2019("mayor")),
            ("councillor2019", "councillor", communal_2019("councillor")),
        ]
        return _communal(day, sets, 2019)
    if day == "2020-12-11":
        return _senate_2020(day)
    if day == "2023-11-16":
        return _president_2023(day)
    if day == "2024-05-29":
        return _legislative_2024(day)
    return _communal(day, [("mayor2024", "mayor", communal_2024())], 2024)


def parse() -> None:
    build_geo().write()
    build_registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())
