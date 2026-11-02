import hashlib
import json
import re
import subprocess
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
WORK = DATA / "i18n"
CHUNK_CHARS = 20_000
SITES = 3

LOCALES = {
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


class Variant(TypedDict):
    declarations: list[str]
    selectors: list[str]
    match: dict[str, str]


type Message = str | list[Variant]
type Text = str | dict[str, str]


class Item(TypedDict):
    key: str
    english: Text
    placeholders: list[str]
    forms: NotRequired[list[str]]
    sites: NotRequired[list[str]]
    translation: NotRequired[Text]
    rejected: NotRequired[str]


class Entry(TypedDict):
    en: str
    text: Text
    reviewed: bool


def english() -> dict[str, Message]:
    messages: dict[str, Message] = json.loads((MESSAGES / "en.json").read_text())
    messages.pop("$schema", None)
    return messages


def digest(value: object) -> str:
    return hashlib.md5(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def categories() -> dict[str, dict[str, list[str]]]:
    script = (
        "const out={};for(const l of process.argv.slice(1)){out[l]={plural:new Intl.PluralRules(l)"
        ".resolvedOptions().pluralCategories,ordinal:new Intl.PluralRules(l,{type:'ordinal'})"
        ".resolvedOptions().pluralCategories}}console.log(JSON.stringify(out))"
    )
    found: dict[str, dict[str, list[str]]] = json.loads(
        subprocess.run(["node", "-e", script, *LOCALES], capture_output=True, text=True, check=True).stdout
    )
    return found


def sites() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    out = subprocess.run(
        ["rg", "-n", "--no-heading", "-g", "!src/lib/paraglide/**", r"\bm\.([a-z0-9_]+)\(", "src"],
        cwd=WEB,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in out.splitlines():
        place, _, rest = line.partition(":")
        number, _, code = rest.partition(":")
        for key in sorted(set(re.findall(r"\bm\.([a-z0-9_]+)\(", code))):
            here = found.setdefault(key, [])
            if len(here) < SITES:
                here.append(f"{place}:{number}: {' '.join(code.split())[:180]}")
    return found


def holes(text: str) -> list[str]:
    return sorted(re.findall(r"\{([A-Za-z0-9_]+)\}", text))


def forms(variant: Variant, kinds: dict[str, list[str]]) -> list[str]:
    combos: list[list[str]] = [[]]
    for name in variant["selectors"]:
        ordinal = any(line.startswith(f"local {name} =") and "ordinal" in line for line in variant["declarations"])
        options = [
            f"{name}={category}" for category in kinds["ordinal" if ordinal else "plural"] if category != "other"
        ]
        combos = [[*combo, option] for combo in combos for option in [*options, f"{name}=*"]]
    return [", ".join(combo) for combo in combos]


def item(key: str, value: Message, kinds: dict[str, list[str]], where: dict[str, list[str]]) -> Item:
    texts = [value] if isinstance(value, str) else list(value[0]["match"].values())
    row: Item = {
        "key": key,
        "english": value if isinstance(value, str) else value[0]["match"],
        "placeholders": sorted({hole for text in texts for hole in holes(text)}),
    }
    if not isinstance(value, str):
        row["forms"] = forms(value[0], kinds)
    if key in where:
        row["sites"] = where[key]
    return row


def chunks(rows: list[Item]) -> list[list[Item]]:
    groups: list[list[Item]] = [[]]
    size = 0
    for row in rows:
        weight = len(json.dumps(row, ensure_ascii=False))
        if groups[-1] and size + weight > CHUNK_CHARS:
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
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def glossary(locale: str) -> dict[str, dict[str, str]]:
    file = GLOSSARY / f"{locale}.json"
    return json.loads(file.read_text()) if file.exists() else {}


def glossary_wants() -> dict[str, batch.Want]:
    wanted: dict[str, batch.Want] = {}
    for locale, (language, place) in LOCALES.items():
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
        wanted[f"glossary/{locale}/{digest(TERMS)}"] = (
            {"model": MODEL, "max_tokens": 16_000, "messages": [{"role": "user", "content": prompt}]},
            estimate(prompt, 12_000),
        )
    return wanted


RULES = """You translate the interface of electionsarchive.com, a world election archive (results down to the precinct, maps, parties, people, offices, ballot measures, live election-night coverage, and an API and tools for agents), from American English into {language}, for readers in {place}.

Rules:
- Translate meaning, not words. Write what a native {language} interface writer at a national statistics office or a quality newspaper would write: natural, concise, and consistent.
- Use the glossary below for electoral vocabulary every time the concept appears, inflected as the grammar requires.
- Never translate proper nouns: party names, people, places, the product name electionsarchive, brand names, file formats, and code (JSON, CSV, API, MCP, URL, SQL, Markdown).
- Keep every placeholder such as {{name}} or {{countText}} exactly as written, braces included; never translate or rename one. Move placeholders wherever the grammar needs them. Placeholders hold names, numbers, dates, or already-translated phrases; the call sites show what each holds.
- Keep markup, HTML tags, Markdown, URLs, email addresses, keyboard keys, and escaped characters exactly as written.
- Use the language's own punctuation, quotation marks, number words, and typographic conventions. Do not copy the English serial comma unless the language uses one.
- Address the reader the way good {language} software does, consistently across all strings.
- Labels, buttons, column headers, and tab names must stay short: when the English is under about 25 characters, keep the translation close to that length, abbreviating as the language conventionally does if needed.
- A string with "forms" is a plural or ordinal message: return an object with exactly those keys. Each key names CLDR categories for {language} ("*" means other). Write each form correctly for numbers in that category; repeat a form if two categories share it.
- The "sites" are the Svelte or TypeScript lines that render the string; use them to see what the string labels and how short it must be. Neighboring keys usually belong to the same screen.
- Keys starting with language_banner are shown to a visitor whose browser prefers {language}, to offer the site in {language}: where the English says "English", name {language} instead, in its own grammar.
- An item with "rejected" was translated before and failed a check; the note says why. Fix that.

Reply with only a JSON object mapping each key to its translation (a string, or an object of forms). Include every key you were given and nothing else.

Glossary (English: {language}, with notes):
{glossary}"""

REVIEW = """You are the senior {language} reviewer for the interface of electionsarchive.com, a world election archive, translated from American English for readers in {place}. Each item has the English, the draft translation, and the lines of code that render it.

Check each draft for: wrong meaning or omissions; wrong electoral terms (follow the glossary below); grammar, agreement, case, and spelling; unnatural or machine-sounding phrasing; untranslated English; inconsistent register or terminology with neighboring strings; labels that are much longer than the space allows; placeholders ({{name}}) renamed, translated, dropped, or in an ungrammatical position; plural forms that are wrong for their CLDR category ("*" means other); and translated proper nouns (party names, people, places, electionsarchive, JSON, API, MCP must stay as written).

Only report real problems. Reply with only a JSON object mapping each key that needs a change to {{"fix": <the corrected translation, same shape as the draft>, "why": "<short English reason>"}}. Reply {{}} when every draft is right.

Glossary (English: {language}, with notes):
{glossary}"""


def system(template: str, locale: str) -> list[dict[str, object]]:
    language, place = LOCALES[locale]
    terms = "\n".join(
        f"- {term}: {entry['term']}" + (f" ({entry['note']})" if entry.get("note") else "")
        for term, entry in glossary(locale).items()
    )
    return [
        {
            "type": "text",
            "text": template.format(language=language, place=place, glossary=terms),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def wants(stage: str, locale: str, rows: list[Item]) -> dict[str, tuple[batch.Want, list[Item]]]:
    head = system(RULES if stage == "translate" else REVIEW, locale)
    out: dict[str, tuple[batch.Want, list[Item]]] = {}
    for group in chunks(rows):
        body = json.dumps(group, ensure_ascii=False, indent=1)
        verb = "Translate" if stage == "translate" else "Review"
        prompt = f"{verb} these {len(group)} strings:\n\n{body}"
        english = sum(len(json.dumps(row["english"], ensure_ascii=False)) + len(row["key"]) for row in group)
        output = english * 1.3 if stage == "translate" else english * 0.25
        params: dict[str, object] = {
            "model": MODEL,
            "max_tokens": 32_000,
            "system": head,
            "messages": [{"role": "user", "content": prompt}],
        }
        out[f"{stage}/{locale}/{digest([head, group])}"] = (
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
        for pattern, label in ((r"<[^>]+>", "markup"), (r"https?://[^\s)]+", "URLs"), (r"`[^`]+`", "code spans")):
            if sorted(re.findall(pattern, before)) != sorted(re.findall(pattern, after)):
                return f"{label} must match the English: {re.findall(pattern, before)}"
        if after == before and len(re.findall(r"\b[a-z]{2,}\b", before)) >= 4:
            return "left in English"
    return None


def state(locale: str) -> dict[str, Entry]:
    file = WORK / f"{locale}.json"
    return json.loads(file.read_text()) if file.exists() else {}


def save(locale: str, entries: dict[str, Entry]) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    partial = WORK / f"{locale}.part"
    partial.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    partial.replace(WORK / f"{locale}.json")


def write(locale: str, entries: dict[str, Entry], source: dict[str, Message]) -> None:
    out: dict[str, object] = {"$schema": "https://inlang.com/schema/inlang-message-format"}
    for key, value in source.items():
        entry = entries[key]
        text = entry["text"]
        if isinstance(value, str) or isinstance(text, str):
            out[key] = text
        else:
            variant = value[0]
            out[key] = [{"declarations": variant["declarations"], "selectors": variant["selectors"], "match": text}]
    (MESSAGES / f"{locale}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")


def advance(stage: str, plans: dict[str, dict[str, tuple[batch.Want, list[Item]]]]) -> tuple[bool, str]:
    wanted = {key: want for plan in plans.values() for key, (want, _) in plan.items()}
    if not wanted:
        return True, f"{stage}: nothing to send"
    return batch.ensure(SOURCE, stage, wanted)


def run() -> str:
    WORK.mkdir(parents=True, exist_ok=True)
    GLOSSARY.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    missing = {
        key: want for key, want in glossary_wants().items() if not (GLOSSARY / f"{key.split('/')[1]}.json").exists()
    }
    if missing:
        done, note = batch.ensure(SOURCE, "glossary", missing)
        if not done:
            return note
        for key in missing:
            locale = key.split("/")[1]
            parsed = reply(key) if key in raw.keys(SOURCE) else None
            if parsed is not None and set(parsed) >= set(TERMS):
                (GLOSSARY / f"{locale}.json").write_text(
                    json.dumps({term: parsed[term] for term in TERMS}, ensure_ascii=False, indent=1) + "\n"
                )
            else:
                notes.append(f"glossary for {locale} came back unusable")
        if notes:
            return "; ".join(notes)
    source = english()
    kinds = categories()
    where = sites()
    for stage in ("translate", "review"):
        plans: dict[str, dict[str, tuple[batch.Want, list[Item]]]] = {}
        entries = {locale: state(locale) for locale in LOCALES}
        for locale in LOCALES:
            rows: list[Item] = []
            for key, value in source.items():
                entry = entries[locale].get(key)
                fresh = entry is not None and entry["en"] == digest(value)
                if stage == "translate" and not fresh:
                    row = item(key, value, kinds[locale], where)
                    note = rejected(locale).get(key)
                    if note is not None:
                        row["rejected"] = note
                    rows.append(row)
                if stage == "review" and fresh and entry is not None and not entry["reviewed"]:
                    row = item(key, value, kinds[locale], where)
                    row["translation"] = entry["text"]
                    rows.append(row)
            if rows:
                plans[locale] = wants(stage, locale, rows)
        done, note = advance(stage, plans)
        notes.append(note)
        if not done:
            return "; ".join(notes)
        held = raw.keys(SOURCE)
        for locale, plan in plans.items():
            bad = rejected(locale)
            for request, (_, group) in plan.items():
                parsed = reply(request) if request in held else None
                for row in group:
                    key = row["key"]
                    if stage == "translate":
                        text = parsed.get(key) if parsed is not None else None
                        issue = "no reply" if parsed is None else problem(row["english"], text, row.get("forms"))
                        if issue is None and isinstance(text, (str, dict)):
                            entries[locale][key] = {
                                "en": digest(source[key]),
                                "text": cast(Text, text),
                                "reviewed": False,
                            }
                            bad.pop(key, None)
                        else:
                            bad[key] = issue or "invalid"
                        continue
                    if parsed is None:
                        continue
                    found = parsed.get(key)
                    fix = found.get("fix") if isinstance(found, dict) else None
                    if fix is not None and problem(row["english"], fix, row.get("forms")) is None:
                        entries[locale][key]["text"] = cast(Text, fix)
                    entries[locale][key]["reviewed"] = True
            save(locale, entries[locale])
            (WORK / f"{locale}.rejected.json").write_text(json.dumps(bad, ensure_ascii=False, indent=1))
        if stage == "translate" and any(rejected(locale) for locale in LOCALES):
            failed = sum(len(rejected(locale)) for locale in LOCALES)
            return f"{failed} strings failed checks; run `ea i18n` again to retranslate them"
    written: list[str] = []
    for locale in LOCALES:
        entries = state(locale)
        if all(
            key in entries and entries[key]["en"] == digest(value) and entries[key]["reviewed"]
            for key, value in source.items()
        ):
            write(locale, entries, source)
            written.append(locale)
    notes.append(f"wrote {len(written)} of {len(LOCALES)} locales: {', '.join(written)}")
    return "; ".join(notes)


def rejected(locale: str) -> dict[str, str]:
    file = WORK / f"{locale}.rejected.json"
    return json.loads(file.read_text()) if file.exists() else {}


def quote() -> str:
    source = english()
    kinds = categories()
    where = sites()
    translate = review = 0.0
    requests = 0
    for locale in LOCALES:
        rows = [item(key, value, kinds[locale], where) for key, value in source.items()]
        for want, group in wants("translate", locale, rows).values():
            translate += want[1]
            requests += 1
            for row in group:
                row["translation"] = row["english"]
        review += sum(want[1] for want, _ in wants("review", locale, rows).values())
    glossary_cost = sum(want[1] for want in glossary_wants().values())
    return (
        f"{len(LOCALES)} locales, {len(source)} strings, {requests} translate requests; "
        f"glossary ${glossary_cost:.2f}, translate ${translate:.2f}, review ${review:.2f} "
        "(list price at batch rates, before cache reads)"
    )
