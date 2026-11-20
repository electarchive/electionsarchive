"""Friuli-Venezia Giulia regional council and presidential elections, 1998 to 2023, at region level.

The Region's election portals publish each election as JSON or pages for the whole region and for every
electoral district (circoscrizione) and municipality. This pipeline loads the regional totals, on the region's
existing reporting unit (it.regione 06):

- 1998 to 2013: the history portal's JSON, one row for each municipality, district, and the region (structure 6).
- 2018: the history portal's results pages for the region (candidates, groups of lists, list votes, seats,
  turnout).
- 2023: the live portal's JSON files for the region (structure 6).

The council was elected in 1998 on lists alone, and from 2003 together with a directly elected President.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "it.fvg"
ITALY = str(uuid5(package.NAMESPACE, "polity\x1fIT"))
REGION_POLITY = "003af61b-db4b-53d0-a3ab-6b17ba0520dd"
REGION_AREA = "01a13bf7-2f89-7329-a8e6-bbfa2ff5d618"
REGION_UNIT = "19499865-133c-570b-9c8f-e3863b8eadae"
HOME = "https://elezioni.regione.fvg.it/"
ARCHIVE = "https://elezionistorico.regione.fvg.it/distribuzione/json-storico/"
PAGES = (
    "https://elezionistorico.regione.fvg.it/elezioni/elezioni2018/000497_Reg/"
    "{Candidature,Affluenza,Coalizioni,Gruppi,Liste,Seggi,Consiglio}/{000001..000006}.html"
)
LIVE = "https://elezioni.regione.fvg.it/distribuzione/json/"
LIVE_ID = "2023-04-02_ELZ_REG_6_"
LIVE_ELECTIONS = LIVE + "elezioni.json"
DISTRICTS = {"Trieste", "Gorizia", "Udine", "Tolmezzo", "Pordenone"}
STRUCTURE = 6


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
COUNCIL = key("body", "consiglio")
PRESIDENCY = key("office", "presidente")

# The same party under the labels the ballots printed in different years.
PARTIES = {
    "FORZA ITALIA": ("Forza Italia", "party"),
    "F.I.": ("Forza Italia", "party"),
    "LEGA NORD": ("Lega Nord", "party"),
    "L.N.": ("Lega Nord", "party"),
    "A.N.": ("Alleanza Nazionale", "party"),
    "AN": ("Alleanza Nazionale", "party"),
    "D.S.": ("Democratici di Sinistra", "party"),
    "PARTITO DEMOCRATICO": ("Partito Democratico", "party"),
    "U.D.C.": ("Unione di Centro", "party"),
    "UNIONE DI CENTRO": ("Unione di Centro", "party"),
    "UNIONE DEI DEMOCR. CRISTIANI E DEMOCR. DI CENTRO": ("Unione di Centro", "party"),
    "IL POPOLO DELLA LIBERTA'": ("Il Popolo della Libertà", "party"),
    "POPOLO DELLA LIBERTA' PER TONDO PRESIDENTE": ("Il Popolo della Libertà", "party"),
    "SLOVENSKA SKUPNOST": ("Slovenska Skupnost", "party"),
    "MOVIMENTO 5 STELLE": ("Movimento 5 Stelle", "party"),
    "AUTONOMIA RESPONSABILE": ("Autonomia Responsabile", "party"),
    "PARTITO PENSIONATI": ("Partito Pensionati", "party"),
    "PENSIONATI": ("Partito Pensionati", "party"),
    "RIFONDAZIONE": ("Rifondazione Comunista", "party"),
    "RIF.COMUN.": ("Rifondazione Comunista", "party"),
    "VERDI": ("Verdi", "party"),
    "F. VERDI": ("Verdi", "party"),
    "ITALIA DEI VALORI - LISTA DI PIETRO": ("Italia dei Valori", "party"),
    "LISTA DI PIETRO": ("Italia dei Valori", "party"),
    "FRATELLI D'ITALIA": ("Fratelli d'Italia", "party"),
    "FRATELLI D'ITALIA - ALLEANZA NAZIONALE": ("Fratelli d'Italia - Alleanza Nazionale", "party"),
}


@dataclass
class Race:
    """The regional figures of one election."""

    day: date
    files: list[str]
    registered: int | None
    cast: int
    blank: int
    null: int
    contested: int
    candidates: list[tuple[str, int]]
    lists: list[tuple[str, int, int | None]]
    list_valid: int | None = None
    list_blank: int | None = None
    list_invalid: int | None = None
    seats: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.day.year}"


def number(text: str) -> int:
    return int(text.replace(".", "").replace(",", "").strip())


def label(text: str) -> str:
    return " ".join(text.split())


def stored(url: str) -> object:
    return json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))


def archive(day: date) -> Race:
    """1998 to 2013: the region's row of the history portal's regional JSON."""
    data = stored(ARCHIVE)
    assert isinstance(data, dict)
    rows = data[f"{ARCHIVE}a_{day}_ELZ_REG_6.json"]
    row = next(r for r in rows if r["idStrutturaInteressata"] == STRUCTURE)
    candidates: list[tuple[str, int]] = []
    lists: list[tuple[str, int, int | None]] = []
    for premier in row["premierEletti"]:
        name = label(premier["nominativoElettorale"])
        if name != "- -":
            candidates.append((name, premier["votiValidi"]))
        lists.extend((label(item["nomeLista"]), item["votiValidi"], item["numeroSeggi"]) for item in premier["liste"])
    race = Race(
        day,
        [ARCHIVE],
        row["numElettori"],
        row["numVotanti"],
        row["numSchedeBianche"],
        row["numSchedeNulle"],
        row["numVotiContestati"],
        candidates,
        lists,
    )
    if day.year == 1998:
        # The portal's seat column sums to 75 for the 1998 council, which the Region's own report of the election
        # puts at 60 seats, so no seats are taken from it.
        race.lists = [(name, votes, None) for name, votes, _ in lists]
        race.notes.append(
            "The portal's seat column for 1998 does not match the council the Region's report describes, so seats "
            "are left out."
        )
    else:
        race.seats = sum(seats or 0 for _, _, seats in lists)
    return race


def page(name: str) -> str:
    """The text of one of the 2018 region pages, with a `|` after each table cell."""
    text = raw.cached(SOURCE, PAGES).path.read_text(encoding="utf-8", errors="replace")
    parts = re.split(r"<!-- SOURCE: (\S+) -->", text)
    body = {parts[at].split("000497_Reg/")[1]: parts[at + 1] for at in range(1, len(parts), 2)}[name]
    body = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<head>.*?</head>|<!--.*?-->", "", body)
    body = re.sub(r"</t[dh]>", "|", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", body)


def pages() -> Race:
    """2018: the region pages for the candidates, the lists, the seats, and the turnout."""
    candidates = page("Coalizioni/000001.html")
    start = candidates.index("percentuale|")
    figures = candidates[start + len("percentuale|") :]
    people = [
        (label(match[1]), number(match[2]))
        for match in re.finditer(r"([A-ZÀ-Ý][A-ZÀ-Ý' ]+?) \| (?:[^|]*?) \| ([\d.]+) \| [\d,]+% \|", figures)
    ]
    trailer = {
        name: number(value)
        for name, value in re.findall(r"(Schede bianche|Schede nulle|Voti contestati e non assegnati|Totale votanti): ([\d.]+)", figures)
    }
    votes = page("Liste/000001.html")
    list_votes = {
        label(match[1]): number(match[2])
        for match in re.finditer(r"\| ([A-Z][^|]+?) \| ([\d.]+) \| [\d,]+% \|", votes[votes.index("percentuale|") :])
    }
    list_trailer = {
        name: number(value)
        for name, value in re.findall(r"(Schede bianche|Schede nulle|Voti contestati e non assegnati|Totale voti validi): ([\d.]+)", votes)
    }
    seats = page("Seggi/000001.html")
    seat_rows = {label(m[1]): (number(m[2]), int(m[3])) for m in re.finditer(r"\| ([A-Z][^|]+?) \| \| ([\d.]+) \| (\d+) \|", seats)}
    if not seat_rows:
        seat_rows = {
            label(m[1]): (number(m[2]), int(m[3])) for m in re.finditer(r"([A-Z][^|]+?)\| +\| ([\d.]+)\| (\d+)", seats)
        }
    turnout = page("Affluenza/000001.html")
    registered = [number(m[1]) for m in re.finditer(r"TOTALE \| [\d,]+ % \| [\d./]+ \| -\| -\| [\d.]+ \| ([\d.]+)", turnout)]
    assert len(people) == 4 and list_votes.keys() == seat_rows.keys(), (people, list_votes.keys(), seat_rows.keys())
    for name, (voted, _) in seat_rows.items():
        assert list_votes[name] == voted, name
    return Race(
        date(2018, 4, 29),
        [PAGES],
        registered[-1],
        trailer["Totale votanti"],
        trailer["Schede bianche"],
        trailer["Schede nulle"],
        trailer["Voti contestati e non assegnati"],
        people,
        [(name, votes_, seat_rows[name][1]) for name, votes_ in list_votes.items()],
        list_valid=list_trailer["Totale voti validi"],
        list_blank=list_trailer["Schede bianche"],
        list_invalid=list_trailer["Schede nulle"] + list_trailer["Voti contestati e non assegnati"],
        seats=sum(seat for _, (_, seat) in seat_rows.items()),
    )


def live() -> Race:
    """2023: the live portal's regional rows."""
    data = stored(LIVE)
    assert isinstance(data, dict)

    def region(name: str) -> list[dict[str, object]]:
        return [r for r in data[f"{LIVE}{LIVE_ID}{name}.json"] if r["id_struttura"] == STRUCTURE]

    head = region("intestazione_voti_presidenti")[0]
    heads = region("intestazione_voti_liste")[0]
    candidates = [(label(str(r["nominativo"])), int(str(r["voti_validi"]))) for r in region("voti_presidenti")]
    # The list rows hold each list's regional total under its own name and its votes in each district under the
    # district's name.
    lists = [
        (label(str(r["nome"])), int(str(r["voti_validi"])), None)
        for r in region("voti_liste")
        if r["nome"] not in DISTRICTS
    ]
    assert sum(votes for _, votes, _ in lists) == heads["voti_validi"]
    return Race(
        date(2023, 4, 2),
        [LIVE],
        None,
        int(str(head["totale"])),
        int(str(head["schede_bianche"])),
        int(str(head["voti_nulli"])),
        int(str(head["voti_contestati"])),
        candidates,
        lists,
        list_valid=int(str(heads["voti_validi"])),
        list_invalid=int(str(heads["voti_contestati"])) + int(str(heads["voti_nulli"])),
    )


