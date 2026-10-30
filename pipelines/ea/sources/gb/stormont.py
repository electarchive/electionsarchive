import html
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.stormont"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://web.archive.org/web/2005id_/http://www.election.demon.co.uk/stormont/{}.html"
PAGES = (
    "borough",
    "counties",
    "belfast",
    "ldrcity",
    "antrim",
    "armagh",
    "down",
    "fermanagh",
    "ldrcounty",
    "tyrone",
    "qub",
)
EXTRA = ("stormont", "totals", "members", "intro", "boundaries")
INBOX = DATA / "inbox" / "manual" / "MF-337"
CAIN = {
    "https://cain.ulster.ac.uk/issues/politics/election/rs1969.htm": "ark-stormont-1969.html",
    "https://cain.ulster.ac.uk/issues/politics/election/rsby1970.htm": "ark-stormont-by-1970-bannside-south-antrim.html",
}


def fetch() -> None:
    for page in (*PAGES, *EXTRA):
        raw.fetch(SOURCE, BASE.format(page), suffix=".html")
    for url, name in CAIN.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            file = INBOX / name
            stamp = datetime.fromtimestamp(file.stat().st_mtime, UTC)
            raw.store(SOURCE, url, file.read_bytes(), suffix=".html", retrieved_at=stamp, media_type="text/html")


def rows(page: str) -> list[list[tuple[str, int, list[str]]]]:
    text = raw.cached(SOURCE, BASE.format(page)).path.read_bytes().decode("latin-1")
    found = []
    for chunk in re.split(r"<TR[^>]*>", text, flags=re.IGNORECASE)[1:]:
        chunk = re.split(r"</TR>", chunk, flags=re.IGNORECASE)[0]
        cells = []
        for tag, attrs, body in re.findall(
            r"<T([DH])([^>]*)>(.*?)(?=<T[DH][\s>]|$)", chunk, flags=re.IGNORECASE | re.DOTALL
        ):
            span = re.search(r"COLSPAN=(\d+)", attrs, re.IGNORECASE)
            heading = re.search(r"<H([23])", body, re.IGNORECASE)
            lines = [
                html.unescape(re.sub(r"<[^>]*>", "", part)).replace("\xa0", " ").replace("&nbsp", " ").strip()
                for part in re.split(r"<BR>", body, flags=re.IGNORECASE)
            ]
            kind = f"H{heading.group(1)}" if heading else tag.upper()
            cells.append((kind, int(span.group(1)) if span else 1, [re.sub(r"\s+", " ", line) for line in lines]))
        if cells:
            found.append(cells)
    return found


GENERALS = {
    1921: date(1921, 5, 24),
    1925: date(1925, 4, 3),
    1929: date(1929, 5, 22),
    1933: date(1933, 11, 30),
    1938: date(1938, 2, 9),
    1945: date(1945, 6, 14),
    1949: date(1949, 2, 10),
    1953: date(1953, 10, 22),
    1958: date(1958, 3, 20),
    1962: date(1962, 5, 31),
    1965: date(1965, 11, 25),
    1969: date(1969, 2, 24),
}
MAGNITUDES = {
    "Belfast East": 4,
    "Belfast North": 4,
    "Belfast South": 4,
    "Belfast West": 4,
    "Antrim": 7,
    "Armagh": 4,
    "Down": 8,
    "Fermanagh and Tyrone": 8,
    "Londonderry": 5,
    "Queen's University of Belfast": 4,
}
REDATED = {("Newtownabbey", "1929"): "1969"}
TITLES = re.compile(
    r"^((Rt\.|Hon\.|Sir|Dr\.|Rev\.|Prof\.|Professor|Major|Captain|Capt\.|Commander|Lt-Col\.|Lt\.-Col\.|Colonel|Col\.|"
    r"Lieutenant-Colonel|Brigadier|Mrs\.|Miss|Lady|Lord|Dame|Very|Canon|Senator|Viscount|Earl|Wing|Group|Squadron|Leader|"
    r"Lt\.|Lieutenant|Surgeon|Admiral|Rear-Admiral|Mr\.|Professor)\s+)+"
)
ORDINALS = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth")


