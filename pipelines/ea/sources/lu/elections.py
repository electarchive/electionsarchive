"""Luxembourg's national elections by commune, from the open data of the government's elections site.

The Centre des technologies de l'information de l'Etat publishes each election's results as an XML file with a
nested tree of country, constituency, canton, and commune. Every commune holds its own count: the ballots, and for
each list its votes and its candidates' personal votes. The commune is the finest unit the authority publishes.
"""

import functools
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid5
from xml.etree import ElementTree

import httpx
import polars as pl
import shapely

from ea import package, raw
from ea.package import Package
from ea.sources.lu import pages, shapes
from ea.sources.lu.model import Candidate, Entry, Place, Race, Stats, clean, fold, number, slug

SOURCE = "lu.elections"
SITE = "https://elections.public.lu"
DATA = "https://download.data.public.lu/resources"
LUXEMBOURG = str(uuid5(package.NAMESPACE, "polity\x1fLU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fLUX"))
ELECTORAL_ROLL, VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 0, 1, 2, 3, 4, 5, 6
PREFERENCE, LIST_VOTES, CANDIDATE_VOTES = 7, 8, 10
POSTAL = "Votes par correspondance"
SEATS_COUNT = {"sud": 23, "centre": 21, "nord": 9, "est": 7}
EUROPEAN_SEATS = 6
NS1 = "{http://www.elections.public.lu}"

# One election day's files, by family. Each entry names the file (by url), its schema, and the day.
FILES: dict[str, tuple[str, str, str]] = {
    "legislative-2013": (
        "legislative",
        "v1",
        "https://elections.public.lu/opendata/legislatives/2013/2013-legislatives-xml.xml",
    ),
    "legislative-2018": (
        "legislative",
        "v2",
        f"{DATA}/elections-legislatives-du-14-octobre-2018-donnees-officieuses/20181014-230100/leg-2018-10-14-22-54-01-575.xml",
    ),
    "legislative-2023": (
        "legislative",
        "v2",
        f"{DATA}/elections-legislatives-2023-donnees-officieuses/20231009-102942/leg-2023-10-08-22-57-35-939.xml",
    ),
    "european-2014": ("european", "v1", "https://elections.public.lu/opendata/europeennes/2014/2014-europeennes.xml"),
    "european-2019": (
        "european",
        "v2",
        f"{DATA}/elections-europeennes-2019-donnees-officieuses/20190526-230019/eur-2019-05-26-23-00-00-000.xml",
    ),
    "european-2024": (
        "european",
        "v2",
        f"{DATA}/elections-europeennes-2024-donnees-officieuses/20240610-083838/eur-2024-06-09-22-58-10-699.xml",
    ),
    "communal-2017": (
        "communal",
        "v2",
        f"{DATA}/elections-communales-2017-donnees-officieuses/20171113-165900/COM_2017-11-10-13-30-00-000.xml",
    ),
}
DAYS = {
    "1994-06-12": ["legislative-1994", "european-1994"],
    "1999-06-13": ["legislative-1999", "european-1999"],
    "2004-06-13": ["legislative-2004", "european-2004"],
    "2005-10-09": ["communal-2005"],
    "2009-06-07": ["legislative-2009", "european-2009"],
    "2011-10-09": ["communal-2011"],
    "2013-10-20": ["legislative-2013"],
    "2014-05-25": ["european-2014"],
    "2015-06-07": ["referendum-2015"],
    "2017-10-08": ["communal-2017"],
    "2018-10-14": ["legislative-2018"],
    "2019-05-26": ["european-2019"],
    "2023-06-11": ["communal-2023"],
    "2023-10-08": ["legislative-2023"],
    "2024-06-09": ["european-2024"],
}
NAMES = {
    "legislative": ("legislative election", "Chambre des Députés", "Chamberwahlen"),
    "european": ("European Parliament election", "Élections européennes", "Europawahlen"),
    "communal": ("communal election", "Élections communales", "Gemengewalen"),
    "referendum": ("referendum", "Référendum", "Referendum"),
}
QUESTIONS = (
    (
        "Approuvez-vous l'idée que les Luxembourgeois âgés entre seize et dix-huit ans aient le droit de s'inscrire de "
        "manière facultative sur les listes électorales en vue de participer comme électeurs aux élections pour la "
        "Chambre des Députés, aux élections européennes et communales ainsi qu'aux référendums?"
    ),
    (
        "Approuvez-vous l'idée que les résidents non luxembourgeois aient le droit de s'inscrire de manière facultative "
        "sur les listes électorales en vue de participer comme électeurs aux élections pour la Chambre des Députés, à la "
        "double condition particulière d'avoir résidé pendant au moins dix ans au Luxembourg et d'avoir préalablement "
        "participé aux élections communales ou européennes au Luxembourg?"
    ),
    (
        "Approuvez-vous l'idée de limiter à dix ans la durée maximale pendant laquelle, de façon continue, une personne "
        "peut être membre du Gouvernement?"
    ),
)
ACCEPTED: dict[str, list[tuple[str, str]]] = {
    "1994-06-12": [
        (
            "accounting.ballots_sum",
            (
                "the 1994 European Parliament pages print ballot figures that do not add up in three communes: Bastendorf "
                "(379 ballots cast, 36 invalid, 0 valid), Grosbous (397 cast, 368 valid, 0 invalid), and Mertert (1,717 "
                "cast, 1,547 valid, 0 invalid); they are stored as printed"
            ),
        ),
        (
            "accounting.turnout",
            (
                "the 1994 European Parliament page for Flaxweiler prints 735 ballots in the box for 713 registered voters; "
                "stored as printed"
            ),
        ),
    ],
    "2005-10-09": [
        (
            "accounting.turnout",
            (
                "the 2005 communal page for Reisdorf prints 478 ballots in the box for 463 registered voters; stored as "
                "printed"
            ),
        )
    ],
    "2013-10-20": [
        (
            "accounting.declared",
            (
                "the Est constituency's printed totals include the commune of Waldbredimus, whose ballots the file "
                "gives but whose list and candidate votes it omits; the commune's votes are missing here"
            ),
        )
    ],
}


