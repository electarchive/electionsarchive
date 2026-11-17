"""Madagascar's Senate elections of 2015 and 2020: the Haute Cour Constitutionnelle's proclamations of the official
results, by province, with the 2015 arrêt's polling-station annexes."""

import html
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from uuid import uuid5

import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.mg import read

SOURCE = "mg.hcc_senat"
SITE = "https://www.hcc.gov.mg"
UPLOADS = f"{SITE}/wp-content/uploads"
PAGES = {
    "2008": f"{SITE}/?p=541",
    "2015": f"{SITE}/?p=2096",
    "2020": f"{SITE}/?p=6480",
}
# The 2008 arrêt's page prints no table under its article that proclaims the results, so no election is built from it.
BV_2015 = {
    1: f"{UPLOADS}/2016/01/TanaBV.pdf",
    2: f"{UPLOADS}/2016/01/DiegoBV.pdf",
    3: f"{UPLOADS}/2016/01/FianaraBV.pdf",
    4: f"{UPLOADS}/2016/01/MajungaBV.pdf",
    5: f"{UPLOADS}/2016/01/ToamasinaBV.pdf",
    6: f"{UPLOADS}/2016/01/TulearBV.pdf",
}
RESULT_2020 = {
    1: f"{UPLOADS}/2021/01/Resultat-Tana.pdf",
    2: f"{UPLOADS}/2021/01/Resultat-Diego.pdf",
    3: f"{UPLOADS}/2021/01/Resultat-Fianarantsoa.pdf",
    4: f"{UPLOADS}/2021/01/Resultat-Mahajanga.pdf",
    5: f"{UPLOADS}/2021/01/Resultat-Tamatave.pdf",
    6: f"{UPLOADS}/2021/01/Resultat-Toliara.pdf",
}
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/MDG/ADM1"
GB_REGIONS = f"{GEOBOUNDARIES}/geoBoundaries-MDG-ADM1.geojson"
GB_META = f"{GEOBOUNDARIES}/geoBoundaries-MDG-ADM1-metaData.json"

MADAGASCAR = str(uuid5(package.NAMESPACE, "polity\x1fMG"))
PROVINCE, REGION, DISTRICT, BV = "mg.province", "mg.region", "mg.district", "mg.bv"
VOTES, REGISTERED, CAST, VALID, INVALID, QUOTA = 1, 2, 3, 4, 5, 26
DAYS = {2015: date(2015, 12, 29), 2020: date(2020, 12, 11)}
LAW = date(2015, 3, 3)
BOUNDARIES_FROM = date(2015, 1, 1)
PROVINCES = {
    1: ("Antananarivo", "antananarivo"),
    2: ("Antsiranana", "antsiranana"),
    3: ("Fianarantsoa", "fianarantsoa"),
    4: ("Mahajanga", "mahajanga"),
    5: ("Toamasina", "toamasina"),
    6: ("Toliara", "toliara"),
}
# The region a geoBoundaries shape draws, by the region number the 2015 annexes print; its first digit is the province.
REGIONS = {
    "Analamanga": 11,
    "Bongolava": 12,
    "Itasy": 13,
    "Vakinankaratra": 14,
    "Diana": 21,
    "Sava": 22,
    "Amoron'i Mania": 31,
    "Atsimo-Atsinanana": 32,
    "Matsiatra Ambony": 33,
    "Ihorombe": 34,
    "Vatovavy-Fitovinany": 35,
    "Betsiboka": 41,
    "Boeny": 42,
    "Melaky": 43,
    "Sofia": 44,
    "Alaotra-Mangoro": 51,
    "Analanjirofo": 52,
    "Atsinanana": 53,
    "Androy": 61,
    "Anosy": 62,
    "Atsimo-Andrefana": 63,
    "Menabe": 64,
}
# A district name the annex truncates where its line meets the next column.
DISTRICT_NAMES = {"ANTANANARIVO-ATSIMONDRAN": "ANTANANARIVO-ATSIMONDRANO"}


def key(kind: str, *parts: object) -> str:
    return IDS.id(kind, *parts)


IDS = Package(f"{SOURCE}/ids", SOURCE, "Identifier minting")
HCC = key("source", "hcc")
SENATE = key("body", "senate")
SYSTEM = key("electoral_system", "senate", 2015)


