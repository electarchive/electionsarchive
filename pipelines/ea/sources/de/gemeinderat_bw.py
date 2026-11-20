"""Municipal council elections in Baden-Württemberg, 1975 to 2024, by municipality.

The Statistisches Landesamt Baden-Württemberg publishes its Gemeinderatswahlstatistik in its GENESIS database
(statistic 14431). Table 14431-0001 gives each municipality's electorate, voters, and valid and invalid ballots, table
14431-0002 the valid votes of every party, joint list, and category of voters' associations, and table 14431-0004
the seats each won, for every municipality on its current boundaries. Votes and ballots go back to 1975; seats by
municipality start in 1989.

The database answers through a session-bound form: a request picks the election day and, for the large votes table,
the municipalities of one Kreis at a time, and then downloads the result as the database's semicolon-separated
table (dcsv). Each download is cached under a key that names the table, the day, and the Kreis.
"""

import csv
import html
import io
import re
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from time import sleep

import httpx

from ea import package, raw
from ea.raw import Artifact
from ea.sources.de import gemeinderat as common
from ea.sources.de.gemeinderat import Entry, Land, Result, Vote, count

SOURCE = "de.gemeinderat_bw"
ONLINE = "https://daten.statistik-bw.de/genesisonline/online"
DAYS = [
    date(1975, 4, 20),
    date(1980, 6, 22),
    date(1984, 10, 28),
    date(1989, 10, 22),
    date(1994, 6, 12),
    date(1999, 10, 24),
    date(2004, 6, 13),
    date(2009, 6, 7),
    date(2014, 5, 25),
    date(2019, 5, 26),
    date(2024, 6, 9),
]
SEATS_FROM = date(1989, 10, 22)
SCHEPERS_FROM = date(2014, 5, 25)
TURNOUT, VOTES, SEATS = "0001", "0002", "0004"
# The position of the municipality variable in each table's layout form.
GEMEINDEN = {VOTES: 6}
TOTAL = "Insgesamt"
ALL_GROUPS = "Wählervereinigungen insgesamt"
OTHER_GROUPS = "Übrige Wählervereinigungen"
NO_SPLIT = "Stimmen ohne Aufgliederung nach Wahlvorschlägen"
MAJORITY = "Mehrheitswahl"
PROPORTIONAL = "Verhältniswahl"
# Parties production already holds, by the Landesamt's label.
HELD = {
    "Christlich Demokratische Union Deutschlands (CDU)": "01a13bb5-c6e1-7410-816e-3b23101bdc3f",
    "Sozialdemokratische Partei Deutschlands (SPD)": "01a13bb5-c6e1-7410-816e-3b22bd40874f",
    "BÜNDNIS 90/DIE GRÜNEN": "01a13bb5-c6e1-7410-816e-3b24305503a8",
    "Freie Demokratische Partei (FDP)": "01a13bb5-c6e1-7410-816e-3b2599d640df",
    "Alternative für Deutschland (AfD)": "01a13bb5-c6e1-7410-816e-3b26fc9b8ec8",
    "Die Linke": "01a13bb5-c6e1-7410-816e-3b28cf8ec1ca",
    "Volt Deutschland": "01a13bb5-c6e1-7410-816e-3b2f89b4ed1c",
    "Partei für Arbeit, Rechtsstaat, Tierschutz, Elitenförderung und basisdemok. Initiative (Die PARTEI)": (
        "01a13bb5-c6e1-7410-816e-3b2cfa7dc0d3"
    ),
    "PARTEI MENSCH UMWELT TIERSCHUTZ (Tierschutzpartei)": "01a13bb5-c6e1-7410-816e-3b2ac2f89ea1",
    "Ökologisch-Demokratische Partei (ÖDP)": "01a13bb5-c6e1-7410-816e-3b306b9f0b7e",
    "Piratenpartei Deutschland (PIRATEN)": "01a13bb5-c6e1-7410-816e-3b2e34cdd413",
    "DEMOKRATIE IN BEWEGUNG (DiB)": "25584173-d78e-5f89-a6fd-5feb98995c5a",
    "Bündnis für Innovation & Gerechtigkeit (BIG)": "01a13bc4-54a3-704e-abec-9cd940d8cde0",
    "FREIE WÄHLER": "01a13bb5-c6e1-7410-816e-3b29049511af",
    "Deutsche Kommunistische Partei (DKP)": "01a13bc4-54a3-704e-abec-9cdae4e9f823",
    "DIE REPUBLIKANER (REP)": "9c1a8c34-a74a-5edc-b337-02c030a78095",
    "DEUTSCHE VOLKSUNION (DVU)": "54f2240d-0ab1-5364-aec4-ba6284428d62",
    "BÜNDNIS DEUTSCHLAND": "01a13bb5-c6e1-7410-816e-3b3b7436e814",
    "Bündnis C - Christen für Deutschland": "01a13bb5-c6e1-7410-816e-3b34116d42ea",
    "Kommunistische Partei Deutschlands (KPD)": "f23d9f66-3200-5418-a24a-fe201cd83684",
    "Block der Heimatvertriebenen und Entrechteten (BHE)": "77d7c637-f0b5-5fad-b6d1-1d6423756ba9",
    "Europäische Arbeiterpartei (EAP)": "64bae3d4-7aee-598e-90bb-13e179e7dfc5",
    "Kommunistischer Bund Westdeutschland (KBW)": "ecf22af4-61ff-50e7-903c-19d324be6617",
    "Liberal-Konservative Reformer (LKR)": "44d553e0-09b0-5dc8-89c4-112935b38e65",
    "Marxistisch-Leninistische Partei Deutschlands (MLPD)": "01a13bb5-c6e1-7410-816e-3b36f02ce58f",
    "Partei Bibeltreuer Christen (PBC)": "283086aa-4a93-5295-9ccb-4f28587b2c41",
    "STATT Partei DIE UNABHÄNGIGEN": "f81e85e0-f915-5704-9dca-c2424e20d4e0",
    "NATURGESETZ PARTEI, AUFBRUCH ZU NEUEM BEWUSSTSEIN (NATURGESETZ)": "89d308b2-480c-55d2-bb16-87e2e385ff06",
    "Anarchistische Pogo-Partei Deutschlands (APPD)": "092c4187-2e4c-52bb-b6cd-90178a2e6a93",
    "Freiheitliche Deutsche Arbeiterpartei (FAP)": "cddc9916-5eb6-5adc-98a2-10a34572efce",
    "Liberale Demokraten (LD)": "c30edd9e-f93c-5f14-a998-7646da21233a",
    "Europäische Föderalistische Partei (EFP)": "3c428bd5-d561-5838-a6c1-3602faa9e37e",
    "Allianz für Menschenrechte, Tier- und Naturschutz (Tierschutzallianz)": "c49b55f7-1d33-526a-adbf-32dfe1df4b31",
    "Klimaliste": "01a13bc4-54a3-704e-abec-9cddfbf1e52f",
    "Deutsche Liga für Volk und Heimat (DLVH)": "bee269f9-d9c3-5089-af77-2f73c7f2748b",
    "Menschliche Welt - für das Wohl und Glücklichsein aller": "01a13bb5-c6e1-7410-816e-3b375f68f5f6",
    "Die Gerechtigkeitspartei - Team Todenhöfer": "01a13bb5-c6e1-7410-816e-3b2d22754384",
    "Gerechtigkeitspartei (G)": "9e0518b0-4fa9-51f7-bf6a-a60f9727a3ff",
    "BUND FREIER BÜRGER - OFFENSIVE FÜR DEUTSCHLAND, Die Freiheitlichen (BfB)": "2949dcb7-954e-5311-bd37-aaac71df5dca",
}
# Categories that pool many local lists across the Land: they name no single party.
CATEGORIES = {
    "Sonstige Wählervereinigungen",
    "Grüne Listen",
    "Linksorientierte Listen",
    "Frauenlisten",
    "Liste der Jungen Union",
}
INDEPENDENTS = "Einzelbewerber/-innen"
LAND = Land(
    source=SOURCE,
    code="08",
    slug="baden-wurttemberg",
    name="Baden-Württemberg",
    body="Gemeinderat",
    publisher="Statistisches Landesamt Baden-Württemberg",
    title="Gemeinderatswahlstatistik (GENESIS-Online, Statistik 14431)",
    url=f"{ONLINE}?operation=statistic&code=14431",
    license="Datenlizenz Deutschland, Namensnennung, Version 2.0",
    license_url="https://www.govdata.de/dl-de/by-2-0",
    attribution="Statistisches Landesamt Baden-Württemberg",
    method=(
        "Counted by the municipalities and published by the Statistisches Landesamt Baden-Württemberg for each "
        "municipality on its current boundaries. Each voter has as many votes as the council has seats, may give a "
        "candidate up to three of them, and may spread them over several lists, so a list's votes are the sum of "
        "its candidates' votes and are many times the number of voters. Registered voters are the electoral roll, "
        "voters those who took part, and valid and invalid ballots the ballot papers counted as such. Lists of "
        "voters' associations are printed by category (for example 'Sonstige Wählervereinigungen' or 'CDU und "
        "Wählervereinigungen'), each the sum of the municipality's lists of that kind, and independent candidates "
        "as one sum. Where only one list or none was submitted, the council is elected by majority vote and the "
        "source gives only the municipality's total votes, stored here as one option. Where the source prints a "
        "municipality's total votes without splitting them by list, the votes it does not split are stored as one "
        "option. Seats are as declared by the source, including any seats added to balance the representation of "
        "a municipality's districts (unechte Teilortswahl)."
    ),
    system_note=(
        "Each voter has as many votes as the council has seats, up to three for one candidate, which may be spread "
        "over several lists (panachage). Seats are shared among the lists in proportion to the sum of their "
        "candidates' votes: by the D'Hondt method until 2009 and by the Sainte-Laguë/Schepers method since 2014. "
        "Where only one list or none is submitted, the council is elected by majority vote. Municipalities with "
        "unechte Teilortswahl reserve seats for their districts and may add balancing seats. The declared seats are "
        "stored as published."
    ),
)
NOTES = {
    date(1975, 4, 20): "The 1975 figures include elections held early or late in municipalities merged in the reform.",
    date(2019, 5, 26): "The 2019 figures include the new election in Tauberbischofsheim on February 5, 2023.",
    date(2024, 6, 9): (
        "The 2024 figures include the partial repeat election in Rastatt on October 20, 2024 and the new election "
        "in Crailsheim on March 23, 2025."
    ),
}