RACES = {
    date(1998, 6, 14): lambda: archive(date(1998, 6, 14)),
    date(2003, 6, 8): lambda: archive(date(2003, 6, 8)),
    date(2008, 4, 13): lambda: archive(date(2008, 4, 13)),
    date(2013, 4, 21): lambda: archive(date(2013, 4, 21)),
    date(2018, 4, 29): pages,
    date(2023, 4, 2): live,
}
STAMP = datetime(2026, 11, 20)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day.year}" for day in RACES)]


def fetch() -> None:
    """The portals answer only a script-running browser, so the raw files are stored from saved copies."""
    raw.fetch(SOURCE, LIVE_ELECTIONS, suffix=".json")


def parse() -> None:
    registry().write()
    for day in RACES:
        election(RACES[day]()).write()


def emit() -> None:
    package.run("load", packages())


def title(text: str) -> str:
    small = {"di", "del", "della", "dei", "per", "e", "in", "il", "la", "le", "da"}
    words = []
    for at, word in enumerate(text.lower().split()):
        words.append(word if at and word in small else word[:1].upper() + word[1:])
    return " ".join(words)


def party_of(text: str) -> tuple[str, str, str]:
    """The party's key, name, and kind for a list label; a list named for a candidate is a list vehicle."""
    if text in PARTIES:
        name, kind = PARTIES[text]
    else:
        name, kind = title(text), "list_vehicle"
    return key("party", name), name, kind


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Friuli-Venezia Giulia regional council, the presidency, and the lists on regional ballots, 1998 to 2023",
    )
    p.external |= {ITALY, REGION_POLITY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Regione autonoma Friuli Venezia Giulia",
        title="Elezioni regionali: risultati (portale elezioni e archivio storico)",
        url=HOME,
        attribution="Regione autonoma Friuli Venezia Giulia",
    )
    for url in (ARCHIVE, PAGES, LIVE, LIVE_ELECTIONS):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=COUNCIL, polity_id=REGION_POLITY, kind="unicameral")
    p.name(COUNCIL, "Regional Council of Friuli-Venezia Giulia", "en", "official")
    p.name(COUNCIL, "Consiglio regionale del Friuli Venezia Giulia", "it", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=REGION_POLITY, kind="governor")
    p.name(PRESIDENCY, "President of Friuli-Venezia Giulia", "en", "official")
    p.name(PRESIDENCY, "Presidente della Regione Friuli Venezia Giulia", "it", "native", False)
    seen: set[str] = set()
    for day in RACES:
        for text, *_ in RACES[day]().lists:
            id, name, kind = party_of(text)
            if id in seen:
                continue
            seen.add(id)
            p.add("party", id=id, kind=kind, polity_id=ITALY)
            p.name(id, name, "it", "official")
    return p