def family_of(name: str) -> str:
    return FILES[name][0] if name in FILES else PAGES[name][0]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
BOUNDARIES = key("source", "boundaries")
CHAMBER = key("body", "chamber")
SYSTEM_LEGISLATIVE = key("electoral_system", "legislative")
SYSTEM_EUROPEAN = key("electoral_system", "european")
SYSTEM_LIST_COMMUNE = key("electoral_system", "communal", "list")
SYSTEM_BLOCK_COMMUNE = key("electoral_system", "communal", "block")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")

METHOD = (
    "Counted per commune and published by the Centre des technologies de l'information de l'Etat as the elections "
    "site's open data. Registered voters are the electoral roll. Ballots cast are the ballots in the box, including "
    "those cast by post; valid, blank, and invalid ballots add up to them. A voter has as many votes as there are "
    "seats and may give a candidate up to two, so a list's total votes are its list votes (cast for the list as "
    "printed) plus its candidates' personal votes. The commune is the finest unit published; the authority "
    "reports no polling-station results."
)


# Parties are source-local. These are the national parties, by their folded abbreviations; every other list is a
# local one and carries no party.
PARTIES = {
    "lsap": ("LSAP", "Luxembourg Socialist Workers' Party"),
    "dp": ("DP", "Democratic Party"),
    "csv": ("CSV", "Christian Social People's Party"),
    "adr": ("ADR", "Alternative Democratic Reform Party"),
    "greng": ("déi gréng", "The Greens"),
    "lenk": ("déi Lénk", "The Left"),
    "kpl": ("KPL", "Communist Party of Luxembourg"),
    "piraten": ("PIRATEN", "Pirate Party Luxembourg"),
    "pid": ("PID", "Party for Integral Democracy"),
    "demokratie": ("DEMOKRATIE", "Demokratie"),
    "konservativ": ("déi Konservativ", "The Conservatives"),
    "fokus": ("FOKUS.", "Fokus."),
    "liberte": ("LIBERTÉ-FRÄIHEET !", "Liberté-Fräiheet"),
    "volt": ("VOLT", "Volt Luxembourg"),
    "mirdvollek": ("Mir d'Vollek", "Mir d'Vollek"),
    "zesummen": ("Zesummen – d'Bréck", "Zesummen – d'Bréck"),
}
# What each national party's list is called, folded: a list belongs to the first party with a matching start or word.
PATTERNS = (
    ("lsap", ("lsap", "dsozialisten", "sozialistesch", "socialiste")),
    ("csv", ("csv", "chreschtlech")),
    ("dp", ("dp", "demokratesch partei")),
    ("adr", ("adr", "alternativdemokratesch", "aktiounskomitee")),
    ("greng", ("greng", "greenen")),
    ("lenk", ("lenk", "lagauche")),
    ("kpl", ("kpl", "kommunist")),
    ("piraten", ("pirat",)),
    ("pid", ("pid", "partei fir integral")),
    ("demokratie", ("demokratie",)),
    ("konservativ", ("konservativ",)),
    ("fokus", ("fokus",)),
    ("liberte", ("liberte",)),
    ("volt", ("volt",)),
    ("mirdvollek", ("mirdvollek",)),
    ("zesummen", ("zesummen",)),
)


def party_slug(sigle: str, name: str = "") -> str | None:
    for text in (sigle, name):
        folded = fold(text)
        for found, patterns in PATTERNS:
            if any(folded.startswith(fold(p)) if len(fold(p)) <= 3 else fold(p) in folded for p in patterns):
                return found
    return None


def list_party(entry: Entry, family: str) -> str | None:
    """The party a list stands for: a national party, or for the national elections the list's own party."""
    found = party_slug(entry.sigle, entry.name)
    if found or family == "communal" or not (entry.name or entry.sigle):
        return found
    return f"list-{fold(entry.name or entry.sigle)}"


def party_id(slug: str) -> str:
    return key("party", slug)


def url(name: str) -> str:
    return FILES[name][2]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def page(path: str) -> str:
    return f"{SITE}/fr/{path}.html"


# Elections whose results the site publishes only as one page per commune.
PAGES: dict[str, tuple[str, str, str]] = {
    **{
        f"legislative-{year}": ("legislative", f"elections-legislatives/{year}", "communes")
        for year in (1994, 1999, 2004, 2009)
    },
    **{
        f"european-{year}": ("european", f"elections-europeennes/{year}", "communes")
        for year in (1994, 1999, 2004, 2009)
    },
    **{f"communal-{year}": ("communal", f"elections-communales/{year}", "communes") for year in (2005, 2011, 2023)},
    "referendum-2015": ("referendum", "referendum/2015", "commune"),
}


NAMES_ALL = [*FILES, *PAGES]


def index_pages(name: str) -> list[str]:
    _, base, _ = PAGES[name]
    return [page(f"{base}/resultats")]


def commune_pages(name: str) -> list[str]:
    """The URL of every commune's page, as the election's results index links them."""
    _, base, folder = PAGES[name]
    found = re.findall(
        rf'href="(/fr/{re.escape(base)}/resultats/{folder}/[^"/]+\.html)"',
        raw.cached(SOURCE, page(f"{base}/resultats")).path.read_text(encoding="utf-8"),
    )
    return [f"{SITE}{path}" for path in dict.fromkeys(found)]


def extra_pages(name: str) -> list[str]:
    """The constituency pages of an election, and the pages of a referendum's questions."""
    _, base, _ = PAGES[name]
    text = raw.cached(SOURCE, page(f"{base}/resultats")).path.read_text(encoding="utf-8")
    found = re.findall(rf'href="(/fr/{re.escape(base)}/resultats/(?:circonscriptions|question)/[^"]+\.html)"', text)
    if name == "referendum-2015":
        found += [f"/fr/{base}/resultats/question/{n}.html" for n in (1, 2, 3)]
    return [f"{SITE}{path}" for path in dict.fromkeys(found)]