def number(text: str) -> float | None:
    text = text.replace(",", "").replace("*", "").strip()
    return float(text) if re.fullmatch(r"-?\d+(\.\d+)?", text) else None


def constituency(lines: list[str], page: str) -> tuple[str, int]:
    text = " ".join(line for line in lines if line).replace("’", "'")
    seats = re.search(r"\((\w+) Seats\)", text)
    text = re.sub(r"\s*\[\d+\]|\s*\(\w+ Seats\)", "", text).strip()
    if text.startswith("THE QUEEN'S"):
        return "Queen's University of Belfast", 4
    county, _, division = text.partition(", ")
    title = re.sub(r"\b(Of|And)\b", lambda m: m.group(1).lower(), (division or county).title()).replace("'S", "'s")
    if county == "BELFAST":
        title = f"Belfast {title}"
    if page in ("borough", "counties"):
        if title not in MAGNITUDES:
            raise ValueError(f"{page}: {text}")
        return title, MAGNITUDES[title]
    if seats is not None:
        raise ValueError(f"{page}: {text} has {seats.group(1)} seats")
    return title, 1


def slots(names: list[str], parties: list[str]) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    carried = ""
    for index, line in enumerate(names):
        party = parties[index] if index < len(parties) else ""
        if line in ("Non-transferable", "Total"):
            break
        if party.startswith("(") and not line and found:
            last = found[-1]
            found[-1] = (last[0], last[1], f"{last[2]} {party}")
        elif line and not party:
            carried = f"{carried}{line}" if carried.endswith("-") else f"{carried} {line}".strip()
        elif party:
            name = f"{carried}{line}" if carried.endswith("-") else f"{carried} {line}".strip()
            found.append((index, name, party))
            carried = ""
    return found


def bare(name: str) -> str:
    name = re.sub(r",.*$", "", name.replace("’", "'"))
    name = re.sub(r"\((\d)(?:st|nd|rd|th)?\)", r"<\1>", name)
    name = re.sub(r"\s*\([^)]*\)", "", name)
    return TITLES.sub("", name).replace("<", "(").replace(">", ")").strip()


def surname(name: str) -> tuple[str, list[str]]:
    parts = name.split()
    cut = len(parts) - 2 if len(parts) > 2 and parts[-2] in ("de", "van", "von", "Mac", "Mc") else len(parts) - 1
    given = " ".join(parts[:cut])
    initials = (
        re.findall(r"[A-Z]", given)
        if "." in given or len(given) <= 3
        else [w[0] for w in re.split(r"[\s-]+", given) if w]
    )
    return fold(" ".join(parts[cut:])), initials


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text.replace("’", "'")).encode("ascii", "ignore").decode().lower()


def members() -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    found: list[tuple[str, str, str]] = []
    aliases: dict[str, str] = {}
    for row in rows("members"):
        text = " ".join(line for line in row[0][2] if line)
        if " - see " in text:
            alias, target = text.split(" - see ")
            aliases[bare(alias)] = target
        elif len(row) == 3 and row[0][0] == "D" and re.search(r"\d{4}", " ".join(row[2][2])):
            found.append((text, bare(text), " ".join(row[2][2])))
    return found, aliases


def member(
    name: str, place: str, roster: list[tuple[str, str, str]], aliases: dict[str, str], strict: bool
) -> str | None:
    key = bare(name)
    if key in aliases:
        return aliases[key]
    last, initials = surname(key)
    hits = [full for full, clean, _ in roster if surname(clean) == (last, initials)]
    if len(hits) > 1 or strict:
        county = fold(place.replace("Belfast ", "").replace("Queen's University of Belfast", "Queen")).split()[-1]
        hits = [full for full, clean, seats in roster if full in hits and county in fold(seats)]
    if not hits:
        target = next((aliases[alias] for alias in aliases if surname(alias) == (last, initials)), None)
        return target if target is not None and not strict else None
    return hits[0] if len(hits) == 1 else None


def polled(year: str, extra: str | None) -> tuple[date, bool]:
    if extra:
        day, month = (int(part) for part in extra.strip("()").split("/"))
        return date(int(year), month, day), False
    return GENERALS[int(year)], True


