"""The adjudication and proclamation acts of the Córdoba electoral court.

After each provincial election the court first assigns the offices and seats to the lists (the adjudication) and then
proclaims the people elected (the proclamation). Both are published as PDFs on the court's election-process page. The
acts of 2003, 2007, 2015 and 2019 are text PDFs, read here; the 2011 acts are scans, transcribed once into
`cordoba_acts_2011.json` and checked against the printed vote totals."""

import json
import re
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

ACTS = "https://www.justiciacordoba.gob.ar/Jel/pdf/"
LATER = ACTS + "EleccionesAnteriores/Elecciones2019/"
PROCESOS = ACTS + "procesos/"

# Each election day and the acts that declare its outcomes: the proclamation, and where one is published as a text
# PDF, the adjudication that assigns the seats.
URLS: dict[str, dict[str, str]] = {
    "20030608": {
        "adjudication": PROCESOS + quote("2003.06.08 - Adjudicación y Proclamación.pdf"),
        "proclamation": PROCESOS + quote("2003.06.08 - Adjudicación y Proclamación.pdf"),
    },
    "20070902": {
        "adjudication": PROCESOS + quote("2007.09.02 - Adjudicacion.pdf"),
        "proclamation": PROCESOS + quote("2007.09.02 - Proclamación.pdf"),
    },
    "20110807": {
        "adjudication": PROCESOS + quote("2011.08.07 - Adjudicacion.pdf"),
        "proclamation": PROCESOS + quote("2011.08.07 - Proclamación.pdf"),
    },
    "20150705": {"proclamation": PROCESOS + quote("2015.08.07 - Proclamación.pdf")},
    "20190512": {
        "adjudication": LATER + quote("Resolución 50 adjudicación del 2019.pdf"),
        "proclamation": LATER + quote("Resolución 51 Proclamación del 2019.pdf"),
    },
}
SCANNED = {"20110807"}
TRANSCRIPT = Path(__file__).with_name("cordoba_acts_2011.json")


@dataclass(frozen=True)
class Person:
    name: str
    document: str | None


@dataclass(frozen=True)
class Act:
    """One declared result: the list that won, what it won, and the people the proclamation names."""

    kind: str  # governor, legislators, departmental or audit
    scope: str | None  # the department's printed name
    order: int  # the position among the acts of the same kind and scope
    lead: str  # the list's printed name; with a sum of votes, the party whose own votes lead it
    group: tuple[str, ...]  # every party in the sum of votes, in the printed order
    votes: int | None  # the votes the act prints for the list or the sum
    own: int | None  # the lead party's own votes, where the adjudication prints them
    seats: int | None  # the seats the adjudication assigns
    people: tuple[Person, ...]


def fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text.lower()) if ch.isalnum() or ch == " ").strip()


def text_of(path: Path) -> str:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    text = FOOTER.sub("\n", unicodedata.normalize("NFC", text).replace("\x0c", "\n"))
    text = re.sub(
        r"(?m)^[ \t]*((?:[IVXL] ?)+)\.\s*-\s*P ?R ?O ?C ?L ?A ?M ?A ?R\b",
        lambda m: m[1].replace(" ", "") + ".- PROCLAMAR",
        text,
    )
    return re.sub(r"(?<=[a-záéíóúñ])(a la|al)(?=\s+[Aa]lianza|\s+[Pp]artido)", r" \1", text)


SECTION = re.compile(r"(?m)^[ \t]*([IVXL]+)\.\s*-\s*([A-Za-zÁÉÍÓÚ]+)\b")
NUMBER = re.compile(r"\(\s*(\d{1,3}(?:\.\d{3})*)\s*\)")
WORDS = {"una": 1, "un": 1, "dos": 2, "tres": 3, "seis": 6}


def number(text: str) -> int:
    return int(text.replace(".", ""))


def sections(text: str, verb: str) -> list[str]:
    marks = list(SECTION.finditer(text))
    out = []
    for index, mark in enumerate(marks):
        if mark.group(2).lower().startswith(verb):
            end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
            out.append(text[mark.start() : end])
    return out


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def classify(head: str) -> tuple[str, str | None]:
    """The kind of office a section declares and, for a department's legislator, the department's name."""
    low = fold(head[:400])
    if "tribuno" in low:
        return "audit", None
    if found := re.search(r"departamento ([a-z ]+?)(?: por | al | a la | a los| con |$)", low):
        return "departmental", found[1].strip()
    if "gobernador" in low and "legislador" not in low:
        return "governor", None
    return "legislators", None