def get(url: str) -> None:
    """Fetch one page, backing off when the site resets the connection."""
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, url, suffix=".html")
            return
        except httpx.TransportError:
            if attempt == 5:
                raise
            time.sleep(2**attempt)


def fetch() -> None:
    shapes.fetch()
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=".xml")
    for link in merged_pages("communal-2017"):
        get(link)
    for name, (_, base, _) in PAGES.items():
        raw.fetch(SOURCE, page(f"{base}/resultats"), suffix=".html")
    with ThreadPoolExecutor(2) as pool:
        for name in PAGES:
            list(pool.map(get, [*extra_pages(name), *commune_pages(name)]))


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


# Reading the files.


def _int(element: ElementTree.Element | None, name: str) -> int | None:
    return None if element is None else number(element.get(name))


def _stats_v2(entity: ElementTree.Element) -> Stats:
    voters = entity.find("statistiques/electeurs")
    ballots = entity.find("statistiques/bulletins")
    return Stats(
        _int(voters, "inscrits"),
        _int(ballots, "urne"),
        _int(ballots, "blancs"),
        _int(ballots, "nuls"),
        _int(ballots, "valables"),
    )


def _candidates_v2(parent: ElementTree.Element | None) -> list[Candidate]:
    if parent is None:
        return []
    return [
        Candidate(int(c.get("numero", "0")), clean(c.text), number(c.get("suffrages")), c.get("elu") == "ELU")
        for c in parent.findall("candidat")
    ]


def _entries_v2(results: ElementTree.Element | None) -> list[Entry]:
    found: list[Entry] = []
    if results is None:
        return found
    for liste in results.findall("listes/liste"):
        name = liste.find("noms/nom")
        sigle = liste.find("sigles/sigle")
        found.append(
            Entry(
                int(liste.get("numero", "0")),
                clean(name.text if name is not None else ""),
                clean(sigle.text if sigle is not None else ""),
                number(liste.get("suffragesTotal")),
                number(liste.get("suffragesListe")),
                number(liste.get("suffragesNominatifs")),
                number(liste.get("mandatsAttribues")),
                _candidates_v2(liste.find("candidats")),
            )
        )
    return found


def _place_v2(entity: ElementTree.Element, canton: str | None) -> Place:
    results = entity.find("resultats")
    return Place(
        clean(entity.findtext("nom")),
        canton,
        _stats_v2(entity),
        _entries_v2(results),
        _candidates_v2(results.find("candidats") if results is not None else None),
    )


def read_v2(name: str) -> list[Race]:
    root = ElementTree.parse(raw.cached(SOURCE, url(name)).path).getroot()
    races: list[Race] = []

    def walk(entity: ElementTree.Element, canton: str | None) -> list[Place]:
        places: list[Place] = []
        for child in entity.findall("entites/entite"):
            if child.get("type") == "COMMUNE":
                places.append(_place_v2(child, canton))
            else:
                places += walk(child, clean(child.findtext("nom")) if child.get("type") == "CANTON" else canton)
        return places

    def visit(entity: ElementTree.Element, canton: str | None) -> None:
        if entity.get("circonscriptionElectorale") == "true":
            results = entity.find("resultats")
            commune = entity.get("type") == "COMMUNE"
            races.append(
                Race(
                    clean(entity.findtext("nom")),
                    number(entity.get("mandats")),
                    entity.get("modeScrutin", ""),
                    _stats_v2(entity),
                    _entries_v2(results),
                    _candidates_v2(results.find("candidats") if results is not None else None),
                    [_place_v2(entity, canton)] if commune else walk(entity, canton),
                )
            )
            return
        for child in entity.findall("entites/entite"):
            visit(child, clean(child.findtext("nom")) if child.get("type") == "CANTON" else canton)

    top = root.find("entite")
    assert top is not None
    visit(top, None)
    return races


def _stats_v1(info: ElementTree.Element) -> Stats:
    voters = info.find(f"{NS1}electeurs")
    ballots = info.find(f"{NS1}bulletins")
    return Stats(
        _int(voters, "inscrits"),
        _int(ballots, "urne"),
        None,
        _int(ballots, "nuls"),
        _int(ballots, "valables"),
    )


def _entries_v1(results: ElementTree.Element | None) -> list[Entry]:
    found: list[Entry] = []
    if results is None:
        return found
    for liste in results.findall(f"{NS1}liste"):
        counted = liste.find(f"{NS1}resultats_liste")
        assert counted is not None
        found.append(
            Entry(
                int(liste.get("id", "0")),
                clean(liste.findtext(f"{NS1}nom")),
                clean(liste.findtext(f"{NS1}sigle")),
                number(counted.get("suffrages_total")),
                number(counted.get("suffrages_liste")),
                None,
                number(counted.get("nombre_mandats")),
                [
                    Candidate(int(c.get("id", "0")), clean(c.text), number(c.get("suffrages")), c.get("elu") == "oui")
                    for c in counted.findall(f"{NS1}candidat")
                ],
            )
        )
    return found