def parse() -> None:
    contests: list[dict[str, object]] = []
    lines: list[dict[str, object]] = []
    stages: list[dict[str, object]] = []
    notes: list[tuple[str, str, str]] = []
    for page in PAGES:
        label, seats, cause, stats, heading = "", 0, None, {}, None
        pending: list[list[str]] = []
        for row in rows(page):
            kind, _, first = row[0]
            text = " ".join(line for line in first if line)
            if kind == "H2":
                label, seats = constituency(first, page)
                cause, stats, heading = None, {}, None
                continue
            if kind == "H3" and text.startswith("This constituency was abolished"):
                continue
            if kind == "H3":
                heading = re.fullmatch(r"(\d{4})(?: (\(\d+/\d+\)))?", text)
                if heading is None:
                    raise ValueError(f"{page} {label}: {text}")
                stats = {}
                continue
            if kind == "H" or not text:
                continue
            if text.startswith("Electors:"):
                for key, value in re.findall(r"(Electors|Turnout|Quota): ([\d,.]+)", text):
                    stats[key.lower()] = number(value)
                if "(Note:" in text:
                    stats["quota_note"] = text.split("(Note: ", 1)[1].rstrip(")")
                continue
            if re.fullmatch(r"\[.*\]", text):
                cause = text[1:-1]
                continue
            if re.fullmatch(r"Notes?:-", text):
                continue
            if re.fullmatch(r"\d{4}(-|:)", first[0]) and len(row) in (2, 3):
                notes.append((label, first[0][:4], " ".join(line for line in row[1][2] if line)))
                continue
            stv = heading is not None and not re.fullmatch(r"\d{4}", first[0])
            if stv:
                day, general = polled(heading.group(1), heading.group(2))
                electors, turnout = stats.get("electors"), stats.get("turnout")
                names, parties = first, row[1][2]
                counts = [cell[2] for cell in row[2:]]
                marks = counts.pop() if len(counts) > 1 and set(counts[-1]) <= {"", "*"} else []
                result = counts.pop() if len(counts) % 2 == 0 else None
                totals = [*pending, *counts[2::2]] if pending else counts[0::2]
                if result is None and len(counts) > 1:
                    pending = totals
                    continue
                pending = []
            else:
                if not re.fullmatch(r"\d{4}", first[0]):
                    raise ValueError(f"{page} {label}: unexpected row {text}")
                day, general = polled(
                    REDATED.get((label, first[0]), first[0]), first[1] if len(first) > 1 and first[1] else None
                )
                electors, turnout = number(row[1][2][0]), number(row[2][2][0])
                names, parties = row[4][2], row[5][2]
                totals, result, marks = [row[6][2]], None, row[7][2]
            magnitude = seats if general else 1
            key = f"{day}:{label}"
            candidates = slots(names, parties)
            unopposed = all(totals[0][index] == "unop." for index, _, _ in candidates)
            contests.append(
                {
                    "key": key,
                    "page": page,
                    "constituency": label,
                    "seats": magnitude,
                    "day": day,
                    "general": general,
                    "cause": None if general else cause,
                    "vacant": cause if general else None,
                    "redated": (label, first[0]) in REDATED if not stv else False,
                    "electors": electors,
                    "turnout": turnout,
                    "quota": stats.get("quota"),
                    "quota_note": stats.get("quota_note"),
                    "stv": page in ("borough", "counties", "qub"),
                    "unopposed": unopposed,
                }
            )
            cause, stats = None, {}
            if not stv:
                heading = None
            ranked = [index for index, _, _ in candidates]
            if stv and not unopposed:
                for stage, column in enumerate(totals, 1):
                    spare = names.index("Non-transferable") if "Non-transferable" in names else None
                    total = names.index("Total") if "Total" in names else None
                    stages.append(
                        {
                            "key": key,
                            "stage": stage,
                            "nontransferable": number(column[spare])
                            if spare is not None and spare < len(column)
                            else None,
                            "total": number(column[total]) if total is not None and total < len(column) else None,
                        }
                    )
            elected_order = {}
            if result is not None:
                for index in ranked:
                    if index < len(result) and result[index]:
                        elected_order[index] = ORDINALS.index(result[index]) + 1
            for order, (index, name, party) in enumerate(candidates, 1):
                daggers = row[3][2] if not stv else []
                deposit = [f"{mark}{cell}" for mark, cell in zip(marks + [""] * len(totals[0]), totals[0])]
                series = [column[index] if index < len(column) else "" for column in totals]
                votes = [number(cell) for cell in series]
                lines.append(
                    {
                        "key": key,
                        "number": order,
                        "name": name,
                        "party": party,
                        "votes": votes,
                        "elected": 0,
                        "order": elected_order.get(index),
                        "lost_deposit": "*" in (deposit[index] if index < len(deposit) else ""),
                        "sitting": "†" in (daggers[index] if index < len(daggers) else ""),
                    }
                )
    frame = pl.DataFrame(lines, infer_schema_length=None)
    contest_frame = pl.DataFrame(contests, infer_schema_length=None)
    frame = frame.join(contest_frame.select("key", "seats", "stv", "unopposed"), on="key")
    elected = []
    for row in frame.iter_rows(named=True):
        if row["unopposed"]:
            elected.append(True)
        elif row["stv"] and row["seats"] > 1:
            elected.append(row["order"] is not None)
        else:
            first = row["votes"][0] or 0
            rivals = frame.filter((pl.col("key") == row["key"]) & (pl.col("number") != row["number"]))
            elected.append(all(first > (other[0] or 0) for other in rivals["votes"]))
    roster, aliases = members()
    frame = frame.with_columns(elected=pl.Series(elected)).drop("seats", "stv", "unopposed")
    frame = frame.with_columns(
        member=pl.Series(
            [
                member(row["name"], row["key"].split(":", 1)[1], roster, aliases, not row["elected"])
                for row in frame.iter_rows(named=True)
            ],
            dtype=pl.String,
        )
    )
    unmatched = frame.filter(pl.col("elected") & pl.col("member").is_null())
    if unmatched.height:
        raise ValueError(f"winners missing from the members list: {unmatched['name'].to_list()}")
    PARSED.mkdir(parents=True, exist_ok=True)
    contest_frame.write_parquet(PARSED / "contests.parquet")
    frame.write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(stages, infer_schema_length=None).write_parquet(PARSED / "stages.parquet")
    pl.DataFrame(notes, schema=["constituency", "year", "body"], orient="row").write_parquet(PARSED / "notes.parquet")
    print(contest_frame.height, "contests,", frame.height, "candidacies,", len(notes), "notes")