def url(table: str, day: date, kreis: str | None = None) -> str:
    """The cache key of one download: the table, the election day, and the Kreis whose municipalities it holds."""
    where = f"&gemeinden={kreis}*" if kreis else ""
    return f"{ONLINE}?operation=table&code=14431-{table}&wahltag={day:%d.%m.%Y}{where}&format=dcsv"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day.year}" for day in DAYS)]


class Session:
    """One session of the database's table form."""

    def __init__(self) -> None:
        self.client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, timeout=300, follow_redirects=True)

    @staticmethod
    def _form(page: str) -> tuple[str, dict[str, object]]:
        for match in re.finditer(r"<form([^>]*)>(.*?)</form>", page, re.DOTALL):
            head, body = match.groups()
            if "sucheingabe" in head or "signUp" in head:
                continue
            fields: dict[str, object] = {}
            for field in re.finditer(r"<input([^>]*)>", body):
                attributes = field.group(1)
                name = re.search(r'name="([^"]+)"', attributes)
                if "hidden" in attributes and name:
                    value = re.search(r'value="([^"]*)"', attributes)
                    fields[name.group(1)] = html.unescape(value.group(1) if value else "")
            return ("post" if "post" in head else "get"), fields
        raise LookupError("the database answered without a form")

    def _submit(self, page: str, extra: dict[str, object]) -> str:
        method, fields = self._form(page)
        fields |= extra
        if method == "post":
            return self.client.post(ONLINE, data=fields).text
        return self.client.get(ONLINE, params={k: str(v) for k, v in fields.items()}).text

    def download(self, table: str, day: date, kreis: str | None) -> bytes:
        code = f"14431-{table}"
        page = self.client.get(ONLINE, params={"operation": "table", "code": code, "levelindex": "", "levelid": ""})
        page = self._submit(page.text, {"auspraegungen$$$2": "Zeit auswählen"})
        offered = re.findall(r'name="ausgewaehlte" value="(\d+)"', page)
        listed = re.findall(r"\b(\d\d\.\d\d\.\d{4})\b", re.sub(r"<[^>]+>", " ", page.split("Bestimmte", 1)[-1]))
        index = listed.index(f"{day:%d.%m.%Y}")
        page = self._submit(
            page, {"ziauswahl": "liste", "ausgewaehlte": offered[index], "auspraegungenWaehlen": "übernehmen"}
        )
        if kreis:
            page = self._submit(page, {f"auspraegungen$$${GEMEINDEN[table]}": "auswählen"})
            page = self._submit(page, {"code": f"{kreis}*", "kmaauswahl.x": "1", "kmaauswahl.y": "1"})
            page = self._submit(page, {"seitenlaenge": "100"})
            chosen = re.findall(r'name="newselected" value="(\d+)"', page)
            if not chosen or len(chosen) >= 100:
                raise ValueError(f"{code} {day}: {len(chosen)} municipalities match {kreis}")
            page = self._submit(page, {"newselected": chosen, "uebernehmen": "übernehmen"})
        page = self._submit(page, {"werteabruf": "starten"})
        link = re.search(r'(https://[^"\s]*ergebnistabelleDownload[^"\s]*option=dcsv)', page)
        if not link:
            raise LookupError(f"{code} {day} {kreis}: the database offered no download")
        content = self.client.get(html.unescape(link.group(1))).content
        if not content.startswith(f"Tabelle: {code}".encode()) or f"{day:%d.%m.%Y}".encode() not in content[:20000]:
            raise ValueError(f"{code} {day} {kreis}: the download is not the requested table")
        if kreis and f"\n{kreis}".encode() not in content:
            raise ValueError(f"{code} {day} {kreis}: the download holds none of the Kreis's municipalities")
        return content