def read_v1(name: str) -> list[Race]:
    root = ElementTree.parse(raw.cached(SOURCE, url(name)).path).getroot()
    family = FILES[name][0]
    top = root.find(f"{NS1}elections_legislatives" if family == "legislative" else f"{NS1}elections_europeennes")
    assert top is not None
    if family == "legislative":
        circles = top.findall(f"{NS1}circonscriptions/{NS1}circonscription")
    else:
        circles = top.findall(f"{NS1}circonscription")
    races: list[Race] = []
    for circle in circles:
        info = circle.find(f"{NS1}info_circonscription")
        assert info is not None
        label = clean(info.findtext(f"{NS1}nom")).title()
        entries = _entries_v1(circle.find(f"{NS1}resultats"))
        places: list[Place] = []
        for commune in circle.findall(f"{NS1}communes/{NS1}commune"):
            about = commune.find(f"{NS1}info_commune")
            assert about is not None
            places.append(
                Place(
                    clean(about.findtext(f"{NS1}nom")),
                    clean(about.findtext(f"{NS1}canton")).title(),
                    _stats_v1(about),
                    _entries_v1(commune.find(f"{NS1}resultats")),
                )
            )
        races.append(
            Race(
                label if family == "legislative" else "Luxembourg",
                SEATS_COUNT[label.casefold()] if family == "legislative" else EUROPEAN_SEATS,
                "PROPORTIONNEL",
                _stats_v1(info),
                entries,
                [],
                places,
            )
        )
    held = {place.name for race in races for place in race.places}
    for commune in top.findall(f"{NS1}progression/{NS1}commune"):
        about = commune.find(f"{NS1}info_commune")
        assert about is not None
        found = clean(about.findtext(f"{NS1}nom"))
        canton = clean(about.findtext(f"{NS1}canton")).title()
        if found not in held:
            # The file lists this commune's ballots but no list results; hold it with its ballots in the
            # constituency of its canton.
            home = [r for r in races if any(pl.canton == canton for pl in r.places)]
            if len(home) == 1:
                home[0].places.append(Place(found, canton, _stats_v1(about)))
    return races


MERGED_2017 = ("helperknapp", "rosport-mompach", "parc-hosingen", "vallee-de-l-ernz")


def merged_pages(name: str) -> list[str]:
    """The pages of the communes the 2017 file holds only ballots for, which print each section's candidates."""
    if name != "communal-2017":
        return []
    return [f"{SITE}/fr/elections-communales/2017/resultats/communes/{part}.html" for part in MERGED_2017]


@functools.cache
def races(name: str) -> list[Race]:
    if name in FILES:
        found = read_v1(name) if FILES[name][1] == "v1" else read_v2(name)
        return found + _sections_2017(found) if merged_pages(name) else found
    return read_pages(name)


def _sections_2017(found: list[Race]) -> list[Race]:
    """The sections of the merged communes, each elected by majority, from the commune pages.

    The file counts these communes' ballots as a whole and holds no candidates. The pages give each section's seats
    and each candidate's votes, as printed; Ermsdorf's five candidates for five seats print zero votes each.
    """
    cantons = {race.places[0].name: race.places[0].canton for race in found if len(race.places) == 1}
    made: list[Race] = []
    for link in merged_pages("communal-2017"):
        for commune, _, _, section, seats, _, plain in pages.communal_2023(html_of(link)):
            if seats is None or not section:
                raise ValueError(f"{link}: no section or seats")
            place = Place(commune, cantons.get(commune), Stats(), [], plain)
            made.append(Race(f"{commune} ({section})", seats, "MAJORITAIRE", place.stats, [], plain, [place], False))
    return made


def html_of(link: str) -> str:
    return raw.cached(SOURCE, link).path.read_text(encoding="utf-8")


def read_pages(name: str) -> list[Race]:
    family, base, _ = PAGES[name]
    year = int(name.rsplit("-", 1)[1])
    if family == "referendum":
        return []
    links = commune_pages(name)
    if name == "communal-2023":
        return _communal_2023(links)
    found = [pages.legacy_place(html_of(link)) for link in links]
    for (place, _), link in zip(found, links, strict=True):
        if place.name.startswith(POSTAL):
            place.name = f"{POSTAL} - {link.rsplit('/', 1)[1].removeprefix('vc-').removesuffix('.html').title()}"
    if family == "communal":
        if all(not (pl.stats.invalid or 0) for pl, _ in found) and any(
            (pl.stats.cast or 0) > (pl.stats.valid or 0) for pl, _ in found
        ):
            for pl, _ in found:  # the page prints zero blank and invalid ballots everywhere, which cannot be so
                pl.stats.invalid = None
        return [_communal_race(place, view) for place, view in found]
    groups: dict[str, list[Place]] = {}
    for place, view in found:
        circle = clean(view.details.get("Circonscription")).title()
        label = circle if family == "legislative" else "Luxembourg"
        if family == "european" or circle in ("Sud", "Centre", "Nord", "Est"):
            groups.setdefault(label, []).append(place)
        else:
            raise ValueError(f"{name}: {place.name} has constituency {circle!r}")
    made: list[Race] = []
    for label, group in sorted(groups.items()):
        seats = SEATS_COUNT[label.casefold()] if family == "legislative" else EUROPEAN_SEATS
        link = page(f"{base}/resultats/circonscriptions/{'unique' if family == 'european' else slug(label)}")
        try:
            main = pages.Legacy(html_of(link))
        except LookupError:
            made.append(Race(label, seats, "PROPORTIONNEL", _sum(group), [], [], group, printed=False))
            continue
        made.append(Race(label, seats, "PROPORTIONNEL", pages.stats_of(main.details), main.entries(), [], group))
    del year
    return made


def _sum(places: list[Place]) -> Stats:
    totals = Stats()
    for field_ in ("registered", "cast", "blank", "invalid", "valid"):
        values = [getattr(pl.stats, field_) for pl in places]
        setattr(totals, field_, sum(v or 0 for v in values) if any(v is not None for v in values) else None)
    return totals


def _communal_race(place: Place, view: pages.Legacy) -> Race:
    seats = pages.figure(view.details.get("Conseillers", ""))
    proportional = "proportionnelle" in view.details.get("Système électoral", "").casefold()
    if not proportional:
        place.candidates = [Candidate(i, who, votes, won) for i, (who, votes, won) in enumerate(view.plain, 1)]
        place.entries = []
    return Race(
        place.name,
        seats,
        "PROPORTIONNEL" if proportional else "MAJORITAIRE",
        place.stats,
        place.entries,
        [] if proportional else place.candidates,
        [place],
        printed=False,
    )


