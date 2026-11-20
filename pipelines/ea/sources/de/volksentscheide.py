"""Volksentscheide of the German Länder: Berlin's eight popular votes since 2008, by Bezirk.

Berlin's Landesabstimmungsleiter publishes the final result of every Volksentscheid as a statistical report
(Statistischer Bericht B VII 4) from the Amt für Statistik Berlin-Brandenburg: the eligible voters, the participants,
and the Yes, No, and invalid votes for each of the twelve Bezirke and for Berlin. The reports are read from their
text layer. Every row must add up (Yes, No, and invalid votes to the participants) and the Bezirke must add up to
the Berlin row, which the report prints.
"""

import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "de.volksentscheide"
BERLIN = "01a13bb5-c563-741b-83f7-4b80c9e09ccb"
BERLIN_AREA = "01a13bb5-c563-741b-83f7-4b815208f13d"
GERMANY = "01a13bb5-c550-7079-8650-a96e2b145352"
BEZIRKE = {
    "Mitte": ("11001", "01a13bb5-c57a-72d2-9042-3beb361267f3"),
    "Friedrichshain-Kreuzberg": ("11002", "01a13bb5-c57a-72d2-9042-3bec8786786f"),
    "Pankow": ("11003", "01a13bb5-c57a-72d2-9042-3bed0f9dc951"),
    "Charlottenburg-Wilmersdorf": ("11004", "01a13bb5-c57a-72d2-9042-3beec617b1e7"),
    "Spandau": ("11005", "01a13bb5-c57a-72d2-9042-3bef71e3584e"),
    "Steglitz-Zehlendorf": ("11006", "01a13bb5-c57a-72d2-9042-3bf079d9e2f0"),
    "Tempelhof-Schöneberg": ("11007", "01a13bb5-c57a-72d2-9042-3bf1f68aeb10"),
    "Neukölln": ("11008", "01a13bb5-c57a-72d2-9042-3bf247220add"),
    "Treptow-Köpenick": ("11009", "01a13bb5-c57a-72d2-9042-3bf3ee048d7c"),
    "Marzahn-Hellersdorf": ("11010", "01a13bb5-c57a-72d2-9042-3bf4dbb6c529"),
    "Lichtenberg": ("11011", "01a13bb5-c57a-72d2-9042-3bf53af5ba54"),
    "Reinickendorf": ("11012", "01a13bb5-c57a-72d2-9042-3bf6ce0efdcc"),
}
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
HISTORY = "https://www.berlin.de/wahlen/historie/volksbegehren-und-volksentscheide"
LIBRARY = "https://www.statistischebibliothek.de/mir/servlets/MCRFileNodeServlet"
PUBLISHER = "Amt für Statistik Berlin-Brandenburg, Landesabstimmungsleiter Berlin"
NUMBER = re.compile(r"\d{1,3}(?: \d{3})*(?:,\d+)?|\d+(?:,\d+)?")


@dataclass(frozen=True)
class Vote:
    day: date
    url: str
    slug: str
    title: str
    native: str
    questions: tuple[tuple[str, str], ...]
    quorum: str