def norm(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", plain)


def titled(text: str) -> str:
    return text.title() if text.isupper() else text


def independent(label: str) -> bool:
    return re.match(r"IND(?:\b|EP)", norm_words(label)) is not None


def norm_words(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9 ]", "", plain).strip()


def party_id(label: str) -> str:
    return key("party", norm(label))


def fetch() -> None:
    for url in PAGES.values():
        raw.fetch(SOURCE, url, suffix=".html")
    for url in (*BV_2015.values(), *RESULT_2020.values()):
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, GB_REGIONS, suffix=".geojson")
    raw.fetch(SOURCE, GB_META, suffix=".json")


@dataclass
class Row:
    """One list's printed result in a province: its votes and the seats the quotient and the highest average gave."""

    label: str
    votes: int
    by_quota: int
    by_average: int
    seats: int


@dataclass
class Elected:
    name: str
    position: int
    label: str


@dataclass
class Province:
    number: int
    name: str
    received: int
    stations: int
    registered: int
    cast: int
    invalid: int
    valid: int
    quota: Decimal
    rows: list[Row] = field(default_factory=list)
    elected: list[Elected] = field(default_factory=list)

    @property
    def seats(self) -> int:
        return len(self.elected)

    def check(self) -> None:
        """The province's printed figures must agree with each other and with the seats it declares."""
        where = f"province {self.number} {self.name}"
        if self.cast - self.invalid != self.valid:
            raise ValueError(f"{where}: voters less blank and null ballots is not the valid votes")
        if sum(r.votes for r in self.rows) != self.valid:
            raise ValueError(f"{where}: list votes do not add up to the valid votes")
        if sum(r.seats for r in self.rows) != self.seats:
            raise ValueError(f"{where}: seats won do not add up to the senators elected")
        for r in self.rows:
            if r.by_quota + r.by_average != r.seats:
                raise ValueError(f"{where}: {r.label} seats do not add up")
            if sum(1 for e in self.elected if e.label == r.label) != r.seats:
                raise ValueError(f"{where}: {r.label} has a different number of senators from its seats")
        if abs(Decimal(self.valid) / self.seats - self.quota) > Decimal("0.01"):
            raise ValueError(f"{where}: the quotient is not the valid votes over the seats")


