"""The Central Electoral Commission's totalization decisions: who passed the threshold, how many seats each list
won, which single-member candidates were elected, and which president-elect was declared."""

import html
import re
import unicodedata

from ea import raw

SOURCE = "md.cec"
PAGE = "https://a.cec.md/index.php/ro/hotarari-2751.html"
PRINT = "https://a.cec.md/index.php/ro/print/decision-2751_{}.html"

# Each decision is a page of the Commission's decisions list; the page number in the URL is the decision's own id.
EXCLUDED = "excluded"
SEATS = "seats"
DISTRICTS = "districts"
ADVANCED = "advanced"
ELECTED = "elected"
DECISIONS: dict[str, dict[str, tuple[int, str]]] = {
    "2014-11-30": {
        EXCLUDED: (83597, "No. 3104 of 5 December 2014"),
        SEATS: (83598, "No. 3105 of 5 December 2014"),
    },
    "2016-10-30": {ADVANCED: (85821, "No. 517 of 2 November 2016")},
    "2016-11-13": {ELECTED: (85872, "No. 571 of 18 November 2016")},
    "2019-02-24": {
        EXCLUDED: (92706, "No. 2415 of 3 March 2019"),
        SEATS: (92707, "No. 2416 of 3 March 2019"),
        DISTRICTS: (92708, "No. 2417 of 3 March 2019"),
    },
    "2020-11-01": {ADVANCED: (98124, "No. 4466 of 4 November 2020")},
    "2020-11-15": {ELECTED: (98230, "No. 4507 of 20 November 2020")},
    "2021-07-11": {
        EXCLUDED: (99936, "No. 5179 of 19 July 2021"),
        SEATS: (99937, "No. 5180 of 19 July 2021"),
    },
    "2024-10-20": {ADVANCED: (111792, "No. 3130 of 23 October 2024")},
    "2024-11-03": {ELECTED: (111942, "No. 3208 of 9 November 2024")},
    "2025-09-28": {
        EXCLUDED: (115474, "No. 4150 of 5 October 2025"),
        SEATS: (115475, "No. 4151 of 5 October 2025"),
    },
}
URLS = [PRINT.format(number) for entries in DECISIONS.values() for number, _ in entries.values()]


def fetch() -> None:
    for url in URLS:
        raw.fetch(SOURCE, url, suffix=".html")


def reference(day: str, kind: str) -> str:
    return DECISIONS[day][kind][1]


def page(day: str, kind: str) -> str:
    """The decision's body as HTML."""
    source = raw.cached(SOURCE, PRINT.format(DECISIONS[day][kind][0])).path.read_text(encoding="utf-8")
    start = source.index('<div class="page-content">')
    end = source.find('<div class="container footer">', start)
    return re.sub(r"<(script|style)\b.*?</\1>", " ", source[start : end if end > 0 else None], flags=re.DOTALL)


def plain(markup: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).replace("\xa0", " ").split())


def text(day: str, kind: str) -> str:
    """The decision's body as one line of text."""
    return plain(page(day, kind))


def tokens(label: str) -> tuple[str, ...]:
    """A name reduced to what identifies it, so a decision's wording matches the workbook's ballot label."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", label.casefold()) if not unicodedata.combining(c))
    plain = re.sub(r"candidat(?:ul)? independent|\bretras\b", " ", plain)
    words = re.sub(r"[^a-z0-9]+", " ", plain).split()
    return tuple(sorted(word for word in words if word not in ("partidul", "partid", "politic")))


def _operative(body: str) -> str:
    """The part of a decision after its 'hotărăște:' formula."""
    found = re.search(r"h\s*o\s*t\s*[ăa]\s*r\s*[ăâîa]\s*[șş]\s*t\s*e\s*:", body, flags=re.IGNORECASE)
    if found is None:
        raise ValueError("the decision has no operative clause")
    return body[found.end() :]


def excluded(day: str) -> list[str]:
    """The names a decision excludes from the allocation for falling short of the threshold."""
    body = _operative(text(day, EXCLUDED))
    body = body[: body.index("Prezenta")] if "Prezenta" in body else body
    names: list[str] = []
    for part in body.split(":")[1:]:
        part = re.sub(r"Denumirea concurentului electoral|Numărul de voturi(?: acumulate)?(?:\s*%)?", " ", part)
        for found in re.finditer(r"(?:\d{1,2}\.\s*)?([^\d:]+?)\s+(?:\d[\d ]*?\s+)?\d+,\d+\s*%?(?=\s|$)", part):
            names.append(found.group(1).strip(" .,;"))
    return names


def seats(day: str) -> list[tuple[str, int]]:
    """The seat count the decision's operative clause gives each list."""
    body = _operative(text(day, SEATS))
    body = body[: body.index("Prezenta")] if "Prezenta" in body else body
    parts = re.split(r"(\d+)\s+mandate\b\.?", body)
    found: list[tuple[str, int]] = []
    for index in range(1, len(parts) - 1, 2):
        name = parts[index - 1].rsplit("urmează:", 1)[-1]
        name = re.sub(r"Denumirea concurentului electoral|Numărul de mandate stabilite", " ", name)
        name = re.sub(r"^\s*(?:\d+\)\s*)?", "", name).strip(" -–.")
        found.append((name, int(parts[index])))
    return found


def winners(day: str) -> dict[int, str]:
    """The elected candidate's name in each single-member constituency, from the decision's annex."""
    found: dict[int, str] = {}
    for row in re.findall(r"<tr\b.*?</tr>", page(day, DISTRICTS), flags=re.DOTALL):
        cells = [plain(cell) for cell in re.findall(r"<td\b.*?</td>", row, flags=re.DOTALL)]
        cells = [cell for cell in cells if cell]
        number = re.match(r"Circumscripția uninominală nr\. (\d+)", cells[0]) if cells else None
        if number is not None and len(cells) >= 2:
            found[int(number.group(1))] = cells[1]
    return found


def advanced(day: str) -> list[tuple[str, int]]:
    """The two candidates a first-round decision sends to the second round, with the votes it prints."""
    body = _operative(text(day, ADVANCED))
    return [
        (name.strip(), int(re.sub(r"\D", "", votes)))
        for name, votes in re.findall(
            r"([A-ZĂÂÎȘȚŞŢ][^;:,]+?)\s*,\s*(?:desemnat|candidat independent)[^;]*?obținut\s*([\d.,\s]+?)\s*voturi",
            body,
        )
    ]


def elected(day: str) -> str:
    """The name of the candidate a second-round decision declares elected."""
    found = re.search(r"S\s*e declară ales concurentul electoral\s+(.+?)\s*,\s*desemnat", text(day, ELECTED))
    if found is None:
        raise ValueError("the decision declares nobody elected")
    return found.group(1).strip()