def lead_of(head: str) -> tuple[str, tuple[str, ...]]:
    """The list that won and the parties in its sum of votes, from a section's wording."""
    text = flat(head)
    sums = re.search(
        r"(?:obtenido|obtuvieron) (?:los |las |el |la )?(?:partidos?|agrupaciones?)?:?\s*"
        r"((?:[^;:()]|\([^)]*\))+?)(?:,? en sumatoria|,? un total|,? la cantidad|,? al haber|;)",
        text,
        re.IGNORECASE,
    )
    quoted = re.search(r"(?:Alianza|Partido|alianza|partido)\s+[“\"]([^”\"]+)[”\"]", text)
    if quoted and "conformada" in text[quoted.end() : quoted.end() + 30]:
        names = re.findall(r"[“\"]([^”\"]+)[”\"]", text[quoted.end() : text.find(" un total", quoted.end())])
        return quoted[1].strip(), tuple(n.strip() for n in names)
    if quoted:
        return quoted[1].strip(), ()
    if sums:
        listed = re.split(r",\s*| y (?=[A-ZÁÉÍÓÚ])", sums[1].strip())
        names = tuple(n.strip() for n in listed if n.strip())
        return names[0], names
    raise ValueError(f"no list in {text[:200]!r}")


PERSON_LINE = re.compile(
    r"^[ \t]*(?:\d+[ \t]*[.\-]*[ \t]*)?([^\n(]+?)[ \t]+M\.?[ \t]*I\.?[ \t]*([\d.]+)[ \t]*$", re.MULTILINE
)
PERSON_INLINE = re.compile(r"([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜa-záéíóúñü.,'\- ]+?)\s*\((?:M\.\s?I\.\s*)?([\d.]+)\)")


FOOTER = re.compile(r"(?m)^[ \t]*Expediente Nro\..*$")
INLINE = re.compile(r"ciudadan[oa]s?\s+([^()]+?)\s+M\.\s?I\.\s*([\d.]+)")


def people_of(body: str, kind: str) -> tuple[Person, ...]:
    start = re.search(r"ciudadan[oa]s?\b", body)
    if start is None:
        return ()
    tail = body[start.start() :]
    found = [Person(tidy(m[1]), m[2].rstrip(".")) for m in PERSON_LINE.finditer(tail) if "ciudadan" not in m[1].lower()]
    if found:
        return tuple(found)
    text = flat(tail)
    inline = [Person(tidy(m[1]), m[2].rstrip(".")) for m in PERSON_INLINE.finditer(text)]
    if inline:
        return tuple(inline)
    single = INLINE.search(text)
    if single:
        return (Person(tidy(single[1]), single[2].rstrip(".")),)
    paired = re.match(r"ciudadanos (.+?) y (.+?),? respectivamente", text)
    if paired and kind == "governor":
        return (Person(tidy(paired[1]), None), Person(tidy(paired[2]), None))
    return ()


PARTICLES = {"de", "del", "y", "e"}
ARTICLES = {"la", "las", "los", "el"}


def tidy(printed: str) -> str:
    """A person's name as 'Given Surname' in mixed case, from 'SURNAME, GIVEN' or 'Given SURNAME'."""
    text = re.sub(r"\s+", " ", printed).strip(" .-,")
    text = re.sub(r"\s*M\.\s*I\.?$", "", text).strip(" .-,")
    if "," in text:
        surname, given = (part.strip() for part in text.split(",", 1))
        text = f"{given} {surname}"
    out: list[str] = []
    for word in text.split(" "):
        low = word.lower()
        if out and (low in PARTICLES or (low in ARTICLES and out[-1] in PARTICLES)):
            out.append(low)
        elif word in (word.upper(), low):
            out.append("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")))
        else:
            out.append(word)
    return " ".join(out)


def seats_of(head: str) -> int | None:
    text = flat(head)
    if found := re.search(r"\((\d+)\)\s*bancas?", text):
        return int(found[1])
    return None