def _fetch(jobs: list[tuple[str, date, str | None]], workers: int = 2) -> None:
    """Download the jobs not yet cached. Each worker keeps one session for all its downloads, since the database
    admits only so many guest sessions at once and answers the rest with a notice to try later."""
    known = raw.keys(SOURCE)
    pending = [job for job in jobs if url(*job) not in known]
    local = threading.local()
    failed: list[str] = []

    def one(job: tuple[str, date, str | None]) -> None:
        for attempt in range(8):
            if not hasattr(local, "session"):
                local.session = Session()
            try:
                content = local.session.download(*job)
            except httpx.HTTPError, LookupError, ValueError:
                local.session.client.close()
                del local.session
                sleep(20 * (attempt + 1))
                continue
            raw.store(
                SOURCE,
                url(*job),
                content,
                suffix=".csv",
                retrieved_at=datetime.now(UTC),
                media_type="text/comma-separated-values;charset=ISO-8859-1",
            )
            return
        failed.append(url(*job))

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, pending))
    if failed:
        raise LookupError(f"{len(failed)} downloads failed, first {failed[0]}")


def fetch() -> None:
    _fetch([(table, day, None) for day in DAYS for table in (TURNOUT, SEATS) if table == TURNOUT or day >= SEATS_FROM])
    _fetch([(VOTES, day, kreis) for day in DAYS for kreis in sorted({ags[:5] for ags in turnout(day)})])


