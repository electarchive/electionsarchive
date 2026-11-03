import codecs
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from uuid import UUID

from ea.changeset import Changeset, slug
from ea.store import Connection

GARBLED = "\ufffd"
DOUBLED = GARBLED.encode().decode("cp1252")
LOST = "[^\\x01-\\x7f\\ufffd]"
REASON = "The source's text was read as UTF-8 when it is Windows-1252, or the source lost the letter itself, which garbled the accented letters."


def _cp1252(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeDecodeError):
        raise error
    return error.object[error.start : error.end].decode("cp1252", errors="replace"), error.end


codecs.register_error("cp1252", _cp1252)


def decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="cp1252")


def rekey(cs: Changeset, text: str) -> None:
    if (new := slug(text)) != (old := slug(re.sub(r"[^\x00-\x7f]", "", text))) and old:
        cs.renamed.setdefault(new, old)


def restorer(conn: Connection) -> Callable[[str], str]:
    known: dict[str, str] = {}

    def word(garbled: str) -> str:
        if garbled not in known:
            known[garbled] = garbled
            upper = garbled.replace(GARBLED, "").isupper()
            for lost, share in ((LOST, 0.9), ("[a-z]", 0.75)):
                pattern = "".join(lost if c == GARBLED else re.escape(c) for c in garbled)
                counts: Counter[str] = Counter()
                for (text,) in conn.execute("select text from name where text ~* %s", [f"\\m{pattern}"]):
                    counts.update(found.lower() for found in re.findall(f"(?i)(?<!\\w){pattern}(?!\\w)", text))
                best = counts.most_common(1)
                if best and best[0][1] >= share * counts.total():
                    known[garbled] = "".join(
                        (b.upper() if upper or i == 0 else b) if g == GARBLED else g
                        for i, (g, b) in enumerate(zip(garbled, best[0][0], strict=True))
                    )
                    break
        return known[garbled]

    def restore(text: str) -> str:
        text = text.replace(DOUBLED, GARBLED)
        if GARBLED not in text:
            return text
        return re.sub(f"[\\w{GARBLED}]*{GARBLED}[\\w{GARBLED}]*", lambda match: word(match[0]), text)

    return restore


def merge(text: str, ballot: str) -> str:
    out = list(text)
    for match in re.finditer(f"[\\w{GARBLED}]+", text):
        lower = any(c.islower() for c in match[0])
        for i in range(match.start(), match.end()):
            if text[i] != GARBLED:
                continue
            inner = i > match.start()
            out[i] = (ballot[i].lower() if lower else ballot[i].upper()) if inner else ballot[i]
            if inner and lower and i + 1 < match.end() and text[i + 1] != GARBLED:
                out[i + 1] = text[i + 1].lower()
    return "".join(out)


def repair(cs: Changeset, reason: str = REASON) -> int:
    texts: dict[UUID, set[str]] = defaultdict(set)
    for row in cs.rows.get("candidacy", []):
        if isinstance(person := row.get("person_id"), UUID) and isinstance(ballot := row.get("ballot_name"), str):
            texts[person].add(ballot)
    held: set[tuple[UUID, str, str | None, str]] = set()
    for row in cs.rows.get("name", []):
        if isinstance(entity := row.get("entity_id"), UUID) and isinstance(text := row.get("text"), str):
            texts[entity].add(text)
            lang, kind = row.get("lang"), row.get("kind")
            held.add(
                (entity, text, lang if isinstance(lang, str) else None, kind if isinstance(kind, str) else "official")
            )
    for row in cs.rows.get("entity_merge", []):
        if isinstance(entity := row.get("entity_id"), UUID) and (into := cs.survivors.get(entity)) is not None:
            texts[entity] = texts[into]
    words: dict[str, str] = {}
    dropped = {row["name_id"] for row in cs.rows.get("name_drop", [])}
    count = 0
    for name_id, entity, text, lang, kind, preferred, start, end in cs.conn.execute(
        """select id, entity_id, text, lang, kind, is_preferred, valid_from, valid_to from name
           where entity_id = any(%s) and (strpos(text, chr(65533)) > 0 or strpos(text, %s) > 0)
           order by kind = 'short', is_preferred desc, id""",
        [list(texts), DOUBLED],
    ).fetchall():
        garbled = text.replace(DOUBLED, GARBLED)
        pattern = re.compile("".join("\\S" if c == GARBLED else re.escape(c) for c in garbled), re.IGNORECASE)
        ballot = next((t for t in sorted(texts[entity]) if pattern.fullmatch(t)), None)
        stripped = " ".join(garbled.replace(GARBLED, "").split()).strip(" -")
        fixed = (
            merge(garbled, ballot)
            if ballot is not None
            else stripped
            if any(t.lower() == stripped.lower() for t in texts[entity])
            else " ".join(words.get(w.lower(), w) for w in garbled.split())
            if kind == "short"
            else garbled
        )
        if GARBLED in fixed or name_id in dropped:
            continue
        for old, new in zip(garbled.split(), fixed.split(), strict=False):
            if GARBLED in old:
                words[old.lower()] = new
        cs.add("name_drop", name_id=name_id, entity_id=entity, text=text, reason=reason)
        survivor = cs.survivors.get(entity, entity)
        if (survivor, fixed, lang, kind) not in held:
            held.add((survivor, fixed, lang, kind))
            cs.add(
                "name",
                entity_id=survivor,
                text=fixed,
                lang=lang,
                kind=kind,
                is_preferred=preferred and survivor == entity,
                valid_from=start,
                valid_to=end,
            )
        count += 1
    return count
