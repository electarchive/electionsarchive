import collections
import hashlib
import json
import random
import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import batch, i18n, raw
from ea.changeset import Changeset
from ea.sources import shorts
from ea.store import DATA, connect

SOURCE = "name_translations"
VERSION = "1"
LOCALES = ("tr", "kmr", "de", "fr", "pt-BR")
WORK = DATA / "i18n" / "names"
TEXTS = WORK / "texts.json"
PLACES = WORK / "places.json"
VOCAB = WORK / "vocab.json"
MINED = WORK / "templates.json"
TYPES = ("contest", "election", "body", "office", "polity", "government", "electoral_system", "alliance", "party")
EXONYM_MIN = 20
CONTEST_MIN = 2
SAMPLE = 0.02
WHOLE_KINDS = ("contest", "body", "office", "alliance", "electoral_system")
SHORTS = {short for table in (shorts.OFFICES, shorts.BODIES) for names in table.values() for short in names.values()}
FULL_REVIEW = ("tr", "kmr", "fr")
LEFTOVER = {
    "de": re.compile(
        r"\b(?:Township|Supervisors?|Clerk|Trustees?|Judge|Court|Precinct|Primary|Election|Runoff|Unexpired|Term"
        r"|At-Large|City|Town|Village|School|Measure|Proposition|Question|Amendment|Seat|Position|Division|Mayor"
        r"|Sheriff|Treasurer|Assessor|Attorney|Recorder|Auditor|Coroner|Constable|Justice|Peace|Member|Special"
        r"|General|Republican|Democratic|First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth|Tenth)\b"
    ),
    "pt-BR": re.compile(
        r"\b(?:County|Township|Board|Commission|Council|Committee|District|Ward|Supervisors?|Clerk"
        r"|Trustees?|Judge|Court|Precinct|Primary|Election|Runoff|Unexpired|Term|At-Large|City|Town"
        r"|Village|School|Measure|Proposition|Question|Amendment|Seat|Position|Division|Mayor|Sheriff"
        r"|Treasurer|Assessor|Attorney|Recorder|Auditor|Coroner|Constable|Justice|Peace|Member"
        r"|Special|General|Republican|Democratic|First|Second|Third|Fourth|Fifth|Sixth|Seventh"
        r"|Eighth|Ninth|Tenth)\b"
    ),
}

SUFFIX = {
    "County", "Parish", "Township", "City", "Town", "Village", "Borough", "Council", "Municipality", "Commune",
    "District", "Province", "Region", "Department", "State", "Canton", "Ward", "Division", "Constituency",
}  # fmt: skip
GENERIC = {
    "Senate", "House", "Assembly", "Congress", "Council", "Board", "School", "Union", "Commerce", "Independence",
    "Liberty", "Republic", "Center", "Central", "North", "South", "East", "West", "Lake", "Park", "Mayor", "Judge",
    "Court", "State", "General", "Special", "Municipal", "Local", "National", "Federal", "United", "Democratic",
    "Republican", "Green", "Labour", "Liberal", "Conservative", "Unity", "Progress", "Victory", "Justice", "Peace",
    "Harmony", "Friendship", "Hope", "Supreme", "Superior", "Circuit", "Probate", "Chancery", "Water", "Fire",
    "Hospital", "Library", "Sheriff", "Clerk", "Treasurer", "Auditor", "Recorder", "Coroner", "Assessor",
    "Controller", "Commissioner", "Trustee", "Constable", "Marshal", "Education", "Highway", "Cemetery", "Sanitary",
    "Drainage", "Levee", "Port", "Airport", "Transit", "Police", "Election", "Primary", "Runoff", "Measure",
    "Proposition", "Amendment", "Question", "Referendum", "Initiative", "Recall", "Retention", "Governor",
    "Lieutenant", "President", "Secretary", "Attorney", "Representative", "Representatives", "Delegate", "Delegates",
    "Senator", "Member", "Members", "Chamber", "Deputies", "Parliament", "Legislature", "Commons", "Lords",
    "Bundestag", "Landtag", "Post", "Position", "Seat", "Zone", "Precinct", "Area", "Subdistrict", "Dist", "Place",
    "Office", "Group", "At-large", "Large", *SUFFIX,
}  # fmt: skip
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
DATE = re.compile(rf"\b(?:{'|'.join(MONTHS)}) \d{{1,2}}, \d{{4}}\b")
MONTH = re.compile(rf"\b(?:{'|'.join(MONTHS)}) \d{{4}}\b")
ORD = re.compile(r"\b\d+(?:st|nd|rd|th)\b")
NUM = re.compile(r"\b\d+[A-Z]?\b")
TOKEN = re.compile(r"[^\s,()/]+")
WORD = re.compile(
    r"(?<![{\w'’])[A-Z][\w'’.&-]*(?:\s+(?:of|de|du|des|la|le|and|&|on|upon|the|in|sur|-)?\s*[A-Z][\w'’.&-]*)*"
)
SLOT = re.compile(r"\{([a-z]+)(\d*)\}")
MONTH_NAMES = {
    "tr": ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"),
    "kmr": (
        "Kanûna Paşîn", "Sibat", "Adar", "Nîsan", "Gulan", "Hezîran",
        "Tîrmeh", "Tebax", "Îlon", "Cotmeh", "Mijdar", "Kanûna Pêşîn",
    ),
    "de": (
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ),
    "fr": (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ),
    "pt-BR": (
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ),
}  # fmt: skip

