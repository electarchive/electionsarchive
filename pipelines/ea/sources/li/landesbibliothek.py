"""Liechtenstein's Landtag elections of 1918-1997 by municipality, digitized from the country's newspapers.

The paper was the principality's official publication organ, and from the 1970s it printed the government's own
notice of each result (Amtliche Kundmachung). The national library's digital collection holds every issue with its
OCR text in ALTO, so each number keeps the page and box it was read from. The figures were transcribed from the page
images into `landtag_1945_1997.json`; this module checks every row against the printed totals and locates each value
on the page through the ALTO words.

The majority-vote elections of 1918-1936 come from the same collection: the Volksblatt, and where it has no issue or
prints less, the Oberrheinische Nachrichten, the Liechtensteiner Nachrichten, and the Liechtensteiner Vaterland. Their
candidate votes by municipality were transcribed into `landtag_1918_1936.json`; every printed total is kept as a
declared figure, so a row whose municipal votes do not add up to it shows as a finding.
"""

import itertools
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from uuid import uuid5
from xml.etree import ElementTree

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.li import regierung

SOURCE = "li.landesbibliothek"
API = "https://www.eliechtensteinensia.li/viewer/api/v1/records"
VIEWER = "https://www.eliechtensteinensia.li/viewer/image"
LIECHTENSTEIN = regierung.LIECHTENSTEIN
DATA = Path(__file__).with_name("landtag_1945_1997.json")
DISTRICTS = {
    "Oberland": ("Vaduz", "Balzers", "Planken", "Schaan", "Triesen", "Triesenberg"),
    "Unterland": ("Eschen", "Gamprin", "Mauren", "Ruggell", "Schellenberg"),
}
CODES = {name: code for name, code, _ in regierung.PLACES.values()}
# Accounting columns and the measure each is stored under.
ACCOUNTING = {"reg": 2, "cards": 14, "cast": 3, "valid": 4, "invalid": 5, "blank": 6}
# Columns that are printed but not stored: the sum of the party votes, and the voting cards cast by post or in the
# mobile ballot box, which would split the votes by channel for one figure only.
SKIPPED = {"total", "postal"}
PARTIES = {
    "CSP": "Christlich-soziale Partei",
    "ULL": "Überparteiliche Liste Liechtensteins",
    "UEK": "Liste der unselbständig Erwerbenden und Kleinbauern",
}
BALLOT_LABELS = {
    "FBP": "Fortschrittliche Bürgerpartei",
    "VU": "Vaterländische Union",
    "FL": "Freie Liste",
    "CSP": "Christlich-soziale Partei",
    "ULL": "Überparteiliche Liste Liechtensteins",
    "UEK": "Liste der unselbständig Erwerbenden und Kleinbauern",
}
BALLOT_LABEL_1986 = {"FL": "Freie Wählerliste"}
PAPER = "Liechtensteiner Volksblatt"
# Findings the printed figures cause, by election.
ACCEPTED = {
    date(1945, 4, 29): {
        "accounting.declared": "The 1945 report prints registered voters only for each district (Oberland 2,032, "
        "Unterland 1,056), not by municipality, so the declared district figures have no municipal values to sum.",
    },
    date(1949, 2, 6): {
        "accounting.declared": "The 1949 report prints registered voters, voting cards, voters, and valid, invalid, "
        "and blank ballots only for each district, not by municipality, so the declared district figures have no "
        "municipal values to sum.",
    },
    date(1953, 2, 15): {
        "accounting.votes_valid": "In February 1953 a valid ballot needed no list designation, and the paper's "
        "list votes for Triesen, Balzers, Triesenberg, Schaan, Gamprin and Ruggell add up to 1 to 6 more or fewer than "
        "their valid ballots; the values are stored as printed.",
        "accounting.ballots_sum": "The table prints Triesen with 331 ballots against 321 valid, 8 invalid and 5 blank, "
        "and Schaan with 444 against 414 valid and 21 invalid; stored as printed.",
        "accounting.declared": "The Oberland total row prints 2,194 registered voters where its municipalities add up "
        "to 2,244 (Schaan is clearly printed 486), and 198 votes for the list of employees and small farmers where "
        "they add up to 197; the municipal values and the printed totals are stored as printed.",
    },
    date(1957, 9, 1): {
        "accounting.votes_valid": "Schellenberg's list votes (73 and 31) add up to 104 against 105 valid ballots in "
        "the printed table; stored as printed.",
        "accounting.ballots_sum": "Schellenberg is printed with 105 ballots, 105 valid and 2 blank; stored as printed.",
    },
    date(1974, 2, 3): {
        "accounting.ballots_sum": "The table counts blank envelopes apart from the ballots in some rows: Gamprin prints "
        "153 ballots against 152 valid, 1 invalid and 1 blank envelope, and Triesenberg 450 against 448, 2 and 2; "
        "stored as printed.",
    },
    date(1989, 3, 5): {
        "accounting.ballots_sum": "The government's notice counts blank envelopes apart from the ballots in two rows: "
        "Triesenberg prints 1,147 ballots against 1,141 valid, 6 invalid and 11 blank envelopes, and Schellenberg 330 "
        "against 329, 1 and 1; stored as printed.",
    },
    date(1997, 2, 2): {
        "accounting.ballots_sum": "The 1997 table's valid, invalid and blank columns differ from its ballots by 3 to "
        "15 in Vaduz, Schaan, Triesen, Triesenberg and Ruggell, read again from the page image; stored as printed.",
    },
}
# The word that opens each list's line in a district notice.
DISTRICT_WORDS = {"FBP": "Fortschrittlichen", "VU": "Vaterländischen"}
# Printed district totals that differ from the sum of their municipalities, as {(day, district, column)}.
MISPRINTS = {(date(1953, 2, 15), "Oberland", "UEK")}
SYSTEMS = {
    # (first, last): (Oberland seats, Unterland seats, national threshold in percent)
    (date(1939, 1, 1), date(1973, 12, 31)): (9, 6, 18),
    (date(1974, 1, 1), date(1988, 12, 31)): (9, 6, 8),
    (date(1989, 1, 1), date(2000, 12, 31)): (15, 10, 8),
}
SYSTEM_NOTES = {
    18: (
        "The Landtag had 15 members, 9 elected in the Oberland and 6 in the Unterland, by list proportional "
        "representation under the law of 1939. Each voter cast one list ballot, which could be changed by striking "
        "or adding candidates; a list's votes are the ballots cast for it (Listenstimmen). Only lists with at least "
        "18% of the votes nationwide shared in the seats, which each district allocated by the Hagenbach-Bischoff "
        "method; the list's candidates with the most votes took them."
    ),
    8: (
        "Each voter has as many votes as the district has seats and may give them to candidates of any list. A list's "
        "party votes are its candidates' votes plus the additional votes (Zusatzstimmen) of ballots that leave votes "
        "unused. Only lists with at least 8% of the votes nationwide share in the seats, which each district "
        "allocates by the Hagenbach-Bischoff method."
    ),
}
METHOD = (
    "Counted by the municipalities and published by the government; digitized from the {paper} of {issue}, which "
    "prints the result by municipality. Registered voters are the electoral roll (Stimmberechtigte), voters are the "
    "voting cards cast (Stimmkarten), ballots cast are the ballots or envelopes put in the box, and each is valid, "
    "invalid, or blank. {votes} Each value keeps the page and the box of the printed figure."
)
LIST_BALLOTS = "Each voter cast one list ballot; a list's votes are the list ballots cast for it (Listenstimmen)."
PARTY_VOTES = (
    "A list's party votes are its candidates' votes plus its additional votes (Kandidaten- und Zusatzstimmen); each "
    "voter has as many votes as the district has seats."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


LB_SOURCE = key("source", SOURCE)


@dataclass
class Table:
    page: str
    rows: dict[str, list[int | None]]
    district: bool


@dataclass
class Election:
    day: date
    issue: str
    columns: list[str]
    tables: list[Table]
    seats: dict[str, dict[str, int]]
    totals: dict[str, dict[str, int]]
    notes: list[str]

    @property
    def parties(self) -> list[str]:
        return [c for c in self.columns if c not in ACCOUNTING and c not in SKIPPED]


def elections() -> list[Election]:
    loaded = json.loads(DATA.read_text(encoding="utf-8"))
    return [
        Election(
            day=date.fromisoformat(e["date"]),
            issue=e["issue"],
            columns=e["columns"],
            tables=[Table(t["page"], t["rows"], bool(t.get("district"))) for t in e["tables"]],
            seats=e["seats"],
            totals=e["totals"],
            notes=e["notes"],
        )
        for e in loaded
    ]


def alto_url(issue: str, page: str) -> str:
    return f"{API}/{issue}/files/alto/{page}.xml"


def image_url(issue: str, page: str) -> str:
    return f"{API}/{issue}/files/images/{page}.tif/full/max/0/default.jpg"


def manifest_url(issue: str) -> str:
    return f"{API}/{issue}/manifest/"


def not_html(path: Path) -> None:
    if path.read_bytes()[:64].lstrip(b"\xef\xbb\xbf").lower().startswith(b"<!doctype html"):
        raise ValueError(f"{path}: the library answered with an HTML page")


def fetch() -> None:
    for e in elections():
        raw.fetch(SOURCE, manifest_url(e.issue), suffix=".json", check=not_html)
        for t in e.tables:
            raw.fetch(SOURCE, alto_url(e.issue, t.page), suffix=".xml", check=not_html)
    for issue, page in sorted({(c.issue, c.page) for m in majority() for r in m.rounds for c in r.contests}):
        raw.fetch(SOURCE, manifest_url(issue), suffix=".json", check=not_html)
        raw.fetch(SOURCE, alto_url(issue, page), suffix=".xml", check=not_html)


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def digits(self) -> str:
        return re.sub(r"\D", "", self.text)


def words(issue: str, page: str) -> list[Word]:
    text = raw.cached(SOURCE, alto_url(issue, page)).path.read_text(encoding="utf-8")
    text = re.sub(r'\s[\w:]*xmlns(:\w+)?="[^"]+"', "", text)
    # Some issues' ALTO prefixes every element and attribute with a namespace.
    text = re.sub(r"\s\w+:(\w+=)", r" \1", re.sub(r"<(/?)\w+:", r"<\1", text))
    root = ElementTree.fromstring(text.encode())
    found = []
    for s in root.iter("String"):
        x, y = float(s.get("HPOS") or 0), float(s.get("VPOS") or 0)
        found.append(
            Word(s.get("CONTENT") or "", x, y, x + float(s.get("WIDTH") or 0), y + float(s.get("HEIGHT") or 0))
        )
    return found


def canvas(issue: str, page: str) -> tuple[int, int]:
    loaded = json.loads(raw.cached(SOURCE, manifest_url(issue)).path.read_text(encoding="utf-8"))
    for c in loaded["sequences"][0]["canvases"]:
        if any(r["@id"].endswith(f"/alto/{page}.xml") for r in c["rendering"]):
            return int(c["width"]), int(c["height"])
    raise LookupError(f"page {page} not in {issue}")


Box = tuple[float, float, float, float]


def locate(e: Election, t: Table) -> dict[tuple[str, int], Box]:
    """The box of each printed value as {(row, column index): box} in canvas pixels."""
    page = words(e.issue, t.page)
    # Thousands printed with a space ("12 124") are two ALTO words; add their union as one more word.
    ordered = sorted(page, key=lambda w: (round(w.y0 / 10), w.x0))
    for a, b in itertools.pairwise(ordered):
        if (
            abs(a.y0 - b.y0) < 15
            and 0 <= b.x0 - a.x1 < 45
            and re.fullmatch(r"\d{1,3}", a.text)
            and re.fullmatch(r"\d{3}", b.text)
        ):
            page.append(Word(f"{a.text}{b.text}", a.x0, min(a.y0, b.y0), b.x1, max(a.y1, b.y1)))
    found: dict[tuple[str, int], Box] = {}
    lines: dict[str, list[Word]] = {}
    used: set[int] = set()
    for row, values in t.rows.items():
        wanted = {str(v) for v in values if v is not None}
        if t.district:
            # A district notice prints one line per list: "Listenstimmen der <party> <votes>", Oberland first.
            order = list(DISTRICTS).index(row)
            for i, value in enumerate(values):
                if value is None:
                    continue
                word = DISTRICT_WORDS[e.columns[i]]
                heads = sorted((w for w in page if w.text.startswith(word)), key=lambda w: w.y0)
                head = heads[order]
                line = sorted((w for w in page if abs(w.y0 - head.y0) < 20 and w.x0 > head.x1), key=lambda w: w.x0)
                exact = [w for w in line if w.digits == str(value)]
                hit = exact[0] if exact else line[1]
                found[(row, i)] = (hit.x0, hit.y0, hit.x1, hit.y1)
            continue
        anchors = [w for w in page if w.text.strip(".,:;'").lower().startswith(row.lower()[:6])]
        if row == "Triesen":
            anchors = [w for w in anchors if not w.text.lower().startswith("triesenb")]
        best: tuple[int, list[Word], float] = (-1, [], 0.0)
        for anchor in anchors:
            height = max(anchor.y1 - anchor.y0, 20)
            near = [w for w in page if abs(w.y0 - anchor.y0) <= height * 1.2 and w.x0 > anchor.x1]
            score = sum(1 for w in near if w.digits and w.digits in wanted)
            if score > best[0]:
                window = [w for w in page if -height * 1.2 <= w.y0 - anchor.y0 <= height * 2.6 and w.x0 > anchor.x1]
                best = (score, window, anchor.y0)
        window, anchor_y = best[1], best[2]
        lines[row] = sorted((w for w in window if abs(w.y0 - anchor_y) <= 40), key=lambda w: w.x0)
        left = 0.0
        for i, value in enumerate(values):
            if value is None:
                continue
            hits = [w for w in window if id(w) not in used and w.digits == str(value) and w.x0 >= left - 5]
            if hits:
                hit = min(hits, key=lambda w: (round(abs(w.y0 - anchor_y) / 40), w.x0))
                used.add(id(hit))
                found[(row, i)] = (hit.x0, hit.y0, hit.x1, hit.y1)
                left = hit.x0
    if t.district:
        return found
    # Values the OCR missed or misread take their column's span and their row's line.
    columns: dict[int, list[Box]] = defaultdict(list)
    for (_, i), box in found.items():
        columns[i].append(box)
    for row, values in t.rows.items():
        line = lines[row]
        top = median([w.y0 for w in line[:3]]) if line else 0.0
        bottom = median([w.y1 for w in line[:3]]) if line else 0.0
        for i, value in enumerate(values):
            if value is None or (row, i) in found:
                continue
            if columns[i]:
                found[(row, i)] = (min(b[0] for b in columns[i]), top, max(b[2] for b in columns[i]), bottom)
                continue
            # A column the OCR missed entirely lies between its neighbors.
            before = [j for j in range(i) if columns[j]]
            after = [j for j in range(i + 1, len(values)) if columns[j]]
            if before and after:
                x0 = max(b[2] for b in columns[before[-1]]) + 10
                x1 = min(b[0] for b in columns[after[0]]) - 10
                found[(row, i)] = (x0, top, max(x1, x0 + 10), bottom)
    return found


def check(e: Election) -> None:
    """Every column of every district must add up to the printed total."""
    for district, towns in DISTRICTS.items():
        sums: dict[str, int] = defaultdict(int)
        for t in e.tables:
            for row, values in t.rows.items():
                if row in towns or row == district:
                    for column, value in zip(e.columns, values, strict=True):
                        if value is not None:
                            sums[column] += value
        for column, total in e.totals[district].items():
            if (
                column in sums
                and column in e.parties
                and sums[column] != total
                and (e.day, district, column) not in MISPRINTS
            ):
                raise ValueError(f"{e.day} {district} {column}: rows add up to {sums[column]}, printed {total}")
        for t in e.tables:
            for row, values in t.rows.items():
                if row not in towns:
                    continue
                named = dict(zip(e.columns, values, strict=True))
                party = sum(named[p] or 0 for p in e.parties)
                if "total" in named and named["total"] != party:
                    raise ValueError(f"{e.day} {row}: party votes {party} against the printed total {named['total']}")


def label(e: Election) -> str:
    return (
        f"{e.day:%B} {e.day.year}" if sum(1 for o in elections() if o.day.year == e.day.year) > 1 else str(e.day.year)
    )


def packages() -> list[str]:
    return [
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{e.day}" for e in elections()),
        MAJORITY_REGISTRY,
        *(f"{SOURCE}/{m.day}" for m in majority()),
    ]


def parse() -> None:
    registry().write()
    for e in elections():
        election(e).write()
    majority_registry().write()
    for m in majority():
        majority_election(m).write()


def emit() -> None:
    package.run("load", packages())


def system_id(day: date) -> str:
    for first, last in SYSTEMS:
        if first <= day <= last:
            return key("electoral_system", first)
    raise LookupError(f"no electoral system for {day}")


def system_of(day: date) -> tuple[int, int, int]:
    for (first, last), rules in SYSTEMS.items():
        if first <= day <= last:
            return rules
    raise LookupError(f"no electoral system for {day}")


def party(abbreviation: str) -> str:
    if abbreviation in regierung.PARTIES:
        return regierung.party(abbreviation)
    return key("party", abbreviation)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Volksblatt archive as a source, the Landtag's electoral systems of 1939-2000, and the minor lists of "
        "1953-1989",
    )
    p.external.add(LIECHTENSTEIN)
    p.add(
        "source",
        id=LB_SOURCE,
        kind="official_body",
        publisher="Regierung des Fürstentums Liechtenstein, in the Liechtensteiner Volksblatt (eLiechtensteinensia, "
        "Liechtensteinische Landesbibliothek)",
        title="Liechtensteiner Volksblatt: official results of the Landtag elections, 1945-1997",
        url="https://www.eliechtensteinensia.li/",
        license="Public record; scans by the Liechtensteinische Landesbibliothek",
        license_url="https://www.landesbibliothek.li/",
        attribution="Liechtensteiner Volksblatt, eLiechtensteinensia, Liechtensteinische Landesbibliothek",
    )
    for (first, last), (_, _, threshold) in SYSTEMS.items():
        id = key("electoral_system", first)
        p.add(
            "electoral_system",
            id=id,
            polity_id=LIECHTENSTEIN,
            valid_from=first,
            valid_to=last,
            rules={
                "formula": "hagenbach_bischoff",
                "threshold": {"level": "list", "scope": "national", "percent": threshold},
                "lists": {"order": "open", "vacancy": "substitute"},
            },
            rules_version=1,
        )
        seats = SYSTEMS[(first, last)]
        body = SYSTEM_NOTES[threshold]
        if threshold == 8:
            body = (
                f"The Landtag has {seats[0] + seats[1]} members, {seats[0]} elected in the Oberland and {seats[1]} in "
                f"the Unterland. " + body
            )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    for abbreviation, name in PARTIES.items():
        id = party(abbreviation)
        p.add("party", id=id, kind="party", polity_id=LIECHTENSTEIN)
        p.name(id, name, "de", "official")
        p.name(id, abbreviation, "de", "abbreviation", False)
    return p