def whole(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def decimal(text: str) -> Decimal:
    return Decimal(re.sub(r"[^\d,]", "", text).replace(",", "."))


def cells(table: str) -> list[list[str]]:
    rows = re.findall(r"(?is)<tr.*?</tr>", table)
    return [
        [" ".join(html.unescape(re.sub(r"<[^>]+>", "", c)).split()) for c in re.findall(r"(?is)<t[dh].*?</t[dh]>", r)]
        for r in rows
    ]


def label_of(text: str) -> str:
    """A list's label as printed, with the colon after an independent's mark spaced evenly and bare when nothing follows."""
    return re.sub(r"\s*:\s*", " : ", " ".join(text.split())).strip(" :")


@cache
def provinces_2015() -> list[Province]:
    body = raw.cached(SOURCE, PAGES["2015"]).path.read_text(errors="replace")
    body = body[body.index("Sont proclamés officiellement") :]
    found: list[Province] = []
    pieces = re.split(r"(?is)(<table.*?</table>)", body)
    for piece in pieces:
        if piece.lower().startswith("<table"):
            rows = cells(piece)
            if rows[0][0] == "LISTE" and found:
                for label, votes, _, quota, average, total in (r[:6] for r in rows[1:]):
                    found[-1].rows.append(
                        Row(label_of(label), whole(votes), whole(quota), whole(average), whole(total))
                    )
            elif rows[0][0].startswith("Nom"):
                for name, position, label in (r[:3] for r in rows[1:]):
                    found[-1].elected.append(Elected(name, whole(position), label_of(label)))
            continue
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", piece)).split())
        m = re.search(
            r"FARITANY\s*:\s*(\d)\s+(.+?)\s+BV RECUS\s*:\s*(\d+)\s+SUR\s*:\s*(\d+)\s+Inscrits\s+([\d ]+?)\s+Votants\s+"
            r"([\d ]+?)\s+Blancs et nuls\s+([\d ]+?)\s+Suffrages exprimés\s*:\s*([\d ]+?)\s+QE\s*:\s*([\d ,]+?)\s+Taux",
            text,
        )
        if m:
            found.append(
                Province(
                    int(m[1]),
                    titled(m[2]),
                    whole(m[3]),
                    whole(m[4]),
                    whole(m[5]),
                    whole(m[6]),
                    whole(m[7]),
                    whole(m[8]),
                    decimal(m[9]),
                )
            )
    if [p.number for p in found] != list(PROVINCES):
        raise ValueError("the 2015 arrêt does not print six provinces")
    for p in found:
        p.check()
    return found


@dataclass
class Station:
    region: int
    district: int
    number: int
    name: str
    partial: int
    total: int
    registered: int
    cast: int
    invalid: int
    valid: int
    votes: list[int]


@dataclass
class Annex:
    regions: dict[int, str] = field(default_factory=dict)
    districts: dict[int, str] = field(default_factory=dict)
    stations: list[Station] = field(default_factory=list)


@cache
def annex_2015(number: int) -> Annex:
    """One province's polling-station annex. Each line is a station's number, its name, then six accounting figures and
    one vote count per list, so the figures are read from the right and what is left is the name."""
    lists = len(provinces_2015()[number - 1].rows)
    out = Annex()
    region = district = 0
    totals: dict[int, list[int]] = {}
    for page in read.pages(raw.cached(SOURCE, BV_2015[number]).path):
        for line in page.splitlines():
            if m := re.match(r"^\s*REGION\s+(\d+)\s+(.+?)\s{2,}BV re", line):
                region = int(m[1])
                out.regions[region] = m[2].strip()
            elif m := re.match(r"^\s*DISTRICT\s*:\s*(\d+)\s+(.+?)\s+BV re", line):
                district = int(m[1])
                out.districts[district] = DISTRICT_NAMES.get(m[2].strip(), m[2].strip())
            elif m := re.match(r"^\s*TOTAL DISTRICT\s+(.*)$", line):
                totals[district] = [int(x) for x in m[1].split()]
            elif (m := re.match(r"^\s*(\d{1,3})\s+(\S.*)$", line)) and district:
                tokens = m[2].split()
                figures: list[int] = []
                while tokens and len(figures) < lists + 6 and re.fullmatch(r"\d+", tokens[-1]):
                    figures.insert(0, int(tokens.pop()))
                if len(figures) != lists + 6 or not tokens:
                    raise ValueError(f"{BV_2015[number]}: cannot read the station line {line.strip()!r}")
                ap, at, registered, cast, invalid, valid, *votes = figures
                if invalid + valid != cast or sum(votes) != valid:
                    raise ValueError(f"{BV_2015[number]}: station {district}-{m[1]} does not add up")
                out.stations.append(
                    Station(
                        region, district, int(m[1]), " ".join(tokens), ap, at, registered, cast, invalid, valid, votes
                    )
                )
    by_district: dict[int, list[int]] = defaultdict(lambda: [0] * (lists + 6))
    for s in out.stations:
        for i, x in enumerate([s.partial, s.total, s.registered, s.cast, s.invalid, s.valid, *s.votes]):
            by_district[s.district][i] += x
    for d, printed in totals.items():
        if by_district[d] != printed:
            raise ValueError(f"{BV_2015[number]}: district {d} stations do not add up to the printed total")
    province = provinces_2015()[number - 1]
    summed = [sum(by_district[d][i] for d in by_district) for i in range(lists + 6)]
    printed_province = [
        province.registered,
        province.cast,
        province.invalid,
        province.valid,
        *(r.votes for r in province.rows),
    ]
    if summed[2:] != printed_province:
        raise ValueError(f"{BV_2015[number]}: the stations do not add up to the province's proclaimed totals")
    return out


@cache
def province_2020(number: int) -> Province:
    text = "\n".join(read.pages(raw.cached(SOURCE, RESULT_2020[number]).path))
    head = re.search(r"FARITANY\s+(\d)\s+(\S+(?: \S+)*?)\s*\n", text)
    received = re.search(r"BV RECUS\s*:\s*(\d+)\s+sur\s*:\s*(\d+)", text)

    def figure(label: str) -> str:
        m = re.search(rf"{label}\s*:\s*([\d ,]+?)(?:\s{{2,}}|\n)", text)
        if not m:
            raise ValueError(f"{RESULT_2020[number]}: no {label}")
        return m[1]

    quota = re.search(r"QE\s*:\s*([\d ,]+)", text)
    if not (head and received and quota) or int(head[1]) != number:
        raise ValueError(f"{RESULT_2020[number]}: the sheet's heading is not as expected")
    province = Province(
        number,
        titled(head[2]),
        int(received[1]),
        int(received[2]),
        whole(figure("Inscrits")),
        whole(figure("Votants")),
        whole(figure("Blancs et nuls")),
        whole(figure("Suffrages exprimés")),
        decimal(quota[1]),
    )
    start = text.index("Tot sièges")
    end = text.index("Total des voix")
    for line in text[start:end].splitlines()[1:]:
        m = re.match(r"^\s*(\S.*?)\s{2,}([\d ]+?)\s+[\d,]+%\s+(\d+)\s+(\d+)\s+(\d+)\s*$", line)
        if m:
            province.rows.append(Row(label_of(m[1]), whole(m[2]), int(m[3]), int(m[4]), int(m[5])))
    for line in text[end:].splitlines():
        m = re.match(r"^\s+(\S.*?)\s{2,}(\d+)\s{2,}(\S.*?)\s*$", line)
        if m and not m[1].startswith("Nom"):
            province.elected.append(Elected(m[1], int(m[2]), label_of(m[3])))
    declared = re.search(r"LISTE DES (\d+) SENATEURS ELUS", text)
    if not declared or int(declared[1]) != province.seats:
        raise ValueError(f"{RESULT_2020[number]}: the elected senators are not the number the sheet announces")
    province.check()
    return province


@cache
def shapes() -> dict[int, shapely.Geometry]:
    """Each province drawn as the union of its regions, from the regions' open boundaries."""
    parts: dict[int, list[shapely.Geometry]] = defaultdict(list)
    for feature in json.loads(raw.cached(SOURCE, GB_REGIONS).path.read_bytes())["features"]:
        number = REGIONS[feature["properties"]["shapeName"]]
        parts[number // 10].append(shapely.make_valid(shapely.geometry.shape(feature["geometry"])))
    out: dict[int, shapely.Geometry] = {}
    for number, pieces in parts.items():
        merged = shapely.union_all(pieces)
        polygons = [
            q
            for part in shapely.get_parts(merged)
            if shapely.get_type_id(part) in (3, 6)
            for q in shapely.get_parts(part)
        ]
        out[number] = shapely.set_srid(shapely.MultiPolygon(polygons), 4326)
    return out


def province_area(number: int) -> str:
    return key("area", PROVINCE, number)


def build_geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Madagascar's six provinces, the constituencies of the Senate elections of 2015 and 2020, drawn from the "
        "regions' open boundaries",
    )
    p.external.update({MADAGASCAR, PROVINCE})
    source = key("source", "geoboundaries-adm1")
    p.add(
        "source",
        id=source,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
        title="geoBoundaries gbOpen: Madagascar ADM1 (regions)",
        url="https://www.geoboundaries.org/",
        license="Open Data Commons Open Database License 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries; © OpenStreetMap contributors",
        notes="Boundary year 2017, built December 2023. Each province is the union of its regions' shapes.",
    )
    p.artifact(raw.cached(SOURCE, GB_REGIONS), source)
    p.artifact(raw.cached(SOURCE, GB_META), source)
    for number, shape in sorted(shapes().items()):
        area = province_area(number)
        p.add("area", id=area, scheme_code=PROVINCE)
        p.name(area, PROVINCES[number][0], "fr", "official")
        p.add(
            "area_geometry",
            area_id=area,
            valid_from=BOUNDARIES_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=source,
        )
    return p


def build_registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Senate of Madagascar, its electoral system, the parties and lists on the provincial ballots of 2015 and "
        "2020, and the Haute Cour Constitutionnelle as a source",
    )
    p.external.add(MADAGASCAR)
    p.add(
        "source",
        id=HCC,
        kind="official_body",
        publisher="Haute Cour Constitutionnelle de Madagascar",
        title="Arrêts proclaiming the official results of the Senate elections",
        url=f"{SITE}/",
        license="Official publication; reuse terms are not stated",
        attribution="Haute Cour Constitutionnelle de Madagascar",
    )
    p.add("body", id=SENATE, polity_id=MADAGASCAR, kind="upper_house")
    p.name(SENATE, "Senate of Madagascar", "en", "official")
    p.name(SENATE, "Sénat de Madagascar", "fr", "native", False)
    p.name(SENATE, "Antenimierandoholona", "mg", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MADAGASCAR,
        valid_from=LAW,
        rules={"formula": "dhondt"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Mayors and municipal and communal councillors elect two thirds of the Senate by list, with the same "
        "number of senators for each province. Seats go first to each list for every whole electoral quotient "
        "(valid votes divided by the province's seats) and then, one at a time, to the list with the highest average "
        "of votes per seat already held plus one, which gives the seats the highest-average method of d'Hondt gives. "
        "The President appoints the remaining third.",
        lang="en",
    )
    labels: dict[str, str] = {}
    for province in (*provinces_2015(), *(province_2020(n) for n in PROVINCES)):
        for row in province.rows:
            if not independent(row.label):
                labels.setdefault(party_id(row.label), row.label)
    for id, label in sorted(labels.items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="party", polity_id=MADAGASCAR)
        p.name(id, label, "fr", "official")
    return p


METHOD = (
    "Proclaimed by the Haute Cour Constitutionnelle after it checked the polling-station records and ruled on the "
    "petitions. {grain} Registered voters are the electors on the roll, ballots cast are the voters, and invalid "
    "ballots are the blank and null ballots, which the Court prints as one figure. The quotient is the valid votes "
    "divided by the province's seats."
)
PROVINCE_GRAIN = "These are the province's totals."
STATION_GRAIN = (
    "These are the polling stations' counts, annexed to the arrêt. A polling station's votes that the Court annulled, "
    "in whole or in part, are counted among its blank and null ballots; each station's annulled votes are printed "
    "in the annex but not stored here."
)


class Writer:
    def __init__(self, p: Package, year: int) -> None:
        self.p = p
        self.year = year
        self.day = DAYS[year]

    def put(self, result_set: str, unit: str, option: str | None, measure: int, value: int | Decimal) -> None:
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

    def unit(
        self, kind: str, scheme: str, code: object, text: str, area: str, relation: str, parent: str | None
    ) -> str:
        unit = key("unit", self.year, scheme, code)
        self.p.add(
            "reporting_unit",
            id=unit,
            polity_id=MADAGASCAR,
            scheme_code=scheme,
            code=f"{self.day.isoformat()}:{code}",
            parent_id=parent,
            area_relation=relation,
            area_id=area,
            kind=kind,
        )
        if text:
            self.p.name(unit, text, "fr", "official")
        return unit

    def result_set(self, name: str, election: str, contest: str, grain: str, default: bool, completeness: float) -> str:
        result_set = key("result_set", self.year, name)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=HCC,
            status="active",
            is_default=default,
            completeness=round(completeness, 6),
        )
        self.p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(grain=grain),
            lang="en",
        )
        return result_set


