"""Identifies the municipalities that the Atlas Electoral and the results bulletins print under changing names.

A municipality is printed with the name of its capital town, with the official name in parentheses, with the word
"Ciudad" in front, or with a province's name that has leaked into the column. Two printed names are one municipality
when they share a variant within a department, where a variant is the name outside the parentheses or the name inside
them. A name that no other table repeats is joined to the one municipality whose variants contain all its words.
"""

import re
import unicodedata
from collections import Counter, defaultdict

DEPARTMENTS = {
    "chuquisaca": "1",
    "la paz": "2",
    "cochabamba": "3",
    "oruro": "4",
    "potosi": "5",
    "tarija": "6",
    "santa cruz": "7",
    "beni": "8",
    "pando": "9",
}
NAMES = {
    "1": "Chuquisaca",
    "2": "La Paz",
    "3": "Cochabamba",
    "4": "Oruro",
    "5": "Potosí",
    "6": "Tarija",
    "7": "Santa Cruz",
    "8": "Beni",
    "9": "Pando",
}
# Names that the tables print for one municipality and that share no word to join them by: the Atlas lists the
# municipality of Totora, in the province of San Pedro de Totora, under the province's name for indigenous autonomy.
JOINED = [("4", "san pedro de totora", "totora")]
CAPITALS = {"cruz de la sierra": "santa cruz de la sierra", "nuestra senora de la paz": "la paz"}
PREFIX = re.compile(r"^(ciudad capital|capital ciudad|ciudad de|ciudad|capital)\s+", re.IGNORECASE)


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text).lower())
    return re.sub(r"\s+", " ", "".join(c for c in plain if unicodedata.category(c) != "Mn")).strip()


def department(text: str) -> str | None:
    return DEPARTMENTS.get(fold(text))


def base(name: str) -> str:
    """The name outside the parentheses, or inside them when nothing stands outside."""
    name = unicodedata.normalize("NFKC", name)
    head = re.sub(r"\s+", " ", name.split("(")[0].replace("“", '"').replace("”", '"')).strip()
    head = re.sub(
        r"(\w{4,}) (?!de$|la$|el$|del$|y$|en$)([a-z]{1,2})$", r"\1\2", head, flags=re.IGNORECASE
    )  # Filadelfi a
    if head:
        return head
    return re.sub(r"\s+", " ", name.replace("(", " ").replace(")", " ")).strip()


def display(name: str) -> str:
    text = PREFIX.sub("", base(name))
    return {"Cruz de la Sierra": "Santa Cruz de la Sierra", "Nuestra Señora de La Paz": "La Paz"}.get(text, text)


def variants(name: str) -> list[str]:
    """Folded variants of a printed name; a parenthesis that never closes was cut off and is dropped."""
    found = []
    head = display(name)
    if head:
        found.append(head)
    closed = re.search(r"\(([^()]+)\)\s*$", unicodedata.normalize("NFKC", name))
    if closed and "(" in name.split(")")[0]:
        found.append(closed.group(1))
    out = []
    for text in found:
        key = CAPITALS.get(re.sub(r"[()\"]", "", fold(text)), re.sub(r"[()\"]", "", fold(text)))
        key = re.sub(r"\s+", " ", key).strip()
        if key and key not in out:
            out.append(key)
    return out


class Places:
    """Union of the municipality names seen in each department, resolved to a single code per municipality."""

    def __init__(self) -> None:
        self.parent: dict[tuple[str, str], tuple[str, str]] = {}
        self.seen: dict[tuple[str, str], set[str]] = defaultdict(set)  # variant -> where it was printed
        self.printed: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.settled: dict[tuple[str, str], str] | None = None
        self.merged: list[tuple[str, str, str]] = []

    def find(self, node: tuple[str, str]) -> tuple[str, str]:
        self.parent.setdefault(node, node)
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def add(self, dept: str, name: str, where: str) -> None:
        keys = [(dept, key) for key in variants(name)]
        if not keys:
            raise ValueError(f"no name in {name!r}")
        for key in keys:
            self.find(key)
            self.seen[key].add(where)
        for key in keys[1:]:
            self.parent[self.find(key)] = self.find(keys[0])
        self.printed[keys[0]][display(name)] += 1

    def settle(self) -> None:
        """Join a name printed in one table only to the one municipality whose variants hold all its words."""
        for dept, one, other in JOINED:
            if (dept, one) in self.parent and (dept, other) in self.parent:
                self.parent[self.find((dept, one))] = self.find((dept, other))
        groups: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
        for node in list(self.parent):
            groups[self.find(node)].add(node)

        def where(root: tuple[str, str]) -> set[str]:
            return set().union(*(self.seen[node] for node in groups[root]))

        for root in sorted(groups):
            if root not in groups or len(where(root)) > 2:
                continue
            words = {w for node in groups[root] for w in node[1].split()}
            others = [
                other
                for other in groups
                if other != root
                and other[0] == root[0]
                and any(words <= set(node[1].split()) for node in groups[other])
                and len(where(other)) > 2
            ]
            if len(others) == 1:
                self.merged.append((root[0], min(n[1] for n in groups[root]), min(n[1] for n in groups[others[0]])))
                groups[others[0]] |= groups.pop(root)
                for node in groups[others[0]]:
                    self.parent[node] = others[0]
        self.settled = {}
        for root, nodes in groups.items():
            code = f"{root[0]}:{min(n[1] for n in nodes).replace(' ', '-')}"
            for node in nodes:
                self.settled[node] = code

    def code(self, dept: str, name: str) -> str:
        if self.settled is None:
            raise RuntimeError("settle first")
        keys = [(dept, key) for key in variants(name)]
        codes = {self.settled[key] for key in keys}
        if len(codes) != 1:
            raise ValueError(f"{name!r} in department {dept} is {codes}")
        return codes.pop()

    def names(self) -> dict[str, tuple[str, list[str]]]:
        """Each code's preferred printed name and its other names."""
        if self.settled is None:
            raise RuntimeError("settle first")
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for node, count in self.printed.items():
            counts[self.settled[node]].update(count)
        out = {}
        for code, count in counts.items():
            ranked = sorted(count, key=lambda text: (-count[text], -len(text), text))
            out[code] = (ranked[0], ranked[1:])
        return out
