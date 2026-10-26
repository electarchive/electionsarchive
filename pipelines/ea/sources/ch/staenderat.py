import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.ch.nationalrat import CANTONS, DAYS
from ea.store import DATA, connect

SOURCE = "ch.staenderat"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CANDIDATES = "https://dam-api.bfs.admin.ch/hub/api/dam/assets/28765111/master"
MEMBERS = (
    "https://ws.parlament.ch/odata.svc/MemberCouncil?$filter=Language%20eq%20'DE'"
    "&$select=ID,PersonNumber,FirstName,LastName,GenderAsString,DateOfBirth,CantonAbbreviation"
    "&$orderby=ID&$top=1000&$skip={}&$format=json"
)
PAGES = range(0, 5000, 1000)
PUBLISHER = "Federal Statistical Office"
TITLE = "Ständeratswahlen: Ergebnisse der Kandidierenden"
ALIASES = {
    "zurich": "ZH",
    "bern": "BE",
    "luzern": "LU",
    "uri": "UR",
    "schwyz": "SZ",
    "obwalden": "OW",
    "nidwalden": "NW",
    "glarus": "GL",
    "zug": "ZG",
    "freiburg": "FR",
    "fribourg": "FR",
    "solothurn": "SO",
    "baselstadt": "BS",
    "basellandschaft": "BL",
    "schaffhausen": "SH",
    "appenzellarh": "AR",
    "appenzellirh": "AI",
    "stgallen": "SG",
    "graubunden": "GR",
    "aargau": "AG",
    "thurgau": "TG",
    "tessin": "TI",
    "ticino": "TI",
    "waadt": "VD",
    "vaud": "VD",
    "wallis": "VS",
    "valais": "VS",
    "neuenburg": "NE",
    "neuchatel": "NE",
    "genf": "GE",
    "geneve": "GE",
    "jura": "JU",
}
LABELS = {
    "FDP": "FDP",
    "CVP": "CVP",
    "Die Mitte": "Mitte",
    "SP": "SP",
    "SPS": "SP",
    "SVP": "SVP",
    "GPS": "GP",
    "GP": "GP",
    "GRÜNE": "GP",
    "GLP": "GLP",
    "PdA": "PdA",
    "EVP": "EVP",
    "SD": "SD",
    "BDP": "BDP",
    "BPD": "BDP",
    "LPS": "LPS",
    "LP": "LPS",
    "FGA": "FGA",
    "EDU": "EDU",
    "Sol.": "Sol",
    "Lega": "Lega",
    "LEGA": "Lega",
    "LdU": "LdU",
    "MCR": "MCR",
    "MCG": "MCR",
    "FP": "FPS",
    "FPS": "FPS",
    "CSP": "CSP",
    "PCS": "CSP",
    "PdA/Sol.": "PdA-Sol",
    "PST/Sol.": "PdA-Sol",
    "DSP": "DSP",
    "PSU": "PSU",
    "Bunte Liste": "BuL",
}
NEW_PARTIES = {
    "PdA-Sol": ("PdA/Solidarität", "PdA/Sol.", "bloc", "de"),
    "DSP": ("Demokratisch-Soziale Partei", "DSP", "party", "de"),
    "PSU": ("Parti socialiste unifié", "PSU", "party", "fr"),
    "BuL": ("Bunte Liste", "Bunte Liste", "party", "de"),
}
UNAFFILIATED = {"Übrige", "parteilos", "Indépendent-e-s et sans partis", "Rauraque Indépendant"}
SCATTERED = {"Vereinzelte", "Verschiedene", "Voix éparses", "Vereinzelte (Übrige)"}
PARTICLES = {"de", "la", "von", "van", "di", "da", "dal", "del"}
LANDSGEMEINDE = {"OW", "NW", "AI"}
MISSPELLED = {"COUTEAU GILBERT": 51, "CAVELTI LUREGN MATHIAS": 43}
DATE_FIXES = {
    (1991, "ZH", 1): (
        "1991-10-20",
        "The Federal Statistical Office dates this vote 20 October 1990; it was held on 20 October 1991, the day "
        "of the federal election (Parliamentary Services, Ständeratswahlen 44. Legislatur).",
    ),
    (2003, "VD", 2): (
        "2003-11-09",
        "The Federal Statistical Office dates this second round 20 November 2003; it was held on 9 November 2003.",
    ),
}
TACIT_SECOND = {
    (2023, "BE"): ("2023-11-09", "day", set(), "the Bern Executive Council's notice of 9 November 2023"),
    (2019, "LU"): ("2019-10-01", "month", {"Müller Damian"}, "SRF and the Luzerner Zeitung, October 2019"),
    (2011, "LU"): ("2011-10-27", "day", set(), "20 Minuten, 27 October 2011"),
    (2007, "LU"): ("2007-10-25", "day", {"Leumann-Würsch Helen"}, "the Neue Zürcher Zeitung, 25 October 2007"),
    (2011, "VS"): ("2011-10-01", "month", set(), "Radio Télévision Suisse and Année politique suisse"),
    (2007, "VS"): ("2007-10-01", "month", {"Fournier Jean-René"}, "Agefi, 24 October 2007"),
    (2007, "FR"): ("2007-10-01", "month", {"Schwaller Urs"}, "the Fribourg State Chancellery"),
}
ANNULLED = {
    (2023, "SH"): (
        "Stocker Simon",
        "Simon Stocker was declared elected in the second round. The Federal Supreme Court annulled his election "
        "in 2025 (BGE 1C_467/2024), and Schaffhausen elected a successor on 29 June 2025. The Federal Statistical "
        "Office now lists him as not elected; the declared result is kept here.",
    )
}
SHORT_TOTALS = {
    (2015, "NE", "FDP"): (
        "The Federal Statistical Office gives the FDP list 25,012 list votes, 9 fewer than its candidates' votes and "
        "additional votes add up to (25,021)."
    )
}
SOURCE_NOTE = (
    "The Federal Statistical Office publishes the candidates' votes in every Council of States election since "
    "1990/1991, canton by canton and round by round, with the turnout of each round as a percentage. Each canton "
    "elects its members under its own law, mostly on the day of the federal election."
)
MAJORITY = (
    "Most cantons elect their members of the Council of States by majority vote. A candidate needs an absolute "
    "majority in the first round, as each canton defines it; seats still open go to a second round, decided by "
    "relative majority, in which new candidates may also stand. Where no more candidates stand than seats remain, "
    "they are declared elected without a vote (a tacit election)."
)
PROPORTIONAL = (
    "Jura (since 1979) and Neuchâtel (since 2011) elect their two members of the Council of States by proportional "
    "representation. Seats go to lists by their list votes, which are the candidates' votes plus the list's "
    "additional votes, and within each list to the candidates with the most votes."
)
LANDSGEMEINDE_NOTE = (
    "Elected by the Landsgemeinde, the canton's open-air assembly of voters, by show of hands; no count is published."
)
LANDSGEMEINDE_COUNTED = "Elected by the Landsgemeinde, the canton's open-air assembly of voters."
TACIT_NOTE = "No more candidates stood than seats, so they were declared elected without a vote (a tacit election)."
TACIT_SECOND_NOTE = (
    "The second round was not held: no more candidates stood than seats remained, so they were declared elected "
    "without a vote. The Federal Statistical Office marks the second round as a tacit election but does not say "
    "who was already elected in the first round; that comes from {cited}."
)
TACIT_DAY_NOTE = (
    "The Federal Statistical Office gives {printed} for this tacit election, which is not the day it was declared."
)
TURNOUT_NOTE = "The Federal Statistical Office gives the turnout as a percentage of registered voters, not as a count."


