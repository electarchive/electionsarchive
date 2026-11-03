import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import batch, raw
from ea.store import DATA

SOURCE = "i18n"
MODEL = "claude-sonnet-5-5"
WEB = Path(__file__).resolve().parents[2] / "apps" / "web"
MESSAGES = WEB / "messages"
GLOSSARY = Path(__file__).resolve().parent / "glossary"
STYLE = GLOSSARY / "style"
NOTES = GLOSSARY / "notes.json"
WORK = DATA / "i18n"
REDO = WORK / "redo"
LOCK = MESSAGES / ".source.json"
SETTINGS = WEB / "project.inlang" / "settings.json"
EFFORT = "medium"
STAGE_EFFORT = {"translate": "high", "review": "high"}
AUTO = 3.0
ATTEMPTS = 3
POLL = 30
CHUNK_CHARS = 20_000
GROUP_CHARS = 30_000
GROUP_ROWS = 40
SITES = 2
NEIGHBORS = 6
REACH = 40
SNIPPET = 150
EXAMPLES = 30
CONTEXT_VERSION = "2"

LANGUAGES = {
    "bg": ("Bulgarian", "Bulgaria"),
    "cs": ("Czech", "Czechia"),
    "cy": ("Welsh", "Wales (Senedd Cymru, UK Parliament, local councils)"),
    "da": ("Danish", "Denmark"),
    "de": ("German", "Germany, with Swiss and Austrian readers"),
    "el": ("Greek", "Greece and Cyprus"),
    "es": ("Spanish (Castilian, as used in Spain)", "Spain"),
    "et": ("Estonian", "Estonia"),
    "fi": ("Finnish", "Finland"),
    "fr": ("French", "France, with Swiss, Belgian, and Canadian readers"),
    "ga": ("Irish (Gaeilge, An Caighdeán Oifigiúil)", "Ireland (Dáil, Seanad, local elections)"),
    "gd": ("Scottish Gaelic", "Scotland (Scottish Parliament, UK Parliament, councils)"),
    "hr": ("Croatian", "Croatia"),
    "hu": ("Hungarian", "Hungary"),
    "it": ("Italian", "Italy, with Swiss readers"),
    "kmr": ("Northern Kurdish (Kurmanji), Latin (Hawar) script", "Kurdish speakers in Turkey"),
    "lt": ("Lithuanian", "Lithuania"),
    "lv": ("Latvian", "Latvia"),
    "mt": ("Maltese", "Malta"),
    "nl": ("Dutch", "the Netherlands and Flanders"),
    "pl": ("Polish", "Poland"),
    "pt-BR": ("Brazilian Portuguese", "Brazil"),
    "pt-PT": ("European Portuguese (pre-reform-neutral, Portugal usage)", "Portugal"),
    "ro": ("Romanian", "Romania"),
    "rm": ("Romansh (Rumantsch Grischun)", "Switzerland (Grisons/Graubünden)"),
    "sk": ("Slovak", "Slovakia"),
    "sl": ("Slovenian", "Slovenia"),
    "sv": ("Swedish", "Sweden, with Finland-Swedish readers"),
    "tr": ("Turkish", "Turkey"),
}

TERMS = {
    "election": "a vote to choose officeholders or decide a question",
    "general election": "the main scheduled election for an office or legislature",
    "primary": "a party's nominating election (US)",
    "runoff": "a second round between the top finishers when no one reached the threshold",
    "first round": "the first ballot of a two-round system",
    "by-election": "an election to fill a single vacancy between general elections",
    "special election": "US term for an election held outside the regular cycle, often to fill a vacancy",
    "recall election": "a vote on removing an official before the term ends",
    "contest": "one race for one office or seat, or one question, within an election",
    "race": "informal synonym of contest",
    "ballot measure": "a question put to voters: referendum, initiative, proposition, or amendment",
    "referendum": "a popular vote on a law or constitutional change",
    "initiative": "a measure placed on the ballot by citizen petition",
    "proposition": "US name for a numbered ballot measure",
    "retention election": "a yes/no vote on whether a sitting judge keeps the office",
    "candidate": "a person standing for election",
    "incumbent": "the current holder of the office",
    "nominee": "the candidate a party selected",
    "write-in": "a vote for a name written onto the ballot",
    "unopposed": "elected with no opponent",
    "ballot": "the paper or screen on which a vote is cast",
    "votes cast": "the number of ballots or votes counted",
    "valid votes": "votes that count toward a result",
    "invalid votes": "spoiled or rejected ballots",
    "blank votes": "ballots cast with no choice marked",
    "turnout": "the share of registered voters who voted",
    "registered voters": "people on the electoral roll",
    "electorate": "everyone entitled to vote in an area",
    "vote share": "a candidate's or party's percentage of the vote",
    "margin": "the gap between the winner and the runner-up",
    "swing": "the change in vote share between two elections",
    "majority": "more than half of the votes or seats",
    "plurality": "the most votes, without a majority",
    "landslide": "a very large winning margin",
    "seat": "a place in a legislature or council",
    "constituency": "an area that elects one or more members (UK, EU usage)",
    "electoral district": "an area that elects one or more members (general term)",
    "district": "US and general term for an electoral area, such as a congressional district",
    "ward": "a city or council electoral subdivision",
    "precinct": "the smallest US reporting unit for votes",
    "polling station": "a place where people vote",
    "reporting unit": "any area for which results are published",
    "county": "a US county, or a UK/Irish county",
    "municipality": "a city, town, or commune as a local government",
    "state": "a US or Brazilian state, or a German Land",
    "province": "a province or Turkish il",
    "region": "a first- or second-level region",
    "polity": "any country, state, or territory that holds its own elections",
    "jurisdiction": "the area a government or court covers",
    "boundaries": "the lines that define electoral or administrative areas",
    "redistricting": "redrawing electoral district boundaries",
    "chamber": "one house of a legislature",
    "legislature": "a law-making assembly",
    "parliament": "a national legislature",
    "lower house": "the larger or popularly elected chamber",
    "upper house": "the second chamber, such as a senate",
    "senate": "an upper chamber",
    "house of representatives": "a lower chamber, such as the US House",
    "council": "a local or regional elected body",
    "mayor": "the head of a city or municipal government",
    "governor": "the elected head of a US state",
    "officeholder": "a person holding an office",
    "term": "the period an officeholder serves",
    "term limit": "a cap on how many terms one can serve",
    "party": "a political party",
    "independent": "a candidate with no party",
    "coalition": "parties governing or campaigning together",
    "alliance": "parties running together on a joint list or ticket",
    "party list": "a party's ranked list of candidates in a list system",
    "closed list": "a list where voters cannot change the order",
    "open list": "a list where voters' preference votes reorder candidates",
    "preference vote": "a vote for an individual candidate within a list",
    "list seat": "a seat filled from a party list, not a constituency",
    "constituency seat": "a seat won in a single-member district",
    "overhang seat": "an extra seat when a party wins more constituencies than its share entitles it to (Germany)",
    "leveling seat": "an adjustment seat added to make the result proportional",
    "threshold": "the minimum vote share needed to win seats",
    "quota": "the number of votes that guarantees a seat (STV, largest remainder)",
    "proportional representation": "a system that gives seats in proportion to votes",
    "first past the post": "single-member plurality voting",
    "two-round system": "a system with a runoff if no one wins a majority in the first round",
    "ranked-choice voting": "US instant-runoff voting with ranked ballots",
    "single transferable vote": "STV, the multi-member ranked system used in Ireland and Malta",
    "mixed-member proportional": "MMP, as in Germany",
    "D'Hondt method": "a highest-averages seat allocation method",
    "Sainte-Laguë method": "a highest-averages method with odd divisors",
    "seat allocation": "how seats are distributed among parties",
    "transfer": "votes moved from an eliminated or elected candidate under STV",
    "elimination": "removing the lowest candidate in a count round",
    "count": "a round of counting under STV or instant runoff",
    "results": "the declared vote totals and winners",
    "official results": "results certified by the electoral authority",
    "certified": "formally confirmed by the authority",
    "preliminary results": "early, uncertified results",
    "election night": "the evening when results come in",
    "called": "a race the projection desk has declared won",
    "projection": "a forecast of the winner before the count ends",
    "too close to call": "a race whose winner cannot yet be projected",
    "reporting": "share of units or votes counted so far",
    "votes counted": "votes tallied so far",
    "absentee ballot": "a vote cast by post or in advance away from the polling place",
    "mail-in ballot": "a postal vote",
    "early voting": "voting in person before election day",
    "flip": "a seat that changes party",
    "gain": "a seat a party won from another party",
    "hold": "a seat a party kept",
    "gerrymander": "district lines drawn to favor a party",
    "electoral college": "the US body that elects the president",
    "elector": "a member of the electoral college",
    "delegate": "a person sent to a party convention",
    "convention": "a party's nominating gathering",
    "roll call": "a state-by-state vote tally at a convention",
    "turnover": "change in who holds seats",
    "source": "the publication a figure comes from",
    "changeset": "a reviewed batch of edits to the archive's data",
    "provenance": "where a figure came from and how it was processed",
    "archive": "this election archive",
    "coverage": "how much of the elections the archive holds",
    "map": "a geographic map",
    "shapes": "boundary geometries for areas",
    "watchlist": "elections or pages a user follows",
    "proposal": "a suggested edit someone submits for review",
    "bounty": "a requested data task others can take on",
    "workbench": "the site's tool for building custom tables and charts",
}