def _communal_2023(links: list[str]) -> list[Race]:
    made: list[Race] = []
    for link in links:
        for commune, registered, canton, section, seats, entries, plain in pages.communal_2023(html_of(link)):
            if seats is None:
                continue  # the commune's election was postponed; the page holds no results
            place = Place(commune, canton or None, Stats(registered=registered), entries, plain)
            label = f"{commune} ({section})" if section else commune
            made.append(
                Race(
                    label,
                    seats,
                    "PROPORTIONNEL" if entries else "MAJORITAIRE",
                    place.stats,
                    entries,
                    plain,
                    [place],
                    printed=False,
                )
            )
    return made


def read_referendum(name: str) -> list[tuple[Place, list[tuple[int | None, int | None]]]]:
    found = []
    for link in commune_pages(name):
        commune, stats, answers = pages.referendum(html_of(link))
        found.append((Place(commune, None, stats), answers))
    return found


def national_referendum(name: str, question: int) -> dict[str, int]:
    """The question's national yes and no votes as the question page prints them."""
    _, base, _ = PAGES[name]
    return pages.national_votes(html_of(page(f"{base}/resultats/question/{question}")))


# Geography.


# The elections site spells some communes in ways the cadastre's files for the same period do not. Each spelling maps
# to the cadastre's commune in force at that election, so a commune has one area and its shapes apply. The 2011
# council election was held before the 2012 mergers, so its Kaerjeng lists stand for the two former communes, while
# the single list for the Ernz valley stands for the commune the merger formed, which the election chose a council for.
SPELLINGS = {
    "bettborn": "Préizerdaul",
    "colmas-berg": "Colmar-Berg",
    "redange-sur-attert": "Redange",
    "habscht-hobscheid": "Hobscheid",
    "habscht-septfontaines": "Septfontaines",
    "kaerjeng-bascharage": "Bascharage",
    "kaerjeng-clemency": "Clemency",
    "lac-haute-sure": "Lac de la Haute-Sûre",
    "aerenzdallgemeng": "Vallée de l'Ernz",
}


def commune_name(text: str) -> str:
    """The cadastre's spelling of a commune the elections site names."""
    return SPELLINGS.get(slug(text), text)


def area(scheme: str, text: str) -> str:
    return key("area", scheme, slug(commune_name(text) if scheme == "lu.commune" else text))


def places_of(name: str) -> dict[str, str | None]:
    """The communes an election counted, with their cantons where the source gives them."""
    found: dict[str, str | None] = {}
    for race in races(name):
        for place in race.places:
            if not place.name.startswith(POSTAL):
                found[place.name] = place.canton
    if name == "referendum-2015":
        found = {place.name: None for place, _ in read_referendum(name)}
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        (
            "Luxembourg's four legislative constituencies with their shapes since 2006, its cantons, and its communes with their shapes since before "
            "1920 and the mergers between them, with the communes named in the elections site's result files"
        ),
    )
    p.external |= {LUXEMBOURG, COUNTRY}
    _source(p)
    for name in NAMES_ALL:
        for artifact in sources_of(name):
            p.uses(artifact)
    for code, rank, kind, noun in (
        ("lu.circonscription", 1, "electoral_district", "constituency"),
        ("lu.canton", 1, "administrative", "canton"),
        ("lu.commune", 2, "administrative", "commune"),
        ("lu.count", 3, "precinct", "commune count"),
    ):
        p.add("scheme", code=code, polity_id=LUXEMBOURG, kind=kind, rank=rank, noun=noun, code_system=None)
    for text in ("Sud", "Centre", "Nord", "Est"):
        id = area("lu.circonscription", text)
        p.add("area", id=id, scheme_code="lu.circonscription")
        p.add("area_code", area_id=id, code=slug(text))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "fr", "official")
    atlas = shapes.load()
    _boundaries(p, atlas)
    cantons: dict[str, str] = {}
    communes: dict[str, tuple[str, str]] = {}
    for name in NAMES_ALL:
        for commune, canton in places_of(name).items():
            commune = commune_name(commune)
            _, held = communes.get(slug(commune), (commune, ""))
            communes.setdefault(slug(commune), (commune, canton or held))
            if canton:
                communes[slug(commune)] = (communes[slug(commune)][0], canton)
                cantons.setdefault(slug(canton), canton)
    for code, found in atlas.cantons.items():
        cantons.setdefault(code, found.name)
    for code, found in atlas.communes.items():
        communes.setdefault(code, (found.name, ""))
    for code, text in sorted(cantons.items()):
        id = key("area", "lu.canton", code)
        p.add("area", id=id, scheme_code="lu.canton")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text.title(), "fr", "official")
    for text, canton in sorted(communes.values(), key=lambda item: slug(item[0])):
        id = area("lu.commune", text)
        p.add("area", id=id, scheme_code="lu.commune")
        p.add("area_code", area_id=id, code=slug(text))
        p.name(id, text, "fr", "official")
        if slug(text) not in atlas.communes:
            p.add("area_parent", area_id=id, parent_id=key("area", "lu.canton", slug(canton)) if canton else COUNTRY)
    _shapes(p, atlas)
    return p


def _boundaries(p: Package, atlas: shapes.Atlas) -> None:
    p.add(
        "source",
        id=BOUNDARIES,
        kind="official_body",
        publisher="Administration du cadastre et de la topographie",
        title="Limites communales historiques and Limites administratives du Grand-Duché de Luxembourg",
        url=shapes.DATASET,
        license="Open data of the Luxembourg government, published on data.public.lu under the Creative Commons "
        "Zero licence",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Administration du cadastre et de la topographie, data.public.lu",
    )
    for artifact in atlas.artifacts:
        p.artifact(artifact, BOUNDARIES)