def rows(artifact: Artifact) -> list[list[str]]:
    text = artifact.path.read_bytes().decode("latin-1")
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def label(text: str) -> str:
    return " ".join(text.split())


def turnout(day: date) -> dict[str, tuple[str, int | None, int | None, int | None, int | None]]:
    """Each municipality's name, electorate, voters, and valid and invalid ballots, in the table's order."""
    found: dict[str, tuple[str, int | None, int | None, int | None, int | None]] = {}
    for row in rows(raw.cached(SOURCE, url(TURNOUT, day))):
        if row and re.fullmatch(r"08\d{6}", row[0].strip()):
            found[row[0].strip()] = (label(row[1]), count(row[2]), count(row[3]), count(row[5]), count(row[7]))
    return found


def _header(table: list[list[str]]) -> int:
    """The index of the header row of list labels, which begins with blanks and then the total."""
    for at, row in enumerate(table):
        cells = [cell for cell in row if cell]
        if cells and cells[0] == TOTAL and row[0] == "":
            return at
    raise LookupError("the table has no header row of list labels")


def seats(day: date) -> dict[str, dict[str, int]]:
    """Each municipality's seats by list, with the council's size under 'Insgesamt'. The table names municipalities
    without their codes, in the order of the turnout table, so rows are matched in order and checked by name."""
    table = rows(raw.cached(SOURCE, url(SEATS, day)))
    head = _header(table)
    labels = [label(cell) for cell in table[head]]
    names = list(turnout(day).items())
    data = [row for row in table[head + 3 :] if len(row) > 2 and row[0].strip()]
    if label(data[0][0]) != "Baden-Württemberg":
        raise ValueError(f"{day}: the seats table does not begin with the Land")
    data = data[1 : 1 + len(names)]
    out: dict[str, dict[str, int]] = {}
    for (ags, (name, *_)), row in zip(names, data, strict=True):
        if label(row[0]) != name:
            raise ValueError(f"{day}: seats row {row[0]!r} does not match {ags} {name}")
        out[ags] = {labels[i]: n for i in range(1, len(labels)) if (n := count(row[i]) or 0)}
    return out