VOTES_LIST = (
    Vote(
        date(2008, 4, 27),
        f"{HISTORY}/tempelhof-2008/volksentscheid_tempelhof.pdf",
        "tempelhof",
        "Tempelhof remains an airport",
        "Volksentscheid „Tempelhof bleibt Verkehrsflughafen!“",
        (("Stimmen Sie diesem Beschluss zu?", "Do you agree with this resolution?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2009, 4, 26),
        f"{HISTORY}/ethik-und-religion-2009/volksentscheid_ethik_religion.pdf",
        "pro-reli",
        "Ethics and religion as an elective subject (Pro Reli)",
        "Volksentscheid über die Einführung eines Wahlpflichtbereichs Ethik/Religion",
        (("Stimmen Sie diesem Gesetzentwurf zu?", "Do you agree with this draft law?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2011, 2, 13),
        f"{HISTORY}/berliner-wasserbetriebe-2010-und-2011/volksentscheid_wasserbetriebe.pdf",
        "wasser",
        "Berlin water contracts (Schluss mit Geheimverträgen)",
        "Volksentscheid „Schluss mit den Geheimverträgen – Wir Berliner wollen unser Wasser zurück“",
        (("Stimmen Sie diesem Gesetzentwurf zu?", "Do you agree with this draft law?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2013, 11, 3),
        f"{HISTORY}/berliner-energieversorgung-2013/volksentscheid_energieversorgung.pdf",
        "energie",
        "Berlin energy supply (Neue Energie für Berlin)",
        "Volksentscheid über den Gesetzentwurf „Neue Energie für Berlin“",
        (("Stimmen Sie diesem Gesetzentwurf zu?", "Do you agree with this draft law?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2014, 5, 25),
        f"{HISTORY}/tempelhofer-feld-2014/volksentscheid_tempelhofer_feld.pdf",
        "tempelhofer-feld",
        "Preservation of the Tempelhofer Feld",
        "Volksentscheid über den Erhalt des Tempelhofer Feldes",
        (
            ("Erste Abstimmungsfrage: Gesetzentwurf der Initiative", "First question: the initiative's draft law"),
            ("Zweite Abstimmungsfrage: Gesetzentwurf des Abgeordnetenhauses", "Second question: the House's draft law"),
        ),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2017, 9, 24),
        f"{HISTORY}/tegel-2017/volksentscheid_tegel.pdf",
        "tegel",
        "Continued operation of Tegel airport",
        "Volksentscheid über den Weiterbetrieb des Flughafens Berlin-Tegel „Otto-Lilienthal“ (TXL)",
        (("Stimmen Sie diesem Beschlussentwurf zu?", "Do you agree with this draft resolution?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2021, 9, 26),
        f"{LIBRARY}/BBHeft_derivate_00029569/SB_B07-04-01_2021u00_BE.pdf",
        "enteignen",
        "Expropriation of large housing companies (Deutsche Wohnen & Co enteignen)",
        "Volksentscheid „Deutsche Wohnen & Co enteignen“",
        (("Stimmen Sie diesem Beschlussentwurf zu?", "Do you agree with this draft resolution?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
    Vote(
        date(2023, 3, 26),
        f"{HISTORY}/berlin-2030-klimaneutral-2023/sb_b07-04-02_2023u00_be.pdf",
        "klimaneutral",
        "Berlin 2030 climate neutral",
        "Volksentscheid „Berlin 2030 klimaneutral“",
        (("Stimmen Sie diesem Gesetzentwurf zu?", "Do you agree with this draft law?"),),
        "A majority of the participants and at least 25 percent of those entitled to vote had to agree.",
    ),
)
BY_KEY = {f"be-{v.day}": v for v in VOTES_LIST}
SHARED = {
    date(2014, 5, 25): "It was held on the day of the European Parliament election.",
    date(2017, 9, 24): "It was held on the day of the federal election.",
    date(2021, 9, 26): "It was held on the day of the federal election and the Abgeordnetenhaus election.",
}
# The Land's printed totals: eligible voters, participants, Yes, No, invalid, for each question.
BERLIN_TOTALS = {
    date(2008, 4, 27): [(2438035, 881035, 529880, 349384, 1771)],
    date(2009, 4, 26): [(2445699, 713095, 345004, 366721, 1370)],
    date(2011, 2, 13): [(2466288, 678507, 666235, 11590, 692)],
    date(2013, 11, 3): [(2483756, 722109, 599588, 121113, 1408)],
    date(2014, 5, 25): [(2491365, 1149145, 739124, 343982, 66039), (2491365, 1149145, 468094, 588586, 92465)],
    date(2017, 9, 24): [(2482264, 1763076, 994916, 738024, 30136)],
    date(2021, 9, 26): [(2447600, 1798308, 1035950, 715698, 46660)],
    date(2023, 3, 26): [(2430073, 868570, 442028, 423594, 2948)],
}


# Rows whose Yes, No, and invalid votes differ from the participants the report prints, with the gap (the three
# votes less the participants). The Bezirke's participants add up to Berlin's; the votes of the Bezirke add up to
# Berlin's votes, so the Berlin row itself is out by the sum of the gaps.
PRINTED_GAPS = {
    (date(2011, 2, 13), "Friedrichshain-Kreuzberg"): 7,
    (date(2011, 2, 13), "Tempelhof-Schöneberg"): -2,
    (date(2011, 2, 13), "Lichtenberg"): 5,
    (date(2011, 2, 13), "Berlin"): 10,
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def is_pdf(path: Path) -> None:
    if path.read_bytes()[:5] != b"%PDF-":
        raise ValueError(f"{path.name} is not a PDF (the host throttled the request?)")


def fetch() -> None:
    for vote in VOTES_LIST:
        for attempt in range(4):
            try:
                raw.fetch(SOURCE, vote.url, suffix=".pdf", check=is_pdf)
                break
            except Exception:
                time.sleep(30 * (attempt + 1))
        else:
            raise ValueError(f"could not fetch {vote.url}")
        time.sleep(15)


def text_of(vote: Vote) -> str:
    import subprocess

    path = raw.cached(SOURCE, vote.url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def numbers(rest: str) -> list[int | float]:
    found: list[int | float] = []
    for token in NUMBER.findall(rest):
        text = token.replace(" ", "")
        found.append(float(text.replace(",", ".")) if "," in token else int(text))
    return found


def blocks(text: str) -> list[dict[str, list[int | float]]]:
    """The tables of rows that name the twelve Bezirke and then Berlin, in the order the report prints them."""
    names = sorted(BEZIRKE, key=len, reverse=True)
    found: list[dict[str, list[int | float]]] = []
    current: dict[str, list[int | float]] = {}
    for line in text.splitlines():
        stripped = re.sub(r"^\s*\d(?:\.\d)?\s{2,}", "", line).strip()
        for name in (*names, "Berlin"):
            if stripped.startswith(name):
                rest = stripped[len(name) :]
                if name == "Berlin" and not re.match(r"\s*\d", rest):
                    break
                if name != "Berlin" and re.match(r"[\wäöüÄÖÜß-]", rest):
                    continue
                rest = re.sub(r"[.…�]{2,}|[^\x00-\x7fÀ-ſ]+", "  ", rest)
                values = numbers(rest)
                if len(values) >= 5:
                    current[name] = values
                    if name == "Berlin":
                        if len(current) == 13:
                            found.append(current)
                        current = {}
                break
    return found


# The columns of each report's two tables. A participation row is [eligible, with voting slip, %, participants,
# postal, %, turnout]; a count row varies with the report.
def counts(vote: Vote, tables: list[dict[str, list[int | float]]]) -> list[dict[str, tuple[int, int, int, int, int]]]:
    """Per question: for each Bezirk and Berlin, (eligible, participants, yes, no, invalid)."""
    out = []
    participation = tables[0]
    for index in range(len(vote.questions)):
        rows = {}
        result = tables[1 + index]
        for name, row in result.items():
            eligible = int(participation[name][0])
            if len(row) == 9 and row[0] == eligible:
                participants, yes, no, invalid = int(row[1]), int(row[2]), int(row[5]), int(row[7])
            elif len(row) == 8 and row[0] == participation[name][3]:
                participants, yes, no, invalid = int(row[0]), int(row[1]), int(row[4]), int(row[6])
            else:
                raise ValueError(f"{vote.day} {name}: unexpected row {row}")
            if participants != participation[name][3]:
                raise ValueError(f"{vote.day} {name}: participants differ between tables")
            if yes + no + invalid != participants:
                if (vote.day, name) not in PRINTED_GAPS:
                    raise ValueError(f"{vote.day} {name}: votes do not add up to the participants")
                if yes + no + invalid - participants != PRINTED_GAPS[vote.day, name]:
                    raise ValueError(f"{vote.day} {name}: the printed gap changed")
            rows[name] = (eligible, participants, yes, no, invalid)
        total = rows.pop("Berlin")
        for column in range(5):
            if sum(row[column] for row in rows.values()) != total[column]:
                raise ValueError(f"{vote.day}: column {column} of the Bezirke does not add up to Berlin")
        if total != BERLIN_TOTALS[vote.day][index]:
            raise ValueError(f"{vote.day}: Berlin total {total} differs from the report's headline figures")
        rows["Berlin"] = total
        out.append(rows)
    return out


def read(vote: Vote) -> list[dict[str, tuple[int, int, int, int, int]]]:
    tables = blocks(text_of(vote))
    return counts(vote, tables)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in BY_KEY)]


def parse() -> None:
    registry().write()
    for vote in VOTES_LIST:
        build(vote).write()


def emit() -> None:
    package.run("load", packages())


SYSTEM = key("electoral_system", "referendum")


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The electoral system of Land referendums: one question put to all voters, answered Yes or No",
    )
    p.external.add(BERLIN)
    p.external.add(GERMANY)
    p.add(
        "scheme",
        code="de.volksentscheid",
        polity_id=GERMANY,
        kind="administrative",
        rank=50,
        noun="count unit",
        code_system=None,
    )
    p.add("electoral_system", id=SYSTEM, polity_id=BERLIN, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "A question put to all voters entitled to vote for the Land parliament. A draft law or resolution passes "
            "when a majority of the participants and at least a stated share of those entitled to vote agree."
        ),
        lang="en",
    )
    return p


def build(vote: Vote) -> Package:
    day = vote.day.isoformat()
    source_id = key("source", day)
    p = Package(
        f"{SOURCE}/be-{day}",
        SOURCE,
        f"Berlin's Volksentscheid of {day} ({vote.title}) by Bezirk, from the Landesabstimmungsleiter's final report",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {BERLIN, BERLIN_AREA, GERMANY, *(area for _, area in BEZIRKE.values())}
    artifact = raw.cached(SOURCE, vote.url)
    p.add(
        "source",
        id=source_id,
        kind="official_body",
        publisher=PUBLISHER,
        title="Volksentscheide in Berlin: Endgültiges Ergebnis (Statistischer Bericht B VII 4)",
        url=vote.url,
        license="Public report of the Amt für Statistik Berlin-Brandenburg; reuse with attribution",
        license_url="https://www.statistik-berlin-brandenburg.de/impressum",
        attribution="Amt für Statistik Berlin-Brandenburg",
    )
    p.artifact(artifact, source_id)
    results = read(vote)
    if vote.day == date(2011, 2, 13):
        p.accept(
            "accounting.ballots_sum",
            "The report prints participants for Friedrichshain-Kreuzberg (46,346), Tempelhof-Schöneberg (66,798), and "
            "Lichtenberg (48,070) that differ by 7, -2, and 5 from their Yes, No, and invalid votes; the Berlin row "
            "is out by the sum, 10. The archive stores the figures as printed.",
        )
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=BERLIN,
        kind="referendum",
        date_from=vote.day,
        date_to=vote.day,
        status="held",
    )
    p.name(election, f"{vote.day.year} Berlin referendum: {vote.title}", "en", "official")
    p.name(election, vote.native, "de", "native", False)
    path = f"de/be/{day}-volksentscheid"
    p.add("slug", path=path, entity_id=election, is_current=True)
    units = {}
    for name in BEZIRKE:
        code = BEZIRKE[name][0]
        unit = key("unit", day, code)
        units[name] = unit
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BERLIN,
            scheme_code="de.volksentscheid",
            code=code,
            area_relation="equals",
            area_id=BEZIRKE[name][1],
            kind="standard",
            valid_from=vote.day,
            valid_to=vote.day + timedelta(days=1),
        )
        p.name(unit, name, "de", "official")
    for number, ((question, english), rows) in enumerate(zip(vote.questions, results, strict=True), 1):
        contest = key("contest", day, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=BERLIN_AREA,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        suffix = "" if len(vote.questions) == 1 else f", question {number}"
        p.name(contest, f"{vote.day.year} Berlin referendum: {vote.title}{suffix}", "en", "official")
        p.name(contest, question, "de", "native", False)
        p.add("slug", path=f"{path}/question-{number}", entity_id=contest, is_current=True)
        for lang, body in (("de", question), ("en", english)):
            p.add(
                "ballot_question_text",
                id=key("ballot_question_text", contest, lang),
                contest_id=contest,
                kind="text",
                lang=lang,
                body=body,
                url=vote.url,
                source_id=source_id,
            )
        result_set = key("result_set", day, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = (
            "Final result (endgültiges Ergebnis) by Bezirk from the Landesabstimmungsleiter's statistical report. "
            "Participants are the valid and invalid votes together; the Bezirke add up to the Berlin total. "
            f"{vote.quorum} {SHARED.get(vote.day, '')}"
        ).strip()
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        options = {}
        for position, (answer, label) in enumerate((("yes", "Ja"), ("no", "Nein")), 1):
            options[answer] = key("option", day, number, answer)
            p.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(options[answer], answer.title(), "en", "english")
        values: list[tuple[str, str, str | None, int, int]] = []
        for name, (eligible, participants, yes, no, invalid) in rows.items():
            if name == "Berlin":
                continue
            unit = units[name]
            values += [
                (result_set, unit, options["yes"], VOTES, yes),
                (result_set, unit, options["no"], VOTES, no),
                (result_set, unit, None, REGISTERED, eligible),
                (result_set, unit, None, CAST, participants),
                (result_set, unit, None, VALID, yes + no),
                (result_set, unit, None, INVALID, invalid),
            ]
        p.frame(
            "result_value",
            pl.DataFrame(
                {
                    "result_set_id": [v[0] for v in values],
                    "unit_id": [v[1] for v in values],
                    "option_id": [v[2] for v in values],
                    "channel": [0] * len(values),
                    "measure": [v[3] for v in values],
                    "count_round": [0] * len(values),
                    "flags": [0] * len(values),
                    "value": [v[4] for v in values],
                },
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
            ),
        )
        eligible, participants, yes, no, invalid = rows["Berlin"]
        for answer, votes in (("yes", yes), ("no", no)):
            p.add("declared", result_set_id=result_set, option_id=options[answer], measure=VOTES, value=votes)
        # Passed when a majority of the participants and at least a quarter of those entitled to vote said Yes.
        won = yes > no and yes * 4 >= eligible
        for answer, option in options.items():
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if (answer == "yes") == won else "lost",
            )
    print(f"{p.key}: {len(results)} question(s)", flush=True)
    return p