PLACEHOLDERS = {
    "count": "a number, also used to pick the plural form",
    "countText": "a number already formatted for the locale, such as 1.234",
    "name": "a proper name (person, place, party, page, or record), never translated",
    "names": "a list of proper names already joined, such as 'A, B, and C'",
    "party": "a party name as published, such as 'Republican Party' or 'CHP'",
    "parties": "a list of party names already joined",
    "year": "a four-digit year",
    "years": "a range or list of years",
    "date": "a date already formatted for the locale",
    "time": "a time of day already formatted for the locale",
    "total": "a number",
    "place": "a place name, such as 'Ohio' or 'İstanbul'",
    "places": "a list of place names or a number of places",
    "state": "a US state or other first-level region name",
    "share": "a percentage already formatted with its sign, such as 54.2%",
    "percent": "a percentage already formatted with its sign",
    "seats": "a number of seats",
    "noun": "an area word already in the target language and lowercase, such as county or district",
    "nouns": "the plural of an area word, already in the target language and lowercase",
    "from": "the earlier of two elections or years",
    "to": "the later of two elections or years",
    "title": "a page, election, or contest title",
    "first": "a party or candidate name",
    "second": "a party or candidate name",
    "runner": "the runner-up's party or candidate name",
    "margin": "a margin already formatted (points or votes)",
    "points": "a number of percentage points",
    "votes": "a number of votes, already formatted",
    "majority": "a number of votes, already formatted",
    "publisher": "the name of a publisher or source",
    "email": "an email address",
    "newEmail": "an email address",
    "race": "a contest name, such as 'Ohio Senate'",
    "election": "an election name, such as '2024 general election'",
    "contest": "a contest name",
    "reason": "a short reason phrase from another message",
    "label": "a short label from another message",
    "round": "a round number or round name",
    "number": "a number",
    "rank": "a position number",
    "query": "the text the reader typed into a search box",
    "license": "a license name, such as CC BY-SA 4.0",
    "chamber": "a chamber name, such as 'House of Representatives'",
    "polity": "a country or state name",
    "language": "a language name",
    "seat": "a seat or district name",
    "held": "a party name",
    "won": "a number",
    "page": "a page number",
    "n": "a number",
}

TAGS = {
    "button": "button",
    "Button": "button",
    "a": "link",
    "h1": "page heading",
    "h2": "section heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "th": "column header",
    "label": "form label",
    "Label": "form label",
    "option": "menu option",
    "summary": "disclosure toggle",
    "legend": "group label",
    "caption": "table caption",
    "figcaption": "caption",
    "title": "browser tab title",
    "dt": "term label",
    "dd": "value",
    "td": "table cell",
    "li": "list item",
    "p": "sentence or paragraph",
}

SUFFIXES = {
    ".Title": "dialog or panel title",
    ".Description": "dialog or panel text",
    ".Item": "menu item",
    ".Option": "menu option",
    ".Trigger": "button",
    ".Content": "popover or tooltip text",
    ".Label": "group label",
}

ATTRIBUTES = {
    "aria-label": "accessible label read by screen readers",
    "placeholder": "input placeholder",
    "title": "tooltip",
    "alt": "image alt text",
    "label": "label",
    "description": "description line",
    "heading": "heading",
    "summary": "summary line",
    "hint": "hint line",
}

SHORT = ("button", "column header", "form label", "menu", "link", "heading", "title", "label", "badge", "toggle")