def votes_by_list(day: date) -> dict[str, dict[str, dict[str, int]]]:
    """Each municipality's valid votes by list, with the total under 'Insgesamt', by electoral system. In 1975 some
    municipalities on their current boundaries held both: a list vote in some former municipalities and a majority
    vote in others."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for kreis in sorted({ags[:5] for ags in turnout(day)}):
        table = rows(raw.cached(SOURCE, url(VOTES, day, kreis)))
        head = _header(table)
        labels = [label(cell) for cell in table[head]]
        measures = table[head + 1]
        columns = [i for i in range(3, len(labels)) if measures[i] == "Gültige Stimmen"]
        for row in table[head + 3 :]:
            ags = row[0].strip() if row else ""
            if not re.fullmatch(r"08\d{6}", ags) or not ags.startswith(kreis):
                continue
            values = {labels[i]: n for i in columns if (n := count(row[i])) is not None}
            if values:
                out.setdefault(ags, {})[row[2]] = values
    return out


def entries(day: date, ags: str, systems: dict[str, dict[str, int]], won: dict[str, int] | None) -> list[Entry]:
    votes = systems.get(PROPORTIONAL, {})
    majority = systems.get(MAJORITY, {}).get(TOTAL)
    if set(systems) - {PROPORTIONAL, MAJORITY}:
        raise ValueError(f"{day} {ags}: unknown electoral system in {sorted(systems)}")
    if any(name != TOTAL for name in systems.get(MAJORITY, {})):
        raise ValueError(f"{day} {ags}: list votes under a majority vote")
    total = votes.get(TOTAL)
    groups = sum(n for name, n in votes.items() if name in CATEGORIES or name.endswith("und Wählervereinigungen"))
    lists = sum(n for name, n in votes.items() if name not in (TOTAL, ALL_GROUPS))
    # The sum of all voters' associations is a list of its own where the other lists leave exactly its votes over.
    # Where it also holds the categories, the rest of it is the associations the source puts in no category.
    whole = votes.get(ALL_GROUPS)
    alone = total is not None and whole is not None and total - lists == whole
    skip = {TOTAL} if alone else {TOTAL, ALL_GROUPS}
    listed = [name for name in votes if name not in skip]
    if won:
        listed += [name for name in won if name not in skip and name not in votes]
    out: list[Entry] = []
    for name in listed:
        seats = won.get(name) if won else None
        if name in HELD:
            out.append(Entry(name, "P", votes.get(name), seats, HELD[name]))
        elif name in CATEGORIES or name == ALL_GROUPS or name.endswith("und Wählervereinigungen"):
            out.append(Entry(name, "OC", votes.get(name), seats))
        elif name == INDEPENDENTS:
            out.append(Entry(name, "OE", votes.get(name), seats))
        elif name.startswith("Gemeinsamer Wahlvorschlag"):
            out.append(Entry(name, "LV", votes.get(name), seats))
        else:
            out.append(Entry(name, "P", votes.get(name), seats))
    counted = sum(entry.votes or 0 for entry in out)
    if total is not None and counted > total:
        raise ValueError(f"{day} {ags}: list votes {counted} exceed the printed total {total}")
    given = won.get(TOTAL) if won else None
    spare = None if given is None else given - sum(entry.seats or 0 for entry in out)
    if spare is not None and spare < 0:
        raise ValueError(f"{day} {ags}: list seats exceed the printed council size {given}")
    rest = None if total is None else total - counted
    if majority is not None:
        # The seats the source gives no list are those won by majority vote.
        return [*out, Entry(f"{MAJORITY} (alle Bewerberinnen und Bewerber)", "MW", majority, spare or None)]
    if rest and whole is not None and groups and rest == whole - groups:
        out.append(Entry(OTHER_GROUPS, "OC", rest, spare or None))
    elif rest or spare:
        out.append(Entry(NO_SPLIT, "NA", rest or None, spare or None))
    return out


def vote(day: date) -> Vote:
    names = turnout(day)
    counted = votes_by_list(day)
    won = seats(day) if day >= SEATS_FROM else {}
    results: list[Result] = []
    for ags, (name, registered, voters, valid, invalid) in names.items():
        if ags not in counted:
            if registered is not None:
                raise ValueError(f"{day} {ags}: turnout printed but no votes")
            continue
        given = won.get(ags)
        results.append(
            Result(
                ags=ags,
                name=name,
                registered=registered,
                voters=voters,
                invalid=invalid,
                valid_ballots=valid,
                seats=given.get(TOTAL) if given else None,
                entries=entries(day, ags, counted[ags], given),
            )
        )
    files = [raw.cached(SOURCE, url(TURNOUT, day))]
    if day >= SEATS_FROM:
        files.append(raw.cached(SOURCE, url(SEATS, day)))
    files += [raw.cached(SOURCE, url(VOTES, day, kreis)) for kreis in sorted({ags[:5] for ags in names})]
    formula = "sainte_lague_schepers" if day >= SCHEPERS_FROM else "dhondt"
    return Vote(day, formula, results, files, NOTES.get(day))


def votes() -> Iterator[tuple[str, Vote]]:
    for day in DAYS:
        yield str(day.year), vote(day)


def parse() -> None:
    all_votes = dict(votes())
    common.geo(LAND, all_votes.values()).write()
    common.registry(LAND, all_votes.values()).write()
    for name, item in all_votes.items():
        common.election(LAND, item, name).write()


def emit() -> None:
    package.run("load", packages())
