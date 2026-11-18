"""Moldova's general local elections of 14 June 2015 and 20 October 2019, from the lists of elected mayors and
councillors the Central Electoral Commission publishes: the 2015 local, district, and municipal councils and the 2019
mayors. The lists name the winners only, so the packages hold declared outcomes and no votes."""

import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

import fastexcel
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "md.cec"
MOLDOVA = str(uuid5(package.NAMESPACE, "polity\x1fMD"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMDA"))
GEO = "md.cec/local-geo"
REGISTRY = "md.cec/local-registry"
OLD = "https://a.cec.md/storage/old_site_files/files/files/Alegeri%20locale%202015"
COUNCILLORS_2015 = f"{OLD}/Lista%20Consilieri%20Locali%20ALG%202015.xlsx"
COUNCILS_2015 = f"{OLD}/C2_Lista%20consilierilor%20alesi%20in%20consiliile%20muncipale_raionale%20(2).docx"
MAYORS_2019 = "http://a.cec.md/storage/ckfinder/files/lista%20primarilor%20ale%C8%99i(1).xlsx"
SHAPES = (
    "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/MDA/ADM1/"
    "geoBoundaries-MDA-ADM1.geojson"
)
PAGES = {
    "2015-06-14": "https://a.cec.md/index.php/ro/rezultatele-alegerilor-19352.html",
    "2019-10-20": "https://a.cec.md/index.php/ro/lista-primarilor-alesi-6518.html",
}
FILES = {
    "2015-06-14": (COUNCILLORS_2015, COUNCILS_2015),
    "2019-10-20": (MAYORS_2019,),
}
DAYS = tuple(FILES)
ABBREVIATIONS = {"BEPPEM": "BPPE", "BELP": "BLP"}
SEATS = 16
SHAPES_ID = str(uuid5(package.NAMESPACE, "md.cec\x1fsource\x1fgeoboundaries.mda.adm1"))
CEDILLA = str.maketrans("şŞţŢ", "șȘțȚ")
UNDONE = str.maketrans("șȘțȚ", "şŞţŢ")
PREFIX = re.compile(r"^(r\.|mun\.|municipiul|sat|comuna|oraș|or\.|s\.|com\.)\s+", re.IGNORECASE)
KINDS = {"s.": "sat", "sat": "sat", "com.": "comuna", "comuna": "comuna", "or.": "oras", "oraș": "oras"}
LABELS = {"oras": "town", "sat": "village", "comuna": "commune"}
# Localities where a town and a village or commune share a name within one district. The 2015 list marks one of
# the two with a prefix and leaves the other bare; the bare one's type is known here. Cornești in Ungheni is bare
# for both, so its two councils cannot be told apart and are left out.
AMBIGUOUS = {
    ("donduseni", "donduseni"): "oras",
    ("drochia", "drochia"): "oras",
    ("ocnita", "ocnita"): "oras",
    ("floresti", "ghindesti"): "comuna",
    ("floresti", "marculesti"): "sat",
    ("ungheni", "cornesti"): None,
}
INDEPENDENT = "Candidat independent"
METHOD = (
    "The Central Electoral Commission lists only the candidates it declared elected. Votes, turnout, and the "
    "candidates who lost are not in the list, so this result set holds the declared winners alone."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def fix(text: object) -> str:
    return clean(text).translate(CEDILLA)


def fold(text: object) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", fix(text)) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def place(text: object) -> str:
    return PREFIX.sub("", fix(text))


def title(text: object) -> str:
    return re.sub(r"\s*-\s*", "-", fix(text)).title()


def slug(text: str) -> str:
    return fold(text).replace(" ", "-")


def district_key(name: object) -> str:
    text = fold(place(name))
    return text.removeprefix("uta ")


def district_name(name: object) -> str:
    text = place(name)
    return "UTA Găgăuzia" if fold(text).startswith("uta ") else title(text)


def sheet(url: str) -> list[list[str | None]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    return [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]


class Elected:
    """A winner the Commission lists: where, for which party, and as which person."""

    def __init__(self, district: str, locality: str | None, name: str, party: str | None, year: int = 0) -> None:
        self.year = year
        self.district = district  # as the source prints it
        self.locality = locality
        self.name = name
        self.party = party  # a full party name or abbreviation, or None for an independent

    @property
    def district_key(self) -> str:
        return district_key(self.district)

    @property
    def locality_key(self) -> str | None:
        if not self.locality:
            return None
        base = fold(place(self.locality))
        if (self.district_key, base) not in AMBIGUOUS:
            return base
        marked = PREFIX.match(fix(self.locality))
        kind = (
            KINDS[marked.group(1).lower()]
            if marked
            else (AMBIGUOUS[(self.district_key, base)] if self.year == 2015 else None)
        )
        return f"{base} {kind}" if kind else None

    @property
    def locality_name(self) -> str:
        text = title(place(self.locality))
        key = self.locality_key or ""
        return f"{text} ({LABELS[key.rsplit(' ', 1)[1]]})" if key != fold(text) and " " in key else text


def fetch() -> None:
    for urls in FILES.values():
        for url in urls:
            raw.fetch(SOURCE, url, suffix=Path(url.split("?")[0]).suffix)
    raw.fetch(SOURCE, SHAPES, suffix=".geojson")


@cache
def read_councillors_2015() -> tuple[list[Elected], dict[str, str]]:
    """The councillors of the local councils, and the full party name behind each abbreviation."""
    found: list[Elected] = []
    names: dict[str, str] = {}
    for row in sheet(COUNCILLORS_2015)[5:]:
        if not row[0] or not row[6]:
            continue
        party = clean(row[4])
        independent = party.startswith(INDEPENDENT)
        if not independent:
            names[clean(row[3])] = fix(party)
        found.append(
            Elected(
                clean(row[1]), clean(row[2]), f"{title(row[6])} {title(row[5])}", None if independent else party, 2015
            )
        )
    return [person for person in found if person.locality_key], names


@cache
def read_councils_2015() -> dict[str, list[Elected]]:
    """The councillors of the district and municipal councils, by council heading."""
    path = raw.cached(SOURCE, COUNCILS_2015).path
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    body = re.search(r"<w:body>(.*)</w:body>", xml, re.DOTALL)
    assert body is not None
    texts = re.compile(r"<w:t[^>]*>([^<]*)</w:t>")
    councils: dict[str, list[Elected]] = {}
    heading = ""
    after_table = True
    for found in re.finditer(r"<w:tbl>.*?</w:tbl>|<w:p[ >].*?</w:p>", body.group(1), re.DOTALL):
        block = found.group(0)
        if not block.startswith("<w:tbl>"):
            text = "".join(texts.findall(block))
            if text.strip():
                heading = text if after_table else heading + text
                after_table = False
            continue
        after_table = True
        named = re.search(r"Consiliul\s+(municipal|raional)\s+(.+)$", clean(heading))
        if named is None:
            raise ValueError(f"a table of councillors follows the heading {heading!r}")
        label = f"{named.group(1)} {named.group(2).strip()}"
        for row in re.findall(r"<w:tr[ >].*?</w:tr>", block, re.DOTALL):
            cells = ["".join(texts.findall(cell)) for cell in re.findall(r"<w:tc>.*?</w:tc>", row, re.DOTALL)]
            if len(cells) < 3 or not clean(cells[1]) or clean(cells[1]) == "Numele, prenumele":
                continue
            party = clean(cells[2])
            councils.setdefault(label, []).append(
                Elected(named.group(2).strip(), None, fix(cells[1]), None if party == "CI" else party)
            )
    return councils


@cache
def read_mayors_2019() -> list[Elected]:
    found: list[Elected] = []
    for row in sheet(MAYORS_2019)[2:]:
        if not row[0] or not row[2]:
            continue
        party = clean(row[4])
        found.append(
            Elected(
                clean(row[0]),
                clean(row[1]) or None,
                f"{title(row[2])} {title(row[3])}",
                None if party.startswith(INDEPENDENT) else party,
                2019,
            )
        )
    return found


def districts() -> dict[str, tuple[str, str]]:
    """Every district-level area the lists name, by key: its name and its kind."""
    found: dict[str, tuple[str, str]] = {}
    for council in read_councils_2015():
        kind, _, name = council.partition(" ")
        found[district_key(name)] = (district_name(name), kind)
    for person in [*read_councillors_2015()[0], *read_mayors_2019()]:
        text = fix(person.district)
        kind = "municipal" if text.startswith("mun.") else "raional"
        found.setdefault(person.district_key, (district_name(person.district), kind))
    return found


def localities() -> dict[tuple[str, str], str]:
    """Every locality the lists name, by district key and locality key, with the first spelling found."""
    found: dict[tuple[str, str], str] = {}
    for person in [*read_councillors_2015()[0], *read_mayors_2019()]:
        if person.locality is not None and person.locality_key is not None:
            found.setdefault((person.district_key, person.locality_key), person.locality_name)
    return found


def district_area(district: str) -> str:
    return key("area", "md.raion", district)


def locality_area(district: str, locality: str) -> str:
    return key("area", "md.locality", district, locality)


def party_ref(label: str, existing: set[str]) -> str:
    from ea.sources.md import cec

    for text in (label, fix(label), fix(label).translate(UNDONE)):
        if cec.party_id(text) in existing:
            return cec.party_id(text)
    return cec.party_id(fix(label))


@cache
def existing_parties() -> set[str]:
    from ea.sources.md import cec

    return {str(row["id"]) for row in cec.registry().rows["party"]}


def party_label(person: Elected, names: dict[str, str]) -> str | None:
    """The full name of a winner's party, or None for an independent."""
    if person.party is None:
        return None
    return names.get(ABBREVIATIONS.get(person.party, person.party), fix(person.party))


def parties() -> dict[str, str]:
    """The full names of every party a winner belongs to, by party id."""
    names = read_councillors_2015()[1]
    labels: set[str] = set()
    for person in [
        *read_councillors_2015()[0],
        *(w for council in read_councils_2015().values() for w in council),
        *read_mayors_2019(),
    ]:
        label = party_label(person, names)
        if label is not None:
            labels.add(re.sub(r"\s+", " ", label).strip())
    return {party_ref(label, existing_parties()): label for label in sorted(labels)}


def source_rows(p: Package) -> None:
    from ea.sources.md import cec

    for url in (url for urls in FILES.values() for url in urls):
        p.artifact(raw.cached(SOURCE, url), cec.CEC_SOURCE)


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The districts, municipalities, and localities named in the Central Electoral Commission's lists of the "
        "councillors and mayors elected in 2015 and 2019",
    )
    p.external |= {MOLDOVA, COUNTRY}
    shapes = raw.cached(SOURCE, SHAPES)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="geoBoundaries",
        title="geoBoundaries gbOpen Moldova ADM1 (districts, 2020)",
        url="https://www.geoboundaries.org/",
        license="Creative Commons Attribution 3.0 Intergovernmental Organisations (CC BY 3.0 IGO)",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="geoBoundaries (William & Mary geoLab), from UNHCR and OCHA FISS boundaries",
    )
    p.artifact(shapes, SHAPES_ID)
    p.add(
        "scheme",
        code="md.raion",
        polity_id=MOLDOVA,
        kind="administrative",
        rank=1,
        noun="district",
        kinds={"raional": "district", "municipal": "municipality"},
    )
    p.add("scheme", code="md.locality", polity_id=MOLDOVA, kind="administrative", rank=2, noun="locality")
    p.add(
        "boundary_set",
        id=key("boundary_set", "md.raion", 2020),
        scheme_code="md.raion",
        vintage="2020",
        as_of=shapes.retrieved_at.date(),
    )
    outlines = {
        fold(feature["properties"]["shapeName"]): shapely.geometry.shape(feature["geometry"])
        for feature in json.loads(shapes.path.read_text(encoding="utf-8"))["features"]
    }
    for district, (name, kind) in sorted(districts().items()):
        id = district_area(district)
        p.add("area", id=id, scheme_code="md.raion")
        p.add("area_code", area_id=id, code=district)
        p.add("area_kind", area_id=id, kind=kind, source_id=SHAPES_ID)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "ro", "official")
        p.name(id, name, "en", "english", False)
        outline = outlines.get(district)
        if outline is None:
            continue
        outline = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(shapely.make_valid(outline)))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(outline, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES_ID,
        )
    for (district, locality), name in sorted(localities().items()):
        id = locality_area(district, locality)
        p.add("area", id=id, scheme_code="md.locality")
        p.add("area_code", area_id=id, code=f"{district}:{locality}")
        p.add("area_parent", area_id=id, parent_id=district_area(district))
        p.name(id, name, "ro", "official")
        p.name(id, name, "en", "english", False)
    return p