SLOTS = """Slots, in braces, are filled in later; keep every slot exactly once and spelled exactly as given, moving it wherever the grammar needs it:
- {{placeN}}: a place name, filled in with its {language} form.
- {{nameN}}: a proper name, kept as written.
- {{nN}}: a number, such as a year or a district number.
- {{ordN}}: an ordinal such as 27th, filled in already written as a {language} ordinal, so add no ordinal ending to it.
- {{dateN}} and {{monthN}}: a full date or a month and year, filled in already formatted.
Never fuse an ending, article, or case suffix to a slot, because the filled-in word cannot be inflected; in Turkish and Kurmanji, phrase the name so each slot stands in its base form (for example, put the place before a noun such as 'seçimi' or 'ilçesi' rather than adding a locative ending)."""

RULES = (
    """You translate the names of elections, contests, offices, bodies, parties, and polities on electionsarchive.com, a world election archive, from American English into {language}, for readers in {place}. Each item is a name pattern ("english") with the type of thing it names ("kind").

Rules:
- Write each name as a careful {language} editor at a quality newspaper would: accurate, concise, and natural, in the style of a heading.
- Give institutions, offices, and parties their established {language} names when they have one (for example, a national parliament's usual {language} name). A party or body of a {language}-speaking country takes its own official name.
- Keep proper nouns that have no {language} form as written.
- Use the glossary below for electoral vocabulary.
- An item with "rejected" was translated before and failed a check; the note says why. Fix that.

"""
    + SLOTS
    + """

Reply with only a JSON object mapping each id to its translation. Include every id you were given and nothing else.

Glossary (English: {language}, with notes):
{glossary}"""
)

PLACE_RULES = """You give the {language} names of places for electionsarchive.com, a world election archive, for readers in {place}. Each item is a place's English name ("english") and the ISO code of the country it lies in ("country"; empty when unknown).

Give the name a {language} newspaper would print. Many places have an established {language} form (countries, many regions and states, some large cities); most have none, and then the name stays exactly as written. Never translate a place name word by word, and never add a word such as 'state' or 'province'.

Reply with only a JSON object mapping each id to its name. Include every id you were given and nothing else."""

REVIEW = (
    """You are the senior {language} reviewer for the names of elections, contests, offices, bodies, parties, and polities on electionsarchive.com, a world election archive, translated from American English for readers in {place}. Each item has the English pattern, the kind of thing it names, and the draft.

Check each draft for wrong meaning or omissions, wrong electoral terms (follow the glossary below), grammar and spelling, unnatural phrasing, untranslated English, and slots that are dropped, duplicated, renamed, or given an ending.

"""
    + SLOTS
    + """

Only report real problems. Reply with only a JSON object mapping each id that needs a change to {{"fix": "<the corrected translation>", "why": "<short English reason>"}}. Reply {{}} when every draft is right.

Glossary (English: {language}, with notes):
{glossary}"""
)

ROOT = """
with recursive up(id, at, depth) as (
  select id, id, 0 from polity
  union
  select up.id, pp.parent_id, up.depth + 1 from up join polity_parent pp on pp.polity_id = up.at where up.depth < 12
), root as (
  select distinct on (up.id) up.id, lower(p.iso_code) as iso
  from up join polity p on p.id = up.at
  where p.iso_code is not null
  order by up.id, p.iso_code ~ '^[A-Z]{2}$' desc, up.depth
)"""