def _election(p: Package, year: int, urls: list[str]) -> tuple[Writer, str]:
    w = Writer(p, year)
    for url in urls:
        p.artifact(raw.cached(SOURCE, url), HCC)
    p.external.add(MADAGASCAR)
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=MADAGASCAR,
        kind="indirect",
        date_from=w.day,
        date_to=w.day,
        status="held",
    )
    path = f"mg/{w.day.isoformat()}-senate"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Malagasy Senate election", "en", "official")
    p.name(election, f"Élections sénatoriales de {year}", "fr", "native", False)
    return w, election


def _contest(w: Writer, election: str, province: Province) -> tuple[str, dict[str, str]]:
    p, year = w.p, w.year
    name, slug = PROVINCES[province.number]
    contest = key("contest", year, province.number)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=SENATE,
        district_area_id=province_area(province.number),
        magnitude=province.seats,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Malagasy Senate election, {name} province", "en", "official")
    p.name(contest, f"Élections sénatoriales de {year}, province de {name}", "fr", "native", False)
    p.add("slug", path=f"mg/{w.day.isoformat()}-senate/{slug}", entity_id=contest, is_current=True)
    options: dict[str, str] = {}
    for position, row in enumerate(province.rows, 1):
        option = options[row.label] = key("option", year, province.number, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=None if independent(row.label) else party_id(row.label),
            ballot_position=position,
            ballot_label=row.label,
        )
    return contest, options