def election(e: Election) -> Package:
    check(e)
    p = Package(
        f"{SOURCE}/{e.day}",
        SOURCE,
        f"The {label(e)} Liechtenstein Landtag election by municipality, from the {PAPER}",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({LIECHTENSTEIN, regierung.LANDTAG_BODY, system_id(e.day), "li.gemeinde", "li.wahlkreis"})
    p.uses(raw.cached(SOURCE, manifest_url(e.issue)))
    page_ids: dict[str, str] = {}
    for t in e.tables:
        alto = raw.cached(SOURCE, alto_url(e.issue, t.page))
        artifact = p.artifact(alto, LB_SOURCE)
        width, height = canvas(e.issue, t.page)
        page_ids[t.page] = key("page", e.issue, t.page)
        # The ALTO file gives each word's box in the pixels of the page scan, which the IIIF service at
        # image_url(issue, page) serves at the canvas size recorded here.
        p.add(
            "source_page",
            id=page_ids[t.page],
            artifact_id=artifact,
            page_number=int(t.page),
            image_key=f"pages/{SOURCE}/{e.issue}/{t.page}.jpg",
            width=width,
            height=height,
        )
    election_id = key("election", e.day)
    p.add(
        "election",
        id=election_id,
        polity_id=LIECHTENSTEIN,
        kind="general",
        date_from=date(1997, 1, 31) if e.day == date(1997, 2, 2) else e.day,
        date_to=e.day,
        status="held",
    )
    p.name(election_id, f"{label(e)} Liechtenstein parliamentary election", "en", "official")
    p.name(election_id, f"Landtagswahlen {label(e)}", "de", "native", False)
    path = f"li/{e.day}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add(
        "citation",
        id=key("citation", election_id),
        entity_id=election_id,
        source_id=LB_SOURCE,
        locator=json.dumps({"url": f"{VIEWER}/{e.issue}/", "paper": PAPER}),
    )
    district_only = any(t.district for t in e.tables)
    units: dict[str, str] = {}
    for t in e.tables:
        for row in t.rows:
            if t.district:
                scheme, code, area = "li.wahlkreis", row, regierung.area("li.wahlkreis", row)
            else:
                scheme, code, area = "li.gemeinde", CODES[row], regierung.area("li.gemeinde", CODES[row])
            p.external.add(area)
            units[row] = key("unit", e.day, code)
            p.add(
                "reporting_unit",
                id=units[row],
                polity_id=LIECHTENSTEIN,
                scheme_code=scheme,
                code=f"{e.day}:{code}",
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
    oberland, unterland, threshold = system_of(e.day)
    # Until 1973 each voter cast one list ballot, so a list's votes are plain votes; from 1974 they are party votes.
    list_measure = 1 if threshold == 18 else 8
    for district, towns in DISTRICTS.items():
        magnitude = oberland if district == "Oberland" else unterland
        contest = key("contest", e.day, district)
        district_area = regierung.area("li.wahlkreis", district)
        p.external.add(district_area)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=regierung.LANDTAG_BODY,
            district_area_id=district_area,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=system_id(e.day),
            default_result_kind="counted",
        )
        p.name(contest, f"Landtag, {district} electoral district", "en", "english")
        p.name(contest, f"Wahlkreis {district}", "de", "official", False)
        p.add("slug", path=f"{path}/{changeset.slug(district)}", entity_id=contest, is_current=True)
        set_id = key("result_set", e.day, district)
        p.add(
            "result_set",
            id=set_id,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=LB_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        issue_day = date.fromisoformat(e.issue.split("_")[1])
        method = METHOD.format(
            paper=PAPER,
            issue=f"{issue_day:%-d %B %Y}",
            votes=LIST_BALLOTS if threshold == 18 else PARTY_VOTES,
        )
        if district_only:
            method = method.replace("by municipality", "by electoral district")
        p.add(
            "note",
            id=key("note", set_id),
            entity_id=set_id,
            kind="methodology",
            body_md=" ".join([method, *e.notes]),
            lang="en",
        )
        rows = {row: (t, values) for t in e.tables for row, values in t.rows.items() if row in towns or row == district}
        running = [
            c
            for i, c in enumerate(e.columns)
            if c in e.parties and any(values[i] is not None for _, values in rows.values())
        ]
        options: dict[str, str] = {}
        for abbreviation in running:
            option = options[abbreviation] = key("option", e.day, district, abbreviation)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(abbreviation),
                ballot_label=(BALLOT_LABEL_1986 if e.day.year == 1986 else {}).get(
                    abbreviation, BALLOT_LABELS[abbreviation]
                ),
            )
            if abbreviation in regierung.PARTIES:
                p.external.add(party(abbreviation))
        won = e.seats[district]
        if sum(won.values()) != magnitude:
            raise ValueError(f"{e.day} {district}: seats {won} do not fill {magnitude}")
        for abbreviation, count in won.items():
            if count:
                p.add("outcome", result_set_id=set_id, option_id=options[abbreviation], kind="won", seats=count)
        boxes: dict[tuple[str, int], Box] = {}
        for t in {id(t): t for t, _ in rows.values()}.values():
            boxes |= {k: v for k, v in locate(e, t).items() if k[0] in rows}
        for row, (t, values) in rows.items():
            for i, (column, value) in enumerate(zip(e.columns, values, strict=True)):
                if value is None or column in SKIPPED:
                    continue
                if column in ACCOUNTING:
                    option, measure = None, ACCOUNTING[column]
                else:
                    option, measure = options[column], list_measure
                p.add(
                    "result_value",
                    result_set_id=set_id,
                    unit_id=units[row],
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
                box = boxes.get((row, i))
                if box is None:
                    raise ValueError(f"{e.day} {row} {column}: no box on page {t.page}")
                p.add(
                    "value_locator",
                    result_set_id=set_id,
                    unit_id=units[row],
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=page_ids[t.page],
                    source_id=LB_SOURCE,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                )
        for column, value in e.totals[district].items():
            if column in SKIPPED:
                continue
            if column in ACCOUNTING:
                p.add("declared", result_set_id=set_id, option_id=None, measure=ACCOUNTING[column], value=value)
            elif column in options:
                p.add("declared", result_set_id=set_id, option_id=options[column], measure=list_measure, value=value)
    for code, reason in ACCEPTED.get(e.day, {}).items():
        p.accept(code, reason)
    if threshold == 8:
        p.accept(
            "accounting.votes_valid",
            "Each voter has as many votes as the district has seats, so the party votes never add up to the valid "
            "ballots. Party votes are stored as list votes (measure 8) and valid ballots as valid (measure 4).",
        )
    return p


# The majority-vote elections of 1918-1936.

MAJORITY_DATA = Path(__file__).with_name("landtag_1918_1936.json")
MAJORITY_REGISTRY = f"{SOURCE}/registry-1918"
MAJORITY_SOURCE = key("source", SOURCE, "1918")
PAPERS = {
    "000476564": "Liechtensteiner Volksblatt",
    "000476558": "Oberrheinische Nachrichten",
    "000476556": "Liechtensteiner Nachrichten",
    "000469834": "Liechtensteiner Vaterland",
}
# Electoral systems: (key, first day, last day, rules, note).
MAJORITY_SYSTEMS = {
    "1918": (
        date(1918, 1, 1),
        date(1931, 12, 31),
        {"formula": "two_round", "majority": 0.5},
        (
            "Each district elected its members by absolute majority. Each voter wrote as many names as the district had "
            "seats; a candidate was elected with more than half of the valid ballots. Seats left over went to a runoff "
            "a week or so later among twice as many of the next candidates as seats remained, decided by the most "
            "votes. The Oberland elected 7 and the Unterland 5 of the 15 members in 1918, the prince appointing the "
            "other three; from 1922, under the constitution of 1921, the districts elected all 15, 9 and 6."
        ),
    ),
    "1932-municipal": (
        date(1932, 1, 1),
        date(1938, 12, 31),
        {"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5}, {"fromRound": 3, "share": 0}]},
        (
            "Under the law of 1932 each municipality except Planken elected one member: a candidate with an absolute "
            "majority was elected in the first or second ballot of the day, and the third ballot was decided by the "
            "most votes."
        ),
    ),
    "1932-national": (
        date(1932, 1, 1),
        date(1938, 12, 31),
        {"formula": "block"},
        (
            "Under the law of 1932 the five members not elected by the municipalities, four from the Oberland and one "
            "from the Unterland, were elected by the voters of the whole country a week after the municipal round. Each "
            "voter wrote five names, and the candidates with the most votes were elected."
        ),
    ),
}
MAJORITY_METHOD = (
    "Counted by the municipalities; digitized from the {paper} of {issue}, which prints {what}. A candidate's votes "
    "are the ballots that named the candidate; each voter could name as many candidates as there were seats, so the "
    "votes do not add up to the ballots. Each value keeps the page and the box of the printed figure."
)
OUTCOMES = {"elected": "elected", "advanced": "advanced", "eliminated": "eliminated", "lost": "lost"}


@dataclass
class Candidate:
    name: str
    place: str
    votes: list[int | None]
    total: int
    outcome: str
    title: str | None
    party: str | None


@dataclass
class Contest:
    district: str
    scheme: str
    magnitude: int
    issue: str
    page: str
    box: tuple[float, float, float, float]
    units: list[str]
    accounting: dict[str, list[int]]
    winner_only: bool
    candidates: list[Candidate]


@dataclass
class Round:
    day: date
    contests: list[Contest]


@dataclass
class Majority:
    day: date
    kind: str
    system: str
    notes: list[str]
    rounds: list[Round]


def majority() -> list[Majority]:
    loaded = json.loads(MAJORITY_DATA.read_text(encoding="utf-8"))
    return [
        Majority(
            day=date.fromisoformat(m["date"]),
            kind=m["kind"],
            system=m["system"],
            notes=m["notes"],
            rounds=[
                Round(
                    day=date.fromisoformat(r["date"]),
                    contests=[
                        Contest(
                            district=c["district"],
                            scheme=c.get("scheme", "li.wahlkreis"),
                            magnitude=c["magnitude"],
                            issue=c["issue"],
                            page=c["page"],
                            box=tuple(c["box"]),
                            units=c["units"],
                            accounting={a: c[a] for a in ("registered", "cast", "valid", "invalid", "blank") if a in c},
                            winner_only=bool(c.get("winner_only")),
                            candidates=[
                                Candidate(
                                    d["name"],
                                    d["place"],
                                    d["votes"],
                                    d["total"],
                                    d["outcome"],
                                    d.get("title"),
                                    d.get("party"),
                                )
                                for d in c["candidates"]
                            ],
                        )
                        for c in r["contests"]
                    ],
                )
                for r in m["rounds"]
            ],
        )
        for m in loaded
    ]


ACCOUNTING_MEASURES = {"registered": 2, "cast": 3, "valid": 4, "invalid": 5, "blank": 6}
# Unit codes: a municipality's ISO code, a district's name, or the joint count of Schaan and Planken in 1918.
JOINT = {"Schaan+Planken": ("Oberland", ("LI-07", "LI-05"))}


def unit_area(name: str) -> tuple[str, str, str, str]:
    """(scheme, code, area relation, area) of a printed unit."""
    if name in JOINT:
        district, codes = JOINT[name]
        return "li.gemeinde", "+".join(codes), "within", regierung.area("li.wahlkreis", district)
    if name in DISTRICTS:
        return "li.wahlkreis", name, "equals", regierung.area("li.wahlkreis", name)
    return "li.gemeinde", CODES[name], "equals", regierung.area("li.gemeinde", CODES[name])


def contest_area(c: Contest) -> str:
    if c.district == "Liechtenstein":
        return regierung.NATION
    if c.scheme == "li.gemeinde":
        return regierung.area("li.gemeinde", CODES[c.district])
    return regierung.area("li.wahlkreis", c.district)


def person_key(c: Candidate) -> str:
    return key("person", c.name, c.place, c.title or "")


def majority_system(m: Majority, c: Contest) -> str:
    if m.system == "1918":
        return key("electoral_system", "majority", "1918")
    return key("electoral_system", "majority", "1932-national" if c.district == "Liechtenstein" else "1932-municipal")


LINE = 36.0


def table_boxes(c: Contest) -> dict[tuple[int, int], Box]:
    """The box of each printed candidate value as {(candidate, unit): box}, found among the ALTO words of the table.

    A value printed once in the table is placed first; the rows and columns those values fix then place the others at
    the nearest word with the same digits, or, for a dash or a misread figure, at the crossing of their row and column.
    """
    x0, y0, w, h = c.box
    # Some pages' ALTO gives words no height; such a word takes the height of a line of type.
    inside = [
        Word(word.text, word.x0, word.y0, word.x1, max(word.y1, word.y0 + LINE))
        for word in words(c.issue, c.page)
        if x0 <= word.x0 <= x0 + w and y0 <= word.y0 <= y0 + h and word.digits and len(word.digits) <= 4
    ]
    found: dict[tuple[int, int], Box] = {}
    used: set[int] = set()
    cells = [
        (i, j, value)
        for i, cand in enumerate(c.candidates)
        for j, value in enumerate(cand.votes)
        if value is not None and value > 0
    ]
    counts: dict[str, int] = defaultdict(int)
    for word in inside:
        counts[word.digits] += 1
    wanted: dict[str, int] = defaultdict(int)
    for _, _, value in cells:
        wanted[str(value)] += 1
    for i, j, value in cells:
        if counts[str(value)] == 1 and wanted[str(value)] == 1:
            word = next(word for word in inside if word.digits == str(value))
            used.add(id(word))
            found[(i, j)] = (word.x0, word.y0, word.x1, word.y1)

    def lines() -> tuple[dict[int, float], dict[int, float], float, float]:
        rows: dict[int, list[float]] = defaultdict(list)
        columns: dict[int, list[float]] = defaultdict(list)
        heights, widths = [], []
        for (i, j), box in found.items():
            rows[i].append((box[1] + box[3]) / 2)
            columns[j].append((box[0] + box[2]) / 2)
            heights.append(box[3] - box[1])
            widths.append(box[2] - box[0])
        return (
            {i: median(v) for i, v in rows.items()},
            {j: median(v) for j, v in columns.items()},
            median(heights) if heights else h / max(len(c.candidates), 1),
            median(widths) if widths else w / (len(c.units) + 2),
        )

    def guess(i: int, j: int, rows: dict[int, float], columns: dict[int, float]) -> tuple[float, float] | None:
        def between(index: int, known: dict[int, float], count: int) -> float | None:
            if index in known:
                return known[index]
            before = [k for k in known if k < index]
            after = [k for k in known if k > index]
            if before and after:
                a, b = max(before), min(after)
                return known[a] + (known[b] - known[a]) * (index - a) / (b - a)
            if len(known) >= 2:
                a, b = sorted(known)[:2] if after else sorted(known)[-2:]
                step = (known[b] - known[a]) / (b - a)
                return known[a] + step * (index - a)
            return None

        y = between(i, rows, len(c.candidates))
        x = between(j, columns, len(c.units))
        return None if x is None or y is None else (x, y)

    for _ in range(2):
        rows, columns, height, width = lines()
        for i, j, value in cells:
            if (i, j) in found:
                continue
            at = guess(i, j, rows, columns)
            if at is None:
                continue
            near = [
                word
                for word in inside
                if id(word) not in used
                and word.digits == str(value)
                and abs((word.y0 + word.y1) / 2 - at[1]) < height * 1.5
                and abs((word.x0 + word.x1) / 2 - at[0]) < width * 2
            ]
            if near:
                ax, ay = at
                word = min(near, key=lambda word: abs((word.y0 + word.y1) / 2 - ay) + abs(word.x0 - ax) / 4)
                used.add(id(word))
                found[(i, j)] = (word.x0, word.y0, word.x1, word.y1)
    rows, columns, height, width = lines()
    for i, cand in enumerate(c.candidates):
        for j, value in enumerate(cand.votes):
            if value is None or (i, j) in found:
                continue
            at = guess(i, j, rows, columns)
            if at is None:
                found[(i, j)] = (x0, y0, x0 + w, y0 + h)
            else:
                found[(i, j)] = (at[0] - width / 2, at[1] - height / 2, at[0] + width / 2, at[1] + height / 2)
    return found


def majority_registry() -> Package:
    p = Package(
        MAJORITY_REGISTRY,
        SOURCE,
        "The newspaper archive as the source of the Landtag's majority-vote elections of 1918-1936, and their "
        "electoral systems",
    )
    p.external.update({LIECHTENSTEIN, *(regierung.party(a) for a in ("FBP", "VU"))})
    p.add(
        "source",
        id=MAJORITY_SOURCE,
        kind="official_body",
        publisher="Regierung des Fürstentums Liechtenstein and the country's newspapers (eLiechtensteinensia, "
        "Liechtensteinische Landesbibliothek)",
        title="Liechtensteiner Volksblatt, Oberrheinische Nachrichten, Liechtensteiner Nachrichten and Liechtensteiner "
        "Vaterland: results of the Landtag elections, 1918-1936",
        url="https://www.eliechtensteinensia.li/",
        license="Public record; scans by the Liechtensteinische Landesbibliothek",
        license_url="https://www.landesbibliothek.li/",
        attribution="eLiechtensteinensia, Liechtensteinische Landesbibliothek",
    )
    for name, (first, last, rules, body) in MAJORITY_SYSTEMS.items():
        id = key("electoral_system", "majority", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=LIECHTENSTEIN,
            valid_from=first,
            valid_to=last,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=body, lang="en")
    return p


def majority_label(m: Majority) -> str:
    same = [o for o in majority() if o.day.year == m.day.year and o.kind == m.kind]
    return f"{m.day:%B} {m.day.year}" if len(same) > 1 else str(m.day.year)


def majority_election(m: Majority) -> Package:
    label_ = majority_label(m)
    noun = "by-election" if m.kind == "by_election" else "parliamentary election"
    p = Package(
        f"{SOURCE}/{m.day}",
        SOURCE,
        f"The {label_} Liechtenstein Landtag {'by-election' if m.kind == 'by_election' else 'election'} by "
        "candidate and municipality, from the country's newspapers",
        depends=[MAJORITY_REGISTRY],
    )
    p.external.update({LIECHTENSTEIN, regierung.LANDTAG_BODY, MAJORITY_SOURCE, "li.gemeinde", "li.wahlkreis"})
    p.external.update(
        regierung.party(cand.party) for r in m.rounds for c in r.contests for cand in c.candidates if cand.party
    )
    last = m.rounds[-1].day if m.system != "1918" else m.day
    election_id = key("election", "majority", m.day)
    p.add(
        "election",
        id=election_id,
        polity_id=LIECHTENSTEIN,
        kind=m.kind,
        date_from=m.day,
        date_to=last,
        status="held",
    )
    p.name(election_id, f"{label_} Liechtenstein {noun}", "en", "official")
    german = "Landtagsersatzwahl" if m.kind == "by_election" else "Landtagswahlen"
    p.name(election_id, f"{german} {label_}", "de", "native", False)
    slug_kind = "by-election" if m.kind == "by_election" else "general"
    path = f"li/{m.day}-{slug_kind}"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add(
        "citation",
        id=key("citation", election_id),
        entity_id=election_id,
        source_id=MAJORITY_SOURCE,
        locator=json.dumps({"url": f"{VIEWER}/{m.rounds[0].contests[0].issue}/", "paper": paper(m.rounds[0])}),
    )
    pages: dict[tuple[str, str], str] = {}
    units: dict[tuple[date, str], str] = {}
    first_round: dict[str, str] = {}
    for number, r in enumerate(m.rounds, start=1):
        runoff = m.system == "1918" and number > 1
        holder = election_id
        round_path = path
        if runoff:
            holder = key("election", "majority", r.day)
            round_path = f"li/{r.day}-runoff"
            p.add(
                "election",
                id=holder,
                polity_id=LIECHTENSTEIN,
                parent_id=election_id,
                kind="runoff",
                date_from=r.day,
                date_to=r.day,
                status="held",
            )
            p.name(holder, f"{label_} Liechtenstein {noun}, second round", "en", "official")
            p.name(holder, f"Landtagswahlen {label_}, Stichwahl", "de", "native", False)
            p.add("slug", path=round_path, entity_id=holder, is_current=True)
        for c in r.contests:
            check_majority(m, r, c)
            if (c.issue, c.page) not in pages:
                alto = raw.cached(SOURCE, alto_url(c.issue, c.page))
                p.uses(raw.cached(SOURCE, manifest_url(c.issue)))
                artifact = p.artifact(alto, MAJORITY_SOURCE)
                width, height = canvas(c.issue, c.page)
                pages[(c.issue, c.page)] = key("page", c.issue, c.page)
                p.add(
                    "source_page",
                    id=pages[(c.issue, c.page)],
                    artifact_id=artifact,
                    page_number=int(c.page),
                    image_key=f"pages/{SOURCE}/{c.issue}/{c.page}.jpg",
                    width=width,
                    height=height,
                )
            for name in c.units:
                if (r.day, name) in units:
                    continue
                scheme, code, relation, area = unit_area(name)
                p.external.add(area)
                units[(r.day, name)] = key("unit", r.day, code)
                p.add(
                    "reporting_unit",
                    id=units[(r.day, name)],
                    polity_id=LIECHTENSTEIN,
                    scheme_code=scheme,
                    code=f"{r.day}:{code}",
                    area_relation=relation,
                    area_id=area,
                    kind="standard",
                )
            contest_id = key("contest", "majority", r.day, c.district)
            area = contest_area(c)
            p.external.add(area)
            system = majority_system(m, c)
            p.external.add(system)
            p.add(
                "contest",
                id=contest_id,
                election_id=holder,
                kind="body_seats",
                body_id=regierung.LANDTAG_BODY,
                district_area_id=area,
                magnitude=c.magnitude,
                round_number=2 if runoff else 1,
                previous_round_id=first_round.get(c.district) if runoff else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            if not runoff:
                first_round[c.district] = contest_id
            where = {
                "Liechtenstein": ("national seats", "Landesweite Mandate"),
            }.get(
                c.district,
                (f"{c.district} municipal seat", f"Gemeindemandat {c.district}")
                if c.scheme == "li.gemeinde"
                else (f"{c.district} electoral district", f"Wahlkreis {c.district}"),
            )
            suffix = (", second round", ", Stichwahl") if runoff else ("", "")
            p.name(contest_id, f"Landtag, {where[0]}{suffix[0]}", "en", "english")
            p.name(contest_id, f"{where[1]}{suffix[1]}", "de", "official", False)
            slug = "national" if c.district == "Liechtenstein" else changeset.slug(c.district)
            p.add("slug", path=f"{round_path}/{slug}", entity_id=contest_id, is_current=True)
            set_id = key("result_set", "majority", r.day, c.district)
            p.add(
                "result_set",
                id=set_id,
                election_id=holder,
                contest_id=contest_id,
                stage="official",
                kind="counted",
                source_id=MAJORITY_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            issue_day = date.fromisoformat(c.issue.split("_")[1])
            what = (
                "the elected candidate's votes and the ballots by municipality"
                if c.winner_only
                else "each candidate's votes by electoral district"
                if c.units == [c.district]
                else "each candidate's votes by municipality"
            )
            method = MAJORITY_METHOD.format(
                paper=PAPERS[c.issue[:9]],
                issue=f"{issue_day:%-d %B %Y}",
                what=what,
            )
            p.add(
                "note",
                id=key("note", set_id),
                entity_id=set_id,
                kind="methodology",
                body_md=" ".join([method, *m.notes]),
                lang="en",
            )
            boxes = table_boxes(c)
            for i, cand in enumerate(c.candidates):
                option = key("option", "majority", r.day, c.district, cand.name, cand.place, cand.title or "")
                ballot = f"{cand.name}, {cand.place}"
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="candidate",
                    party_id=regierung.party(cand.party) if cand.party else None,
                    ballot_label=ballot,
                )
                who = person_key(cand)
                if not any(row["id"] == who for row in p.rows.get("person", [])):
                    p.add("person", id=who, gender="male")
                    p.name(who, cand.name, "de", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", "majority", r.day, c.district, cand.name, cand.place, cand.title or ""),
                    person_id=who,
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    member_of_party_id=regierung.party(cand.party) if cand.party else None,
                    is_independent=False,
                )
                p.add("outcome", result_set_id=set_id, option_id=option, kind=OUTCOMES[cand.outcome])
                for j, value in enumerate(cand.votes):
                    if value is None:
                        continue
                    stored(p, set_id, units[(r.day, c.units[j])], option, 1, value)
                    box = boxes[(i, j)]
                    locate_value(p, set_id, units[(r.day, c.units[j])], option, 1, pages[(c.issue, c.page)], box)
                if len(c.units) > 1 and not c.winner_only:
                    p.add("declared", result_set_id=set_id, option_id=option, measure=1, value=cand.total)
            if c.accounting:
                for column, values in c.accounting.items():
                    for j, value in enumerate(values):
                        unit = units[(r.day, c.units[j])]
                        stored(p, set_id, unit, None, ACCOUNTING_MEASURES[column], value)
                        # Accounting figures stand in their own block of the table; their box is the table's.
                        x0, y0, w, h = c.box
                        locate_value(
                            p,
                            set_id,
                            unit,
                            None,
                            ACCOUNTING_MEASURES[column],
                            pages[(c.issue, c.page)],
                            (x0, y0, x0 + w, y0 + h),
                        )
    for code, reason in majority_accepted(m).items():
        p.accept(code, reason)
    return p


def paper(r: Round) -> str:
    return PAPERS[r.contests[0].issue[:9]]


def stored(p: Package, set_id: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=set_id,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def locate_value(p: Package, set_id: str, unit: str, option: str | None, measure: int, page: str, box: Box) -> None:
    p.add(
        "value_locator",
        result_set_id=set_id,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=page,
        source_id=MAJORITY_SOURCE,
        x0=box[0],
        y0=box[1],
        x1=box[2],
        y1=box[3],
    )


def check_majority(m: Majority, r: Round, c: Contest) -> None:
    """Every row has a value per unit, the elected fill the seats, and the accounting columns add up."""
    for cand in c.candidates:
        if len(cand.votes) != len(c.units):
            raise ValueError(f"{r.day} {c.district} {cand.name}: {len(cand.votes)} values for {len(c.units)} units")
        if cand.outcome not in OUTCOMES:
            raise ValueError(f"{r.day} {c.district} {cand.name}: unknown outcome {cand.outcome}")
    elected = sum(1 for cand in c.candidates if cand.outcome == "elected")
    advanced = sum(1 for cand in c.candidates if cand.outcome == "advanced")
    if elected + (advanced // 2 if advanced else 0) != c.magnitude:
        raise ValueError(
            f"{r.day} {c.district}: {elected} elected and {advanced} in the runoff for {c.magnitude} seats"
        )
    for column, values in c.accounting.items():
        if len(values) != len(c.units):
            raise ValueError(f"{r.day} {c.district} {column}: {len(values)} values for {len(c.units)} units")


def majority_accepted(m: Majority) -> dict[str, str]:
    """Findings the printed figures cause, with the reason; each is checked against the data before it is kept."""
    reasons: dict[str, str] = {}
    mismatches = []
    for r in m.rounds:
        for c in r.contests:
            for cand in c.candidates:
                if len(c.units) > 1 and not c.winner_only and sum(v or 0 for v in cand.votes) != cand.total:
                    mismatches.append(cand.name)
    if mismatches:
        reasons["accounting.declared"] = (
            "The paper prints totals that differ from the municipal votes of the same row ("
            + ", ".join(sorted(set(mismatches)))
            + "); the methodology note lists each, and both the municipal votes and the totals are stored as printed."
        )
    short = [
        c
        for r in m.rounds
        for c in r.contests
        if c.magnitude == 1
        and not c.winner_only
        and "valid" in c.accounting
        and any(
            sum(cand.votes[j] or 0 for cand in c.candidates) != c.accounting["valid"][j] for j in range(len(c.units))
        )
    ]
    if short:
        reasons["accounting.votes_valid"] = (
            "The paper prints the votes of the candidates each party put up; the valid ballots also count the "
            "scattered votes for other persons, which it does not list, so the candidate votes fall short of them."
        )
    if any(c.winner_only for r in m.rounds for c in r.contests):
        reasons["accounting.votes_valid"] = (
            "The government's notice of the 1932 municipal round prints only the elected candidate's votes in each "
            "municipality, so the stored candidate votes fall short of the valid ballots."
        )
    for r in m.rounds:
        for c in r.contests:
            a = c.accounting
            if "cast" in a and any(
                a["cast"][j] != a["valid"][j] + a["invalid"][j] + a["blank"][j] for j in range(len(c.units))
            ):
                reasons["accounting.ballots_sum"] = (
                    "The runoff protocol of 29 July 1928 prints Balzers with 257 ballots against 148 valid, 8 invalid "
                    "and 103 blank, while every column adds up to its printed total; stored as printed."
                )
    return reasons