def mayor_office(district: str, locality: str | None) -> str:
    return key("office", "mayor", district, locality or "")


def council_body(kind: str, district: str, locality: str | None = None) -> str:
    return key("body", kind, district, locality or "")


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The mayors' offices, the local, district, and municipal councils, and the parties named in the Central "
        "Electoral Commission's lists of the councillors and mayors elected in 2015 and 2019",
        depends=[GEO, "md.cec/registry"],
    )
    p.external.add(MOLDOVA)
    source_rows(p)
    names = districts()
    places = localities()
    made: set[str] = set()
    for person in read_mayors_2019():
        office = mayor_office(person.district_key, person.locality_key)
        if office in made:
            continue
        made.add(office)
        p.add("office", id=office, polity_id=MOLDOVA, kind="mayor")
        where = places[(person.district_key, person.locality_key)] if person.locality_key else None
        where = where or names[person.district_key][0]
        p.name(office, f"Mayor of {where}", "en", "official")
        p.name(office, f"Primarul localității {where}", "ro", "native", False)
    for council in read_councils_2015():
        kind, _, text = council.partition(" ")
        district = district_key(text)
        body = council_body(kind, district)
        name = names[district][0]
        p.add("body", id=body, polity_id=MOLDOVA, kind="council")
        p.name(body, f"{name} {'Municipal' if kind == 'municipal' else 'District'} Council", "en", "official")
        p.name(body, f"Consiliul {kind} {name}", "ro", "native", False)
    for district, locality in sorted({(w.district_key, w.locality_key) for w in read_councillors_2015()[0]}):
        body = council_body("local", district, locality)
        name = places[(district, str(locality))]
        p.add("body", id=body, polity_id=MOLDOVA, kind="council")
        p.name(body, f"{name} Local Council", "en", "official")
        p.name(body, f"Consiliul local {name}", "ro", "native", False)
    for id, label in parties().items():
        if id in existing_parties():
            continue
        p.add("party", id=id, kind="bloc" if label.lower().startswith("blocul") else "party", polity_id=MOLDOVA)
        p.name(id, label, "ro", "official")
    return p