ROOTS = (
    ROOT
    + """, owner(id, polity_id) as (
  select c.id, e.polity_id from contest c join election e on e.id = c.election_id
  union all select id, polity_id from election
  union all select id, polity_id from body
  union all select id, polity_id from office
  union all select id, id from polity
  union all select id, polity_id from government
  union all select id, polity_id from electoral_system
  union all select id, polity_id from party where polity_id is not null
)
select distinct en.type::text, n.text, coalesce(r.iso, '')
from name n
join entity en on en.id = n.entity_id
left join owner o on o.id = n.entity_id
left join root r on r.id = o.polity_id
where n.lang = 'en' and n.is_preferred and en.type::text = any(%s)
"""
)

PLACE_ROWS = (
    ROOT
    + """
select distinct n.text, case
  when en.type = 'area' then split_part(a.scheme_code, '.', 1)
  when p.iso_code ~ '^[A-Z]{2}$' then ''
  else coalesce(r.iso, '')
end
from name n
join entity en on en.id = n.entity_id
left join area a on a.id = n.entity_id
left join polity p on p.id = n.entity_id
left join root r on r.id = n.entity_id
where en.type::text in ('area', 'polity') and n.lang in ('en', 'und') and length(n.text) > 2
"""
)

Slots = list[tuple[str, str]]


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def fetch() -> str:
    with connect() as conn:
        rows = conn.execute(ROOTS, [list(TYPES)]).fetchall()
        places = conn.execute(PLACE_ROWS).fetchall()
    WORK.mkdir(parents=True, exist_ok=True)
    TEXTS.write_text(json.dumps([list(row) for row in rows], ensure_ascii=False))
    countries: dict[str, set[str]] = collections.defaultdict(set)
    for text, iso in places:
        countries[text].add(iso)
    PLACES.write_text(json.dumps({text: sorted(isos) for text, isos in countries.items()}, ensure_ascii=False))
    return f"{len(rows)} names, {len(places)} place names"


class Miner:
    def __init__(self, vocab: set[str]) -> None:
        self.places: dict[str, list[str]] = json.loads(PLACES.read_text())
        self.vocab = vocab

    def rare(self, text: str, put: Callable[[str, str], str]) -> str:
        if not self.vocab:
            return text

        def sub(match: re.Match[str]) -> str:
            words = match.group().split()
            head: list[str] = []
            tail: list[str] = []
            while words and (words[0] in self.vocab or not words[0][0].isupper()):
                head.append(words.pop(0))
            while words and (words[-1] in self.vocab or not words[-1][0].isupper()):
                tail.insert(0, words.pop())
            return " ".join([*head, put("name", " ".join(words)), *tail]) if words else match.group()

        return WORD.sub(sub, text)

    def mask(self, text: str, iso: str) -> tuple[str, Slots]:
        slots: Slots = []

        def put(kind: str, value: str) -> str:
            slots.append((kind, value))
            return f"{{{kind}{len(slots)}}}"

        masked = DATE.sub(lambda match: put("date", match.group()), text)
        masked = MONTH.sub(lambda match: put("month", match.group()), masked)
        tokens = [(match.start(), match.end()) for match in TOKEN.finditer(masked)]
        out: list[str] = []
        at = last = 0
        while at < len(tokens):
            hit = None
            for end in range(min(len(tokens), at + 7), at, -1):
                span = masked[tokens[at][0] : tokens[end - 1][1]]
                if (
                    span in self.places
                    and span[0].isupper()
                    and not (end - at == 1 and (span in GENERIC or not {"", iso} & set(self.places[span])))
                    and "{" not in span
                ):
                    hit = end
                    break
            if hit is None:
                at += 1
                continue
            start, stop = tokens[at][0], tokens[hit - 1][1]
            words = masked[start:stop].split(" ")
            tail: list[str] = []
            while len(words) > 1 and words[-1] in SUFFIX:
                tail.insert(0, words.pop())
            out.extend([masked[last:start], " ".join([put("place", " ".join(words)), *tail])])
            last = stop
            at = hit
        out.append(masked[last:])
        masked = self.rare("".join(out), put)
        masked = ORD.sub(lambda match: put("ord", match.group()), masked)
        masked = NUM.sub(lambda match: put("n", match.group()), masked)
        order = [int(number) - 1 for _, number in SLOT.findall(masked)]
        counts: collections.Counter[str] = collections.Counter()

        def renumber(match: re.Match[str]) -> str:
            counts[match.group(1)] += 1
            return f"{{{match.group(1)}{counts[match.group(1)]}}}"

        return SLOT.sub(renumber, masked), [slots[index] for index in order]