def _shapes(p: Package, atlas: shapes.Atlas) -> None:
    """The shapes and cantons of the communes in each period, and a note of each merger on the communes it joined."""
    for scheme, found in (
        ("lu.circonscription", atlas.circonscriptions),
        ("lu.canton", atlas.cantons),
        ("lu.commune", atlas.communes),
    ):
        for code, commune in found.items():
            id = key("area", scheme, code)
            for version in commune.versions:
                shape = shapes.wgs(version.shape)
                p.add(
                    "area_geometry",
                    area_id=id,
                    valid_from=version.start,
                    valid_to=version.end,
                    geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                    label_point=shapely.to_wkb(
                        shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                    ),
                    quality="official",
                    source_id=BOUNDARIES,
                )
            if scheme == "lu.commune":
                for canton, start, end in commune.parents():
                    p.add(
                        "area_parent",
                        area_id=id,
                        parent_id=key("area", "lu.canton", canton),
                        valid_from=start,
                        valid_to=end,
                    )
        for period in shapes.PERIODS:
            if scheme == "lu.circonscription" and period.vintage < "2006":
                continue
            p.add(
                "boundary_set",
                id=p.id("boundary_set", scheme, period.vintage),
                scheme_code=scheme,
                vintage=period.vintage,
                valid_from=period.start,
                valid_to=period.end,
                as_of=period.start or date(1919, 12, 31),
            )
    for merger in shapes.MERGERS:
        into = area("lu.commune", merger.target)
        names = [name for name in merger.sources if slug(name) != slug(merger.target)]
        joined = f"{', '.join(names[:-1])} and {names[-1]}" if len(names) > 1 else names[0]
        verb = "merged into" if slug(merger.target) in {slug(s) for s in merger.sources} else "merged to form"
        text = f"On {_day(merger.day)}, {joined} {verb} {merger.target}."
        _note(p, into, merger.day, text)
        for name in names:
            _note(p, area("lu.commune", name), merger.day, f"On {_day(merger.day)}, merged into {merger.target}.")
    for day, old, new in shapes.RENAMES:
        _note(
            p,
            area("lu.commune", old),
            day,
            f"On {_day(day)}, renamed {new}; the file of the later period draws it under that name.",
        )
        _note(p, area("lu.commune", new), day, f"Formerly {old}, renamed on {_day(day)}.")


def _day(day: date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def _note(p: Package, entity: str, day: date, text: str) -> None:
    p.add("note", id=p.id("note", entity, day, text), entity_id=entity, kind="context", body_md=text, lang="en")
    p.add(
        "citation",
        id=p.id("citation", entity, day, text),
        entity_id=entity,
        source_id=BOUNDARIES,
        locator={"file": "fusions-communales.pdf"},
    )


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Centre des technologies de l'information de l'Etat, for the Ministère d'État",
        title="Elections site of the Grand Duchy of Luxembourg: results open data",
        url=f"{SITE}/",
        license="Open data of the Luxembourg government, published on data.public.lu under the Creative Commons "
        "Zero licence",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Centre des technologies de l'information de l'Etat, elections.public.lu",
    )