def _outcomes(w: Writer, province: Province, result_set: str, options: dict[str, str]) -> None:
    p = w.p
    for row in province.rows:
        if row.seats:
            p.add("outcome", result_set_id=result_set, option_id=options[row.label], kind="won", seats=row.seats)
    for e in province.elected:
        tag = f"{w.year}:{province.number}:{norm(e.label)}:{e.position}"
        person = key("person", tag)
        p.add("person", id=person, birth_precision=None)
        p.name(person, e.name, "fr", "official")
        candidacy = key("candidacy", tag)
        free = independent(e.label)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[e.label],
            role="list_member",
            list_position=e.position,
            ballot_name=e.name,
            member_of_party_id=None if free else party_id(e.label),
            is_independent=free,
        )
        p.add("outcome", result_set_id=result_set, option_id=options[e.label], candidacy_id=candidacy, kind="elected")


def _province_totals(w: Writer, result_set: str, unit: str, province: Province, options: dict[str, str]) -> None:
    for measure, value in (
        (REGISTERED, province.registered),
        (CAST, province.cast),
        (VALID, province.valid),
        (INVALID, province.invalid),
    ):
        w.put(result_set, unit, None, measure, value)
    w.put(result_set, unit, None, QUOTA, province.quota)
    for row in province.rows:
        w.put(result_set, unit, options[row.label], VOTES, row.votes)