def texts() -> list[tuple[str, str, str]]:
    return [cast(tuple[str, str, str], tuple(row)) for row in json.loads(TEXTS.read_text())]


def vocab() -> set[str]:
    if VOCAB.exists():
        return set(json.loads(VOCAB.read_text()))
    miner = Miner(set())
    seen: collections.Counter[str] = collections.Counter()
    for _, text, iso in texts():
        template, _ = miner.mask(text, iso)
        seen.update(set(re.findall(r"[A-Z][\w'’.&-]*", SLOT.sub(" ", template))))
    words = {word for word, count in seen.items() if count >= 25}
    VOCAB.write_text(json.dumps(sorted(words), ensure_ascii=False))
    return words


def key(template: str) -> str:
    return md5(template)[:16]


def split(miner: Miner, kind: str, text: str, iso: str) -> tuple[str, Slots, bool]:
    template, slots = miner.mask(text, iso)
    if text in SHORTS or (
        kind in WHOLE_KINDS
        and slots
        and len(text.split()) <= 4
        and not re.search(r"\d", text)
        and not re.search(r"[a-z]{3}", SLOT.sub("", template))
    ):
        return text, [], True
    return template, slots, False


def mine() -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    if MINED.exists():
        held = json.loads(MINED.read_text())
        return held["templates"], held["places"]
    miner = Miner(vocab())
    templates: dict[str, dict[str, object]] = {}
    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    bare: set[tuple[str, str]] = set()
    for kind, text, iso in texts():
        template, slots, whole = split(miner, kind, text, iso)
        found = templates.setdefault(key(template), {"english": template, "kind": kind, "count": 0})
        found["count"] = cast(int, found["count"]) + 1
        if whole:
            found["whole"] = True
        for slot, value in slots:
            if slot == "place":
                counts[(iso, value)] += 1
        if kind == "polity" and template == "{place1}":
            bare.add((iso, slots[0][1]))
    places: dict[str, dict[str, object]] = {
        f"{iso}|{value}": {"english": value, "country": iso}
        for (iso, value), count in counts.items()
        if count >= EXONYM_MIN or (iso, value) in bare
    }
    MINED.write_text(json.dumps({"templates": templates, "places": places}, ensure_ascii=False))
    return templates, places


def translatable(item: dict[str, object]) -> bool:
    if item["kind"] == "contest" and cast(int, item["count"]) < CONTEST_MIN and not item.get("whole"):
        return False
    return re.search(r"[A-Za-z]", SLOT.sub("", cast(str, item["english"]))) is not None


def slots(text: str) -> list[str]:
    return sorted(match.group() for match in SLOT.finditer(text))


def check(english: str, text: object) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "empty"
    if slots(english) != slots(text) or text.count("{") != english.count("{"):
        return f"the slots must be exactly {', '.join(slots(english))}, each once and spelled as given"
    return None