# Parties, bodies, and electoral systems.


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        (
            "Luxembourg's national parties, the Chamber of Deputies, the communal councils, and the electoral systems of "
            "the legislative, European, and communal elections"
        ),
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(LUXEMBOURG)
    seen: dict[str, str] = {}
    for name in NAMES_ALL:
        for race in races(name):
            for entry in [*race.entries, *(e for place in race.places for e in place.entries)]:
                found = list_party(entry, family_of(name))
                if found:
                    seen.setdefault(found, entry.name or entry.sigle)
    for found, label in sorted(seen.items()):
        id = party_id(found)
        p.add("party", id=id, kind="party", polity_id=LUXEMBOURG)
        if found in PARTIES:
            sigle, english = PARTIES[found]
            p.name(id, english, "en", "english")
            p.name(id, sigle, "lb", "abbreviation", False)
        else:
            p.name(id, label, "fr", "official")
    p.add("body", id=CHAMBER, polity_id=LUXEMBOURG, kind="unicameral")
    p.name(CHAMBER, "Chamber of Deputies", "en", "official")
    p.name(CHAMBER, "Chambre des Députés", "fr", "native", False)
    p.name(CHAMBER, "Chamber vun den Deputéierten", "lb", "native", False)
    communes = {
        slug(commune_name(c)): commune_name(c)
        for name in reversed(NAMES_ALL)
        if family_of(name) == "communal"
        for c in places_of(name)
    }
    for commune in sorted(communes.values(), key=slug):
        id = council(commune)
        p.add("body", id=id, polity_id=LUXEMBOURG, kind="council")
        p.name(id, f"Communal council of {commune}", "en", "english")
        p.name(id, f"Conseil communal de {commune}", "fr", "native", False)
    systems = (
        (
            SYSTEM_LEGISLATIVE,
            {"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
            (
                "Sixty deputies are elected in four constituencies (Sud 23, Centre 21, Nord 9, Est 7) by list "
                "proportional representation with the Hagenbach-Bischoff method. Each voter has as many votes as the "
                "constituency has seats and may vote for a whole list or distribute votes across lists, giving a "
                "candidate at most two."
            ),
        ),
        (
            SYSTEM_EUROPEAN,
            {"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
            (
                "Luxembourg's six members of the European Parliament are elected in one national constituency by list "
                "proportional representation with the Hagenbach-Bischoff method. Each voter has six votes and may "
                "distribute them across lists, giving a candidate at most two."
            ),
        ),
        (
            SYSTEM_LIST_COMMUNE,
            {"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
            (
                "Councils of communes with 3,000 or more inhabitants are elected by list proportional representation "
                "with the Hagenbach-Bischoff method; voters have as many votes as there are seats."
            ),
        ),
        (
            SYSTEM_REFERENDUM,
            {"formula": "referendum"},
            "A question put to all voters; the answer with the most votes carries.",
        ),
        (
            SYSTEM_BLOCK_COMMUNE,
            {"formula": "block"},
            (
                "Councils of communes with fewer than 3,000 inhabitants are elected by majority vote: voters have as "
                "many votes as there are seats, and the candidates with the most votes are elected."
            ),
        ),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=LUXEMBOURG, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


def council(commune: str) -> str:
    return key("body", "council", slug(commune_name(commune)))


# Elections.


class Facts:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str | None, int, int | None]] = []

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is not None:
            self.rows.append((result_set, unit, option, measure, value))

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "result_set_id": [r[0] for r in self.rows],
                "unit_id": [r[1] for r in self.rows],
                "option_id": [r[2] for r in self.rows],
                "channel": [0] * len(self.rows),
                "measure": [r[3] for r in self.rows],
                "count_round": [0] * len(self.rows),
                "flags": [0] * len(self.rows),
                "value": [r[4] for r in self.rows],
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        )


def build(day: str) -> Package:
    first = date.fromisoformat(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"Luxembourg's election{'s' if len(DAYS[day]) > 1 else ''} of {day} by commune, from the elections site's "
        "results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {LUXEMBOURG, COUNTRY}
    units: dict[str, str] = {}
    for name in DAYS[day]:
        for artifact in sources_of(name):
            p.uses(artifact)
        if family_of(name) == "referendum":
            _referendum(p, name, first, units)
        else:
            _election(p, name, first, units)
    _accept(p, day)
    return p


def sources_of(name: str) -> list[raw.Artifact]:
    if name in FILES:
        return [raw.cached(SOURCE, url(name)), *[raw.cached(SOURCE, link) for link in merged_pages(name)]]
    return [raw.cached(SOURCE, link) for link in [*index_pages(name), *extra_pages(name), *commune_pages(name)]]


def _accept(p: Package, day: str) -> None:
    if any(family_of(name) != "referendum" for name in DAYS[day]):
        p.accept(
            "accounting.votes_valid",
            (
                "each voter has as many votes as the contest has seats, so the votes for lists add up to more than the "
                "valid ballots; both are stored as the authority prints them"
            ),
        )
    for reason in ACCEPTED.get(day, []):
        p.accept(*reason)


def _units(p: Package, day: str, found: list[Place], units: dict[str, str]) -> None:
    for place in found:
        if slug(place.name) in units:
            continue
        unit = units[slug(place.name)] = key("unit", f"{day}:{slug(place.name)}")
        postal = place.name.startswith(POSTAL)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LUXEMBOURG,
            scheme_code="lu.count",
            code=f"{day}:{slug(place.name)}",
            area_relation="unlocated" if postal else "equals",
            area_id=None if postal else area("lu.commune", place.name),
            kind="postal_district" if postal else "standard",
        )
        p.name(unit, place.name, "fr", "official")


def _election(p: Package, name: str, first: date, units: dict[str, str]) -> None:
    family = family_of(name)
    english, french, luxembourgish = NAMES[family]
    day = first.isoformat()
    election = key("election", name)
    p.add(
        "election",
        id=election,
        polity_id=LUXEMBOURG,
        kind="local" if family == "communal" else "general",
        date_from=first,
        date_to=first,
        status="held",
    )
    p.name(election, f"{first.year} Luxembourg {english}", "en", "official")
    p.name(election, f"{french} {first.year}", "fr", "native", False)
    p.name(election, f"{luxembourgish} {first.year}", "lb", "native", False)
    path = f"lu/{day}-{family}"
    p.add("slug", path=path, entity_id=election, is_current=True)
    for race in races(name):
        if race.entries or race.candidates or any(pl.entries or pl.candidates for pl in race.places):
            _race(p, name, first, election, path, race, units)


def _result_set(p: Package, name: str, election: str, contest: str, tag: str) -> str:
    result_set = key("result_set", name, tag)
    p.add(
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
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return result_set


def _race(p: Package, name: str, first: date, election: str, path: str, race: Race, units: dict[str, str]) -> None:
    family = family_of(name)
    tag = slug(race.name)
    contest = key("contest", name, tag)
    if family == "legislative":
        label = f"{first.year} Luxembourg legislative election, {race.name} constituency"
        district = area("lu.circonscription", race.name)
        system, body, where = SYSTEM_LEGISLATIVE, CHAMBER, tag
    elif family == "european":
        label = f"{first.year} Luxembourg European Parliament election"
        district, system, body, where = COUNTRY, SYSTEM_EUROPEAN, None, "national"
    else:
        label = f"{first.year} {race.name} communal election"
        district = area("lu.commune", race.places[0].name)
        system = SYSTEM_LIST_COMMUNE if race.mode == "PROPORTIONNEL" or race.entries else SYSTEM_BLOCK_COMMUNE
        body, where = council(race.places[0].name), tag
    proportional = race.mode == "PROPORTIONNEL" or bool(race.entries)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation" if proportional else "body_seats",
        body_id=body,
        district_area_id=district,
        magnitude=race.seats,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, label, "en", "official")
    p.add("slug", path=f"{path}/{where}", entity_id=contest, is_current=True)
    result_set = _result_set(p, name, election, contest, tag)
    values = Facts()
    lists = {entry.number: entry for entry in race.entries}
    for place in race.places:
        for entry in place.entries:
            lists.setdefault(entry.number, Entry(entry.number, entry.name, entry.sigle))
    list_option: dict[int, str] = {}
    candidate_option: dict[tuple[int, str], str] = {}
    for number_, entry in sorted(lists.items()):
        option = list_option[number_] = key("option", name, tag, "list", number_)
        found = list_party(entry, family)
        party = party_id(found) if found else None
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=number_,
            ballot_label=entry.name or entry.sigle,
        )
        people: dict[str, Candidate] = {c.name: c for c in entry.candidates}
        for place in race.places:
            for held in place.entries:
                if held.number == number_:
                    for c in held.candidates:
                        people.setdefault(c.name, c)
        for position, (who, candidate) in enumerate(people.items(), 1):
            person = key("person", name, tag, number_, who)
            p.add("person", id=person)
            p.name(person, who, "fr", "official")
            sub = candidate_option[(number_, who)] = key("option", name, tag, "candidate", number_, who)
            p.add(
                "contest_option",
                id=sub,
                contest_id=contest,
                kind="candidate",
                parent_option_id=option,
                party_id=party,
                ballot_position=candidate.number if name in FILES else position,
                ballot_label=who,
            )
            candidacy = key("candidacy", name, tag, number_, who)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=candidate.number if name in FILES else position,
                ballot_name=who,
                member_of_party_id=party,
            )
            if candidate.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
        if entry.seats is not None:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if entry.seats else "lost",
                seats=entry.seats,
            )
    plain: dict[int, str] = {}
    elected: dict[int, tuple[str, bool]] = {c.number: (c.name, c.elected) for c in race.candidates}
    for place in race.places:
        for c in place.candidates:
            elected.setdefault(c.number, (c.name, c.elected))
    for position, (ballot, (who, won)) in enumerate(elected.items(), 1):
        person = key("person", name, tag, "plain", ballot)
        p.add("person", id=person)
        p.name(person, who, "fr", "official")
        option = plain[ballot] = key("option", name, tag, "candidate", ballot)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=who,
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, tag, "plain", ballot),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=who,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
    _units(p, first.isoformat(), race.places, units)
    for place in race.places:
        unit = units[slug(place.name)]
        _stats(values, result_set, unit, place.stats)
        for entry in place.entries:
            option = list_option[entry.number]
            values.add(result_set, unit, option, VOTES, entry.total)
            values.add(result_set, unit, option, LIST_VOTES, entry.listed)
            values.add(result_set, unit, option, PREFERENCE, entry.personal)
            for candidate in entry.candidates:
                values.add(
                    result_set, unit, candidate_option[(entry.number, candidate.name)], CANDIDATE_VOTES, candidate.votes
                )
        for candidate in place.candidates:
            values.add(result_set, unit, plain[candidate.number], VOTES, candidate.votes)
    p.frame("result_value", values.frame())
    if race.printed:
        _declared(p, result_set, race, list_option, candidate_option, plain)
    print(f"{p.key}: {race.name}: {len(race.places)} communes, {len(values.rows):,} values", flush=True)