FIRST_PERSON = {
    "tr": r"\b(biz|bizim|bize|bizi|bizden)\b|\w+(ıyoruz|iyoruz|uyoruz|üyoruz)\b",
    "kmr": r"\b(em|me)\b",
    "de": r"\b(wir|uns|unser\w*)\b",
    "fr": r"\b(nous|notre|nos)\b",
    "pt-BR": r"\b(nós|nosso|nossa|nossos|nossas)\b|\ba gente\b",
}

BUTTON_POLITE = {"tr": r"\w{3,}(y?[ıiuü]n|y?[ıiuü]n[ıi]z)$"}
FILLER = {"tr": r"\blütfen\b", "de": r"\bbitte\b", "fr": r"\bveuillez\b", "pt-BR": r"\bpor favor\b"}
SUFFIXED = {"tr": r"\}['’]?[a-zçğıöşü]"}


class Variant(TypedDict):
    declarations: list[str]
    selectors: list[str]
    match: dict[str, str]


type Message = str | list[Variant]
type Text = str | dict[str, str]


class Site(TypedDict):
    where: list[str]
    code: list[str]
    near: list[str]
    file: str
    line: int


class Item(TypedDict):
    key: str
    english: Text
    placeholders: NotRequired[dict[str, str]]
    forms: NotRequired[list[str]]
    where: NotRequired[list[str]]
    code: NotRequired[list[str]]
    near: NotRequired[dict[str, dict[str, object]]]
    note: NotRequired[str]
    terms: NotRequired[dict[str, str]]
    limit: NotRequired[int]
    translation: NotRequired[Text]
    flags: NotRequired[list[str]]
    rejected: NotRequired[str]


class Entry(TypedDict):
    en: str
    text: Text
    reviewed: bool
    machine: NotRequired[Text]
    pinned: NotRequired[bool]
    flags: NotRequired[list[str]]


def english() -> dict[str, Message]:
    messages: dict[str, Message] = json.loads((MESSAGES / "en.json").read_text())
    messages.pop("$schema", None)
    return messages


def body(value: Message) -> Text:
    return value if isinstance(value, str) else value[0]["match"]


