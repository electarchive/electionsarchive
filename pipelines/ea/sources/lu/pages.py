"""Readers for the result pages of elections.public.lu.

The site shows each commune's results as one HTML page. The pages of the elections held up to 2011 share a layout:
a table of the lists with their list votes, personal votes, and seats, a table of the commune's ballots, and one
table per list with its candidates' votes. The 2023 communal elections and the 2015 referendum use their own
layouts.
"""

import re
from collections.abc import Iterator
from html.parser import HTMLParser

from ea.sources.lu.model import Candidate, Entry, Place, Stats, clean

VOID = {"br", "img", "input", "meta", "link", "hr", "area", "base", "col", "source", "wbr", "path", "use"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.kids: list[Node | str] = []

    def text(self) -> str:
        return clean("".join(k if isinstance(k, str) else k.text() for k in self.kids))

    def walk(self) -> Iterator[Node]:
        for kid in self.kids:
            if isinstance(kid, Node):
                yield kid
                yield from kid.walk()

    def find(self, tag: str) -> Node | None:
        return next((n for n in self.walk() if n.tag == tag), None)

    def all(self, tag: str) -> list[Node]:
        return [n for n in self.walk() if n.tag == tag]

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def rows(self) -> list[Node]:
        """This table's own rows, leaving out the rows of tables nested in it."""
        found: list[Node] = []

        def visit(node: Node) -> None:
            for kid in node.kids:
                if isinstance(kid, Node):
                    if kid.tag == "tr":
                        found.append(kid)
                    elif kid.tag != "table":
                        visit(kid)

        visit(self)
        return found

    def cells(self) -> list[Node]:
        return [k for k in self.kids if isinstance(k, Node) and k.tag in ("td", "th")]


class Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]

    def _close(self, tags: set[str], stop: set[str]) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag in stop:
                return
            if self.stack[i].tag in tags:
                del self.stack[i:]
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("td", "th"):
            self._close({"td", "th"}, {"tr", "table"})
        elif tag == "tr":
            self._close({"td", "th", "tr"}, {"table"})
        node = Node(tag, dict(attrs))
        self.stack[-1].kids.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].kids.append(Node(tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].kids.append(data)


def parse(html: str) -> Node:
    builder = Builder()
    builder.feed(html)
    builder.close()
    main = next((n for n in builder.root.walk() if n.tag == "main"), builder.root)
    return main


def figure(text: str) -> int | None:
    """A count as the site prints it: digits with spaces or no-break spaces as thousand separators."""
    digits = re.sub(r"[\s  .,]", "", text)
    return int(digits) if digits.isdigit() else None


def labelled(table: Node) -> dict[str, str]:
    """A two-column table of labels and values."""
    found: dict[str, str] = {}
    for row in table.rows():
        cells = row.cells()
        if len(cells) == 2 and cells[0].tag == "th":
            found[cells[0].text()] = cells[1].text()
    return found


def stats_of(details: dict[str, str]) -> Stats:
    return Stats(
        registered=figure(details.get("Electeurs inscrits", "")),
        cast=figure(details.get("Bulletins dans l'urne", "")),
        invalid=figure(details.get("Bulletins blancs / nuls", "")),
        valid=figure(details.get("Bulletins valables", "")),
    )


def entry_name(text: str) -> tuple[int | None, str]:
    found = re.match(r"(\d+)\s*-\s*(.*)", text)
    return (int(found.group(1)), found.group(2)) if found else (None, text)


def sigle_of(name: str) -> str:
    """The abbreviation a list's printed name starts with, as in "DP - Demokratesch Partei"."""
    head, _, _ = name.partition(" - ")
    return head if " - " in name else name


class Legacy:
    """One commune's or constituency's page in the layout used up to 2011."""

    def __init__(self, html: str) -> None:
        main = parse(html)
        self.title = clean((main.find("h1") or Node("h1", {})).text())
        self.summary: list[list[str]] = []
        self.details: dict[str, str] = {}
        self.plain: list[tuple[str, int | None, bool]] = []
        self.lists: list[tuple[str, str, list[tuple[str, int | None, bool]], dict[str, int | None]]] = []
        for table in main.all("table"):
            rows = table.rows()
            if not rows:
                continue
            head = rows[0].cells()
            first = head[0].text() if head else ""
            if first == "Nom du candidat":
                for row in rows[1:]:
                    cells = row.cells()
                    if len(cells) == 2:
                        shaded = bool({"elu", "highlight"} & (row.classes() | cells[0].classes()))
                        self.plain.append((cells[0].text(), figure(cells[1].text()), shaded))
            elif first == "Nom du parti":
                self.summary = [[c.text() for c in row.cells()] for row in rows[1:]]
            elif len(head) == 2 and head[0].tag == "th" and head[1].text().replace(" ", " ").endswith("%"):
                name = head[0].text()
                people: list[tuple[str, int | None, bool]] = []
                extras: dict[str, int | None] = {}
                for row in rows[1:]:
                    cells = row.cells()
                    if len(cells) != 2:
                        continue
                    label, value = cells[0].text(), figure(cells[1].text())
                    if label in ("Total de la liste", "Total suffrages") or label.casefold().startswith("suffrage"):
                        extras["Suffrages de liste" if label.casefold().startswith("suffrage") else label] = value
                    else:
                        shaded = bool({"elu", "highlight"} & (row.classes() | cells[0].classes()))
                        people.append((label, value, shaded))
                self.lists.append((name, head[1].text(), people, extras))
            elif any(c.tag == "th" for c in head) and len(head) == 2 and not self.details.get("Canton"):
                self.details.update(labelled(table))
            else:
                self.details.update(labelled(table))

    @property
    def commune(self) -> str:
        return re.sub(r"^Commune\s+", "", self.title)

    def entries(self) -> list[Entry]:
        """The lists, joined to their summary row (list votes, personal votes, seats) where there is one."""
        found: list[Entry] = []
        for index, (name, _, people, extras) in enumerate(self.lists, 1):
            position, label = entry_name(name)
            row = self._row(index, position, label)
            entry = Entry(
                position or index,
                label,
                sigle_of(label),
                total=figure(row[3]) if row else extras.get("Total de la liste", extras.get("Total suffrages")),
                listed=figure(row[1]) if row else extras.get("Suffrages de liste"),
                personal=figure(row[2]) if row else None,
                seats=figure(row[5]) if row and len(row) > 5 else None,
            )
            entry.candidates = [Candidate(i, who, votes, shaded) for i, (who, votes, shaded) in enumerate(people, 1)]
            found.append(entry)
        return found

    def _row(self, index: int, position: int | None, label: str) -> list[str] | None:
        for row in self.summary:
            n, text = entry_name(row[0])
            if position is not None and n == position:
                return row
            if position is None and len(self.summary) >= index and text == label:
                return row
        return self.summary[index - 1] if len(self.summary) >= index and position is None else None


def legacy_place(html: str) -> tuple[Place, Legacy]:
    page = Legacy(html)
    place = Place(
        page.commune,
        clean(page.details.get("Canton")).title() or None,
        stats_of(page.details),
        page.entries(),
    )
    return place, page


def with_class(node: Node, tag: str, name: str) -> list[Node]:
    return [n for n in node.walk() if n.tag == tag and name in n.classes()]


def person(row: Node) -> tuple[str, int | None, bool]:
    """A candidate's name, votes, and whether the page marks the candidate as elected."""
    head = row.cells()[0]
    status = with_class(head, "span", "candidate-status")
    elected = bool(status)
    marker = status[0].text() if status else ""
    name = clean(head.text().removeprefix(marker))
    votes = row.cells()[1]
    return name, figure(votes.attrs.get("data-value") or votes.text()), elected


def candidates_of(table: Node, start: int = 1) -> list[Candidate]:
    found: list[Candidate] = []
    for row in table.rows():
        cells = row.cells()
        if (
            len(cells) == 2
            and cells[0].tag == "th"
            and "candidate" in row.classes()
            and "candidate-votes" in cells[1].classes()
        ):
            who, votes, elected = person(row)
            found.append(Candidate(start + len(found), who, votes, elected))
    return found


def communal_2023(html: str) -> list[tuple[str, int | None, str, str | None, int | None, list[Entry], list[Candidate]]]:
    """One commune's page of the 2023 communal elections: its sections, each with its seats and lists or candidates.

    Each item holds the commune, its registered voters, its canton, the section's name, its seats, its lists, and
    (where the commune elects by majority) its candidates.
    """
    main = parse(html)
    commune = clean((main.find("h1") or Node("h1", {})).text())
    canton = None
    registered = None
    for item in main.all("li"):
        label = clean("".join(k if isinstance(k, str) else "" for k in item.kids))
        bold = item.find("b")
        if bold is None:
            continue
        if label == "Canton":
            canton = bold.text().title()
        elif label == "Electeurs inscrits":
            registered = figure(bold.text())
    blocks = with_class(main, "div", "suffrages-section") or [main]
    found = []
    for block in blocks:
        kicker = next(iter(with_class(block, "div", "kicker")), None) or next(
            iter(with_class(main, "div", "kicker")), None
        )
        text = kicker.text() if kicker else ""
        seats = re.search(r"Conseillers\s*:\s*(\d+)", text)
        section = re.search(r"section\s+(.*?)\s+Conseillers", text)
        entries: list[Entry] = []
        for body in with_class(block, "tbody", "suffrages-parti"):
            head = next(iter(with_class(body, "tr", "candidate")), None)
            if head is None:
                continue
            name = next(iter(with_class(head, "span", "suffrages-parti-name")), None)
            label = name.text() if name else head.cells()[0].text()
            values = {
                c: figure(next(iter(with_class(head, "td", c)), Node("td", {})).text())
                for c in ("suffrages-list", "suffrages-nameList", "suffrages-total", "suffrages-mandats")
            }
            inner = next(iter(body.all("table")), None)
            entry = Entry(
                len(entries) + 1,
                label,
                label,
                values["suffrages-total"],
                values["suffrages-list"],
                values["suffrages-nameList"],
                values["suffrages-mandats"],
                candidates_of(inner) if inner else [],
            )
            entries.append(entry)
        plain: list[Candidate] = []
        if not entries:
            table = next((t for t in block.all("table") if "table-sort" in t.classes()), None)
            plain = candidates_of(table) if table else []
        found.append(
            (
                commune,
                registered,
                canton or "",
                section.group(1) if section else "",
                int(seats.group(1)) if seats else None,
                entries,
                plain,
            )
        )
    return found


def referendum(html: str) -> tuple[str, Stats, list[tuple[int | None, int | None]]]:
    """One commune's page of the 2015 referendum: its ballots and each question's yes and no votes."""
    main = parse(html)
    commune = clean((main.find("h1") or Node("h1", {})).text())
    found: dict[str, str] = {}
    for item in main.all("li"):
        strong = item.find("strong")
        if strong is not None:
            found[clean("".join(k if isinstance(k, str) else "" for k in item.kids))] = strong.text()
    stats = Stats(
        registered=figure(found.get("Electeurs inscrits", "")),
        cast=figure(found.get("Bulletins dans l'urne", "")),
        blank=figure(found.get("Bulletins blancs", "")),
        invalid=figure(found.get("Bulletins nuls", "")),
        valid=figure(found.get("Bulletins valables", "")),
    )
    answers: list[tuple[int | None, int | None]] = []
    for row in with_class(main, "tr", "question"):
        voices = {
            (c.attrs.get("data-item") or ""): figure(c.attrs.get("data-value") or "")
            for c in row.cells()
            if c.attrs.get("data-name") == "voice"
        }
        answers.append((voices.get("yes"), voices.get("no")))
    return commune, stats, answers


def national_votes(html: str) -> dict[str, int]:
    """The national yes and no votes on a question's page."""
    main = parse(html)
    found: dict[str, int] = {}
    for row in with_class(main, "tr", "question"):
        for cell in row.cells():
            votes = figure(cell.attrs.get("data-value") or "")
            if cell.attrs.get("data-name") == "voice" and votes is not None:
                found[cell.attrs.get("data-item") or ""] = votes
    return found
