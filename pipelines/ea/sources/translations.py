import hashlib
import json
import random
import re
from pathlib import Path
from typing import cast

from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import batch, i18n, raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "translations"
VERSION = "1"
LOCALES = ("tr", "kmr", "de", "fr", "pt-BR")
WORK = DATA / "i18n" / "notes"
TEXTS = WORK / "texts.json"
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
DECIMAL = re.compile(r"\d+\.\d+")
SEPARATORS = {"tr": (".", ","), "kmr": (".", ","), "de": (".", ","), "fr": (" ", ","), "pt-BR": (".", ",")}
SAMPLE = 0.02
COLUMNS = (
    ("note", "body_md", "lang = 'en' and kind::text <> 'retired'"),
    ("outcome", "notes", "true"),
    ("area_event", "notes", "true"),
    ("polity_period", "notes", "true"),
    ("source", "notes", "true"),
    ("measure", "description", "true"),
    ("indicator", "description", "true"),
    ("channel", "description", "true"),
    ("scheme", "noun", "true"),
)

RULES = """You translate reader-facing notes of electionsarchive.com, a world election archive, from American English into {language}, for readers in {place}. The notes explain results, sources, methods, and caveats on election, contest, party, person, and place pages.

Rules:
- Translate meaning, not words, as a careful {language} editor at a national statistics office or a quality newspaper would write it: accurate, plain, and natural.
- Use the glossary below for electoral vocabulary every time the concept appears, inflected as the grammar requires.
- Keep proper nouns as written: people, parties, places, laws, offices' official titles, sources, publications, and the product name electionsarchive. A name with an established {language} form (a country, a well-known body) may take it.
- Numbers are masked as {{1}}, {{2}}, and so on. Keep every one exactly once, as written; move it wherever the grammar needs it. They hold numbers, years, or dates already formatted.
- Keep Markdown, links, URLs, code spans, and quoted text in another language exactly as written.
- Use the language's own punctuation, quotation marks, and typographic conventions.
- An item with "rejected" was translated before and failed a check; the note says why. Fix that.

Reply with only a JSON object mapping each id to its translation. Include every id you were given and nothing else.

Glossary (English: {language}, with notes):
{glossary}"""

REVIEW = """You are the senior {language} reviewer for reader-facing notes of electionsarchive.com, a world election archive, translated from American English for readers in {place}. Each item has the English (numbers masked as {{1}}, {{2}}) and the draft.

Check each draft for wrong meaning or omissions, wrong electoral terms (follow the glossary below), grammar and spelling, unnatural phrasing, untranslated English, translated proper nouns, and masked numbers dropped, duplicated, or misplaced.

Only report real problems. Reply with only a JSON object mapping each id that needs a change to {{"fix": "<the corrected translation>", "why": "<short English reason>"}}. Reply {{}} when every draft is right.

Glossary (English: {language}, with notes):
{glossary}"""


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def mask(text: str) -> tuple[str, list[str]]:
    if "{" in text or "}" in text:
        return text, []
    found: list[str] = []

    def hole(match: re.Match[str]) -> str:
        found.append(match.group())
        return f"{{{len(found)}}}"

    return NUMBER.sub(hole, text), found


def localized(number: str, locale: str) -> str:
    group, point = SEPARATORS[locale]
    if GROUPED.fullmatch(number):
        whole, _, fraction = number.partition(".")
        return whole.replace(",", group) + (point + fraction if fraction else "")
    if DECIMAL.fullmatch(number):
        return number.replace(".", point)
    return number


def unmask(template: str, numbers: list[str], locale: str) -> str:
    return re.sub(r"\{(\d+)\}", lambda match: localized(numbers[int(match.group(1)) - 1], locale), template)


def fetch() -> str:
    texts: set[str] = set()
    with connect() as conn:
        for table, column, where in COLUMNS:
            texts.update(
                row[0]
                for row in conn.execute(
                    f"select distinct {column} from {table} where {where} and {column} is not null and {column} <> ''"
                ).fetchall()
            )
    WORK.mkdir(parents=True, exist_ok=True)
    TEXTS.write_text(json.dumps(sorted(texts), ensure_ascii=False))
    return f"{len(texts)} distinct texts"


def templates() -> dict[str, str]:
    found: dict[str, str] = {}
    for text in json.loads(TEXTS.read_text()):
        template, _ = mask(text)
        found[md5(template)[:16]] = template
    return found