VOTES, REGISTERED, VALID, EXHAUSTED, QUOTA = 1, 2, 4, 21, 26
NIR = "NIR"
DISSOLVED = date(1973, 7, 18)
KNOWN = {
    "U": "b1402dfb-7f85-53f1-a9a6-15372fe29396",
    "N": "b2bedbc0-d575-5e1c-98db-c87bd700d9e7",
    "Lab": "a68d116c-c61b-56cb-8a25-7ada9991d955",
    "SF": "01a13bd3-6475-7344-8c51-dd5068c68834",
    "Rep LP": "8f42dfcb-a9b7-569c-bddb-aac2479d20a2",
    "Com": "e22fddbc-6d73-5a91-938c-c8306fb6abdc",
    "CWLP": "7a5f8bc1-f4d5-5360-aa6b-4d6f5a0486ba",
    "Prot U": "ead180d9-5a29-5706-a7bc-68a6bbe1f2b7",
    "Irish Lab": "dbc8a6f1-97b8-5172-bc91-9daf98fa74fb",
    "FF": "01a13bd3-6475-7344-8c51-dd464533b0c9",
    "Nat DP": "bf5d3751-3dd1-50e6-a28b-bd61b4effced",
    "PD": "48989d03-f0c6-5b8e-b56e-d63391e53132",
    "AP": "e55e2f39-40b6-5c9f-a27f-28756bb65eb2",
    "Prog U": "ed00d54c-f38c-5c9a-8f78-28819297660f",
    "Ind U": "b4b90d86-ddd9-554d-804f-15c1584d50c3",
    "UDUP": "31366233-b7b6-553c-a6bc-4b82bcc40e71",
    "SDLP": "b3f8a618-1c9c-569c-a5c0-15dba4be615e",
    "APNI": "bc5c4442-2dc7-504a-a55f-17337893cab8",
}
LIBERAL = ("01a13b8d-570d-753a-9432-089a16f8a68d", "d6753f13-cb67-5ec8-9c02-e4d24a4e457c", date(1956, 1, 1))
MINTED = {
    "Soc Rep": "Socialist Republican",
    "Fed Lab": "Federation of Labour",
    "TA": "Town Tenants' Association",
    "UTA": "Unbought Tenants' Association",
    "NIM": "New Ireland Movement",
    "IUA": "Independent Unionist Association",
    "LO": "Local Option",
    "PPP": "People's Progressive Party",
    "Rep": "Republican",
}
CAIN_DIFFERS = {
    ("1969-02-24:North Armagh", "A.J. Kennedy"): 5487,
    ("1969-02-24:South Tyrone", "J.D. Taylor"): 7686,
    ("1970-04-16:South Antrim", "A. Whitby"): 1733,
}
ONEILL = "O’N"


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet").sort("day", "page", "constituency")
    lines = pl.read_parquet(PARSED / "lines.parquet")
    stages = pl.read_parquet(PARSED / "stages.parquet")
    notes = pl.read_parquet(PARSED / "notes.parquet")
    expansions = {}
    for row in rows("totals"):
        if len(row) == 2 and row[0][2][0] and not re.search(r"\d", row[0][2][0]):
            expansions[row[0][2][0].replace("’", "'")] = re.sub(r" \(pronounced.*", "", row[1][2][0])
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Northern Ireland House of Commons (Stormont) general elections and by-elections 1921 to 1972 by "
            "constituency, with every STV count, from David Boothroyd's results archive, cross-checked against CAIN for "
            "1969 and 1970",
            polity="GB-NIR",
        )

        def name(entity: UUID, text: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind=kind, is_preferred=kind == "official")

        nir = cs.polity("GB-NIR")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = 'gb.stormont'").fetchone() is None:
            cs.add("scheme", code="gb.stormont", polity_id=nir, kind="electoral_district", rank=1, noun="constituency")
        systems = {code: cs.key("gb.electoral_system", code) for code in ("stv", "fptp")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish the stv and fptp electoral systems first")
        body = cs.key("gb.body", "stormont-commons")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=nir, kind="lower_house")
            name(body, "House of Commons of Northern Ireland")
            name(body, "Stormont", "short")
            cs.add(
                "body_size",
                body_id=body,
                seats=52,
                valid_from=GENERALS[1921],
                valid_from_precision="day",
                valid_to=DISSOLVED,
                valid_to_precision="day",
            )
        boothroyd = cs.source("David Boothroyd", "Northern Ireland House of Commons election results 1921–1972")
        cs.add(
            "source",
            id=boothroyd,
            kind="compendium",
            publisher="David Boothroyd",
            title="Northern Ireland House of Commons election results 1921–1972",
            url="http://www.election.demon.co.uk/stormont/stormont.html",
            attribution="Results, counts, and members from David Boothroyd's Northern Ireland Parliamentary election "
            "results (election.demon.co.uk/stormont), read from the Internet Archive's copy.",
            notes="Every general election and by-election to the House of Commons of Northern Ireland, with electors, "
            "turnout, and votes by constituency, and stage-by-stage counts for the single transferable vote contests "
            "of 1921 and 1925 and the Queen's University seat. The site went offline; the archive reads the Wayback "
            "Machine's 2005 copies.",
        )
        for page in (*PAGES, *EXTRA):
            cs.artifact(raw.cached(SOURCE, BASE.format(page)), boothroyd)
        cain = cs.source("CAIN", "Stormont general election 1969 and by-elections 1970")
        cs.add(
            "source",
            id=cain,
            kind="academic",
            publisher="CAIN (Conflict Archive on the Internet), Ulster University",
            title="Stormont General Election (NI) 24 February 1969, and Stormont By-elections (NI) 16 April 1970",
            url="https://cain.ulster.ac.uk/issues/politics/election/rs1969.htm",
            attribution="Draft results compiled by Fionnuala McKenna for CAIN, Ulster University.",
        )
        for url in CAIN:
            cs.artifact(raw.cached(SOURCE, url), cain)

        partition = date(1922, 12, 6)
        nations = [
            (cs.area("gb.country", "IRL", on=GENERALS[1921]), None, partition),
            (cs.area("gb.country", NIR, on=partition), partition, None),
        ]
        if any(nation in cs.minted for nation, _, _ in nations):
            raise ValueError("no gb.country IRL or NIR")
        contests = contests.with_columns(
            era=pl.when(pl.col("constituency").str.starts_with("Queen's") | (pl.col("day") < GENERALS[1929]))
            .then(1921)
            .otherwise(1929)
        )
        contests = contests.with_columns(
            era=pl.when((pl.col("era") == 1929) & (pl.col("day").min().over("constituency", "era") >= GENERALS[1969]))
            .then(1969)
            .otherwise(pl.col("era"))
        )
        areas: dict[tuple[int, str], tuple[UUID, UUID]] = {}
        for (era, label), _ in contests.group_by("era", "constituency", maintain_order=True):
            code = "queens-university" if label.startswith("Queen's") else f"{era}-{slug(label)}"
            start = GENERALS[era]
            end = GENERALS[1969] if code == "queens-university" else GENERALS[1929] if era == 1921 else DISSOLVED
            area = cs.area("gb.stormont", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="gb.stormont")
            cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
            name(area, label)
            for nation, begin, finish in nations:
                if (begin is None or begin < end) and (finish is None or start < finish):
                    cs.add(
                        "area_parent",
                        area_id=area,
                        parent_id=nation,
                        valid_from=max(start, begin or start),
                        valid_to=min(end, finish or end),
                    )
            unit = cs.unit("gb.stormont", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=nir,
                    scheme_code="gb.stormont",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, label)
            areas[era, label] = (area, unit)

        parties: dict[str, UUID] = {}

        def party_of(abbreviation: str, day: date) -> UUID | None:
            if abbreviation == "L":
                return UUID(LIBERAL[0] if day < LIBERAL[2] else LIBERAL[1])
            if abbreviation in KNOWN:
                return UUID(KNOWN[abbreviation])
            if abbreviation in MINTED:
                if abbreviation not in parties:
                    parties[abbreviation] = cs.key(f"{SOURCE}.party", abbreviation)
                    if parties[abbreviation] in cs.minted:
                        cs.add("party", id=parties[abbreviation], kind="party", polity_id=gb)
                        name(parties[abbreviation], MINTED[abbreviation])
                        name(parties[abbreviation], abbreviation, "abbreviation")
                return parties[abbreviation]
            if abbreviation == "Ind" or abbreviation.startswith("Ind "):
                return None
            raise ValueError(f"unknown party label {abbreviation}")

        persons: dict[str, UUID] = {}
        elections: dict[date, UUID] = {}
        for (day,), on_day in contests.group_by("day", maintain_order=True):
            general = on_day["general"][0]
            spoken = f"{day:%B} {day.day}, {day.year}"
            election = elections[day] = cs.key(f"{SOURCE}.election", str(day))
            cs.add(
                "election",
                id=election,
                polity_id=nir,
                kind="general" if general else "by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            path = f"gb/northern-ireland/{day}-{'general' if general else 'by-election'}"
            if general:
                name(election, f"{day.year} Northern Ireland general election")
            elif on_day.height == 1:
                name(election, f"{on_day['constituency'][0]} by-election, {spoken}")
            else:
                name(election, f"Northern Ireland House of Commons by-elections, {spoken}")
            cs.add("slug", path=path, entity_id=election)
            for row in on_day.iter_rows(named=True):
                label, key = row["constituency"], row["key"]
                area, unit = areas[row["era"], label]
                tacit = row["unopposed"]
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=row["seats"],
                    round_number=1,
                    electoral_system_id=systems["stv" if row["stv"] else "fptp"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(
                    contest,
                    f"{day.year} Northern Ireland general election in {label}"
                    if general
                    else f"{day.year} {label} by-election",
                )
                cs.add("slug", path=f"{path}/{slug(label)}", entity_id=contest)
                if row["cause"]:
                    cs.add("note", entity_id=contest, kind="context", body_md=f"Vacancy: {row['cause']}.")
                if row["vacant"]:
                    cs.add("note", entity_id=contest, kind="context", body_md=f"Before this election: {row['vacant']}.")
                for body_md in notes.filter((pl.col("constituency") == label) & (pl.col("year") == str(day.year)))[
                    "body"
                ]:
                    if (
                        general
                        or contests.filter(
                            (pl.col("constituency") == label)
                            & pl.col("general")
                            & (pl.col("day").dt.year() == day.year)
                        ).is_empty()
                    ):
                        cs.add("note", entity_id=contest, kind="context", body_md=body_md)
                result_set = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=boothroyd,
                    is_default=True,
                    completeness=1,
                )
                if day.year >= 1969:
                    cs.add("citation", entity_id=result_set, source_id=cain)
                if row["redated"]:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="Boothroyd's table prints this result under 1929, but the constituency was created for "
                        "the 1969 general election, and CAIN's 1969 results give the same candidates and votes.",
                    )
                if row["quota_note"]:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=f"{row['quota_note']}.")
                counts = stages.filter(pl.col("key") == key).sort("stage")
                for measure, amount in (
                    (REGISTERED, row["electors"]),
                    (QUOTA, row["quota"]),
                    (VALID, counts["total"][0] if counts.height else None),
                ):
                    if amount is not None and not tacit:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=amount,
                        )
                for stage, spare in counts.select("stage", "nontransferable").iter_rows():
                    if spare is not None and stage > 1:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=EXHAUSTED,
                            count_round=stage,
                            flags=0,
                            value=spare,
                        )
                for line in lines.filter(pl.col("key") == key).sort("number").iter_rows(named=True):
                    label_text = line["party"]
                    oneill = ONEILL in label_text
                    later = [part for part in re.findall(r"\(([^)]*)\)", label_text) if part != ONEILL]
                    abbreviation = re.sub(r"\s*\(.*", "", label_text).strip()
                    party = party_of(abbreviation, day)
                    option_key = f"{key}:{line['number']}"
                    option = cs.key(f"{SOURCE}.option", option_key)
                    clean = bare(line["member"] or line["name"])
                    identity = line["member"] or option_key
                    if identity not in persons:
                        persons[identity] = cs.key(f"{SOURCE}.person", identity)
                        if persons[identity] in cs.minted:
                            female = re.match(r"(Mrs\.|Miss|Dame|Lady)\b", line["member"] or line["name"])
                            cs.add("person", id=persons[identity], gender="female" if female else None)
                            name(persons[identity], clean)
                    ballot = line["name"]
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=ballot,
                    )
                    context = []
                    if oneill:
                        context.append("Stood as a supporter of the Prime Minister, Terence O'Neill.")
                    if later:
                        names = [expansions.get(part.replace("’", "'"), part) for part in later]
                        context.append(
                            f"Boothroyd's table gives this member's later affiliation: {', then '.join(names)}."
                        )
                    if (key, line["name"]) in CAIN_DIFFERS:
                        cs.add(
                            "note",
                            entity_id=result_set,
                            kind="caveat",
                            body_md=f"CAIN's draft results give {line['name']} {CAIN_DIFFERS[(key, line['name'])]:,} "
                            f"votes. Boothroyd's {line['votes'][0]:,.0f} agrees with his printed majority, percentages, "
                            "and turnout, so it is the figure stored here.",
                        )
                    for text in context:
                        cs.add("note", entity_id=option, kind="context", body_md=text)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=persons[identity],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        member_of_party_id=party,
                        is_independent=party is None,
                        is_incumbent=True if line["sitting"] else None,
                    )
                    series = line["votes"] if not tacit else []
                    for stage, amount in enumerate(series, 1 if row["stv"] else 0):
                        if amount is None:
                            break
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=VOTES,
                            count_round=stage,
                            flags=0,
                            value=amount,
                        )
                    present = [amount for amount in series if amount is not None]
                    dropped = len(present) < len(series) or (len(series) > 1 and present and present[-1] == 0)
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="elected" if line["elected"] else "eliminated" if dropped else "lost",
                    )
        return cs.write()