def proclaimed(path: Path) -> list[Act]:
    text = text_of(path)
    acts: list[Act] = []
    counts: dict[tuple[str, str | None], int] = {}
    for body in sections(text, "proclamar"):
        head = flat(body)[:1400]
        if "asimismo" in head[:120].lower():
            continue
        kind, scope = classify(head)
        lead, group = lead_of(head)
        votes = re.search(r"(?:cantidad de|total de) [^()]*?\(\s*([\d.]+)\s*\)", head)
        people = people_of(body, kind)
        order = counts[(kind, scope)] = counts.get((kind, scope), 0) + 1
        acts.append(Act(kind, scope, order, lead, group, number(votes[1]) if votes else None, None, None, people))
    return acts


def adjudicated(path: Path) -> list[Act]:
    text = text_of(path)
    acts: list[Act] = []
    counts: dict[tuple[str, str | None], int] = {}
    for body in sections(text, "adjudicar"):
        head = flat(body)
        if re.match(r"\S+ ADJUDICAR los cargos de Gobernador", head, re.IGNORECASE):
            kind, scope = "governor", None
        else:
            kind, scope = classify(head)
        lead, group = lead_of_adjudication(head)
        order = counts[(kind, scope)] = counts.get((kind, scope), 0) + 1
        seats = 1 if kind == "governor" else seats_of(head)
        acts.append(Act(kind, scope, order, lead, group, None, own_votes(head, lead), seats, ()))
    return acts


LEAD = re.compile(
    r"(?:(?:al|a la) (?:Partido|Alianza|partido|alianza)\b|\ba (?:las|los) siguientes (?:agrupaciones|partidos):)\s*"
)


def lead_of_adjudication(head: str) -> tuple[str, tuple[str, ...]]:
    text = flat(head)
    found = LEAD.search(text)
    if found is None:
        raise ValueError(f"no list in {text[:200]!r}")
    quoted = re.match(r"[“\"]([^”\"]+)[”\"]", text[found.end() :])
    if quoted:
        return quoted[1].strip(), ()
    name = re.match(r"(.+?)\s+(?:al haber|que obtuvo)", text[found.end() :])
    if name is None:
        raise ValueError(f"no list in {text[:200]!r}")
    return clean_name(name[1]), ()


def own_votes(head: str, lead: str) -> int | None:
    """The votes the adjudication prints for the leading party alone, before any sum."""
    text = flat(head)
    at = text.find(lead)
    found = NUMBER.search(text, at + len(lead))
    return number(found[1]) if found else None


def clean_name(name: str) -> str:
    return re.sub(r"^(?:el |la )?(?:Partido|Alianza|partido|alianza) ", "", name.strip())


def transcribed() -> list[Act]:
    data = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    acts = []
    for item in data:
        people = tuple(Person(tidy(p["name"]), p.get("document")) for p in item["people"])
        acts.append(
            Act(
                item["kind"],
                item.get("scope"),
                item["order"],
                item["lead"],
                tuple(item.get("group", ())),
                item.get("votes"),
                None,
                item.get("seats"),
                people,
            )
        )
    return acts


def sources(day: str) -> list[str]:
    """The PDFs that declare the outcomes of one election day."""
    return list(dict.fromkeys(URLS.get(day, {}).values()))


def declared(day: str, locate: Callable[[str], Path]) -> list[Act]:
    """Every office and seat list the acts of one election day declare, with the people proclaimed. `locate` gives the
    cached file of an act's URL."""
    if day in SCANNED:
        return transcribed()
    urls = URLS[day]
    named = proclaimed(locate(urls["proclamation"]))
    if "adjudication" not in urls:
        return [replace(act, seats=1 if act.kind == "governor" else len(act.people)) for act in named]
    assigned = {(a.kind, a.scope, a.order): a for a in adjudicated(locate(urls["adjudication"]))}
    out = []
    for act in named:
        found = assigned.pop((act.kind, act.scope, act.order), None)
        if found is None:
            raise ValueError(f"{day}: the adjudication has no {act.kind} {act.scope} {act.order}")
        if found.seats != (1 if act.kind == "governor" else len(act.people)):
            raise ValueError(f"{day} {act.kind} {act.scope}: the adjudication seats differ from the people proclaimed")
        out.append(replace(act, lead=found.lead, own=found.own, seats=found.seats))
    if assigned:
        raise ValueError(f"{day}: the adjudication assigns {sorted(map(str, assigned))} that no proclamation names")
    return out