def fetch() -> None:
    raw.fetch(SOURCE, CANDIDATES, suffix=".xlsx")
    for skip in PAGES:
        raw.fetch(SOURCE, MEMBERS.format(skip), suffix=".json")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return re.sub(r"[^a-z]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def day(cell: object) -> str | None:
    text = str(cell or "").strip()
    if not text:
        return None
    if "Stille Wahl" in text:
        return "tacit"
    if match := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", text):
        year = int(match[3]) if len(match[3]) == 4 else 1900 + int(match[3]) if int(match[3]) > 50 else 2000 + int(match[3])
        return date(year, int(match[2]), int(match[1])).isoformat()
    return datetime.fromisoformat(text).date().isoformat()


def share(cell: object) -> float | None:
    text = str(cell or "").strip()
    if not re.fullmatch(r"[\d.]+", text):
        return None
    value = float(text)
    return round(value / 100 if value > 1 else value, 6)


def count(cell: object) -> int | None:
    text = str(cell or "").strip()
    return int(float(text)) if re.fullmatch(r"[\d.]+", text) else None


def candidate(text: str) -> tuple[str, str | None, bool | None]:
    incumbent = None
    if match := re.search(r",?\s*(bisher|neu)$", text):
        incumbent = match[1] == "bisher"
        text = text[: match.start()].strip()
    party = None
    if match := re.search(r"(?:\(([^()]+)|\s([^\s()]+))\)$", text):
        party = (match[1] or match[2]).strip()
        text = text[: match.start()].strip()
        if party == "MCG" and text.endswith(" MCR"):
            text = text[:-4]
    return text.rstrip(", ").strip(), party, incumbent


def display(printed: str) -> str:
    if printed.isupper():
        printed = re.sub(r"[^\s-]+", lambda match: match[0].capitalize(), printed)
    tokens = printed.split()
    particles = 0
    while particles < len(tokens) - 2 and tokens[particles] in PARTICLES:
        particles += 1
    if len(tokens) - particles == 2:
        return " ".join([tokens[-1], *tokens[:-1]])
    if len(tokens) - particles == 3 and tokens[-1].endswith("."):
        return " ".join([*tokens[-2:], *tokens[:-2]])
    return printed


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rounds: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    sheets = pl.read_excel(raw.cached(SOURCE, CANDIDATES).path, sheet_id=0, has_header=False, raise_if_empty=False)
    for sheet, frame in sheets.items():
        cycle = int(sheet.split()[-1])
        header = [str(cell or "").strip() for cell in frame.row(1)]
        name_col = header.index("Kandidierende")
        columns = {1: header.index("1. Wahlgang"), 2: header.index("2. Wahlgang")}
        canton = None
        status = None
        listing = None
        proportional = False
        labeled = -2
        for index, row in enumerate(frame.iter_rows()):
            first = str(row[0] or "").strip()
            if first.startswith(("Vollständige", "Quelle")):
                break
            folded = fold(re.sub(r"\(.*?\)|\d+", "", first)).replace(" ", "")
            if folded in ALIASES:
                canton = ALIASES[folded]
                status = listing = None
                proportional = "Proporz" in first
                for number, column in columns.items():
                    when = day(row[column])
                    if "Stille Wahl" in str(row[column + 1] or ""):
                        when = "tacit" if when is None else f"tacit:{when}"
                    if when is None and number == 2:
                        continue
                    rounds.append(
                        {
                            "cycle": cycle,
                            "canton": canton,
                            "round": number,
                            "day": when,
                            "turnout": share(row[column + 1]),
                            "proportional": proportional,
                            "landsgemeinde": "Landsgemeinde" in first,
                        }
                    )
            if canton is None:
                continue
            for cell in row[:name_col]:
                text = str(cell or "").strip()
                if not text or fold(re.sub(r"\(.*?\)|\d+", "", text)).replace(" ", "") in ALIASES:
                    continue
                if "Landsgemeinde" in text:
                    for spec in rounds[-2:]:
                        if spec["canton"] == canton and spec["cycle"] == cycle:
                            spec["landsgemeinde"] = True
                elif "nicht gewählt" in text:
                    status = False
                elif "gewählt" in text:
                    status = True
                elif proportional:
                    text = re.sub(r"\s+\d+$", "", text)
                    if labeled == index - 1 and not any("gewählt" in str(other or "") for other in row[:name_col]):
                        for entry in rows:
                            if entry["cycle"] == cycle and entry["canton"] == canton and entry["list"] == listing:
                                entry["list"] = f"{listing} {text}"
                        listing = f"{listing} {text}"
                    else:
                        listing = text
                    labeled = index
            text = re.sub(r"\s+", " ", str(row[name_col] or "")).strip()
            if not text:
                continue
            values = {number: count(row[column + 2]) for number, column in columns.items()}
            if proportional and text in LABELS and "," not in text:
                listing = text
                status = None
                rows.append({"cycle": cycle, "canton": canton, "kind": "total", "list": listing, "votes1": values[1]})
                continue
            if text == "Total":
                if proportional and rows[-1]["kind"] == "legend" and not any(
                    entry["kind"] == "total" and entry["list"] == listing and entry["cycle"] == cycle
                    and entry["canton"] == canton for entry in rows
                ):
                    rows.append(
                        {"cycle": cycle, "canton": canton, "kind": "total", "list": listing, "votes1": values[1]}
                    )
                continue
            if text == "Zusatzstimmen":
                rows.append({"cycle": cycle, "canton": canton, "kind": "legend", "list": listing, "votes1": values[1]})
                continue
            if text in SCATTERED:
                rows.append(
                    {
                        "cycle": cycle,
                        "canton": canton,
                        "kind": "scattered",
                        "printed": text.removesuffix(" (Übrige)"),
                        "votes1": values[1],
                        "votes2": values[2],
                    }
                )
                continue
            name, party, incumbent = candidate(text)
            rows.append(
                {
                    "cycle": cycle,
                    "canton": canton,
                    "kind": "candidate",
                    "list": listing if proportional else None,
                    "printed": name,
                    "party": party,
                    "incumbent": incumbent,
                    "elected": status is True or (status is None and name.isupper()),
                    "votes1": values[1],
                    "votes2": values[2],
                }
            )
    contests = pl.DataFrame(rounds, infer_schema_length=None)
    entries = pl.DataFrame(rows, infer_schema_length=None)
    for cycle, group in contests.group_by("cycle"):
        if missing := set(CANTONS) - set(group["canton"]):
            raise ValueError(f"cantons missing in {cycle[0]}: {missing}")
    sums = (
        entries.filter(pl.col("kind").is_in(["candidate", "legend"]), pl.col("list").is_not_null())
        .group_by("cycle", "canton", "list")
        .agg(sum=pl.col("votes1").sum())
    )
    totals = entries.filter(pl.col("kind") == "total").join(sums, on=["cycle", "canton", "list"], how="left")
    wrong = totals.filter(pl.col("votes1") != pl.col("sum"))
    if set(wrong.select("cycle", "canton", "list").rows()) != set(SHORT_TOTALS):
        raise ValueError(f"list totals differ from their candidates and additional votes: {wrong}")
    labels = set(entries.filter(pl.col("kind") == "candidate")["party"].drop_nulls()) | set(entries["list"].drop_nulls())
    if unknown := labels - set(LABELS) - UNAFFILIATED:
        raise ValueError(f"unknown party labels: {unknown}")
    if (lost := entries.filter(pl.col("kind").is_in(["total", "legend"]), pl.col("list").is_null())).height:
        raise ValueError(f"list rows without a list: {lost}")

    members: list[dict[str, object]] = []
    for skip in PAGES:
        payload = json.loads(raw.cached(SOURCE, MEMBERS.format(skip)).path.read_text(encoding="utf-8"))["d"]
        for record in payload["results"] if isinstance(payload, dict) else payload:
            born = re.search(r"-?\d+", record["DateOfBirth"] or "")
            members.append(
                {
                    "person": record["PersonNumber"],
                    "first": record["FirstName"],
                    "last": record["LastName"],
                    "gender": {"m": "male", "f": "female"}.get(record["GenderAsString"] or ""),
                    "born": None if born is None else datetime.fromtimestamp(int(born[0]) / 1000, UTC).date().isoformat(),
                    "member_canton": record["CantonAbbreviation"],
                }
            )
    roster = (
        pl.DataFrame(members)
        .unique("person")
        .filter(pl.col("born").is_null() | (pl.col("born") > "1900"))
        .with_columns(key=(pl.col("last") + " " + pl.col("first")).map_elements(fold, return_dtype=pl.String))
    )
    wins = entries.filter(pl.col("kind") == "candidate").group_by("canton", "printed").agg(pl.col("elected").any())
    found: list[dict[str, object]] = []
    for canton, printed, won in wins.iter_rows():
        tokens = set(fold(printed).split())
        exact = roster.filter(
            (pl.col("person") == MISSPELLED[printed]) if printed in MISSPELLED else (pl.col("key") == fold(printed))
        )
        if exact.height > 1:
            exact = exact.filter(pl.col("member_canton") == canton)
        if exact.height == 1:
            found.append({"canton": canton, "printed": printed, **exact.row(0, named=True)})
            continue
        if exact.height > 1:
            raise ValueError(f"ambiguous member matches for {canton} {printed}: {exact}")

        def close(first: str, last: str) -> bool:
            surname = [token for token in fold(last).split() if token not in PARTICLES]
            given = fold(first).split()
            if not surname or not given or surname[0] not in tokens:
                return False
            return given[0] in tokens or won and any(
                token[:3] == given[0][:3] and len(token) > 2 for token in tokens - {surname[0]}
            )

        near = roster.filter(pl.col("member_canton") == canton).filter(
            pl.struct("first", "last").map_elements(lambda row: close(row["first"], row["last"]), return_dtype=pl.Boolean)
        )
        if near.height == 1:
            found.append({"canton": canton, "printed": printed, **near.row(0, named=True)})
        elif near.height > 1:
            raise ValueError(f"ambiguous member matches for {canton} {printed}: {near}")
        else:
            found.append({"canton": canton, "printed": printed})
    people = pl.DataFrame(found, infer_schema_length=None)
    unmatched = wins.filter("elected").join(people.filter(pl.col("person").is_null()), on=["canton", "printed"], how="semi")
    print(f"{people['person'].is_not_null().sum()} of {people.height} candidates matched to members")
    print(f"elected without a member match: {unmatched.select('canton', 'printed').rows()}")
    contests.write_parquet(PARSED / "contests.parquet")
    entries.write_parquet(PARSED / "entries.parquet")
    people.drop("key").write_parquet(PARSED / "people.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    entries = pl.read_parquet(PARSED / "entries.parquet")
    people = pl.read_parquet(PARSED / "people.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swiss Council of States elections from 1990/1991 to 2023 by canton and round, with candidates' votes",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def note(entity: UUID, kind: str, text: str) -> None:
            if entity in cs.minted:
                cs.add("note", entity_id=entity, kind=kind, body_md=text)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.bfs.admin.ch/bfs/de/home/statistiken/politik/wahlen/eidgenoessische-wahlen/staenderat.html",
            license="Open use; must provide the source",
            license_url="https://www.bfs.admin.ch/bfs/en/home/fso/swiss-federal-statistical-office/terms-of-use.html",
            notes=SOURCE_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, CANDIDATES), source)
        services = cs.source("Parliamentary Services", "Parliament open data: members of the councils")
        cs.add(
            "source",
            id=services,
            kind="official_body",
            publisher="Parliamentary Services",
            title="Parliament open data: members of the councils",
            url="https://ws.parlament.ch/odata.svc/MemberCouncil",
            license="Open use",
            license_url="https://www.parlament.ch/en/services/open-data-webservices",
            notes="The names, genders, and birth dates of candidates who sat in either council.",
        )
        for skip in PAGES:
            cs.artifact(raw.cached(SOURCE, MEMBERS.format(skip)), services)

        switzerland = cs.polity("CH")
        areas = {code: cs.area("ch.canton", code) for code in CANTONS}
        units = {code: cs.unit("ch.canton", code) for code in CANTONS}
        cantons = {code: cs.polity(f"CH-{code}") for code in CANTONS}
        if cs.minted & {switzerland, *areas.values(), *units.values(), *cantons.values()}:
            raise ValueError("run ch.swisstopo and ch.swissvotes first: Switzerland and its cantons must exist")

        body = cs.key("ch.body", "council-of-states")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=switzerland, kind="upper_house")
            name(body, "Council of States")
            for text, lang in (
                ("Ständerat", "de"),
                ("Conseil des États", "fr"),
                ("Consiglio degli Stati", "it"),
                ("Cussegl dals stadis", "rm"),
            ):
                name(body, text, lang, "native")
            cs.add(
                "body_size",
                body_id=body,
                seats=44,
                valid_from=date(1848, 11, 6),
                valid_from_precision="day",
                valid_to=date(1979, 1, 1),
                valid_to_precision="day",
            )
            cs.add("body_size", body_id=body, seats=46, valid_from=date(1979, 1, 1), valid_from_precision="day")
        majority = cs.key("ch.electoral_system", "council-of-states")
        cs.add("electoral_system", id=majority, polity_id=switzerland, rules={"formula": "two_round"}, rules_version=1)
        name(majority, "Swiss Council of States majority vote")
        note(majority, "methodology", MAJORITY)
        proportional = cs.key("ch.electoral_system", "council-of-states-proportional")
        cs.add(
            "electoral_system",
            id=proportional,
            polity_id=switzerland,
            rules={"formula": "hagenbach_bischoff", "lists": {"order": "open", "vacancy": "next_by_votes"}},
            rules_version=1,
        )
        name(proportional, "Swiss Council of States proportional representation")
        note(proportional, "methodology", PROPORTIONAL)

        made: set[UUID] = set()
        parties: dict[str, UUID | None] = dict.fromkeys(UNAFFILIATED)
        for label, key in LABELS.items():
            parties[label] = party = cs.key("ch.party", key)
            if party in cs.minted and party not in made:
                made.add(party)
                full, short, kind, lang = NEW_PARTIES[key]
                cs.add("party", id=party, kind=kind, polity_id=switzerland)
                name(party, full, lang)
                name(party, short, lang, "abbreviation")

        persons: dict[tuple[str, str], UUID] = {}
        for row in people.iter_rows(named=True):
            if row["person"] is not None:
                person = cs.key("ch.parlament.person", str(row["person"]))
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add(
                        "person",
                        id=person,
                        gender=row["gender"],
                        birth_date=None if row["born"] is None else date.fromisoformat(row["born"]),
                        birth_precision=None if row["born"] is None else "day",
                    )
                    name(person, f"{row['first']} {row['last']}")
            else:
                person = cs.key(f"{SOURCE}.person", f"{row['canton']}:{fold(row['printed'])}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, display(row["printed"]))
            persons[row["canton"], row["printed"]] = person

        held = {
            str(day)
            for (day,) in conn.execute(
                "select e.date_from from election e join polity p on p.id = e.polity_id "
                "where p.iso_code = 'CH' and e.kind = 'referendum'"
            ).fetchall()
        }
        generals: dict[int, UUID] = {}
        for cycle in sorted(set(contests["cycle"])):
            generals[cycle] = cs.key(
                "ch.election", f"{DAYS[cycle]}:federal" if str(DAYS[cycle]) in held else str(DAYS[cycle])
            )
            if generals[cycle] in cs.minted:
                raise ValueError("run ch.nationalrat first: the federal elections must exist")

        elections: dict[tuple[str, str | None], UUID] = {}

        def election(when: date, canton: str | None, kind: str, parent: UUID, precision: str, printed: str | None) -> UUID:
            label = f"{when:%Y-%m}" if precision == "month" else str(when)
            if (label, canton) in elections:
                return elections[label, canton]
            entity = elections[label, canton] = cs.key(
                "ch.election", f"{label}:council-of-states" + ("" if canton is None else f":{canton}")
            )
            cs.add(
                "election",
                id=entity,
                polity_id=switzerland if canton is None else cantons[canton],
                parent_id=parent,
                kind=kind,
                date_from=when,
                date_to=when,
                date_precision=precision,
                status="held",
            )
            stage = "second round" if kind == "runoff" else "election"
            place = "" if canton is None else f" in {CANTONS[canton][0]}"
            stamp = f"{when:%B %Y}" if precision == "month" else f"{when.day} {when:%B %Y}"
            name(entity, f"Council of States {stage}{place}, {stamp}")
            if entity in cs.minted:
                prefix = "ch" if canton is None else f"ch/{canton.lower()}"
                cs.add("slug", path=f"{prefix}/{label}-{'runoff' if kind == 'runoff' else 'general'}", entity_id=entity)
            if printed is not None:
                note(entity, "caveat", TACIT_DAY_NOTE.format(printed=printed))
            return entity

        slugs = {code: english.lower().replace(" ", "-").replace(".", "") for code, (english, _) in CANTONS.items()}
        values: list[dict[str, object]] = []
        for (cycle, canton), group in contests.group_by("cycle", "canton", maintain_order=True):
            cycle, canton = int(cycle), str(canton)
            here = entries.filter(pl.col("cycle") == cycle, pl.col("canton") == canton)
            candidates = here.filter(pl.col("kind") == "candidate")
            pr = bool(group["proportional"][0])
            uncounted = candidates["votes1"].is_null().all()
            first_day = group["day"][0]
            assembly = bool(group["landsgemeinde"][0]) or (
                canton in LANDSGEMEINDE
                and uncounted
                and first_day not in (None, "tacit")
                and date.fromisoformat(first_day).weekday() == 6
            )
            elected = set(candidates.filter("elected")["printed"])
            if (cycle, canton) in ANNULLED:
                elected.add(ANNULLED[cycle, canton][0])
            tacit_second = TACIT_SECOND.get((cycle, canton))
            second = group.filter(pl.col("round") == 2)
            if second.height and str(second["day"][0]).startswith("tacit") and tacit_second is None:
                raise ValueError(f"{cycle} {canton}: tacit second round without a known first-round result")
            if tacit_second is not None:
                first_winners = tacit_second[2]
            elif second.height:
                first_winners = set(candidates.filter("elected", pl.col("votes2").is_null())["printed"])
            else:
                first_winners = elected
            if not first_winners <= elected:
                raise ValueError(f"{cycle} {canton}: {first_winners} not among the elected")
            previous: UUID | None = None
            previous_day = DAYS[cycle]
            for spec in group.sort("round").iter_rows(named=True):
                number = spec["round"]
                printed_day = spec["day"] or ""
                tacit = printed_day.startswith("tacit") or (number == 1 and not assembly and uncounted)
                when_text = printed_day.removeprefix("tacit").removeprefix(":")
                fix_note = None
                if (cycle, canton, number) in DATE_FIXES:
                    when_text, fix_note = DATE_FIXES[cycle, canton, number]
                precision = "day"
                printed_note = None
                if number == 2 and tacit_second is not None:
                    if when_text and when_text != tacit_second[0]:
                        shown = date.fromisoformat(when_text)
                        printed_note = f"{shown.day} {shown:%B %Y}"
                    when_text, precision = tacit_second[0], tacit_second[1]
                when = date.fromisoformat(when_text) if when_text else DAYS[cycle]
                label = f"{when:%Y-%m}" if precision == "month" else str(when)
                winners = first_winners if number == 1 else elected - first_winners
                if number == 1 and when == DAYS[cycle]:
                    parent = generals[cycle]
                    path = f"ch/{when}-general/council-of-states-{slugs[canton]}"
                elif number == 1:
                    parent = election(when, canton, "general", generals[cycle], precision, None)
                    path = f"ch/{canton.lower()}/{label}-general/council-of-states"
                else:
                    shared = previous_day == DAYS[cycle] and not tacit
                    parent = election(
                        when,
                        None if shared else canton,
                        "runoff",
                        generals[cycle] if previous_day == DAYS[cycle] else elections[str(previous_day), canton],
                        precision,
                        printed_note,
                    )
                    path = (
                        f"ch/{label}-runoff/council-of-states-{slugs[canton]}"
                        if shared
                        else f"ch/{canton.lower()}/{label}-runoff/council-of-states"
                    )
                previous_day = when
                contest_key = f"{label}:council-of-states:{canton}"
                contest = cs.key("ch.contest", contest_key)
                field = f"votes{number}"
                result_kind = (
                    "tacit"
                    if tacit
                    else "show_of_hands"
                    if assembly and number == 1 and uncounted
                    else "counted"
                )
                counted = result_kind == "counted"
                cs.add(
                    "contest",
                    id=contest,
                    election_id=parent,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=areas[canton],
                    magnitude=len(elected) if number == 1 else len(winners),
                    round_number=number,
                    previous_round_id=previous,
                    electoral_system_id=proportional if pr else majority,
                    default_result_kind=result_kind,
                )
                stage = " (second round)" if number == 2 else ""
                name(contest, f"{when.year} Council of States election in {CANTONS[canton][0]}{stage}")
                cs.add("slug", path=path, entity_id=contest)
                if fix_note is not None:
                    note(contest, "caveat", fix_note)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=parent,
                    contest_id=contest,
                    stage="official",
                    kind=result_kind,
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1 if counted else 0,
                )
                if number == 2 and (cycle, canton) in ANNULLED:
                    note(result_set, "context", ANNULLED[cycle, canton][1])
                if assembly and number == 1:
                    note(result_set, "context", LANDSGEMEINDE_COUNTED if counted else LANDSGEMEINDE_NOTE)
                elif tacit and number == 2 and tacit_second is not None:
                    note(result_set, "context", TACIT_SECOND_NOTE.format(cited=tacit_second[3]))
                elif tacit:
                    note(result_set, "context", TACIT_NOTE)
                if counted:
                    standing = candidates.filter(pl.col(field).is_not_null())
                elif number == 2:
                    standing = candidates.filter(pl.col("printed").is_in(list(winners)))
                else:
                    standing = candidates
                lists: dict[str, UUID] = {}
                if pr:
                    for listing in standing["list"].unique(maintain_order=True):
                        option = lists[listing] = cs.key("ch.option", f"{contest_key}:list:{listing}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="list",
                            party_id=parties[listing],
                            ballot_label=listing,
                        )
                        if (cycle, canton, listing) in SHORT_TOTALS:
                            note(result_set, "caveat", SHORT_TOTALS[cycle, canton, listing])
                        seats = standing.filter(pl.col("list") == listing, pl.col("printed").is_in(list(winners))).height
                        if seats:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
                    for kind, measure in (("total", 8), ("legend", 9)):
                        for listing, value in here.filter(pl.col("kind") == kind).select("list", "votes1").iter_rows():
                            values.append(
                                {"set": result_set, "canton": canton, "option": lists[listing], "measure": measure, "value": value}
                            )
                for row in standing.iter_rows(named=True):
                    option = cs.key("ch.option", f"{contest_key}:{fold(row['printed'])}")
                    label_text = row["party"] or row["list"]
                    party = None if label_text is None else parties[label_text]
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=lists[row["list"]] if pr else None,
                        party_id=party,
                        ballot_label=row["printed"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", f"{contest_key}:{fold(row['printed'])}"),
                        person_id=persons[canton, row["printed"]],
                        option_id=option,
                        role="head",
                        ballot_name=row["printed"],
                        member_of_party_id=party,
                        is_independent=label_text == "parteilos",
                        is_incumbent=row["incumbent"],
                    )
                    if row["printed"] in winners:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                    if counted and row[field] is not None:
                        values.append(
                            {"set": result_set, "canton": canton, "option": option, "measure": 10 if pr else 1, "value": row[field]}
                        )
                if counted:
                    for printed, value in (
                        here.filter(pl.col("kind") == "scattered", pl.col(field).is_not_null())
                        .select("printed", field)
                        .iter_rows()
                    ):
                        option = cs.key("ch.option", f"{contest_key}:scattered")
                        cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=printed)
                        values.append({"set": result_set, "canton": canton, "option": option, "measure": 1, "value": value})
                    if spec["turnout"] is not None:
                        values.append(
                            {"set": result_set, "canton": canton, "option": None, "measure": 14, "value": spec["turnout"], "flags": 4}
                        )
                        note(result_set, "methodology", TURNOUT_NOTE)
                previous = contest
        cs.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": [str(row["set"]) for row in values],
                    "unit_id": [str(units[str(row["canton"])]) for row in values],
                    "option_id": [None if row["option"] is None else str(row["option"]) for row in values],
                    "channel": [0] * len(values),
                    "measure": [row["measure"] for row in values],
                    "count_round": [0] * len(values),
                    "flags": [row.get("flags", 0) for row in values],
                    "value": [float(row["value"]) for row in values],
                },
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            ),
        )
        return cs.write()