def _stats(values: Facts, result_set: str, unit: str, stats: Stats) -> None:
    for measure, value in (
        (REGISTERED, stats.registered),
        (CAST, stats.cast),
        (VALID, stats.valid),
        (INVALID, stats.invalid),
        (BLANK, stats.blank),
    ):
        values.add(result_set, unit, None, measure, value)


def _declared(
    p: Package,
    result_set: str,
    race: Race,
    list_option: dict[int, str],
    candidate_option: dict[tuple[int, str], str],
    plain: dict[int, str],
) -> None:
    for measure, value in (
        (REGISTERED, race.stats.registered),
        (CAST, race.stats.cast),
        (VALID, race.stats.valid),
        (INVALID, race.stats.invalid),
        (BLANK, race.stats.blank),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for entry in race.entries:
        if entry.total is not None:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=list_option[entry.number],
                measure=VOTES,
                value=entry.total,
            )
        for candidate in entry.candidates:
            if candidate.votes is not None:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=candidate_option[(entry.number, candidate.name)],
                    measure=CANDIDATE_VOTES,
                    value=candidate.votes,
                )
    for candidate in race.candidates:
        if candidate.votes is not None:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=plain[candidate.number],
                measure=VOTES,
                value=candidate.votes,
            )


def _referendum(p: Package, name: str, first: date, units: dict[str, str]) -> None:
    day = first.isoformat()
    english, french, luxembourgish = NAMES["referendum"]
    election = key("election", name)
    p.add(
        "election",
        id=election,
        polity_id=LUXEMBOURG,
        kind="referendum",
        date_from=first,
        date_to=first,
        status="held",
    )
    p.name(election, f"{first.year} Luxembourg {english}", "en", "official")
    p.name(election, f"{french} {first.year}", "fr", "native", False)
    p.name(election, f"{luxembourgish} {first.year}", "lb", "native", False)
    path = f"lu/{day}-referendum"
    p.add("slug", path=path, entity_id=election, is_current=True)
    found = read_referendum(name)
    _units(p, day, [place for place, _ in found], units)
    for question, text in enumerate(QUESTIONS, 1):
        contest = key("contest", name, question)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=COUNTRY,
            round_number=1,
            electoral_system_id=SYSTEM_REFERENDUM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, f"{first.year} Luxembourg referendum, question {question}", "en", "official")
        p.name(contest, text, "fr", "native", False)
        p.add("slug", path=f"{path}/question-{question}", entity_id=contest, is_current=True)
        result_set = _result_set(p, name, election, contest, str(question))
        options = []
        for position, (answer, label) in enumerate((("yes", "Oui"), ("no", "Non")), 1):
            option = key("option", name, question, answer)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, answer.title(), "en", "english")
        values = Facts()
        for place, answers in found:
            unit = units[slug(place.name)]
            _stats(values, result_set, unit, place.stats)
            for option, count in zip(options, answers[question - 1], strict=True):
                values.add(result_set, unit, option, VOTES, count)
        p.frame("result_value", values.frame())
        for measure, count in national_referendum(name, question).items():
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[0] if measure == "yes" else options[1],
                measure=VOTES,
                value=count,
            )
        yes, no = (sum(a[question - 1][i] or 0 for _, a in found) for i in (0, 1))
        winner = options[0] if yes > no else options[1]
        for option in options:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if option == winner else "lost")
        print(f"{p.key}: question {question}: {len(found)} communes, {yes:,} yes, {no:,} no", flush=True)