def _senate_2015() -> Package:
    p = Package(
        f"{SOURCE}/2015-12-29",
        SOURCE,
        "The 2015 Malagasy Senate election by province and by polling station, from the Haute Cour Constitutionnelle's "
        "proclamation of the official results",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    w, election = _election(p, 2015, [PAGES["2015"], *BV_2015.values()])
    p.external.update({PROVINCE, REGION, DISTRICT, BV})
    for province in provinces_2015():
        name = PROVINCES[province.number][0]
        area = province_area(province.number)
        contest, options = _contest(w, election, province)
        official = w.result_set(f"{province.number}:province", election, contest, PROVINCE_GRAIN, True, 1.0)
        unit = w.unit("standard", PROVINCE, province.number, f"{name} province", area, "equals", None)
        _province_totals(w, official, unit, province, options)
        _outcomes(w, province, official, options)
        annex = annex_2015(province.number)
        valid = sum(s.valid for s in annex.stations)
        finer = w.result_set(
            f"{province.number}:station", election, contest, STATION_GRAIN, False, valid / province.valid
        )
        regions = {
            r: w.unit("aggregate", REGION, r, titled(label), area, "within", unit) for r, label in annex.regions.items()
        }
        districts: dict[int, str] = {}
        for d, label in annex.districts.items():
            r = next(s.region for s in annex.stations if s.district == d)
            districts[d] = w.unit("aggregate", DISTRICT, d, titled(label), area, "within", regions[r])
        for s in annex.stations:
            station = w.unit(
                "standard",
                BV,
                f"{s.district}-{s.number:02d}",
                f"{titled(s.name)} ({titled(annex.districts[s.district])})",
                area,
                "within",
                districts[s.district],
            )
            for measure, value in ((REGISTERED, s.registered), (CAST, s.cast), (VALID, s.valid), (INVALID, s.invalid)):
                w.put(finer, station, None, measure, value)
            for row, votes in zip(province.rows, s.votes, strict=True):
                w.put(finer, station, options[row.label], VOTES, votes)
        for measure, value in (
            (REGISTERED, province.registered),
            (CAST, province.cast),
            (VALID, province.valid),
            (INVALID, province.invalid),
        ):
            p.add("declared", result_set_id=finer, option_id=None, measure=measure, value=value)
        for row in province.rows:
            p.add("declared", result_set_id=finer, option_id=options[row.label], measure=VOTES, value=row.votes)
    return p


def _senate_2020() -> Package:
    p = Package(
        f"{SOURCE}/2020-12-11",
        SOURCE,
        "The 2020 Malagasy Senate election by province, from the Haute Cour Constitutionnelle's proclamation of the "
        "official results",
        depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
    )
    w, election = _election(p, 2020, [PAGES["2020"], *RESULT_2020.values()])
    p.external.add(PROVINCE)
    for number in PROVINCES:
        province = province_2020(number)
        name = PROVINCES[number][0]
        contest, options = _contest(w, election, province)
        official = w.result_set(f"{number}:province", election, contest, PROVINCE_GRAIN, True, 1.0)
        unit = w.unit("standard", PROVINCE, number, f"{name} province", province_area(number), "equals", None)
        _province_totals(w, official, unit, province, options)
        _outcomes(w, province, official, options)
    return p


ACCEPT: dict[str, dict[str, str]] = {}


def build(day: str) -> Package:
    p = _senate_2015() if day == "2015-12-29" else _senate_2020()
    for code, reason in ACCEPT.get(day, {}).items():
        p.accept(code, reason)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{d.isoformat()}" for d in DAYS.values())]


def parse() -> None:
    build_geo().write()
    build_registry().write()
    for day in DAYS.values():
        build(day.isoformat()).write()


def emit() -> None:
    package.run("load", packages())