def digest(value: object) -> str:
    return hashlib.md5(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def categories(targets: list[str]) -> dict[str, dict[str, list[str]]]:
    script = (
        "const out={};for(const l of process.argv.slice(1)){out[l]={plural:new Intl.PluralRules(l)"
        ".resolvedOptions().pluralCategories,ordinal:new Intl.PluralRules(l,{type:'ordinal'})"
        ".resolvedOptions().pluralCategories}}console.log(JSON.stringify(out))"
    )
    found: dict[str, dict[str, list[str]]] = json.loads(
        subprocess.run(["node", "-e", script, *targets], capture_output=True, text=True, check=True).stdout
    )
    return found


def sources_files() -> list[Path]:
    return sorted(
        path
        for path in (WEB / "src").rglob("*")
        if path.suffix in {".svelte", ".ts", ".js"} and "paraglide" not in path.parts and path.is_file()
    )


def place(path: Path) -> str:
    rel = path.relative_to(WEB / "src").as_posix()
    if rel.startswith("routes/"):
        parts = [part for part in rel.split("/")[1:-1] if not part.startswith("(")]
        route = "/" + "/".join(parts)
        if "[...path]" in parts:
            route += " (archive pages: polity, election, contest, place, person)"
        if path.name.startswith("+page"):
            return f"page {route}"
        if path.name.startswith("+layout"):
            return f"layout around {route}"
        if path.name.startswith("+server"):
            return f"endpoint {route}"
        return f"{path.stem} part of {route}"
    if "twin" in path.stem:
        return f"{rel} (Markdown twin of a page, read by agents and people)"
    if "mail" in path.stem:
        return f"{rel} (email)"
    return f"component {rel}" if path.suffix == ".svelte" else f"module {rel}"


def role(lines: list[str], number: int, at: int, path: Path) -> str:
    head = lines[number][:at]
    found = re.search(r"([\w-]+)\s*[=:]\s*\{?\s*(?:[\w.?()]+\s*(?:\?\?|\|\|)\s*)?$", head)
    if found is not None and found[1] in ATTRIBUTES:
        return ATTRIBUTES[found[1]]
    if path.suffix != ".svelte":
        return ""
    stack: list[str] = []
    window = "\n".join(lines[max(0, number - 12) : number]) + "\n" + head
    for closing, tag in re.findall(r"<(/?)([A-Za-z][\w:.-]*)", window):
        if closing and tag in stack:
            del stack[len(stack) - 1 - stack[::-1].index(tag) :]
        elif not closing:
            stack.append(tag)
    for tag in reversed(stack):
        if tag in TAGS:
            return TAGS[tag]
        for suffix, label in SUFFIXES.items():
            if tag.endswith(suffix):
                return f"{label} ({tag})"
        if tag.startswith("Tooltip"):
            return "tooltip"
    return ""


def scan() -> dict[str, Site]:
    paths = sources_files()
    signature = digest(
        [CONTEXT_VERSION, *([path.as_posix(), path.stat().st_size, path.stat().st_mtime_ns] for path in paths)]
    )
    cache = WORK / "context.json"
    if cache.exists():
        stored = json.loads(cache.read_text())
        if stored.get("signature") == signature:
            return cast(dict[str, Site], stored["context"])
    keys = set(english())
    texts: dict[Path, list[str]] = {}
    direct: dict[str, list[tuple[Path, int, int]]] = {}
    quoted: dict[str, list[tuple[Path, int, int]]] = {}
    for path in paths:
        lines = path.read_text().splitlines()
        texts[path] = lines
        for number, line in enumerate(lines):
            for match in re.finditer(r"\bm\.([A-Za-z][A-Za-z0-9_]*)\b", line):
                if match[1] in keys:
                    direct.setdefault(match[1], []).append((path, number, match.start()))
            for match in re.finditer(r"[\"'`]([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)[\"'`]", line):
                if match[1] in keys:
                    quoted.setdefault(match[1], []).append((path, number, match.start()))
    hits = quoted | direct
    by_file: dict[Path, list[tuple[int, str]]] = {}
    for key, found in hits.items():
        for path, number, _ in found:
            by_file.setdefault(path, []).append((number, key))
    context: dict[str, Site] = {}
    for key, found in hits.items():
        found.sort(key=lambda hit: (hit[0].suffix != ".svelte", "twin" in hit[0].stem, hit[0].as_posix(), hit[1]))
        where: list[str] = []
        code: list[str] = []
        for path, number, at in found[:SITES]:
            lines = texts[path]
            kind = role(lines, number, at, path)
            where.append(place(path) + (f", {kind}" if kind else ""))
            before = " ".join(
                " ".join(line.split()) for line in [*lines[max(0, number - 2) : number], lines[number][:at]]
            )
            after = " ".join(" ".join(line.split()) for line in [lines[number][at:], *lines[number + 1 : number + 2]])
            code.append(f"{before[-SNIPPET:]} {after[:SNIPPET]}".strip())
        path, number, _ = found[0]
        near: list[str] = []
        for _, other in sorted((abs(line - number), other) for line, other in by_file[path] if other != key):
            if other not in near and len(near) < NEIGHBORS:
                near.append(other)
        context[key] = {
            "where": where,
            "code": code,
            "near": [
                other for other in near if any(abs(line - number) <= REACH for line, o in by_file[path] if o == other)
            ],
            "file": path.relative_to(WEB).as_posix(),
            "line": number,
        }
    WORK.mkdir(parents=True, exist_ok=True)
    partial = cache.with_suffix(".part")
    partial.write_text(json.dumps({"signature": signature, "context": context}, ensure_ascii=False))
    partial.replace(cache)
    return context


def holes(text: str) -> list[str]:
    return sorted(re.findall(r"\{([A-Za-z0-9_]+)\}", text))


def flat(text: object) -> str:
    return text if isinstance(text, str) else " ".join(str(value) for value in cast(dict[str, str], text).values())


def forms(variant: Variant, kinds: dict[str, list[str]]) -> list[str]:
    combos: list[list[str]] = [[]]
    for name in variant["selectors"]:
        ordinal = any(line.startswith(f"local {name} =") and "ordinal" in line for line in variant["declarations"])
        options = [
            f"{name}={category}" for category in kinds["ordinal" if ordinal else "plural"] if category != "other"
        ]
        combos = [[*combo, option] for combo in combos for option in [*options, f"{name}=*"]]
    return [", ".join(combo) for combo in combos]


def matched(text: str, terms: dict[str, dict[str, object]]) -> dict[str, str]:
    lowered = text.lower()
    hits: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        if re.search(rf"\b{re.escape(term.lower())}(s|es)?\b", lowered) and not any(
            term.lower() in hit.lower() for hit in hits
        ):
            hits.append(term)
    out: dict[str, str] = {}
    for term in hits[:8]:
        entry = terms[term]
        line = str(entry.get("term", ""))
        if entry.get("note"):
            line += f" ({entry['note']})"
        if entry.get("avoid"):
            line += f"; never {', '.join(cast(list[str], entry['avoid']))}"
        out[term] = line
    return out


def item(
    key: str,
    value: Message,
    kinds: dict[str, list[str]],
    sites: dict[str, Site],
    notes: dict[str, str],
    terms: dict[str, dict[str, object]],
) -> Item:
    texts = [value] if isinstance(value, str) else list(value[0]["match"].values())
    row: Item = {"key": key, "english": body(value)}
    names = sorted({hole for text in texts for hole in holes(text)})
    if names:
        row["placeholders"] = {name: PLACEHOLDERS.get(name, "") for name in names}
    if not isinstance(value, str):
        row["forms"] = forms(value[0], kinds)
    site = sites.get(key)
    if site is not None:
        row["where"] = site["where"]
        row["code"] = site["code"]
    if key in notes:
        row["note"] = notes[key]
    found = matched(" ".join(texts), terms)
    if found:
        row["terms"] = found
    size = max(len(text) for text in texts)
    kind = " ".join(site["where"]) if site is not None else ""
    if size <= 30 and (any(word in kind for word in SHORT) or not re.search(r"[.!?:]\s*$", texts[0])):
        row["limit"] = max(int(size * 1.6), size + 10)
    return row


def order(rows: list[Item], sites: dict[str, Site], source: dict[str, Message]) -> list[Item]:
    index = {key: number for number, key in enumerate(source)}

    def place_of(row: Item) -> tuple[str, int, int]:
        site = sites.get(row["key"])
        return (site["file"], site["line"], 0) if site is not None else ("~", 0, index.get(row["key"], 0))

    return sorted(rows, key=place_of)


def chunks(rows: list[Item]) -> list[list[Item]]:
    groups: list[list[Item]] = [[]]
    size = 0
    for row in rows:
        weight = len(json.dumps(row, ensure_ascii=False))
        if groups[-1] and (size + weight > GROUP_CHARS or len(groups[-1]) >= GROUP_ROWS):
            groups.append([])
            size = 0
        groups[-1].append(row)
        size += weight
    return [group for group in groups if group]


def estimate(prompt: str, output_chars: float) -> float:
    price_in, price_out = batch.PRICES[MODEL]
    return (len(prompt) / 3.0 * price_in + output_chars / 2.2 * price_out) / 1e6


def reply(key: str) -> dict[str, object] | None:
    message = json.loads(raw.cached(SOURCE, key).path.read_text())
    text = "".join(block["text"] for block in message["content"] if block["type"] == "text").strip()
    decoder = json.JSONDecoder()
    merged: dict[str, object] | None = None
    start = text.find("{")
    while start != -1:
        try:
            parsed, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(parsed, dict):
            merged = (merged or {}) | cast(dict[str, object], parsed)
        start = text.find("{", end)
    return merged


def glossary(locale: str) -> dict[str, dict[str, object]]:
    file = GLOSSARY / f"{locale}.json"
    return json.loads(file.read_text()) if file.exists() else {}


def style(locale: str) -> str:
    file = STYLE / f"{locale}.txt"
    return file.read_text().strip() if file.exists() else ""


def notes() -> dict[str, str]:
    return json.loads(NOTES.read_text()) if NOTES.exists() else {}


def glossary_wants(targets: list[str]) -> dict[str, batch.Want]:
    wanted: dict[str, batch.Want] = {}
    for locale in targets:
        language, place = LANGUAGES[locale]
        prompt = (
            f"You are a professional election-terminology translator for {language}, writing for readers in {place}.\n"
            "Build a glossary for electionsarchive.com, a world election archive (results, maps, parties, people, "
            "offices, ballot measures, election-night coverage). For each English term below, give the standard term "
            f"that {language}-language official bodies, electoral law, and quality press use. Where a term names a "
            "foreign institution with no local equivalent (US precinct, US county, retention election, electoral "
            "college), give the established rendering used in that language's press, not a calque. Use the noun "
            "form, singular, in the base (nominative) case.\n\n"
            "Reply with only a JSON object mapping each English term exactly as given to an object "
            '{"term": "<target term>", "note": "<short English note on usage, inflection, or alternatives; '
            'empty if none>"}.\n\n' + "\n".join(f"- {term}: {meaning}" for term, meaning in TERMS.items())
        )
        params: dict[str, object] = {
            "model": MODEL,
            "max_tokens": 32_000,
            "output_config": {"effort": EFFORT},
            "messages": [{"role": "user", "content": prompt}],
        }
        wanted[f"glossary/{locale}/{digest(params)}"] = (
            cast(MessageCreateParamsNonStreaming, params),
            estimate(prompt, 12_000),
        )
    return wanted


PRINCIPLES = """Writing principles, from the site's own editor:
- Plain and direct. Concrete words over abstract ones; common phrases over coined ones; nothing is sold.
- Keep each subject close to its verb. No stacks of nouns, no chains of participles or modifiers.
- Give actions to people and programs, not to pages, documents, data, or ideas.
- The English is impersonal and never says "we", "our", or "us". Never add a first-person voice for the site.
- No dashes as sentence punctuation, no exclamation marks, no filler such as "please", "simply", or "just".
- Terse UI: a button names its action with the specific object; an error says what failed.
- Consistency: the same English term gets the same translation everywhere, and the strings of one screen read as one voice."""

RULES = """You translate the interface of electionsarchive.com, a world election archive (results down to the precinct, maps, parties, people, offices, ballot measures, live election-night coverage, and an API and tools for agents), from American English into {language}, for readers in {place}.

The goal: every string reads as if a careful native {language} writer wrote it for that exact spot on the screen. A reader must never be able to see the English word order underneath.

For each string:
1. Work out what it is and where it sits from "where" (the page or component and the element: button, heading, column header, label, tooltip, sentence, email line), "code" (the lines that render it), "note", "near" (strings next to it on the same screen, with their current translations), and "placeholders" (what fills each one).
2. Decide what the string must say there. Then write it the way {language} says that, in the form the style guide sets for that kind of string: a button is a command, a header is a noun phrase, a sentence is a sentence.
3. Check it: meaning complete with nothing added, glossary terms right ("terms" lists the ones this string touches), placeholders intact and grammatical, length within "limit" when one is given.

{principles}

Hard rules:
- Keep every placeholder such as {{name}} or {{countText}} exactly as written, braces included; never translate, rename, drop, or add one. Move it wherever the grammar needs it.
- Keep markup, HTML tags, Markdown syntax, URLs, email addresses, code spans, keyboard keys, and escaped characters exactly as written.
- Never translate proper nouns: people, places, parties, the product name Elections Archive or electionsarchive, brand names, file formats, and code terms (JSON, CSV, API, MCP, URL, SQL, Markdown).
- A string with "forms" is a plural or ordinal message: return an object with exactly those keys. Each key names CLDR categories for {language} ("*" means other). Write each form correctly for numbers in that category; repeat a form if two categories share it.
- Keys starting with language_banner are shown to a visitor whose browser prefers {language}, to offer the site in {language}: where the English says "English", name {language} instead.
- An item with "rejected" was translated before and failed a check; the note says why and shows the failed attempt. Fix exactly that.

Reply with only a JSON object mapping each key to its translation (a string, or an object of forms). Include every key you were given and nothing else.

{style}

Glossary (English: {language}, with notes; "never" lists forms that are wrong):
{glossary}
{examples}"""

REVIEW = """You are the senior {language} editor for the interface of electionsarchive.com, a world election archive translated from American English for readers in {place}. You are a native {language} writer with years of newsroom and product copy-editing. Each item has the English, the draft, where it appears, what fills its placeholders, and nearby strings.

Judge each draft against the English and its screen, in this order of importance:
1. Accuracy: mistranslation, omission, addition, or a meaning that changes in this context.
2. Terminology: the glossary ("terms" lists the ones this item touches), and the same English term translated the same way across the items and "near" strings.
3. Register and form: the forms the style guide sets for buttons, labels, headings, sentences, errors, and progress states; the same address form everywhere; never a first-person "we" for the site.
4. Fluency: grammar, agreement, case, word order, calques, bureaucratic or stacked phrasing. Ask whether a careful native editor would have written exactly this for this spot. A draft that is correct but reads as translated needs a rewrite.
5. Mechanics: placeholders renamed, dropped, suffixed, or in an ungrammatical spot; plural forms wrong for their CLDR category ("*" means other); markup or proper nouns changed; length over "limit".
"flags" lists problems an automatic check already found in the draft; fix them.

{principles}

Only report real problems; leave a draft that is already natural and correct untouched. Reply with only a JSON object mapping each key that needs a change to {{"fix": <the corrected translation, same shape as the draft>, "why": "<category>: <short English reason>"}}. Reply {{}} when every draft is right.

{style}

Glossary (English: {language}, with notes; "never" lists forms that are wrong):
{glossary}
{examples}"""


def examples(entries: dict[str, Entry], source: dict[str, Message]) -> str:
    chosen = [
        (key, entry)
        for key, entry in entries.items()
        if entry.get("pinned") and key in source and entry["en"] == fingerprint(source[key])
    ][:EXAMPLES]
    if not chosen:
        return ""
    lines = [
        f"- {json.dumps(body(source[key]), ensure_ascii=False)} → {json.dumps(entry['text'], ensure_ascii=False)}"
        for key, entry in chosen
    ]
    return "\nThe site's owner, a native speaker, wrote these by hand. Match their voice:\n" + "\n".join(lines)


def system(template: str, locale: str, extra: str = "") -> list[dict[str, object]]:
    language, place = LANGUAGES[locale]
    terms: list[str] = []
    for term, entry in glossary(locale).items():
        line = f"- {term}: {entry['term']}"
        if entry.get("note"):
            line += f" ({entry['note']})"
        if entry.get("avoid"):
            line += f"; never {', '.join(cast(list[str], entry['avoid']))}"
        terms.append(line)
    return [
        {
            "type": "text",
            "text": template.format(
                language=language,
                place=place,
                principles=PRINCIPLES,
                style=style(locale),
                glossary="\n".join(terms),
                examples=extra,
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def wants(
    stage: str,
    locale: str,
    rows: list[Item],
    known: dict[str, Text],
    source: dict[str, Message],
    sites: dict[str, Site],
    extra: str,
) -> dict[str, tuple[batch.Want, list[Item]]]:
    head = system(RULES if stage == "translate" else REVIEW, locale, extra)
    out: dict[str, tuple[batch.Want, list[Item]]] = {}
    for group in chunks(order(rows, sites, source)):
        inside = {row["key"] for row in group}
        sent: list[Item] = []
        for row in group:
            row = cast(Item, dict(row))
            site = sites.get(row["key"])
            near = {
                other: {"en": body(source[other])} | ({"now": known[other]} if other in known else {})
                for other in (site["near"] if site is not None else [])
                if other in source and other not in inside
            }
            if near:
                row["near"] = cast(dict[str, dict[str, object]], near)
            if stage == "review":
                row.pop("code", None)
            sent.append(row)
        text = json.dumps(sent, ensure_ascii=False, indent=1)
        verb = "Translate" if stage == "translate" else "Review"
        prompt = f"{verb} these {len(sent)} strings:\n\n{text}"
        size = sum(len(json.dumps(row["english"], ensure_ascii=False)) + len(row["key"]) for row in sent)
        output = size * 3.0 if stage == "translate" else size * 1.5
        params: dict[str, object] = {
            "model": MODEL,
            "max_tokens": 32_000,
            "output_config": {"effort": STAGE_EFFORT[stage]},
            "system": head,
            "messages": [{"role": "user", "content": prompt}],
        }
        out[f"{stage}/{locale}/{digest([head, sent])}"] = (
            (cast(MessageCreateParamsNonStreaming, params), estimate(str(head) + prompt, output)),
            group,
        )
    return out


def problem(source: Text, text: object, required: list[str] | None) -> str | None:
    if isinstance(source, str):
        if not isinstance(text, str) or not text.strip():
            return "not a nonempty string"
        pairs = [(source, text)]
    else:
        if not isinstance(text, dict) or any(
            not isinstance(value, str) or not value.strip() for value in text.values()
        ):
            return "not an object of nonempty forms"
        forms_given = cast(dict[str, str], text)
        if sorted(forms_given) != sorted(required or []):
            return f"forms must be exactly {required}"
        other = next(value for key, value in source.items() if all(part.endswith("=*") for part in key.split(", ")))
        last = next(value for key, value in forms_given.items() if all(part.endswith("=*") for part in key.split(", ")))
        if set(holes(last)) != set(holes(other)):
            return f"the * form must use exactly the placeholders {sorted(set(holes(other)))}"
        known = {hole for value in source.values() for hole in holes(value)}
        if any(set(holes(value)) - known for value in forms_given.values()):
            return f"only these placeholders exist: {sorted(known)}"
        pairs = [(other, value) for value in forms_given.values()]
    for before, after in pairs:
        if isinstance(source, str) and sorted(holes(after)) != sorted(holes(before)):
            return f"placeholders must be exactly {sorted(holes(before))}"
        for pattern, label in (
            (r"<[^>]+>", "markup"),
            (r"https?://[\w-][^\s)]*[\w/]", "URLs"),
            (r"`[^`]+`", "code spans"),
        ):
            if sorted(re.findall(pattern, before)) != sorted(re.findall(pattern, after)):
                return f"{label} must match the English: {re.findall(pattern, before)}"
        if after == before and len(re.findall(r"\b[a-z]{2,}\b", before)) >= 4:
            return "left in English"
    return None


def sloppy(locale: str, row: Item, text: object, terms: dict[str, dict[str, object]]) -> list[str]:
    issues: list[str] = []
    english_text = flat(row["english"])
    target = flat(text)
    lowered = target.lower()
    for term, entry in terms.items():
        allowed = [str(phrase) for phrase in cast(list[str], entry.get("allow", []))]
        scrubbed = lowered
        for phrase in allowed:
            scrubbed = scrubbed.replace(phrase.lower(), "")
        for wrong in cast(list[str], entry.get("avoid", [])):
            if re.search(rf"\b{re.escape(wrong.lower())}", scrubbed):
                issues.append(f"'{wrong}' is wrong for {term}; use '{entry['term']}'")
    pattern = FIRST_PERSON.get(locale)
    if pattern and re.search(pattern, lowered) and not re.search(r"\b(we|our|us|ours)\b", english_text.lower()):
        issues.append("speaks for the site in the first person; the English is impersonal")
    if re.search(r"\s[–—]\s", target) and not re.search(r"\s[–—]\s", english_text):
        issues.append("uses a dash as sentence punctuation; use a colon, a comma, or two sentences")
    suffixed = SUFFIXED.get(locale)
    if suffixed and re.search(suffixed, target):
        issues.append("attaches a suffix to a placeholder; rebuild the phrase so the placeholder stands bare")
    filler = FILLER.get(locale)
    if filler and re.search(filler, lowered) and not re.search(r"\bplease\b", english_text.lower()):
        issues.append("adds a 'please' the English does not have")
    where = " ".join(row.get("where", []))
    polite = BUTTON_POLITE.get(locale)
    if (
        polite
        and isinstance(text, str)
        and "button" in where
        and re.search(polite, text.strip().split()[-1] if text.strip() else "")
    ):
        issues.append("a button takes the bare verb stem, not the polite -in form")
    limit = row.get("limit")
    if limit is not None and isinstance(text, str) and len(text) > limit:
        issues.append(f"too long for its spot: {len(text)} characters, keep it under {limit}")
    if isinstance(text, str) and isinstance(row["english"], str):
        source = row["english"]
        if (
            source[: len(source) - len(source.lstrip())] != text[: len(text) - len(text.lstrip())]
            or source[len(source.rstrip()) :] != text[len(text.rstrip()) :]
        ):
            issues.append("leading or trailing whitespace differs from the English")
        if "  " in text and "  " not in source:
            issues.append("has a double space")
        ends = re.compile(r"[.!?…:;。]\s*[»”\"')]*\s*$")
        if bool(ends.search(source)) != bool(ends.search(text)) and len(source) < 80:
            issues.append("sentence-final punctuation differs from the English")
    return issues


def fingerprint(value: Message) -> str:
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return hashlib.md5(text.encode()).hexdigest()


def lock(file: Path = LOCK) -> dict[str, dict[str, str]]:
    return json.loads(file.read_text()) if file.exists() else {}


def state(work: Path, locale: str) -> dict[str, Entry]:
    file = work / f"{locale}.json"
    return json.loads(file.read_text()) if file.exists() else {}


def shipped(locale: str) -> dict[str, Message]:
    file = MESSAGES / f"{locale}.json"
    found: dict[str, Message] = json.loads(file.read_text()) if file.exists() else {}
    found.pop("$schema", None)
    return found


def seeded(locale: str, source: dict[str, Message], sources: dict[str, str]) -> dict[str, Entry]:
    entries = state(WORK, locale)
    live = shipped(locale)
    for key, value in source.items():
        current = fingerprint(value)
        if key not in live or sources.get(key) != current:
            continue
        text = body(live[key])
        entry = entries.get(key)
        if entry is not None and entry["en"] == current:
            if entry["text"] == text or not entry["reviewed"]:
                continue
            entry["text"] = text
            entry["pinned"] = True
            continue
        entries[key] = {"en": current, "text": text, "reviewed": True}
    return entries


def staged(locale: str, source: dict[str, Message], sources: dict[str, str]) -> dict[str, Entry]:
    entries = state(REDO, locale)
    live = shipped(locale)
    was = REDO / f"{locale}.was.json"
    if not was.exists():
        REDO.mkdir(parents=True, exist_ok=True)
        was.write_text(json.dumps({key: body(value) for key, value in live.items()}, ensure_ascii=False))
    for key, entry in state(WORK, locale).items():
        fresh = key in source and entry["en"] == fingerprint(source[key]) == sources.get(key)
        if fresh and entry.get("pinned") and key in live:
            entries[key] = {"en": entry["en"], "text": body(live[key]), "reviewed": True, "pinned": True}
    return entries


def save(work: Path, locale: str, entries: dict[str, Entry]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    partial = work / f"{locale}.part"
    partial.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    partial.replace(work / f"{locale}.json")


def write(
    locale: str, entries: dict[str, Entry], source: dict[str, Message], sources: dict[str, str], out: Path
) -> None:
    result: dict[str, object] = {"$schema": "https://inlang.com/schema/inlang-message-format"}
    sources.clear()
    for key, value in source.items():
        text = entries[key]["text"]
        sources[key] = entries[key]["en"]
        if isinstance(value, str) or isinstance(text, str):
            result[key] = text
        else:
            variant = value[0]
            result[key] = [{"declarations": variant["declarations"], "selectors": variant["selectors"], "match": text}]
    out.mkdir(parents=True, exist_ok=True)
    partial = out / f"{locale}.json.part"
    partial.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    partial.replace(out / f"{locale}.json")


type Plan = dict[str, tuple[str, batch.Want, list[Item]]]


def planned(file: Path, build: Callable[[], Plan]) -> Plan:
    if file.exists():
        stored: dict[str, list[object]] = json.loads(file.read_text())
        return {
            request: (
                str(locale),
                (cast(MessageCreateParamsNonStreaming, params), float(cast(float, cost))),
                cast(list[Item], group),
            )
            for request, (locale, params, cost, group) in stored.items()
        }
    plan = build()
    if plan:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(
                {request: [locale, want[0], want[1], group] for request, (locale, want, group) in plan.items()},
                ensure_ascii=False,
            )
        )
    return plan


def tries(work: Path, locale: str) -> dict[str, list[object]]:
    file = work / f"{locale}.rejected.json"
    return json.loads(file.read_text()) if file.exists() else {}


def step(targets: list[str], *, redo: bool = False) -> tuple[bool, str]:
    work = REDO if redo else WORK
    out = REDO / "messages" if redo else MESSAGES
    lockfile = out / ".source.json"
    prefix = "redo-" if redo else ""
    WORK.mkdir(parents=True, exist_ok=True)
    GLOSSARY.mkdir(parents=True, exist_ok=True)
    notes_out: list[str] = []
    missing = {
        key: want
        for key, want in glossary_wants(targets).items()
        if not (GLOSSARY / f"{key.split('/')[1]}.json").exists()
    }
    if missing:
        done, note = batch.ensure(SOURCE, "glossary", missing)
        if not done:
            return False, note
        for key in missing:
            locale = key.split("/")[1]
            parsed = reply(key) if key in raw.keys(SOURCE) else None
            if parsed is not None and set(parsed) >= set(TERMS):
                (GLOSSARY / f"{locale}.json").write_text(
                    json.dumps({term: parsed[term] for term in TERMS}, ensure_ascii=False, indent=1) + "\n"
                )
            else:
                notes_out.append(f"glossary for {locale} came back unusable")
        if notes_out:
            raise SystemExit("; ".join(notes_out))
    source = english()
    main_lock = lock()
    sources = lock(lockfile) if redo else main_lock
    entries = {locale: (staged if redo else seeded)(locale, source, main_lock.get(locale, {})) for locale in targets}
    terms = {locale: glossary(locale) for locale in targets}
    failed = {
        locale: {
            key: note
            for key, note in tries(work, locale).items()
            if key in source and not entries[locale].get(key, {}).get("pinned")
        }
        for locale in targets
    }
    for stage in ("translate", "review"):

        def build(stage: str = stage) -> Plan:
            kinds = categories(targets)
            sites = scan()
            hints = notes()
            plan: Plan = {}
            for locale in targets:
                rows: list[Item] = []
                known: dict[str, Text] = {}
                for key, value in source.items():
                    entry = entries[locale].get(key)
                    fresh = entry is not None and entry["en"] == fingerprint(value)
                    if fresh and entry is not None and entry["reviewed"]:
                        known[key] = entry["text"]
                    if stage == "translate" and (not fresh or key in failed[locale]):
                        row = item(key, value, kinds[locale], sites, hints, terms[locale])
                        if key in failed[locale]:
                            note = failed[locale][key]
                            attempt = (
                                f" Failed attempt: {json.dumps(note[2], ensure_ascii=False)}" if len(note) > 2 else ""
                            )
                            row["rejected"] = f"{note[0]}.{attempt}"
                        rows.append(row)
                    if stage == "review" and fresh and entry is not None and not entry["reviewed"]:
                        row = item(key, value, kinds[locale], sites, hints, terms[locale])
                        row["translation"] = entry["text"]
                        if entry.get("flags"):
                            row["flags"] = entry.get("flags", [])
                        rows.append(row)
                extra = examples(state(WORK, locale), source)
                for request, (want, group) in wants(stage, locale, rows, known, source, sites, extra).items():
                    plan[request] = (locale, want, group)
            return plan

        plan = planned(work / f"{stage}.plan.json", build)
        if plan:
            wanted = {request: want for request, (_, want, _) in plan.items()}
            projected = sum(want[1] for want in wanted.values())
            done, note = batch.ensure(SOURCE, prefix + stage, wanted, approved=AUTO if projected < AUTO else None)
            notes_out.append(note)
            if not done:
                return False, "; ".join(notes_out)
        held = raw.keys(SOURCE)
        changed = 0
        for request, (locale, _, group) in plan.items():
            parsed = reply(request) if request in held else None
            for row in group:
                key = row["key"]
                current = fingerprint(source[key]) if key in source and body(source[key]) == row["english"] else None
                if current is None:
                    continue
                if stage == "translate":
                    text = parsed.get(key) if parsed is not None else None
                    hard = "no reply" if parsed is None else problem(row["english"], text, row.get("forms"))
                    count = int(cast(int, failed[locale].get(key, ["", 0])[1]))
                    soft = [] if hard is not None else sloppy(locale, row, text, terms[locale])
                    if hard is None and isinstance(text, (str, dict)) and (not soft or count + 1 >= ATTEMPTS):
                        entries[locale][key] = {
                            "en": current,
                            "text": cast(Text, text),
                            "reviewed": False,
                            "machine": cast(Text, text),
                        }
                        if soft:
                            entries[locale][key]["flags"] = soft
                            print(f"{locale}/{key}: accepted after {ATTEMPTS} tries, for review: {'; '.join(soft)}")
                        failed[locale].pop(key, None)
                    else:
                        reason = hard or "; ".join(soft)
                        failed[locale][key] = [reason, count + 1, text]
                        print(f"{locale}/{key}: try {count + 1} failed: {reason}", flush=True)
                    continue
                entry = entries[locale].get(key)
                if entry is None or entry["en"] != current or parsed is None:
                    continue
                found = parsed.get(key)
                fix = found.get("fix") if isinstance(found, dict) else None
                if (
                    fix is not None
                    and problem(row["english"], fix, row.get("forms")) is None
                    and len(sloppy(locale, row, fix, terms[locale]))
                    <= len(sloppy(locale, row, entry["text"], terms[locale]))
                ):
                    entry["text"] = cast(Text, fix)
                    changed += 1
                entry["reviewed"] = True
                entry["machine"] = entry["text"]
                entry.pop("flags", None)
        for locale in targets:
            save(work, locale, entries[locale])
            partial = work / f"{locale}.rejected.part"
            partial.write_text(json.dumps(failed[locale], ensure_ascii=False, indent=1))
            partial.replace(work / f"{locale}.rejected.json")
        (work / f"{stage}.plan.json").unlink(missing_ok=True)
        if stage == "review" and plan:
            notes_out.append(f"review changed {changed} drafts")
        stuck = [
            f"{locale}/{key}: {note[0]}"
            for locale in targets
            for key, note in failed[locale].items()
            if int(cast(int, note[1])) >= ATTEMPTS
        ]
        if stuck:
            raise SystemExit(f"BLOCKED: {len(stuck)} strings failed checks {ATTEMPTS} times: {'; '.join(stuck[:10])}")
        if stage == "translate" and any(failed.values()):
            return False, f"{sum(len(found) for found in failed.values())} strings failed checks; retranslating them"
    written: list[str] = []
    for locale in targets:
        if all(
            key in entries[locale]
            and entries[locale][key]["en"] == fingerprint(value)
            and entries[locale][key]["reviewed"]
            for key, value in source.items()
        ):
            write(locale, entries[locale], source, sources.setdefault(locale, {}), out)
            written.append(locale)
    out.mkdir(parents=True, exist_ok=True)
    lockfile.write_text(json.dumps(dict(sorted(sources.items())), ensure_ascii=False, indent=2) + "\n")
    notes_out.append(f"wrote {len(written)} of {len(targets)} locales{' to ' + str(out) if redo else ''}")
    return True, "; ".join(notes_out)


def adopt(chosen: list[str]) -> str:
    targets = locales(chosen)
    source = english()
    sources = lock()
    staged_lock = lock(REDO / "messages" / ".source.json")
    lines: list[str] = []
    for locale in targets:
        staged_file = REDO / "messages" / f"{locale}.json"
        if not staged_file.exists():
            lines.append(f"{locale}: nothing staged")
            continue
        fresh: dict[str, Message] = json.loads(staged_file.read_text())
        was: dict[str, Text] = json.loads((REDO / f"{locale}.was.json").read_text())
        live = shipped(locale)
        main = state(WORK, locale)
        locked = sources.setdefault(locale, {})
        taken = kept = 0
        for key, value in source.items():
            current = fingerprint(value)
            if key not in fresh or staged_lock.get(locale, {}).get(key) != current:
                continue
            if (key in live and body(live[key]) != was.get(key)) or main.get(key, {}).get("pinned"):
                kept += 1
                continue
            live[key] = fresh[key]
            locked[key] = current
            main[key] = {"en": current, "text": body(fresh[key]), "reviewed": True, "machine": body(fresh[key])}
            taken += 1
        result: dict[str, object] = {"$schema": "https://inlang.com/schema/inlang-message-format"}
        for key in source:
            if key in live:
                result[key] = live[key]
        (MESSAGES / f"{locale}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        save(WORK, locale, main)
        lines.append(f"{locale}: took {taken} staged strings, kept {kept} edited since staging")
    LOCK.write_text(json.dumps(dict(sorted(sources.items())), ensure_ascii=False, indent=2) + "\n")
    return "\n".join(lines)


def locales(chosen: list[str]) -> list[str]:
    settings = json.loads(SETTINGS.read_text())
    targets = chosen or [locale for locale in settings["locales"] if locale != settings["baseLocale"]]
    unknown = [locale for locale in targets if locale not in LANGUAGES]
    if unknown:
        raise SystemExit(f"add {', '.join(unknown)} to LANGUAGES in pipelines/ea/i18n.py first")
    return targets


def run(chosen: list[str], *, wait: bool = True, redo: bool = False) -> str:
    targets = locales(chosen)
    shown = ""
    while True:
        done, note = step(targets, redo=redo)
        if done or not wait:
            return note
        if note != shown:
            print(note, flush=True)
            shown = note
        time.sleep(POLL)


def quote(chosen: list[str]) -> str:
    targets = locales(chosen)
    source = english()
    kinds = categories(targets)
    sites = scan()
    hints = notes()
    translate = review = 0.0
    requests = 0
    for locale in targets:
        terms = glossary(locale)
        rows = [item(key, value, kinds[locale], sites, hints, terms) for key, value in source.items()]
        for want, group in wants("translate", locale, rows, {}, source, sites, "").values():
            translate += want[1]
            requests += 1
            for row in group:
                row["translation"] = row["english"]
        review += sum(want[1] for want, _ in wants("review", locale, rows, {}, source, sites, "").values())
    glossary_cost = sum(want[1] for want in glossary_wants(targets).values())
    return (
        f"{len(targets)} locales, {len(source)} strings, {requests} translate requests; "
        f"glossary ${glossary_cost:.2f}, translate ${translate:.2f}, review ${review:.2f} "
        f"(list price at batch rates, before cache reads; {sum(1 for key in source if key in sites)} strings have call sites)"
    )