def system(rules: str, locale: str) -> list[dict[str, object]]:
    language, place = i18n.LANGUAGES[locale]
    terms = "\n".join(
        f"- {term}: {entry['term']}" + (f" ({entry['note']})" if entry.get("note") else "")
        for term, entry in i18n.glossary(locale).items()
    )
    return [
        {
            "type": "text",
            "text": rules.format(language=language, place=place, glossary=terms),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def wants(
    stage: str, rules: str, locale: str, rows: list[dict[str, str]]
) -> dict[str, tuple[batch.Want, list[dict[str, str]]]]:
    head = system(rules, locale)
    verb = "Review" if rules is REVIEW else "Translate"
    groups: list[list[dict[str, str]]] = [[]]
    size = 0
    for row in rows:
        weight = len(json.dumps(row, ensure_ascii=False))
        if groups[-1] and size + weight > i18n.CHUNK_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(row)
        size += weight
    out: dict[str, tuple[batch.Want, list[dict[str, str]]]] = {}
    for group in [group for group in groups if group]:
        prompt = f"{verb} these items:\n\n" + json.dumps(group, ensure_ascii=False, indent=1)
        english = sum(len(row["english"]) for row in group)
        params: dict[str, object] = {
            "model": i18n.MODEL,
            "max_tokens": 32_000,
            "output_config": {"effort": i18n.EFFORT},
            "system": head,
            "messages": [{"role": "user", "content": prompt}],
        }
        out[f"{stage}/{locale}/{i18n.digest([head, group])}"] = (
            (
                cast(MessageCreateParamsNonStreaming, params),
                i18n.estimate(prompt, english * 1.3 + len(group) * 24 if verb == "Translate" else english * 0.1),
            ),
            group,
        )
    return out


def held(name: str) -> dict[str, str]:
    file = WORK / f"{name}.json"
    return json.loads(file.read_text()) if file.exists() else {}


def quote() -> str:
    lines: list[str] = []
    total = 0.0
    templates, places = mine()
    for locale in LOCALES:
        cost = 0.0
        for kind, rules, items, extra in (
            ("templates", RULES, templates, "kind"),
            ("places", PLACE_RULES, places, "country"),
        ):
            rows = [
                {"id": id, "english": cast(str, item["english"]), extra: cast(str, item[extra])}
                for id, item in items.items()
                if kind == "places" or translatable(item)
            ]
            cost += sum(want[1] for want, _ in wants(f"names-{kind}", rules, locale, rows).values())
        total += cost
        lines.append(f"{locale} ${cost:.2f}")
    return (
        f"{len(templates)} templates and {len(places)} places; "
        + ", ".join(lines)
        + f"; total ${total:.2f} before the review sample, thinking, and cache reads"
    )


def parse() -> str:
    templates, places = mine()
    sources = {"templates": (RULES, templates, "kind"), "places": (PLACE_RULES, places, "country")}
    plan: dict[str, tuple[str, str, batch.Want, list[dict[str, str]]]] = {}
    for locale in LOCALES:
        for kind, (rules, items, extra) in sources.items():
            done = held(f"{locale}.{kind}")
            failed = held(f"{locale}.{kind}.rejected")
            rows = [
                {"id": id, "english": cast(str, item["english"]), extra: cast(str, item[extra])}
                | ({"rejected": failed[id]} if id in failed else {})
                for id, item in items.items()
                if id not in done and (kind == "places" or translatable(item))
            ]
            for request, (want, group) in wants("names", rules, locale, rows).items():
                plan[request] = (locale, kind, want, group)
    notes: list[str] = []
    if plan:
        ready, note = batch.ensure(i18n.SOURCE, "names", {request: want for request, (_, _, want, _) in plan.items()})
        notes.append(note)
        if not ready:
            return note
        keys = raw.keys(i18n.SOURCE)
        for locale in LOCALES:
            for kind in sources:
                done = held(f"{locale}.{kind}")
                failed: dict[str, str] = {}
                for request, (owner, owned, _, group) in plan.items():
                    if owner != locale or owned != kind:
                        continue
                    parsed = i18n.reply(request) if request in keys else None
                    for row in group:
                        text = parsed.get(row["id"]) if parsed is not None else None
                        issue = "no reply" if parsed is None else check(row["english"], text)
                        if issue is None and isinstance(text, str):
                            done[row["id"]] = text.strip()
                        else:
                            failed[row["id"]] = issue or "invalid"
                (WORK / f"{locale}.{kind}.json").write_text(json.dumps(done, ensure_ascii=False))
                (WORK / f"{locale}.{kind}.rejected.json").write_text(json.dumps(failed, ensure_ascii=False))
        missing = sum(len(held(f"{locale}.{kind}.rejected")) for locale in LOCALES for kind in sources)
        if missing:
            return f"{note}; {missing} items failed checks; run parse again to retranslate them"
    review: dict[str, tuple[str, batch.Want, list[dict[str, str]]]] = {}
    for locale in LOCALES:
        done = held(f"{locale}.templates")
        reviewed = held(f"{locale}.reviewed")
        picked = set(random.Random(locale).sample(sorted(done), round(len(done) * SAMPLE)))
        leftover = LEFTOVER.get(locale)
        rows = [
            {"id": id, "english": cast(str, templates[id]["english"]), "kind": cast(str, templates[id]["kind"])}
            | {"draft": text}
            for id, text in done.items()
            if id not in reviewed
            and (id in picked or locale in FULL_REVIEW or (leftover is not None and leftover.search(text) is not None))
        ]
        for request, (want, group) in wants("names-review", REVIEW, locale, rows).items():
            review[request] = (locale, want, group)
    if not review:
        return "; ".join(notes) or "nothing left to translate or review"
    ready, note = batch.ensure(i18n.SOURCE, "names-review", {request: want for request, (_, want, _) in review.items()})
    notes.append(note)
    if not ready:
        return "; ".join(notes)
    keys = raw.keys(i18n.SOURCE)
    flagged: collections.Counter[str] = collections.Counter()
    why: list[str] = []
    for locale in LOCALES:
        done = held(f"{locale}.templates")
        reviewed = held(f"{locale}.reviewed")
        for request, (owner, _, group) in review.items():
            if owner != locale or request not in keys:
                continue
            parsed = i18n.reply(request) or {}
            for row in group:
                reviewed[row["id"]] = row["draft"]
                found = parsed.get(row["id"])
                fix = found.get("fix") if isinstance(found, dict) else None
                if not isinstance(fix, str) or fix.strip() == row["draft"]:
                    continue
                flagged[locale] += 1
                why.append(
                    f"{locale}\t{row['english']}\t{row['draft']}\t{fix}\t{cast(dict[str, str], found).get('why')}"
                )
                if check(row["english"], fix) is None:
                    done[row["id"]] = fix.strip()
        (WORK / f"{locale}.templates.json").write_text(json.dumps(done, ensure_ascii=False))
        (WORK / f"{locale}.reviewed.json").write_text(json.dumps(reviewed, ensure_ascii=False))
    with (WORK / "review.tsv").open("a") as file:
        file.writelines(f"{line}\n" for line in why)
    sampled = {
        locale: sum(1 for _, (owner, _, group) in review.items() if owner == locale for _ in group)
        for locale in LOCALES
    }
    notes.append(
        "review flag rate: " + ", ".join(f"{locale} {flagged[locale]}/{sampled[locale]}" for locale in LOCALES)
    )
    return "; ".join(notes)


def ordinal(number: str, locale: str) -> str:
    digits = re.sub(r"\D", "", number)
    if locale == "fr":
        return "1er" if digits == "1" else f"{digits}e"
    if locale == "pt-BR":
        return f"{digits}º"
    return f"{digits}."


def date(value: str, locale: str) -> str:
    month, _, rest = value.partition(" ")
    names = MONTH_NAMES[locale]
    name = names[MONTHS.index(month)]
    if "," not in rest:
        return f"{name} de {rest}" if locale == "pt-BR" else f"{name} {rest}"
    day, _, year = rest.partition(", ")
    if locale == "de":
        return f"{day}. {name} {year}"
    if locale == "fr":
        return f"{'1er' if day == '1' else day} {name} {year}"
    if locale == "pt-BR":
        return f"{day} de {name} de {year}"
    return f"{day} {name} {year}"


def emit() -> list[Path]:
    _, places = mine()
    miner = Miner(vocab())
    names = texts()
    written: list[Path] = []
    with connect() as conn:
        for locale in LOCALES:
            done = held(f"{locale}.templates")
            exonyms = {id: text for id, text in held(f"{locale}.places").items() if text != places[id]["english"]}
            bodies: dict[str, str | None] = {}

            def offer(english: str, body: str) -> None:
                if body == english:
                    return
                known = bodies.get(english, body)
                bodies[english] = body if known == body else None

            for id, exonym in exonyms.items():
                offer(cast(str, places[id]["english"]), exonym)
            for kind, text, iso in names:
                template, found, _ = split(miner, kind, text, iso)
                pattern = done.get(key(template))
                if pattern is None:
                    continue
                values: dict[str, str] = {}
                counts: collections.Counter[str] = collections.Counter()
                for kind, value in found:
                    counts[kind] += 1
                    filled = value
                    if kind == "place":
                        filled = exonyms.get(f"{iso}|{value}", value)
                    elif kind == "ord":
                        filled = ordinal(value, locale)
                    elif kind in ("date", "month"):
                        filled = date(value, locale)
                    values[f"{kind}{counts[kind]}"] = filled
                offer(text, SLOT.sub(lambda match: values.get(match.group(1) + match.group(2), match.group()), pattern))
            cs = Changeset(
                conn,
                pipeline=f"{SOURCE}.{locale}",
                version=VERSION,
                summary=f"Machine translations of election, contest, office, body, party, and polity names into {i18n.LANGUAGES[locale][0]}",
                polity=None,
            )
            for english, body in bodies.items():
                if body is not None:
                    cs.add(
                        "translation",
                        source_md5=md5(english),
                        lang=locale,
                        source_lang="en",
                        body=body,
                        origin="machine",
                    )
            written.append(cs.write())
    return written
