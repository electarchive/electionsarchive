import re
import unicodedata
from itertools import combinations

SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
TITLES = {"judge", "gen", "col", "capt", "maj", "dr", "rev", "hon", "mr", "mrs", "gov", "senator"}


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