def build(day: str) -> Package:
    from ea.sources.md import cec

    election_names = {
        "2015-06-14": ("2015 Moldovan general local elections", "Alegeri locale generale 2015"),
        "2019-10-20": ("2019 Moldovan general local elections", "Alegeri locale generale 2019"),
    }
    p = Package(
        f"md.cec/{day}",
        SOURCE,
        f"The councillors and mayors elected in the Moldovan general local elections of {day}, from the Central "
        "Electoral Commission's lists of the elected",
        depends=[REGISTRY, GEO, "md.cec/registry"],
    )
    p.external.add(MOLDOVA)
    for url in FILES[day]:
        p.uses(raw.cached(SOURCE, url))
    held = date.fromisoformat(day)
    path = f"md/{day}-local"
    english, romanian = election_names[day]
    election = key("election", day)
    p.add("election", id=election, polity_id=MOLDOVA, kind="local", date_from=held, date_to=held, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)
    names = districts()
    places = localities()
    builder = Builder(p, day, election, path, cec.CEC_SOURCE)
    if day == "2015-06-14":
        labels = read_councillors_2015()[1]
        by_place: dict[tuple[str, str], list[Elected]] = defaultdict(list)
        for person in read_councillors_2015()[0]:
            by_place[(person.district_key, str(person.locality_key))].append(person)
        for (district, locality), elected in sorted(by_place.items()):
            name = places[(district, locality)]
            builder.council(
                "local",
                district,
                locality,
                elected,
                labels,
                locality_area(district, locality),
                f"{name} Local Council",
                f"Consiliul local {name}",
                f"{district}/{locality}/council",
            )
        for council, elected in sorted(read_councils_2015().items()):
            kind, _, text = council.partition(" ")
            district = district_key(text)
            name = names[district][0]
            builder.council(
                kind,
                district,
                None,
                elected,
                labels,
                district_area(district),
                f"{name} {'Municipal' if kind == 'municipal' else 'District'} Council",
                f"Consiliul {kind} {name}",
                f"{district}/council",
            )
    else:
        labels = read_councillors_2015()[1]
        for person in read_mayors_2019():
            where = places[(person.district_key, person.locality_key)] if person.locality_key else None
            where = where or names[person.district_key][0]
            area = (
                locality_area(person.district_key, person.locality_key)
                if person.locality_key
                else district_area(person.district_key)
            )
            builder.mayor(person, labels, area, where)
    return p