def system(template: str, locale: str) -> list[dict[str, object]]:
    language, place = i18n.LANGUAGES[locale]
    terms = "\n".join(
        f"- {term}: {entry['term']}" + (f" ({entry['note']})" if entry.get("note") else "")
        for term, entry in i18n.glossary(locale).items()
    )
    return [
        {
            "type": "text",
            "text": template.format(language=language, place=place, glossary=terms),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def wants(stage: str, locale: str, rows: list[dict[str, str]]) -> dict[str, tuple[batch.Want, list[dict[str, str]]]]:
    head = system(RULES if stage == "notes" else REVIEW, locale)
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
        prompt = ("Translate" if stage == "notes" else "Review") + " these items:\n\n"
        prompt += json.dumps(group, ensure_ascii=False, indent=1)
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
                i18n.estimate(prompt, english * 1.2 if stage == "notes" else english * 0.1),
            ),
            group,
        )
    return out


def held(locale: str) -> dict[str, str]:
    file = WORK / f"{locale}.json"
    return json.loads(file.read_text()) if file.exists() else {}


def quote() -> str:
    found = templates()
    lines: list[str] = []
    total = 0.0
    for locale in LOCALES:
        rows = [{"id": key, "english": text} for key, text in found.items()]
        cost = sum(want[1] for want, _ in wants("notes", locale, rows).values())
        total += cost
        lines.append(f"{locale} ${cost:.2f}")
    return f"{len(found)} templates; " + ", ".join(lines) + f"; total ${total:.2f} before thinking and cache reads"


def parse() -> str:
    found = templates()
    notes: list[str] = []
    for stage in ("notes", "notes-review"):
        plan: dict[str, tuple[str, batch.Want, list[dict[str, str]]]] = {}
        for locale in LOCALES:
            done = held(locale)
            failed = held(f"{locale}.rejected")
            if stage == "notes":
                rows = [
                    {"id": key, "english": text} | ({"rejected": failed[key]} if key in failed else {})
                    for key, text in found.items()
                    if key not in done
                ]
            else:
                picked = random.Random(locale).sample(sorted(found), round(len(found) * SAMPLE))
                rows = [{"id": key, "english": found[key], "draft": done[key]} for key in picked if key in done]
            for request, (want, group) in wants(stage, locale, rows).items():
                plan[request] = (locale, want, group)
        if not plan:
            continue
        ready, note = batch.ensure(i18n.SOURCE, stage, {request: want for request, (_, want, _) in plan.items()})
        notes.append(note)
        if not ready:
            return "; ".join(notes)
        keys = raw.keys(i18n.SOURCE)
        flagged: dict[str, int] = {}
        for locale in LOCALES:
            done = held(locale)
            failed: dict[str, str] = {} if stage == "notes" else held(f"{locale}.rejected")
            for request, (owner, _, group) in plan.items():
                if owner != locale:
                    continue
                parsed = i18n.reply(request) if request in keys else None
                for row in group:
                    key = row["id"]
                    if stage == "notes":
                        text = parsed.get(key) if parsed is not None else None
                        issue = "no reply" if parsed is None else i18n.problem(row["english"], text, None)
                        if issue is None and isinstance(text, str):
                            done[key] = text
                        else:
                            failed[key] = issue or "invalid"
                        continue
                    found_fix = parsed.get(key) if parsed is not None else None
                    fix = found_fix.get("fix") if isinstance(found_fix, dict) else None
                    if fix is not None:
                        flagged[locale] = flagged.get(locale, 0) + 1
                        if i18n.problem(row["english"], fix, None) is None:
                            done[key] = cast(str, fix)
            (WORK / f"{locale}.json").write_text(json.dumps(done, ensure_ascii=False))
            (WORK / f"{locale}.rejected.json").write_text(json.dumps(failed, ensure_ascii=False))
        if stage == "notes" and any(held(f"{locale}.rejected") for locale in LOCALES):
            missing = sum(len(held(f"{locale}.rejected")) for locale in LOCALES)
            return f"{missing} templates failed checks; run parse again to retranslate them"
        if stage == "notes-review":
            sampled = {locale: round(len(found) * SAMPLE) for locale in LOCALES}
            notes.append(
                "review flag rate: "
                + ", ".join(f"{locale} {flagged.get(locale, 0)}/{sampled[locale]}" for locale in LOCALES)
            )
    return "; ".join(notes) or "nothing to translate"


def emit() -> list[Path]:
    texts: list[str] = json.loads(TEXTS.read_text())
    written: list[Path] = []
    with connect() as conn:
        for locale in LOCALES:
            done = held(locale)
            cs = Changeset(
                conn,
                pipeline=f"{SOURCE}.{locale}",
                version=VERSION,
                summary=f"Machine translations of reader-facing prose into {i18n.LANGUAGES[locale][0]}",
                polity=None,
            )
            for text in texts:
                template, numbers = mask(text)
                body = done.get(md5(template)[:16])
                if body is not None:
                    cs.add(
                        "translation",
                        source_md5=md5(text),
                        lang=locale,
                        source_lang="en",
                        body=unmask(body, numbers, locale),
                        origin="machine",
                    )
            written.append(cs.write())
    return written
