import re
import unicodedata
from itertools import combinations

SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
TITLES = {"judge", "gen", "col", "capt", "maj", "dr", "rev", "hon", "mr", "mrs", "gov", "senator"}
PARTICLE = re.compile(r"(?<=\S )(?:[Dd]e|[Vv]an|[Vv]on|[Ll]a)(?= \S)")
ROMAN = {"II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "XIII", "XIV", "XV"}
ACRONYMS = {"AKA", "CPA", "DDS", "EDR", "LLC", "NAACP", "NCAA", "PHD", "SDR", "USA", "USAF", "USMC", "USN"}
LOWER = {
    "fr": {"de", "du", "des", "d"},
    "es": {"de", "del", "y"},
    "pt": {"de", "da", "do", "das", "dos", "e"},
    "it": set(),
    "de": {"von", "van", "der", "den", "zu"},
    "nl": {"van", "der", "den", "de", "ter", "te"},
}
MAC = re.compile(
    r"mac(?=(?:arthur|aulay|callum|donald|dougall?|ewan|farlane|gregor|innes|intyre|iver|kay|kenzie|kinnon"
    r"|lachlan|lean|leod|millan|naughton|neil|pherson|queen|rae)$)"
)
RUN = re.compile(r"[^\W\d_]+")


def initials(text: str) -> str:
    return re.sub(r"(?<![\w.'’-])([A-Z])(?=\s)", r"\1.", text)


def tokens(text: str) -> tuple[list[str], frozenset[str]] | None:
    if re.search(r"\d", text):
        return None
    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    folded = re.sub(r"\(.*?\)|\".*?\"|“.*?”", " ", folded).replace("'", "")
    words = [word for word in re.split(r"[^a-z]+", folded) if word]
    suffixes = frozenset(word for word in words if word in SUFFIXES)
    words = [word for word in words if word not in SUFFIXES and word not in TITLES]
    return (words, suffixes) if len(words) >= 2 else None


def compatible(left: str, right: str) -> bool:
    a, b = tokens(left), tokens(right)
    if a is None or b is None or a[1] != b[1]:
        return False
    (x, _), (y, _) = a, b
    if x[1:] + x[:1] == y or y[1:] + y[:1] == x:
        return True
    if x[-1] != y[-1]:
        return False
    return all(
        p == q or (len(p) == 1 and q.startswith(p)) or (len(q) == 1 and p.startswith(q)) for p, q in zip(x[:-1], y[:-1])
    )


def fold(labels: list[str], preferred: list[bool] | None = None) -> dict[int, int]:
    parent = list(range(len(labels)))

    def root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for i, j in combinations(range(len(labels)), 2):
        if compatible(labels[i], labels[j]):
            parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for index in range(len(labels)):
        groups.setdefault(root(index), []).append(index)
    folded: dict[int, int] = {}
    for members in groups.values():
        if len(members) < 2 or not all(compatible(labels[i], labels[j]) for i, j in combinations(members, 2)):
            continue
        if preferred is not None and sum(preferred[i] for i in members) > 1:
            continue
        survivor = max(
            members,
            key=lambda i: (
                preferred[i] if preferred is not None else False,
                len(labels[i].split()),
                len(labels[i]),
                -i,
            ),
        )
        for index in members:
            if index != survivor:
                folded[index] = survivor
    return folded


def display(text: str, lang: str = "en", known: str | None = None) -> str:
    turkic = lang in ("tr", "az")

    def lower(word: str) -> str:
        return (word.replace("I", "ı").replace("İ", "i") if turkic else word).lower()

    def upper(letter: str) -> str:
        return {"i": "İ", "ı": "I"}.get(letter, letter.upper()) if turkic else letter.upper()

    def proper(word: str) -> str:
        word = lower(word)
        if word.startswith("mc") and len(word) > 3:
            return "Mc" + upper(word[2]) + word[3:]
        if MAC.match(word):
            return "Mac" + upper(word[3]) + word[4:]
        return upper(word[0]) + word[1:]

    def vowel(word: str) -> bool:
        return any(c in "AEIOUYÆØ" for c in unicodedata.normalize("NFKD", word.replace("ı", "i").upper()))

    if known is not None and lower(known) == lower(text):
        return known
    shouting = not any(c.islower() for c in text)
    runs = list(RUN.finditer(text))
    out = []
    end = 0
    for index, run in enumerate(runs):
        word = run[0]
        out.append(text[end : run.start()])
        end = run.end()
        mixed = re.fullmatch(r"([A-Z][a-z]{1,2})([A-Z]{3,})", word)
        if mixed and not shouting:
            out.append(mixed[1] + proper(mixed[2]))
            continue
        after = text[run.end() : run.end() + 1]
        if (
            word.isupper()
            and 0 < index < len(runs) - 1
            and lower(word) in LOWER.get(lang, set())
            and (after in ("'", "’")) == (word == "D")
        ):
            out.append(lower(word))
            continue
        if word[:2].isupper() and sum(c.islower() for c in word) == 1 and sum(c.isupper() for c in word) >= 3:
            word = word.upper()
        if not word.isupper() or len(word) < 2 or word in ACRONYMS or (index and word in ROMAN):
            out.append(word)
            continue
        if not shouting and not (
            vowel(word)
            and (lang != "en" or len(word) >= 4 or (len(word) == 3 and any(c.islower() for c in text[: run.start()])))
        ):
            out.append(word)
            continue
        out.append(proper(word))
    out.append(text[end:])
    return "".join(out)