class Builder:
    def __init__(self, p: Package, day: str, election: str, path: str, source: str) -> None:
        self.p = p
        self.day = day
        self.election = election
        self.path = path
        self.source = source
        self.slugs: set[str] = set()
        self.people: set[str] = set()
        self.unit = ""

    def slug(self, text: str) -> str:
        found = f"{self.path}/{text}"
        while found in self.slugs:
            found += "-2"
        self.slugs.add(found)
        return found

    def contest(
        self,
        name: str,
        kind: str,
        english: str,
        romanian: str,
        path: str,
        area: str,
        scheme: str,
        magnitude: int,
        **fields: object,
    ) -> tuple[str, str]:
        p = self.p
        contest = key("contest", self.day, name)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind=kind,
            district_area_id=area,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
            **fields,
        )
        p.name(contest, english, "en", "official")
        p.name(contest, romanian, "ro", "native", False)
        p.add("slug", path=self.slug(path), entity_id=contest, is_current=True)
        result_set = key("result_set", self.day, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=self.source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        self.unit = key("unit", self.day, name)
        p.add(
            "reporting_unit",
            id=self.unit,
            polity_id=MOLDOVA,
            scheme_code=scheme,
            code=f"{self.day}:{name.replace(chr(31), ':')}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )
        p.name(self.unit, english, "en", "official")
        return contest, result_set

    def seats(self, result_set: str, option: str, seats: int) -> None:
        self.p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=self.unit,
            option_id=option,
            channel=0,
            measure=SEATS,
            count_round=0,
            flags=0,
            value=seats,
        )

    def person(self, name: str, *parts: object) -> str:
        who = key("person", self.day, *parts)
        if who not in self.people:
            self.people.add(who)
            self.p.add("person", id=who)
            self.p.name(who, name, "ro", "official")
        return who

    def mayor(self, person: Elected, names: dict[str, str], area: str, where: str) -> None:
        p = self.p
        name = f"mayor\x1f{person.district_key}\x1f{person.locality_key or ''}"
        contest, result_set = self.contest(
            name,
            "office",
            f"Mayor of {where}",
            f"Primarul localității {where}",
            f"{person.district_key.replace(' ', '-')}/{slug(where)}/mayor",
            area,
            "md.locality" if person.locality_key else "md.raion",
            1,
            office_id=mayor_office(person.district_key, person.locality_key),
        )
        label = party_label(person, names)
        party = party_ref(label, existing_parties()) if label else None
        option = key("option", self.day, name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party,
            ballot_label=person.name,
        )
        who = self.person(person.name, name)
        candidacy = key("candidacy", self.day, name)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=who,
            option_id=option,
            role="head",
            ballot_name=person.name,
            member_of_party_id=party,
            is_independent=party is None,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        self.seats(result_set, option, 1)

    def council(
        self,
        kind: str,
        district: str,
        locality: str | None,
        elected: list[Elected],
        names: dict[str, str],
        area: str,
        english: str,
        romanian: str,
        path: str,
    ) -> None:
        p = self.p
        name = f"{kind}\x1f{district}\x1f{locality or ''}"
        contest, result_set = self.contest(
            name,
            "list_allocation",
            english,
            romanian,
            path.replace(" ", "-"),
            area,
            "md.locality" if locality else "md.raion",
            len(elected),
            body_id=council_body(kind, district, locality),
        )
        options: dict[str, str] = {}
        seats: dict[str, int] = defaultdict(int)
        for number, person in enumerate(elected, 1):
            label = party_label(person, names)
            party = party_ref(label, existing_parties()) if label else None
            group = party or f"independent {number}"
            if group not in options:
                options[group] = key("option", self.day, name, group)
                p.add(
                    "contest_option",
                    id=options[group],
                    contest_id=contest,
                    kind="list" if party else "candidate",
                    party_id=party,
                    ballot_label=label or person.name,
                )
            seats[group] += 1
            who = self.person(person.name, name, number)
            candidacy = key("candidacy", self.day, name, number)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=who,
                option_id=options[group],
                role="list_member" if party else "head",
                ballot_name=person.name,
                member_of_party_id=party,
                is_independent=party is None,
            )
            if party:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=options[group],
                    candidacy_id=candidacy,
                    kind="elected",
                )
        for group, option in options.items():
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats[group])
            self.seats(result_set, option, seats[group])


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"md.cec/{day}" for day in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()