def method(race: Race) -> str:
    text = (
        "Results for the whole region, as the Region's election portal prints them for the regional unit "
        "(structure 6), stored on the region's reporting unit. Invalid ballots are the null ballots and the "
        "contested and unassigned votes together."
    )
    return " ".join([text, *race.notes])


def values(p: Package, result_set: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=REGION_UNIT,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def election(race: Race) -> Package:
    day, year = race.day, race.day.year
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {day} Friuli-Venezia Giulia regional election, council lists and President, for the whole region",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {ITALY, REGION_POLITY, REGION_AREA, REGION_UNIT}
    for url in race.files:
        p.uses(raw.cached(SOURCE, url))
    id = key("election", year)
    p.add("election", id=id, polity_id=REGION_POLITY, kind="general", date_from=day, date_to=day, status="held")
    p.name(id, f"{year} Friuli-Venezia Giulia regional election", "en", "official")
    p.name(id, f"Elezioni regionali del Friuli Venezia Giulia {year}", "it", "native", False)
    path = f"it/{day}-friuli-venezia-giulia-regional"
    p.add("slug", path=path, entity_id=id, is_current=True)

    def contest_of(kind: str, name: str, it: str, slug: str, **row: object) -> tuple[str, str]:
        contest = key("contest", year, slug)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind=kind,
            district_area_id=REGION_AREA,
            default_result_kind="counted",
            **row,
        )
        p.name(contest, f"{year} {name}", "en", "official")
        p.name(contest, f"{it} {year}", "it", "native", False)
        p.add("slug", path=f"{path}/{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, slug)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method(race), lang="en"
        )
        return contest, result_set

    council, lists_set = contest_of(
        "list_allocation",
        "Friuli-Venezia Giulia Regional Council",
        "Consiglio regionale",
        "council",
        body_id=COUNCIL,
        magnitude=race.seats,
    )
    options: dict[str, str] = {}
    for position, (text, votes, seats) in enumerate(race.lists, start=1):
        party, *_ = party_of(text)
        option = options[text] = key("option", year, "list", position)
        p.add(
            "contest_option",
            id=option,
            contest_id=council,
            kind="list",
            party_id=party,
            ballot_position=position,
            ballot_label=text,
        )
        values(p, lists_set, option, 1, votes)
        if seats:
            p.add("outcome", result_set_id=lists_set, option_id=option, kind="won", seats=seats)
    values(p, lists_set, None, 4, race.list_valid)
    values(p, lists_set, None, 6, race.list_blank)
    values(p, lists_set, None, 5, race.list_invalid)

    invalid = race.null + race.contested
    totals = {2: race.registered, 3: race.cast, 5: invalid, 6: race.blank}
    if not race.candidates:
        for measure, value in totals.items():
            values(p, lists_set, None, measure, value)
        return p
    president, president_set = contest_of(
        "office",
        "Friuli-Venezia Giulia presidential election",
        "Presidente della Regione",
        "president",
        office_id=PRESIDENCY,
        magnitude=1,
    )
    leader = max(votes for _, votes in race.candidates)
    for position, (name, votes) in enumerate(race.candidates, start=1):
        person = key("person", year, name)
        p.add("person", id=person)
        p.name(person, title(name), "it", "official")
        option = key("option", year, "president", position)
        p.add(
            "contest_option",
            id=option,
            contest_id=president,
            kind="candidate",
            ballot_position=position,
            ballot_label=title(name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", year, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=title(name),
        )
        p.add("outcome", result_set_id=president_set, option_id=option, kind="elected" if votes == leader else "lost")
        values(p, president_set, option, 1, votes)
    valid = sum(votes for _, votes in race.candidates)
    gap = race.cast - race.blank - invalid - valid
    for measure, value in totals.items():
        values(p, president_set, None, measure, value)
    if gap == 0:
        values(p, president_set, None, 4, valid)
    elif abs(gap) <= 10:
        values(p, president_set, None, 4, valid)
        p.accept(
            "accounting.ballots_sum",
            f"the portal's regional row prints {race.cast:,} ballots, {race.blank:,} blank, {invalid:,} invalid, and "
            f"{valid:,} votes for the candidates, {abs(gap)} apart, as printed",
        )
    else:
        p.add(
            "note",
            id=key("note", president_set, "valid"),
            entity_id=president_set,
            kind="caveat",
            body_md=f"Votes for the candidates add up to {valid:,}, {gap:,} fewer than the ballots cast less the blank "
            "and invalid ones: ballots could carry a vote for a list alone. Valid presidential votes are therefore "
            "not stored as a total.",
            lang="en",
        )
    return p
